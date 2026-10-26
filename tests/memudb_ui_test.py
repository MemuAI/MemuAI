"""Actual module document/bridge with an isolated SQLite/ASGI host in Chromium."""
from __future__ import annotations
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import modules_host, memudb_api
from app.memudb import Workspace

ROOT=Path(__file__).resolve().parents[1]

class MemuDBUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        chrome=Path(os.environ.get("MEMU_TEST_CHROMIUM",r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        if not chrome.is_file():cls.pw.stop();raise unittest.SkipTest("Chromium unavailable")
        cls.browser=cls.pw.chromium.launch(executable_path=str(chrome),headless=True)
        cls.output=ROOT/"build"/"memudb-ui";cls.output.mkdir(parents=True,exist_ok=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/"customers.sqlite"
        conn=sqlite3.connect(self.path);conn.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,balance REAL);INSERT INTO customers VALUES(1,'Alice',25),(2,'Bob',50);");conn.close()
        self.workspace=Workspace();app=FastAPI();memudb_api.register(app,workspace=self.workspace,picker=lambda create:str(self.path),allowed=lambda module:module=="memudb")
        self.profile=patch.object(memudb_api.pgdata,"load_profiles",return_value={"active_id":"default"});self.profile.start()
        self.client=TestClient(app);self.client.__enter__();self.calls=[];self.ai=[];self.saved={};self.errors=[]
        self.context=self.browser.new_context(viewport={"width":1200,"height":850},reduced_motion="reduce")
        self.page=self.context.new_page();self.page.on("pageerror",lambda e:self.errors.append(str(e)))
        self.page.route("http://**/*",lambda route:route.abort());self.page.route("https://**/*",lambda route:route.abort())
        self.page.expose_function("__host",self.host)
        package=modules_host.load_package(self.root/"modules","memudb")
        html=modules_host.module_document(package,env={"lang":"ru","theme":"light"})
        host="""<script>window.addEventListener('message',async event=>{const m=event.data;if(m?.type!=='memu-module')return;try{const result=await window.__host(m);if(m.action==='stream')window.postMessage({type:'memu-module-event',id:m.id,event:'done',result},'*');else window.postMessage({type:'memu-module-result',id:m.id,result},'*');}catch(e){window.postMessage({type:'memu-module-result',id:m.id,error:String(e)},'*');}});</script>"""
        self.page.set_content(html.replace("<head>","<head>"+host,1))
        self.page.wait_for_function("() => document.querySelector('#status').textContent.includes('Откройте')")

    def host(self,message):
        action,payload=message["action"],message.get("payload",{})
        if action=="database_call":
            args={**payload.get("args",{})};op=payload["op"];self.calls.append((op,args))
            if op in {"transaction.apply","connection.configure"}:args["confirmed"]=True
            response=self.client.post("/api/pg/memudb/call",json={"module_id":"memudb","op":op,"args":args})
            data=response.json()
            if response.status_code!=200:raise ValueError(data.get("error"))
            return data["result"]
        if action=="models":return [{"id":"test-text","name":"Test model","kind":"chat","default":True},{"id":"embedding","kind":"embedding"}]
        if action=="save":self.saved=payload;return {"ok":True}
        if action=="stream":self.ai.append(payload);return {"text":json.dumps({"sql":"SELECT name FROM customers ORDER BY id LIMIT 10","explanation":"Клиенты по номеру","mode":"read"})}
        if action=="download":return {"name":payload.get("name")}
        return {"ok":True}

    def tearDown(self):
        self.context.close();self.client.__exit__(None,None,None);self.profile.stop();self.workspace.close_all();self.temp.cleanup();self.assertEqual(self.errors,[])

    def open(self):
        self.page.locator("#welcome-open").click();self.page.wait_for_selector("#data-grid tbody tr");self.assertIn("Alice",self.page.locator("#data-grid").inner_text())

    def test_pagehide_flushes_sql_before_the_debounce_deadline(self):
        self.page.evaluate("""() => {
          const schedule = window.setTimeout;
          window.setTimeout = (fn, delay, ...args) => delay === 500 ? 0 : schedule(fn, delay, ...args);
          window.addEventListener('message', e => {
            if (e.data?.type === 'memu-module' && e.data.action === 'save') window.__flushedSQL = e.data.payload.sql;
          });
          const input = document.querySelector('#sql');
          input.value = 'SELECT 74';
          input.dispatchEvent(new Event('input', {bubbles:true}));
          window.dispatchEvent(new Event('pagehide'));
        }""")
        self.page.wait_for_function("() => window.__flushedSQL === 'SELECT 74'", timeout=2000)

    def test_browse_search_schema_query_and_language(self):
        self.open();self.assertEqual(self.page.locator("#mode").inner_text(),"Только чтение")
        self.page.locator("#search").fill("Bob");self.page.wait_for_function("() => document.querySelector('#data-grid').innerText.includes('Bob')&&!document.querySelector('#data-grid').innerText.includes('Alice')")
        self.page.locator("#tab-schema").click();self.assertIn("PRIMARY KEY",self.page.locator("#schema").inner_text())
        self.page.locator("#tab-sql").click();self.page.locator("#sql").fill("SELECT name FROM customers WHERE id=?");self.page.locator("#panel-sql summary").click();self.page.locator("#parameters").fill("[1]");self.page.locator("#run").click();self.page.wait_for_selector("#query-result tbody tr")
        self.assertEqual(self.page.locator("#query-result tbody td").inner_text(),"Alice")
        self.page.evaluate("window.postMessage({type:'memu-module-host',action:'language',payload:{lang:'en'}},'*')")
        self.page.wait_for_function("() => document.querySelector('#run').textContent==='Run read query'")
        self.page.screenshot(path=str(self.output/"sql-light.png"))

    def test_write_preview_cancel_and_apply_actual_database(self):
        self.open();self.page.locator("#mode").click();self.page.wait_for_function("() => document.querySelector('#mode').textContent.includes('разрешена')")
        self.page.locator("#tab-sql").click();self.page.locator("#sql").fill("UPDATE customers SET balance=100 WHERE id=1 RETURNING *")
        self.page.locator("#preview").click();self.page.wait_for_selector("#dialog[open]")
        with closing(sqlite3.connect(self.path)) as conn:self.assertEqual(conn.execute("SELECT balance FROM customers WHERE id=1").fetchone()[0],25)
        self.assertIn("100",self.page.locator("#dialog-body").inner_text());self.page.screenshot(path=str(self.output/"preview-light.png"))
        self.page.locator("#dialog-submit").click();self.page.wait_for_function("() => !document.querySelector('#dialog').open")
        with closing(sqlite3.connect(self.path)) as conn:self.assertEqual(conn.execute("SELECT balance FROM customers WHERE id=1").fetchone()[0],100)
        self.assertEqual(len(list((self.root/".memudb-backups").glob("*.sqlite"))),1)

    def test_ai_schema_only_and_explicit_samples_never_execute(self):
        self.open();self.page.locator("#tab-ai").click();self.page.locator("#ai-request").fill("Найди клиентов")
        self.page.locator("#ai-go").click();self.page.wait_for_selector("#ai-use:not([hidden])")
        self.assertEqual(len(self.ai),1);self.assertNotIn("Alice",self.ai[0]["text"]);self.assertEqual(self.page.locator("#model option").count(),2)
        reads=sum(op=="query.read" for op,args in self.calls)
        self.page.locator("#ai-use").click();self.assertIn("SELECT name",self.page.locator("#sql").input_value())
        self.assertEqual(sum(op=="query.read" for op,args in self.calls),reads)
        self.assertTrue(self.page.locator("#panel-ai").is_visible());self.page.locator("#ai-samples").check();self.page.locator("#ai-go").click();self.page.wait_for_function("() => !document.querySelector('#ai-go').disabled")
        self.assertIn("Alice",self.ai[1]["text"])

    def test_ai_followup_context_clear_and_side_panel_keep_query_visible(self):
        self.open();self.page.locator('#tab-ai').click()
        self.page.locator('.db-model-picker summary').click()
        self.page.locator('.db-model-picker input').fill('Test')
        self.assertEqual(self.page.locator('[data-db-model]').count(),1)
        self.page.keyboard.press('ArrowDown');self.page.keyboard.press('Enter')
        self.assertEqual(self.page.locator('#model').input_value(),'test-text')
        self.page.locator('#ai-request').fill('Find customers');self.page.locator('#ai-go').click()
        self.page.wait_for_selector('#ai-use:not([hidden])')
        self.page.locator('#ai-request').fill('Sort the previous query by name');self.page.locator('#ai-go').click()
        self.page.wait_for_function("() => !document.querySelector('#ai-go').disabled")
        self.assertIn('Find customers',self.ai[1]['text'])
        self.assertIn('SELECT name FROM customers',self.ai[1]['text'])
        self.assertTrue(self.page.locator('#data-grid').is_visible())
        self.page.screenshot(path=str(self.output/'assistant-light.png'))
        self.page.locator('#ai-clear').click()
        self.assertEqual(self.page.locator('#ai-conversation').inner_text(),'')
        self.page.locator('#ai-request').fill('Explain the schema');self.page.locator('#ai-go').click()
        self.page.wait_for_function("() => !document.querySelector('#ai-go').disabled")
        self.assertNotIn('Find customers',self.ai[2]['text'])

    def test_narrow_layout_no_body_overflow_and_keyboard_tabs(self):
        self.open();self.page.set_viewport_size({"width":460,"height":760})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth<=innerWidth"))
        self.page.locator("#tab-data").focus();self.page.keyboard.press("ArrowRight");self.assertEqual(self.page.locator("#tab-sql").get_attribute("aria-selected"),"true")
        self.page.screenshot(path=str(self.output/"sql-narrow.png"))
        self.page.locator('#tab-ai').click()
        self.assertGreaterEqual(self.page.locator('#ai-request').bounding_box()['height'],80)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    def test_duckdb_real_file_browse_row_preview_apply_and_backup(self):
        import duckdb
        self.path=self.root/'customers.duckdb'
        conn=duckdb.connect(str(self.path));conn.execute('CREATE TABLE customers(id INTEGER PRIMARY KEY,name VARCHAR,balance DOUBLE)')
        conn.execute("INSERT INTO customers VALUES(1,'Alice',25),(2,'Bob',50)");conn.close()
        self.open();self.assertIn('DuckDB',self.page.locator('#engine').inner_text())
        self.assertNotIn('undefined',self.page.locator('#engine').inner_text())
        self.page.locator('#mode').click();self.page.wait_for_function("() => document.querySelector('#mode').getAttribute('aria-pressed')==='true'")
        self.page.locator('[data-edit="0"]').click();self.page.locator('[data-column="2"]').fill('77')
        self.page.locator('#dialog-submit').click()
        self.page.wait_for_function("() => document.querySelector('#dialog-title').textContent==='Просмотр изменений'")
        self.page.locator('#dialog-submit').click();self.page.wait_for_function("() => !document.querySelector('#dialog').open")
        self.page.locator('#close').click();self.page.wait_for_selector('#welcome:visible')
        conn=duckdb.connect(str(self.path),read_only=True)
        try:self.assertEqual(conn.execute('SELECT balance FROM customers WHERE id=1').fetchone()[0],77)
        finally:conn.close()
        self.assertEqual(len(list((self.root/'.memudb-backups').glob('*.duckdb'))),1)

    def test_table_builder_and_row_editor_require_preview(self):
        self.open();self.page.locator("#mode").click();self.page.wait_for_function("() => document.querySelector('#mode').textContent.includes('разрешена')")
        self.page.locator('[data-edit="0"]').click();self.page.locator('[data-column="2"]').fill("90");self.page.locator("#dialog-submit").click()
        self.page.wait_for_function("() => document.querySelector('#dialog-title').textContent==='Просмотр изменений'")
        self.page.locator("#dialog-submit").click();self.page.wait_for_function("() => !document.querySelector('#dialog').open")
        with closing(sqlite3.connect(self.path)) as conn:self.assertEqual(conn.execute("SELECT balance FROM customers WHERE id=1").fetchone()[0],90)
        self.page.locator("#new-table").click();self.page.locator("#new-table-name").fill("notes");self.page.locator("#dialog-submit").click()
        self.page.wait_for_function("() => document.querySelector('#dialog-title').textContent==='Просмотр изменений'")
        self.assertIn("notes",self.page.locator("#dialog-body").inner_text());self.page.locator('#dialog button[value="cancel"]').last.click()
        self.page.wait_for_function("() => !document.querySelector('#dialog').open")
        with closing(sqlite3.connect(self.path)) as conn:self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_schema WHERE name='notes'").fetchone())

if __name__=="__main__":unittest.main()
