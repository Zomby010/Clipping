"""OpenAI-compatible self-hosted inference with bounded, local-only retries.

No vLLM/torch import belongs here: the model server has its own environment.
"""

from copy import deepcopy
import time

from .base import (
    ContextOverflowError, InvalidOutputError, ProviderConfigError,
    ProviderError, ProviderUnavailableError,
)
from .config import normalize_base_url, vllm_config_values
from .parsing import clean_text, parse_clips
from .validation import transcript_end, validate_clips


MAX_ATTEMPTS = 3
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _local_prompt(prompt):
    # Only change the template, never replace strings inside user transcripts.
    instruction, separator, transcript = prompt.partition("\nTranscript:\n")
    instruction = instruction.replace(
        "Output MUST be a valid JSON array.",
        'Output MUST be a valid JSON object with a single field "clips" that is an array.',
    )
    instruction = instruction.replace(
        "REQUIRED JSON STRUCTURE (follow these field names exactly):\n[",
        'REQUIRED JSON STRUCTURE (follow these field names exactly):\n{"clips": [',
        1,
    )
    if 'exactly):\n{"clips": [' in instruction:
        # The fixed template finishes with its example array before Transcript.
        position = instruction.rfind("]")
        instruction = instruction[:position + 1] + "}" + instruction[position + 1:]
    return instruction + separator + transcript


class VllmProvider:
    name = "vllm"

    def __init__(self, cfg, *, http_client=None, sleep=None, clock=None):
        import httpx
        from openai import OpenAI

        self.cfg = cfg
        self.options = vllm_config_values(vars(cfg))
        self.root, self.api_base = normalize_base_url(self.options["vllm_base_url"])
        self.model = self.options["vllm_model"]
        self.max_tokens = self.options["local_max_tokens"]
        self.timeout = self.options["vllm_timeout"]
        self.context_limit = self.options["vllm_context_limit"]
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(
            follow_redirects=False, trust_env=False,
        )
        self._key = self.options["api_key_vllm"] or "local-vllm"
        self._client = OpenAI(
            base_url=self.api_base, api_key=self._key, max_retries=0,
            http_client=self._http, timeout=self.timeout,
        )
        self._model_limit = None
        self._deadline = None
        self.run_meta = {}

    @classmethod
    def from_cfg(cls, cfg):
        return cls(cfg)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._owns_http:
            self._client.close()

    def _remaining(self):
        remaining = self.timeout if self._deadline is None else self._deadline - self._clock()
        if remaining <= 0:
            raise ProviderUnavailableError("The overall vLLM time limit was exceeded.")
        return remaining

    def _http_timeout(self, *, probe=False):
        import httpx
        remaining = min(self._remaining(), 10) if probe else self._remaining()
        return httpx.Timeout(remaining, connect=min(5, remaining), pool=min(5, remaining))

    @staticmethod
    def _status_error(status):
        if status in {401, 403}:
            return ProviderConfigError("vLLM rejected the credentials. Check VLLM_API_KEY on the client and the server.", status_code=status)
        if status == 404:
            return ProviderConfigError("vLLM endpoint or model not found. Check the base URL, the served model and access to /tokenize.", status_code=status)
        if status == 400:
            return ProviderConfigError("vLLM rejected the request. Check the context limit, chat template, schema and server version.", status_code=status)
        return ProviderUnavailableError(
            f"vLLM request failed (HTTP {status}). Check the server status.",
            status_code=status, retryable=status in _RETRYABLE,
        )

    def _json_request(self, method, url, *, payload=None):
        import httpx
        try:
            response = self._http.request(
                method, url, json=payload,
                headers={"Authorization": "Bearer " + self._key},
                timeout=self._http_timeout(probe=True), follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ProviderUnavailableError("Probe/tokenisasi vLLM timeout.", retryable=True) from None
        except httpx.TransportError:
            raise ProviderUnavailableError("The vLLM server cannot be reached. Start the server and check VLLM_BASE_URL.") from None
        if response.status_code != 200:
            raise self._status_error(response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise ProviderConfigError("Endpoint vLLM mengembalikan response non-JSON.") from None
        if not isinstance(data, dict):
            raise ProviderConfigError("Unsupported vLLM endpoint response format.")
        return data

    def validate(self):
        """Probe only this configured server and ensure the model/alias exists."""
        data = self._json_request("GET", self.api_base + "/models")
        records = data.get("data")
        if not isinstance(records, list):
            raise ProviderConfigError("The /v1/models response has no model list.")
        record = next((m for m in records if isinstance(m, dict) and m.get("id") == self.model), None)
        if record is None:
            raise ProviderConfigError("The server is not serving VLLM_MODEL. Match the name with /v1/models or --served-model-name.")
        limit = record.get("max_model_len")
        self._model_limit = limit if _positive_int(limit) else None

    def _preflight_tokens(self, messages, max_tokens):
        data = self._json_request("POST", self.root + "/tokenize", payload={
            "model": self.model, "messages": messages,
            "add_generation_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        count = data.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ProviderConfigError("/tokenize did not return a valid token count.")
        limits = [v for v in (data.get("max_model_len"), self._model_limit, self.context_limit) if _positive_int(v)]
        if not limits:
            raise ProviderConfigError("The server context limit is unknown; set VLLM_CONTEXT_LIMIT to match the server configuration.")
        limit = min(limits)
        self.run_meta.update(prompt_tokens=count, context_limit=limit, max_tokens=max_tokens)
        if count + max_tokens > limit:
            remaining = limit - count
            if remaining > 0:
                advice = (f"This prompt leaves {remaining} tokens for the answer. "
                          "Lower the output limit if that is enough, or use a shorter input.")
            else:
                advice = "The prompt already fills the context limit. Use a shorter input, for example one part of the video."
            raise ContextOverflowError(
                f"Not enough vLLM context: prompt {count} + output {max_tokens} > {limit} tokens. "
                f"{advice} For this full request, the model and server need to support "
                f"a context of at least {count + max_tokens} tokens."
            )

    def _completion(self, messages, *, max_tokens, temperature, top_p, schema=None):
        import openai
        kwargs = dict(
            model=self.model, messages=messages, max_tokens=max_tokens,
            temperature=temperature, top_p=top_p,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            timeout=self._http_timeout(),
        )
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema", "json_schema": {"name": "clips", "strict": True, "schema": schema},
            }
        try:
            return self._client.chat.completions.create(**kwargs)
        except openai.APITimeoutError:
            raise ProviderUnavailableError("Inference vLLM timeout.", retryable=True) from None
        except openai.APIConnectionError:
            raise ProviderUnavailableError("vLLM connection failed. Check the server process and the base URL.") from None
        except openai.APIStatusError as exc:
            raise self._status_error(exc.status_code) from None
        except openai.APIError:
            raise ProviderUnavailableError("The SDK could not read the vLLM response. Check server compatibility.") from None

    def _generate(self, messages, *, max_tokens, temperature, top_p, schema=None, parse=None):
        started = self._clock()
        self._deadline = started + self.timeout
        self.run_meta = dict(
            actual_provider="vllm", model=self.model, attempts=0,
            fallback_used=False, status="failed", usage=[], failures=[],
        )
        stage = "model_probe"

        def record_failure(exc, cycle):
            # Keep the first inference failure even if a retry can no longer
            # reach the server. Never retain raw exception/response content.
            self.run_meta["failures"].append({
                "retry_cycle": cycle,
                "stage": stage,
                "error_type": type(exc).__name__,
                "status_code": exc.status_code,
                "retryable": exc.retryable,
            })

        try:
            current = list(messages)
            validated = False
            for attempt in range(MAX_ATTEMPTS):
                try:
                    if not validated:
                        stage = "model_probe"
                        self.validate()
                        validated = True
                    stage = "tokenize"
                    self._preflight_tokens(current, max_tokens)
                    stage = "inference"
                    self.run_meta["attempts"] += 1
                    response = self._completion(
                        current, max_tokens=max_tokens, temperature=temperature,
                        top_p=top_p, schema=schema,
                    )
                    stage = "output_validation"
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        self.run_meta["usage"].append({
                            k: getattr(usage, k, None)
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        })
                    if not response.choices:
                        raise InvalidOutputError("vLLM returned no choices.")
                    choice = response.choices[0]
                    if choice.finish_reason == "length":
                        # Repeating the same bounded request cannot fix truncation.
                        raise ContextOverflowError("vLLM output was cut off at the token limit. Ask for fewer clips or raise the output budget and available context.")
                    if choice.finish_reason != "stop":
                        raise InvalidOutputError("vLLM did not finish a normal text answer.")
                    result = parse(choice.message.content) if parse else clean_text(choice.message.content)
                    self.run_meta["status"] = "completed"
                    return result
                except InvalidOutputError as exc:
                    record_failure(exc, attempt + 1)
                    if attempt == MAX_ATTEMPTS - 1:
                        raise
                    # Trusted diagnostic only; never echo model text into repair/logs.
                    current = list(messages) + [{"role": "user", "content": (
                        "Write the full answer again. Fix this problem: " + str(exc)
                        + " Follow the schema and the source timestamp limits; do not add explanations."
                    )}]
                except ProviderUnavailableError as exc:
                    record_failure(exc, attempt + 1)
                    if not exc.retryable or attempt == MAX_ATTEMPTS - 1:
                        raise
                except ProviderError as exc:
                    record_failure(exc, attempt + 1)
                    raise
                stage = "retry_wait"
                delay = min(2 ** attempt, self._remaining())
                self._sleep(delay)
            raise ProviderUnavailableError("Percobaan vLLM habis.")
        except ProviderError as exc:
            self.run_meta["error_type"] = type(exc).__name__
            self.run_meta["error_stage"] = stage
            raise
        finally:
            self.run_meta["duration_seconds"] = round(self._clock() - started, 3)
            self._deadline = None

    def generate_clips(self, prompt, schema, *, transcript, requested_clips, min_duration=20, max_duration=179):
        if not _positive_int(requested_clips):
            raise ProviderConfigError("The number of clips must be a positive whole number.")
        end = transcript_end(transcript)
        if end < min_duration:
            raise ProviderConfigError("The transcript is shorter than the minimum clip length.")
        array = deepcopy(schema)
        array.update(minItems=1, maxItems=requested_clips)
        wrapped = {"type": "object", "properties": {"clips": array}, "required": ["clips"], "additionalProperties": False}

        def parse(content):
            return validate_clips(
                parse_clips(content, wrapped), source_end=end, requested_clips=requested_clips,
                min_duration=min_duration, max_duration=max_duration,
            )

        messages = [
            {"role": "system", "content": (
                'Return only a JSON object {"clips": [...]} matching the supplied schema. '
                'All timestamps are absolute source-video seconds. Choose non-overlapping clips. '
                'Return fewer clips only if the source cannot support the requested count.'
            )},
            {"role": "user", "content": _local_prompt(prompt)},
        ]
        try:
            result = self._generate(messages, max_tokens=self.max_tokens, temperature=0.5, top_p=1, schema=wrapped, parse=parse)
            self.run_meta.update(requested_clips=requested_clips, returned_clips=len(result))
            if len(result) < requested_clips:
                print(f"[vLLM] Produced {len(result)} of the {requested_clips} clips requested; no extra clips were invented.")
            return result
        finally:
            self.cfg.ai_run_meta = dict(self.run_meta)

    def generate_text(self, prompt, *, temperature=0.7, top_p=0.9, max_tokens=None):
        budget = self.max_tokens if max_tokens is None else max_tokens
        if not _positive_int(budget):
            raise ProviderConfigError("The voice-over token budget must be a positive whole number.")
        # Keep voiceover metadata separate from transcript-analysis provenance.
        try:
            return self._generate(
                [{"role": "user", "content": prompt}], max_tokens=budget,
                temperature=temperature, top_p=top_p,
            )
        finally:
            self.cfg.voiceover_run_meta = dict(self.run_meta)
