"""Long read-only jobs of MemuFiles with progress: folder size, disk usage map, duplicate finder, hashes.

``Jobs.start(kind, args)`` → id; the page polls ``Jobs.get(id)`` (progress +
result when done) and may ``cancel``. Nothing here changes files.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .fsops import FsError, file_hash

log = logging.getLogger("memufiles.jobs")
SKIP_DIRS = {"$Recycle.Bin", "System Volume Information"}

class Job:
    def __init__(self, kind: str, args: Dict[str, Any]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.args = args
        self.status = "running"
        self.progress: Dict[str, Any] = {"files": 0, "bytes": 0, "current": ""}
        self.result: Any = None
        self.error = ""
        self.cancel = threading.Event()
        self.started = time.time()
        self.finished = 0.0

    def snapshot(self, with_result: bool = True) -> Dict[str, Any]:
        data = {"id": self.id, "kind": self.kind, "status": self.status, "progress": dict(self.progress),
                "error": self.error, "elapsed": round((self.finished or time.time()) - self.started, 2)}
        if with_result and self.status == "done":
            data["result"] = self.result
        return data

EXTRA_RUNNERS: Dict[str, Any] = {}

class Jobs:
    def __init__(self) -> None:
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, args: Dict[str, Any]) -> Job:
        runner = getattr(self, "_" + kind, None)
        if runner is None and not EXTRA_RUNNERS:
            from . import extras
            extras.register(EXTRA_RUNNERS)
        runner = runner or EXTRA_RUNNERS.get(kind)
        if runner is None or kind.startswith("_"):
            raise FsError("bad_kind", kind)
        job = Job(kind, args)
        with self.lock:
            self.jobs[job.id] = job
            if len(self.jobs) > 60:
                for old in sorted(self.jobs.values(), key=lambda j: j.started)[:20]:
                    if old.status != "running":
                        self.jobs.pop(old.id, None)

        def run() -> None:
            try:
                job.result = runner(job)
                job.status = "cancelled" if job.cancel.is_set() else "done"
            except FsError as exc:
                job.status, job.error = ("cancelled" if exc.code == "cancelled" else "error"), exc.code
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s failed", kind)
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"[:300]
            job.finished = time.time()
        threading.Thread(target=run, name=f"memufiles-job-{kind}", daemon=True).start()
        if args.get("wait"):
            deadline = time.time() + float(args.get("wait") or 0)
            while job.status == "running" and time.time() < deadline:
                time.sleep(0.02)
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise FsError("no_job", job_id)
        return job

    @staticmethod
    def _walk_size(job: Job, path: str) -> Dict[str, int]:
        total = files = folders = 0
        stack = [path]
        while stack:
            if job.cancel.is_set():
                raise FsError("cancelled")
            folder = stack.pop()
            try:
                with os.scandir(folder) as handle:
                    for entry in handle:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in SKIP_DIRS:
                                    stack.append(entry.path)
                                folders += 1
                            else:
                                total += entry.stat(follow_symlinks=False).st_size
                                files += 1
                        except OSError:
                            continue
            except OSError:
                continue
            job.progress.update(files=job.progress.get("files", 0) + 0, current=folder)
            job.progress["bytes_now"] = total
        return {"size": total, "files": files, "folders": folders}

    def _size(self, job: Job) -> Dict[str, Any]:
        """Total size of each of ``args["paths"]`` (folders recursively)."""
        out: Dict[str, Any] = {"items": {}, "size": 0, "files": 0, "folders": 0}
        for path in job.args.get("paths") or []:
            if os.path.isdir(path) and not os.path.islink(path):
                info = self._walk_size(job, path)
                if len(job.args.get("paths") or []) > 1:
                    out["folders"] += 1
            else:
                try:
                    info = {"size": os.lstat(path).st_size, "files": 1, "folders": 0}
                except OSError:
                    info = {"size": 0, "files": 0, "folders": 0}
            out["items"][path] = info
            out["size"] += info["size"]
            out["files"] += info["files"]
            out["folders"] += info["folders"]
            job.progress.update(files=out["files"], bytes=out["size"])
        return out

    def _usage(self, job: Job) -> Dict[str, Any]:
        """Disk usage map of ``args["path"]``: every child with its recursive size, biggest first."""
        root = job.args.get("path") or ""
        children: List[Dict[str, Any]] = []
        try:
            entries = list(os.scandir(root))
        except OSError as exc:
            raise FsError("not_found", str(exc)) from exc
        loose_size = loose_count = 0
        for entry in entries:
            if job.cancel.is_set():
                raise FsError("cancelled")
            try:
                if entry.is_dir(follow_symlinks=False):
                    info = self._walk_size(job, entry.path)
                    children.append({"path": entry.path, "name": entry.name, "is_dir": True, **info})
                else:
                    size = entry.stat(follow_symlinks=False).st_size
                    children.append({"path": entry.path, "name": entry.name, "is_dir": False, "size": size,
                                     "files": 1, "folders": 0})
                    loose_size += size
                    loose_count += 1
            except OSError:
                continue
            job.progress.update(files=len(children), bytes=sum(c["size"] for c in children), current=entry.name)
        children.sort(key=lambda c: -c["size"])
        total = sum(c["size"] for c in children)
        try:
            import shutil
            usage = shutil.disk_usage(root)
            disk = {"total": usage.total, "free": usage.free}
        except OSError:
            disk = {}
        return {"path": root, "size": total, "children": children[:500], "disk": disk,
                "loose": {"size": loose_size, "count": loose_count}}

    def _dupes(self, job: Job) -> Dict[str, Any]:
        """Duplicate files under ``args["path"]``: group by size → first 64 KB hash → full SHA-256."""
        root = job.args.get("path") or ""
        min_size = int(job.args.get("min_size") or 1)
        by_size: Dict[int, List[str]] = defaultdict(list)
        stack = [root]
        scanned = 0
        while stack:
            if job.cancel.is_set():
                raise FsError("cancelled")
            folder = stack.pop()
            try:
                with os.scandir(folder) as handle:
                    for entry in handle:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                                    stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                size = entry.stat(follow_symlinks=False).st_size
                                if size >= min_size:
                                    by_size[size].append(entry.path)
                                scanned += 1
                        except OSError:
                            continue
            except OSError:
                continue
            job.progress.update(files=scanned, current=folder, phase="scan")
        candidates = {size: paths for size, paths in by_size.items() if len(paths) > 1}

        def partial(path: str) -> str:
            with open(path, "rb") as handle:
                return hashlib.sha256(handle.read(65536)).hexdigest()
        groups: List[Dict[str, Any]] = []
        done = 0
        total = sum(len(p) for p in candidates.values())
        for size, paths in sorted(candidates.items(), key=lambda kv: -kv[0]):
            by_partial: Dict[str, List[str]] = defaultdict(list)
            for path in paths:
                if job.cancel.is_set():
                    raise FsError("cancelled")
                try:
                    by_partial[partial(path)].append(path)
                except OSError:
                    pass
                done += 1
                job.progress.update(phase="hash", done=done, total=total, current=path)
            for same in by_partial.values():
                if len(same) < 2:
                    continue
                if size > 65536:
                    by_full: Dict[str, List[str]] = defaultdict(list)
                    for path in same:
                        try:
                            by_full[file_hash(path, "sha256", job.cancel)].append(path)
                        except OSError:
                            pass
                    sets = [v for v in by_full.values() if len(v) > 1]
                else:
                    sets = [same]
                for paths_set in sets:
                    rows = []
                    for path in paths_set:
                        try:
                            mtime = int(os.stat(path).st_mtime * 1000)
                        except OSError:
                            mtime = 0
                        rows.append({"path": path, "mtime": mtime})
                    rows.sort(key=lambda r: r["mtime"])
                    groups.append({"size": size, "files": rows, "wasted": size * (len(rows) - 1)})
        groups.sort(key=lambda g: -g["wasted"])
        return {"path": root, "groups": groups[:2000], "scanned": scanned,
                "wasted": sum(g["wasted"] for g in groups)}

    def _hash(self, job: Job) -> Dict[str, Any]:
        path = job.args.get("path") or ""
        return {algo: file_hash(path, algo, job.cancel) for algo in ("md5", "sha1", "sha256")}
