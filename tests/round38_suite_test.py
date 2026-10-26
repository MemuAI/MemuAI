"""Round 38 (r38-suite): MemuNote / MemuFiles settings in MemuAI; the Setup without «Что нового».

* One schema per app (app/memunote/settings_schema.py, app/memufiles/settings_schema.py) — the apps' settings.py
  derive defaults/choices/ranges from it; MemuAI validates writes with it (types, ranges, enums, local URLs).
* /api/pg/appsettings/<app> reads/writes the app's own settings.json; a write notifies a running instance
  (/api/remote action "settings" → page event) — both app servers and pages handle it, plus a focus re-check.
* Settings → «MemuNote» / «MemuFiles» show every visible preference, grouped, with a local filter; the global
  settings search finds them; a change made in the file shows up on focus; MemuFiles has «Интеграция с Windows»
  with r38-files' block. No network: MemuAI runs locally, nothing talks to a provider.

Screenshots: MEMU_R38_SHOTS=/workspace/work/shots/r38/r38-suite
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r38-suite-"))

def _chromium():
    path = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    return path if Path(path).exists() else ""

class SchemaTest(unittest.TestCase):
    def test_settings_use_the_schema(self):
        from app.memunote import settings as mn, settings_schema as mns
        from app.memufiles import settings as mf, settings_schema as mfs
        self.assertIs(mn.DEFAULTS, mns.DEFAULTS)
        self.assertIs(mf.DEFAULTS, mfs.DEFAULTS)
        for schema in (mns, mfs):
            groups = {g["id"] for g in schema.GROUPS}
            for item in schema.SCHEMA:
                self.assertIn(item["group"], groups, item["key"])
                if not item.get("hidden"):
                    self.assertTrue(item["ru"] and item["en"], item["key"])
                if item["type"] == "enum":
                    self.assertIn(item["default"], [c["value"] for c in item["choices"]], item["key"])
                if item["type"] in ("int", "float"):
                    self.assertTrue(item["min"] <= item["default"] <= item["max"], item["key"])
            self.assertTrue({"appearance", "ai", "hotkeys", "advanced"} <= groups)
            self.assertTrue(schema.SHORTCUTS)
        self.assertIn("windows", {g["id"] for g in mfs.GROUPS})

        self.assertIn("md_live_migrated", mns.DEFAULTS)
        self.assertEqual(mfs.DEFAULTS["pinned"], [])

    def test_strict_validation(self):
        from app.memunote import settings_schema as mns
        from app.memufiles import settings_schema as mfs
        clean, errors = mns.validate({"font_size": 50, "theme": "neon", "line_height": 1.8, "zoom": 120, "word_wrap": "yes",
                                      "proxy_url": "http://example.com", "spellcheck": True, "font_family": "a;b", "nope": 1})
        self.assertEqual(clean, {"line_height": 1.8, "zoom": 120})
        self.assertEqual(errors, {"font_size": "range", "theme": "choice", "word_wrap": "type", "proxy_url": "url",
                                  "spellcheck": "unknown_key", "font_family": "font", "nope": "unknown_key"})
        self.assertEqual(mns.validate({"zoom": 1.5})[1], {"zoom": "type"})
        self.assertEqual(mns.validate({"proxy_url": "http://127.0.0.1:8317"})[0], {"proxy_url": "http://127.0.0.1:8317"})
        clean, errors = mfs.validate({"density": "comfortable", "icon_size": 999, "pinned": [], "terminal": "wt"})
        self.assertEqual(clean, {"density": "comfortable", "terminal": "wt"})
        self.assertEqual(errors, {"icon_size": "range", "pinned": "unknown_key"})
        public = mfs.public()
        self.assertNotIn("view_by_folder", [i["key"] for i in public["items"]])

    def test_memufiles_load_checks_enums_and_ranges(self):
        from app.memufiles import settings as mf
        mf._path().write_text(json.dumps({"density": "huge", "icon_size": 5000, "show_hidden": True}), encoding="utf-8")
        value = mf.load()
        self.assertEqual(value["density"], "normal")
        self.assertEqual(value["icon_size"], 256)
        self.assertTrue(value["show_hidden"])
        self.assertEqual(mf.save({"terminal": "evil"})["terminal"], "auto")
        mf._path().unlink()

class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import app_settings_api
        app = FastAPI()
        app_settings_api.register(app)
        cls.client = TestClient(app)
        cls.api = app_settings_api

    def test_read_write_reset_and_notify(self):
        from app.memunote import desktop, settings as mn
        calls = []
        original = desktop._remote
        desktop._remote = lambda action, payload, timeout=3.0: calls.append(action) or {"ok": True}
        instance = desktop._instance_path()
        try:
            r = self.client.get("/api/pg/appsettings/memunote").json()
            self.assertTrue(r["ok"])
            keys = [i["key"] for i in r["schema"]["items"]]
            self.assertIn("word_wrap", keys)
            self.assertNotIn("md_live_migrated", keys)
            self.assertNotIn("md_live_migrated", r["values"])
            bad = self.client.post("/api/pg/appsettings/memunote", json={"changes": {"font_size": 99, "word_wrap": False}})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(bad.json()["errors"], {"font_size": "range"})
            self.assertTrue(mn.load()["word_wrap"], "nothing is written when one value is wrong")
            ok = self.client.post("/api/pg/appsettings/memunote", json={"changes": {"word_wrap": False, "font_size": 18}}).json()
            self.assertFalse(ok["values"]["word_wrap"])
            self.assertFalse(ok["notified"], "no running MemuNote: nothing to tell")
            self.assertEqual(json.loads(mn.path().read_text(encoding="utf-8"))["font_size"], 18)
            self.assertGreater(ok["mtime"], 0)
            instance.write_text(json.dumps({"port": 1, "token": "x"}), encoding="utf-8")
            ok = self.client.post("/api/pg/appsettings/memunote", json={"changes": {"theme": "light"}}).json()
            self.assertTrue(ok["notified"])
            for _ in range(50):
                if calls:
                    break
                time.sleep(0.02)
            self.assertEqual(calls, ["settings"])
            reset = self.client.post("/api/pg/appsettings/memunote/reset", json={"keys": ["word_wrap", "font_size"]}).json()
            self.assertTrue(reset["values"]["word_wrap"])
            self.assertEqual(reset["values"]["font_size"], 14)
            self.assertEqual(reset["values"]["theme"], "light")
            snap = self.client.get("/api/pg/appsettings/memunote?schema=0").json()
            self.assertNotIn("schema", snap)
            self.assertEqual(self.client.get("/api/pg/appsettings/other").status_code, 404)
        finally:
            desktop._remote = original
            instance.unlink(missing_ok=True)
            mn.path().unlink(missing_ok=True)

    def test_memufiles_keeps_app_state(self):
        from app.memufiles import settings as mf
        mf.save({"pinned": ["C:\\Work"], "view_by_folder": {"C:\\Work": {"view": "grid"}}})
        r = self.client.post("/api/pg/appsettings/memufiles", json={"changes": {"show_hidden": True}}).json()
        self.assertTrue(r["values"]["show_hidden"])
        self.assertNotIn("pinned", r["values"])
        self.assertEqual(mf.load()["pinned"], ["C:\\Work"], "pins survive a write from MemuAI")
        self.client.post("/api/pg/appsettings/memufiles/reset", json={})
        self.assertEqual(mf.load()["pinned"], ["C:\\Work"], "a full reset touches visible settings only")
        self.assertFalse(mf.load()["show_hidden"])
        mf._path().unlink()

class WiringTest(unittest.TestCase):
    def test_apps_apply_a_settings_event(self):
        for app in ("memunote", "memufiles"):
            server = (ROOT / "app" / app / "server.py").read_text(encoding="utf-8")
            self.assertIn('elif action == "settings":', server)
            self.assertIn('self.app.events.push({"type": "settings"})', server)
        note = (ROOT / "app/memunote/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn('if (ev.type === "settings") await reloadSettings();', note)
        self.assertIn('window.addEventListener("focus", () => reloadSettings());', note)
        files = (ROOT / "app/memufiles/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn('else if (ev.type === "settings") this.reloadSettings();', files)
        self.assertIn('window.addEventListener("focus", () => this.reloadSettings());', files)
        self.assertIn("app.applySettings(await api(\"settings\", changes));", (ROOT / "app/memufiles/static/js/dialogs.js").read_text(encoding="utf-8"))
        index = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("/app-settings-form.js"), index.index("/memunote-settings.js"))
        self.assertIn('href="/app-settings-form.css?v=1.0-r38"', index)
        self.assertIn("app_settings_api.register(app)", (ROOT / "app/playground_api.py").read_text(encoding="utf-8"))

    def test_setup_has_no_whats_new(self):
        ui = ROOT / "installer" / "ui"
        for name in ("index.html", "app.js", "style.css", "i18n.js", "mock.js"):
            text = (ui / name).read_text(encoding="utf-8")
            for needle in ("btn-notes", "notes-label", "showNotes", "notes-list", '"notes.'):
                self.assertNotIn(needle, text, f"{name}: {needle}")

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class BrowserTest(unittest.TestCase):
    SHOTS = os.environ.get("MEMU_R38_SHOTS", "")

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            import uvicorn
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright/uvicorn unavailable: {exc}")
        from app import pgdata
        from app.playground_api import create_app
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
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

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def wait(self, page, expression, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if page.evaluate(f"() => !!({expression})"):
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise AssertionError(f"timed out: {expression}")

    def open_page(self, page_id, lang="ru", theme="dark", size=(1280, 820)):
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "language": lang, "theme": theme})
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle")
        self.wait(page, "document.body.classList.contains('boot-ready') && typeof openSettingsPage === 'function'", 30)
        page.evaluate(f"() => openSettingsPage('{page_id}')")
        prefix = "mnset-" if page_id == "memunote" else "mfset-"
        self.wait(page, f"document.querySelector('#{prefix}groups .sx-row')")
        page.wait_for_timeout(400)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def shot(self, page, name, full=False):
        if self.SHOTS:
            Path(self.SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(self.SHOTS) / f"{name}.png"), full_page=full)

    def test_memunote_page_edits_and_syncs(self):
        from app.memunote import settings as mn, settings_schema as mns
        page = self.open_page("memunote")
        visible = [i for i in mns.SCHEMA if not i.get("hidden")]
        self.assertEqual(page.evaluate("() => document.querySelectorAll('#mnset-groups [data-sx-key]').length"), len(visible))
        titles = page.evaluate("() => [...document.querySelectorAll('#mnset-groups .sx-section-title > span:not([class])')].map(n => n.textContent)")
        self.assertEqual(titles, ["Внешний вид", "Редактор", "Файлы и сохранение", "ИИ", "Горячие клавиши", "Дополнительно"])
        self.assertGreater(page.evaluate("() => document.querySelectorAll('#mnset-g-hotkeys kbd').length"), 10)
        self.shot(page, "memunote-ru-dark")

        page.evaluate("() => { const t = document.getElementById('mnset-word_wrap'); t.checked = false; t.dispatchEvent(new Event('change', { bubbles: true })); }")
        self.wait(page, "true")
        deadline = time.time() + 5
        while time.time() < deadline and mn.load()["word_wrap"]:
            time.sleep(0.05)
        self.assertFalse(mn.load()["word_wrap"])

        self.assertTrue(page.evaluate("() => document.getElementById('mnset-row-autosave_delay').hidden"))
        page.evaluate("() => document.querySelector('input[name=\"mnset-autosave\"][value=\"delay\"]').click()")
        self.wait(page, "!document.getElementById('mnset-row-autosave_delay').hidden")
        deadline = time.time() + 5
        while time.time() < deadline and mn.load()["autosave"] != "delay":
            time.sleep(0.05)
        self.assertEqual(mn.load()["autosave"], "delay")

        time.sleep(0.05)
        mn.save({"line_numbers": False})
        page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        self.wait(page, "document.getElementById('mnset-line_numbers').checked === false")

        page.fill("#mnset-filter", "шрифт")
        self.wait(page, "document.getElementById('mnset-g-editor').classList.contains('appset-hide')")
        self.assertFalse(page.evaluate("() => document.getElementById('mnset-row-font_family').classList.contains('appset-hide')"))
        self.shot(page, "memunote-filter")
        page.fill("#mnset-filter", "")
        hits = page.evaluate("() => MemuSettingsKit.search('перенос строк').map(h => h.page)")
        self.assertIn("memunote", hits)
        mn.path().unlink(missing_ok=True)

    def test_memufiles_page_light_en(self):
        from app.memufiles import settings_schema as mfs
        page = self.open_page("memufiles", lang="en", theme="light")
        visible = [i for i in mfs.SCHEMA if not i.get("hidden")]
        self.assertEqual(page.evaluate("() => document.querySelectorAll('#mfset-groups [data-sx-key]').length"), len(visible))
        titles = page.evaluate("() => [...document.querySelectorAll('#mfset-groups .sx-section-title > span:not([class])')].map(n => n.textContent)")
        self.assertIn("Windows integration", titles)
        self.assertTrue(page.evaluate("() => !!document.querySelector('#mfset-g-windows #mfds-root')"), "r38-files' block sits in the group")

        self.assertEqual(page.evaluate("""() => [...document.querySelectorAll('#pane-memufiles *')]
            .filter(n => n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflowX !== 'visible' && n.clientWidth > 0 && !n.matches('input, select, textarea')).map(n => n.className)"""), [])
        self.shot(page, "memufiles-en-light")
        for group in ("hotkeys", "windows"):
            page.evaluate(f"() => document.getElementById('mfset-g-{group}').scrollIntoView({{ block: 'start' }})")
            page.wait_for_timeout(250)
            self.shot(page, f"memufiles-{group}")
        page.set_viewport_size({"width": 760, "height": 820})
        page.wait_for_timeout(300)
        self.shot(page, "memufiles-narrow")

if __name__ == "__main__":
    unittest.main()
