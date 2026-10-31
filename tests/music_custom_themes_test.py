"""Custom themes in the MemuAI music module: the shared JS logic, the
assembled package and the module persist store.

Custom themes live inside the module's ordinary persisted ``prefs``
(``persist/music.json`` under the modules root, written by ``Memu.save``),
so no new host mechanism is needed: the shared module store already
exists in ``app/modules_host.py`` (``save_persistent``/``load_persistent``).

The JS checks run in node with stubbed browser globals; the harness is
written to a temporary directory, never into the repository.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import modules_host as host  # noqa: E402
from app.modules_host import load_package, load_persistent, save_persistent  # noqa: E402

MUSIC = ROOT / "app" / "module_builtins" / "music"

HARNESS = r'''"use strict";
const fs = require("fs");
const vm = require("vm");
const WEB = process.argv[2];
const custom = require(WEB + "/custom-themes.js");
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b);
  if (x !== y) throw new Error(label + ": expected " + y + ", got " + x);
}
function throws(fn, key, label) {
  try { fn(); } catch (e) { if (e.message !== key) throw new Error(label + ": wrong error " + e.message); return; }
  throw new Error(label + ": no error thrown");
}
const theme = custom.normalize({ name: "  Ocean 2 ", h: 200, s: 50, acc: ["#1fb2ff", "#3a6bff", "#2fd8c8"],
  light: [95, 99], dark: [6, 10, 0.5], tokens: { dark: { "--mm-bg": "#101418" } } });
eq(theme.format, "memumusic-theme", "format");
eq(theme.name, "Ocean 2", "name trimmed");
eq(theme.id.startsWith("custom:"), true, "custom id");
eq(custom.toBuiltin(theme).acc, ["#1fb2ff", "#3a6bff", "#2fd8c8"], "accents");
eq(custom.parseImport(custom.exportText(theme)), theme, "export/import round-trip");
const fromMemu = custom.parseImport(JSON.stringify({ format: "memuai-theme", schema: 1, name: "Memu",
  base: "dark", author: "A", tokens: { "--bg": "#202020", "--panel": "#2a2a2a", "--text": "#eeeeee", "--accent": "#ff8800" },
  elements: { x: 1 }, look: { layout: {} }, prefs: {}, css: "body{}" }));
eq(fromMemu.tokens.dark["--mm-bg"], "#202020", "memuai bg mapped");
eq(fromMemu.tokens.dark["--mm-surface"], "#2a2a2a", "memuai panel mapped");
eq(fromMemu.tokens.dark["--mm-text"], "#eeeeee", "memuai text mapped");
eq(fromMemu.acc[0], "#ff8800", "memuai accent mapped");
eq(fromMemu.elements, undefined, "memuai-only fields ignored");
throws(() => custom.parseImport("not json"), "ui.custom.err.json", "bad json");
throws(() => custom.parseImport('{"format":"other","name":"x"}'), "ui.custom.err.format", "bad format");
throws(() => custom.parseImport('{"name":""}'), "ui.custom.err.name", "empty name");
throws(() => custom.normalize({ name: "x", acc: ["red"] }), "ui.custom.err.color", "bad colour");
throws(() => custom.normalize({ name: "x", h: "soon" }), "ui.custom.err.range", "bad number");
const safe = custom.normalize({ name: "x", tokens: { dark: { "--mm-bg": "red; background:url(evil)", "--mm-text": "#112233", "--nope": "#000" } } });
eq(safe.tokens.dark, { "--mm-text": "#112233" }, "unsafe tokens dropped");
eq(custom.loadList([theme, { name: "" }, theme, "junk"]).length, 1, "loadList drops damaged");
let items = custom.upsert([], theme);
items = custom.upsert(items, Object.assign({}, theme, { name: "Renamed" }));
eq(items.length, 1, "upsert replaces");
eq(items[0].name, "Renamed", "upsert rename");
eq(custom.remove(items, theme.id).length, 0, "remove");
eq(custom.fallbackId(items, theme.id), theme.id, "fallback keeps active");
eq(custom.fallbackId([], "custom:x"), "cover", "fallback to cover");
eq(custom.fallbackId(custom.remove(items, theme.id), theme.id), "cover", "fallback after delete");
const full = Array.from({ length: 48 }, (_, i) => custom.normalize({ name: "t" + i }));
throws(() => custom.upsert(full, { name: "one more" }), "ui.custom.err.limit", "limit");
console.log("pure checks OK");

const vars = {};
const root = {
  style: { setProperty(k, v) { vars[k] = v; }, colorScheme: "" },
  setAttribute(k, v) { this["attr:" + k] = v; },
  getAttribute(k) { return k === "data-theme" ? "dark" : this["attr:" + k]; },
  classList: { add() {}, remove() {}, toggle() {} },
  toggleAttribute() {},
};
const sandbox = {
  console, Memu: undefined,
  App: { lang: "ru", setLanguage() {} },
  window: {}, document: { getElementById: () => null, querySelector: () => null, documentElement: root, body: {} },
  matchMedia: () => ({ matches: true, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  root,
  S: { prefs: { customThemes: [theme, custom.normalize({ name: "Dyn", h: 10, s: 60, dyn: true }),
    custom.normalize({ name: "Light", h: 200, s: 50, tokens: { dark: {}, light: { "--mm-text": "#123456" } } })],
    ui: { theme: "cover", mode: "dark" } } },
  savePrefs() {}, applyPalette() {}, applyCalm() {},
  Player: { current: () => null },
  NP: {}, Fx: { reduce: { addEventListener() {} } },
  SETTINGS_TABS: [], ambient: null, coverArt: () => "",
  $: () => null, $$: () => [],
  esc: (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]),
  t: (k) => k, debounce: (f) => f, icon: () => "",
};
sandbox.window.matchMedia = sandbox.matchMedia;
vm.createContext(sandbox);
const art = fs.readFileSync(WEB + "/art.js", "utf8");
const ct = fs.readFileSync(WEB + "/custom-themes.js", "utf8");
const themeJs = fs.readFileSync(WEB + "/theme.js", "utf8");
vm.runInContext(art + "\n" + ct + "\n" + themeJs + "\n;globalThis.__Theme = Theme;", sandbox);
const Theme = sandbox.__Theme;
Theme.ui.theme = "midnight";
Theme.apply();
eq([...Theme.accents()], ["#7c5cff", "#ff5c8a", "#20c7b5"], "builtin accents unchanged");
eq(Theme.coverAccent(), false, "builtin coverAccent");
eq(Theme.coverSoft(), true, "builtin coverSoft");
const midnightBg = vars["--mm-bg"];
Theme.ui.theme = theme.id;
Theme.apply();
eq(vars["--mm-bg"], "#101418", "explicit token wins");
eq(vars["--mm-bg"] === midnightBg, false, "custom background differs");
eq([...Theme.accents()], ["#1fb2ff", "#3a6bff", "#2fd8c8"], "custom accents");
eq(Theme.coverAccent(), false, "custom accents win over cover");
const dyn = sandbox.S.prefs.customThemes[1];
Theme.ui.theme = dyn.id;
Theme.apply();
eq(Theme.coverAccent(), true, "dyn custom follows cover");
Theme.ui.theme = "custom:gone";
Theme.apply();
eq([...Theme.accents()], ["#7c5cff", "#ff5c8a", "#20c7b5"], "unknown custom id falls back");
Theme.ui.mode = "light";
Theme.ui.theme = sandbox.S.prefs.customThemes[2].id;
Theme.apply();
eq(vars["--mm-text"], "#123456", "explicit light token applies");
let captured = "";
const hostStub = { addEventListener() {}, querySelector() { return { addEventListener() {} }; } };
Object.defineProperty(hostStub, "innerHTML", { get() { return captured; }, set(v) { captured = v; } });
Theme.render(hostStub);
if (!captured.includes("ui.theme.midnight_hint")) throw new Error("builtin theme hint missing from render");
if (captured.includes("<span>}</span>")) throw new Error("broken card template in render");
if (!captured.includes("ui.custom.title")) throw new Error("custom themes group missing from render");
if (!captured.includes(theme.id)) throw new Error("custom theme card missing from render");
if (!captured.includes("ct-file")) throw new Error("import input missing from render");
console.log("render checks OK");
console.log("theme.js checks OK");
'''

NODE = shutil.which("node")
THEME = {"format": "memumusic-theme", "schema": 1, "id": "custom:sea", "name": "Sea",
         "author": "", "description": "", "base": "dark", "extends": "ocean",
         "h": 200, "s": 50, "acc": ["#1fb2ff", "#3a6bff", "#2fd8c8"],
         "dyn": False, "mono": False, "light": [95, 99], "dark": [6, 10, 0.5],
         "tokens": {"dark": {}, "light": {}}}
I18N_KEYS = ("ui.custom.title", "ui.custom.new", "ui.custom.import", "ui.custom.edit",
             "ui.custom.duplicate", "ui.custom.export", "ui.custom.delete", "ui.custom.name",
             "ui.custom.err.json", "ui.custom.err.object", "ui.custom.err.format",
             "ui.custom.err.name", "ui.custom.err.color", "ui.custom.err.range",
             "ui.custom.err.limit", "ui.custom.saved", "ui.custom.deleted",
             "ui.custom.duplicated", "ui.custom.imported")


class MusicCustomThemesJsTests(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is required")
    def test_custom_themes_logic_and_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "custom_themes_harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run([NODE, str(harness), str(MUSIC)],
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pure checks OK", result.stdout)
        self.assertIn("theme.js checks OK", result.stdout)
        self.assertIn("render checks OK", result.stdout)

    @unittest.skipUnless(NODE, "node is required")
    def test_js_files_parse(self):
        for name in ("main.js", "theme.js", "custom-themes.js"):
            result = subprocess.run([NODE, "--check", str(MUSIC / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(NODE, "node is required")
    def test_assembled_package_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = load_package(Path(tmp), "music")
            path = Path(tmp) / "module.js"
            path.write_text(row["js"], encoding="utf-8")
            result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
        self.assertIn("CustomThemes", row["js"])
        self.assertIn("ctEditorHtml", row["js"])
        self.assertIn(".mm-ct-card", row["css"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_i18n_has_custom_theme_keys(self):
        data = json.loads((MUSIC / "i18n.json").read_text(encoding="utf-8"))
        for lang in ("ru", "en"):
            for key in I18N_KEYS:
                self.assertIn(key, data[lang], f"{lang}:{key}")

    def test_manifest_persist_covers_prefs(self):
        """The shared module store key the themes ride on."""
        with tempfile.TemporaryDirectory() as tmp:
            row = load_package(Path(tmp), "music")
        self.assertIn("prefs", row["persist"])


class MusicCustomThemesStoreTests(unittest.TestCase):
    def test_custom_themes_round_trip_through_module_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_persistent(root, "music", {"prefs": {"customThemes": [THEME],
                                                             "ui": {"theme": THEME["id"]}}})
            self.assertEqual(saved["prefs"]["customThemes"], [THEME])
            self.assertEqual(load_persistent(root, "music")["prefs"]["customThemes"], [THEME])
            # The data lives in the modules root, never inside the package.
            self.assertTrue((root / "persist" / "music.json").is_file())
            self.assertFalse((MUSIC / "persist").exists())

    def test_package_saved_state_carries_custom_themes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_persistent(root, "music", {"prefs": {"customThemes": [THEME]}})
            row = load_package(root, "music")
        self.assertEqual(row["saved"]["prefs"]["customThemes"], [THEME])

    def test_damaged_stored_themes_do_not_break_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_persistent(root, "music", {"prefs": {"customThemes": ["junk", {"name": ""}]}})
            row = load_package(root, "music")
        self.assertEqual(row["saved"]["prefs"]["customThemes"], ["junk", {"name": ""}])


if __name__ == "__main__":
    unittest.main()
