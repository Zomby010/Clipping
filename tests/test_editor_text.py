"""Clip Editor phase 2: styles, the .ass file, emoji, the render command and export files."""

import os
import shutil
import subprocess
from datetime import datetime

import pytest

from clipping.editor import captions as C
from clipping.editor import media, publish, render, styles, text_layers
from clipping.editor import project as P

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _project(tmp_path, channel="GTA 6"):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title="Text test", channel=channel)
    src = pr.add_source(P.Source(path=str(video), duration=60, width=1920, height=1080))
    pr.add_ranges(src.id, "0:10-0:14")
    words = [{"word": w, "src_start": 10 + i * 0.5, "src_end": 10.4 + i * 0.5}
             for i, w in enumerate(["no", "way", "bro"])]
    pr.captions = C.captions_from_words(pr, pr.segments[0], words)
    return pr


def test_channel_styles_and_overrides():
    gta = styles.caption_style("GTA 6")
    assert gta.font == "Montserrat Black" and gta.highlight == "FFE600"
    assert styles.caption_style("Sports").colour == "FFE600"
    assert styles.caption_style("Kids").highlight_cycle
    assert styles.caption_style("Nope") == styles.CHANNEL_STYLES["Other"]
    st = styles.caption_style("GTA 6", {"size_scale": 1.5, "position": "top", "word_highlight": False,
                                        "all_caps": False})
    assert (st.size, st.position, st.word_highlight, st.all_caps) == (126, "top", False, False)
    assert styles.caption_style("GTA 6", {"size_scale": 9}).size == int(round(84 * 1.6))


def test_split_emoji_and_colours():
    assert text_layers.split_emoji("BRO WHAT 💀") == ("BRO WHAT", "💀")
    assert text_layers.split_emoji("❤️🔥 yes") == ("yes", "❤️🔥")
    assert text_layers.ass_colour("FFE600") == "&H0000E6FF"
    assert text_layers.ass_time(62.35) == "0:01:02.35"


def test_ass_has_highlight_safe_margins_and_scales(tmp_path):
    pr = _project(tmp_path)
    full = text_layers.build_ass(pr, 1080, 1920)
    small = text_layers.build_ass(pr, 540, 960)
    assert "PlayResX: 1080" in full and "PlayResY: 960" in small
    style = next(line for line in full.splitlines() if line.startswith("Style: Caption"))
    fields = style.split(",")
    assert fields[2] == "84" and fields[-5:-1] == ["2", "86", "172", "460"]  # lower third, clear of buttons
    assert next(l for l in small.splitlines() if l.startswith("Style: Caption")).split(",")[2] == "42"
    events = [l for l in full.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 3  # one per word: the spoken word lights up
    assert "\\c&H0000E6FF" in events[1] and "WAY" in events[1] and events[1].startswith("Dialogue: 0,0:00:00.50")


def test_ass_without_highlight_and_kids_colours(tmp_path):
    pr = _project(tmp_path)
    pr.caption_style = {"word_highlight": False}
    events = [l for l in text_layers.build_ass(pr, 1080, 1920).splitlines() if l.startswith("Dialogue")]
    assert len(events) == 1 and events[0].endswith("NO WAY BRO")
    kids = _project(tmp_path, "Kids")
    text = text_layers.build_ass(kids, 1080, 1920)
    assert "Fredoka" in text and "no way" not in text.upper().replace("NO WAY", "")  # kids: not all caps
    cols = {styles.caption_style("Kids").highlight_cycle[i] for i in range(3)}
    for c in cols:
        assert text_layers.ass_colour(c) in text


def test_hook_and_popups_in_ass(tmp_path):
    pr = _project(tmp_path)
    pr.hook = {"text": "He did NOT see this coming 😳", "seconds": 3}
    pr.overlays.append({"type": "text", "start": 1.0, "end": 2.5, "text": "bro what 💀", "position": "0,2"})
    text = text_layers.build_ass(pr, 1080, 1920)
    hook = next(l for l in text.splitlines() if ",Hook," in l and l.startswith("Dialogue"))
    assert hook.startswith("Dialogue: 1,0:00:00.00,0:00:03.00") and hook.endswith("HE DID NOT SEE THIS COMING")
    pop = next(l for l in text.splitlines() if ",Popup," in l and l.startswith("Dialogue"))
    assert "0:00:01.00,0:00:02.50" in pop and "\\pos(734,345)" in pop and pop.endswith("BRO WHAT")
    assert text_layers.spot("top") == (0.46, 0.18) and text_layers.spot("nonsense") == (0.46, 0.42)


def test_no_text_means_no_ass(tmp_path):
    pr = _project(tmp_path)
    pr.captions = []
    assert text_layers.build_ass(pr, 1080, 1920) is None
    assert render.text_layers_for(pr, render.EXPORT, str(tmp_path)) is None


def test_emoji_pictures_or_a_warning(tmp_path):
    pr = _project(tmp_path)
    pr.overlays.append({"type": "text", "start": 1.0, "end": 2.0, "text": "💀", "position": "middle"})
    marks, warnings = text_layers.emoji_marks(pr, 1080, 1920, str(tmp_path), None)
    assert marks == [] and warnings == [styles.EMOJI_HELP]
    font = styles.emoji_font()
    if font:
        marks, warnings = text_layers.emoji_marks(pr, 1080, 1920, str(tmp_path), font)
        assert len(marks) == 1 and os.path.exists(marks[0].png) and (marks[0].start, marks[0].end) == (1.0, 2.0)


def test_command_burns_text_and_emoji_in_one_pass(tmp_path):
    pr = _project(tmp_path)
    layers = text_layers.TextLayers(ass_path="/tmp/x/subs.ass", fonts_dir="/tmp/x/fonts",
                                    emojis=[text_layers.EmojiMark("/tmp/x/e0.png", 10, 20, 1.0, 2.5)])
    cmd = render.build_command(pr, "o.mp4", render.EXPORT, layers)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[vcat]ass=filename=/tmp/x/subs.ass:fontsdir=/tmp/x/fonts[vass]" in graph
    assert "[vass][1:v]overlay=x=10:y=20:eof_action=repeat:enable='between(t,1,2.5)'[ve0]" in graph
    assert "[ve0]null[vout]" in graph and cmd.count("-i") == 2
    assert render._filter_path("/a b/c:d") == "/a\\ b/c\\:d"


def test_preview_cache_key_changes_with_text(tmp_path):
    pr = _project(tmp_path)
    before = render.edit_fingerprint(pr, render.PREVIEW)
    C.set_text(pr.captions[0], "changed words here")
    assert render.edit_fingerprint(pr, render.PREVIEW) != before
    mid = render.edit_fingerprint(pr, render.PREVIEW)
    pr.hook = {"text": "hook", "seconds": 2}
    assert render.edit_fingerprint(pr, render.PREVIEW) != mid


def test_phase1_project_file_still_opens(tmp_path):
    old = {"title": "Old", "channel": "Sports", "mode": "explain", "angle": "", "version": 1,
           "sources": [{"path": "/x.mp4", "name": "x", "duration": 10, "width": 1920, "height": 1080,
                        "fps": 30, "has_audio": True, "id": "src_1"}],
           "segments": [{"source_id": "src_1", "source_in": 1, "source_out": 3, "speed": 1.0, "join": "cut",
                         "framing": {"mode": "auto", "pan_x": 0.5, "pan_y": 0.5, "zoom": 1.0,
                                     "auto_x": None, "auto_y": None}, "note": "", "id": "seg_1"}],
           "script_lines": [], "captions": [], "overlays": [], "audio": {}, "exports": [{"path": "a.mp4"}],
           "created": "2026-11-22T10:00:00"}
    pr = P.Project.from_dict(old)
    again = pr.to_dict()
    for key, value in old.items():
        assert again[key] == value
    assert pr.hook == {} and pr.upload == {} and pr.cover == {} and pr.caption_style == {}


def test_upload_text_and_kids_reminder(tmp_path):
    pr = _project(tmp_path, "Kids")
    pr.upload = {"title": "Tortoise wins", "description": "A tale", "hashtags": "#kids  #stories"}
    text = publish.upload_text(pr)
    assert text.startswith("TITLE\nTortoise wins\n") and "HASHTAGS\n#kids #stories" in text
    assert "kids, stories" in text and "made for kids" in text
    pr.upload = {}
    assert publish.upload_fields(pr)["hashtags"] == "#kids #stories #shorts"
    pr.upload = {"title": "x" * 150}
    assert "x" * 101 not in publish.upload_text(pr)


@needs_ffmpeg
def test_export_writes_video_cover_captions_and_upload_text(tmp_path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=duration=8", "-shortest", "-pix_fmt", "yuv420p",
                    str(tmp_path / "src.mp4")], check=True)
    pr = P.Project(title="Full export", channel="Sports")
    src = pr.add_source(media.probe(str(tmp_path / "src.mp4")))
    pr.add_ranges(src.id, "0:01-0:03, 0:05-0:07")
    words = [{"word": "what", "src_start": 1.2, "src_end": 1.6}, {"word": "a", "src_start": 1.6, "src_end": 1.8},
             {"word": "goal", "src_start": 5.2, "src_end": 5.8}]
    for seg in pr.segments:
        pr.captions += C.captions_from_words(pr, seg, words)
    pr.hook = {"text": "Wait for it 😳", "seconds": 1.5}
    pr.overlays.append({"type": "text", "start": 2.0, "end": 3.0, "text": "GOAL 🔥", "position": "top"})
    pr.cover = {"time": 2.5, "title": "What a goal"}
    pr.upload = {"title": "What a goal", "description": "", "hashtags": "#football"}
    rec = render.export(pr, str(tmp_path / "Ready"), fonts={}, now=datetime(2026, 11, 22, 20, 0))
    files = sorted(os.listdir(rec["folder"]))
    assert files == ["What a goal.mp4", "YouTube.txt", "captions.srt", "cover.jpg"]
    out = media.probe(rec["path"])
    assert (out.width, out.height) == (1080, 1920) and abs(out.duration - 4) < 0.15
    srt = open(os.path.join(rec["folder"], "captions.srt"), encoding="utf-8").read()
    assert "00:00:00,200 --> 00:00:00,800" in srt and "goal" in srt and "00:00:02,200" in srt
    from PIL import Image
    with Image.open(os.path.join(rec["folder"], "cover.jpg")) as im:
        assert im.size == (1080, 1920)
    again = render.export(pr, str(tmp_path / "Ready"), fonts={}, now=datetime(2026, 11, 22, 20, 0))
    assert again["folder"].endswith("(2)")  # a second export never overwrites the first
