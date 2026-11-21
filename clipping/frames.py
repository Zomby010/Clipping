"""
clipping.frames — Pictures of the video for Claude to look at.

Claude can't watch a video file, but it reads images well. We cut the
video into "contact sheets": one JPEG holding a grid of small frames
(3 x 3 by default), taken at an even step. A whole 20-minute video fits in
about a dozen sheets, which is cheap to send and enough to see what
happens on screen (a chase, a crash, a goal, a laugh).
"""

from __future__ import annotations

import os
import subprocess


def probe_duration(path: str) -> float | None:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        return float(res.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def plan_sheets(start: float, end: float, max_sheets: int, per_sheet: int = 9,
                min_step: float = 2.0) -> tuple[float, list[tuple[float, float]]]:
    """
    Pick the step between frames so the range fits in ``max_sheets`` sheets.
    Returns (step, [(first_frame_time, last_frame_time) per sheet]).
    """
    length = max(0.0, float(end) - float(start))
    if length <= 0 or max_sheets <= 0:
        return 0.0, []
    frames_wanted = max_sheets * per_sheet
    step = max(min_step, length / frames_wanted)
    n_frames = max(1, int(length // step))
    sheets = []
    for first in range(0, n_frames, per_sheet):
        last = min(n_frames, first + per_sheet) - 1
        # FFmpeg's fps filter takes each frame from the middle of its step.
        sheets.append((round(start + (first + 0.5) * step, 2), round(start + (last + 0.5) * step, 2)))
    return step, sheets[:max_sheets]


def make_sheets(
    video: str,
    out_dir: str,
    *,
    start: float = 0.0,
    end: float | None = None,
    max_sheets: int = 12,
    cols: int = 3,
    rows: int = 3,
    frame_width: int = 320,
    prefix: str = "sheet",
) -> list[dict]:
    """
    Write contact sheets for ``video`` between ``start`` and ``end`` seconds.
    Returns [{"path", "from", "to", "step"}] (frame times in source seconds,
    frames run left to right, top to bottom). Empty list if FFmpeg fails.
    """
    if end is None:
        end = probe_duration(video) or 0.0
    per_sheet = cols * rows
    step, spans = plan_sheets(start, end, max_sheets, per_sheet)
    if not spans:
        return []
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith(prefix + "_") and old.endswith(".jpg"):
            os.remove(os.path.join(out_dir, old))
    pattern = os.path.join(out_dir, f"{prefix}_%03d.jpg")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", video, "-t", f"{end - start:.3f}",
        "-vf", f"fps=1/{step:.4f},scale={frame_width}:-2,tile={cols}x{rows}:padding=4:margin=4",
        "-frames:v", str(len(spans)), "-q:v", "4", pattern,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    sheets = []
    for i, (t0, t1) in enumerate(spans, 1):
        path = os.path.join(out_dir, f"{prefix}_{i:03d}.jpg")
        if os.path.exists(path):
            sheets.append({"path": path, "from": t0, "to": t1, "step": round(step, 2)})
    return sheets


def describe_sheets(sheets: list[dict], cols: int = 3, rows: int = 3) -> str:
    """One line per sheet telling Claude which seconds it shows."""
    lines = []
    for i, s in enumerate(sheets, 1):
        lines.append(
            f"- Picture {i} ({os.path.basename(s['path'])}): {cols}x{rows} frames, left to right then top to bottom, "
            f"one every {s['step']:.1f}s, from {s['from']:.1f}s to {s['to']:.1f}s of the source video."
        )
    return "\n".join(lines)
