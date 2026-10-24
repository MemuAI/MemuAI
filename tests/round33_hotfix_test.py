"""Hotfix after Round 33.

1. MemuNote opened off-screen: a window closed while minimised reported x = y = -32000 and window.json kept it,
   so the next start showed MemuNote in the taskbar (with a live preview) but nowhere on the desktop.
2. Quick Access «Найти модель…» (and the Ctrl+K action search) accepted no typing: ``input.onkeydown = (e) =>
   listKeys(…)`` returned ``false`` for ordinary keys, and an on-handler returning false cancels the key.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import os
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.memunote import desktop, winsys  # noqa: E402

SCREEN = [(0, 0, 1920, 1040)]
TWO = [(0, 0, 1920, 1040), (1920, 0, 3840, 1040)]


# ---------------------------------------------------------------- 1. MemuNote placement

def test_parked_minimised_position_is_dropped():
    g = desktop.sane_geometry({"x": -32000, "y": -32000, "width": 1180, "height": 780})
    assert g["x"] is None and g["y"] is None
    g = desktop.sane_geometry({"x": -32000, "y": -32000, "width": 160, "height": 28}, SCREEN)
    assert g["x"] is None and g["width"] >= 560 and g["height"] >= 400


def test_good_position_kept_and_bad_values_ignored():
    g = desktop.sane_geometry({"x": 200, "y": 120, "width": 1000, "height": 700}, SCREEN)
    assert (g["x"], g["y"], g["width"], g["height"]) == (200, 120, 1000, 700)
    for bad in ({"x": "a", "y": 5}, {"x": None, "y": 3}, {"x": True, "y": 1}, [], None, "x"):
        g = desktop.sane_geometry(bad, SCREEN)
        assert g["x"] is None and g["y"] is None


def test_position_on_a_disconnected_monitor_is_dropped():
    saved = {"x": 2500, "y": 100, "width": 1000, "height": 700}
    assert desktop.sane_geometry(saved, TWO)["x"] == 2500          # second monitor present
    g = desktop.sane_geometry(saved, SCREEN)                        # unplugged
    assert g["x"] is None and g["y"] is None


def test_caption_must_be_reachable():
    assert winsys.rect_visible(100, 100, 800, SCREEN)
    assert not winsys.rect_visible(100, -600, 800, SCREEN)          # caption above the screen
    assert not winsys.rect_visible(1900, 100, 800, SCREEN)          # only 20 px visible
    assert winsys.rect_visible(-500, 10, 800, SCREEN)               # 300 px still grabbable


def test_huge_saved_size_shrinks_to_the_screen_when_recentred():
    g = desktop.sane_geometry({"x": -32000, "y": -32000, "width": 5000, "height": 3000}, SCREEN)
    assert g["width"] <= 1920 and g["height"] <= 1040


def test_closing_minimised_keeps_the_restored_rectangle():
    data = desktop.closing_geometry("Minimized", (-32000, -32000, 160, 28), (300, 200, 1100, 760), {})
    assert data == {"maximized": False, "x": 300, "y": 200, "width": 1100, "height": 760}
    # no placement API: the previous good placement survives instead of -32000
    prev = {"x": 50, "y": 60, "width": 900, "height": 600, "maximized": False}
    data = desktop.closing_geometry("Minimized", (-32000, -32000, 160, 28), None, prev)
    assert data["x"] == 50 and data["y"] == 60 and data["width"] == 900
    # a parked previous value is never carried over
    data = desktop.closing_geometry("Minimized", (-32000, -32000, 160, 28), None, {"x": -32000, "y": -32000})
    assert "x" not in data and "y" not in data


def test_closing_normal_and_maximised():
    data = desktop.closing_geometry("Normal", (10, 20, 1000, 700), None, {})
    assert data == {"maximized": False, "x": 10, "y": 20, "width": 1000, "height": 700}
    data = desktop.closing_geometry("Maximized", (-8, -8, 1936, 1056), (100, 80, 1000, 700), {})
    assert data["maximized"] is True and data["x"] == 100
    data = desktop.closing_geometry("Minimized", (-32000, -32000, 160, 28), (100, 80, 1000, 700), {"maximized": True})
    assert data["maximized"] is True                                  # minimised from maximised stays maximised


def test_window_is_pulled_back_on_show_and_on_activate():
    src = (ROOT / "app/memunote/desktop.py").read_text(encoding="utf-8")
    assert "sane_geometry(read_json(" in src and "winsys.ensure_on_screen(desktop.hwnd())" in src
    assert "closing_geometry(" in src and "x=window.x, y=window.y)" not in src
    ws = (ROOT / "app/memunote/winsys.py").read_text(encoding="utf-8")
    body = ws.split("def bring_to_front", 1)[1].split("\ndef ", 1)[0]
    assert "ensure_on_screen(hwnd)" in body                          # the second launch's hand-off fixes a running one


def test_helpers_are_noops_off_windows():
    if os.name == "nt":
        pytest.skip("Windows")
    assert winsys.monitor_work_areas() == []
    assert winsys.normal_rect(123) is None
    assert winsys.ensure_on_screen(123) is False


# ---------------------------------------------------------------- 2. Quick Access search fields

QUICK = ROOT / "app/pgstatic/quick/quick.js"


def test_listkeys_never_cancels_typing():
    src = QUICK.read_text(encoding="utf-8")
    body = src.split("function listKeys(", 1)[1].split("\n  }\n", 1)[0]
    assert "return false" not in body
    assert not re.search(r"\.onkeydown\s*=\s*\(e\)\s*=>\s*listKeys\(", src)


def _chrome():
    for path in ("/usr/bin/chromium", "/workspace/work/chromium/chrome"):
        if os.path.exists(path):
            return path
    return ""


@pytest.mark.skipif(not _chrome(), reason="no chromium")
def test_typing_into_model_and_action_search():
    pw_mod = pytest.importorskip("playwright.async_api")
    from app import themes
    static = ROOT / "app" / "pgstatic"
    models = [{"id": "reform-qwen3.8-flash", "caps": {"vision": False}}, {"id": "gpt-6.1-sol", "caps": {"vision": True}},
              {"id": "claude-opus-4-6", "caps": {}}]

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    async def run():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(static)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        prefs = {"theme": "dark", "language": "ru", "quick_ask_model": "reform-qwen3.8-flash"}

        async def route(r, req):
            path = "/" + req.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return await r.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return await r.fulfill(json={"prefs": prefs, "status": {"native": True, "pinned": False, "hotkey": {"ok": True}},
                                             "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
            if path == "/api/pg/state":
                return await r.fulfill(json={"models": models, "image_models": []})
            if path.startswith("/api/"):
                return await r.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0,
                                             "settings": {"enabled": True, "voice": {"enabled": False}}})
            return await r.continue_()

        out = {}
        async with pw_mod.async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=_chrome(), args=["--no-sandbox"])
            page = await browser.new_page(viewport={"width": 720, "height": 640})
            await page.route("**/*", route)
            await page.goto(base + "/quick/?native=1", wait_until="load")
            await page.wait_for_timeout(400)
            await page.evaluate("QuickAsk.onShow({fresh:true,maxHeight:640,rounded:true,pinned:false,source:'hotkey',page:null})")
            await page.wait_for_timeout(400)
            await page.click("#qa-model")
            await page.wait_for_timeout(250)
            await page.keyboard.type("gpt", delay=30)
            await page.wait_for_timeout(200)
            out["picker"] = await page.evaluate("document.getElementById('qa-picker-q').value")
            out["rows"] = await page.evaluate("document.querySelectorAll('#qa-picker-list .qa-pick').length")
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+k")
            await page.wait_for_timeout(250)
            await page.keyboard.type("скрин", delay=30)
            out["menu"] = await page.evaluate("(document.getElementById('qa-menu-q')||{}).value")
            await browser.close()
        server.shutdown()
        return out

    out = asyncio.run(run())
    assert out["picker"] == "gpt" and out["rows"] == 1
    assert out["menu"] == "скрин"


# ---------------------------------------------------------------- 3. Computer Use pause → HTTP 422

def test_stuck_decide_accepts_the_users_answer():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import stuck_api

    seen = []

    class Gate:
        def decide(self, payload):
            seen.append(payload)
            return {"decided": payload["decision"]}

    app = FastAPI()
    original = stuck_api.gate_of
    stuck_api.gate_of = lambda runtime: Gate()
    try:
        stuck_api.register(app, runtimes={"computer": object(), "browser": object()})
        client = TestClient(app)
        for decision, hint in (("continue", ""), ("hint", "поле ввода уже активно — пиши текст"), ("replan", ""), ("stop", "")):
            r = client.post("/api/stuck/decide", json={"source": "computer", "decision": decision, "hint": hint})
            assert r.status_code == 200, r.text
        assert seen[1]["hint"].startswith("поле ввода")
        assert client.post("/api/stuck/decide", json={"source": "x", "decision": "continue"}).status_code == 400
        assert client.get("/api/stuck/state").status_code == 200
    finally:
        stuck_api.gate_of = original


def _route_annotation_problems():
    """Every FastAPI route parameter annotation must resolve in its module's globals (PEP 563 strings)."""
    import ast
    import builtins

    def names(body, out):
        for n in body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                out.update(a.asname or a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.Assign):
                out.update(x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Try):
                names(n.body, out); names(n.orelse, out); names(n.finalbody, out)
                for h in n.handlers:
                    names(h.body, out)
            elif isinstance(n, ast.If):
                names(n.body, out); names(n.orelse, out)

    bad = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "from __future__ import annotations" not in src:
            continue
        tree = ast.parse(src)
        known: set = set()
        names(tree.body, known)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and d.func.attr in ("get", "post", "put", "delete", "patch", "websocket", "api_route")
                       for d in fn.decorator_list):
                continue
            for arg in fn.args.args + fn.args.kwonlyargs:
                if arg.annotation is None:
                    continue
                missing = [x.id for x in ast.walk(arg.annotation) if isinstance(x, ast.Name)
                           and x.id not in known and not hasattr(builtins, x.id)]
                if missing:
                    bad.append(f"{path.relative_to(ROOT)}:{fn.lineno} {fn.name}({arg.arg}: {missing})")
    return bad


def test_no_route_has_an_unresolvable_annotation():
    assert _route_annotation_problems() == []


# ---------------------------------------------------------------- 4. Computer Use clicking a focused field again

def test_focus_outcome_tells_the_model_to_type():
    from app import automation_runtime as ar
    field = {"type": "edit", "name": "Написать сообщение…", "text": True, "key": "edit||42|300,900"}
    first = ar.focus_outcome({"type": "pane", "text": False, "key": "pane"}, field)
    assert "ready" in first and "type" in first and "Написать сообщение" in first
    again = ar.focus_outcome(field, dict(field))
    assert "ALREADY" in again and "Do NOT click it again" in again
    assert ar.focus_outcome(None, {"type": "button", "text": False}) == ""
    assert ar.focus_outcome(None, None) == ""


def test_user_hint_stays_in_every_prompt():
    from app import automation_runtime as ar

    class R:
        pass
    r = R()
    assert ar.user_notes_prompt(r) == ""
    ar.remember_user_note(r, "У ТЕБЯ УЖЕ ПОЛЕ ВВОДА АКТИВНО")
    ar.remember_user_note(r, "У ТЕБЯ УЖЕ ПОЛЕ ВВОДА АКТИВНО")
    for i in range(7):
        ar.remember_user_note(r, f"n{i}")
    text = ar.user_notes_prompt(r)
    assert text.startswith("<user_notes") and "n6" in text and len(r._user_notes) == 5
    src = (ROOT / "app/automation_runtime.py").read_text(encoding="utf-8")
    assert "+ user_notes_prompt(runtime)" in src and "remember_user_note(runtime, hint)" in src
    assert "the next action is type, never the same click again" in ar.computer_system_prompt("pixels", [{"monitor": 1, "width": 1400, "height": 788}])


# ---------------------------------------------------------------- 5. panel drag left the drop zones on

def test_panel_drag_always_ends():
    src = (ROOT / "app/pgstatic/panels.js").read_text(encoding="utf-8")
    start = src.split("function onDragStart(", 1)[1].split("\n  }\n", 1)[0]
    assert "if (state.drag !== drag) return;" in start and "armDragWatchdog()" in start
    assert 'document.addEventListener("pointermove", endStaleDrag, true)' in src


@pytest.mark.skipif(not _chrome(), reason="no chromium")
def test_panel_drag_browser():
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("uvicorn")
    import subprocess
    env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    r = subprocess.run([sys.executable, str(ROOT / "tests/round33_hotfix_panels_browser.py")], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
