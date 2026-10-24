"""Round 35: admin routes of composite models (Proxy → «Составные модели»). Logic: :mod:`app.composite_models`.

``GET  /api/admin/composite``                 models, statistics, available models, presets, schema
``POST /api/admin/composite/save``            ``{"model": {...}, "original_id": "memu/old"}`` → ``{model, issues}`` or 400 + issues
``POST /api/admin/composite/validate``        ``{"model": {...}, "original_id"}`` → ``{model, issues}``
``POST /api/admin/composite/delete``          ``{"id"}``
``POST /api/admin/composite/toggle``          ``{"id", "enabled"}``
``POST /api/admin/composite/simulate``        ``{"model": {...}, "sample": {...}}`` → the decision (nothing is sent)
``POST /api/admin/composite/try``             ``{"id", "text", "image": bool}`` — one real short request through the composite
``GET  /api/admin/composite/export?id=a,b``   JSON file of the composites
``POST /api/admin/composite/import``          ``{"data": <export|list|model>, "mode": "merge|replace"}``
``POST /api/admin/composite/stats/reset``     ``{"id"}``

:func:`register` also installs :class:`app.composite_models.RouteContextMiddleware` (the ``x-memu-route`` header).
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import composite_models as cm

MAX_BODY = 4 * 1024 * 1024


def _err(message: str, message_en: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, "error_en": message_en, **extra}, status_code=status)


async def _body(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None
    try:
        import json
        data = json.loads(raw or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _auth(request: Request):
    from .admin_api import _auth as admin_auth
    return admin_auth(request)


def _tiny_png() -> str:
    """A small solid red PNG for «try with a picture» (the model is asked about its colour)."""
    from .model_capabilities import solid_png
    return base64.b64encode(solid_png((220, 30, 30), 64)).decode()


def register(app: FastAPI) -> None:
    app.add_middleware(cm.RouteContextMiddleware, expose=True)

    @app.get("/api/admin/composite")
    async def composite_list(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        models = cm.all_models()
        available = cm.available_models()
        out = []
        for model in models:
            row = dict(model)
            try:
                row["summary"] = cm.summary_caps(model)
            except Exception:
                row["summary"] = {}
            row["issues"] = cm.validate(model, original_id=model["id"])
            out.append(row)
        return {"models": out, "stats": cm.stats.get(), "available": available, "presets": cm.presets(available),
                "schema": cm.SCHEMA}

    @app.post("/api/admin/composite/validate")
    async def composite_validate(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request)
        if body is None or not isinstance(body.get("model"), dict):
            return _err("Нужен объект model", "A model object is required")
        model = cm.normalize(body["model"])
        return {"model": model, "issues": cm.validate(model, original_id=str(body.get("original_id") or ""))}

    @app.post("/api/admin/composite/save")
    async def composite_save(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request)
        if body is None or not isinstance(body.get("model"), dict):
            return _err("Нужен объект model", "A model object is required")
        original = str(body.get("original_id") or "").strip().lower()
        model = cm.normalize(body["model"])
        issues = cm.validate(model, original_id=original)
        if any(i["level"] == "error" for i in issues):
            first = next(i for i in issues if i["level"] == "error")
            return _err(first["message"], first["message_en"], 400, issues=issues, model=model)
        previous = cm.get(original) if original else None
        model["created"] = int((previous or {}).get("created") or time.time())
        model["updated"] = int(time.time())
        cm.store.upsert(model, original_id=original)
        if original and original != model["id"]:
            cm.stats.rename(original, model["id"])
        return {"model": model, "issues": issues}

    @app.post("/api/admin/composite/delete")
    async def composite_delete(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request) or {}
        model_id = str(body.get("id") or "").strip().lower()
        used_by = [m["id"] for m in cm.all_models() if m["id"] != model_id
                   and model_id in [c.lower() for c in cm._all_candidate_ids_shallow(m)]]
        if used_by and not body.get("force"):
            names = ", ".join(used_by)
            return _err(f"Эту модель используют другие составные модели: {names}. Сначала уберите её оттуда.",
                        f"Other composite models use this model: {names}. Remove it from them first.", 409,
                        used_by=used_by)
        if not cm.store.delete(model_id):
            return _err("Составная модель не найдена", "Composite model not found", 404)
        cm.stats.reset(model_id)
        return {"ok": True}

    @app.post("/api/admin/composite/toggle")
    async def composite_toggle(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request) or {}
        model = cm.get(str(body.get("id") or ""))
        if model is None:
            return _err("Составная модель не найдена", "Composite model not found", 404)
        model = dict(model, enabled=bool(body.get("enabled")), updated=int(time.time()))
        cm.store.upsert(model, original_id=model["id"])
        return {"model": model}

    @app.post("/api/admin/composite/simulate")
    async def composite_simulate(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request)
        if body is None or not isinstance(body.get("model"), dict):
            return _err("Нужен объект model", "A model object is required")
        sample = body.get("sample") if isinstance(body.get("sample"), dict) else {}
        sample = dict(sample, original_id=str(body.get("original_id") or ""))
        return cm.simulate(body["model"], sample)

    @app.post("/api/admin/composite/try")
    async def composite_try(request: Request):
        """One real request through a saved composite: the answer, the route and the time."""
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request) or {}
        model_id = str(body.get("id") or "").strip().lower()
        if not cm.is_composite(model_id):
            return _err("Сначала сохраните и включите модель", "Save and enable the model first", 404)
        text = str(body.get("text") or "Ответь одним словом: привет").strip()[:4000]
        content: Any = text
        if body.get("image"):
            content = [{"type": "text", "text": text},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _tiny_png()}}]
        messages = [{"role": "user", "content": content}]
        route: Dict[str, Any] = {}
        params: Dict[str, Any] = {"_stream": bool(body.get("stream", True)), "max_tokens": 1024,
                                  "_composite_route_sink": route}
        from .chat import CompletionError, chat_service
        from .state import state
        trace = state.open_trace(model=model_id)
        started = time.time()
        parts = []
        error = None
        try:
            async for chunk in chat_service.stream_completion(model_id, messages, params, trace=trace):
                if chunk.get("object") == "memuai.event":
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        parts.append(delta["content"])
            trace.status = 200
        except CompletionError as exc:
            error = {"message": exc.message, "message_en": exc.message_en, "status": exc.status}
            trace.status, trace.error = exc.status, exc.message
        finally:
            trace.ms = int((time.time() - started) * 1000)
            state.commit_trace(trace)
        return {"ok": error is None, "answer": "".join(parts)[:4000], "error": error, "route": route,
                "ms": int((time.time() - started) * 1000)}

    @app.get("/api/admin/composite/export")
    async def composite_export(request: Request, id: str = ""):
        deny = _auth(request)
        if deny:
            return deny
        wanted = [x.strip() for x in id.split(",") if x.strip()]
        data = cm.export_json(wanted or None)
        name = (wanted[0].replace("/", "-") if len(wanted) == 1 else "composite-models") + ".json"
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/admin/composite/import")
    async def composite_import(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request)
        if body is None:
            return _err("Файл не похож на JSON", "The file is not JSON")
        result = cm.import_json(body.get("data"), "replace" if body.get("mode") == "replace" else "merge")
        if result["errors"]:
            first = result["errors"][0]
            return _err(first["message"], first["message_en"], 400, **result)
        return result

    @app.post("/api/admin/composite/stats/reset")
    async def composite_stats_reset(request: Request):
        deny = _auth(request)
        if deny:
            return deny
        body = await _body(request) or {}
        cm.stats.reset(str(body.get("id") or "").strip().lower())
        return {"ok": True}
