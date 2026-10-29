"""Round 38: server data of built-in module panels (``contributes.panels[].data`` → ``Memu.panel.data()``).

A panel runs in the module sandbox without network; what it cannot read through its own bridge (another store, the
public model catalogue, a compact digest of a big cache) comes from here. Every source is read-only, bounded and
fast: it reads files the module already wrote (characters store, monitor events, the Benchmarks cache) and, for
prices, the public OpenRouter catalogue cached for six hours (no network → no prices, never an error).
The AI commands of the same modules (app/module_integration_handlers.py) reuse these digests.
"""
from __future__ import annotations

import datetime as _dt
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .module_integration import data_source
from .model_listing import find_spec, listing_spec

_THUMB_LIMIT = 400_000

def _thumb(value: Any) -> str:
    value = str(value or "")
    return value if value.startswith("data:image/") and len(value) <= _THUMB_LIMIT else ""

def _store(root, key: str, default: Any) -> Any:
    from .characters_api import store_get
    try:
        return store_get(root, "characters", key, default)
    except (OSError, ValueError, KeyError):
        return default

def characters_digest(root, limit: int = 5) -> Dict[str, Any]:
    """``{recent: [...], day: {...}|None, total}`` — the characters with the latest chats and a «character of the day»."""
    index = [r for r in (_store(root, "index", []) or []) if isinstance(r, dict) and r.get("id")]
    def row(r: Dict[str, Any]) -> Dict[str, Any]:
        chats = _store(root, f"chatlist/{r['id']}", []) or []
        last = max((c for c in chats if isinstance(c, dict)), key=lambda c: c.get("updated") or 0, default={})
        return {"id": str(r["id"]), "name": str(r.get("name") or "—")[:80], "thumb": _thumb(r.get("thumb")),
                "tags": [str(t)[:24] for t in (r.get("tags") or [])[:3]], "fav": bool(r.get("fav")),
                "updated": +(last.get("updated") or r.get("updated") or 0), "chats": int(r.get("chats") or len(chats) or 0),
                "preview": re.sub(r"\s+", " ", str(last.get("preview") or ""))[:140], "messages": int(last.get("count") or 0)}
    ordered = sorted(index, key=lambda r: -(r.get("updated") or 0))
    recent = [row(r) for r in ordered[:max(1, min(8, limit))]]
    day = None
    if index:

        pool = [r for r in index if r.get("fav")] or index
        pick = sorted(pool, key=lambda r: str(r["id"]))[_dt.date.today().toordinal() % len(pool)]
        day = next((x for x in recent if x["id"] == str(pick["id"])), None) or row(pick)
    return {"recent": recent, "day": day, "total": len(index)}

@data_source("characters.panel")
def _characters_panel(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    return characters_digest(ctx["root"], int(args.get("limit") or 5))

_CATALOG: Dict[str, Any] = {"at": 0.0, "specs": {}, "lock": threading.Lock()}
_CATALOG_TTL = 6 * 3600

def _fetch_openrouter() -> Dict[str, Dict[str, Any]]:
    import httpx
    response = httpx.get("https://openrouter.ai/api/v1/models", timeout=8)
    response.raise_for_status()
    specs = {}
    payload = response.json()
    for item in (payload.get("data") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        specs[str(item["id"]).lower()] = listing_spec(item)
    return specs

def catalog() -> Dict[str, Dict[str, Any]]:
    """The public OpenRouter catalogue (context, $ per 1M tokens), cached; {} when it cannot be read."""
    with _CATALOG["lock"]:
        if _CATALOG["specs"] and time.time() - _CATALOG["at"] < _CATALOG_TTL:
            return _CATALOG["specs"]
        if time.time() - _CATALOG["at"] < 300:
            return _CATALOG["specs"]
        _CATALOG["at"] = time.time()
        try:
            _CATALOG["specs"] = _fetch_openrouter() or _CATALOG["specs"]
        except Exception:  # noqa: BLE001 - offline / blocked: the panel shows models without prices
            pass
        return _CATALOG["specs"]

def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower().removeprefix("models/").split("/")[-1].split(":")[0])

def _monitor(module_id: str) -> Dict[str, Any]:
    from . import module_monitor
    try:
        return module_monitor.events(0, module_id)
    except Exception:  # noqa: BLE001 - a broken store never breaks a panel
        return {"events": []}

def _monitor_status(module_id: str) -> Dict[str, Any]:
    from . import module_monitor
    try:
        return module_monitor.status(module_id) or {}
    except Exception:  # noqa: BLE001
        return {}

def api_models_digest(limit: int = 8, *, prices: bool = True) -> Dict[str, Any]:
    """New models from the «New API models» monitor: ``{models: [{name, key, source, family, at, context, input, output}],
    enabled, sources, checked}`` — newest first, one row per model."""
    data = _monitor("api-models")
    models: List[Dict[str, Any]] = []
    seen = set()
    for event in sorted(data.get("events") or [], key=lambda e: -int(e.get("id") or 0)):
        title = str(event.get("title") or "")
        source = title.split(" · ", 1)[1] if " · " in title else title
        entries = [(item["name"], item["key"]) for item in event.get("models", [])
                   if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("key"), str)] if isinstance(event.get("models"), list) else []
        if not entries:
            entries = [(found[1], found[2]) for line in str(event.get("text") or "").splitlines()
                       if (found := re.match(r"^\+\s+(.+)\s+·\s+(\S.*)$", line.strip()))]
        for name, identity in entries:
            if (source, identity) in seen:
                continue
            seen.add((source, identity))
            key = identity.strip()
            bare = key.removeprefix("models/")
            name = name.strip()
            models.append({"name": name.split(": ", 1)[1] if ": " in name else name, "key": key, "source": source,
                           "family": bare.split("/", 1)[0] if "/" in bare else "", "at": float(event.get("at") or 0),
                           "_event_id": int(event.get("id") or 0),
                           "seen": int(event.get("id") or 0) <= int(data.get("seen") or 0)})
    snapshots = {}
    for event in data.get("events") or []:
        for model in event.get("models", []) if isinstance(event.get("models"), list) else []:
            if isinstance(model, dict) and isinstance(model.get("spec"), dict):
                snapshots[(int(event.get("id") or 0), model.get("key"))] = model["spec"]
    models = models[:max(1, min(20, limit))]
    if prices and models:
        specs = catalog()
        for model in models:
            spec = find_spec(model["key"], model["name"], specs)
            if spec:
                model.update(spec)
    for model in models:
        model.update(snapshots.get((model.pop("_event_id"), model["key"]), {}))
    status = _monitor_status("api-models")
    sources = [s for s in status.get("sources") or [] if isinstance(s, dict)]
    return {"models": models, "enabled": bool(status.get("enabled")), "sources": len(sources),
            "checked": max([float(s.get("last_success") or 0) for s in sources] or [0])}

@data_source("api-models.panel")
def _api_models_panel(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    return api_models_digest(int(args.get("limit") or 8))

BOARDS = [

    ("arena-text", "LMArena · Текст", "LMArena · Text", "arena:text"),
    ("arena-code", "LMArena · WebDev", "LMArena · WebDev", "arena:code/webdev"),
    ("aa", "Artificial Analysis · Интеллект", "Artificial Analysis · Intelligence", "aa"),
    ("livebench", "LiveBench", "LiveBench", "livebench"),
    ("simple", "SimpleBench", "SimpleBench", "simple"),
    ("eq", "EQ-Bench", "EQ-Bench", "eq"),
]

def _board_rows(saved: Dict[str, Any], key: str) -> Tuple[List[Dict[str, Any]], float]:
    if key.startswith("arena:"):
        arena = saved.get("cache_arena") if isinstance(saved.get("cache_arena"), dict) else {}
        slug = key.split(":", 1)[1]
        entry = arena.get(slug) or next((v for k, v in arena.items() if str(k).split("::")[0] == slug), None) or {}
        rows = [{"model": r.get("model"), "org": r.get("org") or "", "score": r.get("score")} for r in entry.get("rows") or []]
        return rows, float(entry.get("at") or 0)
    entry = saved.get("cache_" + key) if isinstance(saved.get("cache_" + key), dict) else {}
    rows = []
    for r in entry.get("rows") or []:
        if not isinstance(r, dict):
            continue
        if key == "aa":
            if r.get("deprecated"):
                continue
            score = (r.get("m") or {}).get("intelligence")
        elif key == "livebench":
            score = r.get("global")
        else:
            score = r.get("score")
        rows.append({"model": r.get("model"), "org": r.get("org") or "", "score": score})
    return rows, float(entry.get("at") or 0)

def benchmarks_digest(root, top: int = 5, board: str = "") -> Dict[str, Any]:
    """Leaders of the leaderboards the Benchmarks module already loaded (its 6-hour cache):
    ``{boards: [{id, title, title_en, at, rows: [{model, org, score}]}], changes: [...]}``."""
    from .modules_host import load_persistent
    try:
        saved = load_persistent(root, "benchmarks") or {}
    except (KeyError, ValueError, OSError):
        saved = {}
    boards = []
    for bid, title, title_en, key in BOARDS:
        if board and board != bid:
            continue
        rows, at = _board_rows(saved, key)
        rows = [r for r in rows if r["model"] and isinstance(r["score"], (int, float))]
        if not rows:
            continue
        rows.sort(key=lambda r: -float(r["score"]))
        boards.append({"id": bid, "title": title, "title_en": title_en, "at": at,
                       "rows": [{**r, "model": str(r["model"])[:80], "score": round(float(r["score"]), 1)} for r in rows[:max(1, min(10, top))]]})
    changes = []
    for event in sorted(_monitor("benchmarks").get("events") or [], key=lambda e: -int(e.get("id") or 0))[:8]:
        changes.append({"title": str(event.get("title") or "")[:120], "text": str(event.get("text") or "")[:400],
                        "at": float(event.get("at") or 0)})
    return {"boards": boards, "changes": changes}

def find_board(digest: Dict[str, Any], topic: str) -> Optional[Dict[str, Any]]:
    """«кто лидирует в бенчмарке arena / кодинге / SimpleBench» → the matching board (default: the first one)."""
    topic = str(topic or "").lower()
    words = {"arena-code": ("code", "код", "webdev", "веб", "программ"), "arena-text": ("arena", "арен", "lmarena", "текст", "text"),
             "aa": ("artificial", "aa", "интеллект", "intelligence"), "livebench": ("livebench", "live"),
             "simple": ("simple", "симпл"), "eq": ("eq", "эмоц", "emotion")}
    for board in digest.get("boards") or []:
        if topic and any(w in topic for w in words.get(board["id"], ())):
            return board
    return (digest.get("boards") or [None])[0]

@data_source("benchmarks.panel")
def _benchmarks_panel(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    return benchmarks_digest(ctx["root"], int(args.get("top") or 5), str(args.get("board") or ""))

@data_source("learn.panel")
def _learn_panel(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .learn.api import panel_digest
    return panel_digest(ctx["lang"])
