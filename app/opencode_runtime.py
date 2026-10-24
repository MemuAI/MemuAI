"""User-installed opencode2api process, owned by Proxy, never a public listener.

The separate upstream GPL-3.0 executable is downloaded only on an explicit
install action. Its license/readme stay beside it. No upstream source is linked
into MemuAI. Release assets are pinned and verified before extraction/execution.
"""
from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile

from . import voice_downloads
from .config import DATA_DIR

VERSION = "v1.4.3"
PROJECT = "https://github.com/jasonxu114514/opencode2api"
SOURCE = PROJECT + "/tree/" + VERSION
ASSETS = {
    "amd64": (4191989, "5b548ffa21da8ac1e9c81dd5579471ccb1c70bf3e673602656ff3e003ad39c34"),
    "arm64": (3777020, "6821902d28c2178479be8bd9be450251938a4344b9d34e1332be0dfe362bb01a"),
}


def architecture():
    machine = platform.machine().lower()
    return ({"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, "")
            if os.name == "nt" else "")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json_file(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        json.dump(data, stream, ensure_ascii=False)
    os.replace(temp, path)


def _manual_availability(path, models, upstream="https://opencode.ai/zen"):
    """Pinned v1.4.3's supported manual registry: no automatic generation probes.

    Source: internal/config/secrets.go Fingerprint and models/availability.go.
    This controls probing only, never authentication, routing or model prices.
    """
    fingerprint = hashlib.sha256(upstream.encode()).hexdigest()[:16]
    target = Path(str(path) + "." + fingerprint + ".availability.json")
    _json_file(target, {model: {"model": model, "manual": True, "disabled": False,
                              "probe_version": 1, "reason": "manually_enabled",
                              "next_check": "9999-01-01T00:00:00Z"} for model in models})


class GatewayRuntime:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(DATA_DIR) / "opencode2api"
        self._guard = threading.RLock()
        self._install = {}
        self._worker = None
        self._cancel = threading.Event()
        self._entries = {}
        self._closed = False

    def _directory(self):
        arch = architecture()
        if not arch:
            raise RuntimeError("Автоустановка opencode2api поддерживает Windows x64/ARM64; для другой ОС подключите свой шлюз / Automatic setup requires Windows x64/ARM64; connect an external gateway on other systems")
        return self.root / (VERSION + "-" + arch)

    def binary(self):
        directory = self._directory()
        exe = directory / "opencode2api.exe"
        try:
            manifest = json.loads((directory / "install.json").read_text("utf-8"))
            if manifest.get("archive_sha256") == ASSETS[architecture()][1] and _digest(exe) == manifest.get("binary_sha256"):
                return exe
        except (OSError, ValueError, KeyError):
            pass
        return None

    def status(self):
        supported = bool(architecture())
        try:
            installed = self.binary() is not None
        except RuntimeError:
            installed = False
        with self._guard:
            # No process locks or joins here: installation/startup never blocks polling.
            state = {key: self._install.get(key) for key in (
                "stage", "progress", "bytes_downloaded", "total_bytes", "error") if key in self._install}
            running = sum(e.get("process") is not None and e["process"].poll() is None for e in self._entries.values())
            active = bool(self._worker and self._worker.is_alive())
        return {**state, "installed": installed, "supported": supported, "installing": active,
                "running": running, "version": VERSION, "license": "GPL-3.0", "source_url": SOURCE,
                "project_url": PROJECT, "download_bytes": ASSETS.get(architecture(), (0, ""))[0]}

    def install(self):
        with self._guard:
            if self._closed:
                raise RuntimeError("Proxy выключается / Proxy is shutting down")
            self._directory()
            if self._worker and self._worker.is_alive():
                return self.status()
            if self.binary():
                return self.status()
            self._cancel = threading.Event()
            self._install = {"stage": "preparing", "progress": 0, "bytes_downloaded": 0}
            self._worker = threading.Thread(target=self._install_worker, daemon=True, name="opencode2api-install")
            self._worker.start()
        return self.status()

    def cancel_install(self):
        self._cancel.set()
        if self._worker and self._worker.is_alive():
            self._install["stage"] = "cancelling"
        voice_downloads.interrupt(self._install)
        return self.status()

    def _install_worker(self):
        state, cancel = self._install, self._cancel
        try:
            arch = architecture()
            size, sha = ASSETS[arch]
            directory = self._directory()
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="install-", dir=self.root) as work:
                stage = Path(work)
                name = f"opencode2api_{VERSION}_windows_{arch}.zip"
                voice_downloads.transfer([{"name": name, "size": size, "sha256": sha,
                    "url": PROJECT + "/releases/download/" + VERSION + "/" + name}], stage, state, cancel)
                if cancel.is_set():
                    raise asyncio.CancelledError()
                with zipfile.ZipFile(stage / name) as archive:
                    # Read named files, never extract paths supplied by an archive.
                    for target in ("opencode2api.exe", "LICENSE", "README.md"):
                        matches = [i for i in archive.infolist() if PurePosixPath(i.filename).name == target and not i.is_dir()]
                        if len(matches) != 1 or not 0 < matches[0].file_size <= 50 * 1024 * 1024:
                            raise RuntimeError("Некорректный архив opencode2api / Invalid opencode2api archive")
                        (stage / target).write_bytes(archive.read(matches[0]))
                manifest = {"version": VERSION, "archive_sha256": sha, "binary_sha256": _digest(stage / "opencode2api.exe"),
                            "source_url": SOURCE, "license": "GPL-3.0"}
                if cancel.is_set():
                    raise asyncio.CancelledError()
                directory.mkdir(parents=True, exist_ok=True)
                for name in ("opencode2api.exe", "LICENSE", "README.md"):
                    os.replace(stage / name, directory / name)
                _json_file(directory / "install.json", manifest)  # installed marker written last
                state.update(stage="done", progress=100)
        except asyncio.CancelledError:
            state.update(stage="cancelled", progress=None)
        except Exception as exc:
            from .providers.base import safe_provider_error
            state.update(stage="error", error=safe_provider_error(exc))

    @staticmethod
    def _request(entry):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(entry["base_url"] + "/models", headers={"Authorization": "Bearer " + entry["key"]})
        with opener.open(req, timeout=2) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Слишком большой каталог шлюза / Gateway catalogue is too large")
        data = json.loads(raw)
        return isinstance(data.get("data"), list) and bool(data["data"])

    async def ensure(self, account_id, proxy="", *, models=()):
        return await asyncio.to_thread(self._ensure_sync, str(account_id), str(proxy or ""), tuple(sorted(set(models))))

    def _ensure_sync(self, account_id, proxy, models=()):
        with self._guard:
            if self._closed:
                raise RuntimeError("Proxy выключается / Proxy is shutting down")
            entry = self._entries.setdefault(account_id, {"lock": threading.RLock()})
        with entry["lock"]:
            if self._closed:
                raise RuntimeError("Proxy выключается / Proxy is shutting down")
            if (entry.get("process") is not None and entry["process"].poll() is None
                    and entry.get("proxy") == proxy and entry.get("models", ()) == models):
                return {key: entry[key] for key in ("base_url", "key")}
            self._stop_entry(entry)
            exe = self.binary()
            if exe is None:
                raise RuntimeError("Сначала установите opencode2api: Добавить аккаунт → OpenCode → Бесплатные модели → Установить шлюз. API-ключ и сайт OpenCode для этого не нужны / First install the gateway: Add account → OpenCode → Free models → Install gateway. No API key or OpenCode website is needed")
            directory = self.root / "runs" / hashlib.sha256(account_id.encode()).hexdigest()[:32]
            directory.mkdir(parents=True, exist_ok=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            key = secrets.token_hex(32)
            config = {"listen": f"127.0.0.1:{port}", "server_keys": [key], "zen_keys": [], "go_keys": [],
                      "anonymous": True, "models": {"free_only": True, "refresh_seconds": 300},
                      "webui": {"enabled": False}, "proxies": [proxy or "direct"],
                      "logging": {"level": "error", "dump_request_bodies": False},
                      "retry": {"max_attempts": 2, "timeout_seconds": 120}}
            path = directory / "config.json"
            _json_file(path, config)
            _manual_availability(path, models)
            log = directory / "gateway.log"
            if log.exists() and log.stat().st_size > 1024 * 1024:
                log.write_bytes(b"")
            with log.open("ab") as output:
                child = subprocess.Popen([str(exe.resolve()), "-config", str(path.resolve())], cwd=directory,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if os.name == "nt":
                from .local_models import _kill_with_proxy
                _kill_with_proxy(child)
            entry.update(process=child, proxy=proxy, models=models, base_url=f"http://127.0.0.1:{port}/v1", key=key, path=path)
            try:
                deadline = time.monotonic() + 65
                while time.monotonic() < deadline:
                    if self._closed or child.poll() is not None:
                        raise RuntimeError(f"Локальный opencode2api остановлен (код {child.poll()}); журнал: {log} / Local opencode2api stopped; see its log")
                    try:
                        if self._request(entry):
                            return {key: entry[key] for key in ("base_url", "key")}
                    except (OSError, ValueError):
                        pass
                    time.sleep(.25)
                raise RuntimeError("Шлюз запущен, но каталог не загрузился. Проверьте интернет и прокси аккаунта, затем нажмите Проверить / Gateway started but could not load models. Check the connection and account proxy, then retry Check")
            except BaseException:
                self._stop_entry(entry)
                raise

    @staticmethod
    def _stop_entry(entry):
        child = entry.pop("process", None)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        path = entry.pop("path", None)
        if path:
            Path(path).unlink(missing_ok=True)
        entry.pop("key", None)

    def stop(self, account_id):
        with self._guard:
            entry = self._entries.pop(str(account_id), None)
        if entry:
            with entry["lock"]:
                self._stop_entry(entry)

    def shutdown(self, *, permanent=False):
        self._closed = True
        self.cancel_install()
        with self._guard:
            ids = list(self._entries)
        for account_id in ids:
            self.stop(account_id)
        worker = self._worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=4)
        if not permanent:
            self._closed = False


runtime = GatewayRuntime()
atexit.register(lambda: runtime.shutdown(permanent=True))
