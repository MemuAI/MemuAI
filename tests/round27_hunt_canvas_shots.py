"""Round 27 (r27-hunt-canvas): headless checks of the Quick Ask canvas fixes (run by tests/round27_hunt_canvas_test.py).

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round27_hunt_canvas_shots.py OUT_DIR

* a drag in a narrow (6-column) window keeps a W panel 8 wide in the stored layout;
* two quick presses of a button start one run;
* a render does not rebuild unchanged buttons (10 panels + 30 buttons: render() stays fast);
* the editor shows «Во всех программах» for a hotkey and «Иконка программы» (ru/en screenshots).
Exit code 1 on any problem; screens hc-*.png in OUT_DIR.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

async def main(out: Path) -> int:
    import httpx
    import uvicorn
    from playwright.async_api import async_playwright
    import round27_quick_canvas_shots as base
    from app import panels, themes
    out.mkdir(parents=True, exist_ok=True)
    seeded = base.seed_panels()
    port = base.free_port()
    server = uvicorn.Server(uvicorn.Config(base.make_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    B = f"http://127.0.0.1:{port}"
    problems: list = []
    prefs = {}

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def post(path, body):
        return httpx.post(B + path, json=body, timeout=20).json()

    async def route(r, req):
        path = "/" + req.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "/themes.css":
            return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await r.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                         "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await r.fulfill(json={"models": base.MODELS, "image_models": []})
        if path == "/api/quick/canvas/app-icon":
            return await r.fulfill(json={"ok": False, "error": "App icons are available on Windows only"})
        if path.startswith("/api/quick/canvas") or path.startswith("/api/pg/panels"):
            return await r.continue_()
        if path == "/api/assistant/state":
            return await r.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if path.startswith("/api/"):
            return await r.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await r.continue_()

    show = json.dumps({"fresh": True, "maxHeight": 760, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False, "source": "hotkey", "page": None, "selection": ""})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=base.CHROME if os.path.exists(base.CHROME) else None, args=["--no-sandbox"])

        async def open_page(width=720, lang="ru", theme="dark"):
            prefs.clear()
            prefs.update({"theme": theme, "accent_color": "violet", "ui_design": "classic", "language": lang, "quick_ask_model": "deepseek-v4.1-flash"})
            ctx = await browser.new_context(viewport={"width": width, "height": 700})
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (e) {{}}")
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            await page.route("**/*", route)
            await page.goto(B + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(600)
            await page.evaluate("QuickAsk.onShow(" + show + ")")
            await page.wait_for_timeout(500)
            return page

        async def shot(page, name, width=720):
            await page.wait_for_timeout(250)
            h = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(80, min(900, int(h)))})
            await page.wait_for_timeout(400)
            await page.screenshot(path=str(out / f"{name}.png"))

        post("/api/quick/canvas/panels", {"panel": seeded["notes"], "size": "w"})
        ids = [post("/api/quick/canvas/actions", {"action": {"label": f"N{i}", "steps": [{"type": "wait", "ms": 900}]}})["action"]["id"] for i in range(3)]
        page = await open_page(width=420)
        await page.keyboard.press("Control+b")
        await page.wait_for_timeout(1200)
        check(await page.evaluate("MemuQuickCanvas.state().cols") == 6, "420 px → 6 columns")
        await page.keyboard.press("Control+e")
        await page.wait_for_timeout(300)
        bb = await page.locator(".qcv-item[data-type=action]").first.bounding_box()
        await page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        await page.mouse.down()
        await page.mouse.move(bb["x"] + bb["width"] * 2.6, bb["y"] + bb["height"] / 2, steps=8)
        await page.mouse.up()
        await page.wait_for_timeout(900)
        items = httpx.get(B + "/api/quick/canvas").json()["layout"]["items"]
        wide = [i for i in items if i["type"] == "panel" and i["ref"] == seeded["notes"]]
        check(wide and wide[0]["w"] == 8, f"the W panel stays 8 wide after a drag in a narrow window ({wide})")
        moved = [i for i in items if i["type"] == "action" and i["ref"] == ids[0]]
        check(moved and moved[0]["x"] > 0, f"the dragged button moved ({moved})")
        await page.close()

        page = await open_page()
        n = await page.evaluate(f"""async () => {{ await Promise.all([MemuQuickCanvas.runAction('{ids[1]}', true), MemuQuickCanvas.runAction('{ids[1]}', true)]);
            const r = await fetch('/api/quick/canvas/runs').then(r => r.json()); return r.runs.filter(x => x.action === '{ids[1]}').length; }}""")
        check(n == 1, f"a double press starts one run ({n})")
        await page.close()

        for kind in ("clock", "notes", "todo", "timers", "system", "clock"):
            p = panels.create_panel(template=kind, start=False)
            post("/api/quick/canvas/panels", {"panel": p["id"], "size": "s"})
        for k in ("weather", "todo", "clock"):
            post("/api/quick/canvas/panels", {"panel": seeded[k], "size": "m"})
        for i in range(27):
            post("/api/quick/canvas/actions", {"action": {"label": f"K{i}", "color": "#3b82f6", "steps": [{"type": "wait", "ms": 100}]}})
        page = await open_page()
        res = await page.evaluate("""async () => { const t0 = performance.now(); MemuQuickCanvas.setOpen(true); await MemuQuickCanvas.ensurePanels();
            let n = 0; while (document.querySelectorAll('.qcv-item .mp-card').length < 10 && n < 600) { await new Promise(r => setTimeout(r, 5)); n++; }
            const open = performance.now() - t0;
            const node = document.querySelector('.qcv-item[data-type=action] .qcv-act');
            const r0 = performance.now(); for (let i = 0; i < 10; i++) MemuQuickCanvas.render(); const avg = (performance.now() - r0) / 10;
            return { open, avg, cards: document.querySelectorAll('.qcv-item .mp-card').length, buttons: document.querySelectorAll('.qcv-item[data-type=action]').length,
                     same: node === document.querySelector('.qcv-item[data-type=action] .qcv-act') }; }""")
        print("perf", res)
        check(res["cards"] >= 10 and res["buttons"] == 30, f"10 panels and 30 buttons rendered ({res})")
        check(res["same"], "an unchanged button keeps its DOM node across renders")
        check(res["open"] < 3000, f"the canvas with 10 panels opens fast ({res['open']:.0f} ms)")
        spill = await page.evaluate("""() => { const res = []; document.querySelectorAll('.qcv-item .mp-card').forEach(card => { const cr = card.getBoundingClientRect();
            card.querySelectorAll('*').forEach(e => { const r = e.getBoundingClientRect(); if (r.width && r.right > cr.right + 1) res.push(card.dataset.kind + ' ' + e.tagName + '.' + String(e.className)); }); });
            return res.slice(0, 6); }""")
        check(not spill, f"nothing spills out of a 2-column panel (the S To-Do input did) {spill}")
        await shot(page, "hc-perf-dark-ru")
        await page.close()

        ask = post("/api/quick/canvas/actions", {"action": {"label": "ask", "steps": [{"type": "ask", "mode": "window", "prompt": "Переведи {clipboard}"}]}})["action"]["id"]
        page = await open_page()
        await page.evaluate("""() => { const Q = window.MemuQuick, orig = Q.state; window.__sent = []; window.__busy = true;
            Q.state = () => ({ ...orig(), busy: window.__busy }); Q.send = (text, o) => { window.__sent.push([text, !!(o && o.direct)]); }; }""")
        await page.evaluate(f"MemuQuickCanvas.runAction('{ask}', true)")
        await page.wait_for_timeout(900)
        check(await page.evaluate("window.__sent.length") == 0, "the button's AI request waits while another answer streams")
        await page.evaluate("window.__busy = false; MemuQuick.emit('answer-done', { text: 'previous answer' })")
        await page.wait_for_timeout(300)
        sent = await page.evaluate("window.__sent")
        check(sent == [["Переведи буфер", True]], f"…and is sent after it, straight to the model ({sent})")
        await page.evaluate("MemuQuick.emit('answer-done', { text: 'button answer' })")
        await page.wait_for_timeout(900)
        out_run = await page.evaluate(f"(MemuQuickCanvas.state().runs.get('{ask}') || {{}}).output || ''")
        check(out_run == "button answer", f"the run takes the button's answer, not the previous one ({out_run!r})")
        await page.close()

        for lang, theme in (("ru", "dark"), ("en", "light")):
            page = await open_page(lang=lang, theme=theme)
            await page.evaluate("""MemuQuickCanvas.openEditor(null, {label: 'Telegram', icon: {kind: 'builtin', value: 'bolt'}, color: '#3b82f6', color2: '', shape: 'circle',
                hotkey: 'Ctrl+Alt+T', global: true, steps: [{type: 'open', target: 'app', value: 'Telegram'}]})""")
            await page.wait_for_timeout(700)
            await page.click('[data-icon-tab="image"]')
            await page.wait_for_timeout(300)
            check(await page.evaluate("document.querySelector('[data-f=\"appicon\"]').value") == "Telegram", "the app icon field is prefilled from the open-app step")
            check(await page.evaluate("document.querySelector('[data-f=\"global\"]').checked"), "the global checkbox reflects the button")
            await page.click('[data-qcv="app-icon"]')
            await page.wait_for_timeout(600)
            err = await page.evaluate("(document.querySelector('.qcv-error') || {}).textContent || ''")
            check("Windows" in err, f"outside Windows the app icon explains itself ({err})")
            await shot(page, f"hc-editor-{theme}-{lang}")
            await page.close()
        await browser.close()
    server.should_exit = True
    print("problems:", problems)
    return 1 if problems else 0

if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r27/r27-hunt-canvas")
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="hc-shots-")
    sys.exit(asyncio.run(main(out_dir)))
