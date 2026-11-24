"""
clipping.editor.gui.integration_panels — ClipsAI and Postiz in the Clip Editor window:

  Find clips…            (cut list) ClipsAI suggests moments from the speech of
                         the selected video; tick the ones to add
  Follow the speaker     (Framing) points each moment of this video at whoever
                         is talking
  Send to Postiz…        (Export > Upload list) posts or schedules the upload
                         list on TikTok, Instagram Reels, YouTube and Facebook

Mixed into EditorApp (gui/app.py); uses its helpers (commit, run_job, status…).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from clipping.editor import timeline
from clipping.integrations import clipsai_tool, postiz

LABELS = {"tiktok": "TikTok", "instagram": "Instagram Reels", "youtube": "YouTube Shorts", "facebook": "Facebook Reels"}


class IntegrationPanelsMixin:
    # ============================================================ ClipsAI: find clips
    def find_clips_with_clipsai(self):
        src = self.selected_source()
        if src is None:
            messagebox.showinfo(self.APP_NAME, "Add a video and pick it in the list first.")
            return
        if not clipsai_tool.available():
            messagebox.showinfo(self.APP_NAME, clipsai_tool.INSTALL_HINT)
            return
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return
        path, src_id = src.path, src.id

        def work(progress):
            progress(0.05)
            return clipsai_tool.find_clips(path, 5, 180, log=lambda m: self.jobs.put(("status", m)))

        self.run_job("ClipsAI is looking for clips in the speech…", work,
                     lambda clips: self._show_found_clips(src_id, clips))

    def _show_found_clips(self, src_id: str, clips: list[dict]):
        if not clips:
            self.status("ClipsAI found no clips in this video (it needs speech).")
            return
        win = tk.Toplevel(self.root)
        win.title("Clips found by ClipsAI")
        win.transient(self.root)
        win.geometry("640x440")
        ttk.Label(win, padding=8, wraplength=600, justify="left",
                  text="ClipsAI found these from what people say. Tick the ones to add to your cut list.").pack(anchor="w")
        frame = ttk.Frame(win, padding=(8, 0))
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        ticks = []
        for c in clips:
            var = tk.BooleanVar(value=False)
            text = (f"{timeline.format_time(c['start'], True)}–{timeline.format_time(c['end'], True)}"
                    f"  ({c['length']:.0f} s)  {c.get('text', '')[:90]}")
            ttk.Checkbutton(inner, text=text, variable=var).pack(anchor="w", pady=1)
            ticks.append((var, c))

        def add():
            chosen = [c for v, c in ticks if v.get()]
            if not chosen:
                messagebox.showinfo(self.APP_NAME, "Tick at least one clip.", parent=win)
                return
            for c in chosen:
                self.project.add_segment(src_id, c["start"], c["end"])
            win.destroy()
            self.commit(f"Added {len(chosen)} moment(s) found by ClipsAI.")

        row = ttk.Frame(win, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Add ticked", style="Accent.TButton", command=add).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        self._clips_dialog = win

    # ============================================================ ClipsAI: follow the speaker
    def follow_speaker_with_clipsai(self):
        i = self.selected_index()
        if i is None:
            messagebox.showinfo(self.APP_NAME, "Pick a moment in the cut list first.")
            return
        if not clipsai_tool.available():
            messagebox.showinfo(self.APP_NAME, clipsai_tool.INSTALL_HINT)
            return
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return
        seg = self.project.segments[i]
        src = self.project.source(seg.source_id)
        path, src_id, keep = src.path, src.id, seg.id
        split = messagebox.askyesno(self.APP_NAME, "Follow whoever is talking in every moment from this video.\n\n"
                                    "Also cut a moment in two where the speaker changes, so each part "
                                    "is framed on its own speaker?")

        def work(progress):
            progress(0.05)
            return clipsai_tool.speaker_crops(path, log=lambda m: self.jobs.put(("status", m)))

        def done(crops):
            n = clipsai_tool.follow_speaker(self.project, src_id, split=split, crops_for=lambda _p: crops)
            self.commit(f"Framed {n} moment(s) on the speaker. Undo takes it back.", keep_selection=keep)
            self._refresh_framing()

        self.run_job("ClipsAI is finding who talks when…", work, done)

    # ============================================================ Postiz
    def _build_postiz_section(self, t):
        ttk.Button(t, text="Send to Postiz…", command=self.open_postiz_dialog).pack(anchor="w", pady=(4, 0))
        ttk.Label(t, style="Help.TLabel", wraplength=260, justify="left",
                  text="Posts or schedules the list on TikTok, Instagram Reels, YouTube and Facebook "
                       "through your Postiz account (POSTIZ_API_KEY in .env).").pack(anchor="w")

    def open_postiz_dialog(self):
        if not postiz.settings()["api_key"]:
            messagebox.showinfo(self.APP_NAME, "Add your Postiz API key first: put POSTIZ_API_KEY=... in the .env "
                                "file (Postiz > Settings > Developers > Public API), or set it in Clipping "
                                "Studio's API keys. For your own Postiz server also add POSTIZ_URL.")
            return
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return

        def work(progress):
            progress(0.2)
            return postiz.Client().channels()

        self.run_job("Asking Postiz which accounts are connected…", work, self._postiz_dialog)

    def _postiz_dialog(self, channels: list[dict]):
        usable = [c for c in channels if not c.get("disabled") and c.get("identifier") in postiz.PROVIDERS]
        if not usable:
            messagebox.showinfo(self.APP_NAME, "No TikTok, Instagram, YouTube or Facebook account is connected "
                                "in Postiz yet. Connect them in Postiz first.")
            return
        win = tk.Toplevel(self.root)
        win.title("Send to Postiz")
        win.transient(self.root)
        win.geometry("700x470")
        ttk.Label(win, padding=8, text="Send the videos in the upload list that weren't sent yet to:").pack(anchor="w")
        ticks = []
        for c in usable:
            var = tk.BooleanVar(value=postiz.short_name(c) in ("tiktok", "instagram"))
            ttk.Checkbutton(win, text=f"{LABELS.get(postiz.short_name(c), postiz.short_name(c))}  ({c.get('name')})", variable=var,
                            padding=(16, 0)).pack(anchor="w")
            ticks.append((var, c))
        when = tk.StringVar(value="schedule")
        ttk.Radiobutton(win, text="Post now", variable=when, value="now", padding=(8, 6, 0, 0)).pack(anchor="w")
        sched = ttk.Frame(win, padding=(8, 0))
        sched.pack(anchor="w")
        ttk.Radiobutton(sched, text="Schedule from", variable=when, value="schedule").pack(side="left")
        start = tk.StringVar(value=self._next_evening())
        ttk.Entry(sched, textvariable=start, width=17).pack(side="left", padx=4)
        ttk.Label(sched, text="one every").pack(side="left")
        every = tk.StringVar(value="12")
        ttk.Spinbox(sched, from_=1, to=72, textvariable=every, width=4).pack(side="left", padx=4)
        ttk.Label(sched, text="hours (Nairobi time)").pack(side="left")
        out = tk.Text(win, height=10, wrap="word", relief="flat", font=self.base_font,
                      highlightthickness=1, highlightbackground="#d0d5dd")
        out.pack(fill="both", expand=True, padx=8, pady=8)
        out.insert("1.0", "Press Check first to see exactly what will be posted, and when.")

        def args():
            chosen = [str(c["id"]) for v, c in ticks if v.get()]
            if not chosen:
                raise postiz.PostizError("Tick at least one account.")
            try:
                hours = float(every.get())
            except ValueError as e:
                raise postiz.PostizError("Hours must be a number.") from e
            return chosen, (start.get() if when.get() == "schedule" else None), hours

        def run(really: bool):
            try:
                chosen, first, hours = args()
            except postiz.PostizError as e:
                messagebox.showinfo(self.APP_NAME, str(e), parent=win)
                return
            if really and not messagebox.askyesno(self.APP_NAME, "Post these videos now (or schedule them) on the "
                                                  "ticked accounts? This can't be undone from here.", parent=win):
                return
            ready = self.ready_root

            def work(progress):
                progress(0.1)
                return postiz.send_manifest(ready, chosen, first, hours, dry_run=not really,
                                            log=lambda m: self.jobs.put(("status", m)))

            def done(report):
                out.delete("1.0", "end")
                out.insert("1.0", postiz.describe(report, dry_run=not really))
                self._refresh_platform_section()

            self.run_job("Sending to Postiz…" if really else "Checking what would be posted…", work, done)

        row = ttk.Frame(win, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Post", style="Accent.TButton", command=lambda: run(True)).pack(side="right")
        ttk.Button(row, text="Check", command=lambda: run(False)).pack(side="right", padx=6)
        ttk.Button(row, text="Close", command=win.destroy).pack(side="left")
        self._postiz_win = win

    @staticmethod
    def _next_evening() -> str:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(postiz.DEFAULT_TZ))
        first = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if first <= now + timedelta(minutes=10):
            first += timedelta(days=1)
        return first.strftime("%Y-%m-%d %H:%M")
