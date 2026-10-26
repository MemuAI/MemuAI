"""Round 29 (r29-quick-explorer): headless screenshots of the Explorer / extras plugins in the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round29_quick_explorer_shots.py OUT_DIR

The page runs against mocks of /api/* (no Windows needed). Screens: the folder chip with actions, the rename
change card, a diff card, duplicates, eyedropper, currency — ru/dark and en/light, classic and glass designs.
Exit code 1 on a page error.
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
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "")
SHOW = "{fresh:true, maxHeight:700, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null}"

CTX = {"id": "a1x", "seq": 1, "kind": "explorer", "app": "Explorer", "exe": "explorer.exe", "title": "Downloads",
       "folder": "C:\\Users\\me\\Downloads", "folder_name": "Загрузки", "selected": ["report.pdf", "notes.txt", "img"],
       "selected_count": 3, "selected_dirs": 1, "file": "", "file_name": "", "doc": None, "writable": True, "approx": False,
       "reason": "", "pending": False, "color": {"hex": "#3366CC", "r": 51, "g": 102, "b": 204, "x": 840, "y": 412}}
RENAME = {"id": "cs1", "ctx": "a1x", "folder_name": "Загрузки", "summary": "Переименовать по дате", "status": "pending", "risky": 0, "undo": "",
          "ops": [{"n": i, "op": "move", "path": a, "to": b, "status": "pending"} for i, (a, b) in enumerate([
              ("IMG_4471.jpg", "2026-09-14 Море.jpg"), ("IMG_4472.jpg", "2026-09-14 Закат.jpg"),
              ("scan 3.pdf", "2026-10-01 Договор аренды.pdf"), ("Новый документ.docx", "2026-10-05 План поездки.docx")])]}
EDIT = {"id": "cs2", "ctx": "a1x", "folder_name": "Загрузки", "summary": "Исправить опечатки", "status": "pending", "risky": 1, "undo": "",
        "ops": [{"n": 0, "op": "edit", "path": "notes.txt", "status": "pending",
                 "diff": "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,3 +1,3 @@\n Список дел\n-- купить малоко\n+- купить молоко\n - позвонить маме"},
                {"n": 1, "op": "write", "path": "README.md", "status": "pending", "existed": False, "preview": "# Загрузки\n\nЗдесь лежат документы и фото."},
                {"n": 2, "op": "delete", "path": "old/tmp.log", "status": "pending", "risky": "delete"}]}

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC)))
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    state = {"lang": "ru", "theme": "dark", "design": "classic", "set": RENAME}

    async def route(route_obj, request):
        url = request.url
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": {"theme": state["theme"], "language": state["lang"], "ui_design": state["design"],
                                                           "accent_color": "violet", "quick_ask_model": "qwen3-vl-plus"},
                                                 "status": {"native": True, "hotkey": {"ok": True}}, "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": [{"id": "qwen3-vl-plus", "caps": {"vision": True}}]})
        if path == "/api/quick/apps/context":
            ctx = dict(CTX, folder_name="Downloads" if state["lang"] == "en" else "Загрузки")
            return await route_obj.fulfill(json={"context": ctx, "native": True, "settings": {"access": "ask"},
                                                 "presets": [{"id": "p_1", "name": "Reply to the email" if state["lang"] == "en" else "Ответ на письмо", "prompt": "x", "apps": []}]})
        if path == "/api/quick/apps/settings":
            return await route_obj.fulfill(json={"settings": {}, "presets": [], "native": True, "history": []})
        if path == "/api/quick/extras/rates":
            return await route_obj.fulfill(json={"base": "USD", "rates": {"USD": 1, "RUB": 92.4567, "EUR": 0.91}, "source": "open.er-api.com", "t": 1791570000})
        if path == "/api/quick/explorer/duplicates":
            return await route_obj.fulfill(json={"groups": [{"size": 2400000, "files": ["IMG_4471.jpg", "Копия IMG_4471.jpg"]},
                                                            {"size": 81234, "files": ["report.pdf", "old/report (1).pdf", "old/report (2).pdf"]}],
                                                 "files_scanned": 214, "wasted": 2500000, "wasted_h": "2,4 МБ", "wasted_h_en": "2.4 MB"})
        if path == "/api/quick/ask":
            events = [{"event": "tool_ui", "tool": "folder_change", "ui": {"type": "changeset", "set": state["set"]}},
                      {"event": "delta", "text": "Предложил изменения — проверьте и нажмите «Применить»." if state["lang"] == "ru" else "I proposed the changes — review and press “Apply”."},
                      {"event": "done", "model": "qwen3-vl-plus", "tier": "fast"}]
            body = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events)
            return await route_obj.fulfill(status=200, content_type="text/event-stream", body=body)
        if path.startswith("/api/assistant/state"):
            return await route_obj.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME or None, args=["--no-sandbox"])

        async def open_page():
            page = await browser.new_page(viewport={"width": 720, "height": 700})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_function("!!window.QuickExplorer && !!window.QuickExtras", timeout=10000)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(600)
            return page

        async def shot(page, name):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": 720, "height": max(80, min(900, int(height)))})
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(out / f"{name}.png"))

        for lang, theme, design in (("ru", "dark", "classic"), ("en", "light", "classic"), ("ru", "light", "glass"), ("en", "dark", "terminal")):
            state.update(lang=lang, theme=theme, design=design, set=RENAME)
            sfx = f"{lang}-{theme}-{design}"
            page = await open_page()
            await shot(page, f"chip-{sfx}")
            await page.fill("#qa-input", "переименуй по дате" if lang == "ru" else "rename by date")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)
            await shot(page, f"rename-{sfx}")
            if design == "classic":
                state["set"] = EDIT
                await page.fill("#qa-input", "исправь опечатки" if lang == "ru" else "fix typos")
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(700)
                await page.evaluate("document.querySelectorAll('#qa-slot-bottom .qe-card')[0] && (document.querySelectorAll('#qa-slot-bottom .qe-card')[0].style.display='none')")
                await page.evaluate("document.querySelectorAll('#qa-slot-bottom details').forEach(d => d.open = true)")
                await shot(page, f"edit-{sfx}")
                await page.evaluate("MemuQuick.newChat()")
                await page.evaluate("QuickExplorer.openView('dups')")
                await page.wait_for_timeout(400)
                await shot(page, f"dups-{sfx}")
                await page.keyboard.press("Escape")
                await page.evaluate("QuickExplorer.openView('ask')")
                await page.evaluate("QuickExtras.openView('color')")
                await page.wait_for_timeout(300)
                await shot(page, f"color-{sfx}")
                await page.keyboard.press("Escape")
                await page.keyboard.press("Escape")
                await page.fill("#qa-input", "100 usd в rub")
                await page.dispatch_event("#qa-input", "input")
                await page.wait_for_timeout(400)
                await shot(page, f"currency-{sfx}")
            await page.close()
        await browser.close()
    server.shutdown()
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r29-quick-explorer-shots"))))
