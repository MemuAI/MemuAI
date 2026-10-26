"""Round 27 (r27-quick-canvas): headless Chromium checks + screenshots of panels and quick actions in Quick Ask.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_quick_canvas_shots.py OUT_DIR

A real backend (app.quick_canvas routes + the MemuAI panel routes over a temporary profile with To-Do, clock,
notes, timers and a weather panel with data) serves the window; the run executor is a mock. Checks (exit code 1
on any problem; tests/round27_quick_canvas_test.py runs it):
  * the window opens in the input mode (canvas folded, the field focused, panels.js NOT loaded yet);
  * Ctrl+B opens the canvas; panels are rendered by panels.js (the same .mp-card) and buttons are round;
  * no two items overlap and every item stays inside the grid at 720 / 520 / 420 / 340 px (8 / 8 / 6 / 4 columns);
  * a To-Do toggled in the window reaches /api/pg/panels-todo (the one source), a change made «in MemuAI»
    (directly in the backend) appears in the window after a reload without restarting;
  * edit mode wiggles, shows ✕/⚙/resize; drag moves a button to another cell and saves the layout;
  * the editor builds a macro; the first run of a risky button asks for approval; an in-run confirmation
    shows a notice; Esc in the canvas returns to the input instead of closing the window;
  * ru/en, dark/light, reduced motion.
Screens: qcv-*.png in OUT_DIR (look at every one).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")
MODELS = [{"id": "deepseek-v4.1-flash", "caps": {"vision": False, "search": False}}]
CALLS: list = []

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def seed_panels():
    from app import panels
    todo = panels.create_panel(template="todo", start=False)
    clock = panels.create_panel(template="clock", start=False)
    notes = panels.create_panel(template="notes", start=False)
    weather = next(p for p in panels.snapshot()["panels"] if p["kind"] == "weather")

    def fill(doc):
        doc["settings"]["location"] = {"mode": "manual", "name": "Москва", "lat": 55.75, "lon": 37.62, "country": "RU", "timezone": "Europe/Moscow"}
        for p in doc["panels"]:
            if p["id"] == weather["id"]:
                p["status"] = "live"
                p["wall"] = None
                p["last_refresh"] = time.time() - 300
                p["data"] = {"place": "Москва", "temp": 7, "t_unit": "°C", "feels": 4, "humidity": 81, "wind": 5, "w_unit": "м/с",
                             "cond": "Облачно", "icon": "cloudy", "hi": 9, "lo": 3,
                             "days": [{"label": "Сегодня", "value": "9°", "sub": "3°", "icon": "cloudy"}, {"label": "Сб", "value": "11°", "sub": "4°", "icon": "partly"},
                                      {"label": "Вс", "value": "8°", "sub": "2°", "icon": "rain"}],
                             "hours": []}
                p["sources"] = [{"name": "Open-Meteo", "url": "https://open-meteo.com"}]
    panels.mutate(fill)
    return {"todo": todo["id"], "clock": clock["id"], "notes": notes["id"], "weather": weather["id"]}

def make_app():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from app import quick_canvas, quick_actions as qa, panels_api

    async def run_action(action_id, params, lang="ru", confirmed=False):
        CALLS.append((action_id, params, confirmed))
        from app import assistant_catalog as catalog
        tier = (catalog.get(action_id) or {}).get("tier", "safe")
        if tier != "safe" and not confirmed:
            return {"status": "confirm", "message": "Подтвердите: " + action_id, "confirm": {"token": "tok", "tier": tier, "countdown": 0, "expires": time.time() + 120}}
        await asyncio.sleep(0.4)
        return {"status": "done", "message": "ok " + action_id}

    async def confirm_token(token, accept, lang="ru"):
        CALLS.append(("confirm", token, accept))
        return {"status": "done" if accept else "cancelled", "message": "confirmed" if accept else "cancelled"}

    async def ask_ai(prompt, lang):
        return "ИИ: " + prompt[:40]

    quick_canvas.RUNNER = qa.Runner(run_action=run_action, confirm_token=confirm_token, ask_ai=ask_ai,
                                    insert_text=lambda text: asyncio.sleep(0, True), clipboard_get=lambda: "буфер",
                                    dnd=lambda mode: True, engine_settings=lambda: {})
    app = FastAPI()
    app.include_router(quick_canvas.router)
    panels_api.register_panel_routes(app)
    app.mount("/", StaticFiles(directory=str(STATIC), html=True))
    return app

async def main(out: Path) -> int:
    import uvicorn
    from playwright.async_api import async_playwright
    from app import themes
    out.mkdir(parents=True, exist_ok=True)
    seeded = seed_panels()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(make_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    problems: list = []
    state = {"prefs": {}}

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
        if path.startswith("/api/quick/canvas") or path.startswith("/api/pg/panels"):
            return await route_obj.continue_()
        if path == "/api/assistant/state":
            return await route_obj.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await route_obj.continue_()

    show = json.dumps({"fresh": True, "maxHeight": 760, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False,
                       "source": "hotkey", "page": None, "selection": ""})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None, args=["--no-sandbox"])

        async def open_page(width=720, theme="dark", lang="ru", reduced=False):
            state["prefs"] = {"theme": theme, "accent_color": "violet", "ui_design": "classic", "language": lang, "quick_ask_model": "deepseek-v4.1-flash"}
            ctx = await browser.new_context(viewport={"width": width, "height": 700}, reduced_motion="reduce" if reduced else "no-preference")
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("console", lambda m: print("console:", m.text) if m.type == "error" else None)
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(600)
            await page.evaluate("QuickAsk.onShow(" + show + ")")
            await page.wait_for_timeout(700)
            return page

        async def shot(page, name, width=720):
            await page.wait_for_timeout(250)
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(80, min(900, int(height)))})
            await page.wait_for_timeout(450)
            await page.screenshot(path=str(out / f"{name}.png"))

        async def layout_ok(page, what):
            bad = await page.evaluate("""() => {
              const grid = document.querySelector('.qcv-grid'); if (!grid) return ['no grid'];
              const g = grid.getBoundingClientRect();
              const items = Array.from(grid.querySelectorAll('.qcv-item')).map((n) => ({ id: n.dataset.item, r: n.getBoundingClientRect() }));
              const out = [];
              for (const a of items) {
                if (a.r.left < g.left - 1 || a.r.right > g.right + 1) out.push('outside ' + a.id + ' ' + [a.r.left, a.r.right, g.left, g.right].map(Math.round).join(','));
                for (const b of items) if (a.id < b.id && a.r.left < b.r.right - 1 && b.r.left < a.r.right - 1 && a.r.top < b.r.bottom - 1 && b.r.top < a.r.bottom - 1) out.push('overlap ' + a.id + '/' + b.id);
              }
              const card = document.getElementById('qa-card').getBoundingClientRect();
              if (g.right > card.right) out.push('grid outside the card');
              return out;
            }""")
            check(not bad, f"{what}: layout without overlaps ({bad})")

        api = lambda page, path, body=None: page.evaluate(  # noqa: E731
            "([p, b]) => fetch(p, b ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)} : {}).then(r => r.json())", [path, body])

        page = await open_page()
        check(await page.evaluate("document.activeElement && document.activeElement.id === 'qa-input'"), "the input has the focus on open")
        check(await page.evaluate("!document.querySelector('.qcv-body') || document.querySelector('.qcv-body').hidden"), "the canvas is folded by default")
        check(await page.evaluate("!window.MemuPanels"), "panels.js is not loaded until the canvas is opened")
        await shot(page, "qcv-closed-strip-dark-ru")
        await page.keyboard.press("Control+b")
        await page.wait_for_timeout(700)
        check(await page.evaluate("!document.querySelector('.qcv-body').hidden"), "Ctrl+B opens the canvas")
        check(await page.evaluate("!!window.MemuPanels"), "opening the canvas loads panels.js")
        await shot(page, "qcv-empty-dark-ru")

        await page.focus('[data-qcv="add"]')
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        check(await page.evaluate("document.activeElement && document.activeElement.id === 'qa-input'"), "Esc in the canvas returns to the input")
        await page.click('[data-qcv="templates"]')
        await page.wait_for_timeout(700)
        await shot(page, "qcv-templates-dark-ru")
        await page.close()

        for key, size in (("weather", "m"), ("todo", "l"), ("clock", "s"), ("notes", "w")):
            r = await asyncio.to_thread(lambda: __import__("httpx").post(base + "/api/quick/canvas/panels", json={"panel": seeded[key], "size": size}, timeout=10).json())
            check(r.get("ok"), f"add panel {key}")
        import httpx
        tpls = httpx.get(base + "/api/quick/canvas/catalog?lang=ru", timeout=20).json()["templates"]
        for tp in tpls[:7]:
            row = {k: v for k, v in tp.items() if k != "key"}
            r = httpx.post(base + "/api/quick/canvas/actions", json={"action": row}, timeout=10).json()
            check(r.get("ok"), f"add button {tp['key']} ({r.get('error')})")
        closer = httpx.post(base + "/api/quick/canvas/actions", json={"action": {"label": "Закрыть Блокнот", "icon": {"kind": "emoji", "value": "🧹"}, "color": "#64748b",
                            "steps": [{"type": "command", "action": "volume.set", "params": {"level": 30}}, {"type": "command", "action": "app.close", "params": {"name": "notepad"}}]}}, timeout=10).json()
        check(closer.get("ok") and closer["action"]["risks"], "a risky button reports its risks")

        for theme, lang, width in (("dark", "ru", 720), ("light", "en", 720), ("light", "ru", 520), ("dark", "en", 420), ("dark", "ru", 340)):
            page = await open_page(width=width, theme=theme, lang=lang)
            await page.keyboard.press("Control+b")
            await page.wait_for_timeout(1400)
            cols = await page.evaluate("MemuQuickCanvas.state().cols")
            check(cols == (8 if width >= 560 else 6 if width >= 400 else 4), f"{width}px → {cols} columns")
            n_cards = await page.evaluate("document.querySelectorAll('.qcv-item .mp-card').length")
            check(n_cards == 4, f"{theme}/{lang}/{width}: four MemuAI panels rendered by panels.js ({n_cards})")
            await layout_ok(page, f"{theme}/{lang}/{width}")
            await shot(page, f"qcv-grid-{theme}-{lang}-{width}", width)
            await page.close()

        page = await open_page()
        await page.keyboard.press("Control+b")
        await page.wait_for_timeout(1300)
        lists = httpx.get(base + "/api/pg/panels-todo", timeout=10).json()
        lid = (lists.get("lists") or [{}])[0].get("id")
        if lid:
            httpx.post(base + f"/api/pg/panels-todo/{lid}/items", json={"title": "Купить хлеб"}, timeout=10)
        await page.evaluate("MemuPanels.load(true)")
        await page.wait_for_timeout(900)
        has = await page.evaluate("document.querySelector('.qcv-grid').textContent.includes('Купить хлеб')")
        check(has, "a To-Do added in MemuAI shows up in the window after a reload (no restart)")
        box = page.locator(".qcv-grid .mp-todo-item input[type=checkbox], .qcv-grid [data-todo-toggle]").first
        if await box.count():
            await box.click()
            await page.wait_for_timeout(900)
            items = httpx.get(base + "/api/pg/panels-todo", timeout=10).json()["lists"][0].get("items", [])
            check(any(i.get("done") for i in items), "toggling the To-Do in the window reached the backend")
        else:
            check(False, "a To-Do checkbox in the window")
        await shot(page, "qcv-todo-synced-dark-ru")

        await page.keyboard.press("Control+e")
        await page.wait_for_timeout(400)
        check(await page.evaluate("document.querySelector('.qcv').dataset.edit === '1'"), "Ctrl+E enters edit mode")
        await page.screenshot(path=str(out / "qcv-edit-dark-ru.png"))
        before = await page.evaluate("MemuQuickCanvas.state().layout.find(i => i.type === 'action')")
        node = page.locator(f'.qcv-item[data-item="{before["id"]}"]')
        bb = await node.bounding_box()
        grid = await page.locator(".qcv-grid").bounding_box()
        await page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        await page.mouse.down()
        await page.mouse.move(grid["x"] + grid["width"] - 20, grid["y"] + 10, steps=12)
        await page.wait_for_timeout(150)
        await page.screenshot(path=str(out / "qcv-dragging-dark-ru.png"))
        await page.mouse.up()
        await page.wait_for_timeout(700)
        after = next(i for i in httpx.get(base + "/api/quick/canvas", timeout=10).json()["layout"]["items"] if i["id"] == before["id"])
        check((after["x"], after["y"]) != (before["x"], before["y"]) and after["x"] >= 6, f"drag moved and saved the button ({before['x']},{before['y']} → {after['x']},{after['y']})")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check(await page.evaluate("document.querySelector('.qcv').dataset.edit === '0'"), "Esc leaves edit mode")
        await layout_ok(page, "after drag")

        CALLS.clear()
        await page.evaluate(f"MemuQuickCanvas.runAction('{closer['action']['id']}', true)")
        await page.wait_for_timeout(500)
        check(await page.evaluate("MemuQuickCanvas.state().view === 'approve'"), "a risky button asks for approval first")
        check(not CALLS, "nothing ran before the approval")
        await shot(page, "qcv-approve-dark-ru")
        await page.click('[data-qcv="approve"]')
        await page.wait_for_timeout(1500)
        check(any(c[0] == "app.close" and c[2] is True for c in CALLS), f"the approved confirm-step ran confirmed ({CALLS})")
        await shot(page, "qcv-run-done-dark-ru")
        await page.close()

        for theme, lang in (("light", "en"), ("dark", "ru")):
            page = await open_page(theme=theme, lang=lang)
            await page.keyboard.press("Control+b")
            await page.wait_for_timeout(800)
            tpl = next(t for t in httpx.get(base + f"/api/quick/canvas/catalog?lang={lang}", timeout=10).json()["templates"] if t["key"] == "call")
            await page.evaluate("(t) => MemuQuickCanvas.openEditor(null, t)", tpl)
            await page.wait_for_timeout(900)
            await shot(page, f"qcv-editor-{theme}-{lang}")

            await page.click('[data-add-step="keys"]')
            await page.click('[data-add-step="shell"]')
            await page.fill('[data-f="s3.keys"]', "Ctrl+Shift+Esc")
            await page.fill('[data-f="s4.command"]', "Get-Process | Select -First 5")
            await page.wait_for_timeout(1200)
            check(await page.locator(".qcv-danger").count() == 1, f"{lang}: the PowerShell step carries its warning")
            check(await page.locator(".qcv-risk").count() == 1, f"{lang}: the editor lists what the first run will ask about")
            await page.evaluate("document.querySelector('.qcv-editor').scrollTop = 1e6")
            await page.wait_for_timeout(200)
            await shot(page, f"qcv-editor-steps-{theme}-{lang}")
            await page.close()

        layout = httpx.get(base + "/api/quick/canvas", timeout=10).json()
        items = layout["layout"]["items"]
        for i in items[:6]:
            if i["type"] == "action" or i["ref"] == seeded["weather"]:
                i["pinned"] = True
        httpx.post(base + "/api/quick/canvas/layout", json={"items": items}, timeout=10)
        keys = httpx.post(base + "/api/quick/canvas/actions", json={"action": {"label": "Диспетчер", "icon": {"kind": "builtin", "value": "terminal"},
                          "steps": [{"type": "keys", "keys": "Ctrl+Shift+Esc", "confirm": True}]}}, timeout=10).json()
        page = await open_page()
        await page.wait_for_timeout(900)
        await shot(page, "qcv-strip-pinned-dark-ru")
        await page.evaluate(f"MemuQuickCanvas.runAction('{keys['action']['id']}', true, true)")
        await page.wait_for_timeout(900)
        check(await page.evaluate("!!document.querySelector('.qcv-note[data-kind=\"confirm\"]')"), "a step marked «ask before» shows a confirmation notice")
        await shot(page, "qcv-confirm-notice-dark-ru")
        await page.click('[data-note-act="reject"]')
        await page.wait_for_timeout(700)
        check(not any(c[0] == "keys.press" for c in CALLS), "a rejected step never ran")
        await page.close()

        page = await open_page(theme="light", reduced=True)
        await page.keyboard.press("Control+b")
        await page.wait_for_timeout(1000)
        await page.keyboard.press("Control+e")
        await page.wait_for_timeout(300)
        anim = await page.evaluate("getComputedStyle(document.querySelector('.qcv-item')).animationName")
        check(anim == "none", f"reduced motion: no wiggle ({anim})")
        await shot(page, "qcv-edit-reduced-light-ru")
        await page.close()
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0

if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r27/r27-quick-canvas")
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="qcv-shots-")
    sys.exit(asyncio.run(main(out_dir)))
