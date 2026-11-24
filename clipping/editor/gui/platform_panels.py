"""
clipping.editor.gui.platform_panels — Phase 6 for the Clip Editor window:

  Other platforms     TikTok, Instagram Reels and Facebook Reels text (follows
                      the YouTube text until you change it), Write them with Claude
  Several edits       export the day's edits one after another
  Upload list         "Ready to post/upload_manifest.json" and the commands
                      that send it to YouTube and Facebook
  My Clips…           open a Clipping Studio clip as a new edit
  Button areas        shade one platform's buttons on the player, or all of them

Mixed into EditorApp (gui/app.py); uses its helpers (commit, run_job, settings…).
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from clipping.editor import batch, importer, platforms, timeline
from clipping.editor import project as P

PLATFORM_NAMES = [(spec["label"], key) for key, spec in platforms.PLATFORMS.items()]


class PlatformPanelsMixin:
    # ============================================================ export tab additions
    def _build_platform_section(self, t):
        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Other platforms", "Follows the YouTube text until you change it. "
                                            "Saved as TikTok.txt, Instagram.txt and Facebook.txt.")
        self.plat_var = tk.StringVar(value=PLATFORM_NAMES[0][0])
        cb = ttk.Combobox(t, textvariable=self.plat_var, state="readonly", width=18,
                          values=[n for n, _ in PLATFORM_NAMES])
        cb.pack(anchor="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_platform_fields())
        self.plat_title_lbl = ttk.Label(t, text="Title", style="Card.TLabel")
        self.plat_title = tk.StringVar()
        self.plat_title_entry = ttk.Entry(t, textvariable=self.plat_title)
        self.plat_title_entry.bind("<FocusOut>", lambda e: self._platform_text_changed("title"))
        self.plat_title_entry.bind("<Return>", lambda e: self._platform_text_changed("title"))
        self.plat_body_lbl = ttk.Label(t, text="Caption", style="Card.TLabel")
        self.plat_body = tk.Text(t, height=5, wrap="word", relief="flat", font=self.base_font,
                                 highlightthickness=1, highlightbackground="#d0d5dd")
        self.plat_body.bind("<FocusOut>", lambda e: self._platform_text_changed(self._plat_body_field()))
        self.plat_body.bind("<KeyRelease>", lambda e: self._platform_counter())
        self.plat_count = tk.StringVar()
        self.plat_count_lbl = ttk.Label(t, textvariable=self.plat_count, style="Help.TLabel", wraplength=260,
                                        justify="left")
        self._plat_anchor = ttk.Frame(t, style="Card.TFrame")
        self._plat_anchor.pack(fill="x")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Follow YouTube", command=self.platform_follow_youtube).pack(side="left")
        ttk.Button(t, text="Write them with Claude", command=self.platforms_with_claude).pack(anchor="w", pady=(4, 0))

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Several edits at once", "Export today's edits one after another.")
        ttk.Button(t, text="Export several edits…", command=self.open_batch_dialog).pack(anchor="w")

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Upload list", "Every export is added to upload_manifest.json in Ready to post, "
                                        "which the YouTube and Facebook uploaders read. Both post the YouTube "
                                        "title and description; the Facebook text above is for posting by "
                                        "hand. Nothing is uploaded until you run the command.")
        self.manifest_info = tk.StringVar()
        ttk.Label(t, textvariable=self.manifest_info, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Copy YouTube command", command=lambda: self.copy_upload_command("youtube")).pack(side="left")
        ttk.Button(t, text="Copy Facebook command",
                   command=lambda: self.copy_upload_command("facebook")).pack(anchor="w", pady=(4, 0))
        self._build_postiz_section(t)

    def _plat_key(self) -> str:
        return dict(PLATFORM_NAMES).get(self.plat_var.get(), "tiktok")

    def _plat_body_field(self) -> str:
        return "description" if self._plat_key() == "facebook" else "caption"

    def _refresh_platform_fields(self):
        key = self._plat_key()
        text = platforms.text_for(self.project, key)
        for w in (self.plat_title_lbl, self.plat_title_entry, self.plat_body_lbl, self.plat_body, self.plat_count_lbl):
            w.pack_forget()
        before = self._plat_anchor
        if key == "facebook":
            self.plat_title_lbl.pack(anchor="w", pady=(6, 0), after=before)
            self.plat_title_entry.pack(fill="x", after=self.plat_title_lbl)
            before = self.plat_title_entry
            self.plat_title.set(text["title"])
        field = self._plat_body_field()
        self.plat_body_lbl.configure(text=platforms.FIELD_LABELS[field] +
                                     ("" if platforms.is_edited(self.project, key, field) else "  (from YouTube)"))
        self.plat_body_lbl.pack(anchor="w", pady=(6, 0), after=before)
        self.plat_body.pack(fill="x", after=self.plat_body_lbl)
        self.plat_count_lbl.pack(anchor="w", after=self.plat_body)
        if self.root.focus_get() is not self.plat_body:
            self.plat_body.delete("1.0", "end")
            self.plat_body.insert("1.0", text[field])
        self._platform_counter()

    def _platform_counter(self):
        key, field = self._plat_key(), self._plat_body_field()
        spec = platforms.PLATFORMS[key]
        n = len(self.plat_body.get("1.0", "end").rstrip("\n"))
        line = f"{n} / {spec['fields'][field]}"
        if spec.get("shown") and n > spec["shown"]:
            line += f": people see the first {spec['shown']} before \"more\""
        problems = platforms.limits(self.project, key)
        self.plat_count.set("  ".join([line] + problems))
        self.plat_count_lbl.configure(foreground="#c0262d" if n > spec["fields"][field] or problems else "")

    def _platform_text_changed(self, field):
        if self._loading:
            return
        key = self._plat_key()
        text = self.plat_title.get() if field == "title" else self.plat_body.get("1.0", "end").rstrip("\n")
        if platforms.set_text(self.project, key, field, text):
            self.commit(f"{platforms.PLATFORMS[key]['label']} text saved.")

    def platform_follow_youtube(self):
        platforms.follow_youtube(self.project, self._plat_key())
        self.plat_body.delete("1.0", "end")
        self.commit("This platform follows the YouTube text again.")

    def platforms_with_claude(self):
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return
        snap = P.Project.from_dict(self.project.to_dict())
        settings = dict(self.settings)

        def work(progress):
            progress(0.1)
            return platforms.write_with_claude(snap, settings)

        def done(got):
            platforms.apply_claude(self.project, got)
            self.commit("Claude wrote the TikTok, Instagram and Facebook text. Undo takes it back.")

        self.run_job("Claude is writing text for each platform…", work, done)

    def _refresh_platform_section(self):
        self._refresh_platform_fields()
        rows = batch.load_manifest(self.ready_root)
        waiting = [r for r in rows if not batch.is_uploaded(r)]
        self.manifest_info.set(f"{len(rows)} video(s) in the list, {len(waiting)} not uploaded yet."
                               if rows else "The list is empty until you export.")

    def copy_upload_command(self, which: str):
        cmd = batch.upload_commands(self.ready_root)[which]
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd)
        messagebox.showinfo(self.APP_NAME, "Copied. Paste it in a terminal in the Clipping folder:\n\n" + cmd +
                            ("\n\nThe YouTube uploader needs .credentials/youtube_token.json first "
                             "(python youtube_uploader/generate_youtube_token.py)." if which == "youtube" else
                             "\n\nThe Facebook uploader needs META_PAGE_ACCESS_TOKEN and META_PAGE_ID in .env."))

    def add_export_to_manifest(self, project, record):
        try:
            batch.add_to_manifest(self.ready_root, project, record, edit_folder=self.folder)
        except OSError as e:
            self.status(f"Exported, but the upload list couldn't be updated: {e}")
        self._refresh_platform_section()

    # ============================================================ batch export
    def open_batch_dialog(self):
        if self.busy:
            messagebox.showinfo(self.APP_NAME, "Wait for the current job to finish, or press Stop.")
            return
        self.save()
        win = tk.Toplevel(self.root)
        win.title("Export several edits")
        win.transient(self.root)
        win.geometry("560x420")
        ttk.Label(win, text="Tick the edits to export. They are made one after another.",
                  padding=8).pack(anchor="w")
        show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="Show all edits (not only today's)", variable=show_all,
                        command=lambda: fill()).pack(anchor="w", padx=8)
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        ticks: list[tuple[tk.BooleanVar, batch.EditInfo]] = []

        def fill():
            for c in inner.winfo_children():
                c.destroy()
            ticks.clear()
            edits = batch.list_edits(self.edits_root)
            if not show_all.get():
                edits = [e for e in edits if e.is_today]
            if not edits:
                ttk.Label(inner, text="No edits from today. Tick 'Show all edits'.").pack(anchor="w")
            for e in edits:
                var = tk.BooleanVar(value=e.problem is None and e.is_today)
                last = f", last export {e.last_export[:16].replace('T', ' ')}" if e.last_export else ""
                text = f"{e.title}  ({e.channel}, {timeline.format_time(e.length)}{last})"
                if e.problem:
                    text += f"  ✖ {e.problem}"
                ttk.Checkbutton(inner, text=text, variable=var,
                                state="disabled" if e.problem else "normal").pack(anchor="w", pady=1)
                ticks.append((var, e))

        def go():
            chosen = [e.folder for var, e in ticks if var.get()]
            if not chosen:
                messagebox.showinfo(self.APP_NAME, "Tick at least one edit.", parent=win)
                return
            win.destroy()
            self.export_many(chosen)

        row = ttk.Frame(win, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Export ticked", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        fill()
        self._batch_dialog = win

    def export_many(self, folders: list[str]):
        if self.folder in folders:
            self.save()
        ready, stop = self.ready_root, threading.Event()
        self._batch_stop = stop

        def work(progress):
            def each(i, n, f):
                progress((i + f) / max(1, n))
                self.jobs.put(("status", f"Exporting {min(i + 1, n)} of {n}…"))
            # Stop finishes the video being made, then stops.
            watcher = threading.Thread(target=lambda: (self.cancel.wait(), stop.set()), daemon=True)
            watcher.start()
            return batch.export_many(folders, ready, each, stop)

        def done(report):
            if self.folder in folders:   # the open edit was exported too: reload its record
                try:
                    fresh, _ = P.load(self.folder)
                    self.project.exports = fresh.exports
                    self.history.absorb(self.project)
                except Exception:
                    pass
            self._refresh_export()
            self._refresh_platform_section()
            self.status(batch.describe_report(report).splitlines()[0])
            if messagebox.askyesno(self.APP_NAME, batch.describe_report(report) + "\n\nOpen Ready to post?"):
                self.open_folder(ready)

        self.run_job(f"Exporting {len(folders)} edits…", work, done)

    # ============================================================ My Clips
    def open_my_clips_dialog(self):
        root = self.settings.get("my_clips") or importer.DEFAULT_MY_CLIPS
        runs = importer.list_runs(root)
        if not runs:
            path = filedialog.askopenfilename(
                title="Pick a clip made by Clipping Studio", initialdir=root if os.path.isdir(root) else importer.REPO_DIR,
                filetypes=[("Videos", " ".join(f"*{e}" for e in importer.VIDEO_EXTS)), ("All files", "*")])
            if path:
                self.open_clip(path)
            return
        win = tk.Toplevel(self.root)
        win.title("Open a clip from My Clips")
        win.transient(self.root)
        win.geometry("620x420")
        ttk.Label(win, text="Pick one of Clipping Studio's clips. It opens as a new edit.", padding=8).pack(anchor="w")
        tree = ttk.Treeview(win, show="tree", selectmode="browse")
        tree.pack(fill="both", expand=True, padx=8)
        for run, videos in runs:
            node = tree.insert("", "end", text=os.path.basename(run), open=runs[0][0] == run)
            for v in videos:
                tree.insert(node, "end", iid=v, text=os.path.basename(v))

        def go():
            sel = tree.selection()
            if not sel or not os.path.isfile(sel[0]):
                messagebox.showinfo(self.APP_NAME, "Pick a video (not a folder).", parent=win)
                return
            win.destroy()
            self.open_clip(sel[0])

        def other():
            path = filedialog.askdirectory(title="Where are your Clipping Studio clips?", parent=win)
            if path:
                self.settings["my_clips"] = path
                from clipping.editor.gui.app import save_settings
                save_settings(self.settings)
                win.destroy()
                self.open_my_clips_dialog()

        row = ttk.Frame(win, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Open as a new edit", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(row, text="Another folder…", command=other).pack(side="left")
        tree.bind("<Double-1>", lambda e: go())

    def open_clip(self, path: str):
        try:
            pr = importer.import_clip(path, self.project.channel)
        except Exception as e:
            messagebox.showerror(self.APP_NAME, f"Can't open that clip: {e}")
            return
        self.player.pause()
        self.project = pr
        self.apply_channel_logo()
        self.apply_template_to_new_edit()
        for seg in self.project.segments:
            seg.framing.mode = "fit_blur"   # the clip is already tall: never crop it
        self.folder = None
        self.history = P.History(self.project)
        self.current_source_id = pr.sources[0].id
        self.preview_path = None
        self.mark_in = self.mark_out = None
        self._show_marks()
        self.watch.set("source")
        self.save()
        self.refresh_all()
        self._sync_source()
        self.status(f"Opened '{pr.title}' as a new edit. {importer.BURNED_IN_NOTE}")

    # ============================================================ player button areas
    def _zones_changed(self):
        key = dict(platforms.ZONE_CHOICES).get(self.zone_var.get())
        self.player.set_zones(platforms.safe_zones(key))
        self._overlay_changed()
