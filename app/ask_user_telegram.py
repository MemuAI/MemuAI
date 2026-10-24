"""ask_user in the Telegram bot (Round 35): the form becomes inline buttons under one message.

* choice / bool — chips that toggle (✅), multi — ☑️/⬜ checkboxes, «✏️ Другое…» and text / number / date
  fields ask for a reply message (date also has «Сегодня» / «Завтра»); «✅ Готово» and «⏭ Пропустить — решай сам».
* Pure state functions (``new_state`` / ``render`` / ``on_callback`` / ``on_text`` / ``collect``) are tested
  without Telegram; :func:`ask` drives one form in a chat through ``BotApi.call`` and waits on
  :data:`app.ask_user.STORE` (the same store the Inbox resolves), so «Пропустить» in Входящие works too.
* telegram_bot.py hooks: the answer loop calls :func:`ask` when the model's reply is an ask_user call,
  ``_handle_callback`` routes ``au:*`` buttons to :func:`handle_callback`, and the update dispatcher hands a text
  message to :func:`handle_message` (outside the chat's ordered lane, which the waiting answer holds).
Callback data: ``au:<form-tag>:<op>`` (≤ 64 bytes): ``c:<f>:<o>`` pick, ``b:<f>:<0|1>`` yes/no, ``m:<f>:<o>``
toggle, ``o:<f>`` other…, ``i:<f>`` type a value, ``d:<f>:<days>`` quick date, ``ok``, ``skip``.
"""
import asyncio
import datetime as _dt
import html
import logging
from typing import Any, Dict, List, Optional

from . import ask_user as au

log = logging.getLogger("memuai.telegram.ask_user")

#: one pending form per Telegram chat: chat_id → state
_ACTIVE: Dict[str, Dict[str, Any]] = {}
WAIT_TIMEOUT = 420.0          # the bot's update handler lives at most 600 s; after this the model decides itself


def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def new_state(form_id: str, form: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    other: Dict[str, str] = {}
    for f in form.get("fields") or []:
        if "default" in f and f["default"] is not None:
            if f["type"] == "choice" and f["default"] not in (f.get("options") or []):
                other[f["id"]] = str(f["default"])
            else:
                values[f["id"]] = list(f["default"]) if isinstance(f["default"], list) else f["default"]
    return {"id": form_id, "tag": form_id[-6:], "form": form, "lang": "en" if lang == "en" else "ru",
            "values": values, "other": other, "awaiting": None, "message_id": 0, "error": ""}


def _value_text(state: Dict[str, Any], field: Dict[str, Any]) -> str:
    lang = state["lang"]
    fid = field["id"]
    value = state["values"].get(fid)
    other = state["other"].get(fid)
    if field["type"] == "multi":
        items = list(value or []) + ([other] if other else [])
        return ", ".join(items) if items else "—"
    if field["type"] == "choice" and other:
        return other
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return _tr(lang, "Да", "Yes") if value else _tr(lang, "Нет", "No")
    if field["type"] == "date":
        day = au._date(value)
        if day:
            return _dt.date.fromisoformat(day).strftime("%d.%m.%Y")
    if field["type"] == "number" and field.get("unit"):
        return f"{value} {field['unit']}"
    return str(value)


def render(state: Dict[str, Any]) -> (str, Dict[str, Any]):
    """(HTML text, inline keyboard) of the pending form."""
    lang, form, tag = state["lang"], state["form"], state["tag"]
    fields = form.get("fields") or []
    lines = ["❓ <b>" + html.escape(form.get("question") or _tr(lang, "Уточните, пожалуйста", "A quick question")) + "</b>"]
    for f in fields:
        lines.append(f"• <i>{html.escape(f['label'])}</i>: {html.escape(_value_text(state, f))}")
    if state.get("awaiting"):
        fid = state["awaiting"].replace("__other", "")
        field = next((f for f in fields if f["id"] == fid), None)
        if field:
            lines.append("")
            lines.append("✍️ " + html.escape(_tr(lang, "Напишите ответ сообщением: ", "Reply with a message: ") + field["label"]))
    if state.get("error"):
        lines.append("⚠️ " + html.escape(state["error"]))
    lines.append("")
    lines.append("<i>" + html.escape(_tr(lang, "Выберите ответ кнопками или просто напишите его сообщением.",
                                             "Pick with the buttons or simply reply with a message.")) + "</i>")
    rows: List[List[Dict[str, str]]] = []
    cb = lambda op: f"au:{tag}:{op}"[:64]   # noqa: E731
    for fi, f in enumerate(fields):
        if len(fields) > 1:
            rows.append([{"text": "▸ " + f["label"][:40], "callback_data": "noop"}])
        fid = f["id"]
        if f["type"] in ("choice", "multi"):
            chosen = state["values"].get(fid)
            buttons = []
            for oi, option in enumerate(f.get("options") or []):
                if f["type"] == "multi":
                    mark = "☑️ " if option in (chosen or []) else "⬜ "
                    buttons.append({"text": mark + option[:30], "callback_data": cb(f"m:{fi}:{oi}")})
                else:
                    mark = "✅ " if chosen == option and fid not in state["other"] else ""
                    buttons.append({"text": mark + option[:30], "callback_data": cb(f"c:{fi}:{oi}")})
            if f.get("allow_other"):
                mark = "✅ " if state["other"].get(fid) else "✏️ "
                buttons.append({"text": mark + _tr(lang, "Другое…", "Other…"), "callback_data": cb(f"o:{fi}")})
            per_row = 2 if max((len(b["text"]) for b in buttons), default=0) > 14 else 3
            rows.extend(buttons[i:i + per_row] for i in range(0, len(buttons), per_row))
        elif f["type"] == "bool":
            value = state["values"].get(fid)
            rows.append([{"text": ("✅ " if value is True else "") + _tr(lang, "Да", "Yes"), "callback_data": cb(f"b:{fi}:1")},
                         {"text": ("✅ " if value is False else "") + _tr(lang, "Нет", "No"), "callback_data": cb(f"b:{fi}:0")}])
        else:
            row = [{"text": "✏️ " + _tr(lang, "Ввести", "Type") + (": " + f["label"][:24] if len(fields) == 1 else ""),
                    "callback_data": cb(f"i:{fi}")}]
            if f["type"] == "date":
                row += [{"text": _tr(lang, "Сегодня", "Today"), "callback_data": cb(f"d:{fi}:0")},
                        {"text": _tr(lang, "Завтра", "Tomorrow"), "callback_data": cb(f"d:{fi}:1")}]
            rows.append(row)
    rows.append([{"text": "✅ " + (form.get("submit_label") or _tr(lang, "Готово", "Done")), "callback_data": cb("ok")},
                 {"text": "⏭ " + _tr(lang, "Пропустить — решай сам", "Skip — you decide"), "callback_data": cb("skip")}])
    return "\n".join(lines), {"inline_keyboard": rows}


def collect(state: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in state["form"].get("fields") or []:
        fid = f["id"]
        value = state["values"].get(fid)
        other = state["other"].get(fid)
        if f["type"] == "multi":
            items = list(value or []) + ([other] if other else [])
            if items:
                out[fid] = items
        elif f["type"] == "choice" and other:
            out[fid] = other
        elif value is not None and value != "":
            out[fid] = value
    return out


def on_callback(state: Dict[str, Any], op: str) -> Dict[str, Any]:
    """Apply one button press. → {changed?, ask_text?, submit?, skip?}"""
    fields = state["form"].get("fields") or []
    parts = op.split(":")
    state["error"] = ""
    if parts[0] == "ok":
        return {"submit": collect(state)}
    if parts[0] == "skip":
        return {"skip": True}
    try:
        field = fields[int(parts[1])]
    except (IndexError, ValueError):
        return {}
    fid = field["id"]
    kind = parts[0]
    if kind == "c" and len(parts) > 2:
        options = field.get("options") or []
        option = options[int(parts[2])] if parts[2].isdigit() and int(parts[2]) < len(options) else None
        if option is None:
            return {}
        state["other"].pop(fid, None)
        state["values"][fid] = None if state["values"].get(fid) == option and not field.get("required") else option
        state["awaiting"] = None
        return {"changed": True}
    if kind == "m" and len(parts) > 2:
        options = field.get("options") or []
        if not (parts[2].isdigit() and int(parts[2]) < len(options)):
            return {}
        option = options[int(parts[2])]
        chosen = list(state["values"].get(fid) or [])
        if option in chosen:
            chosen.remove(option)
        else:
            chosen.append(option)
        state["values"][fid] = chosen
        return {"changed": True}
    if kind == "b" and len(parts) > 2:
        value = parts[2] == "1"
        state["values"][fid] = None if state["values"].get(fid) is value and not field.get("required") else value
        return {"changed": True}
    if kind == "d" and len(parts) > 2 and parts[2].isdigit():
        state["values"][fid] = (_dt.date.today() + _dt.timedelta(days=int(parts[2]))).isoformat()
        state["awaiting"] = None
        return {"changed": True}
    if kind == "o":
        state["awaiting"] = fid + "__other"
        return {"changed": True, "ask_text": field["label"]}
    if kind == "i":
        state["awaiting"] = fid
        return {"changed": True, "ask_text": field["label"]}
    return {}


def on_text(state: Dict[str, Any], text: str) -> Dict[str, Any]:
    """A reply message: fills the awaited field, or (nothing awaited) is the answer itself."""
    text = str(text or "").strip()
    if not text:
        return {}
    awaiting = state.get("awaiting")
    fields = state["form"].get("fields") or []
    if not awaiting:
        # One text-like field and nothing else: the message is its value; otherwise a free reply in words.
        free = [f for f in fields if f["type"] in ("text", "number", "date")]
        if len(fields) == 1 and free:
            awaiting = free[0]["id"]
        else:
            return {"submit": {"_reply": text[:au.MAX_TEXT_ANSWER]}}
    fid = awaiting.replace("__other", "")
    field = next((f for f in fields if f["id"] == fid), None)
    if not field:
        state["awaiting"] = None
        return {}
    if awaiting.endswith("__other"):
        state["other"][fid] = text[:300]
        if field["type"] == "choice":
            state["values"].pop(fid, None)
    else:
        if field["type"] == "number" and au._num(text) is None:
            state["error"] = _tr(state["lang"], "Нужно число — напишите ещё раз.", "A number is needed — try again.")
            return {"changed": True}
        if field["type"] == "date" and not au._date(text):
            state["error"] = _tr(state["lang"], "Нужна дата, например 25.12.2026.", "A date is needed, e.g. 2026-12-25.")
            return {"changed": True}
        state["values"][fid] = text[:au.MAX_TEXT_ANSWER]
    state["awaiting"] = None
    state["error"] = ""
    return {"changed": True}


# ---------------------------------------------------------------- bot side
def claims_message(chat_id: str, message: Dict[str, Any]) -> bool:
    """A text message while a form waits in this chat answers the form (commands still go to the bot)."""
    state = _ACTIVE.get(str(chat_id))
    text = str((message or {}).get("text") or "").strip()
    return bool(state and text and not text.startswith("/"))


async def _edit(api: Any, chat_id: str, state: Dict[str, Any], *, final: str = "") -> None:
    if not state.get("message_id"):
        return
    if final:
        text, markup = final, {"inline_keyboard": []}
    else:
        text, markup = render(state)
    try:
        await api.call("editMessageText", chat_id=chat_id, message_id=int(state["message_id"]), text=text,
                       parse_mode="HTML", reply_markup=markup)
    except Exception as exc:          # «message is not modified» and the like
        if "not modified" not in str(exc):
            log.debug("ask_user edit failed: %s", type(exc).__name__)


def _final_text(state: Dict[str, Any], record: Dict[str, Any]) -> str:
    lang = state["lang"]
    form = state["form"]
    status = str(record.get("status") or "")
    head = "✅ " if status == "answered" else "⏭ "
    title = html.escape(form.get("question") or _tr(lang, "Уточнение", "Question"))
    return f"{head}<b>{title}</b>\n" + html.escape(au.summary(form, record.get("answers") or {}, status, lang))


async def ask(api: Any, chat_id: str, args: Any, *, lang: str = "ru", title: str = "",
              stop: Optional[asyncio.Event] = None, timeout: float = WAIT_TIMEOUT) -> Dict[str, Any]:
    """Show the form in ``chat_id``, wait, return the tool result for the model (never raises)."""
    try:
        form = au.normalize_form(args)
    except au.FormError as exc:
        return {"tool": au.TOOL_NAME, "error": str(exc) + ". Resend with 1–3 fields, each with a type and a label."}
    record = await asyncio.to_thread(au.STORE.open, form, surface="telegram", lang=lang, title=title,
                                     ref={"chat_id": str(chat_id)})
    state = new_state(record["id"], record["form"], lang)
    previous = _ACTIVE.get(str(chat_id))
    if previous:
        await asyncio.to_thread(au.STORE.settle, previous["id"], "cancelled")
    _ACTIVE[str(chat_id)] = state
    text, markup = render(state)
    try:
        sent = await api.call("sendMessage", essential=True, chat_id=chat_id, text=text, parse_mode="HTML",
                              reply_markup=markup)
        state["message_id"] = int((sent or {}).get("message_id") or 0) if isinstance(sent, dict) else 0
    except Exception as exc:
        log.warning("ask_user form not delivered: %s", type(exc).__name__)
        _ACTIVE.pop(str(chat_id), None)
        await asyncio.to_thread(au.STORE.settle, record["id"], "cancelled")
        return {"tool": au.TOOL_NAME, "error": "The form could not be shown in Telegram; ask in plain text instead."}
    try:
        settled = await au.STORE.wait(record["id"], timeout=timeout, stop=stop)
    finally:
        if _ACTIVE.get(str(chat_id)) is state:
            _ACTIVE.pop(str(chat_id), None)
    await _edit(api, chat_id, state, final=_final_text(state, settled))
    status = str(settled.get("status") or "cancelled")
    out = au.tool_result(record["form"], settled.get("answers"), status)
    # the caller pops this and saves it with the answer (the MemuAI window shows it as an answered form)
    out["_chat_entry"] = {"id": record["id"], "form": record["form"], "status": status,
                          "answers": settled.get("answers"), "via": "telegram"}
    return out


async def _settle(api: Any, chat_id: str, state: Dict[str, Any], status: str, answers: Any = None) -> bool:
    record, errors = await asyncio.to_thread(au.STORE.settle, state["id"], status, answers)
    if errors:
        names = {f["id"]: f["label"] for f in state["form"].get("fields") or []}
        state["error"] = _tr(state["lang"], "Проверьте: ", "Check: ") + ", ".join(names.get(e["field"], e["field"]) for e in errors)
        await _edit(api, chat_id, state)
        return False
    return record is not None


async def handle_callback(api: Any, chat_id: str, data: str) -> str:
    """``au:<tag>:<op>`` button → toast text ('' = none)."""
    parts = str(data).split(":", 2)
    state = _ACTIVE.get(str(chat_id))
    if len(parts) < 3 or not state or parts[1] != state["tag"]:
        return "Этот вопрос уже закрыт" if not state or state["lang"] != "en" else "This question is closed"
    result = on_callback(state, parts[2])
    if result.get("skip"):
        await _settle(api, chat_id, state, "skipped")
        return ""
    if "submit" in result:
        await _settle(api, chat_id, state, "answered", result["submit"])
        return ""
    if result.get("changed"):
        await _edit(api, chat_id, state)
    return ("✍️ " + _tr(state["lang"], "Напишите ответ сообщением", "Reply with a message")) if result.get("ask_text") else ""


async def handle_message(api: Any, chat_id: str, message: Dict[str, Any]) -> bool:
    state = _ACTIVE.get(str(chat_id))
    if not state:
        return False
    result = on_text(state, str(message.get("text") or ""))
    if "submit" in result:
        await _settle(api, chat_id, state, "answered", result["submit"])
    elif result.get("changed"):
        await _edit(api, chat_id, state)
    return True


def cancel_chat(chat_id: str) -> None:
    """Stop / a new chat: the waiting form is closed (the waiter gets ``cancelled``)."""
    state = _ACTIVE.pop(str(chat_id), None)
    if state:
        try:
            au.STORE.settle(state["id"], "cancelled")
        except Exception:
            pass
