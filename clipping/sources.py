"""
clipping.sources — The source video(s) behind a run.

* A downloaded file is only reused for the same link(s) and download quality.
* Several links can be joined into one long video, so one story can use
  moments from all of them ("V2 11:40" = 11:40 in the second link).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess

_YT_ID = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def link_id(url: str) -> str:
    """The same video gives the same id, however its link is written."""
    url = str(url).strip()
    found = _YT_ID.search(url)
    return f"youtube:{found.group(1)}" if found and "youtu" in url else url


def _stem(video_path: str) -> str:
    return os.path.splitext(video_path)[0]


def _read_key(video_path: str):
    try:
        with open(_stem(video_path) + "_key.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def claim(video_path: str, links: list[str], height) -> bool:
    """
    Make ``video_path`` belong to these links at this quality, *before* downloading.

    Anything left there by other links (video, part files, subtitles) is deleted,
    so the downloader can never silently reuse it. Returns True if it was kept.
    """
    key = {"links": [link_id(u) for u in links], "height": str(height)}
    if _read_key(video_path) == key:
        return True
    stem = glob.escape(_stem(video_path))
    old = [p for pattern in (stem + ".*", stem + "_*") for p in glob.glob(pattern) if os.path.isfile(p)]
    for path in old:
        os.remove(path)
    if old:
        print("      🗑️ Removed the files saved from a different link or quality.", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(video_path)), exist_ok=True)
    with open(_stem(video_path) + "_key.json", "w", encoding="utf-8") as f:
        json.dump(key, f)
    return False


def part_path(outputs_dir: str, url: str) -> str:
    """Where one link of a joined run is downloaded."""
    name = hashlib.sha1(link_id(url).encode()).hexdigest()[:10]
    return os.path.join(outputs_dir, "sources", name, "video.mp4")


# ---------------------------------------------------------------- joining
def probe(path: str) -> dict:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels:format=duration",
         "-of", "json", path],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(res.stdout)
    info = {"duration": float(data["format"]["duration"])}
    for stream in data["streams"]:
        kind = stream.pop("codec_type", None)
        if kind in ("video", "audio") and kind not in info:
            info[kind] = stream
    if "video" not in info or "audio" not in info:
        raise RuntimeError(f"{path} needs both picture and sound to be joined.")
    return info


def join_videos(paths: list[str], out_path: str) -> list[float]:
    """
    Glue ``paths`` into ``out_path``. Returns each part's start second.

    Matching videos are copied as they are (fast). Different ones are first
    re-encoded to the first video's size, which is slow on a CPU.
    """
    infos = [probe(p) for p in paths]
    pieces, temps = list(paths), []
    if any((i["video"], i["audio"]) != (infos[0]["video"], infos[0]["audio"]) for i in infos):
        width, height = infos[0]["video"]["width"], infos[0]["video"]["height"]
        print(f"      ⚠️ The videos differ in size or format: converting them all to {width}x{height} "
              "first. This is slow without a graphics card.", flush=True)
        pieces = []
        for n, path in enumerate(paths, 1):
            piece = f"{_stem(out_path)}_part{n}.mp4"
            print(f"      🔁 Converting video {n} of {len(paths)}...", flush=True)
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                 "-map", "0:v:0", "-map", "0:a:0",
                 "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-c:a", "aac", "-ar", "48000", "-ac", "2", piece],
                check=True,
            )
            pieces.append(piece)
            temps.append(piece)

    list_file = _stem(out_path) + "_join.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for piece in pieces:
            escaped = os.path.abspath(piece).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    print(f"      🔗 Joining {len(pieces)} videos...", flush=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", out_path],
        check=True,
    )
    offsets, total = [], 0.0
    for piece in pieces:
        offsets.append(round(total, 3))
        total += probe(piece)["duration"]
    for path in temps + [list_file]:
        os.remove(path)
    return offsets


# ---------------------------------------------------------------- times
def shift_transcript(text: str, segments: list[dict], offset: float) -> tuple[str, list[dict]]:
    """Move one part's transcript to its place in the joined video."""
    text = re.sub(
        r"^\[(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)\]",
        lambda m: f"[{float(m[1]) + offset:.1f} - {float(m[2]) + offset:.1f}]",
        text, flags=re.M,
    )
    moved = []
    for seg in segments:
        new = {**seg, "start": seg["start"] + offset, "end": seg["end"] + offset}
        if "words" in seg:
            new["words"] = [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in seg["words"]]
        moved.append(new)
    return text, moved


_MARK_OR_TIME = re.compile(r"\b[Vv](\d{1,2})\b[ ,:]*(?=\d{1,2}:\d{2})|\b[Vv](\d{1,2})\b|\b(\d{1,3}(?::\d{2}){1,2})\b")


def to_joined_times(text: str, offsets: list[float]) -> str:
    """
    Rewrite "V2 11:40" (and the times after it, until the next V mark) as the
    matching time in the joined video. Each line is one angle and starts at V1.
    """
    out = []
    for line in str(text or "").split("\n"):
        current = [0]

        def swap(m):
            number = m[1] or m[2]
            if number is not None:
                index = int(number) - 1
                if not 0 <= index < len(offsets):
                    raise ValueError(f"The story mentions V{number}, but there are only {len(offsets)} links.")
                current[0] = index
                return "" if m[1] else f"video {number}"
            seconds = 0
            for piece in m[3].split(":"):
                seconds = seconds * 60 + int(piece)
            seconds += int(offsets[current[0]])
            return f"{seconds // 60:02d}:{seconds % 60:02d}"

        out.append(_MARK_OR_TIME.sub(swap, line))
    return "\n".join(out)


# ---------------------------------------------------------------- storyline cuts
def selection_rules(cfg, min_seconds: int, max_seconds: int) -> str:
    """Extra clip-picking rules when one clip may use moments from anywhere."""
    if not getattr(cfg, "storyline_cuts", False):
        return ""
    parts = getattr(cfg, "source_parts", None) or []
    layout = ""
    if len(parts) > 1:
        layout = "\nThe video is several videos joined together:\n" + "\n".join(
            f"- Video {n}: {p['start']:.1f}s to {p['end']:.1f}s{' (' + p['title'] + ')' if p.get('title') else ''}"
            for n, p in enumerate(parts, 1)
        ) + "\n"
    wanted = "\n".join(x for x in (getattr(cfg, "story_brief", ""), getattr(cfg, "story_angles", "")) if x).strip()
    if wanted:
        wanted = ("\nTHE CHANNEL OWNER'S STORYLINE. Pick the footage it describes; where it gives times "
                  "(mm:ss), use those moments. One line per clip: the clip with rank 1 follows the first line, "
                  "rank 2 the second, and so on, whatever their viral_score:\n" + wanted + "\n")
    return f"""

STORYLINE CUTS (these rules replace any earlier rule about segment order or one continuous clip):
- A clip may be built from moments anywhere in the video, even far apart or in different videos.
- "keep_segments" lists the moments in the order the viewer sees them (story order). They must not overlap.
- start_time is the earliest second used by any segment and end_time the latest.
- The {min_seconds}-{max_seconds} second length rule applies to the TOTAL of the segments.
- Keep each segment at least 2 seconds long. The hook must be inside one of the segments.
{layout}{wanted}"""


def fit_bounds(clips) -> None:
    """start_time/end_time must cover every segment, whatever the AI wrote."""
    for clip in clips:
        segments = clip.get("keep_segments") if isinstance(clip, dict) else None
        if not segments:
            continue
        try:
            clip["start_time"] = min(float(s["start_time"]) for s in segments)
            clip["end_time"] = max(float(s["end_time"]) for s in segments)
        except (KeyError, TypeError, ValueError):
            pass  # validation reports the broken segment
