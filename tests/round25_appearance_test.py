"""Round 25 (appearance): clipped texts in Settings show their full text on hover and on keyboard focus.

The design cards in Settings -> Appearance clamp their description to three lines; the card's title
(appearance.js) and the overflow layer of tooltip.js give the whole text in the styled tooltip, in every
design and both languages. Any other ellipsis / line clamp that actually hides text gets the same.

    cd /workspace/work/memuai && python3 -m unittest tests.round25_appearance_test
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PGSTATIC = ROOT / "app" / "pgstatic"
DESIGNS = ("classic", "aurora", "focus", "grid", "glass", "soft", "terminal", "brutal")

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""

def _playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True

class Sources(unittest.TestCase):
    def test_cards_carry_name_and_full_description(self) -> None:
        js = (PGSTATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('title="${esc(`${pick(row)}. ${pick(row, "note")}`)}"', js, "design card title = name + full note")
        self.assertIn('<label class="variant-option" title="${esc([pick(row), pick(row, "note")]', js,
                      "theme variant card title names the variant too")

    def test_tooltip_keeps_line_breaks(self) -> None:
        css = (PGSTATIC / "appearance.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.memu-tooltip-text \{[^}]*white-space: pre-line")

    def test_no_pseudo_element_inside_is_where(self) -> None:
        """A pseudo-element inside :is()/:where() invalidates the whole rule (terminal's blinking cursor ignored
        «no animation» because of it)."""
        files = [PGSTATIC / name for name in ("design.css", "appearance.css", "motion.css", "titlebar.css", "docs.css")]
        files += sorted((PGSTATIC / "designs").glob("*.css"))
        pattern = re.compile(r":(?:is|where)\([^)]*::")
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(pattern.search(line), f"{path.name}:{number}: {line.strip()[:120]}")

    def test_terminal_accent_text_uses_readable_token(self) -> None:
        css = (PGSTATIC / "designs" / "terminal.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?<![-\w])color: var\(--accent\)", css), "accent text goes through --t-hi")
        light = css[css.index(':root[data-design="terminal"][data-theme="light"] {'):]
        self.assertIn("--t-hi: color-mix(in srgb, var(--accent)", light[:light.index("}")])

    def test_filled_danger_button_has_dark_ink_in_dark_themes(self) -> None:
        for name in ("soft", "brutal", "glass"):
            css = (PGSTATIC / "designs" / f"{name}.css").read_text(encoding="utf-8")
            self.assertRegex(css, r'\[data-design="%s"\](?:\[data-theme="dark"\]|:not\(\[data-theme="light"\]\)) \.pbtn\.danger \{ color: color-mix' % name, name)

    def test_studio_focus_ring_only_for_radio_labels(self) -> None:
        css = (PGSTATIC / "appearance.css").read_text(encoding="utf-8")
        self.assertNotIn(".theme-studio label:has(input:focus-visible)", css)
        self.assertIn('.theme-studio label:has(input[type="radio"]:focus-visible)', css)

CONTRAST_JS = """() => {
  const tip = document.getElementById('memu-tooltip');
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const rgba = (value) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1);
    return Array.from(ctx.getImageData(0, 0, 1, 1).data); };
  const page = rgba(getComputedStyle(document.body).backgroundColor);
  const back = rgba(getComputedStyle(tip).backgroundColor);
  const a = back[3] / 255, bodyA = page[3] / 255;
  const base = page.slice(0, 3).map((c) => c * bodyA + (1 - bodyA) * 128);
  const bg = back.slice(0, 3).map((c, i) => c * a + base[i] * (1 - a));
  const fg = rgba(getComputedStyle(tip.querySelector('.memu-tooltip-text')).color).slice(0, 3);
  const lum = (rgb) => { const [r, g, b] = rgb.map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}"""

ELEMENT_CONTRAST_JS = """(selector) => {
  const el = Array.from(document.querySelectorAll(selector)).find((node) => node.offsetParent);
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const rgba = (value) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = 'rgba(0,0,0,0)'; ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1);
    return Array.from(ctx.getImageData(0, 0, 1, 1).data); };
  const layers = [];
  for (let node = el; node; node = node.parentElement) { const c = rgba(getComputedStyle(node).backgroundColor); if (c[3]) { layers.push(c); if (c[3] === 255) break; } }
  let bg = [255, 255, 255];
  for (let i = layers.length - 1; i >= 0; i--) { const a = layers[i][3] / 255; bg = bg.map((v, k) => layers[i][k] * a + v * (1 - a)); }
  const f = rgba(getComputedStyle(el).color), fa = f[3] / 255;
  const fg = f.slice(0, 3).map((v, k) => v * fa + bg[k] * (1 - fa));
  const lum = (rgb) => { const [r, g, b] = rgb.map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}"""

TIP_JS = """() => { const t = document.getElementById('memu-tooltip'); if (!t || t.hidden) return null;
  const r = t.getBoundingClientRect();
  return {text: t.querySelector('.memu-tooltip-text').innerText, left: r.left, right: r.right, top: r.top, bottom: r.bottom,
          width: r.width, vw: innerWidth, vh: innerHeight}; }"""

@unittest.skipUnless(_chromium() and _playwright(), "Chromium / Playwright is not available")
class SettingsTooltips(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright
        from tests.appearance_fixture import build_fixture
        from app import themes
        cls.catalog = {row["id"]: row for row in themes.catalog()["designs"]}
        cls.fixture = build_fixture(Path(tempfile.mkdtemp(prefix="memu-r25a-")))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls._pw.stop()

    def open_appearance(self, lang: str, design: str = "classic", theme: str = "dark", width: int = 1280):
        ctx = self.browser.new_context(viewport={"width": width, "height": 820})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(f"""try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}');
          localStorage.setItem('memu_design', '{design}');
          localStorage.setItem('qa_prefs', JSON.stringify({{language: '{lang}', theme: '{theme}', ui_design: '{design}', ui_motion: 'reduced'}}));
        }} catch (_) {{}}""")
        page.goto(self.fixture.as_uri(), wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('boot-ready') && typeof showSettings === 'function'", timeout=90000)
        page.evaluate("currentSettingsTab = 'appearance'; showSettings(true)")
        page.wait_for_selector(".design-option", timeout=10000)
        self.assertEqual(page.evaluate("document.documentElement.dataset.design"), design)
        return page, errors

    def wait_tip(self, page):
        for _ in range(30):
            tip = page.evaluate(TIP_JS)
            if tip:
                return tip
            time.sleep(0.05)
        return None

    def assert_inside(self, tip, label: str) -> None:
        self.assertGreaterEqual(tip["left"], 0, label)
        self.assertLessEqual(tip["right"], tip["vw"], label)
        self.assertGreaterEqual(tip["top"], 0, label)
        self.assertLessEqual(tip["bottom"], tip["vh"], label)

    def test_design_cards_hover_every_design_both_languages(self) -> None:
        for lang, theme in (("ru", "dark"), ("en", "light")):
            page, errors = self.open_appearance(lang, theme=theme)
            for design in DESIGNS:
                with self.subTest(lang=lang, design=design):
                    card = page.locator(f".design-option[data-design-card='{design}']")
                    card.evaluate("(el) => el.scrollIntoView({block: 'center'})")
                    card.click()
                    page.wait_for_function(f"document.documentElement.dataset.design === '{design}'", timeout=5000)
                    page.mouse.move(0, 0)
                    time.sleep(0.15)
                    page.locator(f".design-option[data-design-card='{design}']").hover()
                    tip = self.wait_tip(page)
                    self.assertIsNotNone(tip, "hovering a design card shows the styled tooltip")
                    row = self.catalog[design]
                    self.assertEqual(tip["text"], f"{row[lang]}. {row['note_' + lang]}", "the whole description, not the clamp")
                    self.assert_inside(tip, design)
                    self.assertGreaterEqual(tip["width"], 200, "a readable width, not a narrow column")
                    self.assertGreaterEqual(page.evaluate(CONTRAST_JS), 4.5, "tooltip text is readable in this design")
                    self.assertFalse(page.evaluate(f"document.querySelector(\"[data-design-card='{design}']\").hasAttribute('title')"),
                                     "the native title is moved aside so it never doubles the styled one")
            self.assertEqual(errors, [])

    def test_design_contrast_and_motion_fixes(self) -> None:
        page, _ = self.open_appearance("ru", design="terminal", theme="light")
        self.assertGreaterEqual(page.evaluate(ELEMENT_CONTRAST_JS, ".sx-nav .settings-tab-btn.active .sx-nav-text"), 4.5,
                                "terminal light: the active settings page in the navigation")
        page.evaluate("document.documentElement.dataset.motion = 'off'")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.brand'), '::after').animationName"), "none",
                         "terminal: the blinking cursor stops with animations off")
        page.evaluate("setTheme('dark', false)")
        for design in ("soft", "brutal", "glass"):
            with self.subTest(design=design):
                page.evaluate(f"document.documentElement.dataset.design = '{design}'; void askConfirm('?', '?'); 0")
                page.wait_for_selector(".pbtn.danger")
                self.assertGreaterEqual(page.evaluate(ELEMENT_CONTRAST_JS, ".pbtn.danger"), 4.5, "dark theme: the Delete button label")
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)

    def test_design_card_keyboard_focus(self) -> None:
        page, _ = self.open_appearance("ru")
        page.evaluate("document.querySelector('[name=\"appearance-accent\"]:checked').focus()")
        page.keyboard.press("Tab")
        self.assertEqual(page.evaluate("document.activeElement.name"), "appearance-design")
        tip = self.wait_tip(page)
        self.assertIsNotNone(tip, "keyboard focus on a design card shows the tooltip")
        self.assertTrue(tip["text"].startswith(self.catalog["classic"]["ru"] + ". "))
        self.assert_inside(tip, "focus")
        page.keyboard.press("Tab")
        time.sleep(0.1)
        self.assertIsNone(page.evaluate(TIP_JS), "leaving the card hides it")

    def test_clipped_text_without_title_gets_full_text(self) -> None:
        page, _ = self.open_appearance("en", width=860)
        page.evaluate("""() => {
          const pane = document.querySelector('.settings-tab-pane.active');
          pane.insertAdjacentHTML('afterbegin', `
            <button type="button" id="r25-clip" style="width:120px;display:flex"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">A rather long label that cannot fit</span><span aria-hidden="true">✓</span></button>
            <button type="button" id="r25-fit" style="width:400px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Short</span></button>
            <div id="r25-plain" style="width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Some plain clipped text here</div>`);
        }""")
        page.hover("#r25-clip")
        tip = self.wait_tip(page)
        self.assertIsNotNone(tip)
        self.assertEqual(tip["text"], "A rather long label that cannot fit", "decorative aria-hidden marks are left out")
        page.mouse.move(0, 0)
        page.hover("#r25-fit")
        time.sleep(0.7)
        self.assertIsNone(page.evaluate(TIP_JS), "text that fits gets no tooltip")
        page.mouse.move(0, 0)
        page.hover("#r25-plain")
        tip = self.wait_tip(page)
        self.assertEqual(tip and tip["text"], "Some plain clipped text here", "a clipped text outside a control too")

    def test_clipped_select_value_in_settings(self) -> None:
        page, _ = self.open_appearance("ru", width=860)
        page.evaluate("document.querySelector(\"[data-set-tab='search']\").click()")

        page.add_style_tag(content=".sx-shell .settings-tab-pane.active .pretty-select-button { width: 150px !important;"
                                   " max-width: 150px !important; }")
        full = page.wait_for_function("""() => {
          const el = Array.from(document.querySelectorAll('.sx-shell .settings-tab-pane.active *')).find((node) => {
            const s = getComputedStyle(node);
            return node.offsetParent && s.textOverflow === 'ellipsis' && node.scrollWidth > node.clientWidth + 1; });
          if (!el) return null; el.id = 'r25-clipped-value'; return el.textContent.replace(/\\s+/g, ' ').trim(); }""", timeout=10000).json_value()
        page.hover("#r25-clipped-value")
        tip = self.wait_tip(page)
        self.assertIsNotNone(tip, "the clipped search service name in its select shows in full")
        self.assertIn(full, tip["text"])

if __name__ == "__main__":
    unittest.main()
