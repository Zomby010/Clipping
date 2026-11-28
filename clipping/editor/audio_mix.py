"""
clipping.editor.audio_mix — The sound of the finished video, in the same
ffmpeg pass as the picture:

    original sound (from the cut, quieter while a reaction cut-away shows)
  + voice-over lines           (phase 3: clipping.editor.voice.voice_clips)
  + music                      looped / faded to fit, dipping under voices
  + sound effects              at their moments
  + reaction clips' own sound
  -> loudness evened out for the platforms (about -14 LUFS) -> [aout]

Music settings live in project.audio["music"]:

    {"path": ".../assets/bgm/epic/track.mp3", "mood": "epic", "volume": 0.15,
     "start_at": 0.0, "fade_in": 0.5, "fade_out": 1.5, "loop": true,
     "ducking": "strong" | "light" | "off"}

Ducking uses the times we already know: voice-over lines and caption lines
(what people say). The music volume follows a smooth envelope that dips under
each of them. With no such times and original sound present, it falls back to
ffmpeg's sidechain compressor keyed on the original sound.

Every extra sound is padded to the full length, so ffmpeg's amix keeps a fixed
scale (works on old and new ffmpeg) and ``volume=N`` puts levels back.
"""

from __future__ import annotations

import json
import math
import os

from clipping.editor import overlays as O

BGM_DIR = os.path.join(O.ASSETS_DIR, "bgm")
LICENCES_FILE = os.path.join(BGM_DIR, "licences.json")
MOODS = ("chill", "epic", "sad", "suspense", "upbeat")
MUSIC_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus")

DUCK_LEVELS = {"off": 1.0, "light": 0.5, "strong": 0.3}   # music level under speech
DUCK_ATTACK = 0.1
DUCK_RELEASE = 0.4
LOUDNESS = -14.0          # LUFS, what YouTube / TikTok / Reels play at
TRUE_PEAK = -1.5

MUSIC_DEFAULTS = {"volume": 0.15, "start_at": 0.0, "fade_in": 0.5, "fade_out": 1.5,
                  "loop": True, "ducking": "strong"}


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def _audio(project) -> dict:
    if not isinstance(project.audio, dict):
        project.audio = {}
    return project.audio


# ================================================================ music library
def music_library(bgm_dir: str = BGM_DIR) -> dict[str, list[str]]:
    """{mood: [tracks]}; every mood folder is listed, even when empty."""
    out = {}
    names = set(MOODS)
    if os.path.isdir(bgm_dir):
        names |= {n for n in os.listdir(bgm_dir) if os.path.isdir(os.path.join(bgm_dir, n))}
    for mood in sorted(names):
        out[mood] = O._list_files(os.path.join(bgm_dir, mood), MUSIC_EXTS)
    return out


def load_licences(path: str = LICENCES_FILE) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _licence_key(track: str, bgm_dir: str) -> str:
    try:
        rel = os.path.relpath(track, bgm_dir)
    except ValueError:
        return os.path.basename(track)
    return os.path.basename(track) if rel.startswith("..") else rel.replace(os.sep, "/")


def licence_for(track: str, bgm_dir: str = BGM_DIR, path: str | None = None) -> str:
    return load_licences(path or os.path.join(bgm_dir, "licences.json")).get(_licence_key(track, bgm_dir), "")


def save_licence(track: str, note: str, bgm_dir: str = BGM_DIR, path: str | None = None) -> None:
    path = path or os.path.join(bgm_dir, "licences.json")
    data = load_licences(path)
    key = _licence_key(track, bgm_dir)
    if note.strip():
        data[key] = note.strip()
    else:
        data.pop(key, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


def music(project) -> dict | None:
    """The music settings with defaults, or None when there is no music."""
    m = _audio(project).get("music") or {}
    if not m.get("path"):
        return None
    return {**MUSIC_DEFAULTS, **m}


def set_music(project, path: str | None, mood: str = "", **settings) -> None:
    a = _audio(project)
    if not path:
        a.pop("music", None)
        return
    old = a.get("music") or {}
    keep = {k: old[k] for k in MUSIC_DEFAULTS if k in old}
    a["music"] = {**keep, **settings, "path": os.path.abspath(path), "mood": mood or old.get("mood", "")}


def music_credit(project) -> str:
    """'Music: <track> (<licence note>)' for the upload text, or ''."""
    m = music(project)
    if not m:
        return ""
    name = os.path.splitext(os.path.basename(m["path"]))[0]
    note = m.get("licence") or licence_for(m["path"])
    return f"Music: {name}" + (f" ({note})" if note else "")


def loudness_target(project) -> float | None:
    v = _audio(project).get("loudness", LOUDNESS)
    return None if v in (None, "off", False) else float(v)


# ================================================================ voices (phase 3)
def voice_clips(project) -> list[dict]:
    """Voice-over lines placed in edit time, from phase 3 when it is installed."""
    try:
        from clipping.editor.voice import voice_clips as phase3_voice_clips
    except ImportError:
        return []
    try:
        return [v for v in phase3_voice_clips(project) if v.get("path") and os.path.exists(v["path"])]
    except Exception:
        return []


# ================================================================ ducking
def speech_times(project, voices: list[dict] | None = None) -> list[tuple[float, float]]:
    """When someone talks: voice-over lines and caption lines, merged."""
    from clipping.editor import captions as C
    spans = [(float(v["start"]), float(v["end"])) for v in (voices if voices is not None else voice_clips(project))]
    spans += [(float(c["start"]), float(c["end"])) for c in C.visible(project)
              if c.get("start") is not None and c.get("end") is not None]
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if b <= a:
            continue
        if merged and a - merged[-1][1] < DUCK_ATTACK + DUCK_RELEASE:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def duck_expression(spans: list[tuple[float, float]], level: float) -> str:
    """ffmpeg volume expression: 1 normally, ``level`` under each span, with
    short ramps (attack before, release after)."""
    if not spans or level >= 1.0:
        return "1"
    envs = []
    for a, b in spans:
        envs.append(f"min(clip((t-{_num(a - DUCK_ATTACK)})/{_num(DUCK_ATTACK)},0,1),"
                    f"clip(({_num(b + DUCK_RELEASE)}-t)/{_num(DUCK_RELEASE)},0,1))")
    env = envs[0]
    for e in envs[1:]:
        env = f"max({env},{e})"
    return f"1-{_num(1 - level)}*{env}"


def music_gain_at(spans, level: float, t: float) -> float:
    """The same envelope in Python (for tests and the timeline)."""
    if not spans or level >= 1.0:
        return 1.0
    env = 0.0
    for a, b in spans:
        up = min(1.0, max(0.0, (t - (a - DUCK_ATTACK)) / DUCK_ATTACK))
        down = min(1.0, max(0.0, ((b + DUCK_RELEASE) - t) / DUCK_RELEASE))
        env = max(env, min(up, down))
    return 1.0 - (1.0 - level) * env


# ================================================================ the graph
FMT = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _delay(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    return f"adelay={ms}|{ms}"


def _fit(total: float) -> str:
    return f"apad,atrim=0:{_num(total)},asetpts=PTS-STARTPTS"


def has_original_sound(project) -> bool:
    """Is any moment's own sound audible?"""
    from clipping.editor import render
    return any(render.original_level(project, s) > 0 and (project.source(s.source_id) or s).__dict__.get("has_audio")
               for s in project.segments)


def needs_mixer(project, reactions=(), voices=None) -> bool:
    voices = voice_clips(project) if voices is None else voices
    return bool(music(project) or O.visible_sfx(project) or reactions or voices
                or loudness_target(project) is not None)


def build(project, total: float, next_input: int, reactions: list[tuple[int, "O.ReactionClip"]] = (),
          main: str = "acat", out: str = "aout", has_original: bool = True,
          voices: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """(extra ffmpeg input args, filter parts) turning ``[main]`` into ``[out]``.
    ``reactions`` are (input index, clip) pairs whose inputs render.py already added."""
    args: list[str] = []
    parts: list[str] = []
    streams: list[str] = []
    k = next_input

    # Original sound, quieter while a reaction fills the screen.
    cut = [(r.start, r.end) for _i, r in reactions if r.style == "cutaway"]
    if cut:
        cond = "+".join(f"between(t,{_num(a)},{_num(b)})" for a, b in cut)
        parts.append(f"[{main}]volume='if({cond},{_num(O.CUTAWAY_MAIN_LEVEL)},1)':eval=frame[a_main]")
    else:
        parts.append(f"[{main}]anull[a_main]")
    streams.append("a_main")

    voices = voice_clips(project) if voices is None else voices
    # Above 100 % the voice is already near full scale, so everything else is turned
    # down instead. Below 100 % the voice itself is turned down.
    boost = max([float(v.get("gain", 1.0)) for v in voices] + [1.0])
    others = 1.0 / boost
    spans_v = [(float(v["start"]), float(v["end"])) for v in voices]
    if boost > 1.0:
        parts[0] = parts[0].replace("[a_main]", "[a_main_loud]")
        parts.append(f"[a_main_loud]volume='{duck_expression(spans_v, others)}':eval=frame[a_main]")
    for n, v in enumerate(voices):
        args += ["-i", v["path"]]
        gain = min(1.0, float(v.get("gain", 1.0)))
        parts.append(f"[{k}:a]{FMT},volume={_num(gain)},"
                     f"{_delay(float(v['start']))},{_fit(total)}[a_vo{n}]")
        streams.append(f"a_vo{n}")
        k += 1

    m = music(project)
    if m and os.path.exists(m["path"]):
        if m.get("loop", True):
            args += ["-stream_loop", "-1"]
        args += ["-ss", _num(max(0.0, float(m.get("start_at", 0.0)))), "-t", _num(total + 0.5), "-i", m["path"]]
        fi = min(float(m.get("fade_in", 0.5)), total / 3)
        fo = min(float(m.get("fade_out", 1.5)), total / 3)
        chain = [f"[{k}:a]{FMT}", f"atrim=0:{_num(total)}", "asetpts=PTS-STARTPTS",
                 f"volume={_num(float(m.get('volume', 0.15)))}"]
        if fi > 0:
            chain.append(f"afade=t=in:st=0:d={_num(fi)}")
        if fo > 0:
            chain.append(f"afade=t=out:st={_num(max(0.0, total - fo))}:d={_num(fo)}")
        k += 1
        level = DUCK_LEVELS.get(m.get("ducking", "strong"), 0.3)
        spans = speech_times(project, voices)
        if boost > 1.0:
            chain.append(f"volume='{duck_expression(spans_v, others)}':eval=frame")
        if level < 1.0 and spans:
            chain.append(f"volume='{duck_expression(spans, level)}':eval=frame")
            parts.append(",".join(chain) + f",{_fit(total)}[a_music]")
        elif level < 1.0 and has_original:
            # No known speech times: let the original sound push the music down.
            parts[0] = parts[0].replace("[a_main]", "[a_main_full]")
            parts.append("[a_main_full]asplit=2[a_main][a_key]")
            ratio = 8 if level <= 0.3 else 4
            parts.append(",".join(chain) + "[a_music_raw]")
            parts.append(f"[a_music_raw][a_key]sidechaincompress=threshold=0.04:ratio={ratio}:"
                         f"attack={int(DUCK_ATTACK * 1000)}:release={int(DUCK_RELEASE * 1000)},{_fit(total)}[a_music]")
        else:
            parts.append(",".join(chain) + f",{_fit(total)}[a_music]")
        streams.append("a_music")

    for n, fx in enumerate(O.visible_sfx(project)):
        if not os.path.exists(fx["path"]):
            continue
        args += ["-i", fx["path"]]
        parts.append(f"[{k}:a]{FMT},volume={_num(float(fx.get('gain', 0.8)))},"
                     f"{_delay(float(fx['start']))},{_fit(total)}[a_sfx{n}]")
        streams.append(f"a_sfx{n}")
        k += 1

    for n, (index, r) in enumerate(reactions):
        if not r.has_audio or r.gain <= 0:
            continue
        parts.append(f"[{index}:a]{FMT},asetpts=PTS-STARTPTS,atrim=0:{_num(r.end - r.start)},"
                     f"volume={_num(r.gain)},{_delay(r.start)},{_fit(total)}[a_re{n}]")
        streams.append(f"a_re{n}")

    tail = []
    target = loudness_target(project)
    if target is not None:
        quieter = max([float(v.get("gain", 1.0)) for v in voices], default=1.0)
        alone = voices and not (m or O.visible_sfx(project) or reactions) and not has_original_sound(project)
        if alone and 0 < quieter < 1.0:
            # Nothing to compare the voice with: evening out the loudness would undo the slider.
            target = max(-40.0, target + 20 * math.log10(quieter))
        tail.append(f"loudnorm=I={_num(target)}:TP={_num(TRUE_PEAK)}:LRA=11")
        tail.append("aresample=48000")
    if len(streams) == 1:
        parts.append(f"[{streams[0]}]" + (",".join(tail) if tail else "anull") + f"[{out}]")
    else:
        mix = (f"{''.join(f'[{s}]' for s in streams)}amix=inputs={len(streams)}:duration=first:"
               f"dropout_transition=0,volume={len(streams)}")
        parts.append(",".join([mix, *tail]) + f"[{out}]")
    return args, parts


def fingerprint_data(project) -> dict:
    def stamp(path):
        return os.path.getmtime(path) if path and os.path.exists(path) else 0
    m = music(project) or {}
    return {"music": m, "music_mtime": stamp(m.get("path")),
            "sfx": [{**f, "mtime": stamp(f.get("path"))} for f in O.visible_sfx(project)],
            "loudness": loudness_target(project),
            "voices": [{**v, "mtime": stamp(v.get("path"))} for v in voice_clips(project)]}


# ================================================================ measuring
def measure_loudness(path: str) -> float | None:
    """Integrated loudness (LUFS) of a finished file, via ffmpeg's ebur128."""
    import re
    import subprocess
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
                              "-af", "ebur128", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", res.stderr)
    return float(found[-1]) if found else None
