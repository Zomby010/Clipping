"""
clipping.editor.publish — The files that go next to an exported video:

    cover.jpg      the cover picture (a frame you picked, with an optional title)
    captions.srt   the captions as a subtitle file (YouTube Studio > Subtitles)
    YouTube.txt    title, description, hashtags and tags, in the same layout as
                   Clipping Studio's "Upload details" files

The upload text reuses ``clipping.library``'s writer and the .srt reuses
``clipping/studio/captions_export.py``, so both match Clipping Studio.
The cover follows the look of ``clipping/studio/thumbnail.py`` (dark overlay,
big centred title with an outline) but uses the frame you chose.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap

from clipping import library
from clipping.editor import captions as C
from clipping.editor import styles as S

TITLE_LIMIT = 100


def upload_fields(project) -> dict:
    """Title, description and hashtags, with the channel's defaults filled in."""
    up = dict(project.upload or {})
    up.setdefault("title", project.title)
    up.setdefault("description", "")
    up.setdefault("hashtags", S.DEFAULT_HASHTAGS.get(project.channel, "#shorts"))
    return up


def upload_item(project) -> dict:
    """The upload fields in the same keys Clipping Studio's manifest uses
    (youtube_title_final, youtube_description_final, hastag, ...)."""
    up = upload_fields(project)
    title = up["title"][:TITLE_LIMIT]
    hashtags = " ".join(up["hashtags"].split())
    tags = [t.lstrip("#") for t in hashtags.split() if t.lstrip("#")]
    item = {
        "youtube_title_final": title,
        "youtube_description_final": "\n\n".join(
            t for t in (up["description"].strip(), up.get("description_footer", "").strip()) if t),
        "hastag": hashtags,
        "youtube_tags_final": tags,
        "tiktok_caption_final": f"{title} {hashtags}".strip(),
    }
    from clipping.editor import audio_mix
    credit = audio_mix.music_credit(project)
    if credit:  # credit the music in the description itself, so it gets posted
        item["youtube_description_final"] = f"{item['youtube_description_final']}\n\n{credit}".strip()
    return item


def upload_text(project) -> str:
    text = library._upload_text(upload_item(project))
    if project.channel == "Kids":
        text += ("MADE FOR KIDS\n"
                 "Yes. When uploading, choose \"Yes, it's made for kids\" (YouTube requires this).\n")
    return text


def cover_time(project) -> float:
    total = project.total_length
    t = (project.cover or {}).get("time")
    if t is None:
        t = min(1.0, total / 2)
    return max(0.0, min(float(t), max(0.0, total - 0.05)))


def cover_picture_path(project) -> str | None:
    """The picture you chose for the cover, if it is still there."""
    path = (project.cover or {}).get("image")
    return path if path and os.path.exists(path) else None


def fit_cover(path: str, size: tuple[int, int] = (1080, 1920)):
    """Your picture filling the tall cover (centre crop)."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), size, Image.LANCZOS)


def make_cover(video_path: str, out_jpg: str, seconds: float, title: str = "",
               font_path: str | None = None, picture: str | None = None) -> str:
    """A 1080x1920 JPG of the frame at ``seconds`` (or of ``picture``), with
    ``title`` on it if given."""
    from PIL import Image, ImageDraw, ImageFont
    if picture:
        img = fit_cover(picture)
    else:
        with tempfile.TemporaryDirectory(prefix="clipedit_cover_") as tmp:
            png = os.path.join(tmp, "frame.png")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seconds:.3f}",
                            "-i", video_path, "-frames:v", "1", png], check=True, capture_output=True)
            img = Image.open(png).convert("RGB")
    title = (title or "").strip()
    if title:
        img = Image.alpha_composite(img.convert("RGBA"),
                                    Image.new("RGBA", img.size, (0, 0, 0, 110))).convert("RGB")
        draw = ImageDraw.Draw(img)
        size = int(img.size[0] * 0.11)
        try:
            font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
        lines = textwrap.wrap(title.upper(), width=12)[:5]
        y = (img.size[1] - len(lines) * (size + 12)) // 2
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            draw.text(((img.size[0] - (box[2] - box[0])) // 2, y), line, font=font, fill="white",
                      stroke_width=max(2, size // 16), stroke_fill="black")
            y += size + 12
    img.save(out_jpg, quality=92)
    return out_jpg


def write_extras(project, folder: str, video_path: str, fonts: dict | None = None) -> list[str]:
    """Write cover.jpg, captions.srt (when there are captions) and YouTube.txt."""
    written = []
    if fonts is None:
        fonts = S.ensure_fonts()
    cover = os.path.join(folder, "cover.jpg")
    make_cover(video_path, cover, cover_time(project), (project.cover or {}).get("title", ""),
               fonts.get(S.COVER_FONT), cover_picture_path(project))
    written.append(cover)
    lines = C.srt_lines(project)
    if lines:
        srt = os.path.join(folder, "captions.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(library._load_captions_export().to_srt(lines))
        written.append(srt)
    txt = os.path.join(folder, "YouTube.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(upload_text(project))
    written.append(txt)
    from clipping.editor import platforms   # phase 6: TikTok, Instagram, Facebook text
    written += platforms.write_files(project, folder)
    return written
