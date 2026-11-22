"""Clip Editor: the project file, editing actions and undo."""

import json
import os
from datetime import datetime

import pytest

from clipping.editor import project as P


def _project(tmp_path):
    video = tmp_path / "game.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title="Worst day")
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080))
    return pr, src


def test_add_ranges_in_typed_order(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "4:06-4:55, 1:02-1:10, 2:38-2:49")
    assert [(s.source_in, s.source_out) for s in pr.segments] == [(246, 295), (62, 70), (158, 169)]
    assert pr.total_length == 68
    assert src.name == "game"
    assert pr.problems() == []


def test_adding_same_video_twice_reuses_it(tmp_path):
    pr, src = _project(tmp_path)
    again = pr.add_source(P.Source(path=src.path))
    assert again is src and len(pr.sources) == 1


def test_segment_end_is_clamped_to_video(tmp_path):
    pr, src = _project(tmp_path)
    seg = pr.add_segment(src.id, 290, 400)
    assert seg.source_out == 300
    with pytest.raises(ValueError):
        pr.add_segment(src.id, 10, 5)


def test_move_split_nudge_remove(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:10-0:20, 0:30-0:40")
    first = pr.segments[0]
    pr.move_segment(0, 1)
    assert pr.segments[1] is first
    a, b = pr.split_segment(1, 15)
    assert (a.source_in, a.source_out, b.source_in, b.source_out) == (10, 15, 15, 20)
    assert a.id != b.id and len(pr.segments) == 3
    with pytest.raises(ValueError):
        pr.split_segment(1, 10.05)
    pr.nudge(0, "in", -0.1)
    pr.nudge(0, "out", 0.1)
    assert (pr.segments[0].source_in, pr.segments[0].source_out) == (29.9, 40.1)
    pr.nudge(0, "in", -100)
    assert pr.segments[0].source_in == 0
    pr.remove_segment(0)
    assert len(pr.segments) == 2


def test_remove_source_removes_its_moments(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:10-0:20")
    pr.remove_source(src.id)
    assert pr.sources == [] and pr.segments == []


def test_save_and_load_round_trip(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "1:02-1:10")
    pr.segments[0].framing.mode = "fit_blur"
    pr.segments[0].speed = 1.5
    folder = P.new_project_dir(str(tmp_path / "My Edits"), "Worst day: part 1?", datetime(2026, 11, 22, 18, 5))
    assert os.path.basename(folder) == "2026-11-22 18-05 - Worst day part 1"
    path = P.save(pr, folder)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["version"] == P.PROJECT_VERSION
    for key in ("script_lines", "captions", "overlays", "audio", "exports"):
        assert key in data  # later phases' fields exist from day one
    loaded, where = P.load(folder)
    assert where == folder
    assert loaded.to_dict() == pr.to_dict()
    assert not [f for f in os.listdir(folder) if f.startswith(".project-")]


def test_new_project_dir_never_overwrites(tmp_path):
    when = datetime(2026, 11, 22, 18, 5)
    a = P.new_project_dir(str(tmp_path), "Same", when)
    b = P.new_project_dir(str(tmp_path), "Same", when)
    assert a != b and b.endswith("(2)")


def test_unknown_future_keys_are_ignored_and_newer_versions_refused(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:01-0:02")
    data = pr.to_dict()
    data["something_new"] = 1
    data["segments"][0]["shiny"] = True
    data["segments"][0]["framing"]["sparkle"] = 2
    assert P.Project.from_dict(data).segments[0].source_out == 2
    data["version"] = P.PROJECT_VERSION + 1
    with pytest.raises(ValueError):
        P.Project.from_dict(data)


def test_missing_video_is_reported(tmp_path):
    pr, src = _project(tmp_path)
    os.unlink(src.path)
    assert any("can't be found" in p for p in pr.problems())


def test_framing_centre():
    f = P.Framing()
    assert f.centre() == (0.5, 0.5)
    f.auto_x, f.auto_y = 0.2, 0.4
    assert f.centre() == (0.2, 0.4)
    f.mode, f.pan_x = "crop", 0.9
    assert f.centre() == (0.9, 0.5)


def test_undo_redo(tmp_path):
    pr, src = _project(tmp_path)
    h = P.History(pr)
    assert not h.record(pr)  # nothing changed
    pr.add_ranges(src.id, "0:10-0:20")
    assert h.record(pr)
    pr.title = "Renamed"
    h.record(pr)
    back = h.undo()
    assert back.title == "Worst day" and len(back.segments) == 1
    back = h.undo()
    assert back.segments == []
    assert h.undo() is None
    forward = h.redo()
    assert len(forward.segments) == 1
    forward.title = "Other"
    h.record(forward)
    assert not h.can_redo()  # a new change clears redo
