"""
clipping.claude_tools — The jobs Claude can run for you, as plain functions.

clipping_mcp.py turns each one into a tool for Claude Code (see .mcp.json), so
in the Clipping folder you can just ask Claude things like:

    "find 30 to 60 s clips in ~/Videos/podcast.mp4 and make an edit of the best three"
    "frame my latest edit on whoever is talking"
    "export today's edits"
    "schedule the upload list on TikTok and Reels, one every 12 hours from 6 pm tomorrow"

Every function returns plain data (dicts and lists) and never asks questions.
Posting is a dry run unless ``post=True``.
"""

from __future__ import annotations

import json
import os

from clipping.integrations import clipsai_tool, postiz

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_SETTINGS = os.path.join(REPO_DIR, "clip_editor_settings.json")


def _editor_settings() -> dict:
    try:
        with open(EDITOR_SETTINGS, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def folders() -> dict:
    """Where the apps keep things (the Clip Editor's own choices win)."""
    from clipping.editor import importer
    from clipping.editor import project as P
    s = _editor_settings()
    return {
        "my_clips": s.get("my_clips") or importer.DEFAULT_MY_CLIPS,
        "my_edits": s.get("edits_root") or P.default_edits_root(REPO_DIR),
        "ready_to_post": s.get("ready_root") or P.default_ready_root(REPO_DIR),
    }


# ================================================================ what is there
def status() -> dict:
    """What is installed and set up, and where the folders are."""
    cfg = postiz.settings()
    return {
        "folders": folders(),
        "clipsai_installed": clipsai_tool.available(),
        "hf_token_set": bool(clipsai_tool.hf_token()),
        "postiz_api_key_set": bool(cfg["api_key"]),
        "postiz_url": cfg["url"],
        "notes": [n for n in (
            None if clipsai_tool.available() else "ClipsAI: pip install -r requirements-clipsai.txt",
            None if cfg["api_key"] else "Postiz: put POSTIZ_API_KEY (and POSTIZ_URL if self-hosted) in .env",
        ) if n],
    }


def list_my_clips(limit: int = 10) -> list[dict]:
    """Clipping Studio's latest runs and their videos."""
    from clipping.editor import importer
    out = []
    for run, videos in importer.list_runs(folders()["my_clips"])[:limit]:
        out.append({"run": run, "videos": [{"path": v, **importer.details_for(v)} for v in videos]})
    return out


def list_edits(today_only: bool = False) -> list[dict]:
    """The Clip Editor's edits, newest first."""
    from clipping.editor import batch
    root = folders()["my_edits"]
    edits = batch.todays_edits(root) if today_only else batch.list_edits(root)
    return [{"folder": e.folder, "title": e.title, "channel": e.channel, "length": round(e.length, 1),
             "modified": e.modified.isoformat(timespec="minutes"), "last_export": e.last_export,
             "problem": e.problem} for e in edits]


def upload_list() -> list[dict]:
    """The upload list (Ready to post/upload_manifest.json), short form."""
    from clipping.editor import batch
    rows = batch.load_manifest(folders()["ready_to_post"])
    return [{"rank": r.get("rank"), "title": r.get("youtube_title_final"), "video": r.get("video_path"),
             "channel": r.get("channel"), "uploaded": batch.is_uploaded(r),
             "postiz": {v.get("channel"): v.get("when") for v in (r.get("postiz") or {}).values()}}
            for r in rows]


# ================================================================ ClipsAI
def clipsai_find_clips(video: str, min_seconds: float = 15, max_seconds: float = 60) -> list[dict]:
    """Moments that stand on their own, found from the speech (best for talk)."""
    return clipsai_tool.find_clips(os.path.expanduser(video), min_seconds, max_seconds)


def make_edit(video: str, moments: list[dict] | str, title: str = "", channel: str = "Other") -> dict:
    """A new Clip Editor edit from one video. ``moments`` is a list of
    {"start", "end"} in seconds, or text like "1:02-1:10, 2:38-2:49"."""
    from clipping.editor import media
    from clipping.editor import project as P
    video = os.path.abspath(os.path.expanduser(video))
    if channel not in P.CHANNELS:
        channel = "Other" if "Other" in P.CHANNELS else P.CHANNELS[0]
    pr = P.Project(title=P.safe_name(title or os.path.splitext(os.path.basename(video))[0]), channel=channel)
    src = pr.add_source(media.probe(video))
    if isinstance(moments, str):
        pr.add_ranges(src.id, moments)
    else:
        for m in moments:
            pr.add_segment(src.id, float(m["start"]), float(m["end"]))
    if not pr.segments:
        raise ValueError("No moments to put in the edit.")
    folder = P.new_project_dir(folders()["my_edits"], pr.title)
    P.save(pr, folder)
    return {"folder": folder, "title": pr.title, "moments": len(pr.segments), "length": round(pr.total_length, 1)}


def clipsai_follow_speaker(edit_folder: str, split_at_speaker_changes: bool = False) -> dict:
    """Point every moment of an edit at whoever is talking (needs HF_TOKEN)."""
    from clipping.editor import project as P
    pr, _ = P.load(edit_folder)
    framed = clipsai_tool.follow_speaker(pr, split=split_at_speaker_changes)
    P.save(pr, edit_folder)
    return {"folder": edit_folder, "moments_framed": framed, "moments": len(pr.segments)}


# ================================================================ export
def export_edits(edit_folders: list[str] | None = None, today: bool = False) -> dict:
    """Export edits to Ready to post (and add them to the upload list)."""
    from clipping.editor import batch
    if not edit_folders:
        edit_folders = [e["folder"] for e in list_edits(today_only=today)
                        if not e["problem"]][: (None if today else 1)]
    report = batch.export_many(edit_folders, folders()["ready_to_post"])
    return {"summary": batch.describe_report(report), "report": report}


# ================================================================ Postiz
def postiz_channels() -> list[dict]:
    """The accounts connected in Postiz."""
    return [{"channel": postiz.short_name(c), "name": c.get("name"), "id": c.get("id"),
             "disabled": bool(c.get("disabled")), "supported": c.get("identifier") in postiz.PROVIDERS}
            for c in postiz.Client().channels()]


def postiz_send(channels: list[str] | None = None, start: str = "", every_hours: float = 12,
                limit: int = 0, post: bool = False, tiktok_privacy: str = "PUBLIC_TO_EVERYONE",
                youtube_visibility: str = "public") -> dict:
    """Send the upload list to Postiz. A dry run (nothing posted) unless post=True.
    ``start`` is local Nairobi time like "2026-11-25 18:00"; empty = now."""
    report = postiz.send_manifest(folders()["ready_to_post"], channels or None, start or None, every_hours,
                                  dry_run=not post, limit=limit or None, tiktok_privacy=tiktok_privacy,
                                  youtube_visibility=youtube_visibility, log=lambda *_: None)
    for r in report[1:]:   # keep the answer short: one example of what is posted is enough
        r.pop("body", None)
    return {"summary": postiz.describe(report, dry_run=not post), "report": report}


# ================================================================ reactions (Claude watches, then picks)
def _edit_folder(edit_folder: str) -> str:
    if edit_folder in ("", "latest", None):
        edits = list_edits()
        if not edits:
            raise ValueError("There are no edits yet.")
        return edits[0]["folder"]
    return os.path.abspath(os.path.expanduser(edit_folder))


def watch_edit(edit_folder: str = "latest", every_seconds: float = 0) -> dict:
    """Watch an edit: numbered contact sheets of the cut (open them with the Read
    tool to see the pictures), plus speech, script, moments and the reactions
    already there, all in edit seconds. edit_folder "latest" = the newest edit."""
    from clipping.editor import batch, watch
    from clipping.editor import project as P
    folder = _edit_folder(edit_folder)
    pr, _ = P.load(folder)
    pr.folder = folder            # phase 3: voices are found next to the edit
    batch.work_out_auto_crop(pr)  # the preview needs the automatic crop
    P.save(pr, folder)
    seen = watch.watch(pr, folder, every_seconds)
    seen["folder"] = folder
    seen["title"] = pr.title
    return seen


def list_reactions(with_pictures: bool = True) -> dict:
    """Every reaction clip in assets/reactions/<mood>/, numbered, and one picture
    of them all (open it with the Read tool to see what each clip shows)."""
    from clipping.editor import watch
    items = watch.reactions()
    return {"reactions": [{k: it[k] for k in ("n", "mood", "clip", "length")} for it in items],
            "picture": watch.reaction_sheet(items) if with_pictures and items else None,
            "folder": watch.O.REACTIONS_DIR,
            "styles": {"cutaway": "fills the screen for 0.8-2 s while the video keeps playing underneath",
                       "corner": "a rounded box in a corner (top-left, top-right or middle-left)"}}


def add_reactions(edit_folder: str, reactions: list[dict]) -> dict:
    """Put reactions into an edit. Each item: {"at": edit seconds, "clip": number
    from list_reactions (or file name, or a mood), "style": "cutaway" or "corner",
    "length": 0.8-2 s, "position": "top-left" | "top-right" | "middle-left"}.
    Returns the added reactions with their ids and any warnings."""
    from clipping.editor import project as P
    from clipping.editor import watch
    folder = _edit_folder(edit_folder)
    pr, _ = P.load(folder)
    added = watch.add_reactions(pr, reactions)
    P.save(pr, folder)
    return {"folder": folder, "added": added, "reactions_now": watch.current_reactions(pr),
            "note": "Reopen the edit in the Clip Editor (Open…) to see them, or export_edits to render."}


def remove_reactions(edit_folder: str, ids: list[str] | None = None) -> dict:
    """Take reactions out of an edit: the given ids, or all of them."""
    from clipping.editor import overlays as O
    from clipping.editor import project as P
    from clipping.editor import watch
    folder = _edit_folder(edit_folder)
    pr, _ = P.load(folder)
    gone = [o["id"] for o in O.anchored(pr, "reaction") if ids is None or o["id"] in ids]
    for item_id in gone:
        O.delete(pr, item_id)
    P.save(pr, folder)
    return {"folder": folder, "removed": gone, "reactions_now": watch.current_reactions(pr)}
