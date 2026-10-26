"""MemuNote ↔ MemuAI Proxy: discovery, models, streaming chat, web search, memory.

Standard library only (``http.client``): MemuNote never imports httpx, FastAPI
or the Proxy itself, so it starts fast and works when no Proxy is installed —
then :func:`status` says ``online: False`` and the AI features are disabled.

Discovery is the same contract as MemuCode CLI and MemuAI: the runtime file
``%APPDATA%\\MemuAI\\memuai\\proxy.json`` (only ``http://127.0.0.1|localhost``
URLs are trusted), falling back to ``http://127.0.0.1:8317``; liveness is
``GET /health`` → ``{"service": "memuai-proxy"}``.

Chat goes to ``POST /api/admin/playground/chat`` (the bridge MemuAI and
MemuCode use: every model of the user's accounts, ``web_search`` handled by the
Proxy itself and reported as ``memuai.event`` items). If that route is missing
(an old or foreign Proxy) the OpenAI-compatible ``/v1/chat/completions`` is
used. Every request carries ``X-MemuAI-Service: memunote``.

Memory: the "note" service of MemuAI memory v2 (``app.memory_v2``): ranked
facts for the request go in as an untrusted ``<profile_context>`` message, the
memory protocol lets the model propose new facts (scoped to MemuNote when they
only matter here), markers are filtered out of the stream and stored at the end.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, Iterator, List, Optional

from . import SERVICE
from .paths import data_root, memuai_preferences

log = logging.getLogger("memunote.ai")

DEFAULT_URL = "http://127.0.0.1:8317"
MEMORY_SERVICE = "note"
_STATUS_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_LOCK = threading.Lock()

class AIError(Exception):
    def __init__(self, message: str, kind: str = "error", status: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

def _valid_local(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(url or "").strip())
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme == "http" and (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
            and port and 1 <= port <= 65535 and not parsed.username and parsed.path in ("", "/")
            and not parsed.query and not parsed.fragment):
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"http://{host}:{port}"
    return ""

def discover(configured: str = "") -> str:
    """The Proxy base URL: an explicit setting, then ``proxy.json``, then the default port."""
    url = _valid_local(configured) or _valid_local(os.environ.get("MEMUAI_PROXY_URL", ""))
    if url:
        return url
    try:
        data = json.loads((data_root() / "memuai" / "proxy.json").read_text(encoding="utf-8"))
        url = _valid_local(str(data.get("url") or ""))
    except (OSError, ValueError, TypeError):
        url = ""
    return url or DEFAULT_URL

def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    lang = "en" if memuai_preferences().get("language") == "en" else "ru"
    headers = {"Content-Type": "application/json", "X-MemuAI-Service": SERVICE, "X-MemuAI-Lang": lang,
               "User-Agent": "MemuNote"}
    token = os.environ.get("MEMUAI_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    if extra:
        headers.update(extra)
    return headers

def _conn(base: str, timeout: float) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlsplit(base)
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)

def request_json(base: str, method: str, path: str, body: Any = None, timeout: float = 20) -> Any:
    conn = _conn(base, timeout)
    try:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        conn.request(method, path, body=payload, headers=_headers())
        response = conn.getresponse()
        raw = response.read()
        if response.status >= 400:
            message = ""
            try:
                data = json.loads(raw or b"{}")
                message = data.get("error") if isinstance(data, dict) else ""
                if isinstance(message, dict):
                    message = message.get("message") or json.dumps(message, ensure_ascii=False)
            except ValueError:
                message = raw[:300].decode("utf-8", "replace")
            raise AIError(str(message or f"HTTP {response.status}"), "http", response.status)
        return json.loads(raw or b"null")
    except (OSError, http.client.HTTPException) as exc:
        raise AIError(f"MemuAI Proxy: {type(exc).__name__}", "offline") from exc
    finally:
        conn.close()

def health(base: str) -> bool:
    try:
        data = request_json(base, "GET", "/health", timeout=1.5)
        return isinstance(data, dict) and data.get("service") == "memuai-proxy"
    except AIError:
        return False

def _pretty(model_id: str) -> str:
    """Round 33 (r33c-models-lists): the readable model name of app/model_names.py (falls back to the id)."""
    try:
        from ..model_names import pretty
        return pretty(model_id) or model_id
    except Exception:
        return model_id

def _chat_model(row: Dict[str, Any]) -> bool:
    caps = row.get("caps") or row.get("capabilities") or {}
    from ..model_contract import kind_for_row
    if kind_for_row({**row, "caps": caps if isinstance(caps, dict) else {}}) != "chat":
        return False
    if not isinstance(caps, dict):
        return True
    if caps.get("chat") is False or caps.get("text") is False:
        return False
    if caps.get("chat") is True or caps.get("text") is True:
        return True
    return not any(caps.get(k) for k in ("image", "video", "music", "tts", "stt", "transcription"))

def models(base: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    catalog_available = False
    try:
        data = request_json(base, "GET", "/api/admin/pg/state", timeout=15)
        catalog_available = isinstance(data, dict) and isinstance(data.get("models"), list)
        for row in (data.get("models") or []) if isinstance(data, dict) else []:
            if not isinstance(row, dict) or not row.get("id") or not _chat_model(row):
                continue
            caps = row.get("caps") if isinstance(row.get("caps"), dict) else {}
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            try:
                window = max(0, int(context.get("context") or 0))
            except (TypeError, ValueError, OverflowError):
                window = 0

            rows.append({"id": str(row["id"]), "label": str(row.get("name") or row.get("label") or _pretty(str(row["id"]))),
                         "family": str(context.get("label") or ""), "kind": "chat",
                         "provider": str(row.get("provider") or ""), "search": bool(caps.get("search")),
                         "vision": bool(caps.get("vision")), "thinking": bool(caps.get("thinking")),
                         "image": bool(caps.get("image")), "context": window})
    except AIError as exc:
        if exc.kind == "offline":
            raise
    if not catalog_available:
        data = request_json(base, "GET", "/v1/models", timeout=15)
        for row in (data or {}).get("data") or []:
            if isinstance(row, dict) and row.get("id") and _chat_model(row):
                rows.append({"id": str(row["id"]), "label": _pretty(str(row["id"])), "provider": str(row.get("owned_by") or ""), "kind": "chat"})
    unique = {}
    for row in rows:
        unique.setdefault(row["id"], row)
    return list(unique.values())

def status(configured: str = "", force: bool = False) -> Dict[str, Any]:
    """``{online, url, models, default_model}`` — cached for 20 s (the status bar polls it)."""
    with _LOCK:
        cached = _STATUS_CACHE["value"]

        if (cached is not None and not force and time.time() - _STATUS_CACHE["at"] < 20
                and _STATUS_CACHE.get("configured", "") == (configured or "")):
            return cached
    base = discover(configured)
    value: Dict[str, Any] = {"online": False, "url": base, "models": [], "default_model": "", "error": ""}
    if health(base):
        value["online"] = True
        try:
            value["models"] = models(base)
        except AIError as exc:
            value["error"] = str(exc)
        prefs = memuai_preferences()
        default = str(prefs.get("default_model") or "")
        ids = [m["id"] for m in value["models"]]
        value["default_model"] = default if default in ids else (ids[0] if ids else "")
    with _LOCK:
        _STATUS_CACHE.update(at=time.time(), value=value, configured=configured or "")
    return value

def _memory():
    try:
        from .. import memory_v2      # type: ignore
        return memory_v2
    except Exception:  # noqa: BLE001 - memory is optional (older bundles, broken file)
        return None

def memory_context(query: str, allow_write: bool) -> Dict[str, Any]:
    mem = _memory()
    if mem is None:
        return {"block": "", "protocol": "", "facts": 0}
    try:
        facts = mem.context_facts(MEMORY_SERVICE, query[:4000])
        prefs = memuai_preferences()
        block = mem.profile_context(MEMORY_SERVICE, query[:4000], name=str(prefs.get("user_name") or ""),
                                    facts=facts)
        protocol = mem.protocol_prompt(MEMORY_SERVICE) if allow_write else ""
        return {"block": block, "protocol": protocol, "facts": len(facts)}
    except Exception as exc:  # noqa: BLE001
        log.warning("memory context failed: %s", exc)
        return {"block": "", "protocol": "", "facts": 0}

def memory_store(raw_answer: str, excerpt: str, title: str) -> Dict[str, Any]:
    mem = _memory()
    if mem is None:
        return {}
    try:
        _clean, result = mem.process_answer(raw_answer, service=MEMORY_SERVICE,
                                            source={"chat_title": title[:120], "excerpt": excerpt[:300],
                                                    "message_at": int(time.time() * 1000)})
        return {k: (len(v) if isinstance(v, list) else v) for k, v in (result or {}).items()
                if k in ("added", "pending", "updated", "blocked")}
    except Exception as exc:  # noqa: BLE001
        log.warning("memory store failed: %s", exc)
        return {}

def memory_filter():
    mem = _memory()
    if mem is None:
        return None
    try:
        return mem.StreamFilter()
    except Exception:  # noqa: BLE001
        return None

def _sse_lines(response) -> Iterator[str]:
    buffer = b""
    while True:
        chunk = response.read1(65536) if hasattr(response, "read1") else response.read(4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8", "replace")
    if buffer:
        yield buffer.decode("utf-8", "replace")

class ThinkFilter:
    """Hide ``<think>…</think>`` blocks some providers put inline (reported as reasoning instead)."""

    def __init__(self) -> None:
        self.inside = False
        self.pending = ""

    def feed(self, text: str):
        data = self.pending + text
        self.pending = ""
        out = []
        while data:
            tag = "</think>" if self.inside else "<think>"
            index = data.find(tag)
            if index < 0:
                keep = 0
                for size in range(min(len(tag) - 1, len(data)), 0, -1):
                    if tag.startswith(data[-size:]):
                        keep = size
                        break
                chunk, self.pending = (data[:-keep], data[-keep:]) if keep else (data, "")
                out.append(("reasoning" if self.inside else "delta", chunk))
                break
            out.append(("reasoning" if self.inside else "delta", data[:index]))
            data = data[index + len(tag):]
            self.inside = not self.inside
        return [(k, t) for k, t in out if t]

EFFORTS = ("auto", "off", "on", "minimal", "low", "medium", "high", "xhigh", "max")

def stream_chat(base: str, body: Dict[str, Any], cancel: threading.Event,
                on_conn: Optional[Callable[[Any], None]] = None) -> Iterator[Dict[str, Any]]:
    """Yield ``{"t": "delta"|"reasoning"|"search"|"usage"|"done", ...}``; raise :class:`AIError`."""
    payload = {"model": body["model"], "messages": body["messages"], "web_search": bool(body.get("web_search")),
               "thinking": bool(body.get("thinking")), "prompt_caching": True,
               "tools_available": bool(body.get("tools_available"))}
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("max_tokens"):
        payload["max_tokens"] = int(body["max_tokens"])

    if str(body.get("effort") or "").lower() in EFFORTS:
        payload["effort"] = str(body["effort"]).lower()
    for path, openai in (("/api/admin/playground/chat", False), ("/v1/chat/completions", True)):
        if cancel.is_set():
            return
        conn = _conn(base, 900)
        if on_conn:
            on_conn(conn)
        try:
            if cancel.is_set():
                return
            data = dict(payload)
            if openai:
                data = {"model": payload["model"], "messages": payload["messages"], "stream": True}
                if "temperature" in payload:
                    data["temperature"] = payload["temperature"]
                if "max_tokens" in payload:
                    data["max_tokens"] = payload["max_tokens"]
            try:
                conn.request("POST", path, body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                             headers=_headers({"Accept": "text/event-stream", **(
                                 {"X-MemuAI-Service": "memufiles", "User-Agent": "MemuFiles"}
                                 if body.get("service") == "memufiles" else {})}))
                response = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                raise AIError("MemuAI Proxy is not reachable", "offline") from exc
            if response.status == 404 and not openai:
                response.read()
                continue
            if response.status != 200:
                raw = response.read()[:4000].decode("utf-8", "replace")
                message = raw
                try:
                    parsed = json.loads(raw)
                    message = parsed.get("error", parsed) if isinstance(parsed, dict) else parsed
                    if isinstance(message, dict):
                        message = message.get("message") or json.dumps(message, ensure_ascii=False)
                except ValueError:
                    pass
                raise AIError(str(message)[:600] or f"HTTP {response.status}", "http", response.status)
            think = ThinkFilter()
            for line in _sse_lines(response):
                if cancel.is_set():
                    return
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("error"):
                    error = item["error"]
                    if isinstance(error, dict):
                        lang_en = memuai_preferences().get("language") == "en"
                        message = (error.get("message_en") if lang_en else None) or error.get("message") or str(error)
                    else:
                        message = str(error)
                    raise AIError(str(message)[:600], "provider")
                if item.get("object") == "memuai.event":
                    yield {"t": "search", "data": item.get("web_search") or {}}
                    continue
                choices = item.get("choices") if isinstance(item.get("choices"), list) else []
                choice = choices[0] if choices else {}
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield {"t": "reasoning", "text": str(delta["reasoning_content"])}
                if delta.get("content"):
                    for kind, text in think.feed(str(delta["content"])):
                        yield {"t": kind, "text": text}
                if isinstance(item.get("usage"), dict):
                    yield {"t": "usage", "usage": item["usage"]}
                if choice.get("finish_reason"):
                    yield {"t": "finish", "reason": str(choice["finish_reason"])}

            if think.pending and not think.inside:
                yield {"t": "delta", "text": think.pending}
            return
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    raise AIError("MemuAI Proxy has no chat endpoint", "http", 404)

def websearch(base: str, query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Web search through the Proxy (the engine chosen in MemuAI settings); local fallback."""
    try:
        data = request_json(base, "POST", "/api/admin/websearch", {"query": query[:500], "limit": limit}, timeout=60)
        rows = data.get("results") if isinstance(data, dict) else None
        if isinstance(rows, list):
            return [{"title": str(r.get("title") or "")[:300], "url": str(r.get("url") or r.get("link") or ""),
                     "snippet": str(r.get("snippet") or r.get("content") or r.get("body") or "")[:1200]}
                    for r in rows if isinstance(r, dict)]
    except AIError as exc:
        if exc.kind != "offline" and exc.status not in (404, 405):
            raise
    try:
        from .. import websearch as local            # type: ignore
        search = getattr(local, "search", None) or getattr(local, "web_search", None)
        if search is None:
            raise AIError("web search is unavailable", "offline")
        result = search(query, limit) if not _is_coroutine(search) else _run_async(search(query, limit))
        rows = result.get("results") if isinstance(result, dict) else result
        return [{"title": str(r.get("title") or ""), "url": str(r.get("url") or r.get("href") or ""),
                 "snippet": str(r.get("snippet") or r.get("body") or "")} for r in rows or [] if isinstance(r, dict)]
    except AIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIError(f"web search failed: {type(exc).__name__}", "search") from exc

def _is_coroutine(func) -> bool:
    import inspect
    return inspect.iscoroutinefunction(func)

def _run_async(coro):
    import asyncio
    return asyncio.run(coro)
