"""Фоновые автообновления отдельного MemuAI Proxy.

Только для Proxy, установленного без MemuAI: когда рядом есть сам MemuAI,
обновлениями занимается он, а этот раздел в панели просто не показывается.
Устроено как обновления MemuMusic: проверка и скачивание идут в фоне,
скачанный установщик проверяется по размеру, сигнатуре MZ и контрольной
сумме, а перед запуском живой релиз запрашивается ещё раз — кэш на диске
сам по себе ничему не доверяет.

Состояние лежит в ``<DATA_DIR>/updates/state.json``, настройки — в обычных
настройках Proxy (см. ``app/config.py``), поэтому их видно и в панели.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

# E07: Request must be importable at module level so route annotations
# resolve before register_proxy_update_routes() runs (fastapi is a hard
# dependency of the project, so this import is always available).
from fastapi import Request

from . import update_release
from .config import DATA_DIR, UPDATE_CHANNELS, UPDATE_INTERVAL_HOURS, config_store
from .storage import load_json_with_recovery, save_json
from .version import APP_VERSION

REPOSITORY = update_release.REPOSITORY
RELEASES_URL = update_release.RELEASES_URL
LATEST_URL = update_release.LATEST_URL
MAX_INSTALLER = update_release.MAX_INSTALLER_SIZE
INSTALLER_FILE = "MemuAIProxy-Setup.exe"
CHECKSUM_SUFFIX = ".sha256"
INTERVALS = UPDATE_INTERVAL_HOURS
CHANNELS = UPDATE_CHANNELS

_SHA = re.compile(r"^[0-9a-f]{64}$")
_ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
# Ключ реестра, под которым кастомный установщик регистрирует набор MemuAI.
_SUITE_REG_KEY = "{7C2A93E1-5B44-4E0D-9F61-8A2B43E9A122}_is1"
_UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
# Запись memuai.json старше месяца при не отвечающем MemuAI считаем
# остатком от удалённого набора, а не живым соседом.
_SUITE_FILE_FRESH_SECONDS = 30 * 24 * 3600


class UpdateError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def installer_name(version: str) -> str:
    return f"MemuAIProxy-Setup-{str(version).lstrip('vV')}.exe"


def _checksum(raw: bytes, name: str) -> str:
    """Разбирает файл ``<установщик>.sha256``: хеш и, если есть, имя файла."""
    if len(raw) > 4096:
        raise UpdateError("checksum_invalid")
    try:
        tokens = raw.decode("utf-8-sig").strip().split()
    except UnicodeError as exc:
        raise UpdateError("checksum_invalid") from exc
    if not 1 <= len(tokens) <= 2 or not _SHA.fullmatch(tokens[0]):
        raise UpdateError("checksum_invalid")
    if len(tokens) == 2 and tokens[1].lstrip("*") != name:
        raise UpdateError("checksum_invalid")
    return tokens[0].lower()


def _valid_url(url: str, hosts: set[str]) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in hosts
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        return False


def _read_runtime_file(path: Path) -> dict:
    """Читает memuai.json, принимая только локальный http-адрес без пароля.

    Та же проверка, что в main.py: адрес должен вести на loopback, иначе
    файл считается чужим и игнорируется.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    url = str(data.get("url", ""))
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return {}
    if (parsed.scheme.lower() != "http"
            or (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1")
            or parsed.username is not None or parsed.password is not None
            or port is None or not 1 <= port <= 65535
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        return {}
    return data


def _ping_memuai(url: str, timeout: float = 0.75) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/pg/ping", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _suite_registered() -> bool:
    """Только чтение реестра: установлен ли набор MemuAI (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    key_path = _UNINSTALL_ROOT + "\\" + _SUITE_REG_KEY
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ):
                return True
        except OSError:
            continue
    return False


def _suite_data_dir(base: Path, environ: Any) -> Path:
    """Папка данных набора MemuAI, соседняя с папкой данных Proxy."""
    if os.path.basename(os.path.normpath(str(base))).lower() == "memuaiproxy":
        return base.parent / "MemuAI"
    appdata = environ.get("APPDATA") if environ is not None else None
    if appdata:
        return Path(appdata) / "MemuAI"
    return base


def edition_info(*, data_dir: str | os.PathLike[str] | None = None,
                 environ: Any = None, ping: Callable[[str], bool] | None = None,
                 registry_check: Callable[[], bool] | None = None,
                 now: float | None = None) -> dict:
    """Что это за установка и есть ли рядом живой MemuAI.

    ``proxy_only`` — флаг MEMUAI_PROXY_ONLY или папка данных MemuAIProxy.
    ``memuai_present`` — хотя бы один из трёх сигналов: файл
    ``memuai.json`` набора (свежий или с отвечающим адресом), ping его
    runtime-URL или запись набора в реестре Windows. Ничего не пишет и
    никогда не бросает исключение: при сомнениях считаем, что MemuAI нет.
    """
    env = os.environ if environ is None else environ
    try:
        base = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
    except (TypeError, ValueError):
        base = Path(DATA_DIR)
    try:
        proxy_only = env.get("MEMUAI_PROXY_ONLY") == "1" or \
            os.path.basename(os.path.normpath(str(base))).lower() == "memuaiproxy"
    except Exception:
        proxy_only = False
    suite_dir = _suite_data_dir(base, env)
    candidates = [suite_dir / "memuai" / "memuai.json"]
    if sys.platform != "win32":
        xdg = env.get("XDG_DATA_HOME") if env is not None else None
        if xdg:
            candidates.append(Path(xdg) / "MemuAI" / "memuai" / "memuai.json")
    seen: set[str] = set()
    runtime: dict = {}
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file():
                runtime = _read_runtime_file(candidate)
                break
        except OSError:
            continue
    url = str(runtime.get("url", "")) if runtime else ""
    ping_ok = False
    if url:
        try:
            ping_ok = ping(url) if ping is not None else _ping_memuai(url)
        except Exception:
            ping_ok = False
    fresh = False
    if runtime:
        updated = runtime.get("updated")
        moment = time.time() if now is None else now
        fresh = updated is None or (isinstance(updated, (int, float))
                                    and -60 <= moment - float(updated) <= _SUITE_FILE_FRESH_SECONDS)
    try:
        registered = registry_check() if registry_check is not None else _suite_registered()
    except Exception:
        registered = False
    memuai_present = bool(ping_ok or registered or (url and (ping_ok or fresh)))
    return {"edition": "proxy-only" if proxy_only else "suite",
            "proxy_only": bool(proxy_only), "memuai_present": bool(memuai_present)}


def _default_offer_fetcher(current: str, channel: str) -> tuple[dict, str | None]:
    """Живой релиз с GitHub: предложение + адрес checksum-файла, если есть."""
    async def _fetch() -> dict:
        import httpx
        source = os.environ.get("MEMUAI_UPDATE_MANIFEST_URL", "").strip() or LATEST_URL
        async with httpx.AsyncClient(
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "MemuAI-Proxy-Updater"},
                timeout=15, follow_redirects=True) as client:
            return await update_release.fetch_payload(client, source)

    payload = asyncio.run(_fetch())
    offer = update_release.release_offer(payload, current, installer="proxy")
    checksum_url = None
    if offer.get("update_available"):
        wanted = installer_name(offer["latest_version"]) + CHECKSUM_SUFFIX
        for asset in payload.get("assets") or []:
            if (isinstance(asset, dict) and asset.get("name") == wanted
                    and asset.get("state", "uploaded") == "uploaded"):
                checksum_url = asset.get("browser_download_url") or None
                break
    return offer, checksum_url


def _default_client_factory():
    import httpx
    return httpx.Client(
        timeout=httpx.Timeout(60, connect=10), follow_redirects=False,
        headers={"User-Agent": "MemuAI-Proxy-Updater", "Accept": "application/octet-stream"})


class ProxyUpdater:
    def __init__(self, root: Path | None = None, current_version: str | None = None,
                 client_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 offer_fetcher: Callable[[str, str], tuple[dict, str | None]] | None = None):
        self.root = Path(root or Path(DATA_DIR) / "updates")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / INSTALLER_FILE
        self.current = current_version or APP_VERSION
        self.clock = clock
        self.client_factory = client_factory or _default_client_factory
        self.offer_fetcher = offer_fetcher or _default_offer_fetcher
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._handoff = None
        cached = load_json_with_recovery(self.root / "state.json", {})
        if not isinstance(cached, dict):
            cached = {}
        try:
            last_checked = float(cached.get("last_checked", 0))
            if not 0 <= last_checked <= self.clock() + 60:
                last_checked = 0
        except (ValueError, TypeError):
            last_checked = 0
        if cached.get("current_version") != self.current:
            last_checked = 0
        self._state = {"phase": "idle", "current_version": self.current,
                       "last_checked": last_checked, "latest": None,
                       "progress": 0, "total": 0, "error": "", "checksum": ""}
        if last_checked and cached.get("result") == "up_to_date":
            self._state["phase"] = "up_to_date"
        saved = cached.get("latest")
        if isinstance(saved, dict):
            try:
                # Кэш на диске не заслуживает доверия, пока его не подтвердит
                # тот же разбор, что и для живого релиза.
                restored = update_release.release_offer(
                    {"tag_name": "v" + str(saved["version"]), "assets": [{
                        "name": installer_name(saved["version"]),
                        "browser_download_url": saved["download_url"],
                        "size": saved["size"],
                        "digest": ("sha256:" + saved["sha256"]) if saved.get("sha256") else "",
                        "state": "uploaded"}]},
                    self.current, installer="proxy")
                if restored.get("update_available"):
                    self._state["latest"] = {
                        "version": restored["latest_version"],
                        "release_url": restored["release_url"],
                        "download_url": restored["download_url"],
                        "sha256": restored.get("sha256", ""),
                        "size": restored.get("size", 0),
                        "checksum_url": saved.get("checksum_url") or None}
                    expected = str(cached.get("download_checksum") or "")
                    if (_SHA.fullmatch(expected) and self.path.is_file()
                            and self.path.stat().st_size == restored.get("size", 0)):
                        self._state.update(phase="ready", checksum=expected.lower(),
                                           total=restored.get("size", 0),
                                           progress=restored.get("size", 0))
                    elif cached.get("result") == "available":
                        self._state["phase"] = "available"
            except (KeyError, TypeError, ValueError, OSError):
                pass

    def _config(self) -> dict:
        settings = getattr(config_store, "settings", None)
        interval = getattr(settings, "updates_interval_hours", 24)
        channel = getattr(settings, "updates_channel", "stable")
        return {"enabled": getattr(settings, "updates_enabled", True) is not False,
                "auto_download": getattr(settings, "updates_auto_download", True) is not False,
                "channel": channel if channel in CHANNELS else "stable",
                "interval_hours": interval if interval in INTERVALS else 24}

    def status(self) -> dict:
        with self._lock:
            state = copy.deepcopy(self._state)
        state.update(config=self._config(), busy=self._operation.locked(),
                     can_install=sys.platform == "win32" and bool(getattr(sys, "frozen", False)))
        return state

    def _save(self) -> None:
        with self._lock:
            data = {"last_checked": self._state["last_checked"],
                    "current_version": self.current, "result": self._state["phase"],
                    "latest": self._state["latest"],
                    "download_checksum": self._state["checksum"] if self._state["phase"] == "ready" else ""}
        save_json(self.root / "state.json", data, backup=False)

    def settings(self, changes: dict) -> dict:
        allowed = {"enabled", "auto_download", "channel", "interval_hours"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise UpdateError("invalid_settings")
        for key, value in changes.items():
            if key in ("enabled", "auto_download") and not isinstance(value, bool):
                raise UpdateError("invalid_settings")
            if key == "channel" and value not in CHANNELS:
                raise UpdateError("invalid_settings")
            if key == "interval_hours" and (not isinstance(value, int) or isinstance(value, bool)
                                            or value not in INTERVALS):
                raise UpdateError("invalid_settings")
        before = self._config()
        mapping = {"enabled": "updates_enabled", "auto_download": "updates_auto_download",
                   "interval_hours": "updates_interval_hours", "channel": "updates_channel"}
        rejected = config_store.apply_updates({mapping[key]: value for key, value in changes.items()})
        if rejected:
            raise UpdateError("invalid_settings")
        if "channel" in changes and changes["channel"] != before["channel"]:
            with self._lock:
                self._state.update(phase="idle", latest=None, checksum="", last_checked=0, error="")
            self._save()
        return self.status()

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, UpdateError):
            code = exc.code
        elif isinstance(exc, (OSError, ValueError)):
            code = "disk" if isinstance(exc, OSError) else "invalid_response"
        else:
            # Сюда попадают и сетевые ошибки httpx: модуль импортируется
            # лениво, поэтому по имени класс не проверяем.
            code = "network" if type(exc).__name__ in (
                "HTTPError", "RequestError", "HTTPStatusError", "ConnectError",
                "ReadTimeout", "ConnectTimeout") else "invalid_response"
        with self._lock:
            self._state.update(phase="error", error=code)

    def _live_offer(self) -> tuple[dict, str | None]:
        with self._lock:
            channel = self._config()["channel"]
        offer, checksum_url = self.offer_fetcher(self.current, channel)
        if not isinstance(offer, dict):
            raise UpdateError("invalid_response")
        if offer.get("status") == "missing_installer":
            raise UpdateError("installer_missing")
        if not offer.get("update_available"):
            return {"version": offer.get("latest_version", self.current),
                    "release_url": offer.get("release_url", RELEASES_URL),
                    "download_url": "", "sha256": "", "size": 0,
                    "checksum_url": None}, None
        return {"version": offer["latest_version"],
                "release_url": offer.get("release_url", RELEASES_URL),
                "download_url": offer["download_url"],
                "sha256": offer.get("sha256", "") or "",
                "size": offer.get("size", 0) or 0,
                "checksum_url": checksum_url}, checksum_url

    def _check(self) -> bool:
        candidate, _checksum_url = self._live_offer()
        with self._lock:
            old = self._state["latest"]
            has_candidate = bool(candidate["download_url"])
            retain = bool(has_candidate and old and old.get("version") == candidate["version"]
                          and old.get("sha256") == candidate["sha256"]
                          and self._state["checksum"] and self.path.is_file()
                          and self.path.stat().st_size == candidate["size"])
            self._state.update(phase="ready" if retain else "available" if has_candidate else "up_to_date",
                               latest=candidate if has_candidate else None,
                               last_checked=self.clock(), error="", progress=0, total=0,
                               checksum=self._state["checksum"] if retain else "")
            auto = self._config()["auto_download"] and has_candidate and not retain
        self._save()
        return auto

    @contextmanager
    def _asset_response(self, client: Any, url: str):
        """Потоковая загрузка ассета с ручным разбором редиректов.

        Принимаем только github-хосты: адрес из релиза не должен увести
        скачивание куда попало.
        """
        current = url
        for _ in range(6):
            if not _valid_url(current, _ASSET_HOSTS):
                raise UpdateError("untrusted_url")
            with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise UpdateError("network")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                yield response
                return
        raise UpdateError("untrusted_url")

    def _read_checksum(self, client: Any, url: str, name: str, offer_digest: str) -> str:
        with self._asset_response(client, url) as response:
            parts = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 4096:
                    raise UpdateError("checksum_invalid")
                parts.append(chunk)
        value = _checksum(b"".join(parts), name)
        if offer_digest and not hmac.compare_digest(value, offer_digest):
            raise UpdateError("checksum_mismatch")
        return value

    def _download(self) -> None:
        with self._lock:
            candidate = copy.deepcopy(self._state["latest"])
        if not candidate or not candidate.get("download_url"):
            raise UpdateError("no_update")
        name = installer_name(candidate["version"])
        temp = self.root / f"download-{uuid.uuid4().hex}.part"
        try:
            with self.client_factory() as client:
                expected = candidate.get("sha256") or ""
                if candidate.get("checksum_url"):
                    expected = self._read_checksum(client, candidate["checksum_url"], name, expected)
                digest = hashlib.sha256()
                size = 0
                first = b""
                with self._asset_response(client, candidate["download_url"]) as response:
                    with temp.open("xb") as handle:
                        for chunk in response.iter_bytes(128 * 1024):
                            if not first:
                                first = chunk[:2]
                            size += len(chunk)
                            if size > candidate["size"] or size > MAX_INSTALLER:
                                raise UpdateError("download_size")
                            handle.write(chunk)
                            digest.update(chunk)
                            with self._lock:
                                self._state.update(progress=size, total=candidate["size"])
                        handle.flush()
                        os.fsync(handle.fileno())
            if first != b"MZ" or size != candidate["size"]:
                raise UpdateError("download_size")
            if expected and not hmac.compare_digest(digest.hexdigest(), expected):
                raise UpdateError("checksum_mismatch")
            try:
                update_release.verify_installer(temp, candidate["size"], expected)
            except ValueError as exc:
                raise UpdateError("checksum_mismatch") from exc
            with self._lock:
                if not self._state["latest"] or self._state["latest"].get("version") != candidate["version"]:
                    raise UpdateError("update_changed")
                os.replace(temp, self.path)
                self._state.update(phase="ready", checksum=expected, error="",
                                   progress=size, total=size)
            self._save()
        finally:
            temp.unlink(missing_ok=True)

    def _schedule(self, phase: str, work: Callable) -> bool:
        if not self._operation.acquire(blocking=False):
            return False
        with self._lock:
            self._state.update(phase=phase, error="", progress=0, total=0)

        def worker() -> None:
            auto = False
            try:
                auto = bool(work())
            except Exception as exc:  # noqa: BLE001 - любая ошибка становится кодом состояния
                self._error(exc)
            finally:
                self._operation.release()
            if auto:
                self.download()

        threading.Thread(target=worker, name="proxy-updates", daemon=True).start()
        return True

    def check(self) -> bool:
        return self._schedule("checking", self._check)

    def download(self) -> bool:
        return self._schedule("downloading", self._download)

    def auto(self) -> bool:
        with self._lock:
            cfg, state = dict(self._config()), dict(self._state)
        if not cfg["enabled"]:
            return False
        if self.clock() - float(state["last_checked"] or 0) >= cfg["interval_hours"] * 3600:
            return self.check()
        if cfg["auto_download"] and state["phase"] == "available":
            return self.download()
        return False

    def install(self, launcher: Callable[..., Any] = subprocess.Popen) -> dict:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            raise UpdateError("unsupported")
        if not self._operation.acquire(blocking=False):
            raise UpdateError("busy")
        try:
            with self._lock:
                candidate = copy.deepcopy(self._state["latest"])
                stored_checksum = self._state["checksum"]
            if not candidate or not candidate.get("download_url") or not self.path.is_file():
                raise UpdateError("not_downloaded")
            # Кэш не заслуживает доверия: живой релиз запрашивается заново.
            try:
                live, checksum_url = self._live_offer()
            except Exception as exc:
                raise UpdateError("network") from exc
            if not live.get("download_url") or live["version"] != candidate["version"]:
                raise UpdateError("update_changed")
            expected = live.get("sha256") or ""
            if checksum_url:
                try:
                    with self.client_factory() as client:
                        expected = self._read_checksum(
                            client, checksum_url, installer_name(live["version"]), expected)
                except UpdateError:
                    raise
                except Exception as exc:
                    raise UpdateError("network") from exc
            if self.path.stat().st_size != live["size"]:
                raise UpdateError("checksum_mismatch")
            if stored_checksum and expected and not hmac.compare_digest(stored_checksum, expected):
                raise UpdateError("checksum_mismatch")
            try:
                update_release.verify_installer(self.path, live["size"], expected)
            except ValueError as exc:
                raise UpdateError("checksum_mismatch") from exc
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            try:
                child = launcher([str(self.path)], cwd=str(self.path.parent),
                                 env=update_release.installer_environment(),
                                 creationflags=flags, close_fds=True)
            except OSError as exc:
                raise UpdateError("launch_failure") from exc
            with self._lock:
                self._handoff = child
                self._state.update(phase="installing", error="")
            return {"launched": True}
        finally:
            self._operation.release()

    def close_after_handoff(self, close_window: Callable) -> None:
        with self._lock:
            child = self._handoff
        if child is None or (hasattr(child, "poll") and child.poll() is not None):
            self._error(UpdateError("launch_failure"))
            return
        try:
            close_window()
        except Exception:
            self._error(UpdateError("launch_failure"))


def register_proxy_update_routes(app: Any, updater: ProxyUpdater | None = None) -> ProxyUpdater:
    """Маршруты админки Proxy: статус, проверка, скачивание, установка, настройки.

    Авторизация — та же, что у остальных роутов панели (``admin_api._auth``):
    на loopback без пароля панель не спрашивает, в сети — спрашивает.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    # E07: Request must be importable at module level — the old code only
    # put it into globals() inside register(), so the annotation was
    # unresolvable before registration (and a static test flagged it).
    # The globals() line stays as a belt-and-braces for the same reason.
    globals()["Request"] = Request

    from .admin_api import _auth

    updater = updater or ProxyUpdater()
    app.state.proxy_updater = updater

    def _current(request: Request) -> ProxyUpdater:
        # Тесты и встраивание могут подменить updater через app.state.
        return getattr(request.app.state, "proxy_updater", None) or updater

    @app.get("/api/admin/updates/status")
    async def updates_status(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        return _current(request).status()

    @app.post("/api/admin/updates/check")
    async def updates_check(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        current = _current(request)
        return {"started": current.check(), "status": current.status()}

    @app.post("/api/admin/updates/download")
    async def updates_download(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        current = _current(request)
        return {"started": current.download(), "status": current.status()}

    @app.post("/api/admin/updates/settings")
    async def updates_settings(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        try:
            changes = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_settings"}, 400)
        try:
            return await asyncio.to_thread(_current(request).settings, changes)
        except UpdateError as exc:
            return JSONResponse({"error": exc.code}, 400)

    @app.post("/api/admin/updates/install")
    async def updates_install(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        info = edition_info()
        if not info["proxy_only"] or info["memuai_present"]:
            return JSONResponse({"error": "suite_managed"}, 409)
        callback = getattr(request.app.state, "shutdown_callback", None)
        if callback is None:
            return JSONResponse({"error": "unsupported"}, 409)
        current = _current(request)
        try:
            result = await asyncio.to_thread(current.install)
        except UpdateError as exc:
            return JSONResponse({"error": exc.code}, 400)
        # Даём браузеру получить ответ, прежде чем сервер закроется.
        # Подменённый updater в тестах/встраивании может не иметь
        # close_after_handoff — тогда просто вызовем callback по таймеру.
        handoff = getattr(current, "close_after_handoff", None)
        if handoff is None:
            timer = threading.Timer(0.6, callback)
        else:
            timer = threading.Timer(0.6, handoff, args=(callback,))
        timer.daemon = True
        timer.start()
        return result

    return updater
