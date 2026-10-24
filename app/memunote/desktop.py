"""MemuNote window: pywebview (WinForms + WebView2 on Windows), single instance, native glue.

Start-up path (kept short on purpose — MemuNote must open fast):
``memunote.py`` → :func:`main` → hand off to a running instance if there is one
(``instance.json`` + ``/api/remote``) → start the stdlib HTTP server → create
the window. Heavy modules (formats, AI, memory) load on first use.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_NAME
from .paths import note_dir, read_json, write_json

log = logging.getLogger("memunote")

INSTANCE_FILE = "instance.json"


# ------------------------------------------------------------------------- single instance

def _instance_path() -> Path:
    return note_dir() / INSTANCE_FILE


def _remote(action: str, payload: Dict[str, Any], timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """POST ``/api/remote`` of the running instance; its JSON reply, or None (no instance / stale file)."""
    data = read_json(_instance_path(), {})
    port, token = data.get("port"), data.get("token")
    if not isinstance(port, int) or not token:
        return None
    body = json.dumps({"action": action, **payload}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/remote", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-MN-Token": str(token),
                                              "Host": f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            reply = json.loads(response.read(65536).decode("utf-8") or "{}")
            return reply if isinstance(reply, dict) else {}
    except Exception:  # noqa: BLE001 - a stale file: we become the instance
        return None


STARTING_GRACE = 45.0      # seconds a starting instance may take to show its window before it counts as a ghost


def instance_alive(reply: Optional[Dict[str, Any]]) -> bool:
    """Does the running instance have a UI that can take the files?

    ``ui`` is ``window`` / ``browser`` / ``headless`` once usable and ``starting``
    until the window is shown. An instance stuck in ``starting`` (its window
    failed but the process lived on) is a ghost: handing files to it is what
    made every later start blink and vanish. Round-29 instances send no ``ui``.
    """
    if reply is None:
        return False
    ui = reply.get("ui")
    if ui is None or ui in ("window", "browser", "headless"):
        return True
    try:
        age = float(reply.get("age") or 0)
    except (TypeError, ValueError):
        age = 0.0
    return ui == "starting" and age < STARTING_GRACE


def hand_off(files: List[str]) -> bool:
    """Send *files* to an already running MemuNote. True when it took them; a ghost is shut down instead."""
    reply = _remote("ping", {})
    if reply is None:
        return False
    if not instance_alive(reply):
        log.warning("MemuNote instance %s has no window (%s) — replacing it", reply.get("pid"), reply.get("ui"))
        _remote("shutdown", {}, timeout=3.0)
        return False
    return _remote("open", {"paths": files}) is not None


def publish_instance(port: int, token: str) -> None:
    write_json(_instance_path(), {"port": port, "token": token, "pid": os.getpid(), "started": int(time.time())})


def clear_instance(port: int) -> None:
    data = read_json(_instance_path(), {})
    if data.get("port") == port and data.get("pid") == os.getpid():
        try:
            _instance_path().unlink()
        except OSError:
            pass


# ------------------------------------------------------------------------- window glue

class Desktop:
    """Bridge between the HTTP backend and the pywebview window (dialogs, PDF, window commands)."""

    def __init__(self, webview_module: Any, window: Any, theme_mode: str) -> None:
        self.webview = webview_module
        self.window = window
        self.theme_mode = theme_mode
        self.closing_ok = False

    # ---- native handles
    def _native(self):
        try:
            from webview.platforms.winforms import BrowserView   # type: ignore
            return BrowserView.instances.get(self.window.uid)
        except Exception:  # noqa: BLE001
            return None

    def hwnd(self) -> int:
        native = self._native()
        try:
            if native is not None and bool(getattr(native, "IsHandleCreated", True)):
                return int(native.Handle.ToInt64())
        except Exception:  # noqa: BLE001
            pass
        return 0

    def _invoke(self, fn):
        native = self._native()
        if native is None:
            return fn()
        if not getattr(native, "InvokeRequired", False):
            return fn()
        box: Dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
        from System import Action  # type: ignore
        native.Invoke(Action(run))
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def apply_theme(self, mode: str) -> None:
        self.theme_mode = mode
        hwnd = self.hwnd()
        if hwnd:
            from .. import win_window_theme
            self._invoke(lambda: win_window_theme.apply(self._native(), hwnd, mode == "dark", _theme_background(mode)))

    # ---- dialogs
    def file_dialog(self, mode: str, directory: str, filename: str, filters: List[str], multiple: bool) -> List[str]:
        wv = self.webview
        dialog = getattr(wv, "FileDialog", None)
        kinds = {
            "open": getattr(dialog, "OPEN", None) if dialog else getattr(wv, "OPEN_DIALOG", 10),
            "save": getattr(dialog, "SAVE", None) if dialog else getattr(wv, "SAVE_DIALOG", 30),
            "folder": getattr(dialog, "FOLDER", None) if dialog else getattr(wv, "FOLDER_DIALOG", 20),
        }
        kind = kinds.get(mode, kinds["open"])
        file_types = tuple(str(f) for f in filters if isinstance(f, str) and "(" in f and ")" in f)[:40]
        kwargs: Dict[str, Any] = {"directory": directory or "", "allow_multiple": bool(multiple and mode == "open")}
        if mode == "save":
            kwargs["save_filename"] = filename or ""
        if file_types:
            kwargs["file_types"] = file_types
        try:
            result = self.window.create_file_dialog(kind, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a bad filter string must not break the dialog
            log.warning("file dialog with filters failed: %s", exc)
            kwargs.pop("file_types", None)
            result = self.window.create_file_dialog(kind, **kwargs)
        if not result:
            return []
        if isinstance(result, str):
            return [result]
        return [str(item) for item in result]

    # ---- window
    def window_command(self, cmd: str, body: Dict[str, Any]) -> None:
        win = self.window
        try:
            if cmd == "title":
                win.set_title(str(body.get("title") or APP_NAME)[:200])
            elif cmd == "activate":
                win.restore()
                win.show()
                from . import winsys
                winsys.bring_to_front(self.hwnd())
            elif cmd == "fullscreen":
                win.toggle_fullscreen()
            elif cmd == "on_top":
                win.on_top = bool(body.get("value"))
            elif cmd == "minimize":
                win.minimize()
            elif cmd == "theme":
                self.apply_theme(str(body.get("mode") or "dark"))
            elif cmd == "close":
                self.closing_ok = True
                win.destroy()
            elif cmd == "zoom":
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("window command %s failed: %s", cmd, exc)

    def force_close(self) -> None:
        self.closing_ok = True
        # A window that never finished starting may ignore destroy(): never leave a process behind.
        timer = threading.Timer(6.0, os._exit, args=(0,))
        timer.daemon = True
        timer.start()
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            os._exit(0)

    # ---- PDF
    def print_to_pdf(self, path: str):
        """WebView2 ``PrintToPdfAsync`` of the current page (print CSS applies). (ok, error)."""
        native = self._native()
        if native is None:
            return False, "print_unavailable"
        done = threading.Event()
        box: Dict[str, Any] = {}

        def start() -> None:
            browser = getattr(native, "browser", None)
            control = getattr(browser, "webview", None)
            core = getattr(control, "CoreWebView2", None)
            if core is None:
                raise RuntimeError("CoreWebView2 is not ready")
            settings = None
            try:
                settings = core.Environment.CreatePrintSettings()
                settings.ShouldPrintBackgrounds = True
                settings.ShouldPrintHeaderAndFooter = False
            except Exception:  # noqa: BLE001 - older runtime: default settings
                settings = None
            task = core.PrintToPdfAsync(path, settings)

            def finished(t) -> None:
                try:
                    box["ok"] = bool(t.Result)
                except Exception as exc:  # noqa: BLE001
                    box["error"] = str(exc)
                done.set()
            def poll() -> None:          # the .NET Task is polled: no delegate marshalling needed
                for _ in range(1200):
                    if task.IsCompleted:
                        finished(task)
                        return
                    time.sleep(0.05)
                box["error"] = "timeout"
                done.set()
            threading.Thread(target=poll, daemon=True).start()
        try:
            self._invoke(start)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        if not done.wait(60):
            return False, "timeout"
        if box.get("error"):
            return False, box["error"]
        return bool(box.get("ok")), "" if box.get("ok") else "print_failed"


# ------------------------------------------------------------------------- main

def _theme_background(mode: str) -> str:
    return "#1a1a1c" if mode != "light" else "#fbfaf8"


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    files, flags = [], set()
    for item in argv:
        if item.startswith("--"):
            flags.add(item)
        elif item:
            files.append(os.path.abspath(item))
    return {"files": files, "flags": flags}


def _say(text: str) -> None:
    """print() that survives a windowed build, where ``sys.stdout`` is None."""
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:  # noqa: BLE001
        pass


def _setup_logging(debug: bool) -> None:
    level = logging.INFO if debug else logging.WARNING
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    if sys.stderr is not None:
        logging.basicConfig(level=level, format=fmt)
        return
    from .crash import log_dir         # windowed build: warnings go to %LOCALAPPDATA%\MemuAI\logs\memunote.log
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=level, format=fmt, filename=str(log_dir() / "memunote.log"), encoding="utf-8")
    except OSError:
        logging.basicConfig(level=level, format=fmt, handlers=[logging.NullHandler()])


def _load_webview() -> Tuple[Any, str]:
    """``(webview module, "")`` or ``(None, reason)``: ``webview2`` (runtime missing) or ``pywebview: …``."""
    if os.name == "nt":
        from . import winsys
        if not winsys.webview2_version():
            return None, "webview2"
    try:
        import webview  # type: ignore
    except Exception as exc:  # noqa: BLE001 - ImportError, or pythonnet failing to load .NET
        log.exception("pywebview is unavailable")
        return None, f"pywebview: {type(exc).__name__}: {exc}"
    return webview, ""


BROWSER_IDLE_EXIT = 180.0       # the page long-polls every ≤25 s; background tabs may be throttled to 1/min
BROWSER_FIRST_VISIT = 300.0


def _serve_in_browser(app: Any, url: str, reason: str, detail: str = "") -> int:
    """No window possible: open MemuNote in the default browser and live while the page is open."""
    import webbrowser
    from . import crash
    app.ui = "browser"
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("browser start failed: %s", exc)
    if reason == "webview2":
        title, body = crash.text("webview2_title"), crash.text("webview2", url=crash.WEBVIEW2_URL)
    else:
        path = crash.write_log("window unavailable — opened in the browser", detail or reason)
        title, body = crash.text("fallback_title"), crash.text("fallback", error=reason[:400],
                                                               log=str(path or crash.log_path()))
    threading.Thread(target=crash.message_box, args=(title, body, False), name="memunote-notice", daemon=True).start()
    started = time.monotonic()
    while not app.stopping.is_set():
        app.stopping.wait(5.0)
        now = time.monotonic()
        seen = app.last_seen
        if (seen and now - seen > BROWSER_IDLE_EXIT) or (not seen and now - started > BROWSER_FIRST_VISIT):
            break
    return 0


def _serve_headless(app: Any) -> int:
    app.ui = "headless"
    try:
        while not app.stopping.is_set():
            app.stopping.wait(3600)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    flags = args["flags"]
    if "--unregister-open-with" in flags:
        from . import winsys
        winsys.unregister()
        winsys.clean_script_choices()   # Round 31: a 3.5 that never started has not migrated yet
        winsys.forget_state()
        return 0
    if "--migrate-open-with" in flags:
        from . import winsys
        winsys.migrate()
        return 0
    if "--register-open-with" in flags:
        from . import winsys
        winsys.register_open_with(True)
        return 0
    if "--shutdown" in flags:
        _remote("shutdown", {}, timeout=5.0)
        return 0
    if "--new-instance" not in flags and hand_off(args["files"]):
        return 0

    _setup_logging("--debug" in flags)
    if os.name == "nt":       # Round 31: clean up the Round 29–30 "Open with" registration once (no-op afterwards)
        threading.Thread(target=_migrate_assoc, name="memunote-assoc", daemon=True).start()
    from . import crash, server
    app = server.NoteApp(args["files"])
    httpd, _thread = server.start(app)
    publish_instance(app.port, app.token)
    url = f"http://127.0.0.1:{app.port}/index.html#t={app.token}"
    if "--print-url" in flags:
        _say(url)
    try:
        if "--no-window" in flags:      # development / tests: open the URL in a browser yourself
            return _serve_headless(app)
        webview, reason = _load_webview()
        if webview is None:
            return _serve_in_browser(app, url, reason)
        error, detail = _run_window(webview, app, flags, url)
        if error and app.ui == "starting":   # the window never appeared: the browser still works
            return _serve_in_browser(app, url, error, detail)
        if error:
            crash.write_log("window failed", detail)
        return 0
    finally:
        clear_instance(app.port)
        try:
            httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _migrate_assoc() -> None:
    try:
        from . import winsys
        result = winsys.migrate()
        if result.get("migrated"):
            log.warning("file associations cleaned up: %s", result)
    except Exception as exc:  # noqa: BLE001 - never block the start
        log.warning("association clean-up failed: %s", exc)


def _run_window(webview: Any, app: Any, flags: set, url: str) -> Tuple[str, str]:
    """Create the pywebview window and run its loop: ``("", "")`` on a normal close, else (error, traceback)."""
    from . import crash
    crash.install_dotnet()
    try:
        _open_window(webview, app, flags, url)
    except Exception as exc:  # noqa: BLE001 - logged; the caller falls back to the browser
        log.exception("window failed")
        app.desktop = None
        return f"{type(exc).__name__}: {exc}", traceback.format_exc()
    return "", ""


def _open_window(webview: Any, app: Any, flags: set, url: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MemuAI.MemuNote")
        except Exception:  # noqa: BLE001
            pass
    mode = app.theme()["mode"]
    areas: List[tuple] = []
    if os.name == "nt":
        from . import winsys
        areas = winsys.monitor_work_areas()
    geometry = sane_geometry(read_json(note_dir() / "window.json", {}), areas)
    window = webview.create_window(
        APP_NAME, url, width=geometry["width"], height=geometry["height"],
        x=geometry["x"], y=geometry["y"], min_size=(520, 360),
        background_color=_theme_background(mode), text_select=True, confirm_close=False, zoomable=True,
        maximized=bool(geometry.get("maximized")))
    desktop = Desktop(webview, window, mode)
    app.desktop = desktop

    def on_shown() -> None:
        app.ui = "window"
        desktop.apply_theme(mode)
        if os.name == "nt":            # last line of defence: never stay parked off every monitor
            from . import winsys
            winsys.ensure_on_screen(desktop.hwnd())

    def on_closing() -> bool:
        # Unsaved buffers are already in recovery/; the page saves the session on every change,
        # so closing never loses text. Remember the window geometry.
        try:
            native = desktop._native()
            state = str(native.WindowState) if native is not None else ""
            previous = read_json(note_dir() / "window.json", {})
            data = closing_geometry(state, (window.x, window.y, window.width, window.height),
                                    _normal_rect(desktop), previous)
            write_json(note_dir() / "window.json", data)
        except Exception:  # noqa: BLE001
            pass
        app.events.push({"type": "closing"})
        return True

    def on_loaded() -> None:
        _attach_drop(window, app)

    try:
        window.events.shown += on_shown
        window.events.closing += on_closing
        window.events.loaded += on_loaded
    except Exception:  # noqa: BLE001
        pass
    options = {"private_mode": False, "storage_path": str(note_dir() / "webview"), "icon": _icon_path(),
               "debug": "--debug" in flags}
    try:
        accepted = inspect.signature(webview.start).parameters
        options = {k: v for k, v in options.items() if k in accepted and v is not None}
    except (TypeError, ValueError):
        options = {"debug": options["debug"]}
    webview.start(**options)


def _normal_rect(desktop: "Desktop") -> Optional[tuple]:
    if os.name != "nt":
        return None
    from . import winsys
    return winsys.normal_rect(desktop.hwnd())


PARKED = -30000     # Windows reports a minimised window at (-32000, -32000)


def _int(value: Any) -> Optional[int]:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def sane_geometry(saved: Any, areas: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """window.json → create_window arguments that always put the window on a real screen.

    Drops a position from a minimised window (≈ -32000), garbage values and, when the
    monitor list is known, a position whose caption is on no monitor (a disconnected
    display). ``x``/``y`` of ``None`` lets the window centre itself.
    """
    saved = saved if isinstance(saved, dict) else {}
    width = max(560, min(_int(saved.get("width")) or 1180, 6000))
    height = max(400, min(_int(saved.get("height")) or 780, 4000))
    x, y = _int(saved.get("x")), _int(saved.get("y"))
    if x is None or y is None or x <= PARKED or y <= PARKED or abs(x) > 40000 or abs(y) > 40000:
        x = y = None
    elif areas:
        from .winsys import rect_visible
        if not rect_visible(x, y, width, areas):
            x = y = None
    if areas and x is None:          # a saved size bigger than every screen would hang off it
        width = min(width, max(560, max(a[2] - a[0] for a in areas)))
        height = min(height, max(400, max(a[3] - a[1] for a in areas)))
    return {"width": width, "height": height, "x": x, "y": y, "maximized": bool(saved.get("maximized"))}


def closing_geometry(state: str, current: tuple, normal: Optional[tuple], previous: Any) -> Dict[str, Any]:
    """What to remember on close. Minimised/maximised windows store their *restored* rectangle."""
    previous = previous if isinstance(previous, dict) else {}
    maximized = state == "Maximized" or (state == "Minimized" and bool(previous.get("maximized")))
    data: Dict[str, Any] = {"maximized": maximized}
    rect = None
    if normal and all(isinstance(v, int) for v in normal) and normal[0] > PARKED and normal[1] > PARKED:
        rect = normal
    elif state not in ("Minimized", "Maximized"):
        x, y, w, h = (_int(v) for v in current)
        if None not in (x, y, w, h) and x > PARKED and y > PARKED:
            rect = (x, y, w, h)
    if rect:
        data.update(x=rect[0], y=rect[1], width=rect[2], height=rect[3])
    else:                             # keep the last good placement rather than a parked one
        for key in ("x", "y", "width", "height"):
            v = _int(previous.get(key))
            if v is not None and not (key in ("x", "y") and v <= PARKED):
                data[key] = v
    return data


def _icon_path(system: str = os.name, frozen: Optional[bool] = None) -> Optional[str]:
    """The window icon handed to pywebview.

    Windows: ``.ico`` only — pywebview ≥ 6.2 builds ``System.Drawing.Icon(path)``
    from it in the GUI thread, and a PNG there throws a .NET exception that kills
    the process before the window shows (the Round-29 "blinks in Task Manager"
    bug). A frozen MemuNote.exe needs none: pywebview takes the exe's own icon.
    GTK/Qt take the PNG.
    """
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if system == "nt" and frozen:
        return None
    names = ("memunote-icon.ico",) if system == "nt" else ("memunote-icon.png", "memuai-icon.png", "icon.png")
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for name in names:
        for folder in (base, base / "app" / "memunote" / "static"):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def _attach_drop(window: Any, app: Any) -> None:
    """Files dropped from Explorer: pywebview gives full paths (``pywebviewFullPath``) on the drop event."""
    try:
        from webview.dom import DOMEventHandler  # type: ignore
    except Exception:  # noqa: BLE001
        return

    def on_drop(event: Dict[str, Any]) -> None:
        try:
            files = event.get("dataTransfer", {}).get("files", []) or []
            paths = [f.get("pywebviewFullPath") for f in files if isinstance(f, dict) and f.get("pywebviewFullPath")]
            if paths:
                app.events.push({"type": "open", "paths": paths})
        except Exception as exc:  # noqa: BLE001
            log.warning("drop failed: %s", exc)

    try:
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception as exc:  # noqa: BLE001
        log.debug("drop handler unavailable: %s", exc)
