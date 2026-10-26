# -*- coding: utf-8 -*-
"""Round 29: module bridge extensions first needed by the «Characters» module.

Everything here is GENERIC — any module with the right permission gets it, there
is no «if module == characters» branch (rule 1 of README_FOR_AI section 14):

* ``POST /api/pg/modules/complete`` — streaming chat completion with a FULL
  message array (system / user / assistant roles, any order) and sampler
  parameters passed through to the Proxy (temperature, top_p, top_k, min_p,
  top_a, frequency/presence/repetition penalties, max tokens, stop strings,
  seed, reasoning effort, prompt caching). Bridge: ``Memu.complete``. Action
  ``complete`` -> permission ``chat``. The stream also carries reasoning
  («thoughts») deltas and the provider's real token usage.
* ``POST /api/pg/modules/store`` — a file-based key/value store per module
  (``modules/store/<id>/<key>.json``): large values (chats, avatars as
  data-URLs, lorebooks) that do not fit the 32-key ``persist`` file.
  Bridge: ``Memu.store.get/set/remove/list/usage``. Action ``store`` ->
  permission ``storage``.
* ``POST /api/pg/modules/asset`` — read-only access to the files in the
  module's own package folder ``assets/`` (built-in data such as presets that
  are too big for ``main.js``). Bridge: ``Memu.asset(name)``. Free action
  ``asset``: a module can always read its own package.

No route gives a module the network: the completion goes to the local Proxy
only, the store and assets are confined to the module's own folders, and every
id/key that reaches a path is validated first.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import Request

STORE_VALUE_LIMIT = 40 * 1024 * 1024

STORE_TOTAL_LIMIT = 2 * 1024 * 1024 * 1024
STORE_KEYS_LIMIT = 20_000
ASSET_LIMIT = 8 * 1024 * 1024

_KEY_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
_KEY_RE = re.compile(rf"^{_KEY_SEGMENT}(?:/{_KEY_SEGMENT}){{0,2}}$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,120}){0,2}$")
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)

_ROLES = ("system", "user", "assistant")
_EFFORTS = ("auto", "off", "on", "minimal", "low", "medium", "high", "xhigh", "max")

def _module_root() -> Path:
    from .modules_host import default_root
    return default_root()

def _clean_id(raw: Any) -> str:
    from .modules_host import _safe_id
    return _safe_id(re.sub(r"[^a-z0-9-]", "", str(raw or "").lower())[:41])

def check_key(key: Any) -> str:
    """Validate a store key; ``..``, absolute paths, devices and odd characters are refused."""
    text = str(key or "").strip()
    if not _KEY_RE.match(text):
        raise ValueError("Недопустимый ключ хранилища / Invalid store key")
    for part in text.split("/"):
        if part in (".", "..") or part.endswith(".") or _RESERVED.match(part):
            raise ValueError("Недопустимый ключ хранилища / Invalid store key")
    return text

def store_dir(root: Path, module_id: str) -> Path:
    return Path(root) / "store" / _clean_id(module_id)

def _key_path(root: Path, module_id: str, key: str) -> Path:
    base = store_dir(root, module_id)
    parts = check_key(key).split("/")
    path = base.joinpath(*parts[:-1], parts[-1] + ".json")
    resolved_base = base.resolve()
    if resolved_base not in path.resolve().parents:
        raise ValueError("Недопустимый ключ хранилища / Invalid store key")
    return path

def _usage(base: Path) -> Dict[str, int]:
    total, count = 0, 0
    if base.is_dir():
        for path in base.rglob("*.json"):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                pass
    return {"bytes": total, "keys": count}

def store_get(root: Path, module_id: str, key: str, default: Any = None) -> Any:
    from .storage import load_json_with_recovery
    path = _key_path(root, module_id, key)
    if not path.is_file():
        return default
    return load_json_with_recovery(path, default)

def store_set(root: Path, module_id: str, key: str, value: Any) -> Dict[str, Any]:
    from .storage import save_json
    path = _key_path(root, module_id, key)
    encoded = json.dumps(value, ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    if size > STORE_VALUE_LIMIT:
        raise ValueError(f"Значение больше {STORE_VALUE_LIMIT // (1024 * 1024)} МБ / "
                         f"The value is larger than {STORE_VALUE_LIMIT // (1024 * 1024)} MB")
    base = store_dir(root, module_id)
    usage = _usage(base)
    previous = path.stat().st_size if path.is_file() else 0
    if usage["bytes"] - previous + size > STORE_TOTAL_LIMIT:
        raise ValueError("Хранилище модуля заполнено / The module store is full")
    if not path.is_file() and usage["keys"] >= STORE_KEYS_LIMIT:
        raise ValueError("Слишком много ключей в хранилище / Too many store keys")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, value, backup=False)
    return {"ok": True, "key": key, "bytes": size}

def store_remove(root: Path, module_id: str, key: str) -> Dict[str, Any]:
    from .storage import _json_transaction
    path = _key_path(root, module_id, key)
    removed = False
    with _json_transaction(str(path)):
        if path.is_file():
            path.unlink()
            removed = True
    lock = path.with_name(path.name + ".lock")
    try:
        if lock.is_file():
            lock.unlink()
    except OSError:
        pass

    base = store_dir(root, module_id).resolve()
    parent = path.parent
    while parent.resolve() != base and base in parent.resolve().parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return {"ok": True, "removed": removed}

def store_list(root: Path, module_id: str, prefix: str = "") -> List[Dict[str, Any]]:
    base = store_dir(root, module_id)
    if not base.is_dir():
        return []
    prefix = str(prefix or "")
    rows: List[Dict[str, Any]] = []
    for path in base.rglob("*.json"):
        try:
            rel = path.relative_to(base).with_suffix("").as_posix()
            if not _KEY_RE.match(rel) or not rel.startswith(prefix):
                continue
            stat = path.stat()
            rows.append({"key": rel, "bytes": stat.st_size, "modified": int(stat.st_mtime * 1000)})
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda row: row["key"])
    return rows[:STORE_KEYS_LIMIT]

def read_asset(module_id: str, name: str) -> Dict[str, Any]:
    """A file from ``assets/`` of the module's package (an edited copy first, then the shipped one)."""
    from .modules_host import BUILTIN_ORDER, _package_dir, builtin_dir
    clean = str(name or "").strip()
    if not _ASSET_RE.match(clean) or any(part in (".", "..") for part in clean.split("/")):
        raise ValueError("Недопустимое имя файла / Invalid asset name")
    module_id = _clean_id(module_id)
    folders: List[Path] = []
    package = _package_dir(_module_root(), module_id)
    if package is not None:
        folders.append(package)
    if module_id in BUILTIN_ORDER:
        folders.append(builtin_dir() / module_id)
    for folder in folders:
        base = (folder / "assets").resolve()
        path = (base / clean).resolve()
        if base not in path.parents or not path.is_file():
            continue
        if path.stat().st_size > ASSET_LIMIT:
            raise ValueError("Файл слишком большой / The asset is too large")
        text = path.read_text(encoding="utf-8")
        if clean.lower().endswith(".json"):
            return {"name": clean, "json": json.loads(text)}
        return {"name": clean, "text": text}
    raise KeyError(clean)

def _clean_content(content: Any) -> Any:
    if isinstance(content, str):
        return content[:2_000_000]
    if isinstance(content, list):
        parts: List[Dict[str, Any]] = []
        for part in content[:40]:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append({"type": "text", "text": str(part.get("text") or "")[:2_000_000]})
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url") or "")

                if url.startswith("data:image/") and len(url) < 20_000_000:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts
    return str(content or "")

def build_completion(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a module's completion request and turn it into the Proxy playground body."""
    if not isinstance(body, dict):
        raise ValueError("Ожидается JSON-объект / A JSON object is expected")
    model = str(body.get("model") or "").strip()[:300]
    if not model:
        raise ValueError("Выберите модель / Choose a model")
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Нужен непустой список messages / A non-empty messages list is required")
    if len(raw) > 2000:
        raise ValueError("Слишком много сообщений / Too many messages")
    messages: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in _ROLES:
            role = "system" if role in ("developer",) else "user"
        content = _clean_content(item.get("content"))
        if content in ("", []):
            continue
        row: Dict[str, Any] = {"role": role, "content": content}
        name = str(item.get("name") or "")
        if name and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            row["name"] = name
        messages.append(row)
    if not messages:
        raise ValueError("Все сообщения пустые / All messages are empty")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    out: Dict[str, Any] = {"model": model, "messages": messages,
                           "web_search": False, "tools_available": False,
                           "prompt_caching": bool(params.get("prompt_caching", True)),
                           "thinking": bool(params.get("thinking"))}

    def number(name: str, low: float, high: float, *, integer: bool = False) -> None:
        value = params.get(name)
        if value is None or value == "":
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if value != value:
            return
        value = max(low, min(high, value))
        out[name] = int(value) if integer else value

    number("temperature", 0.0, 2.0)
    number("top_p", 0.0, 1.0)
    number("max_tokens", 1, 1_000_000, integer=True)

    for name, low, high, neutral, integer in (
            ("top_k", 0, 1000, 0, True), ("min_p", 0.0, 1.0, 0.0, False), ("top_a", 0.0, 1.0, 0.0, False),
            ("frequency_penalty", -2.0, 2.0, 0.0, False), ("presence_penalty", -2.0, 2.0, 0.0, False),
            ("repetition_penalty", 0.0, 3.0, 1.0, False)):
        value = params.get(name)
        try:
            if value is None or float(value) == neutral:
                continue
        except (TypeError, ValueError):
            continue
        number(name, low, high, integer=integer)
    try:
        seed = int(params.get("seed")) if params.get("seed") not in (None, "") else -1
    except (TypeError, ValueError):
        seed = -1
    if seed >= 0:
        out["seed"] = seed
    stop = params.get("stop")
    if isinstance(stop, list):
        clean_stop = [str(item)[:200] for item in stop if str(item or "")][:16]
        if clean_stop:
            out["stop"] = clean_stop
    effort = str(params.get("effort") or "")
    if effort in _EFFORTS:
        out["effort"] = effort
    return out

def _parse_frame(raw: str) -> Optional[Dict[str, Any]]:
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None

def _search_units() -> Dict[str, Any]:
    """Characters of the «characters» module for the global search (one unit per character)."""
    from .search_index import Doc
    rows = store_get(_module_root(), "characters", "index", []) or []
    units: Dict[str, Any] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        cid = str(row["id"])[:80]
        updated = row.get("updated") or 0

        def build(row=row, cid=cid, updated=updated):
            tags = ", ".join(str(tag) for tag in (row.get("tags") or [])[:12])
            text = " ".join(part for part in (str(row.get("summary") or ""), tags, str(row.get("creator") or "")) if part)
            return [Doc("character", "character:" + cid, str(row.get("name") or cid)[:200], text[:4000],
                        ts=float(updated or 0), subtitle=tags[:200],
                        open={"action": "character", "id": cid, "module_id": "characters"})]
        units[cid] = (updated, build)
    return units

def register_search() -> bool:
    """Plug the module's characters into the global search registry (app/search_index.py)."""
    try:
        from .search_index import FunctionProvider, register_provider
    except Exception:
        return False

    def revision() -> Any:
        path = store_dir(_module_root(), "characters") / "index.json"
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    register_provider(FunctionProvider("characters", ("character",), revision=revision, units=_search_units,
                                       min_interval=5.0, labels={"character": ("Персонажи", "Characters")}))
    return True

def register(app, *, stream_lines: Callable[[str, bytes], Any]) -> None:
    from fastapi.responses import JSONResponse, StreamingResponse

    register_search()

    def allowed(module_id: str, action: str) -> bool:
        from .modules_host import ACTION_PERMISSION, load_package
        needed = ACTION_PERMISSION.get(action)
        if not needed:
            return True
        try:
            return needed in (load_package(_module_root(), module_id).get("permissions") or [])
        except (KeyError, ValueError):
            return False

    from . import character_import
    character_import.register(app, allowed=allowed)

    async def read_body(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise ValueError("Некорректный JSON / Invalid JSON")
        if not isinstance(body, dict):
            raise ValueError("Ожидается JSON-объект / A JSON object is expected")
        return body

    @app.post("/api/pg/modules/complete")
    async def modules_complete(request: Request):
        """Streaming completion with a full message array and sampler parameters."""
        try:
            body = await read_body(request)
            module_id = _clean_id(body.get("module_id") or body.get("id"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        if not allowed(module_id, "complete"):
            return JSONResponse({"error": "Модулю не разрешено обращаться к моделям / "
                                          "The module may not call models"}, 403)
        fallback = getattr(request.app.state, "module_fallback_model", None)
        if not str(body.get("model") or "").strip() and fallback is not None:
            body["model"] = await fallback()
        try:
            built = build_completion(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        payload = json.dumps(built, ensure_ascii=False).encode("utf-8")

        async def events():
            text: List[str] = []
            reasoning: List[str] = []
            usage: Dict[str, Any] = {}
            finish = ""
            failure = ""
            started = time.time()
            yield "data: " + json.dumps({"status": "queued"}) + "\n\n"
            try:
                async for line in stream_lines("/api/admin/playground/chat", payload):
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    item = _parse_frame(raw)
                    if item is None:
                        continue
                    if item.get("error"):
                        err = item["error"]
                        failure = str(err.get("message", err)) if isinstance(err, dict) else str(err)
                        break
                    if isinstance(item.get("usage"), dict):
                        usage = item["usage"]
                        yield "data: " + json.dumps({"event": "usage", "usage": usage}) + "\n\n"
                    choice = (item.get("choices") or [{}])[0] or {}
                    if choice.get("finish_reason"):
                        finish = str(choice.get("finish_reason"))
                    delta = choice.get("delta") or {}
                    thought = delta.get("reasoning_content") or delta.get("reasoning")
                    if thought:
                        reasoning.append(str(thought))
                        yield "data: " + json.dumps({"event": "reasoning", "delta": str(thought)},
                                                    ensure_ascii=False) + "\n\n"
                    piece = delta.get("content")
                    if piece:
                        text.append(str(piece))
                        yield "data: " + json.dumps({"event": "delta", "delta": str(piece)},
                                                    ensure_ascii=False) + "\n\n"
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
            if failure:
                yield "data: " + json.dumps({"event": "error", "error": failure, "text": "".join(text)},
                                            ensure_ascii=False) + "\n\n"
                return
            result = {"text": "".join(text), "reasoning": "".join(reasoning), "usage": usage,
                      "finish_reason": finish, "model": built["model"],
                      "ms": int((time.time() - started) * 1000)}
            yield "data: " + json.dumps({"event": "done", "result": result}, ensure_ascii=False) + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/api/pg/modules/store")
    async def modules_store(request: Request):
        """File-based key/value store of a module (Memu.store)."""
        try:
            body = await read_body(request)
            module_id = _clean_id(body.get("module_id") or body.get("id"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        if not allowed(module_id, "store"):
            return JSONResponse({"error": "Модулю не выдано право storage / "
                                          "The module lacks the storage permission"}, 403)
        op = str(body.get("op") or "get")
        root = _module_root()
        try:
            if op == "get":
                key = check_key(body.get("key"))
                value = await asyncio.to_thread(store_get, root, module_id, key, None)
                return {"key": key, "value": value, "exists": value is not None}
            if op == "get_many":
                keys = [check_key(key) for key in (body.get("keys") or [])[:500]]
                values = {}
                for key in keys:
                    values[key] = await asyncio.to_thread(store_get, root, module_id, key, None)
                return {"values": values}
            if op == "set":
                key = check_key(body.get("key"))
                return await asyncio.to_thread(store_set, root, module_id, key, body.get("value"))
            if op == "remove":
                key = check_key(body.get("key"))
                return await asyncio.to_thread(store_remove, root, module_id, key)
            if op == "list":
                prefix = str(body.get("prefix") or "")[:300]
                return {"keys": await asyncio.to_thread(store_list, root, module_id, prefix)}
            if op == "usage":
                usage = await asyncio.to_thread(_usage, store_dir(root, module_id))
                return {**usage, "limit": STORE_TOTAL_LIMIT, "value_limit": STORE_VALUE_LIMIT}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        except OSError as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return JSONResponse({"error": "Неизвестная операция / Unknown operation"}, 400)

    @app.post("/api/pg/modules/asset")
    async def modules_asset(request: Request):
        """A read-only file from the module's own package folder assets/ (Memu.asset)."""
        try:
            body = await read_body(request)
            module_id = _clean_id(body.get("module_id") or body.get("id"))
            return await asyncio.to_thread(read_asset, module_id, str(body.get("name") or ""))
        except KeyError:
            return JSONResponse({"error": "Файл не найден / Asset not found"}, 404)
        except (ValueError, UnicodeDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, 400)

__all__ = ["register", "build_completion", "check_key", "store_get", "store_set", "store_remove",
           "store_list", "read_asset", "STORE_VALUE_LIMIT", "STORE_TOTAL_LIMIT"]
