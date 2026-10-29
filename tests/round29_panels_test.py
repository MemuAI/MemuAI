"""Round 29 (r29-panels): clipped panels on the screen layer and the panel management rework.

Pure / static checks (the headless checks are tests/round29_panels_ui_test.py → round29_panels_shots.py):
* app/quick_layer.effective_scale — the click-through region uses the factor the page really renders with (the
  page's viewport vs. the window's physical size), never a nonsense one; region_plan rounds an item's box OUTWARD;
* /api/quick/layer/region hands the page's viewport to the layer (and stays compatible with a layer without it);
* the layer page fits cards to their boxes, measures the region from layout boxes, re-reports it, uses the shared
  context menu with outside-click / Esc / blur closing, drags without arrange mode, keeps the Round 28 security
  rules (no run on the layer itself, the 300 ms guard, the relay whitelist);
* panels.js: right clicks resolved from the press / the point (live cards), capture phase, the shared menu;
* every English string in the touched files is free of Cyrillic.
"""
from __future__ import annotations

import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.quick_layer import effective_scale, region_plan  # noqa: E402

STATIC = ROOT / "app" / "pgstatic"
LAYER_JS = (STATIC / "quick" / "layer.js").read_text(encoding="utf-8")
LAYER_CSS = (STATIC / "quick" / "layer.css").read_text(encoding="utf-8")
LAYER_HTML = (STATIC / "quick" / "layer.html").read_text(encoding="utf-8")
MENU_JS = (STATIC / "memu-ctxmenu.js").read_text(encoding="utf-8")
PANELS_JS = (STATIC / "panels.js").read_text(encoding="utf-8")
CANVAS_JS = (STATIC / "quick" / "plugins" / "canvas.js").read_text(encoding="utf-8")

class Scale(unittest.TestCase):
    def test_nominal_without_a_view(self):
        self.assertEqual(effective_scale(1.5, (2880, 1560), None), 1.5)
        self.assertEqual(effective_scale(1.25, (2400, 1290), {}), 1.25)
        self.assertEqual(effective_scale(0, (1920, 1040), None), 1.0)

    def test_the_real_factor_wins(self):

        self.assertEqual(effective_scale(1.5, (2880, 1560), {"w": 2880, "h": 1560}), 1.0)

        self.assertAlmostEqual(effective_scale(1.5, (2880, 1560), {"w": 2304, "h": 1248}), 1.25, places=3)

        self.assertEqual(effective_scale(1.5, (2880, 1560), {"w": 1920, "h": 1040}), 1.5)

    def test_implausible_views_are_ignored(self):
        for view in ({"w": 0, "h": 0}, {"w": "x", "h": 3}, {"w": float("nan"), "h": 1040}, {"w": 50, "h": 50},
                     {"w": 1000, "h": 1560},
                     {"w": 100000, "h": 56000},
                     [1920, 1040]):
            self.assertEqual(effective_scale(1.5, (2880, 1560), view), 1.5, view)
        self.assertEqual(effective_scale(1.5, (0, 0), {"w": 1920, "h": 1040}), 1.5)

    def test_region_rounds_outward(self):
        plan = region_plan([[10.3, 10.3, 72.2, 72.2, 18]], 1.25, (1920, 1152))
        l, t, r, b, _d = plan["rects"][0]
        self.assertLessEqual(l, math.floor(10.3 * 1.25))
        self.assertLessEqual(t, math.floor(10.3 * 1.25))
        self.assertGreaterEqual(r, math.ceil((10.3 + 72.2) * 1.25) - 0)
        self.assertGreaterEqual(b, math.ceil((10.3 + 72.2) * 1.25) - 0)

        plan = region_plan([[10, 10, 72, 72, 18], [-50, 0, 100, 40, 0]], 1.5, (1920, 1152))
        self.assertEqual(plan["rects"][0], [15, 15, 123, 123, 54])
        self.assertEqual(plan["rects"][1][:4], [0, 0, 75, 60])

        plan = region_plan([[1900, 1100, 100, 100, 0]], 1.0, (1920, 1152))
        self.assertEqual(plan["rects"][0][:4], [1900, 1100, 1920, 1152])

    def test_set_region_uses_the_view(self):
        from app.quick_layer import ScreenLayer

        class Api:
            def __init__(self):
                self.calls = []

            def SetWindowRgn(self, hwnd, region, redraw):
                self.calls.append(region)
                return 1

        class Gdi:
            def __init__(self):
                self.rects = []

            def CreateRectRgn(self, *a):
                self.rects.append(("rect", a))
                return 1

            def CreateRoundRectRgn(self, *a):
                self.rects.append(("round", a))
                return 1

            def CombineRgn(self, *a):
                return 1

            def DeleteObject(self, *a):
                return 1

        class Manager:
            native_ok = False
            api = Api()

        layer = ScreenLayer(Manager())
        layer.hwnd, layer.visible = 7, True
        layer.area = {"scale": 1.5, "physical": (0, 0, 2880, 1560), "key": "x"}
        layer.size = (2880, 1560)
        layer.gdi = Gdi()
        out = layer.set_region([[100, 100, 200, 100, 0]], False, view={"w": 2880, "h": 1560})
        self.assertTrue(out["ok"])
        self.assertIn(("rect", (100, 100, 300, 200)), layer.gdi.rects)
        layer.gdi.rects.clear()
        layer.set_region([[100, 100, 200, 100, 0]], False)
        self.assertIn(("rect", (150, 150, 450, 300)), layer.gdi.rects)

class Route(unittest.TestCase):
    def test_region_route_passes_the_view(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import quick_canvas

        seen = []

        class Layer:
            def available(self):
                return True

            def set_region(self, rects, full=False, view=None):
                seen.append((rects, full, view))
                return {"ok": True}

        class Old:
            def available(self):
                return True

            def set_region(self, rects, full=False):
                seen.append((rects, full, "old"))
                return {"ok": True}

        app = FastAPI()
        app.include_router(quick_canvas.router)
        client = TestClient(app)
        original = quick_canvas._layer_of
        try:
            quick_canvas._layer_of = lambda: (object(), Layer())
            r = client.post("/api/quick/layer/region", json={"rects": [[1, 2, 3, 4, 5]], "full": False, "view": {"w": 1920, "h": 1040, "dpr": 1}}).json()
            self.assertTrue(r["ok"])
            self.assertEqual(seen[-1], ([[1, 2, 3, 4, 5]], False, {"w": 1920, "h": 1040, "dpr": 1}))
            quick_canvas._layer_of = lambda: (object(), Old())
            client.post("/api/quick/layer/region", json={"rects": [], "full": True})
            self.assertEqual(seen[-1], ([], True, "old"))
            r = client.post("/api/quick/layer/region", json={"rects": [], "full": True, "view": "junk"}).json()
            self.assertEqual(seen[-1], ([], True, "old"))
        finally:
            quick_canvas._layer_of = original

class LayerPage(unittest.TestCase):
    def test_cards_are_fitted_to_their_boxes(self):
        self.assertIn("function fitCard(node)", LAYER_JS)
        for level in ('[data-fit="1"]', '[data-fit="2"]', '[data-fit="3"]', '[data-clipped="1"]'):
            self.assertIn(level, LAYER_CSS, level)
        self.assertIn("FIT_MIN_ZOOM = 0.6", LAYER_JS)
        self.assertIn("card.style.zoom", LAYER_JS)
        self.assertIn("MutationObserver", LAYER_JS)
        self.assertIn("document.fonts.ready", LAYER_JS)
        self.assertIn('"fit"', LAYER_JS)

    def test_region_from_layout_boxes(self):
        body = LAYER_JS.split("function regionRects()", 1)[1].split("function reportRegion()", 1)[0]
        self.assertIn("el.offsetLeft, el.offsetTop, el.offsetWidth, el.offsetHeight", body)
        self.assertIn(".mcm", body)
        report = LAYER_JS.split("function reportRegion()", 1)[1].split("/* ----", 1)[0]
        self.assertIn("view", report)
        self.assertIn("!!S.menu && S.glass", report)
        self.assertIn('addEventListener("animationend"', LAYER_JS)
        self.assertIn('addEventListener("transitionend"', LAYER_JS)

    def test_management(self):
        self.assertIn("DRAG_START = 5", LAYER_JS)
        self.assertIn("startDrag(press.id, e, \"\", { x: press.x, y: press.y })", LAYER_JS)
        self.assertIn("swallowOutside: true", LAYER_JS)
        self.assertIn("function itemAt(e)", LAYER_JS)
        self.assertIn("document.elementFromPoint", LAYER_JS)
        for key in ("KeyZ", "KeyY", "ArrowLeft", '"F10"', '"ContextMenu"', '"Delete"'):
            self.assertIn(key, LAYER_JS, key)
        self.assertIn('id="ql-guides"', LAYER_HTML)
        self.assertIn('/memu-ctxmenu.js?v=4.1', LAYER_HTML)
        self.assertLess(LAYER_HTML.index("memu-ctxmenu.js"), LAYER_HTML.index("quick/layer.js"))
        for name in ("onShow", "onHide", "setKeepout", "setEdit"):
            self.assertRegex(LAYER_JS, rf"\b{name}\b")

    def test_round28_security_kept(self):
        run = LAYER_JS.split("function run(actionId, explicit)", 1)[1].split("function pollRuns", 1)[0]
        self.assertIn("performance.now() - S.shownAt < 300", run)
        self.assertIn('bus.send("quick", "run"', run)
        self.assertNotRegex(LAYER_JS, r"/api/quick/canvas/run[\"'?]")
        from app import quick_canvas
        self.assertNotIn("menu", " ".join(quick_canvas.RELAY_EVENTS))

    def test_english_strings_have_no_cyrillic(self):
        for name, src in (("layer.js", LAYER_JS), ("canvas.js", CANVAS_JS), ("panels.js", PANELS_JS)):
            for ru, en in re.findall(r't\("((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\)', src):
                self.assertFalse(re.search("[а-яА-ЯёЁ]", en), f"{name}: {en}")
        self.assertFalse(re.search("[а-яА-ЯёЁ]", MENU_JS.split("(function", 1)[1]), "memu-ctxmenu.js carries no UI text of its own")

class SharedMenu(unittest.TestCase):
    def test_closing_rules(self):
        for needle in ('addEventListener("pointerdown", onDown, true)', 'addEventListener("blur", onBlur)',
                       'addEventListener("resize", onResize)', 'addEventListener("scroll", onScroll, true)',
                       'addEventListener("visibilitychange", onVis)', 'k === "Escape"', 'close("pick")'):
            self.assertIn(needle, MENU_JS, needle)
        self.assertIn("MemuLayers.open", MENU_JS)
        self.assertIn("if (cur === c) attach();", MENU_JS)
        self.assertNotIn("setTimeout(() => { if (cur === c) attach(); }, 0)", MENU_JS)

    def test_stays_in_bounds(self):
        place = MENU_JS.split("function place(el, o)", 1)[1].split("function close(", 1)[0]
        self.assertIn("B.right - pad - w", place)
        self.assertIn("B.bottom - pad - h", place)
        self.assertIn("maxHeight", place)

    def test_main_window_wiring(self):
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('/memu-ctxmenu.js?v=4.1', index)
        self.assertLess(index.index("memu-ctxmenu.js"), index.index("/panels.js?v="))
        self.assertRegex(index, r'/panels\.js\?v=[^"\s]+')
        ctx = PANELS_JS.split("let lastDown = null;", 1)[1].split("document.addEventListener(\"keydown\"", 1)[0]
        self.assertIn("event.stopPropagation()", ctx)
        self.assertIn("}, true);", ctx)
        self.assertIn("elementFromPoint", ctx)
        self.assertIn("window.MemuCtxMenu && !EMBED", PANELS_JS)
        self.assertIn('loadScript("/memu-ctxmenu.js?v=4.1")', CANVAS_JS)
        manifest = json.loads((STATIC / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
        canvas = next(p for p in manifest["plugins"] if p["id"] == "canvas")
        self.assertRegex(canvas["v"], r"-r(29|3\d)$")

if __name__ == "__main__":
    unittest.main()
