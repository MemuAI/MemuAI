"""Round 29 (r29-panels): headless Chromium checks + screenshots of the panel fixes (run by tests/round29_panels_ui_test.py).

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round29_panels_shots.py OUT_DIR

A real backend (app.quick_canvas incl. /api/quick/layer/*, the MemuAI panel routes over a temporary profile) serves
/quick/layer.html; the native calls (region, layer commands) are recorded by a route. Checks (exit code 1 on any
problem):
  * CLIPPING: every panel card on the layer fits its box (no overflow) — the M weather card that was cut in the
    user's screenshot, at 1920×1040, 1366×728 and a 150 % monitor, ru/en; with 160 % text (Windows text scaling) the
    cards still fit (compact levels + zoom ≥ 60 %) or are explicitly marked clipped; the click-through region equals
    the items' LAYOUT boxes (not the shrunken boxes of the first animation frame) and carries the real viewport;
  * MENU: a right click opens one menu (memu-ctxmenu) — also when the card re-rendered between press and release;
    while it is open the whole area takes the mouse (a click anywhere closes it and does nothing else); Esc closes
    it; it stays inside the screen near the right / bottom edges; «Size → S» resizes; ⋯ toggles it;
  * DRAG: holding the left button and moving drags an item WITHOUT arrange mode, snaps with guide lines, saves for
    this monitor; a press that moves < 5 px is still a click (a button click is relayed to Quick Ask as «run»);
    Ctrl+Z / Ctrl+Shift+Z undo / redo; Alt+arrows move; Ctrl+arrows resize; the corner grip resizes without arrange mode;
  * the MemuAI window (panels.js + memu-ctxmenu.js): right click → the shared menu, also on a card re-rendered under
    the pointer; outside press / Esc close it; places toggle keeps it open; the chat's own menu does not open too.
Screens r29-*.png in OUT_DIR.
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

FIT_JS = """() => Array.from(document.querySelectorAll('.ql-item[data-type=panel]')).map((n) => {
  const c = n.querySelector('.ql-body > .mp-card'); if (!c) return { id: n.dataset.item, card: false };
  return { id: n.dataset.item, kind: c.dataset.kind, size: n.dataset.size, fit: c.dataset.fit, zoom: +(c.style.zoom || 1), clipped: c.dataset.clipped,
    over: c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1,
    box: [n.offsetLeft, n.offsetTop, n.offsetWidth, n.offsetHeight], rect: (() => { const r = c.getBoundingClientRect(); return [r.width, r.height]; })() };
})"""


async def main(out: Path) -> int:
    import httpx
    import uvicorn
    from playwright.async_api import async_playwright
    import round27_quick_canvas_shots as base
    from app import themes
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
    native: list = []

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def post(path, body):
        return httpx.post(B + path, json=body, timeout=20).json()

    for key, size in (("weather", "m"), ("todo", "l"), ("clock", "s"), ("notes", "m")):
        post("/api/quick/canvas/panels", {"panel": seeded[key], "size": size})
    tpls = httpx.get(B + "/api/quick/canvas/catalog?lang=ru", timeout=20).json()["templates"]
    for tp in tpls[:3]:
        post("/api/quick/canvas/actions", {"action": {k: v for k, v in tp.items() if k != "key"}})

    async def route(r, req):
        p = "/" + req.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if p == "/themes.css":
            return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if p == "/api/quick/layer/region":
            native.append(("region", json.loads(req.post_data or "{}")))
            return await r.fulfill(json={"ok": True})
        if p in ("/api/quick/layer", "/api/quick/window"):
            native.append((p, json.loads(req.post_data or "{}")))
            return await r.fulfill(json={"ok": True})
        if p.startswith("/api/quick/") or p.startswith("/api/pg/panels"):
            return await r.continue_()
        if p.startswith("/api/"):
            return await r.fulfill(json={"items": [], "ok": True})
        return await r.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=base.CHROME if os.path.exists(base.CHROME) else None, args=["--no-sandbox"])

        async def open_layer(w, h, key, scale=1, lang="ru", theme="dark", glass=True):
            # r29t: a real device pixel ratio (the 150 % monitor case used to pass only the nominal scale); the menu /
            # drag / resize sections can be re-run at 125 / 150 % with MEMU_LAYER_DPR=1.25 / 1.5.
            dpr = scale if scale != 1 else float(os.environ.get("MEMU_LAYER_DPR") or 1)
            ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr)
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
            await ctx.route("**/*", route)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"layer pageerror: {e}"))
            await page.goto(B + "/quick/layer.html?native=1", wait_until="load")
            await page.wait_for_timeout(250)
            keep = {"hard": [round((w - 640) / 2), 160, 640, 120], "soft": [round((w - 640) / 2), 160, 640, h - 176]}
            await page.evaluate("(p) => MemuLayer.onShow(p)", {"monitor": {"key": key, "w": w, "h": h, "scale": scale}, "keepout": keep, "glass": glass, "fresh": True})
            await page.wait_for_timeout(1500)
            await page.evaluate(WALLPAPER)
            # record what the layer sends to Quick Ask
            await page.evaluate("() => { window.__sent = []; const s = MemuQuickBus.send; MemuQuickBus.send = (to, ev, d) => { window.__sent.push([to, ev, d]); return s(to, ev, d); }; }")
            return ctx, page

        def regions():
            return [b for k, b in native if k == "region"]

        # ------------------------------------------------------------ 1. every card fits its box
        for (w, h, key, scale, lang, theme, name) in ((1920, 1040, "1920x1080@96", 1, "ru", "dark", "r29-layer-1920-ru"),
                                                     (1366, 728, "1366x768@96", 1, "en", "light", "r29-layer-1366-en"),
                                                     (1280, 696, "1920x1080@144", 1.5, "ru", "dark", "r29-layer-150pct-ru")):
            native.clear()
            ctx, page = await open_layer(w, h, key, scale, lang, theme)
            fits = await page.evaluate(FIT_JS)
            check(len(fits) >= 3, f"{key}: panels drawn ({len(fits)})")
            for f in fits:
                check(f.get("card") is not False and not f["over"], f"{key}: {f.get('kind')} card fits its box ({f})")
            wx = next((f for f in fits if f.get("kind") == "weather"), None)
            check(wx and wx["zoom"] == 1 and wx["clipped"] == "0", f"{key}: the M weather card fits without shrinking ({wx})")
            await page.wait_for_timeout(500)
            rg = regions()
            boxes = await page.evaluate("() => Array.from(document.querySelectorAll('.ql-item')).map((n) => [n.offsetLeft, n.offsetTop, n.offsetWidth, n.offsetHeight])")
            last = rg[-1] if rg else {}
            check(rg and not last.get("full") and len(last.get("rects", [])) == len(boxes), f"{key}: region = the items ({last})")
            check(last.get("view", {}).get("w") == w, f"{key}: the region carries the real viewport ({last.get('view')})")
            for bx in boxes:
                hit = any(r[0] <= bx[0] and r[1] <= bx[1] and r[0] + r[2] >= bx[0] + bx[2] and r[1] + r[3] >= bx[1] + bx[3] for r in last.get("rects", []))
                check(hit, f"{key}: the region covers the whole item {bx}")
            await page.screenshot(path=str(out / f"{name}.png"))
            await ctx.close()

        # ------------------------------------------------------------ 2. bigger text: still fits (compact + zoom)
        native.clear()
        ctx, page = await open_layer(1920, 1040, "1920x1080@96")
        await page.add_style_tag(content=".ql-body > .mp-card * { font-size: 160% !important; }")
        await page.evaluate("() => document.querySelectorAll('.ql-item[data-type=panel]').forEach((n) => MemuLayer.fitCard(n.dataset.item))")
        fits = await page.evaluate(FIT_JS)
        for f in fits:
            check(not f["over"] or f["clipped"] == "1", f"160 % text: {f.get('kind')} fits or says it is clipped ({f})")
            check(f["zoom"] >= 0.6, f"160 % text: zoom never below 60 % ({f})")
        check(any(f["zoom"] < 1 or (f["fit"] or "0") != "0" for f in fits), "160 % text: something was compacted")
        await page.evaluate("MemuLayer.setEdit(true)")
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(out / "r29-layer-bigtext-edit.png"))
        await page.evaluate("MemuLayer.setEdit(false)")
        await ctx.close()

        # ------------------------------------------------------------ 3. menu
        native.clear()
        ctx, page = await open_layer(1920, 1040, "1920x1080@96")
        items = await page.evaluate("() => MemuLayer.state().placed.filter((p) => !p.hidden).map((p) => ({ id: p.id, type: p.type, x: p.x, y: p.y, w: p.w, h: p.h, units: p.units }))")
        wx_id = await page.evaluate("() => { const c = document.querySelector('.ql-item .mp-card[data-kind=weather]'); return c.closest('.ql-item').dataset.item; }")
        wx = next(i for i in items if i["id"] == wx_id)
        # open the menu on the panel farthest from some button, so that button stays clear of the menu
        far_p, far_b = max(((p, b) for p in items if p["type"] == "panel" for b in items if b["type"] == "action"),
                           key=lambda pb: abs(pb[0]["x"] - pb[1]["x"]) + abs(pb[0]["y"] - pb[1]["y"]))
        await page.mouse.click(far_p["x"] + far_p["w"] / 2, far_p["y"] + far_p["h"] / 2, button="right")
        await page.wait_for_timeout(250)
        check(await page.locator(".mcm").count() == 1, "right click → one menu")
        await page.wait_for_timeout(100)
        check(regions() and regions()[-1]["full"], "while the menu is open the whole area takes the mouse (a click anywhere closes it)")
        box = await page.evaluate("() => { const r = document.querySelector('.mcm').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
        check(box[0] >= 0 and box[1] >= 0 and box[2] <= 1920 and box[3] <= 1040, f"the menu stays on the screen {box}")
        await page.screenshot(path=str(out / "r29-layer-menu.png"))
        sent_before = len(await page.evaluate("window.__sent"))
        def clear_of(i, m):
            return i["x"] + i["w"] < m[0] or i["x"] > m[2] or i["y"] + i["h"] < m[1] or i["y"] > m[3]
        btn = far_b if clear_of(far_b, box) else next((i for i in items if i["type"] == "action" and clear_of(i, box)), None)
        check(btn is not None, "a button outside the menu to click")
        btn = btn or {"x": 20, "y": 20, "w": 10, "h": 10}
        await page.mouse.click(btn["x"] + btn["w"] / 2, btn["y"] + btn["h"] / 2)        # the click that closes the menu
        await page.wait_for_timeout(250)
        check(await page.locator(".mcm").count() == 0, "a click elsewhere closes the menu")
        sent = await page.evaluate("window.__sent")
        check(not any(ev == "run" for _, ev, _ in sent[sent_before:]), "…and that click does nothing else (no button run)")
        await page.wait_for_timeout(150)
        check(regions() and not regions()[-1]["full"], "the region shrinks back to the items")
        # Esc
        await page.mouse.click(wx["x"] + 40, wx["y"] + 40, button="right")
        await page.wait_for_timeout(200)
        check(await page.locator(".mcm").count() == 1, "a right click opens the menu again")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        check(await page.locator(".mcm").count() == 0, "Esc closes the menu")
        check(not any(k == "/api/quick/window" and b.get("action") == "hide" for k, b in native), "…and only the menu (Quick Ask stays)")
        # right click on a card that re-renders between press and release (a live card)
        await page.evaluate("""(id) => { const n = document.querySelector('.ql-item[data-item="' + id + '"]');
            n.addEventListener('pointerdown', () => { const b = n.querySelector('.ql-body'); b.innerHTML = b.innerHTML; }, { once: true }); }""", wx_id)
        await page.mouse.click(wx["x"] + 60, wx["y"] + 60, button="right")
        await page.wait_for_timeout(200)
        check(await page.locator(".mcm").count() == 1, "a right click on a card re-rendered under the pointer still opens the menu")
        # near the bottom-right corner the menu flips inside the screen
        await page.keyboard.press("Escape")
        corner = max(items, key=lambda i: i["x"] + i["y"])
        await page.mouse.click(corner["x"] + corner["w"] - 14, corner["y"] + corner["h"] - 14, button="right")
        await page.wait_for_timeout(200)
        box = await page.evaluate("() => { const m = document.querySelector('.mcm'); if (!m) return null; const r = m.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
        check(box and box[2] <= 1920 and box[3] <= 1040, f"a menu at the corner stays on the screen {box}")
        await page.keyboard.press("Escape")
        # ⋯ toggles; Size → S resizes
        await page.mouse.move(wx["x"] + wx["w"] / 2, wx["y"] + wx["h"] / 2)
        await page.wait_for_timeout(200)
        more = page.locator(f'.ql-item[data-item="{wx_id}"] .ql-more')
        await more.click()
        await page.wait_for_timeout(200)
        check(await page.locator(".mcm").count() == 1, "⋯ opens the menu")
        await page.locator('.mcm [data-mcm-opt="s"]').click()
        await page.wait_for_timeout(700)
        units = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{wx_id}').units")
        check(units == [2, 2], f"Size → S resizes the panel ({units})")
        check(await page.locator(".mcm").count() == 0, "the menu closes after the action")
        await page.keyboard.press("Control+z")
        await page.wait_for_timeout(700)
        units = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{wx_id}').units")
        check(units == [4, 2], f"Ctrl+Z undoes the resize ({units})")
        await ctx.close()

        # ------------------------------------------------------------ 4. drag without arrange mode, click stays a click
        native.clear()
        ctx, page = await open_layer(1920, 1040, "1920x1080@96")
        items = await page.evaluate("() => MemuLayer.state().placed.filter((p) => !p.hidden).map((p) => ({ id: p.id, type: p.type, x: p.x, y: p.y, w: p.w, h: p.h }))")
        btn = next(i for i in items if i["type"] == "action")
        await page.wait_for_timeout(350)                    # past the 300 ms guard
        await page.mouse.move(btn["x"] + btn["w"] / 2, btn["y"] + btn["h"] / 2)
        await page.mouse.down()
        await page.mouse.move(btn["x"] + btn["w"] / 2 + 2, btn["y"] + btn["h"] / 2 + 1)
        await page.mouse.up()
        await page.wait_for_timeout(200)
        sent = await page.evaluate("window.__sent")
        check(any(ev == "run" for _, ev, _ in sent), f"a press that moved 2 px is still a click → «run» relayed to Quick Ask ({sent})")
        pan = next(i for i in items if i["type"] == "panel")
        await page.mouse.move(pan["x"] + pan["w"] / 2, pan["y"] + 30)
        await page.mouse.down()
        await page.mouse.move(pan["x"] + pan["w"] / 2 - 40, pan["y"] + 60, steps=3)
        await page.mouse.move(100 + pan["w"] / 2, 1040 - 30 - pan["h"] + 30, steps=10)
        await page.wait_for_timeout(100)
        st = await page.evaluate("() => ({ drag: !!MemuLayer.state().drag, edit: MemuLayer.state().edit, guides: document.querySelectorAll('.ql-guide').length })")
        check(st["drag"] and not st["edit"], f"holding the left button drags at once, no arrange mode ({st})")
        check(regions() and regions()[-1]["full"], "while dragging the whole area takes the mouse")
        await page.screenshot(path=str(out / "r29-layer-drag.png"))
        await page.mouse.up()
        await page.wait_for_timeout(900)
        moved = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}')")
        check(moved["x"] < 400 and moved["y"] > 1040 - 420, f"the panel went to the bottom-left ({moved['x']}, {moved['y']})")
        check(not await page.evaluate("MemuLayer.state().edit"), "still not in arrange mode")
        doc = json.loads(httpx.get(B + "/api/quick/canvas", timeout=20).text)
        check(doc["layer"]["monitors"] >= 1, "the arrangement is saved for this monitor")
        # snapping + guides: drag the button next to the panel's top edge
        st2 = await page.evaluate(f"""() => {{ const S = MemuLayer.state(); const p = S.placed.find((i) => i.id === '{pan['id']}');
            const r = MemuLayer.snapXY(p.x + p.w + 8 + 4, p.y + 3, 72, 72, [p], S.monitor); return r; }}""")
        check(st2["gx"] and st2["gy"], f"snapXY returns guide lines ({st2})")
        # undo / redo of the drag
        await page.locator(f'.ql-item[data-item="{pan["id"]}"]').focus()
        await page.keyboard.press("Control+z")
        await page.wait_for_timeout(800)
        back = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}')")
        check(abs(back["x"] - pan["x"]) < 1 and abs(back["y"] - pan["y"]) < 1, f"Ctrl+Z puts it back ({back['x']}, {back['y']} vs {pan['x']}, {pan['y']})")
        await page.keyboard.press("Control+Shift+z")
        await page.wait_for_timeout(800)
        again = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}')")
        check(abs(again["x"] - moved["x"]) < 1, "Ctrl+Shift+Z redoes it")
        # keyboard: Alt+arrow moves, Ctrl+arrow resizes
        await page.locator(f'.ql-item[data-item="{pan["id"]}"]').focus()
        x0 = again["x"]
        await page.keyboard.press("Alt+ArrowRight")
        await page.wait_for_timeout(700)
        x1 = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}').x")
        check(abs(x1 - x0 - 8) < 1, f"Alt+→ moves by 8 px ({x0} → {x1})")
        u0 = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}').units")
        await page.keyboard.press("Control+ArrowDown")
        await page.wait_for_timeout(700)
        u1 = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}').units")
        check(u1[1] == u0[1] + 1 or u1[1] == 8, f"Ctrl+↓ makes it a unit taller ({u0} → {u1})")
        # the corner grip resizes without arrange mode
        cur = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}')")
        await page.mouse.move(cur["x"] + cur["w"] / 2, cur["y"] + cur["h"] / 2)
        await page.wait_for_timeout(150)
        await page.mouse.move(cur["x"] + cur["w"] - 8, cur["y"] + cur["h"] - 8)
        await page.mouse.down()
        await page.mouse.move(cur["x"] + cur["w"] + 90, cur["y"] + cur["h"] - 8, steps=6)
        await page.mouse.up()
        await page.wait_for_timeout(800)
        grown = await page.evaluate(f"() => MemuLayer.state().placed.find((p) => p.id === '{pan['id']}')")
        check(grown["units"][0] > cur["units"][0], f"the corner grip resizes without arrange mode ({cur['units']} → {grown['units']})")
        await ctx.close()

        # ------------------------------------------------------------ 5. the MemuAI window (panels.js + memu-ctxmenu.js)
        import panels_browser_test as pbt
        fixture = pbt.FIXTURE.replace('<script src="/panels.js"></script>', '<script src="/memu-ctxmenu.js"></script><script src="/panels.js"></script>')
        fixture = fixture.replace("</body>", """<script>window.__chatMenu = 0; document.addEventListener('contextmenu', () => { window.__chatMenu++; });</script></body>""")
        ctx = await browser.new_context(viewport={"width": 1300, "height": 860})
        page = await ctx.new_page()
        errors: list = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        STATIC = ROOT / "app" / "pgstatic"
        requests: list = []

        async def mroute(r, req):
            path = req.url[len(pbt.ORIGIN):].split("?")[0]
            if path in ("/", "/index.html"):
                return await r.fulfill(status=200, content_type="text/html", body=fixture)
            if path == "/api/pg/panels":
                return await r.fulfill(status=200, content_type="application/json", body=json.dumps(pbt.SNAPSHOT))
            if path.startswith("/api/pg/panels"):
                requests.append((req.method, path, req.post_data))
                return await r.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "settings": pbt.SNAPSHOT["settings"]}))
            file = STATIC / path.lstrip("/")
            if file.is_file():
                kind = "text/css" if file.suffix == ".css" else "application/javascript"
                return await r.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return await r.fulfill(status=404, body="")
        await page.route(f"{pbt.ORIGIN}/**", mroute)
        await page.goto(pbt.ORIGIN + "/")
        await page.wait_for_selector("#panels-home .mp-card", timeout=5000)
        card = page.locator('#panels-home .mp-card[data-panel-id="p_weather001"]')
        await card.click(button="right")
        await page.wait_for_timeout(200)
        check(await page.locator(".mcm").count() == 1, "MemuAI: right click → the shared menu")
        check(await page.evaluate("window.__chatMenu") == 0, "MemuAI: the chat's own context menu does not see the click")
        check(await page.locator(".mcm [data-mcm-i]").count() >= 6, "MemuAI: refresh, customize, places, size, remove")
        await page.screenshot(path=str(out / "r29-memuai-menu.png"))
        # places toggle keeps it open and patches
        await page.locator(".mcm .mcm-item", has_text="Боковая панель").click()
        await page.wait_for_timeout(250)
        check(await page.locator(".mcm").count() == 1, "MemuAI: toggling a place keeps the menu open")
        check(any(m == "PATCH" and "placements" in (d or "") for m, _, d in requests), f"MemuAI: the place is saved ({requests[-1:] if requests else None})")
        # outside press closes
        await page.mouse.click(5, 840)
        await page.wait_for_timeout(150)
        check(await page.locator(".mcm").count() == 0, "MemuAI: a press elsewhere closes the menu")
        # a card re-rendered between press and release
        await page.evaluate("""() => { const c = document.querySelector('#panels-home .mp-card[data-panel-id="p_alltypes01"]');
            c.addEventListener('pointerdown', () => { c.innerHTML = c.innerHTML; }, { once: true, capture: true }); }""")
        big = page.locator('#panels-home .mp-card[data-panel-id="p_alltypes01"] .mp-metric')
        await big.click(button="right")
        await page.wait_for_timeout(200)
        check(await page.locator(".mcm").count() == 1, "MemuAI: right click on a card re-rendered under the pointer opens the menu")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(120)
        check(await page.locator(".mcm").count() == 0, "MemuAI: Esc closes it")
        # ⋯ toggles
        await card.hover()
        await card.locator("[data-mp-menu]").click()
        await page.wait_for_timeout(150)
        check(await page.locator(".mcm").count() == 1, "MemuAI: ⋯ opens the menu")
        await card.locator("[data-mp-menu]").click()
        await page.wait_for_timeout(150)
        check(await page.locator(".mcm").count() == 0, "MemuAI: ⋯ again closes it (no reopen)")
        # keyboard
        await card.focus()
        await page.keyboard.press("Shift+F10")
        await page.wait_for_timeout(150)
        check(await page.evaluate("!!document.querySelector('.mcm .mcm-active')"), "MemuAI: Shift+F10 opens the menu with the first item active")
        await page.keyboard.press("Escape")
        check(not errors, f"MemuAI: no page errors ({errors[:3]})")
        await ctx.close()
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    if not os.environ.get("MEMUAI_DATA_DIR"):
        os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r29-panels-shots-")
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="r29-shots-")))))
