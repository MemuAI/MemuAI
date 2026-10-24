"""Планировщик квот (Round 35, зона quota): прогноз «хватит ли лимита до сброса».

Идея — как планировщик квот в Antigravity, но для всех аккаунтов Proxy, у которых лимит известен:

* **Источники замеров.**
  - ``provider.usage(account)`` — строки ``{"display", "used", "limit", "unit", "reset"}`` (Antigravity —
    проценты окон «5 часов»/«неделя» по котлам Gemini и Claude/GPT; Kiro — кредиты месяца с
    ``next_reset``). Фоновый цикл опрашивает такие аккаунты раз в ``poll_minutes`` (по умолчанию 10 мин),
    плюс каждый ручной просмотр «Лимитов» в карточке аккаунта тоже даёт замер (:func:`observe_usage`).
  - Заголовки ответа Custom OpenAI-совместимых API: ``x-ratelimit-{limit,remaining,reset}-{requests,tokens}``
    (OpenAI/Groq/…), ``x-ratelimit-{limit,remaining,reset}`` (OpenRouter), ``anthropic-ratelimit-*`` —
    :func:`observe_headers`. Окна короче 15 минут (лимиты «в минуту») — не квота, их пропускаем.
  - Счётчик успешных запросов по аккаунтам (почасовые корзины, :func:`note_request`) — чтобы
    перевести «% в час» в понятное «не больше ~N запросов в час».
* **Ряд.** Для каждой пары аккаунт × окно хранится компактный ряд ``[ts, остаток 0..1, ts сброса]``
  (замеры ближе 60 с сливаются, старше 6 ч — не чаще раза в 10 мин, ≤ 3000 точек, ≤ 15 суток). Сбросы определяются сами: остаток резко
  вырос (≥ 25 % или любой рост после прошедшего срока сброса) либо срок сброса прыгнул на следующий цикл.
* **Прогноз.** Скорость расхода — EWMA по парам замеров (период полураспада ≈ окно/8) плюс профиль
  «по часам суток» за последние 7 дней (если покрыто ≥ 36 часов наблюдений). Ближнее будущее идёт по EWMA,
  дальнее — плавно переходит на профиль. Неопределённость — случайное блуждание почасового разброса
  плюс 15–35 % на ошибку модели → полоса 10–90 %.
* **Вердикт.** ``ok`` «Хватит с запасом» · ``tight`` «Впритык» (пессимистичный край кончается до сброса
  или в запасе < 10 %) · ``runs_out`` «Закончится примерно в HH:MM (за X до сброса)» · ``exhausted``
  «Лимит исчерпан» · ``insufficient`` «Мало данных» (меньше 3 замеров или < 20 мин наблюдений) ·
  ``no_reset`` — срок сброса неизвестен (показываем только скорость и время до нуля).
* **Темп.** «Не больше ~N запросов/час» (если известна цена запроса в процентах), иначе единицы лимита
  в час (токены/запросы/кредиты) или «% в час».

Контракт для других зон (composite и пр.):

    from app import quota_planner
    quota_planner.score(account_id) -> float | None   # 0..1: 1 — квоты с большим запасом, 0 — исчерпана
                                                       # или кончится задолго до сброса; None — нет данных
    quota_planner.status(account_id) -> str | None     # худший вердикт аккаунта (см. выше)
    quota_planner.rank(account_ids) -> list[str]       # те же id, от «лучше брать» к «лучше беречь»
    quota_planner.reorder(accounts) -> list[Account]   # пул: при «Экономить квоту» отодвигает аккаунты,
                                                       # которым по прогнозу не хватит (основные/резерв не смешиваются)

Всё дешёвое: прогноз пересчитывается при замере, ``score``/``reorder`` берут кэш и не ходят в сеть.
Маршруты: ``GET /api/admin/quota/planner``, ``POST /api/admin/quota/planner/settings``,
``POST /api/admin/quota/planner/refresh``, ``GET /api/admin/quota/planner/badges``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("memuai.quota_planner")

HOUR = 3600.0
DAY = 86400.0
WEEK = 7 * DAY
MAX_POINTS = 3000
DENSE_SECONDS = 6 * HOUR             # последние 6 часов — каждый замер (≥ 60 с), старше — не чаще раза в 10 мин
SPARSE_STEP = 600.0
KEEP_SECONDS = 15 * DAY
MERGE_SECONDS = 60.0
MIN_HEADER_WINDOW = 15 * 60          # окна «в минуту» — не квота
REQ_KEEP_HOURS = 15 * 24
MARGIN = 0.10                        # «впритык», если к сбросу остаётся меньше 10 %
KEEP_RESERVE = 0.03                  # темп оставляет 3 % на непредвиденное
STATUS_RANK = {"ok": 0, "idle": 0, "insufficient": 0, "no_reset": 0, "tight": 1, "runs_out": 2, "exhausted": 3}
STATUSES = ("ok", "tight", "runs_out", "exhausted", "insufficient", "no_reset")
DEFAULT_SETTINGS: Dict[str, Any] = {"enabled": True, "poll_minutes": 10, "save_quota": False, "notify": True}
POLL_PROVIDERS = ("antigravity", "kiro")

_clock: Callable[[], float] = time.time


def set_clock(fn: Optional[Callable[[], float]]) -> None:
    """Тесты и демо-данные: детерминированные часы (None — вернуть настоящие)."""
    global _clock
    _clock = fn or time.time


def _now() -> float:
    return float(_clock())


# ====================================================================== разбор источников

def _num(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def parse_ts(value: Any) -> float:
    """epoch (с, мс) или ISO-8601 → epoch-секунды; 0 — нет."""
    number = _num(value)
    if number is not None:
        if number > 1e12:
            number /= 1000.0
        return number if number > 1e8 else 0.0
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse_duration(value: Any) -> Optional[float]:
    """«6m0s», «1h2m3.5s», «20ms», «59» (секунды) → секунды."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    number = _num(text)
    if number is not None:
        return number
    total, found = 0.0, False
    for amount, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s|d)", text):
        amt = _num(amount)
        if amt is None:
            continue
        found = True
        total += amt * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return total if found else None


def period_from_title(text: str) -> Optional[float]:
    low = (text or "").lower()
    if re.search(r"\b5\s*(ч|h|час|hour)|5-hour|5-час", low):
        return 5 * HOUR
    if re.search(r"недел|week|7\s*(дн|day)|weekly", low) and "оценк" not in low and "estimate" not in low:
        return WEEK
    if re.search(r"\bсут|\bдень|\bday\b|daily|24\s*(ч|h)", low):
        return DAY
    if re.search(r"месяц|month", low):
        return 30 * DAY
    return None


def guess_period(seconds_to_reset: float) -> Optional[float]:
    """Срок до сброса при первом взгляде → вероятная длина окна (грубо, помечается как оценка)."""
    if seconds_to_reset <= 0:
        return None
    for limit, period in ((1.2 * HOUR, HOUR), (6 * HOUR, 5 * HOUR), (26 * HOUR, DAY), (8 * DAY, WEEK)):
        if seconds_to_reset <= limit:
            return period
    return 31 * DAY


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9а-яё]+", "-", str(text or "").lower()).strip("-")
    return value[:60] or "quota"


def rows_from_usage(usage: Any, now: float) -> List[Dict[str, Any]]:
    """Ответ ``provider.usage`` → окна ``{key,title,title_en,remaining,reset,limit,unit,period}``.

    Строки без числового лимита (оценки «за 7 дней», «окон по 5 ч») и без ``used`` пропускаются —
    прогнозировать их честно нельзя.
    """
    if not isinstance(usage, dict):
        return []
    default_reset = parse_ts(usage.get("next_reset"))
    out: List[Dict[str, Any]] = []
    for row in usage.get("rows") or []:
        if not isinstance(row, dict):
            continue
        used, limit = _num(row.get("used")), _num(row.get("limit"))
        if used is None or not limit or limit <= 0:
            continue
        title = str(row.get("display") or row.get("title") or "").strip() or "quota"
        title_en = str(row.get("display_en") or title)
        unit = str(row.get("unit") or "")
        reset = parse_ts(row.get("reset")) or default_reset
        remaining = max(0.0, min(1.0, 1.0 - used / limit))
        period = period_from_title(title) or period_from_title(title_en)
        out.append({"key": slug(title_en), "title": title, "title_en": title_en, "remaining": remaining,
                    "reset": reset if reset > now - 60 else 0.0, "limit": limit, "unit": unit,
                    "unit_en": str(row.get("unit_en") or unit), "period": period})
    return out


def rows_from_headers(headers: Any, now: float, tag: str = "") -> List[Dict[str, Any]]:
    """Заголовки ответа API → окна квоты (только окна ≥ 15 минут)."""
    try:
        items = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    except Exception:  # noqa: BLE001
        return []
    found: Dict[str, Dict[str, Any]] = {}

    def put(kind: str, field: str, value: str) -> None:
        found.setdefault(kind, {})[field] = value

    for name, value in items.items():
        m = re.fullmatch(r"x-ratelimit-(limit|remaining|reset)(?:-(requests|tokens)(?:-(day|minute|hour))?)?", name)
        if m:
            kind = (m.group(2) or "requests") + (("-" + m.group(3)) if m.group(3) else "")
            put("x:" + kind, m.group(1), value)
            continue
        m = re.fullmatch(r"anthropic-ratelimit-(requests|tokens|input-tokens|output-tokens)-(limit|remaining|reset)", name)
        if m:
            put("a:" + m.group(1), m.group(2), value)
    out: List[Dict[str, Any]] = []
    for kind, data in found.items():
        limit, remaining = _num(data.get("limit")), _num(data.get("remaining"))
        if not limit or limit <= 0 or remaining is None:
            continue
        raw_reset = data.get("reset", "")
        reset = 0.0
        if kind.startswith("a:"):
            reset = parse_ts(raw_reset)
        else:
            number = _num(raw_reset)
            if number is not None and number > 1e8:
                reset = parse_ts(number)          # OpenRouter: epoch в мс
            else:
                span = parse_duration(raw_reset)
                reset = now + span if span is not None else 0.0
        if not reset or reset - now < MIN_HEADER_WINDOW:
            continue
        what = kind.split(":", 1)[1]
        unit_ru = "токенов" if "token" in what else "запросов"
        unit_en = "tokens" if "token" in what else "requests"
        base_kind = what.split("-day")[0].split("-hour")[0]
        title_ru = {"requests": "Запросы", "tokens": "Токены", "input-tokens": "Входные токены",
                    "output-tokens": "Выходные токены"}.get(base_kind, what)
        title_en = {"requests": "Requests", "tokens": "Tokens", "input-tokens": "Input tokens",
                    "output-tokens": "Output tokens"}.get(base_kind, what)
        if what.endswith("-hour"):
            title_ru, title_en = title_ru + " в час", title_en + " per hour"
        elif what.endswith("-day"):
            title_ru, title_en = title_ru + " в сутки", title_en + " per day"
        suffix = f" · ключ #{tag}" if tag else ""
        suffix_en = f" · key #{tag}" if tag else ""
        out.append({"key": slug(f"hdr-{what}-{tag}"), "title": title_ru + suffix, "title_en": title_en + suffix_en,
                    "remaining": max(0.0, min(1.0, remaining / limit)), "reset": reset, "limit": limit,
                    "unit": unit_ru, "unit_en": unit_en,
                    "period": DAY if what.endswith("-day") else (HOUR if what.endswith("-hour") else None)})
    return out


# ====================================================================== ряд и сбросы

def new_series(acc: str, row: Dict[str, Any]) -> Dict[str, Any]:
    return {"acc": acc, "key": row["key"], "title": row.get("title", ""), "title_en": row.get("title_en", ""),
            "unit": row.get("unit", ""), "unit_en": row.get("unit_en", ""), "limit": row.get("limit"),
            "period": row.get("period"), "period_guess": False, "points": [], "resets": [], "cycle_start": 0.0}


def add_point(series: Dict[str, Any], ts: float, remaining: float, reset: float = 0.0) -> bool:
    """Добавить замер; True — обнаружен сброс. Ряд остаётся отсортированным и компактным."""
    remaining = max(0.0, min(1.0, float(remaining)))
    reset = float(reset or 0.0)
    pts: List[List[float]] = series.setdefault("points", [])
    if pts and ts < pts[-1][0]:
        return False                                  # замер из прошлого (гонка) — пропускаем
    if not series.get("period") and reset:
        series["period"] = guess_period(reset - ts)
        series["period_guess"] = True
    is_reset = False
    if pts:
        p_ts, p_rem, p_reset = pts[-1]
        rise = remaining - p_rem
        period = float(series.get("period") or 0)
        reset_passed = bool(p_reset) and ts >= p_reset - 30
        if p_reset and reset:
            jumped = reset - p_reset > (max(0.3 * period, 20 * 60) if period else 3 * HOUR)
        else:
            jumped = False
        is_reset = rise >= 0.25 or (rise > 0.005 and (reset_passed or jumped))
        if is_reset:
            series["cycle_start"] = ts
            resets = series.setdefault("resets", [])
            resets.append(round(p_reset if (reset_passed and p_reset) else ts, 1))
            del resets[:-24]
        if not is_reset and ts - p_ts < MERGE_SECONDS and len(pts) > 1:
            pts[-1] = [round(ts, 1), round(remaining, 5), round(reset, 1)]
            return False
    else:
        series["cycle_start"] = ts
    pts.append([round(ts, 1), round(remaining, 5), round(reset, 1)])
    cutoff = ts - KEEP_SECONDS
    while pts and pts[0][0] < cutoff:
        pts.pop(0)
    if len(pts) % 50 == 0:
        _thin(pts, ts - DENSE_SECONDS)
    if len(pts) > MAX_POINTS:
        del pts[: len(pts) - MAX_POINTS]
    # длина окна по фактическим сбросам точнее любой догадки
    resets = series.get("resets") or []
    if len(resets) >= 3 and (series.get("period_guess") or not series.get("period")):
        gaps = sorted(b - a for a, b in zip(resets, resets[1:]) if b - a > 10 * 60)
        if gaps:
            series["period"] = gaps[len(gaps) // 2]
    return is_reset


def _thin(pts: List[List[float]], dense_from: float) -> None:
    """Старые замеры прореживаются до одного в 10 минут; сбросы (рост остатка) и последний старый — остаются."""
    out: List[List[float]] = []
    for i, p in enumerate(pts):
        if p[0] >= dense_from or not out:
            out.append(p)
            continue
        last = out[-1]
        nxt = pts[i + 1] if i + 1 < len(pts) else None
        boundary = nxt is not None and nxt[0] >= dense_from
        if p[0] - last[0] >= SPARSE_STEP or p[1] > last[1] + 0.05 or boundary or \
                (nxt is not None and nxt[1] > p[1] + 0.05):
            out.append(p)
    pts[:] = out


# ====================================================================== прогноз

def _local_hour(ts: float) -> int:
    return time.localtime(ts).tm_hour


def _pairs(points: List[List[float]], since: float) -> List[Tuple[float, float, float]]:
    """(начало, конец, расход) по соседним замерам; рост остатка — не расход; разрывы > 12 ч — неизвестно."""
    out = []
    for a, b in zip(points, points[1:]):
        if b[0] <= since or b[0] - a[0] <= 0 or b[0] - a[0] > 12 * HOUR:
            continue
        out.append((a[0], b[0], max(0.0, a[1] - b[1])))
    return out


def _hourly(pairs: List[Tuple[float, float, float]]) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Расход и покрытие (секунды) по часовым корзинам — расход пары делится по часам пропорционально."""
    spent: Dict[int, float] = {}
    cover: Dict[int, float] = {}
    for start, end, used in pairs:
        span = end - start
        t = start
        while t < end - 1e-6:
            hour = int(t // HOUR)
            edge = min(end, (hour + 1) * HOUR)
            share = (edge - t) / span
            spent[hour] = spent.get(hour, 0.0) + used * share
            cover[hour] = cover.get(hour, 0.0) + (edge - t)
            t = edge
    return spent, cover


def _request_rate(requests: Dict[str, int], pairs: List[Tuple[float, float, float]]) -> Optional[float]:
    """Доля квоты на один запрос по интервалам, где были и замеры, и запросы."""
    if not requests:
        return None
    counts = {int(k): int(v) for k, v in requests.items()}
    total_req, total_used = 0.0, 0.0
    for start, end, used in pairs:
        hours = range(int(start // HOUR), int(end // HOUR) + 1)
        n = 0.0
        for hour in hours:
            c = counts.get(hour)
            if not c:
                continue
            overlap = min(end, (hour + 1) * HOUR) - max(start, hour * HOUR)
            if overlap > 0:
                n += c * overlap / HOUR
        total_req += n
        total_used += used
    if total_req >= 5 and total_used >= 0.02:
        return total_used / total_req
    return None


def forecast(series: Dict[str, Any], now: Optional[float] = None, requests: Optional[Dict[str, int]] = None,
             with_curves: bool = True) -> Dict[str, Any]:
    """Прогноз одного окна. Возвращает словарь для UI и для :func:`score` (см. описание модуля)."""
    now = _now() if now is None else now
    pts: List[List[float]] = [p for p in series.get("points") or [] if p[0] <= now + 5]
    period = float(series.get("period") or 0) or None
    out: Dict[str, Any] = {"key": series.get("key"), "title": series.get("title"), "title_en": series.get("title_en"),
                           "unit": series.get("unit"), "unit_en": series.get("unit_en"), "limit": series.get("limit"),
                           "period": period, "period_guess": bool(series.get("period_guess")),
                           "points": len(pts), "status": "insufficient", "remaining": None, "reset": None,
                           "reset_estimated": False, "eta": None, "eta_early": None, "eta_late": None,
                           "rem_at_reset": None, "rem_at_reset_lo": None, "rem_at_reset_hi": None,
                           "rate": None, "rate_now": None, "pace_pct": None, "pace_units": None, "pace_req": None,
                           "req_now": None, "stale": False, "last": None, "history": [], "projection": [],
                           "resets": [r for r in series.get("resets") or [] if r > now - 8 * DAY]}
    if not pts:
        return out
    last_ts, rem, last_reset = pts[-1]
    out.update(remaining=rem, last=last_ts)
    stale_after = max(2 * HOUR, (period or 0) * 0.25)
    out["stale"] = now - last_ts > stale_after

    # --- срок сброса: заявленный, иначе предсказанный по длине окна
    reset = last_reset if last_reset and last_reset > now else 0.0
    if not reset and period:
        anchor = (series.get("resets") or [0])[-1] or 0.0
        if last_reset and last_reset <= now:
            anchor = max(anchor, last_reset)
        if anchor:
            k = max(1, math.floor((now - anchor) / period) + 1)
            reset = anchor + k * period
            out["reset_estimated"] = True
    out["reset"] = reset or None

    # --- история для графика: текущий цикл, но не меньше окна
    cycle = float(series.get("cycle_start") or pts[0][0])
    view_from = max(now - 7 * DAY, min(cycle, now - min(period or 5 * HOUR, DAY)))
    out["view_from"] = view_from
    if with_curves:
        hist = [[p[0], p[1]] for p in pts if p[0] >= view_from]
        if len(hist) > 240:
            step = len(hist) / 240.0
            hist = [hist[int(i * step)] for i in range(240)] + [hist[-1]]
        out["history"] = hist

    # --- скорость: EWMA + профиль по часам суток
    pairs_all = _pairs(pts, now - 7 * DAY)
    half_life = min(12 * HOUR, max(20 * 60, (period or 5 * HOUR) / 8))
    recent = [p for p in pairs_all if p[1] > now - max(3 * half_life, 6 * HOUR)]
    ewma = None
    for start, end, used in recent:
        inst = used / ((end - start) / HOUR)
        alpha = 1 - math.exp(-(end - start) / half_life)
        ewma = inst if ewma is None else ewma + alpha * (inst - ewma)
    if ewma is not None and now - last_ts > half_life:
        # давно без замеров: EWMA не знает, что было потом; гасим её к среднему
        ewma *= math.exp(-(now - last_ts - half_life) / (4 * half_life))
    spent, cover = _hourly(pairs_all)
    covered_hours = sum(cover.values()) / HOUR
    long_mean = (sum(spent.values()) / covered_hours) if covered_hours >= 1 else None
    profile: Optional[List[float]] = None
    if covered_hours >= 36 and long_mean is not None:
        by_h = [0.0] * 24
        cov_h = [0.0] * 24
        for hour, used in spent.items():
            h = _local_hour(hour * HOUR + 1)
            by_h[h] += used
            cov_h[h] += cover.get(hour, 0.0) / HOUR
        profile = [(by_h[h] / cov_h[h]) if cov_h[h] >= 0.5 else long_mean for h in range(24)]
    rates = [spent[h] / (cover[h] / HOUR) for h in spent if cover.get(h, 0) >= 0.5 * HOUR and h * HOUR > now - 3 * DAY]
    if len(rates) >= 3:
        mean_r = sum(rates) / len(rates)
        sigma = math.sqrt(sum((r - mean_r) ** 2 for r in rates) / (len(rates) - 1))
    else:
        sigma = (ewma or 0) * 0.6
    span_obs = (pts[-1][0] - max(pts[0][0], now - 7 * DAY)) if len(pts) > 1 else 0
    enough = len(pairs_all) >= 2 and span_obs >= 20 * 60 and ewma is not None
    out["rate"] = round(ewma, 6) if ewma is not None else None
    per_req = _request_rate(requests or {}, pairs_all)
    if requests:
        hour_now = int(now // HOUR)
        last_hour = sum(int(v) for k, v in requests.items() if int(k) in (hour_now, hour_now - 1))
        out["req_now"] = last_hour
    if per_req and ewma is not None:
        out["rate_now"] = round(ewma / per_req, 1)

    if rem <= 0.005:
        out["status"] = "exhausted"
        if reset:
            out["eta"] = last_ts
        return _finish(out, reset, now)
    if not enough:
        return _finish(out, reset, now)

    # --- проекция до сброса (или на сутки, если сброс неизвестен)
    start = max(last_ts, now - 1)
    horizon_end = reset if reset else start + (period or DAY)
    if horizon_end <= start:
        return _finish(out, reset, now)
    total = horizon_end - start
    step = min(1800.0, max(60.0, total / 120.0))
    tau = min(12 * HOUR, max(30 * 60, (period or 8 * HOUR) * 0.25))
    rel = 0.15 if profile is not None or covered_hours >= 6 else 0.35
    base_rate = long_mean if long_mean is not None else ewma
    # С профилем EWMA — не уровень, а «насколько сегодня активнее обычного для этих часов»: множитель
    # к профилю, который затухает к 1. Так тихая ночь остаётся тихой, даже если вечер был бурным.
    ratio = None
    if profile is not None and long_mean:
        look = max(3 * half_life, HOUR)
        samples = [profile[_local_hour(start - look + (i + 0.5) * look / 6)] for i in range(6)]
        usual = sum(samples) / len(samples)
        if usual > 0.1 * long_mean:
            ratio = max(0.25, min(4.0, ewma / usual))
    t, used, var = start, 0.0, 0.0
    curve = [[round(start, 1), rem, rem, rem]]
    eta = eta_early = eta_late = None
    while t < horizon_end - 1e-6:
        dt = min(step, horizon_end - t)
        mid = t + dt / 2
        w = math.exp(-(mid - start) / tau)
        if profile is not None and ratio is not None:
            rate = profile[_local_hour(mid)] * (1 + (ratio - 1) * w)
        else:
            base = profile[_local_hour(mid)] if profile is not None else base_rate
            rate = w * ewma + (1 - w) * base
        used += rate * dt / HOUR
        shape = 1.0
        if profile is not None and long_mean:
            shape = max(0.1, min(3.0, profile[_local_hour(mid)] / long_mean))   # тихие часы — меньше разброс
        var += ((sigma * shape) ** 2) * dt / HOUR
        t += dt
        band = 1.2816 * math.sqrt(var) + rel * used
        hi_used, lo_used = used + band, max(0.0, used - band)
        if eta is None and used >= rem:
            eta = t
        if eta_early is None and hi_used >= rem:
            eta_early = t
        if eta_late is None and lo_used >= rem:
            eta_late = t
        curve.append([round(t, 1), round(max(0.0, rem - used), 5), round(max(0.0, rem - hi_used), 5),
                      round(max(0.0, rem - lo_used), 5)])
    if with_curves:
        if len(curve) > 121:
            stride = len(curve) / 120.0
            curve = [curve[int(i * stride)] for i in range(120)] + [curve[-1]]
        out["projection"] = curve
    final = curve[-1]
    out.update(eta=eta, eta_early=eta_early, eta_late=eta_late)
    if reset:
        out.update(rem_at_reset=final[1], rem_at_reset_lo=final[2], rem_at_reset_hi=final[3])
        if eta is not None:
            out["status"] = "runs_out"
        elif eta_early is not None or final[1] < MARGIN or final[2] < MARGIN / 2:
            out["status"] = "tight"
        else:
            out["status"] = "ok"
    else:
        out["status"] = "no_reset"
    return _finish(out, reset, now)


def _finish(out: Dict[str, Any], reset: float, now: float) -> Dict[str, Any]:
    """Рекомендуемый темп: остаток (минус 3 % запаса) поровну на время до сброса."""
    rem = out.get("remaining")
    if rem is None or not reset or reset <= now:
        return out
    # Round 36: less than an hour before the reset the whole remainder is the hour's budget — dividing by minutes
    # recommended «up to 11 160 requests/hour» with 9 532 left of a 10 000/hour limit.
    hours = max((reset - now) / HOUR, 1.0)
    budget = max(0.0, rem - KEEP_RESERVE)
    pace = budget / hours
    out["pace_pct"] = round(pace * 100, 3)
    limit = _num(out.get("limit"))
    unit = str(out.get("unit") or "")
    if limit and unit and unit != "%":
        out["pace_units"] = round(pace * limit, 1)
    rate, rate_now = out.get("rate"), out.get("rate_now")
    if rate and rate_now:
        per_req = rate / rate_now
        if per_req > 0:
            out["pace_req"] = max(0, int(pace / per_req))
    return out


def window_score(fc: Dict[str, Any], now: float) -> Optional[float]:
    """0..1 для одного окна (1 — большой запас); None — судить не по чему."""
    status = fc.get("status")
    rem = fc.get("remaining")
    if rem is None:
        return None
    if status == "exhausted":
        return 0.0
    reset = fc.get("reset")
    if status == "runs_out" and fc.get("eta") and reset:
        share = max(0.0, min(1.0, (fc["eta"] - now) / max(1.0, reset - now)))
        return round(0.05 + 0.2 * share, 4)
    if status == "tight":
        return round(0.3 + 0.2 * max(0.0, min(1.0, (fc.get("rem_at_reset") or 0) / MARGIN)), 4)
    if status == "ok":
        return round(0.5 + 0.5 * max(0.0, min(1.0, fc.get("rem_at_reset") or 0)), 4)
    return round(0.25 + 0.5 * float(rem), 4)          # мало данных / сброс неизвестен: по остатку


# ====================================================================== хранилище и сервис

def _path() -> str:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return os.path.join(env, "quota_planner.json")
    from .config import DATA_DIR
    return os.path.join(DATA_DIR, "quota_planner.json")


class Planner:
    def __init__(self, path: Optional[str] = None) -> None:
        self._path_override = path
        self._lock = threading.RLock()
        self._loaded = False
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.series: Dict[str, Dict[str, Any]] = {}
        self.requests: Dict[str, Dict[str, int]] = {}
        self.notified: Dict[str, str] = {}
        self.polled: Dict[str, float] = {}
        self.errors: Dict[str, str] = {}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._last_save = 0.0

    # ---------------------------------------------------------------- диск
    @property
    def path(self) -> str:
        return self._path_override or _path()

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return
            if not isinstance(data, dict):
                return
            settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
            self.settings.update({k: settings[k] for k in DEFAULT_SETTINGS if k in settings})
            self.series = {k: v for k, v in (data.get("series") or {}).items()
                           if isinstance(v, dict) and isinstance(v.get("points"), list)}
            self.requests = {k: {str(h): int(c) for h, c in v.items()}
                             for k, v in (data.get("requests") or {}).items() if isinstance(v, dict)}
            self.notified = {k: str(v) for k, v in (data.get("notified") or {}).items()}

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self._loaded:
                return                        # never overwrite the file with an empty, not-yet-loaded state
            if not force and (not self._dirty or _now() - self._last_save < 60):
                return
            data = {"version": 1, "settings": self.settings, "series": self.series,
                    "requests": self.requests, "notified": self.notified}
            path = self.path
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, path)
                self._dirty = False
                self._last_save = _now()
            except OSError as exc:
                log.warning("quota planner: save failed: %s", exc)

    # ---------------------------------------------------------------- замеры
    def observe_rows(self, account_id: str, rows: Iterable[Dict[str, Any]], ts: Optional[float] = None) -> int:
        self.load()
        ts = _now() if ts is None else ts
        n = 0
        with self._lock:
            for row in rows:
                sid = f"{account_id}|{row['key']}"
                series = self.series.get(sid)
                if series is None:
                    series = self.series[sid] = new_series(account_id, row)
                for key in ("title", "title_en", "unit", "unit_en", "limit"):
                    if row.get(key) not in (None, ""):
                        series[key] = row[key]
                if row.get("period") and (series.get("period_guess") or not series.get("period")):
                    series["period"], series["period_guess"] = row["period"], False
                if add_point(series, ts, row["remaining"], row.get("reset") or 0.0):
                    self._on_reset(account_id, series)
                n += 1
            if n:
                self._dirty = True
                self._recompute(account_id, ts)
        if n:
            self.save()
        return n

    def note_request(self, account_id: str, ts: Optional[float] = None, count: int = 1) -> None:
        self.load()
        ts = _now() if ts is None else ts
        hour = str(int(ts // HOUR))
        with self._lock:
            bucket = self.requests.setdefault(account_id, {})
            bucket[hour] = int(bucket.get(hour, 0)) + count
            if len(bucket) > REQ_KEEP_HOURS:
                for key in sorted(bucket, key=int)[: len(bucket) - REQ_KEEP_HOURS]:
                    bucket.pop(key, None)
            self._dirty = True

    def _requests_for(self, account_id: str) -> Optional[Dict[str, int]]:
        """Счётчик запросов аккаунта — только если его можно отнести к окнам однозначно.

        Запросы считаются на аккаунт, а не на модель: у Antigravity два котла (Gemini и Claude/GPT), и
        запросы к одному не тратят другой — «N запросов/час» для такого аккаунта было бы выдумкой.
        """
        pools = {"api-headers" if str(s.get("key", "")).startswith("hdr-")
                 else str(s.get("title_en") or s.get("title") or "").split(" — ")[0].strip().lower()
                 for s in self.series.values() if s.get("acc") == account_id}
        if len(pools) > 1:
            return None
        return self.requests.get(account_id)

    def forget_account(self, account_id: str) -> None:
        with self._lock:
            for sid in [s for s in self.series if s.startswith(account_id + "|")]:
                self.series.pop(sid, None)
            self.requests.pop(account_id, None)
            self._cache.pop(account_id, None)
            self._dirty = True

    def _on_reset(self, account_id: str, series: Dict[str, Any]) -> None:
        key = f"quota:{account_id}:{series.get('key')}"
        if key in self.notified:
            self.notified.pop(key, None)
            _inbox_resolve(key)

    # ---------------------------------------------------------------- прогноз
    def _recompute(self, account_id: str, now: float) -> Dict[str, Any]:
        windows = []
        for sid, series in self.series.items():
            if series.get("acc") != account_id:
                continue
            fc = forecast(series, now, self._requests_for(account_id), with_curves=False)
            fc["score"] = window_score(fc, now)
            windows.append(fc)
        scores = [w["score"] for w in windows if w.get("score") is not None]
        present = [w.get("status") for w in windows]
        worst = max(present, key=lambda st: STATUS_RANK.get(st or "", 0), default=None)
        if worst is not None and STATUS_RANK.get(worst, 0) == 0:
            worst = next((st for st in ("ok", "no_reset", "insufficient") if st in present), worst)
        summary = {"status": worst, "score": min(scores) if scores else None, "computed": now,
                   "windows": [{k: w.get(k) for k in ("key", "status", "eta", "reset", "remaining", "score")} for w in windows]}
        self._cache[account_id] = summary
        if self.settings.get("notify", True):
            for w in windows:
                self._maybe_notify(account_id, w, now)
        return summary

    def summary(self, account_id: str) -> Optional[Dict[str, Any]]:
        self.load()
        with self._lock:
            cached = self._cache.get(account_id)
            if cached is None or _now() - cached.get("computed", 0) > 15 * 60:
                if not any(s.get("acc") == account_id for s in self.series.values()):
                    return None
                cached = self._recompute(account_id, _now())
            return cached

    def _maybe_notify(self, account_id: str, w: Dict[str, Any], now: float) -> None:
        key = f"quota:{account_id}:{w.get('key')}"
        status = w.get("status")
        if status == "runs_out" and w.get("eta") and w.get("reset") and w["eta"] > now:
            stamp = f"{int(w['reset'])}"
            if self.notified.get(key) == stamp:
                return
            label, provider = _account_label(account_id)
            when = time.strftime("%H:%M", time.localtime(w["eta"]))
            before = human_span(w["reset"] - w["eta"], "ru")
            before_en = human_span(w["reset"] - w["eta"], "en")
            title = f"Квота «{w.get('title')}» ({label}) закончится примерно в {when}"
            body = (f"По текущему темпу лимит кончится за {before} до сброса "
                    f"({time.strftime('%d.%m %H:%M', time.localtime(w['reset']))}). "
                    "Сбавьте темп или переключитесь на другой аккаунт — подробности в «Планировщике квот» Proxy.")
            meta = {"title_en": f"Quota “{w.get('title_en')}” ({label}) will run out at about {when}",
                    "body_en": f"At the current pace the limit runs out {before_en} before the reset. "
                               "Slow down or switch accounts — see the Quota planner in the Proxy.",
                    "account_id": account_id, "provider": provider, "window": w.get("key")}
            if _ui_lang() == "en":           # the Inbox shows one language: the user's MemuAI language
                title, body = meta["title_en"], meta["body_en"]
            if _inbox_post(key, title, body, meta):
                self.notified[key] = stamp
                self._dirty = True
        elif status in ("ok",) and key in self.notified:
            self.notified.pop(key, None)
            self._dirty = True
            _inbox_resolve(key)

    def view(self, now: Optional[float] = None, accounts: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Полный вид для вкладки «Планировщик квот»."""
        self.load()
        now = _now() if now is None else now
        known = {a.id: a for a in (accounts if accounts is not None else _all_accounts())}
        rows = []
        with self._lock:
            by_acc: Dict[str, List[Dict[str, Any]]] = {}
            for series in self.series.values():
                acc = series.get("acc")
                if acc not in known:
                    continue
                fc = forecast(series, now, self._requests_for(acc))
                fc["score"] = window_score(fc, now)
                by_acc.setdefault(acc, []).append(fc)
            for acc_id, windows in by_acc.items():
                account = known[acc_id]
                windows.sort(key=lambda w: (-STATUS_RANK.get(w.get("status") or "", 0), w.get("title") or ""))
                summary = self._recompute(acc_id, now)
                req = self.requests.get(acc_id, {})
                hour_now = int(now // HOUR)
                rows.append({"id": acc_id, "label": getattr(account, "label", "") or "",
                             "provider": getattr(account, "provider", ""), "enabled": bool(getattr(account, "enabled", True)),
                             "priority": getattr(account, "priority", "normal"),
                             "status": summary.get("status"), "score": summary.get("score"),
                             "requests_24h": sum(int(v) for k, v in req.items() if int(k) > hour_now - 24),
                             "polled": self.polled.get(acc_id), "error": self.errors.get(acc_id, ""),
                             "windows": windows})
        rows.sort(key=lambda r: (-STATUS_RANK.get(r.get("status") or "", 0),
                                 r["score"] if r.get("score") is not None else 2, r["label"]))
        pollable = [a for a in known.values() if getattr(a, "provider", "") in POLL_PROVIDERS]
        tracked = {r["id"] for r in rows}
        counts = {s: 0 for s in STATUSES}
        for r in rows:
            for w in r["windows"]:
                counts[w["status"]] = counts.get(w["status"], 0) + 1
        prefer = [r["id"] for r in sorted(rows, key=lambda r: -(r.get("score") or 0))
                  if r.get("status") in ("ok",) and r.get("enabled")][:3]
        return {"now": now, "settings": dict(self.settings), "accounts": rows, "counts": counts,
                "prefer": prefer,
                "untracked": [{"id": a.id, "label": getattr(a, "label", ""), "provider": a.provider}
                              for a in pollable if a.id not in tracked][:20],
                "sources": {"providers": list(POLL_PROVIDERS), "headers": True}}

    def badges(self) -> Dict[str, Any]:
        self.load()
        out = {}
        for acc_id in {s.get("acc") for s in self.series.values()}:
            summary = self.summary(acc_id) if acc_id else None
            if summary and summary.get("status"):
                first = next((w for w in summary["windows"] if w.get("status") == summary["status"]), {})
                out[acc_id] = {"status": summary["status"], "score": summary.get("score"),
                               "eta": first.get("eta"), "reset": first.get("reset"),
                               "remaining": first.get("remaining")}
        return out


planner = Planner()


def human_span(seconds: float, lang: str = "ru") -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if lang == "en":
        if days:
            return f"{days} d {hours} h" if hours else f"{days} d"
        if hours:
            return f"{hours} h {minutes} min" if minutes else f"{hours} h"
        return f"{max(1, minutes)} min"
    if days:
        return f"{days} д {hours} ч" if hours else f"{days} д"
    if hours:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{max(1, minutes)} мин"


# ====================================================================== связи с Proxy

def _all_accounts() -> List[Any]:
    try:
        from .state import state
        return list(state.accounts.values())
    except Exception:  # noqa: BLE001
        return []


def _account_label(account_id: str) -> Tuple[str, str]:
    for acc in _all_accounts():
        if acc.id == account_id:
            return (acc.label or acc.provider), acc.provider
    return account_id, ""


def _ui_lang() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:  # noqa: BLE001
        return "ru"


def _inbox_post(key: str, title: str, body: str, meta: Dict[str, Any]) -> bool:
    try:
        from . import inbox
    except ImportError:
        return False
    try:
        # source="proxy": the Inbox shows it as «Proxy» and its «kind: ui» button opens the Proxy window.
        inbox.post("quota", title, body=body, source="proxy", ref={"tab": "quota", "account_id": meta.get("account_id")},
                   actions=[{"id": "open", "label": "Open Proxy" if _ui_lang() == "en" else "Открыть Proxy", "kind": "ui"}],
                   dedupe_key=key, severity="warn", group="notify", meta=meta)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("quota planner: inbox post failed: %s", exc)
        return False


def _inbox_resolve(key: str) -> None:
    try:
        from . import inbox
        inbox.resolve(key, result="recovered")
    except Exception:  # noqa: BLE001
        pass


def observe_usage(account: Any, usage: Any, ts: Optional[float] = None) -> int:
    """Ответ ``provider.usage(account)`` (карточка аккаунта или фоновый опрос) → замеры. Не бросает."""
    try:
        planner.load()
        if not planner.settings.get("enabled", True):
            return 0
        ts = _now() if ts is None else ts
        rows = rows_from_usage(usage, ts)
        planner.polled[account.id] = ts
        planner.errors.pop(account.id, None)
        return planner.observe_rows(account.id, rows, ts) if rows else 0
    except Exception as exc:  # noqa: BLE001
        log.debug("quota planner: observe_usage failed: %s", exc)
        return 0


def observe_headers(account_id: str, headers: Any, key: str = "", ts: Optional[float] = None) -> int:
    """Заголовки ответа API (Custom) → замеры. ``key`` хэшируется: в данных только метка #abcd."""
    try:
        planner.load()
        if not planner.settings.get("enabled", True) or not headers:
            return 0
        ts = _now() if ts is None else ts
        tag = hashlib.sha1(key.encode("utf-8")).hexdigest()[:4] if key else ""
        rows = rows_from_headers(headers, ts, tag)
        return planner.observe_rows(account_id, rows, ts) if rows else 0
    except Exception as exc:  # noqa: BLE001
        log.debug("quota planner: observe_headers failed: %s", exc)
        return 0


def note_request(account_id: str) -> None:
    try:
        planner.note_request(account_id)
    except Exception:  # noqa: BLE001
        pass


def score(account_id: str) -> Optional[float]:
    """Контракт для composite: 0..1 (1 — большой запас до сброса), None — нет данных о квоте."""
    summary = planner.summary(account_id)
    return None if summary is None else summary.get("score")


def status(account_id: str) -> Optional[str]:
    summary = planner.summary(account_id)
    return None if summary is None else summary.get("status")


def rank(account_ids: Iterable[str]) -> List[str]:
    """От «лучше брать» к «лучше беречь»; без данных — посередине, порядок внутри групп сохраняется."""
    ids = list(account_ids)

    def key(item: Tuple[int, str]) -> Tuple[float, int]:
        value = score(item[1])
        return (-(value if value is not None else 0.5), item[0])

    return [acc for _, acc in sorted(enumerate(ids), key=key)]


def reorder(accounts: List[Any]) -> List[Any]:
    """Пул аккаунтов: при «Экономить квоту» отодвигает тех, кому по прогнозу не хватит лимита.

    Стабильно: внутри одной группы риска порядок пула (round-robin/по порядку) не меняется; основные и
    резервные аккаунты не смешиваются. Без настройки — список как есть.
    """
    planner.load()
    if not accounts or not planner.settings.get("save_quota"):
        return accounts
    try:
        def risk(acc: Any) -> int:
            return STATUS_RANK.get(status(acc.id) or "", 0)

        primary = [a for a in accounts if getattr(a, "priority", "") != "reserve"]
        reserve = [a for a in accounts if getattr(a, "priority", "") == "reserve"]
        return sorted(primary, key=risk) + sorted(reserve, key=risk)
    except Exception:  # noqa: BLE001
        return accounts


# ====================================================================== фоновый опрос

async def poll_once(force: bool = False) -> int:
    """Опросить аккаунты с известными лимитами (Antigravity, Kiro …). Возвращает число замеров."""
    planner.load()
    if not planner.settings.get("enabled", True):
        return 0
    from .providers import get as get_provider
    interval = max(3, int(planner.settings.get("poll_minutes") or 10)) * 60
    total = 0
    for acc in _all_accounts():
        if acc.provider not in POLL_PROVIDERS or not acc.enabled or getattr(acc, "status", "") == "invalid":
            continue
        last = planner.polled.get(acc.id, 0)
        if not force and _now() - last < interval:
            continue
        if force and _now() - last < 30:
            continue
        provider = get_provider(acc.provider)
        if provider is None:
            continue
        planner.polled[acc.id] = _now()
        try:
            usage = await asyncio.wait_for(provider.usage(acc), timeout=45)
        except Exception as exc:  # noqa: BLE001
            planner.errors[acc.id] = f"{type(exc).__name__}: {str(exc)[:160]}"
            continue
        total += observe_usage(acc, usage)
        await asyncio.sleep(1.5)
    known = {a.id for a in _all_accounts()}
    if known:
        for acc_id in {s.get("acc") for s in list(planner.series.values())} - known:
            planner.forget_account(str(acc_id))          # аккаунт удалён — его ряды больше не нужны
    planner.save()
    return total


async def poll_loop() -> None:
    try:
        await asyncio.sleep(25)
        while True:
            try:
                await poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("quota planner: poll failed")
            await asyncio.sleep(60)
            planner.save()                    # замеры из заголовков ответов копятся между опросами
    finally:
        try:
            planner.save(force=True)          # выход Proxy: дописать хвост замеров
        except Exception:  # noqa: BLE001
            pass


# ====================================================================== маршруты

try:  # module level: with «from __future__ import annotations» FastAPI resolves annotations here
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover — the planner core works without FastAPI (tests, tools)
    Request = Any  # type: ignore
    JSONResponse = None  # type: ignore


def register(app: Any) -> None:

    def auth(request: Request):
        from .admin_api import _auth
        return _auth(request)

    @app.get("/api/admin/quota/planner")
    async def quota_view(request: Request):
        deny = auth(request)
        if deny:
            return deny
        view = await asyncio.to_thread(planner.view)
        from . import api_budget
        view["api_budgets"] = await asyncio.to_thread(api_budget.view)
        return view

    @app.get("/api/admin/quota/planner/badges")
    async def quota_badges(request: Request):
        deny = auth(request)
        if deny:
            return deny
        return {"badges": await asyncio.to_thread(planner.badges),
                "save_quota": bool(planner.settings.get("save_quota"))}

    @app.post("/api/admin/quota/planner/settings")
    async def quota_settings(request: Request):
        deny = auth(request)
        if deny:
            return deny
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "Ожидался объект", "error_en": "An object was expected"}, status_code=400)
        planner.load()
        for key in ("enabled", "save_quota", "notify"):
            if key in body:
                planner.settings[key] = bool(body[key])
        if "poll_minutes" in body:
            try:
                planner.settings["poll_minutes"] = max(3, min(240, int(body["poll_minutes"])))
            except (TypeError, ValueError):
                return JSONResponse({"error": "Неверный интервал", "error_en": "Bad interval"}, status_code=400)
        planner._dirty = True
        planner.save(force=True)
        return {"settings": dict(planner.settings)}

    @app.post("/api/admin/quota/planner/refresh")
    async def quota_refresh(request: Request):
        deny = auth(request)
        if deny:
            return deny
        added = await poll_once(force=True)
        view = await asyncio.to_thread(planner.view)
        from . import api_budget
        view["api_budgets"] = await asyncio.to_thread(api_budget.view)
        view["added"] = added
        return view

    @app.get("/api/admin/quota/planner/api/accounts/{account_id}/models")
    async def budget_models(account_id: str, request: Request):
        deny = auth(request)
        if deny:
            return deny
        from . import api_budget
        account = next((a for a in _all_accounts() if a.id == account_id), None)
        if account is None:
            return JSONResponse({"error": "Аккаунт не найден", "error_en": "Account not found"}, status_code=404)
        return {"models": await asyncio.to_thread(api_budget.account_models, account)}

    @app.post("/api/admin/quota/planner/api/accounts/{account_id}")
    async def budget_save(account_id: str, request: Request):
        deny = auth(request)
        if deny:
            return deny
        from . import api_budget
        account = next((a for a in _all_accounts() if a.id == account_id), None)
        if account is None:
            return JSONResponse({"error": "Аккаунт не найден", "error_en": "Account not found"}, status_code=404)
        try:
            body = await request.json()
            await asyncio.to_thread(api_budget.save_account, account, body)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc), "error_en": str(exc)}, status_code=400)
        return await asyncio.to_thread(api_budget.view)

    @app.delete("/api/admin/quota/planner/api/accounts/{account_id}")
    async def budget_delete(account_id: str, request: Request):
        deny = auth(request)
        if deny:
            return deny
        from . import api_budget
        await asyncio.to_thread(api_budget.remove_account, account_id)
        return await asyncio.to_thread(api_budget.view)

    @app.post("/api/admin/quota/planner/api/prices/refresh")
    async def budget_prices_refresh(request: Request):
        deny = auth(request)
        if deny:
            return deny
        from . import pricing
        result = await asyncio.to_thread(pricing.refresh_online, force=True, timeout=15.0)
        if not result.get("ok"):
            return JSONResponse({"error": result.get("error") or result.get("reason") or "Price refresh failed"}, status_code=502)
        from . import api_budget
        return await asyncio.to_thread(api_budget.view)
