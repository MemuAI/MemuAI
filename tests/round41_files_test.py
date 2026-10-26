"""Round 41 (r41-files): MemuFiles — Explorer's order in Downloads (date ↓, date groups, folders among files), Tiles,
«Show in folder» (/select, --select, hand-off with reveal, the shell hint), archives opened like folders (listing,
preview, extract chosen, add, create 7z/tar/level), «Open archives in MemuFiles» (HKCU, fake registry, rollback),
Quick Access over the MemuFiles window, the schema-driven settings window and the new settings. No network."""
import io
import json
import os
import tarfile
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield

def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")
    if not os.path.exists(exe):
        pytest.skip("no chromium")
    return exe

def touch(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))

def make_downloads(base: Path) -> Path:
    from PIL import Image
    dl = base / "Downloads"
    dl.mkdir(parents=True)
    now = datetime.now()
    for name, delta, folder in (("today.txt", timedelta(hours=0.1), False), ("tdata", timedelta(hours=0.2), True),
                                ("old.txt", timedelta(days=400), False), ("archive-folder", timedelta(days=400), True),
                                ("yesterday.txt", timedelta(days=1, hours=1), False)):
        p = dl / name
        p.mkdir() if folder else p.write_text("x" * 50, encoding="utf-8")
        touch(p, now - delta)
    Image.new("RGB", (40, 30), (10, 120, 200)).save(dl / "pic.png")
    z = dl / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("pack/README.md", "# hello\n")
        zf.writestr("pack/src/main.py", "print(1)\n")
        zf.writestr("pack/src/util.py", "x = 2\n")
    touch(z, now - timedelta(days=1, hours=2))
    return dl

def call(app, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-MF-Token": app.token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        raw = res.read()
    return json.loads(raw) if raw[:1] == b"{" else raw

def ls(app, path):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}/api/ls?path=" + urllib.request.quote(path), headers={"X-MF-Token": app.token})
    with urllib.request.urlopen(req, timeout=10) as res:
        lines = [json.loads(l) for l in res.read().decode().splitlines() if l.strip()]
    rows = [r for l in lines for r in l.get("rows", [])]
    return lines[0], rows

def wait_op(app, op_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        op = app.queue.ops[op_id]
        if op.status in ("done", "error", "cancelled"):
            return op
        time.sleep(0.05)
    raise AssertionError("operation did not finish")

def test_parse_args_explorer_switches(tmp_path):
    from app.memufiles import desktop
    f = tmp_path / "a b.txt"
    f.write_text("x")
    d = tmp_path / "Папка"
    d.mkdir()
    got = desktop._parse_args(["/select," + str(f)])
    assert got["paths"] == [str(f)] and got["reveal"] == [str(f)]
    got = desktop._parse_args(["/select,", str(d)])
    assert got["reveal"] == [str(d)]
    got = desktop._parse_args(["--select", str(f), "--debug"])
    assert got["reveal"] == [str(f)] and "--debug" in got["flags"]
    got = desktop._parse_args(["--select=" + str(f)])
    assert got["reveal"] == [str(f)]
    got = desktop._parse_args(["/e," + str(d)])
    assert got["paths"] == [str(d)] and got["reveal"] == []
    got = desktop._parse_args([str(f)])
    assert got["paths"] == [str(f)] and got["reveal"] == []
    got = desktop._parse_args(["--archive", str(f)])
    assert "--archive" in got["flags"] and got["paths"] == [str(f)]

def test_launcher_marks_shell_folders(monkeypatch):
    import shutil
    import subprocess
    from app.memufiles import default_shell
    src = default_shell.launcher_source(r"C:\MemuFiles\MemuFiles.exe")
    line = next(l for l in src.splitlines() if "var body =" in l).strip()
    node = shutil.which("node")
    if not node:
        pytest.skip("no node")
    for shell, want in (("',\"shell\":true'", True), ('""', None)):
        probe = f'var shell = {shell}; var path = "C:\\\\x"; ' + line + \
            ' var b = JSON.parse(body); console.log(JSON.stringify([b.shell === undefined ? null : b.shell, b.paths[0]]));'
        out = subprocess.run([node, "-e", probe], capture_output=True, text=True)
        assert json.loads(out.stdout) == [want, "C:\\x"], out.stderr
    assert "var shell = path ? ',\"shell\":true' : \"\";" in src

def test_boot_marks_handoff_and_state(tmp_path):
    from app.memufiles import server as srv
    dl = make_downloads(tmp_path)
    target = str(dl / "today.txt")
    app = srv.FilesApp([target], reveal=[target], shell=None, browse=[str(dl / "pack.zip")])
    httpd, _ = srv.start(app)
    try:
        boot = call(app, "/api/boot")
        assert boot["reveal"] == [target] and boot["browse"] == [str(dl / "pack.zip")] and boot["shell"] == []
        call(app, "/api/remote", {"action": "open", "paths": [str(dl)], "shell": True})
        ev = [e for _, e in app.events.items if e.get("type") == "open"][-1]
        assert ev["shell"] == [str(dl)] and ev["reveal"] == [] and ev["browse"] == []
        call(app, "/api/uistate", {"folder": str(dl), "selected": [target], "mode": "dir"})
        state = call(app, "/api/remote", {"action": "state"})["state"]
        assert state["folder"] == str(dl) and state["selected"] == [target] and state["pid"] == os.getpid()
        schema = call(app, "/api/settings/schema")
        keys = {i["key"] for i in schema["items"]}
        assert {"folders_order", "default_sort", "default_group", "accent", "ui_scale", "date_format", "size_format",
                "open_archives", "archive_format", "start_page", "new_tab_page", "show_system"} <= keys
        assert "folders_first" not in keys and "view_by_folder" not in keys
        assert {g["id"] for g in schema["groups"]} >= {"tabs", "archives"}
    finally:
        httpd.shutdown()

def test_migrate_downloads_view_once(tmp_path, monkeypatch):
    from app.memufiles import server as srv, settings
    dl = make_downloads(tmp_path)
    monkeypatch.setattr(srv, "_KNOWN", {"downloads": str(dl)})
    settings.save({"view_by_folder": {str(dl): {"view": "grid", "sort": {"key": "m", "dir": -1}, "group": "none"},
                                      str(tmp_path): {"view": "list", "sort": {"key": "name", "dir": 1}, "group": "none"}}})
    out = srv._migrate_views(settings.load())
    assert out["view_by_folder"][str(dl)] == {"view": "grid"}
    assert out["view_by_folder"][str(tmp_path)]["sort"]["key"] == "name"
    assert out["views_rev"] == 41
    settings.save({"view_by_folder": {str(dl): {"view": "grid", "sort": {"key": "name", "dir": 1}, "group": "none"}}})
    assert srv._migrate_views(settings.load())["view_by_folder"][str(dl)]["sort"]["key"] == "name"

def test_archive_listing_preview_extract_add(tmp_path):
    from app.memufiles import archive, server as srv
    dl = make_downloads(tmp_path)
    z = str(dl / "pack.zip")
    assert archive.split(z) == (z, "") and archive.split(z + os.sep + "pack" + os.sep + "src") == (z, "pack/src")
    assert archive.split(str(dl)) is None
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        head, rows = ls(app, z)
        assert head["archive"]["kind"] == "zip" and [r["n"] for r in rows] == ["pack"] and rows[0]["d"] == 1
        head, rows = ls(app, os.path.join(z, "pack", "src"))
        assert sorted(r["n"] for r in rows) == ["main.py", "util.py"] and all(r["a"] == 1 for r in rows)
        text = call(app, "/api/text?path=" + urllib.request.quote(os.path.join(z, "pack", "README.md")))
        assert "hello" in json.dumps(text)
        info = call(app, "/api/arc/info?path=" + urllib.request.quote(z))
        assert info["single_root"] == "pack" and info["files"] == 3
        stamp = call(app, "/api/dirstamp", {"paths": [os.path.join(z, "pack")]})["stamps"]
        assert list(stamp.values())[0].startswith("arc:")

        out = tmp_path / "out"
        res = call(app, "/api/ops/submit", {"kind": "extract", "sources": [z], "dest": str(out),
                                            "args": {"members": ["pack/src/main.py"], "strip": "pack/src"}})
        assert wait_op(app, res["op"]["id"]).status == "done"
        assert sorted(os.listdir(out)) == ["main.py"] and (out / "main.py").read_text() == "print(1)\n"

        extra = tmp_path / "new.txt"
        extra.write_text("added", encoding="utf-8")
        res = call(app, "/api/ops/submit", {"kind": "arcadd", "sources": [str(extra)], "dest": z, "args": {"inner": "pack/src"}})
        assert wait_op(app, res["op"]["id"]).status == "done"
        with zipfile.ZipFile(z) as zf:
            assert zf.read("pack/src/new.txt") == b"added"
        assert "new.txt" in [r["n"] for r in ls(app, os.path.join(z, "pack", "src"))[1]]

        t = dl / "d.tar.gz"
        with tarfile.open(t, "w:gz") as tf:
            data = b"tar!"
            ti = tarfile.TarInfo("x/y.txt")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        assert [r["n"] for r in ls(app, os.path.join(str(t), "x"))[1]] == ["y.txt"]
        import gzip
        with gzip.open(dl / "notes.txt.gz", "wb") as gz:
            gz.write(b"gz content")
        assert [r["n"] for r in ls(app, str(dl / "notes.txt.gz"))[1]] == ["notes.txt"]
        assert open(archive.materialize(str(dl / "notes.txt.gz" / "notes.txt")), "rb").read() == b"gz content"
    finally:
        httpd.shutdown()

@pytest.mark.parametrize("fmt", ["tar.gz", "tar.xz", "zip"])
def test_create_archive_formats_and_levels(tmp_path, fmt):
    from app.memufiles import archive, server as srv
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a" * 5000)
    (src / "sub" / "b.txt").write_text("b")
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        dest = tmp_path / f"out.{fmt}"
        res = call(app, "/api/ops/submit", {"kind": "compress", "sources": [str(src)], "dest": str(dest), "args": {"format": fmt, "level": "max"}})
        op = wait_op(app, res["op"]["id"])
        assert op.status == "done", op.errors
        names = sorted(n for n, r in archive.index(str(dest))["entries"].items() if not r["d"])
        assert names == ["src/a.txt", "src/sub/b.txt"]
    finally:
        httpd.shutdown()

def test_archive_assoc_fake_registry(tmp_path, monkeypatch):
    from app.memufiles import archive_assoc, default_shell

    class FakeReg:
        def __init__(self):
            self.v = {}

        def get(self, path, name):
            return (1, self.v[(path.lower(), name)]) if (path.lower(), name) in self.v else None

        def key_exists(self, path):
            return any(k[0] == path.lower() or k[0].startswith(path.lower() + "\\") for k in self.v)

        def set(self, path, name, kind, data):
            self.v[(path.lower(), name)] = data

        def delete_value(self, path, name):
            self.v.pop((path.lower(), name), None)

        def is_empty(self, path):
            return not self.key_exists(path)

        def delete_tree(self, path):
            for k in [k for k in self.v if k[0] == path.lower() or k[0].startswith(path.lower() + "\\")]:
                del self.v[k]

        def notify(self):
            self.notified = True

    reg = FakeReg()
    reg.set(r"Software\Classes\.zip", "", 1, "CompressedFolder")
    reg.set(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.rar\UserChoice", "ProgId", 1, "WinRAR")
    default_shell.set_registry(reg)
    monkeypatch.setattr(default_shell, "current_exe", lambda: r"C:\MemuAI\MemuFiles\MemuFiles.exe")
    try:
        st = archive_assoc.sync(True)
        assert st["on"] and st["extensions"][".zip"] == "on" and st["extensions"][".rar"] == "choice"
        assert reg.get(r"Software\Classes\MemuFiles.Archive\shell\open\command", "")[1] == r'"C:\MemuAI\MemuFiles\MemuFiles.exe" --archive "%1"'
        assert reg.get(r"Software\Classes\.zip\OpenWithProgids", "MemuFiles.Archive") is not None
        assert reg.get(r"Software\RegisteredApplications", "MemuFiles")[1] == archive_assoc.CAPS and reg.notified
        archive_assoc.sync(True)
        st = archive_assoc.sync(False)
        assert not st["on"]
        assert reg.get(r"Software\Classes\.zip", "")[1] == "CompressedFolder"
        assert reg.get(r"Software\Classes\.7z", "") is None
        assert not reg.key_exists(r"Software\Classes\MemuFiles.Archive") and reg.get(r"Software\RegisteredApplications", "MemuFiles") is None
    finally:
        default_shell.set_registry(None)

def test_quick_access_reads_memufiles_window(tmp_path, monkeypatch):
    from app import quick_apps
    from app.memufiles import desktop
    dl = make_downloads(tmp_path)
    assert quick_apps.classify("WindowsForms10.Window.8.app.0.1", r"C:\MemuAI\MemuFiles\MemuFiles.exe") == {"kind": "explorer", "app": "MemuFiles"}
    sel = str(dl / "today.txt")
    monkeypatch.setattr(desktop, "_remote", lambda action, payload, timeout=3.0: {"ok": True, "state": {"folder": str(dl), "selected": [sel, str(dl / "gone.txt")], "pid": 77}})
    ctx = quick_apps.capture(5, describe=lambda h: {"class": "WindowsForms10", "exe": r"C:\x\MemuFiles.exe", "title": "Downloads", "pid": 77}, prefs={"explorer": True, "apps": True})
    assert ctx["kind"] == "explorer" and ctx["app"] == "MemuFiles" and ctx["folder"] == str(dl) and ctx["selected"] == [sel]

    monkeypatch.setattr(desktop, "_remote", lambda action, payload, timeout=3.0: {"ok": True, "state": {"folder": str(dl / "pack.zip"), "selected": [], "archive": True, "pid": 77}})
    assert quick_apps.memufiles_folder(77) == {"path": str(dl), "selected": [str(dl / "pack.zip")], "reason": ""}
    monkeypatch.setattr(desktop, "_remote", lambda action, payload, timeout=3.0: None)
    assert quick_apps.memufiles_folder(77)["reason"] == "memufiles_offline"

def test_ui_downloads_order_tiles_reveal_archive_settings(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import fsops, server as srv
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    dl = make_downloads(tmp_path)
    monkeypatch.setattr(srv, "_KNOWN", {"downloads": str(dl)})
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    shots = os.environ.get("MEMU_SHOTS_DIR")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 800}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{app.port}/index.html#t={app.token}")
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("p => MemuFiles.pane().navigate(p)", str(dl))
            page.wait_for_function("MemuFiles.pane().shown.length >= 7")
            st = page.evaluate("() => ({sort: MemuFiles.pane().sort, group: MemuFiles.pane().group, names: MemuFiles.pane().shown.map(r => r.n), groups: MemuFiles.pane().groups.map(g => g.label)})")

            assert st["sort"] == {"key": "m", "dir": -1} and st["group"] == "date"
            assert st["groups"][0] == "Сегодня" and st["groups"][-1] == "Давно"
            assert st["names"].index("today.txt") < st["names"].index("tdata") < st["names"].index("yesterday.txt")
            assert st["names"].index("old.txt") < st["names"].index("archive-folder")

            page.evaluate("() => { const p = MemuFiles.pane(); p.sort = {key: 'name', dir: 1}; p.group = 'none'; p.refresh(false); }")
            names = page.evaluate("() => MemuFiles.pane().shown.map(r => r.n)")
            assert names[:2] == ["archive-folder", "tdata"]
            page.evaluate("() => MemuFiles.setSetting('folders_order', 'mixed')")
            page.wait_for_function("MemuFiles.pane().shown[0].n !== 'archive-folder' || true")
            names = page.evaluate("() => MemuFiles.pane().shown.map(r => r.n)")
            assert names.index("archive-folder") == 0 and names.index("tdata") > names.index("pack.zip")
            page.evaluate("() => MemuFiles.setSetting('folders_order', 'auto')")

            page.evaluate("() => MemuFiles.pane().setView('tiles')")
            page.wait_for_selector(".tile.card")
            card = page.locator(".tile.card", has_text="today.txt").first
            assert card.locator(".sub").count() == 2 and "50" in card.inner_text()
            if shots:
                page.screenshot(path=os.path.join(shots, "r41-tiles.png"))

            target = str(dl / "old.txt")
            call(app, "/api/remote", {"action": "open", "paths": [target], "reveal": [target]})
            page.wait_for_function("p => MemuFiles.pane().sel.has(p)", arg=target)
            assert page.evaluate("() => document.activeElement === MemuFiles.pane().fv")

            time.sleep(0.5)
            assert call(app, "/api/remote", {"action": "state"})["state"]["selected"] == [target]

            fresh = dl / "just-downloaded.bin"
            fresh.write_bytes(b"1234")
            page.evaluate("() => { const p = MemuFiles.pane(); p.sel.clear(); }")
            call(app, "/api/remote", {"action": "open", "paths": [str(dl)], "shell": True})
            page.wait_for_function("p => MemuFiles.pane().sel.has(p)", arg=str(fresh))

            page.evaluate("() => MemuFiles.pane().setView('details')")
            page.locator(".row", has_text="pack.zip").first.dblclick()
            page.wait_for_selector(".banner.arc")
            page.locator(".row", has_text="pack").first.dblclick()
            page.wait_for_function("MemuFiles.pane().shown.some(r => r.n === 'README.md')")
            page.locator(".row", has_text="README.md").first.click()
            page.wait_for_selector(".banner.arc .btn.primary")
            if shots:
                page.screenshot(path=os.path.join(shots, "r41-archive.png"))
            page.click(".banner.arc .btn.primary")
            page.wait_for_selector(".modal .zip41")
            out = tmp_path / "x"
            page.fill(".modal .zip41 input.input", str(out))
            page.click(".modal .btn.primary")
            page.wait_for_function("() => ![...MemuFiles.ops.values()].some(o => o.status === 'running' || o.status === 'queued')")
            time.sleep(0.3)
            assert sorted(os.listdir(out)) == ["README.md"]

            page.keyboard.press("F2")
            page.wait_for_function("() => [...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('архива'))")

            page.evaluate("() => import('/js/dialogs.js').then((m) => m.settingsDialog(MemuFiles))")
            page.wait_for_selector(".st41-nav button[data-g=archives]")
            page.fill(".st41-search", "формат даты")
            page.wait_for_selector(".st41-row[data-key=date_format]")
            page.select_option(".st41-row[data-key=date_format] select", "iso")
            page.wait_for_function("() => MemuFiles.settings.date_format === 'iso'")
            page.fill(".st41-search", "")
            page.click(".st41-nav button[data-g=appearance]")
            page.click(".st41-swatches button[title='Зелёный']")
            page.wait_for_function("() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() === '#2fa36b'")
            if shots:
                page.screenshot(path=os.path.join(shots, "r41-settings.png"))
            page.keyboard.press("Escape")
            from app.memufiles import settings
            assert settings.load()["accent"] == "green" and settings.load()["date_format"] == "iso"
            assert not [e for e in errors if "favicon" not in e], errors
            browser.close()
    finally:
        httpd.shutdown()
