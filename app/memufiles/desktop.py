"""MemuFiles window: pywebview (WinForms + WebView2 on Windows), single instance, native glue.

Mirrors ``app.memunote.desktop`` (same instance hand-off, the same safe window
geometry and Windows helpers from ``app.memunote.winsys``) so both apps behave
alike. ``MemuFiles.exe [folders/files…] [--new-instance] [--shutdown]
[--no-window --print-url] [--debug] [--shell-open <folder>] [--restore-explorer]``. A second start opens its folders as new
tabs in the running window.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_NAME
from .paths import files_dir, read_json, write_json

class _LazyLog:
    """Round 39: ``logging`` (and the ``traceback`` it imports) stays off the hand-off path — a second start that
    only passes a folder to the running window never logs anything."""

    def __getattr__(self, name: str) -> Any:
        import logging
        return getattr(logging.getLogger("memufiles"), name)


log = _LazyLog()
INSTANCE_FILE = "instance.json"


def _instance_path() -> Path:
    return files_dir() / INSTANCE_FILE


def _remote(action: str, payload: Dict[str, Any], timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """POST ``/api/remote`` to the running instance over a plain loopback socket.

    Round 39: no ``urllib.request`` (it pulled http.client/email/ssl — ~25 ms of imports on every second start,
    which is now the main path: Win+E and double-clicking a folder). ``None`` = no live instance."""
    data = read_json(_instance_path(), {})
    port, token = data.get("port"), data.get("token")
    if not isinstance(port, int) or not token:
        return None
    import socket
    body = json.dumps({"action": action, **payload}).encode("utf-8")
    head = (f"POST /api/remote HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
            f"X-MF-Token: {token}\r\nContent-Length: {len(body)}\r\n\r\n").encode("latin-1", "replace")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(head + body)
            chunks: List[bytes] = []
            size = 0
            while size < 65536:
                chunk = sock.recv(16384)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        raw = b"".join(chunks)
        status_line, _, rest = raw.partition(b"\r\n")
        if b" 200 " not in status_line + b" ":
            return None
        reply = json.loads(rest.partition(b"\r\n\r\n")[2].decode("utf-8") or "{}")
        return reply if isinstance(reply, dict) else {}
    except Exception:  # noqa: BLE001 - a stale file: we become the instance
        return None


def hand_off(paths: List[str], reveal: Optional[List[str]] = None, shell: bool = False, browse: bool = False) -> bool:
    """Give *paths* to the running instance in ONE request (Round 39; was ping + open). The instance refuses when it
    is a ghost (a window that never showed) — then it is shut down and this process becomes the instance.
    Round 41: ``reveal`` — paths opened like ``explorer /select,`` (parent + selection); ``shell`` — from the folder
    handler (a browser's «Show in folder»)."""
    payload: Dict[str, Any] = {"paths": paths, "require_ui": True}
    if reveal:
        payload["reveal"] = reveal
    if shell:
        payload["shell"] = True
    if browse:
        payload["browse"] = True              # r41-files: «--archive x.zip» — the archive opens like a folder
    reply = _remote("open", payload)
    if reply is None:
        return False
    if reply.get("taken") is False:
        _remote("shutdown", {}, timeout=3.0)
        return False
    return True


def open_path(path: str) -> bool:
    """Show a folder/file in MemuFiles from the MemuAI process (Quick Access file search, Alt+Enter —
    ``app/fsindex_api.open_in_memufiles`` looks this name up). The running window gets a new tab
    (a file: its folder with the file selected); otherwise the installed MemuFiles is started."""
    if hand_off([path]):
        return True
    from .. import memufiles_manager
    return bool(memufiles_manager.open_app([path]).get("ok"))


def publish_instance(port: int, token: str) -> None:
    write_json(_instance_path(), {"port": port, "token": token, "pid": os.getpid(), "started": int(time.time())})


def clear_instance(port: int) -> None:
    data = read_json(_instance_path(), {})
    if data.get("port") == port and data.get("pid") == os.getpid():
        try:
            _instance_path().unlink()
        except OSError:
            pass


class Desktop:
    def __init__(self, webview_module: Any, window: Any, theme_mode: str, titlebar: Any = None) -> None:
        self.webview = webview_module
        self.window = window
        self.theme_mode = theme_mode
        self.titlebar = titlebar
        self._hwnd = 0           # cached on the UI thread (``shown``): request threads must not touch .NET Handle
        self.quitting = False
        self.dnd_paths = False   # r40-files-core: pywebview reports full paths of files dropped from Explorer
        self.taskbar: Any = None  # r40-files-core: operation progress on the taskbar button (taskbar.py)

    def _native(self):
        try:
            from webview.platforms.winforms import BrowserView   # type: ignore
            return BrowserView.instances.get(self.window.uid)
        except Exception:  # noqa: BLE001
            return None

    def file_drag(self, paths: List[str]) -> Dict[str, Any]:
        """Export real files through OLE, on the WinForms STA thread.

        Chromium's text/plain and DownloadURL are not a shell file drop: chat
        applications paste those as text. FileDrop carries CF_HDROP instead.
        Copy is the only advertised effect; exporting never deletes a source.
        """
        if os.name != "nt":
            return {"ok": False, "error": "unsupported"}
        if not isinstance(paths, list) or not paths or len(paths) > 20000:
            return {"ok": False, "error": "bad_paths"}
        files = []
        for path in paths:
            if not isinstance(path, str) or "\0" in path or not os.path.isabs(path) or not os.path.exists(path):
                return {"ok": False, "error": "not_found"}
            files.append(os.path.abspath(path))
        native = self._native()
        if native is None:
            return {"ok": False, "error": "unavailable"}
        try:
            from System.Collections.Specialized import StringCollection  # type: ignore
            from System.Windows.Forms import DataObject, DragDropEffects  # type: ignore
            from ..win_titlebar import winforms_ui_invoker

            def run() -> int:
                data = DataObject()
                collection = StringCollection()
                for path in files:
                    collection.Add(path)
                data.SetFileDropList(collection)
                data.SetData("application/x-memufiles", json.dumps(files))
                return int(native.DoDragDrop(data, DragDropEffects.Copy))

            effect = winforms_ui_invoker(native)(run)
            return {"ok": True, "effect": effect}
        except Exception as exc:  # noqa: BLE001
            log.warning("file drag failed: %s", exc)
            return {"ok": False, "error": "drag_failed"}

    def hwnd(self) -> int:
        if self._hwnd:
            return self._hwnd
        native = self._native()
        try:
            if native is not None and bool(getattr(native, "IsHandleCreated", True)):
                return int(native.Handle.ToInt64())
        except Exception:  # noqa: BLE001
            pass
        return 0

    def apply_theme(self, mode: str) -> None:
        self.theme_mode = mode
        if os.name != "nt":
            return
        hwnd = self.hwnd()
        if hwnd:
            from .. import win_window_theme
            from ..win_titlebar import winforms_ui_invoker
            native = self._native()
            winforms_ui_invoker(native)(lambda: win_window_theme.apply(native, hwnd, mode == "dark",
                "#17171a" if mode != "light" else "#f7f6f3"))

    def window_command(self, cmd: str, body: Dict[str, Any]) -> None:
        win = self.window
        try:
            if cmd == "title":
                win.set_title(str(body.get("title") or APP_NAME)[:200])
            elif cmd == "activate":
                win.restore()
                win.show()
                if os.name == "nt":
                    from ..memunote import winsys
                    winsys.bring_to_front(self.hwnd())
            elif cmd == "fullscreen":
                win.toggle_fullscreen()
            elif cmd == "minimize":
                win.minimize()
            elif cmd in ("maximize", "toggle") and self.titlebar is not None and self.titlebar.command(cmd):
                pass
            elif cmd == "theme":
                self.apply_theme(str(body.get("mode") or "dark"))
            elif cmd == "close":
                win.destroy()
        except Exception as exc:  # noqa: BLE001
            log.warning("window command %s failed: %s", cmd, exc)

    def force_close(self) -> None:
        self.quitting = True                 # Round 39: a real quit, even with «keep ready in the background»
        timer = threading.Timer(6.0, os._exit, args=(0,))
        timer.daemon = True
        timer.start()
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            os._exit(0)


_EXPLORER_SWITCH = re.compile(r"^/(select|e|n|root|separate|idlist)(?:,(.*))?$", re.I | re.S)


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    """Round 41: Explorer's own switches work too — ``/select,"C:\\a\\b.txt"`` (also split as ``/select,`` ``path``),
    ``--select <path>`` / ``--select=<path>``, ``/e,<folder>``, ``/n``, ``/root,<folder>`` — so «Show in folder» from
    other apps (they run ``explorer /select,path``) and scripts reach MemuFiles with the item selected."""
    paths: List[str] = []
    flags = set()
    raw: List[str] = []
    reveal: List[str] = []
    want_select = False
    items = list(argv)
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if not item:
            continue
        if item.startswith("--select"):
            value = item.split("=", 1)[1] if "=" in item else (items[i] if i < len(items) else "")
            if "=" not in item:
                i += 1
            if value:
                full = os.path.abspath(value.strip().strip('"'))
                paths.append(full)
                reveal.append(full)
            continue
        if item.startswith("--"):
            flags.add(item)
            continue
        switch = _EXPLORER_SWITCH.match(item) if not os.path.exists(item) else None
        if switch:
            name, rest = switch.group(1).lower(), (switch.group(2) or "").strip().strip('"').strip()
            if name == "select":
                want_select = True
            if rest and name in ("select", "e", "root"):
                item = rest
            else:
                continue
        item = item.strip().strip('"') if item.endswith('"') or item.startswith('"') else item
        raw.append(item)
        if "::" not in item and not item.lower().startswith("shell:"):
            full = os.path.abspath(item)
            paths.append(full)
            if want_select:
                reveal.append(full)
                want_select = False
    return {"paths": paths, "flags": flags, "raw": raw, "reveal": reveal}


def _shell_open(raw: List[str]) -> Optional[List[str]]:
    """``--shell-open`` (the folder handler in the registry, Round 38): what MemuFiles cannot show goes to the
    real Explorer through the untouched ``explore`` verb; ``None`` = nothing left for MemuFiles to open."""
    from . import default_shell
    kind, target = default_shell.route(raw[0] if raw else "")
    if kind == "explorer":
        default_shell.explore(target)
        return None
    return [target or os.path.expanduser("~")]      # Win+E (no folder): a new tab, like a new Explorer window


def _win_shell_extras(app: Any) -> None:
    """Round 38, Windows: re-point the folder handler if MemuFiles.exe moved; the optional Win+E hook."""
    from . import default_shell
    try:
        default_shell.heal()
        from . import settings as settings_store
        if settings_store.load().get("open_archives"):     # r41-files: re-point the archive association if the exe moved
            from . import archive_assoc
            archive_assoc.sync(True)
        if default_shell.load_state().get("hook"):
            from . import winkey
            winkey.start(lambda: _remote("open", {"paths": [os.path.expanduser("~")]}))
    except Exception:  # noqa: BLE001 - never block start-up
        log.exception("default shell check failed")


def _say(text: str) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:  # noqa: BLE001
        pass


def _setup_logging(debug: bool) -> None:
    import logging
    level = logging.INFO if debug else logging.WARNING
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    if sys.stderr is not None:
        logging.basicConfig(level=level, format=fmt)
        return
    from .crash import log_dir
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=level, format=fmt, filename=str(log_dir() / "memufiles.log"), encoding="utf-8")
    except OSError:
        logging.basicConfig(level=level, format=fmt, handlers=[logging.NullHandler()])


def _load_webview() -> Tuple[Any, str]:
    if os.name == "nt":
        from ..memunote import winsys
        if not winsys.webview2_version():
            return None, "webview2"
    try:
        import webview  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.exception("pywebview is unavailable")
        return None, f"pywebview: {type(exc).__name__}: {exc}"
    return webview, ""


def _serve_in_browser(app: Any, url: str) -> int:
    import webbrowser
    app.ui = "browser"
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("browser start failed: %s", exc)
    started = time.monotonic()
    while not app.stopping.is_set():
        app.stopping.wait(5.0)
        now = time.monotonic()
        seen = app.last_seen
        if (seen and now - seen > 180) or (not seen and now - started > 300):
            break
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    flags = args["flags"]
    if "--shutdown" in flags:
        _remote("shutdown", {}, timeout=5.0)
        return 0
    if "--restore-explorer" in flags:             # emergency rollback / the uninstaller (Round 38)
        from .default_shell import cli_restore
        return cli_restore()
    if "--shell-open" in flags:
        opened = _shell_open(args["raw"])
        if opened is None:
            return 0
        args["paths"] = opened
    shell = "--shell-open" in flags and bool(args["raw"])
    browse = "--archive" in flags                 # r41-files: the archive file association (archive_assoc.py)
    if "--new-instance" not in flags and hand_off(args["paths"], args.get("reveal"), shell, browse):
        return 0
    _setup_logging("--debug" in flags)
    from . import server
    app = server.FilesApp(args["paths"], reveal=args.get("reveal"), shell=args["paths"] if shell else None,
                          browse=args["paths"] if browse else None)
    port = 0
    for item in flags:
        if item.startswith("--port="):
            try:
                port = int(item.split("=", 1)[1])
            except ValueError:
                port = 0
    httpd, _thread = server.start(app, port)
    publish_instance(app.port, app.token)
    if os.name == "nt":
        threading.Thread(target=_win_shell_extras, args=(app,), name="memufiles-shell", daemon=True).start()
    url = f"http://127.0.0.1:{app.port}/index.html#t={app.token}"
    if "--print-url" in flags:
        _say(url)
    try:
        if "--no-window" in flags:
            app.ui = "headless"
            try:
                while not app.stopping.is_set():
                    app.stopping.wait(3600)
            except KeyboardInterrupt:
                pass
            return 0
        webview, reason = _load_webview()
        if webview is None:
            return _serve_in_browser(app, url)
        try:
            _open_window(webview, app, flags, url)
        except Exception:  # noqa: BLE001
            log.exception("window failed")
            if app.ui == "starting":
                import traceback
                from . import crash
                crash.write_log("window failed", traceback.format_exc())
                return _serve_in_browser(app, url)
        return 0
    finally:
        clear_instance(app.port)
        try:
            httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _icon_path(system: str = os.name, frozen: Optional[bool] = None) -> Optional[str]:
    """``.ico`` on Windows (a PNG there kills pywebview ≥ 6.2, see MemuNote), PNG elsewhere; None when frozen."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if system == "nt" and frozen:
        return None
    names = ("memufiles-icon.ico",) if system == "nt" else ("memufiles-icon.png",)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for name in names:
        for folder in (base, base / "app" / "memufiles" / "static"):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def _create_titlebar(flags: set) -> Any:
    """MemuAI's custom caption (``app/win_titlebar.py``): tabs become the title bar. ``None`` off Windows or if
    the module fails; Settings → «Tabs in the window title bar», ``--native-titlebar`` and
    ``MEMUAI_NATIVE_TITLEBAR=1`` keep the system frame."""
    if os.name != "nt":
        return None
    try:
        from .. import win_titlebar
        from . import settings
        preference = win_titlebar.MODE_CUSTOM if settings.load().get("custom_titlebar", True) else win_titlebar.MODE_NATIVE
        return win_titlebar.TitlebarController(preference=preference, cli_native=win_titlebar.CLI_FLAG in flags,
                                               env_value=os.environ.get(win_titlebar.ENV_VAR))
    except Exception as exc:  # noqa: BLE001 - the window must open with the system caption
        log.warning("custom title bar unavailable: %s", exc)
        return None


def _attach_titlebar(controller: Any, native: Any) -> bool:
    """Install the caption hook on the WinForms window (UI thread, like ``memuai._attach_titlebar``)."""
    if controller is None or native is None:
        return False
    try:
        from ..win_titlebar import winforms_ui_invoker

        def hwnd_provider() -> int:
            if not bool(getattr(native, "IsHandleCreated", True)):
                return 0
            return int(native.Handle.ToInt64())
        return bool(controller.attach(hwnd_provider, winforms_ui_invoker(native)))
    except Exception as exc:  # noqa: BLE001
        log.warning("custom title bar not attached: %s", exc)
        return False


def _open_window(webview: Any, app: Any, flags: set, url: str) -> None:
    from ..memunote.desktop import closing_geometry, sane_geometry
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MemuAI.MemuFiles")
        except Exception:  # noqa: BLE001
            pass
    mode = app.theme()["mode"]
    areas: List[tuple] = []
    if os.name == "nt":
        from ..memunote import winsys
        areas = winsys.monitor_work_areas()
    saved = read_json(files_dir() / "window.json", {})
    if not saved:
        saved = {"width": 1240, "height": 800}
    geometry = sane_geometry(saved, areas)
    titlebar = _create_titlebar(flags)
    extra: Dict[str, Any] = {}
    if titlebar is not None:
        from ..win_titlebar import TitlebarBridge
        extra["js_api"] = TitlebarBridge(titlebar)      # window.pywebview.api.titlebar_* (static/js/titlebar.js)
    window = webview.create_window(
        APP_NAME, url, width=geometry["width"], height=geometry["height"], x=geometry["x"], y=geometry["y"],
        min_size=(560, 380), background_color="#17171a" if mode != "light" else "#f7f6f3", text_select=False,
        confirm_close=False, zoomable=True, maximized=bool(geometry.get("maximized")), **extra)
    desktop = Desktop(webview, window, mode, titlebar)
    if os.name == "nt":
        window.expose(desktop.file_drag)
    if os.name == "nt":
        from .taskbar import TaskbarProgress
        desktop.taskbar = TaskbarProgress(lambda: desktop._hwnd, lambda: app.queue.list())
    app.desktop = desktop

    def on_shown() -> None:
        app.ui = "window"
        desktop._hwnd = 0
        desktop._hwnd = desktop.hwnd()
        if titlebar is not None and not titlebar.installed:
            _attach_titlebar(titlebar, desktop._native())
        desktop.apply_theme(mode)
        if os.name == "nt":
            from ..memunote import winsys
            winsys.ensure_on_screen(desktop.hwnd())

    def on_closing() -> bool:
        if app.ui == "window" and not desktop.quitting and not app.stopping.is_set() and _keep_running():
            # Round 39: «Держать готовым в фоне» — the window hides instead of closing; the next Win+E / folder
            # double-click shows it again at once (``activate``), with a new tab for the folder
            app.events.push({"type": "hidden"})
            app.ui = "warm"
            try:
                window.hide()
                return False
            except Exception:  # noqa: BLE001 - cannot hide: close for real
                app.ui = "window"
        try:
            native = desktop._native()
            state = str(native.WindowState) if native is not None else ""
            normal = None
            if os.name == "nt":
                from ..memunote import winsys
                normal = winsys.normal_rect(desktop.hwnd())
            data = closing_geometry(state, (window.x, window.y, window.width, window.height), normal,
                                    read_json(files_dir() / "window.json", {}))
            write_json(files_dir() / "window.json", data)
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
    options = {"private_mode": False, "storage_path": str(files_dir() / "webview"), "icon": _icon_path(),
               "debug": "--debug" in flags}
    import inspect
    try:
        accepted = inspect.signature(webview.start).parameters
        options = {k: v for k, v in options.items() if k in accepted and v is not None}
    except (TypeError, ValueError):
        options = {"debug": options["debug"]}
    webview.start(**options)


def _keep_running() -> bool:
    try:
        from . import settings
        return bool(settings.load().get("keep_running"))
    except Exception:  # noqa: BLE001
        return False


def _attach_drop(window: Any, app: Any) -> None:
    """Files dropped from Explorer: pywebview gives full paths (``pywebviewFullPath``); the page knows the target.

    Round 40 (r40-files-core): the event lists EVERY dropped file — ``files: [{name, path}]`` with ``path`` empty
    for virtual files (a zip folder open in Explorer, Outlook attachments, images from a browser): the page then
    reads those through the File API and streams them in (``/api/upload/*``). The page no longer stops the drop
    event's propagation, so this document-level listener really gets it (before, the file view swallowed it)."""
    try:
        from webview.dom import DOMEventHandler  # type: ignore
    except Exception:  # noqa: BLE001
        return

    def on_drop(event: Dict[str, Any]) -> None:
        try:
            files = event.get("dataTransfer", {}).get("files", []) or []
            rows = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                path = f.get("pywebviewFullPath") or ""
                if path and not os.path.exists(path):
                    path = ""                    # a temp file of a virtual item that is already gone
                rows.append({"name": str(f.get("name") or ""), "path": path})
            if rows:
                app.events.push({"type": "drop", "files": rows, "paths": [r["path"] for r in rows if r["path"]]})
        except Exception as exc:  # noqa: BLE001
            log.warning("drop failed: %s", exc)

    try:
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, False)
        if app.desktop is not None:
            app.desktop.dnd_paths = True
    except Exception as exc:  # noqa: BLE001
        log.debug("drop handler unavailable: %s", exc)
