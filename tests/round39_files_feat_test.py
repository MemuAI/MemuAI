"""Round 39 (r39-files-feat): new MemuFiles features — folder compare, image conversion, document templates,
recent files, saved searches, code editors, batch-rename tokens, and the UI (Quick Look, compare dialog,
Home extras, «Копировать как»). No network, no Windows needed."""
import json
import os
import re
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"
SHOTS = os.environ.get("MEMU_SHOTS_DIR", "")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


@pytest.fixture()
def server(tmp_path):
    from app.memufiles import server as srv
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    yield app
    httpd.shutdown()


def call(app, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method="POST" if body is not None else "GET")
    req.add_header("X-MF-Token", app.token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def run_job(kind, args):
    from app.memufiles.jobs import Jobs
    job = Jobs().start(kind, args)
    deadline = time.time() + 20
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.02)
    assert job.status == "done", job.error
    return job.result


def make_pair(base: Path):
    left, right = base / "L", base / "R"
    for root in (left, right):
        (root / "same").mkdir(parents=True)
        (root / "same" / "x.txt").write_text("same", encoding="utf-8")
        os.utime(root / "same" / "x.txt", (1_700_000_000, 1_700_000_000))
    (left / "only-left").mkdir()
    (left / "only-left" / "deep.txt").write_text("d", encoding="utf-8")
    (right / "только справа.md").write_text("r", encoding="utf-8")
    (left / "doc.txt").write_text("new version", encoding="utf-8")
    (right / "doc.txt").write_text("old", encoding="utf-8")
    os.utime(left / "doc.txt", (1_700_100_000, 1_700_100_000))
    os.utime(right / "doc.txt", (1_700_000_000, 1_700_000_000))
    (left / "c.bin").write_bytes(b"aaaa")
    (right / "c.bin").write_bytes(b"bbbb")
    for p in (left / "c.bin", right / "c.bin"):
        os.utime(p, (1_700_000_000, 1_700_000_000))
    return left, right


# ------------------------------------------------------------------ backend
def test_compare_states_and_collapsed_one_side_folders(tmp_path):
    left, right = make_pair(tmp_path)
    res = run_job("compare", {"left": str(left), "right": str(right)})
    states = {r["rel"]: r["state"] for r in res["rows"]}
    assert states["only-left"] == "left"
    assert "only-left/deep.txt" not in states                 # the folder row stands for its contents
    assert states["только справа.md"] == "right"
    assert states["doc.txt"] == "newer_left"
    assert states["same/x.txt"] == "same"
    assert states["c.bin"] == "same"                          # same size + time: equal without content check
    deep = run_job("compare", {"left": str(left), "right": str(right), "content": True})
    assert {r["rel"]: r["state"] for r in deep["rows"]}["c.bin"] == "differ"
    flat = run_job("compare", {"left": str(left), "right": str(right), "recursive": False})
    assert "same/x.txt" not in {r["rel"] for r in flat["rows"]}


def test_imgconvert_resize_format_and_subfolder(tmp_path):
    from PIL import Image
    src = tmp_path / "pics"
    src.mkdir()
    Image.new("RGBA", (800, 400), (255, 0, 0, 128)).save(src / "a.png")
    Image.new("RGB", (100, 50), (0, 255, 0)).save(src / "b.jpg")
    (src / "broken.png").write_bytes(b"not an image")
    res = run_job("imgconvert", {"paths": [str(src / n) for n in ("a.png", "b.jpg", "broken.png")],
                                 "format": "jpg", "max_side": 200, "quality": 80, "subfolder": "Преобразованные"})
    assert len(res["done"]) == 2 and len(res["failed"]) == 1
    with Image.open(src / "Преобразованные" / "a.jpg") as im:
        assert im.format == "JPEG" and im.size == (200, 100)
    with Image.open(src / "Преобразованные" / "b.jpg") as im:
        assert im.size == (100, 50)                           # smaller than max_side: not upscaled
    res = run_job("imgconvert", {"paths": [str(src / "b.jpg")], "format": "keep", "max_side": 40, "subfolder": ""})
    assert Path(res["done"][0]).name == "b (40).jpg"          # same folder never overwrites the original


def test_new_documents_are_valid_office_files(server, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    for name in ("Новый документ.docx", "Новая таблица.xlsx", "Новая презентация.pptx"):
        status, res = call(server, "/api/x/newdoc", {"parent": str(folder), "name": name})
        assert status == 200, res
        with zipfile.ZipFile(res["path"]) as zf:
            assert zf.testzip() is None and "[Content_Types].xml" in zf.namelist()
    status, res = call(server, "/api/x/newdoc", {"parent": str(folder), "name": "Новый документ.docx"})
    assert Path(res["path"]).name == "Новый документ (2).docx"
    status, res = call(server, "/api/x/newdoc", {"parent": str(folder), "name": "tool.py"})
    assert '"""tool."""' in Path(res["path"]).read_text(encoding="utf-8")
    status, res = call(server, "/api/x/newdoc", {"parent": str(folder), "name": "bad/name.txt"})
    assert status >= 400
    try:                                                      # deep check when python-docx/openpyxl happen to exist
        import openpyxl
        openpyxl.load_workbook(folder / "Новая таблица.xlsx")
    except ImportError:
        pass


def test_recent_saved_and_editors_endpoints(server, tmp_path, monkeypatch):
    from app.memufiles import extras, fsops
    monkeypatch.setattr(extras, "IS_WIN", False)
    f = tmp_path / "отчёт.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    assert call(server, "/api/open", {"path": str(f)})[0] == 200
    assert call(server, "/api/open", {"path": str(f)})[0] == 200
    status, rec = call(server, "/api/x/recent")
    assert [r["path"] for r in rec["items"]] == [str(f)]        # deduplicated, newest first
    call(server, "/api/x/recent", {"clear": True})
    assert call(server, "/api/x/recent")[1]["items"] == []
    status, saved = call(server, "/api/x/saved", {"name": "Большие видео", "query": "kind:video size:>1gb", "scope": "everywhere"})
    assert saved["items"][0]["scope"] == "everywhere" and saved["items"][0]["name"] == "Большие видео"
    status, saved = call(server, "/api/x/saved", {"remove": saved["items"][0]["id"]})
    assert saved["items"] == []
    # editors: a fake «code» on PATH is found and launched with the path
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "code"
    exe.write_text("#!/bin/sh\necho \"$@\" > \"%s\"\n" % (tmp_path / "launched.txt"), encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(extras, "_editor_candidates", lambda: [{"id": "vscode", "name": "VS Code", "exe": str(exe)}])
    launched = []
    monkeypatch.setattr(extras.subprocess, "Popen", lambda command, **opts: launched.append((command, opts)))
    extras._EDITORS["at"] = 0
    status, eds = call(server, "/api/x/editors")
    assert eds["editors"][0]["id"] == "vscode"
    status, res = call(server, "/api/x/editor", {"path": str(tmp_path), "id": "vscode"})
    assert status == 200 and res["editor"] == "VS Code"
    assert launched[0][0] == [str(exe), str(tmp_path)]
    assert launched[0][1]["cwd"] == str(tmp_path)
    status, err = call(server, "/api/x/eject", {"path": str(tmp_path)})
    assert status >= 400 and err["error"] == "unavailable"        # Windows only
    assert call(server, "/api/x/nope", {})[0] == 404


def test_batch_rename_exif_size_created_tokens(tmp_path):
    from PIL import Image
    from app.memufiles import batchrename
    img = tmp_path / "IMG_1.jpg"
    exif = Image.Exif()
    exif[306] = "2024:07:15 10:20:30"
    Image.new("RGB", (64, 32)).save(img, exif=exif)
    rows = batchrename.preview([str(img)], {"pattern": "{exif} {width}x{height}"})
    assert rows[0]["new"] == "2024-07-15 10-20-30 64x32.jpg", rows
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    rows = batchrename.preview([str(txt)], {"pattern": "{name}-{created}-{width}"})
    assert re.fullmatch(r"a-\d{4}-\d{2}-\d{2}-\.txt", rows[0]["new"]), rows


def test_static_strings_and_wiring():
    js = (STATIC / "js" / "extras.js").read_text(encoding="utf-8")
    en = js[js.index("addStrings({"):js.index("addStrings(null, {")]
    ru = js[js.index("addStrings(null, {"):js.index("const VIDEO")]
    assert not re.search(r"[А-Яа-яЁё]", en)
    key = re.compile(r'"([\w.]+)":')
    assert set(key.findall(en)) == set(key.findall(ru))
    assert 'href="extras.css"' in (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "extras.quickLook(this, pane)" in app and "extras.copyAsItems(paths)" in app


# ------------------------------------------------------------------ UI (headless Chromium)
def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    if not Path(exe).exists():
        pytest.skip("no chromium")
    return exe


def _shot(page, name):
    if SHOTS:
        time.sleep(0.25)                                      # let the open animation finish
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name))


def test_ui_quicklook_compare_home_and_copy_as(server, tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from PIL import Image
    from app.memufiles import fsops
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    folder = tmp_path / "Проект"
    folder.mkdir()
    (folder / "main.py").write_text("def hello(name):\n    return f'Привет, {name}'\n", encoding="utf-8")
    (folder / "README.md").write_text("# Проект\n\nОписание **важно**.\n", encoding="utf-8")
    Image.new("RGB", (640, 360), (60, 120, 200)).save(folder / "фото.png")
    left, right = make_pair(tmp_path)
    call(server, "/api/open", {"path": str(folder / "README.md")})
    call(server, "/api/x/saved", {"name": "Python", "query": "ext:py", "root": str(folder)})
    url = f"http://127.0.0.1:{server.port}/index.html#t={server.token}"
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_selector(".tabbar .tab")
            # Home: recent files + saved searches + compare card
            page.wait_for_selector(".x-recent .x-rrow")
            assert page.locator(".x-saved", has_text="Python").count() == 1
            assert page.locator(".home-card", has_text="Сравнение папок").count() == 1
            _shot(page, "home.png")
            # Quick Look on Space: code, then → markdown, image
            page.evaluate("p => window.MemuFiles.pane().navigate(p)", str(folder))
            page.locator(".row", has_text="main.py").first.click()
            page.keyboard.press(" ")
            page.wait_for_selector(".ql .pv-code")
            assert "hello" in page.inner_text(".ql .pv-code")
            _shot(page, "quicklook-code.png")
            page.keyboard.press("ArrowRight")                 # Explorer order (r40): main.py, README.md, фото.png
            page.wait_for_selector(".ql .ql-md h1")
            page.keyboard.press("End")
            page.wait_for_selector(".ql .ql-stage.media img")
            page.wait_for_function("document.querySelector('.ql .ql-stage img').naturalWidth > 0")
            _shot(page, "quicklook-image.png")
            page.keyboard.press("Delete")                     # shortcuts are off while Quick Look is open
            page.keyboard.press("Escape")
            assert page.locator(".ql").count() == 0
            assert (folder / "фото.png").exists()
            # «Копировать путь как» submenu
            page.locator(".row", has_text="main.py").first.click(button="right")
            page.hover(".menu >> text=Копировать путь как")
            page.wait_for_selector(".menu >> text=Ссылка file://")
            assert page.locator(".menu >> text=Для Bash / WSL").count() == 1
            page.keyboard.press("Escape")
            fmts = page.evaluate("() => import('/js/extras.js').then(m => m.pathFormats('C:\\\\Users\\\\Я\\\\a b.txt'))")
            assert fmts["uri"] == "file:///C:/Users/%D0%AF/a%20b.txt" and fmts["bash"] == "'/mnt/c/Users/Я/a b.txt'"
            assert fmts["ps"] == "'C:\\Users\\Я\\a b.txt'" and fmts["stem"] == "a b"
            # compare dialog
            page.evaluate("([a, b]) => import('/js/extras.js').then(m => m.compareDialog(window.MemuFiles, a, b))", [str(left), str(right)])
            page.wait_for_selector(".cmp-row.st-newer_left")
            assert page.locator(".cmp-row.st-same").count() == 0          # default filter: differences
            _shot(page, "compare.png")
            page.click(".cmp-actions >> text=Синхронизировать")
            deadline = time.time() + 8
            while time.time() < deadline and not ((right / "only-left" / "deep.txt").exists() and (left / "только справа.md").exists()
                                                  and (right / "doc.txt").read_text(encoding="utf-8") == "new version"):
                time.sleep(0.1)
            assert (right / "only-left" / "deep.txt").exists() and (left / "только справа.md").exists()
            assert (right / "doc.txt").read_text(encoding="utf-8") == "new version"
            page.click(".cmp-modal .modal-head .icon-btn")
            # image conversion dialog from the context menu
            page.locator(".row", has_text="фото.png").first.click(button="right")
            page.click(".menu >> text=Преобразовать изображения…")
            page.wait_for_selector(".modal >> text=Большая сторона")
            _shot(page, "imgconvert.png")
            page.click(".modal .btn.primary")
            deadline = time.time() + 8
            while time.time() < deadline and not (folder / "Преобразованные" / "фото.jpg").exists():
                time.sleep(0.1)
            assert (folder / "Преобразованные" / "фото.jpg").exists()
            page.keyboard.press("Escape")
            # «Что занимает место»: squarified treemap
            (tmp_path / "big").mkdir()
            (tmp_path / "big" / "v.bin").write_bytes(b"0" * 400_000)
            page.evaluate("p => import('/js/tools.js').then(m => m.usageDialog(window.MemuFiles, p))", str(tmp_path))
            page.wait_for_selector(".treemap.sq div")
            assert page.locator(".treemap.sq div", has_text="big").count() == 1
            _shot(page, "usage-treemap.png")
            assert not errors, errors
        finally:
            browser.close()
