"""Round 36 — chat features of round 35 (branches, canvas, @-mentions, ask_user, big documents): bugs and speed.

Static checks (no browser): no polling timers / permanent capture listeners in the chat modules, the chat-switch event.
Browser checks (offline, the mock model from tests/ui_lang_scan.Servers):
  • «memu:chat-open» fires once per chat switch, not per render; the canvas launcher follows it (no 500 ms poll);
  • ask_user: every field type renders, a chip click repaints only the form (renderMessages is not called), Skip and
    Done settle the tool result, a pending form survives a reload;
  • @-mentions: the token is found at the end of a long draft and not in the middle of a word.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round36_chat_test.py
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")

def _chromium() -> str | None:
    return os.environ.get("MEMU_TEST_CHROMIUM") or None

class StaticTest(unittest.TestCase):
    def test_no_polling_in_chat_modules(self):
        for name in ("canvas.js", "askuser.js", "bigdocs.js", "mentions.js", "branches.js"):
            self.assertNotIn("setInterval(", read(name), name)

    def test_bigdocs_scroll_listener_only_while_tip(self):
        src = read("bigdocs.js")
        self.assertNotIn('window.addEventListener("scroll", () => hideTip(), true)', src)
        self.assertIn('window.addEventListener("scroll", hideTip, { capture: true, passive: true })', src)
        self.assertIn('window.removeEventListener("scroll", hideTip, true)', src)

    def test_chat_open_event(self):
        app = read("app.js")
        body = app[app.index("function renderMessages() {"):][:600]
        self.assertIn('new CustomEvent("memu:chat-open"', body)
        self.assertIn('document.addEventListener("memu:chat-open", onChatMaybeChanged)', read("canvas.js"))

    def test_mentions_lazy_providers(self):
        src = read("mentions.js")
        init = src[src.index("  function init() {"):]
        self.assertNotIn("    loadProviders();", init)
        self.assertIn("caret - 320", src)

    def test_cache_versions_bumped(self):
        html = read("index.html")
        for name in ("askuser.js", "bigdocs.js", "mentions.js"):
            self.assertRegex(html, re.escape(name) + r"\?v=3\.5-r36", name)
        for name in ("canvas.js", "canvas.css"):
            self.assertRegex(html, re.escape(name) + r"\?v=3\.6-r40", name)

@unittest.skipUnless(_chromium(), "MEMU_TEST_CHROMIUM is not set")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        from tests.ui_lang_scan import Servers
        from app import pgdata
        cls.servers = Servers().start()
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        msg = lambda role, text, at: {"role": role, "content": text, "createdAt": 1_700_000_000_000 + at, "id": f"m-{role}-{at}"}
        pgdata.save_chats([
            {"id": "c-a", "title": "A", "updated": 2, "mode": "chat", "messages": [msg("user", "привет", 1), msg("assistant", "здравствуйте", 2)]},
            {"id": "c-b", "title": "B", "updated": 1, "mode": "chat", "messages": [msg("user", "вопрос", 3), msg("assistant", "ответ", 4)]},
        ])
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))

        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        for _ in range(300):
            if cls.page.evaluate("typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 2 && !!window.MemuAskUser"):
                break
            cls.page.wait_for_timeout(200)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    def js(self, code):
        return self.page.evaluate(code)

    def wait_js(self, code, timeout=30.0):
        """page.wait_for_function needs eval, which the app's CSP refuses — poll with evaluate instead."""
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.page.evaluate(code):
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(150)
        raise AssertionError(f"timed out: {code}")

    def test_1_chat_open_event_once_per_switch(self):
        n = self.js("""async () => {
          let n = 0; const on = () => { n += 1; };
          document.addEventListener('memu:chat-open', on);
          await openChat('c-a'); renderMessages(); renderMessages();
          await openChat('c-b'); renderMessages();
          document.removeEventListener('memu:chat-open', on);
          return n; }""")
        self.assertEqual(n, 2)

    def ask(self, form):
        self.page.evaluate("""(form) => { window.__auResult = null;
          MemuAskUser.ask(S.messages[1], form, { chatId: S.currentId, messages: S.messages, mode: 'chat' })
            .then((r) => { window.__auResult = r; }); }""", form)
        self.page.wait_for_selector(".au-pending", timeout=10000)

    def result(self):
        self.wait_js("window.__auResult !== null", 10)
        return self.js("window.__auResult")

    def test_2_ask_user_all_fields_skip_and_done(self):
        self.js("openChat('c-a')")
        self.page.wait_for_timeout(300)

        self.ask({"question": "Пара уточнений", "fields": [
            {"id": "style", "type": "choice", "label": "Стиль", "options": ["Строгий", "Дружеский"], "default": "Дружеский", "allow_other": True},
            {"id": "tags", "type": "multi", "label": "Темы", "options": ["Цены", "Сроки", "Риски"]},
            {"id": "when", "type": "date", "label": "Когда"}]})
        types = self.js("[...document.querySelectorAll('.au-pending .au-field')].map(f => [...f.classList].find(c => c.startsWith('au-t-')))")
        self.assertEqual(types, ["au-t-choice", "au-t-multi", "au-t-date"])

        self.js("() => { window.__renders = 0; window.__origRender = renderMessages;"
                "renderMessages = function () { window.__renders += 1; return window.__origRender.apply(this, arguments); }; }")
        self.page.click(".au-pending [data-au-multi] >> nth=1")
        self.page.click(".au-pending [data-au-day] >> nth=1")
        renders = self.js("() => { const n = window.__renders; renderMessages = window.__origRender; return n; }")
        self.assertEqual(renders, 0)
        self.assertEqual(self.js("document.querySelectorAll('.au-pending [data-au-multi].on').length"), 1)
        self.page.click(".au-pending .au-submit")
        res = self.result()
        self.assertEqual(res["status"], "answered")
        self.assertEqual(res["answers"]["tags"], ["Сроки"])
        self.assertEqual(res["answers"]["style"], "Дружеский")
        self.assertRegex(res["answers"]["when"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(self.js("!!document.querySelector('.au-done.au-s-answered')"))
        self.ask({"question": "Ещё", "fields": [
            {"id": "budget", "type": "number", "label": "Бюджет", "min": 0, "max": 100, "unit": "₽"},
            {"id": "ok", "type": "bool", "label": "Срочно?"},
            {"id": "note", "type": "text", "label": "Комментарий"}]})
        self.page.click(".au-pending .au-step[data-au-dir='1']")
        self.page.click(".au-pending .au-t-bool .au-chip >> nth=0")
        self.page.fill(".au-pending .au-text", "без спешки")
        self.assertEqual(self.js("document.querySelector('.au-pending .au-number').value"), "1")
        self.page.click(".au-pending .au-submit")
        res = self.result()
        self.assertEqual((res["answers"]["ok"], res["answers"]["note"], float(res["answers"]["budget"])), (True, "без спешки", 1.0))

        self.ask({"question": "Третий", "fields": [{"id": "x", "type": "text", "label": "X"}]})
        self.page.click(".au-pending .au-skip")
        self.assertEqual(self.result()["status"], "skipped")

    def test_3_mentions_token_in_long_draft(self):
        got = self.js("""() => {
          const el = document.querySelector('#input'); el.focus();
          el.value = 'x'.repeat(5000) + ' @чат: дог'; el.selectionStart = el.selectionEnd = el.value.length;
          const a = MemuMentions.owns();
          el.value = 'a'.repeat(400) + 'mail@host'; el.selectionStart = el.selectionEnd = el.value.length;
          const b = MemuMentions.owns();
          el.value = ''; return [a, b]; }""")
        self.assertEqual(got, [True, False])

    def test_4_pending_form_survives_reload(self):
        self.js("openChat('c-b')")
        self.page.wait_for_timeout(300)
        self.page.evaluate("""() => { MemuAskUser.ask(S.messages[1], {question: 'После перезагрузки', fields: [
            {id: 'c', type: 'choice', label: 'Вариант', options: ['Один', 'Два']}]},
            { chatId: S.currentId, messages: S.messages, mode: 'chat' }); }""")
        self.page.wait_for_selector(".au-pending", timeout=10000)
        self.page.wait_for_timeout(1500)
        self.page.reload(wait_until="load")
        self.wait_js("typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 2 && !!window.MemuAskUser", 60)
        self.js("openChat('c-b')")
        self.page.wait_for_selector(".au-pending", timeout=15000)
        self.assertIn("После перезагрузки", self.js("document.querySelector('.au-pending').innerText"))
        self.assertEqual([e for e in self.errors if "localStorage" not in e], [])

if __name__ == "__main__":
    unittest.main()
