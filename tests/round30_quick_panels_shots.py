"""Round 30 (r30-quick-panels, item 7): headless screenshots + layout checks of the panel looks in Quick Access.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round30_quick_panels_shots.py OUT_DIR [--only grid,font,embed,layer,settings]

The backend of tests/round27_quick_canvas_shots.py (real app.quick_canvas + MemuAI panel routes over a temporary
profile with weather / To-Do / clock / notes; a mock run executor) serves the Alt+Space window and the screen layer;
the settings page is the real playground (app.playground_api.create_app) over the same profile.
Covers the four looks (card / compact / text / icon) in the window grid, on the screen layer and in the section built
into the window (above / below / side), dark + light, RU + EN, 720 and 420 px, font 80 % / 140 %, and
Settings → Quick Access → «Вид панелей и встраивание». Checks (exit code 1 on a problem):
  * no page errors; every panel face stays inside its cell / tile (no clipped or overflowing content);
  * no scrollbar where the content fits (a scrollable box that does not actually scroll is a bug);
  * the side column fits the window (the window grows for it) and a 420 px window shows the section below instead;
  * the built-in section and the canvas strip («Панели Ctrl B») never overlap;
  * the settings page: nothing sticks out of the pane, in English no Cyrillic.
Screens: qp-*.png in OUT_DIR — look at every one.
"""
from __future__ import annotations

import argparse
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

STYLES = ("card", "compact", "text", "icon")
WALLPAPER = ("document.body.style.background = 'linear-gradient(135deg, #2b4c7e 0%, #567ebb 40%, #c38d9e 75%, #e8a87c 100%)';"
             "document.documentElement.style.background = '#2b4c7e';")
SHOW = json.dumps({"fresh": True, "maxHeight": 760, "rounded": True, "corner": "dwm", "radius": 8, "pinned": False,
                   "source": "hotkey", "page": None, "selection": ""})

# Faces that leave their cell, and scroll boxes that show a bar without having anything to scroll.
FACES_JS = """(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((cell) => {
    const r = cell.getBoundingClientRect();
    if (!r.width) return;
    const face = cell.querySelector(':scope > .pl-face, :scope > .mp-card, :scope > .ql-body');
    if (!face) return;
    const f = face.getBoundingClientRect();
    if (f.left < r.left - 1.5 || f.right > r.right + 1.5 || f.top < r.top - 1.5 || f.bottom > r.bottom + 1.5)
      out.push('face outside ' + (cell.dataset.item || cell.dataset.ref || '') + ' ' + [f.width, f.height, r.width, r.height].map(Math.round).join('/'));
    face.querySelectorAll('.pl-v, .pl-big, .mp-title, .mp-value, .mp-big').forEach((el) => {
      const e = el.getBoundingClientRect();
      if (e.width && (e.right > r.right + 1.5 || e.bottom > r.bottom + 1.5)) out.push('text outside ' + (el.textContent || '').trim().slice(0, 24));
    });
  });
  return out;
}"""
BARS_JS = """(root) => {
  const box = root ? document.querySelector(root) : document.body;
  if (!box) return [];
  const out = [];
  [box, ...box.querySelectorAll('*')].forEach((el) => {
    if (!el.offsetParent && el !== document.body) return;
    const cs = getComputedStyle(el);
    for (const [ax, ov, sz, cl] of [['y', cs.overflowY, el.scrollHeight, el.clientHeight], ['x', cs.overflowX, el.scrollWidth, el.clientWidth]]) {
      if (ov !== 'scroll' && ov !== 'auto') continue;
      // a real bar takes room: offsetWidth - clientWidth (y) / offsetHeight - clientHeight (x)
      const bar = ax === 'y' ? el.offsetWidth - el.clientWidth - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth)
                             : el.offsetHeight - el.clientHeight - parseFloat(cs.borderTopWidth) - parseFloat(cs.borderBottomWidth);
      if (ov === 'scroll' && bar > 0 && sz <= cl + 1) out.push('empty scrollbar ' + ax + ' ' + (el.id || el.className || el.tagName));
      if (ov === 'auto' && bar > 0 && sz > cl + 1 && sz - cl < 6) out.push('scrollbar for ' + (sz - cl) + 'px ' + ax + ' ' + (el.id || el.className || el.tagName));
    }
  });
  return out.slice(0, 6);
}"""


async def main(out: Path, only: set) -> int:
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
    prefs: dict = {}
    layer_on = {"available": False}

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    def post(path, body):
        return httpx.post(B + path, json=body, timeout=20).json()

    for key, size in (("weather", "m"), ("todo", "l"), ("clock", "s"), ("notes", "w")):
        check(post("/api/quick/canvas/panels", {"panel": seeded[key], "size": size}).get("ok"), f"add panel {key}")
    for tp in httpx.get(B + "/api/quick/canvas/catalog?lang=ru", timeout=20).json()["templates"][:3]:
        post("/api/quick/canvas/actions", {"action": {k: v for k, v in tp.items() if k != "key"}})
    embed_items = [{"ref": seeded[k], "hidden": False} for k in ("weather", "clock", "todo", "notes")]

    def set_look(look=None, embed=None):
        body = {"reset_all": True, "look": look or {}}
        if embed is not None:
            body["embed"] = embed
        r = post("/api/quick/canvas/look", body)
        check(r.get("ok"), f"save look {look} {embed} ({r})")

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
            return await r.fulfill(json={"ok": True, "available": layer_on["available"], "visible": True, "edit": False})
        if p == "/api/quick/canvas" and req.method == "GET":
            resp = await r.fetch()
            data = await resp.json()
            data["layer"] = {**data.get("layer", {}), "available": layer_on["available"], "visible": True}
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

        async def new_ctx(w, h, theme, lang):
            prefs.clear()
            prefs.update({"theme": theme, "accent_color": "violet", "ui_design": "classic", "language": lang, "quick_ask_model": "deepseek-v4.1-flash"})
            ctx = await browser.new_context(viewport={"width": w, "height": h})
            await ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
            await ctx.route("**/*", route)
            return ctx

        async def open_window(width, theme, lang, canvas=True):
            ctx = await new_ctx(width, 700, theme, lang)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: problems.append(f"window pageerror: {e}"))
            await page.goto(B + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(500)
            await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
            await page.wait_for_timeout(900)
            if canvas:
                await page.keyboard.press("Control+b")
                await page.wait_for_timeout(1300)
            return page

        async def fit_shot(page, name, width):
            await page.wait_for_timeout(250)
            height = await page.evaluate("QuickAsk.render()")
            await page.set_viewport_size({"width": width, "height": max(80, min(900, int(height)))})
            await page.wait_for_timeout(450)
            await page.screenshot(path=str(out / f"{name}.png"))

        # ------------------------------------------------------------ 1. the window grid: four looks
        if not only or "grid" in only:
            for style in STYLES:
                set_look({"style": style})
                for theme, lang, width in (("dark", "ru", 720), ("light", "en", 420)):
                    page = await open_window(width, theme, lang)
                    what = f"grid {style} {theme}/{lang}/{width}"
                    n = await page.evaluate("document.querySelectorAll('.qcv-item[data-pl-style]').length")
                    check(n >= 4, f"{what}: the panels carry the look ({n})")
                    if style != "card":
                        faces = await page.evaluate(f"document.querySelectorAll('.qcv-item .pl-{style}').length")
                        check(faces >= 4, f"{what}: drawn as «{style}» ({faces})")
                    bad = await page.evaluate(FACES_JS, ".qcv-item[data-pl-style]")
                    check(not bad, f"{what}: faces inside their cells {bad}")
                    bars = await page.evaluate(BARS_JS, "#qa-card")
                    check(not bars, f"{what}: no needless scrollbars {bars}")
                    await fit_shot(page, f"qp-grid-{style}-{theme}-{lang}-{width}", width)
                    await page.context.close()

        # ------------------------------------------------------------ 2. font size 80 % / 140 % (the card zooms, faces scale)
        if not only or "font" in only:
            for style in ("card", "compact", "icon"):
                for font in (80, 140):
                    set_look({"style": style, "font": font})
                    page = await open_window(720, "dark", "ru")
                    what = f"font {font}% {style}"
                    bad = await page.evaluate(FACES_JS, ".qcv-item[data-pl-style]")
                    check(not bad, f"{what}: faces inside their cells {bad}")
                    await fit_shot(page, f"qp-font{font}-{style}-dark-ru", 720)
                    await page.context.close()

        # ------------------------------------------------------------ 3. built into the window: above / below / side
        if not only or "embed" in only:
            cases = [("above", "compact", "dark", "ru", 720), ("below", "card", "dark", "ru", 720), ("side", "card", "dark", "ru", 720),
                     ("side", "text", "light", "en", 720), ("above", "icon", "light", "en", 720), ("below", "text", "light", "ru", 720),
                     ("side", "compact", "dark", "en", 420), ("above", "card", "light", "ru", 420)]
            for place, style, theme, lang, width in cases:
                set_look({"style": style}, {"place": place, "items": embed_items, "style": "", "max": 6})
                page = await open_window(width, theme, lang, canvas=False)
                await page.wait_for_timeout(600)
                what = f"embed {place} {style} {theme}/{lang}/{width}"
                state = await page.evaluate("""() => { const b = document.querySelector('.qpe');
                  return b && !b.hidden ? { place: b.dataset.place, tiles: b.querySelectorAll('.qpe-tile').length } : {}; }""")
                expect = "below" if place == "side" and width < 560 else place
                if not (state and state["place"] == expect and state["tiles"] == 4):
                    state = {"seen": state, "embed": await page.evaluate("JSON.stringify([MemuQuickEmbed.state(), (MemuQuickCanvas.state().data || {}).embed, !!window.MemuPanels, !!window.MemuPanelLook])")}
                check(state.get("place") == expect and state.get("tiles") == 4, f"{what}: the section is shown {expect} ({state})")
                bad = await page.evaluate(FACES_JS, ".qpe-tile")
                check(not bad, f"{what}: tiles inside their boxes {bad}")
                await fit_shot(page, f"qp-embed-{place}-{style}-{theme}-{lang}-{width}", width)
                geo = await page.evaluate("""() => {
                  const b = document.querySelector('.qpe'), card = document.getElementById('qa-card');
                  if (!b || b.hidden) return null;
                  const r = b.getBoundingClientRect(), c = card.getBoundingClientRect();
                  const strip = document.querySelector('.qcv-strip, .qcv-head, .qcv-chip');
                  const s = strip && strip.offsetParent ? strip.getBoundingClientRect() : null;
                  return { box: [r.top, r.bottom, r.left, r.right], card: [c.top, c.bottom, c.left, c.right],
                           scroll: b.scrollHeight - b.clientHeight, strip: s ? [s.top, s.bottom, s.left, s.right] : null,
                           vh: innerHeight };
                }""")
                if geo:
                    check(geo["box"][1] <= geo["vh"] + 1, f"{what}: the section fits the window ({geo})")
                    if expect == "side":
                        check(geo["scroll"] <= 1, f"{what}: the side column shows all tiles without scrolling ({geo['scroll']}px)")
                    s = geo["strip"]
                    if s:
                        b = geo["box"]
                        apart = s[1] <= b[0] + 1 or s[0] >= b[1] - 1 or s[3] <= b[2] + 1 or s[2] >= b[3] - 1
                        check(apart, f"{what}: the canvas strip does not overlap the section ({geo})")
                bars = await page.evaluate(BARS_JS, "#qa-card")
                check(not bars, f"{what}: no needless scrollbars {bars}")
                await page.context.close()
            set_look({}, {"place": "off"})

        # ------------------------------------------------------------ 4. the screen layer
        if not only or "layer" in only:
            layer_on["available"] = True
            mon = {"key": "1366x728@96", "w": 1366, "h": 728, "scale": 1}
            keep = {"hard": [323, 160, 720, 136], "soft": [323, 160, 720, 480]}
            for style, theme, lang in (("card", "dark", "ru"), ("compact", "dark", "ru"), ("text", "dark", "en"), ("icon", "light", "en"), ("text", "light", "ru")):
                set_look({"style": style, "opacity": 85 if style != "text" else 100})
                ctx = await new_ctx(1366, 728, theme, lang)
                page = await ctx.new_page()
                page.on("pageerror", lambda e: problems.append(f"layer pageerror: {e}"))
                await page.goto(B + "/quick/layer.html?native=1", wait_until="load")
                await page.wait_for_timeout(300)
                await page.evaluate("(p) => MemuLayer.onShow(p)", {"monitor": mon, "keepout": keep, "glass": True, "fresh": True})
                await page.wait_for_timeout(1400)
                what = f"layer {style} {theme}/{lang}"
                n = await page.evaluate("document.querySelectorAll('.ql-item[data-pl-style]').length")
                check(n >= 4, f"{what}: the panels carry the look ({n})")
                bad = await page.evaluate(FACES_JS, ".ql-item[data-pl-style]")
                check(not bad, f"{what}: faces inside their items {bad}")
                await page.evaluate(WALLPAPER)
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(out / f"qp-layer-{style}-{theme}-{lang}.png"))
                await ctx.close()
            layer_on["available"] = False
            set_look({})

        # ------------------------------------------------------------ 5. Settings → Quick Access → «Вид панелей и встраивание»
        if not only or "settings" in only:
            from app.playground_api import create_app
            pg_port = base.free_port()
            pg = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=pg_port, log_level="warning"))
            threading.Thread(target=pg.run, daemon=True).start()
            P = f"http://127.0.0.1:{pg_port}"
            for _ in range(200):
                try:
                    httpx.get(P + "/api/quick/canvas", timeout=2)
                    break
                except Exception:
                    time.sleep(0.1)
            set_look({"style": "compact"}, {"place": "side", "items": embed_items, "style": "", "max": 6})
            for theme, lang, width in (("dark", "ru", 1180), ("light", "en", 1180), ("dark", "en", 900), ("light", "ru", 1366)):
                httpx.post(P + "/api/pg/preferences", json={"theme": theme, "language": lang, "profile_onboarded": True}, timeout=20)
                page = await browser.new_page(viewport={"width": width, "height": 860})
                page.on("pageerror", lambda e: problems.append(f"settings pageerror: {e}"))
                await page.goto(P + "/", wait_until="networkidle")
                await page.wait_for_timeout(900)
                await page.evaluate("openSettingsPage('quick')")
                link = page.locator('[data-sx-open="quick-plugin-canvas-embed"]')
                try:      # the plugins' settings scripts load one by one after the page opens
                    await link.wait_for(state="attached", timeout=20000)
                except Exception:
                    pass
                what = f"settings {theme}/{lang}/{width}"
                if await link.count() != 1:
                    have = await page.evaluate("[...document.querySelectorAll('[data-sx-open^=quick-plugin]')].map(e => e.dataset.sxOpen)")
                    check(False, f"{what}: the «look & embed» link is on the Quick Access page ({have})")
                if await link.count():
                    await link.click()
                    await page.wait_for_timeout(1400)
                pane = "#qs-sub-plugin-canvas-embed"
                wide = await page.evaluate("""(sel) => { const pane = document.querySelector(sel); if (!pane) return ['no pane'];
                    const right = pane.getBoundingClientRect().right + 1;
                    return [...pane.querySelectorAll('*')].filter((el) => el.offsetParent && el.getBoundingClientRect().right > right && !el.closest('.sx-section-head'))
                      .slice(0, 5).map((el) => (el.id || el.className || el.tagName) + ''); }""", pane)
                check(not wide, f"{what}: nothing sticks out ({wide})")
                bars = await page.evaluate(BARS_JS, pane)
                check(not bars, f"{what}: no needless scrollbars {bars}")
                bad = await page.evaluate(FACES_JS, ".qls-tile")
                check(not bad, f"{what}: preview tiles inside their boxes {bad}")
                if lang == "en":
                    cyr = await page.evaluate("""(sel) => { const pane = document.querySelector(sel); if (!pane) return [];
                      const w = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT); const o = []; let n;
                      while ((n = w.nextNode())) { const el = n.parentElement; if (el && el.offsetParent && /[А-Яа-яЁё]/.test(n.textContent)
                        && !el.closest('.qls-preview, .qls-name')) o.push(n.textContent.trim().slice(0, 40)); }
                      return o.slice(0, 5); }""", pane)
                    check(not cyr, f"{what}: no Russian in English ({cyr})")
                # the page scrolls inside the settings dialog: shoot the dialog at each screenful of the sub-page
                steps = await page.evaluate("""(sel) => { const pane = document.querySelector(sel); if (!pane) return 0;
                  let s = pane.parentElement; while (s && !(/(auto|scroll)/.test(getComputedStyle(s).overflowY) && s.scrollHeight > s.clientHeight)) s = s.parentElement;
                  window.__qpScroller = s || null; return s ? Math.ceil((s.scrollHeight - s.clientHeight) / (s.clientHeight - 120)) + 1 : 1; }""", pane)
                for i in range(min(steps, 4)):
                    await page.evaluate("(i) => { const s = window.__qpScroller; if (s) s.scrollTop = i * (s.clientHeight - 120); }", i)
                    await page.wait_for_timeout(250)
                    await page.screenshot(path=str(out / f"qp-settings-{theme}-{lang}-{width}-{i + 1}.png"))
                await page.close()
            pg.should_exit = True
            set_look({}, {"place": "off"})
        await browser.close()
    server.should_exit = True
    print(f"{len(problems)} problems")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="/workspace/work/shots/r29/r30-quick-panels")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r30qp-shots-")   # never the caller's profile: panels are seeded
    sys.exit(asyncio.run(main(Path(a.out), set(filter(None, a.only.split(","))))))
