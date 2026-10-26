# -*- coding: utf-8 -*-
"""Локальные данные панелей (Раунд 23): списки задач («To-Do») и заметки.

Обычная панель показывает данные из источника; эти — данные самого пользователя. Они лежат
отдельно от ``panels.json`` — в ``memuai/panels-local.json`` текущего профиля (атомарная
межпроцессная запись через ``storage.mutate_json``): обновление панелей по расписанию не
переписывает задачи, а удаление панели не удаляет список (его можно показать в другой).

Задача: ``{id t_…, title, done, done_at, created, updated, due "YYYY-MM-DD[THH:MM]", priority 0..3
(0 — нет, 1 — высокий), tags[], notes, subtasks[{id, title, done}], repeat {every: day|weekdays|week|
month|year, n}, remind "YYYY-MM-DDTHH:MM", reminder {local, telegram}, completed_count}``.

Повторяющаяся задача при отметке не закрывается, а переезжает на следующий срок (как в
Todoist): подзадачи сбрасываются, счётчик выполнений растёт. Напоминание ставится через
таймеры «Быстрого доступа» (звук и уведомление Windows) и, если так настроено там, через
Telegram — та же доставка, что у «напомни мне…». Ошибка напоминания задачу не ломает.
"""
from __future__ import annotations

import calendar
import copy
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.panels.local")

FILE_NAME = "panels-local.json"
MAX_LISTS = 30
MAX_ITEMS = 500
MAX_SUBTASKS = 30
MAX_TAGS = 8
REPEATS = ("day", "weekdays", "week", "month", "year")
COLORS = ("accent", "blue", "green", "orange", "red", "purple", "pink", "gray")
_ID = re.compile(r"^[lt]_[a-z0-9]{6,16}$")
_SUB_ID = re.compile(r"^s_[a-z0-9]{4,16}$")

class LocalError(ValueError):
    """Понятная пользователю ошибка операции над задачами (``en`` — тот же текст для английского интерфейса)."""

    def __init__(self, message: str, en: str = "") -> None:
        super().__init__(message)
        self.en = en or message

def _path():
    from .pgdata import MEMU_DIR, _profile_data_name
    try:
        name = _profile_data_name(FILE_NAME)
    except Exception:
        name = FILE_NAME
    return MEMU_DIR / name

def _new(prefix: str, size: int = 10) -> str:
    return f"{prefix}_" + uuid.uuid4().hex[:size]

def _text(value: Any, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[:limit]

def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number < 1e11 else None

def clean_due(value: Any) -> str:
    """«2026-10-08» или «2026-10-08T18:30» (местное время); всё остальное — пусто."""
    text = str(value or "").strip()[:25]
    if not text:
        return ""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}):(\d{2}))?(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?", text)
    if not match:
        return ""
    try:
        day = date.fromisoformat(match.group(1))
    except ValueError:
        return ""
    if match.group(2) is None:
        return day.isoformat()
    hour, minute = int(match.group(2)), int(match.group(3))
    if hour > 23 or minute > 59:
        return day.isoformat()
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}"

def clean_repeat(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        value = {"every": value}
    if not isinstance(value, dict):
        return None
    every = str(value.get("every") or "")
    if every not in REPEATS:
        return None
    try:
        n = max(1, min(365, int(value.get("n") or 1)))
    except (TypeError, ValueError):
        n = 1
    out = {"every": every, "n": 1 if every == "weekdays" else n}
    try:
        anchor = int(value.get("day") or 0)
    except (TypeError, ValueError):
        anchor = 0
    if every in ("month", "year") and 1 <= anchor <= 31:
        out["day"] = anchor
    return out

def clean_tags(value: Any) -> List[str]:
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    out: List[str] = []
    for tag in value if isinstance(value, list) else []:
        tag = re.sub(r"[^\w\-]+", "", str(tag or "").lstrip("#").lower())[:24]
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]

def clean_item(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), 300)
    if not title:
        return None
    iid = str(raw.get("id") or "")
    now = time.time()
    subtasks = []
    for row in (raw.get("subtasks") or [])[:MAX_SUBTASKS] if isinstance(raw.get("subtasks"), list) else []:
        if isinstance(row, dict) and _text(row.get("title"), 200):
            sid = str(row.get("id") or "")
            subtasks.append({"id": sid if _SUB_ID.match(sid) else _new("s", 8), "title": _text(row.get("title"), 200),
                             "done": bool(row.get("done"))})
    try:
        priority = max(0, min(3, int(raw.get("priority") or 0)))
    except (TypeError, ValueError):
        priority = 0
    reminder = raw.get("reminder") if isinstance(raw.get("reminder"), dict) else {}
    return {
        "id": iid if _ID.match(iid) and iid.startswith("t_") else _new("t"),
        "title": title,
        "done": bool(raw.get("done")),
        "done_at": _num(raw.get("done_at")) if raw.get("done") else None,
        "created": _num(raw.get("created")) or now,
        "updated": _num(raw.get("updated")) or now,
        "due": clean_due(raw.get("due")),
        "priority": priority,
        "tags": clean_tags(raw.get("tags")),
        "notes": _text(raw.get("notes"), 2000),
        "subtasks": subtasks,
        "repeat": clean_repeat(raw.get("repeat")),
        "remind": clean_due(raw.get("remind")) if "T" in clean_due(raw.get("remind")) else "",
        "reminder": {k: str(v)[:40] for k, v in reminder.items() if k in ("local", "telegram") and v},
        "completed_count": max(0, min(100000, int(raw.get("completed_count") or 0)))
        if str(raw.get("completed_count") or "0").isdigit() else 0,
    }

def clean_list(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    lid = str(raw.get("id") or "")
    items, seen = [], set()
    for row in (raw.get("items") or [])[:MAX_ITEMS] if isinstance(raw.get("items"), list) else []:
        item = clean_item(row)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    color = str(raw.get("color") or "accent")
    return {"id": lid if _ID.match(lid) and lid.startswith("l_") else _new("l"),
            "name": _text(raw.get("name"), 60) or "Задачи", "color": color if color in COLORS else "accent",
            "created": _num(raw.get("created")) or time.time(), "items": items}

def _normalize(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    lists, seen = [], set()
    for row in (raw.get("lists") or [])[:MAX_LISTS] if isinstance(raw.get("lists"), list) else []:
        item = clean_list(row)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            lists.append(item)
    notes = {}
    for key, value in (raw.get("notes") or {}).items() if isinstance(raw.get("notes"), dict) else []:
        if re.fullmatch(r"p_[a-z0-9]{6,24}", str(key)) and isinstance(value, dict):
            notes[str(key)] = {"text": str(value.get("text") or "")[:20000], "updated": _num(value.get("updated")) or 0}
    return {"version": 1, "lists": lists, "notes": notes}

def mutate(mutator: Callable[[Dict[str, Any]], Any]) -> Tuple[Dict[str, Any], Any]:
    from .storage import mutate_json
    box: Dict[str, Any] = {}

    def update(raw: Any):
        doc = _normalize(raw)
        box["result"] = mutator(doc)
        return True, doc

    doc = mutate_json(_path(), {}, update, backup=True)
    return doc, box.get("result")

def load() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    return _normalize(load_json_with_recovery(_path(), {}))

def _list(doc: Dict[str, Any], lid: str) -> Dict[str, Any]:
    for row in doc["lists"]:
        if row["id"] == lid:
            return row
    raise LocalError("Список не найден", "List not found")

def _item(lst: Dict[str, Any], iid: str) -> Dict[str, Any]:
    for row in lst["items"]:
        if row["id"] == iid:
            return row
    raise LocalError("Задача не найдена", "Task not found")

_WEEKDAYS = {"пн": 0, "понедельник": 0, "mon": 0, "monday": 0, "вт": 1, "вторник": 1, "tue": 1, "tuesday": 1,
             "ср": 2, "среда": 2, "среду": 2, "wed": 2, "wednesday": 2, "чт": 3, "четверг": 3, "thu": 3, "thursday": 3,
             "пт": 4, "пятница": 4, "пятницу": 4, "fri": 4, "friday": 4, "сб": 5, "суббота": 5, "субботу": 5, "sat": 5,
             "saturday": 5, "вс": 6, "воскресенье": 6, "sun": 6, "sunday": 6}
_REPEAT_WORDS = [
    (r"(?:каждый день|ежедневно|every day|daily)", {"every": "day", "n": 1}),
    (r"(?:по будням|weekdays|every weekday)", {"every": "weekdays", "n": 1}),
    (r"(?:каждую неделю|еженедельно|every week|weekly)", {"every": "week", "n": 1}),
    (r"(?:каждый месяц|ежемесячно|every month|monthly)", {"every": "month", "n": 1}),
    (r"(?:каждый год|ежегодно|every year|yearly|annually)", {"every": "year", "n": 1}),
]

def parse_quick(text: str, *, today: Optional[date] = None) -> Dict[str, Any]:
    """«Купить хлеб завтра в 18:00 !1 #дом каждую неделю» → поля задачи.

    Понимает: ``!1``…``!3`` (и ``!!`` = высокий), ``#тег``, сегодня/завтра/послезавтра, дни недели,
    ``25.10``/``25.10.2026``/``2026-10-25``, время ``18:00`` (с «в»/«at»), повтор («каждый день», «по будням»,
    «каждую неделю/месяц/год», «every …»). Нераспознанное остаётся в названии."""
    today = today or date.today()
    out: Dict[str, Any] = {"priority": 0, "tags": [], "due": "", "repeat": None}
    rest = " " + str(text or "").strip() + " "

    def cut(pattern: str) -> Optional["re.Match[str]"]:
        nonlocal rest
        match = re.search(pattern, rest, flags=re.I)
        if match:
            rest = rest[:match.start()] + " " + rest[match.end():]
        return match

    if cut(r"(?<=\s)!!(?=\s)"):
        out["priority"] = 1
    else:
        match = cut(r"(?<=\s)!([1-3])(?=\s)")
        if match:
            out["priority"] = int(match.group(1))
    while True:
        match = cut(r"(?<=\s)#([\w\-]{1,24})(?=\s)")
        if not match:
            break
        out["tags"].append(match.group(1).lower())
    for pattern, rule in _REPEAT_WORDS:
        if cut(r"(?<=\s)" + pattern + r"(?=\s)"):
            out["repeat"] = dict(rule)
            break
    clock = cut(r"(?<=\s)(?:в |at )?([01]?\d|2[0-3]):([0-5]\d)(?=\s)")
    day: Optional[date] = None
    if cut(r"(?<=\s)(?:сегодня|today)(?=\s)"):
        day = today
    elif cut(r"(?<=\s)(?:послезавтра|day after tomorrow)(?=\s)"):
        day = today + timedelta(days=2)
    elif cut(r"(?<=\s)(?:завтра|tomorrow)(?=\s)"):
        day = today + timedelta(days=1)
    else:
        match = cut(r"(?<=\s)(\d{4})-(\d{2})-(\d{2})(?=\s)")
        if match:
            try:
                day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                day = None
        else:
            match = cut(r"(?<=\s)(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?=\s)")
            if match:
                year = int(match.group(3)) if match.group(3) else today.year
                year += 2000 if year < 100 else 0
                try:
                    day = date(year, int(match.group(2)), int(match.group(1)))
                    if not match.group(3) and day < today:
                        day = date(year + 1, day.month, day.day)
                except ValueError:
                    day = None
            else:
                match = cut(r"(?<=\s)(?:в |во |on |next )?(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")(?=\s)")
                if match:
                    wanted = _WEEKDAYS[match.group(1).lower()]
                    delta = (wanted - today.weekday()) % 7 or 7
                    day = today + timedelta(days=delta)
    if clock and day is None:
        day = today
    if day is not None:
        out["due"] = day.isoformat() + (f"T{int(clock.group(1)):02d}:{clock.group(2)}" if clock else "")
    if out["repeat"] and not out["due"]:
        out["due"] = today.isoformat()
    out["title"] = re.sub(r"\s+", " ", rest).strip()
    return out

def next_due(due: str, repeat: Dict[str, Any], *, today: Optional[date] = None) -> str:
    """Следующий срок повторяющейся задачи — не раньше завтрашнего дня."""
    today = today or date.today()
    base_day = date.fromisoformat(due[:10]) if due else today
    tail = due[10:] if due and len(due) > 10 else ""
    every, n = repeat["every"], int(repeat.get("n") or 1)

    def step(day: date) -> date:
        if every == "day":
            return day + timedelta(days=n)
        if every == "weekdays":
            day += timedelta(days=1)
            while day.weekday() >= 5:
                day += timedelta(days=1)
            return day
        if every == "week":
            return day + timedelta(weeks=n)

        months = (n if every == "month" else 12 * n) * count
        month = base_day.month - 1 + months
        year = base_day.year + month // 12
        month = month % 12 + 1
        anchor = int(repeat.get("day") or base_day.day)
        return date(year, month, min(anchor, calendar.monthrange(year, month)[1]))

    count = 1
    day = step(base_day)
    while day <= today and count < 2000:
        count += 1
        day = step(day)
    return day.isoformat() + tail

def _schedule_reminder(item: Dict[str, Any], lang: str = "ru") -> Dict[str, str]:
    """Ставит напоминание задачи тем же путём, что «напомни мне…» в «Быстром доступе»."""
    when = datetime.fromisoformat(item["remind"])
    if when.timestamp() <= time.time():
        return {}
    ids: Dict[str, str] = {}
    from . import assistant_commands
    eng = assistant_commands.engine()
    channel = (eng.settings() or {}).get("reminder_channel") or "local"
    text = ("Задача: " if lang != "en" else "Task: ") + item["title"]
    if channel in ("telegram", "both") and not assistant_commands.telegram_status().get("ok"):

        channel = "local"
    if channel in ("local", "both"):
        timer = eng.timers.add_timer(0, item["title"][:60], kind="reminder", text=text, ends_at=when.timestamp(),
                                     meta={"todo": item["id"]})
        ids["local"] = str(timer["id"])
    if channel in ("telegram", "both"):
        ids["telegram"] = str(assistant_commands.add_reminder_task(text, when.astimezone(), lang))
    return ids

def _cancel_reminder(ids: Dict[str, str]) -> None:
    from . import assistant_commands
    if ids.get("local"):
        timers = assistant_commands.engine().timers

        if any(t.get("id") == ids["local"] for t in timers.find("", "reminder")):
            timers.cancel(ids["local"], kind="reminder")
    if ids.get("telegram"):
        assistant_commands.remove_reminder_task(ids["telegram"])

REMINDERS = {"schedule": _schedule_reminder, "cancel": _cancel_reminder}

def _sync_reminder(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]], lang: str) -> None:
    """Снимает старое и ставит новое напоминание, если изменились время, название или статус."""
    old_ids = (before or {}).get("reminder") or {}
    key = lambda it: (it.get("remind"), it.get("title"), bool(it.get("done"))) if it else None  # noqa: E731
    if before is not None and after is not None and key(before) == key(after) and old_ids:
        after["reminder"] = dict(old_ids)
        return
    if old_ids:
        try:
            REMINDERS["cancel"](old_ids)
        except Exception as exc:  # noqa: BLE001
            log.info("todo: reminder not cancelled: %s", exc)
    if after is None:
        return
    after["reminder"] = {}
    if after.get("remind") and not after.get("done"):
        try:
            after["reminder"] = REMINDERS["schedule"](after, lang) or {}
        except Exception as exc:  # noqa: BLE001
            log.info("todo: reminder not scheduled: %s", exc)

def snapshot() -> Dict[str, Any]:
    doc = load()
    return {"lists": doc["lists"]}

def ensure_default_list(name: str = "") -> Dict[str, Any]:
    """Первый список создаётся сам, чтобы у новой панели было куда добавлять."""

    def update(doc):
        if doc["lists"]:
            return copy.deepcopy(doc["lists"][0])
        lst = clean_list({"name": name or "Задачи"})
        doc["lists"].append(lst)
        return copy.deepcopy(lst)

    return mutate(update)[1]

def create_list(name: str, color: str = "accent") -> Dict[str, Any]:
    def update(doc):
        if len(doc["lists"]) >= MAX_LISTS:
            raise LocalError(f"Не больше {MAX_LISTS} списков", f"At most {MAX_LISTS} lists")
        lst = clean_list({"name": name or "Список", "color": color})
        doc["lists"].append(lst)
        return copy.deepcopy(lst)

    return mutate(update)[1]

def update_list(lid: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    def update(doc):
        lst = _list(doc, lid)
        if _text(changes.get("name"), 60):
            lst["name"] = _text(changes.get("name"), 60)
        if changes.get("color") in COLORS:
            lst["color"] = changes["color"]
        return copy.deepcopy(lst)

    return mutate(update)[1]

def delete_list(lid: str) -> None:
    removed: List[Dict[str, Any]] = []

    def update(doc):
        lst = _list(doc, lid)
        removed.extend(lst["items"])
        doc["lists"] = [row for row in doc["lists"] if row["id"] != lid]

    mutate(update)
    for item in removed:
        _sync_reminder(item, None, "ru")

def add_item(lid: str, fields: Dict[str, Any], *, quick: bool = False, lang: str = "ru",
             index: Optional[int] = None) -> Dict[str, Any]:
    raw = dict(fields or {})
    if quick:
        parsed = parse_quick(str(raw.get("title") or ""))
        for key in ("due", "repeat"):
            if parsed.get(key) and not raw.get(key):
                raw[key] = parsed[key]
        if parsed["priority"] and not raw.get("priority"):
            raw["priority"] = parsed["priority"]
        raw["tags"] = clean_tags(list(raw.get("tags") or []) + parsed["tags"])
        raw["title"] = parsed["title"] or raw.get("title")
    raw.pop("id", None)
    raw["created"] = raw["updated"] = time.time()
    item = clean_item(raw)
    if not item:
        raise LocalError("Нужно название задачи" if lang != "en" else "A task needs a title")

    def update(doc):
        lst = _list(doc, lid)
        if len(lst["items"]) >= MAX_ITEMS:
            raise LocalError(f"Не больше {MAX_ITEMS} задач в списке" if lang != "en" else f"At most {MAX_ITEMS} tasks per list")
        pos = len(lst["items"]) if index is None else max(0, min(int(index), len(lst["items"])))
        lst["items"].insert(pos, item)
        return copy.deepcopy(item)

    created = mutate(update)[1]
    if created.get("remind"):
        _sync_reminder(None, created, lang)
        _store_reminder(lid, created)
    return created

def _store_reminder(lid: str, item: Dict[str, Any]) -> None:
    def update(doc):
        try:
            _item(_list(doc, lid), item["id"])["reminder"] = dict(item.get("reminder") or {})
        except LocalError:
            pass

    mutate(update)

def update_item(lid: str, iid: str, changes: Dict[str, Any], *, lang: str = "ru") -> Dict[str, Any]:
    """Правка задачи. ``done: true`` у повторяющейся — перенос на следующий срок."""
    box: Dict[str, Any] = {}

    def update(doc):
        lst = _list(doc, lid)
        item = _item(lst, iid)
        before = copy.deepcopy(item)
        merged = {**item}
        for key in ("title", "due", "priority", "tags", "notes", "subtasks", "repeat", "remind", "done"):
            if key in changes:
                merged[key] = changes[key]
        old_rule = item.get("repeat") or {}
        if isinstance(merged.get("repeat"), dict):
            if (str(merged.get("due") or "")[:10] != str(item.get("due") or "")[:10]
                    or merged["repeat"].get("every") != old_rule.get("every")):
                merged["repeat"] = {k: v for k, v in merged["repeat"].items() if k != "day"}
            elif old_rule.get("day") and not merged["repeat"].get("day"):
                merged["repeat"] = {**merged["repeat"], "day": old_rule["day"]}
            if merged["repeat"].get("every") == old_rule.get("every") and "n" not in merged["repeat"]:
                merged["repeat"] = {**merged["repeat"], "n": old_rule.get("n", 1)}
        if changes.get("toggle_subtask"):
            for sub in merged["subtasks"]:
                if sub["id"] == changes["toggle_subtask"]:
                    sub["done"] = not sub["done"]
        clean = clean_item(merged)
        if not clean:
            raise LocalError("Нужно название задачи" if lang != "en" else "A task needs a title")
        clean["id"], clean["created"] = item["id"], item["created"]
        clean["updated"] = time.time()
        advanced = ""
        if clean["done"] and not item["done"]:
            if clean["repeat"]:
                old_due = clean["due"]
                if clean["repeat"]["every"] in ("month", "year") and old_due and "day" not in clean["repeat"]:
                    clean["repeat"]["day"] = int(old_due[8:10])
                clean["due"] = next_due(old_due, clean["repeat"])
                if clean["remind"] and old_due:
                    shift = date.fromisoformat(clean["due"][:10]) - date.fromisoformat(old_due[:10])
                    clean["remind"] = (datetime.fromisoformat(clean["remind"]) + shift).strftime("%Y-%m-%dT%H:%M")
                clean["done"], clean["done_at"] = False, None
                clean["completed_count"] = item.get("completed_count", 0) + 1
                for sub in clean["subtasks"]:
                    sub["done"] = False
                advanced = clean["due"]
            else:
                clean["done_at"] = time.time()
                clean["completed_count"] = item.get("completed_count", 0) + 1
        elif not clean["done"]:
            clean["done_at"] = None
        lst["items"][lst["items"].index(item)] = clean
        box["before"] = before
        box["advanced"] = advanced
        return copy.deepcopy(clean)

    item = mutate(update)[1]
    before = box.get("before")
    old = dict(item)
    _sync_reminder(before, item, lang)
    if item.get("reminder") != old.get("reminder"):
        _store_reminder(lid, item)
    item["advanced"] = box.get("advanced") or ""
    return item

def delete_item(lid: str, iid: str) -> Dict[str, Any]:
    def update(doc):
        lst = _list(doc, lid)
        item = _item(lst, iid)
        lst["items"].remove(item)
        return copy.deepcopy(item)

    item = mutate(update)[1]
    _sync_reminder(item, None, "ru")
    return item

def restore_item(lid: str, item: Dict[str, Any], index: Optional[int] = None) -> Dict[str, Any]:
    """«Вернуть» после удаления: та же задача на прежнем месте."""
    clean = clean_item(item)
    if not clean:
        raise LocalError("Нечего вернуть", "Nothing to restore")

    def update(doc):
        lst = _list(doc, lid)
        if any(row["id"] == clean["id"] for row in lst["items"]):
            return copy.deepcopy(clean)
        pos = len(lst["items"]) if index is None else max(0, min(int(index), len(lst["items"])))
        lst["items"].insert(pos, clean)
        return copy.deepcopy(clean)

    restored = mutate(update)[1]
    restored["reminder"] = {}
    _sync_reminder(None, restored, "ru")
    _store_reminder(lid, restored)
    return restored

def move_item(lid: str, iid: str, *, to_list: str = "", index: int = -1) -> Dict[str, Any]:
    """Порядок внутри списка или перенос в другой список (``index`` −1 — в конец)."""

    def update(doc):
        src = _list(doc, lid)
        dst = _list(doc, to_list or lid)
        item = _item(src, iid)
        src["items"].remove(item)
        pos = len(dst["items"]) if index < 0 else max(0, min(index, len(dst["items"])))
        dst["items"].insert(pos, item)
        return copy.deepcopy(item)

    return mutate(update)[1]

def clear_done(lid: str) -> int:
    def update(doc):
        lst = _list(doc, lid)
        before = len(lst["items"])
        lst["items"] = [row for row in lst["items"] if not row["done"]]
        return before - len(lst["items"])

    return mutate(update)[1]

def note_text(pid: str) -> Dict[str, Any]:
    return load()["notes"].get(pid) or {"text": "", "updated": 0}

def save_note(pid: str, text: str) -> Dict[str, Any]:
    if not re.fullmatch(r"p_[a-z0-9]{6,24}", str(pid)):
        raise LocalError("Неизвестная панель", "Unknown panel")

    def update(doc):
        doc["notes"][pid] = {"text": str(text or "")[:20000], "updated": time.time()}
        return dict(doc["notes"][pid])

    return mutate(update)[1]

def drop_note(pid: str) -> None:
    def update(doc):
        doc["notes"].pop(pid, None)

    mutate(update)
