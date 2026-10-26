"""Round 29: more of MemuAI in the Mini App (``app/tgapp_api.py`` registers these operations).

* ``voice.status`` / ``voice.transcribe`` — audio from the Mini App (recorded with MediaRecorder or an
  uploaded file) transcribed by the local Whisper model on the computer (``telegram_voice.py``);
* ``chats.search`` — full-text search in titles and messages of the profile's chats, with snippets;
* ``chats.edit`` — edit one of your messages and answer it again (later messages are replaced, like the
  desktop's «edit and resend»);
* ``schedules.list`` / ``schedules.toggle`` / ``schedules.run`` — the scheduled tasks of MemuAI;
* ``prompts.list`` — the prompt library (insert a prompt into the message box);
* ``weblogin.*`` — the browser sign-in confirmed from Telegram (``tgapp_weblogin.py``).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Dict, List

from . import telegram_voice, tgapp_weblogin
from . import tgapp_chat as tchat

SEARCH_LIMIT = 60
SNIPPET = 90
voice_limiter_hits: Dict[str, List[float]] = {}

def _snippet(text: str, needle: str) -> str:
    flat = " ".join(str(text or "").split())
    at = flat.lower().find(needle)
    if at < 0:
        return flat[:SNIPPET * 2]

    start = max(0, at - 40)
    if start:
        space = flat.find(" ", start, at)
        start = space + 1 if space >= 0 else start
    end = min(len(flat), at + len(needle) + SNIPPET + 50)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")

def search_chats(rows: List[Dict[str, Any]], query: str, *, limit: int = SEARCH_LIMIT) -> List[Dict[str, Any]]:
    needle = " ".join(str(query or "").lower().split())
    if len(needle) < 2:
        return []
    hits: List[Dict[str, Any]] = []
    for chat in rows:
        if str(chat.get("mode") or "chat") != "chat" or chat.get("temp"):
            continue
        title = str(chat.get("title") or "")
        found_title = needle in title.lower()
        matches = []
        for index, message in enumerate(chat.get("messages") or []):
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant") or message.get("internal"):
                continue
            content = str(message.get("content") or "")
            if needle in content.lower():
                matches.append({"i": index, "role": message.get("role"),
                                "snippet": _snippet(tchat.clean_answer(content) if message.get("role") == "assistant"
                                                    else content, needle)})
                if len(matches) >= 3:
                    break
        if found_title or matches:
            hits.append({"id": str(chat.get("id")), "title": title[:120], "updated": float(chat.get("updated") or 0),
                         "title_match": found_title, "matches": matches,
                         "score": (2 if found_title else 0) + len(matches)})
    hits.sort(key=lambda row: (-row["score"], -row["updated"]))
    return hits[:limit]

GLOBAL_TYPES = ("chat", "memory", "prompt", "schedule", "character", "panel", "quick", "work", "code", "doc")
GLOBAL_PER_TYPE = 4

def _compact_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """A search result trimmed to what the phone draws (no scores, no internal meta)."""
    open_ = item.get("open") if isinstance(item.get("open"), dict) else {}
    hits = []
    for hit in (item.get("hits") or [])[:2]:
        if isinstance(hit, dict):
            hits.append({"snippet": str(hit.get("snippet") or "")[:400], "highlights": hit.get("highlights") or [],
                         "role": str((hit.get("meta") or {}).get("role") or "")})
    return {"id": str(item.get("id") or ""), "type": str(item.get("type") or ""),
            "title": str(item.get("title") or "")[:160], "subtitle": str(item.get("subtitle") or "")[:160],
            "title_highlights": item.get("title_highlights") or [], "snippet": str(item.get("snippet") or "")[:400],
            "highlights": item.get("highlights") or [], "hits": hits, "ts": float(item.get("ts") or 0),
            "chat_id": str(open_.get("chat_id") or "") if open_.get("action") == "chat" and open_.get("mode") == "chat" else ""}

def global_search(query: str, *, lang: str = "", per_type: int = GLOBAL_PER_TYPE) -> Dict[str, Any]:
    """Grouped results of MemuAI's global search for the Mini App (desktop-only sources excluded)."""
    from . import search_api
    data = search_api.remote_search(query, types=list(GLOBAL_TYPES), limit=80, lang=lang, per_group=2)
    groups = []
    for group in data.get("groups") or []:
        items = [_compact_item(row) for row in (group.get("items") or [])[:per_type]]
        if items:
            groups.append({"type": group.get("type"), "label": list(group.get("label") or []),
                           "count": int(group.get("count") or len(items)), "items": items})
    return {"groups": groups, "total": int(data.get("total") or 0), "suggestion": str(data.get("suggestion") or ""),
            "layout_switched": str(data.get("layout_switched") or "")}

def merge_chat_hits(hits: List[Dict[str, Any]], found: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chats the index found by word forms / typos / layout that the plain substring search missed."""
    known = {row["id"] for row in hits}
    chats = {str(c.get("id")): c for c in rows if isinstance(c, dict)}
    extra = []
    for group in found.get("groups") or []:
        if group.get("type") != "chat":
            continue
        for item in group["items"]:
            chat = chats.get(item.get("chat_id") or "")
            if not chat or item["chat_id"] in known or chat.get("temp"):
                continue
            matches = [{"i": -1, "role": h["role"], "snippet": h["snippet"], "highlights": h["highlights"]}
                       for h in item["hits"] if h["snippet"]]
            extra.append({"id": item["chat_id"], "title": item["title"], "updated": float(chat.get("updated") or 0),
                          "title_match": bool(item["title_highlights"]), "matches": matches,
                          "title_highlights": item["title_highlights"], "score": 0, "fuzzy": True})
    return hits + extra

MEMORY_ITEM_KEYS = ("id", "text", "category", "scope", "confidence", "pinned", "status", "created_at", "updated_at",
                    "source", "tags", "sensitive", "reason", "op")

def memory_reply(profile: Dict[str, Any], active: str) -> Dict[str, Any]:
    """What the Mini App memory page draws: the legacy view + memory v2 facts and the labels of their categories."""
    reply: Dict[str, Any] = {"memory": tchat.profile_memory(profile), "editable": str(profile.get("id")) == active}
    try:
        from . import memory_v2
        doc = memory_v2.load(str(profile.get("id") or active))
        reply["items"] = [{k: item.get(k) for k in MEMORY_ITEM_KEYS if k in item}
                          for item in doc["items"] if item.get("status") in ("active", "pending")]
        reply["categories"] = {k: list(v) for k, v in memory_v2.CATEGORY_LABELS.items()}
        reply["mode"] = str(((doc.get("settings") or {}).get("mode")) or "")
    except Exception:
        pass
    return reply

def _when(task: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("daily_at", "interval_minutes", "run_at"):
        if task.get(key) not in (None, ""):
            out[key] = task.get(key)
    return out

def _rule_fields(task: Dict[str, Any], lang: str, by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Planning 2.0: the same human summary and status inputs the desktop «Планирование» card shows."""
    from . import schedule_rules as rules
    rule = task.get("rule") if isinstance(task.get("rule"), dict) else rules.legacy_rule(task)
    out: Dict[str, Any] = {"rule": isinstance(task.get("rule"), dict), "summary": "", "event_only": False}
    if rule:
        out["summary"] = rules.summary(rule, lang, by_id)[:400]
        try:
            out["event_only"] = not rules.has_time_triggers(rules.normalize_rule(rule))
        except rules.RuleError:
            pass
    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
    reason = state.get("reason") if isinstance(state.get("reason"), dict) else {}
    out["state"] = {"status": str(state.get("status") or ""),
                    "reason": str(reason.get("en" if lang == "en" else "ru") or "")[:300]}
    out["run_count"] = int(task.get("run_count") or 0) if str(task.get("run_count") or "0").isdigit() else 0
    return out

def _flow_fields(task: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """Round 35 (flows): the steps of a task, read-only lines for the Mini App («3.1 · Telegram: …»)."""
    if not (isinstance(task.get("steps"), list) and task.get("steps")):
        return {}
    try:
        from . import flow_engine as fe
        steps = fe.normalize_steps(task["steps"])
        numbers = fe.numbering(steps)
        rows = [{"n": numbers.get(s["id"], ""), "type": s["type"], "cat": fe.STEP_TYPES[s["type"]]["cat"],
                 "text": fe.describe(s, lang)[:160], "depth": numbers.get(s["id"], "").count(".")} for s in fe.walk(steps)]
        return {"steps": rows[:60]}
    except Exception:
        return {}

def schedule_rows(lang: str = "ru") -> List[Dict[str, Any]]:
    from .pgdata import load_schedule_results, load_schedules
    tasks = load_schedules()
    by_id = {str(t.get("id")): t for t in tasks}
    try:
        from .schedule_runtime import handoff_items
        active = {str(it.get("task_id")): str(it.get("status")) for it in handoff_items()
                  if it.get("status") in ("queued", "running")}
    except Exception:
        active = {}
    results: Dict[str, Dict[str, Any]] = {}
    try:
        for row in load_schedule_results():
            task_id = str(row.get("task_id") or "")

            stamp = float(row.get("ts") or row.get("created") or row.get("at") or 0)
            seen = results.get(task_id) or {}
            if task_id and (not seen or stamp > float(seen.get("ts") or seen.get("created") or seen.get("at") or 0)):
                results[task_id] = row
    except Exception:
        results = {}
    out = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        enabled = task.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("", "0", "false", "no", "off")
        last = results.get(task_id) or {}
        out.append({"id": task_id, "name": str(task.get("name") or "")[:160],
                    "prompt": str(task.get("prompt") or task.get("reminder_text") or "")[:600],
                    "kind": str(task.get("kind") or "chat"), "enabled": bool(enabled),
                    "next_run": task.get("next_run"), "last_run": task.get("last_run"), "model": str(task.get("model") or ""),
                    "notify_telegram": bool(task.get("notify_telegram")), **_when(task),
                    **_rule_fields(task, lang, by_id), **_flow_fields(task, lang), "active": active.get(task_id, ""),
                    "last": {"ok": last.get("ok"), "text": str(last.get("text") or "")[:400],
                             "at": last.get("ts") or last.get("created") or last.get("at"), "test": bool(last.get("test"))}
                    if last else None})
    return out

def _set_schedule(task_id: str, change: Callable[[Dict[str, Any]], None]) -> bool:
    from .pgdata import _mutate_schedules
    found: List[bool] = []

    def mutate(tasks: List[Dict[str, Any]]) -> bool:
        for task in tasks:
            if str(task.get("id") or "") == task_id:
                change(task)
                found.append(True)
                return True
        return False

    _mutate_schedules(mutate)
    return bool(found)

def prompt_rows(lang: str) -> List[Dict[str, Any]]:
    from .pgdata import load_prompts
    data = load_prompts()
    rows = []
    for row in data.get("prompts") or []:
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            continue
        name = str((row.get("name_ru") if lang == "ru" else "") or row.get("name") or row.get("id"))
        description = str((row.get("description_ru") if lang == "ru" else "") or row.get("description") or "")
        rows.append({"id": str(row.get("id")), "name": name[:160], "description": description[:400],
                     "text": str(row.get("text") or "")[:20000],

                     "default": str(row.get("id")) == str(data.get("default_id") or "")})
    return rows[:120]

def register(op: Callable[[str], Callable], ApiError: Any, *, t: Callable, lang_of: Callable,
             profile_id_of: Callable, client_key: Callable) -> Dict[str, Callable]:
    """Registers the authenticated operations; returns the public ones (no session) for the dispatcher."""

    @op("voice.status")
    async def voice_status(ctx, body):
        return await asyncio.to_thread(telegram_voice.status, ctx["prefs"], lang=lang_of(ctx["prefs"]))

    @op("voice.transcribe")
    async def voice_transcribe(ctx, body):
        prefs = ctx["prefs"]
        lang = lang_of(prefs)
        now = time.monotonic()
        hits = [x for x in voice_limiter_hits.get(ctx["user"], []) if now - x < 60]
        if len(hits) >= 12:
            raise ApiError(429, "rate_limited", t(prefs, "Слишком много записей подряд — подождите минуту",
                                                  "Too many recordings in a row — wait a minute"))
        voice_limiter_hits[ctx["user"]] = hits + [now]
        try:
            audio, mime = telegram_voice.decode_upload(body.get("audio"))
        except ValueError:
            raise ApiError(400, "bad_audio", t(prefs, "Это не аудио", "This is not audio")) from None
        try:
            text = await telegram_voice.transcribe(audio, mime, prefs, lang=lang, name=str(body.get("name") or ""))
        except telegram_voice.VoiceUnavailable as exc:
            raise ApiError(409, "voice_unavailable", str(exc)) from None
        except RuntimeError as exc:
            raise ApiError(422, "voice_failed", str(exc)) from None
        return {"text": text, "review": bool(prefs.get("telegram_voice_review"))}

    @op("chats.search")
    async def chats_search(ctx, body):
        from .pgdata import load_chats
        query = str(body.get("query") or "")[:200]
        profile_id = profile_id_of(ctx, body)
        rows = await asyncio.to_thread(load_chats, profile_id)
        hits = await asyncio.to_thread(search_chats, rows, query)
        out: Dict[str, Any] = {"query": query, "hits": hits}

        active, _items = tchat.profiles()
        if body.get("global", True) and len(query.strip()) >= 2 and profile_id == active:
            try:
                found = await asyncio.to_thread(global_search, query, lang=lang_of(ctx["prefs"]))
            except Exception:
                found = None
            if found:
                out["hits"] = merge_chat_hits(hits, found, rows)
                out["global"] = [g for g in found["groups"] if g["type"] != "chat"]
                out["suggestion"] = found["suggestion"]
        return out

    @op("memory.item")
    async def memory_item(ctx, body):
        """One fact from the phone: approve / reject a pending one, pin / unpin, edit, delete, restore."""
        from . import memory_v2
        prefs = ctx["prefs"]
        profile = tchat.profile_by_id(profile_id_of(ctx, body))
        active, _items = tchat.profiles()
        if str(profile.get("id")) != active:
            raise ApiError(400, "not_active", t(prefs, "Память этого профиля меняется на компьютере",
                                                "This profile's memory can only be changed on the computer"))
        item_id = str(body.get("id") or "")[:40]
        action = str(body.get("action") or "")
        try:
            if action == "approve":
                await asyncio.to_thread(memory_v2.approve, item_id, text=str(body.get("text") or "")[:2000])
            elif action == "reject":
                await asyncio.to_thread(memory_v2.reject, item_id)
            elif action in ("pin", "unpin"):
                await asyncio.to_thread(memory_v2.update_item, item_id, {"pinned": action == "pin"})
            elif action == "edit":
                await asyncio.to_thread(memory_v2.update_item, item_id, {"text": str(body.get("text") or "")[:2000]})
            elif action in ("delete", "restore"):
                await asyncio.to_thread(memory_v2.update_item, item_id,
                                        {"status": "deleted" if action == "delete" else "active"})
            else:
                raise ApiError(400, "bad_value", t(prefs, "Неизвестное действие", "Unknown action"))
        except KeyError:
            raise ApiError(404, "not_found", t(prefs, "Факт не найден", "Fact not found")) from None
        except ValueError as exc:
            if str(exc) == "secret":
                raise ApiError(400, "secret", t(prefs, "Похоже на пароль или ключ — такое не запоминается",
                                                "This looks like a password or a key — it is never stored")) from None
            raise ApiError(400, "bad_value", t(prefs, "Пустой текст", "Empty text")) from None
        return await asyncio.to_thread(memory_reply, profile, active)

    @op("search.preview")
    async def search_preview(ctx, body):
        from . import search_api
        result_id = str(body.get("id") or "")[:300]
        active, _items = tchat.profiles()
        if profile_id_of(ctx, body) != active or result_id.startswith(("file:", "project:", "module:")):
            raise ApiError(404, "not_found", t(ctx["prefs"], "Не найдено", "Not found"))
        data = await asyncio.to_thread(search_api.remote_preview, result_id, str(body.get("query") or "")[:200])
        if not data:
            raise ApiError(404, "not_found", t(ctx["prefs"], "Не найдено", "Not found"))
        blocks = [{"text": str(b.get("text") or "")[:4000], "highlights": b.get("highlights") or [],
                   "role": str(b.get("role") or ""), "label": str(b.get("label") or ""), "hit": bool(b.get("hit"))}
                  for b in (data.get("blocks") or [])[:12] if isinstance(b, dict)]
        return {"id": data.get("id"), "type": data.get("type"), "title": str(data.get("title") or "")[:200],
                "subtitle": str(data.get("subtitle") or "")[:200], "blocks": blocks}

    @op("chats.edit")
    async def chats_edit(ctx, body):
        """Edit the user message at ``index`` and answer it again; later messages are replaced."""
        from .pgdata import patch_chat
        prefs = ctx["prefs"]
        lang = lang_of(prefs)
        profile_id = profile_id_of(ctx, body)
        chat_id = str(body.get("id") or "")
        text = str(body.get("text") or "").strip()[:100_000]
        if not text:
            raise ApiError(400, "bad_value", t(prefs, "Пустое сообщение", "Empty message"))
        if tchat.jobs.active_for_chat(chat_id):
            raise ApiError(409, "busy", t(prefs, "В этом чате уже идёт ответ", "An answer is already running in this chat"))
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise ApiError(400, "bad_value", "Нет сообщения", "No message") from None
        problem: List[str] = []

        def change(row: Dict[str, Any]) -> bool:
            messages = [dict(item) for item in row.get("messages") or [] if isinstance(item, dict)]
            if not 0 <= index < len(messages) or messages[index].get("role") != "user":
                problem.append("index")
                return False
            edited = {k: v for k, v in messages[index].items() if k != "id"}
            edited["content"] = text
            edited["createdAt"] = tchat.now_ms()
            edited["edited"] = True

            row["messages"] = messages
            from . import chat_tree
            chat_tree.begin_version(row, index)
            row["messages"] = list(row.get("messages") or [])[:index] + [edited]
            row["updated"] = tchat.now_ms()
            return True

        saved = await asyncio.to_thread(patch_chat, chat_id, change, profile_id)
        if saved is None or problem:
            raise ApiError(404, "not_found", t(prefs, "Сообщение не найдено — обновите чат", "Message not found — refresh the chat"))
        try:
            job = tchat.start_send(owner=ctx["user"], profile_id=profile_id, chat_id=chat_id, text="", attachments=[],
                                   model=str(body.get("model") or ""),
                                   web_search=body.get("web_search") if isinstance(body.get("web_search"), bool) else None,
                                   thinking=body.get("thinking") if isinstance(body.get("thinking"), bool) else None,
                                   temp=False, meme_id="", regenerate=True, temp_history=None, lang=lang)
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from None
        return {"job": job.id, "chat_id": chat_id, "model": job.meta["model"]}

    @op("chats.version")
    async def chats_version(ctx, body):
        """Round 35 (branches): show another version of a message — ``{id, key, to}`` (``to`` = 0-based version)."""
        from .pgdata import patch_chat
        from . import chat_tree
        prefs = ctx["prefs"]
        profile_id = profile_id_of(ctx, body)
        chat_id = str(body.get("id") or "")
        key = str(body.get("key") or "")
        try:
            target = int(body.get("to"))
        except (TypeError, ValueError):
            raise ApiError(400, "bad_value", "Нет версии", "No version") from None
        if tchat.jobs.active_for_chat(chat_id):
            raise ApiError(409, "busy", t(prefs, "Дождитесь окончания ответа", "Wait for the answer to finish"))

        def change(row: Dict[str, Any]) -> bool:
            if not chat_tree.switch_to(row, key, target):
                return False
            chat_tree.normalize(row)
            if int(row.get("contextSummaryThrough") or 0) > len(chat_tree.active_path(row)):
                row["contextSummary"], row["contextSummaryThrough"] = "", 0
            row["updated"] = tchat.now_ms()
            return True

        saved = await asyncio.to_thread(patch_chat, chat_id, change, profile_id)
        if saved is None:
            raise ApiError(404, "not_found", t(prefs, "Версия не найдена — обновите чат", "Version not found — refresh the chat"))
        return {"ok": True, "chat_id": chat_id}

    @op("schedules.list")
    async def schedules_list(ctx, body):
        return {"tasks": await asyncio.to_thread(schedule_rows, lang_of(ctx["prefs"]))}

    @op("schedules.toggle")
    async def schedules_toggle(ctx, body):
        task_id = str(body.get("id") or "")
        enabled = bool(body.get("enabled"))
        def toggle(task: Dict[str, Any]) -> None:

            was = task.get("enabled", True) not in (False, "false", 0)
            task["enabled"] = enabled
            if enabled and not was:
                if isinstance(task.get("rule"), dict):
                    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
                    task["rule_state"] = {k: v for k, v in state.items() if k in ("seen_done",)}
                    if state.get("status") == "finished":
                        task["run_count"] = 0
                        task["fail_streak"] = 0
                task.pop("next_run", None)

        if not await asyncio.to_thread(_set_schedule, task_id, toggle):
            raise ApiError(404, "not_found", "Задача не найдена", "Task not found")
        return {"tasks": await asyncio.to_thread(schedule_rows, lang_of(ctx["prefs"]))}

    @op("schedules.run")
    async def schedules_run(ctx, body):
        task_id = str(body.get("id") or "")

        def run_now(task: Dict[str, Any]) -> None:

            if isinstance(task.get("rule"), dict):
                task["run_now"] = time.time()
            else:
                task["next_run"] = time.time()
                if task.get("enabled") in (False, "false", 0, None, ""):
                    task["enabled"] = True

        if not await asyncio.to_thread(_set_schedule, task_id, run_now):
            raise ApiError(404, "not_found", "Задача не найдена", "Task not found")
        return {"ok": True}

    @op("schedules.parse")
    async def schedules_parse(ctx, body):

        from . import schedule_rules as rules
        from . import schedule_rules_api as plan_api
        from .tgapp_chat import complete, pick_model
        lang = lang_of(ctx["prefs"])
        text = str(body.get("text") or "").strip()[:2000]
        if not text:
            raise ApiError(400, "bad_request", "Опишите, что и когда сделать", "Describe what to do and when")
        model = await asyncio.to_thread(pick_model, str(body.get("model") or ""), ctx["prefs"])
        if not model:
            raise ApiError(400, "no_model", "Нет доступной модели — добавьте провайдера в MemuAI",
                           "No model available — add a provider in MemuAI")

        async def completion(model_id, messages, **_kw):
            return await complete(model_id, messages)

        tasks = await asyncio.to_thread(plan_api._load_tasks)
        try:
            draft = await plan_api.parse_text(text, model, completion, lang=lang, tasks=tasks)
            payload = plan_api.preview_payload(draft, tasks, lang)
        except rules.RuleError as exc:
            raise ApiError(422, "not_a_schedule", exc.ru, exc.en) from None
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 — the provider's own words help the user (429, no key…)
            raise ApiError(502, "model_error", "Модель не ответила: " + str(exc)[:300],
                           "The model did not answer: " + str(exc)[:300]) from None
        draft["rule"] = payload["rule"]
        draft["model"] = draft.get("model") or model
        draft["notify_telegram"] = draft.get("kind") != "flow"
        return {"task": draft, "summary": payload["summary"], "next": payload["next"][:5],
                "events": payload["events"], "warnings": payload["warnings"]}

    @op("schedules.create")
    async def schedules_create(ctx, body):
        from . import schedule_rules as rules
        from . import schedule_rules_api as plan_api
        lang = lang_of(ctx["prefs"])
        raw = body.get("task") if isinstance(body.get("task"), dict) else {}
        raw = {**raw, "created_by": "telegram"}
        raw.pop("id", None)
        try:
            task_id = await asyncio.to_thread(plan_api.save_task, raw, lang)
        except rules.RuleError as exc:
            raise ApiError(400, "bad_rule", exc.ru, exc.en) from None
        return {"id": task_id, "tasks": await asyncio.to_thread(schedule_rows, lang)}

    @op("schedules.delete")
    async def schedules_delete(ctx, body):
        from .pgdata import _mutate_schedules
        task_id = str(body.get("id") or "")
        found: List[bool] = []

        def drop(tasks: List[Dict[str, Any]]) -> bool:
            for task in list(tasks):
                if str(task.get("id") or "") == task_id:
                    tasks.remove(task)
                    found.append(True)
                    return True
            return False

        await asyncio.to_thread(_mutate_schedules, drop)
        if not found:
            raise ApiError(404, "not_found", "Задача не найдена", "Task not found")
        return {"tasks": await asyncio.to_thread(schedule_rows, lang_of(ctx["prefs"]))}

    @op("prompts.list")
    async def prompts_list(ctx, body):
        return {"prompts": await asyncio.to_thread(prompt_rows, lang_of(ctx["prefs"]))}

    def _web_enabled(ctx) -> None:
        if ctx["config"].get("web_login") is False:
            raise ApiError(403, "web_off", t(ctx["prefs"], "Вход из браузера выключен на компьютере",
                                             "Browser sign-in is off on the computer"))

    @op("weblogin.inspect")
    async def weblogin_inspect(ctx, body):
        _web_enabled(ctx)
        info = tgapp_weblogin.inspect(str(body.get("code") or ""))
        if info is None:
            raise ApiError(404, "no_request", t(ctx["prefs"], "Код не найден или устарел — обновите страницу в браузере",
                                                "The code is unknown or expired — reload the page in the browser"))
        return info

    @op("weblogin.approve")
    async def weblogin_approve(ctx, body):
        _web_enabled(ctx)
        approve = body.get("approve") is not False
        if not tgapp_weblogin.decide(str(body.get("code") or ""), ctx["user"], approve):
            raise ApiError(404, "no_request", t(ctx["prefs"], "Код не найден или устарел — обновите страницу в браузере",
                                                "The code is unknown or expired — reload the page in the browser"))
        return {"ok": True, "approved": approve}

    from . import tg_webapp

    def _prefs():
        from .pgdata import load_preferences
        return load_preferences()

    def _issue(user: str):
        config = tg_webapp.load_config()
        return tg_webapp.issue_token(user, config["secret"], ttl=tgapp_weblogin.WEB_SESSION_TTL)

    def _guard(request) -> Dict[str, Any]:
        config = tg_webapp.load_config()
        prefs = _prefs()
        if not config["enabled"]:
            raise ApiError(403, "disabled", t(prefs, "Mini App выключен в настройках MemuAI",
                                              "The Mini App is turned off in MemuAI settings"))
        if config.get("web_login") is False:
            raise ApiError(403, "web_off", t(prefs, "Вход из браузера выключен на компьютере",
                                             "Browser sign-in is off on the computer"))
        return prefs

    async def weblogin_start(request, body):
        prefs = _guard(request)
        agent = re.sub(r"[\x00-\x1f]", " ", request.headers.get("user-agent", ""))[:300]
        try:
            out = tgapp_weblogin.start(client_key(request), agent)
        except OverflowError:
            raise ApiError(429, "rate_limited", t(prefs, "Слишком много запросов входа — подождите",
                                                  "Too many sign-in requests — please wait")) from None
        out["lang"] = lang_of(prefs)
        return out

    async def weblogin_poll(request, body):
        _guard(request)
        result = tgapp_weblogin.poll(str(body.get("request_id") or ""), _issue)
        if result.get("status") == "approved":
            prefs = _prefs()
            if not tg_webapp.is_authorized_user(tg_webapp.verify_token(result["token"], tg_webapp.load_config()["secret"]).get("u"), prefs):
                return {"status": "denied"}
            result["lang"] = lang_of(prefs)
        return result

    async def weblogin_redeem(request, body):
        prefs = _guard(request)
        user = tgapp_weblogin.redeem(str(body.get("nonce") or ""))
        if not user or not tg_webapp.is_authorized_user(user, prefs):
            raise ApiError(401, "link_expired", t(prefs, "Ссылка уже использована или устарела — попросите новую командой /web",
                                                  "The link was already used or has expired — ask for a new one with /web"))
        token, expires = _issue(user)
        return {"token": token, "expires": expires, "lang": lang_of(prefs)}

    return {"weblogin.start": weblogin_start, "weblogin.poll": weblogin_poll, "weblogin.redeem": weblogin_redeem}
