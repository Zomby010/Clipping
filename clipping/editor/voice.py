"""
clipping.editor.voice — Voices for the script: free edge-tts voices, or your own mic.

    make_all(...)       a voice file for every script line (cached, so only
                        changed lines are made again)
    record_line(...)    record one line from the laptop microphone
    voice_clips(...)    every line that has audio, placed in edit time. The sound
                        phase's mixer calls this to put the voices in the mix, dip
                        the music under them and keep sound effects clear of them.

Voice files live in ``<edit folder>/Voice/`` and are named after what they say
(text + voice + speed + pitch), so an unchanged line is never made twice.
edge-tts needs the internet; everything else here works offline.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading

from clipping.editor import script as S

VOICE_DIR = "Voice"

# (voice id, what to show, language). The free Microsoft neural voices edge-tts offers.
VOICES = (
    ("en-KE-AsiliaNeural", "Asilia · Kenyan English (woman)", "en"),
    ("en-KE-ChilembaNeural", "Chilemba · Kenyan English (man)", "en"),
    ("en-NG-AbeoNeural", "Abeo · Nigerian English (man)", "en"),
    ("en-NG-EzinneNeural", "Ezinne · Nigerian English (woman)", "en"),
    ("sw-KE-RafikiNeural", "Rafiki · Swahili (man)", "sw"),
    ("sw-KE-ZuriNeural", "Zuri · Swahili (woman)", "sw"),
    ("en-US-GuyNeural", "Guy · American English (man)", "en"),
    ("en-GB-RyanNeural", "Ryan · British English (man)", "en"),
)

# One default voice per channel (Kids: Swahili, gentle).
CHANNEL_VOICE = {
    "GTA 6": "en-KE-ChilembaNeural",
    "Sports": "en-NG-AbeoNeural",
    "Kids": "sw-KE-ZuriNeural",
    "Other": "en-KE-AsiliaNeural",
}

SPEED_LIMITS = (-50, 100)   # percent
PITCH_LIMITS = (-50, 50)    # Hz
SAMPLE_TEXT = {"en": "Here is how I sound. Pick me if you like it.",
               "sw": "Hivi ndivyo ninavyosikika. Nichague kama unapenda."}


class VoiceError(RuntimeError):
    """A voice could not be made. The message is plain English for the window."""


def label(voice_id: str) -> str:
    return next((lab for vid, lab, _ in VOICES if vid == voice_id), voice_id)


def language_of(voice_id: str) -> str:
    """"en" or "sw" (the language the script should be written in for this voice)."""
    return next((lang for vid, _, lang in VOICES if vid == voice_id), "sw" if voice_id.startswith("sw-") else "en")


# ---------------------------------------------------------------- settings
def settings(project) -> dict:
    """The voice settings with every missing piece filled in (project.voices is left alone)."""
    v = dict(project.voices or {})
    return {
        "default": v.get("default") or CHANNEL_VOICE.get(project.channel, CHANNEL_VOICE["Other"]),
        "speed": int(v.get("speed") or 0),
        "pitch": int(v.get("pitch") or 0),
        "characters": [dict(c) for c in v.get("characters", [])],
    }


def voice_for(project, line: dict) -> str:
    """The voice a line is spoken in: its own, its character's, or the default."""
    if line.get("voice"):
        return line["voice"]
    cfg = settings(project)
    for c in cfg["characters"]:
        if line.get("character") and c.get("name", "").lower() == line["character"].lower() and c.get("voice"):
            return c["voice"]
    return cfg["default"]


def rate_string(percent: int) -> str:
    return f"{int(percent):+d}%"


def pitch_string(hz: int) -> str:
    return f"{int(hz):+d}Hz"


def line_speed(project, line: dict) -> int:
    """Speed percent for this line: its own "Speed up" setting, else the project's."""
    return int(line["speed"]) if line.get("speed") is not None else settings(project)["speed"]


def cache_name(text: str, voice: str, speed: int, pitch: int) -> str:
    key = f"{text}|{voice}|{speed}|{pitch}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".mp3"


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", path],
                             capture_output=True, text=True, check=True, timeout=30).stdout
        return float(out.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


# ---------------------------------------------------------------- edge-tts
def edge_synth(text: str, voice: str, rate: str, pitch: str, out_path: str) -> list[dict]:
    """Speak ``text`` into ``out_path`` (mp3). Returns word timings [{"word","start","end"}]
    in seconds. Reuses clipping.voiceover's synthesis."""
    import asyncio

    from clipping import voiceover
    tmp_subs = out_path + ".vtt"
    try:
        segments = asyncio.run(voiceover._synthesize_async(
            text, voice, out_path, tmp_subs, rate=rate, pitch=pitch, boundary="WordBoundary"))
    except ImportError as e:
        raise VoiceError("The voice maker (edge-tts) is not installed. Run: pip install edge-tts") from e
    except Exception as e:  # network trouble shows up as many different exceptions
        _drop(out_path)
        name = type(e).__name__
        if any(k in name for k in ("Client", "Connection", "Timeout", "WebSocket", "DNS", "Gaierror")) \
                or isinstance(e, (OSError, TimeoutError)):
            raise VoiceError("Can't reach the voice service. Voices need the internet; check "
                             "your connection and try again. Everything else still works.") from e
        raise VoiceError(f"The voice could not be made ({name}: {e}).") from e
    finally:
        _drop(tmp_subs)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        _drop(out_path)
        raise VoiceError("The voice service returned no sound. Try again, or pick another voice.")
    return [{"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for seg in segments for w in seg.get("words", [])]


def _drop(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def make_line_audio(project, line: dict, folder: str, synth=edge_synth, force: bool = False) -> bool:
    """Make (or reuse) the voice file for one line. Returns True if a new file was made."""
    text = (line.get("text") or "").strip()
    if not text:
        raise VoiceError("This line has no words.")
    voice, speed = voice_for(project, line), line_speed(project, line)
    pitch = settings(project)["pitch"]
    name = cache_name(text, voice, speed, pitch)
    rel = os.path.join(VOICE_DIR, name)
    path = os.path.join(folder, rel)
    if not force and line.get("audio") == rel and os.path.exists(path):
        return False
    made = False
    if force or not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        words = synth(text, voice, rate_string(speed), pitch_string(pitch), tmp)
        os.replace(tmp, path)
        _save_words(path, words)
        made = True
    line["audio"] = rel
    line["duration"] = round(probe_duration(path), 3)
    line["words"] = _load_words(path)
    return made


def _words_file(path: str) -> str:
    return path[:-4] + ".words.json"


def _save_words(path: str, words: list[dict]) -> None:
    import json
    with open(_words_file(path), "w", encoding="utf-8") as f:
        json.dump(words, f)


def _load_words(path: str) -> list[dict]:
    import json
    try:
        with open(_words_file(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def make_all(project, folder: str, on_progress=None, cancel: threading.Event | None = None,
             synth=edge_synth, only: list[str] | None = None, log=None) -> dict:
    """Make the voice for every visible line (or just ``only`` line ids).
    Returns {"made": n, "reused": n, "failed": [(line_id, message)]}."""
    lines = [l for l in S.visible(project) if not only or l["id"] in only]
    result = {"made": 0, "reused": 0, "failed": []}
    for i, ln in enumerate(lines):
        if cancel is not None and cancel.is_set():
            break
        if ln.get("recorded"):          # your own voice: never replaced
            result["reused"] += 1
        else:
            try:
                result["made" if make_line_audio(project, ln, folder, synth) else "reused"] += 1
            except VoiceError as e:
                result["failed"].append((ln["id"], str(e)))
                if "internet" in str(e):   # no point trying every other line
                    break
                if log:
                    log(str(e))
        if on_progress:
            on_progress((i + 1) / len(lines))
    S.refresh(project)
    return result


# ---------------------------------------------------------------- mic
def mic_command(out_path: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", "default",
            "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "3", out_path]


def start_recording(out_path: str) -> subprocess.Popen:
    """Start recording from the default microphone. Call ``stop_recording`` to finish."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        return subprocess.Popen(mic_command(out_path), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        raise VoiceError("Can't start the microphone recorder (ffmpeg).") from e


def stop_recording(proc: subprocess.Popen) -> None:
    """Stop recording; ffmpeg finishes the file when it gets 'q'."""
    try:
        proc.communicate("q", timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()
        proc.wait()


def attach_recording(project, line: dict, folder: str, recorded_path: str) -> None:
    """Use a recorded file as a line's voice. There are no word timings, so
    captions spread the words evenly over the duration."""
    dur = probe_duration(recorded_path)
    if dur <= 0.2:
        _drop(recorded_path)
        raise VoiceError("Nothing was recorded. Check that your microphone is on and try again.")
    line["audio"] = os.path.relpath(recorded_path, folder)
    line["duration"] = round(dur, 3)
    line["words"] = even_words(line["text"], dur)
    line["recorded"] = True
    S.refresh(project)


def even_words(text: str, duration: float) -> list[dict]:
    words = (text or "").split()
    if not words or duration <= 0:
        return []
    step = duration / len(words)
    return [{"word": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
            for i, w in enumerate(words)]


def recording_path(folder: str, line_id: str) -> str:
    return os.path.join(folder, VOICE_DIR, f"mic-{line_id}.mp3")


# ---------------------------------------------------------------- speed fitting
def fit_speed(project, line: dict, available: float, limit: int = SPEED_LIMITS[1]) -> int | None:
    """Speed percent that would make ``line`` fit in ``available`` seconds,
    or None when even the fastest allowed speed would not do."""
    dur = S.line_duration(line)
    if available <= 0 or dur <= available:
        return None
    base = line_speed(project, line)
    needed = int(round(((dur / available) * (1 + base / 100.0) - 1) * 100)) + 2
    return needed if needed <= limit else None


# ---------------------------------------------------------------- for the mixer
def voice_clips(project, folder: str | None = None) -> list[dict]:
    """Every voice-over line that has audio, placed in edit time:

        {"path": absolute path to the audio file, "start": edit seconds,
         "end": edit seconds, "gain": linear volume (1.0 = as recorded),
         "line_id": script line id}

    ``folder`` is the edit's folder; it defaults to ``project.folder`` (set by
    the app). Lines whose file is missing are left out. Lines hidden because
    their moment was deleted are left out too.
    """
    folder = folder or getattr(project, "folder", None)
    if not folder:
        return []
    S.refresh(project)
    out = []
    for ln in S.visible(project):
        if not ln.get("audio"):
            continue
        path = ln["audio"] if os.path.isabs(ln["audio"]) else os.path.join(folder, ln["audio"])
        if not os.path.exists(path):
            continue
        dur = float(ln.get("duration") or 0.0) or probe_duration(path)
        out.append({"path": os.path.abspath(path), "start": round(ln["start"], 3),
                    "end": round(ln["start"] + dur, 3), "gain": float(ln.get("gain", 1.0)),
                    "line_id": ln["id"]})
    out.sort(key=lambda c: c["start"])
    return out


def sample_path(folder: str, voice: str) -> str:
    return os.path.join(folder, VOICE_DIR, f"sample-{voice}.mp3")


def make_sample(voice: str, folder: str, synth=edge_synth) -> str:
    """A short sample of a voice, cached. Returns the file path."""
    path = sample_path(folder, voice)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        synth(SAMPLE_TEXT.get(language_of(voice), SAMPLE_TEXT["en"]), voice, "+0%", "+0Hz", tmp)
        os.replace(tmp, path)
    return path
