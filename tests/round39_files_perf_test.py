"""Round 39 (r39-files-perf): MemuFiles start-up speed and bugs.

Start-up: lazy server members, deferred index warm-up, the one-request hand-off (Python and the registry
launcher), static ETag/304, the themed index.html, no extra «Home» tab, modulepreload + skeleton.
Bugs: Downloads newest first (Explorer's defaults for known folders), per-folder view keys, shortcuts with the
Russian keyboard layout, hand-offs that arrived before the page booted, «keep ready in the background».
No network, no Windows needed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"
PY = sys.executable

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
    app.stopping.set()
    httpd.shutdown()

def call(app, path, body=None, headers=None, raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method="POST" if body is not None else "GET")
    req.add_header("X-MF-Token", app.token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            payload = resp.read()
            return (resp.status, dict(resp.headers), payload) if raw else json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        if raw:
            return exc.code, dict(exc.headers), exc.read()
        raise

def _env(tmp_path):
    return dict(os.environ, MEMUAI_DATA_DIR=str(tmp_path / "data"), PYTHONPATH=str(ROOT))

def _chromium():
    from tests.round32_slides_test import _chromium as installed_chromium
    executable = installed_chromium()
    if not executable:
        pytest.skip("Chromium is not installed")
    return executable

def test_startup_imports_stay_light(tmp_path):
    """The hand-off path (memufiles.py → crash → desktop) must not pull urllib/http.client/traceback/tempfile, and
    FilesApp() must not import the operation queue, jobs or the AI client."""
    code = ("import sys; from app.memufiles import crash; crash.install(); import app.memufiles.desktop; "
            "bad=[m for m in ('urllib.request','http.client','logging','traceback','tempfile','inspect') if m in sys.modules]; "
            "from app.memufiles import server; a=server.FilesApp(); "
            "lazy=[m for m in ('app.memufiles.opqueue','app.memufiles.jobs','app.memufiles.assistant') if m in sys.modules]; "
            "print(bad, lazy)")
    out = subprocess.run([PY, "-c", code], cwd=ROOT, env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[] []", out.stdout

def test_lazy_members_and_deferred_warm(server, monkeypatch):
    from app.memufiles import server as srv
    assert server._queue is None and server._jobs is None
    assert server.queue is server.queue and server._queue is not None
    calls = []
    monkeypatch.setattr(srv, "_deferred_warm", lambda app, delay=2.5: calls.append(app))
    boot = call(server, "/api/boot")
    assert calls == [server] and boot["path_info"] == {} and "known_folders" in boot

def test_boot_path_info_and_handoff_before_boot(server, tmp_path):
    folder = tmp_path / "Папка"
    folder.mkdir()
    file = folder / "отчёт.txt"
    file.write_text("x", encoding="utf-8")

    reply = call(server, "/api/remote", {"action": "open", "paths": [str(folder), str(file)], "require_ui": True})
    assert reply["taken"] is True
    boot = call(server, "/api/boot")
    assert boot["paths"] == [str(folder), str(file)]
    assert boot["path_info"][str(folder)] == {"exists": True, "is_dir": True}
    assert boot["path_info"][str(file)] == {"exists": True, "is_dir": False}

    call(server, "/api/remote", {"action": "open", "paths": [str(folder)]})
    seq, events = server.events.wait(boot["events_seq"], timeout=1)
    opened = [e for e in events if e["type"] == "open"]
    assert opened and opened[-1]["info"][str(folder)]["is_dir"] is True

def test_ghost_instance_refuses_handoff(server):
    server.ui = "starting"
    server.started = time.time() - 600
    reply = call(server, "/api/remote", {"action": "open", "paths": ["/"], "require_ui": True})
    assert reply["taken"] is False and server.initial_paths == []
    server.ui = "warm"
    assert call(server, "/api/remote", {"action": "open", "paths": ["/"], "require_ui": True})["taken"] is True

def test_desktop_handoff_over_socket(server, tmp_path, monkeypatch):
    from app.memufiles import desktop
    desktop.publish_instance(server.port, server.token)
    server.ui = "headless"
    assert desktop.hand_off([str(tmp_path)]) is True
    assert server.initial_paths == [str(tmp_path)]
    assert desktop._remote("ping", {})["ui"] == "headless"
    server.ui, server.started = "starting", time.time() - 600
    stopped = []
    real = desktop._remote
    monkeypatch.setattr(desktop, "_remote", lambda a, p, timeout=3.0: (stopped.append(a), real(a, p, timeout))[1]
                        if a != "shutdown" else stopped.append(a))
    assert desktop.hand_off(["/"]) is False and stopped == ["open", "shutdown"]

def test_handoff_process_is_fast(server, tmp_path):
    """A second MemuFiles start hands its folder over and exits — measured as a whole process."""
    from app.memufiles import desktop
    server.ui = "headless"
    desktop.publish_instance(server.port, server.token)
    started = time.perf_counter()
    out = subprocess.run([PY, "memufiles.py", str(tmp_path)], cwd=ROOT, env=_env(tmp_path), capture_output=True, timeout=30)
    took = time.perf_counter() - started
    assert out.returncode == 0 and server.initial_paths == [str(tmp_path)]
    assert took < 3.0, took

def test_static_etag_and_themed_index(server, monkeypatch):
    status, headers, body = call(server, "/js/core.js", raw=True)
    assert status == 200 and headers.get("ETag")
    status2, _, body2 = call(server, "/js/core.js", headers={"If-None-Match": headers["ETag"]}, raw=True)
    assert status2 == 304 and body2 == b""
    from app.memufiles import settings as st
    st.save({"theme": "light", "language": "en"})
    status, headers, html = call(server, "/index.html", raw=True)
    text = html.decode("utf-8")
    assert '<html lang="en" data-theme="light"' in text and "ETag" not in headers
    assert "Content-Security-Policy" in headers
    if server.theme()["tokens"]:
        assert re.search(r'<style id="mf-theme">:root\{--[\w-]+:', text)
    else:
        assert 'class="fallback-tokens"' in text

def test_index_has_modulepreload_and_skeleton():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = {p.name for p in (STATIC / "js").glob("*.js")}
    preloaded = set(re.findall(r'rel="modulepreload" href="js/([\w-]+\.js)"', html))
    imported = set()
    for path in (STATIC / "js").glob("*.js"):
        imported |= set(re.findall(r'^import [^;]*from "\./([\w-]+\.js)"', path.read_text(encoding="utf-8"), re.M))
    assert imported <= preloaded | {"app.js"}, imported - preloaded
    assert preloaded <= js
    assert 'class="boot-splash boot-skel"' in html and ".boot-splash.boot-skel" in (STATIC / "memufiles.css").read_text(encoding="utf-8")

def test_launcher_hands_off_directly(tmp_path):
    from app.memufiles import default_shell
    src = default_shell.launcher_source(r"C:\Users\Иван\MemuFiles\MemuFiles.exe")
    assert "function handOff(path)" in src and "WinHttp.WinHttpRequest.5.1" in src and "SetProxy(1)" in src
    assert "} else if (!handOff(target)) {" in src
    assert '"require_ui":true' in src and str(Path(os.environ["MEMUAI_DATA_DIR"]) / "memufiles" / "instance.json").replace("\\", "\\\\") in src
    node = shutil.which("node")
    if node:
        path = tmp_path / "launcher.js"
        path.write_text(src, encoding="utf-8")
        assert subprocess.run([node, "--check", str(path)], capture_output=True).returncode == 0

        line = next(l for l in src.splitlines() if "var body =" in l).strip()
        probe = 'var shell = ""; var path = "C:\\\\Пап\\"ка\\\\"; ' + line + ' console.log(JSON.parse(body).paths[0] === path && JSON.parse(body).require_ui);'
        out = subprocess.run([node, "-e", probe], capture_output=True, text=True)
        assert out.stdout.strip() == "true", out.stderr

def test_heal_upgrades_an_old_launcher(monkeypatch, tmp_path):
    from app.memufiles import default_shell
    exe = tmp_path / "MemuFiles.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(default_shell, "available", lambda: True)
    monkeypatch.setattr(default_shell, "current_exe", lambda: str(exe))
    state = default_shell.load_state()
    state["exe"] = str(exe)
    state["mode"] = "launcher"
    state["features"] = {"folders": {"enabled": True}}
    default_shell._save_state(state)
    default_shell.launcher_path().parent.mkdir(parents=True, exist_ok=True)
    default_shell.launcher_path().write_bytes(b"\xff\xfe" + "// round 38".encode("utf-16-le"))
    assert default_shell._launcher_outdated(str(exe)) is True
    assert default_shell.heal() is True
    assert default_shell._launcher_outdated(str(exe)) is False
    assert default_shell.heal() is False

def test_keep_running_setting(server):
    from app.memufiles import settings_schema, settings as st
    item = settings_schema.BY_KEY["keep_running"]
    assert item["default"] is False and item["group"] == "files" and item["ru"] and item["en"]
    assert st.load()["keep_running"] is False
    closed = []

    class FakeDesktop:
        def window_command(self, cmd, body):
            closed.append(cmd)

        def force_close(self):
            closed.append("force_close")
    server.desktop = FakeDesktop()
    server.ui = "warm"
    call(server, "/api/remote", {"action": "open", "paths": ["/"]})
    assert server.ui == "window" and closed == ["activate"]
    server.ui = "warm"
    call(server, "/api/settings", {"keep_running": False})
    time.sleep(0.6)
    assert closed[-1] == "force_close"
    keys = {k for k, _r, _e in settings_schema.SHORTCUTS}
    assert {"Ctrl+N", "Ctrl+D"} <= keys

def test_ui_downloads_newest_first_one_tab_and_ru_layout(server, tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import server as srv
    downloads = tmp_path / "Загрузки"
    downloads.mkdir()
    now = time.time()
    for i, name in enumerate(["яблоко.zip", "альфа.pdf", "бета.exe", "гамма.png"]):
        path = downloads / name
        path.write_bytes(b"x" * (i + 1))
        os.utime(path, (now - (4 - i) * 3600 * 30, now - (4 - i) * 3600 * 30))
    other = tmp_path / "Обычная"
    other.mkdir()
    for name in ("б.txt", "а.txt"):
        (other / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(srv, "_KNOWN", {"downloads": str(downloads) + os.sep})
    server.initial_paths = [str(downloads)]
    url = f"http://127.0.0.1:{server.port}/index.html#t={server.token}"
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_selector(".fv .row")
            assert page.locator(".tabbar .tab").count() == 1
            assert page.locator(".boot-skel").count() == 0
            names = page.eval_on_selector_all(".fv .row .nm-text", "els => els.map(e => e.textContent)")
            assert names[0].startswith("гамма") and names[-1].startswith("яблоко"), names
            assert page.locator(".fv-head .col.sorted[data-col=m]").count() == 1
            assert page.locator(".fv .group, .fv [class*=group]").count() >= 1

            page.evaluate("p => window.MemuFiles.pane().navigate(p)", str(other))
            page.wait_for_selector(".fv .row .nm-text >> text=а.txt")
            names = page.eval_on_selector_all(".fv .row .nm-text", "els => els.map(e => e.textContent)")
            assert names[:2] == ["а.txt", "б.txt"], names

            page.locator(".fv").focus()
            page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ф', code: 'KeyA', ctrlKey: true, bubbles: true}))")
            assert page.locator(".fv .row.sel").count() == 2

            same = page.evaluate("""async () => { const core = await import('/js/core.js'); const pane = await import('/js/pane.js');
                const old = core.S.platform; core.S.platform = 'nt';
                const r = [pane.folderKey('C:\\\\Users\\\\Me\\\\Downloads\\\\'), pane.folderKey('c:/users/me/downloads'), pane.folderKey('C:\\\\')];
                core.S.platform = old; return r; }""")
            assert same[0] == same[1] == "c:\\users\\me\\downloads" and same[2] == "c:\\"
            assert not errors, errors
        finally:
            browser.close()

def test_dir_stamp_sees_content_changes(tmp_path):
    """Auto-refresh: a file whose size changed (folder mtime unchanged — and FAT/exFAT never updates it) is noticed."""
    from app.memufiles import fsops
    folder = tmp_path / "usb"
    folder.mkdir()
    file = folder / "лог.txt"
    file.write_text("a", encoding="utf-8")
    st = os.stat(folder)
    before = fsops.dir_stamp(str(folder))
    assert before == fsops.dir_stamp(str(folder))
    file.write_text("abc", encoding="utf-8")
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert fsops.dir_stamp(str(folder)) != before

def _lnk(target, unicode=True, idlist=True):
    """A minimal MS-SHLLINK file: header + (optional) IDList + LinkInfo with a local base path."""
    import struct
    flags = 0x2 | (0x1 if idlist else 0)
    head = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02" + b"\x00" * 13, flags) + b"\x00" * (0x4C - 24)
    ids = struct.pack("<H", 4) + b"\x02\x00\x00\x00" if idlist else b""
    ansi = target.encode("cp1252", "replace") + b"\x00"
    hsize = 0x24 if unicode else 0x1C
    vol = b"\x10\x00\x00\x00" + b"\x00" * 12
    vol_off = hsize
    local_off = vol_off + len(vol)
    suffix_off = local_off + len(ansi)
    body = vol + ansi + b"\x00"
    extra = b""
    if unicode:
        local_u = hsize + len(body)
        wide = target.encode("utf-16-le") + b"\x00\x00"
        suffix_u = local_u + len(wide)
        extra = struct.pack("<2I", local_u, suffix_u)
        body += wide + b"\x00\x00"
    size = hsize + len(body)
    info = struct.pack("<7I", size, hsize, 1, vol_off, local_off, 0, suffix_off) + extra + body
    return head + ids + info

def test_folder_shortcut_opens_in_place(server, tmp_path, monkeypatch):
    from app.memufiles import fsops
    target = tmp_path / "Документы проекта"
    target.mkdir()
    lnk = tmp_path / "Проект.lnk"
    lnk.write_bytes(_lnk(str(target)))
    assert fsops.lnk_target(str(lnk)) == str(target)
    plain = tmp_path / "ascii.lnk"
    plain.write_bytes(_lnk("/tmp/plain-target", unicode=False, idlist=False))
    assert fsops.lnk_target(str(plain)) == "/tmp/plain-target"
    (tmp_path / "broken.lnk").write_bytes(b"not a shortcut")
    assert fsops.lnk_target(str(tmp_path / "broken.lnk")) == ""
    opened = []
    monkeypatch.setattr(fsops, "open_default", lambda p: opened.append(p))
    assert call(server, "/api/open", {"path": str(lnk)}) == {"ok": True, "navigate": str(target)} and not opened
    call(server, "/api/open", {"path": str(plain)})
    assert opened == [str(plain)]
