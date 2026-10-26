"""Round 40: admin routes of web models (Proxy → «Каталог моделей» → model card → «Веб-версия»).

``GET  /api/admin/web-models``          web models, search engines, defaults
``POST /api/admin/web-models/save``     ``{"base": "<model id>", "settings": {...}}`` → ``{model}`` (creates or updates)
``POST /api/admin/web-models/delete``   ``{"id": "<model>:web"}``
``POST /api/admin/web-models/try``      ``{"id", "text"}`` — one real request through the web model → text, sources, ms
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import web_models as wm

def _err(message: str, message_en: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "error_en": message_en}, status_code=status)

async def _json(request: Request) -> Any:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None

def _row(row: dict) -> dict:
    return {**row, "native_tools": wm.native_tools(row["base"]), "base_exists": wm.base_exists(row["base"])}

def register(app: FastAPI) -> None:
    from .admin_api import _auth

    @app.get("/api/admin/web-models")
    async def web_models_list(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        from . import search_engines, websearch
        engine, _key, _url = websearch.configured()
        return {"models": [_row(r) for r in wm.store.all()], "defaults": wm.DEFAULTS,
                "engines": [{"id": e["id"], "label": e["label"], "label_en": e["label_en"], "group": e["group"]}
                            for e in search_engines.catalog()],
                "proxy_engine": engine, "tools": list(wm.EXTRA_TOOLS)}

    @app.post("/api/admin/web-models/save")
    async def web_models_save(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        data = await _json(request)
        if data is None:
            return _err("Неверный запрос.", "Bad request.")
        base = str(data.get("base") or "").strip()
        if not base or base.endswith(wm.SUFFIX):
            return _err("Укажите обычную модель каталога.", "Choose a regular catalogue model.")
        if not wm.base_exists(base):
            return _err(f"Модели «{base}» нет в каталоге Proxy или она выключена.",
                        f"Model “{base}” is not in the Proxy catalogue or is switched off.", 404)
        return {"model": _row(wm.store.upsert(base, data.get("settings")))}

    @app.post("/api/admin/web-models/delete")
    async def web_models_delete(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        data = await _json(request) or {}
        if not wm.store.delete(str(data.get("id") or "")):
            return _err("Такой веб-модели нет.", "No such web model.", 404)
        return {"ok": True}

    @app.post("/api/admin/web-models/try")
    async def web_models_try(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        data = await _json(request) or {}
        model_id = str(data.get("id") or "")
        if wm.store.get(model_id) is None:
            return _err("Такой веб-модели нет.", "No such web model.", 404)
        text = str(data.get("text") or "").strip()[:2000] or "What is the latest stable Python version? Answer briefly."
        from .chat import ChatService, CompletionError
        started = time.time()
        answer, reasoning, citations = "", "", []
        try:
            async for chunk in ChatService().stream_completion(model_id, [{"role": "user", "content": text}],
                                                               {"_stream": True, "max_tokens": 800}):
                if not isinstance(chunk, dict) or chunk.get("object") == "memuai.event":
                    continue
                delta = ((chunk.get("choices") or [{}])[0] or {}).get("delta") or {}
                answer += str(delta.get("content") or "")
                reasoning += str(delta.get("reasoning_content") or "")
                citations = chunk.get("citations") or citations
        except CompletionError as exc:
            return _err(exc.message, exc.message_en, 502)
        return {"text": answer, "reasoning": reasoning[-4000:], "citations": citations,
                "ms": int((time.time() - started) * 1000)}
