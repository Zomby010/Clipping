"""
clipping.editor.gui.widgets — Small shared widgets: pages that scroll, with
text that re-wraps to the panel's width, and sections that fold away.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

MUTED, TEXT = "#667085", "#1d2330"


class ScrollPage(ttk.Frame):
    """A panel page that scrolls when it is taller than the window."""

    def __init__(self, parent, style="Card.TFrame", bg="#ffffff"):
        super().__init__(parent, style=style)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=bg)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style=style, padding=(16, 14))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        def resized(event):
            self.canvas.itemconfigure(win, width=event.width)
            rewrap(self.inner, event.width - 52)

        self.canvas.bind("<Configure>", resized)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _wheel(self, event):
        if (not str(event.widget).startswith(str(self)) or not self.winfo_ismapped()
                or isinstance(event.widget, (tk.Listbox, tk.Text, ttk.Treeview))):   # those scroll themselves
            return
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -int(event.delta / 40) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")


def rewrap(widget, width: int) -> None:
    """Give every wrapping label under ``widget`` the panel's width."""
    if width < 120:
        return
    for child in widget.winfo_children():
        if isinstance(child, ttk.Label) and str(child.cget("wraplength") or "0") not in ("", "0"):
            child.configure(wraplength=width)
        rewrap(child, width)


class Section(ttk.Frame):
    """A titled block whose contents fold away when the title is pressed.
    Put widgets in ``.body``."""

    def __init__(self, parent, title: str, font, open_: bool = True):
        super().__init__(parent, style="Card.TFrame")
        self.title = title
        self.pack(fill="x", pady=(0, 10))
        ttk.Separator(self).pack(fill="x")
        self.header = tk.Label(self, anchor="w", cursor="hand2", background="#ffffff", foreground=TEXT,
                               font=(font[0], font[1] + 1, "bold"), pady=8, takefocus=True)
        self.header.pack(fill="x")
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.header.bind("<Button-1>", lambda e: self.toggle())
        self.header.bind("<Return>", lambda e: self.toggle())
        self.header.bind("<Enter>", lambda e: self.header.configure(foreground="#c2410c"))
        self.header.bind("<Leave>", lambda e: self.header.configure(foreground=TEXT))
        self.is_open = False
        self.set_open(open_)

    def set_open(self, open_: bool) -> None:
        self.is_open = open_
        self.header.configure(text=f"{'▾' if open_ else '▸'}  {self.title}")
        if open_:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()

    def toggle(self) -> None:
        self.set_open(not self.is_open)
