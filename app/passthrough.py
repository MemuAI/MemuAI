"""Сквозной прокси /p/<имя>/<path> — обход CORS для кастомных API.

Любой запрос к /p/<имя-подключения>/<путь> пересылается на base_url
подключения с подставленными ключом и заголовками; ответ помечается
пермиссивными CORS-заголовками, чтобы браузерные клиенты работали напрямую.
"""
from __future__ import annotations

import urllib.parse

from typing import Dict

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .config import config_store
from .state import ClosingStreamingResponse, state

router = APIRouter()

# F15: never `Access-Control-Allow-Origin: *` on /p/... — reflect the
# Origin only when it is a loopback origin (the local UI / dev tools); any
# other origin gets no CORS header at all, so browsers refuse to read the
# response. Non-browser clients are unaffected (they ignore CORS).
def _cors_headers(request: Request) -> Dict[str, str]:
    headers = {
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
    }
    origin = request.headers.get("origin", "")
    if origin:
        try:
            host = (urllib.parse.urlsplit(origin).hostname or "").lower()
        except ValueError:
            host = ""
        if host in ("127.0.0.1", "localhost", "::1"):
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
    return headers

# F16: caps for the passthrough proxy — the request body used to be read
# whole into memory and the upstream stream was relayed without any limit.
PASSTHROUGH_MAX_REQUEST_BYTES = 10 * 1024 * 1024
PASSTHROUGH_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
# Headers forwarded to the upstream, besides Content-Type (handled above)
# and the account credentials injected by the provider. Everything else —
# cookies, authorization, hop-by-hop and spoofing headers — is dropped.
_FORWARDABLE_HEADERS = frozenset({
    "accept", "accept-language", "user-agent", "x-requested-with",
})
_BLOCKED_X_HEADERS = frozenset({
    "x-hub-key", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    "x-real-ip",
})

def _find_custom(name: str):
    name = name.strip().lower().replace(" ", "-")
    for acc in state.accounts.values():
        if acc.provider != "custom" or not acc.enabled:
            continue
        if str(acc.fields.get("name", "")).strip().lower().replace(" ", "-") == name:
            return acc
    return None

@router.options("/p/{name}/{path:path}")
async def passthrough_options(name: str, path: str, request: Request) -> Response:
    return Response(status_code=204, headers=_cors_headers(request))

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
    # F16: `..` segments escape the account's base_url when the upstream
    # URL is joined (httpx normalises dot segments), so reject them outright.
    # The path is already percent-decoded by the router; unquote once more to
    # catch double-encoded `%252e%252e`.
    decoded_path = urllib.parse.unquote(path)
    if any(part == ".." for part in decoded_path.replace("\\", "/").split("/")):
        return JSONResponse(status_code=400, content={"error": "Недопустимый путь: «..»",
                                                      "error_en": "Invalid path: '..'"})
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

    # F16: allow-list instead of the old blocklist — only safe headers are
    # forwarded (plus custom `x-` headers except the blocked ones).
    for k, v in request.headers.items():
        lowered = k.lower()
        if lowered in _FORWARDABLE_HEADERS or (
                lowered.startswith("x-") and lowered not in _BLOCKED_X_HEADERS):
            headers.setdefault(k, v)

    # F16: read the request body with a cap instead of unbounded
    # `await request.body()` (a chunked body had no size limit at all).
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > PASSTHROUGH_MAX_REQUEST_BYTES:
            return JSONResponse(status_code=413, content={
                "error": "Тело запроса слишком большое (предел 10 МБ)",
                "error_en": "Request body too large (limit 10 MB)"})
    body = bytes(body)
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

    out_headers = _cors_headers(request)
    for k, v in resp.headers.items():

        lowered = k.lower()

        if lowered in ("content-type", "cache-control", "retry-after", "retry-after-ms") \
                or lowered.startswith(("x-ratelimit-", "anthropic-ratelimit-")):
            out_headers[k] = v

    async def relay():
        # F16: an upstream that streams forever used to be relayed forever.
        total = 0
        try:
            async for chunk in resp.aiter_content():
                data = chunk if isinstance(chunk, bytes) else str(chunk).encode()
                total += len(data)
                if total > PASSTHROUGH_MAX_RESPONSE_BYTES:
                    break
                yield data
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
