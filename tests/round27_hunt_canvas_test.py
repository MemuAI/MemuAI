"""Round 27 (r27-hunt-canvas): regressions found reviewing «Панели и Быстрые действия» in Quick Ask.

* the first-run approval list shows what a step really does, never only its (importable) label;
* a double press / two requests never start a second run of one button;
* «Остановить» interrupts a silent AI step; open.url refuses javascript:/file: on save; command.run counts with the
  tier of the user's command;
* global hotkeys of buttons (win_hotkey listeners, faked here): sync, conflicts, imports never take them, a press runs
  the button without the window or opens the window when it needs one, a failed hotkey run is surfaced once;
* the program icon (PNG writer, BGRA→RGBA with the mask, non-Windows message);
* the window: a drag in a narrow window keeps the stored size of wide panels; buttons are not re-rendered each time
  (headless Chromium, skipped without it).
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r27hc-test-")

from app import quick_actions as qa  # noqa: E402
from app import quick_canvas as qc  # noqa: E402
from app import quick_app_icon  # noqa: E402
from round27_quick_canvas_test import FakeEngine, action_of, make_runner, wait_status  # noqa: E402

class Approval(unittest.TestCase):
    def test_risk_text_is_the_real_step_not_its_label(self):
        a = qa.validate_action({"label": "x", "steps": [{"type": "keys", "keys": "Ctrl+Alt+Del", "label": "Открыть калькулятор"}]})
        text = qa.risks(a, "ru")[0]["text"]
        self.assertIn("Ctrl+Alt+Del", text)
        self.assertIn("Открыть калькулятор", text)
        self.assertTrue(text.startswith("Нажать"), text)

    def test_open_url_refuses_other_schemes_on_save(self):
        for bad in ("javascript:alert(1)", "file:///C:/Windows/System32/cmd.exe", "ms-settings:"):
            with self.assertRaises(qa.ActionInvalid, msg=bad):
                qa.validate_step({"type": "open", "target": "url", "value": bad})
        for ok in ("https://ya.ru", "ya.ru", "youtube", "{clipboard}"):
            qa.validate_step({"type": "open", "target": "url", "value": ok})

    def test_command_run_takes_the_tier_of_the_command(self):
        old = qa._command_tier
        try:
            qa._command_tier = lambda params: "confirm" if params.get("id") == "risky" else ""
            step = {"type": "command", "action": "command.run", "params": {"id": "risky"}}
            self.assertEqual(qa.step_tier(step), "confirm")
            self.assertEqual(qa.step_tier({**step, "params": {"id": "other"}}), "safe")
        finally:
            qa._command_tier = old

    def test_global_hotkey_needs_ctrl_or_alt_and_is_dropped_with_the_hotkey(self):
        with self.assertRaises(qa.ActionInvalid):
            qa.validate_action({"label": "f", "hotkey": "F8", "global": True, "steps": [{"type": "wait", "ms": 100}]})
        self.assertFalse(qa.validate_action({"label": "f", "hotkey": "", "global": True, "steps": [{"type": "wait", "ms": 100}]})["global"])
        self.assertTrue(qa.validate_action({"label": "f", "hotkey": "Ctrl+Alt+7", "global": True, "steps": [{"type": "wait", "ms": 100}]})["global"])

class ShellConfirmation(unittest.TestCase):
    def test_a_long_or_padded_command_cannot_hide_its_tail(self):
        from app.assistant_commands import describe
        padded = describe("shell.run", {"command": "dir" + " " * 500 + "; Remove-Item C:/x"}, "ru")
        self.assertIn("Remove-Item", padded, "whitespace padding is folded for display")
        long = describe("shell.run", {"command": "x" * 450}, "en")
        self.assertIn("150 more characters", long)

class Runs(unittest.IsolatedAsyncioTestCase):
    async def test_stop_interrupts_a_silent_ai_step(self):
        started = asyncio.Event()

        async def slow_ai(prompt, lang):
            started.set()
            await asyncio.sleep(30)
            return "late"
        runner = make_runner(FakeEngine(), ask_ai=slow_ai)
        run = runner.start(action_of([{"type": "ask", "mode": "silent", "prompt": "hi"}, {"type": "text", "mode": "copy", "text": "{prev}"}]))
        await asyncio.wait_for(started.wait(), 2)
        t0 = time.monotonic()
        self.assertTrue(runner.cancel(run.id))
        await wait_status(run, timeout=2)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.steps[1]["status"], "skipped")

class FakeListener:
    taken = set()

    def __init__(self, callback):
        self.callback, self.active = callback, ""
        self.stopped = False

    def set_hotkey(self, hotkey):
        if hotkey in FakeListener.taken:
            return {"ok": False, "hotkey": hotkey, "code": "taken", "error": "busy"}
        self.active = hotkey
        return {"ok": True, "hotkey": hotkey, "code": "registered", "error": ""}

    def clear(self):
        self.active = ""

    def suspend(self, seconds=20.0):
        self.suspended = self.active

    def resume(self):
        self.suspended = ""

    def stop(self):
        self.stopped = True

class FakeManager:
    def __init__(self, visible=False):
        self.visible, self.hwnd, self.previous_hwnd = visible, 1, 0
        self.shown, self.pushed = [], []

    def show(self, source):
        self.shown.append(source)
        return True

    def _push(self, script):
        self.pushed.append(script)

class Routes(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp(prefix="r27hc-routes-")
        self.saved = (qc._path, qc.RUNNER, qc.HOTKEYS)
        qc._path = lambda: Path(self.tmp) / "quick_canvas.json"
        self.engine = FakeEngine()
        qc.RUNNER = make_runner(self.engine)
        self.manager = FakeManager()
        qc.RUNNER._manager = lambda: self.manager
        qc.HOTKEYS = qc.GlobalHotkeys(listener_factory=FakeListener)
        FakeListener.taken = set()
        app = FastAPI()
        app.include_router(qc.router)
        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        qc._path, qc.RUNNER, qc.HOTKEYS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, action, ok=True):
        out = self.client.post("/api/quick/canvas/actions", json={"action": action}).json()
        self.assertEqual(bool(out.get("ok")), ok, out)
        return out

    def test_two_requests_never_start_two_runs(self):
        a = self.save({"label": "slow", "steps": [{"type": "wait", "ms": 800}]})["action"]
        first = self.client.post("/api/quick/canvas/run", json={"id": a["id"]}).json()
        second = self.client.post("/api/quick/canvas/run", json={"id": a["id"]}).json()
        self.assertTrue(second.get("already"))
        self.assertEqual(first["run"]["id"], second["run"]["id"])
        self.assertEqual(len(self.client.get("/api/quick/canvas/runs").json()["runs"]), 1)

    def test_hotkey_conflicts(self):
        self.save({"label": "one", "hotkey": "Ctrl+Alt+1", "steps": [{"type": "wait", "ms": 100}]})
        out = self.save({"label": "two", "hotkey": "Ctrl+Alt+1", "steps": [{"type": "wait", "ms": 100}]}, ok=False)
        self.assertIn("one", out["error"])

    def test_global_hotkeys_sync_register_and_release(self):
        a = self.save({"label": "g", "hotkey": "Ctrl+Alt+2", "global": True, "steps": [{"type": "wait", "ms": 100}]})
        self.assertTrue(a["hotkey"]["ok"])
        self.assertIn(a["action"]["id"], qc.HOTKEYS.listeners)
        FakeListener.taken = {"Ctrl+Alt+3"}
        b = self.save({"label": "busy", "hotkey": "Ctrl+Alt+3", "global": True, "steps": [{"type": "wait", "ms": 100}]})
        self.assertFalse(b["hotkey"]["ok"], "a combo another program holds is reported, the button is still saved")
        st = self.client.get("/api/quick/canvas").json()
        self.assertEqual(st["hotkeys"]["status"][b["action"]["id"]]["code"], "taken")
        listener = qc.HOTKEYS.listeners[a["action"]["id"]]
        self.client.post("/api/quick/canvas/settings", json={"settings": {"actions": False}})
        self.assertTrue(listener.stopped, "buttons switched off → their global hotkeys are released")
        self.assertEqual(qc.HOTKEYS.listeners, {})

    def test_capturing_a_combo_suspends_the_global_ones(self):
        a = self.save({"label": "g", "hotkey": "Ctrl+Alt+2", "global": True, "steps": [{"type": "wait", "ms": 100}]})["action"]
        listener = qc.HOTKEYS.listeners[a["id"]]
        self.client.post("/api/quick/canvas/hotkeys", json={"suspend": True})
        self.assertEqual(listener.suspended, "Ctrl+Alt+2")
        self.client.post("/api/quick/canvas/hotkeys", json={"suspend": False})
        self.assertEqual(listener.suspended, "")

    def test_import_never_takes_global_hotkeys(self):
        data = {"format": qc.EXPORT_FORMAT, "actions": [{"label": "i", "hotkey": "Ctrl+Alt+4", "global": True, "steps": [{"type": "wait", "ms": 100}]}]}
        out = self.client.post("/api/quick/canvas/import", json={"data": data}).json()
        self.assertEqual(out["added"], 1)
        self.assertFalse(out["actions"][0]["global"])
        self.assertEqual(qc.HOTKEYS.listeners, {})

    def run_hotkey(self, action_id):
        async def go():
            run = await qc.hotkey_run(action_id, foreground=777)
            if run is not None:
                await wait_status(run)
                await asyncio.sleep(0.3)
            return run
        return self.client.portal.call(go)

    def test_hotkey_runs_without_the_window(self):
        a = self.save({"label": "vol", "hotkey": "Ctrl+Alt+5", "global": True,
                       "steps": [{"type": "command", "action": "volume.set", "params": {"level": 20}}]})["action"]
        run = self.run_hotkey(a["id"])
        self.assertEqual(run.status, "done")
        self.assertEqual(self.engine.calls[-1][0], "volume.set")
        self.assertEqual(self.manager.shown, [], "a safe button does its job without opening the window")
        self.assertEqual(self.manager.previous_hwnd, 777, "a paste would go back to the program in front")

    def test_hotkey_opens_the_window_when_the_button_needs_it(self):
        ask = self.save({"label": "ask", "hotkey": "Ctrl+Alt+6", "global": True, "steps": [{"type": "ask", "mode": "window", "prompt": "hi"}]})["action"]
        self.assertIsNone(self.run_hotkey(ask["id"]))
        risky = self.save({"label": "close", "hotkey": "Ctrl+Alt+7", "global": True,
                           "steps": [{"type": "command", "action": "app.close", "params": {"name": "x"}}]})["action"]
        self.assertIsNone(self.run_hotkey(risky["id"]), "an unapproved risky button shows its approval sheet in the window")
        self.assertEqual(self.manager.shown, ["hotkey", "hotkey"])
        self.assertTrue(all("MemuQuickCanvas.runAction" in s for s in self.manager.pushed))
        self.assertEqual(self.engine.calls, [])

    def test_failed_hotkey_run_is_surfaced_once(self):
        self.engine.fail = {"volume.set"}
        a = self.save({"label": "vol", "hotkey": "Ctrl+Alt+8", "global": True,
                       "steps": [{"type": "command", "action": "volume.set", "params": {"level": 20}}]})["action"]
        run = self.run_hotkey(a["id"])
        self.assertEqual(run.status, "error")
        self.assertEqual(self.manager.shown, ["api"], "the error opens the window")
        runs = self.client.get("/api/quick/canvas").json()["runs"]
        self.assertEqual([r["id"] for r in runs], [run.id])
        self.assertEqual(self.client.get("/api/quick/canvas").json()["runs"], [], "shown once")

    def test_hotkey_of_a_non_global_or_switched_off_button_does_nothing(self):
        a = self.save({"label": "local", "hotkey": "Ctrl+Alt+9", "steps": [{"type": "wait", "ms": 50}]})["action"]
        self.assertIsNone(self.run_hotkey(a["id"]))
        self.assertIsNone(self.run_hotkey("a_missing00"))

class AppIcon(unittest.TestCase):
    def test_png_and_alpha_from_the_mask(self):
        from PIL import Image
        bgra = bytes([255, 0, 0, 0, 0, 0, 255, 0])
        rgba = quick_app_icon.bgra_to_rgba(bgra, bytes([255, 255, 255, 0, 0, 0, 0, 0]))
        url = quick_app_icon.png_data_url(2, 1, rgba)
        self.assertTrue(qa.IMAGE_RE.match(url), "accepted as a button picture")
        img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual([img.getpixel((0, 0)), img.getpixel((1, 0))], [(0, 0, 255, 0), (255, 0, 0, 255)])
        kept = quick_app_icon.bgra_to_rgba(bytes([1, 2, 3, 128]))
        self.assertEqual(kept, bytes([3, 2, 1, 128]), "real alpha is kept")

    @unittest.skipIf(sys.platform == "win32", "the message is for other systems")
    def test_other_systems_get_a_clear_message(self):
        out = quick_app_icon.app_icon("notepad", "en")
        self.assertFalse(out["ok"])
        self.assertIn("Windows", out["error"])
        self.assertFalse(quick_app_icon.app_icon("", "ru")["ok"])

class Window(unittest.TestCase):
    def test_headless_window(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")
        out = Path(tempfile.mkdtemp(prefix="r27hc-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            env.pop("MEMUAI_DATA_DIR", None)
            result = subprocess.run([sys.executable, str(ROOT / "tests" / "round27_hunt_canvas_shots.py"), str(out)],
                                    capture_output=True, text=True, timeout=240, env=env)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

class SizeCap(unittest.TestCase):
    """r27-hunt-canvas #16: all buttons together are capped (a 77 MB import made every window open slow)."""

    def _doc(self):
        return {"actions": [], "layout": {"items": []}}

    def test_check_size_rejects_oversized_set(self):
        doc = self._doc()
        big = action_of([{"type": "text", "mode": "paste", "text": "x" * 15000}])
        doc["actions"] = [dict(big, id=f"a{i}") for i in range(qc.MAX_ACTIONS_BYTES // 15000 + 5)]
        with self.assertRaises(qa.ActionInvalid):
            qc.check_size(doc)

    def test_small_set_passes(self):
        doc = self._doc()
        doc["actions"] = [action_of([{"type": "text", "mode": "paste", "text": "hi"}])]
        qc.check_size(doc)

    def test_import_skips_what_does_not_fit(self):
        doc = self._doc()
        row = {k: v for k, v in action_of([{"type": "text", "mode": "paste", "text": "y" * 15000}]).items()}
        n = qc.MAX_ACTIONS_BYTES // 15000 + 20
        data = {"format": qc.EXPORT_FORMAT, "actions": [dict(row, id=f"r{i}") for i in range(n)]}
        out = qc.import_into(doc, data)
        total = sum(qc.action_bytes(a) for a in doc["actions"])
        self.assertLessEqual(total, qc.MAX_ACTIONS_BYTES)
        self.assertEqual(out["added"] + out["skipped"], min(n, qa.MAX_ACTIONS))
