"""Background terminal tasks and a session scrollback for MemuCode.

A task is a supervised process in the session sandbox. Output is drained on
a side thread so a full pipe cannot stall the child. Stop kills the job.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .code_sandbox import (forbidden_reason, kill_tree, launch_captured, manager as sandbox_manager, read_text,
                           spawn)
from .work_tools import WorkError, resolve_inside

MAX_TASKS = 24
MAX_OUTPUT = 40_000
MAX_ACTIVE = 4
MAX_SCROLLBACK = 80_000


def _argv(command: str, shell: str) -> List[str]:
    if sys.platform != "win32":
        return ["/bin/sh", "-c", command]
    if str(shell or "").lower().startswith("power"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["cmd", "/c", command]


class _Pending:
    """Процесс-заглушка на время запуска: для подсчёта «ещё работает»."""

    def poll(self):
        return None

    def kill(self):
        return None


class TaskManager:
    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._terminals: Dict[str, Dict[str, Any]] = {}

    def submit(self, session_id: str, root: Path, command: str, argv: List[str],
               env: Dict[str, str], timeout: int = 600, label: str = "") -> Dict[str, Any]:
        blocked = forbidden_reason(command)
        if blocked:
            raise WorkError("Команда запрещена песочницей MemuCode")
        session_key = str(session_id)[:64]
        with self._lock:
            # Предел — на сессию (чат), а место резервируется под замком:
            # одновременные запуски больше не проскакивают мимо проверки.
            active = [row for row in self._tasks.values()
                      if row["session"] == session_key and (row.get("starting") or row["process"].poll() is None)]
            if len(active) >= MAX_ACTIVE:
                raise WorkError("Уже выполняется слишком много фоновых задач (максимум 4)")
            if len(self._tasks) >= MAX_TASKS:
                self._prune_locked()
            task_id = uuid.uuid4().hex[:12]
            placeholder = {"id": task_id, "session": session_key, "starting": True,
                           "process": _Pending(), "command": str(command)[:500]}
            self._tasks[task_id] = placeholder
        try:
            process = spawn(session_id, root, argv, cwd=str(root), env=env)
        except BaseException:
            with self._lock:
                self._tasks.pop(task_id, None)
            raise
        row = {
            "id": task_id, "session": str(session_id)[:64], "command": str(command)[:500],
            "label": str(label or "")[:120], "started": time.time(),
            "timeout": max(1, min(600, int(timeout))), "process": process,
            "stdout": "", "stderr": "", "exit_code": None, "root": str(root),
        }
        with self._lock:
            self._tasks[task_id] = row
        self._pump(row, process.stdout, "stdout")
        self._pump(row, process.stderr, "stderr")
        threading.Thread(target=self._watch, args=(row,), daemon=True).start()
        return self._public(row)

    def _pump(self, row: Dict[str, Any], pipe, key: str) -> None:
        def keep(text: str) -> None:
            with self._lock:
                row[key] = (row.get(key, "") + text)[-MAX_OUTPUT:]

        def read() -> None:
            if pipe is None:
                return
            try:
                read_text(pipe, keep, 4096)
            except Exception:
                return
        threading.Thread(target=read, daemon=True).start()

    def _watch(self, row: Dict[str, Any]) -> None:
        process = row["process"]
        deadline = row["started"] + row["timeout"]
        while process.poll() is None:
            if time.time() > deadline:
                kill_tree(process)
                row["timed_out"] = True
                break
            time.sleep(0.2)
        try:
            code = process.wait(timeout=5) if process.poll() is None else process.poll()
        except subprocess.TimeoutExpired:
            code = -1
        with self._lock:
            row["exit_code"] = code
            row["finished"] = time.time()
        job = sandbox_manager.job(row["session"], row["root"], create=False)
        if job is not None:
            job.forget(process)

    def _prune_locked(self) -> None:
        finished = [task_id for task_id, row in self._tasks.items()
                    if row["process"].poll() is not None]
        for task_id in finished[:max(1, len(self._tasks) - MAX_TASKS // 2)]:
            self._tasks.pop(task_id, None)

    def status(self, task_id: str, session_id: str = "") -> Dict[str, Any]:
        with self._lock:
            row = self._tasks.get(str(task_id))
            # Чужая сессия видит «нет такой задачи», а не её вывод.
            if row is None or row.get("starting") or (session_id and row["session"] != str(session_id)[:64]):
                raise KeyError("task not found")
            return self._public(row)

    def cancel(self, task_id: str, session_id: str = "") -> Dict[str, Any]:
        with self._lock:
            row = self._tasks.get(str(task_id))
        if row is None or row.get("starting") or (session_id and row["session"] != str(session_id)[:64]):
            raise KeyError("task not found")
        if row["process"].poll() is None:
            kill_tree(row["process"])
        row["cancelled"] = True
        return self.status(task_id, session_id)

    def list(self, session_id: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            return [self._public(row) for row in self._tasks.values()
                    if not row.get("starting") and (not session_id or row["session"] == str(session_id)[:64])]

    def close(self, session_id: str) -> None:
        session_id = str(session_id)[:64]
        with self._lock:
            rows = [row for row in self._tasks.values() if row["session"] == session_id]
        for row in rows:
            if row["process"].poll() is None:
                try:
                    row["process"].kill()
                except OSError:
                    pass
        sandbox_manager.close(session_id)

    def terminal(self, session_id: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
        session = str(session_id or "default")[:64]
        action = str(args.get("action") or "read").lower()
        with self._lock:
            book = self._terminals.setdefault(session, {"scrollback": "", "running": False, "root": str(root)})
        if action == "start":
            sandbox_manager.job(session, root)
            return {"ok": True, "session": session, "scrollback": book["scrollback"][-8000:]}
        if action in ("read", "status"):
            return {"session": session, "running": bool(book.get("running")),
                    "scrollback": book.get("scrollback", "")[-8000:]}
        if action == "stop":
            # Останавливаем только команду терминала: фоновые задачи сессии
            # (dev-сервер из code_task) продолжают работать.
            process = book.get("process")
            if process is not None:
                kill_tree(process)
            book["running"] = False
            book["scrollback"] = (book.get("scrollback", "") + "\n[stopped]\n")[-MAX_SCROLLBACK:]
            return {"ok": True, "stopped": True, "scrollback": book["scrollback"][-8000:]}
        if action != "run":
            raise WorkError(f"Unknown terminal action: {action}")
        command = str(args.get("command") or "").strip()
        if not command:
            raise WorkError("command is required")
        if forbidden_reason(command):
            raise WorkError("Команда запрещена песочницей MemuCode")
        cwd = resolve_inside(root, str(args.get("cwd") or ""))
        if not cwd.is_dir():
            raise WorkError("Каталог терминала не найден внутри проекта")
        book["running"] = True
        book["scrollback"] = (book.get("scrollback", "") + f"\n$ {command}\n")[-MAX_SCROLLBACK:]
        try:
            captured = launch_captured(
                session, root, _argv(command, str(args.get("shell") or "")),
                cwd=str(cwd), timeout=int(args.get("timeout") or 120),
                on_start=lambda process: book.__setitem__("process", process),
            )
        except TimeoutError as exc:
            book["scrollback"] = (book.get("scrollback", "") + f"{exc}\n")[-MAX_SCROLLBACK:]
            book["running"] = False
            raise WorkError(str(exc)) from exc
        finally:
            book["running"] = False
            book.pop("process", None)
        text = (captured.get("stdout") or "") + (captured.get("stderr") or "")
        book["scrollback"] = (book.get("scrollback", "") + text + f"\n[exit {captured.get('exit_code')}]\n")[-MAX_SCROLLBACK:]
        return {"command": command, "exit_code": captured.get("exit_code"),
                "stdout": captured.get("stdout") or "", "stderr": captured.get("stderr") or "",
                "scrollback": book["scrollback"][-8000:]}

    def _public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["id"], "session": row["session"], "command": row["command"],
            "label": row["label"], "started": row["started"],
            "finished": row.get("finished"), "exit_code": row.get("exit_code"),
            "running": row["process"].poll() is None,
            "stdout": row.get("stdout", "")[-8000:], "stderr": row.get("stderr", "")[-8000:],
            "cancelled": bool(row.get("cancelled")), "timed_out": bool(row.get("timed_out")),
        }


manager = TaskManager()
