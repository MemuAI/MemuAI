"""Round 33 (r33-models): the unified model catalogue for every MemuAI window.

    GET  /api/pg/model-catalog?kind=&need=&all=1
         kind: "" (everything) | chat | text (chat, no generators) | image | video | music; need=vision → no «no»-rows;
         all=1 keeps the models the user hid in the picker. Rows are Proxy's ``/pg/state`` rows (only models enabled
         in MemuAI Proxy) enriched by ``app/model_catalog.py`` plus the user's view of them:
         ``alias`` (own name), ``display`` (alias or the automatic name), ``hidden``, ``pinned``, ``default``.
    POST /api/pg/model-catalog/vision  {model, action: probe|set|forget, value: true|false}
         probe — one real request with a tiny picture (Proxy's probe); set — the user's own answer (Proxy's model
         override, the same as on the model card in Proxy); forget — back to automatic.

``pgstatic/model-picker.js`` (``window.MemuModelPicker``) uses ``S.models`` in the main window and this route anywhere
else (modules, Quick Access, the Mini App).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Tuple
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse

from . import model_catalog


def _prefs() -> Dict[str, Any]:
    try:
        from .pgdata import load_preferences
        prefs = load_preferences()
        return prefs if isinstance(prefs, dict) else {}
    except Exception:
        return {}


def build(state: Dict[str, Any], prefs: Dict[str, Any], *, kind: str = "", need: str = "",
          include_hidden: bool = False) -> Dict[str, Any]:
    rows = [dict(row) for row in (state.get("models") or []) if isinstance(row, dict) and row.get("id")]
    if rows and "name" not in rows[0]:            # an older Proxy: name the rows here
        model_catalog.enrich({**state, "models": rows})
    aliases = prefs.get("model_aliases") if isinstance(prefs.get("model_aliases"), dict) else {}
    hidden = set(prefs.get("hidden_models") or [])
    pinned = list(prefs.get("pinned_models") or [])
    default = str(prefs.get("default_model") or "")
    for row in rows:
        alias = aliases.get(row["id"])
        if isinstance(alias, str) and alias.strip():
            row["alias"] = alias.strip()
        row["display"] = row.get("alias") or row.get("name") or row["id"]
        row["hidden"] = row["id"] in hidden
        row["pinned"] = row["id"] in pinned
        row["default"] = row["id"] == default
    picked = model_catalog.pick_rows(rows, kind=kind, need=need, hidden=hidden, include_hidden=include_hidden)
    rank = {model_id: i for i, model_id in enumerate(pinned)}
    picked.sort(key=lambda r: (0 if r["id"] in rank else 1, rank.get(r["id"], 0)))
    return {"models": picked, "default": default, "pinned": pinned, "count": len(picked),
            "image_models": state.get("image_models") or [], "video_models": state.get("video_models") or []}


def register(app, *, proxy_json: Callable[..., Awaitable[Tuple[int, Any]]]) -> None:

    @app.get("/api/pg/model-catalog")
    async def model_catalog_list(kind: str = "", need: str = "", all: str = ""):  # noqa: A002 - query name
        status, data = await proxy_json("GET", "/api/admin/pg/state")
        if status != 200 or not isinstance(data, dict):
            return JSONResponse({"error": (data or {}).get("error") if isinstance(data, dict) else "offline",
                                 "models": []}, status if status >= 400 else 502)
        kind = kind if kind in ("chat", "text", "image", "video", "music") else ""
        need = "vision" if need == "vision" else ""
        return build(data, _prefs(), kind=kind, need=need, include_hidden=all in ("1", "true", "yes"))

    @app.post("/api/pg/model-catalog/vision")
    async def model_catalog_vision(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        model = str(body.get("model") or "").strip()[:300]
        action = str(body.get("action") or "")
        en = body.get("lang") == "en"
        if not model or action not in ("probe", "set", "forget"):
            return JSONResponse({"error": "Bad request" if en else "Неверный запрос"}, 400)
        path = "/api/admin/models/" + quote(model, safe="")
        if action == "probe":
            status, data = await proxy_json("POST", path + "/probe", json.dumps({"confirm": True}).encode())
            data = data if isinstance(data, dict) else {}
            if status != 200:
                return {"ok": False, "vision": None, "error": str(data.get("error_en" if en else "error")
                                                                  or data.get("error") or f"HTTP {status}")[:600]}
            vision = data.get("vision")
            return {"ok": True, "vision": vision if isinstance(vision, bool) else None,
                    "verdict": str(data.get("verdict") or ""), "answer": str(data.get("answer") or "")[:200],
                    "error": str(data.get("error") or "")[:400]}
        if action == "set":
            value = body.get("value")
            if not isinstance(value, bool):
                return JSONResponse({"error": "value must be true or false"}, 400)
            status, data = await proxy_json("POST", path + "/override", json.dumps({"vision": value}).encode())
            return {"ok": status == 200, "vision": value} if status == 200 else \
                JSONResponse({"ok": False, "error": (data or {}).get("error", f"HTTP {status}")}, status)
        await proxy_json("POST", path + "/probe", json.dumps({"action": "forget"}).encode())
        status, data = await proxy_json("POST", path + "/override", json.dumps({"vision": None}).encode())
        return {"ok": status == 200}
