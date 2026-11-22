"""
clipping.editor.gui.review_panels — Phase 5 "Check" tab for the Clip Editor:

  Check my edit         the app's own checks, each with "Fix it" when it can
  Ask Claude to check   Claude's suggestions with tick boxes, "Apply ticked"
  Suggest a cut         Claude trims the cut to 15 / 30 / 45 / 60 s (preview first)
  Channel template      save this edit's look for its channel, or use it

Mixed into EditorApp (gui/app.py); uses its helpers (commit, run_job, settings…).
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from clipping.editor import review as R
from clipping.editor import templates as T
from clipping.editor import timeline

KIND_LABELS = {"caption": "Caption", "hook": "Hook", "popup": "Pop-up", "title": "Title",
               "description": "Description", "hashtags": "Hashtags", "note": "Note"}


class ReviewPanelsMixin:
    _findings: list = []
    _cut_parts: list | None = None

    def _build_check_page(self, t):
        self._heading(t, "Check before posting", "The app looks for anything that could hurt the video.")
        ttk.Button(t, text="Check my edit", style="Accent.TButton", command=self.run_checks).pack(anchor="w")
        self.find_list = tk.Listbox(t, height=6, exportselection=False, relief="flat", activestyle="none",
                                    highlightthickness=1, highlightbackground="#d0d5dd",
                                    selectbackground="#e8590c", font=self.base_font)
        self.find_list.pack(fill="x", pady=(6, 0))
        self.find_list.bind("<<ListboxSelect>>", lambda e: self._finding_selected())
        self.find_text = tk.StringVar(value="Press Check my edit.")
        ttk.Label(t, textvariable=self.find_text, style="Card.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(4, 0))
        self.fix_btn = ttk.Button(t, text="Fix it", command=self.fix_selected, state="disabled")
        self.fix_btn.pack(anchor="w", pady=(4, 0))

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Ask Claude to check", "Spelling, a stronger hook, a clearer title and hashtags. "
                                                "Tick the ones you like.")
        ttk.Button(t, text="Ask Claude to check", command=self.ask_claude_review).pack(anchor="w")
        self.sugg_frame = ttk.Frame(t, style="Card.TFrame")
        self.sugg_frame.pack(fill="x", pady=(6, 0))
        self._sugg_vars: list[tk.BooleanVar] = []
        self.apply_sugg_btn = ttk.Button(t, text="Apply ticked", command=self.apply_ticked, state="disabled")
        self.apply_sugg_btn.pack(anchor="w", pady=(4, 0))

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Suggest a cut", "Claude keeps the strongest parts of your moments.")
        row = ttk.Frame(t, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Make it about", style="Card.TLabel").pack(side="left")
        self.cut_target = tk.StringVar(value="30 s")
        ttk.Combobox(row, textvariable=self.cut_target, state="readonly", width=6,
                     values=[f"{n} s" for n in R.CUT_TARGETS]).pack(side="left", padx=6)
        ttk.Button(t, text="Suggest a cut", command=self.ask_cut).pack(anchor="w", pady=(4, 0))
        self.cut_text = tk.StringVar()
        ttk.Label(t, textvariable=self.cut_text, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(4, 0))
        self.apply_cut_btn = ttk.Button(t, text="Apply this cut", command=self.apply_suggested_cut,
                                        state="disabled")
        self.apply_cut_btn.pack(anchor="w", pady=(4, 0))

        ttk.Separator(t).pack(fill="x", pady=10)
        self._heading(t, "Channel template", "Caption look, hook length, music, logo, hashtags and the "
                                             "description footer, reused on every new edit of this channel.")
        ttk.Label(t, text="Description footer", style="Card.TLabel").pack(anchor="w")
        ttk.Label(t, text="Added under every description, e.g. \"Follow for part 2\".", style="Help.TLabel",
                  wraplength=260, justify="left").pack(anchor="w")
        self.footer_var = tk.StringVar()
        e = ttk.Entry(t, textvariable=self.footer_var)
        e.pack(fill="x")
        e.bind("<Return>", lambda ev: self._footer_changed())
        e.bind("<FocusOut>", lambda ev: self._footer_changed())
        self.tpl_save_btn = ttk.Button(t, text="Save as the channel template", command=self.save_template)
        self.tpl_save_btn.pack(anchor="w", pady=(6, 0))
        ttk.Button(t, text="Use the channel template", command=self.use_template).pack(anchor="w", pady=(4, 0))
        self.tpl_info = tk.StringVar()
        ttk.Label(t, textvariable=self.tpl_info, style="Help.TLabel", wraplength=260,
                  justify="left").pack(anchor="w", pady=(4, 0))

    # ============================================================ local checks
    def run_checks(self, quiet: bool = False) -> list:
        self._findings = R.checks(self.project)
        self.find_list.delete(0, "end")
        for f in self._findings:
            self.find_list.insert("end", f"{f.icon} {f.text}")
        if not self._findings:
            self.find_list.insert("end", "✔ Nothing to fix. Ready to post.")
        problems = len(R.problems_only(self._findings))
        warnings = sum(1 for f in self._findings if f.level == R.WARNING)
        self.find_text.set(f"{problems} problem(s), {warnings} warning(s). Click one to read it all."
                           if self._findings else "")
        self.fix_btn.configure(state="disabled", text="Fix it")
        if not quiet:
            self.show_page("Check")
        return self._findings

    def _finding_selected(self):
        sel = self.find_list.curselection()
        if not sel or sel[0] >= len(self._findings):
            return
        f = self._findings[sel[0]]
        self.find_text.set(f.text)
        if f.fix:
            self.fix_btn.configure(state="normal", text=f"Fix it: {f.fix_label}")
        else:
            self.fix_btn.configure(state="disabled", text="Fix it")

    def fix_selected(self):
        sel = self.find_list.curselection()
        if not sel or sel[0] >= len(self._findings):
            return
        f = self._findings[sel[0]]
        if R.apply_fix(self.project, f):
            self.commit(f"Fixed: {f.fix_label}.")
        self.run_checks(quiet=True)

    def confirm_checks_before_export(self) -> bool:
        problems = R.problems_only(self.run_checks(quiet=True))
        if not problems:
            return True
        text = "\n".join(f"• {p.text}" for p in problems[:5])
        return messagebox.askyesno(self.APP_NAME, f"Check before posting found problems:\n\n{text}\n\nExport anyway?")

    # ============================================================ Claude review
    def ask_claude_review(self):
        if not self.project.segments:
            self.status("Add some moments first.")
            return
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return
        snap = self.project.__class__.from_dict(self.project.to_dict())
        settings, root = dict(self.settings), self.edits_root

        def work(progress):
            progress(0.1)
            return R.claude_review(snap, settings, root)

        def done(suggestions):
            self.project.review = {"suggestions": suggestions, "at": datetime.now().isoformat(timespec="seconds")}
            self.history.absorb(self.project)
            self.save()
            self._show_suggestions()
            self.status(f"Claude made {len(suggestions)} suggestion(s). Untick any you don't want, then Apply ticked."
                        if suggestions else "Claude has nothing to change.")

        self.run_job("Claude is reading your edit…", work, done)

    def _show_suggestions(self):
        for child in self.sugg_frame.winfo_children():
            child.destroy()
        self._sugg_vars = []
        suggestions = (self.project.review or {}).get("suggestions") or []
        for s in suggestions:
            var = tk.BooleanVar(value=bool(s.get("tick")))
            self._sugg_vars.append(var)
            label = KIND_LABELS.get(s["kind"], s["kind"])
            if s["kind"] == "note":
                text = f"💡 {s['why'] or s['old']}"
                ttk.Label(self.sugg_frame, text=text, style="Card.TLabel", wraplength=255,
                          justify="left").pack(anchor="w", pady=1)
                continue
            old = f"\"{s['old'][:40]}\" → " if s["old"] else ""
            row = ttk.Frame(self.sugg_frame, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Checkbutton(row, variable=var, style="Card.TCheckbutton",
                            command=self._ticks_changed).pack(side="left", anchor="n")
            ttk.Label(row, text=f"{label}: {old}\"{s['new'][:90]}\"\n{s['why']}", style="Card.TLabel",
                      wraplength=230, justify="left").pack(side="left", fill="x")
        has = any(s["kind"] != "note" for s in suggestions)
        self.apply_sugg_btn.configure(state="normal" if has else "disabled")

    def _ticks_changed(self):
        suggestions = (self.project.review or {}).get("suggestions") or []
        for s, var in zip(suggestions, self._sugg_vars):
            s["tick"] = bool(var.get())

    def apply_ticked(self):
        self._ticks_changed()
        suggestions = (self.project.review or {}).get("suggestions") or []
        n = R.apply_suggestions(self.project, suggestions)
        for s in suggestions:
            if s.get("tick"):
                s["applied"] = True
        self.project.review["suggestions"] = [s for s in suggestions if not s.get("applied")]
        self.commit(f"Applied {n} suggestion(s). Undo takes them all back.")
        self._show_suggestions()

    # ============================================================ suggested cut
    def ask_cut(self):
        if not self.project.segments:
            self.status("Add some moments first.")
            return
        if self.busy:
            self.status("Wait for the current job to finish, or press Stop.")
            return
        target = int(self.cut_target.get().split()[0])
        if self.project.total_length <= target + 1:
            self.status(f"Your cut is already {timeline.format_time(self.project.total_length)}, "
                        f"no longer than {target} s.")
            return
        snap = self.project.__class__.from_dict(self.project.to_dict())
        settings = dict(self.settings)

        def work(progress):
            progress(0.1)
            return target, R.suggest_cut(snap, target, settings)

        def done(result):
            target_, parts = result
            self._cut_parts = parts
            prev = R.describe_cut(self.project, parts)
            self.cut_text.set(
                f"Now {timeline.format_time(prev.before_total, True)}:\n  " + "\n  ".join(prev.before) +
                f"\n\nSuggested {timeline.format_time(prev.after_total, True)}:\n  " + "\n  ".join(prev.after) +
                "\n\n" + "\n".join(f"{i}. {p['why']}" for i, p in enumerate(parts, 1) if p.get("why")))
            self.apply_cut_btn.configure(state="normal")
            self.status(f"Claude suggests a {round(prev.after_total)} s cut. Check it, then Apply this cut.")

        self.run_job(f"Claude is picking the best {target} s…", work, done)

    def apply_suggested_cut(self):
        if not self._cut_parts:
            return
        ids = {s.id for s in self.project.segments}
        if any(p["segment_id"] not in ids for p in self._cut_parts):
            self.status("The cut list changed since Claude's suggestion. Press Suggest again.")
            return
        R.apply_cut(self.project, self._cut_parts)
        self._cut_parts = None
        self.apply_cut_btn.configure(state="disabled")
        self.cut_text.set("")
        self.commit("New cut applied. Undo brings the old one back.")

    # ============================================================ templates
    def _footer_changed(self):
        if self._loading:
            return
        text = self.footer_var.get().strip()
        up = self.project.upload if isinstance(self.project.upload, dict) else {}
        if text != up.get("description_footer", ""):
            up["description_footer"] = text
            self.project.upload = up
            self.commit("Description footer saved.")

    def save_template(self):
        try:
            path = T.save(self.project, self.edits_root)
        except OSError as e:
            self.status(f"Could not save the template: {e}")
            return
        self._refresh_check_page()
        self.status(f"Saved as the {self.project.channel} template ({path}).")

    def use_template(self):
        tpl = T.load(self.edits_root, self.project.channel)
        if not tpl:
            self.status(f"No {self.project.channel} template yet. Make one edit look right, then save it.")
            return
        T.apply(self.project, tpl)
        self.commit(f"Used the {self.project.channel} template.")

    def apply_template_to_new_edit(self):
        tpl = T.load(self.edits_root, self.project.channel)
        if tpl:
            T.apply(self.project, tpl, new_edit=True)

    def _refresh_check_page(self):
        self.footer_var.set((self.project.upload or {}).get("description_footer", ""))
        self.tpl_save_btn.configure(text=f"Save as the {self.project.channel} template")
        has = T.load(self.edits_root, self.project.channel) is not None
        self.tpl_info.set(f"There is a {self.project.channel} template; new {self.project.channel} edits start from it."
                          if has else f"No {self.project.channel} template yet.")
        if len(self._sugg_vars) != len((self.project.review or {}).get("suggestions") or []):
            self._show_suggestions()
