"""Clip Editor phase 3: the Story and Voice tabs, driven through the real window (Claude and edge-tts mocked)."""

import os
import shutil
import subprocess
import time

import pytest

tk = pytest.importorskip("tkinter")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIP_EDITOR_NO_MPV", "1")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    import clipping.editor.gui.app as A
    from tkinter import filedialog, messagebox
    monkeypatch.setattr(A, "load_settings", lambda: {"edits_root": str(tmp_path / "edits"), "ready_root": str(tmp_path / "ready")})
    monkeypatch.setattr(A, "save_settings", lambda *a, **k: None)
    video = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=200:duration=20:sample_rate=48000", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **k: (str(video),))
    box = {"errors": [], "infos": []}
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: box["errors"].append(a))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: box["infos"].append(a))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: True)
    a = A.EditorApp(root)
    a.box = box
    root.update()
    yield a
    root.destroy()


def wait(app, seconds=60):
    end = time.time() + seconds
    while app.busy and time.time() < end:
        app.root.update()
        time.sleep(0.02)
    for _ in range(5):
        app.root.update()
    assert not app.busy, "job did not finish"
    assert not app.box["errors"], app.box["errors"]


def tone(path, freq, seconds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=24000",
                    "-f", "mp3", path], check=True)


class Claude:
    def __init__(self, app):
        self.app, self.labels = app, []

    def __call__(self, cfg, prompt, schema, images=(), label="Claude", timeout=900):
        self.labels.append(label)
        if label == "Scene log":
            return [{"start": 0, "end": 6, "what_happens": "A chase", "who": "Cars", "mood": "tense", "viral_score": 9, "why": "Crash"},
                    {"start": 6, "end": 12, "what_happens": "Talking", "who": "Men", "mood": "calm", "viral_score": 3, "why": "Slow"},
                    {"start": 12, "end": 18, "what_happens": "A jump", "who": "Bike", "mood": "hype", "viral_score": 8, "why": "Big air"}]
        if label == "Angles":
            return ["A heist gone wrong", "A nature documentary", "A court case"]
        if label == "Script":
            ids = [s.id for s in self.app.project.segments]
            return [{"segment_id": ids[0], "offset": 0.5, "text": "Here come the cars", "character": "", "cue": "sfx:whoosh"},
                    {"segment_id": ids[1], "offset": 0.5, "text": "And a mighty jump", "character": "", "cue": ""}]
        if label == "Rewrite":
            return {"text": "A brand new line"}
        if label == "Series summary":
            return "Cars chased; a bike flew."
        raise AssertionError(label)


@pytest.fixture
def ready(app, monkeypatch, tmp_path):
    import clipping.editor.captions as C
    import clipping.editor.claude_editor as CE
    import clipping.editor.voice as V
    fake = Claude(app)
    monkeypatch.setattr(CE, "ask", fake)
    monkeypatch.setattr(CE.frames, "make_sheets", lambda *a, **k: [])
    monkeypatch.setattr(C, "transcribe_range", lambda *a, **k: [{"word": "vroom", "src_start": 1.0, "src_end": 1.5}])

    def synth(text, voice, rate, pitch, out):
        tone(out, 440, max(0.8, len(text.split()) * 0.4))
        return [{"word": w, "start": round(i * 0.4, 3), "end": round((i + 1) * 0.4, 3)} for i, w in enumerate(text.split())]

    monkeypatch.setattr(V, "edge_synth", synth)
    app.add_video()
    wait(app)
    app.fake = fake
    return app


@needs_ffmpeg
def test_ask_claude_walks_through_watch_angle_script_voices(ready):
    a = ready
    a.ask_claude(); wait(a)                                         # 1. watch
    assert [s["viral_score"] for s in a.project.scene_log] == [9, 3, 8] and len(a.scene_tree.get_children()) == 3
    assert [a.scene_tree.item(i)["values"][0] for i in a.scene_tree.get_children()] == ["★", "", "★"]   # stars
    a.ask_claude()                                                    # no moments yet: tells you what to do
    assert not a.busy and "Add all" in a.status_var.get()
    a.add_starred_to_cut()
    assert len(a.project.segments) == 2 and a.project.segments[0].source_out == 6
    a.ask_claude(); wait(a)                                         # 2. angle suggestions
    assert len(a.angle_buttons.winfo_children()) == 3
    a._use_angle("A heist gone wrong")
    assert a.project.angle == "A heist gone wrong"
    a.ask_claude(); wait(a)                                         # 3. script
    assert len(a.script_tree.get_children()) == 2 and a.project.script_lines[0]["cue"] == "sfx:whoosh"
    a.ask_claude(); wait(a)                                         # 4. voices
    assert all(l["audio"] for l in a.project.script_lines)
    assert [a.voice_tree.item(i)["values"][2] for i in a.voice_tree.get_children()][0].startswith("made")
    a.ask_claude()
    assert "Everything is done" in a.status_var.get()


@needs_ffmpeg
def test_mode_characters_series_and_edit_line_buttons(ready):
    a = ready
    a.ask_to_watch(); wait(a)
    a.add_starred_to_cut()
    a.mode_var.set("new_story"); a._mode_changed()
    assert a.project.mode == "new_story" and a.chars_box.winfo_manager()                       # characters list shows
    a.char_name.set("Pip"); a.char_about.set("a nervous robot"); a.char_voice.set("Guy · American English (man)")
    a.save_character()
    assert a.project.voices["characters"] == [{"name": "Pip", "about": "a nervous robot", "voice": "en-US-GuyNeural"}]
    a.mode_var.set("explain"); a._mode_changed()
    assert not a.chars_box.winfo_manager()
    a.write_script(False); wait(a)
    lid = a.script_tree.get_children()[0]
    a.script_tree.selection_set(lid); a.root.update()
    assert a.line_text.get() == "Here come the cars"
    a.line_text.set("Here come the fast cars"); a.save_line_text()
    assert a.project.script_lines[0]["text"] == "Here come the fast cars"
    start = a.project.script_lines[0]["start"]
    a.script_tree.selection_set(lid); a.nudge_line(0.5)
    assert a.project.script_lines[0]["start"] == pytest.approx(start + 0.5)
    a.script_tree.selection_set(lid); a.rewrite_note.set("funnier"); a.rewrite_line(); wait(a)
    assert a.project.script_lines[0]["text"] == "A brand new line"
    a.script_tree.selection_set(lid); a.delete_line()
    assert len(a.script_tree.get_children()) == 1
    a.undo()
    assert len(a.script_tree.get_children()) == 2                                               # Undo brings it back
    # series memory
    a.series_box.insert("1.0", "Pip owes Bo."); a._save_series()
    a.add_to_series(); wait(a)
    assert "Cars chased" in a.series_box.get("1.0", "end")
    from clipping.editor import claude_editor as CE
    assert "Pip owes Bo." in CE.read_series(a.edits_root, a.project.channel)


@needs_ffmpeg
def test_script_captions_original_level_and_voice_lane(ready):
    a = ready
    a.ask_to_watch(); wait(a)
    a.add_starred_to_cut()
    a.write_script(False); wait(a)
    a.make_voices(); wait(a)
    a.make_captions()                                                                          # asks: script or speech -> script
    caps = [c for c in a.project.captions if c.get("line_id")]
    assert caps and a.project.voices["captions_from"] == "script"
    # moments follow the original sound slider
    a.tree.selection_set(a.project.segments[0].id); a.root.update()
    a.level_var.set(0.5); a._level_changed()
    assert a.project.segments[0].original_level == 0.5 and "50 %" in a.level_info.get()
    # reorder: captions and lines follow
    first_line = a.project.script_lines[0]
    assert first_line["start"] < 6.0                                                           # it plays in the first moment
    a.project.move_segment(0, 1); a.commit("moved")
    assert first_line["start"] >= 6.0                                                          # the line went with its moment
    assert min(c["start"] for c in a.project.captions if c.get("line_id") == first_line["id"]) >= 6.0
    a.draw_timeline()                                                                           # lane drawing doesn't crash
    blocks = [i for i in a.timeline.find_all() if any(t.startswith("line_") for t in a.timeline.gettags(i))]
    assert len(blocks) == 2
    # a click on a voice-over block selects the line on the Story tab
    ln = a.project.script_lines[0]
    total = a.project.total_length
    from clipping.editor.gui import app as A
    x = A.LANE_LABEL_W + (ln["start"] + 0.1) / total * (a.timeline.winfo_width() - 8 - A.LANE_LABEL_W)
    a.timeline.update()
    ev = type("E", (), {"x": x, "y": A.LANE_TOP + A.LANES.index("Voice-over") * (A.LANE_H + 2) + 5})()
    a._timeline_click(ev)
    assert a.script_tree.selection() == (ln["id"],)


@needs_ffmpeg
def test_voice_warnings_and_speed_up(ready):
    a = ready
    a.ask_to_watch(); wait(a)
    a.add_starred_to_cut()
    a.write_script(False); wait(a)
    ln = a.project.script_lines[0]
    from clipping.editor import script as S
    S.set_text(ln, " ".join(["word"] * 40))                                                    # far too long for its moment
    a._story_changed(); a.commit("long")
    assert "past its moment" in S.warning_for(a.project, ln["id"])
    assert "bad" in a.script_tree.item(ln["id"], "tags")
    ln["duration"] = 8.0                                                                       # the voice is 8 s, the moment 6 s
    a.script_tree.selection_set(ln["id"]); a.root.update()
    a.speed_up_line()
    assert 40 <= ln["speed"] <= 60                                                             # 8 s into about 5.4 s needs about +48 %
    assert ln["audio"] == "" and "faster" in a.status_var.get()                                # the voice is made again
