"""
clipping.editor.styles — How captions, the hook line and pop-up text look.

One caption look per channel. Sizes are for a 1920-pixel-tall video and are
scaled for the small preview, so preview and export look the same.

Fonts come from the same places Clipping Studio uses (``clipping/config.py``
DAFTAR_FONT and the thumbnail font), downloaded once into ``custom_fonts/``
with ``clipping/studio/typography.py``. Kids adds one rounded font (Fredoka).
If a font can't be downloaded, libass uses a plain system font instead.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import urllib.request
from dataclasses import asdict, dataclass, replace

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONT_DIR = os.path.join(REPO_DIR, "custom_fonts")

# name used in the .ass file -> (file, download URL)
FONTS = {
    "Montserrat Black": ("Montserrat-Black.ttf",
                         "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Black.ttf"),
    "Anton": ("Anton-Regular.ttf", "https://cdn.jsdelivr.net/fontsource/fonts/anton@latest/latin-400-normal.ttf"),
    "Fredoka": ("Fredoka-SemiBold.ttf", "https://cdn.jsdelivr.net/fontsource/fonts/fredoka@latest/latin-600-normal.ttf"),
}
COVER_FONT = "Montserrat Black"

POSITIONS = ("lower", "middle", "top")
POSITION_LABELS = {"lower": "Lower third", "middle": "Middle", "top": "Near the top"}


@dataclass(frozen=True)
class CaptionStyle:
    font: str = "Montserrat Black"
    size: int = 84                 # at 1920 px tall
    colour: str = "FFFFFF"         # RRGGBB
    highlight: str = "FFE600"      # the word being spoken
    highlight_cycle: tuple = ()    # Kids: colours that take turns
    outline_colour: str = "000000"
    outline: float = 6
    shadow: float = 0
    bold: bool = False
    all_caps: bool = True
    word_highlight: bool = True
    position: str = "lower"
    max_words: int = 4


CHANNEL_STYLES = {
    "GTA 6": CaptionStyle(),
    "Sports": CaptionStyle(font="Anton", size=92, colour="FFE600", highlight="FFFFFF", outline=6),
    "Kids": CaptionStyle(font="Fredoka", size=96, colour="FFFFFF", highlight="FFD23F",
                         highlight_cycle=("FFD23F", "FF6FB5", "4DD9FF", "7CE36B"),
                         outline_colour="4A2A7A", outline=5, shadow=4, all_caps=False, max_words=3),
    "Other": CaptionStyle(size=80, outline=4, word_highlight=False, all_caps=False),
}

HOOK_STYLE = CaptionStyle(font="Anton", size=124, colour="FFFFFF", outline=8, all_caps=True, position="top")
POPUP_STYLE = CaptionStyle(font="Anton", size=104, colour="FFE600", outline=7, all_caps=True)

# Per-channel upload defaults
DEFAULT_HASHTAGS = {
    "GTA 6": "#gta6 #gta #gaming #shorts",
    "Sports": "#football #sports #shorts",
    "Kids": "#kids #stories #shorts",
    "Other": "#shorts",
}

# Settings people can change, stored in project.caption_style
EDITABLE = ("size_scale", "position", "word_highlight", "all_caps")


def caption_style(channel: str, overrides: dict | None = None) -> CaptionStyle:
    """The channel's look with the person's changes applied."""
    base = CHANNEL_STYLES.get(channel, CHANNEL_STYLES["Other"])
    o = overrides or {}
    changes = {}
    if "size_scale" in o:
        changes["size"] = int(round(base.size * min(1.6, max(0.6, float(o["size_scale"])))))
    if o.get("position") in POSITIONS:
        changes["position"] = o["position"]
    for key in ("word_highlight", "all_caps"):
        if key in o:
            changes[key] = bool(o[key])
    return replace(base, **changes)


def to_dict(style: CaptionStyle) -> dict:
    return asdict(style)


# ---------------------------------------------------------------- fonts
def _typography():
    """clipping/studio/typography.py, loaded by path like the studio does.
    It imports heavy packages, so this may fail on a light install."""
    path = os.path.join(REPO_DIR, "clipping", "studio", "typography.py")
    spec = importlib.util.spec_from_file_location("clip_editor_typography", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _download(url: str, filename: str, font_dir: str) -> bool:
    try:
        return bool(_typography().download_google_font(url, filename, font_dir, max_retry=2))
    except Exception:
        pass
    target = os.path.join(font_dir, filename)
    try:
        with urllib.request.urlopen(url, timeout=20) as r, open(target + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        if os.path.getsize(target + ".part") > 1000:
            os.replace(target + ".part", target)
            return True
    except Exception:
        pass
    return False


def ensure_fonts(font_dir: str = FONT_DIR, log=None) -> dict[str, str]:
    """Download missing fonts once. Returns {font name: file path} for the ones
    that are there. Never raises: missing fonts fall back to a system font."""
    os.makedirs(font_dir, exist_ok=True)
    found = {}
    for name, (filename, url) in FONTS.items():
        path = os.path.join(font_dir, filename)
        if not (os.path.exists(path) and os.path.getsize(path) > 1000):
            if log:
                log(f"Downloading the font {name}…")
            _download(url, filename, font_dir)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            found[name] = path
    return found


def emoji_font() -> str | None:
    """A colour emoji font, if one is installed (Ubuntu: fonts-noto-color-emoji)."""
    for path in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                 "/usr/share/fonts/noto/NotoColorEmoji.ttf",
                 os.path.expanduser("~/.local/share/fonts/NotoColorEmoji.ttf")):
        if os.path.exists(path):
            return path
    if shutil.which("fc-match"):
        import subprocess
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", "Noto Color Emoji"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if out and "emoji" in out.lower() and os.path.exists(out):
                return out
        except Exception:
            pass
    return None


EMOJI_HELP = ("Emoji need a colour emoji font. On Ubuntu run:  sudo apt install fonts-noto-color-emoji  "
              "then export again. Until then the text shows without the emoji.")
