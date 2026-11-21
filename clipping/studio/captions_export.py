"""
clipping.studio.captions_export — Caption lines for a finished clip, as plain data.

The captions are burned into the video, but a separate .srt file is handy:
YouTube accepts it as a subtitle track, and it is easy to read and fix.
This module turns transcript segments (in source-video time) into lines in
main-clip time (0 = first frame of the main clip), following smart-trim
segments when the clip was cut into pieces.
"""

from __future__ import annotations


def _seg_text(seg: dict) -> str:
    text = (seg.get("text") or "").strip()
    if not text:
        text = " ".join(w.get("word", "").strip() for w in seg.get("words", []))
    return " ".join(text.split())


def caption_lines(data_segmen, m_start: float, m_end: float, keep_segments=None) -> list[dict]:
    """
    Return [{"start": s, "end": e, "text": "..."}] in main-clip time, sorted.
    Lines that fall outside the clip (or inside a trimmed-out gap) are dropped.
    """
    pieces = (
        [(float(k["start_time"]), float(k["end_time"])) for k in keep_segments]
        if keep_segments
        else [(float(m_start), float(m_end))]
    )
    lines = []
    for seg in data_segmen or []:
        text = _seg_text(seg)
        if not text:
            continue
        s, e = float(seg["start"]), float(seg["end"])
        elapsed = 0.0
        for p_start, p_end in pieces:
            lo, hi = max(s, p_start), min(e, p_end)
            if hi > lo:
                lines.append({
                    "start": round(elapsed + (lo - p_start), 3),
                    "end": round(elapsed + (hi - p_start), 3),
                    "text": text,
                })
            elapsed += p_end - p_start
    lines.sort(key=lambda x: x["start"])
    return lines


def _ts(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(lines: list[dict], offset: float = 0.0) -> str:
    """SubRip text. ``offset`` shifts every line (e.g. the length of the hook in front)."""
    out = []
    for i, line in enumerate(lines, 1):
        out.append(str(i))
        out.append(f"{_ts(line['start'] + offset)} --> {_ts(line['end'] + offset)}")
        out.append(line["text"])
        out.append("")
    return "\n".join(out)
