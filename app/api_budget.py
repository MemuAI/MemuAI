"""Account-scoped USD estimates for API keys. No keys or message content are stored.

A balance is a user-entered snapshot, not a provider balance API. Only requests
observed by this Proxy after that snapshot reduce it. Prices are USD / 1M tokens.
Writes use storage's cross-process transaction (CLI and Proxy share the file).
"""
from __future__ import annotations

import math
import os
import sys
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import pricing
from .model_info import canonical, lookup_known
from .storage import load_json_with_recovery, mutate_json

_cached_stamp = None
_cached_data: Dict[str, Any] = {}
_routes_stamp = None
_routes_cache: Dict[str, set] = {}


def path() -> Path:
    from .config import DATA_DIR
    return Path(os.environ.get("MEMUAI_DATA_DIR") or DATA_DIR) / "memuai" / "api-budgets.json"


def _valid(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("accounts"), dict)


def _models(saved: dict) -> dict:
    value = saved.get("models")
    return value if isinstance(value, dict) else {}


def load() -> Dict[str, Any]:
    global _cached_stamp, _cached_data
    target = path()
    try:
        st = target.stat()
        stamp = (str(target), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (str(target), 0, 0)
    if stamp != _cached_stamp:
        _cached_data = load_json_with_recovery(target, {"version": 1, "accounts": {}}, validator=_valid)
        _cached_stamp = stamp
    return _cached_data  # Read-only snapshot; all writers go through _mutate.


def _amount(value: Any, name: str, *, optional: bool = False) -> Optional[float]:
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name}: expected a non-negative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: expected a non-negative number") from exc
    if not math.isfinite(number) or number < 0 or number > 1e12:
        raise ValueError(f"{name}: expected a finite number between 0 and 1e12")
    return number


def _mutate(fn):
    global _cached_stamp
    result = mutate_json(path(), {"version": 1, "accounts": {}}, fn, validator=_valid, backup=False)
    _cached_stamp = None
    return result


def _prefix(account: Any) -> str:
    if account.provider == "custom":
        return str(account.fields.get("name") or "").strip().lower().replace(" ", "-")
    return str(account.provider)


def account_models(account: Any) -> list:
    from .state import state
    prefix = _prefix(account)
    with state._lock:
        entries = list(state.models.values())
    models = {entry.model: entry.id for entry in entries if entry.provider == account.provider
              and (not entry.account_id or entry.account_id == account.id)
              and (entry.prefix == prefix if account.provider == "custom" else True)}
    saved = load()["accounts"].get(account.id)
    saved = saved if isinstance(saved, dict) else {}
    for raw in _models(saved):
        models.setdefault(raw, f"{prefix}-{raw}" if prefix else raw)
    for raw in (saved.get("usage_models") or {}):
        models.setdefault(raw, f"{prefix}-{raw}" if prefix else raw)
    rows = []
    for raw, public in sorted(models.items()):
        base = pricing.lookup(raw, account.provider, account_id="", account_overrides=False)
        candidates = pricing._candidates(raw)
        key = next((key for key in candidates if key.split("-", 1)[0] in
                    {"claude", "gpt", "gemini", "grok", "deepseek", "qwen", "glm", "kimi", "llama", "mistral"}), canonical(raw))
        known = lookup_known(key) or {}
        override = _models(saved).get(raw)
        effective = price_for(public, account_id=account.id, raw_model=raw)
        rows.append({"model": raw, "id": public, "canonical": key,
                     "label": known.get("label") or raw,
                     "default_price": base.to_dict() if base else None,
                     "price": effective.to_dict() if effective else (base.to_dict() if base else None),
                     "override": override, "usage": (saved.get("usage_models") or {}).get(raw)})
    return rows


def save_account(account: Any, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("An object was expected")
    initial = "balance_usd" in body
    balance = _amount(body.get("balance_usd"), "balance_usd") if initial else None
    until = _amount(body.get("until"), "until", optional=True) if "until" in body else None
    if until and until <= time.time():
        previous = load()["accounts"].get(account.id)
        if not isinstance(previous, dict) or float(previous.get("until") or 0) != until:
            raise ValueError("The planning date must be in the future")
    model_changes = body.get("models")
    if model_changes is not None and (not isinstance(model_changes, dict) or len(model_changes) > 2000):
        raise ValueError("Invalid models table")
    cleaned = {}
    for raw, row in (model_changes or {}).items():
        raw = str(raw).strip()
        if not raw or len(raw) > 512:
            raise ValueError("Invalid model name")
        if row is None:
            cleaned[raw] = None
            continue
        if not isinstance(row, dict):
            raise ValueError("Invalid model price")
        cleaned[raw] = {k: _amount(row.get(k), k, optional=k.startswith("cache_"))
                        for k in ("input", "output", "cache_read", "cache_write")}
        if "caching" in row and not isinstance(row["caching"], bool):
            raise ValueError("caching must be a boolean")
        cleaned[raw]["caching"] = row.get("caching", True)

    def change(data):
        existing = data["accounts"].get(account.id)
        if not isinstance(existing, dict) and not initial:
            raise ValueError("Enter the current balance to add this account")
        if not isinstance(existing, dict):
            data["accounts"][account.id] = {}
        saved = data["accounts"][account.id]
        saved.update({"prefix": _prefix(account), "provider": account.provider, "label": account.label})
        if initial:
            # Explicit balance snapshot resets just the usage period, not custom prices.
            saved.update({"balance_usd": balance, "started_at": time.time(), "spent_usd": 0.0,
                          "requests": 0, "unknown_requests": 0, "estimated_requests": 0, "usage_models": {}})
        if "until" in body:
            saved["until"] = until or 0.0
        prices = saved["models"] = _models(saved)
        for raw, row in cleaned.items():
            if row is None:
                prices.pop(raw, None)
            else:
                prices[raw] = row
        saved["updated_at"] = time.time()
        return True

    return _mutate(change)["accounts"][account.id]


def remove_account(account_id: str) -> None:
    def change(data):
        return data["accounts"].pop(account_id, None) is not None
    _mutate(change)


def _raw(saved: dict, model: str) -> str:
    prefix = str(saved.get("prefix") or "")
    return model[len(prefix) + 1:] if prefix and model.lower().startswith(prefix.lower() + "-") else model


def _live_routes() -> Dict[str, set]:
    """CLI must not load Proxy state just to price a model. Cache only ids/prefixes."""
    global _routes_stamp, _routes_cache
    state_module = sys.modules.get(__package__ + ".state")
    if state_module is not None:
        current = getattr(state_module, "state", None)
        if current is not None:
            with current._lock:
                accounts = list(current.accounts.values())
            routes: Dict[str, set] = {}
            for account in accounts:
                routes.setdefault(_prefix(account), set()).add(account.id)
            return routes
    target = path().parent.parent / "accounts.json"
    try:
        st = target.stat()
        stamp = (str(target), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if stamp != _routes_stamp:
        try:
            rows = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
        routes = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            provider = str(row.get("provider") or "")
            prefix = str((row.get("fields") or {}).get("name") or "").strip().lower().replace(" ", "-") if provider == "custom" else provider
            if prefix and row.get("id"):
                routes.setdefault(prefix, set()).add(str(row["id"]))
        _routes_cache, _routes_stamp = routes, stamp
    return _routes_cache


def price_for(model: str, *, account_id: str = "", raw_model: str = "") -> Optional[pricing.Price]:
    """No cross-account leakage: infer a prefix only if all matching profiles agree."""
    if model.startswith("@") and ":" in model:
        connection, raw = model[1:].split(":", 1)
        model = connection + "-" + raw
    saved_accounts = load()["accounts"]
    matches = [saved_accounts[account_id]] if isinstance(saved_accounts.get(account_id), dict) else []
    if account_id and not matches:
        return None
    if not account_id:
        # A bare canonical model can be shared by many providers. Never price it by
        # an unrelated API key. Match the full, configured public connection prefix.
        routes = _live_routes()
        prefix = next((prefix for prefix in sorted(routes, key=len, reverse=True)
                       if prefix and model.lower().startswith(prefix.lower() + "-")), "")
        ids = routes.get(prefix) or set()
        if not ids or any(key not in saved_accounts for key in ids):
            return None  # Another account can serve the same id at a different rate.
        matches = [saved_accounts[key] for key in ids]
    prices = []
    for saved in matches:
        if not isinstance(saved, dict):
            return None
        raw = raw_model or (model if account_id and model in _models(saved) else _raw(saved, model))
        row = _models(saved).get(raw)
        if not isinstance(row, dict):
            prices.append(None)
            continue
        value = pricing._price_from_row(row, "account_override", raw)
        if value is None:
            prices.append(None)
            continue
        value.cache_read = value.cache_read if row.get("caching", True) and value.cache_read is not None else value.input
        value.cache_write = value.cache_write if row.get("caching", True) and value.cache_write is not None else value.input
        prices.append(value)
    if not prices or any(p is None for p in prices):
        return None
    first = prices[0]
    return first if all(p.to_dict() == first.to_dict() for p in prices[1:]) else None


def _counts(usage: dict) -> dict:
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    return {"input_tokens": max(0, int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)),
            "output_tokens": max(0, int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)),
            "cache_read": max(0, int(details.get("cached_tokens") or 0)) if isinstance(details, dict) else 0,
            "cache_write": max(0, int((details.get("cache_write_tokens") if isinstance(details, dict) else 0)
                                      or usage.get("cache_creation_input_tokens") or 0))}


def apply_usage_price(account: Any, model: str, usage: dict, *, raw_model: str = "") -> None:
    """At the shared chat boundary: CLI, Responses and dashboards get the same price."""
    raw = raw_model or _raw({"prefix": _prefix(account)}, model)
    custom = price_for(raw, account_id=account.id)
    if custom is None:
        return
    estimate = pricing.estimate(raw, provider=account.provider, account_id=account.id, **_counts(usage))
    if "cost" in usage:
        usage.setdefault("provider_cost", usage["cost"])
    usage["cost"] = estimate.cost
    usage["cost_source"] = "account_override"


def record_usage(account: Any, model: str, usage: dict, *, raw_model: str = "") -> None:
    if account.id not in load()["accounts"]:
        return
    counts = _counts(usage)
    raw = raw_model or _raw({"prefix": _prefix(account)}, model)
    result = pricing.estimate(raw, provider=account.provider, account_id=account.id, **counts)
    reported = usage.get("cost")
    reported_ok = isinstance(reported, (int, float)) and not isinstance(reported, bool) and math.isfinite(reported) and reported >= 0
    known = reported_ok or result.price is not None
    cost = float(reported) if reported_ok else result.cost

    def change(data):
        saved = data["accounts"].get(account.id)
        if not saved:
            return False
        saved["requests"] = int(saved.get("requests") or 0) + 1
        saved["spent_usd"] = float(saved.get("spent_usd") or 0) + (cost if known else 0)
        saved["unknown_requests"] = int(saved.get("unknown_requests") or 0) + int(not known)
        saved["estimated_requests"] = int(saved.get("estimated_requests") or 0) + int(bool(usage.get("estimated")))
        row = saved.setdefault("usage_models", {}).setdefault(raw, {"requests": 0, "cost_usd": 0.0})
        row["requests"] += 1
        row["cost_usd"] += cost if known else 0
        for name, value in counts.items():
            row[name] = int(row.get(name) or 0) + value
        return True
    _mutate(change)


def view(accounts=None) -> dict:
    if accounts is None:
        from .state import state
        accounts = list(state.accounts.values())
    data, now = load()["accounts"], time.time()
    rows, available = [], []
    for account in accounts:
        safe = {"id": account.id, "label": account.label or _prefix(account), "provider": account.provider,
                "enabled": account.enabled, "prefix": _prefix(account)}
        saved = data.get(account.id)
        if saved is None:
            available.append(safe)
            continue
        spent = float(saved.get("spent_usd") or 0)
        balance = float(saved.get("balance_usd") or 0)
        remaining = balance - spent
        elapsed = max(0.0, now - float(saved.get("started_at") or now))
        rate = spent / elapsed * 86400 if elapsed >= 3600 and int(saved.get("requests") or 0) >= 3 else None
        until = float(saved.get("until") or 0)
        rows.append({**safe, **{k: saved.get(k, 0) for k in ("started_at", "balance_usd", "requests", "unknown_requests", "estimated_requests", "until")},
                     "spent_usd": spent, "remaining_usd": remaining,
                     "fraction": max(0.0, min(1.0, remaining / balance)) if balance else 0,
                     "daily_spend_usd": rate, "days_left": remaining / rate if rate and remaining > 0 else None,
                     "daily_allowance_usd": max(0.0, remaining) * 86400 / (until - now) if until > now else None,
                     "models": account_models(account)})
    return {"currency": "USD", "accounts": rows, "available": available, "now": now,
            "catalog_age": pricing.cache_age(), "source_url": pricing.SOURCE_URL}
