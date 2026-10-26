"""Alternative whole-interface designs (Classic, Aurora, Focus, Grid, Glass, Soft, Terminal, Brutal)."""
import re
import unittest
from pathlib import Path

from app import pgdata, themes

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"

def _selectors(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    depth, buf, out, stack = 0, "", [], []
    for ch in css:
        if ch == "{":
            head = buf.strip()
            stack.append(head)
            if not head.startswith("@") and not any(s.startswith("@keyframes") or s.startswith("@font-face") for s in stack[:-1]):
                out.append(head)
            buf = ""
        elif ch == "}":
            if stack:
                stack.pop()
            buf = ""
        elif ch == ";" :
            buf = ""
        else:
            buf += ch
    return out

class DesignsTest(unittest.TestCase):
    def test_catalogue_and_normalisation(self):
        ids = [row["id"] for row in themes.DESIGNS]
        self.assertEqual(ids[0], "classic")
        self.assertGreaterEqual(len(ids), 4)
        self.assertEqual(themes.normalize_design("aurora"), "aurora")
        self.assertEqual(themes.normalize_design("nope"), "classic")
        self.assertEqual(themes.normalize_design(None), "classic")
        self.assertIn("designs", themes.catalog())
        prefs = pgdata._normalize_preferences({"ui_design": "grid"})
        self.assertEqual(prefs["ui_design"], "grid")
        self.assertEqual(pgdata._normalize_preferences({"ui_design": "x"})["ui_design"], "classic")

    def test_every_design_is_linked_and_scoped(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        js = (PG / "appearance.js").read_text(encoding="utf-8")
        for row in themes.DESIGNS[1:]:
            name = row["id"]
            self.assertIn(f"/designs/{name}.css", html)
            self.assertIn(f'"{name}"', js)
            css = (PG / "designs" / f"{name}.css").read_text(encoding="utf-8")
            self.assertGreater(len(css), 5000, name)
            scope = f':root[data-design="{name}"]'
            for selector in _selectors(css):
                for part in re.split(r",(?![^()]*\))", selector):
                    part = part.strip()
                    if not part or re.fullmatch(r"(from|to|\d+%)(\s*,\s*\d+%)*", part):
                        continue
                    first = part.split()[0]
                    self.assertTrue(first.startswith((":root", ":where(:root")) and f'[data-design="{name}"]' in first, f"{name}: unscoped selector {part[:80]!r}")

def _srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def _lin_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

def _hex(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))

def _oklch(rgb):
    import math
    r, g, b = (_srgb_to_lin(c) for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360

def _from_oklch(L, C, H):
    import math
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return tuple(_lin_to_srgb(c) for c in (r, g, bl))

def _mix(a, b, wa):
    return tuple(x * wa + y * (1 - wa) for x, y in zip(a, b))

def _lum(rgb):
    r, g, b = (_srgb_to_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

class GlassContrastTest(unittest.TestCase):
    """Worst case of Glass v2: text over the thinnest pane sitting on the
    brightest (dark mode) / darkest (light mode) aurora stop, mirroring the
    OKLCH re-lighting in designs/glass.css."""

    def _blobs(self, mode, accent, accent2):
        L1, C1, H1 = _oklch(accent)
        L2, C2, H2 = _oklch(accent2)
        if mode == "dark":
            return [(_from_oklch(.56, min(C1 * 2.3, .2), H1), .85), (_from_oklch(.54, min(C2 * 2.3, .2), H2), .8),
                    (_from_oklch(.5, min(C1 * 2.1, .19), H1 - 48), .7), (_from_oklch(.46, min(C2 * 2, .18), H2 + 36), .62),
                    (_from_oklch(.78, min(C1 * 1.4, .14), H1 + 24), .34)]
        return [(_from_oklch(.8, min(C1 * 1.1, .15), H1), .8), (_from_oklch(.8, min(C2 * 1.1, .15), H2), .72),
                (_from_oklch(.84, min(C1, .13), H1 + 58), .66), (_from_oklch(.86, min(C2 * .9, .12), H2 - 40), .6)]

    def test_text_on_glass_panes_keeps_aa(self):
        failures = []
        for mode in ("dark", "light"):
            base_tokens = dict(themes._DARK_BASE if mode == "dark" else themes._LIGHT_BASE)
            for variant in themes.VARIANTS[mode]:
                tokens = {**base_tokens, **variant["tokens"]}
                panel, text, muted = _hex(tokens["--panel"]), _hex(tokens["--text"]), _hex(tokens["--muted"])
                pushed = _mix(muted, text, .52)
                for accent_id, row in themes.ACCENTS.items():
                    accent, accent2 = _hex(row[mode]), _hex(row[mode + "2"])
                    base = _hex(tokens["--bg-deep"] if mode == "dark" else tokens["--bg"])
                    walls = [_mix(color, base, alpha) for color, alpha in self._blobs(mode, accent, accent2)]

                    wall = max(walls, key=_lum) if mode == "dark" else min(walls, key=_lum)
                    brightness = 1.03 if mode == "dark" else 1.08
                    wall = tuple(min(1, c * brightness) for c in wall)
                    contrast_surface = variant["id"] == "contrast"

                    side_alpha = (.78 if contrast_surface else .62) if mode == "dark" else (.78 if contrast_surface else .62)
                    white = (1.0, 1.0, 1.0)
                    side_fill = _mix(panel, white, .94) if mode == "dark" else panel
                    pop_fill = _mix(panel, white, .95) if mode == "dark" else panel
                    panes = {"sidebar": _mix(side_fill, wall, side_alpha), "sheet": _mix(panel, wall, .74 if mode == "dark" else .80),
                             "menu": _mix(pop_fill, wall, .94)}
                    for pane, colour in panes.items():
                        for name, fg in (("text", text), ("secondary", pushed)):
                            ratio = _contrast(fg, colour)
                            if ratio < 4.5:
                                failures.append(f"{mode}/{variant['id'] or 'default'}/{accent_id} {name} on {pane}: {ratio:.2f}")
        self.assertFalse(failures, "\n".join(failures[:40]))

    def test_glass_v2_material_contract(self):
        css = (PG / "designs" / "glass.css").read_text(encoding="utf-8")

        for needle in ("backdrop-filter: var(--g-f-composer)", "backdrop-filter: var(--g-f-thick)", "feTurbulence",
                       "mask-composite: exclude", "m-paused", "prefers-reduced-motion", "@supports (color: oklch(from red l c h))",
                       "--mx", "data-motion"):
            self.assertIn(needle, css)

        self.assertNotIn("backdrop-filter: var(--g-f-sheet)", css)
        self.assertNotIn("topbar.appbar {\n  -webkit-backdrop-filter", css)

if __name__ == "__main__":
    unittest.main()
