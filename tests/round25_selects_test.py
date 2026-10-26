"""Round 25 (a2-select-flash): changing a select in Settings must not blank («white flash») the window.

Root cause: a change handler that re-renders the open settings (DFY model, context model, chat titles, …)
replaced the page with new nodes and motion.css / the kit replayed the pane entrance from opacity 0, the
body sat at scrollTop 0 for a frame, long lists swapped pretty-select → searchable picker 120 ms later and
focus jumped to the close button. Re-rendering an open settings shell is now in place.

The browser part loads the REAL UI (tests/appearance_fixture.py, mocked API) in headless Chromium and samples
every animation frame (requestAnimationFrame) while a select is changed through the real picker UI.
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
SHOTS = Path("/workspace/work/shots/r25/selects")
sys.path.insert(0, str(ROOT))

from tests.settings_shell_test import _chromium  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None

MODELS = [{"id": f"m{i}", "name": f"Model {i}", "provider": "Demo", "caps": {"vision": i % 2 == 0}} for i in range(16)]

SAMPLER = """() => { window.__frames = []; let n = 0; const tick = () => {
  const pane = document.querySelector('#panel .settings-tab-pane.active');
  const body = document.querySelector('#panel .settings-body');
  const cs = pane ? getComputedStyle(pane) : null;
  window.__frames.push({ op: cs ? +cs.opacity : -1, vis: cs ? cs.visibility : 'none', st: body ? body.scrollTop : -1,
    shell: !!document.querySelector('#panel .sx-shell'), pretty: document.querySelectorAll('#panel .pretty-select .mpick-native, #panel .mpick .pretty-native').length });
  if (++n < 40) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }"""

class SelectFlashContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (PG / "app.js").read_text(encoding="utf-8")
        cls.kit = (PG / "settings-kit.js").read_text(encoding="utf-8")
        cls.css = (PG / "settings.css").read_text(encoding="utf-8")

    def test_open_settings_rerender_is_in_place(self):
        self.assertIn("function captureSettingsView()", self.js)
        self.assertIn("buildSettingsPanel(view ? { animate: false, scroll: false } : {})", self.js)
        self.assertIn("mountSettingsPage(page, options);\n  renderAboutVersion();", self.js)
        self.assertIn('sh.classList.toggle("sx-quiet", o.animate === false)', self.kit)
        self.assertIn("if (o.animate !== false) animateIn(activePane());", self.kit)
        self.assertIn(".sx-shell.sx-quiet :is(.settings-tab-pane.active:not(.sx-enter), .settings-tab-btn.active) { animation: none; }", self.css)

    def test_long_lists_become_the_searchable_picker_at_once(self):
        self.assertIn('if (select.dataset.mpick === "1" || attachSelectSearch(select)) { select.dataset.pretty = "1"; return; }', self.js)
        self.assertIn("if (select.options.length < LIST_SEARCH_MIN_ITEMS) return false;", self.js)

    def test_pickers_respect_disabled_options_and_unchanged_values(self):
        self.assertIn("if (!option || option.disabled) return;", self.js)
        self.assertIn("'disabled aria-disabled=\"true\"'", self.js)
        self.assertIn("if (changed) select.dispatchEvent(new Event(\"change\", { bubbles: true }));", self.js)

@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class SelectFlashBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.appearance_fixture import build_fixture
        cls._tmp = tempfile.mkdtemp(prefix="memu-r25-selects-")
        fixture = build_fixture(Path(cls._tmp))
        html = fixture.read_text(encoding="utf-8")
        html = html.replace("models:[{id:'demo-claude'", "models:" + json.dumps(MODELS)[:-1] + ",{id:'demo-claude'", 1)
        fixture.write_text(html, encoding="utf-8")
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1200, "height": 640})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.add_init_script("localStorage.setItem('memu_theme', 'light')")
        cls.page.goto(fixture.as_uri(), wait_until="domcontentloaded")
        deadline = time.time() + 90
        while time.time() < deadline:
            if cls.page.evaluate("document.body.classList.contains('boot-ready') && typeof showSettings === 'function' && !!window.MemuSettingsKit"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("MemuAI UI did not boot")
        cls.page.evaluate("setTheme('light', false)")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def wait(self, expression, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.page.evaluate(expression):
                return
            time.sleep(0.1)
        self.fail("timed out: " + expression)

    def open_control(self, control_id):
        """Open the settings page (and sub-page) that holds the control and scroll it into the middle."""
        page = self.page
        page.evaluate("async () => { if (document.querySelector('#panel-back.visible')) closePanel(); currentSettingsTab = 'general'; await showSettings(true); }")
        self.wait("!!document.querySelector('#panel .sx-shell .settings-tab-pane.active')")
        where = page.evaluate(f"""() => {{ const el = document.getElementById('{control_id}'); if (!el) return null;
          const pane = el.closest('.settings-tab-pane'); const sub = el.closest('.sx-sub');
          return {{ page: pane && pane.dataset.page, sub: sub ? sub.dataset.sxSub : '' }}; }}""")
        self.assertIsNotNone(where, control_id)
        page.evaluate(f"openSettingsPage('{where['page']}')")
        if where["sub"]:
            page.evaluate(f"MemuSettingsKit.openSub('{where['sub']}')")
        time.sleep(0.5)
        page.evaluate(f"""() => {{ const el = document.getElementById('{control_id}');
          const host = el.closest('.mpick, .pretty-select, .mmp-select') || el; host.scrollIntoView({{ block: 'center' }}); }}""")
        return self.steady_scroll()

    def steady_scroll(self):
        last = None
        for _ in range(30):
            now = self.page.evaluate("document.querySelector('#panel .settings-body').scrollTop")
            if now == last:
                return now
            last = now
            self.page.wait_for_timeout(120)
        return last

    def tap(self, selector):
        """A real mouse click on an element already on screen (page.click would first scroll every ancestor,
        and any outside scroll closes an open picker, as it should)."""
        page = self.page
        page.evaluate(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'nearest'}})")
        page.wait_for_timeout(150)
        box = page.locator(selector).first.bounding_box()
        page.mouse.click(box["x"] + min(20, box["width"] / 2), box["y"] + box["height"] / 2)

    def screencast(self):
        cdp = self.page.context.new_cdp_session(self.page)
        frames = []

        def on_frame(frame):
            frames.append(frame["data"])
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})
            except Exception:
                pass
        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1})
        return cdp, frames

    def assert_no_blank_frames(self, tag, scroll_before):
        frames = self.page.evaluate("window.__frames")
        self.assertGreater(len(frames), 5)
        for i, f in enumerate(frames):
            self.assertTrue(f["shell"], f"{tag}: frame {i} without the settings shell")
            self.assertGreaterEqual(f["op"], 0.99, f"{tag}: frame {i} pane opacity {f['op']} (blank frame)")
            self.assertEqual(f["vis"], "visible", f"{tag}: frame {i}")
            self.assertLessEqual(abs(f["st"] - scroll_before), 1, f"{tag}: frame {i} scrollTop {f['st']} != {scroll_before}")
            self.assertEqual(f["pretty"], 0, f"{tag}: frame {i} pretty-select/picker swap")

    def assert_screencast_steady(self, tag, frames):
        from PIL import Image
        values = []
        for i, data in enumerate(frames):
            image = Image.open(io.BytesIO(base64.b64decode(data))).convert("L")
            hist = image.histogram()
            values.append(sum(k * v for k, v in enumerate(hist)) / (image.width * image.height))
            if i in (0, len(frames) - 1):
                image.convert("RGB").save(SHOTS / f"{tag}-frame{i}.png")
        if len(values) >= 2:
            self.assertLess(max(values) - min(values), 12, f"{tag}: screen brightness jumped {values}")

    def change_through_picker(self, control_id, option_value, tag):
        page = self.page
        scroll = self.open_control(control_id)
        page.screenshot(path=str(SHOTS / f"{tag}-before.png"))

        kind = page.evaluate(f"(() => {{ const el = document.getElementById('{control_id}'); return el.closest('.mmp-select') ? 'mmp' : el.closest('.mpick') ? 'mpick' : 'pretty'; }})()")
        host = f"#panel :is(.mpick, .pretty-select, .mmp-select):has(#{control_id})"
        cdp, shots = self.screencast()
        time.sleep(0.2)
        self.tap(f"{host} > :is(.mpick-btn, .pretty-select-button, .mmp-trigger)")
        time.sleep(0.3)

        scroll = self.steady_scroll()
        page.evaluate(SAMPLER)
        if kind == "mmp":
            item = f"body > .mmp-pop .mmp-list [data-model='{option_value}']"
        elif kind == "mpick":
            index = page.evaluate(f"""() => [...document.querySelector('{host} .mpick-list').querySelectorAll('[data-mpick-index]')]
              .findIndex((b) => b.textContent.trim() === [...document.getElementById('{control_id}').options].find((o) => o.value === '{option_value}').textContent.trim())""")
            item = f"{host} .mpick-list [data-mpick-index='{index}']"
        else:
            index = page.evaluate(f"[...document.getElementById('{control_id}').options].findIndex((o) => o.value === '{option_value}')")
            item = f"body > .pretty-select-menu.visible [data-pretty-option='{index}']"

        page.evaluate(f"""() => {{ const item = document.querySelector("{item}"); const list = item.parentElement;
          list.scrollTop = item.offsetTop - list.clientHeight / 2; item.click(); }}""")
        time.sleep(1.0)
        cdp.send("Page.stopScreencast")
        page.screenshot(path=str(SHOTS / f"{tag}-after.png"))
        self.assert_no_blank_frames(tag, scroll)
        self.assert_screencast_steady(tag, shots)
        self.assertEqual(page.evaluate(f"document.getElementById('{control_id}').value"), option_value)

        focus = page.evaluate("(() => { const a = document.activeElement; const host = a && a.closest('.mpick, .pretty-select, .mmp-select'); return host ? host.querySelector('select').id : (a && (a.id || a.className)); })()")
        self.assertEqual(focus, control_id)
        return kind

    def test_dfy_model_change_does_not_blank_the_page(self):
        kind = self.change_through_picker("st-dfy-model", "m4", "dfy-model")
        self.assertEqual(kind, "mmp")
        self.wait("S.preferences.dfy_model === 'm4'")

        errors = [e for e in self.errors if "sandboxed" not in e]
        self.assertFalse(errors, errors)

    def test_context_model_change_does_not_blank_the_page(self):
        self.change_through_picker("st-context-model", "m6", "context-model")
        self.wait("S.preferences.context_settings_model === 'm6'")

    def test_pretty_select_change_with_rerender_does_not_blank_the_page(self):
        kind = self.change_through_picker("st-chat-titles", "off", "chat-titles")
        self.assertEqual(kind, "pretty")
        self.wait("S.preferences.chat_titles === 'off'")

    def test_page_switch_still_animates(self):
        page = self.page
        self.open_control("st-chat-titles")
        page.evaluate("rerenderSettingsPreservingScroll()")
        self.assertTrue(page.evaluate("document.querySelector('#panel .sx-shell').classList.contains('sx-quiet')"))
        page.evaluate("openSettingsPage('appearance')")
        self.assertFalse(page.evaluate("document.querySelector('#panel .sx-shell').classList.contains('sx-quiet')"))
        self.assertTrue(page.evaluate("document.querySelector('#pane-appearance').classList.contains('sx-enter')"))

    def test_opening_another_page_while_open_is_a_page_switch(self):
        """showSettings() for another page (e.g. from the Quick window) animates and starts at the top."""
        page = self.page
        self.assertGreater(self.open_control("st-chat-titles"), 0)
        page.evaluate("async () => { currentSettingsTab = 'dfy'; await showSettings(true); }")
        self.wait("document.querySelector('#panel .sx-shell').dataset.page === 'dfy'")
        self.assertFalse(page.evaluate("document.querySelector('#panel .sx-shell').classList.contains('sx-quiet')"))
        self.assertTrue(page.evaluate("document.querySelector('#pane-dfy').classList.contains('sx-enter')"))
        self.assertEqual(page.evaluate("document.querySelector('#panel .settings-body').scrollTop"), 0)

    def test_disabled_options_cannot_be_picked(self):
        page = self.page
        self.open_control("st-chat-titles")

        page.evaluate("""() => { const row = document.getElementById('st-chat-titles').closest('.sx-row');
          const box = document.createElement('div'); box.id = 'r25-box';
          box.innerHTML = '<select id="r25-pick">' + Array.from({length: 10}, (_, i) =>
            `<option value="v${i}" ${i % 2 ? 'disabled' : ''}>Item ${i}</option>`).join('') + '</select>';
          row.after(box); enhanceSelects(box); }""")
        host = "#r25-box .mpick"
        self.assertTrue(page.evaluate(f"!!document.querySelector('{host}')"))
        self.tap(f"{host} > .mpick-btn")
        time.sleep(0.2)
        self.assertEqual(page.evaluate(f"document.querySelectorAll('{host} .mpick-item[disabled]').length"), 5)
        page.evaluate(f"document.querySelector('{host} .mpick-item[disabled]').click()")
        self.assertEqual(page.evaluate("document.getElementById('r25-pick').value"), "v0")

        for _ in range(3):
            page.keyboard.press("ArrowDown")
            self.assertFalse(page.evaluate(f"!!document.querySelector('{host} .mpick-item.cur[disabled]')"))
        page.keyboard.press("Enter")
        self.assertEqual(page.evaluate("document.getElementById('r25-pick').value"), "v6",
                         page.evaluate(f"[document.activeElement.outerHTML.slice(0, 80), document.querySelector('{host}').className, document.querySelector('{host} .mpick-item.cur')?.textContent]"))
        self.assertFalse(page.evaluate(f"document.querySelector('{host}').classList.contains('open')"))
        page.evaluate("document.getElementById('r25-box').remove()")

    def test_programmatic_changes_reach_the_visible_picker(self):
        page = self.page
        self.open_control("st-chat-titles")
        label = "#panel .pretty-select:has(#st-chat-titles) > .pretty-select-button"
        page.evaluate("document.getElementById('st-chat-titles').value = 'off'")
        self.assertEqual(page.inner_text(label).strip().rstrip("⌄").strip(), page.evaluate("document.getElementById('st-chat-titles').selectedOptions[0].text"))
        page.evaluate("document.getElementById('st-chat-titles').disabled = true")
        page.wait_for_timeout(50)
        self.assertTrue(page.evaluate(f"document.querySelector('{label}').disabled"))
        page.evaluate("const s = document.getElementById('st-chat-titles'); s.disabled = false; s.value = 'ai'")
        self.open_control("st-dfy-model")
        page.evaluate("const s = document.getElementById('st-dfy-model'); s.innerHTML += '<option value=\"zz\">Brand new</option>'; s.value = 'zz'")
        page.wait_for_timeout(50)
        self.assertEqual(page.inner_text("#panel .mmp-select:has(#st-dfy-model) .mmp-trigger .mmp-name").strip(), "Brand new")

    def test_picking_the_current_value_saves_nothing(self):
        page = self.page
        self.open_control("st-chat-titles")
        page.wait_for_timeout(400)
        page.evaluate("window.__qa_requests.length = 0")
        current = page.evaluate("document.getElementById('st-chat-titles').selectedIndex")
        self.tap("#panel .pretty-select:has(#st-chat-titles) > .pretty-select-button")
        page.wait_for_timeout(150)
        page.evaluate(f"document.querySelector(\"body > .pretty-select-menu.visible [data-pretty-option='{current}']\").click()")
        page.wait_for_timeout(400)
        posts = page.evaluate("window.__qa_requests.filter((r) => r.post && r.path === '/api/pg/preferences').length")
        self.assertEqual(posts, 0)

if __name__ == "__main__":
    unittest.main()
