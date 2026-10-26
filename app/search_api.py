"""MemuAI global search — HTTP API (Round 29, r29-search).

Routes (local MemuAI server, same trust level as ``/api/pg/chats``):

* ``GET|POST /api/search``          — run a query (``q``, ``types``, filters, ``limit``, ``lang``)
* ``GET  /api/search/preview``      — context of one result (``id``, ``q``)
* ``GET  /api/search/meta``         — types with labels, models / folders / projects for the filter UI
* ``GET  /api/search/status``       — index statistics
* ``POST /api/search/reindex``      — rebuild everything now
* ``POST /api/search/touch``        — the palette opened: refresh in the background
* ``GET|POST /api/search/recent``   — recent searches (``{q}`` adds, ``{remove}``, ``{clear: true}``)
* ``GET|POST /api/search/settings`` — ``remember_recent``, ``index_files``

The Telegram Mini App calls :func:`remote_search` / :func:`remote_preview` from its own
authenticated routes (desktop-only sources — Work files, projects, modules — are excluded).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import search_index as si

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - fastapi ships with both apps
    Request = Any  # type: ignore[misc,assignment]

log = logging.getLogger("memuai.search")

RECENT_LIMIT = 20
_STATE_LOCK = threading.RLock()
_WATCHER: Dict[str, Any] = {"thread": None, "last_use": 0.0, "stop": False}
REMOTE_TYPES_EXCLUDED = {"file", "project", "module"}
FILTER_KEYS = ("date_from", "date_to", "model", "folder", "pinned", "role", "project", "telegram")

def _state_file() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "search_state.json"

def load_state() -> Dict[str, Any]:
    try:
        raw = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return normalize_state(raw)

def normalize_state(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    recent: List[Dict[str, Any]] = []
    seen = set()
    for row in raw.get("recent") or []:
        if isinstance(row, str):
            row = {"q": row, "t": 0}
        if not isinstance(row, dict):
            continue
        q = " ".join(str(row.get("q") or "").split())[:200]
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        try:
            t = int(row.get("t") or 0)
        except (TypeError, ValueError):
            t = 0
        recent.append({"q": q, "t": t})
    return {"recent": recent[:RECENT_LIMIT],
            "remember_recent": raw.get("remember_recent", True) is not False,
            "index_files": raw.get("index_files", True) is not False}

def save_state(state: Dict[str, Any]) -> Dict[str, Any]:
    from .storage import save_json
    clean = normalize_state(state)
    with _STATE_LOCK:
        save_json(str(_state_file()), clean, backup=False)
    return clean

def add_recent(q: str) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = load_state()
        q = " ".join(str(q or "").split())[:200]
        if not q or not state["remember_recent"]:
            return state
        state["recent"] = [{"q": q, "t": int(time.time() * 1000)}] + [r for r in state["recent"] if r["q"].lower() != q.lower()]
        return save_state(state)

def _files_provider() -> Optional[si.WorkFilesProvider]:
    provider = si.INDEX.providers.get("files")
    return provider if isinstance(provider, si.WorkFilesProvider) else None

def _apply_settings(state: Dict[str, Any]) -> None:
    provider = _files_provider()
    if provider is not None and provider.enabled != state["index_files"]:
        provider.enabled = state["index_files"]
        si.INDEX.prov_checked.pop("files", None)
        si.INDEX.prov_rev.pop("files", None)

def touch() -> None:
    _WATCHER["last_use"] = time.monotonic()

def _watch() -> None:
    """Keeps the index warm: cheap sources every few seconds (a stat each), the Work files only
    while search is in use (they are walked at most every 90 s, time-boxed)."""
    time.sleep(6.0)
    try:
        _apply_settings(load_state())
        si.INDEX.refresh([n for n in si.INDEX.providers if n != "files"])
    except Exception:
        log.exception("search: initial indexing failed")
    while not _WATCHER["stop"]:
        try:
            recently_used = time.monotonic() - _WATCHER["last_use"] < 15 * 60
            names = [n for n in si.INDEX.providers if n != "files" or recently_used]
            si.INDEX.refresh(names)
        except Exception:
            log.debug("search: background refresh failed", exc_info=True)
        time.sleep(4.0)

def start_watcher() -> None:
    thread = _WATCHER.get("thread")
    if thread is not None and thread.is_alive():
        return
    thread = threading.Thread(target=_watch, name="memuai-search-index", daemon=True)
    _WATCHER["thread"] = thread
    thread.start()

def notify(provider: str = "") -> None:
    """Something changed (a chat was saved…): re-check that source on the next refresh."""
    if provider:
        si.INDEX.prov_checked.pop(provider, None)

def _types(value: Any) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip().lower()[:40] for v in value if str(v).strip()][:30]

def _filters(source: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    raw = source.get("filters") if isinstance(source.get("filters"), dict) else source
    for key in FILTER_KEYS:
        value = raw.get(key)
        if value in (None, "", [], "0", "false", False):
            continue
        if key in ("date_from", "date_to"):
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                stamp = si._parse_date(str(value), end=(key == "date_to"))
                if stamp is not None:
                    out[key] = stamp
        elif key in ("pinned", "telegram"):
            out[key] = True
        elif key == "role":
            out[key] = "user" if str(value).lower() in ("user", "me") else "assistant"
        else:
            out[key] = str(value)[:120]
    return out

def run_search(body: Dict[str, Any], *, remote: bool = False, refresh: bool = True) -> Dict[str, Any]:
    q = str(body.get("q") or body.get("query") or "")[:400]
    types = _types(body.get("types"))
    if remote:
        allowed = [t for t in (types or [t for t in si.TYPE_ORDER]) if t not in REMOTE_TYPES_EXCLUDED]
        types = allowed
    try:
        limit = int(body.get("limit") or 60)
    except (TypeError, ValueError):
        limit = 60
    lang = "en" if str(body.get("lang") or "") == "en" else ("ru" if body.get("lang") else "")
    touch()
    result = si.search(q, types=types or None, filters=_filters(body), limit=limit,
                       per_group=int(body.get("per_group") or 3), lang=lang, remote=remote, refresh=refresh)
    if remote:
        for item in result["items"]:
            item["meta"].pop("path", None)
    _localize_memory_kinds(result.get("items") or [], lang)
    _localize_prompts(result.get("items") or [], lang)
    return result

def _localize_prompts(items: List[Dict[str, Any]], lang: str) -> None:
    """Built-in prompts and the built-in set carry Russian names: the Russian UI showed «Original MemuAI prompts»."""
    if lang == "en":
        return
    for item in items:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if item.get("type") != "prompt":
            continue
        if meta.get("name_ru"):
            item["title"] = meta["name_ru"]
        if meta.get("set_ru") and item.get("subtitle") == meta.get("set"):
            item["subtitle"] = meta["set_ru"]

def _localize_memory_kinds(items: List[Dict[str, Any]], lang: str) -> None:
    """Memory rows carry the raw category key in the subtitle («health · chat»): show its label instead."""
    try:
        from .memory_v2 import CATEGORY_LABELS
    except Exception:
        return
    index = 1 if lang == "en" else 0
    for item in items:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        kind = str(meta.get("kind") or "")
        if item.get("type") != "memory" or kind not in CATEGORY_LABELS:
            continue
        subtitle = str(item.get("subtitle") or "")
        if subtitle == kind or subtitle.startswith(kind + " · "):
            item["subtitle"] = CATEGORY_LABELS[kind][index] + subtitle[len(kind):]

REMOTE_PROVIDERS_SKIPPED = {"files", "projects", "modules"}
_REMOTE_LOCK = threading.Lock()

def _remote_refresh() -> None:
    """The Mini App lives in MemuAI Proxy, where :func:`register` never runs: install the built-in
    providers on first use and bring only the remote-visible ones up to date."""
    with _REMOTE_LOCK:
        si.install_default_providers()
    si.INDEX.refresh([n for n in si.INDEX.providers if n not in REMOTE_PROVIDERS_SKIPPED])

def remote_search(q: str, *, types: Any = None, filters: Optional[Dict[str, Any]] = None, limit: int = 30,
                  lang: str = "", per_group: int = 3) -> Dict[str, Any]:
    """For the Telegram Mini App (call from an authenticated route)."""
    _remote_refresh()
    body = {"q": q, "types": types or [], "filters": dict(filters or {}), "limit": limit, "lang": lang,
            "per_group": per_group}
    return run_search(body, remote=True, refresh=False)

def remote_preview(result_id: str, q: str = "") -> Optional[Dict[str, Any]]:
    if str(result_id).startswith(("file:", "project:", "module:")):
        return None
    _remote_refresh()
    return si.preview(str(result_id), q)

def meta(lang: str = "ru") -> Dict[str, Any]:
    """What the filter UI offers: types (with counts), models, folders, projects."""
    si.INDEX.refresh([n for n in si.INDEX.providers if n != "files"])
    with si.INDEX.lock:
        groups: Dict[str, set] = {}
        models: Dict[str, int] = {}
        for doc in si.INDEX.docs.values():
            groups.setdefault(doc.type, set()).add(doc.group or doc.key)
            model = doc.meta.get("model")
            if model:
                models[model] = models.get(model, 0) + 1
        counts = {t: len(keys) for t, keys in groups.items()}
    folders: List[Dict[str, str]] = []
    projects: List[Dict[str, str]] = []
    try:
        from .pgdata import load_preferences, load_workspaces
        prefs = load_preferences()
        for key, mode in (("chat_folders", "chat"), ("work_folders", "work"), ("code_folders", "code")):
            for row in prefs.get(key) or []:
                if isinstance(row, dict) and row.get("id"):
                    folders.append({"id": str(row["id"]), "name": str(row.get("name") or ""), "mode": mode})
        projects = [{"id": p["id"], "name": p["name"]} for p in load_workspaces().get("items", [])]
    except Exception:
        pass
    types = [{"type": t, "label": list(si.TYPE_LABELS.get(t, (t, t))), "count": counts.get(t, 0)}
             for t in si.TYPE_ORDER if t in counts or t in ("chat", "work", "code")]
    return {"types": types, "models": [m for m, _n in sorted(models.items(), key=lambda r: -r[1])][:60],
            "folders": folders, "projects": projects}

def resolve_file(result_id: str) -> Optional[Path]:
    """The absolute path of a Work file result, only when it is still inside its saved project."""
    if not result_id.startswith("file:"):
        return None
    with si.INDEX.lock:
        doc_id = si.INDEX.key_to_id.get(result_id)
        doc = si.INDEX.docs.get(doc_id) if doc_id else None
    if doc is None:
        return None
    from .pgdata import load_workspaces
    project = next((p for p in load_workspaces().get("items", []) if str(p["id"]) == doc.open.get("project_id")), None)
    if project is None:
        return None
    try:
        root = Path(project["path"]).resolve()
        target = (root / str(doc.open.get("path") or "")).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target if target.exists() else None

def reveal_path(target: Path) -> None:
    import os
    import subprocess
    import sys
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target.parent)])

def register(app, *, watch: bool = True) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    si.install_default_providers()
    _apply_settings(load_state())
    if watch:
        start_watcher()
    app.state.search_index = si.INDEX

    @app.get("/api/search")
    async def search_get(request: Request):
        params = dict(request.query_params)
        return await asyncio.to_thread(run_search, params)

    @app.post("/api/search")
    async def search_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON object expected"}, 400)
        return await asyncio.to_thread(run_search, body)

    @app.get("/api/search/preview")
    async def search_preview(id: str = "", q: str = ""):
        data = await asyncio.to_thread(si.preview, id[:600], q[:400])
        if data is None:
            return JSONResponse({"error": "not found"}, 404)
        return data

    @app.get("/api/search/meta")
    async def search_meta(lang: str = "ru"):
        return await asyncio.to_thread(meta, lang)

    @app.get("/api/search/status")
    async def search_status():
        return si.INDEX.summary()

    @app.post("/api/search/reindex")
    async def search_reindex():
        def run() -> Dict[str, Any]:
            started = time.perf_counter()
            touch()
            changed = si.INDEX.refresh(force=True)
            return {"ok": True, "units": changed, "took_ms": round((time.perf_counter() - started) * 1000, 1),
                    **si.INDEX.summary()}
        return await asyncio.to_thread(run)

    @app.post("/api/search/touch")
    async def search_touch():
        touch()
        threading.Thread(target=lambda: si.INDEX.refresh(), daemon=True).start()
        return {"ok": True}

    @app.post("/api/search/reveal")
    async def search_reveal(request: Request):
        """Show a Work file of a result in Explorer (the path is resolved here, inside its project)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        target = await asyncio.to_thread(resolve_file, str((body or {}).get("id") or ""))
        if target is None:
            return JSONResponse({"error": "not found"}, 404)
        await asyncio.to_thread(reveal_path, target)
        return {"ok": True}

    @app.get("/api/search/recent")
    async def search_recent():
        state = await asyncio.to_thread(load_state)
        return {"items": state["recent"] if state["remember_recent"] else [], "remember_recent": state["remember_recent"]}

    @app.post("/api/search/recent")
    async def search_recent_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}

        def run() -> Dict[str, Any]:
            with _STATE_LOCK:
                state = load_state()
                if body.get("clear"):
                    state["recent"] = []
                    state = save_state(state)
                elif body.get("remove"):
                    target = str(body.get("remove")).lower()
                    state["recent"] = [r for r in state["recent"] if r["q"].lower() != target]
                    state = save_state(state)
                elif body.get("q"):
                    state = add_recent(str(body.get("q")))
            return {"items": state["recent"] if state["remember_recent"] else [], "remember_recent": state["remember_recent"]}
        return await asyncio.to_thread(run)

    @app.get("/api/search/settings")
    async def search_settings():
        state = await asyncio.to_thread(load_state)
        return {"remember_recent": state["remember_recent"], "index_files": state["index_files"]}

    @app.post("/api/search/settings")
    async def search_settings_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}

        def run() -> Dict[str, Any]:
            with _STATE_LOCK:
                state = load_state()
                for key in ("remember_recent", "index_files"):
                    if key in body:
                        state[key] = bool(body[key])
                if not state["remember_recent"]:
                    state["recent"] = []
                state = save_state(state)
            _apply_settings(state)
            if not state["index_files"]:
                si.INDEX.refresh(["files"], force=True)
            return {"remember_recent": state["remember_recent"], "index_files": state["index_files"]}
        return await asyncio.to_thread(run)
