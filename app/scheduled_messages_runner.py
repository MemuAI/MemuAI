# -*- coding: utf-8 -*-
"""Round 29: MemuAI Proxy side of scheduled messages.

Runs as a background task of the Proxy (``app/main.py`` lifespan). Every few
seconds it looks at each profile's ``scheduled_messages.json`` (a cheap ``stat``
unless something is due) and sends:

* messages the MemuAI window *delegated* (``route = "server"``: a regular chat that
  is not open in the window when its time came);
* every due regular-chat message when the window is closed (no heartbeat for the
  profile) and ``schedsend_background`` is on.

Sending reuses the server-side generation of the Telegram Mini App
(:mod:`app.tgapp_chat`): the same system prompt, personality, agent (мемес),
profile/memory context, attachments and chat-title logic; the user message and
the answer are appended to the stored chat in one transaction, so the window
shows them through its usual chat sync. Work chats and image models are left to
the window.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Set

from . import scheduled_messages as sm

log = logging.getLogger("memuai.schedsend")

TICK_SECONDS = 3.0
_RUNNING: Set[str] = set()
_TASKS: Set[asyncio.Task] = set()
_SEEN: Dict[str, Dict[str, Any]] = {}
_MOBILE_BLOCK = re.compile(r"<mobile_interface>[\s\S]*?</mobile_interface>")

def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _clock(stamp_ms: int) -> str:
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(stamp_ms / 1000))
    except (OverflowError, OSError, ValueError):
        return ""

def delivery_block(item: Dict[str, Any], lang: str) -> str:
    """Replaces the Mini App's «you are on a phone» block for a scheduled delivery."""
    late = item.get("late")
    when = _clock(int(item.get("due_at") or 0)) if item.get("trigger") == "time" else ""
    return ("<scheduled_delivery>The user wrote this message earlier and scheduled it to be sent automatically"
            + (f" at {when}" if when else " as soon as the connection was available")
            + (" (it is delivered late because MemuAI was closed at that time)" if late else "")
            + ". MemuAI Proxy delivers it in the background and the MemuAI window may be closed, so local tools, "
            "Work projects, Browser, Computer Use, skills and image generation are not available: never emit their "
            "hidden protocol blocks and never claim to have used them. Answer it as a normal message of this "
            "conversation.</scheduled_delivery>")

def online(timeout: float = 3.0) -> bool:
    """A best-effort internet probe for «when online» messages (any of a few well-known hosts)."""
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 443), ("www.microsoft.com", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False

def _provider_is_local(model: str) -> bool:
    try:
        from .tgapp_chat import model_provider
        return model_provider(model) == "local"
    except Exception:
        return False

async def _notify(item: Dict[str, Any], lang: str, *, title: str, text: str, prefs: Dict[str, Any],
                  windows: bool, profile_id: str) -> None:
    if windows and prefs.get("schedsend_notify", True) and prefs.get("notify_windows", True):
        try:
            from .desktop_notifications import show_completion
            await asyncio.to_thread(show_completion, title, text[:300], sound=bool(prefs.get("notify_sound", False)))
        except Exception as exc:  # noqa: BLE001
            log.info("schedsend: Windows notification not shown (%s)", type(exc).__name__)
    if item.get("notify_telegram"):
        try:
            from .telegram_bot import send_notification
            await send_notification(title, text, source_chat_id=str(item.get("chat_id") or ""),
                                    source_chat_title=str(item.get("chat_title") or ""), profile_id=profile_id,
                                    model=str(item.get("model_used") or item.get("model") or ""), kind="schedule")
        except Exception as exc:  # noqa: BLE001
            log.info("schedsend: Telegram notification not sent (%s)", type(exc).__name__)

async def execute(item: Dict[str, Any], profile_id: str, *, window_open: bool) -> Dict[str, Any]:
    """Send one claimed message into its chat and record the outcome. Returns a small report."""
    from . import tgapp_chat as tg
    from .pgdata import load_chats, load_preferences
    prefs = await asyncio.to_thread(load_preferences)
    lang = "en" if prefs.get("language") == "en" else "ru"
    item_id = str(item["id"])
    report: Dict[str, Any] = {"id": item_id, "ok": False}

    async def fail(message: str, *, retry: bool = False) -> Dict[str, Any]:
        await asyncio.to_thread(sm.complete, item_id, False, profile_id, error=message, by="proxy", retry=retry)
        if not retry:
            await _notify(item, lang, title=_tr(lang, "MemuAI · Сообщение не отправлено", "MemuAI · Message not sent"),
                          text=message, prefs=prefs, windows=not window_open, profile_id=profile_id)
        report.update({"error": message, "retry": retry})
        return report

    chat_id = str(item["chat_id"])
    chats = await asyncio.to_thread(load_chats, profile_id)
    chat = next((row for row in chats if str(row.get("id")) == chat_id), None)
    if chat is None and not item.get("new_chat"):
        return await fail(_tr(lang, "Чат удалён — сообщение не отправлено", "The chat was deleted — the message was not sent"))
    if tg.jobs.active_for_chat(chat_id):
        return await fail(_tr(lang, "В чате уже идёт ответ", "An answer is already running in this chat"), retry=True)
    profile = tg.profile_by_id(profile_id)
    meme_id = str((prefs.get("chat_memes") or {}).get(chat_id) or "")
    meme = tg.find_meme(prefs, meme_id)
    models = await asyncio.to_thread(tg.chat_models, prefs)
    requested = str(item.get("model") or "")
    model = tg.pick_model(requested, prefs, models)
    if not model:
        return await fail(_tr(lang, "Нет доступных моделей: добавьте аккаунт в MemuAI Proxy",
                              "No models are available: add an account in MemuAI Proxy"), retry=item.get("trigger") == "online")
    if item.get("trigger") == "online" and not _provider_is_local(model):
        if not await asyncio.to_thread(online):
            return await fail(_tr(lang, "Нет подключения к интернету", "No internet connection"), retry=True)
    caps_item = item.get("caps") or {}
    web_search = bool(caps_item["web_search"]) if "web_search" in caps_item else (
        bool(meme.get("web_search")) if meme else bool(prefs.get("web_search")))
    thinking = bool(caps_item["thinking"]) if "thinking" in caps_item else (
        bool(meme.get("thinking")) if meme else bool(prefs.get("thinking")))
    caps = tg.meme_caps(meme) if meme else {key: True for key in tg.MEME_CAPS}
    images, notes, stored = tg.prepare_attachments(
        [{"name": row.get("name"), "data": row.get("data")} for row in item.get("attachments") or []], lang)
    text = str(item.get("text") or "")
    stamp = tg.now_ms()
    if chat is None:
        chat = {"id": chat_id, "title": str(item.get("chat_title") or ""), "messages": [], "mode": "chat",
                "created": stamp, "updated": stamp, "model": model, "_new": True}
    chat = dict(chat)
    history = [dict(row) for row in chat.get("messages") or [] if isinstance(row, dict)]
    user_row: Dict[str, Any] = {"role": "user", "content": text, "createdAt": stamp, "via": "scheduled",
                                "scheduled": {"id": item_id, "at": int(item.get("due_at") or 0),
                                              "late": bool(item.get("late")), "trigger": item.get("trigger")}}
    if stored:
        user_row["attachments"] = stored
    asked = sum(1 for row in history if row.get("role") == "user") + 1
    memory = tg.profile_memory(profile) if caps["memory"] else {"enabled": False, "facts": []}
    reference = tg.profile_reference_context(profile, memory) if caps["memory"] else ""
    system = tg.build_system_prompt(model=model, prefs=prefs, lang=lang, temp=False, meme=meme,
                                    has_profile_context=bool(reference),
                                    title_prompt=tg.title_prompt_for(chat, prefs, asked))
    block = delivery_block(item, lang)
    if system:
        system = _MOBILE_BLOCK.sub(block, system) if _MOBILE_BLOCK.search(system) else system + "\n\n" + block
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if reference:
        messages.append({"role": "user", "content": reference})
    messages.extend(tg.history_messages(history))
    prompt_text = text
    if notes:
        prompt_text = (prompt_text + "\n\n" if prompt_text else "") + "\n\n".join(notes)
    if images:
        messages.append({"role": "user", "content": [{"type": "text", "text": prompt_text or " "}]
                         + [{"type": "image_url", "image_url": {"url": url}} for url in images]})
    else:
        messages.append({"role": "user", "content": prompt_text})

    job = tg.jobs.add(tg.Job("schedsend", "schedsend"))
    job.meta = {"chat_id": chat_id, "profile_id": profile_id, "model": model, "temp": False,
                "meme_id": meme["id"] if meme else "", "scheduled": item_id}
    try:
        await tg._generate(job, chat, history, user_row, messages, model, web_search, thinking, profile,
                           lang, False, meme_id if meme else "")
    except asyncio.CancelledError:
        await asyncio.to_thread(sm.complete, item_id, False, profile_id, error="cancelled", by="proxy")
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("schedsend: generation crashed (%s)", type(exc).__name__)
        return await fail(str(exc)[:300] or type(exc).__name__)
    errors = [str(event.get("message") or "") for event in job.events if event.get("type") == "error"]
    answered = any(event.get("type") == "message" for event in job.events)
    saved = any(event.get("type") == "done" for event in job.events)
    unsaved = any(("не сохран" in value or "not saved" in value or "deleted" in value or "удалён" in value)
                  for value in errors)
    if unsaved or not saved:
        return await fail(errors[0] if errors else _tr(lang, "Сообщение не сохранено", "The message was not saved"))
    answer = next((str((event.get("message") or {}).get("content") or "") for event in job.events
                   if event.get("type") == "message"), "")
    answer_error = "" if answered and not errors else (errors[0] if errors else "")
    late = bool(item.get("late"))
    notice = (not window_open) or late or bool(answer_error)
    await asyncio.to_thread(sm.complete, item_id, True, profile_id, answer_error=answer_error, model_used=model,
                            by="proxy", notice=notice)
    title = str(item.get("chat_title") or "") or _tr(lang, "Чат", "Chat")
    head = (_tr(lang, "MemuAI · Отложенное сообщение отправлено с опозданием", "MemuAI · Scheduled message sent late")
            if late else _tr(lang, "MemuAI · Отложенное сообщение отправлено", "MemuAI · Scheduled message sent"))
    body = f"«{title}»: " + (answer or answer_error or text)[:400]
    await _notify({**item, "model_used": model}, lang, title=head, text=body, prefs=prefs,
                  windows=not window_open, profile_id=profile_id)
    report.update({"ok": True, "answer_error": answer_error, "model": model})
    return report

async def _run(item: Dict[str, Any], profile_id: str, window_open: bool) -> None:
    key = f"{profile_id}:{item['id']}"
    try:
        await execute(item, profile_id, window_open=window_open)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("schedsend: send failed (%s)", type(exc).__name__)
        try:
            await asyncio.to_thread(sm.complete, item["id"], False, profile_id, error=type(exc).__name__, by="proxy")
        except Exception:
            pass
    finally:
        _RUNNING.discard(key)

async def _parked(item: Dict[str, Any], action: str, profile_id: str, window_open: bool) -> None:
    """A missed message waiting for the user (``ask``) or skipped by the policy."""
    if window_open:
        return
    from .pgdata import load_preferences
    prefs = await asyncio.to_thread(load_preferences)
    lang = "en" if prefs.get("language") == "en" else "ru"
    title = (_tr(lang, "MemuAI · Отложенное сообщение пропущено", "MemuAI · Scheduled message skipped") if action == "skip"
             else _tr(lang, "MemuAI · Время отложенного сообщения прошло", "MemuAI · A scheduled message missed its time"))
    text = (_tr(lang, "Откройте MemuAI, чтобы отправить его или удалить: ", "Open MemuAI to send or delete it: ")
            if action == "ask" else "") + str(item.get("text") or "")[:200]
    await _notify(item, lang, title=title, text=text, prefs=prefs, windows=True, profile_id=profile_id)

async def tick(*, now: Optional[int] = None) -> int:
    """One pass over every profile; returns how many sends were started."""
    from .pgdata import load_preferences
    stamp = now if now is not None else sm.now_ms()
    started = 0
    profiles = await asyncio.to_thread(sm.profile_ids)
    prefs: Optional[Dict[str, Any]] = None
    for profile_id in profiles:
        try:
            rev = await asyncio.to_thread(sm.revision, profile_id)
        except Exception:
            continue
        if rev == "0":
            continue
        seen = _SEEN.get(profile_id) or {}
        due = int(seen.get("next_due") or 0)
        if seen.get("rev") == rev and (not due or due > stamp) and stamp - int(seen.get("swept") or 0) < 60_000:
            continue
        if stamp - int(seen.get("swept") or 0) >= 60_000:
            await asyncio.to_thread(sm.release_stale, profile_id)
            seen["swept"] = stamp
        rows = await asyncio.to_thread(sm.load, profile_id)
        seen.update({"rev": await asyncio.to_thread(sm.revision, profile_id), "next_due": sm.next_due(rows)})
        _SEEN[profile_id] = seen
        due_rows = [row for row in rows if sm.is_due(row, stamp)]
        if not due_rows:
            continue
        if prefs is None:
            prefs = await asyncio.to_thread(load_preferences)
        window_open = await asyncio.to_thread(sm.desktop_alive, stamp, profile_id)
        background = bool(prefs.get("schedsend_background", True))
        policy = str(prefs.get("schedsend_missed") or "send")
        for row in due_rows:
            key = f"{profile_id}:{row['id']}"
            if key in _RUNNING or not sm.server_capable(row):
                continue
            if row.get("route") != "server" and (window_open or not background):
                continue
            result = await asyncio.to_thread(sm.claim, row["id"], "proxy", profile_id, policy=policy, now=stamp)
            action = result.get("action")
            if action == "send":
                _RUNNING.add(key)
                task = asyncio.create_task(_run(result["item"], profile_id, window_open))
                _TASKS.add(task)
                task.add_done_callback(_TASKS.discard)
                started += 1
            elif action in ("ask", "skip"):
                await _parked(result["item"], action, profile_id, window_open)
        seen["rev"] = await asyncio.to_thread(sm.revision, profile_id)
    return started

async def runner_loop() -> None:
    """Background loop of MemuAI Proxy (started in ``app/main.py``)."""
    try:
        await asyncio.sleep(2.0)
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("schedsend: %s", type(exc).__name__)
            await asyncio.sleep(TICK_SECONDS)
    finally:

        pending = [task for task in list(_TASKS) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
