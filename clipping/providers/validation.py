"""Shared execution-path preflight and local-only temporal validation."""

import math
from pathlib import Path
import re

from .base import InvalidOutputError, ProviderConfigError


def validate_analysis_config(cfg, *, probe=False, check_cache=True):
    cached = getattr(cfg, "load_gemini_json", False)
    voiceover = getattr(cfg, "voiceover", False)
    provider = getattr(cfg, "ai_provider", "gemini")
    if cached and check_cache and not (Path(cfg.outputs_dir) / "gemini_response.json").is_file():
        raise ProviderConfigError("Cached AI JSON not found. Turn off Bypass AI to run a new analysis.")
    if cached and not voiceover:
        return
    if provider == "vllm":
        from .config import vllm_config_values
        vllm_config_values(vars(cfg))
        if probe:
            from .vllm import VllmProvider
            with VllmProvider.from_cfg(cfg) as local:
                local.validate()
        return
    if provider == "claude":
        from .claude import pick_backend
        if pick_backend(cfg):
            return  # Claude is reachable; Gemini (if its key is set) is only the backup.
        if not getattr(cfg, "api_key_gemini", ""):
            raise ProviderConfigError(
                "Claude is not set up (install Claude Code and sign in, or set ANTHROPIC_API_KEY) "
                "and there is no GOOGLE_API_KEY for the Gemini backup."
            )
        return
    if provider not in {"gemini", "nvidia"}:
        raise ProviderConfigError("Unknown AI provider.")
    if voiceover and not getattr(cfg, "api_key_gemini", ""):
        raise ProviderConfigError("GOOGLE_API_KEY is required for the cloud voiceover provider.")
    if cached:
        return
    if provider == "nvidia" and getattr(cfg, "api_key_nvidia", ""):
        return
    if not getattr(cfg, "api_key_gemini", ""):
        key = "NVIDIA_API_KEY or GOOGLE_API_KEY" if provider == "nvidia" else "GOOGLE_API_KEY"
        raise ProviderConfigError(f"{key} not found. Set it in Settings or the environment (.env).")


_STAMP = re.compile(r"^\[(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\]\s*", re.MULTILINE)


def transcript_end(transcript):
    # Do not silently shorten the source when a trailing timestamp is malformed.
    for line in transcript.splitlines():
        if line.lstrip().startswith("[") and _STAMP.match(line) is None:
            raise ProviderConfigError("The vLLM transcript has an invalid timestamp line.")
    pairs = [(float(m[1]), float(m[2])) for m in _STAMP.finditer(transcript)]
    if not pairs or any(not math.isfinite(s + e) or s < 0 or e < s for s, e in pairs):
        raise ProviderConfigError("The vLLM transcript needs valid [start - end] timestamps.")
    end = max(e for _, e in pairs)
    if end <= 0:
        raise ProviderConfigError("The transcript length must be more than zero.")
    return end


def _number(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise InvalidOutputError("Clip timestamps and scores must be finite numbers.")
    return value


def _interval(item, low, high, *, start="start_time", end="end_time"):
    try:
        a, b = _number(item[start]), _number(item[end])
    except (KeyError, TypeError):
        raise InvalidOutputError("Clip/segment timestamp fields are incomplete.") from None
    if not low <= a < b <= high:
        raise InvalidOutputError("A clip/hook/segment timestamp is outside the source video or reversed.")
    return a, b


def _ordered_intervals(items, low, high):
    last = low
    for item in items:
        start, end = _interval(item, low, high)
        if start < last:
            raise InvalidOutputError("Segments must be in order and must not overlap.")
        last = end


def validate_clips(clips, *, source_end, requested_clips, min_duration=20, max_duration=179, storyline_cuts=False):
    if not clips or len(clips) > requested_clips:
        raise InvalidOutputError("No clips, or more clips than requested.")
    intervals = []
    for item in clips:
        a, b = _interval(item, 0, source_end)
        length = b - a
        if storyline_cuts:
            # Segments come in story order from anywhere: only overlaps are wrong.
            parts = sorted(_interval(s, a, b) for s in item["keep_segments"])
            if not parts or any(right[0] < left[1] for left, right in zip(parts, parts[1:])):
                raise InvalidOutputError("Storyline segments are missing or overlap.")
            length = sum(end - start for start, end in parts)
        if not min_duration <= length <= max_duration:
            raise InvalidOutputError(f"Clip length must be {min_duration}-{max_duration} seconds.")
        if not 1 <= _number(item["viral_score"]) <= 100:
            raise InvalidOutputError("Viral score must be 1-100.")
        _interval(item, a, b, start="hook_start_time", end="hook_end_time")
        if not storyline_cuts:
            _ordered_intervals(item["keep_segments"], a, b)
        _ordered_intervals(item["broll_list"], a, b)
        # Hook montage items may deliberately be re-ordered, but stay in source clip.
        for hook in item["hook_v2"]["items"]:
            _interval(hook, a, b)
        if item["hook_v2"]["enabled"] and not item["hook_v2"]["items"]:
            raise InvalidOutputError("Hook V2 is on but has no segments.")
        intervals.append((a, b))
    # Storyline clips span wide stretches of the source, which may overlap.
    ordered = [] if storyline_cuts else sorted(intervals)
    if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
        raise InvalidOutputError("Clips overlap; pick different moments.")
    return clips
