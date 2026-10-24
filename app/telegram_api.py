"""Small, careful Telegram Bot API client used by the MemuAI bot.

* one shared ``httpx.AsyncClient`` (connection reuse, no per-call clients);
* typed errors: ``TelegramError`` carries ``error_code``/``retry_after`` so the
  caller can tell a flood wait (429) from a bad request or a revoked token;
* essential calls (sending a message) wait out short flood waits and retry,
  cosmetic ones (draft/edit frames, typing) give up immediately;
* the bot token never appears in exceptions or logs (``redact``), including
  the request lines httpx itself logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("memuai.telegram")
API_ROOT = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{20,}$")
_TOKEN_IN_TEXT = re.compile(r"(bot)?\d{5,16}:[A-Za-z0-9_-]{20,}")
_LEGACY_TOKEN = re.compile(r"bot\d+:[\w-]+")


def redact(text: Any) -> str:
    """Remove bot tokens from any text that may reach a log or the UI."""
    value = _TOKEN_IN_TEXT.sub(lambda m: ("bot" if m.group(1) else "") + "<token>", str(text))
    return _LEGACY_TOKEN.sub("bot<token>", value)


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.fullmatch(str(token or "").strip()))


class _RedactFilter(logging.Filter):
    """httpx logs "HTTP Request: POST https://api.telegram.org/bot<TOKEN>/…"."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        try:
            message = record.getMessage()
        except Exception:
            return True
        if "api.telegram.org" in message or _TOKEN_IN_TEXT.search(message):
            record.msg, record.args = redact(message), ()
        return True


_FILTER_INSTALLED = False


def install_log_redaction() -> None:
    global _FILTER_INSTALLED
    if _FILTER_INSTALLED:
        return
    _FILTER_INSTALLED = True
    for name in ("httpx", "httpcore", "memuai.telegram"):
        logging.getLogger(name).addFilter(_RedactFilter())


class TelegramError(RuntimeError):
    def __init__(self, description: str, *, error_code: int = 0, retry_after: float = 0.0,
                 method: str = "", transport: str = "") -> None:
        super().__init__(redact(description))
        self.description = redact(description)
        self.error_code = int(error_code or 0)
        self.retry_after = float(retry_after or 0)
        self.method = method
        # Name of the httpx exception when the request never got an answer
        # (ConnectError, ReadTimeout, RemoteProtocolError, PoolTimeout, SSL …).
        self.transport = str(transport or "")

    @property
    def network(self) -> bool:
        """No usable answer from Telegram: the connection (not the request) failed."""
        return bool(self.transport)

    @property
    def server_error(self) -> bool:
        return 500 <= self.error_code <= 599

    @property
    def flood(self) -> bool:
        return self.error_code == 429

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description.lower()

    @property
    def parse_error(self) -> bool:
        text = self.description.lower()
        return self.error_code == 400 and ("can't parse" in text or "entities" in text or
                                           "unsupported start tag" in text or "can't find end" in text)

    @property
    def unauthorized(self) -> bool:
        return self.error_code == 401

    @property
    def conflict(self) -> bool:
        return self.error_code == 409

    @property
    def unknown_method(self) -> bool:
        text = self.description.lower()
        return self.error_code == 404 or "method not found" in text or "not supported" in text \
            or "unknown method" in text


class BotApi:
    """Thin async wrapper around ``https://api.telegram.org/bot<token>/<method>``."""

    def __init__(self, client: httpx.AsyncClient, token: str, *, root: str = API_ROOT) -> None:
        self.client = client
        self.token = str(token or "").strip()
        self.root = root.rstrip("/")

    def _url(self, method: str) -> str:
        return f"{self.root}/bot{self.token}/{method}"

    async def call(self, method: str, *, essential: bool = False, http_timeout: float = 35,
                   files: Optional[Dict[str, Any]] = None, **payload: Any) -> Any:
        """Call a method and return ``result``.

        ``essential`` calls wait out a flood limit up to 3 times (each wait is
        capped at 60 s); otherwise a 429 is raised straight away so a stream of
        cosmetic edits can simply skip frames.
        """
        attempts = 4 if essential else 1
        last: Optional[TelegramError] = None
        for attempt in range(attempts):
            try:
                if files:
                    form = {key: (json.dumps(value, ensure_ascii=False)
                                  if isinstance(value, (dict, list)) else str(value))
                            for key, value in payload.items() if value is not None}
                    response = await self.client.post(self._url(method), data=form, files=files,
                                                      timeout=max(http_timeout, 120))
                else:
                    response = await self.client.post(self._url(method), json=payload, timeout=http_timeout)
            except (httpx.HTTPError, OSError) as exc:
                # The exception text of httpx can contain the URL (with token).
                # OSError covers ssl.SSLError and sockets torn down by sleep/wake
                # that some transports surface unwrapped.
                raise TelegramError(f"{type(exc).__name__}: {redact(exc)}", method=method,
                                    transport=type(exc).__name__) from None
            try:
                data = response.json()
            except Exception:
                raise TelegramError(f"Telegram HTTP {response.status_code}",
                                    error_code=response.status_code, method=method) from None
            if isinstance(data, dict) and data.get("ok"):
                return data.get("result")
            data = data if isinstance(data, dict) else {}
            parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
            error = TelegramError(str(data.get("description") or f"Telegram HTTP {response.status_code}"),
                                  error_code=int(data.get("error_code") or response.status_code or 0),
                                  retry_after=float(parameters.get("retry_after") or 0), method=method)
            if error.error_code == 429 and error.retry_after <= 0:
                match = re.search(r"retry after (\d+)", error.description, re.I)
                error.retry_after = float(match.group(1)) if match else 3.0
            last = error
            if error.error_code == 429 and attempt + 1 < attempts and error.retry_after <= 60:
                await asyncio.sleep(error.retry_after + 0.2 * attempt)
                continue
            raise error
        raise last or TelegramError("Telegram request failed", method=method)

    async def get_me(self) -> Dict[str, Any]:
        result = await self.call("getMe", http_timeout=15)
        return result if isinstance(result, dict) else {}

    async def download(self, file_path: str, *, max_bytes: int) -> bytes:
        url = f"{self.root}/file/bot{self.token}/{file_path.lstrip('/')}"
        try:
            async with self.client.stream("GET", url, timeout=120) as response:
                if response.status_code >= 400:
                    # Never raise_for_status(): its message contains the URL with the token.
                    raise TelegramError(f"Telegram file download failed: HTTP {response.status_code}",
                                        error_code=response.status_code, method="file")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise OverflowError("attachment too large")
                return bytes(data)
        except (httpx.HTTPError, OSError) as exc:
            raise TelegramError(f"{type(exc).__name__}: {redact(exc)}", method="file",
                                transport=type(exc).__name__) from None
