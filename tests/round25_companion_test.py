"""Round 25 — companion: gaze direction, global cursor feed, framing maths, ru/en model errors.

* the gaze is measured from the character's face and the Live2D focus flips the screen y once
  (cursor BELOW the face → she looks DOWN; the inverted-gaze report);
* the global cursor feed (``/api/companion/cursor/stream``) sends only changes plus a heartbeat and stops when
  the stage disconnects; off Windows the routes say "unsupported" so the stage never polls;
* ``fitBox`` keeps the character (with room for raised arms) inside the stage;
* the Live2D driver no longer forces the TapBody motion on every nod, uses the model's own idle group and waits
  for pose3.json; the VRM "think" pose no longer twists the forearm into the twin-tail;
* rejected models carry an English message next to the Russian one.
"""
from __future__ import annotations

import ast
import asyncio
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from app import companion_avatar as core
from app import companion_avatar_api as api
from app import companion_window as cw

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "app" / "pgstatic" / "companion"
NODE = shutil.which("node")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def collect(gen, limit):
    out = []
    async for line in gen:
        out.append(line)
        if len(out) >= limit:
            break
    return out


@unittest.skipUnless(NODE, "node is not installed")
class GazeMathTests(unittest.TestCase):
    def node(self, expr: str):
        script = (f"const C = require({json.dumps(str(COMPANION / 'companion-core.js'))});"
                  f"process.stdout.write(JSON.stringify({expr}));")
        result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cursor_below_the_face_looks_down(self) -> None:
        face = "{x: 150, y: 120}"
        # Cursor below her face but above the middle of the frame: the old frame-centre origin made her look up.
        below = self.node(f"C.live2dFocus(C.lookVector({{x: 150, y: 170}}, {face}, 300))")
        self.assertEqual(below["x"], 0)
        self.assertLess(below["y"], 0, "Live2D +y is UP: a cursor below the face must give a negative focus y")
        above = self.node(f"C.live2dFocus(C.lookVector({{x: 150, y: 10}}, {face}, 300))")
        self.assertGreater(above["y"], 0)
        right = self.node(f"C.live2dFocus(C.lookVector({{x: 900, y: 120}}, {face}, 300))")
        self.assertGreater(right["x"], 0.9)
        left = self.node(f"C.lookVector({{x: -4000, y: 120}}, {face}, 300)")
        self.assertLess(left["x"], -0.99)
        self.assertEqual(self.node(f"C.lookVector(null, {face}, 300)"), {"x": 0, "y": 0})

    def test_screen_to_client(self) -> None:
        # Physical px at 150 % DPI → CSS px of a viewport whose top-left is at (100, 50) CSS screen px.
        self.assertEqual(self.node("C.screenToClient({x: 300, y: 150}, {x: 100, y: 50}, 1.5)"), {"x": 100, "y": 50})

    def test_fit_box_keeps_raised_arms_inside(self) -> None:
        fit = self.node("C.fitBox({x0: 0, y0: 0, x1: 100, y1: 400}, 300, 390,"
                        " {pad: {x: 60, top: 20, bottom: 0}, headroom: 0.2})")
        scale, x, y = fit["scale"], fit["x"], fit["y"]
        left, right = x + (0 - 60) * scale, x + (100 + 60) * scale
        top, bottom = y + (0 - 20) * scale, y + 400 * scale
        self.assertGreaterEqual(left, -1e-6)
        self.assertLessEqual(right, 300 + 1e-6)
        self.assertGreaterEqual(top, 390 * 0.2 - 1e-6, "the desktop bubble keeps its headroom")
        self.assertLessEqual(bottom, 390 + 1e-6)
        bust = self.node("C.fitBox({x0: 0, y0: 0, x1: 100, y1: 400}, 300, 390, {bust: true, bustCut: 200})")
        self.assertAlmostEqual(bust["y"], 0, places=6)
        self.assertAlmostEqual(bust["scale"], 390 / 200, places=6)


class StageSourceTests(unittest.TestCase):
    """Guards for fixes that only show on a real GPU (checked visually with headless Chromium in Round 25)."""

    def setUp(self) -> None:
        self.stage = (COMPANION / "stage.js").read_text(encoding="utf-8")
        self.host = (COMPANION / "companion.js").read_text(encoding="utf-8")

    def test_live2d_motions_and_pose(self) -> None:
        self.assertIn("idleMotionGroup", self.stage, "the model's own idle group is passed to pixi-live2d-display")
        self.assertIn("poseLoaded", self.stage, "measuring waits for pose3.json (alternative arm parts)")
        self.assertNotIn("/tap|flick|shake|body/i", self.stage, "a nod must not start a random TapBody motion")
        self.assertIn('code: "cubism2"', self.stage)

    def test_vrm_think_pose_does_not_twist_the_forearm(self) -> None:
        self.assertNotIn("rightLowerArm.rotation.y -= 1.6", self.stage)
        self.assertIn("thinkPose(w)", self.stage)

    def test_host_sends_the_cursor_not_a_frame_centre_direction(self) -> None:
        self.assertIn('send("cursor"', self.host)
        self.assertNotIn('send("look"', self.host)
        self.assertIn("/api/companion/cursor/stream", self.stage)


class CursorFeedTests(unittest.TestCase):
    def test_only_changes_and_heartbeat(self) -> None:
        positions = iter([(1, 2), (1, 2), (3, 4), (3, 4), (3, 4), (5, 6)])
        clock = {"t": 0.0}

        async def sleep(dt):
            clock["t"] += dt

        gen = api.cursor_events(lambda: next(positions), hz=30, heartbeat=0.05, clock=lambda: clock["t"], sleep=sleep)
        lines = run(collect(gen, 5))
        self.assertEqual(lines[0], "retry: 3000\n\n")
        data = [line for line in lines if line.startswith("data:")]
        self.assertEqual(data, ["data: 1,2\n\n", "data: 3,4\n\n", "data: 5,6\n\n"])
        self.assertIn(": ping\n\n", lines, "an idle cursor still gets a heartbeat so a closed stage is noticed")

    def test_stops_on_disconnect_and_survives_a_failing_read(self) -> None:
        calls = {"n": 0}

        def read():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("secure desktop")
            return (7, 8)

        state = {"n": 0}

        async def gone():
            state["n"] += 1
            return state["n"] > 3

        async def sleep(_dt):
            return None

        lines = run(collect(api.cursor_events(read, hz=1000, is_disconnected=gone, sleep=sleep, clock=lambda: 0.0), 99))
        self.assertEqual(lines, ["retry: 3000\n\n", "data: 7,8\n\n"])

    def test_off_windows_routes_say_unsupported(self) -> None:
        if sys.platform == "win32":  # pragma: no cover - only on Windows
            self.skipTest("Windows")
        self.assertIsNone(cw.cursor_reader())
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        self.assertEqual(client.get("/api/companion/cursor").json(), {"supported": False, "x": None, "y": None, "relative": False})
        self.assertEqual(client.get("/api/companion/cursor/stream").status_code, 404)


class ErrorLanguageTests(unittest.TestCase):
    def test_model_errors_have_english(self) -> None:
        with self.assertRaises(core.ModelError) as ctx:
            core._glb_json(b"not a model")
        self.assertRegex(str(ctx.exception), "[А-Яа-я]")
        self.assertRegex(ctx.exception.message_en, r"^[ -~—]+$")
        response = api._error(ctx.exception)
        body = json.loads(response.body)
        self.assertEqual(body["code"], "not_glb")
        self.assertEqual(body["error_en"], "The file is not a VRM/GLB (no glTF signature)")

    def test_every_model_error_in_the_sources_is_bilingual(self) -> None:
        for name in ("companion_avatar.py", "companion_avatar_api.py"):
            tree = ast.parse((ROOT / "app" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "ModelError":
                    self.assertEqual(len(node.args), 3, f"{name}:{node.lineno}: ModelError without an English message")

if __name__ == "__main__":
    unittest.main()
