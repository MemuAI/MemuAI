"""Public endpoint: a second, deliberately small server for people outside this computer.

The regular Proxy listens on 127.0.0.1 and carries the panel and the admin
API. Opening that port to a network would expose both. The public endpoint is
a separate listener on its own address and port that serves **only** the
model API (``/v1/models``, ``/v1/chat/completions``, ``/v1/responses``,
``/v1/messages``) and a bare ``/health`` — no panel, no ``/api/admin``, no
static files.

Access is by public keys created in the panel. Every key has its own limits
(requests per minute and per day, tokens per day, a total token budget, an
expiry date, a list of models) on top of the limits of the endpoint as a
whole, and its usage is counted. The models offered outside are a chosen
subset of the catalogue; everything else answers "not found".

The listener runs inside the Proxy's own event loop, so requests go through
exactly the same pipeline (accounts, retries, logs) as local ones.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import hashlib
import ipaddress
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .state import ClosingStreamingResponse

log = logging.getLogger("memuai.public")

KEY_PREFIX = "mpk-"
MAX_KEYS = 200
MAX_BODY_BYTES = 12 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 1024 * 1024
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "host": "0.0.0.0",           # the address to listen on; 127.0.0.1 when a tunnel or reverse proxy stands in front
    "port": 8320,
    "address": "",               # what to show people: a domain or an outside IP (display only)
    "models": [],                # public ids offered outside; empty means nothing is offered
    "rpm": 120,                  # requests per minute for the endpoint as a whole (0 — no limit)
    "concurrency": 8,            # requests in flight at once (0 — no limit)
    "max_output_tokens": 0,      # cap on max_tokens of one request (0 — no cap)
    "cors": False,               # let web pages call the endpoint from a browser
    "tls_cert": "",              # paths to a certificate and its key: HTTPS straight from the Proxy
    "tls_key": "",
    "keys": [],
}
KEY_LIMITS = ("rpm", "rpd", "tpd", "budget", "tpmonth", "max_tokens", "concurrency", "cost_budget")
KEY_FIELDS = ("name", "note", "enabled", "expires", "models", "ips", *KEY_LIMITS)
LEGACY_KEY_FIELDS = ("key", "api_key", "public_key", "access_key")   # the single key of older versions


def _path() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR) / "public.json"


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(low, min(high, number))


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if number != number:      # NaN
        number = default
    return max(low, min(high, number))


def _today() -> str:
    return _dt.date.today().isoformat()


def _month() -> str:
    return _dt.date.today().isoformat()[:7]


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(30)


def hash_key(key: str) -> str:
    """Keys are 240 random bits, so a plain SHA-256 is enough: only the hash is kept on disk."""
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()


def key_hint(key: str) -> str:
    return f"{key[:8]}…{key[-4:]}" if len(key) > 14 else "…"


def normalize_ips(raw: Any) -> List[str]:
    """Allowed client addresses as CIDR strings; bad entries are dropped."""
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            continue
        if str(network) not in out:
            out.append(str(network))
    return out[:100]


def invalid_ips(raw: Any) -> List[str]:
    """Entries of an allow-list that are not an address or a subnet (shown back to the user)."""
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
    bad: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            ipaddress.ip_network(text, strict=False)
        except ValueError:
            bad.append(text[:64])
    return bad


def _check_key_fields(fields: Dict[str, Any]) -> None:
    """A typo in the IP list must not be dropped silently: with every entry dropped the key would work from anywhere."""
    bad = invalid_ips(fields.get("ips")) if "ips" in fields else []
    if bad:
        raise ValueError("Неверные IP-адреса или подсети: " + ", ".join(bad[:5]))


def ip_allowed(ips: List[str], client: str) -> bool:
    if not ips:
        return True
    try:
        address = ipaddress.ip_address(str(client or "").strip().strip("[]"))
    except ValueError:
        return False
    if getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    for item in ips:
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except (ValueError, TypeError):
            continue
    return False


def normalize_key(raw: Any) -> Optional[Dict[str, Any]]:
    """One stored key. A plaintext ``key`` (older files) is turned into its hash here: that is the migration."""
    if not isinstance(raw, dict):
        return None
    plain = str(raw.get("key") or "").strip()
    digest = str(raw.get("key_hash") or "").strip().lower()
    hint = str(raw.get("key_hint") or "").strip()[:40]
    if plain:
        if len(plain) < 16 or len(plain) > 256:
            return None
        digest = hash_key(plain)
        hint = key_hint(plain)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    expires = str(raw.get("expires") or "").strip()[:10]
    if expires and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
        expires = ""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return {
        "id": str(raw.get("id") or "").strip()[:24] or secrets.token_hex(6),
        "key_hash": digest,
        "key_hint": hint or "mpk-…",
        "name": str(raw.get("name") or "").strip()[:80],
        "note": str(raw.get("note") or "").strip()[:300],
        "enabled": raw.get("enabled") is not False,
        "created": _int(raw.get("created"), int(time.time()), 0, 2**40),
        "rotated": _int(raw.get("rotated"), 0, 0, 2**40),
        "expires": expires,
        "rpm": _int(raw.get("rpm"), 20, 0, 100_000),
        "rpd": _int(raw.get("rpd"), 0, 0, 10_000_000),
        "tpd": _int(raw.get("tpd"), 0, 0, 2_000_000_000),
        "tpmonth": _int(raw.get("tpmonth"), 0, 0, 2_000_000_000_000),
        "budget": _int(raw.get("budget"), 0, 0, 2_000_000_000_000),
        "max_tokens": _int(raw.get("max_tokens"), 0, 0, 10_000_000),
        "concurrency": _int(raw.get("concurrency"), 0, 0, 1000),
        "cost_budget": round(_float(raw.get("cost_budget"), 0.0, 0.0, 1_000_000.0), 4),
        "ips": normalize_ips(raw.get("ips")),
        "models": [str(item)[:200] for item in raw.get("models") or [] if str(item).strip()][:500]
        if isinstance(raw.get("models"), list) else [],
        "usage": {
            "requests": _int(usage.get("requests"), 0, 0, 2**53),
            "tokens": _int(usage.get("tokens"), 0, 0, 2**53),
            "input_tokens": _int(usage.get("input_tokens"), 0, 0, 2**53),
            "output_tokens": _int(usage.get("output_tokens"), 0, 0, 2**53),
            "cost": round(_float(usage.get("cost"), 0.0, 0.0, 1e12), 6),
            "day": str(usage.get("day") or "")[:10],
            "day_requests": _int(usage.get("day_requests"), 0, 0, 2**53),
            "day_tokens": _int(usage.get("day_tokens"), 0, 0, 2**53),
            "month": str(usage.get("month") or "")[:7],
            "month_tokens": _int(usage.get("month_tokens"), 0, 0, 2**53),
            "month_requests": _int(usage.get("month_requests"), 0, 0, 2**53),
            "last_used": _int(usage.get("last_used"), 0, 0, 2**40),
            "last_model": str(usage.get("last_model") or "")[:200],
            "last_ip": str(usage.get("last_ip") or "")[:64],
            "refused": _int(usage.get("refused"), 0, 0, 2**53),
        },
    }


def public_key_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the panel may see: never the hash."""
    return {name: value for name, value in row.items() if name != "key_hash"}


def normalize(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    host = str(source.get("host") or DEFAULTS["host"]).strip()[:253]
    if not re.fullmatch(r"[0-9A-Za-z.:\-\[\]]+", host):
        host = DEFAULTS["host"]
    address = str(source.get("address") or "").strip()[:253]
    address = re.sub(r"^[a-z]+://", "", address, flags=re.I).strip("/ ")
    if address and not re.fullmatch(r"[0-9A-Za-z.:\-\[\]]+", address):
        address = ""
    keys: List[Dict[str, Any]] = []
    seen: set = set()
    raw_keys = list(source.get("keys")) if isinstance(source.get("keys"), list) else []
    for field in LEGACY_KEY_FIELDS:
        legacy = source.get(field)
        if isinstance(legacy, str) and legacy.strip():
            # Round 24: the single public key of older versions becomes an ordinary key row.
            raw_keys.insert(0, {"key": legacy.strip(), "id": "legacy", "name": "Прежний ключ", "rpm": 0})
    for item in raw_keys:
        row = normalize_key(item)
        if row and row["key_hash"] not in seen and row["id"] not in seen:
            seen.update((row["key_hash"], row["id"]))
            keys.append(row)
    return {
        "enabled": source.get("enabled") is True,
        "host": host,
        "port": _int(source.get("port"), DEFAULTS["port"], 0, 65535) or DEFAULTS["port"],   # 0 / empty field — the default
        "address": address,
        "models": list(dict.fromkeys(str(item)[:200] for item in source.get("models") or [] if str(item).strip()))[:2000]
        if isinstance(source.get("models"), list) else [],
        "rpm": _int(source.get("rpm"), DEFAULTS["rpm"], 0, 1_000_000),
        "concurrency": _int(source.get("concurrency"), DEFAULTS["concurrency"], 0, 1000),
        "max_output_tokens": _int(source.get("max_output_tokens"), 0, 0, 10_000_000),
        "cors": source.get("cors") is True,
        "tls_cert": str(source.get("tls_cert") or "").strip()[:500],
        "tls_key": str(source.get("tls_key") or "").strip()[:500],
        "keys": keys[:MAX_KEYS],
    }


def _needs_migration(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if any(isinstance(raw.get(field), str) and raw.get(field).strip() for field in LEGACY_KEY_FIELDS):
        return True
    return any(isinstance(row, dict) and row.get("key") for row in raw.get("keys") or [] if isinstance(raw.get("keys"), list))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class Store:
    """Settings and keys on disk, with counters kept in memory between saves."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Optional[Dict[str, Any]] = None
        self._dirty = False
        self._saved_at = 0.0

    def data(self) -> Dict[str, Any]:
        with self._lock:
            if self._data is None:
                from .storage import load_json_with_recovery
                raw = load_json_with_recovery(_path(), {}, validator=lambda value: isinstance(value, dict))
                self._data = normalize(raw)
                if _needs_migration(raw):
                    # Plaintext keys (and the single key of older versions) leave the disk at once.
                    try:
                        self.save()
                        log.info("public keys migrated to hashes")
                    except Exception:
                        log.exception("public keys: migration save failed")
            return self._data

    def reload(self) -> None:
        with self._lock:
            self._data = None

    def save(self) -> None:
        with self._lock:
            data = normalize(self.data())
            # Keep admitted requests attached to their live counters. A disk
            # snapshot must not replace the rows they will update on completion.
            from .storage import save_json
            save_json(_path(), data)
            self._dirty = False
            self._saved_at = time.time()

    def flush(self, force: bool = False) -> None:
        """Counters are written at most every ten seconds: a busy key must not rewrite the file per request."""
        with self._lock:
            if self._dirty and (force or time.time() - self._saved_at >= 10):
                self.save()

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Settings from the panel. Keys are managed by their own calls, never replaced wholesale."""
        with self._lock:
            current = dict(self.data())
            for name, value in (changes or {}).items():
                if name in DEFAULTS and name != "keys":
                    current[name] = value
            self._data = normalize(current)
            self.save()
            return self._data

    def find(self, key: str) -> Optional[Dict[str, Any]]:
        """The stored row for a presented key (compared by hash, in constant time)."""
        if not key:
            return None
        given = hash_key(str(key)).encode("ascii")
        with self._lock:
            found = None
            for row in self.data()["keys"]:
                stored = row.get("key_hash") or (hash_key(str(row["key"])) if row.get("key") else "")
                if stored and secrets.compare_digest(str(stored).encode("ascii", "ignore"), given):
                    found = row
            return found

    def get(self, key_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return next((row for row in self.data()["keys"] if row["id"] == key_id), None)

    def add_key(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """A new key. The returned copy carries the plaintext ``key`` — the only time it exists."""
        with self._lock:
            data = self.data()
            if len(data["keys"]) >= MAX_KEYS:
                raise ValueError(f"Не больше {MAX_KEYS} ключей")
            clean = {name: value for name, value in (fields or {}).items() if name in KEY_FIELDS}
            _check_key_fields(clean)
            plain = new_key()
            row = normalize_key({**clean, "key": plain, "id": secrets.token_hex(6),
                                 "created": int(time.time()), "usage": {}})
            data["keys"].append(row)
            self.save()
            return {**row, "key": plain}

    def edit_key(self, key_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Change a key. ``regenerate`` (rotation) returns the new plaintext ``key`` once."""
        _check_key_fields(fields or {})
        with self._lock:
            data = self.data()
            for index, row in enumerate(data["keys"]):
                if row["id"] == key_id:
                    merged = {**row, **{name: value for name, value in (fields or {}).items() if name in KEY_FIELDS}}
                    plain = ""
                    if (fields or {}).get("reset_usage"):
                        merged["usage"] = {}
                    if (fields or {}).get("regenerate"):
                        plain = new_key()
                        merged["key"] = plain
                        merged["rotated"] = int(time.time())
                    data["keys"][index] = normalize_key(merged)
                    self.save()
                    return {**data["keys"][index], "key": plain} if plain else data["keys"][index]
        raise ValueError("Ключ не найден")

    def delete_key(self, key_id: str) -> None:
        with self._lock:
            data = self.data()
            data["keys"] = [row for row in data["keys"] if row["id"] != key_id]
            self.save()

    # ---- counters
    def _roll(self, row: Dict[str, Any]) -> Dict[str, Any]:
        # Settings/key edits may replace a row while its request is streaming.
        # Charge the current row with the same identity, never a stale copy.
        row = next((item for item in self.data()["keys"] if item["id"] == row["id"]), row)
        usage = row["usage"]
        if usage.get("day") != _today():
            usage.update({"day": _today(), "day_requests": 0, "day_tokens": 0})
        if usage.get("month") != _month():
            usage.update({"month": _month(), "month_tokens": 0, "month_requests": 0})
        return usage

    def count_request(self, row: Dict[str, Any], model: str = "", ip: str = "") -> None:
        with self._lock:
            usage = self._roll(row)
            usage["requests"] += 1
            usage["day_requests"] += 1
            usage["month_requests"] = usage.get("month_requests", 0) + 1
            usage["last_used"] = int(time.time())
            if model:
                usage["last_model"] = str(model)[:200]
            if ip:
                usage["last_ip"] = str(ip)[:64]
            self._dirty = True

    def count_tokens(self, row: Dict[str, Any], tokens: int, *, model: str = "",
                     input_tokens: int = 0, output_tokens: int = 0) -> None:
        if tokens <= 0:
            return
        cost = 0.0
        if model and (input_tokens or output_tokens):
            try:
                from . import pricing
                cost = float(pricing.estimate(model, input_tokens=input_tokens, output_tokens=output_tokens).cost or 0.0)
            except Exception:
                cost = 0.0
        with self._lock:
            usage = self._roll(row)
            usage["tokens"] += tokens
            usage["day_tokens"] += tokens
            usage["month_tokens"] = usage.get("month_tokens", 0) + tokens
            usage["input_tokens"] = usage.get("input_tokens", 0) + max(0, int(input_tokens or 0))
            usage["output_tokens"] = usage.get("output_tokens", 0) + max(0, int(output_tokens or 0))
            usage["cost"] = round(float(usage.get("cost", 0.0)) + cost, 6)
            self._dirty = True
        self.flush()

    def count_usage(self, row: Dict[str, Any], counter: "TokenUsage", model: str = "") -> None:
        total = counter.finish()
        output = counter.output
        source_in = counter.input or max(0, total - output)
        self.count_tokens(row, total, model=model, input_tokens=source_in, output_tokens=output)

    def count_refusal(self, row: Dict[str, Any]) -> None:
        with self._lock:
            self._roll(row)["refused"] += 1
            self._dirty = True


store = Store()


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

class Limiter:
    """Sliding one-minute windows per key and for the endpoint, plus requests in flight (total and per key)."""

    def __init__(self) -> None:
        self._windows: Dict[str, Deque[float]] = {}
        self.active = 0
        self.active_by_key: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _take(self, name: str, limit: int, now: float) -> Optional[int]:
        """None when the request fits; otherwise seconds until a slot frees up."""
        if limit <= 0:
            return None
        window = self._windows.setdefault(name, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= limit:
            return max(1, int(60 - (now - window[0])) + 1)
        window.append(now)
        return None

    def check(self, row: Dict[str, Any], settings: Dict[str, Any], now: Optional[float] = None) -> Optional[Tuple[str, int]]:
        """(reason, retry_after) when the request must be refused."""
        now = time.time() if now is None else now
        with self._lock, store._lock:
            usage = store._roll(row)
            if row["budget"] and usage["tokens"] >= row["budget"]:
                return "budget", 0
            if row.get("cost_budget") and usage.get("cost", 0.0) >= row["cost_budget"]:
                return "cost_budget", 0
            if row.get("tpmonth") and usage.get("month_tokens", 0) >= row["tpmonth"]:
                return "tpmonth", _seconds_to_next_month()
            if row["rpd"] and usage["day_requests"] >= row["rpd"]:
                return "rpd", _seconds_to_midnight()
            if row["tpd"] and usage["day_tokens"] >= row["tpd"]:
                return "tpd", _seconds_to_midnight()
            if settings["concurrency"] and self.active >= settings["concurrency"]:
                return "busy", 2
            if row.get("concurrency") and self.active_by_key.get(row["id"], 0) >= row["concurrency"]:
                return "key_busy", 2
            wait = self._take("key:" + row["id"], row["rpm"], now)
            if wait is not None:
                return "rpm", wait
            wait = self._take("*", settings["rpm"], now)
            if wait is not None:
                # The key's own slot was taken a moment ago: give it back, the request did not run.
                window = self._windows.get("key:" + row["id"])
                if window and row["rpm"] > 0:
                    window.pop()
                return "endpoint_rpm", wait
            return None

    def acquire(self, key_id: str) -> None:
        with self._lock:
            self.active += 1
            self.active_by_key[key_id] = self.active_by_key.get(key_id, 0) + 1

    def release(self, key_id: str) -> None:
        with self._lock:
            self.active = max(0, self.active - 1)
            left = self.active_by_key.get(key_id, 0) - 1
            if left > 0:
                self.active_by_key[key_id] = left
            else:
                self.active_by_key.pop(key_id, None)


def _seconds_to_next_month() -> int:
    now = _dt.datetime.now()
    first = (now.replace(day=1) + _dt.timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((first - now).total_seconds()))


def _seconds_to_midnight() -> int:
    now = _dt.datetime.now()
    tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


limiter = Limiter()

_REFUSALS = {
    "budget": ("Лимит токенов этого ключа исчерпан", "This key's token budget is used up"),
    "cost_budget": ("Бюджет этого ключа исчерпан", "This key's spending budget is used up"),
    "tpmonth": ("Месячный лимит токенов этого ключа исчерпан", "This key's monthly token limit is used up"),
    "key_busy": ("Слишком много одновременных запросов с этого ключа", "Too many concurrent requests from this key"),
    "rpd": ("Дневной лимит запросов этого ключа исчерпан", "This key's daily request limit is used up"),
    "tpd": ("Дневной лимит токенов этого ключа исчерпан", "This key's daily token limit is used up"),
    "rpm": ("Слишком много запросов с этого ключа", "Too many requests from this key"),
    "endpoint_rpm": ("Сервер получает слишком много запросов", "The server is receiving too many requests"),
    "busy": ("Сервер занят, повторите через несколько секунд", "The server is busy, retry in a few seconds"),
}


def _error(message: str, message_en: str, status: int, code: str, retry_after: int = 0) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    kind = {401: "authentication_error", 403: "permission_error", 429: "rate_limit_error"}.get(status, "invalid_request_error")
    if code in ("budget", "cost_budget", "tpmonth"):
        kind = "insufficient_quota"
    return JSONResponse(status_code=status, headers=headers, content={"error": {
        "message": message_en, "message_ru": message, "type": kind, "param": None, "code": code}})


# Round 34: an upstream failure reached outside callers as «Every “custom” account failed (xkiro · api.xkiro.com):
# …» plus the owner's hint «a second account usually helps» — the public server must not name the owner's
# accounts or upstream hosts. The upstream's own reason after the colon stays: it says what to do (wait, …).
_PAREN = r"\s*\((?:[^()]|\([^()]*\))*\)"
_OWNER_TEXT = (
    (re.compile(r"Every “[^”]*” account failed" + _PAREN), "The model service failed"),
    (re.compile(r"Все аккаунты «[^»]*» недоступны" + _PAREN), "Сервис модели недоступен"),
    (re.compile(r"No usable “[^”]*” accounts\."), "The model is temporarily unavailable."),
    (re.compile(r"Нет доступных аккаунтов «[^»]*»\."), "Модель временно недоступна."),
)
_OWNER_HINTS = ("hint", "hint_en", "hint_ru")


def scrub_owner_text(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    for pattern, replacement in _OWNER_TEXT:
        text = pattern.sub(replacement, text)
    return text


def scrub_error_payload(data: Any) -> bool:
    """Remove the owner's account names and hints from an OpenAI / Anthropic error object; True if one was found."""
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return False
    for name in _OWNER_HINTS:
        err.pop(name, None)
    for name in ("message", "message_en", "message_ru"):
        if name in err:
            err[name] = scrub_owner_text(err[name])
    return True


def scrub_sse_chunk(data: bytes) -> bytes:
    if b"error" not in data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    lines = text.split("\n")
    changed = False
    for index, line in enumerate(lines):
        if not line.startswith("data:") or '"error"' not in line:
            continue
        try:
            payload = json.loads(line[5:].strip())
        except ValueError:
            continue
        if scrub_error_payload(payload):
            lines[index] = "data: " + json.dumps(payload, ensure_ascii=False)
            changed = True
    return "\n".join(lines).encode("utf-8") if changed else data


def _own_tunnel_running() -> bool:
    try:
        from . import public_tunnel
        return public_tunnel.tunnel.running()
    except Exception:
        return False


def client_ip(request: Request) -> str:
    """The caller's address. Behind a local tunnel or reverse proxy (peer is loopback) its header names the client.

    ``CF-Connecting-IP`` is trusted only while our own cloudflared runs: a local nginx passes a client's
    header through unchanged. Of ``X-Forwarded-For`` only the last entry counts — the one the nearest proxy
    appended; the entries before it come from the client and are free to lie (an IP allow-list bypass)."""
    peer = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = peer in ("testclient", "localhost")
    if loopback:
        forwarded = request.headers.get("cf-connecting-ip", "") if _own_tunnel_running() else ""
        forwarded = forwarded.strip() or (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
        if forwarded:
            return forwarded
    return peer


def _client_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.headers.get("x-api-key") or "").strip()


def offered_models(settings: Dict[str, Any], row: Optional[Dict[str, Any]] = None) -> List[str]:
    """Public ids a key may use: the endpoint's list, narrowed by the key's own list."""
    enabled = set(catalogue())
    allowed = [model for model in settings["models"] if model in enabled]
    if row and row["models"]:
        own = set(row["models"])
        allowed = [model for model in allowed if model in own]
    return allowed


def catalogue() -> List[str]:
    """Every id a local client sees in /v1/models: the pool the public list is chosen from."""
    from .openai_api import listed_models
    return [row["id"] for row in listed_models()]


class TokenUsage:
    """Read structured usage throughout a stream, including its first event."""

    def __init__(self) -> None:
        self.input = self.output = self.total = 0
        self._buffer = bytearray()
        self._data: List[bytes] = []
        self._event_bytes = 0
        self._discarding = False
        self._discard_line_nonblank = False

    def add(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        usage = value.get("usage")
        if isinstance(usage, dict):
            for field, names in (("input", ("input_tokens", "prompt_tokens")),
                                 ("output", ("output_tokens", "completion_tokens")),
                                 ("total", ("total_tokens",))):
                for name in names:
                    count = usage.get(name)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        setattr(self, field, max(getattr(self, field), count))
        for name in ("message", "response"):
            nested = value.get(name)
            if isinstance(nested, dict):
                self.add(nested)

    def _event(self) -> None:
        payload = b"\n".join(self._data)
        self._data.clear()
        self._event_bytes = 0
        try:
            self.add(json.loads(payload))
        except (ValueError, UnicodeError):
            pass

    def feed(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            end = data.find(b"\n", offset)
            terminated = end >= 0
            if not terminated:
                end = len(data)
            length = end - offset
            if self._discarding:
                if length > 1 or (length == 1 and data[offset] != 13):
                    self._discard_line_nonblank = True
                if terminated:
                    if not self._discard_line_nonblank:
                        self._discarding = False
                    self._discard_line_nonblank = False
            elif self._event_bytes + len(self._buffer) + length > MAX_SSE_EVENT_BYTES:
                # A large text delta or completed Responses object can repeat
                # the whole output. Retain no partial JSON from that event.
                self._buffer.clear()
                self._data.clear()
                self._event_bytes = 0
                self._discarding = True
                self._discard_line_nonblank = not terminated
            else:
                self._buffer.extend(data[offset:end])
                if terminated:
                    line = bytes(self._buffer).rstrip(b"\r")
                    self._buffer.clear()
                    if not line:
                        self._event()
                    elif line.startswith(b"data:"):
                        payload = line[5:].lstrip(b" ")
                        self._data.append(payload)
                        self._event_bytes += len(line) + 1
            offset = end + 1

    def finish(self) -> int:
        if self._buffer and not self._discarding:
            line = bytes(self._buffer).rstrip(b"\r")
            if line.startswith(b"data:"):
                self._data.append(line[5:].lstrip(b" "))
            self._buffer.clear()
        self._event()
        return max(self.total, self.input + self.output)


def tokens_in(body: bytes) -> int:
    """Count protocol usage, never token-like strings in assistant content."""
    counter = TokenUsage()
    try:
        counter.add(json.loads(body))
    except (ValueError, UnicodeError):
        counter.feed(body)
    return counter.finish()


# --------------------------------------------------------------------------
# The public application
# --------------------------------------------------------------------------

def create_public_app() -> FastAPI:
    from . import anthropic_api, openai_api, responses_api

    app = FastAPI(title="MemuAI Proxy — public endpoint", docs_url=None, redoc_url=None, openapi_url=None)
    # Round 35 (composite): composite models see who calls (public key) for their «client» conditions; the route
    # header stays private here (it names the owner's connections).
    from .composite_models import RouteContextMiddleware
    app.add_middleware(RouteContextMiddleware, expose=False)

    @app.middleware("http")
    async def harden(request: Request, call_next):
        settings = store.data()
        if request.method == "OPTIONS" and settings["cors"]:
            return JSONResponse({}, headers={
                "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "authorization, content-type, x-api-key, anthropic-version",
                "Access-Control-Max-Age": "600"})
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _error("Запрос слишком большой", "The request is too large", 413, "request_too_large")
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings["cors"]:
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    def admit(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
        row = store.find(_client_key(request))
        if row is None:
            return None, _error("Неверный ключ доступа", "Invalid API key", 401, "invalid_api_key")
        if not row["enabled"]:
            return None, _error("Ключ отключён", "This key is disabled", 403, "key_disabled")
        if row["expires"] and row["expires"] < _today():
            return None, _error("Срок действия ключа истёк", "This key has expired", 403, "key_expired")
        if row.get("ips") and not ip_allowed(row["ips"], client_ip(request)):
            store.count_refusal(row)
            return None, _error("Доступ с этого адреса для ключа запрещён", "This key may not be used from this IP address",
                                403, "ip_not_allowed")
        return row, None

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models(request: Request):
        row, denied = admit(request)
        if denied:
            return denied
        return {"object": "list", "data": [{"id": model, "object": "model", "created": 1735689600, "owned_by": "memuai"}
                                           for model in offered_models(store.data(), row)]}

    async def relay(request: Request, handler):
        settings = store.data()
        row, denied = admit(request)
        if denied:
            return denied
        try:
            chunks: List[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    return _error("Запрос слишком большой", "The request is too large", 413, "request_too_large")
                chunks.append(chunk)
            body = json.loads(b"".join(chunks) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _error("Тело запроса должно быть JSON-объектом", "The request body must be a JSON object", 400, "invalid_json")
        model = str(body.get("model") or "")
        if model not in offered_models(settings, row):
            store.count_refusal(row)
            if model in offered_models(settings):
                # The endpoint offers it, this key may not use it.
                return _error(f"Ключу не разрешена модель «{model}»", f"This key is not allowed to use the model '{model}'",
                              403, "model_not_allowed")
            return _error(f"Модель «{model}» недоступна для этого ключа", f"The model '{model}' is not available for this key",
                          404, "model_not_found")
        caps = [value for value in (settings["max_output_tokens"], row.get("max_tokens") or 0) if value]
        cap = min(caps) if caps else 0
        if cap:
            for field in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
                if field not in body or body[field] is None:
                    continue
                asked = body[field]
                if not isinstance(asked, int) or isinstance(asked, bool) or asked <= 0:
                    return _error(f"Некорректное значение {field}", f"{field} must be a positive integer", 400, "invalid_request")
                if asked > cap:
                    return _error(f"{field} больше разрешённых {cap}", f"{field} exceeds the allowed {cap}", 400, "max_tokens_exceeded")
            field = "max_output_tokens" if request.url.path == "/v1/responses" else "max_tokens"
            asked = body.get(field)
            if request.url.path == "/v1/chat/completions":
                asked = body.get("max_completion_tokens") or asked
            body[field] = asked or cap
        # Downstream handlers read the same Request again. Preserve the checked
        # body, including the server's default output limit.
        request._body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request._json = body
        refusal = limiter.check(row, settings)
        if refusal:
            store.count_refusal(row)
            reason, wait = refusal
            ru, en = _REFUSALS[reason]
            return _error(ru, en, 429, reason, wait)
        store.count_request(row, model, client_ip(request))
        # The handlers are the Proxy's own; this flag stands in for the local API key check.
        request.state.public_key = row["id"]
        limiter.acquire(row["id"])
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                limiter.release(row["id"])

        try:
            response = await handler(request)
        except BaseException:
            release()
            counter = TokenUsage()
            counter.add({"usage": getattr(request.state, "completion_usage", None)})
            store.count_usage(row, counter, model)
            raise
        if isinstance(response, StreamingResponse):
            inner = response.body_iterator
            counter = TokenUsage()
            counted_tokens = False

            async def counted():
                async for chunk in inner:
                    data = chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8")
                    counter.feed(bytes(data))
                    yield scrub_sse_chunk(bytes(data))

            async def finish():
                nonlocal counted_tokens
                if counted_tokens:
                    return
                counted_tokens = True
                release()
                try:
                    close = getattr(inner, "aclose", None)
                    if close is not None:
                        from .providers.http import close_quietly
                        await close_quietly(close)
                finally:
                    # Handlers record upstream usage directly too: an SSE
                    # event over the parser cap must not erase real usage.
                    counter.add({"usage": getattr(request.state, "completion_usage", None)})
                    store.count_usage(row, counter, model)

            wrapped = ClosingStreamingResponse(
                counted(), closer=finish, status_code=response.status_code,
                media_type=response.media_type, background=response.background,
            )
            wrapped.raw_headers = list(response.raw_headers)
            return wrapped
        release()
        counter = TokenUsage()
        if isinstance(response, dict):
            counter.add(response)
        else:
            data = bytes(getattr(response, "body", b"") or b"")
            try:
                counter.add(json.loads(data))
            except (ValueError, UnicodeError):
                counter.feed(data)
        counter.add({"usage": getattr(request.state, "completion_usage", None)})
        store.count_usage(row, counter, model)
        if isinstance(response, JSONResponse) and response.status_code >= 400:
            try:
                payload = json.loads(bytes(response.body or b"{}"))
            except (ValueError, UnicodeError):
                payload = None
            if scrub_error_payload(payload):
                headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
                return JSONResponse(status_code=response.status_code, content=payload, headers=headers)
        return response

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        return await relay(request, openai_api.chat_completions)

    @app.post("/v1/responses")
    async def responses(request: Request):
        return await relay(request, responses_api.responses)

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await relay(request, anthropic_api.messages)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        # A local estimate: it reaches no upstream, so it spends neither the key's limits nor its counters.
        row, denied = admit(request)
        if denied:
            return denied
        chunks: List[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return _error("Запрос слишком большой", "The request is too large", 413, "request_too_large")
            chunks.append(chunk)
        request._body = b"".join(chunks)
        request.state.public_key = row["id"]
        return await anthropic_api.count_tokens(request)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def nothing_else(path: str):
        return _error("Здесь доступен только API моделей", "Only the model API is served here", 404, "not_found")

    return app


# --------------------------------------------------------------------------
# The listener
# --------------------------------------------------------------------------

class Listener:
    """Starts, stops and restarts the public server inside the running event loop."""

    def __init__(self) -> None:
        self.server: Any = None
        self.task: Optional[asyncio.Task] = None
        self.running: Optional[Dict[str, Any]] = None     # {"host", "port", "tls"} of the live listener
        self.error = ""
        self.error_en = ""
        self._lock: Optional[asyncio.Lock] = None         # created in the loop that uses it

    def status(self) -> Dict[str, Any]:
        alive = bool(self.task and not self.task.done() and self.server and getattr(self.server, "started", False))
        return {"running": alive, "listening": dict(self.running) if alive and self.running else None,
                "error": self.error, "error_en": self.error_en or self.error}

    def _set_error(self, message: str, message_en: str) -> None:
        self.error, self.error_en = message, message_en

    async def stop(self) -> None:
        server, task = self.server, self.task
        self.server = self.task = self.running = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=8)
            except Exception:
                task.cancel()
        store.flush(force=True)

    async def apply(self) -> Dict[str, Any]:
        """Bring the listener in line with the saved settings."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            settings = store.data()
            wanted = {"host": settings["host"], "port": settings["port"],
                      "tls": bool(settings["tls_cert"] and settings["tls_key"])}
            alive = self.status()["running"]
            if not settings["enabled"]:
                if alive or self.task:
                    await self.stop()
                self._set_error("", "")
                await _tunnel_follow(None)
                return self.status()
            if alive and self.running == wanted:
                return self.status()
            await self.stop()
            self._set_error("", "")
            if wanted["tls"]:
                for label, label_en, path in (("сертификат", "certificate", settings["tls_cert"]),
                                              ("ключ сертификата", "certificate key", settings["tls_key"])):
                    if not Path(path).is_file():
                        self._set_error(f"Не найден {label}: {path}", f"The {label_en} was not found: {path}")
                        return self.status()
            busy = _port_busy(settings["host"], settings["port"])
            if busy:
                self._set_error(*busy)
                return self.status()
            import uvicorn
            config = uvicorn.Config(create_public_app(), host=settings["host"], port=settings["port"],
                                    log_config=None, access_log=False,
                                    ssl_certfile=settings["tls_cert"] if wanted["tls"] else None,
                                    ssl_keyfile=settings["tls_key"] if wanted["tls"] else None)
            server = uvicorn.Server(config)
            server.install_signal_handlers = lambda: None      # the Proxy owns Ctrl+C, not this listener
            self.server = server
            self.task = asyncio.get_running_loop().create_task(self._serve(server))
            for _ in range(60):
                if getattr(server, "started", False) or self.task.done():
                    break
                await asyncio.sleep(0.05)
            if getattr(server, "started", False):
                self.running = wanted
                log.info("public endpoint is listening on %s:%s", settings["host"], settings["port"])
                await _tunnel_follow(settings)
            elif not self.error:
                self._set_error("Сервер не запустился", "The server did not start")
            return self.status()

    async def _serve(self, server: Any) -> None:
        try:
            await server.serve()
        except SystemExit:
            if not self.error:
                self._set_error("Не удалось занять адрес и порт", "Could not bind the address and port")
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"[:300]
            self._set_error(text, text)
            log.exception("public endpoint stopped")


async def _tunnel_follow(settings: Optional[Dict[str, Any]]) -> None:
    """Keep the Cloudflare tunnel in step with the listener: off with it, re-pointed on a new port, autostart."""
    try:
        from . import public_tunnel
        tunnel = public_tunnel.tunnel
        if settings is None:
            if tunnel.running():
                await asyncio.to_thread(tunnel.stop)
            return
        tls = bool(settings["tls_cert"] and settings["tls_key"])
        if tunnel.running() and tunnel.port != settings["port"]:
            await asyncio.to_thread(tunnel.start, settings["port"], tls=tls)
        elif not tunnel.running() and tunnel.config()["autostart"] and public_tunnel.find_binary():
            await asyncio.to_thread(tunnel.start, settings["port"], tls=tls)
    except Exception:
        log.exception("public tunnel: follow failed")


def _port_busy(host: str, port: int) -> Optional[Tuple[str, str]]:
    """A clear message (ru, en) when the address cannot be bound (None when it can)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    if os.name != "nt":
        # uvicorn binds with SO_REUSEADDR: without it here, connections of the listener that was just
        # stopped (TIME_WAIT) make a free port look busy. On Windows the flag would let a probe share
        # a port with a live listener, so it stays off there.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host.strip("[]"), port))
    except OSError as exc:
        if getattr(exc, "winerror", 0) == 10049 or exc.errno in (99, 49):
            return f"На этом компьютере нет адреса {host}", f"This computer has no address {host}"
        return f"Порт {port} на {host} занят другой программой", f"Port {port} on {host} is taken by another program"
    finally:
        probe.close()
    return None


listener = Listener()


def local_addresses() -> List[str]:
    """IPv4 addresses of this computer in its networks (for the links shown in the panel)."""
    found: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))          # no packet is sent: this only asks the OS for the outgoing address
        address = probe.getsockname()[0]
        probe.close()
        if address not in found and not address.startswith("127."):
            found.insert(0, address)
    except OSError:
        pass
    return found[:6]


def public_view() -> Dict[str, Any]:
    """Everything the panel shows: settings, keys with usage, listener state, links."""
    settings = store.data()
    scheme = "https" if settings["tls_cert"] and settings["tls_key"] else "http"
    links: List[Dict[str, str]] = []
    if settings["address"]:
        shown = settings["address"]
        links.append({"kind": "address", "url": f"{scheme}://{shown}{'' if ':' in shown.split(']')[-1] else ':' + str(settings['port'])}/v1"})
    if settings["host"] in ("0.0.0.0", "::"):
        links += [{"kind": "lan", "url": f"{scheme}://{address}:{settings['port']}/v1"} for address in local_addresses()]
    elif settings["host"] not in ("127.0.0.1", "localhost", "::1"):
        links.append({"kind": "lan", "url": f"{scheme}://{settings['host']}:{settings['port']}/v1"})
    links.append({"kind": "local", "url": f"{scheme}://127.0.0.1:{settings['port']}/v1"}
                 if settings["host"] in ("0.0.0.0", "127.0.0.1", "localhost") else {"kind": "local", "url": ""})
    return {"settings": {name: settings[name] for name in DEFAULTS if name != "keys"},
            "keys": [public_key_row(row) for row in settings["keys"]], "status": listener.status(),
            "catalogue": sorted(catalogue()), "tunnel": _tunnel_status(),
            "links": [row for row in links if row["url"]], "today": _today()}


def _tunnel_status() -> Dict[str, Any]:
    try:
        from . import public_tunnel
        return public_tunnel.tunnel.status()
    except Exception as exc:       # the panel must open even if the tunnel module breaks
        return {"state": "error", "error": f"{type(exc).__name__}: {exc}"[:200]}
