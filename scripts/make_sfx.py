"""
Make the starter sound effects in assets/sfx/<type>/ with ffmpeg.

Every sound is generated from maths (sine waves, noise, fades), so it is our
own and free to use anywhere. Run again any time:  python scripts/make_sfx.py
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_DIR = os.path.join(REPO, "assets", "sfx")

# type -> (seconds, aevalsrc expression, extra filters)
SOUNDS = {
    "whoosh": (0.9, "(random(0)*2-1)*0.7", "highpass=f=500,lowpass=f=3500,afade=t=in:d=0.4,afade=t=out:st=0.45:d=0.45"),
    "boom": (1.4, "0.9*sin(2*PI*(70*t-12*t*t))*exp(-3.5*t)+0.25*(random(0)*2-1)*exp(-12*t)", "lowpass=f=400"),
    "ding": (1.5, "0.55*sin(2*PI*1320*t)*exp(-3*t)+0.25*sin(2*PI*2640*t)*exp(-5*t)", ""),
    "pop": (0.18, "0.9*sin(2*PI*(900*t-6000*t*t))*exp(-28*t)", ""),
    "riser": (2.0, "(0.45*sin(2*PI*(180*t+150*t*t))+0.2*(random(0)*2-1))*t/2", "highpass=f=120,afade=t=out:st=1.85:d=0.15"),
    "record-stop": (0.8, "0.6*sin(2*PI*(420*t-262*t*t))*(1-t/0.8)", "lowpass=f=2500"),
    "drum-hit": (0.6, "0.9*sin(2*PI*(110*t-40*t*t))*exp(-14*t)+0.35*(random(0)*2-1)*exp(-35*t)", ""),
    "error": (0.55, "0.3*(2*gt(sin(2*PI*140*t),0)-1)*lt(mod(t,0.27),0.2)", "lowpass=f=1800"),
    "camera": (0.3, "(random(0)*2-1)*(exp(-90*t)+exp(-90*(t-0.09))*gte(t,0.09))*0.8", "highpass=f=1200"),
    "cash": (1.1, "0.45*sin(2*PI*2093*t)*exp(-6*t)+0.45*sin(2*PI*2637*(t-0.12))*exp(-6*(t-0.12))*gte(t,0.12)", ""),
}

LICENCE = """Sound effects in this folder

Every file here was made for this project by scripts/make_sfx.py from plain
maths (sine waves, noise and fades). They are free to use in any video,
including monetised ones, with no credit needed.

Add your own sounds with "Add your own..." in Clip Editor, or by copying
.wav / .mp3 / .ogg files into a folder here (one folder per type). Only add
sounds you are allowed to use, and write where they came from in
licences.json if they need a credit.
"""


def make(kind: str, out_dir: str = SFX_DIR) -> str:
    seconds, expr, extra = SOUNDS[kind]
    folder = os.path.join(out_dir, kind)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"{kind}-1.wav")
    chain = "aformat=sample_fmts=s16:channel_layouts=mono" + ("," + extra if extra else "")
    chain += ",alimiter=limit=0.89"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc='{expr}':s=44100:d={seconds}", "-af", chain,
                    "-ar", "44100", "-ac", "1", out], check=True)
    return out


def main(argv=None) -> int:
    os.makedirs(SFX_DIR, exist_ok=True)
    for kind in SOUNDS:
        print("made", make(kind))
    with open(os.path.join(SFX_DIR, "LICENCE.txt"), "w", encoding="utf-8") as f:
        f.write(LICENCE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
