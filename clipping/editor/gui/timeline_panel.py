"""
clipping.editor.gui.timeline_panel — The timeline at the bottom of the window.

Phase A makes it hands-on:
  - drag a moment's left or right edge to trim it (the moments after it close up)
  - drag a moment's middle to move it somewhere else in the cut
  - zoom with Ctrl + mouse wheel or the − / + / Fit buttons; scroll with the
    wheel (or the bar under it) when zoomed in
  - edges snap to the playhead and to the other moments' edges; hold Shift
    while dragging to stop snapping
Every drag is one Undo step. A click without dragging works as before
(selects the moment and moves the player there). Esc while dragging cancels.

Mixed into EditorApp (gui/app.py).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from clipping.editor import captions as C
from clipping.editor import script as S_script
from clipping.editor import timeline

LANES = ("Original video", "Moments", "Voice-over", "Music", "SFX", "Reactions", "Captions")
LANE_LABEL_W, LANE_H, LANE_TOP = 112, 14, 3
EDGE_PX = 6        # how near an edge the mouse must be to trim instead of move
DRAG_PX = 4        # movement before a press counts as a drag


class TimelineMixin:
    # ================================================================ build
    def _build_timeline(self, outer, colors):
        self.tl_view = timeline.View()
        self._drag = None
        self.snap_var = tk.BooleanVar(value=True)
        head = ttk.Frame(outer, style="Page.TFrame")
        ttk.Label(head, text="Timeline", style="Card.TLabel",
                  font=(self.base_font[0], self.base_font[1], "bold")).pack(side="left")
        ttk.Label(head, text="   Drag edges to trim, the middle to move   Space = play   I / O = mark",
                  style="Help.TLabel").pack(side="left")
        bottom = ttk.LabelFrame(outer, labelwidget=head, style="Card.TLabelframe", padding=(6, 2, 6, 4))
        bottom.pack(side="bottom", fill="x", pady=(8, 0))
        tools = ttk.Frame(bottom, style="Card.TFrame")
        tools.pack(fill="x")
        for text, cmd in (("−", lambda: self.zoom_timeline(1 / 1.6)), ("+", lambda: self.zoom_timeline(1.6)),
                          ("Fit", self.fit_timeline)):
            ttk.Button(tools, text=text, width=0, style="Small.TButton", command=cmd).pack(side="left", padx=(0, 2))
        ttk.Checkbutton(tools, text="Snap", variable=self.snap_var, style="Card.TCheckbutton").pack(side="left", padx=6)
        ttk.Label(tools, text="Ctrl + wheel zooms, Shift while dragging = no snap", style="Help.TLabel").pack(side="left")
        self.tl_info = tk.StringVar()
        ttk.Label(tools, textvariable=self.tl_info, style="Help.TLabel").pack(side="left", padx=6)
        self.timeline = tk.Canvas(bottom, height=LANE_TOP + len(LANES) * (LANE_H + 2) + 2,
                                  background=colors["card"], highlightthickness=0)
        self.timeline.pack(fill="x")
        self.tl_scroll = ttk.Scrollbar(bottom, orient="horizontal", command=self._tl_scrolled)
        self.tl_scroll.pack(fill="x")
        c = self.timeline
        c.bind("<Configure>", lambda e: self.draw_timeline())
        c.bind("<ButtonPress-1>", self._tl_press)
        c.bind("<B1-Motion>", self._tl_motion)
        c.bind("<ButtonRelease-1>", self._tl_release)
        c.bind("<Motion>", self._tl_hover)
        c.bind("<Control-Button-4>", lambda e: self._tl_wheel(e, zoom=1))
        c.bind("<Control-Button-5>", lambda e: self._tl_wheel(e, zoom=-1))
        c.bind("<Button-4>", lambda e: self._tl_wheel(e, scroll=-1))
        c.bind("<Button-5>", lambda e: self._tl_wheel(e, scroll=1))
        c.bind("<Control-MouseWheel>", lambda e: self._tl_wheel(e, zoom=1 if e.delta > 0 else -1))
        c.bind("<MouseWheel>", lambda e: self._tl_wheel(e, scroll=-1 if e.delta > 0 else 1))
        self.root.bind_all("<Escape>", lambda e: (self._tl_cancel(), self._place_escape()), add="+")

    # ================================================================ geometry
    def _tl_area(self) -> tuple[float, float]:
        """(left edge, width) of the part of the canvas that shows time."""
        w = max(200, self.timeline.winfo_width())
        return LANE_LABEL_W, max(1.0, w - 8 - LANE_LABEL_W)

    def tl_x(self, t: float) -> float:
        left, width = self._tl_area()
        return self.tl_view.x_of(t, self.project.total_length, left, width)

    def tl_t(self, x: float) -> float:
        left, width = self._tl_area()
        return self.tl_view.t_of(x, self.project.total_length, left, width)

    def _tl_row(self, y: float) -> int:
        return int((y - LANE_TOP) // (LANE_H + 2))

    def timeline_time_at_root(self, x_root: int, y_root: int) -> float | None:
        """Edit time under a screen point, or None if it is not over the timeline's lanes."""
        c = self.timeline
        x, y = x_root - c.winfo_rootx(), y_root - c.winfo_rooty()
        if not (LANE_LABEL_W <= x <= c.winfo_width() and 0 <= y <= c.winfo_height()):
            return None
        total = self.project.total_length
        return min(max(0.0, self.tl_t(x)), total) if total > 0 else 0.0

    # ================================================================ zoom
    def zoom_timeline(self, factor: float, around: float | None = None):
        total = self.project.total_length
        if total <= 0:
            return
        if around is None:
            around = self._playhead_time()
            if around is None:
                around = self.tl_view.start + self.tl_view.span(total) / 2
        self.tl_view.zoom_at(factor, around, total)
        self.draw_timeline()

    def fit_timeline(self):
        self.tl_view.zoom, self.tl_view.start = 1.0, 0.0
        self.draw_timeline()

    def _tl_wheel(self, event, zoom: int = 0, scroll: int = 0):
        total = self.project.total_length
        if total <= 0:
            return "break"
        if zoom:
            self.zoom_timeline(1.25 if zoom > 0 else 0.8, around=self.tl_t(event.x))
        elif self.tl_view.zoom > 1.0:
            self.tl_view.start += scroll * self.tl_view.span(total) * 0.15
            self.tl_view.clamp(total)
            self.draw_timeline()
        return "break"

    def _tl_scrolled(self, *args):
        total = self.project.total_length
        if total <= 0:
            return
        span = self.tl_view.span(total)
        if args[0] == "moveto":
            self.tl_view.start = float(args[1]) * total
        elif args[0] == "scroll":
            step = span * (0.9 if args[2] == "pages" else 0.1)
            self.tl_view.start += int(args[1]) * step
        self.tl_view.clamp(total)
        self.draw_timeline()

    def _playhead_time(self) -> float | None:
        if self.watch.get() == "cut" and self.player.path and self.player.path == self.preview_path:
            return self.player.time()
        return None

    # ================================================================ drawing
    def draw_timeline(self):
        c = self.timeline
        c.delete("all")
        colors = self.COLORS
        w = max(200, c.winfo_width())
        total = self.project.total_length
        self.tl_view.clamp(total)
        if total > 0 and self.player.playing and self._drag is None:
            head = self._playhead_time()
            if head is not None:
                self.tl_view.show(head, total)
        for row, name in enumerate(LANES):
            y = LANE_TOP + row * (LANE_H + 2)
            c.create_rectangle(LANE_LABEL_W, y, w - 8, y + LANE_H, fill=colors["lane"], outline="")
        left, span = self._tl_area()
        sel = self.selected_index()
        self._draw_original_lane(c, span, sel)
        if total > 0:
            self._draw_edit_lanes(c, total, sel)
            self._draw_drag(c, total)
        # labels last, over anything scrolled off to the left
        c.create_rectangle(0, 0, LANE_LABEL_W - 1, c.winfo_height(), fill=colors["card"], outline="")
        for row, name in enumerate(LANES):
            y = LANE_TOP + row * (LANE_H + 2)
            c.create_text(8, y + LANE_H / 2, text=name, anchor="w", fill=colors["muted"],
                          font=(self.base_font[0], 9))
        if total > 0:
            first = self.tl_view.start / total
            self.tl_scroll.set(first, min(1.0, first + 1.0 / self.tl_view.zoom))
            self.tl_info.set("" if self.tl_view.zoom <= 1.0 else f"Zoom {self.tl_view.zoom:.1f}x")
        else:
            self.tl_scroll.set(0, 1)

    def _draw_edit_lanes(self, c, total: float, sel):
        colors = self.COLORS
        x_of = self.tl_x
        moments_y = LANE_TOP + LANES.index("Moments") * (LANE_H + 2)
        dragging = self._drag["index"] if self._drag and self._drag.get("moved") else None
        for p in timeline.layout(self.project.segments):
            x0, x1 = x_of(p.edit_start), x_of(p.edit_end)
            chosen = p.index == sel
            react = self.project.segments[p.index].reaction
            c.create_rectangle(x0 + 1, moments_y + 1, x1 - 1, moments_y + LANE_H - 1,
                               fill=colors["react" if react else "seg"],
                               outline=colors["react_line"] if react else colors["seg_sel" if chosen else "seg_line"],
                               width=3 if chosen else 1, stipple="gray50" if p.index == dragging else "",
                               tags=(f"seg{p.index}",))
            if x1 - x0 > 18:
                c.create_text((x0 + x1) / 2, moments_y + LANE_H / 2, text=str(p.index + 1), fill=colors["text"],
                              font=(self.base_font[0], 9, "bold"))
        # --- phase 3: Voice-over lane, one block per script line ---
        vy = LANE_TOP + LANES.index("Voice-over") * (LANE_H + 2)
        for ln in S_script.visible(self.project):
            x0, x1 = x_of(ln["start"]), x_of(min(total, ln["end"]))
            made = bool(ln.get("audio"))
            c.create_rectangle(x0 + 1, vy + 1, max(x0 + 4, x1 - 1), vy + LANE_H - 1,
                               fill="#d3f9d8" if made else "#fff3bf", outline="#2f9e44" if made else "#e67700",
                               tags=(f"line_{ln['id']}",))
        self.draw_sound_lanes(c, x_of, LANE_H, LANE_TOP, total)
        cy = LANE_TOP + LANES.index("Captions") * (LANE_H + 2)
        for cap in C.visible(self.project):
            x0, x1 = x_of(cap["start"]), x_of(cap["end"])
            c.create_rectangle(x0 + 1, cy + 2, max(x0 + 3, x1 - 1), cy + LANE_H - 2, fill="#d9e2f1", outline="#7a8db3")
        hook = self.project.hook or {}
        extras = [(0.0, float(hook.get("seconds", 2.0)))] if hook.get("text") else []
        extras += [(o["start"], o["end"]) for o in self.project.overlays
                   if o.get("type") == "text" and o.get("visible", True)]
        for a, b in extras:
            x0, x1 = x_of(a), x_of(min(b, total))
            c.create_rectangle(x0 + 1, cy + 4, max(x0 + 3, x1 - 1), cy + LANE_H - 4, fill="#ffd8a8", outline="#e8590c")
        drop = getattr(self, "_drop_time", None)   # a reaction being dragged in from the picker
        if drop is not None:
            x = x_of(drop)
            c.create_line(x, moments_y - 2, x, moments_y + LANE_H + 2, fill="#7048e8", width=4)
        head = self._playhead_time()
        if head is not None:
            x = x_of(min(head, total))
            c.create_line(x, moments_y, x, LANE_TOP + len(LANES) * (LANE_H + 2), fill=colors["accent"], width=2,
                          tags=("playhead",))

    def _draw_original_lane(self, c, span, sel):
        """The whole video you're cutting from, with the parts your moments use (not zoomed)."""
        src = self.project.source(self.current_source_id) if self.current_source_id else None
        if src is None or not src.duration:
            return
        colors = self.COLORS
        y = LANE_TOP + LANES.index("Original video") * (LANE_H + 2)

        def x_of(t):
            return LANE_LABEL_W + min(1.0, max(0.0, t / src.duration)) * span

        for i, seg in enumerate(self.project.segments):
            if seg.source_id != src.id:
                continue
            x0, x1 = x_of(seg.source_in), x_of(seg.source_out)
            c.create_rectangle(x0, y + 1, max(x0 + 3, x1), y + LANE_H - 1, fill=colors["seg"],
                               outline=colors["seg_line"], width=3 if i == sel else 1, tags=("original",))
            if x1 - x0 > 18:
                c.create_text((x0 + x1) / 2, y + LANE_H / 2, text=str(i + 1), fill=colors["text"],
                              font=(self.base_font[0], 9, "bold"))
        if self.watch.get() == "source" and self.player.path == src.path:
            x = x_of(self.player.time())
            c.create_line(x, y, x, y + LANE_H, fill=colors["accent_dark"], width=2, tags=("source_head",))

    def _draw_drag(self, c, total: float):
        d = self._drag
        if not d or not d.get("moved"):
            return
        y = LANE_TOP + LANES.index("Moments") * (LANE_H + 2)
        bottom = LANE_TOP + len(LANES) * (LANE_H + 2)
        if d["mode"] in ("in", "out"):
            a, b = d["ghost"]
            c.create_rectangle(self.tl_x(a), y - 1, self.tl_x(b), y + LANE_H + 1, outline="#1971c2", width=2,
                               dash=(4, 2))
            edge = a if d["mode"] == "in" else b
            c.create_line(self.tl_x(edge), y - 2, self.tl_x(edge), bottom, fill="#1971c2", width=2)
        elif d["mode"] == "move":
            a, b = d["ghost"]
            c.create_rectangle(self.tl_x(a), y - 1, self.tl_x(b), y + LANE_H + 1, outline="#1971c2", width=2,
                               dash=(4, 2))
            x = self.tl_x(d["slot_time"])
            c.create_line(x, y - 3, x, y + LANE_H + 3, fill="#1971c2", width=4)

    # ================================================================ dragging
    def _hit_moment(self, x: float) -> tuple[int | None, str | None]:
        """(moment index, "in" / "out" / "body") under x on the Moments lane."""
        for p in timeline.layout(self.project.segments):
            x0, x1 = self.tl_x(p.edit_start), self.tl_x(p.edit_end)
            if not (x0 - 1 <= x <= x1 + 1):
                continue
            edge = min(EDGE_PX, (x1 - x0) / 3)
            if x >= x1 - edge:
                return p.index, "out"
            if x <= x0 + edge:
                return p.index, "in"
            return p.index, "body"
        return None, None

    def _tl_hover(self, event):
        if self._drag is not None or self._tl_row(event.y) != LANES.index("Moments"):
            self.timeline.configure(cursor="")
            return
        _i, part = self._hit_moment(event.x)
        self.timeline.configure(cursor="sb_h_double_arrow" if part in ("in", "out")
                                else "fleur" if part == "body" else "")

    def _tl_press(self, event):
        self._drag = None
        if self._tl_row(event.y) == LANES.index("Moments") and event.x >= LANE_LABEL_W:
            index, part = self._hit_moment(event.x)
            if index is not None:
                piece = timeline.layout(self.project.segments)[index]
                seg = self.project.segments[index]
                self._drag = {"mode": part if part != "body" else "move", "index": index, "x0": event.x,
                              "y0": event.y, "moved": False, "event": event,
                              "start": piece.edit_start, "end": piece.edit_end,
                              "source_in": seg.source_in, "source_out": seg.source_out,
                              "speed": seg.speed or 1.0, "ghost": (piece.edit_start, piece.edit_end),
                              "slot_time": piece.edit_start, "snapped": None}
                return
        self._drag = {"mode": "click", "x0": event.x, "y0": event.y, "moved": False, "event": event}

    def _snap_targets(self, index: int) -> list[float]:
        targets = timeline.edges(self.project.segments, skip=index)
        head = self._playhead_time()
        if head is not None:
            targets.append(head)
        targets += getattr(self, "extra_snap_times", lambda: [])()   # phase C adds the music's beats
        return targets

    def _tl_motion(self, event):
        d = self._drag
        if d is None or d["mode"] == "click":
            return
        if not d["moved"] and abs(event.x - d["x0"]) < DRAG_PX:
            return
        d["moved"] = True
        total = self.project.total_length
        t = self.tl_t(event.x)
        no_snap = bool(event.state & 0x0001) or not self.snap_var.get()
        left, width = self._tl_area()
        tol = timeline.SNAP_PIXELS / width * self.tl_view.span(total)
        if d["mode"] in ("in", "out"):
            t, hit = (t, None) if no_snap else timeline.snap(t, self._snap_targets(d["index"]), tol)
            d["snapped"] = hit
            seg = self.project.segments[d["index"]]
            if d["mode"] == "out":
                t = max(d["start"] + 0.1, t)
                if seg.reaction:
                    t = min(t, d["start"] + 10.0)
                else:
                    src = self.project.source(seg.source_id)
                    limit = src.duration if src and src.duration else float("inf")
                    t = min(t, d["start"] + (limit - d["source_in"]) / d["speed"])
                d["ghost"] = (d["start"], t)
            else:
                t = min(d["end"] - 0.1, t)
                if not seg.reaction:
                    t = max(t, d["start"] - d["source_in"] / d["speed"])
                d["ghost"] = (t, d["end"])
            a, b = d["ghost"]
            change = (b - a) - (d["end"] - d["start"])
            what = "start" if d["mode"] == "in" else "end"
            note = " · snapped" if hit is not None else ""
            self.status(f"Moment {d['index'] + 1}: {what} {'+' if change >= 0 else '−'}"
                        f"{abs(change):.1f} s, lasts {timeline.format_time(b - a, True)}{note}"
                        "  (Shift = no snap, Esc = cancel)")
        else:
            length = d["end"] - d["start"]
            offset = d["x0"] - self.tl_x(d["start"])
            a = self.tl_t(event.x - offset)
            d["ghost"] = (a, a + length)
            to = timeline.drop_index(self.project.segments, d["index"], self.tl_t(event.x))
            d["to"] = to
            others = [p for p in timeline.layout(self.project.segments) if p.index != d["index"]]
            if to < len(others):
                slot = timeline.layout(self.project.segments)[others[to].index].edit_start
            else:
                slot = total
            d["slot_time"] = slot
            self.status(f"Moment {d['index'] + 1} goes to place {to + 1} of {len(self.project.segments)}"
                        "  (Esc = cancel)")
        # follow the mouse when it reaches the edge of a zoomed timeline
        if self.tl_view.zoom > 1.0:
            if event.x > left + width - 10:
                self.tl_view.start += self.tl_view.span(total) * 0.05
            elif event.x < left + 10:
                self.tl_view.start -= self.tl_view.span(total) * 0.05
            self.tl_view.clamp(total)
        self.draw_timeline()

    def _tl_release(self, event):
        d, self._drag = self._drag, None
        if d is None:
            return
        if not d["moved"]:
            self._timeline_click(d["event"])
            return
        i = d["index"]
        seg = self.project.segments[i]
        if d["mode"] in ("in", "out"):
            a, b = d["ghost"]
            if seg.reaction:
                self.project.trim_to(i, d["mode"], b - a)
            elif d["mode"] == "out":
                self.project.trim_to(i, "out", d["source_in"] + (b - d["start"]) * d["speed"])
            else:
                self.project.trim_to(i, "in", d["source_in"] + (a - d["start"]) * d["speed"])
            if self.project.segments[i].source_in == d["source_in"] and \
                    self.project.segments[i].source_out == d["source_out"]:
                self.draw_timeline()
                return
            self.commit(f"Moment {i + 1} trimmed: it now lasts "
                        f"{timeline.format_time(self.project.segments[i].length, True)}.", keep_selection=seg.id)
        else:
            to = d.get("to", i)
            if to == i:
                self.draw_timeline()
                self.status("Moment left where it was.")
                return
            self.project.move_segment(i, to)
            self.commit(f"Moment {i + 1} moved to place {to + 1}.", keep_selection=seg.id)

    def _tl_cancel(self):
        if self._drag is not None and self._drag.get("moved"):
            self._drag = None
            self.draw_timeline()
            self.status("Drag cancelled.")
        self._drag = None

    # ================================================================ clicks
    def _timeline_click(self, event):
        row = self._tl_row(event.y)
        if row == LANES.index("Original video"):
            self._original_lane_click(event.x)
            return
        total = self.project.total_length
        if total <= 0 or event.x < LANE_LABEL_W:
            return
        t = self.tl_t(event.x)
        # --- phase 3: a click on a Voice-over block selects that script line ---
        if row == LANES.index("Voice-over"):
            for ln in S_script.visible(self.project):
                if ln["start"] - 0.05 <= t <= max(ln["end"], ln["start"] + 0.3):
                    self.select_line(ln["id"])
                    return
        # --- end phase 3 ---
        if not (0 <= t <= total):
            return
        hit = timeline.edit_to_source(self.project.segments, min(t, total - 1e-6))
        if hit:
            self.tree.selection_set(self.project.segments[hit[0]].id)
            if self.watch.get() == "cut" and self.player.path == self.preview_path:
                self.player.seek(t)

    def _original_lane_click(self, x: float):
        """Show that spot of the video you're cutting from."""
        src = self.project.source(self.current_source_id) if self.current_source_id else None
        if src is None or x < LANE_LABEL_W:
            return
        _left, span = self._tl_area()
        t = min(1.0, (x - LANE_LABEL_W) / span) * src.duration
        if self.watch.get() != "source" or self.player.path != src.path:
            self.player.pause()
            self.watch.set("source")
            self.load_source(src, t)
        else:
            self.player.seek(t)

