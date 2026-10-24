"""Round 27 (r27-hunt-quicknew): headless checks of how the new Quick Ask features work TOGETHER, against a real
in-process playground server (real /api/quick/config, /api/quick/chats, /api/quick/features/*; only the model call
/api/quick/ask and the native /api/quick/window are mocked).

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_hunt_quick_shots.py OUT_DIR

Checks (exit code 1 on any problem; tests/round27_hunt_quick_test.py runs it):
  * a DOCX (MIME «…officedocument.wordprocessingml…» — «xml» in it) goes to the document parser, not read as text;
  * a document (DOCX via the file dialog) or a dropped .txt file is a «Файл» chip, not «Выделено»: the placeholder
    speaks of the file, the selection action row is there and «Кратко» sends the file text as the selection;
  * a document attached on top of a captured selection: the action still runs, but «Заменить выделение» is not
    offered (the answer is about more than the selection);
  * an answer started while the clipboard history view is open (wake word, a command) closes the view — it used to
    stream hidden under it;
  * an AI snippet's instruction is kept in the saved Quick Ask chat (quick_chats.json) and comes back on reopen.
Screens: OUT/hq-*.png.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")
DOC_TEXT = "Договор поставки № 17. Срок оплаты — 30 дней с даты отгрузки. Штраф за просрочку 0,1 % в день."
SEL_TEXT = "Thanks for the update! We should move the release to Thursday."


def tiny_docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


def start_server(data_dir: str) -> str:
    os.environ["MEMUAI_DATA_DIR"] = data_dir
    os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
    import uvicorn
    from app.playground_api import create_app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    import urllib.request
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/quick/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    return f"http://127.0.0.1:{port}"


async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    base = start_server(tempfile.mkdtemp(prefix="r27hq-data-"))
    problems: list = []
    asks: list = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async def route(route_obj, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/api/quick/ask":
            body = json.loads(request.post_data or "{}")
            asks.append(body)
            await asyncio.sleep(0.15)
            events = [{"event": "delta", "text": "Кратко: оплата 30 дней, штраф 0,1 % в день."},
                      {"event": "done", "model": "test-model", "tier": "fast"}]
            return await route_obj.fulfill(status=200, content_type="text/event-stream",
                                           body="".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events))
        if path == "/api/quick/window":
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/pg/state":         # no Proxy here: one model to answer with
            return await route_obj.fulfill(json={"models": [{"id": "test-model", "caps": {"vision": False, "search": False}}], "image_models": []})
        if path == "/api/quick/config":     # the real config, with that model chosen
            response = await route_obj.fetch()
            data = await response.json()
            data.setdefault("prefs", {})["quick_ask_model"] = "test-model"
            data.setdefault("ui", {})["onboarded"] = True
            return await route_obj.fulfill(response=response, json=data)
        return await route_obj.continue_()

    def show(selection: str = "", info=None) -> str:
        return json.dumps({"fresh": True, "maxHeight": 700, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False,
                           "source": "hotkey", "page": None, "selection": selection, "selection_info": info}, ensure_ascii=False)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def until(page, expression, seconds=8.0):
            # the page's CSP forbids eval: no wait_for_function, poll with evaluate
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if await page.evaluate(expression):
                    return True
                await page.wait_for_timeout(100)
            check(False, f"timed out waiting for {expression}")
            return False

        async def open_page(selection: str = "", info=None, width: int = 720):
            page = await browser.new_page(viewport={"width": width, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/api/quick/ask", route)
            await page.route("**/api/quick/window", route)
            await page.route("**/api/pg/state", route)
            await page.route("**/api/quick/config", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await until(page, "!!(window.QuickFeatures && window.QuickSelection)")
            await page.evaluate("QuickAsk.onShow(" + show(selection, info) + ")")
            await page.wait_for_timeout(400)
            return page

        async def shot(page, name):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": 720, "height": max(80, min(900, int(height)))})
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / f"{name}.png"))

        # 1. a DOCX through the file dialog
        page = await open_page()
        await page.set_input_files("#qa-file-input", files=[{"name": "dogovor.docx", "buffer": tiny_docx(DOC_TEXT),
                                                             "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}])
        await until(page, "!!document.querySelector('.qa-chip-sel')")
        chip = await page.inner_text(".qa-chip-sel b")
        check(chip == "Файл", f"document chip says «Файл» ({chip!r})")
        # Round 30: a document chip shows the file name; the parsed text is in its tooltip
        name = await page.inner_text(".qa-chip-sel .tl")
        check(name == "dogovor.docx", f"the document chip names the file ({name!r})")
        quote = await page.get_attribute(".qa-chip-sel", "title")
        check("Договор поставки" in quote and "Content_Types" not in quote,
              f"the DOCX went to the parser, not read as text (its MIME type has «xml» in it) ({quote[:80]!r})")
        placeholder = await page.get_attribute("#qa-input", "placeholder")
        check(placeholder == "Что сделать с файлом?", f"placeholder speaks of the file ({placeholder!r})")
        labels = await page.eval_on_selector_all(".qsel-act .tx", "els => els.map(e => e.textContent)")
        check("Кратко" in labels, f"the action row is there for the document ({labels})")
        await shot(page, "hq-document-ru")
        asks.clear()
        await page.click(".qsel-act:has-text('Кратко')")
        await page.wait_for_timeout(700)
        ask = asks[-1] if asks else {}
        check((ask.get("selection_action") or {}).get("id") == "summarize" and DOC_TEXT in (ask.get("selection") or ""),
              f"«Кратко» sends the document as the selection ({ask.get('selection_action')}, {str(ask.get('selection'))[:60]!r})")
        await page.close()

        # 2. a dropped .txt (the old text-file path) — the row and the placeholder used to stay as if nothing was there
        page = await open_page()
        await page.set_input_files("#qa-file-input", files=[{"name": "notes.txt", "buffer": DOC_TEXT.encode("utf-8"), "mimeType": "text/plain"}])
        await page.wait_for_timeout(500)
        placeholder = await page.get_attribute("#qa-input", "placeholder")
        check(placeholder == "Что сделать с файлом?", f".txt: placeholder updated ({placeholder!r})")
        visible = await page.evaluate("!!document.querySelector('.qa-plugin.qsel') && !document.querySelector('.qa-plugin.qsel').hidden")
        check(visible, ".txt: the action row is shown (it was hidden together with the old rows)")
        await page.close()

        # 3. a selection with a document on top: no «Заменить выделение»
        info = {"id": "sel42", "source": "uia", "app": "OUTLOOK.EXE", "title": "Inbox", "editable": True, "truncated": False, "chars": len(SEL_TEXT)}
        page = await open_page(SEL_TEXT, info)
        check((await page.inner_text(".qa-chip-sel b")) == "Выделено", "a captured selection is still «Выделено»")
        await page.evaluate(f"MemuQuick.addContext({json.dumps(DOC_TEXT)}, {{name: 'dogovor.docx'}})")
        await page.wait_for_timeout(200)
        asks.clear()
        await page.click(".qsel-act:has-text('Исправить')")
        await page.wait_for_timeout(800)
        spec = (asks[-1] if asks else {}).get("selection_action") or {}
        check(spec.get("id") == "fix" and spec.get("replace") is False, f"selection + document: no replace ({spec})")
        check(await page.locator("#qa-x-selection-replace").count() == 0, "«Заменить выделение» is not offered")
        await page.close()

        # 4. an answer that starts while the clipboard view is open closes the view
        page = await open_page()
        await page.keyboard.press("Alt+V")
        await page.wait_for_timeout(400)
        check(await page.evaluate("document.documentElement.dataset.qxView === 'clipboard'"), "Alt+V opens the clipboard history")
        await page.evaluate("MemuQuick.send('какая сегодня погода')")       # what the wake word plugin does
        await page.wait_for_timeout(800)
        check(await page.evaluate("!document.documentElement.dataset.qxView"), "the view gives way to the answer")
        answer = await page.evaluate("!document.getElementById('qa-answer').hidden && document.getElementById('qa-answer').innerText")
        check(bool(answer) and "30 дней" in answer, f"the answer is visible ({str(answer)[:60]!r})")
        await shot(page, "hq-answer-after-view-ru")
        await page.close()

        # 5. an AI snippet: the instruction survives a reopen of the chat
        page = await open_page()
        await page.fill("#qa-input", ";короче " + DOC_TEXT)
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1200)
        prompt = (asks[-1] if asks else {}).get("text") or ""
        chat_id = await page.evaluate("MemuQuick.state().chatId")
        import urllib.request
        stored = json.loads(urllib.request.urlopen(f"{base}/api/quick/chats/{chat_id}").read())
        turn = (stored.get("chat") or {}).get("turns", [{}])[0]
        check(turn.get("q") == "Короче и проще" and "Сократи" in (turn.get("prompt") or ""),
              f"the snippet's instruction is stored with the chat ({turn.get('q')!r}, {str(turn.get('prompt'))[:30]!r}, sent {prompt[:30]!r})")
        await page.evaluate("MemuQuick.newChat ? MemuQuick.newChat() : null")
        await page.close()
        await browser.close()
    for line in problems:
        print("PROBLEM:", line)
    print(f"shots in {out}; problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
