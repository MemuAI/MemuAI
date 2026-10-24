"""Round 26 (r26-quick-core): headless Chromium checks + screenshots of the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round26_quick_core_shots.py OUT_DIR

Checks (exit code 1 on any problem, used by tests/round26_quick_core_test.py):
  * a command result (a card from /api/assistant/run) is saved as a turn of the quick chat, is not drawn twice while
    the card is on screen, and «Недавние» in the next window opens THAT chat with the answer (it only put the
    question into the field before);
  * «Сделай саммари страницы» is not taken by the commands plugin (no clipboard summarize) and goes to /api/quick/ask;
    the server's «page» event replaces the turn's page;
  * QuickAsk.onPage(page, true) replaces a stale page chip, onPage(null, true) removes it.
Screens: recent-dark, restored-chat-light, page-replaced-dark, no-page-dark.
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
SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:%s}"
MODELS = [{"id": "gemini-2.5-flash", "caps": {"vision": True, "search": True}}]
CARD = {"id": "k1", "action": "text.summarize", "title": "Кратко пересказать", "status": "done", "kind": "text",
        "message": "• Первый пункт пересказа\n• Второй пункт\n\nВывод: всё понятно.", "copy": "x"}


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
             "chats": [], "runs": [], "asks": []}

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async def route(route_obj, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        query = request.url.split("?", 1)[1] if "?" in request.url else ""
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
            limit = int(dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("limit", 60))
            return await route_obj.fulfill(json={"items": core.search_chats(state["chats"], "", limit), "total": len(state["chats"])})
        if path.startswith("/api/quick/chats/"):
            chat = next((c for c in state["chats"] if c["id"] == path.rsplit("/", 1)[1]), None)
            return await route_obj.fulfill(json={"chat": chat} if chat else {"error": "nf"}, status=200 if chat else 404)
        if path == "/api/quick/history":
            return await route_obj.fulfill(json={"items": [{"q": "Сделай саммари страницы"}], "enabled": True})
        if path == "/api/quick/ask":
            body = json.loads(request.post_data or "{}")
            state["asks"].append(body)
            events = [{"event": "page", "source": "fetch", "title": "Статья на Хабре", "url": "https://habr.com/ru/articles/1/", "chars": 900},
                      {"event": "delta", "text": "Суть статьи в трёх предложениях."},
                      {"event": "done", "text": "Суть статьи в трёх предложениях.", "model": "gemini-2.5-flash", "tier": "fast"}]
            return await route_obj.fulfill(status=200, content_type="text/event-stream",
                                           body="".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events))
        if path == "/api/assistant/run":
            state["runs"].append(json.loads(request.post_data or "{}"))
            return await route_obj.fulfill(json={"card": state.get("card") or CARD})
        if path == "/api/work/computer/run":
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/work/computer/state":
            return await route_obj.fulfill(json=state.get("cu") or {"status": "idle", "logs": []})   # Round 30: quick-computer.js asks on open
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

        async def open_page(theme="dark", page_ctx="null"):
            state["prefs"]["theme"] = theme
            page = await browser.new_page(viewport={"width": 720, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("QuickAsk.onShow(" + SHOW % page_ctx + ")")
            await page.wait_for_timeout(500)
            return page

        async def shot(page, name):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": 720, "height": max(60, int(height))})
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(out / f"{name}.png"))

        # 1. a command result is kept in the chat; «Недавние» opens it
        page = await open_page()
        await page.fill("#qa-input", "кратко перескажи: длинный текст из письма")
        await page.press("#qa-input", "Enter")
        await page.wait_for_timeout(900)
        check(len(state["runs"]) == 1, "the command went to /api/assistant/run")
        check(await page.locator(".mqc-card").count() == 1, "the result card is shown")
        check(await page.is_hidden("#qa-answer"), "the live card is not drawn a second time as an answer")
        check(bool(state["chats"]) and "Вывод: всё понятно." in state["chats"][0]["turns"][0]["a"],
              f"the command result is saved into the quick chat: {state['chats']}")
        await page.close()
        page = await open_page()
        rows = await page.locator("#qa-suggest .qa-row").all_inner_texts()
        check(any("кратко перескажи: длинный текст" in r for r in rows), f"«Недавние» lists the chat: {rows}")
        await shot(page, "recent-dark")
        await page.locator("#qa-suggest .qa-row", has_text="кратко перескажи: длинный текст").first.click()
        await page.wait_for_timeout(500)
        answer = await page.inner_text("#qa-answer") if await page.is_visible("#qa-answer") else ""
        check("Вывод: всё понятно." in answer, f"the opened chat shows the answer, got {answer[:200]!r}")
        check(await page.input_value("#qa-input") == "", "opening a recent chat does not just refill the prompt")
        await page.close()
        state["prefs"]["theme"] = "light"
        page = await open_page(theme="light")
        await page.locator("#qa-suggest .qa-row", has_text="кратко перескажи").first.click()
        await page.wait_for_timeout(500)
        await shot(page, "restored-chat-light")
        await page.close()

        # 2. «Сделай саммари страницы» goes to the model (never the local clipboard summarize); the page follows the server
        stale = "{id:'a-1', browser:'Google Chrome', title:'WebGL Aquarium', url:'https://webglsamples.org/aquarium/aquarium.html'}"
        page = await open_page(page_ctx=stale)
        runs = len(state["runs"])
        await page.evaluate("QuickAsk.onPage({id:'b-2', browser:'Google Chrome', title:'Статья на Хабре', url:'https://habr.com/ru/articles/1/'}, true)")
        await page.wait_for_timeout(200)
        chip = await page.inner_text(".qa-chip-page")
        check("Хабре" in chip and "Aquarium" not in chip, f"onPage(page, true) replaces the stale chip: {chip!r}")
        await shot(page, "page-replaced-dark")
        await page.evaluate("QuickAsk.onPage(null, true)")
        await page.wait_for_timeout(200)
        check(await page.locator(".qa-chip-page").count() == 0, "onPage(null, true) removes the chip")
        await page.fill("#qa-input", "Сделай саммари страницы")
        await page.press("#qa-input", "Enter")
        await page.wait_for_timeout(900)
        check(len(state["runs"]) == runs, "a page question is not taken by the local commands (clipboard summarize)")
        check(len(state["asks"]) == 1, "a page question goes to /api/quick/ask")
        turn_page = await page.evaluate("QuickAsk.state().turns[0].page")
        check(bool(turn_page) and turn_page.get("url") == "https://habr.com/ru/articles/1/",
              f"the turn remembers the page the server read: {turn_page}")
        await shot(page, "no-page-dark")
        await page.close()

        # 3. Computer Use card (bug from r26-automation): no model instruction (report) on error, the error once,
        #    the approval shows pending_action.description, the status is translated
        state["card"] = {"id": "cu1", "action": "computer.use", "title": "Управление компьютером", "status": "done",
                         "message": "Запускаю", "client": {"type": "computer", "task": "открой блокнот"}}
        state["cu"] = {"status": "waiting_approval", "logs": ["Шаг 1: снимок экрана"],
                       "pending_action": {"type": "click", "description": "Нажать «Пуск» в левом нижнем углу"}}
        page = await open_page()
        await page.fill("#qa-input", "открой блокнот через управление компьютером")
        await page.press("#qa-input", "Enter")
        await page.wait_for_timeout(1800)
        text = await page.inner_text(".qcu")
        check("Нажать «Пуск»" in text and "ждёт вашего разрешения" in text.lower(), f"the approval card: {text!r}")
        state["cu"] = {"status": "error", "logs": ["Шаг 1: снимок экрана", "Ошибка: окно не найдено"], "error": "окно не найдено",
                       "report": "Computer Use завершился с ошибкой. Продолжайте основную задачу без него."}
        await page.wait_for_timeout(1500)
        text = await page.inner_text(".qcu")
        check("Продолжайте основную задачу" not in text, f"the model instruction is not shown: {text!r}")
        check(text.count("окно не найдено") == 1, f"the error is shown once: {text!r}")
        check("ошибк" in text.lower() and "error" not in text, f"the status is translated: {text!r}")
        check(not any("через управление компьютером" in (c.get("title") or "") for c in state["chats"]),
              "a hand-over card (Computer Use) is not saved as an answered chat")
        await shot(page, "computer-error-dark")
        await page.close()
        state["card"] = None
        await browser.close()
    server.shutdown()
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r26/r26-quick-core"))))
