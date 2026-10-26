"""DFY proxy (Round 30): every request of the DFY browser goes through the user's proxy.

Chromium has ``--proxy-server`` but cannot send a login/password to a SOCKS5 proxy at all and
asks for an HTTP proxy's password in a dialog. So the DFY browser always talks to a tiny local
relay (``ProxyRelay``, 127.0.0.1, random port, no auth) and the relay talks to the user's proxy
(HTTP, HTTPS or SOCKS5, with or without a login):

* ``CONNECT host:port`` (HTTPS, WebSocket, wss) → a tunnel through the upstream proxy;
* ``GET http://host/…`` (plain HTTP) → forwarded with ``Proxy-Authorization`` (HTTP upstream) or
  through a SOCKS5 tunnel in origin form; ``Connection: close`` so every request is authorized.

No leaks: the browser gets ``--proxy-bypass-list=<-loopback>`` (nothing bypasses the proxy),
``--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1`` (the browser cannot resolve a name
by itself — host names reach the proxy, SOCKS5 gets them as domain names = socks5h), QUIC off
(UDP never goes through an HTTP proxy) and the WebRTC policy ``disable_non_proxied_udp`` (no local
IP candidates, no STUN past the proxy). If the proxy is down the requests fail — never direct.

Settings live in ``DATA_DIR/memuai/dfy_proxy.json`` per machine (``"local"`` or a remote
machine id, see r30 remote DFY); the login/password are encrypted in a separate
``SecretVault`` (``dfy_proxy_secrets/``, DPAPI on Windows) that the DFY model never sees.

The relay part is stdlib-only, so it can run on a remote machine as well:
``python -m app.dfy_proxy relay`` reads ``{"scheme","host","port","username","password"}`` as one
JSON line from stdin, prints ``{"port": N}`` and serves until stdin closes.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMES = ("http", "https", "socks5")
DEFAULT_PORTS = {"http": 8080, "https": 443, "socks5": 1080}
HEAD_LIMIT = 64 * 1024
CONNECT_TIMEOUT = 15.0
IP_SERVICES = ("https://api.ipify.org?format=json", "https://ifconfig.me/ip", "https://icanhazip.com")
_HOST_RE = re.compile(r"^[A-Za-z0-9._\-\[\]:]{1,253}$")

class ProxyError(Exception):
    """An error with a code (``auth`` | ``refused`` | ``timeout`` | ``dns`` | ``upstream`` | ``config``) and both languages."""

    def __init__(self, code: str, ru: str, en: str) -> None:
        super().__init__(ru)
        self.code, self.ru, self.en = code, ru, en

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru

@dataclass(frozen=True)
class Upstream:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def auth(self) -> bool:
        return bool(self.username or self.password)

    def fingerprint(self) -> str:
        """Changes when the browser must be restarted (credentials are not part of it: the relay re-reads them)."""
        return f"{self.scheme}://{self.host}:{self.port}"

def parse_address(raw: Any) -> Dict[str, Any]:
    """``socks5://user:pass@host:1080`` / ``host:port`` → ``{scheme?, host, port?, username?, password?}``."""
    import urllib.parse
    text = str(raw or "").strip()
    if not text:
        return {}
    if "://" not in text:
        text = "http://" + text if not text.startswith("//") else "http:" + text
        guessed = True
    else:
        guessed = False
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise ProxyError("config", "Некорректный адрес прокси", "Invalid proxy address") from None
    scheme = parts.scheme.lower()
    scheme = "socks5" if scheme in ("socks", "socks5", "socks5h") else scheme
    out: Dict[str, Any] = {"host": (parts.hostname or "").strip()}
    if not guessed:
        out["scheme"] = scheme
    if port:
        out["port"] = port
    if parts.username:
        out["username"] = urllib.parse.unquote(parts.username)
    if parts.password:
        out["password"] = urllib.parse.unquote(parts.password)
    return out

def normalize(raw: Any) -> Dict[str, Any]:
    """Validated public settings ``{enabled, scheme, host, port}`` (raises ``ProxyError`` code ``config``)."""
    data = raw if isinstance(raw, dict) else {}
    enabled = data.get("enabled", False)
    enabled = enabled if isinstance(enabled, bool) else str(enabled).lower() in ("1", "true", "yes", "on")
    scheme = str(data.get("scheme") or "http").lower().strip()
    scheme = "socks5" if scheme in ("socks", "socks5h") else scheme
    if scheme not in SCHEMES:
        raise ProxyError("config", "Тип прокси: HTTP, HTTPS или SOCKS5", "Proxy type: HTTP, HTTPS or SOCKS5")
    host = str(data.get("host") or "").strip().strip("[]")
    if host and (not _HOST_RE.match(host) or "/" in host):
        raise ProxyError("config", "Некорректный адрес прокси", "Invalid proxy address")
    try:
        port = int(data.get("port") or 0) or (DEFAULT_PORTS[scheme] if host else 0)
    except (TypeError, ValueError):
        port = -1
    if host and not 1 <= port <= 65535:
        raise ProxyError("config", "Порт прокси — от 1 до 65535", "The proxy port is 1–65535")
    if enabled and not host:
        raise ProxyError("config", "Укажите адрес прокси", "Enter the proxy address")
    return {"enabled": bool(enabled and host), "scheme": scheme, "host": host, "port": port if host else 0}

class ProxyStore:
    """Per-machine proxy settings; credentials in their own encrypted vault (never shown to the model)."""

    def __init__(self, root: Path) -> None:
        from .dfy_vault import SecretVault
        self.root = Path(root)
        self.path = self.root / "dfy_proxy.json"
        self.secrets = SecretVault(self.root / "dfy_proxy_secrets")
        self._lock = threading.RLock()

    @staticmethod
    def _machine(machine: Any) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]", "", str(machine or "local"))[:64]
        return value or "local"

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("machines"), dict):
            data = {"version": 1, "machines": {}}
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)

    def get(self, machine: Any = "local") -> Dict[str, Any]:
        """Public view: no password, the user name only as a mask."""
        key = self._machine(machine)
        with self._lock:
            row = self._read()["machines"].get(key) or {}
        out = {"machine": key, "enabled": False, "scheme": "http", "host": "", "port": 0, "auth": False, "username": ""}
        try:
            out.update(normalize(row))
        except ProxyError:
            pass
        secret = row.get("secret_id") or ""
        meta = self.secrets.get_meta(secret) if secret else None
        if meta:
            out["auth"] = True
            out["username"] = str(row.get("username_hint") or "")
        return out

    def machines(self) -> List[str]:
        with self._lock:
            return sorted(self._read()["machines"])

    def set(self, machine: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Save settings. ``username``/``password`` are write-only: an empty password keeps the saved one,
        ``clear_auth: true`` forgets the login. ``address`` (``socks5://user:pass@host:port``) may replace the fields."""
        key = self._machine(machine)
        body = dict(payload or {})
        if body.get("address"):
            body = {**body, **parse_address(body.pop("address"))}
        with self._lock:
            data = self._read()
            row = dict(data["machines"].get(key) or {})
            merged = {**{k: row.get(k) for k in ("enabled", "scheme", "host", "port")},
                      **{k: body[k] for k in ("enabled", "scheme", "host", "port") if k in body}}
            clean = normalize(merged)
            secret = str(row.get("secret_id") or "")

            username = str(body.get("username") or "").strip()[:2000]
            password = body.get("password")
            password = str(password) if isinstance(password, (str, int)) and str(password) else ""
            if body.get("clear_auth"):
                if secret:
                    self.secrets.delete(secret)
                secret = ""
                row.pop("username_hint", None)
            elif username or password:
                values = {"username": username, "password": password}
                if secret and self.secrets.get_meta(secret):
                    try:
                        old = self.secrets._values(secret)
                    except Exception:
                        old = {}
                    values = {"username": username or old.get("username", ""), "password": password or old.get("password", "")}
                    self.secrets.delete(secret)
                if not values["password"]:
                    raise ProxyError("config", "Укажите пароль прокси", "Enter the proxy password")
                meta = self.secrets.add("login", values, label="DFY proxy", domain="dfy-proxy.local")
                secret = meta["id"]
                user = values["username"]
                row["username_hint"] = (user[:2] + "•••" + user[-1:]) if len(user) > 4 else ("•••" if user else "")
            row.update(clean)
            row["secret_id"] = secret
            row["updated"] = int(time.time())
            data["machines"][key] = row
            self._write(data)
        return self.get(key)

    def delete(self, machine: Any) -> bool:
        key = self._machine(machine)
        with self._lock:
            data = self._read()
            row = data["machines"].pop(key, None)
            if row is None:
                return False
            if row.get("secret_id"):
                self.secrets.delete(row["secret_id"])
            self._write(data)
        return True

    def upstream(self, machine: Any = "local", *, override: Optional[Dict[str, Any]] = None) -> Optional[Upstream]:
        """The upstream with decrypted credentials, or ``None`` when the proxy is off.

        ``override`` (the settings form before saving, for «Check») may carry its own login/password."""
        key = self._machine(machine)
        with self._lock:
            row = dict(self._read()["machines"].get(key) or {})
        if override is not None:
            body = dict(override)
            if body.get("address"):
                body = {**body, **parse_address(body.pop("address"))}
            clean = normalize({**{k: row.get(k) for k in ("scheme", "host", "port")},
                               **{k: body[k] for k in ("scheme", "host", "port") if k in body}, "enabled": True})
        else:
            clean = normalize(row)
            if not clean["enabled"]:
                return None
        username = password = ""
        if override is not None and (override.get("username") or override.get("password")):
            username, password = str(override.get("username") or ""), str(override.get("password") or "")
        if not (username or password) and row.get("secret_id") and not (override or {}).get("clear_auth"):
            try:
                values = self.secrets._values(row["secret_id"])
            except Exception as exc:
                raise ProxyError("config", "Не удалось расшифровать логин прокси — введите его заново",
                                 "Could not decrypt the proxy login — enter it again") from exc
            username, password = values.get("username", ""), values.get("password", "")
        if not clean["host"]:
            raise ProxyError("config", "Укажите адрес прокси", "Enter the proxy address")
        return Upstream(clean["scheme"], clean["host"], int(clean["port"]), username, password)

def chromium_args(relay_port: int) -> List[str]:
    """Launch flags that send everything through the local relay and close the usual leaks."""
    return [f"--proxy-server=http://127.0.0.1:{int(relay_port)}",
            "--proxy-bypass-list=<-loopback>",
            "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--disable-features=DnsOverHttps,UseDnsHttpsSvcb"]

def direct_args(upstream: Upstream) -> List[str]:
    """Flags for a proxy WITHOUT a login when no relay can run (remote machines): Chromium talks to it directly.
    SOCKS5 in Chromium already sends host names to the proxy (remote DNS)."""
    if upstream.auth:
        raise ProxyError("config", "Прокси с логином работает только через релей", "A proxy with a login needs the relay")
    host = f"[{upstream.host}]" if ":" in upstream.host else upstream.host
    return [f"--proxy-server={upstream.scheme}://{host}:{upstream.port}", *chromium_args(0)[1:]]

def _basic(upstream: Upstream) -> str:
    raw = f"{upstream.username}:{upstream.password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")

async def _open_upstream(upstream: Upstream) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    context = None
    if upstream.scheme == "https":
        context = ssl.create_default_context()
    try:
        return await asyncio.wait_for(asyncio.open_connection(
            upstream.host, upstream.port, ssl=context, server_hostname=upstream.host if context else None),
            CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        raise ProxyError("timeout", "Прокси не отвечает (таймаут)", "The proxy does not answer (timeout)") from None
    except socket.gaierror:
        raise ProxyError("dns", f"Не найден адрес прокси {upstream.host}", f"Proxy host {upstream.host} not found") from None
    except ssl.SSLError as exc:
        raise ProxyError("upstream", f"Ошибка TLS при подключении к прокси: {exc.reason or exc}",
                         f"TLS error talking to the proxy: {exc.reason or exc}") from None
    except OSError:
        raise ProxyError("refused", f"Прокси {upstream.host}:{upstream.port} недоступен",
                         f"Proxy {upstream.host}:{upstream.port} is unreachable") from None

async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise ProxyError("upstream", "Слишком длинный заголовок", "Header too long") from None
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            raise ProxyError("upstream", "Соединение оборвалось", "The connection broke") from None
        raise ProxyError("upstream", "Прокси закрыл соединение", "The proxy closed the connection") from None

def _status(head: bytes) -> int:
    try:
        return int(head.split(b"\r\n", 1)[0].split()[1])
    except (IndexError, ValueError):
        return 0

async def open_tunnel(upstream: Upstream, host: str, port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A TCP stream to ``host:port`` through the upstream proxy (the host name is resolved by the proxy)."""
    reader, writer = await _open_upstream(upstream)
    try:
        if upstream.scheme == "socks5":
            await _socks5_connect(reader, writer, upstream, host, port)
        else:
            target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
            if upstream.auth:
                lines.append("Proxy-Authorization: " + _basic(upstream))
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace"))
            await writer.drain()
            head = await asyncio.wait_for(_read_head(reader), CONNECT_TIMEOUT)
            code = _status(head)
            if code == 407:
                raise ProxyError("auth", "Прокси отклонил логин или пароль (407)", "The proxy rejected the login or password (407)")
            if not 200 <= code < 300:
                raise ProxyError("upstream", f"Прокси не пропустил соединение с {host}:{port} (HTTP {code})",
                                 f"The proxy refused the connection to {host}:{port} (HTTP {code})")
        return reader, writer
    except asyncio.TimeoutError:
        writer.close()
        raise ProxyError("timeout", "Прокси не отвечает (таймаут)", "The proxy does not answer (timeout)") from None
    except BaseException:
        writer.close()
        raise

_SOCKS_REPLIES = {1: "general failure", 2: "not allowed by ruleset", 3: "network unreachable", 4: "host unreachable",
                  5: "connection refused", 6: "TTL expired", 7: "command not supported", 8: "address type not supported"}

async def _socks5_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, upstream: Upstream,
                          host: str, port: int) -> None:
    methods = b"\x00\x02" if upstream.auth else b"\x00"
    writer.write(b"\x05" + bytes([len(methods)]) + methods)
    await writer.drain()
    try:
        version, method = await asyncio.wait_for(reader.readexactly(2), CONNECT_TIMEOUT)
        if version != 5:
            raise ProxyError("upstream", "Это не SOCKS5-прокси", "This is not a SOCKS5 proxy")
        if method == 0xFF:
            raise ProxyError("auth", "SOCKS5-прокси требует логин и пароль", "The SOCKS5 proxy requires a login and password")
        if method == 0x02:
            user = upstream.username.encode("utf-8")[:255]
            secret = upstream.password.encode("utf-8")[:255]
            writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
            await writer.drain()
            _ver, status = await asyncio.wait_for(reader.readexactly(2), CONNECT_TIMEOUT)
            if status != 0:
                raise ProxyError("auth", "SOCKS5-прокси отклонил логин или пароль", "The SOCKS5 proxy rejected the login or password")
        elif method != 0x00:
            raise ProxyError("upstream", "SOCKS5-прокси предложил неподдерживаемый способ входа",
                             "The SOCKS5 proxy offered an unsupported auth method")
        try:
            ip = ipaddress.ip_address(host)
            address = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        except ValueError:
            name = host.encode("idna")[:255]
            address = b"\x03" + bytes([len(name)]) + name
        writer.write(b"\x05\x01\x00" + address + struct.pack(">H", int(port)))
        await writer.drain()
        head = await asyncio.wait_for(reader.readexactly(4), CONNECT_TIMEOUT)
        if head[1] != 0:
            reason = _SOCKS_REPLIES.get(head[1], f"code {head[1]}")
            raise ProxyError("upstream", f"SOCKS5-прокси не соединил с {host}:{port} ({reason})",
                             f"The SOCKS5 proxy could not connect to {host}:{port} ({reason})")
        kind = head[3]
        size = 4 if kind == 1 else 16 if kind == 4 else (await reader.readexactly(1))[0]
        await reader.readexactly(size + 2)
    except asyncio.IncompleteReadError:
        raise ProxyError("upstream", "SOCKS5-прокси закрыл соединение", "The SOCKS5 proxy closed the connection") from None

async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await source.read(65536)
            if not chunk:
                break
            target.write(chunk)
            await target.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            if target.can_write_eof():
                target.write_eof()
        except (OSError, RuntimeError):
            pass

async def _splice(a: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
                  b: Tuple[asyncio.StreamReader, asyncio.StreamWriter]) -> None:
    tasks = [asyncio.ensure_future(_pipe(a[0], b[1])), asyncio.ensure_future(_pipe(b[0], a[1]))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        for writer in (a[1], b[1]):
            writer.close()

_HOP_HEADERS = {b"proxy-authorization", b"proxy-connection", b"connection", b"keep-alive"}

class ProxyRelay:
    """Local no-auth HTTP proxy for the DFY browser → the user's proxy. ``last_error`` is shown in the UI."""

    def __init__(self, upstream: Upstream, *, host: str = "127.0.0.1") -> None:
        self.upstream = upstream
        self.host = host
        self.port = 0
        self.server: Optional[asyncio.base_events.Server] = None
        self.last_error: Optional[ProxyError] = None
        self.last_ok = 0.0
        self.requests = 0
        self._clients: set = set()

    async def start(self) -> int:
        if self.server is None:
            self.server = await asyncio.start_server(self._handle, self.host, 0, limit=HEAD_LIMIT)
            self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            server.close()
            for writer in list(self._clients):
                writer.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def _fail(self, writer: asyncio.StreamWriter, exc: ProxyError) -> None:
        self.last_error = exc
        body = exc.en.encode("utf-8")
        code = b"504 Gateway Timeout" if exc.code == "timeout" else b"502 Bad Gateway"
        try:
            writer.write(b"HTTP/1.1 " + code + b"\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except (OSError, RuntimeError):
            pass
        writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            try:
                head = await asyncio.wait_for(_read_head(reader), 30)
            except (ProxyError, asyncio.TimeoutError):
                writer.close()
                return
            line, _, rest = head.partition(b"\r\n")
            parts = line.split()
            if len(parts) != 3:
                writer.close()
                return
            method, target = parts[0].upper(), parts[1].decode("latin-1")
            self.requests += 1
            try:
                if method == b"CONNECT":
                    host, port = _split_host_port(target, 443)
                    remote = await open_tunnel(self.upstream, host, port)
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    await writer.drain()
                else:
                    remote = await self._forward(method, target, parts[2], rest)
            except ProxyError as exc:
                await self._fail(writer, exc)
                return
            self.last_error, self.last_ok = None, time.time()
            await _splice((reader, writer), remote)
        except (ConnectionError, OSError):
            writer.close()
        finally:
            self._clients.discard(writer)

    async def _forward(self, method: bytes, target: str, version: bytes, rest: bytes):
        import urllib.parse
        parts = urllib.parse.urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise ProxyError("upstream", "Неподдерживаемый запрос к прокси", "Unsupported proxy request")
        headers = [h for h in rest.split(b"\r\n") if h and h.split(b":", 1)[0].strip().lower() not in _HOP_HEADERS]
        headers.append(b"Connection: close")
        if self.upstream.scheme == "socks5":
            remote = await open_tunnel(self.upstream, parts.hostname, parts.port or 80)
            path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            first = method + b" " + path.encode("latin-1", "replace") + b" " + version
        else:
            remote = await _open_upstream(self.upstream)
            if self.upstream.auth:
                headers.append(b"Proxy-Authorization: " + _basic(self.upstream).encode("ascii"))
            first = method + b" " + target.encode("latin-1", "replace") + b" " + version
        remote[1].write(first + b"\r\n" + b"\r\n".join(headers) + b"\r\n\r\n")
        await remote[1].drain()
        return remote

def _split_host_port(value: str, default: int) -> Tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        host, _, tail = value[1:].partition("]")
        port = tail.lstrip(":")
    else:
        host, _, port = value.rpartition(":") if value.count(":") == 1 else (value, "", "")
    try:
        number = int(port) if port else default
    except ValueError:
        number = default
    if not host or not 0 < number < 65536:
        raise ProxyError("upstream", "Некорректный адрес назначения", "Invalid destination")
    return host, number

async def check(upstream: Upstream, *, timeout: float = 15.0, services: Tuple[str, ...] = IP_SERVICES) -> Dict[str, Any]:
    """The external IP seen through the proxy: ``{ok, ip, ms, service}`` or ``{ok: False, code, error, error_en}``."""
    import httpx
    relay = ProxyRelay(upstream)
    port = await relay.start()
    started = time.monotonic()
    last: Any = None
    try:
        async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}", trust_env=False, timeout=timeout,
                                     follow_redirects=True) as client:
            for url in services:
                try:
                    response = await client.get(url, headers={"User-Agent": "MemuAI-DFY-proxy-check"})
                except httpx.HTTPError as exc:
                    last = relay.last_error or exc
                    if isinstance(last, ProxyError) and last.code in ("auth", "refused", "dns"):
                        break
                    continue
                if response.status_code >= 400:
                    last = relay.last_error or ProxyError("upstream", f"HTTP {response.status_code}", f"HTTP {response.status_code}")
                    continue
                ip = _ip_from(response.text)
                if ip:
                    return {"ok": True, "ip": ip, "ms": int((time.monotonic() - started) * 1000),
                            "service": url.split("/")[2], "scheme": upstream.scheme}
    finally:
        await relay.close()
    if isinstance(last, ProxyError):
        return {"ok": False, "code": last.code, "error": last.ru, "error_en": last.en}
    return {"ok": False, "code": "upstream", "error": "Через прокси не удалось узнать внешний IP",
            "error_en": "Could not get the external IP through the proxy"}

def _ip_from(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("{"):
        try:
            raw = str(json.loads(raw).get("ip") or "")
        except (ValueError, AttributeError):
            return ""
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return ""

def relay_main() -> None:  # pragma: no cover - exercised on remote machines
    """``python -m app.dfy_proxy relay``: one JSON line with the upstream on stdin → ``{"port": N}`` on stdout."""
    import sys
    config = json.loads(sys.stdin.readline() or "{}")
    upstream = Upstream(str(config.get("scheme") or "http"), str(config.get("host") or ""), int(config.get("port") or 0),
                        str(config.get("username") or ""), str(config.get("password") or ""))

    async def serve() -> None:
        relay = ProxyRelay(upstream)
        port = await relay.start()
        print(json.dumps({"port": port}), flush=True)
        await asyncio.get_running_loop().run_in_executor(None, sys.stdin.read)
        await relay.close()
    asyncio.run(serve())

if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    if _sys.argv[1:2] == ["relay"]:
        relay_main()
