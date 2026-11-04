"""MemuAI Proxy как MCP-сервер (Streamable HTTP, MCP 2025-06-18).

Любой MCP-клиент (Claude Desktop, ...) подключается к
``http://127.0.0.1:8317/mcp`` — тот же адрес и тот же ключ API хаба,
что и у OpenAI-совместимого ``/v1``. Никакого отдельного порта и
отдельных учёток: ключ создаётся в админке (раздел «API» / ``/api/admin/keys``),
а если ключей нет — с loopback работает и без ключа, как и ``/v1``.

Протокол: JSON-RPC 2.0 поверх HTTP (Streamable HTTP).
``POST /mcp`` — запросы клиента, ``GET /mcp`` — SSE-поток сообщений
сервера, ``DELETE /mcp`` — завершение сессии. Сессия опциональна:
клиент без ``Mcp-Session-Id`` обслуживается без состояния.

Инструменты:
  ``list_providers`` — провайдеры (встроенные и плагины) с моделями;
  ``list_models`` — модели из ``/v1/models`` (опционально фильтр по провайдеру);
  ``list_plugins`` — файлы плагинов со статусом;
  ``reload_plugins`` — перечитать плагины без рестарта;
  ``chat`` — нестриминговый вызов модели (model + messages).

Ресурсы: ``memuai://providers``, ``memuai://models``, ``memuai://plugins``.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from .version import APP_VERSION

SERVER_NAME = "MemuAI Proxy"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"

_SESSIONS: Dict[str, float] = {}  # session id -> created at

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _rpc_error(code: int, message: str, request_id: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _rpc_result(result: Any, request_id: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text(value: Any) -> str:
    """Текст из чанка chat_service: строка или список частей."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                       for part in value if part is not None)
    return ""


def _providers_public() -> List[Dict[str, Any]]:
    """Провайдеры с моделями: meta_list() + видимые модели из /v1/models."""
    from .openai_api import listed_models
    from .providers import meta_list
    models_by_provider: Dict[str, List[str]] = {}
    for entry in listed_models():
        owner = str(entry.get("owned_by") or entry.get("prefix") or "")
        models_by_provider.setdefault(owner, []).append(str(entry["id"]))
    out: List[Dict[str, Any]] = []
    for meta in meta_list():
        pid = str(meta["id"])
        from .state import state
        out.append({
            "id": pid,
            "title": meta.get("title") or pid,
            "plugin": bool(meta.get("plugin")),
            "description": meta.get("description") or "",
            "has_account": any(acc.provider == pid for acc in state.accounts.values()),
            "models": models_by_provider.get(pid, []),
        })
    return out


def _models_public(provider: str = "") -> List[Dict[str, Any]]:
    from .openai_api import listed_models
    rows = listed_models()
    if provider:
        rows = [row for row in rows
                if provider in (str(row.get("owned_by") or ""), str(row.get("prefix") or ""))]
    return [{"id": str(row["id"]), "provider": str(row.get("owned_by") or row.get("prefix") or "")}
            for row in rows]


def _plugins_public() -> Dict[str, Any]:
    from .providers import plugin_status
    from .providers.plugins import plugins_dir
    return {"dir": str(plugins_dir()), "plugins": plugin_status()}


async def _run_chat(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Нестриминговый чат через тот же chat_service, что и /v1/chat/completions."""
    from .chat import CompletionError, chat_service
    from .providers.base import safe_provider_error
    from .providers.http import close_quietly

    model = arguments.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("поле 'model' обязательно (строка)")
    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("поле 'messages' обязательно и непустое")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ValueError("каждое сообщение — объект с полем 'role'")
    params: Dict[str, Any] = {}
    max_tokens = arguments.get("max_tokens")
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) \
                or not 1 <= max_tokens <= 1_000_000:
            raise ValueError("поле 'max_tokens' должно быть целым от 1 до 1000000")
        params["max_tokens"] = max_tokens
    temperature = arguments.get("temperature")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) \
                or not 0.0 <= float(temperature) <= 2.0:
            raise ValueError("поле 'temperature' должно быть числом от 0 до 2")
        params["temperature"] = temperature

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    usage: Dict[str, Any] = {}
    source = chat_service.stream_completion(model.strip(), messages, params)
    try:
        async for chunk in source:
            if isinstance(chunk, str):
                if chunk:
                    content_parts.append(chunk)
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("object") == "memuai.event":
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta") or {}
            if not isinstance(delta, dict):
                continue
            if delta.get("content"):
                content_parts.append(_text(delta["content"]))
            if delta.get("reasoning_content"):
                reasoning_parts.append(_text(delta["reasoning_content"]))
    except CompletionError as exc:
        raise RuntimeError(safe_provider_error(exc.message)) from exc
    finally:
        await close_quietly(source.aclose)
    content = "".join(content_parts)
    structured: Dict[str, Any] = {"model": model.strip(), "content": content,
                                   "finish_reason": "stop"}
    if reasoning_parts:
        structured["reasoning"] = "".join(reasoning_parts)
    if usage:
        structured["usage"] = usage
    return {"content": [{"type": "text", "text": content}], "structuredContent": structured}


_TOOLS = (
    {"name": "list_providers",
     "description": "Провайдеры MemuAI Proxy: встроенные и плагины, с моделями и признаком наличия аккаунта.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "list_models",
     "description": "Модели, доступные через /v1/models. Опционально — только одного провайдера.",
     "inputSchema": {"type": "object",
                     "properties": {"provider": {"type": "string",
                                                 "description": "id провайдера, например kimi2api"}},
                     "additionalProperties": False}},
    {"name": "list_plugins",
     "description": "Файлы плагинов провайдеров: загруженные, сломанные (с текстом ошибки) и отключённые.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "reload_plugins",
     "description": "Перечитать плагины провайдеров без перезапуска Proxy.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "chat",
     "description": "Вызов модели MemuAI Proxy (нестриминговый): model — id из list_models, messages — как в /v1/chat/completions.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "model": {"type": "string"},
                         "messages": {"type": "array", "items": {"type": "object"},
                                      "description": "Например: [{\"role\": \"user\", \"content\": \"Привет\"}]"},
                         "max_tokens": {"type": "integer"},
                         "temperature": {"type": "number"}},
                     "required": ["model", "messages"],
                     "additionalProperties": False}},
)

_RESOURCES = (
    {"uri": "memuai://providers", "name": "Провайдеры",
     "description": "Провайдеры MemuAI Proxy (встроенные и плагины) с моделями.",
     "mimeType": "application/json"},
    {"uri": "memuai://models", "name": "Модели",
     "description": "Модели из /v1/models.", "mimeType": "application/json"},
    {"uri": "memuai://plugins", "name": "Плагины провайдеров",
     "description": "Файлы плагинов со статусом.", "mimeType": "application/json"},
)


def _tool_text(payload: Any) -> Dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload}


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


async def _call_tool(name: str, arguments: Any) -> Dict[str, Any]:
    if not isinstance(arguments, dict):
        return _tool_error("аргументы инструмента должны быть объектом")
    try:
        if name == "list_providers":
            return _tool_text({"providers": _providers_public()})
        if name == "list_models":
            provider = arguments.get("provider")
            if provider is not None and not isinstance(provider, str):
                return _tool_error("поле 'provider' должно быть строкой")
            return _tool_text({"models": _models_public(provider or "")})
        if name == "list_plugins":
            return _tool_text(_plugins_public())
        if name == "reload_plugins":
            from .providers import plugin_status, reset_registry
            await reset_registry()
            return _tool_text({"ok": True, "plugins": plugin_status()})
        if name == "chat":
            return await _run_chat(arguments)
    except (ValueError, RuntimeError) as exc:
        return _tool_error(str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 — инструмент не должен ронять JSON-RPC
        return _tool_error(f"внутренняя ошибка: {exc}"[:500])
    return _tool_error(f"неизвестный инструмент «{name}»")


async def _read_resource(uri: str) -> Dict[str, Any]:
    if uri == "memuai://providers":
        payload: Any = {"providers": _providers_public()}
    elif uri == "memuai://models":
        payload = {"models": _models_public()}
    elif uri == "memuai://plugins":
        payload = _plugins_public()
    else:
        raise ValueError(f"неизвестный ресурс «{uri}»")
    return {"contents": [{"uri": uri, "mimeType": "application/json",
                          "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


async def _handle(method: str, params: Any) -> Any:
    """Один JSON-RPC метод. Исключения — в JSON-RPC ошибку наверх."""
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return {"protocolVersion": version,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": SERVER_NAME, "version": APP_VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [dict(tool) for tool in _TOOLS]}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call: нужно поле 'name'")
        return await _call_tool(name, params.get("arguments") or {})
    if method == "resources/list":
        return {"resources": [dict(res) for res in _RESOURCES]}
    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("resources/read: нужно поле 'uri'")
        return await _read_resource(uri)
    if method == "prompts/list":
        return {"prompts": []}
    raise LookupError(f"неизвестный метод «{method}»")


async def _handle_one(payload: Any) -> Optional[Dict[str, Any]]:
    """Один JSON-RPC запрос. None — уведомление, ответа нет."""
    if not isinstance(payload, dict):
        return _rpc_error(_INVALID_REQUEST, "запрос должен быть JSON-объектом", None)
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(_INVALID_REQUEST, "нужно поле 'method' (строка)",
                          payload.get("id"))
    request_id = payload.get("id", None)
    is_notification = "id" not in payload
    if method.startswith("notifications/"):
        return None
    if is_notification:
        return None  # уведомления без id не требуют ответа
    try:
        result = await _handle(method, payload.get("params"))
    except ValueError as exc:
        return _rpc_error(_INVALID_PARAMS, str(exc)[:400], request_id)
    except LookupError as exc:
        return _rpc_error(_METHOD_NOT_FOUND, str(exc)[:400], request_id)
    except Exception as exc:  # noqa: BLE001
        return _rpc_error(_INTERNAL_ERROR, f"внутренняя ошибка: {exc}"[:400], request_id)
    return _rpc_result(result, request_id)


def register(app: Any) -> None:
    """Регистрирует POST/GET/DELETE /mcp в приложении Proxy."""

    @app.post("/mcp")
    async def mcp_post(request: Request):
        from .openai_api import _check_auth
        deny = _check_auth(request)
        if deny:
            return deny
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_rpc_error(_PARSE_ERROR, "тело должно быть корректным JSON", None),
                                status_code=200)
        session_id = request.headers.get("mcp-session-id", "").strip()
        if session_id and session_id not in _SESSIONS:
            return JSONResponse(_rpc_error(_INVALID_REQUEST, "неизвестная сессия MCP", None),
                                status_code=404)
        is_initialize = isinstance(body, dict) and body.get("method") == "initialize"
        if isinstance(body, list):
            results = []
            for item in body:
                response = await _handle_one(item)
                if response is not None:
                    results.append(response)
            payload: Any = results
        else:
            response = await _handle_one(body)
            if response is None:
                return JSONResponse(status_code=202, content={})
            payload = response
        headers: Dict[str, str] = {}
        if is_initialize:
            new_session = secrets.token_hex(8)
            _SESSIONS[new_session] = time.time()
            headers["Mcp-Session-Id"] = new_session
        elif session_id:
            headers["Mcp-Session-Id"] = session_id
        return JSONResponse(payload, headers=headers)

    @app.get("/mcp")
    async def mcp_get(request: Request):
        from .openai_api import _check_auth
        deny = _check_auth(request)
        if deny:
            return deny
        session_id = request.headers.get("mcp-session-id", "").strip()
        if not session_id or session_id not in _SESSIONS:
            return JSONResponse({"error": "нужен заголовок Mcp-Session-Id активной сессии"},
                                status_code=400)

        async def events():
            try:
                while True:
                    yield ": ping\n\n"
                    await asyncio.sleep(20)
            except asyncio.CancelledError:
                raise

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "Mcp-Session-Id": session_id})

    @app.delete("/mcp")
    async def mcp_delete(request: Request):
        from .openai_api import _check_auth
        deny = _check_auth(request)
        if deny:
            return deny
        session_id = request.headers.get("mcp-session-id", "").strip()
        if session_id:
            _SESSIONS.pop(session_id, None)
        return JSONResponse(status_code=204, content={})
