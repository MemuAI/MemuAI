# -*- coding: utf-8 -*-
"""Round 35: data and integration triggers of «Планирование» (``{"type": "watch", "source": …}``).

The Round 29 rule engine (:mod:`app.schedule_rules`) knows clocks and local events. This module
adds triggers that *watch a source* and start the task when something new happens there:

* MemuAI data — a panel value crosses a threshold (with hysteresis), the Proxy quota runs low,
  a new model appears in the Proxy;
* integrations — Gmail (a search query), Google Calendar (N minutes before an event, an event
  created / changed), GitHub (issue, PR, review requested, Actions failed, release), Google
  Sheets (a new row, a cell changed), Google Drive (a file added), Notion (a page / database
  changed);
* the web — an RSS/Atom feed item, a web page changed (optionally only a CSS selector),
  a webhook call to MemuAI Proxy;
* the computer — a new file in a local folder, disk / memory / CPU thresholds;
* logic — «all of» (every sub-trigger fired within N minutes).

How it plugs in (the engine stays pure):

* ``schedule_rules._normalize_trigger`` delegates ``type == "watch"`` to :func:`normalize`;
  ``trigger_text`` → :func:`describe`; ``decide`` takes one pending event per tick with
  :func:`take` (the event payload rides in ``rule_state.event_payload`` → ``last_trigger``).
* ``schedule_runtime.prepare_tick`` (MemuAI Proxy) calls :func:`tick`: it polls the watches that
  are due **in worker threads, without blocking the scheduler** (results of a slow poll are
  collected on a later tick), keeps per-watch cursors in ``memuai/schedule_triggers.json``
  (no duplicate fires, survives restarts), applies exponential backoff on errors, posts a
  «trigger is broken» item to «Входящие» after 3 failures in a row and returns the pending
  events for the sample (``sample["watch"][task_id][key]``).
* ``scheduler._run_task`` calls :func:`apply_trigger`: ``task["trigger"]`` = the payload (flows read
  it as ``{{trigger.*}}``); a prompt with ``{{trigger.field}}`` placeholders gets them substituted,
  otherwise a short human-readable context block is appended.
* the window process: ``register_routes(app)`` — catalog, «Проверить сейчас» (a dry-run poll),
  health of every watch, integration pickers.

A first poll only records a baseline (existing mail, issues, rows… never fire); threshold sources
fire on the first poll when the condition already holds (``initial`` = true) and re-arm only after
the value leaves the zone by the hysteresis.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import difflib
import fnmatch
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from .schedule_rules import RuleError, _days, _hhmm, _int

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - the engine itself does not need FastAPI
    Request = Any  # type: ignore[misc,assignment]

log = logging.getLogger("memuai.schedule_triggers")

STATE_FILE = "schedule_triggers.json"
MAX_PENDING = 20
PENDING_TTL = 24 * 3600
WAIT_TTL = 4 * 24 * 3600
MAX_PAYLOAD = 8000
FAILS_TO_INBOX = 3
MAX_BACKOFF = 6 * 3600
TICK_WAIT = 6.0
WORKERS = 4

IN_PROXY = False

SOURCES: Dict[str, Dict[str, Any]] = {
    "gmail": {"cat": "mail", "icon": "mail", "service": "gmail", "every": 2, "min": 1,
              "ru": "Новое письмо", "en": "New email"},
    "calendar_before": {"cat": "mail", "icon": "calendar", "service": "google_calendar", "every": 10, "min": 2,
                        "ru": "За N минут до встречи", "en": "N minutes before an event"},
    "calendar_created": {"cat": "mail", "icon": "calendar", "service": "google_calendar", "every": 5, "min": 2,
                         "ru": "Новое событие в календаре", "en": "New calendar event"},
    "calendar_changed": {"cat": "mail", "icon": "calendar", "service": "google_calendar", "every": 5, "min": 2,
                         "ru": "Событие календаря изменено", "en": "Calendar event changed"},
    "github_issue": {"cat": "github", "icon": "github", "service": "github", "every": 5, "min": 2,
                     "ru": "Новый issue", "en": "New issue"},
    "github_pr": {"cat": "github", "icon": "github", "service": "github", "every": 5, "min": 2,
                  "ru": "Новый pull request", "en": "New pull request"},
    "github_review": {"cat": "github", "icon": "github", "service": "github", "every": 5, "min": 2,
                      "ru": "Меня попросили о ревью", "en": "Review requested from me"},
    "github_actions": {"cat": "github", "icon": "github", "service": "github", "every": 5, "min": 2,
                       "ru": "Упал GitHub Actions", "en": "GitHub Actions failed"},
    "github_release": {"cat": "github", "icon": "github", "service": "github", "every": 15, "min": 5,
                       "ru": "Новый релиз", "en": "New release"},
    "sheets_row": {"cat": "google", "icon": "sheet", "service": "google_sheets", "every": 5, "min": 1,
                   "ru": "Новая строка в Google Таблице", "en": "New row in a Google Sheet"},
    "sheets_cell": {"cat": "google", "icon": "sheet", "service": "google_sheets", "every": 5, "min": 1,
                    "ru": "Ячейка Google Таблицы изменилась", "en": "A Google Sheets cell changed"},
    "drive_file": {"cat": "google", "icon": "drive", "service": "google_drive", "every": 5, "min": 2,
                   "ru": "Новый файл на Google Диске", "en": "New file on Google Drive"},
    "notion_page": {"cat": "notion", "icon": "notion", "service": "notion", "every": 5, "min": 2,
                    "ru": "Страница Notion изменилась", "en": "A Notion page changed"},
    "notion_db": {"cat": "notion", "icon": "notion", "service": "notion", "every": 5, "min": 2,
                  "ru": "Запись в базе Notion", "en": "A Notion database entry"},
    "rss": {"cat": "web", "icon": "rss", "service": "", "every": 30, "min": 5,
            "ru": "Новая запись RSS", "en": "New RSS item"},
    "web_page": {"cat": "web", "icon": "web", "service": "", "every": 60, "min": 5,
                 "ru": "Страница сайта изменилась", "en": "A web page changed"},
    "webhook": {"cat": "web", "icon": "webhook", "service": "", "every": 1, "min": 1,
                "ru": "Вызов вебхука", "en": "A webhook call"},
    "panel": {"cat": "memuai", "icon": "panel", "service": "", "every": 2, "min": 1,
              "ru": "Значение на панели", "en": "A panel value"},
    "quota_low": {"cat": "memuai", "icon": "quota", "service": "", "every": 5, "min": 2,
                  "ru": "Квота аккаунта заканчивается", "en": "An account quota runs low"},
    "proxy_model": {"cat": "memuai", "icon": "model", "service": "", "every": 15, "min": 5,
                    "ru": "Новая модель в Proxy", "en": "A new model in the Proxy"},
    "folder_file": {"cat": "system", "icon": "folder", "service": "", "every": 1, "min": 1,
                    "ru": "Новый файл в папке", "en": "A new file in a folder"},
    "system": {"cat": "system", "icon": "system", "service": "", "every": 2, "min": 1,
               "ru": "Состояние компьютера", "en": "Computer state"},
    "weather": {"cat": "weather", "icon": "weather", "service": "", "every": 20, "min": 10,
                "ru": "Погода изменилась", "en": "The weather changed"},
    "all": {"cat": "logic", "icon": "logic", "service": "", "every": 5, "min": 1,
            "ru": "Все условия вместе", "en": "All of these together"},
}

TYPE_IDS: Dict[str, str] = {
    "gmail": "gmail.new_email", "calendar_before": "calendar.before_event", "calendar_created": "calendar.new_event",
    "calendar_changed": "calendar.event_changed", "github_issue": "github.new_issue", "github_pr": "github.new_pr",
    "github_review": "github.review_requested", "github_actions": "github.actions_failed",
    "github_release": "github.new_release", "sheets_row": "sheets.new_row", "sheets_cell": "sheets.cell_changed",
    "drive_file": "drive.new_file", "notion_page": "notion.page_changed", "notion_db": "notion.db_entry",
    "rss": "rss.new_item", "web_page": "web.page_changed", "webhook": "webhook.call", "panel": "panel.value",
    "quota_low": "proxy.quota_low", "proxy_model": "proxy.new_model", "folder_file": "folder.new_file",
    "system": "system.state", "weather": "weather.changed", "all": "all.together",
}

def type_id(source: Any) -> str:
    """``gmail`` → ``gmail.new_email`` (unknown sources pass through)."""
    return TYPE_IDS.get(str(source or ""), str(source or ""))

CATEGORIES = [
    ("mail", "Почта и календарь", "Mail and calendar"),
    ("github", "GitHub", "GitHub"),
    ("google", "Google Таблицы и Диск", "Google Sheets and Drive"),
    ("notion", "Notion", "Notion"),
    ("web", "Сайты, RSS и вебхуки", "Websites, RSS and webhooks"),
    ("memuai", "Данные MemuAI", "MemuAI data"),
    ("system", "Компьютер и файлы", "Computer and files"),
    ("weather", "Погода", "Weather"),
    ("logic", "Составные", "Compound"),
]
OPS = ("gt", "ge", "lt", "le", "eq", "ne", "contains", "not_contains", "changed")
OP_TEXT = {"gt": (">", ">"), "ge": ("≥", "≥"), "lt": ("<", "<"), "le": ("≤", "≤"), "eq": ("=", "="),
           "ne": ("≠", "≠"), "contains": ("содержит", "contains"), "not_contains": ("не содержит", "does not contain"),
           "changed": ("изменилось", "changed")}
SYSTEM_METRICS = ("disk_free_pct", "disk_free_gb", "ram_pct", "cpu_pct", "battery_pct")
QUOTA_LEVELS = ("tight", "runs_out", "exhausted")
SERVICE_TITLES = {"gmail": "Gmail", "google_calendar": "Google Calendar", "google_drive": "Google Drive",
                  "google_sheets": "Google Sheets", "notion": "Notion", "github": "GitHub"}

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_NOTION_ID = re.compile(r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})")
_DRIVE_FOLDER = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")

def _t(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _err(ru: str, en: str) -> RuleError:
    return RuleError(ru, en)

def _text(value: Any, limit: int = 300) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:limit]

def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "нет")
    return bool(value)

def parse_number(value: Any) -> Optional[float]:
    """«1 234,5 ₽» / «-3.2%» / 42 → float; None when there is no number."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value or "").replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    match = re.search(r"[-−+]?\d+(?:[.,]\d+)*", text)
    if not match:
        return None
    raw = match.group(0).replace("−", "-")
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif raw.count(",") == 1:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        number = float(raw)
    except ValueError:
        return None
    return number if math.isfinite(number) else None

def _repo(value: Any, required: bool = True) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://github\.com/", "", text).strip("/")
    text = re.sub(r"\.git$", "", text)
    if not text:
        if required:
            raise _err("Укажите репозиторий GitHub в виде владелец/имя", "Enter the GitHub repository as owner/name")
        return ""
    parts = text.split("/")
    text = "/".join(parts[:2])
    if not _REPO_RE.match(text):
        raise _err(f"Неверный репозиторий «{text[:60]}» — нужно владелец/имя",
                   f"Invalid repository “{text[:60]}” — use owner/name")
    return text

def _sheet_id(value: Any) -> str:
    text = str(value or "").strip()
    match = _SHEET_ID.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    raise _err("Вставьте ссылку на Google Таблицу или её id", "Paste the Google Sheet link or its id")

def _notion_id(value: Any, what_ru: str, what_en: str) -> str:
    text = str(value or "").split("?")[0].split("#")[0]
    dashed = re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])", text)
    plain = re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])", text)
    found = (dashed or plain)
    if not found:
        raise _err(f"Вставьте ссылку на {what_ru} Notion или её id", f"Paste the Notion {what_en} link or its id")
    raw = found[-1].replace("-", "").lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

def _url(value: Any) -> str:
    text = str(value or "").strip()
    if text and not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(text) > 1000:
        raise _err("Укажите адрес страницы, например https://example.com", "Enter the page address, e.g. https://example.com")
    return text

def _compare_cfg(raw: Dict[str, Any], out: Dict[str, Any], *, default_op: str = "gt", numeric: bool = False) -> None:
    op = str(raw.get("op") or default_op).strip().lower()
    aliases = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "=": "eq", "==": "eq", "!=": "ne", "above": "gt",
               "below": "lt", "change": "changed", "any": "changed"}
    op = aliases.get(op, op)
    if op not in OPS:
        op = default_op
    out["op"] = op
    if op != "changed":
        value = raw.get("value")
        if value in (None, ""):
            raise _err("Укажите значение для сравнения", "Enter the value to compare with")
        if op in ("gt", "ge", "lt", "le") or numeric:
            number = parse_number(value)
            if number is None:
                raise _err("Для «больше/меньше» нужно число", "“Greater/less” needs a number")
            out["value"] = number
        else:
            out["value"] = _text(value, 200)
    hyst = parse_number(raw.get("hysteresis"))
    if hyst and hyst > 0:
        out["hysteresis"] = hyst
    out["initial"] = _bool(raw.get("initial"), True)

def _normalize_one(raw: Dict[str, Any], *, nested: bool = False) -> Dict[str, Any]:  # noqa: C901 - one flat table
    source = str(raw.get("source") or "").strip().lower()
    if source not in SOURCES:
        raise _err(f"Неизвестный источник «{source[:30]}»", f"Unknown trigger source “{source[:30]}”")
    if nested and source in ("all", "webhook"):
        raise _err("Этот источник нельзя вложить в «Все условия вместе»", "This source cannot be nested in “All of these”")
    meta = SOURCES[source]
    out: Dict[str, Any] = {"type": "watch", "source": source}
    if source == "gmail":
        query = _text(raw.get("query") or raw.get("q"), 300)
        if not query:
            raise _err("Укажите, какие письма ждать (например from:bank.ru)", "Say which emails to wait for (e.g. from:bank.com)")
        out["query"] = query
    elif source == "calendar_before":
        out["minutes"] = _int(raw.get("minutes"), 10, 0, 1440)
        out["calendar"] = _text(raw.get("calendar") or raw.get("calendar_id") or "primary", 200) or "primary"
        if _text(raw.get("match"), 120):
            out["match"] = _text(raw.get("match"), 120)
        out["skip_all_day"] = _bool(raw.get("skip_all_day"), True)
    elif source in ("calendar_created", "calendar_changed"):
        out["calendar"] = _text(raw.get("calendar") or raw.get("calendar_id") or "primary", 200) or "primary"
        if _text(raw.get("match"), 120):
            out["match"] = _text(raw.get("match"), 120)
    elif source.startswith("github_"):
        out["repo"] = _repo(raw.get("repo") or raw.get("repository"), required=source != "github_review")
        if source == "github_issue" and _text(raw.get("labels"), 120):
            out["labels"] = _text(raw.get("labels"), 120)
        if source == "github_actions":
            if _text(raw.get("branch"), 120):
                out["branch"] = _text(raw.get("branch"), 120)
            if _text(raw.get("workflow"), 120):
                out["workflow"] = _text(raw.get("workflow"), 120)
    elif source == "sheets_row":
        out["sheet"] = _sheet_id(raw.get("sheet") or raw.get("spreadsheet") or raw.get("spreadsheet_id"))
        out["tab"] = _text(raw.get("tab") or raw.get("range"), 100)
    elif source == "sheets_cell":
        out["sheet"] = _sheet_id(raw.get("sheet") or raw.get("spreadsheet") or raw.get("spreadsheet_id"))
        cell = _text(raw.get("cell") or raw.get("range"), 120)
        if not re.fullmatch(r"(?:.+!)?\$?[A-Za-z]{1,3}\$?\d{1,7}", cell):
            raise _err("Укажите ячейку, например B2 или Лист1!B2", "Enter a cell, e.g. B2 or Sheet1!B2")
        out["cell"] = cell
        _compare_cfg(raw, out, default_op="changed")
    elif source == "drive_file":
        folder = str(raw.get("folder") or raw.get("folder_id") or "").strip()
        match = _DRIVE_FOLDER.search(folder)
        folder = match.group(1) if match else folder
        if folder and not re.fullmatch(r"[A-Za-z0-9_-]{5,}", folder):
            raise _err("Неверная папка Google Диска", "Invalid Google Drive folder")
        if folder:
            out["folder"] = folder
        name = _text(raw.get("name_has") or raw.get("name"), 120)
        if name:
            out["name_has"] = name
    elif source == "notion_page":
        out["page"] = _notion_id(raw.get("page") or raw.get("page_id"), "страницу", "page")
    elif source == "notion_db":
        out["database"] = _notion_id(raw.get("database") or raw.get("database_id"), "базу данных", "database")
        mode = str(raw.get("mode") or "new").lower()
        out["mode"] = mode if mode in ("new", "changed", "any") else "new"
    elif source == "rss":
        out["url"] = _url(raw.get("url"))
        if _text(raw.get("match"), 120):
            out["match"] = _text(raw.get("match"), 120)
    elif source == "web_page":
        out["url"] = _url(raw.get("url"))
        selector = _text(raw.get("selector"), 200)
        if selector:
            out["selector"] = selector
        mode = str(raw.get("mode") or "changed").lower()
        out["mode"] = mode if mode in ("changed", "contains", "not_contains") else "changed"
        if out["mode"] != "changed":
            text = _text(raw.get("text") or raw.get("value"), 200)
            if not text:
                raise _err("Укажите текст, который нужно найти на странице", "Enter the text to look for on the page")
            out["text"] = text

            if raw.get("exact") is not None and not _bool(raw.get("exact"), True):
                out["exact"] = False
    elif source == "webhook":
        token = str(raw.get("token") or "").strip()
        out["token"] = token if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token) else secrets.token_urlsafe(18)
    elif source == "panel":
        panel_id = _text(raw.get("panel") or raw.get("panel_id"), 40)
        if not panel_id:
            raise _err("Выберите панель", "Choose a panel")
        out["panel"] = panel_id
        key = _text(raw.get("key") or raw.get("field"), 120)
        if not key:
            raise _err("Выберите значение панели", "Choose the panel value")
        out["key"] = key
        _compare_cfg(raw, out)
    elif source == "quota_low":
        out["account"] = _text(raw.get("account") or raw.get("account_id"), 120)
        level = str(raw.get("level") or "runs_out").lower()
        out["level"] = level if level in QUOTA_LEVELS else "runs_out"
    elif source == "proxy_model":
        if _text(raw.get("match"), 80):
            out["match"] = _text(raw.get("match"), 80)
    elif source == "folder_file":
        path = str(raw.get("path") or "").strip().strip('"')
        if not path or len(path) > 400 or "\n" in path:
            raise _err("Укажите папку для слежения", "Choose a folder to watch")
        out["path"] = path
        pattern = _text(raw.get("pattern"), 80)
        if pattern:
            out["pattern"] = pattern
        out["recursive"] = _bool(raw.get("recursive"), False)
    elif source == "system":
        metric = str(raw.get("metric") or "disk_free_pct").lower()
        out["metric"] = metric if metric in SYSTEM_METRICS else "disk_free_pct"
        if out["metric"].startswith("disk"):
            out["drive"] = _text(raw.get("drive") or ("C:\\" if os.name == "nt" else "/"), 120)
        default_op = "lt" if out["metric"] in ("disk_free_pct", "disk_free_gb", "battery_pct") else "gt"
        _compare_cfg(raw, out, default_op=default_op, numeric=True)
        if out["op"] in ("changed", "contains", "not_contains"):
            raise _err("Для показателя компьютера нужно «больше» или «меньше»", "A computer metric needs “greater” or “less”")
    elif source == "weather":
        _weather_cfg(raw, out)
    elif source == "all":
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        clean = [_normalize_one(item, nested=True) for item in items[:4] if isinstance(item, dict)]
        if len(clean) < 2:
            raise _err("Добавьте хотя бы два условия в «Все вместе»", "Add at least two triggers to “All of these”")
        out["items"] = clean
        out["within"] = _int(raw.get("within"), 60, 1, 7 * 1440)

    every = raw.get("every")
    if every not in (None, ""):
        out["every"] = _int(every, meta["every"], meta["min"], 1440)
    if source in ("gmail", "github_issue", "github_pr", "github_review", "sheets_row", "drive_file", "notion_db",
                  "rss", "folder_file", "calendar_created", "calendar_changed") and _bool(raw.get("each"), False):
        out["each"] = True
    if not nested:
        out["delay"] = _int(raw.get("delay"), 0, 0, 1440)
        if not out["delay"]:
            out.pop("delay")
        hours = raw.get("hours") if isinstance(raw.get("hours"), dict) else None
        if hours and (hours.get("from") or hours.get("to")):
            window = {"from": _hhmm(hours.get("from") or "09:00"), "to": _hhmm(hours.get("to") or "18:00"),
                      "days": _days(hours.get("days"))}
            if window["from"] == window["to"]:
                raise _err("Начало и конец окна совпадают", "The window starts and ends at the same time")
            if _bool(hours.get("wait")):
                window["wait"] = True
            out["hours"] = window
    return out

WEATHER_HYSTERESIS = {"temp": 1.0, "feels": 1.0, "wind": 5.0, "precip": 0.2, "precip_prob": 10.0, "cloud": 10.0,
                      "humidity": 5.0}

def _weather_cfg(raw: Dict[str, Any], out: Dict[str, Any]) -> None:
    from . import schedule_conds_ext as conds
    metric = str(raw.get("metric") or "rain").lower()
    if metric not in conds.WEATHER_METRICS:
        raise _err(f"Неизвестный показатель погоды «{metric[:20]}»", f"Unknown weather metric “{metric[:20]}”")
    out["metric"] = metric
    if metric in conds._NUMERIC_METRICS:
        _compare_cfg(raw, out, default_op="lt" if metric in ("temp", "feels") else "gt", numeric=True)
        if out["op"] not in ("gt", "ge", "lt", "le"):
            raise _err("Для погоды нужно «выше» или «ниже»", "The weather needs “above” or “below”")
        if "hysteresis" not in out:
            out["hysteresis"] = WEATHER_HYSTERESIS.get(metric, 0)
    lat, lon = parse_number(raw.get("lat")), parse_number(raw.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise _err("Выберите город для погоды", "Choose a city for the weather")
    out["lat"], out["lon"] = round(lat, 4), round(lon, 4)
    place = _text(raw.get("place"), 80)
    if place:
        out["place"] = place
    out["cooldown"] = _int(raw.get("cooldown"), 180, 0, 7 * 1440)
    out["initial"] = _bool(raw.get("initial"), False)

def normalize(raw: Any) -> Dict[str, Any]:
    """Validate a ``{"type": "watch", …}`` trigger (raises :class:`RuleError`)."""
    if not isinstance(raw, dict):
        raise _err("Неверный триггер", "Invalid trigger")
    return _normalize_one(raw)

def poll_every(trigger: Dict[str, Any]) -> int:
    meta = SOURCES.get(trigger.get("source"), {"every": 5, "min": 1})
    if trigger.get("source") == "all":
        return max(meta["min"], min(poll_every(item) for item in trigger.get("items") or [{}]))
    return _int(trigger.get("every"), meta["every"], meta["min"], 1440)

def services_of(trigger: Dict[str, Any]) -> List[str]:
    if trigger.get("source") == "all":
        out: List[str] = []
        for item in trigger.get("items") or []:
            out += [s for s in services_of(item) if s not in out]
        return out
    service = SOURCES.get(str(trigger.get("source")), {}).get("service")
    return [service] if service else []

def watch_key(task_id: Any, trigger: Dict[str, Any]) -> str:
    """Stable per task+trigger config: an edited trigger starts a new cursor (a new baseline)."""
    body = {k: v for k, v in trigger.items() if k not in ("delay", "hours", "every")}
    digest = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"{str(task_id)[:60]}:{digest}"

def _cmp_text(trigger: Dict[str, Any], lang: str) -> str:
    op = trigger.get("op") or "changed"
    word = OP_TEXT[op][1 if lang == "en" else 0]
    if op == "changed":
        return word
    value = trigger.get("value")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{word} {value}"

def _short(value: Any, size: int = 40) -> str:
    text = str(value or "")
    return text if len(text) <= size else text[: size - 1] + "…"

def describe(trigger: Dict[str, Any], lang: str = "ru", *, panels: Optional[Dict[str, str]] = None) -> str:  # noqa: C901
    en = lang == "en"
    s = trigger.get("source")
    if s == "gmail":
        text = f"when an email matching “{trigger.get('query')}” arrives" if en else \
            f"когда придёт письмо «{trigger.get('query')}»"
    elif s == "calendar_before":
        n = trigger.get("minutes", 10)
        extra = (f" “{trigger['match']}”" if en else f" «{trigger['match']}»") if trigger.get("match") else ""
        text = (f"{n} min before each calendar event{extra}" if en else f"за {n} мин до каждой встречи{extra}") if n else \
            (f"when a calendar event{extra} starts" if en else f"в начале каждой встречи{extra}")
    elif s in ("calendar_created", "calendar_changed"):
        extra = (f" “{trigger['match']}”" if en else f" «{trigger['match']}»") if trigger.get("match") else ""
        text = (("when a calendar event" + extra + (" is created" if s == "calendar_created" else " changes")) if en
                else ("когда в календаре появляется событие" + extra if s == "calendar_created"
                      else "когда меняется событие календаря" + extra))
    elif s == "github_issue":
        text = f"when a new issue opens in {trigger.get('repo')}" if en else f"когда в {trigger.get('repo')} новый issue"
        if trigger.get("labels"):
            text += f" ({trigger['labels']})"
    elif s == "github_pr":
        text = f"when a new pull request opens in {trigger.get('repo')}" if en else f"когда в {trigger.get('repo')} новый pull request"
    elif s == "github_review":
        where = trigger.get("repo")
        text = ("when my review is requested" + (f" in {where}" if where else "")) if en else \
            ("когда меня просят о ревью" + (f" в {where}" if where else ""))
    elif s == "github_actions":
        extra = f" ({trigger['branch']})" if trigger.get("branch") else ""
        wf = trigger.get("workflow")
        text = (f"when GitHub Actions{(' “' + wf + '”') if wf else ''} fails in {trigger.get('repo')}{extra}" if en
                else f"когда падает GitHub Actions{(' «' + wf + '»') if wf else ''} в {trigger.get('repo')}{extra}")
    elif s == "github_release":
        text = f"when {trigger.get('repo')} publishes a release" if en else f"когда в {trigger.get('repo')} выходит релиз"
    elif s == "sheets_row":
        text = "when a new row appears in the Google Sheet" if en else "когда в Google Таблице появляется строка"
        if trigger.get("tab"):
            text += f" ({trigger['tab']})"
    elif s == "sheets_cell":
        text = (f"when cell {trigger.get('cell')} {_cmp_text(trigger, lang)}" if en
                else f"когда ячейка {trigger.get('cell')} {_cmp_text(trigger, lang)}")
    elif s == "drive_file":
        text = "when a new file appears on Google Drive" if en else "когда на Google Диске появляется файл"
        if trigger.get("name_has"):
            text += f" “{trigger['name_has']}”" if en else f" «{trigger['name_has']}»"
    elif s == "notion_page":
        text = "when the Notion page changes" if en else "когда меняется страница Notion"
    elif s == "notion_db":
        mode = trigger.get("mode", "new")
        text = {"new": ("when a new entry appears in the Notion database", "когда в базе Notion новая запись"),
                "changed": ("when an entry of the Notion database changes", "когда меняется запись базы Notion"),
                "any": ("when the Notion database changes", "когда меняется база Notion")}[mode][0 if en else 1]
    elif s == "rss":
        host = urlparse(str(trigger.get("url"))).hostname or ""
        text = f"when {host} publishes a new item" if en else f"когда на {host} новая запись"
        if trigger.get("match"):
            text += f" with “{trigger['match']}”" if en else f" со словом «{trigger['match']}»"
    elif s == "web_page":
        host = urlparse(str(trigger.get("url"))).hostname or ""
        where = f" ({_short(trigger['selector'], 30)})" if trigger.get("selector") else ""
        mode = trigger.get("mode", "changed")
        if mode == "changed":
            text = f"when the page {host}{where} changes" if en else f"когда меняется страница {host}{where}"
        elif mode == "contains":
            text = (f"when “{trigger.get('text')}” appears on {host}{where}" if en
                    else f"когда на {host}{where} появится «{trigger.get('text')}»")
        else:
            text = (f"when “{trigger.get('text')}” disappears from {host}{where}" if en
                    else f"когда с {host}{where} пропадёт «{trigger.get('text')}»")
    elif s == "webhook":
        text = "when the webhook is called" if en else "при вызове вебхука"
    elif s == "panel":
        title = (panels or {}).get(str(trigger.get("panel"))) or _panel_title(str(trigger.get("panel")))
        name = f"{title} · {trigger.get('key')}" if title else str(trigger.get("key"))
        text = f"when “{name}” {_cmp_text(trigger, lang)}" if en else f"когда «{name}» {_cmp_text(trigger, lang)}"
    elif s == "quota_low":
        level = {"tight": ("is tight", "впритык"), "runs_out": ("will run out before the reset", "кончится до сброса"),
                 "exhausted": ("is exhausted", "исчерпана")}[trigger.get("level", "runs_out")]
        who = trigger.get("account") or ("any account" if en else "любого аккаунта")
        text = f"when the quota of {who} {level[0]}" if en else f"когда квота {who} {level[1]}"
    elif s == "proxy_model":
        text = "when a new model appears in the Proxy" if en else "когда в Proxy появляется новая модель"
        if trigger.get("match"):
            text += f" (“{trigger['match']}”)" if en else f" («{trigger['match']}»)"
    elif s == "folder_file":
        text = f"when a new file appears in {trigger.get('path')}" if en else f"когда в {trigger.get('path')} появляется файл"
        if trigger.get("pattern"):
            text += f" ({trigger['pattern']})"
    elif s == "system":
        names = {"disk_free_pct": ("free disk space, %", "свободное место на диске, %"),
                 "disk_free_gb": ("free disk space, GB", "свободное место на диске, ГБ"),
                 "ram_pct": ("memory use, %", "занятая память, %"), "cpu_pct": ("CPU load, %", "загрузка процессора, %"),
                 "battery_pct": ("battery, %", "заряд батареи, %")}[trigger.get("metric", "disk_free_pct")]
        text = f"when {names[0]} {_cmp_text(trigger, lang)}" if en else f"когда {names[1]} {_cmp_text(trigger, lang)}"
    elif s == "weather":
        text = _weather_text(trigger, en)
    elif s == "all":
        parts = [describe(item, lang, panels=panels) for item in trigger.get("items") or []]
        text = (" and ".join(parts) + f" (within {trigger.get('within')} min)") if en else \
            (" и ".join(parts) + f" (в пределах {trigger.get('within')} мин)")
    else:
        text = str(s)
    hours = trigger.get("hours")
    if hours:
        from .schedule_rules import _days_text
        text += (f", only {hours['from']}–{hours['to']} {_days_text(hours.get('days') or [], lang)}" if en
                 else f", только с {hours['from']} до {hours['to']} {_days_text(hours.get('days') or [], lang)}")
        if hours.get("wait"):
            text += " (events outside wait for it)" if en else " (события вне окна ждут его)"
    if trigger.get("delay"):
        text += f", {trigger['delay']} min later" if en else f", через {trigger['delay']} мин"
    return text

def _panel_title(panel_id: str) -> str:
    try:
        panel = _panels_by_id().get(panel_id)
    except Exception:
        return ""
    return str((panel or {}).get("title") or "")

def _compare(op: str, value: Any, target: Any) -> Optional[bool]:
    if op in ("gt", "ge", "lt", "le"):
        number = parse_number(value)
        if number is None or target is None:
            return None
        return {"gt": number > target, "ge": number >= target, "lt": number < target, "le": number <= target}[op]
    if op in ("eq", "ne"):
        a, b = parse_number(value), parse_number(target)
        same = (a == b) if (a is not None and b is not None and isinstance(target, (int, float))) else \
            str(value or "").strip().lower() == str(target or "").strip().lower()
        return same if op == "eq" else not same
    if op in ("contains", "not_contains"):
        hit = str(target or "").lower() in str(value or "").lower()
        return hit if op == "contains" else not hit
    return None

_NEGATIONS = {"нет", "не", "ни", "без", "no", "not", "never", "non", "without"}
_WORD = re.compile(r"[\w]+(?:[-'’][\w]+)*", re.UNICODE)

def _fold(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()

def text_found(haystack: Any, needle: Any, exact: bool = True) -> bool:
    """Round 35 (finish-c): «появился текст» matching. ``exact`` (default): case-insensitive whole words/phrase
    (``in stock`` ⊄ ``out of stock``) and negation-aware — a hit right after «нет / не / no / not / without…»
    does not count, so «В наличии» does not match «Нет в наличии» (unless the needle itself is negated).
    ``exact=False`` — the plain substring of earlier versions."""
    hay, word = _fold(haystack), _fold(needle)
    if not word:
        return False
    if not exact:
        return word in hay
    tokens = _WORD.findall(word)
    if not tokens:
        return word in hay
    negated_needle = tokens[0] in _NEGATIONS
    pattern = re.compile(r"(?<![\w])" + r"[^\w]+".join(re.escape(t) for t in tokens) + r"(?![\w])", re.UNICODE)
    for match in pattern.finditer(hay):
        before = _WORD.findall(hay[max(0, match.start() - 40):match.start()])
        if not negated_needle and before and before[-1] in _NEGATIONS:
            continue
        return True
    return False

def _rearmed(op: str, value: Any, target: Any, hyst: float) -> bool:
    """Has the value left the trigger zone far enough (hysteresis) to fire again?"""
    number = parse_number(value)
    if op in ("gt", "ge") and number is not None and target is not None:
        return number < target - hyst if hyst else not _compare(op, value, target)
    if op in ("lt", "le") and number is not None and target is not None:
        return number > target + hyst if hyst else not _compare(op, value, target)
    return _compare(op, value, target) is False

def threshold_step(cfg: Dict[str, Any], value: Any, cursor: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    """Edge-triggered comparison with hysteresis. Returns (fire, new cursor)."""
    op = cfg.get("op", "gt")
    target = cfg.get("value")
    hyst = float(cfg.get("hysteresis") or 0)
    first = cursor is None
    cur = dict(cursor or {})
    previous = cur.get("last")
    cur["last"] = value
    if value is None:
        return False, cur
    if op == "changed":
        base = cur.get("fired_value", previous)
        if first or base is None:
            cur["fired_value"] = value
            return False, cur
        if hyst:
            a, b = parse_number(value), parse_number(base)
            changed = a is not None and b is not None and abs(a - b) >= hyst
        else:
            changed = str(value) != str(base)
        if changed:
            cur["fired_value"] = value
        return changed, cur
    armed = cur.get("armed", True if (not first or cfg.get("initial", True)) else None)
    holds = _compare(op, value, target)
    if first and not cfg.get("initial", True):
        cur["armed"] = not holds
        return False, cur
    if armed and holds:
        cur["armed"] = False
        return True, cur
    if not armed and _rearmed(op, value, target, hyst):
        cur["armed"] = True
    elif armed is None:
        cur["armed"] = not holds
    return False, cur

def _seen_add(cursor: Dict[str, Any], ids: List[str], limit: int = 300) -> None:
    seen = list(cursor.get("seen") or [])
    for item in ids:
        if item not in seen:
            seen.append(item)
    cursor["seen"] = seen[-limit:]

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _ts(value: Any) -> Optional[float]:
    if not value:
        return None
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.fromisoformat(text).timestamp()
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return moment.timestamp() if moment.tzinfo else moment.timestamp()
    except ValueError:
        return None

def _local_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")

class PollResult:
    __slots__ = ("events", "cursor", "info", "next_check")

    def __init__(self, events: Optional[List[Dict[str, Any]]] = None, cursor: Optional[Dict[str, Any]] = None,
                 info: Optional[Dict[str, Any]] = None, next_check: Optional[float] = None) -> None:
        self.events = events or []
        self.cursor = cursor if cursor is not None else {}
        self.info = info or {}
        self.next_check = next_check

class SourceError(RuntimeError):
    """A poll failure the user can act on. ``kind``: connect (integration off) | auth | config | network | other."""

    def __init__(self, text: str, kind: str = "other", service: str = "") -> None:
        super().__init__(text)
        self.kind, self.service = kind, service

def _list_payload(source: str, items: List[Dict[str, Any]], each: bool, lang: str) -> List[Dict[str, Any]]:
    """New items → events: one per item (``each``) or one event that carries them all."""
    if not items:
        return []
    meta = SOURCES[source]
    label = meta["en"] if lang == "en" else meta["ru"]
    if each:
        return [{"source": source, "kind": label, **item, "items": [item], "count": 1} for item in items[:10]]
    first = items[0]
    title = first.get("title") or label
    if len(items) > 1:
        title = (f"{title} (+{len(items) - 1} more)" if lang == "en" else f"{title} (и ещё {len(items) - 1})")
    return [{"source": source, "kind": label, **first, "title": title, "items": items[:20], "count": len(items)}]

def _ig():
    from . import integrations
    return integrations

def _integration_call(service: str, fn: Callable[[], Any]) -> Any:
    ig = _ig()
    try:
        return fn()
    except getattr(ig, "IntegrationAccountError", ()) as exc:   # type: ignore[misc]
        raise SourceError(str(exc), "connect", service) from exc
    except getattr(ig, "IntegrationError", ()) as exc:          # type: ignore[misc]
        text = str(exc)
        kind = "connect" if re.search(r"не подключ|not connected|подключите|Connect ", text, re.I) else (
            "auth" if re.search(r"401|403|token|токен|scope|доступ", text, re.I) else "other")
        raise SourceError(text, kind, service) from exc

def google(service: str, method: str, url: str, **kwargs: Any) -> Any:
    return _integration_call(service, lambda: _ig()._google(service, method, url, **kwargs))

def github(method: str, path: str, **kwargs: Any) -> Any:
    return _integration_call("github", lambda: _ig()._github(method, path, **kwargs))

def notion(method: str, path: str, **kwargs: Any) -> Any:
    def call():
        from . import integrations_notion
        if not integrations_notion.connected():
            raise SourceError("Notion не подключён", "connect", "notion")
        return integrations_notion._request(method, path, **kwargs)
    try:
        return _integration_call("notion", call)
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - Notion raises its own errors
        raise SourceError(str(exc)[:300], "other", "notion") from exc

def service_status() -> Dict[str, bool]:
    """Connector → connected (no network)."""
    out: Dict[str, bool] = {}
    try:
        for row in _ig().status().get("services") or []:
            out[str(row.get("service"))] = bool(row.get("connected"))
    except Exception:
        pass
    return out

def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise SourceError(f"Адрес не найден: {host}", "network") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved \
                or address.is_multicast or address.is_unspecified:
            raise SourceError("Локальные и внутренние адреса не поддерживаются", "config")

def http_get(url: str, *, limit: int = 3_000_000) -> Tuple[str, str]:
    """GET a public page → (text, final url); redirects re-checked; patched in tests."""
    import httpx
    current = url
    with httpx.Client(timeout=20.0, follow_redirects=False, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MemuAI-Planning/3.5",
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "ru,en;q=0.9"}) as client:
        for _hop in range(6):
            host = urlparse(current).hostname or ""
            _public_host(host)
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise SourceError(f"Сайт не ответил: {type(exc).__name__}", "network") from exc
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                continue
            if response.status_code >= 400:
                raise SourceError(f"Сайт ответил {response.status_code}", "network")
            data = response.content[:limit]
            return data.decode(response.encoding or "utf-8", errors="replace"), current
    raise SourceError("Слишком много перенаправлений", "network")

class _Node:
    __slots__ = ("tag", "attrs", "parent", "children", "text")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_Node"]) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: List[Any] = []
        self.text = ""

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template", "svg"}

class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag.lower() not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag.lower():
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)

def _node_text(node: _Node) -> str:
    if node.tag in _SKIP:
        return ""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            parts.append(_node_text(child))
            if child.tag in ("p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "section", "article"):
                parts.append("\n")
    return "".join(parts)

_SIMPLE = re.compile(r"^([a-zA-Z][a-zA-Z0-9-]*|\*)?((?:[#.][A-Za-z0-9_-]+|\[[^\]]+\])*)$")

def _match_simple(node: _Node, simple: str) -> bool:
    match = _SIMPLE.match(simple)
    if not match:
        return False
    tag, rest = match.group(1), match.group(2) or ""
    if tag and tag != "*" and node.tag != tag.lower():
        return False
    for part in re.findall(r"[#.][A-Za-z0-9_-]+|\[[^\]]+\]", rest):
        if part[0] == "#" and node.attrs.get("id") != part[1:]:
            return False
        if part[0] == "." and part[1:] not in node.attrs.get("class", "").split():
            return False
        if part[0] == "[":
            body = part[1:-1]
            if "=" in body:
                name, value = body.split("=", 1)
                if node.attrs.get(name.strip().lower()) != value.strip().strip("\"'"):
                    return False
            elif body.strip().lower() not in node.attrs:
                return False
    return True

def select(html: str, selector: str) -> List[str]:
    """A small CSS subset (tag, #id, .class, [attr], [attr=v], descendant and «>») → texts of matches."""
    tree = _Tree()
    try:
        tree.feed(html)
    except Exception:  # noqa: BLE001 - broken markup: keep what was parsed
        pass
    results: List[str] = []
    for group in selector.split(","):
        steps = re.findall(r">|[^\s>]+", group.strip())
        if not steps:
            continue
        chain: List[Tuple[str, str]] = []
        rel = " "
        for step in steps:
            if step == ">":
                rel = ">"
                continue
            chain.append((rel, step))
            rel = " "

        def matches(node: _Node, index: int) -> bool:
            if not _match_simple(node, chain[index][1]):
                return False
            if index == 0:
                return True
            rel_here = chain[index][0]
            parent = node.parent
            while parent is not None and parent.tag != "#root":
                if matches(parent, index - 1):
                    return True
                if rel_here == ">":
                    return False
                parent = parent.parent
            return False

        stack = [tree.root]
        while stack:
            node = stack.pop()
            for child in reversed(node.children):
                if isinstance(child, _Node):
                    stack.append(child)
            if node.tag != "#root" and matches(node, len(chain) - 1):
                results.append(_node_text(node))
        if len(results) > 200:
            break
    return results

def page_text(html: str, selector: str = "") -> str:
    if selector:
        texts = select(html, selector)
    else:
        tree = _Tree()
        try:
            tree.feed(html)
        except Exception:  # noqa: BLE001
            pass
        body = next((c for c in _walk(tree.root) if c.tag == "body"), tree.root)
        texts = [_node_text(body)]
    joined = "\n".join(texts)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in joined.splitlines()]
    return "\n".join(line for line in lines if line)[:200_000]

def _walk(node: _Node):
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _walk(child)

def parse_feed(xml_text: str) -> List[Dict[str, str]]:
    """RSS 2.0 / Atom / RDF → [{id, title, url, text, date}] newest first as in the feed."""
    import xml.etree.ElementTree as ET
    text = re.sub(r"<!DOCTYPE[^>]*>", "", xml_text[:3_000_000], flags=re.I)
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise SourceError("Это не RSS/Atom-лента", "config") from exc
    items: List[Dict[str, str]] = []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    for node in root.iter():
        name = local(node.tag)
        if name not in ("item", "entry"):
            continue
        row: Dict[str, str] = {}
        for child in node:
            key = local(child.tag)
            if key == "title":
                row["title"] = _text("".join(child.itertext()), 300)
            elif key == "link":
                href = child.attrib.get("href") or (child.text or "")
                if href and (not row.get("url") or child.attrib.get("rel", "alternate") == "alternate"):
                    row["url"] = href.strip()
            elif key in ("guid", "id"):
                row["id"] = (child.text or "").strip()
            elif key in ("description", "summary", "content") and not row.get("text"):
                row["text"] = _text(re.sub(r"<[^>]+>", " ", "".join(child.itertext())), 600)
            elif key in ("pubdate", "published", "updated", "date") and not row.get("date"):
                row["date"] = (child.text or "").strip()
        row["id"] = row.get("id") or row.get("url") or row.get("title", "")
        if row["id"]:
            items.append(row)
    return items[:100]

def _panels_by_id() -> Dict[str, Dict[str, Any]]:
    from . import panels
    from .storage import load_json_with_recovery
    raw = load_json_with_recovery(panels._path(), {})
    doc = panels._normalize_document(raw)
    return {str(p.get("id")): p for p in doc.get("panels") or [] if isinstance(p, dict)}

def panel_value(panel: Dict[str, Any], key: str) -> Any:
    value: Any = panel.get("data") or {}
    for part in key.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    if isinstance(value, dict):
        for name in ("value", "text", "label"):
            if name in value:
                return value[name]
    return value

def panel_fields(panel: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scalar data keys of a panel (for the picker) with their current values."""
    out: List[Dict[str, Any]] = []

    def visit(prefix: str, value: Any, depth: int) -> None:
        if len(out) >= 40 or depth > 2:
            return
        if isinstance(value, dict):
            for key, inner in list(value.items())[:40]:
                visit(f"{prefix}.{key}" if prefix else str(key), inner, depth + 1)
        elif isinstance(value, (str, int, float, bool)) and not isinstance(value, bool) or isinstance(value, bool):
            out.append({"key": prefix, "value": value if not isinstance(value, str) else value[:80],
                        "numeric": parse_number(value) is not None})

    visit("", panel.get("data") or {}, 0)
    return out

def _quota_accounts() -> List[Dict[str, Any]]:
    try:
        from . import quota_planner
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for account in quota_planner._all_accounts():
            acc_id = str(getattr(account, "id", "") or "")
            if not acc_id:
                continue
            label = quota_planner._account_label(acc_id)
            rows.append({"id": acc_id, "name": label[0] if isinstance(label, tuple) else str(label),
                         "status": quota_planner.status(acc_id), "score": quota_planner.score(acc_id)})
    except Exception as exc:  # noqa: BLE001
        log.debug("quota accounts: %s", exc)
    return rows

def proxy_models() -> List[str]:
    if IN_PROXY:
        try:
            from .openai_api import listed_models
            return sorted(str(row.get("id")) for row in listed_models() if row.get("id"))
        except Exception as exc:  # noqa: BLE001
            log.debug("listed_models: %s", exc)
    import httpx
    base = os.environ.get("MEMUAI_PROXY_URL", "").rstrip("/")
    headers = {}
    if not base:
        try:
            from .cli_integrations import proxy_base_url, proxy_api_key
            base = proxy_base_url().rsplit("/v1", 1)[0]
            key = proxy_api_key()
            if key:
                headers["Authorization"] = "Bearer " + key
        except Exception:
            base = "http://127.0.0.1:8000"
    token = os.environ.get("MEMUAI_TOKEN", "")
    if token and "Authorization" not in headers:
        headers["Authorization"] = "Bearer " + token
    try:
        response = httpx.get(base + "/v1/models", headers=headers, timeout=10.0, trust_env=False)
        response.raise_for_status()
        return sorted(str(row.get("id")) for row in (response.json().get("data") or []) if row.get("id"))
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"MemuAI Proxy не отвечает ({type(exc).__name__})", "network") from exc

def system_metric(metric: str, drive: str = "") -> Optional[float]:
    try:
        import psutil
    except Exception:
        psutil = None  # type: ignore[assignment]
    if metric.startswith("disk"):
        import shutil
        try:
            usage = shutil.disk_usage(os.path.expandvars(drive or ("C:\\" if os.name == "nt" else "/")))
        except OSError as exc:
            raise SourceError(f"Диск не найден: {drive}", "config") from exc
        return round(usage.free / usage.total * 100, 1) if metric == "disk_free_pct" else round(usage.free / 2**30, 1)
    if psutil is None:
        return None
    if metric == "ram_pct":
        return float(psutil.virtual_memory().percent)
    if metric == "cpu_pct":
        return float(psutil.cpu_percent(interval=0.5))
    if metric == "battery_pct":
        battery = psutil.sensors_battery()
        return None if battery is None else float(battery.percent)
    return None

def _p_gmail(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    from .integrations_gmail import GMAIL_API
    query = cfg["query"]
    if "newer_than:" not in query and "after:" not in query:
        query += " newer_than:3d"
    data = google("gmail", "GET", GMAIL_API + "/messages", params={"q": query, "maxResults": 20})
    ids = [str(row.get("id")) for row in data.get("messages") or [] if row.get("id")]
    cur = dict(cursor or {})
    first = cursor is None
    seen = set(cur.get("seen") or [])
    fresh = [mid for mid in ids if mid not in seen]
    items: List[Dict[str, Any]] = []
    preview: List[Dict[str, Any]] = []
    for mid in (fresh if not first else ids[:3])[:10]:
        row = google("gmail", "GET", GMAIL_API + f"/messages/{mid}",
                     params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        heads = {h.get("name", "").lower(): h.get("value", "") for h in ((row.get("payload") or {}).get("headers") or [])}
        item = {"id": mid, "title": _text(heads.get("subject") or _t(lang, "(без темы)", "(no subject)"), 200),
                "from": _text(heads.get("from"), 200), "date": heads.get("date", ""),
                "text": _text(row.get("snippet"), 500), "url": f"https://mail.google.com/mail/u/0/#all/{mid}",
                "thread_id": row.get("threadId", "")}
        (preview if first else items).append(item)
    _seen_add(cur, ids)
    items.reverse()
    info = {"summary": _t(lang, f"Писем по запросу: {len(ids)}", f"Matching emails: {len(ids)}"),
            "preview": [{"title": i["title"], "sub": i["from"]} for i in (items or preview)[:3]]}
    return PollResult(_list_payload("gmail", items, cfg.get("each", False), lang), cur, info)

def _cal_events(cfg: Dict[str, Any], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from urllib.parse import quote
    cal = quote(cfg.get("calendar") or "primary", safe="")
    url = _ig().CALENDAR_API + f"/calendars/{cal}/events"
    data = google("google_calendar", "GET", url, params={"singleEvents": "true", "maxResults": 100, **params})
    rows = [row for row in data.get("items") or [] if isinstance(row, dict)]
    if cfg.get("match"):
        needle = cfg["match"].lower()
        rows = [r for r in rows if needle in str(r.get("summary") or "").lower()
                or needle in str(r.get("description") or "").lower()]
    return rows

def _event_item(row: Dict[str, Any], lang: str) -> Dict[str, Any]:
    start = row.get("start") or {}
    end = row.get("end") or {}
    start_ts = _ts(start.get("dateTime") or start.get("date"))
    link = row.get("hangoutLink") or next((e.get("uri") for e in ((row.get("conferenceData") or {}).get("entryPoints") or [])
                                          if e.get("entryPointType") == "video"), "")
    return {"id": str(row.get("id")), "title": _text(row.get("summary") or _t(lang, "(без названия)", "(no title)"), 200),
            "start": start.get("dateTime") or start.get("date") or "", "end": end.get("dateTime") or end.get("date") or "",
            "start_ts": start_ts, "location": _text(row.get("location"), 200), "text": _text(row.get("description"), 600),
            "url": row.get("htmlLink", ""), "meet": link or "",
            "attendees": [a.get("email") for a in (row.get("attendees") or [])[:20] if a.get("email")],
            "all_day": bool(start.get("date") and not start.get("dateTime"))}

def _p_calendar_before(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    lead = cfg.get("minutes", 10) * 60
    cur = dict(cursor or {})
    refresh = cur.get("fetched", 0) + poll_every(cfg) * 60 <= now or not isinstance(cur.get("upcoming"), list)
    if refresh:
        rows = _cal_events(cfg, {"timeMin": _iso(now - 120), "timeMax": _iso(now + lead + 26 * 3600),
                                 "orderBy": "startTime"})
        upcoming = []
        for row in rows:
            if row.get("status") == "cancelled":
                continue
            item = _event_item(row, lang)
            if item["start_ts"] is None or (item["all_day"] and cfg.get("skip_all_day", True)):
                continue
            upcoming.append(item)
        cur["upcoming"] = upcoming[:60]
        cur["fetched"] = now
    fired = dict(cur.get("fired") or {})
    events: List[Dict[str, Any]] = []
    next_due: Optional[float] = None
    for item in cur.get("upcoming") or []:
        mark = f"{item['id']}@{item['start']}"
        due = item["start_ts"] - lead
        if mark in fired:
            continue
        if due <= now + 20:
            if item["start_ts"] >= now - 60:
                minutes = max(0, round((item["start_ts"] - now) / 60))
                when = _local_hm(item["start_ts"])
                payload = dict(item, minutes_left=minutes,
                               title=item["title"], kind=_t(lang, "Скоро встреча", "Event soon"),
                               text=(_t(lang, f"Начало в {when} (через {minutes} мин)", f"Starts at {when} (in {minutes} min)")
                                     + (f". {item['text']}" if item.get("text") else "")))
                events.append({"source": "calendar_before", **payload, "items": [item], "count": 1})
            fired[mark] = item["start_ts"]
        elif next_due is None or due < next_due:
            next_due = due
    cur["fired"] = {k: v for k, v in fired.items() if v > now - 2 * 86400}
    nxt = next((i for i in cur.get("upcoming") or [] if f"{i['id']}@{i['start']}" not in cur["fired"]), None)
    info = {"summary": (_t(lang, f"Ближайшая: «{nxt['title']}» в {_local_hm(nxt['start_ts'])}",
                           f"Next: “{nxt['title']}” at {_local_hm(nxt['start_ts'])}") if nxt
                        else _t(lang, "Ближайших встреч нет", "No upcoming events")),
            "preview": [{"title": i["title"], "sub": _local_hm(i["start_ts"])} for i in (cur.get("upcoming") or [])[:3]]}
    hint = min(next_due, cur["fetched"] + poll_every(cfg) * 60) if next_due else None
    return PollResult(events, cur, info, next_check=hint)

def _p_calendar_changes(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float,
                        created: bool) -> PollResult:
    cur = dict(cursor or {})
    since = cur.get("since")
    if cursor is None or not since:
        cur["since"] = now - 5
        return PollResult([], cur, {"summary": _t(lang, "Начато слежение", "Watching started")})
    rows = _cal_events(cfg, {"updatedMin": _iso(float(since) - 30), "showDeleted": "false", "orderBy": "updated"})
    seen = dict(cur.get("seen_upd") or {})
    items: List[Dict[str, Any]] = []
    newest = float(since)
    for row in rows:
        updated = _ts(row.get("updated")) or 0
        created_ts = _ts(row.get("created")) or 0
        newest = max(newest, updated)
        mark = str(row.get("id"))
        if seen.get(mark) == row.get("updated"):
            continue
        is_new = created_ts >= float(since) - 30
        if created != is_new or row.get("status") == "cancelled":
            seen[mark] = row.get("updated")
            continue
        seen[mark] = row.get("updated")
        item = _event_item(row, lang)
        item["text"] = (_t(lang, "Начало: ", "Starts: ") + _local_hm(item["start_ts"]) if item["start_ts"] else "") + \
            (f". {item['text']}" if item.get("text") else "")
        items.append(item)
    cur["since"] = max(newest, now - 3600)
    cur["seen_upd"] = dict(list(seen.items())[-300:])
    source = "calendar_created" if created else "calendar_changed"
    info = {"summary": _t(lang, f"Изменений: {len(items)}", f"Changes: {len(items)}"),
            "preview": [{"title": i["title"], "sub": i["start"]} for i in items[:3]]}
    return PollResult(_list_payload(source, items, cfg.get("each", False), lang), cur, info)

def _gh_user(row: Any) -> str:
    return str((row or {}).get("login") or "") if isinstance(row, dict) else ""

def _p_github_numbers(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, pulls: bool) -> PollResult:
    repo = cfg["repo"]
    params: Dict[str, Any] = {"state": "open", "sort": "created", "direction": "desc", "per_page": 20}
    if not pulls and cfg.get("labels"):
        params["labels"] = cfg["labels"]
    rows = github("GET", f"/repos/{repo}/{'pulls' if pulls else 'issues'}", params=params)
    rows = [r for r in rows or [] if isinstance(r, dict) and (pulls or "pull_request" not in r)]
    cur = dict(cursor or {})
    top = max([int(r.get("number") or 0) for r in rows] + [0])
    last = cur.get("max")
    cur["max"] = max(top, int(last or 0))
    items: List[Dict[str, Any]] = []
    if cursor is not None and last is not None:
        for row in sorted(rows, key=lambda r: int(r.get("number") or 0)):
            if int(row.get("number") or 0) > int(last):
                items.append({"id": str(row.get("number")), "number": row.get("number"), "title": _text(row.get("title"), 200),
                              "author": _gh_user(row.get("user")), "url": row.get("html_url", ""),
                              "text": _text(row.get("body"), 800), "repo": repo,
                              "labels": [l.get("name") for l in row.get("labels") or [] if isinstance(l, dict)][:10],
                              **({"branch": (row.get("head") or {}).get("ref", "")} if pulls else {})})
    source = "github_pr" if pulls else "github_issue"
    info = {"summary": _t(lang, f"Открыто: {len(rows)}", f"Open: {len(rows)}"),
            "preview": [{"title": f"#{r.get('number')} {_text(r.get('title'), 80)}", "sub": _gh_user(r.get("user"))} for r in rows[:3]]}
    return PollResult(_list_payload(source, items, cfg.get("each", False), lang), cur, info)

def _p_github_review(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    query = "is:pr is:open review-requested:@me" + (f" repo:{cfg['repo']}" if cfg.get("repo") else "")
    data = github("GET", "/search/issues", params={"q": query, "sort": "updated", "per_page": 20})
    rows = [r for r in (data or {}).get("items") or [] if isinstance(r, dict)]
    cur = dict(cursor or {})
    seen = set(cur.get("seen") or [])
    ids = [str(r.get("id")) for r in rows]
    items = []
    if cursor is not None:
        for row in reversed(rows):
            if str(row.get("id")) not in seen:
                repo = "/".join(str(row.get("repository_url") or "").split("/")[-2:])
                items.append({"id": str(row.get("id")), "number": row.get("number"), "title": _text(row.get("title"), 200),
                              "author": _gh_user(row.get("user")), "url": row.get("html_url", ""), "repo": repo,
                              "text": _text(row.get("body"), 800)})
    _seen_add(cur, ids)
    info = {"summary": _t(lang, f"Ждут вашего ревью: {len(rows)}", f"Waiting for your review: {len(rows)}"),
            "preview": [{"title": _text(r.get("title"), 80), "sub": _gh_user(r.get("user"))} for r in rows[:3]]}
    return PollResult(_list_payload("github_review", items, cfg.get("each", False), lang), cur, info)

def _p_github_actions(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    params: Dict[str, Any] = {"status": "completed", "per_page": 20}
    if cfg.get("branch"):
        params["branch"] = cfg["branch"]
    data = github("GET", f"/repos/{cfg['repo']}/actions/runs", params=params)
    runs = [r for r in (data or {}).get("workflow_runs") or [] if isinstance(r, dict)]
    if cfg.get("workflow"):
        needle = cfg["workflow"].lower()
        runs = [r for r in runs if needle in str(r.get("name") or "").lower() or needle in str(r.get("path") or "").lower()]
    cur = dict(cursor or {})
    last = cur.get("max")
    cur["max"] = max([int(r.get("id") or 0) for r in runs] + [int(last or 0)])
    items = []
    if cursor is not None and last is not None:
        for run in sorted(runs, key=lambda r: int(r.get("id") or 0)):
            if int(run.get("id") or 0) > int(last) and run.get("conclusion") in ("failure", "timed_out", "startup_failure"):
                commit = (run.get("head_commit") or {})
                items.append({"id": str(run.get("id")), "title": f"{run.get('name')} · {run.get('head_branch')}",
                              "workflow": run.get("name"), "branch": run.get("head_branch"),
                              "conclusion": run.get("conclusion"), "url": run.get("html_url", ""),
                              "actor": _gh_user(run.get("actor")), "commit": _text(commit.get("message"), 300),
                              "sha": str(run.get("head_sha") or "")[:12], "repo": cfg["repo"],
                              "text": _text(commit.get("message"), 300)})
    failed = [r for r in runs if r.get("conclusion") in ("failure", "timed_out", "startup_failure")]
    info = {"summary": _t(lang, f"Последних запусков: {len(runs)}, упавших: {len(failed)}",
                          f"Recent runs: {len(runs)}, failed: {len(failed)}"),
            "preview": [{"title": f"{r.get('name')} · {r.get('head_branch')}", "sub": str(r.get("conclusion"))} for r in runs[:3]]}
    return PollResult(_list_payload("github_actions", items, True, lang), cur, info)

def _p_github_release(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    rows = github("GET", f"/repos/{cfg['repo']}/releases", params={"per_page": 10})
    rows = [r for r in rows or [] if isinstance(r, dict) and not r.get("draft")]
    cur = dict(cursor or {})
    last = cur.get("max")
    cur["max"] = max([int(r.get("id") or 0) for r in rows] + [int(last or 0)])
    items = []
    if cursor is not None and last is not None:
        for row in sorted(rows, key=lambda r: int(r.get("id") or 0)):
            if int(row.get("id") or 0) > int(last):
                items.append({"id": str(row.get("id")), "title": _text(row.get("name") or row.get("tag_name"), 200),
                              "tag": row.get("tag_name"), "url": row.get("html_url", ""), "text": _text(row.get("body"), 1500),
                              "prerelease": bool(row.get("prerelease")), "repo": cfg["repo"]})
    info = {"summary": _t(lang, f"Последний релиз: {rows[0].get('tag_name')}", f"Latest release: {rows[0].get('tag_name')}")
            if rows else _t(lang, "Релизов пока нет", "No releases yet"),
            "preview": [{"title": _text(r.get("name") or r.get("tag_name"), 80), "sub": str(r.get("published_at") or "")[:10]} for r in rows[:3]]}
    return PollResult(_list_payload("github_release", items, True, lang), cur, info)

SHEETS_API = "https://sheets.googleapis.com/v4"

def _p_sheets_row(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    from urllib.parse import quote
    tab = cfg.get("tab") or ""
    rng = (f"'{tab}'" if tab and not tab.startswith("'") and "!" not in tab else tab) or "A:ZZ"
    if tab and "!" not in tab:
        rng += "!A:ZZ"
    data = google("google_sheets", "GET", f"{SHEETS_API}/spreadsheets/{cfg['sheet']}/values/{quote(rng, safe='')}",
                  params={"majorDimension": "ROWS"})
    values = [row for row in data.get("values") or []]
    count = len(values)
    while count and not any(str(c).strip() for c in values[count - 1]):
        count -= 1
    cur = dict(cursor or {})
    last = cur.get("rows")
    cur["rows"] = count
    header = [str(c).strip() for c in values[0]] if values else []
    items = []
    if cursor is not None and last is not None and count > int(last):
        for index in range(int(last), count):
            row = values[index]
            if not any(str(c).strip() for c in row):
                continue
            fields = {header[i] if i < len(header) and header[i] else f"col{i + 1}": row[i] for i in range(len(row))}
            items.append({"id": str(index + 1), "row": index + 1, "values": row[:50], "fields": fields,
                          "title": _text(" · ".join(str(c) for c in row if str(c).strip()), 200),
                          "url": f"https://docs.google.com/spreadsheets/d/{cfg['sheet']}/edit"})
    elif cursor is not None and last is not None and count < int(last):
        cur["rows"] = count
    info = {"summary": _t(lang, f"Строк: {count}", f"Rows: {count}"),
            "preview": [{"title": _text(" · ".join(str(c) for c in r), 80), "sub": f"#{i + 1}"}
                        for i, r in list(enumerate(values))[-3:]] if values else []}
    return PollResult(_list_payload("sheets_row", items, cfg.get("each", False), lang), cur, info)

def _p_sheets_cell(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    from urllib.parse import quote
    data = google("google_sheets", "GET", f"{SHEETS_API}/spreadsheets/{cfg['sheet']}/values/{quote(cfg['cell'], safe='')}")
    rows = data.get("values") or []
    value = rows[0][0] if rows and rows[0] else ""
    fire, cur = threshold_step(cfg, value, cursor)
    events = []
    if fire:
        prev = (cursor or {}).get("last")
        events.append({"source": "sheets_cell", "kind": _t(lang, "Ячейка изменилась", "Cell changed"),
                       "title": f"{cfg['cell']} = {value}", "value": value, "previous": prev, "cell": cfg["cell"],
                       "text": _t(lang, f"Было: {prev}", f"Was: {prev}") if prev not in (None, "") else "",
                       "url": f"https://docs.google.com/spreadsheets/d/{cfg['sheet']}/edit", "count": 1})
    return PollResult(events, cur, {"summary": f"{cfg['cell']} = {value}", "value": value})

def _p_drive_file(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    cur = dict(cursor or {})
    since = cur.get("since")
    clauses = ["trashed = false", "mimeType != 'application/vnd.google-apps.folder'"]
    if cfg.get("folder"):
        clauses.append(f"'{cfg['folder']}' in parents")
    if cfg.get("name_has"):
        clauses.append("name contains '" + cfg["name_has"].replace("\\", "\\\\").replace("'", "\\'") + "'")
    if since:
        clauses.append(f"createdTime > '{_iso(float(since) - 120)}'")
    data = google("google_drive", "GET", _ig().DRIVE_API + "/files",
                  params={"q": " and ".join(clauses), "orderBy": "createdTime desc", "pageSize": 30,
                          "fields": "files(id,name,mimeType,createdTime,webViewLink,size,owners(displayName))",
                          "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    files = [f for f in data.get("files") or [] if isinstance(f, dict)]
    seen = set(cur.get("seen") or [])
    items = []
    if cursor is not None and since:
        for row in reversed(files):
            if str(row.get("id")) not in seen:
                items.append({"id": str(row.get("id")), "title": _text(row.get("name"), 200), "mime": row.get("mimeType"),
                              "url": row.get("webViewLink", ""), "created": row.get("createdTime"),
                              "owner": ((row.get("owners") or [{}])[0] or {}).get("displayName", "")})
    _seen_add(cur, [str(f.get("id")) for f in files])
    newest = max([_ts(f.get("createdTime")) or 0 for f in files] + [float(since or 0)])
    cur["since"] = max(newest, float(since or 0)) if since else now
    info = {"summary": _t(lang, f"Новых файлов: {len(items)}", f"New files: {len(items)}"),
            "preview": [{"title": _text(f.get("name"), 80), "sub": str(f.get("createdTime") or "")[:16]} for f in files[:3]]}
    return PollResult(_list_payload("drive_file", items, cfg.get("each", False), lang), cur, info)

def _notion_title(obj: Dict[str, Any]) -> str:
    try:
        from .integrations_notion import _title
        return _title(obj)
    except Exception:
        return ""

def _p_notion_page(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    page = notion("GET", f"/pages/{cfg['page']}")
    edited = str(page.get("last_edited_time") or "")
    title = _notion_title(page) or _t(lang, "Страница Notion", "Notion page")
    cur = dict(cursor or {})
    last = cur.get("edited")
    cur["edited"] = edited
    events = []
    if cursor is not None and last and edited and edited != last:
        events.append({"source": "notion_page", "kind": SOURCES["notion_page"]["en" if lang == "en" else "ru"],
                       "title": title, "url": page.get("url", ""), "edited": edited,
                       "by": ((page.get("last_edited_by") or {}).get("id") or ""), "count": 1})
    return PollResult(events, cur, {"summary": _t(lang, f"«{title}», изменена {edited[:16].replace('T', ' ')}",
                                                  f"“{title}”, edited {edited[:16].replace('T', ' ')}")})

def _p_notion_db(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    cur = dict(cursor or {})
    since = cur.get("since")
    body: Dict[str, Any] = {"page_size": 30, "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}
    if since:
        body["filter"] = {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": _iso(float(since) - 120)}}
    data = notion("POST", f"/databases/{cfg['database']}/query", json_body=body)
    rows = [r for r in data.get("results") or [] if isinstance(r, dict)]
    seen = dict(cur.get("seen_edit") or {})
    items = []
    if cursor is not None and since:
        for row in reversed(rows):
            rid, edited = str(row.get("id")), str(row.get("last_edited_time") or "")
            if seen.get(rid) == edited:
                continue
            created_ts = _ts(row.get("created_time")) or 0
            is_new = created_ts >= float(since) - 120 and rid not in seen
            if cfg.get("mode") == "new" and not is_new or cfg.get("mode") == "changed" and is_new:
                seen[rid] = edited
                continue
            seen[rid] = edited
            items.append({"id": rid, "title": _notion_title(row) or _t(lang, "Запись", "Entry"), "url": row.get("url", ""),
                          "new": is_new, "edited": edited})
    for row in rows:
        seen[str(row.get("id"))] = str(row.get("last_edited_time") or "")
    cur["seen_edit"] = dict(list(seen.items())[-400:])
    newest = max([_ts(r.get("last_edited_time")) or 0 for r in rows] + [float(since or 0)])
    cur["since"] = newest if since else now
    info = {"summary": _t(lang, f"Изменённых записей: {len(rows)}", f"Changed entries: {len(rows)}"),
            "preview": [{"title": _notion_title(r) or "—", "sub": str(r.get("last_edited_time") or "")[:16]} for r in rows[:3]]}
    return PollResult(_list_payload("notion_db", items, cfg.get("each", False), lang), cur, info)

def _p_rss(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    text, _final = http_get(cfg["url"])
    entries = parse_feed(text)
    if cfg.get("match"):
        needle = cfg["match"].lower()
        matching = [e for e in entries if needle in (e.get("title", "") + " " + e.get("text", "")).lower()]
    else:
        matching = entries
    cur = dict(cursor or {})
    seen = set(cur.get("seen") or [])
    items = []
    if cursor is not None:
        for entry in reversed(matching):
            if entry["id"] not in seen:
                items.append({"id": entry["id"], "title": entry.get("title") or entry.get("url", ""),
                              "url": entry.get("url", ""), "text": entry.get("text", ""), "date": entry.get("date", "")})
    _seen_add(cur, [e["id"] for e in entries], limit=400)
    info = {"summary": _t(lang, f"Записей в ленте: {len(entries)}", f"Feed items: {len(entries)}"),
            "preview": [{"title": e.get("title", ""), "sub": e.get("date", "")[:16]} for e in matching[:3]]}
    return PollResult(_list_payload("rss", items, cfg.get("each", False), lang), cur, info)

def _p_web_page(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    html, final = http_get(cfg["url"])
    text = page_text(html, cfg.get("selector", ""))
    if cfg.get("selector") and not text:
        raise SourceError(_t(lang, "Селектор ничего не нашёл на странице", "The selector matched nothing on the page"), "config")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    cur = dict(cursor or {})
    events = []
    mode = cfg.get("mode", "changed")
    host = urlparse(cfg["url"]).hostname or ""
    if mode == "changed":
        if cursor is not None and cur.get("hash") and cur["hash"] != digest:
            old = str(cur.get("text") or "").splitlines()
            new = text.splitlines()
            diff = [line for line in difflib.unified_diff(old, new, lineterm="", n=0)
                    if not line.startswith(("---", "+++", "@@"))][:40]
            added = [l[1:] for l in diff if l.startswith("+")]
            removed = [l[1:] for l in diff if l.startswith("-")]
            events.append({"source": "web_page", "kind": SOURCES["web_page"]["en" if lang == "en" else "ru"],
                           "title": _t(lang, f"Изменилась страница {host}", f"{host} changed"), "url": final,
                           "added": added[:20], "removed": removed[:20], "diff": "\n".join(diff)[:3000],
                           "text": _text(" / ".join(added[:5]) or " / ".join(removed[:5]), 600), "count": 1})
        cur["hash"] = digest
        cur["text"] = text[:20000]
    else:
        hit = text_found(text, cfg["text"], cfg.get("exact", True) is not False)
        fire, state = threshold_step({"op": "eq", "value": 1 if mode == "contains" else 0, "initial": True},
                                     1 if hit else 0, cursor)
        cur.update(state)
        cur.pop("last", None)
        if fire:
            events.append({"source": "web_page", "kind": SOURCES["web_page"]["en" if lang == "en" else "ru"],
                           "title": (_t(lang, f"На {host} появилось «{cfg['text']}»", f"“{cfg['text']}” appeared on {host}")
                                     if mode == "contains" else
                                     _t(lang, f"С {host} пропало «{cfg['text']}»", f"“{cfg['text']}” is gone from {host}")),
                           "url": final, "text": _text(text, 600), "count": 1})
    return PollResult(events, cur, {"summary": _t(lang, f"Текста на странице: {len(text)} симв.", f"Page text: {len(text)} chars"),
                                    "preview": [{"title": _text(text, 120), "sub": cfg.get("selector", "")}] if text else []})

def _p_panel(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    panel = _panels_by_id().get(cfg["panel"])
    if not panel:
        raise SourceError(_t(lang, "Панель удалена", "The panel was deleted"), "config")
    value = panel_value(panel, cfg["key"])
    if value is None:
        raise SourceError(_t(lang, f"У панели нет значения «{cfg['key']}»", f"The panel has no value “{cfg['key']}”"), "config")
    fire, cur = threshold_step(cfg, value, cursor)
    title = str(panel.get("title") or "")
    events = []
    if fire:
        prev = (cursor or {}).get("last")
        events.append({"source": "panel", "kind": SOURCES["panel"]["en" if lang == "en" else "ru"],
                       "title": f"{title}: {cfg['key']} = {value}", "panel": title, "key": cfg["key"], "value": value,
                       "previous": prev, "text": (_t(lang, f"Было: {prev}", f"Was: {prev}") if prev is not None else ""),
                       "count": 1})
    stale = panel.get("last_refresh")
    info = {"summary": f"{title} · {cfg['key']} = {value}", "value": value}
    if isinstance(stale, (int, float)) and time.time() - stale > 6 * 3600:
        info["note"] = _t(lang, "Панель давно не обновлялась — значение может быть старым",
                          "The panel has not refreshed for a while — the value may be old")
    return PollResult(events, cur, info)

def _p_quota(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    accounts = _quota_accounts()
    if cfg.get("account"):
        accounts = [a for a in accounts if a["id"] == cfg["account"] or a["name"] == cfg["account"]]
    order = {"ok": 0, "insufficient": 0, "no_reset": 0, "tight": 1, "runs_out": 2, "exhausted": 3}
    need = order[cfg.get("level", "runs_out")]
    cur = dict(cursor or {})
    armed = dict(cur.get("armed") or {})
    events = []
    for acc in accounts:
        level = order.get(str(acc.get("status") or ""), 0)
        bad = level >= need
        was_armed = armed.get(acc["id"], True)
        if bad and was_armed:
            armed[acc["id"]] = False
            events.append({"source": "quota_low", "kind": SOURCES["quota_low"]["en" if lang == "en" else "ru"],
                           "title": _t(lang, f"Квота {acc['name']}: {acc.get('status')}", f"Quota of {acc['name']}: {acc.get('status')}"),
                           "account": acc["id"], "account_name": acc["name"], "status": acc.get("status"),
                           "score": acc.get("score"), "count": 1})
        elif not bad and level == 0:
            armed[acc["id"]] = True
    cur["armed"] = armed
    info = {"summary": _t(lang, f"Аккаунтов с данными о квоте: {len(accounts)}", f"Accounts with quota data: {len(accounts)}"),
            "preview": [{"title": a["name"], "sub": str(a.get("status") or "—")} for a in accounts[:3]]}
    return PollResult(events, cur, info)

def _p_proxy_model(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    models = proxy_models()
    if cfg.get("match"):
        needle = cfg["match"].lower()
        models = [m for m in models if needle in m.lower()]
    cur = dict(cursor or {})
    known = set(cur.get("models") or [])
    fresh = [m for m in models if m not in known] if cursor is not None else []
    cur["models"] = sorted(set(models) | known)[-3000:] if cursor is not None else models[-3000:]
    events = []
    if fresh:
        events.append({"source": "proxy_model", "kind": SOURCES["proxy_model"]["en" if lang == "en" else "ru"],
                       "title": ", ".join(fresh[:5]) + (f" (+{len(fresh) - 5})" if len(fresh) > 5 else ""),
                       "models": fresh[:50], "count": len(fresh)})
    return PollResult(events, cur, {"summary": _t(lang, f"Моделей: {len(models)}", f"Models: {len(models)}"),
                                    "preview": [{"title": m, "sub": ""} for m in models[-3:]]})

def _p_folder(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    root = Path(os.path.expandvars(os.path.expanduser(cfg["path"])))
    if not root.is_dir():
        raise SourceError(_t(lang, f"Папка не найдена: {root}", f"Folder not found: {root}"), "config")
    pattern = cfg.get("pattern") or ""
    found: Dict[str, Tuple[int, float]] = {}
    walker = root.rglob("*") if cfg.get("recursive") else root.iterdir()
    for index, path in enumerate(walker):
        if index > 5000:
            break
        try:
            if not path.is_file() or path.name.startswith("~$") or path.suffix.lower() in (".crdownload", ".part", ".tmp", ".partial"):
                continue
            if pattern and not any(fnmatch.fnmatch(path.name.lower(), p.strip().lower()) for p in pattern.split(";") if p.strip()):
                continue
            stat = path.stat()
        except OSError:
            continue
        found[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime)
    cur = dict(cursor or {})
    known = set(cur.get("files") or [])
    waiting = dict(cur.get("waiting") or {})
    items = []
    if cursor is not None:
        for name, (size, mtime) in sorted(found.items(), key=lambda kv: kv[1][1]):
            if name in known:
                continue

            if waiting.get(name) != size or now - mtime < 2:
                waiting[name] = size
                continue
            waiting.pop(name, None)
            known.add(name)
            items.append({"id": name, "title": Path(name).name, "path": str(root / name), "size": size,
                          "url": "", "text": str(root / name)})
    else:
        known = set(found)
    cur["files"] = sorted(n for n in known if n in found)[-5000:]
    cur["waiting"] = {k: v for k, v in waiting.items() if k in found}
    info = {"summary": _t(lang, f"Файлов: {len(found)}", f"Files: {len(found)}")}
    hint = now + 5 if cur["waiting"] else None
    return PollResult(_list_payload("folder_file", items, cfg.get("each", False), lang), cur, info, next_check=hint)

def _p_system(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:
    value = system_metric(cfg["metric"], cfg.get("drive", ""))
    if value is None:
        raise SourceError(_t(lang, "Этот компьютер не может измерить показатель", "This computer cannot measure it"), "config")
    fire, cur = threshold_step(cfg, value, cursor)
    events = []
    if fire:
        events.append({"source": "system", "kind": SOURCES["system"]["en" if lang == "en" else "ru"],
                       "title": describe(cfg, lang).replace("когда ", "").replace("when ", "") + f" ({value})",
                       "metric": cfg["metric"], "value": value, "count": 1})
    return PollResult(events, cur, {"summary": f"{cfg['metric']} = {value}", "value": value})

_WEATHER_STARTS = {"rain": ("rain starts", "начнётся дождь"), "snow": ("snow starts", "пойдёт снег"),
                   "clear": ("the sky clears", "прояснится")}
_WEATHER_STARTED = {"rain": ("Начался дождь", "Rain started"), "snow": ("Пошёл снег", "Snow started"),
                    "clear": ("Прояснилось", "The sky cleared")}

def _weather_text(trigger: Dict[str, Any], en: bool) -> str:
    from . import schedule_conds_ext as conds
    metric = trigger.get("metric", "rain")
    place = trigger.get("place") or f"{trigger.get('lat')}, {trigger.get('lon')}"
    if metric in _WEATHER_STARTS:
        what = _WEATHER_STARTS[metric][0 if en else 1]
    else:
        ru, eng, unit = conds._METRIC[metric]
        if en:
            unit = unit.replace(" мм", " mm").replace(" км/ч", " km/h")
        sign = OP_TEXT.get(trigger.get("op", "gt"), (">", ">"))[0]
        what = f"{eng if en else ru} becomes {sign} {_fmt_weather(trigger.get('value'))}{unit}" if en else \
            f"{ru} станет {sign} {_fmt_weather(trigger.get('value'))}{unit}"
    return f"when {what} ({place})" if en else f"когда {what} ({place})"

def _fmt_weather(value: Any) -> str:
    number = parse_number(value)
    return "?" if number is None else (str(int(number)) if float(number).is_integer() else str(number))

def _p_weather(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    from . import schedule_conds_ext as conds
    data = conds.weather_now(f"{round(cfg['lat'], 2)},{round(cfg['lon'], 2)}", cfg["lat"], cfg["lon"], now)
    if not isinstance(data, dict) or data.get(cfg["metric"]) is None:
        raise SourceError(_t(lang, "Нет данных о погоде (нет интернета?)", "No weather data (offline?)"), "network")
    raw = data[cfg["metric"]]
    boolean = cfg["metric"] not in conds._NUMERIC_METRICS
    value = (1 if raw else 0) if boolean else float(raw)
    step_cfg = {"op": "eq", "value": 1, "initial": cfg.get("initial", False)} if boolean else cfg
    holds = bool(raw) if boolean else _compare(cfg["op"], value, cfg["value"])
    fire, cur = threshold_step(step_cfg, value, cursor)
    cooldown = int(cfg.get("cooldown") or 0) * 60
    if fire and cooldown and cur.get("fired_at") and now - float(cur["fired_at"]) < cooldown:
        fire = False
    events = []
    temp = data.get("temp")
    summary = (f"{_fmt_weather(temp)}°C" if temp is not None else "") + (
        (_t(lang, ", дождь", ", rain") if data.get("rain") else "") + (_t(lang, ", снег", ", snow") if data.get("snow") else ""))
    if fire:
        cur["fired_at"] = now
        place = cfg.get("place") or f"{cfg['lat']}, {cfg['lon']}"
        if cfg["metric"] in _WEATHER_STARTED:
            title = _WEATHER_STARTED[cfg["metric"]][1 if lang == "en" else 0] + f" ({place})"
        else:
            title = _weather_text(cfg, lang == "en").replace("когда ", "").replace("when ", "").replace(
                " станет ", " ").replace(" becomes ", " ")
            title = title[:1].upper() + title[1:]
        events.append({"source": "weather", "kind": SOURCES["weather"]["en" if lang == "en" else "ru"],
                       "title": title + (f" — {summary.lstrip(', ')}" if summary else ""), "text": summary.lstrip(", "),
                       "metric": cfg["metric"], "value": raw, "place": place, "temp": temp,
                       "precip_prob": data.get("precip_prob"), "wind": data.get("wind"), "count": 1})
    info = {"summary": _t(lang, "Сейчас: ", "Now: ") + (summary.lstrip(", ") or "—"), "value": raw, "holds": holds}
    return PollResult(events, cur, info)

def _p_webhook(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str) -> PollResult:

    return PollResult([], dict(cursor or {}), {"summary": _t(lang, "Ждёт вызова", "Waiting for a call")})

def _p_all(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> PollResult:
    cur = dict(cursor or {})
    subs = list(cur.get("subs") or [None] * len(cfg["items"]))
    subs += [None] * (len(cfg["items"]) - len(subs))
    hits = list(cur.get("hits") or [None] * len(cfg["items"]))
    hits += [None] * (len(cfg["items"]) - len(hits))
    hints: List[float] = []
    for index, item in enumerate(cfg["items"]):
        sub = subs[index] or {}
        if sub.get("next", 0) > now and sub.get("cursor") is not None:
            continue
        result = poll_source(item, sub.get("cursor"), lang, now)
        subs[index] = {"cursor": result.cursor, "next": result.next_check or now + poll_every(item) * 60}
        hints.append(subs[index]["next"])
        if result.events:
            hits[index] = {"at": now, "title": result.events[-1].get("title", ""), "payload": _clip(result.events[-1], 2000)}
    window = cfg["within"] * 60
    hits = [h if h and now - h["at"] <= window else None for h in hits]
    events = []
    if all(hits):
        events.append({"source": "all", "kind": SOURCES["all"]["en" if lang == "en" else "ru"],
                       "title": " + ".join(h["title"] for h in hits if h), "parts": [h["payload"] for h in hits if h], "count": 1})
        hits = [None] * len(hits)
    cur["subs"], cur["hits"] = subs, hits
    done = sum(1 for h in hits if h)
    return PollResult(events, cur, {"summary": _t(lang, f"Сработало {done} из {len(hits)}", f"{done} of {len(hits)} fired")},
                      next_check=min(hints) if hints else None)

def poll_source(cfg: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str = "ru",
                now: Optional[float] = None) -> PollResult:
    """One poll of one watch (sync; network inside). Raises :class:`SourceError`."""
    now = time.time() if now is None else now
    s = cfg.get("source")
    if s == "gmail":
        return _p_gmail(cfg, cursor, lang)
    if s == "calendar_before":
        return _p_calendar_before(cfg, cursor, lang, now)
    if s in ("calendar_created", "calendar_changed"):
        return _p_calendar_changes(cfg, cursor, lang, now, s == "calendar_created")
    if s in ("github_issue", "github_pr"):
        return _p_github_numbers(cfg, cursor, lang, s == "github_pr")
    if s == "github_review":
        return _p_github_review(cfg, cursor, lang)
    if s == "github_actions":
        return _p_github_actions(cfg, cursor, lang)
    if s == "github_release":
        return _p_github_release(cfg, cursor, lang)
    if s == "sheets_row":
        return _p_sheets_row(cfg, cursor, lang)
    if s == "sheets_cell":
        return _p_sheets_cell(cfg, cursor, lang)
    if s == "drive_file":
        return _p_drive_file(cfg, cursor, lang, now)
    if s == "notion_page":
        return _p_notion_page(cfg, cursor, lang)
    if s == "notion_db":
        return _p_notion_db(cfg, cursor, lang, now)
    if s == "rss":
        return _p_rss(cfg, cursor, lang)
    if s == "web_page":
        return _p_web_page(cfg, cursor, lang)
    if s == "panel":
        return _p_panel(cfg, cursor, lang)
    if s == "quota_low":
        return _p_quota(cfg, cursor, lang)
    if s == "proxy_model":
        return _p_proxy_model(cfg, cursor, lang)
    if s == "folder_file":
        return _p_folder(cfg, cursor, lang, now)
    if s == "system":
        return _p_system(cfg, cursor, lang)
    if s == "weather":
        return _p_weather(cfg, cursor, lang, now)
    if s == "webhook":
        return _p_webhook(cfg, cursor, lang)
    if s == "all":
        return _p_all(cfg, cursor, lang, now)
    raise SourceError(f"unknown source {s}", "config")

def _clip(payload: Dict[str, Any], limit: int = MAX_PAYLOAD) -> Dict[str, Any]:
    """Keep payloads small: they are stored in schedules.json until the run starts."""
    data = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    while len(json.dumps(data, ensure_ascii=False)) > limit:
        if isinstance(data.get("items"), list) and len(data["items"]) > 1:
            data["items"] = data["items"][: max(1, len(data["items"]) // 2)]
            continue
        longest = max((k for k, v in data.items() if isinstance(v, str)), key=lambda k: len(data[k]), default=None)
        if longest and len(data[longest]) > 200:
            data[longest] = data[longest][: len(data[longest]) // 2] + "…"
            continue
        for key in ("diff", "parts", "items", "removed", "added", "attendees", "models", "fields", "values"):
            if key in data:
                data.pop(key)
                break
        else:
            break
    return data

def _in_hours(trigger: Dict[str, Any], now: float) -> bool:
    hours = trigger.get("hours")
    if not hours:
        return True
    from .schedule_rules import _in_time
    return _in_time({"from": hours["from"], "to": hours["to"], "days": hours.get("days") or []}, now)

def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)

def state_path() -> Path:
    return _memu_dir() / STATE_FILE

def load_state() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    try:
        data = load_json_with_recovery(state_path(), {"v": 1, "watches": {}}, validator=lambda v: isinstance(v, dict))
    except Exception:
        data = {}
    if not isinstance(data.get("watches"), dict):
        data["watches"] = {}
    return data

def mutate_state(fn: Callable[[Dict[str, Dict[str, Any]]], Any]) -> Any:
    from .storage import mutate_json
    box: Dict[str, Any] = {}

    def update(raw: Any):
        data = raw if isinstance(raw, dict) else {}
        fresh = data.get("v") != 1 or not isinstance(data.get("watches"), dict)
        data.setdefault("v", 1)
        if not isinstance(data.get("watches"), dict):
            data["watches"] = {}
        before = json.dumps(data["watches"], sort_keys=True, ensure_ascii=False, default=str)
        box["result"] = fn(data["watches"])

        changed = fresh or json.dumps(data["watches"], sort_keys=True, ensure_ascii=False, default=str) != before
        return changed, data

    mutate_json(state_path(), {"v": 1, "watches": {}}, update, backup=False,
                validator=lambda v: isinstance(v, dict), repair_invalid=True)
    return box.get("result")

def _task_enabled(task: Dict[str, Any]) -> bool:
    enabled = task.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(enabled)

def watches_of(tasks: List[Dict[str, Any]], *, enabled_only: bool = True) -> List[Tuple[Dict[str, Any], int, Dict[str, Any], str]]:
    out = []
    from .schedule_rules import normalize_rule
    for task in tasks:
        if not isinstance(task.get("rule"), dict) or (enabled_only and not _task_enabled(task)):
            continue
        try:
            rule = normalize_rule(task["rule"])
        except RuleError:
            continue
        for index, trigger in enumerate(rule["when"]):
            if trigger.get("type") == "watch":
                out.append((task, index, trigger, watch_key(task.get("id"), trigger)))
    return out

_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None
_INFLIGHT: Dict[str, concurrent.futures.Future] = {}
_LOCK = threading.Lock()

def _pool() -> concurrent.futures.ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="memu-watch")
    return _POOL

def _run_poll(trigger: Dict[str, Any], cursor: Optional[Dict[str, Any]], lang: str, now: float) -> Dict[str, Any]:
    try:
        result = poll_source(trigger, cursor, lang, now)
        return {"ok": True, "events": [_clip(e) for e in result.events], "cursor": result.cursor, "info": result.info,
                "next_check": result.next_check, "at": now}
    except SourceError as exc:
        return {"ok": False, "error": str(exc)[:400], "kind": exc.kind, "service": exc.service, "at": now}
    except Exception as exc:  # noqa: BLE001 - a broken source never stops the scheduler
        log.warning("trigger poll %s failed: %s", trigger.get("source"), exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400], "kind": "other", "service": "", "at": now}

def _apply(entry: Dict[str, Any], result: Dict[str, Any], trigger: Dict[str, Any], task: Dict[str, Any],
           now: float, notes: List[Tuple[str, str, Dict[str, Any]]]) -> None:
    every = poll_every(trigger) * 60
    entry["last_check"] = result["at"]
    if result["ok"]:
        entry["cursor"] = result["cursor"]
        entry["info"] = result.get("info") or {}
        entry["ok_at"] = result["at"]
        if entry.get("fails", 0) >= FAILS_TO_INBOX:
            notes.append(("resolve", entry.get("key", ""), {}))
        entry["fails"] = 0
        entry.pop("error", None)
        entry.pop("error_kind", None)
        entry.pop("service", None)
        hint = result.get("next_check")
        entry["next_check"] = min(float(hint), result["at"] + every) if hint else result["at"] + every
        entry["next_check"] = max(entry["next_check"], result["at"] + 5)
        events = result.get("events") or []
        if events and not _in_hours(trigger, now) and not (trigger.get("hours") or {}).get("wait"):
            entry["skipped"] = int(entry.get("skipped") or 0) + len(events)
            events = []
        for payload in events:
            entry["seq"] = int(entry.get("seq") or 0) + 1
            entry.setdefault("pending", []).append({"seq": entry["seq"], "at": now, "payload": payload})
            entry["fired"] = int(entry.get("fired") or 0) + 1
            entry["last_fire"] = now
            entry["last_title"] = str(payload.get("title") or "")[:200]
        entry["pending"] = (entry.get("pending") or [])[-MAX_PENDING:]
    else:
        entry["fails"] = int(entry.get("fails") or 0) + 1
        entry["error"] = result.get("error", "")
        entry["error_kind"] = result.get("kind", "other")
        entry["service"] = result.get("service", "")
        entry["error_at"] = result["at"]
        backoff = min(MAX_BACKOFF, every * (2 ** min(entry["fails"] - 1, 8)))
        if entry["error_kind"] == "connect":
            backoff = max(backoff, 15 * 60)
        entry["next_check"] = result["at"] + backoff
        if entry["fails"] == FAILS_TO_INBOX or (entry["error_kind"] == "connect" and entry["fails"] == 1):
            notes.append(("post", entry.get("key", ""), {"task": str(task.get("name") or ""), "task_id": str(task.get("id")),
                                                          "error": entry["error"], "kind": entry["error_kind"],
                                                          "service": entry["service"], "trigger": trigger}))

def _inbox_notes(notes: List[Tuple[str, str, Dict[str, Any]]], lang: str) -> None:
    if not notes:
        return
    try:
        from . import inbox
    except Exception:
        return
    for action, key, data in notes:
        try:
            if action == "resolve":
                inbox.resolve("trigger:" + key)
                continue
            what = describe(data["trigger"], lang)
            service = SERVICE_TITLES.get(data.get("service") or "", "")
            title = (_t(lang, f"Подключите {service} для задачи «{data['task']}»", f"Connect {service} for the task “{data['task']}”")
                     if data["kind"] == "connect" and service else
                     _t(lang, f"Триггер задачи «{data['task']}» не работает", f"The trigger of “{data['task']}” is failing"))
            inbox.post("schedule_trigger", title, f"{what}\n{data['error']}"[:600], source="schedule",
                       ref={"task_id": data["task_id"], "trigger": key}, dedupe_key="trigger:" + key, severity="warn",
                       actions=[{"id": "open", "label": _t(lang, "Открыть «Планирование»", "Open Planning"), "kind": "ui"}],
                       group="notify", meta={"title_en": title, "service": data.get("service") or ""})
        except Exception as exc:  # noqa: BLE001
            log.debug("inbox note failed: %s", exc)

async def tick(tasks: List[Dict[str, Any]], now: float, lang: str = "ru") -> Dict[str, Any]:
    """Poll the due watches (non-blocking beyond TICK_WAIT) → ``{task_id: {key: [pending events]}}``."""
    global IN_PROXY
    IN_PROXY = True
    rows = watches_of(tasks)
    if not rows and not _INFLIGHT:
        if state_path().exists():
            await asyncio.to_thread(_gc, set(), {})
        return {}
    by_key = {key: (task, idx, trigger) for task, idx, trigger, key in rows}
    consumed = {key: int(((task.get("rule_state") or {}).get("watch_seq") or {}).get(key) or 0)
                for task, _idx, _trigger, key in rows}
    state = await asyncio.to_thread(load_state)
    watches = state["watches"]
    started: List[str] = []
    with _LOCK:
        for key, (task, _idx, trigger) in by_key.items():
            if key in _INFLIGHT:
                continue
            entry = watches.get(key) or {}
            if trigger.get("source") == "webhook" and entry:
                continue
            if float(entry.get("next_check") or 0) > now and "cursor" in entry:
                continue
            _INFLIGHT[key] = _pool().submit(_run_poll, trigger, entry.get("cursor") if "cursor" in entry else None, lang, now)
            started.append(key)
    if started:
        futures = [_INFLIGHT[k] for k in started]
        await asyncio.to_thread(concurrent.futures.wait, futures, TICK_WAIT)
    finished: Dict[str, Dict[str, Any]] = {}
    with _LOCK:
        for key, future in list(_INFLIGHT.items()):
            if future.done():
                _INFLIGHT.pop(key, None)
                try:
                    finished[key] = future.result()
                except Exception as exc:  # noqa: BLE001
                    finished[key] = {"ok": False, "error": str(exc), "kind": "other", "at": now}
    notes: List[Tuple[str, str, Dict[str, Any]]] = []

    def update(all_watches: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        for key, result in finished.items():
            if key not in by_key:
                continue
            task, idx, trigger = by_key[key]
            entry = all_watches.setdefault(key, {"key": key, "created": now})
            entry.update(key=key, task_id=str(task.get("id")), index=idx, source=trigger.get("source"))
            _apply(entry, result, trigger, task, now, notes)
        out: Dict[str, Any] = {}
        for key, (task, idx, trigger) in by_key.items():
            entry = all_watches.get(key)
            if not entry:
                continue
            entry["index"] = idx
            waits = bool((trigger.get("hours") or {}).get("wait"))
            ttl = WAIT_TTL if waits else PENDING_TTL
            pending = [p for p in entry.get("pending") or []
                       if int(p.get("seq") or 0) > consumed.get(key, 0) and now - float(p.get("at") or now) < ttl]
            entry["pending"] = pending
            if pending and waits and not _in_hours(trigger, now):
                continue
            if pending:
                out.setdefault(str(task.get("id")), {})[key] = pending
        for key in [k for k in all_watches if k not in by_key and not k.startswith("_")]:
            all_watches.pop(key, None)
        return out

    pending = await asyncio.to_thread(mutate_state, update)
    await asyncio.to_thread(_inbox_notes, notes, lang)
    return pending or {}

def _gc(keep: set, _unused: Dict[str, Any]) -> None:
    def update(all_watches: Dict[str, Dict[str, Any]]) -> None:
        for key in [k for k in all_watches if k not in keep]:
            all_watches.pop(key, None)
    mutate_state(update)

def next_due() -> Optional[float]:
    """The earliest ``next_check`` of a watch (the scheduler may wake for it)."""
    try:
        values = [float(e.get("next_check") or 0) for e in load_state()["watches"].values() if isinstance(e, dict)]
    except Exception:
        return None
    values = [v for v in values if v > 0]
    return min(values) if values else None

def take(task: Dict[str, Any], trigger: Dict[str, Any], sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The oldest unconsumed event of this watch (marks it consumed in ``rule_state.watch_seq``)."""
    key = watch_key(task.get("id"), trigger)
    rows = ((sample.get("watch") or {}).get(str(task.get("id"))) or {}).get(key) or []
    state = task.setdefault("rule_state", {})
    seen = state.setdefault("watch_seq", {})
    done = int(seen.get(key) or 0)
    for row in sorted(rows, key=lambda r: int(r.get("seq") or 0)):
        seq = int(row.get("seq") or 0)
        if seq > done:
            seen[key] = seq
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

            return dict(payload, fired_at=row.get("at"), type=type_id(payload.get("source") or trigger.get("source")))
    return None

def _flat(payload: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flat(value, name + "."))
        elif isinstance(value, list):
            out[name] = ", ".join(str(v) if not isinstance(v, dict) else str(v.get("title") or v) for v in value[:20])
        elif value is not None:
            out[name] = str(value)
    return out

_PLACEHOLDER = re.compile(r"\{\{\s*trigger(?:\.([A-Za-z0-9_.]+))?\s*\}\}")

def render(text: str, payload: Dict[str, Any]) -> str:
    flat = _flat(payload)

    def sub(match: re.Match) -> str:
        name = match.group(1)
        if not name:
            return context_text(payload, "ru", compact=True)
        return flat.get(name, "")

    return _PLACEHOLDER.sub(sub, text)

def context_text(payload: Dict[str, Any], lang: str = "ru", *, compact: bool = False) -> str:
    """A short human-readable description of the trigger event for the model / notification."""
    labels = {"from": ("От", "From"), "author": ("Автор", "Author"), "start": ("Начало", "Start"), "location": ("Место", "Location"),
              "url": ("Ссылка", "Link"), "meet": ("Видеовстреча", "Video call"), "branch": ("Ветка", "Branch"),
              "commit": ("Коммит", "Commit"), "value": ("Значение", "Value"), "previous": ("Было", "Was"),
              "repo": ("Репозиторий", "Repository"), "path": ("Файл", "File"), "date": ("Дата", "Date")}
    lines = [f"{payload.get('kind') or payload.get('source')}: {payload.get('title') or ''}".strip()]
    for key, (ru, en) in labels.items():
        value = payload.get(key)
        if value not in (None, "", []):
            lines.append(f"{en if lang == 'en' else ru}: {value}")
    if payload.get("text"):
        lines.append(str(payload["text"])[:1500])
    if payload.get("diff"):
        lines.append(str(payload["diff"])[:2000])
    if payload.get("fields"):
        lines.append(json.dumps(payload["fields"], ensure_ascii=False)[:1500])
    items = payload.get("items") or []
    if len(items) > 1:
        lines.append((("All items:" if lang == "en" else "Все элементы:")))
        for item in items[:20]:
            lines.append("• " + " — ".join(str(item.get(k)) for k in ("title", "from", "author", "url") if item.get(k)))
    if compact:
        return "\n".join(lines)
    head = "[The trigger that started this task]" if lang == "en" else "[Событие, которое запустило задачу]"
    return head + "\n" + "\n".join(lines)

def apply_trigger(task: Dict[str, Any]) -> Dict[str, Any]:
    """Before a run: ``task["trigger"]`` = the event payload; the prompt gets it (placeholders or a context block)."""
    meta = task.get("_run_meta") if isinstance(task.get("_run_meta"), dict) else {}
    state = task.get("rule_state") if isinstance(task.get("rule_state"), dict) else {}
    payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else state.get("last_trigger")
    if not isinstance(payload, dict) or meta.get("trigger") != "event":
        return task
    out = dict(task)
    out["trigger"] = payload
    try:
        from .pgdata import load_preferences
        lang = "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        lang = "ru"
    prompt = str(task.get("prompt") or "")
    if _PLACEHOLDER.search(prompt):
        out["prompt"] = render(prompt, payload)
    elif str(task.get("kind") or "chat") in ("chat", "dfy", "memucode"):
        out["prompt"] = (prompt + "\n\n" + context_text(payload, lang)).strip()
    elif str(task.get("kind")) in ("notify", "reminder"):
        out["prompt"] = (prompt + "\n" + str(payload.get("title") or "")).strip()
        if task.get("reminder_text"):
            out["reminder_text"] = (str(task["reminder_text"]) + "\n" + str(payload.get("title") or "")).strip()
    return out

def warnings(rule: Dict[str, Any], lang: str = "ru") -> List[str]:
    out: List[str] = []
    watches = [t for t in rule.get("when") or [] if t.get("type") == "watch"]
    if not watches:
        return out
    status = service_status()
    missing = sorted({s for t in watches for s in services_of(t) if not status.get(s)})
    for service in missing:
        out.append(_t(lang, f"{SERVICE_TITLES.get(service, service)} не подключён — подключите его в Настройки → Интеграции.",
                      f"{SERVICE_TITLES.get(service, service)} is not connected — connect it in Settings → Integrations."))
    if any(t.get("source") == "webhook" for t in watches):
        out.append(_t(lang, "Вебхук принимает вызовы, пока работает MemuAI Proxy.",
                      "The webhook accepts calls while MemuAI Proxy is running."))
    return out

_HOOK_HITS: Dict[str, List[float]] = {}

def hook_call(token: str, body: Any, now: Optional[float] = None) -> Tuple[int, Dict[str, Any]]:
    """A POST to ``/hook/plan/{token}`` → a pending event of every watch with this token."""
    now = time.time() if now is None else now
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(token or "")):
        return 404, {"error": "not found"}
    hits = [t for t in _HOOK_HITS.get(token, []) if now - t < 60]
    if len(hits) >= 30:
        return 429, {"error": "too many calls"}
    hits.append(now)
    _HOOK_HITS[token] = hits
    from .pgdata import load_schedules
    tasks = load_schedules()
    targets = [(task, trigger, key) for task, _i, trigger, key in watches_of(tasks)
               if trigger.get("source") == "webhook" and secrets.compare_digest(str(trigger.get("token")), token)]
    if not targets:
        return 404, {"error": "not found"}
    data = body if isinstance(body, dict) else {"body": body}
    payload = _clip({"source": "webhook", "kind": "Webhook", "title": _text(data.get("title") or data.get("text") or "Webhook", 200),
                     "text": _text(data.get("text") or data.get("message") or "", 1500), "data": data, "count": 1}, 6000)

    def update(all_watches: Dict[str, Dict[str, Any]]) -> None:
        for task, trigger, key in targets:
            entry = all_watches.setdefault(key, {"key": key, "created": now, "cursor": {}})
            entry.update(task_id=str(task.get("id")), source="webhook", last_check=now, ok_at=now)
            entry["seq"] = int(entry.get("seq") or 0) + 1
            entry.setdefault("pending", []).append({"seq": entry["seq"], "at": now, "payload": payload})
            entry["pending"] = entry["pending"][-MAX_PENDING:]
            entry["fired"] = int(entry.get("fired") or 0) + 1
            entry["last_fire"] = now
            entry["last_title"] = payload["title"]

    mutate_state(update)
    try:
        from .schedule_runtime import wake
        wake()
    except Exception:
        pass
    return 200, {"ok": True, "tasks": len(targets)}

def register_proxy(app: Any) -> None:
    """MemuAI Proxy: ``POST /hook/plan/{token}`` (JSON ≤ 64 KB; the token is the secret)."""
    from fastapi.responses import JSONResponse

    @app.post("/hook/plan/{token}")
    async def plan_hook(token: str, request: Request):
        raw = await request.body()
        if len(raw) > 64 * 1024:
            return JSONResponse({"error": "body too large"}, 413)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {"text": raw.decode("utf-8", errors="replace")[:4000]}
        status, out = await asyncio.to_thread(hook_call, token, body)
        return JSONResponse(out, status)

def hook_url(token: str) -> str:
    try:
        from .cli_integrations import proxy_base_url
        base = proxy_base_url().rsplit("/v1", 1)[0]
    except Exception:
        base = os.environ.get("MEMUAI_PROXY_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/hook/plan/{token}"

def catalog(lang: str = "ru") -> Dict[str, Any]:
    status = service_status()
    panels_out = []
    try:
        for panel in _panels_by_id().values():
            if panel.get("dismissed_at"):
                continue
            fields = panel_fields(panel)
            if fields:
                panels_out.append({"id": panel["id"], "title": panel.get("title") or panel["id"], "kind": panel.get("kind"),
                                   "fields": fields})
    except Exception as exc:  # noqa: BLE001
        log.debug("catalog panels: %s", exc)
    default_repo = ""
    try:
        default_repo = str((_ig()._load_store().get("github") or {}).get("repository") or "")
    except Exception:
        pass
    return {"categories": [{"id": c, "title": ru if lang != "en" else en} for c, ru, en in CATEGORIES],
            "sources": [{"id": sid, "cat": m["cat"], "icon": m["icon"], "service": m["service"], "every": m["every"],
                         "min": m["min"], "title": m["en"] if lang == "en" else m["ru"],
                         "connected": (status.get(m["service"], False) if m["service"] else True)}
                        for sid, m in SOURCES.items()],
            "services": {k: {"title": SERVICE_TITLES[k], "connected": status.get(k, False)} for k in SERVICE_TITLES},
            "panels": panels_out, "quota": _quota_accounts(), "github_repo": default_repo,
            "hook_base": hook_url("TOKEN")[:-5],
            "os": "windows" if os.name == "nt" else os.name, "ops": list(OPS)}

def check(trigger_raw: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    """«Проверить сейчас»: a dry-run poll from scratch (does not touch the real cursor)."""
    try:
        trigger = normalize(trigger_raw)
    except RuleError as exc:
        return {"ok": False, "error": exc.text(lang), "kind": "config"}
    if trigger["source"] == "webhook":
        return {"ok": True, "summary": _t(lang, "Вызовите адрес POST-запросом — задача запустится", "POST to the address to start the task"),
                "url": hook_url(trigger["token"]), "token": trigger["token"]}
    started = time.time()
    try:
        result = poll_source(trigger, None, lang, started)
        if trigger["source"] in ("panel", "system", "sheets_cell", "quota_low"):
            fires = bool(result.events)
        elif trigger["source"] == "weather":
            fires = bool(result.info.get("holds"))
        else:
            fires = None
        out = {"ok": True, "summary": result.info.get("summary", ""), "preview": result.info.get("preview") or [],
               "note": result.info.get("note", ""), "value": result.info.get("value"), "would_fire": fires,
               "ms": int((time.time() - started) * 1000)}
        return out
    except SourceError as exc:
        return {"ok": False, "error": str(exc)[:400], "kind": exc.kind, "service": exc.service,
                "service_title": SERVICE_TITLES.get(exc.service, "")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400], "kind": "other"}

def health(lang: str = "ru") -> Dict[str, List[Dict[str, Any]]]:
    from .pgdata import load_schedules
    tasks = load_schedules()
    watches = load_state()["watches"]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for task, index, trigger, key in watches_of(tasks, enabled_only=False):
        entry = watches.get(key) or {}
        row = {"index": index, "key": key, "source": trigger.get("source"), "enabled": _task_enabled(task),
               "last_check": entry.get("last_check"), "next_check": entry.get("next_check"), "ok_at": entry.get("ok_at"),
               "fails": entry.get("fails", 0), "error": entry.get("error", ""), "error_kind": entry.get("error_kind", ""),
               "service": entry.get("service", ""), "fired": entry.get("fired", 0), "last_fire": entry.get("last_fire"),
               "last_title": entry.get("last_title", ""), "summary": (entry.get("info") or {}).get("summary", ""),
               "pending": len(entry.get("pending") or []), "skipped": entry.get("skipped", 0)}
        if trigger.get("source") == "webhook":
            row["url"] = hook_url(trigger["token"])
        out.setdefault(str(task.get("id")), []).append(row)
    return out

def force_check(task_id: str) -> int:
    def update(all_watches: Dict[str, Dict[str, Any]]) -> int:
        count = 0
        for entry in all_watches.values():
            if isinstance(entry, dict) and entry.get("task_id") == str(task_id):
                entry["next_check"] = 0
                count += 1
        return count
    count = mutate_state(update) or 0
    try:
        from .schedule_runtime import wake
        wake()
    except Exception:
        pass
    return count

def pick(what: str, query: str = "", parent: str = "") -> List[Dict[str, str]]:  # noqa: C901
    """Integration pickers for the config forms (raises SourceError)."""
    q = query.strip().lower()
    if what == "github_repos":
        rows = github("GET", "/user/repos", params={"per_page": 100, "sort": "pushed"})
        out = [{"id": r.get("full_name"), "title": r.get("full_name"), "sub": "private" if r.get("private") else ""}
               for r in rows or [] if isinstance(r, dict)]
    elif what == "calendars":
        data = google("google_calendar", "GET", _ig().CALENDAR_API + "/users/me/calendarList", params={"maxResults": 100})
        out = [{"id": r.get("id"), "title": r.get("summaryOverride") or r.get("summary") or r.get("id"),
                "sub": "primary" if r.get("primary") else ""} for r in data.get("items") or []]
        out.sort(key=lambda r: r["sub"] != "primary")
    elif what in ("sheets", "drive_folders"):
        mime = "application/vnd.google-apps.spreadsheet" if what == "sheets" else "application/vnd.google-apps.folder"
        clause = f"mimeType = '{mime}' and trashed = false"
        if q:
            clause += " and name contains '" + q.replace("\\", "\\\\").replace("'", "\\'") + "'"
        data = google("google_drive", "GET", _ig().DRIVE_API + "/files",
                      params={"q": clause, "orderBy": "modifiedTime desc", "pageSize": 40, "fields": "files(id,name,modifiedTime)"})
        out = [{"id": r.get("id"), "title": r.get("name"), "sub": str(r.get("modifiedTime") or "")[:10]} for r in data.get("files") or []]
        q = ""
    elif what == "sheet_tabs":
        data = google("google_sheets", "GET", f"{SHEETS_API}/spreadsheets/{_sheet_id(parent)}",
                      params={"fields": "sheets.properties(title,index)"})
        out = [{"id": (s.get("properties") or {}).get("title"), "title": (s.get("properties") or {}).get("title"), "sub": ""}
               for s in data.get("sheets") or []]
    elif what in ("notion_pages", "notion_dbs"):
        body: Dict[str, Any] = {"page_size": 30, "filter": {"property": "object", "value": "page" if what == "notion_pages" else "database"}}
        if q:
            body["query"] = query.strip()
        data = notion("POST", "/search", json_body=body)
        out = [{"id": r.get("id"), "title": _notion_title(r) or "—", "sub": str(r.get("last_edited_time") or "")[:10]}
               for r in data.get("results") or []]
        q = ""
    elif what == "gmail_labels":
        from .integrations_gmail import GMAIL_API
        data = google("gmail", "GET", GMAIL_API + "/labels")
        out = [{"id": "label:" + str(r.get("name")).replace(" ", "-"), "title": r.get("name"), "sub": r.get("type", "")}
               for r in data.get("labels") or []]
    else:
        raise SourceError("unknown picker", "config")
    if q:
        out = [r for r in out if q in str(r.get("title") or "").lower() or q in str(r.get("id") or "").lower()]
    return out[:60]

def register_routes(app: Any) -> None:
    """MemuAI window: ``/api/plan/triggers/*``."""
    from fastapi.responses import JSONResponse

    def lang_of(value: str) -> str:
        if value in ("ru", "en"):
            return value
        try:
            from .pgdata import load_preferences
            return "en" if load_preferences().get("language") == "en" else "ru"
        except Exception:
            return "ru"

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @app.get("/api/plan/triggers/catalog")
    async def triggers_catalog(lang: str = ""):
        return await asyncio.to_thread(catalog, lang_of(lang))

    @app.post("/api/plan/triggers/check")
    async def triggers_check(request: Request):
        body = await body_of(request)
        lang = lang_of(str(body.get("lang") or ""))
        trigger = body.get("trigger") if isinstance(body.get("trigger"), dict) else {}
        out = await asyncio.to_thread(check, trigger, lang)
        if body.get("task_id"):
            out["forced"] = await asyncio.to_thread(force_check, str(body["task_id"]))
        return out

    @app.get("/api/plan/triggers/health")
    async def triggers_health(lang: str = ""):
        return {"tasks": await asyncio.to_thread(health, lang_of(lang)), "now": time.time()}

    @app.get("/api/plan/triggers/pick")
    async def triggers_pick(what: str = "", q: str = "", parent: str = "", lang: str = ""):
        try:
            return {"items": await asyncio.to_thread(pick, what, q[:100], parent[:300])}
        except SourceError as exc:
            return JSONResponse({"error": str(exc)[:400], "kind": exc.kind, "service": exc.service}, 400)
        except RuleError as exc:
            return JSONResponse({"error": exc.text(lang_of(lang))}, 400)

def parse_prompt(lang: str = "ru") -> str:
    """The extension of ``schedule_rules.PARSE_PROMPT`` (integration/data triggers)."""
    return """
Data and integration triggers (put them in "when"; they start the task when something NEW happens):
 {"type":"watch","source":S, ...}  S =
   gmail (+ "query": Gmail search syntax, e.g. "from:bank.ru" or "from:(sber OR tinkoff) subject:выписка")
   calendar_before (+ "minutes": N before each meeting, optional "match": text in the title)
   calendar_created | calendar_changed (optional "match")
   github_issue (+ "repo":"owner/name", optional "labels") | github_pr (+ "repo") | github_review (optional "repo")
   github_actions (+ "repo", optional "branch", "workflow") — a workflow run FAILED | github_release (+ "repo")
   sheets_row (+ "sheet": the Google Sheets link/id, optional "tab") — a new row
   sheets_cell (+ "sheet", "cell":"Sheet1!B2", "op", "value") — a cell changed / crossed a value
   drive_file (optional "folder": Drive folder id/link, "name_has": part of the file name)
   notion_page (+ "page": link/id) | notion_db (+ "database": link/id, "mode": new|changed|any)
   rss (+ "url", optional "match": a word) | web_page (+ "url", optional "selector": CSS, "mode": changed|contains|not_contains, "text")
   webhook (a URL MemuAI Proxy listens on)
   panel (+ "panel": panel id from "panels" below, "key": its value key, "op", "value", optional "hysteresis")
   quota_low (optional "account", "level": tight|runs_out|exhausted)
   proxy_model (optional "match": text in the model id)
   folder_file (+ "path", optional "pattern":"*.pdf") — a NEW file appeared (with its name in the payload)
   system (+ "metric": disk_free_pct|disk_free_gb|ram_pct|cpu_pct|battery_pct, "op", "value", for disks "drive": "C:\\" / "D:\\")
   weather (+ "metric": rain|snow|clear (starts) or temp|feels|wind|precip_prob|cloud|humidity|precip with "op": lt|gt and "value";
     "lat","lon","place" of the city; optional "cooldown": minutes between runs, default 180) — fires when the weather CROSSES it
   all (+ "items":[2-4 watch triggers], "within": minutes) — ALL of them happened within the window
 "op" = gt|ge|lt|le|eq|ne|contains|not_contains|changed. Optional on any watch: "every": poll minutes,
 "each": true (one run per new item instead of one run per batch), "delay": minutes,
 "hours": {"from":"09:00","to":"18:00","days":[1,2,3,4,5]} (events outside these hours are ignored; add "wait": true when
 they must wait for the hours instead — e.g. «сообщай в рабочее время»).
 For "only during working hours" prefer a "time" condition in only_if plus options {"if_blocked":"wait","wait_minutes":960}
 so events wait for the working hours instead of being lost.
 The run receives the event as {{trigger.title}}, {{trigger.text}}, {{trigger.url}}, {{trigger.from}}… — you may use them in "prompt"
 (e.g. a notify text "Письмо от банка: {{trigger.title}}"); otherwise the event details are appended automatically.
 Examples: "когда придёт письмо от банка — кратко перескажи" → kind chat, when [{"type":"watch","source":"gmail","query":"from:bank"}];
 "за 10 минут до встречи напомни" → kind notify, prompt "Через 10 минут: {{trigger.title}}", when [{"type":"watch","source":"calendar_before","minutes":10}];
 "если упадёт Actions в репозитории X — разберись почему" → kind chat, when [{"type":"watch","source":"github_actions","repo":"owner/X"}].
"""

def tune_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """A parsed draft with watch triggers and a «time» condition («только в рабочее время»): events that arrive
    outside the hours wait for them (``if_blocked: wait``) instead of being dropped — models often omit it."""
    rule = draft.get("rule") if isinstance(draft.get("rule"), dict) else None
    if not rule or not any(t.get("type") == "watch" for t in rule.get("when") or []):
        return draft
    if not any(c.get("type") == "time" for c in rule.get("only_if") or []):
        return draft
    options = rule.setdefault("options", {})
    if options.get("if_blocked", "skip") == "skip":
        options["if_blocked"] = "wait"
        options["wait_minutes"] = max(int(options.get("wait_minutes") or 0), 960)
    return draft

def parse_context() -> Dict[str, Any]:
    """What the parser model needs to fill ids: connected integrations, panels with value keys, quota accounts."""
    out: Dict[str, Any] = {}
    try:
        status = service_status()
        out["connected_integrations"] = sorted(k for k, v in status.items() if v and k in SERVICE_TITLES)
    except Exception:
        pass
    try:
        out["panels"] = [{"id": p["id"], "title": p.get("title"), "keys": {f["key"]: f["value"] for f in panel_fields(p)[:12]}}
                         for p in _panels_by_id().values() if not p.get("dismissed_at") and panel_fields(p)][:20]
    except Exception:
        pass
    try:
        repo = str((_ig()._load_store().get("github") or {}).get("repository") or "")
        if repo:
            out["default_github_repo"] = repo
    except Exception:
        pass
    accounts = _quota_accounts()
    if accounts:
        out["quota_accounts"] = [{"id": a["id"], "name": a["name"]} for a in accounts][:20]
    return out
