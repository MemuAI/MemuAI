"""Bridge polling: fetch queued server-AI requests, execute, report back.

The server never calls the user's computer.  The local bridge polls the
server's bridge queue (authenticated, same token as sync), executes each
pending request through :class:`app.local_bridge.agent.BridgeService`
— with the user's confirmation rules — and posts the result back to the
server.  ``confirm_callback`` defaults to "no interactive approver": such
requests stay ``pending`` in the bridge until the user approves them in
Settings -> Server.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .agent import BridgeRequest, BridgeResult, request_from_dict, result_to_dict


def poll_once(client, service, *,
              confirm_callback: Optional[Callable[[BridgeRequest], bool]] = None) -> List[Dict[str, Any]]:
    """One poll cycle. Returns one outcome dict per queued request."""
    outcomes: List[Dict[str, Any]] = []
    try:
        queued = client.list_bridge_requests("pending")
    except Exception as exc:  # noqa: BLE001 - transport/auth failure surface
        return [{"ok": False, "error": f"list failed: {type(exc).__name__}"}]
    for record in queued:
        req_id = str(record.get("id") or "")
        request = request_from_dict(record)
        request.id = req_id or request.id
        result = service.execute(request, confirm_callback=confirm_callback)
        if result.status == "pending":
            outcomes.append({"ok": True, "id": req_id, "status": "pending"})
            continue
        status = "done" if result.status in ("ok", "needs_host") else \
            "denied" if result.status == "denied" else "failed"
        try:
            client.post_bridge_result(req_id, status, result_to_dict(result))
        except Exception as exc:  # noqa: BLE001 - result stays queued server-side
            outcomes.append({"ok": False, "id": req_id, "status": status,
                             "error": f"result post failed: {type(exc).__name__}"})
            continue
        outcomes.append({"ok": True, "id": req_id, "status": status})
    return outcomes
