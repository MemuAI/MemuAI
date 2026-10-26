"""Round 26 (r26-quick-2): headless Chromium checks + screenshots of the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round26_quick2_shots.py OUT_DIR

Checks (exit code 1 on any problem, used by tests/round26_quick2_test.py):
  * opening chat A from the chats list while chat B is on screen keeps A intact (the debounced save used to fire
    after the switch and wrote B's turns under A's id — the opened chat was overwritten);
  * «New chat» (Ctrl+N) in the middle of a streaming answer stops it AND saves the partial answer (it was lost:
    the AbortError arrived after the turns were replaced);
  * retry while an answer streams keeps the running turn (no second request, no lost turn);
  * deleting a chat right after an answer does not bring it back (pending save);
  * markdown: code block, table and KaTeX math are rendered (KaTeX was never loaded in the window);
  * an endpoint error in the en UI is shown in English.
Screens: math-table-dark.png, math-table-light-en.png, error-en.png, partial-saved.png.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")
SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null}"
MODELS = [{"id": "deepseek-v4.1-flash", "caps": {"vision": False, "search": False}}]

RICH = ("Замыкание — функция, которая помнит переменные места, где её создали.\n\n```js\nfunction counter() {\n"
        "  let count = 0;\n  return () => ++count;\n}\n```\n\n| Что | Как |\n|---|---|\n| Хранит | переменные внешней функции |\n"
        "| Область | лексическая |\n\nИ формула: $17 \\times 23 = 20^2 - 3^2 = 391$, а также\n\n$$\\int_0^1 x^2\\,dx = \\tfrac13$$")
SLOW = {"mode": "fast"}

class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files + a real streaming /api/quick/ask (the slow mode sends one delta, then waits)."""

    def log_message(self, *args):  # noqa: D401
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/quick/ask"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        SLOW.setdefault("asks", []).append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(event):
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        try:
            if SLOW["mode"] == "slow":
                send({"event": "delta", "text": "Частичный ответ, который"})
                time.sleep(6)
                send({"event": "delta", "text": " так и не закончился."})
            elif SLOW["mode"] == "error":
                send({"event": "error", "code": "model", "error": SLOW["error"]})
                return
            elif SLOW["mode"] == "rich":
                send({"event": "delta", "text": RICH})
            else:
                send({"event": "delta", "text": "Ответ на: " + str(body.get("text") or "")})
            send({"event": "done", "model": "deepseek-v4.1-flash", "tier": "fast"})
        except (BrokenPipeError, ConnectionResetError):
            pass

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    from app import quick_ask_core as core
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    state = {"prefs": {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru",
                       "quick_ask_model": "deepseek-v4.1-flash"}, "chats": []}

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def chat_of(question: str):
        return next((c for c in state["chats"] if any(t.get("q") == question for t in c["turns"])), None)

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
        if path.startswith("/api/quick/chats/"):
            chat_id = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                state["chats"] = [c for c in state["chats"] if c["id"] != chat_id]
                return await route_obj.fulfill(json={"ok": True})
            chat = next((c for c in state["chats"] if c["id"] == chat_id), None)
            return await route_obj.fulfill(json={"chat": chat} if chat else {"error": "nf"}, status=200 if chat else 404)
        if path == "/api/quick/ask":
            return await route_obj.continue_()
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0, "card": None})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def open_page(theme="dark", lang="ru"):
            state["prefs"].update(theme=theme, language=lang)
            page = await browser.new_page(viewport={"width": 720, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(400)
            return page

        async def ask(page, text):
            await page.fill("#qa-input", text)
            await page.press("#qa-input", "Enter")

        async def shot(page, name):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": 720, "height": max(60, min(900, int(height)))})
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / f"{name}.png"))

        page = await open_page()
        await ask(page, "Вопрос A")
        await page.wait_for_timeout(700)
        await page.keyboard.press("Control+KeyN")
        await ask(page, "Вопрос B")
        await page.wait_for_timeout(700)
        chat_a = chat_of("Вопрос A")
        check(bool(chat_a) and bool(chat_of("Вопрос B")) and chat_a is not chat_of("Вопрос B"),
              f"two chats saved: {[c['title'] for c in state['chats']]}")
        await page.keyboard.press("Control+KeyH")
        await page.wait_for_timeout(400)
        await page.locator("#qa-chats .qa-chat", has_text="Вопрос A").first.click()
        await page.wait_for_timeout(600)
        chat_a = next((c for c in state["chats"] if chat_a and c["id"] == chat_a["id"]), None)
        check(bool(chat_a) and [t["q"] for t in chat_a["turns"]] == ["Вопрос A"],
              f"chat A keeps its own turns after it is opened: {chat_a and [t['q'] for t in chat_a['turns']]}")
        check(len(state["chats"]) == 2, f"no ghost chat: {[c['title'] for c in state['chats']]}")
        answer = await page.inner_text("#qa-answer")
        check("Ответ на: Вопрос A" in answer, f"the opened chat is shown: {answer[:120]}")

        await page.keyboard.press("Control+KeyN")
        await ask(page, "Удаляемый вопрос")
        await page.wait_for_timeout(40)
        current = await page.evaluate("QuickAsk.state().chatId")
        await page.wait_for_timeout(700)
        await page.keyboard.press("Control+KeyH")
        await page.wait_for_timeout(400)
        await page.locator(f"#qa-chats [data-del='{current}']").click()
        await page.wait_for_timeout(600)
        check(chat_of("Удаляемый вопрос") is None, "a deleted chat stays deleted")
        await page.close()

        SLOW["mode"] = "slow"
        SLOW["asks"] = []
        page = await open_page()
        await ask(page, "Долгий вопрос")
        await page.wait_for_timeout(900)
        turns_before = await page.evaluate("QuickAsk.state().turns.length")
        await page.evaluate("document.getElementById('qa-retry') && document.getElementById('qa-retry').click()")
        await page.keyboard.press("Control+KeyR")
        await page.wait_for_timeout(300)
        check(await page.evaluate("QuickAsk.state().busy") is True, "retry while busy does not stop the answer")
        check(await page.evaluate("QuickAsk.state().turns.length") == turns_before, "retry while busy keeps the turn")
        check(len(SLOW["asks"]) == 1, f"retry while busy sends nothing: {len(SLOW['asks'])} requests")
        await page.keyboard.press("Control+KeyN")
        await page.wait_for_timeout(600)
        saved = chat_of("Долгий вопрос")
        check(bool(saved) and saved["turns"][0]["a"].startswith("Частичный ответ"),
              f"the stopped partial answer is saved: {saved}")
        check(await page.evaluate("QuickAsk.state().busy") is False, "the window is not busy after New chat")
        if saved:
            await page.keyboard.press("Control+KeyH")
            await page.wait_for_timeout(400)
            await page.locator("#qa-chats .qa-chat", has_text="Долгий вопрос").first.click()
            await page.wait_for_timeout(500)
            await shot(page, "partial-saved")
        await page.close()

        SLOW["mode"] = "rich"
        for theme, lang, name in (("dark", "ru", "math-table-dark"), ("light", "en", "math-table-light-en")):
            page = await open_page(theme, lang)
            try:
                await page.wait_for_function("!!window.katex", timeout=5000)
            except Exception:
                check(False, f"{name}: KaTeX is loaded in the Quick Ask window")
            await ask(page, "Объясни замыкания")
            await page.wait_for_timeout(800)
            check(await page.locator("#qa-answer table").count() == 1, f"{name}: the table is rendered")
            check(await page.locator("#qa-answer pre code").count() >= 1, f"{name}: the code block is rendered")
            check(await page.locator("#qa-answer .katex").count() >= 2, f"{name}: math is rendered with KaTeX")
            check(await page.locator("#qa-answer .math-src").count() == 0, f"{name}: no raw TeX left")
            overflow = await page.evaluate("""() => { const a = document.getElementById('qa-answer');
                return Array.from(a.querySelectorAll('.katex-display, .table-wrap, pre')).some((el) =>
                  el.getBoundingClientRect().right > a.getBoundingClientRect().right + 1 && getComputedStyle(el).overflowX === 'visible'); }""")
            check(not overflow, f"{name}: nothing sticks out of the answer")
            await shot(page, name)
            await page.close()

        SLOW["mode"] = "error"
        SLOW["error"] = "The server does not know this model (HTTP 404): check the model name in Settings → Quick Ask."
        page = await open_page("dark", "en")
        await ask(page, "hello")
        await page.wait_for_timeout(700)
        text = await page.inner_text("#qa-answer")
        check("does not know this model" in text, f"the error is shown: {text[:200]}")
        await shot(page, "error-en")
        await page.close()
        await browser.close()
    server.shutdown()
    print("problems:", len(problems))
    for item in problems:
        print(" -", item)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r26-quick2-shots"))))
