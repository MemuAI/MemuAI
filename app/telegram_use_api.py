"""Window-only local control API for Telegram Use; no auth/session material is returned."""
from __future__ import annotations

import asyncio
import io
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import telegram_use as use
from . import telegram_use_config as config

def bridge_token() -> str:
    from .storage import _json_transaction
    store = config.secrets()
    with _json_transaction(store.path):
        token = store.get("bridge_token")
        if not token:
            token = secrets.token_urlsafe(32)
            store.set("bridge_token", token)
    return token

def local_request(request: Request) -> bool:
    try:
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        if request.client and request.client.host not in ("127.0.0.1", "localhost", "::1", "testclient"):
            return False
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        origin = request.headers.get("origin", "")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc != request.headers.get("host", ""):
                return False
        return True
    except ValueError:
        return False

async def body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Запрос слишком велик / Request too large")
    import json
    out = json.loads(raw or b"{}")
    if not isinstance(out, dict):
        raise ValueError("Expected JSON object")
    return out

def register(app) -> None:
    @app.post("/api/pg/integrations/telegram-use/preview")
    async def preview(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        try:
            data = await body(request)
            text = data.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                raise ValueError("Текст: 1–100000 символов / Text must be 1–100000 characters")
            original = text
            if data.get("kind") == "caption":
                text = use.caption_text(text)
            def render():
                from .telegram_format import markdown_to_telegram_html, split_telegram_html
                parts = split_telegram_html(markdown_to_telegram_html(text))
                return parts, [use.parse_html(part)[0] for part in parts]
            parts, plain = await asyncio.to_thread(render)
            if not parts or len(parts) > 30:
                raise ValueError("Недопустимая длина сообщения / Invalid message length")
            return JSONResponse({"parts": parts, "plain": plain, "truncated": text != original},
                                headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.get("/api/pg/integrations/telegram-use")
    async def status(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        result = await asyncio.to_thread(use.start().submit, "status")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/pg/integrations/telegram-use/voice-options")
    async def voice_options(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        from .telegram_use_voice import options
        return JSONResponse(await asyncio.to_thread(options), headers={"Cache-Control": "no-store"})

    @app.get("/api/pg/integrations/telegram-use/qr.png")
    async def qr(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        result = await asyncio.to_thread(use.start().submit, "status")
        flow = result.get("flow") or {}
        if flow.get("phase") != "qr" or not flow.get("url"):
            return JSONResponse({"error": "No active QR code"}, 404)
        def render():
            import qrcode
            image = qrcode.make(flow["url"])
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue()
        return Response(await asyncio.to_thread(render), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/pg/integrations/telegram-use")
    async def action(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        try:
            data = await body(request)
            name = str(data.pop("action", "settings"))
            if name not in ("settings", "qr", "phone", "code", "password", "connect", "disconnect", "cancel"):
                raise ValueError("Unknown Telegram Use action")
            result = await asyncio.to_thread(use.start().submit, name, data)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            wait = max(0, min(86400, int(getattr(exc, "retry_after", 0) or 0)))
            return JSONResponse({"error": use.friendly(exc), "retry_after": wait}, 429 if wait else 400,
                                headers={"Cache-Control": "no-store", **({"Retry-After": str(wait)} if wait else {})})

    @app.post("/api/pg/integrations/telegram-use/tool")
    async def tool(request: Request):
        if not local_request(request):
            return JSONResponse({"error": "Local origin required"}, 403)
        supplied = request.headers.get("x-memuai-telegram-use", "")
        if not supplied or not secrets.compare_digest(supplied, bridge_token()):
            return JSONResponse({"error": "Use the standard /api/pg/tools permission flow"}, 403)
        try:
            data = await body(request)
            result = await asyncio.to_thread(use.start().submit, "tool", data, 360)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return JSONResponse({"error": use.friendly(exc)}, 400)
