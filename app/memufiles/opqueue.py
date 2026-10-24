"""Background file operations of MemuFiles: copy / move / trash / delete / rename / extract / compress.

Every operation is an :class:`Operation` run on its own worker thread (at most
``MAX_PARALLEL`` at a time, the rest wait in the queue). The page sees progress
through ``notify`` (pushed to its long-poll event stream) and controls it with
:meth:`OpQueue.pause` / :meth:`resume` / :meth:`cancel` / :meth:`resolve`.

Conflicts (a file with the same name at the destination) pause the operation in
the ``conflict`` state until the user answers ``replace`` / ``skip`` /
``keep_both`` / ``rename`` (with a new name), optionally "for all". Folders with
the same name are merged (their files may conflict one by one).

Undo: a finished operation records inverse steps; :meth:`OpQueue.undo` runs
them as an ``undo`` operation (copies go to the Recycle Bin, moves and renames
go back, trashed items are restored). Permanent deletes cannot be undone.

Round 40 (r40-files-core): honest progress — ``unit`` says what the bar counts
(bytes for copy / cross-volume move / archives / uploads, items for deletes,
trash and same-volume moves that are plain renames), a smoothed speed with a
history for the graph (``speed_hist``), ``eta``, the current file's own
progress, "skip this file" (:meth:`OpQueue.skip`), per-file errors with
:meth:`OpQueue.retry`, a cancelled cross-volume move removes its half copy,
and two new kinds: ``upload`` (files dropped without a path — Explorer zip
folders, Outlook attachments, browser images — streamed by the page, see
:meth:`OpQueue.upload_prepare` / :meth:`OpQueue.upload_put`) and ``download``
(a link dropped from a browser).
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import stat as statmod
import subprocess
import tarfile
import threading
import time
import uuid
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import archive as archive_mod, fsops, trash
from .fsops import FsError

log = logging.getLogger("memufiles.ops")

MAX_PARALLEL = 2
CHUNK = 4 * 1024 * 1024
KINDS = ("copy", "move", "trash", "delete", "rename", "extract", "compress", "undo", "mkdir", "upload", "download",
         "organize",   # organize: r40-files-max (AI «sort by meaning», _do_organize)
         "vpaste",     # vpaste: r40-files-core (virtual files on the clipboard: zip folders, Outlook)
         "arcadd", "write")
CONFLICT_ACTIONS = ("replace", "skip", "keep_both", "rename", "cancel")
BYTE_KINDS = ("copy", "move", "extract", "compress", "upload", "download", "vpaste", "arcadd")   # arcadd: r41-files
HIST_STEP = 0.5            # seconds between speed-graph points
HIST_LEN = 120             # graph points kept (halved when full, so they always span the whole operation)
UPLOAD_STALL = 300.0       # an upload the page stopped feeding fails after this many idle seconds


class Cancelled(Exception):
    pass


class SkipFile(Exception):
    """The user pressed "skip this file": the current file is dropped (its partial copy removed), the rest goes on."""


class Operation:
    def __init__(self, kind: str, sources: List[str], dest: str = "", args: Optional[Dict[str, Any]] = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.sources = list(sources)
        self.dest = dest
        self.args = dict(args or {})
        self.status = "queued"         # queued | scanning | running | paused | conflict | done | error | cancelled
        self.created = time.time()
        self.started = 0.0
        self.finished = 0.0
        self.total_bytes = 0
        self.done_bytes = 0
        self.total_items = 0
        self.done_items = 0
        self.current = ""
        self.errors: List[Dict[str, str]] = []
        self.conflict: Optional[Dict[str, Any]] = None
        self.policy: Optional[str] = None          # "for all" answer to conflicts
        self.undo: List[Tuple[str, ...]] = []
        self.created_paths: List[str] = []          # top-level results (selected in the page afterwards)
        self.undone = False
        self.message = ""
        self._run = threading.Event()
        self._run.set()
        self._cancel = threading.Event()
        self._decision = threading.Event()
        self._answer: Dict[str, Any] = {}
        self._last_notify = 0.0
        self._speed_mark = (time.monotonic(), 0)
        self.speed = 0.0
        self.skipped: List[str] = []
        self._skip = threading.Event()
        self.speed_hist: List[List[int]] = []     # [progress ‰, bytes/s]
        self.cur_size = 0               # the file being written now: its size and how much of it is done
        self.cur_done = 0
        self._t0 = 0.0                  # monotonic start; paused / waiting time is excluded from "elapsed"
        self._t_end = 0.0
        self._idle = 0.0
        self.upload: Optional[Dict[str, Any]] = None
        self._rev = 0

    # ---------------------------------------------------------------- state
    @property
    def unit(self) -> str:
        return "bytes" if self.kind in BYTE_KINDS and self.total_bytes > 0 else "items"

    def elapsed(self) -> float:
        if not self._t0:
            return 0.0
        end = self._t_end or time.monotonic()
        return max(0.0, end - self._t0 - self._idle)

    def eta(self) -> Optional[int]:
        if self.status not in ("running", "paused", "conflict"):
            return None
        if self.unit == "bytes":
            left = self.total_bytes - self.done_bytes
            return int(left / self.speed) + 1 if self.speed > 1 and left > 0 else None
        left = self.total_items - self.done_items
        took = self.elapsed()
        if self.done_items < 2 or took < 1 or left <= 0:
            return None
        return int(left * took / self.done_items) + 1

    def snapshot(self) -> Dict[str, Any]:
        first = self.sources[0] if self.sources else ""
        self._rev += 1          # the page keeps the newest snapshot (a submit reply can arrive after later events)
        return {"rev": self._rev, "id": self.id, "kind": self.kind, "status": self.status, "sources": self.sources[:50],
                "count": len(self.sources), "dest": self.dest, "total_bytes": self.total_bytes,
                "done_bytes": min(self.done_bytes, self.total_bytes) if self.total_bytes else self.done_bytes,
                "total_items": max(self.total_items, self.done_items), "done_items": self.done_items,
                "current": self.current, "errors": self.errors[-20:], "error_count": len(self.errors),
                "conflict": self.conflict, "created": self.created, "started": self.started,
                "finished": self.finished, "speed": round(self.speed), "undoable": bool(self.undo) and not self.undone
                and self.status in ("done", "cancelled", "error"), "undone": self.undone,
                "results": self.created_paths[:200], "message": self.message,
                "label": self.args.get("label") or "",
                # Round 40: what the bar counts, the speed graph, time left, the current file, skipped files
                "unit": self.unit, "speed_hist": list(self.speed_hist), "eta": self.eta(),
                "elapsed": round(self.elapsed(), 1), "cur_size": self.cur_size, "cur_done": self.cur_done,
                "skipped": len(self.skipped), "src_dir": os.path.dirname(first.rstrip("\\/")) if first and
                "://" not in first else "", "retryable": any(e.get("retry") for e in self.errors)}

    def check(self) -> None:
        """Honour pause and cancel (called between files and between chunks)."""
        if self._cancel.is_set():
            raise Cancelled()
        if not self._run.is_set():
            previous = self.status
            self.status = "paused"
            waited = time.monotonic()
            self._run.wait()
            self._idle += time.monotonic() - waited
            self._speed_mark = (time.monotonic(), self.done_bytes)
            if self._cancel.is_set():
                raise Cancelled()
            self.status = previous if previous != "paused" else "running"

    def check_file(self) -> None:
        """:meth:`check` plus "skip this file" — only inside a file's chunk loop."""
        self.check()
        if self._skip.is_set():
            self._skip.clear()
            raise SkipFile()

    def add_bytes(self, count: int) -> None:
        self.done_bytes += count
        self.cur_done += count
        now = time.monotonic()
        mark_t, mark_b = self._speed_mark
        if now - mark_t >= HIST_STEP:
            instant = (self.done_bytes - mark_b) / max(now - mark_t, 1e-3)
            # smoothed like Explorer: one slow chunk (antivirus, a cache flush) does not swing the time left
            self.speed = instant if not self.speed else 0.35 * instant + 0.65 * self.speed
            self._speed_mark = (now, self.done_bytes)
            # the graph's x axis is the progress of the whole operation (like Explorer), y the speed
            done = int(1000 * self.done_bytes / self.total_bytes) if self.total_bytes else 0
            self.speed_hist.append([min(1000, done), int(instant)])
            if len(self.speed_hist) > HIST_LEN:
                self.speed_hist = self.speed_hist[1::2]          # long operations: half the resolution, same span

    def start_file(self, path: str, size: int) -> None:
        self._skip.clear()              # a "skip" pressed as the previous file finished must not hit this one
        self.current = path
        self.cur_size = max(0, size)
        self.cur_done = 0

    def skip_current(self, size: int, written: int) -> None:
        """Book-keeping of a skipped file: its bytes leave the total, the file counts as handled."""
        self.done_bytes -= written
        self.total_bytes = max(self.done_bytes, self.total_bytes - size)
        self.skipped.append(self.current)
        self.done_items += 1


class OpQueue:
    def __init__(self, notify: Optional[Callable[[Dict[str, Any]], None]] = None,
                 changed: Optional[Callable[[List[str]], None]] = None) -> None:
        self.ops: Dict[str, Operation] = {}
        self.order: List[str] = []
        self.notify_cb = notify
        self.changed_cb = changed
        self.slots = threading.Semaphore(MAX_PARALLEL)
        self.lock = threading.Lock()
        self.undo_stack: List[str] = []
        self.redo_stack: List[str] = []      # r40-files-2: undone operations Ctrl+Y can do again (Explorer)

    # ---------------------------------------------------------------- public
    def submit(self, kind: str, sources: List[str], dest: str = "", **args: Any) -> Operation:
        if kind not in KINDS:
            raise FsError("bad_kind", kind)
        op = Operation(kind, sources, dest, args)
        with self.lock:
            self.ops[op.id] = op
            self.order.append(op.id)
            if kind != "undo" and not args.get("redo_of"):
                self.redo_stack.clear()          # a new action ends the redo chain, as in Explorer
            self._trim()
        self._notify(op, force=True)
        thread = threading.Thread(target=self._worker, args=(op,), name=f"memufiles-op-{op.id}", daemon=True)
        thread.start()
        if args.get("wait"):
            thread.join()
        return op

    def record(self, kind: str, sources: List[str], undo: List[Tuple[str, ...]], results: List[str],
               label: str = "") -> Operation:
        """An operation done synchronously elsewhere (new folder, single rename) — only for the undo stack."""
        op = Operation(kind, sources, "", {"label": label})
        op.status = "done"
        op.started = op.finished = time.time()
        op.undo = undo
        op.created_paths = results
        op.total_items = op.done_items = len(sources) or 1
        with self.lock:
            self.ops[op.id] = op
            self.order.append(op.id)
            if undo:
                self.undo_stack.append(op.id)
                self.redo_stack.clear()
            self._trim()
        self._notify(op, force=True)
        return op

    def get(self, op_id: str) -> Operation:
        op = self.ops.get(op_id)
        if op is None:
            raise FsError("no_operation", op_id)
        return op

    def list(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [self.ops[i].snapshot() for i in self.order if i in self.ops]

    def pause(self, op_id: str) -> None:
        op = self.get(op_id)
        if op.status in ("running", "scanning", "queued"):
            op._run.clear()
            op.status = "paused"
            self._notify(op, force=True)

    def resume(self, op_id: str) -> None:
        op = self.get(op_id)
        op._run.set()
        if op.status == "paused":
            op.status = "running"
        self._notify(op, force=True)

    def cancel(self, op_id: str) -> None:
        op = self.get(op_id)
        op._cancel.set()
        op._run.set()
        op._answer = {"action": "cancel"}
        op._decision.set()
        self._notify(op, force=True)

    def skip(self, op_id: str) -> None:
        """Skip the file being copied / moved / extracted / uploaded now (its partial copy is removed)."""
        op = self.get(op_id)
        if op.status in ("running", "paused") and op.cur_size >= 0:
            op._skip.set()
            if op.status == "paused":
                op._run.set()
                op.status = "running"
            self._notify(op, force=True)

    def retry(self, op_id: str) -> Operation:
        """A new operation for what failed in *op_id* (files that were locked, a drive that came back…)."""
        op = self.get(op_id)
        if op.status not in ("done", "error", "cancelled"):
            raise FsError("busy")
        failed = [e for e in op.errors if e.get("retry")]
        if not failed:
            raise FsError("nothing_to_retry")
        args = {k: v for k, v in op.args.items() if k in ("copy_suffix", "label", "on_conflict", "scope")}
        if op.kind in ("copy", "move"):
            pairs = []
            for err in failed:
                if err.get("dest") and [err["path"], err["dest"]] not in pairs:
                    pairs.append([err["path"], err["dest"]])
            new = self.submit(op.kind, [p for p, _ in pairs], op.dest, pairs_to=pairs, **args)
        elif op.kind in ("trash", "delete"):
            new = self.submit(op.kind, list(dict.fromkeys(e["path"] for e in failed)), "", **args)
        elif op.kind == "download":
            new = self.submit(op.kind, list(op.sources), op.dest, **{**args, "url": op.args.get("url", "")})
        else:
            new = self.submit(op.kind, list(op.sources), op.dest, **args)
        op.errors = [e for e in op.errors if not e.get("retry")]
        self._notify(op, force=True)
        return new

    def resolve(self, op_id: str, action: str, apply_all: bool = False, new_name: str = "") -> None:
        op = self.get(op_id)
        if action not in CONFLICT_ACTIONS:
            raise FsError("bad_action", action)
        op._answer = {"action": action, "all": bool(apply_all), "name": str(new_name or "")}
        op._decision.set()

    def clear_finished(self) -> None:
        with self.lock:
            keep = [i for i in self.order if self.ops[i].status not in ("done", "error", "cancelled")
                    or i in self.undo_stack[-50:]]
            for op_id in self.order:
                if op_id not in keep:
                    self.ops.pop(op_id, None)
            self.order = keep

    def undo(self, op_id: str = "") -> Operation:
        with self.lock:
            if not op_id:
                candidates = [i for i in reversed(self.undo_stack) if i in self.ops and not self.ops[i].undone]
                if not candidates:
                    raise FsError("nothing_to_undo")
                op_id = candidates[0]
        target = self.get(op_id)
        if target.undone or not target.undo:
            raise FsError("nothing_to_undo")
        if target.status not in ("done", "cancelled", "error"):
            raise FsError("busy")
        target.undone = True
        self._notify(target, force=True)
        return self.submit("undo", [], "", steps=list(reversed(target.undo)), of=target.id, of_kind=target.kind)

    REDO_KINDS = ("copy", "move", "trash", "rename", "extract", "compress", "mkdir", "organize", "write")

    def redo(self) -> Operation:
        """r40-files-2: Ctrl+Y — run the last undone operation again (the same sources, destination and options;
        a single rename / new folder done in place is replayed from what it recorded)."""
        with self.lock:
            target = None
            while self.redo_stack:
                cand = self.ops.get(self.redo_stack.pop())
                if cand is not None and cand.undone and cand.kind in self.REDO_KINDS:
                    target = cand
                    break
        if target is None:
            raise FsError("nothing_to_redo")
        args = {k: v for k, v in target.args.items() if k not in ("wait", "redo_of")}
        if target.kind == "rename" and not args.get("pairs") and target.sources and target.created_paths:
            args["pairs"] = [[target.sources[0], os.path.basename(target.created_paths[0])]]
        return self.submit(target.kind, list(target.sources), target.dest, redo_of=target.id, **args)

    # ---------------------------------------------------------------- internals
    def _trim(self) -> None:
        if len(self.order) > 300:
            drop = [i for i in self.order[:-300] if self.ops[i].status in ("done", "error", "cancelled")]
            for op_id in drop:
                self.ops.pop(op_id, None)
                self.order.remove(op_id)
                if op_id in self.undo_stack:
                    self.undo_stack.remove(op_id)

    def _notify(self, op: Operation, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - op._last_notify < 0.25:
            return
        op._last_notify = now
        if self.notify_cb:
            try:
                self.notify_cb(op.snapshot())
            except Exception:  # noqa: BLE001
                log.exception("notify failed")

    def _changed(self, paths: List[str]) -> None:
        if self.changed_cb:
            folders = sorted({p if os.path.isdir(p) and not p.endswith((".zip",)) else os.path.dirname(p)
                              for p in paths if p})
            try:
                self.changed_cb(folders)
            except Exception:  # noqa: BLE001
                pass

    def _worker(self, op: Operation) -> None:
        # an upload is paced by the page (one request per file) and must not wait behind two long copies
        with (contextlib.nullcontext() if op.kind == "upload" else self.slots):
            if op._cancel.is_set():
                op.status = "cancelled"
                op.finished = time.time()
                self._notify(op, force=True)
                return
            op.started = time.time()
            op._t0 = time.monotonic()
            op._speed_mark = (op._t0, 0)
            op.status = "scanning"
            self._notify(op, force=True)
            try:
                self._scope_check(op, list(op.sources) + ([op.dest] if op.dest else []) +
                                  [m[1] for m in op.args.get("moves", [])])
                getattr(self, "_do_" + op.kind)(op)
                op.status = "done" if not op._cancel.is_set() else "cancelled"
            except (Cancelled, SkipFile):
                op.status = "cancelled" if op._cancel.is_set() else "done"
            except FsError as exc:
                op.status = "error"
                op.message = exc.code
                op.errors.append({"path": str(exc), "error": exc.code})
            except Exception as exc:  # noqa: BLE001
                log.exception("operation %s failed", op.kind)
                op.status = "error"
                op.message = f"{type(exc).__name__}: {exc}"[:300]
                op.errors.append({"path": op.current, "error": op.message})
            if op.status == "done" and op.errors:
                op.status = "error" if op.done_items == 0 else "done"
            op.finished = time.time()
            op._t_end = time.monotonic()
            op.conflict = None
            op.current = ""
            op.cur_size = op.cur_done = 0
            if op.status == "done" and op.unit == "bytes":
                op.done_bytes = op.total_bytes          # skipped / merged files: a finished bar is full
            if op.undo and op.kind != "undo":
                with self.lock:
                    self.undo_stack.append(op.id)
                    self.undo_stack = self.undo_stack[-50:]
            elif op.kind == "undo" and op.status == "done" and not op.errors and op.args.get("of"):
                with self.lock:
                    self.redo_stack.append(op.args["of"])
                    self.redo_stack = self.redo_stack[-50:]
            self._notify(op, force=True)
            touched = list(op.sources) + ([op.dest] if op.dest else []) + op.created_paths
            if op.kind == "undo":
                touched += [s[1] for s in op.args.get("steps", []) if len(s) > 1] + \
                           [s[2] for s in op.args.get("steps", []) if len(s) > 2 and isinstance(s[2], str)]
            self._changed(touched)

    # ---- helpers
    @staticmethod
    def _scope_check(op: Operation, paths: List[str]) -> None:
        scope = op.args.get("scope")
        if scope:
            root = os.path.realpath(scope)
            for path in paths:
                if not fsops.is_inside(os.path.realpath(path), root):
                    raise FsError("outside_scope", path)

    @staticmethod
    def _jobs(op: Operation) -> List[Tuple[str, str]]:
        """(source, destination folder) pairs: one destination, or per-item ones (a retry of failed files)."""
        pairs = op.args.get("pairs_to")
        if pairs:
            return [(str(a), str(b)) for a, b in pairs]
        return [(src, op.dest) for src in op.sources]

    @staticmethod
    def _same_volume(src: str, dest: str) -> bool:
        try:
            return os.lstat(src).st_dev == os.stat(dest).st_dev
        except OSError:
            return False

    @staticmethod
    def _tree_size(path: str) -> Tuple[int, int]:
        total_b, total_n = 0, 1
        if os.path.isdir(path) and not os.path.islink(path):
            for root, dirs, files in os.walk(path):
                total_n += len(files) + len(dirs)
                for name in files:
                    try:
                        total_b += os.lstat(os.path.join(root, name)).st_size
                    except OSError:
                        pass
        else:
            try:
                total_b = os.lstat(path).st_size
            except OSError:
                pass
        return total_b, total_n

    def _scan(self, op: Operation, paths: List[str]) -> None:
        """Count bytes and items up front, so the bar is honest from the first file."""
        total_b, total_n = 0, 0
        for path in paths:
            op.check()
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    for root, dirs, files in os.walk(path):
                        op.check()
                        total_n += len(files) + len(dirs)
                        for name in files:
                            try:
                                total_b += os.lstat(os.path.join(root, name)).st_size
                            except OSError:
                                pass
                        op.total_bytes, op.total_items = total_b, total_n     # "counting: 12 345 files, 3.4 GB"
                        self._notify(op)
                    total_n += 1
                else:
                    total_b += os.lstat(path).st_size
                    total_n += 1
            except OSError:
                total_n += 1
        op.total_bytes, op.total_items = total_b, total_n
        op.status = "running"
        self._notify(op, force=True)

    def _ask(self, op: Operation, src: str, dst: str, src_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Wait for the user's answer to a name conflict (or use the "for all" answer)."""
        if op.policy:
            return {"action": op.policy}
        preset = op.args.get("on_conflict")
        if preset in ("replace", "skip", "keep_both"):
            return {"action": preset}

        def info(path: str) -> Dict[str, Any]:
            try:
                st = os.stat(path)
                return {"path": path, "name": os.path.basename(path), "size": st.st_size,
                        "mtime": int(st.st_mtime * 1000), "is_dir": os.path.isdir(path)}
            except OSError:
                return {"path": path, "name": os.path.basename(path)}
        op._decision.clear()
        op.conflict = {"src": dict(src_info) if src_info else info(src), "dst": info(dst),
                       "suggested": fsops.unique_name(os.path.dirname(dst), os.path.basename(dst)),
                       "left": max(0, op.total_items - op.done_items)}
        previous = op.status
        op.status = "conflict"
        self._notify(op, force=True)
        waited = time.monotonic()
        op._decision.wait()
        op._idle += time.monotonic() - waited
        op._speed_mark = (time.monotonic(), op.done_bytes)
        answer = dict(op._answer)
        op.conflict = None
        op.status = previous if previous != "conflict" else "running"
        if answer.get("action") == "cancel":
            op._cancel.set()
            raise Cancelled()
        if answer.get("all") and answer.get("action") in ("replace", "skip", "keep_both"):
            op.policy = answer["action"]
        self._notify(op, force=True)
        return answer

    def _target_for(self, op: Operation, src: str, dst: str,
                    src_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Resolve a conflict at *dst*: the path to write to, or None to skip it."""
        if not os.path.lexists(dst):
            return dst
        if fsops.norm(src) == fsops.norm(dst):
            return None
        src_is_dir = bool(src_info.get("is_dir")) if src_info else os.path.isdir(src)
        if os.path.isdir(dst) and src_is_dir and not os.path.islink(dst):
            return dst                                       # folders merge
        answer = self._ask(op, src, dst, src_info)
        action = answer.get("action")
        if action == "skip":
            return None
        if action == "keep_both":
            return os.path.join(os.path.dirname(dst), fsops.unique_name(os.path.dirname(dst), os.path.basename(dst)))
        if action == "rename":
            name = answer.get("name") or ""
            error = fsops.valid_name(name)
            if error:
                op.errors.append({"path": dst, "error": error})
                return None
            new = os.path.join(os.path.dirname(dst), name)
            return self._target_for(op, src, new, src_info)
        if action == "replace":
            if os.path.isdir(dst) and not os.path.islink(dst):
                op.errors.append({"path": dst, "error": "is_directory"})
                return None
            fsops.ensure_mutable(dst)
            return dst
        return None

    def _copy_file(self, op: Operation, src: str, dst: str) -> None:
        """Chunked copy with honest byte progress; a cancelled / skipped / failed file never stays half-written
        (a replaced file is written to a temp name first, so the old copy survives)."""
        self._scope_check(op, [src, dst])
        try:
            size = os.lstat(src).st_size
        except OSError:
            size = 0
        op.start_file(src, size)
        if os.path.islink(src):
            if os.path.lexists(dst):
                os.unlink(dst)
            os.symlink(os.readlink(src), dst)
            op.done_items += 1
            return
        replacing = os.path.lexists(dst)
        temp = dst + f".~mfpart{os.getpid()}" if replacing else dst
        written = 0
        try:
            with open(src, "rb") as reader, open(temp, "wb") as writer:
                while True:
                    op.check_file()
                    block = reader.read(CHUNK)
                    if not block:
                        break
                    writer.write(block)
                    written += len(block)
                    op.add_bytes(len(block))
                    self._notify(op)
            try:
                shutil.copystat(src, temp)
            except OSError:
                pass
            if replacing:
                self._replace(temp, dst)
        except SkipFile:
            self._unlink_quiet(temp)
            op.skip_current(size, written)
            raise
        except BaseException:
            op.done_bytes -= written
            self._unlink_quiet(temp)
            raise
        op.done_items += 1

    @staticmethod
    def _unlink_quiet(path: str) -> None:
        try:
            if os.path.lexists(path):
                os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _replace(temp: str, dst: str) -> None:
        try:
            os.replace(temp, dst)
        except PermissionError:
            os.chmod(dst, statmod.S_IWRITE | statmod.S_IREAD)
            os.replace(temp, dst)

    def _copy_tree(self, op: Operation, src: str, dst: str, created: List[Tuple[str, ...]]) -> None:
        """Copy *src* (file or folder) to *dst* with conflict handling; records created top items in *created*."""
        op.check()
        self._scope_check(op, [src, dst])
        if os.path.isdir(src) and not os.path.islink(src):
            new_dir = not os.path.exists(dst)
            if new_dir:
                os.makedirs(dst)
                created.append(("trash_created", dst))
            op.done_items += 1
            with os.scandir(src) as handle:
                children = list(handle)
            for child in children:
                target = self._target_for(op, child.path, os.path.join(dst, child.name))
                if target is None:
                    op.done_items += 1
                    continue
                if new_dir:                       # inside a new folder: its removal undoes everything
                    self._copy_tree(op, child.path, target, [])
                else:
                    self._copy_tree(op, child.path, target, created)
            try:
                shutil.copystat(src, dst)
            except OSError:
                pass
            return
        existed = os.path.lexists(dst)
        try:
            self._copy_file(op, src, dst)
            if not existed:
                created.append(("trash_created", dst))
        except SkipFile:
            pass
        except (Cancelled, FsError):
            raise
        except OSError as exc:
            op.errors.append({"path": src, "error": exc.strerror or str(exc), "dest": os.path.dirname(dst),
                              "retry": True})

    @staticmethod
    def _dest_check(op: Operation, src: str, dest: str = "") -> None:
        if os.path.isdir(src) and fsops.is_inside(os.path.realpath(dest or op.dest), os.path.realpath(src)):
            raise FsError("into_self", src)

    # ---- kinds
    def _do_copy(self, op: Operation) -> None:
        jobs = self._jobs(op)
        for _src, dest in jobs:
            if not os.path.isdir(dest):
                raise FsError("no_destination", dest)
        self._scan(op, [src for src, _ in jobs])
        suffix = op.args.get("copy_suffix") or " - Copy"
        for src, dest in jobs:
            op.check()
            self._dest_check(op, src, dest)
            name = os.path.basename(src.rstrip("\\/"))
            dst = os.path.join(dest, name)
            if fsops.norm(os.path.dirname(src)) == fsops.norm(dest):
                base, ext = fsops.split_name(name) if not os.path.isdir(src) else (name, "")
                dst = os.path.join(dest, fsops.unique_name(dest, f"{base}{suffix}{ext}"))
            target = self._target_for(op, src, dst)
            if target is None:
                op.done_items += 1
                continue
            created: List[Tuple[str, ...]] = []
            try:
                self._copy_tree(op, src, target, created)
            finally:
                op.undo.extend(created)              # a cancelled copy can still be undone (what got copied)
            if os.path.lexists(target):
                op.created_paths.append(target)

    def _move_one(self, op: Operation, src: str, dst: str) -> None:
        """Move *src* to the free (or resolved) path *dst*; same volume → rename, else copy + delete."""
        op.check()
        op.current = src
        if os.path.isdir(dst) and os.path.isdir(src) and not os.path.islink(src):
            # merge into an existing folder, child by child
            with os.scandir(src) as handle:
                children = list(handle)
            for child in children:
                target = self._target_for(op, child.path, os.path.join(dst, child.name))
                if target is None:
                    op.done_items += 1
                    continue
                self._move_one(op, child.path, target)
            try:
                os.rmdir(src)
                op.undo.append(("mkdir", src))
            except OSError:
                pass
            return
        replacing = os.path.lexists(dst)
        try:
            if replacing and os.name == "nt":
                os.replace(src, dst)
            else:
                os.rename(src, dst)
            size = op.args.get("_sizes", {}).pop(src, 0)
            if size > 0:
                op.add_bytes(size)                       # counted as a cross-volume copy, turned out to be a rename
            op.done_items += 1
            op.undo.append(("move_back", dst, src))
            self._tags_moved(src, dst)
            return
        except OSError as exc:
            cross = exc.errno == errno.EXDEV or getattr(exc, "winerror", 0) == 17
            if not cross:
                raise
        if not any(src == top or fsops.is_inside(src, top) for top in op.args.get("_cross", ())):
            # predicted a rename (same st_dev) but the OS said "another volume" (junctions, mounted folders)
            size, count = self._tree_size(src)
            op.total_bytes += size
            op.total_items += count - 1
        created: List[Tuple[str, ...]] = []
        mark = len(op.errors), len(op.skipped)
        try:
            self._copy_tree(op, src, dst, created)
            op.check()
        except Cancelled:
            # the source is untouched: remove the half copy, or the user finds duplicates of half the files
            for step in reversed(created):
                if step[0] == "trash_created" and os.path.lexists(step[1]):
                    self._remove_tree(step[1])
            raise
        if len(op.errors) > mark[0] or len(op.skipped) > mark[1]:
            if not created:
                op.undo.append(("move_back", dst, src))
            else:
                op.undo.extend(created)
            return                                   # never delete a source that was not fully copied
        self._remove_tree(src)
        op.undo.append(("move_back", dst, src))
        self._tags_moved(src, dst)

    @staticmethod
    def _tags_moved(src: str, dst: str) -> None:
        try:
            from . import settings
            settings.tags_moved(src, dst)
        except Exception:  # noqa: BLE001
            pass

    def _do_move(self, op: Operation) -> None:
        jobs = self._jobs(op)
        for _src, dest in jobs:
            if not os.path.isdir(dest):
                raise FsError("no_destination", dest)
        # same volume = rename (instant, counted as items); other volume = copy + delete (counted in bytes)
        renames = {src for src, dest in jobs if self._same_volume(src, dest)}
        sizes: Dict[str, int] = op.args.setdefault("_sizes", {})
        op.args["_cross"] = [src for src, _ in jobs if src not in renames]
        total_b = total_n = 0
        for src, _dest in jobs:
            op.check()
            if src in renames:
                total_n += 1
                continue
            size, count = self._tree_size(src)
            sizes[src] = size
            total_b, total_n = total_b + size, total_n + count
            op.total_bytes, op.total_items = total_b, total_n
            self._notify(op)
        op.status = "running"
        self._notify(op, force=True)
        for src, dest in jobs:
            op.check()
            fsops.ensure_mutable(src)
            self._dest_check(op, src, dest)
            name = os.path.basename(src.rstrip("\\/"))
            if fsops.norm(os.path.dirname(src)) == fsops.norm(dest):
                op.done_items += 1
                continue
            target = self._target_for(op, src, os.path.join(dest, name))
            if target is None:
                op.done_items += 1
                continue
            try:
                self._move_one(op, src, target)
                if os.path.lexists(target):
                    op.created_paths.append(target)
            except (Cancelled, FsError):
                raise
            except OSError as exc:
                op.errors.append({"path": src, "error": exc.strerror or str(exc), "dest": dest, "retry": True})

    def _do_trash(self, op: Operation) -> None:
        """Recycle Bin, item by item (honest progress); on Windows in small batches — one SHFileOperation per
        file made 1 000 files take minutes. A failed batch is retried one by one to tell which item failed."""
        op.total_items = len(op.sources)
        op.status = "running"
        batch = 24 if trash.IS_WIN else 1
        pending = list(op.sources)
        split: set = set()                       # items of a failed batch: some of them may already be in the bin
        while pending:
            op.check()
            group, pending = pending[:batch], pending[batch:]
            ready = []
            for src in group:
                try:
                    fsops.ensure_mutable(src)
                    if not os.path.lexists(src) and src in split:
                        op.undo.append(("restore", src, ""))
                        op.done_items += 1
                        continue
                    if not os.path.lexists(src):
                        raise FileNotFoundError(src)
                    ready.append(src)
                except FsError as exc:
                    op.errors.append({"path": src, "error": exc.code})
                except OSError as exc:
                    op.errors.append({"path": src, "error": exc.strerror or str(exc), "retry": False})
            if not ready:
                continue
            op.current = ready[0]
            try:
                results = trash.send(ready)
                for src, result in zip(ready, results):
                    op.undo.append(("restore", src, result.get("token", "")))
                op.done_items += len(ready)
            except (OSError, trash.TrashError) as exc:
                if len(ready) > 1:
                    pending = ready + pending
                    split.update(ready)
                    batch = 1
                    continue
                op.errors.append({"path": ready[0], "error": getattr(exc, "strerror", None) or str(exc), "retry": True})
            self._notify(op)

    def _remove_tree(self, path: str, op: Optional[Operation] = None) -> None:
        def onerror(func, target, _exc):
            try:
                os.chmod(target, statmod.S_IWRITE | statmod.S_IREAD | statmod.S_IEXEC)
                func(target)
            except OSError as exc:
                if op is not None:
                    op.errors.append({"path": target, "error": exc.strerror or str(exc), "retry": True})
        if os.path.isdir(path) and not os.path.islink(path):
            if op is None:
                shutil.rmtree(path, onerror=onerror)
                return
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    op.check()
                    full = os.path.join(root, name)
                    op.current = full
                    try:
                        os.unlink(full)
                    except OSError:
                        onerror(os.unlink, full, None)
                    op.done_items += 1
                    self._notify(op)
                for name in dirs:
                    full = os.path.join(root, name)
                    try:
                        if os.path.islink(full):
                            os.unlink(full)
                        else:
                            os.rmdir(full)
                    except OSError:
                        onerror(os.rmdir, full, None)
                    op.done_items += 1
            try:
                os.rmdir(path)
            except OSError:
                onerror(os.rmdir, path, None)
        else:
            try:
                os.unlink(path)
            except OSError:
                onerror(os.unlink, path, None)

    def _do_delete(self, op: Operation) -> None:
        for src in op.sources:
            fsops.ensure_mutable(src)
        self._scan(op, op.sources)
        for src in op.sources:
            op.check()
            op.current = src
            self._remove_tree(src, op)
            if not os.path.isdir(src):
                op.done_items += 1

    def _do_rename(self, op: Operation) -> None:
        """Batch rename: ``args["pairs"] = [[path, new_name], ...]`` — two-phase, so a→b, b→a works."""
        pairs = [(str(a), str(b)) for a, b in op.args.get("pairs") or []]
        op.total_items = len(pairs)
        op.status = "running"
        staged: List[Tuple[str, str, str]] = []
        targets = set()
        for path, name in pairs:
            error = fsops.valid_name(name)
            if error:
                raise FsError(error, name)
            fsops.ensure_mutable(path)
            target = os.path.join(os.path.dirname(path), name)
            key = fsops.norm(target)
            if key in targets:
                raise FsError("duplicate_target", target)
            targets.add(key)
        sources = {fsops.norm(p) for p, _ in pairs}
        for path, name in pairs:
            target = os.path.join(os.path.dirname(path), name)
            if os.path.lexists(target) and fsops.norm(target) not in sources:
                raise FsError("exists", target)
        try:
            for path, name in pairs:                # phase 1: temp names
                temp = os.path.join(os.path.dirname(path), f".~mfren-{uuid.uuid4().hex[:8]}")
                os.rename(path, temp)
                staged.append((path, temp, os.path.join(os.path.dirname(path), name)))
        except OSError:
            for path, temp, _target in reversed(staged):   # roll back: nothing is renamed half-way
                try:
                    os.rename(temp, path)
                except OSError:
                    pass
            raise
        for path, temp, target in staged:           # phase 2: final names
            try:
                os.rename(temp, target)
                op.undo.append(("move_back", target, path))
                op.created_paths.append(target)
                self._tags_moved(path, target)
                op.done_items += 1
            except OSError as exc:
                os.rename(temp, path)
                op.errors.append({"path": path, "error": exc.strerror or str(exc)})

    def _do_organize(self, op: Operation) -> None:
        """r40-files-max: AI «sort by meaning» — ``args["moves"] = [[src, dest_folder, new_name], ...]`` as ONE
        operation: folders are created as needed, each file moves under its new name (never over an existing file);
        undo moves every file back and removes the folders this operation created (if they are empty again)."""
        moves = [(str(a), str(b), str(c)) for a, b, c in op.args.get("moves") or []]
        op.total_items = len(moves)
        op.status = "running"
        for src, folder, name in moves:
            op.check()
            op.current = src
            try:
                if fsops.valid_name(name):
                    raise FsError(fsops.valid_name(name), name)
                fsops.ensure_mutable(src)
                missing = []
                probe = folder
                while probe and not os.path.isdir(probe):
                    missing.append(probe)
                    parent = os.path.dirname(probe)
                    if parent == probe:
                        break
                    probe = parent
                os.makedirs(folder, exist_ok=True)
                for created in reversed(missing):
                    op.undo.append(("rmdir_empty", created))
                    op.created_paths.append(created)
                target = os.path.join(folder, name)
                if os.path.lexists(target) and fsops.norm(target) != fsops.norm(src):
                    target = os.path.join(folder, fsops.unique_name(folder, name))
                self._move_one(op, src, target)
                op.created_paths.append(target)
            except (Cancelled, FsError) as exc:
                if isinstance(exc, Cancelled):
                    raise
                op.errors.append({"path": src, "error": exc.code})
            except OSError as exc:
                op.errors.append({"path": src, "error": exc.strerror or str(exc)})

    def _do_write(self, op: Operation) -> None:
        from . import textedit
        from ..memunote.formats.textio import atomic_write

        edits = op.args.get("edits") or []
        scope = op.args.get("scope") or ""
        op.total_items = len(edits)
        op.status = "running"
        for item in edits:
            op.check()
            path = item["path"]
            op.current = path
            try:
                if not scope or not fsops.is_inside(os.path.realpath(path), os.path.realpath(scope)):
                    raise FsError("outside_scope", path)
                if fsops.protected_reason(path):
                    raise FsError("protected", path)
                exists = os.path.lexists(path)
                raw, _text, _info = textedit.snapshot(path)
                expected = item.get("expected")
                if (expected is None and exists) or (expected is not None and (not exists or textedit.digest(raw) != expected)):
                    raise FsError("file_changed", path)
                data = textedit.encoded(item)
                atomic_write(path, data)
                op.undo.append(("write_back", path, raw if exists else None, textedit.digest(data)))
                op.created_paths.append(path)
                op.done_items += 1
            except (OSError, FsError) as exc:
                op.errors.append({"path": path, "error": getattr(exc, "code", str(exc))})

    def _do_mkdir(self, op: Operation) -> None:
        for path in op.sources:
            op.check()
            self._scope_check(op, [path])
            os.makedirs(path, exist_ok=True)
            op.undo.append(("trash_created", path))
            op.created_paths.append(path)
            op.done_items += 1

    # ---- archives
    @staticmethod
    def seven_zip() -> str:
        for name in ("7z", "7za", "7zz", "7z.exe"):
            found = shutil.which(name)
            if found:
                return found
        if os.name == "nt":
            for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                         os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
                candidate = os.path.join(base, "7-Zip", "7z.exe")
                if os.path.isfile(candidate):
                    return candidate
        return ""

    def _do_extract(self, op: Operation) -> None:
        if op.args.get("members") or op.args.get("strip") or op.args.get("password") or archive_mod.kind_of(op.sources[0]) == "single":
            return archive_mod.extract(self, op)   # r41-files: chosen members, a browsed sub-folder, a password, .gz/.xz
        archive = op.sources[0]
        dest = op.dest
        lower = archive.lower()
        if not os.path.isdir(os.path.dirname(dest) or dest):
            raise FsError("no_destination", dest)
        fresh = not os.path.exists(dest)
        os.makedirs(dest, exist_ok=True)
        if fresh:
            op.undo.append(("trash_created", dest))
        op.created_paths.append(dest)
        base = os.path.realpath(dest)

        def safe_target(name: str) -> Optional[str]:
            name = name.replace("\\", "/").lstrip("/")
            if not name or any(part == ".." for part in name.split("/")) or ":" in name.split("/")[0]:
                return None
            target = os.path.realpath(os.path.join(dest, *name.split("/")))
            return target if fsops.is_inside(target, base) else None

        if lower.endswith(".zip") or zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                members = zf.infolist()
                op.total_items = len(members)
                op.total_bytes = sum(m.file_size for m in members)
                op.status = "running"
                for member in members:
                    op.check()
                    name = member.filename
                    try:
                        name = name.encode("cp437").decode("utf-8") if not (member.flag_bits & 0x800) else name
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        try:
                            name = member.filename.encode("cp437").decode("cp866")
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            name = member.filename
                    target = safe_target(name)
                    if target is None:
                        op.errors.append({"path": member.filename, "error": "unsafe_path"})
                        continue
                    op.current = name
                    if member.is_dir():
                        os.makedirs(target, exist_ok=True)
                        op.done_items += 1
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    resolved = self._target_for(op, archive + "/" + name, target) if not fresh else target
                    if resolved is None:
                        op.done_items += 1
                        continue
                    existed = os.path.lexists(resolved)
                    try:
                        with zf.open(member) as reader:
                            self._write_stream(op, reader, resolved, existed, member.file_size)
                    except SkipFile:
                        continue
                    try:
                        mtime = time.mktime(member.date_time + (0, 0, -1))
                        os.utime(resolved, (mtime, mtime))
                    except (OverflowError, ValueError, OSError):
                        pass
                    if not fresh and not existed:
                        op.undo.append(("trash_created", resolved))
                    op.done_items += 1
            return
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                members = tf.getmembers()
                op.total_items = len(members)
                op.total_bytes = sum(m.size for m in members if m.isfile())
                op.status = "running"
                for member in members:
                    op.check()
                    target = safe_target(member.name)
                    if target is None or member.issym() or member.islnk() or member.isdev():
                        op.errors.append({"path": member.name, "error": "unsafe_path"})
                        continue
                    op.current = member.name
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                    elif member.isfile():
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        resolved = self._target_for(op, archive + "/" + member.name, target) if not fresh else target
                        if resolved is None:
                            op.done_items += 1
                            continue
                        reader = tf.extractfile(member)
                        if reader is None:
                            continue
                        try:
                            with reader:
                                self._write_stream(op, reader, resolved, os.path.lexists(resolved), member.size)
                        except SkipFile:
                            continue
                        try:
                            os.utime(resolved, (member.mtime, member.mtime))
                        except OSError:
                            pass
                    op.done_items += 1
            return
        tool = self.seven_zip()
        if not tool:
            raise FsError("no_7zip", archive)
        op.status = "running"
        op.total_items = 1
        proc = subprocess.Popen([tool, "x", "-y", "-bso0", "-bsp0", f"-o{dest}", archive],   # noqa: S603
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=0x08000000 if os.name == "nt" else 0)
        while proc.poll() is None:
            if op._cancel.is_set():
                proc.kill()
                raise Cancelled()
            time.sleep(0.2)
        if proc.returncode not in (0, 1):
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")[:300]
            raise FsError("extract_failed", err or archive)
        op.done_items = 1

    def _do_arcadd(self, op: Operation) -> None:   # r41-files: files dropped / pasted into an archive opened as a folder
        archive_mod.add(self, op)

    def _write_stream(self, op: Operation, reader: Any, target: str, existed: bool, size: int = -1) -> int:
        """Write an archive member to *target*; on cancel / error the half-written file is removed (a replaced
        file is written to a temp name first, so the old copy survives a cancel)."""
        temp = target + f".~mfpart{os.getpid()}" if existed else target
        written = 0
        size = size if size >= 0 else 0
        op.start_file(op.current or target, size)
        try:
            with open(temp, "wb") as writer:
                while True:
                    op.check_file()
                    block = reader.read(CHUNK)
                    if not block:
                        break
                    writer.write(block)
                    written += len(block)
                    op.add_bytes(len(block))
                    self._notify(op)
            if existed:
                self._replace(temp, target)
        except SkipFile:
            self._unlink_quiet(temp)
            op.skip_current(size, written)
            raise
        except BaseException:
            op.done_bytes -= written
            self._unlink_quiet(temp)
            raise
        return written

    def _do_compress(self, op: Operation) -> None:
        if op.args.get("format", "zip") != "zip" or op.args.get("level") or op.args.get("password"):
            return archive_mod.compress(self, op)   # r41-files: 7z / tar.*, compression level, password
        dest = op.dest
        folder = os.path.dirname(dest)
        if os.path.lexists(dest):
            dest = os.path.join(folder, fsops.unique_name(folder, os.path.basename(dest)))
            op.dest = dest
        self._scan(op, op.sources)
        parent = os.path.commonpath([os.path.dirname(s.rstrip("\\/")) for s in op.sources]) if op.sources else folder
        partial = dest + ".partial"
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                                 allowZip64=True) as zf:
                for src in op.sources:
                    items: List[Tuple[str, str]] = []
                    if os.path.isdir(src):
                        for root, dirs, files in os.walk(src):
                            rel_root = os.path.relpath(root, parent)
                            if not files and not dirs:
                                zf.writestr(rel_root.replace(os.sep, "/") + "/", b"")
                            for name in files:
                                items.append((os.path.join(root, name), os.path.join(rel_root, name)))
                    else:
                        items.append((src, os.path.relpath(src, parent)))
                    for full, rel in items:
                        op.check()
                        op.current = full
                        if fsops.norm(full) == fsops.norm(partial):
                            continue
                        try:
                            op.start_file(full, os.path.getsize(full))
                            # strict_timestamps=False: a file dated before 1980 (copied from old media, touched by
                            # tools) is stored as 1980-01-01 instead of failing the whole archive with ValueError
                            info = zipfile.ZipInfo.from_file(full, rel.replace(os.sep, "/"), strict_timestamps=False)
                            info.compress_type = zipfile.ZIP_DEFLATED
                            # Zip64 only when the size needs it (zipfile decides from file_size): forced Zip64 headers
                            # on every small file break some older unzip tools and cost 20 bytes per entry
                            with open(full, "rb") as reader, zf.open(info, "w") as writer:
                                while True:
                                    op.check()
                                    block = reader.read(CHUNK)
                                    if not block:
                                        break
                                    writer.write(block)
                                    op.add_bytes(len(block))
                                    self._notify(op)
                            op.done_items += 1
                        except OSError as exc:
                            op.errors.append({"path": full, "error": exc.strerror or str(exc)})
                            op.done_items += 1
            os.replace(partial, dest)
        except BaseException:
            try:
                os.unlink(partial)
            except OSError:
                pass
            raise
        op.undo.append(("trash_created", dest))
        op.created_paths.append(dest)

    # ---- uploads: files the page has only as File objects (no path) — streamed in, one request per file
    @staticmethod
    def _upload_target(dest: str, rel: str) -> Optional[str]:
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or fsops.valid_name(p) for p in parts):
            return None
        target = os.path.join(dest, *parts)
        return target if fsops.is_inside(target, dest) else None

    def _do_upload(self, op: Operation) -> None:
        """The worker side of an upload: lays out folders, then waits while the page streams each file in
        (:meth:`upload_put` runs on the request thread and does the writing under this operation's controls)."""
        dest = op.dest
        if not os.path.isdir(dest):
            raise FsError("no_destination", dest)
        items = op.args.get("items") or []
        state = op.upload = {"items": [{"state": "pending"} for _ in items], "cv": threading.Condition(),
                             "seen": time.monotonic(), "tops": {}}
        op.total_items = len(items)
        op.total_bytes = sum(int(it.get("size") or 0) for it in items if not it.get("dir"))
        op.status = "running"
        self._notify(op, force=True)
        for index, item in enumerate(items):
            target = self._upload_target(dest, item.get("rel", ""))
            if target is None:
                op.errors.append({"path": str(item.get("rel", "")), "error": "unsafe_path"})
                state["items"][index]["state"] = "error"
                op.done_items += 1
                continue
            self._upload_top(op, target)
            if item.get("dir"):
                os.makedirs(target, exist_ok=True)
                state["items"][index]["state"] = "done"
                op.done_items += 1
        with state["cv"]:
            while any(it["state"] in ("pending", "writing") for it in state["items"]):
                if op._cancel.is_set() and not any(it["state"] == "writing" for it in state["items"]):
                    raise Cancelled()
                if op.status in ("paused", "conflict"):
                    state["seen"] = time.monotonic()
                elif time.monotonic() - state["seen"] > UPLOAD_STALL:
                    raise FsError("upload_stalled", dest)
                state["cv"].wait(0.5)
        if op._cancel.is_set():
            raise Cancelled()

    def _upload_top(self, op: Operation, target: str) -> None:
        """Remember the top-level item of an upload (selected afterwards; undone = sent to the Recycle Bin)."""
        rel = os.path.relpath(target, op.dest)
        top = os.path.join(op.dest, rel.split(os.sep)[0])
        tops = op.upload["tops"]
        if top not in tops:
            tops[top] = not os.path.lexists(top)
            if tops[top]:
                op.undo.append(("trash_created", top))
            op.created_paths.append(top)

    def _upload_item(self, op_id: str, index: int) -> Tuple[Operation, Dict[str, Any], Dict[str, Any]]:
        op = self.get(op_id)
        deadline = time.monotonic() + 10
        while op.kind == "upload" and op.upload is None and op.status in ("queued", "scanning") \
                and time.monotonic() < deadline:
            time.sleep(0.02)                         # the worker thread is just starting
        if op.kind != "upload" or op.upload is None:
            raise FsError("not_ready", op_id)
        items = op.args.get("items") or []
        if not 0 <= index < len(items):
            raise FsError("bad_index", str(index))
        return op, items[index], op.upload["items"][index]

    def upload_prepare(self, op_id: str, index: int) -> Dict[str, Any]:
        """Before the page sends file *index*: resolve a name conflict (may wait for the user's answer)."""
        op, item, slot = self._upload_item(op_id, index)
        op.upload["seen"] = time.monotonic()
        if slot["state"] != "pending":
            return {"state": slot["state"]}
        if op._cancel.is_set():
            return {"state": "cancelled"}
        target = self._upload_target(op.dest, item.get("rel", ""))
        if target is None:
            return {"state": "error"}
        os.makedirs(os.path.dirname(target), exist_ok=True)
        op.current = item.get("rel", "")
        info = {"name": os.path.basename(target), "size": int(item.get("size") or 0),
                "mtime": int(item.get("mtime") or 0) or None, "path": item.get("rel", "")}
        try:
            resolved = self._target_for(op, "upload:" + item.get("rel", ""), target, info)
        except Cancelled:
            self._upload_finish(op, slot, "cancelled")
            return {"state": "cancelled"}
        if resolved is None:
            op.total_bytes = max(op.done_bytes, op.total_bytes - info["size"])
            op.skipped.append(op.current)
            op.done_items += 1
            self._upload_finish(op, slot, "skipped")
            return {"state": "skipped"}
        slot["target"] = resolved
        return {"state": "ready"}

    def upload_put(self, op_id: str, index: int, reader: Any, length: int) -> Dict[str, Any]:
        """Write the body of file *index* (exactly *length* bytes from *reader*) under pause / cancel / skip."""
        op, item, slot = self._upload_item(op_id, index)
        if slot["state"] == "pending" and "target" not in slot:
            ready = self.upload_prepare(op_id, index)
            if ready["state"] != "ready":
                return ready
        if slot["state"] != "pending":
            return {"state": slot["state"]}
        target = slot["target"]
        slot["state"] = "writing"
        op.upload["seen"] = time.monotonic()
        op.current = item.get("rel", "")
        left = [max(0, int(length))]

        class _Body:
            def read(self, size: int) -> bytes:
                if left[0] <= 0:
                    return b""
                block = reader.read(min(size, left[0], 1024 * 1024))
                if not block:
                    raise ConnectionError("upload_incomplete")
                left[0] -= len(block)
                op.upload["seen"] = time.monotonic()
                return block
        existed = os.path.lexists(target)
        try:
            self._write_stream(op, _Body(), target, existed, int(item.get("size") or length))
            mtime = int(item.get("mtime") or 0) / 1000.0
            if mtime > 0:
                try:
                    os.utime(target, (mtime, mtime))
                except OSError:
                    pass
            if not existed and not op.upload["tops"].get(os.path.join(op.dest, os.path.relpath(
                    target, op.dest).split(os.sep)[0])):
                op.undo.append(("trash_created", target))
            op.done_items += 1
            self._upload_finish(op, slot, "done")
            return {"state": "done", "path": target}
        except SkipFile:
            self._upload_finish(op, slot, "skipped")
            return {"state": "skipped"}
        except Cancelled:
            self._upload_finish(op, slot, "cancelled")
            return {"state": "cancelled"}
        except (OSError, ConnectionError) as exc:
            op.errors.append({"path": item.get("rel", ""), "error": getattr(exc, "strerror", None) or str(exc)})
            op.done_items += 1
            self._upload_finish(op, slot, "error")
            return {"state": "error", "error": str(exc)[:200]}

    def upload_fail(self, op_id: str, index: int, error: str) -> None:
        """The page could not read file *index* (a virtual file the source app no longer serves, …)."""
        op, item, slot = self._upload_item(op_id, index)
        if slot["state"] in ("pending",):
            op.errors.append({"path": item.get("rel", ""), "error": (error or "read_failed")[:200]})
            op.total_bytes = max(op.done_bytes, op.total_bytes - int(item.get("size") or 0))
            op.done_items += 1
            self._upload_finish(op, slot, "error")

    def _upload_finish(self, op: Operation, slot: Dict[str, Any], state: str) -> None:
        slot["state"] = state
        op.cur_size = op.cur_done = 0
        with op.upload["cv"]:
            op.upload["cv"].notify_all()
        self._notify(op, force=True)

    # ---- vpaste: files copied out of a zip folder in Explorer / Outlook attachments (clipboard, no paths)
    def _do_vpaste(self, op: Operation) -> None:
        from . import clip
        if not os.path.isdir(op.dest):
            raise FsError("no_destination", op.dest)
        source = op.args.get("_source")          # tests hand in a fake iterator; normally the Windows clipboard
        iterator = source if source is not None else clip.iter_virtual()
        op.status = "running"
        op.total_items = int(op.args.get("count") or 0)
        tops: Dict[str, bool] = {}
        presized = False
        self._notify(op, force=True)
        for item, reader in iterator:
            op.check()
            if item.get("summary"):
                op.total_items, op.total_bytes, presized = int(item["count"]), int(item["bytes"]), True
                self._notify(op, force=True)
                continue
            target = self._upload_target(op.dest, item["rel"])
            if target is None:
                op.errors.append({"path": item["rel"], "error": "unsafe_path"})
                op.done_items += 1
                continue
            top = os.path.join(op.dest, os.path.relpath(target, op.dest).split(os.sep)[0])
            if top not in tops:
                tops[top] = not os.path.lexists(top)
                if tops[top]:
                    op.undo.append(("trash_created", top))
                op.created_paths.append(top)
            if reader is None:
                os.makedirs(target, exist_ok=True)
                op.done_items += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            known = int(item.get("size", -1)) >= 0
            size = max(0, int(item.get("size") or 0))
            if not presized:
                op.total_bytes += size               # no summary: sizes arrive item by item
            op.current = item["rel"]
            resolved = self._target_for(op, "clipboard:" + item["rel"], target,
                                        {"name": os.path.basename(target), "size": size if known else None,
                                         "mtime": int(item.get("mtime", 0) * 1000) or None, "path": item["rel"]})
            if resolved is None:
                op.total_bytes -= size
                op.skipped.append(item["rel"])
                op.done_items += 1
                continue
            existed = os.path.lexists(resolved)
            try:
                written = self._write_stream(op, reader, resolved, existed, size)
            except SkipFile:
                continue
            except OSError as exc:
                op.total_bytes -= size
                op.errors.append({"path": item["rel"], "error": exc.strerror or str(exc)})
                op.done_items += 1
                continue
            op.total_bytes += written - size
            if item.get("mtime"):
                try:
                    os.utime(resolved, (item["mtime"], item["mtime"]))
                except OSError:
                    pass
            if not existed and not tops.get(top):
                op.undo.append(("trash_created", resolved))
            op.done_items += 1
        op.total_items = max(op.total_items, op.done_items)

    # ---- download: a link dropped from a browser
    def _do_download(self, op: Operation) -> None:
        import urllib.parse
        import urllib.request
        url = str(op.args.get("url") or "")
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise FsError("bad_url", url[:200])
        if not os.path.isdir(op.dest):
            raise FsError("no_destination", op.dest)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MemuFiles"})
        with urllib.request.urlopen(request, timeout=30) as resp:    # noqa: S310 - http(s) only, checked above
            name = _download_name(url, resp.headers)
            op.total_bytes = int(resp.headers.get("Content-Length") or 0)
            op.total_items = 1
            op.status = "running"
            target = self._target_for(op, url, os.path.join(op.dest, name),
                                      {"name": name, "size": op.total_bytes, "path": url})
            if target is None:
                op.done_items = 1
                return
            existed = os.path.lexists(target)
            op.current = name
            try:
                written = self._write_stream(op, resp, target, existed, op.total_bytes)
            except SkipFile:
                return
            if not op.total_bytes:
                op.total_bytes = written
        if not existed:
            op.undo.append(("trash_created", target))
        op.created_paths.append(target)
        op.done_items = 1

    # ---- undo
    def _do_undo(self, op: Operation) -> None:
        steps = op.args.get("steps") or []
        op.total_items = len(steps)
        op.status = "running"
        for step in steps:
            op.check()
            kind = step[0]
            try:
                if kind == "trash_created":
                    if os.path.lexists(step[1]):
                        trash.send([step[1]])
                elif kind == "move_back":
                    now, original = step[1], step[2]
                    if os.path.lexists(now):
                        os.makedirs(os.path.dirname(original), exist_ok=True)
                        if os.path.lexists(original):
                            original = os.path.join(os.path.dirname(original),
                                                    fsops.unique_name(os.path.dirname(original),
                                                                      os.path.basename(original)))
                        try:
                            os.rename(now, original)
                        except OSError:
                            shutil.move(now, original)
                        self._tags_moved(now, original)
                elif kind == "restore":
                    trash.restore_original(step[1], step[2] if len(step) > 2 else "")
                elif kind == "write_back":
                    from .textedit import digest, snapshot
                    from ..memunote.formats.textio import atomic_write
                    raw, _text, _info = snapshot(step[1])
                    if not os.path.isfile(step[1]) or digest(raw) != step[3]:
                        raise FsError("file_changed", step[1])
                    if step[2] is None:
                        trash.send([step[1]])
                    else:
                        atomic_write(step[1], step[2])
                elif kind == "mkdir":
                    os.makedirs(step[1], exist_ok=True)
                elif kind == "rmdir_empty":           # r40-files-max: a folder «organize» created
                    try:
                        os.rmdir(step[1])
                    except OSError:
                        pass
                op.done_items += 1
                op.created_paths.append(step[2] if kind == "move_back" else step[1])
            except Exception as exc:  # noqa: BLE001
                op.errors.append({"path": str(step[1] if len(step) > 1 else ""), "error": str(exc)[:200]})


def _download_name(url: str, headers: Any) -> str:
    """A safe file name for a download: Content-Disposition, else the URL's last segment (+ an extension from the
    content type when it has none)."""
    import mimetypes
    import urllib.parse
    name = ""
    disposition = headers.get("Content-Disposition") or ""
    match = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", disposition, re.I)
    if match:
        name = urllib.parse.unquote(match.group(1).strip().strip('"'))
    else:
        match = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
        if match:
            name = match.group(1).strip()
    if not name:
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", name).strip(" .")[:180] or "download"
    if "." not in name:
        ext = mimetypes.guess_extension((headers.get("Content-Type") or "").split(";")[0].strip()) or ""
        name += {".jpe": ".jpg", ".jpeg": ".jpg"}.get(ext, ext)
    return name
