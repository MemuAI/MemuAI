"""Round 31 (r31-quick): models in the Quick Access window — «does this model see pictures?» on demand.

The window refused a picture / a screenshot / Computer Use when ``caps.vision`` was ``false``, and that value is often a
guess from the model's name (``caps.vision_guess``): a new multimodal tier the name table does not know yet. Instead of a
dead end the window now offers «Проверить зрение» — one real request with a tiny picture through Proxy's own probe
(``POST /api/admin/models/{id}/probe {"confirm": true}``, the same as the button on the model card in Proxy). The result is
stored by Proxy and from then on decides ``caps.vision`` for every client (MemuAI, Quick Access, DFY).

    POST /api/quick/vision/probe   {model}  →  {ok, vision: true|false|null, model, error?}

With the user's own endpoint (Quick Access → «Свой эндпоинт») the probe is a one-off request to that endpoint (nothing
is stored there; the window's «Модель видит картинки» setting is the persistent answer).
"""
from __future__ import annotations

import base64
import logging
import random
import re
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from fastapi import Request

log = logging.getLogger("memuai")

async def endpoint_probe(url: str, key: str, model: str, *, lang: str = "ru",
                         complete: Optional[Callable[..., Awaitable[str]]] = None) -> Dict[str, Any]:
    """The same picture test as Proxy's probe (``model_capabilities.probe_vision``), sent to the user's own endpoint."""
    from . import quick_ask_endpoint as endpoint
    from .model_capabilities import PROBE_COLORS, judge_probe, solid_png
    color = random.choice(list(PROBE_COLORS))
    url_img = "data:image/png;base64," + base64.b64encode(solid_png(PROBE_COLORS[color][0])).decode("ascii")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "What single colour fills this image? Answer with one English word only."},
        {"type": "image_url", "image_url": {"url": url_img}}]}]
    try:
        answer = await (complete or endpoint.complete)(url, key, model, messages, max_tokens=64, lang=lang)
    except Exception as exc:  # noqa: BLE001 - an endpoint that rejects images is an answer too
        text = str(exc)
        if re.search(r"image|vision|multimodal|content.?type|картин|изображ", text, re.I):
            return {"ok": True, "vision": False, "model": model, "error": text[:300]}
        return {"ok": False, "vision": None, "model": model, "error": text[:600]}
    vision, verdict = judge_probe(str(answer or ""), color)
    return {"ok": True, "vision": vision, "verdict": verdict, "model": model, "answer": str(answer or "")[:200]}

def register(app, *, proxy_json: Callable[..., Awaitable[Tuple[int, Any]]]) -> None:
    from fastapi.responses import JSONResponse
    import json

    @app.post("/api/quick/vision/probe")
    async def quick_vision_probe(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        model = str(body.get("model") or "").strip()[:300]
        lang = "en" if body.get("lang") == "en" else "ru"
        if not model:
            return JSONResponse({"error": "No model" if lang == "en" else "Не указана модель"}, 400)
        from .pgdata import load_preferences
        from . import quick_ask_endpoint as endpoint
        prefs = load_preferences()
        if endpoint.active(prefs):
            return await endpoint_probe(endpoint.normalize_url(prefs.get("quick_ask_endpoint_url")), endpoint.get_key(),
                                        model, lang=lang)
        status, data = await proxy_json("POST", "/api/admin/models/" + quote(model, safe="") + "/probe",
                                        json.dumps({"confirm": True}).encode("utf-8"))
        data = data if isinstance(data, dict) else {}
        if status != 200:
            error = str(data.get("error_en" if lang == "en" else "error") or data.get("error") or data.get("detail") or f"HTTP {status}")
            return JSONResponse({"ok": False, "vision": None, "model": model, "error": error[:600]}, 200)
        vision = data.get("vision")
        return {"ok": True, "vision": vision if isinstance(vision, bool) else None, "model": model,
                "verdict": str(data.get("verdict") or ""), "answer": str(data.get("answer") or "")[:200],
                "error": str(data.get("error") or "")[:400]}
