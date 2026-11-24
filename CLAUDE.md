@AGENTS.md

## Helping with clips, edits and posting

When the user asks you to find clips, make or frame edits, export, or post, use the
`clipping` MCP tools (from `.mcp.json`; code in `clipping/claude_tools.py`). Start with
`status` to see what is installed and where the folders are.

- **Finding clips in talk videos** (podcasts, interviews, pundits): `clipsai_find_clips`,
  then `make_edit` with the moments the user likes. For GTA gameplay, Clipping Studio
  (`python main.py ...`) finds better moments, because ClipsAI only reads the speech.
- **Framing on the speaker**: `clipsai_follow_speaker` on an edit folder (needs `HF_TOKEN`).
- **Adding reactions** (the clips in `assets/reactions/<mood>/`): `watch_edit` gives numbered
  contact sheets of the cut; open each sheet with the Read tool and look at it, together with
  the speech and script times it returns. Then `list_reactions` and Read its picture to see
  what each clip shows. Pick the few moments that earn a reaction (a fail, a surprise, a
  big play, a punchline): usually 1 to 3 in a Short, at least 3 s apart, not in the first or
  last second, and a corner box rather than a cut-away while the narrator talks. Match the
  mood to the moment (laugh for a fail, shock for a surprise, hype for a big play, and so on).
  `add_reactions` puts them in and reports warnings; tell the user what you added and why,
  in edit times. `remove_reactions` takes them out. The Clip Editor shows the change when its
  window is clicked again.
- **Exporting**: `export_edits` (today's edits with `today=true`). Each export joins the
  upload list in `Ready to post/upload_manifest.json`.
- **Posting**: `postiz_channels`, then `postiz_send`. It is a dry run unless `post=true`.
  Show the user the dry run (what goes where, and when) and post only after they say so.
  Use Africa/Nairobi times. Kids videos are marked made for kids on YouTube.
  YouTube and Facebook can also be posted by `run_upload.py` / `run_fb_upload.py`, so
  don't send the same video to YouTube both ways.
- If ClipsAI or Postiz isn't set up, say what to run: `bash scripts/connect_claude.sh`,
  `pip install -r requirements-clipsai.txt`, or `POSTIZ_API_KEY` in `.env`.

The same jobs work from the terminal:
`python -m clipping.integrations.clipsai_tool find|crops|check ...` and
`python -m clipping.integrations.postiz channels|send ...`.
