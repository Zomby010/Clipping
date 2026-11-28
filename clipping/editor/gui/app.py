"""
clipping.editor.gui.app — Clip Editor, the desktop window.

Layout (one window):
  top bar      edit name, channel, New / Open, Undo / Redo, Ask Claude, Export
  left         Cut list: your videos, a box to type moments ("1:02-1:10, ..."),
               the list of moments (reorder, split, delete, nudge), live total
  centre       the player: watch the video you're cutting from, or your cut
               (9:16, with the platform button areas shaded if you want)
  right        tabs: Story, Captions, Voice, Reactions, Music and SFX,
               Framing, Export (phase 1 builds Framing and Export)
  bottom       timeline lanes: Video, Voice-over, Music, SFX, Reactions, Captions

Every change is saved straight away (My Edits/<date> - <name>/project.json)
and can be undone. Slow work (preview copy, export) runs in the background
with a progress bar and a Stop button, so the window never freezes.

Run with:  python clip_editor.py
"""

from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

from clipping.editor import captions as C
from clipping.editor import overlays as O
from clipping.editor import framing, media, platforms, preview, render, timeline
from clipping.editor import importer
from clipping.editor import project as P
from clipping.editor import script as S_script  # phase 3
from clipping.editor.gui.story_panels import StoryPanelsMixin  # phase 3
from clipping.editor.gui.platform_panels import PlatformPanelsMixin
from clipping.editor.gui.review_panels import ReviewPanelsMixin
from clipping.editor.gui.reaction_panels import ReactionPanelsMixin
from clipping.editor.gui.sound_panels import SoundPanelsMixin
from clipping.editor.gui.text_panels import ScrollPage, TextPanelsMixin
from clipping.editor.gui.place_panel import PlacePanelMixin
from clipping.editor.gui.timeline_panel import LANES, LANE_H, LANE_LABEL_W, LANE_TOP, TimelineMixin  # noqa: F401

APP_NAME = "Clip Editor"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SETTINGS_FILE = os.path.join(REPO_DIR, "clip_editor_settings.json")
VIDEO_TYPES = [("Videos", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v"), ("All files", "*")]
SPEEDS = ("0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x")

COLORS = {
    "bg": "#f6f7f9",
    "card": "#ffffff",
    "text": "#1d2330",
    "muted": "#667085",
    "accent": "#e8590c",
    "accent_dark": "#c2410c",
    "bad": "#c0262d",
    "player": "#10141c",
    "lane": "#eef0f3",
    "seg": "#fde3d3",
    "seg_line": "#e8590c",
    "seg_sel": "#e8590c",
    "react": "#e5dbff",
    "react_line": "#7048e8",
}

LATER_TABS = ()   # every phase is built now



def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict):
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass


def ui_font(root) -> tuple[str, int]:
    """A proportional font that is really there. Some Python builds give Tk only
    the old X11 fonts; asking those for "Ubuntu" silently returns a wide
    typewriter font, which made every panel look crammed."""
    if not sys.platform.startswith("linux"):
        return ("Segoe UI", 10)
    have = {f.lower(): f for f in tkfont.families(root)}
    if "ubuntu" in have:
        return ("Ubuntu", 11)
    family = next((have[n] for n in ("dejavu sans", "noto sans", "nimbus sans l", "helvetica") if n in have),
                  "helvetica")
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        tkfont.nametofont(name, root=root).configure(family=family, size=10)
    return (family, 10)


class EditorApp(TimelineMixin, PlacePanelMixin, TextPanelsMixin, StoryPanelsMixin, ReactionPanelsMixin, SoundPanelsMixin, ReviewPanelsMixin, PlatformPanelsMixin):
    APP_NAME = APP_NAME
    COLORS = COLORS

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.edits_root = self.settings.get("edits_root") or P.default_edits_root(REPO_DIR)
        self.ready_root = self.settings.get("ready_root") or P.default_ready_root(REPO_DIR)
        self.project = P.Project()
        self.folder: str | None = None
        self.history = P.History(self.project)
        self.jobs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.mark_in: float | None = None
        self.mark_out: float | None = None
        self.watch = tk.StringVar(value="source")   # "source" or "cut"
        self.current_source_id: str | None = None
        self.preview_path: str | None = None
        self.preview_stale = True
        self._slider_drag = False
        self._loading = False

        root.title(APP_NAME)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(1360, sw - 20)}x{min(860, sh - 70)}+0+0")
        root.minsize(1060, 680)
        if sw <= 1440 or sh <= 820:  # typical laptop screen: use all of it
            try:
                root.attributes("-zoomed", True)
            except tk.TclError:
                pass
        root.configure(background=COLORS["bg"])
        self._style()
        self._build()
        self._bind_keys()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._drain_jobs)
        root.after(200, self._tick)

        last = self.settings.get("last_project")
        if last and os.path.exists(os.path.join(last, P.PROJECT_FILE)):
            root.after(50, lambda: self.open_project(last, quiet=True))
        else:
            self.refresh_all()
            self.status("Add a video to start: press 'Add video…' on the left.")

    # ================================================================= look
    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base = ui_font(self.root)
        self.base_font = base
        st.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=base)
        st.configure("Page.TFrame", background=COLORS["bg"])
        st.configure("Card.TFrame", background=COLORS["card"])
        st.configure("Card.TLabelframe", background=COLORS["card"], bordercolor="#e3e6eb", relief="solid")
        st.configure("Card.TLabelframe.Label", background=COLORS["card"], foreground=COLORS["text"],
                     font=(base[0], base[1] + 1, "bold"))
        st.configure("Card.TLabel", background=COLORS["card"])
        st.configure("Card.TRadiobutton", background=COLORS["card"])
        st.configure("Card.TCheckbutton", background=COLORS["card"])
        st.configure("Help.TLabel", background=COLORS["card"], foreground=COLORS["muted"],
                     font=(base[0], base[1] - 1))
        st.configure("Title.TLabel", background=COLORS["bg"], font=(base[0], base[1] + 6, "bold"))
        st.configure("Sub.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        st.configure("Total.TLabel", background=COLORS["card"], font=(base[0], base[1], "bold"))
        st.configure("Accent.TButton", font=(base[0], base[1] + 1, "bold"), foreground="white",
                     background=COLORS["accent"], padding=(18, 6), borderwidth=0)
        st.map("Accent.TButton", background=[("disabled", "#f3b48f"), ("active", COLORS["accent_dark"])])
        st.configure("TButton", padding=(10, 4))
        st.configure("Small.TButton", padding=(6, 2))
        st.configure("TPanedwindow", background=COLORS["bg"])
        st.configure("Sash", sashthickness=8, gripcount=0)
        st.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(8, 4))
        st.map("TNotebook.Tab", background=[("selected", COLORS["card"])])
        st.configure("Horizontal.TProgressbar", troughcolor="#e8eaee", background=COLORS["accent"])
        st.configure("Treeview", rowheight=24)
        st.configure("Rail.Toolbutton", padding=(8, 7), anchor="w", background="#e9ecf1")
        st.map("Rail.Toolbutton", background=[("selected", COLORS["card"]), ("active", "#dde2e9")],
               foreground=[("selected", COLORS["accent_dark"])])

    # ================================================================ build
    def _build(self):
        outer = ttk.Frame(self.root, style="Page.TFrame", padding=(10, 8))
        outer.pack(fill="both", expand=True)
        self._build_top(outer)

        # Bottom parts first, so they keep their room on small screens.
        bar = ttk.Frame(outer, style="Page.TFrame")
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.status_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.status_var, style="Sub.TLabel").pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop_job, state="disabled")
        self.stop_btn.pack(side="right")
        self.progress = ttk.Progressbar(bar, length=260, maximum=1.0)
        self.progress.pack(side="right", padx=8)

        self._build_timeline(outer, COLORS)   # phase A: gui/timeline_panel.py

        body = ttk.PanedWindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.body = body

        left = ttk.LabelFrame(body, text="Cut list", style="Card.TLabelframe", padding=8)
        self._build_cut_list(left)
        centre = ttk.Frame(body, style="Card.TFrame", padding=8)
        self._build_player(centre)
        right = ttk.Frame(body, style="Page.TFrame")
        self._build_tabs(right)
        for pane, weight in ((left, 0), (centre, 1), (right, 1)):
            body.add(pane, weight=weight)
        body.bind("<ButtonRelease-1>", lambda e: self._remember_columns())
        self._columns_set = 0
        body.bind("<Configure>", lambda e: self._place_columns(e.width))

    def _place_columns(self, width: int):
        """When the window's width changes: your own column widths if you dragged them,
        else a roomy tool panel (the player is a tall picture and needs little width)."""
        if width == self._columns_set or width < 900:
            return
        self._columns_set = width
        saved = self.settings.get("columns")
        first = int(width * saved[0]) if saved else int(min(370, max(290, width * 0.26)))
        second = int(width * saved[1]) if saved else width - int(min(580, max(470, width * 0.40)))
        second = max(first + 260, min(width - 440, second))
        first = min(first, second - 260)

        def place():
            self.body.sashpos(0, first)
            self.body.sashpos(1, second)

        self.root.after_idle(place)

    def _remember_columns(self):
        width = self.body.winfo_width()
        if width > 900:
            now = [round(self.body.sashpos(0) / width, 3), round(self.body.sashpos(1) / width, 3)]
            if now != self.settings.get("columns"):
                self.settings["columns"] = now
                save_settings(self.settings)

    def _build_top(self, parent):
        top = ttk.Frame(parent, style="Page.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="  Name:", style="Sub.TLabel").pack(side="left", padx=(14, 4))
        self.title_var = tk.StringVar()
        title = ttk.Entry(top, textvariable=self.title_var, width=16)
        title.pack(side="left")
        title.bind("<FocusOut>", lambda e: self._title_changed())
        title.bind("<Return>", lambda e: (self._title_changed(), self.root.focus_set()))
        ttk.Label(top, text="  Channel:", style="Sub.TLabel").pack(side="left", padx=(10, 4))
        self.channel_var = tk.StringVar()
        ch = ttk.Combobox(top, textvariable=self.channel_var, values=P.CHANNELS, width=9, state="readonly")
        ch.pack(side="left")
        ch.bind("<<ComboboxSelected>>", lambda e: self._channel_changed())

        self.export_btn = ttk.Button(top, text="Export", style="Accent.TButton", command=self.export)
        self.export_btn.pack(side="right")
        self.claude_btn = ttk.Button(top, text="Ask Claude", command=self.ask_claude)
        self.claude_btn.pack(side="right", padx=8)
        self.redo_btn = ttk.Button(top, text="Redo", width=0, command=self.redo)
        self.redo_btn.pack(side="right")
        self.undo_btn = ttk.Button(top, text="Undo", width=0, command=self.undo)
        self.undo_btn.pack(side="right", padx=(0, 4))
        ttk.Button(top, text="Open…", width=0, command=self.open_dialog).pack(side="right", padx=(0, 12))
        ttk.Button(top, text="New", width=0, command=self.new_project).pack(side="right", padx=(0, 4))
        ttk.Button(top, text="My Clips…", command=self.open_my_clips_dialog).pack(side="right", padx=(0, 4))

    def _build_cut_list(self, parent):
        ttk.Label(parent, text="Your videos", style="Card.TLabel").pack(anchor="w")
        src_row = ttk.Frame(parent, style="Card.TFrame")
        src_row.pack(fill="x")
        self.sources_list = tk.Listbox(src_row, height=2, width=30, exportselection=False,
                                       relief="flat", highlightthickness=1, highlightbackground="#d0d5dd",
                                       font=self.base_font, activestyle="none",
                                       selectbackground=COLORS["accent"])
        self.sources_list.pack(fill="x")
        self.sources_list.bind("<<ListboxSelect>>", lambda e: self._source_selected())
        btns = ttk.Frame(parent, style="Card.TFrame")
        btns.pack(fill="x", pady=(4, 8))
        ttk.Button(btns, text="Add video…", command=self.add_video).pack(side="left")
        ttk.Button(btns, text="Remove video", command=self.remove_video).pack(side="left", padx=4)

        ttk.Label(parent, text="Type moments from the selected video", style="Card.TLabel").pack(anchor="w")
        type_row = ttk.Frame(parent, style="Card.TFrame")
        type_row.pack(fill="x")
        self.ranges_var = tk.StringVar()
        entry = ttk.Entry(type_row, textvariable=self.ranges_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.add_typed_ranges())
        ttk.Button(type_row, text="Add", command=self.add_typed_ranges).pack(side="left", padx=(4, 0))
        ttk.Label(parent, text="Like 1:02-1:10, 2:38-2:49", style="Help.TLabel").pack(anchor="w", pady=(2, 8))

        cols = ("n", "from", "to", "len", "speed", "video")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=4, selectmode="browse")
        time_w = tkfont.Font(font=self.base_font).measure("00:00.0") + 14   # whole times, never cut off
        for col, text, width in (("n", "#", 28), ("from", "From", time_w), ("to", "To", time_w),
                                 ("len", "Length", time_w), ("speed", "Speed", 52), ("video", "Video", 60)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col == "video" else "center", stretch=col == "video")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._segment_selected())

        # Packed at the bottom before the list, so they never get squeezed out.
        self.total_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.total_var, style="Total.TLabel", wraplength=340,
                  justify="left").pack(side="bottom", anchor="w", pady=(6, 0), before=self.tree)
        row1 = ttk.Frame(parent, style="Card.TFrame")
        row2 = ttk.Frame(parent, style="Card.TFrame")
        row2.pack(side="bottom", fill="x", pady=(2, 4), before=self.tree)
        row1.pack(side="bottom", fill="x", pady=(6, 2), before=self.tree)
        self.detail_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.detail_var, style="Help.TLabel", wraplength=340,
                  justify="left").pack(side="bottom", anchor="w", pady=(4, 0), before=self.tree)
        for text, command in (("Move up", lambda: self.move_selected(-1)), ("Move down", lambda: self.move_selected(1)),
                              ("Split here", self.split_selected), ("Delete", self.delete_selected)):
            ttk.Button(row1, text=text, width=0, style="Small.TButton", command=command).pack(side="left", padx=(0, 3))
        ttk.Label(row2, text="Start", style="Card.TLabel").pack(side="left")
        ttk.Button(row2, text="−0.1", width=5, style="Small.TButton", command=lambda: self.nudge("in", -0.1)).pack(side="left", padx=2)
        ttk.Button(row2, text="+0.1", width=5, style="Small.TButton", command=lambda: self.nudge("in", 0.1)).pack(side="left")
        ttk.Label(row2, text="   End", style="Card.TLabel").pack(side="left")
        ttk.Button(row2, text="−0.1", width=5, style="Small.TButton", command=lambda: self.nudge("out", -0.1)).pack(side="left", padx=2)
        ttk.Button(row2, text="+0.1", width=5, style="Small.TButton", command=lambda: self.nudge("out", 0.1)).pack(side="left")


    def _build_player(self, parent):
        top = ttk.Frame(parent, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Watch:", style="Card.TLabel").pack(side="left")
        ttk.Radiobutton(top, text="Original video", value="source", variable=self.watch,
                        style="Card.TRadiobutton", command=self._watch_changed).pack(side="left", padx=6)
        ttk.Radiobutton(top, text="My cut (9:16)", value="cut", variable=self.watch,
                        style="Card.TRadiobutton", command=self._watch_changed).pack(side="left")
        self.safe_var = tk.BooleanVar(value=True)
        self.one_moment = tk.BooleanVar(value=True)
        second = ttk.Frame(parent, style="Card.TFrame")
        second.pack(fill="x")
        self.showing_var = tk.StringVar()
        ttk.Label(second, textvariable=self.showing_var, style="Total.TLabel").pack(side="left")
        ttk.Checkbutton(second, text="Stop after each moment", variable=self.one_moment,
                        style="Card.TCheckbutton").pack(side="right")

        holder = tk.Frame(parent, background=COLORS["player"])
        holder.pack(fill="both", expand=True, pady=6)
        self.player = preview.make_player(holder, COLORS["player"])
        self.player.widget.pack(fill="both", expand=True)

        controls = ttk.Frame(parent, style="Card.TFrame")
        controls.pack(fill="x")
        self.play_btn = ttk.Button(controls, text="Play", width=7, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.time_var = tk.StringVar(value="0:00.0 / 0:00.0")
        ttk.Label(controls, textvariable=self.time_var, style="Card.TLabel", width=17).pack(side="left", padx=6)
        self.slider = ttk.Scale(controls, from_=0, to=1, orient="horizontal", command=self._slider_moved)
        self.slider.pack(side="left", fill="x", expand=True)

        self.slider.bind("<ButtonPress-1>", lambda e: setattr(self, "_slider_drag", True))
        self.slider.bind("<ButtonRelease-1>", self._slider_released)
        if self.player.name != "mpv":
            ttk.Button(controls, text="Open in video player", command=self.player.open_external).pack(side="left", padx=(6, 0))

        marks = ttk.Frame(parent, style="Card.TFrame")
        marks.pack(fill="x", pady=(6, 0))
        for text, command in (("Mark start (I)", self.mark_start), ("Mark end (O)", self.mark_end),
                              ("Add moment", self.add_marked)):
            ttk.Button(marks, text=text, width=0, style="Small.TButton", command=command).pack(side="left", padx=(0, 4))
        self._build_place_button(marks)   # phase A
        self.marks_var = tk.StringVar(value="Start: –   End: –")
        ttk.Label(parent, textvariable=self.marks_var, style="Help.TLabel").pack(anchor="w", pady=(2, 0))
        info = ttk.Frame(parent, style="Card.TFrame")
        info.pack(fill="x")
        self.zone_var = tk.StringVar(value=platforms.ZONE_CHOICES[0][0])
        zones = ttk.Combobox(info, textvariable=self.zone_var, width=9, state="readonly",
                             values=[name for name, _ in platforms.ZONE_CHOICES])
        zones.pack(side="right", padx=(4, 0))
        ttk.Label(info, text="for", style="Card.TLabel").pack(side="right")
        zones.bind("<<ComboboxSelected>>", lambda e: self._zones_changed())
        ttk.Checkbutton(info, text="Shade button areas", variable=self.safe_var,
                        style="Card.TCheckbutton", command=self._overlay_changed).pack(side="left")
        if self.player.name != "mpv":
            ttk.Label(parent, style="Help.TLabel", wraplength=600, justify="left",
                      text="Smooth playback needs mpv: sudo apt install mpv libmpv2 && pip install python-mpv. "
                           "Until then you see still pictures.").pack(anchor="w", pady=(4, 0))

    def _build_tabs(self, parent):
        """Panels on the right, picked from a column of buttons (seven tabs
        side by side would be wider than the panel)."""
        rail = ttk.Frame(parent, style="Page.TFrame")
        rail.pack(side="left", fill="y", padx=(0, 6))
        self.pages = ttk.Frame(parent, style="Card.TFrame", width=300)
        self.pages.pack(side="left", fill="both", expand=True)
        self.pages.pack_propagate(False)
        self.page_var = tk.StringVar()
        self.page_frames: dict[str, ttk.Frame] = {}
        for name, phase, text in LATER_TABS:
            page = ttk.Frame(self.pages, style="Card.TFrame", padding=12)
            ttk.Label(page, text=name, style="Card.TLabel",
                      font=(self.base_font[0], self.base_font[1] + 2, "bold")).pack(anchor="w")
            ttk.Label(page, text=f"Coming in phase {phase}.", style="Help.TLabel").pack(anchor="w", pady=(2, 8))
            ttk.Label(page, text=text, style="Card.TLabel", wraplength=255, justify="left").pack(anchor="w")
            self.page_frames[name] = page
        self.framing_tab = ScrollPage(self.pages, bg=COLORS["card"])
        self.captions_tab = ScrollPage(self.pages, bg=COLORS["card"])
        self.text_tab = ScrollPage(self.pages, bg=COLORS["card"])
        self.export_tab = ScrollPage(self.pages, bg=COLORS["card"])
        # --- phase 3: story and voice ---
        self.story_tab = ScrollPage(self.pages, bg=COLORS["card"])
        self.voice_tab = ScrollPage(self.pages, bg=COLORS["card"])
        self.page_frames["Story"] = self.story_tab
        self.page_frames["Voice"] = self.voice_tab
        # --- end phase 3 ---
        self.sound_tab = ScrollPage(self.pages, bg=COLORS["card"])      # phase 4
        self.react_tab = ScrollPage(self.pages, bg=COLORS["card"])      # phase 4
        self.page_frames["Music and SFX"] = self.sound_tab
        self.page_frames["Reactions"] = self.react_tab
        self.check_tab = ScrollPage(self.pages, bg=COLORS["card"])      # phase 5
        self.page_frames["Check"] = self.check_tab
        self.page_frames["Framing"] = self.framing_tab
        self.page_frames["Captions"] = self.captions_tab
        self.page_frames["Hook and text"] = self.text_tab
        self.page_frames["Export"] = self.export_tab
        order = ("Story", "Captions", "Hook and text", "Voice", "Reactions", "Music and SFX", "Framing", "Check", "Export")
        for name in order:
            ttk.Radiobutton(rail, text=name, value=name, variable=self.page_var, style="Rail.Toolbutton",
                            command=lambda n=name: self.show_page(n)).pack(fill="x", pady=1)
        self._build_framing_tab(self.framing_tab.inner)
        self._build_captions_page(self.captions_tab.inner)
        self._build_text_page(self.text_tab.inner)
        self._build_export_tab(self.export_tab.inner)
        self._build_story_page(self.story_tab.inner)   # phase 3
        self._build_voice_page(self.voice_tab.inner)   # phase 3
        self._build_sound_page(self.sound_tab.inner)
        self._build_reactions_page(self.react_tab.inner)
        self._build_check_page(self.check_tab.inner)
        self.show_page("Framing")

    def show_page(self, name: str):
        self.page_var.set(name)
        for n, frame in self.page_frames.items():
            if n == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    def _build_framing_tab(self, t):
        self.framing_title = tk.StringVar(value="Pick a moment in the cut list.")
        ttk.Label(t, textvariable=self.framing_title, style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1] + 1, "bold")).pack(anchor="w")
        ttk.Label(t, text="How a wide video fits the tall 9:16 frame.", style="Help.TLabel").pack(anchor="w", pady=(0, 8))
        self.fr_mode = tk.StringVar(value="auto")
        for value, text in (("auto", "Follow the people (automatic crop)"),
                            ("crop", "Crop, I choose the position"),
                            ("fit_blur", "Whole picture, blurred behind")):
            ttk.Radiobutton(t, text=text, value=value, variable=self.fr_mode, style="Card.TRadiobutton",
                            command=self._framing_changed).pack(anchor="w", pady=1)
        self.fr_pan_x = tk.DoubleVar(value=0.5)
        self.fr_pan_y = tk.DoubleVar(value=0.5)
        self.fr_zoom = tk.DoubleVar(value=1.0)
        self.pan_scales = []
        for label, var, lo, hi in (("Left ↔ right", self.fr_pan_x, 0.0, 1.0),
                                   ("Up ↕ down (when zoomed)", self.fr_pan_y, 0.0, 1.0),
                                   ("Zoom in", self.fr_zoom, 1.0, 2.0)):
            ttk.Label(t, text=label, style="Card.TLabel").pack(anchor="w", pady=(8, 0))
            s = ttk.Scale(t, from_=lo, to=hi, variable=var, orient="horizontal")
            s.pack(fill="x")
            s.bind("<ButtonRelease-1>", lambda e: self._framing_changed())
            if var is not self.fr_zoom:
                self.pan_scales.append(s)
        ttk.Label(t, text="Speed", style="Card.TLabel").pack(anchor="w", pady=(8, 0))
        self.fr_speed = tk.StringVar(value="1x")
        sp = ttk.Combobox(t, textvariable=self.fr_speed, values=SPEEDS, state="readonly", width=8)
        sp.pack(anchor="w")
        sp.bind("<<ComboboxSelected>>", lambda e: self._framing_changed())
        self._build_original_level(t)   # phase 3: the moment's own sound
        ttk.Button(t, text="Use for every moment", command=self.framing_to_all).pack(anchor="w", pady=(14, 4))
        self.auto_info = tk.StringVar()
        ttk.Label(t, textvariable=self.auto_info, style="Help.TLabel", wraplength=255, justify="left").pack(anchor="w")

    def _build_export_tab(self, t):
        ttk.Label(t, text="Export", style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1] + 2, "bold")).pack(anchor="w")
        page = t
        t = self._section(page, "The video file")
        ttk.Label(t, style="Card.TLabel", wraplength=255, justify="left",
                  text="Makes one 1080 x 1920 video that works on YouTube Shorts, TikTok, Instagram Reels "
                       "and Facebook Reels.").pack(anchor="w", pady=(4, 6))
        self.ready_var = tk.StringVar()
        ttk.Label(t, textvariable=self.ready_var, style="Help.TLabel", wraplength=250, justify="left").pack(anchor="w")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(anchor="w", pady=(8, 8))
        ttk.Button(row, text="Open Ready to post", width=0, command=lambda: self.open_folder(self.ready_root)).pack(anchor="w", pady=1)
        ttk.Button(row, text="Change…", width=0, command=self.change_ready_root).pack(anchor="w", pady=1)
        ttk.Label(t, text="Last exports", style="Card.TLabel").pack(anchor="w", pady=(6, 2))
        self.exports_var = tk.StringVar()
        ttk.Label(t, textvariable=self.exports_var, style="Help.TLabel", wraplength=255, justify="left").pack(anchor="w")
        self._build_upload_section(page)
        self._build_platform_section(page)

    def _bind_keys(self):
        r = self.root
        r.bind_all("<Control-z>", lambda e: self.undo())
        r.bind_all("<Control-y>", lambda e: self.redo())
        r.bind_all("<Control-Z>", lambda e: self.redo())
        r.bind_all("<Control-s>", lambda e: self.save(note="Saved."))

        def move(step):
            self.move_selected(step)
            return "break"

        for widget in (self.tree, r):   # on the list too, so its own arrow keys don't also act
            widget.bind("<Alt-Up>", lambda e: move(-1))
            widget.bind("<Alt-Down>", lambda e: move(1))

        def plain(fn):
            def handler(event):
                if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
                    return None
                fn()
                return "break"
            return handler

        for key, fn in (("i", self.mark_start), ("o", self.mark_end), ("<space>", self.toggle_play),
                        ("<Left>", lambda: self.step(-1)), ("<Right>", lambda: self.step(1)),
                        ("<Delete>", self.delete_selected)):
            r.bind_all(key if key.startswith("<") else f"<Key-{key}>", plain(fn))

        # A focused button also acts on Space, which undid the shortcut. In the main
        # window Space now only plays or pauses; other windows keep the usual press.
        def space_on_button(event):
            if event.widget.winfo_toplevel() is r:
                self.toggle_play()
            else:
                event.widget.invoke()
            return "break"

        for cls in ("TButton", "TRadiobutton", "TCheckbutton"):
            r.bind_class(cls, "<Key-space>", space_on_button)

    # ============================================================== helpers
    def status(self, text: str):
        self.status_var.set(text)

    def selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return self.project.segment_index(sel[0])
        except KeyError:
            return None

    def selected_source(self) -> P.Source | None:
        sel = self.sources_list.curselection()
        if sel and sel[0] < len(self.project.footage()):
            return self.project.footage()[sel[0]]
        return self.project.source(self.current_source_id) if self.current_source_id else None

    def ensure_folder(self) -> str:
        if self.folder is None:
            self.folder = P.new_project_dir(self.edits_root, self.project.title)
            self.settings["last_project"] = self.folder
            save_settings(self.settings)
        return self.folder

    def save(self, note: str | None = None):
        try:
            self.project.folder = self.ensure_folder()   # phase 3: voice_clips() finds the voice files from here
            P.save(self.project, self.project.folder)
            if note:
                self.status(note)
        except OSError as e:
            self.status(f"Could not save: {e}")

    def commit(self, note: str | None = None, keep_selection: str | None = None):
        """Call after every change: undo point, autosave, redraw."""
        C.refresh(self.project)  # captions follow the cut list
        O.refresh(self.project)  # so do sound effects, reactions, arrows and circles
        if self.history.record(self.project):
            self.preview_stale = True
            self.save()
        self.refresh_all(keep_selection)
        if note:
            self.status(note)
        if self.watch.get() == "cut":
            self._schedule_preview()

    # ============================================================ refreshing
    def refresh_all(self, keep_selection: str | None = None):
        self._loading = True
        try:
            self.title_var.set(self.project.title)
            self.channel_var.set(self.project.channel)
            self.root.title(f"{self.project.title} – {APP_NAME}")
            self.sources_list.delete(0, "end")
            for s in self.project.footage():
                self.sources_list.insert("end", f"{s.name}  ({timeline.format_time(s.duration)})")
            if self.current_source_id and self.project.source(self.current_source_id):
                ids = [s.id for s in self.project.footage()]
                if self.current_source_id in ids:
                    self.sources_list.selection_set(ids.index(self.current_source_id))
            sel = keep_selection or (self.tree.selection()[0] if self.tree.selection() else None)
            self.tree.delete(*self.tree.get_children())
            for i, seg in enumerate(self.project.segments, start=1):
                src = self.project.source(seg.source_id)
                self.tree.insert("", "end", iid=seg.id, values=(
                    i, "–" if seg.reaction else timeline.format_time(seg.source_in, True),
                    "–" if seg.reaction else timeline.format_time(seg.source_out, True),
                    timeline.format_time(seg.length, True),
                    f"{seg.speed:g}x", src.name if src else "?"))
            if sel and self.tree.exists(sel):
                self.tree.selection_set(sel)
                self.tree.see(sel)
            self.total_var.set(timeline.describe_length(self.project.total_length))
            self.undo_btn.configure(state="normal" if self.history.can_undo() else "disabled")
            self.redo_btn.configure(state="normal" if self.history.can_redo() else "disabled")
            self._refresh_framing()
            self._refresh_export()
            self._refresh_captions()
            self._refresh_text_page()
            self._refresh_upload()
            self._refresh_story()   # phase 3
            self._refresh_sound_page()
            self._refresh_reactions_page()
            self._refresh_check_page()
            self._refresh_platform_section()
            self.draw_timeline()
        finally:
            self._loading = False

    def _refresh_framing(self):
        i = self.selected_index()
        if i is None:
            self.framing_title.set("Pick a moment in the cut list.")
            self.auto_info.set("")
            self.detail_var.set("")
            self._enable_pan(False, False)
            return
        seg = self.project.segments[i]
        self.framing_title.set(f"Moment {i + 1}")
        self.fr_mode.set(seg.framing.mode)
        self.fr_pan_x.set(seg.framing.pan_x)
        self.fr_pan_y.set(seg.framing.pan_y)
        self.fr_zoom.set(seg.framing.zoom)
        self.fr_speed.set(f"{seg.speed:g}x")
        self._refresh_original_level(seg)   # phase 3
        src = self.project.source(seg.source_id)
        self.detail_var.set(f"Moment {i + 1}: {timeline.format_time(seg.source_in, True)} to "
                            f"{timeline.format_time(seg.source_out, True)}, lasts "
                            f"{timeline.format_time(seg.length, True)}\n{src.name if src else '?'}")
        if seg.framing.mode == "auto":
            if seg.framing.auto_x is None:
                self.auto_info.set("The automatic crop is worked out when you watch your cut or export. "
                                   "No people found = the middle of the picture.")
            else:
                self.auto_info.set(f"Automatic crop centred at {round(seg.framing.auto_x * 100)} % from the left.")
        else:
            self.auto_info.set("")
        can_x = can_y = False
        if src and src.width and src.height:
            cw, ch, _x, _y = framing.crop_box(src.width, src.height, zoom=seg.framing.zoom)
            can_x, can_y = cw < src.width - 2, ch < src.height - 2
            if abs(src.width / src.height - framing.OUT_W / framing.OUT_H) < 0.01:
                self.auto_info.set("This video is already 9:16, so these three choices look the same. "
                                   "Zoom in to crop it, then choose the position.")
        crop = seg.framing.mode == "crop"
        self._enable_pan(crop and can_x, crop and can_y)

    def _enable_pan(self, x: bool, y: bool):
        """Grey out the position sliders when moving them could not change the picture."""
        for scale, on in zip(self.pan_scales, (x, y)):
            scale.state(["!disabled"] if on else ["disabled"])

    def _refresh_export(self):
        self.ready_var.set(f"Videos go to: {self.ready_root}")
        lines = []
        for rec in reversed(self.project.exports[-4:]):
            lines.append(f"{rec.get('at', '')[:16].replace('T', ' ')}: {os.path.basename(rec.get('path', ''))} "
                         f"({timeline.format_time(rec.get('length', 0))}, made in {rec.get('seconds_taken', '?')} s)")
        self.exports_var.set("\n".join(lines) or "Nothing exported yet.")

    # ========================================================== video files
    def add_video(self):
        paths = filedialog.askopenfilenames(title="Add videos", filetypes=VIDEO_TYPES,
                                            initialdir=self.settings.get("last_video_dir") or REPO_DIR)
        if not paths:
            return
        self.settings["last_video_dir"] = os.path.dirname(paths[0])
        save_settings(self.settings)
        added = None
        for path in paths:
            try:
                src = self.project.add_source(media.probe(path))
                added = src
            except media.MediaError as e:
                messagebox.showerror(APP_NAME, str(e))
        if added:
            self.current_source_id = added.id
            if self.project.title == "Untitled edit":
                self.project.title = added.name[:50]
            self.commit(f"Added {added.name}. Watch it, then mark moments with I and O, or type them.")
            self.watch.set("source")
            self.load_source(added)

    def remove_video(self):
        src = self.selected_source()
        if not src:
            return
        used = sum(1 for s in self.project.segments if s.source_id == src.id)
        if used and not messagebox.askyesno(APP_NAME, f"Remove '{src.name}' and its {used} moment(s) from this edit?"):
            return
        self.project.remove_source(src.id)
        if self.current_source_id == src.id:
            self.current_source_id = self.project.footage()[0].id if self.project.footage() else None
        self.commit(f"Removed {src.name}.")
        if not self.project.segments:
            self.watch.set("source")
        self._sync_source()

    def _source_selected(self):
        if self._loading:
            return
        src = self.selected_source()
        if src and src.id != self.current_source_id:
            self.current_source_id = src.id
            self.watch.set("source")
            self.load_source(src)

    def _sync_source(self):
        """Keep the video on screen in step with the edit's videos.
        After Undo, Redo or New edit the edit may have lost (or regained) its video:
        without this the old video kept playing while Mark start / Mark end refused to work."""
        src = self.project.source(self.current_source_id) if self.current_source_id else None
        if src is None:
            src = self.project.footage()[0] if self.project.footage() else None
            self.current_source_id = src.id if src else None
        if self.watch.get() != "source":
            return
        if src is None:
            self.player.clear()
            self.mark_in = self.mark_out = None
            self._show_marks()
            self.slider.set(0)
        elif self.player.path != src.path:
            self.load_source(src)

    def load_source(self, src: P.Source | None, at: float = 0.0):
        if src is None:
            return
        if not os.path.exists(src.path):
            self.status(f"Can't find {src.path}. Was it moved?")
            return
        self.current_source_id = src.id
        self.player.set_overlay(False)
        self.player.load(src.path, at, src.duration)
        self.slider.configure(to=max(0.1, src.duration))
        self.draw_timeline()

    # ============================================================== moments
    def add_typed_ranges(self):
        src = self.selected_source()
        if not src:
            messagebox.showinfo(APP_NAME, "Add a video first, then type its moments.")
            return
        text = self.ranges_var.get()
        try:
            new = self.project.add_ranges(src.id, text)
        except (timeline.TimeParseError, ValueError) as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.ranges_var.set("")
        note = self._whole_clip_note(src.id)
        self.commit(f"Added {len(new)} moment(s) from {src.name}.{note}", keep_selection=new[-1].id)

    def mark_start(self):
        if not self._can_mark():
            return
        self.mark_in = self.player.time()
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self._show_marks()

    def mark_end(self):
        if not self._can_mark():
            return
        t = self.player.time()
        if self.mark_in is not None and t <= self.mark_in:
            self.status("The end must be after the start.")
            return
        self.mark_out = t
        self._show_marks()
        if self.mark_in is not None:
            self.add_marked()

    def _can_mark(self) -> bool:
        if not self.current_source_id:
            self.status("Add a video first, then mark moments in it.")
            return False
        if self.watch.get() != "source":
            self.status("Marks work while watching the video you're cutting from.")
            return False
        return True

    def _show_marks(self):
        a = timeline.format_time(self.mark_in, True) if self.mark_in is not None else "–"
        b = timeline.format_time(self.mark_out, True) if self.mark_out is not None else "–"
        self.marks_var.set(f"Start: {a}   End: {b}")

    def add_marked(self):
        if self.mark_in is None or self.mark_out is None or not self.current_source_id:
            self.status("Mark a start (I) and an end (O) first.")
            return
        try:
            seg = self.project.add_segment(self.current_source_id, self.mark_in, self.mark_out)
        except ValueError as e:
            self.status(str(e))
            return
        self.mark_in = self.mark_out = None
        self._show_marks()
        note = self._whole_clip_note(seg.source_id)
        self.commit(f"Added moment {len(self.project.segments)}.{note}", keep_selection=seg.id)

    def _whole_clip_note(self, source_id: str) -> str:
        if self.project.drop_whole_clip(source_id):
            return " The whole-clip moment was taken out, so your cut is only your moments (Undo brings it back)."
        return ""

    def _segment_selected(self):
        if self._loading:
            return
        i = self.selected_index()
        self._refresh_framing()
        self.draw_timeline()
        if i is None:
            return
        seg = self.project.segments[i]
        if seg.id == getattr(self, "_last_selected", None):
            return  # same moment re-selected after a redraw: don't jump the player
        self._last_selected = seg.id
        if seg.reaction and self.watch.get() == "source":     # a reaction only exists in your cut
            self.watch.set("cut")
            self._watch_changed()
            return
        if self.watch.get() == "source":
            src = self.project.source(seg.source_id)
            if src and (self.current_source_id != src.id or self.player.path != src.path):
                self.load_source(src, seg.source_in)
            else:
                self.player.seek(seg.source_in)
            self._moment_end = None
            if self.player.playing:   # picking a moment while playing: play just that moment
                self._moment_end = seg.source_out
                self._guard_moment_end()
        elif self.player.path == self.preview_path:
            self.player.seek(timeline.layout(self.project.segments)[i].edit_start)

    def move_selected(self, step: int):
        i = self.selected_index()
        if i is None:
            return
        seg_id = self.project.segments[i].id
        self.project.move_segment(i, i + step)
        self.commit(keep_selection=seg_id)

    def delete_selected(self):
        i = self.selected_index()
        if i is None:
            return
        self.project.remove_segment(i)
        nxt = self.project.segments[min(i, len(self.project.segments) - 1)].id if self.project.segments else None
        self.commit(f"Deleted moment {i + 1}. Undo brings it back.", keep_selection=nxt)

    def split_selected(self):
        i = self.selected_index()
        if i is None:
            self.status("Pick the moment to split, move the player to the split point, then press Split here.")
            return
        seg = self.project.segments[i]
        if self.watch.get() == "source":
            if self.current_source_id != seg.source_id:
                self.status("Watch this moment's video to split it.")
                return
            t = self.player.time()
        else:
            hit = timeline.edit_to_source(self.project.segments, self.player.time())
            if not hit or hit[0] != i:
                self.status("Move the player inside the selected moment first.")
                return
            t = hit[1]
        try:
            _a, b = self.project.split_segment(i, t)
        except ValueError as e:
            self.status(str(e))
            return
        self.commit(f"Split moment {i + 1} at {timeline.format_time(t, True)}.", keep_selection=b.id)

    def nudge(self, edge: str, delta: float):
        i = self.selected_index()
        if i is None:
            return
        seg = self.project.segments[i]
        self.project.nudge(i, edge, delta)
        self.commit(keep_selection=seg.id)
        if self.watch.get() == "source":
            self.player.seek(seg.source_in if edge == "in" else max(seg.source_in, seg.source_out - 1.0))

    # ============================================================== framing
    def _framing_changed(self):
        if self._loading:
            return
        i = self.selected_index()
        if i is None:
            return
        seg = self.project.segments[i]
        seg.framing.mode = self.fr_mode.get()
        seg.framing.pan_x = round(self.fr_pan_x.get(), 3)
        seg.framing.pan_y = round(self.fr_pan_y.get(), 3)
        seg.framing.zoom = round(self.fr_zoom.get(), 2)
        seg.speed = float(self.fr_speed.get().rstrip("x"))
        self.commit(keep_selection=seg.id)

    def framing_to_all(self):
        i = self.selected_index()
        if i is None:
            return
        ref = self.project.segments[i]
        for seg in self.project.segments:
            if seg is not ref and not seg.reaction:
                auto_x, auto_y = seg.framing.auto_x, seg.framing.auto_y
                seg.framing = copy.deepcopy(ref.framing)
                seg.framing.auto_x, seg.framing.auto_y = auto_x, auto_y
        self.commit("Every moment now uses this framing.", keep_selection=ref.id)

    # =============================================================== player
    def toggle_play(self):
        if not self.player.path:
            return
        if not self.player.playing and self.watch.get() == "source":
            self._arm_moment_end()
        elif not self.player.playing:
            self._arm_cut_end()
        self.player.toggle()
        if self._moment_end is not None:
            self._guard_moment_end()

    # --- play a selected moment from its start time to its finish time (source view) ---
    _moment_end = None

    def _selected_moment_here(self):
        """The selected moment, if it is cut from the video now loaded in the player."""
        i = self.selected_index()
        if i is None or not self.player.path:
            return None
        seg = self.project.segments[i]
        src = self.project.source(seg.source_id)
        return seg if src and src.path == self.player.path else None

    def _arm_moment_end(self):
        """Before playing: if the playhead is inside the moment (or at its end), stop at its end.
        Parked at the end, Play replays the moment. Anywhere else the video plays freely."""
        seg = self._selected_moment_here()
        self._moment_end = None
        if seg is None:
            return
        t = self.player.time()
        if t >= seg.source_out - 0.05 and t <= seg.source_out + 0.5:
            self.player.seek(seg.source_in)
            t = seg.source_in
        if seg.source_in - 0.05 <= t < seg.source_out:
            self._moment_end = seg.source_out

    def _guard_moment_end(self):
        end = self._moment_end
        if end is None:
            return
        if not self.player.playing or self.watch.get() != "source":
            self._moment_end = None   # paused by the user, or switched view
            return
        if self.player.time() >= end:
            self._moment_end = None
            self.player.pause()
            self.player.seek(end)
            return
        self.root.after(30, self._guard_moment_end)

    # --- My cut: play one moment at a time ---
    _cut_end = None

    def _arm_cut_end(self):
        """Before playing your cut: stop where the moment under the playhead ends.
        Parked at the picked moment's end, Play replays that moment."""
        self._cut_end = None
        if (not self.one_moment.get() or getattr(self, "_play_range", None)
                or self.player.path != self.preview_path or not self.project.segments):
            return
        pieces = timeline.layout(self.project.segments)
        t = self.player.time()
        i = self.selected_index()
        if i is not None and abs(t - (pieces[i].edit_end - 0.04)) <= 0.12:
            self.player.seek(pieces[i].edit_start)
            t = pieces[i].edit_start
        here = next((p for p in pieces if p.edit_start - 0.05 <= t < p.edit_end - 0.1), None)
        if here is None:
            return
        # Stop a frame early, so the next moment's picture never shows.
        self._cut_end = here.edit_end - 0.04
        self.tree.selection_set(self.project.segments[here.index].id)
        self._last_selected = self.project.segments[here.index].id
        self.root.after(30, self._guard_cut_end)

    def _guard_cut_end(self):
        end = self._cut_end
        if end is None:
            return
        if not self.player.playing or self.watch.get() != "cut" or not self.one_moment.get():
            self._cut_end = None
            return
        if self.player.time() >= end:
            self._cut_end = None
            self.player.pause()
            self.player.seek(end)
            return
        self.root.after(30, self._guard_cut_end)

    def step(self, seconds: float):
        if self.player.path:
            self.player.seek(self.player.time() + seconds)

    def _slider_moved(self, value):
        if self._slider_drag:
            self.player.seek(float(value))

    def _slider_released(self, _e):
        self._slider_drag = False
        self.player.seek(float(self.slider.get()))

    def _tick(self):
        try:
            t = self.player.time()
            if self.watch.get() == "cut":
                total = self.project.total_length
            else:
                src = self.project.source(self.current_source_id) if self.current_source_id else None
                total = src.duration if src else 0.0
            self.time_var.set(f"{timeline.format_time(t, True)} / {timeline.format_time(total, True)}")
            self.showing_var.set(self._showing_text(t))
            if not self._slider_drag:
                self.slider.set(t)
            self.play_btn.configure(text="Pause" if self.player.playing else "Play")
            stop_at = getattr(self, "_stop_at", None)   # phase 3: Play line stops at the end of the line
            if stop_at is not None and (not self.player.playing or t >= stop_at):
                if self.player.playing:
                    self.player.pause()
                self._stop_at = None
            self.draw_timeline()
            self._update_react_where()
        except Exception:
            pass
        self.root.after(200, self._tick)

    def _showing_text(self, t: float) -> str:
        if self.watch.get() != "cut":
            return "ORIGINAL VIDEO"
        hit = timeline.edit_to_source(self.project.segments, min(t, max(0.0, self.project.total_length - 0.05)))
        if not hit:
            return "YOUR CUT"
        what = "reaction" if self.project.segments[hit[0]].reaction else "moment"
        return f"YOUR CUT · {what} {hit[0] + 1} of {len(self.project.segments)}"

    def _watch_changed(self):
        if self.watch.get() == "source":
            self.player.pause()
            src = self.project.source(self.current_source_id) if self.current_source_id else None
            if src is None and self.project.footage():
                src = self.project.footage()[0]
            self.load_source(src)
        else:
            if not self.project.segments:
                self.status("Your cut is empty. Add some moments first.")
                self.watch.set("source")
                return
            self._schedule_preview(now=True)

    def _overlay_changed(self):
        if self.watch.get() == "cut":
            self.player.set_overlay(self.safe_var.get())

    # ============================================================ preview
    def _schedule_preview(self, now: bool = False):
        if hasattr(self, "_preview_after") and self._preview_after:
            self.root.after_cancel(self._preview_after)
        self._preview_after = self.root.after(10 if now else 900, self.build_preview)

    def build_preview(self):
        self._preview_after = None
        if self.watch.get() != "cut" or not self.project.segments:
            return
        if self.busy:
            self._schedule_preview()
            return
        if self.project.problems():
            self.status(self.project.problems()[0])
            return
        snapshot = P.Project.from_dict(self.project.to_dict())
        folder = self.ensure_folder()

        def work(progress):
            self._work_out_auto(snapshot, progress, share=0.2)
            path = render.make_preview(snapshot, folder, lambda f: progress(0.2 + 0.8 * f), self.cancel)
            return snapshot, path

        def done(result):
            snap, path = result
            self._apply_auto(snap)
            self.preview_path = path
            self.preview_stale = False
            if self.watch.get() == "cut":
                at = self.player.time() if self.player.path == path else self._selected_start()
                self.player.load(path, at, snap.total_length)
                self.player.set_overlay(self.safe_var.get())
                self.slider.configure(to=max(0.1, snap.total_length))
                self.status(self._cut_ready_note(snap))
                self._refresh_cover()
                if getattr(self, "_play_range", None):   # phase 3: Play line
                    self._start_play_range()

        self.run_job("Making a quick preview of your cut…", work, done)

    @staticmethod
    def _cut_ready_note(project) -> str:
        """What to say under the window once My cut is showing."""
        silent = sum(1 for ln in S_script.visible(project) if not ln.get("audio"))
        if not silent:
            return "Showing your cut. Changes update it automatically."
        muted = " New story mutes the original sound, so those parts are silent." if project.mode == "new_story" else ""
        return f"{silent} script line(s) have no voice yet: open Voice and press Make voices.{muted}"

    def _selected_start(self) -> float:
        """Where the selected moment begins in the finished video (0 if none is selected)."""
        i = self.selected_index()
        return timeline.layout(self.project.segments)[i].edit_start if i is not None else 0.0

    def _work_out_auto(self, snap: P.Project, progress, share: float):
        todo = [s for s in snap.segments
                if s.framing.mode == "auto" and s.framing.auto_x is None and not s.reaction]
        for n, seg in enumerate(todo, start=1):
            if self.cancel.is_set():
                raise render.Cancelled("Stopped.")
            src = snap.source(seg.source_id)
            centre = framing.auto_centre(src.path, seg.source_in, seg.source_out)
            seg.framing.auto_x, seg.framing.auto_y = centre if centre else (0.5, 0.5)
            progress(share * n / len(todo))

    def _apply_auto(self, snap: P.Project):
        """Copy automatic-crop results from a background copy into the live edit."""
        found = {s.id: (s.framing.auto_x, s.framing.auto_y) for s in snap.segments}
        changed = False
        for seg in self.project.segments:
            if seg.id in found and seg.framing.auto_x is None and found[seg.id][0] is not None:
                seg.framing.auto_x, seg.framing.auto_y = found[seg.id]
                changed = True
        if changed:
            # Not an undo step of its own: fold it into the current state.
            self.history.absorb(self.project)
            self.save()
            self._refresh_framing()

    # =============================================================== export
    def export(self):
        if not self.project.segments:
            messagebox.showinfo(APP_NAME, "Add some moments to the cut list first.")
            return
        problems = self.project.problems()
        if problems:
            messagebox.showerror(APP_NAME, "\n".join(problems[:5]))
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, "Wait for the current job to finish, or press Stop.")
            return
        if not self.confirm_checks_before_export():
            return
        self.player.pause()
        snapshot = P.Project.from_dict(self.project.to_dict())
        snapshot.folder = self.ensure_folder()   # phase 3: so the voices are found
        ready = self.ready_root

        def work(progress):
            self._work_out_auto(snapshot, progress, share=0.05)
            return snapshot, render.export(snapshot, ready, lambda f: progress(0.05 + 0.95 * f), self.cancel)

        def done(result):
            snap, record = result
            self._apply_auto(snap)
            self.project.exports.append(record)
            self.add_export_to_manifest(snap, record)
            self.history.absorb(self.project)
            self.save()
            self._refresh_export()
            self.show_page("Export")
            self.status(f"Exported in {record['seconds_taken']} s: {record['path']}")
            files = "\n".join("  " + f for f in record.get("files", []))
            kids = ("\n\nKids video: when you upload, choose \"Yes, it's made for kids\"."
                    if snap.channel == "Kids" else "")
            if messagebox.askyesno(APP_NAME, f"Your video is ready in:\n{record['folder']}\n\n{files}{kids}"
                                             "\n\nOpen the folder?"):
                self.open_folder(record["folder"])

        self.run_job("Exporting the full-size video…", work, done)

    def change_ready_root(self):
        path = filedialog.askdirectory(title="Where should finished videos go?", initialdir=self.ready_root)
        if path:
            self.ready_root = path
            self.settings["ready_root"] = path
            save_settings(self.settings)
            self._refresh_export()

    def open_folder(self, path: str):
        os.makedirs(path, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            messagebox.showinfo(APP_NAME, path)

    # ================================================================ jobs
    def run_job(self, label: str, work, done):
        """Run ``work(progress)`` in the background; ``done(result)`` on the window's thread."""
        self.busy = True
        self.cancel.clear()
        self.progress["value"] = 0
        self.stop_btn.configure(state="normal")
        self.export_btn.configure(state="disabled")
        self.status(label)

        def progress(f):
            self.jobs.put(("progress", f))

        def runner():
            try:
                result = work(progress)
                self.jobs.put(("done", (done, result)))
            except render.Cancelled:
                self.jobs.put(("cancelled", None))
            except Exception as e:  # shown to the user, details on the terminal
                traceback.print_exc()
                self.jobs.put(("error", str(e)))

        threading.Thread(target=runner, daemon=True).start()

    def stop_job(self):
        self.cancel.set()
        self.status("Stopping…")

    def _job_finished(self):
        self.busy = False
        self.stop_btn.configure(state="disabled")
        self.export_btn.configure(state="normal")

    def _drain_jobs(self):
        try:
            while True:
                kind, payload = self.jobs.get_nowait()
                if kind == "progress":
                    self.progress["value"] = payload
                elif kind == "status":
                    self.status(payload)
                elif kind == "done":
                    self._job_finished()
                    fn, result = payload
                    fn(result)
                elif kind == "cancelled":
                    self._job_finished()
                    self.progress["value"] = 0
                    self.status("Stopped.")
                elif kind == "error":
                    self._job_finished()
                    self.status("Something went wrong. See the message.")
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_jobs)

    # ============================================================ projects
    def _title_changed(self):
        title = self.title_var.get().strip() or "Untitled edit"
        if title != self.project.title:
            self.project.title = title
            self.commit()

    def _channel_changed(self):
        if self.channel_var.get() != self.project.channel:
            self.project.channel = self.channel_var.get()
            if (self.project.logo or {}).get("path"):
                self.project.logo = {}
            self.apply_channel_logo()
            self.commit()

    def new_project(self):
        self.leave_place()   # phase A
        title = simpledialog.askstring(APP_NAME, "Name for the new edit:", parent=self.root,
                                       initialvalue="Untitled edit")
        if title is None:
            return
        self.player.pause()
        self.project = P.Project(title=title.strip() or "Untitled edit", channel=self.project.channel)
        self.apply_channel_logo()
        self.apply_template_to_new_edit()
        self.folder = None
        self.history = P.History(self.project)
        self.current_source_id = None
        self.preview_path = None
        self.mark_in = self.mark_out = None
        self._show_marks()
        self.watch.set("source")
        self.save()
        self.refresh_all()
        self._sync_source()
        self.status("New edit. Add a video to start.")

    def open_dialog(self):
        path = filedialog.askopenfilename(title="Open an edit", initialdir=self.edits_root,
                                          filetypes=[("Clip Editor edit", P.PROJECT_FILE), ("All files", "*")])
        if not path:
            return
        if path.lower().endswith(importer.VIDEO_EXTS):
            self.open_video(path)
        else:
            self.open_project(path)

    def open_video(self, path: str):
        """A video picked in Open…: start a new edit with it."""
        if importer.details_for(path):          # a Clipping Studio clip: bring its upload details
            self.open_clip(path)
            return
        try:
            src = media.probe(path)
        except media.MediaError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.player.pause()
        self.project = P.Project(title=P.safe_name(os.path.splitext(os.path.basename(path))[0]),
                                 channel=self.project.channel)
        self.apply_channel_logo()
        self.apply_template_to_new_edit()
        self.current_source_id = self.project.add_source(src).id
        self.folder = None
        self.history = P.History(self.project)
        self.preview_path = None
        self.mark_in = self.mark_out = None
        self._show_marks()
        self.watch.set("source")
        self.save()
        self.refresh_all()
        self._sync_source()
        self.status(f"Started a new edit with {src.name}. Mark moments with I and O, or type them.")

    def open_project(self, path: str, quiet: bool = False):
        self.leave_place()   # phase A
        try:
            project, folder = P.load(path)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            if not quiet:
                messagebox.showerror(APP_NAME, str(e) if isinstance(e, P.NotAnEdit) else f"Can't open that edit: {e}")
            self.refresh_all()
            return
        self.player.pause()
        repaired = S_script.repair(project)
        self.project, self.folder = project, folder
        self.history = P.History(project)
        self.settings["last_project"] = folder
        save_settings(self.settings)
        self.preview_path = None
        self.watch.set("source")
        self.current_source_id = project.footage()[0].id if project.footage() else None
        self.refresh_all()
        if self.current_source_id:
            self.load_source(project.source(self.current_source_id))
        missing = [p for p in project.problems() if "can't be found" in p]
        if repaired:
            self.save()
        fixed = f" Cleaned {repaired} script line(s); press Make voices to hear them." if repaired else ""
        self.status(missing[0] if missing else f"Opened '{project.title}'.{fixed}")

    def undo(self):
        p = self.history.undo()
        if p is not None:
            self._restore(p, "Undone.")

    def redo(self):
        p = self.history.redo()
        if p is not None:
            self._restore(p, "Done again.")

    def _restore(self, project: P.Project, note: str):
        self.project = project
        self.preview_stale = True
        self.save()
        self.refresh_all()
        self._sync_source()
        self.status(note)
        if self.placing:          # phase A: Undo while placing shows the change there
            self._draw_place()
        if self.watch.get() == "cut":
            self._schedule_preview()

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A job is still running. Stop it and close?"):
            return
        self.cancel.set()
        self._stop_sound()
        if self.project.sources or self.project.segments:
            self.save()
        try:
            self.player.close()
        finally:
            self.root.destroy()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="clip_editor.py", description=APP_NAME)
    parser.add_argument("--clip", help="open a Clipping Studio clip (a video in My Clips) as a new edit")
    args = parser.parse_args(argv)
    try:
        media.require_ffmpeg()
    except media.MediaError as e:
        print(e)
    root = tk.Tk(className="ClipEditor")
    icon = os.path.join(REPO_DIR, "assets", "app", "clip-editor.png")
    if os.path.exists(icon):
        try:
            root.iconphoto(True, tk.PhotoImage(file=icon))
        except tk.TclError:
            pass
    app = EditorApp(root)
    if args.clip:
        root.after(300, lambda: app.open_clip(os.path.abspath(args.clip)))
    root.mainloop()


if __name__ == "__main__":
    main()
