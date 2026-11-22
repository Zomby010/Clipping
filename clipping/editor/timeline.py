"""
clipping.editor.timeline — Pure time maths for the cut list.

Nothing here touches files or ffmpeg, so it is fast and easy to test.

Time words people type:
    1:02        1 minute 2 seconds
    1:02.5      1 minute 2.5 seconds
    1:02:03     1 hour 2 minutes 3 seconds
    1.02        1 minute 2 seconds (the "min 1.02" style, two digits after the dot)
    62  / 62s   62 seconds
    62.5s       62.5 seconds (with the "s", a dot is a decimal point)

Ranges are "start-end" (also "start to end" or "start – end"), separated by
commas, semicolons or new lines:  "1:02-1:10, 2:38-2:49, 4:06-4:55".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Lengths the owner posts most, in seconds.
TARGET_LENGTHS = (15, 30, 45, 60)

# Longest video each platform accepts as a Short / Reel (seconds).
# Platforms change these; keep them in one place.
PLATFORM_LIMITS = {
    "YouTube Shorts": 180,
    "TikTok": 600,
    "Instagram Reels": 180,
    "Facebook Reels": 90,
}


class TimeParseError(ValueError):
    """A time or range the user typed could not be understood."""


_RANGE_SPLIT = re.compile(r"[,;\n]+")
_RANGE_SEP = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)


def parse_time(text: str) -> float:
    """Turn a typed time into seconds. Raises TimeParseError on nonsense."""
    raw = (text or "").strip().lower()
    if not raw:
        raise TimeParseError("Empty time.")
    if raw.endswith("s") and ":" not in raw:
        number = raw[:-1].strip()
        try:
            value = float(number)
        except ValueError:
            raise TimeParseError(f"'{text}' is not a time.") from None
        if value < 0:
            raise TimeParseError(f"'{text}' is negative.")
        return value
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3 or any(p == "" for p in parts):
            raise TimeParseError(f"'{text}' is not a time like 1:02 or 1:02:03.")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise TimeParseError(f"'{text}' is not a time like 1:02 or 1:02:03.") from None
        if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
            raise TimeParseError(f"'{text}': minutes and seconds must be under 60.")
        seconds = 0.0
        for n in nums:
            seconds = seconds * 60 + n
        return seconds
    m = re.fullmatch(r"(\d+)\.(\d{2})", raw)
    if m:  # "1.02" = 1 min 2 s, the way people write video times in chat
        minutes, secs = int(m.group(1)), int(m.group(2))
        if secs >= 60:
            raise TimeParseError(f"'{text}': seconds after the dot must be under 60.")
        return minutes * 60.0 + secs
    if re.fullmatch(r"\d+", raw):
        return float(raw)
    raise TimeParseError(f"'{text}' is not a time like 1:02, 1.02 or 62s.")


def parse_ranges(text: str) -> list[tuple[float, float]]:
    """Parse "1:02-1:10, 2:38-2:49" into [(62, 70), (158, 169)]."""
    out: list[tuple[float, float]] = []
    for chunk in _RANGE_SPLIT.split(text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        pieces = _RANGE_SEP.split(chunk)
        if len(pieces) != 2:
            raise TimeParseError(f"'{chunk}' should look like 1:02-1:10.")
        start, end = parse_time(pieces[0]), parse_time(pieces[1])
        if end <= start:
            raise TimeParseError(f"'{chunk}': the end must be after the start.")
        out.append((start, end))
    if not out:
        raise TimeParseError("Type at least one range, like 1:02-1:10.")
    return out


def format_time(seconds: float, tenths: bool = False) -> str:
    """62 -> '1:02', 3723 -> '1:02:03'. With tenths: 62.5 -> '1:02.5'."""
    seconds = max(0.0, float(seconds))
    if tenths:
        whole = int(seconds)
        tenth = int(round((seconds - whole) * 10))
        if tenth == 10:
            whole, tenth = whole + 1, 0
    else:
        whole, tenth = int(round(seconds)), 0
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"{text}.{tenth}" if tenths else text


@dataclass(frozen=True)
class Piece:
    """One segment as it sits on the edit's timeline."""
    index: int          # position in the cut list
    edit_start: float   # where it starts in the finished video
    edit_end: float
    source_in: float    # where it comes from in its source
    source_out: float
    speed: float


def segment_length(source_in: float, source_out: float, speed: float = 1.0) -> float:
    """How long a segment lasts in the finished video."""
    speed = speed if speed and speed > 0 else 1.0
    return max(0.0, (source_out - source_in) / speed)


def layout(segments) -> list[Piece]:
    """Place segments one after another. ``segments`` items need
    ``source_in``, ``source_out`` and ``speed`` attributes."""
    pieces, t = [], 0.0
    for i, seg in enumerate(segments):
        length = segment_length(seg.source_in, seg.source_out, seg.speed)
        pieces.append(Piece(i, t, t + length, seg.source_in, seg.source_out, seg.speed or 1.0))
        t += length
    return pieces


def total_length(segments) -> float:
    return sum(segment_length(s.source_in, s.source_out, s.speed) for s in segments)


def edit_to_source(segments, edit_time: float) -> tuple[int, float] | None:
    """Which segment plays at ``edit_time`` and the matching source time."""
    for p in layout(segments):
        if p.edit_start <= edit_time < p.edit_end:
            return p.index, p.source_in + (edit_time - p.edit_start) * p.speed
    return None


def source_to_edit(segments, index: int, source_time: float) -> float:
    """Edit time of ``source_time`` inside segment ``index`` (clamped to it)."""
    p = layout(segments)[index]
    clamped = min(max(source_time, p.source_in), p.source_out)
    return p.edit_start + (clamped - p.source_in) / p.speed


def length_report(seconds: float) -> dict:
    """What the total length means for posting.

    Returns {"nearest_target": 60, "over_target": True, "platforms": {name: fits}}.
    ``nearest_target`` is the smallest of 15/30/45/60 the video fits in, or the
    largest one when it is longer than all of them.
    """
    fits = [t for t in TARGET_LENGTHS if seconds <= t + 0.05]
    nearest = fits[0] if fits else TARGET_LENGTHS[-1]
    return {
        "nearest_target": nearest,
        "over_target": not fits,
        "platforms": {name: seconds <= limit + 0.05 for name, limit in PLATFORM_LIMITS.items()},
    }


def describe_length(seconds: float) -> str:
    """One plain-English line for the cut list footer."""
    if seconds <= 0:
        return "No moments yet. Add some to the cut list."
    rep = length_report(seconds)
    total = format_time(seconds, tenths=True)
    if rep["over_target"]:
        line = f"Total {total}: longer than 60 s."
    else:
        line = f"Total {total}: fits a {rep['nearest_target']} s Short."
    too_long = [name for name, ok in rep["platforms"].items() if not ok]
    if too_long:
        line += " Too long for " + ", ".join(too_long) + "."
    return line


def validate(segments, durations: dict[str, float]) -> list[str]:
    """Plain-English problems with the cut list. ``durations`` maps source id
    to its length in seconds (missing = unknown, not checked)."""
    problems = []
    for i, seg in enumerate(segments, start=1):
        if seg.source_out <= seg.source_in:
            problems.append(f"Moment {i}: the end is not after the start.")
        if seg.source_in < 0:
            problems.append(f"Moment {i}: starts before the video does.")
        dur = durations.get(seg.source_id)
        if dur and seg.source_out > dur + 0.05:
            problems.append(f"Moment {i}: ends at {format_time(seg.source_out)}, "
                            f"but the video is only {format_time(dur)} long.")
        if not (0.25 <= (seg.speed or 1.0) <= 4.0):
            problems.append(f"Moment {i}: speed must be between 0.25x and 4x.")
    return problems
