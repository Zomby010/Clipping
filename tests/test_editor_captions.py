"""Clip Editor phase 2: captions that follow the cut, editing, speech caching."""

import json
import os
import threading

import pytest

from clipping.editor import captions as C
from clipping.editor import project as P


def _words(*items):
    return [{"word": w, "src_start": s, "src_end": e} for w, s, e in items]


def _project(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    pr = P.Project()
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080))
    return pr, src


def test_group_words_limits_and_pauses():
    words = _words(("one", 0, .3), ("two", .3, .6), ("three", .6, .9), ("four", .9, 1.2),
                   ("five", 1.2, 1.5), ("after", 2.5, 2.8), ("pause.", 2.8, 3.0), ("New", 3.0, 3.2))
    lines = C.group_words(words, max_words=4)
    assert [[w["word"] for w in line] for line in lines] == [
        ["one", "two", "three", "four"], ["five"], ["after", "pause."], ["New"]]


def test_group_words_character_limit():
    words = _words(("extraordinarily", 0, .5), ("unbelievable", .5, 1), ("moment", 1, 1.5))
    assert len(C.group_words(words, max_words=5, max_chars=24)) == 2


def test_captions_follow_reorder_speed_nudge_and_delete(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "1:00-1:04, 2:00-2:04")          # 4 s + 4 s
    words = _words(("first", 61.0, 61.5), ("line", 61.5, 62.0),
                   ("second", 121.0, 121.5), ("moment", 121.5, 122.0), ("outside", 130, 131))
    for seg in pr.segments:
        pr.captions += C.captions_from_words(pr, seg, words, max_words=2)
    assert len(pr.captions) == 2  # the word outside every moment is dropped
    C.refresh(pr)
    first, second = sorted(pr.captions, key=lambda c: c["src_start"])
    assert (first["start"], first["end"]) == (1.0, 2.0)
    assert (second["start"], second["end"]) == (5.0, 6.0)

    pr.move_segment(1, 0)                                   # reorder
    C.refresh(pr)
    assert (second["start"], first["start"]) == (1.0, 5.0)

    pr.segments[0].speed = 2.0                              # 2x moment
    C.refresh(pr)
    assert (second["start"], second["end"]) == (0.5, 1.0)
    assert first["start"] == 3.0  # the 2x moment now lasts 2 s

    pr.nudge(0, "in", 1.6)                                  # start moves past the first word
    C.refresh(pr)
    assert second["words"][0]["start"] is None and second["start"] == 0.0

    pr.remove_segment(0)                                    # delete its moment
    C.refresh(pr)
    assert not second["visible"] and first["visible"]
    assert [c["id"] for c in C.visible(pr)] == [first["id"]]


def test_caption_moves_to_other_half_after_split(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:10-0:20")
    pr.captions += C.captions_from_words(pr, pr.segments[0], _words(("late", 18, 18.5)))
    pr.split_segment(0, 15)
    pr.move_segment(1, 0)
    C.refresh(pr)
    cap = pr.captions[0]
    assert cap["segment_id"] == pr.segments[0].id and cap["start"] == 3.0


def test_set_text_keeps_or_spreads_timing(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:00-0:10")
    cap = C.captions_from_words(pr, pr.segments[0], _words(("no", 1, 1.2), ("way", 1.4, 2.0)))[0]
    C.set_text(cap, "NO WAY")
    assert [(w["word"], w["src_start"]) for w in cap["words"]] == [("NO", 1), ("WAY", 1.4)]
    C.set_text(cap, "no way bro")
    assert [w["src_start"] for w in cap["words"]] == [1.0, 1.333, 1.667] and cap["text"] == "no way bro"
    with pytest.raises(ValueError):
        C.set_text(cap, "   ")


def test_split_join_delete_nudge(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:00-0:10")
    seg = pr.segments[0]
    seg.speed = 2.0
    pr.captions = C.captions_from_words(pr, seg, _words(("a", 1, 1.5), ("bb", 1.5, 2), ("c", 2, 2.5)), 4)
    cap = pr.captions[0]
    assert C.word_index_at(cap, 2) == 1  # cursor before "bb"
    a, b = C.split(pr, cap["id"], 1)
    assert a["text"] == "a" and b["text"] == "bb c" and a["src_end"] == 1.5
    with pytest.raises(ValueError):
        C.split(pr, a["id"], 0)
    joined = C.join_next(pr, a["id"])
    assert joined["text"] == "a bb c" and len(pr.captions) == 1
    C.nudge(pr, joined["id"], "out", 0.1)        # 0.1 s of video = 0.2 s of a 2x source
    assert joined["src_end"] == 2.7
    C.nudge(pr, joined["id"], "in", -5)
    assert joined["src_start"] == 0.0
    C.delete(pr, joined["id"])
    assert pr.captions == []


def test_join_refuses_across_moments(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:00-0:05, 0:10-0:15")
    w = _words(("one", 1, 2), ("two", 11, 12))
    for seg in pr.segments:
        pr.captions += C.captions_from_words(pr, seg, w)
    C.refresh(pr)
    with pytest.raises(ValueError, match="different moment"):
        C.join_next(pr, pr.captions[0]["id"])


def test_srt_lines(tmp_path):
    pr, src = _project(tmp_path)
    pr.add_ranges(src.id, "0:10-0:20")
    pr.captions = C.captions_from_words(pr, pr.segments[0], _words(("hi", 11, 11.5)))
    assert C.srt_lines(pr) == [{"start": 1.0, "end": 1.5, "text": "hi"}]


class FakeWhisper:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def __call__(self, wav):
        self.calls += 1
        assert os.path.exists(wav)
        return [{"word": " hello", "start": 0.2, "end": 0.6}, {"word": "there ", "start": 0.6, "end": 1.0}]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_transcribe_segments_uses_cache(tmp_path):
    import subprocess
    video = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:duration=6",
                    "-f", "lavfi", "-i", "sine=duration=6", "-shortest", str(video)], check=True)
    from clipping.editor import media
    pr = P.Project()
    src = pr.add_source(media.probe(str(video)))
    pr.add_ranges(src.id, "0:01-0:03, 0:04-0:05")
    fake = FakeWhisper()
    seen = []
    caps = C.transcribe_segments(pr, fake, str(tmp_path / "cache"), progress=seen.append)
    assert fake.calls == 2 and seen[-1] == 1.0
    assert caps[0]["words"][0] == {"word": "hello", "src_start": 1.2, "src_end": 1.6}
    assert caps[0]["segment_id"] == pr.segments[0].id
    C.transcribe_segments(pr, fake, str(tmp_path / "cache"))
    assert fake.calls == 2  # cached: never listens twice
    cached = [f for f in os.listdir(tmp_path / "cache") if f.endswith(".json")]
    assert len(cached) == 2 and json.loads((tmp_path / "cache" / cached[0]).read_text())
    stop = threading.Event()
    stop.set()
    from clipping.editor.render import Cancelled
    with pytest.raises(Cancelled):
        C.transcribe_segments(pr, fake, str(tmp_path / "cache"), cancel=stop)


def _whisper_model_cached() -> bool:
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    return os.path.isdir(hub) and any("faster-whisper-small" in d for d in os.listdir(hub))


@pytest.mark.skipif(not _whisper_model_cached(), reason="faster-whisper 'small' model not downloaded")
def test_real_whisper_runs(tmp_path):  # pragma: no cover - only where the model is on disk
    import subprocess
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2", str(wav)], check=True)
    words = C.WhisperTranscriber("small")(str(wav))
    assert isinstance(words, list)
