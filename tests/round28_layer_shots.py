"""Round 28: headless Chromium checks + screenshots of the Quick Ask screen layer (run by tests/round28_layer_ui_test.py).

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round28_layer_shots.py OUT_DIR

A real backend (app.quick_canvas incl. /api/quick/layer/*, the MemuAI panel routes over a temporary profile) serves
/quick/layer.html and the Quick Ask window; the native side is absent, so the page↔page messages go over the
BroadcastChannel fallback of quick-bus.js and the native calls (region, layer commands) are recorded by a route.
Checks (exit code 1 on any problem):
  * a Round 27 grid file is migrated: every panel and button appears on the screen, the grid's shape kept;
  * items never overlap, never leave the work area, never sit under the Quick Ask window (hard) nor in the room its
    answer grows into while there is space elsewhere — on 1920×1040, 1366×728 and a 150 % monitor (1280×696 CSS px);
  * the page reports the click-through region = the items' rounded boxes; edit mode / a modal → the whole area;
  * Ctrl+E arranges: a drag moves an item anywhere on the screen, snaps, never onto another item, and is saved for
    THIS monitor (a smaller monitor adapts, the first one keeps its exact layout);
  * a click on a layer button is relayed to the Quick Ask window and runs there — the first run of a risky button
    shows the approval sheet in the window and runs NOTHING before it; a click in the first 300 ms runs nothing;
  * hiding the layer removes the cards and pauses ALL panel polling;
  * the Quick Ask window in screen mode: Ctrl+B toggles the layer (native call), the body shows the screen summary;
  * ru/en, dark/light, reduced motion. Screens ql-*.png in OUT_DIR (look at every one).
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

WALLPAPER = ("document.body.style.background = 'linear-gradient(135deg, #2b4c7e 0%, #567ebb 40%, #c38d9e 75%, #e8a87c 100%)';"
             "document.documentElement.style.background = '#2b4c7e';")


def overlaps(a, b, pad=0.0):
    return a["x"] < b["x"] + b["w"] + pad - 0.5 and b["x"] < a["x"] + a["w"] + pad - 0.5 and a["y"] < b["y"] + b["h"] + pad - 0.5 and b["y"] < a["y"] + a["h"] + pad - 0.5


async def main(out: Path) -> int:
    import httpx
    import uvicorn
    from playwright.async_api import async_playwright
    import round27_quick_canvas_shots as base
    from app import quick_canvas, themes
    out.mkdir(parents=True, exist_ok=True)
    seeded = base.seed_panels()
    # a Round 27 file: a grid with panels and buttons (one of them risky), written as Round 27 wrote it
    port = base.free_port()
    server = uvicorn.Server(uvicorn.Config(base.make_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    B = f"http://127.0.0.1:{port}"
    problems: list = []
    native: list = []
    prefs: dict = {}
    layer_state = {"visible": True, "edit": False}

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def post(path, body):
        return httpx.post(B + path, json=body, timeout=20).json()

    # ---- seed: build the layout through the Round 27 API, then strip the screen layer → a Round 27 file
    for key, size in (("weather", "m"), ("todo", "l"), ("clock", "s")):
        post("/api/quick/canvas/panels", {"panel": seeded[key], "size": size})
    tpls = httpx.get(B + "/api/quick/canvas/catalog?lang=ru", timeout=20).json()["templates"]
    for tp in tpls[:5]:
        post("/api/quick/canvas/actions", {"action": {k: v for k, v in tp.items() if k != "key"}})
    risky = post("/api/quick/canvas/actions", {"action": {"label": "Закрыть Блокнот", "icon": {"kind": "emoji", "value": "🧹"}, "color": "#64748b",
                                                          "steps": [{"type": "command", "action": "app.close", "params": {"name": "notepad"}}]}})["action"]
    path = quick_canvas._path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("layer", None)
    raw["version"] = 1
    raw["settings"].pop("screen", None)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    n_items = len(raw["layout"]["items"])

    async def route(r, req):
        url = req.url
        p = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if p == "/themes.css":
            return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if p == "/api/quick/config":
            return await r.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                         "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if p == "/api/pg/state":
            return await r.fulfill(json={"models": base.MODELS, "image_models": []})
        if p == "/api/quick/layer/region":
            native.append(("region", json.loads(req.post_data or "{}")))
            return await r.fulfill(json={"ok": True})
        if p == "/api/quick/layer":
            body = json.loads(req.post_data or "{}")
            native.append(("layer", body))
            if body.get("action") == "toggle":
                layer_state["visible"] = not layer_state["visible"]
            if body.get("action") == "edit":
                layer_state["edit"] = bool(body.get("on"))
            return await r.fulfill(json={"ok": True, "available": True, **layer_state})
        if p == "/api/quick/window":
            native.append(("window", json.loads(req.post_data or "{}")))
            return await r.fulfill(json={"ok": True})
        if p == "/api/quick/canvas" and req.method == "GET":
            # the Windows app reports the layer as available → the window runs in screen mode
            resp = await r.fetch()
            data = await resp.json()
            data["layer"] = {**data.get("layer", {}), "available": True, "visible": layer_state["visible"]}
            return await r.fulfill(json=data)
        if p.startswith("/api/quick/canvas") or p.startswith("/api/quick/layer") or p.startswith("/api/pg/panels"):
            return await r.continue_()
        if p == "/api/assistant/state":
            return await r.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if p.startswith("/api/"):
            return await r.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await r.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=base.CHROME if os.path.exists(base.CHROME) else None, args=["--no-sandbox"])

        async def new_ctx(w, h, lang="ru", theme="dark", reduced=False):
            prefs.clear()
            prefs.update({"theme": theme, "accent_color": "violet", "ui_design": "classic", "language": lang, "quick_ask_model": "deepseek-v4.1-flash"})
            ctx = await browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce" if reduced else "no-preference")
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
            await ctx.route("**/*", route)
            return ctx

        async def open_layer(ctx, monitor, keepout, scale=1):
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"layer pageerror: {e}"))
            await page.goto(B + "/quick/layer.html?native=1", wait_until="load")
            await page.wait_for_timeout(300)
            await page.evaluate("(p) => MemuLayer.onShow(p)", {"monitor": {**monitor, "scale": scale}, "keepout": keepout, "glass": True, "fresh": True})
            await page.wait_for_timeout(1300)
            return page

        async def layout(page):
            return await page.evaluate("""() => Array.from(document.querySelectorAll('.ql-item')).map((n) => { const r = n.getBoundingClientRect();
                return { id: n.dataset.item, type: n.dataset.type, x: r.left, y: r.top, w: r.width, h: r.height }; })""")

        def check_layout(rows, monitor, keepout, what, soft=True):
            check(rows, f"{what}: items on the screen")
            for i, a in enumerate(rows):
                if a["x"] < -0.5 or a["y"] < -0.5 or a["x"] + a["w"] > monitor["w"] + 0.5 or a["y"] + a["h"] > monitor["h"] + 0.5:
                    check(False, f"{what}: {a['id']} leaves the work area {a}")
                hard = dict(zip("xywh", keepout["hard"]))
                check(not overlaps(a, hard), f"{what}: {a['id']} is under the Quick Ask window")
                if soft:
                    zone = dict(zip("xywh", keepout["soft"]))
                    check(not overlaps(a, zone), f"{what}: {a['id']} is in the room the answer grows into")
                for b in rows[i + 1:]:
                    check(not overlaps(a, b), f"{what}: {a['id']} overlaps {b['id']}")

        async def shot(page, name):
            await page.evaluate(WALLPAPER)
            await page.wait_for_timeout(350)
            await page.screenshot(path=str(out / f"{name}.png"))

        # ---------------------------------------------------------------- 1. 1920×1040, migrated Round 27 grid
        mon = {"key": "1920x1080@96", "w": 1920, "h": 1040}
        keep = {"hard": [600, 229, 720, 136], "soft": [600, 229, 720, 640]}
        ctx = await new_ctx(1920, 1040)
        layer = await open_layer(ctx, mon, keep)
        rows = await layout(layer)
        check(len(rows) == n_items, f"every Round 27 item is on the screen ({len(rows)}/{n_items})")
        check(await layer.evaluate("document.querySelectorAll('.ql-item .mp-card').length") == 3, "the three MemuAI panels are drawn by panels.js")
        check_layout(rows, mon, keep, "1920×1040")
        check(all(r["x"] > 960 for r in rows), "the migrated grid sits on the right, away from the centred window")
        regions = [b for k, b in native if k == "region"]
        check(regions and not regions[-1]["full"] and len(regions[-1]["rects"]) == n_items, f"the click-through region = the items ({regions[-1] if regions else None})")
        state = httpx.get(B + "/api/quick/canvas").json()
        check(state["settings"]["screen"] == "show" and state["layer"]["migrated"] == "r27-grid", f"migrated settings/layer ({state['settings'].get('screen')}, {state['layer']})")
        await shot(layer, "ql-1920-dark-ru")

        # 300 ms guard + relay to the Quick Ask window (same browser profile → BroadcastChannel)
        quick = await ctx.new_page()
        quick.on("pageerror", lambda e: problems.append(f"quick pageerror: {e}"))
        await quick.set_viewport_size({"width": 720, "height": 520})
        await quick.goto(B + "/quick/?native=1", wait_until="load")
        await quick.wait_for_timeout(700)
        await quick.evaluate("QuickAsk.onShow(" + json.dumps({"fresh": True, "maxHeight": 760, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False, "source": "hotkey", "page": None, "selection": ""}) + ")")
        await quick.wait_for_timeout(900)
        check(await quick.evaluate("MemuQuickCanvas.state().screen === true"), "the window runs in screen mode when the layer is available")
        check(await quick.evaluate("!document.querySelector('.qcv-grid')"), "no in-window grid in screen mode")
        base.CALLS.clear()
        early = await layer.evaluate("""async ([p, id]) => { MemuLayer.onHide(); const t0 = performance.now(); MemuLayer.onShow(p);
            let node = null; while (!(node = document.querySelector('[data-run="' + id + '"]')) && performance.now() - t0 < 2000) await new Promise((r) => setTimeout(r, 5));
            if (node) node.click(); return performance.now() - t0; }""", [{"monitor": mon, "keepout": keep, "glass": True, "fresh": True}, risky["id"]])
        print("early click after", round(early), "ms")
        btn = layer.locator(f'[data-run="{risky["id"]}"]')
        await quick.wait_for_timeout(500)
        check(await quick.evaluate("MemuQuickCanvas.state().view") != "approve", "a click in the first 300 ms after the layer appeared runs nothing")
        await layer.wait_for_timeout(500)
        await btn.click()
        await quick.wait_for_timeout(900)
        check(await quick.evaluate("MemuQuickCanvas.state().view === 'approve'"), "a click on a risky layer button shows the approval sheet in the window")
        check(not base.CALLS, f"nothing ran before the approval ({base.CALLS})")
        await quick.screenshot(path=str(out / "ql-quick-approve-dark-ru.png"))
        await quick.click('[data-qcv="approve"]')
        await quick.wait_for_timeout(1200)
        check(any(c[0] == "app.close" for c in base.CALLS), f"after the approval the server ran the step ({base.CALLS})")
        rings = await layer.evaluate("document.querySelectorAll('.ql-ring').length")
        print("rings seen on the layer:", rings)

        # the window in screen mode: Ctrl+B → the native toggle; the summary body
        native.clear()
        await quick.evaluate("MemuQuickCanvas.setOpen(false, {save: false}); document.getElementById('qa-input').focus()")
        await quick.keyboard.press("Control+b")
        await quick.wait_for_timeout(400)
        check(("layer", {"action": "toggle"}) in [(k, {kk: vv for kk, vv in b.items() if kk == "action"}) for k, b in native], f"Ctrl+B toggles the screen layer ({native})")
        await quick.keyboard.press("Control+b")
        await quick.wait_for_timeout(300)
        await quick.evaluate("MemuQuickCanvas.setOpen(true, {save: false})")
        await quick.wait_for_timeout(400)
        dbg = await quick.evaluate("(() => { const S = MemuQuickCanvas.state(); return {open: S.open, view: S.view, screen: S.screen, items: MemuQuickCanvas.visibleItems().length, body: (document.querySelector('.qcv-body')||{}).hidden, html: (document.querySelector('.qcv-view')||{}).innerHTML ? document.querySelector('.qcv-view').innerHTML.slice(0, 200) : ''}; })()")
        check(await quick.evaluate("!!document.querySelector('.qcv-screen')"), f"the window body shows the screen summary ({dbg})")
        h = await quick.evaluate("QuickAsk.render()")
        await quick.set_viewport_size({"width": 720, "height": max(120, min(900, int(h)))})
        await quick.wait_for_timeout(300)
        await quick.screenshot(path=str(out / "ql-quick-screen-dark-ru.png"))
        await quick.keyboard.press("Control+e")
        await quick.wait_for_timeout(300)
        check(any(k == "layer" and b.get("action") == "edit" and b.get("on") is True for k, b in native), "Ctrl+E in the window starts arranging the layer")

        # ---------------------------------------------------------------- 2. arrange: edit mode, drag anywhere, snap, no overlaps, saved
        native.clear()
        await layer.bring_to_front()
        await layer.evaluate("MemuLayer.setEdit(true)")
        await layer.wait_for_timeout(400)
        check(await layer.evaluate("document.getElementById('ql').dataset.edit === '1'"), "edit mode on the layer")
        regions = [b for k, b in native if k == "region"]
        check(regions and regions[-1]["full"], "edit mode: the whole area takes the mouse")
        await shot(layer, "ql-edit-dark-ru")
        rows = await layout(layer)
        btn_row = next(r for r in rows if r["type"] == "action")
        await layer.mouse.move(btn_row["x"] + btn_row["w"] / 2, btn_row["y"] + btn_row["h"] / 2)
        await layer.mouse.down()
        target = (60 + 3 + btn_row["w"] / 2, 1040 - 60 - btn_row["h"] / 2)      # near the bottom-left corner
        await layer.mouse.move(*target, steps=14)
        await layer.wait_for_timeout(120)
        await layer.screenshot(path=str(out / "ql-dragging-dark-ru.png"))
        await layer.mouse.up()
        await layer.wait_for_timeout(900)
        rows2 = await layout(layer)
        moved = next(r for r in rows2 if r["id"] == btn_row["id"])
        check(moved["x"] < 120 and moved["y"] > 1040 - 200, f"the button went to the bottom-left of the SCREEN ({moved})")
        check_layout(rows2, mon, keep, "after a drag", soft=False)
        doc = quick_canvas.load()
        saved = doc["layer"]["monitors"].get("1920x1080@96", {}).get("items", {})
        check(btn_row["id"] in saved and saved[btn_row["id"]]["ax"] == "l" and saved[btn_row["id"]]["ay"] == "b", f"saved for this monitor, anchored bottom-left ({saved.get(btn_row['id'])})")
        # drop onto a panel → the nearest free spot, never on top
        panel_row = next(r for r in rows2 if r["type"] == "panel")
        other = next(r for r in rows2 if r["type"] == "action" and r["id"] != btn_row["id"])
        await layer.mouse.move(other["x"] + other["w"] / 2, other["y"] + other["h"] / 2)
        await layer.mouse.down()
        await layer.mouse.move(panel_row["x"] + panel_row["w"] / 2, panel_row["y"] + panel_row["h"] / 2, steps=10)
        await layer.mouse.up()
        await layer.wait_for_timeout(900)
        check_layout(await layout(layer), mon, keep, "dropped onto a panel", soft=False)
        # resize a panel with its corner
        prow = next(r for r in await layout(layer) if r["type"] == "panel")
        await layer.mouse.move(prow["x"] + prow["w"] - 8, prow["y"] + prow["h"] - 8)
        await layer.mouse.down()
        await layer.mouse.move(prow["x"] + prow["w"] + 170, prow["y"] + prow["h"] + 10, steps=8)
        await layer.mouse.up()
        await layer.wait_for_timeout(900)
        grown = next(r for r in await layout(layer) if r["id"] == prow["id"])
        check(grown["w"] > prow["w"] + 60, f"the corner resizes a panel ({prow['w']} → {grown['w']})")
        check_layout(await layout(layer), mon, keep, "after a resize", soft=False)
        await layer.keyboard.press("Escape")
        await layer.wait_for_timeout(300)
        check(await layer.evaluate("document.getElementById('ql').dataset.edit === '0'"), "Esc finishes arranging")
        check(any(k == "layer" and b.get("action") == "edit" and b.get("on") is False for k, b in native), "…and tells the native side")
        await layer.keyboard.press("Escape")
        await layer.wait_for_timeout(200)
        check(any(k == "window" and b.get("action") == "hide" for k, b in native), "Esc again closes Quick Ask (with the layer)")

        # ---------------------------------------------------------------- 3. hide: no cards, no polling
        await layer.evaluate("MemuLayer.onHide()")
        await layer.wait_for_timeout(200)
        calls = []
        layer.on("request", lambda rq: calls.append(rq.url) if "/api/pg/" in rq.url else None)
        await layer.evaluate("MemuPanels.load()")          # whatever was scheduled
        await layer.wait_for_timeout(300)
        calls.clear()
        await layer.wait_for_timeout(3500)
        check(await layer.evaluate("document.querySelectorAll('.ql-item').length") == 0, "a hidden layer has no cards")
        check(await layer.evaluate("MemuPanels.state.paused === true"), "panels.js is paused while the layer is hidden")
        check(not calls, f"no panel polling while hidden ({calls[:4]})")
        await ctx.close()

        # ---------------------------------------------------------------- 4. smaller monitor + 150 % monitor adapt
        for (w, h, key, scale, name, lang, theme) in ((1366, 728, "1366x768@96", 1, "ql-1366-light-en", "en", "light"),
                                                      (1280, 696, "1920x1080@144", 1.5, "ql-150pct-dark-ru", "ru", "dark")):
            keep2 = {"hard": [round((w - 640) / 2), 160, 640, 120], "soft": [round((w - 640) / 2), 160, 640, h - 176]}
            ctx = await new_ctx(w, h, lang=lang, theme=theme)
            page = await open_layer(ctx, {"key": key, "w": w, "h": h}, keep2, scale)
            rows = await layout(page)
            check(len(rows) >= n_items - 1, f"{key}: items shown ({len(rows)}/{n_items})")
            check_layout(rows, {"w": w, "h": h}, keep2, key, soft=False)
            hidden = await page.evaluate("MemuLayer.state().hiddenCount")
            print(key, "hidden:", hidden)
            await shot(page, name)
            await ctx.close()
        doc = quick_canvas.load()
        check("1366x768@96" not in doc["layer"]["monitors"], "only showing on a monitor saves nothing for it (adapted from the default)")
        check("1920x1080@96" in doc["layer"]["monitors"], "the arranged monitor keeps its own layout")

        # ---------------------------------------------------------------- 5. reduced motion: no wiggle
        ctx = await new_ctx(1920, 1040, reduced=True)
        page = await open_layer(ctx, mon, keep)
        await page.evaluate("MemuLayer.setEdit(true)")
        await page.wait_for_timeout(300)
        anim = await page.evaluate("getComputedStyle(document.querySelector('.ql-item')).animationName")
        check(anim == "none", f"reduced motion: no wiggle ({anim})")
        await ctx.close()
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/shots/r28/layer")
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="ql-shots-")
    sys.exit(asyncio.run(main(out_dir)))
