"""Round 29 (r29-tables-diagrams), item 9 «Diagrams»: declarative charts (```memuai-chart) drawn by the own SVG
renderer of pgstatic/diagrams.js, Mermaid schemes (vendored, lazy), streaming placeholders, friendly errors,
CSV export, the careful prompt (auto / ask / off) and the «diagrams_mode» preference.

The JS runs in node (tests/round29_tables_runner.cjs); every chart SVG is validated with ElementTree.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-diagrams-")

from tests.round29_tables_test import run_js  # noqa: E402  (one node run for both files)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
SVG = "{http://www.w3.org/2000/svg}"


@unittest.skipUnless(shutil.which("node"), "node is not available")
class ChartRendererTest(unittest.TestCase):
    def test_every_chart_type_renders_valid_svg(self) -> None:
        charts = run_js()["diagrams"]["charts"]
        expected = {"bar": "bar", "stacked": "bar", "hbar": "hbar", "line": "line", "area": "area", "pie": "pie", "donut": "donut",
                    "scatter": "scatter", "radar": "radar", "combo": "combo", "table": "line", "many": "bar"}
        for name, kind in expected.items():
            chart = charts[name]
            self.assertTrue(chart["ok"], f"{name}: {chart.get('error')}")
            self.assertEqual(chart["type"], kind)
            root = ET.fromstring(chart["svg"])
            self.assertEqual(root.tag, SVG + "svg")
            self.assertEqual(root.get("viewBox"), f"0 0 {chart['width']} {chart['height']}")
            marks = [el for el in root.iter() if "md-mark" in (el.get("class") or "") or "md-line" in (el.get("class") or "")]
            self.assertTrue(marks, f"{name}: no marks drawn")
            for el in root.iter():
                for attr in ("x", "y", "cx", "cy", "width", "height", "d", "points"):
                    self.assertNotIn("NaN", el.get(attr) or "", f"{name}: NaN in {el.tag} {attr}")

    def test_content(self) -> None:
        charts = run_js()["diagrams"]["charts"]
        bar = charts["bar"]["svg"]
        self.assertIn("Revenue", bar)
        self.assertIn("Q4", bar)
        self.assertIn('class="md-leg"', bar, "two series -> an interactive legend")
        self.assertIn("$22", bar, "value labels with the unit")
        self.assertEqual(charts["table"]["series"], 2, "table-like data becomes series")
        self.assertEqual(charts["pie"]["labels"], 2)
        self.assertIn("rotate(-38)", charts["many"]["svg"], "crowded labels are rotated")
        self.assertIn("big", charts["scatter"]["svg"])

    def test_type_switch_and_errors(self) -> None:
        d = run_js()["diagrams"]
        self.assertEqual(d["typeSwitch"], "pie")
        self.assertEqual(d["badType"], "bar", "incompatible type switch is ignored")
        errors = d["errors"]
        self.assertEqual(errors[0], "The chart block is empty")
        self.assertTrue(errors[1].startswith("Invalid chart JSON"))
        self.assertIn("object was expected", errors[2])
        self.assertIn("no data", errors[3])
        self.assertIn('Unknown chart type "wat"', errors[4])
        self.assertIn("negative", errors[5])
        self.assertIn("no numbers", errors[6])
        self.assertEqual(d["lenient"], "bar", "single quotes and trailing commas are forgiven")
        self.assertNotIn("evil.example", d["evilColor"], "a color from the spec cannot reference a URL")

    def test_csv_ticks_format(self) -> None:
        d = run_js()["diagrams"]
        self.assertEqual(d["csv"].lstrip("\ufeff").split("\r\n"), ["Category,2024,2025", "Q1,12,14", "Q2,15,18", "Q3,11,16", "Q4,19,22"])
        self.assertEqual(d["ticks"]["ticks"], [0, 20, 40, 60, 80, 100])
        self.assertEqual(d["fmt"], ["1.2M", "12.5%", "-$3"])

    def test_blocks_and_streaming(self) -> None:
        d = run_js()["diagrams"]
        self.assertIn('data-memu-diagram="chart"', d["block"]["chart"])
        self.assertIn('data-memu-diagram="mermaid"', d["block"]["mermaid"])
        self.assertIn("Рисую схему", d["block"]["mermaid"], "the language argument wins")
        self.assertIn("Drawing the scheme", d["block"]["pending"])
        self.assertEqual(d["block"]["other"], "")
        mask = d["mask"]
        self.assertTrue(mask["open"].endswith("```memuai-diagram-pending\nchart\n```"))
        self.assertIn("memuai-diagram-pending\nmermaid", mask["closedAtEnd"])
        self.assertNotIn("pending", mask["closedMiddle"], "a finished scheme followed by text is drawn")
        self.assertEqual(mask["none"], "just text")
        self.assertEqual(d["strip"], "a\n[chart]\nb\n[diagram]")


@unittest.skipUnless(shutil.which("node"), "node is not available")
class PromptTest(unittest.TestCase):
    def test_modes(self) -> None:
        p = run_js()["diagrams"]["prompt"]
        self.assertEqual(p["off"], "")
        self.assertIn("ONLY if", p["auto"])
        self.assertIn("NEVER draw one for greetings", p["auto"])
        self.assertIn("real numeric series", p["auto"])
        self.assertIn("Show this as a chart?", p["ask"])
        self.assertNotIn("real numeric series", p["ask"])
        self.assertIn("```memuai-chart", p["auto"])
        self.assertIn("```mermaid", p["auto"])
        self.assertLess(len(p["auto"]), 3600, "sent with every chat request: keep it compact")

    def test_base_prompts_mirror(self) -> None:
        from app import pgdata
        line = "Charts and diagrams are not decoration"
        self.assertIn(line, pgdata.DEFAULT_PROMPT)
        self.assertIn(line, (STATIC / "app.js").read_text(encoding="utf-8"))
        from app import quick_ask_core
        quick = quick_ask_core.system_prompt(lang="ru", style="brief", web=True, screenshot=True, vision=True)
        self.assertIn("memuai-chart", quick)
        self.assertLess(len(quick), 4200)


class PreferenceTest(unittest.TestCase):
    def test_diagrams_mode_normalized(self) -> None:
        from app import pgdata
        self.assertEqual(pgdata.DEFAULT_PREFERENCES["diagrams_mode"], "auto")
        self.assertEqual(pgdata._normalize_preferences({"diagrams_mode": "ask"})["diagrams_mode"], "ask")
        self.assertEqual(pgdata._normalize_preferences({"diagrams_mode": "off"})["diagrams_mode"], "off")
        self.assertEqual(pgdata._normalize_preferences({"diagrams_mode": "bogus"})["diagrams_mode"], "auto")
        self.assertEqual(pgdata._normalize_preferences({})["diagrams_mode"], "auto")
        saved = pgdata.save_preferences({"diagrams_mode": "ask"})
        self.assertEqual(saved.get("diagrams_mode", pgdata.load_preferences().get("diagrams_mode")), "ask")
        pgdata.save_preferences({"diagrams_mode": "auto"})

    def test_mermaid_vendor(self) -> None:
        lib = STATIC / "vendor" / "mermaid" / "mermaid.min.js"
        self.assertGreater(lib.stat().st_size, 1_000_000)
        self.assertIn("MIT", (STATIC / "vendor" / "mermaid" / "LICENSE").read_text(encoding="utf-8"))
        js = (STATIC / "diagrams.js").read_text(encoding="utf-8")
        self.assertIn('securityLevel: "strict"', js)
        self.assertIn("htmlLabels: false", js, "SVG labels: exportable to PNG")
        self.assertIn("/vendor/mermaid/mermaid.min.js", js)
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("mermaid.min.js", index, "Mermaid is loaded lazily, not with the page")


if __name__ == "__main__":
    unittest.main()
