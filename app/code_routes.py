"""Request-shaped helpers for the MemuCode HTTP API.

The FastAPI routes stay thin. These functions are synchronous where the work
is filesystem-bound, and the one async helper schedules read-only calls together.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import code_artifacts, code_context, code_sandbox, code_sessions, code_skills, code_tools
from .work_tools import WorkError, read_text, resolve_inside, tool_search


def _session(session_id: str) -> str:
    return str(session_id or "").strip() or "default"


def tree(root: Path, session_id: str) -> Dict[str, Any]:
    engine = code_context.engine_for(_session(session_id), root)
    stats = engine.refresh()
    git: Dict[str, str] = {}
    branch = ""
    try:
        status = code_tools.tool_git(root, {"action": "status"})
        branch = str(status.get("branch") or "")
        for change in status.get("changes") or []:
            git[str(change.get("path") or "")] = str(change.get("state") or "")
    except WorkError:
        git = {}
    return {"tree": engine.tree, "git": git, "branch": branch, **stats}


def read_file(root: Path, rel: str, start_line: int = 1, end_line: int = 0) -> Dict[str, Any]:
    path = resolve_inside(root, rel)
    if path.is_dir():
        raise WorkError("Это каталог, а не файл")
    text, truncated_file = read_text(path, limit=200_000)
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    end = int(end_line or 0)
    end = min(end if end > 0 else min(len(lines), start + 399), len(lines))
    if start > len(lines) and lines:
        # Раньше листание за конец файла молча возвращало к строке 1.
        return {"path": path.relative_to(root).as_posix(), "text": "", "start_line": start,
                "end_line": 0, "total_lines": len(lines), "truncated": truncated_file,
                "past_end": True}
    chunk = lines[start - 1:end] if lines else []
    return {
        "path": path.relative_to(root).as_posix(),
        "text": "\n".join(chunk),
        "start_line": start,
        "end_line": start + len(chunk) - 1 if chunk else 0,
        "total_lines": len(lines),
        "truncated": truncated_file or end < len(lines),
    }


#: The built-in editor is for quick fixes; bigger files open in a real editor.
EDIT_LIMIT = 1_500_000
_EDIT_ENCODINGS = ("utf-8", "cp1251")


def _decode_for_edit(raw: bytes) -> Tuple[str, str]:
    """Text and the encoding to write it back with; BOMs are kept."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    for encoding in _EDIT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise WorkError("Кодировку файла не удалось определить — откройте его во внешнем редакторе")


def edit_source(root: Path, rel: str) -> Dict[str, Any]:
    """The whole file for the built-in editor, with what is needed to save it safely.

    Lines always arrive with ``\\n``; the original line ending, encoding and a
    hash of the bytes on disk go along so a save can restore the format and
    refuse to overwrite a file somebody (usually the agent) changed meanwhile.
    """
    path = resolve_inside(root, rel)
    if not path.is_file():
        raise WorkError("Файл не найден")
    raw = path.read_bytes()
    if len(raw) > EDIT_LIMIT:
        raise WorkError("Файл больше 1,5 МБ — для такого откройте внешний редактор")
    if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise WorkError("Это двоичный файл, его нельзя править как текст")
    text, encoding = _decode_for_edit(raw)
    crlf = text.count("\r\n")
    eol = "\r\n" if crlf and crlf >= text.count("\n") / 2 else "\n"
    return {
        "path": path.relative_to(root).as_posix(),
        "text": text.replace("\r\n", "\n"),
        "encoding": encoding,
        "eol": "crlf" if eol == "\r\n" else "lf",
        "hash": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }


def save_source(root: Path, rel: str, text: str, *, expected_hash: str, eol: str = "lf",
                encoding: str = "utf-8", session_id: str = "", force: bool = False) -> Dict[str, Any]:
    """Write a hand edit made in the built-in editor.

    The file must still be the one the editor loaded (same hash) unless
    ``force`` is set: otherwise the agent's newer change would be lost
    silently. The previous content is captured as a checkpoint first, so the
    edit shows up in the Undo tab like any other change.
    """
    from . import code_checkpoints

    path = resolve_inside(root, rel)
    if path == root or path.is_dir():
        raise WorkError("Это каталог, а не файл")
    if not isinstance(text, str):
        raise WorkError("Текст файла должен быть строкой")
    current = path.read_bytes() if path.is_file() else b""
    current_hash = hashlib.sha256(current).hexdigest()
    if expected_hash and current_hash != expected_hash and not force:
        return {"ok": False, "conflict": True, "hash": current_hash,
                "error": "Файл изменился на диске после открытия в редакторе"}
    if encoding not in ("utf-8", "utf-8-sig", "utf-16", "cp1251"):
        encoding = "utf-8"
    body = text.replace("\r\n", "\n")
    if eol == "crlf":
        body = body.replace("\n", "\r\n")
    try:
        data = body.encode(encoding)
    except UnicodeEncodeError:
        # A character cp1251 cannot hold: upgrade the file to UTF-8 rather than lose it.
        data = body.encode("utf-8")
        encoding = "utf-8"
    rel_path = path.relative_to(root).as_posix()
    if session_id:
        try:
            code_checkpoints.require_snapshot(session_id, root, rel_path, label="manual_edit")
        except ValueError as exc:
            raise WorkError(str(exc)) from exc
    handle, temp = tempfile.mkstemp(prefix=".memucode-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        if path.exists():
            shutil.copymode(path, temp)  # mkstemp создаёт файл с правами 0600
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return {"ok": True, "path": rel_path, "hash": hashlib.sha256(data).hexdigest(),
            "size": len(data), "encoding": encoding}


def search(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return tool_search(root, args or {})


def diff(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    path = str((args or {}).get("path") or "")
    staged = bool((args or {}).get("staged"))
    stat = code_tools.tool_git(root, {"action": "diff", "path": path, "staged": staged, "stat": True})
    full = code_tools.tool_git(root, {"action": "diff", "path": path, "staged": staged})
    return {"stat": stat.get("diff") or "", "diff": full.get("diff") or "",
            "repository": full.get("repository", True)}


def skills(root: Path, *, include_prompt: bool = False) -> Dict[str, Any]:
    return {"skills": code_skills.catalogue(root, include_prompt=include_prompt)}


def context_stats(root: Path, session_id: str) -> Dict[str, Any]:
    engine = code_context.engine_for(_session(session_id), root)
    if not engine.tree:
        engine.refresh()
    return engine.stats()


def sandbox_status(session_id: str) -> Dict[str, Any]:
    status = code_sandbox.manager.status(_session(session_id))
    status["tasks"] = code_sessions.manager.list(_session(session_id))
    return status


def stop_sandbox(session_id: str) -> Dict[str, Any]:
    code_sessions.manager.close(_session(session_id))
    return {"ok": True, "stopped": True}


def _guard_mutation(name: str, args: Dict[str, Any], access: str) -> str:
    action = str(args.get("action") or "").lower()
    if name in ("code_artifact", "code_task") and action in ("create", "update", "publish", "delete", "submit"):
        if access != "full":
            return "Изменение артефактов и фоновых задач требует полного доступа"
    # code_terminal_run сам подставляет action=run: смотреть только на поле
    # action нельзя — без него команда уходила в терминал и в режиме Ask.
    terminal_action = {"code_terminal_run": "run", "code_terminal_start": "start",
                       "code_terminal_stop": "stop"}.get(name, action or "read")
    if name.startswith("code_terminal") and terminal_action == "run":
        if access != "full" and not code_tools.is_read_only(str(args.get("command") or "")):
            return "Команда терминала требует полного доступа"
    if name == "code_run" and not code_tools.is_read_only(str(args.get("command") or "")) and access != "full":
        return "Запуск команд требует режима полного доступа"
    if name == "code_diagnostics" and args.get("execute") and access != "full":
        args["allow_write"] = False
    elif name == "code_diagnostics" and access == "full":
        args["allow_write"] = True
    return ""


def _one_sync(root: Path, name: str, args: Dict[str, Any], session: str, access: str) -> Dict[str, Any]:
    denied = _guard_mutation(name, args, access)
    if denied:
        return {"tool": name, "error": denied}
    try:
        if name == "code_run":
            command = str(args.get("command") or "")
            if code_sandbox.forbidden_reason(command):
                return {"tool": name, "error": "Команда запрещена песочницей MemuCode"}
            value = code_tools.run_command(
                root, command, shell=str(args.get("shell") or ""),
                timeout=int(args.get("timeout") or 120), cwd=str(args.get("cwd") or ""),
                session_id=session)
            value["tool"] = name
            return value
        if name in ("code_artifact", "code_task", "code_read_cached", "code_outline_deep",
                    "code_context", "code_symbols", "code_skill", "code_terminal",
                    "code_terminal_start", "code_terminal_run", "code_terminal_stop"):
            return code_tools.run_session_tool(name, root, args, session)
        if name == "code_diagnostics":
            args = {**args, "session": session, "allow_write": access == "full" and bool(args.get("execute"))}
            return code_tools.run(name, root, args)
        if name.startswith("code_"):
            return code_tools.run(name, root, args)
        return {"tool": name, "error": "Пакетный исполнитель принимает только инструменты code_"}
    except (WorkError, KeyError, ValueError) as exc:
        return {"tool": name, "error": str(exc)}
    except Exception as exc:
        return {"tool": name, "error": str(exc)}


async def run_batch(root: Path, calls: List[Any], session_id: str, access: str,
                    project_id: str = "") -> Dict[str, Any]:
    """Up to four read-only code tools at once; writes stay in order."""
    session = _session(session_id)
    normalized: List[tuple[str, Dict[str, Any]]] = []
    for row in list(calls or [])[:12]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("tool") or row.get("name") or "").strip().lower()
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        if name:
            args = dict(args)
            if project_id and not args.get("project_id"):
                # Без project_id выключенный для проекта навык всё равно грузился.
                args["project_id"] = project_id
            normalized.append((name, args))
    results: List[Dict[str, Any]] = []
    index = 0
    while index < len(normalized):
        name, args = normalized[index]
        if code_tools.parallel_safe(name, args):
            group = []
            while index < len(normalized) and len(group) < 4 and code_tools.parallel_safe(*normalized[index]):
                group.append(normalized[index])
                index += 1
            chunk = await asyncio.gather(*[
                asyncio.to_thread(_one_sync, root, item_name, item_args, session, access)
                for item_name, item_args in group
            ])
            results.extend(chunk)
        else:
            results.append(await asyncio.to_thread(_one_sync, root, name, args, session, access))
            index += 1
    return {"results": results, "count": len(results)}
