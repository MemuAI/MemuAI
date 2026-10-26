"""MemuCode CLI — the coding agent of MemuAI in a terminal.

Run from source: ``python memucode.py``; the installed build is ``MemuCode.exe``
(``memucode`` on PATH). See ``app/cli/main.py`` for the options.
"""
from __future__ import annotations

import os
import sys

def _entry() -> int:
    if getattr(sys, "frozen", False):

        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        if base not in sys.path:
            sys.path.insert(0, base)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
    from app.cli import splash
    splash.show(sys.argv[1:])
    from app.cli.main import main
    return main()

if __name__ == "__main__":
    raise SystemExit(_entry())
