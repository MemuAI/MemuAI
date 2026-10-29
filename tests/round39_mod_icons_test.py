"""Round 39: module icons — manifest icon/icon_bg, safe SVG, the person's icon for any module, the built-in set,
module app .ico, and the page (sidebar, module card, window bar, picker). No network.

Screenshots: MEMU_R39_SHOTS=<dir> (the browser test writes sidebar/cards/window/picker PNGs there).
"""
import base64
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r39-mod-icons-"))

from app import module_icons, modules_host, modules_validate  # noqa: E402

BUILTINS = ("translator", "studio", "design", "benchmarks", "catalog", "api-models", "characters", "music", "learn", "memudb")
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z" fill="#f80"/></svg>'

def _png(size=64, color=(200, 80, 120, 255)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()

def _data(raw: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()

class SanitizeTest(unittest.TestCase):
    def test_strips_everything_active(self):
        dirty = ('<svg width="10" height="12" onload="x()"><script>alert(1)</script><foreignObject><p>x</p></foreignObject>'
                 '<a href="http://evil"><path d="M0 0h10v10z" onclick="y()"/></a><image href="http://evil/x.png"/>'
                 '<use href="http://evil#a"/><use href="#ok"/><rect width="5" height="5" style="fill:url(http://x)" fill="url(#g)"/>'
                 '<style>@import url(http://x);</style><style>.a{fill:red}</style><circle r="2" fill="javascript:alert(1)"/></svg>')
        clean = module_icons.sanitize_svg(dirty)
        for bad in ("script", "foreignObject", "onload", "onclick", "http://evil", "http://x", "@import", "javascript"):
            self.assertNotIn(bad, clean)
        self.assertIn('viewBox="0 0 10 12"', clean)
        self.assertIn('<path d="M0 0h10v10z"/>', clean, "a link's drawing stays")
        self.assertIn('href="#ok"', clean)
        self.assertIn(".a{fill:red}", clean)
        self.assertNotIn("width=", clean.split(">")[0], "the root scales: no fixed width/height")

    def test_rejects(self):
        for bad in ("", "<html/>", "not svg", '<!DOCTYPE svg [<!ENTITY a "b">]><svg/>', "<svg><g/></svg>",
                    "<svg>" + "<path d='M0 0'/>" * 30000 + "</svg>"):
            with self.assertRaises(module_icons.IconError):
                module_icons.sanitize_svg(bad)

    def test_raster_data_image_allowed(self):
        clean = module_icons.sanitize_svg(f'<svg viewBox="0 0 4 4"><image href="{_data(_png(16))}" width="4" height="4"/></svg>')
        self.assertIn("data:image/png;base64", clean)

class ManifestTest(unittest.TestCase):
    def test_normalize_icon(self):
        n = module_icons.normalize_icon
        self.assertEqual(n("Music"), "music")
        self.assertEqual(n("icon.svg"), "icon.svg")
        self.assertEqual(n("assets/app.png"), "assets/app.png")
        self.assertNotIn("..", n("../x.svg"))
        self.assertNotIn("/", n("/etc/x.svg"))
        self.assertTrue(n(SVG).startswith("<svg"))
        self.assertEqual(n("<svg><script/></svg>"), "")
        self.assertEqual(n(_data(_png())), _data(_png()))
        self.assertEqual(n("data:text/html;base64,AAAA"), "")
        self.assertEqual(module_icons.normalize_icon_bg(["#AA0000", "bad", "#00ff00"]), ["#aa0000", "#00ff00"])
        self.assertEqual(module_icons.normalize_icon_bg("#123456"), ["#123456"])

    def test_validate_messages(self):
        levels = lambda m: [p[0] for p in module_icons.validate(m)]  # noqa: E731
        self.assertEqual(levels({"icon": "icon.svg"}), [])
        self.assertIn("warn", levels({"icon": "../icon.svg"}))
        self.assertIn("warn", levels({"icon": "<svg><g/></svg>"}))
        self.assertIn("warn", levels({"icon": "x", "icon_bg": ["red"]}))
        self.assertIn("warn", levels({"icon": "icon.svg"}) + [p[0] for p in module_icons.validate({"icon": "icon.svg"}, {"a.svg": ""})])
        report = modules_validate.validate_package({"manifest": {"id": "abc", "title": "A", "icon": 5},
                                                   "files": {"index.html": "<p>x</p>", "main.js": ""}}, check_syntax=False)
        self.assertTrue(any("icon" in str(p) for p in report.get("problems", [])))

    def test_builtins_ship_icons(self):
        for module_id in BUILTINS:
            folder = ROOT / "app" / "module_builtins" / module_id
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["icon"], "icon.svg", module_id)
            svg = (folder / "icon.svg").read_text(encoding="utf-8")
            self.assertEqual(module_icons.sanitize_svg(svg).count("<rect"), svg.count("<rect"), f"{module_id}: survives cleaning")
            self.assertIn('viewBox="0 0 512 512"', svg)
            self.assertIn('rx="118"', svg, "the MemuNote/MemuFiles squircle")
            from PIL import Image
            with Image.open(folder / "icon.png") as image:
                self.assertEqual(image.size, (256, 256))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((2, 2))[3], 0, "transparent corners")
        self.assertEqual(set(BUILTINS), set(modules_host.BUILTIN_ORDER), "every built-in module has a drawn icon")

class HostTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="r39-mi-root-"))

    def _custom(self, module_id="mine", **extra):
        spec = {"id": module_id, "title": "Mine", "html": "<p>x</p>", "js": "", "css": "", **extra}
        return modules_host.create_module(self.root, spec)

    def test_rows_carry_icons(self):
        rows = {row["id"]: row for row in modules_host.list_modules(self.root)}
        self.assertEqual(rows["music"]["icon"], "music", "older code still gets a glyph name")
        self.assertEqual(rows["music"]["icon_src"], "icon.svg")
        self.assertTrue(rows["music"]["icon_url"].startswith("/api/pg/modules/music/icon?v="))
        self.assertFalse(rows["music"]["icon_custom"])
        self._custom(icon="bell", icon_bg=["#112233", "#445566"])
        row = next(r for r in modules_host.list_modules(self.root) if r["id"] == "mine")
        self.assertEqual((row["icon"], row["icon_url"], row["icon_bg"]), ("bell", "", ["#112233", "#445566"]))
        body, media = module_icons.icon_bytes(self.root, "mine")
        self.assertEqual(media, "image/svg+xml")
        self.assertIn(b"#112233", body, "a glyph-only module still answers with a picture (letter tile)")

    def test_inline_and_file_icons(self):
        self._custom("inl", icon=SVG)
        row = next(r for r in modules_host.list_modules(self.root) if r["id"] == "inl")
        self.assertTrue(row["icon_url"])
        self.assertEqual(module_icons.icon_bytes(self.root, "inl"), (SVG.encode(), "image/svg+xml"))
        self._custom("fil", icon="icon.svg")
        (self.root / "custom" / "fil" / "icon.svg").write_text(SVG.replace("#f80", "#0f8"), encoding="utf-8")
        body, _ = module_icons.icon_bytes(self.root, "fil")
        self.assertIn(b"#0f8", body)
        exported = modules_host.export_package(self.root, "fil")
        self.assertTrue(exported["manifest"]["icon"].startswith("<svg"), "export inlines a file icon")
        imported = modules_host.import_package(self.root, exported, new_id="fil-copy")
        self.assertTrue(imported["icon_url"])
        dup = modules_host.duplicate_module(self.root, "music", "music-copy")
        self.assertTrue(dup["icon_url"], "a copy of a built-in keeps its drawn icon")

    def test_user_icon_set_reset_delete(self):
        meta = module_icons.set_user_icon(self.root, "music", SVG, raster=_data(_png(256)), kind="glyph",
                                          meta={"glyph": "music", "bg": "memu", "evil": "<x>"})
        self.assertEqual(meta["ext"], "svg")
        self.assertEqual(meta["meta"], {"glyph": "music", "bg": "memu"})
        row = next(r for r in modules_host.list_modules(self.root) if r["id"] == "music")
        self.assertTrue(row["icon_custom"])
        self.assertIn("v=u", row["icon_url"])
        self.assertEqual(module_icons.icon_bytes(self.root, "music")[0], SVG.encode())
        self.assertEqual(module_icons.icon_bytes(self.root, "music", own=True)[0][:4], b"<svg")
        self.assertNotEqual(module_icons.icon_bytes(self.root, "music", own=True)[0], SVG.encode())
        self.assertEqual(module_icons.raster_for(self.root, "music").getpixel((128, 128)), (200, 80, 120, 255))

        from PIL import Image
        for fmt, mime in (("ICO", "image/x-icon"), ("WEBP", "image/webp"), ("PNG", "image/png")):
            buffer = io.BytesIO()
            Image.new("RGBA", (48, 32), (10, 20, 30, 255)).save(buffer, format=fmt)
            module_icons.set_user_icon(self.root, "studio", _data(buffer.getvalue(), mime))
            body, media = module_icons.icon_bytes(self.root, "studio")
            self.assertEqual(media, "image/png")
            with Image.open(io.BytesIO(body)) as image:
                self.assertEqual(image.width, image.height, f"{fmt}: squared")
        with self.assertRaises(module_icons.IconError):
            module_icons.set_user_icon(self.root, "studio", _data(b"GIF89a" + b"0" * 40, "image/gif"))
        with self.assertRaises(KeyError):
            module_icons.set_user_icon(self.root, "nope", SVG)
        with self.assertRaises(ValueError):
            module_icons.set_user_icon(self.root, "../x", SVG)
        module_icons.reset_user_icon(self.root, "music")
        row = next(r for r in modules_host.list_modules(self.root) if r["id"] == "music")
        self.assertFalse(row["icon_custom"])
        self.assertEqual(sorted(p.name for p in (self.root / "icons").glob("music.*")), [])
        self._custom()
        module_icons.set_user_icon(self.root, "mine", SVG)
        modules_host.delete_module(self.root, "mine")
        self.assertNotIn("mine", module_icons.load_user(self.root))

    def test_app_ico_uses_module_icon(self):
        from app import module_apps
        out = module_apps.write_icon("music", {"title": "MemuMusic"}, "", self.root)
        from PIL import Image
        with Image.open(out["png"]) as image:
            r, g, b, a = image.convert("RGBA").getpixel((128, 60))
            self.assertEqual(a, 255)
            self.assertGreater(r, 200, "the drawn MemuMusic tile, not the letter tile in the accent colour")
        self.assertTrue(Path(out["ico"]).stat().st_size > 1000)

class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        cls.root = Path(tempfile.mkdtemp(prefix="r39-mi-api-"))
        app = FastAPI()
        module_icons.register(app, root_fn=lambda: cls.root)
        cls.client = TestClient(app)

    def test_icon_and_headers(self):
        response = self.client.get("/api/pg/modules/music/icon?v=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertEqual(self.client.get("/api/pg/modules/..%2Fx/icon").status_code, 404)
        self.assertEqual(self.client.get("/api/pg/modules/nope/icon").status_code, 404)
        self.assertEqual(self.client.get("/api/pg/module-icons/builtin/design.svg").status_code, 200)
        self.assertEqual(self.client.get("/api/pg/module-icons/builtin/nope.svg").status_code, 404)

    def test_set_reset_sanitize(self):
        bad = self.client.post("/api/pg/module-icons/set", json={"id": "music", "data": "<svg><script/></svg>"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/pg/module-icons/set", json={"id": "music", "data": SVG.replace("<path", "<script>x</script><path"),
                                                               "kind": "file"})
        self.assertEqual(ok.status_code, 200, ok.text)
        served = self.client.get("/api/pg/modules/music/icon").text
        self.assertNotIn("script", served)
        self.assertEqual(self.client.post("/api/pg/module-icons/set", json={"id": "zzz", "data": SVG}).status_code, 404)
        self.assertEqual(self.client.post("/api/pg/module-icons/reset", json={"id": "music"}).json(), {"ok": True})
        self.assertIn("<svg", self.client.post("/api/pg/module-icons/sanitize", json={"svg": SVG}).json()["svg"])

class StaticTest(unittest.TestCase):
    def test_wiring(self):
        static = ROOT / "app" / "pgstatic"
        index = (static / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("/module-icons.js?v="), index.index("/modules.js?v="))
        self.assertIn("/module-icons.css?v=", index)
        modules = (static / "modules.js").read_text(encoding="utf-8")
        self.assertNotIn("moduleIcon(row.icon)", modules, "every module row draws its own icon")
        self.assertIn('data-module-more="${esc(row.id)}"', modules)
        self.assertIn('if (row) moreMenu(event, row);', modules)
        self.assertIn('t("Сменить иконку…", "Change icon…")', modules)
        self.assertEqual(modules.count("MemuModuleIcon.picker(row)"), 2, "launcher menu + card «More»")
        apps = (static / "module-apps.js").read_text(encoding="utf-8")
        self.assertIn("MemuModuleIcon.png(fresh, 256)", apps)
        js = (static / "module-icons.js").read_text(encoding="utf-8")
        for needed in ("setInterval", "MutationObserver"):
            self.assertNotIn(needed, js)

def _chromium():
    path = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    return path if Path(path).exists() else None

@unittest.skipUnless(_chromium(), "no chromium")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import pgdata
        from app.playground_api import create_app
        cls.root = modules_host.default_root()
        cls.root.mkdir(parents=True, exist_ok=True)
        for module_id in BUILTINS:
            modules_host.configure_module(cls.root, module_id, enabled=True)
        modules_host.create_module(cls.root, {"id": "r39-glyph", "title": "Заметки", "title_en": "Notes", "icon": "list",
                                              "html": "<p>x</p>", "js": "", "css": ""}) if not (cls.root / "custom" / "r39-glyph").exists() else None
        modules_host.configure_module(cls.root, "r39-glyph", enabled=True)
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru", "theme": "dark"})
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 60
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.1)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.shots = Path(os.environ["MEMU_R39_SHOTS"]) if os.environ.get("MEMU_R39_SHOTS") else None
        if cls.shots:
            cls.shots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def shot(self, page, name, **kw):
        if self.shots:
            page.screenshot(path=str(self.shots / f"{name}.png"), **kw)

    def test_icons_everywhere_and_picker(self):
        from app import pgdata
        for theme in ("dark", "light"):
            pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": "ru", "theme": theme})
            page = self.browser.new_page(viewport={"width": 1366, "height": 820}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
            page.wait_for_function("() => document.querySelectorAll('.module-launch .mico').length >= 9", timeout=20000)
            page.wait_for_function("() => [...document.querySelectorAll('.module-launch .mico img')].every(i => i.complete)", timeout=10000)
            info = page.evaluate("""() => [...document.querySelectorAll('.module-launch .mico')].map(n => ({
                id: n.dataset.mico, img: !!n.querySelector('img'), ok: n.querySelector('img') ? n.querySelector('img').complete && n.querySelector('img').naturalWidth > 0 : true,
                w: n.getBoundingClientRect().width }))""")
            self.assertTrue(all(row["ok"] for row in info), info)
            self.assertTrue(all(row["img"] for row in info if row["id"] in BUILTINS), info)
            self.assertFalse(next(row for row in info if row["id"] == "r39-glyph")["img"], "a glyph-only module: the squircle")
            self.assertTrue(all(abs(row["w"] - 20) < 0.6 for row in info), info)
            page.wait_for_timeout(900)
            self.shot(page, f"sidebar-{theme}", clip={"x": 0, "y": 0, "width": 300, "height": 820})

            page.evaluate("() => MemuModules.open('music')")
            page.wait_for_selector(".module-window .module-window-icon .mico img, .mhb-icon .mico img", timeout=15000)
            self.shot(page, f"window-{theme}")
            page.evaluate("() => MemuModules.closeAll && MemuModules.closeAll()")

            page.evaluate("() => { currentSettingsTab = 'modules'; showSettings(); }")
            page.wait_for_selector(".ms-module-icon .mico", timeout=15000)
            page.wait_for_timeout(500)
            self.shot(page, f"cards-{theme}")
            if theme == "dark":
                page.evaluate("() => MemuModuleIcon.picker(MemuModules.catalog.find(r => r.id === 'r39-glyph'))")
                page.wait_for_selector(".mico-dialog [data-mico-preset]")
                page.wait_for_timeout(400)
                self.shot(page, "picker-set")
                page.click("[data-mico-preset='music']")
                page.wait_for_function("() => !document.querySelector('#mico-save').disabled")
                page.click("[data-mico-tab='emoji']")
                page.click("[data-mico-grad='mint']")
                page.wait_for_timeout(300)
                self.shot(page, "picker-emoji")
                page.click("#mico-save")
                page.wait_for_function("() => (MemuModules.catalog.find(r => r.id === 'r39-glyph') || {}).icon_custom === true", timeout=10000)
                page.wait_for_function("() => !!document.querySelector('.module-launch [data-mico=\"r39-glyph\"] img')", timeout=10000)
                png = page.evaluate("() => MemuModuleIcon.png(MemuModules.catalog.find(r => r.id === 'r39-glyph'), 64)")
                self.assertTrue(png.startswith("data:image/png;base64,"))
                self.assertIn("r39-glyph", module_icons.load_user(self.root))
                self.assertTrue((self.root / "icons" / "r39-glyph.256.png").is_file(), "the raster for the app .ico")
                module_icons.reset_user_icon(self.root, "r39-glyph")
            self.assertEqual(errors, [])
            page.close()

if __name__ == "__main__":
    unittest.main()
