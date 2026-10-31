"""Local bridge endpoints (``/api/pg/bridge/*``, localhost only).

Mounted by :mod:`app.server_mode.settings_api` onto the playground app.
These endpoints are local: they expose bridge status, the journal and the
one-click revoke — they never expose the server token and never accept
requests from the network (the playground app itself only listens on
localhost).  Confirmation of a pending request happens here, in the UI
the user is looking at.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


def register(app, *, service_factory: Callable[[], Any]) -> None:
    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @app.get("/api/pg/bridge/status")
    def bridge_status():
        service = service_factory()
        return {"ok": True, "enabled": bool(service.policy.enabled),
                "revoked": service.revoked,
                "classes": sorted(service.policy.enabled_classes),
                "confirm_mode": service.policy.confirm_mode,
                "roots": [str(p) for p in service.policy.root_paths()],
                "pending": len(service.pending()),
                "journal_entries": len(service.journal(100000))}

    @app.get("/api/pg/bridge/journal")
    def bridge_journal(limit: int = 100):
        service = service_factory()
        return {"ok": True, "entries": service.journal(max(1, min(limit, 500))),
                "pending": [{"id": r.id, "action_class": r.action_class,
                             "kind": r.kind, "target": r.target, "argv": r.argv}
                            for r in service.pending()]}

    @app.post("/api/pg/bridge/revoke")
    def bridge_revoke():
        service = service_factory()
        service.revoke()
        return {"ok": True, "revoked": True}

    @app.post("/api/pg/bridge/confirm")
    async def bridge_confirm(request: Request):
        body = await body_of(request)
        req_id = str(body.get("request_id") or body.get("id") or "")
        approve = bool(body.get("approve"))
        service = service_factory()
        result = service.confirm_pending(req_id, approve)
        if result is None:
            return JSONResponse({"ok": False, "error": "not_pending"}, 404)
        return {"ok": True, "status": result.status, "output": result.output,
                "error": result.error}
