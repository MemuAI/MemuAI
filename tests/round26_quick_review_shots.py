"""Round 26 (r26-quick-review): headless Chromium checks + screenshots of the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round26_quick_review_shots.py OUT_DIR

Checks (exit code 1 on any problem, used by tests/round26_quick_review_test.py), all on mocked /api/*:
  * a command confirmed on its card («закрой хром» → «Выполнить») becomes a turn of the quick chat (it was empty);
  * Computer Use started from the window writes its outcome into the chat — done (report) and a failed start (409);
  * a DFY task started from the window (Ctrl+Enter) writes its result into the chat;
  * a double click on «Отправить» does not stop the answer it has just started («Остановлено.»);
  * DFY toasts of kind error / warn are drawn as errors (red), not as neutral toasts.
Screens: confirmed-command, cu-done, cu-busy, dfy-done, dfy-not-ready-toast, dblclick.
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
SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null}"
MODELS = [{"id": "gemini-2.5-flash", "caps": {"vision": True, "search": True}}]
CLOSE_CONFIRM = {"id": "c1", "action": "app.close", "title": "Закрыть приложение", "status": "confirm",
                 "message": "Закрыть Google Chrome?", "confirm": {"token": "tok-close", "tier": "confirm", "countdown": 0}}
CLOSE_DONE = {"id": "c2", "action": "app.close", "title": "Закрыть приложение", "status": "done", "message": "Закрыл Google Chrome."}
CU_CONFIRM = {"id": "cu1", "action": "computer.use", "title": "Управление компьютером", "status": "confirm",
              "message": "Запустить?", "confirm": {"token": "tok-cu", "tier": "confirm", "countdown": 0}}
CU_RUNNING = {"id": "cu2", "action": "computer.use", "title": "Управление компьютером", "status": "running",
              "message": "Передаю управление", "kind": "computer", "client": {"type": "computer", "task": "открой блокнот", "model": "vl"}}

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    from app import quick_ask_core as core
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    state = {"prefs": {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru",
                       "quick_ask_model": "gemini-2.5-flash"},
             "chats": [], "asks": 0, "run_card": CLOSE_CONFIRM, "confirm_card": CLOSE_DONE, "cu_busy": False,
             "dfy_ready": True, "dfy_answers": []}

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async def route(route_obj, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": state["prefs"], "status": {"native": True, "pinned": False,
                                                 "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                                 "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": MODELS, "image_models": []})
        if path == "/api/quick/chats" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            state["chats"] = core.upsert_chat(state["chats"], body.get("chat"))
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/quick/chats":
            return await route_obj.fulfill(json={"items": core.search_chats(state["chats"], "", 60), "total": len(state["chats"])})
        if path == "/api/quick/ask":
            state["asks"] += 1
            await asyncio.sleep(1.2)
            events = [{"event": "delta", "text": "Красный, жёлтый, синий."},
                      {"event": "done", "text": "Красный, жёлтый, синий.", "model": "gemini-2.5-flash", "tier": "fast"}]
            return await route_obj.fulfill(status=200, content_type="text/event-stream",
                                           body="".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events))
        if path == "/api/assistant/run":
            return await route_obj.fulfill(json={"card": state["run_card"]})
        if path == "/api/assistant/confirm":
            return await route_obj.fulfill(json={"card": state["confirm_card"]})
        if path == "/api/work/computer/run":
            if state["cu_busy"]:
                return await route_obj.fulfill(status=409, json={"error": "Компьютерный агент уже работает"})
            return await route_obj.fulfill(json={"ok": True, "status": "starting"})
        if path == "/api/work/computer/state":
            return await route_obj.fulfill(json={"status": "done", "logs": [{"text": "click: ok"}], "report": "Блокнот открыт.", "error": ""})
        if path == "/api/dfy/status":
            return await route_obj.fulfill(json={"enabled": True, "ready": state["dfy_ready"], "model": "vl",
                                                 "model_has_vision": True, "reason": "Microsoft Edge не найден"})
        if path == "/api/dfy/tasks" and request.method == "POST":
            return await route_obj.fulfill(json={"id": "t1", "version": 0, "status": "queued", "goal": json.loads(request.post_data)["goal"]})
        if path.startswith("/api/dfy/tasks/t1/events"):
            return await route_obj.fulfill(status=404, body="")
        if path.startswith("/api/dfy/tasks/t1/answer"):
            state["dfy_answers"].append(json.loads(request.post_data or "{}"))
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/dfy/tasks/t1":
            steps = [{"action": "navigate", "detail": "https://example.com"}]
            if state["dfy_answers"]:
                return await route_obj.fulfill(json={"id": "t1", "version": 9, "status": "done", "result": "Столик забронирован, номер A-17.", "steps": steps})
            return await route_obj.fulfill(json={"id": "t1", "version": 1, "status": "waiting_user", "steps": steps,
                                                 "question": {"id": "q1", "kind": "confirm", "prompt": "Подтвердить бронь?", "amount": "0 ₽"}})
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

        async def open_page():
            page = await browser.new_page(viewport={"width": 720, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("localStorage.removeItem('memu_quick_dfy_tasks')")
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(500)
            return page

        async def shot(page, name):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": 720, "height": max(60, min(1000, int(height)))})
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(out / f"{name}.png"))

        async def turns(page):
            return await page.evaluate("QuickAsk.state().turns.map(t => [t.q, t.a])")

        async def wait_turn(page, needle, seconds=8):
            for _ in range(seconds * 5):
                if any(needle in (a or "") for _, a in await turns(page)):
                    return True
                await page.wait_for_timeout(200)
            return False

        page = await open_page()
        await page.fill("#qa-input", "закрой хром")
        await page.press("#qa-input", "Enter")
        await page.wait_for_timeout(700)
        await page.locator(".mqc-card [data-mqc='confirm']").first.click()
        check(await wait_turn(page, "Закрыл Google Chrome."), f"the confirmed command is a chat turn: {await turns(page)}")
        check(["закрой хром", "Закрыл Google Chrome."] in await turns(page), f"the turn keeps the question: {await turns(page)}")
        await shot(page, "confirmed-command")
        await page.close()

        state.update(run_card=CU_CONFIRM, confirm_card=CU_RUNNING)
        for busy in (False, True):
            state["cu_busy"] = busy
            page = await open_page()
            await page.fill("#qa-input", "управляй компьютером: открой блокнот")
            await page.press("#qa-input", "Enter")
            await page.wait_for_timeout(700)
            await page.locator(".mqc-card [data-mqc='confirm']").first.click()
            needle = "уже работает" if busy else "Блокнот открыт."
            check(await wait_turn(page, needle), f"Computer Use (busy={busy}) outcome is a chat turn: {await turns(page)}")
            rows = await turns(page)
            check(any(q == "управляй компьютером: открой блокнот" for q, _ in rows), f"Computer Use turn keeps the request: {rows}")
            await shot(page, "cu-busy" if busy else "cu-done")
            await page.close()

        page = await open_page()
        await page.fill("#qa-input", "забронируй столик на двоих на 19:00")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Control+Enter")
        await page.wait_for_timeout(2500)
        approve = page.locator("[data-qdfy='approve']")
        check(await approve.count() > 0, "the DFY question card is shown")
        if await approve.count():
            await approve.first.click()
            check(await wait_turn(page, "Столик забронирован", 10), f"the DFY result is a chat turn: {await turns(page)}")
        await shot(page, "dfy-done")
        await page.close()
        state["dfy_ready"] = False
        page = await open_page()
        await page.fill("#qa-input", "забронируй столик на двоих на 19:00")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Control+Enter")
        await page.wait_for_timeout(800)

        check(await page.locator("[data-qdfy='settings']").count() > 0, "a DFY «not ready» card offers the DFY settings")
        await page.evaluate("MemuQuick.toast('x', 'error')")
        cls = await page.evaluate("document.getElementById('qa-toast').className")
        check("err" in cls.split(), f"a toast of kind «error» is drawn as an error: {cls!r}")
        await shot(page, "dfy-not-ready-toast")
        await page.close()

        state["run_card"] = {"fallback": True}
        page = await open_page()
        await page.fill("#qa-input", "Назови три цвета радуги\nчерез запятую")
        await page.dblclick("#qa-send")
        await page.wait_for_timeout(300)
        for _ in range(50):
            if not await page.evaluate("QuickAsk.state().busy"):
                break
            await page.wait_for_timeout(200)
        rows = await turns(page)
        check(state["asks"] == 1, f"one request for a double click: {state['asks']}")
        check(bool(rows) and rows[-1][1] == "Красный, жёлтый, синий.", f"the answer was not stopped: {rows}")
        await shot(page, "dblclick")
        await page.close()
        await browser.close()
    server.shutdown()
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r26/r26-finisher"))))
