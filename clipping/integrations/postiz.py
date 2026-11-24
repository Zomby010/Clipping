"""
clipping.integrations.postiz — Post finished videos to TikTok, Instagram Reels,
YouTube and Facebook through Postiz (https://github.com/gitroomhq/postiz-app).

Postiz is a separate app (self-hosted for free, or Postiz cloud). We only call
its public API; none of its code is copied here.

Settings (in .env, or the environment):
    POSTIZ_API_KEY   Postiz > Settings > Developers > Public API
    POSTIZ_URL       optional. Postiz cloud: https://api.postiz.com/public/v1 (default)
                     self-hosted:  https://<your server>/api/public/v1

send_manifest() reads the Clip Editor's upload list ("Ready to post/
upload_manifest.json"), uploads each video once and makes one Postiz post per
video with an entry for every chosen channel. What was sent is written back to
the row under "postiz", so a video is never sent to the same channel twice.
It is a dry run unless ``dry_run=False``: the dry run shows exactly what would
be posted and sends nothing.

Command line (Claude runs these too):
    python -m clipping.integrations.postiz channels
    python -m clipping.integrations.postiz send --channels tiktok,instagram [--start "2026-11-25 18:00" --every 12] [--post]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

CLOUD_URL = "https://api.postiz.com/public/v1"
DEFAULT_TZ = "Africa/Nairobi"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_FILE = os.path.join(REPO_DIR, ".env")

# Postiz "identifier" -> the settings "__type" and our short name
PROVIDERS = {
    "tiktok": "tiktok",
    "instagram": "instagram",
    "instagram-standalone": "instagram-standalone",
    "youtube": "youtube",
    "facebook": "facebook",
}
SHORT_NAMES = {"tiktok": "tiktok", "instagram": "instagram", "instagram-standalone": "instagram",
               "youtube": "youtube", "facebook": "facebook"}
TIKTOK_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")


class PostizError(Exception):
    """A plain-English problem with Postiz (shown to the user as it is)."""


# ================================================================ settings
def _env_file_values(path: str = ENV_FILE) -> dict:
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def settings(env_file: str = ENV_FILE) -> dict:
    """{"url", "api_key"} from the environment, else .env."""
    file_values = _env_file_values(env_file)
    key = os.environ.get("POSTIZ_API_KEY") or file_values.get("POSTIZ_API_KEY", "")
    url = os.environ.get("POSTIZ_URL") or file_values.get("POSTIZ_URL", "") or CLOUD_URL
    if key.startswith("your-"):
        key = ""
    return {"url": normalise_url(url), "api_key": key}


def normalise_url(url: str) -> str:
    """Accept the server address people copy ("https://postiz.example.com") and
    turn it into the public API address."""
    url = (url or CLOUD_URL).strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    if url.endswith("/public/v1"):
        return url
    if url.rstrip("/") in ("https://api.postiz.com", "http://api.postiz.com"):
        return CLOUD_URL
    if url.endswith("/api"):
        return url + "/public/v1"
    return url + "/api/public/v1"


# ================================================================ the API
class Client:
    def __init__(self, url: str | None = None, api_key: str | None = None, session=None, timeout: float = 120):
        cfg = settings()
        self.url = normalise_url(url) if url else cfg["url"]
        self.api_key = api_key if api_key is not None else cfg["api_key"]
        if not self.api_key:
            raise PostizError("No Postiz API key. Add POSTIZ_API_KEY to .env (Postiz > Settings > Developers > "
                              "Public API), or set it in Clipping Studio's API keys.")
        if session is None:
            import requests
            session = requests.Session()
        self.http = session
        self.timeout = timeout

    def _call(self, method: str, path: str, **kw):
        headers = {"Authorization": self.api_key}
        if "json" in kw:
            headers["Content-Type"] = "application/json"
        try:
            r = self.http.request(method, self.url + path, headers=headers, timeout=self.timeout, **kw)
        except Exception as e:  # network down, wrong address ...
            raise PostizError(f"Can't reach Postiz at {self.url}: {e}") from e
        if r.status_code == 401 or r.status_code == 403:
            raise PostizError("Postiz didn't accept the API key. Check POSTIZ_API_KEY.")
        if r.status_code == 429:
            raise PostizError("Postiz says too many posts this hour. Try again later, or space them out "
                              "with a schedule.")
        if r.status_code == 413:
            raise PostizError("The video is too big for Postiz.")
        if r.status_code >= 400:
            raise PostizError(f"Postiz answered {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            return {}

    def channels(self) -> list[dict]:
        """The connected accounts: [{id, name, identifier, disabled, profile}]."""
        data = self._call("GET", "/integrations")
        return data if isinstance(data, list) else []

    def upload(self, path: str) -> dict:
        """Upload a video or picture; returns {"id", "path"}."""
        if not os.path.isfile(path):
            raise PostizError(f"The video isn't there any more: {path}")
        mime = "video/mp4" if path.lower().endswith(".mp4") else "image/jpeg"
        with open(path, "rb") as f:
            data = self._call("POST", "/upload", files={"file": (os.path.basename(path), f, mime)})
        if not isinstance(data, dict) or not data.get("id") or not data.get("path"):
            raise PostizError(f"Postiz didn't return the uploaded file: {data}")
        return {"id": data["id"], "path": data["path"]}

    def create_post(self, body: dict) -> list[dict]:
        data = self._call("POST", "/posts", json=body)
        return data if isinstance(data, list) else [data]


# ================================================================ building posts
def short_name(channel: dict) -> str:
    return SHORT_NAMES.get(channel.get("identifier", ""), channel.get("identifier", ""))


def pick_channels(all_channels: list[dict], wanted: list[str] | None) -> list[dict]:
    """Channels by short name (tiktok, instagram, youtube, facebook), Postiz id
    or account name. None = every supported, enabled channel."""
    usable = [c for c in all_channels if not c.get("disabled") and c.get("identifier") in PROVIDERS]
    if not wanted:
        return usable
    out = []
    for w in wanted:
        w = w.strip().lower()
        found = [c for c in usable if w in (short_name(c), c.get("identifier", "").lower(),
                                            str(c.get("id", "")).lower(), str(c.get("name", "")).lower(),
                                            str(c.get("profile", "")).lower())]
        if not found:
            raise PostizError(f"No connected Postiz channel called '{w}'. Connected: "
                              + (", ".join(f"{short_name(c)} ({c.get('name')})" for c in usable) or "none"))
        out += [c for c in found if c not in out]
    return out


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def text_for(row: dict, channel: dict) -> str:
    """The words for this platform, from the upload list row."""
    name = short_name(channel)
    title = row.get("youtube_title_final") or ""
    if name == "tiktok":
        return row.get("tiktok_caption_final") or f"{title} {row.get('hastag', '')}".strip()
    if name == "instagram":
        return row.get("instagram_caption") or "\n\n".join(
            p for p in (title, row.get("youtube_description_final", ""), row.get("hastag", "")) if p.strip())
    if name == "facebook":
        return row.get("facebook_description") or row.get("youtube_description_final") or title
    return row.get("youtube_description_final") or title   # youtube


def settings_for(row: dict, channel: dict, tiktok_privacy: str = "PUBLIC_TO_EVERYONE",
                 youtube_visibility: str = "public") -> dict:
    ident = channel.get("identifier", "")
    name = short_name(channel)
    kids = bool(row.get("made_for_kids"))
    if name == "tiktok":
        return {"__type": "tiktok", "title": _clip(row.get("youtube_title_final", ""), 90),
                "privacy_level": tiktok_privacy, "duet": False, "stitch": False, "comment": True,
                "autoAddMusic": "no", "brand_content_toggle": False, "brand_organic_toggle": False,
                "video_made_with_ai": False, "content_posting_method": "DIRECT_POST"}
    if name == "instagram":
        return {"__type": PROVIDERS[ident], "post_type": "post"}   # one video with post_type "post" is a Reel
    if name == "youtube":
        title = _clip(row.get("youtube_title_final") or "Untitled", 100)
        if len(title) < 2:
            title = (title + " video").strip()
        tags = [{"value": t, "label": t} for t in (row.get("youtube_tags_final") or [])[:15] if t]
        return {"__type": "youtube", "title": title, "type": youtube_visibility,
                "selfDeclaredMadeForKids": "yes" if kids else "no", "tags": tags}
    if name == "facebook":
        return {"__type": "facebook", "title": _clip(row.get("facebook_title") or row.get("youtube_title_final", ""), 255)}
    return {"__type": ident}


def build_post(row: dict, channels: list[dict], media: dict, when: datetime | None,
               tiktok_privacy: str = "PUBLIC_TO_EVERYONE", youtube_visibility: str = "public") -> dict:
    """The JSON body for POST /posts: one entry per channel, same video."""
    posts = []
    for ch in channels:
        posts.append({
            "integration": {"id": ch["id"]},
            "value": [{"content": text_for(row, ch), "image": [{"id": media["id"], "path": media["path"]}]}],
            "settings": settings_for(row, ch, tiktok_privacy, youtube_visibility),
        })
    body = {"type": "schedule" if when else "now",
            "date": (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "shortLink": False, "tags": [], "posts": posts}
    return body


def schedule_times(n: int, start: str | datetime | None, every_hours: float, tz_name: str = DEFAULT_TZ) -> list:
    """n times starting at ``start`` (local time, "YYYY-MM-DD HH:MM") every
    ``every_hours``. No start = post now (a list of None)."""
    if not start:
        return [None] * n
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    if isinstance(start, str):
        try:
            first = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
        except ValueError as e:
            raise PostizError('Write the start time like "2026-11-25 18:00".') from e
        first = first.replace(tzinfo=tz)
    else:
        first = start if start.tzinfo else start.replace(tzinfo=tz)
    if first < datetime.now(tz) - timedelta(minutes=1):
        raise PostizError("The start time is in the past.")
    step = timedelta(hours=max(0.0, float(every_hours or 0)))
    return [first + i * step for i in range(n)]


# ================================================================ the upload list
def sent_to(row: dict) -> set[str]:
    return set((row.get("postiz") or {}).keys())


def waiting_rows(rows: list[dict], channels: list[dict]) -> list[dict]:
    """Rows with a video that still has a chosen channel it wasn't sent to."""
    out = []
    for row in rows:
        if row.get("status") != "success" or not row.get("video_path") or not os.path.isfile(row["video_path"]):
            continue
        if any(str(c["id"]) not in sent_to(row) for c in channels):
            out.append(row)
    return out


def send_manifest(ready_root: str, channels: list[str] | None = None, start=None, every_hours: float = 12,
                  tz_name: str = DEFAULT_TZ, dry_run: bool = True, limit: int | None = None,
                  tiktok_privacy: str = "PUBLIC_TO_EVERYONE", youtube_visibility: str = "public",
                  client: Client | None = None, log=print) -> list[dict]:
    """Send the upload list's waiting videos to Postiz. Returns one report row per video."""
    from clipping.editor import batch
    if tiktok_privacy not in TIKTOK_PRIVACY:
        raise PostizError(f"TikTok privacy must be one of {', '.join(TIKTOK_PRIVACY)}.")
    client = client or Client()
    chosen = pick_channels(client.channels(), channels)
    if not chosen:
        raise PostizError("No TikTok, Instagram, YouTube or Facebook account is connected in Postiz yet. "
                          "Connect them in Postiz first.")
    rows = batch.load_manifest(ready_root)
    todo = waiting_rows(rows, chosen)
    if limit:
        todo = todo[:limit]
    times = schedule_times(len(todo), start, every_hours, tz_name)
    report = []
    for row, when in zip(todo, times):
        targets = [c for c in chosen if str(c["id"]) not in sent_to(row)]
        names = ", ".join(f"{short_name(c)} ({c.get('name')})" for c in targets)
        item = {"rank": row.get("rank"), "title": row.get("youtube_title_final"), "video": row["video_path"],
                "channels": names, "when": when.isoformat() if when else "now"}
        if dry_run:
            item["body"] = build_post(row, targets, {"id": "<uploaded-id>", "path": "<uploaded-url>"}, when,
                                      tiktok_privacy, youtube_visibility)
            item["sent"] = False
            log(f"[dry run] #{row.get('rank')} {row.get('youtube_title_final')!r} -> {names} at {item['when']}")
            report.append(item)
            continue
        try:
            media = row.get("postiz_media") or client.upload(row["video_path"])
            row["postiz_media"] = media
            result = client.create_post(build_post(row, targets, media, when, tiktok_privacy, youtube_visibility))
            ids = {str(r.get("integration")): r.get("postId") for r in result if isinstance(r, dict)}
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            sent = row.setdefault("postiz", {})
            for c in targets:
                sent[str(c["id"])] = {"channel": short_name(c), "name": c.get("name"),
                                      "post_id": ids.get(str(c["id"])), "when": item["when"], "sent_at": stamp}
            item["sent"] = True
            log(f"Sent #{row.get('rank')} {row.get('youtube_title_final')!r} -> {names} ({item['when']})")
        except PostizError as e:
            item["sent"] = False
            item["error"] = str(e)
            log(f"Not sent #{row.get('rank')}: {e}")
        finally:
            batch._save_manifest(ready_root, rows)   # keep what was sent even if a later one fails
        report.append(item)
        if item.get("error") and "too many" in item["error"]:
            break
    return report


def describe(report: list[dict], dry_run: bool) -> str:
    if not report:
        return "Nothing to send: every video in the upload list was already sent to those channels."
    if dry_run:
        head = f"Dry run: {len(report)} video(s) would be sent. Nothing was posted."
    else:
        head = f"Sent {sum(1 for r in report if r.get('sent'))} of {len(report)} video(s) to Postiz."
    lines = [head]
    for r in report:
        mark = "✔" if r.get("sent") else ("·" if dry_run else "✖")
        lines.append(f"{mark} #{r['rank']} {r['title']} -> {r['channels']} ({r['when']})"
                     + (f": {r['error']}" if r.get("error") else ""))
    return "\n".join(lines)


# ================================================================ command line
def _default_ready() -> str:
    return os.path.join(REPO_DIR, "Ready to post")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m clipping.integrations.postiz",
                                description="Send Clip Editor videos to TikTok, Reels, YouTube and Facebook via Postiz.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("channels", help="list the accounts connected in Postiz")
    s = sub.add_parser("send", help="send the upload list (a dry run unless --post)")
    s.add_argument("--ready", default=_default_ready(), help="the Ready to post folder")
    s.add_argument("--channels", default="", help="comma list: tiktok,instagram,youtube,facebook (default: all)")
    s.add_argument("--start", default="", help='first post time, local: "2026-11-25 18:00" (default: now)')
    s.add_argument("--every", type=float, default=12, help="hours between videos when scheduling (default 12)")
    s.add_argument("--tz", default=DEFAULT_TZ)
    s.add_argument("--limit", type=int, default=0, help="send at most this many videos")
    s.add_argument("--tiktok-privacy", default="PUBLIC_TO_EVERYONE", choices=TIKTOK_PRIVACY)
    s.add_argument("--youtube", default="public", choices=("public", "unlisted", "private"))
    s.add_argument("--post", action="store_true", help="really post (without it nothing is sent)")
    s.add_argument("--json", action="store_true", help="print the report as JSON")
    a = p.parse_args(argv)
    try:
        if a.cmd == "channels":
            for c in Client().channels():
                flag = "" if c.get("identifier") in PROVIDERS else "  (not used by the clipping apps)"
                off = "  (disabled)" if c.get("disabled") else ""
                print(f"{short_name(c):10} {c.get('name')}  id={c.get('id')}{off}{flag}")
            return 0
        chans = [c for c in a.channels.split(",") if c.strip()] or None
        report = send_manifest(a.ready, chans, a.start or None, a.every, a.tz, dry_run=not a.post,
                               limit=a.limit or None, tiktok_privacy=a.tiktok_privacy, youtube_visibility=a.youtube,
                               log=(lambda *_: None) if a.json else print)
        print(json.dumps(report, indent=2, ensure_ascii=False) if a.json else describe(report, not a.post))
        return 0
    except PostizError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
