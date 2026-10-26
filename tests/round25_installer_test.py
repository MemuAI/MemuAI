"""Round 25: the installer appears once and moves once; a clean progress ring.

User report: "the interface appears with an animation, then JERKS and only after that is
correct — the same on every page change" and "the progress ring looks imperfect: a thin
circle with a bright arc at the top that looks clipped/blurry".

Root causes (see /workspace/work/r25notes/a1-installer.md):

* ``fit()`` measured the active screen with ``getBoundingClientRect``/``scrollHeight``
  while the entrance animations were still translating its children and scaling the
  panel, saw "overflow", shrank the stage (``--stage-s`` ≈ 0.87) and switched on
  ``fit-dense``/``fit-tight``/``fit-scroll``; the delayed refit a second later undid it —
  the jump at the end of every entrance. It now reads layout boxes only.
* The panel animated in at page load, before the bridge answered: texts, the RU/EN thumb
  and the fit arrived afterwards. The page now stays hidden until ``html.ready``
  (bridge answered, fonts loaded, first screen laid out).
* The ring was a Gaussian 1-px line drawn before the tone curve (blurry), a hard cut at
  12 o'clock, and its radius put the top of the ring under the title bar / at the window
  edge. Now: a crisp stroke in device pixels with round caps after the tone curve, sized to
  the free band above the panel, a "busy" circling arc while the size of the work is unknown.

Static checks run everywhere; the browser checks need Playwright + Chromium and SKIP otherwise.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "installer" / "ui"
CHROME_CANDIDATES = [os.environ.get("MEMUAI_CHROMIUM", ""), "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     "/usr/bin/google-chrome", "/workspace/work/chromium/chrome"]

def _chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""

def _playwright_ok() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(_chrome())

async def _wait(page, expression: str, timeout: float = 60.0) -> None:
    """``page.wait_for_function`` evaluates a string, which the page's CSP forbids."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await page.evaluate(f"() => !!({expression})"):
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {expression}")
        await asyncio.sleep(0.05)

def _read(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")

class StaticContract(unittest.TestCase):
    def test_fit_measures_layout_not_transforms(self):
        app = _read("app.js")
        body = app[app.index("function overflowOf"):app.index("function fit()")]
        self.assertNotIn("getBoundingClientRect", body, "entrance transforms must not count as overflow")
        self.assertNotIn("scrollHeight", body)
        self.assertIn("offsetTop", body)

        show = app[app.index("function show(screen"):app.index("function goBack")]
        self.assertNotIn("refit(html.classList.contains(\"reduce-motion\") ? 30 : 1000)", show)
        self.assertIn("if (!modeChange) fit();", show)

    def test_interface_waits_for_bridge_and_fonts(self):
        css, app = _read("style.css"), _read("app.js")
        self.assertIn("html:not(.ready) .stage { visibility: hidden; }", css)
        self.assertIn("html.ready .panel { animation: panel-in", css)
        self.assertIsNone(re.search(r"(?m)^\.panel \{[^}]*animation: panel-in", css), "the panel must not animate in before ready")
        self.assertIn("function fontsReady()", app)
        self.assertIn("function reveal()", app)
        self.assertIn("setTimeout(reveal, 8000)", app, "a silent bridge never leaves an empty window")

    def test_ring_is_a_stroked_arc(self):
        scene, app, html = _read("scene.js"), _read("app.js"), _read("index.html")
        self.assertIn("vec2 arcDist(", scene)
        self.assertIn("uniform float u_ringR; uniform float u_px; uniform vec4 u_arcs; uniform float u_busy;", scene)
        self.assertNotIn("uniform float u_ring;", scene, "the old Gaussian ring is gone")
        self.assertIn("RING_RADIUS", scene)
        self.assertIn("setRing: function (value, visible, busy)", scene)
        self.assertIn("spec.ring", app)
        self.assertIn('class="pring-arc" id="pring-arc"', html, "the CSS fallback ring is an SVG stroke")
        self.assertIn("stroke-linecap: round", _read("style.css"))

    def test_uninstall_phase_texts(self):
        text = _read("i18n.js")
        for lang in ("en", "ru"):
            block = re.search(lang + r": \{(.*?)\n  \}", text, re.S).group(1)
            keys = set(re.findall(r'"([\w.]+)":', block))
            for key in ("un.phase.preparing", "un.phase.done"):
                self.assertIn(key, keys, f"{lang}: {key} (the bridge reports both phases)")

OBSERVE = r"""
window.__fitLog = [];
(function () {
  const snap = () => {
    const h = document.documentElement;
    const cls = Array.from(h.classList).filter(c => /^fit-/.test(c)).sort().join(' ');
    return { ready: h.classList.contains('ready'), fit: cls, s: h.style.getPropertyValue('--stage-s'),
             screen: h.getAttribute('data-screen') || '' };
  };
  let last = '';
  // observe the document (an init script runs before <html> exists) and keep <html>'s own changes
  new MutationObserver(records => {
    if (!records.some(r => r.target === document.documentElement)) return;
    const s = snap(), key = s.fit + '|' + s.s;
    if (key !== last) { last = key; window.__fitLog.push(Object.assign({ t: Math.round(performance.now()) }, s)); }
  }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class', 'style'] });
})();
"""

async def _walk(dsf: float, size, gl: bool):
    from playwright.async_api import async_playwright
    args = ["--no-sandbox", "--hide-scrollbars"]
    args += ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if gl else ["--disable-webgl"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_chrome(), args=args)
        page = await browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=dsf)
        page.set_default_timeout(120000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(OBSERVE)
        await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&lang=ru&speed=1.2", wait_until="load")
        await _wait(page, "document.documentElement.classList.contains('ready')")
        at_ready = await page.evaluate("""() => ({
            screen: document.documentElement.getAttribute('data-screen'),
            fonts: document.fonts.check('16px "Setup Sans"', 'Установка') && document.fonts.check('16px "Setup Sans"', 'Setup'),
            title: document.getElementById('welcome-title').textContent })""")
        mid = []
        for selector in ("#btn-start", ".screen.active [data-next]", ".screen.active [data-next]",
                         ".screen.active [data-next]", ".screen.active [data-back]", ".screen.active [data-next]",
                         "#btn-install"):
            await page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")
            if selector != "#btn-install":
                await page.wait_for_timeout(120)
                mid.append(await page.evaluate("window.__memuFit()"))
            await page.wait_for_timeout(1500)
        await _wait(page, "document.documentElement.getAttribute('data-screen') === 'done'")
        await page.wait_for_timeout(1700)
        log = await page.evaluate("window.__fitLog")
        await browser.close()
    return at_ready, mid, log, errors

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class NoJerk(unittest.TestCase):
    """At the window size the installer uses (960×620 DIP, also the 944×581 seen on real
    Windows) and at 100/125/150 % scaling, the fit state never changes once the window is
    shown: no stage scaling, no dense/tight/scroll fallbacks, nothing undone a second later."""

    def test_entrance_and_page_changes_settle_once(self):
        for dsf, size in ((1.0, (960, 620)), (1.25, (960, 620)), (1.5, (944, 581))):
            with self.subTest(dsf=dsf, size=size):
                at_ready, mid, log, errors = asyncio.run(_walk(dsf, size, gl=False))
                self.assertEqual(errors, [])
                self.assertEqual(at_ready["screen"], "welcome")
                self.assertTrue(at_ready["fonts"], "fonts are loaded before the interface is shown")
                self.assertTrue(at_ready["title"].strip(), "texts are in before the interface is shown")
                for state in mid:
                    self.assertEqual((state["scale"], state["dense"], state["tight"], state["scroll"]), (1, False, False, False), state)

                self.assertTrue(log, "fit() ran")
                self.assertEqual({(e["fit"], e["s"]) for e in log}, {("", "1")}, f"fit state changed: {log}")

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class ProgressRing(unittest.TestCase):
    RING = r"""() => {
      const M = window.MemuScene, H = innerHeight;
      const tb = document.querySelector('.titlebar').getBoundingClientRect().bottom;
      const top = document.getElementById('panel-ghost').getBoundingClientRect().top;
      const r = M.RING_RADIUS * M._target.scale * 0.5 * H, cy = M._target.y * H;
      return { ok: M.ok, r, cy, tb, panelTop: top, busy: M._busyT, ring: M._ringT, ringA: M._ringAT,
               cssBusy: document.documentElement.classList.contains('ring-busy'),
               dash: document.getElementById('pring-arc').style.strokeDashoffset };
    }"""

    def test_ring_fits_band_and_has_a_busy_state(self):
        asyncio.run(self._run())

    async def _run(self):
        from playwright.async_api import async_playwright
        args = ["--no-sandbox", "--hide-scrollbars", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=_chrome(), args=args)
            page = await browser.new_page(viewport={"width": 960, "height": 620})
            page.set_default_timeout(180000)
            warnings = []
            page.on("console", lambda m: warnings.append(m.text) if "WebGL scene disabled" in m.text else None)
            for w, h in ((960, 620), (944, 581), (860, 540), (1280, 800)):
                await page.set_viewport_size({"width": w, "height": h})
                await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&shot=1&lang=en&screen=progress&pct=45", wait_until="commit")
                await _wait(page, "document.documentElement.getAttribute('data-screen') === 'progress'")
                await page.wait_for_timeout(600)
                g = await page.evaluate(self.RING)
                with self.subTest(size=(w, h)):
                    self.assertTrue(g["ok"], "the ring shader compiles")
                    self.assertGreaterEqual(g["cy"] - g["r"], g["tb"] + 8, f"ring top under the title bar: {g}")
                    self.assertLessEqual(g["cy"] + g["r"], g["panelTop"] - 8, f"ring runs into the panel: {g}")
                    self.assertGreater(g["r"], 60, g)
                    self.assertEqual((g["busy"], g["cssBusy"]), (0, False), "45 % is a determinate arc")
                    self.assertAlmostEqual(g["ring"], 0.45, places=2)
                    self.assertAlmostEqual(float(g["dash"]), 55.0, places=2)

            await page.set_viewport_size({"width": 960, "height": 620})
            await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&shot=1&lang=en&screen=progress&pct=3", wait_until="commit")
            await _wait(page, "document.documentElement.getAttribute('data-screen') === 'progress'")
            await page.wait_for_timeout(600)
            g = await page.evaluate(self.RING)
            self.assertEqual((g["busy"], g["cssBusy"], g["dash"]), (1, True, ""), g)
            await browser.close()
        self.assertEqual(warnings, [])

if __name__ == "__main__":
    unittest.main()
