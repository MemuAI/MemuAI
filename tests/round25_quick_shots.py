"""Round 25 (a6-quick): headless Chromium checks + screenshots of the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round25_quick_shots.py OUT_DIR

Checks (exit code 1 on any problem, also used by tests/round25_quick_test.py):
  * the pin button is always in the footer (native window), Ctrl+P / the button toggle it, the pinned state is
    visible (aria-pressed, card[data-pinned]) and the footer still fits at 520 px;
  * Enter while an answer streams does not abort it (it used to — send() doubles as «stop»);
  * a file dropped next to the card (the stage margin) is attached instead of navigating the window away;
  * «Открыть в MemuAI» names its real shortcut (Ctrl+Shift+Enter);
  * the new tool cards (colour swatch, Base64, dates) render.
Screens: pinned-dark/light, unpinned-520, tool-color, tool-base64, tool-date, tool-json-error.
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
from tests.round25_chat_test import _chromium
CHROME = _chromium()
SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:%s}"
MODELS = [{"id": "gemini-2.5-flash", "caps": {"vision": True, "search": True}}]
CARDS = {
    "color": {"id": "k1", "action": "color.convert", "title": "Цвет: HEX, RGB, HSL", "status": "done", "message": "#ff8800",
              "swatch": "#ff8800", "copy": "#ff8800",
              "details": [["HEX", "#ff8800"], ["RGB", "rgb(255, 136, 0)"], ["HSL", "hsl(32, 100%, 50%)"]]},
    "base64": {"id": "k2", "action": "text.encode", "title": "Base64 и URL-кодирование", "status": "done", "kind": "code",
               "message": "0J/RgNC40LLQtdGCLCDQvNC40YAhINCt0YLQviDQtNC70LjQvdC90LDRjyDRgdGC0YDQvtC60LAg0LHQtdC3INC/0YDQvtCx0LXQu9C+0LI=",
               "copy": "x", "details": [["Base64", "закодировано"], ["Буфер", "результат скопирован"]]},
    "date": {"id": "k3", "action": "date.calc", "title": "Калькулятор дат", "status": "done",
             "message": "До 1 января 2027, пятница — 85 дней", "copy": "85", "details": [["Недель", "12.1"]]},
    "json": {"id": "k4", "action": "json.format", "title": "Форматировать JSON", "status": "error", "code": "bad_input",
             "message": "JSON с ошибкой — строка 1, столбец 2: Expecting property name enclosed in double quotes"},
}

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    state = {"prefs": {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru",
                       "quick_ask_model": "gemini-2.5-flash", "quick_ask_screenshot_tool": True},
             "pinned": False, "card": None, "window": []}

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async def route(route_obj, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": state["prefs"], "status": {"native": True, "pinned": state["pinned"],
                                                 "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                                 "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": MODELS, "image_models": []})
        if path == "/api/quick/window":
            body = json.loads(request.post_data or "{}")
            state["window"].append(body)
            if body.get("action") == "pin":
                state["pinned"] = bool(body.get("pinned"))
                return await route_obj.fulfill(json={"ok": True, "pinned": state["pinned"]})
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/assistant/run":
            return await route_obj.fulfill(json={"card": state["card"]} if state["card"] else {"matched": False})
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

        async def open_page(theme="dark", width=720, pinned=False):
            state["prefs"]["theme"] = theme
            state["pinned"] = pinned
            page = await browser.new_page(viewport={"width": width, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("QuickAsk.onShow(" + SHOW % ("true" if pinned else "false") + ")")
            await page.wait_for_timeout(400)
            return page

        async def shot(page, name, width=720):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(60, int(height))})
            await page.wait_for_timeout(250)
            over = await page.evaluate("""() => { const b = document.getElementById('qa-bar').getBoundingClientRect();
              return Array.from(document.querySelectorAll('#qa-bar > button:not([hidden]), #qa-plugin-buttons > button'))
                .filter((k) => { const r = k.getBoundingClientRect(); return r.width > 0 && r.right > b.right + .5; }).map((k) => k.id); }""")
            check(not over, f"footer overflow in {name}: {over}")
            await page.screenshot(path=str(out / f"{name}.png"))

        page = await open_page(width=520)
        check(await page.is_visible("#qa-pin"), "the pin button is visible in an unpinned window")
        check(await page.get_attribute("#qa-pin", "aria-pressed") == "false", "unpinned → aria-pressed=false")
        await shot(page, "unpinned-520", width=520)
        await page.click("#qa-pin")
        await page.wait_for_timeout(300)
        check(state["pinned"] is True, "the pin button asks the native side to pin")
        check(await page.get_attribute("#qa-pin", "aria-pressed") == "true", "pinned → aria-pressed=true")
        check(await page.get_attribute("#qa-card", "data-pinned") == "1", "pinned → card[data-pinned=1]")
        await page.focus("#qa-input")
        await page.keyboard.press("Control+KeyP")
        await page.wait_for_timeout(300)
        check(state["pinned"] is False, "Ctrl+P unpins")
        await page.close()
        for theme in ("dark", "light"):
            page = await open_page(theme=theme, pinned=True)
            check(await page.get_attribute("#qa-card", "data-pinned") == "1", f"a window shown pinned is marked ({theme})")
            await shot(page, f"pinned-{theme}")
            await page.close()

        page = await open_page()
        aborted = await page.evaluate("""async () => {
          const S = QuickAsk.state(); S.busy = true; S.controller = new AbortController();
          const input = document.getElementById('qa-input'); input.value = 'ещё вопрос';
          input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
          await new Promise((r) => setTimeout(r, 50));
          const out = S.controller.signal.aborted; S.busy = false; S.controller = null; return out; }""")
        check(aborted is False, "Enter during a streaming answer must not abort it")
        check(await page.input_value("#qa-input") == "ещё вопрос", "the typed follow-up stays in the field")

        await page.evaluate("""() => { const dt = new DataTransfer();
          dt.items.add(new File(['print(1)\\n'], 'a.py', {type: 'text/x-python'}));
          const ev = new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true});
          document.getElementById('qa-stage').dispatchEvent(ev); window.__dropPrevented = ev.defaultPrevented; }""")
        await page.wait_for_timeout(300)
        check(await page.evaluate("window.__dropPrevented") is True, "a drop outside the card is prevented (no navigation)")
        check("print(1)" in await page.evaluate("QuickAsk.state().selection"), "a text file dropped outside the card is attached")
        await page.close()

        page = await open_page()
        await page.evaluate("""() => { const S = QuickAsk.state(); S.turns = [{q: 'Привет', a: 'Здравствуйте!', model: 'gemini-2.5-flash', tier: 'fast',
          done: true, images: [], sources: [], shots: []}]; QuickAsk.render(); }""")

        await page.click("#qa-act-more")
        await page.wait_for_timeout(200)
        title = await page.inner_text("#qa-open")
        check("Ctrl" in title and "Shift" in title, f"«Открыть в MemuAI» names Ctrl+Shift+Enter, got {title!r}")
        await page.close()
        for name, theme in (("color", "dark"), ("base64", "light"), ("date", "dark"), ("json", "light")):
            state["card"] = CARDS[name]
            page = await open_page(theme=theme)
            await page.fill("#qa-input", {"color": "#ff8800 в rgb", "base64": "base64 Привет, мир!", "date": "сколько дней до нового года",
                                          "json": "json {a:1}"}[name])
            await page.press("#qa-input", "Enter")
            await page.wait_for_timeout(700)
            if name == "color":
                check(await page.locator(".mqc-swatch").count() == 1, "the colour card shows a swatch")
            await shot(page, f"tool-{name}")
            await page.close()
        state["card"] = None
        await browser.close()
    server.shutdown()
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r25/quick"))))
