"""Round 25 (a6-quick, second pass): headless checks of Quick Ask page fixes (mocked server, real command engine).

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round25_quick_fixes_shots.py OUT_DIR

Checks (exit 1 on any failure):
* a stream whose last event has no trailing blank line still finishes with the answer (not «cut off»);
* an «error» event at the very end of such a stream shows the real error;
* «Что у меня на экране?» does not ask the model when the screenshot failed;
* a fresh window drops the command cards of the previous session;
* the pin toast fits one line.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"
CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"
SHOW = "QuickAsk.onShow({fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8})"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve():
    handler = functools.partial(Quiet, directory=str(STATIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    from app import assistant_commands
    out.mkdir(parents=True, exist_ok=True)
    eng = assistant_commands.Engine()
    clip = {"text": ""}
    eng.backend.clipboard_get = lambda: clip["text"]
    eng.backend.clipboard_set = lambda text: clip.__setitem__("text", text)
    server, base = serve()
    errors: list = []
    calls: list = []
    stream = {"events": [], "tail": True}

    async def route(route_obj, request):
        url = request.url
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        body = request.post_data or ""
        if path.startswith("/api/"):
            calls.append((request.method, path, body))
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/dfy-client.js":
            return await route_obj.fulfill(status=404, body="")
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": {"quick_ask_model": "m1", "language": "ru"}, "status": {"native": True, "pinned": False},
                                                 "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {"vision": True}}], "image_models": []})
        if path == "/api/quick/history":
            return await route_obj.fulfill(json={"items": [], "enabled": True})
        if path == "/api/quick/screenshot":
            return await route_obj.fulfill(status=500, json={"error": "Снимок не удался"})
        if path == "/api/quick/window":
            data = json.loads(body or "{}")
            return await route_obj.fulfill(json={"ok": True, **({"pinned": bool(data.get("pinned"))} if data.get("action") == "pin" else {})})
        if path == "/api/quick/ask":
            text = "".join("data: " + json.dumps(e) + "\n\n" for e in stream["events"])
            if not stream["tail"]:
                text = text.rstrip("\n")            # the endpoint closed right after the last event
            return await route_obj.fulfill(status=200, content_type="text/event-stream", body=text)
        if path == "/api/assistant/run":
            data = json.loads(body or "{}")
            found = await asyncio.to_thread(eng.match, data.get("text", ""), "ru", False)
            if not found:
                return await route_obj.fulfill(json={"card": None, "match": None})
            card = await eng.run(found["action"], found["params"], source="local", lang="ru")
            return await route_obj.fulfill(json={"card": card, "match": found})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0, "timers": [], "ok": True,
                                                 "settings": {"enabled": True, "local_match": True, "voice": {"enabled": False}}})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"ok": True})
        return await route_obj.continue_()

    def check(cond, message):
        if not cond:
            errors.append(message)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 680, "height": 560}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        await page.route("**/*", route)
        await page.goto(base + "/quick/?native=1", wait_until="load")
        await page.wait_for_timeout(700)
        await page.evaluate(SHOW)
        await page.wait_for_timeout(500)

        # 1. the pin toast fits one line
        await page.click("#qa-pin")
        await page.wait_for_timeout(300)
        fits = await page.evaluate("""() => { const t = [...document.querySelectorAll('.qa-toast, [class*=toast]')].find((x) => x.offsetParent && x.textContent.includes('Закреплено'));
          return !t || t.scrollWidth <= t.clientWidth + 1; }""")
        check(fits, "the pin toast is not cut with an ellipsis")
        await page.screenshot(path=str(out / "r25qf-pin-toast.png"))
        await page.click("#qa-pin")
        await page.wait_for_timeout(200)

        # 2. a stream without the trailing blank line
        stream.update(events=[{"event": "delta", "text": "Готово: **42**"}, {"event": "done", "model": "m1", "ms": 120}], tail=False)
        await page.fill("#qa-input", "сколько будет шесть на семь словами")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(900)
        answer = await page.inner_text("#qa-card")
        check("42" in answer and "оборвался" not in answer, "the last event without a blank line still finishes the answer")
        await page.screenshot(path=str(out / "r25qf-stream-tail.png"))
        await page.evaluate(SHOW)
        await page.wait_for_timeout(300)
        stream.update(events=[{"event": "error", "error": "Модель m1 недоступна", "code": "model"}], tail=False)
        await page.fill("#qa-input", "ещё вопрос для модели")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(900)
        answer = await page.inner_text("#qa-card")
        check("недоступна" in answer and "оборвался" not in answer, "the real error at the end of the stream is shown")
        await page.screenshot(path=str(out / "r25qf-stream-error.png"))

        # 3. a fresh window drops old command cards
        await page.evaluate(SHOW)
        await page.wait_for_timeout(400)
        await page.fill("#qa-input", "#ff8800 в rgb")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(900)
        check(await page.locator(".mqc-swatch").count() == 1, "the colour card is shown")
        await page.screenshot(path=str(out / "r25qf-color.png"))
        await page.evaluate("QuickAsk.onHide()")
        await page.evaluate(SHOW)
        await page.wait_for_timeout(500)
        check(await page.locator(".mqc-swatch").count() == 0, "a fresh window starts without the old cards")

        # 4. «Что у меня на экране?» with a failing screenshot does not reach the model
        calls.clear()
        rows = page.locator(".qa-row", has_text="Что у меня на экране")
        if await rows.count():
            await rows.first.click(timeout=3000)
            await page.wait_for_timeout(600)
            check(not [c for c in calls if c[1] == "/api/quick/ask"], "no question without the screenshot")
        else:
            errors.append("the «Что у меня на экране?» idea is missing")
        await page.screenshot(path=str(out / "r25qf-screen-failed.png"))
        await browser.close()
    server.shutdown()
    for e in errors:
        print("FAIL:", e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r25/quick/fixes"))))
