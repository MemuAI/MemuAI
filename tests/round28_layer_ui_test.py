"""Round 28: the screen layer UI — headless Chromium (tests/round28_layer_shots.py) + static checks of the settings.

The headless script drives the real backend with /quick/layer.html and the Quick Ask window side by side (see its
docstring for the list of checks); here also: the «Monitor» setting of Quick Ask and the canvas settings speak about
the screen, both languages, and the layer page is wired the way app/quick_layer.py expects (MemuLayer entry points).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

class Static(unittest.TestCase):
    def test_monitor_setting(self):
        src = (STATIC / "quick-settings.js").read_text(encoding="utf-8")
        self.assertIn('"st-qa-monitor"', src)
        self.assertIn("save({ quick_ask_monitor: e.target.value })", src)
        for value in ('["cursor"', '["window"', '["primary"'):
            self.assertIn(value, src)
        self.assertIn("/quick-settings.js?v=1.2-r34", (STATIC / "index.html").read_text(encoding="utf-8"))

    def test_canvas_settings_describe_the_screen(self):
        src = (STATIC / "quick" / "plugins" / "canvas-settings.js").read_text(encoding="utf-8")
        self.assertIn('"qcs-screen"', src)
        self.assertIn("save({ screen: screen.value })", src)
        self.assertNotIn('"qcs-open"', src)
        self.assertIn("в любом месте экрана", src)
        self.assertIn("anywhere on the screen", src)

    def test_layer_entry_points_match_the_native_side(self):
        js = (STATIC / "quick" / "layer.js").read_text(encoding="utf-8")
        py = (ROOT / "app" / "quick_layer.py").read_text(encoding="utf-8")
        for name in ("onShow", "onHide", "setKeepout", "setEdit"):
            self.assertIn(f"MemuLayer.{name}(", py)
            self.assertRegex(js, rf"\b{name}\b")
        self.assertIn("MemuQuickBus.receive(", py)
        self.assertIn("receive", (STATIC / "quick" / "quick-bus.js").read_text(encoding="utf-8"))
        quick = (ROOT / "app" / "quick_ask.py").read_text(encoding="utf-8")
        self.assertIn("MemuQuickCanvas.layerState", quick)
        self.assertIn("layerState", (STATIC / "quick" / "plugins" / "canvas.js").read_text(encoding="utf-8"))

    def test_canvas_strings_are_bilingual(self):
        src = (STATIC / "quick" / "plugins" / "canvas.js").read_text(encoding="utf-8")
        for ru, en in re.findall(r't\("([^"]*)", "([^"]*)"\)', src):
            self.assertFalse(re.search("[а-яА-ЯёЁ]", en), en)

class Headless(unittest.TestCase):
    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")

    def test_headless_layer(self):
        out = Path(tempfile.mkdtemp(prefix="r28-layer-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            env.pop("MEMUAI_DATA_DIR", None)
            result = subprocess.run([sys.executable, str(ROOT / "tests" / "round28_layer_shots.py"), str(out)],
                                    capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
