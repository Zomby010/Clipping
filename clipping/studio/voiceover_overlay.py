"""
clipping.studio.voiceover_overlay — Voice-over that plays OVER the moving clip.

The original ("intro") mode froze the first frame, dimmed it and showed an
equalizer while the voice-over played, then started the clip. Viewers saw a
still picture for 20-40 seconds and scrolled away.

"overlay" mode keeps the clip playing the whole time:
  * the voice-over audio is laid over the clip's own audio, starting a little
    after the clip begins;
  * the game/creator audio is turned down (ducked) while the voice talks and
    fades back up afterwards;
  * the captions show the voice-over words while it talks, and the creator's
    words the rest of the time.

Everything here is pure Python + one audio-only FFmpeg pass (video is copied,
not re-encoded), so it adds only a few seconds per clip on a CPU laptop.
"""

from __future__ import annotations

import os
import subprocess


def clip_output_duration(m_start: float, m_end: float, keep_segments=None) -> float:
    """Length of the main clip as it appears in the final video, in seconds."""
    if keep_segments:
        return sum(float(s["end_time"]) - float(s["start_time"]) for s in keep_segments)
    return float(m_end) - float(m_start)


def plan_window(vo_duration: float, clip_duration: float, offset: float) -> tuple[float, float]:
    """
    Decide when the voice-over starts and stops, in clip time (0 = first frame
    of the main clip).

    The offset is pulled earlier if needed so the whole voice-over fits inside
    the clip. Returns (start, end).
    """
    offset = max(0.0, float(offset))
    vo_duration = max(0.0, float(vo_duration))
    clip_duration = max(0.0, float(clip_duration))
    if offset + vo_duration > clip_duration:
        offset = max(0.0, clip_duration - vo_duration)
    end = min(clip_duration, offset + vo_duration)
    return offset, end


def output_to_source_time(t_out: float, m_start: float, keep_segments=None) -> float:
    """
    Convert a time in the rendered main clip to the matching time in the
    original source video. With smart-trim segments the clip is several
    pieces glued together, so we walk the pieces.
    """
    if not keep_segments:
        return float(m_start) + t_out
    elapsed = 0.0
    for seg in keep_segments:
        s, e = float(seg["start_time"]), float(seg["end_time"])
        length = e - s
        if t_out < elapsed + length:
            return s + (t_out - elapsed)
        elapsed += length
    last = keep_segments[-1]
    return float(last["end_time"])


def merge_captions(
    data_segmen: list[dict],
    vo_segments: list[dict],
    window: tuple[float, float],
    m_start: float,
    keep_segments=None,
) -> list[dict]:
    """
    Build the caption track for the main clip in overlay mode.

    Creator caption lines that overlap the voice-over window are dropped and
    replaced by the voice-over's own lines, shifted into source-video time so
    the existing subtitle builder can use them unchanged.
    """
    start_out, end_out = window
    if end_out <= start_out or not vo_segments:
        return list(data_segmen)

    src_window_points = [
        output_to_source_time(start_out, m_start, keep_segments),
        output_to_source_time(max(start_out, end_out - 1e-3), m_start, keep_segments),
    ]

    def in_window_src(seg_start: float, seg_end: float) -> bool:
        if not keep_segments:
            return seg_end > src_window_points[0] and seg_start < src_window_points[1]
        # With trimmed segments the window may span several pieces: test the
        # segment against every piece the window touches.
        elapsed = 0.0
        for ks in keep_segments:
            s, e = float(ks["start_time"]), float(ks["end_time"])
            piece_out_start, piece_out_end = elapsed, elapsed + (e - s)
            elapsed = piece_out_end
            lo = max(piece_out_start, start_out)
            hi = min(piece_out_end, end_out)
            if hi <= lo:
                continue
            src_lo = s + (lo - piece_out_start)
            src_hi = s + (hi - piece_out_start)
            if seg_end > src_lo and seg_start < src_hi:
                return True
        return False

    kept = [
        seg for seg in data_segmen
        if not in_window_src(float(seg["start"]), float(seg["end"]))
    ]

    shifted = []
    for seg in vo_segments:
        words = []
        for w in seg.get("words", []):
            w_out = start_out + float(w["start"])
            if w_out >= end_out:
                continue
            w_len = max(0.05, float(w["end"]) - float(w["start"]))
            w_src = output_to_source_time(w_out, m_start, keep_segments)
            words.append({**w, "start": w_src, "end": w_src + w_len})
        if not words:
            continue
        shifted.append({
            **seg,
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": " ".join(w["word"] for w in words),
            "words": words,
        })

    merged = kept + shifted
    merged.sort(key=lambda s: float(s["start"]))
    return merged


def build_audio_filter(
    window: tuple[float, float],
    duck_volume: float,
    vo_volume: float,
    fade: float = 0.4,
) -> str:
    """
    FFmpeg filter_complex for: [0:a] = clip audio, [1:a] = voice-over.
    The clip audio ramps down to ``duck_volume`` while the voice talks and
    back up after. Output label: [a_out].
    """
    start, end = window
    delay_ms = int(round(start * 1000))
    a = start - fade
    b = end + fade
    # weight = 1 inside the window, ramping 0→1 over `fade` seconds at each edge
    weight = f"clip((t-{a:.3f})/{fade:.3f},0,1)*clip(({b:.3f}-t)/{fade:.3f},0,1)"
    duck = f"1-{1 - duck_volume:.3f}*{weight}"
    return (
        f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume='{duck}':eval=frame[bed]; "
        f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume={vo_volume:.3f},adelay={delay_ms}|{delay_ms}[vo]; "
        f"[bed][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]"
    )


def mix_into_clip(
    clip_path: str,
    vo_audio_path: str,
    window: tuple[float, float],
    duck_volume: float,
    vo_volume: float,
    out_path: str,
) -> None:
    """Lay the voice-over over ``clip_path`` (video is copied, audio re-mixed)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", clip_path,
        "-i", vo_audio_path,
        "-filter_complex", build_audio_filter(window, duck_volume, vo_volume),
        "-map", "0:v:0", "-map", "[a_out]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-f", "mpegts",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg voice-over mix failed:\n{e.stderr}") from e


def probe_duration(path: str) -> float | None:
    """Audio/video duration in seconds via ffprobe, or None if it cannot be read."""
    if not os.path.exists(path):
        return None
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        return float(res.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None
