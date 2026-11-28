"""
clipping.editor.captions — Captions that follow the cut.

Each caption line belongs to one moment (``segment_id``) and keeps its words in
SOURCE time (``src_start`` / ``src_end``). Edit times (``start`` / ``end``, the
time in the finished video) are worked out again by ``refresh()`` after every
change, so captions follow when moments are reordered, sped up, nudged, split
or deleted. A caption whose moment is gone is hidden (undo brings it back).

A caption in project.captions looks like:

    {"id": "cap_1a2b3c4d", "segment_id": "seg_...", "text": "no way bro",
     "src_start": 62.1, "src_end": 63.4,
     "words": [{"word": "no", "src_start": 62.1, "src_end": 62.4,
                "start": 0.1, "end": 0.4}, ...],
     "start": 0.1, "end": 1.4, "visible": true}

Speech recognition (``transcribe_segments``) runs faster-whisper on just the
moments in the cut list and caches the words per video and time range, so the
same audio is never listened to twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid

from clipping.editor import script as script_lines
from clipping.editor import timeline

PAUSE_BREAK = 0.6       # a gap this long between words starts a new line
MAX_LINE_SECONDS = 3.0  # a line never stays on screen longer than this
_SENTENCE_END = re.compile(r"[.!?…]$")


def _new_id() -> str:
    return f"cap_{uuid.uuid4().hex[:8]}"


def _clean(word: str) -> str:
    return " ".join((word or "").split())


# ---------------------------------------------------------------- grouping
def group_words(words: list[dict], max_words: int = 4, max_chars: int = 24,
                pause: float = PAUSE_BREAK) -> list[list[dict]]:
    """Split a list of words (each with src_start/src_end) into short lines:
    at most ``max_words`` words and about ``max_chars`` letters, a new line at
    a pause, at the end of a sentence, or when a line gets too long in time."""
    lines: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if not _clean(w.get("word")):
            continue
        if current:
            gap = w["src_start"] - current[-1]["src_end"]
            chars = sum(len(x["word"]) + 1 for x in current) + len(w["word"])
            too_long = w["src_end"] - current[0]["src_start"] > MAX_LINE_SECONDS
            ends_sentence = bool(_SENTENCE_END.search(current[-1]["word"]))
            if (len(current) >= max_words or chars > max_chars or gap >= pause
                    or too_long or ends_sentence):
                lines.append(current)
                current = []
        current.append({"word": _clean(w["word"]), "src_start": float(w["src_start"]),
                        "src_end": float(w["src_end"])})
    if current:
        lines.append(current)
    return lines


def make_caption(words: list[dict], segment_id: str) -> dict:
    return {
        "id": _new_id(),
        "segment_id": segment_id,
        "text": " ".join(w["word"] for w in words),
        "src_start": words[0]["src_start"],
        "src_end": words[-1]["src_end"],
        "words": [dict(w) for w in words],
        "start": 0.0,
        "end": 0.0,
        "visible": True,
    }


def captions_from_words(project, segment, words: list[dict], max_words: int = 4) -> list[dict]:
    """Captions for one moment from words in source time (words outside the
    moment are dropped)."""
    inside = [w for w in words
              if w["src_end"] > segment.source_in and w["src_start"] < segment.source_out]
    clipped = []
    for w in inside:
        clipped.append({"word": w["word"], "src_start": max(w["src_start"], segment.source_in),
                        "src_end": min(w["src_end"], segment.source_out)})
    return [make_caption(line, segment.id) for line in group_words(clipped, max_words)]


# ---------------------------------------------------------------- edit times
def _segment_for(project, cap: dict):
    """The moment a caption plays in: its own one if it still contains the
    caption, else another moment of the same video that does (after a split)."""
    by_id = {s.id: (i, s) for i, s in enumerate(project.segments)}
    own = by_id.get(cap.get("segment_id"))
    if own is None:
        return None  # its moment was deleted: hide it (undo brings both back)
    mid = (cap["src_start"] + cap["src_end"]) / 2
    if own[1].source_in <= mid <= own[1].source_out:
        return own
    # The moment was split and this line is now in the other half.
    for i, s in enumerate(project.segments):
        if s.source_id == own[1].source_id and s.source_in <= mid <= s.source_out:
            return i, s
    return own  # nudged: words outside the moment are simply not shown


def refresh(project) -> None:
    """Recompute every caption's edit times from the current cut list."""
    script_lines.refresh(project)  # phase 3: script captions follow their line
    by_line = {l["id"]: l for l in project.script_lines}
    for cap in project.captions:
        if cap.get("line_id"):           # --- phase 3: a caption made from the script ---
            ln = by_line.get(cap["line_id"])
            if ln is None or not ln.get("visible", True):
                cap["visible"] = False
                continue
            cap["segment_id"], cap["source_id"] = ln["segment_id"], ln["source_id"]
            for w in cap["words"]:       # src_* are seconds into the voice line here
                w["start"] = round(ln["start"] + w["src_start"], 3)
                w["end"] = round(ln["start"] + w["src_end"], 3)
            cap["visible"] = True
            cap["start"], cap["end"] = cap["words"][0]["start"], cap["words"][-1]["end"]
            continue
        found = _segment_for(project, cap)
        if found is None:
            cap["visible"] = False
            continue
        index, seg = found
        cap["segment_id"] = seg.id
        cap["source_id"] = seg.source_id
        words = []
        for w in cap["words"]:
            lo, hi = max(w["src_start"], seg.source_in), min(w["src_end"], seg.source_out)
            if hi <= lo:
                w["start"] = w["end"] = None
                continue
            w["start"] = round(timeline.source_to_edit(project.segments, index, lo), 3)
            w["end"] = round(timeline.source_to_edit(project.segments, index, hi), 3)
            words.append(w)
        if not words:
            cap["visible"] = False
            continue
        cap["visible"] = True
        cap["start"] = words[0]["start"]
        cap["end"] = words[-1]["end"]
    project.captions.sort(key=lambda c: (not c.get("visible", True), c.get("start", 0)))


def visible(project) -> list[dict]:
    return [c for c in project.captions if c.get("visible", True)]


def srt_lines(project) -> list[dict]:
    refresh(project)
    return [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in visible(project)]


# ---------------------------------------------------------------- editing
def find(project, cap_id: str) -> dict:
    for c in project.captions:
        if c["id"] == cap_id:
            return c
    raise KeyError(cap_id)


def set_text(cap: dict, text: str) -> None:
    """Change a line's words. Same number of words: each keeps its timing.
    Different number: the line's time is shared out evenly."""
    new = text.split()
    if not new:
        raise ValueError("A caption needs at least one word. Use Delete to remove it.")
    old = cap["words"]
    if len(new) == len(old):
        for w, word in zip(old, new):
            w["word"] = word
    else:
        a, b = cap["src_start"], cap["src_end"]
        step = (b - a) / len(new)
        cap["words"] = [{"word": word, "src_start": round(a + i * step, 3),
                         "src_end": round(a + (i + 1) * step, 3)} for i, word in enumerate(new)]
    cap["text"] = " ".join(w["word"] for w in cap["words"])


def word_index_at(cap: dict, char_index: int) -> int:
    """Which word a text cursor sits before (for "split at cursor")."""
    pos = 0
    for i, w in enumerate(cap["words"]):
        if char_index <= pos:
            return i
        pos += len(w["word"]) + 1
    return len(cap["words"])


def split(project, cap_id: str, word_index: int) -> tuple[dict, dict]:
    cap = find(project, cap_id)
    if not (0 < word_index < len(cap["words"])):
        raise ValueError("Put the cursor between two words to split the line there.")
    second = make_caption(cap["words"][word_index:], cap["segment_id"])
    second["source_id"] = cap.get("source_id")
    cap["words"] = cap["words"][:word_index]
    cap["text"] = " ".join(w["word"] for w in cap["words"])
    cap["src_end"] = cap["words"][-1]["src_end"]
    project.captions.insert(project.captions.index(cap) + 1, second)
    return cap, second


def join_next(project, cap_id: str) -> dict:
    refresh(project)
    shown = visible(project)
    cap = find(project, cap_id)
    i = shown.index(cap)
    if i + 1 >= len(shown):
        raise ValueError("This is the last line.")
    nxt = shown[i + 1]
    if nxt.get("segment_id") != cap.get("segment_id"):
        raise ValueError("The next line is in a different moment, so they can't be joined.")
    cap["words"] = cap["words"] + nxt["words"]
    cap["text"] = " ".join(w["word"] for w in cap["words"])
    cap["src_start"] = min(cap["src_start"], nxt["src_start"])
    cap["src_end"] = max(cap["src_end"], nxt["src_end"])
    project.captions.remove(nxt)
    return cap


def delete(project, cap_id: str) -> None:
    project.captions.remove(find(project, cap_id))


def nudge(project, cap_id: str, edge: str, delta: float) -> None:
    """Move a line's start or end by ``delta`` seconds of the finished video."""
    cap = find(project, cap_id)
    found = None if cap.get("line_id") else _segment_for(project, cap)  # script captions run in voice time
    speed = found[1].speed if found else 1.0
    d = delta * (speed or 1.0)
    words = cap["words"]
    if edge == "in":
        first = words[0]
        first["src_start"] = round(min(max(0.0, first["src_start"] + d), first["src_end"] - 0.05), 3)
        cap["src_start"] = first["src_start"]
    elif edge == "out":
        last = words[-1]
        last["src_end"] = round(max(last["src_end"] + d, last["src_start"] + 0.05), 3)
        cap["src_end"] = last["src_end"]
    else:
        raise ValueError("edge must be 'in' or 'out'")


# ---------------------------------------------------------------- speech
def _cache_key(path: str, start: float, end: float, model: str) -> str:
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = 0
    raw = json.dumps([os.path.abspath(path), stamp, round(start, 2), round(end, 2), model])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _extract_audio(path: str, start: float, end: float, out_wav: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
           "-t", f"{max(0.05, end - start):.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000", out_wav]
    subprocess.run(cmd, check=True, capture_output=True)


class WhisperTranscriber:
    """faster-whisper, loaded once, CPU-friendly settings. Reuses
    ``clipping.engine.transcribe_video`` so it behaves like Clipping Studio."""

    def __init__(self, model_size: str = "small", device: str = "cpu", compute_type: str = "int8"):
        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        self._model = None

    @property
    def name(self) -> str:
        return f"whisper-{self.model_size}"

    def __call__(self, wav_path: str) -> list[dict]:
        from clipping import engine
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        _text, chunks = engine.transcribe_video(
            wav_path, model_size=self.model_size, device=self.device,
            compute_type=self.compute_type, whisper_model_instance=self._model)
        return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
                for chunk in chunks for w in chunk.get("words", [])]


def transcribe_range(path: str, start: float, end: float, transcriber, cache_dir: str) -> list[dict]:
    """Words spoken in ``path`` between ``start`` and ``end`` (source time).
    ``transcriber(wav_path)`` returns [{"word","start","end"}] in wav time."""
    os.makedirs(cache_dir, exist_ok=True)
    name = getattr(transcriber, "name", "custom")
    cache = os.path.join(cache_dir, f"words-{_cache_key(path, start, end, name)}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    with tempfile.TemporaryDirectory(prefix="clipedit_audio_") as tmp:
        wav = os.path.join(tmp, "audio.wav")
        _extract_audio(path, start, end, wav)
        raw = transcriber(wav)
    words = [{"word": _clean(w["word"]), "src_start": round(start + float(w["start"]), 3),
              "src_end": round(start + float(w["end"]), 3)} for w in raw if _clean(w.get("word"))]
    tmp_cache = cache + ".tmp"
    with open(tmp_cache, "w", encoding="utf-8") as f:
        json.dump(words, f)
    os.replace(tmp_cache, cache)
    return words


def uncaptioned(project) -> list:
    """Moments with sound that have never had captions made."""
    have = {c.get("segment_id") for c in project.captions}
    out = []
    for s in project.segments:
        src = project.source(s.source_id)
        if not s.captioned and not s.reaction and s.id not in have and src is not None and src.has_audio:
            out.append(s)
    return out


def transcribe_segments(project, transcriber, cache_dir: str, max_words: int = 4,
                        progress=None, cancel=None, only=None) -> list[dict]:
    """New captions for every moment in the cut list (does not change the
    project; the caller swaps them in)."""
    out: list[dict] = []
    segs = [s for s in project.segments if not s.reaction and (only is None or s.id in only)]
    for n, seg in enumerate(segs, start=1):
        if cancel is not None and cancel.is_set():
            from clipping.editor.render import Cancelled
            raise Cancelled("Stopped.")
        src = project.source(seg.source_id)
        if src is None or not src.has_audio:
            continue
        words = transcribe_range(src.path, seg.source_in, seg.source_out, transcriber, cache_dir)
        for cap in captions_from_words(project, seg, words, max_words):
            cap["source_id"] = seg.source_id
            out.append(cap)
        if progress:
            progress(n / len(segs))
    return out


# ---------------------------------------------------------------- from the script (phase 3)
def line_signature(line: dict) -> str:
    """Changes whenever the words or their timings change, so only those captions are remade."""
    return hashlib.sha1(json.dumps([line.get("text"), line.get("words"), line.get("duration")],
                                   sort_keys=True).encode()).hexdigest()[:10]


def captions_from_line(line: dict, max_words: int = 4) -> list[dict]:
    """Caption lines for one script line, timed by the voice's word timings
    (evenly spread when the voice has none, e.g. a mic recording).
    Words are in seconds from the start of the voice (kept in src_start/src_end)."""
    timings = line.get("words") or []
    if len(timings) != len((line.get("text") or "").split()):
        from clipping.editor import voice
        timings = voice.even_words(line.get("text", ""), script_lines.line_duration(line))
    words = [{"word": w["word"], "src_start": float(w["start"]), "src_end": float(w["end"])} for w in timings]
    out = []
    for group in group_words(words, max_words):
        cap = make_caption(group, line["segment_id"])
        cap["source_id"], cap["line_id"], cap["line_sig"] = line.get("source_id"), line["id"], line_signature(line)
        out.append(cap)
    return out


def sync_script_captions(project, max_words: int = 4) -> None:
    """Make the captions match the script: remake the captions of lines that
    changed, drop the captions of lines that are gone, leave the rest alone.
    Captions from speech (phase 2) are not touched."""
    script_lines.refresh(project)
    lines = {l["id"]: l for l in project.script_lines}
    keep, have = [], set()
    for cap in project.captions:
        if not cap.get("line_id"):
            keep.append(cap)
            continue
        ln = lines.get(cap["line_id"])
        if ln is not None and cap.get("line_sig") == line_signature(ln):
            keep.append(cap)
            have.add(ln["id"])
    for ln in script_lines.visible(project):
        if ln["id"] not in have and ln.get("text"):
            keep.extend(captions_from_line(ln, max_words))
    project.captions = keep
    refresh(project)


def use_script_captions(project, max_words: int = 4) -> int:
    """Replace every caption with captions from the script. Returns how many."""
    project.captions = []
    sync_script_captions(project, max_words)
    return len(project.captions)
