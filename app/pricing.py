"""What a model request would cost at public API prices.

MemuAI mostly runs on the user's own web accounts, where a request costs no
money directly — but knowing what the same work would cost through the
official API is still the best measure of how heavy a session was, and for
custom API connections (OpenRouter and similar) it *is* the bill.

Prices are resolved in this order, first match wins:

1. **account tariffs** — the quota planner's ``memuai/api-budgets.json``:
   the account selected for this request, or an unambiguous public connection;
2. **overrides** — ``memuai/pricing-overrides.json`` and the caller's own
   table (MemuCode ``pricing.overrides``): ``{"pattern": {"input": 3,
   "output": 15}}``, the pattern is a glob over the model id;
3. **local models** — ``local-…`` ids run on this computer and cost nothing;
4. **the model catalogue** — the "Model catalogue" module keeps a cached slim
   copy of ``openrouter.ai/api/v1/models``; MemuCode refreshes its own copy
   (``memuai/pricing-cache.json``, at most once a day);
5. **the built-in table** below — official Anthropic prices and typical
   prices of other popular families, used when nothing else is available.

All prices are US dollars per one million tokens. Names are compared in the
canonical form of :func:`app.model_info.canonical`, after dropping the
MemuAI provider prefix (``genspark-claude-opus-5`` → ``claude-opus-5``).
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import DATA_DIR
from .model_info import canonical
from .version import APP_VERSION

MEMU_DIR = Path(DATA_DIR) / "memuai"
CATALOG_MODULE_CACHE = MEMU_DIR / "modules" / "persist" / "catalog.json"
OWN_CACHE = MEMU_DIR / "pricing-cache.json"
OVERRIDES_FILE = MEMU_DIR / "pricing-overrides.json"
SOURCE_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL = 24 * 3600

#: Providers that run on the user's web accounts or subscriptions: the estimate
#: is the API-equivalent value, not money actually charged per request.
SUBSCRIPTION_PROVIDERS = {"genspark", "kiro", "antigravity", "geminiweb", "grok"}
PROVIDER_PREFIXES = ("genspark", "kiro", "antigravity", "geminiweb", "grok", "local", "custom")


@dataclass
class Price:
    input: float
    output: float
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None
    web: Optional[float] = None
    source: str = ""
    matched: str = ""
    approximate: bool = False
    #: $/1M for hidden reasoning when the catalogue prices it apart from output (rare).
    reasoning: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"input": self.input, "output": self.output, "cache_read": self.cache_read,
                "cache_write": self.cache_write, "web": self.web, "source": self.source,
                "matched": self.matched, "approximate": self.approximate, "reasoning": self.reasoning}


@dataclass
class Estimate:
    cost: float
    price: Optional[Price]
    billing: str                      # api | subscription | local | unknown
    breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"cost": round(self.cost, 6), "billing": self.billing,
                "price": self.price.to_dict() if self.price else None,
                "breakdown": {key: round(value, 6) for key, value in self.breakdown.items()}}


# (regex over the canonical name, input, output, cache read, cache write, approximate)
BUILTIN: List[Tuple[str, float, float, Optional[float], Optional[float], bool]] = [
    # Anthropic — official first-party prices.
    (r"(?:fable|mythos)-?5\.1", 10.0, 50.0, 0.25, 12.5, False),
    (r"(?:fable|mythos)-?5(?![.\d])", 10.0, 50.0, 1.0, 12.5, False),
    (r"opus-?5\.5", 4.0, 20.0, 0.20, 5.0, False),
    (r"opus-?5(?![.\d])", 5.0, 25.0, 0.50, 6.25, False),
    # Some catalogues (Genspark: ``claude-4-5-haiku``) put the version before the
    # family name; without the mirrored forms Haiku 4.5 was priced as Haiku 3
    # and ``claude-4-opus`` / ``claude-4-sonnet`` had no price at all.
    (r"opus-?4\.[5-9]|4\.[5-9]-?opus", 5.0, 25.0, 0.50, 6.25, False),
    (r"opus-?4(?:\.1)?(?![.\d])|claude-?4(?:\.1)?-?opus|claude-?3-?opus", 15.0, 75.0, 1.50, 18.75, False),
    (r"sonnet-?5(?![.\d])", 2.0, 10.0, 0.20, 2.5, False),
    (r"sonnet-?4(?:\.\d)?|claude-?4(?:\.\d)?-?sonnet|sonnet-?3\.7|3\.7-?sonnet|claude-?3\.5-?sonnet",
     3.0, 15.0, 0.30, 3.75, False),
    (r"haiku-?4\.5|4\.5-?haiku", 1.0, 5.0, 0.10, 1.25, False),
    (r"haiku-?3\.5|3\.5-?haiku", 0.8, 4.0, 0.08, 1.0, False),
    (r"haiku", 0.25, 1.25, 0.03, 0.3, True),
    # OpenAI.
    (r"gpt-?5\.6-?sol", 2.0, 10.0, 0.20, None, True),
    (r"gpt-?5\.6-?terra", 2.0, 12.0, 0.20, None, True),
    (r"gpt-?5\.6-?luna", 0.2, 1.2, 0.02, None, True),
    (r"gpt-?5(?:\.\d)?-?nano", 0.05, 0.4, 0.005, None, True),
    (r"gpt-?5(?:\.\d)?-?mini", 0.25, 2.0, 0.025, None, True),
    (r"gpt-?5(?:\.\d)?-?(?:pro)", 15.0, 120.0, None, None, True),
    (r"gpt-?(?:6|5)", 1.25, 10.0, 0.125, None, True),
    (r"gpt-?4\.1-?nano", 0.1, 0.4, 0.025, None, True),
    (r"gpt-?4\.1-?mini", 0.4, 1.6, 0.1, None, True),
    (r"gpt-?4\.1", 2.0, 8.0, 0.5, None, True),
    (r"gpt-?4o-?mini", 0.15, 0.6, 0.075, None, True),
    (r"gpt-?4o|chatgpt", 2.5, 10.0, 1.25, None, True),
    (r"gpt-?4-?turbo", 10.0, 30.0, None, None, True),
    (r"gpt-?4(?![.\do])", 30.0, 60.0, None, None, True),
    (r"gpt-?3\.5", 0.5, 1.5, None, None, True),
    (r"gpt-?oss", 0.05, 0.25, None, None, True),
    (r"(?<![a-z0-9])o[134]-?mini", 1.1, 4.4, 0.275, None, True),
    (r"(?<![a-z0-9])o3-?pro", 20.0, 80.0, None, None, True),
    (r"(?<![a-z0-9])o1-?pro", 150.0, 600.0, None, None, True),
    (r"(?<![a-z0-9])o3(?![a-z0-9])", 2.0, 8.0, 0.5, None, True),
    (r"(?<![a-z0-9])o1(?![a-z0-9])", 15.0, 60.0, 7.5, None, True),
    # Google.
    (r"gemini-?3\.[5-9]-?flash-?lite|gemini.*flash-?lite", 0.1, 0.4, 0.01, None, True),
    (r"gemini-?3\.[5-9]-?flash", 0.75, 3.75, 0.075, None, True),
    (r"gemini-?3(?:\.\d)?-?flash", 0.5, 3.0, 0.05, None, True),
    (r"gemini-?3(?:\.\d)?-?pro|gemini-?pro-?agent", 2.0, 12.0, 0.2, None, True),
    (r"gemini-?2\.5-?pro", 1.25, 10.0, 0.31, None, True),
    (r"gemini-?2\.5-?flash", 0.3, 2.5, 0.075, None, True),
    (r"gemini", 0.5, 3.0, 0.05, None, True),
    (r"gemma", 0.05, 0.1, None, None, True),
    # xAI.
    (r"grok-?code", 0.2, 1.5, 0.02, None, True),
    (r"grok-?4(?:\.\d)?-?fast|grok-?4\.1", 0.2, 0.5, 0.05, None, True),
    (r"grok", 3.0, 15.0, 0.75, None, True),
    # Amazon (Kiro can expose Nova).
    (r"nova-?premier", 2.5, 12.5, None, None, True),
    (r"nova-?pro", 0.8, 3.2, 0.2, None, True),
    (r"nova-?lite", 0.06, 0.24, 0.015, None, True),
    (r"nova-?micro", 0.035, 0.14, 0.00875, None, True),
    # Others.
    (r"deepseek-?v4.*pro", 0.35, 0.7, 0.03, None, True),
    (r"deepseek-?v4", 0.05, 0.1, 0.01, None, True),
    (r"deepseek", 0.28, 0.42, 0.028, None, True),
    (r"glm-?5\.\d-?flashx", 0.37, 1.25, 0.075, None, True),
    (r"glm-?5\.\d-?flash", 0.15, 0.5, 0.03, None, True),
    (r"glm-?5\.\d", 1.4, 4.4, 0.26, None, True),
    (r"glm-?5", 1.0, 3.2, 0.2, None, True),
    (r"glm", 0.6, 2.2, 0.11, None, True),
    (r"kimi", 0.6, 2.5, 0.15, None, True),
    (r"qwen.*coder", 0.4, 1.6, None, None, True),
    (r"qwen.*(?:max|plus)", 1.2, 6.0, None, None, True),
    (r"qwen|qwq", 0.2, 0.8, None, None, True),
    (r"minimax", 0.3, 1.2, 0.03, None, True),
    (r"devstral|codestral|mistral-?(?:small|medium)|ministral", 0.4, 2.0, None, None, True),
    (r"mistral|magistral|pixtral", 2.0, 6.0, None, None, True),
    (r"llama", 0.2, 0.6, None, None, True),
]
_BUILTIN = [(re.compile(pattern), *rest) for pattern, *rest in BUILTIN]
#: Round 34: vendor rates checked against the vendor's own price page. They win over the public catalogue, whose
#: listing for these ids is a reseller's odd rate (OpenRouter «z-ai/glm-5.3»: $0.07 in / $4.80 out, while Z.ai sells
#: it at $1.40 / $4.40 — https://docs.z.ai/guides/overview/pricing, checked 2026-10-11). ``memucode models`` showed
#: one price and /model the other. Same columns as BUILTIN; user overrides still come first.
PINNED = [
    (r"glm-?5\.3-?flashx(?![a-z0-9])", 0.37, 1.25, 0.075, None),
    (r"glm-?5\.3-?flash(?![a-z0-9])", 0.15, 0.5, 0.03, None),
    (r"glm-?5\.[123](?![0-9a-z.]|-?(?:flash|air|turbo|v))", 1.4, 4.4, 0.26, None),
    (r"glm-?5(?![0-9a-z.]|-?(?:flash|air|turbo|v))", 1.0, 3.2, 0.2, None),
]
_PINNED = [(re.compile(pattern), *rest) for pattern, *rest in PINNED]

_lock = threading.Lock()
_index: Dict[str, Dict[str, Any]] = {}
_index_stamp: Tuple[float, float] = (-1.0, -1.0)
_refresh_running = False
#: lookup() results for the current catalogue and overrides: the statistics card prices every
#: model of every hourly bucket, and each lookup used to re-read the overrides file from disk.
_memo: Dict[Tuple[str, str], Optional["Price"]] = {}
_MEMO_LIMIT = 4096
_overrides_cache: Tuple[float, Dict[str, Any]] = (-1.0, {})


# ---------------------------------------------------------------- catalogue

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _slim_openrouter(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One OpenRouter entry → the fields the estimator needs ($/1M)."""
    model_id = str(row.get("id") or "")
    pricing = row.get("pricing") or {}
    if not model_id or not isinstance(pricing, dict):
        return None

    def per_million(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if not math.isfinite(number) or number < 0 else number * 1_000_000

    price = {"input": per_million(pricing.get("prompt")), "output": per_million(pricing.get("completion")),
             "cacheRead": per_million(pricing.get("input_cache_read")),
             "cacheWrite": per_million(pricing.get("input_cache_write"))}
    web = pricing.get("web_search")
    try:
        if web is not None and float(web) >= 0:
            price["web"] = float(web)
    except (TypeError, ValueError):
        pass
    return {"id": model_id, "price": price,
            "context": int(row.get("context_length") or 0)}


def _catalog_rows() -> List[Dict[str, Any]]:
    """Rows from the module cache and our own cache; the fresher wins per id."""
    rows: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for path, key in ((CATALOG_MODULE_CACHE, "cache"), (OWN_CACHE, None)):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        block = data.get(key) if key else data
        if not isinstance(block, dict) or not isinstance(block.get("rows"), list):
            continue
        at = float(block.get("at") or 0)
        at = at / 1000 if at > 1e11 else at
        for row in block["rows"]:
            if isinstance(row, dict) and row.get("id"):
                current = rows.get(row["id"])
                if current is None or current[0] < at:
                    rows[row["id"]] = (at, row)
    return [row for _at, row in rows.values()]


def _is_free(price: Dict[str, Any]) -> bool:
    try:
        return float(price.get("input")) == 0 and float(price.get("output")) == 0
    except (TypeError, ValueError):
        return False


def _build_index() -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for row in _catalog_rows():
        model_id = str(row.get("id") or "")
        if not model_id or ":" in model_id.split("/")[-1]:
            continue  # ":batch", ":free", ":thinking" variants price differently
        price = row.get("price") or {}
        if price.get("input") is None or price.get("output") is None:
            continue  # routers: "depends on the model"
        key = canonical(model_id.lstrip("~"))
        if not key:
            continue
        current = index.get(key)
        # Prefer a paid listing over a promotional free one (an API-equivalent estimate of
        # "$0" would be misleading), then the vendor's own listing over resellers/aliases.
        rank = (1 if _is_free(price) else 0, 0 if not model_id.startswith("~") else 1, len(model_id))
        if current is None or rank < current["_rank"]:
            index[key] = {**row, "_rank": rank}
    return index


def _index_now() -> Dict[str, Dict[str, Any]]:
    global _index, _index_stamp
    stamp = (_mtime(CATALOG_MODULE_CACHE), _mtime(OWN_CACHE))
    with _lock:
        if stamp != _index_stamp:
            _index = _build_index()
            _index_stamp = stamp
            _memo.clear()
        return _index


def cache_age() -> Optional[float]:
    """Seconds since the freshest catalogue copy, or None if there is none."""
    newest = 0.0
    for path, key in ((CATALOG_MODULE_CACHE, "cache"), (OWN_CACHE, None)):
        data = _read_json(path)
        block = (data or {}).get(key) if key and isinstance(data, dict) else data
        if isinstance(block, dict):
            at = float(block.get("at") or 0)
            newest = max(newest, at / 1000 if at > 1e11 else at)
    return time.time() - newest if newest else None


def refresh_online(*, force: bool = False, timeout: float = 20.0) -> Dict[str, Any]:
    """Download the public model list once a day. Safe to call from a thread."""
    global _refresh_running
    age = cache_age()
    if not force and age is not None and age < CACHE_TTL:
        return {"ok": True, "skipped": True, "age": age}
    with _lock:
        if _refresh_running:
            return {"ok": False, "skipped": True, "reason": "already running"}
        _refresh_running = True
    try:
        import httpx
        response = httpx.get(SOURCE_URL, timeout=timeout, follow_redirects=True,
                             headers={"Accept": "application/json", "User-Agent": f"MemuAI-pricing/{APP_VERSION}"})
        response.raise_for_status()
        data = response.json()
        rows = [slim for slim in (_slim_openrouter(row) for row in data.get("data") or [])
                if slim is not None]
        if not rows:
            return {"ok": False, "error": "empty model list"}
        from .storage import save_json
        save_json(OWN_CACHE, {"at": time.time(), "source": SOURCE_URL, "rows": rows}, backup=False)
        return {"ok": True, "rows": len(rows)}
    except Exception as exc:  # network is optional: the estimate falls back to the table
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        with _lock:
            _refresh_running = False


def refresh_in_background() -> None:
    threading.Thread(target=refresh_online, name="pricing-refresh", daemon=True).start()


# ---------------------------------------------------------------- lookup

#: Words that are never a model on their own: dropping one more dash-segment from
#: ``grok-grok-3`` must not leave "3", and ``gemini-3.1-pro`` must not end up as "pro".
_GENERIC_KEYS = {"pro", "max", "mini", "nano", "lite", "fast", "auto", "chat", "large", "small", "medium",
                 "latest", "preview", "turbo", "plus", "ultra", "flash", "base", "instruct", "codex",
                 "agent", "agents", "expert", "heavy", "thinking", "reasoning", "default", "model"}


def _plausible_key(key: str) -> bool:
    """A canonical name that can stand for a model, not a version or size fragment."""
    if not key or key in _GENERIC_KEYS:
        return False
    head = key.split("-", 1)[0]
    # "3-pro", "4.6", "235b-a22b", "of-agents": a fragment of a longer id.
    if not re.search(r"[a-z]{2,}", head) or head in {"of", "v1", "v2", "v3"}:
        return False
    return len(key) >= 3


def _known_prefixes() -> List[str]:
    """Provider prefixes plus the user's custom connection names (longest first)."""
    names = set(PROVIDER_PREFIXES)
    # The Proxy routing table knows custom connection names. Only consult it when the Proxy
    # already loaded it: importing it from MemuCode would pull in the whole account state.
    routing = sys.modules.get(__name__.rsplit(".", 1)[0] + ".routing")
    if routing is not None:
        try:
            names.update(str(name).lower() for name in routing.known_prefixes() if name)
        except Exception:
            pass
    return sorted(names, key=len, reverse=True)


#: Router/auto names resolved to the model the service actually routes to (an estimate).
PROVIDER_ALIASES: Dict[Tuple[str, str], str] = {
    ("kiro", "auto"): "claude-sonnet-4.5",
}


def _candidates(model: str) -> List[str]:
    """``genspark-claude-opus-5-thinking`` → canonical forms, prefix dropped step by step.

    Known provider prefixes and custom connection names are removed first; after that a
    dash-segment is dropped only while the remainder still looks like a model name.
    """
    raw = str(model or "").strip()
    if raw.startswith("@") and ":" in raw:           # MemuCode direct connection "@name:model"
        raw = raw.split(":", 1)[1]
    if raw.startswith("genspark-image/"):
        raw = raw.split("/", 1)[1]
    out: List[str] = []

    def push(text: str, strict: bool) -> None:
        key = canonical(text)
        if key and key not in out and (not strict or _plausible_key(key)):
            out.append(key)

    push(raw, False)
    lower = raw.lower()
    for prefix in _known_prefixes():
        if lower.startswith(prefix + "-") and len(raw) > len(prefix) + 1:
            push(raw[len(prefix) + 1:], False)
            break
    text = raw
    for _ in range(3):
        if "-" not in text:
            break
        text = text.split("-", 1)[1]
        push(text, True)
    return out


def _file_overrides() -> Dict[str, Any]:
    global _overrides_cache
    stamp = _mtime(OVERRIDES_FILE)
    if stamp != _overrides_cache[0]:
        data = _read_json(OVERRIDES_FILE) if stamp else None
        _overrides_cache = (stamp, data if isinstance(data, dict) else {})
        _memo.clear()
    return _overrides_cache[1]


def _load_overrides(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    table: Dict[str, Any] = dict(_file_overrides())
    if isinstance(extra, dict):
        table.update(extra)
    return table


def _price_from_row(row: Dict[str, Any], source: str, matched: str) -> Optional[Price]:
    price = row.get("price") or row
    try:
        pin, pout = _float(price.get("input")), _float(price.get("output"))
        if pin is None or pout is None:
            return None
        return Price(input=pin, output=pout,
                     cache_read=_float(price.get("cacheRead", price.get("cache_read"))),
                     cache_write=_float(price.get("cacheWrite", price.get("cache_write"))),
                     web=_float(price.get("web")), source=source, matched=matched,
                     reasoning=_float(price.get("reasoning")))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        number = None if value is None or isinstance(value, bool) else float(value)
        return number if number is not None and math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def lookup(model: str, provider: str = "", *, overrides: Optional[Dict[str, Any]] = None,
           account_id: str = "", account_overrides: bool = True) -> Optional[Price]:
    raw = str(model or "").strip()
    if not raw:
        return None
    # Account tariffs precede the public list, and are deliberately outside the
    # public-price memo: another process can change a tariff without restarting CLI.
    if account_overrides:
        from .api_budget import price_for
        scoped = price_for(raw, account_id=account_id)
        if scoped is not None:
            return scoped
    if overrides:
        return _lookup(raw, provider, overrides)
    _index_now()
    _file_overrides()   # both clear the memo when their files change
    key = (raw, str(provider or "").lower())
    if key in _memo:
        return _memo[key]
    price = _lookup(raw, provider, None)
    if len(_memo) >= _MEMO_LIMIT:
        _memo.clear()
    _memo[key] = price
    return price


def _builtin(candidates: List[str]) -> Optional[Price]:
    for key in candidates:
        for pattern, pin, pout, read, write, approx in _BUILTIN:
            if pattern.search(key):
                return Price(pin, pout, read, write, source="builtin", matched=pattern.pattern,
                             approximate=approx)
    return None


def _lookup(raw: str, provider: str, overrides: Optional[Dict[str, Any]]) -> Optional[Price]:
    candidates = _candidates(raw)
    for pattern, row in _load_overrides(overrides).items():
        if not isinstance(row, dict):
            continue
        low = str(pattern).lower()
        if fnmatch.fnmatch(raw.lower(), low) or any(fnmatch.fnmatch(key, low) for key in candidates):
            price = _price_from_row(row, "override", str(pattern))
            if price:
                return price
    if provider == "local" or raw.startswith("local-"):
        return Price(0.0, 0.0, 0.0, 0.0, source="local", matched=raw)
    if raw.lower().endswith(":free"):
        # An explicitly free variant ("qwen/qwen3.8-max:free") costs nothing;
        # don't let a builtin family row ("qwen|qwq") price it.
        return Price(0.0, 0.0, 0.0, 0.0, source="free", matched=raw)
    for key in candidates:
        for pattern, pin, pout, read, write in _PINNED:
            if pattern.fullmatch(key):
                return Price(pin, pout, read, write, source="vendor", matched=pattern.pattern)
    index = _index_now()
    free: Optional[Price] = None
    for key in candidates:
        row = index.get(key)
        if row:
            price = _price_from_row(row, "catalog", str(row.get("id")))
            if price and price.input == 0 and price.output == 0:
                free = free or price   # a promotional free listing: keep looking for a real price
                continue
            if price:
                return price
    builtin = _builtin(candidates)
    if builtin:
        return builtin
    if free:
        return free
    # Router names ("kiro-auto") are priced as the model the service routes to.
    head = (provider or raw.split("-", 1)[0]).lower()
    tail = canonical(raw.split("-", 1)[1]) if "-" in raw else canonical(raw)
    target = PROVIDER_ALIASES.get((head, tail))
    if target:
        price = _lookup(target, "", overrides)
        if price:
            price.approximate = True
            price.matched = f"{tail} → {target}"
            return price
    return None


def billing_kind(model: str, provider: str = "") -> str:
    raw = str(model or "")
    provider = (provider or "").lower()
    if provider == "local" or raw.startswith("local-"):
        return "local"
    if raw.startswith("@"):
        return "api"
    if not provider:
        head = raw.split("-", 1)[0].lower()
        provider = head if head in PROVIDER_PREFIXES else "custom"
    if provider in SUBSCRIPTION_PROVIDERS:
        return "subscription"
    return "api"


def estimate(model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
             cache_write: int = 0, web_searches: int = 0, provider: str = "",
             reasoning_tokens: int = 0, overrides: Optional[Dict[str, Any]] = None,
             account_id: str = "") -> Estimate:
    """Cost of one request. ``input_tokens`` includes cached tokens, ``output_tokens``
    includes hidden reasoning (OpenAI convention); ``reasoning_tokens`` only matters when
    the catalogue prices reasoning apart from output."""
    price = lookup(model, provider, overrides=overrides, account_id=account_id)
    billing = billing_kind(model, provider)
    if price is None:
        return Estimate(0.0, None, "unknown" if billing != "local" else "local")
    total_input = max(0, int(input_tokens or 0))
    cache_read = min(total_input, max(0, int(cache_read or 0)))
    cache_write = min(total_input - cache_read, max(0, int(cache_write or 0)))
    fresh = total_input - cache_read - cache_write
    read_rate = price.cache_read if price.cache_read is not None else price.input * 0.1
    write_rate = price.cache_write if price.cache_write is not None else price.input * 1.25
    output = max(0, int(output_tokens or 0))
    reasoning = min(output, max(0, int(reasoning_tokens or 0))) if price.reasoning is not None else 0
    parts = {
        "input": fresh * price.input / 1e6,
        "cache_read": cache_read * read_rate / 1e6,
        "cache_write": cache_write * write_rate / 1e6,
        "output": (output - reasoning) * price.output / 1e6,
        "web": max(0, int(web_searches or 0)) * (price.web or 0.0),
    }
    if reasoning:
        parts["reasoning"] = reasoning * float(price.reasoning or 0.0) / 1e6
    return Estimate(sum(parts.values()), price, billing, parts)


def format_usd(value: float, *, precise: bool = False) -> str:
    value = float(value or 0)
    if value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:.4f}" if precise or value >= 0.0001 else "<$0.0001"
    if value < 1:
        return f"${value:.3f}"
    if value < 100:
        return f"${value:.2f}"
    return f"${value:,.0f}"


def describe(price: Optional[Price]) -> str:
    """``$5 / $25 per 1M (catalog)`` for tooltips and tables."""
    if price is None:
        return "—"
    def num(value: float) -> str:
        if value == 0:
            return "0"
        return f"{value:g}" if value >= 0.01 else f"{value:.4f}"
    text = f"${num(price.input)} / ${num(price.output)}"
    if price.cache_read is not None:
        text += f" · cache ${num(price.cache_read)}"
    return text


def summarize(rows: Iterable[Dict[str, Any]], *, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate per-model usage rows ``{model, provider, input, output, cache_read, cache_write}``."""
    total = 0.0
    models: List[Dict[str, Any]] = []
    unknown: List[str] = []
    for row in rows:
        result = estimate(str(row.get("model") or ""), input_tokens=int(row.get("input") or 0),
                          output_tokens=int(row.get("output") or 0),
                          cache_read=int(row.get("cache_read") or 0),
                          cache_write=int(row.get("cache_write") or 0),
                          provider=str(row.get("provider") or ""), overrides=overrides)
        if result.price is None and result.billing != "local":
            unknown.append(str(row.get("model") or ""))
        total += result.cost
        models.append({"model": row.get("model"), "cost": round(result.cost, 6),
                       "billing": result.billing, "price": result.price.to_dict() if result.price else None})
    return {"total": round(total, 6), "models": models, "unknown": unknown}
