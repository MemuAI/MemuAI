"""Round 36 · MemuFiles: «Open with» (Windows shell glue simulated with fakes), custom title bar, fixes.

The Windows code paths cannot run here: ``ctypes.windll`` / COM vtables / ``winreg`` are replaced by fakes, so
these tests check the logic (enumeration, ordering, fallbacks, quoting, threading), not the real shell.
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield

class FakeShell:
    """Fake shell32/ole32/user32/shlwapi + a COM vtable dispatcher for ``openwith._vcall``."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.pos = 0
        self.invoked = []
        self.released = []
        self.exec_infos = []
        self.chooser_hr = 0
        self.shell_execute_ok = True
        self.default_exe = ""
        outer = self

        class Shell32:
            def SHAssocEnumHandlers(self, ext, flt, out):
                outer.pos = 0
                out._obj.value = 100
                return 0

            def SHCreateItemFromParsingName(self, path, bc, iid, out):
                outer.parsed = path
                out._obj.value = 300
                return 0

            def SHOpenWithDialog(self, hwnd, info):
                outer.chooser_args = (hwnd, info._obj.pcszFile, info._obj.oaifInFlags)
                return outer.chooser_hr

            def ShellExecuteExW(self, info):
                obj = info._obj
                outer.exec_infos.append({"verb": obj.lpVerb, "file": obj.lpFile, "params": obj.lpParameters,
                                         "cls": obj.lpClass, "mask": obj.fMask})
                return 1 if outer.shell_execute_ok else 0

        class Ole32:
            def CoTaskMemFree(self, ptr):
                return None

            def CoInitializeEx(self, *a):
                outer.co_thread = threading.current_thread().name
                return 0

        class User32:
            def AllowSetForegroundWindow(self, v):
                outer.allowed = v
                return 1

            def PeekMessageW(self, *a):
                return 0

        class Shlwapi:
            def AssocQueryStringW(self, flags, what, assoc, extra, buf, size):
                buf.value = outer.default_exe
                return 0 if outer.default_exe else 0x80070483

        self.shell32, self.ole32, self.user32, self.shlwapi = Shell32(), Ole32(), User32(), Shlwapi()

    def vcall(self, ptr, index, argtypes, *args):
        if index == 2:
            self.released.append(ptr)
            return 0
        if ptr == 100 and index == 3:
            if self.pos >= len(self.handlers):
                return 1
            args[1]._obj.value = 200 + self.pos
            args[2]._obj.value = 1
            self.pos += 1
            return 0
        if ptr == 300 and index == 3:
            args[3]._obj.value = 400
            return 0
        if 200 <= ptr < 300:
            exe, ui, icon_path, icon_index = self.handlers[ptr - 200]
            if index == 3:
                args[0]._obj.value = exe
                return 0
            if index == 4:
                args[0]._obj.value = ui
                return 0
            if index == 5:
                if not icon_path:
                    return 0x80004005
                args[0]._obj.value = icon_path
                args[1]._obj.value = icon_index
                return 0
            if index == 8:
                self.invoked.append((exe, args[0]))
                return 0
        raise AssertionError(f"unexpected vcall {ptr} {index}")

@pytest.fixture()
def fake(monkeypatch):
    from app.memufiles import openwith
    shell = FakeShell([
        (r"C:\Program Files\Notepad++\notepad++.exe", "Notepad++", r"C:\Program Files\Notepad++\notepad++.exe", 0),
        (r"C:\Windows\system32\notepad.exe", "Блокнот", r"%SystemRoot%\system32\notepad.exe", 0),
        ("Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "Notepad (Store)", "@{Microsoft.WindowsNotepad?ms-resource://x}", 0),
        (r"C:\Windows\system32\notepad.exe", "Блокнот", "", 0),
    ])
    shell.default_exe = r"C:\Windows\system32\notepad.exe"
    monkeypatch.setattr(openwith, "IS_WIN", True)
    monkeypatch.setattr(openwith, "_api", lambda: shell)
    monkeypatch.setattr(openwith, "_vcall", shell.vcall)
    monkeypatch.setattr(openwith, "_pump", lambda: None)
    monkeypatch.setattr(openwith, "_WORKER", openwith._StaWorker())
    openwith._ICONS.clear()
    openwith._ICON_PNG.clear()
    return shell

def test_handlers_list_default_first_deduped_and_released(fake, tmp_path):
    from app.memufiles import openwith
    target = tmp_path / "Мои документы с пробелами" / "отчёт.TXT"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    items = openwith.handlers(str(target))
    assert [i["name"] for i in items] == ["Блокнот", "Notepad (Store)", "Notepad++"]
    assert items[0]["default"] is True and not items[1]["default"]
    assert all(i["id"].startswith("shell:") for i in items)
    assert items[0]["icon"] is True and items[1]["icon"] is True
    assert set(fake.released) >= {100, 200, 201, 202, 203}
    assert fake.co_thread == "memufiles-shell"

def test_handlers_empty_for_folder_and_no_extension(fake, tmp_path):
    from app.memufiles import openwith
    (tmp_path / "Makefile").write_text("x")
    assert openwith.handlers(str(tmp_path)) == []
    assert openwith.handlers(str(tmp_path / "Makefile")) == []

def test_launch_invokes_handler_with_data_object(fake, tmp_path):
    from app.memufiles import openwith
    target = tmp_path / "файл с пробелами.txt"
    target.write_text("x", encoding="utf-8")
    openwith.launch(str(target), "shell:Microsoft.WindowsNotepad_8wekyb3d8bbwe!App")
    assert fake.invoked == [("Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", 400)]
    assert fake.parsed == str(target)
    assert 400 in fake.released and fake.allowed == -1

def test_launch_gone_handler_falls_back_to_exe_with_quoted_argument(fake, tmp_path, monkeypatch):
    from app.memufiles import openwith
    target = tmp_path / "a b.txt"
    target.write_text("x")
    exe = tmp_path / "Tool Dir" / "tool.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    openwith.launch(str(target), f"shell:{exe}")
    assert fake.exec_infos[-1]["file"] == str(exe) and fake.exec_infos[-1]["params"] == f'"{target}"'
    with pytest.raises(openwith.OpenWithError) as err:
        openwith.launch(str(target), r"shell:C:\nope\missing.exe")
    assert err.value.code == "handler_gone"

def test_launch_registry_class_uses_classname_mask(fake, tmp_path):
    from app.memufiles import openwith
    target = tmp_path / "x.txt"
    target.write_text("x")
    openwith.launch(str(target), "class:Applications\\notepad.exe")
    info = fake.exec_infos[-1]
    assert info["cls"] == "Applications\\notepad.exe" and info["verb"] == "open"
    assert info["mask"] & openwith.SEE_MASK_CLASSNAME and info["mask"] & openwith.SEE_MASK_NOASYNC
    with pytest.raises(openwith.OpenWithError):
        openwith.launch(str(target), "bogus")
    with pytest.raises(FileNotFoundError):
        openwith.launch(str(tmp_path / "missing.txt"), "class:x")

def test_registry_fallback_when_shell_enumeration_fails(fake, tmp_path, monkeypatch):
    from app.memufiles import openwith
    HKCU, HKCR = "HKCU", "HKCR"
    data = {
        (HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.log\OpenWithList"):
            {"values": [("a", "code.exe"), ("b", "stale.exe"), ("MRUList", "ba")]},
        (HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.log\OpenWithProgids"):
            {"values": [("txtfile", b"")]},
        (HKCR, r".log\OpenWithProgids"): {"values": [("txtfile", b""), ("Dead.ProgId", b"")]},
        (HKCR, r".log\OpenWithList"): {"keys": ["code.exe"]},
    }

    class Key:
        def __init__(self, entry):
            self.entry = entry

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def open_key(root, sub):
        if (root, sub) not in data:
            raise OSError(sub)
        return Key(data[(root, sub)])

    def enum_value(key, index):
        values = key.entry.get("values", [])
        if index >= len(values):
            raise OSError
        name, value = values[index]
        return name, value, 1

    def enum_key(key, index):
        keys = key.entry.get("keys", [])
        if index >= len(keys):
            raise OSError
        return keys[index]

    monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace(
        HKEY_CURRENT_USER=HKCU, HKEY_CLASSES_ROOT=HKCR, OpenKey=open_key, EnumValue=enum_value, EnumKey=enum_key))
    names = {"code.exe": ("Visual Studio Code", r"C:\VS Code\Code.exe"), "txtfile": ("Блокнот", r"C:\Windows\notepad.exe")}

    def assoc(assoc_name, what, flags=0):
        name, exe = names.get(assoc_name, ("", ""))
        if assoc_name == ".log":
            return r"C:\VS Code\Code.exe" if what == openwith.ASSOCSTR_EXECUTABLE else ""
        return name if what == openwith.ASSOCSTR_FRIENDLYAPPNAME else exe

    monkeypatch.setattr(openwith, "assoc_query", assoc)

    def broken(ext):
        raise openwith.OpenWithError("enum_failed")
    monkeypatch.setattr(openwith, "_enum_shell", broken)
    target = tmp_path / "app.log"
    target.write_text("x")
    items = openwith.handlers(str(target))
    assert [(i["name"], i["id"], i["default"]) for i in items] == [
        ("Visual Studio Code", "class:Applications\\code.exe", True),
        ("Блокнот", "class:txtfile", False),
    ]

def test_chooser_dialog_cancel_and_fallbacks(fake, tmp_path, monkeypatch):
    from app.memufiles import openwith
    target = tmp_path / "Новая папка" / "фото 1.heic"
    target.parent.mkdir()
    target.write_bytes(b"x")
    assert openwith.chooser(str(target), hwnd=4242) == "dialog"
    hwnd, file, flags = fake.chooser_args
    assert getattr(hwnd, "value", hwnd) == 4242 and file == str(target) and flags == 0x7
    fake.chooser_hr = openwith.ERROR_CANCELLED_HR - (1 << 32)
    assert openwith.chooser(str(target)) == "cancelled"
    fake.chooser_hr = -2147467259
    assert openwith.chooser(str(target)) == "openas"
    assert fake.exec_infos[-1]["verb"] == "openas" and fake.exec_infos[-1]["mask"] & openwith.SEE_MASK_INVOKEIDLIST
    fake.shell_execute_ok = False
    started = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert openwith.chooser(str(target)) == "rundll"
    assert isinstance(started[0], str) and started[0].endswith("shell32.dll,OpenAs_RunDLL " + str(target))

def test_worker_serialises_and_reraises(monkeypatch):
    from app.memufiles import openwith
    monkeypatch.setattr(openwith, "IS_WIN", False)
    worker = openwith._StaWorker()
    seen = []
    threads = [threading.Thread(target=lambda i=i: seen.append(worker.call(lambda: (time.sleep(0.01), i)[1]))) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(seen) == list(range(8))

    def boom():
        raise openwith.OpenWithError("x")
    with pytest.raises(openwith.OpenWithError):
        worker.call(boom)
    assert worker.call(lambda: 5) == 5

def test_fsops_off_windows_reports_unsupported(tmp_path, monkeypatch):
    from app.memufiles import fsops
    monkeypatch.setattr(fsops, "IS_WIN", False)
    with pytest.raises(fsops.FsError) as err:
        fsops.open_with_dialog(str(tmp_path))
    assert err.value.code == "unsupported"

@pytest.fixture()
def server(tmp_path):
    from app.memufiles import server as srv
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    yield app
    httpd.shutdown()

def call(app, path, body=None, token=True):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method="POST" if body is not None else "GET")
    if token:
        req.add_header("X-MF-Token", app.token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            ctype = resp.headers.get("Content-Type", "")
            content = resp.read()
            return resp.status, (json.loads(content) if ctype.startswith("application/json") else content)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")

def test_server_openwith_routes(server, tmp_path, monkeypatch):
    from app.memufiles import openwith, fsops
    monkeypatch.setattr(openwith, "IS_WIN", False)
    monkeypatch.setattr(fsops, "IS_WIN", False)
    f = tmp_path / "doc.txt"
    f.write_text("x")
    status, data = call(server, "/api/openwith?path=" + urllib.parse.quote(str(f)))
    assert status == 200 and data["handlers"] == [] and data["supported"] is False
    status, data = call(server, "/api/openwith", {"path": str(f)})
    assert status == 400 and data["error"] == "unsupported"
    launched = []
    monkeypatch.setattr(openwith, "launch", lambda p, h: launched.append((p, h)))
    status, data = call(server, "/api/openwith", {"path": str(f), "handler": "shell:x.exe"})
    assert status == 200 and launched == [(str(f), "shell:x.exe")]
    monkeypatch.setattr(openwith, "icon_png", lambda hid: b"\x89PNG" if hid == "shell:x.exe" else None)
    status, body = call(server, "/api/appicon?id=shell%3Ax.exe&t=" + server.token, token=False)
    assert status == 200 and body == b"\x89PNG"
    status, _ = call(server, "/api/appicon?id=nope", token=True)
    assert status == 404
    status, _ = call(server, "/api/appicon?id=shell%3Ax.exe", token=False)
    assert status == 401

def test_custom_titlebar_setting_and_controller(monkeypatch):
    import os as real_os
    from app import win_titlebar
    from app.memufiles import desktop, settings
    assert settings.load()["custom_titlebar"] is True
    monkeypatch.setattr(desktop, "os", types.SimpleNamespace(name="posix", environ={}, path=real_os.path))
    assert desktop._create_titlebar(set()) is None
    monkeypatch.setattr(desktop, "os", types.SimpleNamespace(name="nt", environ={}, path=real_os.path))
    made = []

    class Fake:
        def __init__(self, **kw):
            made.append(kw)
            self.mode = "native" if kw["cli_native"] else kw["preference"]
    monkeypatch.setattr(win_titlebar, "TitlebarController", Fake)
    assert desktop._create_titlebar(set()).mode == "custom"
    settings.save({"custom_titlebar": False})
    assert desktop._create_titlebar(set()).mode == "native"
    settings.save({"custom_titlebar": True})
    assert desktop._create_titlebar({"--native-titlebar"}).mode == "native"
    assert desktop._attach_titlebar(None, object()) is False

    assert win_titlebar.TitlebarController.__name__ == "Fake"

def test_spec_and_build_know_new_modules():
    import build
    hidden = build.TARGETS["memufiles"]["hidden"]
    assert "app.memufiles.openwith" in hidden and "app.win_titlebar" in hidden
    spec = (ROOT / "MemuFiles.spec").read_text(encoding="utf-8")
    assert "app.memufiles.openwith" in spec and "app.win_titlebar" in spec

def test_i18n_new_keys_both_languages():
    text = (ROOT / "app/memufiles/static/js/i18n.js").read_text(encoding="utf-8")
    for key in ("ctx.open_with_menu", "ctx.choose_app", "ctx.default_app", "ctx.no_apps", "ctx.loading", "wc.minimize",
                "wc.maximize", "wc.restore", "wc.close", "wc.group", "set.custom_titlebar", "err.handler_gone",
                "err.invoke_failed", "err.timeout"):
        assert text.count(f'"{key}":') == 2, key

def _chromium():
    import os
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    if not Path(exe).exists():
        pytest.skip("no chromium")
    return exe

BRIDGE = """
window.__tb = [];
window.pywebview = { api: {
  titlebar_state: async () => ({ ok: true, installed: true, supported: true, mode: "custom", maximized: false, active: true, os: "win11", doubleClickMs: 500 }),
  titlebar_drag: async (e) => { window.__tb.push("drag:" + e); return true; },
  titlebar_command: async (c) => { window.__tb.push("cmd:" + c); return true; },
  titlebar_system_menu: async (s) => { window.__tb.push("menu:" + s); return true; },
  titlebar_set_mode: async (m) => ({ ok: true, installed: m === "custom", supported: true, mode: m }),
} };
setTimeout(() => window.dispatchEvent(new Event("pywebviewready")), 30);
"""

def test_ui_titlebar_and_open_with_submenu(server, tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    folder = tmp_path / "Папка"
    folder.mkdir()
    (folder / "заметка 1.txt").write_text("x", encoding="utf-8")
    from app.memufiles import openwith
    url = f"http://127.0.0.1:{server.port}/index.html#t={server.token}"
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:

            page = browser.new_page(viewport={"width": 1100, "height": 700})
            page.goto(url)
            page.wait_for_selector(".tabbar .tab")
            assert page.locator(".memu-wc").count() == 0
            page.close()

            page = browser.new_page(viewport={"width": 1100, "height": 700})
            page.add_init_script(BRIDGE)
            page.goto(url)
            page.wait_for_selector(".tabbar .memu-wc .memu-wc-close")
            assert page.evaluate("document.documentElement.dataset.titlebar") == "custom"
            assert page.evaluate("document.querySelector('.memu-wc').parentElement.classList.contains('tabbar')")
            tabs_before = page.locator(".tabbar .tab").count()
            box = page.locator(".tabbar .tab-add").bounding_box()
            x, y = box["x"] + box["width"] + 60, box["y"] + 10
            page.mouse.click(x, y)
            time.sleep(0.6)
            page.mouse.click(x, y, click_count=2)
            time.sleep(0.2)
            calls = page.evaluate("window.__tb")
            assert calls[0] == "drag:caption" and "cmd:toggle" in calls
            assert page.locator(".tabbar .tab").count() == tabs_before
            page.click(".memu-wc-close")
            page.click(".memu-wc-minimize")
            page.mouse.click(x, y, button="right")
            calls = page.evaluate("window.__tb")
            assert "cmd:close" in calls and "cmd:minimize" in calls and "menu:mouse" in calls
            page.locator(".memu-tb-resize [data-edge=top]").dispatch_event("mousedown", {"button": 0})
            assert "drag:top" in page.evaluate("window.__tb")
            page.close()
        finally:
            browser.close()

def test_ui_open_with_submenu_lists_apps(server, tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import openwith
    folder = tmp_path / "Папка"
    folder.mkdir()
    (folder / "заметка 1.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(openwith, "IS_WIN", True)
    monkeypatch.setattr(openwith, "handlers", lambda p: [
        {"id": "shell:C:\\n.exe", "name": "Блокнот", "exe": "C:\\n.exe", "default": True, "icon": False},
        {"id": "shell:C:\\c.exe", "name": "VS Code", "exe": "C:\\c.exe", "default": False, "icon": False}])
    launched, chooser = [], []
    monkeypatch.setattr(openwith, "launch", lambda p, h: launched.append((p, h)))
    from app.memufiles import fsops
    monkeypatch.setattr(fsops, "open_with_dialog", lambda p, hwnd=0: chooser.append(p) or "dialog")
    url = f"http://127.0.0.1:{server.port}/index.html#t={server.token}"
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 700})
            page.goto(url)
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("() => import('/js/core.js').then(m => { m.S.platform = 'nt'; })")
            page.evaluate("p => window.MemuFiles.pane().navigate(p)", str(folder))
            row = page.locator(".row", has_text="заметка 1.txt")
            row.first.wait_for()
            row.first.click(button="right")
            page.hover(".menu >> text=Открыть с помощью")
            page.wait_for_selector(".menu >> text=VS Code")
            assert page.locator(".menu >> text=Блокнот (по умолчанию)").count() == 1
            page.click(".menu >> text=VS Code")
            time.sleep(0.3)
            assert launched and launched[0][1] == "shell:C:\\c.exe"
            row.first.click(button="right")
            page.hover(".menu >> text=Открыть с помощью")
            page.click(".menu >> text=Выбрать другое приложение…")
            time.sleep(0.3)
            assert chooser and chooser[0].endswith("заметка 1.txt")
        finally:
            browser.close()
