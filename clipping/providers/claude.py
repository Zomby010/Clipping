"""
clipping.providers.claude — Ask Claude, with pictures, and get JSON back.

Two ways to reach Claude, tried in this order when ``--claude-backend auto``:

1. ``cli``: the Claude Code program installed on this computer (``claude``).
   It runs on the Claude plan you are signed in to, so there is no
   separate bill; it counts toward that plan's usage limits. Claude reads
   the frame pictures with its Read tool.
2. ``api``: the Anthropic API, when ``ANTHROPIC_API_KEY`` is set. Billed per
   use on your API account.

If neither works, callers fall back to Gemini.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess

DEFAULT_MODEL = "claude-opus-5-5"


class ClaudeUnavailable(RuntimeError):
    """Claude could not be reached or did not give a usable answer."""


def cli_path() -> str | None:
    return shutil.which("claude")


def pick_backend(cfg) -> str | None:
    """'cli', 'api' or None, following --claude-backend."""
    wanted = getattr(cfg, "claude_backend", "auto")
    has_cli = cli_path() is not None
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or getattr(cfg, "api_key_anthropic", None))
    if wanted == "cli":
        return "cli" if has_cli else None
    if wanted == "api":
        return "api" if has_key else None
    if has_cli:
        return "cli"
    if has_key:
        return "api"
    return None


def _wrap_schema(schema: dict | None) -> tuple[dict | None, bool]:
    """Structured output needs an object at the top; wrap arrays as {"items": [...]}."""
    if schema is None:
        return None, False
    if schema.get("type") == "object":
        return schema, False
    return {
        "type": "object",
        "properties": {"items": schema},
        "required": ["items"],
        "additionalProperties": False,
    }, True


def ask(cfg, prompt: str, *, schema: dict | None = None, images: list[str] = (),
        timeout: float = 1800, label: str = "Claude"):
    """
    Send ``prompt`` (plus ``images``, JPEG/PNG paths) to Claude.
    Returns the parsed JSON when ``schema`` is given, else the text.
    Raises ClaudeUnavailable on any problem.
    """
    backend = pick_backend(cfg)
    if backend is None:
        raise ClaudeUnavailable(
            "Claude is not set up: install Claude Code and sign in (run 'claude' once), "
            "or put ANTHROPIC_API_KEY in .env."
        )
    wire_schema, wrapped = _wrap_schema(schema)
    model = getattr(cfg, "claude_model", None) or DEFAULT_MODEL
    images = [p for p in images if p and os.path.exists(p)]
    print(f"   🤖 [{label}] Asking Claude ({backend}, {model}) with {len(images)} picture(s)...")
    if backend == "cli":
        result = _ask_cli(prompt, wire_schema, images, model, timeout)
    else:
        result = _ask_api(cfg, prompt, wire_schema, images, model)
    if wire_schema is not None and wrapped:
        if not isinstance(result, dict) or "items" not in result:
            raise ClaudeUnavailable("Claude's answer did not have the expected shape.")
        return result["items"]
    return result


# ---------------------------------------------------------------- Claude Code

def _ask_cli(prompt, schema, images, model, timeout):
    work_dirs = sorted({os.path.dirname(os.path.abspath(p)) for p in images})
    full_prompt = prompt
    if images:
        listing = "\n".join(f"- {os.path.abspath(p)}" for p in images)
        full_prompt = (
            "Before answering, open every picture below with your Read tool and look at it carefully.\n"
            f"{listing}\n\n{prompt}"
        )
    cmd = [cli_path(), "-p", "--output-format", "json", "--model", model,
           "--no-session-persistence", "--strict-mcp-config"]
    if images:
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        for d in work_dirs:
            cmd += ["--add-dir", d]
    else:
        cmd += ["--tools", ""]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    try:
        res = subprocess.run(
            cmd, input=full_prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, cwd=work_dirs[0] if work_dirs else None,
        )
    except subprocess.TimeoutExpired:
        raise ClaudeUnavailable(f"Claude Code took longer than {int(timeout)}s.") from None
    except OSError as e:
        raise ClaudeUnavailable(f"Could not start Claude Code: {e}") from None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        tail = (res.stderr or res.stdout or "").strip()[-400:]
        raise ClaudeUnavailable(f"Claude Code did not answer (exit {res.returncode}). {tail}") from None
    if data.get("is_error") or data.get("subtype") != "success":
        raise ClaudeUnavailable(f"Claude Code reported an error: {str(data.get('result'))[:400]}")
    if schema is not None:
        out = data.get("structured_output")
        if out is None:
            try:
                out = json.loads(data.get("result") or "")
            except ValueError:
                raise ClaudeUnavailable("Claude Code did not return JSON.") from None
        return out
    return (data.get("result") or "").strip()


# ---------------------------------------------------------------- Anthropic API

def _ask_api(cfg, prompt, schema, images, model):
    try:
        import anthropic
    except ImportError:
        raise ClaudeUnavailable("The 'anthropic' package is not installed (pip install anthropic).") from None

    key = getattr(cfg, "api_key_anthropic", None) or os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    content = []
    for p in images:
        media = "image/png" if p.lower().endswith(".png") else "image/jpeg"
        with open(p, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    content.append({"type": "text", "text": prompt})

    output_config = {"effort": getattr(cfg, "claude_effort", "high")}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config=output_config,
            # If a safety check declines the request, the API retries it on a
            # suitable fallback model inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ClaudeUnavailable("The Anthropic API key was rejected.") from None
    except anthropic.RateLimitError:
        raise ClaudeUnavailable("Anthropic API rate limit reached; try again later.") from None
    except anthropic.APIStatusError as e:
        raise ClaudeUnavailable(f"Anthropic API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise ClaudeUnavailable("Could not reach the Anthropic API (network).") from None

    if message.stop_reason == "refusal":
        raise ClaudeUnavailable("Claude declined this request.")
    if message.stop_reason == "max_tokens":
        raise ClaudeUnavailable("Claude's answer was cut off (too long).")
    text = "".join(b.text for b in message.content if b.type == "text").strip()
    if schema is None:
        return text
    try:
        return json.loads(text)
    except ValueError:
        raise ClaudeUnavailable("Claude did not return valid JSON.") from None
