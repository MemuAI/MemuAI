"""Desktop catalogue endpoints, guarded against stale profile/source actions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from . import module_catalog as catalog, modules_host, pgdata

def _profile(expected: str = "") -> str:
    active = str(pgdata.load_profiles().get("active_id") or "default")
    if expected and str(expected) != active:
        raise catalog.CatalogError("Профиль сменился. Откройте каталог заново / Profile changed; reopen the catalogue", 409)
    return active

async def _body(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 64 * 1024:
            raise catalog.CatalogError("Запрос слишком большой / Request is too large", 413)
    try:
        value = json.loads(raw or b"{}")
    except (ValueError, TypeError, RecursionError) as exc:
        raise catalog.CatalogError("Некорректный JSON / Invalid JSON") from exc
    if not isinstance(value, dict):
        raise catalog.CatalogError("Нужен объект запроса / Expected a request object")
    return value

def register(app, *, root_fn: Callable[[], Path], fetch: Callable = catalog.download) -> None:
    prefix = "/api/pg/module-catalog"

    def failure(exc):
        return JSONResponse({"error":str(exc)}, getattr(exc, "status", 400))

    @app.get(prefix)
    async def listing(profile_id: str = "", refresh: bool = False):
        try:
            active = _profile(profile_id)
            result = await catalog.catalogue(Path(root_fn()), refresh=refresh, fetch=fetch, guard=lambda:_profile(active))
            _profile(active)
            return {**result, "profile_id":active}
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.post(prefix + "/source")
    async def source(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            result = await asyncio.to_thread(catalog.set_source, Path(root_fn()), str(body.get("source") or ""), guard=lambda:_profile(active))
            _profile(active)
            return {**result, "profile_id":active}
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.post(prefix + "/install")
    async def install(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            return await catalog.install(Path(root_fn()), str(body.get("id") or ""), str(body.get("sha256") or ""),
                str(body.get("source") or ""), replace_local=body.get("replace_local") is True,
                fetch=fetch, guard=lambda:_profile(active))
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.post(prefix + "/preview")
    async def preview(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            result = await catalog.preview(Path(root_fn()), str(body.get("id") or ""), str(body.get("sha256") or ""),
                str(body.get("source") or ""), fetch=fetch, guard=lambda:_profile(active))
            _profile(active)
            return {**result, "profile_id":active}
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.post(prefix + "/rollback")
    async def rollback(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            return await asyncio.to_thread(catalog.rollback, Path(root_fn()), str(body.get("id") or ""),
                replace_local=body.get("replace_local") is True, guard=lambda:_profile(active))
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.get(prefix + "/authoring")
    async def authoring(profile_id: str = ""):
        try:
            active = _profile(profile_id)
            rows = await asyncio.to_thread(modules_host.list_modules, Path(root_fn()))
            _profile(active)
            return {"profile_id":active, "modules":[{key:row.get(key) for key in ("id","title","title_en","version","author")}
                    for row in rows if not row.get("builtin")], "contributing_url":catalog.CONTRIBUTING}
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)

    @app.post(prefix + "/prepare")
    async def prepare(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            result = await asyncio.to_thread(catalog.contribution, Path(root_fn()), str(body.get("id") or ""), body)
            _profile(active)
            return result
        except (ValueError, KeyError, OSError) as exc:
            return failure(exc)
