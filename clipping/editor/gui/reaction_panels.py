"""
clipping.editor.gui.reaction_panels — The Reactions tab: pick a reaction from
thumbnails (all, or your favourites), put it into the cut or over it, and
manage what is already in the edit. Arrows, circles and the logo follow.

Mixed into EditorApp next to SoundPanelsMixin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk

from clipping.editor import overlays as O
from clipping.editor import reactions as R
from clipping.editor import timeline

PAGE_SIZE = 12
THUMB = 88
PLACEMENTS = (("insert", "In between (adds time)"),
              ("cutaway", "Over my video, full screen"),
              ("corner", "Over my video, small box"))


def _number(var, default, lo, hi):
    try:
        return min(hi, max(lo, float(var.get())))
    except (ValueError, tk.TclError):
        return default


class ReactionPanelsMixin:
    # ============================================================ building
    def _build_reaction_picker(self, page):
        t = self._section(page, "Pick a reaction")
        self.react_lib: dict[str, list[str]] = O.reaction_library()
        self.react_view = tk.StringVar(value="all")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        for value, text in (("all", "All"), ("fav", "★ Favourites")):
            ttk.Radiobutton(row, text=text, value=value, variable=self.react_view, style="Rail.Toolbutton",
                            command=self._fill_reactions).pack(side="left", padx=(0, 4))
        moods = sorted(set(self.react_lib) | {"laugh", "shock", "hype", "sad", "cringe"})
        self.react_mood = tk.StringVar(value="laughing" if "laughing" in self.react_lib
                                       else next(iter(self.react_lib), moods[0]))
        self.react_mood_box = ttk.Combobox(row, textvariable=self.react_mood, state="readonly", width=13,
                                           values=moods)
        self.react_mood_box.pack(side="right")
        self.react_mood_box.bind("<<ComboboxSelected>>", lambda e: self._fill_reactions())

        self.react_grid = ttk.Frame(t, style="Card.TFrame")
        self.react_grid.pack(fill="x", pady=(8, 0))
        self._react_columns = 3
        self.react_grid.bind("<Configure>", lambda e: self._grid_resized(e.width))
        pager = ttk.Frame(t, style="Card.TFrame")
        pager.pack(fill="x", pady=(4, 0))
        ttk.Button(pager, text="‹ Back", width=0, style="Small.TButton",
                   command=lambda: self._turn_page(-1)).pack(side="left")
        ttk.Button(pager, text="Next ›", width=0, style="Small.TButton",
                   command=lambda: self._turn_page(1)).pack(side="right")
        self.react_page_text = tk.StringVar()
        ttk.Label(pager, textvariable=self.react_page_text, style="Help.TLabel").pack()

        self.react_name = tk.StringVar(value="Click a picture to pick it.")
        ttk.Label(t, textvariable=self.react_name, style="Card.TLabel", wraplength=255,
                  justify="left").pack(anchor="w", pady=(6, 2))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(anchor="w")
        self.fav_btn = ttk.Button(row, text="☆ Favourite", width=0, command=self.toggle_favourite)
        self.fav_btn.pack(side="left")
        ttk.Button(row, text="Play", width=0, command=self._play_reaction).pack(side="left", padx=4)
        ttk.Button(row, text="Add your own…", width=0, command=self.add_own_reaction).pack(side="left")

        t = self._section(page, "Put it in")
        self.react_style = tk.StringVar(value="insert")
        for value, text in PLACEMENTS:
            tk.Radiobutton(t, text=text, value=value, variable=self.react_style, background="#ffffff",
                           activebackground="#ffffff", highlightthickness=0, anchor="w", justify="left",
                           wraplength=300, command=self._placement_changed).pack(anchor="w", fill="x")
        self.corner_box = ttk.Frame(t, style="Card.TFrame")
        row = ttk.Frame(self.corner_box, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Label(row, text="Corner", style="Card.TLabel").pack(side="left")
        self.react_corner = tk.StringVar(value=O.CORNER_SPOTS[0][1])
        ttk.Combobox(row, textvariable=self.react_corner, state="readonly", width=12,
                     values=[n for _, n in O.CORNER_SPOTS]).pack(side="left", padx=6)
        self.react_border = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="White edge", variable=self.react_border,
                        style="Card.TCheckbutton").pack(side="left")
        self.react_row = ttk.Frame(t, style="Card.TFrame")
        self.react_row.pack(fill="x", pady=(8, 0))
        ttk.Label(self.react_row, text="Stays for (seconds)", style="Card.TLabel").pack(side="left")
        self.react_secs = tk.StringVar(value="2.0")
        ttk.Spinbox(self.react_row, from_=R.SECONDS[0], to=R.SECONDS[1], increment=0.5, width=5,
                    textvariable=self.react_secs).pack(side="left", padx=6)
        ttk.Label(t, text="Volume of its own sound", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        self.react_gain = tk.DoubleVar(value=0.9)
        ttk.Scale(t, from_=0.0, to=1.0, variable=self.react_gain, orient="horizontal").pack(fill="x")
        self.react_where = tk.StringVar()
        ttk.Label(t, textvariable=self.react_where, style="Help.TLabel", wraplength=255,
                  justify="left").pack(anchor="w", pady=(8, 4))
        ttk.Button(t, text="Add reaction here", style="Accent.TButton",
                   command=self.add_reaction_here).pack(anchor="w")
        ttk.Button(t, text="Use Claude's cues", command=self.use_script_cues).pack(anchor="w", pady=(8, 0))

        self._react_page = 0
        self._react_files: list[str] = []
        self.react_path: str | None = None
        self._thumbs: dict[str, tk.PhotoImage] = {}
        self._fill_reactions()

    # ============================================================ the grid
    def favourites(self) -> list[str]:
        return R.favourite_paths(self.settings.get("favourite_reactions", []), O.REACTIONS_DIR)

    def toggle_favourite(self):
        if not self.react_path:
            self.status("Click a reaction first.")
            return
        key = R.favourite_key(self.react_path, O.REACTIONS_DIR)
        keys = list(self.settings.get("favourite_reactions", []))
        if key in keys:
            keys.remove(key)
            note = "Taken out of your favourites."
        else:
            keys.append(key)
            note = "Added to your favourites."
        self.settings["favourite_reactions"] = keys
        from clipping.editor.gui.app import save_settings
        save_settings(self.settings)
        if self.react_view.get() == "fav":
            self._fill_reactions(keep_page=True)
        else:
            self._draw_reaction_grid()
        self.status(note)

    def _fill_reactions(self, keep_page: bool = False):
        fav = self.react_view.get() == "fav"
        self._react_files = self.favourites() if fav else self.react_lib.get(self.react_mood.get(), [])
        self.react_mood_box.state(["disabled"] if fav else ["!disabled"])
        if not keep_page:
            self._react_page = 0
        self._draw_reaction_grid()

    def _turn_page(self, step: int):
        pages = max(1, -(-len(self._react_files) // PAGE_SIZE))
        self._react_page = (self._react_page + step) % pages
        self._draw_reaction_grid()

    def _grid_resized(self, width: int):
        columns = max(2, width // (THUMB + 14))
        if columns != self._react_columns:
            self._react_columns = columns
            self._draw_reaction_grid()

    def _thumb(self, path: str):
        if path not in self._thumbs:
            png = R.thumbnail(path, THUMB)
            self._thumbs[path] = tk.PhotoImage(master=self.root, file=png) if png else None
        return self._thumbs[path]

    def _draw_reaction_grid(self):
        for child in self.react_grid.winfo_children():
            child.destroy()
        pages = max(1, -(-len(self._react_files) // PAGE_SIZE))
        self._react_page = min(self._react_page, pages - 1)
        shown = self._react_files[self._react_page * PAGE_SIZE:(self._react_page + 1) * PAGE_SIZE]
        favs = set(self.favourites())
        for n, path in enumerate(shown):
            picture = self._thumb(path)
            ext = os.path.splitext(path)[1].lower()
            tag = "video" if ext in R.VIDEO_EXTS else "GIF" if ext in R.GIF_EXTS else "picture"
            if os.path.abspath(path) in favs:
                tag = "★ " + tag
            chosen = path == self.react_path
            cell = tk.Button(self.react_grid, text=tag, compound="top", background="#ffffff", relief="flat",
                             borderwidth=0, padx=2, pady=2, highlightthickness=3,
                             highlightbackground="#e8590c" if chosen else "#ffffff",
                             highlightcolor="#e8590c", font=(self.base_font[0], 8),
                             command=lambda p=path: self.pick_reaction(p))
            if picture is not None:
                cell.configure(image=picture)
            else:
                cell.configure(text=f"can't show\n{tag}", width=11, height=5)
            cell.grid(row=n // self._react_columns, column=n % self._react_columns, padx=2, pady=2)
        if not shown:
            empty = ("No favourites yet. Pick a reaction and press ☆ Favourite."
                     if self.react_view.get() == "fav" else "Nothing in this mood yet. Press Add your own…")
            ttk.Label(self.react_grid, text=empty, style="Help.TLabel", wraplength=260,
                      justify="left").grid(row=0, column=0, sticky="w")
        self.react_page_text.set(f"Page {self._react_page + 1} of {pages}  ·  {len(self._react_files)} in all")
        picked = self.react_path
        self.fav_btn.configure(text="★ Unfavourite" if picked and os.path.abspath(picked) in favs else "☆ Favourite")

    def pick_reaction(self, path: str):
        if not R.kind_of(path):
            self.status("That file can't be used as a reaction.")
            return
        self.react_path = path
        self.react_secs.set(f"{R.natural_seconds(path):g}")
        self.react_name.set(f"Picked: {os.path.basename(path)[:34]} ({R.kind_of(path)})")
        self._draw_reaction_grid()

    def _picked_reaction(self) -> str | None:
        return self.react_path

    def _play_reaction(self):
        path = self._picked_reaction()
        if not path:
            self.status("Click a reaction first.")
            return
        self._stop_sound()
        if shutil.which("mpv"):
            self._sound_proc = subprocess.Popen(
                ["mpv", "--really-quiet", "--geometry=360x640", "--loop-file=inf", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.open_folder(path)

    def add_own_reaction(self):
        paths = filedialog.askopenfilenames(title="Add reactions", filetypes=[
            ("Videos, GIFs and pictures", " ".join(f"*{e}" for e in R.ALL_EXTS)), ("All files", "*")])
        if not paths:
            return
        folder = os.path.join(O.REACTIONS_DIR, self.react_mood.get() or "laugh")
        added = [O.add_to_library(p, folder) for p in paths]
        self.react_lib = O.reaction_library()
        self.react_view.set("all")
        self._fill_reactions()
        if added:
            self.pick_reaction(added[-1])
        self.status(f"Added to the '{self.react_mood.get()}' reactions.")

    # ============================================================ putting it in
    def _placement_changed(self):
        if self.react_style.get() == "corner":
            self.corner_box.pack(anchor="w", pady=(4, 0), before=self.react_row)
        else:
            self.corner_box.pack_forget()
        self._update_react_where()

    def _update_react_where(self):
        """Say where the next reaction will go (called as the player moves)."""
        if not hasattr(self, "react_where"):
            return
        t = self._cut_time()
        if t is None:
            self.react_where.set("To choose the spot: watch My cut, then pause or click the timeline there.")
        else:
            extra = " The video gets longer." if self.react_style.get() == "insert" else ""
            self.react_where.set(f"It goes in at {timeline.format_time(t, True)} of your cut.{extra}")

    def add_reaction_here(self):
        path = self._picked_reaction()
        if not path:
            self.status("Click a reaction first (or add your own).")
            return
        if not self.project.segments:
            self.status("Add some moments to your cut first.")
            return
        t = self._cut_time()
        if t is None:
            self.status("Watch 'My cut' and move the player to where the reaction should go.")
            self.watch.set("cut")
            self._watch_changed()
            return
        if self.preview_stale:
            self.status("My cut is still updating. Add the reaction when the new preview shows.")
            return
        gain = round(_number(self.react_gain, 0.9, 0.0, 1.0), 2)
        if self.react_style.get() == "insert":
            seconds = _number(self.react_secs, R.DEFAULT_SECONDS, *R.SECONDS)
            try:
                seg = R.insert(self.project, path, t, seconds, gain)
            except (ValueError, OSError) as e:
                self.status(f"Could not add it: {e}")
                return
            self._play_around(seg.id)
            self.commit(f"Reaction put in. Your video is now {timeline.format_time(self.project.total_length, True)}.",
                        keep_selection=seg.id)
            return
        corner = {n: v for v, n in O.CORNER_SPOTS}.get(self.react_corner.get(), "top-left")
        O.add_reaction(self.project, path, t, self.react_style.get(),
                       _number(self.react_secs, 1.2, *O.REACTION_LENGTH), corner, gain,
                       bool(self.react_border.get()))
        self.commit(f"Reaction added at {timeline.format_time(t, True)}.")

    def _play_around(self, segment_id: str):
        """Once the preview is rebuilt, play from just before the reaction to just after."""
        i = self.project.segment_index(segment_id)
        piece = timeline.layout(self.project.segments)[i]
        self._play_range = (max(0.0, piece.edit_start - 1.0), min(self.project.total_length, piece.edit_end + 1.0))

    # ============================================================ what is in the edit
    def _inserted_rows(self) -> list[dict]:
        return [{"id": seg.id, "type": "insert", "start": start, "length": seg.length,
                 "path": self.project.source(seg.source_id).path} for seg, start in R.inserted(self.project)]

    def _picked_row(self, kind: str | None = None) -> dict | None:
        sel = self.ovl_list.curselection()
        if not sel or sel[0] >= len(self._ovl_rows):
            self.status("Pick one in the list first.")
            return None
        row = self._ovl_rows[sel[0]]
        if kind and row["type"] != kind:
            self.status("That works on reactions that are in between your moments.")
            return None
        return row

    def move_inserted_here(self):
        row = self._picked_row("insert")
        t = self._cut_time()
        if row is None:
            return
        if t is None or self.preview_stale:
            self.status("Watch 'My cut', wait for the preview, and move the player to the new spot.")
            return
        seg = R.move(self.project, row["id"], t)
        self._play_around(seg.id)
        self.commit("Reaction moved.", keep_selection=seg.id)

    def resize_inserted(self, delta: float):
        row = self._picked_row("insert")
        if row is None:
            return
        seg = self.project.segments[self.project.segment_index(row["id"])]
        seg.source_out = round(min(R.SECONDS[1], max(R.SECONDS[0], seg.source_out + delta)), 2)
        self.commit(f"The reaction now stays {seg.length:g} s.", keep_selection=seg.id)

    def nudge_row(self, delta: float):
        row = self._picked_row()
        if row is None:
            return
        if row["type"] == "insert":
            self.status("For a reaction in between, use Move to player spot.")
            return
        O.nudge(self.project, row["id"], delta)
        self.commit()

    def delete_row(self):
        row = self._picked_row()
        if row is None:
            return
        if row["type"] == "insert":
            R.remove(self.project, row["id"])
            self.commit("Reaction taken out. Your video is shorter again.")
        else:
            O.delete(self.project, row["id"])
            self.commit("The item was deleted.")
