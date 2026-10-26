"""MemuAI Proxy — локальный API, нативное окно и системный трей.

``--headless`` оставлен лишь для совместимости со старыми ярлыками и теперь
означает нормальный фоновый запуск *с иконкой в трее*. Новое имя флага —
``--background``.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

def _runtime_file(name: str) -> str:
    data_dir = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if not data_dir:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(appdata, "MemuAI")
    return os.path.join(data_dir, "memuai", name)

def _read_local_runtime(name: str) -> dict:
    try:
        with open(_runtime_file(name), encoding="utf-8") as f:
            data = json.load(f)
        parsed = urllib.parse.urlsplit(str(data.get("url", "")))
        port = parsed.port
        if (parsed.scheme.lower() != "http"
                or (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1")
                or parsed.username is not None or parsed.password is not None
                or port is None or not 1 <= port <= 65535
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return {}
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}

def activate_running_proxy() -> bool:
    """Показывает уже запущенный Proxy и не создаёт второй процесс."""
    data = _read_local_runtime("proxy.json")
    url = str(data.get("url", "")).rstrip("/")
    if not url:
        return False
    try:
        with urllib.request.urlopen(url + "/health", timeout=1.5) as response:
            if response.status != 200:
                return False
            try:
                health = json.loads(response.read().decode("utf-8"))
            except Exception:
                return False
            if health.get("service") != "memuai-proxy":
                return False
        request = urllib.request.Request(url + "/api/admin/show_window", data=b"{}",
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
        try:
            urllib.request.urlopen(request, timeout=1.5).close()
        except Exception:

            pass
        return True
    except Exception:
        return False

def shutdown_running_proxy() -> bool:
    """Ask the installed tray process to leave cleanly before uninstall."""
    data = _read_local_runtime("proxy.json")
    url = str(data.get("url") or "").rstrip("/")
    token = str(data.get("shutdown_token") or "")
    if not url or len(token) < 32:
        return True
    try:
        request = urllib.request.Request(
            url + "/api/admin/shutdown_local", data=b"{}",
            headers={"Content-Type": "application/json", "X-MemuAI-Shutdown-Token": token},
            method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            accepted = 200 <= response.status < 300
        if accepted:
            for _ in range(50):
                if not _proxy_is_online(url, timeout=.2):
                    break
                time.sleep(.1)
        return accepted
    except Exception:
        return True

def _proxy_is_online(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("service") == "memuai-proxy"
    except Exception:
        return False

def pick_port(preferred: int, host: str = "127.0.0.1") -> int:
    if not 1 <= preferred <= 65535:
        raise ValueError("порт должен быть в диапазоне 1–65535")
    bind_host = host.strip()
    if bind_host.startswith("[") and bind_host.endswith("]"):
        bind_host = bind_host[1:-1]
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    for port in range(preferred, min(65535, preferred + 24) + 1):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((bind_host, port))
            except OSError:
                continue
            return port
    raise RuntimeError("не удалось найти свободный порт")

def local_server_url(host: str, port: int) -> str:
    """Return a reachable loopback URL even when the server binds all interfaces."""
    local_host = host.strip()
    if local_host.startswith("[") and local_host.endswith("]"):
        local_host = local_host[1:-1]
    if local_host == "0.0.0.0":
        local_host = "127.0.0.1"
    elif local_host == "::":
        local_host = "::1"
    if ":" in local_host and not local_host.startswith("["):
        local_host = f"[{local_host}]"
    return f"http://{local_host}:{port}"

def secure_bind_host(host: str, password_is_default: bool) -> str:
    """Fail closed: a well-known admin password must never be exposed to a LAN."""
    if password_is_default and host.strip().lower() not in ("127.0.0.1", "localhost", "::1"):
        return "127.0.0.1"
    return host

def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--headless", action="store_true", help="фоновый запуск (совместимость)")
    parser.add_argument("--background", action="store_true", help="запустить свёрнутым в трей")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-tray", action="store_true", help="без иконки в трее")
    parser.add_argument("--shutdown", action="store_true", help="корректно завершить запущенный экземпляр")
    args, _ = parser.parse_known_args()

    if args.shutdown:
        shutdown_running_proxy()
        return

    if not os.environ.get("MEMUAI_PROGRAM_DIR"):
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))
        os.environ["MEMUAI_PROGRAM_DIR"] = base

    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    from app import desktop

    if activate_running_proxy():
        return
    lock = desktop.acquire_instance_lock("MemuAIProxy")
    if lock is None:
        if not activate_running_proxy():
            desktop.message_box(
                "MemuAI Proxy",
                "Другой экземпляр MemuAI Proxy уже запущен. Закройте его через трей "
                "и повторите запуск.",
            )
        return

    from app.config import config_store
    from app.main import create_app

    preferred_port = args.port or config_store.settings.port
    requested_host = config_store.settings.host
    host = secure_bind_host(requested_host, config_store.first_run_password)
    if host != requested_host:
        desktop.message_box(
            "MemuAI Proxy — защита сети",
            "Публикация в локальной сети отключена, пока используется стандартный пароль. "
            "Сначала измените пароль администратора в настройках Proxy.",
        )
    try:
        port = pick_port(preferred_port, host)
    except (OSError, RuntimeError, ValueError) as exc:
        desktop.message_box(
            "MemuAI Proxy — ошибка запуска",
            f"Локальный сервер не запущен: {exc}.",
            error=True,
        )
        lock.close()
        return
    if port != preferred_port:
        print(f"[MemuAI Proxy] порт {preferred_port} занят — беру {port}")

    config_store.set_runtime_endpoint(host, port)

    import uvicorn
    proxy_app = create_app()
    server = uvicorn.Server(uvicorn.Config(proxy_app, host=host, port=port,
                                           log_config=None, access_log=False))
    shutdown_token = secrets.token_urlsafe(32)
    shutdown_event = threading.Event()

    def request_shutdown() -> None:
        shutdown_event.set()
        server.should_exit = True
        desktop.quit_active_shell()

    proxy_app.state.shutdown_token = shutdown_token
    proxy_app.state.shutdown_callback = request_shutdown
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    url = local_server_url(host, port)
    if not desktop.wait_for_service(
        lambda: _proxy_is_online(url, timeout=1.0), server_thread,
        attempts=100, interval=0.2,
    ):
        desktop.stop_server(server, server_thread)
        desktop.message_box(
            "MemuAI Proxy — ошибка запуска",
            "Локальный сервер не смог запуститься. Проверьте адрес и порт в настройках.",
            error=True,
        )
        lock.close()
        return

    try:
        from app.config import DATA_DIR
        from app.storage import save_json
        import pathlib as _pl
        from app.version import APP_VERSION
        d = _pl.Path(DATA_DIR) / "memuai"
        d.mkdir(parents=True, exist_ok=True)
        save_json(str(d / "proxy.json"), {
            "url": url, "pid": os.getpid(), "version": APP_VERSION,
            "updated": time.time(), "shutdown_token": shutdown_token,
        }, backup=False)
    except Exception:
        pass

    if shutdown_event.is_set():
        desktop.stop_server(server, server_thread)
        lock.close()
        return

    print("=" * 62)
    print("  MemuAI Proxy — все ваши AI-аккаунты в одном API")
    print(f"  API:     {url}/v1")
    print(f"  Панель:  {url}/  (нативное окно приложения)")
    print("=" * 62)

    background = bool(args.background or args.headless or not config_store.settings.open_ui_on_start)

    show_on_start = not background or args.no_tray
    try:
        code = desktop.run_desktop(
            url, port,
            close_to_tray=bool(config_store.settings.close_to_tray),
            show_tray=not args.no_tray,
            show_on_start=show_on_start,
        )
    finally:
        desktop.stop_server(server, server_thread)
        lock.close()
    if code != 0:
        return
    print("[MemuAI Proxy] остановка…")

def _crash_log_path() -> str:
    try:
        path = _runtime_file("startup-error.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path
    except Exception:
        name = "memuai-proxy-startup-error.log" if os.environ.get("MEMUAI_PROXY_ONLY") == "1" \
            else "memuai-startup-error.log"
        return os.path.join(os.path.expanduser("~"), name)

def _report_startup_crash(exc: BaseException) -> None:
    """Записывает трассировку и показывает понятное окно вместо тихого падения.

    Раньше сбой на старте (нехватка DLL, битый VC-Runtime, антивирус, запуск от
    имени администратора после установки) заканчивался системным сообщением вида
    "Failed to start python…", по которому невозможно понять причину. Теперь в
    APPDATA/MemuAI/memuai/startup-error.log остаётся полный отчёт, а
    пользователь получает текст ошибки и путь к журналу.
    """
    import traceback
    report = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path = _crash_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(f"python={sys.version}\nexecutable={sys.executable}\n")
            f.write(f"frozen={getattr(sys, 'frozen', False)}\ncwd={os.getcwd()}\n")
            f.write(report + "\n")
    except Exception:
        pass
    print(report, file=sys.stderr)
    summary = f"{type(exc).__name__}: {exc}"[:400]
    try:
        from app import desktop
        desktop.message_box(
            "MemuAI Proxy — ошибка запуска / startup error",
            "MemuAI Proxy не смог запуститься.\n\n"
            f"{summary}\n\n"
            f"Подробный отчёт: {log_path}\n\n"
            "Что помогает чаще всего: запустить приложение обычным двойным "
            "кликом (не от имени администратора), проверить, что антивирус не "
            "блокирует файлы MemuAI Proxy, и переустановить приложение.",
            error=True,
        )
    except Exception:
        pass

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    except BaseException as _exc:  # noqa: BLE001
        _report_startup_crash(_exc)
        sys.exit(1)
