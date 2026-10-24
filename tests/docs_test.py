"""Settings → Documentation: every page exists in both languages and every link resolves."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from app import docs

ROOT = Path(__file__).resolve().parents[1]
INDEX = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
PAGE_IDS = [page for group in INDEX["groups"] for page in group["pages"]]
LINK = re.compile(r"\]\((doc:[^)]+|#[^)]+)\)")


class DocsTests(unittest.TestCase):
    def test_every_indexed_page_exists_in_both_languages(self) -> None:
        self.assertEqual(len(PAGE_IDS), len(set(PAGE_IDS)), "a page is listed twice in index.json")
        for lang in docs.LANGUAGES:
            for page_id in PAGE_IDS:
                page = docs.page(lang, page_id)
                self.assertIsNotNone(page, f"{lang}/{page_id}.md is missing")
                self.assertFalse(page.get("fallback"), f"{lang}/{page_id}.md falls back to another language")
                self.assertTrue(page["title"].strip(), f"{lang}/{page_id}.md has no title")
                self.assertTrue(page["summary"].strip(), f"{lang}/{page_id}.md has no summary")

    def test_no_orphan_markdown_files(self) -> None:
        for lang in docs.LANGUAGES:
            files = {path.stem for path in (ROOT / "app" / "docs" / lang).glob("*.md")}
            self.assertEqual(files - set(PAGE_IDS), set(), f"{lang}: pages not listed in index.json")

    def test_translations_have_the_same_shape(self) -> None:
        # The same number of sections keeps the two languages from drifting apart.
        for page_id in PAGE_IDS:
            if page_id == "glossary":
                continue  # sorted alphabetically: Russian has Cyrillic and Latin sections, English one list
            ru = docs.page("ru", page_id)["headings"]
            en = docs.page("en", page_id)["headings"]
            self.assertEqual([row["level"] for row in ru], [row["level"] for row in en],
                             f"{page_id}: RU and EN headings differ in structure")

    def test_every_link_and_anchor_resolves(self) -> None:
        problems = []
        anchors_of = {(lang, page_id): {row["anchor"] for row in docs.page(lang, page_id)["headings"]}
                      for lang in docs.LANGUAGES for page_id in PAGE_IDS}
        for lang in docs.LANGUAGES:
            for page_id in PAGE_IDS:
                text = (ROOT / "app" / "docs" / lang / f"{page_id}.md").read_text(encoding="utf-8")
                for match in LINK.finditer(text):
                    target = match.group(1)
                    if target.startswith("#"):
                        other, anchor = page_id, target[1:]
                    else:
                        other, _, anchor = target[4:].partition("#")
                    if other not in PAGE_IDS:
                        problems.append(f"{lang}/{page_id}: unknown page {target}")
                        continue
                    if anchor and anchor not in anchors_of[(lang, other)]:
                        problems.append(f"{lang}/{page_id}: unknown anchor {target}")
        self.assertEqual(problems, [])

    def test_catalog_and_search(self) -> None:
        catalog = docs.catalog("en")
        self.assertEqual(catalog["home"], "welcome")
        self.assertEqual(sum(len(group["pages"]) for group in catalog["groups"]), len(PAGE_IDS))
        hits = docs.search("ru", "квантизация")
        self.assertTrue(any(hit["id"] in ("local-models", "glossary") for hit in hits))
        hits = docs.search("en", "--user-bubble")
        self.assertEqual(hits[0]["id"], "themes-reference")

    def test_key_markup_allows_plus_inside(self) -> None:
        # "++Ctrl+Shift+T++" once broke and swallowed the text up to the next "++".
        self.assertEqual(docs.plain("press ++/++ then ++Ctrl+Shift+T++ or ++Alt++ + click"),
                         "press / then Ctrl+Shift+T or Alt + click")

    def test_ai_summaries_and_screenshots(self) -> None:
        # Round 29: every page has a shipped «AI summary» in both languages (details: round29_docs_ai_test).
        from app import docs_ai
        image = re.compile(r"^\s*!\[[^\]]+\]\((docs-img/(ru|en)/[^)\s]+)", re.M)
        for lang in docs.LANGUAGES:
            pages = docs_ai.load(lang)["pages"]
            self.assertEqual([p for p in PAGE_IDS if p not in pages], [], f"{lang}: pages without an AI summary")
            for page_id in PAGE_IDS:
                text = (ROOT / "app" / "docs" / lang / f"{page_id}.md").read_text(encoding="utf-8")
                for match in image.finditer(text):
                    self.assertEqual(match.group(2), lang, f"{lang}/{page_id}: a screenshot of the other language")
                    self.assertTrue((ROOT / "app" / "pgstatic" / match.group(1)).is_file(), f"{lang}/{page_id}: {match.group(1)}")

    def test_code_blocks_are_closed(self) -> None:
        for lang in docs.LANGUAGES:
            for page_id in PAGE_IDS:
                text = (ROOT / "app" / "docs" / lang / f"{page_id}.md").read_text(encoding="utf-8")
                fences = sum(1 for line in text.splitlines() if line.lstrip().startswith("```"))
                self.assertEqual(fences % 2, 0, f"{lang}/{page_id}: an unclosed code block")


if __name__ == "__main__":
    unittest.main()
