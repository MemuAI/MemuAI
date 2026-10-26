"""Round 38: Quick Access backend of the module integration — the ``module_command`` tool.

The tool line lists every command of the enabled modules (app/module_integration.py ``skill_prompt``); the handler runs
the command on the server and returns a ``tool_ui`` card ``{type: "module-card", card, text, module, command}`` that
quick/plugins/modules.js draws (cover, buttons). A command that needs the main window (start the host player, open a
module) is handed to it by ``module_integration_api.nudge_main``. Dangerous commands are refused here unless the user
confirmed them on the card (the card's button calls /api/pg/modint/command with ``confirmed: true``).
"""
from __future__ import annotations

from typing import Any, Dict

from . import module_integration as mi
from .quick_ask_api import register_tool

def _description(settings: Dict[str, Any]) -> str:
    lang = "en" if str((settings or {}).get("language") or (settings or {}).get("lang") or "") == "en" else "ru"
    block = mi.skill_prompt(None, lang=lang, surface="quick")
    if not block:
        return ""
    body = block.split("\n", 4)[-1].replace("</module_commands>", "").strip()
    return ("{module, command, args} — run a command of an enabled MemuAI module (music, characters…); the window shows "
            "a result card, so answer in one short sentence. Commands:\n" + body)

def _enabled(settings: Dict[str, Any]) -> bool:
    try:
        return any(row["commands"] for row in mi.contributions())
    except Exception:  # noqa: BLE001 - a broken module never breaks Quick Access
        return False

async def _run(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    lang = "en" if ctx.get("lang") == "en" else "ru"
    result = await mi.run_command(str(args.get("module") or ""), str(args.get("command") or ""), args.get("args") or {},
                                  lang=lang, surface="quick")
    from .module_integration_api import nudge_main
    if nudge_main(result.get("client")):
        result["client_sent"] = True
    ui = {"type": "module-card", "module": result.get("module") or str(args.get("module") or ""),
          "command": result.get("command") or str(args.get("command") or ""), "ok": bool(result.get("ok")),
          "text": result.get("text") or result.get("error") or "", "card": result.get("card") or None,
          "needs_confirm": bool(result.get("needs_confirm")), "args": result.get("args") or args.get("args") or {},
          "client": result.get("client") or {}, "client_sent": bool(result.get("client_sent"))}
    value: Dict[str, Any] = {k: v for k, v in result.items() if k in ("ok", "text", "error", "needs_confirm")}
    value["ui"] = ui
    if result.get("needs_confirm"):
        value["note"] = "The window asks the user to confirm on the card; tell them to press the button."
    return value

def _label(args: Dict[str, Any], lang: str) -> str:
    command = str((args or {}).get("command") or "")
    return ("Команда модуля: " if lang != "en" else "Module command: ") + f"{(args or {}).get('module') or ''}.{command}"

register_tool("module_command", _description, _run, label=_label, enabled=_enabled)
