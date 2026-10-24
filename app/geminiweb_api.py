"""Gemini Web (раунд 37): разбор кук, пошаговая проверка и «Войти через Google в окне».

Маршруты (под паролем панели, как весь /api/admin):
  POST /api/admin/geminiweb/parse      {cookie}                         → что распознано и что не так
  POST /api/admin/geminiweb/diagnose   {account_id?, fields?, proxy?}   → шаги проверки
  GET  /api/admin/geminiweb/login                                       → {available, reason}
  POST /api/admin/geminiweb/login                                       → {flow_id}
  GET  /api/admin/geminiweb/login/{flow_id}                             → {state, cookie?, message}
Раунд 41b (API-ключ хаба, как весь /v1; медиа — по неугадываемому имени-хэшу, без ключа, как подписанная ссылка):
  GET  /v1/geminiweb/media/<sha256>.<ext>                               → картинка/видео, нарисованные Gemini
  POST /v1/images/generations {model?, prompt, n?, response_format?}    → OpenAI Images API поверх Gemini Web

Окно входа — pywebview (WebView2 на Windows). После входа куки .google.com забираются через
window.get_cookies(); на Linux без pywebview кнопка скрывается, остаётся ручной ввод.
"""
from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .admin_api import _auth, _err, _json_object

router = APIRouter(prefix="/api/admin/geminiweb")
public = APIRouter()
IMAGE_PROMPT = "Generate an image: {prompt}"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fgemini.google.com%2Fapp"
_flows: Dict[str, Dict[str, Any]] = {}
_flows_lock = threading.Lock()


def _provider():
    from .providers import get
    return get("geminiweb")


@router.post("/parse")
async def parse_cookies(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, bad = await _json_object(request)
    if bad is not None:
        return bad
    from .providers.geminiweb_cookies import parse_cookie_import
    return parse_cookie_import(body.get("cookie", "")).public()


@router.post("/diagnose")
async def diagnose(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, bad = await _json_object(request)
    if bad is not None:
        return bad
    from .state import state
    provider = _provider()
    if provider is None:
        return _err("Провайдер Gemini Web недоступен", 500)
    acc = state.get_account(str(body.get("account_id") or "")) if body.get("account_id") else None
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else None
    if acc is None or fields is not None:
        base = dict(acc.fields) if acc is not None else {}
        base.update({k: str(v) for k, v in (fields or {}).items() if isinstance(k, str)})
        target = SimpleNamespace(id=f"draft-{uuid.uuid4().hex[:8]}", provider="geminiweb", fields=base, extra={},
                                 proxy=str(body.get("proxy", acc.proxy if acc else "") or "").strip(), enabled=True)
    else:
        target = acc
    try:
        return await provider.diagnose(target, test_request=body.get("test_request", True) is not False)
    finally:
        if target is not acc:
            provider._pages.pop(target.id, None)
            provider._client_ids.pop(target.id, None)
            fresh = provider._status.pop(target.id, None)
            # r41-gemini: список моделей, прочитанный проверкой, сразу становится списком сохранённого аккаунта.
            if fresh and acc is not None and target.extra.get("gw_models"):
                provider._status[acc.id] = fresh
                acc.extra["gw_models"] = target.extra["gw_models"]
                state.save_accounts_soon(2.0)


# ------------------------------------------------------------------ вход в окне


def _webview():
    try:
        import webview  # type: ignore
    except Exception:
        return None, "pywebview недоступен (обычно на Linux без графической оболочки) — вставьте куки вручную."
    if not getattr(webview, "windows", None):
        return None, "Окно входа доступно только в настольном MemuAI — вставьте куки вручную."
    return webview, ""


@router.get("/login")
async def login_available(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    module, reason = _webview()
    return {"available": module is not None, "reason": reason}


@router.post("/login")
async def login_start(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    module, reason = _webview()
    if module is None:
        return _err(reason, 400)
    flow_id = uuid.uuid4().hex[:12]
    flow = {"state": "waiting", "cookie": "", "message": "", "started": time.time()}
    with _flows_lock:
        for old in [k for k, v in _flows.items() if time.time() - v["started"] > 1800]:
            _flows.pop(old, None)
        _flows[flow_id] = flow
    try:
        window = module.create_window("Gemini — вход в Google", LOGIN_URL, width=520, height=720)
    except Exception as exc:  # noqa: BLE001
        flow.update(state="error", message=f"Не удалось открыть окно: {exc}")
        return _err(flow["message"], 500)
    window.events.closed += lambda *_a: flow["state"] == "waiting" and flow.update(state="closed")
    threading.Thread(target=_watch, args=(window, flow), name="MemuAI Gemini login", daemon=True).start()
    return {"ok": True, "flow_id": flow_id}


def _watch(window, flow: Dict[str, Any]) -> None:
    from .providers.geminiweb_cookies import PSID, PSIDTS, cookie_header, cookies_from_webview
    deadline = time.time() + 15 * 60
    while flow["state"] == "waiting" and time.time() < deadline:
        time.sleep(2)
        try:
            url = str(window.get_current_url() or "")
            cookies = cookies_from_webview(window.get_cookies())
        except Exception:
            continue
        if cookies.get(PSID) and cookies.get(PSIDTS) and "gemini.google.com" in url:
            flow.update(state="done", cookie=cookie_header(cookies),
                        message="Вход выполнен, куки получены.")
            try:
                window.destroy()
            except Exception:
                pass
            return
    if flow["state"] == "waiting":
        flow.update(state="timeout", message="Время ожидания входа истекло.")


@router.get("/login/{flow_id}")
async def login_status(flow_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    flow = _flows.get(flow_id)
    if flow is None:
        return _err("Вход не найден", 404)
    out = {"state": flow["state"], "message": flow["message"]}
    if flow["state"] == "done":
        out["cookie"] = flow["cookie"]
        with _flows_lock:
            _flows.pop(flow_id, None)
    return out


# ------------------------------------------------------------------ медиа и Images API (раунд 41b)


@public.get("/v1/geminiweb/media/{name}")
async def media_file(name: str):
    from .providers import geminiweb_media as media
    path = media.local_path(name)
    if path is None:
        return JSONResponse(status_code=404, content={"error": {"message": "Media not found", "type": "not_found"}})
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
            "mp4": "video/mp4", "webm": "video/webm"}[name.rsplit(".", 1)[-1]]
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _image_error(ru: str, en: str, status: int = 400, code: str = "invalid_request_error") -> JSONResponse:
    from .state import api_error_text
    return JSONResponse(status_code=status, content={"error": {**api_error_text(ru, en), "type": code, "code": code}})


def _default_image_model() -> str:
    from .routing import public_id
    from .state import state
    rows = [m for m in state.enabled_models() if m.provider == "geminiweb"]
    rows.sort(key=lambda m: ("flash" not in m.model or "lite" in m.model, m.model))
    return public_id(rows[0].prefix, rows[0].model) if rows else ""


@public.post("/v1/images/generations")
async def images_generations(request: Request):
    """OpenAI Images API: Gemini Web рисует картинку (Imagen / Nano Banana) по запросу «Generate an image: …»."""
    import base64
    import re

    from .openai_api import _check_auth
    from .chat import CompletionError, chat_service
    from .providers import geminiweb_media as media
    from .routing import resolve
    deny = _check_auth(request)
    if deny:
        return deny
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _image_error("Тело запроса должно быть JSON-объектом", "The request body must be a JSON object")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _image_error("Поле 'prompt' обязательно", "'prompt' is required")
    model = str(body.get("model") or "").strip() or _default_image_model()
    resolved = resolve(model) if model else None
    if resolved is None:
        return _image_error(f"Модель «{model or '—'}» не найдена. Добавьте аккаунт Gemini Web в Proxy → Аккаунты.",
                            f"Model «{model or '—'}» not found. Add a Gemini Web account in Proxy → Accounts.", 404,
                            "model_not_found")
    if resolved.provider != "geminiweb":
        return _image_error("Этот эндпоинт рисует картинки только моделями Gemini Web (например, gemini-3.8-flash). "
                            "Другие модели — через чат MemuAI или их собственный API.",
                            "This endpoint draws images only with Gemini Web models (for example gemini-3.8-flash). "
                            "Use other models through the MemuAI chat or their own API.")
    n = body.get("n", 1)
    n = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 4 else 1
    as_b64 = body.get("response_format") == "b64_json"
    size = str(body.get("size") or "").strip()
    ask = IMAGE_PROMPT.format(prompt=prompt) + (f" (aspect/size: {size})" if size and size != "auto" else "")
    data, last_text = [], ""
    for _ in range(n):
        text = ""
        try:
            async for chunk in chat_service.stream_completion(model, [{"role": "user", "content": ask}], {}):
                for choice in chunk.get("choices") or []:
                    text += str((choice.get("delta") or {}).get("content") or "")
        except CompletionError as exc:
            return JSONResponse(status_code=exc.status, content={"error": {
                "message": exc.message_en, "message_ru": exc.message, "type": exc.err_type, "code": exc.err_type}},
                headers=exc.headers)
        last_text = text
        urls = [u for u in media.urls_in(text) if not u.endswith((".mp4", ".webm"))]
        if not urls:   # скачать куками не удалось — остались адреса Google
            urls = re.findall(r"!\[Gemini image \d+\]\((https?://[^)\s]+)\)", text)
        for url in urls:
            item: dict = {"revised_prompt": prompt}
            path = media.local_path(url)
            if as_b64 and path is not None:
                item["b64_json"] = base64.b64encode(path.read_bytes()).decode("ascii")
            else:
                item["url"] = url
            data.append(item)
    if not data:
        quote = " ".join(last_text.split())[:300] or "—"
        return _image_error(f"Gemini не нарисовал картинку. Ответ: «{quote}». Генерация изображений может быть "
                            "недоступна аккаунту/региону или исчерпан лимит.",
                            f"Gemini did not draw an image. Reply: «{quote}». Image generation may be unavailable to "
                            "the account/region or its limit is used up.", 422, "image_not_generated")
    import time
    return {"created": int(time.time()), "data": data, "model": model}


def register(app) -> None:
    app.include_router(router)
    app.include_router(public)
