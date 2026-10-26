# -*- coding: utf-8 -*-
"""MemuLearn in the Telegram Mini App — the «Тренажёр» tab (``app/tgapp/learn.{js,css}``).

The Mini App lives in the Proxy process; the learning data is the same SQLite file on disk (``app.learn.store``, WAL),
so the operations call ``app.learn.api`` directly — the MemuAI window does not have to be open.

``learn.state`` → ``{enabled, digest}`` (the tab shows only while the MemuLearn module is on);
``learn.queue {course_id?}`` → today's cards with the interval of each button; ``learn.answer {id, rating, duration_ms}``;
``learn.undo``. Card text goes as is (Markdown / cloze / math source) — the phone renders it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from .store import LearnError

CARD_FIELDS = ("id", "course_id", "lesson_id", "kind", "front", "back", "extra", "ord", "state", "due", "tags", "buttons")

def module_enabled() -> bool:
    from ..modules_host import default_root, list_modules
    try:
        return any(row.get("id") == "learn" and row.get("enabled") for row in list_modules(default_root()))
    except Exception:
        return False

def register_tgapp_ops(op: Callable[[str], Callable], ApiError: Any) -> None:  # noqa: N803
    from . import api

    def lang(ctx) -> str:
        return "en" if (ctx.get("prefs") or {}).get("language") == "en" else "ru"

    async def run(ctx, name: str, args: Dict[str, Any]) -> Any:
        if not await asyncio.to_thread(module_enabled):
            raise ApiError(403, "learn_off", "Модуль MemuLearn выключен — включите его в MemuAI → Модули",
                           "The MemuLearn module is off — turn it on in MemuAI → Modules")
        try:
            return await api.call_async(name, args, lang=lang(ctx))
        except LearnError as exc:
            raise ApiError(404 if exc.code == "not_found" else 400, exc.code, exc.ru, exc.en) from None

    @op("learn.state")
    async def learn_state(ctx, body):
        enabled = await asyncio.to_thread(module_enabled)
        if not enabled:
            return {"enabled": False}
        return {"enabled": True, "digest": await api.call_async("panel", {}, lang=lang(ctx))}

    @op("learn.queue")
    async def learn_queue(ctx, body):
        q = await run(ctx, "review.queue", {"course_id": str((body or {}).get("course_id") or ""), "limit": 100})
        return {"counts": q["counts"], "minutes": q["minutes"],
                "cards": [{k: c.get(k) for k in CARD_FIELDS} for c in q["cards"]]}

    @op("learn.answer")
    async def learn_answer(ctx, body):
        body = body or {}
        card = await run(ctx, "review.answer", {"id": str(body.get("id") or ""), "rating": int(body.get("rating") or 0),
                                                "duration_ms": int(body.get("duration_ms") or 0), "device": "Telegram"})
        return {"id": card["id"], "state": card["state"], "due": card["due"], "interval_label": card.get("interval_label", "")}

    @op("learn.undo")
    async def learn_undo(ctx, body):
        card = await run(ctx, "review.undo", {})
        return {"id": card["id"]}
