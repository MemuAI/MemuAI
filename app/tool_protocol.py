"""Shared, lossless translation helpers for client-owned function calls."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from .routing import resolve


TOOL_PROVIDER_ERROR = (
    "Tool calls require an OpenAI-compatible Custom API model with native function "
    "calling or a local model (llama.cpp). Select such a model in MemuAI Proxy."
)
TOOL_PROVIDER_ERROR_RU = (
    "Вызовы инструментов работают только с моделью OpenAI-совместимого подключения «Собственный API» "
    "с нативным function calling или с локальной моделью (llama.cpp). Выберите такую модель в MemuAI Proxy."
)

#: Providers that pass tools to the model natively. Round 33: local models — llama-server runs with
#: --jinja and returns OpenAI tool_calls parsed from the model's own chat template.
TOOL_PROVIDERS = frozenset({"custom", "local"})


def tool_provider_supported(model: str) -> bool:
    from . import composite_models   # Round 35: a composite supports tools when one of its models does
    if composite_models.is_composite(model):
        return composite_models.tools_supported(model)
    from . import web_models   # Round 40: a web model («…:web») passes the client's tools to its base model
    web = web_models.store.get(model)
    if web is not None:
        return tool_provider_supported(web["base"])
    route = resolve(model)
    return route is not None and route.provider in TOOL_PROVIDERS


def collect_tool_delta(calls: Dict[int, Dict[str, Any]], delta: Dict[str, Any]) -> None:
    """Reassemble streamed Chat Completions calls by their stable index."""
    for part in delta.get("tool_calls") or []:
        if not isinstance(part, dict):
            continue
        if part.get("index") is None:
            # Some OpenAI-compatible servers (Gemini's compat layer, older
            # llama.cpp) omit ``index``. Defaulting every part to 0 merged
            # parallel calls into one with concatenated arguments. A new id
            # starts a new call; an id-less part continues the latest one.
            part_id = str(part.get("id") or "")
            known = next((i for i, c in calls.items() if part_id and c["id"] == part_id), None)
            if known is not None:
                index = known
            elif part_id:
                index = (max(calls) + 1) if calls else 0
            else:
                index = max(calls) if calls else 0
        else:
            try:
                index = int(part["index"])
            except (TypeError, ValueError):
                continue
        call = calls.setdefault(index, {"id": "", "type": "function",
                                        "function": {"name": "", "arguments": ""}})
        if part.get("id"):
            call["id"] = str(part["id"])
        function = part.get("function") or {}
        if isinstance(function, dict):
            for key in ("name", "arguments"):
                if function.get(key) is not None:
                    value = function[key]
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    if key == "name" and call["function"]["name"] and text.startswith(call["function"]["name"]):
                        # Several servers repeat the full name in every delta;
                        # appending produced «run_commandrun_command».
                        call["function"]["name"] = text
                    else:
                        call["function"][key] += text


def ordered_tool_calls(calls: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    for index in sorted(calls):
        if not calls[index].get("id"):
            # An upstream without call ids left ``id: ""``: the client then
            # answers with tool_call_id/tool_use_id "" and can no longer pair
            # results with calls. Assign a stable id once (stored in place).
            calls[index]["id"] = "call_" + uuid.uuid4().hex[:24]
    return [calls[index] for index in sorted(calls)]
