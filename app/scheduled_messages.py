# -*- coding: utf-8 -*-
"""Round 29: scheduled messages («Отложенная отправка», like Telegram).

A message typed in the composer can be sent later: at an exact time (with an
optional repeat) or «when online» (as soon as the connection and MemuAI Proxy
are available). Each profile keeps its queue in ``memuai/scheduled_messages.json``
(``profiles/<id>/scheduled_messages.json`` for other profiles), written only
through the atomic cross-process helpers of :mod:`app.storage`.

Who sends
---------
* The MemuAI window, while it is open, keeps exact JS timers. A due message of
  the chat that is open is sent through the window's own send path (the same
  prompt, tools, skills and agents as a typed message). A due message of a
  regular chat that is not open is *delegated* (``route = "server"``).
* MemuAI Proxy runs :mod:`app.scheduled_messages_runner`. It executes delegated
  items and — when the window is closed (no heartbeat) and the user allowed it —
  every due regular-chat item, through the same server-side generation path as
  the Telegram Mini App (the answer is written into the chat).
* Work chats and image models need the window: they wait for it.

Every send starts with :func:`claim`, one atomic transaction that moves an item
from ``scheduled`` to ``sending``; two senders can never both own one item.

Missed messages
---------------
A message that became due while nothing could send it (MemuAI and the Proxy were
closed) is *late* once more than :data:`LATE_GRACE_MS` passed. ``schedsend_missed``
decides: ``send`` it anyway (with a notice), ``ask`` the user (status ``missed``)
or ``skip`` it (status ``skipped``, with a notice).
"""
from __future__ import annotations

import copy
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

FILE_NAME = "scheduled_messages.json"
PRESENCE_FILE = "scheduled_messages_presence.json"
VERSION = 1

STATUSES = ("scheduled", "sending", "sent", "failed", "missed", "skipped", "cancelled")
ACTIVE_STATUSES = ("scheduled", "sending", "missed", "failed")
TRIGGERS = ("time", "online")
REPEATS = ("none", "daily", "weekdays", "weekly")
MODES = ("chat", "work")
MISSED_POLICIES = ("send", "ask", "skip")

MAX_ITEMS = 300
MAX_ACTIVE_PER_CHAT = 50
MAX_TEXT = 100_000
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_CHARS = 20 * 1024 * 1024
MAX_ATTACHMENTS_CHARS = 40 * 1024 * 1024
KEEP_FINISHED_MS = 7 * 24 * 3600 * 1000
LATE_GRACE_MS = 3 * 60 * 1000
PRESENCE_STALE_MS = 150 * 1000
SENDING_STALE_MS = 20 * 60 * 1000
ONLINE_RETRY_MS = (60_000, 120_000, 300_000, 600_000, 1_800_000)
ONLINE_GIVE_UP_MS = 3 * 24 * 3600 * 1000
MIN_LEAD_MS = 30 * 1000

PREF_DEFAULTS: Dict[str, Any] = {

    "schedsend_missed": "send",

    "schedsend_background": True,

    "schedsend_notify": True,

    "schedsend_telegram_default": False,
}

_CAP_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def normalize_prefs(out: Dict[str, Any], raw: Any) -> None:
    """Load/save normalization of the ``schedsend_*`` keys (called by pgdata)."""
    source = raw if isinstance(raw, dict) else {}
    policy = str(source.get("schedsend_missed", out.get("schedsend_missed", "send")) or "send").strip().lower()
    out["schedsend_missed"] = policy if policy in MISSED_POLICIES else "send"
    for key in ("schedsend_background", "schedsend_notify", "schedsend_telegram_default"):
        value = source.get(key, out.get(key, PREF_DEFAULTS[key]))
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        out[key] = bool(value)

def now_ms() -> int:
    return int(time.time() * 1000)

def _int(value: Any, default: int = 0) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return number

def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)

def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").replace("\x00", "")[:limit]

def new_id() -> str:
    return "sm-" + uuid.uuid4().hex[:14]

def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)

def path_for(profile_id: Optional[str] = None) -> Path:
    """The queue file of a profile (the active one by default)."""
    from .pgdata import _profile_data_name
    return _memu_dir() / _profile_data_name(FILE_NAME, profile_id)

def presence_path() -> Path:
    return _memu_dir() / PRESENCE_FILE

def revision(profile_id: Optional[str] = None) -> str:
    try:
        stat = path_for(profile_id).stat()
    except (OSError, ValueError):
        return "0"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"

def _attachments(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    total = 0
    for item in (value if isinstance(value, list) else [])[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data") or "")
        if not data.startswith("data:") or len(data) > MAX_ATTACHMENT_CHARS:
            continue
        if total + len(data) > MAX_ATTACHMENTS_CHARS:
            break
        total += len(data)
        mime = str(item.get("type") or data[5:].split(";", 1)[0].split(",", 1)[0] or "")[:120]
        name = Path(str(item.get("name") or "file").replace("\\", "/")).name[:160] or "file"
        rows.append({"name": name, "data": data, "type": mime,
                     "size": max(0, _int(item.get("size"), len(data) * 3 // 4)),
                     "isImg": bool(item.get("isImg")) or mime.startswith("image/")})
    return rows

def _caps(value: Any) -> Dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, bool] = {}
    for key, flag in list(value.items())[:24]:
        if isinstance(key, str) and _CAP_KEY.match(key):
            out[key] = _bool(flag)
    return out

def _tools(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:40] for item in value if isinstance(item, str) and _CAP_KEY.match(item)][:16]

def normalize_item(raw: Any, *, now: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """A stored row with every field in range, or ``None`` for garbage."""
    if not isinstance(raw, dict):
        return None
    stamp = now if now is not None else now_ms()
    item_id = str(raw.get("id") or "")
    if not _ID.match(item_id):
        return None
    chat_id = _text(raw.get("chat_id"), 120).strip()
    if not chat_id:
        return None
    status = str(raw.get("status") or "scheduled")
    trigger = str(raw.get("trigger") or "time")
    repeat = str(raw.get("repeat") or "none")
    mode = str(raw.get("mode") or "chat")
    item: Dict[str, Any] = {
        "id": item_id,
        "chat_id": chat_id,
        "chat_title": _text(raw.get("chat_title"), 200),
        "new_chat": _bool(raw.get("new_chat")),
        "mode": mode if mode in MODES else "chat",
        "project_id": _text(raw.get("project_id"), 120),
        "access_mode": "full" if raw.get("access_mode") == "full" else ("ask" if raw.get("access_mode") else ""),
        "text": _text(raw.get("text"), MAX_TEXT),
        "attachments": _attachments(raw.get("attachments")),
        "model": _text(raw.get("model"), 300),
        "caps": _caps(raw.get("caps")),
        "tools": _tools(raw.get("tools")),
        "imagine": _bool(raw.get("imagine")),
        "trigger": trigger if trigger in TRIGGERS else "time",
        "due_at": max(0, _int(raw.get("due_at"))),
        "repeat": repeat if repeat in REPEATS else "none",
        "notify_telegram": _bool(raw.get("notify_telegram")),
        "status": status if status in STATUSES else "scheduled",
        "route": "server" if raw.get("route") == "server" else "auto",
        "created": _int(raw.get("created"), stamp) or stamp,
        "updated": _int(raw.get("updated"), stamp) or stamp,
        "claimed_by": _text(raw.get("claimed_by"), 20),
        "claimed_at": max(0, _int(raw.get("claimed_at"))),
        "sent_at": max(0, _int(raw.get("sent_at"))),
        "sent_count": max(0, _int(raw.get("sent_count"))),
        "late": _bool(raw.get("late")),
        "attempts": max(0, min(1000, _int(raw.get("attempts")))),
        "next_attempt": max(0, _int(raw.get("next_attempt"))),
        "error": _text(raw.get("error"), 1000),
        "answer_error": _text(raw.get("answer_error"), 1000),
        "model_used": _text(raw.get("model_used"), 300),
        "notice": _bool(raw.get("notice")),
        "notice_kind": _text(raw.get("notice_kind"), 20),
        "sent_by": _text(raw.get("sent_by"), 20),
        "lang": "en" if raw.get("lang") == "en" else "ru",
    }

    if _bool(raw.get("now_requested")):
        item["now_requested"] = True
    if _bool(raw.get("approved")):
        item["approved"] = True
    if item["trigger"] == "time" and not item["due_at"]:
        item["due_at"] = stamp
    if item["trigger"] == "online":
        item["repeat"] = "none"
    if item["mode"] == "work":
        item["route"] = "auto"
    return item

def public_item(item: Dict[str, Any], *, with_data: bool = False) -> Dict[str, Any]:
    """The row for the window: attachment bytes only when asked for (editing / sending)."""
    out = dict(item)
    out["attachments"] = [{key: value for key, value in row.items() if with_data or key != "data"}
                          for row in item.get("attachments") or []]
    return out

def _rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("items")
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw if isinstance(raw, list) else []:
        item = normalize_item(value)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            rows.append(item)
    return rows

def _prune(rows: List[Dict[str, Any]], now: int) -> List[Dict[str, Any]]:
    kept = [row for row in rows
            if row["status"] in ACTIVE_STATUSES or row.get("notice")
            or now - max(row.get("updated") or 0, row.get("sent_at") or 0) < KEEP_FINISHED_MS]
    if len(kept) > MAX_ITEMS:
        active = [row for row in kept if row["status"] in ACTIVE_STATUSES]
        rest = sorted((row for row in kept if row["status"] not in ACTIVE_STATUSES),
                      key=lambda row: row.get("updated") or 0, reverse=True)
        kept = active[:MAX_ITEMS] + rest[:max(0, MAX_ITEMS - len(active))]
    return kept

def load(profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    from .storage import load_json_with_recovery
    raw = load_json_with_recovery(path_for(profile_id), {"version": VERSION, "items": []},
                                  validator=lambda value: isinstance(value, (dict, list)))
    return _rows(raw)

def mutate(change: Callable[[List[Dict[str, Any]]], Any], profile_id: Optional[str] = None,
           *, now: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Any]:
    """One atomic read-modify-write of a profile's queue. ``change(rows)`` edits the
    list in place and returns a result (``False`` = nothing changed, no write)."""
    from .storage import mutate_json
    stamp = now if now is not None else now_ms()
    box: Dict[str, Any] = {}

    def apply(raw: Any) -> Tuple[bool, Any]:
        rows = _rows(raw)
        result = change(rows)
        box["result"] = result
        rows = _prune(rows, stamp)
        box["rows"] = rows
        changed = result is not False
        valid_shape = isinstance(raw, dict) and isinstance(raw.get("items"), list)
        return changed or not valid_shape, {"version": VERSION, "items": rows}

    mutate_json(path_for(profile_id), {"version": VERSION, "items": []}, apply, backup=False,
                validator=lambda value: isinstance(value, (dict, list)), repair_invalid=True)
    return box.get("rows", []), box.get("result")

def next_repeat(due_at: int, repeat: str, *, after: Optional[int] = None) -> int:
    """The next occurrence of a repeating message strictly after ``after`` (ms)."""
    if repeat not in ("daily", "weekdays", "weekly"):
        return 0
    limit = after if after is not None else now_ms()
    moment = datetime.fromtimestamp(due_at / 1000)
    for _ in range(800):
        step = 7 if repeat == "weekly" else 1
        moment = moment + timedelta(days=step)
        if repeat == "weekdays" and moment.weekday() >= 5:
            continue
        stamp = int(moment.timestamp() * 1000)
        if stamp > limit:
            return stamp
    return 0

def is_due(item: Dict[str, Any], now: int) -> bool:
    if item["status"] != "scheduled":
        return False
    if item.get("next_attempt") and item["next_attempt"] > now:
        return False
    if item["trigger"] == "online":
        return True
    return item["due_at"] <= now

def next_due(rows: List[Dict[str, Any]]) -> int:
    stamps = [max(row["due_at"] if row["trigger"] == "time" else 0, row.get("next_attempt") or 0)
              for row in rows if row["status"] == "scheduled"]
    return min(stamps) if stamps else 0

def server_capable(item: Dict[str, Any]) -> bool:
    """Can MemuAI Proxy send it without the window (regular chat, text model)?"""
    if item.get("mode") != "chat" or item.get("imagine"):
        return False
    from_model = str(item.get("model") or "")
    try:
        from .tgapp_chat import is_image_model
        if from_model and is_image_model(from_model):
            return False
    except Exception:
        pass
    return not any(row.get("type", "").startswith("video/") for row in item.get("attachments") or [])

def write_presence(*, profile_id: str = "", now: Optional[int] = None) -> Dict[str, Any]:
    from .storage import save_json
    value = {"seen": now if now is not None else now_ms(), "pid": os.getpid(), "profile": str(profile_id or "")}
    save_json(presence_path(), value, backup=False)
    return value

def clear_presence() -> None:
    try:
        presence_path().unlink()
    except OSError:
        pass

def desktop_alive(now: Optional[int] = None, profile_id: Optional[str] = None) -> bool:
    """Is a MemuAI window open (heartbeat) — for ``profile_id``, when given? The window only
    serves its active profile, so another profile's queue counts as «window closed»."""
    from .storage import load_json_with_recovery
    try:
        value = load_json_with_recovery(presence_path(), {}, validator=lambda data: isinstance(data, dict))
    except Exception:
        return False
    seen = _int((value or {}).get("seen"))
    stamp = now if now is not None else now_ms()
    if not (bool(seen) and 0 <= stamp - seen < PRESENCE_STALE_MS):
        return False
    if profile_id is not None:
        return str((value or {}).get("profile") or "default") == str(profile_id or "default")
    return True

class ScheduleError(ValueError):
    """A user-facing validation error (``ru`` / ``en`` text)."""

    def __init__(self, ru: str, en: str) -> None:
        super().__init__(ru)
        self.ru, self.en = ru, en

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru

def _validate_new(item: Dict[str, Any], rows: List[Dict[str, Any]], now: int) -> None:
    if not item["text"].strip() and not item["attachments"]:
        raise ScheduleError("Пустое сообщение нельзя запланировать", "An empty message cannot be scheduled")
    if item["trigger"] == "time" and item["due_at"] < now + MIN_LEAD_MS:
        raise ScheduleError("Выберите время хотя бы на минуту позже текущего",
                            "Pick a time at least a minute from now")
    if item["trigger"] == "time" and item["due_at"] > now + 366 * 24 * 3600 * 1000:
        raise ScheduleError("Можно запланировать не больше чем на год вперёд", "You can schedule at most a year ahead")
    if item["mode"] == "work" and not item["project_id"]:
        raise ScheduleError("Work-чат без проекта нельзя запланировать", "A Work chat without a project cannot be scheduled")
    active = [row for row in rows if row["chat_id"] == item["chat_id"] and row["status"] in ACTIVE_STATUSES
              and row["id"] != item["id"]]
    if len(active) >= MAX_ACTIVE_PER_CHAT:
        raise ScheduleError(f"В одном чате — не больше {MAX_ACTIVE_PER_CHAT} отложенных сообщений",
                            f"At most {MAX_ACTIVE_PER_CHAT} scheduled messages per chat")

def create(raw: Dict[str, Any], profile_id: Optional[str] = None, *, now: Optional[int] = None) -> Dict[str, Any]:
    stamp = now if now is not None else now_ms()
    data = dict(raw or {})
    if not _ID.match(str(data.get("id") or "")):
        data["id"] = new_id()
    for key in ("status", "claimed_by", "claimed_at", "sent_at", "sent_count", "late", "attempts",
                "next_attempt", "error", "answer_error", "model_used", "notice", "notice_kind", "sent_by"):
        data.pop(key, None)
    data.update({"status": "scheduled", "created": stamp, "updated": stamp})
    item = normalize_item(data, now=stamp)
    if item is None:
        raise ScheduleError("Некорректное сообщение", "Invalid message")

    def change(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        _validate_new(item, rows, stamp)
        rows[:] = [row for row in rows if row["id"] != item["id"]]
        rows.append(item)
        return item

    _rows_after, result = mutate(change, profile_id, now=stamp)
    return result

def _find(rows: List[Dict[str, Any]], item_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in rows if row["id"] == str(item_id or "")), None)

EDITABLE = ("text", "due_at", "repeat", "trigger", "notify_telegram", "model", "caps", "tools", "attachments")

def update(item_id: str, patch: Dict[str, Any], profile_id: Optional[str] = None,
           *, now: Optional[int] = None) -> Dict[str, Any]:
    """Edit a message that has not been sent yet; a failed/missed one is scheduled again."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        row = _find(rows, item_id)
        if row is None:
            raise ScheduleError("Сообщение не найдено — возможно, оно уже отправлено",
                                "Message not found — it may have been sent already")
        if row["status"] not in ("scheduled", "failed", "missed"):
            raise ScheduleError("Сообщение уже отправляется или отправлено", "The message is being sent or was sent")
        merged = dict(row)
        for key in EDITABLE:
            if key in (patch or {}):
                merged[key] = patch[key]
        merged.update({"status": "scheduled", "updated": stamp, "error": "", "attempts": 0, "next_attempt": 0,
                       "notice": False, "notice_kind": "", "claimed_by": "", "claimed_at": 0})
        if "due_at" in (patch or {}) or "trigger" in (patch or {}):
            merged["late"] = False
            merged["route"] = "auto"
        item = normalize_item(merged, now=stamp)
        if item is None:
            raise ScheduleError("Некорректное сообщение", "Invalid message")
        _validate_new(item, rows, stamp)
        rows[rows.index(row)] = item
        return item

    return mutate(change, profile_id, now=stamp)[1]

def delete(item_id: str, profile_id: Optional[str] = None) -> bool:
    def change(rows: List[Dict[str, Any]]) -> Any:
        row = _find(rows, item_id)
        if row is None:
            return False
        if row["status"] == "sending":
            raise ScheduleError("Сообщение уже отправляется", "The message is being sent right now")
        rows.remove(row)
        return True

    return bool(mutate(change, profile_id)[1])

def send_now(item_id: str, profile_id: Optional[str] = None, *, now: Optional[int] = None) -> Dict[str, Any]:
    """Make an item due immediately (also «Send» for a missed or failed one)."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        row = _find(rows, item_id)
        if row is None:
            raise ScheduleError("Сообщение не найдено", "Message not found")
        if row["status"] not in ("scheduled", "failed", "missed"):
            raise ScheduleError("Сообщение уже отправляется или отправлено", "The message is being sent or was sent")
        row.update({"status": "scheduled", "due_at": stamp if row["trigger"] == "time" else row["due_at"],
                    "next_attempt": 0, "error": "", "updated": stamp, "notice": False, "notice_kind": "",
                    "late": False, "now_requested": True})
        if row["trigger"] == "online":
            row["trigger"] = "time"
            row["due_at"] = stamp
        return dict(row)

    return mutate(change, profile_id, now=stamp)[1]

def delegate(item_id: str, profile_id: Optional[str] = None, *, now: Optional[int] = None) -> Dict[str, Any]:
    """The window hands a due regular-chat message to MemuAI Proxy."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Any:
        row = _find(rows, item_id)
        if row is None or row["status"] != "scheduled" or not server_capable(row):
            return False
        row["route"] = "server"
        row["updated"] = stamp
        return dict(row)

    result = mutate(change, profile_id, now=stamp)[1]
    if not result:
        raise ScheduleError("Это сообщение нельзя отправить в фоне", "This message cannot be sent in the background")
    return result

def classify(item: Dict[str, Any], now: int, policy: str, *, delegated: bool = False) -> str:
    """``send`` / ``ask`` / ``skip`` for a due item. Late = past due by more than the grace time,
    unless the user asked to send it now or the window delegated it on time."""
    if item.get("trigger") != "time" or item.get("now_requested") or item.get("approved"):
        return "send"
    late = now - int(item.get("due_at") or 0) > LATE_GRACE_MS
    if not late or delegated:
        return "send"
    return policy if policy in MISSED_POLICIES else "send"

def claim(item_id: str, by: str, profile_id: Optional[str] = None, *, policy: str = "send",
          now: Optional[int] = None) -> Dict[str, Any]:
    """Atomically take a due item for sending.

    Returns ``{"action": "send", "item": …}`` when the caller owns it now, ``{"action": "ask"}`` /
    ``{"action": "skip"}`` when the missed-message policy parked it, or ``{"action": "none"}``."""
    stamp = now if now is not None else now_ms()
    who = "proxy" if by == "proxy" else "desktop"

    def change(rows: List[Dict[str, Any]]) -> Any:
        row = _find(rows, item_id)
        if row is None or not is_due(row, stamp):
            return False

        delegated_at = int(row.get("updated") or 0)
        decision = classify(row, stamp, policy, delegated=row.get("route") == "server" and
                            stamp - delegated_at < LATE_GRACE_MS
                            and delegated_at - int(row.get("due_at") or 0) <= LATE_GRACE_MS)
        if decision == "ask":
            row.update({"status": "missed", "notice": True, "notice_kind": "missed", "updated": stamp})
            return {"action": "ask", "item": dict(row)}
        if decision == "skip":
            row.update({"status": "skipped", "notice": True, "notice_kind": "skipped", "updated": stamp})
            _schedule_next_occurrence(rows, row, stamp)
            return {"action": "skip", "item": dict(row)}
        late = row["trigger"] == "time" and stamp - row["due_at"] > LATE_GRACE_MS and not row.get("now_requested")
        row.update({"status": "sending", "claimed_by": who, "claimed_at": stamp, "late": late,
                    "attempts": row.get("attempts", 0) + 1, "updated": stamp})
        return {"action": "send", "item": dict(row)}

    result = mutate(change, profile_id, now=stamp)[1]
    return result or {"action": "none"}

def _schedule_next_occurrence(rows: List[Dict[str, Any]], row: Dict[str, Any], now: int) -> Optional[Dict[str, Any]]:
    """A repeating message continues as a fresh row (the finished one stays in the log)."""
    if row.get("repeat", "none") == "none" or row.get("trigger") != "time":
        return None
    following = next_repeat(int(row["due_at"]), row["repeat"], after=now + MIN_LEAD_MS)
    if not following:
        return None
    fresh = dict(row)
    fresh.update({"id": new_id(), "status": "scheduled", "due_at": following, "created": now, "updated": now,
                  "claimed_by": "", "claimed_at": 0, "sent_at": 0, "late": False, "attempts": 0,
                  "next_attempt": 0, "error": "", "answer_error": "", "model_used": "", "notice": False,
                  "notice_kind": "", "sent_by": "", "route": "auto", "new_chat": False,
                  "sent_count": int(row.get("sent_count") or 0)})
    fresh.pop("now_requested", None)
    fresh.pop("approved", None)
    item = normalize_item(fresh, now=now)
    if item:
        rows.append(item)
    return item

def complete(item_id: str, ok: bool, profile_id: Optional[str] = None, *, error: str = "",
             answer_error: str = "", model_used: str = "", by: str = "desktop", retry: bool = False,
             notice: Optional[bool] = None, now: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Record the outcome of a claimed send. ``retry`` puts a failed «when online» message back
    in the queue with a back-off instead of failing it."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Any:
        row = _find(rows, item_id)
        if row is None:
            return False
        if ok:
            row.pop("now_requested", None)
            row.pop("approved", None)
            row.update({"status": "sent", "sent_at": stamp, "error": "", "updated": stamp,
                        "answer_error": _text(answer_error, 1000), "model_used": _text(model_used, 300),
                        "sent_count": int(row.get("sent_count") or 0) + 1, "sent_by": by,
                        "claimed_by": "", "next_attempt": 0})
            want_notice = (bool(row.get("late")) or bool(answer_error)) if notice is None else notice
            if want_notice:
                row["notice"] = True
                row["notice_kind"] = "late" if row.get("late") else ("answer_error" if answer_error else "background")
            _schedule_next_occurrence(rows, row, stamp)
            return dict(row)
        attempts = int(row.get("attempts") or 1)
        too_old = stamp - int(row.get("created") or stamp) > ONLINE_GIVE_UP_MS
        if retry and not too_old:
            delay = ONLINE_RETRY_MS[min(len(ONLINE_RETRY_MS) - 1, max(0, attempts - 1))]
            row.update({"status": "scheduled", "next_attempt": stamp + delay, "error": _text(error, 1000),
                        "claimed_by": "", "claimed_at": 0, "updated": stamp})
            return dict(row)
        row.update({"status": "failed", "error": _text(error, 1000) or "error", "claimed_by": "",
                    "claimed_at": 0, "updated": stamp, "notice": True, "notice_kind": "failed"})
        return dict(row)

    return mutate(change, profile_id, now=stamp)[1] or None

def release_stale(profile_id: Optional[str] = None, *, now: Optional[int] = None) -> int:
    """A ``sending`` claim nobody finished (the sender crashed) becomes ``failed`` with a notice —
    never silently re-sent, since the message may already be in the chat."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Any:
        count = 0
        for row in rows:
            if row["status"] == "sending" and stamp - int(row.get("claimed_at") or 0) > SENDING_STALE_MS:
                row.update({"status": "failed", "notice": True, "notice_kind": "failed", "updated": stamp,
                            "error": "interrupted", "claimed_by": ""})
                count += 1
        return count or False

    return int(mutate(change, profile_id, now=stamp)[1] or 0)

def acknowledge(ids: List[str], profile_id: Optional[str] = None) -> int:
    wanted = {str(value) for value in (ids or [])[:500]}

    def change(rows: List[Dict[str, Any]]) -> Any:
        count = 0
        for row in rows:
            if row["id"] in wanted and row.get("notice"):
                row["notice"] = False
                count += 1
        return count or False

    return int(mutate(change, profile_id)[1] or 0)

def approve_missed(item_id: str, profile_id: Optional[str] = None, *, now: Optional[int] = None) -> Dict[str, Any]:
    """«Send» on a missed message: due now, the late policy no longer applies."""
    stamp = now if now is not None else now_ms()

    def change(rows: List[Dict[str, Any]]) -> Any:
        row = _find(rows, item_id)
        if row is None or row["status"] not in ("missed", "failed", "scheduled"):
            raise ScheduleError("Сообщение не найдено", "Message not found")
        row.update({"status": "scheduled", "approved": True, "next_attempt": 0, "notice": False,
                    "notice_kind": "", "updated": stamp, "error": ""})
        return dict(row)

    return mutate(change, profile_id, now=stamp)[1]

def drop_chat(chat_id: str, profile_id: Optional[str] = None) -> int:
    """Remove the queue of a deleted chat."""
    def change(rows: List[Dict[str, Any]]) -> Any:
        before = len(rows)
        rows[:] = [row for row in rows if row["chat_id"] != chat_id or row["status"] == "sending"]
        return (before - len(rows)) or False

    return int(mutate(change, profile_id)[1] or 0)

def summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [row for row in rows if row["status"] in ("scheduled", "sending", "missed", "failed")]
    by_chat: Dict[str, int] = {}
    for row in active:
        by_chat[row["chat_id"]] = by_chat.get(row["chat_id"], 0) + 1
    return {"active": len(active), "by_chat": by_chat, "next_due": next_due(rows),
            "notices": sum(1 for row in rows if row.get("notice"))}

def profile_ids() -> List[str]:
    try:
        from .pgdata import load_profiles
        state = load_profiles()
        ids = [str(item.get("id")) for item in state.get("items") or [] if isinstance(item, dict) and item.get("id")]
        return ids or ["default"]
    except Exception:
        return ["default"]

def deep_copy(item: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(item)
