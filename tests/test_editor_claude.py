"""Clip Editor phase 3: what is asked of Claude, with Claude mocked."""

import json
import os

import pytest

from clipping.editor import claude_editor as CE
from clipping.editor import project as P
from clipping.editor import script as S
from clipping.providers import claude as provider


def _project(mode="explain"):
    pr = P.Project(title="Story", channel="GTA 6", mode=mode, angle="A heist gone wrong")
    src = pr.add_source(P.Source(path="/x/a.mp4", name="a", duration=300, width=1920, height=1080))
    a = pr.add_segment(src.id, 10, 20)
    b = pr.add_segment(src.id, 100, 110)
    return pr, src, a, b


class FakeClaude:
    """Stands in for CE.ask: returns each queued reply in turn and records the prompts."""
    def __init__(self, *replies):
        self.replies, self.prompts, self.images = list(replies), [], []

    def __call__(self, cfg, prompt, schema, images=(), label="Claude", timeout=900):
        self.prompts.append(prompt)
        self.images.append(list(images))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


GOOD_SCENES = [
    {"start": 0, "end": 40, "what_happens": "A car chase", "who": "Two cars", "mood": "tense", "viral_score": 8, "why": "Big crash"},
    {"start": 40, "end": 90, "what_happens": "They talk", "who": "Two men", "mood": "calm", "viral_score": 3, "why": "Slow"},
]


# ---------------------------------------------------------------- scene log
def test_scene_log_checked_cleaned_and_starred(tmp_path, monkeypatch):
    pr, src, *_ = _project()
    src.path = str(tmp_path / "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"x")
    fake = FakeClaude(GOOD_SCENES + [{"start": 95, "end": 99999, "what_happens": " Clipped ", "who": "x", "mood": "y",
                                      "viral_score": 12, "why": "z"}])
    monkeypatch.setattr(CE, "ask", fake)
    monkeypatch.setattr(CE.frames, "make_sheets", lambda *a, **k: [])
    log = CE.watch(CE.make_cfg(), src, str(tmp_path), lambda s: [{"word": "hi", "src_start": 1, "src_end": 2}])
    assert [s["source_id"] for s in log] == [src.id] * 3
    assert log[2]["end"] == 300 and log[2]["viral_score"] == 10 and log[2]["what_happens"] == "Clipped"
    assert [s["start"] for s in CE.stars_from(log)] == [0, 95]       # 8 and 10 are starred, 3 is not
    assert "[1.0] hi" in fake.prompts[0]


def test_scene_log_is_cached_and_never_asked_twice(tmp_path, monkeypatch):
    pr, src, *_ = _project()
    src.path = str(tmp_path / "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"x")
    fake = FakeClaude(GOOD_SCENES)
    monkeypatch.setattr(CE, "ask", fake)
    monkeypatch.setattr(CE.frames, "make_sheets", lambda *a, **k: [])
    words = lambda s: [{"word": "hi", "src_start": 1, "src_end": 2}]
    first = CE.watch(CE.make_cfg(), src, str(tmp_path), words)
    second = CE.watch(CE.make_cfg(), src, str(tmp_path), lambda s: pytest.fail("transcribed again"))
    assert first == second and len(fake.prompts) == 1


def test_bad_reply_is_retried_once_with_the_problem_pointed_out(monkeypatch, tmp_path):
    pr, src, *_ = _project()
    src.path = str(tmp_path / "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"x")
    bad = [{"start": "zero", "end": 5}]                               # wrong types, missing fields
    fake = FakeClaude(bad, GOOD_SCENES)
    monkeypatch.setattr(CE, "ask", fake)
    monkeypatch.setattr(CE.frames, "make_sheets", lambda *a, **k: [])
    log = CE.watch(CE.make_cfg(), src, str(tmp_path), lambda s: [{"word": "hi", "src_start": 1, "src_end": 2}])
    assert len(log) == 2 and len(fake.prompts) == 2
    assert "not usable" in fake.prompts[1]


def test_two_bad_replies_give_a_plain_error(monkeypatch, tmp_path):
    pr, src, *_ = _project()
    src.path = str(tmp_path / "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"x")
    monkeypatch.setattr(CE, "ask", FakeClaude([], "not even a list"))
    monkeypatch.setattr(CE.frames, "make_sheets", lambda *a, **k: [])
    with pytest.raises(CE.ClaudeError) as e:
        CE.watch(CE.make_cfg(), src, str(tmp_path), lambda s: [{"word": "hi", "src_start": 1, "src_end": 2}])
    assert "twice in a row" in str(e.value) and "Traceback" not in str(e.value)


def test_claude_not_set_up_says_how_to_fix_it(monkeypatch):
    pr, *_ = _project()
    monkeypatch.setattr(CE, "ask", FakeClaude(provider.ClaudeUnavailable(
        "Claude is not set up: install Claude Code and sign in (run 'claude' once), or put ANTHROPIC_API_KEY in .env.")))
    with pytest.raises(CE.ClaudeError) as e:
        CE.suggest_angles(CE.make_cfg(), pr, "")
    assert "claude" in str(e.value).lower() and "sign in" in str(e.value)


# ---------------------------------------------------------------- angles
def test_three_angles(monkeypatch):
    pr, *_ = _project()
    monkeypatch.setattr(CE, "ask", FakeClaude(["Heist gone wrong", " Nature documentary ", "Court case"]))
    assert CE.suggest_angles(CE.make_cfg(), pr, "a chase") == ["Heist gone wrong", "Nature documentary", "Court case"]


def test_wrong_number_of_angles_is_retried(monkeypatch):
    pr, *_ = _project()
    fake = FakeClaude(["only one"], ["a", "b", "c"])
    monkeypatch.setattr(CE, "ask", fake)
    assert len(CE.suggest_angles(CE.make_cfg(), pr, "")) == 3 and len(fake.prompts) == 2


# ---------------------------------------------------------------- script
def test_script_prompt_has_the_cut_scene_log_transcript_and_context(monkeypatch):
    pr, src, a, b = _project()
    pr.voices = {"characters": [{"name": "Pip", "about": "a nervous robot", "voice": "en-NG-AbeoNeural"}]}
    pr.mode = "new_story"
    log = [dict(s, source_id=src.id) for s in GOOD_SCENES]
    fake = FakeClaude([{"segment_id": a.id, "offset": 0.5, "text": "Pip sees it", "character": "Pip", "cue": "sfx:whoosh"}])
    monkeypatch.setattr(CE, "ask", fake)
    out = CE.write_script(CE.make_cfg(), pr, log, lambda seg: f"said in {seg.id}", series="Pip owes Bo money.")
    p = fake.prompts[0]
    assert a.id in p and b.id in p and "starts at 10.0s" in p and "lasts 10.0s" in p      # the cut with edit times
    assert "A car chase" in p and f"said in {a.id}" in p                                  # scene log + transcript
    assert "Pip: a nervous robot" in p and "A heist gone wrong" in p                      # characters + angle
    assert "Pip owes Bo money." in p and "New story" in p and "GTA 6" in p                # series memory, mode, channel
    assert out[0]["character"] == "Pip" and out[0]["cue"] == "sfx:whoosh"


def test_script_drops_unknown_moments_and_unknown_characters(monkeypatch):
    pr, src, a, b = _project()
    pr.voices = {"characters": [{"name": "Pip", "about": "", "voice": ""}]}
    reply = [{"segment_id": a.id, "offset": 0, "text": "Hello there", "character": "Stranger", "cue": ""},
             {"segment_id": "seg_nope", "offset": 0, "text": "Lost", "character": "", "cue": ""}]
    monkeypatch.setattr(CE, "ask", FakeClaude(reply))
    out = CE.write_script(CE.make_cfg(), pr, [], lambda s: "")
    assert len(out) == 1 and out[0]["character"] == ""


def test_script_with_nothing_usable_is_retried_then_errors(monkeypatch):
    pr, src, a, b = _project()
    bad = [{"segment_id": "seg_nope", "offset": 0, "text": "Lost", "character": "", "cue": ""}]
    fake = FakeClaude(bad, bad)
    monkeypatch.setattr(CE, "ask", fake)
    with pytest.raises(CE.ClaudeError):
        CE.write_script(CE.make_cfg(), pr, [], lambda s: "")
    assert len(fake.prompts) == 2


def test_script_needs_moments():
    with pytest.raises(CE.ClaudeError):
        CE.write_script(CE.make_cfg(), P.Project(), [], lambda s: "")


def test_channel_tone_and_language_reach_the_prompt(monkeypatch):
    pr, src, a, b = _project()
    pr.channel = "Kids"
    fake = FakeClaude([{"segment_id": a.id, "offset": 0, "text": "Habari", "character": "", "cue": ""}])
    monkeypatch.setattr(CE, "ask", fake)
    CE.write_script(CE.make_cfg(), pr, [], lambda s: "", language="sw")
    assert "no violence jokes" in fake.prompts[0] and "Swahili" in fake.prompts[0]


# ---------------------------------------------------------------- one line
def test_rewrite_one_line_fits_its_time(monkeypatch):
    pr, src, a, b = _project()
    ln = S.make_line(pr, a.id, 0, "A plain line")
    pr.script_lines.append(ln)
    fake = FakeClaude({"text": " ".join(["word"] * 40)})
    monkeypatch.setattr(CE, "ask", fake)
    out = CE.rewrite_line(CE.make_cfg(), pr, ln, available=4.0, note="funnier")
    assert S.count_words(out) <= S.line_budget(4.0) and "funnier" in fake.prompts[0]


# ---------------------------------------------------------------- series memory
def test_series_memory_file_per_channel(tmp_path):
    assert CE.read_series(str(tmp_path), "GTA 6") == ""
    CE.write_series(str(tmp_path), "GTA 6", "Pip owes Bo.")
    assert os.path.basename(CE.series_path(str(tmp_path), "GTA 6")) == "series-gta-6.md"
    assert CE.read_series(str(tmp_path), "GTA 6") == "Pip owes Bo."
    assert CE.read_series(str(tmp_path), "Sports") == ""


def test_series_summary(monkeypatch):
    pr, src, a, b = _project()
    pr.script_lines.append(S.make_line(pr, a.id, 0, "Pip stole the car"))
    fake = FakeClaude("  Pip stole a car.\n New joke: Bo's hat. ")
    monkeypatch.setattr(CE, "ask", fake)
    assert CE.series_summary(CE.make_cfg(), pr) == "Pip stole a car. New joke: Bo's hat."
    assert "Pip stole the car" in fake.prompts[0]
