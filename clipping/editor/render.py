"""
clipping.editor.render — ffmpeg commands for the quick preview and the export.

Both come from the same builder, so what you preview is what you export:

  preview   540 x 960, "ultrafast", small file. Made in the background so the
            window never waits; cached by the edit's content.
  export    1080 x 1920 H.264 + AAC, "veryfast". One pass. Works for YouTube
            Shorts, TikTok, Instagram Reels and Facebook Reels.

Each moment is its own ffmpeg input with a fast seek (-ss before -i), so only
the parts you use are decoded. That keeps long source videos cheap on a CPU.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from clipping.editor import framing, text_layers
from clipping.editor.project import Project, Segment, Source, safe_name


@dataclass(frozen=True)
class Quality:
    width: int
    height: int
    preset: str
    crf: int
    audio_bitrate: str
    fps: int = 30


PREVIEW = Quality(540, 960, "ultrafast", 30, "96k")
EXPORT = Quality(1080, 1920, "veryfast", 20, "192k")

# Approximate areas covered by platform buttons and text on a 9:16 video
# (fractions of width/height). Shown on the preview so captions avoid them.
SAFE_ZONES = (
    ("top", 0.0, 0.0, 1.0, 0.08),
    ("bottom", 0.0, 0.78, 1.0, 0.22),
    ("right", 0.86, 0.38, 0.14, 0.40),
)


class RenderError(RuntimeError):
    """ffmpeg failed or the edit can't be rendered."""


class Cancelled(RuntimeError):
    """The user pressed Cancel."""


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def atempo_chain(speed: float) -> list[str]:
    """ffmpeg's atempo only takes 0.5..2 per step; chain steps for the rest."""
    speed = float(speed or 1.0)
    steps = []
    while speed > 2.0:
        steps.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        steps.append(0.5)
        speed /= 0.5
    if abs(speed - 1.0) > 1e-6:
        steps.append(speed)
    return [f"atempo={_num(s)}" for s in steps]


def video_filter(seg: Segment, src: Source, q: Quality, label_in: str, label_out: str) -> str:
    """Filter chain that turns one moment into a 9:16 piece of size q."""
    W, H = q.width, q.height
    speed = seg.speed or 1.0
    head = f"{label_in}setpts=(PTS-STARTPTS)/{_num(speed)}"
    tail = f"fps={q.fps},format=yuv420p,setsar=1{label_out}"
    fr = seg.framing
    if fr.mode == "fit_blur":
        bw, bh = max(2, (W // 8) // 2 * 2), max(2, (H // 8) // 2 * 2)
        zoomed_w = int(W * max(1.0, fr.zoom or 1.0)) // 2 * 2
        tag = seg.id
        return (
            f"{head},split=2[bg_{tag}][fg_{tag}];"
            f"[bg_{tag}]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=8:2,scale={W}:{H}[bgs_{tag}];"
            f"[fg_{tag}]scale={zoomed_w}:-2,crop='min(iw,{W})':'min(ih,{H})'[fgs_{tag}];"
            f"[bgs_{tag}][fgs_{tag}]overlay=(W-w)/2:(H-h)/2,{tail}"
        )
    cw, ch, x, y = framing.crop_box(src.width, src.height, fr.centre(), fr.zoom, W, H)
    return f"{head},crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=bicubic,{tail}"


def audio_filter(seg: Segment, src: Source, index: int, label_out: str,
                 level: float = 1.0, remove_talking: str = "") -> str:
    length = seg.length
    tempo = atempo_chain(seg.speed)
    if src.has_audio:
        chain = [f"[{index}:a]aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo",
                 "asetpts=PTS-STARTPTS", *tempo]
        # --- phase 3: voice --- the moment's own sound: quieter, muted, or without the talking
        if remove_talking and level > 0:
            chain.append(remove_talking)
        if abs(level - 1.0) > 1e-6:
            chain.append(f"volume={_num(max(0.0, level))}")
        # --- end phase 3 ---
    else:
        chain = ["anullsrc=r=48000:cl=stereo"]
    chain += ["apad", f"atrim=0:{_num(length)}", "asetpts=PTS-STARTPTS"]
    return ",".join(chain) + label_out


# --- phase 3: voice ---
MODE_ORIGINAL_LEVEL = {"explain": 1.0, "react": 1.0, "new_story": 0.0}
EXPLAIN_DIP = 0.25        # the original drops to this under a voice line in Explain mode
DIP_FADE = 0.15           # seconds to fade down and up
# "Keep game sounds, remove talking" (project.voices["remove_talking"] = "centre" or "band"):
REMOVE_TALKING = {
    # Removes what is the same in the left and right channel (speech is usually in the
    # middle of the picture), keeps what differs (game sounds, music, effects).
    # Tested on a real clip: speech recognition heard 72 words before and 1 after. It takes about
    # 10 dB off the level, so it is lifted back by 3x. A mono source has nothing to tell the
    # middle from the sides, so it goes quiet.
    "centre": "pan=stereo|c0=c0-c1|c1=c1-c0,volume=3.0",
    # Tested and not used: cutting the 300-3400 Hz band left 80 of 72 words audible to speech recognition.
    "band": "bandreject=f=1850:width_type=h:w=3100",
}


def original_level(project, seg: Segment) -> float:
    """How loud a moment's own sound is: its slider, else what the mode says."""
    if seg.original_level is not None:
        return min(1.0, max(0.0, float(seg.original_level)))
    return MODE_ORIGINAL_LEVEL.get(project.mode, 1.0)


def _dip_expression(voices: list[dict], depth: float = EXPLAIN_DIP, fade: float = DIP_FADE) -> str:
    """A volume expression (per frame) that dips to ``depth`` under every voice line,
    fading down and up over ``fade`` seconds."""
    terms = []
    for v in voices:
        a, b = v["start"], v["end"]
        terms.append(f"min(1,max(0,(t-({_num(a - fade)}))/{_num(fade)}))*"
                     f"min(1,max(0,(({_num(b + fade)})-t)/{_num(fade)}))")
    if not terms:
        return "1"
    inner = terms[-1]
    for term in reversed(terms[:-1]):   # ffmpeg's max() takes exactly two arguments, so nest them
        inner = f"max({term},{inner})"
    return f"1-{_num(1 - depth)}*{inner}"


def _mix_voice(parts: list[str], cmd: list[str], project: Project, first_input: int,
               voices: list[dict], audio_in: str, audio_out: str) -> None:
    """Put the voice-over lines on top of the original sound.

    Adds input files to ``cmd`` and filter lines to ``parts``: every voice is
    delayed to its place in the video, the original sound dips under the voices
    in Explain mode, and everything is mixed to ``audio_out``. The footage is
    never frozen or stretched for a voice. Kept in one function so the sound
    phase can route it through its own mixer."""
    labels = []
    for k, v in enumerate(voices):
        cmd += ["-i", v["path"]]
        ms = max(0, int(round(v["start"] * 1000)))
        gain = _num(v.get("gain", 1.0))
        parts.append(f"[{first_input + k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"asetpts=PTS-STARTPTS,adelay={ms}|{ms},volume={gain}[vo{k}]")
        labels.append(f"[vo{k}]")
    base = audio_in
    if project.mode == "explain":
        parts.append(f"{audio_in}volume='{_dip_expression(voices)}':eval=frame[adip]")
        base = "[adip]"
    parts.append(f"{base}{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:"
                 f"dropout_transition=0:normalize=0,alimiter=limit=0.97{audio_out}")
# --- end phase 3 ---


def build_command(project: Project, out_path: str, q: Quality = EXPORT,
                  layers: "text_layers.TextLayers | None" = None,
                  voices: "list[dict] | None" = None) -> list[str]:
    """The full ffmpeg command for this edit (no progress flags).
    ``layers`` adds captions / hook / pop-ups (.ass) and emoji pictures,
    drawn in the same pass. ``voices`` is ``voice.voice_clips(project)``: the
    voice-over lines to mix in."""
    if not project.segments:
        raise RenderError("The cut list is empty. Add at least one moment.")
    problems = project.problems()
    if problems:
        raise RenderError(problems[0])
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    parts = []
    for i, seg in enumerate(project.segments):
        src = project.source(seg.source_id)
        cmd += ["-ss", _num(seg.source_in), "-t", _num(seg.source_out - seg.source_in), "-i", src.path]
    # --- phase 3: voice ---
    talk_cut = REMOVE_TALKING.get((project.voices or {}).get("remove_talking") or "", "")
    # --- end phase 3 ---
    for i, seg in enumerate(project.segments):
        src = project.source(seg.source_id)
        parts.append(video_filter(seg, src, q, f"[{i}:v]", f"[v{i}]"))
        parts.append(audio_filter(seg, src, i, f"[a{i}]",
                                  level=original_level(project, seg), remove_talking=talk_cut))
    n = len(project.segments)
    joined = "".join(f"[v{i}][a{i}]" for i in range(n))
    audio_cat = "[acat]" if voices else "[aout]"   # phase 3: voices are mixed in after the concat
    parts.append(f"{joined}concat=n={n}:v=1:a=1[vcat]{audio_cat}")
    last = "vcat"
    if layers is not None and layers.ass_path:
        opts = f"filename={_filter_path(layers.ass_path)}"
        if layers.fonts_dir:
            opts += f":fontsdir={_filter_path(layers.fonts_dir)}"
        parts.append(f"[{last}]ass={opts}[vass]")
        last = "vass"
    for k, mark in enumerate(layers.emojis if layers is not None else []):
        cmd += ["-i", mark.png]
        parts.append(f"[{last}][{n + k}:v]overlay=x={mark.x}:y={mark.y}:eof_action=repeat:"
                     f"enable='between(t,{_num(mark.start)},{_num(mark.end)})'[ve{k}]")
        last = f"ve{k}"
    parts.append(f"[{last}]null[vout]")
    if voices:  # --- phase 3: voice ---
        _mix_voice(parts, cmd, project, n + len(layers.emojis if layers is not None else []),
                   voices, audio_cat, "[aout]")
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", q.preset, "-crf", str(q.crf), "-pix_fmt", "yuv420p",
        "-r", str(q.fps),
        "-c:a", "aac", "-b:a", q.audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart",
    ]
    if q is EXPORT:
        cmd += ["-profile:v", "high"]
    cmd.append(out_path)
    return cmd


def _filter_path(path: str) -> str:
    """Escape a file path for use as a filter option value."""
    for ch in ("\\", ":", "'", ",", "[", "]", ";", " "):
        path = path.replace(ch, "\\" + ch)
    return path


def text_layers_for(project: Project, q: Quality, workdir: str, fonts=None, log=None):
    """Captions, hook and pop-ups for this render (None when there are none)."""
    from clipping.editor import styles
    has_text = (project.captions or (project.hook or {}).get("text")
                or any(o.get("type") == "text" for o in project.overlays))
    if not has_text:
        return None
    if fonts is None:
        fonts = styles.ensure_fonts(log=log)
    layers = text_layers.prepare(project, q.width, q.height, workdir, fonts)
    for w in layers.warnings:
        if log:
            log(w)
    return layers


def safe_zone_filter() -> str:
    """drawbox filters that shade the platform button areas. Uses iw/ih, so it
    works at any size (the mpv preview and ffmpeg alike)."""
    boxes = []
    for _name, x, y, w, h in SAFE_ZONES:
        boxes.append(
            f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=black@0.35:t=fill"
        )
    return ",".join(boxes)


# ------------------------------------------------------------------ running
_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")


def run(cmd: list[str], total_seconds: float, on_progress=None, cancel: threading.Event | None = None,
        log=None) -> None:
    """Run ffmpeg with live progress (0..1) and a cancel switch."""
    full = cmd[:-1] + ["-progress", "pipe:1", "-nostats", "-loglevel", "error", cmd[-1]]
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_lines: list[str] = []

    def read_err():
        for line in proc.stderr:
            err_lines.append(line.rstrip())
            if log:
                log(line.rstrip())

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                break
            m = _TIME_RE.match(line.strip())
            if m and on_progress and total_seconds > 0:
                on_progress(min(1.0, int(m.group(1)) / 1_000_000 / total_seconds))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        try:
            os.unlink(cmd[-1])
        except OSError:
            pass
        raise Cancelled("Stopped.")
    if proc.returncode != 0:
        detail = "\n".join(err_lines[-6:]) or f"ffmpeg exit code {proc.returncode}"
        raise RenderError(f"The video could not be made.\n{detail}")
    if on_progress:
        on_progress(1.0)


def _voices(project: Project, folder: str | None = None) -> list[dict]:
    """The voice-over lines to mix in (phase 3)."""
    from clipping.editor import voice
    return voice.voice_clips(project, folder)


def edit_fingerprint(project: Project, q: Quality, voices: "list[dict] | None" = None) -> str:
    data = {
        # --- phase 3: voice --- which voice files, where, how loud, and the mode (it sets the dip)
        "voices": [[v["path"], v["start"], v["end"], v["gain"],
                    os.path.getmtime(v["path"]) if os.path.exists(v["path"]) else 0] for v in voices or []],
        "mode": project.mode, "talk_cut": (project.voices or {}).get("remove_talking") or "",
        # --- end phase 3 ---
        "q": [q.width, q.height, q.preset, q.crf],
        "segments": [dict(s.__dict__, framing=s.framing.__dict__) for s in project.segments],
        "text": text_layers.fingerprint_data(project),
        "sources": [[s.id, s.path, os.path.getmtime(s.path) if os.path.exists(s.path) else 0]
                    for s in project.sources],
    }
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]


def make_preview(project: Project, project_dir: str, on_progress=None, cancel=None, log=None,
                 fonts=None) -> str:
    """A small, fast copy of the whole edit for watching. Cached."""
    folder = os.path.join(project_dir, "Preview")
    os.makedirs(folder, exist_ok=True)
    voices = _voices(project, project_dir)   # phase 3
    out = os.path.join(folder, f"preview-{edit_fingerprint(project, PREVIEW, voices)}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        if on_progress:
            on_progress(1.0)
        return out
    for old in os.listdir(folder):  # keep the folder small
        if old.startswith("preview-") and old.endswith(".mp4"):
            try:
                os.unlink(os.path.join(folder, old))
            except OSError:
                pass
    tmp = out + ".part.mp4"
    with tempfile.TemporaryDirectory(prefix="clipedit_") as work:
        layers = text_layers_for(project, PREVIEW, work, fonts, log)
        run(build_command(project, tmp, PREVIEW, layers, voices), project.total_length, on_progress, cancel, log)
    os.replace(tmp, out)
    return out


def export(project: Project, ready_root: str, on_progress=None, cancel=None, log=None,
           now: datetime | None = None, fonts=None) -> dict:
    """Render the finished 1080 x 1920 video into 'Ready to post/<date> - <title>/'
    with its cover picture, captions file and YouTube upload text.
    Returns the export record (also added to project.exports)."""
    from clipping.editor import publish
    now = now or datetime.now()
    title = safe_name((project.upload or {}).get("title") or project.title)
    base = os.path.join(ready_root, f"{now:%Y-%m-%d} - {title}")
    folder, n = base, 2
    while os.path.exists(folder):
        folder, n = f"{base} ({n})", n + 1
    os.makedirs(folder)
    out = os.path.join(folder, f"{title}.mp4")
    tmp = out[:-4] + ".part.mp4"
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="clipedit_") as work:
            layers = text_layers_for(project, EXPORT, work, fonts, log)
            run(build_command(project, tmp, EXPORT, layers, _voices(project)),
                project.total_length, on_progress, cancel, log)
        os.replace(tmp, out)
    except BaseException:
        if not os.listdir(folder):
            os.rmdir(folder)
        raise
    took = round(time.monotonic() - started, 1)
    extras = publish.write_extras(project, folder, out, fonts=fonts)
    record = {
        "path": out,
        "folder": folder,
        "length": round(project.total_length, 2),
        "seconds_taken": took,
        "at": now.isoformat(timespec="seconds"),
        "size": "1080x1920",
        "files": [os.path.basename(p) for p in [out, *extras]],
    }
    project.exports.append(record)
    if log:
        log(f"Export finished in {took} s for a {round(project.total_length, 1)} s video: {out}")
    return record
