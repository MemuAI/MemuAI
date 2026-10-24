"""Everything the Proxy can learn about a model, with where each fact came from.

Round 22. Quick Ask already knew whether a model can look at a picture
(:func:`app.model_info.vision_hint` plus the provider's own rules in
``/api/admin/pg/state``). This module turns that into one shared layer that
answers *every* question about a model — what it reads and writes (text,
images, audio, video, files), whether it calls tools, returns structured JSON,
reasons, searches the web, how large its context and answer are, what it
would cost at public API prices, when it was released and what it knows up
to, whether its weights are open, how a local file is quantised, whether it
is deprecated — and says for every answer **which source** gave it and **how
sure** we are.

Sources, strongest first (one field can have several; the strongest wins and
the others stay visible as "alternatives"):

``probe``       a real test request the user started (a tiny picture, "what colour is it?");
``local``       the GGUF file and launch settings of a llama.cpp model on this computer;
``provider``    the row the provider itself returned from its model list (kept raw);
``openrouter``  the public OpenRouter catalogue (``/api/v1/models``);
``models.dev``  the public Models.dev catalogue (``/api/api.json``);
``catalog``     the built-in table of published limits in :mod:`app.model_info`;
``proxy``       what the Proxy itself does for every model (it always streams);
``heuristic``   the model's name (families, ``-vl``, ``thinking``, ``whisper``…).

Confidence: ``verified`` (probe), ``high`` (the model's own row, the file, an
exact catalogue match), ``medium`` (a catalogue row matched by canonical name,
the built-in table), ``low`` (a guess from the name or the family).

Nothing here touches the network on its own. The public catalogues are
downloaded only by :func:`refresh_catalog` (the "Refresh metadata" button in
Proxy settings); until then the Proxy reads the copy that the MemuAI "Model
catalogue" module may already have cached. The vision probe runs only when
the user clicks it, because it spends a real request.

Stable functions for other parts of the app (Quick Ask, MemuAI window,
Telegram, MemuCode):

* :func:`vision_for(public_id)` -> ``True`` / ``False`` / ``None`` — can it see images;
* :func:`capabilities_for(public_id)` -> compact dict for badges and filters;
* :func:`describe(public_id)` -> the full card (every field with source + confidence, raw JSON).
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
import time
import zlib
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .config import DATA_DIR
from . import model_info

# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

CATALOG_FILE = "model-catalog.json"     # our own copy of OpenRouter + Models.dev (explicit refresh only)
RAW_FILE = "model-raw.json"             # raw rows of providers' model lists
PROBES_FILE = "model-probes.json"       # results of user-started vision probes
OVERRIDES_FILE = "model-overrides.json"  # Round 24: capabilities the user set by hand

#: Round 24 — what the model can do *through this connection* vs. what public catalogues say.
#: Routing (vision filters, DFY, attachments) reads only ``effective``; ``reference`` is shown as
#: "по данным OpenRouter / Models.dev" and never turns a capability on by itself.
EFFECTIVE_KEYS = ("vision", "tools", "reasoning", "context", "max_output", "structured_output")
OVERRIDE_KEYS = ("vision", "tools", "reasoning", "context", "max_output")
#: Sources that describe this API / provider: user override, the vision probe, the local file,
#: the provider's own /models row and the Proxy's adapter facts.
EFFECTIVE_SOURCES = ("user", "probe", "local", "provider", "proxy")
REFERENCE_SOURCES = ("openrouter", "models.dev")
SOURCE_KIND = {"user": "user-override", "probe": "api-probe", "local": "provider", "provider": "provider",
               "proxy": "provider", "openrouter": "openrouter-reference", "models.dev": "openrouter-reference",
               "catalog": "reference", "heuristic": "heuristic"}

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
MAX_DOWNLOAD = 24 * 1024 * 1024
MAX_RAW_ROW = 24_000                    # characters of JSON kept per provider row

SOURCE_RANK = {"user": -1, "probe": 0, "local": 1, "provider": 2, "openrouter": 3, "models.dev": 4,
               "catalog": 5, "proxy": 6, "heuristic": 7}
CONFIDENCE_RANK = {"verified": 0, "high": 1, "medium": 2, "low": 3}

#: Providers whose adapters pass pictures to the model (local: only with an mmproj projector).
IMAGE_ADAPTERS = {"genspark", "antigravity", "kiro", "custom"}
#: Providers that run on web accounts or subscriptions: prices are an API-equivalent estimate.
SUBSCRIPTION_PROVIDERS = {"genspark", "kiro", "antigravity", "geminiweb", "grok"}

MODALITIES = ("text", "image", "audio", "video", "file")

#: Field order and groups of the card. The UI renders groups in this order.
GROUPS: List[Tuple[str, Tuple[str, ...]]] = [
    ("identity", ("name", "kind", "vendor", "family", "description", "huggingface", "tokenizer", "canonical")),
    ("modalities", ("input", "output", "vision", "audio_input", "video_input", "file_input",
                    "image_output", "audio_output")),
    ("features", ("tools", "structured_output", "json_mode", "reasoning", "reasoning_efforts",
                  "reasoning_mandatory", "web_search", "streaming", "temperature", "parameters")),
    ("limits", ("context", "max_output", "trained_context", "rate_limits")),
    ("pricing", ("billing", "free", "price_input", "price_output", "price_cache_read", "price_cache_write",
                 "price_reasoning", "price_audio_input", "price_image_input", "price_image_output",
                 "price_web_search", "price_request", "price_tiers")),
    ("dates", ("release_date", "last_updated", "knowledge_cutoff", "expiration_date", "deprecated", "status")),
    ("weights", ("open_weights", "license", "quantization", "size_label", "architecture", "layers",
                 "file_size", "projector", "moderated")),
]
FIELD_ORDER = [key for _group, keys in GROUPS for key in keys]

_lock = threading.RLock()


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _save(path: str, data: Any) -> None:
    try:
        from .storage import save_json
        save_json(path, data, backup=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _tokens(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None and 256 <= number <= 100_000_000 else None


def _per_million(value: Any) -> Optional[float]:
    """OpenRouter prices are USD per token (strings); ``-1`` means "varies"."""
    number = _num(value)
    if number is None or number < 0:
        return None
    return round(number * 1_000_000, 6)


def _price(value: Any) -> Optional[float]:
    number = _num(value)
    return None if number is None or number < 0 else round(number, 6)


def _strs(values: Any) -> List[str]:
    if isinstance(values, str):
        values = re.split(r"[,+\s]+", values)
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(v).strip().lower() for v in values if isinstance(v, (str, int)) and str(v).strip()]


def _modality(name: str) -> str:
    name = str(name or "").lower()
    if name in ("pdf", "document", "documents", "file", "files"):
        return "file"
    if name in ("images", "img", "vision", "image_url"):
        return "image"
    if name in ("speech", "voice"):
        return "audio"
    if name in ("embedding", "embeddings", "vector"):
        return "embeddings"
    return name


def _modalities(values: Any) -> List[str]:
    out: List[str] = []
    for value in _strs(values):
        name = _modality(value)
        if name and name not in out:
            out.append(name)
    return out


def _date(value: Any) -> str:
    """Unix seconds/ms or a date string → ``YYYY-MM-DD`` (or the original text, trimmed)."""
    if value in (None, "", 0):
        return ""
    number = _num(value)
    if number is not None and number > 10_000_000:
        number = number / 1000 if number > 1e11 else number
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(number))
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    return text[:32]


def _bare(model: str) -> str:
    """``anthropic/Claude-Haiku-4.5:free`` → ``claude-haiku-4.5`` (only vendor path and ``:variant`` dropped)."""
    text = str(model or "").strip().lower().lstrip("~")
    text = text.split("/")[-1]
    return re.split(r":(?![0-9]{2}$)", text)[0]


# ---------------------------------------------------------------------------
# Public catalogues: normalised rows
# ---------------------------------------------------------------------------

def normalize_openrouter(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    arch = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    top = row.get("top_provider") if isinstance(row.get("top_provider"), dict) else {}
    params = _strs(row.get("supported_parameters"))
    reasoning = row.get("reasoning") if isinstance(row.get("reasoning"), dict) else {}
    name = str(row.get("name") or row["id"])
    model_in = _modalities(arch.get("input_modalities"))
    model_out = _modalities(arch.get("output_modalities"))
    if not model_in and isinstance(arch.get("modality"), str) and "->" in arch["modality"]:
        left, right = arch["modality"].split("->", 1)
        model_in, model_out = _modalities(left), _modalities(right)
    price = {
        "input": _per_million(pricing.get("prompt")), "output": _per_million(pricing.get("completion")),
        "cache_read": _per_million(pricing.get("input_cache_read")),
        "cache_write": _per_million(pricing.get("input_cache_write")),
        "reasoning": _per_million(pricing.get("internal_reasoning")),
        "audio_input": _per_million(pricing.get("audio")),
        "image_input": _price(pricing.get("image")),
        "image_output": _per_million(pricing.get("image_output")),
        "web_search": _price(pricing.get("web_search")),
        "request": _price(pricing.get("request")),
    }
    tiers = []
    for tier in pricing.get("overrides") or []:
        if isinstance(tier, dict):
            tiers.append({"min_prompt_tokens": tier.get("min_prompt_tokens"),
                          "input": _per_million(tier.get("prompt")), "output": _per_million(tier.get("completion"))})
    return {
        "id": str(row["id"]), "source": "openrouter",
        "name": name.split(": ", 1)[1] if ": " in name else name,
        "vendor": name.split(": ", 1)[0] if ": " in name else str(row["id"]).lstrip("~").split("/")[0],
        "family": "", "description": str(row.get("description") or "")[:2000],
        "release_date": _date(row.get("created")), "last_updated": "",
        "cutoff": _date(row.get("knowledge_cutoff")), "expires": _date(row.get("expiration_date")),
        "context": _tokens(row.get("context_length")) or _tokens(top.get("context_length")),
        "max_out": _tokens(top.get("max_completion_tokens")),
        "in": model_in, "out": model_out,
        "tools": ("tools" in params) if params else None,
        "structured": ("structured_outputs" in params) if params else None,
        "json_mode": ("response_format" in params or "structured_outputs" in params) if params else None,
        "reasoning": ("reasoning" in params or "include_reasoning" in params or bool(reasoning)) if params or reasoning else None,
        "reasoning_efforts": _strs(reasoning.get("supported_efforts")),
        "reasoning_mandatory": reasoning.get("mandatory") if isinstance(reasoning.get("mandatory"), bool) else None,
        # pricing.web_search is OpenRouter's own search plugin (any model); only the parameter means native search.
        "web": True if "web_search_options" in params else None,
        "temperature": ("temperature" in params) if params else None,
        "open_weights": True if row.get("hugging_face_id") else None,
        "price": price, "price_tiers": tiers,
        "hf": str(row.get("hugging_face_id") or ""), "tokenizer": str(arch.get("tokenizer") or ""),
        "params": params[:60], "moderated": top.get("is_moderated") if isinstance(top.get("is_moderated"), bool) else None,
        "status": "", "rate_limits": row.get("per_request_limits") or None,
        "alias": bool(row.get("alias_target")) or str(row["id"]).startswith("~"),
        "raw": row,
    }


#: Models.dev providers that are the lab itself (its listing wins over resellers).
LAB_PROVIDERS = {"anthropic", "openai", "google", "xai", "deepseek", "mistral", "alibaba", "moonshotai", "zai",
                 "zhipuai", "minimax", "meta", "meta-llama", "cohere", "amazon-bedrock", "nvidia", "qwen",
                 "inception", "perplexity", "ai21", "baidu", "bytedance", "tencent", "stepfun", "microsoft"}


def normalize_models_dev(provider_id: str, provider: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
    limit = row.get("limit") if isinstance(row.get("limit"), dict) else {}
    modal = row.get("modalities") if isinstance(row.get("modalities"), dict) else {}
    canonical_id = str(row.get("canonical_model_id") or "")
    efforts: List[str] = []
    for option in row.get("reasoning_options") or []:
        if isinstance(option, dict):
            efforts += _strs(option.get("values") or option.get("efforts") or [])
    return {
        "id": canonical_id or f"{provider_id}/{row['id']}", "source": "models.dev",
        "name": str(row.get("name") or row["id"]), "vendor": str((provider or {}).get("name") or provider_id),
        "provider_id": provider_id, "family": str(row.get("family") or ""),
        "description": str(row.get("description") or "")[:2000],
        "release_date": _date(row.get("release_date")), "last_updated": _date(row.get("last_updated")),
        "cutoff": _date(row.get("knowledge")), "expires": "",
        "context": _tokens(limit.get("context")), "max_out": _tokens(limit.get("output")),
        "in": _modalities(modal.get("input")), "out": _modalities(modal.get("output")),
        "tools": row.get("tool_call") if isinstance(row.get("tool_call"), bool) else None,
        "structured": row.get("structured_output") if isinstance(row.get("structured_output"), bool) else None,
        "json_mode": row.get("structured_output") if isinstance(row.get("structured_output"), bool) else None,
        "reasoning": row.get("reasoning") if isinstance(row.get("reasoning"), bool) else None,
        "reasoning_efforts": efforts, "reasoning_mandatory": None, "web": None,
        "temperature": row.get("temperature") if isinstance(row.get("temperature"), bool) else None,
        "open_weights": row.get("open_weights") if isinstance(row.get("open_weights"), bool) else None,
        "price": {"input": _price(cost.get("input")), "output": _price(cost.get("output")),
                  "cache_read": _price(cost.get("cache_read")), "cache_write": _price(cost.get("cache_write")),
                  "reasoning": _price(cost.get("reasoning")), "audio_input": _price(cost.get("input_audio")),
                  "image_input": None, "image_output": None, "web_search": None, "request": None},
        "price_tiers": [], "hf": "", "tokenizer": "", "params": [], "moderated": None,
        "status": str(row.get("status") or ("experimental" if row.get("experimental") else "")),
        "rate_limits": None, "alias": False,
        "raw": {"provider": provider_id, **row},
    }


def models_dev_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per model (canonical name): the lab's own listing first, then any with a canonical id,
    a live one before a deprecated one. Models.dev lists the same model once per reseller (~8400 rows)."""
    best: Dict[str, Tuple[Tuple[int, int, int], Dict[str, Any]]] = {}
    for provider_id, provider in (data or {}).items():
        if not isinstance(provider, dict):
            continue
        for row in (provider.get("models") or {}).values():
            slim = normalize_models_dev(str(provider_id), provider, row)
            if slim is None:
                continue
            key = model_info.canonical(row.get("canonical_model_id") or row.get("id"))
            if not key:
                continue
            canonical_id = str(row.get("canonical_model_id") or "")
            lab = str(provider_id) in LAB_PROVIDERS or (canonical_id and canonical_id.split("/")[0] == provider_id)
            rank = (1 if slim["status"] == "deprecated" else 0, 0 if lab else 1, 0 if canonical_id else 1)
            if key not in best or rank < best[key][0]:
                best[key] = (rank, slim)
    return [row for _rank, row in best.values()]


def _module_rows() -> List[Dict[str, Any]]:
    """Rows the MemuAI "Model catalogue" module cached (slim JS shape) — used until our own copy exists."""
    try:
        from .pgdata import MEMU_DIR
        data = _read_json(os.path.join(str(MEMU_DIR), "modules", "persist", "catalog.json"))
    except Exception:
        data = None
    block = data.get("cache") if isinstance(data, dict) else None
    rows = block.get("rows") if isinstance(block, dict) else None
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        price = row.get("price") if isinstance(row.get("price"), dict) else {}
        source = "models.dev" if row.get("source") == "models.dev" else "openrouter"
        out.append({
            "id": str(row["id"]), "source": source, "name": str(row.get("name") or row["id"]),
            "vendor": str(row.get("provider") or row.get("org") or ""), "family": "",
            "description": str(row.get("description") or ""), "release_date": _date(row.get("created")),
            "last_updated": "", "cutoff": _date(row.get("cutoff")), "expires": _date(row.get("expires")),
            "context": _tokens(row.get("context")), "max_out": _tokens(row.get("maxOut")),
            "in": _modalities(row.get("in")), "out": _modalities(row.get("out")),
            "tools": row.get("tools") if isinstance(row.get("tools"), bool) else None,
            "structured": row.get("structured") if isinstance(row.get("structured"), bool) else None,
            "json_mode": row.get("structured") if isinstance(row.get("structured"), bool) else None,
            "reasoning": row.get("reasoning") if isinstance(row.get("reasoning"), bool) else None,
            "reasoning_efforts": [], "reasoning_mandatory": None, "web": True if row.get("web") else None,
            "temperature": None, "open_weights": True if row.get("hf") else None,
            "price": {"input": _price(price.get("input")), "output": _price(price.get("output")),
                      "cache_read": _price(price.get("cacheRead")), "cache_write": _price(price.get("cacheWrite")),
                      "reasoning": _price(price.get("reasoning")), "audio_input": None,
                      "image_input": _price(price.get("image")), "image_output": None,
                      "web_search": _price(price.get("web")), "request": None},
            "price_tiers": [], "hf": str(row.get("hf") or ""), "tokenizer": str(row.get("tokenizer") or ""),
            "params": _strs(row.get("params")), "moderated": row.get("moderated") if isinstance(row.get("moderated"), bool) else None,
            "status": "", "rate_limits": None, "alias": str(row["id"]).startswith("~"),
            "raw": row, "via_module": True,
        })
    return out


class _Catalog:
    """Index over the catalogue rows: canonical name → best row per source. Rebuilt when a file changes."""

    #: The MemuAI window polls /api/admin/pg/state every 3 s with hundreds of models: the files are
    #: stat()ed at most this often (a stat costs ~30 µs on Windows/Defender, ×2 per model added up).
    CHECK_EVERY = 2.0

    def __init__(self) -> None:
        self.stamp: Tuple[float, float] = (-1.0, -1.0)
        self.by_key: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.meta: Dict[str, Any] = {}
        self.checked = 0.0
        self._module_path: Optional[str] = None

    def module_path(self) -> str:
        if self._module_path is None:
            try:
                from .pgdata import MEMU_DIR
                self._module_path = os.path.join(str(MEMU_DIR), "modules", "persist", "catalog.json")
            except Exception:
                self._module_path = ""
        return self._module_path

    def ensure(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.stamp[0] >= 0 and now - self.checked < self.CHECK_EVERY:
            return
        self.checked = now
        stamp = (_mtime(_path(CATALOG_FILE)), _mtime(self.module_path()) if self.module_path() else 0.0)
        if stamp == self.stamp:
            return
        data = _read_json(_path(CATALOG_FILE))
        rows: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {"own": False, "module": False, "sources": {}}
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            rows = [row for row in data["rows"] if isinstance(row, dict) and row.get("id")]
            meta.update({"own": True, "at": data.get("at"), "sources": data.get("sources") or {}})
        have = {row.get("source") for row in rows}
        module = [row for row in _module_rows() if row["source"] not in have]
        if module:
            meta["module"] = True
        index: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in rows + module:
            key = model_info.canonical(str(row["id"]).lstrip("~"))
            if not key:
                continue
            slot = index.setdefault(key, {})
            current = slot.get(row["source"])
            # A real listing beats a "~…-latest" alias; a paid listing beats a ":free" promo; shorter id wins.
            rank = (1 if row.get("alias") else 0, 1 if ":" in str(row["id"]) else 0, len(str(row["id"])))
            if current is None or rank < current["_rank"]:
                slot[row["source"]] = {**row, "_rank": rank}
        meta["rows"] = len(rows) + len(module)
        self.by_key, self.meta, self.stamp = index, meta, stamp

    def match(self, model: str) -> Dict[str, Tuple[Dict[str, Any], str]]:
        """``{source: (row, "exact"|"canonical")}`` for one model name."""
        self.ensure()
        key = model_info.canonical(model)
        found = self.by_key.get(key) or {}
        bare = _bare(model)
        result: Dict[str, Tuple[Dict[str, Any], str]] = {}
        for source, row in found.items():
            kind = "exact" if _bare(row["id"]) == bare or _bare(row.get("raw", {}).get("id", "")) == bare else "canonical"
            result[source] = (row, kind)
        return result


_catalog = _Catalog()


def catalog_status() -> Dict[str, Any]:
    with _lock:
        _catalog.ensure(force=True)
        meta = dict(_catalog.meta)
    at = _num(meta.get("at"))
    return {"own": bool(meta.get("own")), "module": bool(meta.get("module")), "rows": meta.get("rows", 0),
            "at": at, "age": (time.time() - at) if at else None, "sources": meta.get("sources") or {}}


def refresh_catalog(*, timeout: float = 40.0,
                    fetch: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """Download OpenRouter and Models.dev (explicit user action). A failed source keeps its previous rows."""
    def default_fetch(url: str) -> Any:
        import httpx
        from .version import APP_VERSION
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"Accept": "application/json", "User-Agent": f"MemuAI-Proxy/{APP_VERSION}"}) as client:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > MAX_DOWNLOAD:
                raise ValueError("ответ слишком большой")
            return response.json()

    fetch = fetch or default_fetch
    previous = _read_json(_path(CATALOG_FILE))
    old_rows = previous.get("rows") if isinstance(previous, dict) and isinstance(previous.get("rows"), list) else []
    old_sources = previous.get("sources") if isinstance(previous, dict) and isinstance(previous.get("sources"), dict) else {}
    rows: List[Dict[str, Any]] = []
    sources: Dict[str, Any] = {}
    for name, url in (("openrouter", OPENROUTER_URL), ("models.dev", MODELS_DEV_URL)):
        try:
            data = fetch(url)
            if name == "openrouter":
                got = [r for r in (normalize_openrouter(x) for x in (data or {}).get("data") or []) if r]
            else:
                got = models_dev_rows(data if isinstance(data, dict) else {})
            if not got:
                raise ValueError("пустой список моделей")
            rows += got
            sources[name] = {"ok": True, "rows": len(got), "at": time.time(), "url": url}
        except Exception as exc:
            kept = [row for row in old_rows if isinstance(row, dict) and row.get("source") == name]
            rows += kept
            sources[name] = {"ok": False, "error": str(exc)[:300], "rows": len(kept), "url": url,
                             "at": (old_sources.get(name) or {}).get("at")}
    ok = any(item.get("ok") for item in sources.values())
    if rows:
        with _lock:
            _save(_path(CATALOG_FILE), {"at": time.time() if ok else (previous or {}).get("at"),
                                        "sources": sources, "rows": rows})
            _summary_cache.clear()
            _catalog.ensure(force=True)
    return {"ok": ok, "sources": sources, "rows": len(rows)}


# ---------------------------------------------------------------------------
# Raw provider rows
# ---------------------------------------------------------------------------

_raw: Dict[str, Dict[str, Any]] = {}
_raw_loaded = False
_raw_version = 0
_raw_timer: Optional[threading.Timer] = None


def _raw_key(prefix: str, model: str) -> str:
    return f"{str(prefix or '').lower()}::{str(model or '')}"


def _trim(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, dict):
        return {str(k)[:200]: _trim(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, list):
        return [_trim(v, depth + 1) for v in value[:200]]
    return value


def _ensure_raw() -> None:
    global _raw_loaded
    if _raw_loaded:
        return
    _raw_loaded = True
    data = _read_json(_path(RAW_FILE))
    if isinstance(data, dict):
        _raw.update({str(k): v for k, v in data.items() if isinstance(v, dict)})


def _flush_raw() -> None:
    global _raw_timer
    with _lock:
        _raw_timer = None
        snapshot = dict(_raw)
    _save(_path(RAW_FILE), snapshot)


def record_raw(prefix: str, rows: Iterable[Dict[str, Any]], *, id_field: str = "id",
               source: str = "api:models") -> int:
    """Keep the provider's own model-list rows (all fields) so the card can show them. Never blocks."""
    global _raw_timer, _raw_version
    count = 0
    changed = False
    with _lock:
        _ensure_raw()
        for row in rows or []:
            if not isinstance(row, dict) or not row.get(id_field):
                continue
            trimmed = _trim(row)
            text = json.dumps(trimmed, ensure_ascii=False, default=str)
            if len(text) > MAX_RAW_ROW:
                trimmed = {"_truncated": True, "json": text[:MAX_RAW_ROW]}
            key = _raw_key(prefix, str(row[id_field]))
            old = _raw.get(key) or {}
            count += 1
            if old.get("row") == trimmed and old.get("source") == source:
                continue
            _raw[key] = {"row": trimmed, "source": source, "ts": int(time.time())}
            changed = True
        if changed:
            _raw_version += 1
            _summary_cache.clear()
            if _raw_timer is None:
                _raw_timer = threading.Timer(1.5, _flush_raw)
                _raw_timer.daemon = True
                _raw_timer.start()
    return count


def raw_row(prefix: str, provider: str, model: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _ensure_raw()
        found = _raw.get(_raw_key(prefix, model)) or _raw.get(_raw_key(provider, model))
        return dict(found) if found else None


def extract_provider(row: Dict[str, Any]) -> Dict[str, Any]:
    """Capabilities from one provider row of any common shape (OpenAI-compatible, OpenRouter, LM Studio,
    Ollama, vLLM, Together, Gemini API, Kiro ``ListAvailableModels``, Antigravity ``fetchAvailableModels``).
    Only facts the row states; nothing is guessed here."""
    out: Dict[str, Any] = {}
    if not isinstance(row, dict) or row.get("_truncated"):
        return out
    if isinstance(row.get("architecture"), dict) or isinstance(row.get("pricing"), dict) and "prompt" in row["pricing"]:
        slim = normalize_openrouter({"id": row.get("id") or "x", **row})
        if slim:
            for key in ("in", "out", "tools", "structured", "json_mode", "reasoning", "reasoning_efforts",
                        "web", "temperature", "hf", "tokenizer", "moderated", "rate_limits", "cutoff", "expires"):
                if slim.get(key) not in (None, "", [], {}):
                    out[key] = slim[key]
            if any(v is not None for v in slim["price"].values()):
                out["price"] = slim["price"]
            if slim.get("price_tiers"):
                out["price_tiers"] = slim["price_tiers"]
    context, output = model_info.extract_limits(row)
    if row.get("tokenLimits") and isinstance(row["tokenLimits"], dict):        # Kiro
        context = context or _tokens(row["tokenLimits"].get("maxInputTokens"))
        output = output or _tokens(row["tokenLimits"].get("maxOutputTokens"))
    if context:
        out["context"] = context
    if output:
        out["max_out"] = output
    # modalities
    for field in ("input_modalities", "modalities", "supportedInputTypes", "input_types", "inputModalities"):
        value = row.get(field)
        if isinstance(value, dict):
            if value.get("input"):
                out.setdefault("in", _modalities(value.get("input")))
            if value.get("output"):
                out.setdefault("out", _modalities(value.get("output")))
        elif value:
            out.setdefault("in", _modalities(value))
    if row.get("output_modalities"):
        out.setdefault("out", _modalities(row.get("output_modalities")))
    caps = row.get("capabilities")
    flags: Dict[str, Any] = {}
    if isinstance(caps, list):                                                # Ollama / LM Studio
        names = set(_strs(caps))
        flags = {"vision": "vision" in names or None, "tools": ("tools" in names or "tool_use" in names) or None,
                 "reasoning": ("thinking" in names or "reasoning" in names) or None,
                 "embedding": "embedding" in names or None}
    elif isinstance(caps, dict):
        for key, value in caps.items():
            if isinstance(value, bool):
                flags[str(key).lower()] = value
    for key in ("supports_vision", "supportsImages", "supports_images", "vision"):
        if isinstance(row.get(key), bool):
            flags["vision"] = row[key]
    for key in ("supports_tools", "supports_function_calling", "function_calling", "supportsTools", "tool_call"):
        if isinstance(row.get(key), bool):
            flags["tools"] = row[key]
    for key in ("supports_reasoning", "thinking", "reasoning", "supportsThinking"):
        if isinstance(row.get(key), bool):
            flags["reasoning"] = row[key]
    if isinstance(row.get("supports_response_schema"), bool):
        flags["structured_output"] = row["supports_response_schema"]
    if flags.get("vision") is True and "image" not in (out.get("in") or []):
        out["in"] = list(dict.fromkeys((out.get("in") or ["text"]) + ["image"]))
    if flags.get("vision") is False and out.get("in") is None:
        out["vision"] = False
    for key, target in (("tools", "tools"), ("function_calling", "tools"), ("reasoning", "reasoning"),
                        ("thinking", "reasoning"), ("structured_output", "structured"), ("json_mode", "json_mode"),
                        ("web_search", "web"), ("streaming", "streaming")):
        if isinstance(flags.get(key), bool) and key != "vision":
            out.setdefault(target, flags[key])
    # Round 33 (r33-models): one-api / new-api gateways list the endpoints a model serves
    # («supported_endpoint_types»: ["image-generation", "openai"]) — an image generator, whatever its name.
    endpoints = {e.replace("_", "-") for e in _strs(row.get("supported_endpoint_types") or row.get("endpoint_types"))}
    if endpoints & {"image-generation", "images", "image-generations", "dall-e"}:
        out["kind"] = "image"
        out["out"] = ["image"]
    elif endpoints & {"video-generation", "videos", "video"}:
        out["kind"] = "video"
        out["out"] = ["video"]
    elif endpoints & {"embeddings", "embedding"} and not endpoints & {"openai", "chat", "anthropic", "gemini"}:
        out["kind"] = "embedding"
    kind = str(row.get("type") or row.get("model_type") or row.get("object_type") or "").lower()
    if kind in ("vlm",):
        out["in"] = list(dict.fromkeys((out.get("in") or ["text"]) + ["image"]))
    if kind in ("embedding", "embeddings") or flags.get("embedding"):
        out["kind"] = "embedding"
    methods = _strs(row.get("supportedGenerationMethods"))
    if methods and "embedcontent" in methods and "generatecontent" not in methods:
        out["kind"] = "embedding"
    for key in ("quantization", "quant", "quantization_level"):
        if row.get(key) and isinstance(row.get(key), (str, int)):
            out["quantization"] = str(row[key])
        elif isinstance(row.get("details"), dict) and row["details"].get("quantization_level"):
            out["quantization"] = str(row["details"]["quantization_level"])
    if isinstance(row.get("details"), dict):                                   # Ollama
        details = row["details"]
        if details.get("parameter_size"):
            out["size_label"] = str(details["parameter_size"])
        if details.get("family"):
            out["family"] = str(details["family"])
    if row.get("arch"):
        out["architecture"] = str(row["arch"])
    for key in ("displayName", "display_name", "modelName", "name"):
        if isinstance(row.get(key), str) and row[key].strip():
            name = row[key].strip()
            if isinstance(row.get("architecture"), dict) and ": " in name:
                name = name.split(": ", 1)[1]          # OpenRouter-style "Vendor: Model"
            out["name"] = name
            break
    if isinstance(row.get("description"), str) and row["description"].strip():
        out["description"] = row["description"].strip()[:2000]
    owner = row.get("owned_by") or row.get("organization") or row.get("publisher")
    if isinstance(owner, str) and owner.strip():
        out["vendor"] = owner.strip()
    created = row.get("created") or row.get("created_at")
    if created:
        out["release_date"] = _date(created)
    if isinstance(row.get("deprecated"), bool):
        out["deprecated"] = row["deprecated"]
    if row.get("deprecation_date") or row.get("deprecationDate"):
        out["expires"] = _date(row.get("deprecation_date") or row.get("deprecationDate"))
    if isinstance(row.get("pricing"), dict) and "price" not in out:            # Together: USD per 1M
        pricing = row["pricing"]
        price = {"input": _price(pricing.get("input")), "output": _price(pricing.get("output"))}
        if any(v is not None for v in price.values()):
            out["price"] = price
    limits = {k: row.get(k) for k in ("rate_limits", "rateLimits", "rpm", "tpm", "rpd") if row.get(k)}
    if limits and not out.get("rate_limits"):
        out["rate_limits"] = limits
    if isinstance(row.get("recommended"), bool):
        out["recommended"] = row["recommended"]
    return out


# ---------------------------------------------------------------------------
# Name heuristics
# ---------------------------------------------------------------------------

_IMAGE_GEN = re.compile(r"(?<![a-z0-9])(?:dall-?e|gpt-image|imagen|flux|sdxl|stable-diffusion|seedream|kolors|recraft|"
                        r"ideogram|nano-banana|midjourney|playground-v|hidream|qwen-image|wan-?2|kling|veo|sora|"
                        r"runway|hailuo|seedance|imagine|wanx|cogview|kandinsky|t2i)(?![a-z])|-image(?:-|$)")
_VIDEO_GEN = re.compile(r"(?<![a-z0-9])(?:veo|sora|kling|runway|hailuo|seedance|wan-?2\.\d-?(?:t2v|i2v)|video)(?![a-z])")
_EMBED = re.compile(r"(?<![a-z0-9])(?:embed\w*|bge|e5-|gte-|nomic-embed|rerank\w*)")
_STT = re.compile(r"(?<![a-z0-9])(?:whisper|transcribe|stt|asr|parakeet)(?![a-z])")
_TTS = re.compile(r"(?<![a-z0-9])(?:tts|speech|voice|eleven|kokoro|piper)(?![a-z])")
_MUSIC = re.compile(r"(?<![a-z0-9])(?:suno|udio|lyria|music)(?![a-z])")
_AUDIO_CHAT = re.compile(r"audio|realtime|gpt-4o-(?:mini-)?audio|omni|gemini-(?:2|3)")
_REASONING = re.compile(r"thinking|reason|(?<![a-z0-9])r1(?![0-9])|qwq|qvq|(?<![a-z0-9])o[134](?![0-9a-z])|"
                        r"gpt-?5|gpt-?6|deepseek-?(?:v3\.[1-9]|v4)|magistral|grok-?[45]|grok-?3-mini|claude-(?:opus|sonnet|haiku)-?[45]|"
                        r"(?:opus|sonnet|haiku|fable|mythos)-?[45]|gemini-?(?:2\.5|3)|kimi-?k2\.|glm-?(?:4\.[5-9]|5)|minimax-?m|"
                        r"qwen-?3|phi-?4-?reason|nemotron.*(?:ultra|super)|hermes-?4|gpt-?oss|cogito")
_TOOLS = re.compile(r"claude|opus|sonnet|haiku|fable|mythos|gpt-?(?:4|5|6)|gpt-?oss|(?<![a-z0-9])o[34]|gemini|grok-?[2-9]|"
                    r"qwen-?(?:2\.5|3)|deepseek-?(?:v3|v4|chat|r1-?0528)|kimi|glm-?(?:4|5)|minimax|mistral|"
                    r"devstral|magistral|codestral|llama-?(?:3\.[1-3]|4)|command-?(?:r|a)|nova|hermes|granite-?3|phi-?4")
_NO_TOOLS = re.compile(r"(?<![a-z0-9])(?:o1-(?:mini|preview)|gpt-3\.5-turbo-instruct|davinci|babbage|deepseek-?r1(?!-?0528)|"
                       r"gemma-?2|llama-?2|phi-?[23])")
_WEB = re.compile(r"(?<![a-z0-9])(?:sonar|online|search|perplexity|deep-?research|compound)(?![a-z])")
_OPEN = re.compile(r"llama|mistral-?(?:7b|nemo|small)|mixtral|qwen|deepseek|gemma|phi-|gpt-?oss|kimi|glm|minimax|"
                   r"olmo|falcon|yi-|nemotron|granite|hermes|command-?r|codestral|devstral|magistral|smollm|internlm")
_CLOSED = re.compile(r"claude|opus|sonnet|haiku|fable|mythos|gpt-?(?:3|4|5|6)(?!-?oss)|(?<![a-z0-9])o[134]|gemini|grok|nova|"
                     r"command-?a|mistral-?(?:large|medium)|qwen-?(?:max|plus|turbo)")
_VENDORS = [("anthropic", r"claude|opus|sonnet|haiku|fable|mythos"), ("openai", r"gpt|chatgpt|(?<![a-z0-9])o[134]|dall-?e|whisper|sora|codex"),
            ("google", r"gemini|gemma|imagen|veo|lyria|nano-banana|palm"), ("xai", r"grok"), ("deepseek", r"deepseek"),
            ("alibaba", r"qwen|qwq|qvq|wan-"), ("moonshotai", r"kimi|moonshot"), ("z-ai", r"glm|cogview|cogvlm"),
            ("minimax", r"minimax|hailuo"), ("meta", r"llama|muse"), ("mistral", r"mistral|mixtral|codestral|devstral|magistral|pixtral|ministral"),
            ("cohere", r"command|aya"), ("amazon", r"nova|titan"), ("microsoft", r"phi-"), ("nvidia", r"nemotron"),
            ("black-forest-labs", r"flux"), ("bytedance", r"seedream|seedance|doubao|ui-tars"), ("ibm", r"granite")]
_VENDORS_RE = [(name, re.compile(pattern)) for name, pattern in _VENDORS]


def heuristics(model: str, provider: str = "") -> Dict[str, Any]:
    """What the name alone suggests. Everything here is ``low`` confidence (``vision`` from the curated
    family list in :mod:`app.model_info` is ``medium``)."""
    name = model_info.canonical(model)
    raw = str(model or "").lower()
    out: Dict[str, Any] = {}
    if not name:
        return out
    kind = "chat"
    if _EMBED.search(name):
        kind = "embedding"
    elif _MUSIC.search(name) and not re.search(r"chat|instruct", name):
        kind = "music"
    elif _VIDEO_GEN.search(name) and not re.search(r"vl|vision|understanding", name):
        kind = "video"
    elif _IMAGE_GEN.search(name):
        kind = "image"
    elif _STT.search(name):
        kind = "stt"
    elif _TTS.search(name) and not re.search(r"chat|instruct|omni", name):
        kind = "tts"
    out["kind"] = kind
    if kind == "embedding":
        out.update({"in": ["text"], "out": ["embeddings"], "tools": False, "reasoning": False})
        return out
    if kind == "image":
        out.update({"in": ["text", "image"] if re.search(r"edit|kontext|banana|gpt-image|seedream|qwen-image", name) else ["text"],
                    "out": ["image"], "tools": False})
        return out
    if kind == "video":
        out.update({"in": ["text", "image"], "out": ["video"], "tools": False})
        return out
    if kind == "music":
        out.update({"in": ["text"], "out": ["audio"], "tools": False})
        return out
    if kind == "stt":
        out.update({"in": ["audio"], "out": ["text"], "tools": False})
        return out
    if kind == "tts":
        out.update({"in": ["text"], "out": ["audio"], "tools": False})
        return out
    sees = model_info.vision_hint(model)
    if sees is not None:
        out["vision"] = sees
    model_in = ["text"] + (["image"] if sees else [])
    if _AUDIO_CHAT.search(name) and re.search(r"audio|realtime|omni|gemini", name):
        model_in.append("audio")
    out["in"] = model_in
    out["out"] = ["text"] + (["audio"] if re.search(r"audio|realtime|omni-?(?!.*vl)", name) else [])
    if _NO_TOOLS.search(name):
        out["tools"] = False
    elif _TOOLS.search(name):
        out["tools"] = True
    if _REASONING.search(name) or "thinking" in raw or "reasoning" in raw:
        out["reasoning"] = True
    if re.search(r"non-?reasoning|instant|-chat(?:-|$)|turbo", raw) and "thinking" not in raw:
        out["reasoning"] = False if not re.search(r"gpt-?5\.\d+-?chat", raw) else out.get("reasoning")
    if _WEB.search(name) or provider == "genspark":
        out["web"] = True
    if raw.endswith(":free") or raw.endswith("-free"):
        out["free"] = True
    if _OPEN.search(name) and not _CLOSED.search(name):
        out["open_weights"] = True
    elif _CLOSED.search(name):
        out["open_weights"] = False
    for vendor, pattern in _VENDORS_RE:
        if pattern.search(name):
            out["vendor"] = vendor
            break
    family = re.match(r"([a-z]+(?:-[a-z]+)?)", name)
    if family:
        out["family"] = family.group(1)
    known = model_info.lookup_known(model)
    if known and known.get("label"):
        out["label"] = known["label"]
    return out


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

_probes: Dict[str, Dict[str, Any]] = {}
_probes_loaded = False
_probe_version = 0

PROBE_COLORS = {
    "red": ((220, 30, 30), r"red|красн|rouge|rot\b"),
    "green": ((30, 170, 60), r"green|зел[её]н|vert\b|grün"),
    "blue": ((30, 70, 220), r"blue|син|голуб|bleu|blau"),
    "yellow": ((245, 215, 30), r"yellow|ж[её]лт|jaune|gelb"),
    "black": ((10, 10, 10), r"black|ч[её]рн|noir|schwarz"),
}
_BLIND_ANSWER = re.compile(r"can(?:no|')t (?:see|view|process|analy[sz]e|open)|unable to (?:see|view|process)|"
                           r"no image|don'?t see (?:an|any) image|text-?only|не (?:вижу|могу (?:увидеть|просмотреть|открыть))|"
                           r"нет изображени|не поддерживаю изображ", re.I)
_UNSUPPORTED_ERROR = re.compile(r"image|vision|multimodal|изображен|картин|mmproj|image_url", re.I)


def _ensure_probes() -> None:
    global _probes_loaded
    if _probes_loaded:
        return
    _probes_loaded = True
    data = _read_json(_path(PROBES_FILE))
    if isinstance(data, dict):
        _probes.update({str(k): v for k, v in data.items() if isinstance(v, dict)})


def probe_result(public_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _ensure_probes()
        found = _probes.get(str(public_id or ""))
        return dict(found) if found else None


def _store_probe(public_id: str, result: Dict[str, Any]) -> None:
    global _probe_version
    with _lock:
        _ensure_probes()
        _probes[str(public_id)] = result
        _probe_version += 1
        _summary_cache.clear()
        snapshot = dict(_probes)
    _save(_path(PROBES_FILE), snapshot)


def forget_probe(public_id: str) -> bool:
    global _probe_version
    with _lock:
        _ensure_probes()
        existed = _probes.pop(str(public_id), None) is not None
        if existed:
            _probe_version += 1
            _summary_cache.clear()
        snapshot = dict(_probes)
    if existed:
        _save(_path(PROBES_FILE), snapshot)
    return existed


_overrides: Dict[str, Dict[str, Any]] = {}
_overrides_loaded = False


def _ensure_overrides() -> None:
    global _overrides_loaded
    if _overrides_loaded:
        return
    _overrides_loaded = True
    data = _read_json(_path(OVERRIDES_FILE))
    if isinstance(data, dict):
        _overrides.update({str(k): v for k, v in data.items() if isinstance(v, dict)})


def get_override(public_id: str) -> Dict[str, Any]:
    with _lock:
        _ensure_overrides()
        return dict(_overrides.get(str(public_id or "")) or {})


def set_override(public_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Set capabilities by hand. ``None`` (or "") for a key removes it; booleans for flags, ints for limits."""
    with _lock:
        _ensure_overrides()
        current = dict(_overrides.get(str(public_id)) or {})
        for key, value in (changes or {}).items():
            if key not in OVERRIDE_KEYS:
                continue
            if value is None or value == "":
                current.pop(key, None)
            elif key in ("context", "max_output"):
                number = _tokens(value)
                if number:
                    current[key] = number
                else:
                    current.pop(key, None)
            elif isinstance(value, bool):
                current[key] = value
            elif str(value).lower() in ("true", "1", "yes", "да"):
                current[key] = True
            elif str(value).lower() in ("false", "0", "no", "нет"):
                current[key] = False
        if current:
            _overrides[str(public_id)] = current
        else:
            _overrides.pop(str(public_id), None)
        _summary_cache.clear()
        snapshot = dict(_overrides)
    _save(_path(OVERRIDES_FILE), snapshot)
    return current


def _best(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    items = sorted(items, key=lambda c: (CONFIDENCE_RANK.get(c["confidence"], 9), SOURCE_RANK.get(c["source"], 9)))
    best = {k: v for k, v in items[0].items() if k != "alts"}
    best["kind"] = SOURCE_KIND.get(best.get("source"), best.get("source"))
    return best


def split_capabilities(candidates: Dict[str, List[Dict[str, Any]]], *, adapter_blind: bool = False,
                       provider: str = "") -> Tuple[Dict[str, Optional[Dict[str, Any]]], Dict[str, Optional[Dict[str, Any]]]]:
    """(effective, reference) for :data:`EFFECTIVE_KEYS` from the raw evidence of one model.

    * effective — only from this API (user override, probe, local file, provider row, adapter facts);
      vision may also come from the provider's own list of input modalities;
    * reference — only from OpenRouter / Models.dev.
    A key with no evidence of that kind is ``None`` (unknown)."""
    effective: Dict[str, Optional[Dict[str, Any]]] = {}
    reference: Dict[str, Optional[Dict[str, Any]]] = {}
    for key in EFFECTIVE_KEYS:
        items = candidates.get(key) or []
        eff = [item for item in items if item["source"] in EFFECTIVE_SOURCES]
        ref = [item for item in items if item["source"] in REFERENCE_SOURCES]
        if key == "vision":
            for item in candidates.get("input") or []:
                if not isinstance(item.get("value"), list):
                    continue
                derived = {"value": "image" in item["value"], "source": item["source"], "confidence": item["confidence"],
                           "note": "из списка входных модальностей"}
                if item["source"] in EFFECTIVE_SOURCES and item["source"] != "proxy":
                    eff.append(derived)
                elif item["source"] in REFERENCE_SOURCES:
                    ref.append(derived)
        effective[key] = _best(eff)
        reference[key] = _best(ref)
    vision = effective.get("vision")
    if adapter_blind and not (vision and vision["source"] == "probe" and vision["value"]):
        effective["vision"] = {"value": False, "source": "proxy", "confidence": "high", "kind": "provider",
                               "note": f"адаптер {provider} не передаёт изображения"}
    return effective, reference


def solid_png(rgb: Tuple[int, int, int], size: int = 64) -> bytes:
    """A tiny solid-colour PNG without Pillow."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * size
    raw = row * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def judge_probe(answer: str, expected: str) -> Tuple[Optional[bool], str]:
    """``(vision, verdict)`` from the model's answer to "what colour is this square?"."""
    text = str(answer or "").strip()
    if not text:
        return None, "empty"
    if _BLIND_ANSWER.search(text):
        return False, "blind"
    hits = [name for name, (_rgb, pattern) in PROBE_COLORS.items() if re.search(pattern, text, re.I)]
    if expected in hits and len(hits) == 1:
        return True, "correct"
    if expected in hits:
        return None, "ambiguous"
    if hits:
        return False, "wrong"
    return None, "unclear"


async def probe_vision(public_id: str, *, complete: Optional[Callable[..., Any]] = None,
                       timeout: float = 90.0, color: str = "") -> Dict[str, Any]:
    """Send one tiny picture and ask for its colour. Spends one real request — run only on a user click."""
    import asyncio
    import secrets as _secrets
    color = color if color in PROBE_COLORS else _secrets.choice(list(PROBE_COLORS))
    rgb, _pattern = PROBE_COLORS[color]
    url = "data:image/png;base64," + base64.b64encode(solid_png(rgb)).decode("ascii")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "What single colour fills this image? Answer with one English word only."},
        {"type": "image_url", "image_url": {"url": url}}]}]
    if complete is None:
        from .model_call import complete as complete_call
        complete = complete_call
    started = time.time()
    result: Dict[str, Any] = {"kind": "vision", "ts": int(started), "expected": color}
    try:
        answer = await asyncio.wait_for(complete(public_id, messages, {"max_tokens": 64, "temperature": 0}), timeout)
        text = str((answer or {}).get("text") or "").strip()
        vision, verdict = judge_probe(text, color)
        result.update({"vision": vision, "verdict": verdict, "answer": text[:300],
                       "usage": (answer or {}).get("usage") or {}})
    except asyncio.TimeoutError:
        result.update({"vision": None, "verdict": "timeout", "error": "timeout"})
    except Exception as exc:
        message = str(getattr(exc, "message", "") or exc)[:400]
        unsupported = bool(_UNSUPPORTED_ERROR.search(message)) and int(getattr(exc, "status", 400) or 400) < 500
        result.update({"vision": False if unsupported else None,
                       "verdict": "unsupported" if unsupported else "error", "error": message})
    result["ms"] = int((time.time() - started) * 1000)
    if result.get("vision") is not None:
        _store_probe(public_id, result)
    return result


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class _Fields:
    def __init__(self) -> None:
        self.candidates: Dict[str, List[Dict[str, Any]]] = {}

    def add(self, key: str, value: Any, source: str, confidence: str, note: str = "") -> None:
        if value is None or value == "" or value == [] or value == {}:
            return
        self.candidates.setdefault(key, []).append(
            {"value": value, "source": source, "confidence": confidence, **({"note": note} if note else {})})

    def resolve(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for key, items in self.candidates.items():
            items = sorted(items, key=lambda c: (CONFIDENCE_RANK.get(c["confidence"], 9), SOURCE_RANK.get(c["source"], 9)))
            best = dict(items[0])
            alts = [dict(item) for item in items[1:]]
            if alts:
                best["alts"] = alts
            out[key] = best
        return out


def _entry(public_id: str):
    try:
        from .state import state
        return state.find_model(public_id)
    except Exception:
        return None


def _local_info(model: str) -> Optional[Dict[str, Any]]:
    try:
        from . import local_models
        return local_models.get_model(model)
    except Exception:
        return None


def _catalog_conf(kind: str) -> str:
    return "high" if kind == "exact" else "medium"


def _add_catalog(fields: _Fields, row: Dict[str, Any], kind: str) -> None:
    source = row["source"]
    conf = _catalog_conf(kind)
    note = row["id"] + (" · " + ("через модуль «Каталог моделей»" if row.get("via_module") else "")).rstrip(" ·")
    for key, field in (("name", "name"), ("vendor", "vendor"), ("family", "family"), ("description", "description"),
                       ("hf", "huggingface"), ("tokenizer", "tokenizer"), ("release_date", "release_date"),
                       ("last_updated", "last_updated"), ("cutoff", "knowledge_cutoff"), ("expires", "expiration_date"),
                       ("context", "context"), ("max_out", "max_output"), ("in", "input"), ("out", "output"),
                       ("tools", "tools"), ("structured", "structured_output"), ("json_mode", "json_mode"),
                       ("reasoning", "reasoning"), ("reasoning_efforts", "reasoning_efforts"),
                       ("reasoning_mandatory", "reasoning_mandatory"), ("web", "web_search"),
                       ("temperature", "temperature"), ("open_weights", "open_weights"), ("params", "parameters"),
                       ("moderated", "moderated"), ("rate_limits", "rate_limits"), ("price_tiers", "price_tiers")):
        fields.add(field, row.get(key), source, conf, note)
    status = str(row.get("status") or "")
    if status:
        fields.add("status", status, source, conf, note)
        fields.add("deprecated", status == "deprecated", source, conf, note)
    if row.get("expires"):
        fields.add("deprecated", True, source, conf, note)
    for key, value in (row.get("price") or {}).items():
        fields.add("price_" + key, value, source, conf, note)


def describe(public_id: str, *, entry: Any = None, include_raw: bool = True) -> Dict[str, Any]:
    """The full card of one catalogue model: every field with value, source, confidence and alternatives."""
    entry = entry or _entry(public_id)
    if entry is None:
        prefix, provider, model = "", "", str(public_id or "")
    else:
        prefix, provider, model = entry.prefix, entry.provider, entry.model
        public_id = entry.id
    fields = _Fields()
    sources: Dict[str, Any] = {}
    matched: Dict[str, Any] = {}

    # --- user override (Round 24) ------------------------------------------------
    override = get_override(public_id)
    for key, value in override.items():
        fields.add(key, value, "user", "verified", "указано вручную")
    if override:
        sources["user"] = override

    # --- probe ---------------------------------------------------------------
    probe = probe_result(public_id)
    if probe and probe.get("vision") is not None:
        fields.add("vision", bool(probe["vision"]), "probe", "verified" if probe["vision"] else "high",
                   f"{probe.get('verdict')} · {_date(probe.get('ts'))}")
        sources["probe"] = probe

    # --- local file ------------------------------------------------------------
    local = _local_info(model) if provider == "local" else None
    if local:
        meta = local.get("meta") or {}
        settings = local.get("settings") or {}
        projector = str(settings.get("mmproj") or "")
        sees = bool(projector) or bool(meta.get("vision"))
        fields.add("vision", bool(projector), "local", "high",
                   "проектор mmproj подключён" if projector else ("в файле есть визуальный энкодер, но проектор не подключён" if sees else "нет проектора mmproj"))
        fields.add("input", ["text", "image"] if projector else ["text"], "local", "high")
        fields.add("output", ["text"], "local", "high")
        for key, field in (("quant", "quantization"), ("size_label", "size_label"), ("architecture", "architecture"),
                           ("layers", "layers"), ("license", "license"), ("name", "name")):
            fields.add(field, meta.get(key) or None, "local", "high")
        fields.add("context", _tokens(settings.get("context")), "local", "high", "настройка запуска llama.cpp")
        fields.add("trained_context", _tokens(meta.get("context_length")), "local", "high", "из файла GGUF")
        fields.add("file_size", int(local.get("size") or 0) or None, "local", "high", str(local.get("file") or ""))
        fields.add("projector", os.path.basename(projector) if projector else None, "local", "high")
        fields.add("huggingface", local.get("repo") or None, "local", "high")
        fields.add("open_weights", True, "local", "high")
        fields.add("free", True, "local", "high", "работает на этом компьютере")
        fields.add("billing", "local", "local", "high")
        for key in ("input", "output"):
            fields.add("price_" + key, 0.0, "local", "high")
        sources["local"] = {key: local.get(key) for key in ("id", "file", "size", "repo", "meta", "settings", "state")}

    # --- provider row ------------------------------------------------------------
    raw = raw_row(prefix, provider, model)
    if raw and isinstance(raw.get("row"), dict):
        got = extract_provider(raw["row"])
        src = "provider"
        note = str(raw.get("source") or "")
        mapping = {"in": "input", "out": "output", "max_out": "max_output", "structured": "structured_output",
                   "web": "web_search", "hf": "huggingface", "cutoff": "knowledge_cutoff", "expires": "expiration_date"}
        for key, value in got.items():
            if key == "price":
                for pkey, pvalue in value.items():
                    fields.add("price_" + pkey, pvalue, src, "high", note)
                continue
            if key in ("recommended",):
                continue
            fields.add(mapping.get(key, key), value, src, "high", note)
        sources["provider"] = raw
    if not (raw and raw.get("row")):
        learned = model_info.learned(prefix, model) or model_info.learned(provider, model)
        if learned:
            fields.add("context", learned.get("context"), "provider", "high", str(learned.get("source") or ""))
            fields.add("max_output", learned.get("output"), "provider", "high", str(learned.get("source") or ""))

    # --- public catalogues -------------------------------------------------------
    with _lock:
        found = _catalog.match(model)
    for source in ("openrouter", "models.dev"):
        if source in found:
            row, kind = found[source]
            _add_catalog(fields, row, kind)
            matched[source] = {"id": row["id"], "match": kind, "via_module": bool(row.get("via_module"))}
            if include_raw:
                sources[source] = row.get("raw")

    # --- built-in table ---------------------------------------------------------
    known = model_info.lookup_known(model)
    if known:
        conf = "medium" if known.get("source") == "catalog" else "low"
        src = "catalog" if known.get("source") == "catalog" else "heuristic"
        fields.add("context", known.get("context"), src, conf, known.get("label") or "оценка по семейству")
        fields.add("max_output", known.get("output"), src, conf, known.get("label") or "")
        if known.get("label"):
            fields.add("name", known["label"], src, "low")

    # --- Proxy itself -----------------------------------------------------------
    fields.add("streaming", True, "proxy", "high", "Proxy отдаёт потоковый SSE для любой модели")
    if provider in SUBSCRIPTION_PROVIDERS:
        fields.add("billing", "subscription", "proxy", "high",
                   "веб-аккаунт или подписка: цены — эквивалент публичного API")
    elif provider == "custom":
        fields.add("billing", "api", "proxy", "high")
    adapter_blind = bool(provider) and provider not in IMAGE_ADAPTERS and provider != "local"

    # --- name ----------------------------------------------------------------------
    guess = heuristics(model, provider)
    hint_map = {"in": "input", "out": "output", "web": "web_search", "label": None}
    for key, value in guess.items():
        target = hint_map.get(key, key)
        if target is None:
            continue
        conf = "medium" if key == "vision" else "low"
        fields.add(target, value, "heuristic", conf)
    if provider == "antigravity":
        try:
            from .providers import antigravity_models
            if antigravity_models.is_thinking(model) or str(model).lower().endswith("-thinking"):
                fields.add("reasoning", True, "provider", "medium", "думающий вариант Antigravity")
        except Exception:
            pass

    resolved = fields.resolve()
    _derive(resolved)
    effective, reference = split_capabilities(fields.candidates, adapter_blind=adapter_blind, provider=provider)
    if adapter_blind and not (probe and probe.get("vision")):
        # The model may see, but this provider's adapter cannot pass a picture: for the Proxy it is "no".
        previous = resolved.get("vision")
        alts = []
        if previous:
            alts = [{k: v for k, v in previous.items() if k != "alts"}] + list(previous.get("alts") or [])
        resolved["vision"] = {"value": False, "source": "proxy", "confidence": "high",
                              "note": f"адаптер {provider} не передаёт изображения", **({"alts": alts} if alts else {})}
    groups = [{"id": gid, "fields": [k for k in keys if k in resolved]} for gid, keys in GROUPS]
    extra = sorted(k for k in resolved if k not in FIELD_ORDER)
    if extra:
        groups.append({"id": "other", "fields": extra})
    card = {
        "id": public_id, "prefix": prefix, "provider": provider, "model": model,
        "canonical": model_info.canonical(model), "enabled": bool(getattr(entry, "enabled", True)),
        "known": entry is not None,
        "fields": resolved, "groups": [g for g in groups if g["fields"]], "matched": matched,
        "summary": _summary_from(resolved, effective, reference),
        "effective": effective, "reference": reference, "override": override,
        "catalog": catalog_status(),
    }
    if include_raw:
        card["raw"] = sources
    return card


def _value(resolved: Dict[str, Dict[str, Any]], key: str) -> Any:
    item = resolved.get(key)
    return item.get("value") if item else None


def _derive(resolved: Dict[str, Dict[str, Any]]) -> None:
    """Fill the per-modality booleans from the strongest ``input``/``output`` and the reverse."""
    model_in = _value(resolved, "input") or []
    model_out = _value(resolved, "output") or []
    src_in = resolved.get("input") or {}
    src_out = resolved.get("output") or {}
    vision = resolved.get("vision")
    if model_in:
        sees = "image" in model_in
        if vision is None:
            resolved["vision"] = {"value": sees, "source": src_in.get("source"), "confidence": src_in.get("confidence"),
                                  "note": "из списка входных модальностей"}
        elif (CONFIDENCE_RANK.get(src_in.get("confidence"), 9), SOURCE_RANK.get(src_in.get("source"), 9)) < \
                (CONFIDENCE_RANK.get(vision.get("confidence"), 9), SOURCE_RANK.get(vision.get("source"), 9)) \
                and vision.get("source") not in ("probe", "proxy", "local"):
            alts = [dict(vision)] + list(vision.get("alts") or [])
            for alt in alts:
                alt.pop("alts", None)
            resolved["vision"] = {"value": sees, "source": src_in.get("source"),
                                  "confidence": src_in.get("confidence"), "note": "из списка входных модальностей",
                                  "alts": alts}
    for key, modality, src, values in (("audio_input", "audio", src_in, model_in), ("video_input", "video", src_in, model_in),
                                       ("file_input", "file", src_in, model_in), ("image_output", "image", src_out, model_out),
                                       ("audio_output", "audio", src_out, model_out)):
        if values and key not in resolved:
            resolved[key] = {"value": modality in values, "source": src.get("source"), "confidence": src.get("confidence")}
    pin, pout = _value(resolved, "price_input"), _value(resolved, "price_output")
    # "Free" only from prices that belong to this connection (the provider's own row, a local model): a public
    # catalogue lists the same model at another reseller, whose $0 promo says nothing about the user's endpoint.
    if "free" not in resolved and pin is not None and pout is not None \
            and (resolved.get("price_input") or {}).get("source") in ("provider", "local"):
        src = resolved["price_input"]
        resolved["free"] = {"value": pin == 0 and pout == 0, "source": src.get("source"),
                            "confidence": src.get("confidence"), "note": "по ценам каталога"}
    billing = _value(resolved, "billing")
    if billing == "subscription" and "free" in resolved and resolved["free"]["source"] != "heuristic":
        # The catalogue price of the same model says nothing about a web account: it is not "free".
        resolved["free"] = {"value": False, "source": "proxy", "confidence": "high",
                            "note": "оплачивается подпиской или квотой аккаунта"}
    out = model_out
    kind = ("image" if out == ["image"] else "audio" if out == ["audio"] else "embedding" if "embeddings" in out else
            "video" if "video" in out and "text" not in out else "chat") if out else None
    current = resolved.get("kind")
    if kind and current is None:
        resolved["kind"] = {"value": kind, "source": src_out.get("source"), "confidence": src_out.get("confidence")}
    elif kind and current and current.get("source") == "heuristic" and src_out.get("source") not in (None, "heuristic") \
            and (kind != "chat" or current.get("value") == "chat"):
        # The model's own output list is stronger than the name; "chat" from a list never overrides stt/tts names.
        resolved["kind"] = {"value": kind, "source": src_out.get("source"), "confidence": src_out.get("confidence"),
                            "alts": [{k: v for k, v in current.items() if k != "alts"}]}


def _summary_from(resolved: Dict[str, Dict[str, Any]], effective: Optional[Dict[str, Any]] = None,
                  reference: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    def flag(key: str) -> Optional[bool]:
        value = _value(resolved, key)
        return value if isinstance(value, bool) else None

    effective = effective or {}
    reference = reference or {}
    vision = resolved.get("vision") or {}
    # Round 24: the vision badge follows this API. A catalogue may say "no" (cautious), but a catalogue
    # "yes" without evidence from the provider/probe/user stays unknown — it never switches vision on.
    eff_vision = effective.get("vision")
    if eff_vision is not None:
        vision_value, vision = eff_vision["value"], eff_vision
    elif flag("vision") is False:
        vision_value = False
    else:
        vision_value, vision = None, {}
    summary = {
        "kind": _value(resolved, "kind") or "chat",
        "vision": vision_value, "vision_source": vision.get("source"), "vision_confidence": vision.get("confidence"),
        "vision_ref": (reference.get("vision") or {}).get("value"),
        "effective": {key: item["value"] for key, item in effective.items() if item is not None},
        "reference": {key: item["value"] for key, item in reference.items() if item is not None},
        "tools": flag("tools"), "reasoning": flag("reasoning"), "structured": flag("structured_output"),
        "web": flag("web_search"), "audio": flag("audio_input"), "video": flag("video_input"), "file": flag("file_input"),
        "image_out": flag("image_output"), "audio_out": flag("audio_output"), "free": flag("free"),
        "open": flag("open_weights"), "deprecated": flag("deprecated"),
        "context": _value(resolved, "context"), "max_output": _value(resolved, "max_output"),
        "price_in": _value(resolved, "price_input"), "price_out": _value(resolved, "price_output"),
        "quant": _value(resolved, "quantization"),
    }
    return {key: value for key, value in summary.items() if value is not None}


_summary_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
LOCAL_TTL = 20.0


def capabilities_for(public_id: str, *, entry: Any = None) -> Dict[str, Any]:
    """Compact capabilities for badges and filters (cached; cleared when a source changes)."""
    with _lock:
        _catalog.ensure()
        stamp = _catalog.stamp
    found = entry if entry is not None else _entry(public_id)
    # Round 23: the same public id can be re-pointed (a custom connection renamed/re-created with
    # another upstream model) — without the upstream in the key its old card stayed cached forever.
    upstream = f"{getattr(found, 'prefix', '')}/{getattr(found, 'provider', '')}/{getattr(found, 'model', '')}" \
        if found is not None else ""
    key = f"{public_id}|{stamp}|{upstream}"
    cached = _summary_cache.get(key)
    now = time.time()
    if cached and (cached[0] == 0 or now - cached[0] < LOCAL_TTL):
        return dict(cached[1])
    card = describe(public_id, entry=found, include_raw=False)
    summary = card["summary"]
    is_local = card.get("provider") == "local"
    if len(_summary_cache) > 20_000:
        _summary_cache.clear()
    _summary_cache[key] = (now if is_local else 0, summary)
    return dict(summary)


def all_capabilities() -> Dict[str, Dict[str, Any]]:
    try:
        from .state import state
        entries = state.catalog_models()
    except Exception:
        entries = []
    return {entry.id: capabilities_for(entry.id, entry=entry) for entry in entries}


def vision_for(public_id: str, *, entry: Any = None) -> Optional[bool]:
    """Can this catalogue model look at images? ``None`` — unknown (callers then try and learn from the error).

    This is the function Quick Ask, the MemuAI window and Telegram should ask. It follows the probe, the local
    projector, the provider's own row and the catalogues; a provider whose adapter cannot pass pictures is ``False``.
    """
    summary = capabilities_for(public_id, entry=entry)
    return summary.get("vision")


def has_vision(model_id: str, *, entry: Any = None) -> Optional[bool]:
    """Round 24 — for routing (DFY, vision filters): can this model see images **through this connection**?

    ``True`` only with evidence from this API: a manual override, a passed probe, a local mmproj projector or
    the provider's own model row. ``False`` when one of them (or an adapter that cannot pass images) says no.
    ``None`` — unknown: OpenRouter/Models.dev knowledge alone never counts (see ``reference_capabilities``)."""
    summary = capabilities_for(model_id, entry=entry)
    value = (summary.get("effective") or {}).get("vision")
    return value if isinstance(value, bool) else None


def effective_capabilities(model_id: str, *, entry: Any = None) -> Dict[str, Any]:
    """``{vision, tools, reasoning, context, max_output, structured_output}`` known from this API (missing = unknown)."""
    return dict(capabilities_for(model_id, entry=entry).get("effective") or {})


def reference_capabilities(model_id: str, *, entry: Any = None) -> Dict[str, Any]:
    """The same keys "according to OpenRouter / Models.dev" — for display only."""
    return dict(capabilities_for(model_id, entry=entry).get("reference") or {})


def adapter_vision(public_id: str, prefix: str, provider: str, model: str, *, entry: Any = None) -> bool:
    """The ``caps.vision`` value for MemuAI/Quick Ask: may the window attach a picture for this model?

    * a probe the user ran decides;
    * a local model sees only with an mmproj projector;
    * other providers must have an adapter that passes images; then the strongest evidence (provider row,
      exact/canonical catalogue match, curated family list) may say "no"; with no evidence — yes, as before.
    """
    probe = probe_result(public_id)
    if probe and probe.get("vision") is not None:
        return bool(probe["vision"])
    if provider == "local":
        local = _local_info(model)
        return bool(local and (local.get("settings") or {}).get("mmproj"))
    if provider not in IMAGE_ADAPTERS:
        return False
    summary = capabilities_for(public_id, entry=entry)
    if summary.get("vision") is False and summary.get("vision_confidence") in ("verified", "high", "medium"):
        return False
    return True
