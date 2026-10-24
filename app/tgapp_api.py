"""HTTP side of the Telegram Mini App: static files and a whitelisted JSON API.

Mounted on its own listener (``tg_webapp._make_server``), never on the Proxy or
desktop API. Everything under ``/tgapp/api/`` except ``auth`` requires the
session token issued for validated Telegram ``initData`` of the paired user.
Every operation is listed here explicitly: there is no generic pass-through to
MemuAI internals, secrets (API keys, the bot token, SMTP password) are never
returned, and preferences are written only through ``pgdata.save_preferences``
after per-key validation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import tg_webapp
from . import telegram_focus
from . import tgapp_chat as tchat

log = logging.getLogger("memuai.tgapp")

API = "/tgapp/api"
MAX_BODY = 32 * 1024 * 1024
SMALL_BODY = 1024 * 1024
STREAM_SEGMENT = 22.0

# Sign-ins: a generous cap on every attempt (signature checks are cheap) and a strict one on FAILED
# attempts — only failures can be guessing. (The failure counter used to be counted but never checked,
# while successful sign-ins of the app itself used up the strict cap.)
auth_limiter = tg_webapp.RateLimiter(60, 60)
auth_failures = tg_webapp.RateLimiter(12, 60)
api_limiter = tg_webapp.RateLimiter(600, 60)
pin_limiter = tg_webapp.RateLimiter(6, 300)
send_limiter = tg_webapp.RateLimiter(40, 60)


class ApiError(Exception):
    """``message`` is Russian; ``en`` is its English twin — the response picks the Mini App's language
    (Round 27: «Память этого профиля меняется на компьютере» and the rest reached an English Mini App)."""

    def __init__(self, status: int, code: str, message: str = "", en: str = "") -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.en = status, code, message or code, en

    def payload(self, prefs: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        message = self.message
        if self.en and self.en != message:
            try:
                message = _t(prefs if prefs is not None else _prefs(), message, self.en)
            except Exception:       # unreadable preferences: the Russian text is still an answer
                pass
        return {"error": message, "code": self.code}


def _lang(prefs: Dict[str, Any]) -> str:
    return "en" if prefs.get("language") == "en" else "ru"


def _t(prefs: Dict[str, Any], ru: str, en: str) -> str:
    return en if _lang(prefs) == "en" else ru


def _prefs() -> Dict[str, Any]:
    from .pgdata import load_preferences
    return load_preferences()


async def _read_json(request: Request, limit: int = SMALL_BODY) -> Dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "too_large", "Запрос слишком большой", "The request is too large")
    chunks: List[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise ApiError(413, "too_large", "Запрос слишком большой", "The request is too large")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        raise ApiError(400, "bad_json", "Некорректный JSON", "Invalid JSON") from None
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "Ожидается JSON-объект", "A JSON object is expected")
    return data


def _client_key(request: Request) -> str:
    # Behind the tunnel every request comes from 127.0.0.1; Cloudflare adds the visitor address.
    return (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?"))[:64]


# --------------------------------------------------------------------------- settings schema

def _accent_options() -> List[str]:
    from . import themes
    return list(themes.ACCENTS.keys())


def _variant_options(mode: str) -> List[str]:
    from . import themes
    return [""] + [row["id"] for row in themes.VARIANTS[mode]] + [
        "custom:" + row["id"] for row in themes.list_custom() if row.get("base") == mode]


def _design_options() -> List[str]:
    from . import themes
    return [row["id"] for row in themes.DESIGNS]


# key, type, options/range; labels live in the front end (app/tgapp/app.js, SETTINGS).
SETTINGS: Dict[str, Dict[str, Any]] = {
    "language": {"type": "enum", "options": ["ru", "en"]},
    "theme": {"type": "enum", "options": ["dark", "light"]},
    "accent_color": {"type": "enum", "options": _accent_options},
    "surface_dark": {"type": "enum", "options": lambda: _variant_options("dark")},
    "surface_light": {"type": "enum", "options": lambda: _variant_options("light")},
    "ui_design": {"type": "enum", "options": _design_options},
    "app_font": {"type": "enum", "options": ["serif", "sans", "mono", "system"]},
    "font_scale": {"type": "int", "min": 85, "max": 125},
    "compact_messages": {"type": "bool"},
    "reduce_motion": {"type": "bool"},
    "default_model": {"type": "model"},
    "web_search": {"type": "bool"},
    "thinking": {"type": "bool"},
    "default_effort": {"type": "enum", "options": ["auto", "off", "minimal", "low", "medium", "high", "xhigh", "max"]},
    "prompt_caching": {"type": "bool"},
    "visualizations_enabled": {"type": "bool"},
    "chat_titles": {"type": "enum", "options": ["ai", "off"]},
    "chat_titles_update": {"type": "bool"},
    "ai_personality": {"type": "enum", "options": ["friendly", "pragmatic"]},
    "custom_system_prompt": {"type": "text", "max": 12000},
    "profile_name": {"type": "text", "max": 80},
    "profile_bio": {"type": "text", "max": 1000},
    "web_search_mode": {"type": "enum", "options": ["auto", "builtin", "custom"]},
    "custom_search_max_results": {"type": "int", "min": 1, "max": 10},
    "image_send_mode": {"type": "enum", "options": ["ask", "direct", "fallback"]},
    "vision_fallback_model": {"type": "model", "empty": True},
    "context_optimization": {"type": "bool"},
    "context_compression_threshold": {"type": "int", "min": 50, "max": 98},
    "context_recent_messages": {"type": "int", "min": 6, "max": 80},
    "confirm_deletes": {"type": "bool"},
    "notify_windows": {"type": "bool"},
    "notify_sound": {"type": "bool"},
    "notify_sound_style": {"type": "enum", "options": ["gentle", "crystal", "pop", "warm"]},
    "notify_sound_volume": {"type": "int", "min": 0, "max": 100},
    "telegram_notify_mode": {"type": "enum", "options": ["off", "silent", "sound"]},
    "telegram_model": {"type": "model", "empty": True},
    "telegram_language": {"type": "enum", "options": ["ru", "en"]},
    "telegram_history_messages": {"type": "int", "min": 10, "max": 100},
    "telegram_voice_review": {"type": "bool"},
    "research_model": {"type": "model", "empty": True},
    "research_max_queries": {"type": "int", "min": 4, "max": 150},
    "research_max_rounds": {"type": "int", "min": 1, "max": 8},
    "research_max_sources": {"type": "int", "min": 4, "max": 100},
    "agent_count": {"type": "int", "min": 1, "max": 4},
    "updates_enabled": {"type": "bool"},
}


def validate_settings(values: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(values, dict) or not values:
        raise ApiError(400, "no_values", "Нет изменений", "Nothing to change")
    clean: Dict[str, Any] = {}
    models: Optional[List[str]] = None
    for key, value in values.items():
        spec = SETTINGS.get(key)
        if spec is None:
            raise ApiError(400, "unknown_setting", f"Эта настройка меняется только на компьютере: {key}",
                           f"This setting can only be changed on the computer: {key}")
        kind = spec["type"]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            clean[key] = value
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            if not spec["min"] <= int(value) <= spec["max"]:
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            clean[key] = int(value)
        elif kind == "enum":
            options = spec["options"]() if callable(spec["options"]) else spec["options"]
            if value not in options:
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            clean[key] = value
        elif kind == "text":
            if not isinstance(value, str) or len(value) > spec["max"]:
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            clean[key] = value
        elif kind == "model":
            if models is None:
                models = [row["id"] for row in tchat.chat_models(prefs)]
            if not isinstance(value, str) or (value and value not in models) or (not value and not spec.get("empty")):
                raise ApiError(400, "bad_value", f"Недопустимое значение: {key}", f"Invalid value: {key}")
            clean[key] = value
    if "telegram_notify_mode" in clean:
        clean["telegram_notify"] = clean["telegram_notify_mode"] != "off"
    if "default_model" in clean:
        hidden = [item for item in prefs.get("hidden_models") or [] if item != clean["default_model"]]
        clean["hidden_models"] = hidden
    return clean


def public_settings(prefs: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: prefs.get(key) for key in SETTINGS}
    try:
        # The computer shows the active profile's name/bio: show the same here.
        from .pgdata import load_profiles
        state = load_profiles()
        active = next((item for item in state.get("items") or []
                       if str(item.get("id")) == str(state.get("active_id"))), None)
        if active is not None:
            out["profile_name"] = str(active.get("name") or out.get("profile_name") or "")[:80]
            out["profile_bio"] = str(active.get("bio") or out.get("profile_bio") or "")[:1000]
    except Exception:
        pass
    return out


def _sync_active_profile(clean: Dict[str, Any]) -> None:
    from .pgdata import load_profiles, save_profiles
    state = load_profiles()
    active = str(state.get("active_id") or "default")
    items = []
    for item in state.get("items") or []:
        row = {key: value for key, value in item.items() if key not in ("has_pin", "pin")}
        if str(item.get("id")) == active:
            if "profile_name" in clean:
                row["name"] = str(clean["profile_name"])[:80]
            if "profile_bio" in clean:
                row["bio"] = str(clean["profile_bio"])[:1000]
        items.append(row)
    save_profiles({"items": items, "active_id": active})


# --------------------------------------------------------------------------- appearance

def appearance(prefs: Dict[str, Any]) -> Dict[str, Any]:
    from . import themes
    mode = themes.normalize_mode(prefs.get("theme") or "dark")
    tokens = {}
    for which in ("dark", "light"):
        try:
            tokens[which] = {key: value for key, value in themes.resolve_tokens(
                which, prefs.get(f"surface_{which}") or "", prefs.get("accent_color") or "terracotta").items()
                if not key.startswith("--tok-")}
        except Exception:
            tokens[which] = {}
    return {"theme": mode, "accent": themes.normalize_accent(prefs.get("accent_color")),
            "design": themes.normalize_design(prefs.get("ui_design")),
            "font": prefs.get("app_font") or "serif", "font_scale": prefs.get("font_scale") or 100,
            "compact": bool(prefs.get("compact_messages")), "reduce_motion": bool(prefs.get("reduce_motion")),
            "tokens": tokens}


def theme_catalog(lang: str) -> Dict[str, Any]:
    from . import themes
    name = "en" if lang == "en" else "ru"
    return {
        "accents": [{"id": key, "name": row[name], "dark": row["dark"], "light": row["light"]}
                    for key, row in themes.ACCENTS.items()],
        # The built-in list starts with the default variant (id "") — no extra «MemuAI» entry: with one the
        # default palette showed twice and both cards were highlighted.
        "variants": {mode: [{"id": row["id"], "name": row[name],
                         "bg": themes.variant_tokens(mode, row["id"]).get("--bg", ""),
                         "panel": themes.variant_tokens(mode, row["id"]).get("--panel", "")}
                        for row in themes.VARIANTS[mode]]
                     + [{"id": "custom:" + row["id"], "name": row.get("name") or row["id"]}
                        for row in themes.list_custom() if row.get("base") == mode]
                     for mode in ("dark", "light")},
        "designs": [{"id": row["id"], "name": row[name], "note": row["note_" + name]} for row in themes.DESIGNS],
    }


# --------------------------------------------------------------------------- chats

_MD_FENCE = re.compile(r"```[^\n]*\n?([\s\S]*?)(?:```|$)")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_LINE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d{1,3}[.)]\s+)", re.M)
_MD_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M)
_MD_EMPH = re.compile(r"(\*\*|~~|\*|`)(?=\S)([^*`~\n]+?)(?<=\S)\1|(?<!\w)(__|_)(?=\S)([^_\n]+?)(?<=\S)\3(?!\w)")


def plain_snippet(text: str, limit: int = 140) -> str:
    """A chat-list preview: Markdown markup removed, code blocks shown as their first line."""
    value = str(text or "")
    value = _MD_FENCE.sub(lambda m: " " + (m.group(1).strip().splitlines() or [""])[0] + " ", value)
    value = _MD_LINK.sub(lambda m: m.group(1), value)
    value = _MD_TABLE_RULE.sub(" ", value)
    value = _MD_LINE.sub("", value)
    for _ in range(2):
        value = _MD_EMPH.sub(lambda m: m.group(2) if m.group(1) else m.group(4), value)
    value = value.replace("|", " ").replace("`", "")
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _chat_summary(chat: Dict[str, Any], memes: Dict[str, str]) -> Dict[str, Any]:
    messages = [row for row in chat.get("messages") or [] if isinstance(row, dict)]
    last = next((row for row in reversed(messages) if row.get("role") in ("user", "assistant")), None)
    snippet = ""
    if last is not None:
        snippet = plain_snippet(tchat.clean_answer(str(last.get("content") or "")))
    return {"id": str(chat.get("id")), "title": str(chat.get("title") or "")[:120],
            "updated": float(chat.get("updated") or 0), "pinned": bool(chat.get("pinned")),
            "unread": bool(chat.get("unread")), "folder_id": str(chat.get("folder_id") or ""),
            "mode": str(chat.get("mode") or "chat"), "count": len(messages), "snippet": snippet,
            "last_role": str(last.get("role")) if last else "", "meme_id": memes.get(str(chat.get("id")), ""),
            "live": bool(chat.get("live")) or any(row.get("via") == "live" for row in messages[-20:]),
            "telegram": bool(chat.get("telegram")) or str(chat.get("id", "")).startswith(("tg-", "tga-")),
            "model": str(chat.get("model") or "")}


def _message_out(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    out = {"i": index, "role": row.get("role"), "content": str(row.get("content") or ""),
           "createdAt": row.get("createdAt"), "model": row.get("model") or "",
           "via": row.get("via") or ""}
    if row.get("role") == "assistant":
        out["content"] = tchat.clean_answer(out["content"])
        if row.get("reasoning"):
            out["reasoning"] = str(row.get("reasoning"))[-8000:]
        if row.get("sources"):
            out["sources"] = row.get("sources")[:12]
        if row.get("stopped"):
            out["stopped"] = True
        if isinstance(row.get("askForms"), list):   # Round 35 (askuser): answered forms of this answer
            out["askForms"] = [f for f in row["askForms"][:4] if isinstance(f, dict) and f.get("status") != "pending"]
    attachments = []
    for item in row.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data") or item.get("url") or "")
        is_img = bool(item.get("isImg")) or data.startswith("data:image/") or item.get("kind") == "image"
        attachments.append({"name": str(item.get("name") or "file")[:120], "isImg": is_img,
                            "data": data if is_img and len(data) < 3_000_000 else "",
                            "size": item.get("size") or 0})
    if attachments:
        out["attachments"] = attachments
    if row.get("role") == "assistant" and (row.get("media") or row.get("images") or row.get("videos")):
        from . import telegram_media_gen   # Round 29 (r29c-media): generated pictures and videos
        out.update(telegram_media_gen.app_media_out(row))
    return out


def _find_chat(profile_id: str, chat_id: str) -> Dict[str, Any]:
    from .pgdata import load_chats
    chat = next((row for row in load_chats(profile_id) if str(row.get("id")) == str(chat_id)), None)
    if chat is None:
        raise ApiError(404, "not_found", "Чат не найден", "Chat not found")
    return chat


# --------------------------------------------------------------------------- app

def create_tgapp() -> FastAPI:
    app = FastAPI(title="MemuAI Mini App", docs_url=None, redoc_url=None, openapi_url=None)
    routes: Dict[str, Callable[..., Awaitable[Any]]] = {}

    def op(name: str):
        def register(func):
            routes[name] = func
            return func
        return register

    @app.middleware("http")
    async def headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except ApiError as exc:
            response = JSONResponse(exc.payload(), exc.status)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/tgapp/m/"):
            return response                            # module documents carry their own policy
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; media-src 'self' data: blob:; font-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")
        return response

    # ------------------------------------------------------------------ auth
    def session_of(request: Request) -> Dict[str, Any]:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        config = tg_webapp.load_config()
        data = tg_webapp.verify_token(token, config["secret"]) if token else None
        prefs = _prefs()
        if data is None:
            raise ApiError(401, "unauthorized", _t(prefs, "Сессия истекла — откройте приложение заново",
                                                   "The session has expired — open the app again"))
        paired = str(data.get("u") or "")
        if not config["enabled"] or not tg_webapp.is_authorized_user(paired, prefs):
            raise ApiError(403, "forbidden", _t(prefs, "Доступ закрыт", "Access denied"))
        if not api_limiter.hit("u:" + paired):
            raise ApiError(429, "rate_limited", _t(prefs, "Слишком много запросов — подождите минуту",
                                                   "Too many requests — wait a minute"))
        return {"user": paired, "prefs": prefs, "config": config, "token": data}

    async def auth(request: Request):
        key = _client_key(request)
        prefs = _prefs()
        if auth_failures.full("f:" + key) or not auth_limiter.hit("a:" + key):
            raise ApiError(429, "rate_limited", _t(prefs, "Слишком много попыток входа — подождите минуту",
                                                   "Too many sign-in attempts — wait a minute"))
        body = await _read_json(request, 16 * 1024)
        config = tg_webapp.load_config()
        if not config["enabled"]:
            raise ApiError(403, "disabled", _t(prefs, "Mini App выключен в настройках MemuAI",
                                               "The Mini App is turned off in MemuAI settings"))
        token = tg_webapp.bot_token(prefs)
        try:
            data = tg_webapp.validate_init_data(str(body.get("init_data") or ""), token,
                                                max_age=config["max_age_hours"] * 3600)
        except tg_webapp.InitDataError as exc:
            auth_failures.hit("f:" + key)
            raise ApiError(401, exc.code, _t(prefs, str(exc), tg_webapp.INIT_DATA_ERRORS_EN.get(exc.code, str(exc)))) from None
        user = data["user"]
        paired = str(user.get("id") or "")
        if not tg_webapp.is_authorized_user(paired, prefs):
            auth_failures.hit("f:" + key)
            log.warning("tgapp: rejected Telegram user (not paired)")
            raise ApiError(403, "not_paired", _t(prefs, "Этот аккаунт Telegram не привязан к MemuAI",
                                                     "This Telegram account is not paired with MemuAI"))
        session, expires = tg_webapp.issue_token(paired, config["secret"])
        return {"token": session, "expires": expires,
                "user": {"id": user.get("id"), "first_name": str(user.get("first_name") or "")[:80],
                         "username": str(user.get("username") or "")[:80],
                         "photo_url": str(user.get("photo_url") or "")[:500]},
                "lang": _lang(prefs)}

    public_routes: Dict[str, Callable[..., Awaitable[Any]]] = {}

    async def public(name: str, request: Request):
        """Round 29: browser sign-in steps that run before there is a session (rate-limited per address)."""
        key = _client_key(request)
        prefs = _prefs()
        if auth_failures.full("f:" + key) or not auth_limiter.hit("a:" + key):
            raise ApiError(429, "rate_limited", _t(prefs, "Слишком много попыток входа — подождите минуту",
                                                   "Too many sign-in attempts — wait a minute"))
        body = await _read_json(request, 16 * 1024)
        try:
            return await public_routes[name](request, body)
        except ApiError as exc:
            if exc.status in (401, 404):
                auth_failures.hit("f:" + key)
            raise

    @app.post(API + "/{name}")
    async def dispatch(name: str, request: Request):
        try:
            if name == "auth":
                return JSONResponse(await auth(request))
            if name in public_routes:
                return JSONResponse(await public(name, request))
            handler = routes.get(name)
            if handler is None:
                raise ApiError(404, "unknown", "Неизвестная операция", "Unknown operation")
            ctx = session_of(request)
            limit = MAX_BODY if name in ("chats.send", "modules.request", "modules.stream", "voice.transcribe") else SMALL_BODY
            body = await _read_json(request, limit)
            result = await handler(ctx, body)
            if isinstance(result, Response):
                return result
            return JSONResponse(result)
        except ApiError as exc:
            return JSONResponse(exc.payload(), exc.status)
        except Exception as exc:
            log.exception("tgapp %s failed", name)
            return JSONResponse({"error": f"{type(exc).__name__}: {str(exc)[:300]}", "code": "internal"}, 500)

    # ------------------------------------------------------------------ bootstrap
    def profile_id_of(ctx: Dict[str, Any], body: Dict[str, Any]) -> str:
        """The Mini App works in the desktop's active profile unless a PIN-checked switch was made."""
        active, items = tchat.profiles()
        wanted = str(body.get("profile_id") or "")
        token_profile = str((ctx.get("token") or {}).get("x") or "")
        known = {str(item.get("id")) for item in items}
        if wanted and wanted in known:
            item = next(row for row in items if str(row.get("id")) == wanted)
            if wanted != active and item.get("has_pin") and token_profile != wanted:
                raise ApiError(403, "pin_required", _t(ctx["prefs"], "Профиль защищён PIN-кодом",
                                                      "This profile is protected by a PIN"))
            return wanted
        return active if active in known else (next(iter(known)) if known else "default")

    @op("bootstrap")
    async def bootstrap(ctx, body):
        prefs = ctx["prefs"]
        lang = _lang(prefs)
        active, items = tchat.profiles()
        profile_id = profile_id_of(ctx, body)
        from .version import APP_VERSION
        return {
            "lang": lang, "version": APP_VERSION,
            "appearance": appearance(prefs), "themes": theme_catalog(lang),
            "settings": public_settings(prefs),
            "models": tchat.chat_models(prefs),
            "folders": prefs.get("chat_folders") or [],
            "memes": [_meme_out(row) for row in prefs.get("memes") or [] if isinstance(row, dict)],
            "profiles": [{"id": str(item.get("id")), "name": str(item.get("name") or "")[:80],
                          "has_pin": bool(item.get("has_pin")), "active": str(item.get("id")) == active}
                         for item in items],
            "profile_id": profile_id,
            "features": {"code": bool(ctx["config"].get("code_control")), "modules": True, "live": False,
                         # Round 29: code host (slash commands, new sessions, files), voice, browser sign-in.
                         "code_host": bool(ctx["config"].get("code_control")), "files": True, "search": True,
                         "schedules": True, "prompts": True, "edit": True,
                         "web_login": ctx["config"].get("web_login") is not False,
                         "voice": await asyncio.to_thread(_voice_status, prefs, lang)},
            "memory": tchat.profile_memory(tchat.profile_by_id(profile_id)),
        }

    def _voice_status(prefs: Dict[str, Any], lang: str) -> Dict[str, Any]:
        try:
            from . import telegram_voice
            return telegram_voice.status(prefs, lang=lang)
        except Exception as exc:     # the Mini App still opens; the microphone button explains
            return {"available": False, "reason": "error", "message": str(exc)[:200]}

    def _meme_out(row: Dict[str, Any]) -> Dict[str, Any]:
        keys = ("id", "name", "description", "instructions", "model", "web_search", "thinking", "strict",
                "avatar", "caps", "created", "updated", "ai_created")
        return {key: row.get(key) for key in keys if key in row}

    @op("profiles.switch")
    async def profiles_switch(ctx, body):
        from .pgdata import verify_profile_pin
        active, items = tchat.profiles()
        wanted = str(body.get("profile_id") or "")
        item = next((row for row in items if str(row.get("id")) == wanted), None)
        if item is None:
            raise ApiError(404, "not_found", "Профиль не найден", "Profile not found")
        if item.get("has_pin") and wanted != active:
            if not pin_limiter.hit("p:" + ctx["user"]):
                raise ApiError(429, "rate_limited", _t(ctx["prefs"], "Слишком много попыток PIN — подождите 5 минут",
                                                      "Too many PIN attempts — wait 5 minutes"))
            pin = str(body.get("pin") or "")
            if not pin or not await asyncio.to_thread(verify_profile_pin, wanted, pin):
                raise ApiError(403, "bad_pin", _t(ctx["prefs"], "Неверный PIN-код", "Wrong PIN"))
        token, expires = tg_webapp.issue_token(ctx["user"], ctx["config"]["secret"], extra=wanted)
        return {"ok": True, "profile_id": wanted, "token": token, "expires": expires}

    # ------------------------------------------------------------------ chats
    @op("chats.list")
    async def chats_list(ctx, body):
        from .pgdata import chats_revision, load_chats
        profile_id = profile_id_of(ctx, body)
        rev = chats_revision(profile_id)
        if body.get("rev") and body.get("rev") == rev:
            return {"unchanged": True, "rev": rev}
        prefs = ctx["prefs"]
        memes = {str(k): str(v) for k, v in (prefs.get("chat_memes") or {}).items()}
        rows = await asyncio.to_thread(load_chats, profile_id)
        running = {job.meta.get("chat_id") for job in tchat.jobs.jobs.values()
                   if job.kind == "chat" and not job.done and not job.meta.get("settled")}
        out = []
        for chat in rows:
            if str(chat.get("mode") or "chat") != "chat" or chat.get("temp"):
                continue
            item = _chat_summary(chat, memes)
            item["running"] = item["id"] in running
            out.append(item)
        return {"chats": out, "rev": rev, "folders": prefs.get("chat_folders") or [],
                "collapsed": prefs.get("collapsed_folders") or []}

    @op("chats.get")
    async def chats_get(ctx, body):
        profile_id = profile_id_of(ctx, body)
        chat = _find_chat(profile_id, str(body.get("id") or ""))
        messages = [row for row in chat.get("messages") or [] if isinstance(row, dict)]
        visible = [(index, row) for index, row in enumerate(messages)
                   if row.get("role") in ("user", "assistant") and not row.get("internal")]
        limit = max(10, min(400, int(body.get("limit") or 120)))
        memes = {str(k): str(v) for k, v in (ctx["prefs"].get("chat_memes") or {}).items()}
        job = tchat.jobs.active_for_chat(str(chat.get("id")))
        if chat.get("unread"):
            await asyncio.to_thread(_patch_chat, profile_id, str(chat["id"]), {"unread": False}, False)
        # Round 35 (branches): only the active branch is sent; messages with other versions carry «versions».
        from . import chat_tree
        versions = chat_tree.info(chat)
        out_rows = []
        for index, row in visible[-limit:]:
            item = _message_out(row, index)
            if index in versions:
                item["versions"] = {"key": versions[index]["key"], "cur": versions[index]["cur"], "n": versions[index]["n"]}
            out_rows.append(item)
        return {"chat": {**_chat_summary(chat, memes), "titleManual": bool(chat.get("titleManual")),
                         "other_versions": chat_tree.stats(chat)["versions"]},
                "messages": out_rows,
                "total": len(visible), "job": job.id if job and job.owner == ctx["user"] else ""}

    def _patch_chat(profile_id: str, chat_id: str, patch: Dict[str, Any], touch: bool = True) -> Dict[str, Any]:
        from .pgdata import load_chats, upsert_chat
        chat = next((row for row in load_chats(profile_id) if str(row.get("id")) == chat_id), None)
        if chat is None:
            raise ApiError(404, "not_found", "Чат не найден", "Chat not found")
        row = dict(chat)
        row.update(patch)
        if touch:
            row["updated"] = tchat.now_ms()
        upsert_chat(row, profile_id)
        return row

    @op("chats.update")
    async def chats_update(ctx, body):
        profile_id = profile_id_of(ctx, body)
        chat_id = str(body.get("id") or "")
        patch: Dict[str, Any] = {}
        if "title" in body:
            title = " ".join(str(body.get("title") or "").split())[:120]
            if not title:
                raise ApiError(400, "bad_value", "Пустое название", "The name is empty")
            patch.update({"title": title, "titleManual": True})
        if "pinned" in body:
            patch["pinned"] = bool(body.get("pinned"))
        if "unread" in body:
            patch["unread"] = bool(body.get("unread"))
        if "folder_id" in body:
            folder = str(body.get("folder_id") or "")
            if folder and folder not in {str(f.get("id")) for f in ctx["prefs"].get("chat_folders") or []}:
                raise ApiError(400, "bad_value", "Папка не найдена", "Folder not found")
            patch["folder_id"] = folder
        if not patch:
            raise ApiError(400, "no_values", "Нет изменений", "Nothing to change")
        row = await asyncio.to_thread(_patch_chat, profile_id, chat_id, patch, "title" in patch or "folder_id" in patch)
        memes = {str(k): str(v) for k, v in (ctx["prefs"].get("chat_memes") or {}).items()}
        return {"chat": _chat_summary(row, memes)}

    @op("chats.delete")
    async def chats_delete(ctx, body):
        from .pgdata import merge_chats_partial, load_preferences, save_preferences
        profile_id = profile_id_of(ctx, body)
        chat_id = str(body.get("id") or "")
        _find_chat(profile_id, chat_id)
        job = tchat.jobs.active_for_chat(chat_id)
        if job is not None:
            await tchat.cancel_job(job)
        await asyncio.to_thread(merge_chats_partial, [], [chat_id], profile_id)
        prefs = load_preferences()
        if chat_id in (prefs.get("chat_memes") or {}):
            mapping = dict(prefs.get("chat_memes") or {})
            mapping.pop(chat_id, None)
            save_preferences({"chat_memes": mapping})
        return {"ok": True}

    @op("chats.send")
    async def chats_send(ctx, body):
        if not send_limiter.hit("s:" + ctx["user"]):
            raise ApiError(429, "rate_limited", _t(ctx["prefs"], "Слишком часто — подождите",
                                                   "Too often — please wait"))
        prefs = ctx["prefs"]
        # «Время и фокус» applies here too (telegram_focus.py): quiet hours ask once per window.
        focus_lang = {"telegram_language": _lang(prefs)}
        window = telegram_focus.quiet_pending("app:" + ctx["user"], prefs)
        if window:
            if not body.get("quiet_ack"):
                raise ApiError(409, "quiet_hours", _t(
                    prefs, f"Сейчас ваши тихие часы — до {window['until']} вы решили оставить время для другого. Отправить всё равно?",
                    f"These are your quiet hours — you set aside the time until {window['until']} for something else. Send anyway?"))
            telegram_focus.acknowledge("app:" + ctx["user"], window["ends_at"])
        try:   # Round 29 (r29c-media): fresh image/video model lists for the prompt and /image, /video
            from . import telegram_media_gen
            await telegram_media_gen.media_models()
        except Exception:
            pass
        try:
            job = tchat.start_send(
                owner=ctx["user"], profile_id=profile_id_of(ctx, body), chat_id=str(body.get("chat_id") or ""),
                text=str(body.get("text") or ""), attachments=body.get("attachments") or [],
                model=str(body.get("model") or ""),
                web_search=body.get("web_search") if isinstance(body.get("web_search"), bool) else None,
                thinking=body.get("thinking") if isinstance(body.get("thinking"), bool) else None,
                temp=bool(body.get("temp")), meme_id=str(body.get("meme_id") or ""),
                regenerate=bool(body.get("regenerate")), temp_history=body.get("history"), lang=_lang(prefs),
                effort=str(body.get("effort") or "")[:12])
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from None
        out = {"job": job.id, "chat_id": job.meta["chat_id"], "model": job.meta["model"]}
        minutes = await telegram_focus.break_minutes(prefs)
        if minutes is not None:
            out["break"] = {"text": _t(prefs, f"Сегодня вы уже {telegram_focus.duration(minutes, focus_lang)} с MemuAI. Встаньте, разомнитесь — разговор подождёт.",
                                       f"You've spent {telegram_focus.duration(minutes, focus_lang)} with MemuAI today. Stand up, stretch — the chat will wait.")}
        return out

    @op("media.file")
    async def media_file(ctx, body):
        """Round 29 (r29c-media): a generated video / picture of this profile as a data URL (<video> has no auth)."""
        from . import telegram_media_gen
        try:
            return await telegram_media_gen.app_media_file(str(body.get("url") or ""), profile_id_of(ctx, body))
        except Exception as exc:
            raise ApiError(404, "not_found", "Файл не найден: " + str(exc)[:200], "File not found: " + str(exc)[:200]) from None

    @op("focus.break")
    async def focus_break(ctx, body):
        kind = str(body.get("action") or "")
        if kind not in ("snooze", "dismiss", "break"):
            raise ApiError(400, "bad_value", "Неизвестное действие (snooze | dismiss | break)",
                           "Unknown action (snooze | dismiss | break)")
        await telegram_focus.break_action(kind)
        return {"ok": True}

    # ------------------------------------------------------------------ jobs
    def job_of(ctx, body) -> tchat.Job:
        job = tchat.jobs.get(str(body.get("job") or ""), ctx["user"])
        if job is None:
            raise ApiError(404, "no_job", "Задача не найдена или уже удалена", "The task was not found or is already deleted")
        return job

    @op("jobs.poll")
    async def jobs_poll(ctx, body):
        job = job_of(ctx, body)
        offset = max(0, int(body.get("from") or 0))
        events = await job.wait(offset, min(10.0, float(body.get("wait") or 0)))
        return {"events": events, "next": offset + len(events), "done": job.done}

    @op("jobs.stream")
    async def jobs_stream(ctx, body):
        """Newline-delimited JSON over a POST (quick tunnels buffer GET streams); ends after a segment."""
        job = job_of(ctx, body)
        offset = max(0, int(body.get("from") or 0))

        async def produce():
            nonlocal offset
            deadline = time.monotonic() + STREAM_SEGMENT
            yield (json.dumps({"type": "hello", "from": offset}) + "\n").encode()
            while time.monotonic() < deadline:
                events = await job.wait(offset, min(5.0, max(0.1, deadline - time.monotonic())))
                for event in events:
                    yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
                offset += len(events)
                if job.done and offset >= len(job.events):
                    return
                if not events:
                    yield b'{"type":"ping"}\n'
        return StreamingResponse(produce(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @op("jobs.stop")
    async def jobs_stop(ctx, body):
        job = job_of(ctx, body)
        return {"stopped": await tchat.cancel_job(job)}

    @op("jobs.answer")
    async def jobs_answer(ctx, body):
        """Round 35 (askuser): the answer to the job's ask_user form ({job, id, answers} or {job, id, skip: true})."""
        from . import ask_user
        job = job_of(ctx, body)
        form_id = str(body.get("id") or "")
        if not form_id or job.meta.get("ask_id") != form_id:
            raise ApiError(409, "closed", "Этот вопрос уже закрыт", "This question is closed")
        answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
        record, errors = await asyncio.to_thread(ask_user.STORE.settle, form_id,
                                                 "skipped" if body.get("skip") else "answered", answers)
        if record is None:
            raise ApiError(404, "not_found", "Вопрос не найден", "Question not found")
        return {"ok": not errors, "errors": errors, "status": record.get("status")}

    # ------------------------------------------------------------------ folders
    @op("folders.save")
    async def folders_save(ctx, body):
        from .pgdata import save_preferences
        rows = body.get("folders")
        if not isinstance(rows, list) or len(rows) > 50:
            raise ApiError(400, "bad_value", "Некорректный список папок", "Invalid folder list")
        current = {str(f.get("id")): f for f in ctx["prefs"].get("chat_folders") or [] if isinstance(f, dict)}
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            folder_id = str(row.get("id") or "")
            name = " ".join(str(row.get("name") or "").split())[:50]
            if not name:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", folder_id):
                folder_id = "f-" + uuid.uuid4().hex[:10]
            clean.append({**current.get(folder_id, {}), "id": folder_id, "name": name})
        removed = set(current) - {row["id"] for row in clean}
        saved = await asyncio.to_thread(save_preferences, {"chat_folders": clean})
        if removed:
            from .pgdata import load_chats, merge_chats_partial
            profile_id = profile_id_of(ctx, body)
            changed = []
            for chat in load_chats(profile_id):
                if str(chat.get("folder_id") or "") in removed:
                    row = dict(chat)
                    row["folder_id"] = ""
                    row["updated"] = max(float(row.get("updated") or 0), tchat.now_ms())
                    changed.append(row)
            if changed:
                await asyncio.to_thread(merge_chats_partial, changed, [], profile_id)
        return {"folders": saved.get("chat_folders") or []}

    # ------------------------------------------------------------------ memes
    @op("memes.save")
    async def memes_save(ctx, body):
        from .pgdata import load_preferences, save_preferences
        raw = body.get("meme")
        if not isinstance(raw, dict):
            raise ApiError(400, "bad_value", "Нет мемеса", "No agent")
        prefs = load_preferences()
        rows = [row for row in prefs.get("memes") or [] if isinstance(row, dict)]
        existing = next((row for row in rows if row.get("id") == raw.get("id")), None)
        meme = tchat.normalize_meme(raw, existing)
        if not meme["name"]:
            raise ApiError(400, "bad_value", _t(prefs, "Дайте мемесу имя", "Give the agent a name"))
        if meme.get("model") and meme["model"] not in {row["id"] for row in tchat.chat_models(prefs)}:
            meme["model"] = ""
        if existing is None and len(rows) >= 100:
            raise ApiError(400, "limit", "Слишком много мемесов", "Too many agents")
        rows = [meme if row.get("id") == meme["id"] else row for row in rows] if existing else rows + [meme]
        await asyncio.to_thread(save_preferences, {"memes": rows})
        return {"meme": _meme_out(meme)}

    @op("memes.delete")
    async def memes_delete(ctx, body):
        from .pgdata import load_preferences, save_preferences
        meme_id = str(body.get("id") or "")
        prefs = load_preferences()
        rows = [row for row in prefs.get("memes") or [] if isinstance(row, dict) and row.get("id") != meme_id]
        mapping = {k: v for k, v in (prefs.get("chat_memes") or {}).items() if v != meme_id}
        await asyncio.to_thread(save_preferences, {"memes": rows, "chat_memes": mapping})
        return {"ok": True}

    @op("memes.generate")
    async def memes_generate(ctx, body):
        prefs = ctx["prefs"]
        request = str(body.get("request") or "").strip()[:4000]
        if not request:
            raise ApiError(400, "bad_value", _t(prefs, "Опишите, какого мемеса вы хотите", "Describe the agent you want"))
        model = tchat.pick_model(str(body.get("model") or ""), prefs)
        if not model:
            raise ApiError(400, "no_model", "Нет доступных моделей", "No models available")
        try:
            draft = await tchat.design_meme(request, model)
        except (ValueError, json.JSONDecodeError):
            raise ApiError(502, "bad_answer", "Модель не вернула настройки мемеса", "The model returned no agent settings") from None
        return {"meme": _meme_out(draft)}

    # ------------------------------------------------------------------ settings & memory
    @op("settings.get")
    async def settings_get(ctx, body):
        prefs = ctx["prefs"]
        return {"settings": public_settings(prefs), "models": tchat.chat_models(prefs),
                "themes": theme_catalog(_lang(prefs)), "appearance": appearance(prefs)}

    @op("settings.save")
    async def settings_save(ctx, body):
        from .pgdata import save_preferences
        prefs = ctx["prefs"]
        clean = validate_settings(body.get("values") or {}, prefs)
        saved = await asyncio.to_thread(save_preferences, clean)
        if "language" in clean and clean["language"] != _lang(prefs):
            # Round 27: panel titles and walls («Погода», «Укажите город…») follow the panels' own language,
            # which only the desktop window used to post — after switching the Mini App to English they stayed Russian.
            from . import panels
            try:
                await asyncio.to_thread(panels.update_settings, {"lang": clean["language"]})
            except Exception as exc:  # the language itself is saved; the panels catch up when the window opens
                log.warning("panels language sync failed: %s", exc)
        if "profile_name" in clean or "profile_bio" in clean:
            # Round 21: MemuAI on the computer shows the *profile* name/bio
            # (profiles.json); the preference alone changed nothing there.
            await asyncio.to_thread(_sync_active_profile, clean)
        return {"settings": public_settings(saved), "appearance": appearance(saved)}

    @op("memory.get")
    async def memory_get(ctx, body):
        profile = tchat.profile_by_id(profile_id_of(ctx, body))
        active, _ = tchat.profiles()
        # Round 29: memory v2 — structured facts (source, confidence, scope, pending…) for a richer Mini App view
        from .tgapp_extras import memory_reply
        return await asyncio.to_thread(memory_reply, profile, active)

    @op("memory.save")
    async def memory_save(ctx, body):
        from .pgdata import MEMU_DIR
        from .storage import mutate_json
        profile = tchat.profile_by_id(profile_id_of(ctx, body))
        active, _ = tchat.profiles()
        if str(profile.get("id")) != active:
            raise ApiError(400, "not_active", "Память этого профиля меняется на компьютере",
                           "This profile's memory can only be changed on the computer")
        remove = {str(x) for x in body.get("remove") or [] if str(x)}
        enabled = body.get("enabled")

        def change(raw):
            data = raw if isinstance(raw, dict) else {"enabled": True, "facts": []}
            data["facts"] = [str(x) for x in data.get("facts") or [] if str(x) not in remove][:500]
            if isinstance(enabled, bool):
                data["enabled"] = enabled
            return True, data

        try:   # Round 29: memory v2 — removals by text or by id, the rest of the document is kept
            from . import memory_v2

            def change_v2(doc):
                if isinstance(enabled, bool):
                    doc["enabled"] = enabled
                for item in doc["items"]:
                    if item["status"] != "deleted" and (item["id"] in remove or item["text"] in remove):
                        item["status"], item["deleted_at"] = "deleted", memory_v2.now_ms()
                        memory_v2._history(item, "deleted", by="user", service="telegram")
            await asyncio.to_thread(memory_v2.mutate, change_v2)
        except ImportError:
            await asyncio.to_thread(mutate_json, Path(MEMU_DIR) / "memory.json", {"enabled": True, "facts": []},
                                    change, backup=False, validator=lambda value: isinstance(value, dict))
        from .tgapp_extras import memory_reply
        return await asyncio.to_thread(memory_reply, profile, active)

    # ------------------------------------------------------------------ modules
    def _module_root():
        from .modules_host import default_root
        return default_root()

    def _module_row(ctx, module_id: str) -> Dict[str, Any]:
        from .modules_host import list_modules
        row = next((item for item in list_modules(_module_root()) if item.get("id") == module_id), None)
        if row is None:
            raise ApiError(404, "not_found", "Модуль не найден", "Module not found")
        return row

    MOBILE_ACTIONS = {"env", "save", "log", "models", "request", "stream", "cancel", "clipboard", "close",
                      "resize", "notify", "chat_history"}
    ACTION_PERMISSION = {"request": "chat", "stream": "chat", "chat_history": "chat", "save": "storage",
                         "models": "models", "notify": "notify", "clipboard": "clipboard"}

    @op("modules.list")
    async def modules_list(ctx, body):
        from .modules_host import list_modules
        rows = await asyncio.to_thread(list_modules, _module_root())
        out = []
        for row in rows:
            permissions = set(row.get("permissions") or [])
            desktop_only = sorted(permissions & {"media", "files", "download", "external", "background", "web"})
            out.append({key: row.get(key) for key in ("id", "title", "title_en", "summary", "summary_en", "icon",
                                                      "accent", "builtin", "enabled", "version")}
                       | {"permissions": sorted(permissions), "desktop_only": desktop_only,
                          "hidden": bool((row.get("launcher") or {}).get("hidden"))})
        return {"modules": out}

    @op("modules.toggle")
    async def modules_toggle(ctx, body):
        from .modules_host import configure_module
        try:
            row = await asyncio.to_thread(configure_module, _module_root(), str(body.get("id") or ""),
                                          enabled=bool(body.get("enabled")))
        except (KeyError, ValueError):
            raise ApiError(404, "not_found", "Модуль не найден", "Module not found") from None
        return {"enabled": bool(row.get("enabled"))}

    @op("modules.open")
    async def modules_open(ctx, body):
        row = _module_row(ctx, str(body.get("id") or ""))
        if not row.get("enabled"):
            raise ApiError(403, "disabled", _t(ctx["prefs"], "Модуль выключен", "The module is switched off"))
        token, expires = tg_webapp.issue_token(ctx["user"], ctx["config"]["secret"], ttl=300, scope="module",
                                               extra=row["id"])
        return {"url": f"/tgapp/m/{row['id']}?t={token}", "expires": expires,
                "actions": sorted(MOBILE_ACTIONS)}

    @app.get("/tgapp/m/{module_id}", include_in_schema=False)
    async def module_page(module_id: str, t: str = ""):
        from .modules_host import load_package, load_persistent, module_document
        config = tg_webapp.load_config()
        data = tg_webapp.verify_token(t, config["secret"], scope="module")
        prefs = _prefs()
        if data is None or data.get("x") != module_id or not tg_webapp.is_authorized_user(data.get("u"), prefs) \
                or not config["enabled"]:
            return Response("Session expired", 401, media_type="text/plain")
        try:
            package = load_package(_module_root(), module_id)
            saved = load_persistent(_module_root(), module_id)
        except (KeyError, ValueError):
            return Response("Not found", 404, media_type="text/plain")
        if not package.get("enabled"):
            return Response("Disabled", 403, media_type="text/plain")
        from . import themes
        theme = themes.normalize_mode(prefs.get("theme") or "dark")
        env = {"lang": _lang(prefs), "theme": theme, "accent_name": themes.normalize_accent(prefs.get("accent_color")),
               "surface": str(prefs.get(f"surface_{theme}") or ""), "density": "normal",
               "font_scale": max(85, min(125, int(prefs.get("font_scale") or 100))), "platform": "telegram",
               "host": "MemuAI Telegram"}
        page = Response(module_document(package, saved, env=env), media_type="text/html; charset=utf-8")
        page.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
            "media-src data: blob:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'self'")
        page.headers["X-Content-Type-Options"] = "nosniff"
        page.headers["Cache-Control"] = "no-store"
        page.headers["Referrer-Policy"] = "no-referrer"
        return page

    def _module_allowed(row: Dict[str, Any], action: str) -> None:
        if action not in MOBILE_ACTIONS:
            raise ApiError(400, "desktop_only", "Это действие модуля доступно только на компьютере",
                           "This module action is only available on the computer")
        needed = ACTION_PERMISSION.get(action)
        if needed and needed not in (row.get("permissions") or []):
            raise ApiError(403, "permission", f"Модулю не выдано право «{needed}»", f"The module has no «{needed}» permission")

    def _module_build(row: Dict[str, Any], payload: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
        from .modules_host import build_request
        bridge = row.get("bridge") or {}
        built = build_request(model=str(payload.get("model") or bridge.get("model") or ""),
                              instructions=str(payload.get("instructions") or bridge.get("instructions") or ""),
                              keep_in_chat=bool(payload.get("keep_in_chat", bridge.get("keep_in_chat"))),
                              text=str(payload.get("text") or ""), images=payload.get("images") or [],
                              documents=payload.get("documents") or [], history=payload.get("history") or [],
                              thinking=bool(payload.get("thinking", bridge.get("thinking"))),
                              web_search=bool(payload.get("web_search", bridge.get("web_search"))),
                              temperature=payload.get("temperature"))
        built["model"] = tchat.pick_model(built["model"], prefs)
        if not built["model"]:
            raise ApiError(400, "no_model", "Выберите модель", "Choose a model")
        return built

    def _module_chat(module_id: str, title: str, text: str, answer: str, model: str) -> None:
        from .pgdata import load_chats, upsert_chat
        chat_id = f"module-{module_id}"
        stamp = tchat.now_ms()
        row = next((chat for chat in load_chats() if chat.get("id") == chat_id), None)
        rows = [{"role": "user", "content": text, "createdAt": stamp},
                {"role": "assistant", "content": answer, "model": model, "createdAt": stamp}]
        if row:
            row = dict(row)
            row["messages"] = list(row.get("messages") or []) + rows
            row["updated"] = stamp
        else:
            row = {"id": chat_id, "title": (title or module_id)[:80], "mode": "chat", "messages": rows,
                   "created": stamp, "updated": stamp, "module": module_id}
        upsert_chat(row)

    @op("modules.bridge")
    async def modules_bridge(ctx, body):
        from .modules_host import save_persistent
        row = _module_row(ctx, str(body.get("id") or ""))
        if not row.get("enabled"):
            raise ApiError(403, "disabled", "Модуль выключен", "The module is switched off")
        action = str(body.get("action") or "")
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        _module_allowed(row, action)
        prefs = ctx["prefs"]
        if action == "save":
            try:
                return {"result": await asyncio.to_thread(save_persistent, _module_root(), row["id"], payload)}
            except ValueError as exc:
                raise ApiError(400, "bad_value", str(exc)) from None
        if action == "models":
            return {"result": [{"id": item["id"], "name": item["name"], "provider": item["provider"],
                                "image": False, "video": False, "audio": False,
                                "vision": bool(item.get("vision")), "thinking": False, "web_search": False,
                                "context": 0} for item in tchat.chat_models(prefs)]}
        if action == "chat_history":
            from .pgdata import load_chats
            chat = next((c for c in load_chats() if c.get("id") == f"module-{row['id']}"), None)
            return {"result": chat}
        if action == "request":
            built = _module_build(row, payload, prefs)
            try:
                answer = await tchat.complete(built["model"], built["messages"], web_search=built["web_search"],
                                              thinking=built["thinking"])
            except Exception as exc:
                raise ApiError(502, "model_error", str(exc)[:500]) from None
            built["text"] = answer
            if built.get("keep_in_chat"):
                await asyncio.to_thread(_module_chat, row["id"], row.get("title") or "",
                                        str(payload.get("text") or ""), answer, built["model"])
            return {"result": {key: built.get(key) for key in ("text", "model", "thinking", "web_search")}}
        raise ApiError(400, "desktop_only", "Это действие модуля доступно только на компьютере",
                           "This module action is only available on the computer")

    @op("modules.stream")
    async def modules_stream(ctx, body):
        row = _module_row(ctx, str(body.get("id") or ""))
        if not row.get("enabled"):
            raise ApiError(403, "disabled", "Модуль выключен", "The module is switched off")
        _module_allowed(row, "stream")
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        built = _module_build(row, payload, ctx["prefs"])
        job = tchat.jobs.add(tchat.Job("module", ctx["user"]))
        job.meta = {"module": row["id"]}

        async def run():
            from .chat import chat_service
            parts: List[str] = []
            try:
                async for chunk in chat_service.stream_completion(built["model"], built["messages"], {
                        "web_search": built["web_search"], "thinking": built["thinking"]}):
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            parts.append(str(delta))
                            job.emit("delta", text=str(delta))
                answer = "".join(parts)
                if built.get("keep_in_chat"):
                    await asyncio.to_thread(_module_chat, row["id"], row.get("title") or "",
                                            str(payload.get("text") or ""), answer, built["model"])
                job.emit("result", result={"text": answer, "model": built["model"]})
            except asyncio.CancelledError:
                job.emit("error", message="stopped")
            except Exception as exc:
                job.emit("error", message=str(exc)[:500])
            job.emit("done")
            job.finish()

        job.task = asyncio.ensure_future(run())
        return {"job": job.id}

    # ------------------------------------------------------------------ MemuCode
    def require_code(ctx) -> None:
        if not ctx["config"].get("code_control"):
            raise ApiError(403, "code_disabled", _t(ctx["prefs"], "Управление MemuCode выключено в настройках MemuAI",
                                                    "MemuCode control is switched off in MemuAI settings"))

    from . import tgapp_code as tcode

    @op("code.sessions")
    async def code_sessions(ctx, body):
        require_code(ctx)
        rows = await asyncio.to_thread(tcode.list_sessions, 120)
        return {"sessions": rows, "cli": bool(tcode.memucode_command())}

    @op("code.transcript")
    async def code_transcript(ctx, body):
        require_code(ctx)
        session_id = str(body.get("session_id") or "")
        data = await asyncio.to_thread(tcode.read_transcript, session_id, int(body.get("cursor") or 0))
        terminal = await asyncio.to_thread(tcode.terminal_for, session_id)
        data["live"] = terminal
        return data

    @op("code.watch")
    async def code_watch(ctx, body):
        """Streamed segment: new transcript items and live-state changes of one session."""
        require_code(ctx)
        session_id = str(body.get("session_id") or "")
        # Round 29: a terminal (or a phone host) given by ``rc`` is followed across /clear, /resume and the
        # first message of a new session — its session id changes, the phone switches with it.
        rc = str(body.get("rc") or "")
        if rc:
            state = await asyncio.to_thread(tcode.live_state, rc)
            if state and state.get("session_id") and state.get("session_id") != session_id:
                session_id = str(state["session_id"])
                body = {**body, "cursor": 0}
        if tcode.find_transcript(session_id) is None and tcode.terminal_for(session_id) is None:
            raise ApiError(404, "not_found", "Сессия не найдена", "Session not found")
        cursor = int(body.get("cursor") or 0)
        seen = str(body.get("live_hash") or "")

        async def produce():
            nonlocal cursor, seen, session_id
            deadline = time.monotonic() + STREAM_SEGMENT
            last_ping = time.monotonic()
            yield (json.dumps({"type": "session", "id": session_id, "cursor": cursor}) + "\n").encode()
            while time.monotonic() < deadline:
                if rc:
                    state = await asyncio.to_thread(tcode.live_state, rc)
                    if state and state.get("session_id") and state.get("session_id") != session_id:
                        session_id, cursor = str(state["session_id"]), 0
                        yield (json.dumps({"type": "session", "id": session_id, "cursor": 0, "reset": True}) + "\n").encode()
                data = await asyncio.to_thread(tcode.read_transcript, session_id, cursor)
                if data.get("items") or data.get("reset"):
                    yield (json.dumps({"type": "items", **{k: data.get(k) for k in ("items", "cursor", "reset")}},
                                      ensure_ascii=False) + "\n").encode()
                cursor = int(data.get("cursor") or cursor)
                terminal = await asyncio.to_thread(tcode.terminal_for, session_id)
                state = dict(terminal or {})
                state.pop("heartbeat", None)
                digest = str(hash(json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)))
                if digest != seen:
                    seen = digest
                    yield (json.dumps({"type": "live", "live": terminal, "hash": digest},
                                      ensure_ascii=False, default=str) + "\n").encode()
                if time.monotonic() - last_ping > 5:
                    last_ping = time.monotonic()
                    yield b'{"type":"ping"}\n'
                await asyncio.sleep(0.4)
            yield (json.dumps({"type": "end", "cursor": cursor, "hash": seen}) + "\n").encode()
        return StreamingResponse(produce(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @op("code.command")
    async def code_command(ctx, body):
        require_code(ctx)
        rc = str(body.get("rc") or "")
        command = body.get("command") if isinstance(body.get("command"), dict) else {}
        try:
            command_id = await asyncio.to_thread(tcode.send_to_terminal, rc, command)
        except tcode.BlockedCommand:
            raise ApiError(403, "blocked", _t(ctx["prefs"], "«!команда» и /run с телефона не выполняются: попросите агента обычным сообщением — команда пройдёт через разрешения",
                                              "“!command” and /run are not run from the phone: ask the agent in a normal message so it goes through the permission dialog")) from None
        except PermissionError:
            raise ApiError(409, "confirm", _t(ctx["prefs"], "Нужно подтверждение опасного действия",
                                              "This dangerous action needs confirmation")) from None
        except LookupError:
            raise ApiError(409, "gone", "Вопрос в терминале уже сменился", "The question in the terminal has changed") from None
        except (ValueError, RuntimeError):
            raise ApiError(400, "bad_request", "Некорректная команда", "Invalid command") from None
        return {"ok": True, "id": command_id}

    @op("code.run")
    async def code_run(ctx, body):
        require_code(ctx)
        mode = str(body.get("mode") or "default")
        if mode == "bypassPermissions" and body.get("confirm") is not True:
            raise ApiError(409, "confirm", _t(ctx["prefs"], "Режим без разрешений нужно подтвердить",
                                              "Bypassing permissions needs confirmation"))
        try:
            job = tcode.start_headless(ctx["user"], str(body.get("session_id") or ""), str(body.get("text") or ""),
                                       mode)
        except LookupError as exc:
            missing_folder = "folder" in str(exc)
            raise ApiError(404, "not_found", "Папка проекта не найдена" if missing_folder else "Сессия не найдена",
                           "The project folder is missing" if missing_folder else "Session not found") from None
        except RuntimeError as exc:
            message = {"live": _t(ctx["prefs"], "Сессия открыта в терминале — пишите в неё напрямую",
                                  "The session is open in a terminal — send to it directly"),
                       "busy": _t(ctx["prefs"], "Эта сессия уже выполняется", "This session is already running")}
            raise ApiError(409, str(exc), message.get(str(exc), str(exc))) from None
        except FileNotFoundError:
            raise ApiError(400, "no_cli", _t(ctx["prefs"], "MemuCode CLI не установлен", "MemuCode CLI is not installed")) from None
        except ValueError:
            raise ApiError(400, "bad_request", "Пустой или слишком длинный запрос", "The request is empty or too long") from None
        return {"job": job.id}

    # ------------------------------------------------------------------ Round 29: MemuCode host, files, extras
    from . import tgapp_code_api, tgapp_extras
    tgapp_code_api.register(op, ApiError, require_code, _t, _lang)
    public_routes.update(tgapp_extras.register(op, ApiError, t=_t, lang_of=_lang, profile_id_of=profile_id_of,
                                               client_key=_client_key))

    # ------------------------------------------------------------------ panels (Round 24)
    from . import telegram_panels
    telegram_panels.register_tgapp_ops(op, ApiError)

    # ------------------------------------------------------------------ Round 37 (plan-2): Planning conditions on the phone
    from . import schedule_tgapp_ops
    schedule_tgapp_ops.register_tgapp_ops(op, ApiError)

    # ------------------------------------------------------------------ Round 37: MemuMusic (music.*, /tgapp/music/*)
    from .music import tgapp_ops as music_tgapp_ops
    music_tgapp_ops.register_tgapp_ops(op, ApiError, app=app)

    # ------------------------------------------------------------------ Round 40 (r40-learn): MemuLearn «Тренажёр» (learn.*)
    from .learn import tgapp_ops as learn_tgapp_ops
    learn_tgapp_ops.register_tgapp_ops(op, ApiError)

    # ------------------------------------------------------------------ static
    static_root = tg_webapp.STATIC_DIR.resolve()

    @app.get("/tgapp", include_in_schema=False)
    async def entry_redirect():
        return RedirectResponse("/tgapp/", 307)

    @app.get("/tgapp/health", include_in_schema=False)
    async def health():
        return {"ok": True}

    @app.get("/tgapp/healthz", include_in_schema=False)
    async def healthz():
        # Round 21: the manager calls this over the PUBLIC address before it
        # gives the address to Telegram; the nonce proves it reached us.
        return {"ok": True, "app": "memuai-tgapp", "nonce": tg_webapp.instance_nonce()}

    # r30: the /image /video chip of the message box is the main window's own (pgstatic/cmd-highlight.*), shared
    # so both look and behave the same; only these two files are reachable from here.
    shared_root = Path(__file__).parent / "pgstatic"
    shared_files = {"cmd-highlight.js": "text/javascript", "cmd-highlight.css": "text/css",
                    "memu-keys.js": "text/javascript",   # r33: shortcuts in any keyboard layout
                    "askuser.js": "text/javascript", "askuser.css": "text/css"}   # Round 35: the ask_user form

    @app.get("/tgapp/shared/{name}", include_in_schema=False)
    async def shared(name: str):
        if name not in shared_files or not (shared_root / name).is_file():
            return JSONResponse({"error": "not found"}, 404)
        response = FileResponse(str(shared_root / name), media_type=shared_files[name])
        response.headers["Cache-Control"] = "max-age=300"
        return response

    @app.get("/tgapp/{path:path}", include_in_schema=False)
    async def static(path: str):
        if path.startswith("api/") or path.startswith("m/"):
            return JSONResponse({"error": "not found"}, 404)
        target = (static_root / (path or "index.html")).resolve()
        if static_root not in target.parents and target != static_root:
            return JSONResponse({"error": "not found"}, 404)
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return JSONResponse({"error": "not found"}, 404)
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".js", ".mjs"):
            media = "text/javascript"
        response = FileResponse(str(target), media_type=media)
        response.headers["Cache-Control"] = "no-cache" if target.name == "index.html" else "max-age=300"
        return response

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                   include_in_schema=False)
    async def everything_else(path: str):
        return JSONResponse({"error": "not found"}, 404)

    return app
