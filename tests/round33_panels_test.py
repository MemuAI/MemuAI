"""Round 33 (r33-panels, item #25): eight places for panels, per-zone settings, one-shot layout saves, soft scrollbars
and the «Edit layout» mode of the MemuAI window.

    python3 -m pytest -q tests/round33_panels_test.py

The browser part (real MemuAI server on a temporary profile + headless Chromium) is tests/round33_panels_browser.py;
it is run from here when Chromium and Playwright are present.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import panels, panels_layout, pgdata  # noqa: E402

STATIC = ROOT / "app" / "pgstatic"
PANELS_JS = (STATIC / "panels.js").read_text(encoding="utf-8")
LAYOUT_JS = (STATIC / "panels-layout.js").read_text(encoding="utf-8")
LAYOUT_CSS = (STATIC / "panels-layout.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CYR = re.compile(r"[А-Яа-яЁё]")

class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def write_doc(self, doc):
        path = panels._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def row(pid, places, **extra):
        out = {"id": pid, "kind": "notes", "title": pid, "status": "live", "source": {"type": "local", "params": {}},
               "spec": {"blocks": [{"type": "text", "text": "x"}]}, "data": {"_static": True}, "placements": places,
               "order": {p: i for i, p in enumerate(places)}, "size": "m", "cadence": {"kind": "manual"}}
        out.update(extra)
        return out

class LayoutModelTest(unittest.TestCase):
    def test_zone_ids(self):
        self.assertEqual(panels.PLACEMENTS, panels_layout.ZONE_IDS)
        self.assertEqual(set(panels_layout.ZONE_IDS), {"appbar", "top", "composer", "home", "left", "right", "sidebar", "sidebar_bottom"})
        for legacy in panels_layout.LEGACY_ZONES:
            self.assertIn(legacy, panels_layout.ZONE_IDS)

    def test_defaults(self):
        lay = panels_layout.default_layout()
        self.assertEqual(lay["v"], 1)
        self.assertEqual(lay["zones"]["composer"], {"view": "chips", "chats": False})
        self.assertEqual(lay["zones"]["left"], {"view": "cards", "chats": False, "width": 280})
        self.assertEqual(lay["zones"]["home"], {"view": "cards"})
        self.assertEqual(lay["zones"]["appbar"], {"view": "chips"})

    def test_clean_rejects_junk(self):
        lay = panels_layout.clean_layout({"zones": {"home": {"view": "chips", "chats": True, "width": 999},
                                                    "left": {"width": 9999, "view": "nope", "chats": "yes"},
                                                    "right": {"width": "abc"}, "bogus": {"view": "cards"}}})
        self.assertEqual(lay["zones"]["home"], {"view": "cards"})
        self.assertEqual(lay["zones"]["left"], {"view": "cards", "chats": False, "width": panels_layout.WIDTH_MAX})
        self.assertEqual(lay["zones"]["right"]["width"], 300)
        self.assertNotIn("bogus", lay["zones"])
        self.assertEqual(panels_layout.clean_layout("garbage"), panels_layout.default_layout())
        self.assertEqual(panels_layout.clean_layout({"zones": {"left": {"width": 10}}})["zones"]["left"]["width"], panels_layout.WIDTH_MIN)

    def test_patch_keeps_other_zones(self):
        base = panels_layout.clean_layout({"zones": {"left": {"width": 360, "view": "chips"}, "top": {"chats": True}}})
        out = panels_layout.clean_layout({"zones": {"left": {"width": 320}}}, base=base)
        self.assertEqual(out["zones"]["left"], {"view": "chips", "chats": False, "width": 320})
        self.assertTrue(out["zones"]["top"]["chats"])

    def test_clean_items(self):
        items = panels_layout.clean_items([
            {"id": "p_a", "placements": ["left", "nowhere", "left", "top"], "order": {"left": 3, "x": 1, "top": True}, "size": "w"},
            {"id": "p_unknown", "placements": ["home"]},
            {"id": "p_a", "placements": ["home"]},
            {"id": "p_b", "size": "xl"},
            "junk",
        ], ["p_a", "p_b"])
        self.assertEqual(items, [{"id": "p_a", "placements": ["left", "top"], "order": {"left": 3}, "size": "w"}, {"id": "p_b"}])

    def test_public_zones(self):
        ru = panels_layout.public_zones("ru")
        en = panels_layout.public_zones("en")
        self.assertEqual([z["id"] for z in ru], list(panels_layout.ZONE_IDS))
        self.assertEqual(next(z for z in ru if z["id"] == "composer")["name"], "Над полем ввода")
        for z in en:
            self.assertFalse(CYR.search(z["name"]), z)

class PanelsLayoutTest(Base):
    def test_old_document_migrates(self):
        """A Round 32 panels.json (three places, no layout) reads unchanged plus the default layout."""
        self.write_doc({"version": 2, "seeded": True, "settings": {"lang": "ru", "appbar_max": 3},
                        "panels": [self.row("p_old000001", ["home", "appbar"]), self.row("p_old000002", ["sidebar"], size="l")]})
        doc = panels.load()
        self.assertEqual(doc["settings"]["layout"], panels_layout.default_layout())
        self.assertEqual(doc["settings"]["appbar_max"], 3)
        one = next(p for p in doc["panels"] if p["id"] == "p_old000001")
        self.assertEqual(one["placements"], ["home", "appbar"])
        self.assertEqual(next(p for p in doc["panels"] if p["id"] == "p_old000002")["size"], "l")
        self.assertIn("left", one["order"])

    def test_new_places_survive(self):
        self.write_doc({"version": 2, "seeded": True, "settings": {},
                        "panels": [self.row("p_new000001", ["left", "composer", "nope", "sidebar_bottom"])]})
        panel = panels.load()["panels"][0]
        self.assertEqual(panel["placements"], ["left", "composer", "sidebar_bottom"])

    def test_apply_layout_atomic(self):
        self.write_doc({"version": 2, "seeded": True, "settings": {},
                        "panels": [self.row("p_a00000001", ["home"]), self.row("p_b00000001", ["home", "appbar"])]})
        snap = panels.apply_layout(
            [{"id": "p_a00000001", "placements": ["top", "right"], "order": {"top": 0, "right": 2}, "size": "s"},
             {"id": "p_b00000001", "placements": ["composer"], "order": {"composer": 0}},
             {"id": "p_missing01", "placements": ["home"]}],
            {"zones": {"right": {"width": 340, "chats": True}, "composer": {"view": "cards"}}})
        a = next(p for p in snap["panels"] if p["id"] == "p_a00000001")
        b = next(p for p in snap["panels"] if p["id"] == "p_b00000001")
        self.assertEqual((a["placements"], a["size"], a["order"]["top"], a["order"]["right"]), (["top", "right"], "s", 0, 2))
        self.assertEqual(b["placements"], ["composer"])
        zones = snap["settings"]["layout"]["zones"]
        self.assertEqual(zones["right"], {"view": "cards", "chats": True, "width": 340})
        self.assertEqual(zones["composer"]["view"], "cards")
        self.assertEqual(zones["left"]["width"], 280)
        self.assertEqual([z["id"] for z in snap["zones"]], list(panels_layout.ZONE_IDS))

        doc = panels.load()
        self.assertEqual(doc["settings"]["layout"]["zones"]["right"]["width"], 340)

    def test_update_settings_layout_patch(self):
        panels.update_settings({"layout": {"zones": {"left": {"width": 260}}}})
        panels.update_settings({"layout": {"zones": {"top": {"view": "chips"}}}})
        zones = panels.load()["settings"]["layout"]["zones"]
        self.assertEqual(zones["left"]["width"], 260)
        self.assertEqual(zones["top"]["view"], "chips")

    def test_create_reorder_move_into_new_zones(self):
        made = panels.create_panel(template="notes", placements=["left", "composer", "bogus"], start=False)
        self.assertEqual(made["placements"], ["left", "composer"])
        other = panels.create_panel(template="clock", placements=["home"], start=False)
        panels.move(other["id"], "home", "right", 0)
        panels.reorder("left", [other["id"], made["id"]])
        doc = panels.load()
        o = next(p for p in doc["panels"] if p["id"] == other["id"])
        self.assertEqual(set(o["placements"]), {"right", "left"})
        self.assertEqual(o["order"]["left"], 0)
        self.assertEqual(next(p for p in doc["panels"] if p["id"] == made["id"])["order"]["left"], 1)

    def test_route(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import panels_api
        app = FastAPI()
        panels_api.register_panel_routes(app)
        self.write_doc({"version": 2, "seeded": True, "settings": {}, "panels": [self.row("p_r00000001", ["home"])]})
        with TestClient(app) as client:
            out = client.post("/api/pg/panels-layout", json={"panels": [{"id": "p_r00000001", "placements": ["sidebar_bottom"]}],
                                                             "layout": {"zones": {"sidebar_bottom": {"view": "cards"}}}}).json()
            self.assertEqual(out["panels"][0]["placements"], ["sidebar_bottom"])
            self.assertEqual(out["settings"]["layout"]["zones"]["sidebar_bottom"]["view"], "cards")
            snap = client.get("/api/pg/panels").json()
            self.assertIn("zones", snap)
            self.assertEqual(snap["settings"]["layout"]["zones"]["sidebar_bottom"]["view"], "cards")

class WiringTest(unittest.TestCase):
    def test_index_tags(self):
        css = INDEX.index("/panels-layout.css?v=")
        js = INDEX.index("/panels-layout.js?v=")
        self.assertGreater(css, INDEX.index("/panels.css?v="))
        self.assertGreater(js, INDEX.index("/panels.js?v="))
        self.assertLess(js, INDEX.index("/app.js"))

    def test_build_and_specs(self):
        for name in ("build.py", "MemuAI.spec", "MemuAIProxy.spec"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("app.panels_layout", text, name)

    def test_zone_lists_agree(self):
        places = re.search(r"const PLACES = \[([^\]]+)\]", PANELS_JS).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', places), list(panels_layout.ZONE_IDS))
        zones = re.search(r"const ZONES = \{(.+?)\n  \};", LAYOUT_JS, re.S).group(1)
        self.assertEqual(re.findall(r"^\s{4}(\w+): \{", zones, re.M), list(panels_layout.ZONE_IDS))
        for zid, meta in panels_layout.ZONES.items():
            line = re.search(rf"^\s{{4}}{zid}: \{{(.+)\}},?$", zones, re.M).group(1)
            self.assertIn(f'kind: "{meta["kind"]}"', line)
            self.assertIn("views: [" + ", ".join(f'"{v}"' for v in meta["views"]) + "]", line)
        self.assertIn("WIDTH_MIN = 200, WIDTH_MAX = 480", LAYOUT_JS)

    def test_english_strings(self):
        """Every t("ru", "en") pair in the new file has an English half without Cyrillic."""
        pairs = re.findall(r't\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', LAYOUT_JS)
        pairs += re.findall(r't\(\s*`((?:[^`\\]|\\.)*)`\s*,\s*`((?:[^`\\]|\\.)*)`\s*\)', LAYOUT_JS)
        self.assertGreater(len(pairs), 40)
        for ru, en in pairs:
            self.assertFalse(CYR.search(en), en)
        new_js = [m for m in re.findall(r't\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', PANELS_JS) if CYR.search(m[1])]
        self.assertEqual(new_js, [])

    def test_soft_scrollbar_rules(self):
        self.assertIn("scrollbar-width: none !important", LAYOUT_CSS)
        self.assertIn(".mp-softscroll::-webkit-scrollbar { display: none", LAYOUT_CSS)
        self.assertIn("scrollify(home", LAYOUT_JS)

        for design in ("glass", "brutal", "grid"):
            self.assertIn(f'data-design="{design}"] .mp-sthumb', LAYOUT_CSS)

    def test_hooks_in_panels_js(self):
        for hook in ("LAY()?.ensureMounts()", "LAY()?.renderZones()", "LAY()?.autoScroll(event)", "LAY()?.remember()",
                     'a === "arrange"', "LAY().placesField(selected)", "_lay: {"):
            self.assertIn(hook, PANELS_JS)

    def test_node_syntax(self):
        node = os.environ.get("NODE", "node")
        for name in ("panels.js", "panels-layout.js", "memu-ctxmenu.js"):
            try:
                res = subprocess.run([node, "--check", str(STATIC / name)], capture_output=True, text=True, timeout=60)
            except FileNotFoundError:
                self.skipTest("node is not installed")
            self.assertEqual(res.returncode, 0, res.stderr)

def _chromium():
    for path in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(path):
            return path
    return ""

@unittest.skipUnless(_chromium() and os.environ.get("R33_PANELS_BROWSER", "1") != "0", "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    def test_browser(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright unavailable")
        out = Path(tempfile.mkdtemp(prefix="r33-panels-"))
        env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", PYTHONUTF8="1")
        for part in ("1", "2", "3"):
            with self.subTest(part=part):
                res = subprocess.run([sys.executable, str(ROOT / "tests" / "round33_panels_browser.py"), str(out), part],
                                     capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
                self.assertEqual(res.returncode, 0, res.stdout[-4000:] + res.stderr[-4000:])

if __name__ == "__main__":
    unittest.main()
