"""Round 28: the screen layer — storage migration, routes and the native wiring (mocked Win32).

* migration of a Round 27 ``quick_canvas.json`` (the in-window grid): nothing is lost, the grid becomes screen
  positions, «as last time» keeps its meaning, the file version goes to 2;
* ``/api/quick/layer/resolve|move|region|relay`` and ``/api/quick/layer`` with a fake Quick Ask manager and layer;
  the relay whitelist and the 300 ms guard; delete → undo keeps the screen spot;
* ``QuickAskManager``: the window opens on the monitor the user is on (cursor / active window / primary; a pinned
  window stays), the remembered spot per monitor, following the user to another monitor, the layer hides with the
  window, a click on the layer is not «going to another program», Escape knows the layer window, screenshots exclude it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r28layer-test-")

from app import quick_actions as qa  # noqa: E402
from app import quick_ask  # noqa: E402
from app import quick_ask_core as core  # noqa: E402
from app import quick_canvas as qc  # noqa: E402
from app import quick_layer_core as lc  # noqa: E402

MONITORS = [
    {"rect": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1040), "dpi": 96, "primary": True},
    {"rect": (-2560, -200, 0, 1240), "work": (-2560, -200, 0, 1192), "dpi": 144, "primary": False},
]

R27_FILE = {
    "version": 1,
    "settings": {"enabled": True, "panels": True, "actions": True, "open_mode": "last", "strip": True,
                 "collapse_on_answer": True, "density": "normal", "labels": True},
    "ui": {"open": True},
    "layout": {"cols": 8, "items": [
        {"id": "i_r27panel1", "type": "panel", "ref": "p_weather", "x": 0, "y": 0, "w": 4, "h": 2},
        {"id": "i_r27btn001", "type": "action", "ref": "a_r27btn01", "x": 4, "y": 0, "w": 1, "h": 1, "pinned": True},
        {"id": "i_r27btn002", "type": "action", "ref": "a_r27btn02", "x": 5, "y": 0, "w": 2, "h": 2},
    ]},
    "actions": [
        {"id": "a_r27btn01", "label": "Калькулятор", "icon": {"kind": "builtin", "value": "bolt"}, "steps": [{"type": "open", "target": "app", "value": "calc"}]},
        {"id": "a_r27btn02", "label": "Громкость", "icon": {"kind": "emoji", "value": "🔊"}, "steps": [{"type": "command", "action": "volume.set", "params": {"level": 30}}]},
    ],
}

class FakeLayer:
    def __init__(self):
        self.visible = True
        self.edit = False
        self.hwnd = 4242
        self.regions = []
        self.relayed = []
        self.click_ok = True
        self.hidden = 0
        self.shown = []
        self.keepouts = []

    def available(self):
        return True

    def public(self):
        return {"available": True, "visible": self.visible, "edit": self.edit, "glass": True, "monitor": ""}

    def set_region(self, rects, full=False):
        self.regions.append((rects, full))
        return {"ok": True, "rects": len(rects), "full": full}

    def relay(self, message):
        self.relayed.append(message)
        return True

    def accept_click(self):
        return self.click_ok

    def hide(self):
        self.hidden += 1
        self.visible = False
        return True

    def show(self, geometry, monitors, reason="open"):
        self.shown.append((geometry, reason))
        self.visible = True
        return True

    def update_keepout(self, geometry):
        self.keepouts.append(geometry)

    def on_focus(self):
        pass

    def set_edit(self, on):
        self.edit = on
        return on

    def destroy(self):
        pass

class FakeManager:
    native_ok = True

    def __init__(self, layer):
        self.layer = layer
        self.visible = True
        self.pushed = []
        self.hwnd = 77
        self.api = None
        self.commands = []

    def ensure_layer(self):
        return self.layer

    def _push(self, script):
        self.pushed.append(script)

    def raise_to_top(self):
        return False

    def _on_ui(self, fn):
        return None

    def _focus_browser(self):
        pass

    def layer_command(self, action, on=None):
        self.commands.append((action, on))
        return {"ok": True, "action": action, **self.layer.public()}

class Base(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp(prefix="r28api-")
        self.old_path = qc._path
        qc._path = lambda: Path(self.tmp) / "quick_canvas.json"
        self.old_manager = qa.Runner._manager
        self.layer = FakeLayer()
        self.manager = FakeManager(self.layer)
        qa.Runner._manager = lambda _self: self.manager
        app = FastAPI()
        app.include_router(qc.router)
        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        qc._path = self.old_path
        qa.Runner._manager = self.old_manager
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_r27(self):
        (Path(self.tmp) / "quick_canvas.json").write_text(json.dumps(R27_FILE, ensure_ascii=False), encoding="utf-8")

class MigrationAndRoutes(Base):
    def test_round27_file_is_migrated_without_losing_anything(self):
        self.write_r27()
        state = self.client.get("/api/quick/canvas?lang=ru").json()
        self.assertEqual({a["id"] for a in state["actions"]}, {"a_r27btn01", "a_r27btn02"})
        self.assertEqual([i["id"] for i in state["layout"]["items"]], ["i_r27panel1", "i_r27btn001", "i_r27btn002"])
        self.assertTrue(next(i for i in state["layout"]["items"] if i["id"] == "i_r27btn001")["pinned"])
        self.assertEqual(state["settings"]["screen"], "last", "«as last time» keeps its meaning")
        self.assertEqual(state["layer"]["migrated"], "r27-grid")
        self.assertTrue(state["layer"]["available"])
        doc = qc.load()
        self.assertEqual(doc["version"], 2)
        self.assertEqual(set(doc["layer"]["default"]["items"]), {"i_r27panel1", "i_r27btn001", "i_r27btn002"})

        self.client.post("/api/quick/canvas/settings", json={"settings": {"labels": False}})
        raw = json.loads((Path(self.tmp) / "quick_canvas.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)
        self.assertEqual(raw["layer"]["migrated"], "r27-grid")
        self.assertEqual(raw["settings"]["screen"], "last")

        self.assertEqual(qc.normalize_settings({"open_mode": "input"})["screen"], "show")
        self.assertEqual(qc.normalize_settings({"open_mode": "canvas"})["screen"], "show")

    def test_resolve_and_move(self):
        self.write_r27()
        monitor = {"key": "1920x1080@96", "w": 1920, "h": 1040}
        keepout = {"hard": [600, 228, 720, 136], "soft": [600, 228, 720, 760]}
        out = self.client.post("/api/quick/layer/resolve", json={"monitor": monitor, "keepout": keepout,
                                                                  "panels": ["p_weather"]}).json()
        self.assertEqual(len(out["items"]), 3)
        self.assertEqual(out["hidden"], 0)

        gone = self.client.post("/api/quick/layer/resolve", json={"monitor": monitor, "keepout": keepout, "panels": []}).json()
        self.assertEqual({i["type"] for i in gone["items"]}, {"action"})

        rows = [{"id": i["id"], "x": i["x"], "y": i["y"], "units": i["units"]} for i in out["items"]]
        for r in rows:
            if r["id"] == "i_r27panel1":
                r.update({"x": 20, "y": 1040 - 20 - lc.unit_px(4), "units": [4, 4]})
        moved = self.client.post("/api/quick/layer/move", json={"monitor": monitor, "keepout": keepout, "items": rows, "panels": ["p_weather"]}).json()
        self.assertTrue(moved["saved"])
        panel = next(i for i in moved["items"] if i["id"] == "i_r27panel1")
        self.assertEqual((panel["x"], panel["y"], panel["units"]), (20, 1040 - 20 - lc.unit_px(4), [4, 4]))
        self.assertEqual(panel["source"], "exact")
        doc = qc.load()
        self.assertIn("1920x1080@96", doc["layer"]["monitors"])
        self.assertNotIn("migrated", doc["layer"])
        self.assertEqual(next(i for i in doc["layout"]["items"] if i["id"] == "i_r27panel1")["h"], 4)

        small = self.client.post("/api/quick/layer/resolve", json={"monitor": {"key": "1366x768@120", "w": 1092, "h": 582}}).json()
        for i in small["items"]:
            self.assertTrue(0 <= i["x"] and i["x"] + i["w"] <= 1092 and 0 <= i["y"] and i["y"] + i["h"] <= 582, i)
            self.assertEqual(i["source"], "adapted")

        bad = self.client.post("/api/quick/layer/resolve", json={"monitor": {"key": "../../etc", "w": "x"}}).json()
        self.assertEqual(bad["monitor"]["key"], "default")
        self.assertFalse(self.client.post("/api/quick/layer/move", json={"monitor": monitor, "items": [{"id": "i_nope", "x": 1, "y": 1}]}).json()["saved"])

    def test_delete_and_undo_keep_the_screen_spot(self):
        self.write_r27()
        monitor = {"key": "1920x1080@96", "w": 1920, "h": 1040}
        out = self.client.post("/api/quick/layer/resolve", json={"monitor": monitor}).json()
        rows = [{"id": i["id"], "x": i["x"], "y": i["y"], "units": i["units"]} for i in out["items"]]
        for r in rows:
            if r["id"] == "i_r27btn001":
                r.update({"x": 40, "y": 500})
        self.client.post("/api/quick/layer/move", json={"monitor": monitor, "items": rows})
        removed = self.client.delete("/api/quick/canvas/actions/a_r27btn01?lang=ru").json()["removed"]
        self.assertIn("i_r27btn001", removed["screen"])
        self.client.post("/api/quick/canvas/actions/restore", json={"removed": removed, "lang": "ru"})
        again = self.client.post("/api/quick/layer/resolve", json={"monitor": {"key": "1600x900@96", "w": 1600, "h": 860}}).json()
        btn = next(i for i in again["items"] if i["ref"] == "a_r27btn01")
        self.assertLess(btn["x"], 200, "the restored button comes back near where it was (left side)")

    def test_region_command_and_relay(self):
        r = self.client.post("/api/quick/layer/region", json={"rects": [[1, 2, 3, 4, 5]], "full": False}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.layer.regions[-1], ([[1, 2, 3, 4, 5]], False))
        self.client.post("/api/quick/layer", json={"action": "toggle"})
        self.client.post("/api/quick/layer", json={"action": "edit", "on": True})
        self.assertEqual(self.manager.commands, [("toggle", False), ("edit", True)])

        self.assertEqual(self.client.post("/api/quick/layer/relay", json={"to": "quick", "event": "eval", "data": {}}).status_code, 400)
        self.assertEqual(self.client.post("/api/quick/layer/relay", json={"to": "main", "event": "run", "data": {}}).status_code, 400)
        out = self.client.post("/api/quick/layer/relay", json={"to": "quick", "event": "run", "id": "m1", "data": {"id": "a_x</script>"}}).json()
        self.assertTrue(out["relayed"])
        self.assertIn("MemuQuickBus.receive(", self.manager.pushed[-1])
        self.assertIn('"a_x</script>"', self.manager.pushed[-1])
        out = self.client.post("/api/quick/layer/relay", json={"to": "layer", "event": "reload", "id": "m2"}).json()
        self.assertTrue(out["relayed"])
        self.assertEqual(self.layer.relayed[-1]["event"], "reload")

        self.layer.click_ok = False
        pushed = len(self.manager.pushed)
        out = self.client.post("/api/quick/layer/relay", json={"to": "quick", "event": "run", "id": "m3", "data": {"id": "a_1"}}).json()
        self.assertTrue(out.get("guarded"))
        self.assertEqual(len(self.manager.pushed), pushed)
        big = self.client.post("/api/quick/layer/relay", json={"to": "quick", "event": "toast", "data": {"text": "x" * 30000}})
        self.assertEqual(big.status_code, 413)

    def test_without_the_windows_app_everything_falls_back(self):
        self.manager.native_ok = False
        self.write_r27()
        state = self.client.get("/api/quick/canvas?lang=en").json()
        self.assertFalse(state["layer"]["available"], "no layer → the window keeps the Round 27 grid")
        self.assertFalse(self.client.post("/api/quick/layer/relay", json={"to": "quick", "event": "run", "data": {}}).json()["relayed"])
        self.assertFalse(self.client.post("/api/quick/layer/region", json={"rects": []}).json()["ok"])
        self.assertFalse(self.client.post("/api/quick/layer", json={"action": "toggle"}).json()["ok"])

class FakeWin32:
    def __init__(self, cursor=(100, 100)):
        self._cursor = cursor
        self.calls = []
        self.foreground = 0
        self.rects = {}

    def cursor(self):
        return self._cursor

    def monitors(self):
        return [dict(m) for m in MONITORS]

    def window_rect(self, hwnd):
        return self.rects.get(hwnd)

    def SetWindowPos(self, *args):
        self.calls.append(("pos",) + args)
        return True

    def GetForegroundWindow(self):
        return self.foreground

    def GetWindowLong(self, *_):
        return 0

    def IsWindow(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False

    def take_foreground(self, hwnd):
        self.foreground = hwnd
        return True

    CreateRoundRectRgn = None
    SetWindowRgn = None

class Manager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r28mgr-")
        self.old_state = quick_ask._state_file
        quick_ask._state_file = lambda: Path(self.tmp) / "quick_state.json"
        self.prefs = {"quick_ask_position": "last", "quick_ask_monitor": "cursor"}
        m = quick_ask.QuickAskManager("http://127.0.0.1:1", load_prefs=lambda: dict(self.prefs))
        m.native_ok = True
        m.api = FakeWin32()
        m.hwnd = 77
        m.pinned = False
        self.m = m

    def tearDown(self):
        quick_ask._state_file = self.old_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drag_to_top_right_of(self, index):
        monitors = self.m._monitors()
        g = core.place(monitors, (0, 0), mode="top-right", target=index)
        self.m.geometry = g
        self.m.css_height = core.COLLAPSED_HEIGHT
        self.m._settle({"record": core.snap(monitors, (g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]))["record"]}, monitors)

    def test_opens_on_the_cursor_monitor_even_after_a_drag(self):
        self.drag_to_top_right_of(0)
        self.m.api._cursor = (-1000, 300)
        g = self.m._initial_geometry(self.prefs)
        self.assertEqual(g["monitor"], 1, "Round 27 reopened on the monitor of the drag forever")
        self.assertEqual(g["scale"], 1.5)
        self.assertEqual(g["x"] + g["w"], 0 - round(core.SCREEN_MARGIN * 1.5))
        state = quick_ask.load_state()
        self.assertIn("1920x1080@96", state["monitor_spots"])

    def test_each_monitor_remembers_its_own_spot(self):
        self.drag_to_top_right_of(0)
        monitors = self.m._monitors()
        g = core.place(monitors, (0, 0), mode="bottom-left", target=1)
        self.m.geometry = g
        self.m._settle({"record": core.snap(monitors, (g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]))["record"]}, monitors)
        self.m.api._cursor = (500, 500)
        g0 = self.m._initial_geometry(self.prefs)
        self.assertEqual((g0["monitor"], g0["dock"]), (0, "top-right"))
        self.m.api._cursor = (-500, 500)
        g1 = self.m._initial_geometry(self.prefs)
        self.assertEqual((g1["monitor"], g1["dock"]), (1, "bottom-left"))

    def test_primary_and_window_choices_and_pinned(self):
        self.m.api._cursor = (-1000, 300)
        self.prefs["quick_ask_monitor"] = "primary"
        self.assertEqual(self.m._initial_geometry(self.prefs)["monitor"], 0)
        self.prefs["quick_ask_monitor"] = "window"
        self.m.api.rects[555] = (100, 100, 900, 700)
        self.assertEqual(self.m._initial_geometry(self.prefs, 555)["monitor"], 0)
        self.m.api.rects[556] = (-2000, 0, -100, 900)
        self.m.api._cursor = (300, 300)
        self.assertEqual(self.m._initial_geometry(self.prefs, 556)["monitor"], 1)

        self.prefs["quick_ask_monitor"] = "cursor"
        self.drag_to_top_right_of(0)
        self.m.pinned = True
        self.m.api._cursor = (-1000, 300)
        self.assertEqual(self.m._initial_geometry(self.prefs)["monitor"], 0)

    def test_follows_the_user_to_another_monitor(self):
        self.m.geometry = core.place(self.m._monitors(), (500, 500), mode="center")
        self.m.visible = True
        self.m.api._cursor = (-800, 400)
        self.assertTrue(self.m._follow_monitor(self.prefs, 0))
        self.assertEqual(self.m.geometry["monitor"], 1)
        self.assertFalse(self.m._follow_monitor(self.prefs, 0), "already there")
        self.m.pinned = True
        self.m.api._cursor = (500, 500)
        self.assertFalse(self.m._follow_monitor(self.prefs, 0), "a pinned window stays")

    def test_layer_hides_with_the_window_and_is_quick_ask_itself(self):
        layer = FakeLayer()
        self.m.layer = layer
        self.m.visible = True
        self.m.form = None

        self.m.api.foreground = layer.hwnd
        self.assertFalse(self.m.step_back())

        from app import win_hotkey
        own = {}
        old = win_hotkey.escape_should_close
        win_hotkey.escape_should_close = lambda *a, **k: own.update(k) or False
        try:
            self.m._escape_decide(0x1B, 0x100, 0)
        finally:
            win_hotkey.escape_should_close = old
        self.assertIn(layer.hwnd, own["own_windows"])
        self.m.hide(restore_focus=False)
        self.assertEqual(layer.hidden, 1)
        self.assertFalse(self.m.visible)

    def test_keepout_follows_resizes(self):
        layer = FakeLayer()
        self.m.layer = layer
        self.m.visible = True
        self.m.geometry = core.place(self.m._monitors(), (500, 500), mode="center")
        self.m.resize(400, animate=False)
        self.assertTrue(layer.keepouts)

    def test_layer_settings(self):
        old = qc._path
        qc._path = lambda: Path(self.tmp) / "quick_canvas.json"
        try:
            self.assertEqual(self.m.layer_settings(), {"on": False, "show": False}, "nothing placed — nothing to show")
            (Path(self.tmp) / "quick_canvas.json").write_text(json.dumps(R27_FILE, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self.m.layer_settings(), {"on": True, "show": True}, "last + it was open")
            data = dict(R27_FILE, settings=dict(R27_FILE["settings"], screen="hidden"))
            (Path(self.tmp) / "quick_canvas.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self.m.layer_settings(), {"on": True, "show": False})
        finally:
            qc._path = old

    def test_screenshots_exclude_the_layer(self):
        src = (ROOT / "app" / "quick_ask.py").read_text(encoding="utf-8")
        self.assertIn("ours = [hwnd for hwnd in (self.hwnd, int(self.main_hwnd() or 0), layer)", src)
        win = (ROOT / "app" / "quick_features_win.py").read_text(encoding="utf-8")
        self.assertIn("layer_excluded = manager._affinity(layer_hwnd, WDA_EXCLUDEFROMCAPTURE)", win)

class Wiring(unittest.TestCase):
    def test_static_files_versions_and_build(self):
        static = ROOT / "app" / "pgstatic"
        for name in ("quick/layer.html", "quick/layer.js", "quick/layer.css", "quick/quick-bus.js"):
            self.assertTrue((static / name).exists(), name)
        html = (static / "quick" / "layer.html").read_text(encoding="utf-8")
        self.assertRegex(html, r"layer\.js\?v=1\.\d+-r(28|29|30)")
        self.assertIn("quick-bus.js?v=1.0-r28", (static / "quick" / "index.html").read_text(encoding="utf-8"))
        manifest = json.loads((static / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
        canvas = next(p for p in manifest["plugins"] if p["id"] == "canvas")
        self.assertRegex(canvas["v"], r"-r(28|29|3\d)$")
        for spec in ("build.py", "MemuAI.spec"):
            text = (ROOT / spec).read_text(encoding="utf-8")
            self.assertIn("app.quick_layer", text, spec)
            self.assertIn("app.quick_layer_core", text, spec)

        self.assertRegex((ROOT / "app" / "version.py").read_text(encoding="utf-8"), r'APP_VERSION = "\d+\.\d+"', "one APP_VERSION")

    def test_every_layer_string_has_both_languages(self):
        import re
        src = (ROOT / "app" / "pgstatic" / "quick" / "layer.js").read_text(encoding="utf-8")
        pairs = re.findall(r't\("([^"]*)", "([^"]*)"\)', src)
        self.assertGreater(len(pairs), 20)
        for ru, en in pairs:
            self.assertTrue(re.search("[а-яА-ЯёЁ]", ru) or not re.search("[A-Za-z]{3,}", ru), ru)
            self.assertFalse(re.search("[а-яА-ЯёЁ]", en), en)

if __name__ == "__main__":
    unittest.main()
