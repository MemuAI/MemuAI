"""Round 29: MemuCode operations of the Mini App beyond watching (``app/tgapp_api.py`` registers them).

* ``code.open`` / ``code.new`` — open a session on the phone: the live terminal, or a phone-hosted MemuCode
  (``memucode remote-host``) so slash commands, skills, the model picker and permission questions work;
* ``code.close`` — end a phone-hosted session (never a terminal on the computer);
* ``code.commands`` — the slash-command catalogue for auto-complete (+ the models the CLI can use);
* ``code.complete`` — r30: suggestions for the command, its arguments or an ``@file`` being typed;
* ``code.projects`` / ``code.browse`` / ``code.mkdir`` — choose the folder of a new session;
* ``code.files`` / ``code.file`` / ``code.changes`` / ``code.diff`` — read-only file browser of the session's
  project folder (paths are confined to it) and ``git status``;
* ``code.rename`` / ``code.delete`` — manage sessions like ``/rename`` and the dashboard do.

Everything requires the Mini App's «MemuCode control» switch (``tgapp.json: code_control``).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from . import tgapp_code as tcode


def register(op: Callable[[str], Callable], ApiError: Any, require_code: Callable[[Dict[str, Any]], None],
             t: Callable[[Dict[str, Any], str, str], str], lang_of: Callable[[Dict[str, Any]], str]) -> None:

    def _root(ctx: Dict[str, Any], body: Dict[str, Any]) -> str:
        root = tcode.root_of(str(body.get("session_id") or ""), str(body.get("rc") or ""))
        if not root:
            raise ApiError(404, "not_found", "Папка проекта не найдена", "The project folder is missing")
        return root

    def _host_error(ctx: Dict[str, Any], exc: Exception) -> Any:
        prefs = ctx["prefs"]
        if isinstance(exc, LookupError):
            folder = "folder" in str(exc)
            return ApiError(404, "not_found", "Папка проекта не найдена" if folder else "Сессия не найдена",
                            "The project folder is missing" if folder else "Session not found")
        if isinstance(exc, FileNotFoundError):
            return ApiError(400, "no_cli", t(prefs, "MemuCode CLI не установлен", "MemuCode CLI is not installed"))
        if isinstance(exc, TimeoutError):
            return ApiError(504, "host_timeout", t(prefs, "MemuCode не запустился вовремя — попробуйте ещё раз",
                                                   "MemuCode did not start in time — try again"))
        if isinstance(exc, RuntimeError) and str(exc) == "busy":
            return ApiError(409, "busy", t(prefs, "Эта сессия уже выполняется", "This session is already running"))
        if isinstance(exc, RuntimeError) and str(exc).startswith("host_failed:"):
            detail = str(exc).split(":", 1)[1].strip()
            return ApiError(502, "host_failed", t(prefs, "MemuCode не запустился: ", "MemuCode failed to start: ") + detail)
        return ApiError(500, "internal", str(exc)[:300])

    @op("code.open")
    async def code_open(ctx, body):
        require_code(ctx)
        try:
            return await asyncio.to_thread(
                tcode.start_host, ctx["user"], session_id=str(body.get("session_id") or ""),
                model=str(body.get("model") or ""), mode=str(body.get("mode") or ""), lang=lang_of(ctx["prefs"]))
        except Exception as exc:
            raise _host_error(ctx, exc) from None

    @op("code.new")
    async def code_new(ctx, body):
        require_code(ctx)
        mode = str(body.get("mode") or "")
        if mode == "bypassPermissions" and body.get("confirm") is not True:
            raise ApiError(409, "confirm", t(ctx["prefs"], "Режим без разрешений нужно подтвердить",
                                             "Bypassing permissions needs confirmation"))
        try:
            return await asyncio.to_thread(
                tcode.start_host, ctx["user"], root=str(body.get("root") or ""), model=str(body.get("model") or ""),
                mode=mode, lang=lang_of(ctx["prefs"]))
        except Exception as exc:
            raise _host_error(ctx, exc) from None

    @op("code.close")
    async def code_close(ctx, body):
        require_code(ctx)
        try:
            await asyncio.to_thread(tcode.send_to_terminal, str(body.get("rc") or ""), {"type": "close"})
        except LookupError:
            return {"ok": True, "gone": True}
        except ValueError:
            raise ApiError(400, "not_host", "Закрыть можно только сессию, запущенную с телефона",
                           "Only a session started from the phone can be closed") from None
        return {"ok": True}

    @op("code.commands")
    async def code_commands(ctx, body):
        require_code(ctx)
        return await asyncio.to_thread(tcode.command_catalog, str(body.get("rc") or ""))

    @op("code.complete")
    async def code_complete(ctx, body):
        """r30: suggestions for the command / argument / @file being typed (``tgapp_complete.py``)."""
        require_code(ctx)
        from . import tgapp_chat, tgapp_complete
        rc, session_id = str(body.get("rc") or ""), str(body.get("session_id") or "")

        def run() -> Dict[str, Any]:
            root = tcode.root_of(session_id, rc) if (rc or session_id) else ""   # files only of a real session
            rows = tgapp_chat.chat_models(ctx["prefs"])
            catalog = tcode.command_catalog(rc)
            live = tcode.live_state(rc) if rc else None
            if live and live.get("model"):
                catalog["model"] = str(live["model"])          # «current» next to the model the session uses now
            return tgapp_complete.complete(str(body.get("text") or ""), root=root, catalog=catalog,
                                           models=[row["id"] for row in rows],
                                           model_labels={row["id"]: row.get("provider", "") for row in rows},
                                           lang=lang_of(ctx["prefs"]))
        return await asyncio.to_thread(run)

    @op("code.projects")
    async def code_projects(ctx, body):
        require_code(ctx)
        rows = await asyncio.to_thread(tcode.recent_projects, 40)
        return {"projects": rows, "cli": bool(tcode.memucode_command())}

    @op("code.browse")
    async def code_browse(ctx, body):
        require_code(ctx)
        try:
            return await asyncio.to_thread(tcode.browse_folders, str(body.get("path") or ""),
                                           hidden=bool(body.get("hidden")))
        except LookupError:
            raise ApiError(404, "not_found", "Папка не найдена", "Folder not found") from None
        except PermissionError:
            raise ApiError(403, "no_access", "Нет доступа к папке", "No access to the folder") from None
        except ValueError:
            raise ApiError(400, "bad_value", "Нужен полный путь к папке", "A full folder path is needed") from None

    @op("code.mkdir")
    async def code_mkdir(ctx, body):
        require_code(ctx)
        try:
            path = await asyncio.to_thread(tcode.make_folder, str(body.get("parent") or ""), str(body.get("name") or ""))
        except FileExistsError:
            raise ApiError(409, "exists", "Такая папка уже есть", "This folder already exists") from None
        except LookupError:
            raise ApiError(404, "not_found", "Папка не найдена", "Folder not found") from None
        except (ValueError, OSError):
            raise ApiError(400, "bad_value", "Недопустимое имя папки", "Invalid folder name") from None
        return {"path": path}

    @op("code.files")
    async def code_files(ctx, body):
        require_code(ctx)
        root = _root(ctx, body)
        try:
            return await asyncio.to_thread(tcode.list_files, root, str(body.get("path") or ""),
                                           hidden=bool(body.get("hidden")))
        except PermissionError:
            raise ApiError(403, "outside", "Путь вне папки проекта", "The path is outside the project") from None
        except (LookupError, OSError):
            raise ApiError(404, "not_found", "Папка не найдена", "Folder not found") from None

    @op("code.file")
    async def code_file(ctx, body):
        require_code(ctx)
        root = _root(ctx, body)
        try:
            return await asyncio.to_thread(tcode.read_file, root, str(body.get("path") or ""))
        except PermissionError:
            raise ApiError(403, "outside", "Путь вне папки проекта", "The path is outside the project") from None
        except (LookupError, OSError):
            raise ApiError(404, "not_found", "Файл не найден", "File not found") from None

    @op("code.changes")
    async def code_changes(ctx, body):
        require_code(ctx)
        root = _root(ctx, body)
        try:
            return await asyncio.to_thread(tcode.git_changes, root)
        except LookupError:
            raise ApiError(404, "not_found", "Папка проекта не найдена", "The project folder is missing") from None

    @op("code.diff")
    async def code_diff(ctx, body):
        require_code(ctx)
        root = _root(ctx, body)
        try:
            return {"diff": await asyncio.to_thread(tcode.file_diff, root, str(body.get("path") or ""))}
        except PermissionError:
            raise ApiError(403, "outside", "Путь вне папки проекта", "The path is outside the project") from None

    @op("code.rename")
    async def code_rename(ctx, body):
        require_code(ctx)
        try:
            await asyncio.to_thread(tcode.rename_session, str(body.get("session_id") or ""), str(body.get("name") or ""))
        except LookupError:
            raise ApiError(404, "not_found", "Сессия не найдена", "Session not found") from None
        except ValueError:
            raise ApiError(400, "bad_value", "Пустое название", "The name is empty") from None
        return {"ok": True}

    @op("code.delete")
    async def code_delete(ctx, body):
        require_code(ctx)
        try:
            await asyncio.to_thread(tcode.delete_session, str(body.get("session_id") or ""))
        except LookupError:
            raise ApiError(404, "not_found", "Сессия не найдена", "Session not found") from None
        except RuntimeError as exc:
            raise ApiError(409, str(exc), t(ctx["prefs"], "Сессия сейчас открыта или выполняется — сначала закройте её",
                                            "The session is open or running — close it first")) from None
        except ValueError:
            raise ApiError(400, "bad_value", "Некорректная сессия", "Invalid session") from None
        return {"ok": True}
