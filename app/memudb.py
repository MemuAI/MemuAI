"""MemuDB's SQLite workspace. SQL never writes without a staged, confirmed preview.

The production HTTP bridge opens files only through the native picker. Database
handles and prepared transactions are opaque and bound to a profile and module.
No network drivers, extension loading, ATTACH or filesystem SQL functions exist.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_SQL = 128_000
MAX_DATABASE = 4 * 1024**3
MAX_RESULT = 4 * 1024**2
MAX_EXPORT = 32 * 1024**2
MAX_PREVIEWS = 12
MAX_SESSIONS = 8
PREVIEW_TTL = 600
SESSION_TTL = 3600
_IDENT = re.compile(r"^[^\W\d][\w ]{0,99}$", re.UNICODE)

class DBError(ValueError):
    pass

def quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'

def _params(value, *, integer_bits=64):
    if value is None:
        return []
    if not isinstance(value, (list, dict)) or len(value) > 2000:
        raise DBError("Параметры должны быть списком или объектом / Parameters must be a list or object")
    rows = value.values() if isinstance(value, dict) else value
    if any(not isinstance(v, (str, int, float, bool, type(None))) or isinstance(v, str) and len(v) > 1_000_000 for v in rows):
        raise DBError("Параметры содержат неподдерживаемые значения / Unsupported parameter values")
    for item in rows:
        if isinstance(item, float) and not math.isfinite(item):
            raise DBError("Параметры должны содержать конечные числа / Parameters must contain finite numbers")
        if isinstance(item, int) and not -(1 << (integer_bits - 1)) <= item < (1 << (integer_bits - 1)):
            raise DBError("Целое число выходит за диапазон движка / Integer exceeds the database engine range")
    return dict(value) if isinstance(value, dict) else list(value)

def statements(sql: str) -> list[str]:
    sql = str(sql or "").strip()
    if not sql or len(sql) > MAX_SQL:
        raise DBError("Введите SQL до 128 КБ / Enter SQL up to 128 KB")
    result, pending = [], ""
    for char in sql:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            result.append(pending.strip())
            pending = ""
    if re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", "", pending).strip():

        result.append(pending.strip())
    if len(result) > 40:
        raise DBError("Не более 40 выражений за один просмотр / At most 40 statements per preview")
    return result

def _json_value(value, *, preview=False):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    if isinstance(value, bytes):
        if preview:
            return {"type":"blob", "bytes":len(value), "hex":value[:32].hex(), "truncated":len(value)>32}
        return {"type":"blob", "base64":base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_value(item, preview=preview) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item, preview=preview) for item in value]
    if preview and isinstance(value, str) and len(value) > 10000:
        return value[:10000] + "… [truncated]"
    return value

def _cell(value):
    return _json_value(value, preview=True)

def _rows(cursor, limit=500):
    columns = [item[0] for item in cursor.description or []]
    rows, size, truncated = [], 0, False
    if len(columns) > 200:
        raise DBError("Слишком много столбцов (предел 200) / Too many columns (limit 200)")
    for row in cursor:
        converted = [_cell(v) for v in row]
        size += len(json.dumps(converted, ensure_ascii=False, default=str).encode("utf-8"))
        if len(rows) >= limit or size > MAX_RESULT:
            truncated = True
            break
        rows.append(converted)
    return {"columns":columns, "rows":rows, "truncated":truncated, "returned":len(rows)}

_WRITE_CODES = {getattr(sqlite3, name) for name in (
    "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE", "SQLITE_CREATE_TABLE", "SQLITE_DROP_TABLE",
    "SQLITE_CREATE_INDEX", "SQLITE_DROP_INDEX", "SQLITE_CREATE_VIEW", "SQLITE_DROP_VIEW",
    "SQLITE_CREATE_TRIGGER", "SQLITE_DROP_TRIGGER", "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
    "SQLITE_CREATE_TEMP_TABLE", "SQLITE_DROP_TEMP_TABLE", "SQLITE_CREATE_TEMP_INDEX", "SQLITE_DROP_TEMP_INDEX",
    "SQLITE_CREATE_TEMP_VIEW", "SQLITE_DROP_TEMP_VIEW", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_DROP_TEMP_TRIGGER")}
_FORBIDDEN = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,
              sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE}
_READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list",
                 "database_list", "user_version", "encoding", "page_count", "page_size", "freelist_count",
                 "quick_check", "integrity_check", "table_list"}

def authorizer(writable: bool):
    def authorize(code, first, second, database, trigger):
        if code in _FORBIDDEN or code in _WRITE_CODES and not writable:
            return sqlite3.SQLITE_DENY
        if code == sqlite3.SQLITE_PRAGMA:
            if str(first).lower() not in _READ_PRAGMAS or (second is not None and str(first).lower() not in {
                    "table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}):
                return sqlite3.SQLITE_DENY
        if code == sqlite3.SQLITE_FUNCTION and str(second or first).lower() in {
                "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorize

def connect(path: Path, writable=False):
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True,
                                 timeout=2, check_same_thread=False, isolation_level=None)
    connection.enable_load_extension(False)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024**2)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 200)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    return connection

@dataclass
class Session:
    id: str
    path: Path
    owner: tuple[str,str]
    connection: sqlite3.Connection
    readonly: bool = True
    touched: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock)
    closed: bool = False

@dataclass
class Preview:
    id: str
    session: str
    owner: tuple[str,str]
    operations: list
    fingerprint: tuple
    result: dict
    created: float = field(default_factory=time.monotonic)

class Workspace:
    column_types = {"INTEGER", "TEXT", "REAL", "NUMERIC", "BLOB"}
    parameter_integer_bits = 64
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.previews: dict[str, Preview] = {}
        self.operations: dict[tuple, tuple[threading.Event, sqlite3.Connection]] = {}
        self.lock = threading.RLock()

    def _expire(self):
        now = time.monotonic()
        for key, preview in list(self.previews.items()):
            if now-preview.created > PREVIEW_TTL:
                self.previews.pop(key, None)

        for key, session in list(self.sessions.items()):
            if now-session.touched > SESSION_TTL and session.lock.acquire(blocking=False):
                try:
                    session.closed = True
                    session.connection.close()
                    self.sessions.pop(key, None)
                finally:
                    session.lock.release()

    def get(self, session_id: str, owner: tuple[str,str]) -> Session:
        with self.lock:
            self._expire()
            session = self.sessions.get(str(session_id))
            if not session or session.owner != owner or session.closed:
                raise DBError("Соединение закрыто или принадлежит другому профилю / Connection is closed or belongs to another profile")
            session.touched = time.monotonic()
            return session

    def public(self, session: Session):
        return {"id":session.id, "name":session.path.name, "path":str(session.path), "engine":"sqlite",
                "readonly":session.readonly, "bytes":session.path.stat().st_size}

    def list(self, owner):
        with self.lock:
            self._expire()
            return [self.public(s) for s in self.sessions.values() if s.owner==owner and not s.closed]

    def open(self, path: str, owner, *, create=False) -> dict:
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() not in {".sqlite", ".sqlite3", ".db", ".db3"}:
            raise DBError("Выберите файл SQLite (.sqlite, .sqlite3, .db) / Choose a SQLite database file")
        with self.lock:
            self._expire()
            existing = next((s for s in self.sessions.values() if s.owner==owner and s.path==target and not s.closed), None)
            if existing:
                return self.public(existing)
            if len([s for s in self.sessions.values() if s.owner==owner and not s.closed]) >= MAX_SESSIONS:
                raise DBError("Закройте одно из восьми соединений / Close one of the eight open connections")
            if create:

                with target.open("xb"):
                    pass
                try:
                    created = sqlite3.connect(target)
                    created.execute("PRAGMA user_version=0")
                    created.close()
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
            if not target.is_file() or target.stat().st_size > MAX_DATABASE:
                raise DBError("Файл не найден или превышает 4 ГБ / File missing or exceeds 4 GB")
            with target.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    raise DBError("Это не база SQLite / This is not a SQLite database")
            conn = connect(target)
            try:
                conn.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchall()
            except Exception:
                conn.close()
                raise
            session = Session("db_"+secrets.token_hex(12), target, owner, conn)
            self.sessions[session.id] = session
            return self.public(session)

    def close(self, session_id, owner):
        session = self.get(session_id, owner)
        with self.lock:
            session.closed = True
            for key,(cancel,conn) in list(self.operations.items()):
                if key[0] == session.id:
                    cancel.set();conn.interrupt()
        with session.lock:
            session.connection.close()
        with self.lock:
            self.sessions.pop(session.id, None)
            self.previews = {key:p for key,p in self.previews.items() if p.session!=session.id}
        return {"closed":True}

    def configure(self, session_id, owner, readonly, *, confirmed=False):
        session = self.get(session_id, owner)
        if not readonly and not confirmed:
            raise DBError("Включение записи требует подтверждения / Enabling writes requires confirmation")
        with session.lock:
            session.readonly = bool(readonly)
        return self.public(session)

    def _fingerprint(self, session):
        files = []
        for file in (session.path, Path(str(session.path)+"-wal")):
            try:
                stat = file.stat();files.append((stat.st_size,stat.st_mtime_ns,stat.st_ino))
            except FileNotFoundError:
                files.append(None)
        return (session.connection.execute("PRAGMA data_version").fetchone()[0], *files)

    def _begin_op(self, session, connection, opid, guard, seconds=10):
        opid = str(opid or secrets.token_hex(8))[:80]
        cancel = threading.Event()
        key = (session.id, opid)
        with self.lock:
            if key in self.operations:
                raise DBError("Запрос с этим ID уже выполняется / Operation ID is already running")
            self.operations[key] = (cancel,connection)
        deadline = time.monotonic()+max(1,min(30,float(seconds)))
        last_check = 0.0
        def progress():
            nonlocal last_check
            now = time.monotonic()
            if cancel.is_set() or session.closed or now>deadline:
                return 1
            if now-last_check>.2:
                last_check=now
                try:
                    guard()
                except Exception:
                    return 1
            return 0
        connection.set_progress_handler(progress,1000)
        return key, progress

    def cancel(self, session_id, owner, opid):
        session = self.get(session_id,owner)
        with self.lock:
            item=self.operations.get((session.id,str(opid)[:80]))
            if item:
                item[0].set();item[1].interrupt()
        return {"cancelled":bool(item)}

    def _end_op(self, key, connection):
        connection.set_progress_handler(None,0)
        connection.set_authorizer(None)
        with self.lock:
            self.operations.pop(key,None)

    def schema(self, session_id, owner, *, guard=lambda:None):
        session=self.get(session_id,owner)
        with session.lock:
            guard()
            conn=session.connection
            key,_=self._begin_op(session,conn,None,guard,5)
            try:
                tables=[]
                rows=conn.execute("SELECT name,type,sql FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 501").fetchall()
                for name,kind,sql in rows[:500]:
                    columns=[{"cid":r[0],"name":r[1],"type":r[2],"notnull":bool(r[3]),"default":r[4],"pk":r[5],"hidden":r[6]} for r in conn.execute("PRAGMA table_xinfo("+quote(name)+")")]
                    indexes=[{"name":r[1],"unique":bool(r[2]),"origin":r[3],"partial":bool(r[4])} for r in conn.execute("PRAGMA index_list("+quote(name)+")")]
                    foreign=[{"table":r[2],"from":r[3],"to":r[4],"on_update":r[5],"on_delete":r[6]} for r in conn.execute("PRAGMA foreign_key_list("+quote(name)+")")]
                    tables.append({"name":name,"type":kind,"sql":sql or "","columns":columns,"indexes":indexes,"foreign_keys":foreign})
                version=conn.execute("SELECT sqlite_version()").fetchone()[0]
                guard()
                return {"connection":self.public(session),"tables":tables,"truncated":len(rows)>500,"sqlite_version":version}
            finally:
                self._end_op(key,conn)

    def query(self, session_id, owner, sql, parameters=None, *, limit=500, opid="", guard=lambda:None, explain=False):
        session=self.get(session_id,owner)
        scripts=statements(sql)
        if len(scripts)!=1:
            raise DBError("Для запроса используйте одно выражение / Queries accept one statement")
        parameters=_params(parameters)
        started=time.monotonic()
        with session.lock:
            guard();conn=session.connection
            key,_=self._begin_op(session,conn,opid,guard)
            conn.set_authorizer(authorizer(False))
            try:
                stmt=re.sub(r"^\s*EXPLAIN(?:\s+QUERY\s+PLAN)?\b\s*","",scripts[0],flags=re.I) if explain else scripts[0]
                cursor=conn.execute(("EXPLAIN QUERY PLAN " if explain else "")+stmt,parameters)
                result=_rows(cursor,max(1,min(1000,int(limit))));cursor.close()
                guard();result.update(ms=round((time.monotonic()-started)*1000),readonly=True)
                return result
            except sqlite3.DatabaseError as exc:
                message=str(exc)
                if "not authorized" in message or "readonly" in message:
                    message="Этот запрос изменяет данные или использует запрещённую операцию. Сначала подготовьте изменения / This query writes data or uses a forbidden operation. Preview changes first"
                raise DBError(message) from exc
            finally:
                self._end_op(key,conn)

    def browse(self, session_id, owner, table, *, page=0, page_size=100, sort="", descending=False, search="", guard=lambda:None):
        schema=self.schema(session_id,owner,guard=guard)
        info=next((t for t in schema["tables"] if t["name"]==table),None)
        if not info:
            raise DBError("Таблица не найдена / Table not found")
        names=[c["name"] for c in info["columns"] if c["hidden"]!=1]
        if sort and sort not in names:
            raise DBError("Столбец сортировки не найден / Sort column not found")
        page=max(0,min(1_000_000,int(page)));page_size=max(10,min(500,int(page_size)))
        where="";params=[]
        if search:
            value=str(search)[:300].replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
            where=" WHERE ("+" OR ".join("CAST("+quote(name)+" AS TEXT) LIKE ? ESCAPE '\\'" for name in names[:60])+")"
            params=["%"+value+"%"]*min(60,len(names))
        order=" ORDER BY "+quote(sort)+(" DESC" if descending else " ASC") if sort else ""
        result=self.query(session_id,owner,"SELECT * FROM "+quote(table)+where+order+" LIMIT ? OFFSET ?",params+[page_size,page*page_size],limit=page_size,guard=guard)
        count=self.query(session_id,owner,"SELECT COUNT(*) FROM "+quote(table)+where,params,limit=1,guard=guard)
        result.update(table=table,page=page,page_size=page_size,total=count["rows"][0][0],schema=info)
        return result

    def preview(self, session_id, owner, operations, *, opid="", guard=lambda:None):
        session=self.get(session_id,owner)
        if session.readonly:
            raise DBError("Соединение только для чтения. Включите запись / Connection is read-only; enable writes first")
        if not isinstance(operations,list) or not 1<=len(operations)<=40:
            raise DBError("Нужно от 1 до 40 операций / Expected 1–40 operations")
        prepared=[]
        for operation in operations:
            if not isinstance(operation,dict):raise DBError("Некорректная операция / Invalid operation")
            sqls=statements(operation.get("sql"))
            if len(sqls)>1 and operation.get("parameters"):
                raise DBError("Параметры применяются к одному выражению / Parameters apply to one statement")
            prepared.extend({"sql":sql,"parameters":_params(operation.get("parameters"))} for sql in sqls)
        if len(prepared)>40:raise DBError("Не более 40 выражений / At most 40 statements")
        with session.lock:
            if session.readonly:
                raise DBError("Соединение только для чтения / Connection is read-only")
            guard();source=session.connection
            fingerprint=self._fingerprint(session)
            fd,snapshot=tempfile.mkstemp(prefix="memudb-preview-",suffix=".sqlite")
            os.close(fd)
            clone=sqlite3.connect(snapshot,check_same_thread=False,isolation_level=None)
            clone.enable_load_extension(False);clone.execute("PRAGMA foreign_keys=ON");clone.execute("PRAGMA trusted_schema=OFF")
            clone.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,MAX_SQL)
            clone.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,16*1024**2)
            clone.setlimit(sqlite3.SQLITE_LIMIT_COLUMN,200)
            key,progress=self._begin_op(session,clone,opid,guard,30)
            try:
                source.backup(clone,pages=256,progress=lambda *args: (_ for _ in ()).throw(DBError("Копирование отменено / Snapshot cancelled")) if progress() else None)
                if fingerprint!=self._fingerprint(session):raise DBError("База изменилась во время подготовки. Повторите / Database changed during snapshot; retry")
                before={r[0]:r[1] for r in clone.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")}
                clone.execute("BEGIN")
                clone.set_authorizer(authorizer(True))
                results=[]; result_size=0
                for operation in prepared:
                    changed_before=clone.total_changes
                    cursor=clone.execute(operation["sql"],operation["parameters"])
                    row=_rows(cursor,50) if cursor.description else {"columns":[],"rows":[],"returned":0,"truncated":False}
                    cursor.close()
                    row.update(changes=max(0,clone.total_changes-changed_before),sql=operation["sql"])
                    row_size=len(json.dumps(row,ensure_ascii=False,default=str).encode("utf-8"))
                    if result_size+row_size>MAX_RESULT:
                        row.update(rows=[],returned=0,truncated=True)
                        row_size=len(json.dumps(row,ensure_ascii=False,default=str).encode("utf-8"))
                    result_size+=row_size
                    results.append(row)
                clone.set_authorizer(None)
                violations=clone.execute("PRAGMA foreign_key_check").fetchmany(20)
                if violations:raise DBError("Изменения нарушают внешние ключи / Changes violate foreign keys")
                after={r[0]:r[1] for r in clone.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")}
                result={"statements":results,"affected":sum(r["changes"] for r in results),"schema_changes":[{"name":name,"before":before.get(name),"after":after.get(name)} for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)],"note":"Preview only; original database unchanged"}
                guard()
                with self.lock:
                    self._expire()
                    owned=[p for p in self.previews.values() if p.owner==owner]
                    if len(owned)>=MAX_PREVIEWS:self.previews.pop(min(owned,key=lambda p:p.created).id,None)
                    token="tx_"+secrets.token_hex(18)
                    self.previews[token]=Preview(token,session.id,owner,prepared,fingerprint,result)
                return {"preview_id":token,**result,"expires_in":PREVIEW_TTL}
            except sqlite3.DatabaseError as exc:
                raise DBError(str(exc)) from exc
            finally:
                self._end_op(key,clone);clone.close()
                for suffix in ("", "-journal", "-wal", "-shm"):
                    Path(snapshot+suffix).unlink(missing_ok=True)

    def preview_get(self, session_id, owner, preview_id):
        self.get(session_id,owner)
        with self.lock:
            self._expire();preview=self.previews.get(str(preview_id))
            if not preview or preview.owner!=owner or preview.session!=session_id:
                raise DBError("Просмотр устарел. Подготовьте изменения заново / Preview expired; prepare changes again")
            return {"preview_id":preview.id,**preview.result}

    def discard(self, session_id, owner, preview_id):
        self.preview_get(session_id,owner,preview_id)
        with self.lock:self.previews.pop(str(preview_id),None)
        return {"discarded":True}

    def _backup(self, session, *, check=lambda:False):
        folder=session.path.parent/".memudb-backups"
        folder.mkdir(exist_ok=True)
        path=folder/(session.path.stem+"-"+time.strftime("%Y%m%d-%H%M%S")+"-"+secrets.token_hex(4)+".sqlite")
        with path.open("xb"):pass
        target=sqlite3.connect(path)
        try:
            def progress(*_):
                if check():raise DBError("Копирование отменено / Backup cancelled")
            session.connection.backup(target,pages=256,progress=progress,sleep=.05)
        except BaseException:
            target.close();path.unlink(missing_ok=True)
            raise
        finally:target.close()
        return str(path)

    def backup(self, session_id, owner, *, opid="", guard=lambda:None):
        session=self.get(session_id,owner)
        with session.lock:
            guard();conn=session.connection
            key,progress=self._begin_op(session,conn,opid,guard,30)
            try:return {"path":self._backup(session,check=progress)}
            finally:self._end_op(key,conn)

    def apply(self, session_id, owner, preview_id, *, confirmed=False, opid="", guard=lambda:None):
        if confirmed is not True:raise DBError("Применение требует явного подтверждения / Applying requires explicit confirmation")
        self.preview_get(session_id,owner,preview_id)
        with self.lock:preview=self.previews[str(preview_id)]
        session=self.get(session_id,owner)
        with session.lock:
            if session.readonly:raise DBError("Соединение только для чтения / Connection is read-only")
            guard();conn=connect(session.path,True);key,progress=self._begin_op(session,conn,opid,guard,30)
            try:
                conn.execute("BEGIN IMMEDIATE")
                if self._fingerprint(session)!=preview.fingerprint:
                    raise DBError("База изменилась после просмотра. Подготовьте изменения заново / Database changed after preview; prepare again")
                backup=self._backup(session,check=progress)
                conn.set_authorizer(authorizer(True))
                affected=0
                for operation in preview.operations:
                    changed_before=conn.total_changes
                    cursor=conn.execute(operation["sql"],operation["parameters"])
                    if cursor.description:
                        for _ in cursor:pass
                    cursor.close();affected+=max(0,conn.total_changes-changed_before)
                conn.set_authorizer(None)
                if conn.execute("PRAGMA foreign_key_check").fetchone():raise DBError("Нарушены внешние ключи / Foreign key violation")
                guard()
                conn.execute("COMMIT")
                with self.lock:
                    self.previews={key:p for key,p in self.previews.items() if p.session!=session.id}
                return {"applied":True,"affected":affected,"backup":backup}
            except sqlite3.DatabaseError as exc:
                raise DBError(str(exc)) from exc
            finally:
                conn.set_authorizer(None)
                if conn.in_transaction:conn.rollback()
                self._end_op(key,conn);conn.close()

    def export(self, session_id, owner, *, table="", format="csv", safe_excel=True, guard=lambda:None):
        session=self.get(session_id,owner)
        with session.lock:
            guard();conn=session.connection;key,progress=self._begin_op(session,conn,None,guard,30)
            try:
                if format=="sql":
                    pieces=[];size=0
                    for line in conn.iterdump():
                        size+=len(line.encode("utf-8"))+1
                        if size>MAX_EXPORT or progress():raise DBError("Экспорт превысил лимит 32 МБ или время / Export exceeded 32 MB or time limit")
                        pieces.append(line)
                    return {"name":session.path.stem+".sql","text":"\n".join(pieces)+"\n","mime":"text/plain"}
                if format not in {"csv","json"}:raise DBError("Поддерживаются CSV, JSON и SQL / Supported formats: CSV, JSON and SQL")
                if not conn.execute("SELECT 1 FROM sqlite_schema WHERE name=? AND type IN ('table','view')",(table,)).fetchone():raise DBError("Таблица не найдена / Table not found")
                conn.set_authorizer(authorizer(False));cursor=conn.execute("SELECT * FROM "+quote(table))
                columns=[c[0] for c in cursor.description]
                output=io.StringIO();writer=csv.writer(output);rows=[];json_size=2
                if format=="csv":writer.writerow(["'"+v if safe_excel and v.lstrip().startswith(("=","+","-","@")) else v for v in columns])
                for index,row in enumerate(cursor):
                    if index>=100000:raise DBError("Экспорт ограничен 100000 строк / Export is limited to 100000 rows")
                    values=[_json_value(value) for value in row]
                    if format=="csv":
                        cells=[json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v for v in values]
                        if safe_excel:cells=["'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")) else v for v in cells]
                        writer.writerow(cells)
                        if output.tell()>MAX_EXPORT:raise DBError("Экспорт превысил 32 МБ / Export exceeded 32 MB")
                    else:
                        record=json.dumps(dict(zip(columns,values)),ensure_ascii=False,default=str,allow_nan=False)
                        json_size+=len(record.encode("utf-8"))+2
                        if json_size>MAX_EXPORT:raise DBError("Экспорт превысил 32 МБ / Export exceeded 32 MB")
                        rows.append(record)
                cursor.close();guard()
                text=("\ufeff"+output.getvalue()) if format=="csv" else '[\n'+',\n'.join(rows)+'\n]'
                if len(text.encode("utf-8"))>MAX_EXPORT:raise DBError("Экспорт превысил 32 МБ / Export exceeded 32 MB")
                return {"name":re.sub(r'[^\w .-]','_',table)[:80]+"."+format,"text":text,"mime":"text/csv" if format=="csv" else "application/json"}
            finally:self._end_op(key,conn)

    def close_all(self):
        for session in list(self.sessions.values()):
            try:self.close(session.id,session.owner)
            except Exception:pass

    def row_preview(self, session_id, owner, table, values, key=None, *, action="update", guard=lambda:None):
        schema=self.schema(session_id,owner,guard=guard)
        info=next((t for t in schema["tables"] if t["name"]==table and t["type"]=="table"),None)
        if not info:raise DBError("Таблица не найдена или это представление / Table missing or is a view")
        names={c["name"] for c in info["columns"] if not c["hidden"]}
        if not isinstance(values,dict) or not set(values)<=names:raise DBError("Некорректные столбцы / Invalid columns")
        _params(values, integer_bits=self.parameter_integer_bits)
        if action=="insert":
            if values:
                columns=list(values)
                sql="INSERT INTO "+quote(table)+" ("+",".join(map(quote,columns))+") VALUES ("+",".join("?" for _ in columns)+") RETURNING *"
                parameters=list(values.values())
            else:sql="INSERT INTO "+quote(table)+" DEFAULT VALUES RETURNING *";parameters=[]
        elif action in {"update","delete"}:
            pk={c["name"] for c in info["columns"] if c["pk"]}
            if not pk or not isinstance(key,dict) or set(key)!=pk or any(v is None for v in key.values()):
                raise DBError("Для изменения строки нужен полный первичный ключ / A complete primary key is required")
            _params(key, integer_bits=self.parameter_integer_bits)
            where=" WHERE "+" AND ".join(quote(name)+getattr(self,"key_comparison"," IS ?") for name in key)
            if action=="delete":sql="DELETE FROM "+quote(table)+where+" RETURNING *";parameters=list(key.values())
            else:
                if not values:raise DBError("Нет изменений / No changes")
                sql="UPDATE "+quote(table)+" SET "+",".join(quote(name)+"=?" for name in values)+where+" RETURNING *"
                parameters=list(values.values())+list(key.values())
        else:raise DBError("Неизвестное действие / Unknown row action")
        return self.preview(session_id,owner,[{"sql":sql,"parameters":parameters}],guard=guard)

    def table_preview(self, session_id, owner, name, columns, *, guard=lambda:None):
        name=str(name or "").strip()
        if not _IDENT.fullmatch(name) or name.lower().startswith("sqlite_"):
            raise DBError("Название: буквы, цифры, пробелы и подчёркивание / Use letters, digits, spaces and underscores for the name")
        if not isinstance(columns,list) or not 1<=len(columns)<=60:raise DBError("Нужно 1–60 столбцов / Expected 1–60 columns")
        definitions=[];seen=set();pk=[]
        for item in columns:
            if not isinstance(item,dict):
                raise DBError("Столбец должен быть объектом / Column must be an object")
            key=str(item.get("name") or "").strip();kind=str(item.get("type") or "TEXT").upper()
            if not _IDENT.fullmatch(key) or key.casefold() in seen or kind not in self.column_types:
                raise DBError("Проверьте имена и типы столбцов / Check column names and types")
            seen.add(key.casefold());definition=quote(key)+" "+kind
            if item.get("notnull"):definition+=" NOT NULL"
            if item.get("unique"):definition+=" UNIQUE"
            if item.get("pk"):pk.append(key)
            if "default" in item and item["default"] is not None:
                value=item["default"]
                if not isinstance(value,(str,int,float,bool)):raise DBError("Некорректное значение по умолчанию / Invalid default")
                _params([value], integer_bits=self.parameter_integer_bits)
                definition+=" DEFAULT "+("'"+str(value).replace("'","''")+"'" if isinstance(value,str) else str(int(value) if isinstance(value,bool) else value))
            definitions.append(definition)
        if pk:definitions.append("PRIMARY KEY ("+",".join(map(quote,pk))+")")
        return self.preview(session_id,owner,[{"sql":"CREATE TABLE "+quote(name)+" ("+",".join(definitions)+")"}],guard=guard)

    def csv_preview(self, session_id, owner, table, text, *, delimiter=",", guard=lambda:None):
        if not isinstance(text,str) or len(text.encode("utf-8"))>1024**2:raise DBError("CSV должен быть до 1 МБ / CSV must be at most 1 MB")
        if delimiter not in {",",";","\t"}:raise DBError("Неподдерживаемый разделитель / Unsupported delimiter")
        schema=self.schema(session_id,owner,guard=guard)
        info=next((t for t in schema["tables"] if t["name"]==table and t["type"]=="table"),None)
        if not info:raise DBError("Таблица не найдена / Table not found")
        try:
            rows=list(csv.reader(io.StringIO(text.lstrip("\ufeff")),delimiter=delimiter,strict=True))
        except csv.Error as exc:
            raise DBError("Некорректный CSV / Invalid CSV: "+str(exc)) from exc
        if not rows or not 1<=len(rows[0])<=60 or len(rows)>501:raise DBError("CSV: заголовок и до 500 строк / CSV needs a header and up to 500 rows")
        columns=rows[0]
        names={c["name"] for c in info["columns"] if not c["hidden"]}
        if len(set(columns))!=len(columns) or not set(columns)<=names:raise DBError("Заголовки CSV должны совпадать со столбцами / CSV headers must match table columns")
        if any(len(row)!=len(columns) for row in rows[1:]):raise DBError("В строках разное число полей / Rows have different field counts")
        if len(rows)==1:raise DBError("В CSV нет строк данных / CSV contains no data rows")
        operations=[]
        for offset in range(1,len(rows),20):
            batch=rows[offset:offset+20]
            sql="INSERT INTO "+quote(table)+" ("+",".join(map(quote,columns))+") VALUES "+",".join("("+",".join("?" for _ in columns)+")" for _ in batch)
            operations.append({"sql":sql,"parameters":[value for row in batch for value in row]})
        return self.preview(session_id,owner,operations,guard=guard)
