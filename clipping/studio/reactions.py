"""
clipping.studio.reactions — Cut short pre-saved reaction clips into a Short.

Put your own short reaction clips (a laugh, a shocked face, a "let's go!")
in folders named after the feeling:

    assets/reactions/laugh/   assets/reactions/shock/   assets/reactions/hype/ ...

The AI marks moments in each clip plan as ``reaction_inserts``:

    "reaction_inserts": [{"time": 52.3, "mood": "laugh"}]

``time`` is in source-video seconds (like every other time in the plan). At
that point the clip pauses, the reaction plays for about a second (with its
own sound), and the clip carries on from exactly where it stopped.

Reactions are never placed while the narrator is talking, too close to the
start or end of the clip, or too close to each other.
"""

from __future__ import annotations

import os
import random
import subprocess

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def list_moods(reactions_dir: str | None) -> dict[str, list[str]]:
    """{mood: [clip paths]} for every sub-folder that holds at least one video."""
    moods: dict[str, list[str]] = {}
    if not reactions_dir or not os.path.isdir(reactions_dir):
        return moods
    for name in sorted(os.listdir(reactions_dir)):
        folder = os.path.join(reactions_dir, name)
        if not os.path.isdir(folder):
            continue
        clips = sorted(
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(VIDEO_EXTS) and not f.startswith(".")
        )
        if clips:
            moods[name.lower()] = clips
    return moods


def source_to_output(t_src: float, m_start: float, m_end: float, keep_segments=None) -> float | None:
    """Source-video second -> second in the rendered main clip (None if cut out)."""
    t_src = float(t_src)
    if not keep_segments:
        if float(m_start) <= t_src <= float(m_end):
            return t_src - float(m_start)
        return None
    elapsed = 0.0
    for seg in keep_segments:
        s, e = float(seg["start_time"]), float(seg["end_time"])
        if s <= t_src <= e:
            return elapsed + (t_src - s)
        elapsed += e - s
    return None


def plan_inserts(
    inserts,
    moods: dict[str, list[str]],
    *,
    m_start: float,
    m_end: float,
    keep_segments=None,
    clip_duration: float,
    avoid_window: tuple[float, float] | None = None,
    max_count: int = 2,
    edge_margin: float = 1.5,
    min_gap: float = 4.0,
    seed: int = 0,
) -> list[dict]:
    """
    Turn the AI's suggestions into a safe list of {"at": clip_second, "path": file, "mood": m}.
    Unknown moods fall back to any available reaction.
    """
    if not inserts or not moods or max_count <= 0:
        return []
    rng = random.Random(seed)
    all_clips = [p for paths in moods.values() for p in paths]
    planned: list[dict] = []
    for ins in inserts:
        if not isinstance(ins, dict) or "time" not in ins:
            continue
        try:
            at = source_to_output(float(ins["time"]), m_start, m_end, keep_segments)
        except (TypeError, ValueError):
            continue
        if at is None or at < edge_margin or at > clip_duration - edge_margin:
            continue
        if avoid_window and avoid_window[0] - 0.3 <= at <= avoid_window[1] + 0.3:
            continue
        if any(abs(at - p["at"]) < min_gap for p in planned):
            continue
        mood = str(ins.get("mood", "")).lower().strip()
        pool = moods.get(mood) or all_clips
        planned.append({"at": round(at, 3), "path": rng.choice(pool), "mood": mood if mood in moods else "any"})
        if len(planned) >= max_count:
            break
    planned.sort(key=lambda p: p["at"])
    return planned


def _probe(path: str) -> tuple[float | None, bool]:
    """(duration seconds, has_audio) via ffprobe."""
    duration, has_audio = None, False
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "default=noprint_wrappers=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        for line in res.stdout.splitlines():
            if line.startswith("duration="):
                try:
                    duration = float(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif line.strip() == "codec_type=audio":
                has_audio = True
    except (OSError, subprocess.CalledProcessError):
        pass
    return duration, has_audio


def _probe_size(path: str) -> tuple[int, int] | None:
    """(width, height) of the first video stream, or None."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True,
        )
        w, h = res.stdout.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def splice(
    clip_path: str,
    plan: list[dict],
    *,
    width: int | None = None,
    height: int | None = None,
    max_seconds: float,
    volume: float,
    encode_args: list[str],
    out_path: str,
) -> list[dict]:
    """
    Write ``out_path`` = clip with each reaction cut in at its "at" second.
    Reactions are scaled and cropped to the clip's own size (read from the
    file when ``width``/``height`` are not given).
    Returns the plan with each item's real "duration" filled in.
    """
    clip_len, clip_audio = _probe(clip_path)
    if clip_len is None:
        raise RuntimeError(f"Could not read the clip length: {clip_path}")
    if not clip_audio:
        return []
    if not width or not height:
        size = _probe_size(clip_path)
        if not size:
            raise RuntimeError(f"Could not read the clip size: {clip_path}")
        width, height = size

    inputs = ["-i", clip_path]
    filters = []
    labels = []
    idx = 1
    cursor = 0.0
    piece = 0
    norm_v = f"fps=30,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p"
    norm_a = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

    done = []
    for item in plan:
        if not cursor < item["at"] < clip_len:
            continue
        r_len, r_audio = _probe(item["path"])
        if not r_len:
            continue
        dur = round(min(float(max_seconds), r_len), 3)

        # Clip piece before the reaction
        filters.append(f"[0:v]trim=start={cursor:.3f}:end={item['at']:.3f},setpts=PTS-STARTPTS,{norm_v}[v{piece}]")
        filters.append(f"[0:a]atrim=start={cursor:.3f}:end={item['at']:.3f},asetpts=PTS-STARTPTS,{norm_a}[a{piece}]")
        labels.append(f"[v{piece}][a{piece}]")
        piece += 1

        # The reaction itself
        inputs += ["-t", f"{dur:.3f}", "-i", item["path"]]
        v_in = idx
        idx += 1
        filters.append(f"[{v_in}:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS,{norm_v}[rv{v_in}]")
        if r_audio:
            filters.append(
                f"[{v_in}:a]{norm_a},volume={volume:.3f},apad,atrim=duration={dur:.3f},asetpts=PTS-STARTPTS[ra{v_in}]"
            )
        else:
            inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            silent = idx
            idx += 1
            filters.append(f"[{silent}:a]{norm_a},asetpts=PTS-STARTPTS[ra{v_in}]")
        labels.append(f"[rv{v_in}][ra{v_in}]")
        cursor = item["at"]
        done.append({**item, "duration": dur})

    if not done:
        return []

    # The rest of the clip
    filters.append(f"[0:v]trim=start={cursor:.3f},setpts=PTS-STARTPTS,{norm_v}[v{piece}]")
    filters.append(f"[0:a]atrim=start={cursor:.3f},asetpts=PTS-STARTPTS,{norm_a}[a{piece}]")
    labels.append(f"[v{piece}][a{piece}]")

    n = len(labels)
    filters.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[v_out][a_out]")
    cmd = (
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        + inputs
        + ["-filter_complex", "; ".join(filters), "-map", "[v_out]", "-map", "[a_out]"]
        + list(encode_args)
        + [out_path]
    )
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg reaction insert failed:\n{e.stderr}") from e
    return done


def shift_caption_lines(lines: list[dict], done: list[dict]) -> list[dict]:
    """Push caption lines that come after each reaction later by its length."""
    if not lines or not done:
        return lines
    shifted = []
    for line in lines:
        extra = sum(d["duration"] for d in done if d["at"] <= line["start"])
        shifted.append({**line, "start": round(line["start"] + extra, 3), "end": round(line["end"] + extra, 3)})
    return shifted
