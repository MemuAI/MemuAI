"""MemuNote image editor (MemuAI 5.0): looks, tone/colour adjustments, new filters, layer styles, border, before/after.

Run: /workspace/work/venv/bin/python -m pytest tests/photo_v5_test.py -q
* pixels.js in Node: shadows/highlights, clarity, dehaze, channel mixer, gradient map, photo filter, split toning,
  15 ready-made looks (amount, masks), zoom blur, tilt-shift, chromatic aberration, halftone, pencil sketch;
* browser (headless Chromium, skipped without it): layer styles composited on the canvas (drop shadow, stroke, glow,
  colour overlay, masks), .memuimg round trip and PSD export with baked styles, the looks dialog, before/after split view,
  layer-style dialog with presets / copy / paste / rasterize, the border dialog and its single undo step, AI op whitelist.
"""
from __future__ import annotations

import io
import json
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

from tests import round32_photo_test as base  # noqa: E402
from app.memunote.formats import psd  # noqa: E402

PHOTO_JS = base.PHOTO_JS
CHROME = base.CHROME

NODE_V5 = r"""
import * as P from "PIXELS";
const ok = (c, m) => { if (!c) { console.error("FAIL " + m); process.exitCode = 1; } };
const img = (w, h, rgba) => P.makeImage(w, h, rgba);
const px = (im, x, y) => Array.from(im.data.slice((y * im.width + x) * 4, (y * im.width + x) * 4 + 4));
const grad = (w, h) => { const im = img(w, h, [0, 0, 0, 255]); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const i = (y * w + x) * 4, v = Math.round(x / (w - 1) * 255); im.data[i] = v; im.data[i + 1] = Math.round(y / (h - 1) * 255); im.data[i + 2] = 255 - v; } return im; };
const checker = (w, h, s) => { const im = img(w, h, [0, 0, 0, 255]); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const v = ((x / s | 0) + (y / s | 0)) % 2 ? 255 : 0, i = (y * w + x) * 4; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; } return im; };
const maxDiff = (a, b) => { let m = 0; for (let i = 0; i < a.data.length; i++) m = Math.max(m, Math.abs(a.data[i] - b.data[i])); return m; };
// tone
ok(px(P.ADJUST.shadowsHighlights(img(8, 8, [20, 20, 20, 255]), { shadows: 60, highlights: 0, radius: 4 }), 3, 3)[0] > 40, "shadows lift dark pixels");
ok(px(P.ADJUST.shadowsHighlights(img(8, 8, [240, 240, 240, 255]), { shadows: 0, highlights: 60, radius: 4 }), 3, 3)[0] < 225, "highlights recover bright pixels");
ok(px(P.ADJUST.shadowsHighlights(img(8, 8, [20, 20, 20, 255]), { shadows: 0, highlights: 0 }), 3, 3)[0] === 20, "shadows/highlights 0 = identity");
const flat = img(16, 16, [120, 130, 140, 255]);
ok(maxDiff(P.ADJUST.clarity(flat, { amount: 80, radius: 4 }), flat) <= 1, "clarity keeps flat areas");
const ch = checker(32, 32, 4), cl = P.ADJUST.clarity(ch, { amount: -100, radius: 6 });
ok(px(cl, 0, 0)[0] > 10 && px(cl, 4, 0)[0] < 245, "negative clarity softens local contrast");
const hazy = grad(40, 20); for (let i = 0; i < hazy.data.length; i += 4) for (let c = 0; c < 3; c++) hazy.data[i + c] = 120 + hazy.data[i + c] * 0.5;
const dh = P.ADJUST.dehaze(hazy, { amount: 70 }), spread = (im) => { let lo = 255, hi = 0; for (let i = 0; i < im.data.length; i += 4) { lo = Math.min(lo, im.data[i]); hi = Math.max(hi, im.data[i]); } return hi - lo; };
ok(spread(dh) > spread(hazy) + 20, "dehaze increases contrast of a hazy image");
ok(spread(P.ADJUST.dehaze(hazy, { amount: -70 })) < spread(hazy), "negative dehaze adds haze");
// colour
const c1 = img(2, 2, [200, 100, 50, 255]);
ok(maxDiff(P.ADJUST.channelMixer(c1, {}), c1) === 0, "channel mixer identity");
ok(px(P.ADJUST.channelMixer(c1, { rr: 0, rb: 100, bb: 0, br: 100 }), 0, 0).slice(0, 3).join() === "50,100,200", "channel mixer swaps red and blue");
const mono = px(P.ADJUST.channelMixer(c1, { rr: 30, rg: 60, rb: 10, mono: true }), 0, 0); ok(mono[0] === mono[1] && mono[1] === mono[2] && mono[0] === 125, "channel mixer mono");
const bw = img(2, 1, [0, 0, 0, 255]); bw.data.set([255, 255, 255, 255], 4);
const gm = P.ADJUST.gradientMap(bw, { from: "#102030", to: "#f0e0d0", amount: 100 });
ok(px(gm, 0, 0).slice(0, 3).join() === "16,32,48" && px(gm, 1, 0).slice(0, 3).join() === "240,224,208", "gradient map endpoints");
ok(px(P.ADJUST.gradientMap(bw, { from: "#102030", to: "#f0e0d0", reverse: true }), 0, 0).slice(0, 3).join() === "240,224,208", "gradient map reverse");
const pf = px(P.ADJUST.photoFilter(img(1, 1, [128, 128, 128, 255]), { color: "#ec8a00", density: 60 }), 0, 0);
ok(pf[0] > pf[2] + 30, "warming photo filter");
ok(Math.abs((0.299 * pf[0] + 0.587 * pf[1] + 0.114 * pf[2]) - 128) < 8, "photo filter preserves luminosity");
const st = (v) => px(P.ADJUST.splitToning(img(1, 1, [v, v, v, 255]), { shadowHue: 210, shadowSat: 80, highlightHue: 40, highlightSat: 80 }), 0, 0);
ok(st(40)[2] > st(40)[0] + 10, "split toning: blue shadows");
ok(st(225)[0] > st(225)[2] + 10, "split toning: warm highlights");
// looks
ok(P.LOOK_NAMES.length === 15 && P.LOOK_NAMES.every((n) => P.LOOKS[n]), "15 looks");
const photo = grad(24, 16);
for (const n of P.LOOK_NAMES) { const r = P.ADJUST.look(photo, { name: n }); ok(r.width === 24 && maxDiff(r, photo) > 3, "look changes the image: " + n); ok(maxDiff(P.ADJUST.look(photo, { name: n, amount: 0 }), photo) <= 1, "look amount 0: " + n); }
for (const n of ["noir", "mono"]) { const r = P.ADJUST.look(photo, { name: n }), p = px(r, 5, 5); ok(Math.abs(p[0] - p[1]) <= 2 && Math.abs(p[1] - p[2]) <= 2, "look is monochrome: " + n); }
ok(maxDiff(P.ADJUST.look(photo, { name: "nope" }), photo) === 0, "unknown look = no-op");
const half = P.ADJUST.look(photo, { name: "vintage", amount: 50 }), full = P.ADJUST.look(photo, { name: "vintage" });
const mid = (a, b, c) => Math.abs(px(a, 7, 7)[0] - (px(b, 7, 7)[0] + px(c, 7, 7)[0]) / 2) <= 2; ok(mid(half, photo, full), "look amount blends linearly");
// filters
const f = img(24, 24, [90, 140, 200, 255]);
for (const op of ["zoomBlur", "tiltShift", "chromatic"]) ok(maxDiff(P.FILTER[op](f, {}), f) <= 1, op + " keeps uniform images");
const cb = checker(48, 48, 3);
const zb = P.FILTER.zoomBlur(cb, { amount: 60 }); ok(maxDiff(zb, cb) > 60, "zoom blur blurs the periphery"); ok(Math.abs(px(zb, 24, 24)[0] - px(cb, 24, 24)[0]) < 40, "zoom blur keeps the centre");
const ts = P.FILTER.tiltShift(cb, { position: 50, band: 20, radius: 6 }); ok(px(ts, 1, 24)[0] === px(cb, 1, 24)[0] && px(ts, 4, 24)[0] === px(cb, 4, 24)[0], "tilt-shift keeps the band sharp");
const top = px(ts, 1, 1)[0]; ok(top > 40 && top < 215, "tilt-shift blurs top/bottom");
const edge = img(20, 4, [0, 0, 0, 255]); for (let y = 0; y < 4; y++) for (let x = 16; x < 20; x++) edge.data.set([255, 255, 255, 255], (y * 20 + x) * 4);
const ca = P.FILTER.chromatic(edge, { distance: 3 }), cp = (x) => px(ca, x, 1); ok([13, 14, 15, 16, 17].some((x) => cp(x)[0] !== cp(x)[2]), "chromatic aberration separates channels towards the edges");
const g = grad(40, 40), ht = P.FILTER.halftone(g, { size: 8 }); let grey = 0; for (let i = 0; i < ht.data.length; i += 4) { const v = ht.data[i]; if (v > 30 && v < 225) grey++; if (ht.data[i] !== ht.data[i + 1] || ht.data[i + 1] !== ht.data[i + 2]) { grey = 1e9; break; } }
ok(grey < ht.data.length / 4 * 0.25, "halftone is black dots on white " + grey);
const htc = P.FILTER.halftone(g, { size: 8, colored: true }); ok(px(htc, 36, 20)[0] !== px(htc, 36, 20)[2] || px(htc, 4, 20)[0] !== px(htc, 4, 20)[2], "coloured halftone keeps colour");
let dark = 0, dark2 = 0; for (let i = 0; i < 40 * 40 * 4; i += 4) { dark += g.data[i] < 128; dark2 += ht.data[i] < 128; } ok(Math.abs(dark - dark2) < 40 * 40 * 0.2, "halftone keeps tone " + dark + "/" + dark2);
const sk = P.FILTER.sketch(f, {}); ok(px(sk, 12, 12)[0] > 235, "sketch of a flat image is white paper");
const skc = P.FILTER.sketch(checker(32, 32, 8), {}); let lines = 0; for (let i = 0; i < skc.data.length; i += 4) lines += skc.data[i] < 200; ok(lines > 20, "sketch draws edges"); ok(skc.data[0] === skc.data[1], "sketch is grey");
// masks are honoured
const sel = new Uint8Array(4 * 4); sel[0] = 255; const mres = P.runOp("look", img(4, 4, [100, 150, 200, 255]), { name: "noir" }, sel);
ok(px(mres, 3, 3).slice(0, 3).join() === "100,150,200" && px(mres, 0, 0).slice(0, 3).join() !== "100,150,200", "selection mask limits a look");
for (const op of P.OPS) { P.runOp(op, img(1, 1, [0, 0, 0, 0]), {}); P.runOp(op, img(5, 3, [80, 90, 100, 255]), {}); }
console.log("OK " + P.OPS.length);
"""

@unittest.skipUnless(shutil.which("node"), "node not installed")
class PixelsV5NodeTest(unittest.TestCase):
    def test_new_adjustments_looks_and_filters(self) -> None:
        code = NODE_V5.replace("PIXELS", (PHOTO_JS / "pixels.js").as_uri())
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(code)
        try:
            out = subprocess.run(["node", fh.name], capture_output=True, text=True, timeout=180)
        finally:
            os.unlink(fh.name)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("OK", out.stdout)

class StaticV5Test(unittest.TestCase):
    def test_new_ops_are_wired_everywhere(self) -> None:
        cmds = (PHOTO_JS / "commands.js").read_text(encoding="utf-8")
        ai = (PHOTO_JS / "ai.js").read_text(encoding="utf-8")
        i18n = (PHOTO_JS / "i18n.js").read_text(encoding="utf-8")
        from app.memunote import photo
        for name in ("shadowsHighlights", "clarity", "dehaze", "channelMixer", "gradientMap", "photoFilter", "splitToning",
                     "zoomBlur", "tiltShift", "chromatic", "halftone", "sketch", "look"):
            self.assertIn(f"{name}:", cmds, name)
            self.assertIn(f'"{name}"', ai, name)
            self.assertIn(name, photo.OPS_SCHEMA, name)
            self.assertEqual(i18n.count(f"ph_adj_{name}:"), 2, name)
        for look in ("vivid", "warm", "cool", "golden", "matte", "fade", "vintage", "film", "cinematic", "dramatic", "noir",
                     "mono", "lomo", "pastel", "duotone"):
            self.assertEqual(i18n.count(f"ph_look_{look}:"), 2, look)
            self.assertIn(look, photo.OPS_SCHEMA)

MODEL_CHECKS = r"""
async () => {
  const R = {};
  const D = await import('./js/photo/doc.js');
  const io = await import('./js/photo/io.js');
  const AI = await import('./js/photo/ai.js');
  const pix = (c, x, y) => Array.from(c.getContext('2d').getImageData(x, y, 1, 1).data);
  const rgb = (c, x, y) => pix(c, x, y).slice(0, 3).join();
  const mk = () => {
    const t = new D.PhotoDoc(80, 60), bg = t.makeLayer({ name: 'bg' }), sq = t.makeLayer({ name: 'sq' });
    const g = bg.canvas.getContext('2d'); g.fillStyle = '#ffffff'; g.fillRect(0, 0, 80, 60);
    const s = sq.canvas.getContext('2d'); s.fillStyle = '#ff0000'; s.fillRect(30, 20, 20, 20);
    t.layers.push(bg, sq); t.activeId = bg.id; return [t, sq];
  };
  const [t, sq] = mk();
  R.noFx = [rgb(t.composite(), 40, 45), rgb(t.composite(), 40, 30)];
  sq.fx = D.sanitizeFx({ shadow: { on: true, color: '#000000', opacity: 100, angle: 90, distance: 10, size: 0 } }); t.compDirty = true;
  R.shadow = [rgb(t.composite(), 40, 45), rgb(t.composite(), 40, 15), rgb(t.composite(), 40, 30)];
  sq.fx = D.sanitizeFx({ shadow: { on: true, color: '#000000', opacity: 100, angle: 120, distance: 12, size: 0 } }); t.compDirty = true;
  R.shadowAngle = [rgb(t.composite(), 53, 48), rgb(t.composite(), 27, 48)];
  sq.fx = D.sanitizeFx({ stroke: { on: true, color: '#00ff00', size: 4, opacity: 100 } }); t.compDirty = true;
  R.stroke = [pix(t.composite(), 27, 30), rgb(t.composite(), 23, 30), rgb(t.composite(), 40, 30)];
  sq.fx = D.sanitizeFx({ overlay: { on: true, color: '#0000ff', opacity: 100 } }); t.compDirty = true;
  R.overlay = [rgb(t.composite(), 40, 30), rgb(t.composite(), 20, 30)];
  sq.fx = D.sanitizeFx({ glow: { on: true, color: '#ffd23f', opacity: 100, size: 12 } }); t.compDirty = true;
  R.glow = [pix(t.composite(), 27, 30), rgb(t.composite(), 40, 30), rgb(t.composite(), 2, 2)];
  R.sanitize = D.sanitizeFx({ shadow: { on: 1, color: 'red', opacity: 900, distance: -5 }, bogus: { on: true } });
  // masks hide the styled pixels too
  sq.fx = D.sanitizeFx({ shadow: { on: true, color: '#000000', opacity: 100, angle: 90, distance: 10, size: 0 } });
  sq.mask = D.newCanvas(80, 60); const mx = sq.mask.getContext('2d'); mx.fillStyle = '#fff'; mx.fillRect(0, 0, 40, 60); sq.maskOn = true; t.compDirty = true;
  R.masked = [rgb(t.composite(), 35, 45), rgb(t.composite(), 45, 45)];
  sq.mask = null; t.compDirty = true;
  // .memuimg keeps the style; PSD/SVG bake it
  const back = await io.readMemuimg(await (await io.writeMemuimg(t)).arrayBuffer());
  const bsq = back.flat().find((l) => l.name === 'sq');
  R.memuimg = [!!bsq?.fx?.shadow?.on, bsq?.fx?.shadow?.distance, rgb(back.flatten(), 40, 45)];
  R.svg = (await (await io.writeSvg(t)).text()).split('<image').length - 1;
  R.psd = (await io.saveDocument(t, OUT + '/fx.psd', 'psd', { force: true })).ok;
  // undo of a style change
  t.begin('fx'); sq.fx = D.sanitizeFx({ overlay: { on: true, color: '#00ff00', opacity: 100 } }); t.commit(); t.compDirty = true;
  R.undo1 = rgb(t.composite(), 40, 30); t.undo(); t.compDirty = true; R.undo2 = rgb(t.composite(), 40, 30); t.redo(); t.compDirty = true; R.undo3 = rgb(t.composite(), 40, 30);
  // AI whitelist knows the new operations
  const ops = AI.sanitizeOps([{ op: 'adjust', name: 'look', params: { name: 'film', amount: 70 } }, { op: 'adjust', name: 'look', params: { name: 'bogus' } },
    { op: 'adjust', name: 'clarity', params: { amount: 20 } }, { op: 'filter', name: 'tiltShift', params: {} }, { op: 'filter', name: 'nope' }]);
  R.ai = [ops.length, ops.map((o) => o.name).join(), AI.opLabel(ops[0])];
  return R;
}
"""

@unittest.skipUnless(CHROME, "headless Chromium is not available")
class EditorV5BrowserTest(base._ServerCase):
    ED = "MN.tabs.find(t => t.kind === 'image').comp"

    def _page(self, p, files, size=(1280, 800)):
        for f in files:
            self.app.allow(f)
        self.app.initial_files = list(files)
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-gpu"])
        pg = b.new_page(viewport={"width": size[0], "height": size[1]})
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{self.app.port}/index.html#t={self.app.token}")
        for _ in range(100):
            if pg.evaluate("!!(window.MN && MN.tabs.some(t => t.kind === 'image' && t.comp && t.comp.doc))"):
                return b, pg
            time.sleep(0.1)
        b.close()
        self.fail("photo editor did not load: " + "; ".join(self.errors))

    def _until(self, pg, expr, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if pg.evaluate(expr):
                return True
            time.sleep(0.1)
        self.fail("timeout: " + expr + " " + "; ".join(self.errors))

    def test_layer_styles_composite_save_and_undo(self) -> None:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        src = self.tmp / "p.png"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(src)
        out = self.tmp / "out"
        out.mkdir()
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                r = pg.evaluate(MODEL_CHECKS.replace("OUT", json.dumps(str(out))))
            finally:
                b.close()
        self.assertEqual(r["noFx"], ["255,255,255", "255,0,0"])
        self.assertEqual(r["shadow"], ["0,0,0", "255,255,255", "255,0,0"])
        self.assertEqual(r["shadowAngle"], ["0,0,0", "255,255,255"])
        self.assertTrue(r["stroke"][0][1] > 200 and r["stroke"][0][0] < 60, r["stroke"])
        self.assertEqual(r["stroke"][1:], ["255,255,255", "255,0,0"])
        self.assertEqual(r["overlay"], ["0,0,255", "255,255,255"])
        glow = r["glow"][0]
        self.assertTrue(glow[0] > 200 and glow[1] > 180 and glow[2] < 235, glow)
        self.assertEqual(r["glow"][1:], ["255,0,0", "255,255,255"])
        self.assertEqual(sorted(r["sanitize"]), ["shadow"])
        self.assertEqual(r["sanitize"]["shadow"]["color"], "#000000")
        self.assertEqual(r["sanitize"]["shadow"]["opacity"], 100)
        self.assertEqual(r["sanitize"]["shadow"]["distance"], 0)
        self.assertEqual(r["masked"], ["0,0,0", "255,255,255"])
        self.assertEqual(r["memuimg"], [True, 10, "0,0,0"])
        self.assertEqual(r["svg"], 2)
        self.assertTrue(r["psd"])
        tree = psd.read_psd((out / "fx.psd").read_bytes())
        layer = next(x for x in tree["layers"] if x["name"] == "sq")
        im = Image.open(io.BytesIO(layer["png"])).convert("RGBA")
        self.assertEqual(im.getpixel((40 - layer["x"], 45 - layer["y"])), (0, 0, 0, 255))
        self.assertEqual(r["undo1"], "0,255,0")
        self.assertEqual(r["undo2"], "255,0,0")
        self.assertEqual(r["undo3"], "0,255,0")
        self.assertEqual(r["ai"][:2], [3, "look,clarity,tiltShift"])
        self.assertIn("Плёнка", r["ai"][2])
        self.assertEqual(self.errors, [])

    def test_looks_compare_layer_style_and_border_ui(self) -> None:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        src = self.tmp / "two.png"
        im = Image.new("RGB", (200, 150), (40, 160, 200))
        im.paste((220, 40, 40), (0, 0, 100, 150))
        im.save(src)
        ED = self.ED
        with sync_playwright() as p:
            b, pg = self._page(p, [str(src)])
            try:
                time.sleep(0.4)
                # props panel exposes the new tools
                pg.evaluate(f"{ED}.showTab('props')")
                for sel in ("[data-adj=clarity]", "[data-adj=shadowsHighlights]", "[data-adj=halftone]", "[data-adj=tiltShift]",
                            "[data-cmd2=looks]", "[data-cmd2=compare]", "[data-cmd2=frame]", "[data-fx=shadow]"):
                    self.assertEqual(pg.locator(sel).count(), 1, sel)
                pg.locator("[data-adj=clarity]").click()
                self._until(pg, "document.querySelectorAll('.ph-dlg').length === 1")
                pg.keyboard.press("Escape")
                self._until(pg, "document.querySelectorAll('.ph-dlg').length === 0")

                # looks: 15 tiles with live thumbnails, one undoable step
                pg.locator("[data-cmd2=looks]").click()
                self._until(pg, "document.querySelectorAll('.ph-dlg.wide .ph-look').length === 15")
                self._until(pg, "(() => { const c = document.querySelectorAll('.ph-look canvas')[14]; return c.width > 96 && c.getContext('2d').getImageData(5, 5, 1, 1).data[3] === 255; })()")
                pg.locator(".ph-look[data-look=noir]").click()
                self.assertEqual(pg.locator(".ph-look.on").get_attribute("data-look"), "noir")
                n0 = pg.evaluate(f"{ED}.doc.history.length")
                pg.locator(".ph-dlg [data-ok]").click()
                self._until(pg, f"{ED}.doc.history.length === {n0 + 1}")
                self.assertIn("Нуар", pg.evaluate(f"{ED}.doc.history[{ED}.doc.history.length - 1].label"))
                p1 = pg.evaluate(f"Array.from({ED}.doc.composite().getContext('2d').getImageData(50, 75, 1, 1).data)")
                self.assertTrue(abs(p1[0] - p1[1]) <= 2 and abs(p1[1] - p1[2]) <= 2, p1)

                # before/after: left of the divider shows the original, right — the edit
                pg.evaluate(f"{ED}.el.focus()")
                pg.keyboard.press("Backslash")
                self.assertTrue(pg.evaluate(f"!!{ED}.compare && {ED}.el.classList.contains('ph-comparing')"))
                self.assertTrue(pg.evaluate(f"{ED}.el.querySelector('[data-a=compare]').classList.contains('on')"))
                time.sleep(0.2)
                view = f"((x, y) => {{ const e = {ED}, d = e.dpr; return Array.from(e.canvas.getContext('2d').getImageData(Math.round((e.view.panX + x * e.view.zoom) * d), Math.round((e.view.panY + y * e.view.zoom) * d), 1, 1).data); }})"
                before, after = pg.evaluate(f"{view}(50, 75)"), pg.evaluate(f"{view}(150, 75)")
                self.assertTrue(before[0] > before[1] + 100, before)
                self.assertTrue(abs(after[0] - after[1]) <= 3 and abs(after[1] - after[2]) <= 3, after)
                box = pg.locator(".ph-view").bounding_box()
                vx = box["x"] + pg.evaluate(f"{ED}.view.panX") + 20 * pg.evaluate(f"{ED}.view.zoom")
                vy = box["y"] + pg.evaluate(f"{ED}.view.panY") + 75 * pg.evaluate(f"{ED}.view.zoom")
                pg.mouse.move(vx, vy); pg.mouse.down(); pg.mouse.move(vx + 5, vy, steps=3); pg.mouse.up()
                split = pg.evaluate(f"{ED}.compare.split")
                self.assertTrue(0.05 < split < 0.2, split)
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), n0 + 1)
                pg.keyboard.press("Escape")
                self.assertFalse(pg.evaluate(f"!!{ED}.compare || {ED}.el.classList.contains('ph-comparing')"))

                # layer style dialog: preset → OK → badge; copy / paste; rasterize + undo
                pg.evaluate(f"{ED}.showTab('layers')")
                pg.evaluate(f"{ED}.cmd('layerStyle', 'shadow')")
                self._until(pg, "!!document.querySelector('.ph-dlg .ph-fxpresets')")
                pg.locator(".ph-dlg [data-preset=sticker]").click()
                self.assertTrue(pg.evaluate("document.querySelector('.ph-dlg [data-on=stroke]').checked"))
                pg.locator(".ph-dlg [data-ok]").click()
                self._until(pg, f"!!{ED}.doc.active.fx && {ED}.doc.active.fx.stroke.on")
                self.assertIn("Стиль слоя", pg.evaluate(f"{ED}.doc.history[{ED}.doc.history.length - 1].label"))
                self._until(pg, "document.querySelectorAll('.ph-fxb').length === 1")
                pg.evaluate(f"{ED}.cmd('copyLayerStyle')")
                pg.evaluate(f"{ED}.cmd('newLayer')")
                pg.evaluate(f"{ED}.cmd('pasteLayerStyle')")
                self.assertTrue(pg.evaluate(f"JSON.stringify({ED}.doc.active.fx) === JSON.stringify({ED}.doc.layers[0].fx)"))
                pg.evaluate(f"{ED}.cmd('deleteLayer')")
                pg.evaluate(f"{ED}.doc.activeId = {ED}.doc.layers[0].id")
                c0 = pg.evaluate(f"Array.from({ED}.doc.composite().getContext('2d').getImageData(0, 0, 200, 150).data.filter((_, i) => i % 97 === 0))")
                pg.evaluate(f"{ED}.cmd('rasterizeLayerStyle')")
                self.assertFalse(pg.evaluate(f"!!{ED}.doc.layers[0].fx"))
                c1 = pg.evaluate(f"Array.from({ED}.doc.composite().getContext('2d').getImageData(0, 0, 200, 150).data.filter((_, i) => i % 97 === 0))")
                self.assertLessEqual(max(abs(a - b2) for a, b2 in zip(c0, c1)), 2)
                pg.evaluate(f"{ED}.undo()")
                self.assertTrue(pg.evaluate(f"!!{ED}.doc.layers[0].fx && {ED}.doc.layers[0].fx.stroke.on"))

                # border: outside enlarges the canvas, one undo step reverts everything
                n1, layers1 = pg.evaluate(f"{ED}.doc.history.length"), pg.evaluate(f"{ED}.doc.flat().length")
                pg.evaluate(f"{ED}.cmd('frame')")
                self._until(pg, "!!document.querySelector('.ph-framedlg')")
                pg.locator(".ph-framedlg input.ph-num[data-p=size]").fill("12")
                pg.locator(".ph-framedlg input[data-p=color]").evaluate("(e) => { e.value = '#ffcc00'; e.dispatchEvent(new Event('input', { bubbles: true })); }")
                time.sleep(0.2)
                self.assertTrue(pg.evaluate(f"!!{ED}.framePreview && {ED}.framePreview.size === 12"))
                pg.locator(".ph-framedlg [data-ok]").click()
                self._until(pg, f"{ED}.doc.width === 224")
                self.assertEqual(pg.evaluate(f"[{ED}.doc.width, {ED}.doc.height]"), [224, 174])
                self.assertEqual(pg.evaluate(f"{ED}.doc.layers[{ED}.doc.layers.length - 1].name"), "Рамка")
                self.assertEqual(pg.evaluate(f"Array.from({ED}.doc.composite().getContext('2d').getImageData(3, 3, 1, 1).data).join()"), "255,204,0,255")
                self.assertEqual(pg.evaluate(f"{ED}.doc.history.length"), n1 + 1)
                pg.evaluate(f"{ED}.undo()")
                self.assertEqual(pg.evaluate(f"[{ED}.doc.width, {ED}.doc.height, {ED}.doc.flat().length]"), [200, 150, layers1])
            finally:
                b.close()
        self.assertEqual(self.errors, [])

if __name__ == "__main__":
    unittest.main()
