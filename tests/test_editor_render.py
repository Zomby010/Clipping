"""Clip Editor: crop maths, the ffmpeg command, and real renders of tiny test videos."""

import os
import shutil
import subprocess
from datetime import datetime

import pytest

from clipping.editor import framing, media, render
from clipping.editor import project as P

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="ffmpeg not installed")


def test_crop_box_wide_video():
    assert framing.crop_box(1920, 1080) == (608, 1080, 656, 0)
    # far left / far right stay inside the picture
    assert framing.crop_box(1920, 1080, (0.0, 0.5))[2] == 0
    w, _h, x, _y = framing.crop_box(1920, 1080, (1.0, 0.5))
    assert x + w <= 1920
    # zoom keeps 9:16 and makes the area smaller
    w, h, x, y = framing.crop_box(1920, 1080, (0.5, 0.5), zoom=2.0)
    assert (w, h) == (304, 540) and 0 <= y <= 540


def test_crop_box_tall_video_keeps_full_width():
    w, h, x, y = framing.crop_box(1080, 2400)
    assert w == 1080 and h == 1920 and x == 0


def test_atempo_chain():
    assert render.atempo_chain(1.0) == []
    assert render.atempo_chain(1.5) == ["atempo=1.5"]
    assert render.atempo_chain(4.0) == ["atempo=2", "atempo=2"]
    assert render.atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]


def _project(tmp_path, has_audio=True):
    video = tmp_path / "src.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title="Test")
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080, has_audio=has_audio))
    return pr, src


def test_command_uses_fast_seek_per_moment(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "1:02-1:10, 0:05-0:07")
    cmd = render.build_command(pr, "out.mp4")
    assert cmd[cmd.index("-i") - 4: cmd.index("-i") + 1] == ["-ss", "62", "-t", "8", "-i"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1[vcat][aout]" in graph and "[vcat]null[vout]" in graph
    assert "crop=608:1080:656:0,scale=1080:1920" in graph
    assert cmd[cmd.index("-preset") + 1] == "veryfast"
    assert cmd[-1] == "out.mp4"


def test_command_handles_silent_video_speed_and_blur(tmp_path):
    pr, src = _project(tmp_path, has_audio=False)
    seg = pr.add_segment(src.id, 0, 4)
    seg.speed = 2.0
    seg.framing.mode = "fit_blur"
    graph = render.build_command(pr, "o.mp4", render.PREVIEW)
    graph = graph[graph.index("-filter_complex") + 1]
    assert "anullsrc" in graph and "atrim=0:2" in graph
    assert "boxblur" in graph and "overlay" in graph
    assert "setpts=(PTS-STARTPTS)/2" in graph
    assert "scale=540:960" in graph


def test_command_refuses_empty_or_broken_edits(tmp_path):
    pr, src = _project(tmp_path)
    with pytest.raises(render.RenderError, match="empty"):
        render.build_command(pr, "o.mp4")
    pr.add_segment(src.id, 0, 5)
    os.unlink(src.path)
    with pytest.raises(render.RenderError, match="can't be found"):
        render.build_command(pr, "o.mp4")


def test_safe_zone_filter():
    f = render.safe_zone_filter()
    assert f.count("drawbox") == len(render.SAFE_ZONES)


# ---------------------------------------------------------------- real renders
def _make_video(path, size, seconds, audio=True):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


@needs_ffmpeg
def test_probe(tmp_path):
    src = media.probe(_make_video(tmp_path / "w.mp4", "320x180", 3))
    assert (src.width, src.height, src.has_audio) == (320, 180, True)
    assert src.duration == pytest.approx(3, abs=0.1)
    silent = media.probe(_make_video(tmp_path / "s.mp4", "180x320", 2, audio=False))
    assert not silent.has_audio
    with pytest.raises(media.MediaError):
        media.probe(str(tmp_path / "missing.mp4"))


@needs_ffmpeg
def test_export_joins_moments_from_two_videos(tmp_path):
    wide = media.probe(_make_video(tmp_path / "wide.mp4", "320x180", 10))
    tall = media.probe(_make_video(tmp_path / "tall.mp4", "180x320", 6, audio=False))
    pr = P.Project(title="Join test")
    a, b = pr.add_source(wide), pr.add_source(tall)
    pr.add_ranges(a.id, "0:01-0:03, 0:06-0:08")   # 2 s + 2 s
    seg = pr.add_segment(b.id, 1, 3)               # 2 s at 2x = 1 s
    seg.speed = 2.0
    pr.segments[1].framing.mode = "fit_blur"
    seen = []
    rec = render.export(pr, str(tmp_path / "Ready to post"), on_progress=seen.append,
                        now=datetime(2026, 11, 22, 18, 0), fonts={})
    assert rec["folder"].endswith("2026-11-22 - Join test")
    out = media.probe(rec["path"])
    assert (out.width, out.height, out.has_audio) == (1080, 1920, True)
    assert out.duration == pytest.approx(5, abs=0.15)
    assert seen and seen[-1] == 1.0
    assert pr.exports[-1]["seconds_taken"] >= 0
    assert not [f for f in os.listdir(rec["folder"]) if ".part" in f]


@needs_ffmpeg
def test_preview_is_small_and_cached(tmp_path):
    src = media.probe(_make_video(tmp_path / "v.mp4", "320x180", 4))
    pr = P.Project()
    s = pr.add_source(src)
    pr.add_ranges(s.id, "0:00-0:02")
    first = render.make_preview(pr, str(tmp_path))
    assert media.probe(first).height == 960
    mtime = os.path.getmtime(first)
    assert render.make_preview(pr, str(tmp_path)) == first and os.path.getmtime(first) == mtime
    pr.segments[0].source_out = 3
    second = render.make_preview(pr, str(tmp_path))
    assert second != first and not os.path.exists(first)


@needs_ffmpeg
def test_cancel_stops_and_cleans_up(tmp_path):
    import threading
    src = media.probe(_make_video(tmp_path / "v.mp4", "320x180", 3))
    pr = P.Project()
    s = pr.add_source(src)
    pr.add_ranges(s.id, "0:00-0:03")
    stop = threading.Event()
    stop.set()
    out = tmp_path / "o.mp4"
    with pytest.raises(render.Cancelled):
        render.run(render.build_command(pr, str(out)), 3, cancel=stop)
    assert not out.exists()
