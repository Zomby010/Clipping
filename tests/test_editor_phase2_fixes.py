"""Fixes from the phase 2 hands-on test (November 2026)."""

import json
import shutil
import subprocess

import pytest

from clipping.editor import captions as C
from clipping.editor import project as P
from clipping.editor import text_layers

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _project():
    pr = P.Project()
    src = pr.add_source(P.Source(path="v.mp4", duration=60.0))
    return pr, src


def test_own_moments_replace_the_untouched_whole_clip():
    pr, src = _project()
    pr.add_segment(src.id, 0, 60).whole_clip = True
    pr.add_segment(src.id, 5, 10)
    assert pr.drop_whole_clip(src.id) == 1
    assert [s.source_out for s in pr.segments] == [10]
    assert pr.drop_whole_clip(src.id) == 0       # an ordinary moment is never dropped


def test_a_trimmed_or_split_whole_clip_is_kept():
    for change in (lambda p: p.nudge(0, "out", -1), lambda p: p.split_segment(0, 30)):
        pr, src = _project()
        pr.add_segment(src.id, 0, 60).whole_clip = True
        change(pr)
        assert pr.drop_whole_clip(src.id) == 0


def test_only_new_moments_need_captions():
    pr, src = _project()
    first = pr.add_segment(src.id, 0, 10)
    pr.captions = [{"id": "c", "segment_id": first.id}]
    new = pr.add_segment(src.id, 20, 30)
    assert [s.id for s in C.uncaptioned(pr)] == [new.id]
    new.captioned = True                          # listened to, nobody spoke
    assert C.uncaptioned(pr) == []
    again = P.Project.from_dict(pr.to_dict())
    assert again.segments[1].captioned


def test_hook_movement_and_box():
    pr, _src = _project()
    pr.hook = {"text": "Wait for it", "seconds": 2.0}
    plain = text_layers.build_ass(pr, 1080, 1920)
    assert ",Hook,,0,0,0,,{\\fscx70\\fscy70\\t(0,160," in plain      # the original look
    for anim, tag in (("bounce", "\\fscx118"), ("slide", "\\move("), ("shake", "\\frz")):
        pr.hook = {"text": "Wait for it", "seconds": 2.0, "anim": anim, "box": True}
        out = text_layers.build_ass(pr, 1080, 1920)
        assert ",HookBox,," in out and tag in out


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
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=180x320:d=12:r=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    from clipping.editor import media
    editor.src = editor.project.add_source(media.probe(str(video)))
    editor.current_source_id = editor.src.id
    editor.refresh_all()
    root.update()
    yield editor
    editor.player.close()
    root.destroy()


def test_typing_a_moment_removes_the_whole_clip_and_undo_restores_it(app):
    app.project.add_segment(app.src.id, 0, 12).whole_clip = True
    app.commit()
    app.sources_list.selection_set(0)
    app.ranges_var.set("0:01-0:04")
    app.add_typed_ranges()
    assert [(s.source_in, s.source_out) for s in app.project.segments] == [(1, 4)]
    app.undo()
    assert [s.source_out for s in app.project.segments] == [12]


def test_playing_my_cut_stops_at_the_end_of_each_moment(app):
    for a, b in ((0, 3), (5, 9)):
        seg = app.project.add_segment(app.src.id, a, b)
    app.commit(keep_selection=seg.id)
    app.watch.set("cut")
    app.preview_path = app.player.path = app.src.path      # stands in for the built preview
    app.player.seek(1.0)
    app._arm_cut_end()
    assert app._cut_end == pytest.approx(2.96)
    app.player.seek(app._cut_end)                          # parked at the end: Play replays it
    app.tree.selection_set(app.project.segments[0].id)
    app._arm_cut_end()
    assert app.player.time() == pytest.approx(0.0)
    app.one_moment.set(False)
    app._arm_cut_end()
    assert app._cut_end is None


def test_emoji_menu_uses_pictures(app):
    from clipping.editor import styles
    if not styles.emoji_font():
        pytest.skip("no colour emoji font")
    assert len(app._emoji_pictures()) == len(text_layers.COMMON_EMOJI)


def test_make_captions_keeps_fixes_and_only_hears_new_moments(app, monkeypatch):
    from clipping.editor.gui import text_panels
    asked, heard = [], []
    monkeypatch.setattr(text_panels.messagebox, "askyesno", lambda *a: asked.append(a[1]) or True)
    monkeypatch.setattr(app, "run_job", lambda _label, work, done: done(work(lambda f: None)))

    def fake(project, transcriber, cache, max_words, progress, cancel, only=None):
        ids = [s.id for s in project.segments if only is None or s.id in only]
        heard.append(ids)
        return [C.make_caption([{"word": "hi", "src_start": project.segments[project.segment_index(i)].source_in,
                                 "src_end": project.segments[project.segment_index(i)].source_in + 0.5}], i)
                for i in ids]

    monkeypatch.setattr(text_panels.C, "transcribe_segments", fake)
    monkeypatch.setattr(text_panels.C, "uncaptioned", C.uncaptioned)
    for s in [app.src]:
        s.has_audio = True
    first = app.project.add_segment(app.src.id, 0, 3)
    app.commit()
    app.make_captions()
    app.project.captions[0]["words"][0]["word"] = app.project.captions[0]["text"] = "fixed"
    second = app.project.add_segment(app.src.id, 5, 8)
    app.commit()
    app.make_captions()
    assert heard == [[first.id], [second.id]] and not asked
    assert sorted(c["text"] for c in app.project.captions) == ["fixed", "hi"]
    app.make_captions()                       # nothing new: ask, then redo them all
    assert len(asked) == 1 and heard[-1] == [first.id, second.id]
