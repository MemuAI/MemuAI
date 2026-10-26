"""Screenshots of the animated companion (Round 22).

Usage: python tests/companion_ui_shots.py BASE_URL OUT_DIR [--auto-fallback]

BASE_URL is a running MemuAI window server (devrun.py). Uses headless
Chromium with SwiftShader WebGL. SwiftShader crashes its GPU process on
vertex-texture skinning, so the stage is put into its CPU-skinning
compatibility mode (the same mode it switches to by itself after repeated
context losses; --auto-fallback exercises that path instead).
Prints MemuStage.info() of each stage so the run can be checked without
looking at the pictures.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

CHROME = os.environ.get("CHROME") or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium")
                                      else "/workspace/work/chromium/chrome")
BASE = ""
FLAGS = ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
         "--autoplay-policy=no-user-gesture-required"]

async def stage_info(page):
    out = []
    for frame in page.frames:
        if "/companion/stage" in frame.url:
            try:
                out.append(await frame.evaluate("JSON.stringify(MemuStage.info())"))
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                out.append(f"error: {exc}")
    return out

async def wait_loaded(page, timeout_s=90):
    for _ in range(timeout_s * 2):
        infos = await stage_info(page)
        if infos and all('"loaded":true' in i for i in infos):
            return infos
        await page.wait_for_timeout(500)
    return await stage_info(page)

async def open_app(page):
    """The MemuAI page, ready for the shots. On fresh data app.js opens the profile onboarding dialog
    (showProfileOnboarding) 250 ms after boot and it covers the Live panel; it is closed here, in this page only —
    nothing is saved, so the data folder and the app's first-run behaviour stay untouched."""
    await page.goto(BASE + "/", wait_until="networkidle")
    await page.wait_for_function("() => document.body.classList.contains('boot-ready')", timeout=30000)
    pending = await page.evaluate(
        "() => !pref('profile_onboarded', false) || !String(currentProfile().name || '').trim()")
    if not pending:
        return
    try:
        await page.wait_for_selector("#onboarding-name", timeout=5000)
    except Exception:  # noqa: BLE001 - no dialog after all: nothing to close
        return
    await page.evaluate("() => closePanel()")
    await page.wait_for_function("() => !document.getElementById('panel-back').classList.contains('visible')",
                                 timeout=5000)

async def main():
    global BASE
    base, out = sys.argv[1].rstrip("/"), sys.argv[2]
    BASE = base
    auto = "--auto-fallback" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    want = lambda name: only is None or name in only
    os.makedirs(out, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=FLAGS)

        async def new_page(w, h, theme="dark"):
            page = await browser.new_page(viewport={"width": w, "height": h})
            if not auto:
                await page.add_init_script("try{sessionStorage.setItem('memu-stage-cpu-skin','1')}catch(e){}")
            page.on("pageerror", lambda e: print("pageerror", e))
            return page

        if want("live"):
            await shots_live(new_page, out)
        if want("settings"):
            await shots_settings(new_page, out)
        if want("quick"):
            await shots_quick(new_page, out)
        if want("desktop"):
            await shots_desktop(new_page, out)
        if want("live2d"):
            await shots_live2d(new_page, out)
        await browser.close()

async def shots_live(new_page, out):
    base = BASE
    if True:
        page = await new_page(1280, 820)
        await open_app(page)
        await page.wait_for_timeout(1500)
        await page.evaluate("""() => {
          S.preferences = Object.assign(S.preferences || {}, { companion_live_enabled: true, companion_live_placement: 'panel' });
          const panel = document.getElementById('live-call'); panel.hidden = false; panel.dataset.mode = 'classic';
          renderLiveControls(); voiceStatus(PG_LANG === 'en' ? 'Ready to talk' : 'Готов к разговору', 'ready');
          MemuCompanionLive.emit('open');
        }""")
        print("live-panel", await wait_loaded(page))
        await page.evaluate("""() => {
          const c = MemuCompanionLive.instance;
          document.getElementById('live-transcript').textContent = 'Расскажи что-нибудь весёлое!';
          voiceStatus('MemuAI говорит', 'speaking');
          c.text('[emotion:happy] Конечно! Знаешь, почему программисты путают Хэллоуин и Рождество? Потому что Oct 31 = Dec 25!', true);
          c.speak(true, 'Конечно! Знаешь, почему программисты путают Хэллоуин и Рождество?');
        }""")
        await page.wait_for_timeout(1700)
        await page.screenshot(path=os.path.join(out, "live-panel-speaking.png"))
        await page.evaluate("() => { voiceStatus('MemuAI отвечает…', 'working'); }")
        await page.wait_for_timeout(1500)
        await page.locator("#live-call").screenshot(path=os.path.join(out, "live-panel-thinking.png"))
        await page.evaluate("() => { document.documentElement.dataset.theme = 'light'; MemuCompanionLive.instance.emotion('surprised', 1); voiceStatus('Ваша очередь', 'ready'); }")
        await page.wait_for_timeout(900)
        await page.locator("#live-call").screenshot(path=os.path.join(out, "live-panel-light-surprised.png"))

        await page.evaluate("""() => {
          document.documentElement.dataset.theme = 'dark';
          S.preferences.companion_live_placement = 'window'; S.preferences.companion_framing = 'full';
          MemuCompanionLive.refresh();
        }""")
        print("live-window", await wait_loaded(page))
        await page.evaluate("""() => {
          const c = MemuCompanionLive.instance;
          voiceStatus('MemuAI говорит', 'speaking');
          c.text('Привет! Я теперь живу прямо над окном MemuAI. Меня можно перетащить куда угодно.', false);
          c.speak(true, 'Привет! Я теперь живу прямо над окном MemuAI.');
          c.gesture('wave');
        }""")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=os.path.join(out, "live-window-floating.png"))
        await page.close()

async def shots_settings(new_page, out):
    base = BASE
    if True:

        page = await new_page(1280, 900)
        await open_app(page)
        await page.wait_for_timeout(1500)
        await page.evaluate("""() => { currentSettingsTab = 'voice'; showSettings(true); }""")
        await page.wait_for_timeout(800)

        await page.locator('#panel [data-sx-open="companion"]').first.click()
        print("settings", await wait_loaded(page))
        await page.wait_for_timeout(800)
        await page.screenshot(path=os.path.join(out, "settings-live.png"))
        await page.close()

async def shots_quick(new_page, out):
    base = BASE
    if True:

        page = await new_page(760, 560)
        await page.goto(base + "/quick/?companion_demo=1", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        has_plugin = await page.evaluate("() => !!window.MemuQuickCompanion")
        print("quick plugin loaded:", has_plugin)
        if has_plugin:
            await page.evaluate("() => MemuQuickCompanion.demo()")
            print("quick", await wait_loaded(page))
            await page.wait_for_timeout(1200)
        await page.screenshot(path=os.path.join(out, "quick-inline.png"))
        await page.close()

async def shots_desktop(new_page, out):
    base = BASE
    if True:

        page = await new_page(320, 440)
        await page.goto(base + "/companion/stage?surface=desktop", wait_until="networkidle")
        print("desktop", await wait_loaded(page))
        await page.evaluate("() => MemuStage.receive({type:'bubble', text:'Я в отдельном прозрачном окне поверх рабочего стола.', final:false})")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=os.path.join(out, "desktop-stage.png"), omit_background=True)
        await page.close()

async def shots_live2d(new_page, out):
    """A user-imported Live2D model in the Live panel (needs the runtime and a Live2D model in the library)."""
    page = await new_page(1280, 820)
    await open_app(page)
    lib = await page.evaluate("() => fetch('/api/companion/models').then((r) => r.json())")
    model = next((m for m in lib["models"] if m["kind"] == "live2d"), None)
    if not model or not lib["runtime"]["installed"]:
        print("live2d: skipped (no runtime or no Live2D model in the library)")
        await page.close()
        return
    await page.evaluate("""(id) => {
      S.preferences = Object.assign(S.preferences || {}, { companion_live_enabled: true, companion_live_placement: 'panel', companion_model_id: id, companion_framing: 'bust' });
      const panel = document.getElementById('live-call'); panel.hidden = false; panel.dataset.mode = 'classic';
      renderLiveControls(); MemuCompanionLive.emit('open');
    }""", model["id"])
    print("live2d", await wait_loaded(page))
    await page.evaluate("""() => { voiceStatus('MemuAI говорит', 'speaking');
      const c = MemuCompanionLive.instance; c.text('[emotion:happy] Привет! Это модель Live2D.', true); c.speak(true, 'Привет! Это модель Live2D, и она тоже говорит.'); }""")
    await page.wait_for_timeout(900)
    await page.locator("#live-call").screenshot(path=os.path.join(out, "live-panel-live2d.png"))
    await page.close()

if __name__ == "__main__":
    asyncio.run(main())
