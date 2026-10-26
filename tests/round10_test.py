"""Round 10: context detection, version detector, MemuCode stats, local draft files,
screen capture replays, partial chat saves, Antigravity quota/rename, the
Benchmarks module (MemuAI Index, personal arena) and the Model catalog module.

Everything is offline. Module logic runs in Node (tests/*_runner.cjs) with a
fake DOM and bridge; nothing here reaches a real provider or website.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import code_stats, detector, local_models, model_info, pgdata, screen_capture
from app.providers import antigravity

ROOT = Path(__file__).resolve().parents[1]
BUILTINS = ROOT / "app" / "module_builtins"
NODE = shutil.which("node")

def run_node(script: str) -> dict:
    done = subprocess.run([NODE, str(ROOT / "tests" / script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    lines = [line for line in done.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise AssertionError(f"{script} printed nothing: {done.stderr[-2000:]}")
    result = json.loads(lines[-1])
    if "error" in result:
        raise AssertionError(result["error"])
    return result

class ModelContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(model_info, "DATA_DIR", self.temp.name),
                        patch.object(model_info, "_learned", {}), patch.object(model_info, "_loaded", True)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        timer = model_info._save_timer
        if timer is not None:
            timer.cancel()
            model_info._save_timer = None
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_canonical_names_drop_vendor_dates_and_modes(self) -> None:
        self.assertEqual(model_info.canonical("anthropic/Claude-Opus-5-5-20260901:thinking"), "claude-opus-5.5")
        self.assertEqual(model_info.canonical("genspark-gpt-5.6-sol"), "genspark-gpt-5.6-sol")
        self.assertEqual(model_info.canonical("Qwen3-70B-Instruct-Q4_K_M"), "qwen3-70b-instruct")
        self.assertEqual(model_info.canonical("llama-3.1-8b-iq4_xs"), "llama-3.1-8b")

    def test_known_catalogue_gives_a_window_for_common_models(self) -> None:
        info = model_info.resolve("kiro", "kiro", "claude-sonnet-4.5")
        self.assertEqual(info["source"], "catalog")
        self.assertEqual(info["context"], 200000)
        self.assertTrue(info["label"])

    def test_provider_numbers_beat_the_catalogue(self) -> None:
        model_info.record("custom", "claude-sonnet-4.5", context=64000, source="api:custom")
        info = model_info.resolve("custom", "custom", "claude-sonnet-4.5")
        self.assertEqual((info["context"], info["source"]), (64000, "api:custom"))
        self.assertEqual(info["label"], "Claude Sonnet 4.5")

    def test_unknown_model_has_no_window_so_the_ui_uses_its_default(self) -> None:
        info = model_info.resolve("custom", "custom", "totally-unknown-model-xyz")
        self.assertIsNone(info["context"])

    def test_meter_reads_the_row_shape_pg_state_sends(self) -> None:

        source = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        start = source.index("function modelContextInfo(model)")
        body = source[start:source.index("function modelContextLimit", start)]
        self.assertIn(".context || null", body)
        self.assertIn("Number(detected?.context)", body)
        self.assertIn('setTimeout(renderContextMeter, 0)', source)
        admin = (ROOT / "app" / "admin_api.py").read_text(encoding="utf-8")
        self.assertIn('row["context"] = {key: info.get(key) for key in', admin)

class DetectorVersionTest(unittest.TestCase):
    QUIZ = {"estimate": "2025-08", "items": [
        {"date": "2025-06", "correct": True, "answered": True},
        {"date": "2025-12", "correct": False, "answered": True},
        {"date": "2026-03", "correct": False, "answered": True},
    ]}

    def test_newer_generation_cannot_know_less_than_its_predecessor(self) -> None:
        row = next(item for item in detector.MODELS if item["id"] == "gpt-6-astra")
        self.assertEqual(detector._floor_cutoff(row), "2025-08")

    def test_other_version_that_fits_better_is_named(self) -> None:
        report = detector.assess_version("gpt-6-astra", "gpt", {}, self.QUIZ, None)
        self.assertEqual(report["verdict"], "likely_other")
        self.assertEqual(report["likely"]["id"], "gpt-5-2")
        self.assertGreater(report["likely"]["percent"], report["claimed_percent"] + 10)
        claimed = next(entry for entry in report["entries"] if entry["id"] == "gpt-6-astra")
        self.assertEqual(claimed["status"], "at_floor")

    def test_self_named_claimed_version_is_confirmed(self) -> None:
        results = {"id1": {"kind": "identity", "text": "I am GPT-6 Astra by OpenAI."}}
        report = detector.assess_version("gpt-6-astra", "gpt", results, None, None)
        self.assertEqual(report["verdict"], "confirmed")

class CodeStatsTest(unittest.TestCase):
    def test_rounds_add_up_per_day_session_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(pgdata, "MEMU_DIR", Path(folder)):
            code_stats.record({"day": "2026-09-20", "hour": 10, "session": "s1", "model": "m", "user_message": True,
                               "tokens_in": 1000, "tokens_out": 200})
            code_stats.record({"day": "2026-09-21", "hour": 11, "session": "s2", "model": "m",
                               "tokens_in": 500, "tokens_out": 100})
            code_stats.record({"day": "2026-01-01", "hour": 9, "session": "old", "model": "m",
                               "tokens_in": 7, "tokens_out": 7})
            week = code_stats.summary(7, today="2026-09-21")
            everything = code_stats.summary(None, today="2026-09-21")
        self.assertEqual(week["tokens"], {"input": 1500, "output": 300, "total": 1800})
        self.assertEqual((week["messages"], week["sessions"], week["peak_hour"]), (3, 2, 10))
        self.assertEqual(week["favorite_model"], "m")
        self.assertEqual(everything["tokens"]["total"], 1814)

    def test_comparisons_are_drawn_from_many_references(self) -> None:
        source = (ROOT / "app" / "pgstatic" / "code-stats.js").read_text(encoding="utf-8")
        self.assertIn("REFERENCES", source)
        self.assertIn("MEASURES", source)
        self.assertGreaterEqual(source.count("tokens:"), 25)

class LocalDraftTest(unittest.TestCase):
    def test_draft_heads_are_recognised(self) -> None:
        self.assertTrue(local_models.is_draft_meta({"layers": 64, "tensors": 20}))
        self.assertTrue(local_models.is_draft_meta({"draft": True}))
        self.assertFalse(local_models.is_draft_meta({"layers": 64, "tensors": 700}))
        self.assertTrue(local_models.is_draft_meta({"layers": 64, "tensors": 200}, "Qwen3-MTP-draft.gguf"))

    def test_access_violation_is_explained(self) -> None:
        text = local_models.describe_exit(-1073741819, ["load_tensors: something", "error: missing tensor blk.12"])
        self.assertIn("0xC0000005", text)
        self.assertIn("missing tensor", text)
        self.assertIn("draft", local_models.draft_problem("model-draft.gguf"))

class ScreenCaptureReplayTest(unittest.TestCase):
    def test_a_replayed_request_right_after_cancel_does_not_reopen_the_overlay(self) -> None:
        with patch.object(screen_capture.os, "name", "nt"), \
                patch.object(screen_capture, "_last_cancel_at", time.monotonic()), \
                patch.object(screen_capture, "_capture", side_effect=AssertionError("overlay reopened")):
            result = screen_capture.capture_region()
        self.assertEqual(result["code"], "cancelled")
        self.assertGreaterEqual(screen_capture.REPLAY_WINDOW, 1.0)

class PartialChatSaveTest(unittest.TestCase):
    def test_only_mentioned_chats_change_and_newest_wins(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(pgdata, "MEMU_DIR", Path(folder)):
            pgdata.save_chats([{"id": "a", "title": "A", "updated": 10, "messages": []},
                               {"id": "b", "title": "B", "updated": 20, "messages": []},
                               {"id": "c", "title": "C", "updated": 30, "messages": []}])
            pgdata.merge_chats_partial([{"id": "a", "title": "A2", "updated": 40, "messages": []},
                                        {"id": "b", "title": "stale", "updated": 5, "messages": []},
                                        {"id": "t", "title": "temp", "temp": True, "updated": 50}], ["c"])
            rows = {row["id"]: row for row in pgdata.load_chats()}
        self.assertEqual(set(rows), {"a", "b"})
        self.assertEqual(rows["a"]["title"], "A2")
        self.assertEqual(rows["b"]["title"], "B")

class AntigravityQuotaTest(unittest.TestCase):
    def test_retired_ids_follow_the_rename(self) -> None:
        self.assertEqual(antigravity.current_model_id("gemini-3.1-pro-high"), "gemini-pro-agent")
        self.assertEqual(antigravity.current_model_id("claude-sonnet-4-6"), "claude-sonnet-4-6")
        with patch.dict(antigravity._deprecated_learned, {"a": "b", "b": "a"}):
            self.assertIn(antigravity.current_model_id("a"), {"a", "b"})

    def test_usage_reports_pools_resets_and_weekly_limit_note(self) -> None:
        provider = antigravity.AntigravityProvider()
        buckets = {
            "gemini-3.5-flash": {"remainingFraction": 0.9997, "resetTime": "2026-09-27T20:00:00Z"},
            "gemini-pro-agent": {"remainingFraction": 0.5, "resetTime": "2026-09-27T21:00:00Z"},
            "claude-sonnet-4-6": {"resetTime": "2026-09-27T22:00:00Z"},
        }

        async def token(_account):
            return "token"

        async def project(_account, _token):
            return "project"

        async def quota(_account, _token, _project):
            return buckets

        async def tier(_account, _token):
            return "Google AI Pro"

        account = SimpleNamespace(id="acc-1")
        with patch.object(provider, "_ensure_token", token), patch.object(provider, "_project_id", project), \
                patch.object(provider, "_quota_buckets", quota), patch.object(provider, "_tier_name", tier), \
                patch.dict(antigravity._quota_hits, {"acc-1": {"Gemini": time.time() - 60}}):
            usage = asyncio.run(provider.usage(account))
        rows = {row["display"]: row for row in usage["rows"]}
        self.assertEqual(rows["Gemini — 5 часов"]["used"], 50.0)
        self.assertEqual(rows["Claude / GPT — 5 часов"]["used"], 100.0)
        self.assertIn("reset", rows["Gemini — 5 часов"])
        self.assertIn("note", rows["Gemini — 5 часов"])
        self.assertNotIn("note", rows["Claude / GPT — 5 часов"])
        self.assertEqual(usage["next_reset"], antigravity._iso_ts("2026-09-27T21:00:00Z"))
        self.assertEqual(usage["subscription"], "Google AI Pro")

@unittest.skipUnless(NODE, "Node.js is required for module runners")
class BenchmarksModuleTest(unittest.TestCase):
    def test_outdated_sources_are_gone(self) -> None:
        folder = BUILTINS / "benchmarks"
        text = "\n".join((folder / name).read_text(encoding="utf-8") for name in ("main.js", "i18n.json", "manifest.json"))
        for gone in ("swebench", "SWE-bench", "Aider", "aider", "ifbench", "tau2", "terminalbench", "Terminal-Bench"):
            self.assertNotIn(gone, text)

    def test_index_never_penalises_a_model_for_missing_sources(self) -> None:
        out = run_node("benchmarks_runner.cjs")
        self.assertEqual(out["parts"], ["arena:text", "arena:code/webdev", "aa:intelligence", "simple:simplebench"])
        self.assertEqual(out["order"], ["Alpha 1", "Beta 2", "Gamma 3", "Delta 4", "Eps 5"])

        self.assertNotIn("Zeta 6", out["order"])
        self.assertNotIn("Solo 9", out["order"])

        for name, raw in out["raw"].items():
            self.assertAlmostEqual(out["scores"][name], raw, places=2)

        titan = out["titan"]
        self.assertEqual(titan["sources"], 7)
        self.assertEqual(titan["order"], ["Titan A", "Titan B"])
        self.assertEqual(titan["scoreA"], 100)
        self.assertLess(titan["scoreB"], titan["scoreA"])
        self.assertEqual((titan["countA"], titan["countB"]), (4, 7))
        self.assertLess(titan["coverageA"], titan["coverageB"])
        self.assertTrue(out["method"])
        self.assertEqual(out["catalog"][0], "memu")
        self.assertIn("memu:own", out["boards"])
        self.assertEqual(out["overview"][0], "memu:index")
        self.assertEqual(out["missingI18n"], [])

    def test_personal_arena_checks_exact_answers_and_judges_blind(self) -> None:
        out = run_node("benchmarks_runner.cjs")["own"]
        self.assertEqual(len(out["tasks"]), 8)
        self.assertEqual(out["judged"], 4)
        self.assertEqual(out["judgeError"], "")
        self.assertGreater(out["totals"]["m-a"], out["totals"]["m-b"])
        self.assertTrue(out["html"])

    def test_manifest_grants_what_the_code_uses(self) -> None:
        manifest = json.loads((BUILTINS / "benchmarks" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("chat", manifest["permissions"])
        self.assertIn("own_runs", manifest["persist"])
        self.assertNotIn("cache_swe", manifest["persist"])

@unittest.skipUnless(NODE, "Node.js is required for module runners")
class CatalogModuleTest(unittest.TestCase):
    def test_filters_sorting_compare_and_my_models(self) -> None:
        out = run_node("catalog_runner.cjs")
        self.assertEqual(out["count"], 6)
        self.assertEqual(out["newest"][0], "router/auto")
        self.assertEqual(out["opus"], {"org": "Anthropic", "name": "Claude Opus 5", "input": 5, "output": 25,
                                       "cacheRead": 0.5, "tools": True, "reasoning": True, "structured": True,
                                       "context": 1000000})
        self.assertIsNone(out["router"]["input"])
        self.assertEqual(out["imageOutput"], ["openai/gpt-image-2"])
        self.assertEqual(out["free"], ["meta/llama-5-70b:free"])
        self.assertNotIn("router/auto", out["cheap"])
        self.assertEqual(out["toolsReasoning"], ["anthropic/claude-opus-5"])
        self.assertFalse(out["noExpiring"])
        self.assertEqual(out["orgs"], ["google/gemini-3.5-flash"])
        self.assertEqual(out["priceAsc"][0], "meta/llama-5-70b:free")
        self.assertEqual(out["priceAsc"][-1], "router/auto")
        self.assertEqual(out["mine"], ["anthropic/claude-opus-5"])
        self.assertTrue(out["compare"] and out["card"] and out["savedCache"])
        self.assertEqual(out["missingI18n"], [])

    def test_catalog_is_a_registered_bilingual_builtin(self) -> None:
        from app import modules_host
        self.assertIn("catalog", modules_host.BUILTIN_ORDER)
        folder = BUILTINS / "catalog"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "catalog")
        self.assertFalse(manifest["enabled"])
        self.assertNotIn("chat", manifest["permissions"])
        i18n = json.loads((folder / "i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(set(i18n["ru"]), set(i18n["en"]))
        self.assertIn("openrouter.ai/api/v1/models", (folder / "main.js").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
