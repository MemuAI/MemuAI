"""API панели MemuAI Proxy: аккаунты, модели, настройки, логи и OAuth."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import json
import os
import pathlib
import re
import secrets
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .applog import app_log
from .config import config_store
from .metrics import metrics
from . import model_info
from .pool import pool
from .providers import RESERVED_PREFIXES, all_providers, get as get_provider, meta_list
from .routing import known_prefixes, resolve
from .state import ST_OK, ST_UNKNOWN, ClosingStreamingResponse, ModelEntry, state
from .version import APP_VERSION

log = logging.getLogger("memuai.admin")
router = APIRouter(prefix="/api/admin")


@router.post("/accounts/{account_id}/opencode-probe")
async def opencode_generation_probe(account_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=4096)
    if error is not None:
        return error
    if body.get("confirmed") is not True:
        return _err("Подтвердите один короткий запрос Free / Confirm one tiny Free request")
    account = state.get_account(account_id)
    if not account or account.provider != "opencode":
        return _err("Подключение OpenCode не найдено / OpenCode connection not found", 404)
    from .providers.opencode_catalog import mode_of
    if not account.enabled or mode_of(account.fields) != "free":
        return _err("Выберите сохранённое включённое подключение Free / Choose a saved enabled Free connection")
    provider = get_provider("opencode")
    if provider is None:
        return _err("OpenCode недоступен / OpenCode is unavailable", 503)
    return await provider.probe_free(account)


@router.get("/opencode/runtime")
async def opencode_runtime_status(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .opencode_runtime import runtime
    return await asyncio.to_thread(runtime.status)


@router.post("/opencode/runtime")
async def opencode_runtime_action(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .opencode_runtime import runtime
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return _err("Ожидается объект / Expected an object")
        if body.get("action") == "install" and body.get("confirmed") is True:
            return await asyncio.to_thread(runtime.install)
        if body.get("action") == "cancel":
            return await asyncio.to_thread(runtime.cancel_install)
        return _err("Неизвестное действие или установка не подтверждена / Unknown action or install not confirmed")
    except (ValueError, RuntimeError) as exc:
        return _err(str(exc))

_sessions: Dict[str, float] = {}
_internal_sessions: set[str] = set()
_login_fails: Dict[str, float] = {}
SESSION_TTL = 7 * 24 * 3600
_PLAYGROUND_CHAT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_MODEL_PREFIX_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_STARTED_AT = time.time()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _client_is_loopback(request: Request) -> bool:
    """Use the socket peer only; forwarded headers are intentionally ignored."""
    peer = _client_ip(request).split("%", 1)[0]
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return peer.casefold() == "localhost"


def _is_loopback_bind() -> bool:
    return config_store.effective_host.strip().lower() in _LOOPBACK_HOSTS


def _browser_origin_guard(request: Request) -> Optional[JSONResponse]:
    """Keep browser callers on the exact Proxy origin and stop DNS rebinding."""
    host_header = request.headers.get("host", "")
    try:
        host_name = urllib.parse.urlsplit("//" + host_header).hostname or ""
    except ValueError:
        host_name = ""
    effective_host = config_store.effective_host.strip().strip("[]").lower()
    trusted_host = False
    if _is_loopback_bind():
        trusted_host = host_name.lower() in _LOOPBACK_HOSTS
    elif effective_host in ("0.0.0.0", "::"):
        try:
            ipaddress.ip_address(host_name)
            trusted_host = True
        except ValueError:
            trusted_host = host_name.lower() == "localhost"
    else:
        trusted_host = host_name.lower() == effective_host
    if not trusted_host:
        return _err("Недоверенный Host", 403)
    origin = request.headers.get("origin", "")
    if origin:
        try:
            same_origin = urllib.parse.urlsplit(origin).netloc.lower() == host_header.lower()
        except ValueError:
            same_origin = False
        if not same_origin:
            return _err("Cross-origin доступ к панели запрещён", 403)
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return _err("Cross-site доступ к панели запрещён", 403)
    return None


def _auth(request: Request) -> Optional[JSONResponse]:
    """Панель без пароля только для same-origin UI на loopback.

    Если Proxy открыт в сеть (host 0.0.0.0), пароль снова обязателен."""
    browser_denied = _browser_origin_guard(request)
    if browser_denied:
        return browser_denied
    if not _is_loopback_bind():
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or _sessions.get(token, 0) < time.time():
            _sessions.pop(token, None)
            _internal_sessions.discard(token)
            return JSONResponse(status_code=401, content={"error": "Требуется вход в панель",
                                         "error_en": "Panel sign-in required"})
    return None


# Панель двуязычная, а ошибки API исторически были только на русском. Чтобы в
# английском интерфейсе не появлялся русский текст, каждое сообщение отдаётся
# сразу в двух вариантах: "error" (RU) и "error_en" (EN).
_ERR_EN: Dict[str, str] = {
    "Cross-origin доступ к панели запрещён": "Cross-origin access to the panel is blocked",
    "Cross-site доступ к панели запрещён": "Cross-site access to the panel is blocked",
    "Аккаунт не найден": "Account not found",
    "Активация окна разрешена только локальному процессу":
        "Only a local process may bring the window to front",
    "Выберите хотя бы один аккаунт": "Select at least one account",
    "Запрос слишком большой": "The request is too large",
    "Изображение больше 30 МБ": "The image is larger than 30 MB",
    "Кастомное подключение копировать нельзя: имя префикса должно быть уникальным":
        "A custom connection cannot be duplicated: its prefix name must stay unique",
    "Ключ не найден": "Key not found",
    "Локальное завершение запрещено": "Local shutdown is not allowed",
    "Метка или адрес прокси слишком длинные": "The label or proxy address is too long",
    "Модель не найдена": "Model not found",
    "Укажите название (префикс)": "Enter a name (prefix)",
    "Base URL должен начинаться с http(s)://": "Base URL must start with http(s)://",
    "Неизвестный режим пула API-ключей": "Unknown API key pool mode",
    "Номер аккаунта (auth_user) должен быть числом": "The account index (auth_user) must be a number",
    "Сначала добавьте аккаунт или подключение с этим префиксом":
        "Add an account or connection with this prefix first",
    "Название должно содержать только латинские буквы, цифры, точки, дефисы и подчёркивания":
        "The name may only contain Latin letters, digits, dots, hyphens and underscores",
    "Название подключения нельзя изменить; создайте новое подключение":
        "A connection name cannot be changed; create a new connection instead",
    "Неверный пароль": "Wrong password",
    "Недоверенный Host": "Untrusted Host header",
    "Недопустимый адрес изображения": "Invalid image address",
    "Недопустимый токен завершения": "Invalid shutdown token",
    "Некорректное имя модели": "Invalid model name",
    "Некорректное название подключения": "Invalid connection name",
    "Некорректное поле аккаунта": "Invalid account field",
    "Некорректный Content-Length": "Invalid Content-Length",
    "Некорректный JSON": "Malformed JSON",
    "Некорректный идентификатор провайдера": "Invalid provider id",
    "Некорректный префикс модели": "Invalid model prefix",
    "Некорректный приоритет аккаунта": "Invalid account priority",
    "Нет доступного аккаунта Genspark": "No usable Genspark account",
    "Новый пароль должен быть не короче 8 символов":
        "The new password must be at least 8 characters long",
    "Нужны model и messages": "Both model and messages are required",
    "Ожидается JSON-объект": "A JSON object is expected",
    "Пароль слишком длинный": "The password is too long",
    "Поле enabled должно быть логическим": "Field enabled must be a boolean",
    "Поле extra должно быть JSON-объектом": "Field extra must be a JSON object",
    "Поле facts должно быть массивом": "Field facts must be an array",
    "Поле fields должно быть JSON-объектом": "Field fields must be a JSON object",
    "Поле images должно быть массивом": "Field images must be an array",
    "Поле order должно быть числом": "Field order must be a number",
    "Поле order/ids должно быть списком идентификаторов":
        "Field order/ids must be a list of identifiers",
    "Поле settings должно быть JSON-объектом": "Field settings must be a JSON object",
    "Поле tags должно быть списком строк": "Field tags must be a list of strings",
    "Поле tasks должно быть массивом": "Field tasks must be an array",
    "Поле known_ids должно быть массивом": "Field known_ids must be an array",
    "Примечание слишком длинное": "The note is too long",
    "Провайдер не найден": "Provider not found",
    "Провайдер не поддерживает вход через браузер":
        "This provider has no browser sign-in",
    "Провайдер не поддерживает вход по коду": "This provider has no device-code sign-in",
    "AWS не выдал код входа вовремя — повторите через минуту":
        "AWS did not issue a sign-in code in time — try again in a minute",
    "Провайдер недоступен": "Provider unavailable",
    "Пустой промпт": "The prompt is empty",
    "Слишком большой запрос": "The request is too large",
    "Слишком длинный идентификатор модели": "The model id is too long",
    "Слишком длинный список": "The list is too long",
    "Слишком много аккаунтов за раз": "Too many accounts at once",
    "Слишком часто. Подождите пару секунд.": "Too many attempts. Wait a couple of seconds.",
    "Такая кука уже добавлена": "This cookie is already added",
    "Текущий пароль неверен": "The current password is wrong",
    "Укажите префикс и модель": "Provide both a prefix and a model",
    "Укажите провайдера": "Provide a provider",
    "Это имя занято встроенным провайдером": "That name belongs to a built-in provider",
}


def _field_label_en(label: str) -> str:
    for provider in all_providers().values():
        try:
            fields = provider.account_fields()
        except Exception:
            continue
        for field in fields:
            if field.label == label:
                return field.label_en or label
    return label


# Сообщения проверки полей аккаунта с подставленным значением.
_ERR_EN_PATTERNS = (
    (re.compile(r"Заполните поле «(.+)»"), lambda m: f"Fill in the “{_field_label_en(m.group(1))}” field"),
    (re.compile(r"Слишком много сообщений в запросе: (\d+) \(предел (\d+)\)"),
     lambda m: f"Too many messages in the request: {m.group(1)} (limit {m.group(2)})"),
    (re.compile(r"Префикс «(.+)» зарезервирован встроенным провайдером"),
     lambda m: f"The prefix “{m.group(1)}” belongs to a built-in provider"),
    (re.compile(r"Модель (.+) не создаёт изображения\. Выберите модель изображений\."),   # Round 33 (r33-models)
     lambda m: f"The model {m.group(1)} does not create images. Pick an image model."),
    (re.compile(r"Нет рабочего подключения для модели (.+)\. Проверьте его в MemuAI Proxy\."),   # Round 33 (r33c-models-vision)
     lambda m: f"No working connection for the model {m.group(1)}. Check it in MemuAI Proxy."),
    (re.compile(r"Нет доступного аккаунта (.+) для генерации"),
     lambda m: f"No usable {m.group(1)} account for generation — check the connection in MemuAI Proxy"),
)


def _err_en(message: str) -> str:
    exact = _ERR_EN.get(message)
    if exact:
        return exact
    for pattern, english in _ERR_EN_PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return english(match)
    for russian, english in _ERR_EN.items():
        if message.startswith(russian):
            return english + message[len(russian):]
    return message


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": message, "error_en": _err_en(message)})



async def _json_object(request: Request, *, max_bytes: int = 1_048_576
                       ) -> tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """Read a bounded JSON object and turn malformed bodies into a stable 4xx."""
    raw_length = request.headers.get("content-length", "")
    if raw_length:
        try:
            parsed_length = int(raw_length)
            if parsed_length < 0:
                return None, _err("Некорректный Content-Length")
            if parsed_length > max_bytes:
                return None, _err("Запрос слишком большой", 413)
        except (TypeError, ValueError):
            return None, _err("Некорректный Content-Length")
    try:
        # Read with a running limit: a chunked body has no Content-Length, and
        # ``request.body()`` would buffer all of it before the size check.
        buffer = bytearray()
        async for chunk in request.stream():
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                return None, _err("Запрос слишком большой", 413)
        raw = bytes(buffer)
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, _err("Некорректный JSON")
    if not isinstance(body, dict):
        return None, _err("Ожидается JSON-объект")
    return body, None


def _playground_chat_path(chat_id: str) -> pathlib.Path:
    """Return a contained legacy-playground path for a validated chat id."""
    value = str(chat_id or "")
    if not _PLAYGROUND_CHAT_ID_RE.fullmatch(value):
        raise ValueError("Некорректный идентификатор чата")
    from .config import DATA_DIR
    root = (pathlib.Path(DATA_DIR) / "playground").resolve()
    target = (root / f"{value}.json").resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Некорректный идентификатор чата") from exc
    return target


# ------------------------------ вход ------------------------------

@router.post("/login")
async def login(request: Request):
    browser_denied = _browser_origin_guard(request)
    if browser_denied:
        return browser_denied
    ip = _client_ip(request)
    if time.time() - _login_fails.get(ip, 0) < 1.5:
        return _err("Слишком часто. Подождите пару секунд.", 429)
    body, error = await _json_object(request, max_bytes=65_536)
    if error is not None:
        return error
    password = str(body.get("password", ""))
    if len(password) > 256:
        return _err("Пароль слишком длинный")
    if not config_store.check_admin_password(password):
        _login_fails[ip] = time.time()
        return _err("Неверный пароль", 401)
    _login_fails.pop(ip, None)
    now = time.time()
    # Expired sessions and stale throttle entries were never removed: on a
    # network-exposed panel both dictionaries only grew.
    for stale in [key for key, until in _sessions.items() if until < now]:
        _sessions.pop(stale, None)
        _internal_sessions.discard(stale)
    for stale in [key for key, at in _login_fails.items() if now - at > 3600]:
        _login_fails.pop(stale, None)
    token = secrets.token_urlsafe(32)
    _sessions[token] = now + SESSION_TTL
    return {"token": token, "password_is_default": config_store.first_run_password}


@router.post("/logout")
async def logout(request: Request):
    browser_denied = _browser_origin_guard(request)
    if browser_denied:
        return browser_denied
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    _sessions.pop(token, None)
    _internal_sessions.discard(token)
    return {"ok": True}


# ------------------------------ обзор ------------------------------

@router.get("/overview")
async def overview(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    endpoint_host = config_store.effective_host
    if ":" in endpoint_host and not endpoint_host.startswith("["):
        endpoint_host = f"[{endpoint_host}]"
    providers = []
    # Только модели живых провайдеров: плитка Genspark без аккаунта не должна считать его модели.
    enabled_models = state.enabled_models()
    for pid, p in all_providers().items():
        accounts = state.accounts_of(pid)
        models = [m for m in enabled_models if m.provider == pid]
        usable = len(state.usable_accounts(pid))
        providers.append({
            "id": pid, "title": p.title, "icon": p.icon, "oauth": p.oauth,
            "description": p.description,
            "title_en": getattr(p, "title_en", "") or p.title,
            "description_en": getattr(p, "description_en", "") or p.description,
            "accounts": len(accounts), "accounts_usable": usable,
            "models": len(models),
        })
    return {
        "providers": providers,
        "accounts_total": len(state.accounts),
        "accounts_usable": len([a for a in state.accounts.values() if a.usable()]),
        "models_total": len(enabled_models),
        # Round 31: the «Подключить клиент» sample used a hard-coded Genspark model id.
        "sample_model": next((m.id for m in enabled_models
                              if state.usable_accounts(m.provider)), enabled_models[0].id if enabled_models else ""),
        "api_keys_count": len(config_store.settings.api_keys),
        "api_protected": bool(config_store.settings.api_keys),
        "host": endpoint_host,
        "port": config_store.effective_port,
        "proxy_enabled": config_store.settings.proxy_enabled,
        "password_is_default": config_store.first_run_password,
        "prefixes": known_prefixes(),
        "first_api_key": (config_store.settings.api_keys or [""])[0],
        "version": APP_VERSION,
        "metrics": metrics.snapshot() if config_store.settings.metrics_enabled else None,
        "started_at": _STARTED_AT,
    }


# ------------------------------ настройки ------------------------------

@router.get("/settings")
async def get_settings(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return {"settings": config_store.public_dict()}


@router.post("/settings")
async def update_settings(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=262_144)
    if error is not None:
        return error
    updates = body.get("settings")
    if not isinstance(updates, dict):
        return _err("Поле settings должно быть JSON-объектом")
    rejected = config_store.apply_updates(updates)
    from .providers import http as hub_http
    hub_http.configure(verify=config_store.settings.verify_ssl)
    gp = get_provider("genspark")
    if gp:
        await gp.reconfigure()
    return {"ok": True, "rejected": rejected, "settings": config_store.public_dict()}


@router.post("/password")
async def change_password(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=65_536)
    if error is not None:
        return error
    old, new = str(body.get("old", "")), str(body.get("new", ""))
    if len(old) > 256 or len(new) > 256:
        return _err("Пароль слишком длинный")
    if not config_store.check_admin_password(old):
        return _err("Текущий пароль неверен", 403)
    if len(new) < 8:
        return _err("Новый пароль должен быть не короче 8 символов")
    config_store.set_admin_password(new)
    for token in list(_sessions):
        if token not in _internal_sessions:
            _sessions.pop(token, None)
    return {"ok": True, "reauth_required": not _is_loopback_bind()}


# ------------------------------ аккаунты ------------------------------

@router.get("/providers")
async def providers_meta(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return {"providers": meta_list()}


@router.get("/providers/presets")
async def provider_presets(request: Request):
    """Готовые подключения поверх «Собственного API»: остаётся ввести ключ."""
    deny = _auth(request)
    if deny:
        return deny
    # Round 33 (#24): ~100 services from app/providers/catalog.py (top ones first, then A–Z).
    from .providers.catalog import catalog, headers_for
    customs = [acc for acc in state.accounts.values() if acc.provider == "custom"]
    taken = {str(acc.fields.get("name", "")).strip().lower() for acc in customs}
    bases = {str(acc.fields.get("base_url", "")).strip().rstrip("/").lower() for acc in customs}
    presets = []
    for preset in catalog():
        item = dict(preset)
        item["icon"] = item.get("mono", "")               # older UI code reads «icon»
        item["headers"] = headers_for(preset)
        item["in_use"] = item["name"] in taken or (
            not item.get("params") and item["base_url"].rstrip("/").lower() in bases)
        presets.append(item)
    return {"presets": presets}


@router.get("/accounts")
async def list_accounts(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return {"accounts": state.list_accounts(mask=True)}


@router.post("/accounts")
async def add_account(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=1_048_576)
    if error is not None:
        return error
    provider_id = str(body.get("provider", "")).strip()
    if len(provider_id) > 80:
        return _err("Некорректный идентификатор провайдера")
    provider = get_provider(provider_id)
    if provider is None:
        return _err(f"Неизвестный провайдер «{provider_id}»")
    if getattr(provider, "managed", False) is True:
        return _err("Локальные модели добавляются на вкладке «Локальные модели»")
    fields_raw = body.get("fields")
    if not isinstance(fields_raw, dict):
        return _err("Поле fields должно быть JSON-объектом")
    fields = dict(fields_raw)
    extra = body.get("extra", {})
    if not isinstance(extra, dict):
        return _err("Поле extra должно быть JSON-объектом")
    label = str(body.get("label", "") or "").strip()
    proxy = str(body.get("proxy", "") or "").strip()
    if len(label) > 256 or len(proxy) > 4096:
        return _err("Метка или адрес прокси слишком длинные")
    priority = str(body.get("priority", "normal"))
    if priority not in ("normal", "reserve"):
        return _err("Некорректный приоритет аккаунта")
    err = provider.validate_fields(fields)
    if err:
        return _err(err)
    if provider_id == "custom":
        name = str(fields.get("name", "")).strip().lower().replace(" ", "-")
        if not _MODEL_PREFIX_RE.fullmatch(name):
            return _err("Название должно содержать только латинские буквы, цифры, точки, дефисы и подчёркивания")
        fields["name"] = name
        for acc in state.accounts.values():
            if acc.provider == "custom" and str(acc.fields.get("name", "")).lower() == name:
                return _err(f"Подключение с именем «{name}» уже существует")
        if name in RESERVED_PREFIXES:
            return _err("Это имя занято встроенным провайдером")
    if provider_id == "genspark":
        cookie = str(fields.get("cookie", ""))
        if "session_id=" not in cookie.lower():
            fields["cookie"] = f"session_id={cookie}"
        for acc in state.accounts.values():
            if acc.provider == "genspark" and acc.fields.get("cookie") == fields["cookie"]:
                return _err("Такая кука уже добавлена")
    note = str(body.get("note", "") or "")[:2000]
    tags_raw = body.get("tags") if isinstance(body.get("tags"), list) else []
    tags = [str(tag).strip()[:40] for tag in tags_raw if str(tag).strip()][:12]
    next_order = state.next_order(provider_id)
    acc = state.add_account(provider_id, fields,
                            label=_auto_label(provider, provider_id, fields, label),
                            proxy=proxy, extra=extra)
    acc.order = next_order
    acc.note = note
    acc.tags = tags
    if priority != acc.priority:
        acc.priority = priority
    state.save_accounts()
    # сеем каталог моделей провайдера при первом аккаунте
    if provider_id == "custom":
        try:
            models_raw = str(fields.get("models", "") or "")
            names = [m.strip() for m in re.split(r"[,\n;]", models_raw) if m.strip()]
            from .music_runtime import model_names
            names = list(dict.fromkeys(names + model_names(fields)))
            if names:
                # Declared image/video generators are models of this connection too (Round 29).
                media = [m.strip() for key in ("image_models", "video_models")
                         for m in re.split(r"[,\n;]", str(fields.get(key, "") or "")) if m.strip()]
                names = list(dict.fromkeys(names + media))
            if not names:
                dynamic = await provider.dynamic_models(acc)
                names = [str(m) for m in (dynamic or [])][:5000]
            state.seed_provider_models("custom", name, [(m, "кастомное подключение") for m in names],
                                       account_id=acc.id)
        except Exception:
            pass
    else:
        state.seed_provider_models(provider_id, provider_id, provider.seed_models())
        if provider_id in ("antigravity", "opencode"):
            from .model_refresh import refresher
            if provider_id == "opencode":
                # The form offers discovered models immediately after saving.
                # No seeds/guessed IDs; failure leaves the saved connection
                # available for a manual refresh rather than inventing models.
                await refresher.refresh(providers=[provider_id], reason="account-added")
            else:
                from .providers.base import spawn_background
                spawn_background(refresher.refresh(providers=[provider_id], reason="account-added"))
    return {"ok": True, "account": acc.public_dict()}


def _auto_label(provider, provider_id: str, fields: Dict[str, Any], label: str) -> str:
    """Уникальное имя для нового аккаунта, если пользователь его не задал.

    Девять безымянных «Собственный API» в списке различить невозможно, поэтому
    метка собирается из самого говорящего поля: имени подключения, хоста
    base_url или почты. Одинаковые метки получают номер.
    """
    label = (label or "").strip()
    if not label:
        parts = []
        name = str(fields.get("name", "") or "").strip()
        if name:
            parts.append(name)
        base_url = str(fields.get("base_url", "") or "").strip()
        if base_url:
            try:
                host = urllib.parse.urlsplit(base_url).hostname or ""
            except ValueError:
                host = ""
            host = host[4:] if host.startswith("www.") else host
            if host and host.lower() != name.lower():
                parts.append(host)
        if not parts:
            email = str(fields.get("email", "") or "").strip()
            parts.append(email or getattr(provider, "title", provider_id))
        label = " · ".join(parts)[:120]
    taken = {str(acc.label or "").strip().lower() for acc in state.accounts.values()}
    if label.lower() not in taken:
        return label
    for index in range(2, 200):
        candidate = f"{label} {index}"
        if candidate.lower() not in taken:
            return candidate
    return f"{label} {uuid.uuid4().hex[:4]}"


@router.put("/accounts/{account_id}")
async def update_account(account_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    acc = state.get_account(account_id)
    if not acc:
        return _err("Аккаунт не найден", 404)
    body, error = await _json_object(request, max_bytes=1_048_576)
    if error is not None:
        return error
    if "enabled" in body and not isinstance(body["enabled"], bool):
        return _err("Поле enabled должно быть логическим")
    if "priority" in body and body["priority"] not in ("normal", "reserve"):
        return _err("Некорректный приоритет аккаунта")
    label = str(body.get("label", acc.label)).strip()
    proxy = str(body.get("proxy", acc.proxy)).strip()
    if len(label) > 256 or len(proxy) > 4096:
        return _err("Метка или адрес прокси слишком длинные")
    connection_changed = proxy != acc.proxy

    candidate_fields = dict(acc.fields)
    fields_changed = False
    if "fields" in body:
        if not isinstance(body["fields"], dict):
            return _err("Поле fields должно быть JSON-объектом")
        clearable = {"space_id", "project_id", "region", "thread_type", "model_mode",
                     "image_models", "video_models", "music_models", "music_config", "api_key", "api_keys",
                     "key_error_keywords"}
        for raw_key, raw_value in body["fields"].items():
            key = str(raw_key)
            if not key or len(key) > 128 or raw_value is not None and isinstance(raw_value, (dict, list)):
                return _err("Некорректное поле аккаунта")
            value = str(raw_value or "").strip()
            if len(value) > 131_072:
                return _err(f"Поле «{key}» слишком длинное")
            if value == "[REDACTED]":
                continue
            current = str(acc.fields.get(key) or "")
            if len(current) > 14 and "…" in value and value == current[:6] + "…" + current[-4:]:
                # Форма показала сокращённое значение и вернула его как есть.
                continue
            if value or key in clearable:
                candidate_fields[key] = value
        if acc.provider == "genspark" and "cookie" in body["fields"]:
            cookie = str(candidate_fields.get("cookie", "")).strip()
            if cookie and "session_id=" not in cookie.lower():
                candidate_fields["cookie"] = f"session_id={cookie}"
            if any(other.id != acc.id and other.provider == "genspark"
                   and other.fields.get("cookie") == candidate_fields.get("cookie")
                   for other in state.accounts.values()):
                return _err("Такая кука уже добавлена")
        if acc.provider == "custom" and "name" in body["fields"]:
            old_name = str(acc.fields.get("name", "")).strip().lower().replace(" ", "-")
            new_name = str(candidate_fields.get("name", "")).strip().lower().replace(" ", "-")
            if not _MODEL_PREFIX_RE.fullmatch(new_name):
                return _err("Некорректное название подключения")
            if new_name != old_name:
                return _err("Название подключения нельзя изменить; создайте новое подключение")
            candidate_fields["name"] = new_name
        provider = get_provider(acc.provider)
        if provider is None:
            return _err("Провайдер недоступен", 500)
        validation_error = provider.validate_fields(candidate_fields)
        if validation_error:
            return _err(validation_error)
        fields_changed = candidate_fields != acc.fields

    acc.label = label
    acc.proxy = proxy
    if "priority" in body:
        acc.priority = body["priority"]
    if "note" in body:
        acc.note = str(body.get("note") or "")[:2000]
    if "tags" in body:
        if not isinstance(body["tags"], list):
            return _err("Поле tags должно быть списком строк")
        acc.tags = [str(tag).strip()[:40] for tag in body["tags"] if str(tag).strip()][:12]
    if "order" in body:
        try:
            acc.order = max(0, int(body["order"]))
        except (TypeError, ValueError):
            return _err("Поле order должно быть числом")
    if body.get("reset_stats"):
        acc.stats = {"requests": 0, "success": 0, "failures": 0, "total_ms": 0,
                     "last_error": "", "last_error_at": 0.0}
    if fields_changed:
        acc.fields = candidate_fields
        # Токены провайдера берутся из extra раньше полей: без сброса вставленный
        # в «Изменить» свежий токен молча игнорировался.
        edited = set(body.get("fields", {}))
        if acc.provider == "kiro" and edited & {"token_json", "access_token", "refresh_token"}:
            acc.extra.pop("tokens", None)
        if acc.provider == "antigravity" and edited & {"access_token", "refresh_token"}:
            acc.extra.pop("oauth", None)
            acc.extra.pop("expires_at", None)
        if acc.provider == "custom" and any(
                key in body.get("fields", {})
                for key in ("api_key", "api_keys", "key_pool_mode", "key_error_keywords")):
            # Editing the policy is the explicit way to reactivate a pool.
            acc.extra.pop("disabled_api_key_hashes", None)
    if "enabled" in body:
        acc.enabled = body["enabled"]
    if not acc.enabled:
        acc.status = "disabled"
    elif fields_changed or connection_changed or acc.status == "disabled":
        acc.mark(ST_UNKNOWN, "обновлено — нужна проверка")
        pool.clear_account_blocks(acc.id)
    state.save_accounts()
    if acc.provider == "custom" and fields_changed:
        from .music_runtime import model_names
        names = [value.strip() for value in re.split(r"[,\n;]", str(acc.fields.get("models", "") or "")) if value.strip()]
        names = list(dict.fromkeys(names + model_names(acc.fields)))
        # Round 29: declared image/video generators become models of the connection as soon as they are listed.
        names = list(dict.fromkeys(names + [m.strip() for key in ("image_models", "video_models")
                                            for m in re.split(r"[,\n;]", str(acc.fields.get(key, "") or "")) if m.strip()]))
        if names:
            state.seed_provider_models("custom", str(acc.fields.get("name", "")),
                                       [(name, "кастомное подключение") for name in names], account_id=acc.id)
    if acc.provider == "opencode" and (fields_changed or connection_changed or "enabled" in body):
        from .opencode_runtime import runtime
        await asyncio.to_thread(runtime.stop, acc.id)
        from .model_refresh import refresher
        await refresher.refresh(providers=["opencode"], reason="account-updated")
    return {"ok": True, "account": acc.public_dict()}


def _remove_connection_models(acc) -> None:
    """Модели кастомного подключения живут, пока живо само подключение."""
    if acc.provider == "custom":
        prefix = str(acc.fields.get("name", "")).lower()
        for m in [m for m in state.models.values() if m.prefix == prefix]:
            state.remove_model(m.id)
    else:
        _remove_provider_models_if_last(acc)


# Провайдеры, чьи модели сеются на аккаунты: custom обслуживает подключения
# (у каждого свой prefix), local вообще не имеет аккаунтов.
_ACCOUNT_PROVIDERS = frozenset(RESERVED_PREFIXES) - {"custom", "local"}


def _remove_provider_models_if_last(acc) -> None:
    """После удаления аккаунта чистит каталог, если аккаунтов провайдера не осталось.

    Модели встроенных провайдеров сеются на провайдера целиком (account_id=""),
    поэтому живут в каталоге, пока жив хотя бы один их аккаунт. custom и local
    здесь не трогаем: у custom модели привязаны к подключению и удалены выше,
    а local работает без аккаунтов.
    """
    if acc.provider not in _ACCOUNT_PROVIDERS:
        return
    if any(other.id != acc.id and other.provider == acc.provider
           for other in state.accounts.values()):
        return
    doomed = [m.id for m in state.models.values() if m.provider == acc.provider]
    for model_id in doomed:
        state.models.pop(model_id, None)
    if doomed:
        # одна запись на диск: у провайдера могут быть сотни моделей
        state.save_models()


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    acc = state.get_account(account_id)
    if not acc:
        return _err("Аккаунт не найден", 404)
    if acc.provider == "opencode":
        from .opencode_runtime import runtime
        await asyncio.to_thread(runtime.stop, account_id)
    _remove_connection_models(acc)
    if not state.remove_account(account_id):
        return _err("Аккаунт не найден", 404)
    return {"ok": True}


@router.post("/accounts/{account_id}/check")
async def check_account(account_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    acc = state.get_account(account_id)
    if not acc:
        return _err("Аккаунт не найден", 404)
    provider = get_provider(acc.provider)
    if provider is None:
        return _err("Провайдер недоступен", 500)
    # Проверка — явная просьба «попробуй снова»: прежние блокировки моделей этого аккаунта снимаются.
    pool.clear_account_blocks(acc.id)
    try:
        ok, message = await provider.check(acc)
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:400]}"
        acc.last_checked = time.time()
        acc.status_message = message
        state.save_accounts()
        return _err(f"Проверка не удалась: {message}", 502)
    if ok is True:
        acc.mark(ST_OK, message)
    elif ok is False:
        acc.mark("invalid", message)
    else:
        acc.status_message = message
    acc.last_checked = time.time()
    state.save_accounts()
    if acc.provider == "opencode":
        from .model_refresh import refresher
        # check() already fetched the exact account catalogue. Apply it to the
        # shared model list without a second round of upstream requests.
        provider_accounts = [(item, "opencode", "opencode") for item in state.all_accounts()
                             if item.provider == "opencode" and item.usable()]
        rows = [(item, "opencode", "opencode", list(provider._snapshot(item).get("models", {})), "")
                for item, _, _ in provider_accounts if provider._snapshot(item)]
        refresher._apply(rows, provider_accounts, prune=True, wanted={"opencode"})
    return {"ok": bool(ok), "message": message, "account": acc.public_dict()}


@router.post("/accounts/check_all")
async def check_all_accounts(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    results = {"ok": 0, "failed": 0, "skipped": 0}
    for acc in list(state.accounts.values()):
        if not acc.enabled:
            continue
        provider = get_provider(acc.provider)
        if provider is None:
            continue
        try:
            ok, message = await provider.check(acc)
        except Exception as e:
            ok, message = None, f"{type(e).__name__}: {e}"
        acc.last_checked = time.time()
        if ok is True:
            acc.mark(ST_OK, message)
            results["ok"] += 1
        elif ok is False:
            acc.mark("invalid", message)
            results["failed"] += 1
        else:
            acc.status_message = message
            results["skipped"] += 1
        state.save_accounts()
        await asyncio.sleep(0.3)
    return {"ok": True, **results}


@router.post("/accounts/reorder")
async def reorder_accounts(request: Request):
    """Порядок аккаунтов после перетаскивания в панели.

    Без поля ``provider`` список считается глобальным: карточки можно
    переставлять между провайдерами, а приоритет внутри провайдера по-прежнему
    задаётся относительным положением карточек.
    """
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=262_144)
    if error is not None:
        return error
    provider_id = str(body.get("provider", "")).strip()
    order = body.get("order")
    if order is None:
        order = body.get("ids")
    if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
        return _err("Поле order/ids должно быть списком идентификаторов")
    if len(order) > 500:
        return _err("Слишком длинный список")
    if provider_id:
        changed = state.reorder_accounts(provider_id, order)
    else:
        changed = state.reorder_accounts_global(order)
    return {"ok": True, "changed": changed, "accounts": state.list_accounts(mask=True)}


@router.post("/accounts/bulk")
async def bulk_accounts(request: Request):
    """Массовые действия: enable | disable | check | delete | priority."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=262_144)
    if error is not None:
        return error
    action = str(body.get("action", "")).strip()
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _err("Выберите хотя бы один аккаунт")
    if len(ids) > 500:
        return _err("Слишком много аккаунтов за раз")
    allowed = {"enable", "disable", "check", "delete",
               "priority_normal", "priority_reserve"}
    if action not in allowed:
        return _err(f"Неизвестное действие «{action}»")

    done, failed = 0, 0
    messages: list[str] = []
    for account_id in ids:
        acc = state.get_account(str(account_id))
        if acc is None:
            failed += 1
            continue
        try:
            if action == "delete":
                _remove_connection_models(acc)
                state.remove_account(acc.id)
            elif action == "enable":
                acc.enabled = True
                if acc.status == "disabled":
                    acc.mark(ST_UNKNOWN, "включён — нужна проверка")
            elif action == "disable":
                acc.enabled = False
                acc.status = "disabled"
            elif action.startswith("priority_"):
                acc.priority = action.split("_", 1)[1]
            elif action == "check":
                provider = get_provider(acc.provider)
                if provider is None:
                    failed += 1
                    continue
                pool.clear_account_blocks(acc.id)
                ok, message = await provider.check(acc)
                acc.last_checked = time.time()
                if ok is True:
                    acc.mark(ST_OK, message)
                elif ok is False:
                    acc.mark("invalid", message)
                else:
                    acc.status_message = message
                    messages.append(f"{acc.label or acc.id}: {message}")
                await asyncio.sleep(0.2)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            messages.append(f"{acc.label or acc.id}: {type(exc).__name__}: {exc}")
    state.save_accounts()
    return {"ok": True, "done": done, "failed": failed, "messages": messages[:20],
            "accounts": state.list_accounts(mask=True)}


@router.post("/accounts/{account_id}/duplicate")
async def duplicate_account(account_id: str, request: Request):
    """Копия аккаунта со всеми полями — удобно для второй куки того же сервиса."""
    deny = _auth(request)
    if deny:
        return deny
    source = state.get_account(account_id)
    if source is None:
        return _err("Аккаунт не найден", 404)
    if source.provider == "custom":
        return _err("Кастомное подключение копировать нельзя: имя префикса должно быть уникальным")
    label = (source.label or source.provider) + " (копия)"
    acc = state.add_account(source.provider, dict(source.fields), label=label[:256],
                            proxy=source.proxy, extra=json.loads(json.dumps(source.extra)))
    acc.priority = source.priority
    acc.note = source.note
    acc.tags = list(source.tags)
    acc.order = state.next_order(source.provider)
    acc.mark(ST_UNKNOWN, "копия — нужна проверка")
    state.save_accounts()
    return {"ok": True, "account": acc.public_dict()}


@router.get("/accounts/export")
async def export_accounts(request: Request):
    """Выгрузка аккаунтов без секретов — для переноса структуры и заметок."""
    deny = _auth(request)
    if deny:
        return deny
    rows = []
    for acc in sorted(state.accounts.values(), key=lambda a: (a.provider, a.order)):
        rows.append({"provider": acc.provider, "label": acc.label, "priority": acc.priority,
                     "order": acc.order, "note": acc.note, "tags": list(acc.tags),
                     "enabled": acc.enabled, "proxy": acc.proxy,
                     "fields": {key: "" for key in acc.fields}})
    return {"version": APP_VERSION, "exported": int(time.time()), "accounts": rows}


@router.get("/accounts/{account_id}/secrets")
async def account_secrets(account_id: str, request: Request):
    """Настоящие значения полей аккаунта для формы редактирования.

    Панель живёт на loopback за паролем, а править ключ или куку, не видя их,
    невозможно: приходилось удалять аккаунт и заводить заново.
    """
    deny = _auth(request)
    if deny:
        return deny
    acc = state.get_account(account_id)
    if not acc:
        return _err("Аккаунт не найден", 404)
    return {"id": acc.id, "fields": dict(acc.fields), "proxy": acc.proxy}


@router.get("/accounts/{account_id}/usage")
async def account_usage(account_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    acc = state.get_account(account_id)
    if not acc:
        return _err("Аккаунт не найден", 404)
    provider = get_provider(acc.provider)
    if provider is None:
        return _err("Провайдер недоступен", 500)
    try:
        usage = await provider.usage(acc)
    except Exception as e:
        return _err(f"Не удалось получить лимиты: {e}", 502)
    try:  # Round 35 (quota): every look at the limits is also a sample for the quota planner
        from . import quota_planner
        quota_planner.observe_usage(acc, usage)
    except Exception:
        pass
    return {"usage": usage, "provider": acc.provider}


# ------------------------------ OAuth ------------------------------

@router.post("/oauth/{provider_id}/start")
async def oauth_start(provider_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    provider = get_provider(provider_id)
    if provider is None or not provider.oauth:
        return _err("Провайдер не поддерживает вход через браузер", 400)
    try:
        result = await provider.oauth_start()
    except Exception as e:
        return _err(f"Не удалось начать вход: {e}", 500)
    if not result.get("ok"):
        return _err(result.get("message", "Не удалось начать вход"), 500)
    return result


@router.post("/oauth/{provider_id}/device_start")
async def oauth_device_start(provider_id: str, request: Request):
    """Device-code sign-in (Kiro Builder ID): no local callback port needed."""
    deny = _auth(request)
    if deny:
        return deny
    provider = get_provider(provider_id)
    start = getattr(provider, "oauth_device_start", None) if provider is not None else None
    if start is None:
        return _err("Провайдер не поддерживает вход по коду", 400)
    try:
        result = await start()
    except Exception as e:
        return _err(f"Не удалось начать вход: {e}", 500)
    if not result.get("ok"):
        return _err(result.get("message", "Не удалось начать вход"), 502)
    return result


@router.get("/oauth/{provider_id}/status/{flow_id}")
async def oauth_status(provider_id: str, flow_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    provider = get_provider(provider_id)
    if provider is None:
        return _err("Провайдер не найден", 404)
    return await provider.oauth_status(flow_id)


# ------------------------------ модели ------------------------------

@router.get("/models")
async def list_models(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    rows = state.list_models()
    from .music_runtime import is_music_model
    for row in rows:
        if is_music_model(row.get("id")):
            row["caps"] = {"music": True, "text": False}
        try:
            info = model_info.resolve(row.get("prefix", ""), row.get("provider", ""), row.get("model", ""))
            if info.get("context"):
                row["context"] = {key: info.get(key) for key in ("context", "output", "source", "label") if info.get(key)}
        except Exception:
            pass
    return {"models": rows}


@router.post("/models")
async def upsert_model(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=65_536)
    if error is not None:
        return error
    prefix = str(body.get("prefix", "")).strip().lower().replace(" ", "-")
    model = str(body.get("model", "")).strip()
    if not prefix or not model:
        return _err("Укажите префикс и модель")
    if not _MODEL_PREFIX_RE.fullmatch(prefix):
        return _err("Некорректный префикс модели")
    if len(model) > 512 or any(ord(char) < 32 for char in model):
        return _err("Некорректное имя модели")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        return _err("Поле enabled должно быть логическим")
    note = str(body.get("note", ""))
    if len(note) > 2000:
        return _err("Примечание слишком длинное")
    existing = state.models.get(f"{prefix}-{model}")
    if existing:
        if "enabled" in body:
            existing.enabled = bool(body["enabled"])
        if "note" in body:
            existing.note = note
        state.save_models()
        return {"ok": True, "model": existing.public_dict()}
    provider = _provider_of_prefix(prefix)
    entry = ModelEntry(prefix, provider, model, enabled=body.get("enabled", True),
                       source="manual", note=note)
    if not state.model_has_owner(entry):
        # Иначе модель сохранялась «в никуда»: её некому обслужить и список её не покажет.
        return _err("Сначала добавьте аккаунт или подключение с этим префиксом")
    state.upsert_model(entry)
    return {"ok": True, "model": entry.public_dict()}


def _provider_of_prefix(prefix: str) -> str:
    for acc in state.accounts.values():
        if acc.provider == "custom" and str(acc.fields.get("name", "")).lower() == prefix:
            return "custom"
    return prefix if prefix in all_providers() else "custom"


@router.delete("/models/{model_id:path}")
async def delete_model(model_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    if not state.remove_model(model_id):
        return _err("Модель не найдена", 404)
    return {"ok": True}


@router.post("/models/refresh")
async def refresh_models(request: Request):
    """Live model discovery. Optional body: {"providers": ["kiro", ...],
    "accounts": ["id", ...], "prune": true}.

    prune (по умолчанию включён) убирает осиротевшие модели и устаревшие
    автоматические записи после успешного полного discovery всех аккаунтов
    префикса. Ручные модели и локальный каталог сохраняются.
    """
    deny = _auth(request)
    if deny:
        return deny
    body: Dict[str, Any] = {}
    if request.headers.get("content-length") not in (None, "", "0"):
        body, error = await _json_object(request, max_bytes=65_536)
        if error is not None:
            return error
    for key in ("providers", "accounts"):
        value = body.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            return _err(f"{key}: ожидается список строк")
    if "prune" in body and not isinstance(body["prune"], bool):
        return _err("prune: ожидается логическое значение")
    wanted = {str(x).strip().lower() for x in (body.get("providers") or []) if str(x).strip()}
    only_accounts = {str(x) for x in (body.get("accounts") or []) if str(x)}
    from .model_refresh import refresher
    return await refresher.refresh(providers=wanted, accounts=only_accounts,
                                   prune=body.get("prune", True))


def _prune_orphan_models(wanted: set) -> int:
    """Обещанный docstring prune: чистит каталог от осиротевших моделей.

    Удаляются модели провайдеров, у которых не осталось живых аккаунтов (в том
    числе давно убранных из приложения, вроде Notion). custom (direct-API
    подключения) и local (локальные модели) не трогаем: их каталог живёт по
    своим правилам и не зависит от аккаунтов.
    """
    return state.prune_orphan_models(wanted)


def _apply_hidden_keywords() -> int:
    """Disables every model whose id matches a configured hide keyword."""
    words = [w.lower() for w in (config_store.settings.model_hidden_keywords or []) if w]
    if not words:
        return 0
    changed = 0
    for entry in list(state.models.values()):
        if not entry.enabled:
            continue
        haystack = f"{entry.prefix}/{entry.model}".lower()
        if any(w in haystack for w in words):
            entry.enabled = False
            changed += 1
    if changed:
        state.save_models()
    return changed


@router.post("/models/bulk")
async def bulk_models(request: Request):
    """Bulk actions over selected models: enable / disable / delete / note."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=1_048_576)
    if error is not None:
        return error
    action = str(body.get("action", "")).strip().lower()
    ids = [str(x) for x in (body.get("ids") or []) if str(x)]
    if action not in ("enable", "disable", "delete", "note"):
        return _err("Unknown bulk action")
    if not ids:
        return _err("No models selected")
    if len(ids) > 5000:
        return _err("Too many models selected")
    note = str(body.get("note", ""))[:2000]
    done = 0
    for model_id in ids:
        entry = state.models.get(model_id)
        if action == "delete":
            if state.remove_model(model_id):
                done += 1
            continue
        if entry is None:
            continue
        if action == "enable":
            entry.enabled = True
        elif action == "disable":
            entry.enabled = False
        else:
            entry.note = note
        done += 1
    if action != "delete":
        state.save_models()
    return {"ok": True, "count": done}


@router.post("/models/hide_keywords")
async def hide_by_keywords(request: Request):
    """Disable/delete models by keyword, optionally saving the keyword rules."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=65_536)
    if error is not None:
        return error
    words = [str(w).strip().lower() for w in (body.get("keywords") or []) if str(w).strip()]
    words = words[:64]
    mode = str(body.get("mode", "disable")).strip().lower()
    if mode not in ("disable", "enable", "delete"):
        return _err("Unknown mode")
    if body.get("save"):
        config_store.apply_updates({"model_hidden_keywords": words})
    if not words:
        return {"ok": True, "count": 0, "keywords": config_store.settings.model_hidden_keywords}
    matched = [m.id for m in list(state.models.values())
               if any(w in f"{m.prefix}/{m.model}".lower() for w in words)]
    count = 0
    for model_id in matched:
        if mode == "delete":
            if state.remove_model(model_id):
                count += 1
            continue
        entry = state.models.get(model_id)
        if entry is None:
            continue
        entry.enabled = (mode == "enable")
        count += 1
    if mode != "delete":
        state.save_models()
    return {"ok": True, "count": count,
            "keywords": config_store.settings.model_hidden_keywords}


@router.get("/models/export")
async def export_models(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    payload = {"exported_at": time.time(), "models": state.list_models()}
    return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=memuai-models.json"})


@router.post("/models/import")
async def import_models(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=4_194_304)
    if error is not None:
        return error
    rows = body.get("models")
    if not isinstance(rows, list):
        return _err("Field models must be an array")
    added = skipped = 0
    for row in rows[:5000]:
        if not isinstance(row, dict):
            continue
        prefix = str(row.get("prefix", "")).strip().lower().replace(" ", "-")
        model = str(row.get("model", "")).strip()
        if not prefix or not model or not _MODEL_PREFIX_RE.fullmatch(prefix):
            continue
        if f"{prefix}-{model}" in state.models:
            continue
        entry = ModelEntry(
            prefix, _provider_of_prefix(prefix), model,
            enabled=bool(row.get("enabled", True)), source="import",
            note=str(row.get("note", ""))[:2000],
            account_id=str(row.get("account_id", "")),
        )
        if not state.model_has_owner(entry):
            # Экспорт из времени, когда был аккаунт Genspark (или другое
            # подключение), не должен возвращать модели без аккаунта.
            skipped += 1
            continue
        state.models[entry.id] = entry
        added += 1
    if added:
        state.save_models()
    return {"ok": True, "added": added, "skipped": skipped}


@router.post("/models/genspark/refresh_config")
async def genspark_models_config(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    gp = get_provider("genspark")
    if gp is None:
        return _err("Провайдер недоступен", 500)
    try:
        sections = await gp.refresh_models_config()
    except Exception as e:
        return _err(f"/api/models_config недоступен: {e}", 502)
    from .storage import save_json
    save_json(str(_memu_file("media_models.json")), sections, backup=False)
    total = sum(len(v) for v in sections.values())
    return {"ok": True, "sections": sections, "total": total}


@router.post("/models/genspark/discover")
async def genspark_discover(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    gp = get_provider("genspark")
    if gp is None:
        return _err("Провайдер недоступен", 500)
    try:
        res = await gp.discover_models()
    except Exception as e:
        return _err(f"Сканирование не удалось: {e}", 502)
    ids = res.get("ids") or []
    known = {m.model for m in state.models.values() if m.prefix == "genspark"}
    added = state.seed_provider_models(
        "genspark", "genspark",
        [(i, f"найдено в бандле {res.get('date', '')}") for i in ids if i not in known])
    return {"ok": True, "added": added, "chunks": res.get("chunks", 0),
            "errors": res.get("errors", 0), "total_found": len(ids)}


# ------------------------------ playground ------------------------------

@router.get("/playground")
async def pg_list(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .config import DATA_DIR
    import os as _os
    d = _os.path.join(DATA_DIR, "playground")
    items = []
    if _os.path.isdir(d):
        for f in sorted(_os.listdir(d), reverse=True):
            if f.endswith(".json"):
                try:
                    items.append(json.loads(open(_os.path.join(d, f), encoding="utf-8").read()))
                except Exception:
                    pass
    return {"chats": items[:50]}


@router.post("/playground")
async def pg_save(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=8 * 1024 * 1024)
    if error is not None:
        return error
    chat_id = str(body.get("id") or uuid.uuid4().hex[:12])
    try:
        path = _playground_chat_path(chat_id)
    except ValueError as exc:
        return _err(str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    body["id"] = chat_id
    body["updated"] = time.time()
    from .storage import save_json
    save_json(path, body, backup=False)
    return {"ok": True, "id": chat_id}


@router.delete("/playground/{chat_id}")
async def pg_delete(chat_id: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    try:
        path = _playground_chat_path(chat_id)
    except ValueError as exc:
        return _err(str(exc))
    if path.exists():
        path.unlink()
    return {"ok": True}


@router.post("/websearch")
async def admin_websearch(request: Request):
    """Веб-поиск для MemuAI: Kiro (если есть аккаунт), иначе движок из настроек."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=64 * 1024)
    if error is not None:
        return error
    query = str(body.get("query") or "").strip()
    if not query:
        return _err("Нужен непустой query")
    try:
        limit = max(1, min(10, int(body.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    from . import websearch
    # MemuAI может попросить именно бесплатный режим или Kiro: аккаунты живут здесь.
    engine = str(body.get("engine") or "")
    try:
        results = await websearch.search(query, limit=limit, raise_errors=True,
                                         proxy=config_store.settings.default_proxy,
                                         engine=engine if engine in ("auto", "kiro") else "")
    except Exception as exc:
        return _err(f"Поиск не удался: {exc}", 502)
    return {"query": query, "results": results, "count": len(results)}


@router.get("/search/engines")
async def admin_search_engines(request: Request):
    """Каталог источников веб-поиска для окна настроек."""
    deny = _auth(request)
    if deny:
        return deny
    from . import search_engines
    return {"engines": search_engines.catalog(), "default": search_engines.DEFAULT_ENGINE}


@router.post("/search/test")
async def admin_search_test(request: Request):
    """Пробный запрос выбранным источником: сразу видно, работает ли ключ."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=16 * 1024)
    if error is not None:
        return error
    from . import search_engines, websearch
    engine, api_key, base_url = websearch.configured()
    if not search_engines.ready(engine, api_key, base_url):
        return _err("Источник не настроен: нужен ключ или адрес")
    started = time.time()
    try:
        results = await websearch.run_engine(
            engine, str(body.get("query") or "").strip()[:200] or "weather today", 3,
            proxy=config_store.settings.default_proxy, api_key=api_key, base_url=base_url)
    except Exception as exc:
        return _err(str(exc)[:400] or type(exc).__name__, 502)
    return {"ok": True, "engine": engine, "count": len(results), "ms": int((time.time() - started) * 1000),
            "first": results[0] if results else None}


#: Round 32: messages per Playground/MemuCode request (was 500 — too few for a long agent session).
PG_CHAT_MAX_MESSAGES = 5000


@router.post("/playground/chat")
async def pg_chat(request: Request):
    """Чат Playground: стрим SSE из ядра хаба (авторизация панели)."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=32 * 1024 * 1024)
    if error is not None:
        return error
    messages = body.get("messages") or []
    model = str(body.get("model", "")).strip()
    if not isinstance(messages, list) or not messages or not model:
        return _err("Нужны model и messages")
    # Round 32: a long MemuCode session easily holds more than 500 messages (each tool call and result is
    # one); the old cap answered «Слишком большой запрос», which read like a size problem. The cap stays as
    # a sanity bound and names exactly what is too big.
    if len(messages) > PG_CHAT_MAX_MESSAGES:
        return _err(f"Слишком много сообщений в запросе: {len(messages)} (предел {PG_CHAT_MAX_MESSAGES})", 413)
    if len(model) > 512:
        return _err("Слишком длинный идентификатор модели")
    params = {"web_search": body.get("web_search"), "thinking": body.get("thinking"),
              # MemuAI умеет вызывать инструменты сам, поэтому прокси не должен
              # подмешивать веб-поиск к каждому сообщению.
              "tools_available": bool(body.get("tools_available")),
              "prompt_caching": body.get("prompt_caching", True), "_stream": True}
    # Round 33 (r33-thinking): + off / on / xhigh; the Proxy clamps the level to the model (reasoning_levels).
    if str(body.get("effort") or "").lower() in ("auto", "off", "none", "on", "minimal", "low", "medium", "high",
                                                  "xhigh", "max"):
        params["effort"] = str(body["effort"]).lower()
    if body.get("temperature") is not None:
        params["temperature"] = body["temperature"]
    # Round 29: sampler passthrough for modules (Memu.complete); numbers/strings only.
    for key in ("top_p", "top_k", "min_p", "top_a", "frequency_penalty", "presence_penalty",
                "repetition_penalty", "seed"):
        if isinstance(body.get(key), (int, float)) and not isinstance(body.get(key), bool):
            params[key] = body[key]
    if isinstance(body.get("stop"), list):
        params["stop"] = [str(item)[:200] for item in body["stop"] if isinstance(item, str) and item][:16]
    try:
        max_tokens = int(body.get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens > 0:
        params["max_tokens"] = min(max_tokens, 1_000_000)

    from .chat import CompletionError, chat_service
    import time as _t

    trace = state.open_trace(model=model)
    trace.capture_prompt(messages, tools=params.get("tools"))
    started = _t.time()

    async def sse():
        usage = {}
        status, error = 200, ""
        error_en, error_code = "", ""
        stream = chat_service.stream_completion(model, messages, params, trace=trace)
        try:
            # Пока провайдер думает или идёт повтор, канал молчит. MemuAI
            # не отличает «думает» от «связь умерла», поэтому шлём
            # SSE-комментарии — они не видны клиенту, но доказывают, что
            # запрос жив.
            keepalive = (float(config_store.settings.sse_keepalive_interval)
                         if config_store.settings.sse_keepalive else 0.0)
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            sentinel = object()

            async def pump() -> None:
                try:
                    async for item in stream:
                        await queue.put(item)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 — пробрасываем потребителю
                    await queue.put(exc)
                else:
                    await queue.put(sentinel)

            pump_task = asyncio.create_task(pump())
            try:
                while True:
                    if keepalive > 0:
                        try:
                            chunk = await asyncio.wait_for(queue.get(), timeout=keepalive)
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                    else:
                        chunk = await queue.get()
                    if chunk is sentinel:
                        break
                    if isinstance(chunk, BaseException):
                        raise chunk
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            finally:
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await stream.aclose()
                except Exception:
                    pass
        except CompletionError as e:
            status, error = e.status, e.message
            error_en, error_code = getattr(e, "message_en", "") or e.message, e.err_type
            yield "data: " + json.dumps({"error": {
                "message": e.message, "message_en": error_en,
                "hint": e.hint, "hint_en": e.hint_en,
                "type": e.err_type, "status": e.status,
                "provider": getattr(e, "provider", "") or "",
                # Round 33: the upstream's pause (Retry-After) for MemuCode's /auto-continue auto
                "retry_after": getattr(e, "retry_after", None)}},
                ensure_ascii=False) + "\n\n"
        except Exception as e:
            status, error = 500, str(e)
            error_en, error_code = str(e), "internal_error"
            yield "data: " + json.dumps({"error": {"message": str(e)}}, ensure_ascii=False) + "\n\n"
        trace.status = status
        trace.error = error
        trace.error_en = error_en
        trace.error_code = error_code
        trace.ms = int((_t.time() - started) * 1000)
        trace.prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        trace.completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        if status != 200:
            trace.add_stage("finished", {"error": error, "status": status})
        if config_store.settings.log_requests or status != 200:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        yield "data: [DONE]\n\n"

    return ClosingStreamingResponse(state.guard_stream(sse(), trace), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/open_memuai")
async def open_memuai(request: Request):
    """Открывает или активирует единственное окно MemuAI."""
    deny = _auth(request)
    if deny:
        return deny
    if os.environ.get("MEMUAI_PROXY_ONLY") == "1":
        return _err("Недоступно в отдельной версии Proxy", 404)
    import urllib.parse
    import httpx

    runtime = _load_json(_memu_file("memuai.json"), {})
    running_url = str(runtime.get("url", "")).rstrip("/")
    parsed = urllib.parse.urlparse(running_url)
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        try:
            # trust_env=False: a system/VPN proxy (env or the Windows registry) must not see a 127.0.0.1 ping.
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                ping = await client.get(running_url + "/api/pg/ping")
                if ping.status_code == 200:
                    await client.post(running_url + "/api/pg/activate", json={})
                    return {"ok": True, "mode": "existing"}
        except Exception:
            pass

    import subprocess
    import sys
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    _internal_sessions.add(token)
    proxy_host = config_store.effective_host
    if proxy_host == "0.0.0.0":
        proxy_host = "127.0.0.1"
    elif proxy_host == "::":
        proxy_host = "::1"
    if ":" in proxy_host and not proxy_host.startswith("["):
        proxy_host = f"[{proxy_host}]"
    proxy_url = f"http://{proxy_host}:{config_store.effective_port}"
    root = os.path.dirname(os.path.abspath(sys.argv[0]))
    frozen = getattr(sys, "frozen", False)
    exe_dir = os.path.dirname(sys.executable) if frozen else root

    candidates = []
    if frozen:
        candidates.append(os.path.join(exe_dir, "MemuAI.exe"))
        candidates.append(os.path.join(os.path.dirname(exe_dir), "MemuAI", "MemuAI.exe"))
    py = os.path.join(os.path.dirname(sys.executable),
                      "pythonw.exe" if os.name == "nt" else "python")
    if not os.path.exists(py):
        py = sys.executable
    candidates.append((py, os.path.join(root, "memuai.py")))

    errors = []
    child_env = os.environ.copy()
    child_env["MEMUAI_TOKEN"] = token
    for cand in candidates:
        try:
            if isinstance(cand, tuple):
                py, script = cand
                if os.path.exists(script):
                    subprocess.Popen([py, script, "--proxy", proxy_url],
                                     cwd=root, env=child_env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    return {"ok": True, "mode": "source"}
            elif os.path.exists(cand):
                subprocess.Popen([cand, "--proxy", proxy_url],
                                 cwd=os.path.dirname(cand), env=child_env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                return {"ok": True, "mode": "exe"}
        except Exception as e:
            errors.append(f"{cand}: {e}")
    _sessions.pop(token, None)
    _internal_sessions.discard(token)
    return _err("MemuAI не найден: " + "; ".join(errors or ["dist/MemuAI/MemuAI.exe"]), 500)


@router.post("/show_window")
async def show_proxy_window(request: Request):
    """Активирует окно из второго запуска приложения/ярлыка."""
    if not _client_is_loopback(request):
        return _err("Активация окна разрешена только локальному процессу", 403)
    browser_denied = _browser_origin_guard(request)
    if browser_denied:
        return browser_denied
    from .desktop import show_active_window
    shown = show_active_window()
    # Round 35 (protocol): memuai://proxy/<page> — open a page of the Proxy window ({"tab": "composite"}).
    try:
        raw = await request.body()
        tab = str((json.loads(raw) if raw and len(raw) < 1024 else {}).get("tab") or "")
    except (ValueError, AttributeError):
        tab = ""
    if shown and re.fullmatch(r"[a-z][a-z_-]{0,23}", tab):
        from . import desktop as _desktop
        window = getattr(getattr(_desktop, "_active_shell", None), "window", None)
        if window is not None:
            script = "document.querySelector('.nav-item[data-tab=\"%s\"]')?.click()" % tab
            await asyncio.to_thread(lambda: _safe_eval(window, script))
    return {"ok": shown}


def _safe_eval(window, script: str) -> None:
    try:
        window.evaluate_js(script)
    except Exception:  # noqa: BLE001 - the window may be closing
        pass


@router.post("/shutdown_local")
async def shutdown_proxy_local(request: Request):
    """Graceful shutdown for the signed per-user uninstaller helper."""
    if not _client_is_loopback(request) or _browser_origin_guard(request):
        return _err("Локальное завершение запрещено", 403)
    token = str(request.headers.get("x-memuai-shutdown-token") or "")
    expected = str(getattr(request.app.state, "shutdown_token", "") or "")
    if not expected or not token or not secrets.compare_digest(token, expected):
        return _err("Недопустимый токен завершения", 403)
    callback = getattr(request.app.state, "shutdown_callback", None)
    if callback:
        callback()
    return {"ok": True}


# ------------------------------ playground: state/memory/prompts/schedules/image ------------------------------

def _memu_file(name: str) -> pathlib.Path:
    from .config import DATA_DIR
    import pathlib as _pl
    d = _pl.Path(DATA_DIR) / "memuai"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _custom_account_label(prefix: str) -> str:
    """The user's name for the Custom API account that owns ``prefix`` (its label, else its name)."""
    for account in state.accounts_of("custom"):
        name = str(account.fields.get("name", "")).strip()
        if name.lower().replace(" ", "-") == str(prefix).lower():
            return (account.label or name or "custom")[:40]
    return "custom"


@router.get("/pg/state")
async def pg_state(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    models = []
    from .music_runtime import models as music_catalog
    music_rows = music_catalog()
    music_ids = {row["id"] for row in music_rows}
    usable_providers = {pid for pid in all_providers() if state.usable_accounts(pid)}
    # Кэш медиакаталога Genspark переживает удаление аккаунта: без живого
    # аккаунта его модели AI Image не показываем (их некому обслужить).
    genspark_live = bool(state.usable_accounts("genspark"))
    sections = _load_json(_memu_file("media_models.json"), {}) if genspark_live else {}
    if not isinstance(sections, dict):
        sections = {}
    raw_image_models = [str(x) for x in (sections.get("image") or sections.get("image_models") or [])
                        if str(x) and str(x) != "auto"]
    custom_image_models = []
    declared_custom_images = set()
    for account in state.usable_accounts("custom"):
        prefix = str(account.fields.get("name", "")).strip().lower().replace(" ", "-")
        declared = re.split(r"[,\n;]", str(account.fields.get("image_models", "") or ""))
        declared_custom_images.update(f"{prefix}-{name.strip()}" for name in declared if name.strip())
    if genspark_live:
        raw_image_models.insert(0, "auto")
    image_hints = ("gpt-image", "dall-e", "dalle", "imagen", "flux", "seedream",
                   "recraft", "ideogram", "qwen-image", "nano-banana", "imagegen",
                   "stable-diffusion", "sdxl", "kolors")
    for enabled in state.enabled_models():
        raw = str(enabled.model)
        if enabled.provider == "genspark" and genspark_live and any(h in raw.lower() for h in image_hints):
            raw_image_models.append(raw)
        elif enabled.provider == "custom" and (enabled.id in declared_custom_images
                                                or any(h in raw.lower() for h in image_hints)):
            custom_image_models.append(enabled.id)
    # Round 29 (media routing): video models of Custom connections (declared or known by name) and Genspark AI Video;
    # chat-native image names (gemini-*-image, *-image-preview) as image models (app/media_gen.py).
    custom_video_models: List[str] = []
    try:
        from . import media_gen
        extra_images, custom_video_models = media_gen.custom_media_ids(
            [e for e in state.enabled_models() if e.provider in usable_providers], state.usable_accounts("custom"))
        custom_image_models = [x for x in custom_image_models if x not in custom_video_models]
        custom_image_models += [x for x in extra_images if x not in custom_image_models]
    except Exception:
        custom_video_models = []
    raw_video_models = [str(x) for x in (sections.get("video") or sections.get("video_models") or [])
                        if str(x) and str(x) != "auto"] if genspark_live else []
    raw_image_models = list(dict.fromkeys(raw_image_models))
    image_keys = {x.lower() for x in raw_image_models}
    for m in state.enabled_models():
        if m.provider not in usable_providers:
            continue
        # Медиамодели Genspark принадлежат агенту AI Image, а не обычному чату.
        if m.provider == "genspark" and (m.model.lower() in image_keys or m.id.lower() in image_keys):
            continue
        if m.provider == "custom" and (m.id in custom_image_models or m.id in custom_video_models):
            continue
        if m.id in music_ids:
            continue
        # These providers take pictures, but not every model behind them does (a text-only DeepSeek on a custom
        # endpoint, GPT-OSS on Antigravity): the model's family decides when the name is conclusive.
        accepts = m.provider in ("genspark", "antigravity", "kiro", "custom")
        if accepts and model_info.vision_hint(m.model) is False:
            accepts = False
        extra_caps: Dict[str, Any] = {}
        try:
            # Round 22: one shared answer (app/model_capabilities.py): a probe the user ran, a local mmproj
            # projector, the provider's own model row and the public catalogues can correct the name rule.
            from . import model_capabilities
            accepts = model_capabilities.adapter_vision(m.id, m.prefix, m.provider, m.model, entry=m)
            summary = model_capabilities.capabilities_for(m.id, entry=m)
            for key in ("tools", "reasoning"):
                if summary.get(key) is True:
                    extra_caps[key] = True
            # Round 31 (r31-quick): «text only» guessed from the name alone — the window lets the user try / probe
            # instead of refusing a picture outright (a new multimodal tier the name table does not know yet).
            if not accepts and summary.get("vision_source") == "heuristic" \
                    and m.provider in model_capabilities.IMAGE_ADAPTERS:
                extra_caps["vision_guess"] = True
        except Exception:
            pass
        caps = {"search": False, "thinking": False, "image": False, "vision": accepts, **extra_caps}
        try:
            # Round 33 (r33-thinking): the reasoning levels this model really supports (pickers show only these).
            from . import reasoning_levels
            caps["reasoning_profile"] = reasoning_levels.public(reasoning_levels.profile(
                m.id, m.provider, m.model, reasoning=extra_caps.get("reasoning")))
        except Exception:
            pass
        if m.provider == "genspark":
            caps["search"] = True
        row = {"id": m.id, "provider": m.provider, "caps": caps}
        if m.availability:
            row["availability"] = dict(m.availability)
        if m.provider == "custom" and m.prefix:
            # Round 31 (design): the model picker showed «custom» for every own API — name the account instead.
            row["provider_label"] = _custom_account_label(m.prefix)
        try:
            # Context window for MemuAI's meter and compression: provider
            # metadata, then the local runtime, then the known-model catalogue.
            info = model_info.resolve(m.prefix, m.provider, m.model)
            if info.get("context"):
                row["context"] = {key: info.get(key) for key in
                                  ("context", "output", "source", "label", "canonical") if info.get(key)}
        except Exception:
            pass
        models.append(row)
    # Никаких внешних запросов при обновлении интерфейса: медиакаталог
    # обновляется только явной кнопкой в Proxy и читается из локального кэша.
    if genspark_live and len(raw_image_models) <= 1:
        raw_image_models = ["auto", "gpt-image-2", "nano-banana-pro", "nano-banana-2",
                            "flux-2-pro", "ideogram-v3", "recraft-v3",
                            "seedream", "qwen-image-2"]
    image_models = ["genspark-image/" + name for name in raw_image_models]
    for image_id, raw_name in zip(image_models, raw_image_models):
        models.append({"id": image_id, "provider": "Genspark · AI Image",
                       "raw_model": raw_name,
                       "caps": {"search": False, "thinking": False, "image": True,
                                "vision": True}})
    for custom_id in dict.fromkeys(custom_image_models):
        image_models.append(custom_id)
        entry = state.find_model(custom_id)
        label = entry.prefix if entry else "Custom API"
        models.append({"id": custom_id, "provider": f"{label} · Images",
                       "caps": {"search": False, "thinking": False, "image": True,
                                "vision": False}})
    video_models = ["genspark-video/" + name for name in dict.fromkeys(raw_video_models)]
    for video_id in video_models:
        models.append({"id": video_id, "provider": "Genspark · AI Video", "raw_model": video_id.split("/", 1)[1],
                       "caps": {"search": False, "thinking": False, "image": False, "video": True, "vision": False}})
    for custom_id in dict.fromkeys(custom_video_models):
        video_models.append(custom_id)
        entry = state.find_model(custom_id)
        label = entry.prefix if entry else "Custom API"
        models.append({"id": custom_id, "provider": f"{label} · Video",
                       "caps": {"search": False, "thinking": False, "image": False, "video": True, "vision": False}})
    mem = _load_json(_memu_file("memory.json"), {"enabled": True, "facts": []})
    if isinstance(mem, dict) and isinstance(mem.get("items"), list):
        # Round 29: memory v2 — the 3-second poll carries only the legacy view and the inbox size.
        mem = {"enabled": mem.get("enabled", True) is not False, "facts": mem.get("facts") or [], "version": 2,
               "pending": sum(1 for row in mem["items"] if isinstance(row, dict) and row.get("status") == "pending")}
    models.extend(music_rows)
    try:   # Round 35: composite models for the MemuAI pickers (app/composite_models.py)
        from . import composite_models
        models.extend(composite_models.pg_rows())
    except Exception:
        log.exception("composite models: pg rows failed")
    unavailable_models = [entry.public_dict() for entry in state.catalog_models()
                          if entry.enabled and entry.provider in usable_providers
                          and entry.availability.get("available") is False]
    payload = {"models": models, "unavailable_models": unavailable_models,
               "image_models": image_models, "video_models": video_models,
               "music_models": list(music_ids), "memory": mem}
    try:   # Round 33 (r33-models): names, kind, account and one vision answer per row (app/model_catalog.py)
        from . import model_catalog
        model_catalog.enrich(payload, find_model=state.find_model)
        model_catalog.enrich({"models": unavailable_models}, find_model=state.find_model)
    except Exception:
        log.exception("model catalog enrich failed")
    # Round 21 (производительность): окно запрашивает состояние каждые 3 с, и
    # почти половину времени занимал обход ответа jsonable_encoder. Здесь только
    # обычные JSON-значения, поэтому ответ сериализуется сразу (те же байты);
    # если вдруг попадётся необычный тип — прежний путь FastAPI.
    try:
        return JSONResponse(payload)
    except (TypeError, ValueError):
        return payload


@router.get("/music/presets")
async def music_presets(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .music_runtime import PRESETS
    from . import modules_host
    from .storage import load_json_with_recovery
    legacy = load_json_with_recovery(str(modules_host.default_root() / "media-connections.json"), {}).get("studio", {})
    return {"presets": PRESETS, "legacy_available": bool(legacy.get("endpoint"))}


@router.post("/music/import-studio")
async def import_studio_music(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import modules_host
    from .storage import load_json_with_recovery
    legacy = load_json_with_recovery(str(modules_host.default_root() / "media-connections.json"), {}).get("studio", {})
    if not legacy.get("endpoint"):
        return _err("Сохранённого подключения Studio нет / No saved Studio connection")
    endpoint = urllib.parse.urlsplit(str(legacy["endpoint"]))
    if endpoint.scheme != "https" or endpoint.username or endpoint.password:
        return _err("Некорректный адрес сохранённого API / Invalid saved API address")
    if any(acc.provider == "custom" and acc.extra.get("imported_studio_music") for acc in state.accounts.values()):
        return {"ok": True, "already_imported": True}
    used = {str(acc.fields.get("name", "")) for acc in state.accounts.values() if acc.provider == "custom"}
    prefix = "studio-music"
    suffix = 2
    while prefix in used:
        prefix = f"studio-music-{suffix}"; suffix += 1
    model = str(legacy.get("model") or "music")[:300]
    config = {key: value for key, value in legacy.items() if key not in ("api_key", "model")}
    fields = {"name": prefix, "base_url": f"https://{endpoint.netloc}", "api_key": str(legacy.get("api_key", "")),
              "music_models": model, "music_config": json.dumps(config, ensure_ascii=False)}
    provider = get_provider("custom")
    error = provider.validate_fields(fields)
    if error:
        return _err(error)
    account = state.add_account("custom", fields, label="Studio Music", extra={"imported_studio_music": True})
    state.seed_provider_models("custom", prefix, [(model, "Music")], account_id=account.id)
    return {"ok": True, "account": account.public_dict()}


def _load_json(path, default):
    try:
        import json as _json
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


@router.get("/pg/memory")
async def pg_memory_get(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from . import memory_v2   # Round 29: v2 document (keeps the legacy "enabled"/"facts" fields)
    return memory_v2.public(await asyncio.to_thread(memory_v2.load))


@router.post("/pg/memory")
async def pg_memory_set(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request)
    if error is not None:
        return error
    if "enabled" in body and not isinstance(body["enabled"], bool):
        return _err("Поле enabled должно быть логическим")
    if not isinstance(body.get("facts", []), list):
        return _err("Поле facts должно быть массивом")
    # Round 29: memory v2 — an {enabled, facts} write is merged into the structured document
    # (sources, rules, pending facts… are kept), see app/memory_v2.save_legacy.
    from . import memory_v2
    await asyncio.to_thread(memory_v2.save_legacy, body)
    return {"ok": True}


@router.get("/pg/prompts")
async def pg_prompts_get(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .pgdata import load_prompts
    return load_prompts()


@router.post("/pg/prompts")
async def pg_prompts_set(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request)
    if error is not None:
        return error
    from .pgdata import save_prompts
    # save_prompts keeps the original prompt set read-only and normalizes the
    # document, so a broken payload can never destroy the reference prompts.
    return {"ok": True, "prompts": save_prompts(body)}


@router.get("/pg/schedules")
async def pg_schedules_get(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .pgdata import load_schedules
    return {"tasks": load_schedules()}


@router.post("/pg/schedules")
async def pg_schedules_set(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request)
    if error is not None:
        return error
    if not isinstance(body.get("tasks", []), list):
        return _err("Поле tasks должно быть массивом")
    known_ids = body.get("known_ids")
    if known_ids is not None and not isinstance(known_ids, list):
        return _err("Поле known_ids должно быть массивом")
    from .pgdata import save_schedules
    save_schedules(body.get("tasks") or [], known_ids)
    return {"ok": True}


@router.get("/pg/schedule_results")
async def pg_schedule_results(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    from .pgdata import load_schedule_results
    return load_schedule_results()


@router.get("/pg/image/file")
async def pg_image_file(url: str, request: Request):
    """Proxy protected Genspark image files through the authenticated account."""
    deny = _auth(request)
    if deny:
        return deny
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname not in ("genspark.ai", "www.genspark.ai") \
            or not parsed.path.startswith("/api/files/s/"):
        return _err("Недопустимый адрес изображения")
    accounts = state.usable_accounts("genspark")
    if not accounts:
        return _err("Нет доступного аккаунта Genspark", 503)
    gp = get_provider("genspark")
    resp = await gp.client.download_image(gp._cookie(accounts[0]), url,
                                          proxy=gp.proxy_for(accounts[0]))
    if resp.status_code < 200 or resp.status_code >= 300:
        return _err(f"Genspark image: HTTP {resp.status_code}", 502)
    content = bytes(resp.content)
    if len(content) > 30 * 1024 * 1024:
        return _err("Изображение больше 30 МБ", 413)
    media_type = str(resp.headers.get("content-type") or "image/png").split(";", 1)[0]
    return Response(content, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.post("/pg/image")
async def pg_image(request: Request):
    """Генерация изображения: SSE-статусы + URL в финале."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=64 * 1024 * 1024)
    if error is not None:
        return error
    if not isinstance(body.get("images", []), list):
        return _err("Поле images должно быть массивом")
    prompt = str(body.get("prompt", "")).strip()
    public_model = str(body.get("model", "auto"))
    model = public_model
    provider_id = "genspark"
    account = None
    provider = None
    if model.startswith("genspark-image/"):
        model = model.split("/", 1)[1] or "auto"
        accounts = state.usable_accounts("genspark")
        provider = get_provider("genspark")
        account = accounts[0] if accounts else None
    else:
        routed = resolve(public_model)
        if routed and routed.provider == "custom":
            provider_id = "custom"
            model = routed.model
            accounts = pool.ordered_candidates("custom")
            if routed.entry and routed.entry.account_id:
                accounts = [a for a in accounts if a.id == routed.entry.account_id]
            else:
                accounts = [a for a in accounts if str(a.fields.get("name", "")).strip().lower().replace(" ", "-") == routed.prefix]
            provider = get_provider("custom")
            account = accounts[0] if accounts else None
        elif routed and routed.provider not in ("genspark", "custom") and public_model:
            # Round 33 (r33-models): a chat model of another provider used to fall back to Genspark «auto» silently
            # (or to «no genspark account») — a retry «with another model» then never used the model picked.
            return _err(f"Модель {public_model} не создаёт изображения. Выберите модель изображений.", 400)
        else:
            model = "auto"
            accounts = state.usable_accounts("genspark")
            provider = get_provider("genspark")
            account = accounts[0] if accounts else None
    aspect = str(body.get("aspect", "auto"))
    images = [str(x) for x in (body.get("images") or []) if str(x).startswith("data:image")][:10]
    if not prompt:
        return _err("Пустой промпт")
    if not account or not provider:
        if provider_id == "custom":   # Round 33: name the model, not «custom»
            return _err(f"Нет рабочего подключения для модели {public_model}. Проверьте его в MemuAI Proxy.", 503)
        return _err(f"Нет доступного аккаунта {provider_id} для генерации", 503)

    from fastapi.responses import StreamingResponse
    import asyncio as _aio
    import time as _t

    trace = state.open_trace(model=public_model, provider=provider_id)
    trace.capture_prompt([{"role": "user", "content": [{"type": "text", "text": prompt}] +
                          [{"type": "image"}] * min(len(images), 128)}])
    trace.add_stage("request_parsed", {"request_id": trace.request_id,
                                       "model": public_model, "provider": provider_id,
                                       "kind": "image", "reference_images": len(images)})

    async def events():
        yield "data: " + json.dumps({"status": "queued"}, ensure_ascii=False) + "\n\n"
        trace.add_stage("sending_to_provider", {"provider": provider_id, "agent": "AI Image",
                                                 "model": model})
        if provider_id == "custom":
            # Round 29: chat-native image models, Google AI Studio and reference pictures (app/media_gen.py).
            from . import media_gen
            job = media_gen.generate_image(provider, account, prompt, model, aspect, images,
                                           system=str(body.get("system") or "")[:8000])
        else:
            job = provider.generate_images(account, prompt, model, aspect, images)
        task = _aio.create_task(job)
        try:
            while not task.done():
                yield "data: " + json.dumps({"status": "generating",
                                              "ms": int((_t.time() - t0) * 1000)},
                                             ensure_ascii=False) + "\n\n"
                await _aio.sleep(1.5)
        except BaseException:
            # Client disconnect closes the generator with GeneratorExit at a
            # ``yield``, not CancelledError: the provider job kept generating
            # for nobody. Any early exit cancels it.
            task.cancel()
            raise
        t0_done = _t.time()
        try:
            urls = task.result()
            trace.status = 200
            trace.ms = int((t0_done - t0) * 1000)
            trace.add_stage("finished", {"finish_reason": "stop", "images": len(urls),
                                          "output_text_chars": 0})
            yield "data: " + json.dumps({"status": "done", "urls": urls,
                                         "ms": int((t0_done - t0) * 1000)},
                                        ensure_ascii=False) + "\n\n"
        except Exception as e:
            trace.status = getattr(e, "status", 502) or 502
            trace.error = str(e)
            trace.ms = int((_t.time() - t0) * 1000)
            trace.add_stage("finished", {"error": str(e), "status": trace.status})
            yield "data: " + json.dumps({"status": "error", "error": str(e)},
                                        ensure_ascii=False) + "\n\n"
        if config_store.settings.log_requests or trace.status != 200:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        yield "data: [DONE]\n\n"

    t0 = _t.time()
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/pg/video")
async def pg_video(request: Request):
    """Генерация видео через тот же конвейер Genspark, что и изображения."""
    deny = _auth(request)
    if deny:
        return deny
    body, error = await _json_object(request, max_bytes=32 * 1024 * 1024)
    if error is not None:
        return error
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _err("Пустой промпт")
    public_model = str(body.get("model") or "auto").strip() or "auto"
    model = public_model
    provider_id = "genspark"
    routed = None if public_model.startswith(("genspark-video/", "genspark-image/")) or public_model == "auto" \
        else resolve(public_model)
    if routed and routed.provider == "custom":
        # Round 29: OpenAI-compatible /videos and Google AI Studio Veo (app/media_gen.py).
        provider_id = "custom"
        model = routed.model
        accounts = pool.ordered_candidates("custom")
        if routed.entry and routed.entry.account_id:
            accounts = [a for a in accounts if a.id == routed.entry.account_id]
        else:
            accounts = [a for a in accounts if str(a.fields.get("name", "")).strip().lower().replace(" ", "-") == routed.prefix]
        provider = get_provider("custom")
        account = accounts[0] if accounts else None
        if not account or not provider:
            return _err("Нет доступного подключения для генерации видео", 503)
    else:
        for prefix in ("genspark-video/", "genspark-image/"):
            if model.startswith(prefix):
                model = model.split("/", 1)[1] or "auto"
        accounts = state.usable_accounts("genspark")
        provider = get_provider("genspark")
        account = accounts[0] if accounts else None
        if not account or not provider or not hasattr(provider, "generate_images"):
            return _err("Нет доступного аккаунта Genspark для генерации видео", 503)
    aspect = str(body.get("aspect") or "16:9")
    images = [str(item) for item in (body.get("images") or []) if str(item).startswith("data:image")][:4]
    try:
        seconds = max(0, min(int(body.get("seconds") or 0), 60))
    except (TypeError, ValueError):
        seconds = 0

    from fastapi.responses import StreamingResponse
    import asyncio as _aio
    import time as _t

    trace = state.open_trace(model=public_model, provider=provider_id)
    trace.capture_prompt([{"role": "user", "content": [{"type": "text", "text": prompt}] +
                          [{"type": "image"}] * min(len(images), 128)}])
    trace.add_stage("request_parsed", {"request_id": trace.request_id, "model": public_model,
                                       "provider": provider_id, "kind": "video"})

    async def events():
        yield "data: " + json.dumps({"status": "queued"}, ensure_ascii=False) + "\n\n"
        if provider_id == "custom":
            from . import media_gen
            job = media_gen.generate_video(provider, account, prompt, model, aspect, images, seconds)
        else:
            job = provider.generate_images(account, prompt, model, aspect, images, media="video")
        task = _aio.create_task(job)
        try:
            while not task.done():
                yield "data: " + json.dumps({"status": "generating",
                                              "ms": int((_t.time() - started) * 1000)},
                                             ensure_ascii=False) + "\n\n"
                await _aio.sleep(1.5)
        except BaseException:
            # Client disconnect closes the generator with GeneratorExit at a
            # ``yield``, not CancelledError: the provider job kept generating
            # for nobody. Any early exit cancels it.
            task.cancel()
            raise
        try:
            urls = task.result()
            trace.status = 200
            trace.ms = int((_t.time() - started) * 1000)
            trace.add_stage("finished", {"finish_reason": "stop", "videos": len(urls)})
            yield "data: " + json.dumps({"status": "done", "urls": urls, "ms": trace.ms},
                                        ensure_ascii=False) + "\n\n"
        except Exception as exc:
            trace.status = getattr(exc, "status", 502) or 502
            trace.error = str(exc)
            trace.ms = int((_t.time() - started) * 1000)
            trace.add_stage("finished", {"error": str(exc), "status": trace.status})
            yield "data: " + json.dumps({"status": "error", "error": str(exc)},
                                        ensure_ascii=False) + "\n\n"
        if config_store.settings.log_requests or trace.status != 200:
            state.commit_trace(trace)
        else:
            state.discard_trace(trace)
        yield "data: [DONE]\n\n"

    started = _t.time()
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/pg/chat")
async def pg_chat2(request: Request):
    return await pg_chat(request)


# ------------------------------ ключи / логи ------------------------------

@router.get("/keys")
async def list_keys(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return {"keys": config_store.settings.api_keys}


@router.post("/keys")
async def create_key(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    key = f"oh-{secrets.token_urlsafe(24)}"
    config_store.settings.api_keys.append(key)
    config_store.save()
    return {"ok": True, "key": key, "keys": config_store.settings.api_keys}


@router.delete("/keys/{key}")
async def delete_key(key: str, request: Request):
    deny = _auth(request)
    if deny:
        return deny
    keys = config_store.settings.api_keys
    if key in keys:
        keys.remove(key)
        config_store.save()
        return {"ok": True, "keys": keys}
    return _err("Ключ не найден", 404)


@router.post("/shutdown")
async def shutdown_app(request: Request):
    """Выключает приложение целиком (из панели или трея)."""
    deny = _auth(request)
    if deny:
        return deny
    import threading as _t
    import os as _os

    callback = getattr(request.app.state, "shutdown_callback", None)

    def _die():
        time.sleep(0.5)
        # Штатное завершение: lifespan, atexit, остановка llama-server и сброс
        # метрик. os._exit сразу оставлял процессы моделей висеть в памяти.
        if callback:
            try:
                callback()
            except Exception:
                pass
            time.sleep(10)
        try:
            from . import local_models
            local_models.pool.stop_all()
        except Exception:
            pass
        try:
            from .metrics import metrics as _metrics
            _metrics.flush(force=True)
        except Exception:
            pass
        _os._exit(0)

    _t.Thread(target=_die, daemon=True).start()
    return {"ok": True, "message": "MemuAI Proxy выключается…"}


@router.get("/logs")
async def get_logs(request: Request, limit: int = 100, offset: int = 0,
                   query: str = "", status: str = "", provider: str = "",
                   model: str = "", since: float = 0.0, facets: bool = False):
    deny = _auth(request)
    if deny:
        return deny
    result = state.list_logs(limit=max(1, min(limit, 500)), offset=max(0, offset),
                             query=query, status=status, provider=provider,
                             model=model, since=since)
    for row in result["logs"]:
        snapshot = row.get("prompt")
        if snapshot is not None:
            row["prompt"] = {"available": bool(snapshot.get("messages") or snapshot.get("tools")),
                             "truncated": bool(snapshot.get("truncated")),
                             "omitted_media": bool(snapshot.get("omitted_media"))}
    if facets:
        result["facets"] = state.log_facets()
    return result


@router.get("/logs/prompt")
async def get_log_prompt(request: Request, request_id: str = ""):
    deny = _auth(request)
    if deny:
        return deny
    snapshot = state.log_prompt(request_id)
    if snapshot is None:
        return JSONResponse({"error": "Промпт не сохранён или запись уже удалена",
                             "error_en": "The prompt was not saved or the log entry was removed"}, 404)
    return {"prompt": snapshot}


@router.get("/logs/facets")
async def logs_facets(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    return state.log_facets()


@router.get("/logs/export")
async def export_logs(request: Request, fmt: str = "json", query: str = "",
                      status: str = "", provider: str = "", model: str = ""):
    deny = _auth(request)
    if deny:
        return deny
    data = state.list_logs(limit=5000, query=query, status=status,
                           provider=provider, model=model)
    rows = data.get("logs") or []
    if fmt == "txt":
        lines = []
        for row in rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row.get("ts") or 0)))
            head = (f"[{stamp}] "
                    f"{row.get('status')} {row.get('provider')} {row.get('model')} "
                    f"{row.get('ms')}ms")
            lines.append(head)
            if row.get("error"):
                lines.append(f"    error: {row['error']}")
            if row.get("prompt"):
                lines.append("    prompt: " + json.dumps(row["prompt"], ensure_ascii=False))
            for stage in row.get("stages") or []:
                lines.append(f"    - {stage.get('step')}: "
                             f"{json.dumps(stage.get('payload'), ensure_ascii=False)}")
            lines.append("")
        body = "\n".join(lines)
        return Response(body, media_type="text/plain; charset=utf-8", headers={
            "Content-Disposition": "attachment; filename=memuai-logs.txt"})
    return Response(json.dumps({"exported_at": time.time(), "logs": rows},
                               ensure_ascii=False, indent=2),
                    media_type="application/json", headers={
                        "Content-Disposition": "attachment; filename=memuai-logs.json"})


@router.post("/logs/clear")
async def clear_logs(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    state.clear_logs()
    return {"ok": True}


@router.get("/applog")
async def get_applog(request: Request, limit: int = 200, level: str = "",
                     query: str = "", after: int = 0):
    """Application (server) log: python logger records kept in a ring buffer."""
    deny = _auth(request)
    if deny:
        return deny
    return app_log.list(limit=max(1, min(limit, 1000)), level=level,
                        query=query, after=max(0, after))


@router.post("/applog/clear")
async def clear_applog(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    app_log.clear()
    return {"ok": True}


@router.get("/metrics")
async def get_metrics(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    period = str(request.query_params.get("period", "day") or "day").strip().lower()
    return metrics.snapshot(period)


@router.post("/metrics/reset")
async def reset_metrics(request: Request):
    deny = _auth(request)
    if deny:
        return deny
    metrics.reset()
    return {"ok": True}


@router.post("/restart")
async def restart_services(request: Request):
    """Soft restart: rebuilds HTTP sessions, provider registry and runtime state.

    Fixes the "proxy stops answering after a long uptime" case without killing
    the process (the tray icon and the local window stay alive).
    """
    deny = _auth(request)
    if deny:
        return deny
    body: Dict[str, Any] = {}
    if request.headers.get("content-length") not in (None, "", "0"):
        body, error = await _json_object(request, max_bytes=8192)
        if error is not None:
            return error
    steps: List[str] = []
    try:
        from .providers import reset_registry
        await reset_registry()
        steps.append("providers")
    except Exception as e:
        steps.append(f"providers:{e}")
    try:
        from .providers import http as hub_http
        await hub_http.close_all()
        hub_http.configure(verify=config_store.settings.verify_ssl)
        steps.append("http")
    except Exception as e:
        steps.append(f"http:{e}")
    try:
        # Блокировки «эта модель недоступна этому аккаунту» живут в памяти и
        # истекают сами; после перезапуска служб они проверяются заново.
        pool.clear_blocks()
        steps.append("accounts")
    except Exception as e:
        steps.append(f"accounts:{e}")
    if body.get("reset_metrics"):
        metrics.reset()
        steps.append("metrics")
    log.info("Soft restart finished: %s", ", ".join(steps))
    return {"ok": True, "steps": steps}
