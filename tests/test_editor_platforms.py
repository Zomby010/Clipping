"""Clip Editor phase 6: text per platform, button areas, export several, upload list, My Clips import."""

import json
import os
import shutil
import subprocess
import threading

import pytest

from clipping import library
from clipping.editor import batch, importer, platforms, publish, render
from clipping.editor import project as P

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _project(tmp_path, title="Big crash", channel="GTA 6"):
    video = tmp_path / f"{title}.mp4"
    video.write_bytes(b"x")
    pr = P.Project(title=title, channel=channel)
    src = pr.add_source(P.Source(path=str(video), duration=300, width=1920, height=1080))
    pr.add_ranges(src.id, "1:00-1:10")
    pr.upload = {"title": "He did NOT see this coming", "description": "Watch till the end",
                 "hashtags": "#gta6 #shorts #funny"}
    return pr


def _make_video(path, size="360x640", seconds=1.5):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=30:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-shortest", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return str(path)


# ------------------------------------------------------------------ text per platform
def test_platform_text_follows_youtube_until_edited(tmp_path):
    pr = _project(tmp_path)
    assert platforms.text_for(pr, "tiktok")["caption"] == "He did NOT see this coming #gta6 #shorts #funny"
    fb = platforms.text_for(pr, "facebook")
    assert fb["title"] == "He did NOT see this coming"
    assert "Watch till the end" in fb["description"] and "#gta6" in fb["description"]
    assert platforms.text_for(pr, "instagram")["caption"].startswith("He did NOT see this coming\n\n")

    assert platforms.set_text(pr, "tiktok", "caption", "no way bro #gta6")
    assert platforms.is_edited(pr, "tiktok", "caption")
    pr.upload["title"] = "New title"
    assert platforms.text_for(pr, "tiktok")["caption"] == "no way bro #gta6"      # edited: kept
    assert platforms.text_for(pr, "facebook")["title"] == "New title"           # not edited: follows

    # typing the default back stops storing it
    assert platforms.set_text(pr, "tiktok", "caption", "New title #gta6 #shorts #funny")
    assert "tiktok" not in pr.upload
    platforms.set_text(pr, "facebook", "title", "Own title")
    platforms.follow_youtube(pr, "facebook")
    assert platforms.text_for(pr, "facebook")["title"] == "New title"


def test_platform_text_survives_save_and_load(tmp_path):
    pr = _project(tmp_path)
    platforms.set_text(pr, "instagram", "caption", "hook\n\n#gta6")
    folder = tmp_path / "edit"
    P.save(pr, str(folder))
    again, _ = P.load(str(folder))
    assert platforms.text_for(again, "instagram")["caption"] == "hook\n\n#gta6"
    assert again.upload["title"] == "He did NOT see this coming"


def test_limits_and_counter(tmp_path):
    pr = _project(tmp_path)
    assert all(platforms.limits(pr, p) == [] for p in platforms.PLATFORMS)
    platforms.set_text(pr, "tiktok", "caption", "x" * 2300)
    platforms.set_text(pr, "instagram", "caption", " ".join(f"#t{i}" for i in range(35)))
    platforms.set_text(pr, "facebook", "title", "y" * 300)
    assert "2300 characters; the limit is 2200" in platforms.limits(pr, "tiktok")[0]
    assert "allows 30 hashtags; this has 35" in platforms.limits(pr, "instagram")[0]
    assert "title is 300 characters" in platforms.limits(pr, "facebook")[0]
    assert platforms.counter(pr, "tiktok", "caption").startswith("2300 / 2200 (people see the first 150")
    assert platforms.counter(pr, "facebook", "title") == "300 / 255"


def test_instagram_default_caps_hashtags(tmp_path):
    pr = _project(tmp_path)
    pr.upload["hashtags"] = " ".join(f"#t{i}" for i in range(40))
    caption = platforms.text_for(pr, "instagram")["caption"]
    assert len(platforms._hashtags(caption).split()) == 30


def test_platform_files_written_on_export(tmp_path):
    pr = _project(tmp_path, channel="Kids")
    platforms.set_text(pr, "tiktok", "caption", "tiny caption")
    written = platforms.write_files(pr, str(tmp_path))
    assert sorted(os.path.basename(p) for p in written) == ["Facebook.txt", "Instagram.txt", "TikTok.txt"]
    tiktok = (tmp_path / "TikTok.txt").read_text()
    assert tiktok.startswith("CAPTION\ntiny caption\n") and "KIDS" in tiktok
    fb = (tmp_path / "Facebook.txt").read_text()
    assert fb.startswith("TITLE\nHe did NOT see this coming\n\nDESCRIPTION\n")


def test_claude_writes_platform_text(tmp_path):
    pr = _project(tmp_path, channel="Kids")
    seen = {}

    def asker(cfg, prompt, schema=None, label=None):
        seen["prompt"] = prompt
        return {"tiktok_caption": "Wait for it #kids", "instagram_caption": "So fun\n\n#kids",
                "facebook_title": "F" * 400, "facebook_description": "Two lines\n#kids"}

    got = platforms.write_with_claude(pr, asker=asker)
    assert "children's channel" in seen["prompt"] and "He did NOT see this coming" in seen["prompt"]
    assert len(got["facebook"]["title"]) == 255                       # cut to the limit
    platforms.apply_claude(pr, got)
    assert platforms.text_for(pr, "tiktok")["caption"] == "Wait for it #kids"
    assert platforms.text_for(pr, "instagram")["caption"] == "So fun\n\n#kids"


# ------------------------------------------------------------------ button areas
def test_zones_per_platform_fit_inside_the_combined_zones():
    combined = {name: (x, y, w, h) for name, x, y, w, h in render.SAFE_ZONES}
    for platform, zones in platforms.PLATFORM_SAFE_ZONES.items():
        assert platforms.safe_zones(platform) == zones
        for name, x, y, w, h in zones:
            cx, cy, cw, ch = combined[name]
            assert x >= cx - 1e-9 and y >= cy - 1e-9, (platform, name)
            assert x + w <= cx + cw + 1e-9 and y + h <= cy + ch + 1e-9, (platform, name)
    assert platforms.safe_zones(None) == render.SAFE_ZONES
    assert [name for name, _ in platforms.ZONE_CHOICES][0] == "All"


def test_zone_filter_follows_the_chosen_platform():
    tiktok = render.safe_zone_filter(platforms.safe_zones("tiktok"))
    assert tiktok.count("drawbox") == 3 and "ih*0.8" in tiktok
    assert render.safe_zone_filter() == render.safe_zone_filter(render.SAFE_ZONES)


# ------------------------------------------------------------------ export several
def _save_edit(root, tmp_path, title, broken=False):
    pr = _project(tmp_path, title=title)
    if broken:
        pr.segments.clear()
    folder = os.path.join(root, title)
    P.save(pr, folder)
    return folder


def _fake_exporter(calls):
    def exporter(pr, ready_root, on_progress=None, log=None, fonts=None):
        if on_progress:
            on_progress(1.0)
        folder = os.path.join(ready_root, pr.title)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, pr.title + ".mp4")
        with open(path, "wb") as f:
            f.write(b"video")
        record = {"path": path, "folder": folder, "length": pr.total_length, "at": "2026-11-22T10:00:00"}
        pr.exports.append(record)
        calls.append(pr.title)
        return record
    return exporter


def test_list_edits_newest_first_and_today(tmp_path):
    root = str(tmp_path / "My Edits")
    old = _save_edit(root, tmp_path, "Old one")
    os.utime(os.path.join(old, P.PROJECT_FILE), (1_600_000_000, 1_600_000_000))
    _save_edit(root, tmp_path, "New one")
    _save_edit(root, tmp_path, "Empty one", broken=True)
    os.makedirs(os.path.join(root, "Templates"))
    edits = batch.list_edits(root)
    assert [e.title for e in edits][-1] == "Old one"
    assert {e.title for e in batch.todays_edits(root)} == {"New one", "Empty one"}
    empty = next(e for e in edits if e.title == "Empty one")
    assert empty.problem


def test_export_many_skips_broken_and_fills_the_upload_list(tmp_path):
    root, ready = str(tmp_path / "My Edits"), str(tmp_path / "Ready")
    folders = [_save_edit(root, tmp_path, "One"), _save_edit(root, tmp_path, "Broken", broken=True),
               _save_edit(root, tmp_path, "Two")]
    calls, progress = [], []
    report = batch.export_many(folders, ready, on_progress=lambda i, n, f: progress.append((i, n)),
                               exporter=_fake_exporter(calls))
    assert calls == ["One", "Two"]
    assert [r["ok"] for r in report] == [True, False, True]
    assert "empty" in report[1]["skipped"]
    assert batch.describe_report(report).startswith("Exported 2 of 3.")
    assert progress[-1] == (3, 3)
    rows = batch.load_manifest(ready)
    assert [r["youtube_title_final"] for r in rows] == ["He did NOT see this coming"] * 2
    assert [r["rank"] for r in rows] == [1, 2]
    again, _ = P.load(folders[0])
    assert again.exports and again.exports[-1]["path"] == rows[0]["video_path"]


def test_export_many_stop_finishes_the_current_video(tmp_path):
    root, ready = str(tmp_path / "My Edits"), str(tmp_path / "Ready")
    folders = [_save_edit(root, tmp_path, n) for n in ("A", "B", "C")]
    stop, calls = threading.Event(), []
    inner = _fake_exporter(calls)

    def exporter(pr, *a, **k):
        record = inner(pr, *a, **k)
        stop.set()      # Stop pressed while the first video was being made
        return record

    report = batch.export_many(folders, ready, stop=stop, exporter=exporter)
    assert calls == ["A"]
    assert report[0]["ok"] and report[1]["skipped"] == "Stopped before this one."


def test_export_many_names_a_failure_and_goes_on(tmp_path):
    root, ready = str(tmp_path / "My Edits"), str(tmp_path / "Ready")
    folders = [_save_edit(root, tmp_path, "A"), _save_edit(root, tmp_path, "B")]
    calls = []
    inner = _fake_exporter(calls)

    def exporter(pr, *a, **k):
        if pr.title == "A":
            raise RuntimeError("ffmpeg said no")
        return inner(pr, *a, **k)

    report = batch.export_many(folders, ready, exporter=exporter)
    assert report[0]["skipped"] == "Failed: ffmpeg said no" and report[1]["ok"]


@needs_ffmpeg
def test_export_many_real_videos(tmp_path):
    root, ready = str(tmp_path / "My Edits"), str(tmp_path / "Ready")
    folders = []
    for name in ("Real one", "Real two"):
        pr = P.Project(title=name)
        from clipping.editor import media
        src = pr.add_source(media.probe(_make_video(tmp_path / f"{name}.mp4", "640x360", 1.5)))
        pr.add_segment(src.id, 0.0, 1.0)
        folders.append(os.path.join(root, name))
        P.save(pr, folders[-1])
    report = batch.export_many(folders, ready)
    assert all(r["ok"] for r in report), report
    rows = batch.load_manifest(ready)
    assert len(rows) == 2 and all(os.path.getsize(r["video_path"]) > 0 for r in rows)
    for r in rows:
        files = os.listdir(os.path.dirname(r["video_path"]))
        assert {"TikTok.txt", "Instagram.txt", "Facebook.txt", "YouTube.txt"} <= set(files)


# ------------------------------------------------------------------ upload list
def _record(tmp_path, name="v"):
    folder = tmp_path / "Ready" / name
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / f"{name}.mp4"
    video.write_bytes(b"video")
    (folder / "cover.jpg").write_bytes(b"jpg")
    return {"path": str(video), "folder": str(folder), "length": 10.0, "at": "2026-11-22T10:00:00"}


def test_manifest_add_update_and_keep_uploaded_rows(tmp_path):
    ready = str(tmp_path / "Ready")
    a, b = _project(tmp_path, "A"), _project(tmp_path, "B")
    b.created = a.created + "-b"
    row = batch.add_to_manifest(ready, a, _record(tmp_path, "a1"))
    assert row["rank"] == 1 and row["status"] == "success" and row["thumbnail_path"].endswith("cover.jpg")
    assert row["source"] == "Clip Editor" and row["made_for_kids"] is False
    assert row["facebook_title"] == "He did NOT see this coming"
    batch.add_to_manifest(ready, b, _record(tmp_path, "b1"))

    a.upload["title"] = "Better title"
    batch.add_to_manifest(ready, a, _record(tmp_path, "a2"))        # re-export: same row, same rank
    rows = batch.load_manifest(ready)
    assert len(rows) == 2 and rows[0]["rank"] == 1 and rows[0]["youtube_title_final"] == "Better title"

    rows[0]["youtube_video_id"] = "abc123"                            # the uploader marked it
    batch._save_manifest(ready, rows)
    batch.add_to_manifest(ready, a, _record(tmp_path, "a3"))
    rows = batch.load_manifest(ready)
    assert len(rows) == 3
    assert rows[0]["youtube_video_id"] == "abc123" and rows[0]["youtube_title_final"] == "Better title"
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert json.loads((tmp_path / "Ready" / batch.MANIFEST_FILE).read_text())[2]["video_path"].endswith("a3.mp4")


def test_manifest_survives_a_broken_file(tmp_path):
    ready = tmp_path / "Ready"
    ready.mkdir()
    (ready / batch.MANIFEST_FILE).write_text("{not json")
    assert batch.load_manifest(str(ready)) == []
    batch.add_to_manifest(str(ready), _project(tmp_path), _record(tmp_path))
    assert len(batch.load_manifest(str(ready))) == 1


def test_facebook_uploader_reads_the_upload_list(tmp_path):
    fb = pytest.importorskip("facebook_uploader.uploader")
    ready = str(tmp_path / "Ready")
    batch.add_to_manifest(ready, _project(tmp_path), _record(tmp_path))
    candidates = fb.get_upload_candidates(batch.load_manifest(ready))
    assert len(candidates) == 1 and candidates[0]["facebook_title"] == "He did NOT see this coming"


def test_youtube_uploader_reads_the_upload_list(tmp_path):
    pytest.importorskip("google.auth")
    yt = pytest.importorskip("youtube_uploader.uploader")
    ready = str(tmp_path / "Ready")
    batch.add_to_manifest(ready, _project(tmp_path), _record(tmp_path))
    assert len(yt.get_upload_candidates(batch.load_manifest(ready))) == 1


def test_upload_commands_point_at_the_list(tmp_path):
    cmds = batch.upload_commands(str(tmp_path / "Ready"))
    assert cmds["youtube"].startswith("python run_upload.py --manifest-file")
    assert cmds["facebook"].startswith("python run_fb_upload.py --manifest-file")
    assert "--tz-name Africa/Nairobi" in cmds["youtube"] and batch.MANIFEST_FILE in cmds["facebook"]


# ------------------------------------------------------------------ My Clips import
def test_parse_upload_details_from_clipping_studio():
    text = library._upload_text({
        "youtube_title_final": "Crazy heist", "youtube_description_final": "Line one\n\nLine two",
        "hastag": "#gta6 #shorts", "youtube_tags_final": ["gta6"], "tiktok_caption_final": "cap",
        "story_episode": {"mode": "series", "parts": 3, "part": 1, "series_title": "S", "episode_title": "E"},
    })
    got = importer.parse_upload_details(text)
    assert got == {"title": "Crazy heist", "description": "Line one\n\nLine two", "hashtags": "#gta6 #shorts"}


def test_guess_channel():
    assert importer.guess_channel("#shorts #GTA6") == "GTA 6"
    assert importer.guess_channel("#football #goal") == "Sports"
    assert importer.guess_channel("#shorts") is None


@needs_ffmpeg
def test_import_a_clipping_studio_clip(tmp_path):
    run = tmp_path / "My Clips" / "2026-11-22 10-00 - Heist"
    (run / "Videos").mkdir(parents=True)
    (run / "Upload details").mkdir()
    video = _make_video(run / "Videos" / "1 - Crazy heist.mp4")
    _make_video(run / "Videos" / "2 - Second.mp4")
    (run / "Upload details" / "1 - Crazy heist.txt").write_text(library._upload_text({
        "youtube_title_final": "Crazy heist", "youtube_description_final": "Watch this",
        "hastag": "#football #shorts"}))

    runs = importer.list_runs(str(tmp_path / "My Clips"))
    assert len(runs) == 1 and [os.path.basename(v) for v in runs[0][1]] == ["1 - Crazy heist.mp4", "2 - Second.mp4"]

    pr = importer.import_clip(video, channel="GTA 6")
    assert pr.title == "Crazy heist" and pr.channel == "Sports"
    assert len(pr.segments) == 1 and pr.segments[0].framing.mode == "fit_blur"
    assert abs(pr.segments[0].source_out - 1.5) < 0.2
    assert pr.upload == {"title": "Crazy heist", "description": "Watch this", "hashtags": "#football #shorts"}
    assert publish.upload_item(pr)["youtube_title_final"] == "Crazy heist"
    assert pr.review["note"] == importer.BURNED_IN_NOTE and not pr.captions

    plain = importer.import_clip(str(run / "Videos" / "2 - Second.mp4"))
    assert plain.title == "Second" and plain.channel == "GTA 6" and plain.upload == {}
