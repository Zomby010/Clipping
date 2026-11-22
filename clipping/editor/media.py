"""
clipping.editor.media — Small ffprobe / ffmpeg helpers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from clipping.editor.project import Source


class MediaError(RuntimeError):
    """A video could not be read."""


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def require_ffmpeg():
    missing = [t for t in ("ffmpeg", "ffprobe") if not tool_path(t)]
    if missing:
        raise MediaError("Clip Editor needs ffmpeg. On Ubuntu run:  sudo apt install ffmpeg")


def _fps(text: str) -> float:
    try:
        if "/" in text:
            a, b = text.split("/")
            return float(a) / float(b) if float(b) else 30.0
        return float(text)
    except (ValueError, ZeroDivisionError):
        return 30.0


def probe(path: str) -> Source:
    """Read a video's length, size, frame rate and whether it has sound."""
    require_ffmpeg()
    if not os.path.exists(path):
        raise MediaError(f"File not found: {path}")
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
        info = json.loads(out or "{}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise MediaError(f"Can't read {os.path.basename(path)}: is it a video file?") from e
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise MediaError(f"{os.path.basename(path)} has no picture in it.")
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    rotation = int((video.get("tags") or {}).get("rotate", rotation) or 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return Source(
        path=os.path.abspath(path),
        name=os.path.splitext(os.path.basename(path))[0],
        duration=round(duration, 3),
        width=width,
        height=height,
        fps=round(_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30"), 3),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def still_frame(path: str, seconds: float, out_png: str, height: int = 480) -> str:
    """Grab one picture at ``seconds`` (fast seek). Returns ``out_png``."""
    require_ffmpeg()
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, seconds):.3f}", "-i", path,
        "-frames:v", "1", "-vf", f"scale=-2:{int(height)}", out_png,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, check=True)
    if not os.path.exists(out_png):
        raise MediaError("Could not grab a picture at that time.")
    return out_png
