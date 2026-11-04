"""Local model wizard: quantisation guide and fit estimates.

Covers the quantisation guide (``quant_guide``), the KV-cache correction in
``speed`` (the cache was discounted twice) and the honest "needs at least"
figure for quants that do not fit, plus the new catalogue models added for
the 5.0 wizard pass (coder, tiny, reasoning, vision, Russian-first, flagship).
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import local_hw, local_wizard as wiz  # noqa: E402

GB = 1024 ** 3
CYRILLIC = re.compile("[А-Яа-яЁё]")

LAPTOP = {"cpu": {"name": "Core i5-1235U", "threads": 12, "cores": 10, "avx2": True},
          "ram": {"total": 16 * GB, "type": "DDR4", "speed": 3200},
          "gpus": [{"name": "Intel(R) Iris(R) Xe Graphics", "vram": 128 * 1024 ** 2}], "disk": {"free": 60 * GB}}
PC_4070 = {"cpu": {"name": "Ryzen 7 7700X", "threads": 16, "cores": 8, "avx2": True, "avx512": True},
           "ram": {"total": 32 * GB, "type": "DDR5", "speed": 6000},
           "gpus": [{"name": "NVIDIA GeForce RTX 4070", "vram": 12 * GB}], "disk": {"free": 400 * GB}}
WORKSTATION = {"cpu": {"cores": 24, "threads": 32, "avx2": True}, "ram": {"total": 128 * GB, "type": "DDR5", "speed": 6400},
               "gpus": [{"name": "NVIDIA GeForce RTX 5090", "vram": 32 * GB}], "disk": {"free": 2000 * GB}}
BIG_RAM = {"cpu": {"cores": 32, "threads": 64, "avx2": True, "avx512": True},
           "ram": {"total": 256 * GB, "type": "DDR5", "speed": 6400},
           "gpus": [], "disk": {"free": 2000 * GB}}


class QuantGuideTests(unittest.TestCase):
    def test_guide_covers_every_quant_in_use(self):
        catalog = wiz.load_catalog()
        used = set(wiz.QUANT_QUALITY)
        for entry in catalog["models"]:
            for info in (entry.get("files") or {}).values():
                used.add(str(info.get("quant") or ""))
        guide = wiz.quant_guide()
        for quant in sorted(q for q in used if q):
            self.assertIn(quant, wiz.QUANT_GUIDE, quant)
        for quant, info in wiz.QUANT_GUIDE.items():
            for lang in (0, 1):
                self.assertTrue(info["title"][lang].strip(), quant)
                self.assertTrue(info["text"][lang].strip(), quant)
            self.assertGreater(info["gb_per_b"], 0.2)
            self.assertLess(info["gb_per_b"], 2.5)

    def test_guide_english_has_no_cyrillic(self):
        for english in (False, True):
            blob = json.dumps(wiz.quant_guide(english), ensure_ascii=False)
            if english:
                self.assertIsNone(CYRILLIC.search(blob), blob)
            else:
                self.assertTrue(CYRILLIC.search(blob))

    def test_guide_in_recommend_payload(self):
        result = wiz.recommend({"uses": ["chat"]}, LAPTOP, installed={})
        self.assertTrue(result["results"])
        for key in ("quant_guide", "quant_guide_en"):
            guide = result[key]
            self.assertTrue(guide["what"])
            self.assertTrue(guide["picks"])
            self.assertGreater(len(guide["quants"]), 10)
        self.assertEqual([q["quant"] for q in result["quant_guide"]["quants"]],
                         [q["quant"] for q in result["quant_guide_en"]["quants"]])


class FitEstimateTests(unittest.TestCase):
    def entry(self, model_id):
        return next(m for m in wiz.load_catalog()["models"] if m["id"] == model_id)

    def test_speed_falls_with_context(self):
        # The KV cache is read once per generated token; at a long context the
        # estimate must be slower than at a short one. (The cache used to be
        # discounted twice: kv(context // 2) * 0.5.)
        entry = self.entry("qwen3.5-9b")
        profile = local_hw.finish(LAPTOP)
        budget = wiz.budgets(profile, 0)
        size = entry["files"]["Q4_K_M"]["size"]
        short = wiz.speed(entry, size, 16384, "cpu", 0.0, profile, budget)
        long = wiz.speed(entry, size, 131072, "cpu", 0.0, profile, budget)
        self.assertGreater(short, 1)
        self.assertLess(long, short)

    def test_none_option_need_includes_kv_cache(self):
        entry = self.entry("deepseek-r1-distill-qwen-32b")
        profile = local_hw.finish(LAPTOP)
        result = wiz.evaluate(entry, wiz.normalize_goals({"uses": ["chat"]}), profile, wiz.budgets(profile, 0))
        self.assertTrue(result["options"])
        for option in result["options"]:
            self.assertEqual(option["placement"], "none")
            # weight + overhead alone would be size * 1.02 + 0.5 GB; the honest
            # minimum also counts the KV cache at the smallest context.
            self.assertGreater(option["need"], int(option["size"] * 1.02 + 0.5 * GB))

    def test_new_models_fit_where_they_should(self):
        laptop = wiz.recommend({"uses": ["chat"]}, LAPTOP, installed={})
        laptop_ids = [row["id"] for row in laptop["results"] + laptop["more"]]
        self.assertIn("smollm3-3b", laptop_ids)
        self.assertEqual(next(r for r in laptop["results"] + laptop["more"] if r["id"] == "smollm3-3b")["placement"], "cpu")
        coding = wiz.recommend({"uses": ["coding"]}, LAPTOP, installed={})
        self.assertIn("qwen2.5-coder-7b", [row["id"] for row in coding["results"] + coding["more"]])
        self.assertIn("deepseek-r1-distill-qwen-32b", [e["id"] for e in laptop["excluded"]])
        vision = wiz.recommend({"uses": ["vision"]}, PC_4070, installed={})
        self.assertIn("qwen3-vl-8b", [row["id"] for row in vision["results"] + vision["more"]])
        ru = wiz.recommend({"uses": ["chat"]}, PC_4070, installed={})
        self.assertIn("t-lite-2.1", [row["id"] for row in ru["results"] + ru["more"]])

    def test_flagship_needs_a_huge_machine(self):
        workstation = wiz.recommend({"uses": ["chat"]}, WORKSTATION, installed={})
        self.assertIn("qwen3.5-397b-a17b", [e["id"] for e in workstation["excluded"]])
        big = wiz.recommend({"uses": ["chat"]}, BIG_RAM, installed={})
        flagship = next((r for r in big["results"] + big["more"] if r["id"] == "qwen3.5-397b-a17b"), None)
        self.assertIsNotNone(flagship)
        self.assertEqual(flagship["placement"], "cpu")
        self.assertGreater(flagship["tps"], 1)

    def test_t_lite_has_no_lossy_q3(self):
        self.assertEqual(self.entry("t-lite-2.1")["quants"], ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"])


if __name__ == "__main__":
    unittest.main()
