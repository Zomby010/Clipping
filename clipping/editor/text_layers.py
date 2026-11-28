"""
clipping.editor.text_layers — Captions, hook line and pop-up text as an .ass
file (drawn by ffmpeg's libass) plus emoji pictures (libass can't draw colour
emoji, so they are made with Pillow and laid over the video by ffmpeg).

Everything is in edit time (0 = first frame of the finished video) and sized
for the output, so the 540x960 preview and the 1080x1920 export match.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from clipping.editor import captions as C
from clipping.editor import styles as S

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF"
    "\u3030\u303D\u3297\u3299\uFE0F\u200D\u20E3]+"
)

# Pop-up spots: a 3 x 3 grid inside the safe area (fractions of width / height)
GRID_X = (0.24, 0.46, 0.68)
GRID_Y = (0.18, 0.42, 0.64)
NAMED_SPOTS = {"top": (0, 1), "middle": (1, 1)}

COMMON_EMOJI = ("😂", "💀", "😳", "🔥", "😭", "👀", "🤯", "💯", "❤️", "🙏",
                "😱", "🤣", "😎", "🥶", "😡", "👏", "🚀", "⚽", "🎮", "✨")
# Shown in the menu when there is no colour emoji font to draw them with.
EMOJI_NAMES = dict(zip(COMMON_EMOJI, (
    "laughing", "skull", "shocked", "fire", "crying", "eyes", "mind blown", "100", "heart", "please",
    "scream", "rolling", "cool", "cold", "angry", "clap", "rocket", "football", "game", "sparkles")))


@dataclass
class EmojiMark:
    png: str
    x: int          # top-left corner in output pixels
    y: int
    start: float
    end: float
    anim: str = ""   # how it arrives (the hook's emoji move like its words)


@dataclass
class TextLayers:
    ass_path: str | None = None
    fonts_dir: str | None = None
    emojis: list[EmojiMark] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.ass_path is None and not self.emojis


# ---------------------------------------------------------------- helpers
HOOK_SECONDS = (1.0, 600.0)   # shortest and longest the hook line can stay up


def hook_seconds(hook: dict) -> float:
    return min(HOOK_SECONDS[1], max(HOOK_SECONDS[0], float(hook.get("seconds", 2.0))))


def split_emoji(text: str) -> tuple[str, str]:
    """('BRO WHAT', '💀') from 'BRO WHAT 💀'."""
    emoji = "".join(EMOJI_RE.findall(text or ""))
    plain = " ".join(EMOJI_RE.sub(" ", text or "").split())
    return plain, emoji


def ass_colour(rrggbb: str, alpha: int = 0) -> str:
    rr, gg, bb = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _geometry(style: S.CaptionStyle, w: int, h: int) -> tuple[int, int, int, int]:
    """(alignment, margin_l, margin_r, margin_v) keeping text out of the button areas."""
    left, right = int(0.08 * w), int(0.16 * w)
    if style.position == "top":
        return 8, int(0.08 * w), int(0.08 * w), int(0.12 * h)
    if style.position == "middle":
        return 5, left, right, 0
    return 2, left, right, int(0.24 * h)   # lower third: text ends at 76 % height


def _style_line(name: str, st: S.CaptionStyle, w: int, h: int) -> str:
    k = h / 1920
    align, ml, mr, mv = _geometry(st, w, h)
    return (f"Style: {name},{st.font},{max(8, int(st.size * k))},{ass_colour(st.colour)},"
            f"{ass_colour(st.colour)},{ass_colour(st.outline_colour)},{ass_colour('000000', 0x80)},"
            f"{-1 if st.bold else 0},0,0,0,100,100,0,0,1,{round(st.outline * k, 2)},"
            f"{round(st.shadow * k, 2)},{align},{ml},{mr},{mv},1")


BOX_COLOUR = "E8590C"   # orange, unless another colour was picked


def box_colour(hook: dict) -> str:
    """The hook box colour as RRGGBB."""
    value = str(hook.get("box_color") or "").lstrip("#")
    return value.upper() if re.fullmatch(r"[0-9a-fA-F]{6}", value) else BOX_COLOUR


def box_text_colour(rrggbb: str) -> str:
    """White words, or black ones on a light box."""
    r, g, b = (int(rrggbb[i:i + 2], 16) for i in (0, 2, 4))
    return "000000" if 0.299 * r + 0.587 * g + 0.114 * b > 160 else "FFFFFF"


def _box_style_line(w: int, h: int, colour: str = BOX_COLOUR) -> str:
    """The hook on a solid box (BorderStyle 3 fills the box with the outline colour)."""
    parts = _style_line("HookBox", S.HOOK_STYLE, w, h).split(",")
    box = ass_colour(colour)
    parts[3] = parts[4] = ass_colour(box_text_colour(colour))
    parts[5] = parts[6] = box                     # outline and shadow colour
    parts[15], parts[16], parts[17] = "3", str(round(14 * h / 1920, 2)), "0"
    return ",".join(parts)


def _case(text: str, st: S.CaptionStyle) -> str:
    return text.upper() if st.all_caps else text


def caption_events(project, st: S.CaptionStyle) -> list[str]:
    events = []
    for cap in C.visible(project):
        words = [w for w in cap["words"] if w.get("start") is not None]
        plain = [_case(split_emoji(w["word"])[0], st) for w in words]
        if not any(plain):
            continue
        if not st.word_highlight:
            text = ass_text(" ".join(p for p in plain if p))
            events.append(f"Dialogue: 0,{ass_time(cap['start'])},{ass_time(cap['end'])},Caption,,0,0,0,,{text}")
            continue
        for i, w in enumerate(words):
            start = w["start"]
            end = words[i + 1]["start"] if i + 1 < len(words) else cap["end"]
            if end - start < 0.01:
                continue
            colour = st.highlight_cycle[i % len(st.highlight_cycle)] if st.highlight_cycle else st.highlight
            parts = []
            for j, p in enumerate(plain):
                if not p:
                    continue
                p = ass_text(p)
                parts.append(f"{{\\c{ass_colour(colour)}}}{p}{{\\c{ass_colour(st.colour)}}}" if j == i else p)
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return events


# How the hook arrives. "pop" is the original look.
HOOK_ANIMATIONS = (("pop", "Pop"), ("bounce", "Bounce"), ("slide", "Slide down"), ("shake", "Shake"))


def hook_effect(anim: str, w: int, h: int) -> str:
    """The .ass tags that bring the hook in."""
    if anim == "bounce":
        return ("\\fscx40\\fscy40\\t(0,140,\\fscx118\\fscy118)\\t(140,240,\\fscx92\\fscy92)"
                "\\t(240,330,\\fscx100\\fscy100)")
    if anim == "slide":
        x, y = w // 2, int(0.12 * h)
        return f"\\an8\\move({x},{-int(0.1 * h)},{x},{y},0,260)"
    if anim == "shake":
        steps = "".join(f"\\t({t},{t + 60},\\frz{a})" for t, a in
                        zip(range(0, 420, 60), (5, -5, 4, -4, 2, -2, 0)))
        return steps
    return "\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)"


def emoji_motion(anim: str, x: int, y: int) -> tuple[str, str]:
    """ffmpeg overlay x / y expressions that bring a hook emoji in like the words."""
    if anim == "slide":
        return str(x), f"if(lt(t,0.26),-h+({y}+h)*t/0.26,{y})"
    if anim == "bounce":
        return str(x), f"{y}-if(lt(t,0.6),abs(sin(t*PI/0.2))*H*0.05*(1-t/0.6),0)"
    if anim == "shake":
        return f"{x}+if(lt(t,0.6),sin(t*45)*W*0.05*(1-t/0.6),0)", str(y)
    if anim == "pop":
        return str(x), f"{y}+if(lt(t,0.16),H*0.03*(1-t/0.16),0)"
    return str(x), str(y)


def put_on_box(png: str, colour: str) -> None:
    """Redraw an emoji picture on a rounded box of ``colour``."""
    from PIL import Image, ImageDraw
    with Image.open(png) as im:
        emoji = im.convert("RGBA")
    pad = max(4, emoji.height // 6)
    box = Image.new("RGBA", (emoji.width + 2 * pad, emoji.height + 2 * pad), (0, 0, 0, 0))
    rgb = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
    ImageDraw.Draw(box).rounded_rectangle((0, 0, box.width - 1, box.height - 1), radius=pad, fill=rgb + (255,))
    box.alpha_composite(emoji, (pad, pad))
    box.save(png)


def spot(position) -> tuple[float, float]:
    """Pop-up position -> (x, y) fractions of the frame."""
    if isinstance(position, str) and position in NAMED_SPOTS:
        r, c = NAMED_SPOTS[position]
    else:
        try:
            r, c = (int(v) for v in str(position).split(","))
        except ValueError:
            r, c = NAMED_SPOTS["middle"]
    r, c = min(2, max(0, r)), min(2, max(0, c))
    return GRID_X[c], GRID_Y[r]


def popups(project) -> list[dict]:
    return [o for o in project.overlays if o.get("type") == "text"]


def build_ass(project, w: int, h: int) -> str | None:
    """The whole .ass file, or None when there is no text to draw."""
    C.refresh(project)
    st = S.caption_style(project.channel, project.caption_style)
    hook = project.hook or {}
    hook_text = split_emoji(hook.get("text", ""))[0]
    events = caption_events(project, st)
    if hook_text:
        secs = hook_seconds(hook)
        look = "HookBox" if hook.get("box") else "Hook"
        events.append(
            f"Dialogue: 1,{ass_time(0)},{ass_time(secs)},{look},,0,0,0,,"
            f"{{{hook_effect(hook.get('anim', 'pop'), w, h)}}}{ass_text(_case(hook_text, S.HOOK_STYLE))}")
    for o in popups(project):
        text = split_emoji(o.get("text", ""))[0]
        if not text:
            continue
        fx, fy = spot(o.get("position", "middle"))
        events.append(
            f"Dialogue: 2,{ass_time(o['start'])},{ass_time(o['end'])},Popup,,0,0,0,,"
            f"{{\\an5\\pos({int(fx * w)},{int(fy * h)})\\fscx70\\fscy70\\t(0,120,\\fscx100\\fscy100)}}"
            f"{ass_text(_case(text, S.POPUP_STYLE))}")
    if not events:
        return None
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{_style_line('Caption', st, w, h)}\n"
        f"{_style_line('Hook', S.HOOK_STYLE, w, h)}\n"
        f"{_box_style_line(w, h, box_colour(hook))}\n"
        f"{_style_line('Popup', S.POPUP_STYLE, w, h)}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------- emoji
def render_emoji_png(emoji: str, height: int, out_png: str, font_path: str) -> str | None:
    """Draw ``emoji`` with a colour emoji font into a transparent PNG ``height`` px tall."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(font_path, 109)  # Noto Color Emoji only has this size
        probe = Image.new("RGBA", (10, 10))
        box = ImageDraw.Draw(probe).textbbox((0, 0), emoji, font=font, embedded_color=True)
        img = Image.new("RGBA", (max(1, box[2] - box[0] + 8), max(1, box[3] - box[1] + 8)), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((4 - box[0], 4 - box[1]), emoji, font=font, embedded_color=True)
        bbox = img.getbbox()
        if not bbox:
            return None
        img = img.crop(bbox)
        width = max(1, int(img.width * height / img.height))
        img = img.resize((width, height), Image.LANCZOS)
        img.save(out_png)
        return out_png
    except Exception:
        return None


def emoji_marks(project, w: int, h: int, workdir: str, font_path: str | None) -> tuple[list[EmojiMark], list[str]]:
    wanted = []   # (emoji, centre x, centre y, size, start, end, movement, box colour)
    hook = project.hook or {}
    hook_plain, hook_emoji = split_emoji(hook.get("text", ""))
    if hook_emoji:
        secs = hook_seconds(hook)
        size = int(S.HOOK_STYLE.size * h / 1920)
        # Below the hook text: estimate how many lines it wraps to.
        chars_per_line = max(6, int((0.84 * w) / (0.52 * size)))
        lines = max(1, -(-len(hook_plain) // chars_per_line)) if hook_plain else 0
        cy = int(0.12 * h + lines * size * 1.15 + size * 0.7)
        # Emoji on their own get the box; with words, the words already have it.
        boxed = box_colour(hook) if hook.get("box") and not hook_plain else ""
        wanted.append((hook_emoji, w // 2, cy, int(size * 1.1), 0.0, secs, hook.get("anim", "pop"), boxed))
    for o in popups(project):
        plain, emoji = split_emoji(o.get("text", ""))
        if not emoji:
            continue
        fx, fy = spot(o.get("position", "middle"))
        size = int(S.POPUP_STYLE.size * h / 1920)
        cy = int(fy * h - (size * 1.2 if plain else 0))
        wanted.append((emoji, int(fx * w), cy, int(size * 1.15), float(o["start"]), float(o["end"]), "", ""))
    if not wanted:
        return [], []
    if not font_path:
        return [], [S.EMOJI_HELP]
    marks = []
    for n, (emoji, cx, cy, size, start, end, anim, boxed) in enumerate(wanted):
        png = render_emoji_png(emoji, size, os.path.join(workdir, f"emoji{n}.png"), font_path)
        if not png:
            continue
        if boxed:
            put_on_box(png, boxed)
        from PIL import Image
        with Image.open(png) as im:
            pw, ph = im.size
        marks.append(EmojiMark(png, int(cx - pw / 2), int(cy - ph / 2), round(start, 3), round(end, 3), anim))
    return marks, []


def prepare(project, w: int, h: int, workdir: str, fonts: dict[str, str] | None = None,
            emoji_font_path: str | None = "auto") -> TextLayers:
    """Write subs.ass and emoji pictures into ``workdir`` (a plain temp folder,
    so ffmpeg never trips over spaces or quotes in paths)."""
    layers = TextLayers()
    text = build_ass(project, w, h)
    if text:
        layers.ass_path = os.path.join(workdir, "subs.ass")
        with open(layers.ass_path, "w", encoding="utf-8") as f:
            f.write(text)
        if fonts:
            fdir = os.path.join(workdir, "fonts")
            os.makedirs(fdir, exist_ok=True)
            for path in fonts.values():
                link = os.path.join(fdir, os.path.basename(path))
                try:
                    os.symlink(path, link)
                except OSError:
                    import shutil
                    shutil.copy2(path, link)
            layers.fonts_dir = fdir
    font_path = S.emoji_font() if emoji_font_path == "auto" else emoji_font_path
    layers.emojis, layers.warnings = emoji_marks(project, w, h, workdir, font_path)
    return layers


def fingerprint_data(project) -> dict:
    """What in the project changes the text layers (for the preview cache)."""
    return {"captions": project.captions, "hook": project.hook, "overlays": popups(project),
            "style": project.caption_style, "channel": project.channel}
