"""A one-line sign of life for the ~half second Python needs before the interface can draw.

Printed by ``memucode.py`` before the heavy imports and erased (carriage return + spaces, which works on
every console) when the full-screen interface takes over. Only for an interactive start.
"""
from __future__ import annotations

import os
import sys
from typing import Sequence

_WIDTH = 0
_QUIET = ("-p", "--print", "-v", "--version", "-h", "--help")
_SUBCOMMANDS = ("doctor", "mcp", "config", "sessions", "models", "schedule", "install", "uninstall", "update", "version")

def show(argv: Sequence[str]) -> None:
    global _WIDTH
    try:
        if any(arg in _QUIET for arg in argv) or (argv and argv[0] in _SUBCOMMANDS):
            return
        if not (sys.stdout and sys.stdout.isatty() and sys.stdin and sys.stdin.isatty()):
            return
        text = "MemuCode is starting…"
        modern = bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or os.environ.get("COLORTERM"))
        sys.stdout.write(("\x1b[2m" + text + "\x1b[0m") if modern else text)
        sys.stdout.flush()
        _WIDTH = len(text)
    except Exception:
        _WIDTH = 0

def clear() -> None:
    global _WIDTH
    if not _WIDTH:
        return
    try:
        sys.stdout.write("\r" + " " * _WIDTH + "\r")
        sys.stdout.flush()
    except Exception:
        pass
    _WIDTH = 0
