"""Round 27 (r27-selection): headless Chromium checks + screenshots of the selection actions in the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_selection_shots.py OUT_DIR

Checks (exit code 1 on any problem; tests/round27_selection_test.py runs it):
  * a window opened with a selection shows the chip (words · characters) and ONE action row (6 buttons, numbers),
    the old selection rows of the idle list are gone; «Перевести на …» follows the language of the text;
  * «3» in the empty field runs «Исправить»: the request carries selection_action {id, replace, selId} and only the
    short label as the question; the answer offers «Заменить выделение» instead of «Вставить»; Alt+Enter and the
    button post replace_selection with the selection id; a refusal copies the answer and says why;
  * ↓ enters the row, ←/→ move, Enter runs, Esc returns to the field; «Спросить» focuses the field and digits type;
  * the user's own actions appear in the configured order; with the feature off the old rows come back;
  * Alt+Space over an open window (QuickAsk.onSelection) shows the row under an existing answer;
  * the row stays inside the card at 520 and 720 px, dark/light, ru/en, soft/terminal designs;
  * r27-quick-polish: a selection read after the window opened (``late``) joins that opening only — the typed text,
    the caret, the focus and the field position stay; dropped after sending / hiding / without a pending read.
Screens: sel-<theme>-<lang>-<width>.png, sel-answer-replace-*.png, sel-ask-*.png, sel-custom-*.png, sel-<design>-*.png, sel-late-*.png.
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
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")
MODELS = [{"id": "deepseek-v4.1-flash", "caps": {"vision": False, "search": False}}]
EN_TEXT = ("Thanks for the update! I think we should move the release to Thursday, because the QA team still "
           "needs two days to finish the regression tests.")
RU_TEXT = "Привет! Посмотрел договор — в пункте 4.2 нужно поменять срок оплаты с 10 на 30 дней, остальное ок."
ASKS: list = []

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/quick/ask"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        ASKS.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        action = (body.get("selection_action") or {}).get("id")
        answer = {"fix": "Thanks for the update! I think we should move the release to Thursday, because the QA team "
                         "still needs two days to finish the regression tests.",
                  "translate": "Спасибо за новости! Думаю, релиз стоит перенести на четверг."}.get(action, "Ответ: " + str(body.get("text")))
        for event in ({"event": "delta", "text": answer}, {"event": "done", "model": "deepseek-v4.1-flash", "tier": "fast"}):
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

def show_payload(text: str, *, fresh: bool = True, app: str = "OUTLOOK.EXE", editable=True) -> str:
    info = {"id": "sel123", "source": "uia", "app": app, "title": "Inbox", "editable": editable, "truncated": False, "chars": len(text)}
    return json.dumps({"fresh": fresh, "maxHeight": 700, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False,
                       "source": "hotkey", "page": None, "selection": text, "selection_info": info}, ensure_ascii=False)

async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    from app import quick_selection
    out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list = []
    windows: list = []
    state = {"prefs": {}, "window": {"ok": False, "code": "gone"}}

    def reset_prefs(**extra):
        prefs = {"theme": "dark", "accent_color": "violet", "ui_design": "classic", "language": "ru",
                 "quick_ask_model": "deepseek-v4.1-flash"}
        prefs.update({k: (list(v) if isinstance(v, list) else v) for k, v in quick_selection.PREF_DEFAULTS.items()})
        prefs.update(extra)
        state["prefs"] = prefs

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
        if path == "/api/quick/window":
            body = json.loads(request.post_data or "{}")
            windows.append(body)
            if body.get("action") == "replace_selection":
                return await route_obj.fulfill(json=state["window"])
            return await route_obj.fulfill(json={"ok": True})
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

        async def open_page(text: str, *, width: int = 720, **prefs):
            reset_prefs(**prefs)
            page = await browser.new_page(viewport={"width": width, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(500)
            await page.evaluate("QuickAsk.onShow(" + show_payload(text) + ")")
            await page.wait_for_timeout(500)
            return page

        async def shot(page, name, width=720):
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(60, min(900, int(height)))})
            await page.wait_for_timeout(350)
            await page.screenshot(path=str(out / f"{name}.png"))

        async def inside(page, what):
            bad = await page.evaluate("""() => {
              const card = document.getElementById('qa-card').getBoundingClientRect();
              return Array.from(document.querySelectorAll('.qsel-act')).filter((b) => { const r = b.getBoundingClientRect();
                return r.right > card.right - 4 || r.left < card.left || r.width < 40; }).map((b) => b.textContent.trim());
            }""")
            check(not bad, f"{what}: action buttons inside the card ({bad})")

        for theme, lang, width in (("dark", "ru", 720), ("light", "ru", 520), ("dark", "en", 520), ("light", "en", 720)):
            page = await open_page(EN_TEXT if lang == "ru" else RU_TEXT, width=width, theme=theme, language=lang)
            labels = await page.eval_on_selector_all(".qsel-act .tx", "els => els.map(e => e.textContent)")
            check(len(labels) == 6, f"{theme}/{lang}: six actions ({labels})")
            check(bool(labels) and labels[0] == ("Перевести на русский" if lang == "ru" else "Translate to English"),
                  f"{theme}/{lang}: translation goes into the interface language ({labels[:1]})")
            chip = await page.inner_text(".qa-chip-sel")
            check(("симв." if lang == "ru" else "chars") in chip, f"{theme}/{lang}: chip shows the length ({chip})")
            check(await page.evaluate("document.getElementById('qa-suggest').hidden"), f"{theme}/{lang}: old selection rows are gone")
            await inside(page, f"{theme}/{lang}/{width}")
            await shot(page, f"sel-{theme}-{lang}-{width}", width)
            await page.close()

        page = await open_page(RU_TEXT)
        first = await page.inner_text(".qsel-act .tx")
        check(first == "Перевести на английский", f"Russian text in the Russian UI → English ({first})")
        await page.close()

        ASKS.clear(); windows.clear()
        page = await open_page(EN_TEXT)
        await page.focus("#qa-input")
        await page.keyboard.press("3")
        await page.wait_for_timeout(900)
        ask = ASKS[-1] if ASKS else {}
        spec = ask.get("selection_action") or {}
        check(spec.get("id") == "fix" and spec.get("replace") is True and spec.get("selId") == "sel123",
              f"«3» sends the fix action with the selection id ({spec})")
        check(ask.get("text") == "Исправить" and ask.get("selection") == EN_TEXT and ask.get("page") is None,
              f"only the label is the question, the selection goes along ({ask.get('text')!r})")
        check(await page.input_value("#qa-input") == "", "the digit was not typed into the field")
        check(await page.locator("#qa-x-selection-replace").count() == 1, "the answer offers «Заменить выделение»")
        check(await page.locator("#qa-insert").count() == 0, "«Вставить» is replaced by «Заменить выделение»")
        check(await page.evaluate("document.querySelector('.qa-plugin.qsel').hidden"), "the row hides once the action ran")
        await shot(page, "sel-answer-replace-dark-ru")
        state["window"] = {"ok": False, "code": "gone"}
        await page.keyboard.press("Alt+Enter")
        await page.wait_for_timeout(400)
        posted = [w for w in windows if w.get("action") == "replace_selection"]
        check(bool(posted) and posted[-1].get("selection_id") == "sel123" and posted[-1].get("text", "").startswith("Thanks"),
              f"Alt+Enter posts replace_selection ({posted[-1:]} )")
        toast = await page.inner_text("#qa-toast")
        check("закрыто" in toast and "Ctrl+V" in toast, f"a refused replace explains and copies ({toast})")
        state["window"] = {"ok": True}
        await page.click("#qa-x-selection-replace")
        await page.wait_for_timeout(300)
        check(len([w for w in windows if w.get("action") == "replace_selection"]) == 2, "the button posts replace too")

        ASKS.clear()
        await page.click("#qa-retry")
        await page.wait_for_timeout(700)
        check(bool(ASKS) and (ASKS[-1].get("selection_action") or {}).get("id") == "fix", "«Ещё раз» repeats the selection action")

        await page.evaluate("QuickAsk.onSelection(" + json.dumps({"text": RU_TEXT, "info": {"id": "sel456", "app": "Telegram.exe", "editable": False}}, ensure_ascii=False) + ")")
        await page.wait_for_timeout(300)
        check(not await page.evaluate("document.querySelector('.qa-plugin.qsel').hidden"), "onSelection shows the row in a chat")
        await shot(page, "sel-onselection-dark-ru")
        ASKS.clear()
        await page.click(".qsel-act:nth-child(1)")
        await page.wait_for_timeout(700)
        spec = (ASKS[-1].get("selection_action") or {}) if ASKS else {}
        check(spec.get("id") == "translate" and spec.get("target") == "en" and spec.get("replace") is False,
              f"a read-only selection is not replaceable; translate target sent ({spec})")
        check(await page.locator("#qa-x-selection-replace").count() == 0 and await page.locator("#qa-insert").count() == 1,
              "no «Заменить» for a read-only selection — «Вставить» stays")
        await page.close()

        ASKS.clear()
        page = await open_page(EN_TEXT, theme="light")
        await page.focus("#qa-input")
        await page.keyboard.press("ArrowDown")
        focused = await page.evaluate("document.activeElement.className")
        check("qsel-act" in focused, f"↓ enters the action row ({focused})")
        await page.keyboard.press("ArrowRight")
        idx = await page.evaluate("document.activeElement.dataset.i")
        check(idx == "1", f"→ moves to the next action ({idx})")
        await shot(page, "sel-keyboard-light-ru")
        await page.keyboard.press("Escape")
        check(await page.evaluate("document.activeElement.id") == "qa-input", "Esc returns to the field (window stays)")
        check(windows[-1:] != [{"action": "hide"}], "Esc in the row does not hide the window")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowRight")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(700)
        check(bool(ASKS) and (ASKS[-1].get("selection_action") or {}).get("id") == "explain", "Enter on «Объяснить» runs it")
        await page.close()

        ASKS.clear()
        page = await open_page(EN_TEXT)
        await page.focus("#qa-input")
        await page.keyboard.press("6")
        await page.wait_for_timeout(200)
        check(await page.evaluate("document.activeElement.id") == "qa-input", "«Спросить» focuses the field")
        await page.keyboard.type("2 варианта короче?")
        check(await page.input_value("#qa-input") == "2 варианта короче?", "after «Спросить» digits are typed")
        await shot(page, "sel-ask-dark-ru")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(700)
        check(bool(ASKS) and not ASKS[-1].get("selection_action") and ASKS[-1].get("selection") == EN_TEXT,
              "a typed question goes with the selection as context")
        await page.close()

        ASKS.clear()
        custom = [{"id": "c_formal01", "name": "Официальнее", "prompt": "Перепиши официально: {text}", "replace": True}]
        page = await open_page(EN_TEXT, quick_ask_selection_custom=custom,
                               quick_ask_selection_actions=["c_formal01", "fix", "translate"])
        labels = await page.eval_on_selector_all(".qsel-act .tx", "els => els.map(e => e.textContent)")
        check(labels == ["Официальнее", "Исправить", "Перевести на русский"], f"own actions in the configured order ({labels})")
        await shot(page, "sel-custom-dark-ru")
        await page.fill("#qa-input", "короче")
        await page.click(".qsel-act:nth-child(1)")
        await page.wait_for_timeout(700)
        spec = (ASKS[-1].get("selection_action") or {}) if ASKS else {}
        check(spec.get("id") == "c_formal01" and spec.get("note") == "короче" and ASKS[-1].get("text") == "Официальнее: короче",
              f"a custom action with a typed wish ({spec}, {ASKS[-1].get('text') if ASKS else ''})")
        await page.close()

        page = await open_page(EN_TEXT, quick_ask_selection=False)
        check(await page.evaluate("document.querySelector('.qa-plugin.qsel').hidden"), "feature off: no action row")
        rows = await page.eval_on_selector_all("#qa-suggest .qa-row .tx", "els => els.map(e => e.textContent)")
        check("Объяснить" in rows, f"feature off: the old rows are back ({rows})")
        await page.close()

        for design in ("soft", "terminal"):
            page = await open_page(EN_TEXT, ui_design=design, width=520)
            await inside(page, design)
            await shot(page, f"sel-{design}-dark-ru-520", 520)
            await page.close()

        late = json.dumps({"text": EN_TEXT, "late": True, "info": {"id": "sel789", "source": "clipboard", "app": "Discord.exe",
                                                                   "editable": None, "chars": len(EN_TEXT)}}, ensure_ascii=False)
        pending = json.loads(show_payload(""))
        pending.update(selection_pending=True, selection_info=None)
        for lang in ("ru", "en"):
            page = await open_page("", language=lang)
            await page.evaluate("QuickAsk.onShow(" + json.dumps(pending) + ")")
            await page.wait_for_timeout(200)
            await page.focus("#qa-input")
            await page.keyboard.type("про это")
            caret = await page.evaluate("document.getElementById('qa-input').selectionStart")
            box = await page.evaluate("document.getElementById('qa-input').getBoundingClientRect().top")
            accepted = await page.evaluate("QuickAsk.onSelection(" + late + ")")
            await page.wait_for_timeout(60)
            await shot(page, f"sel-late-anim-dark-{lang}")
            await page.wait_for_timeout(400)
            check(accepted is True, "a late selection joins the window it was read for")
            check(await page.evaluate("document.getElementById('qa-input').value") == "про это", "the typed text is untouched")
            check(await page.evaluate("document.activeElement.id") == "qa-input", "the focus stays in the field")
            check(await page.evaluate("document.getElementById('qa-input').selectionStart") == caret, "the caret stays")
            check(abs(await page.evaluate("document.getElementById('qa-input').getBoundingClientRect().top") - box) < 1,
                  "the field does not move when the chip arrives")
            check(await page.locator(".qa-chip-sel").count() == 1, "the chip is shown")
            await shot(page, f"sel-late-dark-{lang}")
            check(await page.evaluate("QuickAsk.onSelection(" + late + ")") is False, "a second late delivery is dropped")
            await page.close()

        page = await open_page("")
        await page.evaluate("QuickAsk.onShow(" + json.dumps(pending) + ")")
        await page.wait_for_timeout(200)
        await page.focus("#qa-input")
        await page.keyboard.type("какая погода")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        check(await page.evaluate("QuickAsk.onSelection(" + late + ")") is False, "a late selection after sending is dropped")
        check(await page.locator(".qa-chip-sel").count() == 0, "no chip after sending")

        await page.evaluate("QuickAsk.onShow(" + json.dumps(pending) + ")")
        await page.evaluate("QuickAsk.onHide()")
        check(await page.evaluate("QuickAsk.onSelection(" + late + ")") is False, "a late selection after hiding is dropped")
        await page.evaluate("QuickAsk.onShow(" + show_payload("") + ")")
        check(await page.evaluate("QuickAsk.onSelection(" + late + ")") is False, "no read pending — nothing late to accept")

        await page.evaluate("QuickAsk.selectionPending()")
        check(await page.evaluate("QuickAsk.onSelection(" + late + ")") is True, "toggle: the late selection joins the raised window")
        await page.close()
        await browser.close()
    server.shutdown()
    print("problems:", len(problems))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r27-selection-shots"))))
