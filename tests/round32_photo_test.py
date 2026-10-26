"""Round 32 (photo): MemuNote photo editor — formats, pixel maths, layers / blend / undo, AI flows.

Run: /workspace/work/venv/bin/python -m pytest tests/round32_photo_test.py -q
* formats: PSD writer/reader round trip (+ psd-tools when installed), Pillow encoders/decoders, .memuimg in the page;
* pixels.js in Node: adjustments, filters, blend modes, selection masks, segmentation, inpainting;
* HTTP: /api/photo/* (opened-files guard, atomic writes, conflicts, PSD), AI SSE against a fake MemuAI Proxy;
* browser (headless Chromium, skipped without it): open → paint → undo exact, blend compositing = W3C maths, layers,
  memuimg/PSD/SVG/PNG/JPEG export, transform, adjustments dialog, AI panel with the fake Proxy;
* live (skipped without XKIRO_* keys): a real vision model describes an image and returns a valid edit plan.
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "memunote" / "static"
PHOTO_JS = STATIC / "js" / "photo"
CHROME = next((p for p in ("/usr/bin/chromium", "/workspace/work/chromium/chrome") if os.path.exists(p)), None)

from app.memunote import photo  # noqa: E402
from app.memunote.formats import images, psd  # noqa: E402

def _png(color=(255, 0, 0, 255), size=(16, 12)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()

class PsdTest(unittest.TestCase):
    def _doc(self):
        layers = [{"name": "Фон", "png": _png((255, 0, 0, 255), (20, 16))},
                  {"type": "group", "name": "Группа", "opacity": 0.5, "open": False, "children": [
                      {"name": "Синий", "png": _png((0, 0, 255, 128), (10, 8)), "x": 3, "y": 4, "blend": "multiply", "visible": False,
                       "mask": {"x": 3, "y": 4, "png": _png((255, 255, 255, 255), (10, 8))}}]}]
        return psd.write_psd(20, 16, layers, _png((200, 10, 10, 255), (20, 16)))

    def test_roundtrip_layers_groups_masks_names(self) -> None:
        doc = psd.read_psd(self._doc())
        self.assertEqual((doc["width"], doc["height"]), (20, 16))
        bg, grp = doc["layers"]
        self.assertEqual(bg["name"], "Фон")
        self.assertEqual(grp["type"], "group")
        self.assertEqual(grp["name"], "Группа")
        self.assertFalse(grp["open"])
        self.assertAlmostEqual(grp["opacity"], 0.5, places=2)
        child = grp["children"][0]
        self.assertEqual((child["name"], child["x"], child["y"], child["w"], child["h"]), ("Синий", 3, 4, 10, 8))
        self.assertEqual(child["blend"], "multiply")
        self.assertFalse(child["visible"])
        self.assertIn("mask", child)
        from PIL import Image
        px = Image.open(io.BytesIO(child["png"])).getpixel((0, 0))
        self.assertEqual(px, (0, 0, 255, 128))
        self.assertIsNotNone(doc["merged"])

    def test_packbits_roundtrip_and_flat_layers_are_rle(self) -> None:
        import random
        rnd = random.Random(7)
        for n in list(range(1, 300)) + [6000]:
            for row in (bytes(rnd.choice([0, 0, 0, 9, 255, rnd.randint(0, 255)]) for _ in range(n)), b"z" * n, bytes(rnd.randint(0, 255) for _ in range(n))):
                self.assertEqual(psd.unpackbits(psd.packbits_row(row), n), row)
        from PIL import Image, ImageDraw
        flat = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
        ImageDraw.Draw(flat).rectangle((100, 100, 500, 300), fill=(10, 200, 30, 255))
        buf = io.BytesIO(); flat.save(buf, "PNG")
        data = psd.write_psd(800, 600, [{"name": "Плашка", "png": buf.getvalue()}], buf.getvalue())
        self.assertLess(len(data), 800 * 600 * 4 // 10)
        doc = psd.read_psd(data)
        px = Image.open(io.BytesIO(doc["layers"][0]["png"]))
        self.assertEqual((px.getpixel((200, 200)), px.getpixel((700, 500))), ((10, 200, 30, 255), (0, 0, 0, 0)))
        merged = Image.open(io.BytesIO(data)); merged.load()
        self.assertEqual(merged.convert("RGBA").getpixel((200, 200)), (10, 200, 30, 255))

    def test_merged_image_and_pillow_reads_composite(self) -> None:
        from PIL import Image
        im = Image.open(io.BytesIO(self._doc()))
        self.assertEqual(im.size, (20, 16))
        self.assertEqual(im.getpixel((0, 0))[:3], (200, 10, 10))

    def test_psd_tools_compatibility(self) -> None:
        try:
            from psd_tools import PSDImage
        except ImportError:
            self.skipTest("psd-tools not installed (optional cross-check)")
        p = PSDImage.open(io.BytesIO(self._doc()))
        kinds = [(l.kind, l.name) for l in p.descendants()]
        self.assertEqual(kinds, [("pixel", "Фон"), ("group", "Группа"), ("pixel", "Синий")])
        blue = list(p.descendants())[2]
        self.assertTrue(blue.has_mask())
        self.assertEqual(str(blue.blend_mode), "BlendMode.MULTIPLY")

    def test_packbits_and_bad_files(self) -> None:
        self.assertEqual(psd.unpackbits(bytes([2, 1, 2, 3, 254, 7]), 6), b"\x01\x02\x03\x07\x07\x07")
        with self.assertRaises(ValueError):
            psd.read_psd(b"NOPE" + b"\x00" * 40)
        with self.assertRaises(ValueError):
            psd.write_psd(0, 10, [], None)

class ImagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r32photo-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_encoder_produces_a_readable_file(self) -> None:
        from PIL import Image
        src = _png((10, 200, 30, 128), (40, 30))
        for fmt in images.capabilities()["encoders"]:
            data = images.encode(src, fmt, 80)
            if fmt == "pdf":
                self.assertTrue(data.startswith(b"%PDF-"))
                continue
            im = Image.open(io.BytesIO(data))
            expect = {"jpeg": "JPEG", "tiff": "TIFF", "ico": "ICO", "gif": "GIF", "tga": "TGA", "bmp": "BMP", "webp": "WEBP", "avif": "AVIF", "png": "PNG"}[fmt]
            self.assertEqual(im.format, expect, fmt)
        with self.assertRaises(images.ImageFormatError):
            images.encode(src, "xyz")

    def test_jpeg_flattens_on_background_and_png_keeps_alpha(self) -> None:
        from PIL import Image
        src = _png((0, 0, 0, 0), (8, 8))
        jpg = Image.open(io.BytesIO(images.encode(src, "jpeg", 95, "#00ff00")))
        r, g, b = jpg.getpixel((4, 4))
        self.assertTrue(g > 200 and r < 60 and b < 60)
        self.assertEqual(Image.open(io.BytesIO(images.encode(src, "png"))).getpixel((4, 4))[3], 0)

    def test_decode_tiff_ico_gif_frames_and_heic_message(self) -> None:
        from PIL import Image
        Image.new("RGB", (30, 20), (1, 2, 3)).save(self.tmp / "a.tif")
        Image.new("RGB", (64, 64), (9, 9, 9)).save(self.tmp / "a.ico", sizes=[(16, 16), (48, 48)])
        frames = [Image.new("RGB", (10, 10), c) for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]]
        frames[0].save(self.tmp / "a.gif", save_all=True, append_images=frames[1:], duration=50)
        t = images.decode(str(self.tmp / "a.tif"))
        self.assertEqual((t["format"], t["width"], t["height"]), ("TIFF", 30, 20))
        ico = images.decode(str(self.tmp / "a.ico"))
        self.assertEqual((ico["width"], ico["frames"]), (48, 2))
        g = images.frames(str(self.tmp / "a.gif"))
        self.assertEqual(g["total"], 3)
        third = Image.open(io.BytesIO(base64.b64decode(g["frames"][2]["png"]))).convert("RGB")
        self.assertEqual(third.getpixel((5, 5)), (0, 0, 255))
        (self.tmp / "x.heic").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        if not images.capabilities()["heif"]:
            with self.assertRaises(images.ImageFormatError) as ctx:
                images.decode(str(self.tmp / "x.heic"))
            self.assertEqual(ctx.exception.code, "heic")

    def test_psd_decode_gives_layers(self) -> None:
        data = psd.write_psd(8, 8, [{"name": "A", "png": _png((1, 2, 3, 255), (8, 8))}, {"name": "B", "png": _png((9, 9, 9, 255), (4, 4)), "x": 2, "y": 2}], _png((1, 2, 3, 255), (8, 8)))
        (self.tmp / "l.psd").write_bytes(data)
        d = images.decode(str(self.tmp / "l.psd"))
        self.assertEqual([l["name"] for l in d["layers"]], ["A", "B"])
        self.assertIsInstance(d["layers"][1]["png"], str)

class PromptsTest(unittest.TestCase):
    IMG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 30).decode()

    def test_vision_message_carries_image_edits_and_language(self) -> None:
        msgs = photo.build_messages({"action": "describe", "image": self.IMG, "width": 640, "height": 400, "doc_width": 1600, "doc_height": 1000,
                                     "edits": ["Кисть", "Уровни (Гамма 1.2)"], "layers": ["Фон [raster]"], "name": "x.jpg"}, "ru")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Уровни (Гамма 1.2)", msgs[0]["content"])
        self.assertIn("1600×1000", msgs[0]["content"])
        self.assertIn("Russian", msgs[0]["content"])
        parts = msgs[-1]["content"]
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg"))

    def test_ask_keeps_history_and_requires_a_question(self) -> None:
        msgs = photo.build_messages({"action": "ask", "image": self.IMG, "question": "Какого цвета мяч?",
                                     "history": [{"role": "user", "text": "Привет"}, {"role": "assistant", "text": "Здравствуйте"}]}, "en")
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user"])
        with self.assertRaises(ValueError):
            photo.build_messages({"action": "ask", "image": self.IMG}, "ru")
        with self.assertRaises(ValueError):
            photo.build_messages({"action": "describe", "image": "http://x/y.png"}, "ru")
        with self.assertRaises(ValueError):
            photo.build_messages({"action": "rm -rf", "image": self.IMG}, "ru")

    def test_command_prompt_lists_operations(self) -> None:
        sys_prompt = photo.build_messages({"action": "command", "image": self.IMG, "question": "теплее"}, "ru")[0]["content"]
        for word in ("temperature", "remove_background", "blur_background", "crop", "0..1000"):
            self.assertIn(word, sys_prompt)

    def test_parse_json_answer_tolerates_fences_and_prose(self) -> None:
        self.assertEqual(photo.parse_json_answer('```json\n{"reply": "ok", "ops": []}\n```')["reply"], "ok")
        self.assertEqual(photo.parse_json_answer('Вот: {"a": {"b": "}"}} и всё')["a"]["b"], "}")
        self.assertIsNone(photo.parse_json_answer("no json here"))
        self.assertEqual(photo.parse_json_answer('{bad} {"ok": 1}')["ok"], 1)

    def test_pick_models_prefers_vision(self) -> None:
        st = {"models": [{"id": "t", "vision": False}, {"id": "v", "vision": True}], "default_model": "t"}
        self.assertEqual(photo.pick_models(st)["model"], "v")
        self.assertEqual(photo.pick_models(st, "t")["model"], "t")
        self.assertFalse(photo.pick_models(st, "t")["chosen_vision"])

NODE_PIXELS = r"""
import * as P from "PIXELS";
const ok = (c, m) => { if (!c) { console.error("FAIL " + m); process.exitCode = 1; } };
const img = (w, h, rgba) => P.makeImage(w, h, rgba);
const px = (im, x, y) => Array.from(im.data.slice((y * im.width + x) * 4, (y * im.width + x) * 4 + 4));
let a = img(4, 4, [10, 100, 200, 255]);
ok(px(P.ADJUST.invert(a, {}), 0, 0).join() === "245,155,55,255", "invert");
ok(px(P.ADJUST.threshold(a, { level: 128 }), 1, 1).join() === "0,0,0,255", "threshold");
ok(px(P.ADJUST.posterize(img(1, 1, [100, 100, 100, 255]), { levels: 2 }), 0, 0)[0] === 0, "posterize");
ok(px(P.ADJUST.levels(img(1, 1, [128, 128, 128, 255]), { inBlack: 0, inWhite: 128 }), 0, 0)[0] === 255, "levels white point");
ok(px(P.ADJUST.brightnessContrast(img(1, 1, [100, 100, 100, 255]), { brightness: 20, contrast: 0 }), 0, 0)[0] === 130, "brightness");
ok(px(P.ADJUST.curves(a, { points: [[0, 0], [255, 255]] }), 0, 0).join() === "10,100,200,255", "identity curve");
const lut = P.curveLut([[0, 0], [64, 40], [192, 220], [255, 255]]); let mono = true; for (let i = 1; i < 256; i++) if (lut[i] < lut[i - 1]) mono = false; ok(mono, "monotone curve");
ok(px(P.ADJUST.grayscale(img(1, 1, [255, 0, 0, 255]), {}), 0, 0)[0] === 76, "luma");
const hs = px(P.ADJUST.hueSaturation(img(1, 1, [255, 0, 0, 255]), { hue: 120 }), 0, 0); ok(hs[1] > 250 && hs[0] < 5, "hue +120 red→green " + hs);
const warm = px(P.ADJUST.temperature(img(1, 1, [128, 128, 128, 255]), { temperature: 50 }), 0, 0); ok(warm[0] > warm[2], "warmer");
// selection mask limits an adjustment
const mask = new Uint8Array(16); mask[0] = 255;
const inv = P.ADJUST.invert(a, {}, mask); ok(px(inv, 0, 0)[0] === 245 && px(inv, 1, 0)[0] === 10, "mask");
// blur keeps a flat image flat, alpha edges do not darken
const flat = P.FILTER.gaussianBlur(img(30, 20, [50, 60, 70, 255]), { radius: 6 }); ok(px(flat, 15, 10).join() === "50,60,70,255", "flat blur");
const half = img(20, 1, [255, 255, 255, 255]); for (let x = 10; x < 20; x++) half.data[x * 4 + 3] = 0;
const hb = P.FILTER.gaussianBlur(half, { radius: 4 }); ok(px(hb, 9, 0)[0] >= 250, "premultiplied blur keeps colour " + px(hb, 9, 0));
ok(px(P.FILTER.pixelate(img(4, 4, [9, 9, 9, 255]), { size: 2 }), 3, 3)[0] === 9, "pixelate");
const sharp = P.FILTER.unsharpMask(img(10, 10, [100, 100, 100, 255]), { amount: 100, radius: 2 }); ok(px(sharp, 5, 5)[0] === 100, "unsharp flat");
// blend maths (W3C): multiply / screen / normal / opacity
ok(P.blendPixel("multiply", [255, 128, 0, 255], [128, 128, 128, 255]).join() === "128,64,0,255", "multiply");
ok(P.blendPixel("screen", [0, 128, 255, 255], [128, 128, 128, 255]).join() === "128,192,255,255", "screen");
ok(P.blendPixel("normal", [0, 0, 0, 255], [255, 255, 255, 255], 0.5).join() === "128,128,128,255", "opacity");
ok(P.blendPixel("normal", [0, 0, 0, 0], [255, 0, 0, 128]).join() === "255,0,0,128", "over transparent");
ok(P.blendPixel("difference", [200, 50, 0, 255], [50, 50, 50, 255]).join() === "150,0,50,255", "difference");
ok(P.BLEND_MODES.length === 16, "16 modes");
// selection: magic wand, colour range, grow/shrink/feather, bounds
const s = img(20, 10, [0, 0, 255, 255]); for (let y = 2; y < 6; y++) for (let x = 3; x < 9; x++) { const i = (y * 20 + x) * 4; s.data[i] = 255; s.data[i + 2] = 0; }
const w = P.magicWand(s, 4, 3, { tolerance: 10 }); ok(P.maskArea(w) === 24, "wand area " + P.maskArea(w));
const b = P.maskBounds(w, 20, 10); ok(b.x === 3 && b.y === 2 && b.w === 6 && b.h === 4, "bounds");
ok(P.maskArea(P.growMask(w, 20, 10, 1)) === 8 * 6, "grow");
ok(P.maskArea(P.growMask(w, 20, 10, -1)) === 4 * 2, "shrink");
ok(P.maskArea(P.combineMasks(w, P.invertMask(w), "add")) === 200, "add");
ok(P.maskArea(P.combineMasks(w, w, "subtract")) === 0, "subtract");
const cr = P.colorRange(s, [255, 0, 0], 20); ok(cr[3 * 20 + 4] === 255 && cr[0] === 0, "colour range");
// segmentation finds the red object in a box; inpainting fills it from the green surroundings
const sc = img(60, 40, [30, 120, 40, 255]); for (let y = 10; y < 30; y++) for (let x = 20; x < 40; x++) { const i = (y * 60 + x) * 4; sc.data[i] = 230; sc.data[i + 1] = 40; sc.data[i + 2] = 40; }
const seg = P.segment(sc, { x: 12, y: 5, w: 36, h: 30 }); ok(seg[20 * 60 + 30] > 200 && seg[7 * 60 + 14] < 30 && Math.abs(P.maskArea(seg) - 400) < 60, "segment " + P.maskArea(seg));
const filled = P.inpaint(sc, P.magicWand(sc, 30, 20, { tolerance: 20 })); const f = px(filled, 30, 20); ok(Math.abs(f[1] - 120) < 10 && f[0] < 60, "inpaint " + f);
ok(P.palette(sc, 2).includes("#e62828"), "palette");
// colour conversions round-trip
for (const c of [[0, 0, 0], [255, 255, 255], [12, 200, 99], [250, 10, 160]]) { const back = P.hslToRgb(...P.rgbToHsl(...c)); ok(back.every((v, i) => Math.abs(v - c[i]) <= 1), "hsl " + c); const hv = P.hsvToRgb(...P.rgbToHsv(...c)); ok(hv.every((v, i) => Math.abs(v - c[i]) <= 1), "hsv " + c); }
ok(P.parseHex("#abc").join() === "170,187,204,255" && P.toHex(255, 0, 16) === "#ff0010" && P.parseHex("zz") === null, "hex");
// every op runs on a 1×1 transparent pixel and on 3×3 without throwing
for (const op of P.OPS) { P.runOp(op, img(1, 1, [0, 0, 0, 0]), {}); P.runOp(op, img(3, 3, [80, 90, 100, 255]), {}); }
console.log("OK " + P.OPS.length);
"""

@unittest.skipUnless(shutil.which("node"), "node not installed")
class PixelsNodeTest(unittest.TestCase):
    def test_pixels_module(self) -> None:
        code = NODE_PIXELS.replace("PIXELS", (PHOTO_JS / "pixels.js").as_uri())
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(code)
        try:
            out = subprocess.run(["node", fh.name], capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(fh.name)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("OK", out.stdout)

    def test_js_modules_parse(self) -> None:
        for f in list(PHOTO_JS.glob("*.js")) + [STATIC / "js" / "app.js"]:
            out = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, f"{f.name}: {out.stderr}")

class StaticTest(unittest.TestCase):
    def test_i18n_parity_and_english_without_cyrillic(self) -> None:
        text = (PHOTO_JS / "i18n.js").read_text(encoding="utf-8")
        ru_part, en_part = text.split("\n  en: {", 1)
        en_part = en_part.split("\n};", 1)[0]
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", en_part), re.search(r".{30}[А-Яа-яЁё].{10}", en_part))
        key = re.compile(r'(?:"([\w-]+)"|(\w+)):\s*"')
        ru_keys = {a or b for a, b in key.findall(ru_part.split("ru: {", 1)[1])}
        en_keys = {a or b for a, b in key.findall(en_part)}
        self.assertEqual(ru_keys - en_keys, set())
        self.assertEqual(en_keys - ru_keys, set())

    def test_every_used_key_exists(self) -> None:
        text = (PHOTO_JS / "i18n.js").read_text(encoding="utf-8")
        used = set()
        for f in PHOTO_JS.glob("*.js"):
            used |= set(re.findall(r'"(ph_[a-z0-9_]+)"', f.read_text(encoding="utf-8")))
        used = {k for k in used if not k.endswith("_")}
        missing = sorted(k for k in used if f"{k}:" not in text and f'"{k}":' not in text)
        self.assertEqual(missing, [])

    def test_packaging_lists_new_modules(self) -> None:
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        spec = (ROOT / "MemuNote.spec").read_text(encoding="utf-8")
        for mod in ("app.memunote.photo", "app.memunote.formats.images", "app.memunote.formats.psd", "PIL.Image"):
            self.assertIn(f'"{mod}"', build)
            self.assertIn(f"'{mod}'", spec)

    def test_app_hooks_are_surgical(self) -> None:
        app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn('import("./photo/editor.js")', app_js)
        self.assertIn("tab.comp.handleKey(e)", app_js)
        self.assertIn("ph_new_image", app_js)
        server_py = (ROOT / "app" / "memunote" / "server.py").read_text(encoding="utf-8")
        self.assertIn("_photo.register(Handler)", server_py)

    def test_open_with_adds_images_but_never_svg(self) -> None:
        from app.memunote import winsys
        for ext in (".png", ".jpg", ".psd", ".webp"):
            self.assertIn(ext, winsys.SAFE_EXT)
        self.assertNotIn(".svg", winsys.SAFE_EXT)
        self.assertIn(".svg", winsys.NEVER_EXT)

class FakeProxy:
    """/health, /api/admin/pg/state, /api/admin/playground/chat (SSE), /api/admin/pg/image (SSE)."""

    def __init__(self, image_models=True):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, value, status=200):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    return self._json({"service": "memuai-proxy"})
                if self.path == "/api/admin/pg/state":
                    return self._json({"models": [{"id": "fake/text", "label": "Text", "caps": {"vision": False, "chat": True}},
                                                  {"id": "fake/vision", "label": "Vision", "caps": {"vision": True, "chat": True}}],
                                       "image_models": ["fake/painter"] if image_models else []})
                self._json({"error": "nope"}, 404)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
                outer.requests.append((self.path, body))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def ev(obj):
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()

                if self.path == "/api/admin/playground/chat":
                    system = body["messages"][0]["content"]
                    if "turn the user's natural-language edit request" in system:
                        text = '```json\n{"reply": "Сделал теплее", "ops": [{"op": "adjust", "name": "temperature", "params": {"temperature": 30}}, {"op": "evil", "x": 1}]}\n```'
                    elif "Find the object(s)" in system:
                        text = '{"objects": [{"label": "ball", "box": [400, 300, 600, 700]}]}'
                    else:
                        text = "На фото красный мяч на зелёной траве."
                    for i in range(0, len(text), 7):
                        ev({"choices": [{"delta": {"content": text[i:i + 7]}}]})
                    ev({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                    self.wfile.write(b"data: [DONE]\n\n")
                elif self.path == "/api/admin/pg/image":
                    ev({"status": "queued"})
                    ev({"status": "generating", "ms": 500})
                    if "FAIL" in body.get("prompt", ""):
                        ev({"status": "error", "error": "provider refused"})
                    else:
                        ev({"status": "done", "urls": ["data:image/png;base64," + base64.b64encode(_png((0, 128, 255, 255), (32, 32))).decode()]})
                    self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

class _ServerCase(unittest.TestCase):
    proxy_images = True

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r32photo-api-"))
        self.fake = FakeProxy(self.proxy_images)
        self.patch = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": str(self.tmp / "data"), "MEMUAI_PROXY_URL": self.fake.url,
                                                  "NO_PROXY": "127.0.0.1,localhost"})
        self.patch.start()
        from app.memunote import ai, server
        ai._STATUS_CACHE.update(at=0.0, value=None)
        self.server = server
        self.app = server.NoteApp([])
        self.httpd, _ = server.start(self.app)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.fake.stop()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=60)
        hdrs = {"X-MN-Token": self.app.token, "Host": f"127.0.0.1:{self.app.port}"}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def sse(self, path, body):
        status, data = self.call("POST", path, body)
        self.assertEqual(status, 200, data[:300])
        return [json.loads(line[5:]) for line in data.decode().splitlines() if line.startswith("data:")]

class PhotoApiTest(_ServerCase):
    def test_decode_requires_an_opened_file_then_works(self) -> None:
        from PIL import Image
        f = self.tmp / "a.tif"
        Image.new("RGB", (12, 9), (5, 6, 7)).save(f)
        status, _ = self.call("POST", "/api/photo/decode", {"path": str(f)})
        self.assertEqual(status, 403)
        self.app.open_document(str(f))
        status, data = self.call("POST", "/api/photo/decode", {"path": str(f)})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["width"], 12)
        status, _ = self.call("POST", "/api/photo/decode", {"path": str(f)}, headers={"X-MN-Token": "bad"})
        self.assertEqual(status, 401)

    def test_encode_writes_atomically_and_detects_conflicts(self) -> None:
        from PIL import Image
        target = self.tmp / "out.tif"
        q = f"/api/photo/encode?path={urllib.request.quote(str(target))}&format=tiff"
        status, data = self.call("POST", q, _png((1, 2, 3, 255), (5, 5)), {"Content-Type": "image/png"})
        self.assertEqual(status, 200, data)
        self.assertEqual(Image.open(target).format, "TIFF")
        mtime = json.loads(data)["mtime"]
        os.utime(target, (time.time() + 60, time.time() + 60))
        status, data = self.call("POST", q + f"&mtime={mtime}", _png(), {"Content-Type": "image/png"})
        self.assertTrue(json.loads(data)["conflict"])
        status, _ = self.call("POST", q, b"not a png")
        self.assertEqual(status, 400)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("out")), ["out.tif"])

    def test_psd_route(self) -> None:
        target = self.tmp / "l.psd"
        b64 = base64.b64encode(_png((0, 255, 0, 255), (6, 4))).decode()
        status, data = self.call("POST", "/api/photo/psd", {"path": str(target), "width": 6, "height": 4, "merged": b64,
                                                            "layers": [{"name": "Слой", "png": b64, "opacity": 0.7, "blend": "overlay"}]})
        self.assertEqual(status, 200, data)
        doc = psd.read_psd(target.read_bytes())
        self.assertEqual(doc["layers"][0]["name"], "Слой")
        self.assertEqual(doc["layers"][0]["blend"], "overlay")

    def test_caps_report_vision_and_image_models(self) -> None:
        status, data = self.call("GET", "/api/photo/caps")
        caps = json.loads(data)
        self.assertTrue(caps["online"])
        self.assertEqual(caps["model"], "fake/vision")
        self.assertEqual(caps["vision_models"], ["fake/vision"])
        self.assertEqual([m["id"] for m in caps["image_models"]], ["fake/painter"])
        self.assertTrue(caps["codecs"]["pillow"])

    def test_describe_streams_and_sends_the_image(self) -> None:
        img = "data:image/png;base64," + base64.b64encode(_png((255, 0, 0, 255), (20, 20))).decode()
        events = self.sse("/api/photo/ai", {"action": "describe", "image": img, "width": 20, "height": 20, "edits": ["Кисть"]})
        self.assertEqual(events[0]["t"], "start")
        self.assertEqual(events[0]["model"], "fake/vision")
        text = "".join(e.get("text", "") for e in events if e["t"] == "delta")
        self.assertIn("красный мяч", text)
        self.assertEqual(events[-1]["t"], "done")
        path, body = self.fake.requests[-1]
        self.assertEqual(path, "/api/admin/playground/chat")
        self.assertEqual(body["model"], "fake/vision")
        self.assertEqual(body["messages"][-1]["content"][1]["image_url"]["url"], img)
        self.assertIn("Кисть", body["messages"][0]["content"])

    def test_command_returns_parsed_ops(self) -> None:
        img = "data:image/png;base64," + base64.b64encode(_png()).decode()
        events = self.sse("/api/photo/ai", {"action": "command", "image": img, "question": "сделай теплее"})
        result = next(e for e in events if e["t"] == "result")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["ops"][0]["name"], "temperature")
        self.assertFalse([e for e in events if e["t"] == "delta"])
        self.assertEqual(self.fake.requests[-1][1]["temperature"], 0.2)

    def test_generate_returns_data_urls_and_errors(self) -> None:
        events = self.sse("/api/photo/generate", {"prompt": "кот в космосе"})
        done = next(e for e in events if e["t"] == "done")
        self.assertTrue(done["images"][0].startswith("data:image/png;base64,"))
        self.assertEqual(self.fake.requests[-1][1]["model"], "fake/painter")
        events = self.sse("/api/photo/generate", {"prompt": "FAIL please", "model": "fake/painter"})
        self.assertEqual(next(e for e in events if e["t"] == "error")["message"], "provider refused")

    def test_ai_offline(self) -> None:
        self.fake.stop()
        from app.memunote import ai
        ai._STATUS_CACHE.update(at=0.0, value=None)
        img = "data:image/png;base64," + base64.b64encode(_png()).decode()
        status, data = self.call("POST", "/api/photo/ai", {"action": "describe", "image": img})
        self.assertEqual(status, 503)
        status, data = self.call("GET", "/api/photo/caps")
        self.assertFalse(json.loads(data)["online"])
        self.fake = FakeProxy()

class PhotoNoImageModelTest(_ServerCase):
    proxy_images = False

    def test_generate_without_image_model(self) -> None:
        status, data = self.call("POST", "/api/photo/generate", {"prompt": "x"})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["kind"], "no_image_model")

BROWSER_CHECKS = r"""
async () => {
  const R = {};
  const P = await import('./js/photo/pixels.js');
  const D = await import('./js/photo/doc.js');
  const io = await import('./js/photo/io.js');
  const ed = MN.tabs.find(t => t.kind === 'image').comp;
  await ed.ready;
  const doc = ed.doc;
  const pix = (c, x, y) => Array.from(c.getContext('2d').getImageData(x, y, 1, 1).data);
  R.size = [doc.width, doc.height];
  R.before = pix(doc.composite(), 10, 10);
  // --- blend modes: canvas compositing == W3C maths (±2)
  const t = new D.PhotoDoc(2, 1);
  const bottom = t.makeLayer({ name: 'b' }); const top = t.makeLayer({ name: 't' });
  const bx = bottom.canvas.getContext('2d'); bx.fillStyle = 'rgb(200,120,40)'; bx.fillRect(0, 0, 2, 1);
  t.layers.push(bottom, top);
  const tx = top.canvas.getContext('2d'); tx.fillStyle = 'rgba(60,160,220,1)'; tx.fillRect(0, 0, 2, 1);
  R.blend = {};
  for (const mode of P.BLEND_MODES) {
    top.blend = mode; top.opacity = 0.75; t.compDirty = true;
    const got = pix(t.composite(), 0, 0), want = P.blendPixel(mode, [200, 120, 40, 255], [60, 160, 220, 255], 0.75);
    R.blend[mode] = got.every((v, i) => Math.abs(v - want[i]) <= 3) ? 'ok' : `${got} vs ${want}`;
  }
  // --- masks and groups
  top.blend = 'normal'; top.opacity = 1; top.mask = D.newCanvas(2, 1); top.mask.getContext('2d').fillStyle = '#fff'; top.mask.getContext('2d').fillRect(1, 0, 1, 1); t.compDirty = true;
  R.mask = [pix(t.composite(), 0, 0).slice(0, 3).join(), pix(t.composite(), 1, 0).slice(0, 3).join()];
  // --- undo / redo of a pixel patch is exact
  const l = doc.active; const before = l.canvas.getContext('2d').getImageData(0, 0, doc.width, doc.height).data.slice();
  doc.begin('paint'); doc.touch(l.canvas, { x: 5, y: 5, w: 30, h: 30 }); const lx = l.canvas.getContext('2d'); lx.fillStyle = '#00ff00'; lx.fillRect(5, 5, 30, 30); doc.commit();
  R.painted = pix(l.canvas, 10, 10).join();
  doc.undo();
  const after = l.canvas.getContext('2d').getImageData(0, 0, doc.width, doc.height).data;
  R.undoExact = before.every((v, i) => v === after[i]);
  doc.redo(); R.redo = pix(doc.active.canvas, 10, 10).join(); doc.undo();
  // --- adjustments through the worker + history label
  await ed.cmd('newLayer'); await ed.cmd('deleteLayer');
  doc.activeId = doc.layers[0].id;
  const okInv = await (await import('./js/photo/commands.js')).COMMANDS.applyOp(ed, 'invert', {});
  R.invert = [okInv, pix(doc.composite(), 10, 10).slice(0, 3).join(), doc.history[doc.history.length - 1].label];
  doc.undo();
  R.afterUndo = pix(doc.composite(), 10, 10).join() === R.before.join();
  // --- text + shape layers survive .memuimg round trip; composite identical
  doc.begin('t'); const tl = doc.insertLayer(doc.makeLayer({ type: 'text', name: 'Текст', text: { ...D.DEFAULT_TEXT, text: 'Привет', size: 30, color: '#ff00ff', x: 10, y: 10 } })); D.renderVector(doc, tl);
  const sl = doc.insertLayer(doc.makeLayer({ type: 'shape', name: 'Фигура', shape: { ...D.DEFAULT_SHAPE, x: 40, y: 40, w: 30, h: 20, fill: '#0000ff' } })); D.renderVector(doc, sl); doc.commit();
  const blob = await io.writeMemuimg(doc);
  const back = await io.readMemuimg(await blob.arrayBuffer());
  R.memuimg = { layers: back.flat().map(x => x.type + ':' + x.name), text: back.flat().find(x => x.type === 'text')?.text.text };
  const c1 = doc.flatten().getContext('2d').getImageData(0, 0, doc.width, doc.height).data, c2 = back.flatten().getContext('2d').getImageData(0, 0, doc.width, doc.height).data;
  let diff = 0; for (let i = 0; i < c1.length; i++) diff = Math.max(diff, Math.abs(c1[i] - c2[i])); R.memuimgDiff = diff;
  // --- SVG export keeps text and shapes as vector elements
  const svg = await (await io.writeSvg(doc)).text();
  R.svg = [svg.includes('<text'), svg.includes('Привет'), svg.includes('<rect'), svg.includes('<image')];
  // --- crop / image size / rotate keep vectors vector and update the size
  ed.cmd('cropToSelection');
  (await import('./js/photo/commands.js')).COMMANDS.cropTo(ed, { x: 5, y: 5, w: 60, h: 50 });
  R.crop = [doc.width, doc.height, doc.flat().find(x => x.type === 'text').text.x];
  (await import('./js/photo/commands.js')).COMMANDS.resizeImage(ed, 120, 100);
  R.resize = [doc.width, doc.height, doc.flat().find(x => x.type === 'text').text.size];
  doc.undo(); doc.undo();
  R.restored = [doc.width, doc.height];
  // --- selection → layer via copy, merge down, group/ungroup
  const m = new Uint8Array(doc.width * doc.height); for (let y = 0; y < 20; y++) for (let x = 0; x < 20; x++) m[y * doc.width + x] = 255;
  ed.select(m, 'new', 'sel'); doc.activeId = doc.layers[0].id; ed.cmd('layerViaCopy');
  R.viaCopy = [doc.flat().length, pix(doc.active.canvas, 30, 30)[3]];
  ed.cmd('groupLayers'); R.grouped = doc.active.type; ed.cmd('ungroup');
  ed.cmd('mergeDown'); R.merged = doc.flat().length;
  R.historyCount = doc.history.length;
  // --- PNG / JPEG / PSD export through the page
  R.saves = {};
  for (const [f, ext] of [['png', 'png'], ['jpeg', 'jpg'], ['psd', 'psd'], ['webp', 'webp'], ['tiff', 'tif']]) {
    const r = await io.saveDocument(doc, OUT + '/x.' + ext, f, { force: true, quality: 85 }); R.saves[f] = r.ok;
  }
  return R;
}
"""

@unittest.skipUnless(CHROME, "headless Chromium is not available")
class BrowserTest(_ServerCase):
    def _page(self, p, files, size=(1280, 800)):
        for f in files:
            self.app.allow(f)
        self.app.initial_files = list(files)
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-gpu"])
        pg = b.new_page(viewport={"width": size[0], "height": size[1]})
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{self.app.port}/index.html#t={self.app.token}")
        return b, pg

    def _wait_doc(self, pg):
        for _ in range(100):
            if pg.evaluate("!!(window.MN && MN.tabs.some(t => t.kind === 'image' && t.comp && t.comp.doc))"):
                return
            time.sleep(0.1)
        self.fail("photo editor did not load: " + "; ".join(self.errors))

    def test_editor_core_in_chromium(self) -> None:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        src = self.tmp / "photo.png"
        Image.new("RGB", (160, 120), (30, 90, 200)).save(src)
        out = self.tmp / "out"
        out.mkdir()
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                self._wait_doc(pg)
                r = pg.evaluate(BROWSER_CHECKS.replace("OUT", json.dumps(str(out))))
            finally:
                b.close()
        self.assertEqual(r["size"], [160, 120])
        self.assertEqual({k: v for k, v in r["blend"].items() if v != "ok"}, {})
        self.assertEqual(r["mask"][0], "200,120,40")
        self.assertEqual(r["mask"][1], "60,160,220")
        self.assertEqual(r["painted"], "0,255,0,255")
        self.assertTrue(r["undoExact"])
        self.assertEqual(r["redo"], "0,255,0,255")
        self.assertTrue(r["invert"][0])
        self.assertEqual(r["invert"][1], "225,165,55")
        self.assertIn("Инверсия", r["invert"][2])
        self.assertTrue(r["afterUndo"])
        self.assertEqual(r["memuimg"]["text"], "Привет")
        self.assertIn("text:Текст", r["memuimg"]["layers"])
        self.assertIn("shape:Фигура", r["memuimg"]["layers"])
        self.assertLessEqual(r["memuimgDiff"], 2)
        self.assertEqual(r["svg"], [True, True, True, True])
        self.assertEqual(r["crop"], [60, 50, 5])
        self.assertEqual(r["resize"][:2], [120, 100])
        self.assertEqual(r["restored"], [160, 120])
        self.assertEqual(r["viaCopy"][1], 0)
        self.assertEqual(r["grouped"], "group")
        self.assertTrue(all(r["saves"].values()), r["saves"])
        self.assertEqual(Image.open(out / "x.jpg").format, "JPEG")
        self.assertEqual(Image.open(out / "x.tif").format, "TIFF")
        self.assertEqual(len(psd.read_psd((out / "x.psd").read_bytes())["layers"]) >= 1, True)
        self.assertEqual(self.errors, [])

    def test_ui_tools_shortcuts_and_ai_panel(self) -> None:
        from playwright.sync_api import sync_playwright
        from PIL import Image, ImageDraw
        src = self.tmp / "ball.png"
        im = Image.new("RGB", (400, 300), (40, 140, 60))
        ImageDraw.Draw(im).ellipse((150, 100, 250, 200), fill=(230, 40, 40))
        im.save(src)
        ED = "MN.tabs.find(t => t.kind === 'image').comp"
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                self._wait_doc(pg)
                time.sleep(0.5)
                box = pg.locator(".ph-view").bounding_box()
                at = lambda x, y: (box["x"] + pg.evaluate(f"{ED}.view.panX") + x * pg.evaluate(f"{ED}.view.zoom"),
                                   box["y"] + pg.evaluate(f"{ED}.view.panY") + y * pg.evaluate(f"{ED}.view.zoom"))

                pg.keyboard.press("b")
                self.assertEqual(pg.evaluate(f"{ED}.toolId"), "brush")
                pg.mouse.move(*at(20, 20)); pg.mouse.down(); pg.mouse.move(*at(120, 40), steps=10); pg.mouse.up()
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), 1)
                pg.keyboard.press("Control+z")
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), 0)

                pg.evaluate(f"{ED}.setTool('wand')")
                pg.mouse.click(*at(200, 150))
                time.sleep(0.5)
                area = pg.evaluate(f"(() => {{ const m = {ED}.doc.selection; let n = 0; for (const v of m) n += v > 127; return n; }})()")
                self.assertTrue(6500 < area < 9000, area)

                pg.keyboard.press("Control+j")
                self.assertEqual(pg.evaluate(f"{ED}.doc.flat().length"), 2)
                pg.keyboard.press("Control+d")
                self.assertFalse(pg.evaluate(f"!!{ED}.doc.selection"))

                pg.keyboard.press("t")
                pg.mouse.click(*at(30, 250)); time.sleep(0.2)
                pg.keyboard.type("Съешь ещё")
                pg.keyboard.press("Control+Enter")
                self.assertIn("Съешь ещё", pg.evaluate(f"{ED}.doc.flat().filter(l => l.type === 'text').map(l => l.text.text).join()"))

                pg.keyboard.press("Control+a")
                pg.evaluate(f"{ED}.doc.activeId = {ED}.doc.layers[0].id")
                pg.keyboard.press("Control+l"); time.sleep(0.5)
                self.assertEqual(pg.locator(".ph-dlg").count(), 1)
                pg.keyboard.press("Escape"); time.sleep(0.2)
                self.assertEqual(pg.locator(".ph-dlg").count(), 0)
                self.assertTrue(pg.evaluate(f"!!{ED}.doc.selection"))

                pg.evaluate(f"{ED}.showTab('ai')"); time.sleep(0.8)
                pg.locator(".pai-act[data-act=describe]").click()
                pg.wait_for_selector(".pai-msg.ai:not(.pending) .pai-btns", timeout=15000)
                self.assertIn("красный мяч", pg.locator(".pai-log").inner_text())
                n0 = pg.evaluate(f"{ED}.doc.history.length")
                pg.locator(".pai-modes [data-mode=edit]").click()
                pg.locator(".pai-q").fill("сделай теплее"); pg.keyboard.press("Enter")
                for _ in range(80):
                    if pg.evaluate(f"{ED}.doc.history.length") > n0:
                        break
                    time.sleep(0.1)
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), n0 + 1)
                self.assertIn("ИИ", pg.evaluate(f"{ED}.doc.history[{ED}.doc.history.length - 1].label"))
                self.assertIn("Тёплый", pg.locator(".pai-log").inner_text())

                pg.evaluate(f"{ED}.ai.locate('мяч').then(async (b) => {{ window.__boxes = b; }})")
                for _ in range(60):
                    if pg.evaluate("!!window.__boxes"):
                        break
                    time.sleep(0.1)
                boxes = pg.evaluate("window.__boxes.map(b => [Math.round(b.r.x), Math.round(b.r.y), Math.round(b.r.w), Math.round(b.r.h)])")
                self.assertEqual(boxes, [[160, 90, 80, 120]])

                pg.locator(".pai-modes [data-mode=gen]").click()
                pg.locator(".pai-q").fill("кот"); pg.keyboard.press("Enter")
                pg.wait_for_selector(".pai-gen img", timeout=15000)
                layers0 = pg.evaluate(f"{ED}.doc.flat().length")
                pg.locator(".pai-gen [data-mact^=layer]").first.click(); time.sleep(0.5)
                self.assertEqual(pg.evaluate(f"{ED}.doc.flat().length"), layers0 + 1)
            finally:
                b.close()
        self.assertEqual(self.errors, [])

    def test_clone_stamp_and_crop_frame_with_the_mouse(self) -> None:
        """Alt+click sets the clone source, the stroke copies pixels as ONE «Штамп» step; a drag inside the untouched
        full-canvas crop frame draws a new frame (it used to move the whole frame instead)."""
        from playwright.sync_api import sync_playwright
        from PIL import Image, ImageDraw
        src = self.tmp / "halves.png"
        im = Image.new("RGB", (1000, 700), (20, 40, 220))
        ImageDraw.Draw(im).rectangle((0, 0, 499, 699), fill=(220, 30, 30))
        im.save(src)
        ED = "MN.tabs.find(t => t.kind === 'image').comp"
        px = lambda x, y: pg.evaluate(f"Array.from({ED}.doc.layers[0].canvas.getContext('2d').getImageData({x}, {y}, 1, 1).data).slice(0, 3).join()")
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                self._wait_doc(pg)
                time.sleep(0.5)
                box = pg.locator(".ph-view").bounding_box()
                at = lambda x, y: (box["x"] + pg.evaluate(f"{ED}.view.panX") + x * pg.evaluate(f"{ED}.view.zoom"),
                                   box["y"] + pg.evaluate(f"{ED}.view.panY") + y * pg.evaluate(f"{ED}.view.zoom"))
                for pt in (at(0, 0), at(1000, 700)):
                    self.assertTrue(box["x"] <= pt[0] <= box["x"] + box["width"] and box["y"] <= pt[1] <= box["y"] + box["height"], (pt, box))
                self.assertEqual(px(750, 300), "20,40,220")
                pg.keyboard.press("s")
                self.assertEqual(pg.evaluate(f"{ED}.toolId"), "clone")
                pg.evaluate(f"{ED}.opts.clone.opacity = 100; {ED}.opts.clone.hardness = 100; {ED}.opts.clone.size = 40")

                pg.mouse.move(*at(700, 300)); pg.mouse.down(); pg.mouse.move(*at(720, 300), steps=3); pg.mouse.up()
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), 0)
                pg.keyboard.down("Alt"); pg.mouse.click(*at(100, 350)); pg.keyboard.up("Alt")
                self.assertEqual(pg.evaluate(f"{ED}.toolId"), "clone")
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), 0)
                pg.mouse.move(*at(700, 300)); pg.mouse.down(); pg.mouse.move(*at(800, 300), steps=10); pg.mouse.up()
                time.sleep(0.3)
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.map(e => e.label)"), ["Штамп"])
                self.assertEqual(px(750, 300), "220,30,30")
                self.assertEqual(px(750, 400), "20,40,220")
                pg.keyboard.press("Control+z")
                self.assertEqual(px(750, 300), "20,40,220")

                pg.keyboard.press("c")
                self.assertEqual(pg.evaluate(f"{ED}.toolId"), "crop")
                pg.mouse.move(*at(100, 50)); pg.mouse.down(); pg.mouse.move(*at(900, 650), steps=10); pg.mouse.up()
                frame = pg.evaluate(f"(async () => {{ const b = (await import('./js/photo/tools.js')).TOOLS.crop.box; return [b.x, b.y, b.w, b.h].map(Math.round); }})()")
                self.assertTrue(abs(frame[0] - 100) <= 2 and abs(frame[1] - 50) <= 2 and abs(frame[2] - 800) <= 3 and abs(frame[3] - 600) <= 3, frame)
                pg.evaluate(f"(async () => {{ const T = (await import('./js/photo/tools.js')).TOOLS; T.crop.box = {{x: 100, y: 50, w: 800, h: 600}}; }})()")
                pg.keyboard.press("Enter"); time.sleep(0.5)
                self.assertEqual(pg.evaluate(f"[{ED}.doc.width, {ED}.doc.height]"), [800, 600])
                self.assertEqual(px(0, 0), "220,30,30")
                self.assertEqual(px(450, 0), "20,40,220")
            finally:
                b.close()
        self.assertEqual(self.errors, [])

    def test_partial_composite_view_cache_and_pan_limits(self) -> None:
        """Brush dabs recomposite only their rectangle (blend modes + masks above must still be exact), the screen cache
        is refreshed after every change, a wheel pan never loses the image, the narrow menu bar scrolls with the wheel."""
        from playwright.sync_api import sync_playwright
        from PIL import Image
        src = self.tmp / "grad.png"
        im = Image.new("RGB", (600, 400))
        im.putdata([(x * 255 // 600, y * 255 // 400, 128) for y in range(400) for x in range(600)])
        im.save(src)
        ED = "MN.tabs.find(t => t.kind === 'image').comp"
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                self._wait_doc(pg)
                time.sleep(0.5)

                pg.evaluate(f"""(() => {{ const e = {ED}; e.cmd('duplicateLayer'); e.cmd('setLayerProp', 'blend', 'multiply'); e.cmd('setLayerProp', 'opacity', 0.7);
                  e.cmd('addMask', 'reveal'); const l = e.doc.active; l.mask.getContext('2d').clearRect(0, 0, 300, 400);
                  e.doc.target = 'layer'; e.doc.activeId = e.doc.layers[0].id; e.layerChanged(); }})()""")
                box = pg.locator(".ph-view").bounding_box()
                at = lambda x, y: (box["x"] + pg.evaluate(f"{ED}.view.panX") + x * pg.evaluate(f"{ED}.view.zoom"),
                                   box["y"] + pg.evaluate(f"{ED}.view.panY") + y * pg.evaluate(f"{ED}.view.zoom"))
                pg.keyboard.press("b")
                pg.evaluate(f"{ED}.fg = '#ff00ff'; {ED}.opts.brush.size = 30; {ED}.opts.brush.opacity = 100; {ED}.opts.brush.hardness = 100")
                pg.mouse.move(*at(100, 200)); pg.mouse.down(); pg.mouse.move(*at(500, 220), steps=12); pg.mouse.up()
                time.sleep(0.3)
                diff = pg.evaluate(f"""(() => {{ const d = {ED}.doc; const a = d.composite().getContext('2d').getImageData(0, 0, d.width, d.height).data;
                  const b = d.flatten().getContext('2d').getImageData(0, 0, d.width, d.height).data; let m = 0; for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i])); return m; }})()""")
                self.assertLessEqual(diff, 1)

                scr = lambda x, y: pg.evaluate(f"""(() => {{ const e = {ED}, s = e.toScreen({{x: {x}, y: {y}}}); return Array.from(e.ctx.getImageData(Math.round(s.x * e.dpr), Math.round(s.y * e.dpr), 1, 1).data).slice(0, 3); }})()""")
                pg.evaluate(f"{ED}.render()")
                self.assertEqual(scr(150, 205), [255, 0, 255])
                pg.keyboard.press("Control+z"); time.sleep(0.2); pg.evaluate(f"{ED}.render()")
                self.assertNotEqual(scr(150, 205), [255, 0, 255])
                self.assertNotEqual(scr(450, 212), [255, 0, 255])

                pg.mouse.move(box["x"] + 200, box["y"] + 200)
                for _ in range(25):
                    pg.mouse.wheel(0, 400)
                time.sleep(0.3)
                v = pg.evaluate(f"[{ED}.view.panY, {ED}.doc.height * {ED}.view.zoom]")
                self.assertGreaterEqual(v[0] + v[1], 79)
            finally:
                b.close()
            b, pg = self._page(p, [str(src)], size=(420, 800))
            try:
                self._wait_doc(pg)
                time.sleep(0.5)
                self.assertIn("more", pg.evaluate("document.querySelector('.ph-bar').className"))
                bb = pg.locator(".ph-bar").bounding_box()
                pg.mouse.move(bb["x"] + 150, bb["y"] + 10); pg.mouse.wheel(0, 600); time.sleep(0.3)
                self.assertGreater(pg.evaluate("document.querySelector('.ph-bar').scrollLeft"), 50)
                self.assertTrue(pg.locator(".ph-menu[data-menu=ai]").is_visible())
            finally:
                b.close()
        self.assertEqual(self.errors, [])

@unittest.skipUnless(os.environ.get("XKIRO_BASE") and os.environ.get("XKIRO_KEY"), "live keys are not set")
class LiveVisionTest(unittest.TestCase):
    """qwen3.8-max (vision) sees the picture through our prompts and answers an edit plan that parses."""

    def _ask(self, msgs):
        base = os.environ["XKIRO_BASE"].rstrip("/")
        last = None
        for key in [os.environ.get(k) for k in ("XKIRO_KEY", "XKIRO_KEY2", "XKIRO_KEY3") if os.environ.get(k)]:
            req = urllib.request.Request(base + "/chat/completions", method="POST",
                                         data=json.dumps({"model": "qwen/qwen3.8-max:free", "messages": msgs, "stream": False}).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": "MemuNote-tests/3.5"})
            try:
                return json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"]
            except Exception as exc:  # noqa: BLE001 - 429 → next key
                last = exc
                time.sleep(2)
        self.skipTest(f"model unavailable: {last}")

    def _image(self):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (640, 400), (60, 150, 70))
        ImageDraw.Draw(im).ellipse((260, 120, 380, 240), fill=(220, 30, 30))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    def test_describe_and_locate(self) -> None:
        img = self._image()
        text = self._ask(photo.build_messages({"action": "describe", "image": img, "width": 640, "height": 400}, "ru"))
        self.assertRegex(text.lower(), r"красн|круг|шар|мяч")
        loc = photo.parse_json_answer(self._ask(photo.build_messages({"action": "locate", "image": img, "width": 640, "height": 400, "question": "красный круг"}, "ru")))
        self.assertTrue(loc and loc.get("objects"), loc)
        x0, y0, x1, y1 = loc["objects"][0]["box"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.assertTrue(350 < cx < 650 and 300 < cy < 700, loc)

    def test_command_plan(self) -> None:
        plan = photo.parse_json_answer(self._ask(photo.build_messages({"action": "command", "image": self._image(), "question": "сделай фото теплее и чуть ярче"}, "ru")))
        self.assertIsNotNone(plan)
        names = {o.get("name") for o in plan.get("ops", []) if o.get("op") == "adjust"}
        self.assertTrue(names & {"temperature", "colorBalance", "brightnessContrast", "exposure", "levels", "curves", "vibrance"}, plan)

if __name__ == "__main__":
    unittest.main()
