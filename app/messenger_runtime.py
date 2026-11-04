"""Discord and WhatsApp bots inside MemuAI Proxy: one supervisor task started with the Proxy."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("memuai.messengers")


async def messengers_loop() -> None:
    from . import discord_bot, whatsapp_bot
    tasks = [asyncio.create_task(discord_bot.supervise()), asyncio.create_task(whatsapp_bot.supervise())]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
