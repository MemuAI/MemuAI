"""Round 40, second wave (r40-files-2): MemuFiles integration fixes found by a live Windows-11-style audit.

Backend: Ctrl+Y redo (OpQueue.redo_stack / redo(), /api/ops/redo; a new action ends the chain; a single in-place rename
is replayed), saved searches in /api/sidebar («smart folders»). UI (headless Chromium, real backend): R/S/B in the
conflict dialog work after ticking «for all» (the modal's capture listener used to swallow them), the Menu key opens
the menu of the selection (not the background menu), «new folder» right after an operation keeps its inline rename box,
Esc in a dialog gives the keyboard back to the list, Ctrl+Z → Ctrl+Y, the «sort by meaning» card is labelled (not a raw
i18n key), moved files leave the selection at once, saved searches in the sidebar.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


def wait(cond, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def done(op):
    wait(lambda: op.status in ("done", "error", "cancelled"))
    return op


def test_redo_replays_undone_operations_and_new_action_ends_chain(tmp_path):
    from app.memufiles.opqueue import OpQueue
    from app.memufiles.fsops import FsError
    q = OpQueue()
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")
    done(q.submit("move", [str(src / "a.txt")], str(dst)))
    assert (dst / "a.txt").exists()
    done(q.undo())
    assert (src / "a.txt").exists() and not (dst / "a.txt").exists()
    redo = done(q.redo())
    assert redo.status == "done" and (dst / "a.txt").exists()
    done(q.undo())                                        # the redone move is undoable again
    assert (src / "a.txt").exists()
    # a single in-place rename (recorded, not submitted) is replayed from what it recorded
    os.rename(src / "b.txt", src / "c.txt")
    q.record("rename", [str(src / "b.txt")], [("move_back", str(src / "c.txt"), str(src / "b.txt"))], [str(src / "c.txt")])
    done(q.undo())
    assert (src / "b.txt").exists()
    done(q.redo())
    assert (src / "c.txt").exists() and not (src / "b.txt").exists()
    # undo → a new action → nothing to redo (Explorer)
    done(q.undo())
    done(q.submit("mkdir", [str(tmp_path / "new")]))
    with pytest.raises(FsError):
        q.redo()


def call(app, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method="POST" if data is not None else "GET")
    req.add_header("X-MF-Token", app.token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_sidebar_lists_saved_searches_and_redo_route(tmp_path):
    from app.memufiles import server as srv
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        assert call(app, "/api/sidebar")[1]["saved"] == []
        call(app, "/api/x/saved", {"name": "Счета", "query": "счёт", "root": str(tmp_path), "scope": "folder"})
        saved = call(app, "/api/sidebar")[1]["saved"]
        assert [s["name"] for s in saved] == ["Счета"]
        status, body = call(app, "/api/ops/redo", {})
        assert status >= 400 and "nothing_to_redo" in json.dumps(body)
    finally:
        httpd.shutdown()


def test_frontend_wiring():
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    ops_js = (STATIC / "js" / "ops.js").read_text(encoding="utf-8")
    core_js = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    assert 'kl === "y"' in app_js and 'api("ops/redo"' in app_js
    assert "keys(e)" in core_js and "opener.focus" in core_js
    for key in ("opx.k.organize", "opx.d.organize", "op.organize"):
        assert ops_js.count(f'"{key}"') == 2, key                     # en + ru
    assert 'organize: "sparkle"' in ops_js
    assert "opc-graph-label" not in ops_js                            # the speed is in the line above the graph
    css = (STATIC / "ops.css").read_text(encoding="utf-8")
    assert ".modal.cfx-modal" in css                                  # .modal.wide no longer wins (860 px)


def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    if not Path(exe).exists():
        pytest.skip("no chromium")
    return exe


def test_ui_keyboard_conflicts_redo_and_smart_folders(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import fsops, server as srv
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    work, other = tmp_path / "work", tmp_path / "other"
    (work / "sub").mkdir(parents=True)
    other.mkdir()
    for n in ("alpha.txt", "beta.txt", "gamma.md"):
        (work / n).write_text("new " + n, encoding="utf-8")
        (other / n).write_text("old", encoding="utf-8")
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 800}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{app.port}/index.html#t={app.token}")
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("p => MemuFiles.pane().navigate(p)", str(work))
            page.wait_for_function("MemuFiles.pane().shown.length === 4")
            sel = lambda: page.evaluate("() => MemuFiles.pane().selected().map((r) => r.n)")
            page.locator(".fv").focus()
            # the Menu key: the menu of the focused selection, the selection stays
            page.keyboard.type("bet")
            page.keyboard.press("ContextMenu")
            page.wait_for_selector(".menu")
            assert sel() == ["beta.txt"] and "Массовое переименование" in page.inner_text(".menu")
            page.keyboard.press("Escape")
            # Esc in Properties gives the keyboard back to the list
            page.keyboard.press("Alt+Enter")
            page.wait_for_selector(".modal-back")
            page.keyboard.press("Escape")
            assert page.evaluate("() => document.activeElement.classList.contains('fv')")
            # Ctrl+Z → Ctrl+Y (trash, undo, redo)
            page.keyboard.press("Delete")
            wait(lambda: not (work / "beta.txt").exists())
            page.wait_for_timeout(300)
            page.keyboard.press("Control+KeyZ")
            wait(lambda: (work / "beta.txt").exists())
            page.wait_for_timeout(300)
            page.keyboard.press("Control+KeyY")
            wait(lambda: not (work / "beta.txt").exists())
            # «new folder» right after an operation (its «changed» event re-reads the folder) keeps the rename box
            page.wait_for_timeout(150)
            page.keyboard.press("Control+Shift+KeyN")
            page.wait_for_function("document.activeElement && document.activeElement.classList.contains('rename-input')", timeout=5000)
            page.keyboard.type("Папка 2026")
            page.keyboard.press("Enter")
            wait(lambda: (work / "Папка 2026").is_dir())
            # conflicts: tick «for all», then R — every file replaced, no second dialog
            page.evaluate("([s, d]) => MemuFiles.transfer(s, d, 'copy')", [[str(work / n) for n in ("alpha.txt", "gamma.md")], str(other)])
            page.wait_for_selector(".cfx-cards")
            page.check(".modal-foot input[type=checkbox]")
            page.keyboard.press("KeyR")
            wait(lambda: all((other / n).read_text(encoding="utf-8").startswith("new") for n in ("alpha.txt", "gamma.md")))
            page.wait_for_timeout(300)
            assert page.locator(".cfx-cards").count() == 0
            # «sort by meaning» card: a real title and the sparkle icon, never «opx.k.organize»
            page.evaluate("""p => { MemuFiles.onOp({ id: 'fx-org', rev: 1, kind: 'organize', status: 'running', sources: [p + '/x.txt'], count: 1, dest: p,
                unit: 'items', total_items: 5, done_items: 2, total_bytes: 0, done_bytes: 0, errors: [], error_count: 0, speed_hist: [], created: Date.now() / 1000 });
                MemuFiles.opsUi.toggle(true); }""", str(work))
            page.wait_for_selector(".opc[data-op=fx-org] .opc-title")
            title = page.inner_text(".opc[data-op=fx-org] .opc-title")
            assert "Раскладка по смыслу" in title and "opx." not in title
            assert "opx." not in page.inner_text(".ops-ind")
            page.evaluate("() => { MemuFiles.opsUi.remove(MemuFiles.ops.get('fx-org')); MemuFiles.opsUi.toggle(false); }")
            # a moved file leaves the selection as soon as its operation finishes
            page.evaluate("() => MemuFiles.pane().reload(true)")
            page.wait_for_timeout(400)
            page.locator(".row", has_text="gamma.md").first.click(position={"x": 60, "y": 12})
            page.wait_for_timeout(400)                    # the preview has read the file
            page.evaluate("([s, d]) => MemuFiles.transfer(s, d, 'move')", [[str(work / "gamma.md")], str(work / "sub")])
            page.wait_for_function("MemuFiles.pane().sel.size === 0", timeout=5000)
            # saved searches are «smart folders» in the sidebar
            page.evaluate("r => import('/js/core.js').then((c) => c.api('x/saved', { name: 'Тексты', query: '.txt', root: r, scope: 'folder' })).then(() => MemuFiles.loadSidebar())", str(work))
            page.wait_for_selector(".side-item >> text=Тексты")
            page.click(".side-item >> text=Тексты")
            page.wait_for_function("MemuFiles.pane().mode === 'search'")
            assert not errors, errors
            browser.close()
    finally:
        httpd.shutdown()
