"""Round 37 — small chat improvements, second wave (r37-extras-2).

  • «Сравнение ответов» — compare.js: versions of one answer side by side (⇆ in the version switcher of branches.js),
    «Выбрать этот ответ» switches the fork, «Ответ другой моделью…» = retryAnswerAt(index, model);
  • «;сниппеты» and template blanks {?name|hint} in the main composer — composer-snippets.js (the Quick Access library
    /api/quick/features), the «Сниппеты и шаблоны» dialog, Ctrl+K provider «Сниппеты»;
  • Ctrl+F options «Учитывать регистр» (Alt+C) and «Слово целиком» (Alt+W) — chat-find.js.
Static checks always; browser checks (offline: Proxy + playground + mock model of tests/ui_lang_scan.Servers) with MEMU_TEST_CHROMIUM.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round37_extras2_test.py
"""
from __future__ import annotations

import os
import re
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")

def _chromium() -> str | None:
    return os.environ.get("MEMU_TEST_CHROMIUM") or None

SNIPPETS = [
    {"id": "t1", "keyword": "привет", "name": "Приветствие", "kind": "text",
     "text": "Здравствуйте, {?имя|как обращаться}! Встреча {?когда}.{cursor} С уважением, {?имя}"},
    {"id": "t2", "keyword": "дата", "name": "Сегодня", "kind": "text", "text": "Сегодня {{важно}}"},
    {"id": "t3", "keyword": "кратко", "name": "Короче", "kind": "ai", "text": "Сократи текст вдвое."},
]

def seed(pgdata, quick_features):
    pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru"})
    quick_features.save_config(snippets=SNIPPETS)
    pgdata.save_chats([
        {"id": "find", "title": "Поиск", "updated": 9, "mode": "chat", "messages": [
            {"role": "user", "content": "Где мой ёжик?", "createdAt": 1, "id": "f1"},
            {"role": "assistant", "content": "Ежик спит. ЕЖИК проснётся, а ежики играют.", "createdAt": 2, "id": "f2", "model": "gpt-test"}]},
        {"id": "cmp", "title": "Сравнение", "updated": 10, "mode": "chat",
         "messages": [
             {"role": "user", "content": "Назови столицу Франции", "createdAt": 10, "id": "u0"},
             {"role": "assistant", "content": "Вторая версия: **Париж** — столица Франции.", "createdAt": 30, "id": "a0b", "model": "gpt-test"}],
         "branches": {"v": 1, "forks": {"u0": {"sel": 1, "alts": [
             {"id": "v1", "at": 20, "m": [{"role": "assistant", "content": "Первая версия: Париж.", "createdAt": 20, "id": "a0a", "model": "model-b"}]},
             {"id": "v2", "at": 30, "m": None}]}}}},
    ])

class StaticTest(unittest.TestCase):
    def test_files_wired(self):
        html = read("index.html")
        for ref in ("/compare.css?v=3.5-r37", "/composer-snippets.css?v=3.5-r37", "/compare.js?v=3.5-r37",
                    "/composer-snippets.js?v=3.5-r37", "/branches.js?v=3.5-r37"):
            self.assertIn(ref, html)
        self.assertLess(html.index("/branches.js?v="), html.index("/compare.js?v="))
        self.assertLess(html.index("/app.js?v="), html.index("/composer-snippets.js?v="))

    def test_hooks_are_small_and_optional(self):
        app = read("app.js")
        self.assertIn('async function retryAnswerAt(index, modelOverride = "")', app)
        self.assertIn("const model = modelOverride || currentModel() || S.messages[index]?.model;", app)
        self.assertEqual(app.count("window.MemuCompare ? MemuCompare.menuItem(i) : \"\""), 1)
        br = read("branches.js")
        self.assertIn('role === "assistant" && window.MemuCompare ? window.MemuCompare.switchButton(i) : ""', br)

    def test_cheap_by_design(self):
        for name in ("compare.js", "composer-snippets.js", "chat-find.js"):
            src = read(name)
            self.assertNotIn("setInterval", src, name)
            self.assertNotIn("MutationObserver", src, name)
        snip = read("composer-snippets.js")

        self.assertIn("if (!state.open || e.isComposing", snip)
        self.assertNotRegex(snip, r"\n  load\(\);")
        cmp = read("compare.js")
        self.assertIn("{ passive: true }", cmp)
        for css in ("compare.css", "composer-snippets.css"):
            self.assertNotIn("backdrop-filter", read(css), css)

    def test_bilingual(self):
        for name, words in (("compare.js", ("Compare answers", "Use this answer", "Answer with another model")),
                            ("composer-snippets.js", ("Snippets and templates", "Fill in the template", "Insert")),
                            ("chat-find.js", ("Match case", "Whole word"))):
            src = read(name)
            for w in words:
                self.assertIn(w, src, name)
        self.assertGreaterEqual(read("compare.js").count("L(\""), 12)
        self.assertGreaterEqual(read("composer-snippets.js").count("L(\""), 25)

    def test_server_expand_keeps_blanks(self):
        from app.quick_features import expand
        out = expand("Привет, {?имя|подсказка}! {{x}} {cursor}конец", lang="ru")
        self.assertEqual(out["text"], "Привет, {?имя|подсказка}! {x} конец")
        self.assertEqual(out["cursor"], len("конец"))

@unittest.skipUnless(_chromium(), "MEMU_TEST_CHROMIUM is not set")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        from tests.ui_lang_scan import Servers
        cls.servers = Servers().start()
        from app import pgdata, quick_features
        seed(pgdata, quick_features)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        cls.wait(cls.page, "typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 2 && S.models && S.models.length > 0")
        cls.page.evaluate("() => { try { closePanel() } catch (e) {} localStorage.removeItem('memu_find_opts'); }")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    @staticmethod
    def wait(page, code, timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if page.evaluate(code):
                    return
            except Exception:
                pass
            page.wait_for_timeout(120)
        raise AssertionError(f"timed out: {code}")

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def test_1_find_options(self):
        self.js("async () => { await openChat('find'); }")
        self.wait(self.page, "document.querySelectorAll('#messages > .msg').length === 2")
        self.page.click("#messages")
        self.page.keyboard.press("Control+f")
        self.wait(self.page, "!!document.querySelector('.cfind input')")
        self.page.keyboard.type("ежик")
        self.wait(self.page, "MemuChatFind._state.ranges.length === 4")
        self.page.keyboard.press("Alt+c")
        self.wait(self.page, "MemuChatFind._state.ranges.length === 1")
        self.assertEqual(self.js("document.querySelector('[data-cf=\"cs\"]').getAttribute('aria-pressed')"), "true")
        self.page.click("[data-cf='cs']")
        self.page.click("[data-cf='word']")
        self.wait(self.page, "MemuChatFind._state.ranges.length === 3")
        self.assertEqual(self.js("MemuChatFind.options()"), {"cs": False, "word": True})
        self.assertEqual(self.js("JSON.parse(localStorage.getItem('memu_find_opts'))"), {"cs": False, "word": True})
        self.page.keyboard.press("Escape")
        self.wait(self.page, "!document.querySelector('.cfind')")
        self.js("() => localStorage.removeItem('memu_find_opts')")

    def test_2_snippet_with_fields(self):
        self.js("async () => { await openChat('find'); }")
        self.page.fill("#input", "")
        self.page.focus("#input")
        self.page.keyboard.type(";при")
        self.wait(self.page, "!document.getElementById('snip-menu')?.hidden && document.querySelectorAll('#snip-menu [data-snip]').length === 1")
        self.assertIn("Приветствие", self.js("document.querySelector('#snip-menu [data-snip]').textContent"))
        self.page.keyboard.press("Enter")
        self.wait(self.page, "document.querySelectorAll('.snip-form input').length === 2")
        self.assertEqual(self.js("document.querySelector('.snip-form input').placeholder"), "как обращаться")
        self.assertEqual(self.js("S.messages.length"), 2)
        self.page.keyboard.type("Анна")
        self.page.keyboard.press("Enter")
        self.page.keyboard.type("завтра")
        self.page.keyboard.press("Enter")
        self.wait(self.page, "!document.querySelector('.snip-form')")
        value = self.page.input_value("#input")
        self.assertEqual(value, "Здравствуйте, Анна! Встреча завтра. С уважением, Анна")
        caret = self.js("document.getElementById('input').selectionStart")
        self.assertEqual(value[:caret], "Здравствуйте, Анна! Встреча завтра.")

        self.page.fill("#input", "Итак ;дат")
        self.page.focus("#input")
        self.page.keyboard.press("End")
        self.page.keyboard.type("а")
        self.wait(self.page, "!document.getElementById('snip-menu')?.hidden")
        self.page.keyboard.press("Tab")
        self.wait(self.page, "document.getElementById('input').value === 'Итак Сегодня {важно}'")

        self.page.fill("#input", "")
        self.page.keyboard.type(";крат")
        self.wait(self.page, "!document.getElementById('snip-menu')?.hidden")
        self.page.keyboard.press("Escape")
        self.wait(self.page, "document.getElementById('snip-menu').hidden")
        self.assertEqual(self.page.input_value("#input"), ";крат")
        self.page.keyboard.type("к")
        self.wait(self.page, "!document.getElementById('snip-menu')?.hidden")
        self.page.keyboard.press("Enter")
        self.wait(self.page, "document.getElementById('input').value === 'Сократи текст вдвое.\\n\\n'")

        self.page.fill("#input", "```\nfoo();\nx ;при")
        self.js("() => document.getElementById('input').dispatchEvent(new Event('input'))")
        self.page.wait_for_timeout(300)
        self.assertTrue(self.js("document.getElementById('snip-menu').hidden"))
        self.page.fill("#input", "")

    def test_3_snippet_helpers_and_dialog(self):
        f = self.js("() => MemuComposerSnippets.fields('A {?x} B {?Y|подсказка} {?x} {date}')")
        self.assertEqual(f, [{"name": "x", "hint": ""}, {"name": "Y", "hint": "подсказка"}])
        self.assertEqual(self.js("() => MemuComposerSnippets.fill('A {?x} {?y|h}', {x: '1'})"), "A 1 {?y|h}")
        self.js("() => MemuComposerSnippets.manage()")
        self.wait(self.page, "document.querySelectorAll('#panel .snip-row').length === 3")
        self.page.click("#panel [data-snip-new]")
        self.wait(self.page, "!!document.querySelector('#snip-kw')")
        self.page.fill("#snip-kw", "письмо")
        self.page.fill("#snip-name", "Письмо")
        self.page.fill("#snip-text", "Тема: {?тема}")
        self.page.click("#panel [data-snip-save]")
        self.wait(self.page, "document.querySelectorAll('#panel .snip-row').length === 4")
        from app import quick_features
        rows = quick_features.load_config("ru")["snippets"]
        self.assertTrue(any(r["keyword"] == "письмо" and r["text"] == "Тема: {?тема}" for r in rows))
        self.page.click("#panel .snip-row:last-child [data-snip-del]")
        self.wait(self.page, "document.querySelectorAll('#panel .snip-row').length === 3")
        self.js("() => closePanel()")

        rows = self.js("async () => { const p = MemuSearch._state.providers.get('snippets');"
                       " return p.search('приветствие', { score: MemuSearch._scoreText }); }")
        self.assertTrue(any(r.get("_id") == "t1" for r in rows))

    def test_4_compare(self):
        self.js("async () => { await openChat('cmp'); }")
        self.wait(self.page, "document.querySelectorAll('#messages > .msg').length === 2")
        self.wait(self.page, "!!document.querySelector('#messages .br-switch [data-cmp=\"1\"]')")
        self.page.click("#messages .br-switch [data-cmp='1']")
        self.wait(self.page, "document.querySelectorAll('#panel .cmp-col').length === 2")
        cols = self.js("""() => [...document.querySelectorAll('#panel .cmp-col')].map(c => ({ active: c.classList.contains('cmp-active'),
            text: c.querySelector('.cmp-body').textContent.trim(), pick: !!c.querySelector('[data-cmp-pick]') }))""")
        self.assertEqual([c["active"] for c in cols], [False, True])
        self.assertIn("Первая версия", cols[0]["text"])
        self.assertIn("Париж", cols[1]["text"])
        self.assertNotIn("**", cols[1]["text"])
        self.assertEqual([c["pick"] for c in cols], [True, False])
        self.page.click("#panel [data-cmp-pick='0']")
        self.wait(self.page, "!document.querySelector('#panel-back.visible') && S.messages[1].id === 'a0a'")
        self.assertEqual(self.js("document.querySelector('.br-switch .br-count').textContent"), "1/2")

        self.js("() => { window.__retry = null; window.__origRetry = retryAnswerAt; window.retryAnswerAt = (i, m) => { window.__retry = [i, m]; }; }")
        try:
            self.js("() => materializeMessageTools(document.querySelector('#messages > [data-msg=\"1\"]'))")
            self.wait(self.page, "!!document.querySelector('#messages [data-cmp-model=\"1\"]')")
            self.js("() => MemuCompare.askModel(1, null)")
            self.wait(self.page, "Array.isArray(window.__retry)")
            got = self.js("window.__retry")
            self.assertEqual(got[0], 1)
            self.assertTrue(got[1])
        finally:
            self.js("() => { window.retryAnswerAt = window.__origRetry; }")

        self.js("() => MemuCompare.open(1)")
        self.wait(self.page, "!!document.querySelector('#panel [data-cmp-another]')")
        self.js("() => closePanel()")

    def test_9_no_page_errors(self):
        self.assertEqual([e for e in self.errors if not re.search(r"ResizeObserver|Failed to fetch", e)], [])

if __name__ == "__main__":
    unittest.main()
