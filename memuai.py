"""MemuAI (Playground) — отдельное приложение.

Собственный процесс и собственное окно: живёт независимо от MemuAI Proxy.
Локальный сервер раздаёт интерфейс и ходит к прокси за моделями и чатом.
Работа с файлами (режим Work) идёт напрямую через этот процесс.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


def pick_port(preferred: int) -> int:
    if not 1 <= preferred <= 65535:
        raise ValueError("порт должен быть в диапазоне 1–65535")
    for port in range(preferred, min(65535, preferred + 29) + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("не удалось найти свободный порт")


def _runtime_file(name: str) -> Path:
    data_dir = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if not data_dir:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(appdata, "MemuAI")
    return Path(data_dir) / "memuai" / name


def _read_runtime_url(name: str, fallback: str = "") -> str:
    try:
        url = str(json.loads(_runtime_file(name).read_text(encoding="utf-8")).get("url", ""))
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        if (parsed.scheme.lower() == "http"
                and (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
                and parsed.username is None and parsed.password is None
                and port is not None and 1 <= port <= 65535
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment):
            return url.rstrip("/")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return fallback.rstrip("/")


def _is_online(url: str, path: str = "/health", timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as response:
            if response.status != 200:
                return False
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                return False
            if path == "/health":
                return payload.get("service") == "memuai-proxy"
            if path == "/api/pg/ping":
                return payload.get("ok") is True
            return True
    except Exception:
        return False


def _activate_memuai() -> bool:
    url = _read_runtime_url("memuai.json")
    if not url or not _is_online(url, "/api/pg/ping"):
        return False
    try:
        request = urllib.request.Request(url + "/api/pg/activate", data=b"{}",
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            if not 200 <= response.status < 300:
                return False
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                return False
            return payload.get("ok") is True
    except Exception:
        return False


def _launch_module_in_running(module_id: str) -> bool:
    """Round 33 (#26): ``MemuAI.exe --module <id>`` while MemuAI runs → that instance opens the module window."""
    url = _read_runtime_url("memuai.json")
    if not url or not _is_online(url, "/api/pg/ping"):
        return False
    try:
        request = urllib.request.Request(url + "/api/pg/module-apps/launch",
                                         data=json.dumps({"id": module_id}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            return 200 <= response.status < 300 and payload.get("ok") is True
    except Exception:
        return False


def _forward_deeplink(link: str, attempts: int = 40) -> bool:
    """Round 35 (protocol): hand a ``memuai://`` link to the running MemuAI (it shows/confirms it in its window).

    The running instance may still be starting (lock taken, server not up yet), so wait a little."""
    for _ in range(max(1, attempts)):
        url = _read_runtime_url("memuai.json")
        if url and _is_online(url, "/api/pg/ping", timeout=1.0):
            try:
                request = urllib.request.Request(
                    url + "/api/pg/deeplink/dispatch",
                    data=json.dumps({"url": link, "source": "os"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
                return True
            except urllib.error.HTTPError:
                return True      # rejected (bad link, links off): the running app logged it — nothing to retry
            except Exception:
                pass
        time.sleep(0.25)
    return False


def _module_arg(value: str) -> str:
    """The ``--module`` value as a safe module id, or ''."""
    value = str(value or "").strip()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) else ""


def _create_module_windows(app, base_url: str, module_only: bool):
    """Round 33: native module windows + the main window close guard (app/module_apps.py), or ``None``."""
    try:
        from app import module_apps
        manager = module_apps.ModuleWindows(base_url, module_only=module_only)
        app.state.module_windows = manager
        module_apps.MANAGER = manager
        return manager
    except Exception as exc:
        print(f"[MemuAI] окна модулей недоступны: {exc}")
        return None


def _shutdown_memuai() -> bool:
    """Ask a running instance to exit cleanly (installer/uninstaller helper)."""
    try:
        data = json.loads(_runtime_file("memuai.json").read_text(encoding="utf-8"))
        url = _read_runtime_url("memuai.json")
        token = str(data.get("shutdown_token") or "")
        if not url or len(token) < 32:
            return True
        request = urllib.request.Request(
            url + "/api/window/shutdown", data=b"{}",
            headers={"Content-Type": "application/json", "X-MemuAI-Shutdown-Token": token},
            method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            accepted = 200 <= response.status < 300
        if accepted:
            for _ in range(50):
                if not _is_online(url, "/api/pg/ping", timeout=.2):
                    break
                time.sleep(.1)
        return accepted
    except Exception:
        # A stale runtime record means there is nothing left to close.
        return True


def _should_start_proxy() -> bool:
    """Настройка MemuAI, доступная ещё до запуска локального интерфейса."""
    try:
        data = json.loads(_runtime_file("preferences.json").read_text(encoding="utf-8"))
        return bool(data.get("start_proxy_with_memuai", True))
    except Exception:
        return True


def _startup_theme() -> str:
    """Match the native window background to the saved UI before WebView paints."""
    try:
        data = json.loads(_runtime_file("preferences.json").read_text(encoding="utf-8"))
        return "light" if data.get("theme") == "light" else "dark"
    except Exception:
        return "dark"


def _startup_background(theme: str) -> str:
    """The native window colour = the ``--bg`` the page paints first (Round 25).

    WebView2 shows the window's own background until the page draws. A fixed
    grey flashed visibly for every non-default variant (OLED black, Nord, Sepia,
    user themes) and even differed from the default palette by a shade. The
    page's first paint uses the same catalogue (``app/themes.py``)."""
    fallback = "#fcfcfb" if theme == "light" else "#1a1a1c"
    try:
        data = json.loads(_runtime_file("preferences.json").read_text(encoding="utf-8"))
        from app import themes
        value = themes.resolve_tokens(theme, data.get(f"surface_{theme}") or "",
                                      data.get("accent_color") or themes.DEFAULT_ACCENT).get("--bg", "")
    except Exception:
        return fallback
    value = str(value).strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def _configure_native_window(win, theme: str) -> None:
    """Apply only non-invasive Windows metadata to the normal system window.

    Resizing, maximize, Snap Layouts, corners, shadows, and transitions stay
    owned by Windows.  In particular, do not graft resize styles or a custom
    WndProc onto a frameless WinForms window: DWM cannot render that hybrid
    consistently.  The custom title bar (Round 20, ``app/win_titlebar.py``)
    is the supported alternative: it keeps this normal sizable window and
    only removes its caption through ``WM_NCCALCSIZE``.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = 0
        try:
            from webview.platforms.winforms import BrowserView
            native = BrowserView.instances.get(win.uid)
            if native is not None:
                hwnd = int(native.Handle.ToInt64())
        except Exception:
            pass
        hwnd = hwnd or user32.FindWindowW(None, "MemuAI")
        if not hwnd:
            return

        win_app = getattr(win, "app", None)
        del win_app  # document that this hook never mutates pywebview internals
        # Round 33 (#16): no SetCurrentProcessExplicitAppUserModelID here any more —
        # calling it after the window exists (and again after each restore from the
        # tray) regrouped the taskbar button; the process id is set in main().
        _apply_window_identity(hwnd)

        dark = theme == "dark"
        dark_value = ctypes.c_int(1 if dark else 0)
        # 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on current Windows 10/11; 19 is
        # retained as a compatibility fallback for older Windows 10 builds.
        for attribute in (20, 19):
            try:
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), attribute, ctypes.byref(dark_value),
                    ctypes.sizeof(dark_value))
                if result == 0:
                    break
            except Exception:
                continue
    except Exception:
        pass


def _create_titlebar(cli_native: bool = False):
    """Controller of the custom title bar, or ``None`` if it cannot load.

    Never raises: the window must open even if this module is broken.
    """
    try:
        from app.win_titlebar import CLI_FLAG, create_controller
        argv = list(sys.argv[1:]) + ([CLI_FLAG] if cli_native else [])
        return create_controller(_runtime_file("preferences.json"), argv)
    except Exception as exc:
        print(f"[MemuAI] своя строка заголовка недоступна: {exc}")
        return None


def _titlebar_bridge(controller):
    if controller is None:
        return None
    try:
        from app.win_titlebar import TitlebarBridge
        return TitlebarBridge(controller)
    except Exception:
        return None


def _attach_titlebar(controller, native) -> bool:
    """Install the caption hook on the WinForms window (UI thread)."""
    if controller is None or native is None:
        return False
    try:
        from app.win_titlebar import winforms_ui_invoker

        def hwnd_provider() -> int:
            # Never let a background thread *create* a handle.
            if not bool(getattr(native, "IsHandleCreated", True)):
                return 0
            return int(native.Handle.ToInt64())
        invoker = winforms_ui_invoker(native)
        try:
            # Round 30: Mica / Acrylic behind the page (Settings → Appearance), applied on request from the page
            from app import win_backdrop
            controller.backdrop = win_backdrop.create(native, hwnd_provider, invoker)
        except Exception as exc:
            print(f"[MemuAI] материал окна недоступен: {exc}")
        return controller.attach(hwnd_provider, invoker)
    except Exception as exc:
        print(f"[MemuAI] системная строка заголовка (ошибка: {exc})")
        return False


def _create_quick_ask(app, base_url: str):
    """«Быстрый доступ» (Alt+Space): the popup manager, or ``None``.

    Never raises: MemuAI must start even if this feature is broken.
    """
    if sys.platform != "win32":
        return None
    try:
        from app import desktop
        from app.pgdata import load_preferences, save_preferences
        from app.quick_ask import QuickAskManager

        def main_hwnd() -> int:
            return int(getattr(app.state, "native_hwnd", 0) or 0)

        def main_window():
            return getattr(app.state, "window", None)

        manager = QuickAskManager(base_url, load_prefs=load_preferences, save_prefs=save_preferences,
                                  main_hwnd=main_hwnd, show_main=desktop.show_active_window,
                                  main_window=main_window)
        app.state.quick_ask = manager
        return manager
    except Exception as exc:
        print(f"[MemuAI] быстрый доступ недоступен: {exc}")
        return None


def _create_companion_window(app, base_url: str):
    """Round 22: the desktop companion window manager (Quick Ask, experimental), or ``None``.

    Never raises; the window itself is created only when Quick Ask asks for it.
    """
    if sys.platform != "win32":
        return None
    try:
        from app.companion_avatar_api import data_root
        from app.companion_window import CompanionWindowManager
        manager = CompanionWindowManager(base_url, state_root=data_root)
        app.state.companion_window = manager
        return manager
    except Exception as exc:
        print(f"[MemuAI] окно компаньона недоступно: {exc}")
        return None


def _launch_proxy() -> str:
    """Запускает соседний Proxy и возвращает обнаруженный локальный URL."""
    frozen = bool(getattr(sys, "frozen", False))
    root = Path(sys.executable).parent if frozen else Path(__file__).resolve().parent
    candidates: list[list[str]] = []
    if frozen:
        candidates.append([str(root / "MemuAIProxy.exe"), "--background"])
        candidates.append([str(root.parent / "MemuAIProxy" / "MemuAIProxy.exe"), "--background"])
    else:
        py = Path(sys.executable).with_name("pythonw.exe") if os.name == "nt" else Path(sys.executable)
        if not py.exists():
            py = Path(sys.executable)
        candidates.append([str(py), str(root / "main.py"), "--background"])

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for command in candidates:
        if not Path(command[0]).exists() or (len(command) > 1 and command[1].endswith("main.py")
                                              and not Path(command[1]).exists()):
            continue
        try:
            subprocess.Popen(command, cwd=str(root), creationflags=flags)
            break
        except Exception:
            continue
    for _ in range(60):
        time.sleep(0.25)
        url = _read_runtime_url("proxy.json", "http://127.0.0.1:8317")
        if _is_online(url):
            return url
    return _read_runtime_url("proxy.json", "http://127.0.0.1:8317")


def _memuai_tray_icon() -> str:
    """Prefer the user's own app icon, then the MemuAI mark, never Proxy's."""
    try:
        from app.pgdata import DATA_DIR
        from app import assistant_icon
        _ico, png = assistant_icon.current_icon(DATA_DIR)
        if png and Path(png).is_file():
            return png
    except Exception:
        pass
    return "memuai-icon.png"


def _memuai_icon_ico() -> str:
    """Round 33: the .ico for the MemuAI window and tray (the assistant's look or the app icon)."""
    try:
        from app.pgdata import DATA_DIR
        from app import assistant_icon
        ico, _png = assistant_icon.current_icon(DATA_DIR)
        if ico:
            return ico
    except Exception:
        pass
    return "memuai-icon.ico"


def _apply_window_identity(hwnd: int) -> None:
    """Round 33 (#16): the main window's own AppUserModelID + relaunch icon, and its icon.

    The process id is set once at the start of ``main()`` (Windows wants it
    before any window); this only describes the window, so an entry pinned from
    it keeps a real icon. Called on every "shown" — all steps are idempotent.
    """
    try:
        from app import win_app_identity, assistant_icon
        from app.pgdata import DATA_DIR
        relaunch_icon = assistant_icon.bundled_ico()
        win_app_identity.set_window_identity(hwnd, win_app_identity.APP_ID_MEMUAI, "MemuAI",
                                             f"{relaunch_icon},0" if relaunch_icon else "")
        assistant_icon.apply_to_window(hwnd, DATA_DIR)
    except Exception:
        pass


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        # Computer Use живёт в этом процессе: без per-monitor DPI координаты
        # скриншотов и кликов расходятся на мониторах с масштабом ≠ 100 %.
        # Вызов должен произойти до создания любого окна pywebview.
        try:
            from app.desktop import ensure_per_monitor_dpi_awareness
            ensure_per_monitor_dpi_awareness()
        except Exception:
            pass
        # Round 33 (#16): the taskbar identity, once and before any window.
        try:
            from app import win_app_identity
            win_app_identity.set_process_app_id(win_app_identity.APP_ID_MEMUAI)
        except Exception:
            pass

    # адрес Proxy из %APPDATA%/MemuAI/memuai/proxy.json (пишет Proxy при старте)
    default_proxy = _read_runtime_url("proxy.json", "http://127.0.0.1:8317")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--proxy", default=default_proxy)
    parser.add_argument("--token", default=os.environ.get("MEMUAI_TOKEN", ""))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-start-proxy", action="store_true")
    parser.add_argument("--shutdown", action="store_true")
    # Shortcut/jump-list style actions: MemuAI.exe --new-chat / --temporary-chat
    parser.add_argument("--new-chat", action="store_true",
                        help="открыть новый чат сразу после запуска")
    parser.add_argument("--temporary-chat", action="store_true",
                        help="открыть временный чат сразу после запуска")
    # Recovery switch: always use the system window caption.
    parser.add_argument("--native-titlebar", action="store_true",
                        help="системная строка заголовка вместо своей")
    # Round 29: safe mode (app/safe_mode.py) and restart (wait for the previous process to exit).
    parser.add_argument("--safe-mode", action="store_true",
                        help="безопасный режим: без модулей, плагинов, своих тем, MCP и компаньона")
    parser.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    # Round 33 (#26): a module installed as its own app — only that module's window, no main window.
    parser.add_argument("--module", default="", help="открыть только окно модуля (модуль как приложение)")
    # Round 35 (protocol): the memuai:// handler — MemuAI.exe --deeplink "memuai://…" (app/deeplinks*.py).
    parser.add_argument("--deeplink", default="", help=argparse.SUPPRESS)
    args, _ = parser.parse_known_args()
    deeplink = ""
    try:
        from app.deeplinks_win import link_from_argv
        deeplink = (args.deeplink or link_from_argv(sys.argv[1:]))[:9000]
    except Exception:
        deeplink = ""

    if args.shutdown:
        _shutdown_memuai()
        return

    if not os.environ.get("MEMUAI_PROGRAM_DIR"):
        os.environ["MEMUAI_PROGRAM_DIR"] = str(
            Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    from app import desktop
    if args.wait_pid:
        try:
            from app.safe_mode import wait_for_pid
            wait_for_pid(args.wait_pid)
        except Exception:
            pass
    module_only = _module_arg(args.module)
    lock = desktop.acquire_instance_lock("MemuAI")
    if lock is None:
        if module_only and _launch_module_in_running(module_only):
            return
        if deeplink:
            if not _forward_deeplink(deeplink):
                desktop.message_box("MemuAI", "MemuAI запущен, но не ответил — ссылка memuai:// не передана.")
            return
        if not _activate_memuai():
            desktop.message_box("MemuAI", "MemuAI уже запущен, но окно не удалось активировать.")
        return
    try:
        # Round 29: --safe-mode, the marker file, Shift at start, the offer after failed starts.
        from app import safe_mode
        safe_mode.decide_at_startup(args.safe_mode, interactive=not args.headless)
    except Exception as exc:
        print(f"[MemuAI] безопасный режим недоступен: {exc}")

    proxy_url = args.proxy.rstrip("/")
    if not _is_online(proxy_url):
        discovered = _read_runtime_url("proxy.json", proxy_url)
        if _is_online(discovered):
            proxy_url = discovered
        elif not args.no_start_proxy and _should_start_proxy():
            proxy_url = _launch_proxy()

    os.environ["MEMUAI_PROXY_URL"] = proxy_url
    os.environ["MEMUAI_TOKEN"] = args.token or os.environ.get("MEMUAI_TOKEN", "")

    try:
        port = args.port or pick_port(8600)
        if not 1 <= port <= 65535:
            raise ValueError("порт должен быть в диапазоне 1–65535")
    except (OSError, RuntimeError, ValueError) as exc:
        desktop.message_box("MemuAI — ошибка запуска", f"Локальный сервер не запущен: {exc}.", error=True)
        lock.close()
        return

    import uvicorn
    from app.playground_api import create_app
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_config=None, access_log=False))
    shutdown_token = secrets.token_urlsafe(32)
    shutdown_event = threading.Event()

    def request_shutdown() -> None:
        shutdown_event.set()
        server.should_exit = True
        # Ask the shared desktop shell to quit so its tray icon is removed
        # and the native window is really destroyed (not merely hidden).
        desktop.quit_active_shell()

    app.state.shutdown_token = shutdown_token
    app.state.shutdown_callback = request_shutdown
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    start_action = "temp" if args.temporary_chat else ("new" if args.new_chat else "")
    base_url = f"http://127.0.0.1:{port}"
    url = base_url + (f"/?start={start_action}" if start_action else "")
    if not desktop.wait_for_service(
        lambda: _is_online(base_url, "/api/pg/ping", timeout=1.0), server_thread,
        attempts=80, interval=0.2,
    ):
        desktop.stop_server(server, server_thread)
        desktop.message_box(
            "MemuAI — ошибка запуска",
            "Локальный сервер не смог запуститься. Проверьте выбранный порт.",
            error=True,
        )
        lock.close()
        return

    try:
        from app.storage import save_json
        from app.version import APP_VERSION
        save_json(str(_runtime_file("memuai.json")), {
            "url": base_url, "pid": os.getpid(), "version": APP_VERSION,
            "updated": time.time(), "shutdown_token": shutdown_token,
        }, backup=False)
    except Exception:
        pass

    if shutdown_event.is_set():
        desktop.stop_server(server, server_thread)
        lock.close()
        return

    # Round 35 (protocol): the link MemuAI was started with waits for the window; repair the handler.
    try:
        from app import deeplinks, deeplinks_win
        if deeplink:
            deeplinks.dispatch(deeplink, "os", notify=False)
        threading.Thread(target=deeplinks_win.self_heal, daemon=True, name="memuai-deeplink-heal").start()
    except Exception as exc:
        print(f"[MemuAI] ссылки memuai:// недоступны: {exc}")

    print("=" * 56)
    print("  MemuAI")
    print(f"  Локально: {url}")
    print(f"  Прокси:   {os.environ['MEMUAI_PROXY_URL']}")
    print("=" * 56)

    if args.headless:
        try:
            while not shutdown_event.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass
        desktop.stop_server(server, server_thread)
        lock.close()
        return

    try:
        # Round 36 (perf-core): lighter WebView2 (no spare renderer, no Edge cloud services) — app/webview_flags.py
        from app import webview_flags
        webview_flags.apply()
    except Exception as exc:
        print(f"[MemuAI] флаги WebView2 не применены: {exc}")

    try:
        initial_theme = _startup_theme()
        titlebar = _create_titlebar(args.native_titlebar)
        quick_ask = _create_quick_ask(app, base_url)
        from app.safe_mode import is_safe_mode
        companion_window = None if is_safe_mode() else _create_companion_window(app, base_url)
        module_windows = _create_module_windows(app, base_url, bool(module_only))
        module_start = {"done": not module_only, "watch": False}

        def _module_windows_ready(win):
            """Round 33: track whether MemuAI itself was shown; open the --module window once."""
            if module_windows is None:
                return
            if not module_start["watch"]:
                module_start["watch"] = True
                if not module_only:
                    module_windows.main_shown = True
                try:
                    win.events.shown += lambda *_: setattr(module_windows, "main_shown", True)
                except Exception:
                    pass
            if not module_start["done"]:
                module_start["done"] = True

                def open_first():
                    try:
                        module_windows.open(module_only)
                    except Exception as exc:
                        print(f"[MemuAI] модуль {module_only} не открыт: {exc}")
                        module_windows.main_shown = True
                        shell.show_window()
                threading.Thread(target=open_first, daemon=True).start()

        def _on_window_ready(win):
            _module_windows_ready(win)
            _configure_native_window(win, initial_theme)
            if sys.platform == "win32":
                try:
                    from webview.platforms.winforms import BrowserView
                    native = BrowserView.instances.get(win.uid)
                    if native is not None:
                        app.state.native_hwnd = int(native.Handle.ToInt64())
                        # Microphone and clipboard for our own page without
                        # the browser's "Allow?" prompt every time.
                        desktop.grant_local_permissions(native, f"http://127.0.0.1:{port}")
                        _attach_titlebar(titlebar, native)
                except Exception:
                    pass
            if quick_ask is not None and not getattr(quick_ask, "_started", False):
                # The shown event fires again after every restore from the tray.
                quick_ask._started = True
                try:
                    quick_ask.start()
                except Exception as exc:
                    print(f"[MemuAI] быстрый доступ не запущен: {exc}")
                try:
                    # Round 27: the wake word (off by default) opens this popup; see app/wake_word.py.
                    # Round 29: not in safe mode.
                    if not is_safe_mode():
                        from app.wake_word import SERVICE as wake_word
                        wake_word.attach(app)
                        shell.quit_hooks.append(wake_word.shutdown)
                except Exception as exc:
                    print(f"[MemuAI] слово-активатор не запущено: {exc}")

        shell = desktop.DesktopShell(
            url, close_to_tray=True, show_tray=True, show_on_start=not module_only,
            title="MemuAI", app_ref=app, on_ready=_on_window_ready,
            tray_icon=_memuai_tray_icon(), app_user_model_id="MemuAI.Desktop",
            tray_ico=_memuai_icon_ico(), window_ico=_memuai_icon_ico(),
            width=1280, height=880, min_size=(420, 360),
            background_color=_startup_background(initial_theme),
            js_api=_titlebar_bridge(titlebar),
        )
        if module_windows is not None:
            # Round 33: modules outside the main window (app/module_apps.py).
            shell.ready_on_load = bool(module_only)
            shell.close_guard = module_windows.main_close_guard
            module_windows.main_window_ref = lambda: shell.window

            def _close_main_usual():
                if shell._on_closing():  # no tray → the close really quits
                    shell.request_quit()
            module_windows.close_main_cb = _close_main_usual
            module_windows.quit_cb = shell.request_quit
            module_windows.show_main_cb = shell.show_window
            module_windows.dark_cb = lambda: _startup_theme() != "light"
            shell.quit_hooks.append(module_windows.destroy_all)
        if quick_ask is not None:
            shell.quit_hooks.append(quick_ask.destroy)
            # Round 27: the region overlay, sticker windows, clipboard watcher and feature hotkeys.
            from app import quick_features_win
            shell.quit_hooks.append(quick_features_win.destroy_all)
        if companion_window is not None:
            shell.quit_hooks.append(companion_window.destroy)
        # Round 29: a backup on exit (handed to the Proxy when it runs) and a clean-exit mark for safe mode.
        try:
            from app import gdrive_sync, safe_mode as _safe
            shell.quit_hooks.append(lambda: gdrive_sync.service().on_exit(quick=shutdown_event.is_set()))
            shell.quit_hooks.append(_safe.clean_exit)
        except Exception as exc:
            print(f"[MemuAI] синхронизация при выходе недоступна: {exc}")
        shell.run()
    except Exception as e:
        print(f"[MemuAI] окно недоступно ({e}), открываю браузер")
        webbrowser_open(url)
        try:
            # --shutdown от установщика должен завершать и этот режим.
            while not shutdown_event.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass
    finally:
        desktop.stop_server(server, server_thread)
        lock.close()



def webbrowser_open(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


if __name__ == "__main__":
    # Dispatch before the instance lock, Proxy startup, WebView or any native
    # desktop imports. Also works in PyInstaller --windowed without stdio.
    if len(sys.argv) > 1 and sys.argv[1] == "--memuai-tts-worker":
        from app.tts_worker import main as tts_worker_main
        raise SystemExit(tts_worker_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--memuai-screen-capture":
        from app.screen_capture import main as screen_capture_main
        raise SystemExit(screen_capture_main(sys.argv[2:]))
    main()
