"""Clip Editor phase 4: music, ducking, sound effects, loudness, reactions, arrows, circles, logo."""

import os
import shutil
import subprocess
from datetime import datetime

import pytest

from clipping.editor import audio_mix as A
from clipping.editor import captions as C
from clipping.editor import media, publish, render
from clipping.editor import overlays as O
from clipping.editor import project as P

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="ffmpeg not installed")


def _project(tmp_path, channel="Sports"):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title="Sound test", channel=channel)
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080))
    pr.add_ranges(src.id, "1:00-1:04, 2:00-2:06")   # 4 s + 6 s
    return pr, src


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    return str(p)


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


# ------------------------------------------------------------------ anchoring
def test_sfx_and_reactions_follow_the_cut(tmp_path):
    pr, src = _project(tmp_path)
    fx = O.add_sfx(pr, _file(tmp_path, "whoosh.wav"), 5.0)        # 1 s into moment 2 (src 121)
    re = O.add_reaction(pr, _file(tmp_path, "laugh.mp4"), 1.0, "corner", 1.2)   # src 61
    assert (fx["src_time"], fx["start"]) == (121.0, 5.0)
    assert (re["start"], re["end"]) == (1.0, 2.2)

    pr.move_segment(1, 0)                                         # reorder
    O.refresh(pr)
    assert fx["start"] == 1.0 and re["start"] == 7.0

    pr.segments[0].speed = 2.0                                    # 2x moment (now 3 s long)
    O.refresh(pr)
    assert fx["start"] == 0.5 and re["start"] == 4.0

    pr.nudge(1, "in", 1.5)                                        # trim the reaction's moment past it
    O.refresh(pr)
    assert re["visible"] is False and fx["visible"] is True

    pr.remove_segment(0)                                          # delete the sound's moment
    O.refresh(pr)
    assert fx["visible"] is False
    assert O.visible_sfx(pr) == []


def test_items_move_to_the_other_half_after_a_split(tmp_path):
    pr, src = _project(tmp_path)
    fx = O.add_sfx(pr, _file(tmp_path, "boom.wav"), 7.0)          # src 123
    pr.split_segment(1, 122.0)
    pr.move_segment(2, 0)                                         # second half goes first
    O.refresh(pr)
    assert fx["segment_id"] == pr.segments[0].id and fx["start"] == 1.0


def test_nudge_and_delete(tmp_path):
    pr, _src = _project(tmp_path)
    pr.segments[1].speed = 2.0
    fx = O.add_sfx(pr, _file(tmp_path, "ding.wav"), 5.0)          # src 122 at 2x
    O.nudge(pr, fx["id"], 0.1)
    assert fx["src_time"] == 122.2 and fx["start"] == pytest.approx(5.1)
    O.delete(pr, fx["id"])
    assert O.sfx_items(pr) == []


def test_adding_outside_the_cut_fails(tmp_path):
    pr, _src = _project(tmp_path)
    pr.segments.clear()
    with pytest.raises(ValueError):
        O.add_sfx(pr, _file(tmp_path, "a.wav"), 1.0)


# ------------------------------------------------------------------ ducking
def test_speech_times_merge_voices_and_captions(tmp_path):
    pr, _src = _project(tmp_path)
    pr.captions = C.captions_from_words(pr, pr.segments[0], [
        {"word": "hi", "src_start": 60.5, "src_end": 61.0},
        {"word": "you", "src_start": 61.1, "src_end": 61.4}])
    C.refresh(pr)
    voices = [{"path": "v.mp3", "start": 1.6, "end": 2.5, "gain": 1.0}, {"path": "v2.mp3", "start": 6, "end": 7}]
    spans = A.speech_times(pr, voices)
    assert spans == [(0.5, 2.5), (6.0, 7.0)]   # close spans join into one


def test_duck_envelope():
    spans = [(2.0, 4.0)]
    assert A.music_gain_at(spans, 0.3, 0.0) == 1.0
    assert A.music_gain_at(spans, 0.3, 3.0) == pytest.approx(0.3)
    assert A.music_gain_at(spans, 0.3, 4.2) == pytest.approx(0.65)  # halfway through the release
    assert A.music_gain_at(spans, 0.3, 5.0) == 1.0
    assert A.duck_expression([], 0.3) == "1"
    expr = A.duck_expression([(1, 2), (5, 6)], 0.5)
    assert expr.startswith("1-0.5*max(") and "clip((t-0.9)/0.1,0,1)" in expr


# ------------------------------------------------------------------ the audio graph
def test_audio_graph_music_sfx_voices_and_loudness(tmp_path, monkeypatch):
    pr, _src = _project(tmp_path)
    voice = _file(tmp_path, "line1.mp3")
    monkeypatch.setattr(A, "voice_clips", lambda project: [
        {"path": voice, "start": 1.0, "end": 3.0, "gain": 1.0, "line_id": "ln_1"}])
    A.set_music(pr, _file(tmp_path, "song.mp3"), "epic", volume=0.2, start_at=12, fade_in=1, fade_out=2)
    O.add_sfx(pr, _file(tmp_path, "whoosh.wav"), 4.25, gain=0.7)
    cmd = render.build_command(pr, "out.mp4")
    g = _graph(cmd)
    # inputs: 2 moments, the voice, the looping music (from 12 s), the sound effect
    assert cmd.count("-i") == 5
    i_music = cmd.index(str(tmp_path / "song.mp3"))
    assert cmd[i_music - 7:i_music] == ["-stream_loop", "-1", "-ss", "12", "-t", "10.5", "-i"]
    assert "[2:a]" in g and "adelay=1000|1000" in g                      # voice at 1 s
    assert "afade=t=in:st=0:d=1" in g and "afade=t=out:st=8:d=2" in g     # fades fit the 10 s cut
    assert "volume=0.2" in g and "volume='1-0.7*" in g and ":eval=frame" in g   # ducked under the voice
    assert "[4:a]" in g and "volume=0.7,adelay=4250|4250" in g           # sound effect at 4.25 s
    assert "amix=inputs=4:duration=first:dropout_transition=0,volume=4" in g
    assert g.endswith("loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")


def test_music_without_speech_ducks_on_the_original_sound(tmp_path):
    pr, _src = _project(tmp_path)
    A.set_music(pr, _file(tmp_path, "song.mp3"), "chill", loop=False, ducking="light")
    pr.audio["loudness"] = None
    cmd = render.build_command(pr, "out.mp4")
    g = _graph(cmd)
    assert "-stream_loop" not in cmd
    assert "asplit=2[a_main][a_key]" in g and "sidechaincompress" in g and "ratio=4" in g
    assert "loudnorm" not in g


def test_no_music_and_loudness_off_keeps_the_plain_sound(tmp_path):
    pr, _src = _project(tmp_path)
    pr.audio["loudness"] = None
    g = _graph(render.build_command(pr, "out.mp4"))
    assert g.endswith("[acat]anull[aout]")


# ------------------------------------------------------------------ pictures inside the safe area
@pytest.mark.parametrize("w,h", [(540, 960), (1080, 1920)])
def test_corner_marks_and_logo_stay_clear_of_buttons(w, h):
    left, top, right, bottom = O.safe_box(w, h)
    for pos, _label in O.CORNER_SPOTS:
        x, y, bw, bh = O.corner_rect(pos, w, h)
        assert left <= x and x + bw <= right and top <= y and y + bh <= bottom, pos
    for corner, _label in O.LOGO_CORNERS:
        x, y, lw, lh = O.logo_rect({"corner": corner, "size": 0.3}, w, h, (400, 200))
        assert left <= x and x + lw <= right and top <= y and y + lh <= bottom
    side = int(0.22 * w)
    for fx in (0.24, 0.46, 0.68):
        for fy in (0.18, 0.42, 0.64):
            x, y = O.mark_xy({"x": fx, "y": fy}, w, h, side)
            assert 0 <= x <= w - side and top <= y and y + side <= bottom


def test_arrow_and_circle_pictures(tmp_path):
    from PIL import Image
    for item in ({"type": "arrow", "direction": "up-right", "colour": "yellow", "size": 0.2},
                 {"type": "circle", "colour": "red", "size": 0.3}):
        png, side, _ = O.mark_png(item, 1080, str(tmp_path / f"{item['type']}.png"))
        with Image.open(png) as im:
            assert im.size == (side, side) and im.mode == "RGBA"
            assert im.getbbox() is not None


# ------------------------------------------------------------------ preview cache key
def test_preview_key_changes_with_every_layer(tmp_path):
    pr, _src = _project(tmp_path)
    keys = {render.edit_fingerprint(pr, render.PREVIEW)}
    A.set_music(pr, _file(tmp_path, "m.mp3"), "epic")
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    pr.audio["music"]["volume"] = 0.3
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    O.add_sfx(pr, _file(tmp_path, "s.wav"), 1.0)
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    O.add_reaction(pr, _file(tmp_path, "r.mp4"), 2.0)
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    O.add_mark(pr, "arrow", 3.0)
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    pr.logo = {"path": _file(tmp_path, "logo.png")}
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    pr.audio["loudness"] = None
    keys.add(render.edit_fingerprint(pr, render.PREVIEW))
    assert len(keys) == 8


# ------------------------------------------------------------------ files
def test_old_edits_open_and_keep_everything(tmp_path):
    pr, _src = _project(tmp_path)
    old = pr.to_dict()
    old.pop("logo")
    old["audio"] = {}
    loaded = P.Project.from_dict(old)
    assert loaded.logo == {} and loaded.audio == {}
    O.add_sfx(loaded, _file(tmp_path, "x.wav"), 1.0)
    loaded.logo = {"path": "/x/logo.png", "corner": "top-left"}
    P.save(loaded, str(tmp_path / "edit"))
    again, _ = P.load(str(tmp_path / "edit"))
    assert again.logo == loaded.logo and again.audio["sfx"][0]["src_time"] == 61.0


def test_music_licence_and_credit(tmp_path):
    bgm = tmp_path / "bgm"
    (bgm / "epic").mkdir(parents=True)
    track = bgm / "epic" / "Big Win.mp3"
    track.write_bytes(b"x")
    A.save_licence(str(track), "Pixabay licence, by Someone", bgm_dir=str(bgm))
    assert A.licence_for(str(track), bgm_dir=str(bgm)) == "Pixabay licence, by Someone"
    assert A.music_library(str(bgm))["epic"] == [str(track)]
    assert set(A.MOODS) <= set(A.music_library(str(bgm)))
    pr, _src = _project(tmp_path)
    A.set_music(pr, str(track), "epic")
    pr.audio["music"]["licence"] = "Pixabay licence, by Someone"
    assert A.music_credit(pr) == "Music: Big Win (Pixabay licence, by Someone)"
    assert "Music: Big Win (Pixabay licence, by Someone)" in publish.upload_text(pr)


def test_starter_sound_effects_are_there():
    lib = O.sfx_library()
    for kind in ("whoosh", "boom", "ding", "pop", "riser", "record-stop", "drum-hit", "error", "camera", "cash"):
        assert lib.get(kind), kind
    assert os.path.exists(os.path.join(O.SFX_DIR, "LICENCE.txt"))


# ------------------------------------------------------------------ real renders
def _make(path, args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return str(path)


def _frame(video, t, out):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=54:96", "-f", "rawvideo", "-pix_fmt", "rgb24", str(out)],
                   check=True)
    return open(out, "rb").read()


def _diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


@pytest.fixture
def media_files(tmp_path):
    src = _make(tmp_path / "src.mp4", ["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=10", "-f", "lavfi",
                                       "-i", "sine=f=300:d=10", "-shortest", "-c:v", "libx264", "-preset",
                                       "ultrafast", "-c:a", "aac"])
    react = _make(tmp_path / "react.mp4", ["-f", "lavfi", "-i", "color=c=green:s=360x640:r=30:d=3", "-f", "lavfi",
                                           "-i", "sine=f=900:d=3", "-shortest", "-c:v", "libx264", "-preset",
                                           "ultrafast", "-c:a", "aac"])
    music = _make(tmp_path / "music.mp3", ["-f", "lavfi", "-i", "sine=f=200:d=4"])
    from PIL import Image
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 128, 255)).save(logo)
    return {"src": src, "react": react, "music": music, "logo": str(logo)}


def _media_project(files):
    pr = P.Project(title="Phase four", channel="GTA 6")
    src = pr.add_source(media.probe(files["src"]))
    pr.add_ranges(src.id, "0:01-0:04, 0:06-0:09")
    for s in pr.segments:
        s.framing.mode = "crop"
    return pr


@needs_ffmpeg
def test_cutaway_keeps_the_main_video_moving(tmp_path, media_files):
    plain = _media_project(media_files)
    with_react = _media_project(media_files)
    O.add_reaction(with_react, media_files["react"], 1.0, "cutaway", 1.0)
    a = render.make_preview(plain, str(tmp_path / "a"))
    b = render.make_preview(with_react, str(tmp_path / "b"))
    # During the cut-away the picture is the reaction (green) ...
    assert _diff(_frame(a, 1.5, tmp_path / "a1"), _frame(b, 1.5, tmp_path / "b1")) > 40
    # ... and straight after it the main video is where it would have been anyway,
    # i.e. it kept playing underneath and never froze.
    after_a, after_b = _frame(a, 2.3, tmp_path / "a2"), _frame(b, 2.3, tmp_path / "b2")
    before = _frame(b, 0.9, tmp_path / "b0")
    assert _diff(after_a, after_b) < 6
    assert _diff(before, after_b) > 6


@needs_ffmpeg
def test_export_with_everything_is_platform_loud(tmp_path, media_files):
    pr = _media_project(media_files)
    A.set_music(pr, media_files["music"], "epic")
    O.add_sfx(pr, os.path.join(O.SFX_DIR, "whoosh", "whoosh-1.wav"), 2.0)
    O.add_reaction(pr, media_files["react"], 1.0, "cutaway", 1.0)
    O.add_reaction(pr, media_files["react"], 3.5, "corner", 1.5, "top-right")
    O.add_mark(pr, "arrow", 4.0, direction="down-left")
    O.add_mark(pr, "circle", 4.5, colour="yellow")
    pr.logo = {"path": media_files["logo"], "corner": "top-left", "size": 0.16, "opacity": 0.85}
    rec = render.export(pr, str(tmp_path / "Ready"), now=datetime(2026, 11, 22, 21, 0), fonts={})
    assert os.path.exists(rec["path"])
    assert rec["loudness"] is not None and abs(rec["loudness"] - A.LOUDNESS) < 1.5
    got = media.probe(rec["path"])
    assert (got.width, got.height) == (1080, 1920) and got.has_audio
    assert abs(got.duration - 6.0) < 0.2
