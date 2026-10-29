"""Round 36 — speed of the main window's JS (r36-perf-js-2).

Static checks: no `:has(.br-switch)` (it restyled the whole chat on every inserted element), the message tool buttons are
not in the message markup any more (built lazily), the capture scroll listener of the pickers is passive and idle,
the update status poll is not a fixed 1.5 s interval, the chat search is debounced, the tooltip checks clipping only
once the pointer rests, panels-layout re-measures only the scrollers a scroll can move.
Browser checks (offline: Proxy + playground + the mock model of tests/ui_lang_scan.Servers):
  • older messages carry only the shell of their tools row; hover builds the buttons, keyboard focus on the shell
    builds them and moves focus to the first one; the last message has them at once;
  • a real streamed answer (mock model) ends with its buttons and text, with few full renders;
  • typing a word in the chat search re-renders the sidebar once.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round36_perf_js_test.py
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

class StaticTest(unittest.TestCase):
    def test_branches_css_without_has(self):
        css = read("branches.css")
        self.assertNotIn(":has(.br-switch)", css)
        self.assertIn(".msg-tools.has-br", css)
        self.assertIn('" has-br" : ""', read("app.js"))

    def test_message_tools_are_lazy(self):
        app = read("app.js")
        body = app[app.index("function renderMessages() {"):]
        body = body[:body.index("\nfunction ", 10)]
        self.assertNotIn('<button class="msg-tool"', body)
        self.assertIn("lazyMessageTools(brInfo, i)", body)
        self.assertIn("materializeMessageTools(box.lastElementChild)", body)
        self.assertIn('messagesBox?.addEventListener("focusin", wake)', app)

    def test_scroll_listeners_cheap(self):
        app = read("app.js")
        self.assertIn('document.getElementsByClassName("pretty-select open")', app)
        self.assertIn("}, { capture: true, passive: true });", app)
        self.assertNotIn('document.addEventListener("scroll", (event) => {\n  if (!event.target.closest?.(".pretty-select-menu")) closePrettySelects();\n}, true);', app)
        self.assertIn('if (!$("#load-more-chats")) return;', app)
        self.assertIn("t.contains(el)", read("panels-layout.js"))

    def test_polls(self):
        app = read("app.js")
        self.assertNotIn("setInterval(refreshUpdateStatus", app)
        self.assertIn("setTimeout(pollUpdateStatus, busy ? 1500 : 60_000)", app)
        self.assertIn("refreshState().catch(() => {}); }, 6000)", app)

    def test_search_debounced_and_indexed(self):
        app = read("app.js")
        self.assertIn("chatSearchTimer = setTimeout(", app)
        self.assertIn("chatSearchText(c).includes(q)", app)

    def test_tooltip_clip_check_deferred(self):
        src = read("tooltip.js")
        self.assertIn("candidate(start, true)", src)
        self.assertIn("CLIP_WAIT", src)

    def test_cache_versions(self):
        html = read("index.html")
        for asset in ("/app.js", "/tooltip.js", "/branches.css"):
            self.assertRegex(html, re.escape(asset) + r'\?v=[^"\s]+"')
        from app.version import APP_VERSION
        self.assertIn(f'/app.js?v={APP_VERSION}"', html)

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
        msgs = []
        for i in range(40):
            msgs.append({"role": "user", "content": f"вопрос {i}", "createdAt": 1_700_000_000_000 + i * 2, "id": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"## Ответ {i}\n\nтекст ответа {i} " * 3, "createdAt": 1_700_000_000_001 + i * 2,
                         "id": f"a{i}", "model": "gpt-test"})
        chats = [{"id": "long", "title": "Длинный", "updated": 10, "mode": "chat", "messages": msgs}]
        chats += [{"id": f"c{i}", "title": f"Чат {i} про котов" if i % 3 == 0 else f"Чат {i}", "updated": 1, "mode": "chat",
                   "messages": [{"role": "user", "content": f"привет {i}", "createdAt": 1}]} for i in range(30)]
        pgdata.save_chats(chats)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        cls.wait_js_static(cls.page, "typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 30 && S.models && S.models.length > 0")
        cls.page.evaluate("()=>{try{closePanel()}catch(e){}}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    @staticmethod
    def wait_js_static(page, code, timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if page.evaluate(code):
                    return
            except Exception:
                pass
            page.wait_for_timeout(150)
        raise AssertionError(f"timed out: {code}")

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def test_1_lazy_tools(self):
        self.js("async () => { await openChat('long'); }")
        self.wait_js_static(self.page, "document.querySelectorAll('#messages > .msg').length === 80")
        counts = self.js("""() => { const msgs = [...document.querySelectorAll('#messages > .msg')];
          return { older: msgs.slice(0, -1).reduce((n, m) => n + m.querySelectorAll('.msg-tool').length, 0),
                   shells: document.querySelectorAll('#messages .msg-tools[data-tools-lazy][tabindex="0"]').length,
                   last: msgs[msgs.length - 1].querySelectorAll('.msg-tool').length }; }""")
        self.assertEqual(counts["older"], 0)
        self.assertEqual(counts["shells"], 79)
        self.assertEqual(counts["last"], 4)

        for _ in range(10):
            self.js("() => document.querySelector('#messages > .msg-user[data-msg=\"10\"]').scrollIntoView({block: 'center', behavior: 'instant'})")
            self.page.wait_for_timeout(300)
            box = self.js("() => { const r = document.querySelector('#messages > .msg-user[data-msg=\"10\"] .bubble').getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }")
            if self.js("(p) => document.elementFromPoint(p.x, p.y)?.closest('.msg')?.dataset.msg", box) == "10":
                break
        self.page.mouse.move(box["x"], box["y"])
        self.page.mouse.move(box["x"] + 2, box["y"] + 1)
        self.page.wait_for_timeout(400)

        self.assertEqual(self.js("document.querySelectorAll('#messages > [data-msg=\"10\"] .msg-tool').length"), 5)
        self.js("() => renderMessages()")
        self.assertEqual(self.js("document.querySelectorAll('#messages > [data-msg=\"10\"] .msg-tool').length"), 5)

        self.js("() => document.querySelector('#messages > [data-msg=\"21\"] .msg-tools[data-tools-lazy]').focus()")
        state = self.js("""() => ({ tools: document.querySelectorAll('#messages > [data-msg="21"] .msg-tool').length,
          active: document.activeElement.classList.contains('msg-tool') && !!document.activeElement.closest('[data-msg="21"]'),
          more: !!document.querySelector('#messages > [data-msg="21"] .msg-more-pop [data-branch="21"]') })""")
        self.assertEqual(state, {"tools": 4, "active": True, "more": True})

        self.js("() => { window.__copied = null; window.__origCopy = copyText; copyText = (t) => { window.__copied = t; }; }")
        self.js("() => document.querySelector('#messages > [data-msg=\"21\"] .msg-tool').click()")
        self.assertIn("текст ответа 10", self.js("window.__copied") or "")
        self.js("() => { copyText = window.__origCopy; }")

    def test_2_streamed_answer(self):
        self.js("async () => { await openChat('long'); }")
        self.js("""() => { window.__renders = 0; const orig = renderMessages; window.__origRender = orig;
          renderMessages = function () { window.__renders += 1; return orig.apply(this, arguments); }; }""")
        try:
            self.page.fill("#input", "Скажи что-нибудь")
            self.page.click("#btn-send")
            self.wait_js_static(self.page, "S.messages.length >= 82 && !S.messages[S.messages.length - 1].streaming && !generationJobs.size", 60)
            res = self.js("""() => { const last = document.querySelector('#messages > .msg:last-child');
              return { text: last.querySelector('.body').textContent.trim().length, tools: last.querySelectorAll('.msg-tool').length,
                       prevShell: !!document.querySelector('#messages > .msg:nth-last-child(2) .msg-tools[data-tools-lazy]'),
                       renders: window.__renders, error: S.messages[S.messages.length - 1].error || '' }; }""")
        finally:
            self.js("() => { renderMessages = window.__origRender; }")
        self.assertEqual(res["error"], "")
        self.assertGreater(res["text"], 0)
        self.assertEqual(res["tools"], 4)
        self.assertTrue(res["prevShell"])
        self.assertLess(res["renders"], 25)

    def test_3_search_debounced(self):
        self.js("() => { window.__rc = 0; const orig = renderChats; window.__origRC = orig; renderChats = function () { window.__rc += 1; return orig.apply(this, arguments); }; }")
        try:
            self.page.fill("#chat-search", "")
            self.page.wait_for_timeout(300)
            self.js("() => { window.__rc = 0; }")
            self.page.type("#chat-search", "котов", delay=20)
            self.page.wait_for_timeout(500)
            rc = self.js("window.__rc")
            titles = self.js("[...document.querySelectorAll('#chats-list .ci-title')].map((n) => n.textContent)")
        finally:
            self.page.fill("#chat-search", "")
            self.page.wait_for_timeout(300)
            self.js("() => { renderChats = window.__origRC; }")
        self.assertEqual(rc, 1)
        self.assertTrue(titles and all("котов" in t for t in titles), titles)

        self.js("() => { const c = S.chats.find((x) => x.id === 'c1'); c.messages.push({ role: 'assistant', content: 'Уникальное СЛОВО-маркер', createdAt: 2 }); }")
        self.page.type("#chat-search", "слово-маркер", delay=5)
        self.page.wait_for_timeout(400)
        titles = self.js("[...document.querySelectorAll('#chats-list .ci-title')].map((n) => n.textContent)")
        self.page.fill("#chat-search", "")
        self.assertEqual(titles, ["Чат 1"])

    def test_9_no_page_errors(self):
        self.assertEqual([e for e in self.errors if "ResizeObserver" not in e], [])

if __name__ == "__main__":
    unittest.main()
