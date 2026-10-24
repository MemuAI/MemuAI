"""Round 24 designs: Glass v3 and the three new designs (Мягкий / soft, Терминал / terminal,
Брутализм / brutal).

- registration everywhere a design is enumerated (catalogue, preference normalisation, first-paint
  regexes of the main and Quick Ask windows, quick.js, appearance.js, picker thumbnails, stylesheets);
- every new stylesheet is scoped, has a light branch, respects the motion settings and keeps
  keyboard focus visible;
- Glass v3: no vignette / conic sweep darkening the area around the composer, the composer is a
  plain floating pill without an accent halo;
- WCAG AA: body text on the key surfaces of each new design, recomputed from the same color-mix()
  formulas, for every built-in variant and accent of both modes.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import pgdata, themes
from tests.designs_test import _contrast, _hex, _mix, _selectors

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
NEW = ("soft", "terminal", "brutal")
WHITE, BLACK, CREAM = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), _hex("#fff3d6")


def css(name):
    return (PG / "designs" / f"{name}.css").read_text(encoding="utf-8")


class RegistrationTest(unittest.TestCase):
    def test_catalogue_has_the_new_designs_with_russian_names(self):
        rows = {row["id"]: row for row in themes.DESIGNS}
        for name, ru in (("soft", "Мягкий"), ("terminal", "Терминал"), ("brutal", "Брутализм")):
            self.assertIn(name, rows)
            self.assertEqual(rows[name]["ru"], ru)
            for key in ("en", "note_ru", "note_en"):
                self.assertTrue(rows[name].get(key), (name, key))
            self.assertEqual(themes.normalize_design(name), name)
            self.assertEqual(pgdata._normalize_preferences({"ui_design": name})["ui_design"], name)
        self.assertEqual(len(rows), len(themes.DESIGNS), "duplicate design ids")

    def test_every_enumeration_lists_every_design(self):
        ids = [row["id"] for row in themes.DESIGNS]
        js = (PG / "appearance.js").read_text(encoding="utf-8")
        listed = re.search(r"const DESIGN_IDS = \[([^\]]*)\]", js).group(1)
        self.assertEqual(re.findall(r'"([a-z]+)"', listed), ids)
        alt = ids[1:]
        for path in (PG / "index.html", PG / "quick" / "index.html", PG / "quick" / "quick.js"):
            text = path.read_text(encoding="utf-8")
            match = re.search(r"/\^\(([a-z|]+)\)\$/", text)
            self.assertIsNotNone(match, path)
            self.assertEqual(sorted(match.group(1).split("|")), sorted(alt), path)
        html = (PG / "index.html").read_text(encoding="utf-8")
        for name in alt:
            self.assertIn(f'href="/designs/{name}.css', html)
        appearance_css = (PG / "appearance.css").read_text(encoding="utf-8")
        for name in ids:
            self.assertIn(f".design-mini-{name}", appearance_css, name)
        # the picker search keywords know the new names: since round 26 they
        # are built from the design catalogue (ru + en names) at render time
        self.assertIn("designs().map((row) => `${row.ru || \"\"} ${row.en || \"\"}`)", js)
        names = " ".join(f"{row['ru']} {row['en']}".lower() for row in themes.DESIGNS)
        for word in ("мягкий", "терминал", "брутализм"):
            self.assertIn(word, names)

    def test_quick_window_gets_the_new_designs(self):
        quick = (PG / "quick" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/designs/quick-extra.css', quick)
        self.assertLess(quick.index("/quick/quick.css"), quick.index("/designs/quick-extra.css"))
        text = (PG / "designs" / "quick-extra.css").read_text(encoding="utf-8")
        for name in NEW:
            self.assertIn(f':root[data-design="{name}"]', text)
        for selector in _selectors(text):
            for part in re.split(r",(?![^()]*\))", selector):
                first = part.strip().split()[0]
                self.assertTrue(first.startswith(":root[data-design="), part)


class NewDesignStylesheetTest(unittest.TestCase):
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
            for needle in (f':root[data-design="{name}"][data-theme="light"]', "prefers-reduced-motion", 'data-motion="off"',
                           ":focus-visible", "::-webkit-scrollbar", ".composer", ".msg-user .bubble", ".code-block-wrap",
                           ".table-wrap", ".dd-pop", ".panel {", ".settings-tab-btn", ".mp-card", ".module-window",
                           ".toast", "--sx-r", "max-width: 800px"):
                self.assertIn(needle, text, (name, needle))
            # bundled fonts only (offline app): every url() points into designs/fonts
            for url in re.findall(r'url\("([^"]+)"\)', text):
                self.assertTrue(url.startswith("fonts/") and (PG / "designs" / url).is_file(), url)


class GlassV3Test(unittest.TestCase):
    def test_no_darkening_around_the_composer(self):
        text = css("glass")
        vignettes = re.findall(r"--g-vignette:\s*([^;]+);", text)
        self.assertTrue(vignettes)
        for value in vignettes:
            self.assertNotRegex(value, r"rgba\(0,\s*0,\s*0", "the vignette must not darken the window")
        self.assertNotIn("conic-gradient", text)
        composer = re.search(r':root\[data-design="glass"\] \.composer \{(.*?)\}', text, re.S).group(1)
        self.assertNotIn("inset 0 -10px", composer)
        self.assertNotRegex(composer, r"0 22px 56px -30px color-mix\(in srgb, var\(--accent\)")
        self.assertIn("v3", text[:400])


def _variants(mode):
    base = dict(themes._DARK_BASE if mode == "dark" else themes._LIGHT_BASE)
    for variant in themes.VARIANTS[mode]:
        yield variant["id"] or "default", {**base, **variant["tokens"]}


class NewDesignContrastTest(unittest.TestCase):
    """Mirror of the color-mix() formulas in soft/terminal/brutal.css (mix(a, b, wa) == color-mix(a wa%, b))."""

    def surfaces(self, name, mode, t, accent, accent2):
        bg, deep, panel = _hex(t["--bg"]), _hex(t["--bg-deep"]), _hex(t["--panel"])
        text, muted = _hex(t["--text"]), _hex(t["--muted"])
        if name == "terminal":
            if mode == "dark":
                ink, dim = _mix(text, accent, .88), _mix(muted, text, .70)
                surf = {"canvas": _mix(_mix(deep, BLACK, .70), accent, .95), "panel": _mix(_mix(panel, BLACK, .78), accent, .93),
                        "bg": _mix(_mix(deep, BLACK, .55), accent, .96)}
            else:
                ink, dim = text, _mix(muted, text, .80)
                surf = {"canvas": _mix(bg, accent, .95), "panel": _mix(panel, accent, .94), "bg": _mix(deep, accent, .92)}
            return {"ink": (ink, surf), "dim": (dim, {k: surf[k] for k in ("canvas", "bg")})}
        if name == "soft":
            if mode == "dark":
                surf = {"canvas": _mix(bg, accent, .94), "c1": _mix(_mix(panel, accent, .92), WHITE, .96),
                        "c2": _mix(_mix(panel, accent, .86), WHITE, .92), "secondary": _mix(accent, panel, .26),
                        "surface": _mix(deep, accent, .90)}
            else:
                surf = {"canvas": _mix(bg, accent, .97), "c1": _mix(panel, accent, .93), "c2": _mix(panel, accent, .89),
                        "secondary": _mix(accent, panel, .18), "surface": _mix(bg, accent, .86)}
            return {"text": (text, surf)}
        if name == "brutal":
            if mode == "dark":
                surf = {"paper": _mix(bg, accent, .92), "card": _mix(panel, accent, .94), "block": _mix(accent, panel, .24),
                        "block2": _mix(accent2, panel, .22), "desk": _mix(deep, accent2, .86)}
            else:
                surf = {"paper": _mix(_mix(bg, CREAM, .82), accent, .96), "card": _mix(panel, CREAM, .96),
                        "block": _mix(accent, WHITE, .24), "block2": _mix(accent2, WHITE, .22),
                        "desk": _mix(_mix(accent2, CREAM, .26), bg, .86)}
            return {"text": (text, surf)}
        raise AssertionError(name)

    def test_text_keeps_aa_on_every_surface(self):
        failures = []
        for name in NEW:
            for mode in ("dark", "light"):
                for vid, tokens in _variants(mode):
                    for aid, row in themes.ACCENTS.items():
                        accent, accent2 = _hex(row[mode]), _hex(row[mode + "2"])
                        for role, (fg, surfaces) in self.surfaces(name, mode, tokens, accent, accent2).items():
                            for sname, colour in surfaces.items():
                                ratio = _contrast(fg, colour)
                                if ratio < 4.5:
                                    failures.append(f"{name}/{mode}/{vid}/{aid}: {role} on {sname} {ratio:.2f}")
        self.assertFalse(failures, "\n".join(failures[:40]))


if __name__ == "__main__":
    unittest.main()
