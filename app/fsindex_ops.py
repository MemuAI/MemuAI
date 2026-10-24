"""Round 36: file operations on PC-search results (the right-click menu of «Поиск файлов на ПК», quick/plugins/fsearch.js).

A thin layer over the MemuFiles back end (``app.memufiles.fsops`` / ``clip`` / ``opqueue`` / ``trash``), imported —
never copied — so the search menu does exactly what MemuFiles does: rename with the same name rules, duplicate as
«name - копия.ext», ZIP next to the item, Recycle Bin with undo, CF_HDROP copy/cut that Explorer pastes.

Safety (the page is local, but a local page must not turn this into «do anything with any path»):
* every path must be one the search showed in this process (``fsindex_api._was_shown``) and absolute, without NUL;
* rename / trash / anything that creates an item go through ``fsops.ensure_mutable`` (drive roots, Windows,
  Program Files, the profile and its special folders, the app's data are refused with code ``protected``);
* no command line is ever built from user text: opening uses ``os.startfile`` / ``xdg-open`` with the path as one
  argument, «Open with…» the system dialog (``OpenAs_RunDLL``), «Properties» ``ShellExecuteEx`` verb.

Route: ``POST /api/fsindex/op {action, path, name?, op?, lang}`` and ``GET /api/fsindex/op/{id}`` (a long ZIP /
duplicate still running after ``WAIT`` seconds — the page polls it). Actions: ``properties``, ``open_with``,
``shell_properties``, ``clip_copy``, ``clip_cut``, ``rename``, ``duplicate``, ``compress``, ``trash``, ``undo``,
``attach`` (lets an @-mention chip of the Quick Access read the file).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import fsindex

log = logging.getLogger("memuai.fsindex.ops")

WAIT = 12.0               # a synchronous answer for anything that finishes this fast (almost everything)
ACTIONS = ("properties", "open_with", "shell_properties", "clip_copy", "clip_cut", "rename", "duplicate",
           "compress", "trash", "undo", "attach")
_QUEUE: List[Any] = []
_QLOCK = threading.Lock()


def _mf():
    """The MemuFiles modules (lazy: nothing of MemuFiles loads before the first menu action)."""
    from .memufiles import fsops, clip, opqueue    # noqa: WPS433
    return fsops, clip, opqueue


def queue():
    with _QLOCK:
        if not _QUEUE:
            _QUEUE.append(_mf()[2].OpQueue())
        return _QUEUE[0]


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


MESSAGES = {
    "protected": ("Это системная или защищённая папка — её нельзя менять отсюда.",
                  "This is a system or protected item — it can't be changed from here."),
    "exists": ("Объект с таким именем уже есть.", "An item with this name already exists."),
    "empty_name": ("Введите имя.", "Type a name."),
    "name_too_long": ("Слишком длинное имя.", "The name is too long."),
    "invalid_chars": ("Имя не может содержать символы \\ / : * ? \" < > |", "A name can't contain \\ / : * ? \" < > |"),
    "trailing_dot": ("Имя не может заканчиваться точкой или пробелом.", "A name can't end with a dot or a space."),
    "reserved_name": ("Это имя зарезервировано Windows.", "Windows reserves this name."),
    "unsupported": ("Доступно только в Windows.", "Available on Windows only."),
    "gone": ("Файл уже удалён или перемещён.", "The file was deleted or moved."),
    "not_shown": ("Этот путь не из результатов поиска.", "This path is not a search result."),
    "nothing_to_undo": ("Отменять нечего.", "Nothing to undo."),
    "busy": ("Операция ещё выполняется.", "The operation is still running."),
    "failed": ("Не получилось.", "It didn't work."),
}


def message(code: str, lang: str, detail: str = "") -> str:
    ru, en = MESSAGES.get(code, MESSAGES["failed"])
    text = tr(lang, ru, en)
    return f"{text} {detail}".strip() if code == "failed" and detail else text


class OpError(Exception):
    def __init__(self, code: str, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


def _row(path: str) -> Dict[str, Any]:
    """The same row shape as a search result (``fsindex._result``), for the page to insert / replace in place."""
    from .fsindex_query import kind_of
    st = os.stat(path, follow_symlinks=False)
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    name = os.path.basename(path.rstrip("\\/")) or path
    return {"path": path, "name": name, "dir": os.path.dirname(path), "size": 0 if is_dir else st.st_size,
            "mtime": st.st_mtime, "is_dir": is_dir, "kind": "folder" if is_dir else kind_of(name)}


def _index_events(events: List[tuple]) -> None:
    """Our own change goes into MemuAI's index at once (live watching may be off or slow); Everything sees it itself."""
    try:
        svc = fsindex.service()
        if svc.index is not None:
            svc.apply_events(events)
    except Exception as exc:  # noqa: BLE001
        log.debug("fsindex ops: index update failed: %s", exc)


def _remember(paths: List[str]) -> None:
    from . import fsindex_api
    fsindex_api._remember([{"path": p} for p in paths if p])


def _checked(path: str) -> str:
    from . import fsindex_api
    fsops = _mf()[0]
    if not path or not fsindex_api._was_shown(path):
        raise OpError("not_shown")
    try:
        clean = fsops.clean_path(path)
    except fsops.FsError:
        raise OpError("not_shown")
    if not os.path.lexists(clean):
        raise OpError("gone", 404)
    return clean


def _wait(op: Any, timeout: float = WAIT) -> Dict[str, Any]:
    end = time.monotonic() + timeout
    while op.status not in ("done", "error", "cancelled") and time.monotonic() < end:
        time.sleep(0.03)
    return _op_reply(op)


def _op_reply(op: Any) -> Dict[str, Any]:
    snap = op.snapshot()
    reply: Dict[str, Any] = {"op": op.id, "status": op.status, "undoable": snap.get("undoable", False),
                             "errors": snap.get("errors", [])[-5:], "done_items": op.done_items,
                             "total_items": op.total_items, "done_bytes": op.done_bytes, "total_bytes": op.total_bytes}
    if op.status == "done":
        rows = []
        for path in op.created_paths:
            if os.path.lexists(path):
                rows.append(_row(path))
        if rows:
            _remember([r["path"] for r in rows])
            if op.kind != "undo":
                _index_events([("created", r["path"], "") for r in rows])
        reply["rows"] = rows
    return reply


def _folder_stats(path: str, budget: float = 1.5, cap: int = 200000) -> Dict[str, Any]:
    """Size / file / folder count of a folder, bounded in time (a «Properties» look, not a disk scan)."""
    total = files = dirs = 0
    end = time.monotonic() + budget
    stack = [path]
    complete = True
    while stack:
        if time.monotonic() > end or files + dirs > cap:
            complete = False
            break
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(entry.path)
                        else:
                            files += 1
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return {"size": total, "files": files, "dirs": dirs, "complete": complete}


def run(action: str, path: str, body: Dict[str, Any], lang: str) -> Dict[str, Any]:
    fsops, clip, _opq = _mf()
    if action not in ACTIONS:
        raise OpError("failed", 400, "bad action")
    if action == "undo":
        try:
            op = queue().undo(str(body.get("op") or ""))
        except fsops.FsError as exc:
            raise OpError(exc.code if exc.code in MESSAGES else "failed")
        reply = _wait(op)
        restored = [r["path"] for r in reply.get("rows", [])]
        _index_events([("created", p, "") for p in restored] +
                      [("deleted", s[1], "") for s in op.args.get("steps", []) if s and s[0] == "trash_created"])
        return reply
    src = _checked(path)
    if action == "properties":
        info = fsops.properties(src)
        if info.get("is_dir"):
            info["folder"] = _folder_stats(src)
        info["windows"] = fsops.IS_WIN
        return {"ok": True, "info": info}
    if action == "attach":
        try:
            from . import mentions
            remember = getattr(mentions, "_remember_path", None)
            if callable(remember):
                remember(src)
        except Exception as exc:  # noqa: BLE001
            log.debug("fsindex ops: mention remember failed: %s", exc)
        return {"ok": True, "path": src}
    if action == "open_with":
        if not fsops.IS_WIN:
            raise OpError("unsupported")
        fsops.open_with_dialog(src)
        return {"ok": True}
    if action == "shell_properties":
        if not fsops.IS_WIN:
            raise OpError("unsupported")
        fsops.shell_verb(src, "properties")
        return {"ok": True}
    if action in ("clip_copy", "clip_cut"):
        ok = bool(clip.write_files([src], cut=action == "clip_cut"))
        if not ok:
            raise OpError("unsupported")
        return {"ok": True}
    try:
        if action == "rename":
            new_name = str(body.get("name") or "").strip()
            target = fsops.rename(src, new_name)
            _remember([target])
            _index_events([("renamed", src, target)])
            return {"ok": True, "old": src, "row": _row(target)}
        if action == "trash":
            fsops.ensure_mutable(src)
            op = queue().submit("trash", [src], label=os.path.basename(src))
            reply = _wait(op)
            if op.status == "done" and not os.path.lexists(src):
                _index_events([("deleted", src, "")])
                reply["removed"] = [src]
            elif op.errors:
                code = op.errors[-1].get("error") or "failed"
                raise OpError(code if code in MESSAGES else "failed", 400, "" if code in MESSAGES else code)
            return reply
        parent = os.path.dirname(src.rstrip("\\/"))
        name = os.path.basename(src.rstrip("\\/"))
        if action == "duplicate":
            suffix = tr(lang, " - копия", " - Copy")
            base, ext = fsops.split_name(name) if not os.path.isdir(src) else (name, "")
            fsops.ensure_mutable(os.path.join(parent, f"{base}{suffix}{ext}"))
            op = queue().submit("copy", [src], parent, copy_suffix=suffix, label=name)
            return _wait(op)
        if action == "compress":
            base = fsops.split_name(name)[0] if not os.path.isdir(src) else name
            dest = os.path.join(parent, (base or name) + ".zip")
            fsops.ensure_mutable(dest)
            op = queue().submit("compress", [src], dest, label=name)
            return _wait(op)
    except fsops.FsError as exc:
        raise OpError(exc.code if exc.code in MESSAGES else "failed", 400, "" if exc.code in MESSAGES else exc.code)
    except PermissionError:
        raise OpError("failed", 403, tr(lang, "Нет доступа.", "Access denied."))
    except OSError as exc:
        raise OpError("failed", 400, exc.strerror or str(exc))
    raise OpError("failed")


router = APIRouter()


@router.post("/api/fsindex/op")
async def api_op(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    lang = "en" if body.get("lang") == "en" else "ru"
    action = str(body.get("action") or "")
    try:
        reply = await asyncio.to_thread(run, action, str(body.get("path") or ""), body, lang)
    except OpError as exc:
        return JSONResponse({"error": message(exc.code, lang, exc.detail), "code": exc.code}, exc.status)
    if reply.get("status") == "error":
        detail = "; ".join(str(e.get("error") or "") for e in reply.get("errors", []))[:200]
        code = (reply.get("errors") or [{}])[-1].get("error") or "failed"
        return JSONResponse({"error": message(code if code in MESSAGES else "failed", lang, detail), "code": code,
                             **reply}, 400)
    return {"ok": True, **reply}


@router.get("/api/fsindex/op/{op_id}")
async def api_op_status(op_id: str):
    if not _QUEUE:
        return JSONResponse({"error": "no operation"}, 404)
    op = _QUEUE[0].ops.get(op_id)
    if op is None:
        return JSONResponse({"error": "no operation"}, 404)
    return await asyncio.to_thread(_op_reply, op)
