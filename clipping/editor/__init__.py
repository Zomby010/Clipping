"""
clipping.editor — Clip Editor, the second desktop app.

Clipping Studio finds viral moments with AI. Clip Editor lets a person pick
their own moments from full videos, join them in any order, frame them for
9:16 and export a postable video. Later phases add captions, a Claude-written
storyline, voice-overs, music, reactions and per-platform export.

Modules:
  project.py   the edit project (one JSON file per edit), undo/redo history
  timeline.py  pure time maths: range parser, edit time <-> source time, lengths
  media.py     ffprobe/ffmpeg helpers (video info, still frames)
  framing.py   9:16 crop maths and the "follow the action" auto-crop
  render.py    ffmpeg commands for the quick preview and the final export
  preview.py   video playback inside the window (mpv) with a still-frame fallback
  gui/         the Tkinter window
"""
