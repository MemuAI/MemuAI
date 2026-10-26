"""Round 36 — main window speed, third wave (r36-perf-3).

Static checks:
  • no `:has()` that searches descendants on the chat's ancestors / <body> (every element inserted into the chat
    re-checked them): body classes set by JS instead (temp-guide-on, project-mode, chat-empty, code-dock-expanded),
    `.menu-open` on a message instead of `.msg:has(.msg-more.open)`, sibling selectors instead of `:has(input) span`;
  • no full-window backdrop blur under dialogs (classic and glass) — it was recomposited on every scrolled frame;
  • settings sections use content-visibility; the panels 1 s tick uses live class collections;
  • grid's square-corner floor has no `.katex *` / `svg *` inside :not().
Browser checks (offline: Proxy + playground of tests/ui_lang_scan.Servers):
  • the body classes follow the real state (home → chat → temporary chat → Work);
  • inserting one element into a long chat costs about as much as with no :has() rules at all.
Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=… python -m pytest -q tests/round36_perf3_test.py
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

def _css_without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)

class StaticTest(unittest.TestCase):
    CHAT_CSS = ("panels.css", "panels-layout.css", "style.css", "memucode.css", "appearance.css", "design.css",
                "designs/glass.css", "branches.css")

    def test_no_descendant_has_on_chat_ancestors(self):
        for name in self.CHAT_CSS:
            css = _css_without_comments(read(name))
            with self.subTest(name=name):
                self.assertNotIn("body:has(", css)
                self.assertNotRegex(css, r":has\([^)]*#temp-guide")
                self.assertNotRegex(css, r":has\([^)]*#code-dock")
                self.assertNotRegex(css, r":has\([^)]*\.msg-more")
                self.assertNotRegex(css, r":has\(> \.chat-view-wrap")
                self.assertNotRegex(css, r":has\(> #chat\.(work|code)-mode")

                self.assertNotRegex(css, r":has\(input[^)]*\)\)?\s*>?\s*span")

    def test_js_sets_the_classes(self):
        app = read("app.js")
        self.assertIn('document.body.classList.toggle("temp-guide-on", temp);', app)
        self.assertIn('document.body.classList.toggle("project-mode", work);', app)
        self.assertIn('document.body.classList.toggle("chat-empty", empty);', app)
        self.assertIn('wrap.closest(".msg")?.classList.add("menu-open")', app)
        self.assertIn('#messages > .msg:is(:last-child, :focus-within, .menu-open)', read("style.css"))
        self.assertIn('document.body.classList.toggle("code-dock-expanded", state.dockExpanded);', read("memucode.js"))
        self.assertIn("body.code-mode.code-dock-expanded:not(.code-dock-hidden) #main-content", read("memucode.css"))
        self.assertIn("body > :is(.mp-modal-back, .mp-pop) ~ .pln-pop", read("panels.css"))

    def test_no_dialog_backdrop_blur(self):
        design = _css_without_comments(read("design.css"))
        rule = re.search(r"\n\.panel-back \{[^}]*\}", design).group(0)
        self.assertIn("backdrop-filter: none", rule)
        glass = _css_without_comments(read("designs/glass.css"))
        for sel in (':root[data-design="glass"] .panel-back {', ':root[data-design="glass"] .panel {'):
            body = glass[glass.index(sel):]
            body = body[:body.index("}")]
            self.assertIn("backdrop-filter: none", body, sel)

    def test_settings_sections_content_visibility(self):
        self.assertRegex(read("settings.css"), r":root \.sx-shell \.sx-section \{ content-visibility: auto; contain-intrinsic-size: auto \d+px;")

    def test_panels_tick_uses_live_collections(self):
        js = read("panels.js")
        self.assertIn('document.getElementsByClassName("mp-countdown")', js)
        self.assertNotIn('$$q("[data-mp-countdown]")', js)
        self.assertNotIn('$$q("[data-mp-clock]")', js)
        self.assertIn("if (!liveCd.length && !liveClock.length && !liveChip.length && !state.timers?.snap) return;", js)

    def test_grid_square_corner_floor_is_cheap(self):
        grid = _css_without_comments(read("designs/grid.css"))
        self.assertNotIn(".katex *", grid)
        self.assertNotIn("svg *", grid)
        self.assertIn(':root[data-design="grid"] :where(.katex, svg) :where(*) { border-radius: 0; }', grid)

    def test_single_universal_scrollbar_rule(self):
        style = _css_without_comments(read("style.css"))
        self.assertNotRegex(style, r"(^|\n)\* \{ scrollbar-width")
        self.assertRegex(_css_without_comments(read("appearance.css")), r"\n\* \{ scrollbar-width: thin;")

    def test_versions(self):
        index = read("index.html")
        for ref in ("/style.css?v=1.9-r41", "/design.css?v=3.4-r36", "/panels.css?v=1.5-r36", "/panels-layout.css?v=3.6-r36",
                    "/memucode.css?v=2.4-r36", "/settings.css?v=1.4-r36", "designs/glass.css?v=3.2-r36", "designs/grid.css?v=1.2-r36",
                    "/app.js?v=1.30-r41", "/memucode.js?v=2.6-r36"):
            self.assertIn(ref, index)

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
        for i in range(120):
            msgs.append({"role": "user", "content": f"вопрос {i}", "createdAt": 1_700_000_000_000 + i * 2, "id": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"## Ответ {i}\n\n- пункт\n- пункт\n\nтекст ответа {i} " * 3,
                         "createdAt": 1_700_000_000_001 + i * 2, "id": f"a{i}", "model": "gpt-test"})
        pgdata.save_chats([{"id": "long", "title": "Длинный", "updated": 10, "mode": "chat", "messages": msgs}])
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820}, bypass_csp=True)
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        cls.wait(cls.page, "typeof S !== 'undefined' && Array.isArray(S.chats) && S.chats.length >= 1 && !document.body.classList.contains('booting')")
        cls.page.evaluate("()=>{try{closePanel()}catch(e){}}")

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
            page.wait_for_timeout(150)
        raise AssertionError(f"timed out: {code}")

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def test_1_body_classes_follow_state(self):
        self.wait(self.page, "document.body.classList.contains('chat-empty')")
        self.js("()=>openChat('long')")
        self.wait(self.page, "document.querySelectorAll('#messages > .msg').length > 100")
        self.assertFalse(self.js("()=>document.body.classList.contains('chat-empty')"))
        self.assertFalse(self.js("()=>document.querySelector('#chat-view-wrap').classList.contains('empty-chat')"))
        self.js("()=>newChat(true)")
        self.wait(self.page, "document.body.classList.contains('temp-guide-on') && document.body.classList.contains('chat-empty')")
        self.assertFalse(self.js("()=>document.querySelector('#temp-guide').hidden"))
        home = self.js("()=>{const h=document.querySelector('#panels-home'); return h ? getComputedStyle(h).display : 'none'}")
        self.assertEqual(home, "none")
        self.js("()=>newChat(false)")
        self.wait(self.page, "!document.body.classList.contains('temp-guide-on')")
        self.assertTrue(self.js("()=>document.querySelector('#temp-guide').hidden"))
        self.assertEqual(self.js("()=>document.body.classList.contains('project-mode')"),
                         self.js("()=>document.querySelector('#chat').classList.contains('work-mode')"))
        self.assertEqual(self.errors, [])

    def test_2_insert_is_cheap(self):
        self.js("()=>openChat('long')")
        self.wait(self.page, "document.querySelectorAll('#messages > .msg').length > 100")
        self.page.wait_for_timeout(800)
        probe = """(n)=>{const body=document.querySelector('#messages > .msg:last-child .body')||document.querySelector('#messages > .msg:last-child'); const ts=[];
          for(let k=0;k<n;k++){const p=document.createElement('p'); p.textContent='probe'; const t=performance.now(); body.appendChild(p); void document.body.offsetHeight; ts.push(performance.now()-t); p.remove(); void document.body.offsetHeight}
          ts.sort((a,b)=>a-b); return ts[ts.length>>1]}"""
        with_has = self.js(probe, 15)

        self.js("""()=>{const walk=(list)=>{for(let i=list.length-1;i>=0;i--){const r=list[i]; if(r.cssRules&&!r.selectorText) walk(r.cssRules);
            else if(r.selectorText&&r.selectorText.includes(':has(')) (r.parentRule||r.parentStyleSheet).deleteRule(i);}};
            for(const sh of document.styleSheets){try{walk(sh.cssRules)}catch(e){}}}""")
        floor = self.js(probe, 15)
        self.assertLess(with_has, floor * 2.5 + 1.0, f"insert {with_has:.2f} ms vs floor {floor:.2f} ms")

if __name__ == "__main__":
    unittest.main()
