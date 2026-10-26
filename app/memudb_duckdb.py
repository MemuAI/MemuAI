"""DuckDB adapter for MemuDB: isolated previews, confirmed transactions, no external SQL access."""
from __future__ import annotations
import csv
import base64
import io
import json
import secrets
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from .memudb import Workspace, Session, Preview, DBError, quote, _params, _rows, _json_value, MAX_PREVIEWS, PREVIEW_TTL, MAX_DATABASE, MAX_EXPORT, MAX_RESULT

def _duck_rows(cursor,limit):
    class Rows:
        description=cursor.description
        def __iter__(self):
            while True:
                row=cursor.fetchone()
                if row is None:break
                yield row
    return _rows(Rows(),limit)

def connect(path, writable=False):
    import duckdb
    conn=duckdb.connect(str(path),read_only=not writable,config={"enable_external_access":"false","autoload_known_extensions":"false","autoinstall_known_extensions":"false","allow_community_extensions":"false","memory_limit":"512MB","threads":"2"})
    conn.execute("SET lock_configuration=true")
    return conn

class DuckWorkspace(Workspace):
    key_comparison = " IS NOT DISTINCT FROM ?"
    column_types = Workspace.column_types | {"BIGINT", "DOUBLE", "VARCHAR", "BOOLEAN", "TIMESTAMP", "JSON"}
    parameter_integer_bits = 128
    def public(self,s):
        return {"id":s.id,"name":s.path.name,"path":str(s.path),"engine":"duckdb","readonly":s.readonly,"bytes":s.path.stat().st_size}

    def open(self,path,owner,*,create=False):
        target=Path(path).resolve()
        if target.suffix.lower()!='.duckdb':raise DBError('Выберите файл .duckdb / Choose a .duckdb file')
        with self.lock:
            self._expire()
            for session in self.sessions.values():
                if session.closed:
                    continue
                if session.path==target:
                    if session.owner==owner:return self.public(session)
                    raise DBError('База уже открыта в другом профиле / Database is open in another profile')
            if sum(1 for s in self.sessions.values() if s.owner==owner and not s.closed)>=8:raise DBError('Закройте одно из соединений / Close a connection first')
            if create:
                if target.exists():raise DBError('Файл уже существует / File already exists')

                raise DBError('Создайте DuckDB внешним инструментом; здесь доступно открытие / Create DuckDB externally, then open it here')
            if not target.is_file() or target.stat().st_size>MAX_DATABASE:raise DBError('Файл не найден или больше 4 ГБ / File missing or exceeds 4 GB')
            conn=connect(target)
            session=Session('duck_'+secrets.token_hex(12),target,owner,conn)
            self.sessions[session.id]=session
            return self.public(session)

    def _fingerprint(self,s):
        return tuple((p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else None for p in (s.path,Path(str(s.path)+'.wal')))

    def _require_checkpoint(self,s):
        wal=Path(str(s.path)+'.wal')
        if wal.exists() and wal.stat().st_size:
            raise DBError('Закройте программу, изменявшую базу, и выполните CHECKPOINT в ней / Checkpoint and close the database in its writer before preparing changes')

    def _checked(self,conn,sql,write=False):
        if not isinstance(sql,str) or not sql.strip() or len(sql)>128000:raise DBError('Введите SQL до 128 КБ / Enter SQL up to 128 KB')
        parsed=conn.extract_statements(sql)
        allowed=({'SELECT','INSERT','UPDATE','DELETE','CREATE','ALTER','DROP'} if write else {'SELECT','EXPLAIN'})
        if not parsed or len(parsed)>40 or any(str(p.type).split('.')[-1] not in allowed for p in parsed):raise DBError('Эта операция SQL недоступна / SQL operation is unavailable')
        for statement in parsed:
            kind=str(statement.type).split('.')[-1]
            cleaned=re.sub(r'^\s*(?:(?:--[^\n]*\n?)|(?:/\*[\s\S]*?\*/\s*))+','',statement.query)
            if kind in {'CREATE','ALTER','DROP'} and not re.match(r'^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|(?:UNIQUE\s+)?INDEX|SEQUENCE)|ALTER\s+TABLE|DROP\s+(?:TABLE|VIEW|INDEX|SEQUENCE))\b',cleaned,re.I):
                raise DBError('Поддерживаются изменения таблиц, представлений, индексов и последовательностей / Only table, view, index and sequence changes are supported')
        return parsed

    def _begin_op(self,s,conn,opid,guard,seconds=10):
        key=(s.id,str(opid or secrets.token_hex(8))[:80])
        cancel=threading.Event()
        with self.lock:
            if key in self.operations:raise DBError('Операция уже выполняется / Operation already running')
            self.operations[key]=(cancel,conn)
        stopped=threading.Event()
        deadline=time.monotonic()+max(1,min(30,float(seconds)))
        def watch():
            while not stopped.wait(.1):
                try:
                    if cancel.is_set() or s.closed or time.monotonic()>deadline:
                        break
                    guard()
                except Exception:
                    break
            else:
                return
            if not stopped.is_set():
                cancel.set()
                try:conn.interrupt()
                except Exception:pass
        threading.Thread(target=watch,daemon=True,name='memudb-operation').start()
        return key,stopped

    def _finish(self,key,timer):
        timer.set()
        with self.lock:self.operations.pop(key,None)

    def query(self,session_id,owner,sql,parameters=None,*,limit=500,opid='',guard=lambda:None,explain=False):
        s=self.get(session_id,owner)
        with s.lock:
            guard();parsed=self._checked(s.connection,sql)
            if len(parsed)!=1:raise DBError('Один запрос за раз / One query at a time')
            key,timer=self._begin_op(s,s.connection,opid,guard)
            try:
                stmt=re.sub(r'^\s*EXPLAIN\b\s*','',sql,flags=re.I) if explain else sql
                start=time.monotonic();cursor=s.connection.execute(('EXPLAIN ' if explain else '')+stmt,_params(parameters,integer_bits=self.parameter_integer_bits))
                result=_duck_rows(cursor,max(1,min(1000,int(limit))));guard()

                result=json.loads(json.dumps(result,default=str,ensure_ascii=False))
                return {**result,'readonly':True,'ms':round((time.monotonic()-start)*1000)}
            except Exception as exc:raise DBError(str(exc)) from exc
            finally:self._finish(key,timer)

    def schema(self,session_id,owner,*,guard=lambda:None):
        s=self.get(session_id,owner)
        with s.lock:
            guard();conn=s.connection;tables=[]
            key,watch=self._begin_op(s,conn,'',guard)
            try:
                rows=conn.execute("SELECT table_name,table_type FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name LIMIT 501").fetchall()
                for name,kind in rows[:500]:
                    cols=conn.execute("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",[name]).fetchall()
                    pk=conn.execute("SELECT constraint_column_names FROM duckdb_constraints() WHERE schema_name='main' AND table_name=? AND constraint_type='PRIMARY KEY'",[name]).fetchall()
                    keys=pk[0][0] if pk else []
                    columns=[{'cid':i,'name':r[0],'type':r[1],'notnull':r[2]=='NO','default':r[3],'pk':keys.index(r[0])+1 if r[0] in keys else 0,'hidden':0} for i,r in enumerate(cols)]
                    tables.append({'name':name,'type':'view' if kind=='VIEW' else 'table','columns':columns,'indexes':[],'foreign_keys':[],'sql':''})
                guard();return {'connection':self.public(s),'tables':tables,'truncated':len(rows)>500,'engine_version':conn.execute('SELECT version()').fetchone()[0]}
            finally:self._finish(key,watch)

    def _schema_snapshot(self,conn):
        tables=conn.execute("SELECT table_name,sql FROM duckdb_tables() WHERE schema_name='main'").fetchall()
        views=conn.execute("SELECT view_name,sql FROM duckdb_views() WHERE schema_name='main' AND NOT internal").fetchall()
        indexes=conn.execute("SELECT index_name,sql FROM duckdb_indexes() WHERE schema_name='main'").fetchall()
        sequences=conn.execute("SELECT sequence_name,start_value,min_value,max_value,increment_by,cycle FROM duckdb_sequences() WHERE schema_name='main'").fetchall()
        result=dict(tables+views)
        result.update(('index:'+name,sql) for name,sql in indexes)
        result.update(('sequence:'+str(row[0]),json.dumps(row[1:],default=str)) for row in sequences)
        return result

    def _backup(self,s,*,check=lambda:False):
        self._require_checkpoint(s)
        fingerprint=self._fingerprint(s)
        folder=s.path.parent/'.memudb-backups';folder.mkdir(exist_ok=True)
        path=folder/(s.path.stem+'-'+secrets.token_hex(8)+'.duckdb')
        try:
            with s.path.open('rb') as source,path.open('xb') as target:
                while chunk:=source.read(1024*1024):
                    if check():raise DBError('Копирование отменено / Backup cancelled')
                    target.write(chunk)
            shutil.copystat(s.path,path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if fingerprint!=self._fingerprint(s):
            path.unlink(missing_ok=True)
            raise DBError('База изменилась при копировании / Database changed while copying')
        return str(path)

    def backup(self,session_id,owner,*,opid='',guard=lambda:None):
        s=self.get(session_id,owner)
        with s.lock:
            guard();key,watch=self._begin_op(s,s.connection,opid,guard,30)
            def check():
                guard()
                return self.operations[key][0].is_set()
            try:return {'path':self._backup(s,check=check)}
            finally:self._finish(key,watch)

    def preview(self,session_id,owner,operations,*,opid='',guard=lambda:None):
        s=self.get(session_id,owner)
        if s.readonly:raise DBError('Включите подготовку изменений / Enable preparing changes')
        if not isinstance(operations,list) or not 1<=len(operations)<=40:raise DBError('Нужно 1–40 операций / Expected 1–40 operations')
        prepared=[]
        for op in operations:
            if not isinstance(op,dict):raise DBError('Некорректная операция / Invalid operation')
            with s.lock:
                parsed=self._checked(s.connection,op.get('sql'),True)
            parameters=_params(op.get('parameters'),integer_bits=self.parameter_integer_bits)
            if len(parsed)>1 and parameters:raise DBError('Параметры применяются к одному выражению / Parameters apply to one statement')
            prepared.extend({'sql':statement.query,'parameters':parameters} for statement in parsed)
        if len(prepared)>40:raise DBError('Не более 40 выражений / At most 40 statements')
        with s.lock,tempfile.TemporaryDirectory(prefix='memudb-duck-') as folder:
            if s.readonly:raise DBError('Только чтение / Read-only')
            self._require_checkpoint(s)
            guard();fingerprint=self._fingerprint(s);target=Path(folder)/'preview.duckdb';shutil.copy2(s.path,target)
            if fingerprint!=self._fingerprint(s):raise DBError('База изменилась / Database changed')
            conn=connect(target,True);key,timer=self._begin_op(s,conn,opid,guard,30)
            try:
                before=self._schema_snapshot(conn)
                conn.execute('BEGIN');results=[];result_size=0
                for op in prepared:
                    cur=conn.execute(op['sql'],op['parameters']);r=_duck_rows(cur,50)
                    changed=int(r['rows'][0][0]) if r['columns']==['Count'] and r['rows'] else 0
                    kind=str(conn.extract_statements(op['sql'])[-1].type).split('.')[-1]
                    if kind in {'INSERT','UPDATE','DELETE'} and r['columns']!=['Count']:
                        changed=len(r['rows'])+int(r['truncated'])
                        while batch:=cur.fetchmany(1000):changed+=len(batch)
                    if r['columns']==['Count'] and kind!='SELECT':
                        r={'columns':[],'rows':[],'returned':0,'truncated':False}
                    row={**json.loads(json.dumps(r,default=str)),'sql':op['sql'],'changes':changed}
                    row_size=len(json.dumps(row,ensure_ascii=False,default=str).encode('utf-8'))
                    if result_size+row_size>MAX_RESULT:
                        row.update(rows=[],returned=0,truncated=True)
                        row_size=len(json.dumps(row,ensure_ascii=False,default=str).encode('utf-8'))
                    result_size+=row_size
                    results.append(row)
                after=self._schema_snapshot(conn)
                schema_changes=[{'name':name,'before':before.get(name),'after':after.get(name)} for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)]
                guard();token='tx_'+secrets.token_hex(18)
                result={'statements':results,'affected':sum(r['changes'] for r in results),'schema_changes':schema_changes,'note':'Preview on an isolated copy; original unchanged'}
                with self.lock:
                    self._expire()
                    owned=[p for p in self.previews.values() if p.owner==owner]
                    if len(owned)>=MAX_PREVIEWS:self.previews.pop(min(owned,key=lambda p:p.created).id,None)
                    self.previews[token]=Preview(token,s.id,owner,prepared,fingerprint,result)
                return {'preview_id':token,**result,'expires_in':PREVIEW_TTL}
            except Exception as exc:raise DBError(str(exc)) from exc
            finally:self._finish(key,timer);conn.close()

    def apply(self,session_id,owner,preview_id,*,confirmed=False,opid='',guard=lambda:None):
        if confirmed is not True:raise DBError('Нужно подтверждение / Confirmation required')
        self.preview_get(session_id,owner,preview_id);s=self.get(session_id,owner)
        with s.lock:
            if s.readonly:raise DBError('Только чтение / Read-only')
            preview=self.previews[preview_id];guard()
            self._require_checkpoint(s)
            if self._fingerprint(s)!=preview.fingerprint:raise DBError('База изменилась; повторите просмотр / Database changed; preview again')
            deadline=time.monotonic()+30
            def check_backup():
                guard()
                return s.closed or time.monotonic()>deadline
            backup=self._backup(s,check=check_backup);s.connection.close();conn=None;timer=None
            try:
                conn=connect(s.path,True)
                if self._fingerprint(s)!=preview.fingerprint:raise DBError('База изменилась; повторите просмотр / Database changed; preview again')
                key,timer=self._begin_op(s,conn,opid,guard,30)
                conn.execute('BEGIN')
                for op in preview.operations:
                    self._checked(conn,op['sql'],True);conn.execute(op['sql'],op['parameters'])
                    while conn.fetchmany(1000):pass
                guard();conn.execute('COMMIT')
                with self.lock:self.previews={k:p for k,p in self.previews.items() if p.session!=s.id}
                return {'applied':True,'affected':preview.result['affected'],'backup':backup}
            except Exception as exc:raise DBError(str(exc)) from exc
            finally:
                if timer:self._finish(key,timer)
                if conn:conn.close()
                try:
                    s.connection=connect(s.path)
                except Exception:
                    s.closed=True
                    with self.lock:
                        self.sessions.pop(s.id,None)

    def export(self,session_id,owner,*,table='',format='csv',safe_excel=True,guard=lambda:None):
        if format not in {'csv','json'}:raise DBError('Для DuckDB экспортируйте CSV/JSON или создайте резервную копию / DuckDB supports CSV/JSON export and file backups')
        if table not in {t['name'] for t in self.schema(session_id,owner,guard=guard)['tables']}:raise DBError('Таблица не найдена / Table not found')
        s=self.get(session_id,owner)
        with s.lock:
            guard();key,timer=self._begin_op(s,s.connection,'',guard,30)
            try:
                cursor=s.connection.execute('SELECT * FROM '+quote(table))
                columns=[column[0] for column in cursor.description]
                output=io.StringIO();writer=csv.writer(output);encoded=[];size=0
                def csv_row(values):
                    cells=[json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list,tuple)) else str(v) if v is not None else '' for v in values]
                    writer.writerow(["'"+v if safe_excel and v.lstrip().startswith(('=','+','-','@')) else v for v in cells])
                if format=='csv':csv_row(columns)
                count=0
                while (row:=cursor.fetchone()) is not None:
                    count+=1
                    if count>100000:raise DBError('Экспорт ограничен 100000 строк / Export is limited to 100000 rows')
                    values=[_json_value(value) for value in row]
                    if format=='csv':
                        csv_row(values)
                        if output.tell()>MAX_EXPORT:raise DBError('Экспорт превышает 32 МБ / Export exceeds 32 MB')
                    else:
                        record=json.dumps(dict(zip(columns,values)),ensure_ascii=False,default=str)
                        size+=len(record.encode('utf-8'))+2
                        if size>MAX_EXPORT:raise DBError('Экспорт превышает 32 МБ / Export exceeds 32 MB')
                        encoded.append(record)
                    if count%128==0:guard()
                text=('\ufeff'+output.getvalue()) if format=='csv' else '[\n'+',\n'.join(encoded)+'\n]'
                if len(text.encode('utf-8'))>MAX_EXPORT:raise DBError('Экспорт превышает 32 МБ / Export exceeds 32 MB')
                guard()
                return {'name':re.sub(r'[^\w .-]','_',table)[:80]+'.'+format,'text':text,'mime':'application/json' if format=='json' else 'text/csv'}
            except Exception as exc:raise DBError(str(exc)) from exc
            finally:self._finish(key,timer)
