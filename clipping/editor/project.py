"""
clipping.editor.project — The edit project: one JSON file per edit.

An edit lives in its own folder:

    My Edits/
      2026-11-22 18-05 - Delivery guy's worst day/
        project.json     everything about the edit (this module)
        Preview/         quick low-res preview copies (safe to delete)

Finished videos go to "Ready to post/<date> - <title>/" (see render.py).

The file has a ``version`` so later phases can add fields without breaking
old edits. Fields for later phases (script, captions, overlays, audio) are
already here, empty, so phase 1 files open fine in later versions.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from clipping.editor import timeline

PROJECT_VERSION = 1
PROJECT_FILE = "project.json"
NOT_AN_EDIT = "That isn't a saved edit. Pick the project.json inside a folder in My Edits."


class NotAnEdit(ValueError):
    """The file picked to open is not a saved edit."""


MODES = ("explain", "react", "new_story")
CHANNELS = ("GTA 6", "Sports", "Kids", "Other")

# Framing modes for 9:16:
#   auto      crop that follows the people / action (worked out once per moment)
#   crop      crop with a manual left-right / up-down position
#   fit_blur  whole picture fitted in the middle, blurred copy behind it
FRAMING_MODES = ("auto", "crop", "fit_blur")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Source:
    path: str
    name: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    has_audio: bool = True
    kind: str = ""            # "reaction" = a reaction file inserted into the cut, not footage of yours
    id: str = field(default_factory=lambda: _new_id("src"))


@dataclass
class Framing:
    mode: str = "auto"
    pan_x: float = 0.5        # 0 = far left, 1 = far right (crop mode)
    pan_y: float = 0.5        # 0 = top, 1 = bottom (only when zoomed or tall video)
    zoom: float = 1.0         # 1 = no zoom, 1.5 = punch in 50 %
    auto_x: float | None = None  # result of the auto-crop, None until worked out
    auto_y: float | None = None

    def centre(self) -> tuple[float, float]:
        """The crop centre to use, as fractions of the frame."""
        if self.mode == "auto":
            return (0.5 if self.auto_x is None else self.auto_x,
                    0.5 if self.auto_y is None else self.auto_y)
        return self.pan_x, self.pan_y


@dataclass
class Segment:
    source_id: str
    source_in: float
    source_out: float
    speed: float = 1.0
    join: str = "cut"          # how it joins the moment before (later: flash, zoom, whoosh)
    framing: Framing = field(default_factory=Framing)
    note: str = ""
    id: str = field(default_factory=lambda: _new_id("seg"))
    # --- phase 3: voice ---
    # How loud the moment's own sound is, 0 (muted) to 1. None = follow the mode
    # (Explain 1, React 1, New story 0).
    original_level: float | None = None
    # --- end phase 3 ---
    # The moment My Clips makes from the whole clip. It stands in until you add
    # your own moments from that video.
    whole_clip: bool = False
    captioned: bool = False    # speech in this moment has been listened to
    # An inserted reaction (see reactions.py), and whether inserting it cut a moment in two.
    reaction: bool = False
    split_here: bool = False

    @property
    def length(self) -> float:
        return timeline.segment_length(self.source_in, self.source_out, self.speed)


@dataclass
class Project:
    title: str = "Untitled edit"
    channel: str = "GTA 6"
    mode: str = "explain"
    angle: str = ""
    sources: list[Source] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    # Phase 2: captions (see captions.py), caption look changes, the hook
    # line, pop-up text (in overlays), cover picture and upload text.
    captions: list[dict] = field(default_factory=list)
    caption_style: dict = field(default_factory=dict)   # size_scale, position, word_highlight, all_caps
    hook: dict = field(default_factory=dict)            # {"text": ..., "seconds": 2.0}
    overlays: list[dict] = field(default_factory=list)  # {"type": "text", start, end, text, position}
    cover: dict = field(default_factory=dict)           # {"time": edit seconds, "title": ...}
    upload: dict = field(default_factory=dict)          # {"title", "description", "hashtags"}
    # --- phase 3: story and voice ---
    scene_log: list[dict] = field(default_factory=list)  # [{source_id, start, end, what_happens, who, mood, viral_score, why}]
    voices: dict = field(default_factory=dict)           # {"default": voice, "speed": 0, "pitch": 0, "characters": [{name, about, voice}]}
    stars: list[dict] = field(default_factory=list)      # starred moments (score >= 7), a view of scene_log
    # --- end phase 3 ---
    # Later phases. Kept in the file from day one.
    script_lines: list[dict] = field(default_factory=list)
    # Phase 4: {"music": {...}, "sfx": [...], "loudness": -14} (see audio_mix.py,
    # overlays.py). Reactions, arrows and circles go in overlays.
    audio: dict = field(default_factory=dict)
    # --- phase 4: channel logo {"path", "corner", "size", "opacity"} ---
    logo: dict = field(default_factory=dict)
    # --- phase 5: last Claude review, and defaults from the channel template ---
    review: dict = field(default_factory=dict)          # {"suggestions": [...], "at": ...}
    defaults: dict = field(default_factory=dict)        # {"framing": "fit_blur"}
    exports: list[dict] = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    version: int = PROJECT_VERSION

    # ---------------------------------------------------------------- lookups
    def source(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def footage(self) -> list[Source]:
        """The videos you cut from (reaction files are not among them)."""
        return [s for s in self.sources if s.kind != "reaction"]

    def segment_index(self, segment_id: str) -> int:
        for i, s in enumerate(self.segments):
            if s.id == segment_id:
                return i
        raise KeyError(segment_id)

    @property
    def total_length(self) -> float:
        return timeline.total_length(self.segments)

    def problems(self) -> list[str]:
        out = []
        known = {s.id for s in self.sources}
        for i, seg in enumerate(self.segments, start=1):
            if seg.source_id not in known:
                out.append(f"Moment {i}: its video is no longer in this edit.")
        used = {s.source_id for s in self.segments}
        for src in self.sources:
            if src.kind == "reaction" and src.id not in used:
                continue
            if not os.path.exists(src.path):
                out.append(f"The video '{src.name or src.path}' can't be found at {src.path}.")
        out += timeline.validate(self.segments, {s.id: s.duration for s in self.sources})
        return out

    # ---------------------------------------------------------------- editing
    def add_source(self, source: Source) -> Source:
        for s in self.sources:
            if os.path.abspath(s.path) == os.path.abspath(source.path):
                return s
        if not source.name:
            source.name = os.path.splitext(os.path.basename(source.path))[0]
        self.sources.append(source)
        return source

    def remove_source(self, source_id: str):
        self.sources = [s for s in self.sources if s.id != source_id]
        self.segments = [s for s in self.segments if s.source_id != source_id]

    def add_segment(self, source_id: str, start: float, end: float, at: int | None = None) -> Segment:
        src = self.source(source_id)
        if src is None:
            raise KeyError(source_id)
        if src.duration:
            end = min(end, src.duration)
        if end <= start:
            raise ValueError("The end must be after the start.")
        seg = Segment(source_id=source_id, source_in=max(0.0, start), source_out=end)
        if (self.defaults or {}).get("framing") in FRAMING_MODES:
            seg.framing.mode = self.defaults["framing"]
        if at is None:
            self.segments.append(seg)
        else:
            self.segments.insert(at, seg)
        return seg

    def add_ranges(self, source_id: str, text: str) -> list[Segment]:
        """Add every range typed like '1:02-1:10, 2:38-2:49' (in that order)."""
        ranges = timeline.parse_ranges(text)
        src = self.source(source_id)
        if src is not None and src.duration:
            for a, b in ranges:
                # 1 s of slack: lengths are shown rounded to the second.
                if a >= src.duration or b > src.duration + 1.0:
                    raise ValueError(
                        f"{timeline.format_time(a, True)}-{timeline.format_time(b, True)} is outside the video, "
                        f"which is {timeline.format_time(src.duration, True)} long. Nothing was added.")
        return [self.add_segment(source_id, a, b) for a, b in ranges]

    def drop_whole_clip(self, source_id: str) -> int:
        """Take out the untouched whole-clip moment of this video, so your cut
        is only the moments you picked. Returns how many went."""
        keep = [s for s in self.segments if not (s.whole_clip and s.source_id == source_id)]
        gone = len(self.segments) - len(keep)
        self.segments = keep
        return gone

    def remove_segment(self, index: int):
        del self.segments[index]

    def move_segment(self, index: int, to: int):
        to = max(0, min(to, len(self.segments) - 1))
        seg = self.segments.pop(index)
        self.segments.insert(to, seg)

    def split_segment(self, index: int, source_time: float) -> tuple[Segment, Segment]:
        """Cut one moment in two at ``source_time`` (a time in its video)."""
        seg = self.segments[index]
        if not (seg.source_in + 0.1 < source_time < seg.source_out - 0.1):
            raise ValueError("Pick a point inside the moment to split it.")
        seg.whole_clip = False      # you are cutting it up: it is yours now
        second = copy.deepcopy(seg)
        second.id = _new_id("seg")
        seg.source_out = source_time
        second.source_in = source_time
        self.segments.insert(index + 1, second)
        return seg, second

    def nudge(self, index: int, edge: str, delta: float):
        """Move a moment's start or end by ``delta`` seconds (e.g. ±0.1)."""
        seg = self.segments[index]
        seg.whole_clip = False
        src = self.source(seg.source_id)
        limit = src.duration if src and src.duration else float("inf")
        if edge == "in":
            seg.source_in = round(min(max(0.0, seg.source_in + delta), seg.source_out - 0.1), 3)
        elif edge == "out":
            seg.source_out = round(max(min(limit, seg.source_out + delta), seg.source_in + 0.1), 3)
        else:
            raise ValueError("edge must be 'in' or 'out'")

    # ---------------------------------------------------------------- files
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        data = dict(data or {})
        version = int(data.get("version", 1))
        if version > PROJECT_VERSION:
            raise ValueError("This edit was made by a newer Clip Editor. Update the app to open it.")
        sources = [Source(**_known(Source, s)) for s in data.get("sources", [])]
        segments = []
        for s in data.get("segments", []):
            s = dict(s)
            framing = Framing(**_known(Framing, s.pop("framing", {}) or {}))
            segments.append(Segment(framing=framing, **_known(Segment, s, skip=("framing",))))
        plain = _known(cls, data, skip=("sources", "segments", "version"))
        return cls(sources=sources, segments=segments, version=PROJECT_VERSION, **plain)


def _known(klass, data: dict, skip=()) -> dict:
    """Keep only the fields ``klass`` knows, so unknown future keys don't crash."""
    names = {f for f in klass.__dataclass_fields__} - set(skip)
    return {k: v for k, v in data.items() if k in names}


# -------------------------------------------------------------------- folders
def default_edits_root(base_dir: str) -> str:
    return os.path.join(base_dir, "My Edits")


def default_ready_root(base_dir: str) -> str:
    return os.path.join(base_dir, "Ready to post")


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", text or "").strip(" .")
    text = re.sub(r"\s+", " ", text)
    return (text[:limit].rstrip(" .") or "Untitled edit")


def new_project_dir(edits_root: str, title: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    base = os.path.join(edits_root, f"{now:%Y-%m-%d %H-%M} - {safe_name(title)}")
    path, n = base, 2
    while os.path.exists(path):
        path, n = f"{base} ({n})", n + 1
    os.makedirs(path)
    return path


def save(project: Project, folder: str) -> str:
    """Write project.json atomically (a crash never leaves half a file)."""
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, PROJECT_FILE)
    fd, tmp = tempfile.mkstemp(prefix=".project-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load(path: str) -> tuple[Project, str]:
    """Open an edit from its folder or its project.json. Returns (project, folder)."""
    if os.path.isdir(path):
        folder, file = path, os.path.join(path, PROJECT_FILE)
    else:
        folder, file = os.path.dirname(path), path
    if os.path.basename(file) != PROJECT_FILE:
        raise NotAnEdit(NOT_AN_EDIT)
    with open(file, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError:
            raise NotAnEdit(NOT_AN_EDIT) from None
    if not isinstance(data, dict) or "segments" not in data:
        raise NotAnEdit(NOT_AN_EDIT)
    return Project.from_dict(data), folder


# -------------------------------------------------------------------- undo
class History:
    """Undo / redo by keeping snapshots of the project (they are small)."""

    def __init__(self, project: Project, limit: int = 200):
        self.limit = limit
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._last = project.to_dict()

    def record(self, project: Project) -> bool:
        """Call after every change. Returns False when nothing changed."""
        now = project.to_dict()
        if now == self._last:
            return False
        self._undo.append(self._last)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()
        self._last = now
        return True

    def absorb(self, project: Project):
        """Make the current state ``project`` without adding an undo step
        (for results the app works out by itself, like the automatic crop)."""
        self._last = project.to_dict()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Project | None:
        if not self._undo:
            return None
        self._redo.append(self._last)
        self._last = self._undo.pop()
        return Project.from_dict(copy.deepcopy(self._last))

    def redo(self) -> Project | None:
        if not self._redo:
            return None
        self._undo.append(self._last)
        self._last = self._redo.pop()
        return Project.from_dict(copy.deepcopy(self._last))
