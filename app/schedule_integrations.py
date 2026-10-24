"""Round 43: scheduled calls to the connected personal-account integrations.

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


def normalize(tool: Any, args: Any) -> Dict[str, Any]:
    name = str(tool or "").strip()
    if not TOOL_NAME.fullmatch(name):
        raise RuleError("Выберите инструмент Telegram Use или Chrome", "Choose a Telegram Use or Chrome tool")
    if not isinstance(args, dict):
        raise RuleError("Аргументы интеграции должны быть JSON-объектом", "Integration arguments must be a JSON object")
    try:
        encoded = json.dumps(args, ensure_ascii=False, allow_nan=False)
        # Bound depth as well as bytes, before persistence/template traversal.
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
    for service in status_rows():
        tools = service.get("tools") or []
        if not tools and service.get("service") == "telegram_use":
            from .telegram_use import TOOLS
            tools = TOOLS  # drafting a schedule does not require an active account session
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
                rows.append({**tool, "name": name, "connected": bool(service.get("connected")),
                             "service": service.get("service") or service.get("id") or service.get("name") or ""})
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
    # scheduled=True is explicit: never fall back to interactive approvals.
    # Saved tasks authorize reads; the integration setting separately controls
    # unattended writes. Connection/read-access switches still apply.
    result = await connected_services.run(action["tool"], action["args"], scheduled=True)
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False
                                     or result.get("needs_confirm") or result.get("needs_confirmation")):
        message = result.get("error") or result.get("message") or "Scheduled integration call was not authorized"
        return False, str(message)[:20000], {"kind": "integration", "tool": action["tool"]}
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return True, text[:20000], {"kind": "integration", "tool": action["tool"], "integration_result": result}
