"""Round 23: «Установить» must walk the user through every step of the installer.

User feedback: people press «Установить» (Install) expecting the settings to come
next — and the old welcome screen installed immediately with the defaults, while the
steps hid behind a separate «Настроить» (Customize) button. Now:

* «Установить» opens a wizard: components → folder → shortcuts & startup → review →
  install (Proxy Setup: folder → options → review). Every step has good defaults, so
  «Далее» is all it takes; «Назад» and Esc go back; nothing is written before the
  review's confirmation.
* «Быстрая установка» (express) jumps straight to the review — still no silent install.
* Update / repair / downgrade open the review of what will happen (a shorter flow);
  any «Изменить» there expands it into the full walk, and the step's primary button
  becomes «К сводке».
* The done screen opens the app explicitly and offers the folder (round 38: no «Что нового»).

Static checks run everywhere; the flow runs the real page against the mock bridge
(installer/ui/mock.js) in headless Chromium and SKIPS without Playwright/Chromium.
Also: the 3D scene renders at the device pixel ratio (the "pixelated star" bug) and a
long stall does not lower its resolution.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from pathlib import Path

from app.version import APP_VERSION as _APP_VERSION

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

def _dicts():
    text = (UI / "i18n.js").read_text(encoding="utf-8")
    out = {}
    for lang in ("en", "ru"):
        block = re.search(lang + r": \{(.*?)\n  \}", text, re.S).group(1)
        out[lang] = dict(re.findall(r'"([\w.]+)": "((?:[^"\\]|\\.)*)"', block))
    return out

class WizardMarkupAndWording(unittest.TestCase):
    def test_no_customize_button_and_every_step_exists(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("btn-customize", html, "the separate «Настроить» button is gone")
        self.assertNotIn("btn-quick", html, "the old one-click install button is gone")
        for screen in ("welcome", "edition", "location", "options", "review", "progress", "done"):
            self.assertIn(f'data-screen="{screen}"', html)
        for step in ("edition", "location", "options", "review", "progress"):
            self.assertIn(f'data-step="{step}"', html)
        for needle in ('id="btn-start"', 'id="btn-express"', 'id="btn-install"', 'id="summary"',
                       'id="btn-finish"', 'id="btn-done-close"', 'id="btn-open-folder"'):
            self.assertIn(needle, html)

        self.assertNotIn("btn-notes", html)

        welcome = html.split('data-screen="welcome"')[1].split("<section")[0]
        self.assertNotIn('id="btn-install"', welcome)
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('$("#btn-start").addEventListener("click", startWizard)', app)
        self.assertNotIn("btn-quick", app)

    def test_wording_both_languages(self):
        d = _dicts()
        self.assertEqual(set(d["ru"]), set(d["en"]), "ru/en key parity")
        ru = d["ru"]
        self.assertEqual(ru["btn.install"], "Установить")
        self.assertEqual(ru["btn.continue"], "Далее")
        self.assertEqual(ru["btn.back"], "Назад")
        self.assertEqual(ru["btn.express"], "Быстрая установка")
        self.assertIn("сводк", ru["btn.express.hint"])
        self.assertIn("не подтвердите", ru["btn.express.hint"], "express never installs silently")
        self.assertEqual(ru["btn.toReview"], "К сводке")
        self.assertNotIn("btn.customize", ru)
        for lang in ("ru", "en"):
            for key in ("step.edition", "step.location", "step.options", "step.review", "step.install", "step.update",
                        "location.title", "options.group.startup", "options.group.shortcuts", "review.title",
                        "review.title.update", "review.title.repair", "review.title.downgrade", "sum.components",
                        "sum.location", "sum.options", "sum.data", "btn.edit", "btn.open",
                        "done.folder"):
                self.assertTrue(d[lang].get(key), f"{lang}: {key}")
            self.assertFalse([k for k in d[lang] if k.startswith("notes.")], "What's new strings are removed")

    def test_every_task_has_a_label_and_a_description(self):
        from installer import products
        d = _dicts()
        for product in products.PRODUCTS.values():
            for task in product.tasks:
                for lang in ("ru", "en"):
                    self.assertTrue(d[lang].get(f"task.{task.id}"), f"{lang} task.{task.id}")
                    key = f"task.{task.id}.proxy.b" if product.id == "proxy" and task.id in ("autostart", "desktopicon") else f"task.{task.id}.b"
                    self.assertTrue(d[lang].get(key), f"{lang} {key}")

    def test_scene_renders_at_device_pixel_ratio(self):
        scene = (UI / "scene.js").read_text(encoding="utf-8")
        self.assertNotIn("_scaleFactor", scene, "the old 0.4× resolution watchdog is gone")
        self.assertIn("device-pixel-content-box", scene)
        self.assertIn("visibilitychange", scene)
        self.assertIn("Math.min(Math.max(dpr, this.software ? 1 : 1.5), 2.5)", scene)
        self.assertIn("cover", scene, "analytic edge antialiasing in the shader")

FLOW_HELPERS = """
window.__t = {
  screen: () => document.documentElement.getAttribute('data-screen'),
  click: sel => { const el = document.querySelector(sel); if (!el) throw new Error('missing ' + sel); el.click(); },
  primary: () => document.querySelector('.screen.active .btn.primary').click(),
  back: () => document.querySelector('.screen.active [data-back]').click(),
  calls: () => (window.__memuCalls || []).filter(c => c[0] === 'install'),
  steps: () => Array.from(document.querySelectorAll('.step')).filter(s => !s.hidden).map(s => s.dataset.step),
  active: () => { const a = document.querySelector('.step.active'); return a ? a.dataset.step : ''; },
  done: () => Array.from(document.querySelectorAll('.step.done')).map(s => s.dataset.step),
  label: () => document.querySelector('.screen.active .btn.primary').textContent.trim(),
};
"""

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class WizardFlow(unittest.TestCase):
    def test_flows(self):
        asyncio.run(self._run())

    async def _open(self, browser, query):
        page = await browser.new_page(viewport={"width": 960, "height": 620})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&shot=1&{query}", wait_until="load")
        for _ in range(80):
            await page.wait_for_timeout(25)
            if await page.evaluate("() => !!window.__memuUi && document.documentElement.getAttribute('data-screen')"):
                break
        await page.evaluate(FLOW_HELPERS)
        await page.wait_for_timeout(300)
        return page, errors

    async def _ev(self, page, js):
        return await page.evaluate(f"() => {js}")

    async def _step(self, page, js, wait=60):
        await page.evaluate(f"() => {{ {js}; }}")
        await page.wait_for_timeout(wait)

    async def _run(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=_chrome(), args=["--no-sandbox", "--disable-webgl"])
            await self._fresh(browser)
            await self._express_and_change(browser)
            await self._folder_validation(browser)
            await self._update(browser)
            await self._proxy(browser)
            await self._done_screen(browser)
            await browser.close()

    async def _fresh(self, browser):
        page, errors = await self._open(browser, "lang=ru")
        ev = lambda js: self._ev(page, js)  # noqa: E731
        self.assertEqual(await ev("__t.screen()"), "welcome")
        self.assertEqual(await ev("__t.label()"), "Установить")
        self.assertEqual(await ev("__t.steps()"), ["edition", "location", "options", "review", "progress"],
                         "the welcome screen previews every step")

        await self._step(page, "__t.click('#btn-start')")
        self.assertEqual(await ev("__t.screen()"), "edition")
        self.assertEqual(await ev("__t.calls().length"), 0)
        self.assertEqual(await ev("__t.active()"), "edition")
        visited = ["edition"]
        for expected in ("location", "options", "review"):
            await self._step(page, "__t.primary()")
            visited.append(await ev("__t.screen()"))
            self.assertEqual(await ev("__t.calls().length"), 0, f"nothing installed before the review ({expected})")
        self.assertEqual(visited, ["edition", "location", "options", "review"])
        self.assertEqual(await ev("__t.done()"), ["edition", "location", "options"])

        await self._step(page, "__t.back()")
        self.assertEqual(await ev("__t.screen()"), "options")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(60)
        self.assertEqual(await ev("__t.screen()"), "location")
        await self._step(page, "__t.back()")
        await self._step(page, "__t.back()")
        self.assertEqual(await ev("__t.screen()"), "welcome")

        await self._step(page, "__t.click('#btn-start')")
        await self._step(page, "__t.click('#opt-memucode')")
        await self._step(page, "__t.primary()")
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "options")
        await self._step(page, "__t.click('input[data-task=\"desktopicon\"]')")
        await self._step(page, "__t.click('input[data-task=\"autostart\"]')")
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "review")
        summary = await ev("document.getElementById('summary').innerText")
        self.assertIn("Полный MemuAI", summary)
        self.assertNotIn("MemuCode CLI", summary)
        self.assertIn("MemuAI на рабочем столе", summary)
        self.assertNotIn("Proxy вместе с Windows", summary)
        await self._step(page, "__t.click('#btn-install')", 120)
        calls = await ev("__t.calls()")
        self.assertEqual(len(calls), 1)
        opts = calls[0][1]
        self.assertEqual(opts["components"], ["proxy", "memuai"])
        self.assertEqual(sorted(opts["tasks"]), ["desktopicon"])
        self.assertTrue(opts["dir"].endswith("\\MemuAI"))
        self.assertEqual(await ev("__t.screen()"), "progress")
        self.assertEqual(await ev("__t.active()"), "progress")
        self.assertEqual(errors, [])
        await page.close()

    async def _express_and_change(self, browser):
        page, errors = await self._open(browser, "lang=en")
        ev = lambda js: self._ev(page, js)  # noqa: E731
        self.assertFalse(await ev("document.getElementById('btn-express').hidden"))
        await self._step(page, "__t.click('#btn-express')")
        self.assertEqual(await ev("__t.screen()"), "review", "express goes to the summary, it does not install")
        self.assertEqual(await ev("__t.calls().length"), 0)
        summary = await ev("document.getElementById('summary').innerText")
        self.assertIn("Complete MemuAI · MemuCode CLI", summary)
        self.assertIn("Proxy with Windows", summary)

        await self._step(page, "document.querySelector('[data-edit=\"location\"]').click()")
        self.assertEqual(await ev("__t.screen()"), "location")
        self.assertEqual(await ev("__t.label()"), "Back to summary")
        await page.fill("#opt-dir", "D:\\Apps\\MemuAI")
        await page.wait_for_timeout(320)
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "review")
        self.assertIn("D:\\Apps\\MemuAI", await ev("document.getElementById('summary').innerText"))

        await self._step(page, "document.querySelector('[data-edit=\"options\"]').click()")
        await self._step(page, "__t.back()")
        self.assertEqual(await ev("__t.screen()"), "review", "Back from a «Change» step returns to the summary")
        await self._step(page, "__t.back()")
        self.assertEqual(await ev("__t.screen()"), "options", "Back from the review walks the steps")
        self.assertEqual(await ev("__t.label()"), "Continue")
        await self._step(page, "__t.primary()")
        await self._step(page, "__t.click('#btn-install')", 120)
        calls = await ev("__t.calls()")
        self.assertEqual(calls[0][1]["dir"], "D:\\Apps\\MemuAI")
        self.assertEqual(calls[0][1]["components"], ["proxy", "memuai", "memucode"])
        self.assertEqual(calls[0][1]["tasks"], ["autostart"])
        self.assertEqual(errors, [])
        await page.close()

    async def _folder_validation(self, browser):
        page, errors = await self._open(browser, "lang=ru&screen=location")
        await page.wait_for_timeout(450)
        ev = lambda js: self._ev(page, js)  # noqa: E731
        self.assertEqual(await ev("__t.screen()"), "location")
        self.assertTrue(await ev("document.getElementById('btn-dir-reset').hidden"))
        await page.fill("#opt-dir", "")
        await page.wait_for_timeout(320)
        self.assertTrue(await ev("document.getElementById('btn-location-next').disabled"), "an empty folder blocks «Далее»")
        self.assertEqual(await ev("document.getElementById('dir-msg').textContent"), "Выберите папку.")
        self.assertFalse(await ev("document.getElementById('btn-dir-reset').hidden"))
        await self._step(page, "__t.click('#btn-dir-reset')", 320)
        self.assertFalse(await ev("document.getElementById('btn-location-next').disabled"))
        self.assertTrue(await ev("document.getElementById('opt-dir').value.endsWith('\\\\MemuAI')"))
        self.assertEqual(errors, [])
        await page.close()

    async def _update(self, browser):
        page, errors = await self._open(browser, "lang=ru&kind=upgrade")
        ev = lambda js: self._ev(page, js)  # noqa: E731
        self.assertEqual(await ev("__t.label()"), "Обновить")
        self.assertTrue(await ev("document.getElementById('btn-express').hidden"), "an update needs no express link")
        self.assertEqual(await ev("__t.steps()"), ["review", "progress"])
        await self._step(page, "__t.click('#btn-start')")
        self.assertEqual(await ev("__t.screen()"), "review", "an update starts with what will happen")
        self.assertEqual(await ev("__t.calls().length"), 0)
        text = await ev("document.getElementById('summary').innerText")
        self.assertIn("2.0", text)
        self.assertIn("3.6", text)
        self.assertIn("остаются на месте", text)
        self.assertEqual(await ev("document.querySelector('.step[data-step=\"progress\"] span').textContent"), "Обновление")

        await self._step(page, "document.querySelector('[data-edit=\"edition\"]').click()")
        self.assertEqual(await ev("__t.screen()"), "edition")
        self.assertEqual(await ev("__t.steps()"), ["edition", "location", "options", "review", "progress"])
        self.assertEqual(await ev("__t.label()"), "К сводке")
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "review")
        await self._step(page, "__t.click('#btn-install')", 120)
        self.assertEqual(len(await ev("__t.calls()")), 1)
        await page.close()

        for kind, label in (("reinstall", "Переустановить"), ("downgrade", f"Установить {_APP_VERSION}")):
            page, errors2 = await self._open(browser, f"lang=ru&kind={kind}")
            self.assertEqual(await self._ev(page, "__t.label()"), label)
            await self._step(page, "__t.click('#btn-start')")
            self.assertEqual(await self._ev(page, "__t.screen()"), "review")
            self.assertEqual(await self._ev(page, "document.getElementById('review-downgrade').hidden"), kind != "downgrade")
            self.assertEqual(await self._ev(page, "__t.calls().length"), 0)
            errors += errors2
            await page.close()
        self.assertEqual(errors, [])

    async def _proxy(self, browser):
        page, errors = await self._open(browser, "lang=ru&product=proxy")
        ev = lambda js: self._ev(page, js)  # noqa: E731
        self.assertEqual(await ev("__t.steps()"), ["location", "options", "review", "progress"])
        await self._step(page, "__t.click('#btn-start')")
        self.assertEqual(await ev("__t.screen()"), "location")
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "options")
        rows = await ev("Array.from(document.querySelectorAll('#toggles input')).map(i => i.dataset.task)")
        self.assertEqual(rows, ["autostart", "desktopicon"])
        await self._step(page, "__t.primary()")
        self.assertEqual(await ev("__t.screen()"), "review")
        self.assertFalse(await ev("!!document.querySelector('[data-row=\"components\"]')"), "Proxy Setup has no components row")
        self.assertEqual(errors, [])
        await page.close()

    async def _done_screen(self, browser):
        page, errors = await self._open(browser, "lang=ru&screen=done&speed=0.2")
        ev = lambda js: self._ev(page, js)  # noqa: E731
        for _ in range(60):
            await page.wait_for_timeout(50)
            if await ev("__t.screen()") == "done":
                break
        self.assertEqual(await ev("__t.screen()"), "done")
        self.assertEqual(await ev("document.getElementById('btn-finish').innerText.trim()"), "Открыть MemuAI")
        self.assertEqual(await ev("__t.done()"), ["edition", "location", "options", "review", "progress"])
        self.assertIsNone(await ev("document.getElementById('btn-notes')"))
        await self._step(page, "__t.click('#btn-open-folder')")
        await self._step(page, "__t.click('#btn-finish')", 120)
        names = [c[0] for c in await ev("window.__memuCalls")]
        self.assertIn("open_folder", names)
        self.assertEqual(names[-2:], ["launch", "close"], "Open = launch, then close Setup")
        self.assertEqual(errors, [])
        await page.close()

SCENE_JS = """() => { const c = document.getElementById('scene'), M = window.MemuScene;
  return {ok: !!(M && M.ok), w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight, scale: M && M.dpr,
          dpr: devicePixelRatio, software: !!(M && M.software)}; }"""

@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class SceneResolution(unittest.TestCase):
    """The pixelated star: the canvas must be drawn at (at least) the device pixel ratio,
    and a stall (hidden window, native drag, GC) must never lower it."""

    def test_canvas_matches_device_pixels_and_survives_stalls(self):
        asyncio.run(self._run())

    async def _run(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=_chrome(), args=[
                "--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            for dsf in (1, 1.25, 1.5, 2):
                page = await browser.new_page(viewport={"width": 960, "height": 620}, device_scale_factor=dsf)
                await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&lang=ru&quality=full", wait_until="load")
                await page.wait_for_timeout(700)
                info = await page.evaluate(SCENE_JS)
                if not info["ok"]:
                    self.skipTest("WebGL unavailable in this Chromium: " + json.dumps(info))
                self.assertGreaterEqual(info["w"], round(info["cw"] * dsf) - 1, info)
                self.assertGreaterEqual(info["h"], round(info["ch"] * dsf) - 1, info)
                self.assertLessEqual(info["scale"], 2.5)

                gpu = await page.evaluate("() => { const M = window.MemuScene; M.software = false; M.resize(); return M.dpr; }")
                self.assertAlmostEqual(gpu, max(1.5, dsf), places=3)
                await page.close()

            page = await browser.new_page(viewport={"width": 960, "height": 620}, device_scale_factor=1)
            await page.goto(f"{(UI / 'index.html').as_uri()}?mock=1&lang=ru", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("() => { const M = window.MemuScene; M.software = false; M.adaptive = true; M._q = 1; M.resize(); }")
            before = await page.evaluate(SCENE_JS)
            await page.evaluate("() => { const t = performance.now(); while (performance.now() - t < 600) {} }")
            await page.wait_for_timeout(250)
            await page.evaluate("() => { const t = performance.now(); while (performance.now() - t < 400) {} }")
            await page.wait_for_timeout(250)
            after = await page.evaluate(SCENE_JS)
            self.assertEqual((before["w"], before["h"]), (after["w"], after["h"]), (before, after))
            self.assertTrue(after["ok"])

            floor = await page.evaluate("() => { const M = window.MemuScene; M._q = 0.01; M.resize(); return M.dpr; }")
            self.assertGreaterEqual(floor, 1.0)
            await page.close()
            await browser.close()

if __name__ == "__main__":
    unittest.main()
