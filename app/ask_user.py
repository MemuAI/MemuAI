"""ask_user — the model asks the user with a small form instead of a paragraph of questions (Round 35).

One contract for every surface (main chat, Work, DFY, Quick Access, Telegram bot, Mini App, flows, Inbox):

    form = {"question": "short title", "fields": [
        {"id": "kind", "type": "choice|multi|text|date|number|bool", "label": "…",
         "options": ["…", …], "allow_other": bool, "default": …, "required": bool,
         "min": n, "max": n, "step": n, "unit": "…", "placeholder": "…"}]}   # ≤ 3 fields

* :func:`normalize_form` — accepts what models really send (aliases, strings, too many fields) and returns
  a clean form or raises :class:`FormError` (nothing usable). Never more than ``MAX_FIELDS`` fields.
* :func:`validate_answers` — checks the user's answers against the form (server is the judge, the
  client only helps): unknown option → error unless ``allow_other``; numbers within min/max; ISO dates.
* :func:`tool_result` — what goes back to the model as the tool result (status answered / skipped /
  timeout / cancelled + answers keyed by field id, with labels for clarity).
* :class:`PendingStore` — pending forms of server-side surfaces (Telegram, Mini App, Quick Access, DFY)
  in ``<data>/memuai/ask_user.json`` (cross-process: Proxy and the MemuAI window are different processes),
  plus in-process futures so the waiting tool loop wakes at once; ``wait`` also polls the file so an
  answer given in another process (Inbox) arrives too. Every pending form is posted to the Inbox
  («ждёт ответа», source ``ask_user``) and resolved when answered.
* :func:`register` — HTTP routes ``<prefix>/normalize|open|pending|{id}|{id}/answer|{id}/skip|{id}/cancel``.
* :func:`prompt_section` / ``TOOL_DESCRIPTION`` / :func:`openai_tool` — what the model is taught.
* :func:`extract_call` — finds an ask_user call in a text-protocol answer (```memuai-tool block, bare JSON
  or <tool_call>) for loops that have no native tool calls (Telegram, Mini App).
"""

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.ask_user")

TOOL_NAME = "ask_user"
FIELD_TYPES = ("choice", "multi", "text", "date", "number", "bool")
MAX_FIELDS = 3
MAX_OPTIONS = 8
MAX_LABEL = 120
MAX_OPTION = 60
MAX_QUESTION = 200
MAX_TEXT_ANSWER = 2000
DEFAULT_TIMEOUT = 30 * 60          # server-side surfaces stop waiting after 30 min and let the model decide
PENDING_TTL = 24 * 3600            # forgotten pending forms are dropped after a day

_TYPE_ALIASES = {
    "choice": "choice", "chips": "choice", "chip": "choice", "select": "choice", "radio": "choice",
    "single": "choice", "single_choice": "choice", "enum": "choice", "option": "choice", "options": "choice",
    "dropdown": "choice", "buttons": "choice",
    "multi": "multi", "multiple": "multi", "multi_choice": "multi", "multiselect": "multi", "multi_select": "multi",
    "checkbox": "multi", "checkboxes": "multi", "checklist": "multi", "tags": "multi",
    "text": "text", "string": "text", "textarea": "text", "input": "text", "str": "text", "free_text": "text",
    "date": "date", "day": "date", "datetime": "date",
    "number": "number", "int": "number", "integer": "number", "float": "number", "numeric": "number",
    "slider": "number", "range": "number", "amount": "number",
    "bool": "bool", "boolean": "bool", "yes_no": "bool", "yesno": "bool", "toggle": "bool", "switch": "bool",
    "confirm": "bool",
}

TOOL_DESCRIPTION = (
    "ask_user {question, fields:[{id, type: choice|multi|text|date|number|bool, label, options?, allow_other?, "
    "default?, required?, min?, max?, unit?}]} — shows the user a small form (chips, checkboxes, a date, a "
    "number) and waits; the answers come back as the tool result and you continue the same answer. When the "
    "answer depends on something only the user knows (budget, purpose, a date, which of several real options) "
    "and no sensible default fits, do NOT end your reply with questions — call ask_user instead. Use it ONLY "
    "when the result would really differ without the answer and you cannot pick a sensible default yourself; "
    "at most 3 fields, short labels, 2–6 options, offer the likely choice as default; allow_other adds «Other». "
    "Never for trivial things, never to confirm what the user already said, never twice for the same thing. "
    "If the user skips, decide yourself, state the assumption in one short clause and continue."
)

_PROMPT = """<ask_user>
When a request is genuinely ambiguous and a wrong guess would change the result (which of several real
options, a date, a budget, a size), ask with a small form instead of writing a paragraph of questions:
```memuai-tool
[{"tool":"ask_user","args":{"question":"Short title","fields":[
 {"id":"style","type":"choice","label":"Style","options":["Formal","Friendly"],"default":"Friendly","allow_other":true},
 {"id":"date","type":"date","label":"When"}]}}]
```
Field types: choice (one chip), multi (checkboxes), text, date (YYYY-MM-DD), number (min, max, unit), bool.
Never end a reply with a list of questions for the user: questions you really need belong in the form.
Rules: ask ONLY what changes the result — never trivial things, never what the user already told you, never
what a sensible default covers (then just use the default and say so). At most 3 fields, short labels,
2–6 options with the likely one as default. Write nothing else in that reply: the form is the reply; the
answers arrive as the tool result and you continue the same answer from them. If the user skipped the form,
decide yourself, name the assumption in one short clause and finish the task. One form per turn is normal;
a second only if the answers opened a genuinely new question.
</ask_user>"""


class FormError(ValueError):
    """The arguments do not make a usable form."""


# ---------------------------------------------------------------- prompt
def prompt_section(lang: str = "ru", surface: str = "chat") -> str:
    """The ask_user block for a system prompt (the same rules on every surface)."""
    text = _PROMPT
    if surface == "telegram":
        text = text.replace("</ask_user>", "In Telegram the form becomes buttons under your message.\n</ask_user>")
    return text


def openai_tool() -> Dict[str, Any]:
    """OpenAI function schema (for loops with native tool calls)."""
    field = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "enum": list(FIELD_TYPES)},
            "label": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_OPTIONS},
            "allow_other": {"type": "boolean"},
            "default": {},
            "required": {"type": "boolean"},
            "min": {"type": "number"}, "max": {"type": "number"}, "step": {"type": "number"},
            "unit": {"type": "string"},
        },
        "required": ["type", "label"],
    }
    return {"type": "function", "function": {
        "name": TOOL_NAME, "description": TOOL_DESCRIPTION,
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "fields": {"type": "array", "items": field, "minItems": 1, "maxItems": MAX_FIELDS}},
            "required": ["fields"]}}}


# ---------------------------------------------------------------- normalization
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def _clean_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = _CTRL.sub("", text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text).strip()
    # Markdown/HTML never renders inside a form: strip the obvious wrappers instead of showing them.
    text = re.sub(r"^\*\*(.+)\*\*$", r"\1", text)
    text = re.sub(r"<[^>]{1,40}>", "", text)
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _slug(text: str, fallback: str) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", text.lower()).strip("_")[:24]
    return base or fallback


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    else:
        raw = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", raw, re.I):
            return None
        out = float(raw)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _tidy_num(value: float) -> Any:
    return int(value) if float(value).is_integer() and abs(value) < 1e15 else round(value, 6)


def _date(value: Any) -> Optional[str]:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.strftime("%Y-%m-%d")
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", raw) or None
    if match:
        y, m, d = (int(x) for x in match.groups())
    else:
        match = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", raw)
        if not match:
            return None
        d, m, y = (int(x) for x in match.groups())
    try:
        return _dt.date(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if raw in ("true", "yes", "y", "1", "on", "да", "д", "ага", "конечно"):
        return True
    if raw in ("false", "no", "n", "0", "off", "нет", "н", "не"):
        return False
    return None


def _options(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [part for part in re.split(r"\s*[|;\n]\s*|\s*,\s+", raw) if part]
    if isinstance(raw, dict):
        raw = list(raw.values()) if all(isinstance(v, str) for v in raw.values()) else list(raw.keys())
    out: List[str] = []
    seen = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            item = item.get("label") or item.get("value") or item.get("text") or item.get("title") or ""
        text = _clean_text(item, MAX_OPTION)
        key = text.casefold()
        if text and key not in seen and key not in ("other", "другое", "другой", "иное", "other…", "другое…"):
            seen.add(key)
            out.append(text)
        if len(out) >= MAX_OPTIONS:
            break
    return out


def _field(raw: Any, index: int, used: set) -> Optional[Dict[str, Any]]:
    if isinstance(raw, str):
        raw = {"type": "text", "label": raw}
    if not isinstance(raw, dict):
        return None
    label = _clean_text(raw.get("label") or raw.get("question") or raw.get("title") or raw.get("name")
                        or raw.get("text") or raw.get("prompt") or "", MAX_LABEL)
    kind = _TYPE_ALIASES.get(str(raw.get("type") or raw.get("kind") or "").strip().lower().replace("-", "_"), "")
    options = _options(raw.get("options") if raw.get("options") is not None else
                       raw.get("choices") if raw.get("choices") is not None else
                       raw.get("values") if raw.get("values") is not None else raw.get("enum"))
    if not kind:
        kind = "choice" if options else "text"
    if raw.get("multiple") is True and kind == "choice":
        kind = "multi"
    if kind in ("choice", "multi") and not options:
        kind = "text"                     # chips without chips — a text field still gets the answer
    if kind == "choice" and len(options) == 1 and not raw.get("allow_other"):
        kind = "bool"                     # one chip is a yes/no question
        label = label or options[0]
    if not label:
        label = options[0] if options and kind in ("choice", "multi") else ""
    if not label:
        return None
    field: Dict[str, Any] = {"id": "", "type": kind, "label": label}
    fid = _slug(str(raw.get("id") or raw.get("key") or raw.get("name") or ""), "") or _slug(label, f"f{index + 1}")
    if fid in used or not re.match(r"^[a-z_]", fid):
        fid = f"f{index + 1}" if f"f{index + 1}" not in used else f"f{index + 1}_{uuid.uuid4().hex[:4]}"
    used.add(fid)
    field["id"] = fid
    allow_other = bool(raw.get("allow_other") or raw.get("other") or raw.get("allowOther"))
    if kind in ("choice", "multi"):
        field["options"] = options
        field["allow_other"] = allow_other
    placeholder = _clean_text(raw.get("placeholder") or raw.get("hint") or "", 80)
    if placeholder and kind in ("text", "number", "date"):
        field["placeholder"] = placeholder
    if kind == "number":
        lo, hi = _num(raw.get("min")), _num(raw.get("max"))
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        if lo is not None:
            field["min"] = _tidy_num(lo)
        if hi is not None:
            field["max"] = _tidy_num(hi)
        step = _num(raw.get("step"))
        if step is not None and step > 0:
            field["step"] = _tidy_num(step)
        unit = _clean_text(raw.get("unit") or raw.get("suffix") or "", 16)
        if unit:
            field["unit"] = unit
    if kind == "date":
        for key in ("min", "max"):
            value = _date(raw.get(key))
            if value:
                field[key] = value
    if kind == "text":
        field["multiline"] = bool(raw.get("multiline") or str(raw.get("type") or "").lower() == "textarea")
    default = raw.get("default", raw.get("value"))
    if default is not None and default != "":
        clean = _clean_default(field, default)
        if clean is not None:
            field["default"] = clean
    field["required"] = bool(raw.get("required"))
    return field


def _clean_default(field: Dict[str, Any], value: Any) -> Any:
    kind = field["type"]
    if kind == "choice":
        text = _clean_text(value[0] if isinstance(value, list) and value else value, MAX_OPTION)
        match = next((o for o in field["options"] if o.casefold() == text.casefold()), None)
        return match or (text if field.get("allow_other") and text else None)
    if kind == "multi":
        items = value if isinstance(value, list) else [value]
        out = []
        for item in items:
            text = _clean_text(item, MAX_OPTION)
            match = next((o for o in field["options"] if o.casefold() == text.casefold()), None)
            if match and match not in out:
                out.append(match)
        return out or None
    if kind == "number":
        num = _num(value)
        if num is None:
            return None
        if "min" in field:
            num = max(num, float(field["min"]))
        if "max" in field:
            num = min(num, float(field["max"]))
        return _tidy_num(num)
    if kind == "date":
        return _date(value)
    if kind == "bool":
        return _bool(value)
    text = _clean_text(value, 500)
    return text or None


def normalize_form(args: Any) -> Dict[str, Any]:
    """A clean form from model arguments; raises FormError when nothing usable is there."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"question": args}
    if isinstance(args, list):
        args = {"fields": args}
    if not isinstance(args, dict):
        raise FormError("ask_user needs an object with fields")
    raw_fields = args.get("fields")
    if raw_fields is None:
        raw_fields = args.get("questions") if args.get("questions") is not None else args.get("inputs")
    if isinstance(raw_fields, dict):
        raw_fields = [dict(v, id=v.get("id") or k) if isinstance(v, dict) else {"id": k, "label": str(v)}
                      for k, v in raw_fields.items()]
    if raw_fields is None and (args.get("options") or args.get("choices") or args.get("type")):
        raw_fields = [dict(args, label=args.get("label") or args.get("question") or args.get("title") or "")]
    question = _clean_text(args.get("question") or args.get("title") or args.get("prompt")
                           or args.get("message") or "", MAX_QUESTION)
    fields: List[Dict[str, Any]] = []
    used: set = set()
    dropped = 0
    for index, raw in enumerate(raw_fields if isinstance(raw_fields, list) else []):
        if len(fields) >= MAX_FIELDS:
            dropped += 1
            continue
        field = _field(raw, index, used)
        if field:
            fields.append(field)
    if not fields and question:
        fields = [{"id": "answer", "type": "text", "label": question, "required": False, "multiline": False}]
        question = ""
    if not fields:
        raise FormError("ask_user: no usable fields (each needs a type and a label)")
    form: Dict[str, Any] = {"question": question, "fields": fields}
    if dropped:
        form["dropped_fields"] = dropped
    submit = _clean_text(args.get("submit_label") or args.get("submit") or "", 24)
    if submit:
        form["submit_label"] = submit
    return form


# ---------------------------------------------------------------- answers
def validate_answers(form: Dict[str, Any], answers: Any) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """(clean answers by field id, errors [{field, error}]) — the server decides what is valid."""
    answers = answers if isinstance(answers, dict) else {}
    clean: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []
    for field in form.get("fields") or []:
        fid, kind = field["id"], field["type"]
        value = answers.get(fid)
        other = answers.get(fid + "__other")
        if isinstance(value, dict):          # {"value": …, "other": …}
            other = value.get("other", other)
            value = value.get("value", value.get("values"))
        empty = value is None or value == "" or value == []
        if kind == "choice":
            text = _clean_text(value, MAX_OPTION) if not empty else ""
            other_text = _clean_text(other, 300) if other else ""
            match = next((o for o in field.get("options", []) if o.casefold() == text.casefold()), None)
            if match:
                clean[fid] = match
            elif field.get("allow_other") and (other_text or text):
                clean[fid] = other_text or _clean_text(value, 300)
            elif text:
                errors.append({"field": fid, "error": "not_an_option"})
            elif field.get("required"):
                errors.append({"field": fid, "error": "required"})
        elif kind == "multi":
            items = value if isinstance(value, list) else ([] if empty else [value])
            out: List[str] = []
            for item in items[:MAX_OPTIONS + 1]:
                text = _clean_text(item, MAX_OPTION)
                match = next((o for o in field.get("options", []) if o.casefold() == text.casefold()), None)
                if match:
                    if match not in out:
                        out.append(match)
                elif text and field.get("allow_other"):
                    out.append(_clean_text(item, 300))
                elif text:
                    errors.append({"field": fid, "error": "not_an_option"})
            other_text = _clean_text(other, 300) if other else ""
            if other_text and field.get("allow_other") and other_text not in out:
                out.append(other_text)
            if out:
                clean[fid] = out
            elif field.get("required"):
                errors.append({"field": fid, "error": "required"})
        elif kind == "number":
            if empty:
                if field.get("required"):
                    errors.append({"field": fid, "error": "required"})
                continue
            num = _num(value)
            if num is None:
                errors.append({"field": fid, "error": "not_a_number"})
            elif "min" in field and num < float(field["min"]):
                errors.append({"field": fid, "error": "below_min"})
            elif "max" in field and num > float(field["max"]):
                errors.append({"field": fid, "error": "above_max"})
            else:
                clean[fid] = _tidy_num(num)
        elif kind == "date":
            if empty:
                if field.get("required"):
                    errors.append({"field": fid, "error": "required"})
                continue
            day = _date(value)
            if not day:
                errors.append({"field": fid, "error": "not_a_date"})
            elif field.get("min") and day < field["min"]:
                errors.append({"field": fid, "error": "below_min"})
            elif field.get("max") and day > field["max"]:
                errors.append({"field": fid, "error": "above_max"})
            else:
                clean[fid] = day
        elif kind == "bool":
            flag = None if empty else _bool(value)
            if flag is None:
                if not empty:
                    errors.append({"field": fid, "error": "not_a_bool"})
                elif field.get("required"):
                    errors.append({"field": fid, "error": "required"})
            else:
                clean[fid] = flag
        else:
            text = "" if empty else _CTRL.sub("", str(value)).strip()[:MAX_TEXT_ANSWER]
            if text:
                clean[fid] = text
            elif field.get("required"):
                errors.append({"field": fid, "error": "required"})
    # A free reply typed instead of filling the form (composer / Telegram message) — kept as it is.
    reply = answers.get("_reply")
    if isinstance(reply, str) and reply.strip():
        clean["_reply"] = _CTRL.sub("", reply).strip()[:MAX_TEXT_ANSWER]
    return clean, errors


def _fmt_value(field: Dict[str, Any], value: Any, lang: str) -> str:
    if isinstance(value, bool):
        return ("Да" if value else "Нет") if lang != "en" else ("Yes" if value else "No")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if field.get("type") == "number" and field.get("unit"):
        return f"{value} {field['unit']}"
    if field.get("type") == "date":
        try:
            day = _dt.date.fromisoformat(str(value))
            return day.strftime("%d.%m.%Y") if lang != "en" else day.strftime("%b %d, %Y").replace(" 0", " ")
        except ValueError:
            pass
    return str(value)


def summary(form: Dict[str, Any], answers: Dict[str, Any], status: str = "answered", lang: str = "ru") -> str:
    """One line for the compact answered form («Стиль: Дружелюбный · Когда: 12.10.2026»)."""
    if status == "skipped":
        return "Пропущено — модель решает сама" if lang != "en" else "Skipped — the model decides"
    if status == "timeout":
        return "Нет ответа — модель решает сама" if lang != "en" else "No answer — the model decides"
    if status == "cancelled":
        return "Отменено" if lang != "en" else "Cancelled"
    parts = []
    for field in form.get("fields") or []:
        if field["id"] in answers:
            parts.append(f"{field['label']}: {_fmt_value(field, answers[field['id']], lang)}")
    if answers.get("_reply"):
        parts.append(str(answers["_reply"])[:200])
    return " · ".join(parts) or ("Без ответа" if lang != "en" else "No answer")


def tool_result(form: Dict[str, Any], answers: Optional[Dict[str, Any]], status: str = "answered") -> Dict[str, Any]:
    """The tool result the model receives."""
    answers = answers or {}
    out: Dict[str, Any] = {"tool": TOOL_NAME, "status": status}
    if status == "answered":
        out["answers"] = {k: v for k, v in answers.items() if k != "_reply"}
        out["labels"] = {f["id"]: f["label"] for f in form.get("fields") or []}
        missing = [f["label"] for f in form.get("fields") or [] if f["id"] not in answers]
        if answers.get("_reply"):
            out["reply"] = answers["_reply"]
            out["note"] = ("The user answered in their own words instead of the form; read the reply, treat it as "
                           "the answer and continue the task.")
        elif missing:
            out["note"] = ("Fields left empty (" + ", ".join(missing) + "): choose sensible defaults for them, "
                           "name them in one short clause and continue.")
        else:
            out["note"] = "Use these answers and continue the task now. Do not ask the same again."
    elif status == "skipped":
        out["note"] = ("The user skipped the form («decide yourself»). Choose sensible defaults, state them in one "
                       "short clause and finish the task. Do not ask again.")
    elif status == "timeout":
        out["note"] = ("The user did not answer in time. Choose sensible defaults, state them briefly and finish "
                       "the task. Do not ask again.")
    else:
        out["note"] = "The form was cancelled. Stop and do not ask again."
    return out


# ---------------------------------------------------------------- text protocol
_TOOL_FENCE = re.compile(r"```[ \t]*memuai[-_ ]?tool\b[^\n]*\n?", re.I)


def _scan(source: str, start: int) -> Optional[Tuple[int, int]]:
    i = start
    while i < len(source) and source[i].isspace():
        i += 1
    if i >= len(source) or source[i] not in "[{":
        return None
    begin, depth, in_str, esc = i, 0, False, False
    for j in range(i, len(source)):
        ch = source[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return begin, j + 1
    return None


def rebalance_closers(raw: str) -> str:
    """Replace mismatched closers with the right ones, drop stray ones, append missing ones (strings respected)."""
    out, stack, in_str, esc = [], [], False, False
    for ch in str(raw or ""):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
            out.append(ch)
        elif ch in "]}":
            if stack:
                out.append(stack.pop())
        else:
            out.append(ch)
    out.extend(reversed(stack))
    return "".join(out)


def _calls_from(data: Any) -> List[Dict[str, Any]]:
    rows = data if isinstance(data, list) else [data]
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fn = row.get("function") if isinstance(row.get("function"), dict) else {}
        name = str(row.get("tool") or row.get("name") or fn.get("name") or "").strip().lower().replace("functions.", "")
        args = row.get("args", row.get("arguments", row.get("parameters", row.get("input", fn.get("arguments")))))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        out.append({"tool": name, "args": args if isinstance(args, dict) else {}})
    return out


def extract_call(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """(text without the call, ask_user args or None) — for text-protocol loops (Telegram, Mini App)."""
    source = str(text or "")
    for match in _TOOL_FENCE.finditer(source):
        span = _scan(source, match.end())
        if not span:
            continue
        try:
            calls = _calls_from(json.loads(source[span[0]:span[1]]))
        except ValueError:
            try:
                calls = _calls_from(json.loads(re.sub(r",\s*([\]}])", r"\1", source[span[0]:span[1]])))
            except ValueError:
                try:  # swapped closers at the end («]}]}]») — common with free models
                    calls = _calls_from(json.loads(rebalance_closers(
                        re.sub(r",\s*([\]}])", r"\1", source[span[0]:span[1]]))))
                except ValueError:
                    continue
        call = next((c for c in calls if c["tool"] == TOOL_NAME), None)
        if call:
            end = span[1]
            close = re.match(r"\s*\]*\s*```", source[end:])
            end += close.end() if close else 0
            return (source[:match.start()] + source[end:]).strip(), call["args"]
    tagged = re.search(r"<tool_call>\s*([\s\S]*?)\s*</tool_call>", source)
    if tagged:
        try:
            calls = _calls_from(json.loads(tagged.group(1)))
        except ValueError:
            calls = []
        call = next((c for c in calls if c["tool"] == TOOL_NAME), None)
        if call:
            return (source[:tagged.start()] + source[tagged.end():]).strip(), call["args"]
    bare = source.strip()
    bare = re.sub(r"^```[ \t]*(?:json)?[ \t]*\n([\s\S]*?)\n?```$", r"\1", bare).strip()
    if bare[:1] in "[{" and bare[-1:] in "]}":
        try:
            calls = _calls_from(json.loads(bare))
        except ValueError:
            calls = []
        call = next((c for c in calls if c["tool"] == TOOL_NAME), None)
        if call:
            return "", call["args"]
    return source, None


def strip_calls(text: str) -> str:
    """Removes every memuai-tool block (a call that will not run must not be shown)."""
    out, last = [], 0
    source = str(text or "")
    for match in _TOOL_FENCE.finditer(source):
        if match.start() < last:
            continue
        span = _scan(source, match.end())
        end = span[1] if span else len(source)
        close = re.match(r"\s*```", source[end:])
        end += close.end() if close else 0
        out.append(source[last:match.start()])
        last = end
    out.append(source[last:])
    return "".join(out).strip()


# ---------------------------------------------------------------- pending store
def _data_dir() -> str:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return env
    try:
        from .config import DATA_DIR
        return str(DATA_DIR)
    except Exception:  # pragma: no cover
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MemuAI")


def _inbox():
    try:
        from . import inbox
        return inbox
    except Exception:
        return None


_SURFACE_NAMES = {"chat": ("Чат", "Chat"), "work": ("Work", "Work"), "dfy": ("DFY", "DFY"),
                  "quick": ("Быстрый доступ", "Quick Access"), "telegram": ("Telegram", "Telegram"),
                  "miniapp": ("Mini App", "Mini App"), "flow": ("Планирование", "Planning")}


class PendingStore:
    """Pending forms: file-backed (cross-process) + in-process futures."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._futures: Dict[str, "asyncio.Future[Dict[str, Any]]"] = {}
        self._loops: Dict[str, asyncio.AbstractEventLoop] = {}

    @property
    def path(self) -> str:
        return self._path or os.path.join(_data_dir(), "memuai", "ask_user.json")

    def _mutate(self, fn) -> Any:
        from .storage import mutate_json
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        box: Dict[str, Any] = {}

        def mutator(data: Any) -> Any:
            if not isinstance(data, dict) or not isinstance(data.get("forms"), dict):
                data = {"version": 1, "forms": {}}
                box["result"] = fn(data["forms"])
                return True, data
            now = time.time()
            for key in [k for k, v in data["forms"].items()
                        if not isinstance(v, dict) or now - float(v.get("created") or 0) > PENDING_TTL]:
                data["forms"].pop(key, None)
            box["result"] = fn(data["forms"])
            return True

        with self._lock:
            mutate_json(self.path, {"version": 1, "forms": {}}, mutator, backup=False)
        return box.get("result")

    def _read(self) -> Dict[str, Any]:
        try:
            from .storage import load_json_with_recovery
            data = load_json_with_recovery(self.path, {"version": 1, "forms": {}})
        except Exception:
            return {}
        forms = data.get("forms") if isinstance(data, dict) else None
        return forms if isinstance(forms, dict) else {}

    def open(self, form: Dict[str, Any], *, surface: str = "chat", ref: Optional[Dict[str, Any]] = None,
             title: str = "", lang: str = "ru", form_id: str = "", inbox_post: bool = True) -> Dict[str, Any]:
        form = normalize_form(form) if "fields" not in form or any(
            not isinstance(f, dict) or f.get("type") not in FIELD_TYPES or not f.get("id")
            for f in form.get("fields") or []) else form
        form_id = re.sub(r"[^0-9a-zA-Z_-]", "", form_id)[:40] or "au-" + uuid.uuid4().hex[:12]
        record = {"id": form_id, "form": form, "surface": surface if surface in _SURFACE_NAMES else "chat",
                  "ref": dict(ref or {}), "title": _clean_text(title, 120), "lang": "en" if lang == "en" else "ru",
                  "status": "pending", "answers": None, "created": time.time(), "updated": time.time()}

        def put(forms: Dict[str, Any]) -> Dict[str, Any]:
            forms[form_id] = record
            return record

        self._mutate(put)
        if inbox_post:
            self._post_inbox(record)
        return dict(record)

    def _post_inbox(self, record: Dict[str, Any]) -> None:
        box = _inbox()
        if box is None:
            return
        en = record.get("lang") == "en"
        name = _SURFACE_NAMES.get(record["surface"], _SURFACE_NAMES["chat"])[1 if en else 0]
        form = record["form"]
        title = form.get("question") or (form["fields"][0]["label"] if form.get("fields") else "")
        body_lines = [f"• {f['label']}" for f in form.get("fields") or []]
        where = record.get("title") or ""
        try:
            box.post("question", title or ("Нужен ваш ответ" if not en else "Your answer is needed"),
                     body=((("Ждёт ответа — " if not en else "Waiting for your answer — ") + name
                            + (f" «{where}»" if where else "")) + "\n" + "\n".join(body_lines)),
                     source="ask_user",
                     ref={"form_id": record["id"], "surface": record["surface"], "form": form, **record.get("ref", {})},
                     actions=[{"id": "open", "label": "Открыть" if not en else "Open"},
                              {"id": "skip", "label": "Пропустить — решай сам" if not en else "Skip — you decide"}],
                     dedupe_key="ask_user:" + record["id"], severity="ask")
        except Exception:
            log.debug("inbox post failed", exc_info=True)

    def get(self, form_id: str) -> Optional[Dict[str, Any]]:
        record = self._read().get(str(form_id))
        return dict(record) if isinstance(record, dict) else None

    def pending(self, surface: str = "", ref_key: str = "", ref_value: str = "") -> List[Dict[str, Any]]:
        rows = [dict(v) for v in self._read().values() if isinstance(v, dict) and v.get("status") == "pending"
                and time.time() - float(v.get("created") or 0) <= PENDING_TTL]
        if surface:
            rows = [r for r in rows if r.get("surface") == surface]
        if ref_key:
            rows = [r for r in rows if str((r.get("ref") or {}).get(ref_key) or "") == str(ref_value)]
        return sorted(rows, key=lambda r: float(r.get("created") or 0))

    def settle(self, form_id: str, status: str, answers: Optional[Dict[str, Any]] = None
               ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
        """Answer / skip / cancel / timeout a pending form. Returns (record, errors)."""
        if status not in ("answered", "skipped", "timeout", "cancelled"):
            raise ValueError("bad status")
        box: Dict[str, Any] = {"errors": []}

        def change(forms: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            record = forms.get(form_id)
            if not isinstance(record, dict):
                return None
            if record.get("status") != "pending":
                return dict(record)           # already settled: the first answer wins
            clean: Dict[str, Any] = {}
            if status == "answered":
                clean, errors = validate_answers(record["form"], answers or {})
                if errors:
                    box["errors"] = errors
                    return dict(record)
            record.update(status=status, answers=clean if status == "answered" else None, updated=time.time())
            box["changed"] = True
            return dict(record)

        record = self._mutate(change)
        if box["errors"] or record is None:
            return record, box["errors"]
        if box.get("changed"):
            self._wake(form_id, record)
            inbox = _inbox()
            if inbox is not None:
                try:
                    inbox.resolve("ask_user:" + form_id, result=status)
                except Exception:
                    pass
        return record, []

    def _wake(self, form_id: str, record: Dict[str, Any]) -> None:
        future = self._futures.get(form_id)
        loop = self._loops.get(form_id)
        if future is None or loop is None:
            return

        def done() -> None:
            if not future.done():
                future.set_result(record)
        try:
            if loop.is_closed():
                return
            loop.call_soon_threadsafe(done)
        except RuntimeError:
            pass

    async def wait(self, form_id: str, *, timeout: float = DEFAULT_TIMEOUT, stop: Optional[asyncio.Event] = None,
                   poll: float = 1.0) -> Dict[str, Any]:
        """Waits for the answer; on timeout settles the form as ``timeout``; ``stop`` → ``cancelled``."""
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[Dict[str, Any]]" = loop.create_future()
        self._futures[form_id] = future
        self._loops[form_id] = loop
        deadline = time.monotonic() + max(1.0, timeout)
        try:
            while True:
                record = self.get(form_id)
                if record is None:
                    return {"id": form_id, "status": "cancelled", "answers": None, "form": {"fields": []}}
                if record.get("status") != "pending":
                    return record
                if stop is not None and stop.is_set():
                    return self.settle(form_id, "cancelled")[0] or record
                left = deadline - time.monotonic()
                if left <= 0:
                    return self.settle(form_id, "timeout")[0] or record
                try:
                    return await asyncio.wait_for(asyncio.shield(future), timeout=min(poll, left))
                except asyncio.TimeoutError:
                    continue
        finally:
            self._futures.pop(form_id, None)
            self._loops.pop(form_id, None)


STORE = PendingStore()


def _inbox_handler(item: Dict[str, Any], action_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    form_id = str((item.get("ref") or {}).get("form_id") or "")
    if not form_id:
        return {"resolve": True}
    if action_id == "skip":
        STORE.settle(form_id, "skipped")
        return {"resolve": True}
    if action_id == "answer":
        record, errors = STORE.settle(form_id, "answered", (payload or {}).get("answers") or {})
        if errors:
            return {"ok": False, "errors": errors}
        return {"resolve": True, "summary": summary(record["form"], record.get("answers") or {})} if record else {"resolve": True}
    return {"open": (item.get("ref") or {})}


def _register_inbox_handler() -> None:
    box = _inbox()
    if box is not None and hasattr(box, "register_handler"):
        try:
            box.register_handler("ask_user", _inbox_handler)
        except Exception:
            pass


# ---------------------------------------------------------------- HTTP
def register(app: Any, prefix: str = "/api/pg/ask_user", store: Optional[PendingStore] = None) -> None:
    """Routes for clients of this process (main window, Quick Access, Mini App, Inbox)."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    st = store or STORE
    _register_inbox_handler()

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @app.post(prefix + "/normalize")
    async def ask_normalize(request: Request):
        data = await body_of(request)
        try:
            return {"form": normalize_form(data.get("form", data))}
        except FormError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.post(prefix + "/open")
    async def ask_open(request: Request):
        data = await body_of(request)
        try:
            form = normalize_form(data.get("form") or data.get("args") or {})
        except FormError as exc:
            return JSONResponse({"error": str(exc), "result": {"tool": TOOL_NAME, "error": str(exc) +
                                 ". Resend ask_user with 1–3 fields, each with a type and a label."}}, 400)
        ref = data.get("ref") if isinstance(data.get("ref"), dict) else {}
        ref = {str(k)[:40]: (v[:200] if isinstance(v, str) else v) for k, v in list(ref.items())[:8]
               if isinstance(v, (str, int, float, bool))}
        record = await asyncio.to_thread(st.open, form, surface=str(data.get("surface") or "chat"), ref=ref,
                                         title=str(data.get("title") or ""), lang=str(data.get("lang") or "ru"),
                                         form_id=str(data.get("id") or ""))
        return record

    @app.get(prefix + "/pending")
    async def ask_pending(surface: str = "", chat_id: str = ""):
        rows = await asyncio.to_thread(st.pending, surface, "chat_id" if chat_id else "", chat_id)
        return {"forms": rows}

    @app.get(prefix + "/{form_id}")
    async def ask_get(form_id: str):
        record = await asyncio.to_thread(st.get, form_id)
        return record if record else JSONResponse({"error": "not found"}, 404)

    async def settle(form_id: str, status: str, answers: Any = None):
        record, errors = await asyncio.to_thread(st.settle, form_id, status, answers)
        if record is None:
            return JSONResponse({"error": "not found"}, 404)
        if errors:
            return JSONResponse({"error": "invalid", "errors": errors}, 422)
        lang = record.get("lang") or "ru"
        status_now = record.get("status")
        return {"ok": True, "record": record,
                "result": tool_result(record["form"], record.get("answers"), status_now),
                "summary": summary(record["form"], record.get("answers") or {}, status_now, lang)}

    @app.post(prefix + "/{form_id}/answer")
    async def ask_answer(form_id: str, request: Request):
        data = await body_of(request)
        return await settle(form_id, "answered", data.get("answers") if isinstance(data.get("answers"), dict) else data)

    @app.post(prefix + "/{form_id}/skip")
    async def ask_skip(form_id: str):
        return await settle(form_id, "skipped")

    @app.post(prefix + "/{form_id}/cancel")
    async def ask_cancel(form_id: str):
        return await settle(form_id, "cancelled")
