"""
clipping.editor.review — "Check before posting".

Three parts:

  checks(project)          the app checks the edit itself (no Claude needed):
                           hook, length, text in button areas, music level,
                           upload text, licence notes, missing files, Kids ...
                           Each Finding can carry a safe automatic fix.
  claude_review(project)   Claude reads the words in the edit (captions, hook,
                           pop-ups, title, description, hashtags, script) and
                           suggests changes; the person ticks the ones to keep
                           and apply_suggestions() applies them.
  suggest_cut(project, n)  Claude picks the strongest parts of the moments for
                           an n-second cut; apply_cut() rebuilds the cut list.

Claude is reached with clipping.providers.claude.ask (the Claude Code
program, or an API key), the same way Clipping Studio does it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace

from clipping.editor import audio_mix as A
from clipping.editor import captions as C
from clipping.editor import overlays as O
from clipping.editor import publish, styles, text_layers, timeline

PROBLEM, WARNING, TIP = "problem", "warning", "tip"
LEVEL_ICONS = {PROBLEM: "✖", WARNING: "⚠", TIP: "💡"}
HOOK_SECONDS = 2.0
MAX_HASHTAGS = 15
CUT_TARGETS = (15, 30, 45, 60)
CUT_TOLERANCE = 0.2   # a suggested cut may be up to 20 % off its target


class ReviewError(RuntimeError):
    """Claude couldn't be reached or gave an answer we can't use."""


@dataclass
class Finding:
    level: str
    text: str
    fix: str | None = None        # id for apply_fix(); None = nothing automatic
    fix_label: str = ""
    target: str | None = None     # e.g. the overlay id a fix works on

    @property
    def icon(self) -> str:
        return LEVEL_ICONS.get(self.level, "•")


# ================================================================ local checks
def _hashtags(text: str) -> list[str]:
    return [t for t in (text or "").split() if t.startswith("#") and len(t) > 1]


def _inside_safe(x0, y0, x1, y1, w=1080, h=1920) -> bool:
    left, top, right, bottom = O.safe_box(w, h)
    return x0 >= left - 1 and y0 >= top - 1 and x1 <= right + 1 and y1 <= bottom + 1


def _missing_files(project) -> list[str]:
    out = []
    for s in project.sources:
        if not os.path.exists(s.path):
            out.append(s.name or os.path.basename(s.path))
    m = A.music(project)
    if m and not os.path.exists(m["path"]):
        out.append("music: " + os.path.basename(m["path"]))
    for f in O.visible_sfx(project):
        if not os.path.exists(f["path"]):
            out.append("sound effect: " + os.path.basename(f["path"]))
    for o in O.anchored(project, "reaction"):
        if not os.path.exists(o["path"]):
            out.append("reaction: " + os.path.basename(o["path"]))
    logo = (project.logo or {}).get("path")
    if logo and not os.path.exists(logo):
        out.append("logo: " + os.path.basename(logo))
    return out


def checks(project, emoji_font: str | None | bool = "auto") -> list[Finding]:
    """Everything the app can check by itself, worst first."""
    C.refresh(project)
    O.refresh(project)
    out: list[Finding] = []
    total = project.total_length
    if total <= 0:
        return [Finding(PROBLEM, "The cut list is empty. Add some moments first.")]

    # Files and the cut list
    missing = _missing_files(project)
    if missing:
        out.append(Finding(PROBLEM, "Missing files: " + "; ".join(missing[:5]) +
                           ". Put them back or remove them from the edit."))
    for p in timeline.validate(project.segments, {s.id: s.duration for s in project.sources}):
        out.append(Finding(PROBLEM, p))

    # Length
    rep = timeline.length_report(total)
    too_long = [n for n, ok in rep["platforms"].items() if not ok]
    if too_long:
        out.append(Finding(PROBLEM, f"At {timeline.format_time(total)} it is too long for "
                                    + ", ".join(too_long) + ". Trim it or use Suggest a cut."))
    elif rep["over_target"]:
        out.append(Finding(TIP, f"At {timeline.format_time(total)} it is longer than 60 s. "
                                "Shorts under a minute usually do better."))

    # Hook in the first 2 seconds
    hook = (project.hook or {}).get("text", "").strip()
    early_text = any(c["start"] < HOOK_SECONDS for c in C.visible(project)) or any(
        o.get("type") == "text" and float(o.get("start", 99)) < HOOK_SECONDS for o in project.overlays)
    if not hook and not early_text:
        out.append(Finding(WARNING, "Nothing on screen in the first 2 seconds. Add a hook line "
                                    "(Hook and text) so people stop scrolling."))
    elif not hook:
        out.append(Finding(TIP, "No hook line. A big line of text in the first 2 s often keeps more viewers."))

    # Text, reactions, arrows inside the safe area (positions are grid spots, but check anyway)
    w, h = 1080, 1920
    for o in project.overlays:
        if o.get("type") == "text":
            fx, fy = text_layers.spot(o.get("position", "middle"))
            if not _inside_safe(fx * w - 1, fy * h - 1, fx * w + 1, fy * h + 1):
                out.append(Finding(WARNING, f"The pop-up \"{o.get('text', '')}\" sits under the platform buttons.",
                                   "popup_middle", "Move it to the middle", o.get("id") or str(id(o))))
        elif o.get("type") in ("arrow", "circle") and o.get("visible", True):
            side = int(float(o.get("size", 0.22)) * w)
            x, y = O.mark_xy(o, w, h, side)
            if not _inside_safe(x, y, x + side, y + side):
                out.append(Finding(TIP, f"The {o['type']} at {timeline.format_time(o['start'], True)} "
                                        "touches the platform button areas.",
                                   "mark_middle", "Move it to the middle", o["id"]))

    # Captions
    caps = C.visible(project)
    if not caps:
        out.append(Finding(TIP, "No captions. Most people watch without sound; Make captions on the Captions tab."))
    else:
        flashes = [c for c in caps if c["end"] - c["start"] < 0.3]
        long_ones = [c for c in caps if c["end"] - c["start"] > 3.5]
        if flashes:
            out.append(Finding(WARNING, f"{len(flashes)} caption line(s) flash by in under 0.3 s "
                                        f"(first at {timeline.format_time(flashes[0]['start'], True)}). "
                                        "Join them with the next line."))
        if long_ones:
            out.append(Finding(WARNING, f"{len(long_ones)} caption line(s) stay up for over 3.5 s "
                                        f"(first at {timeline.format_time(long_ones[0]['start'], True)}). "
                                        "Split them."))

    # Emoji font
    font = styles.emoji_font() if emoji_font == "auto" else emoji_font
    words = [hook] + [o.get("text", "") for o in project.overlays if o.get("type") == "text"]
    if not font and any(text_layers.split_emoji(t)[1] for t in words):
        out.append(Finding(WARNING, "Emoji won't show: " + styles.EMOJI_HELP))

    # Sound
    m = A.music(project)
    if m:
        speech = bool(A.speech_times(project))
        if float(m.get("volume", 0.15)) > 0.35:
            out.append(Finding(WARNING, f"The music is loud ({round(float(m['volume']) * 100)} %). "
                                        "Voices may be hard to hear.",
                               "music_quieter", "Set the music to 15 %"))
        if speech and m.get("ducking") == "off":
            out.append(Finding(WARNING, "The music doesn't dip when people talk.",
                               "music_duck", "Turn the dip on"))
        if not (m.get("licence") or A.licence_for(m["path"])):
            out.append(Finding(WARNING, "The music has no licence note. Write where it's from "
                                        "(Music and SFX tab) so you can credit it."))
    if A.loudness_target(project) is None:
        out.append(Finding(TIP, "Loudness isn't evened out. Platforms play videos at about -14 LUFS.",
                           "loudness_on", "Turn it on"))

    # Upload text
    up = publish.upload_fields(project)
    title = (project.upload or {}).get("title", "").strip()
    if not title:
        out.append(Finding(WARNING, "No YouTube title yet (it will use the edit name).",
                           "title_from_name", "Use the edit name"))
    elif len(title) > publish.TITLE_LIMIT:
        out.append(Finding(PROBLEM, f"The title is {len(title)} characters; YouTube allows 100.",
                           "title_trim", "Cut it to 100"))
    tags = _hashtags(up.get("hashtags", ""))
    if not tags:
        out.append(Finding(WARNING, "No hashtags.", "hashtags_default", "Add the channel hashtags"))
    elif len(tags) > MAX_HASHTAGS:
        out.append(Finding(WARNING, f"{len(tags)} hashtags. YouTube ignores all of them over 15.",
                           "hashtags_trim", "Keep the first 15"))
    if not up.get("description", "").strip():
        out.append(Finding(TIP, "No description. One or two lines help people find the video."))

    if project.channel == "Kids":
        out.append(Finding(WARNING, "Kids channel: when uploading, choose \"Yes, it's made for kids\" "
                                    "(YouTube requires this)."))

    order = {PROBLEM: 0, WARNING: 1, TIP: 2}
    out.sort(key=lambda f: order.get(f.level, 3))
    return out


def problems_only(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == PROBLEM]


def apply_fix(project, finding: Finding) -> bool:
    """Do the safe automatic fix. Returns True when something changed."""
    up = project.upload if isinstance(project.upload, dict) else {}
    project.upload = up
    m = (project.audio or {}).get("music") if isinstance(project.audio, dict) else None
    fix = finding.fix
    if fix == "title_from_name":
        up["title"] = project.title[:publish.TITLE_LIMIT]
    elif fix == "title_trim":
        up["title"] = up.get("title", "")[:publish.TITLE_LIMIT].rstrip()
    elif fix == "hashtags_default":
        up["hashtags"] = styles.DEFAULT_HASHTAGS.get(project.channel, "#shorts")
    elif fix == "hashtags_trim":
        up["hashtags"] = " ".join(_hashtags(up.get("hashtags", ""))[:MAX_HASHTAGS])
    elif fix == "music_quieter" and m:
        m["volume"] = 0.15
    elif fix == "music_duck" and m:
        m["ducking"] = "strong"
    elif fix == "loudness_on":
        project.audio["loudness"] = A.LOUDNESS
    elif fix in ("popup_middle", "mark_middle"):
        for o in project.overlays:
            if (o.get("id") or str(id(o))) == finding.target:
                if fix == "popup_middle":
                    o["position"] = "1,1"
                else:
                    o["x"], o["y"] = 0.46, 0.42
                break
        else:
            return False
    else:
        return False
    return True


# ================================================================ Claude
def claude_settings(settings: dict | None = None):
    s = settings or {}
    return SimpleNamespace(claude_backend=s.get("claude_backend", "auto"),
                           claude_model=s.get("claude_model") or None,
                           api_key_anthropic=os.environ.get("ANTHROPIC_API_KEY"))


def _ask(prompt: str, schema: dict, settings=None, asker=None):
    if asker is None:
        from clipping.providers import claude
        asker = claude.ask
    try:
        return asker(claude_settings(settings), prompt, schema=schema, label="Clip Editor")
    except Exception as e:  # ClaudeUnavailable, timeouts, bad JSON ...
        raise ReviewError(f"Claude couldn't help this time: {e}") from e


def series_memory(project, edits_root: str | None) -> str:
    """Phase 3 keeps a short story-so-far per channel; use it when it's there."""
    if not edits_root:
        return ""
    path = os.path.join(edits_root, f"series-{project.channel}.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[-3000:]
    except OSError:
        return ""


SUGGESTION_KINDS = ("caption", "hook", "popup", "title", "description", "hashtags", "note")
REVIEW_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(SUGGESTION_KINDS)},
            "target": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["kind", "target", "old", "new", "why"],
        "additionalProperties": False,
    },
}


def _words_for_review(project) -> dict:
    up = publish.upload_fields(project)
    return {
        "channel": project.channel,
        "mode": project.mode,
        "angle": project.angle,
        "length_seconds": round(project.total_length, 1),
        "hook": (project.hook or {}).get("text", ""),
        "captions": [{"id": c["id"], "at": round(c["start"], 1), "text": c["text"]} for c in C.visible(project)],
        "popups": [{"id": o.get("id") or f"popup{n}", "at": round(float(o["start"]), 1), "text": o.get("text", "")}
                   for n, o in enumerate(project.overlays) if o.get("type") == "text"],
        "title": up.get("title", ""),
        "description": up.get("description", ""),
        "hashtags": up.get("hashtags", ""),
        "script": [ln.get("text", "") for ln in (project.script_lines or []) if ln.get("text")],
    }


def review_prompt(project, memory: str = "") -> str:
    import json
    kids = ("\nThis is a KIDS channel: also add a 'note' for anything not suitable for young children "
            "(violence, scary or rude words, unsafe actions) and suggest gentle wording.") if project.channel == "Kids" else ""
    series = f"\n\nSeries memory (story so far, characters, running jokes):\n{memory}" if memory else ""
    return (
        "You are checking a vertical short video (YouTube Shorts, TikTok, Reels) before it is posted. "
        "The audience speaks English, mostly in Kenya, Nigeria and South Africa, plus worldwide.\n"
        "Here is every word in the edit as JSON:\n"
        f"{json.dumps(_words_for_review(project), ensure_ascii=False, indent=1)}{series}\n\n"
        "Suggest at most 12 changes that make it better: fix spelling and grammar in captions (keep the "
        "same meaning and about the same number of words, because the words are timed to speech), a "
        "stronger hook (under 8 words), a clearer title (under 70 characters, no clickbait lies), a short "
        "description, and 3 to 6 fitting hashtags including #shorts. Keep the channel's tone."
        f"{kids}\n\n"
        "Answer as a JSON list. Each item: kind (caption, hook, popup, title, description, hashtags or note), "
        "target (the caption or popup id, else empty), old (the current text), new (your text; empty for a "
        "note), why (one short plain-English reason). Don't suggest changes that only change capital letters."
    )


def claude_review(project, settings=None, edits_root=None, asker=None) -> list[dict]:
    raw = _ask(review_prompt(project, series_memory(project, edits_root)), REVIEW_SCHEMA, settings, asker)
    return validate_suggestions(project, raw)


def validate_suggestions(project, raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ReviewError("Claude's answer was not a list of suggestions.")
    caps = {c["id"]: c for c in project.captions}
    pops = {(o.get("id") or f"popup{n}"): o for n, o in enumerate(project.overlays) if o.get("type") == "text"}
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in SUGGESTION_KINDS:
            continue
        s = {k: str(item.get(k) or "").strip() for k in ("kind", "target", "old", "new", "why")}
        if s["kind"] == "caption" and s["target"] not in caps:
            continue
        if s["kind"] == "popup" and s["target"] not in pops:
            continue
        if s["kind"] != "note" and (not s["new"] or s["new"] == s["old"]):
            continue
        if s["kind"] == "title":
            s["new"] = s["new"][:publish.TITLE_LIMIT]
        s["tick"] = s["kind"] != "note"
        out.append(s)
    return out


def apply_suggestions(project, suggestions: list[dict]) -> int:
    """Apply the ticked suggestions. Returns how many changed something."""
    n = 0
    up = project.upload if isinstance(project.upload, dict) else {}
    project.upload = up
    pops = {(o.get("id") or f"popup{i}"): o for i, o in enumerate(project.overlays) if o.get("type") == "text"}
    for s in suggestions:
        if not s.get("tick") or s.get("kind") == "note":
            continue
        kind, new = s["kind"], s["new"]
        if kind == "caption":
            try:
                C.set_text(C.find(project, s["target"]), new)
            except KeyError:
                continue
        elif kind == "hook":
            project.hook = {"text": new, "seconds": float((project.hook or {}).get("seconds", 2.0))}
        elif kind == "popup":
            if s["target"] not in pops:
                continue
            pops[s["target"]]["text"] = new
        elif kind in ("title", "description", "hashtags"):
            up[kind] = new
        else:
            continue
        n += 1
    C.refresh(project)
    return n


# ---------------------------------------------------------------- suggested cut
CUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string"},
            "keep_from": {"type": "number"},
            "keep_to": {"type": "number"},
            "why": {"type": "string"},
        },
        "required": ["segment_id", "keep_from", "keep_to", "why"],
        "additionalProperties": False,
    },
}


def cut_prompt(project, target: int) -> str:
    import json
    C.refresh(project)
    moments = []
    for seg in project.segments:
        src = project.source(seg.source_id)
        words = [{"from": round(c["src_start"], 1), "to": round(c["src_end"], 1), "text": c["text"]}
                 for c in project.captions if c.get("segment_id") == seg.id and c.get("visible", True)]
        moments.append({"segment_id": seg.id, "video": src.name if src else "?",
                        "from": round(seg.source_in, 2), "to": round(seg.source_out, 2),
                        "speed": seg.speed, "length": round(seg.length, 2), "captions": words})
    return (
        f"This is the cut list of a vertical short video ({project.channel} channel, mode {project.mode}). "
        f"It is {round(project.total_length, 1)} s long. Make it about {target} s by keeping only the "
        "strongest parts: a gripping start, the clearest action, the payoff. Times are in each moment's "
        "own video (seconds).\n"
        f"{json.dumps(moments, ensure_ascii=False, indent=1)}\n\n"
        "Answer as a JSON list in the order they should play. Each item: segment_id (from the list), "
        "keep_from and keep_to (inside that moment's from..to; don't cut words in half, use the caption "
        "times), why (a short reason). Leave out moments to drop them. You may use one moment twice only "
        "as two separate, non-overlapping parts."
    )


def suggest_cut(project, target: int, settings=None, asker=None) -> list[dict]:
    raw = _ask(cut_prompt(project, target), CUT_SCHEMA, settings, asker)
    return validate_cut(project, raw, target)


def validate_cut(project, raw, target: float) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ReviewError("Claude didn't suggest any parts to keep.")
    segs = {s.id: s for s in project.segments}
    out, total = [], 0.0
    used: dict[str, list[tuple[float, float]]] = {}
    for item in raw:
        seg = segs.get(str(item.get("segment_id")))
        if seg is None:
            raise ReviewError("Claude suggested a moment that isn't in your cut list.")
        a, b = float(item["keep_from"]), float(item["keep_to"])
        if a < seg.source_in - 0.05 or b > seg.source_out + 0.05 or b - a < 0.3:
            raise ReviewError("Claude suggested times outside a moment.")
        a, b = max(a, seg.source_in), min(b, seg.source_out)
        for x, y in used.get(seg.id, []):
            if a < y and b > x:
                raise ReviewError("Claude suggested overlapping parts.")
        used.setdefault(seg.id, []).append((a, b))
        total += timeline.segment_length(a, b, seg.speed)
        out.append({"segment_id": seg.id, "keep_from": round(a, 3), "keep_to": round(b, 3),
                    "why": str(item.get("why", "")), "length": round(timeline.segment_length(a, b, seg.speed), 2)})
    if abs(total - target) > max(3.0, target * CUT_TOLERANCE):
        raise ReviewError(f"Claude's cut is {round(total)} s, too far from {target} s. Try again.")
    return out


def apply_cut(project, parts: list[dict]) -> None:
    """Rebuild the cut list from suggested parts (framing and speed kept).
    The first part of each moment keeps the moment's id, so captions and
    other anchored items follow it; a second part of the same moment is a
    new moment."""
    import copy
    import uuid
    segs = {s.id: s for s in project.segments}
    seen = set()
    new = []
    for p in parts:
        base = segs[p["segment_id"]]
        seg = copy.deepcopy(base)
        if base.id in seen:
            seg.id = f"seg_{uuid.uuid4().hex[:8]}"
        seen.add(base.id)
        seg.source_in, seg.source_out = p["keep_from"], p["keep_to"]
        new.append(seg)
    project.segments = new
    C.refresh(project)
    O.refresh(project)


@dataclass
class CutPreview:
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    before_total: float = 0.0
    after_total: float = 0.0


def describe_cut(project, parts: list[dict]) -> CutPreview:
    prev = CutPreview()
    for i, s in enumerate(project.segments, 1):
        prev.before.append(f"{i}. {timeline.format_time(s.source_in, True)}-{timeline.format_time(s.source_out, True)}"
                           f" ({timeline.format_time(s.length, True)})")
    index = {s.id: i for i, s in enumerate(project.segments, 1)}
    for i, p in enumerate(parts, 1):
        prev.after.append(f"{i}. from moment {index.get(p['segment_id'], '?')}: "
                          f"{timeline.format_time(p['keep_from'], True)}-{timeline.format_time(p['keep_to'], True)}"
                          f" ({timeline.format_time(p['length'], True)})")
    prev.before_total = project.total_length
    prev.after_total = sum(p["length"] for p in parts)
    return prev
