"""One-time protocol handoff. URLs contain correlation IDs, never access keys."""
from __future__ import annotations

import re
import threading
import time

_lock = threading.RLock()
_pending = {}
_challenge = re.compile(r"[0-9a-f]{32}")
_extension = re.compile(r"[a-p]{32}")

def approve(challenge, extension_id):
    if not _challenge.fullmatch(str(challenge)) or not _extension.fullmatch(str(extension_id)):
        raise ValueError("Некорректный запрос подключения Chrome")
    from .browser_extension import bridge
    with _lock:
        now = time.monotonic()
        for key in list(_pending):
            if _pending[key][2] < now:
                _pending.pop(key, None)
        if len(_pending) >= 32:
            _pending.pop(next(iter(_pending)))
        code = bridge.begin_pair()["code"]
        _pending[challenge] = (extension_id, code, now + 300)
    return {"ok": True, "message": "Подключение разрешено. Вернитесь в параметры расширения Chrome.",
            "ui": {"do": "settings.open", "section": "integrations"}}

def claim(challenge, extension_id):
    if not _challenge.fullmatch(str(challenge)):
        raise ValueError("Неверный идентификатор запроса")
    with _lock:
        item = _pending.get(challenge)
        if item is None or item[0] != extension_id:
            return {"ready": False}
        _pending.pop(challenge, None)
        if item[2] <= time.monotonic():
            return {"ready": False, "expired": True}
        return {"ready": True, "code": item[1], "expires_in": max(1, int(item[2] - time.monotonic()))}
