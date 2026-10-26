"""Round 29: MemuAI safe mode — a clean start when an extension broke something.

Safe mode is a *runtime overlay*: nothing in the user's data is changed or deleted.
While it is on, MemuAI does not load or start:

* modules (the list is empty, module documents are refused, Proxy pauses module
  background jobs while a safe-mode session of MemuAI is alive);
* Quick Ask plugins — the page half (``/quick/plugins/index.json`` is served empty)
  and the backend half (``quick_ask_api.plugin_manifest``);
* MCP servers (``mcp_client.MCPRegistry.server``);
* the desktop companion window and the companion next to Live;
* the wake word;
* scheduled panel refreshes (``panels.panels_loop``);
* custom themes / user CSS and alternative designs (the page uses the built-in look);
* the user's own Quick Ask routines (``assistant_commands`` refuses to run them);
* automatic backups of «Синхронизация» (manual ones still work).

How it is turned on (checked in this order by :func:`decide_at_startup`):

1. ``MemuAI.exe --safe-mode`` (the Start-menu shortcut «MemuAI (безопасный режим)»)
   or the environment variable ``MEMUAI_SAFE_MODE=1``;
2. the marker file ``%APPDATA%\\MemuAI\\memuai\\safe_mode.json`` (``{"once": true}``
   makes it one-shot; any content or an empty file means «until turned off»);
3. holding **Shift** while MemuAI starts (Windows; a Yes/No question first);
4. after two starts in a row that never reached a working window
   (``startup_guard.json``), MemuAI offers safe mode itself.

Every part of the program asks one function, :func:`is_safe_mode`.  The page gets
``window.MEMU_SAFE_MODE`` from ``/api/pg/safe-mode/boot.js`` before anything else,
shows the «Безопасный режим» banner (``pgstatic/safe-mode.js``) and can restart
MemuAI normally through ``POST /api/pg/app/restart``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - fastapi ships with both apps
    Request = Any  # type: ignore[misc,assignment]

log = logging.getLogger("memuai.safe_mode")

ENV = "MEMUAI_SAFE_MODE"
CRASH_THRESHOLD = 2
HEALTHY_AFTER = 8.0
SOURCES = ("flag", "env", "marker", "shift", "crash", "restart")

_STATE: Dict[str, Any] = {"active": False, "source": "", "since": 0.0, "crash_count": 0}
_LOCK = threading.Lock()

def _memu_dir() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR) / "memuai"

def marker_path() -> Path:
    return _memu_dir() / "safe_mode.json"

def guard_path() -> Path:
    return _memu_dir() / "startup_guard.json"

def session_path() -> Path:
    return _memu_dir() / "safe_mode_session.json"

def _read(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {}

def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass

def is_safe_mode() -> bool:
    """The one question every extension point asks."""
    return bool(_STATE["active"]) or os.environ.get(ENV, "").strip() in ("1", "true", "yes")

def activate(source: str) -> None:
    with _LOCK:
        if not _STATE["active"]:
            _STATE.update(active=True, source=source if source in SOURCES else "flag", since=time.time())
        os.environ[ENV] = "1"
    try:
        _write(session_path(), {"pid": os.getpid(), "since": _STATE["since"], "source": _STATE["source"]})
    except OSError:
        pass
    log.warning("MemuAI runs in safe mode (%s): extensions are not loaded", _STATE["source"])

def deactivate_for_tests() -> None:
    with _LOCK:
        _STATE.update(active=False, source="", since=0.0, crash_count=0)
        os.environ.pop(ENV, None)

def session_active() -> bool:
    """For MemuAI Proxy: is a MemuAI process running in safe mode right now?"""
    if os.environ.get(ENV, "").strip() in ("1", "true", "yes"):
        return True
    data = _read(session_path())
    if not isinstance(data, dict) or not data.get("pid"):
        return False
    try:
        pid = int(data["pid"])
    except (TypeError, ValueError):
        return False
    try:
        import psutil
        alive = psutil.pid_exists(pid)
    except Exception:
        alive = False
    if not alive:
        _remove(session_path())
    return alive

def marker_state() -> Dict[str, Any]:
    data = _read(marker_path())
    if data is None:
        return {"exists": False, "once": False}
    return {"exists": True, "once": isinstance(data, dict) and data.get("once") is True}

def set_marker(*, once: bool) -> None:
    _write(marker_path(), {"once": bool(once), "created": time.time()})

def clear_marker() -> None:
    _remove(marker_path())

def _guard() -> Dict[str, Any]:
    data = _read(guard_path())
    return data if isinstance(data, dict) else {}

def startup_begin() -> int:
    """Count this start as «not yet healthy»; returns the number of unfinished starts before it."""
    data = _guard()
    before = int(data.get("pending") or 0)
    try:
        _write(guard_path(), {"pending": before + 1, "last_start": time.time()})
    except OSError:
        pass
    return before

def mark_healthy() -> None:
    try:
        _write(guard_path(), {"pending": 0, "last_ok": time.time()})
    except OSError:
        pass

def clean_exit() -> None:
    """MemuAI is closing normally: not a crash; the safe-mode session ends."""
    data = _guard()
    if int(data.get("pending") or 0):
        mark_healthy()
    if _STATE["active"]:
        _remove(session_path())

def _lang() -> str:
    try:
        data = json.loads((_memu_dir() / "preferences.json").read_text(encoding="utf-8"))
        return "en" if data.get("language") == "en" else "ru"
    except Exception:
        return "ru"

def shift_held() -> bool:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
    except Exception:
        return False

def ask_yes_no(title: str, text: str) -> bool:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return ctypes.windll.user32.MessageBoxW(0, text, title, 0x4 | 0x20 | 0x40000 | 0x10000) == 6
    except Exception:
        return False

def decide_at_startup(flag: bool = False, *, interactive: bool = True,
                      ask: Callable[[str, str], bool] | None = None,
                      shift: Callable[[], bool] | None = None) -> Dict[str, Any]:
    """Called by ``memuai.py`` before the server starts.  Never raises."""
    ask = ask or ask_yes_no
    shift = shift or shift_held
    try:
        en = _lang() == "en"
        if flag:
            activate("flag")
        elif os.environ.get(ENV, "").strip() in ("1", "true", "yes"):
            activate("env")
        else:
            marker = marker_state()
            if marker["exists"]:
                activate("marker")
                if marker["once"]:
                    clear_marker()
            elif interactive and shift():
                if ask("MemuAI", "Start MemuAI in safe mode?\n\nModules, plugins, custom themes, MCP servers, the companion "
                                 "and the wake word will not load. Your data is not changed." if en else
                       "Запустить MemuAI в безопасном режиме?\n\nМодули, плагины, свои темы, серверы MCP, компаньон и "
                       "слово-активатор не будут загружены. Ваши данные не меняются."):
                    activate("shift")

        pending = startup_begin() if interactive else int(_guard().get("pending") or 0)
        _STATE["crash_count"] = pending
        if not is_safe_mode() and interactive and pending >= CRASH_THRESHOLD:
            if ask("MemuAI", (f"MemuAI did not start correctly {pending} times in a row.\n\nStart in safe mode? "
                              "Extensions will not load; your data is not changed." if en else
                              f"MemuAI не смог нормально запуститься {pending} раза подряд.\n\nЗапустить в безопасном "
                              "режиме? Расширения не загрузятся, данные не меняются.")):
                activate("crash")
            else:
                mark_healthy()
                startup_begin()
    except Exception:
        log.exception("safe mode: startup decision failed")
    return status()

def _count(fn: Callable[[], int]) -> Optional[int]:
    try:
        return int(fn())
    except Exception:
        return None

def disabled_items() -> List[Dict[str, Any]]:
    """What safe mode switched off on this PC (counts are best-effort)."""

    def modules() -> int:
        from .modules_host import default_root, list_modules
        return len(list_modules(default_root()))

    def quick_plugins() -> int:
        path = Path(__file__).resolve().parent / "pgstatic" / "quick" / "plugins" / "index.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data.get("plugins") or [])

    def mcp() -> int:
        from . import mcp_client
        return sum(1 for row in mcp_client.load_config()["servers"].values() if row.get("enabled"))

    def themes() -> int:
        from . import themes as themes_module
        return len(themes_module.list_custom())

    prefs: Dict[str, Any] = {}
    try:
        prefs = json.loads((_memu_dir() / "preferences.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    wake: Dict[str, Any] = {}
    try:
        wake = json.loads((_memu_dir() / "wake_word.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    design = str(prefs.get("ui_design") or "classic")
    rows = [
        {"id": "modules", "ru": "Модули", "en": "Modules", "count": _count(modules),
         "note_ru": "не загружаются, фоновые задачи модулей в Proxy на паузе",
         "note_en": "not loaded; module background jobs in Proxy are paused"},
        {"id": "quick_plugins", "ru": "Плагины быстрого доступа", "en": "Quick Ask plugins", "count": _count(quick_plugins),
         "note_ru": "окно Alt+Space работает без расширений", "note_en": "the Alt+Space window works without extensions"},
        {"id": "mcp", "ru": "Серверы MCP", "en": "MCP servers", "count": _count(mcp),
         "note_ru": "не запускаются", "note_en": "not started"},
        {"id": "themes", "ru": "Свои темы и CSS", "en": "Custom themes and CSS", "count": _count(themes),
         "note_ru": "используется встроенное оформление" + (f" (вместо дизайна «{design}»)" if design != "classic" else ""),
         "note_en": "the built-in look is used" + (f" (instead of the “{design}” design)" if design != "classic" else "")},
        {"id": "companion", "ru": "Компаньон", "en": "Companion",
         "count": 1 if prefs.get("companion_live_enabled") else 0,
         "note_ru": "окно компаньона и персонаж в Live не показываются", "note_en": "the companion window and the Live character are hidden"},
        {"id": "wake_word", "ru": "Слово-активатор", "en": "Wake word", "count": 1 if wake.get("enabled") else 0,
         "note_ru": "микрофон не слушается", "note_en": "the microphone is not listened to"},
        {"id": "panels", "ru": "Обновление панелей", "en": "Panel refresh", "count": None,
         "note_ru": "панели показывают последние данные без фонового обновления",
         "note_en": "panels show their last data without background refresh"},
        {"id": "routines", "ru": "Свои сценарии", "en": "Custom routines", "count": None,
         "note_ru": "сценарии быстрого доступа не выполняются", "note_en": "Quick Ask routines do not run"},
        {"id": "sync", "ru": "Автоматические резервные копии", "en": "Automatic backups", "count": None,
         "note_ru": "на паузе; «Сохранить сейчас» работает", "note_en": "paused; «Back up now» still works"},
    ]
    return rows

def status() -> Dict[str, Any]:
    return {"active": is_safe_mode(), "source": _STATE.get("source") or ("env" if is_safe_mode() else ""),
            "since": _STATE.get("since") or 0, "crash_count": int(_STATE.get("crash_count") or 0),
            "marker": marker_state(), "crash_threshold": CRASH_THRESHOLD, "healthy_after": HEALTHY_AFTER}

def launch_command(safe: bool, wait_pid: int) -> tuple[List[str], str]:
    """``(argv, cwd)`` of a new MemuAI: the frozen exe, or ``python memuai.py`` from a source tree."""
    args = ["--wait-pid", str(wait_pid)] + (["--safe-mode"] if safe else [])
    if getattr(sys, "frozen", False):
        return [sys.executable, *args], str(Path(sys.executable).parent)
    program = Path(os.environ.get("MEMUAI_PROGRAM_DIR") or Path(__file__).resolve().parent.parent)
    return [sys.executable, str(program / "memuai.py"), *args], str(program)

def relaunch(app, *, safe: bool, popen: Callable[..., Any] | None = None) -> Dict[str, Any]:
    """Start a new MemuAI that waits for this process to exit, then close this one."""
    shutdown = getattr(getattr(app, "state", None), "shutdown_callback", None)
    if shutdown is None:
        return {"ok": False, "error_ru": "Перезапуск доступен только в приложении MemuAI",
                "error_en": "Restarting works only in the MemuAI app"}
    command, cwd = launch_command(safe, os.getpid())
    env = dict(os.environ)
    env.pop(ENV, None)
    flags = 0
    if os.name == "nt":  # pragma: no cover - Windows only
        flags = 0x00000008 | 0x00000200
    try:
        (popen or subprocess.Popen)(command, env=env, cwd=cwd, creationflags=flags, close_fds=True)
    except Exception as exc:
        return {"ok": False, "error_ru": f"Не удалось запустить MemuAI: {exc}", "error_en": f"Could not start MemuAI: {exc}"}
    clean_exit()
    threading.Timer(0.4, shutdown).start()
    return {"ok": True}

def wait_for_pid(pid: int, timeout: float = 30.0) -> None:
    """``memuai.py --wait-pid``: let the previous MemuAI release its single-instance lock."""
    if pid <= 0 or pid == os.getpid():
        return
    deadline = time.time() + timeout
    try:
        import psutil
    except Exception:
        time.sleep(2.0)
        return
    while time.time() < deadline and psutil.pid_exists(pid):
        time.sleep(0.2)

_BOOT_TEMPLATE = """/* MemuAI safe mode bootstrap (Round 29): loaded before the first paint. */
window.MEMU_SAFE_MODE = %s;
(function () {
  if (!window.MEMU_SAFE_MODE || !window.MEMU_SAFE_MODE.active) return;
  var root = document.documentElement;
  root.dataset.safeMode = "1";
  function neutralize() {
    var style = document.getElementById("memu-user-theme");
    if (style) style.media = "not all";
    root.dataset.design = "classic";
  }
  document.addEventListener("DOMContentLoaded", neutralize);
})();
"""

def register(app) -> None:
    from fastapi.responses import FileResponse, JSONResponse, Response

    started = time.time()

    @app.get("/api/pg/safe-mode/boot.js", include_in_schema=False)
    async def safe_mode_boot():
        payload = json.dumps(status() if is_safe_mode() else {"active": False})
        return Response(_BOOT_TEMPLATE % payload, media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/pg/safe-mode")
    async def safe_mode_status():
        data = status()
        if data["active"]:
            data["disabled"] = disabled_items()
        return data

    @app.post("/api/pg/safe-mode/healthy")
    async def safe_mode_healthy():

        if time.time() - started >= 2.0:
            mark_healthy()
        return {"ok": True}

    @app.post("/api/pg/safe-mode/marker")
    async def safe_mode_marker(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        mode = str((body or {}).get("mode") or "")
        if mode == "off":
            clear_marker()
        elif mode in ("once", "always"):
            set_marker(once=mode == "once")
        else:
            return JSONResponse({"error": "mode: once | always | off"}, 400)
        return {"marker": marker_state()}

    @app.post("/api/pg/app/restart")
    async def app_restart(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        mode = str((body or {}).get("mode") or "normal")
        safe = mode == "safe"
        if not safe:
            clear_marker()
        result = relaunch(app, safe=safe)
        return result if result.get("ok") else JSONResponse(result, 400)

    @app.get("/quick/plugins/index.json", include_in_schema=False)
    async def quick_plugin_manifest():
        if is_safe_mode():
            return JSONResponse({"version": 1, "plugins": [], "safe_mode": True}, headers={"Cache-Control": "no-store"})
        path = Path(__file__).resolve().parent / "pgstatic" / "quick" / "plugins" / "index.json"
        return FileResponse(str(path), media_type="application/json", headers={"Cache-Control": "no-store"})
