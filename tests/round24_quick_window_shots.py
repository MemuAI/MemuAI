"""Round 24 (quick-window): headless checks + screenshots of the Quick Ask window (mocked server).

Not part of the unittest suite (needs Playwright + Chromium); tests/round24_quick_window_test.py runs it when
both are available:

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round24_quick_window_shots.py OUT_DIR

Checks (exit 1 on any failure):
* focus loss never posts {action:"blur"}/"hide" (only Escape / explicit ways close the window);
* Escape is layered: menu → DFY offer → running answer (stop) → hide;
* DFY: the offer appears for «забронируй…», Ctrl+Enter starts a task with source "quick", the card shows steps,
  a secret question renders a password field, the answer goes to /answer and the field is cleared,
  «done» shows the result; the card survives a hide/show (re-attached from localStorage);
* a dropped text file becomes context of the next question.
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
    out.mkdir(parents=True, exist_ok=True)
    server, base = serve()
    errors: list = []
    calls: list = []
    task = {"id": "t1", "goal": "", "status": "running", "steps": [{"n": 1, "action": "Открываю сайт", "detail": "booking.com"}]}

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
            return await route_obj.fulfill(json={"prefs": {"quick_ask_model": "m1", "language": "ru"}, "status": {"native": True}, "ui": {"onboarded": True},
                                                 "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {"vision": True}}], "image_models": []})
        if path == "/api/quick/history":
            return await route_obj.fulfill(json={"items": [], "enabled": True})
        if path == "/api/quick/ask":
            await asyncio.sleep(0.05)
            events = [{"event": "delta", "text": "Ответ"}, {"event": "done", "model": "m1"}]
            return await route_obj.fulfill(status=200, content_type="text/event-stream", body="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        if path == "/api/dfy/status":
            return await route_obj.fulfill(json={"enabled": True, "model": "vision-model", "model_has_vision": True, "ready": True, "reason": ""})
        if path == "/api/dfy/tasks" and request.method == "POST":
            data = json.loads(body or "{}")
            task["goal"] = data.get("goal", "")
            task["_source"] = data.get("source")
            return await route_obj.fulfill(json={"id": "t1", "status": "queued"})
        if path == "/api/dfy/tasks/t1/events":
            return await route_obj.fulfill(status=500, body="")     # force the polling fallback
        if path == "/api/dfy/tasks/t1":
            return await route_obj.fulfill(json={k: v for k, v in task.items() if not k.startswith("_")})
        if path == "/api/dfy/tasks/t1/answer":
            task["_answer"] = json.loads(body or "{}")
            task.update(status="done", question=None, result="Номер забронирован: Hotel Aurora, 12–14 ноября.")
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/dfy/tasks/t1/cancel":
            task.update(status="cancelled")
            return await route_obj.fulfill(json={"id": "t1", "status": "cancelled"})
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
        await page.wait_for_timeout(500)
        await page.evaluate(SHOW)
        await page.wait_for_timeout(300)

        # 1. focus loss never hides
        calls.clear()
        await page.evaluate("window.dispatchEvent(new Event('blur'))")
        await page.wait_for_timeout(700)
        check(not [c for c in calls if c[1] == "/api/quick/window" and ('"blur"' in c[2] or '"hide"' in c[2])], "blur must not hide the window")

        # 2. DFY offer + Ctrl+Enter
        await page.fill("#qa-input", "забронируй отель в Казани на 12–14 ноября")
        await page.dispatch_event("#qa-input", "input")
        await page.wait_for_timeout(700)
        check(await page.locator(".qdfy-offer").count() == 1, "the DFY offer should appear for an action-like request")
        await page.screenshot(path=str(out / "r24qw-dfy-offer.png"))
        # Escape drops the offer first (one level), the window stays
        calls.clear()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        check(await page.locator(".qdfy-offer").count() == 0, "Escape should drop the offer first")
        check(not [c for c in calls if '"hide"' in c[2]], "Escape on the offer must not hide the window")
        await page.focus("#qa-input")
        await page.keyboard.press("Control+Enter")
        await page.wait_for_timeout(2200)
        check(task.get("_source") == "quick", "the task should start with source=quick")
        check(await page.locator(".qdfy-card").count() == 1, "the progress card should appear")
        # Round 34 (r34-quick-agentic): a step shows the server's line (detail) and the action word only without one
        check("booking.com" in (await page.inner_text(".qdfy-card .qdfy-steps")), "steps are shown")
        await page.screenshot(path=str(out / "r24qw-dfy-running.png"))

        # 3. secret question
        task.update(status="waiting_user", question={"kind": "secret", "secret_kind": "login", "prompt": "Пароль от booking.com", "fields": [{"name": "password", "label": "Пароль"}]},
                    steps=task["steps"] + [{"n": 2, "action": "Вход", "detail": "нужен пароль"}])
        await page.wait_for_timeout(2000)
        check(await page.locator(".qdfy-card input[type=password]").count() == 1, "a secret question renders a password field")
        await page.screenshot(path=str(out / "r24qw-dfy-secret.png"))
        await page.fill(".qdfy-card input[type=password]", "hunter2")
        # Escape inside the field leaves the field (does not hide)
        calls.clear()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        check(not [c for c in calls if '"hide"' in c[2]], "Escape inside the DFY field must not hide")
        await page.focus(".qdfy-card input[type=password]")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2200)
        # Round 25 (a3-dfy): secrets go by the contract {secret: {fields: {...}}}, never as a plain «answer»
        check(((task.get("_answer") or {}).get("secret") or {}).get("fields", {}).get("password") == "hunter2", "the secret goes to /answer")
        check("hunter2" not in (await page.content()), "the secret is not left in the page")
        check("забронирован" in (await page.inner_text(".qa-plugin.qdfy")), "the result is shown")
        await page.screenshot(path=str(out / "r24qw-dfy-done.png"))

        # 4. hide/show keeps the card (re-attached)
        await page.evaluate("QuickAsk.onHide()")
        await page.evaluate(SHOW)
        await page.wait_for_timeout(800)
        check(await page.locator(".qdfy-card").count() == 1, "the DFY card survives hide/show")

        # 5. Escape with nothing open hides the window
        await page.focus("#qa-input")
        calls.clear()
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check([c for c in calls if c[1] == "/api/quick/window" and '"hide"' in c[2]], "Escape with nothing open should hide")

        # 6. text file drop → context
        await page.evaluate(SHOW)
        await page.wait_for_timeout(300)
        await page.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['def f():\\n  return 42\\n'], 'code.py', {type: 'text/x-python'}));
          document.getElementById('qa-card').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""")
        await page.wait_for_timeout(400)
        check("code.py" in (await page.evaluate("QuickAsk.state().selection")), "a dropped text file becomes context")
        await page.screenshot(path=str(out / "r24qw-file.png"))
        await browser.close()
    server.shutdown()
    for e in errors:
        print("FAIL:", e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r24qw"))))
