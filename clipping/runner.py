"""
clipping.runner — Pipeline Orchestrator

Maps to Cell 4 (Execute) of the notebook.
Orchestrates the full clip generation pipeline.
"""

import glob
import json
import os

from . import diarization as diarization_mod
from . import engine, metadata, sources, studio, hook_manager, voiceover


def _download(cfg, url, path):
    engine.download_video(
        url,
        path,
        getattr(cfg, "use_dlp_subs", False),
        getattr(cfg, "download_source_height", "max"),
        source_platform=getattr(cfg, "source_platform", "youtube"),
        yt_cookies=getattr(cfg, "yt_cookies", None),
    )


def _transcribe(cfg, video, whisper_model_instance):
    """The video's own subtitles if you asked for them and they exist, else Whisper."""
    text, segments = "", []
    # Find any json3 file (the language can be .id.json3 or .en.json3)
    json3_files = glob.glob(video.replace(".mp4", ".*.json3"))
    # Only run YouTube JSON3 subtitle search for YouTube sources
    if (getattr(cfg, "source_platform", "youtube") == "youtube"
            and getattr(cfg, "use_dlp_subs", False) and json3_files):
        text, segments = engine.parse_youtube_json3_subs(
            json3_files[0], max_words_per_subtitle=cfg.max_kata_per_subtitle
        )
        if text and segments:
            print(f"✅ Read the YouTube subtitles ({os.path.basename(json3_files[0])}), skipping Whisper.")
    if not text or not segments:
        text, segments = engine.transcribe_video(
            video,
            max_words_per_subtitle=cfg.max_kata_per_subtitle,
            model_size=cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
            whisper_model_instance=whisper_model_instance,
        )
    return text, segments


def _prepare_joined(cfg, urls, whisper_model_instance):
    """Download every link, join them into one video and one transcript."""
    paths = []
    for n, url in enumerate(urls, 1):
        print(f"\n📹 Video {n} of {len(urls)}: {url}")
        path = sources.part_path(cfg.outputs_dir, url)
        sources.claim(path, [url], getattr(cfg, "download_source_height", "max"))
        _download(cfg, url, path)
        paths.append(path)

    height = getattr(cfg, "download_source_height", "max")
    reused = sources.claim(cfg.file_video_asli, urls, height)
    saved = os.path.splitext(cfg.file_video_asli)[0] + "_offsets.json"
    if reused and os.path.exists(cfg.file_video_asli) and os.path.exists(saved):
        with open(saved, "r", encoding="utf-8") as f:
            offsets = json.load(f)
    else:
        offsets = sources.join_videos(paths, cfg.file_video_asli)
        with open(saved, "w", encoding="utf-8") as f:
            json.dump(offsets, f)
    total = sources.probe(cfg.file_video_asli)["duration"]

    text, segments, creators = "", [], []
    for n, (path, offset) in enumerate(zip(paths, offsets), 1):
        part_text, part_segments = sources.shift_transcript(
            *_transcribe(cfg, path, whisper_model_instance), offset)
        text += part_text if part_text.endswith("\n") else part_text + "\n"
        segments += part_segments
        creators += engine.load_source_creators(path)
        title = ""
        try:
            with open(os.path.splitext(path)[0] + "_source.json", encoding="utf-8") as f:
                title = json.load(f).get("title", "")
        except (OSError, ValueError):
            pass
        end = offsets[n] if n < len(offsets) else total
        cfg.source_parts.append({"start": offset, "end": end, "title": title})
    cfg.source_creators = sorted(set(creators))

    # "V2 11:40" in your storyline now points into the joined video.
    cfg.story_brief = sources.to_joined_times(getattr(cfg, "story_brief", ""), offsets)
    cfg.story_angles = sources.to_joined_times(
        getattr(cfg, "story_angles", "").replace("|", "\n"), offsets)
    return text, segments


def run_pipeline(cfg, whisper_model_instance=None) -> list[dict]:
    """
    Run the full clipping pipeline:
      1. Download YouTube video
      2. Transcribe with Whisper
      3. Analyse with Gemini AI
      4. Normalize metadata
      5. Prepare glitch transition
      6. Render each clip
      7. Save render_manifest.json

    Parameters
    ----------
    cfg : SimpleNamespace
        Configuration object from ``config.build_config()``.
    whisper_model_instance : WhisperModel or None
        Optional pre-loaded WhisperModel to reuse across batch runs.

    Returns
    -------
    list[dict]
        Render manifest (one dict per clip).
    """

    from clipping.providers.validation import validate_analysis_config
    from clipping.providers.metadata import save_run_metadata

    validate_analysis_config(cfg, probe=getattr(cfg, "ai_provider", "gemini") == "vllm")

    joined = list(getattr(cfg, "join_urls", None) or [])
    cfg.source_parts = []
    if joined:
        transkrip_lengkap, data_segmen = _prepare_joined(cfg, joined, whisper_model_instance)
    else:
        # Step 1 — Download (a saved video is only reused for this same link and quality)
        sources.claim(cfg.file_video_asli, [cfg.url_youtube], getattr(cfg, "download_source_height", "max"))
        _download(cfg, cfg.url_youtube, cfg.file_video_asli)

        # Step 2 — Transcribe
        transkrip_lengkap, data_segmen = _transcribe(cfg, cfg.file_video_asli, whisper_model_instance)

        # Who made the source video (saved at download), so the AI never claims it as ours.
        cfg.source_creators = engine.load_source_creators(cfg.file_video_asli)

    # Step 3 — Gemini AI analysis
    gemini_output_path = os.path.join(cfg.outputs_dir, "gemini_response.json")
    
    # A saved AI answer is only reused for the links it was made from.
    links_now = [sources.link_id(u) for u in joined or [cfg.url_youtube]]
    answer_key = os.path.join(cfg.outputs_dir, "ai_answer_links.json")
    reuse = getattr(cfg, "load_gemini_json", False) and os.path.exists(gemini_output_path)
    if reuse and os.path.exists(answer_key):
        with open(answer_key, "r", encoding="utf-8") as f:
            if json.load(f) != links_now:
                print("\n⚠️ The saved AI answer is for a different link, so the AI will look again.")
                reuse = False

    if reuse:
        print(f"\n🔄 [3/3] Loading the saved AI answer ({cfg.ai_provider}) from: {gemini_output_path}")
        with open(gemini_output_path, "r", encoding="utf-8") as f:
            hasil_json = json.load(f)
        save_run_metadata(cfg, cached=True)
    else:
        try:
            hasil_json = engine.analyze_with_ai(transkrip_lengkap, cfg)
        except Exception:
            if getattr(cfg, "ai_run_meta", None):
                try:
                    save_run_metadata(cfg)
                except (OSError, TypeError, ValueError):
                    # A diagnostic write must not hide the inference failure.
                    print("[AI] Could not save the failure details.")
            raise
        
        # Save raw gemini json for future loading/reproduction
        with open(gemini_output_path, "w", encoding="utf-8") as f:
            json.dump(hasil_json, f, indent=4, ensure_ascii=False)
        with open(answer_key, "w", encoding="utf-8") as f:
            json.dump(links_now, f)
        print(f"💾 AI answer saved to: {gemini_output_path}")
        save_run_metadata(cfg)

    # Never let the source creator's name end up in our tags/hashtags.
    removed = engine.scrub_source_names(hasil_json, getattr(cfg, "source_creators", []) or [])
    if removed:
        print(f"🧹 Removed {removed} tag(s)/hashtag(s) that named the source creator.")

    # Step 4 — Metadata normalisation
    hasil_json = metadata.normalize_and_validate(hasil_json)
    if getattr(cfg, "storyline_cuts", False):
        sources.fit_bounds(hasil_json)
    metadata.print_preview(hasil_json)

    metadata_path = os.path.join(cfg.outputs_dir, "metadata_preview.json")
    metadata.save_metadata_preview(hasil_json, path=metadata_path)

    # Step 5 — Diarization (split-screen / camera-switch)
    diarization_data = None
    if (
        (getattr(cfg, "use_split_screen", False) and cfg.split_trigger == "diarization")
        or getattr(cfg, "use_camera_switch", False)
    ) and studio._is_vertical_ratio(cfg.pilihan_rasio):
        try:
            mode_label = (
                "Split-Screen"
                if getattr(cfg, "use_split_screen", False)
                else "Camera-Switch"
            )
            print(f"\n🎙️ [{mode_label}] Working out who is speaking (diarization)...")
            audio_path = cfg.file_video_asli.replace(".mp4", "_audio.wav")
            diarization_mod.extract_audio(cfg.file_video_asli, audio_path)
            num_speakers_arg = getattr(cfg, "diarization_num_speakers", 2)
            min_spk = None
            max_spk = None

            if str(num_speakers_arg).lower() == "auto":
                max_faces = studio.estimate_speaker_count_from_video(
                    cfg.file_video_asli, cfg
                )
                num_speakers_arg = "auto"
                min_spk = max(1, max_faces)
                max_spk = min_spk + 2
                print(f"   ℹ️ Pyannote: {min_spk} to {max_spk} speakers.")

            diarization_data = diarization_mod.run_diarization(
                audio_path,
                hf_token=cfg.hf_token,
                num_speakers=num_speakers_arg,
                min_speakers=min_spk,
                max_speakers=max_spk,
            )
            # Clean up temp audio
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            print(f"⚠️ Diarization failed: {e}")
            print("   Falling back to the normal render (no split-screen).")
            diarization_data = None

    # Step 6 — Video encoder & glitch
    os.environ["OSC_VIDEO_SCALE_ALGO"] = str(
        getattr(cfg, "video_scale_algo", "lanczos")
    )
    
    # Get target dimensions for auto-bitrate calculation
    import cv2
    cap_e = cv2.VideoCapture(cfg.file_video_asli)
    src_h_e = int(cap_e.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_e.release()
    
    target_w_e, target_h_e = studio._get_render_dims(cfg, cfg.pilihan_rasio, source_h=src_h_e)
    video_encoder = studio.detect_video_encoder(cfg, target_h=target_h_e)

    file_glitch_ts = None
    if cfg.use_hook_glitch:
        print("⚙️ Preparing the glitch transition video...")
        
        # Get source dimensions for proper glitch scaling
        import cv2
        cap_g = cv2.VideoCapture(cfg.file_video_asli)
        source_h_g = int(cap_g.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_g.release()

        file_glitch_ts = studio.siapkan_glitch_video(
            cfg.pilihan_rasio, cfg, video_encoder, source_h=source_h_g
        )

    # Step 6 — Render each clip
    render_manifest: list[dict] = []

    custom_hook_path = None
    if getattr(cfg, "hook_source", None):
        print("\n🎣 Downloading the custom hook clip...")
        custom_hook_path = hook_manager.download_custom_hook(cfg)

    # Step 5.5 — Generate Voice-Over (if enabled)
    story_data = None
    if getattr(cfg, "voiceover", False):
        if getattr(cfg, "story_mode", "series") in ("series", "standalone"):
            from . import story as story_mod
            print(f"\n📖 Writing our own storyline from the full captions ({cfg.story_mode})...")
            story_data = story_mod.write_stories(cfg, hasil_json, transkrip_lengkap, data_segmen)
            if story_data:
                story_mod.save(story_data, os.path.join(cfg.outputs_dir, "story.json"))
                for klip in hasil_json:
                    ep = story_data["episodes"].get(int(klip["rank"]))
                    if ep:
                        klip["story_episode"] = {
                            **ep, "series_title": story_data.get("series_title", ""),
                            "mode": story_data.get("mode"),
                        }
            else:
                print("   ↩️ [Story] No storyline this time; using the short commentary instead.")
        print(f"\n🎙️ Making voice-overs for {len(hasil_json)} clip(s)...")
        for klip in hasil_json:
            try:
                # 1. Generate commentary script from snippet
                start = float(klip["start_time"])
                end = float(klip["end_time"])
                # Extract transcript snippet for this time range
                snippet_lines = []
                for seg in data_segmen:
                    if float(seg["end"]) > start and float(seg["start"]) < end:
                        # Support both Whisper format (has 'text') and YouTube JSON3 (only 'words')
                        seg_text = seg.get("text") or " ".join(w["word"] for w in seg.get("words", []))
                        if seg_text:
                            snippet_lines.append(seg_text)
                snippet_text = " ".join(snippet_lines)

                if klip.get("story_episode"):
                    script = klip["story_episode"]["script"]
                else:
                    script = voiceover.generate_commentary_script(
                        snippet_text,
                        cfg,
                        style=cfg.voiceover_style,
                        language=cfg.voiceover_lang,
                        length=cfg.voiceover_length,
                    )

                if script:
                    # 2. Synthesize TTS
                    audio_path, vo_segments = voiceover.synthesize_voice(
                        script,
                        cfg.voiceover_voice,
                        cfg.outputs_dir,
                        str(klip["rank"])
                    )
                    
                    if os.path.exists(audio_path):
                        klip["voiceover"] = {
                            "script": script,
                            "audio_path": audio_path,
                            "segments": vo_segments,
                            "voice": cfg.voiceover_voice
                        }

            except Exception as e:
                print(f"   ⚠️ Could not make the voice-over for Rank {klip['rank']}: {e}")

    for klip in sorted(hasil_json, key=lambda x: x["rank"]):
        
        if custom_hook_path:
            klip["custom_hook_info"] = {"file_path": custom_hook_path}

        hasil_render = studio.proses_klip(
            klip["rank"],
            klip,
            cfg.pilihan_rasio,
            file_glitch_ts,
            data_segmen,
            cfg,
            video_encoder,
            diarization_data=diarization_data,
        )
        if hasil_render:
            if klip.get("story_episode"):
                hasil_render["story_episode"] = {
                    k: v for k, v in klip["story_episode"].items() if k != "script"
                }
            render_manifest.append(hasil_render)

    # Step 7 — Inject source metadata for attribution & safety tracking
    for row in render_manifest:
        # Attach source URL so metadata.py can auto-add source credit
        if not row.get("source_url"):
            row["source_url"] = " ".join(joined) or getattr(cfg, "url_youtube", None)

    # Step 7.5 — Tidy the finished files into "My Clips/<date - title>/"
    if getattr(cfg, "organize_library", True):
        from . import library
        try:
            run_dir = library.organize_run(render_manifest, cfg, story=story_data)
            if run_dir:
                print(f"\n📁 Your finished clips are in: {run_dir}")
        except Exception as e:
            print(
                f"\n⚠️ Could not sort the clips into the 'My Clips' folder ({e}). "
                f"They are still in {cfg.outputs_dir}."
            )
            for row in render_manifest:
                row.pop("caption_lines", None)
    else:
        for row in render_manifest:
            row.pop("caption_lines", None)

    # Step 8 — Save manifest
    manifest_path = os.path.join(cfg.outputs_dir, "render_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(render_manifest, f, ensure_ascii=False, indent=2)

    print(
        f"\n💾 Render manifest saved to {manifest_path} ({len(render_manifest)} item)"
    )


    return render_manifest

