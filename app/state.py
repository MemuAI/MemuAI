"""Состояние MemuAI Proxy: аккаунты, каталог моделей и журнал запросов.

Всё лежит в JSON в data/ с бэкапами и автовосстановлением (storage.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from fastapi.responses import StreamingResponse

from .config import DATA_DIR, RETIRED_PROVIDER_IDS, config_store

ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
MODELS_PATH = os.path.join(DATA_DIR, "models.json")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
LOGS_PATH = os.path.join(LOGS_DIR, "requests.jsonl")

LOG_MEMORY_LIMIT = 3000
LOG_FILE_MAX_BYTES = 12 * 1024 * 1024

def _read_log_tail(path: str, *, row_limit: int, byte_limit: int):
    """Read recent complete UTF-8 JSON records without buffering the file."""
    tail = deque()
    size = 0
    row_limit = max(1, int(row_limit))
    byte_limit = max(1, int(byte_limit))
    with open(path, "rb") as handle:
        while raw := handle.readline(byte_limit + 1):
            if len(raw) > byte_limit:

                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(byte_limit + 1)
                continue
            if not raw.endswith(b"\n"):
                continue
            try:
                if not isinstance(json.loads(raw.decode("utf-8")), dict):
                    continue
            except (ValueError, RecursionError):
                continue
            tail.append(raw)
            size += len(raw)
            while tail and (size > byte_limit or len(tail) > row_limit):
                size -= len(tail.popleft())
    return tail

ST_UNKNOWN = "unknown"
ST_OK = "ok"
ST_INVALID = "invalid"
ST_DISABLED = "disabled"

_RETIRED_STATUSES = frozenset({"cooldown"})

STATUS_LABELS = {
    ST_UNKNOWN: "не проверен",
    ST_OK: "работает",
    ST_INVALID: "невалидный",
    ST_DISABLED: "выключен",
}

_PROGRAM_DIR = os.environ.get("MEMUAI_PROGRAM_DIR") or os.path.dirname(os.path.dirname(DATA_DIR))
_LEGACY_ROOTS = [
    _PROGRAM_DIR,
    os.path.dirname(os.path.dirname(DATA_DIR)),
    os.path.dirname(DATA_DIR),
]
LEGACY_DIR_CANDIDATES = []
for _root in _LEGACY_ROOTS:
    for _cand in (os.path.join(_root, "data"), os.path.join(_root, "omnihub", "data"),
                  os.path.join(_root, "genspark2api", "data"),
                  os.path.join(_root, "genspark2api", "genspark2api", "data")):

        if os.path.normcase(os.path.abspath(_cand)) != os.path.normcase(os.path.abspath(DATA_DIR)):
            LEGACY_DIR_CANDIDATES.append(_cand)

class ClosingStream:
    """Own an iterator's cleanup even when it has never been advanced."""

    def __init__(self, source, closer=None) -> None:
        self.source = source.__aiter__()
        self.closer = closer
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        try:
            return await anext(self.source)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        from .providers.http import close_quietly
        try:
            close = getattr(self.source, "aclose", None)
            if close is not None:
                await close_quietly(close)
        finally:
            if self.closer is not None:
                await close_quietly(self.closer)

class ClosingStreamingResponse(StreamingResponse):
    """Close prefetched sources if ASGI exits before entering the body iterator."""

    def __init__(self, content, *, closer=None, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self.body_iterator = ClosingStream(self.body_iterator, closer)

    async def aclose(self) -> None:
        await self.body_iterator.aclose()

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.aclose()

api_language: ContextVar[str] = ContextVar("memuai_api_language", default="en")

def request_language(request) -> str:
    return "ru" if str(request.headers.get("x-memuai-lang") or "").strip().lower().startswith("ru") else "en"

def api_error_text(message_ru: str, message_en: str = "") -> Dict[str, str]:
    """``message`` in the request's language plus both variants (``message_ru``, ``message_en``)."""
    message_en = message_en or message_ru
    fields = {"message": message_ru if api_language.get() == "ru" else message_en, "message_en": message_en}
    if message_ru != message_en:
        fields["message_ru"] = message_ru
    return fields

async def until_client_disconnects(request, work):
    """Run a model API handler, cancelling it when the client hangs up first.

    Round 27: Starlette never cancels a handler whose client has gone, so a non-stream request (and a stream
    still waiting for its first chunk) kept reading the upstream answer to the end — the tokens were spent,
    the account and the public key's concurrency slot stayed busy. The body is read before watching, so the
    watcher only ever sees the disconnect; a returned StreamingResponse watches the client by itself."""
    from fastapi.responses import Response

    api_language.set(request_language(request))
    await request.body()
    task = asyncio.ensure_future(work)

    async def client_gone() -> None:
        while True:
            message = await request.receive()
            if message.get("type") == "http.disconnect":
                return
            await asyncio.sleep(0.05)

    watcher = asyncio.ensure_future(client_gone())
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        task.cancel()
        raise
    finally:
        watcher.cancel()
    if task.done():
        return task.result()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return Response(status_code=499)

def _now() -> float:
    return time.time()

_REDACTED = "[REDACTED]"
_SECRET_METADATA_SUFFIXES = (
    "budget", "count", "enabled", "endpoint", "expiration", "expiresat",
    "expiry", "limit", "method", "mode", "provider", "scheme", "scope",
    "scopes", "status", "timeout", "ttl", "type", "url", "usage",
)
_PUBLIC_SECRET_LIKE_METADATA_KEYS = {
    "cookieconsent", "cookiedomain", "cookiepath", "cookiepolicy", "cookiesamesite",
    "sessionname",
}
_PROXY_USERINFO_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/?#]*@", re.IGNORECASE)

def _normalized_key(key: Any) -> str:
    """Return a case/separator-insensitive form used only for secret detection."""
    return re.sub(r"[^a-z0-9]+", "", str(key).casefold())

def _is_secret_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    if normalized in _PUBLIC_SECRET_LIKE_METADATA_KEYS:
        return False

    if normalized.endswith(_SECRET_METADATA_SUFFIXES):
        return False

    for public_prefix in ("tokenizer", "tokenization"):
        if normalized.startswith(public_prefix):
            normalized = normalized[len(public_prefix):]
            if not normalized:
                return False
            break
    if any(part in normalized for part in (
        "token", "cookie", "password", "passwd", "passphrase", "pwd",
        "secret", "credential", "session", "apikey", "privatekey",
        "accesskey", "signingkey", "encryptionkey", "bearer", "passcode",
        "authorization", "authentication", "oauth", "csrf", "xsrf", "jwt",
    )):
        return True

    return normalized == "auth" or (
        normalized.startswith("auth") and not normalized.startswith(("author", "authority"))
    )

def _redact_secrets(value: Any) -> Any:
    """Clone nested JSON-like data, replacing values stored under secret keys."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(key) else _redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secrets(item) for item in value)
    return value

def _redact_proxy_credentials(proxy: str) -> str:
    """Remove RFC 3986 userinfo from a proxy URL without changing its endpoint."""
    return _PROXY_USERINFO_RE.sub(r"\1", proxy)

def _log_payloads_enabled() -> bool:
    """Настройка «сохранять payload стадий» (по умолчанию включена)."""
    try:
        from .config import config_store
        return bool(config_store.settings.log_payloads)
    except Exception:
        return True

def _log_persist_enabled() -> bool:
    try:
        from .config import config_store
        return bool(config_store.settings.log_persist)
    except Exception:
        return True

def _prompt_secret_values() -> List[str]:
    """Known transport credentials never belong to the local prompt display."""
    values = []
    settings = config_store.settings
    values.extend(getattr(settings, "api_keys", ()) or ())
    values.append(getattr(settings, "search_api_key", ""))
    values.extend((getattr(settings, "search_keys", {}) or {}).values())
    owner = globals().get("state")
    remaining = 2000

    def walk(value, sensitive=False, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 8:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if _normalized_key(key) == "apikeys" and isinstance(item, str):

                    values.extend(part.strip() for part in re.split(r"[\r\n;]+", item)
                                  if part.strip())
                walk(item, sensitive or _is_secret_key(key), depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, sensitive, depth + 1)
        elif sensitive and isinstance(value, str) and value:
            values.append(value)

    if owner is not None:
        with owner._lock:
            accounts = list(owner.accounts.values())
        for account in accounts:
            if remaining < 0:
                break
            walk(account.fields)
            walk(account.extra)
    return list(dict.fromkeys(value.strip() for value in values
                              if isinstance(value, str) and value.strip()))

_ACCOUNT_KEYS = {"id", "priority", "order", "note", "tags", "enabled", "status",
                 "status_message", "last_used", "last_checked", "cooldown_until",
                 "created", "extra", "stats", "_unknown"}

class Account:
    """Аккаунт любого провайдера. Специфика — в fields/extra (прозрачно для ядра)."""

    def __init__(self, provider: str, fields: Dict[str, Any], label: str = "",
                 proxy: str = "", **kw: Any) -> None:
        self.id: str = kw.get("id") or uuid.uuid4().hex[:12]
        self.provider: str = provider
        self.label: str = label or ""
        self.proxy: str = (proxy or "").strip()
        self.priority: str = kw.get("priority", "normal")

        try:
            self.order: int = int(kw.get("order", 0) or 0)
        except (TypeError, ValueError):
            self.order = 0
        self.note: str = str(kw.get("note", "") or "")
        tags = kw.get("tags") or []
        self.tags: List[str] = [str(tag)[:40] for tag in tags][:12] if isinstance(tags, list) else []
        self.enabled: bool = bool(kw.get("enabled", True))
        self.status: str = kw.get("status", ST_UNKNOWN)
        self.status_message: str = kw.get("status_message", "")
        if self.status in _RETIRED_STATUSES:

            self.status, self.status_message = ST_UNKNOWN, ""
        self.last_used: float = kw.get("last_used") or 0.0
        self.last_checked: float = kw.get("last_checked") or 0.0
        self.created: float = kw.get("created") or _now()
        self.fields: Dict[str, Any] = fields or {}
        self.extra: Dict[str, Any] = kw.get("extra") or {}

        self._unknown: Dict[str, Any] = {key: value for key, value in kw.items()
                                         if key not in _ACCOUNT_KEYS}
        stats = kw.get("stats") if isinstance(kw.get("stats"), dict) else {}
        self.stats: Dict[str, Any] = {
            "requests": int(stats.get("requests", 0) or 0),
            "success": int(stats.get("success", 0) or 0),
            "failures": int(stats.get("failures", 0) or 0),
            "total_ms": int(stats.get("total_ms", 0) or 0),
            "last_error": str(stats.get("last_error", "") or "")[:400],
            "last_error_at": float(stats.get("last_error_at", 0) or 0),
        }

    def record_result(self, ok: bool, *, ms: int = 0, error: str = "") -> None:
        self.stats["requests"] = int(self.stats.get("requests", 0)) + 1
        if ok:
            self.stats["success"] = int(self.stats.get("success", 0)) + 1

            self.stats.pop("last_error", None)
            self.stats.pop("last_error_at", None)
        else:
            self.stats["failures"] = int(self.stats.get("failures", 0)) + 1
            if error:
                self.stats["last_error"] = error[:400]
                self.stats["last_error_at"] = _now()
        if ms > 0:
            self.stats["total_ms"] = int(self.stats.get("total_ms", 0)) + int(ms)

    def usable(self) -> bool:
        """В ротации: включён и не отвергнут сервисом (невалидный вход)."""
        return self.provider not in RETIRED_PROVIDER_IDS and self.enabled and self.status != ST_INVALID

    def mark(self, status: str, message: str = "") -> None:
        self.status = status
        self.status_message = message
        if status == ST_OK:

            self.stats.pop("last_error", None)
            self.stats.pop("last_error_at", None)

    def public_dict(self, mask: bool = True) -> Dict[str, Any]:
        fields = dict(self.fields)
        if mask:
            for k, v in fields.items():
                if isinstance(v, str) and len(v) > 14 and k not in ("base_url", "name", "space_id", "user_id"):
                    fields[k] = v[:6] + "…" + v[-4:]

        fields = _redact_secrets(fields)
        return {
            "id": self.id, "provider": self.provider, "label": self.label,
            "proxy": _redact_proxy_credentials(self.proxy),
            "enabled": self.enabled, "priority": self.priority,
            "order": self.order, "note": self.note, "tags": list(self.tags),
            "stats": dict(self.stats),
            "status": self.status, "status_label": STATUS_LABELS.get(self.status, self.status),
            "status_message": self.status_message,
            "last_used": self.last_used, "last_checked": self.last_checked,
            "created": self.created,
            "fields": fields, "extra": _redact_secrets(self.extra),
            "has_extra": bool(self.extra),
        }

class ModelEntry:
    """Модель каталога: публичный id = prefix + '-' + model.

    prefix — id провайдера или имя кастомного подключения;
    account_id — привязка к конкретному аккаунту ("" — любой аккаунт провайдера).
    """

    def __init__(self, prefix: str, provider: str, model: str, *,
                 account_id: str = "", enabled: bool = True,
                 source: str = "seed", note: str = "", availability: Optional[Dict[str, Any]] = None) -> None:
        self.prefix = prefix
        self.provider = provider
        self.account_id = account_id
        self.model = model
        self.enabled = enabled
        self.source = source
        self.note = note
        self.availability = dict(availability) if isinstance(availability, dict) else {}
        self.id = f"{prefix}-{model}"

    def public_dict(self) -> Dict[str, Any]:
        row = {"id": self.id, "prefix": self.prefix, "provider": self.provider,
                "account_id": self.account_id, "model": self.model,
                "enabled": self.enabled, "source": self.source, "note": self.note}
        if self.availability:
            row["availability"] = dict(self.availability)
        return row

def _is_available_model(entry: "ModelEntry") -> bool:
    """Hide known provider modes that exist in bundles but not in chat."""
    if entry.provider in RETIRED_PROVIDER_IDS:
        return False
    model = str(entry.model or "").lower().replace("_", "-")
    if entry.provider == "geminiweb":

        try:
            from .providers import get as get_provider
            provider = get_provider("geminiweb")
            return not (provider and provider.catalog_hidden(model))
        except Exception:
            return True
    return not (entry.provider == "genspark" and "fable-5" in model)

class LogEntry:
    """Запись о запросе: итог + цепочка стадий с payload'ами (как в KiCk)."""

    MAX_STAGE_PAYLOAD = 12000

    def __init__(self, **kw: Any) -> None:
        self.ts: float = kw.get("ts", _now())
        self.path: str = kw.get("path", "")
        self.model: str = kw.get("model", "")
        self.provider: str = kw.get("provider", "")
        self.status: int = kw.get("status", 0)
        self.ms: int = kw.get("ms", 0)
        self.account: str = kw.get("account", "")
        self.prompt_tokens: int = kw.get("prompt_tokens", 0)
        self.completion_tokens: int = kw.get("completion_tokens", 0)
        self.error: str = kw.get("error", "")

        self.error_en: str = kw.get("error_en", "")
        self.error_code: str = kw.get("error_code", "")
        self.stages: List[Dict[str, Any]] = kw.get("stages") or []
        self.request_id: str = kw.get("request_id", uuid.uuid4().hex[:32])
        self.prompt = None
        self._prompt_bytes = 0
        stored = kw.get("prompt")
        if isinstance(stored, dict):
            self.capture_prompt(stored.get("messages"), tools=stored.get("tools"))
            if self.prompt is not None:
                self.prompt["truncated"] |= bool(stored.get("truncated"))
                self.prompt["omitted_media"] |= bool(stored.get("omitted_media"))

    def capture_prompt(self, messages, *, tools=None) -> None:
        if self.prompt is not None or not _log_payloads_enabled():
            return
        from .prompt_logging import snapshot_prompt
        self.prompt = snapshot_prompt(messages, tools=tools, secrets=_prompt_secret_values(),
                                      is_secret=_is_secret_key)
        if self.prompt is not None:
            try:
                self._prompt_bytes = len(json.dumps(self.prompt, ensure_ascii=False).encode("utf-8"))
            except (ValueError, RecursionError):
                self._prompt_bytes = LOG_FILE_MAX_BYTES + 1

    UNIQUE_STAGES = ("request_parsed",)

    def add_stage(self, step: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if len(self.stages) >= 40:
            return
        if step in self.UNIQUE_STAGES and any(s.get("step") == step for s in self.stages):
            return
        if payload is not None and not _log_payloads_enabled():
            payload = None
        try:
            data = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
            if len(data) > self.MAX_STAGE_PAYLOAD:
                data = data[:self.MAX_STAGE_PAYLOAD] + " …(обрезано)"
        except Exception:
            data = str(payload)[:self.MAX_STAGE_PAYLOAD]
        self.stages.append({"ts": _now(), "step": step, "payload": data})

    def public_dict(self) -> Dict[str, Any]:
        return {"ts": self.ts, "path": self.path, "model": self.model,
                "provider": self.provider, "status": self.status, "ms": self.ms,
                "account": self.account, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "error": self.error,
                "error_en": self.error_en or self.error,
                "error_code": self.error_code, "request_id": self.request_id,
                "stages": self.stages,
                "prompt": self.prompt if _log_payloads_enabled() else None}

class State:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.accounts: Dict[str, Account] = {}
        self.models: Dict[str, ModelEntry] = {}
        self._unparsed_accounts: List[Any] = []
        self._unparsed_models: List[Any] = []
        self.logs: deque = deque(maxlen=LOG_MEMORY_LIMIT)
        self._save_timer: Optional[threading.Timer] = None
        self._load()
        self._load_logs()
        self._import_legacy()

    def _load(self) -> None:
        from .storage import load_json_with_recovery

        items = load_json_with_recovery(ACCOUNTS_PATH, [], validator=lambda value: isinstance(value, list)) or []
        for it in items:
            try:
                if isinstance(it, dict) and it.get("provider") in RETIRED_PROVIDER_IDS:
                    self._unparsed_accounts.append(it)
                    continue
                row = dict(it) if isinstance(it, dict) else {}
                fields = row.pop("fields", {})
                acc = Account(fields=fields, **row)
                self.accounts[acc.id] = acc
            except Exception:
                self._unparsed_accounts.append(it)
                continue
        items = load_json_with_recovery(MODELS_PATH, []) or []
        for it in items:
            try:
                if isinstance(it, dict) and it.get("provider") in RETIRED_PROVIDER_IDS:
                    self._unparsed_models.append(it)
                    continue
                it = dict(it)
                it.pop("id", None)
                m = ModelEntry(**it)
                self.models[m.id] = m
            except Exception:
                self._unparsed_models.append(it)
                continue

    def save_accounts(self, *, backup: bool = True) -> None:
        from .storage import save_json
        with self._lock:
            pending = getattr(self, "_save_timer", None)
            if pending is not None:
                pending.cancel()
            self._save_timer = None
            rows = list(self._unparsed_accounts)
            for acc in self.accounts.values():
                d = acc.__dict__.copy()
                unknown = d.pop("_unknown", None) or {}
                rows.append({**unknown, **d})
            save_json(ACCOUNTS_PATH, rows, backup=backup)

    def save_accounts_soon(self, delay: float = 5.0) -> None:
        """Отложенная запись одной статистики (счётчики, время ответа).

        Раньше каждый успешный запрос синхронно писал accounts.json с fsync и
        бэкапом: кольцо из 10 бэкапов сменялось за 10 запросов и не спасало от
        ошибки пользователя, а запись блокировала цикл событий.
        """
        with self._lock:
            if getattr(self, "_save_timer", None) is not None:
                return
            timer = threading.Timer(delay, self._flush_soon)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def _flush_soon(self) -> None:
        with self._lock:
            if getattr(self, "_save_timer", None) is None:
                return
            self._save_timer = None
        try:
            self.save_accounts(backup=False)
        except Exception as exc:

            logging.getLogger("memuai.state").warning("deferred accounts save failed: %s", exc)
            self.save_accounts_soon(30.0)

    def flush_pending(self) -> None:
        if getattr(self, "_save_timer", None) is not None:
            self._flush_soon()

    def save_models(self) -> None:
        from .storage import save_json
        with self._lock:
            rows = list(self._unparsed_models)
            for m in self.models.values():
                d = m.__dict__.copy()
                d.pop("id")
                rows.append(d)
            save_json(MODELS_PATH, rows)

    def _import_legacy(self) -> None:
        self._import_legacy_accounts()
        self._import_legacy_models()

    def _import_legacy_models(self) -> None:
        from .storage import load_json_with_recovery

        if not self.models and any(acc.provider == "genspark" for acc in self.accounts.values()):
            for cand in LEGACY_DIR_CANDIDATES:
                legacy_models = os.path.join(cand, "models.json")

                if not os.path.isfile(legacy_models):
                    continue
                try:
                    old_models = load_json_with_recovery(legacy_models, []) or []
                except OSError:
                    continue
                if not isinstance(old_models, list):
                    continue
                imported = 0
                for it in old_models:
                    try:
                        if it.get("kind", "text") != "text":
                            continue

                        raw = str(it.get("model") or it.get("upstream") or it.get("id") or "").strip()
                        if not raw or raw == "auto":
                            continue
                        m = ModelEntry("genspark", "genspark", raw,
                                       enabled=bool(it.get("enabled", True)),
                                       source=it.get("source", "seed"),
                                       note=it.get("note", "импорт из genspark2api"))
                        if m.id not in self.models:
                            self.models[m.id] = m
                            imported += 1
                    except Exception:
                        continue
                if imported:
                    self.save_models()

    def _import_legacy_accounts(self) -> None:
        from .storage import load_json_with_recovery

        if config_store.settings.imported_from:
            return
        for cand in LEGACY_DIR_CANDIDATES:
            acc_path = os.path.join(cand, "accounts.json")
            cfg_path = os.path.join(cand, "config.json")
            if not (os.path.exists(acc_path) or os.path.exists(cfg_path)):
                continue
            try:
                old_accounts = load_json_with_recovery(acc_path, []) or []
                old_cfg = load_json_with_recovery(cfg_path, {}) or {}
                for it in old_accounts:
                    if not isinstance(it, dict) or not it:
                        continue

                    fields = dict(it.get("fields") or {})
                    provider = str(it.get("provider", "genspark"))
                    if not fields:
                        cookie = str(it.get("cookie") or "")
                        if not cookie:
                            continue
                        provider = "genspark"
                        fields = {"cookie": cookie if "session_id=" in cookie.lower() else f"session_id={cookie}"}
                    if not any(str(v).strip() for v in fields.values()):
                        continue
                    acc = Account(provider, fields=fields,
                                  label=it.get("label", "") or "импорт",
                                  proxy=it.get("proxy", ""),
                                  priority=it.get("priority", "normal"),
                                  status=it.get("status", ST_UNKNOWN),
                                  status_message=it.get("status_message", ""),
                                  enabled=it.get("enabled", True))
                    self.accounts[acc.id] = acc
                legacy_updates = {
                    key: old_cfg[key]
                    for key in ("port", "host", "default_proxy", "verify_ssl", "log_requests")
                    if key in old_cfg
                }
                if old_cfg.get("api_keys") and not config_store.settings.api_keys:
                    legacy_updates["api_keys"] = old_cfg["api_keys"]
                gs_cfg = old_cfg.get("provider_settings", {}).get("genspark", {})
                merged = dict(gs_cfg)
                for key in ("chat_backend", "context_mode", "reasoning_output", "auto_delete_chats",
                            "enable_search_default", "impersonate",
                            "user_agent", "request_timeout", "stream_idle_timeout", "max_account_retries"):
                    if key in old_cfg:
                        merged[key] = old_cfg[key]
                if merged:
                    legacy_updates["provider_settings"] = {"genspark": merged}
                legacy_updates["imported_from"] = cand

                config_store.apply_updates(legacy_updates)
                self.save_accounts()
                return
            except Exception:
                continue

    def add_account(self, provider: str, fields: Dict[str, Any], label: str = "",
                    proxy: str = "", extra: Dict[str, Any] | None = None) -> Account:
        if provider in RETIRED_PROVIDER_IDS:
            raise ValueError("Провайдер удалён / Provider has been removed")
        acc = Account(provider, fields=fields, label=label, proxy=proxy, extra=extra or {})
        with self._lock:
            self.accounts[acc.id] = acc
            self.save_accounts()
        return acc

    def remove_account(self, account_id: str) -> bool:
        with self._lock:
            if account_id in self.accounts:
                del self.accounts[account_id]
                self.save_accounts()
                return True
            return False

    def get_account(self, account_id: str) -> Optional[Account]:
        return self.accounts.get(account_id)

    def accounts_of(self, provider: str) -> List[Account]:
        with self._lock:
            return sorted((a for a in self.accounts.values() if a.provider == provider),
                          key=lambda a: (a.order, a.created))

    def all_accounts(self) -> List[Account]:
        """Сами объекты аккаунтов в порядке приоритета.

        ``list_accounts`` отдаёт словари для панели; тому, кто меняет аккаунты
        или спрашивает провайдера, нужны именно объекты.
        """
        with self._lock:
            return sorted(self.accounts.values(), key=lambda a: (a.order, a.created))

    def list_accounts(self, mask: bool = True) -> List[Dict[str, Any]]:
        with self._lock:

            ordered = sorted(self.accounts.values(),
                             key=lambda a: (a.order, a.created))
            return [a.public_dict(mask=mask) for a in ordered]

    def usable_accounts(self, provider: str) -> List[Account]:
        with self._lock:
            return sorted((a for a in self.accounts.values()
                           if a.provider == provider and a.usable()),
                          key=lambda a: (a.order, a.created))

    def reorder_accounts(self, provider: str, ordered_ids: List[str]) -> int:
        """Задаёт порядок аккаунтов провайдера по списку id (drag & drop)."""
        changed = 0
        with self._lock:
            position = 0
            seen = set()
            for account_id in ordered_ids:
                account = self.accounts.get(account_id)
                if account is None or account.provider != provider or account_id in seen:
                    continue
                seen.add(account_id)
                if account.order != position:
                    account.order = position
                    changed += 1
                position += 1

            for account in sorted((a for a in self.accounts.values()
                                   if a.provider == provider and a.id not in seen),
                                  key=lambda a: (a.order, a.created)):
                account.order = position
                position += 1
            if changed:
                self.save_accounts()
        return changed

    def reorder_accounts_global(self, ordered_ids: List[str]) -> int:
        """Задаёт порядок всех аккаунтов сразу, без привязки к провайдеру.

        Приоритет внутри провайдера остаётся прежним: пул сортирует свои
        аккаунты по тому же полю ``order``, поэтому карточка выше в общем
        списке по-прежнему используется раньше.
        """
        changed = 0
        with self._lock:
            position = 0
            seen: set = set()
            for account_id in ordered_ids:
                account = self.accounts.get(account_id)
                if account is None or account_id in seen:
                    continue
                seen.add(account_id)
                if account.order != position:
                    account.order = position
                    changed += 1
                position += 1
            for account in sorted((a for a in self.accounts.values() if a.id not in seen),
                                  key=lambda a: (a.order, a.created)):
                if account.order != position:
                    account.order = position
                    changed += 1
                position += 1
            if changed:
                self.save_accounts()
        return changed

    def next_order(self, provider: str = "") -> int:
        """Хвост общей очереди: новый аккаунт встаёт последним по приоритету."""
        with self._lock:
            orders = [a.order for a in self.accounts.values()]
            return (max(orders) + 1) if orders else 0

    def upsert_model(self, entry: ModelEntry) -> None:
        with self._lock:
            self.models[entry.id] = entry
            self.save_models()

    def remove_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id in self.models:
                del self.models[model_id]
                self.save_models()
                return True
            return False

    def _model_owners(self) -> tuple:
        """Кто может обслужить модель каталога: провайдеры с аккаунтами и имена подключений.

        Аккаунт, который не удалось прочитать (запись более новой версии), тоже
        считается: его модели нужны, как только аккаунт снова читается.
        """
        providers: set = set()
        connections: set = set()
        rows = [(acc.provider, acc.fields) for acc in self.accounts.values()]
        rows += [(str(row.get("provider") or ""), row.get("fields") if isinstance(row.get("fields"), dict) else {})
                 for row in self._unparsed_accounts if isinstance(row, dict)]
        for provider, fields in rows:
            if provider in RETIRED_PROVIDER_IDS:
                continue
            providers.add(provider)
            if provider == "custom":
                name = str((fields or {}).get("name", "")).strip().lower().replace(" ", "-")
                if name:
                    connections.add(name)
        return providers, connections

    @staticmethod
    def _owned(entry: "ModelEntry", owners: tuple) -> bool:
        providers, connections = owners
        if entry.provider == "local":
            return True
        if entry.provider == "custom":
            return entry.prefix.lower() in connections
        return entry.provider in providers

    def _visible(self) -> List[ModelEntry]:
        """Модели, которые можно показать и вызвать: без моделей удалённых провайдеров.

        Каталог чистится при удалении аккаунта и на старте, но модели могли
        попасть в него и в обход (импорт, старые версии, ручное добавление):
        список не должен показывать модели, которые некому обслужить.
        """
        owners = self._model_owners()
        return [m for m in self.models.values() if _is_available_model(m) and self._owned(m, owners)]

    def enabled_models(self) -> List[ModelEntry]:
        with self._lock:
            return [m for m in self._visible() if m.enabled and m.availability.get("available") is not False]

    def catalog_models(self) -> List[ModelEntry]:
        with self._lock:
            return self._visible()

    def model_has_owner(self, entry: "ModelEntry") -> bool:
        with self._lock:
            return self._owned(entry, self._model_owners())

    def list_models(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted((m.public_dict() for m in self._visible()),
                          key=lambda m: (m["prefix"], m["model"]))

    def seed_provider_models(self, provider: str, prefix: str,
                             models: List[tuple], account_id: str = "") -> int:
        """Добавляет недостающие модели провайдера. models = [(model, note)]."""
        added = 0
        with self._lock:
            for model, note in models:
                if model in ("", "auto"):
                    continue
                e = ModelEntry(prefix, provider, model, note=note, account_id=account_id)
                if not _is_available_model(e):
                    continue
                if e.id not in self.models:
                    self.models[e.id] = e
                    added += 1
        if added:
            self.save_models()
        return added

    def prune_orphan_models(self, only: Optional[set] = None) -> int:
        """Убирает модели провайдеров, у которых не осталось ни одного аккаунта.

        Каталог встроенного провайдера сеется на провайдера целиком, поэтому
        после удаления последнего аккаунта (Genspark, Notion …) его модели
        раньше оставались в списке навсегда: старые версии чистили каталог
        не на всех путях удаления. Модели ``custom`` уходят вместе с
        подключением (их prefix — имя подключения); пока подключение живо,
        его вручную добавленные модели остаются. Не трогаем:

        * ``local`` — локальные модели работают без аккаунтов;
        * провайдеров, чьи аккаунты не удалось прочитать (запись из более новой
          версии схемы): их модели нужны, как только аккаунт снова читается.
        """
        with self._lock:
            owners = self._model_owners()
            wanted = {str(name).strip().lower() for name in only or () if str(name).strip()}
            doomed = [m.id for m in self.models.values()
                      if not self._owned(m, owners) and (not wanted or m.provider in wanted)]
            for model_id in doomed:
                self.models.pop(model_id, None)
            if doomed:

                self.save_models()
            return len(doomed)

    def find_model(self, model_id: str) -> Optional[ModelEntry]:
        with self._lock:
            owners = self._model_owners()
            m = self.models.get(model_id)
            if m and _is_available_model(m) and self._owned(m, owners):
                return m
            normalized = model_id.lower().replace("_", "-")
            for m in self.models.values():
                if (m.id.lower().replace("_", "-") == normalized and _is_available_model(m)
                        and self._owned(m, owners)):
                    return m
            return None

    def open_trace(self, model: str = "", provider: str = "") -> "LogEntry":
        """Create and publish a live request entry before the upstream starts."""
        entry = LogEntry(model=model, provider=provider)
        with self._lock:
            self.logs.appendleft(entry)
        return entry

    def commit_trace(self, entry: "LogEntry") -> None:
        """Keep a completed live entry at the top without duplicating it."""
        with self._lock:
            known = any(current is entry or current.request_id == entry.request_id
                        for current in self.logs)
            if not known:
                self.logs.appendleft(entry)

            size = sum(current._prompt_bytes for current in self.logs)
            if size > LOG_FILE_MAX_BYTES:
                keep = []
                for current in reversed(self.logs):
                    if size > LOG_FILE_MAX_BYTES and current is not entry and current.status:
                        size -= current._prompt_bytes
                    else:
                        keep.append(current)
                self.logs = deque(reversed(keep), maxlen=self.logs.maxlen)
        self.persist_log(entry)

    def cancel_trace(self, entry: "LogEntry", usage=None) -> None:
        """Finish a request canceled before its handler or stream completed."""
        if entry.status:
            return
        entry.status = 499
        entry.error = entry.error or "client disconnected"
        entry.error_code = entry.error_code or "client_disconnected"
        entry.ms = int((_now() - entry.ts) * 1000)
        if isinstance(usage, dict):
            for field, names in (("prompt_tokens", ("prompt_tokens", "input_tokens")),
                                 ("completion_tokens", ("completion_tokens", "output_tokens"))):
                for name in names:
                    count = usage.get(name)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        setattr(entry, field, count)
                        break
        try:
            self.commit_trace(entry)
        except Exception:
            pass

    def guard_stream(self, stream, entry: "LogEntry"):
        """Отдаёт поток как есть; при обрыве клиентом закрывает запись журнала.

        Без этого запись навсегда оставалась «в процессе» (status 0): код
        фиксации стоит после цикла генератора и при отключении не выполняется.
        """
        completed = False

        async def forward():
            nonlocal completed
            async for item in stream:
                yield item
            completed = True

        async def finish():

            from .providers.http import close_quietly
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close_quietly(close)
            if not completed:
                self.cancel_trace(entry)

        return ClosingStream(forward(), finish)

    def discard_trace(self, entry: "LogEntry") -> None:
        """Remove a successful live entry when request logging is disabled."""
        with self._lock:
            self.logs = deque(
                (current for current in self.logs if current.request_id != entry.request_id),
                maxlen=self.logs.maxlen,
            )

    def list_logs(self, limit: int = 100, *, offset: int = 0, query: str = "",
                  status: str = "", provider: str = "", model: str = "",
                  since: float = 0.0) -> Dict[str, Any]:
        """Фильтрация журнала: поиск по тексту + статус/провайдер/модель/время."""
        needle = (query or "").strip().lower()
        with self._lock:
            rows = [e.public_dict() for e in list(self.logs)]

        def matches(row: Dict[str, Any]) -> bool:
            if since and float(row.get("ts") or 0) < since:
                return False
            if provider and row.get("provider") != provider:
                return False
            if model and model.lower() not in str(row.get("model", "")).lower():
                return False
            code = int(row.get("status") or 0)
            if status == "ok" and code != 200:
                return False
            if status == "error" and (code == 200 or code == 0):
                return False
            if status == "pending" and code:
                return False
            if needle:
                haystack = " ".join([
                    str(row.get("model", "")), str(row.get("provider", "")),
                    str(row.get("error", "")), str(row.get("error_en", "")),
                    str(row.get("account", "")),
                    str(row.get("request_id", "")), str(code),
                    " ".join(str(stage.get("payload", "")) for stage in row.get("stages") or []),
                    json.dumps(row.get("prompt"), ensure_ascii=False) if row.get("prompt") else "",
                ]).lower()
                if needle not in haystack:
                    return False
            return True

        filtered = [row for row in rows if matches(row)]
        total = len(filtered)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit or 100), 10000))
        return {"logs": filtered[offset:offset + limit], "total": total,
                "offset": offset, "limit": limit, "stored": len(rows)}

    def log_facets(self) -> Dict[str, List[str]]:
        with self._lock:
            providers = sorted({e.provider for e in self.logs if e.provider})
            models = sorted({e.model for e in self.logs if e.model})
        return {"providers": providers, "models": models[:200]}

    def log_prompt(self, request_id: str):
        if not _log_payloads_enabled():
            return None
        with self._lock:
            entry = next((item for item in self.logs if item.request_id == request_id), None)
            return entry.prompt if entry is not None else None

    def clear_logs(self) -> None:
        from .storage import _json_transaction
        try:
            with _json_transaction(LOGS_PATH):
                with self._lock:
                    self.logs.clear()
                if os.path.exists(LOGS_PATH):
                    os.remove(LOGS_PATH)
        except OSError:
            with self._lock:
                self.logs.clear()

    def _load_logs(self) -> None:
        from .storage import _json_transaction
        try:
            with _json_transaction(LOGS_PATH):
                if not os.path.exists(LOGS_PATH):
                    return
                rows = _read_log_tail(LOGS_PATH, row_limit=LOG_MEMORY_LIMIT,
                                      byte_limit=LOG_FILE_MAX_BYTES)
                with self._lock:
                    for raw in rows:
                        try:
                            self.logs.appendleft(LogEntry(**json.loads(raw.decode("utf-8"))))
                        except Exception:
                            continue
        except OSError:
            return

    def persist_log(self, entry: "LogEntry") -> None:
        """Дописывает завершённую запись в requests.jsonl (с ротацией)."""
        if not _log_persist_enabled():
            return
        from .storage import _json_transaction
        try:
            with _json_transaction(LOGS_PATH):
                os.makedirs(LOGS_DIR, exist_ok=True)
                if (os.path.exists(LOGS_PATH)
                        and os.path.getsize(LOGS_PATH) > LOG_FILE_MAX_BYTES):
                    self._rotate_logs()
                raw = json.dumps(entry.public_dict(), ensure_ascii=False).encode("utf-8") + b"\n"
                with open(LOGS_PATH, "a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell():
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            handle.write(b"\n")
                    handle.write(raw)
        except (OSError, TypeError, ValueError):
            pass

    def _rotate_logs(self) -> None:
        from .storage import _json_transaction
        tmp = None
        try:
            with _json_transaction(LOGS_PATH):

                tail = _read_log_tail(LOGS_PATH, row_limit=LOG_MEMORY_LIMIT // 2,
                                      byte_limit=LOG_FILE_MAX_BYTES // 2)
                tmp = LOGS_PATH + ".tmp-" + uuid.uuid4().hex
                with open(tmp, "wb") as handle:
                    handle.writelines(tail)
                os.replace(tmp, LOGS_PATH)
        except OSError:
            pass
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

state = State()
