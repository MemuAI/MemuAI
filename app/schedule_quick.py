# -*- coding: utf-8 -*-
"""Round 29 (Planning 2.0): «запланируй… / schedule…» in Quick Access (Alt+Space) and its assistant commands.

The assistant action ``schedule.plan {text}`` (``assistant_catalog``) runs here:

1. the phrase goes to the Quick Access model with ``schedule_rules.PARSE_PROMPT`` (``schedule_rules_api.parse_text``);
2. the answer becomes a compact confirm card — the human summary, the next runs, what will be done, warnings —
   with «Создать задачу» (the engine's confirm token) and «Изменить в MemuAI» (opens the builder with the draft);
3. confirmed, the draft (kept HERE under a secret id, never in the card or the model's view) is saved through
   ``schedule_rules_api.save_task`` — the same validation as the builder.

A phrase that is not a schedule returns ``code: not_found`` so the plugin hands it to the model as a normal question.
"""
from __future__ import annotations

import json
import asyncio
import secrets
import time
from datetime import datetime
from typing import Any, Dict, Optional

from . import schedule_rules as rules

DRAFT_TTL = 15 * 60
_DRAFTS: Dict[str, Dict[str, Any]] = {}

_KIND_TEXT = {"chat": ("Ответ модели", "Model answer"), "notify": ("Напоминание", "Reminder"),
              "dfy": ("Do For You", "Do For You"), "command": ("Команда быстрого доступа", "Quick Access command"),
              "memucode": ("MemuCode", "MemuCode"), "integration": ("Интеграция", "Integration")}

def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _plural(n: int, one: str, few: str, many: str) -> str:
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return many
    if b == 1:
        return one
    return few if 2 <= b <= 4 else many

_WD_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MON_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
_MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def when_text(ts: float, lang: str, now: Optional[float] = None) -> str:
    """«сегодня в 16:00 · через 3 ч» / «пт, 16 октября в 09:00 · через 6 дней» — like the Planning panel."""
    now = time.time() if now is None else now
    moment, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    diff = (moment.date() - today.date()).days
    clock = moment.strftime("%H:%M")
    if diff == 0:
        day = _tr(lang, f"сегодня в {clock}", f"today at {clock}")
    elif diff == 1:
        day = _tr(lang, f"завтра в {clock}", f"tomorrow at {clock}")
    elif lang == "en":
        day = f"{_WD_EN[moment.weekday()]}, {_MON_EN[moment.month - 1]} {moment.day} at {clock}"
    else:
        day = f"{_WD_RU[moment.weekday()]}, {moment.day} {_MON_RU[moment.month - 1]} в {clock}"
    seconds = int(round(ts - now))
    if seconds < 45:
        rel = _tr(lang, "сейчас", "now")
    elif round(seconds / 60) < 60:
        m = max(1, round(seconds / 60))
        rel = _tr(lang, f"через {m} мин", f"in {m} min")
    elif round(seconds / 60) < 1440:
        total = round(seconds / 60)
        h, m = total // 60, total % 60
        rel = _tr(lang, f"через {h} ч" + (f" {m} мин" if m else ""), f"in {h} h" + (f" {m} min" if m else ""))
    else:
        hours = round(seconds / 3600)
        dd, hh = hours // 24, hours % 24
        if dd < 3 and hh:
            rel = _tr(lang, f"через {dd} {_plural(dd, 'день', 'дня', 'дней')} {hh} ч", f"in {dd} {'day' if dd == 1 else 'days'} {hh} h")
        else:
            d = round(seconds / 86400)
            rel = _tr(lang, f"через {d} {_plural(d, 'день', 'дня', 'дней')}", f"in {d} {'day' if d == 1 else 'days'}")
    return f"{day} · {rel}"

def _prune(now: float) -> None:
    for key in [k for k, v in _DRAFTS.items() if v["expires"] < now]:
        _DRAFTS.pop(key, None)

def make_completion(engine: Any, lang: str):
    """The Quick Access model (its own endpoint or the Proxy) as ``completion(model, messages)`` + the model id."""
    from .pgdata import load_preferences
    from . import quick_ask_endpoint as endpoint
    from .assistant_commands import ActionError
    prefs = load_preferences()
    if endpoint.active(prefs):
        model = str(prefs.get("quick_ask_endpoint_model") or "")
        if not model:
            raise ActionError(_tr(lang, "Выберите модель своего эндпоинта", "Choose a model of your endpoint"), "no_model")

        async def via_endpoint(model_id, messages, **_kw):
            return await endpoint.complete(endpoint.normalize_url(prefs.get("quick_ask_endpoint_url")),
                                           endpoint.get_key(), model_id, messages, lang=lang)
        return via_endpoint, model
    if getattr(engine, "completion", None) is None:
        raise ActionError(_tr(lang, "Модель недоступна", "No model available"), "no_model")
    model = str(prefs.get("quick_ask_model") or prefs.get("default_model") or "")
    if not model:
        raise ActionError(_tr(lang, "Выберите модель быстрого доступа", "Choose a Quick Ask model"), "no_model")

    async def via_proxy(model_id, messages, **_kw):
        return await engine.completion(model_id, messages)
    return via_proxy, model

def plan_card(draft: Dict[str, Any], payload: Dict[str, Any], lang: str, now: Optional[float] = None) -> Dict[str, Any]:
    """The fields of the confirm card (no secrets)."""
    now = time.time() if now is None else now
    kind = str(draft.get("kind") or "chat")
    label = _KIND_TEXT.get(kind, _KIND_TEXT["chat"])[1 if lang == "en" else 0]
    what = str(draft.get("command") if kind == "command" else draft.get("prompt") or "").strip()
    if kind == "integration":
        what = str(draft.get("tool") or "") + " " + json.dumps(draft.get("args") or {}, ensure_ascii=False)
    details = [[_tr(lang, "Сделать", "Do"), f"{label}: {what}" if what else label]]
    if draft.get("notify_telegram"):
        details.append([_tr(lang, "Результат", "Result"), _tr(lang, "в Telegram", "in Telegram")])
    nexts = [when_text(ts, lang, now) for ts in (payload.get("next") or [])[:3]]
    events = list(payload.get("events") or [])[:3]
    return {
        "title": _tr(lang, "Новая задача: ", "New task: ") + str(draft.get("name") or ""),
        "message": str(payload.get("summary") or ""),
        "details": details,
        "list": nexts or events or [_tr(lang, "Нет будущих запусков", "No future runs")],
        "warnings": [str(w) for w in (payload.get("warnings") or [])][:4],
        "plan": {"task": {k: v for k, v in draft.items() if k != "id"}, "summary": payload.get("summary") or "",
                 "next_label": _tr(lang, "Ближайшие запуски", "Next runs") if nexts else _tr(lang, "Когда", "When")},
    }

async def plan(engine: Any, params: Dict[str, Any], *, lang: str, source: str) -> Dict[str, Any]:
    """The ``schedule.plan`` handler: a phrase → a confirm card, or (with ``draft``) the confirmed save."""
    from .assistant_commands import ActionError
    from . import schedule_rules_api as plan_api
    now = time.time()
    _prune(now)
    draft_id = str(params.get("draft") or "")
    if draft_id:
        entry = _DRAFTS.pop(draft_id, None)
        if not entry:
            raise ActionError(_tr(lang, "Черновик устарел — повторите команду.", "The draft expired — repeat the command."), "expired")
        try:
            task_id = await asyncio.to_thread(plan_api.save_task, {**entry["task"], "created_by": "quick-ask"}, lang)
        except rules.RuleError as exc:
            raise ActionError(exc.text(lang), "bad_rule") from None
        return {"status": "done", "message": entry["summary"],
                "title": _tr(lang, "Задача создана: ", "Task created: ") + str(entry["task"].get("name") or ""),
                "list": entry.get("list") or [], "plan_id": task_id,
                "details": [[_tr(lang, "Где", "Where"), _tr(lang, "MemuAI → Планирование", "MemuAI → Planning")]]}
    text = str(params.get("text") or "").strip()
    if not text:
        raise ActionError(_tr(lang, "Опишите, что и когда сделать", "Describe what to do and when"), "bad_params")
    completion, model = make_completion(engine, lang)
    tasks = await asyncio.to_thread(plan_api._load_tasks)
    try:
        draft = await asyncio.wait_for(plan_api.parse_text(text, model, completion, lang=lang, tasks=tasks), 120)
        payload = plan_api.preview_payload(draft, tasks, lang, now)
    except rules.RuleError as exc:

        raise ActionError(exc.text(lang), "not_found") from None
    except asyncio.TimeoutError:
        raise ActionError(_tr(lang, "Модель не ответила вовремя", "The model did not answer in time"), "model") from None
    except ActionError:
        raise
    except Exception as exc:  # noqa: BLE001 — the provider's words help (429, no key)
        raise ActionError(_tr(lang, "Модель не ответила: ", "The model did not answer: ") + str(exc)[:300], "model") from None
    draft["rule"] = payload["rule"]
    draft["model"] = draft.get("model") or model
    card = plan_card(draft, payload, lang, now)
    secret = secrets.token_urlsafe(12)
    _DRAFTS[secret] = {"task": draft, "summary": card["message"], "list": card["list"], "expires": now + DRAFT_TTL}
    token = secrets.token_urlsafe(12)
    with engine.lock:
        engine.pending[token] = {"action": "schedule.plan", "params": {"text": text[:500], "draft": secret},
                                 "source": source, "lang": lang, "expires": engine.clock() + DRAFT_TTL, "tier": "confirm"}
    return {"status": "confirm", "kind": "plan", "confirm": {"token": token, "tier": "confirm", "countdown": 0,
                                                             "expires": now + DRAFT_TTL}, **card}

def register(engine: Any) -> None:
    async def handler(params: Dict[str, Any], *, lang: str = "ru", source: str = "local", **_kw: Any) -> Dict[str, Any]:
        return await plan(engine, params, lang=lang, source=source)
    engine.handlers["schedule.plan"] = handler
