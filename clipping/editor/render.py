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

from clipping.editor import audio_mix, framing, overlays, reactions, text_layers
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
    """How loud a moment's own sound is: its slider, else what the mode says.
    "Replace the original sound" silences every moment of yours (not inserted reactions)."""
    if not seg.reaction and (project.voices or {}).get("mute_original"):
        return 0.0
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


# --- end phase 3 ---


def build_command(project: Project, out_path: str, q: Quality = EXPORT,
                  layers: "text_layers.TextLayers | None" = None,
                  visuals: "overlays.VisualLayers | None" = None,
                  voices: "list[dict] | None" = None) -> list[str]:
    """The full ffmpeg command for this edit (no progress flags).
    ``layers`` adds captions / hook / pop-ups (.ass) and emoji pictures;
    ``visuals`` adds reaction clips, arrows, circles and the logo (phase 4).
    ``voices`` is ``voice.voice_clips(project)``, the voice-over lines (phase 3;
    looked up when not given). Music, sound effects and loudness come from the
    project. Everything is drawn and mixed in this one pass."""
    if not project.segments:
        raise RenderError("The cut list is empty. Add at least one moment.")
    problems = project.problems()
    if problems:
        raise RenderError(problems[0])
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    parts = []
    for i, seg in enumerate(project.segments):
        src = project.source(seg.source_id)
        if seg.reaction:
            cmd += reactions.input_args(src.path, seg.source_out - seg.source_in, q.fps)
        else:
            cmd += ["-ss", _num(seg.source_in), "-t", _num(seg.source_out - seg.source_in), "-i", src.path]
    # --- phase 3: voice ---
    talk_cut = REMOVE_TALKING.get((project.voices or {}).get("remove_talking") or "", "")
    # --- end phase 3 ---
    for i, seg in enumerate(project.segments):
        src = project.source(seg.source_id)
        parts.append(video_filter(seg, src, q, f"[{i}:v]", f"[v{i}]"))
        parts.append(audio_filter(seg, src, i, f"[a{i}]",
                                  level=original_level(project, seg),
                                  remove_talking="" if seg.reaction else talk_cut))
    n = len(project.segments)
    k = n  # next input number
    joined = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{joined}concat=n={n}:v=1:a=1[vcat][acat]")
    last = "vcat"
    reaction_inputs = []
    # --- phase 4: reactions, arrows and circles, logo (under the text) ---
    if visuals is not None:
        W, H = q.width, q.height
        for j, r in enumerate(visuals.cutaways):
            cmd += _overlay_input(r, q.fps)
            reaction_inputs.append((k, r))
            parts.append(f"[{k}:v]setpts=PTS-STARTPTS+{_num(r.start)}/TB,fps={q.fps},"
                         f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                         f"format=yuv420p,setsar=1[rcut{j}]")
            parts.append(f"[{last}][rcut{j}]overlay=0:0:eof_action=pass:"
                         f"enable='between(t,{_num(r.start)},{_num(r.end)})'[vcut{j}]")
            last, k = f"vcut{j}", k + 1
        for j, r in enumerate(visuals.corners):
            x, y, bw, bh = r.rect
            cmd += _overlay_input(r, q.fps)
            reaction_inputs.append((k, r))
            parts.append(f"[{k}:v]setpts=PTS-STARTPTS+{_num(r.start)}/TB,fps={q.fps},"
                         f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                         f"format=yuva420p[rbox{j}]")
            k += 1
            cmd += ["-loop", "1", "-framerate", str(q.fps), "-t", _num(r.end - r.start), "-i", r.mask_png]
            parts.append(f"[{k}:v]setpts=PTS-STARTPTS+{_num(r.start)}/TB,format=gray[rmask{j}]")
            parts.append(f"[rbox{j}][rmask{j}]alphamerge[rround{j}]")
            parts.append(f"[{last}][rround{j}]overlay={x}:{y}:eof_action=pass:"
                         f"enable='between(t,{_num(r.start)},{_num(r.end)})'[vcor{j}]")
            last, k = f"vcor{j}", k + 1
            if r.border_png:
                cmd += ["-i", r.border_png]
                parts.append(f"[{last}][{k}:v]overlay={x}:{y}:eof_action=repeat:"
                             f"enable='between(t,{_num(r.start)},{_num(r.end)})'[vbor{j}]")
                last, k = f"vbor{j}", k + 1
        for j, mark in enumerate(visuals.marks):
            cmd += ["-i", mark.png]
            parts.append(f"[{last}][{k}:v]overlay=x={mark.x}:y={mark.y}:eof_action=repeat:"
                         f"enable='between(t,{_num(mark.start)},{_num(mark.end)})'[vmk{j}]")
            last, k = f"vmk{j}", k + 1
        if visuals.logo is not None:
            cmd += ["-i", visuals.logo.png]
            parts.append(f"[{last}][{k}:v]overlay=x={visuals.logo.x}:y={visuals.logo.y}:eof_action=repeat[vlogo]")
            last, k = "vlogo", k + 1
    if layers is not None and layers.ass_path:
        opts = f"filename={_filter_path(layers.ass_path)}"
        if layers.fonts_dir:
            opts += f":fontsdir={_filter_path(layers.fonts_dir)}"
        parts.append(f"[{last}]ass={opts}[vass]")
        last = "vass"
    for e, mark in enumerate(layers.emojis if layers is not None else []):
        cmd += ["-i", mark.png]
        x, y = text_layers.emoji_motion(mark.anim, mark.x, mark.y)
        if mark.anim:
            x, y = f"'{x}'", f"'{y}'"
        parts.append(f"[{last}][{k}:v]overlay=x={x}:y={y}:eof_action=repeat:"
                     f"enable='between(t,{_num(mark.start)},{_num(mark.end)})'[ve{e}]")
        last, k = f"ve{e}", k + 1
    parts.append(f"[{last}]null[vout]")
    # --- phase 3 + 4: sound (original, voices, music, sound effects, loudness) ---
    if voices is None:
        voices = audio_mix.voice_clips(project)
    main = "acat"
    if voices and project.mode == "explain":   # the original dips under the narrator
        parts.append(f"[acat]volume='{_dip_expression(voices)}':eval=frame[adip]")
        main = "adip"
    if audio_mix.needs_mixer(project, reaction_inputs, voices):
        has_original = any(s.has_audio for s in project.sources)
        extra, audio_parts = audio_mix.build(project, project.total_length, k, reaction_inputs,
                                             main=main, out="aout", has_original=has_original, voices=voices)
        cmd += extra
        parts += audio_parts
    else:
        parts.append(f"[{main}]anull[aout]")
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


def _overlay_input(r, fps: int) -> list[str]:
    """Input options for a reaction laid over the video."""
    if r.path.lower().endswith(reactions.VIDEO_EXTS):
        return ["-ss", "0", "-t", _num(r.end - r.start), "-i", r.path]
    return reactions.input_args(r.path, r.end - r.start, fps)


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


def visual_layers_for(project: Project, q: Quality, workdir: str, log=None):
    """Reactions, arrows, circles and the logo for this render (None when there are none)."""
    overlays.refresh(project)
    has = any(o.get("type") in overlays.ANCHORED for o in project.overlays) or (project.logo or {}).get("path")
    if not has:
        return None
    visuals = overlays.prepare(project, q.width, q.height, workdir)
    for w in visuals.warnings:
        if log:
            log(w)
    return visuals


def safe_zone_filter(zones=None) -> str:
    """drawbox filters that shade the platform button areas. Uses iw/ih, so it
    works at any size (the mpv preview and ffmpeg alike). ``zones`` defaults to
    all platforms together (see platforms.PLATFORM_SAFE_ZONES for each one)."""
    boxes = []
    for _name, x, y, w, h in (zones or SAFE_ZONES):
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
        "visuals": overlays.fingerprint_data(project),
        "audio": audio_mix.fingerprint_data(project),
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
        visuals = visual_layers_for(project, PREVIEW, work, log)
        run(build_command(project, tmp, PREVIEW, layers, visuals, voices), project.total_length,
            on_progress, cancel, log)
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
            visuals = visual_layers_for(project, EXPORT, work, log)
            run(build_command(project, tmp, EXPORT, layers, visuals, _voices(project)),
                project.total_length, on_progress, cancel, log)
        os.replace(tmp, out)
    except BaseException:
        if not os.listdir(folder):
            os.rmdir(folder)
        raise
    took = round(time.monotonic() - started, 1)
    loudness = audio_mix.measure_loudness(out) if audio_mix.loudness_target(project) is not None else None
    extras = publish.write_extras(project, folder, out, fonts=fonts)
    record = {
        "path": out,
        "folder": folder,
        "length": round(project.total_length, 2),
        "seconds_taken": took,
        "at": now.isoformat(timespec="seconds"),
        "size": "1080x1920",
        "loudness": loudness,
        "files": [os.path.basename(p) for p in [out, *extras]],
    }
    project.exports.append(record)
    if log:
        log(f"Export finished in {took} s for a {round(project.total_length, 1)} s video: {out}")
        if loudness is not None:
            log(f"Loudness: {loudness:.1f} LUFS (platforms play at about {audio_mix.LOUDNESS:g}).")
    return record
