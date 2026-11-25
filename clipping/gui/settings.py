"""
clipping.gui.settings — Everything the app needs that is not drawing windows.

* Reads every option ``main.py`` understands straight from its argument
  parser, so a new command-line option shows up in the app by itself.
* Sorts the options into plain-English sections with friendly labels.
* Saves your choices (and named profiles, e.g. one per channel) in
  ``studio_settings.json`` next to main.py.
* Turns your choices into the ``python main.py ...`` command to run.
* Reads and writes API keys in ``.env``.

No Tk here, so it can be tested without a screen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

from clipping.editor.voice import VOICES as _EDITOR_VOICES

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAIN_PY = os.path.join(REPO_DIR, "main.py")
SETTINGS_FILE = os.path.join(REPO_DIR, "studio_settings.json")
ENV_FILE = os.path.join(REPO_DIR, ".env")

# Options the app handles itself (links box) or that are only kept for old scripts.
SKIP = {"help", "url", "tiktok"}

# Starting values for this app. They suit a laptop without a graphics card
# and our channels; the command line keeps its own defaults.
APP_DEFAULTS = {
    "clips": 3,
    "min_length": 15,
    "max_length": 60,
    "voiceover": True,
    "voiceover_voice": "en-GB-MaisieNeural",
    "voiceover_length": "normal",
    "ai_provider": "claude",
    "story_mode": "series",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "whisper_model": "small",
}

CLIP_LENGTHS = {
    "Mixed, 15 to 60 seconds": (15, 60),
    "Funny short, 15 to 25 seconds": (15, 25),
    "About 30 seconds": (25, 35),
    "About 45 seconds": (40, 50),
    "About 60 seconds": (50, 60),
    "Long, up to 3 minutes": (20, 179),
}

# The Clip Editor's free voices (most popular first), plus the Studio's own extras.
_STUDIO_EXTRA_VOICES = ["en-GB-MaisieNeural", "en-ZA-LukeNeural", "en-ZA-LeahNeural"]
VOICES = [v[0] for v in _EDITOR_VOICES] + [v for v in _STUDIO_EXTRA_VOICES if v not in {x[0] for x in _EDITOR_VOICES}]

API_KEYS = [
    ("GOOGLE_API_KEY", "Gemini API key (backup AI, recommended)", "https://aistudio.google.com/apikey"),
    ("ANTHROPIC_API_KEY", "Anthropic API key (optional: only if not using Claude Code)",
     "https://platform.claude.com/settings/keys"),
    ("PEXELS_API_KEY", "Pexels API key (for B-roll stock footage)", "https://www.pexels.com/api/"),
    ("HF_TOKEN", "Hugging Face token (only for podcast split-screen)", "https://huggingface.co/settings/tokens"),
    ("NVIDIA_API_KEY", "NVIDIA API key (only if AI provider is nvidia)", "https://build.nvidia.com/"),
]

SECTIONS = [
    "Claude and storyline",
    "Clips",
    "Captions and text",
    "Voice-over",
    "Reaction clips",
    "Music and B-roll",
    "Watermark",
    "Download",
    "AI and speech recognition",
    "Face tracking and podcasts",
    "Video quality",
    "Output folders",
    "Developer tools",
]

# Friendlier names. Anything not listed gets a label made from its name.
LABELS = {
    "clips": "Number of clips",
    "min_length": "Shortest clip (seconds)",
    "max_length": "Longest clip (seconds)",
    "ratio": "Shape",
    "hook_duration": "Hook teaser length (seconds)",
    "no_hook": "Hook teaser at the start",
    "hook_source": "Custom hook video (file or Drive link)",
    "hook_source_start": "Custom hook start (seconds)",
    "hook_v2": "Multi-hook intro (V2)",
    "hook_v2_items": "Multi-hook: number of mini hooks",
    "hook_v2_style": "Multi-hook: style hint for the AI",
    "white_flash_duration": "Flash between hooks (seconds)",
    "no_segment_trim": "Let the AI cut out boring middle parts",
    "silence_trim": "Cut silences hard",
    "no_subs": "Captions on the video",
    "words_per_sub": "Words per caption",
    "no_karaoke": "Karaoke highlight on captions",
    "font_style": "Caption style",
    "advanced_text": "Animated text (kinetic typography)",
    "advanced_text_hook": "Animated text on the hook",
    "voiceover": "AI voice-over",
    "voiceover_voice": "Voice",
    "voiceover_lang": "Voice-over language",
    "voiceover_style": "Voice-over style",
    "voiceover_length": "Voice-over length",
    "voiceover_volume": "Voice-over volume",
    "original_volume": "Game/creator volume while the narrator talks",
    "voiceover_mode": "Voice-over mode",
    "voiceover_offset": "Narrator starts after (seconds)",
    "edge_glow": "Edge glow on the whole clip",
    "edge_glow_mode": "Edge glow mode",
    "no_reactions": "Reaction clips",
    "reactions_dir": "Reaction clips folder",
    "max_reactions": "Most reactions per clip",
    "reaction_seconds": "Longest reaction (seconds)",
    "reaction_volume": "Reaction volume",
    "no_bgm": "Background music",
    "bgm_mode": "Music mixing",
    "no_broll": "B-roll stock footage",
    "watermark": "Watermark",
    "text": "Watermark text",
    "image": "Watermark image file",
    "opacity": "Watermark opacity (%)",
    "position": "Watermark position",
    "padding": "Watermark padding (pixels)",
    "watermark_font_size": "Watermark text size (0 = auto)",
    "watermark_scale": "Watermark image size (% of height)",
    "yt_cookies": "YouTube cookies (browser name or cookies.txt)",
    "source": "Where the video comes from",
    "source_height": "Download quality (max height)",
    "use_dlp_subs": "Use the video's own subtitles when available (faster)",
    "cleanup_source": "Delete the downloaded video afterwards",
    "ai_provider": "AI provider",
    "claude_backend": "How to reach Claude",
    "claude_model": "Claude model",
    "no_claude_frames": "Let Claude look at the video (pictures)",
    "claude_max_sheets": "Pictures of the whole video (sheets of 9 frames)",
    "story_mode": "Story mode",
    "story_brief": "Story direction",
    "story_angles": "Story angles (one per line)",
    "gemini_model": "Gemini model",
    "gemini_fallback_model": "Gemini backup model",
    "load_gemini_json": "Reuse the last AI answer (skip the AI step)",
    "whisper_model": "Speech recognition model",
    "whisper_device": "Speech recognition runs on",
    "whisper_compute_type": "Speech recognition precision",
    "render_height": "Output height (pixels)",
    "no_library": "Sort finished clips into 'My Clips'",
    "library_dir": "'My Clips' folder location",
}

# Options whose name starts with "no_" are shown the positive way round:
# a ticked "Background music" box means --no-bgm is NOT passed.
INVERTED_PREFIX = "no_"


def _section_for(dest: str) -> str:
    d = dest
    if d.startswith(("claude_", "story_")) or d in ("no_claude_frames", "ai_provider"):
        return "Claude and storyline"
    if d in ("clips", "min_length", "max_length", "ratio", "hook_duration", "no_hook", "hook_source",
             "hook_source_start", "white_flash_duration", "no_segment_trim", "silence_trim",
             "render_height") or d.startswith("hook_v2"):
        return "Clips"
    if d in ("no_subs", "words_per_sub", "no_karaoke", "font_style", "advanced_text", "advanced_text_hook"):
        return "Captions and text"
    if d.startswith("voiceover") or d in ("original_volume", "edge_glow", "edge_glow_mode"):
        return "Voice-over"
    if "reaction" in d:
        return "Reaction clips"
    if d in ("no_bgm", "bgm_mode", "no_broll"):
        return "Music and B-roll"
    if d in ("watermark", "text", "image", "opacity", "position", "padding") or d.startswith("watermark_"):
        return "Watermark"
    if d in ("yt_cookies", "source", "source_height", "use_dlp_subs", "cleanup_source"):
        return "Download"
    if d.startswith(("whisper", "gemini", "nvidia", "vllm", "local_")) or d in ("ai_provider", "load_gemini_json"):
        return "AI and speech recognition"
    if d.startswith(("track_", "split_", "switch_", "yolo")) or d in (
        "face_detector", "static_crop", "scene_cut_threshold", "camera_switch", "split_screen",
        "dynamic_split", "diarization_speakers",
    ):
        return "Face tracking and podcasts" if d != "track_lines" else "Developer tools"
    if d.startswith("video_"):
        return "Video quality"
    if d in ("no_library", "library_dir"):
        return "Output folders"
    if d.startswith("dev_mode") or d in ("box_face_detection",):
        return "Developer tools"
    return "Developer tools"


def _auto_label(dest: str) -> str:
    text = dest[len(INVERTED_PREFIX):] if dest.startswith(INVERTED_PREFIX) else dest
    text = text.replace("_", " ")
    for short, full in (("bgm", "background music"), ("vllm", "vLLM"), ("yolo", "YOLO"),
                        ("cq", "CQ"), ("crf", "CRF"), ("iou", "IoU")):
        text = " ".join(full if w == short else w for w in text.split())
    return text[:1].upper() + text[1:]


@dataclass
class Field:
    dest: str
    flag: str
    kind: str                # "bool", "choice" or "text"
    default: object          # the command line's own default
    help: str = ""
    choices: list = field(default_factory=list)
    value_type: object = str
    inverted: bool = False   # ticked box = flag NOT passed
    section: str = "Developer tools"
    label: str = ""

    @property
    def start_value(self):
        """What the app shows the first time (app default, else the command line's)."""
        if self.dest in APP_DEFAULTS:
            return APP_DEFAULTS[self.dest]
        if self.kind == "bool":
            return not bool(self.default) if self.inverted else bool(self.default)
        return "" if self.default is None else self.default


def build_parser() -> argparse.ArgumentParser:
    from clipping.config import _build_parser
    return _build_parser()


def get_fields(parser: argparse.ArgumentParser | None = None) -> list[Field]:
    """Every option of main.py, in section order."""
    parser = parser or build_parser()
    fields = []
    for action in parser._actions:
        if action.dest in SKIP or not action.option_strings:
            continue
        flag = next((o for o in action.option_strings if o.startswith("--")), action.option_strings[0])
        help_text = (action.help or "").replace("%%", "%")
        if isinstance(action, argparse._StoreTrueAction):
            kind = "bool"
        elif action.choices:
            kind = "choice"
        else:
            kind = "text"
        inverted = kind == "bool" and action.dest.startswith(INVERTED_PREFIX)
        fields.append(Field(
            dest=action.dest,
            flag=flag,
            kind=kind,
            default=action.default,
            help=help_text,
            choices=[str(c) for c in action.choices] if action.choices else [],
            value_type=action.type or str,
            inverted=inverted,
            section=_section_for(action.dest),
            label=LABELS.get(action.dest) or _auto_label(action.dest),
        ))
    order = {name: i for i, name in enumerate(SECTIONS)}
    fields.sort(key=lambda f: order.get(f.section, len(SECTIONS)))
    return fields


def default_values(fields: list[Field]) -> dict:
    return {f.dest: f.start_value for f in fields}


# ------------------------------------------------------------------ saving

def load_settings(fields: list[Field], path: str | None = None) -> dict:
    """{"values": {...}, "profiles": {name: {...}}, "last_profile": name}."""
    path = path or SETTINGS_FILE
    data = {"values": {}, "profiles": {}, "last_profile": ""}
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update({k: saved[k] for k in data if k in saved})
    except (OSError, ValueError):
        pass
    values = default_values(fields)
    known = set(values)
    values.update({k: v for k, v in (data.get("values") or {}).items() if k in known})
    data["values"] = values
    return data


def save_settings(data: dict, path: str | None = None) -> None:
    path = path or SETTINGS_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ command

class SettingError(ValueError):
    """A setting holds something main.py would reject."""


def build_command(fields: list[Field], values: dict, urls: list[str], python: str | None = None) -> list[str]:
    """The exact command the app runs. Only settings that differ from main.py's defaults are passed."""
    urls = [u.strip() for u in urls if u and u.strip()]
    if not urls:
        raise SettingError("Paste at least one video link.")
    cmd = [python or sys.executable, "-u", MAIN_PY, "--url", *urls]
    for f in fields:
        value = values.get(f.dest, f.start_value)
        if f.kind == "bool":
            on = bool(value)
            passed = (not on) if f.inverted else on
            if passed:
                cmd.append(f.flag)
            continue
        text = "" if value is None else str(value).strip()
        if text == "":
            continue
        if f.value_type in (int, float):
            try:
                number = f.value_type(text)
            except ValueError:
                kind = "a whole number" if f.value_type is int else "a number"
                raise SettingError(f"'{f.label}' must be {kind} (it says '{text}').") from None
            if f.default is not None and number == f.default:
                continue
            text = str(number)
        elif f.kind == "choice":
            if text not in f.choices:
                raise SettingError(f"'{f.label}' must be one of: {', '.join(f.choices)}.")
            if text == str(f.default):
                continue
        elif f.default is not None and text == str(f.default):
            continue
        cmd += [f.flag, text]
    return cmd


# ------------------------------------------------------------------ .env keys

def read_env(path: str | None = None) -> dict:
    path = path or ENV_FILE
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def is_placeholder(value: str) -> bool:
    return not value or value.startswith("your-") or value.endswith("-here")


def write_env(updates: dict, path: str | None = None) -> None:
    """Set keys in .env, keeping every other line (and comments) as it was."""
    path = path or ENV_FILE
    lines = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        pass
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def library_dir(values: dict) -> str:
    return (str(values.get("library_dir") or "").strip()) or os.path.join(REPO_DIR, "My Clips")


# ------------------------------------------------------------------ run output

class OutputSplitter:
    """
    Splits a program's raw output into ("line", text) and ("progress", text).
    Text ending in a carriage return (\\r) is a progress bar redrawing itself:
    the window shows it by replacing the previous progress line, so a
    10-minute transcription is one line in the log, not thousands.
    """

    def __init__(self):
        import codecs
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buf = ""

    def feed(self, data: bytes) -> list[tuple[str, str]]:
        self._buf += self._dec.decode(data)
        out = []
        while True:
            cut = [i for i in (self._buf.find("\n"), self._buf.find("\r")) if i >= 0]
            if not cut:
                break
            i = min(cut)
            piece, sep, rest = self._buf[:i], self._buf[i], self._buf[i + 1:]
            if sep == "\r" and rest.startswith("\n"):
                sep, rest = "\n", rest[1:]
            elif sep == "\r" and not rest:
                break  # wait: the next byte may be the \n of a \r\n pair
            self._buf = rest
            out.append(("line" if sep == "\n" else "progress", piece))
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest = self._buf + self._dec.decode(b"", final=True)
        self._buf = ""
        rest = rest.rstrip("\r")
        return [("line", rest)] if rest else []
