"""Round 26 — companion: the vertical gaze of VRM models and the invisible frames that cut the character.

* VRM: three-vrm's normalized bones keep the axes of the model file (VRM 0.x faces −Z, 1.0 faces +Z), so a pitch
  written for one version turns the other way on the other. The head pitch was written in 1.0 axes while every other
  pose (and the built-in 0.x sample) used 0.x axes: a cursor ABOVE her made her look DOWN. Checked with the real
  three.js of the bundle: cursor above → the face turns up, below → down, left/right unchanged, arms stay down on both
  VRM versions.
* Invisible frames: the desktop window region is cut to the stage's hit box, which for VRM was guessed from a few bones
  (twin-tails, sleeves, fingers and feet were cut) — now the painted pixels plus the gesture reach; the VRM frame
  measures the hands in perspective; Live2D measures its motions before it is shown; a resize/pin drops the old region;
  the window's own «hidden» reaches the stage even while BroadcastChannel is busy (no drawing in a hidden window).
* MemuAI Live: the previous reply's <audio> no longer stops the lip sync of the next one.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "app" / "pgstatic" / "companion"
NODE = shutil.which("node")

VRM_SCRIPT = r"""
globalThis.window = globalThis; globalThis.self = globalThis;
const C = require(process.argv[1]);
require(process.argv[2]);
const V = globalThis.MemuThreeVRM, T = V.THREE;
const out = {};
for (const version of ["0", "1"]) {
  const scene = new T.Object3D();
  V.VRMUtils.rotateVRM0({ meta: { metaVersion: version }, scene: scene });
  // Normalized bones keep the file's axes: 0.x faces −Z (its left arm points to −X), 1.0 faces +Z.
  const forward = new T.Vector3(0, 0, version === "0" ? -1 : 1);
  const leftArm = new T.Vector3(version === "0" ? -1 : 1, 0, 0);
  const bone = new T.Object3D();
  scene.add(bone);
  const dir = (local, x, y, z) => {
    const e = C.vrmEuler(version, x, y, z);
    bone.rotation.set(e[0], e[1], e[2]);
    scene.updateMatrixWorld(true);
    const v = local.clone().applyQuaternion(bone.getWorldQuaternion(new bone.quaternion.constructor()));
    return { x: +v.x.toFixed(3), y: +v.y.toFixed(3), z: +v.z.toFixed(3) };
  };
  out[version] = {
    above: dir(forward, C.vrmHeadPitch(-0.8), 0, 0),
    below: dir(forward, C.vrmHeadPitch(0.8), 0, 0),
    right: dir(forward, 0, 0.38 * 0.8, 0),
    rest: dir(forward, 0, 0, 0),
    leftArmDown: dir(leftArm, 0.08, -0.1, 1.18),
  };
}
process.stdout.write(JSON.stringify(out));
"""

@unittest.skipUnless(NODE, "node is not installed")
class VrmGazeAxesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run([NODE, "-e", VRM_SCRIPT, str(COMPANION / "companion-core.js"),
                                 str(COMPANION / "vendor" / "memu-three-vrm.min.js")],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.dirs = json.loads(result.stdout)

    def test_cursor_above_turns_the_face_up_on_both_versions(self) -> None:
        for version in ("0", "1"):
            d = self.dirs[version]
            self.assertGreater(d["rest"]["z"], 0.99, f"VRM {version}: she faces the camera")
            self.assertGreater(d["above"]["y"], 0.1, f"VRM {version}: cursor above → look UP")
            self.assertLess(d["below"]["y"], -0.1, f"VRM {version}: cursor below → look DOWN")

    def test_horizontal_follow_unchanged(self) -> None:
        for version in ("0", "1"):
            self.assertGreater(self.dirs[version]["right"]["x"], 0.1, f"VRM {version}: cursor right → viewer's right")

    def test_arms_rest_down_on_both_versions(self) -> None:
        for version in ("0", "1"):
            self.assertLess(self.dirs[version]["leftArmDown"]["y"], -0.8, f"VRM {version}: relaxed arm hangs down")

    def test_stage_poses_every_bone_through_the_axis_helper(self) -> None:
        src = (COMPANION / "stage.js").read_text(encoding="utf-8")
        self.assertIn("Core.vrmHeadPitch(this.headLook.y)", src)
        self.assertIn("this.version = vrm.meta && vrm.meta.metaVersion", src)

        self.assertEqual(len(re.findall(r"\.rotation\.set\(", src)), 1, "bone angles must go through rot()")
        self.assertNotRegex(src, r"\.rotation\.z\s*=", "bone angles must go through rot()")

if __name__ == "__main__":
    unittest.main()

class DesktopWindowStateTests(unittest.TestCase):
    """The desktop window's own state reaches the stage even while BroadcastChannel is busy, and a resize never
    keeps a region cut for the old size."""

    def _manager(self):
        import shutil as _shutil
        import tempfile
        from app import companion_window as cw
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_shutil.rmtree, tmp, True)
        m = cw.CompanionWindowManager("http://x", state_root=lambda: tmp)
        calls = []

        class Window:
            def evaluate_js(self, code):
                calls.append(code)

        class Api:
            rect = [100, 100, 300, 440]
            regions = []

            def IsWindow(self, hwnd): return True
            def ShowWindow(self, hwnd, cmd): return True
            def window_rect(self, hwnd): return tuple(self.rect)
            def work_area_at(self, x, y): return ((0, 0, 1920, 1040), 96)
            def cursor(self): return (500, 500)
            def button_down(self): return False

            def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
                if w and h:
                    self.rect = [x, y, w, h]
                return True

            def SetWindowRgn(self, hwnd, handle, redraw):
                self.regions.append(handle)
                return True

            def CreateRectRgn(self, *a): return 7
            def DeleteObject(self, h): return True
            def client_size(self, hwnd): return (self.rect[2], self.rect[3])
        m.api, m.hwnd, m.visible, m.window = Api(), 1, True, Window()
        return m, calls

    def test_hide_and_pin_bypass_the_relay_filter(self) -> None:
        m, calls = self._manager()
        m.handle("event", {"event": {"type": "gesture", "name": "wave"}})
        m.pin({"pinned": True})
        m.hide()
        self.assertIn("MemuStage.relay(", calls[0], "copies of channel messages still go through relay()")
        self.assertIn("MemuStage.receive(", calls[1])
        self.assertIn('MemuStage.receive({"type": "visible", "on": false})', calls[2],
                      "relay() ignores messages while the channel is busy: the hidden window kept drawing")

    def test_pin_with_a_new_size_drops_the_old_region(self) -> None:
        m, _calls = self._manager()
        m.hit({"rect": {"x": 80, "y": 120, "w": 140, "h": 300}, "dpr": 1})
        self.assertIsNotNone(m._region)
        m.detach({"screen": {"x": 400, "y": 300, "w": 520, "h": 340}, "dpr": 1})
        self.assertEqual(tuple(m.api.rect[2:]), (520, 340))
        self.assertIsNone(m._region, "a region cut for 300×440 would crop the model in the new 520×340 window")
        self.assertIsNone(m.api.regions[-1])

@unittest.skipUnless(NODE, "node is not installed")
class FramingMathTests(unittest.TestCase):
    def node(self, expr: str):
        script = (f"const C = require({json.dumps(str(COMPANION / 'companion-core.js'))});"
                  f"process.stdout.write(JSON.stringify({expr}));")
        result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_alpha_bounds_of_a_readback(self) -> None:

        box = self.node("(() => { const p = new Uint8Array(4 * 3 * 4); p[(1 * 4 + 2) * 4 + 3] = 255;"
                        " p[(0 * 4 + 0) * 4 + 3] = 5; return C.alphaBounds(p, 4, 3, 0.5, 0.5); })()")
        self.assertEqual(box, {"x0": 1, "x1": 1.5, "y0": 0.5, "y1": 1}, "alpha ≤ 8 (haze) is empty; rows flip")
        self.assertIsNone(self.node("C.alphaBounds(new Uint8Array(48), 4, 3, 1, 1)"))

    def test_reach_distance_keeps_a_near_hand_inside(self) -> None:
        tan_h, tan_v, center_y = 0.21, 0.32, 1.2
        point = {"x": -0.45, "y": 1.3, "z": 0.25, "m": 0.07}
        dist = self.node(f"C.reachDistance([{json.dumps(point)}], {tan_h}, {tan_v}, {center_y})")

        depth = dist - point["z"]
        self.assertLessEqual((abs(point["x"]) + point["m"]) / depth, tan_h + 1e-9)
        self.assertLessEqual((point["y"] + point["m"] - center_y) / depth, tan_v + 1e-9)
        flat = (abs(point["x"]) + point["m"]) / tan_h
        self.assertGreater(dist, flat, "the old z = 0 maths stood too close: the near hand was cut")

class StageClippingSourceTests(unittest.TestCase):
    def test_hit_box_follows_the_pixels_and_the_gestures(self) -> None:
        src = (COMPANION / "stage.js").read_text(encoding="utf-8")
        self.assertIn("gl.readPixels(", src, "VRM hit box = the painted pixels, not a guess from a few bones")
        self.assertIn("Core.alphaBounds(", src)
        self.assertIn("S.bboxAt = 0;", src, "a gesture re-reports the box on the next frame")
        self.assertIn("Core.reachDistance(this.reach.points", src)

        live2d = src[src.index("class Live2DDriver"):]
        self.assertIn("const env = this.env;", live2d[live2d.index("    bbox() {"):])

        load = live2d[live2d.index("    async load(model) {"):live2d.index("    async measureMotions(PIXI) {")]
        self.assertIn("await this.measureMotions(PIXI);", load)
        motions = live2d[live2d.index("    async measureMotions(PIXI) {"):live2d.index("    frameCut() {")]
        self.assertIn("config.sound = false;", motions, "measuring must not play the motions' voice lines")
        self.assertIn("config.sound = sound;", motions)
        self.assertIn("mm.stopAllMotions()", motions)

        frame = src[src.index("  function frame(now) {"):src.index("  function reportBox()")]
        self.assertLess(frame.index("S.driver.render();"), frame.index("reportBox();"))

LIVE_SCRIPT = r"""
const vm = require("vm"), fs = require("fs");
const calls = [];
class Audio { constructor() { this.h = {}; this.ended = false; this.currentTime = 1; }
  addEventListener(t, f) { (this.h[t] = this.h[t] || []).push(f); }
  fire(t) { (this.h[t] || []).splice(0).forEach((f) => f()); } }
const el = { classList: { add() {}, remove() {}, toggle() {} }, dataset: {}, hidden: false };
const inst = { el, loaded: true, setVisible() {}, gesture() {}, state(s) { calls.push("state:" + s); },
  attachAudio(a) { calls.push("attach:" + a.id); }, detachAudio() { calls.push("detach"); },
  text() {}, speak(on) { calls.push("speak:" + on); }, setPrefs() {}, destroy() {} };
const host = { classList: el.classList, querySelector: () => ({ insertBefore() {} }) };
const ctx = { window: {}, document: { getElementById: () => host, body: {} }, fetch: () => Promise.reject(new Error("x")),
  pref: (k, f) => (k === "companion_live_enabled" ? true : f), S: { preferences: {} } };
ctx.window.MemuCompanionCore = require(process.argv[1]);
ctx.window.MemuCompanion = { create: () => inst };
ctx.MemuCompanion = ctx.window.MemuCompanion;
vm.createContext(ctx);
vm.runInContext("var window = this.window;" + fs.readFileSync(process.argv[2], "utf8"), ctx);
const Live = ctx.window.MemuCompanionLive;
Live.emit("open");
const first = new Audio(), second = new Audio();
first.id = 1; second.id = 2;
Live.emit("audio", { audio: first, text: "one" });
Live.emit("audio", { audio: second, text: "two" });
calls.push("|");
first.fire("pause");            // the previous reply is paused when the next one starts
calls.push("|");
second.fire("ended");
process.stdout.write(JSON.stringify(calls));
"""

@unittest.skipUnless(NODE, "node is not installed")
class LiveAudioRaceTests(unittest.TestCase):
    def test_previous_reply_never_stops_the_next(self) -> None:
        result = subprocess.run([NODE, "-e", LIVE_SCRIPT, str(COMPANION / "companion-core.js"),
                                 str(COMPANION / "companion-live.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout)
        cut = calls.index("|")
        after_pause = calls[cut + 1:calls.index("|", cut + 1)]
        self.assertEqual(after_pause, [], "the paused previous <audio> detached the reply that is playing")
        self.assertIn("detach", calls[calls.index("|", cut + 1):], "the playing reply still ends the lip sync")
