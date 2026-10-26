"""Round 31 (design-main): the full design audit of the main MemuAI window — fixes and the new tour / tips / «?» help.

Static part (always runs):
* tour.js / tour.css / polish-r31.css are hooked into index.html after app.js / after the designs, with cache versions;
* every English string of tour.js has no Cyrillic, every Russian one has an English pair; no emoji glyphs in tour.js;
* app.js: «/plan» has a Russian title, the app menu has «Тур по интерфейсу», Settings → General mounts the tips section,
  no 📄/📂/💻 emoji in message file chips / the Work file list / the preview device buttons;
* tooltip.js does not show a hint over a control whose menu is open; polish-r31.css holds the folder / app-menu fixes.

Browser part (real servers + headless Chromium; skipped without Playwright / Chromium):
* chats inside a sidebar folder are no wider than the sidebar (they used to overflow and be cut without «…»);
* the ⋯ app menu fits in a 1280×720 window and reaches its last item («Справка»);
* markdown tables honour «|---:|» (right-aligned numeric columns);
* the tour walks through its steps with the keyboard and closes on Esc, the welcome card and the hints show on demand,
  the «?» menu opens / closes, Settings → General has «Подсказки и обучение» and its switch turns hints off;
* automatic welcome / hints never appear in an automated browser (navigator.webdriver) — they must not break other tests;
* the error strip of a failed answer offers «Повторить» and «Другая модель».

    python -m pytest tests/round31_design_main_test.py -q
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r31-design-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402

CYR = re.compile(r"[А-Яа-яЁё]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u26FF]")

def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")

class StaticHooks(unittest.TestCase):
    def test_files_are_hooked_with_cache_versions(self):
        html = read("index.html")
        for ref in ("/tour.js?v=", "/tour.css?v=", "/polish-r31.css?v="):
            self.assertIn(ref, html)
        self.assertLess(html.index("/app.js?v="), html.index("/tour.js?v="))
        self.assertLess(html.index("/designs/brutal.css"), html.index("/polish-r31.css?v="))
        self.assertLess(html.index("/settings.css?v="), html.index("/tour.css?v="))
        self.assertLess(html.index("/tour.css?v="), html.index('id="memu-user-theme"'))

    def test_tour_strings_have_both_languages(self):
        src = read("tour.js")
        pairs = re.findall(r'\bL2?\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"', src)
        pairs += re.findall(r"\bL2?\(\s*`((?:[^`\\]|\\.)*)`\s*,\s*`((?:[^`\\]|\\.)*)`", src)
        self.assertGreater(len(pairs), 40)
        for ru, en in pairs:
            self.assertFalse(CYR.search(en), f"Cyrillic in the English string: {en[:80]}")
            self.assertTrue(CYR.search(ru) or ru == en or not re.search(r"[A-Za-z]{3}", ru) or ru in ("Do For You", "MemuNote"), f"not Russian: {ru[:80]}")
        self.assertFalse(EMOJI.search(src), "emoji in tour.js (no emoji font in WebView2 → «▯»)")

    def test_tour_css_uses_tokens_only(self):
        css = read("tour.css") + read("polish-r31.css")
        self.assertFalse(re.search(r"#[0-9a-fA-F]{6}\b", css), "six-digit colours: use theme tokens")
        self.assertIn("prefers-reduced-motion", read("tour.css"))
        self.assertIn('data-motion="off"', read("tour.css"))

    def test_app_js_hooks_and_fixes(self):
        app = read("app.js")
        self.assertIn('["/plan", { ru: "Режим плана", en: "Plan mode" }', app)
        self.assertNotIn('"Plan mode включён"', app)
        self.assertIn('["Тур по интерфейсу", "", "tour"]', app)
        self.assertIn('tour:"Interface tour"', app)
        self.assertIn('window.MemuTour?.start()', app)
        self.assertIn('settingsSlot("tour", () => MemuTour.settingsHtml({ K, L2 }))', app)
        self.assertIn('settingsSlot("tour.bind", () => MemuTour.bindSettings())', app)
        self.assertNotIn('">📄 ${esc(a.name', app)
        self.assertNotIn('📂 ${memeText("Выбрать папку"', app)
        self.assertNotIn('f.dir ? "📁" : "📄"', app)
        self.assertIn("PREV_DEV_ICONS", app)
        self.assertIn('class="continue-strip error-strip"', app)
        self.assertIn('class="code-intro-more"', app)
        self.assertIn('$("#model-current").textContent = m ? topLabel(m.id)', app)

        pane = app[app.index('id="pane-chat"'):app.index('id: "settings-context"')]
        self.assertLess(pane.index('id: "settings-assistant"'), pane.index('settingsSlot("schedsend"'))

    def test_tooltip_respects_open_menus(self):
        self.assertIn("function menuOpen(node)", read("tooltip.js"))
        self.assertIn("menuOpen(node)) return;", read("tooltip.js"))

    def test_polish_css_fixes(self):
        css = read("polish-r31.css")
        self.assertIn(".chat-folder-items { grid-template-columns: minmax(0, 1fr); }", css)
        self.assertRegex(css, r"\.app-menu-pop \{ max-height: calc\(100vh - \d+px\); overflow-y: auto;")

@unittest.skipUnless(_has_playwright() and _chromium(), "Playwright / Chromium not installed")
class LiveWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        cls.servers = Servers().start()
        cls.http = httpx.Client(timeout=30, trust_env=False)
        cls.http.post(cls.servers.pg + "/api/pg/preferences", json={
            "profile_onboarded": True, "profile_name": "Alex", "language": "ru", "updates_enabled": False, "ui_motion": "off",
            "chat_folders": [{"id": "f_work", "name": "Работа"}]})
        now = int(time.time() * 1000)
        long_title = "Скрипт для переименования фотографий по дате съёмки и месту"
        table = "| Город | Население, млн |\n|---|---:|\n| Москва | 13,1 |\n| Казань | 1,3 |"
        cls.http.post(cls.servers.pg + "/api/pg/chats", json={"chats": [
            {"id": "c_f", "title": long_title, "created": now - 5000, "updated": now - 4000, "mode": "chat", "folder_id": "f_work",
             "messages": [{"role": "user", "content": "x", "ts": now / 1000 - 10},
                          {"role": "assistant", "content": "Таблица:\n\n" + table, "model": "demo", "ts": now / 1000 - 9}]},
            {"id": "c_err", "title": "Ошибка", "created": now - 3000, "updated": now - 2000, "mode": "chat",
             "messages": [{"role": "user", "content": "привет", "ts": now / 1000 - 8},
                          {"role": "assistant", "content": "", "error": "429 Too Many Requests", "model": "demo", "ts": now / 1000 - 7}]},
        ]})
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.http.close()
        cls.servers.stop()

    def open(self, width=1280, height=800, lang="ru"):
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (_) {{}}")
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.wait_for_timeout(600)
        return page

    def test_folder_chats_fit_the_sidebar(self):
        page = self.open()
        res = page.evaluate("""() => { const it = document.querySelector('.chat-folder .chat-item'); const box = it.closest('.side-scroll');
            const t = it.querySelector('.ci-title'); return { right: it.getBoundingClientRect().right, edge: box.getBoundingClientRect().right,
            ellipsis: getComputedStyle(t).textOverflow, cut: t.scrollWidth > t.clientWidth }; }""")
        self.assertLessEqual(res["right"], res["edge"] + 0.5)
        self.assertTrue(res["cut"])
        self.assertEqual(res["ellipsis"], "ellipsis")

    def test_app_menu_fits_a_small_window(self):
        page = self.open(1280, 720)
        page.click("#bar-menu")
        page.wait_for_selector("#app-menu-pop.visible")
        res = page.evaluate("""() => { const m = document.querySelector('#app-menu-pop'); const r = m.getBoundingClientRect();
            m.scrollTop = m.scrollHeight; const last = [...m.querySelectorAll('button')].pop().getBoundingClientRect();
            return { bottom: r.bottom, vh: innerHeight, lastBottom: last.bottom, tour: !!m.querySelector('[data-app-action="tour"]') }; }""")
        self.assertLessEqual(res["bottom"], res["vh"])
        self.assertLessEqual(res["lastBottom"], res["vh"])
        self.assertTrue(res["tour"])

    def test_markdown_table_alignment(self):
        page = self.open()
        page.evaluate("openChat('c_f')")
        page.wait_for_selector(".msg table td")
        aligns = page.evaluate("() => [...document.querySelectorAll('.msg table tr:first-child td, .msg table th')].map((c) => getComputedStyle(c).textAlign)")
        self.assertIn("right", aligns)

    def test_error_strip_offers_retry_and_model(self):
        page = self.open()
        page.evaluate("openChat('c_err')")
        page.wait_for_selector(".error-strip")
        self.assertEqual(page.locator(".error-strip .step-btn").count(), 2)
        page.click(".error-strip .step-btn:nth-of-type(2)")
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("document.querySelector('#model-dd-pop').style.display"), "block")

    def test_no_automatic_popups_in_automation(self):
        page = self.open()
        page.wait_for_timeout(3500)
        self.assertEqual(page.locator(".memu-welcome, .memu-hint, .memu-tour-bubble").count(), 0)
        self.assertEqual(page.locator("#btn-help").count(), 1)

    def test_tour_keyboard_and_close(self):
        page = self.open()
        page.evaluate("MemuTour.start()")
        page.wait_for_selector(".memu-tour-bubble")
        first = page.inner_text(".memu-tour-bubble .tour-title")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(150)
        second = page.inner_text(".memu-tour-bubble .tour-title")
        self.assertNotEqual(first, second)
        box = page.evaluate("""() => { const r = document.querySelector('.memu-tour-bubble').getBoundingClientRect();
            return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }""")
        self.assertTrue(box)
        for _ in range(12):
            if not page.locator(".memu-tour-bubble").count():
                break
            page.click(".memu-tour-bubble [data-tour='next']")
            page.wait_for_timeout(80)
        self.assertEqual(page.locator(".memu-tour-bubble, .memu-tour-spot, .memu-tour-block").count(), 0)
        page.evaluate("MemuTour.start()")
        page.wait_for_selector(".memu-tour-bubble")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(".memu-tour-bubble, .memu-tour-block").count(), 0)
        self.assertEqual(page.evaluate("MemuTour.state().welcome"), "done")

    def test_help_menu_hints_and_settings(self):
        page = self.open()
        page.click("#btn-help")
        page.wait_for_selector(".memu-help-menu")
        self.assertEqual(page.get_attribute("#btn-help", "aria-expanded"), "true")
        self.assertGreaterEqual(page.locator(".memu-help-menu [role=menuitem]").count(), 5)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(".memu-help-menu").count(), 0)

        page.evaluate("showSchedule(true)")
        page.wait_for_selector("#pln-root [data-act='new']")
        self.assertTrue(page.evaluate("MemuTour.hint('plan', true)"))
        res = page.evaluate("""() => { const h = document.querySelector('.memu-hint').getBoundingClientRect();
            const a = document.querySelector('#pln-root [data-act="new"]').getBoundingClientRect();
            return { inside: h.left >= 0 && h.right <= innerWidth && h.top >= 0 && h.bottom <= innerHeight, near: Math.abs(h.top - a.bottom) < 40 || Math.abs(a.top - h.bottom) < 40 }; }""")
        self.assertTrue(res["inside"])
        self.assertTrue(res["near"])
        page.click(".memu-hint .mh-off")
        self.assertTrue(page.evaluate("MemuTour.state().off"))
        page.evaluate("closePanel()")

        page.evaluate("() => { showSettings(true); }")
        page.wait_for_timeout(400)
        page.evaluate("openSettingsPage('general')")
        page.wait_for_selector("#settings-tips #st-tips")
        self.assertFalse(page.is_checked("#st-tips"))
        page.click("#st-tips-reset")
        self.assertTrue(page.is_checked("#st-tips"))
        self.assertFalse(page.evaluate("MemuTour.state().off"))

    def test_english_tour_has_no_cyrillic(self):
        self.http.post(self.servers.pg + "/api/pg/preferences", json={"language": "en"})
        self.addCleanup(lambda: self.http.post(self.servers.pg + "/api/pg/preferences", json={"language": "ru"}))
        page = self.open(lang="en")
        page.click("#btn-help")
        page.wait_for_selector(".memu-help-menu")
        text = page.inner_text(".memu-help-menu")
        page.keyboard.press("Escape")
        page.evaluate("MemuTour.welcome(true)")
        text += page.inner_text(".memu-welcome")
        page.click(".memu-welcome [data-w='go']")
        for _ in range(10):
            if not page.locator(".memu-tour-bubble").count():
                break
            text += page.inner_text(".memu-tour-bubble")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(80)
        self.assertGreater(len(text), 300)
        self.assertFalse(CYR.search(text), CYR.findall(text)[:5])

if __name__ == "__main__":
    unittest.main()
