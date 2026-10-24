"""Общий пул curl_cffi-сессий для провайдеров (TLS-имитация Chrome).

Сессия curl_cffi привязана к циклу событий, на котором её первый раз использовали: libcurl вызывает его таймеры и
сокеты через колбэки cffi. Поэтому пул ведётся **по циклам**: у каждого цикла свои сессии, а сессия закрытого цикла
не используется и не закрывается чужим. Раньше сессия, созданная в одном цикле (``asyncio.run`` потока, теста),
попадала в другой; её колбэк ``timer_function`` брался за уже закрытый цикл и падал с «Event loop is closed».
Ошибка внутри колбэка cffi на Windows показывается окном «Python-CFFI error» поверх рабочего стола, пока жив
процесс — поэтому сами колбэки тоже защищены (``_harden_curl_callbacks``).
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any, Dict, Optional

from curl_cffi.requests import AsyncSession

_sessions: Dict[str, AsyncSession] = {}
_last_used: Dict[str, float] = {}
# Выведенные из пула сессии закрываются не сразу: ими могут ещё читать
# чужие потоки (сессия общая для всех аккаунтов без своего прокси).
_retired: list = []
RETIRE_GRACE = 3600.0
MAX_RETIRED = 16
# Секции под замком короткие и без await, поэтому замок обычный, поточный: asyncio.Lock привязан к одному циклу,
# а пулом пользуются несколько (поток Telegram, планировщик, окно).
_lock = threading.Lock()
_cfg = {"impersonate": "chrome124", "verify": True}


def _harden_curl_callbacks() -> None:
    """Колбэки libcurl не должны падать на закрытом цикле.

    ``timer_function`` и ``socket_function`` из ``curl_cffi.aio`` берут цикл сессии и ставят на него таймер или
    сокет. Если цикл уже закрыт, им нечего планировать; исключение же внутри колбэка cffi нельзя поймать снаружи, а
    на Windows оно превращается в окно «Python-CFFI error». Здесь колбэки заменяются теми же, но с проверкой цикла."""
    try:
        from curl_cffi import aio
    except Exception:
        return
    if getattr(aio, "_memuai_safe_callbacks", False):
        return
    try:
        ffi = aio.ffi
        timer, sock = aio.timer_function, aio.socket_function

        def alive(clientp: Any) -> bool:
            try:
                return not ffi.from_handle(clientp).loop.is_closed()
            except Exception:
                return False

        def timer_function(curlm, timeout_ms, clientp):  # noqa: ANN001
            if not alive(clientp):
                return 0
            try:
                return timer(curlm, timeout_ms, clientp)
            except RuntimeError:                      # the loop closed between the check and the call
                return 0

        def socket_function(curl, sockfd, what, clientp, data):  # noqa: ANN001
            if not alive(clientp):
                return 0
            try:
                return sock(curl, sockfd, what, clientp, data)
            except RuntimeError:
                return 0

        ffi.def_extern(name="timer_function")(timer_function)
        ffi.def_extern(name="socket_function")(socket_function)
        aio._memuai_safe_callbacks = True
    except Exception:
        return


_harden_curl_callbacks()


def configure(impersonate: Optional[str] = None, verify: Optional[bool] = None) -> None:
    if impersonate:
        _cfg["impersonate"] = impersonate
    if verify is not None:
        _cfg["verify"] = verify


def _loop_of(session: Any) -> Optional[asyncio.AbstractEventLoop]:
    """The loop a session works on (None until its first use)."""
    return getattr(session, "_loop", None)


async def _close(session: Any) -> None:
    """Closes a session on the loop it belongs to. A session whose loop is gone cannot be closed by anyone: it is
    dropped as it is (its callbacks are harmless, see ``_harden_curl_callbacks``)."""
    try:
        loop = _loop_of(session)
        running = asyncio.get_running_loop()
        if loop is None or loop is running:
            await session.close()
        elif not loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(session.close(), loop)
            await asyncio.wait_for(asyncio.wrap_future(future), 5)
    except Exception:
        pass


def _forget_closed_loops() -> None:
    """Under ``_lock``: sessions of loops that no longer exist leave the pool."""
    for key in [k for k, s in _sessions.items() if (_loop_of(s) is not None and _loop_of(s).is_closed())]:
        _sessions.pop(key, None)
        _last_used.pop(key, None)
    _retired[:] = [item for item in _retired if not (_loop_of(item[1]) is not None and _loop_of(item[1]).is_closed())]


def _suffix() -> str:
    return f"|{id(asyncio.get_running_loop())}"


def is_loopback(url: str) -> bool:
    """A server on this computer (llama.cpp, LM Studio, Ollama, a local gateway)."""
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    import ipaddress
    try:
        # A literal address only: "127.0.0.1.nip.io" or "127.example.com" is a public name and must keep
        # going through the user's proxy.
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def get_session(proxy: str = "", *, impersonate: bool = True, url: str = "") -> AsyncSession:
    # Browser impersonation adds an h2c Upgrade to plain HTTP requests with
    # recent curl_cffi releases. ASGI servers can then see an empty POST body.
    profile = _cfg["impersonate"] if impersonate else ""
    # Round 27: libcurl takes http(s)_proxy from the environment (on Windows HTTP_PROXY too — env names are
    # case-insensitive there), so a Custom connection to http://127.0.0.1 went to a VPN/corporate proxy and got
    # its 404/502. Without an explicit proxy a loopback address bypasses every proxy.
    direct = not proxy and is_loopback(url)
    key = f"{proxy}|{profile}|{_cfg['verify']}{'|direct' if direct else ''}{_suffix()}"   # one pool per event loop
    with _lock:
        _forget_closed_loops()
        s = _sessions.get(key)
        if s is None:
            kwargs: Dict[str, Any] = {"verify": _cfg["verify"]}
            if profile:
                kwargs["impersonate"] = profile
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            if direct:
                from curl_cffi import CurlOpt
                kwargs["curl_options"] = {CurlOpt.NOPROXY: "*"}
            s = AsyncSession(**kwargs)
            _sessions[key] = s
        _last_used[key] = time.time()
        return s


async def close_all() -> None:
    with _lock:
        old = list(_sessions.values()) + [session for _ts, session in _retired]
        _sessions.clear()
        _last_used.clear()
        _retired.clear()
    for s in old:
        await _close(s)


async def drop_session(proxy: str = "") -> int:
    """Close the cached session for `proxy` so the next call reconnects.

    A keep-alive socket can die silently (laptop sleep, VPN switch, provider
    closing the connection). Reusing it makes every retry fail with the same
    connection error, so a network failure must throw the session away before
    the request is retried.
    """
    prefix, suffix = f"{proxy}|", _suffix()
    with _lock:
        keys = [k for k in _sessions if k.startswith(prefix) and k.endswith(suffix)]
        victims = []
        for key in keys:
            session = _sessions.pop(key, None)
            _last_used.pop(key, None)
            if session is not None:
                victims.append(session)
        now = time.time()
        _retired.extend((now, session) for session in victims)
        overflow = _retired[:-MAX_RETIRED] if len(_retired) > MAX_RETIRED else []
        del _retired[:len(overflow)]
    # Долгий сбой с частыми повторами не должен копить открытые сессии.
    for _ts, session in overflow:
        await _close(session)
    return len(victims)


async def _close_retired(now: float) -> None:
    with _lock:
        due = [item for item in _retired if now - item[0] > RETIRE_GRACE]
        for item in due:
            _retired.remove(item)
    for _ts, session in due:
        await _close(session)


async def recycle_idle(max_idle: float = 900.0) -> int:
    """Close sessions unused for `max_idle` seconds.

    curl_cffi keeps keep-alive sockets open; after a laptop sleeps or the
    network changes those sockets can be silently dead and every later request
    hangs until its timeout. Dropping idle sessions makes the next request
    create a fresh connection instead.
    """
    now = time.time()
    with _lock:
        stale = [k for k, ts in _last_used.items() if now - ts > max_idle]
        victims = []
        for key in stale:
            session = _sessions.pop(key, None)
            _last_used.pop(key, None)
            if session is not None:
                victims.append(session)
        # Поток длиннее max_idle ещё читает эту сессию: закрываем позже.
        _retired.extend((now, session) for session in victims)
    await _close_retired(now)
    return len(victims)


_CURL_TAIL = re.compile(r"\s*See https?://curl\.se/\S*.*$", re.S)


def network_error_text(exc: BaseException) -> str:
    """A short, readable reason for a failed connection, for account cards and checks.

    curl_cffi errors read «Failed to perform, curl: (7) Failed to connect to … See
    https://curl.se/libcurl/c/libcurl-errors.html first for more details.» — the
    wrapper prefix and the link are noise in the UI; the curl sentence stays.
    """
    text = " ".join(str(exc or "").split())
    text = _CURL_TAIL.sub("", text)
    text = re.sub(r"^Failed to perform,\s*", "", text)
    text = re.sub(r"^curl: \(\d+\)\s*", "", text).rstrip(" .")
    return (text or type(exc).__name__)[:200]


async def read_error_text(resp, limit: int = 600) -> str:
    """The head of a failed response, also for ``stream=True`` ones.

    A streamed curl_cffi response has no body until it is read, so ``resp.text``
    is empty and the provider's own explanation («Verify your account», «model
    not found» …) never reached the user. Reading is capped and never raises.
    """
    async def collect() -> bytes:
        # curl_cffi reads a streamed body with ``acontent``; httpx calls it ``aread``.
        reader = getattr(resp, "acontent", None) or getattr(resp, "aread", None)
        if reader is not None:
            data = await reader()
            return bytes(data) if isinstance(data, (bytes, bytearray)) else b""
        chunks = bytearray()
        async for chunk in resp.aiter_content():
            chunks += chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8")
            if len(chunks) > limit * 4:
                break
        return bytes(chunks)

    try:
        data = await asyncio.wait_for(collect(), timeout=10)
        if data:
            return data[:limit * 4].decode("utf-8", errors="replace")[:limit]
    except Exception:
        pass
    try:
        return str(resp.text or "")[:limit]
    except Exception:
        return ""


async def close_quietly(closer, timeout: float = 5.0) -> None:
    """Awaits ``closer()`` to completion even while the surrounding task is being cancelled.

    A client that presses Stop cancels the request task, and under anyio's cancel scopes every
    ``await`` in a ``finally`` block is cancelled again at once. The upstream connection was then
    never closed: the provider kept generating (and counting against the quota) for a request
    nobody was waiting for. The close runs inside a shielded scope with a short time limit.
    """
    try:
        import anyio
        with anyio.CancelScope(shield=True):
            await asyncio.wait_for(closer(), timeout=timeout)
    except BaseException as exc:  # noqa: BLE001 — closing must never raise out of cleanup
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise


async def close_response(resp, timeout: float = 5.0) -> None:
    """Aborts a streamed curl_cffi response instead of draining it.

    ``AsyncResponse.aclose()`` in curl_cffi only awaits the background transfer
    task: it never sets ``quit_now`` (only the sync ``close()`` does). Closing a
    stream the client abandoned (Stop, a ``result`` event before the end, an
    idle timeout) therefore waited until the provider finished generating the
    whole answer — the upstream kept working and the cleanup hung. Here the
    write callback is told to fail and the transfer task is cancelled, which
    removes the handle from curl_multi at once. Anything that is not a curl_cffi
    stream (httpx responses, test doubles) is simply closed with ``aclose``.
    Never raises.
    """
    if resp is None:
        return
    quit_now = getattr(resp, "quit_now", None)
    if isinstance(quit_now, asyncio.Event):
        quit_now.set()
    task = getattr(resp, "astream_task", None)
    if isinstance(task, asyncio.Future) and not task.done():
        task.cancel()
    closer = getattr(resp, "aclose", None)
    if closer is not None:
        await close_quietly(closer, timeout)


async def aiter_lines_str(resp, *, idle_timeout: Optional[float] = None):
    """Normalize curl lines to text and optionally bound inter-line silence."""
    lines = resp.aiter_lines()
    try:
        while True:
            try:
                if idle_timeout is not None and float(idle_timeout) > 0:
                    line = await asyncio.wait_for(anext(lines), timeout=float(idle_timeout))
                else:
                    line = await anext(lines)
            except StopAsyncIteration:
                return
            if isinstance(line, (bytes, bytearray)):
                yield line.decode("utf-8", errors="replace")
            else:
                yield line
    finally:
        close = getattr(lines, "aclose", None)
        if close is not None:
            await close_quietly(close)
