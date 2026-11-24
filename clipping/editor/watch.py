"""
clipping.editor.watch — Let Claude watch an edit and the reaction clips.

  watch(project, folder)   renders the quick preview of the cut (the same one
                           the window plays), takes a frame every second or
                           so and puts them on numbered contact sheets
                           (pictures Claude can look at), with the speech,
                           script lines and reactions already in the edit,
                           all in edit seconds.
  reaction_sheet()         one picture of every reaction clip, numbered, so
                           Claude can see which clip shows what.
  add_reactions(...)       put chosen reactions at edit times, with plain
                           warnings for spots that break the usual rules.

The pictures go in <edit>/Claude/ (and outputs/reactions-sheet.jpg).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile

from clipping.editor import captions as C
from clipping.editor import overlays as O
from clipping.editor import timeline

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REACTION_SHEET = os.path.join(REPO_DIR, "outputs", "reactions-sheet.jpg")
THUMB_W = 180          # one frame on a sheet, pixels wide (9:16 -> 320 tall)
PER_ROW = 8
ROWS_PER_SHEET = 2
MAX_FRAMES = 48
EDGE_GAP = 1.0         # keep reactions this far from the start and the end
MIN_GAP = 3.0          # and this far from each other


# ================================================================ drawing
def _font(size: int):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 os.path.join(REPO_DIR, "custom_fonts", "Montserrat-Bold.ttf")):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _label(img, text: str):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    f = _font(15)
    box = d.textbbox((0, 0), text, font=f)
    w, h = box[2] - box[0] + 10, box[3] - box[1] + 8
    d.rectangle((0, 0, w, h), fill=(0, 0, 0))
    d.text((5, 3), text, fill=(255, 230, 0), font=f)
    return img


def _sheets(images: list, labels: list[str], out_pattern: str, per_row=PER_ROW, rows=ROWS_PER_SHEET) -> list[str]:
    """Lay labelled pictures out on sheets of per_row x rows; returns the files."""
    from PIL import Image
    paths = []
    per_sheet = per_row * rows
    for s in range(0, len(images), per_sheet):
        chunk = images[s:s + per_sheet]
        tw = max(i.width for i in chunk)
        th = max(i.height for i in chunk)
        n_rows = (len(chunk) + per_row - 1) // per_row
        sheet = Image.new("RGB", (per_row * tw + (per_row - 1) * 4, n_rows * th + (n_rows - 1) * 4), (40, 40, 40))
        for k, (img, text) in enumerate(zip(chunk, labels[s:s + per_sheet])):
            sheet.paste(_label(img.copy(), text), ((k % per_row) * (tw + 4), (k // per_row) * (th + 4)))
        path = out_pattern % (len(paths) + 1)
        sheet.save(path, quality=82)
        paths.append(path)
    return paths


def _grab(video: str, t: float, width: int = THUMB_W):
    """One frame at ``t`` seconds as a Pillow picture (None if it fails)."""
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="clipwatch_") as tmp:
        out = os.path.join(tmp, "f.jpg")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", video,
                            "-frames:v", "1", "-vf", f"scale={width}:-2", out], capture_output=True)
        if r.returncode != 0 or not os.path.exists(out):
            return None
        with Image.open(out) as im:
            return im.convert("RGB")


# ================================================================ watching an edit
def frame_step(total: float) -> float:
    """Seconds between frames: every 0.5 s for short edits, at most MAX_FRAMES frames."""
    import math
    return max(0.5, math.ceil(total / MAX_FRAMES * 2) / 2)


def watch(project, folder: str, every: float = 0.0, log=None, preview_path: str | None = None) -> dict:
    """Everything Claude needs to 'watch' the edit; see the module doc."""
    from PIL import Image
    from clipping.editor import render
    total = project.total_length
    if total <= 0:
        raise ValueError("The cut list is empty.")
    if preview_path is None:
        if log:
            log("Making the quick preview of the cut…")
        preview_path = render.make_preview(project, folder, log=log)
    step = every or frame_step(total)
    out_dir = os.path.join(folder, "Claude")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    with tempfile.TemporaryDirectory(prefix="clipwatch_") as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", preview_path, "-vf",
                        f"fps=1/{step},scale={THUMB_W}:-2", os.path.join(tmp, "f%04d.jpg")], check=True)
        files = sorted(glob.glob(os.path.join(tmp, "f*.jpg")))
        images, frames = [], []
        for n, f in enumerate(files):
            t = round(n * step, 2)
            with Image.open(f) as im:
                images.append(im.convert("RGB"))
            frames.append({"n": n + 1, "time": t})
    labels = [f"{fr['n']} · {timeline.format_time(fr['time'], True)}" for fr in frames]
    sheets = _sheets(images, labels, os.path.join(out_dir, "watch-%02d.jpg"))
    pieces = timeline.layout(project.segments)
    C.refresh(project)
    O.refresh(project)
    return {
        "length": round(total, 2),
        "every_seconds": step,
        "sheets": sheets,
        "frames": frames,
        "moments": [{"n": p.index + 1, "start": round(p.edit_start, 2), "end": round(p.edit_end, 2),
                     "video": (project.source(project.segments[p.index].source_id).name
                               if project.source(project.segments[p.index].source_id) else "?")}
                    for p in pieces],
        "speech": [{"start": round(c["start"], 2), "end": round(c["end"], 2), "text": c["text"]}
                   for c in C.visible(project)],
        "script": [{"start": round(ln.get("start", 0.0), 2), "end": round(ln.get("end", 0.0), 2),
                    "text": ln.get("text", ""), "cue": ln.get("cue", "")} for ln in _script_lines(project)],
        "hook": (project.hook or {}).get("text", ""),
        "reactions_now": current_reactions(project),
        "mode": getattr(project, "mode", ""),
        "channel": project.channel,
    }


def _script_lines(project) -> list[dict]:
    try:
        from clipping.editor import script as S
        S.refresh(project)
        return S.visible(project)
    except Exception:
        return []


def current_reactions(project) -> list[dict]:
    O.refresh(project)
    return [{"id": o["id"], "start": round(o["start"], 2), "end": round(o["end"], 2), "style": o.get("style"),
             "clip": os.path.relpath(o["path"], O.REACTIONS_DIR) if o["path"].startswith(O.REACTIONS_DIR) else o["path"]}
            for o in O.anchored(project, "reaction")]


# ================================================================ the reaction clips
def reactions(reactions_dir: str = O.REACTIONS_DIR) -> list[dict]:
    """Every reaction clip, numbered: [{"n", "mood", "clip", "path", "length"}]."""
    from clipping.editor import media
    out = []
    for mood, files in O.reaction_library(reactions_dir).items():
        for path in files:
            try:
                length = round(media.probe(path).duration or 0.0, 2)
            except Exception:
                length = 0.0
            out.append({"n": len(out) + 1, "mood": mood, "clip": os.path.relpath(path, reactions_dir),
                        "path": path, "length": length})
    return out


def reaction_sheet(items: list[dict] | None = None, out_path: str = REACTION_SHEET) -> str | None:
    """One picture with the middle frame of every reaction clip, numbered like reactions()."""
    items = reactions() if items is None else items
    if not items:
        return None
    images, labels = [], []
    for it in items:
        img = _grab(it["path"], (it["length"] or 1.0) / 2)
        if img is None:
            from PIL import Image
            img = Image.new("RGB", (THUMB_W, THUMB_W), (80, 80, 80))
        images.append(img)
        labels.append(f"{it['n']} {it['mood']}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    base, ext = os.path.splitext(out_path)
    paths = _sheets(images, labels, base + "-%02d" + ext, per_row=6, rows=4)
    if len(paths) == 1:
        os.replace(paths[0], out_path)
        return out_path
    return paths[0]


# ================================================================ adding
def check_spot(project, at: float, length: float, others: list[tuple[float, float]]) -> list[str]:
    """Plain warnings for a reaction at ``at`` (it is still added)."""
    total = project.total_length
    warn = []
    if at < EDGE_GAP or at + length > total - EDGE_GAP:
        warn.append("very close to the start or the end")
    for a, b in others:
        if at < b + MIN_GAP and a < at + length + MIN_GAP:
            warn.append(f"less than {MIN_GAP:g} s from another reaction")
            break
    for ln in _script_lines(project):
        if ln.get("audio") and at < ln.get("end", 0) and ln.get("start", 0) < at + length:
            warn.append("over a voice-over line (a corner box keeps the voice clear)")
            break
    return warn


def pick_clip(choice, items: list[dict]) -> dict:
    """A reaction by its number, its file name, its path or its mood (first clip)."""
    if isinstance(choice, int) or (isinstance(choice, str) and choice.isdigit()):
        for it in items:
            if it["n"] == int(choice):
                return it
    text = str(choice).strip()
    for it in items:
        if text in (it["clip"], it["path"], os.path.basename(it["path"])):
            return it
    for it in items:
        if it["mood"] == text.lower():
            return it
    raise ValueError(f"No reaction clip called '{choice}'. Use list_reactions to see them.")


def add_reactions(project, wanted: list[dict], items: list[dict] | None = None) -> list[dict]:
    """``wanted`` = [{"at": edit seconds, "clip": number / name / mood,
    "style": "cutaway" | "corner", "length": 0.8-2 s, "position": corner spot}].
    Returns one row per reaction with its id and any warnings."""
    items = reactions() if items is None else items
    O.refresh(project)
    taken = [(o["start"], o["end"]) for o in O.anchored(project, "reaction")]
    out = []
    for w in sorted(wanted, key=lambda w: float(w.get("at", 0))):
        it = pick_clip(w.get("clip") or w.get("mood"), items)
        at = float(w.get("at", 0))
        if not 0 <= at < project.total_length:
            raise ValueError(f"{at} s is outside the edit (0 to {project.total_length:.1f} s).")
        length = float(w.get("length") or min(1.5, it["length"] or 1.2))
        style = w.get("style") or "cutaway"
        warn = check_spot(project, at, length, taken)
        item = O.add_reaction(project, it["path"], at, style, length, w.get("position") or "top-left")
        taken.append((at, at + length))
        out.append({"id": item["id"], "at": round(at, 2), "clip": it["clip"], "style": item["style"],
                    "length": round(min(O.REACTION_LENGTH[1], max(O.REACTION_LENGTH[0], length)), 2),
                    "warnings": warn})
    O.refresh(project)
    return out
