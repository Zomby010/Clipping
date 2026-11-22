#!/usr/bin/env python3
"""Clip Editor: pick your own moments, join them, frame them for 9:16, export.
Run:  python clip_editor.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter  # noqa: F401
except ImportError:
    sys.exit(
        "Clip Editor needs Tk. On Ubuntu run:  sudo apt install python3-tk\n"
        "then start the app again."
    )

from clipping.editor.gui.app import main

if __name__ == "__main__":
    main()
