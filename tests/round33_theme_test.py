"""Round 33 (r33-theme, item #28): the theme & appearance editor.

    python3 -m pytest -q tests/round33_theme_test.py

Backend: ``ui_look`` normalisation (layout, toolbar, elements, tokens, background), unsafe CSS values rejected,
theme packages (share code round trip, JSON export, migration of older themes and the legacy studio export),
saving a theme from the editor and reading it back, background image assets, the routes and their RU/EN errors.
Frontend (static): the editor script is wired into the window, its fixed bugs stay fixed.
Browser: tests/round33_theme_browser.py (real window + headless Chromium) when Chromium and Playwright are present.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import appearance_extra, pgdata, theme_editor as te, themes  # noqa: E402

STATIC = ROOT / "app" / "pgstatic"
JS = (STATIC / "theme-editor.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CYR = re.compile(r"[А-Яа-яЁё]")
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()


class LookNormalisationTest(unittest.TestCase):
    def test_default_is_complete(self):
        look = te.default_look()
        self.assertEqual(look["v"], te.LOOK_VERSION)
        self.assertEqual(set(look), {"v", "layout", "toolbar", "elements", "tokens", "bg"})
        self.assertEqual(look["layout"]["side_pos"], "left")
        self.assertEqual(look["toolbar"], {"order": [], "hidden": []})
        self.assertEqual(look["tokens"], {"dark": {}, "light": {}})
        self.assertEqual(te.normalize_look("garbage"), look)
        self.assertEqual(te.normalize_look(None), look)

    def test_layout_choices_and_ranges(self):
        lay = te.normalize_layout({"side_pos": "right", "side_mode": "nope", "topbar": "card", "composer": "float",
                                   "bubbles": "<b>", "side_width": 9999, "composer_width": 10, "chat_px": "abc"})
        self.assertEqual(lay["side_pos"], "right")
        self.assertEqual(lay["side_mode"], "full")
        self.assertEqual(lay["topbar"], "card")
        self.assertEqual(lay["bubbles"], "")
        self.assertEqual(lay["side_width"], 480)
        self.assertEqual(lay["composer_width"], 420)
        self.assertEqual(lay["chat_px"], 0)
        self.assertEqual(te.normalize_layout({"side_width": 0})["side_width"], 0)  # 0 = default

    def test_toolbar_partial_order_is_completed_and_hidden_filtered(self):
        tb = te.normalize_toolbar({"order": ["theme", "help", "theme", "evil", 3], "hidden": ["context", "x", "mic", "context"]})
        self.assertEqual(tb["order"][:2], ["theme", "help"])
        self.assertEqual(sorted(tb["order"]), sorted(te.TOOLBAR_ITEMS))
        self.assertEqual(tb["hidden"], ["context", "mic"])
        self.assertEqual(te.normalize_toolbar({"order": [], "hidden": []})["order"], [])

    def test_elements_split_colours_per_mode(self):
        els = te.normalize_elements({
            "msg-user": {"all": {"radius": 99, "bg": "#fff", "shadow": "glow", "font": "comic"},
                         "dark": {"bg": "#123456", "radius": 3}, "light": {"fg": "url(x)"}},
            "BAD ID": {"all": {"radius": 3}}, "empty": {"all": {"nope": 1}},
        })
        self.assertEqual(set(els), {"msg-user"})
        self.assertEqual(els["msg-user"]["all"], {"radius": 48, "shadow": "glow"})   # colours never in "all"
        self.assertEqual(els["msg-user"]["dark"], {"bg": "#123456"})                  # shapes never per mode
        self.assertNotIn("light", els["msg-user"])

    def test_limits(self):
        many = {f"e{i}": {"all": {"radius": 2}} for i in range(500)}
        self.assertEqual(len(te.normalize_elements(many)), te.MAX_ELEMENTS)
        toks = te.normalize_tokens({"dark": {f"--t{i}": "#000" for i in range(500)}, "light": "x"})
        self.assertLessEqual(len(toks["dark"]), te.MAX_TOKENS)
        self.assertEqual(toks["light"], {})

    def test_bg(self):
        bg = te.normalize_bg({"kind": "image", "image": "https://evil.example/x.png", "dim": 300, "angle": 720})
        self.assertEqual(bg["kind"], "")          # an image background without a local asset falls back
        self.assertEqual(bg["image"], "")
        self.assertEqual(bg["dim"], 85)
        self.assertEqual(bg["angle"], 360)
        ok = te.normalize_bg({"kind": "image", "image": "/api/pg/theme-editor/asset/" + "a" * 20 + ".png"})
        self.assertEqual(ok["kind"], "image")
        self.assertEqual(te.normalize_bg({"kind": "gradient", "c1": "red;}", "preset": "?"})["c1"], "")

    def test_preferences_pipeline(self):
        self.assertIn("ui_look", appearance_extra.DEFAULTS)
        prefs = pgdata._normalize_preferences({"ui_look": {"layout": {"side_pos": "right"}, "toolbar": {"hidden": ["help"]}}})
        self.assertEqual(prefs["ui_look"]["layout"]["side_pos"], "right")
        self.assertEqual(prefs["ui_look"]["toolbar"]["hidden"], ["help"])
        self.assertEqual(pgdata._normalize_preferences({"ui_look": "x"})["ui_look"], te.default_look())


class UnsafeValuesTest(unittest.TestCase):
    def test_safe_values_pass(self):
        for value in ("#abc", "#11223344", "rgb(1 2 3 / .5)", "var(--accent)", "color-mix(in srgb, var(--accent) 40%, transparent)",
                      "linear-gradient(140deg, rgb(255 138 101 / .55), #000)", "hsl(200 50% 40%)", "12px"):
            self.assertEqual(te.safe_value(value), value, value)

    def test_unsafe_values_rejected(self):
        for value in ("red; } body { display:none", "url(https://x.example/a.png)", "expression(alert(1))",
                      "#fff /* */", "</style><script>", "var(--a))", "javascript:alert(1)", 'attr("x")', "a" * 300,
                      "image-set(x)", {"x": 1}, True, None, "@import x"):
            self.assertEqual(te.safe_value(value), "", repr(value))
        self.assertEqual(te.safe_color("linear-gradient(#000, #fff)"), "")

    def test_unsafe_look_never_survives(self):
        look = te.normalize_look({"tokens": {"dark": {"--x": "red;}*{display:none", "bad name": "#fff", "--ok": "#000"}},
                                  "elements": {"composer": {"dark": {"bg": "url(//e/x)"}}}})
        self.assertEqual(look["tokens"]["dark"], {"--ok": "#000"})
        self.assertEqual(look["elements"], {})


class PackageTest(Base):
    PKG = {"name": "Night owl", "base": "dark", "tokens": {"--accent": "#ff8800", "--bg": "#101010", "--evil": "red;}"},
           "elements": {"msg-user": {"all": {"radius": 6}, "dark": {"bg": "var(--accent)"}, "light": {"bg": "#fff"}}},
           "css": ".x { color: red; }", "look": {"layout": {"side_pos": "right"}, "elements": {"composer": {"all": {"radius": 3}}}},
           "prefs": {"ui_design": "glass", "accent_color": "nope", "font_scale": 500, "bogus": 1}}

    def test_normalize_package(self):
        pkg = te.normalize_package(self.PKG)
        self.assertEqual(pkg["format"], "memuai-theme")
        self.assertEqual(pkg["schema"], te.SCHEMA)
        self.assertNotIn("--evil", pkg["tokens"])
        self.assertEqual(pkg["elements"]["msg-user"], {"all": {"radius": 6}, "dark": {"bg": "var(--accent)"}})  # own mode only
        self.assertEqual(pkg["look"]["layout"]["side_pos"], "right")
        self.assertEqual(pkg["look"]["elements"], {})                         # colours travel in tokens/elements
        self.assertEqual(pkg["prefs"]["ui_design"], "glass")
        self.assertNotIn("bogus", pkg["prefs"])
        self.assertEqual(pkg["prefs"]["font_scale"], 125)

    def test_share_code_round_trip(self):
        out = te.share_code(self.PKG)
        self.assertTrue(out["code"].startswith(te.SHARE_PREFIX))
        self.assertFalse(out["dropped_image"])
        back = te.parse_text(out["code"])
        self.assertEqual(back, te.normalize_package(self.PKG))
        # whitespace / line breaks pasted from a chat are fine
        spaced = out["code"][:20] + "\n  " + out["code"][20:]
        self.assertEqual(te.parse_text(spaced), back)

    def test_share_code_drops_local_image(self):
        pkg = dict(self.PKG, look={"bg": {"kind": "image", "image": "/api/pg/theme-editor/asset/" + "b" * 20 + ".png"}})
        out = te.share_code(pkg)
        self.assertTrue(out["dropped_image"])
        self.assertEqual(te.parse_text(out["code"])["look"]["bg"]["kind"], "")

    def test_damaged_and_foreign_input(self):
        code = te.share_code(self.PKG)["code"]
        for bad in (code[:-12], te.SHARE_PREFIX + "!!!", "", "hello", '{"format": "other"}', "[1,2]"):
            with self.assertRaises(ValueError, msg=bad):
                te.parse_text(bad)
        bomb = te.SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(b" " * (5 * 1024 * 1024), 9)).decode().rstrip("=")
        with self.assertRaises(ValueError):
            te.parse_text(bomb)

    def test_json_export_any_schema(self):
        self.assertEqual(te.parse_text(json.dumps(self.PKG))["name"], "Night owl")
        legacy = {"name": "Old studio", "base": "light", "css": ":root { --accent: #0a84ff; --bg: #fafafa; }\n.side { opacity: .9 }"}
        pkg = te.parse_text(json.dumps(legacy))
        self.assertEqual(pkg["base"], "light")
        self.assertEqual(pkg["tokens"]["--accent"], "#0a84ff")
        self.assertIn(".side", pkg["css"])
        self.assertNotIn(":root", pkg["css"])

    def test_old_theme_css_migration(self):
        css = "/*\n * @name Paper\n * @base light\n * @author Me\n */\n:root { --accent: #c2410c; --panel: #fff8ee; }\n.composer { border-width: 2px; }\n"
        pkg = te.parse_text(css)
        self.assertEqual(pkg["schema"], te.SCHEMA)
        self.assertEqual((pkg["name"], pkg["base"], pkg["author"]), ("Paper", "light", "Me"))
        self.assertEqual(pkg["tokens"], {"--accent": "#c2410c", "--panel": "#fff8ee"})
        self.assertEqual(pkg["css"], ".composer { border-width: 2px; }")

    def test_save_theme_and_read_back(self):
        css = ":root { --accent: #ff8800; --bg: #101010; }\n/* memu-editor:elements */\n.msg-user .bubble { border-radius: 6px; }\n/* /memu-editor:elements */\n.x { color: red; }"
        saved = te.save_theme({"name": "Night owl", "base": "dark", "css": css, "package": self.PKG})
        theme = themes.get_custom(saved["id"])
        self.assertIsNotNone(theme)
        self.assertIn("@memu-editor", theme["css"])
        self.assertEqual(theme["css"].count("@memu-editor"), 1)
        back = te.package_from_css(theme["css"])
        self.assertEqual(back["name"], "Night owl")
        self.assertEqual(back["tokens"]["--accent"], "#ff8800")
        self.assertEqual(back["elements"]["msg-user"]["all"], {"radius": 6})
        self.assertEqual(back["css"], ".x { color: red; }")
        self.assertEqual(back["look"]["layout"]["side_pos"], "right")
        # a hand edit of the :root block wins over the stale copy in the package
        edited = theme["css"].replace("--accent: #ff8800", "--accent: #00aa55")
        self.assertEqual(te.package_from_css(edited)["tokens"]["--accent"], "#00aa55")
        # saving again (same id) replaces, never duplicates the package block
        again = te.save_theme({"id": saved["id"], "name": "Night owl", "base": "dark", "css": theme["css"], "package": back})
        self.assertEqual(again["id"], saved["id"])
        self.assertEqual(themes.get_custom(saved["id"])["css"].count("@memu-editor"), 1)

    def test_editor_block_cannot_close_comment(self):
        block = te.editor_block(te.normalize_package({"name": "x", "css": "", "prefs": {}, "tokens": {"--a": "#000"}}))
        self.assertEqual(block.count("*/"), 1)
        evil = te.normalize_package({"name": "a */ b", "tokens": {}})
        self.assertEqual(te.editor_block(evil).count("*/"), 1)

    def test_style_tag_rejected(self):
        with self.assertRaises(ValueError):
            te.save_theme({"name": "x", "base": "dark", "css": ".a{}</style><script>alert(1)</script>"})


class AssetTest(Base):
    def test_store_and_read(self):
        url = te.store_asset("data:image/png;base64," + base64.b64encode(PNG).decode())
        self.assertRegex(url, te.ASSET_URL_RE)
        name = url.rsplit("/", 1)[-1]
        self.assertTrue(te.asset_path(name).is_file())
        self.assertEqual(te.store_asset("data:image/png;base64," + base64.b64encode(PNG).decode()), url)  # deduplicated
        self.assertTrue(te.asset_data_url(url).startswith("data:image/png;base64,"))

    def test_rejected(self):
        for bad in ("data:image/png;base64," + base64.b64encode(b"GIF89a....").decode(),   # wrong magic
                    "data:image/svg+xml;base64,PHN2Zz4=", "https://x.example/a.png", "",
                    "data:image/webp;base64," + base64.b64encode(b"RIFF0000JUNK").decode()):
            with self.assertRaises(ValueError, msg=bad[:40]):
                te.store_asset(bad)
        for name in ("../../etc/passwd", "a" * 20 + ".svg", "x.png", "A" * 20 + ".png"):
            self.assertIsNone(te.asset_path(name))


class RoutesTest(Base):
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        te.register(app)
        return TestClient(app)

    def test_routes(self):
        c = self.client()
        pkg = PackageTest.PKG
        code = c.post("/api/pg/theme-editor/share", json={"package": pkg}).json()["code"]
        parsed = c.post("/api/pg/theme-editor/parse", json={"text": code}).json()["package"]
        self.assertEqual(parsed["name"], "Night owl")
        bad = c.post("/api/pg/theme-editor/parse", json={"text": "nonsense"})
        self.assertEqual(bad.status_code, 400)
        self.assertTrue(CYR.search(bad.json()["error"]))
        self.assertFalse(CYR.search(bad.json()["error_en"]))
        self.assertEqual(c.post("/api/pg/theme-editor/parse", content=b"[]", headers={"Content-Type": "application/json"}).status_code, 400)
        saved = c.post("/api/pg/theme-editor/theme", json={"name": "Owl", "base": "dark", "css": ":root{--accent:#f80;}", "package": pkg}).json()["theme"]
        self.assertEqual(saved["package"]["tokens"]["--accent"], "#f80")
        got = c.post("/api/pg/theme-editor/package", json={"id": saved["id"]}).json()["package"]
        self.assertEqual(got["name"], "Owl")
        missing = c.post("/api/pg/theme-editor/package", json={"id": "nope"})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error_en"], "Theme not found")

    def test_asset_routes_and_export(self):
        c = self.client()
        url = c.post("/api/pg/theme-editor/asset", json={"data": "data:image/png;base64," + base64.b64encode(PNG).decode()}).json()["url"]
        r = c.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(r.content, PNG)
        self.assertEqual(c.get("/api/pg/theme-editor/asset/" + "f" * 20 + ".png").status_code, 404)
        self.assertEqual(c.get("/api/pg/theme-editor/asset/..%2F..%2Fx.png").status_code, 404)
        bad = c.post("/api/pg/theme-editor/asset", json={"data": "data:text/html;base64,PGI+"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["error_en"], "A PNG, JPEG or WebP image is needed")
        # export inlines the image; importing that file stores it again
        exp = c.post("/api/pg/theme-editor/export", json={"package": {"name": "Pic", "look": {"bg": {"kind": "image", "image": url}}}}).json()["package"]
        self.assertTrue(exp["image_data"].startswith("data:image/png;base64,"))
        imp = c.post("/api/pg/theme-editor/parse", json={"text": json.dumps(exp)}).json()["package"]
        self.assertEqual(imp["look"]["bg"]["image"], url)
        self.assertNotIn("image_data", imp)

    def test_every_error_has_english(self):
        for ru, en in te.ERRORS_EN.items():
            self.assertTrue(CYR.search(ru))
            self.assertFalse(CYR.search(en), en)
        self.assertEqual(te.error_body("Не удалось сохранить тему: disk full")["error_en"], "Could not save the theme: disk full")
        self.assertFalse(CYR.search(te.error_body("Неизвестно что")["error_en"]))


class FrontendStaticTest(unittest.TestCase):
    def test_wired(self):
        self.assertIn("theme-editor.js", INDEX)
        self.assertIn("theme-editor.css", INDEX)
        self.assertTrue((STATIC / "theme-editor.css").is_file())
        self.assertIn("app.theme_editor", (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))
        self.assertIn("theme_editor", (ROOT / "build.py").read_text(encoding="utf-8"))
        self.assertIn("theme_editor", (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8"))

    def test_node_check(self):
        try:
            res = subprocess.run(["node", "--check", str(STATIC / "theme-editor.js")], capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            self.skipTest("node is not installed")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_fixed_bugs_stay_fixed(self):
        # (b) the «other items» rule must not outrank the mobile menu (chained :not(#id) is very specific)
        self.assertIn(":not(#mobile-menu) { order:", JS)
        # (a) dragging a row scrolls the inspector and indexes by row centres, not by the raw pointer delta
        drag = JS[JS.index("function dragRow"):JS.index("/* ===================================================================== popovers")]
        self.assertIn("scroller.scrollTop", drag)
        self.assertIn("pointercancel", drag)
        self.assertIn("mids", drag)
        # (c) a floating panel gets an explicit height that fits the window
        place = JS[JS.index("function placePanel"):JS.index("function setDock")]
        self.assertIn("panel.style.height", place)
        self.assertIn("innerHeight", place)
        # errors in the English UI come from error_en and never contain Cyrillic
        self.assertIn("data.error_en", JS)

    def test_toolbar_ids_match_backend(self):
        ids = re.findall(r'\{ id: "([a-z]+)", sel: "\.topbar > ', JS)
        self.assertEqual(tuple(ids), te.TOOLBAR_ITEMS)
        hide = JS[JS.index("const HIDE_SEL = {"):JS.index("};", JS.index("const HIDE_SEL = {"))]
        self.assertEqual(set(re.findall(r"\b([a-z]+):", hide)), set(te.HIDEABLE))


def _chromium():
    path = os.environ.get("CHROMIUM", "/usr/bin/chromium")
    return path if os.path.exists(path) else ""


@unittest.skipUnless(_chromium() and os.environ.get("R33_THEME_BROWSER", "1") != "0", "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    def test_browser_flow(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright unavailable")
        out = Path(tempfile.mkdtemp(prefix="r33-theme-"))
        env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", PYTHONUTF8="1")
        res = subprocess.run([sys.executable, str(ROOT / "tests" / "round33_theme_browser.py"), str(out)],
                             capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, res.stdout[-4000:] + res.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
