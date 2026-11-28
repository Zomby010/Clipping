"""Links, joining and storyline cuts."""

import subprocess
from types import SimpleNamespace

import pytest

from clipping import sources
from clipping.providers.validation import InvalidOutputError, validate_clips


def test_saved_video_is_only_reused_for_the_same_link_and_quality(tmp_path):
    video = tmp_path / "video_asli.mp4"
    video.write_bytes(b"old, from before links were recorded")
    (tmp_path / "video_asli.en.json3").write_text("{}")
    assert sources.claim(str(video), ["https://youtu.be/aaaaaaaaaaa"], "max") is False
    assert not video.exists() and not (tmp_path / "video_asli.en.json3").exists()

    video.write_bytes(b"new")
    # The same video written another way is kept.
    assert sources.claim(str(video), ["https://www.youtube.com/watch?v=aaaaaaaaaaa&t=5"], "max") is True
    assert video.exists()
    for links, height in ((["https://youtu.be/bbbbbbbbbbb"], "max"), (["https://youtu.be/bbbbbbbbbbb"], "1080")):
        video.write_bytes(b"x")
        assert sources.claim(str(video), links, height) is False
        assert not video.exists()


def test_story_times_point_into_the_joined_video():
    text = "V1 03:02-03:47 hide, 04:31 run. V2 01:00-01:30 tank\n02:00 first again"
    assert sources.to_joined_times(text, [0.0, 600.0]) == (
        "03:02-03:47 hide, 04:31 run. 11:00-11:30 tank\n02:00 first again")
    with pytest.raises(ValueError):
        sources.to_joined_times("V3 00:10", [0.0, 600.0])


def test_transcript_moves_to_its_place():
    text, segments = sources.shift_transcript(
        "[1.0 - 2.0] hi\n", [{"start": 1.0, "end": 2.0, "words": [{"word": "hi", "start": 1.0, "end": 2.0}]}], 100.0)
    assert text == "[101.0 - 102.0] hi\n"
    assert segments[0]["start"] == 101.0 and segments[0]["words"][0]["end"] == 102.0


def _clip(segments):
    clip = {"viral_score": 90, "hook_start_time": segments[0][0], "hook_end_time": segments[0][0] + 2,
            "keep_segments": [{"start_time": a, "end_time": b} for a, b in segments],
            "broll_list": [], "hook_v2": {"enabled": False, "items": []}, "start_time": 0, "end_time": 0}
    sources.fit_bounds([clip])
    return clip


def test_storyline_clip_may_jump_around_but_not_overlap():
    limits = dict(source_end=3000, requested_clips=1, min_duration=30, max_duration=60)
    jumping = _clip([(1500, 1520), (100, 125)])
    assert (jumping["start_time"], jumping["end_time"]) == (100, 1520)
    validate_clips([jumping], storyline_cuts=True, **limits)
    with pytest.raises(InvalidOutputError):
        validate_clips([jumping], **limits)
    with pytest.raises(InvalidOutputError):
        validate_clips([_clip([(100, 130), (120, 150)])], storyline_cuts=True, **limits)
    with pytest.raises(InvalidOutputError):
        validate_clips([_clip([(100, 150), (900, 950)])], storyline_cuts=True, **limits)  # too long in total


def test_selection_rules_carry_the_storyline():
    cfg = SimpleNamespace(storyline_cuts=True, story_angles="03:02 hide", story_brief="",
                          source_parts=[{"start": 0, "end": 60, "title": "A"}, {"start": 60, "end": 90, "title": "B"}])
    rules = sources.selection_rules(cfg, 30, 60)
    assert "03:02 hide" in rules and "Video 2: 60.0s to 90.0s (B)" in rules
    assert sources.selection_rules(SimpleNamespace(storyline_cuts=False), 30, 60) == ""


@pytest.mark.parametrize("second_size", ["320x180", "240x136"])
def test_join_videos(tmp_path, second_size):
    paths = []
    for name, size, seconds in (("a", "320x180", 2), ("b", second_size, 3)):
        path = str(tmp_path / f"{name}.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=s={size}:r=30:d={seconds}",
                        "-f", "lavfi", "-i", f"sine=d={seconds}", "-pix_fmt", "yuv420p", "-shortest", path],
                       check=True)
        paths.append(path)
    out = str(tmp_path / "joined.mp4")
    offsets = sources.join_videos(paths, out)
    assert offsets[0] == 0 and offsets[1] == pytest.approx(2, abs=0.2)
    info = sources.probe(out)
    assert info["duration"] == pytest.approx(5, abs=0.3) and info["video"]["width"] == 320
