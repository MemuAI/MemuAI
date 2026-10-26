"""Лимиты Antigravity: ответ Google → строки для панели. Без сети и без состояния.

Что на самом деле отдаёт Google (проверено на живых аккаунтах Google AI Pro):
по каждой группе моделей — одно окно: скользящее ~5-часовое, ``resetTime``
в ближайшие часы. Отдельного недельного счётчика в API нет. Поэтому:

* окно, которое кончается позже чем через ~6 часов, — настоящий длинный
  (недельный) лимит, и панель показывает его как есть, с датой сброса;
* иначе недельной цифры у Google нет, и мы не выдумываем процент: по замерам
  (страница лимитов, запросы к модели) считаем, сколько 5-часовых окон аккаунт
  израсходовал за 7 дней, и подписываем это как оценку.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

LONG_AFTER = 6 * 3600
WEEK = 7 * 24 * 3600

MERGE_SECONDS = 300
WINDOW_SECONDS = 5 * 3600
MAX_POINTS = 2048

def iso_ts(value: Any) -> float:
    """'2026-09-27T20:31:38Z' → секунды epoch (0 — нет срока или формат неизвестен)."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() if value else 0.0
    except Exception:
        return 0.0

def used_percent(remaining: float) -> float:
    """Доля остатка → процент расхода: 0,03 % расхода — это не «0 / 100»."""
    used = (1 - remaining) * 100
    return round(used, 2) if used < 10 else round(used, 1)

def bucket_state(bucket: Any) -> Optional[Tuple[float, float]]:
    """(остаток 0..1, время сброса) одного бакета; None — данных нет.

    Google не присылает ``remainingFraction`` = 0 (protobuf опускает нули):
    бакет со сроком сброса, но без доли — это исчерпанный лимит.
    """
    if not isinstance(bucket, dict):
        return None
    reset = iso_ts(bucket.get("resetTime"))
    fraction = bucket.get("remainingFraction")
    if fraction is None:
        return (0.0, reset) if reset else None
    try:
        return max(0.0, min(1.0, float(fraction))), reset
    except (TypeError, ValueError):
        return None

def windows(states: Iterable[Tuple[float, float]], now: float) -> Dict[str, Tuple[float, float]]:
    """{'5h': (остаток, сброс), 'week': (…)} — по самому потраченному бакету группы.

    Общий котёл ограничен самой исчерпанной моделью группы.
    """
    out: Dict[str, Tuple[float, float]] = {}
    for remaining, reset in states:
        kind = "week" if reset and reset - now > LONG_AFTER else "5h"
        if kind not in out or remaining < out[kind][0]:
            out[kind] = (remaining, reset)
    return out

def _points(extra: Any, pool: str) -> List[List[float]]:
    data = extra.get("weekly_quota") if isinstance(extra, dict) else None
    raw = data.get(pool) if isinstance(data, dict) else None
    out: List[List[float]] = []
    for point in raw if isinstance(raw, list) else []:
        try:
            ts, frac = float(point[0]), float(point[1])
            reset = float(point[2]) if len(point) > 2 and point[2] else 0.0
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        out.append([ts, max(0.0, min(1.0, frac)), reset])
    return out

def _window_id(ts: float, reset: float) -> int:

    return int(round(reset)) if reset else int(ts // WINDOW_SECONDS) * WINDOW_SECONDS

def record_point(extra: Any, pool: str, remaining: float, reset: Optional[float] = None,
                 now: Optional[float] = None) -> bool:
    """Добавляет замер (время, остаток, сброс) в ``extra['weekly_quota'][pool]``.

    Возвращает True, если что-то записано. Никогда не падает: у странного
    аккаунта замеры просто не ведутся, а лимиты всё равно показываются.
    """
    if not isinstance(extra, dict):
        return False
    try:
        now = time.time() if now is None else float(now)
        frac = max(0.0, min(1.0, float(remaining)))
        fresh = [p for p in _points(extra, pool) if p[0] >= now - WEEK]
        if not reset and fresh and now - fresh[-1][0] < WINDOW_SECONDS:
            reset = fresh[-1][2]
        reset = float(reset or 0.0)
        last = fresh[-1] if fresh else None
        if last and now - last[0] < MERGE_SECONDS and _window_id(last[0], last[2]) == _window_id(now, reset):
            fresh[-1] = [now, min(last[1], frac), reset]
        else:
            fresh.append([now, frac, reset])
        data = extra.get("weekly_quota")
        if not isinstance(data, dict):
            data = {}
            extra["weekly_quota"] = data
        data[pool] = fresh[-MAX_POINTS:]
        return True
    except Exception:
        return False

def history_windows(extra: Any, pool: str, now: Optional[float] = None) -> Optional[float]:
    """Сколько 5-часовых окон израсходовано за 7 дней (по замерам); None — замеров нет.

    В каждом окне берётся самый низкий замеренный остаток; окна складываются.
    Это нижняя оценка: расход между замерами мы не видели.
    """
    now = time.time() if now is None else float(now)
    lowest: Dict[int, float] = {}
    for ts, frac, reset in _points(extra, pool):
        if ts < now - WEEK:
            continue
        key = _window_id(ts, reset)
        lowest[key] = min(lowest.get(key, 1.0), frac)
    if not lowest:
        return None
    total = sum(1.0 - frac for frac in lowest.values())
    return round(total, 2) if total < 10 else round(total, 1)
