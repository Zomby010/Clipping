"""Clip Editor phase 3: voices, the voice cache, the mic, and voice_clips() for the mixer."""

import os
import shutil
import socket
import subprocess

import pytest

from clipping.editor import project as P
from clipping.editor import script as S
from clipping.editor import voice as V

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg missing")


def tone(path, seconds=1.0, freq=440, rate=24000):
    """A sine-tone file standing in for a voice."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
                    "-c:a", "libmp3lame", "-f", "mp3", path], check=True)


class FakeSynth:
    """Stands in for edge-tts: makes a tone whose length follows the word count."""
    def __init__(self):
        self.calls = []

    def __call__(self, text, voice, rate, pitch, out_path):
        self.calls.append((text, voice, rate, pitch))
        n = len(text.split())
        tone(out_path, seconds=n * 0.4)
        return [{"word": w, "start": round(i * 0.4, 3), "end": round((i + 1) * 0.4, 3)} for i, w in enumerate(text.split())]


def _project():
    pr = P.Project(title="V", channel="Sports", mode="explain")
    src = pr.add_source(P.Source(path="/x/a.mp4", name="a", duration=300, width=1920, height=1080))
    a = pr.add_segment(src.id, 10, 20)
    b = pr.add_segment(src.id, 100, 110)
    return pr, src, a, b


def test_voice_list_and_defaults():
    ids = [v[0] for v in V.VOICES]
    for wanted in ("en-KE-AsiliaNeural", "en-KE-ChilembaNeural", "en-NG-AbeoNeural", "en-NG-EzinneNeural",
                   "sw-KE-RafikiNeural", "sw-KE-ZuriNeural", "en-US-GuyNeural", "en-GB-RyanNeural"):
        assert wanted in ids
    pr = P.Project(channel="Kids")
    assert V.settings(pr)["default"] == "sw-KE-ZuriNeural" and V.language_of("sw-KE-ZuriNeural") == "sw"
    pr.channel = "Sports"
    assert V.settings(pr)["default"] == "en-NG-AbeoNeural"
    assert V.rate_string(10) == "+10%" and V.rate_string(-5) == "-5%" and V.pitch_string(0) == "+0Hz"


def test_voice_per_character_then_default():
    pr, _s, a, _b = _project()
    pr.voices = {"default": "en-KE-AsiliaNeural", "characters": [{"name": "Pip", "voice": "en-US-GuyNeural"}]}
    assert V.voice_for(pr, {"character": "pip"}) == "en-US-GuyNeural"
    assert V.voice_for(pr, {"character": ""}) == "en-KE-AsiliaNeural"
    assert V.voice_for(pr, {"character": "Pip", "voice": "en-GB-RyanNeural"}) == "en-GB-RyanNeural"


@needs_ffmpeg
def test_make_voices_keeps_word_timings_and_reuses_unchanged_lines(tmp_path):
    pr, _s, a, b = _project()
    l1 = S.make_line(pr, a.id, 0.0, "One two three")
    l2 = S.make_line(pr, b.id, 1.0, "Four five")
    pr.script_lines = [l1, l2]
    synth = FakeSynth()
    r = V.make_all(pr, str(tmp_path), synth=synth)
    assert r == {"made": 2, "reused": 0, "failed": []}
    assert l1["audio"].startswith("Voice/") and l1["duration"] == pytest.approx(1.2, abs=0.15)
    assert [w["word"] for w in l1["words"]] == ["One", "two", "three"]
    # nothing changed: nothing is made again
    r = V.make_all(pr, str(tmp_path), synth=synth)
    assert r["made"] == 0 and r["reused"] == 2 and len(synth.calls) == 2
    # change one line's words: only that line is made again
    S.set_text(l2, "Four five six")
    r = V.make_all(pr, str(tmp_path), synth=synth)
    assert r["made"] == 1 and len(synth.calls) == 3
    # a different voice or speed makes a new file too
    pr.voices = {"speed": 10}
    assert V.make_all(pr, str(tmp_path), synth=synth)["made"] == 2
    assert synth.calls[-1][2] == "+10%"


@needs_ffmpeg
def test_voice_clips_in_edit_time_after_reorder_and_speed_change(tmp_path):
    pr, _s, a, b = _project()
    l1 = S.make_line(pr, a.id, 1.0, "One two three")          # moment a: 0-10 s
    l2 = S.make_line(pr, b.id, 2.0, "Four five")              # moment b: 10-20 s
    pr.script_lines = [l1, l2]
    V.make_all(pr, str(tmp_path), synth=FakeSynth())
    clips = V.voice_clips(pr, str(tmp_path))
    assert [c["line_id"] for c in clips] == [l1["id"], l2["id"]]
    assert clips[0]["start"] == pytest.approx(1.0) and clips[1]["start"] == pytest.approx(12.0)
    assert all(os.path.isabs(c["path"]) and os.path.exists(c["path"]) for c in clips)
    assert clips[0]["end"] == pytest.approx(1.0 + 1.2, abs=0.15) and clips[0]["gain"] == 1.0
    # reorder: b first
    pr.move_segment(1, 0)
    clips = V.voice_clips(pr, str(tmp_path))
    assert [c["line_id"] for c in clips] == [l2["id"], l1["id"]]
    assert clips[0]["start"] == pytest.approx(2.0) and clips[1]["start"] == pytest.approx(11.0)
    # make moment b (now first) play at 2x: it lasts 5 s, so a starts at 5 s and its line at 6 s
    pr.segments[0].speed = 2.0
    clips = V.voice_clips(pr, str(tmp_path))
    by = {c["line_id"]: c for c in clips}
    assert by[l2["id"]]["start"] == pytest.approx(1.0)          # 2 s of footage into b, at 2x = 1 s
    assert by[l1["id"]]["start"] == pytest.approx(6.0)
    # project.folder is enough when no folder is passed (what the mixer does)
    pr.folder = str(tmp_path)
    assert len(V.voice_clips(pr)) == 2


@needs_ffmpeg
def test_voice_clips_leave_out_hidden_lines_and_missing_files(tmp_path):
    pr, _s, a, b = _project()
    l1, l2 = S.make_line(pr, a.id, 0, "One two"), S.make_line(pr, b.id, 0, "Three four")
    pr.script_lines = [l1, l2]
    V.make_all(pr, str(tmp_path), synth=FakeSynth())
    pr.remove_segment(1)                                       # l2's moment is deleted
    assert [c["line_id"] for c in V.voice_clips(pr, str(tmp_path))] == [l1["id"]]
    os.unlink(os.path.join(str(tmp_path), l1["audio"]))
    assert V.voice_clips(pr, str(tmp_path)) == []
    assert V.voice_clips(pr) == []                              # no folder known: nothing


def test_no_internet_is_one_plain_message_and_stops_early(tmp_path):
    pr, _s, a, b = _project()
    pr.script_lines = [S.make_line(pr, a.id, 0, "One"), S.make_line(pr, b.id, 0, "Two")]
    calls = []

    def offline(text, voice, rate, pitch, out):
        calls.append(text)
        raise V.VoiceError("Can't reach the voice service. Voices need the internet; check your connection.")

    r = V.make_all(pr, str(tmp_path), synth=offline)
    assert len(r["failed"]) == 1 and "internet" in r["failed"][0][1] and len(calls) == 1


def test_edge_synth_turns_network_errors_into_a_plain_message(tmp_path, monkeypatch):
    import aiohttp

    from clipping import voiceover

    async def boom(*a, **k):
        raise aiohttp.ClientConnectorError(None, OSError("no route"))

    monkeypatch.setattr(voiceover, "_synthesize_async", boom)
    with pytest.raises(V.VoiceError) as e:
        V.edge_synth("hi", "en-KE-AsiliaNeural", "+0%", "+0Hz", str(tmp_path / "x.mp3"))
    assert "internet" in str(e.value)


@needs_ffmpeg
def test_recording_is_used_as_the_line_voice_with_even_words(tmp_path):
    pr, _s, a, _b = _project()
    ln = S.make_line(pr, a.id, 0, "Say this now")
    pr.script_lines = [ln]
    rec = V.recording_path(str(tmp_path), ln["id"])
    tone(rec, seconds=3.0)
    V.attach_recording(pr, ln, str(tmp_path), rec)
    assert ln["recorded"] and ln["duration"] == pytest.approx(3.0, abs=0.2)
    assert [w["word"] for w in ln["words"]] == ["Say", "this", "now"]
    assert ln["words"][1]["start"] == pytest.approx(1.0, abs=0.1)
    # a recorded line is never replaced by "Make voices"
    assert V.make_all(pr, str(tmp_path), synth=lambda *a: pytest.fail("replaced"))["reused"] == 1
    assert V.mic_command("o.mp3")[:9] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", "default"]


@needs_ffmpeg
def test_empty_recording_says_so(tmp_path):
    pr, _s, a, _b = _project()
    ln = S.make_line(pr, a.id, 0, "Say this")
    rec = V.recording_path(str(tmp_path), ln["id"])
    tone(rec, seconds=0.1)
    with pytest.raises(V.VoiceError) as e:
        V.attach_recording(pr, ln, str(tmp_path), rec)
    assert "microphone" in str(e.value)


def test_speed_up_to_fit():
    pr, _s, a, _b = _project()
    ln = S.make_line(pr, a.id, 0, "x")
    ln.update(duration=5.0)
    assert V.fit_speed(pr, ln, available=6.0) is None            # already fits
    pct = V.fit_speed(pr, ln, available=4.0)
    assert pct is not None and 25 <= pct <= 30                   # 5 s -> 4 s needs about +25 %
    assert V.fit_speed(pr, ln, available=1.0) is None            # too much: offer "Shorten with Claude" instead


def test_even_words():
    assert V.even_words("a b", 2.0) == [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "b", "start": 1.0, "end": 2.0}]
    assert V.even_words("", 2.0) == []


@pytest.mark.skipif(not os.environ.get("CLIP_EDITOR_REAL_TTS") and not shutil.which("ffprobe"), reason="needs ffprobe")
def test_real_edge_tts_if_online(tmp_path):
    """The one real network test: skipped without internet."""
    try:
        socket.create_connection(("speech.platform.bing.com", 443), timeout=4).close()
    except OSError:
        pytest.skip("no internet")
    out = str(tmp_path / "real.mp3")
    words = V.edge_synth("He thought it was a normal Tuesday.", "en-KE-ChilembaNeural", "+0%", "+0Hz", out)
    assert os.path.getsize(out) > 1000 and len(words) == 7 and words[0]["word"] == "He"
    assert words[-1]["end"] <= V.probe_duration(out) + 0.5
