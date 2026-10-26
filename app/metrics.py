"""Счётчики трафика прокси для панели «Главная».

Данные лежат в почасовых корзинах и переживают перезапуск, поэтому панель
умеет показывать статистику за сегодня, за сутки, за неделю и за месяц, а не
только с момента старта процесса.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import copy
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from .config import DATA_DIR
from .storage import load_json_with_recovery, save_json

log = logging.getLogger("memuai.metrics")

_WINDOW = 3600.0
HOUR = 3600
DAY = 86400

KEEP_HOURS = 35 * 24

LAT_EDGES = (100, 250, 500, 1000, 2000, 4000, 8000, 15000, 30000, 60000)
METRICS_PATH = os.path.join(DATA_DIR, "metrics.json")
_FLUSH_SECONDS = 20.0

PERIODS: Dict[str, Tuple[Optional[int], int]] = {
    "today": (None, HOUR),
    "day": (DAY, HOUR),
    "week": (7 * DAY, DAY),
    "month": (30 * DAY, DAY),
    "all": (None, DAY),
}

def _new_bucket() -> Dict[str, Any]:
    return {
        "total": 0, "ok": 0, "failed": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "ms": 0,
        "providers": {}, "models": {}, "errors": {},
        "lat": [0] * (len(LAT_EDGES) + 1),
    }

_SLOT_FIELDS = ("total", "ok", "failed", "ms", "prompt_tokens", "completion_tokens",

                "cache_tokens", "reasoning_tokens", "estimated", "billed_n", "billed_micro")

_PROVIDER_KEY = "provider"

def _new_slot() -> Dict[str, int]:
    return {field: 0 for field in _SLOT_FIELDS}

def _coerce_slot(value: Any) -> Dict[str, Any]:
    """A provider/model counter; version-1 files stored a bare request count for models."""
    slot: Dict[str, Any] = _new_slot()
    if isinstance(value, dict):
        for field in _SLOT_FIELDS:
            try:
                slot[field] = max(0, int(value.get(field, 0) or 0))
            except (TypeError, ValueError):
                pass
        provider = value.get(_PROVIDER_KEY)
        if isinstance(provider, str) and provider:
            slot[_PROVIDER_KEY] = provider[:60]
    else:
        try:
            slot["total"] = slot["ok"] = max(0, int(value or 0))
        except (TypeError, ValueError):
            pass
    return slot

def _local_day(stamp: float) -> int:
    """Local midnight (epoch seconds) of ``stamp``: daily bars follow the user's clock, not UTC."""
    local = time.localtime(stamp)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))

def _pct_change(now: float, before: float) -> Optional[float]:
    if not before:
        return None
    return round((now - before) / before * 100, 1)

def _lat_bin(ms: int) -> int:
    for index, edge in enumerate(LAT_EDGES):
        if ms <= edge:
            return index
    return len(LAT_EDGES)

def _percentile_from_hist(hist: List[int], share: float) -> int:
    """Приблизительный перцентиль по гистограмме задержек."""
    total = sum(hist)
    if not total:
        return 0
    target = max(1.0, total * share)
    seen = 0
    for index, count in enumerate(hist):
        seen += count
        if seen >= target:
            low = 0 if index == 0 else LAT_EDGES[index - 1]
            high = LAT_EDGES[index] if index < len(LAT_EDGES) else LAT_EDGES[-1] * 2
            inside = count or 1

            position = (target - (seen - count)) / inside
            return int(low + (high - low) * min(1.0, max(0.0, position)))
    return LAT_EDGES[-1]

def json_copy(value: Any) -> Any:
    return copy.deepcopy(value)

def _merge_counter(into: Dict[str, int], source: Dict[str, Any]) -> None:
    for key, value in (source or {}).items():
        into[key] = into.get(key, 0) + int(value or 0)

def _new_point(stamp: int) -> Dict[str, Any]:
    return {"ts": stamp, "total": 0, "ok": 0, "failed": 0, "tokens": 0, "ms": 0, "cost": 0.0}

def pricing_billing(provider: str) -> str:
    try:
        from . import pricing
        return pricing.billing_kind("", provider) if provider and provider != "—" else "unknown"
    except Exception:
        return "unknown"

def _legacy_share(bucket: Dict[str, Any]) -> Dict[str, Tuple[int, int]]:
    """Tokens an hour counted in total but no model slot carries.

    Version-1 files kept token counts per hour only (models were bare request counters),
    so every model of the old history showed $0. The hour's untracked tokens are split
    between the models that have successful requests but no tokens of their own, in
    proportion to those requests. Current data has no untracked remainder.
    """
    slots = {name: _coerce_slot(raw) for name, raw in (bucket.get("models") or {}).items()}
    if not slots:
        return {}
    tracked_p = sum(slot["prompt_tokens"] for slot in slots.values())
    tracked_c = sum(slot["completion_tokens"] for slot in slots.values())
    free_p = int(bucket.get("prompt_tokens", 0) or 0) - tracked_p
    free_c = int(bucket.get("completion_tokens", 0) or 0) - tracked_c
    if free_p <= 0 and free_c <= 0:
        return {}
    bare = {name: slot["ok"] for name, slot in slots.items()
            if slot["ok"] > 0 and not slot["prompt_tokens"] and not slot["completion_tokens"]}
    weight = sum(bare.values())
    if not weight:
        return {}

    def split(amount: int) -> Dict[str, int]:
        amount = max(0, amount)
        parts = {name: amount * count // weight for name, count in bare.items()}
        rest = amount - sum(parts.values())
        for name in sorted(bare, key=lambda key: -bare[key])[:rest]:
            parts[name] += 1
        return parts

    prompt, completion = split(free_p), split(free_c)
    return {name: (prompt[name], completion[name]) for name in bare}

class _Coster:
    """Prices model slots of hourly buckets; one price lookup per model and provider."""

    def __init__(self) -> None:
        try:
            from . import pricing
            self.pricing = pricing
        except Exception as exc:
            log.debug("pricing unavailable: %s", exc)
            self.pricing = None

    def catalog_age(self) -> Optional[int]:
        if self.pricing is None:
            return None
        try:
            age = self.pricing.cache_age()
        except Exception:
            return None
        return int(age) if age is not None else None

    def provider_of(self, model: str, slot: Dict[str, Any]) -> str:
        provider = str(slot.get(_PROVIDER_KEY) or "")
        if provider:
            return provider
        head = str(model).split("-", 1)[0].lower()
        prefixes = getattr(self.pricing, "PROVIDER_PREFIXES", ()) if self.pricing else ()
        return head if head in prefixes else ""

    def cache_saving(self, name: str, provider: str, cache_tokens: int) -> Optional[float]:
        """Round 40: what the cached input saved — the full input price minus the cache-read price (USD)."""
        if not cache_tokens or self.pricing is None:
            return 0.0 if not cache_tokens else None
        price = self.pricing.lookup(name, provider)
        if price is None or not price.input:
            return None
        read_rate = price.cache_read if price.cache_read is not None else price.input * 0.1
        return max(0.0, cache_tokens * (price.input - read_rate) / 1e6)

    def bucket(self, bucket: Dict[str, Any]) -> List[Dict[str, Any]]:
        legacy = _legacy_share(bucket)
        out = []
        for name, raw in (bucket.get("models") or {}).items():
            slot = _coerce_slot(raw)
            provider = self.provider_of(name, slot)
            extra_p, extra_c = legacy.get(name, (0, 0))
            prompt = slot["prompt_tokens"] + extra_p
            completion = slot["completion_tokens"] + extra_c
            cache_tokens = min(prompt, int(slot.get("cache_tokens", 0) or 0))
            item = {"model": name, "provider": provider, "tokens": prompt + completion, "cost": 0.0,
                    "state": "none", "price": None, "billing": "",
                    "legacy_prompt": extra_p, "legacy_completion": extra_c,
                    "cache_tokens": cache_tokens, "cache_saved": None}
            if self.pricing is None:
                item.update(cost=None, state="unknown" if prompt + completion else "none")
                out.append(item)
                continue
            try:
                item["billing"] = self.pricing.billing_kind(name, provider)
                item["cache_saved"] = self.cache_saving(name, provider, cache_tokens)
                if slot.get("billed_n") and slot["billed_n"] >= slot["ok"]:

                    item.update(cost=slot.get("billed_micro", 0) / 1_000_000, state="billed", billing="api")
                elif prompt or completion:
                    estimate = self.pricing.estimate(
                        name, provider=provider, input_tokens=prompt, output_tokens=completion,
                        cache_read=min(prompt, slot.get("cache_tokens", 0)),
                        reasoning_tokens=slot.get("reasoning_tokens", 0))
                    if estimate.price is None:
                        item.update(cost=None, state="unknown")
                    else:
                        price = estimate.price
                        item.update(cost=estimate.cost, state="local" if price.source == "local" else "estimated",
                                    price={"input": price.input, "output": price.output,
                                           "cache_read": price.cache_read, "source": price.source,
                                           "matched": price.matched, "approximate": price.approximate})
            except Exception as exc:
                log.debug("metrics cost estimate failed for %s: %s", name, exc)
                item.update(cost=None, state="unknown" if prompt + completion else "none")
            out.append(item)
        return out

class Metrics:
    def __init__(self, path: str = METRICS_PATH) -> None:
        self._lock = threading.Lock()
        self._path = path
        self.started = time.time()
        self._buckets: Dict[int, Dict[str, Any]] = {}
        self._recent: Deque[tuple] = deque(maxlen=5000)
        self._dirty = False
        self._last_flush = time.time()
        self._load()

    def _load(self) -> None:
        data = load_json_with_recovery(self._path, {},
                                       validator=lambda x: isinstance(x, dict))
        raw = (data or {}).get("hours")
        if not isinstance(raw, dict):
            return
        cutoff = int(time.time()) - KEEP_HOURS * HOUR
        loaded: Dict[int, Dict[str, Any]] = {}
        for key, value in raw.items():
            try:
                stamp = int(key)
            except (TypeError, ValueError):
                continue
            if stamp < cutoff or not isinstance(value, dict):
                continue
            bucket = _new_bucket()
            for field in ("total", "ok", "failed", "prompt_tokens",
                          "completion_tokens", "ms"):
                bucket[field] = int(value.get(field, 0) or 0)
            for field in ("providers", "models"):
                source = value.get(field)
                if isinstance(source, dict):
                    bucket[field] = {str(k)[:200]: _coerce_slot(v) for k, v in source.items()}
            errors = value.get("errors")
            if isinstance(errors, dict):
                bucket["errors"] = {str(k)[:120]: int(v or 0) for k, v in errors.items()
                                    if isinstance(v, (int, float))}
            hist = value.get("lat")
            if isinstance(hist, list) and len(hist) == len(bucket["lat"]):
                bucket["lat"] = [int(x or 0) for x in hist]
            loaded[stamp] = bucket
        self._buckets = loaded

    def _serialize(self) -> Dict[str, Any]:
        return {"version": 3, "saved": int(time.time()),
                "hours": {str(k): v for k, v in self._buckets.items()}}

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            if not force and time.time() - self._last_flush < _FLUSH_SECONDS:
                return
            payload = self._serialize()
            self._dirty = False
            self._last_flush = time.time()
        try:
            save_json(self._path, payload, backup=False)
        except Exception as exc:
            log.debug("metrics flush failed: %s", exc)

    def _bucket_for(self, now: float) -> Dict[str, Any]:
        stamp = int(now // HOUR) * HOUR
        bucket = self._buckets.get(stamp)
        if bucket is None:
            bucket = _new_bucket()
            self._buckets[stamp] = bucket
            cutoff = stamp - KEEP_HOURS * HOUR
            for old in [k for k in self._buckets if k < cutoff]:
                self._buckets.pop(old, None)
        return bucket

    def record(self, *, provider: str, model: str, ok: bool, ms: int,
               prompt_tokens: int = 0, completion_tokens: int = 0,
               error_code: str = "", cache_read_tokens: int = 0, reasoning_tokens: int = 0,
               cost_usd: Optional[float] = None, estimated: bool = False) -> None:
        now = time.time()
        ms = max(0, int(ms or 0))
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        cached = min(pt, max(0, int(cache_read_tokens or 0)))
        reasoning = min(ct, max(0, int(reasoning_tokens or 0)))
        billed = None
        try:
            if cost_usd is not None and float(cost_usd) >= 0:
                billed = int(round(float(cost_usd) * 1_000_000))
        except (TypeError, ValueError):
            billed = None
        with self._lock:
            bucket = self._bucket_for(now)
            bucket["total"] += 1
            bucket["ms"] += ms
            bucket["prompt_tokens"] += pt
            bucket["completion_tokens"] += ct
            slots = [bucket["providers"].setdefault(provider or "—", _new_slot())]
            if model:
                model_slot = bucket["models"].setdefault(str(model)[:200], _new_slot())
                if provider:
                    model_slot[_PROVIDER_KEY] = str(provider)[:60]
                slots.append(model_slot)
            for slot in slots:
                slot["total"] += 1
                slot["ms"] += ms
                slot["prompt_tokens"] += pt
                slot["completion_tokens"] += ct
                slot["cache_tokens"] = slot.get("cache_tokens", 0) + cached
                slot["reasoning_tokens"] = slot.get("reasoning_tokens", 0) + reasoning
                if ok and estimated:
                    slot["estimated"] = slot.get("estimated", 0) + 1
                if ok and billed is not None:
                    slot["billed_n"] = slot.get("billed_n", 0) + 1
                    slot["billed_micro"] = slot.get("billed_micro", 0) + billed
                slot["ok" if ok else "failed"] += 1
            if ok:
                bucket["ok"] += 1
            else:
                bucket["failed"] += 1
                code = str(error_code or "unknown")[:120]
                bucket["errors"][code] = bucket["errors"].get(code, 0) + 1
            if ms > 0:
                bucket["lat"][_lat_bin(ms)] += 1
            self._recent.append((now, bool(ok), ms))
            self._dirty = True
        self.flush()

    def add_tokens(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            bucket = self._bucket_for(time.time())
            bucket["prompt_tokens"] += max(0, int(prompt_tokens or 0))
            bucket["completion_tokens"] += max(0, int(completion_tokens or 0))
            self._dirty = True
        self.flush()

    def _window_start(self, period: str, now: float) -> float:
        span, _ = PERIODS.get(period, PERIODS["day"])
        if period == "today":
            return float(_local_day(now))
        if span is None:
            return 0.0
        return now - span

    @staticmethod
    def _aggregate(buckets: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
        out = {"total": 0, "ok": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0, "ms": 0}
        for _, bucket in buckets:
            for field in out:
                out[field] += int(bucket.get(field, 0) or 0)
        return out

    def snapshot(self, period: str = "day") -> Dict[str, Any]:
        period = period if period in PERIODS else "day"
        now = time.time()
        start = self._window_start(period, now)
        _, step = PERIODS[period]
        with self._lock:

            first_hour = int(start // HOUR) * HOUR if start else 0
            selected = [(stamp, bucket) for stamp, bucket in self._buckets.items() if stamp >= first_hour]
            span = (now - start) if start else 0
            prev_from = first_hour - (int(span // HOUR) + 1) * HOUR if start else None
            previous = [(stamp, json_copy(bucket)) for stamp, bucket in self._buckets.items()
                        if prev_from is not None and prev_from <= stamp < first_hour]
            recent = [row for row in self._recent if now - row[0] <= _WINDOW]

            hour_bucket = self._buckets.get(int(now // HOUR) * HOUR) or {}
            in_hour = [row for row in self._recent if row[0] >= int(now // HOUR) * HOUR]
            before_ok = max(0, int(hour_bucket.get("ok", 0) or 0) - sum(1 for row in in_hour if row[1]))
            before_failed = max(0, int(hour_bucket.get("failed", 0) or 0) - sum(1 for row in in_hour if not row[1]))
            all_total = sum(b["total"] for b in self._buckets.values())
            all_ok = sum(b["ok"] for b in self._buckets.values())
            oldest = min(self._buckets) if self._buckets else int(now)
            selected = [(stamp, json_copy(bucket)) for stamp, bucket in selected]

        agg = self._aggregate(selected)
        total, ok, failed = agg["total"], agg["ok"], agg["failed"]
        providers: Dict[str, Dict[str, Any]] = defaultdict(_new_slot)
        models: Dict[str, Dict[str, Any]] = defaultdict(_new_slot)
        errors: Dict[str, int] = defaultdict(int)
        hist = [0] * (len(LAT_EDGES) + 1)
        series: Dict[int, Dict[str, Any]] = {}
        hours_of_day = [0] * 24
        coster = _Coster()
        model_cost: Dict[str, Dict[str, Any]] = {}
        provider_cost: Dict[str, Dict[str, Any]] = {}

        cache = {"tokens": 0, "saved_usd": 0.0, "unpriced_tokens": 0}

        for stamp, bucket in selected:
            for name, raw in bucket["providers"].items():
                slot = _coerce_slot(raw)
                for field in _SLOT_FIELDS:
                    providers[name][field] += slot[field]
            for name, raw in bucket["models"].items():
                slot = _coerce_slot(raw)
                for field in _SLOT_FIELDS:
                    models[name][field] += slot[field]
                if slot.get(_PROVIDER_KEY):
                    models[name][_PROVIDER_KEY] = slot[_PROVIDER_KEY]
            point = stamp if step == HOUR else _local_day(stamp)
            row = series.setdefault(point, _new_point(point))
            for item in coster.bucket(bucket):
                info = model_cost.setdefault(item["model"], {
                    "cost": 0.0, "unknown_tokens": 0, "states": set(), "price": None, "billing": "",
                    "legacy_prompt": 0, "legacy_completion": 0, "provider": ""})
                info["states"].add(item["state"])
                cache["tokens"] += item["cache_tokens"]
                if item["cache_saved"] is None:
                    cache["unpriced_tokens"] += item["cache_tokens"]
                else:
                    cache["saved_usd"] += item["cache_saved"]
                info["legacy_prompt"] += item["legacy_prompt"]
                info["legacy_completion"] += item["legacy_completion"]
                info["billing"] = item["billing"] or info["billing"]
                info["provider"] = item["provider"] or info["provider"]
                if item["price"] is not None:
                    info["price"] = item["price"]
                owner = item["provider"] or "—"
                pinfo = provider_cost.setdefault(owner, {"cost": 0.0, "unknown": False})
                if item["cost"] is None:
                    info["unknown_tokens"] += item["tokens"]
                    pinfo["unknown"] = True
                else:
                    info["cost"] += item["cost"]
                    pinfo["cost"] += item["cost"]
                    row["cost"] += item["cost"]
            _merge_counter(errors, bucket["errors"])
            for index, count in enumerate(bucket["lat"]):
                hist[index] += int(count or 0)
            row["total"] += bucket["total"]
            row["ok"] += bucket["ok"]
            row["failed"] += bucket["failed"]
            row["tokens"] += bucket["prompt_tokens"] + bucket["completion_tokens"]
            row["ms"] += bucket["ms"]
            hours_of_day[time.localtime(stamp).tm_hour] += bucket["total"]

        if selected or start:
            first = first_hour if start else min(series) if series else int(now // HOUR) * HOUR
            cursor = first if step == HOUR else _local_day(first)
            last = int(now // HOUR) * HOUR if step == HOUR else _local_day(now)
            guard = 0
            while cursor <= last and guard < 2000:
                series.setdefault(cursor, _new_point(cursor))
                cursor = cursor + HOUR if step == HOUR else _local_day(cursor + DAY + 3 * HOUR)
                guard += 1
        points = []
        for key in sorted(series):
            row = series[key]
            row["avg_ms"] = round(row.pop("ms") / row["total"]) if row["total"] else 0
            row["cost"] = round(row["cost"], 6)
            points.append(row)

        def rows_of(source: Dict[str, Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
            result = []
            for name, slot in source.items():
                divisor = slot["total"] or 1
                result.append({
                    key: name, "total": slot["total"], "ok": slot["ok"], "failed": slot["failed"],
                    "avg_ms": round(slot["ms"] / divisor),
                    "success_rate": round(slot["ok"] / divisor * 100, 1),
                    "prompt_tokens": slot["prompt_tokens"], "completion_tokens": slot["completion_tokens"],
                    "cache_tokens": slot.get("cache_tokens", 0), "reasoning_tokens": slot.get("reasoning_tokens", 0),

                    "estimated_requests": slot.get("estimated", 0),
                    "share": round(slot["total"] / total * 100, 1) if total else 0.0,
                })
            result.sort(key=lambda row: (-row["total"], str(row[key])))
            return result

        provider_rows = rows_of(providers, "provider")[:50]
        all_model_rows = rows_of(models, "model")
        totals = {"known": 0.0, "subscription": 0.0, "api": 0.0, "unknown": [], "unknown_tokens": 0,
                  "approximate": False, "legacy": False, "billed": 0.0}
        for row in all_model_rows:
            info = model_cost.get(row["model"]) or {"cost": 0.0, "unknown_tokens": 0, "states": {"none"},
                                                    "price": None, "billing": "", "legacy_prompt": 0,
                                                    "legacy_completion": 0, "provider": ""}
            states = info["states"] - {"none"}
            legacy_tokens = info["legacy_prompt"] + info["legacy_completion"]
            if legacy_tokens:

                row["prompt_tokens"] += info["legacy_prompt"]
                row["completion_tokens"] += info["legacy_completion"]
                row["tokens_estimated"] = legacy_tokens
                totals["legacy"] = True
            row["provider_id"] = info["provider"] or models[row["model"]].get(_PROVIDER_KEY, "")
            row["billing"] = info["billing"] or "unknown"
            price = info["price"]
            row["price"] = price
            if price and price.get("approximate"):
                totals["approximate"] = True
            if "unknown" in states and info["cost"] == 0:
                row["cost_usd"] = None
                row["cost_state"] = "unknown"
            else:
                row["cost_usd"] = round(info["cost"], 6)
                row["cost_state"] = ("partial" if "unknown" in states else "billed" if states == {"billed"}
                                     else "local" if states == {"local"} else "none" if not states else "estimated")
            if "unknown" in states:
                totals["unknown"].append(row["model"])
                totals["unknown_tokens"] += info["unknown_tokens"]
            totals["known"] += info["cost"]
            if "billed" in states:
                totals["billed"] += info["cost"] if states == {"billed"} else 0.0
            if row["billing"] == "subscription":
                totals["subscription"] += info["cost"]
            elif row["billing"] in ("api", "unknown"):
                totals["api"] += info["cost"]
        model_rows = all_model_rows[:25]
        for row in provider_rows:
            info = provider_cost.get(row["provider"])
            row["cost_usd"] = round(info["cost"], 6) if info and not (info["unknown"] and info["cost"] == 0) else None
            row["cost_complete"] = bool(info) and not info["unknown"]
            row["billing"] = pricing_billing(row["provider"])

        prev = self._aggregate(previous) if prev_from is not None else None
        prev_cost = None
        if prev is not None:
            prev_cost = 0.0
            for _stamp, bucket in previous:
                prev_cost += sum(item["cost"] or 0.0 for item in coster.bucket(bucket))
        avg_ms = round(agg["ms"] / total) if total else 0
        success_rate = round(ok / total * 100, 1) if total else None
        comparison = None
        if prev is not None:
            prev_avg = round(prev["ms"] / prev["total"]) if prev["total"] else 0
            prev_rate = round(prev["ok"] / prev["total"] * 100, 1) if prev["total"] else None
            comparison = {
                "total": prev["total"],
                "total_change": _pct_change(total, prev["total"]),
                "tokens_change": _pct_change(agg["prompt_tokens"] + agg["completion_tokens"],
                                             prev["prompt_tokens"] + prev["completion_tokens"]),
                "avg_ms_change": _pct_change(avg_ms, prev_avg) if total and prev["total"] else None,
                "success_rate_delta": round(success_rate - prev_rate, 1)
                if success_rate is not None and prev_rate is not None else None,
                "cost": round(prev_cost or 0.0, 6),
                "cost_change": _pct_change(totals["known"], prev_cost or 0.0),
            }
        busiest = max(range(24), key=lambda hour: hours_of_day[hour]) if any(hours_of_day) else None
        return {
            "period": period,
            "since": int(start),
            "step": step,
            "history_from": int(oldest),
            "keep_days": KEEP_HOURS // 24,
            "uptime_s": int(now - self.started),
            "total": total, "ok": ok, "failed": failed,

            "success_rate": success_rate if success_rate is not None else 100.0,
            "has_data": bool(total),
            "avg_ms": avg_ms,
            "prompt_tokens": agg["prompt_tokens"],
            "completion_tokens": agg["completion_tokens"],
            "tokens_per_request": round((agg["prompt_tokens"] + agg["completion_tokens"]) / ok) if ok else 0,
            "last_hour": len(recent) + before_ok + before_failed,
            "last_hour_failed": sum(1 for row in recent if not row[1]) + before_failed,
            "p50_ms": _percentile_from_hist(hist, 0.5),
            "p95_ms": _percentile_from_hist(hist, 0.95),
            "latency_hist": [{"le": edge, "count": hist[index]} for index, edge in enumerate(LAT_EDGES)]
            + [{"le": None, "count": hist[-1]}],
            "busiest_hour": busiest,

            "cost_usd": round(totals["known"], 6),
            "cost_complete": not totals["unknown"],
            "cost": {
                "total": round(totals["known"], 6),

                "api_equivalent": round(totals["subscription"], 6),

                "api_billed": round(totals["api"], 6),

                "reported": round(totals["billed"], 6),
                "unknown_models": totals["unknown"][:20],
                "unknown_tokens": totals["unknown_tokens"],
                "approximate": totals["approximate"],
                "legacy_estimated": totals["legacy"],
                "catalog_age_s": coster.catalog_age(),
            },
            "cache": {"tokens": cache["tokens"], "saved_usd": round(cache["saved_usd"], 6),
                      "unpriced_tokens": cache["unpriced_tokens"],
                      "share": round(cache["tokens"] / agg["prompt_tokens"] * 100, 1) if agg["prompt_tokens"] else 0.0},
            "comparison": comparison,
            "providers": provider_rows,
            "models": model_rows,
            "models_total": len(all_model_rows),
            "top_models": [{"model": row["model"], "count": row["total"]} for row in model_rows[:8]],
            "top_errors": [{"code": c, "count": n} for c, n in sorted(errors.items(), key=lambda kv: -kv[1])[:8]],
            "series": points,
            "all_time": {"total": all_total, "ok": all_ok,
                         "failed": all_total - all_ok},
        }

    def reset(self) -> None:
        with self._lock:
            self.started = time.time()
            self._buckets = {}
            self._recent.clear()
            self._dirty = True
        self.flush(force=True)

metrics = Metrics()
