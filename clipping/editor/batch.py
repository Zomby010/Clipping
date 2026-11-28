"""
clipping.editor.batch — Export many edits in one go, and the upload list.

export_many()   exports the chosen edits one after another (a broken one is
                skipped and named in the report). Stop finishes the video
                being made, then stops.

upload manifest Every export adds a row to "Ready to post/upload_manifest.json"
                in the same shape Clipping Studio's render manifest has, so the
                repo's uploaders read it as it is:

                  python run_upload.py --manifest-file "Ready to post/upload_manifest.json" ...
                  python run_fb_upload.py --manifest-file "Ready to post/upload_manifest.json" ...

                Re-exporting an edit updates its row. A row that was already
                uploaded is never changed (a new row is added instead).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime

from clipping.editor import framing, publish, render
from clipping.editor import project as P

MANIFEST_FILE = "upload_manifest.json"
UPLOADED_KEYS = ("youtube_video_id", "fb_video_id", "fb_post_id")


# ================================================================ the list of edits
@dataclass
class EditInfo:
    folder: str
    title: str
    channel: str
    length: float
    modified: datetime
    last_export: str | None
    problem: str | None = None

    @property
    def is_today(self) -> bool:
        return self.modified.date() == date.today()


def list_edits(edits_root: str) -> list[EditInfo]:
    """Every edit under ``edits_root``, newest first."""
    out = []
    if not os.path.isdir(edits_root):
        return out
    for name in os.listdir(edits_root):
        folder = os.path.join(edits_root, name)
        file = os.path.join(folder, P.PROJECT_FILE)
        if not os.path.isfile(file):
            continue
        modified = datetime.fromtimestamp(os.path.getmtime(file))
        try:
            pr, _ = P.load(folder)
        except Exception as e:  # unreadable or from a newer app
            out.append(EditInfo(folder, name, "?", 0.0, modified, None, f"Can't open: {e}"))
            continue
        last = pr.exports[-1].get("at") if pr.exports else None
        problems = pr.problems() or ([] if pr.segments else ["The cut list is empty."])
        out.append(EditInfo(folder, pr.title, pr.channel, pr.total_length, modified, last,
                            problems[0] if problems else None))
    out.sort(key=lambda e: e.modified, reverse=True)
    return out


def todays_edits(edits_root: str) -> list[EditInfo]:
    return [e for e in list_edits(edits_root) if e.is_today]


def work_out_auto_crop(project, cancel=None) -> None:
    """The automatic crop for moments that don't have it yet (the window does
    this before a preview; a batch export has to do it itself)."""
    for seg in project.segments:
        if cancel is not None and cancel.is_set():
            raise render.Cancelled("Stopped.")
        if seg.framing.mode == "auto" and seg.framing.auto_x is None and not seg.reaction:
            src = project.source(seg.source_id)
            centre = framing.auto_centre(src.path, seg.source_in, seg.source_out)
            seg.framing.auto_x, seg.framing.auto_y = centre if centre else (0.5, 0.5)


def export_many(folders: list[str], ready_root: str, on_progress=None, stop: threading.Event | None = None,
                log=None, fonts=None, exporter=None) -> list[dict]:
    """Export each edit folder in turn. ``on_progress(done_count, total, fraction)``.
    ``stop`` is checked between videos. Returns one report row per edit."""
    exporter = exporter or render.export
    report = []
    n = len(folders)
    for i, folder in enumerate(folders):
        if stop is not None and stop.is_set():
            report.append({"folder": folder, "title": os.path.basename(folder), "ok": False,
                           "skipped": "Stopped before this one."})
            continue
        started = time.monotonic()
        row = {"folder": folder, "title": os.path.basename(folder), "ok": False}
        try:
            pr, _ = P.load(folder)
            row["title"] = pr.title
            problems = pr.problems() or ([] if pr.segments else ["The cut list is empty."])
            if problems:
                row["skipped"] = problems[0]
                report.append(row)
                continue
            work_out_auto_crop(pr)
            record = exporter(pr, ready_root,
                              on_progress=(lambda f, i=i: on_progress(i, n, f)) if on_progress else None,
                              log=log, fonts=fonts)
            add_to_manifest(ready_root, pr, record, edit_folder=folder)
            P.save(pr, folder)   # keeps the export record and the worked-out crop
            row.update(ok=True, path=record["path"], seconds=round(time.monotonic() - started, 1))
        except render.Cancelled:
            row["skipped"] = "Stopped."
        except Exception as e:
            row["skipped"] = f"Failed: {e}"
        report.append(row)
        if on_progress:
            on_progress(i + 1, n, 0.0)
    return report


def describe_report(report: list[dict]) -> str:
    done = [r for r in report if r.get("ok")]
    lines = [f"Exported {len(done)} of {len(report)}."]
    for r in report:
        if r.get("ok"):
            lines.append(f"✔ {r['title']} ({r.get('seconds', '?')} s)")
        else:
            lines.append(f"✖ {r['title']}: {r.get('skipped', 'not exported')}")
    return "\n".join(lines)


# ================================================================ upload manifest
def manifest_path(ready_root: str) -> str:
    return os.path.join(ready_root, MANIFEST_FILE)


def load_manifest(ready_root: str) -> list[dict]:
    try:
        with open(manifest_path(ready_root), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_manifest(ready_root: str, rows: list[dict]) -> str:
    os.makedirs(ready_root, exist_ok=True)
    target = manifest_path(ready_root)
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=ready_root)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    os.replace(tmp, target)
    return target


def is_uploaded(row: dict) -> bool:
    return any(row.get(k) for k in UPLOADED_KEYS) or row.get("youtube_upload_status") == "uploaded"


def manifest_row(project, record: dict, rank: int, edit_folder: str | None = None) -> dict:
    from clipping.editor import platforms
    item = publish.upload_item(project)
    cover = os.path.join(record["folder"], "cover.jpg")
    return {
        "rank": rank,
        "status": "success",
        "video_path": record["path"],
        "thumbnail_path": cover if os.path.exists(cover) else None,
        **item,
        "tiktok_caption_final": platforms.text_for(project, "tiktok")["caption"],
        "facebook_title": platforms.text_for(project, "facebook")["title"],
        "facebook_description": platforms.text_for(project, "facebook")["description"],
        "channel": project.channel,
        "made_for_kids": project.channel == "Kids",
        "duration": record.get("length"),
        "edit_id": project.created,
        "edit_folder": edit_folder,
        "exported_at": record.get("at"),
        "source": "Clip Editor",
    }


def _same_edit(row: dict, project, edit_folder: str | None) -> bool:
    """Rows are matched by the edit's folder (two edits can be made in the
    same second, so the start time alone isn't enough)."""
    if edit_folder or row.get("edit_folder"):
        return bool(edit_folder) and os.path.normpath(row.get("edit_folder") or "") == os.path.normpath(edit_folder)
    return row.get("edit_id") == project.created


def add_to_manifest(ready_root: str, project, record: dict, edit_folder: str | None = None) -> dict:
    """Add this export to the upload list, or update this edit's row if it
    wasn't uploaded yet. Returns the row."""
    rows = load_manifest(ready_root)
    mine = [r for r in rows if _same_edit(r, project, edit_folder) and not is_uploaded(r)]
    rank = mine[-1]["rank"] if mine else max([int(r.get("rank") or 0) for r in rows] + [0]) + 1
    row = manifest_row(project, record, rank, edit_folder)
    if mine:
        rows = [row if r is mine[-1] else r for r in rows]
    else:
        rows.append(row)
    _save_manifest(ready_root, rows)
    return row


def upload_commands(ready_root: str, tz_name: str = "Africa/Nairobi") -> dict[str, str]:
    """The commands that send the upload list to YouTube and Facebook."""
    m = manifest_path(ready_root)
    yt_results = os.path.join(ready_root, "youtube_upload_results.json")
    fb_results = os.path.join(ready_root, "facebook_upload_results.json")
    return {
        "youtube": (f'python run_upload.py --manifest-file "{m}" --updated-manifest "{m}" '
                    f'--result-file "{yt_results}" --tz-name {tz_name}'),
        "facebook": (f'python run_fb_upload.py --manifest-file "{m}" --updated-manifest "{m}" '
                     f'--result-file "{fb_results}" --tz-name {tz_name}'),
    }
