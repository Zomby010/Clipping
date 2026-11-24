"""
clipping.integrations.clipsai_tool — ClipsAI (https://github.com/ClipsAI/clipsai, MIT)
as an optional helper.

  find_clips(video)     moments that stand on their own, found from what people
                        say (WhisperX transcript + ClipsAI's topic splitter).
                        Best for talk: podcasts, interviews, sports pundits,
                        streamers who talk a lot.
  speaker_crops(video)  where the person who is talking is, over time
                        (pyannote speaker diarization + face detection), for
                        a 9:16 crop that follows the speaker.
  follow_speaker(project, ...)
                        sets each moment's crop to the speaker, and can split a
                        moment where the speaker changes.

ClipsAI is not installed with the apps (it is large). Install it with

    pip install -r requirements-clipsai.txt

and put a Hugging Face token in .env as HF_TOKEN (needed by speaker_crops only;
accept the pyannote/speaker-diarization-3.1 terms on huggingface.co first).

Results are kept in outputs/clipsai/ per video, so the slow part runs once.

Command line (Claude runs these too):
    python -m clipping.integrations.clipsai_tool find  "video.mp4" [--min 15 --max 60]
    python -m clipping.integrations.clipsai_tool crops "video.mp4"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(REPO_DIR, "outputs", "clipsai")
INSTALL_HINT = ("ClipsAI isn't installed. In the Clipping folder run:\n"
                "  pip install -r requirements-clipsai.txt\n"
                "(it also needs ffmpeg and libmagic: sudo apt install ffmpeg libmagic1)")


class ClipsAIError(Exception):
    """A plain-English problem with ClipsAI."""


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("clipsai") is not None
    except (ImportError, ValueError):
        return False


def _require():
    try:
        import clipsai  # noqa: F401
    except ImportError as e:
        raise ClipsAIError(INSTALL_HINT) from e
    return clipsai


def hf_token() -> str:
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        try:
            with open(os.path.join(REPO_DIR, ".env"), encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("HF_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return "" if token.startswith("your-") else token


# ================================================================ cache
def _cache_path(video: str, kind: str, extra: str = "") -> str:
    st = os.stat(video)
    key = hashlib.sha1(f"{os.path.abspath(video)}|{st.st_size}|{int(st.st_mtime)}|{extra}".encode()).hexdigest()[:16]
    name = os.path.splitext(os.path.basename(video))[0][:40]
    return os.path.join(CACHE_DIR, f"{name}-{kind}-{key}.json")


def _cached(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _store(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _check_video(video: str) -> str:
    video = os.path.abspath(video)
    if not os.path.isfile(video):
        raise ClipsAIError(f"Can't find the video: {video}")
    return video


# ================================================================ finding clips
def find_clips(video: str, min_seconds: float = 0.0, max_seconds: float = 0.0, language: str | None = None,
               log=None, runner=None) -> list[dict]:
    """[{"start", "end", "length", "text"}] in video seconds, longest-first
    filtering by ``min_seconds`` / ``max_seconds`` (0 = no limit).
    ``runner(video, language)`` replaces ClipsAI (for tests)."""
    video = _check_video(video)
    cache = _cache_path(video, "clips", language or "")
    raw = _cached(cache)
    if raw is None:
        if log:
            log("ClipsAI is listening to the video (the first time takes a while on a laptop)…")
        raw = (runner or _run_find)(video, language)
        _store(cache, raw)
    out = []
    for c in raw:
        start, end = float(c["start"]), float(c["end"])
        length = end - start
        if length <= 0.5 or (min_seconds and length < min_seconds) or (max_seconds and length > max_seconds):
            continue
        out.append({"start": round(start, 2), "end": round(end, 2), "length": round(length, 2),
                    "text": c.get("text", "")})
    out.sort(key=lambda c: c["start"])
    return out


def _run_find(video: str, language: str | None) -> list[dict]:
    clipsai = _require()
    try:
        transcription = clipsai.Transcriber().transcribe(audio_file_path=video, iso6391_lang_code=language)
        clips = clipsai.ClipFinder().find_clips(transcription=transcription)
    except Exception as e:
        raise ClipsAIError(f"ClipsAI couldn't find clips: {e}") from e
    text = ""
    try:
        text = transcription.text
    except Exception:
        pass
    out = []
    for c in clips:
        item = {"start": float(c.start_time), "end": float(c.end_time)}
        try:
            item["text"] = text[int(c.start_char):int(c.end_char)].strip()[:400]
        except Exception:
            item["text"] = ""
        out.append(item)
    return out


# ================================================================ following the speaker
def speaker_crops(video: str, aspect=(9, 16), token: str | None = None, log=None, runner=None) -> dict:
    """{"crop_width", "crop_height", "original_width", "original_height",
        "segments": [{"x", "y", "start", "end", "speakers"}]}"""
    video = _check_video(video)
    cache = _cache_path(video, "crops", f"{aspect[0]}x{aspect[1]}")
    data = _cached(cache)
    if data is None:
        token = token if token is not None else hf_token()
        if not token and runner is None:
            raise ClipsAIError("Following the speaker needs a Hugging Face token: put HF_TOKEN in .env "
                               "(huggingface.co/settings/tokens) and accept the terms of "
                               "pyannote/speaker-diarization-3.1 on huggingface.co.")
        if log:
            log("ClipsAI is finding who talks when (the first time takes a while on a laptop)…")
        data = (runner or _run_crops)(video, aspect, token)
        _store(cache, data)
    return data


def _run_crops(video: str, aspect, token: str) -> dict:
    clipsai = _require()
    try:
        crops = clipsai.resize(video_file_path=video, pyannote_auth_token=token, aspect_ratio=tuple(aspect))
    except Exception as e:
        raise ClipsAIError(f"ClipsAI couldn't follow the speaker: {e}") from e
    return {
        "crop_width": int(crops.crop_width), "crop_height": int(crops.crop_height),
        "original_width": int(crops.original_width), "original_height": int(crops.original_height),
        "segments": [{"x": int(s.x), "y": int(s.y), "start": float(s.start_time), "end": float(s.end_time),
                      "speakers": list(s.speakers or [])} for s in crops.segments],
    }


def centre_between(crops: dict, start: float, end: float) -> tuple[float, float] | None:
    """The crop centre (fractions of the frame) used most between start and end."""
    weights: dict[tuple[float, float], float] = {}
    W, H = crops["original_width"] or 1, crops["original_height"] or 1
    for s in crops["segments"]:
        overlap = min(end, s["end"]) - max(start, s["start"])
        if overlap <= 0:
            continue
        c = (round((s["x"] + crops["crop_width"] / 2) / W, 4), round((s["y"] + crops["crop_height"] / 2) / H, 4))
        weights[c] = weights.get(c, 0.0) + overlap
    if not weights:
        return None
    return max(weights.items(), key=lambda kv: kv[1])[0]


def speaker_changes(crops: dict, start: float, end: float, min_piece: float = 1.5) -> list[float]:
    """Times inside (start, end) where the crop moves to another speaker,
    leaving no piece shorter than ``min_piece`` seconds."""
    cuts, last = [], start
    for s in sorted(crops["segments"], key=lambda s: s["start"]):
        t = s["start"]
        if start + min_piece <= t <= end - min_piece and t - last >= min_piece:
            cuts.append(round(t, 3))
            last = t
    return cuts


def follow_speaker(project, source_id: str | None = None, split: bool = False, crops_for=None) -> int:
    """Point every moment (of ``source_id``, or all) at the speaker. With
    ``split``, a moment is cut where the speaker changes so each piece has its
    own crop. ``crops_for(path)`` returns speaker_crops() for a video.
    Returns how many moments were framed."""
    crops_for = crops_for or speaker_crops
    done = 0
    i = 0
    while i < len(project.segments):
        seg = project.segments[i]
        src = project.source(seg.source_id)
        if src is None or (source_id and seg.source_id != source_id):
            i += 1
            continue
        crops = crops_for(src.path)
        if split:
            for t in reversed(speaker_changes(crops, seg.source_in, seg.source_out)):
                try:
                    project.split_segment(i, t)
                except ValueError:
                    pass
            seg = project.segments[i]
        centre = centre_between(crops, seg.source_in, seg.source_out)
        if centre is not None:
            seg.framing.mode = "auto"
            seg.framing.auto_x, seg.framing.auto_y = centre
            done += 1
        i += 1
    return done


# ================================================================ command line
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m clipping.integrations.clipsai_tool",
                                description="Find clips and follow the speaker with ClipsAI.")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="moments that stand on their own, from the speech")
    f.add_argument("video")
    f.add_argument("--min", type=float, default=0, help="shortest clip in seconds")
    f.add_argument("--max", type=float, default=0, help="longest clip in seconds")
    f.add_argument("--language", default=None, help="e.g. en (default: detect)")
    c = sub.add_parser("crops", help="where the speaker is, for a 9:16 crop")
    c.add_argument("video")
    sub.add_parser("check", help="is ClipsAI installed and is there a Hugging Face token?")
    a = p.parse_args(argv)
    try:
        if a.cmd == "check":
            print(f"ClipsAI installed: {'yes' if available() else 'no'}")
            print(f"HF_TOKEN set: {'yes' if hf_token() else 'no'}")
            if not available():
                print(INSTALL_HINT)
            return 0 if available() else 1
        if a.cmd == "find":
            out = find_clips(a.video, a.min, a.max, a.language, log=lambda m: print(m, file=sys.stderr))
        else:
            out = speaker_crops(a.video, log=lambda m: print(m, file=sys.stderr))
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    except ClipsAIError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
