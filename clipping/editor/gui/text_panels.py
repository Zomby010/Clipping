"""
clipping.editor.gui.text_panels — Phase 2 panels for the Clip Editor window:

  Captions        Make captions (speech to text on your moments), fix words,
                  split / join / delete lines, nudge timing, and the look
                  (size, position, word highlight, all caps).
  Hook and text   the big hook line for the first seconds, and pop-up text
                  with emoji at any moment.
  Export extras   cover picture, YouTube title / description / hashtags.

Mixed into EditorApp (gui/app.py); uses its helpers (commit, run_job, player…).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from clipping.editor import captions as C
from clipping.editor import publish, styles, text_layers, timeline

GRID_NAMES = (("Top left", "0,0"), ("Top", "0,1"), ("Top right", "0,2"),
              ("Left", "1,0"), ("Middle", "1,1"), ("Right", "1,2"),
              ("Low left", "2,0"), ("Low middle", "2,1"), ("Low right", "2,2"))


class ScrollPage(ttk.Frame):
    """A panel page that scrolls when it is taller than the window."""

    def __init__(self, parent, style="Card.TFrame", bg="#ffffff"):
        super().__init__(parent, style=style)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=bg)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style=style, padding=12)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _wheel(self, event):
        if not str(event.widget).startswith(str(self)) or not self.winfo_ismapped():
            return
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -int(event.delta / 40) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")


class TextPanelsMixin:
    # ============================================================ captions page
    def _build_captions_page(self, t):
        self._heading(t, "Captions", "Made from what people say in your moments.")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(2, 4))
        ttk.Button(row, text="Make captions", style="Accent.TButton", command=self.make_captions).pack(side="left")
        self.caps_info = tk.StringVar(value="No captions yet.")
        ttk.Label(t, textvariable=self.caps_info, style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w")

        self.caps_list = tk.Listbox(t, height=7, exportselection=False, relief="flat", activestyle="none",
                                    highlightthickness=1, highlightbackground="#d0d5dd",
                                    selectbackground="#e8590c", font=self.base_font)
        self.caps_list.pack(fill="x", pady=(6, 4))
        self.caps_list.bind("<<ListboxSelect>>", lambda e: self._caption_selected())
        self._caps_ids: list[str] = []

        self.cap_text = tk.StringVar()
        self.cap_entry = ttk.Entry(t, textvariable=self.cap_text)
        self.cap_entry.pack(fill="x")
        self.cap_entry.bind("<Return>", lambda e: self.save_caption_words())
        r1 = ttk.Frame(t, style="Card.TFrame")
        r1.pack(fill="x", pady=(4, 0))
        ttk.Button(r1, text="Save words", width=10, style="Small.TButton", command=self.save_caption_words).pack(side="left")
        ttk.Button(r1, text="Split at cursor", width=13, style="Small.TButton", command=self.split_caption).pack(side="left", padx=4)
        r2 = ttk.Frame(t, style="Card.TFrame")
        r2.pack(fill="x", pady=(4, 0))
        ttk.Button(r2, text="Join with next", width=13, style="Small.TButton", command=self.join_caption).pack(side="left")
        ttk.Button(r2, text="Delete", width=7, style="Small.TButton", command=self.delete_caption).pack(side="left", padx=4)
        for label, edge in (("Start", "in"), ("End", "out")):
            r3 = ttk.Frame(t, style="Card.TFrame")
            r3.pack(fill="x", pady=(4, 0))
            ttk.Label(r3, text=label, style="Card.TLabel", width=6).pack(side="left")
            ttk.Button(r3, text="−0.1 s", width=6, style="Small.TButton",
                       command=lambda e=edge: self.nudge_caption(e, -0.1)).pack(side="left", padx=2)
            ttk.Button(r3, text="+0.1 s", width=6, style="Small.TButton",
                       command=lambda e=edge: self.nudge_caption(e, 0.1)).pack(side="left")

        ttk.Separator(t).pack(fill="x", pady=10)
        self.look_title = tk.StringVar()
        ttk.Label(t, textvariable=self.look_title, style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1], "bold")).pack(anchor="w")
        ttk.Label(t, text="Each channel has its own look. Change the channel at the top.",
                  style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w")
        ttk.Label(t, text="Size", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        self.look_size = tk.DoubleVar(value=1.0)
        sz = ttk.Scale(t, from_=0.6, to=1.6, variable=self.look_size, orient="horizontal")
        sz.pack(fill="x")
        sz.bind("<ButtonRelease-1>", lambda e: self._look_changed())
        ttk.Label(t, text="Where", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        self.look_pos = tk.StringVar()
        pos = ttk.Combobox(t, textvariable=self.look_pos, state="readonly", width=16,
                           values=[styles.POSITION_LABELS[p] for p in styles.POSITIONS])
        pos.pack(anchor="w")
        pos.bind("<<ComboboxSelected>>", lambda e: self._look_changed())
        self.look_hl = tk.BooleanVar()
        self.look_caps = tk.BooleanVar()
        ttk.Checkbutton(t, text="Light up the word being said", variable=self.look_hl,
                        style="Card.TCheckbutton", command=self._look_changed).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(t, text="ALL CAPITALS", variable=self.look_caps,
                        style="Card.TCheckbutton", command=self._look_changed).pack(anchor="w")
        ttk.Button(t, text="Back to the channel look", command=self._look_reset).pack(anchor="w", pady=(8, 0))

    def _heading(self, t, title, sub=""):
        ttk.Label(t, text=title, style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1] + 2, "bold")).pack(anchor="w")
        if sub:
            ttk.Label(t, text=sub, style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w", pady=(0, 6))

    # ------------------------------------------------------------ refresh
    def _refresh_captions(self):
        C.refresh(self.project)
        shown = C.visible(self.project)
        keep = self._selected_caption_id()
        self.caps_list.delete(0, "end")
        self._caps_ids = []
        for cap in shown:
            self.caps_list.insert("end", f"{timeline.format_time(cap['start'], True)}  {cap['text']}")
            self._caps_ids.append(cap["id"])
        if keep in self._caps_ids:
            i = self._caps_ids.index(keep)
            self.caps_list.selection_set(i)
            self.caps_list.see(i)
        hidden = len(self.project.captions) - len(shown)
        if not self.project.captions:
            self.caps_info.set("No captions yet. Press Make captions.")
        else:
            info = f"{len(shown)} caption lines."
            if hidden:
                info += f" {hidden} hidden because their moment was removed."
            self.caps_info.set(info)
        st = styles.caption_style(self.project.channel, self.project.caption_style)
        base = styles.CHANNEL_STYLES.get(self.project.channel, styles.CHANNEL_STYLES["Other"])
        self.look_title.set(f"Look: {self.project.channel}")
        self.look_size.set(round(st.size / base.size, 2))
        self.look_pos.set(styles.POSITION_LABELS[st.position])
        self.look_hl.set(st.word_highlight)
        self.look_caps.set(st.all_caps)

    def _refresh_text_page(self):
        hook = self.project.hook or {}
        if self.root.focus_get() is not self.hook_entry:
            self.hook_var.set(hook.get("text", ""))
        self.hook_secs.set(str(hook.get("seconds", 2.0)))
        self.pop_list.delete(0, "end")
        self._pop_rows = []
        for o in sorted(text_layers.popups(self.project), key=lambda o: o["start"]):
            name = dict((v, k) for k, v in GRID_NAMES).get(o.get("position"), o.get("position", ""))
            self.pop_list.insert("end", f"{timeline.format_time(o['start'], True)}  {o['text']}  ({name})")
            self._pop_rows.append(o)

    def _refresh_upload(self):
        up = publish.upload_fields(self.project)
        focus = self.root.focus_get()
        if focus is not self.up_title_entry:
            self.up_title.set(up["title"])
        if focus is not self.up_tags_entry:
            self.up_tags.set(up["hashtags"])
        if focus is not self.up_desc:
            self.up_desc.delete("1.0", "end")
            self.up_desc.insert("1.0", up["description"])
        self._title_count()
        cov = self.project.cover or {}
        if focus is not self.cover_title_entry:
            self.cover_title.set(cov.get("title", ""))
        t = publish.cover_time(self.project) if self.project.segments else 0
        self.cover_info.set(f"Cover picture: the frame at {timeline.format_time(t, True)}"
                            + (" (you picked it)." if cov.get("time") is not None else " (automatic)."))
        self.kids_note.set("Kids video: when you upload, choose \"Yes, it's made for kids\"."
                           if self.project.channel == "Kids" else "")

    # ------------------------------------------------------------ captions actions
    def _selected_caption_id(self):
        sel = self.caps_list.curselection() if hasattr(self, "caps_list") else ()
        if sel and sel[0] < len(self._caps_ids):
            return self._caps_ids[sel[0]]
        return None

    def _caption_selected(self):
        cid = self._selected_caption_id()
        if not cid:
            return
        cap = C.find(self.project, cid)
        self.cap_text.set(cap["text"])
        if self.watch.get() == "cut" and self.player.path == self.preview_path:
            self.player.seek(cap["start"] + 0.01)

    def make_captions(self):
        if not self.project.segments:
            messagebox.showinfo(self.APP_NAME, "Add some moments to the cut list first.")
            return
        if self.busy:
            messagebox.showinfo(self.APP_NAME, "Wait for the current job to finish, or press Stop.")
            return
        if self.project.captions and not messagebox.askyesno(
                self.APP_NAME, "Make new captions? This replaces the captions you have now (Undo brings them back)."):
            return
        snapshot = self.project.__class__.from_dict(self.project.to_dict())
        cache = self._transcript_dir()
        st = styles.caption_style(self.project.channel, self.project.caption_style)
        if getattr(self, "_transcriber", None) is None:
            self._transcriber = C.WhisperTranscriber(self.settings.get("whisper_model", "small"))
        transcriber = self._transcriber
        import time

        def work(progress):
            started = time.monotonic()
            progress(0.02)
            caps = C.transcribe_segments(snapshot, transcriber, cache, st.max_words, progress, self.cancel)
            return caps, round(time.monotonic() - started, 1)

        def done(result):
            caps, took = result
            self.project.captions = caps
            self.commit(f"Made {len(caps)} caption lines in {took} s. Click a line to fix it.")
            self.show_page("Captions")

        self.run_job("Listening to the speech in your moments (the first time downloads the speech model)…",
                     work, done)

    def _transcript_dir(self):
        import os
        return os.path.join(self.ensure_folder(), "Transcripts")

    def _with_caption(self, fn, note=None):
        cid = self._selected_caption_id()
        if not cid:
            self.status("Pick a caption line first.")
            return
        try:
            result = fn(cid)
        except (ValueError, KeyError) as e:
            self.status(str(e))
            return
        self.commit(note)
        return result

    def save_caption_words(self):
        self._with_caption(lambda cid: C.set_text(C.find(self.project, cid), self.cap_text.get()), "Caption saved.")

    def split_caption(self):
        def do(cid):
            cap = C.find(self.project, cid)
            if self.cap_text.get().split() != cap["text"].split():
                C.set_text(cap, self.cap_text.get())
            return C.split(self.project, cid, C.word_index_at(cap, self.cap_entry.index("insert")))
        self._with_caption(do, "Line split in two.")

    def join_caption(self):
        self._with_caption(lambda cid: C.join_next(self.project, cid), "Joined with the next line.")

    def delete_caption(self):
        self._with_caption(lambda cid: C.delete(self.project, cid), "Caption deleted. Undo brings it back.")

    def nudge_caption(self, edge, delta):
        self._with_caption(lambda cid: C.nudge(self.project, cid, edge, delta))

    def _look_changed(self):
        if self._loading:
            return
        labels = {v: k for k, v in styles.POSITION_LABELS.items()}
        self.project.caption_style = {
            "size_scale": round(self.look_size.get(), 2),
            "position": labels.get(self.look_pos.get(), "lower"),
            "word_highlight": bool(self.look_hl.get()),
            "all_caps": bool(self.look_caps.get()),
        }
        self.commit()

    def _look_reset(self):
        self.project.caption_style = {}
        self.commit("Back to the channel look.")

    # ============================================================ hook and text page
    def _build_text_page(self, t):
        self._heading(t, "Hook line", "Big text for the first seconds, to stop people scrolling.")
        self.hook_var = tk.StringVar()
        self.hook_entry = ttk.Entry(t, textvariable=self.hook_var)
        self.hook_entry.pack(fill="x")
        self.hook_entry.bind("<Return>", lambda e: self._hook_changed())
        self.hook_entry.bind("<FocusOut>", lambda e: self._hook_changed())
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Seconds on screen", style="Card.TLabel").pack(side="left")
        self.hook_secs = tk.StringVar(value="2.0")
        sp = ttk.Spinbox(row, from_=1.0, to=4.0, increment=0.5, width=5, textvariable=self.hook_secs,
                         command=self._hook_changed)
        sp.pack(side="left", padx=6)
        sp.bind("<FocusOut>", lambda e: self._hook_changed())
        self._emoji_row(t, lambda e: self._insert_emoji(self.hook_entry, e, self._hook_changed))

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Pop-up text", "Short text or emoji at the player's current time in your cut.")
        self.pop_var = tk.StringVar()
        self.pop_entry = ttk.Entry(t, textvariable=self.pop_var)
        self.pop_entry.pack(fill="x")
        self._emoji_row(t, lambda e: self._insert_emoji(self.pop_entry, e))
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Seconds", style="Card.TLabel").pack(side="left")
        self.pop_secs = tk.StringVar(value="1.5")
        ttk.Spinbox(row, from_=0.5, to=5.0, increment=0.5, width=5, textvariable=self.pop_secs).pack(side="left", padx=6)
        self.pop_pos = tk.StringVar(value="Middle")
        ttk.Combobox(row, textvariable=self.pop_pos, state="readonly", width=11,
                     values=[n for n, _ in GRID_NAMES]).pack(side="left")
        ttk.Button(t, text="Add pop-up here", command=self.add_popup).pack(anchor="w", pady=(6, 4))
        self.pop_list = tk.Listbox(t, height=5, exportselection=False, relief="flat", activestyle="none",
                                   highlightthickness=1, highlightbackground="#d0d5dd",
                                   selectbackground="#e8590c", font=self.base_font)
        self.pop_list.pack(fill="x")
        self.pop_list.bind("<<ListboxSelect>>", lambda e: self._popup_selected())
        self._pop_rows: list[dict] = []
        ttk.Button(t, text="Delete pop-up", command=self.delete_popup).pack(anchor="w", pady=(4, 0))
        self.emoji_note = tk.StringVar(
            value="" if styles.emoji_font() else "Emoji show in the video once you run: "
                                                  "sudo apt install fonts-noto-color-emoji")
        ttk.Label(t, textvariable=self.emoji_note, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(6, 0))

    def _emoji_row(self, t, on_pick):
        """An "Add emoji" button with a menu of common reactions."""
        mb = ttk.Menubutton(t, text="Add emoji ▾")
        menu = tk.Menu(mb, tearoff=False, font=(self.base_font[0], self.base_font[1] + 4))
        for n, e in enumerate(text_layers.COMMON_EMOJI):
            menu.add_command(label=e, command=lambda e=e: on_pick(e), columnbreak=(n > 0 and n % 5 == 0))
        mb["menu"] = menu
        mb.pack(anchor="w", pady=(4, 0))

    def _insert_emoji(self, entry, emoji, then=None):
        entry.insert("insert", emoji)
        entry.focus_set()
        if then:
            then()

    def _hook_changed(self):
        if self._loading:
            return
        text = self.hook_var.get().strip()
        try:
            secs = min(4.0, max(1.0, float(self.hook_secs.get())))
        except ValueError:
            secs = 2.0
        new = {"text": text, "seconds": secs} if text else {}
        if new != (self.project.hook or {}):
            self.project.hook = new
            self.commit("Hook line saved." if text else "Hook line removed.")

    def _cut_time(self) -> float | None:
        if self.watch.get() == "cut" and self.player.path == self.preview_path:
            return self.player.time()
        return None

    def add_popup(self):
        text = self.pop_var.get().strip()
        if not text:
            self.status("Type some text or pick an emoji first.")
            return
        t = self._cut_time()
        if t is None:
            self.status("Watch 'My cut' and move the player to where the pop-up should appear.")
            return
        try:
            secs = min(5.0, max(0.5, float(self.pop_secs.get())))
        except ValueError:
            secs = 1.5
        pos = dict(GRID_NAMES).get(self.pop_pos.get(), "1,1")
        end = min(self.project.total_length, t + secs)
        import uuid
        self.project.overlays.append({"id": f"pop_{uuid.uuid4().hex[:8]}", "type": "text",
                                      "start": round(t, 2), "end": round(end, 2),
                                      "text": text, "position": pos})
        self.pop_var.set("")
        self.commit(f"Pop-up added at {timeline.format_time(t, True)}.")

    def _popup_selected(self):
        sel = self.pop_list.curselection()
        if sel and self.watch.get() == "cut" and self.player.path == self.preview_path:
            self.player.seek(self._pop_rows[sel[0]]["start"] + 0.01)

    def delete_popup(self):
        sel = self.pop_list.curselection()
        if not sel:
            self.status("Pick a pop-up in the list first.")
            return
        self.project.overlays.remove(self._pop_rows[sel[0]])
        self.commit("Pop-up deleted.")

    # ============================================================ export extras
    def _build_upload_section(self, t):
        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Cover picture")
        self.cover_info = tk.StringVar()
        ttk.Label(t, textvariable=self.cover_info, style="Help.TLabel", wraplength=260, justify="left").pack(anchor="w")
        ttk.Button(t, text="Use this frame as the cover", command=self.pick_cover).pack(anchor="w", pady=(4, 4))
        ttk.Label(t, text="Text on the cover (optional)", style="Card.TLabel").pack(anchor="w")
        self.cover_title = tk.StringVar()
        self.cover_title_entry = ttk.Entry(t, textvariable=self.cover_title)
        self.cover_title_entry.pack(fill="x")
        for ev in ("<FocusOut>", "<Return>"):
            self.cover_title_entry.bind(ev, lambda e: self._cover_title_changed())

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "YouTube upload text", "Saved as YouTube.txt next to the video.")
        top = ttk.Frame(t, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Title", style="Card.TLabel").pack(side="left")
        self.title_count = tk.StringVar()
        self.title_count_lbl = ttk.Label(top, textvariable=self.title_count, style="Help.TLabel")
        self.title_count_lbl.pack(side="right")
        self.up_title = tk.StringVar()
        self.up_title_entry = ttk.Entry(t, textvariable=self.up_title)
        self.up_title_entry.pack(fill="x")
        self.up_title.trace_add("write", lambda *a: self._title_count())
        ttk.Label(t, text="Description", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        self.up_desc = tk.Text(t, height=4, wrap="word", relief="flat", font=self.base_font,
                               highlightthickness=1, highlightbackground="#d0d5dd")
        self.up_desc.pack(fill="x")
        ttk.Label(t, text="Hashtags", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        self.up_tags = tk.StringVar()
        self.up_tags_entry = ttk.Entry(t, textvariable=self.up_tags)
        self.up_tags_entry.pack(fill="x")
        for w in (self.up_title_entry, self.up_desc, self.up_tags_entry):
            w.bind("<FocusOut>", lambda e: self._upload_changed())
        self.kids_note = tk.StringVar()
        ttk.Label(t, textvariable=self.kids_note, style="Help.TLabel", wraplength=260,
                  justify="left", foreground="#c0262d").pack(anchor="w", pady=(6, 0))

    def _title_count(self):
        n = len(self.up_title.get())
        self.title_count.set(f"{n} / {publish.TITLE_LIMIT}" + ("  too long" if n > publish.TITLE_LIMIT else ""))

    def _upload_changed(self):
        if self._loading:
            return
        new = {"title": self.up_title.get().strip()[:publish.TITLE_LIMIT],
               "description": self.up_desc.get("1.0", "end").strip(),
               "hashtags": " ".join(self.up_tags.get().split())}
        current = publish.upload_fields(self.project)
        if any(new[k] != current.get(k) for k in new) or not self.project.upload:
            # keep the other keys (description footer, other platforms' text)
            self.project.upload = {**(self.project.upload or {}), **new}
            self.commit()

    def pick_cover(self):
        t = self._cut_time()
        if t is None:
            self.status("Watch 'My cut', move the player to the frame you want, then press this.")
            return
        self.project.cover = {**(self.project.cover or {}), "time": round(t, 2)}
        self.commit(f"Cover picture: the frame at {timeline.format_time(t, True)}.")

    def _cover_title_changed(self):
        if self._loading:
            return
        title = self.cover_title.get().strip()
        if title != (self.project.cover or {}).get("title", ""):
            self.project.cover = {**(self.project.cover or {}), "title": title}
            self.commit()
