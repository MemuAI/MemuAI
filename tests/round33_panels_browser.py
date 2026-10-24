"""Round 33 (r33-panels): the real MemuAI window (app.playground_api on a temporary profile) in headless Chromium.

    PYTHONUTF8=1 NO_PROXY=127.0.0.1,localhost python3 tests/round33_panels_browser.py [OUT_DIR] [PART]

PART: 1 — zones, soft scrollbars, rails; 2 — «Edit layout»; 3 — English; all three when omitted.

Checks (exit code 1 on any problem; screenshots r33p-*.png in OUT_DIR):
  * the panel areas hide the native scrollbar (scrollbar-width: none) and get the overlay thumb + edge fades;
  * panels placed above the greeting / above the message box / in both columns / at the sidebar bottom are drawn there;
    a 1366 px window keeps both columns (narrower), a 1000 px one folds them into rails of chips;
  * «Edit layout» from the right-click menu: every zone is a labelled target; a card dragged from the left column into
    the top strip is saved there; Ctrl+Z puts it back; the corner grip makes a grid card wide (W); the size buttons
    and the ⚙ popover (zone map) work; arrows reorder; Delete takes it out of the place; Esc leaves the mode;
  * «More places…» in the context menu opens the zone map; «In chats too» keeps a zone inside a conversation;
  * the English window has no Cyrillic in zone heads, the bar and the chrome (panel titles are user data).
"""
from __future__ import annotations

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
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="r33p-shots-"))
PARTS = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else {"1", "2", "3"}
DATA = Path(tempfile.mkdtemp(prefix="r33p-data-"))
os.environ["MEMUAI_DATA_DIR"] = str(DATA)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
try:
    import webview  # noqa: F401
except ImportError:
    import types
    sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)

NOW = time.time()
problems: list = []


def check(ok, what):
    if not ok:
        problems.append(what)
        print("FAIL:", what)
    else:
        print("ok  ", what)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def seed():
    from app import panels

    def P(pid, title, blocks, data, places, size="m"):
        return {"id": pid, "kind": "custom", "title": title, "status": "live", "spec": {"blocks": blocks}, "data": data,
                "sources": [{"name": "Source", "url": "https://source.example/"}], "placements": places, "order": {},
                "size": size, "cadence": {"kind": "manual"}, "last_refresh": NOW - 300, "next_refresh": NOW + 10 ** 7,
                "source": {"type": "ai", "params": {}}}
    items = [{"title": f"Story {i}: a fairly long headline about a release", "url": f"https://n{i}.example/"} for i in range(8)]
    rows = [
        P("p_a00000001", "Alpha", [{"type": "title", "text": "Alpha"}, {"type": "metric", "value": "{{v}}", "unit": "%"}], {"v": 42}, ["home", "appbar"], "s"),
        P("p_b00000001", "Beta", [{"type": "title", "text": "Beta"}, {"type": "metric", "value": "{{v}}", "unit": "$"}, {"type": "sparkline", "points": "{{h}}"}],
          {"v": "67 432", "h": [1, 3, 2, 5]}, ["composer", "right"], "s"),
        P("p_c00000001", "Gamma", [{"type": "title", "text": "Gamma"}, {"type": "metric", "value": "279,5", "unit": "₽"}], {"_static": True}, ["left", "composer"], "s"),
        P("p_d00000001", "Delta news", [{"type": "title", "text": "Delta news"}, {"type": "list", "items": "{{items}}", "max": 8}], {"items": items}, ["home"], "m"),
        P("p_e00000001", "Epsilon", [{"type": "title", "text": "Epsilon"}, {"type": "text", "text": "Do the main thing first."}], {"_static": True}, ["top", "sidebar_bottom"], "s"),
        P("p_f00000001", "Zeta", [{"type": "title", "text": "Zeta"}, {"type": "metric", "value": "7", "unit": "°C"}], {"_static": True}, ["home", "top"], "s"),
        P("p_g00000001", "Eta", [{"type": "title", "text": "Eta"}, {"type": "metric", "value": "3", "unit": "h"}], {"_static": True}, ["home", "right"], "s"),
    ]

    def mut(doc):
        doc["panels"] = [panels.clean_panel(r) for r in rows]
        for i, p in enumerate(doc["panels"]):
            for pl in p["placements"]:
                p["order"][pl] = i
        doc["seeded"] = True
        doc["settings"]["location"] = {"mode": "declined"}
    panels.mutate(mut)


def main() -> int:
    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app.playground_api import create_app
    OUT.mkdir(parents=True, exist_ok=True)
    seed()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    client = httpx.Client(timeout=30, trust_env=False)
    errors: list = []

    def panel(pid):
        return next(p for p in client.get(base + "/api/pg/panels").json()["panels"] if p["id"] == pid)

    def open_page(browser, w, h, lang="ru", theme="dark", design="classic"):
        client.post(base + "/api/pg/preferences", json={"language": lang, "theme": theme, "ui_design": design, "ui_motion": "reduced",
                                                        "profile_onboarded": True, "profile_name": "Alex"})
        ctx = browser.new_context(viewport={"width": w, "height": h})
        ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}'); }} catch (e) {{}}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("body.boot-ready", state="attached", timeout=60000)
        page.wait_for_selector("#panels-home .mp-card", timeout=15000)
        if page.locator("#onboarding-name").count():
            page.fill("#onboarding-name", "Alex")
            page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        return page

    try:
        with sync_playwright() as pw:
            chrome = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else None
            browser = pw.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
            # ---------------------------------------------------------------- zones + soft scrollbars
            if "1" in PARTS:
                page = open_page(browser, 1366, 768)
                page.screenshot(path=str(OUT / "r33p-zones-1366.png"))
                info = page.evaluate("""() => ({
                  sw: getComputedStyle(document.querySelector('#panels-home')).scrollbarWidth,
                  soft: document.querySelector('#panels-home').classList.contains('mp-softscroll'),
                  thumbs: document.querySelectorAll('.mp-sthumb').length,
                  zones: Object.fromEntries(MemuPanelsLayout.zones().map(z => [z.id, [z.count, z.visible, z.fold]])),
                  top: [...document.querySelectorAll('#panels-z-top [data-panel-id]')].map(e => e.dataset.panelId),
                  composer: [...document.querySelectorAll('#panels-z-composer .mp-chip[data-panel-id]')].map(e => e.dataset.panelId),
                  sbottom: document.querySelectorAll('#panels-z-sidebar_bottom .mp-chip').length })""")
                check(info["sw"] == "none" and info["soft"], f"home grid: native scrollbar hidden ({info['sw']})")
                check(info["thumbs"] >= 3, "overlay thumbs exist for the scrollers")
                check(info["top"] == ["p_e00000001", "p_f00000001"] or set(info["top"]) == {"p_e00000001", "p_f00000001"}, f"top strip cards {info['top']}")
                check(set(info["composer"]) == {"p_b00000001", "p_c00000001"}, f"chips above the message box {info['composer']}")
                check(info["sbottom"] == 1, "sidebar bottom chip")
                z = info["zones"]
                check(z["left"][1] and z["right"][1] and z["left"][2] == "" and z["right"][2] == "", f"1366: both columns as columns {z['left']} {z['right']}")
                # hover the grid → thumb shows; wheel scrolls; fade on
                home = page.locator("#panels-home")
                hb = home.bounding_box()
                page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + min(hb["height"] - 5, 60))
                page.mouse.wheel(0, 120)
                page.wait_for_timeout(250)
                state = page.evaluate("""() => { const h = document.querySelector('#panels-home'); const t = h._mpThumb;
                  return {scrollable: h.scrollHeight > h.clientHeight + 1, top: h.scrollTop, fade: h.dataset.fadeStart, on: t && t.classList.contains('on') && !t.hidden}; }""")
                if state["scrollable"]:
                    check(state["top"] > 0 and state["fade"] == "1" and state["on"], f"wheel scrolls the grid, top fade + thumb show {state}")
                page.context.close()

            # ---------------------------------------------------------------- narrow: rails
                page = open_page(browser, 1000, 700)
                z = {x["id"]: x["fold"] for x in page.evaluate("MemuPanelsLayout.zones()")}
                check(z["left"] == "rail" and z["right"] == "rail", f"1000 px: columns fold into rails {z}")
                check(page.locator("#panels-z-right .mp-chip[data-panel-id]").count() == 2, "rail shows chips")
                page.screenshot(path=str(OUT / "r33p-rails-1000.png"))
                page.context.close()

            # ---------------------------------------------------------------- edit mode
            if "2" in PARTS:
                page = open_page(browser, 1700, 1000, theme="light")
                page.locator('#panels-home .mp-card[data-panel-id="p_a00000001"]').click(button="right")
                page.wait_for_timeout(250)
                check(page.locator(".mcm .mcm-item", has_text="Изменить расположение").count() == 1, "context menu: «Изменить расположение»")
                check(page.locator(".mcm .mcm-item", has_text="Другие места").count() == 1, "context menu: «Другие места…»")
                page.locator(".mcm .mcm-item", has_text="Изменить расположение").click()
                page.wait_for_timeout(600)
                check(page.evaluate("MemuPanelsLayout.editing()") and page.locator(".mp-editbar").count() == 1, "edit mode on, the bar is shown")
                heads = page.locator("[data-zone-wrap] .mp-zone-head .mp-zone-name").all_inner_texts()
                check(len(heads) >= 6, f"zone heads drawn {heads}")
                page.screenshot(path=str(OUT / "r33p-edit.png"))
                # drag left column → top strip
                page.locator('#panels-z-left .mp-card[data-panel-id="p_c00000001"]').drag_to(page.locator("#panels-z-top .mp-zone-body"))
                page.wait_for_timeout(800)
                pl = panel("p_c00000001")["placements"]
                check("top" in pl and "left" not in pl, f"dragged into the top strip and saved {pl}")
                page.mouse.click(5, 300)
                page.keyboard.press("Control+z")
                page.wait_for_timeout(800)
                pl = panel("p_c00000001")["placements"]
                check("left" in pl and "top" not in pl, f"Ctrl+Z puts it back {pl}")
                # corner grip → W
                card = page.locator('#panels-home .mp-card[data-panel-id="p_a00000001"]')
                card.evaluate("el => el.scrollIntoView({block: 'center'})")
                page.wait_for_timeout(150)
                card.hover()
                page.wait_for_timeout(200)
                grip = page.locator(".mp-edgrip")
                gb = grip.bounding_box()
                grid = page.locator("#panels-home .mp-grid").bounding_box()
                cols = page.evaluate("getComputedStyle(document.querySelector('#panels-home .mp-grid')).gridTemplateColumns.split(' ').length")
                page.mouse.move(gb["x"] + 11, gb["y"] + 11)
                page.mouse.down()
                page.mouse.move(grid["x"] + grid["width"] - 4, gb["y"] + 11, steps=10)
                page.wait_for_timeout(150)
                page.screenshot(path=str(OUT / "r33p-resize.png"))
                page.mouse.up()
                page.wait_for_timeout(800)
                check(panel("p_a00000001")["size"] == ("w" if cols > 2 else "m"), f"corner grip resizes by grid columns ({cols} cols → {panel('p_a00000001')['size']})")
                # size buttons
                card = page.locator('#panels-home .mp-card[data-panel-id="p_a00000001"]')
                card.hover()
                page.wait_for_timeout(150)
                page.locator('.mp-edchrome [data-edc-size="s"]').click()
                page.wait_for_timeout(700)
                check(panel("p_a00000001")["size"] == "s", "S button in the card chrome")
                # ⚙ popover with the zone map
                card = page.locator('#panels-home .mp-card[data-panel-id="p_a00000001"]')
                card.hover()
                page.wait_for_timeout(150)
                page.locator('.mp-edchrome [data-edc="settings"]').click()
                page.wait_for_timeout(300)
                page.locator('.mp-laypop .mz[data-mz="sidebar_bottom"]').click()
                page.wait_for_timeout(700)
                check("sidebar_bottom" in panel("p_a00000001")["placements"], "zone map toggles a place")
                page.screenshot(path=str(OUT / "r33p-popover.png"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
                check(page.locator(".mp-laypop").count() == 0 and page.evaluate("MemuPanelsLayout.editing()"), "Esc closes the popover first, the mode stays")
                # keyboard: arrows reorder, Delete unpins
                order0 = [e for e in page.locator("#panels-home .mp-card[data-panel-id]").evaluate_all("els => els.map(e => e.dataset.panelId)")]
                first = page.locator(f'#panels-home .mp-card[data-panel-id="{order0[0]}"]')
                first.focus()
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(700)
                check(panel(order0[0])["order"]["home"] == 1, "ArrowRight moves the focused card one step")
                page.locator(f'#panels-home .mp-card[data-panel-id="{order0[0]}"]').focus()
                page.keyboard.press("Delete")
                page.wait_for_timeout(700)
                check("home" not in panel(order0[0])["placements"], "Delete takes it out of the place (the panel stays)")
                page.keyboard.press("Control+z")
                page.wait_for_timeout(700)
                check("home" in panel(order0[0])["placements"], "Ctrl+Z brings it back")
                # zone look: chips in the top strip, «in chats too»
                page.locator('#panels-z-top [data-zone-view="chips"]').click()
                page.wait_for_timeout(600)
                check(page.locator("#panels-z-top .mp-chip[data-panel-id]").count() >= 2, "top strip switched to chips")
                page.locator("#panels-z-composer [data-zone-chats]").click()
                page.wait_for_timeout(600)
                lay = client.get(base + "/api/pg/panels").json()["settings"]["layout"]["zones"]
                check(lay["top"]["view"] == "chips" and lay["composer"]["chats"] is True, f"zone settings saved {lay['top']} {lay['composer']}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                check(not page.evaluate("MemuPanelsLayout.editing()") and page.locator(".mp-editbar").count() == 0, "Esc leaves the mode")
                # in a chat: the composer strip stays, the top strip hides
                page.evaluate("""() => { const w = document.querySelector('#chat-view-wrap'); w.classList.remove('empty-chat'); }""")
                page.wait_for_timeout(300)
                vis = {x["id"]: x["visible"] for x in page.evaluate("MemuPanelsLayout.zones()")}
                check(vis["composer"] and not vis["top"] and not vis["home"], f"inside a chat only «in chats too» zones stay {vis}")
                page.context.close()

            # ---------------------------------------------------------------- English
            if "3" in PARTS:
                page = open_page(browser, 1440, 900, lang="en", theme="dark", design="glass")
                page.evaluate("MemuPanelsLayout.enter()")
                page.wait_for_timeout(500)
                page.locator('#panels-home .mp-card >> nth=0').hover()
                page.wait_for_timeout(200)
                cyr = page.evaluate("""() => [...document.querySelectorAll('.mp-zone-head, .mp-editbar, .mp-edchrome, .panels-home-head, .mp-zone-end')]
                  .map(e => e.innerText + ' ' + [...e.querySelectorAll('[title],[aria-label]')].map(n => (n.title||'') + (n.getAttribute('aria-label')||'')).join(' '))
                  .filter(t => /[А-Яа-яЁё]/.test(t))""")
                check(not cyr, f"EN: no Cyrillic in the layout UI {cyr[:3]}")
                page.screenshot(path=str(OUT / "r33p-edit-en-glass.png"))
                page.context.close()
            browser.close()
    finally:
        server.should_exit = True
        client.close()
    check(not errors, f"no page errors {errors[:3]}")
    print(json.dumps({"problems": problems, "out": str(OUT)}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
