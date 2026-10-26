"""Specialized models must not enter text-only MemuNote/Quick Ask workflows."""
import json
import mimetypes
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from app.memunote import ai

ROOT=Path(__file__).resolve().parents[1]
SPECIAL=["image","video","music","audio","stt","tts","embedding","rerank","decisions"]
ROWS=[{"id":"text","name":"Text model","kind":"chat","caps":{"text":True,"vision":True}}, *[{"id":kind,"kind":kind,"caps":{"text":True}} for kind in SPECIAL]]

class MemuNoteModels(unittest.TestCase):
    def test_explicit_task_overrides_text_output(self):
        for row in ROWS:
            with self.subTest(kind=row["kind"]):self.assertEqual(ai._chat_model(row),row["kind"]=="chat")
        self.assertFalse(ai._chat_model({"id":"rerank","capabilities":{"kind":"rerank","text":True}}))
        self.assertTrue(ai._chat_model({"id":"audio-chat","caps":{"text":True,"audio":True}}))

    def test_discovery_preserves_only_chat_metadata(self):
        with patch.object(ai,"request_json",return_value={"models":ROWS}):
            rows=ai.models("http://127.0.0.1:8317")
        self.assertEqual([(r["id"],r["kind"]) for r in rows],[("text","chat")])

class QuickModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        chrome=Path(os.environ.get("MEMU_TEST_CHROMIUM",r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        if not chrome.is_file():cls.pw.stop();raise unittest.SkipTest("Chromium unavailable")
        cls.browser=cls.pw.chromium.launch(executable_path=str(chrome),headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()

    def check_source(self,custom):
        context=self.browser.new_context(viewport={"width":720,"height":640});page=context.new_page();errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
        prefs={"theme":"light","language":"ru","quick_ask_model":"embedding","default_model":"tts","quick_ask_advanced_model":"rerank","quick_ask_endpoint_model":"embedding","quick_ask_endpoint_advanced_model":"rerank"}
        config={"prefs":prefs,"status":{"native":True,"pinned":False},"ui":{"onboarded":True},"endpoint":{"source":"custom" if custom else "proxy","models":ROWS}}
        def route(req):
            path=urlparse(req.request.url).path
            if path=="/api/quick/config":return req.fulfill(json=config)
            if path=="/api/pg/state":return req.fulfill(json={"models":ROWS,"image_models":["image"],"video_models":["video"]})
            if path.startswith("/api/"):return req.fulfill(json={"items":[],"events":[],"seq":0,"ok":True})
            if path=="/themes.css":return req.fulfill(content_type="text/css",body="")
            file=(ROOT/"app"/"pgstatic"/path.lstrip("/")).resolve()
            if file.is_dir():file=file/"index.html"
            if not file.is_relative_to(ROOT/"app"/"pgstatic") or not file.is_file():return req.fulfill(status=404,body="")
            return req.fulfill(content_type=mimetypes.guess_type(str(file))[0] or "text/plain",body=file.read_bytes())
        try:
            page.route("**/*",route);page.goto("http://memu-kinds.test/quick/?native=1");page.wait_for_function("() => window.QuickAsk && window.MemuQuick")
            page.evaluate("QuickAsk.onShow({fresh:true,maxHeight:640})")
            page.wait_for_function("() => window.MemuQuick.models().length===1")
            self.assertEqual(page.evaluate("MemuQuick.models().map(r=>[r.id,r.kind])"),[["text","chat"]])
            self.assertEqual(page.evaluate("QuickAsk.state().model"),"text")
            self.assertEqual(errors,[])
        finally:context.close()

    def test_proxy_metadata_filters_all_specialized_tasks(self):self.check_source(False)
    def test_custom_endpoint_does_not_reinsert_configured_specialized_models(self):self.check_source(True)

if __name__=="__main__":unittest.main()
