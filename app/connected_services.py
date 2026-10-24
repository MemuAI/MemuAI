"""Shared entry point for window-owned Telegram Use and Chrome connections.

Schedulers and the CLI use the same permission checks as the desktop chat.
The scheduler calls this only for a task the user has saved. Reads obey the
service's read permissions; writes require its unattended-execution opt-in.
"""
from __future__ import annotations

import asyncio


def status_rows() -> list[dict]:
    from . import browser_extension, telegram_use
    return [telegram_use.integration_status(), browser_extension.bridge.public()]


def write_tools() -> set[str]:
    from . import browser_extension, telegram_use
    return set(telegram_use.WRITE) | browser_extension.WRITE


def always_confirm() -> set[str]:
    from . import browser_extension, telegram_use
    cfg = browser_extension.settings()
    return set(telegram_use.WRITE) | (browser_extension.WRITE if cfg["control"] != "allow" else set())


def danger_tools() -> set[str]:
    from . import telegram_use
    return set(telegram_use.DANGER) | {"chrome_close"}


async def run(tool: str, args: dict | None = None, *, confirmed: bool = False, scheduled: bool = False):
    from . import browser_extension, telegram_use
    payload = args if isinstance(args, dict) else {}
    if tool.startswith("chrome_"):
        result = await browser_extension.run_tool(tool, payload, confirmed=confirmed, scheduled=scheduled)
        if isinstance(result, dict) and result.get("needs_confirmation"):
            raise PermissionError("Пользователь не подтвердил действие в Chrome")
        return result
    if tool.startswith("telegram_use_"):
        if tool in telegram_use.WRITE and not scheduled and not confirmed:
            raise PermissionError("Пользователь не подтвердил действие в Telegram")
        return await asyncio.to_thread(telegram_use.run_tool, tool, payload, scheduled=scheduled)
    raise ValueError("Неизвестный инструмент подключённого сервиса")
