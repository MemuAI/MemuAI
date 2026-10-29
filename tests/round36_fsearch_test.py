"""Round 36 · fsearch: the right-click menu of «Поиск файлов на ПК» (Quick Access) and file operations on results.

Run: MEMUAI_FSINDEX_NOSTART=1 python -m pytest -q tests/round36_fsearch_test.py
Every file operation runs in a pytest tmp folder (MEMUFILES_TRASH_DIR too). The UI part needs Chromium
(MEMU_TEST_CHROMIUM) and is skipped without it; MEMU_SHOTS_DIR=<dir> also saves screenshots of the menu.
Windows-only actions (Open with…, CF_HDROP copy/cut, Windows properties dialog) are checked for their refusal off
Windows only.
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

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r36-fsearch-"))
os.environ["MEMUAI_FSINDEX_NOSTART"] = "1"

from app import fsindex, fsindex_api, fsindex_ops  # noqa: E402

CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or ""
JS = ROOT / "app" / "pgstatic" / "quick" / "plugins" / "fsearch.js"

@pytest.fixture()
def tree(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(fsindex, "_SERVICE", None)
    root = tmp_path / "root"
    if root.exists():
        import shutil
        shutil.rmtree(root)
    (root / "Проекты" / "memu").mkdir(parents=True)
    (root / "Проекты" / "memu" / "main.py").write_text("print('hi')\n")
    (root / "Отчёт за март.pdf").write_bytes(b"%PDF" + b"x" * 5000)
    (root / "report.docx").write_bytes(b"d" * 2048)
    (root / "notes.txt").write_text("hello")
    fsindex.save_settings({"roots": [str(root)], "live": False, "backend": "own"})
    fsindex.rebuild(wait=True)
    yield root
    fsindex.shutdown()

def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(fsindex_api.router)
    return TestClient(app)

def test_routes_registered():
    c = client()
    assert c.post("/api/fsindex/op", json={"action": "properties", "path": "/nowhere"}).json()["code"] == "not_shown"
    assert c.get("/api/fsindex/op/none").status_code == 404

def test_ops_on_search_results(tree):
    c = client()
    found = c.get("/api/fsindex/search", params={"q": "notes"}).json()
    assert [r["name"] for r in found["results"]] == ["notes.txt"] and "windows" in found
    path = found["results"][0]["path"]
    op = lambda **b: c.post("/api/fsindex/op", json={"lang": "ru", **b})            # noqa: E731

    info = op(action="properties", path=path).json()["info"]
    assert info["size"] == 5 and info["name"] == "notes.txt" and info["modified"]
    folder = c.get("/api/fsindex/search", params={"q": "folder: memu"}).json()["results"][0]["path"]
    stats = op(action="properties", path=folder).json()["info"]["folder"]
    assert stats["files"] == 1 and stats["complete"]

    dup = op(action="duplicate", path=path).json()
    assert dup["status"] == "done" and dup["rows"][0]["name"] == "notes - копия.txt"
    assert (tree / "notes - копия.txt").read_text() == "hello"

    assert "notes - копия.txt" in [r["name"] for r in fsindex.search("копия")]

    z = op(action="compress", path=folder).json()
    assert z["rows"][0]["name"] == "memu.zip" and (tree / "Проекты" / "memu.zip").stat().st_size > 0

    bad = op(action="rename", path=path, name="a/b")
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_chars"
    clash = op(action="rename", path=path, name="report.docx")
    assert clash.status_code == 400 and clash.json()["code"] == "exists"
    ren = op(action="rename", path=path, name="заметки.txt").json()
    assert ren["row"]["name"] == "заметки.txt" and (tree / "заметки.txt").exists() and not (tree / "notes.txt").exists()
    assert [r["name"] for r in fsindex.search("заметки")] == ["заметки.txt"] and fsindex.search("notes.txt") == []

    new_path = ren["row"]["path"]
    gone = op(action="trash", path=new_path).json()
    assert gone["removed"] == [new_path] and not os.path.exists(new_path) and gone["undoable"]
    assert fsindex.search("заметки") == []
    back = op(action="undo", op=gone["op"]).json()
    assert back["status"] == "done" and os.path.exists(new_path)
    assert [r["name"] for r in fsindex.search("заметки")] == ["заметки.txt"]
    assert op(action="undo", op=gone["op"]).json()["code"] == "nothing_to_undo"

def test_ops_safety(tree, monkeypatch):
    c = client()
    op = lambda **b: c.post("/api/fsindex/op", json={"lang": "en", **b})            # noqa: E731

    other = tree / "report.docx"
    r = op(action="trash", path=str(other))
    assert r.status_code == 400 and r.json()["code"] == "not_shown" and other.exists()

    home = os.path.expanduser("~")
    system = os.environ.get("WINDIR", "C:/Windows") if os.name == "nt" else "/usr"
    def forbidden_mutation(*_args, **_kwargs):
        raise AssertionError("Safety tests must not mutate real system folders")
    monkeypatch.setattr(fsindex_ops, "queue", forbidden_mutation)
    monkeypatch.setattr(os, "rename", forbidden_mutation)
    fsindex_api._remember([{"path": home}, {"path": system}])
    for target in (home, system):
        for action, extra in (("trash", {}), ("rename", {"name": "x"})):
            r = op(action=action, path=target, **extra)
            assert r.status_code == 400 and r.json()["code"] == "protected", (target, action)
    assert os.path.isdir(system)

    fsindex_api._remember([{"path": str(tree / "nope.txt")}])
    assert op(action="exec", path=str(other)).status_code == 400
    assert op(action="trash", path="/tmp/x\x00y").json()["code"] == "not_shown"
    assert op(action="trash", path=str(tree / "nope.txt")).status_code == 404
    if os.name != "nt":
        fsindex_api._remember([{"path": str(other)}])
        for action in ("open_with", "clip_copy", "clip_cut", "shell_properties"):
            r = op(action=action, path=str(other))
            assert r.status_code == 400 and r.json()["code"] == "unsupported"
    assert fsindex_ops.message("protected", "en").startswith("This is a system")

def test_frontend_contract():
    js = JS.read_text(encoding="utf-8")

    assert "MutationObserver" not in js and "function bindHost" in js and "function schedule" in js

    assert "/memu-ctxmenu.js?v=4.1" in js and "MemuCtxMenu" in js
    for key in ('"F2"', '"Delete"', '"ContextMenu"', '"F10"', '"KeyC"', '"KeyX"'):
        assert key in js
    for action in ("rename", "duplicate", "compress", "trash", "undo", "properties", "attach", "open_with"):
        assert f'"{action}"' in js or f"'{action}'" in js
    manifest = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "index.json").read_text(encoding="utf-8")
    assert next(row for row in json.loads(manifest)["plugins"] if row["id"] == "fsearch")["v"] == "4.1"

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def _serve(port: int):
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    app = FastAPI()
    app.include_router(fsindex_api.router)
    app.mount("/", StaticFiles(directory=str(ROOT / "app" / "pgstatic"), html=True), name="static")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server

SHOW = "{fresh:true, maxHeight:700, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null}"

async def _ui_flow(base: str, tree: Path, shots: str = "") -> list:
    from playwright.async_api import async_playwright
    from app import themes
    problems: list = []
    state = {"theme": "dark", "lang": "ru"}

    async def route(route_obj, request):
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path.startswith("/api/fsindex/"):
            return await route_obj.continue_()
        if path == "/themes.css":
            return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
        if path == "/api/quick/config":
            return await route_obj.fulfill(json={"prefs": {"theme": state["theme"], "language": state["lang"], "accent_color": "violet",
                                                           "quick_ask_model": "m1"},
                                                 "status": {"native": True, "hotkey": {"ok": True}}, "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}})
        if path == "/api/pg/state":
            return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {}}]})
        if path.startswith("/api/"):
            return await route_obj.fulfill(json={"items": [], "ok": True, "events": [], "seq": 0})
        return await route_obj.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        try:
            for theme, lang in (("dark", "ru"), ("light", "en")):
                checkpoints = []
                state.update(theme=theme, lang=lang)
                page = await browser.new_page(viewport={"width": 720, "height": 640})
                page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
                await page.route("**/*", route)
                await page.goto(base + "/quick/?native=1", wait_until="load")
                await page.wait_for_function("!!window.QuickFileSearch", timeout=15000)
                await page.evaluate("QuickAsk.onShow(" + SHOW + ")")
                await page.wait_for_timeout(300)
                await page.evaluate("QuickFileSearch.open('')")
                await page.keyboard.type("report.docx", delay=15)
                try:
                    await page.wait_for_function("document.querySelectorAll('.fs-row').length === 1", timeout=5000)
                except Exception:
                    problems.append("no rows: " + await page.evaluate("JSON.stringify({lang: MemuQuick.lang(), prefs: MemuQuick.state().prefs, rows: QuickFileSearch.state().items.map(r => r.path)})"))
                    break
                rows = await page.evaluate("[...document.querySelectorAll('.fs-row .tx')].map(e => e.textContent)")
                if rows != ["report.docx"]:
                    problems.append(f"rows {rows}")
                await page.fill("#fs-q", "")
                await page.keyboard.type("*.*", delay=10)
                await page.wait_for_function("document.querySelectorAll('.fs-row').length >= 3", timeout=5000)

                same = await page.evaluate("(() => { const a = document.getElementById('fs-q'); a.dataset.mark = '1'; return true; })()")
                await page.keyboard.press("ArrowDown")
                await page.keyboard.type("x", delay=0)
                await page.keyboard.press("Backspace")
                await page.wait_for_timeout(300)
                checkpoints.append(await page.evaluate("({stage:'typing',query:QuickFileSearch.state().query,input:document.getElementById('fs-q')?.value,names:QuickFileSearch.state().items.map(r=>r.name),focus:document.activeElement?.id})"))
                if not same or await page.evaluate("document.getElementById('fs-q').dataset.mark") != "1":
                    problems.append("the search field was re-rendered")
                if shots:
                    await page.screenshot(path=f"{shots}/before-{theme}.png")

                row = page.locator(".fs-row").nth(0)
                name = await row.locator(".tx").text_content()
                await row.click(button="right")
                await page.wait_for_selector(".mcm.fs-menu", timeout=5000)
                labels = await page.evaluate("[...document.querySelectorAll('.mcm.fs-menu .mcm-label')].map(e => e.textContent)")
                want = ["Открыть", "Переименовать", "Дублировать", "Сжать в ZIP", "Удалить в корзину", "Свойства", "Копировать путь"] if lang == "ru" \
                    else ["Open", "Rename", "Duplicate", "Compress to ZIP", "Move to the Recycle Bin", "Properties", "Copy the path"]
                missing = [w for w in want if w not in labels]
                if missing:
                    problems.append(f"menu lacks {missing}")
                if shots:
                    await page.wait_for_timeout(300)
                    await page.screenshot(path=f"{shots}/menu-{theme}.png")

                await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
                if await page.locator(".mcm").count():
                    problems.append("menu stuck after Esc")
                if not await page.evaluate("!!document.querySelector('.qa-plugin.fs .fs-row')"):
                    problems.append("Esc closed the view together with the menu")
                await row.click(button="right")
                await page.wait_for_selector(".mcm", timeout=3000)
                await page.mouse.click(5, 5)
                await page.wait_for_timeout(250)
                if await page.locator(".mcm").count():
                    details = await page.evaluate("({hit:document.elementFromPoint(5,5)?.id || document.elementFromPoint(5,5)?.className, current:!!MemuCtxMenu.current(), menus:[...document.querySelectorAll('.mcm')].map(e=>({class:e.className,rect:e.getBoundingClientRect().toJSON()}))})")
                    problems.append("menu stuck after an outside click: " + str(details))

                await page.focus("#fs-q")
                await page.keyboard.press("Shift+F10")
                await page.wait_for_selector(".mcm", timeout=3000)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)

                target = page.locator(".fs-row", has_text="notes.txt" if theme == "dark" else "заметки.txt")
                try:
                    await target.wait_for(state="visible", timeout=5000)
                except Exception:
                    details = await page.evaluate("({query:QuickFileSearch.state().query,input:document.getElementById('fs-q')?.value,names:QuickFileSearch.state().items.map(r=>r.name),focus:document.activeElement?.id})")
                    problems.append(f"missing properties target: theme={theme}, lang={lang}, state={details}, checkpoints={checkpoints}")
                    break
                await target.click(button="right")
                await page.wait_for_selector(".mcm", timeout=3000)
                await page.locator(".mcm-item", has_text="Свойства" if lang == "ru" else "Properties").click()
                await page.wait_for_selector(".fs-props", timeout=5000)
                if shots:
                    await page.wait_for_timeout(250)
                    await page.screenshot(path=f"{shots}/props-{theme}.png")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)
                if await page.locator(".fs-props").count():
                    problems.append("properties did not close on Esc")

                if theme == "dark":
                    idx = await page.evaluate("QuickFileSearch.state().items.findIndex(r => r.name === 'notes.txt')")
                    await page.evaluate(f"document.querySelector('.fs-row[data-i=\"{idx}\"]').click()")
                    await page.focus("#fs-q")
                    await page.keyboard.press("F2")
                    await page.wait_for_selector(".fs-rename", timeout=3000)
                    sel = await page.evaluate("(() => { const f = document.querySelector('.fs-rename'); return f.value.slice(f.selectionStart, f.selectionEnd); })()")
                    if sel != "notes":
                        problems.append(f"rename selects {sel!r}")
                    if shots:
                        await page.wait_for_timeout(250)
                        await page.screenshot(path=f"{shots}/rename-{theme}.png")
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type("заметки.txt")
                    await page.keyboard.press("Enter")
                    await page.wait_for_function("[...document.querySelectorAll('.fs-row .tx')].some(e => e.textContent === 'заметки.txt')", timeout=5000)
                    if not (tree / "заметки.txt").exists():
                        problems.append("rename did not happen on disk")

                    await page.locator(".fs-row", has_text="report.docx").click(button="right")
                    await page.locator(".mcm-item", has_text="Дублировать").click()
                    await page.wait_for_function("[...document.querySelectorAll('.fs-row .tx')].some(e => e.textContent === 'report - копия.docx')", timeout=8000)

                    await page.focus("#fs-q")
                    await page.keyboard.press("End")
                    await page.keyboard.press("Delete")
                    await page.wait_for_selector(".fs-undo", timeout=5000)
                    if (tree / "report - копия.docx").exists():
                        problems.append("Delete did not trash the selected file")
                    if shots:
                        await page.wait_for_timeout(250)
                        await page.screenshot(path=f"{shots}/undo-{theme}.png")
                    await page.locator(".fs-undo .qx-link").click()
                    await page.wait_for_function("[...document.querySelectorAll('.fs-row .tx')].some(e => e.textContent === 'report - копия.docx')", timeout=8000)
                    if not (tree / "report - копия.docx").exists():
                        problems.append("undo did not restore the file")
                if theme == "light":

                    await page.evaluate("""(() => { const items = QuickFileSearch.state().items.slice(0, 3); QuickFileSearch.close();
                        MemuQuick.emit('tool-ui', {ui: {type: 'fsresults', query: 'report', results: items}}); })()""")
                    await page.wait_for_selector(".fs-ai .fs-row", timeout=3000)
                    await page.locator(".fs-ai .fs-row").nth(0).click(button="right")
                    await page.wait_for_selector(".mcm.fs-menu", timeout=3000)
                    if shots:
                        await page.wait_for_timeout(300)
                        await page.screenshot(path=f"{shots}/ai-card-menu-{theme}.png")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(250)
                    if await page.locator(".mcm").count():
                        problems.append("AI card menu stuck after Esc")
                await page.close()
        finally:
            await browser.close()
    return problems

@pytest.mark.skipif(not CHROME or not os.path.exists(CHROME), reason="needs Chromium (MEMU_TEST_CHROMIUM)")
def test_ui_menu_flow(tree):
    port = _free_port()
    server = _serve(port)
    shots = os.environ.get("MEMU_SHOTS_DIR", "")
    if shots:
        os.makedirs(shots, exist_ok=True)
    try:
        problems = asyncio.run(_ui_flow(f"http://127.0.0.1:{port}", tree, shots))
    finally:
        server.should_exit = True
    assert problems == [], "\n".join(problems)
