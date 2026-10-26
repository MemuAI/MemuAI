"""Free outside address for the public endpoint: a Cloudflare Tunnel run by ``cloudflared``.

Two modes:

* **Quick Tunnel** (default, no account): ``cloudflared tunnel --url http://127.0.0.1:<port>``.
  Cloudflare prints a random ``https://<words>.trycloudflare.com`` address; we read it from the
  process output. The address is temporary: it changes on every start, and Cloudflare gives
  Quick Tunnels no uptime guarantee (meant for testing / personal use).
* **Named tunnel** (optional): ``cloudflared tunnel run --token <token>`` with a token the user
  created in the Cloudflare Zero Trust dashboard. Its hostname is fixed there; the user types it
  into ``hostname`` so the panel can show it.

The binary is looked up on PATH and in ``DATA_DIR/bin``. ``download()`` fetches the official
release asset from github.com/cloudflare/cloudflared — only after an explicit confirmation from
the panel. The token is kept in its own file and never sent back to the panel.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import re
import shutil
import subprocess

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger("memuai.public.tunnel")

QUICK_URL = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com(?![a-z0-9-]|\.[a-z0-9])", re.I)
RELEASE_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-=.+/]{20,4096}$")

def _token_from_paste(raw: str) -> str:
    """Round 33: the Cloudflare dashboard shows the token only inside an install command
    («cloudflared.exe service install eyJ…» or «cloudflared tunnel run --token eyJ…»), so people paste
    the whole line. Take the token out of it; a bare token passes through unchanged."""
    text = str(raw or "").strip().strip('"\'')
    if not re.search(r"\s", text):
        return text
    words = [w.strip('"\'') for w in text.split()]
    for i, word in enumerate(words[:-1]):
        if word == "--token":
            return words[i + 1]
    candidates = [w for w in words if _TOKEN_RE.fullmatch(w) and not w.lower().endswith((".exe", "cloudflared"))]
    return max(candidates, key=len) if candidates else text

def parse_quick_url(text: str) -> str:
    """The trycloudflare address in a chunk of cloudflared output ("" when none).

    ``api.trycloudflare.com`` is the service cloudflared talks to, not the tunnel address."""
    for match in QUICK_URL.finditer(str(text or "")):
        url = match.group(0).lower()
        if url != "https://api.trycloudflare.com":
            return url
    return ""

def asset_name(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    """Official release asset for this OS / CPU ("" when there is no plain binary for it)."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "386" if machine in ("x86", "i386", "i686") else "amd64"
    if system == "windows":
        return f"cloudflared-windows-{arch}.exe"
    if system == "linux":
        return f"cloudflared-linux-{arch}"
    if system == "darwin":
        return f"cloudflared-darwin-{'arm64' if arch == 'arm64' else 'amd64'}.tgz"
    return ""

def _data_dir() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR)

def _bin_path() -> Path:
    return _data_dir() / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")

def _conf_path() -> Path:
    return _data_dir() / "public-tunnel.json"

def find_binary() -> str:
    own = _bin_path()
    if own.is_file():
        return str(own)
    found = shutil.which("cloudflared")
    if found:
        return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            candidate = Path(base) / "cloudflared" / "cloudflared.exe" if base else None
            if candidate and candidate.is_file():
                return str(candidate)
    return ""

def build_command(binary: str, port: int, *, token: str = "", tls: bool = False) -> List[str]:
    if token:
        return [binary, "tunnel", "--no-autoupdate", "run", "--token", token]
    scheme = "https" if tls else "http"
    command = [binary, "tunnel", "--no-autoupdate", "--url", f"{scheme}://127.0.0.1:{int(port)}"]
    if tls:
        command.append("--no-tls-verify")
    return command

_DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")

def download_host_ok(host: str) -> bool:
    """github.com / *.githubusercontent.com only ("evilgithub.com" is not GitHub)."""
    host = str(host or "").lower().rstrip(".")
    return any(host == base or host.endswith("." + base) for base in _DOWNLOAD_HOSTS)

class Tunnel:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.proc: Optional[subprocess.Popen] = None
        self.url = ""
        self.mode = ""
        self.error = ""
        self.error_en = ""
        self.started_at = 0.0
        self.port = 0
        self.log: Deque[str] = deque(maxlen=60)
        self.downloading = False

    def config(self) -> Dict[str, Any]:
        try:
            raw = json.loads(_conf_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        return {"mode": "named" if raw.get("mode") == "named" else "quick",
                "hostname": str(raw.get("hostname") or "")[:253], "token": str(raw.get("token") or ""),
                "autostart": raw.get("autostart") is True}

    def save_config(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        current = self.config()
        if "mode" in changes:
            current["mode"] = "named" if changes.get("mode") == "named" else "quick"
        if "hostname" in changes:
            host = re.sub(r"^[a-z]+://", "", str(changes.get("hostname") or "").strip(), flags=re.I).strip("/ ")
            current["hostname"] = host if re.fullmatch(r"[0-9A-Za-z.\-]{0,253}", host) else current["hostname"]
        if "token" in changes:
            token = _token_from_paste(str(changes.get("token") or ""))
            if token and not _TOKEN_RE.fullmatch(token):
                raise ValueError("Токен туннеля выглядит неверно")
            current["token"] = token
        if "autostart" in changes:
            current["autostart"] = changes.get("autostart") is True
        from .storage import save_json
        save_json(_conf_path(), current, backup=False)
        try:
            os.chmod(_conf_path(), 0o600)
        except OSError:
            pass
        return current

    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            conf = self.config()
            alive = self.running()
            if alive:
                state = "running" if (self.url or self.mode == "named") else "starting"
            else:
                state = "error" if self.error else "stopped"
            url = self.url if alive else ""
            if alive and self.mode == "named" and conf["hostname"]:
                url = f"https://{conf['hostname']}"
            return {"state": state, "url": url, "api_base": f"{url}/v1" if url else "", "mode": self.mode or conf["mode"],
                    "error": self.error, "error_en": self.error_en or self.error, "binary": find_binary(), "asset": asset_name(), "downloading": self.downloading,
                    "since": int(self.started_at) if alive else 0, "port": self.port if alive else 0,
                    "config": {"mode": conf["mode"], "hostname": conf["hostname"], "has_token": bool(conf["token"]),
                               "autostart": conf["autostart"]},
                    "log": list(self.log)[-12:]}

    def _set_error(self, message: str, message_en: str) -> None:
        """The error shown in the panel, in both languages (the English UI must not get Russian)."""
        self.error, self.error_en = message, message_en

    def start(self, port: int, *, tls: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self.running():
                self.stop()
            binary = find_binary()
            if not binary:
                self._set_error("cloudflared не найден — скачайте его кнопкой или установите сами",
                           "cloudflared was not found — download it with the button or install it yourself")
                return self.status()
            conf = self.config()
            named = conf["mode"] == "named"
            if named and not conf["token"]:
                self._set_error("Для именованного туннеля нужен токен из Cloudflare Zero Trust",
                           "A named tunnel needs a token from Cloudflare Zero Trust")
                return self.status()
            command = build_command(binary, port, token=conf["token"] if named else "", tls=tls)
            env = None
            if named:

                command = command[:-2]
                env = {**os.environ, "TUNNEL_TOKEN": conf["token"]}
            self.url, self.mode, self.port = "", ("named" if named else "quick"), int(port)
            self._set_error("", "")
            self.log.clear()
            flags = 0x08000000 if os.name == "nt" else 0
            try:
                self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, creationflags=flags, env=env)
            except OSError as exc:
                self.proc = None
                self._set_error(f"Не удалось запустить cloudflared: {exc}"[:300], f"Could not start cloudflared: {exc}"[:300])
                return self.status()
            self.started_at = time.time()
            threading.Thread(target=self._read, args=(self.proc, conf["token"]), daemon=True,
                             name="cloudflared-reader").start()

        for _ in range(60):
            if self.url or not self.running() or self.mode == "named":
                break
            time.sleep(0.25)
        return self.status()

    def _read(self, proc: subprocess.Popen, token: str) -> None:
        stream = proc.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if token:
                line = line.replace(token, "***")
            with self._lock:
                if proc is not self.proc:
                    break
                self.log.append(line[:300])
                found = parse_quick_url(line)
                if found and not self.url:
                    self.url = found
                    log.info("quick tunnel: %s", found)
        code = proc.poll() if proc.poll() is not None else proc.wait()
        with self._lock:
            if proc is self.proc and code not in (0, None) and not self.error:
                tail = next((item for item in reversed(self.log) if "ERR" in item or "error" in item.lower()), "")
                tail = f": {tail[-200:]}" if tail else ""
                self._set_error(f"cloudflared завершился (код {code}){tail}", f"cloudflared exited (code {code}){tail}")

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            proc, self.proc = self.proc, None
            self.url = ""
            self._set_error("", "")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        elif proc:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return self.status()

    def download(self) -> Dict[str, Any]:
        """Fetch the official release asset into DATA_DIR/bin (blocking; call from a thread)."""
        name = asset_name()
        if not name:
            raise ValueError("Для этой системы нет готового cloudflared — установите его вручную")
        import httpx
        with self._lock:
            if self.downloading:
                raise ValueError("Загрузка cloudflared уже идёт")
            self.downloading = True
        target = _bin_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        try:
            with httpx.stream("GET", RELEASE_BASE + name, follow_redirects=True, timeout=120) as resp:
                if resp.status_code != 200:
                    raise ValueError(f"GitHub ответил {resp.status_code}")
                host = (resp.url.host or "").lower()
                if resp.url.scheme != "https" or not download_host_ok(host):
                    raise ValueError(f"Неожиданный адрес загрузки: {host}")
                size = 0
                with open(temp, "wb") as handle:
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > 200 * 1024 * 1024:
                            raise ValueError("Файл слишком большой")
                        handle.write(chunk)
            if size < 1_000_000:
                raise ValueError("Скачанный файл слишком мал — загрузка не удалась")
            if name.endswith(".tgz"):
                import tarfile
                with tarfile.open(temp) as archive:
                    member = next((m for m in archive.getmembers() if m.isfile() and m.name.endswith("cloudflared")), None)
                    if member is None:
                        raise ValueError("В архиве нет cloudflared")
                    data = archive.extractfile(member).read()  # type: ignore[union-attr]
                temp.write_bytes(data)
            os.replace(temp, target)
            if os.name != "nt":
                os.chmod(target, 0o755)
            return {"ok": True, "path": str(target), "asset": name}
        finally:
            self.downloading = False
            try:
                temp.unlink()
            except OSError:
                pass

tunnel = Tunnel()

@atexit.register
def _stop_on_exit() -> None:
    try:
        if tunnel.running():
            tunnel.stop()
    except Exception:
        pass

__all__ = ["tunnel", "parse_quick_url", "asset_name", "build_command", "find_binary", "download_host_ok", "Tunnel"]

