"""
clipping.editor.templates — One look per channel, reused on every new edit.

A template is saved from a finished edit and holds only things that are the
same from video to video: caption look, hook length, music settings, logo,
loudness, the default framing, hashtags and a description footer. Moments,
captions, titles and other words that belong to one video are never copied.

    My Edits/Templates/GTA 6.json
"""

from __future__ import annotations

import copy
import json
import os

from clipping.editor import project as P

FOLDER = "Templates"
MUSIC_KEYS = ("path", "mood", "volume", "start_at", "fade_in", "fade_out", "loop", "ducking")


def path_for(edits_root: str, channel: str) -> str:
    return os.path.join(edits_root, FOLDER, f"{P.safe_name(channel)}.json")


def from_project(project) -> dict:
    """The parts of an edit that make up its channel's look."""
    audio = project.audio if isinstance(project.audio, dict) else {}
    music = {k: v for k, v in (audio.get("music") or {}).items() if k in MUSIC_KEYS}
    framing = next((s.framing.mode for s in project.segments if not s.reaction), "auto")
    up = project.upload or {}
    return {
        "channel": project.channel,
        "caption_style": copy.deepcopy(project.caption_style or {}),
        "hook_seconds": float((project.hook or {}).get("seconds", 2.0)),
        "music": music,
        "loudness": audio.get("loudness", -14.0),
        "logo": copy.deepcopy(project.logo or {}),
        "framing": framing,
        "hashtags": up.get("hashtags", ""),
        "description_footer": up.get("description_footer", ""),
    }


def save(project, edits_root: str) -> str:
    data = from_project(project)
    target = path_for(edits_root, project.channel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, target)
    return target


def load(edits_root: str, channel: str) -> dict | None:
    try:
        with open(path_for(edits_root, channel), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def apply(project, template: dict, new_edit: bool = False) -> None:
    """Use a template on an edit. Words belonging to this edit stay as they are;
    hashtags are only filled in when the edit has none."""
    if not template:
        return
    if template.get("caption_style") is not None:
        project.caption_style = copy.deepcopy(template["caption_style"])
    if template.get("hook_seconds") and (project.hook or {}).get("text"):
        project.hook["seconds"] = float(template["hook_seconds"])
    if not isinstance(project.audio, dict):
        project.audio = {}
    music = template.get("music") or {}
    if music.get("path") and os.path.exists(music["path"]):
        project.audio["music"] = copy.deepcopy(music)
    if "loudness" in template:
        project.audio["loudness"] = template["loudness"]
    logo = template.get("logo") or {}
    if logo.get("path") and os.path.exists(logo["path"]):
        project.logo = copy.deepcopy(logo)
    mode = template.get("framing")
    if mode in P.FRAMING_MODES:
        for seg in project.segments:
            if not seg.reaction:
                seg.framing.mode = mode
    up = project.upload if isinstance(project.upload, dict) else {}
    project.upload = up
    if template.get("hashtags") and (new_edit or not up.get("hashtags")):
        up["hashtags"] = template["hashtags"]
    if template.get("description_footer"):
        up["description_footer"] = template["description_footer"]
    if mode in P.FRAMING_MODES:
        project.defaults = {**(project.defaults or {}), "framing": mode}
