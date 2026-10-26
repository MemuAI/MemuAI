"""«Время и фокус» in the Telegram bot (Round 27).

As in Claude, quiet hours and break reminders are set once (MemuAI → Settings → «Время и фокус») and apply
on every client: the main window, Quick Ask and the Telegram bot.

* Quiet hours: the first message of a quiet window is held and the bot answers with a calm reminder —
  «▶️ Продолжить» sends the held message and silences the reminder until the window ends, «Вернусь позже»
  drops it. A reminder, never a lock.
* The Mini App (tgapp_api ``chats.send``) gets the same: a ``quiet_hours`` answer the app turns into a
  «Продолжить?» dialog, and a ``break`` field when the reminder is due (``focus.break`` answers it).
* Break reminder: every message the user sends adds to the day's time with MemuAI
  (:func:`app.reflect.shared_tracker`, the same ``memuai/focus.json`` the window uses — the bot lives in the
  MemuAI Proxy process). When the threshold is reached a silent card comes with «Сделаю перерыв» /
  «Через 10 минут» / «Скрыть»; the answer itself is never held.

State of held messages is per Telegram chat and in memory: a restart simply forgets a pending reminder.
"""
from __future__ import annotations

import asyncio
import itertools
from typing import Any, Dict, List, Optional, Tuple

from . import reflect

QUIET_GO, QUIET_LATER = "fq:go", "fq:later"
BREAK_ACTIONS = {"fb:break": "break", "fb:snooze": "snooze", "fb:dismiss": "dismiss"}

_held: Dict[str, Tuple[List[Tuple[Dict[str, Any], str]], int, str]] = {}
_hold_ids = itertools.count(1)
_acknowledged: Dict[str, int] = {}

def _tr(prefs: Dict[str, Any], ru: str, en: str) -> str:
    """The bot speaks ``telegram_language``; the Mini App passes ``{"telegram_language": its language}``."""
    return en if prefs.get("telegram_language") == "en" else ru

def duration(minutes: int, prefs: Dict[str, Any]) -> str:
    hours, rest = divmod(max(0, int(minutes)), 60)
    if prefs.get("telegram_language") == "en":
        return f"{hours} h {rest} min" if hours and rest else (f"{hours} h" if hours else f"{rest} min")
    return f"{hours} ч {rest} мин" if hours and rest else (f"{hours} ч" if hours else f"{rest} мин")

def quiet_pending(key: str, prefs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The quiet window the user is in and has not yet answered «Продолжить» for (``key`` = bot chat / app user)."""
    window = reflect.quiet_now(prefs)
    if not window or _acknowledged.get(key, 0) >= window["ends_at"]:
        return None
    return window

def acknowledge(key: str, ends_at: int) -> None:
    _acknowledged[key] = max(_acknowledged.get(key, 0), int(ends_at))

def quiet_hold(chat_id: str, message: Dict[str, Any], text: str,
               prefs: Dict[str, Any]) -> Optional[Tuple[str, Optional[Dict[str, Any]]]]:
    """During an unacknowledged quiet window: keep the message and return the reminder (text, keyboard).

    More messages sent before the user answers join the held ones (keyboard ``None``: no second card);
    «Продолжить» sends them all in order."""
    window = quiet_pending(chat_id, prefs)
    if not window:
        return None
    pending = _held.get(chat_id)
    if pending and pending[1] == int(window["ends_at"]):
        pending[0].append((message, text))
        return "", None
    hold_id = str(next(_hold_ids))
    _held[chat_id] = ([(message, text)], int(window["ends_at"]), hold_id)
    body = _tr(prefs,
               f"🌙 Сейчас ваши тихие часы — до {window['until']} вы решили оставить время для другого.\n"
               "Отправить сообщение всё равно? Это только напоминание.",
               f"🌙 These are your quiet hours — you set aside the time until {window['until']} for something else.\n"
               "Send the message anyway? It's only a reminder.")
    keyboard = {"inline_keyboard": [[{"text": _tr(prefs, "▶️ Продолжить", "▶️ Continue"), "callback_data": f"{QUIET_GO}:{hold_id}"},
                                     {"text": _tr(prefs, "Вернусь позже", "Come back later"),
                                      "callback_data": f"{QUIET_LATER}:{hold_id}"}]]}
    return body, keyboard

def _quiet_parts(data: str) -> Tuple[str, str]:
    action, _, hold_id = data.partition(":")[2].partition(":")
    return "fq:" + action, hold_id

def is_quiet_callback(data: str) -> bool:
    return data.startswith("fq:") and _quiet_parts(data)[0] in (QUIET_GO, QUIET_LATER)

def quiet_answer(chat_id: str, data: str,
                 prefs: Dict[str, Any]) -> Tuple[List[Tuple[Dict[str, Any], str]], str]:
    """A tap on the quiet-hours reminder (``fq:go:<hold id>`` / ``fq:later:<hold id>``) → (the held
    ``(message, text)`` pairs to send now, the new card text). Only the reminder that holds them can release them."""
    action, hold_id = _quiet_parts(data)
    held = _held.get(chat_id)
    if held is not None and hold_id and held[2] == hold_id:
        _held.pop(chat_id, None)
    else:
        held = None
    if action != QUIET_GO:
        return [], _tr(prefs, "🌙 Хорошо, сообщение не отправлено. До встречи после тихих часов.",
                       "🌙 Okay, the message wasn't sent. See you after your quiet hours.")
    if held is None:
        return [], _tr(prefs, "Это напоминание уже неактуально — просто отправьте сообщение ещё раз.",
                       "This reminder has expired — just send the message again.")
    messages, ends_at, _ = held
    acknowledge(chat_id, ends_at)
    return messages, _tr(prefs, "🌙 Продолжаем — до конца тихих часов больше не напомню.",
                                "🌙 Continuing — no more reminders until your quiet hours end.")

async def break_minutes(prefs: Dict[str, Any]) -> Optional[int]:
    """Add this message to the day's time; the day's minutes when the break reminder is due, else ``None``."""
    if not prefs.get("focus_break_enabled"):
        return None
    tracker = reflect.shared_tracker()
    try:
        out = await asyncio.to_thread(tracker.activity, int(prefs.get("focus_break_minutes") or 60), True)
    except Exception:
        return None
    return int(out.get("minutes_today") or 0) if out.get("break_due") else None

async def break_due(prefs: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The bot's break card (text, keyboard) when the reminder is due."""
    minutes = await break_minutes(prefs)
    if minutes is None:
        return None
    spent = duration(minutes, prefs)
    body = _tr(prefs, f"☕ Пора сделать перерыв? Сегодня вы уже {spent} с MemuAI. Встаньте, разомнитесь — разговор подождёт.",
               f"☕ Time for a break? You've spent {spent} with MemuAI today. Stand up, stretch — the chat will wait.")
    keyboard = {"inline_keyboard": [[{"text": _tr(prefs, "Сделаю перерыв", "Take a break"), "callback_data": "fb:break"},
                                     {"text": _tr(prefs, "Через 10 мин", "In 10 min"), "callback_data": "fb:snooze"},
                                     {"text": _tr(prefs, "Скрыть", "Dismiss"), "callback_data": "fb:dismiss"}]]}
    return body, keyboard

async def break_action(kind: str) -> None:
    try:
        await asyncio.to_thread(reflect.shared_tracker().action, kind)
    except Exception:
        pass

async def break_answer(data: str, prefs: Dict[str, Any]) -> str:
    """A tap on the break card → the shared tracker learns it; returns the card's new text."""
    kind = BREAK_ACTIONS[data]
    await break_action(kind)
    if kind == "break":
        return _tr(prefs, "☕ Хорошего перерыва!", "☕ Enjoy your break!")
    if kind == "snooze":
        return _tr(prefs, "☕ Напомню через 10 минут.", "☕ I'll remind you in 10 minutes.")
    return _tr(prefs, "☕ Хорошо, сегодня больше не напомню.", "☕ Okay, no more reminders today.")
