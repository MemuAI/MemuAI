"""Round 30 (r30-design): appearance settings, themes, Planning and Settings → Hotkeys regressions.

* the extra appearance preferences (corners, glass, window material, motion, message size, contrast) and the
  density are saved, validated and loaded by pgdata; every one of them syncs to the other windows;
* the three new theme pairs (slate / rose / evergreen) exist in both modes and pass WCAG AA with every accent;
* Planning: «How it works» toggles its card both ways; plan-ui menus stay inside the window even when the
  anchor sits at the bottom of a small window; the 24-hour time parser; conflict warnings fold into one line;
* Settings → Hotkeys: a second click on another shortcut while one is recording starts recording there;
  Tab while recording leaves the field instead of recording «Tab»;
* Settings → Appearance: a changed setting is on <html> at once and still there after a reload.

    cd /workspace/work/r29/memuai && python -m unittest tests.round30_design_test
The browser part runs the real MemuAI window (local servers + an offline model mock) in headless Chromium and
is skipped when Playwright / Chromium are not installed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r30-design-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import appearance_extra, pgdata, themes  # noqa: E402
from appearance_preferences_test import ratio  # noqa: E402
from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402

NEW_THEMES = ("slate", "rose", "evergreen")

class AppearancePreferences(unittest.TestCase):
    def test_valid_values_survive_save_and_load(self):
        values = {"ui_radius": "round", "ui_glass": "strong", "ui_backdrop": "mica", "ui_motion_speed": "fast",
                  "ui_motion_feel": "springy", "ui_msg_anim": "pop", "ui_msg_size": "lg", "ui_contrast": "more",
                  "ui_density": "compact", "ui_motion": "reduced"}
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            pgdata.save_preferences({"profile_name": "Alex"})
            saved = pgdata.save_preferences(values)
            loaded = pgdata.load_preferences()
        self.assertEqual(loaded, saved)
        for key, value in values.items():
            self.assertEqual(loaded[key], value, key)
        self.assertEqual(loaded["profile_name"], "Alex")

    def test_unknown_values_fall_back_to_defaults(self):
        junk = {key: "nope" for key in appearance_extra.CHOICES}
        junk.update({"ui_radius": ["round"], "ui_glass": None, "ui_density": "huge"})
        p = pgdata._normalize_preferences(junk)
        for key, choices in appearance_extra.CHOICES.items():
            self.assertEqual(p[key], choices[0], key)
        self.assertEqual(p["ui_density"], "")
        self.assertEqual(p["ui_backdrop"], "none")

    def test_every_appearance_key_syncs_to_other_windows(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        block = js.split("const PREF_SYNC_APPEARANCE = new Set([", 1)[1].split("]);", 1)[0]
        keys = set(re.findall(r'"([a-z_]+)"', block))
        for key in list(appearance_extra.CHOICES) + ["ui_density", "ui_motion", "theme", "accent_color", "ui_design"]:
            self.assertIn(key, keys)

    def test_settings_page_offers_density_and_motion(self):
        ap = (STATIC / "appearance.js").read_text(encoding="utf-8")
        self.assertIn('{ id: "compact", ru: "Компактно", en: "Compact" }', ap)
        self.assertIn('{ id: "spacious", ru: "Просторно", en: "Spacious" }', ap)
        plus_css = (STATIC / "appearance-plus.css").read_text(encoding="utf-8")
        for density in ("compact", "spacious"):
            for sel in (".chat-item", ".sx-shell .sx-row", ".msx-item", ".hk-row"):
                self.assertIn(f'html:root[data-density="{density}"] {sel}', plus_css)
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="st-motion"', app)
        self.assertIn("prefers-reduced-motion: reduce", (STATIC / "design.css").read_text(encoding="utf-8"))

    def test_cache_versions(self):
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        for asset in ("panels.js", "panels.css", "plan.js", "plan.css", "plan-ui.js", "hotkeys.js", "hotkeys.css",
                      "search.js", "search.css", "appearance.js", "appearance-plus.js", "appearance-plus.css",
                      "panels-telegram.js"):
            m = re.search(r'/' + re.escape(asset) + r'\?v=([^"]+)"', index)
            self.assertTrue(m, asset)

            self.assertTrue(re.search(r"-r(\d+)$", m.group(1)) and int(re.search(r"-r(\d+)$", m.group(1)).group(1)) >= 30, (asset, m.group(1)))

class Themes(unittest.TestCase):
    def test_new_pairs_exist_in_both_modes(self):
        for mode in ("dark", "light"):
            ids = [row["id"] for row in themes.VARIANTS[mode]]
            for theme in NEW_THEMES:
                self.assertIn(theme, ids, (mode, theme))
                self.assertEqual(themes.normalize_variant(mode, theme), theme)
        css = themes.theme_css()
        for mode in ("dark", "light"):
            for theme in NEW_THEMES:
                self.assertIn(f':where([data-theme="{mode}"][data-surface="{theme}"])', css)

    def test_new_pairs_pass_aa_with_every_accent(self):
        failures = []
        for mode in ("dark", "light"):
            for theme in NEW_THEMES:
                tokens = themes.variant_tokens(mode, theme)
                for surface in ("--bg", "--panel", "--panel-2"):
                    pairs = [("text", tokens["--text"]), ("muted", tokens["--muted"])]
                    pairs += [(f"accent {k}", a[mode]) for k, a in themes.ACCENTS.items()]
                    failures += [f"{mode}/{theme} {label} on {surface}: {ratio(fg, tokens[surface]):.2f}"
                                 for label, fg in pairs if ratio(fg, tokens[surface]) < 4.5]
                failures += [f"{mode}/{theme} on-accent {k}" for k, a in themes.ACCENTS.items()
                             if ratio(tokens["--on-accent"], a[mode]) < 4.5]
        self.assertEqual(failures, [], "\n".join(failures))

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PlanUiNode(unittest.TestCase):
    def node(self, script: str):
        src = (STATIC / "plan-ui.js").read_text(encoding="utf-8")
        prelude = "globalThis.window = globalThis; globalThis.document = { addEventListener(){} };\n"
        out = subprocess.run(["node", "-e", prelude + src + "\n" + script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_time_parser(self):
        got = self.node("console.log(JSON.stringify(['9','930','0930','9:3','21.45','24:00','9:75','x','2359'].map(window.MemuPlanUI.parseTime)))")
        self.assertEqual(got, ["09:00", "09:30", "09:30", "09:30", "21:45", "", "", "", "23:59"])

    def test_date_follows_the_ui_language_not_the_os(self):
        """<input type=date> showed the Windows locale format; the field formats by the UI language."""
        got = self.node("const U = window.MemuPlanUI; console.log(JSON.stringify(["
                        "U.formatDate('2026-10-05', 'ru'), U.formatDate('2026-10-05', 'en'), U.formatDate('2026-02-30', 'ru'),"
                        "U.parseDate('05.10.2026', 'ru'), U.parseDate('5.10.26', 'ru'), U.parseDate('2026-10-05', 'en'),"
                        "U.parseDate('Oct 5, 2026', 'en'), U.parseDate('5 окт 2026', 'ru'), U.parseDate('10/5/2026', 'en'),"
                        "U.parseDate('5/10/2026', 'ru'), U.parseDate('31.02.2026', 'ru'), U.parseDate('7 мая 2027', 'ru'), U.parseDate('x', 'ru')]))")
        self.assertEqual(got, ["05.10.2026", "Oct 5, 2026", "", "2026-10-05", "2026-10-05", "2026-10-05",
                               "2026-10-05", "2026-10-05", "2026-10-05", "2026-10-05", "", "2027-05-07", ""])
        plan = (STATIC / "plan.js").read_text(encoding="utf-8")
        self.assertNotIn('type="date"', plan)
        self.assertIn("MemuPlanUI.enhanceDates(host)", plan)

@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class LiveWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        cls.servers = Servers().start()
        cls.http = httpx.Client(timeout=30, trust_env=False)
        cls.http.post(cls.servers.pg + "/api/pg/preferences", json={
            "profile_onboarded": True, "profile_name": "Alex", "language": "ru", "updates_enabled": False, "ui_motion": "reduced"})
        for i, name in enumerate(("Утренняя сводка", "Перерыв в игре")):
            cls.http.post(cls.servers.pg + "/api/plan/save", json={"lang": "ru", "task": {
                "id": f"t{i}abc", "name": name, "kind": "chat", "prompt": "x", "enabled": True,
                "rule": {"when": [{"type": "daily", "times": ["09:00"], "days": [1, 2, 3, 4, 5]}]}}})
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.http.close()
        cls.servers.stop()

    def open(self, width=1366, height=768):
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.wait_for_timeout(500)
        return page

    def test_plan_help_button_toggles_the_card(self):
        page = self.open()
        page.evaluate("showSchedule(true)")
        page.wait_for_selector("#pln-root [data-act='help-show']")
        state = lambda: page.evaluate("""() => ({open: !!document.querySelector('#pln-help'),
            aria: document.querySelector('#pln-root [data-act="help-show"]').getAttribute('aria-expanded')})""")
        first = state()
        self.assertEqual(first["aria"], "true" if first["open"] else "false")
        for _ in range(3):
            before = state()["open"]
            page.click("#pln-root [data-act='help-show']")
            page.wait_for_timeout(250)
            now = state()
            self.assertEqual(now["open"], not before)
            self.assertEqual(now["aria"], "true" if now["open"] else "false")
        if not state()["open"]:
            page.click("#pln-root [data-act='help-show']")
            page.wait_for_timeout(250)
        page.click("#pln-help [data-act='help-hide']")
        page.wait_for_timeout(250)
        self.assertFalse(state()["open"])

    def test_plan_menus_stay_inside_the_window(self):
        page = self.open(900, 600)
        page.evaluate("showSchedule(true)")
        page.wait_for_selector("#pln-root [data-act='new']")
        inside = """() => { const p = document.querySelector('.pln-pop'); if (!p) return null; const r = p.getBoundingClientRect();
          return {ok: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight, r: [r.left, r.top, r.right, r.bottom],
                  icon: p.querySelector('.pln-pop-ico')?.getBoundingClientRect().width || 0}; }"""
        page.click("#pln-root [data-act='new']")
        page.wait_for_timeout(300)
        got = page.evaluate(inside)
        self.assertTrue(got and got["ok"], got)
        self.assertLess(got["icon"], 40, got)
        page.keyboard.press("Escape")
        page.evaluate("MemuPlan.openBuilder({id: 'x1', name: '', kind: 'chat'})")
        page.wait_for_selector("#pln-builder [data-act='add-when']")

        page.evaluate("document.querySelector('#pln-builder [data-act=\"add-when\"]').scrollIntoView({block: 'end'})")
        page.click("#pln-builder [data-act='add-when']")
        page.wait_for_timeout(300)
        got = page.evaluate(inside)
        self.assertTrue(got and got["ok"], got)

    def test_date_field_reads_and_writes_iso(self):
        page = self.open()
        page.evaluate("(async () => { await showSchedule(true); MemuPlan.openBuilder({id: 'x3', name: '', kind: 'chat',"
                      " rule: {when: [{type: 'once', at: '2026-10-20T09:30'}]}}); })()")
        page.wait_for_selector("#pln-builder input[data-date]")
        field = "#pln-builder input[data-dt-part='date']"
        self.assertEqual(page.evaluate(f"[document.querySelector(\"{field}\").value, document.querySelector(\"{field}\").type]"),
                         ["20.10.2026", "text"])
        page.focus(field)
        page.wait_for_selector(".pln-cal .pln-cal-grid button.on")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        self.assertEqual(page.evaluate(f"document.querySelector(\"{field}\").dataset.iso"), "2026-10-21")
        page.fill(field, "1.11.2026")
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate(f"document.querySelector(\"{field}\").value"), "01.11.2026")
        summary = page.evaluate("document.querySelector('.pln-summary')?.textContent || ''")
        self.assertIn("1 ноября", summary)

    def test_conflict_warnings_fold_into_one_line(self):
        page = self.open()
        page.evaluate("(async () => { await showSchedule(true); MemuPlan.openBuilder({id: 'x2', name: '', kind: 'chat'}); })()")
        page.wait_for_selector("#pln-builder")
        page.wait_for_timeout(1200)
        warns = page.evaluate("[...document.querySelectorAll('.pln-summary .pln-warn li')].map((li) => li.textContent.trim())")
        same = [w for w in warns if "одновременно" in w]
        if same:
            self.assertEqual(len(same), 1, warns)
            self.assertIn("«Утренняя сводка»", same[0])

    def test_hotkeys_second_click_and_tab_while_recording(self):
        page = self.open()
        page.evaluate("showSettings(); openSettingsPage('hotkeys')")
        page.wait_for_selector("[data-hk-record]")
        ids = page.evaluate("[...document.querySelectorAll('[data-hk-record]')].slice(0, 2).map((b) => b.dataset.hkRecord)")
        self.assertEqual(len(ids), 2)
        rec = lambda i: page.evaluate(f"document.querySelector('[data-hk-record=\"{i}\"]').classList.contains('recording')")
        page.click(f"[data-hk-record='{ids[0]}']")
        self.assertTrue(rec(ids[0]))
        page.click(f"[data-hk-record='{ids[1]}']")
        page.wait_for_timeout(250)
        self.assertTrue(rec(ids[1]))
        self.assertFalse(rec(ids[0]))
        self.assertTrue(page.evaluate("MemuHotkeys.recording"))
        before = page.evaluate(f"document.querySelector('[data-hk-record=\"{ids[1]}\"]').getAttribute('aria-label')")
        page.keyboard.press("Tab")
        page.wait_for_timeout(250)
        self.assertFalse(page.evaluate("MemuHotkeys.recording"))
        self.assertNotEqual(page.evaluate("document.activeElement.tagName"), "BODY")
        prefs = self.http.get(self.servers.pg + "/api/pg/preferences").json()
        self.assertNotIn("Tab", json.dumps(prefs.get("hotkeys") or {}))
        self.assertEqual(page.evaluate(f"document.querySelector('[data-hk-record=\"{ids[1]}\"]').getAttribute('aria-label')"), before)

    def test_search_chips_stay_on_one_line(self):
        """Terminal's bracketed chips at 1366 px and every design in a narrow window used to wrap «Ещё» onto a second line."""
        for width, design in ((1366, "terminal"), (900, "brutal"), (900, "classic")):
            page = self.open(width, 720)
            page.evaluate(f"document.documentElement.dataset.design = '{design}'; MemuSearch.open('')")
            page.wait_for_selector(".msx-chips .msx-chip")
            page.wait_for_timeout(600)
            tops = page.evaluate("[...document.querySelectorAll('.msx-chips > *')].map((b) => b.offsetTop)")
            self.assertEqual(len(set(tops)), 1, (width, design, tops))
            labels = page.evaluate("[...document.querySelectorAll('.msx-chips > *')].map((b) => b.textContent.trim())")
            self.assertIn("Всё", labels[0])
            page.click("[data-msx-more]")
            page.wait_for_timeout(200)
            self.assertGreater(page.evaluate("document.querySelectorAll('.msx-more-pop button').length"), 0)
            page.close()

    def test_appearance_applies_at_once_and_survives_reload(self):
        page = self.open()
        page.evaluate("showSettings(); openSettingsPage('appearance')")
        page.wait_for_selector("input[name='ap-radius']", state="attached")
        for name, value, attr in (("ap-radius", "round", "radius"), ("appearance-density", "compact", "density"),
                                  ("ap-glass", "light", "glass"), ("ap-motion-speed", "fast", "motionSpeed")):
            page.click(f"label.sx-seg-opt:has(input[name='{name}'][value='{value}'])")
            page.wait_for_timeout(200)
            self.assertEqual(page.evaluate(f"document.documentElement.dataset.{attr}"), value, name)
        page.wait_for_timeout(800)
        prefs = self.http.get(self.servers.pg + "/api/pg/preferences").json()
        self.assertEqual((prefs["ui_radius"], prefs["ui_density"], prefs["ui_glass"], prefs["ui_motion_speed"]),
                         ("round", "compact", "light", "fast"))
        page.reload()
        page.wait_for_selector("body.boot-ready", timeout=90000)
        got = page.evaluate("({...document.documentElement.dataset})")
        self.assertEqual((got.get("radius"), got.get("density"), got.get("glass"), got.get("motionSpeed")),
                         ("round", "compact", "light", "fast"))
        self.http.post(self.servers.pg + "/api/pg/preferences", json={
            "ui_radius": "", "ui_density": "", "ui_glass": "", "ui_motion_speed": ""})

if __name__ == "__main__":
    unittest.main()
