"""
clipping.editor.preview — Video playback inside the Clip Editor window.

Two players with the same small interface:

  MpvPlayer    real playback with sound, drawn inside the window by mpv
               (sudo apt install mpv libmpv2, pip install python-mpv).
               Light on a CPU: mpv decodes in software very efficiently and
               only ever plays the small preview copy or the source itself.
  StillPlayer  fallback when mpv isn't installed: shows still pictures you can
               scrub through, and "Play" steps through a few pictures a second.
               "Open in video player" plays it properly in another window.

make_player() picks the best one available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tkinter as tk

from clipping.editor import media
from clipping.editor.render import SAFE_ZONES, safe_zone_filter


class BasePlayer:
    name = "player"

    def __init__(self, parent: tk.Widget, bg: str = "#10141c"):
        self.parent = parent
        self.path: str | None = None
        self.duration = 0.0
        self._overlay = False
        self.zones = SAFE_ZONES
        self.widget = tk.Frame(parent, background=bg, highlightthickness=0)

    # interface -----------------------------------------------------------
    def load(self, path: str, start: float = 0.0, duration: float | None = None):
        raise NotImplementedError

    def play(self):
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    @property
    def playing(self) -> bool:
        return False

    def toggle(self):
        self.pause() if self.playing else self.play()

    def clear(self):
        """Stop and forget the current video (nothing is loaded afterwards)."""
        self.pause()
        self.path = None
        self.duration = 0.0

    def seek(self, seconds: float):
        raise NotImplementedError

    def time(self) -> float:
        raise NotImplementedError

    def set_overlay(self, on: bool):
        self._overlay = bool(on)

    def set_zones(self, zones):
        """Which button areas to shade (one platform's, or all of them)."""
        self.zones = zones or SAFE_ZONES
        self.set_overlay(self._overlay)

    def close(self):
        pass

    def open_external(self):
        """Play the current file in the computer's own video player."""
        if not self.path:
            return
        for cmd in (["mpv", f"--start={self.time():.2f}", self.path],
                    ["ffplay", "-autoexit", "-ss", f"{self.time():.2f}", self.path],
                    ["xdg-open", self.path]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return


class MpvPlayer(BasePlayer):
    name = "mpv"

    def __init__(self, parent: tk.Widget, bg: str = "#10141c"):
        super().__init__(parent, bg)
        import mpv  # noqa: F401  (checked by make_player)
        self._mpv = None

    def _ensure(self):
        if self._mpv is not None:
            return
        import mpv
        self.widget.update_idletasks()
        # Embedding with wid= only works through X11. On a Wayland desktop mpv's
        # default output ignores wid and opens its own full-screen window, which
        # covers the screen and freezes the app. So always ask for an X11 output.
        forced = os.environ.get("CLIP_EDITOR_MPV_VO")
        choices = [{"vo": forced}] if forced else [
            {"vo": "gpu", "gpu_context": "x11egl"},
            {"vo": "x11"},  # slower, but works without a GPU context
        ]
        last_error = None
        for extra in choices:
            try:
                self._mpv = mpv.MPV(
                    wid=str(self.widget.winfo_id()),
                    keep_open="always",
                    idle="yes",
                    osc="no",
                    input_default_bindings=False,
                    input_vo_keyboard=False,
                    hwdec="no",
                    pause=True,
                    hr_seek="yes",
                    **extra,
                )
                return
            except Exception as err:  # try the next output
                last_error = err
        raise last_error

    def load(self, path: str, start: float = 0.0, duration: float | None = None):
        self._ensure()
        self.path = path
        self.duration = duration or 0.0
        self._mpv.pause = True
        self._mpv.loadfile(path, start=f"{max(0.0, start):.3f}")
        self._apply_overlay()

    def play(self):
        if self._mpv is not None and self.path:
            self._mpv.pause = False

    def pause(self):
        if self._mpv is not None:
            self._mpv.pause = True

    def clear(self):
        super().clear()
        if self._mpv is not None:
            try:
                self._mpv.command("stop")
            except Exception:
                pass

    @property
    def playing(self) -> bool:
        return bool(self._mpv is not None and not self._mpv.pause)

    def seek(self, seconds: float):
        if self._mpv is not None and self.path:
            try:
                self._mpv.seek(max(0.0, seconds), reference="absolute", precision="exact")
            except Exception:
                pass

    def time(self) -> float:
        if self._mpv is None:
            return 0.0
        try:
            return float(self._mpv.time_pos or 0.0)
        except Exception:
            return 0.0

    def set_overlay(self, on: bool):
        super().set_overlay(on)
        self._apply_overlay()

    def _apply_overlay(self):
        if self._mpv is None:
            return
        try:
            self._mpv.vf = f"lavfi=[{safe_zone_filter(self.zones)}]" if self._overlay else ""
        except Exception:
            pass

    def close(self):
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None


class StillPlayer(BasePlayer):
    """Shows one picture at a time. Works anywhere ffmpeg works."""
    name = "pictures"
    STEP = 0.25  # seconds between pictures while "playing"

    def __init__(self, parent: tk.Widget, bg: str = "#10141c"):
        super().__init__(parent, bg)
        self.canvas = tk.Canvas(self.widget, background=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._t = 0.0
        self._playing = False
        self._img = None
        self._tmp = tempfile.mkdtemp(prefix="clip-editor-")
        self._pending = None
        self.canvas.bind("<Configure>", lambda e: self._schedule_draw())

    def load(self, path: str, start: float = 0.0, duration: float | None = None):
        self.path = path
        self.duration = duration if duration else media.probe(path).duration
        self._t = max(0.0, start)
        self._schedule_draw()

    def play(self):
        if not self.path:
            return
        self._playing = True
        self._tick()

    def pause(self):
        self._playing = False

    def clear(self):
        super().clear()
        self._t = 0.0
        self._schedule_draw()

    @property
    def playing(self) -> bool:
        return self._playing

    def seek(self, seconds: float):
        self._t = min(max(0.0, seconds), self.duration or seconds)
        self._schedule_draw()

    def time(self) -> float:
        return self._t

    def set_overlay(self, on: bool):
        super().set_overlay(on)
        self._schedule_draw()

    def _tick(self):
        if not self._playing:
            return
        if self.duration and self._t + self.STEP >= self.duration:
            self._playing = False
            return
        self._t += self.STEP
        self._draw()
        self.widget.after(int(self.STEP * 1000), self._tick)

    def _schedule_draw(self):
        if self._pending is not None:
            self.widget.after_cancel(self._pending)
        self._pending = self.widget.after(60, self._draw)

    def _draw(self):
        self._pending = None
        c = self.canvas
        c.delete("all")
        w, h = max(2, c.winfo_width()), max(2, c.winfo_height())
        if not self.path:
            c.create_text(w / 2, h / 2, text="Add a video to start", fill="#98a2b3")
            return
        png = os.path.join(self._tmp, "frame.png")
        try:
            media.still_frame(self.path, self._t, png, height=max(64, h - (h % 2)))
            self._img = tk.PhotoImage(file=png)
        except Exception:
            c.create_text(w / 2, h / 2, text="Can't show this moment", fill="#98a2b3")
            return
        # Shrink to fit the width if needed (PhotoImage only shrinks by whole steps).
        factor = 1
        while self._img.width() / factor > w and factor < 8:
            factor += 1
        if factor > 1:
            self._img = self._img.subsample(factor, factor)
        iw, ih = self._img.width(), self._img.height()
        x0, y0 = (w - iw) / 2, (h - ih) / 2
        c.create_image(w / 2, h / 2, image=self._img)
        if self._overlay:
            for _name, fx, fy, fw, fh in self.zones:
                c.create_rectangle(x0 + fx * iw, y0 + fy * ih, x0 + (fx + fw) * iw, y0 + (fy + fh) * ih,
                                   fill="#000000", stipple="gray50", outline="")

    def close(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


def mpv_available() -> bool:
    try:
        import mpv  # noqa: F401
        return True
    except Exception:  # OSError when libmpv is missing
        return False


def make_player(parent: tk.Widget, bg: str = "#10141c") -> BasePlayer:
    if os.environ.get("CLIP_EDITOR_NO_MPV") != "1" and mpv_available():
        try:
            return MpvPlayer(parent, bg)
        except Exception:
            pass
    return StillPlayer(parent, bg)
