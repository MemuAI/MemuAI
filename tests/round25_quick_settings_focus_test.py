"""Round 25 (a4-companion-3): Quick Access settings keep focus and scroll when a change re-renders the editor.

``commands-settings.js`` re-renders the whole editor (``innerHTML``) when the voice provider changes, so the
keyboard focus went to <body> and a scrolled settings page jumped back to the top. Now the focused ``data-set``
control (or the one that just changed — an enhanced select moves focus into its menu first) and the scroll of
every scrolling ancestor are restored.

Hermetic: a harness page served by Playwright routes; the plugin's /api/* calls are answered by the real
FastAPI app through TestClient (temporary data folder of the test package). Skipped without Playwright/Chromium.

    cd /workspace/work/memuai && python3 -m unittest tests.round25_quick_settings_focus_test
"""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands-settings.js"
from tests.round25_chat_test import _chromium
CHROME = _chromium()
ORIGIN = "http://memu.test"

HARNESS = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
  #scroller { height: 260px; overflow: auto; } .pad { height: 700px; } .pretty-native { display: none; }
</style></head><body>
<div id="scroller"><div class="pad"></div><div class="%s"><div id="host"></div></div><div class="pad"></div></div>
<script>
  // A minimal stand-in for app.js enhanceSelects(): the <select> is hidden inside a wrapper next to a button.
  window.enhanceSelects = (root) => root.querySelectorAll("select:not([data-pretty])").forEach((select) => {
    select.dataset.pretty = "1";
    const wrap = document.createElement("div"); wrap.className = "pretty-select";
    const button = document.createElement("button"); button.type = "button"; button.className = "pretty-select-button";
    select.parentNode.insertBefore(wrap, select); wrap.append(select, button); select.classList.add("pretty-native");
  });
</script>
<script src="/plugin.js"></script></body></html>"""

def _client():
    from starlette.testclient import TestClient
    from app.playground_api import create_app
    return TestClient(create_app(), base_url="http://127.0.0.1")

class SourceGuard(unittest.TestCase):
    def test_render_restores_the_view(self) -> None:
        src = PLUGIN.read_text(encoding="utf-8")
        block = src[src.index("function render() {"):src.index("function partCommandsHtml(")]
        self.assertRegex(block, re.compile(r"const view = snapshotView\(\);\s*el\.innerHTML", re.S))
        self.assertLess(block.index("enhanceSelects(el)"), block.index("restoreView(view);"),
                        "focus goes to the enhanced control, after enhanceSelects")

class Headless(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except Exception:
            raise unittest.SkipTest("playwright missing")
        if not os.path.exists(CHROME):
            raise unittest.SkipTest("chromium missing")
        cls.client = _client()

    def route(self, page, shell_class: str) -> None:
        client = self.client

        def handle(route):
            request = route.request
            path = request.url[len(ORIGIN):]
            if path in ("/", "/index.html"):
                return route.fulfill(status=200, content_type="text/html", body=HARNESS % shell_class)
            if path == "/plugin.js":
                return route.fulfill(status=200, content_type="text/javascript", body=PLUGIN.read_text(encoding="utf-8"))
            if path.startswith("/api/"):
                if request.method == "POST":
                    res = client.post(path, content=request.post_data or "{}", headers={"Content-Type": "application/json"})
                else:
                    res = client.get(path)
                return route.fulfill(status=res.status_code, content_type="application/json", body=res.content)
            return route.fulfill(status=404, body="")
        page.route(ORIGIN + "/**", handle)

    def run_case(self, shell_class: str, enhanced: bool) -> dict:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 900, "height": 600})
                self.route(page, shell_class)
                page.goto(ORIGIN + "/")
                page.evaluate("() => MemuCommandsSettings.mount(document.getElementById('host'), { part: 'voice' })")
                page.wait_for_selector('select[data-set="tts_provider"]', state="attached", timeout=15000)
                return page.evaluate("""(enhanced) => new Promise((resolve) => {
                  const scroller = document.getElementById('scroller');
                  const select = document.querySelector('select[data-set="tts_provider"]');
                  select.closest('.mcs-row, label').scrollIntoView({ block: 'center' });
                  const before = scroller.scrollTop;
                  const next = Array.from(select.options).map((o) => o.value).find((v) => v && v !== select.value);
                  if (enhanced) {
                    // pretty-select: the button had focus, the menu (in <body>) took it, closed, then «change».
                    select.parentElement.querySelector('button').focus();
                    const menu = document.createElement('button'); menu.setAttribute('role', 'option');
                    const list = document.createElement('div'); list.setAttribute('role', 'listbox'); list.append(menu);
                    document.body.append(list); menu.focus(); list.remove();
                  } else {
                    select.focus();
                  }
                  select.value = next;
                  select.dispatchEvent(new Event('change', { bubbles: true }));
                  requestAnimationFrame(() => {
                    const active = document.activeElement;
                    const row = active && active.closest('.mcs-row, label');
                    const control = row && row.querySelector('[data-set]');
                    resolve({ before, after: scroller.scrollTop, replaced: !select.isConnected,
                              focused: control ? control.dataset.set : (active ? active.tagName : ''),
                              tag: active ? active.tagName : '', value: control ? control.value : null, next });
                  });
                })""", enhanced)
            finally:
                browser.close()

    def check(self, result: dict, tag: str) -> None:
        self.assertTrue(result["replaced"], "the change re-rendered the editor: " + json.dumps(result))
        self.assertGreater(result["before"], 0)
        self.assertEqual(result["after"], result["before"], "scroll kept: " + json.dumps(result))
        self.assertEqual(result["focused"], "tts_provider", "focus kept: " + json.dumps(result))
        self.assertEqual(result["tag"], tag)
        self.assertEqual(result["value"], result["next"])

    def test_native_select_in_quick_access(self) -> None:
        self.check(self.run_case("quick-shell", enhanced=False), "SELECT")

    def test_enhanced_select_in_settings(self) -> None:
        self.check(self.run_case("settings-body", enhanced=True), "BUTTON")

if __name__ == "__main__":
    unittest.main()
