"""Read existing Proxy accounts and quota snapshots, without copying credentials."""
from __future__ import annotations

import asyncio
import math
import re

_proxy = None

def configure(proxy):
    global _proxy
    _proxy = proxy

async def read(path):
    from .panels_usage import UsageWall
    if _proxy is None:
        raise UsageWall("source_gone:proxy", "MemuAI Proxy не подключён / MemuAI Proxy is disconnected")
    code, body = await asyncio.wait_for(_proxy("GET", path), 12)
    if code != 200 or not isinstance(body, dict):
        raise UsageWall("blocked:source-down", "Не удалось прочитать источник MemuAI Proxy / Cannot read the MemuAI Proxy source")
    return body

def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None

async def discover():
    async def safe(path):
        try:
            return await read(path)
        except Exception:
            return {}
    state, planner = await asyncio.gather(safe("/api/admin/pg/state"), safe("/api/admin/quota/planner"))
    budget_ids = {row.get("id") for row in (planner.get("api_budgets") or {}).get("accounts", []) if isinstance(row, dict)}
    tracked_ids = {row.get("id") for row in planner.get("accounts", []) if isinstance(row, dict)}
    return [{"id": str(row["id"]), "provider": str(row.get("provider") or ""),
             "label": str(row.get("label") or row.get("prefix") or row["id"])[:100],
             "enabled": row.get("enabled") is not False,
             "api_budget": row["id"] in budget_ids, "quota_planner": row["id"] in tracked_ids}
            for row in state.get("accounts", []) if isinstance(row, dict) and row.get("id")]

async def fetch(account, secret, lang, now):
    from .panels_usage import UsageWall, make_limit, _ts
    en = lang == "en"
    target = str(account.get("proxy_account_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", target):
        raise UsageWall("needs_login:proxy", "Choose a Proxy account in panel settings" if en else "Выберите аккаунт Proxy в настройках панели")
    source = account["provider"]
    notice = ""
    if source in ("api_budget", "quota_planner"):
        planner = await read("/api/admin/quota/planner")
        rows = (planner.get("api_budgets") or {}).get("accounts", []) if source == "api_budget" else planner.get("accounts", [])
        row = next((row for row in rows if isinstance(row, dict) and str(row.get("id")) == target), None)
        if row is None:
            raise UsageWall("source_gone:proxy", "Account was removed from the quota planner" if en else "Аккаунт удалён из планировщика квот")
        if source == "api_budget":
            balance, spent = number(row.get("balance_usd")), number(row.get("spent_usd"))
            if balance is None or balance <= 0 or spent is None:
                raise UsageWall("blocked:not-found", "Set a positive API balance in the quota planner" if en else "Укажите положительный API-баланс в планировщике квот")

            limit = make_limit("api_balance", "API balance" if en else "Баланс API", spent / balance * 100,
                               None, None, now)
            limit["detail"] = f"${spent:.4f} / ${balance:.4f} · " + ("remaining " if en else "осталось ") + f"${balance - spent:.4f}"
            notice = ("Estimate: only requests observed by Proxy are counted" if en else "Оценка: учтены только запросы, прошедшие через Proxy")
            if row.get("unknown_requests"):
                notice += (f"; unpriced requests: {row['unknown_requests']}" if en else f"; запросов без цены: {row['unknown_requests']}")
            return {"plan": "API · USD", "via": "MemuAI Proxy", "limits": [limit], "notice": notice}
        limits, timestamps = [], []
        stale = bool(row.get("error"))
        for window in row.get("windows", []):
            remaining = number(window.get("remaining"))
            if remaining is None or not 0 <= remaining <= 1:
                continue

            value = make_limit(str(window.get("key") or "quota"), str(window.get("label_en" if en else "label")
                               or window.get("title_en" if en else "title") or window.get("title") or window.get("key") or "Quota"),
                               (1 - remaining) * 100, _ts(window.get("reset")), None, now)
            limits.append(value)
            stamp = _ts(window.get("last"))
            if stamp: timestamps.append(stamp)
            stale = stale or window.get("stale") is True
        notice = "Quota planner snapshot" if en else "Снимок планировщика квот"
        wall = None
        if stale:
            message = "Quota planner data is outdated; refresh the source in Proxy" if en else "Данные планировщика устарели; обновите источник в Proxy"
            notice += ". " + message
            wall = {"status": "blocked:stale", "message": message}
        return {"plan": "Planner" if en else "Планировщик", "via": "MemuAI Proxy", "limits": limits,
                "notice": notice, "stale": stale, "fetched": min(timestamps) if timestamps else None, "wall": wall}
    response = await read("/api/admin/accounts/" + target + "/usage")
    actual = str(response.get("provider") or "")
    if source == "kiro" and actual != source:
        raise UsageWall("source_gone:proxy", "The selected account's provider changed" if en else "Провайдер выбранного аккаунта изменился")
    usage = response.get("usage") or {}
    limits = []
    windows = usage.get("windows") or []
    if windows:
        for window in windows:
            used = number(window.get("used_percent"))
            if used is None:
                continue
            value = make_limit(str(window.get("id") or "quota"), str(window.get("label_en" if en else "label") or window.get("id") or "Quota"),
                               used, _ts(window.get("reset_at") or window.get("resets_at")), None, now)
            limits.append(value)
    else:
        for index, item in enumerate(usage.get("rows") or []):
            used, maximum = number(item.get("used")), number(item.get("limit"))
            if used is None or maximum is None or maximum <= 0:
                continue
            value = make_limit(f"proxy_{index}", str(item.get("display") or item.get("name") or "Quota"), used / maximum * 100,
                               _ts(item.get("reset_at") or usage.get("next_reset")), None, now)
            value["detail"] = f"{used:g} / {maximum:g} {str(item.get('unit') or '')}".strip()
            limits.append(value)
    note = str(usage.get("note_en" if en else "note") or usage.get("notice_en" if en else "notice") or "")[:500]
    if not limits:
        raise UsageWall("blocked:not-found", note or ("This provider does not expose account limits" if en else "Провайдер не публикует лимиты аккаунта"))
    return {"plan": str(usage.get("subscription") or usage.get("plan") or ""), "via": "MemuAI Proxy", "limits": limits, "notice": note}
