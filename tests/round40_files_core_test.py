"""Round 40 (r40-files-core): MemuFiles operations progress and drag & drop.

Backend: honest byte / item progress (unit, speed history, ETA, the current file), skip-a-file, per-file errors with
retry, cancel of a cross-volume move removing the half copy, uploads of path-less files (Explorer zip folders,
Outlook, browsers) with conflicts / cancel / unsafe names, link downloads (local HTTP server, no network), snapshot
revisions, taskbar summary, the desktop drop handler. UI (headless Chromium): a drop of File objects WITHOUT paths
lands in the folder; the «Copy to …» hint never sticks (drop, watchdog, Esc); the operations flyout with progress,
speed graph, pause / resume / cancel; the conflict dialog; the status-bar indicator; light and dark screenshots.
"""
import errno
import http.server
import json
import os
import threading
import time
import urllib.request
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


def call(app, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("X-MF-Token", app.token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def queue_with_events():
    from app.memufiles.opqueue import OpQueue
    snaps = []
    return OpQueue(notify=snaps.append), snaps


def wait(cond, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def wait_op(op, timeout=10.0):
    wait(lambda: op.status in ("done", "error", "cancelled"), timeout)
    return op


def slow_bytes(monkeypatch, delay=0.004, chunk=64 * 1024):
    """Small chunks + a sleep per chunk: progress becomes observable (and pausable) on a fast disk."""
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue, "CHUNK", chunk)
    original = opqueue.Operation.add_bytes

    def add_bytes(self, count):
        time.sleep(delay)
        original(self, count)
    monkeypatch.setattr(opqueue.Operation, "add_bytes", add_bytes)


# ------------------------------------------------------------------ backend: progress
def test_copy_byte_progress_speed_history_eta_and_current_file(tmp_path, monkeypatch):
    slow_bytes(monkeypatch, delay=0.012)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    (src / "big.bin").write_bytes(os.urandom(6 * 1024 * 1024))
    (src / "small.txt").write_text("x" * 100)
    queue, snaps = queue_with_events()
    op = queue.submit("copy", [str(src / "big.bin"), str(src / "small.txt")], str(dst))
    wait_op(op, 30)
    assert op.status == "done" and (dst / "big.bin").stat().st_size == 6 * 1024 * 1024
    running = [s for s in snaps if s["status"] == "running" and s["done_bytes"]]
    assert running, "no progress snapshots"
    assert all(s["unit"] == "bytes" for s in running)
    assert running[0]["total_bytes"] == 6 * 1024 * 1024 + 100          # counted up front
    assert any(s["speed_hist"] for s in running) and any(s["eta"] for s in running)
    assert any(s["current"].endswith("big.bin") and s["cur_size"] == 6 * 1024 * 1024 and 0 < s["cur_done"] for s in running)
    revs = [s["rev"] for s in snaps if s["id"] == op.id]
    assert revs == sorted(revs) and len(set(revs)) == len(revs)          # the page can drop stale replies
    final = op.snapshot()
    assert final["done_bytes"] == final["total_bytes"] and final["elapsed"] > 0 and final["eta"] is None


def test_delete_and_same_volume_move_count_items_not_bytes(tmp_path):
    base = tmp_path / "b"
    (base / "tree" / "sub").mkdir(parents=True)
    for i in range(30):
        (base / "tree" / "sub" / f"f{i}.txt").write_text("data" * 100)
    (base / "dst").mkdir()
    queue, snaps = queue_with_events()
    op = wait_op(queue.submit("move", [str(base / "tree")], str(base / "dst")))
    assert op.status == "done" and (base / "dst" / "tree" / "sub" / "f3.txt").exists()
    snap = op.snapshot()
    assert snap["unit"] == "items" and snap["total_items"] == 1 and snap["done_items"] == 1   # a rename, not a walk
    op = wait_op(queue.submit("delete", [str(base / "dst" / "tree")]))
    snap = op.snapshot()
    assert op.status == "done" and snap["unit"] == "items" and not (base / "dst" / "tree").exists()
    assert snap["done_items"] >= 31 and snap["done_items"] == snap["total_items"]


def _fake_cross_volume(monkeypatch, src_root):
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue.OpQueue, "_same_volume", staticmethod(lambda s, d: False))
    real_rename = os.rename

    def rename(a, b, *args, **kw):
        if str(a).startswith(str(src_root)) and ".~mf" not in str(b):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(a, b, *args, **kw)
    monkeypatch.setattr(opqueue.os, "rename", rename)


def test_cross_volume_move_bytes_and_cancel_removes_half_copy(tmp_path, monkeypatch):
    slow_bytes(monkeypatch, delay=0.01)
    src, dst = tmp_path / "C", tmp_path / "D"
    (src / "folder").mkdir(parents=True); dst.mkdir()
    for i in range(4):
        (src / "folder" / f"part{i}.bin").write_bytes(os.urandom(1024 * 1024))
    _fake_cross_volume(monkeypatch, src)
    queue, snaps = queue_with_events()
    op = queue.submit("move", [str(src / "folder")], str(dst))
    wait(lambda: op.done_bytes > 1.5 * 1024 * 1024, 20)
    assert op.snapshot()["unit"] == "bytes" and op.total_bytes == 4 * 1024 * 1024
    queue.cancel(op.id)
    wait_op(op)
    assert op.status == "cancelled"
    assert not (dst / "folder").exists(), "half copy left behind"
    assert len(list((src / "folder").iterdir())) == 4, "source must stay intact"
    # and a full cross-volume move: copy + delete with byte progress
    op = wait_op(queue.submit("move", [str(src / "folder")], str(dst)), 30)
    assert op.status == "done" and not (src / "folder").exists() and len(list((dst / "folder").iterdir())) == 4
    assert op.snapshot()["done_bytes"] == 4 * 1024 * 1024


def test_skip_current_file_removes_partial_and_continues(tmp_path, monkeypatch):
    slow_bytes(monkeypatch, delay=0.01)
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir(); dst.mkdir()
    (src / "a_big.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    (src / "b.txt").write_text("next")
    queue, _ = queue_with_events()
    op = queue.submit("copy", [str(src / "a_big.bin"), str(src / "b.txt")], str(dst))
    wait(lambda: op.cur_done > 256 * 1024 and op.current.endswith("a_big.bin"), 20)
    queue.skip(op.id)
    wait_op(op, 20)
    assert op.status == "done"
    assert not (dst / "a_big.bin").exists() and (dst / "b.txt").read_text() == "next"
    snap = op.snapshot()
    assert snap["skipped"] == 1 and snap["done_bytes"] == snap["total_bytes"] == 4


@pytest.mark.skipif(os.name == "nt" or os.getuid() == 0, reason="needs POSIX permissions as a normal user")
def test_failed_files_are_listed_and_retried(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    (src / "dir").mkdir(parents=True); dst.mkdir()
    (src / "dir" / "ok.txt").write_text("ok")
    locked = src / "dir" / "locked.txt"
    locked.write_text("secret")
    locked.chmod(0)
    try:
        queue, _ = queue_with_events()
        op = wait_op(queue.submit("copy", [str(src / "dir")], str(dst)))
        snap = op.snapshot()
        assert op.status == "done" and snap["error_count"] == 1 and snap["retryable"]
        assert snap["errors"][0]["path"].endswith("locked.txt")
        locked.chmod(0o644)
        again = wait_op(queue.retry(op.id))
        assert again.status == "done" and (dst / "dir" / "locked.txt").read_text() == "secret"
        assert not op.snapshot()["retryable"]
    finally:
        locked.chmod(0o644)


# ------------------------------------------------------------------ backend: uploads (path-less drops) and downloads
def test_upload_stream_conflict_unsafe_and_undo(server, tmp_path):
    dest = tmp_path / "Цель"
    (dest / "Архив").mkdir(parents=True)
    (dest / "Архив" / "readme.txt").write_text("old")
    items = [{"rel": "Архив", "dir": True}, {"rel": "Архив/readme.txt", "size": 3, "mtime": 1_700_000_000_000},
             {"rel": "Архив/sub/данные.csv", "size": 5}, {"rel": "../evil.txt", "size": 1}, {"rel": "фото.jpg", "size": 4}]
    status, res = call(server, "/api/upload/start", {"dest": str(dest), "items": items, "label": "drop"})
    assert status == 200 and res["op"]["kind"] == "upload"
    op_id = res["op"]["id"]
    op = server.queue.get(op_id)
    # file 1 conflicts with the existing readme.txt: prepare waits for the answer
    result = {}
    worker = threading.Thread(target=lambda: result.update(call(server, "/api/upload/prepare", {"op": op_id, "i": 1})[1]))
    worker.start()
    wait(lambda: op.status == "conflict")
    assert op.conflict["src"]["size"] == 3 and op.conflict["dst"]["name"] == "readme.txt"
    call(server, "/api/ops/control", {"action": "resolve", "id": op_id, "choice": "keep_both"})
    worker.join(10)
    assert result["state"] == "ready"
    status, put = call(server, f"/api/upload/put?op={op_id}&i=1", raw=b"new", headers={"Content-Type": "application/octet-stream"})
    assert put["state"] == "done" and put["path"].endswith("readme (2).txt")
    assert os.path.getmtime(put["path"]) == pytest.approx(1_700_000_000, abs=2)
    status, put = call(server, f"/api/upload/put?op={op_id}&i=2", raw=b"a;b;c")      # prepare happens implicitly
    assert put["state"] == "done" and (dest / "Архив" / "sub" / "данные.csv").read_bytes() == b"a;b;c"
    status, put = call(server, f"/api/upload/put?op={op_id}&i=4", raw=b"\xff\xd8\xff\xe0")
    assert put["state"] == "done"
    wait_op(op)
    snap = op.snapshot()
    assert op.status == "done" and snap["error_count"] == 1 and snap["errors"][0]["error"] == "unsafe_path"
    assert not (tmp_path / "evil.txt").exists()
    assert (dest / "Архив" / "readme.txt").read_text() == "old"
    assert snap["unit"] == "bytes" and snap["done_bytes"] == snap["total_bytes"]
    assert any(p.endswith("фото.jpg") for p in snap["results"])
    status, undo = call(server, "/api/ops/undo", {"id": op_id})
    wait_op(server.queue.get(undo["op"]["id"]))
    assert not (dest / "фото.jpg").exists() and not (dest / "Архив" / "readme (2).txt").exists()
    assert (dest / "Архив" / "readme.txt").exists()                   # the merged folder itself stays


def test_upload_cancel_skip_and_failed_read(server, tmp_path, monkeypatch):
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue, "CHUNK", 64 * 1024)
    dest = tmp_path / "in"
    dest.mkdir()
    items = [{"rel": "a.bin", "size": 3}, {"rel": "gone.txt", "size": 10}, {"rel": "c.bin", "size": 2}]
    _, res = call(server, "/api/upload/start", {"dest": str(dest), "items": items})
    op_id = res["op"]["id"]
    op = server.queue.get(op_id)
    call(server, f"/api/upload/put?op={op_id}&i=0", raw=b"abc")
    call(server, "/api/upload/fail", {"op": op_id, "i": 1, "error": "read failed"})
    assert op.snapshot()["error_count"] == 1
    call(server, "/api/ops/control", {"action": "cancel", "id": op_id})
    _, prep = call(server, "/api/upload/prepare", {"op": op_id, "i": 2})
    assert prep["state"] == "cancelled"
    wait_op(op)
    assert op.status == "cancelled" and (dest / "a.bin").read_bytes() == b"abc" and not (dest / "c.bin").exists()
    # a body shorter than announced never leaves a partial file
    _, res = call(server, "/api/upload/start", {"dest": str(dest), "items": [{"rel": "short.bin", "size": 100}]})
    op2 = server.queue.get(res["op"]["id"])
    reader = type("R", (), {"read": lambda self, n: b""})()
    out = server.queue.upload_put(op2.id, 0, reader, 100)
    assert out["state"] == "error" and not (dest / "short.bin").exists()


def _descriptor(name, size=None, mtime=None, is_dir=False):
    import struct
    flags = (0x4 if is_dir else 0) | (0x40 if size is not None else 0) | (0x20 if mtime else 0)
    ft = int(mtime * 1e7 + 116444736000000000) if mtime else 0
    raw = name.encode("utf-16-le")[:518].ljust(520, b"\0")
    return struct.pack("<I16s8s8sIQQQII", flags, b"", b"", b"", 0x10 if is_dir else 0, 0, 0, ft,
                       (size or 0) >> 32, (size or 0) & 0xFFFFFFFF) + raw


def test_clipboard_virtual_files_parse_and_vpaste(tmp_path):
    import io
    from app.memufiles import clip
    blob = (3).to_bytes(4, "little") + _descriptor("Папка", is_dir=True) + \
        _descriptor("Папка\\внутри.txt", 6, 1_700_000_000) + _descriptor("корень.bin", 4)
    items = clip.parse_group(blob)
    assert len(items) == 3 and len(_descriptor("x")) == clip.DESCRIPTOR_SIZE
    assert items[0] == {"rel": "Папка", "size": -1, "mtime": 0, "dir": True, "index": 0}
    assert items[1]["rel"] == "Папка/внутри.txt" and items[1]["size"] == 6 and items[1]["mtime"] == pytest.approx(1_700_000_000)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "корень.bin").write_bytes(b"old!")
    source = iter([({"summary": True, "count": 3, "bytes": 10}, None), (items[0], None),
                   (items[1], io.BytesIO(b"inside")), (items[2], io.BytesIO(b"new!"))])
    queue, snaps = queue_with_events()
    op = queue.submit("vpaste", [], str(dest), _source=source, on_conflict="keep_both")
    wait_op(op)
    assert op.status == "done", op.errors
    assert (dest / "Папка" / "внутри.txt").read_bytes() == b"inside"
    assert os.path.getmtime(dest / "Папка" / "внутри.txt") == pytest.approx(1_700_000_000, abs=2)
    assert (dest / "корень (2).bin").read_bytes() == b"new!" and (dest / "корень.bin").read_bytes() == b"old!"
    snap = op.snapshot()
    assert snap["unit"] == "bytes" and snap["total_bytes"] == 10 and snap["done_items"] == 3
    assert clip.read_files()["virtual"] == 0 or os.name == "nt"


class _Files(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"PNGDATA" * 1000
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        if "named" in self.path:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%D0%BA%D0%BE%D1%82.png")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_download_of_a_dropped_link(server, tmp_path):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        dest = tmp_path / "dl"
        dest.mkdir()
        port = httpd.server_address[1]
        _, res = call(server, "/api/ops/submit", {"kind": "download", "url": f"http://127.0.0.1:{port}/img/picture", "dest": str(dest)})
        op = wait_op(server.queue.get(res["op"]["id"]))
        assert op.status == "done" and (dest / "picture.png").read_bytes() == b"PNGDATA" * 1000
        _, res = call(server, "/api/ops/submit", {"kind": "download", "url": f"http://127.0.0.1:{port}/named", "dest": str(dest)})
        op = wait_op(server.queue.get(res["op"]["id"]))
        assert op.status == "done" and (dest / "кот.png").exists() and op.snapshot()["done_bytes"] == 7000
        status, bad = call(server, "/api/ops/submit", {"kind": "download", "url": "file:///etc/passwd", "dest": str(dest)})
        assert status == 400 and bad["error"] == "bad_url"
    finally:
        httpd.shutdown()


def test_taskbar_summary_and_desktop_drop_rows(tmp_path):
    from app.memufiles import taskbar
    assert taskbar.summarize([])["state"] == taskbar.TBPF_NOPROGRESS
    s = taskbar.summarize([{"status": "running", "unit": "bytes", "total_bytes": 100, "done_bytes": 50},
                           {"status": "running", "unit": "items", "total_items": 4, "done_items": 4}])
    assert s == {"state": taskbar.TBPF_NORMAL, "done": 750, "total": 1000}
    assert taskbar.summarize([{"status": "conflict", "total_items": 1}])["state"] == taskbar.TBPF_PAUSED
    assert taskbar.summarize([{"status": "scanning"}])["state"] == taskbar.TBPF_INDETERMINATE
    # desktop.py: the pywebview drop handler lists every file, path "" for virtual ones
    import sys
    import types
    from app.memufiles import desktop
    from app.memufiles.server import FilesApp
    handlers = []

    class DOMEventHandler:
        def __init__(self, cb, prevent, stop):
            self.cb, self.prevent, self.stop = cb, prevent, stop

    class Events:
        def __iadd__(self, h):
            handlers.append(h)
            return self
    fake_dom = types.ModuleType("webview.dom")
    fake_dom.DOMEventHandler = DOMEventHandler
    fake_pkg = types.ModuleType("webview")
    fake_pkg.dom = fake_dom
    old = sys.modules.get("webview"), sys.modules.get("webview.dom")
    sys.modules["webview"], sys.modules["webview.dom"] = fake_pkg, fake_dom
    try:
        app = FilesApp()
        app.desktop = types.SimpleNamespace(dnd_paths=False, taskbar=None)
        window = types.SimpleNamespace(dom=types.SimpleNamespace(document=types.SimpleNamespace(events=types.SimpleNamespace(drop=Events()))))
        desktop._attach_drop(window, app)
        assert handlers and handlers[0].stop is False and app.desktop.dnd_paths is True
        real = tmp_path / "real.txt"
        real.write_text("x")
        handlers[0].cb({"dataTransfer": {"files": [{"name": "real.txt", "pywebviewFullPath": str(real)},
                                                    {"name": "inzip.txt"}, {"name": "temp.txt", "pywebviewFullPath": str(tmp_path / "gone")}]}})
        ev = app.events.items[-1][1]
        assert ev["type"] == "drop" and ev["paths"] == [str(real)]
        assert ev["files"] == [{"name": "real.txt", "path": str(real)}, {"name": "inzip.txt", "path": ""}, {"name": "temp.txt", "path": ""}]
    finally:
        for name, mod in zip(("webview", "webview.dom"), old):
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def test_frontend_wiring():
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    drop = app_js[app_js.index("  drop(e, target) {"):app_js.index("  waitBackendDrop(ms)")]
    assert "stopPropagation()" not in drop.replace("NO stopPropagation", "")
    assert "dialogs.toggleOps" not in app_js and "this.opsUi" in app_js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "ops.css" in html and 'href="js/ops.js"' in html and 'href="js/dnd.js"' in html
    for name in ("ops.js", "dnd.js"):
        text = (STATIC / "js" / name).read_text(encoding="utf-8")
        assert "setInterval" not in text and "MutationObserver" not in text
    import re
    ops = (STATIC / "js" / "ops.js").read_text(encoding="utf-8")
    en = ops[ops.index("addStrings({"):ops.index("}, {")]
    assert not re.search(r"[А-Яа-яЁё]", en), "Cyrillic in the English strings"


# ------------------------------------------------------------------ UI (headless Chromium)
def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    if not Path(exe).exists():
        pytest.skip("no chromium")
    return exe


def _shot(page, name):
    if SHOTS:
        time.sleep(0.3)
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name))


DROP_JS = """([files, selector, shift]) => {
  const dt = new DataTransfer();
  for (const [name, text] of files) dt.items.add(new File([text], name, { type: "application/octet-stream", lastModified: 1700000000000 }));
  const el = document.querySelector(selector);
  const r = el.getBoundingClientRect();
  const opts = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, shiftKey: !!shift };
  el.dispatchEvent(new DragEvent("dragenter", opts));
  el.dispatchEvent(new DragEvent("dragover", opts));
  const hint = !!document.querySelector(".drop-hint");
  el.dispatchEvent(new DragEvent("drop", opts));
  return { hint, after: !!document.querySelector(".drop-hint") };
}"""

OVER_ONLY_JS = """(selector) => {
  const dt = new DataTransfer();
  dt.items.add(new File(["x"], "x.txt"));
  const el = document.querySelector(selector);
  const r = el.getBoundingClientRect();
  el.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 50, clientY: r.top + 50 }));
  return !!document.querySelector(".drop-hint");
}"""


def test_ui_pathless_drop_hint_flyout_conflict(server, tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    slow_bytes(monkeypatch, delay=0.02, chunk=128 * 1024)
    target = tmp_path / "MemuAI"
    target.mkdir()
    (target / "уже есть.txt").write_text("old")
    src = tmp_path / "Загрузки"
    src.mkdir()
    (src / "Фильм 4K.mkv").write_bytes(os.urandom(24 * 1024 * 1024))
    (src / "уже есть.txt").write_text("new one")
    url = f"http://127.0.0.1:{server.port}/index.html#t={server.token}"
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1240, "height": 780}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("p => window.MemuFiles.pane().navigate(p)", str(target))
            page.wait_for_selector(".row >> text=уже есть.txt")
            assert page.inner_text(".ops-ind") == "Нет операций"
            # 1) files WITHOUT paths (like a zip open in Explorer): read through the File API and uploaded
            res = page.evaluate(DROP_JS, [[["из архива.txt", "Привет из zip"], ["отчёт.csv", "a;b"]], ".pane.active .fv", False])
            assert res["hint"] and not res["after"], res
            page.wait_for_selector(".row >> text=из архива.txt", timeout=10000)
            wait(lambda: (target / "отчёт.csv").exists() and (target / "из архива.txt").read_text(encoding="utf-8") == "Привет из zip")
            # 1b) desktop (pywebview): Explorer files come with paths in the backend "drop" event — copied by path;
            #     a virtual file in the same drop (no path) is still uploaded
            (src / "с путём.txt").write_text("by path", encoding="utf-8")
            page.evaluate("""async ([real, target]) => {
              const core = await import('/js/core.js');
              const dnd = await import('/js/dnd.js');
              const saved = [core.S.boot.desktop, core.S.platform];
              core.S.boot.desktop = true; core.S.platform = 'nt';
              const pending = dnd.externalDrop(window.MemuFiles, { files: [new File(['by path'], 'с путём.txt'), new File(['virtual'], 'виртуальный.txt')], entries: [], uris: [], shift: false, ctrl: false }, target);
              setTimeout(() => window.MemuFiles.onEvent({ type: 'drop', files: [{ name: 'с путём.txt', path: real }, { name: 'виртуальный.txt', path: '' }] }), 120);
              await pending;
              core.S.boot.desktop = saved[0]; core.S.platform = saved[1];
            }""", [str(src / "с путём.txt"), str(target)])
            wait(lambda: (target / "с путём.txt").exists() and (target / "виртуальный.txt").exists())
            assert (target / "с путём.txt").read_text(encoding="utf-8") == "by path"
            kinds = {o.kind for o in server.queue.ops.values()}
            assert {"copy", "upload"} <= kinds
            # 2) the hint never sticks: dragover without a drop → watchdog; Esc
            assert page.evaluate(OVER_ONLY_JS, ".pane.active .fv")
            if SHOTS:
                page.screenshot(path=os.path.join(SHOTS, "drop-hint.png"), clip={"x": 300, "y": 120, "width": 520, "height": 200})
            page.wait_for_function("!document.querySelector('.drop-hint')", timeout=3000)
            assert page.evaluate(OVER_ONLY_JS, ".pane.active .fv")
            page.keyboard.press("Escape")
            assert page.evaluate("!document.querySelector('.drop-hint')")
            # 3) a big copy: status-bar indicator, flyout card with %, speed graph, pause / resume
            page.evaluate("([s, d]) => window.MemuFiles.transfer([s], d, 'copy')", [str(src / "Фильм 4K.mkv"), str(target)])
            page.wait_for_function("document.querySelector('.ops-ind') && document.querySelector('.ops-ind').textContent.includes('Копирование')", timeout=8000)
            page.click(".ops-ind")
            page.wait_for_selector(".ops-fly .opc.running .opc-graph:not([hidden])", timeout=8000)
            page.wait_for_function("document.querySelector('.ops-fly .opc-speed').textContent.includes('/с')", timeout=8000)
            page.wait_for_function("/Осталось/.test(document.querySelector('.ops-fly .opc-left').textContent)", timeout=8000)
            assert "Фильм 4K.mkv" in page.inner_text(".ops-fly .opc-file")
            time.sleep(1.2)
            _shot(page, "flyout-dark.png")
            page.click(".ops-fly .opc [data-act=pause]")
            page.wait_for_selector(".ops-fly .opc.paused", timeout=5000)
            assert "Пауза" in page.inner_text(".ops-ind")
            page.click(".ops-fly .opc [data-act=pause]")
            page.wait_for_selector(".ops-fly .opc.running", timeout=5000)
            # light theme (applied in place, like MemuAI → Settings does) — the same running card
            page.evaluate("async () => window.MemuFiles.applySettings(await (await fetch('/api/settings', {method: 'POST', headers: {'X-MF-Token': sessionStorage.getItem('mf.token'), 'Content-Type': 'application/json'}, body: JSON.stringify({theme: 'light'})})).json())")
            page.wait_for_function("document.documentElement.dataset.theme === 'light'")
            time.sleep(0.8)
            _shot(page, "flyout-light.png")
            page.click(".ops-fly .opc.running [data-act=cancel]")
            page.wait_for_selector(".ops-fly .opc.cancelled", timeout=8000)
            assert not (target / "Фильм 4K.mkv").exists(), "cancelled copy left a partial file"
            # 4) conflict dialog → keep both
            page.evaluate("([s, d]) => window.MemuFiles.transfer([s], d, 'copy')", [str(src / "уже есть.txt"), str(target)])
            page.wait_for_selector(".cfx-modal .cfx-card", timeout=8000)
            assert page.locator(".cfx-card").count() == 2
            page.click(".cfx-modal .btn:has-text('Сравнить файлы')")
            page.wait_for_selector(".cfx-compare:not([hidden])")
            _shot(page, "conflict-light.png")
            page.click(".cfx-modal [data-choice=keep_both]")
            wait(lambda: (target / "уже есть (2).txt").exists())
            assert (target / "уже есть.txt").read_text() == "old"
            page.wait_for_function("!document.querySelector('.cfx-modal')")
            page.wait_for_selector(".ops-fly .opc.done", timeout=8000)
            _shot(page, "flyout-finished-light.png")
            assert not errors, errors
        finally:
            browser.close()
