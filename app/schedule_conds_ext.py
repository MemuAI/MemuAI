# -*- coding: utf-8 -*-
"""Round 37: more conditions and options for Planning (programmable schedules).

What the core engine (``app/schedule_rules.py``) delegates here:

* conditions ``calendar`` (workday / weekend / public holiday of a country, the N-th weekday of the month,
  the first / last workday of the month, chosen months / days of month, even / odd week, a yearly date range),
  ``sun`` (daytime / night with an offset, computed locally — no network), ``weather`` (Open-Meteo, no key,
  cached), ``wifi`` (connected to a network by name), ``last_result`` (status / text of the previous run of
  this or another task, «within N hours»), ``group`` (a nested all / any group); any condition may carry
  ``"not": true``;
* the trigger ``sun`` (sunrise / sunset ± offset) and the ``monthly`` modes ``nth_weekday`` («the 2nd Tuesday»,
  «the last Friday») and ``workday`` («the 3rd workday», «the last workday of the month»);
* the options ``quiet_from/quiet_to`` (runs are postponed until the quiet hours end), ``max_per_day`` and
  ``retry_count/retry_minutes`` (a failed run is retried with an exponential back-off);
* ``explain()`` — «Проверить сейчас»: every condition with its current value and why it holds or not;
* ``render_vars()`` — ``{{date}}``, ``{{time}}``, ``{{weekday}}``, ``{{run_count}}``, ``{{last_result}}`` … in a
  plain prompt.

Everything measured here is cached (weather 20 min, Wi-Fi 30 s, a country's holidays — per year on disk) and
is measured only when an enabled rule needs it (``needs_ext`` → ``schedule_sampler``). On a system that cannot
tell (Wi-Fi on macOS, no network for the weather) the value is ``None`` = «cannot check», never an exception.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:   # FastAPI resolves the string annotations of the routes below against this module
    from fastapi import Request
except Exception:  # noqa: BLE001 - the engine works without FastAPI (MemuNote, tests)
    Request = Any  # type: ignore[assignment,misc]

log = logging.getLogger("memuai.schedule_conds_ext")

CONDITION_TYPES = ("calendar", "sun", "weather", "wifi", "last_result", "group")
CALENDAR_MODES = ("workday", "weekend", "holiday", "day_off", "short_day", "nth_weekday", "first_workday", "last_workday",
                  "first_workday_week", "last_workday_week", "month_days", "months", "week_parity", "date_range")
#: modes that depend on a country's calendar (holidays / Russia's production calendar)
COUNTRY_MODES = ("workday", "holiday", "day_off", "short_day", "first_workday", "last_workday", "first_workday_week",
                 "last_workday_week")
WEATHER_METRICS = ("temp", "feels", "precip", "precip_prob", "wind", "cloud", "humidity", "rain", "snow", "clear")
_NUMERIC_METRICS = ("temp", "feels", "precip", "precip_prob", "wind", "cloud", "humidity")
MAX_GROUP_ITEMS = 8
WEATHER_TTL = 20 * 60
WEATHER_RETRY = 5 * 60
WEATHER_STALE = 2 * 3600
WIFI_TTL = 30.0
MAX_RETRY = 5
LAST_TEXT = 600

# Countries with built-in public holidays (no network). Others use Nager.Date (cached per year on disk).
COUNTRIES = {
    "RU": ("Россия", "Russia"), "BY": ("Беларусь", "Belarus"), "KZ": ("Казахстан", "Kazakhstan"),
    "LT": ("Литва", "Lithuania"), "LV": ("Латвия", "Latvia"), "EE": ("Эстония", "Estonia"),
    "PL": ("Польша", "Poland"), "DE": ("Германия", "Germany"), "FR": ("Франция", "France"),
    "GB": ("Великобритания", "United Kingdom"), "US": ("США", "United States"),
}


class CondError(ValueError):
    def __init__(self, ru: str, en: str) -> None:
        super().__init__(en)
        self.ru, self.en = ru, en


def _rule_error(ru: str, en: str) -> Exception:
    from .schedule_rules import RuleError
    return RuleError(ru, en)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _int_list(value: Any, lo: int, hi: int, limit: int = 31) -> List[int]:
    if isinstance(value, (int, float, str)):
        value = re.split(r"[,\s]+", str(value))
    if not isinstance(value, list):
        return []
    out = set()
    for item in value[:64]:
        text = str(item).strip()
        span = re.fullmatch(r"(-?\d+)\s*[-–]\s*(-?\d+)", text)
        if span:
            a, b = int(span.group(1)), int(span.group(2))
            for n in range(min(a, b), max(a, b) + 1):
                if lo <= n <= hi:
                    out.add(n)
            continue
        try:
            n = int(float(text))
        except ValueError:
            continue
        if lo <= n <= hi:
            out.add(n)
    return sorted(out)[:limit]


def _mmdd(value: Any) -> str:
    """«MM-DD», «YYYY-MM-DD» or «DD.MM» → «MM-DD»."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(?:\d{4}-)?(\d{1,2})-(\d{1,2})", text)
    dotted = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
    elif dotted:
        day, month = int(dotted.group(1)), int(dotted.group(2))
    else:
        raise _rule_error("Укажите дату в виде ММ-ДД", "Enter the date as MM-DD")
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise _rule_error("Неверная дата", "Invalid date")
    return f"{month:02d}-{day:02d}"


def _place(raw: Dict[str, Any], out: Dict[str, Any]) -> None:
    lat, lon = _float(raw.get("lat")), _float(raw.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise _rule_error("Выберите место (город) для расчёта", "Choose a place (city) for the calculation")
    out["lat"], out["lon"] = round(lat, 4), round(lon, 4)
    place = str(raw.get("place") or "").strip()[:80]
    if place:
        out["place"] = place


def _country(value: Any) -> str:
    code = str(value or "").strip().upper()[:2]
    if not re.fullmatch(r"[A-Z]{2}", code):
        return "RU"
    return code


# --------------------------------------------------------------------------- normalization

def normalize_condition(raw: Dict[str, Any], kind: str, *, depth: int = 0) -> Dict[str, Any]:
    """Validate one of :data:`CONDITION_TYPES` (``not`` is handled by the caller)."""
    if kind == "group":
        if depth >= 1:
            raise _rule_error("Группа не может быть внутри группы", "A group cannot be inside a group")
        items = raw.get("items") or raw.get("only_if") or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            raise _rule_error("Добавьте условия в группу", "Add conditions to the group")
        from .schedule_rules import _normalize_condition
        clean = [_normalize_condition(item, depth=depth + 1) for item in items[:MAX_GROUP_ITEMS]]
        return {"type": "group", "match": "any" if str(raw.get("match") or "all").lower() == "any" else "all",
                "items": clean}
    if kind == "calendar":
        mode = str(raw.get("mode") or "workday").lower()
        if mode not in CALENDAR_MODES:
            raise _rule_error(f"Неизвестный вид календарного условия «{mode[:30]}»", f"Unknown calendar condition “{mode[:30]}”")
        out: Dict[str, Any] = {"type": "calendar", "mode": mode}
        if mode in COUNTRY_MODES or mode == "weekend":
            out["country"] = _country(raw.get("country"))
        if mode == "nth_weekday":
            nth = _int(raw.get("nth"), 1, -1, 5)
            out["nth"] = -1 if nth <= 0 else nth
            out["weekday"] = _int(raw.get("weekday"), 1, 1, 7)
        elif mode == "month_days":
            days = _int_list(raw.get("days"), -1, 31)
            days = [d for d in days if d != 0]
            if not days:
                raise _rule_error("Укажите числа месяца, например 1-10", "Enter days of the month, e.g. 1-10")
            out["days"] = days
        elif mode == "months":
            months = _int_list(raw.get("months"), 1, 12, 12)
            if not months:
                raise _rule_error("Выберите месяцы", "Choose months")
            out["months"] = months
        elif mode == "week_parity":
            out["value"] = "odd" if str(raw.get("value") or "even").lower().startswith("o") else "even"
        elif mode == "date_range":
            out["from"], out["to"] = _mmdd(raw.get("from")), _mmdd(raw.get("to"))
        return out
    if kind == "sun":
        out = {"type": "sun", "value": "night" if str(raw.get("value") or "day").lower().startswith("n") else "day",
               "offset": _int(raw.get("offset"), 0, -180, 180)}
        _place(raw, out)
        return out
    if kind == "weather":
        metric = str(raw.get("metric") or "temp").lower()
        if metric not in WEATHER_METRICS:
            raise _rule_error(f"Неизвестный показатель погоды «{metric[:20]}»", f"Unknown weather metric “{metric[:20]}”")
        out = {"type": "weather", "metric": metric}
        if metric in _NUMERIC_METRICS:
            out["op"] = "lt" if str(raw.get("op") or "gt").lower() in ("lt", "below", "<") else "gt"
            value = _float(raw.get("value"))
            if value is None:
                raise _rule_error("Укажите порог погоды", "Enter the weather threshold")
            out["value"] = round(value, 1)
        _place(raw, out)
        return out
    if kind == "wifi":
        ssid = str(raw.get("ssid") or raw.get("name") or "").strip()[:64]
        return {"type": "wifi", "ssid": ssid, "value": raw.get("value") not in (False, "false", 0, "0")}
    if kind == "last_result":
        status = str(raw.get("status") or "any").lower()
        out = {"type": "last_result", "task_id": str(raw.get("task_id") or "").strip()[:120],
               "status": status if status in ("ok", "fail", "any") else "any"}
        contains = str(raw.get("contains") or "").strip()[:200]
        if contains:
            out["contains"] = contains
        within = _int(raw.get("within_hours"), 0, 0, 24 * 90)
        if within:
            out["within_hours"] = within
        if out["status"] == "any" and not contains and not within:
            raise _rule_error("Укажите, каким должен быть прошлый результат", "Say what the previous result must be")
        return out
    raise _rule_error(f"Неизвестное условие «{kind[:30]}»", f"Unknown condition “{kind[:30]}”")


def normalize_sun_trigger(raw: Dict[str, Any]) -> Dict[str, Any]:
    from .schedule_rules import _days
    out = {"type": "sun", "event": "sunset" if str(raw.get("event") or "sunrise").lower().startswith("sunset") else "sunrise",
           "offset": _int(raw.get("offset"), 0, -180, 180), "days": _days(raw.get("days"))}
    _place(raw, out)
    return out


def normalize_monthly(raw: Dict[str, Any], time_text: str) -> Optional[Dict[str, Any]]:
    """The extended monthly modes, or None for the classic «day N»."""
    mode = str(raw.get("mode") or "").lower()
    if mode == "nth_weekday" or (not mode and raw.get("weekday") not in (None, "")):
        nth = _int(raw.get("nth"), 1, -1, 5)
        return {"type": "monthly", "mode": "nth_weekday", "nth": -1 if nth <= 0 else nth,
                "weekday": _int(raw.get("weekday"), 1, 1, 7), "time": time_text}
    if mode == "workday":
        nth = _int(raw.get("nth", raw.get("day")), 1, -1, 23)
        return {"type": "monthly", "mode": "workday", "nth": -1 if nth <= 0 else nth,
                "country": _country(raw.get("country")), "time": time_text}
    return None


def normalize_options(raw: Dict[str, Any], out: Dict[str, Any]) -> None:
    """Adds the Round 37 options only when set (old rules keep their exact signature)."""
    from .schedule_rules import _hhmm
    start, end = raw.get("quiet_from"), raw.get("quiet_to")
    if start not in (None, "") and end not in (None, ""):
        out["quiet_from"], out["quiet_to"] = _hhmm(start), _hhmm(end)
        if out["quiet_from"] == out["quiet_to"]:
            raise _rule_error("Начало и конец тихих часов совпадают", "Quiet hours start and end at the same time")
    per_day = _int(raw.get("max_per_day"), 0, 0, 1000)
    if per_day:
        out["max_per_day"] = per_day
    retries = _int(raw.get("retry_count"), 0, 0, MAX_RETRY)
    if retries:
        out["retry_count"] = retries
        out["retry_minutes"] = _int(raw.get("retry_minutes"), 5, 1, 1440)


# --------------------------------------------------------------------------- calendar

def _easter(year: int, orthodox: bool = False) -> date:
    if orthodox:
        a, b, c = year % 4, year % 7, year % 19
        d = (19 * c + 15) % 30
        e = (2 * a + 4 * b - d + 34) % 7
        month, day = divmod(d + e + 114, 31)
        return date(year, month, day + 1) + timedelta(days=13)   # Julian → Gregorian (1900–2099)
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday_of(year: int, month: int, weekday: int, nth: int) -> Optional[date]:
    """``weekday`` 1=Mon…7=Sun; ``nth`` 1…5 or -1 (last). None when the month has no such day."""
    if nth == -1:
        last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        return last - timedelta(days=(last.isoweekday() - weekday) % 7)
    first = date(year, month, 1)
    day = first + timedelta(days=(weekday - first.isoweekday()) % 7 + 7 * (nth - 1))
    return day if day.month == month else None


def _us_observed(d: date) -> date:
    return d - timedelta(days=1) if d.isoweekday() == 6 else d + timedelta(days=1) if d.isoweekday() == 7 else d


def builtin_holidays(year: int, country: str) -> Optional[set]:
    fixed = lambda *pairs: {date(year, m, d) for m, d in pairs}   # noqa: E731
    if country == "RU":
        return fixed(*[(1, d) for d in range(1, 9)], (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4))
    if country == "BY":
        return fixed((1, 1), (1, 2), (1, 7), (3, 8), (5, 1), (5, 9), (7, 3), (11, 7), (12, 25)) | {
            _easter(year, True) + timedelta(days=9)}
    if country == "KZ":
        return fixed((1, 1), (1, 2), (1, 7), (3, 8), (3, 21), (3, 22), (3, 23), (5, 1), (5, 7), (5, 9), (7, 6),
                     (8, 30), (10, 25), (12, 16))
    if country == "LT":
        e = _easter(year)
        return fixed((1, 1), (2, 16), (3, 11), (5, 1), (6, 24), (7, 6), (8, 15), (11, 1), (11, 2), (12, 24),
                     (12, 25), (12, 26)) | {e, e + timedelta(days=1)}
    if country == "LV":
        e = _easter(year)
        return fixed((1, 1), (5, 1), (5, 4), (6, 23), (6, 24), (11, 18), (12, 24), (12, 25), (12, 26), (12, 31)) | {
            e - timedelta(days=2), e, e + timedelta(days=1)}
    if country == "EE":
        e = _easter(year)
        return fixed((1, 1), (2, 24), (5, 1), (6, 23), (6, 24), (8, 20), (12, 24), (12, 25), (12, 26)) | {
            e - timedelta(days=2), e, e + timedelta(days=49)}
    if country == "PL":
        e = _easter(year)
        out = fixed((1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 25), (12, 26)) | {
            e, e + timedelta(days=1), e + timedelta(days=49), e + timedelta(days=60)}
        if year >= 2025:
            out.add(date(year, 12, 24))
        return out
    if country == "DE":
        e = _easter(year)
        return fixed((1, 1), (5, 1), (10, 3), (12, 25), (12, 26)) | {
            e - timedelta(days=2), e + timedelta(days=1), e + timedelta(days=39), e + timedelta(days=50)}
    if country == "FR":
        e = _easter(year)
        return fixed((1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)) | {
            e + timedelta(days=1), e + timedelta(days=39), e + timedelta(days=50)}
    if country == "GB":
        e = _easter(year)
        out = {e - timedelta(days=2), e + timedelta(days=1), nth_weekday_of(year, 5, 1, 1),
               nth_weekday_of(year, 5, 1, -1), nth_weekday_of(year, 8, 1, -1)}
        new_year = date(year, 1, 1)
        out.add(new_year + timedelta(days={6: 2, 7: 1}.get(new_year.isoweekday(), 0)))
        xmas, boxing = date(year, 12, 25), date(year, 12, 26)
        if xmas.isoweekday() == 6:
            out |= {date(year, 12, 27), date(year, 12, 28)}
        elif xmas.isoweekday() == 7:
            out |= {date(year, 12, 26), date(year, 12, 27)}
        else:
            out |= {xmas, boxing + timedelta(days=2) if boxing.isoweekday() == 6 else boxing}
        return out
    if country == "US":
        out = {_us_observed(date(year, m, d)) for m, d in ((1, 1), (6, 19), (7, 4), (11, 11), (12, 25))}
        out |= {nth_weekday_of(year, 1, 1, 3), nth_weekday_of(year, 2, 1, 3), nth_weekday_of(year, 5, 1, -1),
                nth_weekday_of(year, 9, 1, 1), nth_weekday_of(year, 10, 1, 2), nth_weekday_of(year, 11, 4, 4)}
        return out
    return None


_HOLIDAYS: Dict[Tuple[str, int], set] = {}
_HOL_LOCK = threading.Lock()
_HOL_TRIED: Dict[Tuple[str, int], float] = {}


def _holidays_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "holidays"


def holidays(year: int, country: str, *, fetch: bool = False) -> Optional[set]:
    """Public holidays of ``country`` in ``year`` — built-in, else Nager.Date cached on disk.
    ``fetch`` (the sampler thread only) may download a missing year once an hour."""
    key = (country, year)
    with _HOL_LOCK:
        if key in _HOLIDAYS:
            return _HOLIDAYS[key]
    found = builtin_holidays(year, country)
    if found is None:
        path = _holidays_dir() / f"{country}-{year}.json"
        try:
            found = {date.fromisoformat(x) for x in json.loads(path.read_text("utf-8"))}
        except Exception:
            found = None
        if found is None and fetch and time.time() - _HOL_TRIED.get(key, 0) > 3600:
            _HOL_TRIED[key] = time.time()
            found = _fetch_nager(year, country, path)
    if found is not None:
        with _HOL_LOCK:
            _HOLIDAYS[key] = found
    return found


def _fetch_nager(year: int, country: str, path: Path) -> Optional[set]:
    try:
        import httpx
        resp = httpx.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}", timeout=8.0)
        if resp.status_code != 200:
            return set() if resp.status_code == 404 else None
        days = sorted({str(row.get("date"))[:10] for row in resp.json()
                       if isinstance(row, dict) and (row.get("global", True) or "Public" in (row.get("types") or []))})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(days), "utf-8")
        return {date.fromisoformat(x) for x in days}
    except Exception as exc:  # noqa: BLE001
        log.debug("holidays %s %s: %s", country, year, exc)
        return None


# Russia's production calendar: the official moves of days off («переносы»). Per year — weekdays that are days off,
# weekend days that are workdays, shortened pre-holiday days (MMDD). Built in for the years below (from the
# government decrees, checked against isdayoff.ru); other years come from isdayoff.ru (xmlcalendar.ru as a
# fallback) in the background and are cached on disk. No data → the fixed holidays above.
RU_PRODUCTION = {
    2025: ("0101 0102 0103 0106 0107 0108 0501 0502 0508 0509 0612 0613 1103 1104 1231", "1101",
           "0307 0430 0611 1101"),
    2026: ("0101 0102 0105 0106 0107 0108 0109 0223 0309 0501 0511 0612 1104 1231", "", "0430 0508 0611 1103"),
}
_PROD: Dict[int, Dict[str, Any]] = {}
_PROD_TRIED: Dict[int, float] = {}


def _prod_from_codes(year: int, codes: str, source: str) -> Optional[Dict[str, Any]]:
    """isdayoff.ru «getdata» answer: one digit per day (0 work, 1 day off, 2 shortened, 4 work)."""
    codes = codes.strip()
    days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    if len(codes) != days or not set(codes) <= set("0124"):
        return None
    off, work, short = set(), set(), set()
    for index, code in enumerate(codes):
        d = date(year, 1, 1) + timedelta(days=index)
        weekend = d.isoweekday() >= 6
        if code == "1" and not weekend:
            off.add(d)
        elif code != "1" and weekend:
            work.add(d)
        if code == "2":
            short.add(d)
    return {"off": off, "work": work, "short": short, "source": source}


def _prod_from_xmlcalendar(year: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """xmlcalendar.ru JSON: per month the non-working days («9+» moved day off) and «30*» shortened days."""
    listed: Dict[date, str] = {}
    for month in data.get("months") or []:
        for item in str(month.get("days") or "").split(","):
            m = re.match(r"^\s*(\d{1,2})([*+]?)\s*$", item)
            if m:
                listed[date(year, int(month["month"]), int(m.group(1)))] = m.group(2)
    if len(listed) < 100:
        return None
    codes = []
    d = date(year, 1, 1)
    while d.year == year:
        mark = listed.get(d)
        codes.append("2" if mark == "*" else "1" if mark is not None else "0")
        d += timedelta(days=1)
    return _prod_from_codes(year, "".join(codes), "xmlcalendar.ru")


def _fetch_production(year: int, path: Path) -> Optional[Dict[str, Any]]:
    import httpx
    found = None
    try:
        resp = httpx.get("https://isdayoff.ru/api/getdata", params={"year": year, "cc": "ru", "pre": 1}, timeout=8.0)
        if resp.status_code == 200:
            found = _prod_from_codes(year, resp.text, "isdayoff.ru")
    except Exception as exc:  # noqa: BLE001
        log.debug("isdayoff %s: %s", year, exc)
    if found is None:
        try:
            resp = httpx.get(f"https://xmlcalendar.ru/data/ru/{year}/calendar.json", timeout=8.0)
            if resp.status_code == 200:
                found = _prod_from_xmlcalendar(year, resp.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("xmlcalendar %s: %s", year, exc)
    if found is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: sorted(x.isoformat() for x in found[k]) for k in ("off", "work", "short")}
                                       | {"source": found["source"]}), "utf-8")
        except OSError as exc:
            log.debug("production calendar cache: %s", exc)
    return found


def production_calendar(year: int, *, fetch: bool = False) -> Optional[Dict[str, Any]]:
    """Russia's production calendar of ``year``: ``{"off", "work", "short": set of dates, "source"}`` or None.
    ``fetch`` (the background pool only) may download a missing year once an hour."""
    with _HOL_LOCK:
        if year in _PROD:
            return _PROD[year]
    found = None
    if year in RU_PRODUCTION:
        off, work, short = RU_PRODUCTION[year]
        conv = lambda text: {date(year, int(x[:2]), int(x[2:])) for x in text.split()}   # noqa: E731
        found = {"off": conv(off), "work": conv(work), "short": conv(short), "source": "built-in"}
    else:
        path = _holidays_dir() / f"RU-production-{year}.json"
        try:
            data = json.loads(path.read_text("utf-8"))
            found = {k: {date.fromisoformat(x) for x in data.get(k) or []} for k in ("off", "work", "short")}
            found["source"] = str(data.get("source") or "")
        except Exception:
            found = None
        if found is None and fetch and time.time() - _PROD_TRIED.get(year, 0) > 3600:
            _PROD_TRIED[year] = time.time()
            found = _fetch_production(year, path)
    if found is not None:
        with _HOL_LOCK:
            _PROD[year] = found
    return found


def day_type(d: date, country: str) -> Optional[str]:
    """``work`` / ``short`` (a shortened pre-holiday workday) / ``off``; None — the holidays are not known yet."""
    if country == "RU":
        prod = production_calendar(d.year)
        if prod is not None:
            if d in prod["short"]:
                return "short"
            if d in prod["off"]:
                return "off"
            if d in prod["work"]:
                return "work"
            return "off" if d.isoweekday() >= 6 else "work"
    if d.isoweekday() >= 6:
        return "off"
    hol = holidays(d.year, country)
    if hol is None:
        return None
    if d in hol:
        return "off"
    return "short" if (d + timedelta(days=1)) in hol else "work"


def is_workday(d: date, country: str) -> Optional[bool]:
    kind = day_type(d, country)
    return None if kind is None else kind != "off"


def _workdays_of_month(year: int, month: int, country: str) -> Optional[List[date]]:
    out = []
    d = date(year, month, 1)
    while d.month == month:
        state = is_workday(d, country)
        if state is None:
            return None
        if state:
            out.append(d)
        d += timedelta(days=1)
    return out


def check_calendar(cond: Dict[str, Any], d: date) -> Optional[bool]:
    mode = cond["mode"]
    country = cond.get("country") or "RU"
    if mode == "workday":
        return is_workday(d, country)
    if mode == "weekend":
        return d.isoweekday() >= 6
    if mode == "holiday":
        hol = holidays(d.year, country)
        if country == "RU" and production_calendar(d.year) is not None:
            hol = (hol or set()) | production_calendar(d.year)["off"]
        return None if hol is None else d in hol
    if mode in ("day_off", "short_day"):
        kind = day_type(d, country)
        return None if kind is None else kind == ("off" if mode == "day_off" else "short")
    if mode in ("first_workday", "last_workday"):
        days = _workdays_of_month(d.year, d.month, country)
        if days is None:
            return None
        return bool(days) and d == (days[0] if mode == "first_workday" else days[-1])
    if mode in ("first_workday_week", "last_workday_week"):
        monday = d - timedelta(days=d.isoweekday() - 1)
        week = [monday + timedelta(days=i) for i in range(7)]
        states = [is_workday(x, country) for x in week]
        if None in states:
            return None
        work = [x for x, state in zip(week, states) if state]
        return bool(work) and d == (work[0] if mode == "first_workday_week" else work[-1])
    if mode == "nth_weekday":
        return nth_weekday_of(d.year, d.month, cond["weekday"], cond["nth"]) == d
    if mode == "month_days":
        last = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)).day
        return any(d.day == (n if n > 0 else last + 1 + n) for n in cond["days"])
    if mode == "months":
        return d.month in cond["months"]
    if mode == "week_parity":
        return (d.isocalendar()[1] % 2 == 0) == (cond["value"] == "even")
    if mode == "date_range":
        key = f"{d.month:02d}-{d.day:02d}"
        a, b = cond["from"], cond["to"]
        return a <= key <= b if a <= b else (key >= a or key <= b)
    return None


def monthly_target(trigger: Dict[str, Any], d: date) -> Optional[date]:
    """The day of ``d``'s month an extended monthly trigger fires on."""
    if trigger.get("mode") == "nth_weekday":
        return nth_weekday_of(d.year, d.month, trigger["weekday"], trigger["nth"])
    if trigger.get("mode") == "workday":
        days = _workdays_of_month(d.year, d.month, trigger.get("country") or "RU")
        if not days:
            return None
        n = trigger["nth"]
        if n == -1:
            return days[-1]
        return days[n - 1] if n <= len(days) else None
    return None


# --------------------------------------------------------------------------- sun

def sun_event(d: date, lat: float, lon: float, rising: bool) -> Optional[float]:
    """UTC timestamp of sunrise/sunset on local day ``d`` (None in polar day/night). ±1–2 min accuracy."""
    zenith = math.radians(90.833)
    n = d.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    l_ = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l_)))) % 360
    ra = (ra + (math.floor(l_ / 90) * 90 - math.floor(ra / 90) * 90)) / 15.0
    sin_dec = 0.39782 * math.sin(math.radians(l_))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(zenith) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
    if cos_h > 1 or cos_h < -1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    local_t = h / 15.0 + ra - 0.06571 * t - 6.622
    ut = (local_t - lng_hour) % 24
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
    value = base + ut * 3600
    # the UTC instant may land on the neighbouring local day for far longitudes — bring it back
    for _ in range(2):
        local = datetime.fromtimestamp(value).date()
        if local < d:
            value += 86400
        elif local > d:
            value -= 86400
        else:
            break
    return value


def sun_slot(trigger: Dict[str, Any], d: date) -> Optional[float]:
    moment = sun_event(d, trigger["lat"], trigger["lon"], trigger["event"] == "sunrise")
    return None if moment is None else moment + trigger.get("offset", 0) * 60


def check_sun(cond: Dict[str, Any], now: float) -> Optional[bool]:
    d = datetime.fromtimestamp(now).date()
    rise, sset = sun_event(d, cond["lat"], cond["lon"], True), sun_event(d, cond["lat"], cond["lon"], False)
    if rise is None or sset is None:
        # polar: the sun is up all day in summer (|declination| side), down in winter — decide by noon elevation
        summer = (d.month in range(4, 10)) == (cond["lat"] > 0)
        day = summer
    else:
        off = cond.get("offset", 0) * 60
        day = rise + off <= now < sset - off
    return day if cond["value"] == "day" else not day


# --------------------------------------------------------------------------- weather / Wi-Fi (sampled)

_WEATHER: Dict[str, Dict[str, Any]] = {}
_WEATHER_LOCK = threading.Lock()
_WIFI: Dict[str, Any] = {"at": 0.0, "value": None}


def weather_key(cond: Dict[str, Any]) -> str:
    return f"{round(cond['lat'], 2)},{round(cond['lon'], 2)}"


def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    """Open-Meteo current conditions (no key). Patched in tests."""
    import httpx
    resp = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=8.0, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,precipitation,rain,snowfall,weather_code,"
                   "wind_speed_10m,cloud_cover,relative_humidity_2m",
        "hourly": "precipitation_probability"})
    resp.raise_for_status()
    data = resp.json()
    cur = data.get("current") or {}
    prob = None
    try:
        hours = (data.get("hourly") or {}).get("time") or []
        probs = (data.get("hourly") or {}).get("precipitation_probability") or []
        stamp = str(cur.get("time") or "")[:13]
        for at, value in zip(hours, probs):
            if str(at)[:13] == stamp:
                prob = value
    except Exception:  # noqa: BLE001
        prob = None
    code = int(cur.get("weather_code") or 0)
    return {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
            "precip": cur.get("precipitation"), "precip_prob": prob, "wind": cur.get("wind_speed_10m"),
            "cloud": cur.get("cloud_cover"), "humidity": cur.get("relative_humidity_2m"), "code": code,
            "rain": bool((cur.get("rain") or 0) > 0 or code in range(51, 68) or code in range(80, 83) or code >= 95),
            "snow": bool((cur.get("snowfall") or 0) > 0 or code in range(71, 78) or code in (85, 86)),
            "clear": code in (0, 1)}


def weather_now(key: str, lat: float, lon: float, now: float, *, fetch: bool = True) -> Optional[Dict[str, Any]]:
    """Cached current weather; no network → the last value for up to ``WEATHER_STALE``, then None."""
    with _WEATHER_LOCK:
        entry = _WEATHER.get(key)
    if entry and entry.get("data") is not None and now - entry["at"] < WEATHER_TTL:
        return entry["data"]
    if not fetch or (entry and now - entry.get("tried", 0) < WEATHER_RETRY):
        return entry.get("data") if entry and now - entry["at"] < WEATHER_STALE else None
    try:
        entry = {"at": now, "tried": now, "data": fetch_weather(lat, lon)}
    except Exception as exc:  # noqa: BLE001 - offline → «cannot check» / the last value for a while
        log.debug("weather %s: %s", key, exc)
        old = entry or {"at": 0.0, "data": None}
        entry = {"at": old["at"], "tried": now, "data": old.get("data") if now - old["at"] < WEATHER_STALE else None}
    with _WEATHER_LOCK:
        _WEATHER[key] = entry
    return entry.get("data")


def wifi_ssid() -> Optional[str]:
    """The SSID of the connected Wi-Fi ("" = not on Wi-Fi), None when this system cannot tell."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=4,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            text = out.stdout.decode("utf-8", "replace") if out.stdout else ""
            if out.returncode != 0 and not text:
                return None
            for line in text.splitlines():
                m = re.match(r"^\s*SSID\s*:\s*(.*)$", line)   # not «BSSID»
                if m:
                    return m.group(1).strip()
            return ""
        for cmd in (["iwgetid", "-r"], ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]):
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=4)
            except (OSError, subprocess.SubprocessError):
                continue
            text = out.stdout.decode("utf-8", "replace")
            if cmd[0] == "iwgetid":
                if out.returncode == 0:
                    return text.strip()
                continue
            for line in text.splitlines():
                if line.startswith("yes:"):
                    return line[4:].strip()
            return ""
    except Exception as exc:  # noqa: BLE001
        log.debug("wifi: %s", exc)
    return None


def wifi_now(now: float, probe=None) -> Optional[str]:
    if now - _WIFI["at"] < WIFI_TTL and _WIFI["at"]:
        return _WIFI["value"]
    _WIFI["value"] = (probe or wifi_ssid)()
    _WIFI["at"] = now
    return _WIFI["value"]


def _walk(conds: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for cond in conds:
        if cond.get("off"):
            continue
        if cond.get("type") == "group":
            yield from _walk(cond.get("items") or [])
        else:
            yield cond


def needs_ext(rule: Dict[str, Any], out: Dict[str, Any]) -> None:
    """Adds ``out["ext"]`` (key → spec) and the core flags that grouped conditions need."""
    from .schedule_rules import process_key
    ext = out.setdefault("ext", {})
    for cond in _walk(rule.get("only_if") or []):
        kind = cond.get("type")
        if kind == "weather":
            ext["weather:" + weather_key(cond)] = {"lat": cond["lat"], "lon": cond["lon"]}
        elif kind == "wifi":
            ext["wifi"] = {}
        elif kind == "calendar" and cond.get("country") and _loads_calendar(cond["country"]):
            ext["holidays:" + cond["country"]] = {}
        elif kind == "process":
            out["processes"].add(process_key(cond["name"]))
        elif kind == "memuai_open":
            out["flags"].add("memuai")
        elif kind in ("idle", "network", "power", "battery", "focus"):
            out["flags"].add({"battery": "power"}.get(kind, kind))
    for trigger in rule.get("when") or []:
        if trigger.get("type") == "monthly" and trigger.get("mode") == "workday" \
                and _loads_calendar(trigger.get("country") or "RU"):
            ext["holidays:" + (trigger.get("country") or "RU")] = {}
    if not ext:
        out.pop("ext", None)


def _loads_calendar(country: str) -> bool:
    """A country whose calendar may need the network: no built-in holidays, or Russia's production calendar."""
    return country == "RU" or builtin_holidays(2000, country) is None


# Network and slow probes (weather, geocoding-free holidays, Wi-Fi via netsh) never block the Proxy tick: they run
# in a small pool; the tick waits at most BACKGROUND_WAIT and otherwise uses the cached value (None the first time,
# the fresh value on the next tick).
BACKGROUND_WAIT = 0.4
_BG_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_BG: Dict[str, concurrent.futures.Future] = {}
_BG_LOCK = threading.Lock()


def _offload(key: str, fn) -> None:
    global _BG_POOL
    with _BG_LOCK:
        fut = _BG.get(key)
        if fut is None:
            if _BG_POOL is None:
                _BG_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="memu-conds")
            fut = _BG[key] = _BG_POOL.submit(fn)
    try:
        fut.result(timeout=BACKGROUND_WAIT)
    except concurrent.futures.TimeoutError:
        return
    except Exception as exc:  # noqa: BLE001 - a failed probe means «cannot check», never a broken tick
        log.debug("background %s: %s", key, exc)
    with _BG_LOCK:
        if _BG.get(key) is fut:
            del _BG[key]


def _weather_due(key: str, now: float) -> bool:
    with _WEATHER_LOCK:
        entry = _WEATHER.get(key)
    if not entry:
        return True
    return now - entry["at"] >= WEATHER_TTL and now - entry.get("tried", 0) >= WEATHER_RETRY


def weather_cached(key: str, lat: float, lon: float, now: float) -> Optional[Dict[str, Any]]:
    """The current weather without blocking: a due refresh runs in the background pool."""
    if _weather_due(key, now):
        _offload("weather:" + key, lambda: weather_now(key, lat, lon, now))
    return weather_now(key, lat, lon, now, fetch=False)


def prefetch(ext: Dict[str, Any], now: float) -> None:
    """Blocking warm-up for «Проверить сейчас» (a request thread, not the tick): fresh weather, Wi-Fi, calendars."""
    year = datetime.fromtimestamp(now).year
    for key, spec in list(ext.items())[:12]:
        if key == "wifi":
            wifi_now(now)
        elif key.startswith("weather:"):
            weather_now(key[8:], spec["lat"], spec["lon"], now)
        elif key.startswith("holidays:"):
            if key[9:] == "RU":
                production_calendar(year, fetch=True)
            else:
                holidays(year, key[9:], fetch=True)


def measure(ext: Dict[str, Any], now: float, *, probes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The sampler's part: weather per place, Wi-Fi, a country's holidays (all cached, loaded in the background)."""
    probes = probes or {}
    out: Dict[str, Any] = {}
    for key, spec in list(ext.items())[:12]:
        if key == "wifi":
            if not (_WIFI["at"] and now - _WIFI["at"] < WIFI_TTL):
                _offload("wifi", lambda: wifi_now(now, probes.get("wifi")))
            out["wifi"] = _WIFI["value"] if _WIFI["at"] else None
        elif key.startswith("weather:"):
            out[key] = weather_cached(key[8:], spec["lat"], spec["lon"], now)
        elif key.startswith("holidays:"):
            year, country = datetime.fromtimestamp(now).year, key[9:]
            years = (year, year + 1) if datetime.fromtimestamp(now).month == 12 else (year,)

            def load(country=country, years=years) -> None:
                for y in years:
                    if country == "RU":
                        production_calendar(y, fetch=True)
                    else:
                        holidays(y, country, fetch=True)
            if any((production_calendar(y) if country == "RU" else holidays(y, country)) is None for y in years):
                _offload(key, load)
            out[key] = (production_calendar(year) if country == "RU" else holidays(year, country)) is not None
    return out


# --------------------------------------------------------------------------- checks

def check(cond: Dict[str, Any], sample: Dict[str, Any], now: float, ctx: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    kind = cond["type"]
    if kind == "calendar":
        return check_calendar(cond, datetime.fromtimestamp(now).date())
    if kind == "sun":
        return check_sun(cond, now)
    if kind == "weather":
        data = (sample.get("ext") or {}).get("weather:" + weather_key(cond))
        if not isinstance(data, dict):
            return None
        value = data.get(cond["metric"])
        if value is None:
            return None
        if cond["metric"] in _NUMERIC_METRICS:
            return float(value) > cond["value"] if cond["op"] == "gt" else float(value) < cond["value"]
        return bool(value)
    if kind == "wifi":
        ssid = (sample.get("ext") or {}).get("wifi")
        if ssid is None:
            return None
        connected = bool(ssid) and (not cond["ssid"] or ssid.casefold() == cond["ssid"].casefold())
        return connected == cond["value"]
    if kind == "last_result":
        ctx = ctx or {}
        task = ctx.get("task") or {}
        if cond.get("task_id"):
            task = (ctx.get("tasks_by_id") or {}).get(cond["task_id"])
            if task is None:
                return None
        finished = task.get("last_finished")
        if not isinstance(finished, (int, float)):
            return False
        if cond.get("within_hours") and now - finished > cond["within_hours"] * 3600:
            return False
        if cond["status"] != "any" and task.get("last_status") != cond["status"]:
            return False
        if cond.get("contains"):
            from .schedule_triggers_ext import text_found
            return text_found(str(task.get("last_text") or ""), cond["contains"], exact=False)
        return True
    return None


def check_group(cond: Dict[str, Any], sample: Dict[str, Any], now: float, ctx: Optional[Dict[str, Any]],
                one) -> Optional[bool]:
    values = [one(item, sample, now, ctx) for item in cond.get("items") or [] if not item.get("off")]
    if cond.get("match") == "any":
        if any(v is True for v in values):
            return True
        return None if values and all(v is None for v in values) else False
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


# --------------------------------------------------------------------------- options

def _in_window(start: str, end: str, now: float) -> Optional[float]:
    """The end timestamp of the [start, end) daily window containing ``now``, else None."""
    from .schedule_rules import _window_bounds
    moment = datetime.fromtimestamp(now)
    for d in (moment.date() - timedelta(days=1), moment.date()):
        begin, finish = _window_bounds(d, start, end)
        if begin <= moment < finish:
            return finish.timestamp()
    return None


def quiet_until(options: Dict[str, Any], now: float) -> Optional[float]:
    if not options.get("quiet_from"):
        return None
    return _in_window(options["quiet_from"], options["quiet_to"], now)


def day_runs(state: Dict[str, Any], now: float) -> int:
    today = datetime.fromtimestamp(now).date().isoformat()
    row = state.get("day_runs")
    return int(row.get("n") or 0) if isinstance(row, dict) and row.get("d") == today else 0


def count_day_run(state: Dict[str, Any], now: float) -> None:
    today = datetime.fromtimestamp(now).date().isoformat()
    state["day_runs"] = {"d": today, "n": day_runs(state, now) + 1}


def plan_retry(task: Dict[str, Any], ok: bool, now: float) -> None:
    """After a run: a failed one gets ``rule_state.retry_at`` (exponential back-off), success clears it."""
    rule = task.get("rule") if isinstance(task.get("rule"), dict) else {}
    options = rule.get("options") or {}
    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else None
    if state is None:
        return
    count = _int(options.get("retry_count"), 0, 0, MAX_RETRY)
    if ok or not count:
        for key in ("retry_at", "retry_n"):
            state.pop(key, None)
        return
    used = _int(state.get("retry_n"), 0, 0, MAX_RETRY)
    reason = state.get("reason") if isinstance(state.get("reason"), dict) else {}
    if not task.get("enabled", True) and state.get("status") == "finished" \
            and reason.get("en") == "No more runs are planned" and used < count:
        # a one-time run that failed: it stays alive for its retries
        task["enabled"] = True
        state["status"] = "waiting"
        state.pop("reason", None)
    if used >= count or not task.get("enabled", True):
        state.pop("retry_at", None)
        state.pop("retry_n", None)
        return
    minutes = _int(options.get("retry_minutes"), 5, 1, 1440)
    state["retry_at"] = now + minutes * 60 * (2 ** used)
    state["retry_n"] = used + 1


# --------------------------------------------------------------------------- texts

_WD_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_WD_RU_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
_WD_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MON_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
           "ноябрь", "декабрь"]
_MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_NTH_RU = {1: "первый", 2: "второй", 3: "третий", 4: "четвёртый", 5: "пятый", -1: "последний"}
_NTH_RU_F = {1: "первую", 2: "вторую", 3: "третью", 4: "четвёртую", 5: "пятую", -1: "последнюю"}
_NTH_EN = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", -1: "last"}
_METRIC = {"temp": ("температура", "temperature", "°C"), "feels": ("ощущается как", "feels like", "°C"),
           "precip": ("осадки", "precipitation", " мм"), "precip_prob": ("вероятность осадков", "chance of rain", "%"),
           "wind": ("ветер", "wind", " км/ч"), "cloud": ("облачность", "cloud cover", "%"),
           "humidity": ("влажность", "humidity", "%"),
           "rain": ("идёт дождь", "it is raining", ""), "snow": ("идёт снег", "it is snowing", ""),
           "clear": ("ясно", "the sky is clear", "")}


def _wd_nth(nth: int, weekday: int, en: bool) -> str:
    if en:
        return f"the {_NTH_EN[nth]} {_WD_EN[weekday - 1]} of the month"
    fem = weekday in (3, 5, 6)
    nth_word = (_NTH_RU_F if fem else _NTH_RU)[nth]
    if fem:
        nth_word = {"первую": "первая", "вторую": "вторая", "третью": "третья", "четвёртую": "четвёртая",
                    "пятую": "пятая", "последнюю": "последняя"}[nth_word]
    elif weekday == 7:
        nth_word = {"первый": "первое", "второй": "второе", "третий": "третье", "четвёртый": "четвёртое",
                    "пятый": "пятое", "последний": "последнее"}[nth_word]
    return f"{nth_word} {_WD_RU[weekday - 1]} месяца"


def _country_text(code: str, en: bool) -> str:
    names = COUNTRIES.get(code)
    return (names[1] if en else names[0]) if names else code


def _place_text(cond: Dict[str, Any]) -> str:
    return cond.get("place") or f"{cond['lat']:.2f}, {cond['lon']:.2f}"


def condition_text(cond: Dict[str, Any], lang: str, *, one=None) -> str:
    en = lang == "en"
    kind = cond["type"]
    if kind == "group":
        glue = (" or " if en else " или ") if cond.get("match") == "any" else (" and " if en else " и ")
        return "(" + glue.join(one(item, lang) for item in cond.get("items") or [] if not item.get("off")) + ")"
    if kind == "calendar":
        mode, country = cond["mode"], _country_text(cond.get("country") or "", en)
        if mode == "workday":
            return f"a workday ({country})" if en else f"рабочий день ({country})"
        if mode == "weekend":
            return "a weekend" if en else "выходной (сб, вс)"
        if mode == "holiday":
            return f"a public holiday ({country})" if en else f"праздничный день ({country})"
        if mode == "day_off":
            return f"a day off — weekend or holiday ({country})" if en else f"нерабочий день — выходной или праздник ({country})"
        if mode == "short_day":
            return f"a shortened pre-holiday day ({country})" if en else f"предпраздничный (сокращённый) день ({country})"
        if mode == "first_workday":
            return f"the first workday of the month ({country})" if en else f"первый рабочий день месяца ({country})"
        if mode == "last_workday":
            return f"the last workday of the month ({country})" if en else f"последний рабочий день месяца ({country})"
        if mode == "first_workday_week":
            return f"the first workday of the week ({country})" if en else f"первый рабочий день недели ({country})"
        if mode == "last_workday_week":
            return f"the last workday of the week ({country})" if en else f"последний рабочий день недели ({country})"
        if mode == "nth_weekday":
            return ("it is " if en else "сегодня ") + _wd_nth(cond["nth"], cond["weekday"], en)
        if mode == "month_days":
            days = ", ".join(("last" if en else "последнее") if n == -1 else str(n) for n in _compact_days(cond["days"]))
            return f"day of the month: {days}" if en else f"число месяца: {days}"
        if mode == "months":
            names = _MON_EN if en else _MON_RU
            return ("in " if en else "месяц: ") + ", ".join(names[m - 1] for m in cond["months"])
        if mode == "week_parity":
            odd = cond["value"] == "odd"
            return ("an odd week" if odd else "an even week") if en else ("нечётная неделя" if odd else "чётная неделя")
        if mode == "date_range":
            return (f"between {cond['from']} and {cond['to']} each year" if en
                    else f"с {_ddmm(cond['from'])} по {_ddmm(cond['to'])} каждый год")
    if kind == "sun":
        off = cond.get("offset", 0)
        tail = ""
        if off:
            tail = (f" (±{abs(off)} min)" if en else f" (±{abs(off)} мин)")
        place = _place_text(cond)
        if cond["value"] == "day":
            return (f"it is daytime in {place}" if en else f"светло — {place}") + tail
        return (f"it is dark in {place}" if en else f"темно — {place}") + tail
    if kind == "weather":
        ru, eng, unit = _METRIC[cond["metric"]]
        place = _place_text(cond)
        if cond["metric"] in _NUMERIC_METRICS:
            sign = ">" if cond["op"] == "gt" else "<"
            unit = unit.replace(" мм", " mm").replace(" км/ч", " km/h") if en else unit
            return f"{eng if en else ru} {sign} {_fmt_num(cond['value'])}{unit} ({place})"
        return f"{eng if en else ru} ({place})"
    if kind == "wifi":
        name = cond.get("ssid")
        if cond["value"]:
            return (f"connected to Wi-Fi «{name}»" if en else f"подключён Wi-Fi «{name}»") if name else (
                "connected to Wi-Fi" if en else "подключён Wi-Fi")
        return (f"not on Wi-Fi «{name}»" if en else f"не подключён Wi-Fi «{name}»") if name else (
            "not on Wi-Fi" if en else "Wi-Fi не подключён")
    if kind == "last_result":
        who = ("the previous run" if en else "прошлый запуск") if not cond.get("task_id") else (
            "the last run of another task" if en else "последний запуск другой задачи")
        parts = []
        if cond["status"] == "ok":
            parts.append("succeeded" if en else "успешен")
        elif cond["status"] == "fail":
            parts.append("failed" if en else "с ошибкой")
        if cond.get("contains"):
            parts.append((f"contains «{cond['contains']}»" if en else f"содержит «{cond['contains']}»"))
        if cond.get("within_hours"):
            parts.append((f"within {cond['within_hours']} h" if en else f"за последние {cond['within_hours']} ч"))
        return who + " " + ", ".join(parts)
    return kind


def _compact_days(days: List[int]) -> List[Any]:
    return days if len(days) <= 6 else days[:5] + ["…"]


def _ddmm(mmdd: str) -> str:
    month, day = mmdd.split("-")
    return f"{day}.{month}"


def _fmt_num(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.1f}"


def trigger_text(trigger: Dict[str, Any], lang: str) -> str:
    en = lang == "en"
    if trigger["type"] == "sun":
        base = ("at sunrise" if trigger["event"] == "sunrise" else "at sunset") if en else (
            "на рассвете" if trigger["event"] == "sunrise" else "на закате")
        off = trigger.get("offset", 0)
        if off:
            when = (f"{abs(off)} min {'before' if off < 0 else 'after'} " if en
                    else f"за {abs(off)} мин до " if off < 0 else f"через {abs(off)} мин после ")
            base = when + (("sunrise" if trigger["event"] == "sunrise" else "sunset") if en else
                           ("рассвета" if trigger["event"] == "sunrise" else "заката"))
        return f"{base} ({_place_text(trigger)})"
    if trigger.get("mode") == "nth_weekday":
        if en:
            return f"monthly on the {_NTH_EN[trigger['nth']]} {_WD_EN[trigger['weekday'] - 1]} at {trigger['time']}"
        fem = trigger["weekday"] in (3, 5, 6)
        nth = (_NTH_RU_F if fem else _NTH_RU)[trigger["nth"]]
        if trigger["weekday"] == 7:
            nth = {"первый": "первое", "второй": "второе", "третий": "третье", "четвёртый": "четвёртое",
                   "пятый": "пятое", "последний": "последнее"}[nth]
        prep = "во" if nth.startswith("вт") else "в"
        return f"каждый месяц {prep} {nth} {_WD_RU_ACC[trigger['weekday'] - 1]} в {trigger['time']}"
    if trigger.get("mode") == "workday":
        n, country = trigger["nth"], _country_text(trigger.get("country") or "", en)
        if en:
            which = "the last workday" if n == -1 else f"workday #{n}"
            return f"monthly on {which} ({country}) at {trigger['time']}"
        which = "в последний рабочий день" if n == -1 else f"в {n}-й рабочий день"
        return f"каждый месяц {which} ({country}) в {trigger['time']}"
    return ""


def options_text(options: Dict[str, Any], lang: str) -> List[str]:
    en = lang == "en"
    out = []
    if options.get("quiet_from"):
        out.append((f"quiet hours {options['quiet_from']}–{options['quiet_to']}" if en
                    else f"тихие часы {options['quiet_from']}–{options['quiet_to']}"))
    if options.get("max_per_day"):
        out.append((f"at most {options['max_per_day']} a day" if en else f"не больше {options['max_per_day']} в день"))
    if options.get("retry_count"):
        n = options["retry_count"]
        times = "раз" if n % 10 not in (2, 3, 4) or n % 100 in (12, 13, 14) else "раза"
        out.append((f"retry {n}× on failure" if en else f"при ошибке повторить {n} {times}"))
    return out


def reason_text(cond: Dict[str, Any], lang: str) -> Optional[str]:
    en = lang == "en"
    if cond.get("type") == "quiet":
        until = datetime.fromtimestamp(cond["until"]).strftime("%H:%M")
        return f"Quiet hours — postponed until {until}" if en else f"Тихие часы — отложено до {until}"
    if cond.get("type") == "day_limit":
        return (f"Daily limit reached ({cond['max']})" if en else f"Дневной лимит запусков исчерпан ({cond['max']})")
    return None


# --------------------------------------------------------------------------- «Проверить сейчас»

def _detail(cond: Dict[str, Any], sample: Dict[str, Any], now: float, ctx: Dict[str, Any], lang: str) -> str:
    en = lang == "en"
    kind = cond["type"]
    if kind == "weather":
        data = (sample.get("ext") or {}).get("weather:" + weather_key(cond))
        if not isinstance(data, dict):
            return "no weather data (offline?)" if en else "нет данных о погоде (нет интернета?)"
        bits = []
        if data.get("temp") is not None:
            bits.append(f"{_fmt_num(data['temp'])}°C")
        if data.get("wind") is not None:
            bits.append(f"{'wind' if en else 'ветер'} {_fmt_num(data['wind'])} {'km/h' if en else 'км/ч'}")
        if data.get("precip_prob") is not None:
            bits.append(f"{'rain chance' if en else 'осадки'} {_fmt_num(data['precip_prob'])}%")
        if data.get("rain"):
            bits.append("rain" if en else "дождь")
        if data.get("snow"):
            bits.append("snow" if en else "снег")
        return ("now: " if en else "сейчас: ") + ", ".join(bits)
    if kind == "sun":
        d = datetime.fromtimestamp(now).date()
        rise, sset = sun_event(d, cond["lat"], cond["lon"], True), sun_event(d, cond["lat"], cond["lon"], False)
        if rise is None or sset is None:
            return "polar day / night" if en else "полярный день / ночь"
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M")   # noqa: E731
        return (f"sunrise {fmt(rise)}, sunset {fmt(sset)}" if en else f"восход {fmt(rise)}, закат {fmt(sset)}")
    if kind == "wifi":
        ssid = (sample.get("ext") or {}).get("wifi")
        if ssid is None:
            return "this computer cannot tell" if en else "компьютер не может определить"
        return (f"now: «{ssid}»" if en else f"сейчас: «{ssid}»") if ssid else ("now: not on Wi-Fi" if en else "сейчас: не по Wi-Fi")
    if kind == "calendar":
        d = datetime.fromtimestamp(now).date()
        if cond["mode"] in COUNTRY_MODES:
            country = cond.get("country") or "RU"
            kind = day_type(d, country)
            if kind is None:
                return "holiday calendar is loading" if en else "календарь праздников загружается"
            text = {"work": ("today is a workday", "сегодня рабочий день"),
                    "short": ("today is a shortened pre-holiday workday", "сегодня предпраздничный (сокращённый) день"),
                    "off": ("today is a day off", "сегодня выходной")}[kind][0 if en else 1]
            prod = production_calendar(d.year) if country == "RU" else None
            if prod is not None:
                text += (" — production calendar with moved days" if en else " — по производственному календарю с переносами")
            return text
        name = _WD_EN[d.isoweekday() - 1] if en else _WD_RU[d.isoweekday() - 1]
        return f"{'today' if en else 'сегодня'}: {d.strftime('%d.%m')}, {name}, {'week' if en else 'неделя'} {d.isocalendar()[1]}"
    if kind == "last_result":
        task = ctx.get("task") or {}
        if cond.get("task_id"):
            task = (ctx.get("tasks_by_id") or {}).get(cond["task_id"]) or {}
        finished = task.get("last_finished")
        if not isinstance(finished, (int, float)):
            return "no runs yet" if en else "запусков ещё не было"
        when = datetime.fromtimestamp(finished).strftime("%d.%m %H:%M")
        status = task.get("last_status")
        word = ("ok" if status == "ok" else "failed") if en else ("успешно" if status == "ok" else "ошибка")
        return f"{when} — {word}"
    return ""


def explain(rule: Dict[str, Any], sample: Dict[str, Any], now: float, lang: str,
            ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every condition now: ``{"rows": [{text, ok, detail, not, depth}], "ok": bool, "options": [...]}``."""
    from . import schedule_rules as rules
    ctx = ctx or {}
    rows: List[Dict[str, Any]] = []

    def visit(cond: Dict[str, Any], depth: int) -> Optional[bool]:
        if cond.get("off"):
            rows.append({"text": rules.condition_text(cond, lang), "ok": None, "off": True, "not": bool(cond.get("not")),
                         "detail": "switched off — not checked" if lang == "en" else "выключено — не проверяется",
                         "depth": depth, "group": cond["type"] == "group"})
            return None
        value = rules.check_condition(cond, sample, now, ctx)
        detail = ""
        if cond["type"] in CONDITION_TYPES and cond["type"] != "group":
            detail = _detail(cond, sample, now, ctx, lang)
        elif value is None:
            detail = "this computer cannot check it" if lang == "en" else "компьютер не может это проверить"
        rows.append({"text": rules.condition_text(cond, lang), "ok": value, "detail": detail,
                     "not": bool(cond.get("not")), "depth": depth, "group": cond["type"] == "group"})
        if cond["type"] == "group":
            for item in cond.get("items") or []:
                visit(item, depth + 1)
        return value

    for cond in rule.get("only_if") or []:
        visit(cond, 0)
    ok, failed = rules.conditions_hold(rule, sample, now, ctx=ctx)
    options = rule.get("options") or {}
    notes = []
    quiet = quiet_until(options, now)
    if quiet:
        notes.append(reason_text({"type": "quiet", "until": quiet}, lang))
    limit = options.get("max_per_day")
    if limit and day_runs(ctx.get("state") or {}, now) >= limit:
        notes.append(reason_text({"type": "day_limit", "max": limit}, lang))
    return {"rows": rows, "ok": bool(ok) and not notes, "notes": notes,
            "reason": rules.reason_text(failed, lang) if failed else ""}


# --------------------------------------------------------------------------- {{variables}} in a plain prompt

_VAR_RE = re.compile(r"\{\{\s*(date|time|datetime|weekday|month|year|run_count|last_result|last_status|task_name)\s*\}\}")


def render_vars(task: Dict[str, Any], now: Optional[float] = None, lang: str = "ru") -> Dict[str, Any]:
    """``{{date}}`` / ``{{time}}`` / ``{{weekday}}`` / ``{{last_result}}`` … in ``prompt`` (and ``reminder_text``)."""
    fields = [key for key in ("prompt", "reminder_text") if isinstance(task.get(key), str) and "{{" in task[key]]
    if not fields:
        return task
    moment = datetime.fromtimestamp(now if now is not None else time.time())
    en = lang == "en"
    values = {
        "date": moment.strftime("%Y-%m-%d"), "time": moment.strftime("%H:%M"),
        "datetime": moment.strftime("%Y-%m-%d %H:%M"),
        "weekday": (_WD_EN if en else _WD_RU)[moment.isoweekday() - 1],
        "month": (["January", "February", "March", "April", "May", "June", "July", "August", "September",
                   "October", "November", "December"] if en else _MON_RU)[moment.month - 1],
        "year": str(moment.year), "run_count": str(task.get("run_count") or 0),
        "last_result": str(task.get("last_text") or ""), "last_status": str(task.get("last_status") or ""),
        "task_name": str(task.get("name") or ""),
    }
    out = dict(task)
    for key in fields:
        out[key] = _VAR_RE.sub(lambda m: values[m.group(1)], out[key])
    return out


# --------------------------------------------------------------------------- HTTP (window)

def register_routes(app: Any) -> None:
    """``POST /api/plan/conds/check {task}`` — «Проверить сейчас»; ``GET /api/plan/conds/geo?q=`` — city search."""
    import asyncio
    from fastapi.responses import JSONResponse
    from . import schedule_rules as rules

    @app.post("/api/plan/conds/check")
    async def plan_conds_check(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        body = body if isinstance(body, dict) else {}
        lang = "en" if body.get("lang") == "en" else "ru"
        task = body.get("task") if isinstance(body.get("task"), dict) else {}
        try:
            rule = rules.normalize_rule(task.get("rule"))
        except rules.RuleError as exc:
            return JSONResponse({"error": exc.text(lang)}, 400)
        from .pgdata import load_schedules
        from .schedule_sampler import Sampler
        tasks = await asyncio.to_thread(load_schedules)
        by_id = {str(t.get("id")): t for t in tasks}
        saved = by_id.get(str(task.get("id") or "")) or {}
        needs = rules.needs(rule, str(task.get("kind") or "chat"))
        now = time.time()
        if needs.get("ext"):
            await asyncio.to_thread(prefetch, needs["ext"], now)
        _prev, sample = await asyncio.to_thread(Sampler().get, needs, now=now, fresh=True)
        sample = dict(sample or {})
        sample.setdefault("memuai_open", True)    # the window asks — it is open
        ctx = {"task": saved, "tasks_by_id": by_id, "state": saved.get("rule_state") or {}}
        return await asyncio.to_thread(explain, rule, sample, now, lang, ctx)

    @app.get("/api/plan/conds/geo")
    async def plan_conds_geo(q: str = "", lang: str = "ru"):
        q = q.strip()[:80]
        if len(q) < 2:
            return {"places": []}
        try:
            places = await asyncio.to_thread(geocode, q, "en" if lang == "en" else "ru")
        except Exception as exc:  # noqa: BLE001
            msg = "Place search is unavailable (no internet?)" if lang == "en" else "Поиск мест недоступен (нет интернета?)"
            log.debug("geocode: %s", exc)
            return JSONResponse({"error": msg, "places": []}, 502)
        return {"places": places}


def geocode(q: str, lang: str) -> List[Dict[str, Any]]:
    """Open-Meteo geocoding (no key). Patched in tests."""
    import httpx
    resp = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=8.0,
                     params={"name": q, "count": 6, "language": lang, "format": "json"})
    resp.raise_for_status()
    out = []
    for row in (resp.json().get("results") or [])[:6]:
        name = ", ".join(x for x in (row.get("name"), row.get("admin1"), row.get("country")) if x)
        out.append({"place": str(row.get("name") or "")[:80], "label": name[:120],
                    "lat": round(float(row["latitude"]), 4), "lon": round(float(row["longitude"]), 4),
                    "country": str(row.get("country_code") or "").upper()[:2]})
    return out
