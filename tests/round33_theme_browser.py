"""Round 33 (r33-theme, item #28): the theme editor in the real MemuAI window (app.playground_api on a temporary profile)
driven by headless Chromium.

    PYTHONUTF8=1 NO_PROXY=127.0.0.1,localhost python3 tests/round33_theme_browser.py [OUT_DIR]

Checks (exit code 1 on any problem; screenshots r33t-*.png in OUT_DIR):
  * Ctrl+Shift+U opens the editor; hovering a message bubble and clicking it selects «user message»;
  * a corner slider changes the bubble live; Ctrl+Z / Ctrl+Shift+Z undo and redo it;
  * Escape with many changes asks first, a second Escape discards everything (bubble and sidebar side reverted,
    nothing written to preferences);
  * Layout tab: a top-bar row dragged by its grip is reordered (also from the bottom of a scrolled list, with
    auto-scroll), Alt+↑ moves a focused row; hiding the context meter keeps the custom order of the other buttons;
  * Save writes ``ui_look``; after a reload the layout is applied from the first frame;
  * the floating inspector dragged to the bottom of a small window stays entirely visible (footer included);
  * the English editor has no Cyrillic.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="r33t-shots-"))
DATA = Path(tempfile.mkdtemp(prefix="r33t-data-"))
os.environ["MEMUAI_DATA_DIR"] = str(DATA)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
try:
    import webview  # noqa: F401
except ImportError:
    import types
    sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)

NOW = time.time()
CYR = re.compile(r"[А-Яа-яЁё]")
problems: list = []

def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        problems.append(what)

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def seed():
    from app import pgdata
    msgs = [{"role": "user", "content": "How do I make my own theme?", "createdAt": int(NOW * 1000) - 60000},
            {"role": "assistant", "content": "Open the **theme editor** and click any element.", "model": "test/model",
             "createdAt": int(NOW * 1000) - 50000}]
    for i in range(4):
        pgdata.upsert_chat({"id": f"c{i}", "title": f"Chat {i}", "messages": msgs, "mode": "chat",
                            "createdAt": int(NOW * 1000) - i * 1000, "updatedAt": int(NOW * 1000) - i * 1000})

ORDER_JS = "() => [...document.querySelectorAll('.te-order-item')].map(n => n.dataset.item)"
BAR_JS = """() => [...document.querySelector('.topbar').children].filter(n => n.offsetParent)
  .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(n => n.id || n.className.split(' ')[0])"""
RADIUS_JS = "() => getComputedStyle(document.querySelector('.msg-user .bubble')).borderTopLeftRadius"

def main():
    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app.playground_api import create_app
    seed()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    post = lambda path, body: httpx.post(base + path, json=body, timeout=30, trust_env=False).json()  # noqa: E731
    get = lambda path: httpx.get(base + path, timeout=30, trust_env=False).json()  # noqa: E731
    errors = []

    def page_for(browser, w=1366, h=768, lang="en"):
        post("/api/pg/preferences", {"language": lang, "theme": "dark", "ui_design": "classic", "ui_motion": "off",
                                     "onboarding_done": True, "profile_onboarded": True})
        ctx = browser.new_context(viewport={"width": w, "height": h})
        ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (e) {{}}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("body.boot-ready", timeout=60000, state="attached")
        page.wait_for_timeout(700)
        if page.locator("#onboarding-name").count():
            page.fill("#onboarding-name", "Alex")
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
        page.evaluate("() => document.querySelector(\"[data-chat='c0']\")?.click()")
        page.wait_for_timeout(600)
        return page

    def drag_grip(page, item, target_y, settle=0):
        g = page.locator(f".te-order-item[data-item='{item}'] [data-grip]")
        g.scroll_into_view_if_needed()
        b = g.bounding_box()
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x, target_y, steps=10)
        if settle:
            page.wait_for_timeout(settle)
        return x

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
        try:
            p = page_for(browser)
            r0 = p.evaluate(RADIUS_JS)
            p.keyboard.press("Control+Shift+U")
            p.wait_for_timeout(500)
            check(p.evaluate("() => MemuThemeEditor.isOpen()"), "Ctrl+Shift+U opens the editor")
            b = p.locator(".msg-user .bubble").first.bounding_box()
            p.mouse.move(b["x"] + 20, b["y"] + 10)
            p.wait_for_timeout(150)
            p.mouse.click(b["x"] + 20, b["y"] + 10)
            p.wait_for_timeout(300)
            check(p.evaluate("() => MemuThemeEditor.state.sel") == "msg-user" or
                  p.locator(".te-props [data-range='el:radius']").count() == 1, "clicking a bubble selects it")
            p.evaluate("""() => { const r = document.querySelector("[data-range='el:radius']"); r.value = 3;
              r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); }""")
            p.wait_for_timeout(200)
            check(p.evaluate(RADIUS_JS) == "3px", f"radius slider applies live ({r0} → {p.evaluate(RADIUS_JS)})")
            p.screenshot(path=str(OUT / "r33t-element.png"))
            p.mouse.move(600, 400)
            p.keyboard.press("Control+z")
            p.wait_for_timeout(200)
            check(p.evaluate(RADIUS_JS) == r0, "Ctrl+Z undoes")
            p.keyboard.press("Control+Shift+z")
            p.wait_for_timeout(200)
            check(p.evaluate(RADIUS_JS) == "3px", "Ctrl+Shift+Z redoes")
            text = p.locator(".te-panel").inner_text()
            check(not CYR.search(text), "English editor (element tab) has no Cyrillic")

            p.click(".te-tabs [data-tab='layout']")
            p.wait_for_timeout(300)
            check(not CYR.search(p.locator(".te-panel").inner_text()), "English editor (layout tab) has no Cyrillic")
            ctx_box = p.locator(".te-order-item[data-item='model']")
            ctx_box.scroll_into_view_if_needed()
            nb = p.locator(".te-order-item[data-item='model']").bounding_box()
            x = drag_grip(p, "help", nb["y"] + 4)
            p.mouse.up()
            p.wait_for_timeout(300)
            order = p.evaluate(ORDER_JS)
            check(order.index("help") == 1, f"grip drag reorders the top bar list: {order}")
            bar = p.evaluate(BAR_JS)
            check("btn-help" in bar and bar.index("btn-help") < bar.index("model-dd"), f"the top bar follows: {bar}")
            check(p.evaluate("() => document.activeElement?.dataset?.item") == "help", "the moved row keeps focus")
            p.keyboard.press("Alt+ArrowDown")
            p.wait_for_timeout(250)
            check(p.evaluate(ORDER_JS).index("help") == 2, "Alt+↓ moves the focused row")

            p.set_viewport_size({"width": 1366, "height": 470})
            p.wait_for_timeout(300)
            p.evaluate("""() => { const b = document.querySelector('.te-body'), r = document.querySelector(".te-order-item[data-item='theme']");
              b.scrollTop += r.getBoundingClientRect().bottom - b.getBoundingClientRect().bottom + 4; }""")
            p.wait_for_timeout(150)
            body = p.locator(".te-body").bounding_box()
            st0 = p.evaluate("() => document.querySelector('.te-body').scrollTop")
            x = drag_grip(p, "theme", body["y"] + 6, settle=1600)
            st1 = p.evaluate("() => document.querySelector('.te-body').scrollTop")
            lst = p.evaluate("() => document.querySelector('.te-order').getBoundingClientRect().top - document.querySelector('.te-body').getBoundingClientRect().top")
            check(st1 < st0 and 0 <= lst <= 60, f"the inspector auto-scrolls while dragging and stops at the list top ({st0} → {st1}, list at +{lst:.0f}px)")
            first = p.evaluate("() => { const n = document.querySelector(\".te-order-item[data-item='nav']\"); const r = n.getBoundingClientRect(); return r.top + r.height / 2 - (new DOMMatrix(getComputedStyle(n).transform).m42 || 0); }")
            p.mouse.move(x, first - 8, steps=3)
            p.mouse.up()
            p.wait_for_timeout(300)
            order = p.evaluate(ORDER_JS)
            check(order[0] == "theme", f"dragging from the bottom auto-scrolls to the top: {order}")
            p.set_viewport_size({"width": 1366, "height": 768})
            p.wait_for_timeout(200)
            p.click("[data-switch='h:context']")
            p.wait_for_timeout(300)
            bar = p.evaluate(BAR_JS)
            check("context-meter" not in bar and bar and bar[0] == "btn-theme", f"hiding the context meter keeps the order: {bar}")
            orders = p.evaluate("() => [...document.querySelector('.topbar').children].filter(n => n.offsetParent).map(n => getComputedStyle(n).order)")
            check(len(set(orders)) > 1, f"top bar items keep distinct orders: {orders}")
            p.click("[data-seg='l:side_pos'] [data-v='right']")
            p.wait_for_timeout(300)
            check(p.evaluate("() => document.documentElement.dataset.teSide") == "right", "sidebar moves right")
            p.screenshot(path=str(OUT / "r33t-layout.png"))

            n = p.evaluate("() => MemuThemeEditor.state.changes")
            p.mouse.move(600, 400)
            p.keyboard.press("Escape")
            p.wait_for_timeout(300)
            check(p.evaluate("() => MemuThemeEditor.isOpen() && MemuThemeEditor.state.confirm"), f"Esc with {n} changes asks first")
            p.screenshot(path=str(OUT / "r33t-confirm.png"))
            p.keyboard.press("Escape")
            p.wait_for_timeout(400)
            check(not p.evaluate("() => MemuThemeEditor.isOpen()"), "second Esc closes")
            check(p.evaluate(RADIUS_JS) == r0, "Esc reverted the element change")
            check(p.evaluate("() => document.documentElement.dataset.teSide || 'left'") == "left", "Esc reverted the sidebar side")
            check(get("/api/pg/preferences").get("ui_look", {}).get("toolbar", {}).get("order") == [], "nothing saved after Esc")

            p.evaluate("() => MemuThemeEditor.open({tab: 'layout'})")
            p.wait_for_timeout(400)
            p.click("[data-seg='l:side_pos'] [data-v='right']")
            p.wait_for_timeout(150)
            p.locator(".te-order-item[data-item='theme']").focus()
            for _ in range(3):
                p.keyboard.press("Alt+ArrowUp")
                p.wait_for_timeout(120)
            p.click(".te-foot [data-act='save']")
            p.wait_for_timeout(900)
            look = get("/api/pg/preferences")["ui_look"]
            check(look["layout"]["side_pos"] == "right" and look["toolbar"]["order"].index("theme") == 5,
                  f"Save writes ui_look: {look['layout']['side_pos']} {look['toolbar']['order']}")
            p.reload()
            p.wait_for_selector("body.boot-ready", state="attached")
            p.wait_for_timeout(900)
            check(p.evaluate("() => document.documentElement.dataset.teSide") == "right", "the layout survives a reload")
            p.context.close()

            p = page_for(browser, 900, 600)
            p.evaluate("() => MemuThemeEditor.open({tab: 'look'})")
            p.wait_for_timeout(500)
            hb = p.locator(".te-head").bounding_box()
            p.mouse.move(hb["x"] + 80, hb["y"] + 20)
            p.mouse.down()
            p.mouse.move(hb["x"] - 250, 590, steps=10)
            p.mouse.up()
            p.wait_for_timeout(400)
            geo = p.evaluate("""() => { const r = document.querySelector('.te-panel').getBoundingClientRect(), f = document.querySelector('.te-foot').getBoundingClientRect();
              return {dock: MemuThemeEditor.state.dock, top: r.top, bottom: r.bottom, foot: f.bottom, vh: innerHeight}; }""")
            check(geo["dock"] == "float" and geo["top"] >= 0 and geo["foot"] <= geo["vh"], f"the floating panel stays inside the window: {geo}")
            p.set_viewport_size({"width": 900, "height": 460})
            p.wait_for_timeout(400)
            geo = p.evaluate("() => { const r = document.querySelector('.te-panel').getBoundingClientRect(); return {top: r.top, bottom: r.bottom, vh: innerHeight}; }")
            check(geo["top"] >= 0 and geo["bottom"] <= geo["vh"], f"…and after the window shrinks: {geo}")
            p.screenshot(path=str(OUT / "r33t-float.png"))
            p.keyboard.press("Escape")
            p.context.close()
        finally:
            browser.close()
            server.should_exit = True
    for e in errors:
        print("page:", e)
    check(not [e for e in errors if "pageerror" in e], "no page errors")
    print("screenshots:", OUT)
    return 1 if problems else 0

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    sys.exit(main())
