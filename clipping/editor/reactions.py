"""
clipping.editor.reactions — Reaction clips and pictures from assets/reactions/.

A reaction can go over the video (see overlays.py) or be **inserted into the
cut**: it becomes a moment of its own, between the others, so the finished
video gets longer by the time it stays on screen. Being a normal moment, it
moves captions, voice lines, sound effects, preview and export along with no
special cases.

Videos, GIFs and still pictures all work. A short video or GIF repeats until
its time is up.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

from clipping.editor import timeline
from clipping.editor.project import Framing, Project, Segment, Source

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
GIF_EXTS = (".gif",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
ALL_EXTS = VIDEO_EXTS + GIF_EXTS + IMAGE_EXTS

SECONDS = (0.5, 10.0)         # how long an inserted reaction can stay
DEFAULT_SECONDS = 2.0
SOURCE_LENGTH = 30.0          # reactions repeat, so any trim up to this is valid
SNAP = 0.25                   # this close to a moment's edge: go between, don't cut a sliver
THUMB_DIR = os.path.join(os.path.expanduser("~"), ".cache", "clip-editor", "thumbs")


def kind_of(path: str) -> str:
    """"video", "gif" or "image", judged by what is in the file (many
    downloaded pictures have the wrong ending). "" when it can't be used."""
    if path.lower().endswith(VIDEO_EXTS):
        return "video"
    try:
        from PIL import Image
        with Image.open(path) as im:
            if getattr(im, "is_animated", False):
                return "gif" if im.format == "GIF" else ""   # ffmpeg can't play animated WebP
            return "image"
    except Exception:
        return ""


def input_args(path: str, seconds: float, fps: int = 30) -> list[str]:
    """ffmpeg input options that show ``path`` for ``seconds``."""
    kind = kind_of(path)
    length = f"{seconds:.3f}"
    if kind == "image":
        return ["-loop", "1", "-framerate", str(fps), "-t", length, "-i", path]
    if kind == "gif":
        return ["-ignore_loop", "0", "-t", length, "-i", path]
    return ["-stream_loop", "-1", "-t", length, "-i", path]


def natural_seconds(path: str) -> float:
    """How long a reaction plays by itself, kept between 1 and the limit."""
    seconds = DEFAULT_SECONDS
    try:
        kind = kind_of(path)
        if kind == "video":
            from clipping.editor import media
            seconds = media.probe(path).duration or seconds
        elif kind == "gif":
            from PIL import Image
            with Image.open(path) as im:
                total = 0
                for frame in range(im.n_frames):
                    im.seek(frame)
                    total += im.info.get("duration", 100)
            seconds = max(DEFAULT_SECONDS, total / 1000.0) if total else seconds
    except Exception:
        pass
    return round(min(SECONDS[1], max(1.0, seconds)), 1)


# ---------------------------------------------------------------- favourites
def favourite_key(path: str, root: str) -> str:
    """Stored relative to the reactions folder, so moving the app keeps them."""
    path, root = os.path.abspath(path), os.path.abspath(root)
    return os.path.relpath(path, root) if path.startswith(root + os.sep) else path


def favourite_paths(keys: list[str], root: str) -> list[str]:
    out = [k if os.path.isabs(k) else os.path.join(root, k) for k in keys or []]
    return [p for p in out if os.path.exists(p)]


# ---------------------------------------------------------------- thumbnails
def thumbnail(path: str, size: int = 96, cache_dir: str = THUMB_DIR) -> str | None:
    """A small square PNG of the reaction (cached). None when it can't be read."""
    try:
        stamp = f"{os.path.abspath(path)}|{os.path.getmtime(path)}|{size}"
        out = os.path.join(cache_dir, hashlib.sha1(stamp.encode()).hexdigest()[:20] + ".png")
        if os.path.exists(out):
            return out
        os.makedirs(cache_dir, exist_ok=True)
        from PIL import Image
        source, grab = path, None
        if kind_of(path) == "video":
            grab = out + ".frame.png"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                            "-frames:v", "1", grab], check=True, capture_output=True, timeout=30)
            source = grab
        with Image.open(source) as im:
            im.draft("RGB", (size * 2, size * 2))
            im = im.convert("RGB")
            im.thumbnail((size, size))
            card = Image.new("RGB", (size, size), (16, 20, 28))
            card.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
            card.save(out)
        if grab:
            os.unlink(grab)
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- inserting
def _source_for(project: Project, path: str) -> Source:
    path = os.path.abspath(path)
    for s in project.sources:
        if os.path.abspath(s.path) == path:
            return s
    width, height, has_audio = 1080, 1920, False
    if kind_of(path) == "video":
        from clipping.editor import media
        info = media.probe(path)
        width, height, has_audio = info.width, info.height, info.has_audio
    else:
        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size
    src = Source(path=path, name="Reaction: " + os.path.splitext(os.path.basename(path))[0][:24],
                 duration=SOURCE_LENGTH, width=width, height=height, has_audio=has_audio, kind="reaction")
    project.sources.append(src)
    return src


def place_for(project: Project, edit_time: float) -> tuple[int, float | None]:
    """(position in the cut list, source time to split at or None) for a
    reaction inserted at ``edit_time``."""
    pieces = timeline.layout(project.segments)
    if not pieces or edit_time <= SNAP:
        return 0, None
    for p in pieces:
        if edit_time < p.edit_end - SNAP:
            if edit_time <= p.edit_start + SNAP:
                return p.index, None
            if project.segments[p.index].reaction:     # never cut a reaction in two
                return p.index + 1, None
            return p.index + 1, p.source_in + (edit_time - p.edit_start) * p.speed
        if edit_time <= p.edit_end + SNAP:
            return p.index + 1, None
    return len(pieces), None


def insert(project: Project, path: str, edit_time: float, seconds: float | None = None,
           volume: float = 0.9, whole_picture: bool = True) -> Segment:
    """Put a reaction into the cut at ``edit_time``; the moment there is split
    when the point is inside it. Returns the new moment."""
    if not kind_of(path):
        raise ValueError("That file can't be used as a reaction.")
    seconds = natural_seconds(path) if seconds is None else seconds
    seconds = round(min(SECONDS[1], max(SECONDS[0], float(seconds))), 2)
    src = _source_for(project, path)
    index, split_at = place_for(project, edit_time)
    did_split = False
    if split_at is not None:
        project.split_segment(index - 1, round(split_at, 3))
        did_split = True
    seg = Segment(source_id=src.id, source_in=0.0, source_out=seconds, reaction=True, captioned=True,
                  original_level=round(min(1.0, max(0.0, float(volume))), 2), split_here=did_split,
                  framing=Framing(mode="fit_blur" if whole_picture else "crop"))
    project.segments.insert(index, seg)
    return seg


def _same_take(a: Segment, b: Segment) -> bool:
    return (a.source_id == b.source_id and abs(a.source_out - b.source_in) < 0.002 and a.speed == b.speed
            and a.framing == b.framing and a.original_level == b.original_level
            and not a.reaction and not b.reaction)


def remove(project: Project, segment_id: str) -> None:
    """Take an inserted reaction out. If putting it in had cut a moment in
    two and the halves are still untouched, they are joined again."""
    i = project.segment_index(segment_id)
    gone = project.segments.pop(i)
    if not (gone.split_here and 0 < i < len(project.segments)):
        return
    first, second = project.segments[i - 1], project.segments[i]
    if not _same_take(first, second):
        return
    first.source_out = second.source_out
    project.segments.pop(i)
    anchored = [*project.captions, *project.script_lines, *project.overlays,
                *(project.audio or {}).get("sfx", [])]
    for item in anchored:                       # whatever sat on the second half
        if item.get("segment_id") == second.id:
            item["segment_id"] = first.id


def inserted(project: Project) -> list[tuple[Segment, float]]:
    """[(moment, where it starts in the finished video)] for inserted reactions."""
    return [(s, p.edit_start) for s, p in zip(project.segments, timeline.layout(project.segments)) if s.reaction]


def move(project: Project, segment_id: str, edit_time: float) -> Segment:
    """Move an inserted reaction so it starts at ``edit_time`` (of the video as it is now)."""
    i = project.segment_index(segment_id)
    seg = project.segments[i]
    start = timeline.layout(project.segments)[i].edit_start
    if edit_time > start:                       # the video gets shorter before the target
        edit_time = max(start, edit_time - seg.length)
    path = project.source(seg.source_id).path
    remove(project, segment_id)
    return insert(project, path, edit_time, seg.length, seg.original_level or 0.0,
                  seg.framing.mode == "fit_blur")
