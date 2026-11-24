"""Clip Editor phase 3: voices in the render, original sound per moment, script captions."""

import os
import shutil
import subprocess

import numpy as np
import pytest

from clipping.editor import captions as C
from clipping.editor import project as P
from clipping.editor import render
from clipping.editor import script as S
from clipping.editor import voice as V

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg missing")


def _tone(path, freq, seconds=1.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}:sample_rate=24000", "-f", "mp3", path], check=True)


def _project(tmp_path, mode="explain", real=False):
    video = tmp_path / "src.mp4"
    if real:   # 6 s picture with a steady 200 Hz tone as the "original sound"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
                        "-f", "lavfi", "-i", "sine=frequency=200:duration=6:sample_rate=48000", "-shortest",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    else:
        video.write_bytes(b"x")
    pr = P.Project(title="Mix", mode=mode, channel="Sports")
    src = pr.add_source(P.Source(path=str(video), name="src", duration=6.0, width=320, height=180))
    pr.add_ranges(src.id, "0:00-0:03, 0:03-0:06")
    return pr


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _clip(path, start, end, gain=1.0, line_id="ln_1"):
    return {"path": path, "start": start, "end": end, "gain": gain, "line_id": line_id}


# ---------------------------------------------------------------- the command
def test_without_voices_the_command_is_unchanged(tmp_path):
    pr = _project(tmp_path)
    g = _graph(render.build_command(pr, "o.mp4"))
    assert "concat=n=2:v=1:a=1[vcat][acat]" in g and "adelay" not in g and "amix" not in g and "volume" not in g


def test_voices_are_delayed_into_place_and_mixed(tmp_path):
    pr = _project(tmp_path)
    cmd = render.build_command(pr, "o.mp4", voices=[_clip("/v/a.mp3", 1.25, 2.25), _clip("/v/b.mp3", 4.0, 5.0, gain=0.5)])
    g = _graph(cmd)
    assert "[acat]" in g and "adelay=1250|1250" in g and "adelay=4000|4000" in g and "volume=0.5" in g
    assert "amix=inputs=3:duration=first:dropout_transition=0" in g and g.rstrip().count("[aout]") >= 1   # the sound mixer (audio_mix)
    assert cmd.count("-i") == 4 and "/v/a.mp3" in cmd and "/v/b.mp3" in cmd     # 2 moments + 2 voices
    assert cmd[cmd.index("-map") + 1] == "[vout]" and "[aout]" in cmd


def test_voice_inputs_come_after_emoji_inputs(tmp_path):
    from clipping.editor import text_layers
    pr = _project(tmp_path)
    layers = text_layers.TextLayers(ass_path="/t/a.ass", emojis=[text_layers.EmojiMark(png="/t/e.png", x=1, y=2, start=0, end=1)])
    cmd = render.build_command(pr, "o.mp4", layers=layers, voices=[_clip("/v/a.mp3", 1, 2)])
    ins = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert ins[-2:] == ["/t/e.png", "/v/a.mp3"]
    assert "[3:a]aresample" in _graph(cmd)                                       # 2 moments + 1 emoji, so the voice is input 3


def test_original_sound_per_mode_and_per_moment(tmp_path):
    pr = _project(tmp_path, mode="explain")
    assert "volume" not in _graph(render.build_command(pr, "o.mp4"))             # Explain keeps it at full
    pr.mode = "react"
    assert "volume" not in _graph(render.build_command(pr, "o.mp4"))             # React keeps it
    pr.mode = "new_story"
    g = _graph(render.build_command(pr, "o.mp4"))
    assert g.count("volume=0,") == 2 or g.count("volume=0[") + g.count("volume=0,") >= 2   # New story mutes both moments
    pr.segments[0].original_level = 0.4                                          # the slider wins over the mode
    g = _graph(render.build_command(pr, "o.mp4"))
    assert "volume=0.4" in g and render.original_level(pr, pr.segments[0]) == 0.4
    assert render.original_level(pr, pr.segments[1]) == 0.0


def test_explain_dips_the_original_under_each_voice_and_other_modes_do_not(tmp_path):
    pr = _project(tmp_path, mode="explain")
    voices = [_clip("/v/a.mp3", 1.0, 2.0), _clip("/v/b.mp3", 4.0, 5.0)]
    g = _graph(render.build_command(pr, "o.mp4", voices=voices))
    assert "[acat]volume='1-0.75*max(" in g and ":eval=frame[adip]" in g and "[adip]anull[a_main]" in g   # the dipped original goes into the mixer
    assert "t-(0.85)" in g and "(5.15)-t" in g                                   # fades of 0.15 s either side
    for mode in ("react", "new_story"):
        pr.mode = mode
        assert "adip" not in _graph(render.build_command(pr, "o.mp4", voices=voices))


def test_keep_game_sounds_remove_talking_only_when_asked_and_not_when_muted(tmp_path):
    pr = _project(tmp_path, mode="new_story")
    pr.voices = {"remove_talking": "centre"}
    for s in pr.segments:
        s.original_level = 1.0
    assert "pan=stereo|c0=c0-c1|c1=c1-c0" in _graph(render.build_command(pr, "o.mp4"))
    pr.segments[0].original_level = 0.0                                          # a muted moment needs no speech cut
    g = _graph(render.build_command(pr, "o.mp4"))
    assert g.count("pan=stereo|c0=c0-c1") == 1
    pr.voices = {"remove_talking": "band"}
    assert "bandreject" in _graph(render.build_command(pr, "o.mp4"))


def test_preview_cache_key_changes_when_a_voice_or_the_mode_changes(tmp_path):
    pr = _project(tmp_path)
    v = tmp_path / "Voice" / "a.mp3"
    _tone(str(v), 440)
    base = render.edit_fingerprint(pr, render.PREVIEW, [_clip(str(v), 1.0, 2.0)])
    assert base == render.edit_fingerprint(pr, render.PREVIEW, [_clip(str(v), 1.0, 2.0)])
    assert base != render.edit_fingerprint(pr, render.PREVIEW, [])                         # voice added/removed
    assert base != render.edit_fingerprint(pr, render.PREVIEW, [_clip(str(v), 1.5, 2.5)])  # moved
    assert base != render.edit_fingerprint(pr, render.PREVIEW, [_clip(str(v), 1.0, 2.0, gain=0.5)])
    os.utime(str(v), (1, 1))                                                               # file remade
    assert base != render.edit_fingerprint(pr, render.PREVIEW, [_clip(str(v), 1.0, 2.0)])
    pr.mode = "react"
    assert render.edit_fingerprint(pr, render.PREVIEW, []) != render.edit_fingerprint(_project(tmp_path), render.PREVIEW, [])
    pr2 = _project(tmp_path)
    pr2.segments[0].original_level = 0.2
    assert render.edit_fingerprint(pr2, render.PREVIEW, []) != render.edit_fingerprint(_project(tmp_path), render.PREVIEW, [])


# ---------------------------------------------------------------- script captions
def _words(text, step=0.4):
    return [{"word": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)} for i, w in enumerate(text.split())]


def test_script_captions_follow_the_voice_timings_and_move_with_the_line(tmp_path):
    pr = _project(tmp_path)
    ln = S.make_line(pr, pr.segments[1].id, 1.0, "He walked right into it")    # moment 2 starts at 3 s
    ln.update(duration=2.0, words=_words("He walked right into it"))
    pr.script_lines = [ln]
    assert C.use_script_captions(pr) >= 1
    caps = C.visible(pr)
    assert all(c["line_id"] == ln["id"] for c in caps)
    assert caps[0]["words"][0]["start"] == pytest.approx(4.0)                    # 3 s + 1 s offset + voice word 0
    assert caps[0]["words"][1]["start"] == pytest.approx(4.4)
    assert " ".join(c["text"] for c in caps) == "He walked right into it"
    pr.move_segment(1, 0)                                                        # moment 2 plays first
    C.refresh(pr)
    assert C.visible(pr)[0]["words"][0]["start"] == pytest.approx(1.0)
    S.nudge(pr, ln["id"], 0.5)
    C.refresh(pr)
    assert C.visible(pr)[0]["words"][0]["start"] == pytest.approx(1.5)
    pr.remove_segment(0)                                                         # its moment is deleted
    C.refresh(pr)
    assert C.visible(pr) == []


def test_script_captions_without_word_timings_spread_evenly(tmp_path):
    pr = _project(tmp_path)
    ln = S.make_line(pr, pr.segments[0].id, 0.0, "one two three four")
    ln.update(duration=2.0, words=[])
    pr.script_lines = [ln]
    C.use_script_captions(pr)
    words = [w for c in C.visible(pr) for w in c["words"]]
    assert [round(w["start"], 2) for w in words] == [0.0, 0.5, 1.0, 1.5]


def test_only_changed_lines_get_new_captions_and_speech_captions_stay(tmp_path):
    pr = _project(tmp_path)
    speech = C.make_caption([{"word": "hello", "src_start": 1.0, "src_end": 1.5}], pr.segments[0].id)
    speech["source_id"] = pr.segments[0].source_id
    l1, l2 = S.make_line(pr, pr.segments[0].id, 0.0, "first line"), S.make_line(pr, pr.segments[1].id, 0.0, "second line")
    for l in (l1, l2):
        l.update(duration=1.0, words=_words(l["text"], 0.5))
    pr.script_lines = [l1, l2]
    pr.captions = [speech]
    C.sync_script_captions(pr)
    assert speech in pr.captions                                                 # phase 2 captions untouched
    ids_before = {c["id"] for c in pr.captions if c.get("line_id") == l1["id"]}
    S.set_text(l2, "second line changed")
    l2.update(duration=1.5, words=_words("second line changed", 0.5))
    C.sync_script_captions(pr)
    assert {c["id"] for c in pr.captions if c.get("line_id") == l1["id"]} == ids_before     # l1 not remade
    assert "changed" in " ".join(c["text"] for c in pr.captions if c.get("line_id") == l2["id"])
    S.delete(pr, l1["id"])
    C.sync_script_captions(pr)
    assert not [c for c in pr.captions if c.get("line_id") == l1["id"]]


def test_srt_matches_what_is_said(tmp_path):
    pr = _project(tmp_path)
    ln = S.make_line(pr, pr.segments[0].id, 0.0, "Say these words aloud")
    ln.update(duration=1.6, words=_words("Say these words aloud"))
    pr.script_lines = [ln]
    C.use_script_captions(pr)
    assert " ".join(l["text"] for l in C.srt_lines(pr)) == "Say these words aloud"


# ---------------------------------------------------------------- real renders
def _decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0


def _level(x, t0, t1, freq, sr=16000):
    """How strong ``freq`` Hz is in x between t0 and t1 seconds."""
    seg = x[int(t0 * sr):int(t1 * sr)]
    if len(seg) == 0:
        return 0.0
    t = np.arange(len(seg)) / sr
    return 2 * abs(np.sum(seg * np.exp(-2j * np.pi * freq * t))) / len(seg)


@needs_ffmpeg
def test_end_to_end_two_voice_lines_land_at_the_right_times(tmp_path):
    pr = _project(tmp_path, mode="explain", real=True)
    folder = tmp_path / "edit"
    pr.folder = str(folder)
    l1 = S.make_line(pr, pr.segments[0].id, 1.0, "Line one")       # 1 s into the cut
    l2 = S.make_line(pr, pr.segments[1].id, 0.5, "Line two")       # 3.5 s into the cut
    for ln, f in ((l1, 440), (l2, 880)):
        _tone(str(folder / "Voice" / f"{f}.mp3"), f, 1.0)
        ln.update(audio=f"Voice/{f}.mp3", duration=1.0, words=_words(ln["text"], 0.5))
    pr.script_lines = [l1, l2]
    S.refresh(pr)
    out = tmp_path / "out.mp4"
    render.run(render.build_command(pr, str(out), render.PREVIEW, voices=V.voice_clips(pr)), pr.total_length)
    assert abs(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                    capture_output=True, text=True).stdout) - 6.0) < 0.2         # no freezing or stretching
    x = _decode(str(out))
    # the 440 Hz voice is at 1.0-2.0 s, the 880 Hz voice at 3.5-4.5 s, and not elsewhere
    assert _level(x, 1.2, 1.9, 440) > 0.05 and _level(x, 0.1, 0.8, 440) < 0.02 and _level(x, 2.3, 3.2, 440) < 0.02
    assert _level(x, 3.7, 4.4, 880) > 0.05 and _level(x, 1.2, 1.9, 880) < 0.02 and _level(x, 4.7, 5.8, 880) < 0.02
    # Explain mode: the original 200 Hz tone dips to about a quarter under a voice, and is full elsewhere
    full, dipped = _level(x, 0.2, 0.7, 200), _level(x, 1.3, 1.8, 200)
    assert full > 0.05 and dipped < full * 0.45


@needs_ffmpeg
def test_end_to_end_new_story_mutes_the_original_and_react_keeps_it(tmp_path):
    for mode, expect_loud in (("new_story", False), ("react", True)):
        pr = _project(tmp_path, mode=mode, real=True)
        pr.folder = str(tmp_path / "edit")
        out = tmp_path / f"{mode}.mp4"
        render.run(render.build_command(pr, str(out), render.PREVIEW, voices=[]), pr.total_length)
        x = _decode(str(out))
        assert bool(_level(x, 0.5, 2.5, 200) > 0.05) == expect_loud


@needs_ffmpeg
def test_end_to_end_export_uses_the_voices_of_the_project(tmp_path):
    pr = _project(tmp_path, mode="react", real=True)
    folder = tmp_path / "edit"
    pr.folder = str(folder)
    ln = S.make_line(pr, pr.segments[0].id, 1.0, "Hello there")
    _tone(str(folder / "Voice" / "v.mp3"), 660, 1.0)
    ln.update(audio="Voice/v.mp3", duration=1.0, words=_words("Hello there", 0.5))
    pr.script_lines = [ln]
    rec = render.export(pr, str(tmp_path / "Ready"), fonts={})   # {} = use system fonts, no downloads
    x = _decode(rec["path"])
    assert _level(x, 1.2, 1.9, 660) > 0.05 and _level(x, 3.0, 5.0, 660) < 0.02


@needs_ffmpeg
def test_original_level_slider_scales_the_sound(tmp_path):
    pr = _project(tmp_path, mode="react", real=True)
    pr.segments[1].original_level = 0.25
    out = tmp_path / "lvl.mp4"
    render.run(render.build_command(pr, str(out), render.PREVIEW), pr.total_length)
    x = _decode(str(out))
    loud, quiet = _level(x, 0.5, 2.5, 200), _level(x, 3.5, 5.5, 200)
    assert 0.15 < quiet / loud < 0.4


@needs_ffmpeg
def test_explain_dip_works_with_many_voice_lines(tmp_path):
    """ffmpeg's max() takes two arguments: a real script has far more than two lines."""
    expr = render._dip_expression([_clip("/v/x.mp3", i * 0.5, i * 0.5 + 0.3, line_id=f"l{i}") for i in range(9)])
    assert expr.count("max(") >= 8 and "," in expr
    pr = _project(tmp_path, mode="explain", real=True)
    folder = tmp_path / "edit"
    voices = []
    for i, start in enumerate((0.3, 1.2, 2.1, 3.0, 3.9, 4.8)):
        f = str(folder / "Voice" / f"{i}.mp3")
        _tone(f, 440 + 100 * i, 0.5)
        voices.append(_clip(f, start, start + 0.5, line_id=f"l{i}"))
    out = tmp_path / "many.mp4"
    render.run(render.build_command(pr, str(out), render.PREVIEW, voices=voices), pr.total_length)
    x = _decode(str(out))
    assert _level(x, 3.1, 3.4, 740) > 0.05                      # the fourth voice (740 Hz) is there
    full, dipped = _level(x, 0.95, 1.05, 200), _level(x, 1.3, 1.6, 200)
    assert full > 0.05 and dipped < full * 0.5                  # back to full between voices, quiet under one
