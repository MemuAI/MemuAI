"""Screenshots and timings of the Proxy model capabilities UI (Round 22).

Usage: python tests/proxy_model_caps_shots.py http://127.0.0.1:PORT OUT_DIR

Needs a running Proxy with a catalogue of models (the r22 dev instance seeds ~640). Takes the model list
with badges and filters, the model card drawer (dark and light), the RU and EN versions, and prints how long
the list takes to render. Polls from Python (the panel CSP has no 'unsafe-eval')."""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"

async def wait_for(page, expression: str, timeout: float = 15.0) -> None:
    for _ in range(int(timeout * 10)):
        if await page.evaluate(expression):
            return
        await page.wait_for_timeout(100)
    raise TimeoutError(expression)

async def main(base: str, out: str) -> None:
    os.makedirs(out, exist_ok=True)
    report = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for theme in ("dark", "light"):
            for lang in (("ru", "en") if theme == "dark" else ("ru",)):
                page = await browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                await page.add_init_script(f"localStorage.setItem('oh_lang', '{lang}');")
                await page.goto(base + "/", wait_until="networkidle")
                if theme == "light":
                    await page.evaluate("document.documentElement.dataset.theme = 'light'")
                await page.evaluate("document.querySelector('[data-tab=\"settings\"]').click()")

                await page.evaluate("window.MemuSettings && MemuSettings.select('models')")
                await wait_for(page, "document.querySelectorAll('#models-table tr[data-mid]').length > 100")
                await wait_for(page, "document.querySelectorAll('#models-table .mcap-b').length > 50")
                await page.wait_for_timeout(600)
                tag = f"{theme}-{lang}"
                timing = await page.evaluate("""() => {
                  const t0 = performance.now(); renderModelsTable(); const t1 = performance.now();
                  return {first_paint_ms: +(t1 - t0).toFixed(1), rows_now: document.querySelectorAll('#models-table tr[data-mid]').length,
                          total: modelsCache.length};
                }""")
                await page.wait_for_timeout(800)
                timing["rows_after"] = await page.evaluate("document.querySelectorAll('#models-table tr[data-mid]').length")
                report[tag] = timing
                await page.evaluate("document.getElementById('models-card').scrollIntoView({block: 'start'})")
                await page.wait_for_timeout(300)
                await page.screenshot(path=f"{out}/list-{tag}.png")
                if theme == "dark" and lang == "ru":
                    await page.evaluate("document.querySelector('[data-mcap-filter=\"vision\"]').click()")
                    await page.evaluate("document.querySelector('[data-mcap-filter=\"tools\"]').click()")
                    await page.wait_for_timeout(400)
                    report["filtered_vision_tools"] = await page.evaluate("document.getElementById('models-count').textContent")
                    await page.screenshot(path=f"{out}/list-filtered-{tag}.png")
                    await page.evaluate("document.querySelector('[data-mcap-clear]').click()")
                    await page.wait_for_timeout(300)
                for model, name in (("openrouter-anthropic/claude-haiku-5.5", "claude"),
                                    ("lmstudio-qwen2.5-vl-7b-instruct", "lmstudio"),
                                    ("groq-whisper-large-v3", "whisper")):
                    if theme == "light" and name != "claude":
                        continue
                    if lang == "en" and name == "whisper":
                        continue
                    await page.evaluate(f"MemuModelCaps.open({json.dumps(model)})")
                    await wait_for(page, "!!document.querySelector('#mcap-drawer .mcap-tiles')")
                    await page.wait_for_timeout(400)
                    await page.screenshot(path=f"{out}/card-{name}-{tag}.png")
                    if name == "claude" and theme == "dark" and lang == "ru":
                        await page.evaluate("document.querySelector('#mcap-drawer .mcap-body').scrollTop = 900")
                        await page.wait_for_timeout(200)
                        await page.screenshot(path=f"{out}/card-{name}-{tag}-scrolled.png")
                        await page.evaluate("document.querySelector('#mcap-drawer details[data-mcap-raw]').open = true")
                        await page.wait_for_timeout(200)
                        await page.evaluate("document.querySelector('#mcap-drawer details[data-mcap-raw]').scrollIntoView()")
                        await page.wait_for_timeout(200)
                        await page.screenshot(path=f"{out}/card-{name}-{tag}-raw.png")
                        await page.evaluate("document.querySelector('[data-mcap-probe]').click()")
                        await page.wait_for_timeout(300)
                        await page.screenshot(path=f"{out}/card-probe-confirm-{tag}.png")
                        await page.evaluate("document.getElementById('m-no').click()")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(300)
                    report[f"drawer_closed_{name}_{tag}"] = await page.evaluate("!document.getElementById('mcap-drawer')")
                report[f"errors_{tag}"] = errors[:10]
                await page.close()
        await browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].rstrip("/"), sys.argv[2]))
