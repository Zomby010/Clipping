"""Tests for Claude as the main AI (with Gemini backup) and the storyline writer."""

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from clipping import frames, story
from clipping.providers import claude


def _clips():
    return [
        {"rank": 2, "start_time": 60.0, "end_time": 90.0, "title_inggris": "Hide", "keep_segments": []},
        {"rank": 1, "start_time": 10.0, "end_time": 40.0, "title_inggris": "Race", "keep_segments": []},
    ]


SEGS = [
    {"start": 12.0, "end": 15.0, "text": "Kamau never loses."},
    {"start": 62.0, "end": 65.0, "text": "Hide in the car wash."},
]


def test_word_budget_fits_clip_and_length_cap():
    assert story.word_budget(30, "normal", 0.5) == int(28.5 * story.WORDS_PER_SECOND)
    assert story.word_budget(120, "short") == story.LENGTH_CAP["short"]
    assert story.word_budget(2, "long") == 12


def test_series_prompt_orders_parts_by_time_and_has_full_transcript():
    p = story.build_prompt("series", "[0.0 - 1.0] FULL TRANSCRIPT MARKER", _clips(), SEGS, brief="Nairobi racers")
    assert p.index("rank 1 — Part 1 of 2") < p.index("rank 2 — Part 2 of 2")
    assert "FULL TRANSCRIPT MARKER" in p and "Nairobi racers" in p
    assert "SERIES" in p and "Kamau never loses." in p


def test_standalone_prompt_rules():
    p = story.build_prompt("standalone", "t", _clips(), SEGS)
    assert "STANDALONE" in p and "SERIES (one" not in p


def test_clean_and_trim():
    raw = {"series_title": "S", "premise": "P", "cast": ["A"],
           "episodes": [{"rank": 1, "episode_title": "E1", "script": " One.  Two. "},
                        {"rank": 9, "episode_title": "x", "script": "unknown rank"}]}
    out = story._clean(raw, _clips())
    assert list(out["episodes"]) == [1] and out["episodes"][1]["script"] == "One. Two."
    assert story._trim_to_budget("A b c. D e f g h.", 4) == "A b c."


def test_write_stories_uses_claude_then_marks_parts(monkeypatch, tmp_path):
    cfg = SimpleNamespace(story_mode="series", ai_provider="claude", voiceover_length="normal",
                          voiceover_offset=0.5, voiceover_style="analysis", story_brief="",
                          claude_frames=False, outputs_dir=str(tmp_path), file_video_asli="")

    def fake_ask(cfg, prompt, schema=None, images=(), label=""):
        assert schema is story.STORY_SCHEMA
        return {"series_title": "S", "premise": "P", "cast": [],
                "episodes": [{"rank": 1, "episode_title": "A", "script": "first"},
                             {"rank": 2, "episode_title": "B", "script": "second"}]}

    monkeypatch.setattr(claude, "ask", fake_ask)
    st = story.write_stories(cfg, _clips(), "t", SEGS)
    assert st["writer"] == "claude"
    assert st["episodes"][1]["part"] == 1 and st["episodes"][2]["part"] == 2
    assert "Part 2: B" in story.as_text(st)


def test_write_stories_falls_back_to_gemini(monkeypatch, tmp_path):
    cfg = SimpleNamespace(story_mode="standalone", ai_provider="claude", claude_frames=False,
                          outputs_dir=str(tmp_path), file_video_asli="")

    def broken(*a, **k):
        raise claude.ClaudeUnavailable("not signed in")

    monkeypatch.setattr(claude, "ask", broken)
    monkeypatch.setattr(story, "_ask_gemini", lambda cfg, prompt: {
        "series_title": "", "premise": "", "cast": [],
        "episodes": [{"rank": 1, "episode_title": "G", "script": "from gemini"}]})
    st = story.write_stories(cfg, _clips(), "t", SEGS)
    assert st["writer"] == "gemini" and st["episodes"][1]["script"] == "from gemini"


def test_classic_mode_writes_no_story():
    assert story.write_stories(SimpleNamespace(story_mode="classic"), _clips(), "t", SEGS) is None


def test_pick_backend(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude, "cli_path", lambda: "/usr/bin/claude")
    assert claude.pick_backend(SimpleNamespace(claude_backend="auto")) == "cli"
    assert claude.pick_backend(SimpleNamespace(claude_backend="api")) is None
    monkeypatch.setattr(claude, "cli_path", lambda: None)
    assert claude.pick_backend(SimpleNamespace(claude_backend="auto")) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert claude.pick_backend(SimpleNamespace(claude_backend="auto")) == "api"


def test_cli_call_sends_prompt_on_stdin_and_unwraps_arrays(monkeypatch, tmp_path):
    pic = tmp_path / "p.jpg"
    pic.write_bytes(b"x")
    seen = {}

    def fake_run(cmd, input=None, **kw):
        seen["cmd"], seen["input"] = cmd, input
        out = {"type": "result", "subtype": "success", "is_error": False,
               "structured_output": {"items": [{"rank": 1}]}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(claude, "cli_path", lambda: "claude")
    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    got = claude.ask(SimpleNamespace(claude_backend="cli"), "PROMPT",
                     schema={"type": "array", "items": {"type": "object"}}, images=[str(pic)])
    assert got == [{"rank": 1}]
    assert "PROMPT" in seen["input"] and str(pic) in seen["input"]
    assert "--add-dir" in seen["cmd"] and "Read" in seen["cmd"]
    schema = json.loads(seen["cmd"][seen["cmd"].index("--json-schema") + 1])
    assert schema["type"] == "object" and "items" in schema["required"]


def test_cli_error_raises_unavailable(monkeypatch):
    monkeypatch.setattr(claude, "cli_path", lambda: "claude")
    monkeypatch.setattr(claude.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "not json", "Please run /login"))
    with pytest.raises(claude.ClaudeUnavailable, match="login"):
        claude.ask(SimpleNamespace(claude_backend="cli"), "x")


def test_plan_sheets_spreads_frames_evenly():
    step, spans = frames.plan_sheets(0, 90, max_sheets=2, per_sheet=9)
    assert step == pytest.approx(5.0)
    assert spans[0][0] == pytest.approx(2.5) and len(spans) == 2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_make_sheets_writes_pictures(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=320x180:rate=10:duration=20", str(video)], check=True)
    sheets = frames.make_sheets(str(video), str(tmp_path / "s"), max_sheets=2)
    assert len(sheets) == 2 and all((tmp_path / "s" / p.split("/")[-1]).exists() for p in
                                    [s["path"] for s in sheets])
    assert "Picture 1" in frames.describe_sheets(sheets)
