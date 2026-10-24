"""Кольцевой буфер системного журнала MemuAI Proxy.

Панель показывает не только историю запросов, но и сообщения самого
приложения (логгеры memuai.*, uvicorn, провайдеры). Раньше они уходили только
в консоль, которой у собранного .exe нет.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List

MAX_RECORDS = 4000
_LEVELS = ("debug", "info", "warning", "error", "critical")


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = MAX_RECORDS) -> None:
        super().__init__()
        self._records: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._counter = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info:
            try:
                message += "\n" + self.format(record).split("\n", 1)[-1]
            except Exception:
                pass
        with self._lock:
            self._counter += 1
            self._records.append({
                "id": self._counter,
                "ts": record.created,
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": message[:4000],
            })

    def snapshot(self, *, limit: int = 300, after: int = 0, level: str = "",
                 query: str = "") -> Dict[str, Any]:
        needle = (query or "").strip().lower()
        wanted = level.strip().lower()
        threshold = _LEVELS.index(wanted) if wanted in _LEVELS else -1
        with self._lock:
            rows = list(self._records)
        out: List[Dict[str, Any]] = []
        for row in rows:
            if after and row["id"] <= after:
                continue
            if threshold >= 0:
                current = row["level"]
                if current not in _LEVELS or _LEVELS.index(current) < threshold:
                    continue
            if needle and needle not in f"{row['logger']} {row['message']}".lower():
                continue
            out.append(row)
        limit = max(1, min(int(limit or 300), 2000))
        return {"records": out[-limit:], "total": len(out),
                "cursor": rows[-1]["id"] if rows else 0, "time": time.time()}

    # Панель обращается к буферу как к «списку записей».
    def list(self, *, limit: int = 300, after: int = 0, level: str = "",
             query: str = "") -> Dict[str, Any]:
        return self.snapshot(limit=limit, after=after, level=level, query=query)

    def install(self, level: int = logging.INFO) -> "RingBufferHandler":
        return install(level)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


app_log = RingBufferHandler()


def install(level: int = logging.INFO) -> RingBufferHandler:
    """Подключает буфер к корневому логгеру (идемпотентно).

    Корневой логгер по умолчанию отбрасывает всё ниже WARNING, поэтому без
    понижения уровня буфер оставался бы пустым и вкладка «Журнал сервера» —
    бесполезной. Шумные сторонние логгеры глушим отдельно.
    """
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    for noisy in ("httpx", "httpcore", "curl_cffi", "asyncio", "urllib3",
                  "websockets", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if app_log not in root.handlers:
        app_log.setLevel(level)
        app_log.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(app_log)
    return app_log
