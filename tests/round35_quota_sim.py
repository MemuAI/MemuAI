"""Детерминированная симуляция расхода квот для тестов и скриншотов «Планировщика квот» (Round 35)."""
from __future__ import annotations

import math
import random
from typing import Callable, Optional

HOUR = 3600.0

def work_profile(hour: int) -> float:
    """Типичный день: ночью почти ноль, пик днём и вечером (доля «интенсивности» в этот час)."""
    if hour < 8:
        return 0.02
    if hour < 10:
        return 0.5
    if hour < 13:
        return 1.2
    if hour < 14:
        return 0.6
    if hour < 19:
        return 1.0
    if hour < 23:
        return 0.7
    return 0.15

def simulate(planner, account_id: str, title: str, *, period: float, now: float, days: float = 3.0,
             per_hour: float = 0.1, poll: float = 600.0, seed: int = 1, unit: str = "%", limit: float = 100,
             per_request: float = 0.004, anchor: Optional[float] = None, title_en: str = "",
             profile: Callable[[int], float] = work_profile, surge: float = 1.0, local_hour=None) -> None:
    """Пишет в planner ряд замеров за ``days`` суток до ``now``: окно длиной ``period`` со сбросом в
    ``anchor + k*period``; расход ``per_hour`` (доля окна в час на «единичной» нагрузке) × профиль × шум;
    последние 2 часа умножаются на ``surge``. Заодно считает запросы (``per_request`` доли на запрос)."""
    import time as _time
    rng = random.Random(seed)
    hour_of = local_hour or (lambda ts: _time.localtime(ts).tm_hour)
    start = now - days * 86400
    anchor = anchor if anchor is not None else now - (now % period)
    k0 = math.floor((start - anchor) / period)
    reset_at = anchor + (k0 + 1) * period
    remaining = 1.0 - rng.random() * 0.3
    t = start
    key_title_en = title_en or title
    while t <= now + 1e-6:
        if t >= reset_at:
            remaining = 1.0
            reset_at += period
        rate = per_hour * profile(hour_of(t)) * (0.6 + 0.8 * rng.random())
        if now - t < 2 * HOUR:
            rate *= surge
        used = min(remaining, rate * poll / HOUR)
        remaining = max(0.0, remaining - used)
        reqs = int(round(used / per_request)) if per_request else 0
        if reqs:
            planner.note_request(account_id, ts=t, count=reqs)
        row = {"key": title_en.lower().replace(" ", "-") if title_en else title.lower().replace(" ", "-"),
               "title": title, "title_en": key_title_en, "remaining": remaining, "reset": reset_at,
               "limit": limit, "unit": unit, "unit_en": unit, "period": period}
        planner.observe_rows(account_id, [row], ts=t)
        t += poll
