"""ClipsAI and Postiz add-ons, and the tools Claude runs (clipping/claude_tools.py, clipping_mcp.py)."""

import io
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clipping import claude_tools
from clipping.editor import batch
from clipping.editor import project as P
from clipping.integrations import clipsai_tool as CA
from clipping.integrations import postiz as PZ

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


# ================================================================ Postiz
class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakePostiz:
    """Records every request; answers like the Postiz public API."""

    def __init__(self, channels, post_status=200):
        self.channels = channels
        self.calls = []
        self.post_status = post_status

    def request(self, method, url, headers=None, timeout=None, **kw):
        path = url.split("/public/v1", 1)[1]
        self.calls.append((method, path, headers, kw))
        if path == "/integrations":
            return FakeResponse(200, self.channels)
        if path == "/upload":
            return FakeResponse(200, {"id": f"m{len(self.calls)}", "path": f"https://u.example/v{len(self.calls)}.mp4"})
        if path == "/posts":
            if self.post_status != 200:
                return FakeResponse(self.post_status, {"error": "x"})
            return FakeResponse(200, [{"postId": f"p-{p['integration']['id']}", "integration": p["integration"]["id"]}
                                      for p in kw["json"]["posts"]])
        return FakeResponse(404, {})

    def paths(self, method=None):
        return [p for m, p, *_ in self.calls if method is None or m == method]


CHANNELS = [
    {"id": "tt1", "name": "GTA Laughs", "identifier": "tiktok", "disabled": False},
    {"id": "ig1", "name": "gtalaughs", "identifier": "instagram", "disabled": False},
    {"id": "yt1", "name": "GTA Laughs YT", "identifier": "youtube", "disabled": False},
    {"id": "fb1", "name": "GTA Laughs Page", "identifier": "facebook", "disabled": False},
    {"id": "x1", "name": "on x", "identifier": "x", "disabled": False},
    {"id": "tt2", "name": "old", "identifier": "tiktok", "disabled": True},
]


_RealClient = PZ.Client


def _client(fake):
    return _RealClient(url="https://postiz.example.com", api_key="k123", session=fake)


def _row(tmp_path, rank=1, kids=False, name="v"):
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"video")
    return {"rank": rank, "status": "success", "video_path": str(video),
            "youtube_title_final": "He did NOT see this coming", "youtube_description_final": "Watch till the end",
            "hastag": "#gta6 #shorts", "youtube_tags_final": ["gta6", "shorts"],
            "tiktok_caption_final": "no way bro #gta6", "instagram_caption": "hook\n\n#gta6",
            "facebook_title": "FB title", "facebook_description": "FB words", "made_for_kids": kids}


def _ready(tmp_path, rows):
    ready = tmp_path / "Ready"
    ready.mkdir(exist_ok=True)
    batch._save_manifest(str(ready), rows)
    return str(ready)


def test_url_forms_people_paste():
    assert PZ.normalise_url("") == PZ.CLOUD_URL
    assert PZ.normalise_url("https://api.postiz.com") == PZ.CLOUD_URL
    assert PZ.normalise_url("postiz.example.com") == "https://postiz.example.com/api/public/v1"
    assert PZ.normalise_url("https://postiz.example.com/api/") == "https://postiz.example.com/api/public/v1"
    assert PZ.normalise_url("http://localhost:5000/api/public/v1") == "http://localhost:5000/api/public/v1"


def test_settings_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    monkeypatch.delenv("POSTIZ_URL", raising=False)
    env = tmp_path / ".env"
    env.write_text('GOOGLE_API_KEY=x\nPOSTIZ_API_KEY="abc"\nPOSTIZ_URL=postiz.example.com\n')
    assert PZ.settings(str(env)) == {"url": "https://postiz.example.com/api/public/v1", "api_key": "abc"}
    env.write_text("POSTIZ_API_KEY=your-postiz-key\n")
    assert PZ.settings(str(env))["api_key"] == ""


def test_no_key_is_a_plain_message(monkeypatch):
    monkeypatch.setattr(PZ, "settings", lambda *a: {"url": PZ.CLOUD_URL, "api_key": ""})
    with pytest.raises(PZ.PostizError, match="POSTIZ_API_KEY"):
        PZ.Client(session=object())


def test_key_goes_in_the_authorization_header():
    fake = FakePostiz(CHANNELS)
    assert len(_client(fake).channels()) == len(CHANNELS)
    method, path, headers, _ = fake.calls[0]
    assert (method, path, headers["Authorization"]) == ("GET", "/integrations", "k123")


def test_pick_channels():
    assert [c["id"] for c in PZ.pick_channels(CHANNELS, None)] == ["tt1", "ig1", "yt1", "fb1"]  # no X, no disabled
    assert [c["id"] for c in PZ.pick_channels(CHANNELS, ["TikTok", "ig1"])] == ["tt1", "ig1"]
    with pytest.raises(PZ.PostizError, match="No connected Postiz channel called 'pinterest'"):
        PZ.pick_channels(CHANNELS, ["pinterest"])


def test_post_body_per_platform(tmp_path):
    row = _row(tmp_path, kids=True)
    when = datetime(2026, 11, 25, 18, 0, tzinfo=ZoneInfo("Africa/Nairobi"))
    body = PZ.build_post(row, PZ.pick_channels(CHANNELS, None), {"id": "m1", "path": "https://u/v.mp4"}, when)
    assert body["type"] == "schedule" and body["date"] == "2026-11-25T15:00:00.000Z"
    by = {p["integration"]["id"]: p for p in body["posts"]}
    assert by["tt1"]["value"][0]["content"] == "no way bro #gta6"
    assert by["tt1"]["settings"]["__type"] == "tiktok"
    assert by["tt1"]["settings"]["content_posting_method"] == "DIRECT_POST"
    assert by["tt1"]["settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert by["ig1"]["settings"] == {"__type": "instagram", "post_type": "post"}
    assert by["ig1"]["value"][0]["content"] == "hook\n\n#gta6"
    yt = by["yt1"]["settings"]
    assert yt["title"] == "He did NOT see this coming" and yt["type"] == "public"
    assert yt["selfDeclaredMadeForKids"] == "yes" and yt["tags"][0] == {"value": "gta6", "label": "gta6"}
    assert by["fb1"]["settings"] == {"__type": "facebook", "title": "FB title"}
    assert by["fb1"]["value"][0]["content"] == "FB words"
    assert all(p["value"][0]["image"] == [{"id": "m1", "path": "https://u/v.mp4"}] for p in body["posts"])
    assert PZ.build_post(row, CHANNELS[:1], {"id": "m", "path": "p"}, None)["type"] == "now"


def test_long_titles_are_cut_to_the_limits(tmp_path):
    row = _row(tmp_path)
    row["youtube_title_final"] = "x" * 150
    tt = PZ.settings_for(row, CHANNELS[0])
    yt = PZ.settings_for(row, CHANNELS[2])
    assert len(tt["title"]) == 90 and len(yt["title"]) == 100


def test_schedule_times():
    tz = ZoneInfo("Africa/Nairobi")
    start = (datetime.now(tz) + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    times = PZ.schedule_times(3, start.strftime("%Y-%m-%d %H:%M"), 12)
    assert [t.hour for t in times] == [18, 6, 18] and times[0].tzinfo is not None
    assert PZ.schedule_times(2, None, 12) == [None, None]
    with pytest.raises(PZ.PostizError, match="in the past"):
        PZ.schedule_times(1, "2020-01-01 10:00", 12)
    with pytest.raises(PZ.PostizError, match="like"):
        PZ.schedule_times(1, "tomorrow", 12)


def test_dry_run_sends_nothing(tmp_path):
    ready = _ready(tmp_path, [_row(tmp_path)])
    fake = FakePostiz(CHANNELS)
    report = PZ.send_manifest(ready, ["tiktok", "instagram"], client=_client(fake), log=lambda *_: None)
    assert fake.paths() == ["/integrations"]
    assert report[0]["sent"] is False and len(report[0]["body"]["posts"]) == 2
    assert "postiz" not in batch.load_manifest(ready)[0]
    assert PZ.describe(report, True).startswith("Dry run: 1 video(s) would be sent. Nothing was posted.")


def test_send_uploads_once_marks_rows_and_never_sends_twice(tmp_path):
    ready = _ready(tmp_path, [_row(tmp_path, 1, name="a"), _row(tmp_path, 2, name="b")])
    fake = FakePostiz(CHANNELS)
    report = PZ.send_manifest(ready, ["tiktok", "instagram"], dry_run=False, client=_client(fake), log=lambda *_: None)
    assert [r["sent"] for r in report] == [True, True]
    assert fake.paths("POST") == ["/upload", "/posts", "/upload", "/posts"]       # one upload, one post per video
    rows = batch.load_manifest(ready)
    assert set(rows[0]["postiz"]) == {"tt1", "ig1"} and rows[0]["postiz"]["tt1"]["post_id"] == "p-tt1"
    assert rows[0]["postiz_media"]["path"].startswith("https://u.example/")
    assert batch.is_uploaded(rows[0])

    again = PZ.send_manifest(ready, ["tiktok", "instagram"], dry_run=False, client=_client(fake), log=lambda *_: None)
    assert again == [] and fake.paths("POST").count("/posts") == 2

    # adding Facebook later sends only to Facebook, and reuses the uploaded video
    fake2 = FakePostiz(CHANNELS)
    PZ.send_manifest(ready, ["tiktok", "facebook"], dry_run=False, client=_client(fake2), log=lambda *_: None)
    assert fake2.paths("POST") == ["/posts", "/posts"]
    posted = [kw["json"] for m, p, h, kw in fake2.calls if p == "/posts"]
    assert all([x["integration"]["id"] for x in b["posts"]] == ["fb1"] for b in posted)


def test_too_many_posts_stops_and_keeps_the_list(tmp_path):
    ready = _ready(tmp_path, [_row(tmp_path, 1, name="a"), _row(tmp_path, 2, name="b")])
    fake = FakePostiz(CHANNELS, post_status=429)
    report = PZ.send_manifest(ready, ["tiktok"], dry_run=False, client=_client(fake), log=lambda *_: None)
    assert len(report) == 1 and "too many" in report[0]["error"]
    assert "postiz" not in batch.load_manifest(ready)[0]


def test_missing_video_is_left_out(tmp_path):
    row = _row(tmp_path)
    os.remove(row["video_path"])
    ready = _ready(tmp_path, [row])
    assert PZ.send_manifest(ready, None, client=_client(FakePostiz(CHANNELS)), log=lambda *_: None) == []


def test_command_line_channels(monkeypatch, capsys):
    fake = FakePostiz(CHANNELS)
    monkeypatch.setattr(PZ, "Client", lambda: _client(fake))
    assert PZ.main(["channels"]) == 0
    out = capsys.readouterr().out
    assert "tiktok     GTA Laughs  id=tt1" in out and "(disabled)" in out and "not used" in out


def test_manifest_rows_carry_the_instagram_caption(tmp_path):
    from clipping.editor import platforms
    pr = P.Project(title="t")
    pr.upload = {"title": "Big crash", "hashtags": "#gta6"}
    platforms.set_text(pr, "instagram", "caption", "my reel words")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    row = batch.manifest_row(pr, {"path": str(video), "folder": str(tmp_path)}, 1)
    assert row["instagram_caption"] == "my reel words"


# ================================================================ ClipsAI
def _video(tmp_path, name="talk.mp4"):
    v = tmp_path / name
    v.write_bytes(b"video")
    return str(v)


def test_find_clips_filters_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(CA, "CACHE_DIR", str(tmp_path / "cache"))
    calls = []

    def runner(video, language):
        calls.append(video)
        return [{"start": 0.0, "end": 8.0, "text": "too short"}, {"start": 10.0, "end": 40.5, "text": "good one"},
                {"start": 50.0, "end": 300.0, "text": "too long"}]

    video = _video(tmp_path)
    got = CA.find_clips(video, 15, 60, runner=runner)
    assert got == [{"start": 10.0, "end": 40.5, "length": 30.5, "text": "good one"}]
    assert len(CA.find_clips(video, 0, 0, runner=runner)) == 3 and len(calls) == 1   # second time from the cache


def test_without_clipsai_installed_the_message_says_how(tmp_path, monkeypatch):
    monkeypatch.setattr(CA, "CACHE_DIR", str(tmp_path / "cache"))
    if CA.available():
        pytest.skip("ClipsAI is installed here")
    with pytest.raises(CA.ClipsAIError, match="requirements-clipsai.txt"):
        CA.find_clips(_video(tmp_path))


def test_speaker_crops_need_a_token(tmp_path, monkeypatch):
    monkeypatch.setattr(CA, "CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(CA.ClipsAIError, match="HF_TOKEN"):
        CA.speaker_crops(_video(tmp_path), token="")


CROPS = {"crop_width": 608, "crop_height": 1080, "original_width": 1920, "original_height": 1080,
         "segments": [{"x": 100, "y": 0, "start": 0.0, "end": 6.0, "speakers": [0]},
                      {"x": 1200, "y": 0, "start": 6.0, "end": 20.0, "speakers": [1]}]}


def test_centre_and_speaker_changes():
    left = round((100 + 304) / 1920, 4)
    right = round((1200 + 304) / 1920, 4)
    assert CA.centre_between(CROPS, 0, 5) == (left, 0.5)
    assert CA.centre_between(CROPS, 4, 12) == (right, 0.5)        # mostly the second speaker
    assert CA.centre_between(CROPS, 30, 40) is None
    assert CA.speaker_changes(CROPS, 2, 12) == [6.0]
    assert CA.speaker_changes(CROPS, 5, 12) == []                  # a 1 s piece is too short


def test_follow_speaker_frames_and_splits(tmp_path):
    pr = P.Project(title="talk")
    src = pr.add_source(P.Source(path=_video(tmp_path), duration=30, width=1920, height=1080))
    other = pr.add_source(P.Source(path=_video(tmp_path, "other.mp4"), duration=30, width=1920, height=1080))
    pr.add_segment(src.id, 2.0, 12.0)
    pr.add_segment(other.id, 0.0, 5.0)
    n = CA.follow_speaker(pr, src.id, split=True, crops_for=lambda p: CROPS)
    assert n == 2 and [(s.source_in, s.source_out) for s in pr.segments] == [(2.0, 6.0), (6.0, 12.0), (0.0, 5.0)]
    assert pr.segments[0].framing.auto_x < 0.3 < 0.7 < pr.segments[1].framing.auto_x
    assert pr.segments[0].framing.mode == "auto" and pr.segments[2].framing.auto_x is None   # other video untouched


def test_clipsai_command_line_check(capsys):
    code = CA.main(["check"])
    assert "ClipsAI installed:" in capsys.readouterr().out and code in (0, 1)


# ================================================================ Claude's tools
@pytest.fixture
def my_folders(tmp_path, monkeypatch):
    f = {"my_clips": str(tmp_path / "My Clips"), "my_edits": str(tmp_path / "My Edits"),
         "ready_to_post": str(tmp_path / "Ready")}
    monkeypatch.setattr(claude_tools, "folders", lambda: f)
    return f


def test_status_says_what_is_missing(my_folders, monkeypatch):
    monkeypatch.setattr(PZ, "settings", lambda *a: {"url": PZ.CLOUD_URL, "api_key": ""})
    s = claude_tools.status()
    assert s["folders"] == my_folders and s["postiz_api_key_set"] is False
    assert any("POSTIZ_API_KEY" in n for n in s["notes"])


@needs_ffmpeg
def test_make_edit_list_and_export(my_folders, tmp_path):
    video = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-pix_fmt", "yuv420p", str(video)], check=True)
    made = claude_tools.make_edit(str(video), [{"start": 0.5, "end": 2.0}, {"start": 2.5, "end": 3.5}],
                                  title="Talk test", channel="Sports")
    assert made["moments"] == 2 and made["length"] == 2.5
    edits = claude_tools.list_edits(today_only=True)
    assert edits[0]["title"] == "Talk test" and edits[0]["channel"] == "Sports"
    out = claude_tools.export_edits([made["folder"]])
    assert out["summary"].startswith("Exported 1 of 1.")
    assert claude_tools.upload_list()[0]["title"] == "Talk test" and claude_tools.upload_list()[0]["uploaded"] is False


def test_postiz_send_tool_is_a_dry_run_by_default(my_folders, tmp_path, monkeypatch):
    _ready(tmp_path, [_row(tmp_path, 1, name="a"), _row(tmp_path, 2, name="b")])
    fake = FakePostiz(CHANNELS)
    monkeypatch.setattr(PZ, "Client", lambda: _client(fake))
    out = claude_tools.postiz_send(["tiktok"])
    assert out["summary"].startswith("Dry run: 2 video(s)") and "/posts" not in fake.paths()
    assert "body" in out["report"][0] and "body" not in out["report"][1]
    assert claude_tools.postiz_channels()[0] == {"channel": "tiktok", "name": "GTA Laughs", "id": "tt1",
                                                 "disabled": False, "supported": True}


def test_mcp_server_lists_every_tool():
    import importlib.util
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, os.path.join(root, "clipping_mcp.py"), "--list"],
                         capture_output=True, text=True, check=True).stdout
    for name in ("status", "clipsai_find_clips", "make_edit", "export_edits", "postiz_send"):
        assert name in out
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("mcp not installed")
    spec = importlib.util.spec_from_file_location("clipping_mcp", os.path.join(root, "clipping_mcp.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    server = mod.build_server()
    import asyncio
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in (tools.tools if hasattr(tools, "tools") else tools)}
    assert {"status", "postiz_send", "clipsai_follow_speaker"} <= names


def test_project_config_lets_claude_run_the_tools():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mcp = json.load(open(os.path.join(root, ".mcp.json")))
    assert mcp["mcpServers"]["clipping"]["args"] == ["scripts/run_clipping_mcp.sh"]
    allow = json.load(open(os.path.join(root, ".claude", "settings.json")))["permissions"]["allow"]
    assert "mcp__clipping" in allow and "mcp__postiz" in allow
    assert io.open(os.path.join(root, "CLAUDE.md")).read().startswith("@AGENTS.md")
