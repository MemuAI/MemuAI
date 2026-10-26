"""Round 27 (r27-selection): Settings → Быстрый доступ → «Выделенный текст» in headless Chromium against a private server.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_selection_settings_shots.py OUT_DIR

Starts its own MemuAI window server (playground_api.create_app) on a free port with a temporary data dir. Checks:
the link opens the sub-page (it is not empty), switching the feature off hides the rest and is saved, a button
switched off and moved up is saved in order, an own action with {text} is added (saved, shows «своё», gets a number),
English works, nothing sticks out of the pane, no page errors. Shots: sel-settings-<theme>-<lang>.png, the sub-page
with an own action, the feature off.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def call(base: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read() or b"{}")

async def main(out: Path) -> int:
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r27sel-settings-")
    import uvicorn
    from playwright.async_api import async_playwright
    from app.playground_api import create_app
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            call(base, "/api/pg/preferences")
            break
        except Exception:
            time.sleep(0.1)
    problems: list = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def prefs() -> dict:
        data = call(base, "/api/pg/preferences")
        return data.get("preferences", data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def open_sub(theme: str, lang: str):
            call(base, "/api/pg/preferences", {"theme": theme, "language": lang})
            page = await browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=1)
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.goto(base + "/", wait_until="networkidle")
            await page.wait_for_timeout(900)
            await page.evaluate("openSettingsPage('quick')")
            await page.wait_for_timeout(1500)

            await page.wait_for_selector('[data-sx-open="quick-selection"]', timeout=20000)
            await page.click('[data-sx-open="quick-selection"]')
            await page.wait_for_timeout(500)
            return page

        async def overflow(page, label):
            wide = await page.evaluate("""() => {
                const pane = document.querySelector('#qs-sub-selection'); if (!pane) return ['no pane'];
                const right = pane.getBoundingClientRect().right + 1;
                return [...pane.querySelectorAll('*')].filter((el) => el.offsetParent && el.getBoundingClientRect().right > right && !el.closest('.sx-section-head'))
                  .slice(0, 5).map((el) => (el.id || el.className || el.tagName) + '');
            }""")
            check(not wide, f"{label}: nothing sticks out ({wide})")

        async def shot(page, name):

            for sid in ("qss-general", "qss-buttons", "qss-own"):
                loc = page.locator(f"#{sid}")
                if await loc.count() and await loc.is_visible():
                    await loc.scroll_into_view_if_needed()
                    await page.wait_for_timeout(150)
                    await loc.screenshot(path=str(out / f"{name}-{sid[4:]}.png"))

        page = await open_sub("dark", "ru")
        items = await page.locator("#qs-sub-selection .qss-item").count()
        check(items == 6, f"six built-in actions listed ({items})")
        await overflow(page, "dark/ru")
        await shot(page, "sel-settings-dark-ru")

        await page.click('#qs-sub-selection label:has(input[data-act="explain"])')
        await page.wait_for_timeout(400)
        await page.click('#qs-sub-selection [data-up="fix"]')
        await page.wait_for_timeout(600)
        order = prefs().get("quick_ask_selection_actions")
        check(order == ["fix", "translate", "summarize", "reply", "ask"], f"order and the switched-off button are saved ({order})")

        await page.fill("#qss-name", "Официальнее")
        await page.fill("#qss-prompt", "Перепиши официально: {text}")
        await page.click('#qs-sub-selection label:has(#qss-replace)')
        await page.click("#qss-save")
        await page.wait_for_timeout(700)
        saved = prefs()
        custom = saved.get("quick_ask_selection_custom") or []
        check(len(custom) == 1 and custom[0]["name"] == "Официальнее" and custom[0]["replace"] is True
              and custom[0]["id"] in saved.get("quick_ask_selection_actions", []), f"own action saved ({custom})")
        check(await page.locator("#qs-sub-selection .qss-text i").count() == 1, "the own action is marked")
        await shot(page, "sel-settings-own-dark-ru")

        await page.click('#qs-sub-selection label:has(#qss-on)')
        await page.wait_for_timeout(500)
        check(prefs().get("quick_ask_selection") is False, "switching the feature off is saved")
        check(not await page.locator("#qss-buttons").is_visible(), "the rest hides when the feature is off")
        await shot(page, "sel-settings-off-dark-ru")
        await page.close()
        call(base, "/api/pg/preferences", {"quick_ask_selection": True})

        page = await open_sub("light", "en")
        label = await page.inner_text("#qss-general .sx-label")
        check("Offer actions" in label, f"English labels ({label})")
        await overflow(page, "light/en")
        await shot(page, "sel-settings-light-en")
        await page.close()
        await browser.close()
    server.should_exit = True
    print("problems:", len(problems))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r27-selection-settings"))))
