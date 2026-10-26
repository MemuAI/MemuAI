"""Round 23: screenshots of the redesigned Quick Ask window (headless Chromium, mocked server).

Not part of the unittest suite (needs Playwright + Chromium):

    PYTHONUTF8=1 NO_PROXY='*' /workspace/work/venv/bin/python tests/quick_ask_r23_shots.py OUT_DIR [scene,scene…] [--all]

Scenes: idle, tip, page, selection, answer, page-answer, picker, chats, menu, error, commands, custom,
widths (520/600/640/720 × designs), matrix (all 5 designs × light/dark for idle/page/answer).
Every shot is sized like the native window: the width given and the height QuickAsk.render() asks for.
The script exits 1 on page errors, a clipped footer or a footer button pushed out of the window.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
STATIC = ROOT / "app" / "pgstatic"
CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"

from quick_ask_ui_shots import MODELS, PREFS as BASE_PREFS, CHATS, PAGE, PAGE_ANSWER, ESCALATED_ANSWER, HISTORY, CODE_ANSWER  # noqa: E402

DESIGNS = (("dark", "classic", "violet"), ("light", "classic", "terracotta"), ("dark", "aurora", "rose"), ("light", "aurora", "blue"),
           ("dark", "focus", "graphite"), ("light", "focus", "graphite"), ("dark", "grid", "teal"), ("light", "grid", "teal"),
           ("dark", "glass", "blue"), ("light", "glass", "violet"))
SHOW = "{fresh:true, maxHeight:640, rounded:true, corner:'dwm', radius:8%s}"

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass

def serve():
    handler = functools.partial(Quiet, directory=str(STATIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"

async def main(out: Path, scenes: set) -> int:
    from playwright.async_api import async_playwright
    from app import themes
    out.mkdir(parents=True, exist_ok=True)
    server, base = serve()
    errors: list = []
    state = {"prefs": dict(BASE_PREFS, quick_ask_advanced_model="claude-sonnet-4.5", quick_ask_model="gemini-2.5-flash",
                           quick_ask_routing="auto", quick_ask_screenshot_tool=True),
             "events": CODE_ANSWER, "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}, "history": HISTORY,
             "assistant_card": None}

    async def route(route_obj, request):
        url = request.url
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": state["prefs"], "status": {"native": True, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"},
                                                                                     "conflicts": [], "warnings": []},
                                                 "alternatives": [], "ui": state["ui"], "endpoint": state["endpoint"]})
        if path == "/api/quick/ui":
            return await route_obj.fulfill(json={"ok": True, **state["ui"]})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": MODELS, "image_models": []})
        if path == "/api/quick/history":
            return await route_obj.fulfill(json={"items": state["history"], "enabled": True})
        if path in ("/api/quick/window", "/api/quick/tool_result"):
            return await route_obj.fulfill(json={"ok": True})
        if path == "/api/quick/chats":
            rows = [dict(row, updated=int(time.time() * 1000) - i * 5400_000) for i, row in enumerate(CHATS)]
            return await route_obj.fulfill(json={"items": rows, "total": len(rows)})
        if path == "/api/quick/ask":
            body = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in state["events"])
            return await route_obj.fulfill(status=200, content_type="text/event-stream", body=body)
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": True, "voice": {"enabled": True, "auto_speak": False}}, "seq": 0, "timers": None})
        if path == "/api/assistant/run":
            return await route_obj.fulfill(json={"card": state["assistant_card"]} if state["assistant_card"] else {"matched": False})
        if path.startswith("/api/assistant/"):
            return await route_obj.fulfill(json={"events": [], "seq": 0})
        if path == "/api/companion/models":
            return await route_obj.fulfill(json={"models": [{"id": "sample", "kind": "vrm", "name": "Sample", "url": "/companion/models/sample/sample.vrm"}]})
        if path == "/api/companion/window":
            return await route_obj.fulfill(json={"supported": True, "visible": False, "ok": True})
        if path == "/api/pg/preferences":
            return await route_obj.fulfill(json=state.get("companion_prefs", {}))
        if path.startswith("/api/companion/"):
            return await route_obj.fulfill(json={})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

        async def open_page(theme="dark", design="classic", accent="violet", lang="ru", width=720):
            state["prefs"].update(theme=theme, ui_design=design, accent_color=accent, language=lang)
            page = await browser.new_page(viewport={"width": width, "height": 640}, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type in ("error", "warning") and "404" not in m.text else None)
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(450)
            return page

        async def show(page, extra=""):
            await page.evaluate("QuickAsk.onShow(" + SHOW % extra + ")")
            await page.wait_for_timeout(350)

        async def shot(page, name, width=720, wait=350):
            await page.wait_for_timeout(wait)
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(60, int(height))})
            await page.wait_for_timeout(220)
            geo = await page.evaluate("""() => { const b = document.getElementById('qa-bar').getBoundingClientRect();
              const kids = Array.from(document.querySelectorAll('#qa-bar > button:not([hidden]), #qa-plugin-buttons > button'));
              const out = kids.filter((k) => { const r = k.getBoundingClientRect(); return r.width > 0 && r.right > b.right + .5; }).map((k) => k.id || k.className);
              const name = document.getElementById('qa-model-name');
              return {out, bar: b.height, inner: document.getElementById('qa-inner').getBoundingClientRect().height, vh: innerHeight}; }""")
            if geo["out"]:
                errors.append(f"footer overflow in {name}: {geo['out']}")
            if geo["inner"] > geo["vh"] + 1:
                errors.append(f"content taller than the window in {name}: {geo}")
            await page.screenshot(path=str(out / f"{name}.png"))
            await page.close()

        async def ask(page, text, events):
            state["events"] = events
            await page.fill("#qa-input", text)
            await page.click("#qa-send")
            await page.wait_for_timeout(700)

        def want(name):
            return "all" in scenes or name in scenes

        page_extra = ", page:" + json.dumps(PAGE, ensure_ascii=False)
        fast_code = [{"event": "route", "tier": "fast", "model": "gemini-2.5-flash", "reason": "simple"}] + [dict(e) for e in CODE_ANSWER]

        if want("idle"):
            page = await open_page(); await show(page); await shot(page, "idle-dark")
            page = await open_page(theme="light", accent="terracotta"); await show(page); await shot(page, "idle-light")
        if want("tip"):
            state["ui"] = {"onboarded": False}
            page = await open_page(); await show(page); await shot(page, "tip-dark")
            page = await open_page(theme="light", design="glass", accent="blue", lang="en"); await show(page); await shot(page, "tip-light-glass-en")
            state["ui"] = {"onboarded": True}
        if want("bare"):
            state["history"] = []
            page = await open_page(); await show(page); await page.fill("#qa-input", "Почему небо голубое"); await page.dispatch_event("#qa-input", "input")
            await shot(page, "typing-dark")
            page = await open_page(); await show(page); await page.fill("#qa-input", "2^10 + 15%"); await page.dispatch_event("#qa-input", "input")
            await shot(page, "typing-calc")
            state["history"] = HISTORY
        if want("page"):
            page = await open_page(); await show(page, page_extra); await shot(page, "page-dark")
            page = await open_page(theme="light", accent="terracotta"); await show(page, page_extra); await shot(page, "page-light")
            page = await open_page(); await show(page, page_extra); await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowDown")
            await shot(page, "page-keyboard")
        if want("selection"):
            sel = ", selection: 'The quarterly deadline is tight, so we will ship the hotfix first and refactor the hotkey module next sprint.'"
            page = await open_page(); await show(page, sel); await shot(page, "selection-dark")
        if want("answer"):
            page = await open_page(); await show(page); await ask(page, "Как создать ветку и сразу запушить её?", fast_code); await shot(page, "answer-dark")
            page = await open_page(theme="light", accent="terracotta"); await show(page); await ask(page, "Как создать ветку и сразу запушить её?", fast_code)
            await shot(page, "answer-light")
            page = await open_page(); await show(page); await ask(page, "Напиши debounce на TypeScript с отменой", ESCALATED_ANSWER); await shot(page, "answer-escalated")
            page = await open_page(); await show(page)
            await page.evaluate("""() => { const S = QuickAsk.state(); S.busy = true;
              S.turns = [{q: 'Какая погода в Москве?', images: [], selection: '', a: '', model: 'gemini-2.5-flash', tier: 'fast',
                status: {status: 'searching', label: 'Ищу: погода Москва сегодня'}, sources: [], shots: [], done: false, error: '',
                instant: {text: 'Москва: +7 °C, облачно, ветер 4 м/с', source: 'Open-Meteo', note: 'сейчас'}}]; }""")
            await shot(page, "answer-streaming")
        if want("page-answer"):
            page = await open_page(); await show(page, page_extra); state["events"] = PAGE_ANSWER
            await page.keyboard.press("Alt+1"); await page.wait_for_timeout(700); await shot(page, "page-answer-dark")
        if want("picker"):
            page = await open_page(); await show(page); await page.click("#qa-model"); await shot(page, "picker-dark")
            page = await open_page(theme="light", accent="terracotta"); await show(page); await page.click("#qa-model"); await shot(page, "picker-light")
        if want("chats"):
            page = await open_page(); await show(page); await page.keyboard.press("Control+h"); await shot(page, "chats-dark", wait=500)
        if want("menu"):
            page = await open_page(); await show(page); await page.keyboard.press("Control+k"); await shot(page, "menu-dark")
            page = await open_page(theme="light", accent="terracotta"); await show(page); await ask(page, "Как создать ветку?", fast_code)
            await page.click("#qa-more"); await shot(page, "menu-after-answer-light")
            page = await open_page(); await show(page); await page.keyboard.press("Control+k"); await page.fill("#qa-menu-q", "сним"); await page.dispatch_event("#qa-menu-q", "input")
            await shot(page, "menu-search")
            page = await open_page(); await show(page); await page.keyboard.press("Control+k")
            await page.click('[data-act="keys"]'); await shot(page, "menu-shortcuts")
        if want("error"):
            page = await open_page(); await show(page)
            await page.evaluate("() => { QuickAsk.state().sessionModel = 'deepseek-v3.2'; QuickAsk.state().attachments = [{data: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', label: 'экран'}]; }")
            await page.fill("#qa-input", "Что на экране?"); await page.click("#qa-send"); await shot(page, "error-vision")
            page = await open_page(); await show(page)
            await ask(page, "Привет", [{"event": "route", "tier": "fast", "model": "gemini-2.5-flash"}, {"event": "error", "code": "model", "error": "HTTP 401: Invalid API key"}])
            await shot(page, "error-model")
        if want("commands"):
            state["assistant_card"] = {"id": "c1", "action": "volume.set", "title": "Громкость 30 %", "status": "done", "kind": "level",
                                       "data": {"level": 30}, "undo": "u1"}
            page = await open_page(); await show(page); await page.fill("#qa-input", "сделай громкость 30"); await page.click("#qa-send")
            await shot(page, "commands-card", wait=700)
            state["assistant_card"] = {"id": "c2", "action": "power.shutdown", "title": "Выключить компьютер?", "status": "confirm",
                                       "message": "Несохранённая работа в открытых программах может пропасть.", "tier": "dangerous",
                                       "confirm": {"token": "t1", "tier": "dangerous", "countdown": 5}}
            page = await open_page(theme="light", accent="terracotta"); await show(page); await page.fill("#qa-input", "выключи компьютер"); await page.click("#qa-send")
            await shot(page, "commands-confirm-light", wait=700)
            state["assistant_card"] = None
        if want("custom"):
            state["endpoint"] = {"source": "custom", "url": "http://localhost:11434/v1", "host": "localhost:11434", "label": "Ollama",
                                 "models": [{"id": "qwen2.5:14b", "caps": {"vision": False}}, {"id": "llava:13b", "caps": {"vision": True}}]}
            state["prefs"].update(quick_ask_endpoint_model="qwen2.5:14b", quick_ask_endpoint_advanced_model="")
            page = await open_page(); await show(page); await page.click("#qa-model"); await shot(page, "custom-endpoint-picker")
            state["endpoint"] = {"source": "proxy"}
        if want("companion"):
            state["companion_prefs"] = {"companion_quick_enabled": True, "companion_quick_mode": "inline", "companion_model_id": "sample"}
            page = await open_page(); await show(page)
            await page.wait_for_timeout(4000)
            print("companion:", await page.evaluate("""async () => { const r = await fetch('/api/pg/preferences'); const p = await r.json();
              return JSON.stringify({p: window.MemuQuickCompanion && MemuQuickCompanion.place(), prefs: p, norm: MemuCompanionCore.normalizePrefs(p).companion_quick_enabled,
                panels: Array.from(document.querySelectorAll('.qa-plugin')).map((e) => e.dataset.plugin + ':' + e.hidden)}); }"""))
            try:
                await page.hover(".memu-companion.in-quick", timeout=3000)
            except Exception:
                errors.append("companion: the inline companion did not appear")
            await shot(page, "companion-inline", wait=600)
            state["companion_prefs"] = {}
        if want("widths"):
            for width, theme, lang, design in ((520, "dark", "ru", "classic"), (520, "light", "en", "glass"), (600, "light", "ru", "grid"),
                                               (640, "dark", "en", "aurora"), (720, "light", "ru", "focus")):
                page = await open_page(theme=theme, design=design, lang=lang, width=width)
                await show(page); await ask(page, "Как создать ветку?", fast_code)
                await page.evaluate("MemuQuick.addComposerButton({id: 'extra', title: 'Extra', icon: MemuQuick.icons.spark, onClick() {}})")
                await shot(page, f"width-{width}-{design}-{theme}", width=width)
            page = await open_page(width=520); await show(page, page_extra); await shot(page, "width-520-page", width=520)
        if want("matrix"):
            for theme, design, accent in DESIGNS:
                page = await open_page(theme=theme, design=design, accent=accent); await show(page, page_extra); await shot(page, f"m-page-{design}-{theme}")
                page = await open_page(theme=theme, design=design, accent=accent); await show(page)
                await ask(page, "Как создать ветку и сразу запушить её?", fast_code); await shot(page, f"m-answer-{design}-{theme}")
        if want("corner"):
            for theme in ("dark", "light"):
                page = await open_page(theme=theme); await show(page)
                height = await page.evaluate("QuickAsk.render()")
                await page.set_viewport_size({"width": 720, "height": int(height)}); await page.wait_for_timeout(250)
                color = await page.evaluate("getComputedStyle(document.body).backgroundColor")
                print("window bg", theme, color)
                await page.screenshot(path=str(out / f"corner-{theme}.png"), clip={"x": 0, "y": 0, "width": 40, "height": 40})
                await page.close()
        await browser.close()
    server.shutdown()
    for line in errors:
        print(line)
    print(f"shots → {out}")
    return 1 if any(line.startswith(("pageerror", "footer", "content")) for line in errors) else 0

if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r23-quick")
    wanted = set((sys.argv[2] if len(sys.argv) > 2 else "idle").split(","))
    raise SystemExit(asyncio.run(main(target, wanted)))
