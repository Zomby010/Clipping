"""Clip Editor phase 5: checks before posting, Claude review, suggested cut, channel templates."""

import pytest

from clipping.editor import audio_mix as A
from clipping.editor import captions as C
from clipping.editor import overlays as O
from clipping.editor import project as P
from clipping.editor import review as R
from clipping.editor import templates as T


def _project(tmp_path, channel="GTA 6", ranges="1:00-1:10, 2:00-2:20"):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title="Review test", channel=channel)
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080))
    pr.add_ranges(src.id, ranges)
    return pr


def _words(*items):
    return [{"word": w, "src_start": a, "src_end": b} for w, a, b in items]


def _texts(findings):
    return " | ".join(f.text for f in findings)


def _good(tmp_path, channel="GTA 6"):
    """An edit that passes every check."""
    pr = _project(tmp_path, channel)
    pr.captions = C.captions_from_words(pr, pr.segments[0], _words(("no", 60.2, 60.6), ("way", 60.7, 61.2)))
    pr.hook = {"text": "He did NOT see this", "seconds": 2.0}
    pr.upload = {"title": "Big crash", "description": "What happened next", "hashtags": "#gta6 #shorts"}
    C.refresh(pr)
    return pr


# ------------------------------------------------------------------ local checks
def test_a_good_edit_passes(tmp_path):
    found = R.checks(_good(tmp_path), emoji_font="/x/emoji.ttf")
    assert found == [], _texts(found)


def test_empty_cut_list(tmp_path):
    pr = _project(tmp_path)
    pr.segments.clear()
    assert R.checks(pr)[0].level == R.PROBLEM


def test_missing_hook_and_captions(tmp_path):
    pr = _project(tmp_path)
    texts = _texts(R.checks(pr, emoji_font="x"))
    assert "first 2 seconds" in texts and "No captions" in texts


def test_early_caption_counts_as_a_hook(tmp_path):
    pr = _good(tmp_path)
    pr.hook = {}
    texts = _texts(R.checks(pr, emoji_font="x"))
    assert "first 2 seconds" not in texts and "No hook line" in texts


def test_too_long_for_platforms(tmp_path):
    pr = _project(tmp_path, ranges="0:00-3:30")
    found = R.checks(pr, emoji_font="x")
    assert found[0].level == R.PROBLEM and "too long" in found[0].text


def test_caption_timing_warnings(tmp_path):
    pr = _good(tmp_path)
    pr.captions += C.captions_from_words(pr, pr.segments[1], _words(("hi", 121.0, 121.1)))
    long = C.make_caption(_words(("slow", 125.0, 126.0), ("words", 126.0, 130.0)), pr.segments[1].id)
    pr.captions.append(long)
    texts = _texts(R.checks(pr, emoji_font="x"))
    assert "flash by" in texts and "over 3.5 s" in texts


def test_music_checks_and_fixes(tmp_path):
    pr = _good(tmp_path)
    track = tmp_path / "song.mp3"
    track.write_bytes(b"x")
    A.set_music(pr, str(track), "epic", volume=0.5, ducking="off")
    pr.audio["loudness"] = None
    found = R.checks(pr, emoji_font="x")
    texts = _texts(found)
    assert "music is loud" in texts and "doesn't dip" in texts and "licence note" in texts
    assert "Loudness" in texts
    for f in found:
        if f.fix:
            assert R.apply_fix(pr, f)
    m = pr.audio["music"]
    assert m["volume"] == 0.15 and m["ducking"] == "strong" and pr.audio["loudness"] == A.LOUDNESS
    pr.audio["music"]["licence"] = "Pixabay"
    assert R.checks(pr, emoji_font="x") == []


def test_upload_text_fixes(tmp_path):
    pr = _good(tmp_path, "Sports")
    pr.upload = {"title": "x" * 120, "description": "", "hashtags": " ".join(f"#t{i}" for i in range(20))}
    found = R.checks(pr, emoji_font="x")
    assert found[0].fix == "title_trim"
    for f in found:
        if f.fix:
            R.apply_fix(pr, f)
    assert len(pr.upload["title"]) == 100 and len(pr.upload["hashtags"].split()) == 15
    pr.upload = {"title": "", "hashtags": ""}
    for f in R.checks(pr, emoji_font="x"):
        if f.fix:
            R.apply_fix(pr, f)
    assert pr.upload["title"] == "Review test" and pr.upload["hashtags"] == "#football #sports #shorts"


def test_missing_files_kids_and_emoji(tmp_path):
    pr = _good(tmp_path, "Kids")
    pr.hook = {"text": "Look 😳", "seconds": 2}
    pr.logo = {"path": str(tmp_path / "gone.png")}
    texts = _texts(R.checks(pr, emoji_font=None))
    assert "Missing files: logo: gone.png" in texts
    assert "made for kids" in texts and "Emoji won't show" in texts


def test_mark_outside_the_safe_area_can_be_moved(tmp_path):
    pr = _good(tmp_path)
    mark = O.add_mark(pr, "circle", 1.0, x=0.95, y=0.9, size=0.3)
    found = [f for f in R.checks(pr, emoji_font="x") if f.fix == "mark_middle"]
    assert found and R.apply_fix(pr, found[0])
    assert (mark["x"], mark["y"]) == (0.46, 0.42)


# ------------------------------------------------------------------ Claude review
def test_claude_review_applies_ticked_suggestions(tmp_path):
    pr = _good(tmp_path)
    cap = pr.captions[0]
    before = [(w["src_start"], w["src_end"]) for w in cap["words"]]
    pr.overlays.append({"id": "pop_1", "type": "text", "start": 3, "end": 4, "text": "bro wat", "position": "1,1"})
    seen = {}

    def fake(cfg, prompt, schema=None, label=""):
        seen["prompt"], seen["schema"] = prompt, schema
        return [
            {"kind": "caption", "target": cap["id"], "old": "no way", "new": "no WAY", "why": "stress"},
            {"kind": "popup", "target": "pop_1", "old": "bro wat", "new": "bro what", "why": "spelling"},
            {"kind": "title", "target": "", "old": "Big crash", "new": "He crashed the getaway car", "why": "clearer"},
            {"kind": "hook", "target": "", "old": "", "new": "Worst getaway ever", "why": "shorter"},
            {"kind": "caption", "target": "cap_nothere", "old": "", "new": "x", "why": "invented"},
            {"kind": "hashtags", "target": "", "old": "#gta6", "new": "#gta6", "why": "same"},
            {"kind": "note", "target": "", "old": "", "new": "", "why": "Great pacing"},
            {"kind": "sticker", "target": "", "old": "", "new": "x", "why": "bad kind"},
        ]

    got = R.claude_review(pr, asker=fake)
    assert [s["kind"] for s in got] == ["caption", "popup", "title", "hook", "note"]
    assert "bro wat" in seen["prompt"] and seen["schema"] is R.REVIEW_SCHEMA
    got[3]["tick"] = False                       # the person unticks the hook
    assert R.apply_suggestions(pr, got) == 3
    assert cap["text"] == "no WAY" and [(w["src_start"], w["src_end"]) for w in cap["words"]] == before
    assert pr.overlays[0]["text"] == "bro what" and pr.upload["title"] == "He crashed the getaway car"
    assert pr.hook["text"] == "He did NOT see this"


def test_claude_review_one_undo_step(tmp_path):
    pr = _good(tmp_path)
    hist = P.History(pr)
    R.apply_suggestions(pr, [{"kind": "title", "target": "", "old": "", "new": "A", "why": "", "tick": True},
                             {"kind": "description", "target": "", "old": "", "new": "B", "why": "", "tick": True}])
    hist.record(pr)
    back = hist.undo()
    assert back.upload["title"] == "Big crash" and back.upload["description"] == "What happened next"


def test_claude_unavailable_or_bad_reply(tmp_path):
    pr = _good(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("not signed in")
    with pytest.raises(R.ReviewError, match="not signed in"):
        R.claude_review(pr, asker=broken)
    with pytest.raises(R.ReviewError):
        R.claude_review(pr, asker=lambda *a, **k: {"oops": 1})


def test_kids_review_asks_for_child_safety(tmp_path):
    assert "KIDS channel" in R.review_prompt(_good(tmp_path, "Kids"))
    assert "KIDS" not in R.review_prompt(_good(tmp_path, "Sports"))


def test_series_memory_is_used_when_there(tmp_path):
    pr = _good(tmp_path)
    (tmp_path / "series-GTA 6.md").write_text("Trevor owes Lamar money.", encoding="utf-8")
    assert R.series_memory(pr, str(tmp_path)) == "Trevor owes Lamar money."
    assert R.series_memory(pr, str(tmp_path / "nope")) == ""


# ------------------------------------------------------------------ suggested cut
def test_suggested_cut_is_validated_and_applied(tmp_path):
    pr = _good(tmp_path)                         # 10 s + 20 s
    first, second = pr.segments
    cap_id = pr.captions[0]["id"]
    reply = [{"segment_id": second.id, "keep_from": 125, "keep_to": 135, "why": "the crash"},
             {"segment_id": first.id, "keep_from": 60, "keep_to": 65, "why": "the reaction"}]
    parts = R.suggest_cut(pr, 15, asker=lambda *a, **k: reply)
    prev = R.describe_cut(pr, parts)
    assert prev.before_total == 30 and prev.after_total == 15
    hist = P.History(pr)
    R.apply_cut(pr, parts)
    hist.record(pr)
    assert [(s.source_in, s.source_out) for s in pr.segments] == [(125, 135), (60, 65)]
    assert pr.segments[1].id == first.id
    cap = C.find(pr, cap_id)
    assert cap["visible"] and cap["start"] == pytest.approx(10.2)   # followed its moment
    assert hist.undo().total_length == 30


@pytest.mark.parametrize("reply,match", [
    ([{"segment_id": "seg_invented", "keep_from": 0, "keep_to": 15, "why": ""}], "isn't in your cut"),
    ("first", "outside a moment"),
    ("far", "too far"),
    ("overlap", "overlapping"),
    ([], "didn't suggest"),
])
def test_bad_cut_suggestions_are_refused(tmp_path, reply, match):
    pr = _good(tmp_path)
    first, second = pr.segments
    if reply == "first":
        reply = [{"segment_id": first.id, "keep_from": 50, "keep_to": 65, "why": ""}]
    elif reply == "far":
        reply = [{"segment_id": second.id, "keep_from": 120, "keep_to": 121, "why": ""}]
    elif reply == "overlap":
        reply = [{"segment_id": second.id, "keep_from": 120, "keep_to": 130, "why": ""},
                 {"segment_id": second.id, "keep_from": 125, "keep_to": 131, "why": ""}]
    with pytest.raises(R.ReviewError, match=match):
        R.suggest_cut(pr, 15, asker=lambda *a, **k: reply)


def test_same_moment_twice_gets_a_new_id(tmp_path):
    pr = _good(tmp_path)
    second = pr.segments[1]
    parts = R.validate_cut(pr, [{"segment_id": second.id, "keep_from": 120, "keep_to": 126, "why": ""},
                                {"segment_id": second.id, "keep_from": 130, "keep_to": 138, "why": ""}], 15)
    R.apply_cut(pr, parts)
    assert pr.segments[0].id == second.id and pr.segments[1].id != second.id


# ------------------------------------------------------------------ templates
def test_templates_save_use_and_new_edits(tmp_path):
    pr = _good(tmp_path, "Sports")
    track = tmp_path / "song.mp3"
    track.write_bytes(b"x")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    A.set_music(pr, str(track), "epic", volume=0.2)
    pr.caption_style = {"size_scale": 1.2, "position": "middle"}
    pr.hook["seconds"] = 3.0
    pr.logo = {"path": str(logo), "corner": "top-middle"}
    pr.upload["description_footer"] = "Follow for more"
    for s in pr.segments:
        s.framing.mode = "fit_blur"
    root = str(tmp_path / "My Edits")
    T.save(pr, root)

    new = P.Project(title="Next one", channel="Sports")
    T.apply(new, T.load(root, "Sports"), new_edit=True)
    assert new.caption_style == {"size_scale": 1.2, "position": "middle"}
    assert new.audio["music"]["volume"] == 0.2 and new.logo["corner"] == "top-middle"
    assert new.upload == {"hashtags": "#gta6 #shorts", "description_footer": "Follow for more"}
    assert new.captions == [] and new.hook == {} and new.segments == []   # nothing edit-specific
    video = tmp_path / "w.mp4"
    video.write_bytes(b"x")
    src = new.add_source(P.Source(path=str(video), duration=60))
    assert new.add_segment(src.id, 1, 5).framing.mode == "fit_blur"

    old = _project(tmp_path, "Sports")
    old.upload = {"title": "Keep me", "hashtags": "#mine"}
    T.apply(old, T.load(root, "Sports"))
    assert old.upload["title"] == "Keep me" and old.upload["hashtags"] == "#mine"
    assert all(s.framing.mode == "fit_blur" for s in old.segments)
    assert T.load(root, "Kids") is None


def test_footer_goes_into_the_description(tmp_path):
    from clipping.editor import publish
    pr = _good(tmp_path)
    pr.upload["description_footer"] = "Follow for part 2"
    assert "What happened next\n\nFollow for part 2" in publish.upload_text(pr)


def test_old_edits_still_open(tmp_path):
    data = _good(tmp_path).to_dict()
    for key in ("review", "defaults", "logo"):
        data.pop(key)
    pr = P.Project.from_dict(data)
    assert pr.review == {} and pr.defaults == {}
