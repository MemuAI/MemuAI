"""Round 36 (r36-perf-css-2): main-window styles — only the active design's sheet matches, no
full-window backdrop blur under animated wallpapers. Static checks + one browser check (skipped
without MEMU_TEST_CHROMIUM). No network."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
DESIGNS = ("aurora", "focus", "grid", "glass", "soft", "terminal", "brutal")


def read(name: str) -> str:
    return (PG / name).read_text(encoding="utf-8")


def rule_block(css: str, selector: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    start = css.index(selector + " {")
    return css[start:css.index("}", start)]


class Round36PerfCssStatic(unittest.TestCase):
    def test_design_sheets_inactive_by_default(self):
        html = read("index.html")
        for name in DESIGNS:
            tag = re.search(r'<link rel="stylesheet" href="/designs/' + name + r'\.css\?v=[^"]+"[^>]*>', html)
            self.assertIsNotNone(tag, name)
            self.assertIn(f'data-design-css="{name}"', tag.group(0))
            self.assertIn('media="not all"', tag.group(0))
        # the switcher follows data-design right after the links, before motion.css
        script = html.index('link[data-design-css]')
        self.assertLess(html.index("/designs/brutal.css"), script)
        self.assertLess(script, html.index("/motion.css"))
        self.assertIn('attributeFilter: ["data-design"]', html)

    def test_every_design_rule_is_scoped(self):
        # a sheet that does not match must not hide unscoped rules other designs rely on
        for name in DESIGNS:
            css = re.sub(r"/\*.*?\*/", "", read(f"designs/{name}.css"), flags=re.S)
            for match in re.finditer(r"(?:^|[{};])\s*([^{};@\s][^{};]*)\{", css):
                depth, part, parts = 0, "", []
                for ch in match.group(1):
                    depth += ch in "([" ; depth -= ch in ")]"
                    if ch == "," and depth == 0:
                        parts.append(part); part = ""
                    else:
                        part += ch
                parts.append(part)
                for sel in (p.strip() for p in parts):
                    if not sel or re.fullmatch(r"from|to|[\d.]+%", sel):
                        continue
                    self.assertIn(f'data-design="{name}"', sel, f"{name}.css: unscoped selector {sel[:80]}")

    def test_big_panes_do_not_blur(self):
        glass = read("designs/glass.css")
        self.assertNotIn("backdrop-filter", rule_block(glass, ':root[data-design="glass"] .side'))
        self.assertNotIn("backdrop-filter", rule_block(glass, ':root[data-design="glass"] .main-content'))
        self.assertIn("g-breathe 18s steps(", glass)
        aurora = read("designs/aurora.css")
        self.assertNotIn("backdrop-filter", rule_block(aurora, ':root[data-design="aurora"] .side'))
        self.assertIn("au-drift 38s steps(", aurora)
        self.assertNotIn("au-breathe 6s 1s ease-in-out infinite", aurora)


@unittest.skipUnless(os.environ.get("MEMU_TEST_CHROMIUM"), "needs MEMU_TEST_CHROMIUM")
class Round36PerfCssBrowser(unittest.TestCase):
    def test_design_switch_without_reload(self):
        from playwright.sync_api import sync_playwright
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        env = dict(os.environ, MEMUAI_DATA_DIR=tempfile.mkdtemp(prefix="r36css-"), MEMUAI_PROXY_URL="http://127.0.0.1:9",
                   NO_PROXY="127.0.0.1,localhost", PYTHONPATH=str(ROOT))
        srv = subprocess.Popen([sys.executable, "-c", "import uvicorn;from app.playground_api import create_app;"
                                f"uvicorn.run(create_app(),host='127.0.0.1',port={port},log_level='error')"],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ["MEMU_TEST_CHROMIUM"], args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1200, "height": 800}, bypass_csp=True)
                for _ in range(60):
                    try:
                        page.goto(f"http://127.0.0.1:{port}/", wait_until="load"); break
                    except Exception:
                        time.sleep(0.5)
                page.wait_for_function("!document.body.classList.contains('booting')", timeout=30000)
                active = "()=>[...document.querySelectorAll('link[data-design-css]')].filter(l=>(l.sheet ? l.sheet.media.mediaText : l.media)==='all').map(l=>l.dataset.designCss)"
                self.assertEqual(page.evaluate(active), [])          # classic: none of the seven
                loads = page.evaluate("()=>performance.getEntriesByType('resource').filter(e=>e.name.includes('/designs/') && /\.css(\?|$)/.test(e.name)).length")
                # what the theme editor / settings preview does: flip data-design
                state = page.evaluate("""async () => { const r = document.documentElement; r.dataset.design = 'glass';
                    await Promise.resolve();   // the switcher runs as a microtask, i.e. before the next paint
                    const side = getComputedStyle(document.querySelector('#app'), '::before').animationName;
                    const sheet = document.querySelector('link[data-design-css="glass"]').sheet;
                    return { side, rules: sheet.cssRules.length, media: sheet.media.mediaText }; }""")
                self.assertEqual(state["side"], "g-aurora")             # glass rules apply before the next paint
                self.assertGreater(state["rules"], 100)
                self.assertEqual(state["media"], "all")
                self.assertEqual(page.evaluate(active), ["glass"])
                page.evaluate("()=>{document.documentElement.dataset.design='terminal'}")
                page.wait_for_timeout(50)
                self.assertEqual(page.evaluate(active), ["terminal"])
                self.assertEqual(loads, page.evaluate("()=>performance.getEntriesByType('resource').filter(e=>e.name.includes('/designs/') && /\.css(\?|$)/.test(e.name)).length"),
                                 "switching must not refetch")
                browser.close()
        finally:
            srv.terminate(); srv.wait(10)


if __name__ == "__main__":
    unittest.main()
