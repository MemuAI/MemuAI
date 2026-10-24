"""Runtime plumbing that keeps the MemuAI Telegram bot alive (Round 21).

* ``BotInstanceLock`` — one poller per bot token on this computer. Two
  processes calling ``getUpdates`` with the same token make Telegram answer
  409 Conflict to one of them on every poll (for example the full MemuAI
  Proxy and the standalone «MemuAI Proxy» edition, which keep separate data
  folders and therefore separate single-instance locks). The lock is keyed by
  the token fingerprint, so two *different* bots can still run side by side.
  Windows: a named mutex in the session namespace (released by the OS when
  the process dies, even after a crash). Elsewhere: ``flock`` on a file in
  the temp directory (also released by the OS).
* ``run_io`` — blocking file work of the bot runs on its own small thread
  pool instead of asyncio's shared default executor: a Proxy that keeps the
  default pool busy (local models, uploads, chat files) can no longer starve
  the bot, and every call can be given a deadline.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import hashlib
import logging
import os
import sys
import tempfile
from typing import Any, Callable, Optional

log = logging.getLogger("memuai.telegram")

_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_CONTROL_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _pool(lane: str = "io") -> concurrent.futures.ThreadPoolExecutor:
    """``io`` — handlers (chat histories, sessions, speech files);
    ``control`` — the polling loop itself (offset, status, preferences).

    Round 24: the two lanes are separate so a burst of handler file work
    (eight workers busy saving megabyte chats) can never delay the next
    ``getUpdates`` — that delay was one of the reasons buttons sometimes
    reacted only after many seconds.
    """
    global _POOL, _CONTROL_POOL
    if lane == "control":
        if _CONTROL_POOL is None:
            _CONTROL_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="memuai-telegram-ctl")
        return _CONTROL_POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="memuai-telegram-io")
    return _POOL


async def run_io(function: Callable[..., Any], *args: Any, timeout: Optional[float] = None,
                 lane: str = "io", **kwargs: Any) -> Any:
    """Run ``function`` on the bot's thread pool; ``timeout`` raises TimeoutError.

    A timed-out call keeps running in its thread (Python cannot kill it), but
    the bot stops waiting for it and stays responsive.
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_pool(lane), functools.partial(function, *args, **kwargs))
    if timeout is None:
        return await future
    return await asyncio.wait_for(future, timeout)


class BotInstanceLock:
    """Non-blocking, process-wide lock for one bot token (see module docs)."""

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = str(fingerprint or "none")
        self._handle: Any = None
        self._file: Any = None

    @property
    def held(self) -> bool:
        return self._handle is not None or self._file is not None

    def _name(self) -> str:
        digest = hashlib.sha256(self.fingerprint.encode("utf-8")).hexdigest()[:24]
        return f"MemuAI.TelegramBot.{digest}"

    def acquire(self) -> bool:
        if self.held:
            return True
        if sys.platform == "win32":
            try:
                return self._acquire_windows()
            except Exception as exc:  # never let the lock itself stop the bot
                log.info("telegram instance lock unavailable (%s); continuing without it", type(exc).__name__)
                return True
        try:
            return self._acquire_posix()
        except Exception as exc:
            log.info("telegram instance lock unavailable (%s); continuing without it", type(exc).__name__)
            return True

    def _acquire_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = create(None, False, "Local\\" + self._name())
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS: another process holds it
            close(handle)
            return False
        self._handle = handle
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        path = os.path.join(tempfile.gettempdir(), self._name() + ".lock")
        handle = open(path, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            try:
                import ctypes
                from ctypes import wintypes
                close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
                close.argtypes = (wintypes.HANDLE,)
                close.restype = wintypes.BOOL
                close(self._handle)
            except Exception:
                pass
            self._handle = None
        if self._file is not None:
            try:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
