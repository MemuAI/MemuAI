"""Round 27: new Quick Ask features (plugin ``features`` in ``pgstatic/quick/plugins``).

* **Screen region** — freeze the monitor under the cursor, let the user drag a rectangle in a full-screen
  overlay (``/quick/region.html``) and attach the crop to the next question (OCR / describe / translate /
  ask). The overlay and the capture are Windows-only (:mod:`app.quick_features_win`).
* **Clipboard history** — opt-in, local, text only. Copies of password managers and of anything marked
  ``ExcludeClipboardContentFromMonitorProcessing`` / ``CanIncludeInClipboardHistory = 0`` /
  ``Clipboard Viewer Ignore`` are never read; likely secrets (API keys, private keys, JWTs, password-like
  tokens) are skipped by default. Pause, clear, pin, search; paste back into the program.
* **Snippets** — ``;keyword`` in the input: text snippets with variables (``{date}``, ``{date+1}``,
  ``{time}``, ``{weekday}``, ``{clipboard}``, ``{selection}``, ``{input}``, ``{uuid}``, ``{cursor}``) are
  pasted into the program the window was opened over; AI snippets are prompt templates sent to the model.
* **Stickers** — an answer pinned into a small always-on-top window (several at once, kept between runs).
* **Documents** — PDF / DOCX / long text files dropped into the window become the question's context
  (the same parser as Telegram and the chat, :func:`app.telegram_media.extract_document_text`).

Storage (outside ``preferences.json``: the main window saves whole snapshots of it):
``memuai/quick_features.json`` (settings + snippets), ``memuai/quick_clipboard.json``,
``memuai/quick_stickers.json``. Routes: ``/api/quick/features/*`` (registered through
:func:`app.quick_ask_api.register_router`).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("memuai.quick.features")

# ---------------------------------------------------------------- settings

PASSWORD_MANAGERS = [
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe",
    "enpass.exe", "keeperpasswordmanager.exe", "roboform.exe", "nordpass.exe", "proton pass.exe",
    "passwordsafe.exe", "pwsafe.exe", "kaspersky password manager.exe", "stickypassword.exe",
]
CLIPBOARD_LIMITS = (20, 1000)
CLIPBOARD_ITEM_CHARS = 8000
SNIPPETS_LIMIT = 200
STICKERS_LIMIT = 24
STICKERS_OPEN = 8
STICKER_COLORS = ("", "yellow", "green", "blue", "pink", "violet")
DOCUMENT_MAX_BYTES = 15 * 1024 * 1024
DOCUMENT_MAX_CHARS = 60_000           # = quick_ask_api.MAX_SELECTION: what the model receives

DEFAULT_SETTINGS: Dict[str, Any] = {
    "region": True,
    "region_hotkey": "",
    "clipboard": False,               # opt-in: a clipboard log is sensitive
    "clipboard_paused": False,
    "clipboard_limit": 200,
    "clipboard_skip_secrets": True,
    "clipboard_ignore": [],           # extra process names (the password managers above are always ignored)
    "clipboard_hotkey": "",
    "snippets": True,
    "stickers": True,
    "documents": True,
}


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "on", "yes"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no"):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _hotkey(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    from .quick_ask_core import HotkeyError, parse_hotkey
    try:
        return parse_hotkey(text)[2]
    except HotkeyError:
        return ""


HOTKEY_ERRORS_EN = {
    "empty": "No hotkey given", "unknown_key": "Unknown key", "two_keys": "Only one regular key is allowed",
    "no_key": "Add a regular key to the modifiers, e.g. S", "need_modifier": "At least one modifier is needed: Ctrl, Alt or Win",
    "shift_only": "Shift with a letter is needed for typing — add Ctrl, Alt or Win", "reserved": "Taken by Windows",
    "editing": "Every program needs this combination for editing text",
}


def hotkey_error(value: Any, lang: str = "ru") -> str:
    """Why a typed hotkey is invalid (``""`` — valid or empty); the core parser speaks Russian only."""
    text = str(value or "").strip()
    if not text:
        return ""
    from .quick_ask_core import HotkeyError, parse_hotkey
    try:
        parse_hotkey(text)
    except HotkeyError as exc:
        return HOTKEY_ERRORS_EN.get(exc.code, "Invalid hotkey") + f" ({text})" if lang == "en" else str(exc)
    return ""


def hotkey_owner(hotkey: Any, lang: str = "ru", *, skip: str = "") -> str:
    """Round 27 (review): which OTHER global hotkey of MemuAI already is ``hotkey`` — Quick Ask itself, the region /
    clipboard hotkeys here, a custom command — as a phrase for an error (``""`` — none of ours). Windows only says
    «already registered», and the settings then blamed «another program» for MemuAI's own hotkey.
    ``skip``: ``"quick"`` / ``"region"`` / ``"clipboard"`` / ``"command:<id>"`` / ``"canvas:<id>"`` — the one being set."""
    from .quick_ask_core import normalize_hotkey
    wanted = normalize_hotkey(hotkey, "")
    if not wanted:
        return ""
    if skip != "quick":
        try:
            from .pgdata import load_preferences
            prefs = load_preferences()
            if prefs.get("quick_ask_enabled", True) and normalize_hotkey(prefs.get("quick_ask_hotkey"), "") == wanted:
                return tr(lang, "быстрый доступ (открывает окно)", "Quick Ask (opens the window)")
        except Exception:
            pass
    try:
        settings = load_config(lang)["settings"]
    except Exception:
        settings = {}
    for key, flag, ru, en in (("region_hotkey", "region", "«Спросить про область экрана»", "“Ask about a screen region”"),
                              ("clipboard_hotkey", "clipboard", "«История буфера обмена»", "“Clipboard history”")):
        if skip != flag and settings.get(flag) and settings.get(key) == wanted:
            return tr(lang, ru, en)
    try:                                       # r27-hunt-canvas: Quick Actions buttons with a global hotkey
        from .quick_canvas import global_hotkeys, load as load_canvas
        doc = load_canvas()
        for action_id, combo in global_hotkeys(doc).items():
            if skip != "canvas:" + action_id and normalize_hotkey(combo, "") == wanted:
                label = next((a.get("label") for a in doc["actions"] if a["id"] == action_id), "") or "…"
                return tr(lang, f"быстрая кнопка «{label}»", f"the quick button “{label}”")
    except Exception:
        pass
    try:
        from .assistant_commands import Store
        for command in Store().load().get("commands") or []:
            if skip == "command:" + str(command.get("id")) or not command.get("enabled", True):
                continue
            if normalize_hotkey(command.get("hotkey"), "") == wanted:
                return tr(lang, f"команда «{command.get('name')}»", f"the command “{command.get('name')}”")
    except Exception:
        pass
    return ""


def normalize_settings(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    for key in ("region", "clipboard", "clipboard_paused", "clipboard_skip_secrets", "snippets", "stickers", "documents"):
        out[key] = _bool(raw.get(key), DEFAULT_SETTINGS[key])
    out["clipboard_limit"] = _int(raw.get("clipboard_limit"), DEFAULT_SETTINGS["clipboard_limit"], *CLIPBOARD_LIMITS)
    ignore = raw.get("clipboard_ignore")
    if isinstance(ignore, str):
        ignore = re.split(r"[,;\n]+", ignore)
    names: List[str] = []
    for name in ignore if isinstance(ignore, list) else []:
        name = str(name or "").strip().lower()[:80]
        if name and not name.endswith(".exe"):
            name += ".exe"
        if name and re.fullmatch(r"[^\\/:*?\"<>|]+", name) and name not in names:
            names.append(name)
    out["clipboard_ignore"] = names[:40]
    out["region_hotkey"] = _hotkey(raw.get("region_hotkey"))
    out["clipboard_hotkey"] = _hotkey(raw.get("clipboard_hotkey"))
    if out["clipboard_hotkey"] and out["clipboard_hotkey"] == out["region_hotkey"]:
        out["clipboard_hotkey"] = ""
    return out


# ---------------------------------------------------------------- snippets

def default_snippets(lang: str = "ru") -> List[Dict[str, Any]]:
    """Seeded once (the user can edit or delete every one of them)."""
    en = lang == "en"
    rows = [
        ("date", "Today's date" if en else "Сегодняшняя дата", "text", "{date}"),
        ("thanks" if en else "спасибо", "Thank-you reply" if en else "Благодарность",
         "text", "Thank you for your message! {cursor}\n\nBest regards," if en
         else "Спасибо за письмо! {cursor}\n\nС уважением,"),
        ("no" if en else "отказ", "Decline politely" if en else "Вежливо отказать", "ai",
         "Write a warm but unambiguous polite refusal to this message. Short, no excessive apologies. "
         "Return only the reply text." if en else
         "Напиши тёплый, но однозначный вежливый отказ на это сообщение. Коротко, без лишних извинений. "
         "Верни только текст ответа."),
        ("formal" if en else "офиц", "Make it formal" if en else "Сделать официальнее", "ai",
         "Rewrite the text in a formal business style, keep the meaning and the language. Return only the text."
         if en else "Перепиши текст в официально-деловом стиле, сохрани смысл и язык. Верни только текст."),
        ("short" if en else "короче", "Shorter and simpler" if en else "Короче и проще", "ai",
         "Make the text twice as short and simpler, keep every fact and the language. Return only the text."
         if en else "Сократи текст вдвое и сделай проще, сохрани все факты и язык. Верни только текст."),
        ("fix" if en else "исправь", "Fix mistakes" if en else "Исправить ошибки", "ai",
         "Fix spelling, grammar and punctuation, keep the style and the language. Return only the corrected text."
         if en else "Исправь орфографию, грамматику и пунктуацию, сохрани стиль и язык. Верни только исправленный текст."),
    ]
    now = int(time.time() * 1000)
    return [{"id": f"s{i}", "keyword": keyword, "name": name, "kind": kind, "text": text, "created": now}
            for i, (keyword, name, kind, text) in enumerate(rows)]


_SEED_FIELDS = ("keyword", "name", "kind", "text")


def localize_defaults(rows: List[Dict[str, Any]], lang: str = "ru") -> List[Dict[str, Any]]:
    """Round 27 (polish): a seeded snippet / AI template the user has not changed (every field exactly as seeded, in
    either language) follows the interface language — a Russian interface no longer shows «Today's date» because
    the first load happened in English. An edited one is the user's and stays as it is; so does a default whose
    keyword in the other language is already taken by another snippet."""
    seeds = {code: {row["id"]: row for row in default_snippets(code)} for code in ("ru", "en")}
    target = seeds["en" if lang == "en" else "ru"]
    out: List[Dict[str, Any]] = []
    for row in rows:
        sid = row.get("id")
        new = target.get(sid)
        if new is not None and any(all(row.get(k) == seeds[code][sid][k] for k in _SEED_FIELDS) for code in seeds):
            taken = new["keyword"] and new["keyword"] != row.get("keyword") and any(
                other is not row and other.get("keyword") == new["keyword"] for other in rows)
            if not taken:
                row = dict(row, **{k: new[k] for k in _SEED_FIELDS})
        out.append(row)
    return out


def normalize_snippet(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "")[:20000]
    keyword = re.sub(r"\s+", "", str(raw.get("keyword") or "")).lstrip(";").lower()[:32]
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:80]
    if not text.strip() or not (keyword or name):
        return None
    sid = str(raw.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sid):
        sid = "s" + uuid.uuid4().hex[:10]
    return {"id": sid, "keyword": keyword, "name": name or keyword, "kind": "ai" if raw.get("kind") == "ai" else "text",
            "text": text, "created": _int(raw.get("created"), int(time.time() * 1000), 0, 1 << 50)}


def normalize_snippets(rows: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_ids, seen_keys = set(), set()
    for raw in rows if isinstance(rows, list) else []:
        row = normalize_snippet(raw)
        if not row or row["id"] in seen_ids:
            continue
        if row["keyword"] and row["keyword"] in seen_keys:
            row["keyword"] = ""          # duplicates keep their name; only the first owns the keyword
        seen_ids.add(row["id"])
        if row["keyword"]:
            seen_keys.add(row["keyword"])
        out.append(row)
    return out[:SNIPPETS_LIMIT]


WEEKDAYS = {"ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
_VAR = re.compile(r"\{\{|\}\}|\{(date|time|datetime|weekday|clipboard|selection|input|uuid|cursor)(?:([+-])(\d{1,4}))?\}")


def expand(text: str, *, now: Optional[datetime] = None, clipboard: str = "", selection: str = "", input_text: str = "",
           lang: str = "ru") -> Dict[str, Any]:
    """Variables of a text snippet → ``{"text", "cursor"}``; ``cursor`` = characters AFTER the caret (``{cursor}``),
    counted the way a text field moves the caret (CRLF is one step), or ``0``."""
    now = now or datetime.now()
    en = lang == "en"
    marker = "\x00cursor\x00"
    used_cursor = [False]

    def sub(match: "re.Match[str]") -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        name, sign, amount = match.group(1), match.group(2), match.group(3)
        if name in ("date", "weekday", "datetime") and sign:
            shift = int(amount) * (1 if sign == "+" else -1)
            day = now + timedelta(days=shift)
        else:
            if sign:
                return token
            day = now
        if name == "date":
            return day.strftime("%Y-%m-%d") if en else day.strftime("%d.%m.%Y")
        if name == "time":
            return now.strftime("%H:%M")
        if name == "datetime":
            return day.strftime("%Y-%m-%d %H:%M") if en else day.strftime("%d.%m.%Y %H:%M")
        if name == "weekday":
            return WEEKDAYS["en" if en else "ru"][day.weekday()]
        if name == "clipboard":
            return clipboard
        if name == "selection":
            return selection
        if name == "input":
            return input_text
        if name == "uuid":
            return str(uuid.uuid4())
        if name == "cursor":
            if used_cursor[0]:
                return ""
            used_cursor[0] = True
            return marker
        return token

    out = _VAR.sub(sub, str(text or ""))
    cursor = 0
    if marker in out:
        head, tail = out.split(marker, 1)
        out = head + tail
        cursor = len(tail.replace("\r\n", "\n"))
    return {"text": out, "cursor": cursor}


def match_snippets(snippets: List[Dict[str, Any]], query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """``;kw rest`` → snippets whose keyword starts with ``kw`` (exact first), then by name words."""
    query = str(query or "").strip()
    if query.startswith(";"):
        query = query[1:]
    word = query.split(None, 1)[0].lower() if query.strip() else ""
    if not word:
        return list(snippets[:limit])
    scored = []
    for row in snippets:
        keyword, name = row["keyword"], row["name"].lower()
        if keyword == word:
            score = 0
        elif keyword.startswith(word):
            score = 1
        elif any(part.startswith(word) for part in re.split(r"\W+", name) if part):
            score = 2
        elif word in keyword or word in name:
            score = 3
        else:
            continue
        scored.append((score, len(keyword), row))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [row for _s, _l, row in scored[:limit]]


# ---------------------------------------------------------------- clipboard: what is never stored

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk)-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b\d{9,10}:AA[A-Za-z0-9_-]{30,}\b"),                     # Telegram bot token
    re.compile(r"(?i)\b(?:password|passwd|pwd|пароль|api[_-]?key|secret|token)\s*[:=]\s*\S{6,}"),
    # Round 34: more provider tokens (Hugging Face, GitLab, xAI, Groq, Replicate, npm, Stripe webhooks, Google OAuth,
    # Yandex Cloud) and an «Authorization: Bearer …» header
    re.compile(r"\b(?:hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9]{40,}|gsk_[A-Za-z0-9]{40,}|"
               r"r8_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,}|whsec_[A-Za-z0-9]{24,}|ya29\.[A-Za-z0-9_-]{30,}|"
               r"AQVN[A-Za-z0-9_-]{30,}|y0_[A-Za-z0-9_-]{30,})"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
]
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def _luhn(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        value = int(ch)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _has_card(text: str) -> bool:
    """A bank card number (13–19 digits, spaces/dashes allowed, Luhn-valid) — never kept in the history."""
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits):
            return True
    return False
_PASSWORD_SYMBOLS = set("!@#$%^&*()+=[]{};:'\",<>?/\\|`~")


def looks_secret(text: str) -> bool:
    """API keys, private keys, JWTs, «password: …» lines and a lone password-like token."""
    value = str(text or "").strip()
    if not value:
        return False
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS) or _has_card(value):
        return True
    if 8 <= len(value) <= 64 and not re.search(r"\s", value) and not re.match(r"(?i)^(?:https?://|www\.|[a-z]:\\|/|\\\\)", value) \
            and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", value):
        classes = sum([any(c.islower() for c in value), any(c.isupper() for c in value),
                       any(c.isdigit() for c in value), any(c in _PASSWORD_SYMBOLS for c in value)])
        if classes >= 4 or (classes == 3 and any(c in _PASSWORD_SYMBOLS for c in value) and any(c.isdigit() for c in value)
                            and not re.search(r"[A-Za-zА-Яа-я]{5,}", value)):
            return True
    return False


def ignored_process(name: str, settings: Dict[str, Any]) -> bool:
    name = str(name or "").strip().lower()
    return bool(name) and (name in PASSWORD_MANAGERS or name in settings.get("clipboard_ignore", [])
                           or "password" in name or "keepass" in name)


# ---------------------------------------------------------------- files

def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)


def _file(name: str) -> Path:
    return _memu_dir() / name


_LOCK = threading.RLock()


def _load(name: str, default: Any, validator: Callable[[Any], bool]) -> Any:
    from .storage import load_json_with_recovery
    try:
        return load_json_with_recovery(_file(name), default, validator=validator)
    except (OSError, ValueError):
        return default


def _save(name: str, data: Any) -> None:
    from .storage import save_json
    path = _file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data, backup=False)


def load_config(lang: str = "ru") -> Dict[str, Any]:
    """``{"settings", "snippets"}``; the first load seeds the default snippets, untouched defaults are in ``lang``."""
    with _LOCK:
        data = _load("quick_features.json", None, lambda v: isinstance(v, dict))
        if not isinstance(data, dict):
            data = {"settings": dict(DEFAULT_SETTINGS), "snippets": default_snippets(lang)}
            try:
                _save("quick_features.json", data)
            except OSError as exc:
                log.warning("quick features: cannot save defaults: %s", exc)
        return {"settings": normalize_settings(data.get("settings")),
                "snippets": localize_defaults(normalize_snippets(data.get("snippets")), lang)}


def save_config(*, settings: Any = None, snippets: Any = None) -> Dict[str, Any]:
    with _LOCK:
        data = load_config()
        if settings is not None:
            merged = dict(data["settings"])
            merged.update(settings if isinstance(settings, dict) else {})
            data["settings"] = normalize_settings(merged)
        if snippets is not None:
            data["snippets"] = normalize_snippets(snippets)
        _save("quick_features.json", data)
        return data


class ClipboardStore:
    """``memuai/quick_clipboard.json``: newest first, pinned rows survive the limit and «Clear»."""

    NAME = "quick_clipboard.json"

    def __init__(self) -> None:
        self._rows: Optional[List[Dict[str, Any]]] = None

    def rows(self) -> List[Dict[str, Any]]:
        with _LOCK:
            if self._rows is None:
                raw = _load(self.NAME, [], lambda v: isinstance(v, list))
                self._rows = [row for row in raw if isinstance(row, dict) and isinstance(row.get("text"), str)
                              and row.get("id")][: CLIPBOARD_LIMITS[1]]
            return self._rows

    def _commit(self) -> None:
        try:
            _save(self.NAME, self._rows or [])
        except OSError as exc:
            log.warning("quick clipboard: save failed: %s", exc)

    def add(self, text: str, *, app: str = "", limit: int = 200) -> Optional[Dict[str, Any]]:
        text = str(text or "")
        if not text.strip():
            return None
        clipped = text[:CLIPBOARD_ITEM_CHARS]
        with _LOCK:
            rows = self.rows()
            old = next((row for row in rows if row["text"] == clipped), None)
            if old is not None:
                rows.remove(old)
            row = {"id": old["id"] if old else "c" + uuid.uuid4().hex[:12], "text": clipped, "t": int(time.time() * 1000),
                   "app": str(app or "")[:80], "pinned": bool(old and old.get("pinned")), "chars": len(text),
                   "count": int((old or {}).get("count") or 0) + 1}
            rows.insert(0, row)
            unpinned = [r for r in rows if not r.get("pinned")]
            for extra in unpinned[max(1, limit):]:
                rows.remove(extra)
            self._commit()
            return row

    def search(self, query: str = "", limit: int = 60) -> Dict[str, Any]:
        needle = str(query or "").strip().lower()
        rows = self.rows()
        found = [row for row in rows if not needle or needle in row["text"].lower() or needle in row.get("app", "").lower()]
        found.sort(key=lambda r: (not r.get("pinned"), -int(r.get("t") or 0)))
        return {"items": [dict(row) for row in found[:limit]], "total": len(rows), "found": len(found)}

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        return next((row for row in self.rows() if row["id"] == item_id), None)

    def update(self, item_id: str, action: str) -> bool:
        with _LOCK:
            rows = self.rows()
            row = self.get(item_id) if item_id else None
            if action == "clear":
                rows[:] = [r for r in rows if r.get("pinned")]
            elif action == "clear_all":
                rows[:] = []
            elif row is None:
                return False
            elif action == "delete":
                rows.remove(row)
            elif action in ("pin", "unpin"):
                row["pinned"] = action == "pin"
            else:
                return False
            self._commit()
            return True


CLIPBOARD = ClipboardStore()


def normalize_sticker(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "")[:20000]
    if not text.strip():
        return None
    sid = str(raw.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sid):
        sid = "k" + uuid.uuid4().hex[:10]
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()[:120]
    geometry = raw.get("geometry") if isinstance(raw.get("geometry"), dict) else {}
    geo = {}
    for key, low, high in (("x", -100000, 100000), ("y", -100000, 100000), ("w", 160, 2000), ("h", 90, 2000)):
        if key in geometry:
            geo[key] = _int(geometry.get(key), 0 if key in "xy" else 320, low, high)
    return {"id": sid, "title": title, "text": text, "color": raw.get("color") if raw.get("color") in STICKER_COLORS else "",
            "open": _bool(raw.get("open"), True), "geometry": geo, "chat": str(raw.get("chat") or "")[:40],
            "created": _int(raw.get("created"), int(time.time() * 1000), 0, 1 << 50),
            "updated": _int(raw.get("updated"), int(time.time() * 1000), 0, 1 << 50)}


def load_stickers() -> List[Dict[str, Any]]:
    raw = _load("quick_stickers.json", [], lambda v: isinstance(v, list))
    out, seen = [], set()
    for row in raw if isinstance(raw, list) else []:
        sticker = normalize_sticker(row)
        if sticker and sticker["id"] not in seen:
            seen.add(sticker["id"])
            out.append(sticker)
    return out[:STICKERS_LIMIT]


def mutate_stickers(fn: Callable[[List[Dict[str, Any]]], Any]) -> Any:
    with _LOCK:
        rows = load_stickers()
        result = fn(rows)
        _save("quick_stickers.json", rows[:STICKERS_LIMIT])
        return result


def sticker_title(text: str, lang: str = "ru") -> str:
    """First meaningful line of an answer, without markdown marks."""
    for raw in str(text or "").splitlines():
        if raw.strip().startswith("```"):
            continue
        line = re.sub(r"^[#>*\-\s\d.)]+|[*_`#]+", "", raw).strip()
        if line:
            return line[:60] + ("…" if len(line) > 60 else "")
    return tr(lang, "Стикер", "Sticker")


# ---------------------------------------------------------------- documents

def document_text(name: str, data_url: str, lang: str = "ru") -> Dict[str, Any]:
    """A dropped document → ``{"name", "text", "chars", "truncated"}`` or ``{"error", "code"}``."""
    name = Path(str(name or "file")).name[:200] or "file"
    data_url = str(data_url or "")
    if "," not in data_url:
        return {"error": tr(lang, "Файл пустой", "The file is empty"), "code": "empty"}
    meta, encoded = data_url.split(",", 1)
    if len(encoded) > DOCUMENT_MAX_BYTES * 4 // 3 + 16:
        return {"error": tr(lang, "Файл больше 15 МБ", "The file is over 15 MB"), "code": "too_large"}
    try:
        blob = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        return {"error": tr(lang, "Файл повреждён", "The file is damaged"), "code": "bad_data"}
    mime = meta[5:].split(";", 1)[0].strip().lower() if meta.startswith("data:") else ""
    lower = name.lower()
    if lower.endswith((".doc", ".xls", ".ppt", ".pages", ".odt", ".rtf")):
        return {"error": tr(lang, "Этот формат не читается — сохраните файл как PDF, DOCX или TXT",
                            "This format cannot be read — save the file as PDF, DOCX or TXT"), "code": "format"}
    from . import telegram_media
    try:
        text = telegram_media.extract_document_text(name, mime, blob)
    except (ValueError, KeyError, OSError) as exc:
        return {"error": tr(lang, f"Файл не разобран: {exc}", f"Could not parse the file: {exc}"), "code": "parse"}
    except Exception as exc:          # zipfile.BadZipFile, pypdf errors on broken files
        return {"error": tr(lang, f"Файл не разобран: {exc}", f"Could not parse the file: {exc}"), "code": "parse"}
    if lower.endswith(".pdf") and not text:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return {"error": tr(lang, "Для PDF нужен компонент pypdf", "PDF needs the pypdf component"), "code": "pypdf"}
        return {"error": tr(lang, "В PDF нет текстового слоя (это скан) — сделайте снимок области экрана и спросите о нём",
                            "The PDF has no text layer (a scan) — capture a screen region and ask about it"), "code": "scan"}
    text = str(text or "").replace("\x00", "").strip()
    if not text:
        return {"error": tr(lang, "Из файла не удалось извлечь текст", "No text could be extracted from the file"), "code": "empty"}
    from .quick_ask_api import clip_middle
    return {"name": name, "text": clip_middle(text, DOCUMENT_MAX_CHARS), "chars": len(text), "truncated": len(text) > DOCUMENT_MAX_CHARS}


# ---------------------------------------------------------------- region selection (pure math)

def crop_box(rect: Any, size: tuple) -> Optional[tuple]:
    """Fractions ``{x, y, w, h}`` of the frozen monitor image → a pixel box, or ``None`` if too small."""
    if not isinstance(rect, dict):
        return None
    try:
        x, y, w, h = (float(rect.get(key)) for key in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        return None
    if any(v != v for v in (x, y, w, h)):         # NaN
        return None
    width, height = int(size[0]), int(size[1])
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    left = max(0, min(width, round(x * width)))
    top = max(0, min(height, round(y * height)))
    right = max(0, min(width, round((x + w) * width)))
    bottom = max(0, min(height, round((y + h) * height)))
    if right - left < 4 or bottom - top < 4:
        return None
    return left, top, right, bottom


class RegionSession:
    """One region pick: the frozen image, the overlay's answer and who waits for it."""

    def __init__(self, image: Any, monitor: Dict[str, Any], lang: str) -> None:
        self.token = uuid.uuid4().hex
        self.image = image
        self.monitor = monitor
        self.lang = lang
        self.done = threading.Event()
        self.result: Dict[str, Any] = {"cancelled": True}
        self.created = time.monotonic()
        self._jpeg: Optional[bytes] = None

    def preview(self) -> bytes:
        if self._jpeg is None:
            import io
            buffer = io.BytesIO()
            self.image.convert("RGB").save(buffer, format="JPEG", quality=90)
            self._jpeg = buffer.getvalue()
        return self._jpeg

    def finish(self, rect: Any) -> Dict[str, Any]:
        if self.done.is_set():
            return self.result
        box = crop_box(rect, self.image.size) if rect is not None else None
        if box is None:
            self.result = {"cancelled": True}
        else:
            from .quick_ask import encode_image
            crop = self.image.crop(box)
            self.result = encode_image(crop, max_side=2048, box=list(box))
            self.result["label"] = f"{box[2] - box[0]}×{box[3] - box[1]}"
        self.done.set()
        return self.result


# ---------------------------------------------------------------- routes

def _manager(request: Request) -> Any:
    return getattr(request.app.state, "quick_ask", None)


def _lang(body: Dict[str, Any], request: Optional[Request] = None) -> str:
    lang = str(body.get("lang") or "")
    if lang not in ("ru", "en") and request is not None:
        lang = str(request.query_params.get("lang") or "")
    if lang not in ("ru", "en"):
        try:
            from .pgdata import load_preferences
            lang = "en" if load_preferences().get("language") == "en" else "ru"
        except Exception:
            lang = "ru"
    return lang


async def _json(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _native(request: Request):
    """The Windows side (``None`` on other systems or without the Quick Ask window)."""
    manager = _manager(request)
    if manager is None or not getattr(manager, "native_ok", False):
        return None
    from . import quick_features_win
    return quick_features_win.runtime(manager)


router = APIRouter()


@router.get("/api/quick/features")
async def features_state(request: Request):
    lang = _lang({}, request)
    config = await asyncio.to_thread(load_config, lang)
    native = _native(request)
    status: Dict[str, Any] = {}
    if native is not None:
        status = await asyncio.to_thread(native.ensure_started, config["settings"], lang)
    stickers = await asyncio.to_thread(load_stickers)
    return {"settings": config["settings"], "snippets": config["snippets"], "native": native is not None,
            "status": status, "stickers": [_sticker_public(s) for s in stickers],
            "clipboard_count": len(CLIPBOARD.rows())}


def _sticker_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: row[key] for key in ("id", "title", "text", "color", "open", "created", "updated", "chat")}


@router.post("/api/quick/features/settings")
async def features_settings(request: Request):
    body = await _json(request)
    patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
    patch = {key: value for key, value in patch.items() if key in DEFAULT_SETTINGS}
    lang = _lang(body)
    for key in ("region_hotkey", "clipboard_hotkey"):
        problem = hotkey_error(patch.get(key), lang) if key in patch else ""
        if not problem and patch.get(key):
            owner = await asyncio.to_thread(hotkey_owner, patch[key], lang, skip=key.split("_", 1)[0])
            if owner:
                problem = tr(lang, f"Это сочетание уже занято в MemuAI: {owner}", f"MemuAI already uses this hotkey: {owner}")
        if problem:
            return JSONResponse({"error": problem, "field": key}, 400)
    data = await asyncio.to_thread(save_config, settings=patch)
    native = _native(request)
    status = await asyncio.to_thread(native.apply, data["settings"], lang) if native is not None else {}
    return {"ok": True, "settings": data["settings"], "status": status}


@router.post("/api/quick/features/snippets")
async def features_snippets(request: Request):
    body = await _json(request)
    if not isinstance(body.get("snippets"), list):
        return JSONResponse({"error": tr(_lang(body), "Нужен список сниппетов", "A list of snippets is required")}, 400)
    data = await asyncio.to_thread(save_config, snippets=body["snippets"])
    return {"ok": True, "snippets": data["snippets"]}


@router.post("/api/quick/features/snippets/use")
async def features_snippet_use(request: Request):
    """Expand a text snippet; ``insert: true`` also pastes it into the program the window was opened over
    and puts the caret at ``{cursor}`` (Windows). Without the native side the page copies ``text``."""
    body = await _json(request)
    lang = _lang(body)
    config = await asyncio.to_thread(load_config, lang)
    row = next((s for s in config["snippets"] if s["id"] == str(body.get("id") or "")), None)
    if row is None:
        return JSONResponse({"error": tr(lang, "Сниппет не найден", "Snippet not found")}, 404)
    native = _native(request)
    if row["kind"] == "ai":
        # An AI template is sent by the page itself (MemuQuick.send); here it only gets the clipboard text to work on.
        clipboard = await asyncio.to_thread(native.clipboard_text) if native is not None else ""
        return {"ok": True, "kind": "ai", "clipboard": clipboard[:CLIPBOARD_ITEM_CHARS * 4]}
    clipboard = await asyncio.to_thread(native.clipboard_text) if native is not None and "{clipboard" in row["text"] else ""
    if not clipboard and isinstance(body.get("clipboard"), str):
        clipboard = body["clipboard"][:CLIPBOARD_ITEM_CHARS * 4]
    out = expand(row["text"], clipboard=clipboard, selection=str(body.get("selection") or "")[:40000],
                 input_text=str(body.get("input") or "")[:20000], lang=lang)
    out.update(ok=True, kind="text", inserted=False)
    if body.get("insert") and native is not None:
        out["inserted"] = await asyncio.to_thread(native.insert, out["text"], out["cursor"])
        if not out["inserted"]:
            out["error"] = tr(lang, "Не нашёл программу, в которую вставлять, — текст скопирован",
                              "No program to paste into — the text is copied")
            await asyncio.to_thread(native.copy, out["text"])
    return out


@router.get("/api/quick/features/clipboard")
async def features_clipboard(request: Request):
    query = str(request.query_params.get("q") or "")[:200]
    limit = _int(request.query_params.get("limit"), 60, 1, 300)
    config = await asyncio.to_thread(load_config)
    native = _native(request)
    status = await asyncio.to_thread(native.ensure_started, config["settings"], _lang({}, request)) if native is not None else {}
    result = await asyncio.to_thread(CLIPBOARD.search, query, limit)
    result.update(settings={key: config["settings"][key] for key in ("clipboard", "clipboard_paused", "clipboard_limit")},
                  native=native is not None, status=status)
    return result


@router.post("/api/quick/features/clipboard")
async def features_clipboard_action(request: Request):
    body = await _json(request)
    lang = _lang(body)
    action = str(body.get("action") or "")
    item_id = str(body.get("id") or "")
    if action in ("pin", "unpin", "delete", "clear", "clear_all"):
        ok = await asyncio.to_thread(CLIPBOARD.update, item_id, action)
        return {"ok": ok} if ok else JSONResponse({"error": tr(lang, "Запись не найдена", "Item not found")}, 404)
    if action in ("paste", "copy"):
        row = CLIPBOARD.get(item_id)
        if row is None:
            return JSONResponse({"error": tr(lang, "Запись не найдена", "Item not found")}, 404)
        native = _native(request)
        # Round 27 (review): the history keeps the first CLIPBOARD_ITEM_CHARS of a long copy — say so, not silently
        cut = int(row.get("chars") or 0) > len(row["text"])
        if native is None:
            return {"ok": False, "native": False, "text": row["text"], "truncated": cut}
        if action == "paste":
            return {"ok": await asyncio.to_thread(native.insert, row["text"], 0), "native": True, "truncated": cut}
        return {"ok": await asyncio.to_thread(native.copy, row["text"]), "native": True, "truncated": cut}
    return JSONResponse({"error": "unknown action"}, 400)


@router.post("/api/quick/features/document")
async def features_document(request: Request):
    body = await _json(request)
    lang = _lang(body)
    result = await asyncio.to_thread(document_text, str(body.get("name") or "file"), str(body.get("data") or ""), lang)
    if result.get("error"):
        return JSONResponse(result, 413 if result.get("code") == "too_large" else 415)
    return result


@router.post("/api/quick/features/region")
async def features_region(request: Request):
    """Freeze the monitor under the cursor, show the overlay and wait for the rectangle (≤ 3 minutes)."""
    body = await _json(request)
    lang = _lang(body)
    native = _native(request)
    if native is None:
        return JSONResponse({"error": tr(lang, "Выделение области экрана работает в приложении MemuAI для Windows",
                                         "Screen regions work in the MemuAI Windows app"), "code": "unsupported"}, 400)
    return await asyncio.to_thread(native.pick_region, lang)


@router.get("/api/quick/features/region/result")
async def features_region_result(request: Request):
    """The region picked with the global hotkey (the window was hidden, nobody awaited ``POST …/region``)."""
    native = _native(request)
    result = native.take_pending_region() if native is not None else None
    return result or {"cancelled": True}


@router.get("/api/quick/features/region/image")
async def features_region_image(request: Request):
    native = _native(request)
    session = native.session(str(request.query_params.get("token") or "")) if native is not None else None
    if session is None:
        return JSONResponse({"error": "no session"}, 404)
    data = await asyncio.to_thread(session.preview)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/quick/features/region/done")
async def features_region_done(request: Request):
    body = await _json(request)
    native = _native(request)
    session = native.session(str(body.get("token") or "")) if native is not None else None
    if session is None:
        return JSONResponse({"error": "no session"}, 404)
    result = await asyncio.to_thread(session.finish, body.get("rect"))
    return {"ok": not result.get("cancelled"), "cancelled": bool(result.get("cancelled"))}


@router.get("/api/quick/features/stickers")
async def features_stickers():
    return {"items": [_sticker_public(s) for s in await asyncio.to_thread(load_stickers)]}


@router.get("/api/quick/features/stickers/{sticker_id}")
async def features_sticker(sticker_id: str):
    row = next((s for s in await asyncio.to_thread(load_stickers) if s["id"] == sticker_id), None)
    return {"sticker": _sticker_public(row)} if row else JSONResponse({"error": "not found"}, 404)


@router.post("/api/quick/features/stickers")
async def features_sticker_action(request: Request):
    body = await _json(request)
    lang = _lang(body)
    action = str(body.get("action") or "")
    sticker_id = str(body.get("id") or "")
    native = _native(request)
    now = int(time.time() * 1000)

    if action == "create":
        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": tr(lang, "Нечего закреплять — ответ пустой", "Nothing to pin — the answer is empty")}, 400)

        def create(rows: List[Dict[str, Any]]):
            row = normalize_sticker({"text": text, "title": body.get("title") or sticker_title(text, lang),
                                     "chat": body.get("chat"), "open": True, "created": now, "updated": now})
            rows.insert(0, row)
            open_rows = [r for r in rows if r["open"]]
            for extra in open_rows[STICKERS_OPEN:]:
                extra["open"] = False
            del rows[STICKERS_LIMIT:]
            return row
        row = await asyncio.to_thread(mutate_stickers, create)
        shown = await asyncio.to_thread(native.show_sticker, row["id"]) if native is not None else False
        return {"ok": True, "sticker": _sticker_public(row), "native": native is not None, "shown": bool(shown)}

    def find(rows: List[Dict[str, Any]]):
        return next((r for r in rows if r["id"] == sticker_id), None)

    if action in ("update", "open", "close", "delete"):
        def change(rows: List[Dict[str, Any]]):
            row = find(rows)
            if row is None:
                return None
            if action == "delete":
                rows.remove(row)
                return row
            if action == "update":
                if isinstance(body.get("text"), str) and body["text"].strip():
                    row["text"] = body["text"][:20000]
                if isinstance(body.get("title"), str):
                    row["title"] = re.sub(r"\s+", " ", body["title"]).strip()[:120] or sticker_title(row["text"], lang)
                if body.get("color") in STICKER_COLORS:
                    row["color"] = body["color"]
            else:
                row["open"] = action == "open"
            row["updated"] = now
            return dict(row)
        row = await asyncio.to_thread(mutate_stickers, change)
        if row is None:
            return JSONResponse({"error": tr(lang, "Стикер не найден", "Sticker not found")}, 404)
        if native is not None:
            if action in ("close", "delete"):
                await asyncio.to_thread(native.close_sticker, sticker_id)
            elif action == "open":
                await asyncio.to_thread(native.show_sticker, sticker_id)
            else:
                await asyncio.to_thread(native.refresh_sticker, sticker_id)
        return {"ok": True, "sticker": _sticker_public(row) if action != "delete" else None}

    if action in ("drag", "resize", "raise"):
        if native is None:
            return {"ok": False, "native": False}
        return await asyncio.to_thread(native.sticker_window_action, sticker_id, action)

    return JSONResponse({"error": "unknown action"}, 400)


def register() -> None:
    from .quick_ask_api import register_router
    register_router(router)


register()
