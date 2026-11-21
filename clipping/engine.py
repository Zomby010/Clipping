"""
clipping.engine — Download, Transcription & Gemini AI Analysis

Maps to Cell 2 (The Engine) of the notebook.
"""

import io
import json
import os
import re
import time

from yt_dlp import YoutubeDL
from faster_whisper import WhisperModel


# ==============================================================================
# TAHAP 1: DOWNLOAD VIDEO
# ==============================================================================

def _build_ydl_format_selector(download_source_height: str | int) -> str:
    """
    Build a yt-dlp format selector string for source-quality preference.
    """
    # Skip AV1 codec as it lacks HW acceleration on many platforms (e.g., Colab T4)
    # and causes decoding failures in OpenCV/FFmpeg software fallbacks.
    # Note: Using [vcodec!*=av01] to safely ensure it does not contain 'av01' anywhere.
    codec_filter = "[vcodec!*=av01]"

    if download_source_height == "max":
        return f"bestvideo{codec_filter}+bestaudio/best{codec_filter}"

    try:
        h_val = int(download_source_height)
    except (ValueError, TypeError):
        h_val = 0

    if 0 < h_val <= 1080:
        # For standard resolutions, strictly prefer native MP4 (H.264/AAC), ensuring no AV1 in mp4
        return (
            f"bestvideo[height<=?{h_val}][ext=mp4]{codec_filter}+bestaudio[ext=m4a]/"
            f"bestvideo[height<=?{h_val}]{codec_filter}+bestaudio/"
            f"best[height<=?{h_val}][ext=mp4]{codec_filter}/"
            f"best[height<=?{h_val}]{codec_filter}"
        )

    return (
        f"bestvideo[height<=?{download_source_height}]{codec_filter}+bestaudio/"
        f"best[height<=?{download_source_height}]{codec_filter}"
    )


_PLATFORM_LABELS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "gdrive": "Google Drive",
}


def _extract_gdrive_file_id(url: str) -> str | None:
    """Extract the Google Drive file ID from various URL formats."""
    import re as _re
    m = _re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = _re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def _download_gdrive(url: str, output_path: str) -> None:
    """Download a video from Google Drive using gdown (more reliable than yt-dlp)."""
    import gdown

    file_id = _extract_gdrive_file_id(url)
    if not file_id:
        raise RuntimeError(
            f"Could not read the file ID from the Google Drive link: {url}\n"
            "      Supported formats:\n"
            "        • https://drive.google.com/file/d/FILE_ID/view\n"
            "        • https://drive.google.com/open?id=FILE_ID"
        )

    download_url = f"https://drive.google.com/uc?id={file_id}"
    print(f"      📥 File ID: {file_id}")
    gdown.download(download_url, output_path, quiet=False)


def _ydl_progress_hook(d: dict) -> None:
    """Render satu baris progress bar download dari data hook yt-dlp.

    yt-dlp mengunduh stream video dan audio secara terpisah, jadi hook ini
    dipanggil untuk masing-masing; newline saat "finished" menjaga tiap bar
    berada di barisnya sendiri.
    """
    status = d.get("status")
    if status == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes", 0)
        speed = d.get("speed")
        eta = d.get("eta")
        spd = f"{speed / 1024 / 1024:4.1f}MB/s" if speed else "  --MB/s"
        eta_s = f"{eta:>3}s" if eta is not None else " --s"
        if total:
            pct = downloaded / total * 100
            filled = int(20 * downloaded / total)
            bar = "█" * filled + " " * (20 - filled)
            print(
                f"\r      Unduh: {pct:3.0f}%|{bar}| "
                f"{downloaded / 1048576:.0f}/{total / 1048576:.0f}MB {spd} ETA {eta_s}   ",
                end="", flush=True,
            )
        else:
            # Ukuran tidak diketahui (live/streamed manifest) — tampilkan byte + speed saja.
            print(
                f"\r      Unduh: {downloaded / 1048576:.0f}MB {spd}   ",
                end="", flush=True,
            )
    elif status == "finished":
        print(flush=True)  # tutup baris bar untuk stream ini


def _apply_cookies(opts: dict, cookies: str | bytes | None) -> None:
    """Apply cookies to yt-dlp options dictionary."""
    if not cookies:
        return
    if isinstance(cookies, bytes):
        # yt-dlp reads and writes this stream. Each downloader gets a fresh copy.
        opts["cookiefile"] = io.StringIO(cookies.decode("utf-8"))
    elif cookies.endswith(".txt") or os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    else:
        # Browser format: (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (cookies, None, None, None)


def download_video(
    url: str,
    output_path: str,
    use_dlp_subs: bool = False,
    download_source_height: str | int = "max",
    source_platform: str = "youtube",
    yt_cookies: str | bytes | None = None,
) -> None:
    """
    Download a video to *output_path* with configurable source height.

    Parameters
    ----------
    source_platform : str
        One of ``"youtube"`` (default), ``"tiktok"``, ``"instagram"``,
        or ``"gdrive"``.
    """
    platform_label = _PLATFORM_LABELS.get(source_platform, source_platform)
    uses_youtube_format = source_platform == "youtube"

    print(f"[1/3] Downloading the video from {platform_label}...")
    if download_source_height == "max":
        print("      🎯 Source quality: highest available", flush=True)
    else:
        print(f"      🎯 Source quality: up to {download_source_height}p", flush=True)

    # --- Google Drive: use gdown instead of yt-dlp ---
    if source_platform == "gdrive":
        _download_gdrive(url, output_path)
        if not os.path.exists(output_path):
            raise RuntimeError(
                f"❌ Google Drive download failed: no file found at {output_path}"
            )
        print(f"      ✅ Video downloaded from Google Drive.", flush=True)
        return

    # --- Build yt-dlp options per platform ---
    if uses_youtube_format:
        # YouTube: complex format selector + AV1 filter + remote components
        ydl_opts = {
            "format": _build_ydl_format_selector(download_source_height),
            "outtmpl": output_path,
            "quiet": True,
            "merge_output_format": "mp4",
            "remote_components": ["ejs:github"],
            "progress_hooks": [_ydl_progress_hook],
            "extractor_args": {"youtube": ["player_client=android,web"]},
        }
    else:
        # TikTok / Instagram: ensure video and audio are merged
        # We explicitly prefer H.264 over H.265 (TikTok's bytevc1) to prevent 
        # PyAV/faster-whisper from crashing with IndexError on Kaggle/Colab.
        ydl_opts = {
            "format": "bestvideo[vcodec^=h264]+bestaudio/best[vcodec^=h264]/best",
            "outtmpl": output_path,
            "quiet": True,
            "merge_output_format": "mp4",
            "progress_hooks": [_ydl_progress_hook],
        }

    # --- Subtitle download — only supported for YouTube ---
    if use_dlp_subs and uses_youtube_format:
        print("      Looking for automatic subtitles (en / id)...")
        import glob

        for lang in ["en", "id"]:
            ydl_opts_subs = ydl_opts.copy()
            _apply_cookies(ydl_opts_subs, yt_cookies)
            ydl_opts_subs.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [lang],
                "subtitlesformat": "json3",
                "skip_download": True,  # Hanya fokus download subtitle
            })

            try:
                with YoutubeDL(ydl_opts_subs) as ydl:
                    ydl.download([url])

                # Cek apakah json3 untuk bahasa ini benar-benar terdownload
                if glob.glob(output_path.replace(".mp4", f".*.json3")):
                    print(f"      ✅ Found '{lang}' subtitles. Moving on to the video...")
                    break
            except Exception as e:
                print(f"      ⚠️ Could not get '{lang}' subtitles ({e}). Trying the next option...")
    elif use_dlp_subs and not uses_youtube_format:
        print(f"      ℹ️ {platform_label} has no automatic subtitles. Whisper will transcribe instead.")

    # Jalankan download video terpisah dari urusan subtitle
    _apply_cookies(ydl_opts, yt_cookies)
    with YoutubeDL(ydl_opts) as ydl:
        # Extra step to verify resolution before downloading
        try:
            info = ydl.extract_info(url, download=False)
            best_h = info.get("height", "unknown")
            v_codec = info.get("vcodec", "unknown")
            print(f"      ✅ Downloading: {best_h}p (codec: {v_codec})", flush=True)
        except Exception as e:
            print(f"      ⚠️ Could not read the video details: {e}", flush=True)

        ydl.download([url])

    # --- Post-download verification ---
    if not os.path.exists(output_path):
        raise RuntimeError(
            f"❌ Download from {platform_label} failed: no video file found at {output_path}.\n"
            "      Check that the link is correct and the video is public."
        )


# ==============================================================================
# TAHAP 2: TRANSKRIPSI WHISPER & JSON3 FALLBACK
# ==============================================================================

def parse_youtube_json3_subs(json_path: str, max_words_per_subtitle: int = 5) -> tuple[str, list[dict]]:
    """
    Parse downloaded YouTube JSON3 subtitles into transkrip_lengkap and data_segmen.
    Returns empty string/list if parsing fails.
    """
    import json

    print("[2/3] Reading the YouTube subtitles (JSON3)...")
    transkrip_lengkap = ""
    data_segmen = []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            subs_data = json.load(f)

        events = subs_data.get("events", [])

        flat_words = []
        for event in events:
            # YouTube timestamps are in ms
            t_start = event.get("tStartMs", 0) / 1000.0
            d_duration = event.get("dDurationMs", 0) / 1000.0
            event_end = t_start + d_duration

            segs = event.get("segs", [])
            for i, seg in enumerate(segs):
                text = seg.get("utf8", "")
                if not text.strip() or text == "\n":
                    continue

                # tOffsetMs is offset from t_start
                offset = seg.get("tOffsetMs", 0) / 1000.0
                seg_start = t_start + offset

                # Determine end of this segment
                if i < len(segs) - 1:
                    next_offset = segs[i + 1].get("tOffsetMs", 0) / 1000.0
                    seg_end = t_start + next_offset
                else:
                    seg_end = event_end

                if seg_end <= seg_start:
                    seg_end = seg_start + 1.0  # Fallback duration

                # Clean up YouTube subtitle artifacts
                clean_text = text.replace("\n", " ").replace("\u200b", "").strip()
                # Remove HTML tags (e.g., <i>, </i>, <b>, </b>, <font color="...">)
                clean_text = re.sub(r"<[^>]+>", "", clean_text)
                # Remove YouTube annotation brackets: [Music], [Applause], [Laughter], etc.
                clean_text = re.sub(r"\[[\w\s]+\]", "", clean_text)
                # Remove speaker change markers: >> 
                clean_text = re.sub(r">>\s*", "", clean_text)
                # Remove music symbols: ♪, ♫, etc.
                clean_text = re.sub(r"[♪♫♬♩]", "", clean_text)
                # Remove leading dashes often used for speaker identification
                clean_text = re.sub(r"^\s*-\s+", "", clean_text)
                # Collapse multiple spaces into one
                clean_text = re.sub(r"\s{2,}", " ", clean_text).strip()

                if clean_text:
                    # Memecah teks menjadi kata tunggal agar karaoke per-kata bekerja seperti whisper
                    words_in_seg = clean_text.split()
                    if not words_in_seg:
                        continue

                    duration_per_word = (seg_end - seg_start) / len(words_in_seg)

                    for w_idx, w_text in enumerate(words_in_seg):
                        w_start = seg_start + (w_idx * duration_per_word)
                        w_end = w_start + duration_per_word

                        flat_words.append({
                            "word": w_text,
                            "start": w_start,
                            "end": w_end,
                        })

        # Adjust end times based on the start time of the next word to prevent overlaps
        for i in range(len(flat_words) - 1):
            if flat_words[i]["end"] > flat_words[i + 1]["start"]:
                flat_words[i]["end"] = max(flat_words[i]["start"] + 0.1, flat_words[i + 1]["start"])

        # Group them into segments
        chunk_words = []
        chunk_start = 0.0

        for i, w in enumerate(flat_words):
            if len(chunk_words) == 0:
                chunk_start = w["start"]

            chunk_words.append(w)

            if len(chunk_words) == max_words_per_subtitle or i == len(flat_words) - 1:
                chunk_text = " ".join([cw["word"] for cw in chunk_words])
                chunk_end = w["end"]
                transkrip_lengkap += f"[{chunk_start:.1f} - {chunk_end:.1f}] {chunk_text}\n"

                data_segmen.append({
                    "start": chunk_start,
                    "end": chunk_end,
                    "words": chunk_words,
                })
                chunk_words = []

        return transkrip_lengkap, data_segmen

    except Exception as e:
        print(f"⚠️ Could not read the JSON3 subtitles: {e}")
        return "", []


def transcribe_video(
    video_path: str,
    max_words_per_subtitle: int = 5,
    model_size: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    whisper_model_instance=None,
) -> tuple[str, list[dict]]:
    """
    Transcribe *video_path* using Faster-Whisper.

    Parameters
    ----------
    whisper_model_instance : WhisperModel or None
        Optional pre-loaded WhisperModel instance.  When provided the
        *model_size*, *device* and *compute_type* arguments are ignored
        and the given model is reused directly.  This avoids expensive
        re-loading when processing multiple videos in batch mode.

    Returns
    -------
    transkrip_lengkap : str
        Human-readable transcript with timestamps.
    data_segmen : list[dict]
        Word-level segments grouped by *max_words_per_subtitle*.
    """
    print("[2/3] Transcribing with Faster-Whisper (word by word)...")

    # Langkah-langkah ini berjalan tanpa output di dalam faster-whisper sebelum
    # segmen pertama dihasilkan, jadi kita umumkan tiap fase — kalau tidak, run
    # pertama di CPU (download model + decode seluruh audio) terlihat seperti hang.
    if whisper_model_instance is not None:
        model = whisper_model_instance
        print(
            f"      ♻️ Reusing the Whisper model that is already loaded (batch mode)",
            flush=True,
        )
    else:
        print(
            f"      ⏳ Loading Whisper model '{model_size}' ({device})"
            " (the first download can take a while)...",
            flush=True,
        )
        model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print("      ⏳ Decoding the audio and extracting features (no output yet)...", flush=True)
    segments, info = model.transcribe(video_path, beam_size=5, word_timestamps=True)

    transkrip_lengkap = ""
    data_segmen: list[dict] = []

    # Progress bar berdasarkan timestamp audio. faster-whisper men-stream segmen
    # secara lazy, jadi bar dimajukan ke waktu akhir tiap segmen saat tiba.
    from tqdm import tqdm

    total_dur = round(info.duration, 2)
    progress = tqdm(
        total=total_dur,
        unit="s",
        desc="      Transkripsi",
        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]",
    )

    for segment in segments:
        # Clamp agar floating-point drift melewati durasi tidak overshoot.
        progress.update(min(segment.end, total_dur) - progress.n)
        transkrip_lengkap += f"[{segment.start:.1f} - {segment.end:.1f}] {segment.text}\n"

        if segment.words:
            chunk_words: list[dict] = []
            chunk_start = 0.0

            for i, w in enumerate(segment.words):
                if len(chunk_words) == 0:
                    chunk_start = w.start

                chunk_words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                })

                if len(chunk_words) == max_words_per_subtitle or i == len(segment.words) - 1:
                    data_segmen.append({
                        "start": chunk_start,
                        "end": w.end,
                        "words": chunk_words,
                    })
                    chunk_words = []

    progress.update(total_dur - progress.n)  # snap ke 100% saat selesai
    progress.close()
    return transkrip_lengkap, data_segmen


# ==============================================================================
# TAHAP 3: ANALISIS GEMINI AI
# ==============================================================================

TARGET_ACCOUNTS = {
    "Business": {
        "akun_tujuan": "Business.Mereska",
        "angle_desc": "When the angle is business, brands, revenue, sales, founders, marketing or small businesses.",
        "bio": "Business insight, founder stories and local brands. Business | Founder | Finance | Beauty | Marketing"
    },
    "Life": {
        "akun_tujuan": "Life.Mereska",
        "angle_desc": "When the angle is personal life, lifestyle, skincare, career, mindset, relationships, personal finance or self-growth.",
        "bio": "Insight clips to upgrade your life and mindset. Podcast | Career | Finance | Beauty | Self Growth"
    },
    "Creator": {
        "akun_tujuan": "Creator.Mereska",
        "angle_desc": "When the angle is digital content, AI, affiliate, tools, clipping, monetization or making money from content.",
        "bio": "Turning digital content into money. AI | Affiliate | Clips | Tools | Monetize"
    },
    "Muslim": {
        "akun_tujuan": "Muslim.Mereska",
        "angle_desc": "When the angle is faith, provision, prayer, worship, working for God, Islamic family life or business with Islamic values.",
        "bio": "Reminders on work, provision and a life with Islamic values. Islamic | Provision | Family | Work | Business"
    }
}

def _build_account_classification_prompt() -> str:
    lines = []
    for tipe, data in TARGET_ACCOUNTS.items():
        lines.append(f"- {tipe}: {data['angle_desc']}")
        lines.append(f"  (akun_tujuan: \"{data['akun_tujuan']}\", bio: \"{data['bio']}\")")
    return "\n".join(lines)


# ---- Retry Config ----
MAX_ATTEMPTS = 10
INITIAL_WAIT_SECONDS = 60
WAIT_INCREMENT_SECONDS = 30
REQUEST_TIMEOUT_MS = 15 * 60 * 1000  # 15 menit
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _extract_status_code(exc: Exception):
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    match = re.search(r"\b(408|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True

    code = _extract_status_code(exc)
    if code in RETRYABLE_STATUS_CODES:
        return True

    msg = str(exc).lower()
    keywords = (
        "timeout", "temporarily unavailable", "deadline",
        "connection reset", "connection aborted", "service unavailable",
    )
    return any(k in msg for k in keywords)


def _generate_json_with_retry(client, model, fallback_model, contents, config):
    last_exc = None
    status_code = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[Gemini] Attempt {attempt}/{MAX_ATTEMPTS}...")

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise ValueError("Gemini returned an empty response.text.")

            return json.loads(text)

        except Exception as exc:
            last_exc = exc
            status_code = _extract_status_code(exc)
            retryable = _is_retryable(exc)

            print(
                f"[Gemini] Attempt {attempt}/{MAX_ATTEMPTS} failed | "
                f"status={status_code} | error={exc}"
            )

            if (not retryable) or attempt == MAX_ATTEMPTS:
                break

            wait_seconds = INITIAL_WAIT_SECONDS + ((attempt - 1) * WAIT_INCREMENT_SECONDS)
            print(f"[Gemini] Trying again in {wait_seconds} seconds...")
            time.sleep(wait_seconds)

    print(f"[Gemini] The main model ({model}) failed.")
    if fallback_model:
        print(f"[Gemini] Trying once more with the fallback model ({fallback_model})...")
        try:
            response = client.models.generate_content(
                model=fallback_model,
                contents=contents,
                config=config,
            )
            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise ValueError("The Gemini fallback returned an empty response.text.")

            return json.loads(text)
        except Exception as exc_fallback:
            print(f"[Gemini] Fallback model failed | error={exc_fallback}")
            raise RuntimeError(
                f"Both the main and fallback Gemini models failed. "
                f"Main: status={status_code}, error={last_exc} | "
                f"Fallback: error={exc_fallback}"
            ) from exc_fallback

    raise RuntimeError(
        f"Gemini failed after {MAX_ATTEMPTS} attempts. Last error: {last_exc}"
    ) from last_exc


# ==== KONFIGURASI DURASI KLIP ====
# Ubah nilai di bawah ini jika ingin mengganti batas durasi klip (dalam detik)
MIN_CLIP_DURATION = 20
MAX_CLIP_DURATION = 179

def clip_length_range(cfg=None) -> tuple[int, int]:
    """(shortest, longest) clip length in seconds, from --min-length/--max-length."""
    lo = int(getattr(cfg, "min_length", None) or MIN_CLIP_DURATION) if cfg else MIN_CLIP_DURATION
    hi = int(getattr(cfg, "max_length", None) or MAX_CLIP_DURATION) if cfg else MAX_CLIP_DURATION
    lo = max(5, min(lo, MAX_CLIP_DURATION))
    hi = max(lo + 5, min(hi, MAX_CLIP_DURATION))
    return lo, hi


def get_analysis_prompt(transkrip_lengkap: str, jumlah_clip: int, durasi_hook: int, cfg=None) -> str:
    """Centralized prompt for both Gemini and NVIDIA providers."""
    MIN_CLIP_DURATION, MAX_CLIP_DURATION = clip_length_range(cfg)
    # Build optional Hook V2 prompt section
    _hook_v2_prompt = ""
    if cfg and getattr(cfg, "hook_v2", False):
        _hook_v2_items = getattr(cfg, "hook_v2_items", 3)
        _hook_v2_style = getattr(cfg, "hook_v2_style", "controversial_fast_glitch")
        _hook_v2_prompt = f"""

HOOK V2 (MULTI-HOOK INTRO, REQUIRED):
- Besides the standard hook, also create "hook_v2" with {_hook_v2_items} short cuts (0.5-2 seconds) taken from the most striking, controversial or emotional moments inside the clip.
- Style: {_hook_v2_style}
- Each item must contain: start_time, end_time and text (a short on-screen text of 2-5 words).
- Order the items from strongest to weakest.
- Transitions between items (white flash / glitch) are added automatically by the system.
- Fill the "hook_v2" field as an object with:
  - "enabled": true
  - "items": an array of objects (start_time, end_time, text)
  - "transition": an object with "type" ("white_flash" or "glitch")
"""

    # Build optional Segment Trimming prompt section
    _segment_prompt = ""
    if cfg and not getattr(cfg, "no_segment_trim", False):
        _silence_hint = ""
        if cfg and getattr(cfg, "silence_trim", False):
            _silence_hint = "\n- AGGRESSIVELY remove silence and dead air. Do not keep pauses longer than 0.5 seconds."
        _segment_prompt = f"""

SEGMENT-BASED TRIMMING (KEEP SEGMENTS, REQUIRED):
- For each clip, check whether the middle contains weak, silent, rambling or filler parts.
- If so, split the clip into several "keep_segments" and keep only the best pieces.
- Each segment contains: start_time and end_time.
- Segments must be in chronological order and must not overlap.
- If the whole clip is already tight and engaging, use 1 segment that covers the whole clip.{_silence_hint}
- Fill the "keep_segments" field as an array of objects (start_time, end_time).
"""
    # Build the reaction-clip prompt section (short pre-saved reactions cut in)
    _reaction_moods = list(getattr(cfg, "reaction_moods", []) or []) if cfg else []
    if _reaction_moods:
        _max_reactions = int(getattr(cfg, "max_reactions", 2))
        _reaction_prompt = f"""

REACTION CLIPS ("reaction_inserts"):
- We have short reaction clips (about 1 second) that can be cut into the clip. Available moods: {", ".join(_reaction_moods)}.
- Pick at most {_max_reactions} moments per clip where a reaction makes the moment land harder: right AFTER a punchline, a fail, a shocking twist or a big play.
- "time" is the source-video second where the clip pauses for the reaction (use the same time scale as the transcript). Pick the end of the funny/shocking line, never the middle of a sentence.
- Keep reactions at least 4 seconds apart and not in the first or last 2 seconds of the clip.
- "mood" must be one of: {", ".join(_reaction_moods)}.
- If no moment truly deserves a reaction, use an empty array [].
"""
    else:
        _reaction_prompt = """

REACTION CLIPS:
- No reaction clips are available. Always set "reaction_inserts" to an empty array [].
"""
    return f"""
You are an Art Director, Video Editor and Metadata Strategist for short-form content on TikTok, Reels and YouTube Shorts.

Read the video transcript below. Transcript format:
[start_second - end_second] text

MAIN TASK:
- Find the {jumlah_clip} most interesting, strongest, most shareable moments with the highest chance of going viral, to turn into short clips.
- Sort the clips by viral_score from highest to lowest. "rank" is only the order number (1, 2, 3...).
- For each clip, produce: clip timing, hook, typography plan, b-roll plan, the reason it was chosen, cross-platform metadata and the target account classification.
- Every output must be specific to that clip, not to the full video in general.

CLIP SELECTION & VIRALITY RULES:
- Clip length must be {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} seconds.
- Choose parts with emotion, conflict, surprise, insight, strong opinion, a practical lesson or a clear punchline.
- Judge how viral each clip could be and give a "viral_score" (1-100):
  - 90-100: Very likely to go viral, strong emotion/conflict, a hook that really hits.
  - 80-89: Interesting, likely to perform well.
  - 70-79: Average, informative but may lack punch.
- Prefer moments that still work when watched without the context of the full video.
- Avoid clips that are too similar to each other.
- Do not pick clips that feel flat, rambling or without a clear payoff.

RETENTION & STRUCTURE RULES:
- The first 3 seconds must grab attention: a hook, conflict, curiosity, a sharp statement, emotion or an implied question.
- The ideal clip structure is:
  hook -> short context -> tension/insight -> payoff.
- Do not pick clips that only get interesting after a long time.
- If the start of a segment is slow, move start_time to a stronger sentence.
- Once the payoff is over, do not extend the clip without reason.
- Do not include intros, small talk, long pauses or transitions that add nothing.
- Prefer clips that make viewers want to:
  1. stop scrolling,
  2. watch to the end,
  3. comment,
  4. share,
  5. save,
  6. or feel "this is so me".

TIMING RULES:
- start_time must begin as close as possible to the first strong moment, not just the start of a topic.
- end_time must stop after the payoff, conclusion, punchline or main emotional beat is finished.
- Do not cut too early while a sentence is still unfinished.
- Do not keep the clip running long after the core message is done.
- The clip must make sense without watching what comes before or after it.
- If two strong moments are close together and support each other, they may be combined as long as the length stays {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} seconds.
- If two strong moments have different angles, keep them as separate clip candidates.

INTERNAL VIRAL_SCORE ASSESSMENT:
Score viral_score 1-100 from the components below. This is for internal judgement only; do NOT add new fields to the JSON.
- Hook strength: 1-20
- Emotional intensity: 1-20
- Shareability/comment potential: 1-20
- Standalone clarity: 1-20
- Payoff/retention: 1-20

Scoring guide:
- Hook strength: how strongly the first 3 seconds make people stop scrolling.
- Emotional intensity: how strong the emotion, conflict, frustration, humour, warmth, anger, awe or relatability is.
- Shareability/comment potential: how likely people are to comment, debate, tag a friend, share or save.
- Standalone clarity: how easy the clip is to understand without the full video.
- Payoff/retention: how clear the reward for watching to the end is, such as a punchline, insight, twist, conclusion or practical lesson.
- Do not pick clips with a viral_score below 70 unless the transcript has very few good moments.

TARGET ACCOUNT CLASSIFICATION (FOR EACH CLIP):
Decide the target account from the ANGLE of the clip. Do not judge only by topic. Judge by angle:
{_build_account_classification_prompt()}

SPECIAL CLASSIFICATION RULES:
1. A topic does not automatically decide the account; the angle does. (Revenue/brand talk -> Business. Review/lifestyle -> Life. Affiliate/content making -> Creator).
2. Finance does not automatically mean Business. (Revenue/business -> Business. Personal finance/saving -> Life. How to make finance content -> Creator).
3. Founder stories are split by angle. (Building the brand -> Business. Personal/family life -> Life. Faith/provision -> Muslim).

HOOK (REQUIRED):
- Take the single most punchy sentence that IS INSIDE the clip.
- The hook must feel strong and grab attention within the first ~{durasi_hook} seconds.
- Save it as hook_start_time and hook_end_time.
- The hook must make people want to keep watching, but no fake clickbait.
- Make sure the hook is natural and actually spoken in the transcript.
- If the best hook is not right at the start of the clip candidate, adjust start_time so the hook appears as early as possible.
- The hook must work as opening on-screen text that holds viewers in the first 3 seconds.

TYPOGRAPHY PLAN (KINETIC TYPOGRAPHY):
- Pick the 3-6 most meaningful, emotional or emphasis-worthy SINGLE words from each clip.
- For each word, set:
  1. 'kata_utama': that exact word, spelled exactly as in the transcript.
  2. 'scale_level': choose 1, 2 or 3.
     - 1 = normal/small
     - 2 = big/emphasis
     - 3 = huge/critical
  3. 'style': choose "utama" (main) or "khusus" (special).
  4. 'animasi': choose "bounce_pop" or "stagger_up".
- Do not pick long phrases. Single words only.
- Prefer words with the strongest emotion, meaning or visual retention.

B-ROLL (REQUIRED IF RELEVANT):
- Find at most 1-3 moments in the clip that would really benefit from inserted B-roll / stock footage.
- Each B-roll lasts 3-7 seconds.
- Give:
  - start_time
  - end_time
  - search_query
- search_query must be short, clear and in English.
- Do not place B-roll at the same second as the hook.
- Only add B-roll if it truly helps visualise what is being said.
- If there is no suitable moment, set broll_list to an empty array [].

VISUAL B-ROLL HOOK (FIRST 0-3 SECONDS):
- Give 2-5 opening B-roll ideas that are contrasting, funny, dramatic or curiosity-provoking before the original video starts.
- Include YouTube/TikTok search keywords for the editor.
- If there is a gesture that could serve as a visual hook, reference it too.
- This is stored in 'recommended_visual_broll_hook' and is only a reference in case the editor wants to find footage manually for the first 3 seconds.

BGM MOOD (BACKGROUND MUSIC):
- Analyse the emotion and topic of this clip.
- Pick ONE background music mood that fits best from this fixed list: [chill, epic, sad, upbeat, suspense].
- Make sure the mood matches the story. (Example: hard struggle = sad/epic, funny/relaxed = chill/upbeat).

SLOW CLOSING:
- end_time MUST include padding of +0.10 to +0.85 seconds after the last word so the ending breathes and is not cut off abruptly.

REASON FOR CHOOSING:
- Fill the 'alasan' field with a short explanation of why this clip deserves to be chosen.
- Focus on emotional value, hook strength, retention potential, shareability and payoff.
- Explain the main viral trigger of the clip.
- Explain why people are likely to watch to the end.
- Explain why the clip still works without the context of the full video.

METADATA LANGUAGE RULES:
- ALL metadata must be in natural, concise, easy-to-read English suited to short-form content. This includes the fields whose names end in "_indonesia" or "_id"; those names are kept only for compatibility.
- The audience is English-speaking viewers in Africa (Kenya, Nigeria, South Africa) and worldwide. Keep words simple and avoid slang that only one country would understand.
- Avoid stiff, literal wording.

CROSS-PLATFORM METADATA:
For each clip, produce the following metadata:

1. title_indonesia
- A short, natural English title (internal fallback, kept for compatibility).
- Maximum 100 characters.

2. title_inggris
- A strong, sharp, readable English title.
- This is the main title used on the platforms.
- Maximum 100 characters.
- Focus on 1 main idea.
- Specific to the clip, not the full video in general.
- No cheap clickbait.
- No excessive capital letters.
- Avoid excessive punctuation such as !!! ??? ...
- Not too generic.

3. hastag
- Only 2 to 3 hashtags in one string.
- All hashtags MUST be in English.
- Separate them with spaces.
- They must relate directly to the clip's topic.
- No duplicates.
- Avoid overly generic hashtags such as #fyp #viral #trending unless truly relevant.
- Use a format such as: #mindset #career #productivity

4. description_hook
- Exactly 1 sentence, in English.
- This is the opening sentence of the description.
- Short, strong and curiosity-provoking.
- No fake clickbait.

5. description_context
- Exactly 1 sentence, in English.
- Briefly explains the main context of the clip.
- Must relate to what is said in the clip.

6. keyword_tags
- 5 to 8 short keywords, in English.
- Not hashtags.
- A list of short phrases relevant to the clip.
- Avoid keyword spam.
- Prefer keywords people actually search for.
- Mainly used for YouTube metadata.

7. tiktok_title_id
- A longer English title that explains the video more than title_inggris.
- No strict 100-character limit, but keep it concise, clear and readable.
- Specific to the clip, not the full video in general.
- No cheap clickbait.

8. tiktok_caption_id
- 1 to 2 sentences in English.
- May be a little longer than tiktok_caption if that helps explain the clip.
- Natural, light and easy to read.
- True to the clip.
- Do not just copy the title.
- Not too formal.

9. tiktok_caption
- 1 to 2 short sentences in English.
- Natural, light and conversational.
- True to the clip.
- Do not just copy the title.
- Not too formal.
- Try to stay under 140 characters.

METADATA QUALITY RULES:
- All metadata must match the clip, not the full video in general.
- Do not promise anything the clip does not cover.
- No fake hyperbole such as "100% guaranteed" or "get rich for sure" unless clearly stated.
- If the original speech has numbers, strong phrases or sharp statements, prioritise them as inspiration for the title/caption.
- Title, descriptions and caption should complement each other, not repeat the same sentence.

OUTPUT RULES:
- Output MUST be a valid JSON array.
- Do not give any explanation outside the JSON.
- Every field must be filled.
- When unsure, prioritise accuracy to the clip over excessive creativity.
{_hook_v2_prompt}{_segment_prompt}{_reaction_prompt}

REQUIRED JSON STRUCTURE (follow these field names exactly):
[
  {{
    "rank": 1,
    "viral_score": 95,
    "start_time": 30.5,
    "end_time": 90.0,
    "hook_start_time": 30.5,
    "hook_end_time": 35.0,
    "bgm_mood": "mood_here",
    "typography_plan": [{{ "kata_utama": "...", "scale_level": 2, "style": "utama", "animasi": "bounce_pop" }}],
    "broll_list": [{{ "start_time": 40.0, "end_time": 45.0, "search_query": "..." }}],
    "recommended_visual_broll_hook": [
      {{ "broll_idea": "...", "search_keyword": "...", "why_it_works": "..." }}
    ],
    "hook_v2": {{
      "enabled": true,
      "items": [{{ "start_time": 31.0, "end_time": 32.5, "text": "KEY WORDS" }}],
      "transition": {{ "type": "white_flash" }}
    }},
    "keep_segments": [
      {{ "start_time": 30.5, "end_time": 55.0 }},
      {{ "start_time": 58.0, "end_time": 90.0 }}
    ],
    "reaction_inserts": [{{ "time": 52.3, "mood": "laugh" }}],
    "title_indonesia": "...",
    "title_inggris": "...",
    "hastag": "#hashtag1 #hashtag2",
    "description_hook": "...",
    "description_context": "...",
    "keyword_tags": ["tag1", "tag2"],
    "tiktok_title_id": "...",
    "tiktok_caption_id": "...",
    "tiktok_caption": "...",
    "alasan": "...",
    "klasifikasi_akun": {{
      "tipe_akun": "Creator",
      "akun_tujuan": "Creator.Mereska",
      "confidence": 87,
      "angle_utama": "Making money from digital content in the beauty niche",
      "alasan": "...",
      "kata_kunci_pendukung": ["affiliate", "monetization"],
      "bio_akun": "...",
      "alternatif_akun": {{
        "tipe_akun": "Life",
        "akun_tujuan": "Life.Mereska",
        "alasan": "..."
      }}
    }}
  }}
]

Transcript:
{transkrip_lengkap}
"""


def analyze_with_nvidia(transkrip_lengkap: str, cfg) -> list[dict]:
    """Legacy facade for NVIDIA NIM analysis."""
    from clipping.providers.nvidia import NvidiaNimProvider
    from clipping.providers.schema import build_clips_schema

    prompt = get_analysis_prompt(transkrip_lengkap, cfg.jumlah_clip, cfg.durasi_hook, cfg=cfg)
    return NvidiaNimProvider.from_cfg(cfg).generate_clips(prompt, build_clips_schema(TARGET_ACCOUNTS))


def analyze_with_vllm(transkrip_lengkap: str, cfg) -> list[dict]:
    """Analyze using the configured self-hosted server, with no cloud fallback."""
    from clipping.providers.schema import build_clips_schema
    from clipping.providers.vllm import VllmProvider

    prompt = get_analysis_prompt(transkrip_lengkap, cfg.jumlah_clip, cfg.durasi_hook, cfg=cfg)
    with VllmProvider.from_cfg(cfg) as provider:
        return provider.generate_clips(
            prompt,
            build_clips_schema(TARGET_ACCOUNTS),
            transcript=transkrip_lengkap,
            requested_clips=cfg.jumlah_clip,
            min_duration=clip_length_range(cfg)[0],
            max_duration=clip_length_range(cfg)[1],
        )


def analyze_with_ai(transkrip_lengkap: str, cfg) -> list[dict]:
    """Dispatch analysis; retain legacy NVIDIA-to-Gemini fallback only."""
    cfg.ai_run_meta = None
    provider = getattr(cfg, "ai_provider", "gemini")
    if provider == "vllm":
        return analyze_with_vllm(transkrip_lengkap, cfg)

    if provider == "nvidia":
        if not cfg.api_key_nvidia:
            print("⚠️ NVIDIA_API_KEY not found. Falling back to Gemini...")
        else:
            try:
                return analyze_with_nvidia(transkrip_lengkap, cfg)
            except Exception as e:
                print(f"⚠️ NVIDIA API failed: {e}. Falling back to Gemini...")

    return analyze_with_gemini(transkrip_lengkap, cfg)


def analyze_with_gemini(transkrip_lengkap: str, cfg) -> list[dict]:
    """Legacy Gemini facade; live retry and prompt patch points remain here."""
    from clipping.providers.gemini import GeminiProvider
    from clipping.providers.schema import build_gemini_schema

    prompt = get_analysis_prompt(transkrip_lengkap, cfg.jumlah_clip, cfg.durasi_hook, cfg=cfg)
    provider = GeminiProvider(
        cfg,
        generate_json=_generate_json_with_retry,
        timeout_ms=REQUEST_TIMEOUT_MS,
    )
    return provider.generate_clips(prompt, build_gemini_schema(TARGET_ACCOUNTS))
