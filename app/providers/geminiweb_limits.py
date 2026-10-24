"""Gemini Web: лимиты аккаунта (раунд 41b) — что показывает окно «Лимиты использования» приложения Gemini.

Три запроса batchexecute (факты протокола — HanaokaYuzu/Gemini-API master, constants.py/client.py, октябрь 2026;
код написан заново):

* ``jSf9Qc`` GetUsageInfo, payload ``[]``, source-path ``/usage`` — новые «вычислительные» лимиты: тело
  ``[тариф, [пункты…], расход AI-кредитов сверх лимита]``; пункт ``[остаток кредитов, доля 0..1, тип, [[сброс, …]]]``,
  тип 1 — окно 5 часов, 2 — неделя, 3 — остаток AI-кредитов. Тариф: 1 Free, 2 Pro, 3/6 Ultra, 4 Plus.
* ``qpEbW`` CheckGeminiQuota — старые счётчики по действиям. Payload ``[[[1,11],[2,11],[6,11]]]`` (Flash) и
  ``[[[1,4],[6,6],[1,15]]]`` (Pro и расширенное мышление). Тело ``[[пункт…]]``, пункт
  ``[[id…, действие], _, процент, [сброс], всего, осталось]``; действие 4 — Pro, 11 — Flash, 15 — Flash Thinking;
  всего = осталось = 0 — без ограничения.
* ``aPya6c`` CheckQuota, payload ``[]`` — дополнительные функции: ``[заблокировано, доля 0..1, [сброс]]``.

Google не публикует эти ответы: любой из них может не прийти или поменять форму — тогда соответствующая строка
просто не показывается, а MemuAI продолжает распознавать исчерпание лимита по ошибке 1037 и тексту ответа.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

RPC_USAGE_INFO = "jSf9Qc"
RPC_GEMINI_QUOTA = "qpEbW"
RPC_EXTRA_QUOTA = "aPya6c"
QUOTA_PAYLOADS: Tuple[Tuple[str, str], ...] = (("[[[1,11],[2,11],[6,11]]]", "flash"), ("[[[1,4],[6,6],[1,15]]]", "pro"))

TIERS: Dict[int, str] = {1: "Free", 2: "Google AI Pro", 3: "Google AI Ultra", 4: "Google AI Plus", 6: "Google AI Ultra"}
WINDOWS: Dict[int, Tuple[str, str, str]] = {1: ("5h", "окно 5 часов", "5-hour window"), 2: ("week", "неделя", "week")}
ACTIONS: Dict[int, Tuple[str, str]] = {4: ("Pro", "Pro"), 11: ("Flash", "Flash"),
                                       15: ("Flash Thinking / расширенное мышление", "Flash Thinking / extended thinking"),
                                       6: ("Pro: расширенное мышление", "Pro: extended thinking")}

# Текст, которым Gemini сообщает об исчерпании лимита вместо ответа (чат, картинки, видео, Deep Research).
LIMIT_RE = re.compile(
    r"(you'?ve (?:reached|hit|used up) (?:your|the) (?:daily |current |usage )?(?:\w+ ){0,3}limit"
    r"|you have reached (?:your|the) (?:\w+ ){0,3}limit|(?:usage|daily|image|video) limit (?:reached|exceeded)"
    r"|limit (?:will )?resets?(?: on| at| in)?|can'?t (?:create|generate|make) (?:any )?more (?:images|videos|pictures)"
    r"|вы (?:достигли|исчерпали|израсходовали) (?:\w+ ){0,3}лимит|лимит (?:\w+ ){0,3}(?:исчерпан|достигнут|сбросится|обновится)"
    r"|не могу (?:больше )?(?:создавать|генерировать) (?:больше )?(?:изображени|картин|видео))",
    re.I)
LIMIT_MAX_LEN = 600


def is_limit_reply(text: str) -> bool:
    """Короткий ответ Gemini «вы достигли лимита …» вместо настоящего ответа."""
    text = " ".join((text or "").split()).replace("’", "'")
    return bool(text) and len(text) <= LIMIT_MAX_LEN and bool(LIMIT_RE.search(text))


def _get(data: Any, path: List[int], default: Any = None) -> Any:
    for key in path:
        if isinstance(data, list) and -len(data) <= key < len(data):
            data = data[key]
        else:
            return default
    return default if data is None else data


def rpc_bodies(text: str, rpcid: str) -> List[Any]:
    """Тела ответа batchexecute для rpcid (JSON-строка в part[2] уже разобрана)."""
    from .geminiweb_proto import FrameParser, iter_parts
    parser = FrameParser()
    out: List[Any] = []
    for frame in parser.feed(text or "") + parser.flush():
        for part in iter_parts(frame):
            if _get(part, [1]) != rpcid:
                continue
            raw = _get(part, [2])
            try:
                body = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                continue
            if body is not None:
                out.append(body)
    return out


def _ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 0:
        return float(value) / 1000 if value > 1e11 else float(value)       # бывает в миллисекундах
    if isinstance(value, str) and value.isdigit():
        return _ts(int(value))
    return None


def parse_usage_info(text: str) -> Dict[str, Any]:
    """jSf9Qc → {"tier": id, "plan": "Google AI Pro", "windows": [...], "ai_credits": n | None}."""
    for body in rpc_bodies(text, RPC_USAGE_INFO):
        if not isinstance(body, list):
            continue
        tier = _get(body, [0])
        out: Dict[str, Any] = {"tier": tier if isinstance(tier, int) else None,
                               "plan": TIERS.get(tier, "") if isinstance(tier, int) else "",
                               "windows": [], "ai_credits": None, "overage": _get(body, [2])}
        for item in _get(body, [1], []) or []:
            kind = _get(item, [2])
            remaining = _get(item, [0])
            if kind == 3:
                out["ai_credits"] = remaining if isinstance(remaining, (int, float)) else None
                continue
            level = _get(item, [1])
            key, ru, en = WINDOWS.get(kind, (f"type{kind}", f"окно {kind}", f"window {kind}"))
            out["windows"].append({"window": key, "label_ru": ru, "label_en": en,
                                   "used_pct": round(level * 100) if isinstance(level, (int, float)) else None,
                                   "remaining": remaining if isinstance(remaining, (int, float)) else None,
                                   "reset": _ts(_get(item, [3, 0, 0]))})
        return out
    return {}


def parse_gemini_quota(text: str) -> List[Dict[str, Any]]:
    """qpEbW → [{"action": 4, "label_ru": "Pro", "used_pct": 40, "total": 100, "remaining": 60, "reset": ts}]."""
    rows: List[Dict[str, Any]] = []
    for body in rpc_bodies(text, RPC_GEMINI_QUOTA):
        for item in _get(body, [0], []) or []:
            ids = _get(item, [0], [])
            action = _get(item, [0, 1])
            level = _get(item, [2])
            total, remaining = _get(item, [4]), _get(item, [5])
            ru, en = ACTIONS.get(action, (f"Действие {action}", f"Action {action}"))
            rows.append({"id": "-".join(map(str, ids)) if isinstance(ids, list) else str(ids), "action": action,
                         "label_ru": ru, "label_en": en,
                         "used_pct": float(level) if isinstance(level, (int, float)) else None,
                         "total": total if isinstance(total, int) else None,
                         "remaining": remaining if isinstance(remaining, int) else None,
                         "unlimited": total == 0 and remaining == 0, "reset": _ts(_get(item, [3, 0]))})
    return rows


def parse_extra_quota(text: str) -> Dict[str, Any]:
    """aPya6c → {"blocked": bool, "used_pct": n | None, "reset": ts | None}."""
    for body in rpc_bodies(text, RPC_EXTRA_QUOTA):
        if not isinstance(body, list):
            continue
        level = _get(body, [1])
        return {"blocked": bool(_get(body, [0])),
                "used_pct": round(level * 100) if isinstance(level, (int, float)) else None,
                "reset": _ts(_get(body, [2, 0]))}
    return {}


def summary(usage: Dict[str, Any], quotas: List[Dict[str, Any]], extra: Dict[str, Any],
            *, paid: Optional[bool] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Всё, что удалось прочитать → формат карточки «Лимиты» Proxy (rows: display/used/limit/unit/reset/note)."""
    now = now or time.time()
    rows: List[Dict[str, Any]] = []
    for win in usage.get("windows") or []:
        if win["used_pct"] is None:
            continue
        row = {"display": f"Gemini — {win['label_ru']}", "display_en": f"Gemini — {win['label_en']}",
               "used": win["used_pct"], "limit": 100, "unit": "%"}
        if win.get("remaining") is not None:
            row["note"], row["note_en"] = f"осталось {win['remaining']} ед.", f"{win['remaining']} units left"
        if win.get("reset"):
            row["reset"] = win["reset"]
        rows.append(row)
    seen = set()
    for q in quotas:
        key = (q["action"], q["id"])
        if key in seen:
            continue
        seen.add(key)
        row = {"display": q["label_ru"], "display_en": q["label_en"]}
        if q["unlimited"]:
            row.update(used=None, limit=None, unit="без ограничения", unit_en="unlimited")
        elif q["total"]:
            row.update(used=q["total"] - (q["remaining"] or 0), limit=q["total"], unit="запросов", unit_en="requests")
        elif q["used_pct"] is not None:
            row.update(used=round(q["used_pct"]), limit=100, unit="%")
        else:
            continue
        if q.get("reset"):
            row["reset"] = q["reset"]
        rows.append(row)
    if extra and (extra.get("used_pct") is not None or extra.get("blocked")):
        row = {"display": "Доп. функции (картинки, видео, Deep Research)",
               "display_en": "Extra features (images, video, Deep Research)",
               "used": 100 if extra.get("blocked") else extra["used_pct"], "limit": 100, "unit": "%"}
        if extra.get("blocked"):
            row["note"], row["note_en"] = "исчерпано до сброса", "used up until the reset"
        if extra.get("reset"):
            row["reset"] = extra["reset"]
        rows.append(row)
    plan = usage.get("plan") or ("" if paid is None else ("Google AI (платный)" if paid else "Free"))
    resets = [r["reset"] for r in rows if r.get("reset") and r["reset"] > now]
    exhausted = [r for r in rows if r.get("limit") and r.get("used") is not None and r["used"] >= r["limit"]]
    out: Dict[str, Any] = {"subscription": plan, "rows": rows, "found": bool(rows or usage),
                           "next_reset": min(resets) if resets else None,
                           "exhausted": [r["display"] for r in exhausted]}
    if usage.get("ai_credits") is not None:
        out["ai_credits"] = usage["ai_credits"]
    if not rows:
        out["notice"] = ("Google не отдал счётчики лимитов для этого аккаунта. Исчерпание лимита MemuAI распознаёт "
                         "по ответу Gemini (ошибка 1037 или текст «вы достигли лимита») и показывает время сброса, "
                         "если Google его назвал.")
        out["notice_en"] = ("Google returned no limit counters for this account. MemuAI detects a used-up limit from "
                            "Gemini's answer (error 1037 or a «you've reached your limit» reply) and shows the reset "
                            "time when Google gives it.")
    return out


def reset_from_text(text: str, now: Optional[float] = None) -> Optional[float]:
    """«…resets at 5:30 PM» / «…in 3 hours» / «сбросится через 2 часа» → время сброса (секунды epoch) или None."""
    now = now or time.time()
    low = (text or "").lower()
    match = re.search(r"(?:in|через)\s+(\d+)\s*(hour|hr|час|minute|min|мин)", low)
    if match:
        n = int(match.group(1))
        return now + n * (3600 if match.group(2).startswith(("h", "ч")) else 60)
    match = re.search(r"(?:at|в)\s+(\d{1,2}):(\d{2})\s*(am|pm)?", low)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if match.group(3) == "pm" and hour < 12:
            hour += 12
        if match.group(3) == "am" and hour == 12:
            hour = 0
        local = time.localtime(now)
        target = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1))
        return target if target > now else target + 86400
    return None


def soonest_reset(limits: Optional[Dict[str, Any]], now: Optional[float] = None) -> Optional[float]:
    """Ближайший сброс среди исчерпанных строк (или вообще ближайший), из последнего прочитанного состояния."""
    if not limits:
        return None
    now = now or time.time()
    rows = limits.get("rows") or []
    full = [r["reset"] for r in rows if r.get("reset") and r["reset"] > now and r.get("limit")
            and r.get("used") is not None and r["used"] >= r["limit"]]
    if full:
        return min(full)
    nxt = limits.get("next_reset")
    return nxt if isinstance(nxt, (int, float)) and nxt > now else None
