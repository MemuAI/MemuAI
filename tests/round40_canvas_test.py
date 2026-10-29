"""Round 40 (r40-canvas) — «Холст 2.0»: multi-file artifacts, preview assembly + line map, file tools, exports,
diagram / slides types, the window wiring and the error bridge in headless Chromium (no network).

Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x MEMU_TEST_CHROMIUM=/path/to/chrome \
     python -m pytest -q tests/round40_canvas_test.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import canvas as C  # noqa: E402
from app import canvas_files as F  # noqa: E402

SITE = [
    ("index.html", '<!doctype html>\n<html><head>\n<link rel="stylesheet" href="style.css">\n</head><body>\n'
                   '<h1 id="t">Hi</h1>\n<img src="logo.svg">\n<script src="./app.js"></script>\n</body></html>'),
    ("style.css", "h1 { color: red; }\n"),
    ("app.js", "const a = 1;\nfetch('data.json').then(r => r.json()).then(d => { document.title = 'n=' + d.n; });\nmissingFn();\n"),
    ("data.json", '{"n": 7}'),
    ("logo.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'),
]

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path / "canvas")
    return tmp_path

def test_pack_unpack_round_trip_exact():
    files = [("a.txt", ""), ("b/c.js", "x\n"), ("d.md", "# t\n\n"), ("e", "last")]
    text = F.pack(files)
    assert F.is_bundle(text) and F.unpack(text) == files
    assert F.unpack("plain text") is None and not F.is_bundle("⟦file: x⟧ but not a line")

def test_clean_path_rejects_unsafe():
    assert F.clean_path("./src\\app.js") == "src/app.js"
    for bad in ("../x", "C:/x", "a/../../b", "", "a<b", "⟦x⟧"):
        with pytest.raises(ValueError):
            F.clean_path(bad)
    with pytest.raises(ValueError):
        F.pack([("a.js", "1"), ("A.js", "2")])
    with pytest.raises(ValueError):
        F.pack([("a.js", "⟦file: b.js⟧\nboom")])

def test_normalize_files_shapes():
    assert F.normalize_files({"index.html": "<p>", "data.json": {"a": 1}}) == [("index.html", "<p>"), ("data.json", '{\n  "a": 1\n}')]
    assert F.normalize_files([{"name": "a.js", "code": "```js\nx()\n```"}]) == [("a.js", "x()")]

def test_find_file_and_file_of_line():
    files = [("index.html", "a\nb"), ("js/app.js", "1\n2\n3")]
    assert F.find_file(files, "app.js") == 1 and F.find_file(files, "INDEX.HTML") == 0 and F.find_file(files, "x") == -1
    text = F.pack(files)
    assert F.file_of_line(text, 0) == ("index.html", 0)
    assert F.file_of_line(text, 2) == ("index.html", 2)
    assert F.file_of_line(text, 5) == ("js/app.js", 2)

def test_assemble_inlines_and_maps_lines():
    out = F.assemble(SITE)
    html = out["html"]
    assert '<style data-memu-file="style.css">' in html and '<script data-memu-file="app.js">' in html
    assert "href=\"style.css\"" not in html and "src=\"./app.js\"" not in html
    assert "data:image/svg+xml" in html
    assert out["files"]["data.json"] == '{"n": 7}' and "index.html" not in out["files"]
    lines = html.split("\n")

    def where(n):
        hit = None
        for row in out["map"]:
            if row["from"] <= n <= row["to"]:
                hit = row
        return hit["path"], hit["line"] + n - hit["from"]

    n = next(i for i, l in enumerate(lines, 1) if l.strip() == "missingFn();")
    assert where(n) == ("app.js", 3)
    n = next(i for i, l in enumerate(lines, 1) if "color: red" in l)
    assert where(n) == ("style.css", 1)
    n = next(i for i, l in enumerate(lines, 1) if '<h1 id="t">' in l)
    assert where(n) == ("index.html", 5)

def test_assemble_unreferenced_and_no_entry():
    out = F.assemble([("index.html", "<html><body><p>x</p></body></html>"), ("extra.css", "p{}"), ("main.js", "go()")])
    assert out["html"].index("extra.css") < out["html"].index("</body>") and "go()" in out["html"]
    js_only = F.assemble([("app.js", "x()"), ("s.css", "b{}")])
    assert '<div id="app"></div>' in js_only["html"] and js_only["html"].index("s.css") < js_only["html"].index("x()")
    assert F.assemble([("main.py", "print(1)")])["html"] == ""
    assert "</script" not in F.assemble([("a.js", "s='</script>'")])["html"].split('data-memu-file="a.js">', 1)[1].split("\n</script>")[0]

def test_tool_create_multi_edit_file_write_read(store):
    res = C.run_tool("c1", "canvas_create", {"type": "html", "title": "Site", "files": [{"path": p, "content": c} for p, c in SITE[:3]]})
    assert res["ok"] and res["files"] == ["index.html", "style.css", "app.js"] and res["type"] == "html"
    aid = res["id"]
    e = C.run_tool("c1", "canvas_edit", {"id": aid, "file": "app.js", "edits": [{"find": "missingFn();", "replace": "start();"}]})
    assert e["ok"] and e["file"] == "app.js" and e["version"] == 2
    bad = C.run_tool("c1", "canvas_edit", {"id": aid, "file": "nope.js", "edits": [{"find": "a", "replace": "b"}]})
    assert bad["code"] == "unknown_file" and "app.js" in bad["error"]

    e2 = C.run_tool("c1", "canvas_edit", {"id": aid, "edits": [{"find": "color: red", "replace": "color: blue"}]})
    assert e2["ok"]
    boundary = C.run_tool("c1", "canvas_edit", {"id": aid, "edits": [{"find": "⟦file: style.css⟧", "replace": "x"}]})
    assert boundary["code"] == "file_boundary"
    w = C.run_tool("c1", "canvas_write_file", {"id": aid, "path": "data.json", "content": '{"n": 1}'})
    assert w["ok"] and w["done"] == "add" and "data.json" in w["files"]
    r = C.run_tool("c1", "canvas_read", {"id": aid, "file": "data.json"})
    assert r["content"] == '{"n": 1}' and r["file"] == "data.json" and "index.html" in r["files"]
    ren = C.run_tool("c1", "canvas_write_file", {"id": aid, "path": "data.json", "rename_to": "db.json"})
    assert ren["ok"] and "db.json" in ren["files"]
    rm = C.run_tool("c1", "canvas_write_file", {"id": aid, "path": "db.json", "delete": True})
    assert rm["ok"] and "db.json" not in rm["files"]
    art = C.get("c1", aid)
    assert [v["review"] for v in art["versions"]][-1] == "pending"
    summ = C.summary(art)
    assert [f["path"] for f in summ["files"]] == ["index.html", "style.css", "app.js"]

def test_single_artifact_becomes_multi_on_write_file(store):
    art = C.create("c2", "html", "Page", "<html><body>x</body></html>")
    res = C.write_file("c2", art["id"], "style.css", "body{}")
    files = F.unpack(C.get("c2", art["id"])["versions"][-1]["content"])
    assert [p for p, _c in files] == ["index.html", "style.css"] and files[0][1] == "<html><body>x</body></html>"
    assert res["op"] == "add"

    old = C.create("c2", "document", "Doc", "# Hi\ntext")
    assert "files" not in C.summary(C.get("c2", old["id"]))
    assert C.edit("c2", old["id"], [{"find": "text", "replace": "body"}])["version"] == 2

def test_review_hunks_of_multi_file_artifact(store):
    art = C.create("c3", "html", "S", "", files=[("index.html", "<p>1</p>"), ("app.js", "a();\nb();\n")])
    C.edit("c3", art["id"], [{"find": "b();", "replace": "c();"}], file="app.js")
    C.edit("c3", art["id"], [{"find": "<p>1</p>", "replace": "<p>2</p>"}], file="index.html")
    res = C.resolve("c3", art["id"], 3, decision="reject")
    files = dict(F.unpack(C.get("c3", art["id"])["versions"][-1]["content"]))
    assert res["rejected"] == 1 and files["index.html"] == "<p>1</p>" and files["app.js"] == "a();\nc();\n"

def test_context_block_lists_files(store):
    art = C.create("c4", "html", "S", "", files=[("index.html", "<p>hi</p>"), ("app.js", "go()")])
    block = C.context_block("c4", focus=art["id"])
    assert 'files="index.html, app.js"' in block and '<file path="app.js"' in block and "go()" in block
    assert "canvas_write_file" in C.PROMPT and "Multi-file" in C.PROMPT and '"diagram"' in C.PROMPT

def test_exports_zip_html_file(store):
    art = C.create("c5", "html", "Мой сайт", "", files=SITE)
    full = C.get("c5", art["id"])
    row = full["versions"][-1]
    data, name, media = C.export_bytes(full, row, "")
    assert name == "Мой сайт.zip" and media == "application/zip"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == sorted(f"Мой сайт/{p}" for p, _c in SITE)
        assert zf.read("Мой сайт/data.json").decode() == '{"n": 7}'
    html, name, _ = C.export_bytes(full, row, "html")
    assert name.endswith(".html") and b"data-memu-file" in html
    one, name, _ = C.export_bytes(full, row, "file:app.js")
    assert name == "app.js" and one.startswith(b"const a")
    folder = store / "exp"
    main = C.write_export(full, row, folder)
    assert main.name == "index.html" and (main.parent / "app.js").read_text(encoding="utf-8").startswith("const a")

def test_types_diagram_and_slides(store):
    assert C.normalize_type("mermaid") == "diagram" and C.normalize_type("presentation") == "slides"
    assert C.normalize_type("", "", "mermaid") == "diagram"
    d = C.run_tool("c6", "canvas_create", {"type": "diagram", "title": "", "content": "```mermaid\nflowchart TD\n A-->B\n```"})
    assert d["ok"] and d["type"] == "diagram" and d["title"] == "Схема"
    art = C.get("c6", d["id"])
    assert art["versions"][0]["content"].startswith("flowchart TD")
    assert C.export_bytes(art, art["versions"][0])[1] == "Схема.mmd"
    s = C.create("c6", "slides", "Deck", "# T\n---\n## A\n- b")
    assert C.export_bytes(C.get("c6", s["id"]), C.get("c6", s["id"])["versions"][0])[1] == "Deck.md"

@pytest.fixture()
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    C.register(app)
    return TestClient(app)

def test_routes_preview_file_diff_engine(client):
    art = client.post("/api/pg/canvas/r1/create", json={"type": "html", "title": "S", "files": [{"path": p, "content": c} for p, c in SITE[:3]]}).json()["artifact"]
    aid = art["id"]
    pv = client.get(f"/api/pg/canvas/r1/{aid}/preview").json()
    assert pv["ok"] and pv["entry"] == "index.html" and any(r["path"] == "app.js" for r in pv["map"])
    add = client.post(f"/api/pg/canvas/r1/{aid}/file", json={"path": "about.html", "content": "<p>a</p>", "base": 1}).json()
    assert add["ok"] and add["version"] == 2 and "about.html" in [f["path"] for f in add["artifact"]["files"]]
    bad = client.post(f"/api/pg/canvas/r1/{aid}/file", json={"path": "../evil", "content": "x"})
    assert bad.status_code == 400
    diff = client.get(f"/api/pg/canvas/r1/{aid}/diff?v=2").json()
    assert diff["hunks"] and diff["hunks"][0]["file"] == "about.html"
    z = client.get(f"/api/pg/canvas/r1/{aid}/export?fmt=zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    js = client.get("/api/pg/canvas-engine/slides/pptx.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "savePptx" in js.text
    css = client.get("/api/pg/canvas-engine/slides/render.css")
    assert css.status_code == 200 and ".sl-slide" in css.text and ".sl-app" not in css.text
    assert client.get("/api/pg/canvas-engine/slides/view.js").status_code == 404
    assert client.get("/api/pg/canvas-engine/slides/..%2Fcore.js").status_code == 404

def test_window_wiring_r40():
    js = (ROOT / "app/pgstatic/canvas.js").read_text(encoding="utf-8")
    kinds = (ROOT / "app/pgstatic/canvas-kinds.js").read_text(encoding="utf-8")
    app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
    assert "canvas_(?:create|edit|read|write_file)" in app_js and "canvas_(?:create|edit|read|write_file)" in js
    assert "/canvas.js?v=3.6-r40" in html and "/canvas.css?v=3.6-r40" in html
    assert "canvas-kinds.js" not in html
    assert 'sandbox="allow-scripts"' in js and "allow-same-origin" not in js
    assert "sendPrepared(text" in js and "Исправить с ИИ" in js and "Fix with AI" in js
    assert "setInterval" not in kinds and "MutationObserver" not in kinds
    assert "/vendor/mermaid/mermaid.min.js" in kinds and "https://" not in kinds.split("*/", 1)[1]
    spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
    build = (ROOT / "build.py").read_text(encoding="utf-8")
    assert "'app.canvas_files'" in spec and '"app.canvas_files"' in build
    assert "app/memunote/static/js/slides" in spec and "app/memunote/static/js/slides" in build
    for name in C.SLIDES_ENGINE:
        assert (ROOT / "app/memunote/static/js/slides" / name).exists()

def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node

def test_js_bundle_mirror_and_slides_parser_in_node():
    node = _node()
    script = r"""
global.window = global; global.localStorage = {getItem(){return null}, setItem(){}};
global.document = {addEventListener(){}, querySelector(){return null}, querySelectorAll(){return []}, body:{classList:{add(){},remove(){}}}, head:{appendChild(){}}};
window.addEventListener = () => {};
require(process.argv[1]); require(process.argv[2]);
const K = window.MemuCanvasKinds;
const s = K._parseSlides("theme: ocean\n# Title\nSub\n\n---\n## Two\n- a\n  - b\nNotes: say hi\n---\n## T\n| A | B |\n|---|---|\n| 1 | 2 |\n---\n# End");
console.log(JSON.stringify({s, ren: K._renameNode("flowchart TD\n  A[Old] --> B(x)\n", "B", 'N "q"'), add: K._renameNode("graph LR\n A-->C\n", "C", "New"),
  doc: window.MemuCanvas._sandboxDoc("<p>x</p>", "N", {"data.json": "{\"a\":\"</script>\"}"})}));
"""
    res = subprocess.run([node, "-e", script, str(ROOT / "app/pgstatic/canvas.js"), str(ROOT / "app/pgstatic/canvas-kinds.js")],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    s = out["s"]
    assert s["theme"] == "ocean" and len(s["slides"]) == 4
    assert s["slides"][0] == {"bullets": [], "title": "Title", "layout": "title", "subtitle": "Sub", "notes": ""}
    assert s["slides"][1]["bullets"] == ["a", "  b"] and s["slides"][1]["notes"] == "say hi" and s["slides"][1]["layout"] == "content"
    assert s["slides"][2]["layout"] == "table" and s["slides"][2]["table"] == [["A", "B"], ["1", "2"]]
    assert s["slides"][3]["layout"] == "section"
    assert 'B("N #quot;q#quot;")' in out["ren"] and 'C["New"]' in out["add"]
    assert '"data.json"' in out["doc"] and "</script>\"}" not in out["doc"].split("<script>", 1)[1].split("</script>", 1)[0]

def _chromium():
    path = os.environ.get("MEMU_TEST_CHROMIUM", "")
    if not path or not Path(path).exists():
        pytest.skip("set MEMU_TEST_CHROMIUM")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright is not installed")
    return path

def test_preview_error_bar_maps_file_line_in_chromium(store):
    chrome = _chromium()
    import threading
    import time
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    app = FastAPI()
    C.register(app)

    @app.get("/")
    def index():
        return HTMLResponse('<!doctype html><html><head><link rel="stylesheet" href="/canvas.css"></head><body>'
                            '<div id="main-content" style="display:flex;height:100vh"><div id="chat-view-wrap" style="flex:1"></div></div>'
                            '<textarea id="input"></textarea><script>var S={currentId:"w1",messages:[],chats:[]};'
                            'window.sent=[];async function sendPrepared(t){window.sent.push(t);document.getElementById("input").value="";}</script>'
                            '<script src="/canvas.js"></script></body></html>')
    app.mount("/", StaticFiles(directory=str(ROOT / "app/pgstatic")), name="static")
    art = C.create("w1", "html", "Site", "", files=SITE)
    port = 18793
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    from playwright.sync_api import sync_playwright
    try:
        for _ in range(50):
            try:
                import httpx
                if httpx.get(f"http://127.0.0.1:{port}/api/pg/canvas/w1", trust_env=False).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/")
            page.evaluate(f"MemuCanvas.open('{art['id']}')")
            page.wait_for_selector("#cv-errbar:not([hidden])", timeout=8000)
            bar = page.inner_text("#cv-errbar")
            assert "app.js:3" in bar and "missingFn" in bar

            frame = page.frame_locator("#cv-frame")
            assert frame.locator("h1").inner_text() == "Hi"
            sandbox = next(f for f in page.frames if f != page.main_frame)
            for _ in range(40):
                if sandbox.evaluate("document.title") == "n=7":
                    break
                time.sleep(0.05)
            assert sandbox.evaluate("document.title") == "n=7"
            page.click('[data-cv-act="err-fix"]')
            sent = page.evaluate("window.sent")
            assert len(sent) == 1 and "app.js" in sent[0] and "missingFn();" in sent[0] and "строка 3" in sent[0]
            page.click('[data-cv-act="err-goto"]')
            assert page.evaluate("document.querySelector('#cv-ed').value.split('\\n')[2]") == "missingFn();"
            assert page.evaluate("document.querySelector('.cv-file.is-on .cv-file-name').textContent") == "app.js"
            browser.close()
    finally:
        server.should_exit = True

def test_live_link_sync_and_push(store):
    import time as _t
    art = C.create("l1", "document", "Заметка", "# Hi\nfirst")
    full = C.get("l1", art["id"])
    path = C.write_export(full, full["versions"][-1], store / "exp")
    C.link_artifact("l1", art["id"], path, 1)
    assert C.sync_links("l1") == []

    C.edit("l1", art["id"], [{"find": "first", "replace": "second"}])
    assert C.push_link("l1", art["id"]) and path.read_text(encoding="utf-8") == "# Hi\nsecond"
    assert not C.push_link("l1", art["id"])

    _t.sleep(0.02)
    path.write_text("# Hi\nfrom MemuNote", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    synced = C.sync_links("l1")
    row = C.get("l1", art["id"])["versions"][-1]
    assert synced == [{"id": art["id"], "version": row["v"]}] and row["author"] == "user" and row["summary"] == "MemuNote"
    assert row["content"] == "# Hi\nfrom MemuNote" and C.sync_links("l1") == []
    assert C.summary(C.get("l1", art["id"]))["link"]["path"] == str(path)

def test_live_link_multi_file_folder(store):
    art = C.create("l2", "html", "Сайт", "", files=[("index.html", "<p>1</p>"), ("js/app.js", "a()")])
    full = C.get("l2", art["id"])
    main = C.write_export(full, full["versions"][-1], store / "exp")
    link = C.link_artifact("l2", art["id"], main, 1)
    assert Path(link["root"]).name == "Сайт"
    js = Path(link["root"]) / "js" / "app.js"
    js.write_text("b()", encoding="utf-8")
    os.utime(js, (js.stat().st_atime, js.stat().st_mtime + 5))
    assert C.sync_links("l2")
    files = dict(F.unpack(C.get("l2", art["id"])["versions"][-1]["content"]))
    assert files == {"index.html": "<p>1</p>", "js/app.js": "b()"}
