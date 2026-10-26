"""MemuDB acceptance: isolated SQLite files, actual transactions and ASGI."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memudb import Workspace, DBError, statements
from app import memudb_api

OWNER=("default","memudb")

class Database(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/"sample.sqlite"
        conn=sqlite3.connect(self.path)
        conn.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, balance REAL DEFAULT 0);INSERT INTO customers VALUES(1,'Alice',25),(2,'Bob',50);CREATE INDEX customer_name ON customers(name);")
        conn.close()
        self.workspace=Workspace();self.session=self.workspace.open(str(self.path),OWNER)["id"]

    def tearDown(self):
        self.workspace.close_all();self.temp.cleanup()

    def writable(self):
        self.workspace.configure(self.session,OWNER,False,confirmed=True)

    def values(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:return conn.execute("SELECT * FROM customers ORDER BY id").fetchall()

    def test_open_schema_browse_parameterization_and_owner(self):
        schema=self.workspace.schema(self.session,OWNER)
        table=schema["tables"][0]
        self.assertEqual(table["name"],"customers")
        self.assertTrue(table["indexes"])
        result=self.workspace.browse(self.session,OWNER,"customers",sort="name",descending=True)
        self.assertEqual(result["rows"][0][1],"Bob")
        self.assertEqual(self.workspace.browse(self.session,OWNER,"customers",search="%' OR 1=1 --")["total"],0)
        with self.assertRaises(DBError):self.workspace.schema(self.session,("other","memudb"))
        with self.assertRaises(DBError):self.workspace.browse(self.session,OWNER,'customers"; DROP TABLE customers;--')

    def test_read_api_cannot_write_or_escape_to_filesystem(self):
        for sql in ["UPDATE customers SET balance=0", "PRAGMA user_version=42", "ATTACH DATABASE ':memory:' AS outside", "SELECT load_extension('x')", "BEGIN", "VACUUM"]:
            with self.subTest(sql=sql),self.assertRaises(DBError):self.workspace.query(self.session,OWNER,sql)
        self.assertEqual(self.values()[0][2],25)
        self.assertEqual(self.workspace.query(self.session,OWNER,"SELECT name FROM customers WHERE id=?",[1])["rows"],[["Alice"]])
        self.assertEqual(self.workspace.query(self.session,OWNER,"SELECT 1; -- trailing comment")["rows"],[[1]])

    def test_preview_never_changes_original_confirmed_apply_and_backup(self):
        with self.assertRaises(DBError):self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=100 WHERE id=1"}])
        with self.assertRaises(DBError):self.workspace.configure(self.session,OWNER,False)
        self.writable()
        preview=self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=? WHERE id=? RETURNING *","parameters":[100,1]}])
        self.assertEqual(self.values()[0][2],25)
        self.assertEqual(preview["affected"],1)
        with self.assertRaises(DBError):self.workspace.apply(self.session,OWNER,preview["preview_id"])
        applied=self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True)
        self.assertEqual(self.values()[0][2],100)
        with closing(sqlite3.connect(applied["backup"])) as conn, conn:self.assertEqual(conn.execute("SELECT balance FROM customers WHERE id=1").fetchone()[0],25)
        with self.assertRaises(DBError):self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True)

    def test_partial_error_and_discard_never_write(self):
        self.writable()
        with self.assertRaises(DBError):self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=20"},{"sql":"INSERT INTO missing VALUES(1)"}])
        self.assertEqual(self.values()[0][2],25)
        preview=self.workspace.preview(self.session,OWNER,[{"sql":"DELETE FROM customers"}])
        self.workspace.discard(self.session,OWNER,preview["preview_id"])
        with self.assertRaises(DBError):self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True)
        self.assertEqual(len(self.values()),2)

    def test_external_change_invalidates_sealed_preview(self):
        self.writable();preview=self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=999 WHERE id=1"}])
        with closing(sqlite3.connect(self.path)) as conn, conn:conn.execute("UPDATE customers SET balance=13 WHERE id=1")
        with self.assertRaisesRegex(DBError,"changed"):self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True)
        self.assertEqual(self.values()[0][2],13)

    def test_profile_guard_before_commit_rolls_back(self):
        self.writable();preview=self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=888 WHERE id=1"}])
        count=0
        def guard():
            nonlocal count
            count+=1
            if count>1:raise DBError("Profile changed")
        with self.assertRaises(DBError):self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True,guard=guard)
        self.assertEqual(self.values()[0][2],25)

    def test_row_table_and_csv_operations_are_previews(self):
        self.writable()
        table=self.workspace.table_preview(self.session,OWNER,"Заметки",[{"name":"id","type":"INTEGER","pk":True},{"name":"text","type":"TEXT"}])
        self.assertEqual(table["schema_changes"][0]["name"],"Заметки")
        self.workspace.apply(self.session,OWNER,table["preview_id"],confirmed=True)
        preview=self.workspace.row_preview(self.session,OWNER,"customers",{"balance":99},{"id":1})
        self.workspace.apply(self.session,OWNER,preview["preview_id"],confirmed=True)
        self.assertEqual(self.values()[0][2],99)
        with self.assertRaises(DBError):self.workspace.row_preview(self.session,OWNER,"customers",{}, {},action="delete")
        imported=self.workspace.csv_preview(self.session,OWNER,"customers","id,name,balance\n3,Carol,125\n4,David,200")
        self.assertEqual(imported["affected"],2)
        self.assertEqual(len(self.values()),2)
        self.workspace.apply(self.session,OWNER,imported["preview_id"],confirmed=True)
        self.assertEqual(len(self.values()),4)

    def test_long_returning_results_count_every_changed_row(self):
        self.writable()
        with closing(sqlite3.connect(self.path)) as conn, conn:conn.executemany("INSERT INTO customers VALUES(?,?,0)",[(i,f"Person {i}") for i in range(3,103)])
        preview=self.workspace.preview(self.session,OWNER,[{"sql":"UPDATE customers SET balance=1 RETURNING id"}])
        self.assertEqual(preview["affected"],102)
        self.assertTrue(preview["statements"][0]["truncated"])
        self.assertEqual(len(preview["statements"][0]["rows"]),50)

    def test_exports_preserve_long_values_and_escape_csv_formulas(self):
        value="x"*20000
        with closing(sqlite3.connect(self.path)) as conn, conn:conn.execute("INSERT INTO customers VALUES(3,?,0)",(value,));conn.execute("INSERT INTO customers VALUES(4,'=1+1',0)")
        result=self.workspace.export(self.session,OWNER,table="customers",format="json")
        self.assertEqual(json.loads(result["text"])[2]["name"],value)
        csv=self.workspace.export(self.session,OWNER,table="customers",format="csv")["text"]
        self.assertIn("'=1+1",csv)
        sql=self.workspace.export(self.session,OWNER,format="sql")["text"]
        restored=sqlite3.connect(":memory:");restored.executescript(sql)
        self.assertEqual(restored.execute("SELECT name FROM customers WHERE id=3").fetchone()[0],value);restored.close()

    def test_cancellation_interrupts_actual_running_sql(self):
        def run():return self.workspace.query(self.session,OWNER,"WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1000000000) SELECT SUM(n) FROM x",opid="long")
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(run)
            for _ in range(1000):
                with self.workspace.lock:
                    if (self.session,"long") in self.workspace.operations:break
                time.sleep(.001)
            self.assertTrue(self.workspace.cancel(self.session,OWNER,"long")["cancelled"])
            with self.assertRaises(DBError):future.result(timeout=3)
        self.assertEqual(self.workspace.query(self.session,OWNER,"SELECT 1")["rows"],[[1]])

    def test_sql_split_handles_string_semicolons_and_trigger_bodies(self):
        sql="CREATE TRIGGER t AFTER UPDATE ON customers BEGIN INSERT INTO customers(name) VALUES('a;b'); END; SELECT 1; -- comment"
        self.assertEqual(len(statements(sql)),2)

class API(unittest.TestCase):
    setUp=Database.setUp
    tearDown=Database.tearDown
    def test_native_selection_permissions_and_expected_profile(self):
        app=FastAPI();memudb_api.register(app,workspace=self.workspace,picker=lambda create:str(self.path),allowed=lambda module:module=="memudb")
        with patch.object(memudb_api.pgdata,"load_profiles",return_value={"active_id":"default"}),TestClient(app) as client:
            self.assertEqual(client.post("/api/pg/memudb/call",json={"op":"connection.list"}).status_code,403)
            self.assertEqual(client.post("/api/pg/memudb/call",json={"module_id":"memudb","profile_id":"other","op":"connection.list"}).status_code,409)
            result=client.post("/api/pg/memudb/call",json={"module_id":"memudb","op":"connection.open","args":{"path":"C:/secret.sqlite"}}).json()["result"]
            self.assertEqual(result["path"],str(self.path))
            self.assertTrue(result["readonly"])
            response=client.post("/api/pg/memudb/call",json={"module_id":"memudb","op":"query.read","args":{"connection":self.session,"sql":"DELETE FROM customers"}})
            self.assertEqual(response.status_code,400)

if __name__=="__main__":unittest.main()
