"""Animated companion (Round 22): everything that can be checked off Windows.

* settings: defaults, normalisation and migration of ``companion_*`` keys and
  their wiring into ``pgdata`` (saved, unknown values repaired);
* model import validation: VRM 0.x / 1.0 containers (the shipped CC0 sample
  among them), broken/truncated/plain glTF/external URIs; Live2D zips with
  zip-slip paths, links, bombs, Cubism 2, missing moc3/textures;
* the model library on disk (import, list, rename, delete, path traversal);
* the Live2D runtime download rules (consent required, pinned hashes);
* the desktop window geometry (DPI, clamping, scaling, hit region);
* the HTTP routes and the stage page headers (CSP with wasm only, framing);
* the JavaScript core through Node: emotion tags (parse/strip, streaming),
  keyword emotions, loudness → viseme mapping, the text lip-sync track,
  prefs normalisation, and the zip builder of the folder import (its output
  is validated by the Python validator).

Not verifiable here: WebGL on real GPUs (screenshots use SwiftShader in CPU
skinning mode — tests/companion_ui_shots.py), Win32 transparency, WebView2.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import companion_avatar as core
from app import companion_window as cw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
SAMPLE = STATIC / "companion" / "models" / "sample" / "sample.vrm"
NODE = shutil.which("node")

def glb(doc: dict, binary: bytes = b"\0\0\0\0") -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
            + struct.pack("<I", len(binary)) + b"BIN\0" + binary)

VRM1 = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}], "bufferViews": [{"buffer": 0, "byteLength": 4}],
        "extensions": {"VRMC_vrm": {"specVersion": "1.0", "meta": {"name": "Test", "authors": ["A", "B"],
                                                                  "licenseUrl": "https://vrm.dev/licenses/1.0/",
                                                                  "allowRedistribution": True},
                                    "humanoid": {"humanBones": {"hips": {"node": 0}}},
                                    "expressions": {"preset": {"happy": {}, "aa": {}}}}}}

def live2d_zip(files: dict, symlink: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()

MODEL3 = json.dumps({"Version": 3, "FileReferences": {
    "Moc": "hiyori.moc3", "Textures": ["hiyori.2048/texture_00.png"],
    "Expressions": [{"Name": "Smile", "File": "exp/smile.exp3.json"}],
    "Motions": {"Idle": [{"File": "m/idle.motion3.json"}], "TapBody": [{"File": "m/tap.motion3.json"}]}}})
GOOD_L2D = {"hiyori/hiyori.model3.json": MODEL3, "hiyori/hiyori.moc3": b"MOC3" + b"\0" * 60,
            "hiyori/hiyori.2048/texture_00.png": b"\x89PNG....", "hiyori/exp/smile.exp3.json": "{}",
            "hiyori/m/idle.motion3.json": "{}", "hiyori/m/tap.motion3.json": "{}"}

class PrefsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        out = core.normalize_prefs(dict(core.PREF_DEFAULTS), {})
        self.assertFalse(out["companion_live_enabled"])
        self.assertFalse(out["companion_quick_enabled"])
        self.assertEqual(out["companion_quick_mode"], "inline")
        self.assertEqual(out["companion_model_id"], "sample")
        self.assertEqual((out["companion_size"], out["companion_fps"], out["companion_v"]), (100, 30, 1))

    def test_migration_and_repair(self) -> None:
        raw = {"companion_enabled": "yes", "companion_quick_window": True, "companion_size": "120%",
               "companion_fps": 50, "companion_model_id": "../../etc", "companion_live_placement": "floating",
               "companion_framing": "giant", "companion_lipsync": "off"}
        out = dict(core.PREF_DEFAULTS)
        out.update(raw)
        core.normalize_prefs(out, raw)
        self.assertTrue(out["companion_live_enabled"] and out["companion_quick_enabled"])
        self.assertEqual(out["companion_quick_mode"], "both")
        self.assertEqual((out["companion_size"], out["companion_fps"]), (120, 60))
        self.assertEqual(out["companion_model_id"], "sample")
        self.assertEqual(out["companion_live_placement"], "window")
        self.assertEqual(out["companion_framing"], "bust")
        self.assertFalse(out["companion_lipsync"])
        self.assertNotIn("companion_enabled", out)
        self.assertNotIn("companion_quick_window", out)
        named = dict(core.PREF_DEFAULTS, companion_size="large", companion_quick_mode="window")
        core.normalize_prefs(named, {"companion_v": 1})
        self.assertEqual((named["companion_size"], named["companion_quick_mode"]), (140, "desktop"))
        tiny = dict(core.PREF_DEFAULTS, companion_size=5)
        self.assertEqual(core.normalize_prefs(tiny, {})["companion_size"], 60)

    def test_migration_does_not_override_explicit_values(self) -> None:
        raw = {"companion_enabled": True, "companion_live_enabled": False}
        out = dict(core.PREF_DEFAULTS, **raw)
        core.normalize_prefs(out, raw)
        self.assertFalse(out["companion_live_enabled"], "an explicit new key wins over the legacy switch")
        self.assertTrue(out["companion_quick_enabled"])

    def test_pgdata_wiring(self) -> None:
        from app import pgdata
        for key in core.PREF_DEFAULTS:
            self.assertIn(key, pgdata.DEFAULT_PREFERENCES)
        saved = pgdata.save_preferences({"companion_quick_enabled": True, "companion_size": "150",
                                         "companion_model_id": "m-abc123"})
        self.assertTrue(saved["companion_quick_enabled"])
        self.assertEqual((saved["companion_size"], saved["companion_model_id"]), (150, "m-abc123"))
        loaded = pgdata.load_preferences()
        self.assertEqual(loaded["companion_size"], 150)
        pgdata.save_preferences({"companion_quick_enabled": False, "companion_size": 100, "companion_model_id": "sample"})

class VrmValidationTests(unittest.TestCase):
    def test_shipped_sample_is_valid_and_cc0(self) -> None:
        meta = core.validate_vrm(SAMPLE.read_bytes())
        self.assertEqual((meta["kind"], meta["vrm_version"]), ("vrm", "0.x"))
        self.assertEqual(meta["license"], "CC0")
        for name in ("a", "i", "u", "e", "o", "blink", "joy", "sorrow", "angry", "fun"):
            self.assertIn(name, meta["expressions"])
        self.assertLess(SAMPLE.stat().st_size, 12 * 1024 * 1024, "keep the installer small")
        notice = (STATIC / "companion" / "models" / "sample" / "LICENSE.md").read_text(encoding="utf-8")
        self.assertIn("CC0", notice)

    def test_vrm1(self) -> None:
        meta = core.validate_vrm(glb(VRM1))
        self.assertEqual(meta["vrm_version"], "1.0")
        self.assertEqual(meta["author"], "A, B")
        self.assertTrue(meta["allow_redistribution"])
        self.assertEqual(meta["expressions"], ["aa", "happy"])

    def test_rejections(self) -> None:
        cases = {
            "not_glb": b"PK\3\4 not a model at all......................",
            "truncated": glb(VRM1)[:-4],
            "not_vrm": glb({"asset": {"version": "2.0"}}),
            "no_humanoid": glb({"extensions": {"VRMC_vrm": {"meta": {}, "humanoid": {"humanBones": {}}}}}),
            "external_uri": glb(dict(VRM1, images=[{"uri": "C:/Users/x/secret.png"}])),
        }
        for code, data in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(core.ModelError) as ctx:
                    core.validate_vrm(data)
                self.assertEqual(ctx.exception.code, code)
        bad_version = bytearray(glb(VRM1))
        bad_version[4:8] = struct.pack("<I", 1)
        with self.assertRaises(core.ModelError):
            core.validate_vrm(bytes(bad_version))

class Live2DValidationTests(unittest.TestCase):
    def test_valid_zip(self) -> None:
        meta, members = core.validate_live2d_zip(live2d_zip(GOOD_L2D))
        self.assertEqual(meta["entry"], "hiyori/hiyori.model3.json")
        self.assertEqual(meta["expressions"], ["Smile"])
        self.assertEqual(meta["motions"], {"Idle": 1, "TapBody": 1})
        self.assertEqual(len(members), len(GOOD_L2D))

    def test_rejections(self) -> None:
        def broken(**changes):
            files = dict(GOOD_L2D)
            for k, v in changes.items():
                if v is None:
                    files.pop(k.replace("__", "/").replace("_DOT_", "."), None)
            return files
        cases = {
            "unsafe_path": live2d_zip(dict(GOOD_L2D, **{"../evil.txt": "x"})),
            "symlink": live2d_zip(GOOD_L2D, symlink="hiyori/link"),
            "no_model3": live2d_zip({"readme.txt": "hi"}),
            "cubism2": live2d_zip({"m/model.json": "{}", "m/model.moc": b"moc"}),
            "missing_file": live2d_zip({k: v for k, v in GOOD_L2D.items() if not k.endswith(".png")}),
            "bad_moc": live2d_zip(dict(GOOD_L2D, **{"hiyori/hiyori.moc3": b"NOPE"})),
            "not_zip": b"definitely not a zip",
        }
        absolute = io.BytesIO()
        with zipfile.ZipFile(absolute, "w") as z:
            z.writestr("/abs/hiyori.model3.json", MODEL3)
        cases["unsafe_path_abs"] = absolute.getvalue()
        for code, data in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(core.ModelError) as ctx:
                    core.validate_live2d_zip(data)
                self.assertEqual(ctx.exception.code, code.replace("_abs", ""))

    def test_zip_bomb(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("bomb.bin", b"\0" * (30 * 1024 * 1024))
            for k, v in GOOD_L2D.items():
                z.writestr(k, v)
        with self.assertRaises(core.ModelError) as ctx:
            core.validate_live2d_zip(buf.getvalue())
        self.assertEqual(ctx.exception.code, "zip_bomb")

    def test_reference_escaping_the_zip(self) -> None:
        model = json.loads(MODEL3)
        model["FileReferences"]["Moc"] = "../../outside.moc3"
        files = dict(GOOD_L2D, **{"hiyori/hiyori.model3.json": json.dumps(model)})
        with self.assertRaises(core.ModelError) as ctx:
            core.validate_live2d_zip(live2d_zip(files))
        self.assertEqual(ctx.exception.code, "missing_file")

class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.lib = core.Library(self.root)

    def test_import_list_rename_delete(self) -> None:
        self.assertEqual([m["id"] for m in self.lib.list()], ["sample"])
        vrm = self.lib.import_bytes("Mine.vrm", glb(VRM1))
        l2d = self.lib.import_bytes("hiyori.zip", live2d_zip(GOOD_L2D), name="Хиёри")
        self.assertEqual(vrm["name"], "Test")
        self.assertEqual(l2d["name"], "Хиёри")
        self.assertEqual(l2d["url"], f"/api/companion/models/{l2d['id']}/files/hiyori/hiyori.model3.json")
        ids = [m["id"] for m in self.lib.list()]
        self.assertEqual(ids, ["sample", vrm["id"], l2d["id"]])
        self.assertEqual(self.lib.file(l2d["id"], "hiyori/hiyori.moc3").read_bytes()[:4], b"MOC3")
        for bad in ("../meta.json", "meta.json", "/etc/passwd", "hiyori/../../x", "C:/Windows/win.ini"):
            self.assertIsNone(self.lib.file(l2d["id"], bad), bad)
        self.assertIsNone(self.lib.file("sample", "sample.vrm"), "the sample is served statically")
        self.assertEqual(self.lib.rename(vrm["id"], "  Новое имя ")["name"], "Новое имя")
        with self.assertRaises(core.ModelError):
            self.lib.delete("sample")
        self.assertTrue(self.lib.delete(vrm["id"]))
        self.assertFalse(self.lib.delete(vrm["id"]))
        self.assertEqual([m["id"] for m in self.lib.list()], ["sample", l2d["id"]])
        self.assertFalse(any(p.name.startswith(".tmp-") for p in (self.root / "models").iterdir()))

    def test_rejected_import_leaves_nothing(self) -> None:
        with self.assertRaises(core.ModelError):
            self.lib.import_bytes("x.zip", live2d_zip({"../evil": "x"}))
        with self.assertRaises(core.ModelError) as ctx:
            self.lib.import_bytes("x.txt", b"hello")
        self.assertEqual(ctx.exception.code, "unsupported")
        self.assertEqual([m["id"] for m in self.lib.list()], ["sample"])

    def test_model_ids_are_strict(self) -> None:
        self.assertEqual(core.normalize_model_id("m-0123abcd"), "m-0123abcd")
        for bad in ("", "..", "M-UP", "a/b", "x" * 60, None):
            self.assertEqual(core.normalize_model_id(bad), "sample")

class RuntimeTests(unittest.TestCase):
    def test_runtime_files_are_pinned(self) -> None:
        for name, (url, digest, _marker) in core.RUNTIME_FILES.items():
            self.assertTrue(url.startswith("https://"), name)
            if name == "live2dcubismcore.min.js":
                self.assertTrue(url.startswith("https://cubism.live2d.com/"), "official Live2D address only")
                self.assertIsNone(digest)
            else:
                self.assertRegex(digest or "", r"^[0-9a-f]{64}$", name)
                self.assertIn("@", url, "npm builds are version-pinned")

    def test_check_runtime_file(self) -> None:
        with self.assertRaises(core.ModelError) as ctx:
            core.check_runtime_file("pixi.min.js", b"PIXI but another build")
        self.assertEqual(ctx.exception.code, "runtime_hash")
        with self.assertRaises(core.ModelError) as ctx:
            core.check_runtime_file("live2dcubismcore.min.js", b"console.log('not the core')")
        self.assertEqual(ctx.exception.code, "runtime_marker")
        core.check_runtime_file("live2dcubismcore.min.js", b"var Live2DCubismCore;")
        self.assertFalse(core.runtime_status(Path(tempfile.mkdtemp()))["installed"])

class WindowGeometryTests(unittest.TestCase):
    def test_default_rect_scales_with_dpi(self) -> None:
        self.assertEqual(cw.default_rect((0, 0, 1920, 1040), 96), (1920 - 300 - 24, 1040 - 440 - 24, 300, 440))
        x, y, w, h = cw.default_rect((-2560, 0, 0, 1400), 192)
        self.assertEqual((w, h), (600, 880))
        self.assertEqual((x + w, y + h), (0 - 48, 1400 - 48))

    def test_clamp_and_scale(self) -> None:
        work = (0, 0, 1920, 1040)
        self.assertEqual(cw.clamp_rect((5000, -300, 10, 10), work), (1920 - 80, 0, 160, 220))
        x, y, w, h = cw.scale_rect((1000, 500, 300, 440), 1, work)
        self.assertEqual((w, h, x + w, y + h), (330, 484, 1300, 940))
        self.assertEqual(cw.scale_rect((0, 0, 160, 220), -1, work)[2:], (160, 220))

    def test_hit_region(self) -> None:
        region = cw.hit_region({"x": 50, "y": 60, "w": 150, "h": 300}, 1.5, (450, 660))
        self.assertEqual(region, (60, 0, 450, 555))
        self.assertIsNone(cw.hit_region({"x": 0, "y": 0, "w": 0, "h": 0}, 1, (300, 440)))
        self.assertIsNone(cw.hit_region({}, 1, (300, 440)))
        self.assertEqual(cw.hit_region({"x": -50, "y": -50, "w": 900, "h": 900}, 1, (300, 440)), (0, 0, 300, 440))

    def test_manager_off_windows(self) -> None:
        manager = cw.CompanionWindowManager("http://127.0.0.1:1", state_root=lambda: Path(tempfile.mkdtemp()))
        if manager.supported:  # pragma: no cover - only on Windows
            self.skipTest("Windows")
        self.assertEqual(manager.handle("status", {}), {"visible": False, "transparency": "", "place": "", "pinned": False})
        self.assertEqual(manager.hide(), {"visible": False})
        manager.destroy()

class RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        from app import companion_avatar_api
        self.app = create_app()
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        shutil.rmtree(companion_avatar_api.data_root() / "models", ignore_errors=True)

    def test_stage_page_headers(self) -> None:
        response = self.client.get("/companion/stage")
        self.assertEqual(response.status_code, 200)
        csp = response.headers["content-security-policy"]
        self.assertIn("'wasm-unsafe-eval'", csp)
        self.assertNotIn("'unsafe-eval'", csp.replace("'wasm-unsafe-eval'", ""))
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertEqual(response.headers["x-frame-options"], "SAMEORIGIN")
        self.assertIn("/companion/stage.js", response.text)
        main = self.client.get("/")
        self.assertNotIn("wasm-unsafe-eval", main.headers.get("content-security-policy", ""),
                         "the main window keeps its stricter policy")

    def test_library_routes(self) -> None:
        listing = self.client.get("/api/companion/models").json()
        self.assertEqual(listing["models"][0]["id"], "sample")
        self.assertFalse(listing["runtime"]["installed"])
        self.assertEqual(self.client.get(listing["models"][0]["url"]).status_code, 200)
        bad = self.client.post("/api/companion/models/import?filename=x.vrm", content=b"nope")
        self.assertEqual((bad.status_code, bad.json()["code"]), (400, "not_glb"))
        ok = self.client.post("/api/companion/models/import?filename=hiyori.zip", content=live2d_zip(GOOD_L2D)).json()
        model = ok["model"]
        self.assertEqual(self.client.get(model["url"]).json()["Version"], 3)
        self.assertEqual(self.client.get(f"/api/companion/models/{model['id']}/files/..%2Fmeta.json").status_code, 404)
        self.assertEqual(self.client.patch(f"/api/companion/models/{model['id']}", json={"name": "H"}).json()["model"]["name"], "H")
        self.assertEqual(self.client.delete("/api/companion/models/sample").status_code, 400)
        self.assertTrue(self.client.delete(f"/api/companion/models/{model['id']}").json()["ok"])
        self.assertEqual(self.client.delete(f"/api/companion/models/{model['id']}").status_code, 404)

    def test_runtime_needs_consent_and_window_is_optional(self) -> None:
        refused = self.client.post("/api/companion/runtime/install", json={})
        self.assertEqual((refused.status_code, refused.json()["code"]), (400, "consent"))
        self.assertEqual(self.client.get("/api/companion/runtime/files/live2dcubismcore.min.js").status_code, 404)
        self.assertEqual(self.client.get("/api/companion/runtime/files/..%2Fstate.json").status_code, 404)
        self.assertEqual(self.client.post("/api/companion/window", json={"action": "show"}).json(),
                         {"ok": False, "supported": False})
        state = self.client.post("/api/companion/state", json={"live_window": {"x": 2, "y": 0.25}, "junk": 1}).json()
        self.assertEqual(state["live_window"], {"x": 1.0, "y": 0.25})
        self.assertNotIn("junk", state)

class StaticWiringTests(unittest.TestCase):
    def test_files_and_licences(self) -> None:
        companion = STATIC / "companion"
        for name in ("stage.html", "stage.js", "stage.css", "companion.js", "companion.css", "companion-core.js",
                     "settings.js", "companion-live.js", "vendor/memu-three-vrm.min.js",
                     "third_party/LICENSE-three.txt", "third_party/LICENSE-three-vrm.txt", "third_party/NOTICE.md"):
            self.assertTrue((companion / name).is_file(), name)
        notice = (companion / "third_party" / "NOTICE.md").read_text(encoding="utf-8")
        for word in ("three.js", "three-vrm", "Live2D", "CC0"):
            self.assertIn(word, notice)

        for path in companion.rglob("*"):
            if path.is_file() and path.suffix in (".js", ".wasm"):
                self.assertNotIn(b"Live2D Cubism Core\n * (C)", path.read_bytes()[:400], path)
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        for script in ("companion-core.js", "companion.js", "settings.js", "companion-live.js"):
            self.assertIn("/companion/" + script, index)
        manifest = json.loads((STATIC / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
        row = next(r for r in manifest["plugins"] if r.get("id") == "companion")
        self.assertEqual((row["src"], row["settings"]), ("companion.js", "companion-settings.js"))
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        for hook in ('MemuCompanionLive?.emit("open")', 'MemuCompanionLive?.emit("close")',
                     'MemuCompanionLive?.emit("state"', 'MemuCompanionLive?.emit("audio"',
                     "MemuCompanionCore.stripTags(message.content)", "MemuCompanionLive?.promptHint"):
            self.assertIn(hook, app_js)

        self.assertNotIn("window.MemuCompanionCore", app_js)
        self.assertNotIn('window.MemuCompanionLive?.emit("audio"', app_js)
        self.assertNotIn("window.MemuCompanionLive?.promptHint", app_js)
        self.assertIn("get analyser()", (STATIC / "realtime.js").read_text(encoding="utf-8"))

        stage = (companion / "stage.js").read_text(encoding="utf-8")
        self.assertIn("const HEADROOM = DESKTOP ?", stage)

@unittest.skipUnless(NODE, "node is not installed")
class JsCoreTests(unittest.TestCase):
    def node(self, script: str) -> str:
        result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_js_core(self) -> None:
        out = self.node(r"""
const assert = require('assert');
const C = require('./app/pgstatic/companion/companion-core.js');
// tags: parse, strip, intensity, aliases, unknown names, spacing preserved elsewhere
let r = C.parseEmotionTags('[emotion:joy] Привет!\n    код  x\nHi [emo:sad 0.4] there <mood=wow>');
assert.strictEqual(r.text, 'Привет!\n    код  x\nHi there');
assert.deepStrictEqual(r.emotions.map(e => [e.emotion, e.intensity]), [['happy', 1], ['sad', 0.4], ['surprised', 1]]);
assert.strictEqual(r.last.emotion, 'surprised');
r = C.parseEmotionTags('[emotion:banana] ok');
assert.strictEqual(r.text, 'ok'); assert.strictEqual(r.emotions.length, 0);
// streaming: an unfinished tag at the end is hidden, ordinary brackets stay
assert.strictEqual(C.stripTags('Hello [emot', true), 'Hello');
assert.strictEqual(C.stripTags('Hello [emotion:ha', true), 'Hello');
assert.strictEqual(C.stripTags('a[i] and [x', true), 'a[i] and [x');
assert.strictEqual(C.stripTags('Hello [emot', false), 'Hello [emot');
// keyword fallback (RU/EN, emoji), no false positives inside words
assert.strictEqual(C.detectEmotion('Ого, это невероятно!').emotion, 'surprised');
assert.strictEqual(C.detectEmotion('К сожалению, не получится').emotion, 'sad');
assert.strictEqual(C.detectEmotion('Great news, congrats! 🎉').emotion, 'happy');
assert.strictEqual(C.detectEmotion('Много текста про парад и радио').score, 0);
assert.deepStrictEqual(C.emotionForAnswer('просто факт'), { emotion: 'neutral', intensity: 0, source: 'none' });
assert.strictEqual(C.emotionForAnswer('[emotion:angry] Отлично!').emotion, 'angry', 'a tag wins over keywords');
// loudness → openness → visemes
assert.strictEqual(C.levelToOpenness(0.005), 0);
assert.ok(C.levelToOpenness(0.05) > 0 && C.levelToOpenness(0.05) < C.levelToOpenness(0.2));
assert.strictEqual(C.levelToOpenness(5), 1);
const silence = new Uint8Array(512).fill(128);
assert.strictEqual(C.rmsFromBytes(silence), 0);
const square = new Uint8Array(512).map((_, i) => (i % 2 ? 228 : 28));
assert.ok(Math.abs(C.rmsFromBytes(square) - 100 / 128) < 1e-9);
const top = (w) => Object.entries(w).sort((a, b) => b[1] - a[1])[0][0];
assert.strictEqual(top(C.visemeWeights(0.8, { low: 0.7, mid: 0.35, high: 0.05 })), 'aa');
assert.strictEqual(top(C.visemeWeights(0.8, { low: 0.7, mid: 0.05, high: 0.02 })), 'oh');
assert.ok(['ih', 'ee'].includes(top(C.visemeWeights(0.8, { low: 0.08, mid: 0.4, high: 0.5 }))));
assert.deepStrictEqual(C.visemeWeights(0, { low: 1, mid: 1, high: 1 }), { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 });
const flat = C.visemeWeights(0.5, null);
assert.ok(flat.aa > flat.oh && flat.aa <= 0.5);
for (const w of [C.visemeWeights(1, { low: 1, mid: 0, high: 0 }), C.visemeWeights(1, { low: 0, mid: 0, high: 1 })])
  for (const v of Object.values(w)) assert.ok(v >= 0 && v <= 1);
// spectrum bands: a 440 Hz peak lands in "low"
const freq = new Uint8Array(512); freq[Math.round(440 / (48000 / 1024))] = 255;
const bands = C.bandsFromSpectrum(freq, 48000, 1024);
assert.ok(bands.low > 0 && bands.mid === 0 && bands.high === 0);
// smoothing: fast attack, slower release, never negative
const up = C.smoothVisemes({ aa: 0 }, { aa: 1 }, 1 / 30), down = C.smoothVisemes({ aa: 1 }, { aa: 0 }, 1 / 30);
assert.ok(up.aa > 1 - down.aa, 'attack is faster than release');
// text track: Cyrillic and Latin vowels, pauses on punctuation
const track = C.textVisemeTrack('Мама, oh!');
assert.strictEqual(C.sampleTrack(track, 62 + 1).aa, 0.75);
assert.ok(track.duration > 7 * 62);
assert.strictEqual(Object.values(C.sampleTrack(track, track.duration + 10)).reduce((a, b) => a + b), 0);
// prefs
const p = C.normalizePrefs({ companion_size: '500', companion_fps: '60', companion_quick_mode: 'x', companion_model_id: 'A/B', companion_lipsync: 'off' });
assert.deepStrictEqual([p.companion_size, p.companion_fps, p.companion_quick_mode, p.companion_model_id, p.companion_lipsync], [200, 60, 'inline', 'sample', false]);
assert.strictEqual(C.personalityProfile('calm').energy < C.personalityProfile('charismatic').energy, true);
assert.ok(C.promptHint().includes('[emotion:NAME]'));
// the zip builder of the Live2D folder import
global.window = { MemuCompanionCore: C };
require('./app/pgstatic/companion/settings.js');
const S = window.MemuCompanionSettings;
assert.strictEqual(S.crc32(new TextEncoder().encode('123456789')), 0xCBF43926);
const enc = new TextEncoder();
const files = [
  ['хиёри/hiyori.model3.json', JSON.stringify({ Version: 3, FileReferences: { Moc: 'hiyori.moc3', Textures: ['t/tex.png'] } })],
  ['хиёри/hiyori.moc3', 'MOC3' + '\0'.repeat(20)], ['хиёри/t/tex.png', 'png'],
].map(([name, text]) => ({ name, data: enc.encode(text) }));
S.buildZip(files).arrayBuffer().then((buf) => { process.stdout.write('ZIP:' + Buffer.from(buf).toString('base64')); });
""")
        zip_bytes = base64.b64decode(out.split("ZIP:", 1)[1])
        meta, members = core.validate_live2d_zip(zip_bytes)
        self.assertEqual(meta["entry"], "хиёри/hiyori.model3.json")
        self.assertEqual(len(members), 3)
        self.assertIsNone(zipfile.ZipFile(io.BytesIO(zip_bytes)).testzip(), "CRCs are correct")

    def test_js_syntax(self) -> None:
        for path in list((STATIC / "companion").glob("*.js")) + [STATIC / "quick" / "plugins" / "companion.js",
                                                                 STATIC / "quick" / "plugins" / "companion-settings.js"]:
            result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

if __name__ == "__main__":
    unittest.main()
