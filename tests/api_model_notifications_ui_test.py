"""API model feed and panel in a fresh, headless browser with offline fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app/module_builtins/api-models"


class ModelNotificationUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        chrome = Path(os.environ.get("MEMU_TEST_CHROMIUM", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        if not chrome.is_file():
            cls.pw.stop()
            raise unittest.SkipTest("Chromium unavailable")
        cls.browser = cls.pw.chromium.launch(executable_path=str(chrome), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_feed_retains_snapshot_without_catalogue_and_shows_text_output(self):
        for lang in ("ru", "en"):
            for width in (500, 1100):
                with self.subTest(lang=lang, width=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 800})
                    errors = []
                    page.on("pageerror", lambda err: errors.append(str(err)))
                    page.route("**/*", lambda route: route.abort())
                    event = {"id": 1, "module_id": "api-models", "title": "New API models · Fixture", "at": 1793140000,
                             "text": "  Price / 1M tokens (input → output): free → $2; output: text",
                             "models": [{"key": "a/model", "name": "New Model", "spec": {"input": 0, "output": 2, "outputs": ["text"]}}]}
                    page.set_content((MODULE / "index.html").read_text(encoding="utf-8"))
                    page.add_script_tag(content="window.fixture=" + json.dumps(event) + "; window.Memu={lang:" + json.dumps(lang) + """, saved:{}, i18n:{number:(v)=>String(v)},
                        monitor:async(p)=>p.action==='events'?{cursor:1,seen:0,events:[fixture]}:{enabled:true,interval:3600,sources:[{id:'openrouter',title:'OpenRouter'}]},
                        fetch:async()=>{throw new Error('offline')}, ready:()=>{}, on:()=>{}, onLanguage:()=>{}, save:async()=>{}, unsaved:async()=>{}};""")
                    page.add_script_tag(content=(MODULE / "main.js").read_text(encoding="utf-8"))
                    page.wait_for_selector('[data-model-key="a/model"]')
                    row = page.locator('[data-model-key="a/model"]')
                    self.assertIn("$2", row.inner_text())
                    self.assertIn("бесплатно" if lang == "ru" else "free", row.inner_text())
                    self.assertIn("текст" if lang == "ru" else "text", row.inner_text())
                    self.assertEqual(page.locator(".am-note").count(), 0)
                    self.assertEqual(errors, [])
                    page.close()

    def test_panel_does_not_call_missing_prices_free(self):
        page = self.browser.new_page()
        page.set_content('<div id="mk"></div>')
        page.add_script_tag(content="""window.Memu={lang:'en',panel:{params:{},data:async()=>({models:[
            {key:'unknown',name:'Unknown price',input:null,output:null,outputs:['text']},
            {key:'free',name:'Free model',input:0,output:0,outputs:['image']}]}),open:()=>{}}};""")
        page.add_script_tag(content=(MODULE / "integration/panels.js").read_text(encoding="utf-8"))
        page.wait_for_selector('[data-key="unknown"]')
        self.assertNotIn("free", page.locator('[data-key="unknown"]').inner_text())
        self.assertIn("text", page.locator('[data-key="unknown"]').inner_text())
        self.assertIn("free", page.locator('[data-key="free"]').inner_text())
        self.assertIn("images", page.locator('[data-key="free"]').inner_text())
        page.close()


if __name__ == "__main__":
    unittest.main()
