"""
clipping.editor.platforms — One edit, every platform.

The 1080x1920 video works everywhere; what differs is the text that goes with
it and where each app draws its buttons.

Text per platform lives in project.upload, next to the YouTube fields:

    upload = {"title": ..., "description": ..., "hashtags": ...,      # YouTube
              "tiktok":    {"caption": ...},
              "instagram": {"caption": ...},
              "facebook":  {"title": ..., "description": ...}}

A field that was never edited isn't stored, so it keeps following the YouTube
text (change the YouTube title and the TikTok caption follows).
"""

from __future__ import annotations

import os

from clipping.editor import publish

# Button areas per app, as (name, x, y, w, h) fractions of the frame. Each
# fits inside render.SAFE_ZONES, which is all of them together.
PLATFORM_SAFE_ZONES = {
    "youtube": (("top", 0.0, 0.0, 1.0, 0.08), ("bottom", 0.0, 0.78, 1.0, 0.22), ("right", 0.86, 0.40, 0.14, 0.38)),
    "tiktok": (("top", 0.0, 0.0, 1.0, 0.07), ("bottom", 0.0, 0.80, 1.0, 0.20), ("right", 0.87, 0.38, 0.13, 0.40)),
    "instagram": (("top", 0.0, 0.0, 1.0, 0.07), ("bottom", 0.0, 0.80, 1.0, 0.20), ("right", 0.87, 0.42, 0.13, 0.36)),
    "facebook": (("top", 0.0, 0.0, 1.0, 0.06), ("bottom", 0.0, 0.80, 1.0, 0.20), ("right", 0.86, 0.40, 0.14, 0.36)),
}
ZONE_CHOICES = (("All", None), ("YouTube", "youtube"), ("TikTok", "tiktok"),
                ("Reels", "instagram"), ("Facebook", "facebook"))

PLATFORMS = {
    "tiktok": {"label": "TikTok", "fields": {"caption": 2200}, "shown": 150, "file": "TikTok.txt"},
    "instagram": {"label": "Instagram Reels", "fields": {"caption": 2200}, "max_hashtags": 30,
                  "file": "Instagram.txt"},
    "facebook": {"label": "Facebook Reels", "fields": {"title": 255, "description": 2200},
                 "file": "Facebook.txt"},
}
FIELD_LABELS = {"caption": "Caption", "title": "Title", "description": "Description"}


def safe_zones(platform: str | None):
    from clipping.editor.render import SAFE_ZONES
    return PLATFORM_SAFE_ZONES.get(platform or "", SAFE_ZONES)


def _hashtags(text: str, limit: int | None = None) -> str:
    tags = [t for t in (text or "").split() if t.startswith("#")]
    return " ".join(tags[:limit] if limit else tags)


def defaults(project, platform: str) -> dict:
    """The text a platform gets when nobody edited it, made from the YouTube text."""
    item = publish.upload_item(project)
    title, desc, tags = item["youtube_title_final"], item["youtube_description_final"], item["hastag"]
    if platform == "tiktok":
        return {"caption": f"{title} {tags}".strip()}
    if platform == "instagram":
        parts = [title, desc, _hashtags(tags, PLATFORMS["instagram"]["max_hashtags"])]
        return {"caption": "\n\n".join(p for p in parts if p.strip())}
    if platform == "facebook":
        return {"title": title, "description": "\n\n".join(p for p in (desc, tags) if p.strip())}
    raise KeyError(platform)


def text_for(project, platform: str) -> dict:
    """Edited text where there is some, the YouTube-based default elsewhere."""
    stored = (project.upload or {}).get(platform) or {}
    out = defaults(project, platform)
    for k in out:
        if isinstance(stored.get(k), str):
            out[k] = stored[k]
    return out


def is_edited(project, platform: str, field_name: str) -> bool:
    return isinstance(((project.upload or {}).get(platform) or {}).get(field_name), str)


def set_text(project, platform: str, field_name: str, text: str) -> bool:
    """Save edited text; text equal to the default stops being stored, so it
    keeps following YouTube. Returns True when something changed."""
    up = project.upload if isinstance(project.upload, dict) else {}
    project.upload = up
    before = dict(up.get(platform) or {})
    entry = dict(before)
    if text.strip() == defaults(project, platform)[field_name].strip():
        entry.pop(field_name, None)
    else:
        entry[field_name] = text
    if entry:
        up[platform] = entry
    else:
        up.pop(platform, None)
    return entry != before


def follow_youtube(project, platform: str) -> None:
    if isinstance(project.upload, dict):
        project.upload.pop(platform, None)


def limits(project, platform: str) -> list[str]:
    """Plain-English problems with the platform text (empty = fine)."""
    out = []
    spec = PLATFORMS[platform]
    text = text_for(project, platform)
    for name, limit in spec["fields"].items():
        n = len(text.get(name, ""))
        if n > limit:
            out.append(f"{spec['label']} {FIELD_LABELS[name].lower()} is {n} characters; the limit is {limit}.")
    if spec.get("max_hashtags"):
        count = len(_hashtags(text.get("caption", "")).split())
        if count > spec["max_hashtags"]:
            out.append(f"{spec['label']} allows {spec['max_hashtags']} hashtags; this has {count}.")
    return out


def counter(project, platform: str, field_name: str) -> str:
    spec = PLATFORMS[platform]
    n = len(text_for(project, platform).get(field_name, ""))
    line = f"{n} / {spec['fields'][field_name]}"
    if spec.get("shown") and field_name == "caption" and n > spec["shown"]:
        line += f" (people see the first {spec['shown']} before \"more\")"
    return line


def file_text(project, platform: str) -> str:
    text = text_for(project, platform)
    lines = []
    for name in PLATFORMS[platform]["fields"]:
        lines += [FIELD_LABELS[name].upper(), text[name], ""]
    if project.channel == "Kids":
        lines += ["KIDS", "This video is made for children. Use the app's setting for audiences under 13.", ""]
    return "\n".join(lines)


def write_files(project, folder: str) -> list[str]:
    written = []
    for platform, spec in PLATFORMS.items():
        path = os.path.join(folder, spec["file"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(file_text(project, platform))
        written.append(path)
    return written


# ================================================================ Claude
CLAUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "tiktok_caption": {"type": "string"},
        "instagram_caption": {"type": "string"},
        "facebook_title": {"type": "string"},
        "facebook_description": {"type": "string"},
    },
    "required": ["tiktok_caption", "instagram_caption", "facebook_title", "facebook_description"],
    "additionalProperties": False,
}


def claude_prompt(project) -> str:
    item = publish.upload_item(project)
    kids = " This is a children's channel: keep every word suitable for young kids." if project.channel == "Kids" else ""
    return (
        "Rewrite the text for one vertical short video so it fits each app. Audience: English speakers, "
        f"mostly Kenya, Nigeria and South Africa, plus worldwide. Channel: {project.channel}.{kids}\n\n"
        f"YouTube title: {item['youtube_title_final']}\n"
        f"YouTube description: {item['youtube_description_final']}\n"
        f"Hashtags: {item['hastag']}\n\n"
        "Write:\n"
        "- tiktok_caption: one punchy line (under 150 characters) plus 3 to 5 hashtags.\n"
        "- instagram_caption: a hook line, one or two short lines, then 5 to 10 hashtags (at most 30).\n"
        "- facebook_title: under 80 characters.\n"
        "- facebook_description: two or three short lines, then 3 to 5 hashtags.\n"
        "Keep any music credit line that is in the description. Don't invent facts."
    )


def write_with_claude(project, settings=None, asker=None) -> dict:
    """Ask Claude for every platform's text; returns {platform: {field: text}}."""
    from clipping.editor import review
    raw = review._ask(claude_prompt(project), CLAUDE_SCHEMA, settings, asker)
    if not isinstance(raw, dict):
        raise review.ReviewError("Claude's answer didn't have the platform texts.")
    got = {
        "tiktok": {"caption": str(raw.get("tiktok_caption", "")).strip()},
        "instagram": {"caption": str(raw.get("instagram_caption", "")).strip()},
        "facebook": {"title": str(raw.get("facebook_title", "")).strip(),
                     "description": str(raw.get("facebook_description", "")).strip()},
    }
    for platform, fields in got.items():
        for name, text in fields.items():
            limit = PLATFORMS[platform]["fields"][name]
            fields[name] = text[:limit]
    return got


def apply_claude(project, got: dict) -> None:
    for platform, fields in got.items():
        for name, text in fields.items():
            if text:
                set_text(project, platform, name, text)
