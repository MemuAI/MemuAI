"""Round 36 (r36-perf-ui): static checks for the main-window performance fixes (no network, no browser)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PG = Path(__file__).resolve().parents[1] / "app" / "pgstatic"

def read(name: str) -> str:
    return (PG / name).read_text(encoding="utf-8")

class Round36PerfUi(unittest.TestCase):
    def test_no_fast_intervals_in_round35_modules(self):

        for name in ("askuser.js", "bigdocs.js", "branches.js", "canvas.js", "mentions.js", "deeplinks.js",
                     "inbox.js", "plan.js", "flows.js", "plan-triggers.js", "memufiles-settings.js"):
            src = read(name)
            for match in re.finditer(r"setInterval\([^;]*?,\s*(\d+)\s*\)", src):
                self.assertGreaterEqual(int(match.group(1)), 1000, f"{name}: setInterval every {match.group(1)} ms")

    def test_canvas_follows_chat_open_event(self):
        self.assertNotIn("setInterval(onChatMaybeChanged", read("canvas.js"))
        self.assertIn('"memu:chat-open"', read("app.js"))

    def test_scheme_zoom_measures_lazily(self):
        src = read("diagram-zoom.js")
        self.assertIn("IntersectionObserver", src)
        self.assertIn("function start()", src)
        self.assertIn('stage.classList.add("mdz-ready")', src)

        self.assertNotIn("const label = textSize(svg);", src)

    def test_diagram_theme_read_once_per_task(self):
        src = read("diagrams.js")
        self.assertIn("themeMemo", src)
        self.assertIn("Promise.resolve().then(() => { themeMemo = null; })", src)

    def test_zone_scrollers_update_once_a_frame(self):
        src = read("panels-layout.js")
        self.assertNotIn('document.addEventListener("scroll", () => { updateAllScroll();', src)
        self.assertRegex(src, r"scrollFrame = requestAnimationFrame\(")
        self.assertIn("{ capture: true, passive: true }", src)

    def test_composer_orb_animates_only_compositor_properties(self):
        css = read("appearance.css")
        for name in ("live-orb-pulse", "live-orb-blob", "live-orb-twinkle", "live-orb-spin"):
            block = re.search(r"@keyframes " + name + r"\s*\{(.*?)\}\s*\}", css, re.S) or re.search(r"@keyframes " + name + r"\s*\{(.*?)\}", css, re.S)
            self.assertIsNotNone(block, name)
            body = block.group(1)
            self.assertNotIn("box-shadow", body, name)
            self.assertNotIn("border-radius", body, name)

    def test_cache_versions_bumped(self):
        html = read("index.html")
        for tag in ('src="/diagram-zoom.js?v=1.1-r36"', 'src="/diagrams.js?v=1.2-r36"',
                    'src="/panels-layout.js?v=3.6-r36"', 'href="/appearance.css?v=1.8-r36"'):
            self.assertIn(tag, html)

if __name__ == "__main__":
    unittest.main()
