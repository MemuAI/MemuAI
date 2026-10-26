"""When does a rate limit end? — read from a provider's answer.

Round 33 (``/auto-continue auto``): a limited provider usually says when it will take requests again, but every one
says it differently:

* headers — ``Retry-After`` (seconds or an HTTP date), ``retry-after-ms``, OpenAI's ``x-ratelimit-reset-requests`` /
  ``x-ratelimit-reset-tokens`` (``"6m0s"``, ``"1.5s"``, ``"20ms"``), ``x-ratelimit-reset`` (epoch or seconds),
  Anthropic's ``anthropic-ratelimit-*-reset`` (RFC 3339);
* the error text — ``"Please try again in 1h2m3.5s"``, ``"retry after 120 seconds"``, ``"resets in 5 hours"``,
  ``"reset at 2026-10-11T18:00:00Z"``, Gemini's ``"retryDelay": "32s"``, ``"reset_at": 1760200000``,
  ``"через 5 минут"``.

Everything here returns an absolute time (``time.time()`` seconds) or ``0.0`` for "nobody said". A plain clock time
without a zone in an upstream text is ignored on purpose: the provider's clock is not the user's.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping, Optional

MAX_AHEAD = 8 * 86400

_UNIT_SECONDS = {
    "ms": 0.001, "msec": 0.001, "millisecond": 0.001, "milliseconds": 0.001,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "с": 1, "сек": 1, "секунд": 1, "секунду": 1, "секунды": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "м": 60, "мин": 60, "минут": 60, "минуту": 60,
    "минуты": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "ч": 3600, "час": 3600, "часа": 3600,
    "часов": 3600,
    "d": 86400, "day": 86400, "days": 86400, "д": 86400, "дн": 86400, "день": 86400, "дня": 86400, "дней": 86400,
}
_UNIT = "|".join(sorted((re.escape(unit) for unit in _UNIT_SECONDS), key=len, reverse=True))

_DURATION = re.compile(rf"((?:\d+(?:[.,]\d+)?\s*(?:{_UNIT})(?![a-zа-я])\s*(?:and\s+|и\s+|,\s*)?)+)", re.I)
_PIECE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNIT})(?![a-zа-я])", re.I)
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2}))", re.I)
_CUES = (r"(?:try(?:ing)? again|retry|retries|available(?: again)?|resets?|reset_at|resets_at|reset_time|"
         r"wait|cool ?down|unlock\w*|lifted|повтор\w*|попробуйте|сбросит\w*|сброс\w*|обнов\w*|станет доступ\w*|"
         r"через)")
_IN_TEXT = re.compile(_CUES + r"[^.\n]{0,24}?\b(?:in|after|within|for|через)?\s*[:=]?\s*\"?" + r"(?=\d)", re.I)
_AT_TEXT = re.compile(_CUES + r"[^\n]{0,24}?(?:at|on|в|:|=)\s*\"?", re.I)
_FIELD_EPOCH = re.compile(r"\"?(?:reset_at|resets_at|reset_time|resetAt|resetsAt|x-ratelimit-reset)\"?\s*[:=]\s*\"?"
                          r"(\d{10}(?:\.\d+)?|\d{13})\b", re.I)
_FIELD_SECONDS = re.compile(r"\"?(?:retry_after|retryAfter|retry_after_seconds|retryDelay|retry_delay)\"?\s*[:=]\s*"
                            r"\"?(\d+(?:\.\d+)?)\s*(ms|s)?\b", re.I)

def _sane(at: float, now: float) -> float:
    return at if now < at <= now + MAX_AHEAD else 0.0

def duration_seconds(text: str) -> float:
    """``"1h2m3.5s"`` / ``"6m0s"`` / ``"20ms"`` / ``"2 hours 5 minutes"`` → seconds (0 when it is not a duration)."""
    total = 0.0
    found = False
    for number, unit in _PIECE.findall(str(text or "")):
        factor = _UNIT_SECONDS.get(unit.lower())
        if factor is None:
            continue
        total += float(number.replace(",", ".")) * factor
        found = True
    return total if found else 0.0

def _date(text: str) -> float:
    text = text.strip().replace("UTC", "+00:00").replace("GMT", "+00:00").replace("utc", "+00:00")
    text = re.sub(r"\s+(?=[+-]\d{2})", "", text).replace(" ", "T", 1) if re.match(r"\d{4}-", text) else text
    text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        try:
            value = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()

def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name)
        if value is None and isinstance(headers, Mapping):
            value = next((v for k, v in headers.items() if str(k).lower() == name), None)
    except Exception:
        return ""
    return str(value or "").strip()

def from_headers(headers: Any, now: Optional[float] = None) -> float:
    """The end of the limit named by HTTP headers, or 0.0."""
    now = time.time() if now is None else now
    if headers is None:
        return 0.0
    value = _header(headers, "retry-after-ms")
    if value:
        try:
            return _sane(now + float(value) / 1000.0, now)
        except ValueError:
            pass
    value = _header(headers, "retry-after")
    if value:
        try:
            return _sane(now + max(1.0, float(value)), now)
        except ValueError:
            at = _date(value)
            if at:
                return _sane(at, now)
    candidates = []
    for name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests-day"):
        value = _header(headers, name)
        seconds = duration_seconds(value) if value and not value.replace(".", "", 1).isdigit() else (
            float(value) if value else 0.0)
        if seconds > 0:
            candidates.append(now + seconds)
    for name in ("anthropic-ratelimit-requests-reset", "anthropic-ratelimit-tokens-reset",
                 "anthropic-ratelimit-input-tokens-reset", "anthropic-ratelimit-output-tokens-reset"):
        value = _header(headers, name)
        at = _date(value) if value else 0.0
        if at:
            candidates.append(at)
    value = _header(headers, "x-ratelimit-reset")
    if value:
        try:
            number = float(value)

            at = number / 1000.0 if number > 1e12 else (number if number > 1e9 else now + number)
            candidates.append(at)
        except ValueError:
            at = _date(value)
            if at:
                candidates.append(at)
    candidates = [at for at in candidates if _sane(at, now)]

    return min(candidates) if candidates else 0.0

def from_text(text: str, now: Optional[float] = None) -> float:
    """The end of the limit named in an error message (or a JSON error body), or 0.0."""
    now = time.time() if now is None else now
    text = str(text or "")
    if not text:
        return 0.0
    match = _FIELD_EPOCH.search(text)
    if match:
        number = float(match.group(1))
        at = _sane(number / 1000.0 if number > 1e12 else number, now)
        if at:
            return at
    match = _FIELD_SECONDS.search(text)
    if match:
        seconds = float(match.group(1)) * (0.001 if (match.group(2) or "").lower() == "ms" else 1.0)
        if seconds > 0:
            return _sane(now + max(1.0, seconds), now)
    for cue in _AT_TEXT.finditer(text):
        iso = _ISO.match(text, cue.end())
        if iso:
            at = _date(iso.group(1))
            if at:
                return _sane(at, now)
    for cue in _IN_TEXT.finditer(text):
        span = _DURATION.match(text, cue.end())
        if span:
            seconds = duration_seconds(span.group(1))
            if seconds > 0:
                return _sane(now + max(1.0, seconds), now)
    iso = _ISO.search(text)
    if iso and re.search(r"(?i)reset|retry|again|available|сброс|повтор", text):
        at = _date(iso.group(1))
        if at:
            return _sane(at, now)
    return 0.0

def reset_at(*sources: Any, headers: Any = None, now: Optional[float] = None) -> float:
    """The first known reset among headers and texts (an exception counts by its ``retry_at`` and its message)."""
    now = time.time() if now is None else now
    at = from_headers(headers, now) if headers is not None else 0.0
    if at:
        return at
    for source in sources:
        if source is None:
            continue
        if isinstance(source, BaseException):
            known = float(getattr(source, "retry_at", 0.0) or 0.0)
            if _sane(known, now):
                return known
            texts: Iterable[str] = (str(getattr(source, "raw_message", "") or ""), str(source))
        else:
            texts = (str(source),)
        for text in texts:
            at = from_text(text, now)
            if at:
                return at
    return 0.0
