"""Round 32 (r32-textpanels): headless check of the «text» look as real text in the Quick Access window.

    PYTHONUTF8=1 NO_PROXY='*' python tests/round32_textpanels_browser.py [OUT_DIR]

The backend of tests/round27_quick_canvas_shots.py (real app.quick_canvas + MemuAI panel routes) over a temporary
profile with a crypto panel and an AI sports panel whose data carries HTML-looking text. Checks (exit 1 on a problem):
the built-in section above the input draws both panels as text faces (blocks of rendered template text, no card),
data HTML stays text, the tile menu opens the editor, a drag of a block switches to the free layout, a typed template
with a filter renders live, «Save» persists the template (style text + text) and the tile shows it; dark + light,
RU + EN. Optional OUT_DIR: screenshots (look at them).
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
os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r32tp-browser-")
SHOW = json.dumps({"fresh": True, "maxHeight": 760, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False,
                   "source": "hotkey", "page": None, "selection": ""})

def seed():
    from app import panels
    crypto = panels.create_panel(template="crypto", params={"coin": "bitcoin", "vs": "usd"}, start=False)
    sports = panels.create_panel(template="sports", params={"team": "Зенит"}, start=False)

    def fill(doc):
        for p in doc["panels"]:
            if p["id"] == crypto["id"]:
                p.update(status="live", wall=None, last_refresh=time.time() - 60,
                         data={"name": "Bitcoin", "symbol": "BTC", "price": "82 976", "price_short": "83,0к", "vs_sign": "$",
                               "delta": "+0,30%", "trend": "up", "hist": [1, 2, 3]})
            if p["id"] == sports["id"]:
                p.update(status="live", wall=None, last_refresh=time.time() - 60,
                         spec={"blocks": [{"type": "title", "text": "{{headline}}", "subtitle": "{{competition}}", "icon": "trophy"},
                                          {"type": "metric", "value": "{{score}}", "label": "{{last_label}}"}],
                               "mini": {"value": "{{score}}", "label": "Зенит", "icon": "trophy"}},
                         data={"headline": "Краснодар — Зенит <img src=x onerror=alert(1)>", "competition": "РПЛ", "score": "2:3",
                               "last_label": "10 октября", "next_match": {"opponent": "Спартак", "date": "2026-10-14T19:30:00+03:00"}})
    panels.mutate(fill)
    return crypto["id"], sports["id"]

async def main(out: Path | None) -> int:
    import httpx
    import uvicorn
    from playwright.async_api import async_playwright
    import round27_quick_canvas_shots as base
    from app import themes
    crypto, sports = seed()
    port = base.free_port()
    server = uvicorn.Server(uvicorn.Config(base.make_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    B = f"http://127.0.0.1:{port}"
    problems: list = []
    prefs: dict = {}

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def post(path, body):
        return httpx.post(B + path, json=body, timeout=20, trust_env=False).json()

    async def route(r, req):
        p = "/" + req.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if p == "/themes.css":
            return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if p == "/api/quick/config":
            return await r.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                                         "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if p == "/api/pg/state":
            return await r.fulfill(json={"models": base.MODELS, "image_models": []})
        if p in ("/api/quick/layer/region", "/api/quick/window"):
            return await r.fulfill(json={"ok": True})
        if p == "/api/quick/layer":
            return await r.fulfill(json={"ok": True, "available": False, "visible": False, "edit": False})
        if p.startswith("/api/quick/canvas") or p.startswith("/api/pg/panels"):
            return await r.continue_()
        if p == "/api/assistant/state":
            return await r.fulfill(json={"settings": {"enabled": False, "voice": {"enabled": False}}, "seq": 0, "timers": None})
        if p.startswith("/api/"):
            return await r.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await r.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=base.CHROME if os.path.exists(base.CHROME) else None, args=["--no-sandbox"])
        for theme, lang in (("dark", "ru"), ("light", "en")):
            what = f"{theme}/{lang}"
            post("/api/quick/canvas/look", {"reset_all": True, "look": {"style": "text"},
                                            "embed": {"place": "above", "items": [{"ref": crypto}, {"ref": sports}], "style": "", "max": 6}})
            prefs.clear()
            prefs.update({"theme": theme, "accent_color": "violet", "ui_design": "classic", "language": lang, "quick_ask_model": "deepseek-v4.1-flash"})
            ctx = await browser.new_context(viewport={"width": 720, "height": 760})
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
            await ctx.route("**/*", route)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("dialog", lambda d: problems.append("a dialog opened (script ran from data)"))
            await page.goto(B + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(500)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(1600)
            faces = await page.evaluate("""() => [...document.querySelectorAll('.qpe-tile')].map((t) => ({
                ref: t.dataset.ref, face: !!t.querySelector('.pt-face'), card: !!t.querySelector('.mp-card'),
                blocks: t.querySelectorAll('.pt-b').length, text: t.innerText, img: t.querySelectorAll('img').length }))""")
            check(len(faces) == 2 and all(f["face"] and not f["card"] and f["blocks"] >= 2 for f in faces), f"{what}: text faces {faces}")
            byref = {f["ref"]: f for f in faces}
            check("82 976 $" in byref.get(crypto, {}).get("text", "").replace("\u00a0", " "), f"{what}: crypto value as text")
            sp = byref.get(sports, {})
            check("<img src=x" in sp.get("text", "") and sp.get("img") == 0, f"{what}: data HTML stays text ({sp})")
            if out:
                height = await page.evaluate("QuickAsk.render()")
                await page.set_viewport_size({"width": 720, "height": max(200, min(900, int(height)))})
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(out / f"tpb-window-{theme}-{lang}.png"))

            tile = page.locator(f'.qpe-tile[data-ref="{crypto}"]')
            await tile.click(button="right")
            await page.wait_for_timeout(300)
            item = page.locator(".mcm-item", has_text="Изменить текст" if lang == "ru" else "Edit the text").first
            check(await item.count() == 1, f"{what}: the tile menu has «edit the text»")
            if await item.count():
                await item.click()
            else:
                await page.keyboard.press("Escape")
                await page.evaluate("(r) => MemuQuickEmbed.openText(r)", crypto)
            await page.wait_for_timeout(900)
            ok = await page.evaluate("!!document.querySelector('.qpt-host:not([hidden]) .pte .pte-stage .pt-b')")
            check(ok, f"{what}: the editor opens from the tile menu")
            if not ok:
                await ctx.close()
                continue
            n_ph = await page.locator(".pte-list > button").count()
            check(n_ph >= 5, f"{what}: the placeholder picker lists the values ({n_ph})")
            blk = page.locator(".pte-stage .pt-b").nth(1)
            bb = await blk.bounding_box()
            await page.mouse.move(bb["x"] + 8, bb["y"] + bb["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(bb["x"] + 30, bb["y"] + 10, steps=5)
            await page.mouse.move(bb["x"] + 50, bb["y"] + 20, steps=5)
            await page.mouse.up()
            await page.wait_for_timeout(250)
            mode = await page.evaluate("document.querySelector('.pte-stage').dataset.mode")
            check(mode == "free", f"{what}: a drag switches to the free layout ({mode})")
            await page.locator(".pte-blocks button").nth(0).click()
            await page.locator(".pte-tpl").fill("BTC {price|round:0} {missing}")
            await page.locator(".pte-filters > summary").click()
            await page.locator('[data-pte-filter="upper"]').click()
            await page.wait_for_timeout(200)
            tpl = await page.locator(".pte-tpl").input_value()
            check("{missing|upper}" in tpl, f"{what}: the filter is inserted into the placeholder ({tpl})")
            first = await page.evaluate("document.querySelector('.pte-stage .pt-b').innerText")
            check(first.replace("\u00a0", " ").startswith("BTC 82 976" if lang == "ru" else "BTC 82,976"), f"{what}: the preview renders live ({first})")
            if out:
                height = await page.evaluate("QuickAsk.render()")
                await page.set_viewport_size({"width": 720, "height": max(200, min(900, int(height)))})
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(out / f"tpb-editor-{theme}-{lang}.png"))
            await page.locator('[data-pte-act="save"]').click()
            await page.wait_for_timeout(900)
            saved = httpx.get(B + "/api/quick/canvas", timeout=20, trust_env=False).json()["looks"].get(crypto, {})
            check(saved.get("style") == "text" and saved.get("text", {}).get("mode") == "free"
                  and saved["text"]["blocks"][0]["t"].startswith("BTC {price|round:0}"), f"{what}: saved {saved}")
            shown = await page.evaluate("(r) => { const t = document.querySelector(`.qpe-tile[data-ref='${r}'] .pt-face`); return t ? [t.dataset.mode, t.innerText] : null; }", crypto)
            check(shown and shown[0] == "free" and "BTC" in shown[1], f"{what}: the tile shows the saved text ({shown})")
            closed = await page.evaluate("document.querySelector('.qpt-host').hidden")
            check(closed, f"{what}: the editor closes after saving")

            await page.evaluate("(r) => MemuQuickEmbed.openText(r)", crypto)
            await page.wait_for_timeout(700)
            await page.locator(".pte-stage .pt-b").nth(0).focus()
            x0 = await page.evaluate("MemuQuickEmbed.state().textRef && document.querySelector('.pte-stage .pt-b').style.left")
            await page.keyboard.press("Shift+ArrowRight")
            x1 = await page.evaluate("document.querySelector('.pte-stage .pt-b').style.left")
            check(x0 != x1, f"{what}: Shift+→ moves the block ({x0} → {x1})")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            st = await page.evaluate("[document.querySelector('.qpt-host').hidden, QuickAsk && document.getElementById('qa-card') ? getComputedStyle(document.getElementById('qa-card')).display : 'none']")
            check(st[0] is True and st[1] != "none", f"{what}: Esc closes only the editor ({st})")
            await ctx.close()
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0

if __name__ == "__main__":
    o = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if o:
        o.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(o)))
