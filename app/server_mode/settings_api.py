"""Client-side API for Settings -> Server (``/api/pg/server-mode/*``).

Registered once from ``playground_api.create_app`` (one line, next to the
sync settings registration).  Non-secret settings live in
``<data>/memuai/server_mode.json``; the token lives only in the protected
store (:mod:`app.server_mode.secure_store`) and is never returned whole —
only ``has_token`` and a mask.  Nothing here changes chat / planning /
module behaviour: with mode ``"local"`` (the default) no server call is
ever made.

Also mounts the local-bridge endpoints (``/api/pg/bridge/*``) from
:mod:`app.local_bridge.router` on the same singleton bridge service, whose
policy is rebuilt from these settings on every request.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from fastapi import Request
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("memuai.server_mode.client_api")

MODES = ("local", "server", "hybrid")
CONFIRM_MODES = ("always", "once")
BRIDGE_CLASSES = ("read", "write", "execute", "browser")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "server_url": "",
    "mode": "local",
    "sync_projects": False,
    "sync_files": False,
    "sync_chats": False,
    "auto_sync": False,
    "bridge_enabled": False,
    "bridge_read": True,
    "bridge_write": False,
    "bridge_execute": False,
    "bridge_browser": False,
    "bridge_confirm": "always",
    "bridge_roots": [],
}

_LAST_CHECK: Dict[str, Any] = {"ok": False, "checked": False}


def _data_root(data_dir: Optional[str | Path]) -> Path:
    if data_dir:
        return Path(data_dir)
    from ..config import DATA_DIR
    return Path(DATA_DIR)


def settings_path(data_dir: Optional[str | Path] = None) -> Path:
    return _data_root(data_dir) / "memuai" / "server_mode.json"


def secret_store_path(data_dir: Optional[str | Path] = None) -> Path:
    return _data_root(data_dir) / "memuai" / "server_token.enc"


def sync_root(data_dir: Optional[str | Path] = None) -> Path:
    return _data_root(data_dir) / "memuai" / "server_files"


def _load_json(path: Path, default: Any) -> Any:
    try:
        from ..storage import load_json_with_recovery
        return load_json_with_recovery(path, default,
                                       validator=lambda v: isinstance(v, dict))
    except Exception:
        try:
            data = json.loads(path.read_text("utf-8"))
            return data if isinstance(data, dict) else default
        except Exception:
            return default


def _save_json(path: Path, data: Any) -> None:
    try:
        from ..storage import save_json
        save_json(path, data, backup=False)
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)


def load_settings(data_dir: Optional[str | Path] = None) -> Dict[str, Any]:
    raw = _load_json(settings_path(data_dir), {})
    out = dict(DEFAULT_SETTINGS)
    for key, value in raw.items():
        if key in DEFAULT_SETTINGS:
            out[key] = value
    out["mode"] = out["mode"] if out["mode"] in MODES else "local"
    out["bridge_confirm"] = out["bridge_confirm"] if out["bridge_confirm"] in CONFIRM_MODES else "always"
    out["bridge_roots"] = [str(r) for r in (out.get("bridge_roots") or []) if isinstance(r, str)]
    return out


def validate_settings(changes: Any) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Whitelist-validate a settings patch. Returns (clean, errors)."""
    clean: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    if not isinstance(changes, dict):
        return {}, {"_": "invalid"}
    if "server_url" in changes:
        url = str(changes.get("server_url") or "").strip().rstrip("/")
        if url:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname \
                    or parsed.username or parsed.password:
                errors["server_url"] = "invalid_url"
            else:
                clean["server_url"] = url
        else:
            clean["server_url"] = ""
    if "mode" in changes:
        if changes.get("mode") in MODES:
            clean["mode"] = changes["mode"]
        else:
            errors["mode"] = "invalid_mode"
    for key in ("sync_projects", "sync_files", "sync_chats", "auto_sync",
                "bridge_enabled", "bridge_read", "bridge_write",
                "bridge_execute", "bridge_browser"):
        if key in changes:
            clean[key] = bool(changes.get(key))
    if "bridge_confirm" in changes:
        if changes.get("bridge_confirm") in CONFIRM_MODES:
            clean["bridge_confirm"] = changes["bridge_confirm"]
        else:
            errors["bridge_confirm"] = "invalid_mode"
    if "bridge_roots" in changes:
        roots = changes.get("bridge_roots")
        if isinstance(roots, list):
            valid = []
            for root in roots:
                if isinstance(root, str) and root.strip() and Path(root.strip()).is_absolute():
                    valid.append(root.strip())
            clean["bridge_roots"] = valid[:8]
        else:
            errors["bridge_roots"] = "invalid_roots"
    return clean, errors


def save_settings(changes: Any, data_dir: Optional[str | Path] = None) -> Dict[str, Any]:
    clean, errors = validate_settings(changes)
    if errors:
        raise ValueError(errors)
    current = load_settings(data_dir)
    current.update(clean)
    _save_json(settings_path(data_dir), current)
    return current


# ---------------------------------------------------------------- bridge --
_bridge_service = None


def bridge_service(data_dir: Optional[str | Path] = None):
    """Process singleton bridge service; policy follows current settings."""
    global _bridge_service
    from ..local_bridge.agent import BridgeService
    from ..local_bridge.policy import BridgePolicy
    settings = load_settings(data_dir)
    policy = BridgePolicy(
        roots=settings["bridge_roots"],
        enabled_classes={cls for cls in BRIDGE_CLASSES
                         if settings.get("bridge_" + cls)},
        confirm_mode=settings["bridge_confirm"],
        enabled=bool(settings["bridge_enabled"]),
    )
    if _bridge_service is None:
        _bridge_service = BridgeService(policy)
    else:
        _bridge_service.policy = policy
    return _bridge_service


def _client_for(settings: Dict[str, Any], data_dir: Optional[str | Path],
                queue_path: Optional[Path] = None):
    from .client import HttpxTransport, ServerClient
    from .secure_store import ClientSecretStore
    token = ClientSecretStore(secret_store_path(data_dir)).load_token()
    if not token:
        return None, None
    transport = HttpxTransport(settings["server_url"], token)
    return ServerClient(transport, queue_path=queue_path), token


def register(app, *, data_dir: Optional[str | Path] = None,
             bridge=None) -> None:
    from .secure_store import ClientSecretStore, SecureStoreError
    from ..local_bridge import router as bridge_router

    def secrets() -> ClientSecretStore:
        return ClientSecretStore(secret_store_path(data_dir))

    def public_snapshot() -> Dict[str, Any]:
        store = secrets()
        return {
            "settings": load_settings(data_dir),
            "has_token": store.has_token(),
            "token_mask": store.mask(),
            "last_check": dict(_LAST_CHECK),
            "bridge": {
                "revoked": bridge_service(data_dir).revoked
                if bridge is None else bridge.revoked,
            },
        }

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @app.get("/api/pg/server-mode/settings")
    def get_settings():
        return {"ok": True, **public_snapshot()}

    @app.post("/api/pg/server-mode/settings")
    async def set_settings(request: Request):
        body = await body_of(request)
        try:
            updated = save_settings(body.get("settings"), data_dir)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": "invalid", "errors": dict(exc.args[0])}, 400)
        store = secrets()
        token = body.get("token")
        if body.get("clear_token") is True:
            store.clear()
        elif isinstance(token, str) and token.strip():
            try:
                store.save_token(token.strip())
            except SecureStoreError as exc:
                return JSONResponse({"ok": False, "error": "secure_store", "detail": str(exc)}, 500)
        bridge_service(data_dir)  # refresh bridge policy from new settings
        return {"ok": True, **public_snapshot(), "settings": updated}

    @app.post("/api/pg/server-mode/check")
    async def check_connection():
        from .client import ServerAuthError, ServerConnectionError
        settings = load_settings(data_dir)
        if not settings["server_url"]:
            return {"ok": False, "error": "no_url"}
        client, _ = _client_for(settings, data_dir)
        if client is None:
            return {"ok": False, "error": "no_token"}
        try:
            result = client.check_connection()
        except ServerAuthError:
            result = {"ok": False, "error": "unauthorized"}
        except ServerConnectionError:
            result = {"ok": False, "error": "unreachable"}
        except Exception as exc:  # noqa: BLE001 - transport failure surface
            log.warning("server check failed: %s", type(exc).__name__)
            result = {"ok": False, "error": "unreachable"}
        _LAST_CHECK.clear()
        _LAST_CHECK.update(result)
        _LAST_CHECK["checked"] = True
        return {"ok": bool(result.get("ok")), **result}

    @app.post("/api/pg/server-mode/sync")
    async def run_sync(request: Request):
        from .client import ServerAuthError, ServerConnectionError
        from .store import ServerStore
        settings = load_settings(data_dir)
        if settings["mode"] == "local" or not settings["sync_files"]:
            return JSONResponse({"ok": False, "error": "disabled"}, 409)
        if not settings["server_url"]:
            return JSONResponse({"ok": False, "error": "no_url"}, 400)
        body = await body_of(request)
        root = sync_root(data_dir)
        local = ServerStore(root)
        queue_path = root / "offline_queue.json"
        client, _ = _client_for(settings, data_dir, queue_path=queue_path)
        if client is None:
            return JSONResponse({"ok": False, "error": "no_token"}, 400)
        base_path = root / "base_manifest.json"
        base = _load_json(base_path, {})
        client._local_files = lambda: {row["rel"]: row for row in local.list_files()}
        client._local_file_reader = lambda name: local.get_file(name)
        client._local_file_writer = lambda name, data: local.put_file(name, data)
        try:
            result = client.sync_files(base_manifest=base,
                                       resolutions=body.get("resolutions") or {},
                                       queue_on_failure=True)
        except ServerAuthError:
            return JSONResponse({"ok": False, "error": "unauthorized"}, 401)
        except ServerConnectionError:
            return JSONResponse({"ok": False, "error": "unreachable"}, 502)
        if result.get("ok"):
            try:
                _save_json(base_path, client.manifest())
            except Exception:
                pass
        return {"ok": True, **result, "queued": client.queued_count()}

    @app.post("/api/pg/server-mode/bridge/poll")
    def bridge_poll():
        from .client import ServerAuthError, ServerConnectionError
        from ..local_bridge.poller import poll_once
        settings = load_settings(data_dir)
        if not settings["bridge_enabled"]:
            return JSONResponse({"ok": False, "error": "bridge_disabled"}, 409)
        client, _ = _client_for(settings, data_dir)
        if client is None:
            return JSONResponse({"ok": False, "error": "no_token"}, 400)
        try:
            outcomes = poll_once(client, bridge_service(data_dir))
        except ServerAuthError:
            return JSONResponse({"ok": False, "error": "unauthorized"}, 401)
        except ServerConnectionError:
            return JSONResponse({"ok": False, "error": "unreachable"}, 502)
        return {"ok": True, "outcomes": outcomes}

    # Bridge: status / journal / revoke / confirm (local only, localhost).
    bridge_router.register(app, service_factory=lambda: bridge_service(data_dir))
