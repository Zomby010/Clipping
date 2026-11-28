"""
clipping.editor.claude_editor — What Clip Editor asks Claude to do.

    watch(...)          read the speech and a sheet of still frames, write a timed
                        scene log (what happens, who, mood, viral score 0-10, why)
    suggest_angles(...) pitch three one-sentence angles for the story
    write_script(...)   write the voice-over lines, timed to the exact cut
    rewrite_line(...)   rewrite just one line ("funnier", "shorter")
    series_summary(...) a 2-line summary of an exported edit for the series memory

Claude is reached through ``clipping.providers.claude`` (the ``claude`` program you
are signed in to, or ANTHROPIC_API_KEY), the same way Clipping Studio does it.
Every reply is checked against a schema. A bad reply is asked for once more
with the problem pointed out; after that the user gets a plain-English error.
Nothing here needs Tk, so it all runs in a background job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from types import SimpleNamespace

import jsonschema

from clipping import frames
from clipping.editor import script as S
from clipping.editor import timeline
from clipping.providers import claude as _claude

STAR_SCORE = 7   # a moment with this score or more is starred
MAX_SHEETS = 12

MODE_HELP = {
    "explain": ("Explain", "A narrator explains what is happening and what the creator is doing."),
    "react": ("React", "Short reaction lines of our own over the original, which keeps its sound."),
    "new_story": ("New story", "A brand-new story told over the footage, ignoring what it was really about."),
}

CHANNEL_TONE = {
    "Kids": "Gentle and warm. Simple short words a young child understands. Never scary, no violence jokes, "
            "no insults. Kind, curious and playful.",
    "Sports": "A hype commentator: fast, loud with excitement, vivid, full of energy. Fair to everyone.",
    "GTA 6": "A funny storyteller: dry, quick jokes, playful exaggeration, a friendly gamer voice.",
    "Other": "Clear, friendly and lively.",
}


class ClaudeError(RuntimeError):
    """Claude could not do it. The message is plain English for the window."""


# ---------------------------------------------------------------- talking to Claude
def make_cfg(settings: dict | None = None) -> SimpleNamespace:
    """The few settings the Claude provider reads, from Clip Editor's settings."""
    s = settings or {}
    return SimpleNamespace(claude_backend=s.get("claude_backend", "auto"),
                           claude_model=s.get("claude_model") or None,
                           claude_effort=s.get("claude_effort", "high"),
                           api_key_anthropic=os.environ.get("ANTHROPIC_API_KEY"))


def ask(cfg, prompt: str, schema: dict | None, images=(), label: str = "Claude", timeout: float = 900):
    """One call to Claude. Tests replace this function."""
    return _claude.ask(cfg, prompt, schema=schema, images=list(images), timeout=timeout, label=label)


def check(data, schema: dict) -> None:
    """Raise ValueError with a short plain message when ``data`` does not fit ``schema``."""
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "the reply"
        raise ValueError(f"{where}: {e.message[:160]}") from None


def ask_checked(cfg, prompt: str, schema: dict, *, images=(), label="Claude", after=None):
    """Ask, check the reply (schema, then ``after(data)`` for the meaning), and
    ask once more if it was wrong. ``after`` returns the cleaned result or
    raises ValueError. Raises ClaudeError."""
    problem = None
    for attempt in range(2):
        text = prompt if problem is None else (
            prompt + f"\n\nYour previous answer was not usable: {problem}\nAnswer again, fixing exactly that. "
                     "Return only the JSON.")
        try:
            data = ask(cfg, text, schema, images=images, label=label)
        except _claude.ClaudeUnavailable as e:
            raise ClaudeError(_plain(str(e))) from e
        try:
            check(data, schema)
            return after(data) if after else data
        except ValueError as e:
            problem = str(e)
    raise ClaudeError(f"Claude's answer could not be used twice in a row ({problem}). "
                      "Try again, or press Stop and change your angle a little.")


def _plain(msg: str) -> str:
    if "not set up" in msg:
        return ("Claude is not set up on this laptop. Install Claude Code and sign in "
                "(type 'claude' in a terminal once), or put ANTHROPIC_API_KEY in the .env file.")
    return "Claude could not answer: " + msg


# ---------------------------------------------------------------- scene log
SCENE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "start": {"type": "number"}, "end": {"type": "number"},
            "what_happens": {"type": "string"}, "who": {"type": "string"}, "mood": {"type": "string"},
            "viral_score": {"type": "number"}, "why": {"type": "string"},
        },
        "required": ["start", "end", "what_happens", "who", "mood", "viral_score", "why"],
        "additionalProperties": False,
    },
}


def transcript_text(words: list[dict], per_line: int = 14) -> str:
    """Words [{word, src_start, src_end}] as '[12.3] a short line' rows for a prompt."""
    rows, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= per_line or re.search(r"[.!?]$", w["word"]):
            rows.append(f"[{cur[0]['src_start']:.1f}] " + " ".join(x["word"] for x in cur))
            cur = []
    if cur:
        rows.append(f"[{cur[0]['src_start']:.1f}] " + " ".join(x["word"] for x in cur))
    return "\n".join(rows) or "(no speech)"


def scene_prompt(name: str, duration: float, transcript: str, sheets_text: str, channel: str) -> str:
    return f"""You are helping a short-form video editor (YouTube Shorts, TikTok, Reels) for a "{channel}" channel.

The source video is "{name}", {duration:.0f} seconds long. Below are what people say (with the second
each line starts at) and a description of contact-sheet pictures you can see.

WHAT IS SAID
{transcript}

PICTURES
{sheets_text}

Write a scene log: walk through the video in order and list each distinct scene or beat.
For each one give:
- start and end, in seconds of the source video (end after start; scenes may not overlap)
- what_happens: one plain sentence about what we see and hear
- who: who or what is in it (people, characters, vehicles)
- mood: one or two words (tense, funny, shocking, calm, hype...)
- viral_score: 0 to 10, how likely this moment is to hold a viewer in a 9:16 short (10 = a moment people send to friends)
- why: one short sentence saying why that score

Use 6 to 30 scenes depending on length. Be honest with scores: most scenes are 2 to 6; keep 7 or more for the few
genuinely strong moments. Return only the JSON array."""


def clean_scene_log(items: list[dict], source_id: str, duration: float) -> list[dict]:
    out = []
    for it in items:
        start, end = float(it["start"]), float(it["end"])
        start, end = max(0.0, start), min(float(duration), end) if duration else end
        if end - start < 0.5:
            continue
        out.append({"source_id": source_id, "start": round(start, 2), "end": round(end, 2),
                    "what_happens": it["what_happens"].strip(), "who": it["who"].strip(),
                    "mood": it["mood"].strip(), "viral_score": int(round(min(10, max(0, it["viral_score"])))),
                    "why": it["why"].strip()})
    if not out:
        raise ValueError("the scene log had no usable scenes")
    out.sort(key=lambda s: s["start"])
    return out


def source_key(path: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


def scene_cache_path(folder: str, source_path: str) -> str:
    return os.path.join(folder, "Story", f"scenes-{source_key(source_path)}.json")


def stars_from(scene_log: list[dict], threshold: int = STAR_SCORE) -> list[dict]:
    return [s for s in scene_log if s.get("viral_score", 0) >= threshold]


def watch(cfg, source, folder: str, transcribe, force: bool = False, progress=None, log=None,
          channel: str = "GTA 6") -> list[dict]:
    """The scene log for ``source`` (cached per file in ``<edit>/Story``).

    ``transcribe(source) -> [{word, src_start, src_end}]`` gets the words of the whole video
    (the caller reuses the phase 2 transcript cache). Returns the log, each scene tagged with
    ``source_id``."""
    cache = scene_cache_path(folder, source.path)
    if not force and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                cached = json.load(f)
            for s in cached:
                s["source_id"] = source.id
            if progress:
                progress(1.0)
            return cached
        except (OSError, ValueError):
            pass
    if progress:
        progress(0.02)
    words = transcribe(source)
    if progress:
        progress(0.5)
    sheet_dir = os.path.join(folder, "Story", "sheets-" + source_key(source.path))
    sheets = frames.make_sheets(source.path, sheet_dir, end=source.duration or None, max_sheets=MAX_SHEETS)
    if not sheets and not words:
        raise ClaudeError("Could not read pictures or speech from this video, so there is nothing for Claude to look at.")
    if progress:
        progress(0.6)
    prompt = scene_prompt(source.name or os.path.basename(source.path), source.duration,
                          transcript_text(words), frames.describe_sheets(sheets) if sheets else "(no pictures)",
                          channel)
    clean = ask_checked(
        cfg, prompt, SCENE_SCHEMA, images=[s["path"] for s in sheets], label="Scene log",
        after=lambda data: clean_scene_log(data, source.id, source.duration))
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".tmp", "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=1)
    os.replace(cache + ".tmp", cache)
    if progress:
        progress(1.0)
    return clean


def scenes_between(scene_log: list[dict], source_id: str, start: float, end: float) -> list[dict]:
    return [s for s in scene_log if s["source_id"] == source_id and s["end"] > start and s["start"] < end]


# ---------------------------------------------------------------- angles
ANGLES_SCHEMA = {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3}


def suggest_angles(cfg, project, scene_text: str, series: str = "") -> list[str]:
    mode_name, mode_help = MODE_HELP.get(project.mode, MODE_HELP["explain"])
    prompt = f"""A video editor is making a short for a "{project.channel}" channel.
Mode: {mode_name}. {mode_help}
Channel tone: {CHANNEL_TONE.get(project.channel, CHANNEL_TONE['Other'])}

What is in the video:
{scene_text or '(not described yet)'}
{('Story so far / running jokes:' + chr(10) + series) if series.strip() else ''}

Pitch exactly 3 different angles. An angle is ONE sentence of direction for the narrator, for example
"Treat the crash as a heist gone wrong". Make them different in feel, concrete, and easy to say out loud.
Return only a JSON array of 3 strings."""
    return ask_checked(cfg, prompt, ANGLES_SCHEMA, label="Angles",
                       after=lambda d: [" ".join(a.split()) for a in d if a.strip()][:3])


# ---------------------------------------------------------------- script
SCRIPT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string"}, "offset": {"type": "number"}, "text": {"type": "string"},
            "character": {"type": "string"}, "cue": {"type": "string"},
        },
        "required": ["segment_id", "offset", "text", "character", "cue"],
        "additionalProperties": False,
    },
}


def moments_context(project, scene_log: list[dict], transcript_for) -> str:
    """The cut list as Claude sees it: each moment's place in the finished video, how long it
    is, what is in it, what is said, and how many words fit."""
    rows = []
    for p, seg in zip(timeline.layout(project.segments), project.segments):
        if seg.reaction:        # an inserted reaction gets no narration
            continue
        src = project.source(seg.source_id)
        scenes = scenes_between(scene_log, seg.source_id, seg.source_in, seg.source_out)
        what = "; ".join(f"[{s['start']:.0f}-{s['end']:.0f}s] {s['what_happens']} ({s['mood']})" for s in scenes) \
            or "(no scene notes)"
        rows.append(
            f"- segment_id \"{seg.id}\": starts at {p.edit_start:.1f}s in the finished video and lasts "
            f"{seg.length:.1f}s (from {src.name if src else '?'} {seg.source_in:.1f}-{seg.source_out:.1f}s"
            f"{', played at ' + format(seg.speed, 'g') + 'x speed' if (seg.speed or 1) != 1 else ''}).\n"
            f"  Fits about {S.line_budget(seg.length)} words in total.\n"
            f"  On screen: {what}\n"
            f"  Said in the original: {transcript_for(seg) or '(nothing)'}")
    return "\n".join(rows)


def script_prompt(project, moments: str, series: str, language: str, lines_to_fix: str = "") -> str:
    mode_name, mode_help = MODE_HELP.get(project.mode, MODE_HELP["explain"])
    chars = (project.voices or {}).get("characters", [])
    char_text = "\n".join(f"- {c['name']}: {c.get('about', '')}" for c in chars if c.get("name"))
    rules = {
        "explain": "Explain what is happening and what the creator is doing, clearly and with personality. "
                   "Do not read the original speech back word for word.",
        "react": "Write SHORT reaction lines (a few words to one short sentence): gasps, jokes, hot takes. "
                 "Leave gaps so the original sound can be heard. Prefer 1 line per moment, none if it is calm.",
        "new_story": "Tell a brand-new story that fits the pictures but IGNORES the original meaning and speech. "
                     "Use the characters below. Keep it moving: every moment should push the story on.",
    }[project.mode]
    return f"""You write the voice-over for a vertical short video on a "{project.channel}" channel.

MODE: {mode_name}. {mode_help}
{rules}
TONE: {CHANNEL_TONE.get(project.channel, CHANNEL_TONE['Other'])}
ANGLE: {project.angle.strip() or '(none given: pick a clear, fun one)'}
LANGUAGE: write the lines in {'Swahili' if language == 'sw' else 'plain, easy English that a Kenyan or Nigerian audience enjoys'}.
{('CHARACTERS (use only these names in "character"; leave "" for the narrator):' + chr(10) + char_text) if char_text else
 '"character" is always "" (one narrator).'}
{('STORY SO FAR / RUNNING JOKES (keep these consistent):' + chr(10) + series.strip()) if series.strip() else ''}

THE CUT (in play order). Times are seconds. You may write one or more lines per moment.
{moments}

RULES
- Each line is {{"segment_id", "offset", "text", "character", "cue"}}.
- "offset" is seconds from the START of that moment, in the finished video. Lines in a moment must not overlap:
  speaking takes about {1 / S.WORDS_PER_SECOND:.2f} seconds per word, so a line of N words needs N*{1 / S.WORDS_PER_SECOND:.2f} seconds.
- A line must finish before the next one starts and before its moment ends. Respect each moment's word total.
- "cue" is a free hint for sound later, like "sfx:whoosh" or "react:laugh", or "" when none.
- No stage directions, no emojis, no hashtags in "text". Write only what is spoken.
{lines_to_fix}
Return only the JSON array of lines, in play order."""


def clean_script(items: list[dict], project) -> list[dict]:
    known = {s.id for s in project.segments if not s.reaction}
    allowed = {c["name"].lower() for c in (project.voices or {}).get("characters", []) if c.get("name")}
    out = []
    for it in items:
        if it["segment_id"] not in known:
            continue
        text = " ".join(S.unwrap_text(it["text"]).split())
        if not text:
            continue
        who = (it.get("character") or "").strip()
        if who and allowed and who.lower() not in allowed:
            who = ""
        out.append({"segment_id": it["segment_id"], "offset": max(0.0, float(it["offset"])),
                    "text": text, "character": who, "cue": (it.get("cue") or "").strip()})
    if not out:
        raise ValueError("none of the lines belonged to a moment in the cut")
    return out


def write_script(cfg, project, scene_log: list[dict], transcript_for, series: str = "",
                 language: str = "en") -> list[dict]:
    """Claude's lines for the whole cut, checked (not yet turned into script lines:
    use ``script.from_claude``)."""
    if not project.segments:
        raise ClaudeError("Add some moments to the cut list first; the script is timed to them.")
    prompt = script_prompt(project, moments_context(project, scene_log, transcript_for), series, language)
    return ask_checked(cfg, prompt, SCRIPT_SCHEMA, label="Script", after=lambda d: clean_script(d, project))


# ---------------------------------------------------------------- one line
REWRITE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}


def rewrite_line(cfg, project, line: dict, available: float, note: str = "", language: str = "en",
                 series: str = "") -> str:
    """New words for one line, short enough for ``available`` seconds."""
    budget = S.line_budget(available)
    context = "\n".join(f"- ({l['character'] or 'narrator'}) {l['text']}" for l in S.visible(project))
    prompt = f"""Rewrite ONE line of a short video's voice-over.

Channel: {project.channel}. Mode: {MODE_HELP.get(project.mode, MODE_HELP['explain'])[0]}. Tone: {CHANNEL_TONE.get(project.channel, CHANNEL_TONE['Other'])}
Angle: {project.angle.strip() or '(none)'}
Language: {'Swahili' if language == 'sw' else 'plain, easy English'}
{('Story so far / running jokes:' + chr(10) + series.strip()) if series.strip() else ''}

The whole script now (for context):
{context}

The line to rewrite: "{line['text']}"
Spoken by: {line.get('character') or 'the narrator'}
Instruction: {note.strip() or 'Make it better.'}
It must be at most {budget} words (it has {available:.1f} seconds). Keep the same job in the story.
Return only JSON like {{"text": "..."}}."""
    text = ask_checked(cfg, prompt, REWRITE_SCHEMA, label="Rewrite", after=_clean_rewrite)["text"]
    return S.trim_to_budget(text, available)


def _clean_rewrite(d: dict) -> dict:
    text = " ".join(S.unwrap_text(d["text"]).split())
    if not text:
        raise ValueError("the new line was empty")
    return {"text": text}


# ---------------------------------------------------------------- series memory
def series_path(edits_root: str, channel: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", channel).strip("-").lower() or "other"
    return os.path.join(edits_root, f"series-{safe}.md")


def read_series(edits_root: str, channel: str) -> str:
    try:
        with open(series_path(edits_root, channel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def write_series(edits_root: str, channel: str, text: str) -> None:
    path = series_path(edits_root, channel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def series_summary(cfg, project) -> str:
    lines = "\n".join(f"({l['character'] or 'narrator'}) {l['text']}" for l in S.visible(project))
    prompt = f"""Here is the voice-over of a short video on a "{project.channel}" channel (angle: {project.angle or 'none'}).

{lines or '(no script)'}

Write a 2-line summary for the series memory: line 1 = what happened in this episode; line 2 = new characters,
running jokes or facts to keep consistent next time. Plain text, at most 45 words in total, no bullet marks."""
    try:
        text = ask(cfg, prompt, None, label="Series summary")
    except _claude.ClaudeUnavailable as e:
        raise ClaudeError(_plain(str(e))) from e
    text = " ".join(str(text).split())
    if not text:
        raise ClaudeError("Claude gave an empty summary. Try again.")
    return text
