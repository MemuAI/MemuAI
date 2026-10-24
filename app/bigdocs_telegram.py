"""Big documents in the Telegram bot (Round 35, zone bigdocs; core: app/bigdocs.py).

Before: a document sent to the bot became its first 30 000 characters (a PDF — its first 80 pages) and the model
answered about the beginning as if it were the whole. Now:

* a document that fits (≈ 9 000 tokens) still goes into the prompt whole, with page marks;
* a bigger one is kept whole on this computer and becomes the chat's **active document**: the bot replies with a
  card «📄 name — 600 стр.» and the buttons «📖 Весь документ (≈ $0.05)» / «✖ Закрыть документ». Every question
  that follows (the caption of the document or later messages) gets the fragments a local search found, the model
  cites pages as [стр. N], and after the answer the bot sends the quotes of the cited pages as text;
* «Весь документ» runs the map-reduce of :mod:`app.bigdocs` with the progress shown by editing one message
  («📖 глава 7 из 20 ▓▓▓░░░»), a «⏹ Остановить» button, a confirmation above the cost threshold, and sends the
  result (cached: a second request is instant).

State: ``<DATA_DIR>/memuai/bigdocs/telegram.json`` — ``{chat_id: {id, name, pages, at}}``; a document stays active
for ``ACTIVE_HOURS`` or until «Закрыть документ» / another document.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import bigdocs

log = logging.getLogger("memuai.bigdocs")
INLINE_TOKENS = 9000
ACTIVE_HOURS = 12
_PENDING: Dict[str, Dict[str, Any]] = {}       # chat → {id, pages} of the question being answered (for the quotes)
_JOBS: Dict[str, str] = {}                      # chat → running whole-document job id


def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def _state_path():
    return bigdocs.store_dir() / "telegram.json"


def _load() -> Dict[str, Any]:
    try:
        return json.loads(_state_path().read_text("utf-8"))
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def active(chat_id: str) -> Optional[Dict[str, Any]]:
    row = _load().get(str(chat_id))
    if not row or time.time() - float(row.get("at") or 0) > ACTIVE_HOURS * 3600:
        return None
    return row


def set_active(chat_id: str, summary: Optional[Dict[str, Any]]) -> None:
    data = _load()
    if summary is None:
        data.pop(str(chat_id), None)
    else:
        data[str(chat_id)] = {"id": summary["id"], "name": summary.get("name", ""), "pages": summary.get("pages", 0),
                              "approx": bool(summary.get("approx_pages")), "at": time.time()}
    for key in [k for k, v in data.items() if time.time() - float(v.get("at") or 0) > 7 * 86400]:
        data.pop(key, None)
    _save(data)


def take_document(name: str, blob: bytes) -> Optional[Dict[str, Any]]:
    """Blocking. ``{"inline": text}`` for a document that fits, ``{"doc": summary}`` for a big one, None when
    bigdocs does not handle it (images, binaries, unreadable files → the old path)."""
    if not bigdocs.is_document(name):
        return None
    try:
        doc = bigdocs.ingest(blob, name)
    except bigdocs.BigDocError:
        return None
    if doc["tokens"] <= INLINE_TOKENS:
        # a one-page note needs no «[стр. 1]» mark (integrator r35: the old untrusted_file shape stays for it)
        return {"inline": bigdocs.full_text(doc, with_pages=len(doc["pages"]) > 1), "doc": bigdocs.summary(doc)}
    return {"doc": bigdocs.summary(doc)}


def card_text(summary: Dict[str, Any], lang: str, has_question: bool) -> str:
    pages = f"{'≈' if summary.get('approx_pages') else ''}{summary.get('pages', 0)}"
    head = f"📄 «{summary.get('name', '')}» — {pages} {_tr(lang, 'стр.', 'pages')}"
    if summary.get("chapters"):
        head += _tr(lang, f", разделов: {summary['chapters']}", f", sections: {summary['chapters']}")
    if has_question:
        return head + _tr(lang, "\nДокумент большой — отвечаю по найденным фрагментам с номерами страниц.",
                          "\nA big document — I answer from the fragments found, with page numbers.")
    return head + _tr(lang, "\n\nСпрашивайте — отвечу с цитатами и номерами страниц. «Весь документ» — пересказ по частям и свод.",
                      "\n\nAsk away — I answer with quotes and page numbers. “Whole document” reads it part by part.")


def card_keyboard(summary: Dict[str, Any], lang: str, model: str) -> Dict[str, Any]:
    label = "📖 " + _tr(lang, "Весь документ", "Whole document")
    try:
        doc = bigdocs.get_doc(summary["id"])
        est = bigdocs.estimate(doc, model, part_tokens=bigdocs.settings(bigdocs._prefs())["part_tokens"], lang=lang)
        if est["cached"]:
            label += _tr(lang, " (готово)", " (ready)")
        elif est["price_known"] and est["billing"] == "api":
            label += f" (≈ {est['cost_text']})"
    except Exception:
        pass
    return {"inline_keyboard": [[{"text": label, "callback_data": f"bd:whole:{summary['id'][:16]}"},
                                 {"text": "✖ " + _tr(lang, "Закрыть документ", "Close document"), "callback_data": "bd:close"}]]}


async def augment_async(chat_id: str, text: str, lang: str = "ru", model: str = "", complete: Any = None) -> str:
    """:func:`augment` with the cross-language step: a question in another language than the document (a Russian
    question over an English book) is translated for the local search first (one short model call)."""
    row = await asyncio.to_thread(active, chat_id)
    question = str(text or "").strip()
    alt = ""
    if row and question and not question.startswith("/") and model and complete is not None:
        try:
            doc = await asyncio.to_thread(bigdocs.get_doc, row["id"])
            alt = await bigdocs.translate_query(doc, question.split("\n\n[")[0][:2000], model, complete)
        except Exception:
            alt = ""
    return await asyncio.to_thread(augment, chat_id, text, lang, alt)


def augment(chat_id: str, text: str, lang: str = "ru", alt: str = "") -> str:
    """The question + the fragments of the chat's active document (when there is one)."""
    row = active(chat_id)
    question = str(text or "").strip()
    if not row or not question or question.startswith("/"):
        return text
    try:
        doc = bigdocs.get_doc(row["id"])
    except bigdocs.BigDocError:
        set_active(chat_id, None)
        return text
    ctx = bigdocs.qa_context(doc, question.split("\n\n[")[0][:2000], k=bigdocs.settings(bigdocs._prefs())["k"], lang=lang,
                             extra=[alt] if alt else None)
    _PENDING[str(chat_id)] = {"id": doc["id"], "pages": ctx["pages"]}
    return (f"{question}\n\n<untrusted_file>\n{ctx['block']}\n</untrusted_file>\n{ctx['instruction']}")


async def after_answer(api: Any, chat_id: str, answer: str, lang: str = "ru") -> None:
    """Quotes of the cited pages as a small follow-up message (Telegram has no hover)."""
    pending = _PENDING.pop(str(chat_id), None)
    if not pending or not answer:
        return
    try:
        doc = await asyncio.to_thread(bigdocs.get_doc, pending["id"])
    except bigdocs.BigDocError:
        return
    cites = bigdocs.cite(doc, answer, [{"page": p} for p in pending["pages"]])
    text = bigdocs.citations_text(cites, lang)
    if not text:
        return
    await api.call("sendMessage", chat_id=chat_id, text="📎 " + _tr(lang, "Цитаты:", "Quotes:") + "\n" + text,
                   disable_notification=True, link_preview_options={"is_disabled": True})


def _bar(done: int, total: int, width: int = 12) -> str:
    filled = int(round(width * done / max(1, total)))
    return "▓" * filled + "░" * (width - filled)


async def _edit(api: Any, chat_id: str, message_id: int, text: str, markup: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
    if markup is not None:
        payload["reply_markup"] = markup
    try:
        await api.call("editMessageText", **payload)
    except Exception as exc:          # "message is not modified" and friends
        log.debug("bigdocs progress edit: %s", exc)


async def handle_callback(api: Any, chat_id: str, message_id: int, data: str, *, lang: str, model: str,
                          complete: Callable[[str, List[Dict[str, Any]]], Awaitable[str]],
                          send_text: Callable[[str], Awaitable[Any]]) -> str:
    """``bd:whole:<id16>``, ``bd:go:<id16>``, ``bd:stop``, ``bd:close`` → a toast text."""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "close":
        set_active(chat_id, None)
        await _edit(api, chat_id, message_id, _tr(lang, "📄 Документ закрыт.", "📄 Document closed."))
        return _tr(lang, "Документ закрыт", "Document closed")
    if action == "stop":
        job_id = _JOBS.get(str(chat_id))
        return (_tr(lang, "Останавливаю…", "Stopping…") if job_id and bigdocs.cancel_job(job_id)
                else _tr(lang, "Уже не идёт", "Not running"))
    row = active(chat_id)
    if not row or not row["id"].startswith(parts[2] if len(parts) > 2 else "-"):
        return _tr(lang, "Документ больше не активен — пришлите его снова", "The document is not active any more — send it again")
    try:
        doc = await asyncio.to_thread(bigdocs.get_doc, row["id"])
    except bigdocs.BigDocError as exc:
        return exc.text(lang)
    conf = bigdocs.settings(bigdocs._prefs())
    est = await asyncio.to_thread(bigdocs.estimate, doc, model, part_tokens=conf["part_tokens"],
                                  confirm_usd=conf["confirm_usd"], lang=lang)
    if action == "whole" and est["needs_confirm"]:
        cost = est["cost_text"] if est["price_known"] else _tr(lang, "цена неизвестна", "price unknown")
        text = _tr(lang, f"📖 Весь документ «{doc['name']}»: {est['parts']} частей, {est['requests']} запросов, "
                         f"≈ {est['input_tokens'] // 1000} тыс. токенов, {cost}. Запустить?",
                   f"📖 Whole document “{doc['name']}”: {est['parts']} parts, {est['requests']} requests, "
                   f"≈ {est['input_tokens'] // 1000}K tokens, {cost}. Start?")
        await api.call("sendMessage", chat_id=chat_id, text=text, reply_markup={"inline_keyboard": [[
            {"text": "▶️ " + _tr(lang, "Запустить", "Start"), "callback_data": f"bd:go:{row['id'][:16]}"},
            {"text": "✖ " + _tr(lang, "Не нужно", "Cancel"), "callback_data": "bd:nogo"}]]})
        return ""
    if str(chat_id) in _JOBS and bigdocs.JOBS.get(_JOBS[str(chat_id)]) and bigdocs.JOBS[_JOBS[str(chat_id)]].status == "running":
        return _tr(lang, "Уже читаю — подождите", "Already reading — please wait")
    asyncio.ensure_future(_run_whole(api, chat_id, doc, model, complete, send_text, lang, conf["part_tokens"]))
    return _tr(lang, "Читаю весь документ…", "Reading the whole document…")


async def _run_whole(api: Any, chat_id: str, doc: Dict[str, Any], model: str, complete, send_text, lang: str,
                     part_tokens: int) -> None:
    stop_kb = {"inline_keyboard": [[{"text": "⏹ " + _tr(lang, "Остановить", "Stop"), "callback_data": "bd:stop"}]]}
    head = f"📖 «{doc['name']}»"
    sent = await api.call("sendMessage", chat_id=chat_id, text=head + "\n" + _tr(lang, "Готовлюсь…", "Preparing…"),
                          reply_markup=stop_kb, disable_notification=True)
    message_id = int((sent or {}).get("message_id") or 0) if isinstance(sent, dict) else 0
    job = bigdocs.start_job(doc, model, complete, prompt="", lang=lang, part_tokens=part_tokens)
    _JOBS[str(chat_id)] = job.id
    last_edit = 0.0
    try:
        async for _index, event in bigdocs.job_events(job):
            stage = event.get("stage")
            if stage in ("map", "reduce", "plan") and message_id and time.monotonic() - last_edit > 2.5:
                last_edit = time.monotonic()
                if stage == "reduce":
                    line = event.get("label") or _tr(lang, "Свожу…", "Combining…")
                else:
                    line = f"{job.unit} {min(job.total, job.done + (0 if stage == 'plan' else 0))} " \
                           f"{_tr(lang, 'из', 'of')} {job.total}  {_bar(job.done, job.total)}"
                await _edit(api, chat_id, message_id, f"{head}\n{line}", stop_kb)
    finally:
        _JOBS.pop(str(chat_id), None)
    if job.status == "done":
        if message_id:
            await _edit(api, chat_id, message_id, head + "\n✅ " + _tr(lang, f"Прочитано: {job.total} из {job.total}",
                                                                      f"Read: {job.total} of {job.total}"), {"inline_keyboard": []})
        await send_text(job.result)
    elif job.status == "cancelled":
        await _edit(api, chat_id, message_id, head + "\n⏹ " + _tr(
            lang, f"Остановлено: готово {job.done} из {job.total}. Нажмите «Весь документ» ещё раз — продолжу с этого места.",
            f"Stopped: {job.done} of {job.total} done. Tap “Whole document” again to resume."),
            {"inline_keyboard": [[{"text": "📖 " + _tr(lang, "Продолжить", "Resume"), "callback_data": f"bd:go:{doc['id'][:16]}"}]]})
    else:
        await _edit(api, chat_id, message_id, head + "\n⚠️ " + (job.error or _tr(lang, "Ошибка", "Error"))[:500],
                    {"inline_keyboard": [[{"text": "↻ " + _tr(lang, "Ещё раз", "Retry"), "callback_data": f"bd:go:{doc['id'][:16]}"}]]})


def completion_from(chat_service: Any):
    """``complete(model, messages)`` over the Proxy's own chat service (the bot runs inside the Proxy)."""
    async def complete(model: str, messages: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        stream = chat_service.stream_completion(model, messages, {"web_search": False, "thinking": False})
        try:
            async for chunk in stream:
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        parts.append(str(delta["content"]))
        finally:
            if hasattr(stream, "aclose"):
                try:
                    await stream.aclose()
                except Exception:
                    pass
        try:
            from .assistant_commands import clean_ai_text
            return clean_ai_text("".join(parts))
        except Exception:
            return "".join(parts)
    return complete
