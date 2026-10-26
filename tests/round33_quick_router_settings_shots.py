"""Round 33 (r33-quick): Settings → Быстрый доступ → Команды — the «Понимать команды с помощью ИИ» rows, real servers.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round33_quick_router_settings_shots.py OUT_DIR

Starts MemuAI Proxy + MemuAI in this process (tests/ui_lang_scan.Servers, private data folder, offline model mock),
opens Settings → Quick Ask → Commands in RU/EN × dark/light/glass and checks: the router mode select is there with
3 options, the model select lists the chat models with «the Quick Ask model» first, «Выключено» hides the model and
the wait rows and is saved (``/api/assistant/config``), the wait is saved as a number; no page errors, no Cyrillic in
English. Exit code 1 on any problem. ``R33_VARIANTS=ru-dark-classic,en-dark-glass`` runs only those variants.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r33-quick-settings-"))
os.environ["MEMUAI_ASSISTANT_NO_WARM"] = "1"
CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"

def post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                     method="POST")
    return json.loads(urllib.request.urlopen(request, timeout=20).read() or b"{}")

def get(base: str, path: str) -> dict:
    return json.loads(urllib.request.urlopen(base + path, timeout=20).read() or b"{}")

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from tests.ui_lang_scan import Servers
    out.mkdir(parents=True, exist_ok=True)
    servers = Servers().start()
    base = servers.pg
    problems: list = []

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            only = [v for v in os.environ.get("R33_VARIANTS", "").split(",") if v]
            for lang, theme, design in (("ru", "dark", "classic"), ("ru", "light", "classic"), ("ru", "dark", "glass"),
                                        ("en", "light", "classic"), ("en", "dark", "glass")):
                tag = f"{lang}-{theme}-{design}"
                if only and tag not in only:
                    continue
                post(base, "/api/pg/preferences", {"language": lang, "theme": theme, "ui_design": design, "profile_onboarded": True})
                post(base, "/api/assistant/settings", {"ai_router": "auto", "router_model": "", "router_timeout": 4})
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda e, tag=tag: problems.append(f"{tag}: pageerror {e}"))
                await page.goto(base + "/", wait_until="networkidle")
                try:
                    await page.wait_for_selector("#startup-cover", state="hidden", timeout=60000)
                except Exception:
                    pass
                await page.wait_for_timeout(600)
                for _ in range(30):
                    await page.keyboard.press("Control+Comma")
                    await page.wait_for_timeout(700)
                    if await page.locator('[data-set-tab="quick"]').count():
                        break
                if not await page.locator('[data-set-tab="quick"]').count():
                    await page.screenshot(path=str(out / f"debug-{tag}.png"))
                    problems.append(f"{tag}: settings did not open")
                    await page.close()
                    continue
                await page.click('[data-set-tab="quick"]')
                await page.wait_for_timeout(700)
                opener = page.locator('[data-sx-open="quick-commands"]')
                await opener.scroll_into_view_if_needed()
                await opener.click()
                await page.wait_for_timeout(1500)
                box = page.locator('[data-sx-sub="quick-commands"]')
                mode = box.locator('select[data-set="ai_router"]')
                check(await mode.count() == 1, f"{tag}: router mode select")
                if not await mode.count():
                    await page.close()
                    continue
                values = await mode.evaluate("s => Array.from(s.options).map(o => o.value)")
                check(values == ["auto", "always", "off"], f"{tag}: modes {values}")
                models = await box.locator('select[data-set="router_model"]').evaluate("s => Array.from(s.options).map(o => o.value)")
                check(models[:1] == [""] and len(models) >= 2, f"{tag}: router models {models}")
                row = box.locator('label.mcs-row:has(select[data-set="ai_router"])')
                await row.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                card = box.locator('section[data-mcs="main"]')
                await card.screenshot(path=str(out / f"settings-{tag}.png"))
                if lang == "en":
                    text = await box.inner_text()
                    cyr = [w for w in text.split() if any("\u0400" <= ch <= "\u04ff" for ch in w)]
                    check(not cyr, f"{tag}: Cyrillic in English settings: {cyr[:6]}")

                num = box.locator('input[data-set="router_timeout"]')
                await num.fill("6")
                await num.press("Tab")
                await page.wait_for_timeout(900)
                check(get(base, "/api/assistant/config")["settings"]["router_timeout"] == 6, f"{tag}: timeout saved")

                if design == "classic":
                    await page.evaluate("""() => { const s = document.querySelector('[data-sx-sub="quick-commands"] select[data-set="ai_router"]');
                        s.value = 'off'; s.dispatchEvent(new Event('change', {bubbles: true})); }""")
                    await page.wait_for_timeout(900)
                    check(await box.locator('select[data-set="router_model"]').count() == 0, f"{tag}: «off» hides the model")
                    check(get(base, "/api/assistant/config")["settings"]["ai_router"] == "off", f"{tag}: «off» saved")
                    await card.screenshot(path=str(out / f"settings-off-{tag}.png"))
                await page.close()
            await browser.close()
    finally:
        servers.stop()
    print("problems:", len(problems))
    for p in problems:
        print(" -", p)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r33-quick-settings"))))
