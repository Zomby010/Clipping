"""
clipping.editor.gui.story_panels — The Story and Voice tabs (phase 3).

    Story   Ask Claude to watch (scene log, ★ starred moments), mode and angle,
            characters, the script (edit / move / rewrite / delete each line),
            series memory
    Voice   a voice for each line (free edge-tts voices), make voices, record
            a line with the mic, play a line, fix lines that are too long,
            keep the game sounds but remove the talking

Every Claude call and every voice job runs in the background with progress and
Stop (``run_job``), so the window never freezes. All the logic lives in
claude_editor.py, script.py and voice.py; this file only wires buttons to them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from clipping.editor import captions as C
from clipping.editor import claude_editor as CE
from clipping.editor import project as P
from clipping.editor import script as S
from clipping.editor import timeline
from clipping.editor import voice as V

NO_CHARACTER = "(narrator)"


class StoryPanelsMixin:
    """Mixed into EditorApp. Uses its project, commit(), run_job(), status() and tabs."""

    # ================================================================= Story tab
    def _build_story_page(self, t):
        self._heading(t, "Story", "Claude watches, you pick the mode and angle, Claude writes lines timed to your cut.")
        ttk.Button(t, text="Ask Claude to watch", style="Accent.TButton", command=self.ask_to_watch).pack(anchor="w")
        self.watch_info = tk.StringVar(value="Claude reads the speech and looks at pictures of the video, "
                                             "then lists what happens and when.")
        ttk.Label(t, textvariable=self.watch_info, style="Help.TLabel", wraplength=270, justify="left").pack(anchor="w", pady=(2, 4))
        cols = ("star", "time", "what", "score")
        self.scene_tree = ttk.Treeview(t, columns=cols, show="headings", height=5, selectmode="browse")
        for c, w, txt in (("star", 24, "★"), ("time", 70, "When"), ("what", 120, "What happens"), ("score", 36, "Score")):
            self.scene_tree.heading(c, text=txt)
            self.scene_tree.column(c, width=w, stretch=(c == "what"), anchor="w")
        self.scene_tree.pack(fill="x")
        self.scene_tree.bind("<<TreeviewSelect>>", lambda e: self._scene_selected())
        self.scene_why = tk.StringVar()
        ttk.Label(t, textvariable=self.scene_why, style="Help.TLabel", wraplength=270, justify="left").pack(anchor="w", pady=2)
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Add to cut", command=self.add_scene_to_cut).pack(side="left")
        ttk.Button(row, text="Add all ★", command=self.add_starred_to_cut).pack(side="left", padx=4)

        ttk.Separator(t).pack(fill="x", pady=8)
        ttk.Label(t, text="Mode", style="Card.TLabel", font=(self.base_font[0], self.base_font[1], "bold")).pack(anchor="w")
        self.mode_var = tk.StringVar(value="explain")
        for key, (name, text) in CE.MODE_HELP.items():
            ttk.Radiobutton(t, text=name, value=key, variable=self.mode_var, style="Card.TRadiobutton",
                            command=self._mode_changed).pack(anchor="w")
            ttk.Label(t, text=text, style="Help.TLabel", wraplength=250, justify="left").pack(anchor="w", padx=(20, 0))
        ttk.Label(t, text="Angle (one sentence of direction)", style="Card.TLabel").pack(anchor="w", pady=(8, 0))
        self.angle_var = tk.StringVar()
        ent = ttk.Entry(t, textvariable=self.angle_var)
        ent.pack(fill="x")
        ent.bind("<FocusOut>", lambda e: self._angle_changed())
        ent.bind("<Return>", lambda e: self._angle_changed())
        ttk.Button(t, text="Suggest 3 angles", command=self.suggest_angles).pack(anchor="w", pady=(4, 0))
        self.angle_buttons = ttk.Frame(t, style="Card.TFrame")
        self.angle_buttons.pack(fill="x")

        self.chars_box = ttk.Frame(t, style="Card.TFrame")
        self.chars_box.pack(fill="x")
        ttk.Label(self.chars_box, text="Characters (New story)", style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1], "bold")).pack(anchor="w", pady=(8, 0))
        self.char_tree = ttk.Treeview(self.chars_box, columns=("name", "voice"), show="headings", height=3, selectmode="browse")
        self.char_tree.heading("name", text="Name")
        self.char_tree.heading("voice", text="Voice")
        self.char_tree.column("name", width=90)
        self.char_tree.column("voice", width=150)
        self.char_tree.pack(fill="x")
        self.char_tree.bind("<<TreeviewSelect>>", lambda e: self._char_selected())
        self.char_name, self.char_about, self.char_voice = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Entry(self.chars_box, textvariable=self.char_name).pack(fill="x", pady=(3, 0))
        ttk.Label(self.chars_box, text="↑ name    ↓ who they are", style="Help.TLabel").pack(anchor="w")
        ttk.Entry(self.chars_box, textvariable=self.char_about).pack(fill="x")
        ttk.Combobox(self.chars_box, textvariable=self.char_voice, state="readonly",
                     values=[V.label(v[0]) for v in V.VOICES]).pack(fill="x", pady=2)
        crow = ttk.Frame(self.chars_box, style="Card.TFrame")
        crow.pack(anchor="w")
        ttk.Button(crow, text="Add / update", command=self.save_character).pack(side="left")
        ttk.Button(crow, text="Remove", command=self.remove_character).pack(side="left", padx=4)

        ttk.Separator(t).pack(fill="x", pady=8)
        ttk.Label(t, text="Script", style="Card.TLabel", font=(self.base_font[0], self.base_font[1], "bold")).pack(anchor="w")
        srow = ttk.Frame(t, style="Card.TFrame")
        srow.pack(anchor="w", pady=(2, 4))
        ttk.Button(srow, text="Write the script", style="Accent.TButton", command=lambda: self.write_script(False)).pack(side="left")
        ttk.Button(srow, text="Write again", command=lambda: self.write_script(True)).pack(side="left", padx=4)
        self.script_tree = ttk.Treeview(t, columns=("when", "who", "text"), show="headings", height=7, selectmode="browse")
        for c, w, txt in (("when", 48, "At"), ("who", 56, "Who"), ("text", 150, "Line")):
            self.script_tree.heading(c, text=txt)
            self.script_tree.column(c, width=w, stretch=(c == "text"), anchor="w")
        self.script_tree.tag_configure("bad", foreground="#c92a2a")
        self.script_tree.pack(fill="x")
        self.script_tree.bind("<<TreeviewSelect>>", lambda e: self._line_selected())
        self.line_text = tk.StringVar()
        le = ttk.Entry(t, textvariable=self.line_text)
        le.pack(fill="x", pady=(4, 0))
        le.bind("<Return>", lambda e: self.save_line_text())
        le.bind("<FocusOut>", lambda e: self.save_line_text())
        self.line_warn = tk.StringVar()
        ttk.Label(t, textvariable=self.line_warn, wraplength=270, justify="left",
                  foreground="#c92a2a", background="#ffffff").pack(anchor="w")
        r1 = ttk.Frame(t, style="Card.TFrame")
        r1.pack(anchor="w", pady=2)
        ttk.Button(r1, text="◀ 0.5 s", width=7, command=lambda: self.nudge_line(-0.5)).pack(side="left")
        ttk.Button(r1, text="0.5 s ▶", width=7, command=lambda: self.nudge_line(0.5)).pack(side="left", padx=3)
        ttk.Button(r1, text="Delete line", command=self.delete_line).pack(side="left")
        ttk.Label(t, text="Rewrite this line (optional note, e.g. funnier, shorter)", style="Help.TLabel").pack(anchor="w")
        self.rewrite_note = tk.StringVar()
        ttk.Entry(t, textvariable=self.rewrite_note).pack(fill="x")
        ttk.Button(t, text="Rewrite", command=self.rewrite_line).pack(anchor="w", pady=2)

        ttk.Separator(t).pack(fill="x", pady=8)
        ttk.Label(t, text="Series memory (story so far, characters, running jokes)", style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1], "bold"), wraplength=270).pack(anchor="w")
        self.series_box = tk.Text(t, height=4, width=30, wrap="word", font=self.base_font)
        self.series_box.pack(fill="x")
        self.series_box.bind("<FocusOut>", lambda e: self._save_series())
        ttk.Button(t, text="Add to series memory", command=self.add_to_series).pack(anchor="w", pady=(3, 0))
        self.series_info = tk.StringVar()
        ttk.Label(t, textvariable=self.series_info, style="Help.TLabel", wraplength=270, justify="left").pack(anchor="w")

    # ================================================================= Voice tab
    def _build_voice_page(self, t):
        self._heading(t, "Voice", "Free voices for each line. Needs the internet to make them; everything else works without.")
        ttk.Label(t, text="Voice for this channel", style="Card.TLabel").pack(anchor="w")
        self.voice_var = tk.StringVar()
        cb = ttk.Combobox(t, textvariable=self.voice_var, state="readonly", values=[v[1] for v in V.VOICES])
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", lambda e: self._voice_changed())
        ttk.Button(t, text="Play sample", command=self.play_sample).pack(anchor="w", pady=3)
        self.speed_var, self.pitch_var = tk.IntVar(value=0), tk.IntVar(value=0)
        for label, var, (lo, hi) in (("Speed (%)", self.speed_var, V.SPEED_LIMITS), ("Pitch (Hz)", self.pitch_var, V.PITCH_LIMITS)):
            ttk.Label(t, text=label, style="Card.TLabel").pack(anchor="w")
            s = ttk.Scale(t, from_=lo, to=hi, variable=var, orient="horizontal")
            s.pack(fill="x")
            s.bind("<ButtonRelease-1>", lambda e: self._voice_changed())
        self.voice_info = tk.StringVar()
        ttk.Label(t, textvariable=self.voice_info, style="Help.TLabel", wraplength=270, justify="left").pack(anchor="w")
        ttk.Button(t, text="Make voices", style="Accent.TButton", command=self.make_voices).pack(anchor="w", pady=(6, 2))
        self.voice_tree = ttk.Treeview(t, columns=("n", "voice", "state"), show="headings", height=6, selectmode="browse")
        for c, w, txt in (("n", 26, "#"), ("voice", 120, "Voice"), ("state", 100, "State")):
            self.voice_tree.heading(c, text=txt)
            self.voice_tree.column(c, width=w, stretch=(c == "voice"), anchor="w")
        self.voice_tree.tag_configure("bad", foreground="#c92a2a")
        self.voice_tree.pack(fill="x")
        self.voice_tree.bind("<<TreeviewSelect>>", lambda e: self._voice_line_selected())
        self.voice_line_info = tk.StringVar()
        ttk.Label(t, textvariable=self.voice_line_info, wraplength=270, justify="left", foreground="#c92a2a",
                  background="#ffffff").pack(anchor="w")
        r1 = ttk.Frame(t, style="Card.TFrame")
        r1.pack(anchor="w", pady=2)
        ttk.Button(r1, text="Play line", command=self.play_line).pack(side="left")
        self.rec_btn = ttk.Button(r1, text="Record with mic", command=self.toggle_recording)
        self.rec_btn.pack(side="left", padx=4)
        r2 = ttk.Frame(t, style="Card.TFrame")
        r2.pack(anchor="w", pady=2)
        ttk.Button(r2, text="Speed up this line", command=self.speed_up_line).pack(side="left")
        ttk.Button(r2, text="Shorten with Claude", command=self.shorten_line).pack(side="left", padx=4)
        ttk.Separator(t).pack(fill="x", pady=8)
        ttk.Label(t, text="Original sound", style="Card.TLabel", font=(self.base_font[0], self.base_font[1], "bold")).pack(anchor="w")
        ttk.Label(t, style="Help.TLabel", wraplength=270, justify="left",
                  text="Explain lowers it under the voice, React keeps it, New story mutes it. "
                       "Set each moment on the Framing tab.").pack(anchor="w")
        self.talk_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(t, text="Keep game sounds, remove talking", variable=self.talk_var,
                        command=self._talk_changed).pack(anchor="w", pady=3)

    # ======================================================== original sound slider (Framing tab)
    def _build_original_level(self, t):
        ttk.Label(t, text="Original sound of this moment", style="Card.TLabel").pack(anchor="w", pady=(8, 0))
        self.level_var = tk.DoubleVar(value=1.0)
        s = ttk.Scale(t, from_=0.0, to=1.0, variable=self.level_var, orient="horizontal")
        s.pack(fill="x")
        s.bind("<ButtonRelease-1>", lambda e: self._level_changed())
        self.level_info = tk.StringVar()
        ttk.Label(t, textvariable=self.level_info, style="Help.TLabel", wraplength=270, justify="left").pack(anchor="w")

    def _refresh_original_level(self, seg):
        from clipping.editor import render
        lvl = render.original_level(self.project, seg)
        self.level_var.set(lvl)
        own = "set by you" if seg.original_level is not None else f"from the mode ({self.project.mode.replace('_', ' ')})"
        self.level_info.set(f"{round(lvl * 100)} % ({own})")

    def _level_changed(self):
        i = self.selected_index()
        if i is None:
            return
        seg = self.project.segments[i]
        seg.original_level = round(min(1.0, max(0.0, float(self.level_var.get()))) / 0.05) * 0.05
        seg.original_level = round(seg.original_level, 2)
        self.commit("Changed the original sound of this moment.", keep_selection=seg.id)

    # ================================================================= refreshing
    def _refresh_story(self):
        """Fill the Story and Voice tabs from the project (called by refresh_all)."""
        if not hasattr(self, "scene_tree"):
            return
        pr = self.project
        self.mode_var.set(pr.mode)
        if self.angle_var.get() != pr.angle:
            self.angle_var.set(pr.angle)
        self.scene_tree.delete(*self.scene_tree.get_children())
        scenes = [s for s in pr.scene_log if s.get("source_id") == self.current_source_id]
        for k, s in enumerate(scenes):
            self.scene_tree.insert("", "end", iid=f"sc{k}", values=(
                "★" if s["viral_score"] >= CE.STAR_SCORE else "", f"{timeline.format_time(s['start'])}–{timeline.format_time(s['end'])}",
                s["what_happens"], s["viral_score"]))
        stars = len(CE.stars_from(scenes))
        if scenes:
            self.watch_info.set(f"{len(scenes)} scenes, {stars} starred (★ = likely to go viral). "
                                "Pick one and press Add to cut.")
        # characters
        self.char_tree.delete(*self.char_tree.get_children())
        for k, c in enumerate(V.settings(pr)["characters"]):
            self.char_tree.insert("", "end", iid=f"ch{k}", values=(c.get("name", ""), V.label(c.get("voice") or V.settings(pr)["default"])))
        if pr.mode == "new_story":
            self.chars_box.pack(fill="x", after=self.angle_buttons)
        else:
            self.chars_box.pack_forget()
        # script
        keep = self._selected_line_id() if hasattr(self, "script_tree") else None
        warn = {w["line_id"] for w in S.warnings(pr)}
        self.script_tree.delete(*self.script_tree.get_children())
        for ln in S.visible(pr):
            self.script_tree.insert("", "end", iid=ln["id"], tags=("bad",) if ln["id"] in warn else (),
                                    values=(timeline.format_time(ln["start"], True), ln["character"] or "—", ln["text"]))
        if keep and self.script_tree.exists(keep):
            self.script_tree.selection_set(keep)
        # voice tab
        cfg = V.settings(pr)
        self.voice_var.set(V.label(cfg["default"]))
        self.speed_var.set(cfg["speed"])
        self.pitch_var.set(cfg["pitch"])
        self.talk_var.set(bool((pr.voices or {}).get("remove_talking")))
        made = sum(1 for l in S.visible(pr) if l.get("audio"))
        self.voice_info.set(f"{made} of {len(S.visible(pr))} lines have a voice. "
                            f"Language: {'Swahili' if V.language_of(cfg['default']) == 'sw' else 'English'}.")
        self.voice_tree.delete(*self.voice_tree.get_children())
        for n, ln in enumerate(S.visible(pr), start=1):
            if ln.get("recorded"):
                state = f"your mic {ln['duration']:.1f}s"
            elif ln.get("audio"):
                state = f"made {ln['duration']:.1f}s"
            else:
                state = "not made yet"
            self.voice_tree.insert("", "end", iid="v_" + ln["id"], tags=("bad",) if ln["id"] in warn else (),
                                   values=(n, "your mic" if ln.get("recorded") else V.label(V.voice_for(pr, ln)).split(" · ")[0], state))
        self._refresh_series()

    def _selected_line_id(self):
        sel = self.script_tree.selection() if hasattr(self, "script_tree") else ()
        if sel:
            return sel[0]
        vs = self.voice_tree.selection() if hasattr(self, "voice_tree") else ()
        return vs[0][2:] if vs else None

    def _line_selected(self):
        lid = self._selected_line_id()
        if not lid:
            return
        try:
            ln = S.find(self.project, lid)
        except KeyError:
            return
        self.line_text.set(ln["text"])
        self.line_warn.set(S.warning_for(self.project, lid))
        if self.watch.get() == "cut" and self.player.path == self.preview_path:
            self.player.seek(ln["start"] + 0.01)

    def _voice_line_selected(self):
        vs = self.voice_tree.selection()
        if not vs:
            return
        lid = vs[0][2:]
        try:
            ln = S.find(self.project, lid)
        except KeyError:
            return
        self.voice_line_info.set(S.warning_for(self.project, lid))
        if self.script_tree.exists(lid):
            self.script_tree.selection_set(lid)

    def select_line(self, line_id: str):
        """Called when a block in the Voice-over lane is clicked."""
        self.show_page("Story")
        if self.script_tree.exists(line_id):
            self.script_tree.selection_set(line_id)
            self.script_tree.see(line_id)

    # ================================================================= scene log
    def _claude_cfg(self):
        return CE.make_cfg(self.settings)

    def _get_transcriber(self):
        if getattr(self, "_transcriber", None) is None:
            self._transcriber = C.WhisperTranscriber(self.settings.get("whisper_model", "small"))
        return self._transcriber

    def _source_words(self, src):
        """Every word said in a whole video (cached on disk with the phase 2 transcripts)."""
        cache = getattr(self, "_words_cache", None)
        if cache is None:
            cache = self._words_cache = {}
        if src.id not in cache:
            cache[src.id] = C.transcribe_range(src.path, 0.0, src.duration, self._get_transcriber(), self._transcript_dir())
        return cache[src.id]

    def _check_free(self) -> bool:
        if self.busy:
            messagebox.showinfo(self.APP_NAME, "Wait for the current job to finish, or press Stop.")
            return False
        return True

    def ask_to_watch(self, then=None):
        src = self.selected_source() or (self.project.source(self.current_source_id) if self.current_source_id else None)
        if src is None:
            messagebox.showinfo(self.APP_NAME, "Add a video first (Add video…), then Claude can watch it.")
            return
        if not self._check_free():
            return
        cfg, folder, channel = self._claude_cfg(), self.ensure_folder(), self.project.channel
        words = lambda s: self._source_words(s)

        def work(progress):
            import time
            started = time.monotonic()
            return CE.watch(cfg, src, folder, words, progress=progress, channel=channel), round(time.monotonic() - started, 1)

        def done(result):
            log, took = result
            self.project.scene_log = [s for s in self.project.scene_log if s.get("source_id") != src.id] + log
            self.project.stars = CE.stars_from(self.project.scene_log)
            self.commit(f"Claude watched '{src.name}': {len(log)} scenes, {len(CE.stars_from(log))} starred, in {took} s.")
            self.show_page("Story")
            if then:
                then()

        self.show_page("Story")
        self.run_job("Claude is watching the video: listening to the speech, then looking at pictures…", work, done)

    def _selected_scene(self):
        sel = self.scene_tree.selection()
        if not sel:
            return None
        scenes = [s for s in self.project.scene_log if s.get("source_id") == self.current_source_id]
        k = int(sel[0][2:])
        return scenes[k] if k < len(scenes) else None

    def _scene_selected(self):
        s = self._selected_scene()
        self.scene_why.set(f"{s['who']} · {s['mood']}. {s['why']}" if s else "")
        if s and self.watch.get() == "source":
            self.player.seek(s["start"])

    def add_scene_to_cut(self):
        s = self._selected_scene()
        if not s:
            self.status("Pick a scene in the list first.")
            return
        seg = self.project.add_segment(s["source_id"], s["start"], s["end"])
        self.commit(f"Added the scene to your cut (moment {len(self.project.segments)}).", keep_selection=seg.id)

    def add_starred_to_cut(self):
        stars = CE.stars_from([s for s in self.project.scene_log if s.get("source_id") == self.current_source_id])
        if not stars:
            self.status("No starred scenes yet. Press Ask Claude to watch first.")
            return
        for s in stars:
            self.project.add_segment(s["source_id"], s["start"], s["end"])
        self.commit(f"Added {len(stars)} starred scene(s) to your cut.")

    # ================================================================= mode, angle, characters
    def _mode_changed(self):
        self.project.mode = self.mode_var.get()
        self.commit(f"Mode: {CE.MODE_HELP[self.project.mode][0]}. {CE.MODE_HELP[self.project.mode][1]}")

    def _angle_changed(self):
        text = " ".join(self.angle_var.get().split())
        if text != self.project.angle:
            self.project.angle = text
            self.commit()

    def _scene_text(self) -> str:
        scenes = [s for s in self.project.scene_log]
        return "\n".join(f"[{s['start']:.0f}-{s['end']:.0f}s] {s['what_happens']} ({s['mood']})" for s in scenes[:30])

    def suggest_angles(self):
        if not self._check_free():
            return
        snapshot, cfg, series = self._snapshot(), self._claude_cfg(), self._series_text()
        scene_text = self._scene_text()

        def work(progress):
            progress(0.1)
            return CE.suggest_angles(cfg, snapshot, scene_text, series)

        def done(angles):
            for w in self.angle_buttons.winfo_children():
                w.destroy()
            for a in angles:
                ttk.Button(self.angle_buttons, text=a, command=lambda a=a: self._use_angle(a)).pack(fill="x", pady=1)
            self.status("Click an angle to use it, or write your own.")

        self.run_job("Claude is thinking of three angles…", work, done)

    def _use_angle(self, text: str):
        self.angle_var.set(text)
        self._angle_changed()

    def _char_selected(self):
        sel = self.char_tree.selection()
        if not sel:
            return
        c = V.settings(self.project)["characters"][int(sel[0][2:])]
        self.char_name.set(c.get("name", ""))
        self.char_about.set(c.get("about", ""))
        self.char_voice.set(V.label(c.get("voice") or V.settings(self.project)["default"]))

    def _voice_id_from_label(self, text: str) -> str:
        return next((vid for vid, lab, _ in V.VOICES if lab == text), V.settings(self.project)["default"])

    def save_character(self):
        name = self.char_name.get().strip()
        if not name:
            self.status("Give the character a name first.")
            return
        cfg = V.settings(self.project)
        chars = [c for c in cfg["characters"] if c.get("name", "").lower() != name.lower()]
        chars.append({"name": name, "about": self.char_about.get().strip(), "voice": self._voice_id_from_label(self.char_voice.get())})
        self._set_voices(characters=chars)
        self.commit(f"Saved {name}.")

    def remove_character(self):
        sel = self.char_tree.selection()
        if not sel:
            return
        chars = V.settings(self.project)["characters"]
        gone = chars.pop(int(sel[0][2:]))
        self._set_voices(characters=chars)
        self.commit(f"Removed {gone.get('name', 'the character')}.")

    def _set_voices(self, **changes):
        cfg = dict(self.project.voices or {})
        cfg.update(changes)
        self.project.voices = cfg

    # ================================================================= script
    def _snapshot(self):
        snap = P.Project.from_dict(self.project.to_dict())
        snap.folder = self.folder
        return snap

    def write_script(self, again: bool):
        if not self.project.segments:
            messagebox.showinfo(self.APP_NAME, "Add some moments to the cut list first. The script is timed to them.")
            return
        if not self._check_free():
            return
        if S.visible(self.project) and not messagebox.askyesno(
                self.APP_NAME, "Write the whole script again? This replaces every line (Undo brings them back)."):
            return
        snapshot, cfg, series = self._snapshot(), self._claude_cfg(), self._series_text()
        language = V.language_of(V.settings(snapshot)["default"])
        chars = {c["name"].lower(): c.get("voice", "") for c in V.settings(snapshot)["characters"] if c.get("name")}
        scene_log = list(snapshot.scene_log)

        def transcript_for(seg):
            src = snapshot.source(seg.source_id)
            try:
                words = self._source_words(src)
            except Exception:
                return ""
            return " ".join(w["word"] for w in words if w["src_end"] > seg.source_in and w["src_start"] < seg.source_out)[:600]

        def work(progress):
            progress(0.1)
            items = CE.write_script(cfg, snapshot, scene_log, transcript_for, series, language)
            progress(0.9)
            return S.from_claude(snapshot, items, default_voice_for=lambda ch: chars.get((ch or "").lower(), ""))

        def done(lines):
            self.project.script_lines = lines
            S.refresh(self.project)
            self._story_changed()
            self.commit(f"Claude wrote {len(lines)} lines. Check them, then make the voices.")
            self.show_page("Story")

        self.run_job("Claude is writing the script, timed to your cut…", work, done)

    def _line_or_none(self):
        lid = self._selected_line_id()
        if not lid:
            self.status("Pick a line in the script first.")
            return None
        try:
            return S.find(self.project, lid)
        except KeyError:
            return None

    def _room(self, ln) -> float:
        """Seconds a line may take: until the next line or the end of its moment."""
        lines = S.visible(self.project)
        k = next((i for i, l in enumerate(lines) if l["id"] == ln["id"]), 0)
        idx = self.project.segment_index(ln["segment_id"])
        end = timeline.layout(self.project.segments)[idx].edit_end
        if k + 1 < len(lines):
            end = min(end, lines[k + 1]["start"])
        return max(0.8, end - ln["start"] - 0.1)

    def save_line_text(self):
        ln = self._line_or_none() if self.script_tree.selection() else None
        if ln is None:
            return
        text = " ".join(self.line_text.get().split())
        if text and text != ln["text"]:
            S.set_text(ln, text)
            self._story_changed()
            self.commit("Changed the line. Make voices again to hear it.")

    def nudge_line(self, delta: float):
        ln = self._line_or_none()
        if ln is None:
            return
        S.nudge(self.project, ln["id"], delta)
        self._story_changed()
        self.commit(f"Moved the line to {ln['offset']:.1f} s into its moment.")

    def delete_line(self):
        ln = self._line_or_none()
        if ln is None:
            return
        S.delete(self.project, ln["id"])
        self._story_changed()
        self.commit("Deleted the line.")

    def rewrite_line(self, note: str | None = None):
        ln = self._line_or_none()
        if ln is None or not self._check_free():
            return
        note = self.rewrite_note.get() if note is None else note
        snapshot, cfg, series = self._snapshot(), self._claude_cfg(), self._series_text()
        language = V.language_of(V.voice_for(snapshot, ln))
        room, lid = self._room(ln), ln["id"]

        def work(progress):
            progress(0.1)
            return CE.rewrite_line(cfg, snapshot, S.find(snapshot, lid), room, note, language, series)

        def done(text):
            line = S.find(self.project, lid)
            S.set_text(line, text)
            self.rewrite_note.set("")
            self._story_changed()
            self.commit("Rewrote the line. Make voices again to hear it.", keep_selection=None)

        self.run_job("Claude is rewriting the line…", work, done)

    def shorten_line(self):
        ln = self._line_or_none()
        if ln is None:
            return
        words = S.count_words(ln["text"])
        self.rewrite_line(note=f"Shorter: at most {max(1, int(words * 0.7))} words, same meaning and style.")

    def _story_changed(self):
        """After any change to the script: refresh positions, keep script captions in step."""
        S.refresh(self.project)
        if (self.project.voices or {}).get("captions_from") == "script":
            C.sync_script_captions(self.project)

    # ================================================================= series memory
    def _series_text(self) -> str:
        try:
            return self.series_box.get("1.0", "end").strip()
        except (tk.TclError, AttributeError):
            return CE.read_series(self.edits_root, self.project.channel)

    def _refresh_series(self):
        text = CE.read_series(self.edits_root, self.project.channel)
        if text != self.series_box.get("1.0", "end").strip():
            self.series_box.delete("1.0", "end")
            self.series_box.insert("1.0", text)
        self.series_info.set(f"Saved in My Edits/series-{os.path.basename(CE.series_path('', self.project.channel))[7:-3]}.md "
                             f"for the {self.project.channel} channel.")

    def _save_series(self):
        CE.write_series(self.edits_root, self.project.channel, self.series_box.get("1.0", "end").strip())

    def add_to_series(self):
        if not S.visible(self.project):
            self.status("Write a script first; the summary comes from it.")
            return
        if not self._check_free():
            return
        snapshot, cfg = self._snapshot(), self._claude_cfg()

        def work(progress):
            progress(0.2)
            return CE.series_summary(cfg, snapshot)

        def done(summary):
            now = self._series_text()
            self.series_box.delete("1.0", "end")
            self.series_box.insert("1.0", (now + "\n" if now else "") + f"- {self.project.title}: {summary}")
            self._save_series()
            self.status("Added to the series memory. Claude reads it next time.")

        self.run_job("Claude is summarising this episode for the series memory…", work, done)

    # ================================================================= voices
    def _voice_changed(self):
        self._set_voices(default=self._voice_id_from_label(self.voice_var.get()),
                         speed=int(round(self.speed_var.get())), pitch=int(round(self.pitch_var.get())))
        self.commit("Voice settings changed. Make voices again to use them.")

    def _talk_changed(self):
        on = bool(self.talk_var.get())
        self._set_voices(remove_talking=("centre" if on else ""))
        if on:  # a muted moment has no talking to remove, so bring the game sounds back
            for seg in self.project.segments:
                if seg.original_level is None and self.project.mode == "new_story":
                    seg.original_level = 1.0
        self.commit("Keeping the game sounds and removing the talking." if on else "Original sound left as it was.")

    def _play_file(self, path: str):
        for cmd in (["mpv", "--no-video", "--really-quiet", path], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        messagebox.showinfo(self.APP_NAME, path)

    def play_sample(self):
        if not self._check_free():
            return
        voice, folder = self._voice_id_from_label(self.voice_var.get()), self.ensure_folder()

        def work(progress):
            progress(0.2)
            return V.make_sample(voice, folder)

        self.run_job("Making a sample of the voice…", work, lambda path: (self._play_file(path), self.status("Playing the sample.")))

    def make_voices(self):
        if not S.visible(self.project):
            messagebox.showinfo(self.APP_NAME, "There is no script yet. Press Write the script on the Story tab first.")
            return
        if not self._check_free():
            return
        snapshot, folder = self._snapshot(), self.ensure_folder()

        def work(progress):
            import time
            started = time.monotonic()
            result = V.make_all(snapshot, folder, progress, self.cancel)
            return snapshot, result, round(time.monotonic() - started, 1)

        def done(out):
            snap, result, took = out
            mine = {l["id"]: l for l in snap.script_lines}
            for ln in self.project.script_lines:
                got = mine.get(ln["id"])
                if got and got.get("audio") and got.get("text") == ln.get("text"):
                    for k in ("audio", "duration", "words"):
                        ln[k] = got[k]
            self._story_changed()
            note = f"Voices ready: {result['made']} made, {result['reused']} already there ({took} s)."
            if result["failed"]:
                note = result["failed"][0][1]
            self.commit(note)
            self.show_page("Voice")

        self.run_job("Making the voices (this needs the internet)…", work, done)

    def speed_up_line(self):
        ln = self._line_or_none()
        if ln is None:
            return
        room = self._room(ln)
        pct = V.fit_speed(self.project, ln, room)
        if pct is None:
            self.status("This line already fits, or is too long even when sped up. Try Shorten with Claude.")
            return
        ln["speed"] = pct
        ln["audio"], ln["duration"], ln["words"] = "", 0.0, []
        self._story_changed()
        self.commit(f"This line will be spoken {pct} % faster. Press Make voices.")

    # ---- mic
    def toggle_recording(self):
        if getattr(self, "_rec", None) is not None:
            self._finish_recording()
            return
        ln = self._line_or_none()
        if ln is None:
            return
        self._rec_line = ln["id"]
        self._countdown(3)

    def _countdown(self, n: int):
        if n > 0:
            self.status(f"Get ready to read the line aloud… {n}")
            self.rec_btn.configure(text=f"{n}…")
            self.root.after(1000, lambda: self._countdown(n - 1))
            return
        try:
            path = V.recording_path(self.ensure_folder(), self._rec_line)
            self._rec, self._rec_path = V.start_recording(path), path
        except V.VoiceError as e:
            self.rec_btn.configure(text="Record with mic")
            self.status(str(e))
            return
        self.rec_btn.configure(text="Stop recording")
        self.status("Recording… read the line, then press Stop recording.")

    def _finish_recording(self):
        proc, path, lid = self._rec, self._rec_path, self._rec_line
        self._rec = None
        V.stop_recording(proc)
        self.rec_btn.configure(text="Record with mic")
        try:
            ln = S.find(self.project, lid)
            V.attach_recording(self.project, ln, self.ensure_folder(), path)
        except (V.VoiceError, KeyError) as e:
            self.status(str(e))
            return
        self._story_changed()
        self.commit("Your voice is on the line. Captions spread its words evenly.")

    # ---- play a line over its footage
    def play_line(self):
        ln = self._line_or_none()
        if ln is None:
            return
        if not ln.get("audio"):
            self.status("This line has no voice yet. Press Make voices first.")
            return
        self._play_range = (ln["start"], ln["end"] + 0.3)
        if self.watch.get() == "cut" and self.player.path == self.preview_path and not self.preview_stale:
            self._start_play_range()
            return
        self.watch.set("cut")
        self._watch_changed()   # builds the preview (voices included); _start_play_range runs when it is ready

    def _start_play_range(self):
        rng, self._play_range = getattr(self, "_play_range", None), None
        if rng:
            self._stop_at = rng[1]
            self.player.seek(rng[0])
            self.player.play()

    # ================================================================= Ask Claude (top bar)
    def ask_claude(self):
        """Do whatever comes next: watch, then angle, then script, then voices."""
        self.show_page("Story")
        if self.busy:
            self.status("Claude is already working. Wait, or press Stop.")
            return
        src = self.selected_source() or (self.project.source(self.current_source_id) if self.current_source_id else None)
        if src is None:
            messagebox.showinfo(self.APP_NAME, "Add a video first. Then Ask Claude can watch it, "
                                               "help with an angle, write the script and make the voices.")
            return
        if not [s for s in self.project.scene_log if s.get("source_id") == src.id]:
            self.ask_to_watch()
            return
        if not self.project.segments:
            self.status("Claude has watched. Add the moments you want (try Add all ★), then press Ask Claude again.")
            return
        if not self.project.angle.strip():
            self.suggest_angles()
            return
        if not S.visible(self.project):
            self.write_script(False)
            return
        if any(not l.get("audio") for l in S.visible(self.project)):
            self.make_voices()
            return
        self.status("Everything is done: watched, angle, script and voices. Preview your cut or Export.")
