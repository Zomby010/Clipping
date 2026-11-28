"""
clipping.editor.placement — Phase A: drag things on the video to place and
resize them.

The player can't draw handles over mpv's own window, so **Place** shows a
still frame of the cut and this module says where each thing sits on it and
how dragging changes it. Everything is in fractions of the frame, so the
540 x 960 preview, the still frame and the 1080 x 1920 export all agree.

Things you can move:

    hook        project.hook["x"], ["y"] (top-middle of the text), ["scale"]
    popup       a text overlay's "x", "y" (centre) and "scale"
    mark        an arrow or circle: "x", "y" (centre) and "size" (already there)
    corner      a corner reaction: "x", "y" (top-left) and "width"
    logo        project.logo["x"], ["y"] (top-left) and "size"

Items that were never dragged keep their named spot, so old edits look the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from clipping.editor import overlays as O
from clipping.editor import styles as S
from clipping.editor import text_layers as T

MIN_SIZE = {"mark": 0.08, "corner": 0.12, "logo": 0.05}
MAX_SIZE = {"mark": 0.5, "corner": 0.9, "logo": 0.4}
SCALE = (0.4, 3.0)


@dataclass
class Item:
    key: str            # "hook", "logo" or the overlay id
    kind: str           # hook / popup / mark / corner / logo
    label: str
    x: int              # box on the frame, in pixels of the frame size asked for
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float, pad: int = 4) -> bool:
        return self.x - pad <= px <= self.x + self.w + pad and self.y - pad <= py <= self.y + self.h + pad

    def on_handle(self, px: float, py: float, size: int = 14) -> bool:
        """Is (px, py) on the resize handle (bottom-right corner)?"""
        return abs(px - (self.x + self.w)) <= size and abs(py - (self.y + self.h)) <= size


def _text_box(text: str, size: int, w: int, centre_x: float, top: float) -> tuple[int, int, int, int]:
    """Rough box of a line of Anton text (it is a narrow font)."""
    chars = max(1, len(text))
    per_line = max(6, int((0.84 * w) / (0.52 * size)))
    lines = max(1, -(-chars // per_line))
    width = min(int(0.84 * w), int(chars * 0.52 * size) + size // 2)
    height = int(lines * size * 1.15)
    return int(centre_x - width / 2), int(top), width, height


def items_at(project, t: float, w: int, h: int, img_sizes: dict | None = None) -> list[Item]:
    """What can be moved on the frame at edit time ``t``. The hook and the logo
    are always listed (they are easy to lose otherwise). ``img_sizes`` maps a
    logo path to its picture size."""
    out: list[Item] = []
    hook = project.hook or {}
    if hook.get("text"):
        plain, emoji = T.split_emoji(hook["text"])
        size = int(S.HOOK_STYLE.size * h / 1920 * T.item_scale(hook))
        hx, hy = T.hook_place(hook) or (0.5, 0.12)
        x, y, bw, bh = _text_box(plain or emoji, size, w, hx * w, hy * h)
        if emoji and plain:
            bh += int(size * 1.3)
        out.append(Item("hook", "hook", "Hook", x, y, bw, bh))
    for o in project.overlays:
        kind = o.get("type")
        if kind == "text":
            if not (float(o.get("start", 0)) <= t <= float(o.get("end", 0))):
                continue
            plain, emoji = T.split_emoji(o.get("text", ""))
            size = int(S.POPUP_STYLE.size * h / 1920 * T.item_scale(o))
            fx, fy = T.popup_xy(o)
            x, y, bw, bh = _text_box(plain or emoji, size, w, fx * w, fy * h)
            y -= bh // 2
            if emoji and plain:
                y -= int(size * 1.2)
                bh += int(size * 1.2)
            out.append(Item(o["id"] if "id" in o else f"text@{o.get('start')}", "popup",
                            f"Pop-up: {o.get('text', '')[:18]}", x, y, bw, bh))
        elif kind in ("arrow", "circle") and o.get("visible", True):
            if not (float(o.get("start", 0)) <= t <= float(o.get("end", 0))):
                continue
            side = int(float(o.get("size", 0.22)) * w)
            x, y = O.mark_xy(o, w, h, side)
            out.append(Item(o["id"], "mark", kind.capitalize(), x, y, side, side))
        elif kind == "reaction" and o.get("style") == "corner" and o.get("visible", True):
            if not (float(o.get("start", 0)) <= t <= float(o.get("end", 0))):
                continue
            x, y, bw, bh = O.reaction_rect(o, w, h)
            out.append(Item(o["id"], "corner", "Reaction", x, y, bw, bh))
    logo = project.logo or {}
    if logo.get("path"):
        size = (img_sizes or {}).get(logo["path"]) or _image_size(logo["path"])
        x, y, lw, lh = O.logo_rect(logo, w, h, size)
        out.append(Item("logo", "logo", "Logo", x, y, lw, lh))
    return out


def _image_size(path: str) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (1, 1)


def find(project, key: str) -> dict:
    if key == "hook":
        return project.hook
    if key == "logo":
        return project.logo
    for o in project.overlays:
        if o.get("id") == key or f"text@{o.get('start')}" == key:
            return o
    raise KeyError(key)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return round(min(hi, max(lo, v)), 4)


def move(project, item: Item, dx: float, dy: float, w: int, h: int) -> None:
    """Move ``item`` by (dx, dy) pixels of a w x h frame."""
    data = find(project, item.key)
    nx, ny = item.x + dx, item.y + dy
    if item.kind == "hook":
        data["x"], data["y"] = _clamp((nx + item.w / 2) / w), _clamp(ny / h)
    elif item.kind == "popup":
        centre_y = ny + item.h / 2
        plain, emoji = T.split_emoji(data.get("text", ""))
        if emoji and plain:
            size = int(S.POPUP_STYLE.size * h / 1920 * T.item_scale(data))
            centre_y = ny + int(size * 1.2) + (item.h - int(size * 1.2)) / 2
        data["x"], data["y"] = _clamp((nx + item.w / 2) / w), _clamp(centre_y / h)
    elif item.kind == "mark":
        data["x"], data["y"] = _clamp((nx + item.w / 2) / w), _clamp((ny + item.h / 2) / h)
    elif item.kind == "corner":
        data["x"], data["y"] = _clamp(nx / w), _clamp(ny / h)
        data.setdefault("width", round(item.w / w, 4))
    elif item.kind == "logo":
        data["x"], data["y"] = _clamp(nx / w), _clamp(ny / h)


def resize(project, item: Item, new_w: float, w: int, h: int) -> None:
    """Make ``item`` ``new_w`` pixels wide (its height follows its shape)."""
    data = find(project, item.key)
    factor = max(0.05, new_w / max(1, item.w))
    if item.kind in ("hook", "popup"):
        if item.kind == "hook" and T.hook_place(data) is None:
            data["x"], data["y"] = 0.5, 0.12   # pin it where it is, so it grows in place
        if item.kind == "popup" and not ("x" in data and "y" in data):
            data["x"], data["y"] = T.popup_xy(data)
        data["scale"] = round(min(SCALE[1], max(SCALE[0], T.item_scale(data) * factor)), 3)
        return
    frac = new_w / w
    lo, hi = MIN_SIZE[item.kind], MAX_SIZE[item.kind]
    if item.kind == "mark":
        data["size"] = _clamp(frac, lo, hi)
    elif item.kind == "corner":
        if not ("x" in data and "y" in data):
            data["x"], data["y"] = _clamp(item.x / w), _clamp(item.y / h)
        data["width"] = _clamp(frac, lo, hi)
    elif item.kind == "logo":
        if not ("x" in data and "y" in data):
            data["x"], data["y"] = _clamp(item.x / w), _clamp(item.y / h)
        data["size"] = _clamp(frac, lo, hi)


def reset(project, item: Item) -> None:
    """Back to the named spot and normal size."""
    data = find(project, item.key)
    for k in ("x", "y", "scale", "width"):
        data.pop(k, None)
