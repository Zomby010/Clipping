"""The Reactions tab, layout and the buttons fixed in November 2026, in the window."""

import os

import pytest

from clipping.editor import overlays as O, script as S, voice as V
from test_editor_phase2_fixes import app  # noqa: F401  (window fixture)


def _settle(app, seconds=40):
    import time
    end = time.time() + seconds
    time.sleep(0.05)
    app.root.update()
    while (app.busy or getattr(app, "_preview_after", None)) and time.time() < end:
        app.root.update()
        time.sleep(0.02)
    for _ in range(5):
        app.root.update()


@pytest.fixture
def library(tmp_path, monkeypatch):
    from PIL import Image
    folder = tmp_path / "reactions" / "laughing"
    folder.mkdir(parents=True)
    for n in range(14):
        Image.new("RGB", (120, 160), (n * 15, 200, 40)).save(folder / f"{n:02d}.jpg")
    monkeypatch.setattr(O, "REACTIONS_DIR", str(tmp_path / "reactions"))
    real = O.reaction_library
    monkeypatch.setattr(O, "reaction_library", lambda d=None: real(str(tmp_path / "reactions")))
    monkeypatch.setattr("clipping.editor.reactions.THUMB_DIR", str(tmp_path / "thumbs"))
    return folder


def test_pick_favourite_insert_and_delete(library, app):  # noqa: F811
    seg = app.project.add_segment(app.src.id, 1, 7)
    app.commit(keep_selection=seg.id)
    app.react_mood.set("laughing")
    app._fill_reactions()
    assert len(app.react_grid.winfo_children()) == 12 and "Page 1 of 2" in app.react_page_text.get()
    app._turn_page(1)
    assert len(app.react_grid.winfo_children()) == 2
    path = app._react_files[13]
    app.pick_reaction(path)
    app.toggle_favourite()
    app.react_view.set("fav")
    app._fill_reactions()
    assert app._react_files == [path] and app.settings["favourite_reactions"] == ["laughing/13.jpg"]

    app.add_reaction_here()                       # watching the original: switches to My cut first
    assert app.watch.get() == "cut" and len(app.project.segments) == 1
    _settle(app)
    app.player.seek(3.0)
    app.react_secs.set("1.5")
    app.add_reaction_here()
    assert [s.reaction for s in app.project.segments] == [False, True, False]
    assert app.project.total_length == pytest.approx(7.5)
    assert len(app.sources_list.get(0, "end")) == 1          # the reaction file isn't one of "your videos"
    _settle(app)
    app.ovl_list.selection_set(0)
    app.resize_inserted(0.5)
    assert app.project.total_length == pytest.approx(8.0)
    app.ovl_list.selection_set(0)
    app.delete_row()
    assert len(app.project.segments) == 1 and app.project.total_length == pytest.approx(6.0)
    app.undo()
    assert len(app.project.segments) == 3


def _line(app):
    seg = app.project.add_segment(app.src.id, 1, 7)
    line = S.make_line(app.project, seg.id, 0.0, "hello there")
    app.project.script_lines.append(line)
    app.commit(keep_selection=seg.id)
    return line


def test_apply_speed_alone_and_undo(app, monkeypatch, tmp_path):  # noqa: F811
    import subprocess

    def synth(text, voice, rate, pitch, out):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", "-f", "mp3", out],
                       check=True)
        return []

    monkeypatch.setattr(V, "edge_synth", synth)
    _line(app)
    app.make_voices()
    _settle(app)
    before = app.project.script_lines[0]["audio"]
    app.speed_var.set(30)
    app.pitch_var.set(10)                       # moved but not applied
    assert V.settings(app.project)["speed"] == 0
    app.apply_voice_setting("speed")
    _settle(app)
    assert V.settings(app.project)["speed"] == 30 and V.settings(app.project)["pitch"] == 0
    assert app.project.script_lines[0]["audio"] not in ("", before)
    app.undo()
    assert V.settings(app.project)["speed"] == 0 and app.project.script_lines[0]["audio"] == before


def test_remove_character_replace_sound_hook_and_cover(app, tmp_path):  # noqa: F811
    from PIL import Image
    _line(app)
    app.char_name.set("Pip")
    app.save_character()
    app.char_tree.selection_set(())
    app.char_name.set("pip")                    # nothing picked in the list: the name box is used
    app.remove_character()
    assert app.project.voices["characters"] == []
    app.mute_var.set(True)
    app._mute_changed()
    assert app.project.voices["mute_original"] is True
    app.hook_var.set("Wait")
    app.hook_whole_video()
    assert app.project.hook["seconds"] == pytest.approx(6.0)
    picture = tmp_path / "me.png"
    Image.new("RGB", (400, 300), (10, 240, 10)).save(picture)
    app.set_cover_picture(str(picture))
    assert os.path.exists(app.project.cover["image"]) and app._cover_image is not None
    app.drop_cover_picture()
    assert "image" not in app.project.cover
    app.line_text.set("a much longer line " * 6)
    assert app.line_box.get("1.0", "end").strip().startswith("a much longer line")


@pytest.mark.parametrize("size", ["1366x700", "1060x680"])
def test_timeline_always_on_screen_and_panel_is_roomy(app, size):  # noqa: F811
    app.root.attributes("-zoomed", False)
    app.root.geometry(size + "+0+0")
    for _ in range(20):
        app.root.update()
    from clipping.editor.gui.app import LANES, LANE_H, LANE_TOP
    height = app.root.winfo_height()
    for name in app.page_frames:
        app.show_page(name)
        app.root.update()
        page = app.page_frames[name]
        page.canvas.yview_moveto(1.0)
        app.root.update()
        bottom = app.timeline.winfo_rooty() - app.root.winfo_rooty() + app.timeline.winfo_height()
        assert app.timeline.winfo_height() >= LANE_TOP + len(LANES) * (LANE_H + 2) and bottom <= height, name
        assert page.inner.winfo_reqwidth() <= page.canvas.winfo_width() + 2, \
            (name, page.inner.winfo_reqwidth(), page.canvas.winfo_width())
    print("panel", page.canvas.winfo_width(), "player", app.player.widget.winfo_width(),
          app.player.widget.winfo_height(), "cut list rows", app.tree.winfo_height())
    assert app.player.widget.winfo_height() > 120 and app.tree.winfo_height() > 50


def test_captions_offer_the_script_in_react_mode_and_old_lines_are_cleaned(app, monkeypatch):  # noqa: F811
    from tkinter import messagebox
    from clipping.editor import project as P
    asked = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: asked.append(a) or True)
    line = _line(app)
    app.project.mode = "react"
    app.make_captions()
    assert asked and app.project.captions[0]["line_id"] == line["id"]
    line["text"] = '{"text": "Sir, tanks are not covered."}'
    app.save()
    app.open_project(app.folder)
    assert app.project.script_lines[0]["text"] == "Sir, tanks are not covered."
    assert "Cleaned 1" in app.status_var.get()
