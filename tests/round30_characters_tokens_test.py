"""Round 30: «Characters» counts tokens with the shared language-aware estimate (app/pgstatic/tokens.js)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r30-chartok-")

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const T = require(process.argv[3]);
const start = src.indexOf("/* @memu-inlined:tokens */"), end = src.indexOf("/* @engine-end */");
const E = new Function(src.slice(start, end) + "\nreturn CharEngine;")();
const ru = "Привет! Расскажи мне, пожалуйста, длинную историю о волшебнице, которая живёт в башне у моря.";
const en = "Hi! Please tell me a long story about a witch who lives in a tower by the sea.";
const out = { ru: E.estimateTokens(ru), en: E.estimateTokens(en), ruShared: T.estimate(ru), enShared: T.estimate(en) };
E.setTokenModel("qwen/qwen3.8-max:free");
out.ruQwen = E.estimateTokens(ru); out.ruQwenShared = T.estimate(ru, "qwen/qwen3.8-max:free");
process.stdout.write(JSON.stringify(out));
"""

class CharactersTokensTest(unittest.TestCase):
    def package_js(self) -> str:
        from app.modules_host import load_package
        with tempfile.TemporaryDirectory() as temp:
            return load_package(Path(temp), "characters")["js"]

    def test_shared_estimate_is_inlined_once_after_use_strict(self):
        js = self.package_js()
        self.assertEqual(js.count("/* @memu-inlined:tokens */"), 1)
        self.assertNotIn("/* @memu-shared:tokens */", js)
        self.assertLess(js.index('"use strict";'), js.index("/* @memu-inlined:tokens */"))
        self.assertNotIn("function estimateTokens(text) {\n    var s = str(text);\n    if (!s) return 0;\n    if (tokenCache.has(s)) return tokenCache.get(s);\n    var count = 0;", js)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_counts_match_memuai(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "main.js").write_text(self.package_js(), encoding="utf-8")
            (Path(temp) / "run.js").write_text(SCRIPT, encoding="utf-8")
            done = subprocess.run(["node", str(Path(temp) / "run.js"), str(Path(temp) / "main.js"),
                                   str(ROOT / "app" / "pgstatic" / "tokens.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["ru"], out["ruShared"])
        self.assertEqual(out["en"], out["enShared"])
        self.assertEqual(out["ruQwen"], out["ruQwenShared"])
        self.assertGreater(out["ru"], out["en"])

if __name__ == "__main__":
    unittest.main()
