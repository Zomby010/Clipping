"""Fixes from the phase 1 hands-on test (November 2026)."""

import json
import os
import shutil
import subprocess

import pytest

from clipping.editor import project as P

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _video(path, size):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:d=6:r=10",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


@pytest.fixture
def app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from clipping.editor.gui import app as A
    monkeypatch.setenv("CLIP_EDITOR_NO_MPV", "1")
    monkeypatch.setattr(A, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text(json.dumps(
        {"edits_root": str(tmp_path / "edits"), "ready_root": str(tmp_path / "ready")}))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    editor = A.EditorApp(root)
    editor.errors = []
    monkeypatch.setattr(A.messagebox, "showerror", lambda _t, m: editor.errors.append(m))
    monkeypatch.setattr(A.messagebox, "askyesno", lambda *a: True)
    root.update()
    yield editor
    editor.player.close()
    root.destroy()


def test_typed_moment_outside_the_video_adds_nothing():
    pr = P.Project()
    src = pr.add_source(P.Source(path="v.mp4", duration=62.835))
    for text in ("0:00-2.25", "0:01-0:05, 1:10-1:20"):
        with pytest.raises(ValueError, match="outside the video"):
            pr.add_ranges(src.id, text)
    assert pr.segments == []
    assert pr.add_ranges(src.id, "0:50-1:03")[0].source_out == 62.835   # shown length, rounded up


def test_only_saved_edits_load(tmp_path):
    other = tmp_path / "settings.json"
    other.write_text("{}")
    video = tmp_path / P.PROJECT_FILE
    video.write_bytes(b"\x00\xee\x00 not text")
    for path in (other, video):
        with pytest.raises(P.NotAnEdit, match="isn't a saved edit"):
            P.load(str(path))
    folder = tmp_path / "edit"
    P.save(P.Project(title="Mine"), str(folder))
    assert P.load(str(folder / P.PROJECT_FILE))[0].title == "Mine"


def test_open_on_a_video_starts_an_edit(app, tmp_path, monkeypatch):
    from clipping.editor.gui import app as A
    video = _video(tmp_path / "clip.mp4", "320x180")
    monkeypatch.setattr(A.filedialog, "askopenfilename", lambda **k: video)
    app.open_dialog()
    assert app.errors == [] and app.project.title == "clip"
    assert [s.path for s in app.project.sources] == [video] and app.player.path == video
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    monkeypatch.setattr(A.filedialog, "askopenfilename", lambda **k: str(junk))
    app.open_dialog()
    assert app.errors == [A.P.NOT_AN_EDIT] and app.project.title == "clip"


def _load(app, path):
    from clipping.editor import media
    src = app.project.add_source(media.probe(path))
    app.current_source_id = src.id
    app.commit()
    app.load_source(src)
    return src


def test_removing_the_video_empties_the_player(app, tmp_path):
    src = _load(app, _video(tmp_path / "a.mp4", "320x180"))
    app.project.add_ranges(src.id, "0:01-0:02")
    app.commit()
    app.remove_video()
    assert app.player.path is None and app.project.sources == []


def test_space_plays_even_with_a_button_focused(app, monkeypatch):
    presses = []
    app.player.path = "loaded.mp4"
    monkeypatch.setattr(app.player, "toggle", lambda: presses.append(1))   # one play/pause action
    app.play_btn.focus_force()
    app.root.update()
    app.play_btn.event_generate("<Key-space>")
    app.root.after(300, app.root.quit)   # ttk's own press would fire after a short delay
    app.root.mainloop()
    assert presses == [1]


def test_new_preview_starts_at_the_selected_moment(app, tmp_path):
    src = _load(app, _video(tmp_path / "a.mp4", "320x180"))
    segs = app.project.add_ranges(src.id, "0:00-0:02, 0:03-0:05")
    app.commit(keep_selection=segs[1].id)
    assert app._selected_start() == 2.0


@pytest.mark.parametrize("size,mode,zoom,expected", [
    ("320x180", "crop", 1.0, (True, False)),     # wide: sideways only
    ("320x180", "crop", 1.5, (True, True)),
    ("320x180", "auto", 1.0, (False, False)),
    ("180x320", "crop", 1.0, (False, False)),    # already 9:16
    ("180x320", "crop", 1.5, (True, True)),
])
def test_position_sliders_only_when_they_do_something(app, tmp_path, size, mode, zoom, expected):
    src = _load(app, _video(tmp_path / "a.mp4", size))
    seg = app.project.add_ranges(src.id, "0:00-0:02")[0]
    seg.framing.mode, seg.framing.zoom = mode, zoom
    app.commit(keep_selection=seg.id)
    app.root.update()
    assert tuple(not s.instate(["disabled"]) for s in app.pan_scales) == expected
    assert "0:00.0 to 0:02.0" in app.detail_var.get()
