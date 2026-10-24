"""Round 37 — small chat improvements (r37-extras).

  • Ctrl+F «Найти в этом чате» — chat-find.js: CSS Custom Highlight API (no DOM changes), counter, Enter / Shift+Enter, Esc;
  • message bookmarks — chat-find.js (MemuBookmarks): ☆ in the tools row, `chat.bookmarks`, ribbon class, ★ list, Ctrl+K provider;
  • per-chat composer drafts and ↑ — last message — chat-drafts.js;
  • Settings → Data → export / import settings — settings-transfer.js (secrets never leave).
Static checks always; browser checks (offline: Proxy + playground + mock model of tests/ui_lang_scan.Servers) with MEMU_TEST_CHROMIUM.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round37_extras_test.py
"""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _chromium() -> str | None:
    return os.environ.get("MEMU_TEST_CHROMIUM") or None


class StaticTest(unittest.TestCase):
    def test_files_wired(self):
        html = read("index.html")
        for ref in ("/chat-find.css?v=3.5-r37", "/settings-transfer.css?v=3.5-r37", "/chat-find.js?v=3.5-r37",
                    "/chat-drafts.js?v=3.5-r37", "/settings-transfer.js?v=3.5-r37"):
            self.assertIn(ref, html)
        # after app.js: they use its globals (S, renderMessages, saveLocalChats, savePreferences)
        self.assertLess(html.index("/app.js?v="), html.index("/chat-find.js?v="))

    def test_app_hooks_are_small_and_optional(self):
        app = read("app.js")
        self.assertEqual(app.count("window.MemuBookmarks ? MemuBookmarks.cls(m) : \"\"}${entrance}"), 2)
        self.assertEqual(app.count("window.MemuBookmarks ? MemuBookmarks.toolButton(i) : \"\""), 2)
        self.assertIn('settingsSlot("settings-transfer", () => MemuSettingsTransfer.html(L2, icon))', app)
        # the tool buttons stay lazy (r36): nothing new inside renderMessages' markup
        body = app[app.index("function renderMessages() {"):]
        body = body[:body.index("\nfunction ", 10)]
        self.assertNotIn("toolButton(", body)

    def test_hotkeys_registered(self):
        hk = read("hotkeys.js")
        self.assertIn('C("find_in_chat", "main", "Ctrl+F"', hk)
        self.assertIn('E("recall_last", "chat", "ArrowUp"', hk)

    def test_cheap_by_design(self):
        for name in ("chat-find.js", "chat-drafts.js", "settings-transfer.js"):
            src = read(name)
            self.assertNotIn("setInterval", src, name)
            self.assertNotIn("MutationObserver", src, name)
        find = read("chat-find.js")
        self.assertIn("CSS.highlights.set(\"memu-find\"", find)
        self.assertNotIn('createElement("mark")', find)
        self.assertNotIn("<mark class", find)
        self.assertIn('addEventListener("resize", () => { if (state.bar) requestAnimationFrame(place); }, { passive: true })', find)
        css = read("chat-find.css")
        self.assertNotIn("backdrop-filter", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_bilingual_strings(self):
        # every visible string goes through L(ru, en)/L2(ru, en)
        for name in ("chat-find.js", "settings-transfer.js"):
            src = read(name)
            self.assertGreaterEqual(src.count("L(\""), 5, name)
        self.assertIn("Find in this chat", read("chat-find.js"))
        self.assertIn("Export settings", read("settings-transfer.js"))


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
        cls.pgdata = pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru"})
        msgs = []
        for i in range(12):
            msgs.append({"role": "user", "content": f"вопрос {i} про Ёжика", "createdAt": 1_700_000_000_000 + i * 2, "id": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"## Ответ {i}\n\nПро **ежика** и картофель {i}.", "createdAt": 1_700_000_000_001 + i * 2,
                         "id": f"a{i}", "model": "gpt-test"})
        pgdata.save_chats([
            {"id": "long", "title": "Длинный", "updated": 10, "mode": "chat", "messages": msgs},
            {"id": "other", "title": "Другой", "updated": 5, "mode": "chat",
             "messages": [{"role": "user", "content": "мой вопрос в другом", "createdAt": 1, "id": "o1"},
                          {"role": "assistant", "content": "ответ", "createdAt": 2, "id": "o2"}]},
        ])
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        cls.wait(cls.page, "typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 2 && S.models && S.models.length > 0")
        cls.page.evaluate("()=>{try{closePanel()}catch(e){}}")
        cls.page.evaluate("async () => { await openChat('long'); }")
        cls.wait(cls.page, "document.querySelectorAll('#messages > .msg').length === 24")

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

    def test_1_find(self):
        self.page.click("#messages")
        self.page.keyboard.press("Control+f")
        self.wait(self.page, "!!document.querySelector('.cfind input') && document.activeElement === document.querySelector('.cfind input')")
        self.page.keyboard.type("ежик")          # ё/е folded: «Ёжика» in questions and «ежика» in answers
        self.wait(self.page, "MemuChatFind._state.ranges.length === 24")
        info = self.js("""() => ({ n: document.querySelector('.cfind-n').textContent, hl: CSS.highlights.has('memu-find'),
            cur: CSS.highlights.has('memu-find-current'), marks: document.querySelectorAll('#messages mark').length })""")
        self.assertTrue(info["hl"] and info["cur"])
        self.assertEqual(info["marks"], 0)
        self.assertRegex(info["n"], r"^\d+/24$")
        before = self.js("() => MemuChatFind._state.pos")
        self.page.keyboard.press("Enter")
        self.assertEqual(self.js("() => MemuChatFind._state.pos"), (before + 1) % 24)
        self.page.keyboard.press("Shift+Enter")
        self.assertEqual(self.js("() => MemuChatFind._state.pos"), before)
        self.page.fill(".cfind input", "нет-такого-слова")
        self.wait(self.page, "document.querySelector('.cfind').classList.contains('none')")
        self.page.keyboard.press("Escape")
        self.wait(self.page, "!document.querySelector('.cfind') && !CSS.highlights.has('memu-find')")

    def test_2_bookmarks(self):
        idx = self.js("() => S.messages.findIndex(m => m.id === 'a3')")
        self.js(f"() => MemuBookmarks.toggle({idx})")
        chat = self.js("() => { const c = S.chats.find(c => c.id === 'long'); return { b: c.bookmarks, updated: c.updated }; }")
        self.assertIn("a3", chat["b"])
        self.assertEqual(chat["updated"], 10)   # bookmarking does not bump the chat to the top
        self.assertTrue(self.js(f"() => document.querySelector('#messages > [data-msg=\"{idx}\"]').classList.contains('msg-bm')"))
        self.wait(self.page, "true")
        end = time.time() + 10
        stored = {}
        while time.time() < end:
            stored = next((c for c in self.pgdata.load_chats() if c["id"] == "long"), {})
            if (stored.get("bookmarks") or {}).get("a3"):
                break
            time.sleep(0.2)
        self.assertIn("a3", stored.get("bookmarks") or {})
        self.page.keyboard.press("Control+f")
        self.wait(self.page, "!!document.querySelector('#cfind-bm')")
        self.page.click("#cfind-bm")
        self.wait(self.page, "!document.querySelector('.cbm-pop').hidden")
        self.assertEqual(self.js("() => document.querySelectorAll('.cbm-pop [data-cbm]').length"), 1)
        self.assertIn("Ответ 3", self.js("() => document.querySelector('.cbm-pop .cbm-text').textContent"))
        self.assertNotIn("##", self.js("() => document.querySelector('.cbm-pop .cbm-text').textContent"))
        self.page.keyboard.press("Escape"); self.page.keyboard.press("Escape")
        rows = self.js("async () => { const p = MemuSearch._state.providers.get('bookmarks');"
                       " return p.search('картофель 3', { score: MemuSearch._scoreText }); }")
        self.assertTrue(any(r["_key"] == "a3" for r in rows))
        self.js(f"() => MemuBookmarks.toggle({idx})")
        self.assertFalse(self.js("() => 'bookmarks' in S.chats.find(c => c.id === 'long')"))

    def test_3_drafts_and_recall(self):
        self.js("async () => { await openChat('long'); }")
        self.page.fill("#input", "недописанный вопрос")
        self.page.wait_for_timeout(500)                  # debounced save
        self.assertEqual(self.js("() => MemuDrafts.get('long')"), "недописанный вопрос")
        self.js("async () => { await openChat('other'); }")
        self.assertEqual(self.page.input_value("#input"), "")
        # ↑ in the empty composer: the last own message of this chat
        self.page.focus("#input")
        self.page.keyboard.press("ArrowUp")
        self.assertEqual(self.page.input_value("#input"), "мой вопрос в другом")
        self.page.fill("#input", "")
        self.js("async () => { await openChat('long'); }")
        self.assertEqual(self.page.input_value("#input"), "недописанный вопрос")
        # a new empty chat takes the text along, the old chat's draft is dropped
        self.js("() => newChat()")
        self.assertEqual(self.page.input_value("#input"), "недописанный вопрос")
        self.assertEqual(self.js("() => MemuDrafts.get('long')"), "")
        self.page.fill("#input", "")
        # a draft equal to the last sent message is not restored
        self.js("() => MemuDrafts.set('other', 'мой вопрос в другом')")
        self.js("async () => { await openChat('other'); }")
        self.assertEqual(self.page.input_value("#input"), "")

    def test_4_settings_transfer(self):
        out = self.js("""() => { const prefs = { ui_density: 'compact', tavily_api_key: 'sk-x', telegram_bot_token: '1:2',
            smtp_password: 'p', profile_name: 'Me', voice_models_dir: 'C:/x', shortcuts: { search: 'Ctrl+K' } };
            return Object.keys(MemuSettingsTransfer.exportable(prefs)).sort(); }""")
        self.assertEqual(out, ["shortcuts", "ui_density"])
        self.assertIsNone(self.js("() => MemuSettingsTransfer.parse('[1,2]')"))
        self.assertIsNone(self.js("() => MemuSettingsTransfer.parse(JSON.stringify({ preferences: { a: 1 } }))"))
        diff = self.js("""() => MemuSettingsTransfer.diff({ ui_density: 'x', unknown_key: 1, tavily_api_key: 'k', language: S.preferences.language },
            { ui_density: 'y', tavily_api_key: '', language: S.preferences.language })""")
        self.assertEqual(diff, {"ui_density": "x"})
        self.js("() => { showSettings(true); }")
        self.js("() => openSettingsPage('transfer')")
        self.wait(self.page, "!!document.getElementById('tr-export-settings') && !!document.getElementById('tr-import-settings')")
        text = self.js("() => document.getElementById('tr-settings-card').innerText")
        self.assertIn("Экспортировать настройки", text)
        self.js("() => closePanel()")

    def test_5_no_page_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
