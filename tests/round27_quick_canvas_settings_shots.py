"""Round 27 (r27-quick-canvas): Settings → Быстрый доступ → «Панели и быстрые действия» in headless Chromium.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_quick_canvas_settings_shots.py OUT_DIR

Starts its own MemuAI window server (playground_api.create_app, temporary data dir). Checks: the plugin link opens a
non-empty sub-page, a switch and a select are saved to quick_canvas.json, the button list shows the buttons (a risky
one with its approval chip), «Забыть разрешение» drops the approval, nothing sticks out, no page errors, ru/en, themes.
Shots: qcv-settings-<section>-<theme>-<lang>.png.
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

def call(base: str, path: str, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"},
                                     method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"{}")

async def main(out: Path) -> int:
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r27qcv-settings-")
    import uvicorn
    from playwright.async_api import async_playwright
    from app.playground_api import create_app
    from app import quick_canvas, quick_actions as qa
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            call(base, "/api/quick/canvas")
            break
        except Exception:
            time.sleep(0.1)
    problems: list = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    for tp in call(base, "/api/quick/canvas/catalog?lang=ru")["templates"][:3]:
        call(base, "/api/quick/canvas/actions", {"action": {k: v for k, v in tp.items() if k != "key"}})
    risky = call(base, "/api/quick/canvas/actions", {"action": {"label": "Закрыть Telegram", "icon": {"kind": "emoji", "value": "🧹"}, "hotkey": "Ctrl+Alt+T",
                                                              "steps": [{"type": "command", "action": "app.close", "params": {"name": "telegram"}}]}})["action"]

    def approve(doc):
        for a in doc["actions"]:
            if a["id"] == risky["id"]:
                a["approved"] = qa.signature(a)
    quick_canvas.mutate(approve)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def open_sub(theme: str, lang: str):
            call(base, "/api/pg/preferences", {"theme": theme, "language": lang, "profile_onboarded": True})
            page = await browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=1)
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.goto(base + "/", wait_until="networkidle")
            await page.wait_for_timeout(900)
            await page.evaluate("openSettingsPage('quick')")
            link = page.locator('[data-sx-open="quick-plugin-canvas"]')
            try:
                await link.wait_for(state="attached", timeout=20000)
            except Exception:
                pass
            check(await link.count() == 1, f"{theme}/{lang}: the plugin link is on the Quick Ask page")
            if await link.count():
                await link.click()
                await page.wait_for_timeout(900)
            return page

        async def shots(page, theme, lang):
            for sid in ("qcs-main", "qcs-buttons", "qcs-keys"):
                loc = page.locator(f"#{sid}")
                check(await loc.count() == 1, f"{theme}/{lang}: section {sid}")
                if await loc.count():
                    await loc.scroll_into_view_if_needed()
                    await page.wait_for_timeout(200)
                    await loc.screenshot(path=str(out / f"qcv-settings-{sid[4:]}-{theme}-{lang}.png"))
            wide = await page.evaluate("""() => {
                const pane = document.querySelector('#qs-sub-plugin-canvas'); if (!pane) return ['no pane'];
                const right = pane.getBoundingClientRect().right + 1;
                return [...pane.querySelectorAll('*')].filter((el) => el.offsetParent && el.getBoundingClientRect().right > right && !el.closest('.sx-section-head'))
                  .slice(0, 5).map((el) => (el.id || el.className || el.tagName) + '');
            }""")
            check(not wide, f"{theme}/{lang}: nothing sticks out ({wide})")

        page = await open_sub("dark", "ru")
        text = await page.inner_text("#qs-sub-plugin-canvas")
        check("Закрыть Telegram" in text and "Ctrl+Alt+T" in text and "Разрешено" in text, "the list shows the buttons, the hotkey and the approval")
        await shots(page, "dark", "ru")
        await page.click("#qcs-labels + *, #qcs-labels", force=True)
        await page.wait_for_timeout(500)
        check(call(base, "/api/quick/canvas")["settings"]["labels"] is False, "a switch is saved")

        await page.select_option("#qcs-screen", "last")
        await page.wait_for_timeout(500)
        check(call(base, "/api/quick/canvas")["settings"]["screen"] == "last", "the screen mode is saved")
        await page.click(f'[data-qcs-forget="{risky["id"]}"]')
        await page.wait_for_timeout(700)
        state = call(base, "/api/quick/canvas")
        check(not next(a for a in state["actions"] if a["id"] == risky["id"])["approved"], "«Забыть разрешение» drops the approval")
        await page.close()
        page = await open_sub("light", "en")
        text = await page.inner_text("#qs-sub-plugin-canvas")
        check("Panels and quick actions" in text and "Import from a file" in text, "English settings")
        await shots(page, "light", "en")
        await page.close()
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r27/r27-quick-canvas"))))
