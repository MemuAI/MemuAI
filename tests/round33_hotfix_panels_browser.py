"""Hotfix after Round 33: a panel drag must never leave the «Перетащите панель сюда» zones on.

    PYTHONUTF8=1 NO_PROXY=127.0.0.1,localhost python3 tests/round33_hotfix_panels_browser.py

Real MemuAI window (temporary profile) in headless Chromium:
  1. press-and-release: dragstart + dragend in the same task (before the next frame) — the late frame used to switch
     body.mp-dragging on for good;
  2. the dragged card is re-rendered during the drag (its dragend fires on a detached node) — the watchdog ends it;
  3. a normal drag with the mouse still works (zones appear while dragging, gone after the drop).
Exit code 1 on any problem.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="r33hp-data-"))
os.environ["MEMUAI_DATA_DIR"] = str(DATA)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")
try:
    import webview  # noqa: F401
except ImportError:
    import types
    sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)

sys.argv = [sys.argv[0], tempfile.mkdtemp(prefix="r33hp-shots-"), "none"]
import round33_panels_browser as base  # noqa: E402  (reuses its seed + helpers; PART "none" runs nothing there)

problems: list = []

def check(ok, what):
    print("ok  " if ok else "FAIL:", what)
    if not ok:
        problems.append(what)

def main() -> int:
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app.playground_api import create_app
    base.seed()
    port = base.free_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    import httpx
    client = httpx.Client(timeout=30, trust_env=False)
    client.post(url + "/api/pg/preferences", json={"language": "ru", "theme": "dark", "ui_motion": "reduced",
                                                   "profile_onboarded": True, "profile_name": "Alex"})
    errors: list = []
    with sync_playwright() as pw:
        chrome = next((p for p in ("/usr/bin/chromium", "/workspace/work/chromium/chrome") if os.path.exists(p)), None)
        browser = pw.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1700, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/", wait_until="domcontentloaded")
        page.wait_for_selector("body.boot-ready", state="attached", timeout=60000)
        page.wait_for_selector("#panels-home .mp-card", timeout=15000)
        page.wait_for_timeout(700)
        dragging = "document.body.classList.contains('mp-dragging')"

        page.evaluate("""() => { const el = document.querySelector('#panels-home [data-panel-id][draggable=true]');
          const dt = new DataTransfer();
          el.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
          el.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt})); }""")
        page.wait_for_timeout(300)
        check(not page.evaluate(dragging), "press-and-release: no zones left on")
        check(page.locator(".mp-drag-source").count() == 0, "press-and-release: no faded source card")

        page.evaluate("""() => { const el = document.querySelector('#panels-home [data-panel-id][draggable=true]');
          el.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: new DataTransfer()}));
          window.__copy = el.cloneNode(true); }""")
        page.wait_for_timeout(150)
        check(page.evaluate(dragging), "drag in progress: zones shown")
        page.evaluate("() => { const el = document.querySelector('.mp-drag-source'); el.replaceWith(window.__copy); }")
        page.wait_for_timeout(2200)
        check(not page.evaluate(dragging), "orphaned drag (source re-rendered): the watchdog switched the zones off")
        check(page.locator(".mp-placeholder").count() == 0, "orphaned drag: no placeholder left")

        page.evaluate("""() => { const el = document.querySelector('#panels-home [data-panel-id][draggable=true]');
          el.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: new DataTransfer()})); }""")
        page.wait_for_timeout(120)
        page.mouse.move(400, 400)
        page.mouse.move(420, 410)
        page.wait_for_timeout(150)
        check(not page.evaluate(dragging), "pointer back with no button: drag over")

        src = page.locator('#panels-z-left .mp-card[data-panel-id="p_c00000001"]')
        if src.count():
            src.drag_to(page.locator("#panels-home"))
            page.wait_for_timeout(700)
            check(not page.evaluate(dragging), "real drag: zones gone after the drop")
        check(not errors, f"no page errors {errors[:3]}")
        browser.close()
    server.should_exit = True
    print("problems:", len(problems))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
