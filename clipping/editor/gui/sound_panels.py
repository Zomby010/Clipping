"""
clipping.editor.gui.sound_panels — Phase 4 panels for the Clip Editor window:

  Music and SFX   music by mood (volume, start point, fades, loop, how much it
                  dips under voices, licence note), sound effects at the
                  player's time, and loudness for the platforms.
  Reactions       reaction clips (full screen or a corner box), arrows and
                  circles, and the channel logo.

Mixed into EditorApp (gui/app.py); uses its helpers (commit, player, _cut_time…).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from clipping.editor import audio_mix as A
from clipping.editor import overlays as O
from clipping.editor import timeline

DUCK_NAMES = (("Strong", "strong"), ("Light", "light"), ("Off", "off"))
SPOT_NAMES = (("Top left", (0.24, 0.18)), ("Top", (0.46, 0.18)), ("Top right", (0.68, 0.18)),
              ("Left", (0.24, 0.42)), ("Middle", (0.46, 0.42)), ("Right", (0.68, 0.42)),
              ("Low left", (0.24, 0.64)), ("Low middle", (0.46, 0.64)), ("Low right", (0.68, 0.64)))
COLOUR_NAMES = (("Red", "red"), ("Yellow", "yellow"), ("White", "white"))
LANE_COLOURS = {"music": ("#d3f9d8", "#2f9e44"), "sfx": ("#fff3bf", "#e67700"),
                "cutaway": ("#ffd8a8", "#e8590c"), "corner": ("#d0ebff", "#1971c2"),
                "mark": ("#eebefa", "#9c36b5")}


def _listbox(parent, font, height=5):
    return tk.Listbox(parent, height=height, exportselection=False, relief="flat", activestyle="none",
                      highlightthickness=1, highlightbackground="#d0d5dd",
                      selectbackground="#e8590c", font=font)


def _float(var, default, lo, hi):
    try:
        return min(hi, max(lo, float(var.get())))
    except (ValueError, tk.TclError):
        return default


class SoundPanelsMixin:
    _sound_proc = None

    # ============================================================ music + sfx page
    def _build_sound_page(self, t):
        self._heading(t, "Music and SFX", "Music plays under the whole video and dips when someone talks.")
        page = t
        t = self._section(page, "Music")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Mood", style="Card.TLabel").pack(side="left")
        self.music_mood = tk.StringVar(value="chill")
        cb = ttk.Combobox(row, textvariable=self.music_mood, state="readonly", width=12,
                          values=list(A.music_library()))
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_tracks())
        self.track_list = _listbox(t, self.base_font, 4)
        self.track_list.pack(fill="x", pady=(4, 0))
        self.track_list.bind("<<ListboxSelect>>", lambda e: self._track_selected())
        self._tracks: list[str] = []
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Play", width=6, command=lambda: self._play_sound(self._picked_track())).pack(side="left")
        ttk.Button(row, text="Stop", width=6, command=self._stop_sound).pack(side="left", padx=4)
        ttk.Button(row, text="Use this track", style="Accent.TButton", command=self.use_track).pack(side="left")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Add your own…", command=self.add_own_track).pack(side="left")
        ttk.Button(row, text="No music", command=self.remove_music).pack(side="left", padx=4)
        self.music_info = tk.StringVar()
        ttk.Label(t, textvariable=self.music_info, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(6, 2))

        self.music_volume = tk.DoubleVar(value=0.15)
        ttk.Label(t, text="Music volume", style="Card.TLabel").pack(anchor="w", pady=(4, 0))
        s = ttk.Scale(t, from_=0.02, to=0.6, variable=self.music_volume, orient="horizontal")
        s.pack(fill="x")
        s.bind("<ButtonRelease-1>", lambda e: self._music_settings_changed())
        self.music_start = tk.StringVar(value="0")
        self.music_fade_in = tk.StringVar(value="0.5")
        self.music_fade_out = tk.StringVar(value="1.5")
        for label, var, hi in (("Start the track at (s)", self.music_start, 600),
                               ("Fade in (s)", self.music_fade_in, 5), ("Fade out (s)", self.music_fade_out, 5)):
            row = ttk.Frame(t, style="Card.TFrame")
            row.pack(fill="x", pady=(3, 0))
            ttk.Label(row, text=label, style="Card.TLabel").pack(side="left")
            sp = ttk.Spinbox(row, from_=0, to=hi, increment=0.5, width=6, textvariable=var,
                             command=self._music_settings_changed)
            sp.pack(side="right")
            sp.bind("<FocusOut>", lambda e: self._music_settings_changed())
            sp.bind("<Return>", lambda e: self._music_settings_changed())
        self.music_loop = tk.BooleanVar(value=True)
        ttk.Checkbutton(t, text="Repeat if shorter than the video", variable=self.music_loop,
                        style="Card.TCheckbutton", command=self._music_settings_changed).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(3, 0))
        ttk.Label(row, text="Dip under voices", style="Card.TLabel").pack(side="left")
        self.music_duck = tk.StringVar(value="Strong")
        cb = ttk.Combobox(row, textvariable=self.music_duck, state="readonly", width=8,
                          values=[n for n, _ in DUCK_NAMES])
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._music_settings_changed())
        ttk.Label(t, text="Licence note (where the track is from, credit needed)", style="Card.TLabel",
                  wraplength=260, justify="left").pack(anchor="w", pady=(6, 0))
        self.licence_var = tk.StringVar()
        e = ttk.Entry(t, textvariable=self.licence_var)
        e.pack(fill="x")
        e.bind("<Return>", lambda ev: self.save_licence())
        e.bind("<FocusOut>", lambda ev: self.save_licence())
        ttk.Label(t, text="The note is saved with the track and added to the YouTube description.",
                  style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w")

        t = self._section(page, "Sound effects")
        ttk.Label(t, text="A short sound at the player's current time in your cut.", style="Help.TLabel",
                  wraplength=255).pack(anchor="w", pady=(0, 6))
        self.sfx_lib: dict[str, list[str]] = O.sfx_library()
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        self.sfx_kind = tk.StringVar(value=next(iter(self.sfx_lib), ""))
        self.sfx_kind_box = ttk.Combobox(row, textvariable=self.sfx_kind, state="readonly", width=14,
                                         values=list(self.sfx_lib))
        self.sfx_kind_box.pack(side="left")
        ttk.Button(row, text="Play", command=lambda: self._play_sound(self._picked_sfx())).pack(side="left", padx=4)
        self.sfx_gain = tk.DoubleVar(value=0.8)
        ttk.Label(t, text="Sound effect volume", style="Card.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Scale(t, from_=0.1, to=1.5, variable=self.sfx_gain, orient="horizontal").pack(fill="x")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Add here", style="Accent.TButton", command=self.add_sfx_here).pack(side="left")
        ttk.Button(t, text="Add your own…", command=self.add_own_sfx).pack(anchor="w", pady=(4, 0))
        self.sfx_list = _listbox(t, self.base_font, 4)
        self.sfx_list.pack(fill="x", pady=(6, 0))
        self.sfx_list.bind("<<ListboxSelect>>", lambda e: self._jump_to_item(self._sfx_rows, self.sfx_list))
        self._sfx_rows: list[dict] = []
        self._item_buttons(t, lambda: self._sfx_rows, self.sfx_list, "sound effect")

        t = self._section(page, "Loudness")
        self.loud_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(t, text="Even out the loudness", variable=self.loud_var, style="Card.TCheckbutton",
                        command=self._loudness_changed).pack(anchor="w")
        ttk.Label(t, text=f"Sets every export to about {A.LOUDNESS:g} LUFS, the level YouTube, TikTok and "
                          "Reels play at, so it is never too quiet or too loud.",
                  style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w")
        self._fill_tracks()

    def _item_buttons(self, t, rows, listbox, what):
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="−0.1 s", width=6,
                   command=lambda: self._nudge_item(rows(), listbox, -0.1)).pack(side="left")
        ttk.Button(row, text="+0.1 s", width=6,
                   command=lambda: self._nudge_item(rows(), listbox, 0.1)).pack(side="left", padx=4)
        ttk.Button(row, text="Delete", width=7, command=lambda: self._delete_item(rows(), listbox, what)).pack(side="left")

    # ---------------------------------------------------------------- music
    def _fill_tracks(self):
        self._tracks = A.music_library().get(self.music_mood.get(), [])
        self.track_list.delete(0, "end")
        for p in self._tracks:
            self.track_list.insert("end", os.path.splitext(os.path.basename(p))[0])
        if not self._tracks:
            self.track_list.insert("end", "(none yet: Add your own…)")

    def _picked_track(self) -> str | None:
        sel = self.track_list.curselection()
        if sel and sel[0] < len(self._tracks):
            return self._tracks[sel[0]]
        return None

    def _track_selected(self):
        path = self._picked_track()
        if path:
            self.licence_var.set(A.licence_for(path))

    def use_track(self):
        path = self._picked_track()
        if not path:
            self.status("Pick a track in the list first.")
            return
        A.set_music(self.project, path, self.music_mood.get())
        self._apply_music_settings()
        self.commit(f"Music: {os.path.basename(path)}.")

    def remove_music(self):
        if A.music(self.project):
            A.set_music(self.project, None)
            self.commit("Music removed.")

    def add_own_track(self):
        paths = filedialog.askopenfilenames(title="Add music", filetypes=[
            ("Music", " ".join(f"*{e}" for e in A.MUSIC_EXTS)), ("All files", "*")])
        if not paths:
            return
        folder = os.path.join(A.BGM_DIR, self.music_mood.get() or "chill")
        for p in paths:
            O.add_to_library(p, folder)
        self._fill_tracks()
        self.status(f"Added to the {self.music_mood.get()} music. Write where it's from in the licence note.")

    def save_licence(self):
        path = self._picked_track() or (A.music(self.project) or {}).get("path")
        if not path:
            return
        note = self.licence_var.get().strip()
        if note != A.licence_for(path):
            try:
                A.save_licence(path, note)
            except OSError as e:
                self.status(f"Could not save the licence note: {e}")
                return
            self.status("Licence note saved.")
            self._refresh_upload_if_any()

    def _refresh_upload_if_any(self):
        if hasattr(self, "_refresh_upload"):
            self._refresh_upload()

    def _apply_music_settings(self):
        m = (self.project.audio or {}).get("music")
        if not m:
            return
        m["volume"] = round(_float(self.music_volume, 0.15, 0.0, 1.0), 3)
        m["start_at"] = _float(self.music_start, 0.0, 0.0, 36000.0)
        m["fade_in"] = _float(self.music_fade_in, 0.5, 0.0, 10.0)
        m["fade_out"] = _float(self.music_fade_out, 1.5, 0.0, 10.0)
        m["loop"] = bool(self.music_loop.get())
        m["ducking"] = dict(DUCK_NAMES).get(self.music_duck.get(), "strong")

    def _music_settings_changed(self):
        if self._loading or not A.music(self.project):
            return
        self._apply_music_settings()
        self.commit()

    def _loudness_changed(self):
        if self._loading:
            return
        if not isinstance(self.project.audio, dict):
            self.project.audio = {}
        self.project.audio["loudness"] = A.LOUDNESS if self.loud_var.get() else None
        self.commit("Loudness evened out on export." if self.loud_var.get() else "Loudness left as it is.")

    # ---------------------------------------------------------------- sfx
    def _picked_sfx(self) -> str | None:
        files = self.sfx_lib.get(self.sfx_kind.get()) or []
        return files[0] if files else None

    def add_sfx_here(self):
        path = self._picked_sfx()
        if not path:
            self.status("Pick a sound effect first.")
            return
        t = self._cut_time()
        if t is None:
            self.status("Watch 'My cut' and move the player to where the sound should play.")
            return
        O.add_sfx(self.project, path, t, round(_float(self.sfx_gain, 0.8, 0.0, 2.0), 2), self.sfx_kind.get())
        self.commit(f"{self.sfx_kind.get().capitalize()} added at {timeline.format_time(t, True)}.")

    def add_own_sfx(self):
        paths = filedialog.askopenfilenames(title="Add sound effects", filetypes=[
            ("Sounds", " ".join(f"*{e}" for e in O.AUDIO_EXTS)), ("All files", "*")])
        if not paths:
            return
        kind = self.sfx_kind.get() or "my sounds"
        for p in paths:
            O.add_to_library(p, os.path.join(O.SFX_DIR, kind))
        self.sfx_lib = O.sfx_library()
        self.sfx_kind_box.configure(values=list(self.sfx_lib))
        self.status(f"Added to '{kind}'. Only add sounds you are allowed to use.")

    # ---------------------------------------------------------------- shared list helpers
    def _jump_to_item(self, rows, listbox):
        sel = listbox.curselection()
        if sel and sel[0] < len(rows) and self.watch.get() == "cut" and self.player.path == self.preview_path:
            self.player.seek(float(rows[sel[0]].get("start", 0)) + 0.01)

    def _nudge_item(self, rows, listbox, delta):
        sel = listbox.curselection()
        if not sel or sel[0] >= len(rows):
            self.status("Pick one in the list first.")
            return
        item_id = rows[sel[0]]["id"]
        O.nudge(self.project, item_id, delta)
        self.commit()
        listbox.selection_clear(0, "end")
        for i, r in enumerate(self._sfx_rows if listbox is self.sfx_list else self._ovl_rows):
            if r.get("id") == item_id:
                listbox.selection_set(i)
                listbox.see(i)

    def _delete_item(self, rows, listbox, what):
        sel = listbox.curselection()
        if not sel or sel[0] >= len(rows):
            self.status("Pick one in the list first.")
            return
        O.delete(self.project, rows[sel[0]]["id"])
        self.commit(f"The {what} was deleted.")

    # ---------------------------------------------------------------- playing a sound
    def _play_sound(self, path: str | None):
        if not path:
            self.status("Pick something to play first.")
            return
        self._stop_sound()
        for cmd in (["mpv", "--no-video", "--really-quiet", path],
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    ["paplay", path]):
            if shutil.which(cmd[0]):
                try:
                    self._sound_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
        self.status("Can't play sounds here: install mpv (sudo apt install mpv).")

    def _stop_sound(self):
        if self._sound_proc is not None and self._sound_proc.poll() is None:
            self._sound_proc.terminate()
        self._sound_proc = None

    # ============================================================ reactions page
    def _build_reactions_page(self, t):
        self._heading(t, "Reactions", "Your clips, GIFs and pictures from assets/reactions/<mood>/.")
        page = t
        self._build_reaction_picker(page)
        t = self._section(page, "In this edit")
        self.ovl_list = _listbox(t, self.base_font, 6)
        self.ovl_list.pack(fill="x")
        self.ovl_list.bind("<<ListboxSelect>>", lambda e: self._jump_to_item(self._ovl_rows, self.ovl_list))
        self._ovl_rows: list[dict] = []
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(anchor="w", pady=(4, 0))
        ttk.Button(row, text="Delete", width=0, command=self.delete_row).pack(side="left")
        ttk.Button(row, text="−0.1 s", width=0, command=lambda: self.nudge_row(-0.1)).pack(side="left", padx=4)
        ttk.Button(row, text="+0.1 s", width=0, command=lambda: self.nudge_row(0.1)).pack(side="left")
        ttk.Label(t, text="For a reaction in between:", style="Help.TLabel").pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Move to player spot", width=0, command=self.move_inserted_here).pack(side="left")
        ttk.Button(row, text="Shorter", width=0, command=lambda: self.resize_inserted(-0.5)).pack(side="left", padx=4)
        ttk.Button(row, text="Longer", width=0, command=lambda: self.resize_inserted(0.5)).pack(side="left")

        t = self._section(page, "Arrows and circles", open_=False)
        ttk.Label(t, text="Point at something for a moment.", style="Help.TLabel").pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        self.mark_kind = tk.StringVar(value="arrow")
        ttk.Radiobutton(row, text="Arrow", value="arrow", variable=self.mark_kind,
                        style="Card.TRadiobutton").pack(side="left")
        ttk.Radiobutton(row, text="Circle", value="circle", variable=self.mark_kind,
                        style="Card.TRadiobutton").pack(side="left", padx=6)
        self.mark_dir = tk.StringVar(value=f"{O.DIRECTION_LABELS['down-left']} down-left")
        ttk.Combobox(row, textvariable=self.mark_dir, state="readonly", width=12,
                     values=[f"{O.DIRECTION_LABELS[d]} {d}" for d in O.DIRECTIONS]).pack(side="left")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(3, 0))
        self.mark_colour = tk.StringVar(value="Red")
        ttk.Combobox(row, textvariable=self.mark_colour, state="readonly", width=7,
                     values=[n for n, _ in COLOUR_NAMES]).pack(side="left")
        self.mark_spot = tk.StringVar(value="Middle")
        ttk.Combobox(row, textvariable=self.mark_spot, state="readonly", width=11,
                     values=[n for n, _ in SPOT_NAMES]).pack(side="left", padx=6)
        self.mark_secs = tk.StringVar(value="1.5")
        ttk.Spinbox(row, from_=0.5, to=5.0, increment=0.5, width=4, textvariable=self.mark_secs).pack(side="left")
        ttk.Label(t, text="Size", style="Card.TLabel").pack(anchor="w", pady=(3, 0))
        self.mark_size = tk.DoubleVar(value=0.22)
        ttk.Scale(t, from_=0.1, to=0.45, variable=self.mark_size, orient="horizontal").pack(fill="x")
        ttk.Button(t, text="Add here", style="Accent.TButton", command=self.add_mark_here).pack(anchor="w", pady=(6, 0))

        t = self._section(page, "Channel logo", open_=False)
        ttk.Label(t, text="The same small logo on every video of this channel.", style="Help.TLabel",
                  wraplength=255).pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="Choose logo…", command=self.choose_logo).pack(side="left")
        ttk.Button(row, text="No logo", command=self.remove_logo).pack(side="left", padx=4)
        self.logo_info = tk.StringVar()
        ttk.Label(t, textvariable=self.logo_info, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(3, 0))
        ttk.Label(row, text="Where", style="Card.TLabel").pack(side="left")
        self.logo_corner = tk.StringVar(value=O.LOGO_CORNERS[0][1])
        cb = ttk.Combobox(row, textvariable=self.logo_corner, state="readonly", width=11,
                          values=[n for _, n in O.LOGO_CORNERS])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._logo_changed())
        self.logo_size = tk.DoubleVar(value=0.16)
        self.logo_opacity = tk.DoubleVar(value=0.85)
        for label, var, lo, hi in (("Size", self.logo_size, 0.08, 0.3), ("Solid ↔ see-through", self.logo_opacity, 1.0, 0.2)):
            ttk.Label(t, text=label, style="Card.TLabel").pack(anchor="w", pady=(3, 0))
            s = ttk.Scale(t, from_=lo, to=hi, variable=var, orient="horizontal")
            s.pack(fill="x")
            s.bind("<ButtonRelease-1>", lambda e: self._logo_changed())

    def use_script_cues(self):
        """Add reactions / sound effects where Claude's script lines ask for them
        (cues like 'react:laugh' or 'sfx:whoosh', from phase 3)."""
        from clipping.editor import script as S
        S.refresh(self.project)   # line times follow the cut
        lines = [ln for ln in S.visible(self.project) if ln.get("cue")]
        if not lines:
            messagebox.showinfo(self.APP_NAME, "No cues yet. Claude adds cues like 'react:laugh' or "
                                               "'sfx:whoosh' when it writes the script (Story tab).")
            return
        pieces = {p.index: p for p in timeline.layout(self.project.segments)}
        index_of = {s.id: i for i, s in enumerate(self.project.segments)}
        added = 0
        for ln in lines:
            if ln.get("start") is not None:
                t = float(ln["start"])
            elif ln.get("segment_id") in index_of:
                t = pieces[index_of[ln["segment_id"]]].edit_start + float(ln.get("offset", 0.0))
            else:
                continue
            for cue in str(ln["cue"]).replace(",", " ").split():
                kind, _, name = cue.partition(":")
                try:
                    if kind == "react" and self.react_lib.get(name):
                        clips = self.react_lib[name]      # a favourite of that mood first, then a video
                        pick = next((p for p in self.favourites() if p in clips), None) or next(
                            (p for p in clips if p.lower().endswith(O.VIDEO_EXTS + (".gif",))), clips[0])
                        O.add_reaction(self.project, pick, t, "corner", 1.2)
                        added += 1
                    elif kind == "sfx" and self.sfx_lib.get(name):
                        O.add_sfx(self.project, self.sfx_lib[name][0], t, 0.8, name)
                        added += 1
                except ValueError:
                    continue
        self.commit(f"Added {added} reaction(s) and sound effect(s) from the script's cues."
                    if added else "No matching reaction clips or sound effects for the script's cues.")

    def add_mark_here(self):
        t = self._cut_time()
        if t is None:
            self.status("Watch 'My cut' and move the player to where it should show.")
            return
        x, y = dict(SPOT_NAMES).get(self.mark_spot.get(), (0.46, 0.42))
        direction = self.mark_dir.get().split()[-1]
        colour = dict(COLOUR_NAMES).get(self.mark_colour.get(), "red")
        secs = _float(tk.StringVar(value=self.mark_secs.get()), 1.5, 0.5, 5.0)
        O.add_mark(self.project, self.mark_kind.get(), t, x, y, direction, colour,
                   round(_float(self.mark_size, 0.22, 0.1, 0.5), 3), secs)
        self.commit(f"{self.mark_kind.get().capitalize()} added at {timeline.format_time(t, True)}.")

    # ---------------------------------------------------------------- logo
    def choose_logo(self):
        path = filedialog.askopenfilename(title="Choose the channel logo", filetypes=[
            ("Pictures", "*.png *.jpg *.jpeg *.webp"), ("All files", "*")])
        if not path:
            return
        os.makedirs(O.LOGOS_DIR, exist_ok=True)
        ext = os.path.splitext(path)[1].lower() or ".png"
        target = os.path.join(O.LOGOS_DIR, f"{self.project.channel}{ext}")
        try:
            if os.path.abspath(path) != os.path.abspath(target):
                shutil.copy2(path, target)
        except OSError as e:
            self.status(f"Could not copy the logo: {e}")
            return
        self.project.logo = {"path": target}
        self._logo_changed(force=True)

    def remove_logo(self):
        if self.project.logo:
            self.project.logo = {}
            self._remember_logo()
            self.commit("No logo on this edit.")

    def _logo_changed(self, force: bool = False):
        if self._loading or not (self.project.logo or {}).get("path"):
            return
        logo = self.project.logo
        logo["corner"] = {n: v for v, n in O.LOGO_CORNERS}.get(self.logo_corner.get(), "top-left")
        logo["size"] = round(_float(self.logo_size, 0.16, 0.05, 0.4), 3)
        logo["opacity"] = round(_float(self.logo_opacity, 0.85, 0.1, 1.0), 2)
        self._remember_logo()
        self.commit("Logo updated." if not force else "Logo added.")

    def _remember_logo(self):
        logos = self.settings.setdefault("logos", {})
        logos[self.project.channel] = dict(self.project.logo or {})
        from clipping.editor.gui.app import save_settings
        save_settings(self.settings)

    def apply_channel_logo(self):
        """A new edit, or a channel change: use the logo saved for this channel."""
        saved = (self.settings.get("logos") or {}).get(self.project.channel) or {}
        if not (self.project.logo or {}).get("path") and saved.get("path") and os.path.exists(saved["path"]):
            self.project.logo = dict(saved)

    # ============================================================ refreshing
    def _refresh_sound_page(self):
        m = A.music(self.project)
        if m:
            name = os.path.basename(m["path"])
            missing = "" if os.path.exists(m["path"]) else " (file missing!)"
            credit = A.licence_for(m["path"])
            self.music_info.set(f"Playing: {name}{missing}" + ("" if credit else "\nNo licence note yet."))
            self.music_volume.set(float(m["volume"]))
            self.music_start.set(f"{float(m['start_at']):g}")
            self.music_fade_in.set(f"{float(m['fade_in']):g}")
            self.music_fade_out.set(f"{float(m['fade_out']):g}")
            self.music_loop.set(bool(m["loop"]))
            self.music_duck.set({v: n for n, v in DUCK_NAMES}.get(m["ducking"], "Strong"))
        else:
            self.music_info.set("No music on this edit.")
        self.loud_var.set(A.loudness_target(self.project) is not None)
        self._sfx_rows = [f for f in O.sfx_items(self.project) if f.get("visible", True)]
        sel = self.sfx_list.curselection()
        self.sfx_list.delete(0, "end")
        for f in self._sfx_rows:
            self.sfx_list.insert("end", f"{timeline.format_time(f['start'], True)}  {f.get('kind', '')}  "
                                        f"({round(float(f.get('gain', 0.8)) * 100)} %)")
        if sel and sel[0] < len(self._sfx_rows):
            self.sfx_list.selection_set(sel[0])

    def _refresh_reactions_page(self):
        self._ovl_rows = [o for o in self.project.overlays
                          if o.get("type") in O.ANCHORED and o.get("visible", True)] + self._inserted_rows()
        self._ovl_rows.sort(key=lambda o: o.get("start", 0))
        sel = self.ovl_list.curselection()
        self.ovl_list.delete(0, "end")
        for o in self._ovl_rows:
            when = timeline.format_time(o["start"], True)
            if o["type"] == "insert":
                text = f"{when}  in between, {o['length']:g} s  {os.path.basename(o['path'])[:22]}"
            elif o["type"] == "reaction":
                where = "full screen" if o.get("style") == "cutaway" else "corner"
                text = f"{when}  reaction ({where}) {os.path.basename(o['path'])}"
            elif o["type"] == "arrow":
                text = f"{when}  arrow {O.DIRECTION_LABELS.get(o.get('direction'), '')} {o.get('colour', '')}"
            else:
                text = f"{when}  circle {o.get('colour', '')}"
            self.ovl_list.insert("end", text)
        if sel and sel[0] < len(self._ovl_rows):
            self.ovl_list.selection_set(sel[0])
        logo = self.project.logo or {}
        if logo.get("path"):
            ok = os.path.exists(logo["path"])
            self.logo_info.set(f"Logo: {os.path.basename(logo['path'])}" + ("" if ok else " (file missing!)"))
            self.logo_corner.set(dict(O.LOGO_CORNERS).get(logo.get("corner", "top-left"), O.LOGO_CORNERS[0][1]))
            self.logo_size.set(float(logo.get("size", 0.16)))
            self.logo_opacity.set(float(logo.get("opacity", 0.85)))
        else:
            self.logo_info.set("No logo. Logos go in assets/logos/, one per channel.")

    # ============================================================ timeline lanes
    def draw_sound_lanes(self, c, label_w, lane_h, top, span, total):
        def x_of(t):
            return label_w + max(0.0, min(total, t)) / total * span

        def lane_y(name):
            from clipping.editor.gui.app import LANES
            return top + LANES.index(name) * (lane_h + 2)

        m = A.music(self.project)
        if m:
            y = lane_y("Music")
            fill, line = LANE_COLOURS["music"]
            c.create_rectangle(x_of(0) + 1, y + 2, x_of(total) - 1, y + lane_h - 2, fill=fill, outline=line)
            c.create_text(x_of(0) + 6, y + lane_h / 2, anchor="w", fill=line, font=(self.base_font[0], 8),
                          text=os.path.splitext(os.path.basename(m["path"]))[0][:40])
        y = lane_y("SFX")
        fill, line = LANE_COLOURS["sfx"]
        for f in O.visible_sfx(self.project):
            x = x_of(float(f["start"]))
            c.create_rectangle(x, y + 2, x + 6, y + lane_h - 2, fill=fill, outline=line)
        y = lane_y("Reactions")
        for o in O.anchored(self.project):
            key = o.get("style", "cutaway") if o["type"] == "reaction" else "mark"
            fill, line = LANE_COLOURS[key]
            x0, x1 = x_of(float(o["start"])), x_of(float(o["end"]))
            c.create_rectangle(x0 + 1, y + 2, max(x0 + 4, x1 - 1), y + lane_h - 2, fill=fill, outline=line)
