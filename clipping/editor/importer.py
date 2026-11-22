"""
clipping.editor.importer — Open Clipping Studio's AI clips as edits.

Clipping Studio sorts finished clips into

    My Clips/<date time - title>/
      Videos/<rank> - <title>.mp4
      Upload details/<rank> - <title>.txt     (TITLE / DESCRIPTION / HASHTAGS ...)

import_clip() makes a new edit from one of those videos: one moment covering
the whole clip, the whole tall picture shown (no crop), and the title,
description and hashtags read from its Upload details file. The clip already
has its captions burned into the picture, so no captions are added.
"""

from __future__ import annotations

import os
import re

from clipping.editor import media
from clipping.editor import project as P

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MY_CLIPS = os.path.join(REPO_DIR, "My Clips")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
BURNED_IN_NOTE = "This clip already has captions in the picture, so no captions were added."

CHANNEL_HINTS = (
    ("GTA 6", ("#gta6", "#gta", "#gtavi", "#gta5", "#gtaonline")),
    ("Sports", ("#football", "#sports", "#soccer", "#nba", "#basketball", "#premierleague")),
    ("Kids", ("#kids", "#forkids", "#kidsstories", "#nurseryrhymes", "#cartoon")),
)
SECTIONS = {"TITLE": "title", "DESCRIPTION": "description", "HASHTAGS": "hashtags"}


def list_runs(my_clips: str = DEFAULT_MY_CLIPS) -> list[tuple[str, list[str]]]:
    """[(run folder, [videos])], newest first."""
    out = []
    if not os.path.isdir(my_clips):
        return out
    for name in sorted(os.listdir(my_clips), reverse=True):
        videos_dir = os.path.join(my_clips, name, "Videos")
        if not os.path.isdir(videos_dir):
            continue
        videos = sorted((os.path.join(videos_dir, f) for f in os.listdir(videos_dir)
                         if f.lower().endswith(VIDEO_EXTS) and not f.startswith(".")),
                        key=_rank_key)
        if videos:
            out.append((os.path.join(my_clips, name), videos))
    return out


def _rank_key(path: str):
    m = re.match(r"(\d+)", os.path.basename(path))
    return (int(m.group(1)) if m else 9999, os.path.basename(path))


OTHER_HEADS = ("TAGS", "SHORT CAPTION", "SERIES:", "STORY TITLE:", "MADE FOR KIDS", "MUSIC")


def parse_upload_details(text: str) -> dict:
    """Read the TITLE / DESCRIPTION / HASHTAGS sections of an Upload details
    file (written by clipping.library._upload_text)."""
    out: dict[str, list[str]] = {}
    current = None
    previous_blank = True
    for line in (text or "").splitlines():
        head = line.strip()
        if previous_blank and head in SECTIONS:
            current = SECTIONS[head]
            out[current] = []
        elif previous_blank and head.startswith(OTHER_HEADS):
            current = None   # a section we don't use
        elif current:
            out[current].append(line)
        previous_blank = not head
    return {k: "\n".join(v).strip() for k, v in out.items()}


def details_for(video_path: str) -> dict:
    run = os.path.dirname(os.path.dirname(video_path))
    base = os.path.splitext(os.path.basename(video_path))[0]
    path = os.path.join(run, "Upload details", base + ".txt")
    try:
        with open(path, encoding="utf-8") as f:
            return parse_upload_details(f.read())
    except OSError:
        return {}


def guess_channel(hashtags: str) -> str | None:
    tags = {t.lower() for t in (hashtags or "").split()}
    for channel, hints in CHANNEL_HINTS:
        if tags & set(hints):
            return channel
    return None


def import_clip(video_path: str, channel: str = "GTA 6", source=None) -> P.Project:
    """A new edit made from one Clipping Studio clip (not saved yet)."""
    src = source or media.probe(video_path)
    details = details_for(video_path)
    name = os.path.splitext(os.path.basename(video_path))[0]
    title = details.get("title") or re.sub(r"^\d+\s*-\s*", "", name) or "Untitled edit"
    pr = P.Project(title=P.safe_name(title), channel=guess_channel(details.get("hashtags", "")) or channel)
    src = pr.add_source(src)
    end = src.duration or 0.0
    if end <= 0.1:
        raise ValueError("This clip seems to be empty.")
    seg = pr.add_segment(src.id, 0.0, end)
    seg.framing.mode = "fit_blur"        # show the whole tall clip, no crop
    pr.upload = {k: details[k] for k in ("title", "description", "hashtags") if details.get(k)}
    pr.review = {"note": BURNED_IN_NOTE, "imported_from": video_path}
    return pr
