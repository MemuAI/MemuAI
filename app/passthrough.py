"""Сквозной прокси /p/<имя>/<path> — обход CORS для кастомных API.

Любой запрос к /p/<имя-подключения>/<путь> пересылается на base_url
подключения с подставленными ключом и заголовками; ответ помечается
пермиссивными CORS-заголовками, чтобы браузерные клиенты работали напрямую.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .config import config_store
from .state import ClosingStreamingResponse, state

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
}

def _find_custom(name: str):
    name = name.strip().lower().replace(" ", "-")
    for acc in state.accounts.values():
        if acc.provider != "custom" or not acc.enabled:
            continue
        if str(acc.fields.get("name", "")).strip().lower().replace(" ", "-") == name:
            return acc
    return None

@router.options("/p/{name}/{path:path}")
async def passthrough_options(name: str, path: str) -> Response:
    return Response(status_code=204, headers=CORS_HEADERS)

@router.api_route("/p/{name}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def passthrough(name: str, path: str, request: Request):
    if not config_store.authorize_api_request(
        request.headers.get("x-hub-key"), host=request.headers.get("host", ""),
        origin=request.headers.get("origin", ""),
        fetch_site=request.headers.get("sec-fetch-site", ""),
    ):
        message, message_en = "Укажите x-hub-key: <ключ хаба>", "Set x-hub-key: <hub key>"
        if not config_store.settings.api_keys:
            message = "Создайте API-ключ перед публикацией MemuAI в локальной сети"
            message_en = "Create an API key before exposing MemuAI on the local network"
        return JSONResponse(status_code=401, content={"error": message, "error_en": message_en})
    acc = _find_custom(name)
    if acc is None:
        return JSONResponse(status_code=404, content={"error": f"Подключение «{name}» не найдено",
                                                      "error_en": f"Connection “{name}” not found"})
    from .providers import get as get_provider
    cp = get_provider("custom")
    base = cp.base_of(acc)
    if not base:
        return JSONResponse(status_code=400, content={"error": "У подключения не задан base_url",
                                                      "error_en": "The connection has no base_url"})

    from .providers import http as hub_http
    headers = cp._headers(acc, accept="*/*")
    incoming_content_type = request.headers.get("content-type")
    if incoming_content_type:

        headers["Content-Type"] = incoming_content_type

    blocked_headers = {
        "host", "content-length", "x-hub-key", "connection", "keep-alive",
        "te", "trailer", "upgrade", "origin", "referer", "authorization",
        "proxy-authorization", "cookie", "accept-encoding", "content-type",
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
        "x-real-ip",
    }
    for k, v in request.headers.items():
        lowered = k.lower()
        if lowered in blocked_headers or lowered.startswith("sec-"):
            continue
        headers.setdefault(k, v)

    body = await request.body()
    try:

        session = await hub_http.get_session(cp.proxy_for(acc),
                                             impersonate=not base.startswith("http://"), url=base)
        resp = await session.request(
            request.method, f"{base}/{path.lstrip('/')}",
            headers=headers, content=body or None, stream=True,
            params=list(request.query_params.multi_items()),
            timeout=config_store.settings.request_timeout)
    except Exception:

        return JSONResponse(status_code=502, content={"error": "Апстрим недоступен",
                                                      "error_en": "Upstream is unavailable"})

    out_headers = dict(CORS_HEADERS)
    for k, v in resp.headers.items():

        lowered = k.lower()

        if lowered in ("content-type", "cache-control", "retry-after", "retry-after-ms") \
                or lowered.startswith(("x-ratelimit-", "anthropic-ratelimit-")):
            out_headers[k] = v

    async def relay():
        try:
            async for chunk in resp.aiter_content():
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode()
        finally:
            try:
                await resp.aclose()
            except Exception:
                pass

    async def close_upstream() -> None:
        try:
            await resp.aclose()
        except Exception:
            pass

    return ClosingStreamingResponse(relay(), closer=close_upstream, status_code=resp.status_code,
                                    headers=out_headers, media_type=resp.headers.get("content-type"))
