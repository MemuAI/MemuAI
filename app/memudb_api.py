"""MemuDB bridge. File selection is native; SQL cannot invent file paths."""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from . import pgdata
from .memudb import Workspace, DBError

def module_allowed(module_id: str) -> bool:
    from . import modules_host
    try:
        row=next((row for row in modules_host.list_modules(modules_host.default_root()) if row["id"]==module_id),None)
        return bool(row and row.get("enabled") and "database" in row.get("permissions",[]))
    except (OSError,ValueError,KeyError):
        return False

def register(app, *, workspace=None, allowed: Callable = module_allowed, picker=None):
    from .memudb_duckdb import DuckWorkspace
    sqlite_store=workspace or Workspace()
    duck_store=DuckWorkspace()
    app.state.memudb=sqlite_store

    def profile():
        return str(pgdata.load_profiles().get("active_id") or "default")

    async def pick(create):
        if picker:
            value=picker(create)
            return await value if inspect.isawaitable(value) else value
        window=getattr(app.state,"window",None)
        if window is None:
            raise DBError("Откройте MemuDB в приложении MemuAI для выбора файла / Open MemuDB in the desktop app to choose files")
        import webview
        if create:
            value=await asyncio.to_thread(window.create_file_dialog,webview.SAVE_DIALOG,save_filename="database.sqlite",file_types=("SQLite (*.sqlite;*.sqlite3;*.db;*.db3)",))
        else:
            value=await asyncio.to_thread(window.create_file_dialog,webview.OPEN_DIALOG,allow_multiple=False,file_types=("SQLite / DuckDB (*.sqlite;*.sqlite3;*.db;*.db3;*.duckdb)",))
        return (value[0] if isinstance(value,(tuple,list)) and value else value) or ""

    @app.post("/api/pg/memudb/call")
    async def call(request: Request):
        try:
            raw=bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw)>3*1024**2:raise DBError("Запрос превышает 3 МБ / Request exceeds 3 MB")
            body=json.loads(raw or b"{}")
            if not isinstance(body,dict):raise DBError("Ожидается объект / Expected an object")
            module_id=str(body.get("module_id") or "")
            if not module_id or allowed is not None and not allowed(module_id):
                return JSONResponse({"error":"Модулю не выдан доступ к базам данных / Module lacks database access"},403)
            active=profile()
            if body.get("profile_id") and str(body["profile_id"])!=active:
                return JSONResponse({"error":"Профиль сменился / Profile changed"},409)
            owner=(active,module_id)
            def guard():
                if profile()!=active:raise DBError("Профиль сменился. Откройте базу заново / Profile changed; reopen the database")
            op=str(body.get("op") or "")
            args=body.get("args") if isinstance(body.get("args"),dict) else {}
            session=str(args.get("connection") or "")
            store=duck_store if session.startswith("duck_") else sqlite_store
            if op in {"connection.open","connection.create"}:
                path=await pick(op.endswith("create"));guard()
                store=duck_store if str(path).lower().endswith(".duckdb") else sqlite_store
                result=await asyncio.to_thread(store.open,str(path),owner,create=op.endswith("create")) if path else {"cancelled":True}
            elif op=="connection.list":result=sqlite_store.list(owner)+duck_store.list(owner)
            elif op=="connection.close":result=await asyncio.to_thread(store.close,session,owner)
            elif op=="connection.configure":result=await asyncio.to_thread(store.configure,session,owner,args.get("readonly") is not False,confirmed=args.get("confirmed") is True)
            elif op=="schema":result=await asyncio.to_thread(store.schema,session,owner,guard=guard)
            elif op=="table.browse":result=await asyncio.to_thread(store.browse,session,owner,str(args.get("table") or ""),page=args.get("page",0),page_size=args.get("page_size",100),sort=str(args.get("sort") or ""),descending=bool(args.get("descending")),search=str(args.get("search") or ""),guard=guard)
            elif op in {"query.read","query.explain"}:result=await asyncio.to_thread(store.query,session,owner,str(args.get("sql") or ""),args.get("parameters"),limit=args.get("limit",500),opid=str(args.get("operation") or ""),guard=guard,explain=op.endswith("explain"))
            elif op=="transaction.preview":result=await asyncio.to_thread(store.preview,session,owner,args.get("operations"),opid=str(args.get("operation") or ""),guard=guard)
            elif op=="transaction.get":result=store.preview_get(session,owner,str(args.get("preview_id") or ""))
            elif op=="transaction.discard":result=store.discard(session,owner,str(args.get("preview_id") or ""))
            elif op=="transaction.apply":result=await asyncio.to_thread(store.apply,session,owner,str(args.get("preview_id") or ""),confirmed=args.get("confirmed") is True,opid=str(args.get("operation") or ""),guard=guard)
            elif op=="row.preview":result=await asyncio.to_thread(store.row_preview,session,owner,str(args.get("table") or ""),args.get("values") or {},args.get("key"),action=str(args.get("action") or "update"),guard=guard)
            elif op=="table.preview":result=await asyncio.to_thread(store.table_preview,session,owner,args.get("name"),args.get("columns"),guard=guard)
            elif op=="csv.preview":result=await asyncio.to_thread(store.csv_preview,session,owner,str(args.get("table") or ""),args.get("text"),delimiter=str(args.get("delimiter") or ","),guard=guard)
            elif op=="operation.cancel":result=store.cancel(session,owner,str(args.get("operation") or ""))
            elif op=="backup":result=await asyncio.to_thread(store.backup,session,owner,opid=str(args.get("operation") or ""),guard=guard)
            elif op=="export":result=await asyncio.to_thread(store.export,session,owner,table=str(args.get("table") or ""),format=str(args.get("format") or "csv"),safe_excel=args.get("safe_excel") is not False,guard=guard)
            else:raise DBError("Неизвестное действие MemuDB / Unknown MemuDB action")
            guard()
            return JSONResponse({"result":result})
        except (ValueError,TypeError,OSError,KeyError) as exc:
            return JSONResponse({"error":str(exc)},400)
        except Exception as exc:
            if exc.__class__.__module__.startswith(("sqlite3","_duckdb","duckdb")):
                return JSONResponse({"error":str(exc)},400)
            raise

    app.router.add_event_handler("shutdown",sqlite_store.close_all)
    app.router.add_event_handler("shutdown",duck_store.close_all)
