"""
clipping.editor.gui.place_panel — Phase A: **Place** things on the video by
dragging them.

mpv draws the video in its own window, so nothing can be drawn over the
playing picture. Place pauses, shows the frame at the playhead as a picture,
and draws the hook, pop-ups, arrows, circles, corner reactions and the logo on
it with a box each:

  drag a box               move it
  drag its corner square   make it bigger or smaller
  right-click it           put it back in its usual spot and size
  Done (or Esc)            back to the video, which is remade with the change

Every drag is one Undo step. The geometry lives in clipping/editor/placement.py.
Mixed into EditorApp (gui/app.py).
"""

from __future__ import annotations

import copy
import os
import tempfile
import tkinter as tk
from tkinter import ttk

from clipping.editor import overlays as O
from clipping.editor import placement as PL
from clipping.editor import project as P
from clipping.editor import reactions as R
from clipping.editor import render, timeline
from clipping.editor import styles as S
from clipping.editor import text_layers as T

HANDLE = 7
BOX_COLOURS = {"hook": "#ff922b", "popup": "#ffd43b", "mark": "#da77f2", "corner": "#4dabf7", "logo": "#69db7c"}


class PlacePanelMixin:
    placing = False

    def _build_place_button(self, parent):
        self.place_btn = ttk.Button(parent, text="Place on video", width=0, style="Small.TButton",
                                    command=self.toggle_place)
        self.place_btn.pack(side="right")

    # ================================================================ in and out
    def toggle_place(self):
        if self.placing:
            self.leave_place()
        else:
            self.enter_place()

    def _place_time(self) -> float:
        if self.watch.get() == "cut" and self.player.path and self.player.path == self.preview_path:
            return min(self.player.time(), max(0.0, self.project.total_length - 0.05))
        return self._selected_start()

    def enter_place(self):
        if not self.project.segments:
            self.status("Add some moments first.")
            return
        if self.busy:
            self.status("Wait for the current job to finish (or press Stop), then press Place.")
            return
        self.player.pause()
        t = self._place_time()
        snapshot = P.Project.from_dict(self.project.to_dict())
        self._place_dir = getattr(self, "_place_dir", None) or tempfile.mkdtemp(prefix="clipedit_place_")
        png = os.path.join(self._place_dir, "frame.png")

        def work(progress):
            render.clean_frame(snapshot, t, png)
            progress(1.0)
            return png

        def done(path):
            self._show_place(path, t)

        self.run_job("Getting the frame to place things on…", work, done)

    def _show_place(self, frame_png: str, t: float):
        from PIL import Image
        holder = self.player.widget.master
        hw, hh = max(200, holder.winfo_width()), max(300, holder.winfo_height())
        dh = hh - 4
        dw = int(dh * 9 / 16)
        if dw > hw - 4:
            dw = hw - 4
            dh = int(dw * 16 / 9)
        self._pl = {"t": t, "w": dw, "h": dh, "drag": None, "images": [], "frame": None}
        with Image.open(frame_png) as im:
            self._pl["frame_img"] = im.convert("RGB").resize((dw, dh))
        self.player.widget.pack_forget()
        canvas = tk.Canvas(holder, width=hw, height=hh, background=self.COLORS["player"], highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._pl_canvas = canvas
        self._pl["ox"], self._pl["oy"] = (hw - dw) // 2, (hh - dh) // 2
        canvas.bind("<ButtonPress-1>", self._pl_press)
        canvas.bind("<B1-Motion>", self._pl_motion)
        canvas.bind("<ButtonRelease-1>", self._pl_release)
        canvas.bind("<Button-3>", self._pl_reset)
        canvas.bind("<Motion>", self._pl_hover)
        self.placing = True
        self.place_btn.configure(text="Done placing")
        self._draw_place()
        self.status(f"Place mode at {timeline.format_time(t, True)}: drag a box to move it, its corner square to "
                    "resize, right-click to put it back. Press Done placing (or Esc) when finished.")

    def leave_place(self):
        if not self.placing:
            return
        self.placing = False
        self._pl_canvas.destroy()
        self.player.widget.pack(fill="both", expand=True)
        self.place_btn.configure(text="Place on video")
        if self.watch.get() == "cut":
            self._schedule_preview(now=True)
        self.status("Back to the video.")

    # ================================================================ drawing
    def _place_items(self):
        return PL.items_at(self.project, self._pl["t"], self._pl["w"], self._pl["h"])

    def _draw_place(self):
        from PIL import Image, ImageTk
        c, pl = self._pl_canvas, self._pl
        c.delete("all")
        ox, oy, w, h = pl["ox"], pl["oy"], pl["w"], pl["h"]
        pl["images"] = []
        frame = ImageTk.PhotoImage(pl["frame_img"], master=self.root)
        pl["images"].append(frame)
        c.create_image(ox, oy, image=frame, anchor="nw")
        if self.safe_var.get():   # the button areas, as on the player
            for _name, x, y, bw, bh in self.player.zones:
                c.create_rectangle(ox + x * w, oy + y * h, ox + (x + bw) * w, oy + (y + bh) * h,
                                   fill="black", stipple="gray25", outline="")
        items = self._place_items()
        pl["items"] = items
        for it in items:
            x0, y0, x1, y1 = ox + it.x, oy + it.y, ox + it.x + it.w, oy + it.y + it.h
            picture = self._place_picture(it)
            if picture is not None:
                pl["images"].append(picture)
                c.create_image(x0, y0, image=picture, anchor="nw")
            elif it.kind in ("hook", "popup"):
                data = PL.find(self.project, it.key)
                text = T.split_emoji(data.get("text") or "")[0].upper() or data.get("text", "")
                style = S.POPUP_STYLE if it.kind == "popup" else S.HOOK_STYLE
                size = max(8, int(style.size * self._pl["h"] / 1920 * T.item_scale(data)))
                colour = "#ffe600" if it.kind == "popup" else "white"
                font = ("Anton", -size)
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    c.create_text((x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy, text=text, fill="black", font=font,
                                  width=max(20, it.w))
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=text, fill=colour, font=font, width=max(20, it.w))
            colour = BOX_COLOURS.get(it.kind, "white")
            c.create_rectangle(x0, y0, x1, y1, outline=colour, width=2, dash=(5, 3))
            c.create_rectangle(x1 - HANDLE, y1 - HANDLE, x1 + HANDLE, y1 + HANDLE, fill=colour, outline="black")
            c.create_text(x0 + 2, y0 - 2, text=it.label, anchor="sw", fill=colour,
                          font=(self.base_font[0], 9, "bold"))
        if not items:
            c.create_text(ox + w / 2, oy + h / 2, fill="white", width=w - 20, justify="center",
                          font=(self.base_font[0], 11, "bold"),
                          text="Nothing to place at this point. Move to where a pop-up, arrow or corner "
                               "reaction shows, or add a hook or logo.")

    def _place_picture(self, it):
        """A picture of the item at its size (arrows, circles, logo, corner reaction)."""
        from PIL import Image, ImageTk
        w = self._pl["w"]
        try:
            if it.kind == "mark":
                data = PL.find(self.project, it.key)
                png = os.path.join(self._place_dir, f"{it.key}.png")
                png, _side, _ = O.mark_png(data, w, png)
                with Image.open(png) as im:
                    return ImageTk.PhotoImage(im.resize((max(1, it.w), max(1, it.h))), master=self.root)
            if it.kind == "logo":
                with Image.open(self.project.logo["path"]) as im:
                    im = im.convert("RGBA").resize((max(1, it.w), max(1, it.h)))
                    return ImageTk.PhotoImage(im, master=self.root)
            if it.kind == "corner":
                data = PL.find(self.project, it.key)
                png = R.thumbnail(data["path"], 160)
                if png:
                    with Image.open(png) as im:
                        return ImageTk.PhotoImage(im.convert("RGB").resize((max(1, it.w), max(1, it.h))),
                                                  master=self.root)
        except Exception:
            return None
        return None

    # ================================================================ mouse
    def _pl_hit(self, x, y):
        ox, oy = self._pl["ox"], self._pl["oy"]
        px, py = x - ox, y - oy
        for it in reversed(self._pl.get("items", [])):      # the top one first
            if it.on_handle(px, py, HANDLE + 2):
                return it, "resize"
        for it in reversed(self._pl.get("items", [])):
            if it.contains(px, py):
                return it, "move"
        return None, None

    def _pl_hover(self, event):
        _it, what = self._pl_hit(event.x, event.y)
        self._pl_canvas.configure(cursor="bottom_right_corner" if what == "resize" else "fleur" if what else "")

    def _pl_press(self, event):
        it, what = self._pl_hit(event.x, event.y)
        self._pl["drag"] = None if it is None else {
            "item": it, "what": what, "x0": event.x, "y0": event.y,
            "snapshot": copy.deepcopy(self.project.to_dict()), "moved": False}

    def _pl_motion(self, event):
        d = self._pl.get("drag")
        if not d:
            return
        dx, dy = event.x - d["x0"], event.y - d["y0"]
        if not d["moved"] and abs(dx) < 3 and abs(dy) < 3:
            return
        d["moved"] = True
        self.project = P.Project.from_dict(copy.deepcopy(d["snapshot"]))
        w, h = self._pl["w"], self._pl["h"]
        it = d["item"]
        if d["what"] == "move":
            PL.move(self.project, it, dx, dy, w, h)
        else:
            PL.resize(self.project, it, max(12, it.w + dx), w, h)
        self._draw_place()

    def _pl_release(self, _event):
        d, self._pl["drag"] = self._pl.get("drag"), None
        if not d or not d["moved"]:
            return
        what = "Resized" if d["what"] == "resize" else "Moved"
        self.commit(f"{what}: {d['item'].label}. Press Done placing to see it on the video.")
        if self.placing:
            self._draw_place()

    def _pl_reset(self, event):
        it, _what = self._pl_hit(event.x, event.y)
        if it is None:
            return
        PL.reset(self.project, it)
        self.commit(f"{it.label} is back in its usual spot.")
        if self.placing:
            self._draw_place()

    def _place_escape(self):
        if self.placing:
            d = self._pl.get("drag")
            if d and d["moved"]:
                self.project = P.Project.from_dict(copy.deepcopy(d["snapshot"]))
                self._pl["drag"] = None
                self._draw_place()
                self.status("Drag cancelled.")
                return
            self.leave_place()
