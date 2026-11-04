# -*- coding: utf-8 -*-
"""MemuUtils operations: ``call(op, args)`` — the module bridge ``Memu.utils.call`` (action ``utils_call``, permission
``utils``) and ``POST /api/pg/utils/call {op, args, module_id?, lang?}`` → ``{result}``.

Operations: ``catalog``, ``run``, ``help``, ``settings.get`` / ``settings.set``, ``skill.preview``,
``upload.start`` / ``upload.chunk`` / ``upload.done`` (big files in parts), ``out.get`` (a data URL for preview),
``out.save`` (into Downloads/MemuAI/MemuUtils), ``out.reveal`` (show in the file manager), ``run.get`` (a stored run: the
chat card «Open MemuUtils»), ``jobs.list`` / ``jobs.stop``,
``pick.folder`` (the native folder dialog of the MemuAI window).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Request

from . import (catalog, jobs, load_settings, output_data, reveal, run_info, runs_dir, run_async, save_folder, save_output,
               save_settings, skill_stats, skill_text, tool_help, upload_chunk, upload_done, upload_start)
from .core import REGISTRY, UtilError

OPS = ("catalog", "run", "help", "settings.get", "settings.set", "skill.preview", "upload.start", "upload.chunk", "upload.done",
       "out.get", "out.save", "out.reveal", "run.get", "jobs.list", "jobs.stop", "pick.folder")

def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False

# a third-party module with the ``utils`` permission gets the safe part only: no system / network tools, nothing
# dangerous, no AI-access settings, saving only into the MemuUtils folder or a folder the person picked in a dialog
UNTRUSTED_CATS = ("system", "net")
_PICKED: set = set()

# runs started by each third-party module: such a module reads and saves only its own results
_OWNED: Dict[str, set] = {}
_OWNED_MAX = 400

def _own(owner: str, result: Any) -> None:
    run = result.get("run") if isinstance(result, dict) else ""
    if owner and run:
        runs = _OWNED.setdefault(owner, set())
        if len(runs) >= _OWNED_MAX:
            runs.clear()
        runs.add(str(run))

def _check_owner(op: str, args: Dict[str, Any], owner: str) -> None:
    own = _OWNED.get(owner, ())
    runs = [str(args.get("run") or "")] if op in ("out.get", "out.save", "run.get") else []
    if op == "run":
        entries = args.get("files")
        for entry in (entries if isinstance(entries, list) else [entries] if entries else []):
            ref = str((entry.get("ref") if isinstance(entry, dict) else entry if isinstance(entry, str) else "") or "")
            if ref.startswith("out:"):
                runs.append(ref[4:].partition("/")[0])
    if any(run not in own for run in runs):
        raise UtilError("Модуль видит только результаты своих запусков", "A module sees only the results of its own runs")

def _check_untrusted(op: str, args: Dict[str, Any]) -> None:
    if op in ("settings.set", "jobs.stop"):
        raise UtilError("Настройки и задачи MemuUtils меняются только в окне MemuUtils",
                        "MemuUtils settings and jobs change only in the MemuUtils window")
    if op == "run":
        from . import clean_opts
        util = REGISTRY.get(str(args.get("util") or ""))
        if util is not None and (util.cat in UNTRUSTED_CATS or util.pick or util.is_danger(clean_opts(util, args.get("opts")))):
            raise UtilError("Эта утилита доступна только в окне MemuUtils", "This utility is available only in the MemuUtils window")

def _picked(folder: str) -> bool:
    try:
        target = Path(folder).resolve()
    except OSError:
        return False
    return str(target) in _PICKED or _inside(save_folder(), target)

async def call_async(op: str, args: Optional[Dict[str, Any]] = None, *, lang: str = "ru", window: Any = None,
                     trusted: bool = True, owner: str = "") -> Any:
    args = args if isinstance(args, dict) else {}
    if not trusted:
        _check_untrusted(op, args)
        _check_owner(op, args, owner or "?")
        result = await _call(op, args, lang=lang, window=window)
        if op == "run":
            _own(owner or "?", result)
        return result
    return await _call(op, args, lang=lang, window=window)

async def _call(op: str, args: Dict[str, Any], *, lang: str, window: Any) -> Any:
    en = lang == "en"
    if op == "catalog":
        return await asyncio.to_thread(catalog, lang)
    if op == "run":
        return await run_async(str(args.get("util") or ""), text=str(args.get("text") or ""), text2=str(args.get("text2") or ""),
                               files=args.get("files"), opts=args.get("opts"), lang=lang, source="window",
                               remember=not args.get("live"))
    if op == "help":
        return {"text": tool_help(str(args.get("util") or ""), lang)}
    if op == "settings.get":
        return load_settings()
    if op == "settings.set":
        patch = args.get("patch") if isinstance(args.get("patch"), dict) else args
        saved = await asyncio.to_thread(save_settings, patch)
        return {"settings": saved, "skill": await asyncio.to_thread(skill_stats)}
    if op == "skill.preview":
        return {"text": skill_text("en"), **skill_stats()}
    if op == "upload.start":
        return await asyncio.to_thread(upload_start, str(args.get("name") or "file"), int(args.get("size") or 0))
    if op == "upload.chunk":
        return await asyncio.to_thread(upload_chunk, str(args.get("upload") or ""), int(args.get("offset") or 0), str(args.get("data") or ""))
    if op == "upload.done":
        return await asyncio.to_thread(upload_done, str(args.get("upload") or ""))
    if op == "out.get":
        return await asyncio.to_thread(output_data, str(args.get("run") or ""), str(args.get("name") or ""))
    if op == "out.save":
        folder = str(args.get("folder") or "")
        if folder and not _picked(folder):
            raise UtilError("Сохранять можно в папку MemuUtils или в папку, выбранную кнопкой «Папка…»",
                            "Save into the MemuUtils folder or a folder chosen with “Folder…”")
        return await asyncio.to_thread(save_output, str(args.get("run") or ""), str(args.get("name") or ""), folder)
    if op == "out.reveal":
        path = Path(str(args.get("path") or ""))
        if not str(args.get("path") or "") or not (_inside(save_folder(), path) or _inside(runs_dir(), path)):
            raise UtilError("Можно показать только файлы MemuUtils", "Only MemuUtils files can be shown")
        return {"ok": await asyncio.to_thread(reveal, str(path))}
    if op == "run.get":
        return await asyncio.to_thread(run_info, str(args.get("run") or ""), lang)
    if op == "jobs.list":
        return {"jobs": jobs.listing(en)}
    if op == "jobs.stop":
        if args.get("all"):
            stopped = jobs.stop()
        elif args.get("id") or args.get("kind"):
            stopped = jobs.stop(str(args.get("id") or ""), str(args.get("kind") or ""))
        else:
            stopped = 0
        return {"stopped": stopped, "jobs": jobs.listing(en)}
    if op == "pick.folder":
        try:
            import webview as _wv  # type: ignore
        except ImportError:
            _wv = None
        if window is None or _wv is None:
            raise UtilError("Выбор папки работает в окне MemuAI — впишите путь вручную", "Folder picking works in the MemuAI window — type the path instead")
        picked = await asyncio.to_thread(window.create_file_dialog, _wv.FOLDER_DIALOG)
        if not picked:
            return {"path": ""}
        path = str(picked[0] if isinstance(picked, (list, tuple)) else picked)
        try:
            _PICKED.add(str(Path(path).resolve()))
        except OSError:
            pass
        return {"path": path}
    raise UtilError(f"Неизвестная операция «{op}»", f"Unknown operation “{op}”")

_ALLOWED: Dict[tuple, tuple] = {}
_ALLOWED_TTL = 5.0

def module_access(module_id: str, action: str = "utils_call") -> tuple:
    """``(allowed, builtin)``: the module must declare the ``utils`` permission (answer cached for a few seconds)."""
    from ..modules_host import ACTION_PERMISSION, default_root, load_package
    root = default_root()
    key = (str(root), module_id, action)
    hit = _ALLOWED.get(key)
    if hit and time.monotonic() - hit[1] < _ALLOWED_TTL:
        return hit[0]
    try:
        package = load_package(root, module_id)
        answer = (ACTION_PERMISSION.get(action, "utils") in (package.get("permissions") or []), bool(package.get("builtin")))
    except (KeyError, ValueError):
        answer = (False, False)
    _ALLOWED[key] = (answer, time.monotonic())
    return answer

def module_allowed(module_id: str, action: str = "utils_call") -> bool:
    return module_access(module_id, action)[0]

def error_payload(exc: Exception, lang: str = "ru") -> tuple:
    if isinstance(exc, UtilError):
        return {"error": exc.text(lang)}, 400
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return {"error": str(exc)[:400] or type(exc).__name__}, 400
    return {"error": f"{exc}"[:600] or type(exc).__name__}, 500

def register(app, *, access: Optional[Callable[[str, str], tuple]] = module_access) -> None:
    from fastapi.responses import JSONResponse

    @app.post("/api/pg/utils/call")
    async def utils_call(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Некорректный JSON / Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Ожидается JSON-объект / A JSON object is expected"}, 400)
        module_id = str(body.get("module_id") or "")
        trusted = True
        if module_id and access is not None:
            ok, builtin = access(module_id, "utils_call")
            if not ok:
                return JSONResponse({"error": "Модулю не выдано право utils / The module lacks the utils permission"}, 403)
            trusted = builtin
        lang = "en" if str(body.get("lang") or "") == "en" else "ru"
        op = str(body.get("op") or "")
        if op not in OPS:
            return JSONResponse({"error": f"Неизвестная операция / Unknown operation: {op[:40]}"}, 400)
        try:
            result = await call_async(op, body.get("args") or {}, lang=lang, window=getattr(app.state, "window", None),
                                      trusted=trusted, owner=module_id)
        except Exception as exc:  # noqa: BLE001 - the window shows the error text
            payload, code = error_payload(exc, lang)
            return JSONResponse(payload, code)
        return JSONResponse({"result": result})

__all__ = ["OPS", "UNTRUSTED_CATS", "call_async", "module_access", "module_allowed", "register", "REGISTRY"]
