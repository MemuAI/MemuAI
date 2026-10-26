"""Round 33 (r33d-shots): regressions found by the screenshot pass — static checks of the small UI fixes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

class ShotsFixesTest(unittest.TestCase):
    def test_guide_player_shows_one_caption_language(self):

        for rel in ("app/pgstatic/guide-video.js", "app/static/guide-video.js"):
            src = read(rel)
            self.assertIn("const pick = () =>", src, rel)
            self.assertGreaterEqual(src.count("pick();"), 3, rel)
            self.assertRegex(src, r"setTimeout\(\(\) => \{ if \(current === state\) pick\(\); \}, 0\)")
        self.assertEqual(read("app/pgstatic/guide-video.js"), read("app/static/guide-video.js"))

    def test_proxy_model_card_title_is_the_readable_name(self):
        src = read("app/static/model_caps.js")
        body = src[src.index("function renderCard("):]
        body = body[:body.index("const provider")]
        self.assertIn("proxyModelName", body)
        self.assertLess(body.index("pretty"), body.index("f.name && f.name.value"))

    def test_provider_catalog_count_explains_the_popular_tiles(self):
        i18n = read("app/static/proxy-r33-i18n.js")
        self.assertEqual(i18n.count('"cat.count.rest"'), 2)
        en = i18n[i18n.index('"cat.count.rest": "Other'):].split("\n", 1)[0]
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", en))
        self.assertIn('fmt("cat.count.rest"', read("app/static/proxy-catalog-r33.js"))

    def test_single_colour_brand_marks_are_dark_on_the_white_tile(self):
        css = read("app/pgstatic/integrations-ui.css")
        self.assertIn('.gi-brand:has(svg path[fill="currentColor"]) { color: #1f2328; }', css)

    def test_mini_app_effort_sheet_and_chip(self):
        js = read("app/tgapp/app.js")
        self.assertIn('item.checked ? `<span class="check">', js)
        self.assertIn("checked: level === current", js)
        self.assertNotIn('(level === current ? " ✓" : "")', js)
        css = read("app/tgapp/models.css")
        self.assertIn(".composer-tools .toggle-chip.model { flex: 0 1 auto;", css)

if __name__ == "__main__":
    unittest.main()
