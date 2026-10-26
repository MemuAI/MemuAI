# -*- coding: utf-8 -*-
"""Round 39 · MemuMusic visual polish (r39-music-polish).

* п.2 — a playing track row shows exactly one thing in the «#» cell: the «playing» bars, or ▶ on hover — never the
  bars mixed with ▶ (focus left on ▶ after a mouse click) or with the ⋮⋮ grip (playlist with manual order);
* п.5 — progress bars are drawn by width, not scaleX (scaleX is not pixel-snapped: faint rows above the bar at
  125 / 150 % and a squashed rounded end), the knob hides by opacity, not by a degenerate scale(0);
* п.9 — one focus ring per text field: the URL field of «Download» had the 2px rectangle outline inside its rounded box.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round37_music_harness import chromium, frame_of, open_page, wait  # noqa: E402

CSS = (ROOT / "app" / "module_builtins" / "music" / "style.css").read_text(encoding="utf-8")
TG_CSS = (ROOT / "app" / "tgapp" / "music.css").read_text(encoding="utf-8")

def rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""

class StaticTest(unittest.TestCase):
    def test_progress_by_width(self):
        for sel in (".mm-seek-fill", ".mm-seek-buf"):
            body = rule(CSS, sel)
            self.assertIn("width: calc(var(--", body, sel)
            self.assertNotIn("scaleX", body, sel)
        knob = rule(CSS, ".mm-seek-knob")
        self.assertIn("opacity: 0", knob)
        self.assertNotIn("scale(0)", knob)
        self.assertNotIn("scaleX", rule(TG_CSS, ".mm-scrub-track i"))

    def test_focus_rules(self):
        self.assertIn(':is(input:not([type="checkbox"], [type="radio"], [type="range"], [type="color"], [type="file"], [type="button"]), textarea, select):focus-visible { outline: none; }', CSS)
        self.assertIn(".mm-select:focus-visible", CSS)
        self.assertNotIn(".mm-row:focus-within", CSS)

@unittest.skipUnless(chromium(), "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    ROW = """() => { const out = []; for (const r of document.querySelectorAll('#view .mm-row.playing')) {
      const vis = (e) => { if (!e) return false; const c = getComputedStyle(e); return c.display !== 'none' && c.visibility !== 'hidden' && e.getBoundingClientRect().width > 0; };
      out.push(['.p-btn', '.eqm', '.grip'].filter((s) => vis(r.querySelector(s)))); } return out; }"""

    def test_row_indicator_and_focus(self):
        from playwright.sync_api import sync_playwright
        from round38_music_design_audit import AUDIT_JS, FOCUS_JS
        with sync_playwright() as pw:
            browser, page, errors = open_page(pw, "ru", "light", (1280, 760))
            try:
                frame = frame_of(page)
                wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.Theme)")
                for view in ("() => MemuMusic.go('playlist', { id: MemuMusic.S.playlists.find((p) => !p.smart).id }, { instant: true })",
                             "() => MemuMusic.go('library', {}, { instant: true })"):
                    frame.evaluate("() => MemuMusic.Player.pause()")
                    frame.evaluate(view)
                    page.wait_for_timeout(600)
                    row = frame.query_selector_all("#view .mm-row")[1]
                    cell = row.query_selector(".n").bounding_box()
                    page.mouse.move(cell["x"] + cell["width"] / 2, cell["y"] + cell["height"] / 2)
                    page.wait_for_timeout(200)
                    page.mouse.click(cell["x"] + cell["width"] - 8, cell["y"] + cell["height"] / 2)
                    page.wait_for_timeout(500)
                    self.assertEqual(frame.evaluate(self.ROW), [[".p-btn"]])
                    page.mouse.move(cell["x"] + 400, 5)
                    page.wait_for_timeout(300)
                    self.assertEqual(frame.evaluate(self.ROW), [[".eqm"]])
                frame.evaluate("() => MemuMusic.go('download', {}, { instant: true })")
                page.wait_for_timeout(400)
                self.assertEqual(frame.evaluate(FOCUS_JS), [])
                found = [f for f in frame.evaluate(AUDIT_JS, {"layer": ""}) if f["kind"].startswith("artifact")]
                self.assertEqual(found, [])
                self.assertFalse([e for e in errors if "favicon" not in e], errors)
            finally:
                browser.close()

if __name__ == "__main__":
    unittest.main()
