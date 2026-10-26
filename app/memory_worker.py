# -*- coding: utf-8 -*-
"""Background work of memory v2 on the MemuAI window server (Round 40, r40-memory).

Two lazy jobs, never on the path of a message:

* **concepts** — facts saved before Round 40 (or by a model that skipped ``"concepts"``) get 5–8 associations
  in one cheap model call per batch of up to 25 facts (:func:`memory_v2.set_concepts`);
* **session diary** — a big chat (≥ 6 messages, ≥ 1200 characters) that has been idle for 20 minutes gets a
  1–2-line summary with its date (:func:`memory_v2.save_diary`); a chat that grew by 4+ messages is summarized again.

:func:`kick` is called from ``/api/memory/context`` (every message) and once after start-up; it starts at most one
run per :data:`MIN_GAP` seconds as an asyncio task. Disk work goes through ``asyncio.to_thread``. A failed model
call backs off for :data:`BACKOFF` seconds. The model is ``settings.helper_model`` or the default chat model.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import memory_v2 as mem

log = logging.getLogger("memuai")

Completion = Callable[..., Awaitable[str]]

MIN_GAP = 90
BACKOFF = 15 * 60
IDLE_MS = 20 * 60 * 1000
MAX_AGE_MS = 30 * 86400_000
MIN_MESSAGES = 6
MIN_CHARS = 1200
REGROW = 4
CONCEPT_BATCH = 25
DIARY_PER_RUN = 3

_STATE: Dict[str, Any] = {"completion": None, "task": None, "last": 0.0, "backoff_until": 0.0, "last_error": "",
                          "last_run": 0.0}

def configure(completion: Optional[Completion]) -> None:
    _STATE["completion"] = completion

def status() -> Dict[str, Any]:
    task = _STATE.get("task")
    return {"running": bool(task and not task.done()), "last_run": int(_STATE["last_run"] * 1000),
            "error": _STATE["last_error"], "paused_until": int(_STATE["backoff_until"] * 1000)}

def kick(force: bool = False) -> bool:
    """Start one background run if none is running and the gap passed. Never raises, never blocks."""
    try:
        if _STATE["completion"] is None:
            return False
        task = _STATE.get("task")
        if task and not task.done():
            return False
        now = time.time()
        if not force and (now - _STATE["last"] < MIN_GAP or now < _STATE["backoff_until"]):
            return False
        loop = asyncio.get_running_loop()
        _STATE["last"] = now
        _STATE["task"] = loop.create_task(run_once())
        return True
    except Exception:
        return False

def _default_model(doc: Dict[str, Any]) -> str:
    model = str(doc["settings"].get("helper_model") or "").strip()
    if model:
        return model
    try:
        from .pgdata import load_preferences
        return str(load_preferences().get("default_model") or "").strip()[:300]
    except Exception:
        return ""

def _json_in(text: str) -> Any:
    raw = str(text or "")
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I)
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    return mem._loads_lenient(fenced.group(1) if fenced else raw)

CONCEPTS_PROMPT = (
    "For each fact about a user, list 5-8 single words or short phrases, in Russian AND English, that a later "
    "question which needs this fact is likely to contain but the fact itself does not: related objects, situations, "
    "synonyms. Example: «Не переносит лактозу» → [\"молоко\",\"кофе\",\"капучино\",\"сыр\",\"мороженое\",\"milk\","
    "\"coffee\",\"dairy\"]. Answer with ONLY a JSON object {\"<id>\": [\"…\"], …} for every id below.")

async def fill_concepts(completion: Completion, model: str, doc: Dict[str, Any]) -> int:
    rows = [i for i in doc["items"] if i["status"] == "active" and "concepts" not in i][:CONCEPT_BATCH]
    if not rows:
        return 0
    listing = "\n".join(f"{i['id']}: {i['text'][:300]}" for i in rows)
    answer = await completion(model, [{"role": "system", "content": CONCEPTS_PROMPT},
                                      {"role": "user", "content": listing}], prompt_caching=False)
    data = _json_in(answer)
    if not isinstance(data, dict):
        raise RuntimeError("concepts: not a JSON object")

    values = {i["id"]: (data.get(i["id"]) if isinstance(data.get(i["id"]), (list, str)) else []) for i in rows}
    out = await asyncio.to_thread(mem.set_concepts, values, profile_id=doc.get("profile_id") or "")
    return int(out.get("result") or 0)

DIARY_PROMPT = (
    "Summarize this finished conversation between a user and the MemuAI assistant for the user's session diary: "
    "ONE or TWO short lines (at most 200 characters), in the language of the conversation, saying what was done or "
    "decided (e.g. «Настраивали парсер вакансий hh.ru в Планировании, остановились на фильтре по зарплате»). "
    "No greetings, no date, no quotes, no secrets, passwords, addresses or health details. Answer with the summary only.")

def _text_of(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return mem.strip_markers(str(content or ""), partial=False)

def _dialog(chat: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [m for m in chat.get("messages") or [] if isinstance(m, dict) and not m.get("internal")
            and m.get("role") in ("user", "assistant") and _text_of(m).strip()]

def diary_candidates(doc: Dict[str, Any], chats: List[Dict[str, Any]], now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Big chats that went quiet and have no (fresh) diary entry, newest first."""
    stamp = now_ms or mem.now_ms()
    by_chat = {e.get("chat_id"): e for e in doc.get("diary") or [] if e.get("chat_id")}
    dismissed = set(doc.get("diary_dismissed") or [])
    out = []
    for chat in chats:
        chat_id = str(chat.get("id") or "")
        if not chat_id or chat.get("temp") or chat_id in dismissed:
            continue
        try:
            updated = int(float(chat.get("updated") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not updated or stamp - updated < IDLE_MS or stamp - updated > MAX_AGE_MS:
            continue
        rows = _dialog(chat)
        if len(rows) < MIN_MESSAGES or sum(len(_text_of(m)) for m in rows) < MIN_CHARS:
            continue
        entry = by_chat.get(chat_id)
        if entry is not None and len(rows) < int(entry.get("msgs") or 0) + REGROW:
            continue
        out.append(chat)
    out.sort(key=lambda c: float(c.get("updated") or 0), reverse=True)
    return out

def _transcript(chat: Dict[str, Any], limit: int = 7000) -> str:
    rows = _dialog(chat)
    parts: List[str] = []
    spent = 0
    for message in reversed(rows[-40:]):
        piece = ("User: " if message["role"] == "user" else "Assistant: ") + _text_of(message)[:900]
        if spent + len(piece) > limit and parts:
            break
        parts.append(piece)
        spent += len(piece)
    title = str(chat.get("title") or "").strip()
    section = {"work": "Work", "code": "MemuCode"}.get(str(chat.get("mode") or ""), "")
    head = (f"Chat title: {title}\n" if title else "") + (f"Section: {section}\n" if section else "")
    return head + "\n\n".join(reversed(parts))

async def summarize_chat(completion: Completion, model: str, chat: Dict[str, Any]) -> str:
    answer = await completion(model, [{"role": "system", "content": DIARY_PROMPT},
                                      {"role": "user", "content": _transcript(chat)}], prompt_caching=False)
    text = re.sub(r"<think>[\s\S]*?</think>", "", str(answer or ""), flags=re.I).strip().strip("«»\"'")
    text = re.sub(r"\s*\n\s*", " ", text)
    return text[:mem.MAX_DIARY_TEXT]

async def write_diary(completion: Completion, model: str, doc: Dict[str, Any]) -> int:
    try:
        from .pgdata import load_chats
    except Exception:
        return 0
    chats = await asyncio.to_thread(load_chats)
    done = 0
    for chat in diary_candidates(doc, chats)[:DIARY_PER_RUN]:
        text = await summarize_chat(completion, model, chat)
        if not text:
            continue
        try:
            await asyncio.to_thread(mem.save_diary, {
                "text": text, "chat_id": chat["id"], "chat_title": chat.get("title") or "",
                "service": "work" if chat.get("mode") == "work" else "code" if chat.get("mode") == "code" else "chat",
                "at": int(float(chat.get("updated") or 0)), "msgs": len(_dialog(chat))},
                profile_id=doc.get("profile_id") or "")
            done += 1
        except ValueError:
            continue
    return done

async def run_once() -> Dict[str, Any]:
    completion = _STATE["completion"]
    result = {"concepts": 0, "diary": 0}
    if completion is None:
        return result
    try:
        doc = await asyncio.to_thread(mem.load)
        if not doc.get("enabled", True):
            return result
        model = _default_model(doc)
        if not model:
            return result
        if doc["settings"].get("concepts_auto", True):
            result["concepts"] = await fill_concepts(completion, model, doc)
        if (doc["settings"].get("diary") or {}).get("enabled", True):
            result["diary"] = await write_diary(completion, model, doc)
        _STATE["last_error"] = ""
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _STATE["backoff_until"] = time.time() + BACKOFF
        _STATE["last_error"] = (str(exc) or type(exc).__name__)[:300]
        log.info("memory background work paused: %s", _STATE["last_error"])
    finally:
        _STATE["last_run"] = time.time()
    return result
