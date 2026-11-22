"""Clip Editor phase 3: script lines timed to the cut, word budget, anchoring, old files."""

import json

import pytest

from clipping.editor import project as P
from clipping.editor import script as S


def _project(speed2=1.0):
    pr = P.Project(title="Story", channel="GTA 6", mode="explain")
    src = pr.add_source(P.Source(path="/x/a.mp4", name="a", duration=300, width=1920, height=1080))
    a = pr.add_segment(src.id, 10, 20)           # 10 s
    b = pr.add_segment(src.id, 100, 110)         # 10 s
    b.speed = speed2
    return pr, src, a, b


# ---------------------------------------------------------------- word budget
def test_budget_and_trim():
    assert S.line_budget(10) == 22 and S.line_budget(0) == 1
    assert S.estimate_duration("one two three four five") == pytest.approx(5 / 2.2)
    text = " ".join(f"w{i}" for i in range(40))
    out = S.trim_to_budget(text, 5)
    assert len(out.rstrip("…").split()) == 11 and out.endswith("…")
    assert S.trim_to_budget("short line", 5) == "short line"


# ---------------------------------------------------------------- anchoring
def test_line_lands_in_edit_time():
    pr, _src, a, b = _project()
    ln = S.make_line(pr, b.id, 2.0, "Here we go")
    pr.script_lines.append(ln)
    S.refresh(pr)
    assert ln["start"] == pytest.approx(10 + 2.0)       # moment b starts at 10 s
    assert ln["offset"] == pytest.approx(2.0)


def test_reorder_moves_the_line_with_its_moment():
    pr, _src, a, b = _project()
    ln = S.make_line(pr, b.id, 2.0, "Here we go")
    pr.script_lines.append(ln)
    pr.move_segment(1, 0)                                # b is now first
    S.refresh(pr)
    assert ln["start"] == pytest.approx(2.0)


def test_trim_the_start_keeps_the_line_on_its_footage():
    pr, _src, _a, b = _project()
    ln = S.make_line(pr, b.id, 4.0, "Look at that")      # footage 104 s
    pr.script_lines.append(ln)
    pr.nudge(1, "in", 1.0)                               # moment now starts at 101 s
    S.refresh(pr)
    assert ln["offset"] == pytest.approx(3.0)
    assert ln["start"] == pytest.approx(10 + 3.0)


def test_line_in_a_double_speed_moment():
    pr, _src, _a, b = _project(speed2=2.0)               # b lasts 5 s
    ln = S.make_line(pr, b.id, 2.0, "Fast")              # 2 s in = footage 104 s
    pr.script_lines.append(ln)
    assert ln["src_start"] == pytest.approx(104.0)
    S.refresh(pr)
    assert ln["start"] == pytest.approx(10 + 2.0)
    pr.segments[1].speed = 1.0                           # back to normal speed: 10 s long
    S.refresh(pr)
    assert ln["offset"] == pytest.approx(4.0)            # the same footage is now 4 s in


def test_split_hands_the_line_to_the_right_half():
    pr, _src, _a, b = _project()
    ln = S.make_line(pr, b.id, 8.0, "Late line")         # footage 108 s
    pr.script_lines.append(ln)
    first, second = pr.split_segment(1, 105.0)
    S.refresh(pr)
    assert ln["segment_id"] == second.id and ln["visible"]
    assert ln["start"] == pytest.approx(10 + 5 + 3.0)


def test_delete_a_moment_hides_the_line_and_undo_brings_it_back():
    pr, _src, a, b = _project()
    ln = S.make_line(pr, b.id, 1.0, "Bye")
    pr.script_lines.append(ln)
    hist = P.History(pr)
    pr.remove_segment(1)
    S.refresh(pr)
    assert ln["visible"] is False and ln in pr.script_lines          # hidden, not lost
    assert S.visible(pr) == []
    # restore the moment (what Undo does) and the line shows again
    pr.segments.append(b)
    S.refresh(pr)
    assert ln["visible"] is True and ln["start"] == pytest.approx(10 + 1.0)


# ---------------------------------------------------------------- Claude's lines
def test_from_claude_fits_lines_and_never_overlaps():
    pr, _src, a, b = _project()
    items = [
        {"segment_id": a.id, "offset": 0.0, "text": " ".join(["word"] * 30), "character": "", "cue": "sfx:whoosh"},
        {"segment_id": a.id, "offset": 3.0, "text": "Second line here", "character": "", "cue": ""},
        {"segment_id": "seg_missing", "offset": 0.0, "text": "ignored", "character": "", "cue": ""},
        {"segment_id": b.id, "offset": 0.0, "text": "   ", "character": "", "cue": ""},
    ]
    lines = S.from_claude(pr, items)
    assert len(lines) == 2                                           # unknown moment + empty text dropped
    assert lines[0]["cue"] == "sfx:whoosh"
    pr.script_lines = lines
    S.refresh(pr)
    # the first line was cut to what fits before the second one starts
    assert S.count_words(lines[0]["text"]) <= S.line_budget(3.0)
    assert S.warnings(pr) == []


def test_lines_pushed_apart_when_claude_overlaps_them():
    pr, _src, a, _b = _project()
    items = [{"segment_id": a.id, "offset": 0.0, "text": "one two three four five six", "character": "", "cue": ""},
             {"segment_id": a.id, "offset": 0.5, "text": "seven eight nine", "character": "", "cue": ""}]
    lines = S.from_claude(pr, items)
    pr.script_lines = lines
    S.refresh(pr)
    assert lines[1]["start"] >= lines[0]["end"] - 1e-6
    assert not [w for w in S.warnings(pr) if w["kind"] == "overlap"]


def test_warning_when_a_line_runs_past_its_moment():
    pr, _src, a, _b = _project()
    ln = S.make_line(pr, a.id, 8.0, "x " * 20)
    pr.script_lines.append(ln)
    kinds = [w["kind"] for w in S.warnings(pr)]
    assert "too_long" in kinds
    assert "past its moment" in S.warning_for(pr, ln["id"])


def test_editing_words_clears_the_voice():
    pr, _src, a, _b = _project()
    ln = S.make_line(pr, a.id, 0.0, "Old words")
    ln.update(audio="Voice/x.mp3", duration=2.0, words=[{"word": "Old", "start": 0, "end": 1}])
    S.set_text(ln, "Old words")                                      # unchanged: keeps the voice
    assert ln["audio"]
    S.set_text(ln, "New words")
    assert ln["audio"] == "" and ln["duration"] == 0.0 and ln["words"] == []


def test_nudge_moves_by_half_seconds_inside_the_moment():
    pr, _src, a, _b = _project()
    ln = S.make_line(pr, a.id, 1.0, "Move me")
    pr.script_lines.append(ln)
    S.nudge(pr, ln["id"], 0.5)
    assert ln["offset"] == pytest.approx(1.5)
    S.nudge(pr, ln["id"], -5)
    assert ln["offset"] == 0.0


# ---------------------------------------------------------------- old files
def test_old_project_files_open_unchanged(tmp_path):
    old = {"version": 1, "title": "Old edit", "channel": "Sports", "mode": "react",
           "sources": [{"id": "src_1", "path": "/x/a.mp4", "name": "a", "duration": 100}],
           "segments": [{"id": "seg_1", "source_id": "src_1", "source_in": 1, "source_out": 5,
                         "framing": {"mode": "crop"}}],
           "captions": [], "script_lines": [], "audio": {"music": "x"}}
    f = tmp_path / "project.json"
    f.write_text(json.dumps(old))
    pr, folder = P.load(str(tmp_path))
    assert pr.scene_log == [] and pr.voices == {} and pr.stars == []
    assert pr.segments[0].original_level is None and pr.segments[0].framing.mode == "crop"
    assert pr.audio == {"music": "x"}
    P.save(pr, folder)
    again = json.loads(f.read_text())
    assert again["audio"] == {"music": "x"} and again["title"] == "Old edit"
    assert again["segments"][0]["original_level"] is None


def test_phase3_fields_round_trip(tmp_path):
    pr, _src, a, _b = _project()
    a.original_level = 0.4
    pr.voices = {"default": "en-KE-AsiliaNeural", "characters": [{"name": "Pip", "about": "a robot", "voice": "en-NG-AbeoNeural"}]}
    pr.scene_log = [{"source_id": "s", "start": 1, "end": 2, "what_happens": "x", "who": "y", "mood": "z",
                     "viral_score": 8, "why": "w"}]
    pr.script_lines = [S.make_line(pr, a.id, 1.0, "Hi", character="Pip", cue="sfx:pop")]
    P.save(pr, str(tmp_path))
    back, _ = P.load(str(tmp_path))
    assert back.segments[0].original_level == 0.4
    assert back.voices["characters"][0]["name"] == "Pip"
    assert back.script_lines[0]["cue"] == "sfx:pop" and back.scene_log[0]["viral_score"] == 8
