"""
clipping.editor.script — The voice-over script: lines timed to the cut.

A script line is a plain dict kept in ``project.script_lines``:

    id          "ln_xxxxxxxx"
    segment_id  the moment (cut-list item) the line belongs to
    source_id   that moment's video (so a split moment can hand the line on)
    src_start   where in the video the line starts (source seconds)
    offset      seconds from the start of its moment, in the finished video (worked out)
    text        what the narrator says
    character   who says it ("" = the narrator)
    voice       the edge-tts voice, "" = the default voice
    cue         free-text hint for the sound phase, e.g. "sfx:whoosh" (just stored)
    audio       the voice file, relative to the edit folder ("" = not made yet)
    duration    seconds of audio (0 = not made yet, the time is guessed from the words)
    words       edge-tts word timings [{"word", "start", "end"}] relative to the line
    start, end  where it sits in the finished video (worked out by ``refresh``)
    visible     False when its moment was deleted (the line is kept, so Undo brings it back)

Lines are anchored to the footage like captions are (see captions.py): reorder,
trim, split, speed up or delete a moment and ``refresh`` puts every line where
its footage now is. Nothing here needs Tk, ffmpeg or the network.
"""

from __future__ import annotations

import uuid

from clipping.editor import timeline

WORDS_PER_SECOND = 2.2   # how fast the free voices really speak (measured 2.3 on a 54 s cut); sets how much fits in a moment
MIN_DURATION = 0.6       # the shortest time a line is ever given when guessing
OVERLAP_TOLERANCE = 0.05


def new_id() -> str:
    return "ln_" + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- word budget
def count_words(text: str) -> int:
    return len((text or "").split())


def line_budget(seconds: float) -> int:
    """How many words fit in ``seconds`` of voice (never less than 1)."""
    return max(1, int(max(0.0, seconds) * WORDS_PER_SECOND))


def estimate_duration(text: str) -> float:
    return max(MIN_DURATION, count_words(text) / WORDS_PER_SECOND)


def line_duration(line: dict) -> float:
    """The real audio length if the voice is made, else a guess from the words."""
    d = float(line.get("duration") or 0.0)
    return d if d > 0 else estimate_duration(line.get("text", ""))


def trim_to_budget(text: str, seconds: float) -> str:
    """Cut ``text`` to the words that fit ``seconds``, ending on a whole word."""
    words = (text or "").split()
    fit = line_budget(seconds)
    if len(words) <= fit:
        return " ".join(words)
    out = " ".join(words[:fit]).rstrip(",;:- ")
    return out if out[-1:] in ".!?…" else out + "…"


# ---------------------------------------------------------------- finding things
def find(project, line_id: str) -> dict:
    for ln in project.script_lines:
        if ln["id"] == line_id:
            return ln
    raise KeyError(line_id)


def visible(project) -> list[dict]:
    return [ln for ln in project.script_lines if ln.get("visible", True)]


def lines_of_segment(project, segment_id: str) -> list[dict]:
    return [ln for ln in visible(project) if ln.get("segment_id") == segment_id]


def _segment_for(project, line: dict):
    """The moment a line plays in: its own, or (after a split) the other half
    of the same video that contains its footage. None if its moment is gone."""
    own = next(((i, s) for i, s in enumerate(project.segments) if s.id == line.get("segment_id")), None)
    if own is None:
        return None
    t = line["src_start"]
    if own[1].source_in - 1e-6 <= t <= own[1].source_out + 1e-6:
        return own
    for i, s in enumerate(project.segments):
        if s.source_id == own[1].source_id and s.source_in - 1e-6 <= t <= s.source_out + 1e-6:
            return i, s
    return own  # trimmed past it: it simply starts with its moment


# ---------------------------------------------------------------- creating lines
def make_line(project, segment_id: str, offset: float, text: str, character: str = "",
              cue: str = "", voice: str = "") -> dict:
    """A new line ``offset`` seconds into moment ``segment_id`` (finished-video seconds)."""
    idx = project.segment_index(segment_id)
    seg = project.segments[idx]
    speed = seg.speed or 1.0
    offset = min(max(0.0, float(offset)), max(0.0, seg.length - 0.1))
    return {
        "id": new_id(), "segment_id": seg.id, "source_id": seg.source_id,
        "src_start": round(seg.source_in + offset * speed, 3),
        "offset": round(offset, 3),
        "text": " ".join((text or "").split()), "character": character or "", "voice": voice or "",
        "cue": cue or "", "audio": "", "duration": 0.0, "words": [],
        "start": 0.0, "end": 0.0, "visible": True,
    }


def refresh(project) -> None:
    """Recompute where every line sits in the finished video."""
    pieces = timeline.layout(project.segments)
    for ln in project.script_lines:
        found = _segment_for(project, ln)
        if found is None:
            ln["visible"] = False
            continue
        index, seg = found
        p = pieces[index]
        ln["segment_id"], ln["source_id"] = seg.id, seg.source_id
        offset = max(0.0, (ln["src_start"] - seg.source_in) / (seg.speed or 1.0))
        offset = min(offset, max(0.0, seg.length - 0.1))
        ln["offset"] = round(offset, 3)
        ln["start"] = round(p.edit_start + offset, 3)
        ln["end"] = round(ln["start"] + line_duration(ln), 3)
        ln["visible"] = True
    project.script_lines.sort(key=lambda l: (not l.get("visible", True), l.get("start", 0.0)))


def from_claude(project, items: list[dict], default_voice_for=None) -> list[dict]:
    """Turn Claude's ``[{segment_id, offset, text, character, cue}]`` into lines.

    Each line is cut to the words that fit before the next line (or the end of
    its moment) so they never overlap. Unknown moments are skipped. Returns the lines."""
    by_seg: dict[str, list[dict]] = {}
    for it in items or []:
        if any(s.id == it.get("segment_id") for s in project.segments) and (it.get("text") or "").strip():
            by_seg.setdefault(it["segment_id"], []).append(it)
    made = []
    for seg in project.segments:
        group = sorted(by_seg.get(seg.id, []), key=lambda it: float(it.get("offset") or 0.0))
        for k, it in enumerate(group):
            start = min(max(0.0, float(it.get("offset") or 0.0)), max(0.0, seg.length - 0.5))
            if made and made[-1]["segment_id"] == seg.id:
                start = max(start, made[-1]["offset"] + line_duration(made[-1]) + 0.2)
            nxt = float(group[k + 1].get("offset") or 0.0) if k + 1 < len(group) else seg.length
            room = max(0.5, max(nxt, start + 0.5) - start - 0.2)
            ln = make_line(project, seg.id, start, trim_to_budget(it["text"], room),
                           character=it.get("character") or "", cue=it.get("cue") or "")
            if default_voice_for:
                ln["voice"] = default_voice_for(ln["character"]) or ""
            made.append(ln)
    return made


# ---------------------------------------------------------------- editing
def set_text(line: dict, text: str) -> None:
    """New words invalidate the voice and its timings (they are made again)."""
    text = " ".join((text or "").split())
    if text != line.get("text"):
        line["text"] = text
        line["audio"], line["duration"], line["words"] = "", 0.0, []


def nudge(project, line_id: str, delta: float) -> None:
    """Move a line ``delta`` seconds earlier or later inside its moment."""
    ln = find(project, line_id)
    idx = project.segment_index(ln["segment_id"])
    seg = project.segments[idx]
    offset = min(max(0.0, ln["offset"] + delta), max(0.0, seg.length - 0.1))
    ln["src_start"] = round(seg.source_in + offset * (seg.speed or 1.0), 3)
    refresh(project)


def delete(project, line_id: str) -> None:
    project.script_lines = [l for l in project.script_lines if l["id"] != line_id]


def clear(project) -> None:
    project.script_lines = []


def replace_all(project, lines: list[dict]) -> None:
    project.script_lines = list(lines)
    refresh(project)


# ---------------------------------------------------------------- checking
def warnings(project) -> list[dict]:
    """Problems to show in red: ``[{"line_id", "kind", "text"}]``.
    kind is "too_long" (runs past its moment), "overlap" or "too_many_words"."""
    refresh(project)
    out = []
    pieces = timeline.layout(project.segments)
    ids = {s.id: i for i, s in enumerate(project.segments)}
    lines = visible(project)
    for k, ln in enumerate(lines):
        end_of_moment = pieces[ids[ln["segment_id"]]].edit_end
        over = ln["end"] - end_of_moment
        if over > OVERLAP_TOLERANCE:
            out.append({"line_id": ln["id"], "kind": "too_long",
                        "text": f"Runs {over:.1f} s past its moment."})
        if k + 1 < len(lines) and ln["end"] - lines[k + 1]["start"] > OVERLAP_TOLERANCE:
            out.append({"line_id": ln["id"], "kind": "overlap",
                        "text": "Overlaps the next line."})
    return out


def warning_for(project, line_id: str) -> str:
    return " ".join(w["text"] for w in warnings(project) if w["line_id"] == line_id)
