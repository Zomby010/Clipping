"""Reactions inserted into the cut, and the fixes made with them (November 2026)."""

import re
import subprocess

import pytest

from clipping.editor import media, overlays, publish, reactions, render, review, script as S, text_layers, voice
from clipping.editor import project as P


def _ff(path, *args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return str(path)


@pytest.fixture
def files(tmp_path):
    from PIL import Image
    src = _ff(tmp_path / "src.mp4", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=10", "-f", "lavfi",
              "-i", "sine=f=300:d=10", "-shortest", "-pix_fmt", "yuv420p")
    still = str(tmp_path / "still.jpg")
    Image.new("RGB", (301, 533), (0, 255, 0)).save(still)
    gif = str(tmp_path / "anim.gif")
    Image.new("RGB", (101, 77), (255, 0, 0)).save(gif, save_all=True, duration=200, loop=0,
                                                  append_images=[Image.new("RGB", (101, 77), (250, 40, 0))])
    clip = _ff(tmp_path / "clip.mp4", "-f", "lavfi", "-i", "color=c=white:s=180x320:r=30:d=1", "-pix_fmt", "yuv420p")
    return {"src": src, "still": still, "gif": gif, "clip": clip}


def _project(files, ranges="0:00-0:06"):
    pr = P.Project(title="r", channel="GTA 6")
    src = pr.add_source(media.probe(files["src"]))
    pr.add_ranges(src.id, ranges)
    for s in pr.segments:
        s.framing.mode = "crop"
    return pr


def test_insert_adds_time_and_remove_joins_the_moment_again(files):
    pr = _project(files)
    line = S.make_line(pr, pr.segments[0].id, 4.0, "after the reaction")
    pr.script_lines.append(line)
    S.refresh(pr)
    seg = reactions.insert(pr, files["still"], 2.0, 1.5)
    assert [s.reaction for s in pr.segments] == [False, True, False]
    assert pr.total_length == pytest.approx(7.5)
    S.refresh(pr)
    assert pr.script_lines[0]["start"] == pytest.approx(5.5)      # the voice moved along with its picture
    assert [s.kind for s in pr.sources] == ["", "reaction"] and len(pr.footage()) == 1
    reactions.remove(pr, seg.id)
    assert len(pr.segments) == 1 and pr.total_length == pytest.approx(6.0)
    S.refresh(pr)
    assert pr.script_lines[0]["visible"] and pr.script_lines[0]["start"] == pytest.approx(4.0)


def test_insert_between_moments_does_not_split(files):
    pr = _project(files, "0:00-0:02, 0:03-0:05")
    reactions.insert(pr, files["gif"], 0.0, 1.0)
    reactions.insert(pr, files["gif"], 3.1, 1.0)        # where moment 1 now ends
    reactions.insert(pr, files["gif"], 99, 1.0)
    assert [s.reaction for s in pr.segments] == [True, False, True, False, True]


def test_move_a_reaction(files):
    pr = _project(files)
    seg = reactions.insert(pr, files["still"], 2.0, 1.0)
    moved = reactions.move(pr, seg.id, 6.0)             # 6.0 on screen = 5.0 once it is taken out
    assert reactions.inserted(pr)[0][1] == pytest.approx(5.0) and moved.length == 1.0
    assert len(pr.segments) == 3


@pytest.mark.parametrize("kind", ["still", "gif", "clip"])
def test_inserted_reaction_renders_in_the_video(files, tmp_path, kind):
    pr = _project(files)
    reactions.insert(pr, files[kind], 2.0, 2.0)         # the 1 s clip repeats to fill 2 s
    out = render.make_preview(pr, str(tmp_path / "edit"))
    assert media.probe(out).duration == pytest.approx(8.0, abs=0.2)

    def pixel(t):
        png = str(tmp_path / f"f{t}.png")
        media.still_frame(out, t, png, height=320)
        from PIL import Image
        with Image.open(png) as im:
            return im.convert("RGB").getpixel((im.width // 2, im.height // 2))

    assert pixel(1.0)[2] > 200 and pixel(6.0)[2] > 200          # your blue video before and after
    mid = pixel(3.5)
    assert not (mid[2] > 200 and mid[0] < 80 and mid[1] < 80)   # the reaction in between


def _level(path, start, length, before=""):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(length), "-i", path, "-vn",
                          "-af", f"{before},volumedetect" if before else "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+)", out).group(1))


def _with_voice(files, tmp_path, volume, level):
    pr = _project(files)
    pr.segments[0].original_level = level
    pr.voices = {"volume": volume}
    folder = tmp_path / f"v{volume}-{level}"
    (folder / "Voice").mkdir(parents=True)
    _ff(folder / "Voice" / "a.mp3", "-f", "lavfi", "-i", "sine=f=900:d=2", "-af", "volume=6")
    line = S.make_line(pr, pr.segments[0].id, 1.0, "words")
    line.update(audio="Voice/a.mp3", duration=2.0)
    pr.script_lines.append(line)
    pr.folder = str(folder)
    return render.make_preview(pr, str(folder))


@pytest.mark.parametrize("level", [0.0, 1.0])
def test_voice_volume_is_audible(files, tmp_path, level):
    """Compare the voice's stretch with a stretch without it, or with the 100 % render."""
    full = _with_voice(files, tmp_path, 1.0, level)
    low = _with_voice(files, tmp_path, 0.4, level)
    if level == 0.0:        # the voice alone
        assert _level(full, 1.2, 1.5) - _level(low, 1.2, 1.5) > 5
        return
    high = _with_voice(files, tmp_path, 2.0, level)

    def over_rest(path):    # how far the voice stands above the original sound
        return _level(path, 1.2, 1.5) - _level(path, 4.0, 1.5)

    assert over_rest(full) - over_rest(low) > 3
    def original_under_voice(path):     # only the 300 Hz original sound, while the voice speaks
        return _level(path, 1.4, 1.2, "bandpass=f=300:width_type=h:w=40,bandpass=f=300:width_type=h:w=40")

    assert original_under_voice(full) - original_under_voice(high) > 3     # 200 %: the rest makes way


def test_replace_the_original_sound_keeps_reactions_audible(files):
    pr = _project(files)
    reactions.insert(pr, files["still"], 2.0, 1.0, volume=0.7)
    pr.voices = {"mute_original": True}
    assert [render.original_level(pr, s) for s in pr.segments] == [0.0, 0.7, 0.0]


def test_suggested_cut_keeps_reactions(files):
    pr = _project(files)
    reactions.insert(pr, files["still"], 2.0, 1.0)
    first, last = pr.segments[0], pr.segments[2]
    review.apply_cut(pr, [{"segment_id": first.id, "keep_from": 0.5, "keep_to": 1.5},
                          {"segment_id": last.id, "keep_from": 3.0, "keep_to": 4.0}])
    assert [s.reaction for s in pr.segments] == [False, True, False]


def test_claude_wrapper_is_removed_from_lines():
    raw = '{"text": "Sir, tanks are not covered. Ever."}'
    assert S.unwrap_text(raw) == "Sir, tanks are not covered. Ever."
    assert S.unwrap_text('{"text": "cut short') == "cut short"
    assert S.unwrap_text("plain {words}") == "plain {words}"
    pr = P.Project()
    pr.script_lines = [{"id": "a", "text": raw, "audio": "x.mp3", "duration": 1.0, "words": []}]
    assert S.repair(pr) == 1 and pr.script_lines[0]["audio"] == "" and S.repair(pr) == 0


def test_silent_mic_take_is_refused_and_a_spoken_one_used(tmp_path):
    folder = tmp_path / "edit"
    (folder / "Voice").mkdir(parents=True)
    line = {"id": "a", "text": "one two", "audio": "", "duration": 0.0, "words": []}
    click = _ff(folder / "Voice" / "mic-a.mp3", "-f", "lavfi", "-i", "sine=f=300:d=3", "-af",
                "volume=if(lt(t\\,0.3)\\,4\\,0.001):eval=frame")
    with pytest.raises(voice.VoiceError, match="almost no sound"):
        voice.attach_recording(P.Project(), line, str(folder), click)
    assert not line["audio"]
    good = _ff(folder / "Voice" / "mic-a.mp3", "-f", "lavfi", "-i", "sine=f=300:d=3", "-af", "volume=0.5")
    voice.attach_recording(P.Project(), line, str(folder), good)
    assert line["recorded"] and line["duration"] == pytest.approx(3.0, abs=0.2)


def test_long_hook_and_own_cover_picture(files, tmp_path):
    assert text_layers.hook_seconds({"seconds": 45}) == 45
    pr = _project(files)
    pr.cover = {"image": files["still"]}
    out = publish.make_cover(files["src"], str(tmp_path / "cover.jpg"), 1.0,
                             picture=publish.cover_picture_path(pr))
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (1080, 1920) and im.getpixel((500, 900))[1] > 200
    pr.cover["image"] = str(tmp_path / "gone.jpg")
    assert publish.cover_picture_path(pr) is None


def test_pictures_and_gifs_are_listed_as_reactions(files, tmp_path):
    mood = tmp_path / "lib" / "happy"
    mood.mkdir(parents=True)
    for name in ("a.jpg", "b.gif", "c.mp4", "d.heic", "e.txt"):
        (mood / name).write_bytes(b"x")
    assert [p.rsplit("/", 1)[1] for p in overlays.reaction_library(str(tmp_path / "lib"))["happy"]] == [
        "a.jpg", "b.gif", "c.mp4"]
    assert reactions.kind_of(files["still"]) == "image" and reactions.kind_of(files["gif"]) == "gif"
    assert reactions.thumbnail(files["gif"], cache_dir=str(tmp_path / "thumbs"))
