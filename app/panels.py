# -*- coding: utf-8 -*-
"""Панели MemuAI (Раунд 21): маленькие карточки «на виду» — погода, курсы, новости.

Панель = декларативная спецификация из блоков (``spec``) + данные (``data``), которые
подставляются в неё через ``{{ключ}}``, + «как обновлять» (``source`` для встроенных
источников либо ``runbook`` для обновления моделью с веб-поиском) + ритм (``cadence``:
не чаще раза в час, только по времени) + места, где она закреплена (``placements``:
главный экран, боковая панель, верхняя панель в виде мини-чипа).

Хранение — ``memuai/panels.json`` текущего профиля (атомарная межпроцессная запись через
``storage.mutate_json``). Обновление идёт в процессе окна MemuAI: ``panels_loop`` раз в
минуту ищет панели, которым пора, и «захватывает» их записью ``refreshing_until``, поэтому
одна панель никогда не обновляется дважды одновременно.

Модель и поиск подставляет ``playground_api`` через :func:`set_context`; тесты подставляют
свои заглушки. Сеть для встроенных источников — :func:`_http_text` (через
``websearch.fetch_page`` с SSRF-защитой), в тестах она подменяется.
"""
from __future__ import annotations

import asyncio
import copy
import csv
import io
import json
import logging
import math
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from html import unescape as html_unescape
from urllib.parse import quote, urlencode, urlparse

log = logging.getLogger("memuai.panels")

SCHEMA_VERSION = 2
FILE_NAME = "panels.json"

from .panels_layout import ZONE_IDS as PLACEMENTS  # noqa: E402

SIZES = ("s", "m", "l", "w")
KINDS = ("weather", "currency", "crypto", "stock", "rss", "countdown", "sports", "web", "custom",
         "news", "system", "usage", "todo", "notes", "clock", "timers", "proxy", "calendar", "github", "module")
SOURCE_TYPES = ("open-meteo", "fx", "coingecko", "stock", "rss", "countdown", "ai", "feed", "system", "usage",
                "local", "timers", "proxy", "calendar", "github", "module")

LOCAL_KINDS = ("todo", "notes", "clock", "timers", "module")

LIVE_KINDS = ("system", "timers", "clock")

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 7 * 24 * 60
KIND_FLOOR_MINUTES = {"system": 1, "usage": 1, "crypto": 1, "stock": 1, "rss": 5, "currency": 15, "weather": 15,
                      "news": 15, "sports": 15, "web": 15, "custom": 15, "countdown": 60, "proxy": 1,
                      "calendar": 5, "github": 5, "todo": 60, "notes": 60, "clock": 60, "timers": 60,
                      "module": 60}

FLOOR_REASONS = {
    "system": ("Чаще минуты — включите «Живой режим»: значения обновляются раз в 1–5 с, пока карточка видна.",
               "For faster than a minute use Live mode: values update every 1–5 s while the card is visible."),
    "usage": ("Сервисы лимитов спрашиваются не чаще раза в 45 с на аккаунт, иначе отвечают 429.",
              "Limit services are asked at most every 45 s per account, otherwise they answer 429."),
    "crypto": ("CoinGecko без ключа банит за запросы чаще раза в минуту.", "CoinGecko without a key bans faster than once a minute."),
    "stock": ("Биржевые данные без ключа отдаются не чаще раза в минуту.", "Keyless market data is served at most once a minute."),
    "rss": ("Ленты обычно обновляются раз в 5–15 минут; чаще — лишняя нагрузка на сайт.",
            "Feeds usually change every 5–15 minutes; faster only loads the site."),
    "currency": ("Курсы ЦБ и ЕЦБ публикуются раз в день — чаще 15 минут смысла нет.",
                 "Central bank rates are published daily — faster than 15 minutes makes no sense."),
    "weather": ("Open-Meteo пересчитывает текущую погоду раз в 15 минут.", "Open-Meteo recomputes current weather every 15 minutes."),
    "news": ("Каждое обновление — десятки источников и работа модели.", "Each refresh reads dozens of sources and runs the model."),
    "sports": ("Каждое обновление — поиск и работа модели.", "Each refresh runs a search and the model."),
    "web": ("Каждое обновление — чтение страницы и работа модели.", "Each refresh reads the page and runs the model."),
    "custom": ("Каждое обновление — поиск и работа модели.", "Each refresh runs a search and the model."),
    "countdown": ("Отсчёт идёт в окне сам, обновлять его не нужно.", "The countdown ticks in the window itself."),
    "proxy": ("Статистика MemuAI Proxy сохраняется на диск раз в 20 секунд.", "MemuAI Proxy saves its statistics every 20 seconds."),
    "calendar": ("Google Calendar API ограничивает частые запросы.", "The Google Calendar API limits frequent requests."),
    "github": ("GitHub просит опрашивать уведомления не чаще раза в минуту; 5 минут бережёт лимит токена.",
               "GitHub asks to poll notifications at most once a minute; 5 minutes saves the token's rate limit."),
}
CADENCE_PRESETS = (1, 5, 15, 30, 60, 180, 360, 720, 1440)
DEFAULT_CADENCE = {"system": {"kind": "interval", "minutes": 5}, "usage": {"kind": "interval", "minutes": 5}}
MAX_PANELS = 40
MAX_DISMISSED = 12
MAX_BLOCKS = 24
MAX_HISTORY = 48
CLAIM_SECONDS = 300
LOOP_SECONDS = 20

MAX_MODEL_REFRESHES = 3
MODEL_SOURCES = ("ai", "feed")

USER_WALLS = ("needs_login", "needs_connector", "paywall", "source_gone", "needs_location")

TRANSIENT_WALLS = ("blocked:source-down", "blocked:proxy-offline", "blocked:model-down")
TRANSIENT_RETRY_SECONDS = 300.0

BLOCK_TYPES = ("title", "metric", "stats", "list", "progress", "sparkline", "image", "badge",
               "text", "link", "action", "row", "strip", "countdown", "gauge", "usage",
               "bars", "meters", "clock", "todo", "note", "timers", "module")
TONES = ("ok", "warn", "danger", "info", "neutral", "accent")
TRENDS = ("up", "down", "flat", "")
_BINDING = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\s*\}\}")
_ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_ID_RE = re.compile(r"^p_[a-z0-9]{6,24}$")

class PanelError(ValueError):
    """Ошибка запроса к панелям, понятная пользователю."""

class Wall(Exception):
    """Обновление упёрлось в препятствие: ``status`` — класс:причина (needs_login:site.ru …)."""

    def __init__(self, status: str, message: str = "", *, retry_after: Optional[float] = None) -> None:
        super().__init__(message or status)
        self.status = _wall_slug(status)
        self.message = str(message or "")[:400]
        self.retry_after = retry_after

def _wall_slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9._:-]", "-", str(value or "blocked:unknown").lower())[:80]
    if ":" not in text:
        text = "blocked:" + text
    kind, _, reason = text.partition(":")
    if kind not in USER_WALLS + ("blocked",):
        kind = "blocked"
    return f"{kind}:{reason or 'unknown'}"

class PanelContext:
    """Что панелям нужно от приложения: модель и веб. Всё асинхронное и подменяемое."""

    def __init__(self, *, complete: Optional[Callable[[str, List[Dict[str, Any]]], Awaitable[str]]] = None,
                 search: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
                 fetch: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
                 default_model: Optional[Callable[[], str]] = None) -> None:
        self.complete = complete
        self.search = search
        self.fetch = fetch
        self.default_model = default_model or (lambda: "")

_CONTEXT = PanelContext()
_TASKS: Dict[str, "asyncio.Task[Any]"] = {}
_RERUN: set = set()

def set_context(ctx: PanelContext) -> None:
    global _CONTEXT
    _CONTEXT = ctx

def context() -> PanelContext:
    return _CONTEXT

def _path():
    from .pgdata import MEMU_DIR, _profile_data_name
    try:
        name = _profile_data_name(FILE_NAME)
    except Exception:
        name = FILE_NAME
    return MEMU_DIR / name

def default_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "location": {"mode": "unset", "name": "", "lat": None, "lon": None, "country": "", "timezone": ""},
        "units": "auto",
        "locale": "",
        "lang": "ru",
        "model": "",
        "appbar_max": 4,

        "layout": _layout().default_layout(),
    }

def _layout():
    from . import panels_layout
    return panels_layout

def _clean_settings(raw: Any) -> Dict[str, Any]:
    out = default_settings()
    if not isinstance(raw, dict):
        return out
    out["enabled"] = raw.get("enabled", True) is not False
    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    mode = str(loc.get("mode") or "unset")
    out["location"] = {
        "mode": mode if mode in ("unset", "auto", "manual", "declined") else "unset",
        "name": str(loc.get("name") or "")[:120],
        "lat": _num(loc.get("lat"), -90, 90),
        "lon": _num(loc.get("lon"), -180, 180),
        "country": str(loc.get("country") or "")[:80],
        "timezone": str(loc.get("timezone") or "")[:64],
    }
    units = str(raw.get("units") or "auto")
    out["units"] = units if units in ("auto", "metric", "imperial") else "auto"
    out["locale"] = re.sub(r"[^A-Za-z0-9-]", "", str(raw.get("locale") or ""))[:24]
    out["lang"] = "en" if str(raw.get("lang") or "") == "en" else "ru"
    out["model"] = str(raw.get("model") or "")[:300]
    try:
        out["appbar_max"] = max(1, min(8, int(raw.get("appbar_max") or 4)))
    except (TypeError, ValueError):
        out["appbar_max"] = 4
    out["layout"] = _layout().clean_layout(raw.get("layout"))
    return out

def _num(value: Any, lo: float, hi: float) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < lo or number > hi:
        return None
    return number

def _new_id() -> str:
    return "p_" + uuid.uuid4().hex[:12]

def floor_minutes(kind: str) -> int:
    """Нижний предел ритма для типа панели (минуты)."""
    return int(KIND_FLOOR_MINUTES.get(kind, 15 if kind else MIN_INTERVAL_MINUTES))

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")

def _hhmm(value: Any) -> str:
    match = _HHMM.fullmatch(str(value or "").strip())
    if match and int(match.group(1)) < 24 and int(match.group(2)) < 60:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return ""

def _clean_days(value: Any) -> List[int]:
    """Дни недели 0 (пн) … 6 (вс); все семь или ни одного — «каждый день» (пустой список)."""
    days = sorted({int(d) for d in (value or []) if isinstance(d, (int, float)) and not isinstance(d, bool) and 0 <= d <= 6}) \
        if isinstance(value, list) else []
    return [] if len(days) in (0, 7) else days

def _clean_cadence(raw: Any, *, kind: str = "") -> Dict[str, Any]:
    """Ритм (Раунд 23 — со своими вариантами):

    * ``interval`` — каждые N минут (не чаще предела вида), по желанию только в дни ``days`` и в часы
      ``from``–``to`` (окно может переходить через полночь);
    * ``daily`` — раз в день в ``at`` (старая форма, остаётся как есть);
    * ``times`` — в несколько своих моментов ``at: [HH:MM…]`` по дням ``days``;
    * ``manual`` — только кнопкой.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = {"kind": "interval", "minutes": raw}
    if isinstance(raw, str):
        text = raw.strip().lower()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if match:
            raw = {"kind": "daily", "at": text}
        elif text in ("manual", "never", "once", "one-shot", "static"):
            raw = {"kind": "manual"}
        elif text in ("daily", "day"):
            raw = {"kind": "daily", "at": "08:00"}
        else:
            match = re.fullmatch(r"(\d{1,5})\s*(s|sec|с|сек|m|min|мин|h|ч|d|д)\w*", text)
            if match:
                unit = match.group(2)
                if unit in ("s", "sec", "с", "сек"):
                    raw = {"kind": "interval", "seconds": int(match.group(1))}
                else:
                    mult = {"m": 1, "min": 1, "мин": 1, "h": 60, "ч": 60, "d": 1440, "д": 1440}[unit]
                    raw = {"kind": "interval", "minutes": int(match.group(1)) * mult}
            else:
                raw = {"kind": "interval", "minutes": 60}
    if not isinstance(raw, dict):
        if kind == "countdown" or kind in LOCAL_KINDS:
            return {"kind": "manual"}
        return dict(DEFAULT_CADENCE.get(kind) or {"kind": "interval", "minutes": max(60, floor_minutes(kind))})
    mode = str(raw.get("kind") or raw.get("type") or "interval")
    if mode == "manual" or kind in LOCAL_KINDS:
        return {"kind": "manual"}
    if mode == "daily":
        return {"kind": "daily", "at": _hhmm(raw.get("at") or raw.get("daily_at") or "08:00") or "08:00"}
    if mode == "times":
        times = raw.get("at") if isinstance(raw.get("at"), list) else [raw.get("at")]
        clean = sorted({t for t in (_hhmm(v) for v in times[:12]) if t})
        days = _clean_days(raw.get("days"))
        if not clean:
            clean = ["08:00"]
        if len(clean) == 1 and not days:
            return {"kind": "daily", "at": clean[0]}
        out: Dict[str, Any] = {"kind": "times", "at": clean}
        if days:
            out["days"] = days
        return out
    try:
        if raw.get("seconds") not in (None, "") and not raw.get("minutes"):
            minutes = int(math.ceil(float(raw.get("seconds")) / 60.0))
        else:
            minutes = int(float(raw.get("minutes") or raw.get("interval_minutes") or 60))
    except (TypeError, ValueError):
        minutes = 60
    floor = max(MIN_INTERVAL_MINUTES, floor_minutes(kind) if kind else MIN_INTERVAL_MINUTES)
    out = {"kind": "interval", "minutes": max(floor, min(MAX_INTERVAL_MINUTES, minutes))}
    days = _clean_days(raw.get("days"))
    if days:
        out["days"] = days
    start, stop = _hhmm(raw.get("from")), _hhmm(raw.get("to"))
    if start and stop and start != stop:
        out["from"], out["to"] = start, stop
    return out

def _cadence_allows(cadence: Dict[str, Any], moment: datetime) -> bool:
    """Попадает ли момент в дни и часы интервального ритма."""
    days = cadence.get("days") or []
    start, stop = cadence.get("from"), cadence.get("to")
    if not start:
        return not days or moment.weekday() in days
    now_t = moment.strftime("%H:%M")
    if start < stop:
        return start <= now_t < stop and (not days or moment.weekday() in days)

    if now_t >= start:
        return not days or moment.weekday() in days
    if now_t < stop:
        return not days or (moment - timedelta(days=1)).weekday() in days
    return False

def next_refresh_at(cadence: Dict[str, Any], *, now: Optional[float] = None) -> Optional[float]:
    """Когда обновлять в следующий раз. Простые ритмы — тот же расчёт, что у Планирования (scheduler)."""
    from .scheduler import _next_run_for
    now = time.time() if now is None else float(now)
    cadence = _clean_cadence(cadence)
    if cadence["kind"] == "manual":
        return None
    if cadence["kind"] == "daily":
        return _next_run_for({"daily_at": cadence["at"]}, now=now)
    if cadence["kind"] == "times":
        days = cadence.get("days") or []
        today = datetime.fromtimestamp(now).date()
        for offset in range(0, 9):
            day = today + timedelta(days=offset)
            if days and day.weekday() not in days:
                continue
            for at in cadence["at"]:
                hour, minute = (int(x) for x in at.split(":"))
                stamp = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).timestamp()
                if stamp > now + 1:
                    return stamp
        return now + 86400
    candidate = _next_run_for({"interval_minutes": cadence["minutes"]}, now=now)
    if not cadence.get("days") and not cadence.get("from"):
        return candidate
    moment = datetime.fromtimestamp(candidate)
    if _cadence_allows(cadence, moment):
        return candidate
    start = cadence.get("from") or "00:00"
    hour, minute = (int(x) for x in start.split(":"))
    days = cadence.get("days") or []
    for offset in range(0, 9):
        day = moment.date() + timedelta(days=offset)
        if days and day.weekday() not in days:
            continue
        stamp = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if stamp > moment:
            return stamp.timestamp()
    return candidate

def _clean_url(value: Any, *, images: bool = False) -> str:
    text = str(value or "").strip()[:2000]
    if not text:
        return ""
    if images and re.match(r"^data:image/(png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$", text) and len(text) < 400_000:
        return text
    if _BINDING.fullmatch(text):
        return text
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return text

def _clean_text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _fmt_number(value)
    return str(value).replace("\x00", "")[:limit]

def _fmt_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return ("%.6f" % value).rstrip("0").rstrip(".")
    return str(value)

def _clean_items(value: Any, fields: Tuple[str, ...], *, limit: int = 20, url_fields: Tuple[str, ...] = ()) -> Any:
    if isinstance(value, str) and _BINDING.fullmatch(value.strip()):
        return value.strip()
    if not isinstance(value, list):
        return []
    out = []
    for row in value[:limit]:
        if isinstance(row, dict):
            item = {}
            for key in fields:
                if key in row and row[key] not in (None, ""):
                    item[key] = _clean_url(row[key]) if key in url_fields else _clean_text(row[key], 300)
            if item:
                out.append(item)
        elif isinstance(row, (str, int, float)) and not isinstance(row, bool):
            out.append({fields[0]: _clean_text(row, 300)})
    return out

def _clean_block(raw: Any, errors: List[str], *, depth: int = 0) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        errors.append("block is not an object")
        return None

    if not raw.get("type"):
        alias = raw.get("kind") or raw.get("block")
        if isinstance(alias, str) and alias.strip().lower() in BLOCK_TYPES:
            raw = {**raw, "type": alias}
        elif len(raw) == 1:
            (name, body), = raw.items()
            name = str(name).strip().lower()
            if name in BLOCK_TYPES and isinstance(body, dict):
                raw = {**body, "type": name}
            elif name in ("title", "text", "badge") and isinstance(body, str):
                raw = {"type": name, "text": body}
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in BLOCK_TYPES:
        errors.append(f"unknown block type {kind!r}")
        return None
    out: Dict[str, Any] = {"type": kind}
    text = lambda key, limit=400: _clean_text(raw.get(key), limit) if raw.get(key) not in (None, "") else ""  # noqa: E731

    def put(key: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            out[key] = value

    if kind == "title":
        put("text", text("text", 160)); put("subtitle", text("subtitle", 200))
        if not out.get("text"):
            errors.append("title needs text"); return None
    elif kind == "metric":
        put("value", text("value", 60)); put("unit", text("unit", 24)); put("label", text("label", 120))
        put("delta", text("delta", 60)); put("caption", text("caption", 200))
        trend = str(raw.get("trend") or "")
        put("trend", trend if (trend in TRENDS or _BINDING.fullmatch(trend)) else "")
        if "value" not in out:
            errors.append("metric needs value"); return None
    elif kind == "stats":
        put("items", _clean_items(raw.get("items"), ("label", "value", "unit", "icon", "optional"), limit=8))
        if isinstance(out.get("items"), list):
            for item in out["items"]:
                if "optional" in item:
                    item["optional"] = item["optional"] in ("true", "1", "True")
        if not out.get("items"):
            errors.append("stats needs items"); return None
    elif kind == "list":
        put("items", _clean_items(raw.get("items"), ("title", "subtitle", "value", "url", "badge", "icon"),
                                  limit=20, url_fields=("url",)))
        put("label", text("label", 120))
        try:
            put("max", max(1, min(20, int(raw.get("max") or 0)))) if raw.get("max") else None
        except (TypeError, ValueError):
            pass
        style = str(raw.get("style") or "")
        if style in ("feed", "compact"):
            out["style"] = style
        summary = str(raw.get("summary") or "")
        if summary in ("auto", "always", "never"):
            out["summary"] = summary
        if raw.get("meta") is False:
            out["meta"] = False
        if raw.get("desc"):
            out["desc"] = True
        put("empty", text("empty", 160))
        if "items" not in out:
            errors.append("list needs items"); return None
    elif kind == "strip":
        put("items", _clean_items(raw.get("items"), ("label", "value", "icon", "sub"), limit=12))
        put("label", text("label", 60))
        try:
            put("max", max(1, min(24, int(raw.get("max") or 0)))) if raw.get("max") else None
        except (TypeError, ValueError):
            pass
        if "items" not in out:
            errors.append("strip needs items"); return None
    elif kind == "progress":
        put("value", text("value", 40)); put("max", text("max", 40)); put("label", text("label", 120))
        put("caption", text("caption", 200))
        if "value" not in out:
            errors.append("progress needs value"); return None
    elif kind == "sparkline":
        points = raw.get("points")
        if isinstance(points, str) and _BINDING.fullmatch(points.strip()):
            put("points", points.strip())
        elif isinstance(points, list):
            nums = [float(p) for p in points[:MAX_HISTORY] if isinstance(p, (int, float)) and not isinstance(p, bool)
                    and math.isfinite(float(p))]
            put("points", nums)
        put("label", text("label", 120)); put("unit", text("unit", 24)); put("value", text("value", 60))
        put("caption", text("caption", 120))
        if "points" not in out:
            errors.append("sparkline needs points"); return None
    elif kind == "image":
        src = raw.get("src") or raw.get("image") or raw.get("image_url")
        link = raw.get("url")
        if not src and link:

            src, link = link, raw.get("link") or raw.get("href")
        put("src", _clean_url(src, images=True)); put("alt", text("alt", 200))
        put("caption", text("caption", 200)); put("url", _clean_url(link))
        if "src" not in out:
            errors.append("image needs an http(s) src"); return None
    elif kind == "badge":
        put("text", text("text", 60))
        tone = str(raw.get("tone") or "neutral")
        out["tone"] = tone if (tone in TONES or _BINDING.fullmatch(tone)) else "neutral"
        if "text" not in out:
            errors.append("badge needs text"); return None
    elif kind == "text":
        put("text", text("text", 600))
        if raw.get("muted"):
            out["muted"] = True
        if "text" not in out:
            errors.append("text needs text"); return None
    elif kind == "link":
        put("text", text("text", 120)); put("url", _clean_url(raw.get("url")))
        if "url" not in out:
            errors.append("link needs an http(s) url"); return None
        out.setdefault("text", out["url"])
    elif kind == "action":
        put("text", text("text", 80)); put("prompt", text("prompt", 1000))
        if "text" not in out or "prompt" not in out:
            errors.append("action needs text and prompt"); return None
    elif kind == "gauge":
        put("value", text("value", 40)); put("max", text("max", 40)); put("label", text("label", 60))
        put("unit", text("unit", 12)); put("caption", text("caption", 120))
        if raw.get("optional"):
            out["optional"] = True
        if "value" not in out:
            errors.append("gauge needs value"); return None
    elif kind == "usage":
        accounts = raw.get("accounts")
        if isinstance(accounts, str) and _BINDING.fullmatch(accounts.strip()):
            out["accounts"] = accounts.strip()
        else:
            errors.append("usage needs accounts bound to data"); return None
        windows = [w for w in (raw.get("windows") or []) if w in ("five_hour", "weekly", "other")] \
            if isinstance(raw.get("windows"), list) else []
        if windows and len(windows) < 3:
            out["windows"] = windows
        for flag in ("reset", "pace"):
            if raw.get(flag) is False:
                out[flag] = False
        if raw.get("sort") == "fullest":
            out["sort"] = "fullest"
    elif kind == "countdown":
        target = text("target", 64)
        put("target", target if (not target or _BINDING.search(target)) else _lenient_target(target))
        put("label", text("label", 120))
        if raw.get("style") in ("cells", "big", "progress"):
            out["style"] = raw["style"]
        if raw.get("units") in ("auto", "days", "dhm", "dhms"):
            out["units"] = raw["units"]
        if raw.get("after") == "up":
            out["after"] = "up"
        put("since", text("since", 64))
        if "target" not in out:
            errors.append("countdown needs target"); return None
    elif kind in ("bars", "meters"):
        items = raw.get("items")
        if isinstance(items, str) and _BINDING.fullmatch(items.strip()):
            out["items"] = items.strip()
        elif isinstance(items, list):
            out["items"] = _clean_items(items, ("label", "value", "pct", "caption"), limit=64)
        put("label", text("label", 80)); put("caption", text("caption", 120))
        if not out.get("items"):
            errors.append(f"{kind} needs items"); return None
    elif kind == "clock":
        zones = [z for z in (str(v)[:80] for v in (raw.get("zones") or [])[:8]) if re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){0,2}(?:\|[^<>]{0,40})?", z)] \
            if isinstance(raw.get("zones"), list) else []
        out["zones"] = zones
        out["style"] = "analog" if raw.get("style") == "analog" else "digital"
        out["h24"] = raw.get("h24") if raw.get("h24") in ("24", "12") else "auto"
        for flag in ("seconds", "date", "diff", "local"):
            out[flag] = bool(raw.get(flag, flag in ("date", "diff", "local")))
    elif kind == "module":

        for key in ("module", "panel"):
            value = str(raw.get(key) or "")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", value):
                errors.append("module block needs module and panel ids"); return None
            out[key] = value
    elif kind == "todo":
        for flag in ("quick_add",):
            out[flag] = raw.get(flag) is not False
    elif kind == "note":
        out["font"] = "mono" if raw.get("font") == "mono" else "text"
        out["text_size"] = raw.get("text_size") if raw.get("text_size") in ("s", "m", "l") else "m"
        out["checklist"] = raw.get("checklist") is not False
    elif kind == "timers":
        parts = [p for p in (raw.get("parts") or []) if p in ("pomodoro", "timers", "stopwatch")] \
            if isinstance(raw.get("parts"), list) else []
        out["parts"] = parts or ["pomodoro", "timers", "stopwatch"]
        out["controls"] = raw.get("controls") is not False
        out["stats"] = raw.get("stats") is not False
    elif kind == "row":
        if depth >= 1:
            errors.append("row cannot be nested"); return None
        children = []
        for child in (raw.get("blocks") or [])[:4] if isinstance(raw.get("blocks"), list) else []:
            block = _clean_block(child, errors, depth=depth + 1)
            if block:
                children.append(block)
        if not children:
            errors.append("row needs blocks"); return None
        out["blocks"] = children
    icon = raw.get("icon")
    if kind in ("title", "metric", "badge", "text", "gauge") and isinstance(icon, str) and icon:
        if _ICON_RE.match(icon) or _BINDING.fullmatch(icon.strip()):
            out["icon"] = icon.strip()
    min_size = str(raw.get("min_size") or "")
    if min_size in ("s", "m", "l"):
        out["min_size"] = min_size
    max_size = str(raw.get("max_size") or "")
    if max_size in ("s", "m"):
        out["max_size"] = max_size
    return out

def validate_spec(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Проверяет дерево блоков: только известные типы, http(s)-ссылки, ограничения длины.

    Возвращает очищенную спецификацию и список замечаний. Пустой ``blocks`` — ошибка.
    """
    errors: List[str] = []
    if isinstance(raw, list):
        raw = {"blocks": raw}
    if not isinstance(raw, dict):
        return {"blocks": []}, ["spec must be an object with blocks"]
    blocks = []
    for item in (raw.get("blocks") or [])[:MAX_BLOCKS] if isinstance(raw.get("blocks"), list) else []:
        block = _clean_block(item, errors)
        if block:
            blocks.append(block)
    if isinstance(raw.get("blocks"), list) and len(raw["blocks"]) > MAX_BLOCKS:
        errors.append(f"at most {MAX_BLOCKS} blocks")
    if not blocks:
        errors.append("spec has no valid blocks")
    out: Dict[str, Any] = {"blocks": blocks}
    mini = raw.get("mini")
    if isinstance(mini, dict):
        clean = {}
        for key, limit in (("value", 40), ("unit", 16), ("label", 40), ("icon", 40)):
            if mini.get(key) not in (None, ""):
                clean[key] = _clean_text(mini[key], limit)
        if clean.get("icon") and not (_ICON_RE.match(clean["icon"]) or _BINDING.fullmatch(clean["icon"])):
            clean.pop("icon")
        if clean.get("value"):
            out["mini"] = clean
    return out, errors

def spec_bindings(spec: Any) -> List[str]:
    """Корневые ключи данных, на которые ссылается спецификация (``{{temp}}`` → ``temp``)."""
    found: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for match in _BINDING.finditer(value):
                if match.group(1) not in found:
                    found.append(match.group(1))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(spec)
    return found

def clean_values(values: Any, allowed: List[str]) -> Dict[str, Any]:
    """Значения от модели или пользователя: только привязанные ключи, ограниченные размеры."""
    out: Dict[str, Any] = {}
    if not isinstance(values, dict):
        return out

    def clean(value: Any, depth: int = 0) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value if math.isfinite(float(value)) else None
        if isinstance(value, str):
            return value[:600]
        if depth >= 2:
            return _clean_text(value, 200)
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value[:MAX_HISTORY]]
        if isinstance(value, dict):
            return {str(k)[:40]: clean(v, depth + 1) for k, v in list(value.items())[:12]}
        return _clean_text(value, 200)

    for key in allowed:
        if key in values:
            out[key] = clean(values[key])
    return out

def _clean_sources(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, str):
            row = {"url": row}
        if not isinstance(row, dict):
            continue
        url = _clean_url(row.get("url"))
        if not url or _BINDING.fullmatch(url):
            continue
        name = _clean_text(row.get("name") or row.get("title") or urlparse(url).hostname or url, 80)
        if not any(item["url"] == url for item in out):
            out.append({"name": name, "url": url})
        if len(out) >= 6:
            break
    return out

def _clean_runbook(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else ({"prompt": raw} if isinstance(raw, str) else {})
    queries = [str(q).strip()[:200] for q in (raw.get("queries") or []) if str(q).strip()][:3] \
        if isinstance(raw.get("queries"), list) else []
    urls = [u for u in (_clean_url(item) for item in (raw.get("urls") or [])) if u][:3] \
        if isinstance(raw.get("urls"), list) else []
    return {"prompt": str(raw.get("prompt") or "")[:2000], "queries": queries, "urls": urls,
            "technique": str(raw.get("technique") or "")[:1000]}

def _clean_wall(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or not raw.get("status"):
        return None
    return {"status": _wall_slug(raw.get("status")), "message": str(raw.get("message") or "")[:400],
            "at": _num(raw.get("at"), 0, 1e11) or time.time()}

def clean_panel(raw: Any) -> Optional[Dict[str, Any]]:
    """Нормализует одну запись панели (миграция старых и повреждённых записей)."""
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "")
    if not _ID_RE.match(pid):
        pid = _new_id()
    kind = str(raw.get("kind") or "custom")
    kind = kind if kind in KINDS else "custom"
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    stype = str(source.get("type") or ("ai" if kind in ("sports", "web", "custom") else KIND_SOURCE.get(kind, "")))
    if stype not in SOURCE_TYPES:
        stype = "ai"
    params = _clean_params(source.get("params"), stype)
    spec, _errors = validate_spec(raw.get("spec") or {"blocks": []})
    placements = [p for p in (raw.get("placements") or []) if p in PLACEMENTS] \
        if isinstance(raw.get("placements"), list) else []
    placements = list(dict.fromkeys(placements))
    order_raw = raw.get("order") if isinstance(raw.get("order"), dict) else {}
    order = {}
    for place in PLACEMENTS:
        try:
            order[place] = int(order_raw.get(place, 1000))
        except (TypeError, ValueError):
            order[place] = 1000
    size = str(raw.get("size") or "m")
    status = str(raw.get("status") or "live")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    try:
        data = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        data = {}
    return {
        "id": pid,
        "kind": kind,
        "title": _clean_text(raw.get("title") or "Панель", 80),
        "subject": _clean_text(raw.get("subject"), 600),
        "rhythm": _clean_text(raw.get("rhythm"), 200),
        "use": _clean_text(raw.get("use"), 300),
        "builtin": bool(raw.get("builtin")),
        "title_custom": bool(raw.get("title_custom")),
        "source": {"type": stype, "params": params},
        "spec": spec,
        "data": data,
        "history": _clean_history(raw.get("history")),
        "sources": _clean_sources(raw.get("sources")),
        "runbook": _clean_runbook(raw.get("runbook")),
        "cadence": _clean_cadence(raw.get("cadence"), kind=kind),
        "placements": placements,
        "order": order,
        "size": size if size in SIZES else "m",
        "status": status if status in ("building", "live", "failed", "setup") else "live",
        "wall": _clean_wall(raw.get("wall")),
        "error": _clean_text(raw.get("error"), 400),
        "failures": max(0, min(50, int(raw.get("failures") or 0))) if str(raw.get("failures") or "0").isdigit() else 0,
        "last_refresh": _num(raw.get("last_refresh"), 0, 1e11),
        "next_refresh": _num(raw.get("next_refresh"), 0, 1e11),
        "refreshing_until": _num(raw.get("refreshing_until"), 0, 1e11),
        "model": _clean_text(raw.get("model"), 300),

        "ai_model": _clean_text(raw.get("ai_model"), 300),
        "options": _options().clean_options(kind, raw.get("options")),
        "created": _num(raw.get("created"), 0, 1e11) or time.time(),
        "updated": _num(raw.get("updated"), 0, 1e11) or time.time(),
        "dismissed_at": _num(raw.get("dismissed_at"), 0, 1e11),

        "recompose": bool(raw.get("recompose")),
        "feed": _curate().clean_state(raw.get("feed")) if stype == "feed" else None,
    }

def _curate():
    from . import panels_curate
    return panels_curate

def _options():
    from . import panels_options
    return panels_options

def _local():
    from . import panels_local
    return panels_local

KIND_SOURCE = {"news": "feed", "system": "system", "usage": "usage", "todo": "local", "notes": "local",
               "clock": "local", "timers": "timers", "proxy": "proxy", "calendar": "calendar", "github": "github",
               "module": "module"}

def _clean_params(raw: Any, stype: str) -> Dict[str, Any]:
    """Параметры источника: плоские значения; у «Использования» — ещё список аккаунтов."""
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for key, value in list(raw.items())[:16]:
        key = str(key)[:40]
        if key == "accounts" and stype == "usage":
            from .panels_usage import clean_accounts
            out[key] = clean_accounts(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, dict)):
            continue
        else:
            out[key] = str(value)[:500]
    return out

def _clean_history(raw: Any) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    if not isinstance(raw, dict):
        return out
    for key, values in list(raw.items())[:8]:
        if isinstance(values, list):
            nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v))]
            out[str(key)[:40]] = nums[-MAX_HISTORY:]
    return out

def _normalize_document(raw: Any) -> Dict[str, Any]:
    """Документ panels.json любой старой формы → текущая схема."""
    if isinstance(raw, list):
        raw = {"panels": raw}
    if not isinstance(raw, dict):
        raw = {}
    panels, seen = [], set()
    try:
        old_version = int(raw.get("version") or 0)
    except (TypeError, ValueError):
        old_version = 0
    for row in raw.get("panels") or [] if isinstance(raw.get("panels"), list) else []:
        if old_version < 2 and isinstance(row, dict) and row.get("size") == "l":
            row = {**row, "size": "w"}
        panel = clean_panel(row)
        if panel and panel["id"] not in seen:
            seen.add(panel["id"])
            panels.append(panel)
    return {"version": SCHEMA_VERSION, "seeded": bool(raw.get("seeded")),
            "settings": _clean_settings(raw.get("settings")), "panels": panels[:MAX_PANELS + MAX_DISMISSED]}

def mutate(mutator: Callable[[Dict[str, Any]], Any]) -> Tuple[Dict[str, Any], Any]:
    """Одна атомарная транзакция над panels.json. Возвращает (документ, результат mutator)."""
    from .storage import mutate_json
    box: Dict[str, Any] = {}

    def update(raw: Any):
        doc = _normalize_document(raw)
        _seed(doc)
        box["result"] = mutator(doc)
        return True, doc

    doc = mutate_json(_path(), {}, update, backup=False)
    return doc, box.get("result")

def load() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    doc = _normalize_document(load_json_with_recovery(_path(), {}))
    if not doc["seeded"]:
        doc, _ = mutate(lambda d: None)
    return doc

def _seed(doc: Dict[str, Any]) -> None:
    """Первый запуск: встроенная панель «Погода» на главном экране и мини-чипом сверху."""
    if doc.get("seeded"):
        return
    doc["seeded"] = True
    if any(p["kind"] == "weather" and p["builtin"] for p in doc["panels"]):
        return
    if not doc["panels"] and doc["settings"]["lang"] == "ru":

        try:
            from .pgdata import load_preferences
            if load_preferences().get("language") == "en":
                doc["settings"]["lang"] = "en"
        except Exception:
            pass
    panel = make_template_panel("weather", {}, doc["settings"])
    panel["builtin"] = True
    panel["placements"] = ["home", "appbar"]
    panel["order"] = {"home": 0, "sidebar": 0, "appbar": 0}
    doc["panels"].insert(0, panel)

def _find(doc: Dict[str, Any], pid: str) -> Dict[str, Any]:
    for panel in doc["panels"]:
        if panel["id"] == pid:
            return panel
    raise PanelError("Панель не найдена")

def active(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in doc["panels"] if not p.get("dismissed_at")]

def dismissed(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [p for p in doc["panels"] if p.get("dismissed_at")]
    return sorted(rows, key=lambda p: -(p.get("dismissed_at") or 0))

def _lang(settings: Dict[str, Any]) -> str:
    return "en" if settings.get("lang") == "en" else "ru"

def _tr(settings: Dict[str, Any], ru: str, en: str) -> str:
    return en if _lang(settings) == "en" else ru

def _location_wall(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "needs_location:weather", "at": time.time(), "message": _tr(
        settings, "Укажите город или разрешите определить местоположение", "Set a city or allow location lookup")}

def units_for(settings: Dict[str, Any]) -> str:
    units = settings.get("units") or "auto"
    if units in ("metric", "imperial"):
        return units
    region = str(settings.get("locale") or "").split("-")[-1].upper()
    return "imperial" if region in ("US", "LR", "MM") else "metric"

CURRENCY_SIGNS = {"RUB": "₽", "USD": "$", "EUR": "€", "GBP": "£", "CNY": "¥", "JPY": "¥", "KZT": "₸",
                  "UAH": "₴", "TRY": "₺", "INR": "₹", "BYN": "Br", "CHF": "₣"}

TEMPLATES = [
    {"kind": "weather", "source": "open-meteo", "cadence": {"kind": "interval", "minutes": 60},
     "fields": [{"key": "city", "label_ru": "Город (пусто — ваше местоположение)", "label_en": "City (empty — your location)"}]},
    {"kind": "currency", "source": "fx", "cadence": {"kind": "interval", "minutes": 180},
     "fields": [{"key": "base", "default": "USD"}, {"key": "quote", "default": "RUB"}]},
    {"kind": "crypto", "source": "coingecko", "cadence": {"kind": "interval", "minutes": 60},
     "fields": [{"key": "coin", "default": "bitcoin"}, {"key": "vs", "default": "usd"}]},
    {"kind": "stock", "source": "stock", "cadence": {"kind": "interval", "minutes": 60},
     "fields": [{"key": "symbol", "default": "SBER"}, {"key": "exchange", "default": "moex"}]},
    {"kind": "rss", "source": "rss", "cadence": {"kind": "interval", "minutes": 120},
     "fields": [{"key": "url", "default": ""}, {"key": "title", "default": ""}]},
    {"kind": "countdown", "source": "countdown", "cadence": {"kind": "manual"},
     "fields": [{"key": "title", "default": ""}, {"key": "target", "default": ""}]},
    {"kind": "sports", "source": "ai", "cadence": {"kind": "interval", "minutes": 120},
     "fields": [{"key": "team", "default": ""}]},
    {"kind": "web", "source": "ai", "cadence": {"kind": "interval", "minutes": 360},
     "fields": [{"key": "url", "default": ""}, {"key": "what", "default": ""}]},
    {"kind": "custom", "source": "ai", "cadence": {"kind": "daily", "at": "08:00"},
     "fields": [{"key": "brief", "default": ""}]},
    {"kind": "news", "source": "feed", "cadence": {"kind": "daily", "at": "08:00"},
     "fields": [{"key": "topic", "default": "ai"}, {"key": "brief", "default": ""}, {"key": "count", "default": 5}]},
    {"kind": "system", "source": "system", "cadence": {"kind": "interval", "minutes": 5}, "fields": []},
    {"kind": "usage", "source": "usage", "cadence": {"kind": "interval", "minutes": 5},
     "fields": [{"key": "accounts", "default": []}]},

    {"kind": "todo", "source": "local", "cadence": {"kind": "manual"}, "fields": []},
    {"kind": "notes", "source": "local", "cadence": {"kind": "manual"}, "fields": []},
    {"kind": "clock", "source": "local", "cadence": {"kind": "manual"}, "fields": []},
    {"kind": "timers", "source": "timers", "cadence": {"kind": "manual"}, "fields": []},
    {"kind": "proxy", "source": "proxy", "cadence": {"kind": "interval", "minutes": 5}, "fields": []},
    {"kind": "calendar", "source": "calendar", "cadence": {"kind": "interval", "minutes": 30}, "fields": []},
    {"kind": "github", "source": "github", "cadence": {"kind": "interval", "minutes": 15}, "fields": []},

    {"kind": "module", "source": "module", "cadence": {"kind": "manual"}, "fields": []},
]

NEWS_TOPICS = {"ai": ("Новости ИИ", "AI news"), "tech": ("Технологии", "Tech news"), "science": ("Наука", "Science"),
               "crypto": ("Крипто", "Crypto"), "security": ("Кибербезопасность", "Security"), "space": ("Космос", "Space"),
               "gaming": ("Игры", "Gaming")}

def _template(kind: str) -> Dict[str, Any]:
    for row in TEMPLATES:
        if row["kind"] == kind:
            return row
    raise PanelError("Неизвестный шаблон панели")

def _system_blocks(options: Dict[str, Any], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Блоки «Производительности ПК» из настройки «что показывать» (Раунд 23)."""
    o = _options()
    t = lambda ru, en: _tr(settings, ru, en)  # noqa: E731
    metrics = list(options.get("metrics") or o.SYSTEM_DEFAULT)
    f = "_f" if _temp_unit(options, settings) == "f" else ""
    bits = "_bits" if options.get("net_units") == "bits" else ""
    PCT = {
        "cpu": ("{{cpu}}", "CPU", "{{cores_label}}", "cpu"),
        "ram": ("{{ram}}", t("Память", "RAM"), "{{ram_caption}}", "memory"),
        "swap": ("{{swap}}", t("Подкачка", "Swap"), "{{swap_caption}}", "memory"),
        "gpu": ("{{gpu}}", "GPU", "{{gpu_caption}}", "gpu"),
        "vram": ("{{vram}}", t("Видеопамять", "VRAM"), "{{vram_caption}}", "gpu"),
        "battery": ("{{battery}}", t("Батарея", "Battery"), "{{battery_caption}}", "battery"),
        "disks": ("{{disk}}", "{{disk_label}}", "{{disk_caption}}", "disk"),
    }
    rings: List[Dict[str, Any]] = []
    bars: List[Dict[str, Any]] = []
    charts: List[Dict[str, Any]] = []
    stats: List[Dict[str, Any]] = []
    lists: List[Dict[str, Any]] = []
    cores: List[Dict[str, Any]] = []
    for metric in metrics:
        view = o.system_view(options, metric)
        if metric == "cores":
            cores.append({"type": "bars", "items": "{{cores}}", "label": t("Ядра", "Cores"), "caption": "{{cores_label}}",
                          "min_size": "m"})
        elif metric == "disks" and options.get("disk_mode") == "all":
            if view == "number":
                lists.append({"type": "list", "items": "{{disks_rows}}", "label": t("Диски", "Disks"), "max": 8,
                              "style": "compact", "min_size": "m"})
            else:
                bars.append({"type": "meters", "items": "{{disks_list}}", "label": t("Диски", "Disks")})
        elif metric in PCT:
            value, label, caption, icon = PCT[metric]
            if view == "ring":
                rings.append({"type": "gauge", "value": value, "label": label, "caption": caption, "icon": icon,
                              **({"optional": True} if metric == "battery" else {})})
            elif view == "bar":
                bars.append({"type": "progress", "value": value, "max": "100", "label": label, "caption": caption})
            elif view == "chart":
                charts.append({"type": "sparkline", "points": "{{%s_hist}}" % metric, "label": label,
                               "value": "{{%s_text}}" % metric, "unit": "%", "min_size": "m"})
            elif metric == "battery":
                stats.append({"label": label, "value": "{{battery_text}}", "optional": True})
            else:
                stats.append({"label": label, "value": "{{%s_text}}" % metric, "unit": "%"})
        elif metric == "gpu_temp":
            if view == "bar":
                bars.append({"type": "progress", "value": "{{gpu_temp}}", "max": "100", "label": t("Температура GPU", "GPU temperature"),
                             "caption": "{{gpu_temp_text%s}}" % f})
            elif view == "chart":
                charts.append({"type": "sparkline", "points": "{{gpu_temp_hist}}", "label": t("Температура GPU", "GPU temperature"),
                               "value": "{{gpu_temp_text%s}}" % f, "min_size": "m"})
            else:
                stats.append({"label": t("GPU, темп.", "GPU temp"), "value": "{{gpu_temp_text%s}}" % f})
        elif metric == "disk_io":
            if view == "chart":
                charts.append({"type": "sparkline", "points": "{{disk_io_hist}}", "label": t("Диски: чтение + запись", "Disks: read + write"),
                               "value": "{{disk_rw}}", "min_size": "m"})
            else:
                stats.append({"label": t("Диск: чтение", "Disk read"), "value": "{{disk_read}}"})
                stats.append({"label": t("Диск: запись", "Disk write"), "value": "{{disk_write}}"})
        elif metric == "net":
            if options.get("net_mode") == "each":
                lists.append({"type": "list", "items": "{{nets%s}}" % bits, "label": t("Сеть", "Network"), "max": 8,
                              "style": "compact", "empty": t("Нет активных адаптеров", "No active adapters"), "min_size": "m"})
            elif view == "chart":
                charts.append({"type": "sparkline", "points": "{{net_down_hist}}", "label": t("Сеть ↓", "Net ↓"),
                               "value": "{{net_down%s}}" % bits, "min_size": "m"})
                charts.append({"type": "sparkline", "points": "{{net_up_hist}}", "label": t("Сеть ↑", "Net ↑"),
                               "value": "{{net_up%s}}" % bits, "min_size": "m"})
            else:
                stats.append({"label": t("Сеть ↓", "Net ↓"), "value": "{{net_down%s}}" % bits})
                stats.append({"label": t("Сеть ↑", "Net ↑"), "value": "{{net_up%s}}" % bits})
        elif metric == "temps":
            if view == "bar":
                bars.append({"type": "meters", "items": "{{temps_meters%s}}" % f, "label": t("Температуры", "Temperatures")})
            else:
                lists.append({"type": "list", "items": "{{temps_rows%s}}" % f, "label": t("Температуры", "Temperatures"),
                              "max": 8, "style": "compact", "empty": "{{temps_note}}", "min_size": "m"})
        elif metric == "fans":
            lists.append({"type": "list", "items": "{{fans_rows}}", "label": t("Вентиляторы", "Fans"), "max": 6, "style": "compact",
                          "empty": "{{fans_note}}", "min_size": "m"})
        elif metric == "uptime":
            stats.append({"label": t("Работает", "Uptime"), "value": "{{uptime_text}}"})
        elif metric == "procs":
            lists.append({"type": "list", "items": "{{procs}}", "label": t("Процессы", "Processes"),
                          "max": int(options.get("procs_count") or 5), "style": "compact", "empty": "{{procs_note}}",
                          "min_size": "m"})
    blocks: List[Dict[str, Any]] = []
    for index in range(0, len(rings), 4):
        chunk = rings[index:index + 4]
        for pos, ring in enumerate(chunk):
            if index + pos >= 2:
                ring["min_size"] = "m"
        blocks.append({"type": "row", "blocks": chunk})
    blocks += cores + [dict(b, min_size=b.get("min_size") or ("m" if i >= 2 else "s")) for i, b in enumerate(bars)]
    blocks += charts
    if stats:
        blocks.append({"type": "stats", "items": stats[:8], "min_size": "m" if (rings or bars) else "s"})
    blocks += lists
    return blocks

def _temp_unit(options: Dict[str, Any], settings: Dict[str, Any]) -> str:
    unit = str((options or {}).get("temp_unit") or (options or {}).get("units") or "auto")
    if unit in ("c", "metric"):
        return "c"
    if unit in ("f", "imperial"):
        return "f"
    return "f" if units_for(settings) == "imperial" else "c"

def _module_panel(params: Dict[str, Any]) -> Dict[str, Any]:
    """Раунд 38: вклад модуля для панели {module, panel} (только включённые модули, app/module_integration.py)."""
    from . import module_integration
    module_id, panel_id = str((params or {}).get("module") or ""), str((params or {}).get("panel") or "")
    row = module_integration.find(module_id)
    item = next((p for p in (row or {}).get("panels", []) if p["id"] == panel_id), None)
    if item is None:
        raise PanelError(f"Панель модуля недоступна: модуль «{module_id}» выключен или не добавляет «{panel_id}»")
    lang = "ru"
    try:
        from .pgdata import load_preferences
        lang = "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        pass
    return {"module": module_id, "panel": panel_id, "title": item["title_en"] if lang == "en" else item["title"],
            "icon": item.get("icon") or "", "size": item.get("size") or "m"}

def typed_spec(kind: str, params: Dict[str, Any], settings: Dict[str, Any],
               options: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """Название и спецификация встроенного типа на языке интерфейса — по настройке содержимого."""
    t = lambda ru, en: _tr(settings, ru, en)  # noqa: E731
    options = _options().clean_options(kind, options)
    if kind == "module":
        info = _module_panel(params)
        return info["title"], {"blocks": [{"type": "module", "module": info["module"], "panel": info["panel"]}],
                               "mini": {"icon": info["icon"] or "box", "value": "", "label": info["title"]}}
    if kind == "weather":
        fields = options.get("fields") or []
        stat_items = []
        names = {"hilo": (t("Макс / мин", "High / low"), "{{hi}}° / {{lo}}°", ""),
                 "wind": (t("Ветер", "Wind"), "{{wind}}", "{{w_unit}}"), "humidity": (t("Влажность", "Humidity"), "{{humidity}}", "%"),
                 "precip": (t("Осадки", "Rain"), "{{precip}}", "%"), "pressure": (t("Давление", "Pressure"), "{{pressure}}", "{{p_unit}}"),
                 "uv": (t("УФ-индекс", "UV index"), "{{uv}}", ""), "sun": (t("Восход / закат", "Sunrise / sunset"), "{{sunrise}} / {{sunset}}", ""),
                 "visibility": (t("Видимость", "Visibility"), "{{visibility}}", "{{v_unit}}")}
        for key in fields:
            if key in names:
                label, value, unit = names[key]
                stat_items.append({"label": label, "value": value, **({"unit": unit} if unit else {})})
        blocks: List[Dict[str, Any]] = [
            {"type": "title", "text": "{{place}}", "subtitle": "{{cond}}"},
            {"type": "metric", "value": "{{temp}}", "unit": "{{t_unit}}", "icon": "{{icon}}",
             **({"caption": t("Ощущается как {{feels}}{{t_unit}}", "Feels like {{feels}}{{t_unit}}")} if "feels" in fields else {})},
        ]
        if stat_items:
            blocks.append({"type": "stats", "min_size": "m", "items": stat_items[:8]})
        forecast = options.get("forecast") or "auto"
        if forecast in ("hourly", "both"):
            blocks.append({"type": "strip", "min_size": "m", "items": "{{hours}}", "label": t("По часам", "Hourly"),
                           "max": int(options.get("hours") or 12)})
        if forecast in ("daily", "both", "auto"):
            blocks.append({"type": "strip", "min_size": "l" if forecast == "auto" else "m", "items": "{{days}}",
                           **({"label": t("По дням", "Daily")} if forecast == "both" else {}),
                           "max": int(options.get("days") or 5)})
        return t("Погода", "Weather"), {"blocks": blocks,
                                         "mini": {"icon": "{{icon}}", "value": "{{temp}}", "unit": "°", "label": "{{place}}"}}
    if kind == "currency":
        base = str(params.get("base") or "USD").upper()
        quote_ = str(params.get("quote") or "RUB").upper()
        sign = CURRENCY_SIGNS.get(quote_, quote_)
        metric = {"type": "metric", "value": "{{rate}}", "unit": sign, "caption": "{{date_label}}"}
        if options.get("show_delta", True):
            metric.update({"delta": "{{delta}}", "trend": "{{trend}}"})
        blocks = [{"type": "title", "text": f"{base} → {quote_}", "subtitle": "{{source_label}}"}, metric]
        if options.get("period") != "off":
            blocks.append({"type": "sparkline", "points": "{{hist}}", "min_size": "m", "label": "{{period_label}}"})
        if options.get("extra"):
            blocks.append({"type": "list", "items": "{{rows}}", "style": "compact", "max": 6, "min_size": "m"})
        return f"{base} → {quote_}", {"blocks": blocks, "mini": {"icon": "coin", "value": "{{rate}}", "unit": sign, "label": base}}
    if kind == "crypto":
        fields = options.get("fields") or []
        metric = {"type": "metric", "value": "{{price}}", "unit": "{{vs_sign}}", "caption": t("за 24 часа", "over 24 hours")}
        if "change" in fields:
            metric.update({"delta": "{{delta}}", "trend": "{{trend}}"})
        else:
            metric.pop("caption")
        blocks = [{"type": "title", "text": "{{name}}", "subtitle": "{{symbol}}"}, metric]
        if options.get("period") != "off":
            blocks.append({"type": "sparkline", "points": "{{hist}}", "min_size": "s", "label": "{{period_label}}"})
        extra_stats = []
        if "mcap" in fields:
            extra_stats.append({"label": t("Капитализация", "Market cap"), "value": "{{mcap}}"})
        if "volume" in fields:
            extra_stats.append({"label": t("Объём 24 ч", "24h volume"), "value": "{{volume}}"})
        if extra_stats:
            blocks.append({"type": "stats", "items": extra_stats, "min_size": "m"})
        if options.get("extra"):
            blocks.append({"type": "list", "items": "{{rows}}", "style": "compact", "max": 6, "min_size": "m"})
        return "{{name}}", {"blocks": blocks,
                            "mini": {"icon": "btc", "value": "{{price_short}}", "unit": "{{vs_sign}}", "label": "{{symbol}}"}}
    if kind == "stock":
        blocks = [
            {"type": "title", "text": "{{name}}", "subtitle": "{{exchange_label}}"},
            {"type": "metric", "value": "{{price}}", "unit": "{{currency_sign}}", "delta": "{{delta}}",
             "trend": "{{trend}}", "caption": "{{time_label}}"},
        ]
        if options.get("period") != "off":
            blocks.append({"type": "sparkline", "points": "{{hist}}", "min_size": "m", "label": "{{period_label}}"})
        if options.get("extra"):
            blocks.append({"type": "list", "items": "{{rows}}", "style": "compact", "max": 6, "min_size": "m"})
        return "{{symbol}}", {"blocks": blocks,
                              "mini": {"icon": "chart", "value": "{{price}}", "unit": "{{currency_sign}}", "label": "{{symbol}}"}}
    if kind == "rss":
        listing = {"type": "list", "items": "{{items}}", "max": int(options.get("count") or 6)}
        if options.get("show_desc"):
            listing["desc"] = True
        return str(params.get("title") or "") or t("Новости", "Headlines"), {"blocks": [
            {"type": "title", "text": "{{feed_title}}", "subtitle": t("Свежие заголовки", "Latest headlines"), "icon": "news"},
            listing,
        ], "mini": {"icon": "news", "value": "{{count}}", "label": "{{feed_short}}"}}
    if kind == "system":
        title = t("Производительность ПК", "PC performance")
        blocks = [{"type": "title", "text": title, "subtitle": "{{cpu_name}}", "icon": "cpu"}] + _system_blocks(options, settings)
        first = (options.get("metrics") or ["cpu"])[0]
        mini_key = first if first in ("cpu", "ram", "gpu", "vram", "swap", "battery") else "cpu"
        mini_label = {"cpu": "CPU", "ram": t("ОЗУ", "RAM"), "gpu": "GPU", "vram": "VRAM", "swap": "Swap",
                      "battery": t("Бат.", "Bat.")}[mini_key]
        return title, {"blocks": blocks, "mini": {"icon": "cpu", "value": "{{%s_text}}" % mini_key, "unit": "%", "label": mini_label}}
    if kind == "usage":
        title = t("Лимиты ИИ", "AI limits")
        block = {"type": "usage", "accounts": "{{accounts}}", "windows": options.get("windows") or [],
                 "reset": bool(options.get("show_reset", True)), "pace": bool(options.get("show_pace", True)),
                 "sort": options.get("sort") or "manual"}
        return title, {"blocks": [
            {"type": "title", "text": title, "subtitle": "{{subtitle}}", "icon": "gauge"}, block,
        ], "mini": {"icon": "gauge", "value": "{{top_pct}}", "unit": "%", "label": "{{top_label}}"}}
    if kind == "news":
        topic = str(params.get("topic") or "")
        brief = str(params.get("brief") or "").strip()
        title = (t(*NEWS_TOPICS[topic]) if topic in NEWS_TOPICS and not brief else brief[:60]) or t("Новости", "News")
        listing = {"type": "list", "items": "{{items}}", "max": 10, "style": "feed", "summary": options.get("summary") or "auto"}
        if options.get("show_meta") is False:
            listing["meta"] = False
        return title, {"blocks": [
            {"type": "title", "text": title, "subtitle": "{{subtitle}}", "icon": "news"},
            listing,
            {"type": "text", "text": "{{note}}", "muted": True},
        ], "mini": {"icon": "news", "value": "{{count}}", "label": _clip_words(title, 18)}}
    if kind == "countdown":
        title = str(params.get("title") or "") or t("Обратный отсчёт", "Countdown")
        block = {"type": "countdown", "target": "{{target}}", "style": options.get("style") or "cells",
                 "units": options.get("units") or "auto"}
        if options.get("after") == "up":
            block["after"] = "up"
        if options.get("style") == "progress":
            block["since"] = "{{since}}"
        return title, {"blocks": [
            {"type": "title", "text": title, "subtitle": "{{target_label}}" if options.get("show_date", True) else "", "icon": "calendar"},
            block,
        ], "mini": {"icon": "calendar", "value": "{{days_left}}", "unit": t("дн.", "d"), "label": title}}
    if kind == "todo":
        title = t("Задачи", "To-Do")
        return title, {"blocks": [{"type": "title", "text": "{{panel_title}}", "subtitle": "{{todo_subtitle}}", "icon": "todo"},
                                  {"type": "todo", "quick_add": bool(options.get("quick_add", True))}],
                       "mini": {"icon": "check", "value": "{{open_count}}", "label": "{{panel_title}}"}}
    if kind == "notes":
        title = t("Заметка", "Note")
        return title, {"blocks": [{"type": "note", "font": options.get("font") or "text",
                                   "text_size": options.get("text_size") or "m",
                                   "checklist": bool(options.get("checklist", True))}],
                       "mini": {"icon": "edit", "value": "{{note_first}}", "label": "{{panel_title}}"}}
    if kind == "clock":
        title = t("Часы", "Clock")
        return title, {"blocks": [{"type": "clock", "zones": list(options.get("zones") or []), "style": options.get("style") or "digital",
                                   "h24": options.get("h24") or "auto", "seconds": bool(options.get("seconds")),
                                   "date": bool(options.get("date", True)), "diff": bool(options.get("diff", True)),
                                   "local": bool(options.get("local", True))}],
                       "mini": {"icon": "clock", "value": "{{clock_now}}", "label": "{{clock_label}}"}}
    if kind == "timers":
        title = t("Помодоро и таймеры", "Pomodoro and timers")
        return title, {"blocks": [{"type": "title", "text": "{{panel_title}}", "subtitle": "{{timers_subtitle}}", "icon": "clock"},
                                  {"type": "timers", "parts": list(options.get("parts") or []),
                                   "controls": bool(options.get("controls", True)), "stats": bool(options.get("stats", True))}],
                       "mini": {"icon": "clock", "value": "{{timer_left}}", "label": "{{timer_label}}"}}
    if kind == "proxy":
        title = t("MemuAI Proxy", "MemuAI Proxy")
        fields = options.get("fields") or []
        stats_items = []
        if "requests" in fields:
            stats_items.append({"label": t("Запросы", "Requests"), "value": "{{requests}}"})
        if "success" in fields:
            stats_items.append({"label": t("Успешно", "Success"), "value": "{{success}}", "unit": "%"})
        if "tokens" in fields:
            stats_items.append({"label": t("Токены", "Tokens"), "value": "{{tokens}}"})
        if "latency" in fields:
            stats_items.append({"label": t("p50 / p95", "p50 / p95"), "value": "{{latency}}"})
        if "cost" in fields:
            stats_items.append({"label": t("Стоимость", "Cost"), "value": "{{cost}}"})
        blocks = [{"type": "title", "text": title, "subtitle": "{{period_label}}", "icon": "gauge"}]
        if stats_items:
            blocks.append({"type": "stats", "items": stats_items[:8]})
        if "chart" in fields:
            blocks.append({"type": "sparkline", "points": "{{series}}", "label": t("Запросы", "Requests"), "min_size": "m"})
        if "models" in fields:
            blocks.append({"type": "list", "items": "{{models}}", "label": t("Модели", "Models"), "max": 5, "style": "compact",
                           "min_size": "m"})
        blocks.append({"type": "text", "text": "{{note}}", "muted": True})
        return title, {"blocks": blocks, "mini": {"icon": "gauge", "value": "{{requests}}", "label": "Proxy"}}
    if kind == "calendar":
        title = t("Календарь", "Calendar")
        listing = {"type": "list", "items": "{{events}}", "max": int(options.get("count") or 8),
                   "empty": t("Событий нет", "No events")}
        return title, {"blocks": [{"type": "title", "text": "{{panel_title}}", "subtitle": "{{subtitle}}", "icon": "calendar"}, listing],
                       "mini": {"icon": "calendar", "value": "{{next_time}}", "label": "{{next_title}}"}}
    if kind == "github":
        title = {"notifications": "GitHub", "review": t("Ревью в GitHub", "GitHub reviews"),
                 "assigned": t("GitHub: мне", "GitHub: assigned"), "mine": t("Мои PR", "My PRs")}.get(options.get("view"), "GitHub")
        return title, {"blocks": [{"type": "title", "text": "{{panel_title}}", "subtitle": "{{subtitle}}", "icon": "bell"},
                                  {"type": "list", "items": "{{items}}", "max": int(options.get("count") or 8),
                                   "empty": t("Ничего нового", "Nothing new")}],
                       "mini": {"icon": "bell", "value": "{{count}}", "label": "GitHub"}}
    raise PanelError("Нет встроенной спецификации для этого типа")

def make_template_panel(kind: str, params: Dict[str, Any], settings: Dict[str, Any],
                        options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Новая панель из галереи. Для типов на модели — заготовка, которую дособерёт фоновая сборка."""
    tpl = _template(kind)
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    t = lambda ru, en: _tr(settings, ru, en)  # noqa: E731
    now = time.time()
    options = _options().clean_options(kind, options)
    panel: Dict[str, Any] = {
        "id": _new_id(), "kind": kind, "source": {"type": tpl["source"], "params": params},
        "cadence": dict(tpl["cadence"]), "placements": ["home"], "size": "m", "status": "building",
        "created": now, "updated": now, "data": {}, "sources": [], "runbook": {}, "options": options,
    }
    if kind == "module":
        title, spec = typed_spec(kind, params, settings, options)
        panel["source"]["params"] = {"module": spec["blocks"][0]["module"], "panel": spec["blocks"][0]["panel"]}
        panel.update(title=title, spec=spec, status="live", last_refresh=now, data={"local": True},
                     size=_module_panel(params)["size"])
    elif kind in LOCAL_KINDS or kind in ("proxy", "calendar", "github"):
        if kind == "todo" and not options.get("lists"):
            first = _local().ensure_default_list(t("Задачи", "Tasks"))
            options["lists"] = [first["id"]]
        panel["source"]["params"] = {}
        title, spec = typed_spec(kind, {}, settings, options)
        panel["title"], panel["spec"] = title, spec
        if kind in LOCAL_KINDS:
            panel["status"] = "live"
            panel["last_refresh"] = now
            panel["data"] = {"local": True}
    elif kind in ("system", "usage", "news"):
        panel["cadence"] = dict(tpl["cadence"])
        if kind == "usage":
            from .panels_usage import clean_accounts
            accounts = clean_accounts(params.get("accounts"))
            panel["source"]["params"] = {"accounts": accounts}
            if not accounts:
                panel["status"] = "setup"
                panel["wall"] = {"status": "needs_connector:usage", "message": t(
                    "Добавьте локальный аккаунт или выберите аккаунт MemuAI Proxy", "Add a local account or select a MemuAI Proxy account"), "at": now}
        if kind == "news":
            topic = str(params.get("topic") or "").strip().lower()
            brief = str(params.get("brief") or "").strip()
            if topic not in NEWS_TOPICS:
                topic = _curate().detect_topic(brief) if brief else ""
            if not topic and not brief:
                raise PanelError(t("Выберите тему или опишите её", "Pick a topic or describe it"))
            try:
                count = max(1, min(10, int(params.get("count") or _curate().wanted_count(brief))))
            except (TypeError, ValueError):
                count = 5
            panel["source"]["params"] = {"topic": topic, "brief": brief[:300], "count": count}
            panel["subject"] = brief or t(*NEWS_TOPICS[topic])
            panel["feed"] = {}
        title, spec = typed_spec(kind, panel["source"]["params"], settings, options)
        panel["title"], panel["spec"] = title, spec
    elif tpl["source"] != "ai":
        title, spec = typed_spec(kind, params, settings, options)
        if "{{" in title:
            title = str(params.get("symbol") or params.get("coin") or kind).replace("-", " ").title()[:40]
            if kind == "stock":
                title = title.upper()
        panel["title"], panel["spec"] = title, spec
        if kind == "countdown":
            target = _parse_target(params.get("target"))
            if not target:
                raise PanelError(t("Укажите дату и время события", "Set the date and time of the event"))
            panel["source"]["params"]["target"] = target
            panel["data"] = countdown_values(target, settings)
            panel["status"] = "live"
            panel["last_refresh"] = now
        if kind == "rss" and not _clean_url(params.get("url")):
            raise PanelError(t("Нужен адрес RSS- или Atom-ленты (http/https)", "An RSS or Atom feed URL (http/https) is required"))
        if kind == "weather" and not params.get("city") and settings["location"]["mode"] in ("unset", "declined"):
            panel["status"] = "setup"
            panel["wall"] = _location_wall(settings)
    else:
        if kind == "sports":
            team = str(params.get("team") or "").strip()
            if not team:
                raise PanelError(t("Укажите команду или турнир", "Name a team or a league"))
            panel["title"] = team[:60]
            panel["subject"] = t(f"Счёт и ближайший матч: {team}", f"Score and next game: {team}")
            panel["runbook"] = {"prompt": t(
                f"Найди последний сыгранный матч и ближайший матч для «{team}»: соперник, счёт, дата, турнир.",
                f"Find the latest finished game and the next game for “{team}”: opponent, score, date, competition."),
                "queries": [f"{team} {t('счёт последний матч', 'latest score')}"]}
            panel["spec"] = {"blocks": [
                {"type": "title", "text": "{{headline}}", "subtitle": "{{competition}}", "icon": "trophy"},
                {"type": "metric", "value": "{{score}}", "label": "{{last_label}}"},
                {"type": "text", "text": "{{next_label}}", "muted": True},
            ], "mini": {"icon": "trophy", "value": "{{score}}", "label": _clip_words(team, 20)}}
        elif kind == "web":
            url = _clean_url(params.get("url"))
            what = str(params.get("what") or "").strip()
            if not url or not what:
                raise PanelError(t("Нужны адрес страницы и что с неё показывать", "A page URL and what to show from it are required"))
            panel["title"] = _clip_words(what[:1].upper() + what[1:], 60)
            panel["subject"] = what
            panel["runbook"] = {"prompt": t(f"Открой страницу и найди: {what}", f"Open the page and find: {what}"), "urls": [url]}
            panel["spec"] = {"blocks": [
                {"type": "title", "text": _clip_words(what[:1].upper() + what[1:], 80), "subtitle": urlparse(url).hostname or url, "icon": "globe"},
                {"type": "metric", "value": "{{value}}", "unit": "{{unit}}", "caption": "{{note}}"},
            ], "mini": {"icon": "globe", "value": "{{value}}", "unit": "{{unit}}", "label": _clip_words(what[:1].upper() + what[1:], 20)}}
        else:
            brief = str(params.get("brief") or params.get("subject") or "").strip()
            if not brief:
                raise PanelError(t("Опишите, что должна показывать панель", "Describe what the panel should show"))
            if _curate().is_feed_brief(brief):

                return make_template_panel("news", {"brief": brief, "topic": _curate().detect_topic(brief)}, settings)
            todo_like = re.search(r"(?i)\b(to-?do|todo|список дел|задач[иа]?|дела на|чек-?лист)\b", brief)
            if todo_like and len(brief) < 60:

                return make_template_panel("todo", {}, settings)
            panel["title"] = brief[:60]
            panel["subject"] = brief
            panel["spec"] = {"blocks": []}
    out = clean_panel(panel)
    assert out is not None
    if tpl["source"] == "ai" and not panel.get("spec", {}).get("blocks"):
        out["spec"] = {"blocks": []}
    return out

def _parse_target(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None or moment.utcoffset() == timedelta(0):

        moment = moment.astimezone()
    return moment.isoformat(timespec="minutes")

def _lenient_target(value: Any) -> str:
    """Дата блока «countdown» от модели (Round 31): ISO как есть, иначе «Fri 2027-01-01 12:00 AM MSK
    (UTC+03:00)», «01.01.2027 00:00», «2027-01-01» → ISO. Окно (``new Date``) не понимало такие
    строки и показывало «NaN». Не разобрали — пустая строка (блок отбрасывается)."""
    text = str(value or "").strip()
    iso = _parse_target(text)
    if iso:
        return iso
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = (int(g) for g in match.groups())
    else:
        match = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", text)
        if not match:
            return ""
        day, month, year = (int(g) for g in match.groups())
    rest = text[match.end():]
    hour = minute = 0
    clock = re.search(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?", rest)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        meridiem = (clock.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    tz = None
    offset = re.search(r"(?:UTC|GMT)\s*([+-])(\d{1,2})(?::?(\d{2}))?", rest)
    if offset:
        sign = 1 if offset.group(1) == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3) or 0)))
    try:
        moment = datetime(year, month, day, hour % 24, minute % 60, tzinfo=tz)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="minutes")

def countdown_values(target: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    moment = datetime.fromisoformat(target.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() == timedelta(0):
        moment = moment.astimezone()
    left = moment.timestamp() - time.time()
    lang = _lang(settings)

    same_year = moment.year == datetime.now(moment.tzinfo).year
    if lang == "ru":
        label = moment.strftime("%d.%m" if same_year else "%d.%m.%Y")
    else:
        label = moment.strftime("%b %d" if same_year else "%b %d, %Y")
    if (moment.hour, moment.minute) != (0, 0):
        label += moment.strftime(" %H:%M") if lang == "ru" else moment.strftime(", %H:%M")
    return {"target": target, "target_label": label, "days_left": max(0, int(left // 86400)) if left > 0 else 0}

async def _http_text(url: str, *, limit: int = 1_500_000) -> str:
    """GET публичного адреса (SSRF-защита и перенаправления — в websearch.fetch_page)."""
    from . import websearch
    page = await websearch.fetch_page(url, limit=limit, raw=True, timeout=15)
    return str(page.get("raw") or page.get("text") or "")

async def _http_json(url: str) -> Any:
    text = await _http_text(url)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Wall("blocked:bad-response", f"Источник вернул не JSON: {text[:120]}") from exc

def _record_hist(panel: Dict[str, Any], key: str, value: Optional[float]) -> List[float]:
    hist = panel.setdefault("history", {}).setdefault(key, [])
    if value is not None and math.isfinite(value):
        hist.append(float(value))
    del hist[:-MAX_HISTORY]
    return list(hist)

def _fmt_money(value: float, lang: str, digits: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    if lang == "ru":
        text = text.replace(",", "\u202f").replace(".", ",")
    return text

def _fmt_delta(value: float, lang: str, digits: int = 2, suffix: str = "") -> str:
    if value is None or not math.isfinite(value):
        return ""
    sign = "+" if value > 0 else ("−" if value < 0 else "±")
    return sign + _fmt_money(abs(value), lang, digits) + suffix

def _trend(value: Optional[float]) -> str:
    if value is None or abs(value) < 1e-12:
        return "flat"
    return "up" if value > 0 else "down"

_WMO = [
    ((0,), "clear", "Ясно", "Clear"),
    ((1,), "mostly-clear", "Преимущественно ясно", "Mostly clear"),
    ((2,), "partly", "Переменная облачность", "Partly cloudy"),
    ((3,), "cloudy", "Пасмурно", "Overcast"),
    ((45, 48), "fog", "Туман", "Fog"),
    ((51, 53, 55, 56, 57), "drizzle", "Морось", "Drizzle"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "rain", "Дождь", "Rain"),
    ((71, 73, 75, 77, 85, 86), "snow", "Снег", "Snow"),
    ((95, 96, 99), "storm", "Гроза", "Thunderstorm"),
]

def weather_condition(code: Any, is_day: bool, lang: str) -> Tuple[str, str]:
    try:
        number = int(code)
    except (TypeError, ValueError):
        number = -1
    for codes, icon, ru, en in _WMO:
        if number in codes:
            if icon in ("clear", "mostly-clear", "partly") and not is_day:
                icon = {"clear": "clear-night", "mostly-clear": "clear-night", "partly": "partly-night"}[icon]
            return icon, (en if lang == "en" else ru)
    return "cloudy", ("—")

async def resolve_location(settings: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Где показывать погоду: город панели → город из настроек → IP (только с согласия)."""
    city = str(params.get("city") or "").strip()
    if params.get("lat") is not None and params.get("lon") is not None:
        return {"name": str(params.get("place") or city or ""), "lat": float(params["lat"]), "lon": float(params["lon"])}
    if city:
        found = await geocode(city, _lang(settings))
        if not found:
            raise Wall("source_gone:geocoding", _tr(settings, f"Город «{city}» не найден", f"City «{city}» not found"))
        return found[0]
    loc = settings["location"]
    if loc["mode"] in ("manual", "auto") and loc.get("lat") is not None and loc.get("lon") is not None:
        return {"name": loc.get("name") or "", "lat": loc["lat"], "lon": loc["lon"]}
    if loc["mode"] == "auto":
        return await ip_location()
    raise Wall("needs_location:weather", _location_wall(settings)["message"])

async def geocode(query: str, lang: str = "ru", count: int = 5) -> List[Dict[str, Any]]:
    query = str(query or "").strip()[:100]
    if len(query) < 2:
        return []
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode(
        {"name": query, "count": max(1, min(10, count)), "language": "en" if lang == "en" else "ru", "format": "json"})
    data = await _http_json(url)
    out = []
    for row in (data or {}).get("results") or []:
        try:
            out.append({"name": str(row.get("name") or ""), "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                        "country": str(row.get("country") or ""), "admin": str(row.get("admin1") or ""),
                        "timezone": str(row.get("timezone") or "")})
        except (KeyError, TypeError, ValueError):
            continue
    return out

async def ip_location() -> Dict[str, Any]:
    """Город по IP-адресу (ipwho.is, без ключа). Вызывается только после согласия пользователя."""
    try:
        data = await _http_json("https://ipwho.is/")
        if data.get("success") is not False and data.get("latitude") is not None:
            return {"name": str(data.get("city") or ""), "lat": float(data["latitude"]), "lon": float(data["longitude"]),
                    "country": str(data.get("country") or ""), "timezone": str((data.get("timezone") or {}).get("id") or "")}
    except Wall:
        raise
    except Exception as exc:  # noqa: BLE001
        log.info("panels: ipwho.is failed: %s", exc)
    data = await _http_json("https://ipapi.co/json/")
    if data.get("latitude") is None:
        raise Wall("blocked:ip-location", "Не удалось определить местоположение по IP")
    return {"name": str(data.get("city") or ""), "lat": float(data["latitude"]), "lon": float(data["longitude"]),
            "country": str(data.get("country_name") or ""), "timezone": str(data.get("timezone") or "")}

async def fetch_weather(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    lang = _lang(settings)
    place = await resolve_location(settings, panel["source"]["params"])
    options = _options().clean_options("weather", panel.get("options"))
    unit_pref = options.get("units") or "auto"
    imperial = (unit_pref == "imperial") or (unit_pref == "auto" and units_for(settings) == "imperial")
    wind_unit = options.get("wind_unit") or "auto"
    if wind_unit == "auto":
        wind_unit = "mph" if imperial else ("ms" if lang == "ru" else "kmh")
    fields = options.get("fields") or []
    forecast = options.get("forecast") or "auto"
    days_n = int(options.get("days") or 5)
    hours_n = int(options.get("hours") or 12)
    want_hourly = forecast in ("hourly", "both") or "precip" in fields or "uv" in fields or "visibility" in fields
    current = ["temperature_2m", "apparent_temperature", "relative_humidity_2m", "weather_code", "wind_speed_10m", "is_day"]
    if "pressure" in fields:
        current.append("pressure_msl")
    daily = ["temperature_2m_max", "temperature_2m_min", "weather_code"]
    if "sun" in fields:
        daily += ["sunrise", "sunset"]
    if "precip" in fields:
        daily.append("precipitation_probability_max")
    query: Dict[str, Any] = {
        "latitude": round(place["lat"], 4), "longitude": round(place["lon"], 4),
        "current": ",".join(current), "daily": ",".join(daily),
        "forecast_days": max(days_n, 2), "timezone": "auto",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": wind_unit,
    }
    if want_hourly:
        query["hourly"] = "temperature_2m,weather_code,precipitation_probability,is_day,uv_index,visibility"
        query["forecast_hours"] = max(hours_n + 1, 3)
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(query)
    data = await _http_json(url)
    if data.get("error"):
        reason = str(data.get("reason") or "")[:200]
        if "limit" in reason.lower():
            raise Wall("blocked:rate-limit", _tr(settings, f"Open-Meteo временно ограничил запросы ({reason}) — повторю позже.",
                                                 f"Open-Meteo is rate-limiting requests ({reason}) — will retry later."))
        raise Wall("blocked:open-meteo", _tr(settings, f"Open-Meteo вернул ошибку: {reason or '—'}",
                                             f"Open-Meteo returned an error: {reason or '—'}"))
    cur = data.get("current") or {}
    if cur.get("temperature_2m") is None:
        raise Wall("blocked:bad-response", _tr(settings, "Open-Meteo не вернул текущую погоду",
                                               "Open-Meteo returned no current weather"))
    icon, cond = weather_condition(cur.get("weather_code"), bool(cur.get("is_day", 1)), lang)
    daily_data = data.get("daily") or {}
    days = []
    names_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    names_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for index, day in enumerate((daily_data.get("time") or [])[:days_n]):
        try:
            weekday = datetime.fromisoformat(day).weekday()
            hi = round(float(daily_data["temperature_2m_max"][index]))
            lo = round(float(daily_data["temperature_2m_min"][index]))
            day_icon, _ = weather_condition(daily_data["weather_code"][index], True, lang)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        label = (("Сегодня" if lang == "ru" else "Today") if index == 0 else (names_en if lang == "en" else names_ru)[weekday])
        days.append({"label": label, "value": f"{hi}°", "sub": f"{lo}°", "icon": day_icon})
    hours: List[Dict[str, Any]] = []
    hourly = data.get("hourly") or {}
    hour_now: Dict[str, Any] = {}
    for index, stamp in enumerate((hourly.get("time") or [])[: hours_n + 1]):
        try:
            moment = datetime.fromisoformat(stamp)
            temp_h = round(float(hourly["temperature_2m"][index]))
            is_day = bool((hourly.get("is_day") or [1] * (index + 1))[index])
            h_icon, _ = weather_condition(hourly["weather_code"][index], is_day, lang)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        prob = (hourly.get("precipitation_probability") or [None] * (index + 1))[index]
        if index == 0:
            hour_now = {"precip": prob, "uv": (hourly.get("uv_index") or [None])[0],
                        "visibility": (hourly.get("visibility") or [None])[0]}
        hours.append({"label": ("Сейчас" if lang == "ru" else "Now") if index == 0 else moment.strftime("%H:%M"),
                      "value": f"{temp_h}°", "icon": h_icon,
                      **({"sub": f"{int(prob)}%"} if isinstance(prob, (int, float)) and prob >= 10 else {})})
    hours = hours[:hours_n]
    temp = round(float(cur["temperature_2m"]))
    _record_hist(panel, "temp", float(cur["temperature_2m"]))
    wind = cur.get("wind_speed_10m")
    w_names = {"ms": ("м/с", "m/s"), "kmh": ("км/ч", "km/h"), "mph": ("mph", "mph"), "kn": ("уз", "kn")}
    values: Dict[str, Any] = {
        "place": place.get("name") or ("Здесь" if lang == "ru" else "Here"),
        "temp": temp, "t_unit": "°F" if imperial else "°C",
        "feels": round(float(cur.get("apparent_temperature", temp))),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": round(float(wind)) if isinstance(wind, (int, float)) else "—",
        "w_unit": w_names.get(wind_unit, w_names["ms"])[1 if lang == "en" else 0],
        "cond": cond, "icon": icon,
        "hi": days[0]["value"].rstrip("°") if days else temp, "lo": days[0]["sub"].rstrip("°") if days else temp,
        "days": days, "hours": hours,
    }
    if "precip" in fields:
        prob = hour_now.get("precip")
        if prob is None:
            prob = (daily_data.get("precipitation_probability_max") or [None])[0]
        values["precip"] = int(prob) if isinstance(prob, (int, float)) else "—"
    if "pressure" in fields:
        hpa = cur.get("pressure_msl")
        unit = options.get("pressure_unit") or "mmhg"
        if isinstance(hpa, (int, float)):
            values["pressure"] = round(hpa * 0.750062) if unit == "mmhg" else (round(hpa * 0.02953, 2) if unit == "inhg" else round(hpa))
        else:
            values["pressure"] = "—"
        values["p_unit"] = {"mmhg": ("мм", "mmHg"), "hpa": ("гПа", "hPa"), "inhg": ("inHg", "inHg")}[unit][1 if lang == "en" else 0]
    if "uv" in fields:
        uv = hour_now.get("uv")
        values["uv"] = round(float(uv), 1) if isinstance(uv, (int, float)) else "—"
    if "visibility" in fields:
        vis = hour_now.get("visibility")
        if isinstance(vis, (int, float)):
            if imperial:
                values["visibility"], values["v_unit"] = round(vis / 1609.34, 1), "mi"
            else:
                values["visibility"], values["v_unit"] = round(vis / 1000, 1), ("км" if lang == "ru" else "km")
        else:
            values["visibility"], values["v_unit"] = "—", ""
    if "sun" in fields:
        try:
            values["sunrise"] = str(daily_data["sunrise"][0])[11:16]
            values["sunset"] = str(daily_data["sunset"][0])[11:16]
        except (KeyError, IndexError, TypeError):
            values["sunrise"] = values["sunset"] = "—"
    return values, [{"name": "Open-Meteo", "url": "https://open-meteo.com/"}]

def _period_label(days: Any, lang: str) -> str:
    names = {"1": ("за сутки", "24 hours"), "7": ("за неделю", "past week"), "30": ("за месяц", "past month"),
             "90": ("за 3 месяца", "past 3 months"), "365": ("за год", "past year"), "1d": ("за день", "today"),
             "5d": ("за 5 дней", "past 5 days"), "1mo": ("за месяц", "past month"), "6mo": ("за полгода", "past 6 months"),
             "1y": ("за год", "past year")}
    pair = names.get(str(days))
    return (pair[1] if lang == "en" else pair[0]) if pair else ""

def _thin(points: List[float], limit: int = 48) -> List[float]:
    if len(points) <= limit:
        return points
    step = len(points) / float(limit)
    return [points[int(i * step)] for i in range(limit - 1)] + [points[-1]]

async def _fx_series(base: str, quote_: str, days: int, valute: Dict[str, Any]) -> List[float]:
    """История курса за период: ЦБ РФ (XML_dynamic) для рубля, Frankfurter — для остальных."""
    end = datetime.now().date()
    start = end - timedelta(days=days)
    if "RUB" in (base, quote_):
        other = quote_ if base == "RUB" else base
        code = (valute.get(other) or {}).get("ID")
        if not code:
            return []
        text = await _http_text("https://www.cbr.ru/scripts/XML_dynamic.asp?" + urlencode({
            "date_req1": start.strftime("%d/%m/%Y"), "date_req2": end.strftime("%d/%m/%Y"), "VAL_NM_RQ": code}))
        points = []
        for nominal, value in re.findall(r"<Record[^>]*>\s*<Nominal>(\d+)</Nominal>\s*<Value>([\d,\.]+)</Value>", text):
            try:
                rub = float(value.replace(",", ".")) / float(nominal or 1)
            except ValueError:
                continue
            points.append(rub if base != "RUB" else (1 / rub if rub else 0))
        return [p for p in points if p]
    data = await _http_json(f"https://api.frankfurter.dev/v1/{start.isoformat()}..{end.isoformat()}?from={base}&to={quote_}")
    rows = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return []
    return [float(rows[day][quote_]) for day in sorted(rows) if isinstance(rows[day], dict) and rows[day].get(quote_) is not None]

async def fetch_fx(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    lang = _lang(settings)
    params = panel["source"]["params"]
    options = _options().clean_options("currency", panel.get("options"))
    base = re.sub(r"[^A-Z]", "", str(params.get("base") or "USD").upper())[:3] or "USD"
    quote_ = re.sub(r"[^A-Z]", "", str(params.get("quote") or "RUB").upper())[:3] or "RUB"
    extra = [c.upper() for c in options.get("extra") or [] if c.upper() not in (base, quote_)]
    if base == quote_:
        raise Wall("source_gone:fx", "Валюты пары совпадают")
    valute: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []

    def fmt(rate: float) -> str:
        wanted = options.get("digits") or "auto"
        digits = int(wanted) if wanted in ("2", "4") else (4 if rate < 1 else 2)
        return _fmt_money(rate, lang, digits)

    if "RUB" in (base, quote_):

        data = await _http_json("https://www.cbr-xml-daily.ru/daily_json.js")
        valute = data.get("Valute") or {}

        def rub_per(code: str, key: str) -> Optional[float]:
            if code == "RUB":
                return 1.0
            row = valute.get(code)
            if not row:
                return None
            return float(row[key]) / float(row.get("Nominal") or 1)

        now_b, now_q = rub_per(base, "Value"), rub_per(quote_, "Value")
        prev_b, prev_q = rub_per(base, "Previous"), rub_per(quote_, "Previous")
        if now_b is None or now_q is None:
            raise Wall("source_gone:cbr", f"ЦБ РФ не публикует курс {base if now_b is None else quote_}")
        rate = now_b / now_q
        prev = (prev_b / prev_q) if prev_b and prev_q else None
        for code in extra:
            cur_x, prev_x = rub_per(code, "Value"), rub_per(code, "Previous")
            if cur_x is None:
                continue
            value = cur_x / now_q
            change = (value - prev_x / prev_q) if prev_x and prev_q else None
            rows.append({"title": f"{code} → {quote_}", "value": fmt(value),
                         **({"subtitle": _fmt_delta(change, lang, 2)} if change is not None else {})})
        date = str(data.get("Date") or "")[:25]
        source = [{"name": "ЦБ РФ" if lang == "ru" else "Bank of Russia", "url": "https://www.cbr-xml-daily.ru/"}]
        source_label = "Курс ЦБ РФ" if lang == "ru" else "Bank of Russia rate"
        try:
            date_label = datetime.fromisoformat(str(data.get("Date"))[:19]).strftime("%d.%m.%Y")
        except ValueError:
            date_label = date
    else:
        data = await _http_json(f"https://api.frankfurter.dev/v1/latest?from={base}&to={quote_}")
        rate = (data.get("rates") or {}).get(quote_)
        if rate is None:
            raise Wall("source_gone:frankfurter", f"Нет курса {base}/{quote_}")
        rate = float(rate)
        prev = None
        hist = panel.get("history", {}).get("rate") or []
        if hist:
            prev = hist[-1] if abs(hist[-1] - rate) > 1e-12 else (hist[-2] if len(hist) > 1 else None)
        if extra:
            try:
                inverse = await _http_json(f"https://api.frankfurter.dev/v1/latest?from={quote_}&to={','.join(extra)}")
                for code in extra:
                    value = (inverse.get("rates") or {}).get(code)
                    if value:
                        rows.append({"title": f"{code} → {quote_}", "value": fmt(1 / float(value))})
            except (Wall, ValueError):
                rows = []
        source = [{"name": "Frankfurter (ECB)", "url": "https://frankfurter.dev/"}]
        source_label = "Курс ЕЦБ" if lang == "ru" else "ECB reference rate"
        date_label = str(data.get("date") or "")
    delta = (rate - prev) if prev is not None else None
    hist = _record_hist(panel, "rate", rate)
    if len(hist) == 1 and prev is not None:
        hist = [prev, rate]
    period = options.get("period") or "30"
    series: List[float] = []
    if period != "off":
        try:
            series = await _fx_series(base, quote_, int(period), valute)
        except Exception as exc:  # noqa: BLE001 — график — украшение, курс важнее
            log.info("panels: fx history unavailable: %s", exc)
    if len(series) >= 2:
        if abs(series[-1] - rate) > 1e-12:
            series.append(rate)
        hist = _thin(series)
    return {
        "rate": fmt(rate), "rate_num": rate,
        "delta": _fmt_delta(delta, lang, 4 if rate < 1 else 2) if delta is not None else "",
        "trend": _trend(delta), "hist": hist, "rows": rows,
        "period_label": _period_label(period, lang) if len(series) >= 2 else "",
        "source_label": source_label,
        "date_label": (("на " if lang == "ru" else "as of ") + date_label) if date_label else "",
    }, source

COIN_SYMBOLS = {"bitcoin": "BTC", "ethereum": "ETH", "the-open-network": "TON", "solana": "SOL", "tether": "USDT",
                "binancecoin": "BNB", "ripple": "XRP", "dogecoin": "DOGE", "cardano": "ADA", "tron": "TRX"}
VS_SIGNS = {"usd": "$", "eur": "€", "rub": "₽", "gbp": "£", "jpy": "¥", "cny": "¥", "btc": "₿"}

def _clip_words(text: str, limit: int) -> str:
    """Короткая подпись мини-чипа: обрезка по слову с «…», а не посреди слова («Все публикации под»)."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit - 1]
    return cut.rstrip(",.;:—- ") + "…"

def _short_money(value: float, lang: str) -> str:
    for size, ru, en in ((1e12, " трлн", "T"), (1e9, " млрд", "B"), (1e6, " млн", "M")):
        if abs(value) >= size:
            return _fmt_money(value / size, lang, 1) + (en if lang == "en" else ru)
    return _fmt_money(value, lang, 0)

async def fetch_crypto(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    lang = _lang(settings)
    params = panel["source"]["params"]
    options = _options().clean_options("crypto", panel.get("options"))
    coin = re.sub(r"[^a-z0-9-]", "", str(params.get("coin") or "bitcoin").lower())[:60] or "bitcoin"
    vs = re.sub(r"[^a-z]", "", str(params.get("vs") or "usd").lower())[:5] or "usd"
    extra = [c for c in options.get("extra") or [] if c != coin][:6]
    fields = options.get("fields") or []
    query = {"ids": ",".join([coin] + extra), "vs_currencies": vs, "include_24hr_change": "true"}
    if "mcap" in fields:
        query["include_market_cap"] = "true"
    if "volume" in fields:
        query["include_24hr_vol"] = "true"
    data = await _http_json("https://api.coingecko.com/api/v3/simple/price?" + urlencode(query))
    row = (data or {}).get(coin) or {}
    if row.get(vs) is None:
        if (data or {}).get("status", {}).get("error_code") == 429:
            raise Wall("blocked:rate-limit", "CoinGecko: слишком много запросов")
        raise Wall("source_gone:coingecko", f"CoinGecko не знает монету «{coin}»")
    price = float(row[vs])
    change = row.get(f"{vs}_24h_change")
    points: List[float] = []
    period = options.get("period") or "1"
    if period != "off":
        try:
            chart = await _http_json(f"https://api.coingecko.com/api/v3/coins/{quote(coin)}/market_chart?" + urlencode(
                {"vs_currency": vs, "days": int(period)}))
            prices = [float(p[1]) for p in (chart or {}).get("prices") or [] if isinstance(p, list) and len(p) > 1]
            if prices:
                step = max(1, len(prices) // 24)
                points = prices[::step][-24:] + [price]
        except Exception:  # noqa: BLE001 - график — украшение, курс важнее
            points = []
    if not points:
        points = _record_hist(panel, "price", price)
    digits = 0 if price >= 1000 else (2 if price >= 1 else 6)
    short = _fmt_money(price, lang, digits)
    if price >= 10_000:
        short = (_fmt_money(price / 1000, lang, 1) + ("к" if lang == "ru" else "k"))
    sign = VS_SIGNS.get(vs, vs.upper())
    rows = []
    for other in extra:
        info = (data or {}).get(other) or {}
        if info.get(vs) is None:
            continue
        value = float(info[vs])
        ch = info.get(f"{vs}_24h_change")
        rows.append({"title": COIN_SYMBOLS.get(other, other[:6].upper()),
                     "value": _fmt_money(value, lang, 0 if value >= 1000 else (2 if value >= 1 else 6)) + " " + sign,
                     **({"subtitle": _fmt_delta(float(ch), lang, 2, "%")} if isinstance(ch, (int, float)) else {})})
    mcap, vol = row.get(f"{vs}_market_cap"), row.get(f"{vs}_24h_vol")
    return {
        "name": str(params.get("name") or coin.replace("-", " ").title()),
        "symbol": COIN_SYMBOLS.get(coin, coin[:6].upper()),
        "price": _fmt_money(price, lang, digits), "price_short": short, "vs_sign": sign,
        "delta": _fmt_delta(float(change), lang, 2, "%") if isinstance(change, (int, float)) else "",
        "trend": _trend(float(change)) if isinstance(change, (int, float)) else "flat",
        "hist": points, "rows": rows, "period_label": _period_label(period, lang) if period != "off" else "",
        "mcap": (_short_money(float(mcap), lang) + " " + sign) if isinstance(mcap, (int, float)) else "—",
        "volume": (_short_money(float(vol), lang) + " " + sign) if isinstance(vol, (int, float)) else "—",
    }, [{"name": "CoinGecko", "url": f"https://www.coingecko.com/en/coins/{coin}"}]

STOCK_RANGES = {"1d": ("1d", "30m", None), "5d": ("5d", "1h", 5), "1mo": ("1mo", "1d", 31), "6mo": ("6mo", "1d", 183),
                "1y": ("1y", "1wk", 366)}

async def _moex_candles(symbol: str, days: int) -> List[float]:
    since = (datetime.now().date() - timedelta(days=days)).isoformat()
    interval = 60 if days <= 7 else 24
    data = await _http_json(f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{quote(symbol)}/candles.json?"
                            + urlencode({"from": since, "interval": interval, "iss.meta": "off", "candles.columns": "close"}))
    return [float(row[0]) for row in ((data.get("candles") or {}).get("data") or []) if row and isinstance(row[0], (int, float))]

async def fetch_stock(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    lang = _lang(settings)
    params = panel["source"]["params"]
    options = _options().clean_options("stock", panel.get("options"))
    period = options.get("period") or "1d"
    symbol = re.sub(r"[^A-Za-z0-9.\-^=]", "", str(params.get("symbol") or "")).upper()[:20]
    if not symbol:
        raise Wall("source_gone:stock", "Не указан тикер")
    extra = [s.upper() for s in options.get("extra") or [] if s.upper() != symbol][:5]
    exchange = str(params.get("exchange") or "moex").lower()
    if exchange == "moex":
        url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{quote(symbol)}.json?"
               "iss.meta=off&iss.only=marketdata,securities&marketdata.columns=SECID,LAST,LASTTOPREVPRICE,UPDATETIME"
               "&securities.columns=SECID,SHORTNAME,PREVPRICE")
        data = await _http_json(url)
        md = (data.get("marketdata") or {}).get("data") or []
        sec = (data.get("securities") or {}).get("data") or []
        if not md or not sec:
            raise Wall("source_gone:moex", f"Мосбиржа не знает тикер {symbol}")
        _, last, change_pct, upd = (md[0] + [None] * 4)[:4]
        _, short_name, prev_price = (sec[0] + [None] * 3)[:3]
        price = float(last if last is not None else prev_price or 0)
        if not price:
            raise Wall("blocked:no-price", "Торги ещё не начались: цены нет")
        hist = _record_hist(panel, "price", price)
        if len(hist) == 1 and prev_price:
            hist = [float(prev_price), price]
        if period not in ("off", "1d"):
            try:
                candles = await _moex_candles(symbol, STOCK_RANGES[period][2] or 5)
                if len(candles) >= 2:
                    hist = _thin(candles + ([price] if abs(candles[-1] - price) > 1e-9 else []))
            except Exception as exc:  # noqa: BLE001
                log.info("panels: moex candles unavailable: %s", exc)
        rows = []
        if extra:
            try:
                more = await _http_json("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?" + urlencode({
                    "securities": ",".join(extra), "iss.meta": "off", "iss.only": "marketdata",
                    "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE"}))
                for secid, last_x, pct_x in ((more.get("marketdata") or {}).get("data") or []):
                    if last_x is None:
                        continue
                    rows.append({"title": str(secid), "value": _fmt_money(float(last_x), lang, 2) + " ₽",
                                 **({"subtitle": _fmt_delta(float(pct_x), lang, 2, "%")} if pct_x is not None else {})})
            except Exception as exc:  # noqa: BLE001
                log.info("panels: moex extra tickers unavailable: %s", exc)
        return {
            "symbol": symbol, "name": str(short_name or symbol), "price": _fmt_money(price, lang, 2),
            "currency_sign": "₽", "exchange_label": "Мосбиржа · TQBR" if lang == "ru" else "Moscow Exchange · TQBR",
            "delta": _fmt_delta(float(change_pct), lang, 2, "%") if change_pct is not None else "",
            "trend": _trend(float(change_pct)) if change_pct is not None else "flat",
            "time_label": (("обновлено " if lang == "ru" else "updated ") + str(upd)) if upd else "",
            "hist": hist, "rows": rows, "period_label": _period_label(period, lang) if period != "off" else "",
        }, [{"name": "Мосбиржа" if lang == "ru" else "MOEX", "url": f"https://www.moex.com/ru/issue.aspx?code={quote(symbol)}"}]
    rng, interval, _days = STOCK_RANGES.get(period if period != "off" else "1d")
    data = await _http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={rng}&interval={interval}")
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise Wall("source_gone:yahoo", f"Тикер {symbol} не найден")
    meta = result.get("meta") or {}
    price = float(meta.get("regularMarketPrice") or 0)
    prev = meta.get("previousClose") if period != "1d" else (meta.get("chartPreviousClose") or meta.get("previousClose"))
    prev = prev or meta.get("chartPreviousClose")
    change_pct = ((price - float(prev)) / float(prev) * 100) if prev else None
    closes = [float(v) for v in (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
              if isinstance(v, (int, float))]
    currency = str(meta.get("currency") or "USD").upper()
    rows = []
    for other in extra:
        try:
            more = await _http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(other)}?range=1d&interval=1d")
            m = (((more.get("chart") or {}).get("result") or [None])[0] or {}).get("meta") or {}
            p_x, prev_x = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
            if p_x is None:
                continue
            pct = ((float(p_x) - float(prev_x)) / float(prev_x) * 100) if prev_x else None
            rows.append({"title": other, "value": _fmt_money(float(p_x), lang, 2) + " " + CURRENCY_SIGNS.get(str(m.get("currency") or "USD").upper(), "$"),
                         **({"subtitle": _fmt_delta(pct, lang, 2, "%")} if pct is not None else {})})
        except Exception as exc:  # noqa: BLE001
            log.info("panels: yahoo %s unavailable: %s", other, exc)
    return {
        "symbol": symbol, "name": str(meta.get("longName") or meta.get("shortName") or symbol)[:60],
        "price": _fmt_money(price, lang, 2), "currency_sign": CURRENCY_SIGNS.get(currency, currency),
        "exchange_label": str(meta.get("fullExchangeName") or meta.get("exchangeName") or ""),
        "delta": _fmt_delta(change_pct, lang, 2, "%") if change_pct is not None else "",
        "trend": _trend(change_pct), "time_label": "",
        "hist": _thin(closes) or _record_hist(panel, "price", price), "rows": rows,
        "period_label": _period_label(period, lang) if period != "off" else "",
    }, [{"name": "Yahoo Finance", "url": f"https://finance.yahoo.com/quote/{quote(symbol)}"}]

def parse_feed(text: str, limit: int = 10) -> Tuple[str, List[Dict[str, str]]]:
    """RSS 2.0 / Atom / RDF → (название ленты, [{title, url, subtitle}])."""
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.strip()[:2_000_000])

    text = re.sub(r"<!DOCTYPE[^\[>]*\[[\s\S]*?\]\s*>", "", text, flags=re.I)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    if "<!ENTITY" in text.upper():
        raise Wall("source_gone:rss", "Лента объявляет сущности XML — такие ленты не читаются")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise Wall("source_gone:rss", f"Это не RSS/Atom-лента ({exc})") from exc

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def child(node, name):
        for item in list(node):
            if local(item.tag) == name:
                return item
        return None

    def text_of(node) -> str:
        if node is None:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", "".join(node.itertext()))).strip()

    title = ""
    items: List[Dict[str, str]] = []
    channel = child(root, "channel") if local(root.tag) == "rss" else root
    if channel is not None:
        title = text_of(child(channel, "title"))
    entries = [n for n in root.iter() if local(n.tag) in ("item", "entry")]
    for node in entries[:limit]:
        name = text_of(child(node, "title"))
        link_node = child(node, "link")
        link = ""
        if link_node is not None:
            link = (link_node.get("href") or text_of(link_node) or "").strip()
        when = text_of(child(node, "pubdate")) or text_of(child(node, "updated")) or text_of(child(node, "published")) \
            or text_of(child(node, "date"))
        stamp = ""
        ts = None
        if when:
            try:
                from email.utils import parsedate_to_datetime
                moment = parsedate_to_datetime(when)
            except (TypeError, ValueError, IndexError):
                try:
                    moment = datetime.fromisoformat(when.replace("Z", "+00:00"))
                except ValueError:
                    moment = None
            if moment is not None:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                stamp = moment.astimezone().strftime("%d.%m %H:%M")
                ts = moment.timestamp()
        if name:
            row = {"title": name[:300], "url": _clean_url(link), "subtitle": stamp}
            desc = text_of(child(node, "description")) or text_of(child(node, "summary")) or text_of(child(node, "content"))
            if desc and desc != name:
                row["summary"] = desc[:280]
            if ts is not None:
                row["ts"] = ts
            items.append(row)
    return title, items

async def _read_feed(url: str) -> Tuple[str, List[Dict[str, Any]]]:
    try:
        text = await _http_text(url)
    except Wall:
        raise
    except ValueError as exc:
        message = str(exc)
        if "HTTP 401" in message or "HTTP 403" in message:
            raise Wall(f"needs_login:{urlparse(url).hostname}", message) from exc
        if "HTTP 404" in message or "HTTP 410" in message:
            raise Wall(f"source_gone:{urlparse(url).hostname}", message) from exc
        raise
    return parse_feed(text, limit=40)

def _words(text: Any) -> List[str]:
    return [w.strip().lower() for w in re.split(r"[,;\n]+", str(text or "")) if w.strip()]

async def fetch_rss(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    url = _clean_url(panel["source"]["params"].get("url"))
    if not url:
        raise Wall("source_gone:rss", "Нет адреса ленты")
    options = _options().clean_options("rss", panel.get("options"))
    feed_title, items = await _read_feed(url)
    sources = []
    for extra in options.get("extra") or []:
        extra_url = _clean_url(extra)
        if not extra_url or extra_url == url:
            continue
        try:
            name, more = await _read_feed(extra_url)
        except Exception as exc:  # noqa: BLE001 — одна дополнительная лента не ломает панель
            log.info("panels: extra feed %s failed: %s", extra_url, exc)
            continue
        for row in more:
            row["source"] = (name or urlparse(extra_url).hostname or "")[:40]
        items += more
        sources.append({"name": (name or urlparse(extra_url).hostname or extra_url)[:60], "url": extra_url})
    if sources:
        for row in items:
            row.setdefault("source", (feed_title or urlparse(url).hostname or "")[:40])
        items.sort(key=lambda r: -(r.get("ts") or 0))
    include, exclude = _words(options.get("include")), _words(options.get("exclude"))
    if include:
        items = [r for r in items if any(w in (r["title"] + " " + r.get("summary", "")).lower() for w in include)]
    if exclude:
        items = [r for r in items if not any(w in (r["title"] + " " + r.get("summary", "") + " " + r.get("url", "")).lower()
                                             for w in exclude)]
    if not options.get("show_time", True):
        for row in items:
            row.pop("subtitle", None)
    elif sources:
        for row in items:
            row["subtitle"] = " · ".join(x for x in (row.get("source"), row.get("subtitle")) if x)
    items = items[: max(int(options.get("count") or 6), 10)]
    title = str(panel["source"]["params"].get("title") or feed_title or urlparse(url).hostname or "RSS")
    return {"feed_title": title[:80], "feed_short": _clip_words(title, 18), "items": items, "count": len(items)}, \
        [{"name": title[:60], "url": url}] + sources

SYSTEM_HIST_KEYS = {"cpu": "cpu", "ram": "ram", "gpu": "gpu", "gpu_temp": "gpu_temp", "net_down": "net_down_v",
                    "net_up": "net_up_v", "disk_io": "disk_io_v"}

async def fetch_system(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Производительность ПК — локальный замер (окно показывает живые значения само, раз в 1–5 с)."""
    from . import panels_system
    lang = _lang(settings)
    options = _options().clean_options("system", panel.get("options"))
    values = await asyncio.to_thread(panels_system.sample, lang=lang)
    values = system_values(values, lang)
    if "procs" in (options.get("metrics") or []):
        values.update(await asyncio.to_thread(panels_system.top_processes, int(options.get("procs_count") or 5),
                                              options.get("procs_sort") or "cpu", lang=lang))
    for name, key in SYSTEM_HIST_KEYS.items():
        number = values.get(key)
        values[name + "_hist"] = _record_hist(panel, name, float(number) if isinstance(number, (int, float)) else None)
    return values, []

def system_values(raw: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """Подписи, которые собираются из нескольких полей замера (общие для API живых значений)."""
    t = (lambda ru, en: en if lang == "en" else ru)
    out = dict(raw)
    cores = raw.get("cores")
    count = len(cores) if isinstance(cores, list) and cores else (cores if isinstance(cores, int) else 0)
    if isinstance(cores, int):
        out["cores"] = []
    threads = _num(raw.get("threads"), 0, 4096) or count
    if threads:
        n = int(threads)
        word = "поток" if n % 10 == 1 and n % 100 != 11 else ("потока" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "потоков")
        out["cores_label"] = t(f"{n} {word}", f"{n} thread" + ("" if n == 1 else "s"))
    else:
        out["cores_label"] = ""
    out["disk_rw"] = f"{raw.get('disk_read', '—')} / {raw.get('disk_write', '—')}"
    for suffix, temp_key, gpu_key in (("", "temp_text", "gpu_temp_text"), ("_f", "temp_text_f", "gpu_temp_text_f")):
        temp = raw.get(temp_key) or "—"
        if raw.get("temp_src") == "acpi" and temp != "—":
            temp += t(" (ACPI)", " (ACPI)")
        if raw.get("gpu_temp") is not None:
            temp = (temp + " · " if temp != "—" else "") + "GPU " + str(raw.get(gpu_key, ""))
        out["temp_label" + suffix] = temp
    out.pop("lang", None)
    return out

USAGE_LINKS = {"claude": ("Claude", "https://claude.ai/settings/usage"),
               "codex": ("Codex", "https://chatgpt.com/codex/settings/usage")}

async def fetch_usage(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    from . import panels_usage
    accounts = panel["source"]["params"].get("accounts") or []
    if not accounts:
        raise Wall("needs_connector:usage", _tr(settings, "Добавьте аккаунт или баланс из MemuAI Proxy, Claude, Codex или Antigravity",
                                                "Add an account or balance from MemuAI Proxy, Claude, Codex or Antigravity"))
    lang = _lang(settings)
    result = await panels_usage.refresh_accounts(accounts, (panel.get("data") or {}).get("accounts") or [],
                                                 lang=lang, force=bool(panel.get("_force")))
    n = result["count"]
    result["subtitle"] = (f"{n} {'аккаунт' if n == 1 else 'аккаунта' if 2 <= n <= 4 else 'аккаунтов'}" if lang != "en"
                          else f"{n} account{'s' if n != 1 else ''}")
    if result["top_pct"] is None:
        result["top_pct"] = "—"
    sources = []
    for provider in dict.fromkeys(a["provider"] for a in accounts):
        if provider in USAGE_LINKS:
            name, url = USAGE_LINKS[provider]
            sources.append({"name": name, "url": url})
    return result, sources

def _big(value: Any, lang: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number >= 1e6:
        return _fmt_money(number / 1e6, lang, 1) + (" млн" if lang != "en" else "M")
    if number >= 1e4:
        return _fmt_money(number / 1e3, lang, 1) + (" тыс." if lang != "en" else "k")
    return _fmt_money(number, lang, 0)

async def fetch_proxy(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Статистика MemuAI Proxy: тот же файл почасовых корзин, что читает «Главная» Proxy (только чтение)."""
    from . import metrics as proxy_metrics
    lang = _lang(settings)
    t = (lambda ru, en: en if lang == "en" else ru)
    options = _options().clean_options("proxy", panel.get("options"))
    period = options.get("period") or "today"
    path = proxy_metrics.METRICS_PATH
    import os
    if not os.path.exists(path):
        raise Wall("source_gone:proxy-metrics", t("У MemuAI Proxy ещё нет статистики (или она выключена в его настройках).",
                                                  "MemuAI Proxy has no statistics yet (or they are turned off in its settings)."))
    snap = await asyncio.to_thread(lambda: proxy_metrics.Metrics(path).snapshot(period))
    total = int(snap.get("total") or 0)
    tokens = int(snap.get("prompt_tokens") or 0) + int(snap.get("completion_tokens") or 0)
    cost = snap.get("cost") or {}
    billed = float(cost.get("api_billed") or 0)
    equiv = float(cost.get("api_equivalent") or 0)
    if billed >= 0.005:
        cost_text = f"${billed:,.2f}".replace(",", " ")
    elif equiv >= 0.005:
        cost_text = t(f"≈ ${equiv:,.2f} по API", f"≈ ${equiv:,.2f} at API prices").replace(",", " ")
    else:
        cost_text = "$0"
    models = [{"title": str(row.get("model") or "")[:48], "value": _big(row.get("total"), lang)}
              for row in (snap.get("models") or [])[:5]]
    series = [float(p.get("total") or 0) for p in snap.get("series") or []]
    labels = {"today": t("Сегодня", "Today"), "day": t("За 24 часа", "Last 24 hours"),
              "week": t("За неделю", "Past week"), "month": t("За месяц", "Past month")}
    values = {
        "requests": _big(total, lang), "success": round(float(snap.get("success_rate") or 0)) if total else "—",
        "tokens": _big(tokens, lang), "cost": cost_text if total else "—",
        "latency": (f"{_fmt_ms(snap.get('p50_ms'), lang)} / {_fmt_ms(snap.get('p95_ms'), lang)}") if total else "—",
        "series": series[-48:] if any(series) else [], "models": models,
        "period_label": labels.get(period, ""),
        "note": "" if total else t("Запросов за период не было.", "No requests in this period."),
    }
    return values, []

def _fmt_ms(value: Any, lang: str) -> str:
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return "—"
    if ms >= 1000:
        return _fmt_money(ms / 1000, lang, 1) + (" с" if lang != "en" else " s")
    return f"{ms:.0f}" + (" мс" if lang != "en" else " ms")

def _when_label(start: str, lang: str) -> Tuple[str, Optional[float]]:
    """«Сегодня 14:00» / «Завтра, весь день» / «Пт 09:30» для начала события."""
    t = (lambda ru, en: en if lang == "en" else ru)
    if not start:
        return "", None
    try:
        if len(start) == 10:
            day = datetime.fromisoformat(start).date()
            moment = None
        else:
            moment = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
            day = moment.date()
    except ValueError:
        return start, None
    today = datetime.now().date()
    if day == today:
        head = t("Сегодня", "Today")
    elif day == today + timedelta(days=1):
        head = t("Завтра", "Tomorrow")
    else:
        names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] if lang != "en" else ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        head = f"{names[day.weekday()]} {day.strftime('%d.%m')}"
    if moment is None:
        return f"{head}, {t('весь день', 'all day')}", datetime.combine(day, datetime.min.time()).timestamp()
    return f"{head} {moment.strftime('%H:%M')}", moment.timestamp()

async def fetch_calendar(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Ближайшие события Google Calendar через подключение «Интеграции» (только чтение)."""
    from . import integrations
    lang = _lang(settings)
    t = (lambda ru, en: en if lang == "en" else ru)
    options = _options().clean_options("calendar", panel.get("options"))
    args = {"days": int(options.get("days") or 3), "limit": int(options.get("count") or 8)}
    if options.get("calendar"):
        args["calendar_id"] = options["calendar"]
    try:
        result = await asyncio.to_thread(integrations.run_tool, "calendar_list_events", args)
    except integrations.IntegrationError as exc:
        message = str(exc)
        low = message.lower()
        if "не подключ" in low or "not connected" in low or "нет данных для входа" in low or "missing" in low or "заполните" in low:
            raise Wall("needs_connector:google_calendar", t("Подключите Google Calendar в «Настройки → Интеграции».",
                                                            "Connect Google Calendar in Settings → Integrations.")) from exc
        if "401" in message or "invalid_grant" in low or "войдите" in low:
            raise Wall("needs_login:google-calendar", message[:300]) from exc
        raise Wall("blocked:calendar", message[:300]) from exc
    events = []
    upcoming = None
    for row in (result or {}).get("events") or []:
        label, stamp = _when_label(str(row.get("start") or ""), lang)
        loc = str(row.get("location") or "") if options.get("show_location", True) else ""
        events.append({"title": str(row.get("summary") or t("(без названия)", "(untitled)"))[:120],
                       "subtitle": " · ".join(x for x in (label, loc[:60]) if x)})
        if upcoming is None and stamp is not None and stamp >= time.time() - 3600:
            upcoming = (row, label)
    days = int(options.get("days") or 3)
    values = {"events": events,
              "subtitle": t(f"Событий: {len(events)} · {days} дн.", f"{len(events)} events · {days} d") if days > 1 else
              t(f"Сегодня · событий: {len(events)}", f"Today · {len(events)} events"),
              "next_time": upcoming[1].split()[-1] if upcoming else "—",
              "next_title": str(upcoming[0].get("summary") or "")[:20] if upcoming else t("Свободно", "Free")}
    return values, [{"name": "Google Calendar", "url": "https://calendar.google.com/"}]

async def fetch_github(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """GitHub через токен из «Интеграций»: уведомления, ревью, назначенное, мои PR."""
    from . import integrations
    lang = _lang(settings)
    t = (lambda ru, en: en if lang == "en" else ru)
    options = _options().clean_options("github", panel.get("options"))
    view = options.get("view") or "notifications"
    count = int(options.get("count") or 8)
    reasons = {"review_requested": t("ревью", "review"), "mention": t("упоминание", "mention"),
               "assign": t("назначено", "assigned"), "author": t("автор", "author"), "comment": t("комментарий", "comment"),
               "ci_activity": "CI", "state_change": t("статус", "state"), "subscribed": t("подписка", "subscribed"),
               "team_mention": t("команда", "team"), "security_alert": t("безопасность", "security")}

    def call():
        if view == "notifications":
            return integrations._github("GET", "/notifications", params={"per_page": min(50, count * 2),
                                                                         "participating": "true" if options.get("participating") else "false"})
        query = {"review": "is:open is:pr review-requested:@me archived:false",
                 "assigned": "is:open assignee:@me archived:false",
                 "mine": "is:open is:pr author:@me archived:false"}[view]
        return integrations._github("GET", "/search/issues", params={"q": query, "per_page": count, "sort": "updated"})

    try:
        data = await asyncio.to_thread(call)
    except integrations.IntegrationError as exc:
        message = str(exc)
        low = message.lower()
        if "не подключ" in low or "not connected" in low or "токен" in low and "нет" in low:
            raise Wall("needs_connector:github", t("Подключите GitHub в «Настройки → Интеграции».",
                                                   "Connect GitHub in Settings → Integrations.")) from exc
        if "лимит запросов" in low or "rate limit" in low:

            raise Wall("blocked:rate-limit", message[:300], retry_after=900) from exc
        if "401" in message:
            raise Wall("needs_login:github", t("GitHub отклонил токен: перевыпустите его и сохраните в «Настройки → Интеграции».",
                                               "GitHub rejected the token: issue a new one and save it in Settings → Integrations.")) from exc
        if "403" in message or ("404" in message and view == "notifications"):
            raise Wall("needs_login:github", t("Токену GitHub не хватает прав на уведомления: нужен classic-токен с доступом "
                                               "«notifications» (fine-grained токены уведомления не читают).",
                                               "The GitHub token cannot read notifications: a classic token with the "
                                               "“notifications” scope is needed (fine-grained tokens cannot).")) from exc
        raise Wall("blocked:github", message[:300]) from exc
    items = []
    if view == "notifications":
        rows = data if isinstance(data, list) else []
        for row in rows[:count]:
            subject = row.get("subject") or {}
            repo = (row.get("repository") or {}).get("full_name") or ""
            api_url = str(subject.get("url") or "")
            html = ""
            match = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/(issues|pulls)/(\d+)", api_url)
            if match:
                html = f"https://github.com/{match.group(1)}/{'pull' if match.group(2) == 'pulls' else 'issues'}/{match.group(3)}"
            elif repo:
                html = f"https://github.com/{repo}"
            stamp = None
            try:
                stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
            items.append({"title": str(subject.get("title") or "")[:200], "url": html,
                          "subtitle": " · ".join(x for x in (repo, reasons.get(str(row.get("reason")), str(row.get("reason") or ""))) if x),
                          **({"ts": stamp} if stamp else {})})
        subtitle = t(f"Непрочитанных: {len(rows)}", f"{len(rows)} unread")
        total = len(rows)
    else:
        rows = (data or {}).get("items") or [] if isinstance(data, dict) else []
        for row in rows[:count]:
            repo = re.sub(r"^https://api\.github\.com/repos/", "", str(row.get("repository_url") or ""))
            items.append({"title": str(row.get("title") or "")[:200], "url": str(row.get("html_url") or ""),
                          "subtitle": f"{repo} #{row.get('number')}"})
        total = int((data or {}).get("total_count") or len(rows)) if isinstance(data, dict) else len(rows)
        subtitle = {"review": t(f"Ждут ревью: {total}", f"{total} awaiting review"),
                    "assigned": t(f"Назначено: {total}", f"{total} assigned"),
                    "mine": t(f"Открытых PR: {total}", f"{total} open PRs")}[view]
    for item in items:
        item["url"] = _clean_url(item.get("url"))
    return {"items": items, "count": total, "subtitle": subtitle}, \
        [{"name": "GitHub", "url": "https://github.com/notifications" if view == "notifications" else "https://github.com/pulls"}]

TYPED_FETCHERS = {"open-meteo": fetch_weather, "fx": fetch_fx, "coingecko": fetch_crypto,
                  "stock": fetch_stock, "rss": fetch_rss, "system": fetch_system, "usage": fetch_usage,
                  "proxy": fetch_proxy, "calendar": fetch_calendar, "github": fetch_github}

_PROGRESS: Dict[str, Dict[str, Any]] = {}

def set_progress(pid: str, stage: str, detail: str = "") -> None:
    """Этап текущего обновления: карточка показывает «Собираю 22 источника → Убираю повторы …»."""
    _PROGRESS[pid] = {"stage": str(stage)[:24], "detail": str(detail)[:60], "at": time.time()}

async def fetch_feed(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]], Dict[str, Any]]:
    """Лента по теме через конвейер panels_curate: план → сбор → склейка → оценка → «уже показанное»."""
    curate = _curate()
    pid = panel["id"]
    params = panel["source"]["params"]
    lang = _lang(settings)
    state = curate.clean_state(panel.get("feed"))
    brief = str(params.get("brief") or panel.get("subject") or panel["title"])
    topic = str(params.get("topic") or "")
    if topic == "security" and curate.wants_hn(brief) and curate.detect_topic(brief) != topic:

        topic = curate.detect_topic(brief)
    options = _options().clean_options("news", panel.get("options"))
    if options.get("out_lang") in ("ru", "en"):
        lang = options["out_lang"]
    use = set(options.get("sources") or [])
    key = (f"{topic}|{brief}|{params.get('count')}|{lang}|{options.get('hours')}|{','.join(sorted(use))}|"
           f"{','.join(options.get('feeds') or [])}")
    plan = state.get("plan") if state.get("planned_for") == key else None
    model = _model_for(panel, settings)
    can_model = _CONTEXT.complete is not None and bool(model)

    async def ask(system: str, user: str) -> Dict[str, Any]:
        return await _ask_model(model, system, user, settings)

    if plan is None:
        set_progress(pid, "plan")
        extra = None
        if topic not in curate.TOPICS and can_model and not curate.wants_hn(brief):
            try:
                extra = await ask(curate.PLANNER_PROMPT, f"User language: {'English' if lang == 'en' else 'Russian'}\n"
                                                         f"Panel brief: {brief}")
            except Wall as wall:
                log.info("panels: planner unavailable for %s: %s", pid, wall.status)
        plan = curate.plan_for(brief, topic=topic, count=int(params.get("count") or 0), out_lang=lang, extra=extra,
                               hours=int(options.get("hours") or 36))

        if "official" not in use:
            plan["feeds"] = [f for f in plan["feeds"] if f.get("kind") not in ("official", "primary")]
            plan["pages"] = [p for p in plan["pages"] if p.get("kind") not in ("official", "primary")]
        if "media" not in use:
            plan["feeds"] = [f for f in plan["feeds"] if f.get("kind") in ("official", "primary")]
        if "hn" not in use:
            plan["hn"] = None
        if "reddit" not in use:
            plan["reddit"] = []
        if "search" not in use:
            plan["queries"] = []
        for url in options.get("feeds") or []:
            plan["feeds"].append(curate._feed(url[:400], curate.host_of(url), curate.host_authority(url)[0], scoped=True, user=True))
        state["plan"], state["planned_for"] = plan, key

    async def fetch_text(url: str) -> str:
        return await _http_text(url)

    async def search(query: str, limit: int) -> Dict[str, Any]:
        return await _CONTEXT.search(query, limit)  # type: ignore[misc]

    try:
        result = await curate.build_feed(plan, state, fetch_text=fetch_text,
                                         search=search if _CONTEXT.search is not None else None,
                                         ask_model=ask if can_model else None, cadence=panel.get("cadence"),
                                         progress=lambda stage, detail: set_progress(pid, stage, detail))
    except RuntimeError as exc:
        raise Wall("blocked:source-down", str(exc)[:300]) from exc
    stats = result["stats"]
    items = result["items"]
    exclude = _words(options.get("exclude"))
    if exclude:
        items = [r for r in items if not any(w in " ".join(str(r.get(k) or "") for k in ("title", "summary", "url", "source")).lower()
                                             for w in exclude)]
    t = (lambda ru, en: en if lang == "en" else ru)
    values: Dict[str, Any] = {
        "note": result["note"], "stats": stats,
        "subtitle": t(f"{stats['sources_ok']} источн. · {stats['stories']} историй · отобрано {stats['shown']}",
                      f"{stats['sources_ok']} source{'' if stats['sources_ok'] == 1 else 's'} · {stats['stories']} stor{'y' if stats['stories'] == 1 else 'ies'} · {stats['shown']} picked"),
        "rated_by_model": bool(stats.get("model")),
    }
    if items or not (panel.get("data") or {}).get("items"):
        values["items"] = items
        values["count"] = len(items)
    sources = result["sources"]
    return values, sources, result["state"]

SPEC_GUIDE = """Panel spec = {"blocks":[...], "mini":{...}}. Block types (fields; * = required):
- title {text*, subtitle, icon}
- metric {value*, unit, label, delta, trend: up|down|flat, caption, icon} — one big number
- stats {items*: [{label, value, unit}]} — 2-4 small figures
- list {items*: [{title, subtitle, value, url, badge}], max, label} — headlines, rows, standings
- strip {items*: [{label, value, sub, icon}]} — a horizontal row of small cells (forecast days, fixtures)
- progress {value*, max, label, caption}
- sparkline {points*: [numbers], label, unit}
- countdown {target*: ISO datetime with the user's UTC offset from «Now» (2027-01-01T00:00+03:00), label}
- gauge {value*: number 0..max, max (default 100), label, unit, caption, icon} — a small ring meter
- badge {text*, tone: ok|warn|danger|info|neutral|accent}
- text {text*, muted}
- link {text, url*} — http(s) only
- action {text*, prompt*} — a button that asks MemuAI the prompt in a new chat
- image {src*: https url, alt, caption}
- row {blocks*: up to 4 non-row blocks side by side}
Any string may bind a data value: "{{key}}" (a whole field may bind a list: "items": "{{headlines}}").
Optional "min_size": "m" or "l" hides a block on smaller cards. "mini": {icon, value*, unit, label} is the
tiny top-bar chip. Icons: clear, clear-night, partly, cloudy, fog, rain, snow, storm, chart, coin, btc,
news, trophy, calendar, clock, globe, bolt, heart, star, bell, home, cart, book, music, film, plane, car,
fire, drop, wind, sun, moon, pin, flag, user, mail, gift."""

_REASONING_BLOCK = re.compile(r"<(think|thinking|reasoning)>[\s\S]*?</\1>", re.I)
_JSON_KEYS = ("values", "spec", "blocks", "runbook", "evidence", "items", "wall")

def _decode_lenient(text: str) -> Optional[Dict[str, Any]]:
    """Объект JSON с начала ``text`` (Round 31). Терпит лишнюю закрывающую скобку посреди ответа
    (qwen3.8: ``…"urls":[…]}},"rhythm":…`` — корень закрывался раньше времени, и весь ответ
    отбрасывался «Модель не вернула JSON»), висячие запятые и хвост после объекта."""
    decoder = json.JSONDecoder()
    for attempt in (text, re.sub(r",\s*([\]}])", r"\1", text)):
        current = attempt
        best: Optional[Dict[str, Any]] = None
        for _ in range(4):
            try:
                value, end = decoder.raw_decode(current)
            except ValueError:
                break
            if not isinstance(value, dict):
                break
            best = value
            rest = current[end:].lstrip()

            if re.match(r",\s*\"", rest) and current[end - 1] == "}":
                current = current[:end - 1] + current[end:]
                continue
            break
        if best is not None:
            return best
    return None

def _extract_json(text: str) -> Dict[str, Any]:
    """JSON-ответ модели. Рассуждения (``<think>…</think>``, текст до/после объекта, «{{ключ}}» в
    объяснениях) не мешают: берётся объект с ожидаемыми ключами, а не просто первая «{»."""
    source = _REASONING_BLOCK.sub("", str(text or ""))
    for tag in ("</think>", "</thinking>", "</reasoning>"):
        if tag in source:
            source = source.rsplit(tag, 1)[1]
    candidates: List[Dict[str, Any]] = []
    for fence in re.finditer(r"```(?:json|JSON)?\s*(\{[\s\S]*?)```", source):
        value = _decode_lenient(fence.group(1).strip())
        if value:
            candidates.append(value)
    if not candidates:

        covered = -1
        decoder = json.JSONDecoder()
        for match in list(re.finditer(r"\{", source))[:200]:
            start = match.start()
            if start < covered:
                continue
            value = _decode_lenient(source[start:])
            if not value:
                continue
            try:
                covered = start + decoder.raw_decode(source[start:])[1]
            except ValueError:
                covered = len(source)
            candidates.append(value)
            if len(candidates) >= 6:
                break
    for value in candidates:
        if any(key in value for key in _JSON_KEYS):
            return value
    if candidates:
        return max(candidates, key=lambda v: len(json.dumps(v, ensure_ascii=False)))
    raise Wall("blocked:model-format", "Модель не вернула JSON")

def _model_for(panel: Dict[str, Any], settings: Dict[str, Any]) -> str:
    """Модель панели: своя модель панели → «Модель для панелей с ИИ» из настроек → модель, которой панель
    создана в чате → модель по умолчанию MemuAI."""
    return str(panel.get("ai_model") or settings.get("model") or panel.get("model") or _CONTEXT.default_model() or "").strip()

async def _gather_web(panel: Dict[str, Any], *, query_fallback: str = "") -> Tuple[str, List[Dict[str, str]]]:
    """Собирает текст для модели: чтение адресов runbook и поиск по его запросам.

    Возвращает (материал, ссылки материала с их текстом). Ссылки — это то, ЧТО модель видела;
    на карточку попадают только те, на которые она сослалась (или где найдены сами значения).
    Один упавший адрес — не стена: стена только если материала нет совсем (Раунд 22).
    """
    ctx = _CONTEXT
    runbook = panel.get("runbook") or {}
    parts: List[str] = []
    refs: List[Dict[str, str]] = []
    queries = list(runbook.get("queries") or [])
    if not queries and not runbook.get("urls"):
        queries = [query_fallback or panel.get("subject") or panel.get("title") or ""]
    queries = [q for q in queries if q][:3]
    errors: List[str] = []
    walls: List[Wall] = []
    for url in (runbook.get("urls") or [])[:3]:
        if ctx.fetch is None:
            break
        set_progress(panel["id"], "fetch", urlparse(url).hostname or "")
        try:
            page = await ctx.fetch(url, 12000)
            body = str(page.get("text") or "")[:12000]
            parts.append(f"<page url=\"{url}\" title=\"{_clean_text(page.get('title'), 200)}\">\n{body}\n</page>")
            refs.append({"name": _clean_text(page.get("title") or urlparse(url).hostname, 80), "url": url, "text": body})
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            host = urlparse(url).hostname or "site"
            if "HTTP 401" in message:
                walls.append(Wall(f"needs_login:{host}", message))
            elif "HTTP 404" in message or "HTTP 410" in message:
                walls.append(Wall(f"source_gone:{host}", message))
            elif "HTTP 403" in message:
                walls.append(Wall(f"blocked:forbidden-{host}", message))
            errors.append(f"{host}: {message[:160]}")
    if queries and ctx.search is not None:
        set_progress(panel["id"], "search", str(len(queries)))
        for query in queries:
            try:
                found = await ctx.search(query, 6)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"search {query!r}: {str(exc)[:160]}")
                continue
            answer = found.get("answer")
            if answer:
                parts.append(f"<instant_answer query=\"{query}\">{json.dumps(answer, ensure_ascii=False)[:3000]}</instant_answer>")
            for index, row in enumerate((found.get("results") or [])[:6], 1):
                snippet = str(row.get("snippet") or "")[:500]
                parts.append(f"[{index}] {row.get('title', '')} — {row.get('url', '')}\n{snippet}")
                if row.get("url"):
                    refs.append({"name": _clean_text(row.get("title") or urlparse(row["url"]).hostname, 80),
                                 "url": row["url"], "text": f"{row.get('title', '')} {snippet}"})
            for page in (found.get("pages") or [])[:3]:
                body = str(page.get("excerpt") or page.get("text") or "")[:4000]
                parts.append(f"<page url=\"{page.get('url', '')}\">\n{body}\n</page>")
                for ref in refs:
                    if ref["url"] == page.get("url"):
                        ref["text"] += " " + body
    if not parts:
        if walls:
            raise walls[0]
        if errors:
            raise Wall("blocked:source-down", "; ".join(errors)[:400])
        if ctx.search is None and ctx.fetch is None:
            raise Wall("blocked:no-web", "Веб-поиск недоступен")
    return "\n\n".join(parts)[:30000], refs

def _digits(text: Any) -> List[str]:
    """Числа из текста без разделителей: «1 234,5» и «1,234.5» → «12345»."""
    return [re.sub(r"[\s,.\u00a0\u202f]", "", m) for m in re.findall(r"\d[\d\s,.\u00a0\u202f]*\d|\d", str(text or ""))]

def _value_texts(value: Any) -> List[str]:
    if isinstance(value, dict):
        return [str(v) for v in value.values() if isinstance(v, (str, int, float))]
    if isinstance(value, list):
        out: List[str] = []
        for item in value[:20]:
            out += _value_texts(item) if isinstance(item, (dict, list)) else [str(item)]
        return out
    return [str(value)]

def cited_sources(values: Dict[str, Any], evidence: Any, refs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Источники карточки: только то, на что модель сослалась и что было в материале, либо где
    нашлись сами числа. Никаких «первых четырёх ссылок выдачи» — так на карточку попадали
    «250 лучших фильмов» и интернет-магазины."""
    by_url = {_curate().canonical_url(r["url"]): r for r in refs if r.get("url")}
    picked: List[Dict[str, str]] = []

    def add(ref):
        if ref and not any(p["url"] == ref["url"] for p in picked):
            picked.append({"name": ref["name"], "url": ref["url"]})

    urls: List[str] = []
    if isinstance(evidence, dict):
        urls = [str(u) for u in evidence.values() if isinstance(u, str)]
    elif isinstance(evidence, list):
        urls = [str(u.get("url") if isinstance(u, dict) else u) for u in evidence]
    for url in urls:
        add(by_url.get(_curate().canonical_url(url)))
    if not picked:
        numbers = [d for v in values.values() for t in _value_texts(v) for d in _digits(t) if len(d) >= 2]
        for ref in refs:
            have = set(_digits(ref.get("text")))
            if numbers and any(n in have for n in numbers):
                add(ref)
    return picked[:4]

_PLACEHOLDER = re.compile(r"(?i)^\s*(n/?a|none|null|unknown|tbd|tba|not (?:found|available|specified)|no data|-+|—|–|\?+|"
                          r"нет данных|не найдено|не указано|неизвестно|н/д)\s*[.!]?\s*$")

_MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^)\s]+)\)")

def tidy_model_text(value: str) -> str:
    """A value from the model is shown as plain text on a tiny card: «**83 000 $**», «`3.15.0`», «<b>2:3</b>»,
    «[Reuters](https://…)» and «&amp;» came through literally (Round 31). Keep the words, drop the markup."""
    text = str(value)
    if "<" in text and re.search(r"</?(b|strong|i|em|span|br|p|code|a|sup|sub|small|u)\b[^>]*>", text, re.I):
        text = re.sub(r"(?i)<br\s*/?>", " ", text)
        text = re.sub(r"</?[a-z][a-z0-9]*\b[^>]*>", "", text, flags=re.I)
    if "&" in text and re.search(r"&(?:[a-z]+|#\d+);", text):
        text = html_unescape(text)
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+(?=\S))", "", text)
    return text.strip()

def countdown_targets(spec: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Round 31: a countdown block bound to a model value ({"target": "{{new_year}}"}) got the model's own wording
    («Fri 2027-01-01 3:00 AM MSK (UTC+03:00)», «01.01.2027») — Telegram / the Mini App could not count days from
    it and the window relied on the browser guessing. Such values become ISO (an unparsable one is dropped)."""
    keys = set()

    def walk(blocks: Any) -> None:
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "row":
                walk(block.get("blocks"))
            elif block.get("type") == "countdown":
                match = _BINDING.fullmatch(str(block.get("target") or "").strip())
                if match and not match.group(2):
                    keys.add(match.group(1))

    walk((spec or {}).get("blocks"))
    out = dict(values)
    for key in keys:
        if isinstance(out.get(key), str):
            iso = _lenient_target(out[key])
            if iso:
                out[key] = iso
            else:
                out.pop(key)
    return out

def drop_placeholders(values: Dict[str, Any]) -> Dict[str, Any]:
    """Round 31: «Never invent — leave the key out» is not always obeyed: models write "N/A", "—", «нет данных».
    Such a value is no value: drop it, so the panel keeps its last real value (or the second pass / «not found»
    wall applies) instead of showing «N/A $» as the price."""
    out: Dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, str):
            value = tidy_model_text(value)
        if isinstance(value, str) and (not value.strip() or _PLACEHOLDER.match(value)):
            continue
        if isinstance(value, list):
            rows = []
            for row in value:
                if isinstance(row, dict):
                    row = {k: (tidy_model_text(v) if isinstance(v, str) and k != "url" else v) for k, v in row.items()}
                    row = {k: v for k, v in row.items() if not (isinstance(v, str) and _PLACEHOLDER.match(v))}
                    if not any(isinstance(v, (int, float)) or (isinstance(v, str) and v.strip()) for v in row.values()):
                        continue
                elif isinstance(row, str) and _PLACEHOLDER.match(row):
                    continue
                rows.append(row)
            if not rows and value:
                continue
            value = rows
        out[key] = value
    return out

def _items_urls_ok(values: Dict[str, Any], refs: List[Dict[str, str]]) -> Dict[str, Any]:
    """Списки от модели: ссылка записи должна быть из материала (иначе модель её придумала)."""
    known = {_curate().canonical_url(r["url"]) for r in refs if r.get("url")}
    for key, value in list(values.items()):
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict) and row.get("url") and _curate().canonical_url(str(row["url"])) not in known:
                    row.pop("url", None)
    return values

def model_wall(message: str, model: str, settings: Optional[Dict[str, Any]] = None) -> Wall:
    """Ошибка вызова модели → понятная стена (Round 31). Раньше любая ошибка была «blocked:model-error»,
    а окно показывало только «Модель вернула ошибку.» — не было видно ни 429, ни «нет такой модели»,
    и 429 повторялся по обычному ритму вместо паузы. Сообщение — на языке панелей, с кодом в скобках."""
    settings = settings or {}
    message = str(message or "")
    low = message.lower()
    name = model.split("/")[-1] if model else "?"
    code = re.search(r"\bHTTP (\d{3})\b", message)
    status = int(code.group(1)) if code else 0
    detail = re.sub(r"\s+", " ", message)[:200]
    log.info("panels: model %s failed: %s", model, detail)

    short = f"HTTP {status}" if status else (detail[:60] + ("…" if len(detail) > 60 else ""))
    if "offline" in low or "не запущен" in low or "connecterror" in low.replace(" ", "") and "proxy" in low:
        return Wall("blocked:proxy-offline", _tr(settings, "Модели недоступны: MemuAI Proxy не запущен.",
                                                  "Models are unavailable: MemuAI Proxy is not running."))
    if status == 429 or "rate limit" in low or "rate_limit" in low or "quota" in low or "частоту" in low \
            or "лимит" in low and "запрос" in low:
        wall = Wall("blocked:model-rate-limit", _tr(
            settings, f"Модель «{name}» ограничила запросы (лимит провайдера) — повторю позже. ({short})",
            f"The model “{name}” is rate-limited by its provider — will retry later. ({short})"))
        retry = re.search(r"retry[- ]after[\"':\s]*(\d+)", low)
        wall.retry_after = float(retry.group(1)) if retry else 900.0
        return wall
    if status == 404 or "не нашёл модель" in low or "не маршрутизируется" in low or "does not exist" in low \
            or "model not found" in low or "unknown model" in low or "no such model" in low:
        return Wall("blocked:model-missing", _tr(
            settings, f"Модель «{name}» не найдена у провайдера. Выберите другую модель в настройках панели.",
            f"The model “{name}” was not found. Pick another model in the panel settings."))
    if status in (401, 403) or "api key" in low or "unauthorized" in low or "ключ" in low and "отклон" in low:
        return Wall("blocked:model-auth", _tr(
            settings, f"Провайдер модели «{name}» отклонил ключ API (HTTP {status or 401}). Проверьте аккаунт в MemuAI Proxy.",
            f"The provider of “{name}” rejected the API key (HTTP {status or 401}). Check the account in MemuAI Proxy."))
    if status >= 500 or "недоступн" in low or "no available accounts" in low or "network" in low \
            or "сетевая ошибка" in low or "failed to connect" in low or "timed out" in low or "aborted" in low:
        return Wall("blocked:model-down", _tr(
            settings, f"Модель «{name}» сейчас недоступна — повторю позже. ({short})",
            f"The model “{name}” is unavailable right now — will retry later. ({short})"))
    return Wall("blocked:model-error", _tr(settings, f"Модель «{name}» вернула ошибку: {detail[:140]}",
                                           f"The model “{name}” returned an error: {detail[:140]}"))

async def _ask_model(model: str, system: str, user: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = settings or {}
    if _CONTEXT.complete is None:
        raise Wall("blocked:proxy-offline", _tr(settings, "Модели недоступны: MemuAI Proxy не запущен.",
                                                "Models are unavailable: MemuAI Proxy is not running."))
    if not model:
        raise Wall("blocked:no-model", _tr(settings, "Не выбрана модель для обновления панелей.",
                                           "No model is selected for refreshing panels."))
    try:
        text = await asyncio.wait_for(_CONTEXT.complete(model, [{"role": "system", "content": system},
                                                                {"role": "user", "content": user}]), 150)
    except asyncio.TimeoutError as exc:
        raise Wall("blocked:model-timeout", _tr(settings, "Модель не ответила за 150 секунд — повторю позже.",
                                                "The model did not answer within 150 seconds — will retry later.")) from exc
    except Wall:
        raise
    except Exception as exc:  # noqa: BLE001
        if "Timeout" in type(exc).__name__:
            raise Wall("blocked:model-timeout", _tr(settings, "Модель не успела ответить — повторю позже.",
                                                    "The model timed out — will retry later.")) from exc
        if type(exc).__module__.startswith(("httpx", "httpcore")) or isinstance(exc, (ConnectionError, OSError)):

            raise Wall("blocked:proxy-offline", _tr(settings, "Модели недоступны: MemuAI Proxy не запущен или не отвечает.",
                                                    "Models are unavailable: MemuAI Proxy is not running or not answering.")) from exc
        raise model_wall(str(exc) or type(exc).__name__, model, settings) from exc
    try:
        return _extract_json(text)
    except Wall as wall:
        snippet = re.sub(r"\s+", " ", str(text or ""))[:160]
        log.info("panels: model %s answered without JSON: %r", model, snippet)
        raise Wall(wall.status, _tr(settings, f"Модель «{model.split('/')[-1]}» ответила не в нужном формате. Попробуйте другую модель.",
                                    f"The model “{model.split('/')[-1]}” answered in the wrong format. Try another model.")) from None

def _today(settings: Dict[str, Any]) -> str:
    """«Сейчас» для модели — с днём недели и числовым смещением (Round 31). ``%Z`` на Windows — длинное
    локализованное имя пояса («RTZ 2 (зима)»), а без смещения модель считала обратный отсчёт по UTC
    (Новый год «в 03:00»)."""
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "+00:00"
    return now.strftime("%Y-%m-%d %H:%M ") + f"(UTC{offset}, {now.strftime('%A')}; user's local time)"

async def ai_refresh(panel: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Обновление моделью по runbook: веб → JSON значений, только для привязанных ключей."""
    keys = spec_bindings(panel["spec"])
    if not keys:
        return {}, panel.get("sources") or []
    material, refs = await _gather_web(panel)
    lang = _lang(settings)
    set_progress(panel["id"], "model")
    system = (
        "You refresh the values of a small MemuAI dashboard panel. Use ONLY facts present in the "
        "<material> below (search results and pages). Never invent or estimate a live value: if a value "
        "is not in the material, leave that key out. Judge the material: prefer official/primary pages and "
        "reputable outlets, ignore pages that are not about the panel's subject (listicles, shops, unrelated "
        "articles) and anything older than the value needs. Keep each value short (it is shown on a tiny "
        f"card), in {'English' if lang == 'en' else 'Russian'}, with numbers formatted for that language. "
        "Lists are arrays of objects with the fields their block uses (title, subtitle, value, url …); a list "
        "item's url must be one that appears in the material. "
        "Return JSON only: {\"values\":{key:value}, \"evidence\":{key:\"the material URL the value came from\"}, "
        "\"wall\":null}. If the material shows the source needs a login, is gone, or is blocked, set "
        "\"wall\":{\"status\":\"needs_login:<domain>|source_gone:<domain>|blocked:<reason>\",\"message\":\"…\"} "
        "and return the values you could still confirm."
    )
    user = (
        f"Now: {_today(settings)}\nPanel: {panel['title']}\nSubject: {panel.get('subject') or panel['title']}\n"
        f"How to refresh (runbook): {(panel.get('runbook') or {}).get('prompt') or '-'}\n"
        f"Keys to fill: {json.dumps(keys)}\n"
        f"Current values (format examples, may be stale): {json.dumps({k: panel['data'].get(k) for k in keys}, ensure_ascii=False)[:3000]}\n"
        f"Spec: {json.dumps(panel['spec'], ensure_ascii=False)[:3000]}\n\n<material>\n{material}\n</material>"
    )
    answer = await _ask_model(_model_for(panel, settings), system, user, settings)
    values = countdown_targets(panel["spec"], _items_urls_ok(drop_placeholders(clean_values(answer.get("values"), keys)), refs))
    wall = answer.get("wall")
    sources = cited_sources(values, answer.get("evidence") or answer.get("sources"), refs)
    if isinstance(wall, dict) and wall.get("status") and not values:
        raise Wall(str(wall.get("status")), str(wall.get("message") or ""))
    if not values:
        raise Wall("blocked:not-found", "В найденных источниках нет нужных значений")
    if isinstance(wall, dict) and wall.get("status"):
        values["_partial_wall"] = {"status": _wall_slug(wall.get("status")), "message": str(wall.get("message") or "")[:300]}
    return values, sources

async def ai_compose(panel: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Сборка панели по описанию: модель выбирает блоки, данные, runbook и ритм."""
    material, refs = await _gather_web(panel, query_fallback=panel.get("subject") or panel["title"])
    lang = _lang(settings)
    set_progress(panel["id"], "compose")
    system = (
        "You design one small glanceable MemuAI panel (like a home-screen widget) from the user's brief. "
        "Show ONLY what the user asked for — no padding. Use only facts from <material>; never invent live "
        "values (leave a value out if it is not there); ignore material that is not about the brief. Language "
        f"of all text: {'English' if lang == 'en' else 'Russian'}.\n\n{SPEC_GUIDE}\n\n"
        "Return JSON only: {\"title\":\"short\", \"spec\":{\"blocks\":[...],\"mini\":{...}}, "
        "\"values\":{key:value}, \"evidence\":{key:\"material URL the value came from\"}, "
        "\"runbook\":{\"prompt\":\"how to re-gather the values next time\",\"queries\":[\"specific search query\"],"
        "\"urls\":[\"https://page that holds the values\"]}, \"rhythm\":\"how often it changes\", "
        "\"cadence\":{\"kind\":\"interval\",\"minutes\":N}|{\"kind\":\"daily\",\"at\":\"HH:MM\"}|{\"kind\":\"manual\"}}. "
        "Bind every changing value through {{key}} in the spec and put it in values. Cadence: the SLOWEST honest "
        "pace (at least every 15 minutes for web values); content scoped to a day is daily. Never write remarks "
        "about missing data into blocks or values («not found in the sources», «N/A», «data for X is shown "
        "instead») and never substitute a different subject: leave that block out — the runbook will find it later. "
        "Put the source pages that really hold the values into runbook.urls."
    )
    user = (f"Now: {_today(settings)}\nBrief: {panel.get('subject') or panel['title']}\n"
            f"Wanted rhythm: {panel.get('rhythm') or '-'}\n\n<material>\n{material}\n</material>")
    answer = await _ask_model(_model_for(panel, settings), system, user, settings)
    spec, errors = validate_spec(answer.get("spec"))
    if not spec["blocks"]:
        raise Wall("blocked:bad-spec", "; ".join(errors)[:300] or "Модель не собрала блоки")
    keys = spec_bindings(spec)
    values = countdown_targets(spec, _items_urls_ok(drop_placeholders(clean_values(answer.get("values"), keys)), refs))
    return {
        "title": _clean_text(answer.get("title") or panel["title"], 80),
        "spec": spec, "values": values,
        "sources": cited_sources(values, answer.get("evidence") or answer.get("sources"), refs),
        "runbook": _clean_runbook(answer.get("runbook")) if answer.get("runbook") else panel.get("runbook"),
        "rhythm": _clean_text(answer.get("rhythm"), 200),
        "cadence": _clean_cadence(answer.get("cadence"), kind=panel["kind"]) if answer.get("cadence") else panel["cadence"],
    }

def _claim(pid: str, *, force: bool, now: float) -> Optional[Dict[str, Any]]:
    """Атомарно помечает панель «обновляется». None — уже обновляется или не найдена."""

    def update(doc):
        try:
            panel = _find(doc, pid)
        except PanelError:
            return None
        if panel.get("dismissed_at"):
            return None
        if (panel.get("refreshing_until") or 0) > now:
            return None
        if not force:
            if panel["status"] == "setup" or (panel.get("wall") and panel["wall"]["status"].split(":")[0] in USER_WALLS):
                return None
            due = panel.get("next_refresh")
            if due is None or due > now:
                return None
        panel["refreshing_until"] = now + CLAIM_SECONDS
        return {"panel": copy.deepcopy(panel), "settings": copy.deepcopy(doc["settings"])}

    _doc, claimed = mutate(update)
    return claimed

def _finish(pid: str, *, values: Optional[Dict[str, Any]] = None, sources: Optional[List[Dict[str, str]]] = None,
            wall: Optional[Wall] = None, history: Optional[Dict[str, Any]] = None,
            composed: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
            fields: Optional[Dict[str, Any]] = None,
            typed: Optional[Tuple[str, bool]] = None) -> Optional[Dict[str, Any]]:
    """Записывает итог обновления. ``typed`` = (вид, брать ли параметры источника): вид встроенной
    панели пересобирается из ТЕКУЩИХ настроек, а не из копии на момент старта — правка «Что показывать»
    или своё название, сделанные, пока шло обновление, не откатываются. Если панель правили так, что
    нужны новые данные (``_RERUN``), собранное по старым параметрам не пишется: сразу идёт повтор.
    None — панель удалили, пока шло обновление."""
    now = time.time() if now is None else now
    _PROGRESS.pop(pid, None)
    stale = pid in _RERUN

    def update(doc):
        try:
            panel = _find(doc, pid)
        except PanelError:
            return None
        panel["refreshing_until"] = None
        if stale:
            return copy.deepcopy(panel)
        panel["updated"] = now
        for key, value in (fields or {}).items():
            if key in ("feed", "kind", "source", "subject"):
                panel[key] = value
        if composed:
            for key in ("title", "spec", "runbook", "rhythm", "cadence"):
                if composed.get(key) and not (key == "title" and panel.get("title_custom")):
                    panel[key] = composed[key]
            if panel.get("recompose") and wall is None:
                panel["data"] = {}
            if wall is None:
                panel["recompose"] = False
        if history is not None:
            panel["history"] = _clean_history(history)
        if wall is None:
            partial = (values or {}).pop("_partial_wall", None)
            panel["data"] = {**panel.get("data", {}), **(values or {})}
            if sources:
                panel["sources"] = _clean_sources(sources)
            panel["status"] = "live"
            panel["wall"] = _clean_wall({**partial, "at": now}) if partial else None
            panel["error"] = ""
            panel["failures"] = 0
            panel["last_refresh"] = now
            panel["next_refresh"] = next_refresh_at(panel["cadence"], now=now)
            if typed:
                kind, with_params = typed
                title, panel["spec"] = typed_spec(kind, panel["source"]["params"] if with_params else {},
                                                  doc["settings"], panel.get("options"))
                if not panel.get("title_custom"):
                    panel["title"] = title if "{{" not in title else _render_str(title, panel["data"])
        else:
            panel["failures"] = min(50, int(panel.get("failures") or 0) + 1)
            panel["wall"] = {"status": wall.status, "message": wall.message, "at": now}
            kind = wall.status.split(":")[0]

            if kind == "needs_location" or wall.status == "needs_connector:usage":
                panel["status"] = "setup"
            elif panel["status"] == "building" or not panel.get("data"):
                panel["status"] = "failed"
            panel["error"] = wall.message
            if kind in USER_WALLS:
                panel["next_refresh"] = None
            elif _clean_cadence(panel["cadence"])["kind"] == "manual" and panel.get("data"):
                panel["next_refresh"] = None
            else:
                base = next_refresh_at(panel["cadence"], now=now) or (now + 3600)
                step = max(60.0, base - now)
                if wall.status in TRANSIENT_WALLS:

                    step = min(step, TRANSIENT_RETRY_SECONDS)
                if wall.status in ("blocked:rate-limit", "blocked:model-rate-limit"):
                    step = max(step, float(getattr(wall, "retry_after", 0) or 300))
                panel["next_refresh"] = now + min(24 * 3600.0, step * (2 ** min(5, panel["failures"] - 1)))
        return copy.deepcopy(panel)

    _doc, panel = mutate(update)
    return panel

async def refresh_panel(pid: str, *, force: bool = False, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Обновляет одну панель (встроенный источник или модель). Возвращает её новое состояние."""
    now = time.time() if now is None else now
    claimed = _claim(pid, force=force, now=now)
    if not claimed:
        return None
    panel, settings = claimed["panel"], claimed["settings"]
    stype = panel["source"]["type"]
    try:
        if stype in ("local", "timers", "module"):

            return _finish(pid, values={"local": True}, typed=(panel["kind"], stype == "module"), now=now)
        if stype == "countdown":
            target = str(panel["source"]["params"].get("target") or panel["data"].get("target") or "")
            if not target:
                raise Wall("source_gone:countdown", "Нет даты события")
            return _finish(pid, values=countdown_values(target, settings), typed=("countdown", True), now=now)
        if stype == "feed":
            values, sources, state = await fetch_feed(panel, settings)
            return _finish(pid, values=values, sources=sources, typed=("news", True), now=now, fields={"feed": state})
        if stype in TYPED_FETCHERS:
            panel["_force"] = force
            values, sources = await TYPED_FETCHERS[stype](panel, settings)
            return _finish(pid, values=values, sources=sources, history=panel.get("history"),
                           typed=(panel["kind"], True), now=now)
        if not panel["spec"].get("blocks") or panel.get("recompose"):
            set_progress(pid, "search")
            composed = await ai_compose(panel, settings)
            values = composed.pop("values")
            keys = spec_bindings(composed["spec"])
            missing = [k for k in keys if k not in values]
            if missing and (composed.get("runbook") or {}).get("urls"):

                second = {**panel, **{k: composed[k] for k in ("spec", "runbook") if composed.get(k)},
                          "data": dict(values)}
                try:
                    more, more_sources = await ai_refresh(second, settings)
                    more.pop("_partial_wall", None)
                    values = {**values, **{k: v for k, v in more.items() if k in missing}}
                    known = composed.get("sources") or []
                    composed["sources"] = known + [s for s in more_sources if s not in known]
                except Wall as wall:
                    log.info("panels: %s second pass hit %s", pid, wall.status)
            if not values and keys:
                raise Wall("blocked:not-found", _tr(settings, "В найденных источниках нет нужных значений.",
                                                    "The sources did not contain the values."))
            if not values:
                values = {"_static": True}
            return _finish(pid, values=values, sources=composed.pop("sources"), composed=composed, now=now)
        set_progress(pid, "search")
        values, sources = await ai_refresh(panel, settings)
        return _finish(pid, values=values, sources=sources, now=now)
    except Wall as wall:
        log.info("panels: %s hit a wall: %s %s", pid, wall.status, wall.message)
        return _finish(pid, wall=wall, now=now)
    except asyncio.CancelledError:

        _PROGRESS.pop(pid, None)
        _release(pid)
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc) or type(exc).__name__
        log.warning("panels: refresh %s failed: %s", pid, message[:300])
        return _finish(pid, wall=failure_wall(message, settings), now=now)

def failure_wall(message: str, settings: Dict[str, Any]) -> Wall:
    """Стена для необработанного сбоя источника. Сообщение — для человека и на языке панелей:
    его без перевода показывают Telegram-бот, Mini App и подсказки (Round 27: погода писала голое
    «HTTP 429»). Код ответа остаётся в скобках — по нему проще разобраться, что случилось."""
    message = str(message or "")
    code = re.search(r"\bHTTP (\d{3})\b", message)
    status = int(code.group(1)) if code else 0
    if status in (401, 403):
        return Wall("needs_login:source", _tr(
            settings, f"Источник не пускает без входа (HTTP {status}). Укажите в настройках панели другой источник.",
            f"The source requires a sign-in (HTTP {status}). Edit the panel to use another source."))
    if status in (404, 410):
        return Wall("source_gone:source", _tr(
            settings, f"Адрес источника не найден (HTTP {status}).",
            f"The source address was not found (HTTP {status})."))
    if status == 429:
        return Wall("blocked:rate-limit", _tr(
            settings, "Источник временно ограничил запросы (HTTP 429) — повторю позже.",
            "The source is rate-limiting requests (HTTP 429) — will retry later."))
    if status >= 500:
        return Wall("blocked:source-down", _tr(
            settings, f"Источник временно не работает (HTTP {status}) — повторю позже.",
            f"The source is temporarily down (HTTP {status}) — will retry later."))
    if status:
        return Wall("blocked:source-down", _tr(
            settings, f"Источник ответил ошибкой (HTTP {status}) — повторю позже.",
            f"The source answered with an error (HTTP {status}) — will retry later."))
    return Wall("blocked:source-down", _tr(
        settings, "Источник не ответил — повторю позже.", "The source did not answer — will retry later.")
        + f" ({message[:200]})")

def _release(pid: str) -> None:
    def update(doc):
        try:
            _find(doc, pid)["refreshing_until"] = None
        except PanelError:
            pass

    mutate(update)

def _render_str(template: str, values: Dict[str, Any]) -> str:
    return _BINDING.sub(lambda m: _clean_text(values.get(m.group(1), ""), 80), template)

_LOOP: Optional[asyncio.AbstractEventLoop] = None

def bind_loop() -> None:
    """Запоминает цикл событий сервера: create/update идут в потоках (to_thread), а фоновая
    сборка должна стартовать в основном цикле."""
    global _LOOP
    try:
        _LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass

def start_refresh(pid: str, *, force: bool = True, rerun: bool = False) -> None:
    """Фоновое обновление (сборка новой панели, «Обновить» без ожидания).

    ``rerun`` — панель только что поменяли (параметры, «Что показывать», место): если обновление уже
    идёт, оно собирает по старым настройкам, поэтому его итог не пишется и сразу идёт повтор.
    «Обновить» во время обновления просто ждёт идущее (rerun=False)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:

        if _LOOP is not None and not _LOOP.is_closed():
            _LOOP.call_soon_threadsafe(lambda: start_refresh(pid, force=force, rerun=rerun))
        return
    task = _TASKS.get(pid)
    if task and not task.done():
        if rerun:
            _RERUN.add(pid)
        return
    task = loop.create_task(refresh_panel(pid, force=force))
    _TASKS[pid] = task
    task.add_done_callback(lambda done, key=pid: _refresh_done(key, done))

def _refresh_done(pid: str, task: "asyncio.Task[Any]") -> None:
    if _TASKS.get(pid) is task:
        _TASKS.pop(pid, None)
    if not task.cancelled() and task.exception() is not None:
        log.warning("panels: refresh %s crashed: %s", pid, task.exception())
    if pid in _RERUN:
        _RERUN.discard(pid)
        if not task.cancelled():
            start_refresh(pid)

async def wait_refresh(pid: str, timeout: float = 45.0) -> None:
    task = _TASKS.get(pid)
    if task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass

def pid_busy(pid: str) -> bool:
    task = _TASKS.get(pid)
    return bool(task and not task.done())

def due_ids(doc: Dict[str, Any], now: float) -> List[str]:
    out = []
    if not doc["settings"].get("enabled", True):
        return out
    for panel in active(doc):
        if panel["status"] in ("setup",) or (panel.get("refreshing_until") or 0) > now:
            continue
        if panel.get("wall") and panel["wall"]["status"].split(":")[0] in USER_WALLS:
            continue
        due = panel.get("next_refresh")
        if panel["status"] == "building" and panel["id"] not in _TASKS:
            out.append(panel["id"])
        elif due is not None and due <= now:
            out.append(panel["id"])
    return out

async def tick(now: Optional[float] = None) -> int:
    """Один проход планировщика: запускает обновление панелей, которым пора."""
    bind_loop()
    now = time.time() if now is None else now
    doc = await asyncio.to_thread(load)
    started = 0
    model_ids = {p["id"] for p in doc["panels"] if p["source"]["type"] in MODEL_SOURCES}
    running = sum(1 for pid, task in _TASKS.items() if pid in model_ids and not task.done())
    for pid in due_ids(doc, now):
        if pid in _TASKS and not _TASKS[pid].done():
            continue
        if pid in model_ids:
            if running >= MAX_MODEL_REFRESHES:
                continue
            running += 1
        start_refresh(pid, force=False)
        started += 1
    _telegram_check()
    return started

def _telegram_check() -> None:
    """Round 24: updates of subscribed panels → Telegram (``telegram_panels``).
    Runs as its own task: a slow Bot API call never delays the scheduler."""
    if _TG_TASK.get("task") is not None and not _TG_TASK["task"].done():
        return
    try:
        from . import telegram_panels
        _TG_TASK["task"] = asyncio.get_running_loop().create_task(telegram_panels.check_all())
        _TG_TASK["task"].add_done_callback(_telegram_done)
    except Exception as exc:  # noqa: BLE001
        log.info("panels: telegram check skipped: %s", exc)

_TG_TASK: Dict[str, Any] = {"task": None}

def _telegram_done(task: "asyncio.Task") -> None:
    """Retrieve (and log) a background failure instead of losing it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("panels: telegram updates check failed: %s: %s", type(exc).__name__, exc)

def reset_stale_claims() -> int:
    """Round 31: a «refreshing» mark left by a process that was killed mid-refresh (crash, Task Manager, power
    off). Only the window process refreshes panels, so at its start nothing can be running: the mark made the
    card spin «Обновляю…» and the window poll every 1.5 s for up to CLAIM_SECONDS, and blocked «Обновить»."""
    def update(doc):
        count = 0
        for panel in doc["panels"]:
            if panel.get("refreshing_until") and not pid_busy(panel["id"]):
                panel["refreshing_until"] = None
                count += 1
        return count

    _doc, count = mutate(update)
    return count

async def panels_loop() -> None:
    """Раз в минуту, пока открыто окно MemuAI. Обновление — по времени, не чаще раза в час."""
    await asyncio.sleep(3)
    from .safe_mode import is_safe_mode
    if is_safe_mode():
        return
    try:
        await asyncio.to_thread(reset_stale_claims)
    except Exception as exc:  # noqa: BLE001
        log.info("panels: stale claims not reset: %s", exc)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("panels: tick failed: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)

def public_panel(panel: Dict[str, Any], local: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {k: v for k, v in panel.items() if k not in ("history", "feed")}
    out["bindings"] = spec_bindings(panel["spec"])
    out["refreshing"] = bool((panel.get("refreshing_until") or 0) > time.time()) or (
        panel["id"] in _TASKS and not _TASKS[panel["id"]].done())
    if out["refreshing"] and panel["id"] in _PROGRESS:
        out["progress"] = dict(_PROGRESS[panel["id"]])
    out["floor_minutes"] = floor_minutes(panel["kind"])
    if panel["kind"] in ("todo", "notes"):
        try:
            local = local if local is not None else _local().load()
        except Exception as exc:  # noqa: BLE001 — повреждённый файл задач не роняет список панелей
            log.warning("panels: local data unreadable: %s", exc)
            local = {"lists": [], "notes": {}}
        if panel["kind"] == "todo":
            wanted = list((panel.get("options") or {}).get("lists") or [])
            by_id = {row["id"]: row for row in local.get("lists") or []}
            out["todo"] = {"lists": [by_id[lid] for lid in wanted if lid in by_id],
                           "all": [{"id": row["id"], "name": row["name"], "color": row["color"]} for row in local.get("lists") or []]}
        else:
            out["note"] = (local.get("notes") or {}).get(panel["id"]) or {"text": "", "updated": 0}
    return out

def snapshot() -> Dict[str, Any]:
    doc = load()
    local = None
    if any(p["kind"] in ("todo", "notes") for p in doc["panels"]):
        try:
            local = _local().load()
        except Exception as exc:  # noqa: BLE001
            log.warning("panels: local data unreadable: %s", exc)
            local = {"lists": [], "notes": {}}
    return {"panels": [public_panel(p, local) for p in active(doc)],
            "dismissed": [public_panel(p, local) for p in dismissed(doc)],
            "settings": doc["settings"], "templates": TEMPLATES,
            "option_schema": _options().public_schema(),
            "zones": _layout().public_zones(doc["settings"].get("lang", "ru")),
            "limits": {"min_interval_minutes": MIN_INTERVAL_MINUTES, "max_panels": MAX_PANELS,
                       "floors": KIND_FLOOR_MINUTES, "presets": list(CADENCE_PRESETS),
                       "floor_reasons": FLOOR_REASONS, "local_kinds": list(LOCAL_KINDS), "live_kinds": list(LIVE_KINDS)}}

def _next_order(doc: Dict[str, Any], place: str) -> int:
    values = [p["order"].get(place, 0) for p in active(doc) if place in p["placements"]]
    return (max(values) + 1) if values else 0

def create_panel(*, template: str = "", params: Optional[Dict[str, Any]] = None, subject: str = "",
                 title: str = "", spec: Any = None, values: Any = None, sources: Any = None, runbook: Any = None,
                 cadence: Any = None, rhythm: str = "", use: str = "", placements: Optional[List[str]] = None,
                 size: str = "", model: str = "", start: bool = True, options: Any = None,
                 ai_model: str = "") -> Dict[str, Any]:
    """Создаёт панель и (для всего, что нужно собрать) запускает первую сборку.

    ``start=False`` — вызывающий (HTTP-маршрут, инструмент модели) сам запустит её в своём цикле
    событий и дождётся, чтобы карточка появилась уже с данными."""
    placements = [p for p in (placements or []) if p in PLACEMENTS] or ["home"]

    def update(doc):
        if len(active(doc)) >= MAX_PANELS:
            raise PanelError(f"Не больше {MAX_PANELS} панелей")
        settings = doc["settings"]
        kind = template if template in KINDS else ("custom" if not template else "")
        if not kind:
            raise PanelError("Неизвестный шаблон панели")
        feed_like = kind == "custom" and subject and _curate().is_feed_brief(subject) and (
            not spec or any(b.get("type") == "list" for b in (validate_spec(spec)[0]["blocks"])))
        if feed_like:

            panel = make_template_panel("news", {"brief": subject, "topic": _curate().detect_topic(subject)}, settings)
        elif kind == "custom" and (spec or subject):
            panel = make_template_panel("custom", {"brief": subject or title or "panel"}, settings)
            if spec:
                clean, errors = validate_spec(spec)
                if not clean["blocks"]:
                    raise PanelError("Спецификация панели некорректна: " + "; ".join(errors[:4]))
                panel["spec"] = clean
        else:
            panel = make_template_panel(kind, dict(params or {}), settings, options if isinstance(options, dict) else None)
        if ai_model:
            panel["ai_model"] = _clean_text(ai_model, 300)
        if title:
            panel["title"] = _clean_text(title, 80)
            panel["title_custom"] = True
        if rhythm:
            panel["rhythm"] = _clean_text(rhythm, 200)
        if use:
            panel["use"] = _clean_text(use, 300)
        if runbook:
            panel["runbook"] = _clean_runbook(runbook)
        if cadence:
            panel["cadence"] = _clean_cadence(cadence, kind=kind)
        if model:
            panel["model"] = _clean_text(model, 300)
        if values and panel["source"]["type"] != "feed":
            panel["data"].update(clean_values(values, spec_bindings(panel["spec"])))
        if sources:
            panel["sources"] = _clean_sources(sources)
        panel["placements"] = placements
        panel["size"] = size if size in SIZES else panel["size"]
        for place in PLACEMENTS:
            panel["order"][place] = _next_order(doc, place)
        doc["panels"].append(panel)
        return copy.deepcopy(panel)

    _doc, panel = mutate(update)
    if panel["status"] == "building" and start:
        start_refresh(panel["id"])
    return panel

async def create_and_fill(*, wait: float = 20.0, **kwargs: Any) -> Dict[str, Any]:
    """Создать и сразу заполнить: первая сборка стартует в этом цикле, ждём до ``wait`` секунд.

    Встроенные источники успевают почти всегда — карточка появляется уже с данными; долгая
    ИИ-сборка продолжается в фоне, а карточка показывает её этапы (``progress``)."""
    bind_loop()
    panel = await asyncio.to_thread(create_panel, start=False, **kwargs)
    if panel["status"] == "building":
        start_refresh(panel["id"])
        await wait_refresh(panel["id"], wait)
        doc = await asyncio.to_thread(load)
        try:
            panel = _find(doc, panel["id"])
        except PanelError:
            pass
    return panel

def update_panel(pid: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Правка структуры и настроек: название, размер, места, ритм, блоки, runbook, параметры источника."""
    rebuild = {"value": False}

    def update(doc):
        panel = _find(doc, pid)
        before_runbook = copy.deepcopy(panel.get("runbook"))
        if "title" in changes and str(changes["title"] or "").strip():
            title = _clean_text(changes["title"], 80)

            if title != panel["title"]:
                panel["title"] = title
                panel["title_custom"] = True
        if "size" in changes and changes["size"] in SIZES:
            panel["size"] = changes["size"]
        if "placements" in changes and isinstance(changes["placements"], list):
            new = list(dict.fromkeys(p for p in changes["placements"] if p in PLACEMENTS))
            for place in new:
                if place not in panel["placements"]:
                    panel["order"][place] = _next_order(doc, place)
            panel["placements"] = new
        if "cadence" in changes:
            panel["cadence"] = _clean_cadence(changes["cadence"], kind=panel["kind"])
            panel["next_refresh"] = next_refresh_at(panel["cadence"], now=panel.get("last_refresh") or time.time())
        if "rhythm" in changes:
            panel["rhythm"] = _clean_text(changes["rhythm"], 200)
        if "spec" in changes and changes["spec"]:
            clean, errors = validate_spec(changes["spec"])
            if not clean["blocks"]:
                raise PanelError("Спецификация панели некорректна: " + "; ".join(errors[:4]))
            panel["spec"] = clean
            missing = [k for k in spec_bindings(clean) if k not in panel["data"]]
            if missing and panel["source"]["type"] == "ai":
                rebuild["value"] = True
        if "runbook" in changes:
            panel["runbook"] = _clean_runbook(changes["runbook"])
        model_panel = panel["source"]["type"] in MODEL_SOURCES
        if "runbook" in changes and model_panel and panel["runbook"] != before_runbook:
            rebuild["value"] = True
        if "subject" in changes:
            subject = _clean_text(changes["subject"], 600)
            if subject and subject != panel.get("subject") and panel["source"]["type"] == "ai":

                if panel["kind"] == "custom":
                    panel["recompose"] = True
                rebuild["value"] = True
            panel["subject"] = subject
        if "model" in changes:
            panel["model"] = _clean_text(changes["model"], 300)
        if "ai_model" in changes:
            new_model = _clean_text(changes["ai_model"], 300)
            if new_model != panel.get("ai_model") and model_panel:

                wall_status = (panel.get("wall") or {}).get("status", "")
                if wall_status.startswith("blocked:model") or wall_status in ("blocked:no-model", "blocked:bad-spec") \
                        or panel["status"] in ("failed", "building") or not panel.get("data"):
                    panel["wall"] = None
                    panel["failures"] = 0
                    if panel["status"] == "failed":
                        panel["status"] = "building"
                    rebuild["value"] = True
            panel["ai_model"] = new_model
        if isinstance(changes.get("options"), dict):
            before_opts = dict(panel.get("options") or {})
            panel["options"] = _options().clean_options(panel["kind"], changes["options"], base=before_opts)
            if panel["source"]["type"] != "ai":
                title, spec = typed_spec(panel["kind"], panel["source"]["params"], doc["settings"], panel["options"])
                panel["spec"] = spec
                if not panel.get("title_custom") and "{{" not in title:
                    panel["title"] = title
            if _options().needs_refetch(panel["kind"], before_opts, panel["options"]):
                if panel["source"]["type"] == "feed" and panel.get("feed"):
                    panel["feed"]["plan"] = None
                rebuild["value"] = panel["source"]["type"] not in ("local", "timers")
        if "params" in changes and isinstance(changes["params"], dict):
            merged = {**panel["source"]["params"], **changes["params"]}
            panel["source"]["params"] = _clean_params({k: v for k, v in merged.items() if v not in ("",)},
                                                      panel["source"]["type"])
            if panel["source"]["type"] == "feed" and panel.get("feed"):
                panel["feed"]["plan"] = None
            if panel["kind"] == "countdown":
                target = _parse_target(panel["source"]["params"].get("target"))
                if not target:
                    raise PanelError("Укажите дату и время события")
                panel["source"]["params"]["target"] = target
            panel["wall"] = None
            if panel["status"] == "setup":
                panel["status"] = "building"
            rebuild["value"] = True
        panel["updated"] = time.time()
        return copy.deepcopy(panel)

    before = {"accounts": []}
    try:
        old = _find(load(), pid)
        before["accounts"] = [a["id"] for a in (old["source"]["params"].get("accounts") or [])]
    except PanelError:
        pass
    _doc, panel = mutate(update)
    if panel["source"]["type"] == "usage":
        keep = {a["id"] for a in panel["source"]["params"].get("accounts") or []}
        from .panels_usage import drop_secret
        for aid in before["accounts"]:
            if aid not in keep:
                drop_secret(aid)
    if rebuild["value"]:
        start_refresh(pid, rerun=True)
    return panel

def reset_feed(pid: str) -> Dict[str, Any]:
    """Забыть «уже показанное» ленты (и план источников) — следующая сборка как с нуля."""

    def update(doc):
        panel = _find(doc, pid)
        if panel["source"]["type"] != "feed":
            raise PanelError("Это не лента")
        panel["feed"] = _curate().clean_state({})
        panel["next_refresh"] = time.time()
        return copy.deepcopy(panel)

    panel = mutate(update)[1]
    start_refresh(pid, rerun=True)
    return panel

def update_data(pid: str, values: Any, sources: Any = None) -> Dict[str, Any]:
    """Новые значения без изменения структуры (только привязанные ключи)."""

    def update(doc):
        panel = _find(doc, pid)
        clean = clean_values(values, spec_bindings(panel["spec"]))
        if not clean:
            raise PanelError("Нет значений для ключей этой панели: " + ", ".join(spec_bindings(panel["spec"])))
        panel["data"].update(clean)
        if sources:
            panel["sources"] = _clean_sources(sources)
        now = time.time()
        panel["last_refresh"] = now
        panel["status"] = "live"
        panel["wall"] = None
        panel["updated"] = now
        return copy.deepcopy(panel)

    return mutate(update)[1]

def reorder(placement: str, ids: List[str]) -> Dict[str, Any]:
    """Порядок панелей в одном месте; панель, перенесённая из другого места, добавляется сюда."""
    if placement not in PLACEMENTS:
        raise PanelError("Неизвестное место")
    ids = [str(i) for i in ids if isinstance(i, str)][:MAX_PANELS]

    def update(doc):
        by_id = {p["id"]: p for p in active(doc)}
        for index, pid in enumerate(ids):
            panel = by_id.get(pid)
            if not panel:
                continue
            if placement not in panel["placements"]:
                panel["placements"].append(placement)
            panel["order"][placement] = index
        for panel in active(doc):
            if placement in panel["placements"] and panel["id"] not in ids:
                panel["order"][placement] = len(ids) + panel["order"].get(placement, 0)
        return None

    mutate(update)
    return snapshot()

def move(pid: str, source: str, target: str, index: int = -1) -> Dict[str, Any]:
    """Переносит панель из одного места в другое (из «нигде» — просто закрепляет)."""
    if target not in PLACEMENTS:
        raise PanelError("Неизвестное место")

    def update(doc):
        panel = _find(doc, pid)
        if source in panel["placements"] and source != target:
            panel["placements"].remove(source)
        if target not in panel["placements"]:
            panel["placements"].append(target)
        siblings = sorted([p for p in active(doc) if target in p["placements"] and p["id"] != pid],
                          key=lambda p: p["order"].get(target, 0))
        position = len(siblings) if index < 0 else min(index, len(siblings))
        siblings.insert(position, panel)
        for order, item in enumerate(siblings):
            item["order"][target] = order
        return None

    mutate(update)
    return snapshot()

def apply_layout(items: Any = None, layout: Any = None) -> Dict[str, Any]:
    """Round 33: one atomic save of the window's layout — places, order and size of many panels at once plus the
    per-zone settings (``settings.layout``). Used by «Edit layout» (drag between zones, undo / redo, reset).
    A panel left without any place is simply not shown (as before, it stays in the list of panels)."""

    def update(doc):
        live = active(doc)
        by_id = {p["id"]: p for p in live}
        for item in _layout().clean_items(items, list(by_id)):
            panel = by_id[item["id"]]
            if "placements" in item:
                panel["placements"] = item["placements"]
            if "order" in item:
                panel["order"].update(item["order"])
            if "size" in item:
                panel["size"] = item["size"]
            panel["updated"] = time.time()
        if isinstance(layout, dict):
            doc["settings"]["layout"] = _layout().clean_layout(layout, base=doc["settings"].get("layout"))
        return None

    mutate(update)
    return snapshot()

def dismiss(pid: str, *, purge: bool = False) -> None:
    box: Dict[str, Any] = {}

    def update(doc):
        panel = _find(doc, pid)
        if purge:
            if panel["source"]["type"] == "usage":
                from .panels_usage import drop_secret
                for account in panel["source"]["params"].get("accounts") or []:
                    drop_secret(account["id"])
            doc["panels"] = [p for p in doc["panels"] if p["id"] != pid]
            if panel["kind"] == "notes":
                box["drop_note"] = True
        else:
            panel["dismissed_at"] = time.time()
            panel["refreshing_until"] = None
            old = dismissed(doc)[MAX_DISMISSED:]
            drop = {p["id"] for p in old}
            doc["panels"] = [p for p in doc["panels"] if p["id"] not in drop]
        return None

    mutate(update)
    if box.get("drop_note"):
        try:
            _local().drop_note(pid)
        except Exception as exc:  # noqa: BLE001
            log.info("panels: note not dropped: %s", exc)
    _RERUN.discard(pid)
    task = _TASKS.pop(pid, None)
    if task and not task.done():

        loop = task.get_loop()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            task.cancel()
        elif not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)

def revive(pid: str) -> Dict[str, Any]:
    def update(doc):
        panel = _find(doc, pid)
        panel["dismissed_at"] = None
        if not panel["placements"]:
            panel["placements"] = ["home"]
        for place in panel["placements"]:
            panel["order"][place] = _next_order(doc, place)
        panel["next_refresh"] = time.time()
        return copy.deepcopy(panel)

    panel = mutate(update)[1]
    start_refresh(pid)
    return panel

def update_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    refresh_weather = {"value": False}

    def update(doc):
        current = doc["settings"]
        merged = {**current, **{k: v for k, v in changes.items() if k in current and k != "location"}}
        if isinstance(changes.get("layout"), dict):
            merged["layout"] = _layout().clean_layout(changes["layout"], base=current.get("layout"))
        if isinstance(changes.get("location"), dict):
            loc = {**current["location"], **changes["location"]}
            if loc.get("mode") != current["location"].get("mode") or loc.get("lat") != current["location"].get("lat"):
                refresh_weather["value"] = True
            merged["location"] = loc
        if changes.get("units") and changes.get("units") != current.get("units"):
            refresh_weather["value"] = True
        lang_changed = "lang" in changes and changes["lang"] != current.get("lang")
        doc["settings"] = _clean_settings(merged)
        settings = doc["settings"]
        for panel in active(doc):
            if panel["source"]["type"] in TYPED_FETCHERS or panel["source"]["type"] in ("countdown", "feed", "local", "timers"):
                if lang_changed:
                    title, spec = typed_spec(panel["kind"], panel["source"]["params"], settings, panel.get("options"))
                    panel["spec"] = spec
                    if not panel.get("title_custom") and "{{" not in title:
                        panel["title"] = title
                    if (panel.get("wall") or {}).get("status") == "needs_location:weather":
                        panel["wall"] = _location_wall(settings)
                    target = panel["source"]["params"].get("target")
                    if panel["kind"] == "countdown" and target:
                        panel["data"].update(countdown_values(str(target), settings))
                    elif panel["status"] == "live" and panel.get("next_refresh") is not None:
                        panel["next_refresh"] = 0
            if panel["kind"] == "weather" and refresh_weather["value"] and not panel["source"]["params"].get("city"):
                if settings["location"]["mode"] in ("auto", "manual"):
                    panel["wall"] = None
                    panel["status"] = "live" if panel.get("data") else "building"
                    panel["next_refresh"] = 0
                else:
                    panel["status"] = "setup"
                    panel["wall"] = _location_wall(settings)
        return copy.deepcopy(settings)

    doc, settings = mutate(update)
    if refresh_weather["value"]:
        for panel in active(doc):
            if panel["kind"] == "weather" and panel["status"] != "setup":
                start_refresh(panel["id"], rerun=True)
    return settings

async def locate_by_ip() -> Dict[str, Any]:
    """Согласие получено: определить город по IP и сохранить как «авто»."""
    found = await ip_location()
    return await asyncio.to_thread(update_settings, {"location": {
        "mode": "auto", "name": found.get("name") or "", "lat": found["lat"], "lon": found["lon"],
        "country": found.get("country") or "", "timezone": found.get("timezone") or ""}})

TOOL_NAMES = ("panel_create", "panel_list", "panel_read", "panel_update_data", "panel_edit",
              "panel_refresh", "panel_remove", "panel_revive", "panel_templates")

def _tool_view(panel: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": panel["id"], "title": panel["title"], "kind": panel["kind"], "status": panel["status"],
            "placements": panel["placements"], "size": panel["size"], "cadence": panel["cadence"],
            "last_refresh": panel.get("last_refresh"), "wall": panel.get("wall"),
            "bindings": spec_bindings(panel["spec"])}

async def run_tool(tool: str, args: Dict[str, Any], *, model: str = "") -> Dict[str, Any]:
    """Вызов инструмента панелей из чата (memuai-tool). Ошибки — обычный результат для модели."""
    bind_loop()
    args = args if isinstance(args, dict) else {}
    try:
        if tool == "panel_templates":
            schema = _options().public_schema()
            return {"tool": tool, "templates": [{"kind": t["kind"], "params": [f["key"] for f in t["fields"]],
                                                 "source": t["source"], "min_minutes": floor_minutes(t["kind"]),
                                                 "options": {o["key"]: ([c[0] for c in o["choices"]] if o.get("choices") else o["type"])
                                                             for o in schema.get(t["kind"], []) if o["type"] != "map"}}
                                                for t in TEMPLATES]}
        if tool == "panel_list":
            doc = await asyncio.to_thread(load)
            return {"tool": tool, "panels": [_tool_view(p) for p in active(doc)],
                    "dismissed": [{"id": p["id"], "title": p["title"]} for p in dismissed(doc)][:8]}
        pid = str(args.get("id") or args.get("panel_id") or "")
        if tool == "panel_read":
            doc = await asyncio.to_thread(load)
            panel = _find(doc, pid)
            return {"tool": tool, "panel": {**_tool_view(panel), "spec": panel["spec"], "data": panel["data"],
                                            "sources": panel["sources"], "runbook": panel["runbook"],
                                            "subject": panel.get("subject")}}
        if tool == "panel_create":
            placement = args.get("placement") or args.get("placements") or "home"
            placements = placement if isinstance(placement, list) else [p.strip() for p in str(placement).split(",")]
            template = str(args.get("template") or "").strip().lower()
            blocks = args.get("spec") or args.get("blocks")
            if isinstance(blocks, list):
                blocks = {"blocks": blocks, **({"mini": args["mini"]} if isinstance(args.get("mini"), dict) else {})}
            subject = str(args.get("subject") or args.get("brief") or "").strip()
            if not template and not subject and not blocks:
                return {"tool": tool, "error": "Give a subject (what the panel shows) or a template."}
            params = args.get("params") if isinstance(args.get("params"), dict) else {}
            panel = await create_and_fill(
                wait=45.0, template=template if template in KINDS and template != "custom" else "",
                params=params, subject=subject, title=str(args.get("title") or ""), spec=blocks,
                values=args.get("values"), sources=args.get("sources"), runbook=args.get("runbook"),
                cadence=args.get("cadence"), rhythm=str(args.get("rhythm") or ""), use=str(args.get("use") or ""),
                placements=placements, size=str(args.get("size") or ""), model=model,
                options=args.get("options") if isinstance(args.get("options"), dict) else None)
            still = panel["status"] == "building" or pid_busy(panel["id"])
            return {"tool": tool, "status": panel["status"], "panel": _tool_view(panel),
                    "note": ("The panel card is on its way: it is still gathering data (the card shows its progress). "
                             "The chat shows a preview with pin buttons — tell the user it is being built, do not "
                             "describe its layout and do not invent its values.") if still else
                    ("The panel is ready and filled; the user sees it as a preview card. Do not repeat its values."
                     if panel["status"] == "live" else
                     "The panel was created but its first refresh hit a wall: " + str((panel.get("wall") or {}).get("message")
                                                                                    or panel.get("error") or "") +
                     " — tell the user plainly what blocks it.")}
        if tool == "panel_update_data":
            panel = await asyncio.to_thread(update_data, pid, args.get("values"), args.get("sources"))
            return {"tool": tool, "panel": _tool_view(panel)}
        if tool == "panel_edit":
            changes = {k: args[k] for k in ("title", "size", "placements", "cadence", "rhythm", "runbook", "subject",
                                            "params", "options") if k in args}
            if args.get("spec") or args.get("blocks"):
                spec = args.get("spec") or {"blocks": args.get("blocks")}
                if isinstance(spec, list):
                    spec = {"blocks": spec}
                changes["spec"] = spec
            if isinstance(changes.get("placements"), str):
                changes["placements"] = [p.strip() for p in changes["placements"].split(",")]
            panel = await asyncio.to_thread(update_panel, pid, changes)
            return {"tool": tool, "panel": _tool_view(panel)}
        if tool == "panel_refresh":
            await asyncio.to_thread(load)
            start_refresh(pid)
            await wait_refresh(pid, 60)
            doc = await asyncio.to_thread(load)
            panel = _find(doc, pid)
            return {"tool": tool, "panel": {**_tool_view(panel), "data": panel["data"]},
                    "refreshing": pid in _TASKS}
        if tool == "panel_remove":
            await asyncio.to_thread(dismiss, pid)
            return {"tool": tool, "removed": pid, "note": "The user can bring it back from «+ Панель → Недавно убранные»."}
        if tool == "panel_revive":
            panel = await asyncio.to_thread(revive, pid)
            return {"tool": tool, "panel": _tool_view(panel)}
        return {"tool": tool, "error": "Unknown panel tool"}
    except PanelError as exc:
        return {"tool": tool, "error": str(exc)}
