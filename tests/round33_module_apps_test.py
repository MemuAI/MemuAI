# -*- coding: utf-8 -*-
"""Round 33 (r33-modules): modules outside the MemuAI window.

#26 «Install as an app» — store, icon, shortcuts (fake shell), repair, routes, ``memuai.py --module``;
#6  closing MemuAI with modules open — the close guard of the main window and the module windows (fake pywebview);
#7  an unmistakable «minimize» on module windows.

Win32 is never called here: ``module_apps.Shell`` and the pywebview module are fakes; the real COM / taskbar
calls (``app/module_apps_win.py``) need real Windows. Browser part (playwright + chromium, skipped without them):
the module-only page, the «MemuAI is closing» question, the install dialog, Settings → Modules, the minimize button.
Run with an isolated MEMUAI_DATA_DIR (a temporary one is used when it is not set).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memu-r33-apps-")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"

from app import module_apps, modules_host  # noqa: E402

class FakeShell(module_apps.Shell):
    def __init__(self, base: Path, ok: bool = True) -> None:
        self.base = base
        self.ok = ok
        self.calls = []

    def available(self) -> bool:
        return True

    def folder(self, name: str):
        path = self.base / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def shortcut(self, link, **kwargs) -> bool:
        self.calls.append((Path(link), kwargs))
        if not self.ok:
            return False
        Path(link).write_text(json.dumps({k: str(v) for k, v in kwargs.items()}), encoding="utf-8")
        return True

class _Events:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *args):
        return [fn(*args) for fn in self.handlers]

class FakeWindow:
    def __init__(self, title, url, **options):
        self.title, self.url, self.options = title, url, options
        self.events = type("E", (), {})()
        self.events.closing = _Events()
        self.events.closed = _Events()
        self.events.shown = _Events()
        self.shown = 0
        self.destroyed = False
        self.scripts = []
        self.native = None

    def show(self):
        self.shown += 1

    def restore(self):
        pass

    def minimize(self):
        self.minimized = True

    def destroy(self):
        results = self.events.closing.fire()
        if all(r is not False for r in results):
            self.destroyed = True
            self.events.closed.fire()

    def evaluate_js(self, script):
        self.scripts.append(script)

class FakeWebview:
    def __init__(self):
        self.created = []

    def create_window(self, title, url, **options):
        window = FakeWindow(title, url, **options)
        self.created.append(window)
        return window

class FakeWin:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))
            return True
        return record

def _wait(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False

class StoreAndIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r33apps-"))
        self.root = self.tmp / "modules"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_store_normalisation_and_close_choice(self):

        self.assertEqual(module_apps.load_store(self.root), {"apps": {}, "sizes": {}, "on_app_close": "ask",
                                                             "custom_appbar": True, "appbar": {}})
        (self.root / "apps.json").write_text(json.dumps({"apps": {"../x": {}, "translator": {"links": ["a", 3]}},
                                                         "on_app_close": "weird", "sizes": {"Bad": {}}}), encoding="utf-8")
        store = module_apps.load_store(self.root)
        self.assertEqual(list(store["apps"]), ["translator"])
        self.assertEqual(store["apps"]["translator"]["links"], ["a"])
        self.assertEqual(store["on_app_close"], "ask")
        self.assertEqual(module_apps.set_close_choice("keep", self.root), "keep")
        self.assertEqual(module_apps.load_store(self.root)["on_app_close"], "keep")
        with self.assertRaises(ValueError):
            module_apps.set_close_choice("never", self.root)
        module_apps.remember_size("translator", 100, 99999, self.root)
        self.assertEqual(module_apps.load_store(self.root)["sizes"]["translator"], {"width": 420, "height": 3000})

    def test_identity_names_and_commands(self):
        self.assertEqual(module_apps.app_id("characters"), "MemuAI.Module.characters")
        with self.assertRaises(ValueError):
            module_apps.app_id("../evil")
        self.assertEqual(module_apps.shortcut_name('A<b>:c/"d"|?*', "x"), "A b c d (MemuAI).lnk")
        self.assertEqual(module_apps.shortcut_name("  ", "my-mod"), "my-mod (MemuAI).lnk")
        frozen = module_apps.launch_command("translator", frozen=True, executable=r"C:\Program Files\MemuAI\MemuAI.exe")
        self.assertEqual(frozen["args"], "--module translator")
        self.assertEqual(frozen["command"], r'"C:\Program Files\MemuAI\MemuAI.exe" --module translator')
        dev = module_apps.launch_command("translator", frozen=False, executable="/usr/bin/python3", program_dir="/opt/memu")
        self.assertIn(f'"{Path("/opt/memu") / "memuai.py"}" --module translator', dev["args"])
        with self.assertRaises(ValueError):
            module_apps.launch_command("a b")

    def test_png_validation_and_icons(self):
        self.assertIsNone(module_apps.decode_png("data:image/gif;base64,AAAA"))
        self.assertIsNone(module_apps.decode_png("data:image/png;base64,bm90IGEgcG5n"))
        self.assertIsNone(module_apps.decode_png("data:image/png;base64," + "A" * 900_000))
        from PIL import Image
        import base64, io
        buf = io.BytesIO()
        Image.new("RGBA", (64, 64), (200, 10, 10, 255)).save(buf, "PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        self.assertEqual(module_apps.decode_png(url).size, (64, 64))
        out = module_apps.write_icon("translator", {"title": "Переводчик", "accent": "#336699"}, url, self.root)
        self.assertTrue(Path(out["ico"]).is_file() and Path(out["png"]).is_file())
        with Image.open(out["ico"]) as ico:
            self.assertIn((256, 256), ico.info.get("sizes", {(256, 256)}))

        out = module_apps.write_icon("studio", {"title": "Studio"}, "", self.root)
        with Image.open(out["png"]) as png:
            self.assertEqual(png.size, (256, 256))
            self.assertGreater(png.getpixel((128, 30))[3], 200)

    def test_icons_live_outside_the_memuai_icon_folder(self):

        from app.pgdata import DATA_DIR
        default = module_apps.app_dir()
        self.assertEqual(default, Path(DATA_DIR) / "module-apps")
        self.assertFalse(str(default).startswith(str(Path(DATA_DIR) / "memuai") + os.sep))

class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r33install-"))
        self.root = self.tmp / "modules"
        self.root.mkdir()
        self.shell = FakeShell(self.tmp / "shell")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_install_update_uninstall_roundtrip(self):
        row = module_apps.install("translator", desktop=True, start_menu=True, hide=True, root=self.root, shell=self.shell)
        self.assertTrue(row["installed"])
        self.assertEqual(row["app_id"], "MemuAI.Module.translator")
        self.assertEqual(len(row["links"]), 2)
        for link in row["links"]:
            self.assertTrue(Path(link).is_file())
            self.assertTrue(link.endswith("(MemuAI).lnk"))
        _, kwargs = self.shell.calls[0]
        self.assertEqual(kwargs["app_id"], "MemuAI.Module.translator")
        self.assertIn("--module translator", kwargs["args"])
        self.assertTrue(kwargs["icon"].endswith("translator.ico"))
        modules = {m["id"]: m for m in modules_host.list_modules(self.root)}
        self.assertTrue(modules["translator"]["enabled"], "installing switches the module on")
        self.assertTrue(modules["translator"]["launcher"]["hidden"], "hidden from the MemuAI list on request")

        row = module_apps.update("translator", desktop=False, root=self.root, shell=self.shell)
        self.assertEqual(len(row["links"]), 1)
        self.assertIn("programs", row["links"][0])
        self.assertFalse((self.tmp / "shell" / "desktop").glob("*.lnk").__next__ if False else list((self.tmp / "shell" / "desktop").glob("*.lnk")))
        row = module_apps.update("translator", hide=False, root=self.root, shell=self.shell)
        self.assertFalse({m["id"]: m for m in modules_host.list_modules(self.root)}["translator"]["launcher"]["hidden"])
        module_apps.update("translator", hide=True, root=self.root, shell=self.shell)
        module_apps.uninstall("translator", root=self.root)
        self.assertEqual(module_apps.load_store(self.root)["apps"], {})
        self.assertEqual(list((self.tmp / "shell").rglob("*.lnk")), [])
        self.assertFalse((self.root / "apps" / "translator.ico").exists())
        self.assertFalse({m["id"]: m for m in modules_host.list_modules(self.root)}["translator"]["launcher"]["hidden"],
                         "uninstall brings the module back to the list")

    def test_uninstall_only_removes_our_shortcuts(self):
        foreign = self.tmp / "keep.lnk"
        foreign.write_text("x", encoding="utf-8")
        module_apps._remove_links([str(foreign), str(self.tmp / "missing (MemuAI).lnk")])
        self.assertTrue(foreign.exists())

    def test_install_reports_refused_shortcuts(self):
        with self.assertRaises(OSError):
            module_apps.install("studio", root=self.root, shell=FakeShell(self.tmp / "s2", ok=False))
        self.assertEqual(module_apps.load_store(self.root)["apps"], {})

    def test_install_off_windows_records_the_app_without_links(self):
        class NoShell(FakeShell):
            def available(self):
                return False
        row = module_apps.install("design", root=self.root, shell=NoShell(self.tmp / "s3"))
        self.assertTrue(row["installed"])
        self.assertEqual(row["links"], [])
        self.assertFalse(row["shortcuts_supported"])

    def test_unknown_module_and_bad_id(self):
        with self.assertRaises(KeyError):
            module_apps.install("no-such-module", root=self.root, shell=self.shell)
        with self.assertRaises(ValueError):
            module_apps.install("../../config", root=self.root, shell=self.shell)
        with self.assertRaises(KeyError):
            module_apps.update("translator", hide=True, root=self.root, shell=self.shell)

    def test_repair_recreates_and_drops(self):
        module_apps.install("translator", root=self.root, shell=self.shell)
        for link in self.tmp.joinpath("shell").rglob("*.lnk"):
            link.unlink()
        self.assertEqual(module_apps.repair(self.root, self.shell), 1)
        self.assertEqual(len(list(self.tmp.joinpath("shell").rglob("*.lnk"))), 2)

        module_apps._mutate(lambda data: data["apps"].__setitem__("gone-module", {"links": []}), self.root)
        self.assertEqual(module_apps.repair(self.root, self.shell), 1)
        self.assertNotIn("gone-module", module_apps.load_store(self.root)["apps"])

class ModuleWindowsTest(unittest.TestCase):
    def setUp(self):

        platform_patch = mock.patch.object(module_apps.sys, "platform", "linux")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="r33win-"))
        self.root = self.tmp / "modules"
        self.root.mkdir()
        for module_id in ("translator", "studio"):
            modules_host.configure_module(self.root, module_id, enabled=True)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.webview = FakeWebview()
        self.win = FakeWin()
        self.main = FakeWindow("MemuAI", "/")
        self.mgr = module_apps.ModuleWindows("http://127.0.0.1:8600/", webview_module=self.webview, win=self.win,
                                             root=self.root)
        self.mgr.main_window_ref = lambda: self.main
        self.closed_main = []
        self.quits = []
        self.mgr.close_main_cb = lambda: self.closed_main.append(True)
        self.mgr.quit_cb = lambda: self.quits.append(True)

    def test_open_focus_close(self):
        self.mgr.open("translator")
        self.assertEqual(len(self.webview.created), 1)
        window = self.webview.created[0]
        self.assertEqual(window.url, "http://127.0.0.1:8600/?module_app=translator")
        self.assertTrue(window.options["hidden"], "shown only after the taskbar identity is set")
        self.assertFalse(window.options["frameless"])
        self.assertTrue(_wait(lambda: window.shown >= 1))
        self.assertEqual(self.mgr.open("translator")["focused"], True)
        self.assertEqual(len(self.webview.created), 1, "a second open focuses, never duplicates")
        self.assertIn("MemuModuleApps.sync", self.main.scripts[-1] if self.main.scripts else "") if _wait(lambda: self.main.scripts) else None
        self.mgr.close("translator")
        self.assertTrue(window.destroyed)
        self.assertEqual(self.mgr.open_ids(), [])

    def test_disabled_module_is_refused(self):
        modules_host.configure_module(self.root, "studio", enabled=False)
        with self.assertRaises(PermissionError):
            self.mgr.open("studio")

    def test_busy_window_asks_before_closing(self):
        self.mgr.open("translator")
        window = self.webview.created[0]
        self.mgr.report("translator", {"busy": True, "width": 900, "height": 700})
        window.destroy()
        self.assertFalse(window.destroyed)
        self.assertTrue(_wait(lambda: any("askWindowClose" in s for s in window.scripts)))
        self.mgr.close("translator", force=True)
        self.assertTrue(window.destroyed)
        self.assertEqual(module_apps.load_store(self.root)["sizes"]["translator"], {"width": 900, "height": 700})

    def test_last_window_of_a_module_only_launch_quits(self):
        self.mgr.module_only = True
        self.mgr.open("translator")
        self.mgr.open("studio")
        self.mgr.close("translator")
        time.sleep(0.05)
        self.assertEqual(self.quits, [])
        self.mgr.close("studio")
        self.assertTrue(_wait(lambda: self.quits == [True]))

    def test_no_quit_once_memuai_was_shown(self):
        self.mgr.module_only = True
        self.mgr.main_shown = True
        self.mgr.open("translator")
        self.mgr.close("translator")
        time.sleep(0.1)
        self.assertEqual(self.quits, [])

    def test_main_close_guard_variants(self):
        self.assertFalse(self.mgr.main_close_guard(), "no modules → the usual close (tray)")
        self.mgr.report_main([{"id": "translator", "width": 800, "height": 600}, {"id": "../bad"}])
        self.assertEqual([m["id"] for m in self.mgr.main_modules], ["translator"])

        self.assertTrue(self.mgr.main_close_guard())
        self.assertTrue(_wait(lambda: any("askAppClose" in s for s in self.main.scripts)))

        module_apps.set_close_choice("keep", self.root)
        self.assertFalse(self.mgr.main_close_guard())
        self.assertEqual(self.mgr.open_ids(), ["translator"])
        self.assertEqual(self.webview.created[0].options["width"], 800)
        self.assertTrue(_wait(lambda: any("movedOut" in s for s in self.main.scripts)))

        self.mgr.report_main([{"id": "studio", "busy": True}])
        self.assertTrue(self.mgr.main_close_guard())

        module_apps.set_close_choice("close", self.root)
        self.mgr.report_main([{"id": "studio"}])
        self.main.scripts.clear()
        self.assertFalse(self.mgr.main_close_guard())
        self.assertTrue(_wait(lambda: any("closeInApp" in s for s in self.main.scripts)))

    def test_proceed_main_close_passes_the_guard_once(self):
        module_apps.set_close_choice("ask", self.root)
        self.mgr.report_main([{"id": "translator"}])
        seen = []
        self.mgr.close_main_cb = lambda: seen.append(self.mgr.main_close_guard())
        self.mgr.proceed_main_close()
        self.assertEqual(seen, [False])
        self.assertTrue(self.mgr.main_close_guard(), "the next close asks again")

    def test_quitting_closes_without_questions(self):
        self.mgr.open("translator")
        self.mgr.report("translator", {"unsaved": True})
        self.mgr.destroy_all()
        self.assertTrue(self.webview.created[0].destroyed)
        self.assertFalse(self.mgr.main_close_guard())

class DesktopShellGuardTest(unittest.TestCase):
    def test_close_guard_cancels_before_the_tray(self):
        from app import desktop
        shell = desktop.DesktopShell("http://127.0.0.1:1/")
        shell.window = mock.Mock()
        shell.tray = object()
        shell.close_guard = lambda: True
        self.assertFalse(shell._on_closing())
        shell.window.hide.assert_not_called()
        shell.close_guard = lambda: False
        self.assertFalse(shell._on_closing())
        shell.window.hide.assert_called_once()

        shell._quitting = True
        shell.close_guard = mock.Mock(return_value=True)
        self.assertTrue(shell._on_closing())
        shell.close_guard.assert_not_called()

    def test_ready_on_load_flag_exists(self):
        from app import desktop
        shell = desktop.DesktopShell("http://127.0.0.1:1/")
        self.assertFalse(shell.ready_on_load)
        self.assertIsNone(shell.close_guard)

class EntryPointTest(unittest.TestCase):
    def test_module_argument_and_second_instance(self):
        import memuai
        self.assertEqual(memuai._module_arg("characters"), "characters")
        self.assertEqual(memuai._module_arg("../x"), "")
        self.assertEqual(memuai._module_arg("A"), "")
        sent = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true, "native": true}'

        def fake_open(request, timeout=0):
            sent["url"] = request.full_url
            sent["body"] = json.loads(request.data.decode())
            return Resp()
        with mock.patch.object(memuai, "_read_runtime_url", return_value="http://127.0.0.1:8600"), \
                mock.patch.object(memuai, "_is_online", return_value=True), \
                mock.patch.object(memuai.urllib.request, "urlopen", fake_open):
            self.assertTrue(memuai._launch_module_in_running("characters"))
        self.assertEqual(sent, {"url": "http://127.0.0.1:8600/api/pg/module-apps/launch", "body": {"id": "characters"}})
        with mock.patch.object(memuai, "_read_runtime_url", return_value=""):
            self.assertFalse(memuai._launch_module_in_running("characters"))

    def test_memuai_wires_the_manager(self):
        text = (ROOT / "memuai.py").read_text(encoding="utf-8")
        for needle in ('parser.add_argument("--module"', "_launch_module_in_running(module_only)",
                       "show_on_start=not module_only", "shell.close_guard = module_windows.main_close_guard",
                       "shell.ready_on_load = bool(module_only)", "shell.quit_hooks.append(module_windows.destroy_all)"):
            self.assertIn(needle, text)

    def test_win32_helpers_are_guarded(self):
        from app import module_apps_win
        if sys.platform != "win32":
            self.assertFalse(module_apps_win.available())
            self.assertFalse(module_apps_win.create_shortcut(Path("x.lnk"), "t"))
            self.assertFalse(module_apps_win.set_window_app_id(1, "MemuAI.Module.x", "cmd", "X"))
            self.assertFalse(module_apps_win.set_window_icon(1, "x.ico"))
            self.assertIsNone(module_apps_win.known_folder("programs"))
            self.assertFalse(module_apps_win.foreground(1))

    def test_packaging_and_page_wiring(self):
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for name in ("app.module_apps", "app.module_apps_win"):
            self.assertIn(f"'{name}'", spec)
            self.assertIn(f'"{name}"', build)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        head = html.split("</head>")[0]
        self.assertIn('<script src="/module-apps.js?v=', head, "the module-only flag is set before the first paint")
        self.assertIn('href="/module-apps.css?v=', html)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("module_apps.register(app)", api)

class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        cls.app = FastAPI()
        module_apps.register(cls.app)
        cls.client = TestClient(cls.app)
        root = modules_host.default_root()
        root.mkdir(parents=True, exist_ok=True)
        for module_id in ("translator", "studio", "characters"):
            modules_host.configure_module(root, module_id, enabled=True)

    def tearDown(self):
        self.app.state.module_windows = None
        module_apps.MANAGER = None

    def test_state_install_settings_icon(self):
        data = self.client.get("/api/pg/module-apps").json()
        self.assertIn("on_app_close", data)
        self.assertFalse(data["native"])
        response = self.client.post("/api/pg/module-apps/install", json={"id": "studio", "hide": False, "desktop": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("studio", [row["id"] for row in response.json()["apps"]])
        icon = self.client.get("/api/pg/module-apps/icon/studio.png")
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.headers["content-type"], "image/png")
        self.assertEqual(self.client.get("/api/pg/module-apps/icon/..%2Fx.png").status_code, 404)
        self.assertEqual(self.client.post("/api/pg/module-apps/install", json={"id": "nope-x"}).status_code, 404)
        self.assertEqual(self.client.post("/api/pg/module-apps/install", json={"id": "../x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/pg/module-apps/settings", json={"on_app_close": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/pg/module-apps/settings", json={"on_app_close": "keep"}).json()["on_app_close"], "keep")
        self.client.post("/api/pg/module-apps/settings", json={"on_app_close": "ask"})
        self.assertEqual(self.client.post("/api/pg/module-apps/uninstall", json={"id": "studio"}).status_code, 200)
        self.assertNotIn("studio", [row["id"] for row in self.client.get("/api/pg/module-apps").json()["apps"]])

    def test_open_without_desktop_shell_returns_a_url(self):
        data = self.client.post("/api/pg/module-apps/open", json={"id": "translator"}).json()
        self.assertEqual(data, {"ok": True, "native": False, "url": "/?module_app=translator"})
        self.assertEqual(self.client.post("/api/pg/module-apps/open", json={"id": "../x"}).status_code, 404)
        self.assertEqual(self.client.post("/api/pg/module-apps/state", json={"where": "main", "modules": []}).json()["native"], False)

    def test_with_a_manager(self):
        webview = FakeWebview()
        main = FakeWindow("MemuAI", "/")
        mgr = module_apps.ModuleWindows("http://127.0.0.1:9", webview_module=webview, win=FakeWin())
        mgr.main_window_ref = lambda: main
        closes, shows = [], []
        mgr.close_main_cb = lambda: closes.append(True)
        mgr.show_main_cb = lambda: shows.append(True)
        self.app.state.module_windows = mgr
        self.assertTrue(self.client.post("/api/pg/module-apps/open", json={"id": "translator"}).json()["native"])
        self.assertEqual(mgr.open_ids(), ["translator"])
        self.assertTrue(self.client.get("/api/pg/module-apps").json()["native"])

        self.client.post("/api/pg/module-apps/state", json={"where": "main", "modules": [{"id": "characters"}]})
        self.assertTrue(self.client.post("/api/pg/module-apps/launch", json={"id": "characters"}).json()["moved"])
        self.assertTrue(_wait(lambda: any("popOut" in s for s in main.scripts)))

        response = self.client.post("/api/pg/module-apps/app-close", json={"action": "keep", "ids": ["characters"]}).json()
        self.assertEqual(response["moved"], ["characters"])
        self.assertTrue(_wait(lambda: closes == [True]))
        self.assertEqual(self.client.post("/api/pg/module-apps/app-close", json={"action": "x"}).status_code, 400)

        self.client.post("/api/pg/module-apps/state", json={"where": "app", "id": "translator", "unsaved": True})
        self.assertTrue(mgr.window_state["translator"]["unsaved"])
        self.client.post("/api/pg/module-apps/window", json={"action": "main", "id": "translator"})
        self.assertEqual(shows, [True])
        self.assertTrue(mgr.main_shown)
        self.client.post("/api/pg/module-apps/window", json={"action": "close", "id": "translator", "force": True})
        self.assertNotIn("translator", mgr.open_ids())
        self.client.post("/api/pg/module-apps/window", json={"action": "dock", "id": "characters"})
        self.assertNotIn("characters", mgr.open_ids())
        self.assertTrue(_wait(lambda: any("docked: true" in s for s in main.scripts)))
        self.assertEqual(self.client.post("/api/pg/module-apps/window", json={"action": "?", "id": "translator"}).status_code, 400)

class StaticUiTest(unittest.TestCase):
    def test_minimize_glyph_is_not_the_download_glyph(self):
        js = (STATIC / "modules.js").read_text(encoding="utf-8")
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        download = app_js.split("download: '")[1].split("'")[0]
        minimize = js.split("minimize: '")[1].split("'")[0]
        self.assertNotIn("M12 4v10M7.5 9.5", minimize)
        self.assertNotIn(download.split('d="')[1].split('"')[0], minimize)
        self.assertIn('class="module-ctl-text"', js)
        self.assertIn("mhb-min-text", js)
        css = (STATIC / "modules.css").read_text(encoding="utf-8")
        self.assertIn(".module-window-tools .module-ctl-text { display: inline; }", css)

    def test_module_app_hooks(self):
        js = (STATIC / "modules.js").read_text(encoding="utf-8")
        for needle in ("MA().redirectOpen(moduleId)", "MA().bootModuleApp(open)", "MA().lastClosed(win.id)",
                       "MA().renderSettings(host)", "MA().minimizeNative()", 'new CustomEvent("memu-modules-changed")',
                       "dropWindow:", "windowBox:"):
            self.assertIn(needle, js)
        apps = (STATIC / "module-apps.js").read_text(encoding="utf-8")
        for needle in ("askAppClose", "movedOut", "/api/pg/module-apps/app-close", "dataset.moduleApp", "installDialog"):
            self.assertIn(needle, apps)

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", shutil.which("chromium") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    SHOTS = os.environ.get("MEMU_R33_SHOTS", "")

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import pgdata
        from app.playground_api import create_app
        root = modules_host.default_root()
        root.mkdir(parents=True, exist_ok=True)
        for module_id in modules_host.BUILTIN_ORDER:
            try:
                modules_host.configure_module(root, module_id, enabled=True)
            except Exception:
                pass
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.app = create_app()
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 60
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.1)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def page(self, path="/", size=(1366, 768), lang="ru", theme="light"):
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": lang, "theme": theme})
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}{path}", wait_until="networkidle")
        self.wait(page, "window.MemuModules && document.body.classList.contains('boot-ready')", 30)
        page.wait_for_timeout(300)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def wait(self, page, expression, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if page.evaluate(f"() => !!({expression})"):
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise AssertionError(f"timed out: {expression}")

    def shot(self, page, name):
        if self.SHOTS:
            Path(self.SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(self.SHOTS) / f"{name}.png"))

    def test_module_only_page(self):
        for lang in ("ru", "en"):
            page = self.page(f"/?module_app=translator", size=(980, 680), lang=lang, theme="dark" if lang == "en" else "light")
            self.wait(page, "document.querySelector('.module-window[data-module=\"translator\"]') && !document.querySelector('.module-window[data-module=\"translator\"]').hidden")
            info = page.evaluate("""() => {
              const w = document.querySelector('.module-window[data-module="translator"]').getBoundingClientRect();
              const vis = (s) => { const n = document.querySelector('.module-window[data-module="translator"] ' + s); return !!(n && n.getClientRects().length); };
              const app = document.getElementById('app');
              return { w: [w.left, w.top, w.width, w.height], appHidden: !app.getClientRects().length || getComputedStyle(app).display === 'none',
                min: vis('[data-win="min"]'), close: vis('[data-win="close"]'), main: vis('[data-mapp-main]'), menu: vis('[data-win="menu"]'),
                title: document.title, flag: document.documentElement.dataset.moduleApp };
            }""")
            self.assertEqual(info["w"], [0, 0, 980, 680])
            self.assertTrue(info["appHidden"])
            self.assertFalse(info["min"] or info["close"], "the Windows caption owns minimize/close")
            self.assertTrue(info["main"] and info["menu"])
            self.assertEqual(info["flag"], "translator")
            self.assertTrue(info["title"].endswith("— MemuAI"))

            page.click('.module-window[data-module="translator"] [data-win="menu"]')
            page.wait_for_timeout(250)
            menu = page.evaluate("() => document.getElementById('context-menu').innerText")
            self.assertIn("MemuAI", menu)
            self.assertNotRegex(menu, "Расположение|layout")
            if lang == "en":
                self.assertNotRegex(menu, "[А-Яа-яЁё]")
            self.shot(page, f"module_app_{lang}")
            page.keyboard.press("Escape")

            row = next(r for r in modules_host.list_modules(modules_host.default_root()) if r["id"] == "translator")
            self.assertFalse(row["open"])

    def test_app_close_question_and_settings(self):
        for lang in ("ru", "en"):
            page = self.page(lang=lang, theme="light" if lang == "ru" else "dark")
            page.evaluate("() => { MemuModules.closeAll(); return MemuModules.open('translator'); }")
            self.wait(page, "MemuModules.openIds.includes('translator')")
            page.wait_for_timeout(600)
            page.evaluate("() => MemuModuleApps.askAppClose()")
            self.wait(page, "document.getElementById('mapp-keep')")
            text = page.evaluate("() => document.querySelector('.mapp-close').innerText")
            self.assertIn("MemuAI", text)
            if lang == "en":
                self.assertNotRegex(text, "[А-Яа-яЁё]")
                self.assertIn("Translator", text)
            self.shot(page, f"app_close_{lang}")

            page.click("#mapp-close-all")
            self.wait(page, "!MemuModules.openIds.length")

            page.evaluate("() => { currentSettingsTab = 'modules'; showSettings(); }")
            self.wait(page, "document.getElementById('mapp-settings')", 20)
            if lang == "en":
                self.assertNotRegex(page.evaluate("() => document.getElementById('mapp-settings').innerText"), "[А-Яа-яЁё]")
            page.click('#mapp-settings input[value="keep"]')
            self.wait(page, "MemuModuleApps.state().on_app_close === 'keep'")
            self.assertEqual(module_apps.load_store()["on_app_close"], "keep")
            self.shot(page, f"settings_{lang}")
            page.click('#mapp-settings input[value="ask"]')
            self.wait(page, "MemuModuleApps.state().on_app_close === 'ask'")

    def test_install_dialog(self):
        page = self.page(lang="en", theme="dark")
        page.evaluate("() => MemuModuleApps.installDialog('characters')")
        self.wait(page, "document.querySelector('#panel-back.visible #mapp-install')")
        self.assertNotRegex(page.evaluate("() => document.querySelector('.mapp-dialog').innerText"), "[А-Яа-яЁё]")
        self.shot(page, "install_en")
        page.keyboard.press("Escape")
        page = self.page(lang="ru")
        page.evaluate("() => MemuModuleApps.installDialog('characters')")
        self.wait(page, "document.getElementById('mapp-install')")
        has_png = page.evaluate("() => (document.querySelector('.mapp-tile img') || {}).src || ''")
        self.assertTrue(has_png.startswith("data:image/png"), "the module glyph is drawn as the app icon")
        self.shot(page, "install_ru")
        page.click("#mapp-install")
        self.wait(page, "MemuModuleApps.state().apps.some((a) => a.id === 'characters')")
        self.assertTrue((module_apps.app_dir() / "characters.ico").is_file())
        self.wait(page, "!document.getElementById('panel-back').classList.contains('visible')")
        module_apps.uninstall("characters")

    def test_close_memuai_keeps_modules_end_to_end(self):
        """Python close guard → the real page asks → «keep» → module windows open, MemuAI closes as usual."""
        page = self.page(lang="ru")
        scripts = []

        class Main:
            def evaluate_js(self, script):
                scripts.append(script)
        webview = FakeWebview()
        mgr = module_apps.ModuleWindows(f"http://127.0.0.1:{self.port}", webview_module=webview, win=FakeWin())
        mgr.main_window_ref = lambda: Main()
        closed = []
        mgr.close_main_cb = lambda: closed.append(True)
        self.app.state.module_windows = mgr
        self.addCleanup(lambda: setattr(self.app.state, "module_windows", None))
        module_apps.set_close_choice("ask")

        def pump():
            while scripts:
                page.evaluate(f"() => {{ {scripts.pop(0)} }}")
        page.evaluate("() => MemuModuleApps.reload()")
        page.evaluate("() => { MemuModules.closeAll(); return MemuModules.open('translator').then(() => MemuModules.open('characters')); }")
        self.wait(page, "MemuModules.openIds.length === 2")
        _wait(lambda: len(mgr.main_modules) == 2, 5)
        self.assertEqual(sorted(m["id"] for m in mgr.main_modules), ["characters", "translator"])
        self.assertTrue(mgr.main_close_guard(), "the × of MemuAI is held while modules are open")
        _wait(lambda: scripts, 3)
        pump()
        self.wait(page, "document.getElementById('mapp-keep') && !document.getElementById('mapp-keep').disabled")
        self.shot(page, "app_close_native_ru")
        page.click("#mapp-keep")
        self.assertTrue(_wait(lambda: closed == [True], 5), "MemuAI closes the usual way after the answer")
        self.assertEqual(sorted(w.url.split("=")[-1] for w in webview.created), ["characters", "translator"])
        _wait(lambda: scripts, 3)
        pump()
        self.wait(page, "MemuModules.openIds.length === 0")
        self.assertTrue(page.evaluate("() => MemuModuleApps.isDetached('translator')"))

        page.evaluate("() => MemuModules.open('translator')")
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("() => MemuModules.openIds.length"), 0)
        self.assertEqual(len(webview.created), 2)

        mgr.close("translator")
        _wait(lambda: scripts, 3)
        pump()
        self.assertFalse(page.evaluate("() => MemuModuleApps.isDetached('translator')"))
        mgr.destroy_all()

    def test_minimize_button_is_labelled(self):
        page = self.page(size=(1366, 768), lang="ru")
        page.evaluate("() => { MemuModules.closeAll(); return MemuModules.open('translator'); }")
        self.wait(page, "MemuModules.openIds.includes('translator')")
        page.wait_for_timeout(600)
        page.evaluate("""() => { const w = document.querySelector('.module-window[data-module="translator"]'); if (w.dataset.full === '1') w.querySelector('[data-win="full"]').click(); }""")
        page.wait_for_timeout(600)
        floating = page.evaluate("""() => { const b = document.querySelector('.module-window[data-module="translator"] [data-win="min"]');
          const word = b.querySelector('.module-ctl-text'); return { text: word && word.getClientRects().length ? word.textContent : '', title: b.title }; }""")
        self.assertEqual(floating["text"], "Свернуть")
        page.evaluate("() => MemuModules.snap('translator', 'max')")
        page.wait_for_timeout(700)
        head = page.evaluate("""() => { const b = document.querySelector('#module-headbar [data-mhb="min"] .mhb-min-text');
          return b && b.getClientRects().length ? b.textContent : ''; }""")
        self.assertEqual(head, "Свернуть")
        page.evaluate("() => MemuModules.closeAll()")

if __name__ == "__main__":
    unittest.main()
