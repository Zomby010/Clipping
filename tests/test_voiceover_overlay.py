"""Tests for voice-over overlay mode (voice plays while the clip keeps moving)."""

import importlib.util
import os
import shutil
import subprocess

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "clipping", "studio", "voiceover_overlay.py")
_spec = importlib.util.spec_from_file_location("voiceover_overlay_under_test", _PATH)
vo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vo)


def test_plan_window_fits_inside_clip():
    assert vo.plan_window(10.0, 45.0, 0.5) == (0.5, 10.5)


def test_plan_window_pulls_offset_earlier_when_voice_too_long():
    start, end = vo.plan_window(30.0, 32.0, 5.0)
    assert start == pytest.approx(2.0)
    assert end == pytest.approx(32.0)


def test_plan_window_cuts_voice_longer_than_clip():
    assert vo.plan_window(50.0, 40.0, 1.0) == (0.0, 40.0)


def test_output_to_source_time_without_segments():
    assert vo.output_to_source_time(3.0, 100.0) == pytest.approx(103.0)


def test_output_to_source_time_walks_trimmed_segments():
    segs = [{"start_time": 100, "end_time": 110}, {"start_time": 200, "end_time": 220}]
    assert vo.output_to_source_time(5.0, 100.0, segs) == pytest.approx(105.0)
    assert vo.output_to_source_time(12.0, 100.0, segs) == pytest.approx(202.0)


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text,
            "words": [{"word": text, "start": start, "end": end}]}


def test_merge_captions_replaces_creator_lines_inside_window():
    creator = [_seg(100.0, 101.0, "before"), _seg(102.0, 103.0, "during"), _seg(110.0, 111.0, "after")]
    voice = [_seg(0.0, 0.5, "hello"), _seg(0.5, 1.0, "world")]
    merged = vo.merge_captions(creator, voice, (1.5, 3.0), 100.0)
    texts = [s["text"] for s in merged]
    assert texts == ["before", "hello", "world", "after"]
    assert merged[1]["start"] == pytest.approx(101.5)


def test_merge_captions_maps_voice_into_second_trimmed_segment():
    segs = [{"start_time": 100, "end_time": 102}, {"start_time": 200, "end_time": 210}]
    creator = [_seg(201.0, 202.0, "creator")]
    voice = [_seg(0.0, 0.5, "hi")]
    merged = vo.merge_captions(creator, voice, (3.0, 4.0), 100.0, segs)
    assert [s["text"] for s in merged] == ["hi"]
    assert merged[0]["start"] == pytest.approx(201.0)


def test_audio_filter_ducks_and_delays():
    f = vo.build_audio_filter((2.0, 12.0), duck_volume=0.3, vo_volume=1.0)
    assert "adelay=2000|2000" in f
    assert "eval=frame" in f
    assert "duration=first" in f


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_mix_keeps_video_length_and_adds_voice(tmp_path):
    clip = tmp_path / "clip.ts"
    voice = tmp_path / "voice.mp3"
    out = tmp_path / "out.ts"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=180x320:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", "-f", "mpegts", str(clip)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=2", str(voice)],
        check=True,
    )
    vo.mix_into_clip(str(clip), str(voice), (1.0, 3.0), 0.3, 1.0, str(out))
    clip_len = vo.probe_duration(str(clip))
    out_len = vo.probe_duration(str(out))
    assert out_len == pytest.approx(clip_len, abs=0.2)

    # Video stream must still be moving (copied, not frozen): frame count unchanged.
    def frames(p):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", p],
            stdout=subprocess.PIPE, text=True, check=True,
        )
        return int(r.stdout.split()[0])

    assert frames(str(out)) == frames(str(clip))
