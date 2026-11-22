"""
clipping.editor.overlays — Phase 4 things laid over the cut:

  sound effects   project.audio["sfx"]        a short sound at a moment
  reactions       project.overlays type "reaction"
                    cutaway  the reaction clip fills the screen for 1-2 s; the
                             main video keeps moving underneath (hidden, never
                             paused) and its sound carries on, quieter
                    corner   the reaction in a small rounded box over the video
  arrows/circles  project.overlays type "arrow" / "circle" (Pillow pictures)
  channel logo    project.logo                one picture in a top corner

Sound effects, reactions, arrows and circles are anchored like captions: each
remembers its moment (``segment_id``) and the time in that moment's video
(``src_time``). ``refresh()`` works out edit times again after every change,
so they follow when moments are reordered, sped up, trimmed or split. One
whose moment is deleted is hidden (undo brings it back).

    {"id": "sfx_1a2b3c4d", "path": ".../whoosh-1.wav", "kind": "whoosh",
     "segment_id": "seg_...", "src_time": 62.4, "gain": 0.8,
     "start": 3.1, "visible": true}

    {"id": "ovl_...", "type": "reaction", "path": ".../laugh/1.mp4",
     "style": "cutaway" | "corner", "position": "top-left", "length": 1.2,
     "gain": 0.9, "border": true, "segment_id": ..., "src_time": ...,
     "start": ..., "end": ..., "visible": true}

    {"id": "ovl_...", "type": "arrow", "direction": "down-left", "colour": "red",
     "size": 0.22, "x": 0.5, "y": 0.42, "length": 1.5, "segment_id": ...,
     "src_time": ..., "start": ..., "end": ..., "visible": true}
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field

from clipping.editor import timeline

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(REPO_DIR, "assets")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")
REACTIONS_DIR = os.path.join(ASSETS_DIR, "reactions")
LOGOS_DIR = os.path.join(ASSETS_DIR, "logos")

AUDIO_EXTS = (".wav", ".mp3", ".ogg", ".m4a", ".flac", ".opus")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
ANCHORED = ("reaction", "arrow", "circle")

REACTION_STYLES = (("cutaway", "Full screen (cut-away)"), ("corner", "Small box in a corner"))
CORNER_SPOTS = (("top-left", "Top left"), ("top-right", "Top right"), ("middle-left", "Middle left"))
CORNER_WIDTH = 0.35          # corner box width, fraction of the video width
REACTION_LENGTH = (0.8, 2.0)  # seconds, shortest and longest
CUTAWAY_MAIN_LEVEL = 0.4     # main sound while a cut-away shows

DIRECTIONS = ("up", "up-right", "right", "down-right", "down", "down-left", "left", "up-left")
DIRECTION_LABELS = {"up": "↑", "up-right": "↗", "right": "→", "down-right": "↘",
                    "down": "↓", "down-left": "↙", "left": "←", "up-left": "↖"}
MARK_COLOURS = {"red": (235, 40, 40), "yellow": (255, 214, 0), "white": (255, 255, 255)}
ANGLES = {"right": 0, "up-right": 45, "up": 90, "up-left": 135, "left": 180,
          "down-left": 225, "down": 270, "down-right": 315}   # degrees, counter-clockwise
LOGO_CORNERS = (("top-left", "Top left"), ("top-middle", "Top middle"))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ================================================================ libraries
def _list_files(folder: str, exts) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(exts) and not f.startswith("."))


def sfx_library(sfx_dir: str = SFX_DIR) -> dict[str, list[str]]:
    """{type: [sound files]} for every folder in assets/sfx that has sounds."""
    out = {}
    if os.path.isdir(sfx_dir):
        for name in sorted(os.listdir(sfx_dir)):
            files = _list_files(os.path.join(sfx_dir, name), AUDIO_EXTS)
            if files:
                out[name] = files
    return out


def reaction_library(reactions_dir: str = REACTIONS_DIR) -> dict[str, list[str]]:
    """{mood: [clips]} from assets/reactions/<mood>/ (same folders Clipping Studio uses)."""
    out = {}
    if os.path.isdir(reactions_dir):
        for name in sorted(os.listdir(reactions_dir)):
            files = _list_files(os.path.join(reactions_dir, name), VIDEO_EXTS)
            if files:
                out[name] = files
    return out


def add_to_library(path: str, folder: str) -> str:
    """Copy a file the person picked into a library folder. Returns the copy."""
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(target):
        base, ext = os.path.splitext(target)
        n = 2
        while os.path.exists(target):
            target, n = f"{base}-{n}{ext}", n + 1
        shutil.copy2(path, target)
    return target


# ================================================================ anchoring
def anchor_at(project, edit_time: float) -> tuple[str, float]:
    """(segment_id, source time) of the frame showing at ``edit_time``."""
    total = project.total_length
    if total <= 0:
        raise ValueError("The cut list is empty.")
    hit = timeline.edit_to_source(project.segments, min(max(0.0, edit_time), total - 1e-6))
    if hit is None:
        raise ValueError("That time is outside your cut.")
    index, src_time = hit
    return project.segments[index].id, round(src_time, 3)


def _place(project, item: dict) -> float | None:
    """Edit time of an anchored item, or None when its moment is gone or trimmed off."""
    by_id = {s.id: (i, s) for i, s in enumerate(project.segments)}
    own = by_id.get(item.get("segment_id"))
    if own is None:
        return None
    t = float(item.get("src_time", 0.0))
    found = own if own[1].source_in <= t < own[1].source_out else None
    if found is None:  # the moment was split and this is now in the other half
        for i, s in enumerate(project.segments):
            if s.source_id == own[1].source_id and s.source_in <= t < s.source_out:
                found = (i, s)
                break
    if found is None:
        return None
    item["segment_id"] = found[1].id
    return round(timeline.source_to_edit(project.segments, found[0], t), 3)


def refresh(project) -> None:
    """Recompute edit times of every sound effect, reaction, arrow and circle."""
    total = project.total_length
    for fx in sfx_items(project):
        start = _place(project, fx)
        fx["visible"] = start is not None and start < total
        if start is not None:
            fx["start"] = start
    for o in project.overlays:
        if o.get("type") not in ANCHORED:
            continue
        start = _place(project, o)
        o["visible"] = start is not None and start < total - 0.05
        if start is not None:
            o["start"] = start
            o["end"] = round(min(total, start + float(o.get("length", 1.5))), 3)
    sfx_items(project).sort(key=lambda f: f.get("start", 0))


def sfx_items(project) -> list[dict]:
    audio = project.audio if isinstance(project.audio, dict) else {}
    project.audio = audio
    return audio.setdefault("sfx", [])


def visible_sfx(project) -> list[dict]:
    return [f for f in sfx_items(project) if f.get("visible", True) and f.get("path")]


def anchored(project, kind: str | tuple = ANCHORED) -> list[dict]:
    kinds = (kind,) if isinstance(kind, str) else kind
    return [o for o in project.overlays if o.get("type") in kinds and o.get("visible", True)]


def find(project, item_id: str) -> dict:
    for f in sfx_items(project):
        if f["id"] == item_id:
            return f
    for o in project.overlays:
        if o.get("id") == item_id:
            return o
    raise KeyError(item_id)


def delete(project, item_id: str) -> None:
    audio = sfx_items(project)
    audio[:] = [f for f in audio if f["id"] != item_id]
    project.overlays = [o for o in project.overlays if o.get("id") != item_id]


def nudge(project, item_id: str, delta: float) -> None:
    """Move an item by ``delta`` seconds of the finished video."""
    item = find(project, item_id)
    seg = next((s for s in project.segments if s.id == item.get("segment_id")), None)
    speed = seg.speed if seg else 1.0
    lo, hi = (seg.source_in, seg.source_out - 0.01) if seg else (0.0, float("inf"))
    item["src_time"] = round(min(max(lo, float(item["src_time"]) + delta * speed), hi), 3)
    refresh(project)


# ================================================================ adding
def add_sfx(project, path: str, edit_time: float, gain: float = 0.8, kind: str = "") -> dict:
    seg_id, src_time = anchor_at(project, edit_time)
    item = {"id": _new_id("sfx"), "path": os.path.abspath(path),
            "kind": kind or os.path.basename(os.path.dirname(path)),
            "segment_id": seg_id, "src_time": src_time, "gain": float(gain)}
    sfx_items(project).append(item)
    refresh(project)
    return item


def add_reaction(project, path: str, edit_time: float, style: str = "cutaway", length: float = 1.2,
                 position: str = "top-left", gain: float = 0.9, border: bool = True) -> dict:
    seg_id, src_time = anchor_at(project, edit_time)
    length = min(REACTION_LENGTH[1], max(REACTION_LENGTH[0], float(length)))
    item = {"id": _new_id("ovl"), "type": "reaction", "path": os.path.abspath(path),
            "style": style if style in dict(REACTION_STYLES) else "cutaway",
            "position": position if position in dict(CORNER_SPOTS) else "top-left",
            "length": round(length, 2), "gain": float(gain), "border": bool(border),
            "segment_id": seg_id, "src_time": src_time}
    project.overlays.append(item)
    refresh(project)
    return item


def add_mark(project, kind: str, edit_time: float, x: float = 0.5, y: float = 0.42,
             direction: str = "down", colour: str = "red", size: float = 0.22,
             length: float = 1.5) -> dict:
    if kind not in ("arrow", "circle"):
        raise ValueError(kind)
    seg_id, src_time = anchor_at(project, edit_time)
    item = {"id": _new_id("ovl"), "type": kind, "x": float(x), "y": float(y),
            "direction": direction if direction in DIRECTIONS else "down",
            "colour": colour if colour in MARK_COLOURS else "red",
            "size": min(0.5, max(0.08, float(size))), "length": float(length),
            "segment_id": seg_id, "src_time": src_time}
    project.overlays.append(item)
    refresh(project)
    return item


# ================================================================ pictures
def safe_box(w: int, h: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) pixels kept clear of platform buttons:
    not the top 8 %, the bottom 22 %, or the right-side button strip."""
    return int(0.03 * w), int(0.08 * h), int(0.86 * w), int(0.78 * h)


def corner_rect(position: str, w: int, h: int) -> tuple[int, int, int, int]:
    """(x, y, box_w, box_h) of a corner reaction, inside the safe area."""
    bw = int(CORNER_WIDTH * w) // 2 * 2
    bh = bw
    left, top, right, _bottom = safe_box(w, h)
    pad = int(0.02 * w)
    if position == "top-right":
        return right - bw - pad, top + pad, bw, bh
    if position == "middle-left":
        return left + pad, int(0.40 * h), bw, bh
    return left + pad, top + pad, bw, bh


def rounded_mask(size: tuple[int, int], out_png: str, radius_frac: float = 0.12) -> str:
    from PIL import Image, ImageDraw
    img = Image.new("L", size, 0)
    r = int(min(size) * radius_frac)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=r, fill=255)
    img.save(out_png)
    return out_png


def rounded_border(size: tuple[int, int], out_png: str, width: int, radius_frac: float = 0.12) -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    r = int(min(size) * radius_frac)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=r,
                                          outline=(255, 255, 255, 255), width=width)
    img.save(out_png)
    return out_png


def mark_png(item: dict, w: int, out_png: str) -> tuple[str, int, int]:
    """Draw an arrow or circle; returns (png, width, height) in output pixels."""
    from PIL import Image, ImageDraw
    side = max(16, int(float(item.get("size", 0.22)) * w))
    colour = MARK_COLOURS.get(item.get("colour"), MARK_COLOURS["red"]) + (255,)
    edge = max(3, side // 18)
    big = side * 2  # draw large, shrink for smooth edges
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if item.get("type") == "circle":
        ring = max(6, big // 14)
        box = (edge * 2, edge * 2, big - edge * 2, big - edge * 2)
        d.ellipse(box, outline=(0, 0, 0, 255), width=ring + edge * 3)
        inner = (box[0] + edge * 1.5, box[1] + edge * 1.5, box[2] - edge * 1.5, box[3] - edge * 1.5)
        d.ellipse(inner, outline=colour, width=ring)
    else:
        # An arrow pointing right, centred, then turned to its direction.
        m = big / 2
        shaft, head = big * 0.13, big * 0.30
        x0, x1 = big * 0.10, big * 0.90
        pts = [(x0, m - shaft / 2), (x1 - head, m - shaft / 2), (x1 - head, m - head * 0.9),
               (x1, m), (x1 - head, m + head * 0.9), (x1 - head, m + shaft / 2), (x0, m + shaft / 2)]
        d.polygon(pts, fill=colour, outline=(0, 0, 0, 255), width=edge * 2)
        angle = ANGLES[item.get("direction", "down")]
        img = img.rotate(angle, resample=Image.BICUBIC, expand=False)
    img = img.resize((side, side), Image.LANCZOS)
    img.save(out_png)
    return out_png, side, side


def mark_xy(item: dict, w: int, h: int, side: int) -> tuple[int, int]:
    """Top-left corner for a mark centred on (x, y), kept on screen."""
    cx, cy = float(item.get("x", 0.5)) * w, float(item.get("y", 0.42)) * h
    x = int(min(max(0, cx - side / 2), w - side))
    y = int(min(max(0, cy - side / 2), h - side))
    return x, y


def logo_rect(logo: dict, w: int, h: int, img_size: tuple[int, int]) -> tuple[int, int, int, int]:
    lw = max(8, int(float(logo.get("size", 0.16)) * w)) // 2 * 2
    lh = max(2, int(lw * img_size[1] / max(1, img_size[0])) // 2 * 2)
    left, top, _r, _b = safe_box(w, h)
    pad = int(0.02 * w)
    if logo.get("corner") == "top-middle":
        return (w - lw) // 2, top + pad, lw, lh
    return left + pad, top + pad, lw, lh


# ================================================================ prepared layers
@dataclass
class Picture:
    """A still picture laid over the video: x, y and its time window."""
    png: str
    x: int
    y: int
    start: float
    end: float | None = None   # None = the whole video


@dataclass
class ReactionClip:
    path: str
    style: str
    start: float
    end: float
    gain: float
    has_audio: bool
    rect: tuple[int, int, int, int] | None = None   # corner only
    mask_png: str | None = None
    border_png: str | None = None


@dataclass
class VisualLayers:
    cutaways: list[ReactionClip] = field(default_factory=list)
    corners: list[ReactionClip] = field(default_factory=list)
    marks: list[Picture] = field(default_factory=list)
    logo: Picture | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def reactions(self) -> list[ReactionClip]:
        return self.cutaways + self.corners


def _has_audio(path: str) -> bool:
    try:
        from clipping.editor import media
        return bool(media.probe(path).has_audio)
    except Exception:
        return False


def prepare(project, w: int, h: int, workdir: str) -> VisualLayers:
    """Make the pictures (arrows, circles, masks, logo) for one render."""
    refresh(project)
    out = VisualLayers()
    for n, o in enumerate(anchored(project, "reaction")):
        if not os.path.exists(o["path"]):
            out.warnings.append(f"A reaction clip is missing: {o['path']}")
            continue
        clip = ReactionClip(o["path"], o.get("style", "cutaway"), float(o["start"]), float(o["end"]),
                            float(o.get("gain", 0.9)), _has_audio(o["path"]))
        if clip.style == "corner":
            x, y, bw, bh = corner_rect(o.get("position", "top-left"), w, h)
            clip.rect = (x, y, bw, bh)
            clip.mask_png = rounded_mask((bw, bh), os.path.join(workdir, f"mask{n}.png"))
            if o.get("border", True):
                clip.border_png = rounded_border((bw, bh), os.path.join(workdir, f"border{n}.png"),
                                                 max(2, bw // 45))
            out.corners.append(clip)
        else:
            out.cutaways.append(clip)
    for n, o in enumerate(anchored(project, ("arrow", "circle"))):
        png, side, _ = mark_png(o, w, os.path.join(workdir, f"mark{n}.png"))
        x, y = mark_xy(o, w, h, side)
        out.marks.append(Picture(png, x, y, float(o["start"]), float(o["end"])))
    logo = project.logo or {}
    if logo.get("path"):
        if os.path.exists(logo["path"]):
            from PIL import Image
            with Image.open(logo["path"]) as im:
                im = im.convert("RGBA")
                x, y, lw, lh = logo_rect(logo, w, h, im.size)
                im = im.resize((lw, lh), Image.LANCZOS)
                alpha = min(1.0, max(0.1, float(logo.get("opacity", 0.85))))
                im.putalpha(im.getchannel("A").point(lambda a: int(a * alpha)))
                png = os.path.join(workdir, "logo.png")
                im.save(png)
            out.logo = Picture(png, x, y, 0.0, None)
        else:
            out.warnings.append(f"The channel logo can't be found: {logo['path']}")
    return out


def fingerprint_data(project) -> dict:
    """What in the project changes these layers (for the preview cache)."""
    def stamp(path):
        return os.path.getmtime(path) if path and os.path.exists(path) else 0
    logo = dict(project.logo or {})
    logo["mtime"] = stamp(logo.get("path"))
    return {"overlays": [o for o in project.overlays if o.get("type") in ANCHORED],
            "logo": logo}

