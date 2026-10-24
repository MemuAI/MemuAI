"""Crash safety net of MemuFiles (same idea as ``app.memunote.crash``; standard library only).

A windowed exe has nowhere to print a traceback: unhandled errors are appended to
``%LOCALAPPDATA%\\MemuAI\\logs\\memufiles-crash.log`` (``$MEMUAI_DATA_DIR/logs``
in tests) and shown once in a native message box.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional

LOG_NAME = "memufiles-crash.log"
_SHOWN = threading.Event()


def log_dir() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR")
    if env:
        return Path(env) / "logs"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "MemuAI" / "logs"


def log_path() -> Path:
    return log_dir() / LOG_NAME


def write_log(title: str, detail: str) -> Optional[Path]:
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        path = log_path()
        if path.is_file() and path.stat().st_size > 512 * 1024:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            import datetime
            handle.write(f"\n=== {datetime.datetime.now().isoformat(timespec='seconds')} {title}\n{detail}\n")
        return path
    except OSError:
        return None


def message_box(title: str, text: str) -> None:
    if os.name != "nt" or _SHOWN.is_set():
        return
    _SHOWN.set()
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000)
    except Exception:  # noqa: BLE001
        pass


def report(exc_type, exc, tb, where: str = "") -> None:
    import traceback                     # Round 39: only when something crashed — not on every start
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    path = write_log(f"crash {where}", detail)
    en = False
    try:
        from .paths import memuai_preferences
        en = memuai_preferences().get("language") == "en"
    except Exception:  # noqa: BLE001
        pass
    if en:
        message_box("MemuFiles — error", f"MemuFiles could not start.\n\n{exc}\n\nLog: {path}")
    else:
        message_box("MemuFiles — ошибка", f"MemuFiles не смог запуститься.\n\n{exc}\n\nЖурнал: {path}")


def install() -> None:
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return previous(exc_type, exc, tb)
        report(exc_type, exc, tb, "main")
    sys.excepthook = hook

    def thread_hook(args):
        write_log(f"thread {getattr(args.thread, 'name', '')}",
                  "".join(__import__("traceback").format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = thread_hook
