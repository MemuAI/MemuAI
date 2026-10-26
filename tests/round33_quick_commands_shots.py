"""Round 33 (r33-quick): headless Chromium checks + screenshots of the command cards in the Quick Ask window.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round33_quick_commands_shots.py OUT_DIR

All /api/* are mocked (no servers). Flow, in RU and EN, dark/light and the glass design:
  * «закрой инси» → the confirmation names INCY and says it lives in the tray → «Выполнить» → «INCY завершена»;
  * «закрой телеграм» → «Окно Telegram закрыто, но программа продолжает работать в фоне» + «Завершить полностью»; the button
    runs app.close again (a new confirmation card) — checked;
  * a command understood by the AI router carries the «ИИ» / «AI» badge with the model and the time in its tooltip;
  * a router «chat» decision (card null) sends the text to the normal answer (the /api/quick/ask stream is called).
Exit code 1 on any problem.
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
MODELS = [{"id": "reform-qwen3.8-flash", "caps": {"vision": False}}]

def cards(en: bool):
    t = (lambda ru, e: e if en else ru)
    return {
        "tray_confirm": {"id": "c1", "action": "app.close", "title": t("Закрыть приложение", "Close an app"), "status": "confirm",
                         "tier": "confirm", "source": "local",
                         "message": t("INCY работает в фоне (в области уведомлений), окна нет. Завершить программу? Сначала попрошу её "
                                      "закрыться сама; если не закроется — завершу процесс.",
                                      "INCY runs in the background (notification area) without a window. Quit it? I'll ask it to close "
                                      "first and end the process only if it doesn't."),
                         "confirm": {"token": "tok-incy", "tier": "confirm", "countdown": 0}},
        "tray_done": {"id": "c2", "action": "app.close", "title": t("Закрыть приложение", "Close an app"), "status": "done",
                      "source": "local", "message": t("INCY завершена", "INCY has quit")},
        "hide_done": {"id": "c3", "action": "app.close", "title": t("Закрыть приложение", "Close an app"), "status": "done",
                      "source": "local",
                      "message": t("Окно Telegram закрыто, но программа продолжает работать в фоне (в трее).",
                                   "Telegram's window is closed, but the app keeps running in the background (tray)."),
                      "next": {"action": "app.close", "params": {"name": "телеграм"}, "label": t("Завершить полностью", "Quit completely")}},
        "next_confirm": {"id": "c4", "action": "app.close", "title": t("Закрыть приложение", "Close an app"), "status": "confirm",
                         "tier": "confirm", "source": "local",
                         "message": t("Telegram работает в фоне (в области уведомлений), окна нет. Завершить программу?",
                                      "Telegram runs in the background (notification area) without a window. Quit it?"),
                         "confirm": {"token": "tok-tg", "tier": "confirm", "countdown": 0}},
        "router_done": {"id": "c5", "action": "volume.set", "title": t("Громкость", "Volume"), "status": "done", "source": "router",
                        "message": t("Громкость 0%", "Volume 0%"), "kind": "level", "data": {"level": 0},
                        "router": {"model": "reform-qwen3.8-flash", "ms": 640, "action": "volume.set"},
                        "undo": "u1"},
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

    def check(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

        async def run_variant(lang: str, theme: str, design: str):
            en = lang == "en"
            C = cards(en)
            state = {"run": [], "confirm": [], "asks": 0, "run_card": None, "confirm_card": None}
            prefs = {"theme": theme, "accent_color": "violet", "ui_design": design, "language": lang,
                     "quick_ask_model": "reform-qwen3.8-flash"}

            async def route(route_obj, request):
                path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
                if path == "/themes.css":
                    return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
                if path == "/api/quick/config":
                    return await route_obj.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False,
                                                         "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                                         "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
                if path == "/api/pg/state":
                    return await route_obj.fulfill(json={"models": MODELS, "image_models": []})
                if path == "/api/quick/ask":
                    state["asks"] += 1
                    answer = "A quasar is a galaxy core." if en else "Квазар — ядро галактики."
                    events = [{"event": "delta", "text": answer},
                              {"event": "done", "text": answer, "model": "reform-qwen3.8-flash", "tier": "fast"}]
                    return await route_obj.fulfill(status=200, content_type="text/event-stream",
                                                   body="".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events))
                if path == "/api/assistant/run":
                    body = json.loads(request.post_data or "{}")
                    state["run"].append(body)
                    card = state["run_card"]
                    if body.get("action") == "app.close":
                        card = C["next_confirm"]
                    return await route_obj.fulfill(json={"card": card, **({"router": card.get("router")} if card and card.get("router") else {})})
                if path == "/api/assistant/confirm":
                    state["confirm"].append(json.loads(request.post_data or "{}"))
                    return await route_obj.fulfill(json={"card": state["confirm_card"]})
                if path == "/api/assistant/state":
                    return await route_obj.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": False}}, "seq": 0, "timers": None})
                if path.startswith("/api/assistant/"):
                    return await route_obj.fulfill(json={"events": [], "seq": 0})
                if path.startswith("/api/"):
                    return await route_obj.fulfill(json={"items": [], "ok": True})
                return await route_obj.continue_()

            page = await browser.new_page(viewport={"width": 720, "height": 640})
            page.on("pageerror", lambda e: problems.append(f"pageerror ({lang}/{theme}/{design}): {e}"))
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(500)
            tag = f"{lang}-{theme}-{design}"

            async def shot(name):
                height = await page.evaluate("QuickAsk.render()")
                await page.set_viewport_size({"width": 720, "height": max(60, min(1000, int(height)))})
                await page.wait_for_timeout(250)
                await page.screenshot(path=str(out / f"{name}-{tag}.png"))

            async def say(text):
                await page.fill("#qa-input", text)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(700)

            state["run_card"], state["confirm_card"] = C["tray_confirm"], C["tray_done"]
            await say("quit insy" if en else "закрой инси")
            check(await page.locator(".mqc-card.mqc-confirm").count() == 1, f"{tag}: the tray confirmation card is shown")
            check("INCY" in (await page.locator(".mqc-card").first.inner_text()), f"{tag}: the card names INCY")
            await shot("01-tray-confirm")
            await page.click('.mqc-card [data-mqc="confirm"]')
            await page.wait_for_timeout(600)
            check(state["confirm"] and state["confirm"][-1].get("token") == "tok-incy", f"{tag}: confirm sent the token")
            await shot("02-tray-done")

            state["run_card"] = C["hide_done"]
            await say("close telegram" if en else "закрой телеграм")
            nxt = page.locator('.mqc-card [data-mqc="next"]')
            check(await nxt.count() == 1, f"{tag}: «Quit completely» button")
            label = (await nxt.first.inner_text()).strip() if await nxt.count() else ""
            check(label == ("Quit completely" if en else "Завершить полностью"), f"{tag}: button label {label!r}")
            await shot("03-hides-to-tray")
            if await nxt.count():
                await nxt.first.click()
                await page.wait_for_timeout(600)
                check(any(b.get("action") == "app.close" for b in state["run"]), f"{tag}: the button ran app.close")
                check(await page.locator('.mqc-card [data-mqc="next"]').count() == 0, f"{tag}: the used button is gone")
                check(await page.locator(".mqc-card.mqc-confirm").count() >= 1, f"{tag}: a new confirmation appeared")

            state["run_card"] = C["router_done"]
            await say("sound to zero" if en else "звук в ноль")
            badge = page.locator(".mqc-src-ai")
            check(await badge.count() == 1, f"{tag}: AI badge")
            if await badge.count():
                check((await badge.first.inner_text()).strip() == ("AI" if en else "ИИ"), f"{tag}: badge text")
                tip = await badge.first.get_attribute("title") or ""
                check("reform-qwen3.8-flash" in tip and ("0.6" in tip), f"{tag}: badge tooltip {tip!r}")
            await shot("04-router-badge")

            state["run_card"] = None
            asks = state["asks"]
            await say("something fun" if en else "что-нибудь интересное")
            await page.wait_for_timeout(800)
            check(state["asks"] == asks + 1, f"{tag}: a «chat» decision goes to the normal answer")
            if en:
                text = await page.evaluate("document.body.innerText")
                check(not any("\u0400" <= ch <= "\u04ff" for ch in text),
                      f"{tag}: Cyrillic in the English window")
            await page.close()

        for lang, theme, design in (("ru", "dark", "classic"), ("ru", "light", "classic"), ("ru", "dark", "glass"),
                                    ("en", "dark", "classic"), ("en", "light", "glass")):
            await run_variant(lang, theme, design)
        await browser.close()
    server.shutdown()
    print("problems:", len(problems))
    for p in problems:
        print(" -", p)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r33-quick-shots"))))
