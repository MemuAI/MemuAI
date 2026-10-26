"""Scheduled prompts: a task is a prompt that MemuCode runs by itself, in a folder, at a time — once, every so
often, every day, on some weekdays, or by a cron line.

Where it runs
    * ``session`` — while a MemuCode window is open, the interface looks at the clock and starts a task that
      is due. Nothing is installed in the system; closing the window stops it.
    * ``system`` — registered with the Windows Task Scheduler under ``\\MemuCode\\<id>``; it runs whether a
      MemuCode window is open or not (a missed run starts when the computer is next awake).

Both start the same thing, ``memucode schedule run <id>``: a headless run of the prompt (see ``headless.py``)
in the task's folder, with the permission mode the task was given — nobody is there to answer a question, so
whatever the mode does not allow is refused. The conversation is saved like any other (``/resume``), the
answer is written to ``schedule/results/<id>/<time>.md`` and a notification is shown.

Everything is stored in MemuCode's own folder; nothing outside it changes unless the task is registered with the
Task Scheduler (and that registration is removed with the task).
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import paths
from .i18n import tr

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_CRON_FIELD = r"(?:[\d*/,\-]|mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)+"
DAY_WORDS = {"mon": 0, "monday": 0, "пн": 0, "пон": 0, "tue": 1, "tues": 1, "tuesday": 1, "вт": 1, "wed": 2, "wednesday": 2, "ср": 2,
             "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "чт": 3, "fri": 4, "friday": 4, "пт": 4, "sat": 5, "saturday": 5, "сб": 5,
             "sun": 6, "sunday": 6, "вс": 6}
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "с": 1, "сек": 1, "секунд": 1, "секунду": 1, "секунды": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "м": 60, "мин": 60, "минут": 60, "минуту": 60, "минуты": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
         "d": 86400, "day": 86400, "days": 86400, "д": 86400, "дн": 86400, "день": 86400, "дня": 86400, "дней": 86400,
         "w": 604800, "week": 604800, "weeks": 604800, "нед": 604800, "неделю": 604800, "недели": 604800, "недель": 604800}
MIN_INTERVAL = 60
DEFAULT_TIMEOUT_MINUTES = 30
MODES = ("plan", "acceptEdits", "auto", "bypassPermissions")

class ScheduleError(ValueError):
    pass

_DAY_STEMS = [(0, r"понедельник|monday"), (1, r"вторник|tuesday"), (2, r"сред[аеуы]|wednesday"), (3, r"четверг|thursday"),
              (4, r"пятниц|friday"), (5, r"суббот|saturday"), (6, r"воскресень|sunday")]
_FILLER = {"at", "on", "в", "во", "the", "по"}

def _day_of(word: str) -> Optional[int]:
    """«пн», «mon», «понедельник», «понедельникам», «среду», «fridays» → 0…6 (None when it is not a day)."""
    word = word.strip(".").lower()
    if word in DAY_WORDS:
        return DAY_WORDS[word]
    for number, stem in _DAY_STEMS:
        if re.match(rf"^(?:{stem})", word):
            return number
    return None

def _days_in(spec: str) -> List[int]:
    """«mon,fri», «пн-пт», «по понедельникам и средам» → sorted weekdays; ScheduleError when a word is not a day."""
    chosen: List[int] = []
    for piece in re.split(r"[,\s]+|\bи\b|\band\b", spec):
        piece = piece.strip()
        if not piece or piece in _FILLER:
            continue
        span = piece.split("-")
        if len(span) == 2 and _day_of(span[0]) is not None and _day_of(span[1]) is not None:
            start, end = _day_of(span[0]), _day_of(span[1])
            chosen += [day % 7 for day in range(start, end + (1 if end >= start else 8))]
            continue
        day = _day_of(piece)
        if day is None:
            raise ScheduleError(tr(f"Не понимаю «{spec}». Примеры: {EXAMPLES_RU}", f"I do not understand “{spec}”. Examples: {EXAMPLES_EN}"))
        chosen.append(day)
    return sorted(set(chosen))

EXAMPLES_RU = "через 20 мин · в 15:30 · завтра в 9:00 · каждые 2 ч · каждый день в 9:00 · по будням 9:00 · пн,пт 18:30 · cron 0 9 * * 1-5"
EXAMPLES_EN = "in 20m · at 15:30 · tomorrow 9am · every 2h · daily 09:00 · weekdays 09:00 · mon,fri 18:30 · cron 0 9 * * 1-5"

def _clock(text: str) -> Tuple[int, int]:
    """«15:30», «9.05», «9h30», «6pm», «6:30pm», «9утра», «9вечера», «noon», «полночь» → (hour, minute).

    Round 34: 12-hour and Russian «утра/вечера» forms and noon/midnight were refused («use HH:MM»), although
    /auto-continue in the same window accepted them. A bare «9» stays refused here (`daily 9` is ambiguous);
    «в 9» / «at 9» are turned into 9:00 before (see :func:`_normalize`)."""
    value = text.strip().lower()
    named = {"noon": (12, 0), "midday": (12, 0), "полдень": (12, 0), "midnight": (0, 0), "полночь": (0, 0)}
    if value in named:
        return named[value]
    match = re.fullmatch(r"(\d{1,2})(?:(?::|\.|h)(\d{2}))?(am|pm|a\.m\.|p\.m\.|утра|вечера|дня|ночи)", value)
    if match:
        hour, minute, part = int(match.group(1)), int(match.group(2) or 0), match.group(3)
        if hour > 12 or minute > 59 or hour == 0 and part in ("am", "a.m.", "pm", "p.m."):
            raise ScheduleError(tr(f"Нет такого времени: {text}", f"There is no such time: {text}"))
        if part in ("pm", "p.m.", "вечера", "дня") and hour < 12:
            hour += 12
        elif part in ("am", "a.m.", "ночи") and hour == 12:
            hour = 0
        return hour, minute
    match = re.fullmatch(r"(\d{1,2})(?::|\.|h)(\d{2})", value)
    if not match:
        raise ScheduleError(tr(f"Не понимаю время «{text}» (нужно ЧЧ:ММ)", f"I do not understand the time “{text}” (use HH:MM)"))
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ScheduleError(tr(f"Нет такого времени: {text}", f"There is no such time: {text}"))
    return hour, minute

def _normalize(raw: str) -> str:
    """The same schedule written the ways people write it → the forms :func:`parse_when` knows."""
    text = re.sub(r"(\d)\s+(am|pm|a\.m\.|p\.m\.|утра|вечера|дня|ночи)\b", r"\1\2", raw)
    text = re.sub(r"\b(at|в|во)\s+(\d{1,2})(?=\s|$)", r"\1 \2:00", text)
    for pattern, value in ((r"(?:по|в)\s+будн(?:ям|ие|и)(?:\s+дни)?|будние\s+дни|по\s+рабочим(?:\s+дням)?", "weekdays"),
                           (r"(?:по|в)\s+выходн(?:ым|ые)(?:\s+дни)?|on\s+weekends", "weekends"),
                           (r"\b(?:mon-fri|пн-пт)\b", "weekdays"),
                           (r"\b(?:half\s+an\s+hour|полчаса)\b", "30m")):
        text = re.sub(pattern, value, text)
    text = re.sub(r"^(in|через)\s+(?:an?\s+)?(hour|minute|day|week|час|минуту|день|неделю)$", r"\1 1\2", text)
    return text

def _once_at(day: datetime, clock: str, now: float, *, roll: bool = False) -> Dict[str, Any]:
    hour, minute = _clock(clock) if clock else (9, 0)
    moment = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment.timestamp() <= now:
        if not roll:
            raise ScheduleError(tr(f"Это время уже прошло: {moment:%d.%m.%Y %H:%M}", f"That time has passed: {moment:%d.%m.%Y %H:%M}"))
        moment += timedelta(days=7)
    return {"kind": "once", "at": moment.timestamp()}

def _seconds(text: str) -> int:
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-zа-я]+)", text.strip().lower())
    if not match or match.group(2) not in UNITS:
        raise ScheduleError(tr(f"Не понимаю интервал «{text}» (например 30m, 2h, 1d)", f"I do not understand the interval “{text}” (e.g. 30m, 2h, 1d)"))
    return int(float(match.group(1).replace(",", ".")) * UNITS[match.group(2)])

def _parse_once(raw: str, now: float) -> Optional[Dict[str, Any]]:
    """One moment written as a day and a time: «завтра в 9», «tomorrow 6pm», «сегодня 22:00», «12.10.2026 9:00»,
    «12.10 18:30», «2026-10-12 09:00», «в понедельник в 10» (the next Monday). None when ``raw`` is not that."""
    base = datetime.fromtimestamp(now)
    match = re.fullmatch(r"(?:(?:at|в|on)\s+)?(today|tonight|tomorrow|сегодня|завтра|послезавтра)(?:\s+(?:at|в|во))?\s*(.*)", raw)
    if match:
        shift = {"today": 0, "tonight": 0, "сегодня": 0, "tomorrow": 1, "завтра": 1, "послезавтра": 2}[match.group(1)]
        return _once_at(base + timedelta(days=shift), match.group(2).strip(), now)
    match = re.fullmatch(r"(?:(?:at|в|on)\s+)?(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t]+(?:(?:at|в)\s+)?(.+))?", raw)
    if match:
        year, month, day, clock, explicit = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4), True
    else:

        match = re.fullmatch(r"(?:(?:at|в|on)\s+)?(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?(?:\s+(?:(?:at|в)\s+)?(.+))?", raw)
        if match and not (match.group(3) or match.group(4)):
            match = None
        if match:
            day, month, clock, explicit = int(match.group(1)), int(match.group(2)), match.group(4), bool(match.group(3))
            year = int(match.group(3)) if match.group(3) else base.year
            year += 2000 if year < 100 else 0
    if match:
        try:
            date = datetime(year, month, day)
        except ValueError:
            raise ScheduleError(tr(f"Нет такой даты: {raw}", f"There is no such date: {raw}")) from None
        if not explicit and date.date() < base.date():
            date = date.replace(year=date.year + 1)
        return _once_at(date, (clock or "").strip(), now)
    match = re.fullmatch(r"(?:at|в|во|on)\s+(\S+)(?:\s+(?:at|в|во))?\s*(.*)", raw)
    if match and _day_of(match.group(1)) is not None and not re.match(r"\d", match.group(1)):
        target = _day_of(match.group(1))
        ahead = (target - base.weekday()) % 7
        return _once_at(base + timedelta(days=ahead), match.group(2).strip(), now, roll=True)
    return None

def parse_when(text: str, now: Optional[float] = None) -> Dict[str, Any]:
    """``"in 20m"``, ``"at 15:30"``, ``"every 2h"``, ``"daily 09:00"``, ``"weekdays 09:00"``, ``"mon,fri 18:30"``,
    ``"cron 0 9 * * 1-5"`` → a ``when`` dict. Raises :class:`ScheduleError` with a helpful message.

    Round 34: also «завтра в 9», «tomorrow 6pm», «12.10.2026 9:00», «по будням в 9:30», «пн-пт 9:00»,
    «каждый понедельник в 10», «через полчаса», «в полночь» (see :func:`_normalize`, :func:`_parse_once`)."""
    now = time.time() if now is None else now
    raw = " ".join(str(text or "").lower().replace(",", ", ").split()).replace(", ", ",")
    if not raw:
        raise ScheduleError(tr("Не сказано, когда.", "No time was given."))
    raw = _normalize(raw)
    if re.fullmatch(r"\d{1,2}(?:[:.]\d{2})?(?:am|pm|a\.m\.|p\.m\.|утра|вечера|дня|ночи)|\d{1,2}:\d{2}|noon|midnight|полдень|полночь", raw):
        raw = "at " + raw
    once = _parse_once(raw, now)
    if once is not None:
        return once
    words = raw.split()
    head = words[0]
    if head == "cron" or len(words) == 5 and all(re.fullmatch(_CRON_FIELD, word) for word in words):
        expression = " ".join(words[1:] if head == "cron" else words)
        parse_cron(expression)
        return {"kind": "cron", "expr": expression}
    if head in ("in", "через"):
        seconds = _seconds("".join(words[1:]))
        return {"kind": "once", "at": now + max(1, seconds)}
    if head in ("at", "в", "on"):
        rest = " ".join(words[1:])
        date = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}[:.]\d{2})", rest)
        if date:
            hour, minute = _clock(date.group(4))
            when = datetime(int(date.group(1)), int(date.group(2)), int(date.group(3)), hour, minute)
            return {"kind": "once", "at": when.timestamp()}
        hour, minute = _clock(rest)
        today = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if today.timestamp() <= now:
            today += timedelta(days=1)
        return {"kind": "once", "at": today.timestamp()}
    if head in ("every", "кажд", "каждые", "каждый", "каждую"):
        rest = words[1:]
        if rest and rest[0].startswith(("weekday", "будн")):
            return parse_when("weekdays " + " ".join(rest[1:]).replace("at ", ""), now)
        if rest and re.match(r"^(day|день|дня)$", rest[0]):
            return parse_when("daily " + " ".join(rest[1:]).replace("at ", ""), now)
        if len(rest) == 1 and rest[0] in UNITS:
            rest = ["1" + rest[0]]
        if rest and _day_of(rest[0]) is not None:
            return parse_when(" ".join(rest), now)
        joined = "".join(rest)
        if rest and re.fullmatch(r"\d+(?:[.,]\d+)?", rest[0]) and len(rest) > 1:
            joined = rest[0] + rest[1]
        seconds = _seconds(joined)
        if seconds < MIN_INTERVAL:
            raise ScheduleError(tr("Чаще раза в минуту нельзя.", "Not more often than once a minute."))
        return {"kind": "every", "seconds": seconds, "anchor": now}
    days = list(range(7))
    clock = words[-1]
    spec = " ".join(words[:-1]).replace("at ", "").strip()
    if head in ("daily", "ежедневно", "everyday"):
        clock = words[-1] if len(words) > 1 else ""
    elif head in ("weekdays", "workdays", "будни", "по-будням"):
        days = [0, 1, 2, 3, 4]
    elif head in ("weekends", "выходные"):
        days = [5, 6]
    else:
        days = _days_in(spec or head)
        if not days:
            raise ScheduleError(tr(f"Не понимаю «{text}».", f"I do not understand “{text}”."))
    hour, minute = _clock(clock) if clock else (9, 0)
    return {"kind": "daily", "time": f"{hour:02d}:{minute:02d}", "days": days}

def describe_when(when: Dict[str, Any]) -> str:
    kind = when.get("kind")
    if kind == "once":
        return tr("один раз, ", "once, ") + time.strftime("%d.%m.%Y %H:%M", time.localtime(float(when["at"])))
    if kind == "every":
        seconds = int(when["seconds"])
        for size, ru, en in ((86400, "д", "d"), (3600, "ч", "h"), (60, "мин", "min")):
            if seconds % size == 0:
                return tr(f"каждые {seconds // size} {ru}", f"every {seconds // size}{en}")
        return tr(f"каждые {seconds} с", f"every {seconds}s")
    if kind == "daily":
        days = when.get("days") or list(range(7))
        if len(days) == 7:
            label = tr("каждый день", "every day")
        elif days == [0, 1, 2, 3, 4]:
            label = tr("по будням", "on weekdays")
        elif days == [5, 6]:
            label = tr("по выходным", "on weekends")
        else:
            label = ", ".join(DAY_NAMES[day] for day in days)
        return f"{label} {when['time']}"
    if kind == "cron":
        return "cron " + str(when["expr"])
    return str(kind)

_CRON_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
_MONTHS = {name: number for number, name in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_CRON_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

def parse_cron(expression: str) -> List[set]:
    """The five fields of a cron line as sets of allowed values (day of week: 0 = Sunday, 7 also means Sunday)."""
    fields = expression.split()
    if len(fields) != 5:
        raise ScheduleError(tr("В cron-строке должно быть 5 полей: минута час день месяц день-недели",
                               "A cron line has 5 fields: minute hour day month weekday"))
    sets: List[set] = []
    for index, (field_text, (low, high)) in enumerate(zip(fields, _CRON_RANGES)):
        values: set = set()
        for part in field_text.lower().split(","):
            step = 1
            if "/" in part:
                part, _, step_text = part.partition("/")
                if not step_text.isdigit() or int(step_text) < 1:
                    raise ScheduleError(tr(f"Плохой шаг в «{field_text}»", f"Bad step in “{field_text}”"))
                step = int(step_text)
            if part in ("*", ""):
                start, end = low, high
            elif "-" in part:
                a, _, b = part.partition("-")
                start, end = _cron_value(a, index), _cron_value(b, index)
            else:
                start = _cron_value(part, index)
                end = high if step > 1 else start
            if index == 4 and end == 7:
                end = 6 if start != 7 else 7
            if start < low or end > (7 if index == 4 else high) or start > end:
                raise ScheduleError(tr(f"Значение вне диапазона в «{field_text}»", f"Out of range in “{field_text}”"))
            values.update(range(start, end + 1, step))
        if index == 4 and 7 in values:
            values.discard(7)
            values.add(0)
        sets.append(values)
    return sets

def _cron_value(text: str, index: int) -> int:
    if index == 3 and text in _MONTHS:
        return _MONTHS[text]
    if index == 4 and text in _CRON_DAYS:
        return _CRON_DAYS[text]
    if not text.isdigit():
        raise ScheduleError(tr(f"Не понимаю «{text}» в cron", f"I do not understand “{text}” in cron"))
    return int(text)

def cron_next(expression: str, after: float) -> Optional[float]:
    minutes, hours, days, months, weekdays = parse_cron(expression)
    moment = datetime.fromtimestamp(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day_star = len(days) == 31
    week_star = len(weekdays) == 7
    for _ in range(366 * 4):
        if moment.month in months:
            cron_weekday = (moment.weekday() + 1) % 7
            by_day = moment.day in days
            by_week = cron_weekday in weekdays
            matches = (by_day or by_week) if not day_star and not week_star else (by_day and by_week)
            if matches:
                for hour in sorted(hours):
                    if hour < moment.hour:
                        continue
                    for minute in sorted(minutes):
                        if hour == moment.hour and minute < moment.minute:
                            continue
                        at = _later(moment.replace(hour=hour, minute=minute), after)
                        if at is not None:
                            return at
        moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
    return None

def _later(moment: datetime, after: float) -> Optional[float]:
    """The wall-clock ``moment`` as a timestamp later than ``after``, or None.

    Round 34: in the hour repeated when clocks go back the naive time maps to its first occurrence — «*/15 * * * *»
    answered 45 minutes in the past, so the task was due again at every tick for an hour. A wall time is taken at its
    first occurrence only (a daily 02:30 runs once on that night, as cron does) and a passed one is skipped."""
    at = moment.replace(fold=0).timestamp()
    return at if at > after else None

def next_run(when: Dict[str, Any], after: float) -> Optional[float]:
    """The first moment after ``after`` at which the schedule fires; None when it never will again."""
    kind = when.get("kind")
    if kind == "once":
        at = float(when.get("at") or 0)
        return at if at > after else None
    if kind == "every":
        seconds = max(MIN_INTERVAL, int(when.get("seconds") or MIN_INTERVAL))
        anchor = float(when.get("anchor") or after)
        if after < anchor:
            return anchor
        return anchor + (math.floor((after - anchor) / seconds) + 1) * seconds
    if kind == "daily":
        hour, minute = _clock(str(when.get("time") or "09:00"))
        days = set(when.get("days") or range(7))
        moment = datetime.fromtimestamp(after).replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(8):
            at = _later(moment, after) if moment.weekday() in days else None
            if at is not None:
                return at
            moment += timedelta(days=1)
        return None
    if kind == "cron":
        return cron_next(str(when.get("expr") or ""), after)
    return None

@dataclass
class Task:
    id: str
    title: str
    prompt: str
    folder: str
    when: Dict[str, Any]
    mode: str = "acceptEdits"
    model: str = ""
    runner: str = "session"
    enabled: bool = True
    notify: bool = True
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    created: float = field(default_factory=time.time)
    next_run: float = 0.0
    last_run: float = 0.0
    last_status: str = ""
    last_summary: str = ""
    last_session: str = ""
    last_result: str = ""
    running_since: float = 0.0
    runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Task":
        known = {name for name in Task.__dataclass_fields__}
        return Task(**{key: value for key, value in data.items() if key in known})

def home() -> Path:
    path = paths.home() / "schedule"
    path.mkdir(parents=True, exist_ok=True)
    return path

def store_path() -> Path:
    return home() / "tasks.json"

def results_dir(task_id: str) -> Path:
    path = home() / "results" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path

def _mutate(change: Callable[[Dict[str, Any]], bool]) -> None:
    from ..storage import mutate_json

    def apply(data: Any):
        data = data if isinstance(data, dict) else {}
        data.setdefault("tasks", {})
        return change(data["tasks"]), data

    mutate_json(store_path(), {"tasks": {}}, apply, backup=False, validator=lambda x: isinstance(x, dict))

def load_tasks() -> List[Task]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("tasks") if isinstance(data, dict) else None
    tasks = []
    for row in (rows or {}).values():
        if isinstance(row, dict) and row.get("id") and row.get("prompt"):
            try:
                tasks.append(Task.from_dict(row))
            except TypeError:
                continue
    return sorted(tasks, key=lambda task: (not task.enabled, task.next_run or 1e18, task.created))

def get_task(task_id: str) -> Optional[Task]:
    wanted = str(task_id or "").strip().lower()
    rows = load_tasks()
    exact = [task for task in rows if task.id == wanted]
    if exact:
        return exact[0]
    prefix = [task for task in rows if wanted and task.id.startswith(wanted)]
    return prefix[0] if len(prefix) == 1 else None

def save_task(task: Task) -> None:
    def change(tasks: Dict[str, Any]) -> bool:
        tasks[task.id] = task.to_dict()
        return True
    _mutate(change)

def update_task(task_id: str, **values: Any) -> Optional[Task]:
    holder: Dict[str, Any] = {}

    def change(tasks: Dict[str, Any]) -> bool:
        row = tasks.get(task_id)
        if not isinstance(row, dict):
            return False
        row.update(values)
        holder["row"] = dict(row)
        return True
    _mutate(change)
    return Task.from_dict(holder["row"]) if holder.get("row") else None

def delete_task(task_id: str) -> bool:
    removed: Dict[str, bool] = {}

    def change(tasks: Dict[str, Any]) -> bool:
        removed["done"] = tasks.pop(task_id, None) is not None
        return removed["done"]
    _mutate(change)
    return bool(removed.get("done"))

def new_task(title: str, prompt: str, folder: str, when: Dict[str, Any], *, mode: str = "acceptEdits", model: str = "",
             runner: str = "session", notify: bool = True, timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES) -> Task:
    if mode not in MODES:
        raise ScheduleError(tr(f"Режим «{mode}» не подходит для задач без присмотра", f"The mode “{mode}” does not suit unattended tasks"))
    if not str(prompt or "").strip():
        raise ScheduleError(tr("Пустой запрос.", "The prompt is empty."))
    task = Task(id=uuid.uuid4().hex[:8], title=(title or prompt).strip().splitlines()[0][:60], prompt=prompt.strip(),
                folder=str(Path(folder)), when=when, mode=mode, model=model, runner=runner, notify=notify,
                timeout_minutes=timeout_minutes)
    task.next_run = next_run(when, time.time()) or 0.0
    if not task.next_run:
        raise ScheduleError(tr("Это время уже прошло.", "That time is already past."))
    return task

def due(tasks: Sequence[Task], now: float) -> List[Task]:
    """Tasks of the ``session`` runner whose time has come and that are not running already."""
    out = []
    for task in tasks:
        if not task.enabled or task.runner != "session" or not task.next_run or task.next_run > now:
            continue
        if task.running_since and now - task.running_since < task.timeout_minutes * 60 + 120:
            continue
        out.append(task)
    return out

def advance(task: Task, now: float) -> Task:
    """Move a task on to its next time after a run (or a skipped one); a one-time task is switched off."""
    following = next_run(task.when, now)
    values: Dict[str, Any] = {"next_run": following or 0.0}
    if following is None:
        values["enabled"] = False
    return update_task(task.id, **values) or task

def command_line(*args: str) -> List[str]:
    """The program and arguments that start ``memucode`` with ``args`` (also for the Task Scheduler)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    script = Path(__file__).resolve().parents[2] / "memucode.py"
    return [sys.executable, str(script), *args]

def system_command(task_id: str) -> List[str]:
    """What the Windows Task Scheduler starts. MemuCode is a console program, and a console program started by the
    scheduler in a signed-in session opens a window; ``conhost --headless`` runs it without one."""
    command = command_line("schedule", "run", task_id)
    conhost = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "conhost.exe"
    if os.name == "nt" and conhost.is_file():
        return [str(conhost), "--headless", *command]
    return command

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)

def _result_from(output: str) -> Dict[str, Any]:
    match = _JSON_OBJECT.search(output)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {"result": output.strip(), "is_error": False}

PREFACE = ("This is a SCHEDULED, UNATTENDED run: nobody is watching and nobody can answer a question. Do the task below "
           "with what you can do on your own; if something is refused or needs a decision, say so instead of waiting. Finish "
           "with a short report: what you did, what you found, and what needs the person.")

async def execute(task_id: str, *, runtime_factory: Optional[Callable[..., Any]] = None, notifier: Optional[Callable[[str, str], None]] = None) -> int:
    """Run one task now (this is ``memucode schedule run <id>``). Returns a process exit code."""
    task = get_task(task_id)
    if task is None:
        print(f"memucode schedule: no task {task_id}", file=sys.stderr)
        return 2
    folder = Path(task.folder)
    started = time.time()
    if not folder.is_dir():
        _finish(task, started, {"result": f"The folder {folder} does not exist.", "is_error": True}, "error", "", notifier)
        return 1
    if not claim_run(task.id, started):
        print(f"memucode schedule: task {task.id} is already running", file=sys.stderr)
        return 3
    from . import headless
    from .runtime import Runtime
    from .session import Session
    from .settings import Settings
    os.chdir(folder)
    settings = Settings(folder, {"permission_mode": task.mode, "notifications": "off"})
    session = Session(folder)
    session.meta["title"] = "◷ " + task.title
    session.meta["scheduled"] = task.id
    runtime = (runtime_factory or Runtime)(folder, settings, session=session, model=task.model)
    buffer = io.StringIO()
    status = "ok"
    result: Dict[str, Any] = {}
    try:
        with contextlib.redirect_stdout(buffer):
            job = asyncio.ensure_future(headless.run(runtime, task.prompt, fmt="json", system_append=PREFACE))
            finished, _pending = await asyncio.wait({job}, timeout=max(1.0, task.timeout_minutes * 60))
            if not finished:

                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                raise asyncio.TimeoutError
            code = job.result()
        result = _result_from(buffer.getvalue())
        if code != 0 or result.get("is_error"):
            status = "error"
    except asyncio.TimeoutError:
        status = "timeout"
        result = {"result": f"The task ran for more than {task.timeout_minutes:g} minutes and was stopped.", "is_error": True}
        try:
            await runtime.shutdown()
        except Exception:
            pass
    except Exception as exc:
        status = "error"
        result = {"result": f"{type(exc).__name__}: {exc}", "is_error": True}
    _finish(task, started, result, status, session.id, notifier)
    return 0 if status == "ok" else 1

def _finish(task: Task, started: float, result: Dict[str, Any], status: str, session_id: str,
            notifier: Optional[Callable[[str, str], None]]) -> None:
    finished = time.time()
    text = str(result.get("result") or "").strip() or "(no answer)"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
    target = results_dir(task.id) / f"{stamp}.md"
    cost = result.get("total_cost_usd")
    header = [f"# {task.title}", "",
              f"- {tr('Статус', 'Status')}: {status}", f"- {tr('Запущено', 'Started')}: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))} "
              f"({int(finished - started)} s)", f"- {tr('Папка', 'Folder')}: {task.folder}", f"- {tr('Режим', 'Mode')}: {task.mode}"]
    if result.get("model"):
        header.append(f"- {tr('Модель', 'Model')}: {result['model']}")
    if cost:
        header.append(f"- {tr('Стоимость по ценам API', 'Cost at API prices')}: ${cost}")
    if session_id:
        header.append(f"- {tr('Разговор', 'Conversation')}: /resume {session_id}")
    try:
        target.write_text("\n".join(header) + "\n\n" + text + "\n", encoding="utf-8")
    except OSError:
        pass
    update_task(task.id, running_since=0.0, last_run=started, last_status=status, last_summary=" ".join(text.split())[:300],
                last_session=session_id, last_result=str(target), runs=task.runs + 1)
    fresh = get_task(task.id) or task
    advance(fresh, finished)
    if task.notify:
        try:
            (notifier or _toast)(f"{task.title}", ("✓ " if status == "ok" else "✗ ") + " ".join(text.split())[:200])
        except Exception:
            pass

def _toast(title: str, text: str) -> None:
    from ..desktop_notifications import show_completion
    show_completion("MemuCode · " + title, text)

def last_result_text(task: Task, limit: int = 20000) -> str:
    path = Path(task.last_result) if task.last_result else None
    if path and path.is_file():
        try:
            return path.read_text(encoding="utf-8")[:limit]
        except OSError:
            return ""
    return ""

TASK_FOLDER = "MemuCode"
Runner = Callable[..., Any]

def system_available() -> bool:
    return os.name == "nt"

def task_name(task_id: str) -> str:
    return f"\\{TASK_FOLDER}\\{task_id}"

def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

def build_xml(task: Task, command: Sequence[str], *, user: Optional[str] = None, now: Optional[float] = None) -> str:
    """The Task Scheduler definition of ``task``. Raises :class:`ScheduleError` for a schedule that Windows cannot
    express (an odd cron line)."""
    when = task.when
    now = time.time() if now is None else now
    start = datetime.fromtimestamp(float(when.get("anchor") or now)).replace(microsecond=0)
    kind = when.get("kind")
    if kind == "once":
        trigger = f"<TimeTrigger><StartBoundary>{datetime.fromtimestamp(float(when['at'])).strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary><Enabled>true</Enabled></TimeTrigger>"
    elif kind == "every":
        minutes = max(1, int(when["seconds"]) // 60)
        interval = f"PT{minutes}M" if minutes % 1440 else f"P{minutes // 1440}D"
        trigger = (f"<TimeTrigger><StartBoundary>{start.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary><Enabled>true</Enabled>"
                   f"<Repetition><Interval>{interval}</Interval></Repetition></TimeTrigger>")
    elif kind in ("daily", "cron"):
        trigger = _calendar_trigger(when, now)
    else:
        raise ScheduleError("unknown schedule")
    exe, *args = list(command)
    arguments = " ".join(f'"{arg}"' if " " in arg else arg for arg in args)
    account = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    limit = f"PT{max(1, task.timeout_minutes)}M"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{_xml_escape('MemuCode scheduled task: ' + task.title)}</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><UserId>{_xml_escape(account)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="Author"><Exec><Command>{_xml_escape(exe)}</Command><Arguments>{_xml_escape(arguments)}</Arguments><WorkingDirectory>{_xml_escape(task.folder)}</WorkingDirectory></Exec></Actions>
</Task>
"""

_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def _calendar_trigger(when: Dict[str, Any], now: float) -> str:
    if when.get("kind") == "daily":
        hour, minute = _clock(str(when["time"]))
        days = sorted(set(when.get("days") or range(7)))
    else:
        minutes, hours, dom, months, weekdays = parse_cron(str(when["expr"]))
        if len(minutes) != 1 or len(hours) != 1 or len(dom) != 31 or len(months) != 12:
            raise ScheduleError(tr("Windows не умеет такое расписание; выберите запуск «в этом окне» или проще расписание.",
                                   "Windows cannot express this schedule; choose “in this window” or a simpler schedule."))
        hour, minute = next(iter(hours)), next(iter(minutes))
        days = sorted((day - 1) % 7 for day in weekdays) if len(weekdays) != 7 else list(range(7))
    start = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    boundary = start.strftime("%Y-%m-%dT%H:%M:%S")
    if len(days) == 7:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        schedule = "<ScheduleByWeek><DaysOfWeek>" + "".join(f"<{_WEEK[day]}/>" for day in days) + "</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
    return f"<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>{schedule}</CalendarTrigger>"

def _run(run: Optional[Runner], *argv: str) -> Tuple[int, str]:
    runner = run or subprocess.run
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = runner(list(argv), capture_output=True, timeout=60, creationflags=flags)
    output = (getattr(done, "stdout", b"") or b"") + (getattr(done, "stderr", b"") or b"")
    text = _console_text(output) if isinstance(output, (bytes, bytearray)) else str(output)
    return int(getattr(done, "returncode", 1)), text.strip()

def _console_text(raw: bytes) -> str:
    """schtasks writes in the OEM code page (cp866 on a Russian Windows), not UTF-8. Decoded as UTF-8 the
    Russian «не удается найти» never matched, so deleting an already missing task was reported as a failure."""
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            return bytes(raw).decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", errors="replace")
        except Exception:
            pass
    return bytes(raw).decode("utf-8", errors="replace")

def register(task: Task, *, run: Optional[Runner] = None, command: Optional[Sequence[str]] = None) -> Tuple[bool, str]:
    """Create or replace the Task Scheduler entry of ``task``. ``(ok, message)``."""
    if not system_available():
        return False, tr("Планировщик Windows есть только в Windows.", "The Windows Task Scheduler exists only on Windows.")
    try:
        xml = build_xml(task, command or system_command(task.id))
    except ScheduleError as exc:
        return False, str(exc)
    path = home() / f"{task.id}.xml"
    path.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
    try:
        code, text = _run(run, "schtasks", "/Create", "/TN", task_name(task.id), "/XML", str(path), "/F")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return code == 0, text

def unregister(task_id: str, *, run: Optional[Runner] = None) -> Tuple[bool, str]:
    if not system_available():
        return True, ""
    code, text = _run(run, "schtasks", "/Delete", "/TN", task_name(task_id), "/F")
    missing = "cannot find" in text.lower() or "не удается найти" in text.lower() or "не найден" in text.lower()
    return code == 0 or missing, text

def registered(task_id: str, *, run: Optional[Runner] = None) -> bool:
    if not system_available():
        return False
    code, _text = _run(run, "schtasks", "/Query", "/TN", task_name(task_id))
    return code == 0

def claim_run(task_id: str, started: float) -> bool:
    """Mark a task as running — atomically, so that two windows (or a window and the Task Scheduler) that notice the
    same due task do not both run it. False when it is already running."""
    outcome: Dict[str, bool] = {"claimed": False}

    def change(tasks: Dict[str, Any]) -> bool:
        row = tasks.get(task_id)
        if not isinstance(row, dict):
            return False
        limit = int(row.get("timeout_minutes") or DEFAULT_TIMEOUT_MINUTES) * 60 + 120
        busy = float(row.get("running_since") or 0.0)
        if busy and started - busy < limit:
            return False
        row["running_since"] = started
        row["last_status"] = "running"
        outcome["claimed"] = True
        return True
    _mutate(change)
    return outcome["claimed"]
