"""Round 27 (r27-quick-features): headless Chromium checks + screenshots of the new Quick Ask features.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_quick_features_shots.py OUT_DIR

The Quick Ask page runs against a mock of /api/quick/* and /api/quick/features/* (the snippet logic is the real
app.quick_features code). Checks (exit code 1 on any problem, used by tests/round27_quick_features_test.py):
  * Ctrl+K lists the new actions (region, clipboard, snippets, stickers, document) in ru and en;
  * clipboard history: the «off» empty state and its «Turn on», the list, Enter pastes (native), Tab attaches the
    text to the question, Ctrl+P pins, Esc closes the view;
  * ;snippets: the inline list, Enter on a text snippet → /snippets/use {insert: true}; an AI snippet with text after
    the keyword → the question is the template and the text is the attached context;
  * snippet editor: validation, save, the new snippet in the list;
  * region: the action strip after a capture, Alt+2 «Copy text» asks with the image and copies the answer;
  * «Sticker» under an answer → POST …/stickers {create} with the answer; the stickers view; the sticker page;
  * a PDF picked in the file dialog → /document → «[File: …]» context chip;
  * the region overlay page: a drag sends fractions of the viewport, Esc sends null;
  * the settings sub-page renders with the settings kit (ru/en).
Screens: OUT/*.png (ru dark / en light / glass and terminal designs).
"""
from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import io
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
MODELS = [{"id": "qwen3-vl-plus", "caps": {"vision": True, "search": False}}]
ASKS: list = []
ANSWER = {"text": "Счёт № 4471\nИтого: 12 300 ₽"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/quick/ask"):
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        ASKS.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in ({"event": "delta", "text": ANSWER["text"]}, {"event": "done", "model": "qwen3-vl-plus", "tier": "fast"}):
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()


def demo_png(width=900, height=520) -> bytes:
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (width, height), (238, 241, 246))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 46), fill=(52, 58, 72))
    for i, y in enumerate(range(80, height - 40, 54)):
        draw.rounded_rectangle((40, y, width - 40, y + 38), radius=8, fill=(255, 255, 255), outline=(210, 214, 222))
        draw.rectangle((56, y + 12, 56 + 120 + (i * 37) % 260, y + 24), fill=(120, 128, 145))
    return buf(image)


def buf(image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def tiny_pdf() -> bytes:
    # a minimal valid one-page PDF with a text layer (pypdf can read it)
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream = b"BT /F1 18 Tf 20 80 Td (Contract deadline: 1 March) Tj ET"
    objects[3] = "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream.decode())
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    from app import quick_ask_core as core
    from app import quick_features as qf
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    calls: list = []
    now = int(time.time() * 1000)
    st = {"prefs": {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru", "quick_ask_model": "qwen3-vl-plus"},
          "chats": [], "settings": dict(qf.DEFAULT_SETTINGS), "snippets": qf.default_snippets("ru"), "stickers": [],
          "clip": [{"id": "c1", "text": "https://github.com/memuai/memuai/pull/412", "t": now - 60000, "app": "chrome.exe", "pinned": True, "chars": 41},
                   {"id": "c2", "text": "Встреча перенесена на четверг, 15:30 — переговорная «Север»", "t": now - 300000, "app": "telegram.exe", "pinned": False, "chars": 58},
                   {"id": "c3", "text": "SELECT id, total FROM orders WHERE status = 'paid' ORDER BY created DESC LIMIT 20;", "t": now - 4000000, "app": "code.exe", "pinned": False, "chars": 52000}]}
    pdf = tiny_pdf()

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async def route(route_obj, request):
        url = request.url
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        query = url.split("?", 1)[1] if "?" in url else ""
        try:   # Round 35: some POSTs carry raw bytes (a big document goes to /ingest as the file itself)
            body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": st["prefs"], "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                                 "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": MODELS, "image_models": []})
        if path == "/api/quick/ask":
            return await route_obj.continue_()
        if path == "/api/quick/chats" and request.method == "POST":
            st["chats"] = core.upsert_chat(st["chats"], body.get("chat"))
            return await route_obj.fulfill(json={"ok": True})
        if path.startswith("/api/quick/features") or path == "/api/quick/window":
            calls.append((path, body))
        if path == "/api/quick/window":
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/quick/features":
            return await route_obj.fulfill(json={"settings": st["settings"], "snippets": st["snippets"], "native": True, "status": {"hotkeys": {}},
                                                 "stickers": st["stickers"], "clipboard_count": len(st["clip"])})
        if path == "/api/quick/features/settings":
            st["settings"] = qf.normalize_settings({**st["settings"], **body.get("settings", {})})
            return await route_obj.fulfill(json={"ok": True, "settings": st["settings"], "status": {}})
        if path == "/api/quick/features/clipboard" and request.method == "GET":
            q = ""
            for part in query.split("&"):
                if part.startswith("q="):
                    from urllib.parse import unquote
                    q = unquote(part[2:]).lower()
            items = [c for c in st["clip"] if q in c["text"].lower()] if st["settings"]["clipboard"] else []
            items.sort(key=lambda r: (not r["pinned"], -r["t"]))
            return await route_obj.fulfill(json={"items": items, "total": len(items), "native": True, "status": {},
                                                 "settings": {k: st["settings"][k] for k in ("clipboard", "clipboard_paused", "clipboard_limit")}})
        if path == "/api/quick/features/clipboard":
            item = next((c for c in st["clip"] if c["id"] == body.get("id")), None)
            if body.get("action") in ("pin", "unpin") and item:
                item["pinned"] = body["action"] == "pin"
            return await route_obj.fulfill(json={"ok": True, "native": True})
        if path == "/api/quick/features/snippets/use":
            row = next(s for s in st["snippets"] if s["id"] == body.get("id"))
            if row["kind"] == "ai":
                return await route_obj.fulfill(json={"ok": True, "kind": "ai", "clipboard": ""})
            result = qf.expand(row["text"], input_text=body.get("input", ""), lang=body.get("lang", "ru"))
            result.update(ok=True, kind="text", inserted=bool(body.get("insert")))
            return await route_obj.fulfill(json=result)
        if path == "/api/quick/features/snippets":
            st["snippets"] = qf.normalize_snippets(body.get("snippets"))
            return await route_obj.fulfill(json={"ok": True, "snippets": st["snippets"]})
        if path == "/api/quick/features/stickers" and request.method == "GET":
            return await route_obj.fulfill(json={"items": st["stickers"]})
        if path.startswith("/api/quick/features/stickers/"):
            sid = path.rsplit("/", 1)[1]
            row = next((s for s in st["stickers"] if s["id"] == sid), None)
            return await route_obj.fulfill(json={"sticker": row} if row else {"error": "nf"}, status=200 if row else 404)
        if path == "/api/quick/features/stickers":
            if body.get("action") == "create":
                row = qf.normalize_sticker({"text": body["text"], "title": body.get("title") or qf.sticker_title(body["text"]), "open": True})
                st["stickers"].insert(0, row)
                return await route_obj.fulfill(json={"ok": True, "sticker": row, "native": True, "shown": True})
            row = next((s for s in st["stickers"] if s["id"] == body.get("id")), None)
            if row and body.get("action") in ("open", "close"):
                row["open"] = body["action"] == "open"
            if row and body.get("action") == "delete":
                st["stickers"].remove(row)
            if row and body.get("action") == "update" and body.get("color") is not None:
                row["color"] = body["color"]
            return await route_obj.fulfill(json={"ok": True, "sticker": row})
        if path == "/api/quick/features/document":
            raw = base64.b64decode(body["data"].split(",", 1)[1])
            result = qf.document_text(body["name"], body["data"], body.get("lang", "ru"))
            check(raw[:5] == b"%PDF-", "the PDF reaches /document intact")
            return await route_obj.fulfill(json=result, status=415 if result.get("error") else 200)
        if path == "/api/quick/features/region/image":
            return await route_obj.fulfill(status=200, content_type="image/png", body=demo_png(1280, 720))
        if path == "/api/quick/features/region/done":
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0, "card": None})
        if path.startswith("/api/focus/"):
            return await route_obj.fulfill(json={"ok": True})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def open_page(theme="dark", lang="ru", design="classic"):
            st["prefs"].update(theme=theme, language=lang, ui_design=design)
            page = await browser.new_page(viewport={"width": 720, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.add_init_script("navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };")
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_function("!!window.QuickFeatures", timeout=8000)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(500)
            return page

        async def shot(page, name, width=720):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(60, min(900, int(height)))})
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(out / f"{name}.png"))

        def latest(path):
            return next((b for p, b in reversed(calls) if p == path), None)

        for theme, lang, design in (("dark", "ru", "classic"), ("light", "en", "classic")):
            en = lang == "en"
            sfx = f"{theme}-{lang}"
            st["settings"] = dict(qf.DEFAULT_SETTINGS)
            st["snippets"] = qf.default_snippets(lang)
            st["stickers"] = []
            for c in st["clip"]:
                c["pinned"] = c["id"] == "c1"
            page = await open_page(theme, lang, design)

            # Ctrl+K
            await page.keyboard.press("Control+KeyK")
            await page.wait_for_timeout(250)
            menu = await page.inner_text("#qa-menu")
            for word in (("Ask about a screen region", "Clipboard history", "Snippets", "Stickers", "PDF, DOCX") if en else
                         ("Спросить про область экрана", "История буфера обмена", "Сниппеты", "Стикеры", "PDF, DOCX")):
                check(word in menu, f"{sfx}: Ctrl+K lists «{word}»")
            await shot(page, f"menu-{sfx}")
            await page.keyboard.press("Escape")

            # clipboard: off → turn on → list
            await page.keyboard.press("Alt+KeyV")
            await page.wait_for_timeout(350)
            text = await page.inner_text("#qa-slot-top")
            check(("Clipboard history is off" if en else "История буфера обмена выключена") in text, f"{sfx}: the clipboard «off» state is explained")
            check(await page.locator("#qa-suggest").is_hidden(), f"{sfx}: the view hides the idle list")
            await shot(page, f"clipboard-off-{sfx}")
            await page.click("[data-qx='enable-clip']")
            await page.wait_for_timeout(400)
            check(st["settings"]["clipboard"] is True, f"{sfx}: «Turn on» saves clipboard: true")
            rows = await page.locator(".qx-row").count()
            check(rows == 3, f"{sfx}: the clipboard list shows 3 items ({rows})")
            meta = await page.locator(".qx-row").nth(2).inner_text()
            check(("start: 82 of 52,000 chars" if en else "начало: 82 из 52\u00a0000 зн.") in meta,
                  f"{sfx}: a long copy says it is kept by its beginning ({meta!r})")   # r27-quick-polish
            await page.keyboard.press("ArrowDown")
            await shot(page, f"clipboard-list-{sfx}")
            await page.keyboard.press("Control+KeyP")
            await page.wait_for_timeout(300)
            check(any(c["id"] == "c2" and c["pinned"] for c in st["clip"]), f"{sfx}: Ctrl+P pins the selected item")
            await page.fill("#qx-q", "select")
            await page.wait_for_timeout(400)
            check(await page.locator(".qx-row").count() == 1, f"{sfx}: the search filters the clipboard")
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(300)
            sel = await page.evaluate("QuickAsk.state().selection")
            check("SELECT id, total" in sel, f"{sfx}: Tab attaches the item to the question ({sel[:40]!r})")
            check(await page.evaluate("!document.documentElement.dataset.qxView"), f"{sfx}: the view is closed after Tab")
            await shot(page, f"clipboard-asked-{sfx}")
            await page.evaluate("QuickAsk.state().selection = ''; QuickAsk.render()")
            await page.keyboard.press("Alt+KeyV")
            await page.wait_for_timeout(400)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            paste = latest("/api/quick/features/clipboard")
            check(bool(paste) and paste.get("action") == "paste", f"{sfx}: Enter pastes the item ({paste})")
            check(await page.evaluate("!document.documentElement.dataset.qxView"), f"{sfx}: the view closes after a paste")
            await page.keyboard.press("Alt+KeyV")
            await page.wait_for_timeout(300)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            check(await page.evaluate("!document.documentElement.dataset.qxView"), f"{sfx}: Esc closes the clipboard view")
            check(await page.evaluate("QuickAsk.state().turns.length") == 0 and await page.locator("#qa-card").is_visible(), f"{sfx}: Esc did not close the window")

            # ;snippets inline
            await page.fill("#qa-input", ";")
            await page.dispatch_event("#qa-input", "input")
            await page.wait_for_timeout(250)
            inline = await page.locator(".qx-inline .qa-row").count()
            check(inline >= 5, f"{sfx}: «;» lists the snippets ({inline})")
            await shot(page, f"snippets-inline-{sfx}")
            await page.fill("#qa-input", ";date")
            await page.dispatch_event("#qa-input", "input")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            use = latest("/api/quick/features/snippets/use")
            check(bool(use) and use.get("id") == "s0" and use.get("insert") is True, f"{sfx}: Enter on ;date pastes it ({use})")
            check(not ASKS, f"{sfx}: a text snippet never asks the model")
            check(await page.input_value("#qa-input") == "", f"{sfx}: the input is cleared after the snippet")
            kw = "no" if en else "отказ"
            await page.fill("#qa-input", f";{kw} Can you join the call at 9 tonight?")
            await page.dispatch_event("#qa-input", "input")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(900)
            check(len(ASKS) == 1, f"{sfx}: the AI snippet asks the model once ({len(ASKS)})")
            if ASKS:
                ask = ASKS[-1]
                check("join the call" in ask.get("selection", ""), f"{sfx}: the text after the keyword is the context ({ask.get('selection', '')[:60]!r})")
                check(ask.get("text") == next(s["text"] for s in st["snippets"] if s["keyword"] == kw), f"{sfx}: the model gets the template")
                shown = await page.inner_text("#qa-answer .qa-q")
                check(shown.strip() == next(s["name"] for s in st["snippets"] if s["keyword"] == kw), f"{sfx}: the chat shows the snippet name ({shown!r})")
            await shot(page, f"snippet-ai-answer-{sfx}")
            # the Sticker button under the answer
            # Round 30: plugin answer actions live in «⋯ Ещё» under the answer
            if await page.locator("#qa-act-more").count():
                await page.click("#qa-act-more")
                await page.wait_for_timeout(150)
            sticker_btn = page.locator("#qa-x-sticker")
            check(await sticker_btn.count() == 1, f"{sfx}: the «Sticker» button is under the answer")
            if await sticker_btn.count():
                await sticker_btn.click()
                await page.wait_for_timeout(300)
                created = latest("/api/quick/features/stickers")
                check(bool(created) and created.get("action") == "create" and "12 300" in created.get("text", ""), f"{sfx}: the sticker is created from the answer")
            ASKS.clear()

            # snippet manager + editor
            await page.keyboard.press("Control+KeyN")
            await page.wait_for_timeout(200)
            await page.evaluate("QuickFeatures.open('snippets')")
            await page.wait_for_timeout(250)
            await shot(page, f"snippets-view-{sfx}")
            await page.keyboard.press("Control+KeyN")
            await page.wait_for_timeout(250)
            check(await page.locator("#qx-form").count() == 1, f"{sfx}: Ctrl+N opens the snippet editor")
            await page.keyboard.press("Control+Enter")
            await page.wait_for_timeout(200)
            err = await page.inner_text("#qx-err")
            check(bool(err.strip()), f"{sfx}: an empty snippet is refused with a message ({err!r})")
            await page.fill("#qx-name", "Подпись" if not en else "Signature")
            await page.fill("#qx-kw", "sig")
            await page.fill("#qx-text", "—\n{cursor}\nMemuAI")
            await page.click(".qx-var[data-var='{date}']")
            await shot(page, f"snippet-editor-{sfx}")
            await page.keyboard.press("Control+Enter")
            await page.wait_for_timeout(350)
            check(any(s["keyword"] == "sig" for s in st["snippets"]), f"{sfx}: the new snippet is saved")
            check(await page.locator(".qx-row", has_text=";sig").count() == 1, f"{sfx}: the new snippet is in the list")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)

            # stickers view
            await page.evaluate("QuickFeatures.open('stickers')")
            await page.wait_for_timeout(350)
            check(await page.locator(".qx-row").count() == 1, f"{sfx}: the stickers view lists the sticker")
            await shot(page, f"stickers-view-{sfx}")
            await page.keyboard.press("Escape")

            # region strip
            png = "data:image/png;base64," + base64.b64encode(demo_png(640, 300)).decode()
            await page.evaluate(f"QuickFeatures.attachRegion({{data: {json.dumps(png)}, width: 640, height: 300, label: '640×300'}})")
            await page.wait_for_timeout(300)
            check(await page.locator(".qx-strip").count() == 1, f"{sfx}: the region strip is shown")
            check(await page.evaluate("QuickAsk.state().attachments.length") == 1, f"{sfx}: the region is attached")
            await shot(page, f"region-strip-{sfx}")
            await page.keyboard.press("Alt+Digit2")
            await page.wait_for_timeout(900)
            check(len(ASKS) == 1 and len(ASKS[-1].get("images", [])) == 1, f"{sfx}: «Copy text» asks with the image")
            copied = await page.evaluate("window.__copied || ''")
            check("4471" in copied, f"{sfx}: the extracted text is copied ({copied!r})")
            check(await page.locator(".qx-strip").count() == 0, f"{sfx}: the strip is gone after the question")
            await shot(page, f"region-answer-{sfx}")
            ASKS.clear()

            # a PDF from the file dialog
            await page.keyboard.press("Control+KeyN")
            await page.set_input_files("#qa-file-input", files=[{"name": "contract.pdf", "mimeType": "application/pdf", "buffer": pdf}])
            sel = ""
            for _ in range(40):                       # the first pypdf import can take a moment
                await page.wait_for_timeout(150)
                sel = await page.evaluate("QuickAsk.state().selection")
                if sel:
                    break
            check(sel.startswith("[File: contract.pdf]" if en else "[Файл: contract.pdf]") and "deadline" in sel, f"{sfx}: the PDF text is attached ({sel[:60]!r})")
            await shot(page, f"document-{sfx}")
            await page.close()

        # designs: glass dark ru / terminal light ru (views must keep their look)
        for design, theme in (("glass", "dark"), ("terminal", "light")):
            page = await open_page(theme, "ru", design)
            await page.keyboard.press("Alt+KeyV")
            await page.wait_for_timeout(400)
            await page.keyboard.press("ArrowDown")
            await shot(page, f"clipboard-{design}-{theme}")
            await page.keyboard.press("Escape")
            await page.fill("#qa-input", ";")
            await page.dispatch_event("#qa-input", "input")
            await page.wait_for_timeout(200)
            await shot(page, f"snippets-inline-{design}-{theme}")
            await page.close()

        # the sticker page (as a browser page; the native window is the same page)
        for theme, lang in (("dark", "ru"), ("light", "en")):
            st["prefs"].update(theme=theme, language=lang)
            st["stickers"] = [qf.normalize_sticker({"id": "k1", "title": "Как вернуть заказ", "color": "yellow",
                                                    "text": "1. Откройте **Мои заказы**\n2. Нажмите «Вернуть»\n3. Курьер заберёт посылку завтра с `10:00` до `14:00`"})]
            page = await browser.new_page(viewport={"width": 400, "height": 300})
            page.on("pageerror", lambda e: problems.append(f"sticker pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/sticker.html?id=k1", wait_until="load")
            await page.wait_for_timeout(600)
            body = await page.inner_text("#st-body")
            check("Мои заказы" in body, f"sticker {theme}: the text is rendered")
            check(await page.evaluate("document.documentElement.dataset.color") == "yellow", f"sticker {theme}: the colour is applied")
            title = await page.get_attribute("#st-close", "title")
            check(("Close the sticker" if lang == "en" else "Закрыть стикер") in (title or ""), f"sticker {theme}: the labels follow the language ({title})")
            await page.click("#st-color")
            await page.wait_for_timeout(150)
            await page.screenshot(path=str(out / f"sticker-{theme}-{lang}.png"))
            await page.click("#st-colors [data-color='blue']")
            await page.wait_for_timeout(250)
            check(st["stickers"][0]["color"] == "blue", "sticker: picking a colour saves it")
            await page.dblclick("#st-body")
            check(await page.locator("#st-edit").is_visible(), "sticker: double click edits")
            await page.close()

        # the region overlay page
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        page.on("pageerror", lambda e: problems.append(f"region pageerror: {e}"))
        await page.route("**/*", route)
        await page.goto(base + "/quick/region.html?native=1&token=t1&lang=ru", wait_until="load")
        await page.wait_for_timeout(400)
        await page.mouse.move(300, 200)
        await page.mouse.down()
        await page.mouse.move(700, 450, steps=6)
        await page.wait_for_timeout(100)
        await page.screenshot(path=str(out / "region-overlay-dragging.png"))
        await page.mouse.up()
        await page.wait_for_timeout(200)
        done = latest("/api/quick/features/region/done")
        rect = (done or {}).get("rect") or {}
        check(abs(rect.get("x", 0) - 300 / 1280) < 0.002 and abs(rect.get("w", 0) - 400 / 1280) < 0.002 and abs(rect.get("h", 0) - 250 / 720) < 0.003,
              f"region: the drag is sent as viewport fractions ({rect})")
        await page.evaluate("RegionPick.start({token: 't2', lang: 'en'})")
        await page.wait_for_timeout(300)
        check("Drag to select" in await page.inner_text("#rg-hint"), "region: the hint follows the language")
        await page.screenshot(path=str(out / "region-overlay-idle-en.png"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        done = latest("/api/quick/features/region/done")
        check(done and done.get("token") == "t2" and done.get("rect") is None, f"region: Esc cancels ({done})")
        await page.close()

        # the settings sub-page (settings kit + our script, mocked api)
        for theme, lang in (("dark", "ru"), ("light", "en")):
            st["settings"] = dict(qf.DEFAULT_SETTINGS, clipboard=True)
            page = await browser.new_page(viewport={"width": 860, "height": 900})
            page.on("pageerror", lambda e: problems.append(f"settings pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/region.html", wait_until="load")      # any same-origin page
            await page.set_content(f"""<!DOCTYPE html><html data-theme="{theme}" lang="{lang}"><head>
              <link rel="stylesheet" href="{base}/themes.css"><link rel="stylesheet" href="{base}/style.css"><link rel="stylesheet" href="{base}/settings.css">
              <link rel="stylesheet" href="{base}/quick/plugins/features-settings.css"></head>
              <body style="padding:20px;background:var(--bg)"><div class="settings-body"><div id="host"></div></div>
              <script src="{base}/settings-kit.js"></script>
              <script>window.MemuQuick = {{ settingsSection: (id, render, opts) => {{ window.__section = {{ id, render, opts }}; }} }};</script>
              <script src="{base}/quick/plugins/features-settings.js"></script></body></html>""")
            await page.wait_for_function("!!window.__section", timeout=5000)
            await page.evaluate("""(en) => {
              const api = (path, o = {}) => fetch(path, { method: o.method || 'GET', headers: { 'Content-Type': 'application/json' }, body: o.body ? JSON.stringify(o.body) : undefined }).then((r) => r.json());
              const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
              window.__section.render(document.getElementById('host'), { en, esc, api, toast: (t) => { window.__toast = t; }, pref: () => undefined, savePreferences: async () => ({}) });
            }""", lang == "en")
            await page.wait_for_timeout(600)
            text = await page.inner_text("#host")
            check(("Clipboard history" if lang == "en" else "История буфера обмена") in text and ("Snippets" if lang == "en" else "Сниппеты") in text,
                  f"settings {lang}: the sections are rendered")
            await page.locator("label.switch", has=page.locator("#qx-set-clip-pause")).click()
            await page.wait_for_timeout(300)
            check(st["settings"]["clipboard_paused"] is True, f"settings {lang}: the pause toggle saves")
            await page.screenshot(path=str(out / f"settings-{theme}-{lang}.png"), full_page=True)
            await page.close()
        await browser.close()
    server.shutdown()
    print("problems:", len(problems))
    for item in problems:
        print(" -", item)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r27/r27-quick-features"))))
