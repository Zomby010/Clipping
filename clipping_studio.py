#!/usr/bin/env python3
"""Clipping Studio: the desktop app. Run:  python clipping_studio.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter  # noqa: F401
except ImportError:
    sys.exit(
        "Clipping Studio needs Tk. On Ubuntu run:  sudo apt install python3-tk\n"
        "then start the app again."
    )

from clipping.gui.app import main

if __name__ == "__main__":
    main()
