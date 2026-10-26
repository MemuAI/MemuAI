# -*- coding: utf-8 -*-
"""Round 38 (r38-mod-host): a full-screen module is a «focus mode»; module apps get their own title bar.

п.4 — ``pgstatic/module-focus.js``: while a module sheet is expanded the hidden content is inert, and any MemuAI
window that opens under the module layer (settings, Ctrl+K search…) parks the module in its pill; it comes back
full screen when that window closes. Shortcuts pressed inside the module are replayed in MemuAI.
п.6 — ``app/module_appbar.py`` + ``pgstatic/module-appbar.{js,css}``: the module window loses the Windows caption
(fake controller here), the page draws the bar; switches global / per module; ``Memu.appbar`` in the bridge.
Win32 is never called. Browser part needs playwright + Chromium (MEMU_TEST_CHROMIUM). Screenshots: MEMU_R38_SHOTS.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memu-r38-host-")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"

from app import module_appbar, module_apps, modules_host  # noqa: E402

class _Events:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

class FakeWindow:
    def __init__(self, title, url, **options):
        self.title, self.url, self.options = title, url, options
        self.events = type("E", (), {"closing": _Events(), "closed": _Events()})()
        self.shown = 0
        self.scripts = []

    def show(self):
        self.shown += 1

    def destroy(self):
        pass

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
    def __getattr__(self, name):
        return lambda *a, **k: True

class FakeController:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.mode = "custom"

    def state(self):
        return {"installed": self.mode == "custom", "maximized": False, "os": "win11", "active": True}

    def start_drag(self, edge):
        self.calls.append(("drag", edge))
        return True

    def command(self, command):
        self.calls.append(("command", command))
        return True

    def system_menu(self, source):
        self.calls.append(("menu", source))
        return True

    def set_mode(self, mode):
        self.mode = mode
        return self.state()

class AppbarUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r38host-"))
        self.root = self.tmp / "modules"
        self.root.mkdir()
        for module_id in ("translator", "music"):
            modules_host.configure_module(self.root, module_id, enabled=True)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_normalize_and_builtin_styles(self):
        self.assertEqual(module_appbar.normalize({"style": "weird", "height": 999, "color": "red"}),
                         {"style": "auto", "height": 64, "color": "", "text": "", "overlay": False})
        self.assertTrue(module_appbar.normalize({"style": "transparent"})["overlay"])
        self.assertEqual(module_appbar.for_module("music", {})["style"], "transparent")
        self.assertEqual(module_appbar.for_module("music", {"appbar": {"style": "custom", "color": "#101010"}})["color"], "#101010")
        for module_id in ("music", "characters", "translator", "studio", "design", "api-models", "benchmarks", "catalog"):
            self.assertIn(module_id, module_appbar.BUILTIN)

        self.assertEqual(modules_host.normalize_window({"appbar": {"style": "none"}})["appbar"]["style"], "none")
        self.assertNotIn("appbar", modules_host.normalize_window({}))

    def test_store_switches(self):
        store = module_apps.load_store(self.root)
        self.assertTrue(store["custom_appbar"])
        self.assertTrue(module_appbar.enabled(store, "translator"))
        module_apps.set_appbar(False, "translator", self.root)
        store = module_apps.load_store(self.root)
        self.assertFalse(module_appbar.enabled(store, "translator"))
        self.assertTrue(module_appbar.enabled(store, "music"))
        module_apps.set_appbar(False, "", self.root)
        self.assertFalse(module_appbar.enabled(module_apps.load_store(self.root), "music"))
        with self.assertRaises(ValueError):
            module_apps.set_appbar(True, "../x", self.root)

    def _manager(self):
        webview = FakeWebview()
        mgr = module_apps.ModuleWindows("http://127.0.0.1:8600", webview_module=webview, win=FakeWin(), root=self.root)
        mgr._appbar_factory = FakeController
        return mgr, webview

    def test_window_gets_the_caption_bridge(self):
        mgr, webview = self._manager()
        mgr.open("music")
        window = webview.created[0]
        bridge = window.options.get("js_api")
        self.assertIsInstance(bridge, module_appbar.ModuleAppbarBridge)
        self.assertIn("music", mgr.appbars)
        state = bridge.appbar_state()
        self.assertTrue(state["installed"])
        self.assertEqual(state["appbar"]["style"], "transparent")
        self.assertTrue(bridge.appbar_drag("caption"))
        self.assertTrue(bridge.appbar_command("toggle"))
        self.assertTrue(bridge.appbar_system_menu("keyboard"))
        self.assertEqual(mgr.appbars["music"].calls, [("drag", "caption"), ("command", "toggle"), ("menu", "keyboard")])

        self.assertFalse([name for name in dir(bridge) if name.startswith("titlebar_")])
        mgr._on_closed("music")
        self.assertNotIn("music", mgr.appbars)

    def test_switch_off_means_system_frame(self):
        module_apps.set_appbar(False, "translator", self.root)
        mgr, webview = self._manager()
        mgr.open("translator")
        self.assertNotIn("js_api", webview.created[0].options)
        mgr.open("music")
        self.assertIn("js_api", webview.created[1].options)
        module_apps.set_appbar(False, "music", self.root)
        mgr.apply_appbar()
        self.assertEqual(mgr.appbars["music"].mode, "native")

    def test_snap_layouts_only_on_windows_11(self):
        pressed = []
        controller = FakeController()
        bridge = module_appbar.ModuleAppbarBridge(controller, "music", {}, snap=lambda: pressed.append(1) or True)
        self.assertTrue(bridge.appbar_snap_layouts())
        controller.state = lambda: {"installed": True, "os": "win10"}
        self.assertFalse(bridge.appbar_snap_layouts())
        self.assertEqual(pressed, [1])

    def test_off_windows_no_controller(self):
        self.assertIsNone(module_appbar.create_controller(platform="linux"))

    def test_bridge_and_static_wiring(self):
        self.assertIn("appbar", modules_host.FREE_ACTIONS)
        self.assertIn("appbar: appbar", modules_host.BRIDGE_JS)
        self.assertIn("data-memu-drag", modules_host.BRIDGE_JS)
        self.assertIn("--memu-appbar-h", modules_host.BRIDGE_JS)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("module-focus.js", "module-focus.css", "module-appbar.js", "module-appbar.css"):
            self.assertIn(f"/{name}?v=3.6-r38", html)
        js = (STATIC / "modules.js").read_text(encoding="utf-8")
        self.assertIn('case "appbar":', js)
        self.assertIn("MemuModuleFocus.replayKey", js)

    def test_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        module_apps.register(app)
        client = TestClient(app)
        data = client.post("/api/pg/module-apps/appbar", json={"id": "translator", "enabled": False}).json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["appbar"]["translator"])
        self.assertTrue(data["custom_appbar"])
        data = client.post("/api/pg/module-apps/appbar", json={"enabled": False}).json()
        self.assertFalse(data["custom_appbar"])
        self.assertFalse(client.get("/api/pg/module-apps").json()["custom_appbar"])
        client.post("/api/pg/module-apps/appbar", json={"enabled": True})
        client.post("/api/pg/module-apps/appbar", json={"id": "translator", "enabled": True})
        self.assertEqual(client.post("/api/pg/module-apps/appbar", json={"id": "../x", "enabled": True}).status_code, 400)

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", shutil.which("chromium") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    SHOTS = os.environ.get("MEMU_R38_SHOTS", "")

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

    def page(self, path="/", size=(1366, 768), lang="ru", theme="light", until="load"):
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": lang, "theme": theme})
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}{path}", wait_until=until)
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

    def _full_module(self, page, module_id="translator"):
        page.evaluate(f"() => {{ MemuModules.closeAll(); return MemuModules.open('{module_id}'); }}")
        self.wait(page, f"MemuModules.openIds.includes('{module_id}')")
        page.wait_for_timeout(500)
        page.evaluate(f"() => MemuModules.windowState('{module_id}').state === 'expanded' || document.querySelector('.module-window[data-module=\"{module_id}\"] [data-win=\"full\"]').click()")
        self.wait(page, "document.documentElement.dataset.moduleFocus === '1'")
        page.wait_for_timeout(300)

    def test_focus_mode_settings_park_and_return(self):
        page = self.page()
        self._full_module(page)
        self.assertTrue(page.evaluate("() => document.getElementById('main-content').inert"))
        self.shot(page, "focus_on")

        page.evaluate("() => { currentSettingsTab = 'modules'; showSettings(); }")
        self.wait(page, "MemuModules.windowState('translator').minimized")
        self.wait(page, "document.querySelector('[data-task=\"translator\"].is-focus-parked')")
        self.assertFalse(page.evaluate("() => document.documentElement.dataset.moduleFocus === '1'"))
        self.assertFalse(page.evaluate("() => document.getElementById('main-content').inert"))
        page.wait_for_timeout(400)
        self.shot(page, "focus_settings_parked")

        page.keyboard.press("Escape")
        self.wait(page, "!MemuModules.windowState('translator').minimized")
        self.wait(page, "MemuModules.windowState('translator').state === 'expanded'")
        self.wait(page, "document.documentElement.dataset.moduleFocus === '1'")
        page.wait_for_timeout(400)
        self.shot(page, "focus_returned")

    def test_shortcut_inside_module_opens_search(self):
        page = self.page(lang="en", theme="dark")
        self._full_module(page)

        page.evaluate("() => MemuModuleFocus.replayKey('translator', {key: 'k', code: 'KeyK', ctrlKey: true})")
        self.wait(page, "window.MemuSearch && MemuSearch.isOpen()")
        self.wait(page, "MemuModules.windowState('translator').minimized")
        page.wait_for_timeout(350)
        self.shot(page, "focus_search_parked")
        page.keyboard.press("Escape")
        self.wait(page, "!MemuSearch.isOpen()")
        self.wait(page, "!MemuModules.windowState('translator').minimized && MemuModules.windowState('translator').state === 'expanded'")

    def test_click_on_sidebar_parks_without_auto_return(self):
        page = self.page()
        self._full_module(page)

        box = page.evaluate("() => { const l = document.querySelector('#group-chats .side-label'); l.scrollIntoView({block: 'center'}); const r = l.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }")
        page.mouse.click(box[0], box[1])
        self.wait(page, "MemuModules.windowState('translator').minimized")
        page.wait_for_timeout(1200)
        self.assertTrue(page.evaluate("() => MemuModules.windowState('translator').minimized"))
        page.click('[data-task="translator"]')
        self.wait(page, "MemuModules.windowState('translator').state === 'expanded' && !MemuModules.windowState('translator').minimized")

    def test_system_layers_stay_above(self):
        page = self.page()
        self._full_module(page)
        page.evaluate("() => toast('Проверка поверх модуля', 'info')")
        page.wait_for_timeout(300)
        z = page.evaluate("""() => {
          const zi = (sel) => { const n = document.querySelector(sel); return n ? Number(getComputedStyle(n).zIndex) || 0 : null; };
          return { layer: zi('#module-layer'), toasts: zi('.toast-container') };
        }""")
        self.assertGreater(z["toasts"], z["layer"])
        self.assertFalse(page.evaluate("() => MemuModules.windowState('translator').minimized"), "a toast does not park the module")
        self.shot(page, "focus_toast")

    def test_module_app_appbar_preview(self):
        for module_id, theme, lang in (("music", "dark", "ru"), ("translator", "light", "en"), ("characters", "dark", "ru")):
            page = self.page(f"/?module_app={module_id}", size=(1180, 700), lang=lang, theme=theme, until="load")
            self.wait(page, f"document.querySelector('.module-window[data-module=\"{module_id}\"]')")
            page.evaluate("() => MemuModuleAppbar.preview()")
            self.wait(page, "document.documentElement.dataset.mab === '1'")
            page.wait_for_timeout(1500)
            info = page.evaluate("""() => {
              const bar = document.querySelector('.module-window-bar').getBoundingClientRect();
              const wc = document.querySelector('.mab-wc').getBoundingClientRect();
              return { style: document.documentElement.dataset.mabStyle, barTop: bar.top, barH: bar.height,
                       wcRight: Math.round(wc.right), width: innerWidth };
            }""")
            self.assertEqual(info["barTop"], 0)
            self.assertEqual(info["wcRight"], info["width"], "window buttons flush with the right edge")
            self.assertEqual(info["style"], "transparent" if module_id == "music" else "auto")
            self.shot(page, f"appbar_{module_id}_{lang}")

            page.mouse.move(420, 14)
            page.mouse.down(); page.mouse.up()
            page.evaluate("() => document.querySelector('.mab-maximize').click()")
            page.wait_for_timeout(250)
            calls = page.evaluate("() => window.__mabCalls.map((c) => c.join(':'))")
            self.assertIn("appbar_drag:caption", calls)
            self.assertIn("appbar_command:toggle", calls)
            self.assertTrue(page.evaluate("() => document.querySelector('.mab-maximize').classList.contains('is-maximized')"))

            sheet = page.evaluate("() => document.querySelector('.module-window').dataset.full || ''")
            page.mouse.dblclick(420, 14)
            page.wait_for_timeout(250)
            calls = page.evaluate("() => window.__mabCalls.map((c) => c.join(':'))")
            self.assertEqual(calls.count("appbar_command:toggle"), 2)
            self.assertEqual(page.evaluate("() => document.querySelector('.module-window').dataset.full || ''"), sheet)
            if lang == "en":
                self.assertEqual(page.evaluate("() => document.querySelector('.mab-close').title"), "Close")

if __name__ == "__main__":
    unittest.main()
