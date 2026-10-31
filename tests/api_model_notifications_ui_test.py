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
        try:
            if chrome.is_file():
                cls.browser = cls.pw.chromium.launch(executable_path=str(chrome), headless=True)
            else:
                cls.browser = cls.pw.chromium.launch(headless=True)
        except Exception:
            cls.pw.stop()
            raise unittest.SkipTest("Chromium unavailable")

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

    SIX = [("tbc/mischief-1", 1793436000, "TBC: mischief 1"),
           ("baseten/joey-1", 1793373600, "Baseten: Joey 1"),
           ("sourceful/riverflow-v2.6-pro", 1793344800, "Sourceful: Riverflow V2.6 Pro"),
           ("nvidia/nemotron-3.5-transcribe", 1793330400, "NVIDIA: Nemotron 3.5 Transcribe"),
           ("assemblyai/assemblyai-tts-1", 1793274000, "AssemblyAI: AssemblyAI TTS 1"),
           ("particle-ai/particle-1", 1793207893, "Particle AI: Particle-1")]

    def _feed_page(self, event_models, fetch_js, sources):
        page = self.browser.new_page(viewport={"width": 1100, "height": 800})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.route("**/*", lambda route: route.abort())
        event = {"id": 1, "module_id": "api-models", "title": "New API models · OpenRouter",
                 "at": 1793440000, "text": "", "models": event_models}
        page.set_content((MODULE / "index.html").read_text(encoding="utf-8"))
        page.add_script_tag(content="window.fixture=" + json.dumps(event) + "; window.Memu={lang:'ru', saved:{}, i18n:{number:(v)=>String(v)},"
            "monitor:async(p)=>p.action==='events'?{cursor:1,seen:0,events:[window.fixture]}:{enabled:true,interval:3600,revision:'1',sources:" + json.dumps(sources) + "},"
            "fetch:" + fetch_js + ", ready:()=>{}, on:()=>{}, onLanguage:()=>{}, save:async()=>{}, unsaved:async()=>{}, models:async()=>[]};")
        page.add_script_tag(content=(MODULE / "main.js").read_text(encoding="utf-8"))
        page.wait_for_selector('[data-model-key]')
        page.wait_for_timeout(500)
        return page, errors

    def _catalog_js(self, ms_ids=()):
        rows = [{"id": key, "name": name, "created": created * 1000 if key in ms_ids else created,
                 "context_length": 1000,
                 "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                 "pricing": {"prompt": "0.001", "completion": "0.002"}}
                for key, created, name in self.SIX]
        return "async()=>({text:" + json.dumps(json.dumps({"data": rows})) + ",truncated:false})"

    def _status(self):
        return [{"id": "openrouter", "title": "OpenRouter", "last_success": 1793440000, "count": 6}]

    def test_feed_sorts_six_models_by_release_date_and_shows_catalog_update_date(self):
        # The event lists the models oldest-first with no release dates: the
        # order must come from the catalog, normalised across seconds/ms.
        event_models = [{"key": key, "name": name, "spec": {}} for key, _, name in reversed(self.SIX)]
        page, errors = self._feed_page(event_models, self._catalog_js(ms_ids={"baseten/joey-1"}), self._status())
        try:
            keys = page.eval_on_selector_all('[data-model-key]', "els => els.map(e => e.dataset.modelKey)")
            self.assertEqual(keys, [key for key, _, _ in self.SIX])
            status = page.locator("#am-catalog-status")
            self.assertIn("Даты выхода обновлены", status.inner_text())
            self.assertTrue(status.evaluate("el => el.dataset.catalogUpdated"))
            self.assertEqual(status.evaluate("el => el.dataset.catalogStale"), "false")
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_feed_marks_stale_catalog_when_fetch_fails_but_keeps_event_dates(self):
        # Catalog fetch fails: the feed must say so (no silent stale order)
        # and still sort by the release dates snapshotted in the event.
        event_models = [{"key": key, "name": name, "spec": {"created": created}}
                        for key, created, name in reversed(self.SIX)]
        page, errors = self._feed_page(event_models, "async()=>{throw new Error('catalog fetch failed')}", self._status())
        try:
            keys = page.eval_on_selector_all('[data-model-key]', "els => els.map(e => e.dataset.modelKey)")
            self.assertEqual(keys, [key for key, _, _ in self.SIX])
            status = page.locator("#am-catalog-status")
            self.assertIn("Даты выхода не загружены", status.inner_text())
            self.assertEqual(status.evaluate("el => el.dataset.catalogStale"), "true")
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_family_filter_note_says_models_reappear_when_chosen(self):
        sources = [{"id": "openrouter", "title": "OpenRouter", "last_success": 1793440000, "count": 6,
                    "families": ["google", "openai"]}]
        page, errors = self._feed_page(
            [{"key": "a/model", "name": "New Model", "spec": {}}],
            "async()=>{throw new Error('offline')}", sources)
        try:
            page.click('[data-tab="settings"]')
            # Round 74: reappearing models ARE notified again once the family
            # is chosen (module_monitor baseline fix) — the warning now says so.
            self.assertIn("появятся как новые", page.locator("#am-settings-pane").inner_text())
            self.assertEqual(errors, [])
        finally:
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
