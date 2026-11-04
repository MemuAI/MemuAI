"""Эмбеддинги через MemuAI Proxy (OpenAI-совместимый ``POST /embeddings``).

Proxy не хранит отдельного каталога эмбеддинг-моделей: модель берётся из собственного
подключения (``custom``) — OpenAI, OpenRouter, NVIDIA, Gemini (OpenAI-режим), llama.cpp,
Ollama и т.п. Ключ никогда не покидает Proxy: окно MemuAI вызывает
``/api/admin/playground/embeddings`` (авторизация панели), внешние клиенты — ``/v1/embeddings``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

MAX_INPUTS = 256
MAX_CHARS = 32_000


# F26: an upstream embedding wider than any real model is malformed, not
# a reason to allocate unbounded memory.
MAX_EMBEDDING_DIMS = 16384
MAX_USER_CHARS = 256


def _clean_inputs(raw: Any) -> tuple:
    """Returns ``(inputs, truncated)`` — truncation is surfaced to the caller
    instead of silently dropping inputs past the cap (old behaviour)."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("'input' must be a string or a non-empty array of strings")
    out = []
    truncated = max(0, len(raw) - MAX_INPUTS)
    for item in raw[:MAX_INPUTS]:
        if not isinstance(item, str):
            raise ValueError("'input' items must be strings")
        if len(item) > MAX_CHARS:
            truncated += 1
        out.append(item[:MAX_CHARS] or " ")
    return out, truncated


def _account_for(model_id: str):
    from .pool import pool
    from .routing import resolve
    routed = resolve(model_id)
    if routed is None:
        return None, None, f"Модель '{model_id}' не найдена / Model not found"
    if routed.provider != "custom":
        return None, None, ("Эмбеддинги доступны для собственных OpenAI-совместимых подключений "
                            "(Proxy → Аккаунты → Своё API) / Embeddings need a custom OpenAI-compatible connection")
    accounts = pool.ordered_candidates("custom")
    if routed.entry and routed.entry.account_id:
        accounts = [a for a in accounts if a.id == routed.entry.account_id]
    else:
        accounts = [a for a in accounts
                    if str(a.fields.get("name", "")).strip().lower().replace(" ", "-") == routed.prefix]
    if not accounts:
        return None, None, "Нет рабочего подключения для этой модели / No working connection for this model"
    return accounts[0], routed.model, ""


async def forward(body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Returns ``(status, openai_style_payload)``."""
    model_id = str(body.get("model") or "").strip()
    if not model_id or len(model_id) > 512:
        return 400, {"error": {"message": "'model' is required", "type": "invalid_request_error"}}
    try:
        inputs, truncated_inputs = _clean_inputs(body.get("input"))
    except ValueError as exc:
        return 400, {"error": {"message": str(exc), "type": "invalid_request_error"}}
    account, upstream_model, problem = _account_for(model_id)
    if account is None:
        return 400, {"error": {"message": problem, "type": "invalid_request_error"}}
    from .config import config_store
    from .providers import get as get_provider
    from .providers import http
    provider = get_provider("custom")
    payload: Dict[str, Any] = {"model": upstream_model, "input": inputs}
    # F26: `dimensions`/`user` used to be copied verbatim into the upstream
    # payload (a string, a negative or 10**9 dimensions, megabytes of `user`).
    dimensions = body.get("dimensions")
    if dimensions is not None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 4096:
            return 400, {"error": {"message": "'dimensions' must be an integer between 1 and 4096",
                                   "type": "invalid_request_error"}}
        payload["dimensions"] = dimensions
    if body.get("encoding_format") in ("float", "base64"):
        payload["encoding_format"] = body["encoding_format"]
    user = body.get("user")
    if user is not None:
        if not isinstance(user, str):
            return 400, {"error": {"message": "'user' must be a string", "type": "invalid_request_error"}}
        payload["user"] = user[:MAX_USER_CHARS]
    base = provider.base_of(account)
    try:
        session = await http.get_session(provider.proxy_for(account), impersonate=not base.startswith("http://"),
                                         url=base)
        resp = await session.post(f"{base}/embeddings", headers=provider._headers(account), json=payload,
                                  timeout=config_store.settings.request_timeout)
    except Exception as exc:
        return 502, {"error": {"message": f"Network error: {type(exc).__name__}: {exc}"[:400], "type": "api_error"}}
    try:
        data = resp.json()
    except Exception:
        data = None
    if resp.status_code != 200 or not isinstance(data, dict):
        message = ""
        if isinstance(data, dict):
            err = data.get("error")
            message = str(err.get("message") if isinstance(err, dict) else err or "")
        message = message or str(getattr(resp, "text", "") or "")[:300] or f"HTTP {resp.status_code}"
        return (resp.status_code if 400 <= resp.status_code < 600 else 502), {
            "error": {"message": message[:400], "type": "api_error"}}
    rows = []
    try:
        for index, item in enumerate(data.get("data") or []):
            if isinstance(item, dict) and isinstance(item.get("embedding"), list):
                vector = item["embedding"]
                if len(vector) > MAX_EMBEDDING_DIMS:
                    raise ValueError("embedding too long")
                rows.append({"object": "embedding", "index": int(item.get("index", index)),
                             "embedding": [float(x) for x in vector]})
    except (ValueError, TypeError):
        # F26: a malformed upstream embedding (non-numeric value, non-int
        # index) is a 502, not an unhandled 500.
        return 502, {"error": {"message": "Upstream returned malformed embeddings", "type": "api_error"}}
    if not rows:
        return 502, {"error": {"message": "The API returned no embeddings", "type": "api_error"}}
    out: Dict[str, Any] = {"object": "list", "data": rows, "model": model_id,
                           "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {}}
    if truncated_inputs:
        out["truncated_inputs"] = truncated_inputs
    return 200, out
