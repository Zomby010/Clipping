"""
clipping.story — Our own storyline, written from the whole video, read as the voice-over.

The AI reads ALL the captions of the source video (and, with Claude, looks
at pictures of each clip), then writes narration for every Short:

  series      one continuing story across all the Shorts from this video,
              in the order they happen: same names, same tone, each part
              ending on a hook for the next ("Part 1", "Part 2", ...).
  standalone  a different, complete mini-story for each Short.

The narration plays over the moving clip (voice-over overlay mode), so it
is written to fit the clip's length and to match what is on screen.

Claude writes it when it is available; Gemini is the backup. If both fail,
the pipeline uses the older one-clip commentary for that Short.
"""

from __future__ import annotations

import json
import os

WORDS_PER_SECOND = 2.4          # edge-tts neural voices speak about 140-150 words a minute
LENGTH_CAP = {"short": 35, "normal": 80, "long": 130}

STORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "series_title": {"type": "string"},
        "premise": {"type": "string"},
        "cast": {"type": "array", "items": {"type": "string"}},
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "rank": {"type": "integer"},
                    "episode_title": {"type": "string"},
                    "script": {"type": "string"},
                },
                "required": ["rank", "episode_title", "script"],
            },
        },
    },
    "required": ["series_title", "premise", "cast", "episodes"],
}

STYLE_HINTS = {
    "analysis": "a sharp, knowing narrator who reads between the lines",
    "reaction": "an excited narrator reacting as the story unfolds",
    "lesson": "a narrator who lets each part land on a lesson or moral",
    "summary": "a punchy storyteller who keeps it moving",
}


def clip_output_seconds(clip: dict, use_segments: bool = True) -> float:
    segs = clip.get("keep_segments") or []
    if use_segments and len(segs) > 1:
        return sum(float(s["end_time"]) - float(s["start_time"]) for s in segs)
    return float(clip["end_time"]) - float(clip["start_time"])


def word_budget(seconds: float, length: str = "normal", offset: float = 0.5) -> int:
    """How many words fit while the clip plays (with a little air at the end)."""
    fit = int(max(0.0, seconds - offset - 1.0) * WORDS_PER_SECOND)
    return max(12, min(fit, LENGTH_CAP.get(length, LENGTH_CAP["normal"])))


def clip_snippet(data_segmen: list[dict], start: float, end: float) -> str:
    parts = []
    for seg in data_segmen or []:
        if float(seg["end"]) > start and float(seg["start"]) < end:
            text = seg.get("text") or " ".join(w.get("word", "") for w in seg.get("words", []))
            text = " ".join(str(text).split())
            if text:
                parts.append(f"[{float(seg['start']):.1f}] {text}")
    return "\n".join(parts) or "(no speech in this part)"


def order_clips(clips: list[dict]) -> list[dict]:
    """Episodes follow the order the moments happen in the video."""
    return sorted(clips, key=lambda c: float(c["start_time"]))


def build_prompt(
    mode: str,
    full_transcript: str,
    clips: list[dict],
    data_segmen: list[dict],
    *,
    brief: str = "",
    style: str = "analysis",
    length: str = "normal",
    offset: float = 0.5,
    use_segments: bool = True,
    picture_for_rank: dict | None = None,
) -> str:
    picture_for_rank = picture_for_rank or {}
    ordered = order_clips(clips)
    blocks = []
    for n, clip in enumerate(ordered, 1):
        start, end = float(clip["start_time"]), float(clip["end_time"])
        secs = clip_output_seconds(clip, use_segments)
        budget = word_budget(secs, length, offset)
        pic = picture_for_rank.get(int(clip["rank"]))
        pic_line = f"\nPicture: {pic}" if pic else ""
        blocks.append(
            f"### Clip rank {clip['rank']} — Part {n} of {len(ordered)}\n"
            f"Source time: {start:.1f}s to {end:.1f}s (plays for about {secs:.0f}s)\n"
            f"Working title: {clip.get('title_inggris') or clip.get('title_indonesia') or '-'}\n"
            f"Write AT MOST {budget} words for this clip.{pic_line}\n"
            f"What is said in this clip:\n{clip_snippet(data_segmen, start, end)}\n"
        )

    if mode == "series":
        mode_rules = """STORY MODE: SERIES (one continuing story across all parts)
- Invent ONE original storyline that runs through every part, in the Part order given.
- Part 1 sets up who the characters are and what they want. Each later part moves the story forward.
- Keep names, roles, relationships and tone exactly consistent between parts. List the cast in "cast".
- End every part except the last on a hook that makes people want the next part.
- Later parts can open with a very short reminder ("Last time, ...") of at most one sentence.
- The last part pays the story off."""
    else:
        mode_rules = """STORY MODE: STANDALONE (a separate story for each clip)
- Write a different, complete mini-story for each clip, with its own setup and twist or punchline.
- Each one must make sense to someone who has seen no other clip.
- Do not reuse the same characters or jokes across clips.
- Use "series_title" and "premise" for the overall theme of the set, and "cast" for every character you named."""

    brief_text = brief.strip() or "None given. Choose the storyline you think will keep viewers watching to the end."
    narrator = STYLE_HINTS.get(style, STYLE_HINTS["analysis"])

    return f"""You are the head writer for a YouTube Shorts / TikTok channel. We turn moments from a long video into Shorts and give them OUR OWN story, told by a narrator whose voice plays over the clip while it keeps playing.

Read the WHOLE transcript of the source video below first, so you understand everything that happens, who is who, and what leads where. Then write the narration for each clip.

STORY DIRECTION FROM THE CHANNEL OWNER:
{brief_text}

{mode_rules}

NARRATION RULES (for every part):
- The narrator is {narrator}. Write in natural spoken English for an African and worldwide audience.
- It is a story, not a description: give the people on screen roles, motives and stakes. Add what the footage alone does not say. Do not just repeat the transcript.
- Match what the viewer sees: the narration starts about {offset:.1f}s into the clip and the clip keeps playing underneath. Follow the order of events in the clip.
- Respect each clip's word limit. Shorter is fine; longer gets cut off.
- Only the words to be read aloud: no stage directions, brackets, emojis, hashtags or speaker labels.
- No greetings ("Hey guys"), no "like and subscribe", no "in this video".
- Do not pretend to be the original creator or speak as them.
- "episode_title" is a short, catchy title (max 8 words) for that part.
- Return one episode per clip, using the clip's rank number.

THE CLIPS:
{chr(10).join(blocks)}

FULL TRANSCRIPT OF THE SOURCE VIDEO ([start - end] text):
{full_transcript}
"""


def _clean(result, clips: list[dict]) -> dict | None:
    if not isinstance(result, dict):
        return None
    ranks = {int(c["rank"]) for c in clips}
    episodes = {}
    for ep in result.get("episodes") or []:
        try:
            rank = int(ep.get("rank"))
        except (TypeError, ValueError):
            continue
        script = " ".join(str(ep.get("script") or "").split()).strip().strip('"')
        if rank in ranks and script:
            episodes[rank] = {"episode_title": str(ep.get("episode_title") or "").strip(), "script": script}
    if not episodes:
        return None
    return {
        "series_title": str(result.get("series_title") or "").strip(),
        "premise": str(result.get("premise") or "").strip(),
        "cast": [str(c) for c in (result.get("cast") or [])],
        "episodes": episodes,
    }


def _trim_to_budget(script: str, budget: int) -> str:
    words = script.split()
    if len(words) <= budget:
        return script
    cut = " ".join(words[:budget])
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > len(cut) * 0.5:
        return cut[: end + 1]
    return cut.rstrip(",;:-") + "."


def _ask_gemini(cfg, prompt: str):
    from google import genai
    from google.genai import types

    key = getattr(cfg, "api_key_gemini", "")
    if not key:
        raise RuntimeError("no GOOGLE_API_KEY")
    client = genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(api_version="v1beta"))
    conf = types.GenerateContentConfig(temperature=0.8, response_mime_type="application/json")
    last = None
    for model in (getattr(cfg, "gemini_model", "gemini-3-flash-preview"),
                  getattr(cfg, "gemini_fallback_model", "gemini-2.5-flash")):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt + "\nAnswer with JSON only, in this shape: "
                + json.dumps({"series_title": "...", "premise": "...", "cast": ["..."],
                              "episodes": [{"rank": 1, "episode_title": "...", "script": "..."}]}),
                config=conf,
            )
            text = (resp.text or "").strip()
            if text.startswith("```"):
                text = text.strip("`").split("\n", 1)[-1]
            return json.loads(text)
        except Exception as e:  # noqa: BLE001 - try the next model, report the last error
            last = e
    raise RuntimeError(str(last))


def write_stories(cfg, clips: list[dict], full_transcript: str, data_segmen: list[dict]) -> dict | None:
    """
    Returns {"series_title", "premise", "cast", "mode", "writer",
             "episodes": {rank: {"episode_title", "script", "part", "parts"}}}
    or None when no AI could write it.
    """
    mode = getattr(cfg, "story_mode", "series")
    if mode not in ("series", "standalone") or not clips:
        return None
    use_segments = not getattr(cfg, "no_segment_trim", False)
    length = getattr(cfg, "voiceover_length", "normal")
    offset = float(getattr(cfg, "voiceover_offset", 0.5))
    common = dict(brief=getattr(cfg, "story_brief", "") or "", style=getattr(cfg, "voiceover_style", "analysis"),
                  length=length, offset=offset, use_segments=use_segments)

    result, writer = None, None
    if getattr(cfg, "ai_provider", "gemini") == "claude":
        from clipping import frames
        from clipping.providers import claude

        pictures = {}
        video = getattr(cfg, "file_video_asli", "")
        if getattr(cfg, "claude_frames", True) and video and os.path.exists(video):
            for clip in clips:
                sheets = frames.make_sheets(
                    video, os.path.join(cfg.outputs_dir, "claude_frames"),
                    start=float(clip["start_time"]), end=float(clip["end_time"]),
                    max_sheets=1, prefix=f"clip{clip['rank']}",
                )
                if sheets:
                    pictures[int(clip["rank"])] = sheets[0]["path"]
        prompt = build_prompt(mode, full_transcript, clips, data_segmen,
                              picture_for_rank={r: os.path.basename(p) for r, p in pictures.items()}, **common)
        if pictures:
            prompt = ("Each clip has one picture: a 3x3 grid of frames from that clip, in time order. "
                      "Look at them so the story matches what is on screen.\n\n" + prompt)
        try:
            result = _clean(claude.ask(cfg, prompt, schema=STORY_SCHEMA, images=list(pictures.values()),
                                       label="Story"), clips)
            writer = "claude"
        except Exception as e:  # noqa: BLE001 - fall back to Gemini
            print(f"   ⚠️ [Story] Claude could not write the story ({e}). Trying Gemini...")

    if result is None:
        try:
            prompt = build_prompt(mode, full_transcript, clips, data_segmen, **common)
            print("   🧠 [Story] Writing the storyline with Gemini...")
            result = _clean(_ask_gemini(cfg, prompt), clips)
            writer = "gemini"
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️ [Story] Gemini could not write the story either ({e}).")
            return None
    if result is None:
        return None

    ordered = order_clips(clips)
    for n, clip in enumerate(ordered, 1):
        ep = result["episodes"].get(int(clip["rank"]))
        if not ep:
            continue
        budget = word_budget(clip_output_seconds(clip, use_segments), length, offset)
        ep["script"] = _trim_to_budget(ep["script"], budget)
        ep["part"], ep["parts"] = n, len(ordered)
    result["mode"], result["writer"] = mode, writer
    print(f"   📖 [Story] '{result['series_title'] or 'Untitled'}' ({mode}, by {writer}): "
          f"{len(result['episodes'])} part(s).")
    return result


def save(story: dict, path: str) -> None:
    data = dict(story)
    data["episodes"] = {str(k): v for k, v in story["episodes"].items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def as_text(story: dict) -> str:
    """Readable storyline for the My Clips folder."""
    lines = [story.get("series_title") or "Storyline", "=" * 40, ""]
    if story.get("premise"):
        lines += [story["premise"], ""]
    if story.get("cast"):
        lines += ["Cast: " + "; ".join(story["cast"]), ""]
    eps = sorted(story["episodes"].items(), key=lambda kv: kv[1].get("part", 0))
    for rank, ep in eps:
        head = f"Part {ep.get('part', '?')}" if story.get("mode") == "series" else f"Clip {rank}"
        lines += [f"{head}: {ep.get('episode_title', '')}  (clip rank {rank})", ep["script"], ""]
    lines.append(f"Written by: {story.get('writer', '?')}, mode: {story.get('mode', '?')}")
    return "\n".join(lines) + "\n"
