"""HTTP-маршруты «Входящих» (Round 35): /api/pg/inbox/* в сервере окна MemuAI.

Хранилище и контракт — app/inbox.py; производители и обработчики кнопок —
app/inbox_hooks.py. Живые обновления — SSE /api/pg/inbox/stream (событие
``counts`` при каждом изменении, в т.ч. сделанном другим процессом — Proxy).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import inbox

SNOOZE_PRESETS = {"1h": 3600, "3h": 3 * 3600, "tomorrow": 0, "1d": 86400, "1w": 7 * 86400}


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _tomorrow_morning() -> float:
    import datetime as dt
    now = dt.datetime.now()
    target = (now + dt.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return (target - now).total_seconds()


_startup: dict = {"fn": None}


def _links_version() -> int:
    try:
        from . import deeplinks
        return deeplinks.queue_version()
    except Exception:  # noqa: BLE001
        return 0


def set_startup(fn: Any) -> None:
    """Корутина, которую надо выполнить один раз при первом запросе к «Входящим»."""
    _startup["fn"] = fn


async def _ensure_started() -> None:
    fn, _startup["fn"] = _startup["fn"], None
    if fn is not None:
        try:
            await fn()
        except Exception:  # noqa: BLE001
            pass


def register(app: Any) -> None:
    @app.get("/api/pg/inbox")
    async def inbox_list(status: str = "all", source: str = "", q: str = "", group: str = "",
                         limit: int = 300):
        await _ensure_started()
        rows = await asyncio.to_thread(inbox.list, status=status, source=source or None, q=q,
                                       group=group or None, limit=max(1, min(600, limit)))
        counts = await asyncio.to_thread(inbox.counts)
        return {"items": rows, "counts": counts, "version": inbox.version()}

    @app.get("/api/pg/inbox/counts")
    async def inbox_counts():
        await _ensure_started()
        return {**(await asyncio.to_thread(inbox.counts)), "version": inbox.version()}

    @app.get("/api/pg/inbox/stream")
    async def inbox_stream(request: Request):
        await _ensure_started()
        async def gen():
            last = ""
            last_counts = None
            idle = 0.0
            links = _links_version()
            while True:
                if await request.is_disconnected():
                    return
                # Раунд 36: новая ссылка memuai:// — окно забирает очередь по этому событию (без опроса раз в 4 с)
                lv = _links_version()
                if lv != links:
                    links = lv
                    yield "event: links\ndata: " + json.dumps({"version": lv}) + "\n\n"
                ver = inbox.version()
                # раз в 15 с пересчитываем и без изменений: истёкшее «отложить» возвращает элемент в бейдж
                if ver != last or idle >= 15:
                    changed = ver != last
                    last = ver
                    idle = 0.0
                    c = await asyncio.to_thread(inbox.counts)
                    if changed or c != last_counts:
                        last_counts = c
                        yield "event: counts\ndata: " + json.dumps({**c, "version": ver}, ensure_ascii=False) + "\n\n"
                    else:
                        yield ": keep-alive\n\n"
                else:
                    idle += 1.0
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/pg/inbox/item/{item_id}")
    async def inbox_get(item_id: str):
        it = await asyncio.to_thread(inbox.get, item_id)
        if not it:
            return JSONResponse({"error": "not_found"}, 404)
        return it

    @app.post("/api/pg/inbox/item/{item_id}/action")
    async def inbox_action(item_id: str, request: Request):
        body = await _body(request)
        action = str(body.get("action") or "").strip()
        if not action:
            return JSONResponse({"ok": False, "error": "action"}, 400)
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        res = await inbox.arun_action(item_id, action, payload)
        if not res.get("ok") and res.get("error") == "not_found":
            return JSONResponse(res, 404)
        return res

    @app.post("/api/pg/inbox/read")
    async def inbox_read(request: Request):
        body = await _body(request)
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        n = await asyncio.to_thread(inbox.mark_read, [str(x) for x in ids][:600],
                                    body.get("read", True) is not False, bool(body.get("all")))
        return {"ok": True, "changed": n}

    @app.post("/api/pg/inbox/item/{item_id}/snooze")
    async def inbox_snooze(item_id: str, request: Request):
        body = await _body(request)
        preset = str(body.get("preset") or "")
        if preset == "tomorrow":
            seconds = _tomorrow_morning()
        elif preset in SNOOZE_PRESETS:
            seconds = SNOOZE_PRESETS[preset]
        else:
            try:
                seconds = float(body.get("seconds") or 0)
            except (TypeError, ValueError):
                seconds = 0
        seconds = max(0.0, min(seconds, 30 * 86400))
        ok = await asyncio.to_thread(inbox.snooze, item_id, seconds)
        return {"ok": ok}

    @app.post("/api/pg/inbox/item/{item_id}/dismiss")
    async def inbox_dismiss(item_id: str):
        return {"ok": await asyncio.to_thread(inbox.dismiss, item_id)}

    @app.post("/api/pg/inbox/clear-done")
    async def inbox_clear_done():
        return {"ok": True, "removed": await asyncio.to_thread(inbox.clear_done)}

    @app.post("/api/pg/inbox/post")
    async def inbox_post(request: Request):
        """Для производителей на стороне интерфейса (JS). Только уведомления/вопросы
        с кнопками «открыть» — серверных обработчиков у них нет."""
        body = await _body(request)
        title = str(body.get("title") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title"}, 400)
        source = str(body.get("source") or "ui")[:40]
        actions = [a for a in (body.get("actions") or []) if isinstance(a, dict)][:6]
        for a in actions:
            a["kind"] = "ui"
        item_id = await asyncio.to_thread(
            inbox.post, str(body.get("kind") or "notice"), title, str(body.get("body") or ""),
            source=source, ref=body.get("ref") if isinstance(body.get("ref"), (dict, str)) else None,
            actions=actions, dedupe_key=str(body.get("dedupe_key") or "") or None,
            severity=str(body.get("severity") or "info"))
        return {"ok": True, "id": item_id}

    @app.post("/api/pg/inbox/resolve")
    async def inbox_resolve(request: Request):
        body = await _body(request)
        key = str(body.get("key") or body.get("id") or "")
        return {"ok": await asyncio.to_thread(inbox.resolve, key, body.get("result"))}
