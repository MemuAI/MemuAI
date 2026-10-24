"""Round 35: composite models of MemuAI Proxy («Составные модели»).

A composite model is a model id of the user's own (``memu/smart``) that is not served by one provider: every request
runs a small pipeline of rule cards that picks real models of the Proxy and falls back between them.

Data (``<DATA_DIR>/composite_models.json``)::

    {"version": 1, "models": [{
        "id": "memu/smart", "prefix": "memu", "name": "smart", "title": "Умная", "description": "…", "enabled": true,
        "default": {"candidates": [{"model": "reform-qwen3.8-max", "weight": 1}], "strategy": {"type": "fixed"},
                    "filters": {}},
        "rules": [{"id": "r1", "kind": "route|filter|prefer|exclude|settings", "title": "…", "enabled": true,
                   "match": "all|any", "conditions": [{"type": "has_image", "op": "is", "value": true}],
                   "candidates": [...], "strategy": {...}, "filters": {...}, "fallthrough": true,   # route
                   "models": ["id", …],                                                         # prefer / exclude
                   "fallback": {...}}],                                                         # settings
        "fallback": {"on": ["error", "429", "5xx", "timeout", "empty", "content_filter", "context_length"],
                     "connect_timeout": 0, "first_token_timeout": 0, "total_timeout": 0, "retries": 0,
                     "backoff_ms": 600, "backoff": "exponential", "max_attempts": 6, "context_upgrade": true},
        "auto_filter": true}]}

Pipeline of one request (:func:`plan`):

1. facts of the request (:func:`facts_from_request`: pictures, audio, files, estimated tokens, tools, JSON mode,
   stream, reasoning, last user text, system prompt, who calls, endpoint, time, weekday, language, max_tokens, …);
2. the first enabled **route** card whose conditions hold gives the candidate list (otherwise ``default``);
   ``fallthrough`` appends the default list after it;
3. the route's **strategy** orders the candidates (fixed, fastest by latency/TTFT over N minutes, cheapest, highest
   success rate, round-robin, weighted random, least used, sticky per conversation);
4. matching **prefer / exclude / filter / settings** cards change the list and the fallback settings, in card order;
5. the **auto filter** drops candidates that certainly cannot take the request (picture → model without vision, tools
   → provider without native tool calls, too many tokens for a known context window).

:func:`run` (called from ``ChatService.stream_completion``) then tries the candidates one by one: an error, 429, 5xx,
timeout (start of answer / first token / whole answer), empty answer, content filter or context-length error moves to
the next one (a context-length error to the next one with a larger window), with retries and backoff. Once the first
token of a stream reached the client, a break is cured by Smart Stream Recovery (round 40, app/stream_recovery.py):
the next candidate continues the answer from the text already sent, the joint's overlap is cut. Composites may contain composites; cycles are refused when saving and
cut at run time.

The decision trace goes to the ``x-memu-route`` response header (:class:`RouteContextMiddleware`), the request log
(stage ``composite_route``), a ``memuai.event`` chunk (MemuAI window) and per-composite statistics.
"""
from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any, AsyncIterator, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from . import stream_recovery
from .config import DATA_DIR
from .storage import load_json_with_recovery, save_json

log = logging.getLogger("memuai.composite")

STORE_PATH = os.path.join(DATA_DIR, "composite_models.json")
STATS_PATH = os.path.join(DATA_DIR, "composite_stats.json")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}/[a-z0-9][a-z0-9._:-]{0,63}$")
PART_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
MAX_DEPTH = 4
MAX_RULES = 40
MAX_CONDITIONS = 20
MAX_CANDIDATES = 30

RULE_KINDS = ("route", "filter", "prefer", "exclude", "settings")
TRIGGERS = ("error", "429", "5xx", "timeout", "empty", "content_filter", "context_length")
STRATEGIES = ("fixed", "fastest", "cheapest", "success", "round_robin", "weighted", "least_used", "sticky")
FILTER_KEYS = ("vision", "tools", "min_context", "free_only", "json")

#: Condition types: value kind and allowed operators.
NUM_OPS = ("gt", "gte", "lt", "lte", "eq", "ne")
TEXT_OPS = ("contains", "not_contains", "regex", "not_regex", "starts_with", "equals")
SET_OPS = ("in", "not_in")
CONDITIONS: Dict[str, Dict[str, Any]] = {
    "has_image": {"kind": "bool"},
    "has_audio": {"kind": "bool"},
    "has_file": {"kind": "bool"},
    "tokens": {"kind": "number"},
    "needs_tools": {"kind": "bool"},
    "tool_choice": {"kind": "set", "options": ["auto", "none", "required", "function", "unset"]},
    "json_mode": {"kind": "bool"},
    "stream": {"kind": "bool"},
    "reasoning": {"kind": "bool"},
    "effort": {"kind": "set", "options": ["off", "minimal", "low", "medium", "high", "xhigh", "max", "auto", "unset"]},
    "last_user": {"kind": "text"},
    "system": {"kind": "text"},
    "client": {"kind": "set", "options": ["memuai", "api", "public", "internal"]},
    "api_key": {"kind": "text"},
    "user_agent": {"kind": "text"},
    "endpoint": {"kind": "set", "options": ["openai", "anthropic", "responses", "panel", "internal"]},
    "time": {"kind": "time"},
    "weekday": {"kind": "set", "options": ["1", "2", "3", "4", "5", "6", "7"]},
    "language": {"kind": "set", "options": ["ru", "en", "zh", "other"]},
    "max_tokens": {"kind": "number"},
    "temperature": {"kind": "number"},
    "turns": {"kind": "number"},
    "random": {"kind": "percent"},
    # Round 40 (r40-proxy): «Сложность задачи» — app/difficulty.py (heuristics, optionally a fast model with a cache).
    "difficulty": {"kind": "set", "options": ["simple", "chat", "translation", "summary", "creative", "code",
                                              "complex_code", "deep_math", "architecture"]},
}
OPS_BY_KIND = {"bool": ("is",), "number": NUM_OPS, "text": TEXT_OPS, "set": SET_OPS, "time": ("between", "not_between"),
               "percent": ("chance",)}

DEFAULT_FALLBACK: Dict[str, Any] = {
    "on": list(TRIGGERS), "connect_timeout": 0, "first_token_timeout": 0, "total_timeout": 0,
    "retries": 0, "backoff_ms": 600, "backoff": "exponential", "max_attempts": 6, "context_upgrade": True,
    # Round 40: Smart Stream Recovery (app/stream_recovery.py) — a stream that breaks after the first token is
    # continued by the next candidate instead of failing; at most ``recovery_attempts`` times per request.
    "stream_recovery": True, "recovery_attempts": 2,
}

_lock = threading.RLock()


# ---------------------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------------------

class _Store:
    def __init__(self, path: str = STORE_PATH) -> None:
        self.path = path
        self._models: Optional[List[Dict[str, Any]]] = None
        self._mtime = -1.0

    def _load(self) -> List[Dict[str, Any]]:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = 0.0
        if self._models is None or mtime != self._mtime:
            data = load_json_with_recovery(self.path, {"version": 1, "models": []},
                                           validator=lambda x: isinstance(x, dict))
            raw = data.get("models") if isinstance(data, dict) else None
            models = []
            for row in raw if isinstance(raw, list) else []:
                if isinstance(row, dict):
                    try:
                        models.append(normalize(row))
                    except Exception:  # a broken row must not hide the others
                        log.warning("composite: skipped a broken model row")
            self._models, self._mtime = models, mtime
        return self._models

    def all(self) -> List[Dict[str, Any]]:
        with _lock:
            return copy.deepcopy(self._load())

    def get(self, model_id: str) -> Optional[Dict[str, Any]]:
        key = str(model_id or "").strip().lower()
        if not key or "/" not in key:
            return None
        with _lock:
            for row in self._load():
                if row["id"] == key:
                    return row
        return None

    def save_all(self, models: List[Dict[str, Any]]) -> None:
        with _lock:
            save_json(self.path, {"version": 1, "models": models})
            self._models = copy.deepcopy(models)
            try:
                self._mtime = os.path.getmtime(self.path)
            except OSError:
                self._mtime = 0.0

    def upsert(self, model: Dict[str, Any], original_id: str = "") -> Dict[str, Any]:
        with _lock:
            models = [m for m in self._load() if m["id"] not in (model["id"], (original_id or "").lower())]
            position = next((i for i, m in enumerate(self._load()) if m["id"] in (model["id"], (original_id or "").lower())),
                            len(models))
            models.insert(min(position, len(models)), model)
            self.save_all(models)
            return model

    def delete(self, model_id: str) -> bool:
        with _lock:
            models = self._load()
            kept = [m for m in models if m["id"] != str(model_id or "").lower()]
            if len(kept) == len(models):
                return False
            self.save_all(kept)
            return True

    def reset_cache(self) -> None:
        with _lock:
            self._models, self._mtime = None, -1.0


store = _Store()


def all_models() -> List[Dict[str, Any]]:
    return store.all()


def get(model_id: str) -> Optional[Dict[str, Any]]:
    return store.get(model_id)


def is_composite(model_id: Any) -> bool:
    """Is ``model_id`` an (enabled) composite model? Cheap: ids without ``/`` never touch the file."""
    if not isinstance(model_id, str) or "/" not in model_id:
        return False
    row = store.get(model_id)
    return bool(row and row.get("enabled", True))


def ids() -> List[str]:
    return [m["id"] for m in store.all() if m.get("enabled", True)]


# ---------------------------------------------------------------------------------------------------------------------
# Normalisation and validation
# ---------------------------------------------------------------------------------------------------------------------

def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _str(value: Any, limit: int = 200) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _candidates(value: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            item = {"model": item}
        if not isinstance(item, dict):
            continue
        model = _str(item.get("model"), 300)
        if not model or model.lower() in seen:
            continue
        seen.add(model.lower())
        row = {"model": model}
        weight = _float(item.get("weight"), 1.0)
        row["weight"] = max(0.0, min(1000.0, weight if weight is not None else 1.0))
        out.append(row)
    return out[:MAX_CANDIDATES]


def _strategy(value: Any) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    kind = value.get("type") if value.get("type") in STRATEGIES else "fixed"
    out: Dict[str, Any] = {"type": kind}
    if kind in ("fastest", "success"):
        out["window_min"] = _int(value.get("window_min"), 60, 5, 7 * 24 * 60)
        out["explore"] = _int(value.get("explore"), 10, 0, 50)
    if kind == "fastest":
        out["metric"] = value.get("metric") if value.get("metric") in ("latency", "ttft") else "ttft"
    if kind == "least_used":
        out["window_min"] = _int(value.get("window_min"), 24 * 60, 60, 30 * 24 * 60)
        out["by"] = value.get("by") if value.get("by") in ("tokens", "requests") else "tokens"
    if kind == "cheapest":
        out["unknown"] = value.get("unknown") if value.get("unknown") in ("last", "skip") else "last"
    if kind == "sticky":
        out["base"] = value.get("base") if value.get("base") in STRATEGIES and value.get("base") != "sticky" else "fixed"
        out["ttl_min"] = _int(value.get("ttl_min"), 360, 5, 7 * 24 * 60)
    return out


def _filters(value: Any) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    out: Dict[str, Any] = {}
    for key in ("vision", "tools", "free_only", "json"):
        if value.get(key) is True:
            out[key] = True
    if value.get("vision_strict") is True and out.get("vision"):
        out["vision_strict"] = True
    ctx = _int(value.get("min_context"), 0, 0, 100_000_000)
    if ctx:
        out["min_context"] = ctx
    return out


def _fallback(value: Any, partial: bool = False) -> Dict[str, Any]:
    """Fallback settings; ``partial`` keeps only the keys present (a «settings» card overrides some of them)."""
    value = value if isinstance(value, dict) else {}
    base = {} if partial else dict(DEFAULT_FALLBACK)
    if "on" in value or not partial:
        on = value.get("on", base.get("on", list(TRIGGERS)))
        base["on"] = [t for t in TRIGGERS if isinstance(on, list) and t in on]
    for key, hi in (("connect_timeout", 600), ("first_token_timeout", 900), ("total_timeout", 3600)):
        if key in value or not partial:
            base[key] = _int(value.get(key, base.get(key, 0)), 0, 0, hi)
    if "retries" in value or not partial:
        base["retries"] = _int(value.get("retries", base.get("retries", 0)), 0, 0, 5)
    if "backoff_ms" in value or not partial:
        base["backoff_ms"] = _int(value.get("backoff_ms", base.get("backoff_ms", 600)), 600, 0, 60_000)
    if "backoff" in value or not partial:
        base["backoff"] = value.get("backoff") if value.get("backoff") in ("fixed", "exponential") else \
            base.get("backoff", "exponential")
    if "max_attempts" in value or not partial:
        base["max_attempts"] = _int(value.get("max_attempts", base.get("max_attempts", 6)), 6, 1, 30)
    if "context_upgrade" in value or not partial:
        base["context_upgrade"] = bool(value.get("context_upgrade", base.get("context_upgrade", True)))
    if "stream_recovery" in value or not partial:
        base["stream_recovery"] = bool(value.get("stream_recovery", base.get("stream_recovery", True)))
    if "recovery_attempts" in value or not partial:
        base["recovery_attempts"] = _int(value.get("recovery_attempts", base.get("recovery_attempts", 2)), 2, 0, 5)
    return base


def _condition(value: Any) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    kind_name = value.get("type") if value.get("type") in CONDITIONS else "has_image"
    spec = CONDITIONS[kind_name]
    ops = OPS_BY_KIND[spec["kind"]]
    op = value.get("op") if value.get("op") in ops else ops[0]
    raw = value.get("value")
    if spec["kind"] == "bool":
        parsed: Any = raw if isinstance(raw, bool) else str(raw).lower() not in ("false", "0", "no", "нет", "")
    elif spec["kind"] in ("number", "percent"):
        parsed = _float(raw, None)
        if parsed is None:
            parsed = raw if isinstance(raw, str) else None   # kept for the validation message
    elif spec["kind"] == "set":
        items = raw if isinstance(raw, list) else [x for x in re.split(r"[,\s]+", str(raw or "")) if x]
        parsed = [str(x).strip().lower()[:80] for x in items if str(x).strip()][:30]
    else:
        parsed = _str(raw, 2000)
    return {"type": kind_name, "op": op, "value": parsed}


def _rule(value: Any, index: int) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    kind = value.get("kind") if value.get("kind") in RULE_KINDS else "route"
    rule: Dict[str, Any] = {
        "id": _str(value.get("id"), 40) or f"r{index + 1}",
        "kind": kind,
        "title": _str(value.get("title"), 120),
        "enabled": value.get("enabled", True) is not False,
        "match": "any" if value.get("match") == "any" else "all",
        "conditions": [_condition(c) for c in (value.get("conditions") or [])[:MAX_CONDITIONS]
                       if isinstance(c, dict)],
    }
    if kind == "route":
        rule["candidates"] = _candidates(value.get("candidates"))
        rule["strategy"] = _strategy(value.get("strategy"))
        rule["filters"] = _filters(value.get("filters"))
        rule["fallthrough"] = value.get("fallthrough", True) is not False
    elif kind == "filter":
        rule["filters"] = _filters(value.get("filters"))
    elif kind in ("prefer", "exclude"):
        models = value.get("models") if isinstance(value.get("models"), list) else []
        rule["models"] = list(dict.fromkeys(_str(m, 300) for m in models if _str(m, 300)))[:MAX_CANDIDATES]
    else:
        rule["fallback"] = _fallback(value.get("fallback"), partial=True)
    return rule


def normalize(value: Dict[str, Any]) -> Dict[str, Any]:
    """A clean copy of a composite model (unknown keys dropped, numbers clamped). Never raises on odd input."""
    value = value if isinstance(value, dict) else {}
    raw_id = _str(value.get("id"), 100).lower()
    prefix = _str(value.get("prefix"), 32).lower()
    name = _str(value.get("name"), 64).lower()
    if (not prefix or not name) and "/" in raw_id:
        prefix, name = raw_id.split("/", 1)
    model = {
        "id": f"{prefix}/{name}" if prefix and name else raw_id,
        "prefix": prefix, "name": name,
        "title": _str(value.get("title"), 80),
        "description": _str(value.get("description"), 600),
        "enabled": value.get("enabled", True) is not False,
        "default": {
            "candidates": _candidates((value.get("default") or {}).get("candidates")
                                      if isinstance(value.get("default"), dict) else []),
            "strategy": _strategy((value.get("default") or {}).get("strategy")
                                  if isinstance(value.get("default"), dict) else {}),
            "filters": _filters((value.get("default") or {}).get("filters")
                                if isinstance(value.get("default"), dict) else {}),
        },
        "rules": [_rule(r, i) for i, r in enumerate((value.get("rules") or [])[:MAX_RULES]) if isinstance(r, dict)],
        "fallback": _fallback(value.get("fallback")),
        "auto_filter": value.get("auto_filter", True) is not False,
        # Round 40: a fast model that settles «difficulty» when the heuristics are unsure ("" — heuristics only).
        "difficulty_model": _str(value.get("difficulty_model"), 200),
    }
    # Rule ids are unique: duplicates (an imported file, a copied card) are renumbered.
    seen = set()
    for index, rule in enumerate(model["rules"]):
        if rule["id"] in seen:
            rule["id"] = f"r{index + 1}-{hashlib.sha1(str(index).encode()).hexdigest()[:4]}"
        seen.add(rule["id"])
    if isinstance(value.get("created"), (int, float)):
        model["created"] = int(value["created"])
    if isinstance(value.get("updated"), (int, float)):
        model["updated"] = int(value["updated"])
    return model


def _issue(path: str, ru: str, en: str, level: str = "error") -> Dict[str, str]:
    return {"path": path, "message": ru, "message_en": en, "level": level}


def _concrete_ids() -> Dict[str, Any]:
    """Public ids of real models (lower-case → entry) the Proxy can route now."""
    try:
        from .state import state
        from .routing import public_id
        out = {}
        for entry in state.enabled_models():
            out[public_id(entry.prefix, entry.model).lower()] = entry
            out[entry.id.lower()] = entry
        return out
    except Exception:
        return {}


def _graph(models: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    graph: Dict[str, List[str]] = {}
    for model in models:
        refs = [c["model"].lower() for c in model["default"]["candidates"]]
        for rule in model["rules"]:
            refs += [c["model"].lower() for c in rule.get("candidates") or []]
        graph[model["id"]] = [r for r in refs if "/" in r]
    return graph


def find_cycle(models: Iterable[Dict[str, Any]], start: str) -> List[str]:
    """A reference cycle through ``start`` (``[a, b, a]``) or ``[]``."""
    graph = _graph(models)
    path: List[str] = []

    def walk(node: str) -> List[str]:
        if node in path:
            return path[path.index(node):] + [node]
        if node not in graph or len(path) > 50:
            return []
        path.append(node)
        for nxt in graph[node]:
            found = walk(nxt)
            if found:
                return found
        path.pop()
        return []

    return walk(start)


def validate(model: Dict[str, Any], *, original_id: str = "", others: Optional[List[Dict[str, Any]]] = None
             ) -> List[Dict[str, str]]:
    """Errors (``level=error`` blocks saving) and warnings for a normalized composite model."""
    issues: List[Dict[str, str]] = []
    others = [m for m in (others if others is not None else store.all())
              if m["id"] not in (model["id"], (original_id or "").lower())]
    concrete = _concrete_ids()
    composite_ids = {m["id"] for m in others} | {model["id"]}
    if not PART_RE.match(model["prefix"] or "") or len(model["prefix"]) > 32:
        issues.append(_issue("prefix", "Префикс: латинские буквы, цифры, «.», «_», «-» (например, memu).",
                             "Prefix: latin letters, digits, “.”, “_”, “-” (for example memu)."))
    if not PART_RE.match(model["name"] or "") or len(model["name"]) > 64:
        issues.append(_issue("name", "Название: латинские буквы, цифры, «.», «_», «-», «:» (например, smart).",
                             "Name: latin letters, digits, “.”, “_”, “-”, “:” (for example smart)."))
    if not issues and not ID_RE.match(model["id"]):
        issues.append(_issue("name", "Неверный идентификатор модели.", "Invalid model id."))
    if model["id"] in {m["id"] for m in others}:
        issues.append(_issue("name", f"Составная модель «{model['id']}» уже есть.",
                             f"A composite model “{model['id']}” already exists."))
    if model["id"] in concrete:
        issues.append(_issue("name", f"«{model['id']}» — уже имя обычной модели Proxy. Выберите другое.",
                             f"“{model['id']}” is already a regular Proxy model. Pick another name."))

    def check_candidates(rows: List[Dict[str, Any]], path: str, strategy: Dict[str, Any]) -> None:
        for index, row in enumerate(rows):
            mid = row["model"].lower()
            if mid == model["id"]:
                issues.append(_issue(f"{path}.candidates[{index}]", "Модель не может ссылаться на саму себя.",
                                     "A model cannot refer to itself."))
            elif "/" in mid and mid in composite_ids:
                continue
            elif mid not in concrete:
                issues.append(_issue(f"{path}.candidates[{index}]",
                                     f"Модель «{row['model']}» сейчас недоступна в Proxy — она будет пропускаться.",
                                     f"Model “{row['model']}” is not available in the Proxy now — it will be skipped.",
                                     "warning"))
        if strategy.get("type") == "weighted" and rows and not any(r["weight"] > 0 for r in rows):
            issues.append(_issue(f"{path}.strategy", "У всех моделей вес 0 — задайте хотя бы одной вес больше 0.",
                                 "Every model has weight 0 — give at least one a weight above 0."))

    check_candidates(model["default"]["candidates"], "default", model["default"]["strategy"])
    has_route = bool(model["default"]["candidates"])
    for r_index, rule in enumerate(model["rules"]):
        path = f"rules[{r_index}]"
        for c_index, cond in enumerate(rule["conditions"]):
            cpath = f"{path}.conditions[{c_index}]"
            spec = CONDITIONS[cond["type"]]
            value = cond["value"]
            if spec["kind"] in ("number", "percent") and not isinstance(value, (int, float)):
                issues.append(_issue(cpath, "Укажите число.", "Enter a number."))
            elif spec["kind"] == "percent" and not 0 <= value <= 100:
                issues.append(_issue(cpath, "Процент — от 0 до 100.", "Percent must be 0–100."))
            elif spec["kind"] == "set" and not value:
                issues.append(_issue(cpath, "Выберите хотя бы одно значение.", "Pick at least one value."))
            elif spec["kind"] == "time" and not _parse_span(str(value)):
                issues.append(_issue(cpath, "Время в формате 09:00-18:00.", "Time as 09:00-18:00."))
            elif spec["kind"] == "text":
                if not value and cond["op"] != "equals":
                    issues.append(_issue(cpath, "Введите текст для сравнения.", "Enter the text to compare."))
                elif cond["op"] in ("regex", "not_regex"):
                    try:
                        re.compile(value)
                    except re.error as exc:
                        issues.append(_issue(cpath, f"Ошибка в регулярном выражении: {exc}",
                                             f"Regular expression error: {exc}"))
        if rule["kind"] == "route":
            if not rule["candidates"]:
                issues.append(_issue(f"{path}.candidates", "Добавьте хотя бы одну модель в правило.",
                                     "Add at least one model to the rule.",
                                     "error" if rule["enabled"] else "warning"))
            check_candidates(rule["candidates"], path, rule["strategy"])
            if rule["enabled"] and rule["candidates"]:
                has_route = True
            if rule["enabled"] and not rule["conditions"]:
                later = [r for r in model["rules"][r_index + 1:] if r["kind"] == "route" and r["enabled"]]
                if later:
                    issues.append(_issue(path, "У правила нет условий — оно срабатывает всегда, следующие правила "
                                               "«Направить» не сработают никогда.",
                                         "The rule has no conditions — it always matches, so the following "
                                         "“Route” rules never will.", "warning"))
        elif rule["kind"] in ("prefer", "exclude") and not rule["models"]:
            issues.append(_issue(f"{path}.models", "Выберите модели для правила.", "Pick models for the rule.",
                                 "error" if rule["enabled"] else "warning"))
        elif rule["kind"] == "filter" and not rule["filters"]:
            issues.append(_issue(f"{path}.filters", "Отметьте, что фильтр требует от модели.",
                                 "Tick what the filter requires from a model.", "warning"))
    if not has_route:
        issues.append(_issue("default.candidates", "Добавьте модели в основной список или в правило «Направить».",
                             "Add models to the main list or to a “Route” rule."))
    elif not model["default"]["candidates"]:
        issues.append(_issue("default.candidates",
                             "Основной список пуст: запрос, под который не подошло ни одно правило, получит ошибку.",
                             "The main list is empty: a request that matches no rule will get an error.", "warning"))
    cycle = find_cycle(others + [model], model["id"])
    if cycle:
        chain = " → ".join(cycle)
        issues.append(_issue("rules", f"Составные модели ссылаются друг на друга по кругу: {chain}.",
                             f"Composite models refer to each other in a loop: {chain}."))
    fb = model["fallback"]
    if fb["connect_timeout"] and fb["total_timeout"] and fb["connect_timeout"] > fb["total_timeout"]:
        issues.append(_issue("fallback.connect_timeout", "Ожидание начала ответа больше общего лимита времени.",
                             "The wait for the answer to start is longer than the total limit.", "warning"))
    return issues


# ---------------------------------------------------------------------------------------------------------------------
# Request facts
# ---------------------------------------------------------------------------------------------------------------------

#: Request context set by :class:`RouteContextMiddleware` (who calls, the path) and the header sink.
_CTX: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("memu_composite_ctx", default=None)

_IMAGE_PARTS = ("image_url", "input_image", "image")
_AUDIO_PARTS = ("input_audio", "audio")
_FILE_PARTS = ("file", "input_file", "document")


def _text_of(content: Any) -> str:
    from .providers.base import content_to_text
    return content_to_text(content)


def _lang_of(text: str) -> str:
    if not text.strip():
        return "other"
    try:
        from . import token_estimate
        mix = token_estimate.script_mix(token_estimate.features(text[:4000]))
    except Exception:
        mix = {}
    if mix.get("cyrillic", 0) >= 0.25:
        return "ru"
    if mix.get("cjk", 0) >= 0.2:
        return "zh"
    if mix.get("latin", 0) >= 0.4:
        return "en"
    return "other"


def _estimate_tokens(messages: List[Dict[str, Any]], tools: Any = None) -> int:
    try:
        from . import token_estimate
        est = token_estimate.estimate
    except Exception:  # pragma: no cover
        def est(text: Any, model: str = "") -> int:
            return len(str(text)) // 4
    total, images = 0, 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        total += est(_text_of(message.get("content"))) + 4
        content = message.get("content")
        if isinstance(content, list):
            images += sum(1 for p in content if isinstance(p, dict) and p.get("type") in _IMAGE_PARTS)
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                total += est(json.dumps(call.get("function") or {}, ensure_ascii=False))
    if tools:
        try:
            total += est(json.dumps(tools, ensure_ascii=False)[:400_000])
        except (TypeError, ValueError):
            pass
    return total + images * 1000


def _client_from_ctx(ctx: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not ctx:
        return {"client": "internal", "endpoint": "internal", "user_agent": "", "api_key": ""}
    scope = ctx.get("scope") or {}
    path = str(scope.get("path") or "")
    headers = {}
    for key, value in scope.get("headers") or []:
        try:
            headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        except Exception:
            continue
    state_dict = scope.get("state") or {}
    public_key = state_dict.get("public_key") if isinstance(state_dict, dict) else ""
    if path.startswith("/v1/messages"):
        endpoint = "anthropic"
    elif path.startswith("/v1/responses"):
        endpoint = "responses"
    elif path.startswith("/v1/"):
        endpoint = "openai"
    elif path.startswith("/api/"):
        endpoint = "panel"
    else:
        endpoint = "internal"
    if public_key:
        client = "public"
    elif endpoint == "panel":
        client = "memuai"
    elif endpoint == "internal":
        client = "internal"
    else:
        client = "api"
    auth = headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "")
    # The key itself never leaves this function: conditions compare against its name or a short fingerprint.
    key_tag = f"public:{public_key}" if public_key else (("key:" + hashlib.sha256(key.encode()).hexdigest()[:8])
                                                         if key else "")
    return {"client": client, "endpoint": endpoint, "user_agent": headers.get("user-agent", "")[:300],
            "api_key": key_tag}


def facts_from_request(messages: List[Dict[str, Any]], params: Dict[str, Any],
                       ctx: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Everything a condition can look at, computed once per request."""
    params = params or {}
    has_image = has_audio = has_file = False
    last_user = ""
    system_parts: List[str] = []
    first_user = ""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                has_image = has_image or kind in _IMAGE_PARTS
                has_audio = has_audio or kind in _AUDIO_PARTS
                has_file = has_file or kind in _FILE_PARTS
        role = message.get("role")
        if role in ("system", "developer"):
            system_parts.append(_text_of(content))
        elif role == "user":
            text = _text_of(content)
            last_user = text
            if not first_user:
                first_user = text
    tools = params.get("tools")
    needs_tools = bool(tools) or any(isinstance(m, dict) and (m.get("role") == "tool" or m.get("tool_calls"))
                                     for m in messages or [])
    choice = params.get("tool_choice")
    if choice is None:
        tool_choice = "unset"
    elif isinstance(choice, dict):
        tool_choice = "function" if choice.get("type") in ("function", "tool") or choice.get("function") or \
            choice.get("name") else str(choice.get("type") or "auto")
    else:
        tool_choice = str(choice).lower()
        tool_choice = "required" if tool_choice == "any" else tool_choice
    fmt = params.get("response_format")
    json_mode = isinstance(fmt, dict) and str(fmt.get("type") or "") in ("json_object", "json_schema")
    effort = str(params.get("effort") or "").lower() or "unset"
    reasoning = params.get("thinking") is True or effort in ("minimal", "low", "medium", "high", "xhigh", "max", "on")
    stamp = time.localtime(now if now is not None else time.time())
    client = _client_from_ctx(ctx)
    from .difficulty import classify_messages
    difficulty, difficulty_conf = classify_messages(messages, tools=bool(tools))
    return {
        "difficulty": difficulty, "difficulty_conf": difficulty_conf, "difficulty_source": "heuristic",
        "has_image": has_image, "has_audio": has_audio, "has_file": has_file,
        "tokens": _estimate_tokens(messages, tools), "needs_tools": needs_tools, "tool_choice": tool_choice,
        "json_mode": json_mode, "stream": params.get("_stream") is not False, "reasoning": reasoning,
        "effort": effort, "last_user": last_user[-20000:], "system": "\n".join(system_parts)[-20000:],
        "client": client["client"], "api_key": client["api_key"], "user_agent": client["user_agent"],
        "endpoint": client["endpoint"], "time": stamp.tm_hour * 60 + stamp.tm_min, "weekday": stamp.tm_wday + 1,
        "language": _lang_of(last_user), "max_tokens": _float(params.get("max_tokens")),
        "temperature": _float(params.get("temperature")),
        "turns": sum(1 for m in messages or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")),
        "conversation": hashlib.sha256((("\n".join(system_parts))[:400] + "\u0000" + first_user[:2000] + "\u0000"
                                        + client["api_key"]).encode("utf-8", "replace")).hexdigest()[:20],
    }


def facts_from_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """Facts for the «Проверить» simulator: a typed message plus toggles."""
    sample = sample if isinstance(sample, dict) else {}
    text = str(sample.get("text") or "")[:200_000]
    system = str(sample.get("system") or "")[:50_000]
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    content: Any = text
    if sample.get("has_image") or sample.get("has_audio") or sample.get("has_file"):
        content = [{"type": "text", "text": text}]
        if sample.get("has_image"):
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}})
        if sample.get("has_audio"):
            content.append({"type": "input_audio", "input_audio": {"data": "", "format": "wav"}})
        if sample.get("has_file"):
            content.append({"type": "file", "file": {"filename": "doc.pdf"}})
    turns = _int(sample.get("turns"), 1, 1, 10_000)
    for _ in range(max(0, turns - 1) // 2):
        messages.append({"role": "user", "content": "…"})
        messages.append({"role": "assistant", "content": "…"})
    messages.append({"role": "user", "content": content})
    params: Dict[str, Any] = {"_stream": sample.get("stream", True) is not False}
    if sample.get("tools"):
        params["tools"] = [{"type": "function", "function": {"name": "sample", "parameters": {"type": "object"}}}]
        if sample.get("tool_choice") in ("auto", "none", "required"):
            params["tool_choice"] = sample["tool_choice"]
    if sample.get("json"):
        params["response_format"] = {"type": "json_object"}
    if sample.get("reasoning"):
        params["effort"] = str(sample.get("effort") or "high")
    for key in ("max_tokens", "temperature"):
        number = _float(sample.get(key))
        if number is not None:
            params[key] = number
    now = None
    if isinstance(sample.get("time"), str) and re.match(r"^\d{1,2}:\d{2}$", sample["time"]):
        hours, minutes = (int(x) for x in sample["time"].split(":"))
        base = time.localtime()
        now = time.mktime((base.tm_year, base.tm_mon, base.tm_mday, hours % 24, minutes % 60, 0, 0, 0, -1))
    facts = facts_from_request(messages, params, None, now=now)
    weekday = _int(sample.get("weekday"), 0, 0, 7)
    if weekday:
        facts["weekday"] = weekday
    tokens = _int(sample.get("tokens"), 0, 0, 100_000_000)
    if tokens:
        facts["tokens"] = tokens
    client = str(sample.get("client") or "").lower()
    if client in ("memuai", "api", "public", "internal"):
        facts["client"] = client
        facts["endpoint"] = {"memuai": "panel", "internal": "internal"}.get(client, facts["endpoint"])
        if client in ("api", "public") and facts["endpoint"] in ("panel", "internal"):
            facts["endpoint"] = "openai"
    if sample.get("endpoint") in CONDITIONS["endpoint"]["options"]:
        facts["endpoint"] = sample["endpoint"]
    for key in ("user_agent", "api_key"):
        if sample.get(key):
            facts[key] = str(sample[key])[:300]
    return facts


# ---------------------------------------------------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------------------------------------------------

def _parse_span(text: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not match:
        return None
    h1, m1, h2, m2 = (int(x) for x in match.groups())
    if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
        return None
    return (h1 * 60 + m1) % 1440, (h2 * 60 + m2) % 1440 if (h2, m2) != (24, 0) else 1440


def _in_span(minute: int, span: Tuple[int, int]) -> bool:
    start, end = span
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end      # over midnight: 22:00-06:00


def check_condition(cond: Dict[str, Any], facts: Dict[str, Any], rng: Optional[random.Random] = None
                    ) -> Tuple[bool, Any]:
    """``(holds, actual value shown in the simulator)``."""
    kind_name, op, value = cond["type"], cond["op"], cond["value"]
    spec = CONDITIONS.get(kind_name) or {"kind": "bool"}
    actual = facts.get(kind_name)
    kind = spec["kind"]
    if kind == "bool":
        return bool(actual) == bool(value), bool(actual)
    if kind == "number":
        if actual is None or not isinstance(value, (int, float)):
            return False, actual
        return {"gt": actual > value, "gte": actual >= value, "lt": actual < value, "lte": actual <= value,
                "eq": actual == value, "ne": actual != value}[op], actual
    if kind == "set":
        values = [str(v).lower() for v in (value or [])]
        hit = str(actual).lower() in values
        return (hit if op == "in" else not hit), actual
    if kind == "time":
        span = _parse_span(str(value))
        if not span:
            return False, actual
        hit = _in_span(int(actual or 0), span)
        shown = f"{int(actual or 0) // 60:02d}:{int(actual or 0) % 60:02d}"
        return (hit if op == "between" else not hit), shown
    if kind == "percent":
        roll = (rng or random).random() * 100
        return (isinstance(value, (int, float)) and roll < value), round(roll, 1)
    text = str(actual or "")
    needle = str(value or "")
    low, low_needle = text.lower(), needle.lower()
    shown = text if len(text) <= 120 else text[:117] + "…"
    if op == "contains":
        return low_needle in low, shown
    if op == "not_contains":
        return low_needle not in low, shown
    if op == "starts_with":
        return low.lstrip().startswith(low_needle), shown
    if op == "equals":
        return low.strip() == low_needle.strip(), shown
    try:
        hit = re.search(needle, text, re.I | re.S) is not None
    except re.error:
        return False, shown
    return (hit if op == "regex" else not hit), shown


def rule_matches(rule: Dict[str, Any], facts: Dict[str, Any], rng: Optional[random.Random] = None
                 ) -> Tuple[bool, List[Dict[str, Any]]]:
    checks = []
    for cond in rule["conditions"]:
        ok, actual = check_condition(cond, facts, rng)
        checks.append({**cond, "ok": ok, "actual": actual})
    if not checks:
        return True, checks
    hit = all(c["ok"] for c in checks) if rule["match"] == "all" else any(c["ok"] for c in checks)
    return hit, checks


# ---------------------------------------------------------------------------------------------------------------------
# Model knowledge: capabilities, price, measurements
# ---------------------------------------------------------------------------------------------------------------------

def model_caps(model_id: str, concrete: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the pipeline knows about one candidate: ``vision`` yes/no/unknown, ``tools`` bool, ``context``,
    ``price`` ($/1M in, out), ``free``, ``available``, ``composite``."""
    key = str(model_id or "").lower()
    if "/" in key and store.get(key) is not None:
        row = store.get(key)
        return {"composite": True, "available": bool(row.get("enabled", True)), "vision": "unknown", "tools": True,
                "context": None, "price": None, "free": False, "name": row.get("title") or row["id"]}
    concrete = concrete if concrete is not None else _concrete_ids()
    entry = concrete.get(key)
    if entry is None:
        return {"available": False, "vision": "unknown", "tools": False, "context": None, "price": None,
                "free": False}
    out: Dict[str, Any] = {"available": True, "provider": entry.provider, "id": entry.id}
    try:
        from . import model_catalog
        out["vision"] = model_catalog.vision_state(entry.id, entry.provider, entry)["state"]
    except Exception:
        out["vision"] = "unknown"
    try:
        from .tool_protocol import TOOL_PROVIDERS
        tools = entry.provider in TOOL_PROVIDERS
    except Exception:
        tools = False
    summary: Dict[str, Any] = {}
    try:
        from . import model_capabilities
        summary = model_capabilities.capabilities_for(entry.id, entry=entry)
        if summary.get("tools") is False and (summary.get("effective") or {}).get("tools") is False:
            tools = False
    except Exception:
        pass
    out["tools"] = tools
    out["json"] = summary.get("structured") is not False
    context = None
    try:
        from . import model_info
        context = model_info.resolve(entry.prefix, entry.provider, entry.model).get("context")
    except Exception:
        pass
    out["context"] = int(context) if isinstance(context, (int, float)) and context > 0 else \
        (int(summary["context"]) if isinstance(summary.get("context"), (int, float)) else None)
    price = None
    free = False
    try:
        from . import pricing
        found = pricing.lookup(entry.id, entry.provider)
        if found is not None:
            price = (float(found.input), float(found.output))
            free = found.input == 0 and found.output == 0
        elif pricing.billing_kind(entry.id, entry.provider) in ("subscription", "local"):
            price, free = (0.0, 0.0), entry.provider == "local"
    except Exception:
        pass
    if entry.model.lower().endswith(":free"):
        free, price = True, (0.0, 0.0)
    out["price"], out["free"] = price, free
    try:
        from . import model_names
        out["name"] = model_names.pretty(entry.id, prefix=entry.prefix)
    except Exception:
        out["name"] = entry.id
    return out


class _Observations:
    """Recent attempts per real model (ts, ok, ttft ms, total ms): «fastest / most reliable over the last N minutes»."""

    def __init__(self) -> None:
        self._rows: Dict[str, Deque[Tuple[float, bool, int, int]]] = {}
        self._lock = threading.Lock()

    def add(self, model: str, ok: bool, ttft_ms: int, total_ms: int, now: Optional[float] = None) -> None:
        with self._lock:
            rows = self._rows.setdefault(model.lower(), deque(maxlen=300))
            rows.append((now if now is not None else time.time(), bool(ok), max(0, int(ttft_ms)), max(0, int(total_ms))))
            if len(self._rows) > 2000:
                self._rows.pop(next(iter(self._rows)))

    def window(self, model: str, minutes: int, now: Optional[float] = None) -> List[Tuple[float, bool, int, int]]:
        since = (now if now is not None else time.time()) - minutes * 60
        with self._lock:
            return [row for row in self._rows.get(model.lower(), ()) if row[0] >= since]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


observations = _Observations()


def _metric_hours(model_id: str, minutes: int) -> Dict[str, float]:
    """Proxy statistics (hourly buckets of app/metrics.py) for ``model_id`` over the window: requests, ok, ms, tokens."""
    out = {"total": 0.0, "ok": 0.0, "ms": 0.0, "tokens": 0.0}
    try:
        from .metrics import metrics, HOUR
        now = time.time()
        since = int((now - minutes * 60) // HOUR) * HOUR
        with metrics._lock:   # read-only peek; the buckets are small dicts
            for stamp, bucket in metrics._buckets.items():
                if stamp < since:
                    continue
                for name, slot in (bucket.get("models") or {}).items():
                    if name.lower() != model_id.lower():
                        continue
                    out["total"] += slot.get("total", 0)
                    out["ok"] += slot.get("ok", 0)
                    out["ms"] += slot.get("ms", 0)
                    out["tokens"] += slot.get("prompt_tokens", 0) + slot.get("completion_tokens", 0)
    except Exception:
        pass
    return out


def quota_score(model_id: str) -> Optional[float]:
    """Best quota reserve (0..1) among the accounts that serve ``model_id`` — app/quota_planner.py (Round 35, quota
    zone): ``score(account_id)``; ``None`` when the planner is absent or knows nothing."""
    try:
        from . import quota_planner
        from .state import state
    except Exception:
        return None
    entry = _concrete_ids().get(str(model_id or "").lower())
    if entry is None:
        return None
    try:
        accounts = state.usable_accounts(entry.provider)
        if entry.provider == "custom":
            accounts = [a for a in accounts if str(a.fields.get("name", "")).strip().lower().replace(" ", "-")
                        == entry.prefix.lower()]
        if getattr(entry, "account_id", ""):
            accounts = [a for a in accounts if a.id == entry.account_id] or accounts
        scores = [quota_planner.score(a.id) for a in accounts]
    except Exception:
        return None
    known = [float(x) for x in scores if isinstance(x, (int, float))]
    return max(known) if known else None


# ---------------------------------------------------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------------------------------------------------

_rr_counters: Dict[str, int] = {}
_sticky: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
_STICKY_MAX = 4000


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{int(ms)} ms"


def order_candidates(rows: List[Dict[str, Any]], strategy: Dict[str, Any], *, key: str, facts: Dict[str, Any],
                     caps: Dict[str, Dict[str, Any]], rng: Optional[random.Random] = None,
                     peek: bool = False) -> List[Dict[str, Any]]:
    """Order candidate rows; each row gets ``score_ru/score_en`` (why it is where it is). ``peek`` — the simulator:
    round-robin and sticky state is read, not advanced."""
    rng = rng or random
    kind = strategy.get("type", "fixed")
    items = [dict(row, order=i) for i, row in enumerate(rows)]
    if kind == "fixed" or not items:
        for row in items:
            row["score_ru"] = row["score_en"] = f"#{row['order'] + 1}"
        return items
    if kind == "fastest":
        minutes, metric = strategy.get("window_min", 60), strategy.get("metric", "ttft")
        for row in items:
            obs = [o for o in observations.window(row["model"], minutes) if o[1]]
            value = None
            if obs:
                values = [o[2] if metric == "ttft" else o[3] for o in obs]
                value, count, src = sum(values) / len(values), len(values), "obs"
            else:
                hours = _metric_hours(row["model"], minutes)
                if hours["ok"]:
                    value, count, src = hours["ms"] / max(1.0, hours["total"]), int(hours["total"]), "stats"
            row["value"] = value
            if value is None:
                row["unmeasured"] = True
                row["score_ru"], row["score_en"] = "нет замеров", "no measurements"
            else:
                label_ru = "до 1-го токена" if metric == "ttft" and src == "obs" else "ответ"
                label_en = "first token" if metric == "ttft" and src == "obs" else "answer"
                row["score_ru"] = f"{label_ru} {_fmt_ms(value)} · замеров: {count}"
                row["score_en"] = f"{label_en} {_fmt_ms(value)} · samples: {count}"
        items.sort(key=lambda r: (r["value"] is None, r["value"] or 0, r["order"]))
        return _explore(items, strategy, rng, peek)
    if kind == "cheapest":
        kept = []
        for row in items:
            price = (caps.get(row["model"].lower()) or {}).get("price")
            row["value"] = None if price is None else (price[0] * 3 + price[1]) / 4
            if price is None:
                row["score_ru"], row["score_en"] = "цена неизвестна", "price unknown"
                if strategy.get("unknown") == "skip":
                    continue
            elif row["value"] == 0:
                row["score_ru"], row["score_en"] = "бесплатно / подписка", "free / subscription"
            else:
                row["score_ru"] = row["score_en"] = f"${price[0]:g} / ${price[1]:g} · 1M"
            kept.append(row)
        kept.sort(key=lambda r: (r["value"] is None, r["value"] or 0, r["order"]))
        return kept
    if kind == "success":
        minutes = strategy.get("window_min", 60)
        for row in items:
            obs = observations.window(row["model"], minutes)
            total, ok = float(len(obs)), float(sum(1 for o in obs if o[1]))
            if not obs:
                hours = _metric_hours(row["model"], minutes)
                total, ok = hours["total"], hours["ok"]
            row["value"] = (ok + 1) / (total + 2)    # Laplace: one failure of one request is not 0 %
            if not total:
                row["unmeasured"] = True
            row["score_ru"] = f"успешно {int(ok)} из {int(total)}" if total else "нет данных"
            row["score_en"] = f"ok {int(ok)} of {int(total)}" if total else "no data"
        items.sort(key=lambda r: (-r["value"], r["order"]))
        return _explore(items, strategy, rng, peek)
    if kind == "round_robin":
        with _lock:
            start = _rr_counters.get(key, 0)
            if not peek:
                _rr_counters[key] = start + 1
        shift = start % len(items)
        rotated = items[shift:] + items[:shift]
        for index, row in enumerate(rotated):
            row["score_ru"] = row["score_en"] = f"{'→ ' if index == 0 else ''}#{(row['order'] + 1)}"
        return rotated
    if kind == "weighted":
        pool = [r for r in items if r.get("weight", 1) > 0]
        rest = [r for r in items if r.get("weight", 1) <= 0]
        total = sum(r["weight"] for r in pool) or 1.0
        out = []
        while pool:
            pick = rng.random() * sum(r["weight"] for r in pool)
            acc = 0.0
            for index, row in enumerate(pool):
                acc += row["weight"]
                if pick <= acc:
                    out.append(pool.pop(index))
                    break
            else:
                out.append(pool.pop())
        for row in out:
            share = row["weight"] / total * 100
            row["score_ru"] = f"вес {row['weight']:g} · {share:.0f}%"
            row["score_en"] = f"weight {row['weight']:g} · {share:.0f}%"
        for row in rest:
            row["score_ru"], row["score_en"] = "вес 0 · запасная", "weight 0 · spare"
        return out + rest
    if kind == "least_used":
        minutes, by = strategy.get("window_min", 1440), strategy.get("by", "tokens")
        for row in items:
            hours = _metric_hours(row["model"], minutes)
            row["value"] = hours["tokens"] if by == "tokens" else hours["total"]
            row["quota"] = quota_score(row["model"])
            unit_ru = "токенов" if by == "tokens" else "запросов"
            unit_en = "tokens" if by == "tokens" else "requests"
            used_ru = f"{int(row['value']):,} {unit_ru}".replace(",", " ")
            used_en = f"{int(row['value']):,} {unit_en}"
            if row["quota"] is not None:
                row["score_ru"] = f"запас квоты {row['quota'] * 100:.0f}% · {used_ru}"
                row["score_en"] = f"quota left {row['quota'] * 100:.0f}% · {used_en}"
            else:
                row["score_ru"], row["score_en"] = used_ru, used_en
        # The quota planner's forecast (1 = plenty left before the reset) wins; no data = the middle, then usage.
        items.sort(key=lambda r: (-(r["quota"] if r["quota"] is not None else 0.5), r["value"], r["order"]))
        for row in items:
            row.pop("quota", None)
        return items
    if kind == "sticky":
        base = order_candidates(rows, {**strategy, "type": strategy.get("base", "fixed")}, key=key, facts=facts,
                                caps=caps, rng=rng, peek=peek)
        conv = f"{key}|{facts.get('conversation', '')}"
        with _lock:
            remembered = _sticky.get(conv)
        ttl = strategy.get("ttl_min", 360) * 60
        if remembered and time.time() - remembered[1] < ttl:
            hit = [r for r in base if r["model"].lower() == remembered[0]]
            if hit:
                hit[0]["score_ru"], hit[0]["score_en"] = "закреплена за беседой", "pinned to the conversation"
                return hit + [r for r in base if r is not hit[0]]
        return base
    return items


def _explore(items: List[Dict[str, Any]], strategy: Dict[str, Any], rng: Any, peek: bool) -> List[Dict[str, Any]]:
    """«Fastest» / «most reliable» never learn about a model that is never tried: now and then (``explore`` %, 10 by
    default) a model without measurements in the window goes first once. Never in the simulator."""
    share = strategy.get("explore", 10)
    fresh = [r for r in items if r.get("unmeasured")]
    if peek or not fresh or not share or len(fresh) == len(items) or rng.random() * 100 >= share:
        return items
    pick = fresh[0]
    pick["score_ru"], pick["score_en"] = "разведка: нужен замер", "exploring: needs a measurement"
    return [pick] + [r for r in items if r is not pick]


def remember_sticky(key: str, facts: Dict[str, Any], model: str) -> None:
    conv = f"{key}|{facts.get('conversation', '')}"
    with _lock:
        _sticky[conv] = (model.lower(), time.time())
        _sticky.move_to_end(conv)
        while len(_sticky) > _STICKY_MAX:
            _sticky.popitem(last=False)


# ---------------------------------------------------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------------------------------------------------

def _filter_reason(filters: Dict[str, Any], cap: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if cap.get("composite"):
        return None
    if filters.get("vision"):
        state_ = cap.get("vision")
        if state_ == "no" or (filters.get("vision_strict") and state_ != "yes"):
            return ("фильтр: нужна модель со зрением", "filter: needs a vision model")
    if filters.get("tools") and not cap.get("tools"):
        return ("фильтр: нужны нативные вызовы инструментов", "filter: needs native tool calls")
    if filters.get("min_context"):
        ctx = cap.get("context")
        if not ctx or ctx < filters["min_context"]:
            return (f"фильтр: окно меньше {filters['min_context']:,} токенов".replace(",", " "),
                    f"filter: context below {filters['min_context']:,} tokens")
    if filters.get("free_only") and not cap.get("free"):
        return ("фильтр: только бесплатные", "filter: free only")
    if filters.get("json") and cap.get("json") is False:
        return ("фильтр: нужен JSON-режим", "filter: needs JSON mode")
    return None


def _auto_reason(facts: Dict[str, Any], cap: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if cap.get("composite"):
        return None
    if not cap.get("available"):
        return ("модель сейчас недоступна в Proxy", "model is not available in the Proxy now")
    if facts.get("has_image") and cap.get("vision") == "no":
        return ("в запросе картинка, а модель её не видит", "the request has a picture, the model has no vision")
    if facts.get("needs_tools") and not cap.get("tools"):
        return ("нужны инструменты, а у подключения нет нативных вызовов",
                "tools are needed, the connection has no native tool calls")
    ctx = cap.get("context")
    if ctx and facts.get("tokens", 0) > ctx:
        return (f"запрос ≈{facts['tokens']:,} токенов больше окна {ctx:,}".replace(",", " "),
                f"request ≈{facts['tokens']:,} tokens exceeds the {ctx:,} window")
    return None


def plan(model: Dict[str, Any], facts: Dict[str, Any], *, rng: Optional[random.Random] = None,
         peek: bool = False) -> Dict[str, Any]:
    """The decision for one request: which rule, which models in which order, what was skipped and why."""
    rng = rng or random
    concrete = _concrete_ids()
    rules_out: List[Dict[str, Any]] = []
    route: Optional[Dict[str, Any]] = None
    modifiers: List[Dict[str, Any]] = []
    for rule in model["rules"]:
        info = {"id": rule["id"], "title": rule["title"], "kind": rule["kind"], "enabled": rule["enabled"]}
        if not rule["enabled"]:
            info.update(matched=False, skipped="disabled", conditions=[])
            rules_out.append(info)
            continue
        if rule["kind"] == "route" and route is not None:
            info.update(matched=None, skipped="after_route", conditions=[])
            rules_out.append(info)
            continue
        hit, checks = rule_matches(rule, facts, rng)
        info.update(matched=hit, conditions=checks)
        rules_out.append(info)
        if not hit:
            continue
        if rule["kind"] == "route":
            route = rule
        else:
            modifiers.append(rule)
    source = route or model["default"]
    rows = list(source["candidates"])
    if route is not None and route.get("fallthrough"):
        known = {r["model"].lower() for r in rows}
        rows += [dict(r, fallthrough=True) for r in model["default"]["candidates"] if r["model"].lower() not in known]
    caps = {r["model"].lower(): model_caps(r["model"], concrete) for r in rows}
    for rule in modifiers:
        for mid in rule.get("models") or []:
            if mid.lower() not in caps:
                caps[mid.lower()] = model_caps(mid, concrete)
    key = f"{model['id']}|{(route or {}).get('id', 'default')}"
    # The route's own models are ordered by its strategy; models added by «fallthrough» follow in the default order.
    own = [r for r in rows if not r.get("fallthrough")]
    tail = [r for r in rows if r.get("fallthrough")]
    ordered = order_candidates(own, source["strategy"], key=key, facts=facts, caps=caps, rng=rng, peek=peek)
    if tail:
        ordered += order_candidates(tail, model["default"]["strategy"], key=f"{model['id']}|default", facts=facts,
                                    caps=caps, rng=rng, peek=peek)
    for row in ordered:
        row.setdefault("excluded", None)
    fallback = dict(model["fallback"])
    applied: List[Dict[str, Any]] = []
    filters = dict(source.get("filters") or {})
    for row in ordered:
        reason = _filter_reason(filters, caps.get(row["model"].lower(), {})) if filters else None
        if reason:
            row["excluded"] = {"ru": reason[0], "en": reason[1], "by": (route or {}).get("id", "default")}
    for rule in modifiers:
        applied.append({"id": rule["id"], "kind": rule["kind"], "title": rule["title"]})
        if rule["kind"] == "prefer":
            wanted = [m.lower() for m in rule["models"]]
            front = sorted([r for r in ordered if r["model"].lower() in wanted],
                           key=lambda r: wanted.index(r["model"].lower()))
            present = {r["model"].lower() for r in ordered}
            extra = [{"model": m, "weight": 1.0, "order": -1, "score_ru": "добавлена правилом",
                      "score_en": "added by a rule", "excluded": None}
                     for m in rule["models"] if m.lower() not in present]
            for row in front:
                row["score_ru"] = f"{row.get('score_ru', '')} · вперёд".strip(" ·")
                row["score_en"] = f"{row.get('score_en', '')} · preferred".strip(" ·")
            ordered = extra + front + [r for r in ordered if r["model"].lower() not in wanted]
        elif rule["kind"] == "exclude":
            gone = {m.lower() for m in rule["models"]}
            for row in ordered:
                if row["model"].lower() in gone and not row["excluded"]:
                    row["excluded"] = {"ru": f"исключена правилом «{rule['title'] or rule['id']}»",
                                       "en": f"excluded by rule “{rule['title'] or rule['id']}”", "by": rule["id"]}
        elif rule["kind"] == "filter":
            for row in ordered:
                reason = _filter_reason(rule["filters"], caps.get(row["model"].lower(), {}))
                if reason and not row["excluded"]:
                    row["excluded"] = {"ru": reason[0], "en": reason[1], "by": rule["id"]}
        elif rule["kind"] == "settings":
            fallback.update(rule["fallback"])
    if model.get("auto_filter", True):
        for row in ordered:
            if row["excluded"]:
                continue
            reason = _auto_reason(facts, caps.get(row["model"].lower(), {}))
            if reason:
                row["excluded"] = {"ru": reason[0], "en": reason[1], "by": "auto"}
    else:
        for row in ordered:
            if not row["excluded"] and not caps.get(row["model"].lower(), {}).get("available", True):
                row["excluded"] = {"ru": "модель сейчас недоступна в Proxy", "en": "model is not available now",
                                   "by": "auto"}
    for row in ordered:
        cap = caps.get(row["model"].lower(), {})
        row["caps"] = {k: cap.get(k) for k in ("vision", "tools", "context", "free", "composite", "name")
                       if cap.get(k) is not None}
        row.pop("value", None)
        row.pop("unmeasured", None)
    order = [r["model"] for r in ordered if not r["excluded"]]
    return {
        "composite": model["id"], "route": {"id": route["id"], "title": route["title"]} if route else
        {"id": "default", "title": ""}, "rules": rules_out, "modifiers": applied, "candidates": ordered,
        "order": order, "strategy": source["strategy"], "fallback": fallback, "facts": _public_facts(facts),
        "route_key": key,
    }


def _uses_condition(model: Dict[str, Any], kind: str) -> bool:
    return any(cond.get("type") == kind for rule in model.get("rules") or [] if rule.get("enabled", True)
               for cond in rule.get("conditions") or [])


def _public_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in facts.items() if k not in ("last_user", "system", "conversation")}
    out["last_user_chars"] = len(facts.get("last_user") or "")
    out["system_chars"] = len(facts.get("system") or "")
    out["time"] = f"{int(facts.get('time') or 0) // 60:02d}:{int(facts.get('time') or 0) % 60:02d}"
    return out


def simulate(model: Dict[str, Any], sample: Dict[str, Any]) -> Dict[str, Any]:
    model = normalize(model)
    facts = facts_from_sample(sample)
    result = plan(model, facts, rng=random.Random(int(_float((sample or {}).get("seed"), 0) or 0) or None), peek=True)
    result["issues"] = validate(model, original_id=str((sample or {}).get("original_id") or model["id"]))
    return result


# ---------------------------------------------------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------------------------------------------------

class _Stats:
    def __init__(self, path: str = STATS_PATH) -> None:
        self.path = path
        self._data: Optional[Dict[str, Any]] = None
        self._dirty = False
        self._saved = 0.0
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Any]:
        if self._data is None:
            data = load_json_with_recovery(self.path, {}, validator=lambda x: isinstance(x, dict))
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def record(self, composite: str, decision: Dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            row = data.setdefault(composite, {"requests": 0, "ok": 0, "failed": 0, "fallbacks": 0, "rules": {},
                                              "models": {}, "triggers": {}, "recent": [], "since": int(time.time())})
            row["requests"] += 1
            row["ok" if decision.get("ok") else "failed"] += 1
            if decision.get("fallbacks"):
                row["fallbacks"] += 1
            rule = decision.get("rule") or "default"
            row["rules"][rule] = row["rules"].get(rule, 0) + 1
            for attempt in decision.get("attempts") or []:
                slot = row["models"].setdefault(attempt["model"][:200], {"tried": 0, "ok": 0, "failed": 0})
                slot["tried"] += 1
                slot["ok" if attempt.get("ok") else "failed"] += 1
                if attempt.get("trigger"):
                    row["triggers"][attempt["trigger"]] = row["triggers"].get(attempt["trigger"], 0) + 1
            if len(row["models"]) > 200:
                for name in sorted(row["models"], key=lambda n: row["models"][n]["tried"])[:50]:
                    row["models"].pop(name, None)
            row["recent"] = ([{k: decision.get(k) for k in ("ts", "rule", "rule_title", "chosen", "ok", "ms",
                                                           "fallbacks", "attempts", "endpoint", "error",
                                                           "recoveries", "difficulty")}]
                             + row["recent"])[:30]
            if decision.get("recoveries"):
                row["recoveries"] = row.get("recoveries", 0) + int(decision["recoveries"])
            self._dirty = True
        self.flush()

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty or (not force and time.time() - self._saved < 5):
                return
            payload = copy.deepcopy(self._data or {})
            self._dirty, self._saved = False, time.time()
        try:
            save_json(self.path, payload, backup=False)
        except Exception as exc:
            log.debug("composite stats flush failed: %s", exc)

    def get(self, composite: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            if composite is not None:
                return copy.deepcopy(data.get(composite) or {})
            return copy.deepcopy(data)

    def reset(self, composite: str) -> None:
        with self._lock:
            self._load().pop(composite, None)
            self._dirty = True
        self.flush(force=True)

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            data = self._load()
            if old in data and old != new:
                data[new] = data.pop(old)
                self._dirty = True
        self.flush(force=True)


stats = _Stats()
# Statistics are written at most every 5 s; the tail is written when the Proxy exits.
import atexit as _atexit  # noqa: E402
_atexit.register(lambda: stats.flush(force=True))


# ---------------------------------------------------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------------------------------------------------

_CONTEXT_RE = re.compile(r"context[_ ]length|context window|maximum context|max(?:imum)? (?:input )?tokens|"
                         r"too many tokens|prompt is too long|input is too long|reduce the length|"
                         r"контекст\w* (?:окн|длин)|слишком длинн", re.I)
_EMPTY_RE = re.compile(r"без текста|без ответа|without (?:any )?(?:text|an answer)|empty (?:answer|response|reply)", re.I)
_TIMEOUT_RE = re.compile(r"timed? ?out|timeout|не начал отвечать|did not start answering|таймаут", re.I)


_UNAVAILABLE_TYPES = ("authentication_error", "permission_error", "model_unavailable", "model_not_found", "model_disabled")


class _Timeout(Exception):
    def __init__(self, stage: str, seconds: int) -> None:
        super().__init__(stage)
        self.stage, self.seconds = stage, seconds


def classify(exc: BaseException) -> str:
    """Trigger kind of a failed attempt: 429 / 5xx / timeout / empty / content_filter / context_length / error."""
    if isinstance(exc, _Timeout):
        return "timeout"
    status = int(getattr(exc, "status", 0) or 0)
    err_type = str(getattr(exc, "err_type", "") or "")
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'message_en', '')} {exc}"
    if err_type == "content_filter":
        return "content_filter"
    if err_type in _UNAVAILABLE_TYPES:
        # Round 36: a rejected key / unknown or blocked model is the candidate's trouble, not the request's. Chat wraps
        # it as 503, so it was reported as «5xx» and, with «5xx» off, the composite model failed instead of switching.
        return "unavailable"
    if _CONTEXT_RE.search(text):
        return "context_length"
    if status == 429 or err_type in ("rate_limit_error", "quota_exceeded"):
        return "429"
    if _EMPTY_RE.search(text):
        return "empty"
    if err_type == "network_error" and _TIMEOUT_RE.search(text):
        return "timeout"
    if status >= 500 or err_type in ("upstream_error", "network_error", "service_unavailable"):
        return "5xx"
    return "error"


def _should_fallback(kind: str, triggers: List[str]) -> bool:
    # A candidate that cannot answer at all (key rejected, model gone) is always skipped, whatever the triggers say.
    return kind == "unavailable" or kind in triggers or "error" in triggers


TRIGGER_LABELS = {
    "error": ("любая ошибка", "any error"), "429": ("лимит (429)", "rate limit (429)"),
    "5xx": ("ошибка сервиса (5xx)", "service error (5xx)"), "timeout": ("таймаут", "timeout"),
    "empty": ("пустой ответ", "empty answer"), "content_filter": ("фильтр контента", "content filter"),
    "context_length": ("не влезло в окно", "context too long"),
    "unavailable": ("модель недоступна", "model unavailable"),
}


def _header_value(sink: Dict[str, Any]) -> str:
    routes = sink.get("routes") or {}
    if 0 not in routes:
        return ""
    top = routes[0]
    chain = [top.get("chosen") or "-"]
    depth = 1
    while depth in routes:
        chain.append(routes[depth].get("chosen") or "-")
        depth += 1
    parts = [f"model={quote(top['composite'], safe='/:')}", f"rule={quote(str(top.get('rule') or 'default'), safe='')}",
             f"chosen={quote('>'.join(chain), safe='/:>')}", f"attempts={len(top.get('attempts') or [])}"]
    tried = [f"{quote(a['model'], safe='/:')}:{a.get('trigger') or ('ok' if a.get('ok') or a.get('committed') else 'fail')}"
             for a in (top.get("attempts") or [])][:8]
    if tried:
        parts.append("tried=" + ",".join(tried))
    if top.get("recoveries"):
        parts.append(f"recovered={int(top['recoveries'])}")
    return "; ".join(parts)[:1500]


class RouteContextMiddleware:
    """Pure ASGI middleware: remembers who calls (for the «client» conditions) and adds ``x-memu-route``."""

    def __init__(self, app, expose: bool = True) -> None:
        self.app = app
        self.expose = expose

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        sink: Dict[str, Any] = {"scope": scope, "routes": {}}
        token = _CTX.set(sink)

        async def wrapped_send(message):
            if message.get("type") == "http.response.start" and self.expose and sink["routes"]:
                value = _header_value(sink)
                if value:
                    headers = list(message.get("headers") or [])
                    headers.append((b"x-memu-route", value.encode("latin-1", "replace")))
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            _CTX.reset(token)


def _composite_error(message: str, message_en: str, status: int = 503, err_type: str = "service_unavailable"):
    from .chat import CompletionError
    return CompletionError(message, status=status, err_type=err_type, message_en=message_en, provider="composite")


async def run(service: Any, model_id: str, messages: List[Dict[str, Any]], params: Dict[str, Any],
              trace: Any = None) -> AsyncIterator[Dict[str, Any]]:
    """Serve one request of a composite model through ``service.stream_completion`` of its candidates."""
    model = store.get(model_id)
    if model is None or not model.get("enabled", True):
        raise _composite_error(f"Составная модель «{model_id}» не найдена или выключена.",
                               f"Composite model “{model_id}” is not found or switched off.", 404, "model_not_found")
    # The chain of composites above this one rides in the params (a ContextVar token cannot be reset when the stream
    # is finished by another task — the endpoints pump streams in their own tasks).
    chain = tuple(params.get("_composite_chain") or ())
    if model["id"] in chain or len(chain) >= MAX_DEPTH:
        raise _composite_error(f"Составные модели ссылаются друг на друга по кругу: {' → '.join(chain + (model['id'],))}.",
                               f"Composite models refer to each other in a loop: {' → '.join(chain + (model['id'],))}.",
                               508, "composite_loop")
    params = dict(params)
    # «Проверить» passes a dict to get THIS request's route back (the stats' newest row may be another request's).
    route_sink = params.pop("_composite_route_sink", None)
    params["_composite_chain"] = chain + (model["id"],)
    ctx = _CTX.get()
    depth = len(chain)
    started = time.time()
    facts = facts_from_request(messages, params, ctx)
    if model.get("difficulty_model") and _uses_condition(model, "difficulty"):
        from . import difficulty
        level, conf, source = await difficulty.refine(service, model["difficulty_model"], messages,
                                                      (facts["difficulty"], facts["difficulty_conf"]), params=params)
        facts.update(difficulty=level, difficulty_conf=conf, difficulty_source=source)
    decision_plan = plan(model, facts)
    fb = decision_plan["fallback"]
    queue: List[str] = list(decision_plan["order"])
    decision: Dict[str, Any] = {
        "ts": int(started), "composite": model["id"], "rule": decision_plan["route"]["id"],
        "rule_title": decision_plan["route"]["title"], "chosen": "", "attempts": [], "ok": False,
        "fallbacks": 0, "endpoint": facts.get("endpoint"), "difficulty": facts.get("difficulty"),
    }
    if ctx is not None:
        ctx.setdefault("routes", {})[depth] = decision
    if trace is not None:
        try:
            trace.add_stage("composite_route", {
                "composite": model["id"], "rule": decision_plan["route"]["id"],
                "rule_title": decision_plan["route"]["title"], "order": queue[:12],
                "skipped": [{"model": c["model"], "why": c["excluded"]["en"]} for c in decision_plan["candidates"]
                            if c.get("excluded")][:12],
                "modifiers": [m["id"] for m in decision_plan["modifiers"]],
                "facts": {k: decision_plan["facts"].get(k) for k in ("has_image", "tokens", "needs_tools", "stream",
                                                                     "client", "endpoint", "language", "difficulty",
                                                                     "difficulty_source")}})
        except Exception:
            pass
    stream_mode = params.get("_stream") is not False
    attempts_left = int(fb.get("max_attempts") or 6)
    triggers = list(fb.get("on") or [])
    last_exc: Optional[BaseException] = None
    last_candidate = ""
    tried_ctx: Dict[str, Optional[int]] = {}
    # Round 40: Smart Stream Recovery — what the client already has, and the joint of a continuation.
    tap = stream_recovery.Tap() if stream_mode and fb.get("stream_recovery", True) else None
    recoveries_left = int(fb.get("recovery_attempts", 2) or 0)
    joiner: Optional[stream_recovery.Joiner] = None
    current = messages

    def pipe(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = joiner.feed(item) if joiner is not None else [item]
        return [o for piece in out for o in tap.feed(piece)] if tap is not None else out

    try:
        if not queue:
            why = "; ".join(f"{c['model']}: {c['excluded']['ru']}" for c in decision_plan["candidates"]
                            if c.get("excluded"))[:600]
            why_en = "; ".join(f"{c['model']}: {c['excluded']['en']}" for c in decision_plan["candidates"]
                               if c.get("excluded"))[:600]
            raise _composite_error(
                f"Составной модели «{model['id']}» некуда отправить запрос" + (f" ({why})." if why else
                                                                               ": в правиле нет моделей."),
                f"Composite model “{model['id']}” has nowhere to send the request" + (f" ({why_en})." if why_en else
                                                                                      ": the rule has no models."),
                503, "service_unavailable")
        index = 0
        while index < len(queue) and attempts_left > 0:
            candidate = queue[index]
            index += 1
            retries = int(fb.get("retries") or 0)
            for retry in range(retries + 1):
                if attempts_left <= 0:
                    break
                attempts_left -= 1
                attempt = {"model": candidate, "ok": False, "trigger": "", "ms": 0, "retry": retry}
                decision["attempts"].append(attempt)
                attempt_started = time.time()
                try:
                    async for item in _attempt(service, candidate, current, params, trace, fb, stream_mode,
                                               attempt, allow_empty=joiner is not None):
                        if item is _COMMITTED:
                            decision["chosen"] = candidate
                            attempt["committed"] = True
                            continue
                        if item.get("object") == "chat.completion.chunk" and item.get("model") != model["id"]:
                            # Round 36: the stream named the real candidate while the non-stream answer and the other
                            # APIs name the composite; which model answered is in x-memu-route and the journal.
                            item = dict(item, model=model["id"])
                        for piece in pipe(item):
                            yield piece
                    if joiner is not None:
                        for piece in [o for held in joiner.release() for o in (tap.feed(held) if tap else [held])]:
                            yield piece
                    if tap is not None:
                        for piece in tap.flush():
                            yield piece
                    attempt["ok"] = True
                    attempt["ms"] = int((time.time() - attempt_started) * 1000)
                    decision["chosen"] = candidate
                    decision["ok"] = True
                    decision["fallbacks"] = sum(1 for a in decision["attempts"] if not a["ok"])
                    if decision_plan["strategy"].get("type") == "sticky":
                        remember_sticky(decision_plan["route_key"], facts, candidate)
                    return
                except _AfterCommit as late:
                    attempt["trigger"] = classify(late.error)
                    attempt["ms"] = int((time.time() - attempt_started) * 1000)
                    if tap is None or tap.finished or recoveries_left <= 0 or attempts_left <= 0 \
                            or attempt["trigger"] not in stream_recovery.RECOVERABLE:
                        decision["error"] = attempt["trigger"]
                        raise late.error
                    # Smart Stream Recovery: the client keeps its half of the answer, the next candidate goes on.
                    closing = tap.close_with_calls() if tap.broken_call() else None
                    tap.recoveries += 1
                    attempt["recovered"] = True
                    decision["recoveries"] = tap.recoveries
                    note_failure(candidate, attempt_started)
                    info = {"composite": model["id"], "model": candidate, "trigger": attempt["trigger"],
                            "sent_chars": len(tap.text), "tool_calls_sent": len(tap.done_calls),
                            "strategy": "close_tool_calls" if closing else "continue",
                            "error": str(getattr(late.error, "message_en", "") or late.error)[:300]}
                    log.info("composite %s: stream of %s broke after %d chars (%s) — %s", model["id"], candidate,
                             len(tap.text), attempt["trigger"], info["strategy"])
                    if trace is not None:
                        try:
                            trace.add_stage("composite_recovery", info)
                        except Exception:
                            pass
                    yield stream_recovery.event("composite_recovery", recovery=info)
                    if closing:
                        for piece in closing:
                            yield dict(piece, model=model["id"])
                        decision["chosen"] = candidate
                        decision["ok"] = True
                        return
                    recoveries_left -= 1
                    current = tap.continuation(messages, facts.get("language") or "ru")
                    joiner = stream_recovery.Joiner(tap.text)
                    if candidate not in queue[index:]:
                        queue.append(candidate)   # a passing network blip: the same model may go on, last
                    last_exc, last_candidate = late.error, candidate
                    break
                except asyncio.CancelledError:
                    attempt["trigger"] = "cancelled"
                    raise
                except Exception as exc:  # noqa: BLE001 — every failure is classified, then retried or surfaced
                    kind = classify(exc)
                    attempt["trigger"] = kind
                    attempt["ms"] = int((time.time() - attempt_started) * 1000)
                    last_exc = exc
                    last_candidate = candidate
                    if "/" not in candidate or store.get(candidate) is None:
                        note_failure(candidate, attempt_started)
                    if trace is not None:
                        try:
                            trace.add_stage("composite_fallback", {"composite": model["id"], "model": candidate,
                                                                   "trigger": kind, "retry": retry,
                                                                   "error": str(getattr(exc, "message_en", "")
                                                                                or exc)[:300]})
                        except Exception:
                            pass
                    if not _should_fallback(kind, triggers):
                        decision["error"] = kind
                        raise _as_completion_error(exc, model["id"], candidate)
                    # Retry the same model only for passing troubles; a wrong request or a filter will not change.
                    if retry < retries and kind in ("429", "5xx", "timeout", "empty"):
                        await asyncio.sleep(_backoff(fb, retry))
                        continue
                    if kind == "context_length" and fb.get("context_upgrade", True):
                        tried_ctx[candidate.lower()] = model_caps(candidate).get("context")
                        queue = queue[:index] + _bigger_first(queue[index:], tried_ctx[candidate.lower()])
                    break
            if index < len(queue) and attempts_left > 0 and fb.get("backoff_ms") and last_exc is not None \
                    and classify(last_exc) == "429":
                # A rate limit across one reseller often hits its neighbours too: a short pause helps.
                await asyncio.sleep(min(2.0, fb["backoff_ms"] / 1000.0))
        decision["error"] = classify(last_exc) if last_exc else "exhausted"
        if last_exc is not None:
            raise _as_completion_error(last_exc, model["id"], last_candidate, exhausted=True,
                                       tried=[a["model"] for a in decision["attempts"]])
        raise _composite_error(f"Составная модель «{model['id']}»: исчерпан лимит попыток.",
                               f"Composite model “{model['id']}”: out of attempts.")
    finally:
        decision["ms"] = int((time.time() - started) * 1000)
        decision["fallbacks"] = sum(1 for a in decision["attempts"] if not a["ok"])
        if isinstance(route_sink, dict):
            route_sink.update({k: decision.get(k) for k in ("ts", "rule", "rule_title", "chosen", "ok", "ms",
                                                           "fallbacks", "attempts", "endpoint", "error")})
        try:
            stats.record(model["id"], decision)
        except Exception:
            log.debug("composite stats failed", exc_info=True)
        if trace is not None:
            try:
                trace.add_stage("composite_result", {"composite": model["id"], "chosen": decision["chosen"],
                                                     "ok": decision["ok"], "attempts": [
                                                         {"model": a["model"], "ok": a["ok"], "trigger": a["trigger"],
                                                          "ms": a["ms"]} for a in decision["attempts"]]})
            except Exception:
                pass


def _bigger_first(rest: List[str], current: Optional[int]) -> List[str]:
    """After «context too long»: models with a larger known window first (smallest sufficient first)."""
    rows = [(m, model_caps(m).get("context")) for m in rest]
    bigger = sorted([r for r in rows if r[1] and (current is None or r[1] > current)], key=lambda r: r[1])
    unknown = [r for r in rows if not r[1]]
    smaller = [r for r in rows if r[1] and current is not None and r[1] <= current]
    return [m for m, _ in bigger + unknown + smaller]


def _backoff(fb: Dict[str, Any], retry: int) -> float:
    base = max(0, int(fb.get("backoff_ms") or 0)) / 1000.0
    if fb.get("backoff") == "exponential":
        base *= 2 ** retry
    return min(30.0, base)


def _as_completion_error(exc: BaseException, composite: str, candidate: str, *, exhausted: bool = False,
                         tried: Optional[List[str]] = None):
    from .chat import CompletionError
    if isinstance(exc, _Timeout):
        stage_ru = {"connect": "не начала отвечать", "first_token": "не дала первый токен",
                    "total": "не уложилась в общий лимит"}.get(exc.stage, "не ответила")
        stage_en = {"connect": "did not start answering", "first_token": "gave no first token",
                    "total": "did not finish in the total limit"}.get(exc.stage, "did not answer")
        base = CompletionError(f"Модель «{candidate}» {stage_ru} за {exc.seconds} с",
                               status=504, err_type="timeout",
                               message_en=f"Model “{candidate}” {stage_en} within {exc.seconds} s")
    elif isinstance(exc, CompletionError):
        base = exc
    else:
        base = CompletionError(f"Внутренняя ошибка составной модели ({type(exc).__name__})", status=500,
                               err_type="internal_error",
                               message_en=f"Internal composite model error ({type(exc).__name__})")
    if not exhausted:
        return base
    names = ", ".join(dict.fromkeys(tried or []))[:400]
    err = CompletionError(f"Составная модель «{composite}»: все модели не ответили ({names}). Последняя ошибка: "
                          f"{base.message}", status=base.status if base.status >= 400 else 502,
                          err_type=base.err_type, message_en=f"Composite model “{composite}”: every model failed "
                                                             f"({names}). Last error: {base.message_en}",
                          provider="composite", retry_after=getattr(base, "retry_after", None))
    return err


_COMMITTED = object()


class _AfterCommit(Exception):
    def __init__(self, error: BaseException) -> None:
        super().__init__("after commit")
        self.error = error


async def _attempt(service: Any, candidate: str, messages: List[Dict[str, Any]], params: Dict[str, Any], trace: Any,
                   fb: Dict[str, Any], stream_mode: bool, attempt: Dict[str, Any],
                   allow_empty: bool = False) -> AsyncIterator[Any]:
    """One candidate. Yields ``_COMMITTED`` once the answer is promised to the client, then its chunks.

    Before the commit everything is held back, so a failure can still move to the next candidate. A stream commits at
    its first visible piece (with a first-token timeout, reasoning is held back until the first word); a non-stream
    request commits only when the whole answer is in."""
    connect_t = int(fb.get("connect_timeout") or 0)
    first_t = int(fb.get("first_token_timeout") or 0)
    total_t = int(fb.get("total_timeout") or 0)
    started = time.time()
    held: List[Dict[str, Any]] = []
    committed = False
    got_any = False
    got_text = False
    text_chars = 0
    tool_call = False
    ttft = 0
    source = service.stream_completion(candidate, messages, dict(params), trace=trace)
    iterator = source.__aiter__()
    try:
        while True:
            timeout: Optional[float] = None
            stage = ""
            elapsed = time.time() - started
            if not committed:
                if connect_t and not got_any:
                    timeout, stage = connect_t - elapsed, "connect"
                if first_t and not got_text and (timeout is None or first_t - elapsed < timeout):
                    timeout, stage = first_t - elapsed, "first_token"
            if total_t and (timeout is None or total_t - elapsed < timeout):
                timeout, stage = total_t - elapsed, "total"
            try:
                if timeout is not None:
                    if timeout <= 0:
                        raise asyncio.TimeoutError
                    item = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
                else:
                    item = await iterator.__anext__()
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                seconds = {"connect": connect_t, "first_token": first_t, "total": total_t}.get(stage, 0)
                error = _Timeout(stage, seconds)
                if committed:
                    raise _AfterCommit(_as_completion_error(error, "", candidate))
                raise error
            except Exception as exc:
                if committed:
                    raise _AfterCommit(exc)
                raise
            if not isinstance(item, dict):
                continue
            if item.get("object") == "memuai.event":
                if committed:
                    yield item
                else:
                    held.append(item)
                continue
            choices = item.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices and isinstance(choices[0], dict) else {}
            if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                if not got_any:
                    got_any = True
                    ttft = int((time.time() - started) * 1000)
                if (isinstance(delta.get("content"), str) and delta["content"].strip()) or delta.get("tool_calls"):
                    got_text = True
                    tool_call = tool_call or bool(delta.get("tool_calls"))
                if isinstance(delta.get("content"), str):
                    text_chars += len(delta["content"].strip())
            if committed:
                yield item
                continue
            held.append(item)
            visible = got_text or (got_any and not first_t)
            if stream_mode and visible:
                committed = True
                yield _COMMITTED
                for chunk in held:
                    yield chunk
                held = []
    finally:
        from .providers.http import close_quietly
        await close_quietly(source.aclose)
    total_ms = int((time.time() - started) * 1000)
    if not committed:
        # A continuation (Smart Stream Recovery) may legitimately be empty: the answer was nearly finished.
        if not got_text and not tool_call and text_chars == 0 and not allow_empty:
            observations.add(candidate, False, ttft, total_ms)
            raise _composite_error(f"Модель «{candidate}» вернула пустой ответ.",
                                   f"Model “{candidate}” returned an empty answer.", 502, "upstream_error")
        yield _COMMITTED
        for chunk in held:
            yield chunk
    observations.add(candidate, True, ttft or total_ms, total_ms)


def note_failure(candidate: str, started: float) -> None:
    observations.add(candidate, False, 0, int((time.time() - started) * 1000))


# ---------------------------------------------------------------------------------------------------------------------
# Listing helpers (/v1/models, MemuAI pickers, tools support)
# ---------------------------------------------------------------------------------------------------------------------

def listed() -> List[Dict[str, Any]]:
    """Rows for ``/v1/models``."""
    out = []
    for model in store.all():
        if not model.get("enabled", True):
            continue
        row = {"id": model["id"], "object": "model", "created": int(model.get("created") or 1735689600),
               "owned_by": "memuai-composite", "prefix": model["prefix"], "composite": True}
        if model.get("title"):
            row["name"] = model["title"]
        if model.get("description"):
            row["description"] = model["description"]
        out.append(row)
    return out


def _all_candidate_ids(model: Dict[str, Any], seen: Optional[set] = None) -> List[str]:
    seen = seen if seen is not None else set()
    if model["id"] in seen:
        return []
    seen.add(model["id"])
    out = [c["model"] for c in model["default"]["candidates"]]
    for rule in model["rules"]:
        if rule["enabled"]:
            out += [c["model"] for c in rule.get("candidates") or []]
            if rule["kind"] == "prefer":
                out += rule.get("models") or []
    flat: List[str] = []
    for mid in dict.fromkeys(out):
        nested = store.get(mid) if "/" in mid else None
        if nested is not None:
            flat += _all_candidate_ids(nested, seen)
        else:
            flat.append(mid)
    return list(dict.fromkeys(flat))


def _all_candidate_ids_shallow(model: Dict[str, Any]) -> List[str]:
    """Every model id the composite names directly (nested composites are not expanded)."""
    out = [c["model"] for c in model["default"]["candidates"]]
    for rule in model["rules"]:
        out += [c["model"] for c in rule.get("candidates") or []] + list(rule.get("models") or [])
    return list(dict.fromkeys(out))


def summary_caps(model: Dict[str, Any]) -> Dict[str, Any]:
    """What a picker should say about the composite: it «sees» when some reachable candidate sees, etc."""
    concrete = _concrete_ids()
    caps = [model_caps(mid, concrete) for mid in _all_candidate_ids(model)]
    caps = [c for c in caps if c.get("available")]
    vision = "yes" if any(c.get("vision") == "yes" for c in caps) else \
        "unknown" if any(c.get("vision") == "unknown" for c in caps) else "no"
    contexts = [c["context"] for c in caps if c.get("context")]
    return {"vision": vision, "tools": any(c.get("tools") for c in caps), "context": max(contexts) if contexts else None,
            "free": bool(caps) and all(c.get("free") for c in caps), "count": len(caps)}


def tools_supported(model_id: str) -> bool:
    model = store.get(model_id)
    if model is None:
        return False
    return summary_caps(model)["tools"]


def pg_rows() -> List[Dict[str, Any]]:
    """Rows for ``/api/admin/pg/state`` — the MemuAI model pickers (enabled composites only)."""
    rows = []
    for model in store.all():
        if not model.get("enabled", True):
            continue
        try:
            caps = summary_caps(model)
        except Exception:
            caps = {"vision": "unknown", "tools": False, "context": None, "free": False}
        row: Dict[str, Any] = {
            "id": model["id"], "provider": "composite", "provider_label": "MemuAI Proxy · " + model["prefix"],
            "composite": True, "description": model.get("description") or "",
            "caps": {"search": False, "thinking": False, "image": False, "vision": caps["vision"] != "no",
                     "vision_state": caps["vision"], "vision_src": "composite",
                     **({"tools": True} if caps.get("tools") else {}), **({"free": True} if caps.get("free") else {})},
        }
        if model.get("title"):
            row["display_name"] = model["title"]
        if caps.get("context"):
            row["context"] = {"context": caps["context"], "source": "composite"}
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------------------------------------------------

def available_models() -> List[Dict[str, Any]]:
    """Real models a candidate picker can offer, with what the pipeline knows about them."""
    concrete = _concrete_ids()
    seen = set()
    out = []
    try:
        from .state import state
        from .routing import public_id
        entries = state.enabled_models()
    except Exception:
        entries = []
    for entry in entries:
        pid = public_id(entry.prefix, entry.model)
        if pid.lower() in seen:
            continue
        seen.add(pid.lower())
        cap = model_caps(pid, concrete)
        label = entry.prefix
        try:
            for account in state.accounts_of("custom"):
                if str(account.fields.get("name", "")).strip().lower().replace(" ", "-") == entry.prefix.lower():
                    label = account.label or label
                    break
        except Exception:
            pass
        out.append({"id": pid, "name": cap.get("name") or pid, "account": label, "provider": entry.provider,
                    "vision": cap.get("vision"), "tools": cap.get("tools"), "context": cap.get("context"),
                    "price": list(cap["price"]) if cap.get("price") else None, "free": cap.get("free")})
    for model in store.all():
        out.append({"id": model["id"], "name": model.get("title") or model["id"], "account": "composite",
                    "provider": "composite", "composite": True, "enabled": model.get("enabled", True)})
    return out


def _pick(models: List[Dict[str, Any]], *, need: str = "", limit: int = 3, sort: str = "") -> List[str]:
    rows = [m for m in models if not m.get("composite")]
    rows = [m for m in rows if not re.search(r"image|imagine|t2i|wanx|video|tts|embed|whisper|music", m["id"], re.I)]
    if need == "vision":
        rows = [m for m in rows if m.get("vision") == "yes"] or [m for m in rows if m.get("vision") == "unknown"]
        # Models made for pictures (Qwen-VL, «vision», omni) first: a family «yes» of a text model is weaker.
        rows.sort(key=lambda m: 0 if re.search(r"(?:^|[-_/.])vl(?:[-_.]|$)|vision|omni|-v(?:[-_.]|$)", m["id"], re.I) else 1)
    if need == "tools":
        rows = [m for m in rows if m.get("tools")]
    if sort == "context":
        rows.sort(key=lambda m: -(m.get("context") or 0))
    elif sort == "cheap":
        rows.sort(key=lambda m: (m.get("price") is None, (sum(m["price"]) if m.get("price") else 0)))
    elif sort == "strong" and need != "vision":
        def strength(m: Dict[str, Any]) -> Tuple[int, float]:
            low = m["id"].lower()
            tier = 0 if re.search(r"opus|gpt-6|gpt-5|max|pro|sonnet|ultra", low) else \
                2 if re.search(r"flash|mini|lite|nano|small|haiku|turbo", low) else 1
            return tier, -(sum(m["price"]) if m.get("price") else 0)
        rows.sort(key=strength)
    return [m["id"] for m in rows[:limit]]


def presets(models: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Ready templates; their candidates are picked from the user's models (empty when nothing fits)."""
    models = models if models is not None else available_models()
    strong = _pick(models, sort="strong", limit=3)
    cheap = _pick(models, sort="cheap", limit=3)
    vision = _pick(models, need="vision", sort="strong", limit=2)
    tools = _pick(models, need="tools", sort="strong", limit=2)
    wide = _pick(models, sort="context", limit=3)

    def cands(ids_: List[str]) -> List[Dict[str, Any]]:
        return [{"model": m, "weight": 1} for m in ids_]

    out = [
        {"key": "reliable", "title": "Надёжная", "title_en": "Reliable",
         "description": "A → B → C по очереди: при ошибке, лимите 429 или таймауте — следующая модель.",
         "description_en": "A → B → C in turn: on an error, a 429 limit or a timeout — the next model.",
         "model": {"prefix": "memu", "name": "reliable", "title": "Надёжная",
                   "default": {"candidates": cands(strong), "strategy": {"type": "fixed"}},
                   "fallback": {**DEFAULT_FALLBACK, "first_token_timeout": 60, "retries": 1}}},
        {"key": "vision", "title": "Зрение + текст", "title_en": "Vision + text",
         "description": "Есть картинка — модель со зрением, иначе — обычная текстовая.",
         "description_en": "A picture goes to a vision model, everything else to a text model.",
         "model": {"prefix": "memu", "name": "vision", "title": "Зрение + текст",
                   "rules": [{"id": "r1", "kind": "route", "title": "Картинка → зрение",
                              "conditions": [{"type": "has_image", "op": "is", "value": True}],
                              "candidates": cands(vision), "filters": {"vision": True}, "fallthrough": False}],
                   "default": {"candidates": cands(strong[:2] or cheap[:2]), "strategy": {"type": "fixed"}}}},
        {"key": "long", "title": "Длинный контекст", "title_en": "Long context",
         "description": "Больше 100 тыс. токенов — модели с самым большим окном; при «не влезло» — следующая побольше.",
         "description_en": "Over 100k tokens — the models with the largest window; on “too long” — the next bigger one.",
         "model": {"prefix": "memu", "name": "long", "title": "Длинный контекст",
                   "rules": [{"id": "r1", "kind": "route", "title": "Больше 100 тыс. токенов",
                              "conditions": [{"type": "tokens", "op": "gt", "value": 100000}],
                              "candidates": cands(wide), "filters": {"min_context": 120000}}],
                   "default": {"candidates": cands(strong[:2]), "strategy": {"type": "fixed"}},
                   "fallback": {**DEFAULT_FALLBACK, "context_upgrade": True}}},
        {"key": "day_night", "title": "Дешёвая днём / лучшая ночью", "title_en": "Cheap by day / best at night",
         "description": "С 9:00 до 21:00 — самая дешёвая из списка, ночью — самая сильная.",
         "description_en": "From 9:00 to 21:00 — the cheapest of the list, at night — the strongest.",
         "model": {"prefix": "memu", "name": "daynight", "title": "Дешёвая днём / лучшая ночью",
                   "rules": [{"id": "r1", "kind": "route", "title": "День: дешёвая",
                              "conditions": [{"type": "time", "op": "between", "value": "09:00-21:00"}],
                              "candidates": cands(list(dict.fromkeys(cheap + strong[:1]))),
                              "strategy": {"type": "cheapest"}}],
                   "default": {"candidates": cands(strong), "strategy": {"type": "fixed"}}}},
        {"key": "fastest", "title": "Самая быстрая за час", "title_en": "Fastest in the last hour",
         "description": "Первой идёт модель с самым быстрым первым токеном за последний час.",
         "description_en": "The model with the fastest first token over the last hour goes first.",
         "model": {"prefix": "memu", "name": "fast", "title": "Самая быстрая",
                   "default": {"candidates": cands(list(dict.fromkeys(cheap + strong))[:4]),
                               "strategy": {"type": "fastest", "metric": "ttft", "window_min": 60}}}},
        {"key": "smart", "title": "Умная (всё сразу)", "title_en": "Smart (all at once)",
         "description": "Картинка → зрение, больше 200 тыс. токенов → большое окно, инструменты → нативные вызовы, "
                        "остальное — надёжная цепочка.",
         "description_en": "Picture → vision, over 200k tokens → large window, tools → native calls, "
                           "the rest — a reliable chain.",
         "model": {"prefix": "memu", "name": "smart", "title": "Умная",
                   "rules": [
                       {"id": "r1", "kind": "route", "title": "Картинка → зрение",
                        "conditions": [{"type": "has_image", "op": "is", "value": True}],
                        "candidates": cands(vision), "filters": {"vision": True}, "fallthrough": False},
                       {"id": "r2", "kind": "route", "title": "Больше 200 тыс. токенов",
                        "conditions": [{"type": "tokens", "op": "gt", "value": 200000}],
                        "candidates": cands(wide), "filters": {"min_context": 200000}},
                       {"id": "r3", "kind": "route", "title": "Нужны инструменты",
                        "conditions": [{"type": "needs_tools", "op": "is", "value": True}],
                        "candidates": cands(tools), "filters": {"tools": True}}],
                   "default": {"candidates": cands(strong), "strategy": {"type": "fixed"}},
                   "fallback": {**DEFAULT_FALLBACK, "first_token_timeout": 90}}},
        {"key": "difficulty", "title": "По сложности задачи", "title_en": "By task difficulty",
         "description": "Простые вопросы, перевод и пересказ — быстрые дешёвые модели; сложный код, математика и "
                        "архитектура — сильные, с бережным расходом квот.",
         "description_en": "Simple questions, translation and summaries — fast cheap models; complex code, maths and "
                           "architecture — strong ones, spending their quotas carefully.",
         "model": {"prefix": "memu", "name": "difficulty", "title": "По сложности",
                   "rules": [
                       {"id": "r1", "kind": "route", "title": "Сложная задача → флагманы",
                        "conditions": [{"type": "difficulty", "op": "in",
                                        "value": ["complex_code", "deep_math", "architecture"]}],
                        "candidates": cands(strong), "strategy": {"type": "least_used"}}],
                   "default": {"candidates": cands(list(dict.fromkeys(cheap + strong[-1:]))),
                               "strategy": {"type": "fastest", "metric": "ttft", "window_min": 60}}}},
        {"key": "ab", "title": "A/B-тест", "title_en": "A/B test",
         "description": "Половина запросов — в одну модель, половина — в другую (статистика покажет разницу).",
         "description_en": "Half of the requests go to one model, half to another (the statistics show the difference).",
         "model": {"prefix": "memu", "name": "ab", "title": "A/B-тест",
                   "default": {"candidates": cands(strong[:2] or cheap[:2]), "strategy": {"type": "weighted"}}}},
    ]
    for preset in out:
        preset["model"] = normalize(preset["model"])
    return out


SCHEMA = {
    "conditions": {k: {"kind": v["kind"], "ops": list(OPS_BY_KIND[v["kind"]]), "options": v.get("options", [])}
                   for k, v in CONDITIONS.items()},
    "kinds": list(RULE_KINDS), "strategies": list(STRATEGIES), "triggers": list(TRIGGERS),
    "fallback_default": DEFAULT_FALLBACK,
}


def export_json(model_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    models = store.all()
    if model_ids:
        wanted = {m.lower() for m in model_ids}
        models = [m for m in models if m["id"] in wanted]
    return {"memuai": "composite-models", "version": 1, "exported": int(time.time()), "models": models}


def import_json(data: Any, mode: str = "merge") -> Dict[str, Any]:
    """Import composites (a whole export, a list or one model). Returns ``{saved, errors}``; nothing is saved when a
    model has errors — the user sees all of them at once."""
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        rows = data["models"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = []
    models = [normalize(r) for r in rows if isinstance(r, dict)]
    if not models:
        return {"saved": [], "errors": [_issue("", "В файле нет составных моделей.", "The file has no composite models.")]}
    existing = [] if mode == "replace" else [m for m in store.all() if m["id"] not in {x["id"] for x in models}]
    errors: List[Dict[str, str]] = []
    pool = existing + models
    for model in models:
        for issue in validate(model, others=[m for m in pool if m["id"] != model["id"]]):
            if issue["level"] == "error":
                errors.append({**issue, "model": model["id"]})
    if errors:
        return {"saved": [], "errors": errors}
    now = int(time.time())
    for model in models:
        model.setdefault("created", now)
        model["updated"] = now
    store.save_all(existing + models)
    return {"saved": [m["id"] for m in models], "errors": []}
