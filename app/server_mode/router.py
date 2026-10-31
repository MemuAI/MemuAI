"""HTTP API of the MemuAI server (``/api/server/*``).

Mounted onto the existing MemuAI Proxy app by :func:`register_server_mode`
(see ``memuai_server.py``); the Proxy's own routes are untouched.  Every
endpoint requires the server token — without it, or with a wrong one, the
answer is 401 and nothing else is revealed.  HTTPS is the reverse proxy's
job (see ``docs/SERVER.md``); this code never issues certificates.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .auth import TokenStore, bearer_token_from_header
from .store import ServerStore, safe_rel
from .version import SERVER_VERSION

log = logging.getLogger("memuai.server_mode")


def create_server_router(*, data_dir: str | Path,
                         token_store: Optional[TokenStore] = None,
                         store: Optional[ServerStore] = None):
    """Build the ``/api/server`` router. Pure: no global state, testable."""
    from fastapi import APIRouter

    tokens = token_store or TokenStore(data_dir)
    storage = store or ServerStore(Path(data_dir) / "data")
    router = APIRouter()

    async def require_auth(authorization: Optional[str] = Header(default=None)):
        if not tokens.verify(bearer_token_from_header(authorization)):
            raise HTTPException(status_code=401, detail="unauthorized")
        return True

    async def authed(ok: Any = Depends(require_auth)):
        return ok

    @router.get("/api/server/health")
    async def health(_: Any = Depends(authed)):
        return {"ok": True, "server": "memuai-server", "version": SERVER_VERSION}

    @router.get("/api/server/manifest")
    async def manifest(_: Any = Depends(authed)):
        return {"ok": True, **storage.manifest()}

    # ---- files ---------------------------------------------------------
    @router.get("/api/server/files")
    async def list_files(_: Any = Depends(authed)):
        return {"ok": True, "files": storage.list_files()}

    @router.get("/api/server/files/{rel:path}")
    async def get_file(rel: str, _: Any = Depends(authed)):
        try:
            safe_rel(rel)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_path"}, 400)
        data = storage.get_file(rel)
        if data is None:
            return JSONResponse({"ok": False, "error": "not_found"}, 404)
        return Response(content=data, media_type="application/octet-stream")

    @router.put("/api/server/files/{rel:path}")
    async def put_file(rel: str, request: Request, _: Any = Depends(authed)):
        try:
            safe_rel(rel)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_path"}, 400)
        data = await request.body()
        if len(data) > 100 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "too_large"}, 413)
        meta = storage.put_file(rel, data)
        return {"ok": True, **meta}

    @router.delete("/api/server/files/{rel:path}")
    async def delete_file(rel: str, _: Any = Depends(authed)):
        try:
            safe_rel(rel)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_path"}, 400)
        return {"ok": storage.delete_file(rel)}

    # ---- bridge queue --------------------------------------------------
    # Requests from the server-side AI wait here until the local bridge
    # polls them (see app/local_bridge). The server never touches the
    # user's computer directly.
    @router.post("/api/server/bridge/requests")
    async def bridge_enqueue(request: Request, _: Any = Depends(authed)):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        record = storage.enqueue_bridge_request(payload)
        return JSONResponse({"ok": True, "request": record}, 201)

    @router.get("/api/server/bridge/requests")
    async def bridge_list(request: Request, _: Any = Depends(authed)):
        status = request.query_params.get("status") or None
        return {"ok": True, "requests": storage.list_bridge_requests(status)}

    @router.post("/api/server/bridge/requests/{req_id}/result")
    async def bridge_result(req_id: str, request: Request, _: Any = Depends(authed)):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        try:
            record = storage.set_bridge_result(req_id, str(payload.get("status", "")),
                                               payload.get("result") or {})
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_status"}, 400)
        if record is None:
            return JSONResponse({"ok": False, "error": "not_found"}, 404)
        return {"ok": True, "request": record}

    # ---- documents -----------------------------------------------------
    @router.get("/api/server/{kind}")
    async def list_docs(kind: str, _: Any = Depends(authed)):
        if kind not in ("chats", "projects"):
            return JSONResponse({"ok": False, "error": "unknown_kind"}, 404)
        return {"ok": True, "docs": storage.list_docs(kind)}

    @router.get("/api/server/{kind}/{doc_id}")
    async def get_doc(kind: str, doc_id: str, _: Any = Depends(authed)):
        if kind not in ("chats", "projects"):
            return JSONResponse({"ok": False, "error": "unknown_kind"}, 404)
        try:
            doc = storage.get_doc(kind, doc_id)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_id"}, 400)
        if doc is None:
            return JSONResponse({"ok": False, "error": "not_found"}, 404)
        return {"ok": True, "doc": doc}

    @router.put("/api/server/{kind}/{doc_id}")
    async def put_doc(kind: str, doc_id: str, request: Request, _: Any = Depends(authed)):
        if kind not in ("chats", "projects"):
            return JSONResponse({"ok": False, "error": "unknown_kind"}, 404)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "invalid_json"}, 400)
        try:
            meta = storage.put_doc(kind, doc_id, payload)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_id"}, 400)
        return {"ok": True, **meta}

    @router.delete("/api/server/{kind}/{doc_id}")
    async def delete_doc(kind: str, doc_id: str, _: Any = Depends(authed)):
        if kind not in ("chats", "projects"):
            return JSONResponse({"ok": False, "error": "unknown_kind"}, 404)
        try:
            return {"ok": storage.delete_doc(kind, doc_id)}
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_id"}, 400)

    return router


def register_server_mode(app: FastAPI, data_dir: str | Path,
                         **kwargs: Any) -> TokenStore:
    """Mount the server-mode router onto an existing app (Proxy's)."""
    tokens = kwargs.pop("token_store", None) or TokenStore(data_dir)
    # Generate the token on first start so the server is never token-less.
    tokens.get_or_create()
    app.include_router(create_server_router(data_dir=data_dir,
                                            token_store=tokens, **kwargs))
    log.info("server mode: /api/server/* mounted (data dir configured)")
    return tokens
