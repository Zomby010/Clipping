"""Fixes from the phase 3 hands-on test (November 2026)."""

import pytest

from clipping.editor import project as P
from clipping.editor import publish, render, styles, text_layers
from test_editor_phase2_fixes import app  # noqa: F401  (window fixture)


def test_emoji_only_hook_moves_and_takes_the_box(tmp_path):
    from PIL import Image
    font = styles.emoji_font()
    if not font:
        pytest.skip("no colour emoji font")
    pr = P.Project()
    pr.hook = {"text": "\U0001F525", "seconds": 2.0, "anim": "shake", "box": True, "box_color": "1971C2"}
    marks, _ = text_layers.emoji_marks(pr, 540, 960, str(tmp_path), font)
    assert marks[0].anim == "shake"
    with Image.open(marks[0].png) as im:
        assert im.convert("RGB").getpixel((im.width // 2, 1)) == (0x19, 0x71, 0xC2)
    for anim, _name in text_layers.HOOK_ANIMATIONS:
        assert text_layers.emoji_motion(anim, 10, 20) != ("10", "20")
    assert text_layers.emoji_motion("", 10, 20) == ("10", "20")


def test_box_colour_in_the_subtitles():
    pr = P.Project()
    pr.hook = {"text": "Wait", "box": True, "box_color": "FFE600"}
    line = next(l for l in text_layers.build_ass(pr, 1080, 1920).splitlines() if l.startswith("Style: HookBox"))
    assert "&H0000E6FF" in line and "&H00000000" in line   # yellow box, black words
    assert text_layers.box_colour({"box_color": "nonsense"}) == text_layers.BOX_COLOUR


def test_timeline_cover_and_hook_in_the_window(app):  # noqa: F811
    seg = app.project.add_segment(app.src.id, 2, 6)
    app.commit(keep_selection=seg.id)
    app.root.update()
    assert app.timeline.find_withtag("original")
    app.preview_path = app.src.path                 # stands in for the built preview
    app.move_cover(0.5)
    assert app.project.cover["time"] == pytest.approx(1.5)
    assert app._cover_image is not None and publish.cover_time(app.project) == pytest.approx(1.5)
    app.hook_var.set("Wait")
    app.hook_colour = "1971C2"
    app.hook_box.set(True)
    app._hook_changed()
    assert app.project.hook["box_color"] == "1971C2"


def test_line_voice_and_volume(tmp_path):
    from clipping.editor import audio_mix, voice as V
    line = {"text": "hi", "voice": "", "audio": "Voice/mic-1.mp3", "duration": 2.0, "words": [], "recorded": True}
    assert V.set_line_voice(line, V.VOICES[1][0])
    assert V.voice_for(P.Project(), line) == V.VOICES[1][0]
    assert line["audio"] == "" and "recorded" not in line
    assert not V.set_line_voice(line, V.VOICES[1][0])
    assert V.set_line_voice(line, "") and V.voice_for(P.Project(), line) != ""

    pr = P.Project()
    assert V.settings(pr)["volume"] == 1.0          # older edits sound as before
    voice = {"path": "v.mp3", "start": 0.0, "end": 1.0, "gain": 1.5, "line_id": "a"}
    loud = ";".join(audio_mix.build(pr, 2.0, 1, voices=[voice])[1])
    assert "volume=1," in loud and "[a_main_loud]volume=" in loud    # voice kept clean, the rest turned down
    quiet = ";".join(audio_mix.build(pr, 2.0, 1, voices=[{**voice, "gain": 0.5}])[1])
    assert "volume=0.5," in quiet and "a_main_loud" not in quiet


def test_voice_controls_in_the_window(app, tmp_path):  # noqa: F811
    from clipping.editor import script as S, voice as V
    seg = app.project.add_segment(app.src.id, 2, 6)
    line = S.make_line(app.project, seg.id, 0.0, "hello there")
    line["id"] = "line_a"
    app.project.script_lines.append(line)
    S.refresh(app.project)
    app.commit(keep_selection=seg.id)
    app.voice_tree.selection_set("v_line_a")
    app.root.update()
    app.line_voice_var.set(V.VOICES[2][1])
    app._line_voice_changed()
    app.root.update()
    assert S.find(app.project, "line_a")["voice"] == V.VOICES[2][0]
    assert app.line_voice_var.get() == V.VOICES[2][1]     # still shown after the redraw
    app.volume_var.set(60)
    app._volume_changed()
    assert V.settings(app.project)["volume"] == 0.6
    path = app.ensure_folder() + "/a.mp3"
    open(path, "wb").close()
    S.find(app.project, "line_a").update(audio=path, duration=1.0)
    assert V.voice_clips(app.project, app.folder)[0]["gain"] == 0.6


def test_my_cut_says_when_lines_have_no_voice():
    from clipping.editor.gui.app import EditorApp
    pr = P.Project(mode="new_story")
    assert "Changes update" in EditorApp._cut_ready_note(pr)
    pr.script_lines.append({"text": "hi", "visible": True, "audio": ""})
    note = EditorApp._cut_ready_note(pr)
    assert "Make voices" in note and "silent" in note


def test_new_words_drop_the_old_mic_take():
    from clipping.editor import script as S
    line = {"text": "old words", "audio": "Voice/mic-1.mp3", "duration": 2.0, "words": [], "recorded": True}
    S.set_text(line, "old words")
    assert line["recorded"]                       # nothing changed: the take stays
    S.set_text(line, "new words")
    assert not line.get("recorded") and line["audio"] == ""
