"""Scheduled calls to the connected personal-account integrations.

Validation is shared by the rule builder, chat schedule blocks and flow steps.
Execution belongs to the MemuAI window, where the Telegram/Chrome sessions live.
The connected-service dispatcher owns the final permission/connection checks.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .schedule_rules import RuleError

MAX_ARGS = 100_000
TOOL_NAME = re.compile(r"^(?:telegram_use|chrome)_[a-z][a-z0-9_]{0,100}$")
# Friendly names for the planning UI: (title ru, title en, about ru, about en).
CHROME_LABELS = {
    "chrome_tabs": ("Список вкладок", "List tabs", "Открытые вкладки Chrome", "The open Chrome tabs"),
    "chrome_read": ("Прочитать страницу", "Read a page", "Текст и элементы вкладки", "Text and elements of a tab"),
    "chrome_screenshot": ("Снимок вкладки", "Tab screenshot", "Картинка видимой части страницы", "An image of the visible page"),
    "chrome_open": ("Открыть адрес", "Open a URL", "Новая вкладка с адресом", "A new tab with the URL"),
    "chrome_navigate": ("Перейти по адресу", "Go to a URL", "Открыть адрес в существующей вкладке", "Open a URL in an existing tab"),
    "chrome_close": ("Закрыть вкладку", "Close a tab", "Закрыть одну вкладку", "Close one tab"),
    "chrome_close_tabs": ("Закрыть вкладки", "Close tabs", "Закрыть несколько вкладок", "Close several tabs"),
    "chrome_action": ("Действие на странице", "Page action", "Нажать, ввести текст, прокрутить", "Click, type or scroll"),
}
ARG_LABELS = {
    "chat": ("Чат или контакт", "Chat or contact"), "query": ("Что искать", "Search for"), "limit": ("Сколько", "How many"),
    "before_id": ("До сообщения №", "Before message #"), "search": ("Текст для поиска", "Search text"),
    "message_id": ("Номер сообщения", "Message ID"), "text": ("Текст", "Text"),
    "reply_to": ("Ответ на сообщение №", "Reply to message #"), "silent": ("Без звука", "Silent"),
    "message_ids": ("Номера сообщений", "Message IDs"), "revoke": ("Удалить у всех", "Delete for everyone"),
    "from_chat": ("Из чата", "From chat"), "emoji": ("Эмодзи", "Emoji"), "prompt": ("Задание для ИИ", "AI instruction"),
    "model": ("Модель", "Model"), "path": ("Путь к файлу", "File path"), "caption": ("Подпись", "Caption"),
    "account": ("Аккаунт Telegram", "Telegram account"), "tab_id": ("Вкладка", "Tab"), "url": ("Адрес", "URL"),
    "active": ("Открыть на переднем плане", "Open in the foreground"), "tab_ids": ("Вкладки", "Tabs"),
    "action": ("Действие", "Action"), "element": ("Элемент страницы", "Page element"), "value": ("Значение", "Value"),
    "dy": ("Прокрутка", "Scroll"), "key": ("Клавиша", "Key"),
}

def _labels(name: str) -> tuple | None:
    if name.startswith("telegram_use_"):
        from .telegram_use import LABELS
        return LABELS.get(name)
    return CHROME_LABELS.get(name)

def _accounts() -> List[Dict[str, Any]]:
    """Connected Telegram Use accounts when there are several; one account needs no choice."""
    try:
        from . import telegram_use, telegram_use_config
        prefs = telegram_use_config.load()
        if len(prefs["accounts"]) < 2:
            return []
        live = telegram_use._instance.status().get("accounts") if telegram_use._instance else None
        rows = live or [{"id": a["id"], "label": a["label"]} for a in prefs["accounts"]]
        return [{"id": str(r.get("id") or ""), "label": str(r.get("label") or r.get("id") or "")} for r in rows]
    except Exception:
        return []

def normalize(tool: Any, args: Any) -> Dict[str, Any]:
    name = str(tool or "").strip()
    if not TOOL_NAME.fullmatch(name):
        raise RuleError("Выберите действие Telegram или Chrome", "Choose a Telegram or Chrome action")
    if not isinstance(args, dict):
        raise RuleError("Аргументы интеграции должны быть JSON-объектом", "Integration arguments must be a JSON object")
    try:
        encoded = json.dumps(args, ensure_ascii=False, allow_nan=False)

        def check(value: Any, depth: int = 0) -> None:
            if depth > 20:
                raise ValueError("depth")
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError("key")
                    check(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    check(item, depth + 1)
        check(args)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RuleError("Аргументы интеграции — некорректный JSON или слишком вложенный объект",
                        "Integration arguments are invalid JSON or nested too deeply") from exc
    if len(encoded) > MAX_ARGS:
        raise RuleError("Аргументы интеграции слишком большие", "Integration arguments are too large")
    return {"tool": name, "args": json.loads(encoded)}

def catalog() -> List[Dict[str, Any]]:
    from .connected_services import status_rows
    rows = []
    accounts = _accounts()
    for service in status_rows():
        tools = service.get("tools") or []
        if not tools and service.get("service") == "telegram_use":
            from .telegram_use import TOOLS
            tools = TOOLS
        for tool in tools:
            if isinstance(tool, str):
                name, _, arguments = tool.partition("{")
                numeric = {"limit", "before_id", "message_id", "tab_id", "reply_to", "dy"}
                boolean = {"silent", "revoke", "active"}
                props = {}
                for arg in arguments.rstrip("}").split(","):
                    arg = arg.strip()
                    if arg:
                        props[arg] = {"type": "integer" if arg in numeric else "boolean" if arg in boolean
                                      else "array" if arg == "message_ids" else "string"}
                        if arg in boolean:
                            props[arg]["default"] = arg == "revoke"
                tool = {"name": name, "description": tool,
                        "parameters": {"type": "object", "properties": props}}
            if not isinstance(tool, dict):
                continue
            tool = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            name = str(tool.get("name") or "")
            if TOOL_NAME.fullmatch(name):
                row = {**tool, "name": name, "connected": bool(service.get("connected")),
                       "service": service.get("service") or service.get("id") or service.get("name") or "",
                       "group": "Telegram" if name.startswith("telegram_use_") else "Chrome"}
                labels = _labels(name)
                if labels:
                    row["title"] = {"ru": labels[0], "en": labels[1]}
                    row["about"] = {"ru": labels[2], "en": labels[3]}
                parameters = dict(row.get("parameters") or {})
                props = {key: dict(value) for key, value in (parameters.get("properties") or {}).items()
                         if isinstance(value, dict)}
                if name.startswith("telegram_use_") and accounts and "account" not in props:
                    props["account"] = {"type": "string", "enum": [a["id"] for a in accounts],
                                        "enum_titles": [a["label"] for a in accounts]}
                for key, value in props.items():
                    if key in ARG_LABELS and "title" not in value:
                        value["title"] = {"ru": ARG_LABELS[key][0], "en": ARG_LABELS[key][1]}
                if props:
                    row["parameters"] = {**parameters, "type": "object", "properties": props}
                rows.append(row)
    return rows

def validate(tool: Any, args: Any) -> Dict[str, Any]:
    action = normalize(tool, args)
    known = {str(tool.get("name")) for tool in catalog()}
    if action["tool"] not in known:
        raise RuleError("Инструмент интеграции не найден — обновите настройки задачи",
                        "Integration tool not found — update the task settings")
    return action

async def run(task: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
    from . import connected_services
    action = validate(task.get("tool"), task.get("args", {}))

    result = await connected_services.run(action["tool"], action["args"], scheduled=True)
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False
                                     or result.get("needs_confirm") or result.get("needs_confirmation")):
        message = result.get("error") or result.get("message") or "Scheduled integration call was not authorized"
        return False, str(message)[:20000], {"kind": "integration", "tool": action["tool"]}
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return True, text[:20000], {"kind": "integration", "tool": action["tool"], "integration_result": result}
