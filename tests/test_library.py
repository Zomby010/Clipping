"""Tests for the tidy 'My Clips' folder and caption export."""

import importlib.util
import os
from types import SimpleNamespace

from clipping import library

_PATH = os.path.join(os.path.dirname(__file__), "..", "clipping", "studio", "captions_export.py")
_spec = importlib.util.spec_from_file_location("captions_export_under_test", _PATH)
captions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(captions)


def _seg(s, e, text):
    return {"start": s, "end": e, "text": text, "words": [{"word": text, "start": s, "end": e}]}


def test_caption_lines_clip_to_main_clip():
    lines = captions.caption_lines([_seg(95, 99, "before"), _seg(101, 103, "inside")], 100, 130)
    assert lines == [{"start": 1.0, "end": 3.0, "text": "inside"}]


def test_caption_lines_follow_trimmed_segments():
    keep = [{"start_time": 100, "end_time": 110}, {"start_time": 200, "end_time": 210}]
    lines = captions.caption_lines([_seg(150, 151, "cut out"), _seg(201, 202, "second")], 100, 210, keep)
    assert lines == [{"start": 11.0, "end": 12.0, "text": "second"}]


def test_srt_format_with_offset():
    srt = captions.to_srt([{"start": 1.0, "end": 2.5, "text": "hello"}], offset=3.0)
    assert srt.splitlines()[:3] == ["1", "00:00:04,000 --> 00:00:05,500", "hello"]


def test_safe_name_strips_bad_characters_and_hashtags():
    assert library.safe_name('GTA 6: "Bridge" chase?! #gta6 #shorts') == "GTA 6 Bridge chase!"


def test_organize_run_sorts_files_and_updates_manifest(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    video = out / "highlight_rank_1_ready.mp4"
    video.write_bytes(b"video")
    thumb = out / "thumbnail_rank_1.jpg"
    thumb.write_bytes(b"jpg")
    vo = out / "vo_clip_1.mp3"
    vo.write_bytes(b"mp3")
    (out / "vo_clip_1.vtt").write_text("x")
    manifest = [{
        "rank": 1, "status": "success",
        "video_path": str(video), "thumbnail_path": str(thumb),
        "youtube_title_final": "Bridge chase goes wrong",
        "youtube_description_final": "desc", "youtube_tags_final": ["gta 6"], "hastag": "#gta6",
        "caption_lines": [{"start": 0.5, "end": 1.0, "text": "hi"}], "main_duration": 30.0,
        "voiceover_audio_path": str(vo), "voiceover_script": "What a chase.",
    }]
    cfg = SimpleNamespace(base_dir=str(tmp_path), outputs_dir=str(out), url_youtube="https://youtu.be/x")
    run_dir = library.organize_run(manifest, cfg)

    assert os.path.dirname(run_dir) == str(tmp_path / "My Clips")
    name = "1 - Bridge chase goes wrong"
    assert os.path.isfile(os.path.join(run_dir, "Videos", name + ".mp4"))
    assert os.path.isfile(os.path.join(run_dir, "Thumbnails", name + ".jpg"))
    assert os.path.isfile(os.path.join(run_dir, "Captions", name + ".srt"))
    assert os.path.isfile(os.path.join(run_dir, "Voice-overs", name + ".mp3"))
    assert os.path.isfile(os.path.join(run_dir, "Voice-overs", name + " (script).txt"))
    assert "Bridge chase goes wrong" in open(os.path.join(run_dir, "Upload details", name + ".txt")).read()
    assert os.path.isfile(os.path.join(run_dir, "About this folder.txt"))
    # Moved, not copied; manifest points at the new place; bulky caption data removed
    assert not video.exists() and not vo.exists() and not (out / "vo_clip_1.vtt").exists()
    assert manifest[0]["video_path"].endswith(name + ".mp4")
    assert "caption_lines" not in manifest[0]


def test_organize_run_skips_failed_runs(tmp_path):
    manifest = [{"rank": 1, "status": "failed", "video_path": "", "caption_lines": []}]
    cfg = SimpleNamespace(base_dir=str(tmp_path), outputs_dir=str(tmp_path), url_youtube="")
    assert library.organize_run(manifest, cfg) is None
    assert "caption_lines" not in manifest[0]
    assert not (tmp_path / "My Clips").exists()
