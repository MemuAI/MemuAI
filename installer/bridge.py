"""The JavaScript API of the installer window (``window.pywebview.api`` in ui/app.js).

pywebview exposes every public attribute of this object to the page, so all
internals are underscored. Long operations run in a worker thread; the page
polls :meth:`poll` (no ``evaluate_js`` from worker threads — that is the
fragile path in the WinForms backend).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import versions
from .engine import AppsRunning, Cancelled, Engine, Existing, Options, SetupError, exists

log = logging.getLogger("memuai.setup")


class Bridge:
    def __init__(self, engine: Engine, mode: str, language: str, log_path: str = "",
                 app_dir: str = "", on_exit: Optional[Callable[[int], None]] = None) -> None:
        self._engine = engine
        self._mode = mode                      # "install" | "uninstall"
        self._language = language
        self._log_path = log_path
        self._app_dir = app_dir
        self._window = None
        self._lock = threading.Lock()
        self._state: Dict[str, object] = {"status": "idle", "percent": 0, "phase": "", "file": ""}
        self._thread: Optional[threading.Thread] = None
        self._options: Optional[Options] = None
        self._existing: Optional[Existing] = None
        self._exit_code = 0
        self._on_exit = on_exit
        # main.run_window installs a callable that schedules winplat.begin_window_drag on the
        # GUI thread; None (tests, non-Windows) makes the page fall back to move_window().
        self._begin_drag: Optional[Callable[[], bool]] = None

    # ------------------------------------------------------------------ info
    def init(self) -> dict:
        engine = self._engine
        product = engine.product
        existing = engine.detect()
        self._existing = existing
        defaults = engine.defaults(existing, self._language)
        if existing.language in ("ru", "en") and self._mode == "uninstall":
            self._language = existing.language
        sizes = {}
        if engine.payload:
            for comp in product.components:
                sizes[comp.id] = engine.payload.index.size_of([comp.id])
        data_dirs = [str(p) for p in engine.data_dirs()]
        return {
            "mode": self._mode,
            "lang": self._language,
            "product": {"id": product.id, "name": product.name},
            "version": versions.normalize_version(engine.version) or engine.version,
            "kind": engine.install_kind(existing) if self._mode == "install" else "uninstall",
            "existing": {"found": existing.found, "version": existing.version, "dir": existing.install_dir,
                         "source": existing.source, "components": existing.components,
                         "legacy_machine": existing.legacy_machine},
            "defaults": {"dir": defaults.install_dir, "components": defaults.components, "tasks": defaults.tasks},
            # Round 29: an optional component this Setup does not carry (e.g. MemuNote) is not offered.
            "components": [{"id": c.id, "required": c.required, "size": sizes.get(c.id, 0)}
                           for c in product.components if not engine.payload or c.required or sizes.get(c.id)],
            "tasks": [{"id": t.id, "needs": list(t.needs)} for t in product.tasks],
            "default_dir": engine.default_dir(),
            "app_dir": self._app_dir or existing.install_dir,
            "data_dirs": data_dirs,
            "log": self._log_path,
        }

    def set_language(self, lang: str) -> str:
        self._language = "ru" if str(lang).lower().startswith("ru") else "en"
        return self._language

    def check_dir(self, path: str, components: List[str]) -> dict:
        try:
            return self._engine.check_dir(path, components or [])
        except Exception as exc:  # noqa: BLE001
            log.exception("check_dir failed")
            return {"ok": False, "path": path, "error": "dir_invalid", "message": str(exc)}

    def browse(self, current: str = "") -> Optional[str]:
        window = self._window
        if window is None:
            return None
        try:
            import webview
            folder_kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if folder_kind is None:
                folder_kind = getattr(webview, "FOLDER_DIALOG")
            start = current if current and os.path.isdir(current) else str(Path(current).parent) if current else ""
            result = window.create_file_dialog(folder_kind, directory=start if os.path.isdir(start) else "")
        except Exception:  # noqa: BLE001
            log.exception("folder dialog failed")
            return None
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    # ------------------------------------------------------------------ work
    def _set(self, **values: object) -> None:
        with self._lock:
            self._state.update(values)

    def poll(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _report(self, event: Dict[str, object]) -> None:
        values = {k: v for k, v in event.items() if k in ("phase", "percent", "file", "done", "total")}
        if "percent" in values:
            with self._lock:
                values["percent"] = max(float(self._state.get("percent") or 0), float(values["percent"]))
        self._set(**values)

    def _run(self, work: Callable[[], dict]) -> None:
        try:
            result = work()
            self._exit_code = 0         # a retry that succeeded is a successful Setup
            self._set(status="done", percent=100, phase="done", result=result)
        except AppsRunning as exc:
            self._set(status="apps", apps=exc.apps, error={"code": exc.code, "message": str(exc)})
        except Cancelled:
            self._set(status="cancelled", percent=0)
        except SetupError as exc:
            log.error("setup error %s: %s", exc.code, exc)
            self._set(status="error", error={"code": exc.code, "message": str(exc), "details": exc.details})
            self._exit_code = 1
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected failure")
            self._set(status="error", error={"code": "unexpected", "message": f"{type(exc).__name__}: {exc}"})
            self._exit_code = 1

    def _start(self, work: Callable[[], dict]) -> dict:
        if self._thread and self._thread.is_alive():
            return {"started": False}
        self._engine.cancel_event.clear()
        self._set(status="running", percent=0, phase="preparing", file="", error=None, apps=None)
        self._thread = threading.Thread(target=self._run, args=(work,), name="setup-worker", daemon=True)
        self._thread.start()
        return {"started": True}

    def install(self, options: dict, force_close: bool = False) -> dict:
        opts = Options(install_dir=str(options.get("dir") or ""), components=list(options.get("components") or []),
                       tasks=list(options.get("tasks") or []), language=self._language)
        self._options = opts
        return self._start(lambda: self._engine.install(opts, self._report, force_close=bool(force_close)))

    def uninstall(self, remove_data: bool = False, force_close: bool = False) -> dict:
        app_dir = Path(self._app_dir or (self._existing.install_dir if self._existing else ""))
        return self._start(lambda: self._engine.uninstall(app_dir, bool(remove_data), self._report,
                                                          force_close=bool(force_close)))

    def cancel(self) -> bool:
        self._engine.cancel_event.set()
        return True

    # ------------------------------------------------------------------ finish
    def launch(self) -> bool:
        state = self.poll()
        result = state.get("result") or {}
        if not isinstance(result, dict) or not result.get("ok"):
            return False
        return self._engine.launch(Path(str(result.get("install_dir"))), list(result.get("components") or []))

    def open_folder(self, path: str = "") -> bool:
        target = path or self._log_path
        if not target:
            return False
        try:
            folder = target if os.path.isdir(target) else str(Path(target).parent)
            if os.name == "nt" and exists(Path(folder)):
                os.startfile(folder)  # noqa: S606 - explorer on a known local folder
                return True
        except OSError:
            log.exception("open folder failed")
        return False

    def open_log(self) -> bool:
        if os.name == "nt" and self._log_path and os.path.isfile(self._log_path):
            try:
                subprocess.Popen(["notepad.exe", self._log_path])
                return True
            except OSError:
                pass
        return False

    def minimize(self) -> None:
        if self._window is not None:
            try:
                self._window.minimize()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ window drag
    def drag_window(self) -> bool:
        """Called by the page on mousedown in a drag zone (titlebar, empty background).

        True = the native move loop was scheduled (Windows moves the window itself until
        the button is released); False = not available, the page moves the window with
        :meth:`move_window` instead."""
        starter = self._begin_drag
        if starter is None:
            return False
        try:
            return bool(starter())
        except Exception as exc:  # noqa: BLE001
            log.info("native drag failed: %s", exc)
            return False

    def move_window(self, x: float, y: float) -> bool:
        """Fallback drag: put the window's top-left corner at screen ``x``/``y`` (CSS px)."""
        window = self._window
        if window is None:
            return False
        try:
            window.move(int(round(float(x))), int(round(float(y))))
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._thread and self._thread.is_alive():
            self._engine.cancel_event.set()
            deadline = time.monotonic() + 20
            while self._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
        if self._on_exit:
            self._on_exit(self._exit_code)
        if self._window is not None:
            threading.Thread(target=self._destroy, daemon=True).start()

    def _destroy(self) -> None:
        try:
            self._window.destroy()
        except Exception:  # noqa: BLE001
            os._exit(self._exit_code)
