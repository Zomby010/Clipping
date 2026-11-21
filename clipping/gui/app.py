"""
clipping.gui.app — Clipping Studio, the desktop window.

Tabs:
  Make clips    paste links, the few choices you change often, Start/Stop,
                live progress, buttons to open your folders.
  All settings  every option main.py has, in plain-English sections, with a
                search box. Nothing is hidden.
  API keys      the keys stored in .env (Gemini, Pexels, ...).

Profiles save a whole set of choices under a name (e.g. "GTA 6", "Sports").
Run with:  python clipping_studio.py
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, filedialog

from clipping.gui import settings as S

APP_NAME = "Clipping Studio"
MAX_LOG_LINES = 3000

COLORS = {
    "bg": "#f6f7f9",
    "card": "#ffffff",
    "text": "#1d2330",
    "muted": "#667085",
    "accent": "#e8590c",
    "accent_dark": "#c2410c",
    "ok": "#12805c",
    "bad": "#c0262d",
    "log_bg": "#10141c",
    "log_fg": "#d7dde8",
}


class ScrollFrame(ttk.Frame):
    """A frame with a vertical scrollbar (mouse wheel works on Linux too)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=COLORS["bg"])
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Page.TFrame")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _wheel(self, event):
        if not str(event.widget).startswith(str(self)):
            return
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -int(event.delta / 40) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")


class StudioApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.fields = S.get_fields()
        self.by_dest = {f.dest: f for f in self.fields}
        self.data = S.load_settings(self.fields)
        self.vars: dict[str, tk.Variable] = {}
        self.rows: list[tuple[S.Field, ttk.Frame, ttk.LabelFrame]] = []
        self.proc: subprocess.Popen | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self._progress_open = False

        root.title(APP_NAME)
        root.geometry("1040x780")
        root.minsize(820, 600)
        root.configure(background=COLORS["bg"])
        self._style()
        self._make_vars()

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_make = ttk.Frame(self.tabs, style="Page.TFrame", padding=14)
        self.tab_all = ttk.Frame(self.tabs, style="Page.TFrame", padding=(14, 10))
        self.tab_keys = ttk.Frame(self.tabs, style="Page.TFrame", padding=14)
        self.tabs.add(self.tab_make, text="  Make clips  ")
        self.tabs.add(self.tab_all, text="  All settings  ")
        self.tabs.add(self.tab_keys, text="  API keys  ")

        self._build_make_tab()
        self._build_all_tab()
        self._build_keys_tab()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(120, self._drain_log)
        if not self._has_gemini_key():
            root.after(400, lambda: self._log(
                "Welcome! Claude does the AI work when Claude Code is installed and signed in on this computer. "
                "Add a Gemini key in the 'API keys' tab as the backup, then paste a video link and press Start.\n"
            ))

    # ------------------------------------------------------------------ look
    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Ubuntu", 11) if sys.platform.startswith("linux") else ("Segoe UI", 10)
        self.root.option_add("*TCombobox*Listbox.font", base)
        self.base_font = base
        st.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=base)
        st.configure("Page.TFrame", background=COLORS["bg"])
        st.configure("Card.TFrame", background=COLORS["card"])
        st.configure("Card.TLabelframe", background=COLORS["card"], bordercolor="#e3e6eb", relief="solid")
        st.configure("Card.TLabelframe.Label", background=COLORS["card"], foreground=COLORS["text"],
                     font=(base[0], base[1] + 1, "bold"))
        st.configure("Card.TLabel", background=COLORS["card"])
        st.configure("Card.TCheckbutton", background=COLORS["card"])
        st.configure("Help.TLabel", background=COLORS["card"], foreground=COLORS["muted"],
                     font=(base[0], base[1] - 1))
        st.configure("Title.TLabel", background=COLORS["bg"], font=(base[0], base[1] + 7, "bold"))
        st.configure("Sub.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        st.configure("Status.TLabel", background=COLORS["bg"], font=(base[0], base[1] + 1, "bold"))
        st.configure("Start.TButton", font=(base[0], base[1] + 2, "bold"), foreground="white",
                     background=COLORS["accent"], padding=(22, 10), borderwidth=0)
        st.map("Start.TButton", background=[("disabled", "#f3b48f"), ("active", COLORS["accent_dark"])])
        st.configure("TButton", padding=(12, 6))
        st.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(10, 6), font=(base[0], base[1] + 1))
        st.map("TNotebook.Tab", background=[("selected", COLORS["card"])])
        st.configure("Horizontal.TProgressbar", troughcolor="#e8eaee", background=COLORS["accent"])

    # ------------------------------------------------------------------ data
    def _make_vars(self):
        for f in self.fields:
            value = self.data["values"].get(f.dest, f.start_value)
            if f.kind == "bool":
                self.vars[f.dest] = tk.BooleanVar(value=bool(value))
            else:
                self.vars[f.dest] = tk.StringVar(value="" if value is None else str(value))

    def current_values(self) -> dict:
        out = {}
        for dest, var in self.vars.items():
            out[dest] = bool(var.get()) if isinstance(var, tk.BooleanVar) else var.get()
        return out

    def apply_values(self, values: dict):
        for dest, var in self.vars.items():
            if dest not in values:
                continue
            if isinstance(var, tk.BooleanVar):
                var.set(bool(values[dest]))
            else:
                var.set("" if values[dest] is None else str(values[dest]))
        self._sync_length_choice()
        self._sync_computer_choice()

    def save(self):
        self.data["values"] = self.current_values()
        try:
            S.save_settings(self.data)
        except OSError as e:
            self._log(f"Could not save settings: {e}\n")

    # ------------------------------------------------------------------ tab 1
    def _build_make_tab(self):
        t = self.tab_make
        head = ttk.Frame(t, style="Page.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, style="Title.TLabel").pack(side="left")

        prof = ttk.Frame(head, style="Page.TFrame")
        prof.pack(side="right")
        ttk.Label(prof, text="Profile:", style="Sub.TLabel").pack(side="left", padx=(0, 6))
        self.profile_var = tk.StringVar(value=self.data.get("last_profile") or "")
        self.profile_box = ttk.Combobox(prof, textvariable=self.profile_var, width=18, state="readonly",
                                        values=sorted(self.data["profiles"]))
        self.profile_box.pack(side="left")
        self.profile_box.bind("<<ComboboxSelected>>", lambda e: self.load_profile())
        ttk.Button(prof, text="Save as…", command=self.save_profile).pack(side="left", padx=(6, 0))
        ttk.Button(prof, text="Delete", command=self.delete_profile).pack(side="left", padx=(6, 0))

        ttk.Label(t, text="Paste video links (one per line). Every setting is under 'All settings'.",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 8))

        links_card = ttk.Frame(t, style="Card.TFrame", padding=10)
        links_card.pack(fill="x")
        self.links = tk.Text(links_card, height=3, wrap="none", relief="flat", background=COLORS["card"],
                             font=self.base_font,
                             highlightthickness=1, highlightbackground="#d0d5dd", highlightcolor=COLORS["accent"])
        self.links.pack(fill="x")
        saved_links = self.data.get("links") or ""
        if saved_links:
            self.links.insert("1.0", saved_links)

        quick = ttk.LabelFrame(t, text="Quick choices", style="Card.TLabelframe", padding=12)
        quick.pack(fill="x", pady=10)
        for c in range(4):
            quick.columnconfigure(c, weight=1)

        def cell(row, col, label, widget_factory):
            box = ttk.Frame(quick, style="Card.TFrame")
            box.grid(row=row, column=col, sticky="ew", padx=6, pady=5)
            ttk.Label(box, text=label, style="Card.TLabel").pack(anchor="w")
            w = widget_factory(box)
            w.pack(fill="x", pady=(2, 0))
            return w

        cell(0, 0, "Number of clips",
             lambda p: ttk.Spinbox(p, from_=1, to=20, textvariable=self.vars["clips"], width=6))

        self.length_var = tk.StringVar()
        lw = cell(0, 1, "Clip length", lambda p: ttk.Combobox(
            p, textvariable=self.length_var, state="readonly", values=list(S.CLIP_LENGTHS) + ["Custom (see All settings)"]))
        lw.bind("<<ComboboxSelected>>", lambda e: self._length_chosen())
        self._sync_length_choice()
        for d in ("min_length", "max_length"):
            self.vars[d].trace_add("write", lambda *a: self._sync_length_choice())

        cell(0, 2, "Shape", lambda p: ttk.Combobox(
            p, textvariable=self.vars["ratio"], state="readonly", values=self.by_dest["ratio"].choices))

        self.computer_var = tk.StringVar()
        cw = cell(0, 3, "This computer has", lambda p: ttk.Combobox(
            p, textvariable=self.computer_var, state="readonly",
            values=["No graphics card (CPU)", "NVIDIA graphics card"]))
        cw.bind("<<ComboboxSelected>>", lambda e: self._computer_chosen())
        self._sync_computer_choice()

        cell(1, 0, "Voice", lambda p: ttk.Combobox(p, textvariable=self.vars["voiceover_voice"], values=S.VOICES))
        cell(1, 1, "Voice-over style", lambda p: ttk.Combobox(
            p, textvariable=self.vars["voiceover_style"], state="readonly",
            values=self.by_dest["voiceover_style"].choices))
        cell(1, 2, "Caption style", lambda p: ttk.Combobox(
            p, textvariable=self.vars["font_style"], state="readonly", values=self.by_dest["font_style"].choices))
        cell(1, 3, "Most reactions per clip",
             lambda p: ttk.Spinbox(p, from_=0, to=6, textvariable=self.vars["max_reactions"], width=6))

        cell(2, 0, "Story", lambda p: ttk.Combobox(
            p, textvariable=self.vars["story_mode"], state="readonly",
            values=self.by_dest["story_mode"].choices))
        brief_box = ttk.Frame(quick, style="Card.TFrame")
        brief_box.grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=5)
        ttk.Label(brief_box, text="Story direction (optional: who, what, the angle)",
                  style="Card.TLabel").pack(anchor="w")
        ttk.Entry(brief_box, textvariable=self.vars["story_brief"]).pack(fill="x", pady=(2, 0))

        checks = ttk.Frame(quick, style="Card.TFrame")
        checks.grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(8, 0))
        for i, dest in enumerate(("voiceover", "no_reactions", "no_bgm", "no_subs",
                                  "no_broll", "no_hook", "watermark", "cleanup_source")):
            ttk.Checkbutton(checks, text=self.by_dest[dest].label, variable=self.vars[dest],
                            style="Card.TCheckbutton").grid(row=i // 4, column=i % 4, sticky="w",
                                                            padx=(0, 24), pady=2)

        actions = ttk.Frame(t, style="Page.TFrame")
        actions.pack(fill="x", pady=(2, 8))
        self.start_btn = ttk.Button(actions, text="▶  Start", style="Start.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="■  Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(actions, text="Open My Clips",
                   command=lambda: self.open_folder(S.library_dir(self.current_values()))).pack(side="right")
        ttk.Button(actions, text="Reaction clips folder",
                   command=lambda: self.open_folder(self.vars["reactions_dir"].get()
                                                    or os.path.join(S.REPO_DIR, "assets", "reactions"))
                   ).pack(side="right", padx=6)
        ttk.Button(actions, text="Music folder",
                   command=lambda: self.open_folder(os.path.join(S.REPO_DIR, "assets", "bgm"))).pack(side="right")

        status_row = ttk.Frame(t, style="Page.TFrame")
        status_row.pack(fill="x")
        self.status_var = tk.StringVar(value="Ready.")
        self.status_lbl = ttk.Label(status_row, textvariable=self.status_var, style="Status.TLabel")
        self.status_lbl.pack(side="left")
        self.progress = ttk.Progressbar(status_row, mode="indeterminate", length=220)

        log_frame = ttk.Frame(t, style="Page.TFrame")
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(log_frame, wrap="word", background=COLORS["log_bg"], foreground=COLORS["log_fg"],
                           insertbackground=COLORS["log_fg"], relief="flat", padx=10, pady=8,
                           font=("Ubuntu Mono", 10) if sys.platform.startswith("linux") else ("Consolas", 9))
        bar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

    def _sync_length_choice(self):
        if not hasattr(self, "length_var"):
            return
        try:
            pair = (int(self.vars["min_length"].get()), int(self.vars["max_length"].get()))
        except ValueError:
            pair = None
        name = next((n for n, p in S.CLIP_LENGTHS.items() if p == pair), "Custom (see All settings)")
        if self.length_var.get() != name:
            self.length_var.set(name)

    def _length_chosen(self):
        pair = S.CLIP_LENGTHS.get(self.length_var.get())
        if pair:
            self.vars["min_length"].set(str(pair[0]))
            self.vars["max_length"].set(str(pair[1]))
        else:
            self.show_setting("min_length")

    def _sync_computer_choice(self):
        if hasattr(self, "computer_var"):
            gpu = self.vars["whisper_device"].get() == "cuda"
            self.computer_var.set("NVIDIA graphics card" if gpu else "No graphics card (CPU)")

    def _computer_chosen(self):
        if self.computer_var.get().startswith("NVIDIA"):
            self.vars["whisper_device"].set("cuda")
            self.vars["whisper_compute_type"].set("float16")
            self.vars["whisper_model"].set("large-v3")
        else:
            self.vars["whisper_device"].set("cpu")
            self.vars["whisper_compute_type"].set("int8")
            self.vars["whisper_model"].set("small")

    # ------------------------------------------------------------------ tab 2
    def _build_all_tab(self):
        t = self.tab_all
        top = ttk.Frame(t, style="Page.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Search:", style="Sub.TLabel").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, width=32)
        search.pack(side="left", padx=6)
        self.search_var.trace_add("write", lambda *a: self._filter())
        ttk.Button(top, text="Reset everything to recommended", command=self.reset_all).pack(side="right")

        self.all_scroll = ScrollFrame(t)
        self.all_scroll.pack(fill="both", expand=True)
        body = self.all_scroll.inner
        body.columnconfigure(0, weight=1)

        self.section_frames: dict[str, ttk.LabelFrame] = {}
        self.row_widgets: dict[str, ttk.Frame] = {}
        for f in self.fields:
            sec = self.section_frames.get(f.section)
            if sec is None:
                sec = ttk.LabelFrame(body, text=f.section, style="Card.TLabelframe", padding=(12, 8))
                sec.pack(fill="x", pady=6, padx=(0, 6))
                sec.columnconfigure(1, weight=1)
                self.section_frames[f.section] = sec
            row = ttk.Frame(sec, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            row.columnconfigure(1, weight=1)
            var = self.vars[f.dest]
            if f.kind == "bool":
                ttk.Checkbutton(row, text=f.label, variable=var, style="Card.TCheckbutton").grid(
                    row=0, column=0, columnspan=2, sticky="w")
            else:
                ttk.Label(row, text=f.label, style="Card.TLabel", width=34).grid(row=0, column=0, sticky="w")
                if f.kind == "choice":
                    w = ttk.Combobox(row, textvariable=var, values=f.choices, state="readonly", width=28)
                elif f.dest == "voiceover_voice":
                    w = ttk.Combobox(row, textvariable=var, values=S.VOICES, width=28)
                else:
                    w = ttk.Entry(row, textvariable=var, width=30)
                w.grid(row=0, column=1, sticky="w")
                if f.dest in ("reactions_dir", "library_dir"):
                    ttk.Button(row, text="Choose…", command=lambda v=var: self._pick_dir(v)).grid(
                        row=0, column=2, padx=6)
                elif f.dest in ("image", "hook_source"):
                    ttk.Button(row, text="Choose…", command=lambda v=var: self._pick_file(v)).grid(
                        row=0, column=2, padx=6)
            reset = ttk.Button(row, text="↺", width=3, command=lambda d=f.dest: self.reset_one(d))
            reset.grid(row=0, column=3, sticky="e")
            help_text = f.help
            if f.inverted:
                help_text = f"On when ticked. Untick to turn it off  [{f.flag}]"
            else:
                help_text = f"{help_text}  [{f.flag}]"
            ttk.Label(row, text=help_text, style="Help.TLabel", wraplength=760, justify="left").grid(
                row=1, column=0, columnspan=4, sticky="w", pady=(1, 2))
            self.row_widgets[f.dest] = row

    def _filter(self):
        q = self.search_var.get().strip().lower()
        shown = set()
        for f in self.fields:
            row = self.row_widgets[f.dest]
            hit = not q or q in f.label.lower() or q in f.help.lower() or q in f.flag or q in f.section.lower()
            if hit:
                row.pack(fill="x", pady=3)
                shown.add(f.section)
            else:
                row.pack_forget()
        for name, sec in self.section_frames.items():
            if name in shown:
                sec.pack(fill="x", pady=6, padx=(0, 6))
            else:
                sec.pack_forget()
        # keep section order after re-packing
        for name in S.SECTIONS:
            if name in shown and name in self.section_frames:
                self.section_frames[name].pack_forget()
                self.section_frames[name].pack(fill="x", pady=6, padx=(0, 6))

    def show_setting(self, dest: str):
        self.tabs.select(self.tab_all)
        self.search_var.set(self.by_dest[dest].label)

    def reset_one(self, dest: str):
        f = self.by_dest[dest]
        self.apply_values({dest: f.start_value})

    def reset_all(self):
        if messagebox.askyesno(APP_NAME, "Put every setting back to the recommended value?"):
            self.apply_values(S.default_values(self.fields))

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or S.REPO_DIR)
        if d:
            var.set(d)

    def _pick_file(self, var):
        p = filedialog.askopenfilename(initialdir=os.path.dirname(var.get()) if var.get() else S.REPO_DIR)
        if p:
            var.set(p)

    # ------------------------------------------------------------------ tab 3
    def _build_keys_tab(self):
        t = self.tab_keys
        ttk.Label(t, text="API keys", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, text="Saved in the .env file next to main.py, on this computer only.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 10))
        card = ttk.Frame(t, style="Card.TFrame", padding=14)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        env = S.read_env()
        self.key_vars: dict[str, tk.StringVar] = {}
        self.key_entries = []
        for i, (key, label, url) in enumerate(S.API_KEYS):
            val = env.get(key, "")
            var = tk.StringVar(value="" if S.is_placeholder(val) else val)
            self.key_vars[key] = var
            ttk.Label(card, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=6, padx=(0, 10))
            e = ttk.Entry(card, textvariable=var, show="•", width=48)
            e.grid(row=i, column=1, sticky="ew", pady=6)
            self.key_entries.append(e)
            ttk.Button(card, text="Get a key", command=lambda u=url: webbrowser.open(u)).grid(
                row=i, column=2, padx=(8, 0))
        row = ttk.Frame(t, style="Page.TFrame")
        row.pack(fill="x", pady=10)
        self.show_keys = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Show keys", variable=self.show_keys,
                        command=lambda: [e.configure(show="" if self.show_keys.get() else "•")
                                         for e in self.key_entries]).pack(side="left")
        ttk.Button(row, text="Save keys", command=self.save_keys).pack(side="right")

    def save_keys(self):
        updates = {k: v.get().strip() for k, v in self.key_vars.items() if v.get().strip()}
        if not updates:
            messagebox.showinfo(APP_NAME, "Type a key first.")
            return
        S.write_env(updates)
        messagebox.showinfo(APP_NAME, "Keys saved.")

    def _claude_ready(self) -> bool:
        import shutil
        key = S.read_env().get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        return bool(shutil.which("claude")) or not S.is_placeholder(key)

    def _has_gemini_key(self) -> bool:
        return not S.is_placeholder(S.read_env().get("GOOGLE_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", ""))

    # ------------------------------------------------------------------ profiles
    def save_profile(self):
        name = simpledialog.askstring(APP_NAME, "Name for this profile (for example: GTA 6, Sports):",
                                      initialvalue=self.profile_var.get(), parent=self.root)
        if not name:
            return
        name = name.strip()
        self.data["profiles"][name] = self.current_values()
        self.data["last_profile"] = name
        self.profile_box.configure(values=sorted(self.data["profiles"]))
        self.profile_var.set(name)
        self.save()
        self._log(f"Saved profile '{name}'.\n")

    def load_profile(self):
        name = self.profile_var.get()
        values = self.data["profiles"].get(name)
        if values:
            self.apply_values({**S.default_values(self.fields), **values})
            self.data["last_profile"] = name
            self._log(f"Loaded profile '{name}'.\n")

    def delete_profile(self):
        name = self.profile_var.get()
        if name and name in self.data["profiles"] and messagebox.askyesno(APP_NAME, f"Delete profile '{name}'?"):
            del self.data["profiles"][name]
            self.data["last_profile"] = ""
            self.profile_var.set("")
            self.profile_box.configure(values=sorted(self.data["profiles"]))
            self.save()

    # ------------------------------------------------------------------ running
    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        urls = self.links.get("1.0", "end").split()
        values = self.current_values()
        try:
            cmd = S.build_command(self.fields, values, urls)
        except S.SettingError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        provider = values.get("ai_provider", "claude")
        if not values.get("load_gemini_json"):
            if provider == "gemini" and not self._has_gemini_key():
                messagebox.showwarning(APP_NAME, "Add your Gemini API key first (API keys tab).")
                self.tabs.select(self.tab_keys)
                return
            if provider == "claude" and not self._claude_ready() and not self._has_gemini_key():
                messagebox.showwarning(
                    APP_NAME,
                    "Claude isn't set up on this computer yet and there is no Gemini key for backup.\n\n"
                    "Install Claude Code and sign in (run 'claude' once in a terminal), "
                    "or add a key in the API keys tab.")
                self.tabs.select(self.tab_keys)
                return
        note = ""
        if provider == "claude" and not values.get("load_gemini_json") and not self._claude_ready():
            note = "Claude isn't set up on this computer, so Gemini will do the AI work this time.\n\n"
        self.data["links"] = "\n".join(urls)
        self.save()

        self._clear_log()
        shown = " ".join(f'"{c}"' if " " in c else c for c in cmd[2:])
        self._log(f"$ python {shown}\n\n" + note)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=S.REPO_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=(os.name == "posix"),
            )
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not start main.py:\n{e}")
            return
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_status(f"Working on {len(urls)} video(s)… this can take a while on a laptop.", COLORS["text"])
        self.progress.pack(side="right")
        self.progress.start(12)

    def _reader(self, proc):
        splitter = S.OutputSplitter()
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            for item in splitter.feed(chunk):
                self.log_queue.put(item)
        for item in splitter.flush():
            self.log_queue.put(item)
        proc.wait()
        self.log_queue.put(("__done__", proc.returncode))

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return
        if not messagebox.askyesno(APP_NAME, "Stop the current run? Clips already finished are kept."):
            return
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        self._log("\nStopping…\n")

    def _finished(self, code: int):
        self.progress.stop()
        self.progress.pack_forget()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.proc = None
        if code == 0:
            self._set_status("Done! Press 'Open My Clips' to see your Shorts.", COLORS["ok"])
        elif code in (-signal.SIGTERM, 143):
            self._set_status("Stopped.", COLORS["muted"])
        else:
            self._set_status(f"Something went wrong (code {code}). The last lines of the log say why.", COLORS["bad"])

    def _drain_log(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    self._finished(item[1])
                elif isinstance(item, tuple) and item[0] == "progress":
                    if item[1].strip():
                        self._log_progress(item[1])
                elif isinstance(item, tuple) and item[0] == "line":
                    if self._progress_open:
                        self._log_progress(item[1])
                        self._progress_open = False
                        self._log("\n")
                    else:
                        self._log(item[1] + "\n")
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    # ------------------------------------------------------------------ helpers
    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_progress(self, text: str):
        """Show a redrawing progress bar on one line that updates in place."""
        self.log.configure(state="normal")
        if self._progress_open:
            self.log.delete("end-1c linestart", "end-1c")
        elif self.log.get("end-2c", "end-1c") not in ("", "\n"):
            self.log.insert("end", "\n")
        self.log.insert("end", text.strip())
        self._progress_open = True
        self.log.see("end")
        self.log.configure(state="disabled")
        self.status_var.set(text.strip()[:110])

    def _clear_log(self):
        self._progress_open = False
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_status(self, text, color):
        self.status_var.set(text)
        self.status_lbl.configure(foreground=color)

    def open_folder(self, path: str):
        path = os.path.abspath(path)
        os.makedirs(path, exist_ok=True)
        opener = "xdg-open" if sys.platform.startswith("linux") else ("open" if sys.platform == "darwin" else None)
        try:
            if opener:
                subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not open {path}:\n{e}")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno(APP_NAME, "Clips are still being made. Stop and close?"):
                return
            try:
                os.killpg(self.proc.pid, signal.SIGTERM) if os.name == "posix" else self.proc.terminate()
            except (ProcessLookupError, OSError):
                pass
        self.data["links"] = self.links.get("1.0", "end").strip()
        self.save()
        self.root.destroy()


def main():
    os.chdir(S.REPO_DIR)
    root = tk.Tk(className="ClippingStudio")
    icon = os.path.join(S.REPO_DIR, "assets", "app", "icon.png")
    if os.path.exists(icon):
        try:
            root.iconphoto(True, tk.PhotoImage(file=icon))
        except tk.TclError:
            pass
    StudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
