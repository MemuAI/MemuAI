# -*- coding: utf-8 -*-
"""Background jobs of MemuUtils (auto-clicker, auto-typing, keep-awake): a thread with a stop flag the window
polls and stops. One job per kind at a time; every job ends by itself (a count, a deadline or Esc)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

_LOCK = threading.Lock()
JOBS: Dict[str, "Job"] = {}

class Job:
    def __init__(self, kind: str, title: str, title_en: str) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title, self.title_en = kind, title, title_en
        self.stop_event = threading.Event()
        self.started = time.time()
        self.ended: Optional[float] = None
        self.count = 0
        self.status = "running"
        self.message = ""
        self.message_en = ""
        self.thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    def public(self, en: bool = False) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title_en if en else self.title, "status": self.status, "running": self.running,
                "count": self.count, "started": self.started, "ended": self.ended,
                "elapsed": round((self.ended or time.time()) - self.started, 1),
                "message": (self.message_en or self.message) if en else self.message}

    def sleep(self, seconds: float) -> bool:
        """Wait; True when the job was asked to stop meanwhile."""
        return self.stop_event.wait(max(0.0, seconds))

def start(kind: str, title: str, title_en: str, target: Callable[[Job], None]) -> Job:
    with _LOCK:
        for job in list(JOBS.values()):
            if job.kind == kind and job.running:
                job.stop_event.set()
        for jid, job in list(JOBS.items()):
            if not job.running and job.ended and time.time() - job.ended > 3600:
                JOBS.pop(jid, None)
        job = Job(kind, title, title_en)
        JOBS[job.id] = job

    def run() -> None:
        try:
            target(job)
            if job.status == "running":
                job.status = "stopped" if job.stop_event.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - the window shows the reason
            job.status = "error"
            job.message = job.message_en = str(exc)[:300]
        finally:
            job.ended = time.time()
    job.thread = threading.Thread(target=run, name=f"memuutils-{kind}", daemon=True)
    job.thread.start()
    return job

def stop(job_id: str = "", kind: str = "") -> int:
    n = 0
    with _LOCK:
        for job in JOBS.values():
            if job.running and (job.id == job_id or (kind and job.kind == kind) or (not job_id and not kind)):
                job.stop_event.set()
                n += 1
    return n

def listing(en: bool = False) -> List[Dict[str, Any]]:
    with _LOCK:
        return [j.public(en) for j in sorted(JOBS.values(), key=lambda j: -j.started)][:20]
