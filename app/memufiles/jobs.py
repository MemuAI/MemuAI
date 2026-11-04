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
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cache import HashCache, cache_path, stat_key
from .fsops import FsError, file_hash, hash_file_both, hash_head

log = logging.getLogger("memufiles.jobs")
SKIP_DIRS = {"$Recycle.Bin", "System Volume Information"}
# Duplicate search only: build caches, VCS checkouts and the like are never
# worth comparing, and scanning them is what made the old search crawl.
# _walk_size/_usage keep using SKIP_DIRS so their totals do not change.
DUPES_SKIP_DIRS = SKIP_DIRS | {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", ".tox",
    "__pycache__", ".cache", ".idea", ".vscode", "dist", "build", "target",
}
_DUPES_SKIP_LOWER = {name.lower() for name in DUPES_SKIP_DIRS}

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

    @staticmethod
    def _dupes_workers(job: Job) -> int:
        """Hashing threads: enough to overlap disk reads, never a swarm."""
        try:
            wanted = int(job.args.get("workers") or 0)
        except (TypeError, ValueError):
            wanted = 0
        if wanted:
            return max(1, min(16, wanted))
        return min(8, max(4, os.cpu_count() or 4))

    @staticmethod
    def _dupes_cache(job: Job):
        """The persistent hash cache, or None when it is disabled/broken."""
        if "cache_path" in job.args:
            path = job.args.get("cache_path")
            return HashCache(path) if path else None
        try:
            return HashCache(cache_path())
        except Exception:  # noqa: BLE001 - a cache must never break the search
            return None

    def _run_parallel(self, job: Job, paths: List[str], func: Callable[[str], Any],
                      phase: str, total: int, done_offset: int = 0) -> Dict[str, Any]:
        """Run ``func`` over ``paths`` in a thread pool, results keyed by path.

        Order of completion does not matter: callers sort before grouping,
        so the result is deterministic. ``OSError`` (a file that vanished
        mid-run) drops that path, as the old single-threaded code did.
        """
        results: Dict[str, Any] = {}
        if not paths:
            return results
        if self._dupes_workers(job) <= 1:
            for index, path in enumerate(paths):
                if job.cancel.is_set():
                    raise FsError("cancelled")
                try:
                    results[path] = func(path)
                except OSError:
                    pass
                job.progress.update(phase=phase, done=done_offset + index + 1,
                                    total=total, current=path)
            return results
        pool = ThreadPoolExecutor(max_workers=self._dupes_workers(job),
                                  thread_name_prefix="memufiles-dupes")
        futures = {}
        try:
            futures = {pool.submit(func, path): path for path in paths}
            done = 0
            for future in as_completed(futures):
                path = futures[future]
                if job.cancel.is_set():
                    raise FsError("cancelled")
                try:
                    results[path] = future.result()
                except OSError:
                    pass
                done += 1
                job.progress.update(phase=phase, done=done_offset + done,
                                    total=total, current=path)
        except BaseException:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return results

    def _dupes(self, job: Job) -> Dict[str, Any]:
        """Duplicate files under ``args["path"]``: group by size → first 64 KB hash → full SHA-256.

        The full hash is computed in one streaming pass together with the
        prefix hash, in parallel, and cached on disk keyed by size/mtime/
        inode — a repeat run over unchanged files barely touches the disk.
        Hard links share an inode, so they are hashed once and never form a
        group on their own. Result shape is unchanged from before.
        """
        root = job.args.get("path") or ""
        min_size = int(job.args.get("min_size") or 1)
        by_size: Dict[int, List[str]] = defaultdict(list)
        aliases: Dict[str, List[str]] = defaultdict(list)
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
                                if entry.name.lower() not in _DUPES_SKIP_LOWER \
                                        and not entry.name.startswith("."):
                                    stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat(follow_symlinks=False)
                                scanned += 1
                                if st.st_size >= min_size:
                                    by_size[st.st_size].append(entry.path)
                        except OSError:
                            continue
            except OSError:
                continue
            job.progress.update(files=scanned, current=folder, phase="scan")
        # Only candidates need a fresh stat. Windows DirEntry.stat() omits
        # the file ID, while stat_key supplies it for both aliases and cache.
        candidates = {}
        keys = {}
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            first_by_inode = {}
            distinct = []
            for path in sorted(paths):
                if job.cancel.is_set():
                    raise FsError("cancelled")
                key = stat_key(path)
                if key is None or key[1] != size:
                    continue
                identity = key[3:5]
                if key[4] and identity in first_by_inode:
                    aliases[first_by_inode[identity]].append(path)
                    continue
                if key[4]:
                    first_by_inode[identity] = path
                distinct.append(path)
                keys[path] = key
            if len(distinct) > 1:
                candidates[size] = distinct
        ordered = [path for size in sorted(candidates, key=lambda s: -s) for path in candidates[size]]
        keys = {path: keys[path] for path in ordered}
        total = len(ordered)

        cache = self._dupes_cache(job)
        try:
            cached: Dict[str, Tuple[str, str]] = {}
            if cache is not None and keys:
                try:
                    cached = cache.lookup(list(keys.values()))
                except Exception:  # noqa: BLE001 - fall through to hashing
                    cached = {}
            prefixes: Dict[str, str] = {}
            fulls: Dict[str, str] = {}
            for path, key in keys.items():
                hit = cached.get(key[0])
                if hit is not None:
                    fulls[path], prefixes[path] = hit
            job.progress.update(phase="hash", done=len(prefixes), total=total, current="")
            need_head = [path for path in ordered if path in keys and path not in prefixes]
            heads = self._run_parallel(job, need_head, partial(hash_head, cancel=job.cancel),
                                       "hash", total, done_offset=len(prefixes))
            prefixes.update(heads)

            by_partial: Dict[int, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
            for size, paths in candidates.items():
                for path in paths:
                    if path in prefixes:
                        by_partial[size][prefixes[path]].append(path)
            need_full = [path for size, groups in by_partial.items() if size > 65536
                         for same in groups.values() if len(same) > 1 for path in same
                         if path not in fulls]
            if need_full:
                both = self._run_parallel(job, need_full, partial(hash_file_both, cancel=job.cancel),
                                          "hash", total + len(need_full), done_offset=total)
                fresh = []
                for path, (full, prefix) in both.items():
                    # The file must still be the one we started with, or the
                    # hash is a mix of two versions: drop it, do not cache it.
                    if stat_key(path) != keys.get(path):
                        continue
                    fulls[path] = full
                    prefixes[path] = prefix
                    fresh.append((keys[path], full, prefix))
                if cache is not None and fresh:
                    try:
                        cache.store(fresh)
                    except Exception:  # noqa: BLE001 - caching is best-effort
                        pass
        finally:
            if cache is not None:
                cache.close()

        groups: List[Dict[str, Any]] = []
        for size in sorted(by_partial, key=lambda s: -s):
            for same in by_partial[size].values():
                if len(same) < 2:
                    continue
                if size > 65536:
                    by_full: Dict[str, List[str]] = defaultdict(list)
                    for path in same:
                        if path in fulls:
                            by_full[fulls[path]].append(path)
                    sets = [v for v in by_full.values() if len(v) > 1]
                else:
                    sets = [same]
                for paths_set in sets:
                    rows = []
                    for path in paths_set:
                        for shown in [path, *aliases.get(path, [])]:
                            try:
                                mtime = int(os.stat(shown).st_mtime * 1000)
                            except OSError:
                                mtime = 0
                            rows.append({"path": shown, "mtime": mtime})
                    rows.sort(key=lambda r: (r["mtime"], r["path"]))
                    groups.append({"size": size, "files": rows,
                                   "wasted": size * (len(paths_set) - 1)})
        groups.sort(key=lambda g: -g["wasted"])
        job.progress.update(phase="group", done=len(groups), total=len(groups), current="")
        return {"path": root, "groups": groups[:2000], "scanned": scanned,
                "wasted": sum(g["wasted"] for g in groups)}

    def _hash(self, job: Job) -> Dict[str, Any]:
        path = job.args.get("path") or ""
        return {algo: file_hash(path, algo, job.cancel) for algo in ("md5", "sha1", "sha256")}
