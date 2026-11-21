"""Tests for reaction clips cut into a Short (clip pauses, reaction plays, clip resumes)."""

import importlib.util
import os
import shutil
import subprocess

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "clipping", "studio", "reactions.py")
_spec = importlib.util.spec_from_file_location("reactions_under_test", _PATH)
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)


def _moods(tmp_path, names=("laugh", "shock")):
    for name in names:
        folder = tmp_path / name
        folder.mkdir()
        (folder / f"{name}1.mp4").write_bytes(b"")
    (tmp_path / "empty").mkdir()
    return rx.list_moods(str(tmp_path))


def test_list_moods_skips_empty_folders(tmp_path):
    moods = _moods(tmp_path)
    assert sorted(moods) == ["laugh", "shock"]
    assert rx.list_moods(str(tmp_path / "missing")) == {}


def test_source_to_output_without_and_with_segments():
    assert rx.source_to_output(105.0, 100.0, 130.0) == pytest.approx(5.0)
    assert rx.source_to_output(150.0, 100.0, 130.0) is None
    segs = [{"start_time": 100, "end_time": 110}, {"start_time": 200, "end_time": 220}]
    assert rx.source_to_output(205.0, 100.0, 220.0, segs) == pytest.approx(15.0)
    assert rx.source_to_output(150.0, 100.0, 220.0, segs) is None  # trimmed out


def test_plan_inserts_applies_safety_rules(tmp_path):
    moods = _moods(tmp_path)
    inserts = [
        {"time": 100.5, "mood": "laugh"},   # too close to the start
        {"time": 105.0, "mood": "laugh"},   # inside the voice-over window
        {"time": 112.0, "mood": "LAUGH"},   # ok
        {"time": 113.0, "mood": "shock"},   # too close to the previous one
        {"time": 120.0, "mood": "unknown"}, # ok, falls back to any clip
        {"time": 125.0, "mood": "shock"},   # over max_count
        {"mood": "laugh"},                  # no time
    ]
    plan = rx.plan_inserts(
        inserts, moods, m_start=100.0, m_end=130.0, clip_duration=30.0,
        avoid_window=(3.0, 8.0), max_count=2,
    )
    assert [p["at"] for p in plan] == [12.0, 20.0]
    assert plan[0]["mood"] == "laugh"
    assert plan[1]["mood"] == "any"


def test_plan_inserts_nothing_without_clips():
    assert rx.plan_inserts([{"time": 10, "mood": "laugh"}], {}, m_start=0, m_end=30, clip_duration=30) == []


def test_shift_caption_lines_pushes_later_lines():
    lines = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 6.0, "end": 7.0, "text": "b"}]
    shifted = rx.shift_caption_lines(lines, [{"at": 5.0, "duration": 1.2}])
    assert shifted[0]["start"] == pytest.approx(1.0)
    assert shifted[1]["start"] == pytest.approx(7.2)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_splice_adds_reaction_length(tmp_path):
    clip = tmp_path / "clip.ts"
    laugh = tmp_path / "laugh.mp4"
    silent = tmp_path / "silent.mov"
    out = tmp_path / "out.ts"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=180x320:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", "-f", "mpegts", str(clip)],
        check=True,
    )
    # A landscape reaction with sound, longer than the limit (gets cut)...
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(laugh)],
        check=True,
    )
    # ...and one with no sound at all.
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:size=200x200:rate=30:duration=0.8",
         "-c:v", "libx264", str(silent)],
        check=True,
    )
    plan = [
        {"at": 2.0, "path": str(laugh), "mood": "laugh"},
        {"at": 4.0, "path": str(silent), "mood": "shock"},
    ]
    done = rx.splice(
        str(clip), plan, max_seconds=1.2, volume=1.0,
        encode_args=["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "30",
                     "-c:a", "aac", "-ar", "48000", "-ac", "2", "-f", "mpegts"],
        out_path=str(out),
    )
    assert [d["duration"] for d in done] == pytest.approx([1.2, 0.8], abs=0.05)
    clip_len, _ = rx._probe(str(clip))
    out_len, out_audio = rx._probe(str(out))
    assert out_audio
    assert out_len == pytest.approx(clip_len + 2.0, abs=0.25)
    assert rx._probe_size(str(out)) == (180, 320)
