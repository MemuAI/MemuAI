"""Best-effort native Windows notifications for background work."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("memuai.notifications")

def show_completion(title: str, text: str, *, sound: bool = False) -> None:
    """Show a native toast without making notification failures affect a task."""
    if os.name != "nt":
        return
    try:
        from winotify import Notification, audio

        icon = Path(DATA_DIR) / "memuai" / "assistant.ico"
        toast = Notification(
            app_id="MemuAI",
            title=str(title or "MemuAI"),
            msg=str(text or "")[:500],
            icon=str(icon) if icon.exists() else "",
        )
        if sound:
            toast.set_audio(audio.Default, loop=False)
        else:
            toast.set_audio(audio.Silent, loop=False)
        toast.show()
    except Exception as exc:
        log.debug("Windows notification unavailable: %s", exc)
