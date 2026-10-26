"""Round 38: MemuAI Settings → «MemuNote» / «MemuFiles» edit every preference of those apps.

The single source of truth stays the app's own ``settings.json`` in its data folder
(``memunote/settings.json``, ``memufiles/settings.json``); the schema lives next to each app
(``app/memunote/settings_schema.py``, ``app/memufiles/settings_schema.py``) and the apps use it too.

Routes (playground):
  GET  /api/pg/appsettings/{app}        → schema, current values, defaults, file mtime
  POST /api/pg/appsettings/{app}        → {"changes": {...}} strictly validated by the schema; nothing is
                                          written when a value is wrong (400 + {"errors": {key: code}})
  POST /api/pg/appsettings/{app}/reset  → {"keys": [...]} (empty/absent = every visible key) back to defaults

After a write the running app is told through its local ``/api/remote`` (``instance.json``: port + token):
it pushes a ``settings`` event to its page, which re-reads and applies without a restart. Changes made in
the app itself are seen by the MemuAI page when it is opened or the window gets focus (it re-reads
``mtime``) — no polling.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Tuple

log = logging.getLogger("memuai")
APPS = ("memunote", "memufiles")

def _modules(app_id: str):
    """(schema module, settings store, desktop module) of an app — imported lazily."""
    if app_id == "memunote":
        from .memunote import desktop, settings, settings_schema
    else:
        from .memufiles import desktop, settings, settings_schema
    return settings_schema, settings, desktop

def _settings_path(app_id: str):
    if app_id == "memunote":
        from .memunote.settings import path
        return path()
    from .memufiles.settings import _path
    return _path()

def _mtime(app_id: str) -> float:
    try:
        return round(_settings_path(app_id).stat().st_mtime, 3)
    except OSError:
        return 0.0

def _visible(schema, values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in values.items() if k in schema.BY_KEY and not schema.BY_KEY[k].get("hidden")}

def snapshot(app_id: str) -> Dict[str, Any]:
    schema, store, _ = _modules(app_id)
    return {"ok": True, "app": app_id, "values": _visible(schema, store.load()),
            "defaults": _visible(schema, schema.DEFAULTS), "mtime": _mtime(app_id), "exists": _mtime(app_id) > 0}

def describe(app_id: str) -> Dict[str, Any]:
    schema, _, _ = _modules(app_id)
    return {**snapshot(app_id), "schema": schema.public()}

def notify(app_id: str) -> bool:
    """Tell a running instance to re-read its settings (in the background: never delays the reply)."""
    _, _, desktop = _modules(app_id)
    try:
        from pathlib import Path
        if not Path(desktop._instance_path()).is_file():
            return False
    except Exception:  # noqa: BLE001
        return False

    def run() -> None:
        try:
            desktop._remote("settings", {}, timeout=2.0)
        except Exception:  # noqa: BLE001 - a stale instance file
            log.debug("%s did not take the settings event", app_id)
    threading.Thread(target=run, name=f"{app_id}-settings-notify", daemon=True).start()
    return True

def write(app_id: str, changes: Any) -> Tuple[Dict[str, Any], int]:
    schema, store, _ = _modules(app_id)
    clean, errors = schema.validate(changes)
    if errors:
        return {"ok": False, "error": "invalid", "errors": errors}, 400
    if clean:
        store.save(clean)
        if app_id == "memufiles" and "open_archives" in clean and os.name == "nt":
            try:
                from .memufiles import archive_assoc
                archive_assoc.sync(bool(clean["open_archives"]))
            except Exception:  # noqa: BLE001
                log.exception("archive association failed")
    return {**snapshot(app_id), "notified": notify(app_id) if clean else False}, 200

def reset(app_id: str, keys: Any) -> Dict[str, Any]:
    schema, store, _ = _modules(app_id)
    wanted: List[str] = [k for k in (keys if isinstance(keys, list) else []) if isinstance(k, str)]
    targets = [k for k in (wanted or list(schema.DEFAULTS)) if k in schema.BY_KEY and not schema.BY_KEY[k].get("hidden")]
    if targets:
        store.save({k: schema.DEFAULTS[k] for k in targets})
    return {**snapshot(app_id), "notified": notify(app_id) if targets else False}

def register(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def unknown():
        return JSONResponse({"ok": False, "error": "unknown_app"}, 404)

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @app.get("/api/pg/appsettings/{app_id}")
    def app_settings_get(app_id: str, schema: int = 1):
        if app_id not in APPS:
            return unknown()
        return describe(app_id) if schema else snapshot(app_id)

    @app.post("/api/pg/appsettings/{app_id}")
    async def app_settings_set(app_id: str, request: Request):
        if app_id not in APPS:
            return unknown()
        body = await body_of(request)
        result, status = write(app_id, body.get("changes"))
        return result if status == 200 else JSONResponse(result, status)

    @app.post("/api/pg/appsettings/{app_id}/reset")
    async def app_settings_reset(app_id: str, request: Request):
        if app_id not in APPS:
            return unknown()
        body = await body_of(request)
        return reset(app_id, body.get("keys"))
