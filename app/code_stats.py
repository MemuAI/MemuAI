"""Usage statistics of MemuCode — the coding mode of MemuAI, and only it.

Chat history keeps at most 200 conversations, so statistics cannot be
recomputed from it forever. MemuCode therefore appends every model round to a
small ledger aggregated by local day (``memucode-stats.json`` in the active
profile): messages, input/output tokens, the hour histogram, models and the
sessions that were active. The first time the ledger is read, MemuCode
sessions still present in the history are imported once, with estimated
tokens, so the numbers do not start from zero.

Tokens come from the provider's ``usage`` when it reports one; otherwise the
window estimates them (by language, ``token_estimate``) and the day is marked
as containing estimates.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from . import token_estimate
from .storage import load_json_with_recovery, mutate_json

LEDGER = "memucode-stats.json"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_SESSIONS_PER_DAY = 400

def _path(profile_id: Optional[str] = None):
    from .pgdata import MEMU_DIR, _profile_data_name
    return MEMU_DIR / _profile_data_name(LEDGER, profile_id)

def _empty() -> Dict[str, Any]:
    return {"version": 1, "days": {}, "backfilled": False}

def _valid(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("days"), dict)

def _day_row(days: Dict[str, Any], day: str) -> Dict[str, Any]:
    row = days.get(day)
    if not isinstance(row, dict):
        row = days[day] = {"messages": 0, "tokens_in": 0, "tokens_out": 0, "hours": [0] * 24,
                           "models": {}, "sessions": [], "estimated": False}
    if not isinstance(row.get("hours"), list) or len(row["hours"]) != 24:
        row["hours"] = [0] * 24
    return row

def _add(row: Dict[str, Any], *, session: str, model: str, hour: int, messages: int,
         tokens_in: int, tokens_out: int, estimated: bool) -> None:
    row["messages"] = int(row.get("messages") or 0) + messages
    row["tokens_in"] = int(row.get("tokens_in") or 0) + tokens_in
    row["tokens_out"] = int(row.get("tokens_out") or 0) + tokens_out
    row["hours"][max(0, min(23, hour))] += messages
    if model:
        stat = row["models"].setdefault(model[:200], {"messages": 0, "tokens": 0})
        stat["messages"] = int(stat.get("messages") or 0) + messages
        stat["tokens"] = int(stat.get("tokens") or 0) + tokens_in + tokens_out

        stat["tokens_in"] = int(stat.get("tokens_in") or 0) + tokens_in
        stat["tokens_out"] = int(stat.get("tokens_out") or 0) + tokens_out
    if session and session not in row["sessions"] and len(row["sessions"]) < _MAX_SESSIONS_PER_DAY:
        row["sessions"].append(session[:80])
    row["estimated"] = bool(row.get("estimated")) or estimated

def _int(value: Any, high: int = 50_000_000) -> int:
    try:
        return max(0, min(high, int(value)))
    except (TypeError, ValueError):
        return 0

def record(event: Dict[str, Any], profile_id: Optional[str] = None) -> None:
    """One model round of a MemuCode session (plus the user message that started it)."""
    day = str(event.get("day") or "")
    if not _DAY_RE.match(day):
        day = _dt.date.today().isoformat()
    hour = _int(event.get("hour"), 23)
    messages = 1 + (1 if event.get("user_message") else 0)

    def change(raw: Any):
        data = raw if _valid(raw) else _empty()
        _add(_day_row(data["days"], day), session=str(event.get("session") or ""), model=str(event.get("model") or ""),
             hour=hour, messages=messages, tokens_in=_int(event.get("tokens_in")), tokens_out=_int(event.get("tokens_out")),
             estimated=bool(event.get("estimated")))
        return True, data

    mutate_json(_path(profile_id), _empty(), change, backup=False, validator=_valid, repair_invalid=True)

def _tokens(value: Any) -> int:
    if isinstance(value, str):
        return token_estimate.estimate(value)
    if isinstance(value, list):
        return sum(_tokens(item.get("text") if isinstance(item, dict) else item) for item in value)
    return 0

def backfill(chats: List[Dict[str, Any]], profile_id: Optional[str] = None) -> None:
    """Import MemuCode sessions still in the history, once, with estimated tokens."""
    def change(raw: Any):
        data = raw if _valid(raw) else _empty()
        if data.get("backfilled"):
            return False, data
        for chat in chats or []:
            if not isinstance(chat, dict) or (chat.get("mode") or "chat") != "code":
                continue
            context = 0
            pending_user = False
            for message in chat.get("messages") or []:
                if not isinstance(message, dict) or message.get("internal"):
                    continue
                size = _tokens(message.get("content")) + _tokens(message.get("reasoning"))
                if message.get("role") == "user":
                    pending_user = True
                    context += size
                    continue
                if message.get("role") != "assistant":
                    continue
                stamp = message.get("createdAt") or chat.get("updated") or 0
                try:
                    when = _dt.datetime.fromtimestamp(float(stamp) / (1000 if float(stamp) > 1e11 else 1))
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                _add(_day_row(data["days"], when.date().isoformat()), session=str(chat.get("id") or ""),
                     model=str(message.get("model") or ""), hour=when.hour, messages=1 + (1 if pending_user else 0),
                     tokens_in=context, tokens_out=size, estimated=True)
                pending_user = False
                context += size
        data["backfilled"] = True
        return True, data

    mutate_json(_path(profile_id), _empty(), change, backup=False, validator=_valid, repair_invalid=True)

def summary(range_days: Optional[int], today: str = "", profile_id: Optional[str] = None) -> Dict[str, Any]:
    data = load_json_with_recovery(_path(profile_id), _empty(), validator=_valid)
    data = data if _valid(data) else _empty()
    try:
        end = _dt.date.fromisoformat(today) if _DAY_RE.match(str(today or "")) else _dt.date.today()
    except ValueError:
        end = _dt.date.today()
    start = end - _dt.timedelta(days=range_days - 1) if range_days else None
    days: List[Dict[str, Any]] = []
    hours = [0] * 24
    models: Dict[str, Dict[str, int]] = {}
    sessions = set()
    totals = {"messages": 0, "tokens_in": 0, "tokens_out": 0}
    estimated = False
    for day, row in sorted(data["days"].items()):
        if not _DAY_RE.match(day) or not isinstance(row, dict):
            continue
        date = _dt.date.fromisoformat(day)
        if date > end or (start and date < start):
            continue
        tokens = int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)
        days.append({"day": day, "messages": int(row.get("messages") or 0), "tokens": tokens})
        for key in totals:
            totals[key] += int(row.get(key) or 0)
        for index, value in enumerate(row.get("hours") or []):
            if index < 24:
                hours[index] += int(value or 0)
        day_in, day_out = int(row.get("tokens_in") or 0), int(row.get("tokens_out") or 0)
        day_share = day_in / (day_in + day_out) if day_in + day_out else 0.85
        for name, stat in (row.get("models") or {}).items():
            current = models.setdefault(name, {"messages": 0, "tokens": 0, "tokens_in": 0, "tokens_out": 0})
            current["messages"] += int(stat.get("messages") or 0)
            tokens = int(stat.get("tokens") or 0)
            current["tokens"] += tokens
            if "tokens_in" in stat or "tokens_out" in stat:
                current["tokens_in"] += int(stat.get("tokens_in") or 0)
                current["tokens_out"] += int(stat.get("tokens_out") or 0)
            else:

                current["tokens_in"] += int(tokens * day_share)
                current["tokens_out"] += tokens - int(tokens * day_share)
        sessions.update(row.get("sessions") or [])
        estimated = estimated or bool(row.get("estimated"))
    total_tokens = totals["tokens_in"] + totals["tokens_out"]
    ranked = sorted(models.items(), key=lambda item: (-item[1]["tokens"], -item[1]["messages"], item[0]))
    peak = max(range(24), key=lambda index: hours[index]) if any(hours) else None
    costs = _model_costs(models)
    return {
        "range": range_days or "all",
        "today": end.isoformat(),
        "sessions": len(sessions),
        "messages": totals["messages"],
        "tokens": {"input": totals["tokens_in"], "output": totals["tokens_out"], "total": total_tokens},
        "active_days": sum(1 for row in days if row["messages"]),
        "peak_hour": peak,
        "hours": hours,
        "favorite_model": ranked[0][0] if ranked else "",
        "models": [{"model": name, "messages": stat["messages"], "tokens": stat["tokens"],
                    "tokens_in": stat["tokens_in"], "tokens_out": stat["tokens_out"],
                    "share": round(stat["tokens"] / total_tokens * 100, 1) if total_tokens else 0.0,
                    "cost": costs.get(name, {}).get("cost", 0.0), "billing": costs.get(name, {}).get("billing", "")}
                   for name, stat in ranked[:20]],
        "cost": {"api_equivalent": round(sum(row["cost"] for row in costs.values()), 6),
                 "api_billed": round(sum(row["cost"] for row in costs.values() if row["billing"] == "api"), 6),
                 "unknown": [name for name, row in costs.items() if row["billing"] == "unknown"][:20]},
        "days": days,
        "estimated": estimated,
        "first_day": min((row["day"] for row in days), default=""),
    }

def _model_costs(models: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, Any]]:
    """What the counted tokens would cost at public API prices (app.pricing)."""
    try:
        from . import pricing
    except Exception:
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    for name, stat in models.items():
        try:
            estimate = pricing.estimate(name, input_tokens=int(stat.get("tokens_in") or 0),
                                        output_tokens=int(stat.get("tokens_out") or 0))
        except Exception:
            continue
        rows[name] = {"cost": round(estimate.cost, 6), "billing": estimate.billing}
    return rows
