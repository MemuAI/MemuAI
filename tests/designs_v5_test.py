"""Six more interface designs: Блокнот / notebook, Мессенджер / messenger, Ретро / retro,
Глина / clay, Студия / studio, Дзен / zen.

- registration everywhere a design is enumerated (catalogue, preference normalisation, first-paint
  regexes, appearance.js and the theme editor, picker thumbnails, quick window flavours, presets);
- every stylesheet is scoped, complete (light branch, motion settings, visible focus, scrollbars,
  the key surfaces of the app and of settings), loads only bundled fonts;
- WCAG AA: body text (and secondary text where it is used for labels) on the key surfaces of each
  design, recomputed from the same color-mix() formulas, for every built-in variant and accent.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import pgdata, themes
from tests.designs_test import _contrast, _hex, _mix, _selectors

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
NEW = {"notebook": "Блокнот", "messenger": "Мессенджер", "retro": "Ретро", "clay": "Глина", "studio": "Студия", "zen": "Дзен"}
WHITE, BLACK = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)


def css(name):
    return (PG / "designs" / f"{name}.css").read_text(encoding="utf-8")


def _variants(mode):
    base = dict(themes._DARK_BASE if mode == "dark" else themes._LIGHT_BASE)
    for variant in themes.VARIANTS[mode]:
        yield variant["id"] or "default", {**base, **variant["tokens"]}


class RegistrationTest(unittest.TestCase):
    def test_catalogue_names_and_normalisation(self):
        rows = {row["id"]: row for row in themes.DESIGNS}
        self.assertEqual(len(rows), len(themes.DESIGNS), "duplicate design ids")
        for name, ru in NEW.items():
            self.assertIn(name, rows)
            self.assertEqual(rows[name]["ru"], ru)
            for key in ("en", "note_ru", "note_en"):
                self.assertTrue(rows[name].get(key), (name, key))
            self.assertEqual(themes.normalize_design(name), name)
            self.assertEqual(pgdata._normalize_preferences({"ui_design": name})["ui_design"], name)

    def test_every_enumeration_lists_the_new_designs(self):
        ids = [row["id"] for row in themes.DESIGNS]
        for path in (PG / "appearance.js", PG / "theme-editor.js"):
            listed = re.search(r"const DESIGN_IDS = \[([^\]]*)\]", path.read_text(encoding="utf-8"))
            self.assertIsNotNone(listed, path)
            self.assertEqual(re.findall(r'"([a-z]+)"', listed.group(1)), ids, path)
        for path in (PG / "index.html", PG / "quick" / "index.html", PG / "quick" / "layer.html", PG / "quick" / "quick.js"):
            text = path.read_text(encoding="utf-8")
            match = re.search(r"/\^\(([a-z|]+)\)\$/", text)
            self.assertIsNotNone(match, path)
            for name in NEW:
                self.assertIn(name, match.group(1).split("|"), path)
        html = (PG / "index.html").read_text(encoding="utf-8")
        for name in NEW:
            self.assertEqual(html.count(f'href="/designs/{name}.css'), 1, name)
            self.assertIn(f'data-design-css="{name}"', html)

    def test_thumbnails_quick_flavours_and_presets(self):
        appearance_css = (PG / "appearance.css").read_text(encoding="utf-8")
        quick = (PG / "designs" / "quick-extra.css").read_text(encoding="utf-8")
        editor = (PG / "theme-editor.js").read_text(encoding="utf-8")
        presets = re.search(r"const PRESETS = \[(.*?)\n\s*\];", editor, re.S).group(1)
        for name in NEW:
            self.assertIn(f".design-mini-{name} ", appearance_css, name)
            self.assertIn(f':root[data-design="{name}"]', quick, name)
            self.assertIn(f'ui_design: "{name}"', presets, name)
        for selector in _selectors(quick):
            for part in re.split(r",(?![^()]*\))", selector):
                self.assertTrue(part.strip().split()[0].startswith(":root[data-design="), part)

    def test_search_keywords_cover_the_new_designs(self):
        js = (PG / "appearance.js").read_text(encoding="utf-8")
        names = " ".join(f"{row['ru']} {row['en']}".lower() for row in themes.DESIGNS)
        for word in ("блокнот", "мессенджер", "ретро", "глина", "студия", "дзен", "notebook", "zen"):
            self.assertIn(word, names)
        self.assertIn("designs().map((row) => `${row.ru || \"\"} ${row.en || \"\"}`)", js)


class StylesheetTest(unittest.TestCase):
    NEEDLES = (".composer", ".msg-user .bubble", ".code-block-wrap", ".table-wrap", ".dd-pop", ".panel {",
               ".settings-tab-btn", ".mp-card", ".module-window", ".toast", "--sx-r", "max-width: 800px",
               "prefers-reduced-motion", 'data-motion="off"', ":focus-visible", "::-webkit-scrollbar", ".greeting h1",
               ".chat-item.active", ".send")

    def test_scoped_complete_and_motion_aware(self):
        for name in NEW:
            text = css(name)
            self.assertGreater(len(text), 12000, name)
            scope = f'[data-design="{name}"]'
            for selector in _selectors(text):
                for part in re.split(r",(?![^()]*\))", selector):
                    part = part.strip()
                    if not part or re.fullmatch(r"(from|to|\d+%)(\s*,\s*\d+%)*", part):
                        continue
                    self.assertTrue(part.split()[0].startswith(":root") and scope in part.split()[0], f"{name}: {part[:80]}")
            for needle in (f':root[data-design="{name}"][data-theme="light"]',) + self.NEEDLES:
                self.assertIn(needle, text, (name, needle))

    def test_only_bundled_fonts_and_no_remote_resources(self):
        for name in NEW:
            text = css(name)
            self.assertNotIn("@import", text, name)
            self.assertNotRegex(text, r"url\(\s*['\"]?(https?:)?//", name)
            for url in re.findall(r'url\("([^"]+)"\)', text):
                self.assertTrue(url.startswith("fonts/") and (PG / "designs" / url).is_file(), (name, url))

    def test_braces_balance(self):
        for name in list(NEW) + ["quick-extra"]:
            text = re.sub(r"/\*.*?\*/", "", css(name), flags=re.S)
            self.assertEqual(text.count("{"), text.count("}"), name)


class ContrastTest(unittest.TestCase):
    """Mirror of the color-mix() formulas (mix(a, b, wa) == color-mix(a wa%, b))."""

    def roles(self, name, mode, t, accent):
        bg, deep, panel, panel2 = (_hex(t[k]) for k in ("--bg", "--bg-deep", "--panel", "--panel-2"))
        text, muted, on_accent = _hex(t["--text"]), _hex(t["--muted"]), _hex(t["--on-accent"])
        dark = mode == "dark"
        if name == "notebook":
            if dark:
                paper, desk, card = _mix(bg, _hex("#31291e"), .80), _mix(deep, _hex("#1d1812"), .82), _mix(panel, _hex("#352c20"), .84)
                ink, note = text, _mix(accent, paper, .09)
            else:
                paper, desk, card = _mix(bg, _hex("#f6efe0"), .70), _mix(deep, _hex("#ece3cf"), .62), _mix(panel, _hex("#fbf6ea"), .76)
                ink, note = _mix(text, _hex("#3a2a12"), .88), _mix(accent, _hex("#fbf6ea"), .08)
            surf = {"paper": paper, "desk": desk, "card": card, "note": note}
            return [("ink", ink, surf), ("muted", muted, {"paper": paper, "card": card})]
        if name == "messenger":
            if dark:
                side, head, wall, bubble = _mix(panel, deep, .92), _mix(panel, deep, .94), _mix(bg, accent, .86), _mix(panel, text, .96)
            else:
                side = head = bubble = panel
                wall = _mix(_mix(bg, accent, .80), _hex("#d9e4c8"), .92)
            surf = {"side": side, "head": head, "wall": wall, "in": bubble}
            return [("text", text, surf), ("on-accent", on_accent, {"out": accent}),
                    ("accent-label", accent, {"side": side, "in": bubble})]
        if name == "retro":
            if dark:
                face, field = _mix(deep, _hex("#3a3a3a"), .70), _mix(bg, _hex("#1e1e1e"), .82)
                note = _mix(_hex("#ffffc0"), field, .12)
                sel, t1, t2 = _mix(accent, _hex("#000080"), .50), _mix(accent, _hex("#000080"), .55), _mix(accent, _hex("#0b4f8a"), .30)
            else:
                face, field, note = _mix(deep, _hex("#c3c3c3"), .18), _mix(panel, WHITE, .30), _hex("#ffffe1")
                sel, t1, t2 = _mix(accent, _hex("#000080"), .40), _mix(accent, _hex("#000080"), .40), _mix(accent, _hex("#1084d0"), .30)
            return [("text", text, {"face": face, "field": field, "note": note}),
                    ("white", WHITE, {"selection": sel, "title-1": t1, "title-2": t2})]
        if name == "clay":
            if dark:
                desk, canvas = _mix(deep, accent, .88), _mix(bg, accent, .90)
                clay, clay2, tint = _mix(panel, accent, .88), _mix(panel2, accent, .84), _mix(accent, panel, .22)
            else:
                desk, canvas = _mix(deep, accent, .86), _mix(bg, accent, .92)
                clay, clay2, tint = _mix(panel, accent, .94), _mix(panel2, accent, .90), _mix(accent, panel, .16)
            surf = {"desk": desk, "canvas": canvas, "clay": clay, "clay-2": clay2, "tint": tint}
            return [("text", text, surf), ("muted", muted, {"canvas": canvas, "clay": clay}), ("on-accent", on_accent, {"ball": accent})]
        if name == "studio":
            if dark:
                base, side, canvas = _mix(deep, BLACK, .86), _mix(deep, panel, .94), _mix(bg, deep, .92)
                raise_, sel = panel2, _mix(accent, panel, .16)
            else:
                base, side, canvas = deep, _mix(deep, panel, .70), panel
                raise_, sel = _mix(panel2, panel, .70), _mix(accent, panel, .11)
            surf = {"base": base, "side": side, "canvas": canvas, "panel": panel, "raise": raise_, "selected": sel}
            return [("text", text, surf), ("muted", muted, {"side": side, "canvas": canvas, "panel": panel, "raise": raise_}),
                    ("on-accent", on_accent, {"accent": accent})]
        if name == "zen":
            paper = _mix(bg, _hex("#17140f"), .88) if dark else _mix(bg, _hex("#f1ebdf"), .78)
            card = _mix(panel, paper, .80) if dark else _mix(panel, paper, .70)
            wash = _mix(text, paper, .04)
            surf = {"paper": paper, "card": card, "wash": wash}
            return [("text", text, surf), ("muted", muted, {"paper": paper, "card": card}),
                    ("paper-on-ink", paper, {"ink button": text}), ("on-accent", on_accent, {"seal": accent})]
        raise AssertionError(name)

    def test_text_keeps_aa_on_every_surface(self):
        failures = []
        for name in NEW:
            for mode in ("dark", "light"):
                for vid, tokens in _variants(mode):
                    for aid, row in themes.ACCENTS.items():
                        accent = _hex(row[mode])
                        for role, fg, surfaces in self.roles(name, mode, tokens, accent):
                            for sname, colour in surfaces.items():
                                ratio = _contrast(fg, colour)
                                if ratio < 4.5:
                                    failures.append(f"{name}/{mode}/{vid}/{aid}: {role} on {sname} {ratio:.2f}")
        self.assertFalse(failures, f"{len(failures)} failures\n" + "\n".join(failures[:60]))


if __name__ == "__main__":
    unittest.main()
