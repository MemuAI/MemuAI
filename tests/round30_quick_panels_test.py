"""Round 30 (r30-quick-panels, item 7): looks of the Quick Access panels and panels built into the window.

Backend: app/quick_panel_look.py (validation, merge, per-panel overrides, embed order) and POST /api/quick/canvas/look
on a temporary profile. Frontend: static guards + a node check of panel-look.js against panels.js' linesOf.
Screens: ``python tests/round30_quick_panels_shots.py OUT`` (headless Chromium; look at them).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r30qp-")

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"


class LookModel(unittest.TestCase):
    def test_defaults_and_clamping(self):
        from app import quick_panel_look as L
        self.assertEqual(L.clean_look(None), L.DEFAULT_LOOK)
        look = L.clean_look({"style": "text", "opacity": 250, "font": "70", "accent": "#ABCDEF", "theme": "auto",
                             "titles": False, "radius": -3, "dim": 55.4, "lines": 9, "shadow": "no", "evil": 1})
        self.assertEqual(look["style"], "text")
        self.assertEqual((look["opacity"], look["font"], look["radius"], look["dim"], look["lines"]), (100, 80, 0, 55, 5))
        self.assertEqual(look["accent"], "#abcdef")
        self.assertFalse(look["titles"])
        self.assertTrue(look["shadow"])                 # a non-bool is ignored
        self.assertNotIn("evil", look)
        for bad in ({"style": "fancy"}, {"accent": "red"}, {"theme": "neon"}, {"opacity": float("nan")}):
            self.assertEqual(L.clean_look(bad), L.DEFAULT_LOOK)

    def test_partial_overrides_and_effective(self):
        from app import quick_panel_look as L
        looks = L.clean_looks({"p_weather": {"style": "icon", "junk": 1}, "bad id": {"style": "text"}, "p_x": {"style": "?"}})
        self.assertEqual(looks, {"p_weather": {"style": "icon"}})
        glob = L.clean_look({"style": "compact", "opacity": 40})
        self.assertEqual(L.effective(glob, looks, "p_weather")["style"], "icon")
        self.assertEqual(L.effective(glob, looks, "p_other")["opacity"], 40)
        self.assertEqual(L.effective(glob, looks, "p_other", embed_style="text")["style"], "text")
        self.assertEqual(L.effective(glob, looks, "p_weather", embed_style="text")["style"], "icon")   # the panel's own wins

    def test_embed(self):
        from app import quick_panel_look as L
        e = L.clean_embed({"place": "side", "items": ["p_a", {"ref": "p_b", "hidden": True}, {"ref": "p_a"}, {"ref": "x"}],
                           "style": "text", "max": 99})
        self.assertEqual(e["place"], "side")
        self.assertEqual(e["items"], [{"ref": "p_a", "hidden": False}, {"ref": "p_b", "hidden": True}])
        self.assertEqual((e["style"], e["max"]), ("text", 12))
        self.assertEqual(L.clean_embed({"place": "top"})["place"], "off")

    def test_patch(self):
        from app import quick_panel_look as L
        doc = {"look": dict(L.DEFAULT_LOOK), "looks": {}, "embed": dict(L.DEFAULT_EMBED)}
        L.apply_patch(doc, {"look": {"style": "text", "font": 120}})
        self.assertEqual((doc["look"]["style"], doc["look"]["font"]), ("text", 120))
        L.apply_patch(doc, {"panel": "p_a", "look": {"style": "icon", "opacity": 30}})
        L.apply_patch(doc, {"panel": "p_a", "look": {"opacity": None}})
        self.assertEqual(doc["looks"], {"p_a": {"style": "icon"}})
        L.apply_patch(doc, {"panel": "p_a", "reset": True})
        self.assertEqual(doc["looks"], {})
        L.apply_patch(doc, {"embed": {"place": "above", "items": [{"ref": "p_a"}]}})
        L.apply_patch(doc, {"embed": {"style": "compact"}})
        self.assertEqual((doc["embed"]["place"], doc["embed"]["style"], len(doc["embed"]["items"])), ("above", "compact", 1))
        L.apply_patch(doc, {"panel": "p_b", "look": {"style": "text"}})
        L.apply_patch(doc, {"reset_all": True})
        self.assertEqual((doc["look"], doc["looks"]), (L.DEFAULT_LOOK, {}))
        with self.assertRaises(ValueError):
            L.apply_patch(doc, {"panel": "../x", "look": {"style": "text"}})


class Route(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import quick_canvas
        self.path = quick_canvas._path()
        self.backup = self.path.read_bytes() if self.path.exists() else None
        app = FastAPI()
        app.include_router(quick_canvas.router)
        self.client = TestClient(app)

    def tearDown(self):
        if self.backup is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(self.backup)

    def test_saved_synced_and_survives_other_saves(self):
        c = self.client
        out = c.post("/api/quick/canvas/look", json={"look": {"style": "compact", "opacity": 60}}).json()
        self.assertEqual(out["look"]["style"], "compact")
        c.post("/api/quick/canvas/look", json={"panel": "p_w1", "look": {"style": "text", "accent": "#112233"}})
        c.post("/api/quick/canvas/look", json={"embed": {"place": "side", "items": ["p_w1"]}})
        c.post("/api/quick/canvas/settings", json={"settings": {"density": "large"}})          # an unrelated save keeps it
        state = c.get("/api/quick/canvas").json()
        self.assertEqual((state["look"]["style"], state["look"]["opacity"]), ("compact", 60))
        self.assertEqual(state["looks"]["p_w1"], {"style": "text", "accent": "#112233"})
        self.assertEqual(state["embed"]["place"], "side")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("look", stored)
        self.assertEqual(c.post("/api/quick/canvas/look", json={"panel": "nope!", "look": {}}).status_code, 400)

    def test_quick_canvas_file_is_synced_with_panels(self):
        from app import gdrive_sync
        self.assertEqual(gdrive_sync.classify("memuai/quick_canvas.json"), "panels")
        self.assertEqual(gdrive_sync.classify("memuai/profiles/work/quick_canvas.json"), "panels")


class Frontend(unittest.TestCase):
    def read(self, rel):
        return (ROOT / "app" / "pgstatic" / rel).read_text(encoding="utf-8")

    def test_wiring(self):
        index = json.loads(self.read("quick/plugins/index.json"))["plugins"]
        ids = [row["id"] for row in index]
        self.assertLess(ids.index("canvas"), ids.index("canvas-embed"))
        embed = next(r for r in index if r["id"] == "canvas-embed")
        self.assertEqual(embed["settings"], "canvas-look-settings.js")
        layer = self.read("quick/layer.html")
        self.assertIn("/quick/panel-look.js", layer)
        self.assertIn("/quick/panel-look.css", layer)
        self.assertLess(layer.index("panel-look.js"), layer.index("layer.js"))
        self.assertIn("MemuPanelLook.apply(node, lookOf(p)", self.read("quick/layer.js"))
        self.assertIn("MemuPanelLook.faceHtml(p, lookOf(it)", self.read("quick/plugins/canvas.js"))
        self.assertIn("miniOf, linesOf, ico", self.read("panels.js"))
        for rel in ("quick/plugins/canvas-look-settings.js", "quick/plugins/canvas-embed.js", "quick/panel-look.js"):
            src = self.read(rel)
            self.assertNotRegex(src, r'tr\("[^"]*[А-Яа-я][^"]*"\)', rel)      # every Russian string has its English pair

    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_faces_in_node(self):
        script = r"""
const fs = require('fs');
global.window = global; global.document = { documentElement: { lang: 'ru', dataset: {} }, querySelector: () => null };
global.matchMedia = () => ({ matches: true });
global.MemuPanels = {
  linesOf: (p, max) => [{label: 'Москва', value: '7', unit: '°C', trend: ''}, {label: 'Ощущается', value: '4', unit: '°'}, {label: 'Ветер', value: '5', unit: 'м/с'}].slice(0, max),
  miniOf: () => ({ icon: 'cloudy', value: '7', unit: '°C', label: 'Москва' }), ico: (n) => '<i data-i="' + n + '"></i>', iconFor: () => 'dot' };
eval(fs.readFileSync(process.argv[1], 'utf8'));
const L = window.MemuPanelLook;
const panel = { id: 'p_w', title: 'Погода', data: { t: 1 }, status: 'live', spec: {} };
const doc = { look: { style: 'text', lines: 2 }, looks: { p_w: { theme: 'auto' } }, embed: { style: 'icon' } };
const look = L.resolve(doc, 'p_w');
const out = { style: look.style, theme: L.theme(look), embedStyle: L.resolve(doc, 'p_w', { embed: true }).style,
  card: L.faceHtml(panel, { ...look, style: 'card' }), text: L.faceHtml(panel, look), icon: L.faceHtml(panel, { ...look, style: 'icon' }),
  building: L.faceHtml({ ...panel, data: {}, status: 'building' }, look), value: L.valueText(panel, look) };
const el = { dataset: {}, style: { props: {}, setProperty(k, v) { this.props[k] = v; }, removeProperty(k) { delete this.props[k]; } } };
L.apply(el, { ...look, accent: '#123456', opacity: 35, dim: 40 }, { place: 'screen' });
out.el = { ds: el.dataset, props: el.style.props };
console.log(JSON.stringify(out));
"""
        res = subprocess.run(["node", "-e", script, str(QUICK / "panel-look.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual((out["style"], out["theme"], out["embedStyle"]), ("text", "dark", "icon"))
        self.assertIsNone(out["card"])
        self.assertIn('class="pl-face pl-text"', out["text"])
        self.assertEqual(out["text"].count('class="pl-line'), 2)
        self.assertIn("pl-big", out["icon"])
        self.assertIn("Собираю", out["building"])
        self.assertEqual(out["value"], "Москва: 7 °C\nОщущается: 4 °")
        self.assertEqual(out["el"]["ds"]["plStyle"], "text")
        self.assertEqual(out["el"]["ds"]["plTheme"], "dark")
        self.assertEqual(out["el"]["ds"]["plPlace"], "screen")
        self.assertEqual(out["el"]["props"]["--accent"], "#123456")
        self.assertEqual(out["el"]["props"]["--pl-op"], "0.35")
        self.assertEqual(out["el"]["props"]["--pl-dim"], "0.6")


if __name__ == "__main__":
    unittest.main()
