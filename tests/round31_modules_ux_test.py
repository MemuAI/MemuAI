# -*- coding: utf-8 -*-
"""Round 31 (modules-ux): closing and minimizing a module is obvious.

Static part (no browser):
- the bridge forwards unhandled Ctrl/Alt shortcuts from the module page (``memu-module-key`` / ``combo``) and
  exposes ``Memu.unsaved(flag, text)`` (a free action);
- the hotkey registry has ``mod_close`` = Ctrl+W (rebindable) and the docs page lists it;
- one-time hints are stored server-side (``modules/ui.json``) through ``/api/pg/modules/ui-hints``;
- the module close/minimize buttons use their own glyphs and the CSS gives them the red close pill.

Browser part (real playground + headless Chromium, the custom title bar mocked like
tests/modules_window_states_browser_test.py): for every built-in module at 1366×768, 1920×1080 and 800×600,
floating and expanded, the module's own «minimize» and «close» buttons are visible, inside the window, not covered
and not overlapping the MemuAI window buttons; their tooltips name the shortcut. Then the behaviour: minimize →
module bar pill with its own ×, the pill restores it; Esc on a window minimizes it, Esc on an expanded sheet brings
the window back; Ctrl+W closes (from MemuAI and from inside the module page); a module that reported unsaved work
asks before closing (Cancel / Minimize / Close); the first-time hint fits the screen and Esc hides it; the MemuAI
window × says it closes MemuAI while a module is open.

Skipped without Playwright/Chromium (MEMU_TEST_CHROMIUM may point at a binary). Run with an isolated
MEMUAI_DATA_DIR. Screenshots of the states: MEMU_R31_SHOTS=/dir python -m pytest tests/round31_modules_ux_test.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "pgstatic"

MODULES = ("translator", "studio", "design", "benchmarks", "catalog", "api-models", "characters")
SIZES = {"1366": (1366, 768), "1920": (1920, 1080), "narrow": (800, 600)}
SHOTS = os.environ.get("MEMU_R31_SHOTS", "")


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


class StaticContractTest(unittest.TestCase):
    def test_bridge_forwards_shortcuts_and_has_unsaved(self):
        from app import modules_host
        bridge = modules_host.BRIDGE_JS
        self.assertIn('key: "combo"', bridge)
        self.assertIn("unsaved: function (flag, text)", bridge)
        self.assertIn('call("unsaved"', bridge)
        self.assertIn("unsaved", modules_host.FREE_ACTIONS)
        # The bridge still reports an unhandled Escape the old way (Round 21 contract).
        self.assertIn('key: "Escape"', bridge)

    def test_host_handles_combo_unsaved_and_request_close(self):
        js = (STATIC / "modules.js").read_text(encoding="utf-8")
        self.assertIn('data.key === "combo"', js)
        self.assertIn('case "unsaved":', js)
        for name in ("function requestClose", "function closeReason", "function escapeWindow", "function windowHotkey",
                     "function showCoach", "function syncAppCloseHint", "data-task-close", "/api/pg/modules/ui-hints"):
            self.assertIn(name, js)
        # Every close the person asks for goes through requestClose (asks when work would be lost).
        self.assertIn('p.close.addEventListener("click", () => requestClose(current()))', js)
        self.assertIn('requestClose(headWin)', js)
        self.assertNotIn('closeButton.onclick = () => closeAll()', js)
        # «свернуть» on the Esc chip meant «restore the size» — it now says «окном».
        self.assertNotIn('t("свернуть", "restore")', js)

    def test_css_close_pill_and_narrow_head(self):
        css = (STATIC / "modules.css").read_text(encoding="utf-8")
        self.assertIn(".module-ctl-close:hover", css)
        self.assertIn(".module-task-x", css)
        self.assertIn(".module-coach", css)
        self.assertIn("@container (max-width: 480px)", css)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_hotkey_registry_has_mod_close(self):
        script = ("const H = require(process.argv[1]); const r = H.entry('mod_close');"
                  "process.stdout.write(JSON.stringify({r, c: H.conflicts({})}));")
        out = subprocess.run(["node", "-e", script, str(STATIC / "hotkeys.js")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["r"]["keys"], ["Ctrl+W"])
        self.assertTrue(data["r"]["custom"])
        self.assertNotRegex(data["r"]["en"], "[А-Яа-яЁё]")
        for lang in ("ru", "en"):
            page = (ROOT / "app" / "docs" / lang / "shortcuts.md").read_text(encoding="utf-8")
            self.assertIn("Ctrl+W", page)

    def test_ui_hints_storage(self):
        from app import modules_host
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(modules_host.load_ui_hints(root), {"seen": []})
            self.assertEqual(modules_host.mark_ui_hint(root, "window"), {"seen": ["window"]})
            self.assertEqual(modules_host.mark_ui_hint(root, "minimize"), {"seen": ["window", "minimize"]})
            self.assertEqual(modules_host.mark_ui_hint(root, "window", seen=False), {"seen": ["minimize"]})
            with self.assertRaises(ValueError):
                modules_host.mark_ui_hint(root, "../x")
            # state.json writes never touch the hints file.
            modules_host.remember_window(root, "translator", {"width": 700, "height": 500})
            self.assertEqual(modules_host.load_ui_hints(root), {"seen": ["minimize"]})


MOCK = """() => {
  window.__state = { ok: true, supported: true, mode: "custom", installed: true, pending: false,
    maximized: false, fullscreen: false, active: true, os: "win11", doubleClickMs: 500, forced: false };
  const rec = () => () => Promise.resolve(true);
  window.__cmds = [];
  window.pywebview = { api: { titlebar_state: () => Promise.resolve(Object.assign({}, window.__state)),
    titlebar_set_mode: () => Promise.resolve(Object.assign({}, window.__state)),
    titlebar_drag: rec(), titlebar_command: (k) => { window.__cmds.push(k); return Promise.resolve(true); },
    titlebar_system_menu: rec() } };
  window.dispatchEvent(new Event("pywebviewready"));
}"""

# The module's own minimize/close: visible, inside the viewport, on top, clear of the MemuAI window buttons.
CONTROLS = r"""(id) => {
  const out = [];
  const win = document.querySelector(`.module-window[data-module="${id}"]`);
  if (!win || win.hidden) return ["window is not shown"];
  const head = document.getElementById("module-headbar");
  const inHead = win.dataset.headbar === "1" && head && !head.hidden;
  const root = inHead ? head : win.querySelector(".module-window-bar");
  const pick = (sel) => root && root.querySelector(sel);
  const min = inHead ? pick('[data-mhb="min"]') : pick('[data-win="min"]');
  const close = inHead ? pick('[data-mhb="close"]') : pick('[data-win="close"]');
  const wc = document.getElementById("memu-wincontrols");
  const wcr = wc && wc.getClientRects().length ? wc.getBoundingClientRect() : null;
  const W = window.innerWidth, H = window.innerHeight;
  for (const [name, el] of [["minimize", min], ["close", close]]) {
    if (!el) { out.push(`${name}: no button`); continue; }
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20) { out.push(`${name}: too small ${r.width}x${r.height}`); continue; }
    if (r.left < 0 || r.top < 0 || r.right > W || r.bottom > H) out.push(`${name}: outside the window ${JSON.stringify([r.left, r.top, r.right, r.bottom])}`);
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(hit === el || el.contains(hit))) out.push(`${name}: covered by ${hit ? (hit.id || hit.className || hit.tagName) : "nothing"}`);
    if (wcr && Math.min(r.right, wcr.right) - Math.max(r.left, wcr.left) > 0 && Math.min(r.bottom, wcr.bottom) - Math.max(r.top, wcr.top) > 0)
      out.push(`${name}: overlaps the MemuAI window buttons`);
    if (!el.title || !el.getAttribute("aria-label")) out.push(`${name}: no tooltip`);
  }
  if (close && !/Ctrl\+W/.test(close.title)) out.push(`close: tooltip without Ctrl+W (${close && close.title})`);
  if (min && !/(панел|bar)/i.test(min.title)) out.push(`minimize: tooltip does not say where it goes (${min && min.title})`);
  return out;
}"""


def _wait(target, expression: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            if target.evaluate(f"() => !!({expression})"):
                return
        except Exception:
            pass
        if time.time() > deadline:
            raise AssertionError(f"timed out waiting for: {expression}")
        time.sleep(0.05)


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class ModuleCloseMinimizeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import modules_host, pgdata
        from app.playground_api import create_app
        cls.root = modules_host.default_root()
        cls.root.mkdir(parents=True, exist_ok=True)
        for module_id in MODULES:
            try:
                modules_host.configure_module(cls.root, module_id, enabled=True)
            except Exception:
                pass
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 60
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.1)
        if not cls.server.started:  # pragma: no cover
            raise unittest.SkipTest("playground server did not start")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    # ------------------------------------------------------------------ helpers
    def page(self, size=(1366, 768), theme="light", lang="ru"):
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": lang, "theme": theme})
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors: list = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle")
        _wait(page, "window.MemuModules && document.body.classList.contains('boot-ready')", 30)
        page.wait_for_timeout(350)
        page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
        page.evaluate(MOCK)
        _wait(page, "document.documentElement.dataset.titlebar === 'custom'", 5)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def open(self, page, module_id):
        page.evaluate("(id) => { MemuModules.closeAll(); return MemuModules.refresh().then(() => MemuModules.open(id)); }", module_id)
        _wait(page, f"(() => {{ const w = document.querySelector(\".module-window[data-module='{module_id}']\"); return w && !w.hidden; }})()")
        deadline = time.time() + 15
        frame = None
        while time.time() < deadline:
            handle = page.query_selector(f".module-window[data-module='{module_id}'] iframe")
            frame = handle.content_frame() if handle else None
            if frame and "chrome=host" in frame.url:
                try:
                    if frame.evaluate("() => document.readyState === 'complete' && !!window.Memu"):
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        page.wait_for_timeout(700)
        return frame

    def expand(self, page, module_id, on):
        page.evaluate("([id, on]) => { const w = document.querySelector(`.module-window[data-module='${id}']`);"
                      " const full = w && w.dataset.full === '1'; if (on !== full) MemuModules.snap(id, on ? 'max' : 'restore'); }",
                      [module_id, on])
        page.wait_for_timeout(500)

    def state(self, page, module_id):
        return page.evaluate("(id) => MemuModules.windowState(id)", module_id)

    def shot(self, page, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))

    # ---------------------------------------------------------- visibility walk
    def walk(self, size_name):
        problems = []
        for theme, lang in (("light", "ru"), ("dark", "en")):
            page = self.page(SIZES[size_name], theme, lang)
            for module_id in MODULES:
                self.open(page, module_id)
                for expanded in (False, True):
                    self.expand(page, module_id, expanded)
                    label = f"{size_name}/{theme}/{module_id}/{'expanded' if expanded else 'window'}"
                    problems += [f"{label}: {p}" for p in page.evaluate(CONTROLS, module_id)]
                    self.shot(page, label.replace("/", "_"))
                self.expand(page, module_id, False)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_controls_visible_1366(self):
        self.walk("1366")

    def test_controls_visible_1920(self):
        self.walk("1920")

    def test_controls_visible_narrow(self):
        self.walk("narrow")

    # --------------------------------------------------------------- behaviour
    def test_minimize_bar_restore_and_close(self):
        page = self.page()
        self.open(page, "translator")
        self.expand(page, "translator", False)
        page.click(".module-window[data-module='translator'] [data-win='min']")
        page.wait_for_timeout(200)
        self.assertTrue(self.state(page, "translator")["minimized"])
        pill = ".module-taskbar [data-task='translator']"
        _wait(page, f"document.querySelector(\"{pill}\") && !document.getElementById('module-taskbar').hidden")
        # Its own × and a hint that it is minimized; no «close all» for a single module.
        self.assertTrue(page.is_visible(".module-taskbar [data-task-close='translator']"))
        self.assertTrue(page.is_visible(".module-taskbar .module-task-badge"))
        self.assertFalse(page.query_selector(".module-taskbar [data-taskbar-close]"))
        self.shot(page, "behaviour-minimized")
        page.click(pill)
        page.wait_for_timeout(200)
        self.assertFalse(self.state(page, "translator")["minimized"])
        # Minimize again and close straight from the bar.
        page.evaluate("() => MemuModules.minimize('translator')")
        page.wait_for_timeout(150)
        page.click(".module-taskbar [data-task-close='translator']")
        _wait(page, "!MemuModules.openIds.includes('translator')")
        self.assertTrue(page.evaluate("() => document.getElementById('module-taskbar').hidden"))

    def test_two_modules_bar_has_close_all(self):
        page = self.page()
        self.open(page, "translator")
        page.evaluate("() => MemuModules.open('catalog')")
        _wait(page, "MemuModules.openIds.length === 2")
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("() => document.querySelectorAll('.module-taskbar [data-task-close]').length"), 2)
        page.click(".module-taskbar [data-taskbar-close]")
        _wait(page, "MemuModules.openIds.length === 0")

    def test_headbar_minimize_and_close(self):
        page = self.page()
        self.open(page, "translator")
        self.expand(page, "translator", True)
        self.assertTrue(page.is_visible("#module-headbar [data-mhb='min']"))
        self.assertTrue(page.is_visible("#module-headbar [data-mhb='close']"))
        # The text label «Закрыть» is shown in a wide head.
        self.assertTrue(page.is_visible("#module-headbar .mhb-close-text"))
        self.assertIn("окном", page.inner_text("#module-headbar [data-mhb='esc']"))
        page.click("#module-headbar [data-mhb='min']")
        page.wait_for_timeout(200)
        self.assertTrue(self.state(page, "translator")["minimized"])
        self.assertTrue(page.evaluate("() => document.getElementById('module-headbar').hidden"))
        page.click(".module-taskbar [data-task='translator']")
        page.wait_for_timeout(300)
        self.assertEqual(self.state(page, "translator")["state"], "expanded")
        page.click("#module-headbar [data-mhb='close']")
        _wait(page, "!MemuModules.openIds.includes('translator')")

    def test_keyboard_esc_and_ctrl_w(self):
        page = self.page()
        frame = self.open(page, "translator")
        self.expand(page, "translator", True)
        # Esc on the expanded sheet: back to a window (Round 21), not minimized.
        page.focus("body")
        page.keyboard.press("Escape")
        page.wait_for_timeout(450)
        st = self.state(page, "translator")
        self.assertEqual((st["state"], st["minimized"]), ("floating", False))
        # Esc inside the module window (unhandled by the module) minimizes it: «back to the chat».
        # (A click puts the keyboard focus into the sandboxed module page, as a real click does.)
        frame.click("body", position={"x": 4, "y": 4})
        self.assertTrue(frame.evaluate("() => document.hasFocus()"))
        page.keyboard.press("Escape")
        _wait(page, "MemuModules.windowState('translator').minimized")
        self.assertTrue(page.evaluate("() => !document.getElementById('module-taskbar').hidden"))
        # Esc in the chat does not touch a minimized module.
        page.click(".module-taskbar [data-task='translator']")
        page.wait_for_timeout(200)
        self.assertFalse(self.state(page, "translator")["minimized"])
        # Ctrl+W pressed inside the module page closes it (forwarded by the bridge).
        frame.click("body", position={"x": 4, "y": 4})
        self.assertTrue(frame.evaluate("() => document.hasFocus()"))
        page.keyboard.press("Control+w")
        _wait(page, "!MemuModules.openIds.includes('translator')")
        # Ctrl+W with the focus in MemuAI closes the active module too.
        self.open(page, "catalog")
        page.focus("body")
        page.keyboard.press("Control+w")
        _wait(page, "!MemuModules.openIds.includes('catalog')")

    def test_unsaved_work_asks_before_closing(self):
        page = self.page()
        frame = self.open(page, "translator")
        frame.evaluate("() => Memu.unsaved(true, 'Черновик не сохранён.')")
        _wait(page, "MemuModules.windowState('translator').unsaved")
        page.click(".module-window[data-module='translator'] [data-win='close']")
        _wait(page, "document.getElementById('module-close-yes')")
        self.assertIn("Черновик не сохранён.", page.inner_text(".module-close-ask"))
        # The question is drawn above the module window.
        self.assertTrue(page.evaluate("() => document.getElementById('panel-back').classList.contains('over-modules')"))
        self.shot(page, "behaviour-unsaved-ask")
        page.click("#module-close-cancel")
        page.wait_for_timeout(250)
        self.assertIn("translator", page.evaluate("() => MemuModules.openIds"))
        page.click(".module-window[data-module='translator'] [data-win='close']")
        _wait(page, "document.getElementById('module-close-min')")
        page.click("#module-close-min")
        _wait(page, "MemuModules.windowState('translator').minimized")
        page.evaluate("() => MemuModules.requestClose('translator')")
        _wait(page, "document.getElementById('module-close-yes')")
        page.click("#module-close-yes")
        _wait(page, "!MemuModules.openIds.includes('translator')")
        # Without unsaved work the window closes at once; Memu.unsaved(false) clears the flag.
        frame = self.open(page, "translator")
        frame.evaluate("() => Memu.unsaved(true).then(() => Memu.unsaved(false))")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => MemuModules.closeReason('translator')"), "")
        page.click(".module-window[data-module='translator'] [data-win='close']")
        _wait(page, "!MemuModules.openIds.includes('translator')")

    def test_first_time_hint_and_app_close_tooltip(self):
        page = self.page(size=(800, 600))
        self.open(page, "translator")
        title = page.evaluate("() => document.querySelector('#memu-wincontrols .memu-wc-close').title")
        self.assertIn("MemuAI", title)
        page.evaluate("() => MemuModules.showHint('translator')")
        _wait(page, "document.getElementById('module-coach')")
        box = page.evaluate("() => { const r = document.getElementById('module-coach').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
        self.assertGreaterEqual(box[0], 0)
        self.assertLessEqual(box[2], 800)
        self.assertLessEqual(box[3], 600)
        text = page.inner_text("#module-coach")
        self.assertIn("Свернуть", text)
        self.assertIn("Ctrl+W", text)
        self.assertIn("MemuAI", text)  # the corner × closes MemuAI itself
        self.shot(page, "behaviour-hint")
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        self.assertFalse(page.query_selector("#module-coach"))
        # Esc only hid the hint: the window is still there.
        self.assertFalse(self.state(page, "translator")["minimized"])
        page.evaluate("() => MemuModules.closeAll()")
        page.wait_for_timeout(200)
        self.assertNotIn("MemuAI", page.evaluate("() => document.querySelector('#memu-wincontrols .memu-wc-close').title"))

    def test_ui_hints_route(self):
        import urllib.request
        base = f"http://127.0.0.1:{self.port}/api/pg/modules/ui-hints"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(base, data=json.dumps({"name": "window"}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        with opener.open(request, timeout=10) as response:
            self.assertIn("window", json.loads(response.read())["seen"])
        with opener.open(base, timeout=10) as response:
            self.assertIn("window", json.loads(response.read())["seen"])
        bad = urllib.request.Request(base, data=json.dumps({"name": "nope"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(bad, timeout=10)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
