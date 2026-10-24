# -*- coding: utf-8 -*-
"""Phase-2 regressions for the «Characters» module in Chromium (mock host from round29_characters_harness).

* a long streamed answer is rendered in batches, not once per token (the module froze on long answers);
* the untouched starter persona «Вы»/«You» is renamed to a name-like default.
"""
from __future__ import annotations

import unittest

import json

from tests import round29_characters_harness as harness
from tests.round29_characters_harness import chromium, frame_of, open_page

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and chromium(), "playwright + chromium required")
class CharactersPhase2BrowserTest(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def boot(self, store=None):
        original = harness.PARENT
        if store:  # the module must see this store on its very first load
            harness.PARENT = original.replace("window.__store = {};", "window.__store = " + json.dumps(store, ensure_ascii=False) + ";", 1)
        try:
            self.browser, self.page, self.errors = open_page(self.pw)
        finally:
            harness.PARENT = original
        self.page.wait_for_timeout(1500)
        self.frame = frame_of(self.page)
        return self.frame

    def test_long_stream_is_rendered_in_batches(self):
        frame = self.boot()
        self.page.evaluate("() => { window.__reply = 'word '.repeat(1400); }")  # 7000 chars → 1000 deltas
        frame.click("text=New character")
        frame.wait_for_selector(".ch-editor")
        frame.query_selector_all(".ch-editor .mu-input, .ch-editor .mu-textarea")[0].fill("Bob")
        frame.click(".ch-modal-foot >> text=Create")
        frame.wait_for_selector("#composer-text", timeout=8000)
        frame.evaluate("""() => { window.__renders = 0; const log = document.getElementById('messages');
          new MutationObserver((list) => { for (const m of list) if (m.target.classList && m.target.classList.contains('ch-text')) window.__renders++; })
            .observe(log, { childList: true, subtree: true }); }""")
        frame.fill("#composer-text", "Tell me a long story")
        frame.press("#composer-text", "Enter")
        for _ in range(200):
            self.page.wait_for_timeout(100)
            if not frame.evaluate("() => window.MemuCharacters.S.busy"):
                break
        self.assertFalse(frame.evaluate("() => window.MemuCharacters.S.busy"))
        last = frame.evaluate("() => { const m = window.MemuCharacters.S.chat.messages; return m[m.length - 1].mes; }")
        self.assertEqual(last.strip(), ("word " * 1400).strip())
        renders = frame.evaluate("() => window.__renders")
        self.assertLess(renders, 400, "every token re-rendered the whole message")
        self.assertGreater(renders, 3, "streaming must still show progress")

    def test_old_starter_persona_is_renamed(self):
        frame = self.boot({"personas": [{"id": "p1", "name": "Вы", "description": "", "avatar": "", "position": 0, "depth": 2, "role": 0}],
                           "settings": {"personaId": "p1"}})
        self.assertEqual(frame.evaluate("() => window.MemuCharacters.S.personas[0].name"), "User")
        self.assertEqual(self.page.evaluate("() => window.__store.personas[0].name"), "User")

    def test_edited_persona_is_kept(self):
        frame = self.boot({"personas": [{"id": "p1", "name": "You", "description": "A traveller", "avatar": "", "position": 0, "depth": 2, "role": 0}],
                           "settings": {"personaId": "p1"}})
        self.assertEqual(frame.evaluate("() => window.MemuCharacters.S.personas[0].name"), "You")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(sync_playwright and chromium(), "playwright + chromium required")
class CharactersPhase2HelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.errors = open_page(cls.pw)
        cls.page.wait_for_timeout(1500)
        cls.frame = frame_of(cls.page)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_short_model_names(self):
        f = self.frame
        self.assertEqual(f.evaluate("() => MemuCharacters.shortModel('ydx-gpt://b1g/deepseek-v4.1-flash/latest')"), "deepseek-v4.1-flash")
        self.assertEqual(f.evaluate("() => MemuCharacters.shortModel('qwen/qwen3.8-max:free')"), "qwen3.8-max:free")
        self.assertEqual(f.evaluate("() => MemuCharacters.shortModel('gpt-4o')"), "gpt-4o")

    def test_stale_pending_answers_are_cleaned(self):
        out = self.frame.evaluate("""() => MemuCharacters.dropStalePending({ messages: [
            { id: 'a', role: 'user', mes: 'hi' },
            { id: 'b', role: 'assistant', mes: 'partial answer', pending: true },
            { id: 'c', role: 'assistant', mes: '', pending: true, swipes: ['old', ''], swipe_id: 1, swipe_info: [{ model: 'm' }, {}] },
            { id: 'd', role: 'assistant', mes: '', reasoning: 'x', pending: true, swipes: [''], swipe_id: 0 } ] }).messages""")
        self.assertEqual([m["id"] for m in out], ["a", "b", "c"])
        self.assertTrue(all("pending" not in m for m in out))
        self.assertEqual(out[2]["mes"], "old")
        self.assertEqual(out[2]["swipes"], ["old"])
        self.assertEqual(out[2]["swipe_id"], 0)

    def test_sent_text_is_not_restored_as_draft(self):
        f = self.frame
        if not f.query_selector("#composer-text"):
            f.click("text=New character")
            f.wait_for_selector(".ch-editor")
            f.query_selector_all(".ch-editor .mu-input, .ch-editor .mu-textarea")[0].fill("Dana")
            f.click(".ch-modal-foot >> text=Create")
            f.wait_for_selector("#composer-text", timeout=8000)
        f.fill("#composer-text", "draft then send")
        f.dispatch_event("#composer-text", "input")
        self.assertEqual(f.evaluate("() => MemuCharacters.S.chat.meta.draft"), "draft then send")
        f.press("#composer-text", "Enter")
        for _ in range(80):
            self.page.wait_for_timeout(100)
            if not f.evaluate("() => MemuCharacters.S.busy"):
                break
        self.assertEqual(f.evaluate("() => MemuCharacters.S.chat.meta.draft"), "")
