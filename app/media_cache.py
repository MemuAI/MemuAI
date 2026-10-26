"""Round 33 (r33d-media): generated pictures are kept as files of the profile, not as links that expire.

DashScope / Reform / other OpenAI-compatible image models (wanx2.1-t2i-turbo, grok-imagine-image-2.0,
gpt-imagine-2.5 …) answer with a signed remote URL that stops working after ~24 h (``Expires=``). The chat used to
store that URL, so an old chat showed a broken picture. Now:

* right after a generation returns, every remote ``http(s)`` result is downloaded (SSRF-guarded: public addresses
  only, the connected peer is re-checked, at most 4 redirects; 30 MB; 45 s; the bytes must really be PNG / JPEG /
  WebP / GIF) into the same ``generated_media`` folder of the profile that generated videos use
  (``media_routing.media_folder``) and the chat keeps ``/api/pg/media/file/<sha256>.<ext>?profile_id=…``;
  when the download fails the original URL is kept (nothing is lost — the lazy migration below retries);
* ``POST /api/pg/media/cache {url}`` — the lazy migration of old chats: the window asks for every remote picture of
  a chat it opens; a working link is cached (``{"url": local}``), a dead one answers ``{"expired": true}`` and the
  window shows the «Image expired · Regenerate» placeholder instead of a broken icon;
* ``read_local`` / ``local_data_url`` — downloads, «Open», the Save dialog, export, Telegram and the Mini App read the
  local file through these helpers (the routes that used to fetch the remote URL).

Genspark result links (``genspark.ai/api/files/s/…``) need the Genspark account cookie: they are fetched through the
caller's ``genspark_fetch`` (the window process configures it with the Proxy's ``/api/admin/pg/image/file`` bridge;
the Telegram bot passes its in-process fetcher). ``data:`` results stay as they are (they never expire).
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import Request

MAX_IMAGE_BYTES = 30 * 1024 * 1024
TIMEOUT = 45.0
MAX_REDIRECTS = 4
LOCAL_RE = re.compile(r"/api/pg/media/file/([0-9a-f]{64}\.(png|jpg|webp|gif|mp4|webm|mov))(?:\?profile_id=([\w.-]+))?")
MIMES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
         "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}
EXPIRED_STATUSES = {400, 401, 403, 404, 410}

_genspark_fetch: Optional[Callable[[str], Awaitable[Tuple[int, bytes, str]]]] = None

class Expired(Exception):
    """The link answers but no longer gives the picture (signature expired, deleted, not an image)."""

class Unreachable(Exception):
    """Temporary trouble (network, 5xx, too large, blocked address): keep the link, try again later."""

def configure(*, genspark_fetch: Optional[Callable[[str], Awaitable[Tuple[int, bytes, str]]]] = None) -> None:
    global _genspark_fetch
    _genspark_fetch = genspark_fetch

def _active_profile() -> str:
    from . import media_routing
    return media_routing._active_profile()

def _folder(profile_id: Optional[str] = None) -> Path:
    from . import media_routing
    return media_routing.media_folder(profile_id)

def is_local(url: Any) -> bool:
    return bool(LOCAL_RE.fullmatch(str(url or "").split("#", 1)[0]))

def local_path(url: Any) -> Optional[Path]:
    """The file behind ``/api/pg/media/file/…`` of the ACTIVE profile (the only one the window may read)."""
    match = LOCAL_RE.fullmatch(str(url or "").split("#", 1)[0])
    if not match:
        return None
    active = _active_profile()
    if match.group(3) and match.group(3) != active:
        return None
    path = _folder(active) / match.group(1)
    if not path.is_file() or path.is_symlink():
        return None
    return path

def read_local(url: Any) -> Optional[Tuple[bytes, str]]:
    path = local_path(url)
    if path is None:
        return None
    return path.read_bytes(), MIMES.get(path.suffix.lstrip(".").lower(), "application/octet-stream")

def local_data_url(url: Any) -> Optional[str]:
    """``data:`` form of a local file — for routes that already handle data URLs (Save dialog, Open, Downloads)."""
    found = read_local(url)
    if not found:
        return None
    import base64
    raw, mime = found
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

def sniff_image(raw: bytes) -> Optional[str]:
    """The real format by magic bytes; ``None`` for anything that is not a picture (HTML error pages, XML…)."""
    head = bytes(raw[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None

def store_image(raw: bytes, profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Write a picture (validated) into ``generated_media`` atomically; same naming as generated videos."""
    mime = sniff_image(raw)
    if not mime:
        raise Expired("not an image")
    if len(raw) > MAX_IMAGE_BYTES:
        raise Unreachable("image is too large")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
    active = profile_id or _active_profile()
    folder = _folder(active)
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(raw).hexdigest() + "." + ext
    target = folder / name
    if not target.exists():
        handle, temporary = tempfile.mkstemp(prefix=".media-", dir=folder)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"url": f"/api/pg/media/file/{name}?profile_id={active}", "mime": mime, "size": len(raw)}

def _ip_is_public(address: Any) -> bool:
    ip = address if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)) \
        else ipaddress.ip_address(str(address).split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_global and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified)

def _resolve(host: str, port: int) -> List[str]:
    return [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]

def safe_url(raw: str) -> str:
    url = str(raw or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Unreachable("only public http(s) addresses")
    if parsed.username is not None or parsed.password is not None:
        raise Unreachable("credentials in the address")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = set(_resolve(parsed.hostname, port))
    except (ValueError, OSError) as exc:
        raise Unreachable("the server is not found") from exc
    if not addresses or any(not _ip_is_public(x) for x in addresses):
        raise Unreachable("local and private addresses are not allowed")
    return url

def _check_peer(response: Any) -> None:
    """Closes the DNS-rebinding gap: the address actually connected to must be public too."""
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream and hasattr(stream, "get_extra_info") else None
    address = peer[0] if isinstance(peer, (tuple, list)) and peer else peer
    try:
        ok = _ip_is_public(ipaddress.ip_address(str(address).split("%", 1)[0]))
    except ValueError:
        ok = False
    if not ok:
        raise Unreachable("the server redirected to a local address")

def is_genspark(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url))
    return parsed.scheme == "https" and parsed.hostname in ("genspark.ai", "www.genspark.ai") \
        and parsed.path.startswith("/api/files/s/")

def is_remote(url: Any) -> bool:
    return bool(re.match(r"^https?://", str(url or ""), re.I))

def _client():
    import httpx
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=10), trust_env=False, follow_redirects=False,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MemuAI"})

async def _download_public(url: str) -> bytes:
    import httpx
    current = await asyncio.to_thread(safe_url, url)
    async with _client() as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", current) as response:
                    _check_peer(response)
                    if response.status_code in (301, 302, 303, 307, 308):
                        current = await asyncio.to_thread(safe_url, urllib.parse.urljoin(
                            current, response.headers.get("location", "")))
                        continue
                    if response.status_code in EXPIRED_STATUSES:
                        raise Expired(f"HTTP {response.status_code}")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise Unreachable(f"HTTP {response.status_code}")
                    try:
                        declared = int(response.headers.get("content-length", "") or 0)
                    except ValueError:
                        declared = 0
                    if declared > MAX_IMAGE_BYTES:
                        raise Unreachable("image is too large")
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > MAX_IMAGE_BYTES:
                            raise Unreachable("image is too large")
                    return bytes(output)
            except httpx.HTTPError as exc:
                raise Unreachable(type(exc).__name__) from exc
    raise Unreachable("too many redirects")

async def download_image(url: str, *, genspark_fetch=None) -> bytes:
    """Bytes of a picture, or ``Expired`` / ``Unreachable``. Whole operation limited to ``TIMEOUT`` seconds."""
    fetch = genspark_fetch or _genspark_fetch
    from .providers import geminiweb_media
    if geminiweb_media.MEDIA_URL_RE.search(str(url)):
        local = geminiweb_media.local_path(str(url))
        if local is None:
            raise Expired("Gemini Web media file is gone")
        return await asyncio.to_thread(local.read_bytes)
    if is_genspark(url):
        if fetch is None:
            raise Unreachable("no Genspark bridge")
        status, raw, _type = await asyncio.wait_for(fetch(url), TIMEOUT)
        if status in EXPIRED_STATUSES:
            raise Expired(f"HTTP {status}")
        if status != 200:
            raise Unreachable(f"HTTP {status}")
        return bytes(raw or b"")
    try:
        return await asyncio.wait_for(_download_public(url), TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise Unreachable("timeout") from exc

async def cache_url(url: str, profile_id: Optional[str] = None, *, genspark_fetch=None) -> Dict[str, Any]:
    raw = await download_image(url, genspark_fetch=genspark_fetch)
    if not raw:
        raise Expired("empty answer")
    return await asyncio.to_thread(store_image, raw, profile_id)

async def localize_urls(urls: List[Any], profile_id: Optional[str] = None, *, genspark_fetch=None) -> List[str]:
    """Remote results → local files; a result that could not be downloaded keeps its URL (the window retries)."""
    out: List[str] = []
    for value in urls or []:
        url = str(value)
        if is_remote(url):
            try:
                url = (await cache_url(url, profile_id, genspark_fetch=genspark_fetch))["url"]
            except Exception:
                pass
        out.append(url)
    return out

async def localize_sse(chunks: AsyncIterator[Any], profile_id: Optional[str] = None) -> AsyncIterator[bytes]:
    """Relays an image SSE stream unchanged except the ``done`` event, whose remote ``urls`` become local files."""
    buffer = b""
    async for chunk in chunks:
        buffer += chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8")
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            yield await _localize_event(event, profile_id) + b"\n\n"
    if buffer:
        yield await _localize_event(buffer, profile_id)

async def _localize_event(event: bytes, profile_id: Optional[str]) -> bytes:
    text = event.decode("utf-8", "replace").strip("\r\n")
    if not text.startswith("data: ") or '"done"' not in text:
        return event
    try:
        item = json.loads(text[6:])
    except Exception:
        return event
    if not isinstance(item, dict) or item.get("status") != "done" or not item.get("urls"):
        return event
    item["urls"] = await localize_urls(item["urls"], profile_id)
    return ("data: " + json.dumps(item, ensure_ascii=False)).encode("utf-8")

def register(app: Any) -> None:
    from fastapi.responses import JSONResponse

    @app.post("/api/pg/media/cache")
    async def media_cache_route(request: Request):
        """Lazy migration of a picture in an old chat: ``{url}`` → ``{url: local}`` / ``{expired: true}``."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        url = str((body or {}).get("url") or "").strip() if isinstance(body, dict) else ""
        if is_local(url):
            return {"url": url, "local": True, "missing": local_path(url) is None}
        if not is_remote(url) or len(url) > 8000:
            return JSONResponse({"error": "unsupported address"}, 400)
        try:
            stored = await cache_url(url)
        except Expired as exc:
            return {"expired": True, "reason": str(exc)[:120]}
        except Unreachable as exc:
            return JSONResponse({"error": str(exc)[:200], "retry": True}, 502)
        except Exception as exc:
            return JSONResponse({"error": type(exc).__name__, "retry": True}, 502)
        return {"url": stored["url"], "mime": stored["mime"], "size": stored["size"]}
