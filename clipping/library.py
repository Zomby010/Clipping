"""
clipping.library — Put finished clips into a tidy, human-friendly folder.

The pipeline works in ``outputs/`` (AI answers, manifests, temporary audio).
That folder is for the machine. After a run, this module gathers what a
person actually uses into one folder per run:

    My Clips/
      2026-11-21 13-05 - Heist goes wrong on the bridge/
        Videos/          1 - Heist goes wrong on the bridge.mp4
        Thumbnails/      1 - Heist goes wrong on the bridge.jpg
        Captions/        1 - Heist goes wrong on the bridge.srt
        Voice-overs/     1 - Heist goes wrong on the bridge.mp3
                         1 - Heist goes wrong on the bridge (script).txt
        Upload details/  1 - Heist goes wrong on the bridge.txt
        About this folder.txt

Finished videos and thumbnails are MOVED (no duplicate copies eating disk
space) and the manifest is updated to the new paths, so the YouTube and
Facebook uploaders keep working.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import importlib.util
from datetime import datetime


def _load_captions_export():
    # clipping/studio/ is a folder of helper files next to clipping/studio.py,
    # not a package, so load the helper by path (same as studio/core.py does).
    path = os.path.join(os.path.dirname(__file__), "studio", "captions_export.py")
    spec = importlib.util.spec_from_file_location("clipping_library_captions_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

FOLDERS = {
    "videos": "Videos",
    "thumbnails": "Thumbnails",
    "captions": "Captions",
    "voiceovers": "Voice-overs",
    "upload": "Upload details",
}

ABOUT_TEXT = """What's in this folder
=====================

Each clip has the same number and name in every folder.

Videos          The finished Shorts, ready to upload.
Thumbnails      A cover picture for each Short.
Captions        The words on screen as a .srt file. YouTube Studio accepts
                this under Subtitles > Upload file, and you can open it in
                any text editor to fix a word.
Voice-overs     The narrator's audio and the script it read, plus
                Storyline.txt (the whole story, part by part) when one was written.
Upload details  Title, description, hashtags and tags, ready to copy.

Source video: {source}
Made on: {made}
"""


def safe_name(text: str, limit: int = 60) -> str:
    """A title that is safe as a file name on Linux, Windows and macOS."""
    text = re.sub(r"[*?\"<>|]+", "", str(text or ""))
    text = re.sub(r"[\\/:\r\n\t]+", " ", text)
    text = re.sub(r"#\S+", "", text)  # hashtags make poor file names
    text = " ".join(text.split()).strip(" .")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" .,-")
    return text or "Clip"


def _unique_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists(f"{path} ({n})"):
        n += 1
    return f"{path} ({n})"


def _probe_duration(path: str) -> float | None:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        return float(res.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def _upload_text(item: dict) -> str:
    title = item.get("youtube_title_final") or item.get("title_inggris") or ""
    desc = item.get("youtube_description_final") or ""
    tags = item.get("youtube_tags_final") or []
    hashtags = item.get("hastag") or ""
    caption = item.get("tiktok_caption_final") or item.get("tiktok_caption") or ""
    ep = item.get("story_episode") or {}
    part_line = []
    if ep.get("mode") == "series" and ep.get("parts", 0) > 1:
        part_line = ["", f"SERIES: {ep.get('series_title') or ''} | Part {ep.get('part')} of {ep.get('parts')}: "
                         f"{ep.get('episode_title') or ''}".strip()]
    elif ep.get("episode_title"):
        part_line = ["", f"STORY TITLE: {ep['episode_title']}"]
    lines = [
        "TITLE",
        title,
        *part_line,
        "",
        "DESCRIPTION",
        desc,
        "",
        "HASHTAGS",
        hashtags,
        "",
        "TAGS (comma separated, for YouTube Studio)",
        ", ".join(tags) if isinstance(tags, list) else str(tags),
        "",
        "SHORT CAPTION (TikTok / Reels)",
        caption,
        "",
    ]
    if item.get("source_url"):
        lines += ["SOURCE", str(item["source_url"]), ""]
    return "\n".join(lines)


def organize_run(manifest: list[dict], cfg, story: dict | None = None) -> str | None:
    """
    Gather a run's finished files into ``<library_dir>/<date time - title>/``.
    Updates each manifest item in place (new paths, ``library_dir``) and
    removes the bulky caption data from it. Returns the run folder, or None
    when there was nothing to organize.
    """
    captions_export = _load_captions_export()

    done = [m for m in manifest if m.get("status") == "success" and os.path.exists(m.get("video_path") or "")]
    if not done:
        for m in manifest:
            m.pop("caption_lines", None)
        return None

    library_root = getattr(cfg, "library_dir", None) or os.path.join(cfg.base_dir, "My Clips")
    first = sorted(done, key=lambda m: m.get("rank", 0))[0]
    first_title = first.get("youtube_title_final") or first.get("title_inggris") or first.get("thumbnail_text")
    stamp = datetime.now().strftime("%Y-%m-%d %H-%M")
    run_dir = _unique_dir(os.path.join(library_root, f"{stamp} - {safe_name(first_title, 50)}"))
    dirs = {k: os.path.join(run_dir, v) for k, v in FOLDERS.items()}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    for item in sorted(done, key=lambda m: m.get("rank", 0)):
        title = item.get("youtube_title_final") or item.get("title_inggris") or item.get("thumbnail_text")
        base = f"{item.get('rank', 0)} - {safe_name(title)}"

        # Video (moved)
        new_video = os.path.join(dirs["videos"], base + ".mp4")
        final_duration = _probe_duration(item["video_path"])
        shutil.move(item["video_path"], new_video)
        item["video_path"] = new_video

        # Thumbnail (moved)
        thumb = item.get("thumbnail_path")
        if thumb and os.path.exists(thumb):
            new_thumb = os.path.join(dirs["thumbnails"], base + os.path.splitext(thumb)[1])
            shutil.move(thumb, new_thumb)
            item["thumbnail_path"] = new_thumb

        # Captions (.srt), shifted past the hook/teaser at the front
        lines = item.pop("caption_lines", None)
        if lines:
            main_dur = float(item.get("main_duration") or 0)
            offset = max(0.0, (final_duration or main_dur) - main_dur) if main_dur else 0.0
            srt_path = os.path.join(dirs["captions"], base + ".srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(captions_export.to_srt(lines, offset=offset))
            item["captions_path"] = srt_path

        # Voice-over audio (moved) + script
        vo_audio = item.get("voiceover_audio_path")
        if vo_audio and os.path.exists(vo_audio):
            new_vo = os.path.join(dirs["voiceovers"], base + ".mp3")
            shutil.move(vo_audio, new_vo)
            item["voiceover_audio_path"] = new_vo
            leftover_vtt = os.path.splitext(vo_audio)[0] + ".vtt"
            if os.path.exists(leftover_vtt):
                os.remove(leftover_vtt)
        if item.get("voiceover_script"):
            with open(os.path.join(dirs["voiceovers"], base + " (script).txt"), "w", encoding="utf-8") as f:
                f.write(item["voiceover_script"].strip() + "\n")

        # Upload details
        with open(os.path.join(dirs["upload"], base + ".txt"), "w", encoding="utf-8") as f:
            f.write(_upload_text(item))

        item["library_dir"] = run_dir

    for m in manifest:
        m.pop("caption_lines", None)

    # Drop empty sub-folders (e.g. no voice-over this run)
    for d in dirs.values():
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)

    if story and story.get("episodes"):
        from . import story as story_mod
        os.makedirs(dirs["voiceovers"], exist_ok=True)
        with open(os.path.join(dirs["voiceovers"], "Storyline.txt"), "w", encoding="utf-8") as f:
            f.write(story_mod.as_text(story))

    with open(os.path.join(run_dir, "About this folder.txt"), "w", encoding="utf-8") as f:
        f.write(ABOUT_TEXT.format(
            source=getattr(cfg, "url_youtube", "") or "",
            made=datetime.now().strftime("%d %B %Y, %H:%M"),
        ))
    return run_dir
