"""Round 25: every entrance of the installer is ONE motion that ends where it rests.

Complements tests/round25_installer_test.py (fit state, reveal, ring placement in WebGL):

* the entrances end without scale or blur — on their last frame Chromium re-rasterised the
  panel's text and re-sampled its backdrop-filter glass, a visible snap at 125/150 %;
* the panel sits on whole pixels (no half-pixel text that snaps differently while composited);
* frame by frame (animations paused and seeked in 40 ms steps, 100/125/150 % device
  scale) the panel, the title and the primary button of the boot and of a screen switch
  converge without ever reversing direction and are at rest at the end;
* the edition cards show their illustrations: ``.card`` is a <button>, whose UA style
  (align-items: flex-start) shrank ``.card-art`` to its 2 px border;
* a retried uninstall starts its progress at 0 (S.lastPct was kept from the first attempt);
* the CSS fallback ring (no WebGL) lies between the title bar and the panel and shows the
  percentage as its dash offset.

The browser tests need Playwright + Chromium and SKIP otherwise.
"""
from __future__ import annotations

import asyncio
import re
import unittest

from tests.installer_layout_test import UI, _playwright_ok, chromium_path

UI_DIR = UI.parent

def read(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")

def function_body(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(name)

class EntranceStaticTest(unittest.TestCase):
    def test_entrances_end_without_scale_or_blur(self):
        css = read("style.css")
        panel_in = re.search(r"@keyframes panel-in \{[^\n]*\}", css).group(0)
        self.assertNotIn("scale", panel_in)
        self.assertNotIn("filter", panel_in)
        screen_rules = re.findall(r"^\.screen(?:\.active)? \{.*?\}", css, re.S | re.M)
        self.assertTrue(screen_rules)
        for rule in screen_rules:
            self.assertNotIn("filter", rule)

    def test_edition_cards_stretch_their_art(self):
        rule = re.search(r"^\.card \{[^}]*\}", read("style.css"), re.M).group(0)
        self.assertIn("align-items: stretch", rule)

    def test_uninstall_retry_resets_progress(self):
        self.assertIn("S.lastPct = 0", function_body(read("app.js"), "startUninstall"))

SEEK = r"""(click) => {
  if (click) { document.querySelector(click).click(); }
  void document.body.offsetWidth; getComputedStyle(document.getElementById('panel')).transform;
  const q = s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height].map(v => Math.round(v * 100) / 100); };
  const anims = document.getAnimations().filter(a => {
    const t = a.effect && a.effect.getComputedTiming(); return t && isFinite(t.endTime); });
  anims.forEach(a => a.pause());
  const out = [];
  for (let t = 0; t <= 2000; t += 40) {
    anims.forEach(a => { a.currentTime = t; });
    out.push({ scr: document.documentElement.getAttribute('data-screen'),
               panel: q('#panel'), h1: q('.screen.active h1'), btn: q('.screen.active .btn.primary') });
  }
  anims.forEach(a => a.finish());
  return out;
}"""

def reversals(frames, key, axis):
    """How many times a coordinate changed direction (a settle is monotonic)."""
    values = [f[key][axis] for f in frames if f[key]]
    steps = [b - a for a, b in zip(values, values[1:]) if abs(b - a) > 0.05]
    return sum(1 for a, b in zip(steps, steps[1:]) if (a > 0) != (b > 0))

async def sample(scale: float):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium_path(),
                                          args=["--no-sandbox", "--hide-scrollbars", "--disable-webgl"])
        ctx = await browser.new_context(viewport={"width": 960, "height": 620}, device_scale_factor=scale)
        page = await ctx.new_page()
        page.set_default_timeout(120000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(f"{UI.as_uri()}?mock=1&lang=ru", wait_until="load")
        for _ in range(300):
            if await page.evaluate("document.documentElement.classList.contains('ready')"):
                break
            await page.wait_for_timeout(50)
        await page.wait_for_timeout(100)
        boot = await page.evaluate(SEEK, None)
        await page.wait_for_timeout(1600)
        rest = await page.evaluate("document.querySelector('#panel').getBoundingClientRect().toJSON()")
        switch = await page.evaluate(SEEK, ".screen.active .btn.primary")
        card = await page.evaluate("document.querySelector('.card-art.proxy').getBoundingClientRect().width")
        await browser.close()
    return boot, rest, switch, card, errors

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class EntranceMotionTest(unittest.TestCase):
    def check(self, scale: float):
        boot, rest, switch, card, errors = asyncio.run(sample(scale))
        self.assertEqual(errors, [])
        for name, frames in (("boot", boot), ("switch", switch)):
            for key in ("panel", "h1", "btn"):
                for axis in (0, 1, 2, 3):
                    self.assertEqual(reversals(frames, key, axis), 0, f"{name} @{scale}: {key} jerked back")
            self.assertEqual(frames[-1]["panel"], frames[-2]["panel"], f"{name} @{scale}: still moving at the end")

        self.assertEqual(boot[-1]["panel"], [rest["x"], rest["y"], rest["width"], rest["height"]])
        self.assertEqual(switch[-1]["scr"], "edition")
        self.assertGreater(card, 100, "the edition card's illustration collapsed")
        for v in boot[-1]["panel"]:
            self.assertEqual(v, round(v), "the panel is not on whole pixels")

    def test_100(self):
        self.check(1)

    def test_125(self):
        self.check(1.25)

    def test_150(self):
        self.check(1.5)

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class CssRingTest(unittest.TestCase):
    def test_fallback_ring_fits_band_and_shows_progress(self):
        async def run():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium_path(),
                                                  args=["--no-sandbox", "--hide-scrollbars", "--disable-webgl"])
                out = []
                for w, h in ((960, 620), (860, 540), (1280, 800)):
                    page = await browser.new_page(viewport={"width": w, "height": h})
                    await page.goto(f"{UI.as_uri()}?mock=1&shot=1&lang=en&screen=progress&pct=62", wait_until="load")
                    await page.wait_for_timeout(900)
                    out.append(await page.evaluate("""(() => {
                      const ring = document.querySelector('.css-star .pring circle').getBoundingClientRect();
                      const bar = document.querySelector('.titlebar').getBoundingClientRect();
                      const panel = document.querySelector('#panel').getBoundingClientRect();
                      const arc = document.getElementById('pring-arc');
                      return { ring: [ring.top, ring.bottom], bar: bar.bottom, panel: panel.top,
                               offset: parseFloat(arc.style.strokeDashoffset),
                               busy: document.documentElement.classList.contains('ring-busy') };
                    })()"""))
                    await page.close()
                await browser.close()
                return out
        for r in asyncio.run(run()):
            self.assertGreaterEqual(r["ring"][0], r["bar"] + 4, r)
            self.assertLessEqual(r["ring"][1], r["panel"] - 4, r)
            self.assertFalse(r["busy"])
            self.assertAlmostEqual(r["offset"], 38, delta=0.5)

if __name__ == "__main__":
    unittest.main()
