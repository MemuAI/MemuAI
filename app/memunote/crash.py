"""Crash safety net of MemuNote (Round 30).

MemuNote.exe is a windowed build (``console=False``): a traceback has nowhere to
go, and an exception inside the pywebview GUI thread (a .NET thread) used to end
the process silently — the user only saw it blink in Task Manager. Here every
unhandled error, from the main thread, a Python thread or the .NET runtime, is

* appended to ``%LOCALAPPDATA%\\MemuAI\\logs\\memunote-crash.log`` (or
  ``$MEMUAI_DATA_DIR/logs`` in tests/portable runs), and
* shown once in a native message box (ru/en, the language of MemuNote/MemuAI)
  with the log path, instead of a silent exit.

Standard library only: this module is imported before anything else can fail.
"""
from __future__ import annotations

import datetime
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

LOG_NAME = "memunote-crash.log"
MAX_LOG = 512 * 1024
_SHOWN = threading.Event()

TEXTS = {
    "ru": {
        "title": "MemuNote — ошибка запуска",
        "crash": "MemuNote не смог запуститься и будет закрыт.\n\n{error}\n\n"
                 "Подробности записаны в журнал:\n{log}\n\n"
                 "Если ошибка повторяется, перезапустите компьютер или переустановите MemuNote "
                 "(MemuAI → Настройки → MemuNote).",
        "webview2_title": "MemuNote — нужен Microsoft Edge WebView2",
        "webview2": "На этом компьютере нет компонента Microsoft Edge WebView2, в котором MemuNote рисует окно.\n\n"
                    "Сейчас MemuNote откроется в браузере — всё работает, только файлы открываются по пути.\n\n"
                    "Чтобы вернуть обычное окно, установите WebView2 Runtime (бесплатно, ~2 минуты):\n{url}",
        "fallback_title": "MemuNote — окно не открылось",
        "fallback": "Окно MemuNote не удалось создать:\n{error}\n\nMemuNote открыт в браузере. "
                    "Подробности в журнале:\n{log}",
    },
    "en": {
        "title": "MemuNote — start-up error",
        "crash": "MemuNote could not start and will close.\n\n{error}\n\n"
                 "Details were written to the log:\n{log}\n\n"
                 "If it keeps happening, restart the computer or reinstall MemuNote "
                 "(MemuAI → Settings → MemuNote).",
        "webview2_title": "MemuNote — Microsoft Edge WebView2 is required",
        "webview2": "This computer has no Microsoft Edge WebView2, the component MemuNote draws its window with.\n\n"
                    "MemuNote will open in your browser now — everything works, files are opened by path.\n\n"
                    "To get the normal window back, install the WebView2 Runtime (free, ~2 minutes):\n{url}",
        "fallback_title": "MemuNote — the window did not open",
        "fallback": "The MemuNote window could not be created:\n{error}\n\nMemuNote is open in your browser. "
                    "Details are in the log:\n{log}",
    },
}

WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

def log_dir() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env) / "logs"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "MemuAI" / "logs"

def log_path() -> Path:
    return log_dir() / LOG_NAME

def language() -> str:
    """MemuNote's own language setting, else MemuAI's, else Russian (never raises)."""
    try:
        from .paths import memuai_preferences, note_dir, read_json
        value = read_json(note_dir() / "settings.json", {}).get("language")
        if value in ("ru", "en"):
            return value
        return "en" if memuai_preferences().get("language") == "en" else "ru"
    except Exception:  # noqa: BLE001
        return "ru"

def text(key: str, **values: str) -> str:
    return TEXTS[language()][key].format(**values)

def write_log(title: str, detail: str) -> Optional[Path]:
    """Append one entry (timestamp, version, argv, traceback). Returns the log path or None."""
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_LOG:
            path.replace(path.with_suffix(".old.log"))
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        head = (f"==== {stamp} {title} | python {sys.version.split()[0]} | frozen={bool(getattr(sys, 'frozen', False))}"
                f" | argv={sys.argv[1:]!r}\n")
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(head + detail.rstrip() + "\n\n")
        return path
    except OSError:
        return None

def message_box(title: str, body: str, error: bool = True) -> None:
    """A native message box on Windows; stderr elsewhere (or when it is None in a windowed build)."""
    if os.name == "nt":
        try:
            import ctypes
            flags = (0x10 if error else 0x40) | 0x10000 | 0x40000
            ctypes.windll.user32.MessageBoxW(0, body, title, flags)
            return
        except Exception:  # noqa: BLE001
            pass
    stream = sys.stderr
    if stream is not None:
        try:
            stream.write(f"{title}\n{body}\n")
            stream.flush()
        except Exception:  # noqa: BLE001
            pass

def _short(exc_type, exc) -> str:
    name = getattr(exc_type, "__name__", str(exc_type))
    return f"{name}: {exc}"[:600]

def report(exc_type, exc, tb, where: str = "main", show: bool = True) -> None:
    """Log an unhandled error and tell the user once (later errors only go to the log)."""
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    path = write_log(f"unhandled error ({where})", detail)
    if show and not _SHOWN.is_set():
        _SHOWN.set()
        message_box(text("title"), text("crash", error=_short(exc_type, exc), log=str(path or log_path())))

def report_text(where: str, detail: str, short: str, show: bool = True) -> None:
    """Same as :func:`report` for errors that arrive as text (the .NET runtime)."""
    path = write_log(f"unhandled error ({where})", detail)
    if show and not _SHOWN.is_set():
        _SHOWN.set()
        message_box(text("title"), text("crash", error=short[:600], log=str(path or log_path())))

def install() -> None:
    """Route unhandled errors of the main thread and Python threads to :func:`report`."""
    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        report(exc_type, exc, tb, "main")

    def thread_hook(args) -> None:
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "thread")

        report(args.exc_type, args.exc_value, args.exc_traceback, f"thread {name}", show=False)

    sys.excepthook = hook
    threading.excepthook = thread_hook

def install_dotnet() -> None:
    """After pythonnet is loaded: log + show .NET exceptions that would otherwise kill the process silently.

    ``AppDomain.UnhandledException`` cannot keep the process alive (the runtime
    terminates it right after the handler returns) — but the user finally sees why.
    """
    if os.name != "nt":
        return
    try:
        import System  # type: ignore

        def on_unhandled(_sender, event) -> None:
            exc = getattr(event, "ExceptionObject", None)
            report_text(".NET", str(exc), f"{type(exc).__name__}: {getattr(exc, 'Message', exc)}")

        System.AppDomain.CurrentDomain.UnhandledException += System.UnhandledExceptionEventHandler(on_unhandled)
    except Exception as exc:  # noqa: BLE001 - no pythonnet / older runtime: Python hooks still work
        write_log("dotnet hook unavailable", f"{type(exc).__name__}: {exc}")
