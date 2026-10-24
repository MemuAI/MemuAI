"""Round 24: the Quick Ask window in the new designs (soft/terminal/brutal) + glass, light/dark, idle and
answer states. Reuses the fixture data and static server of quick_ask_r23_shots.py.
Run: python tests/round24_designs_quick_shots.py -> /workspace/work/shots/designs/quick-*.png
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quick_ask_r23_shots as base  # noqa: E402

OUT = Path(__import__("os").environ.get("R24_SHOTS", "/workspace/work/shots/designs-r24"))


async def main():
    from playwright.async_api import async_playwright
    from app import themes
    server, origin = base.serve()
    state = {"prefs": dict(base.BASE_PREFS, quick_ask_model="gemini-2.5-flash"), "events": base.CODE_ANSWER}
    errors = []

    async def route(r, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await r.fulfill(json={"prefs": state["prefs"], "status": {"native": True, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"},
                                                                        "conflicts": [], "warnings": []}, "alternatives": [], "ui": {"onboarded": True},
                                         "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await r.fulfill(json={"models": base.MODELS, "image_models": []})
        if path == "/api/quick/history":
            return await r.fulfill(json={"items": base.HISTORY, "enabled": True})
        if path == "/api/quick/chats":
            return await r.fulfill(json={"items": [dict(row, updated=int(time.time() * 1000)) for row in base.CHATS], "total": len(base.CHATS)})
        if path == "/api/quick/ask":
            body = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in state["events"])
            return await r.fulfill(status=200, content_type="text/event-stream", body=body)
        if path.startswith("/api/assistant/state"):
            return await r.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": True}}, "seq": 0, "timers": None})
        if path.startswith("/api/"):
            return await r.fulfill(json={"ok": True, "events": [], "seq": 0, "matched": False})
        return await r.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        for design in sys.argv[1:] or ["soft", "terminal", "brutal", "glass"]:
            for theme in ("light", "dark"):
                state["prefs"].update(theme=theme, ui_design=design, accent_color="blue", language="ru")
                page = await browser.new_page(viewport={"width": 720, "height": 640})
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/*", route)
                await page.goto(origin + "/quick/?native=1", wait_until="load")
                await page.wait_for_timeout(500)
                await page.evaluate("QuickAsk.onShow(" + base.SHOW % "" + ")")
                await page.wait_for_timeout(400)
                got = await page.evaluate("document.documentElement.dataset.design")
                if got != design:
                    errors.append(f"{design}/{theme}: data-design={got}")
                await page.screenshot(path=str(OUT / f"quick-{design}-{theme}-idle.png"))
                try:
                    await page.fill(".qa-input, textarea", "Напиши функцию на Python")
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(1200)
                    await page.screenshot(path=str(OUT / f"quick-{design}-{theme}-answer.png"))
                except Exception as exc:
                    errors.append(f"{design}/{theme} answer: {exc}")
                await page.close()
        await browser.close()
    server.shutdown()
    print("\n".join(errors) or "ok")


if __name__ == "__main__":
    asyncio.run(main())
