"""Round 22: model capabilities and the full model card (app/model_capabilities.py, app/model_caps_api.py).

Covers: catalogue normalisation (OpenRouter, Models.dev), refresh with a failing source, raw provider rows of
every common shape, source precedence and confidence, honest "free", the vision probe (fake model call), the
``caps.vision`` rule for MemuAI / Quick Ask, the admin routes, speed with 600+ models, and the UI wiring.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import model_capabilities as mc  # noqa: E402

OR_ROWS = [
    {"id": "anthropic/claude-haiku-4.5", "name": "Anthropic: Claude Haiku 4.5", "created": 1760000000,
     "description": "Fast Claude.", "context_length": 200000,
     "architecture": {"modality": "text+image->text", "input_modalities": ["text", "image", "file"],
                      "output_modalities": ["text"], "tokenizer": "Claude"},
     "pricing": {"prompt": "0.000001", "completion": "0.000005", "input_cache_read": "0.0000001",
                 "web_search": "0.01", "overrides": [{"min_prompt_tokens": 100000, "prompt": "0.000002", "completion": "0.00001"}]},
     "top_provider": {"context_length": 200000, "max_completion_tokens": 64000, "is_moderated": True},
     "supported_parameters": ["tools", "tool_choice", "reasoning", "include_reasoning", "response_format", "structured_outputs", "temperature"],
     "reasoning": {"mandatory": False, "supported_efforts": ["high", "low"]},
     "knowledge_cutoff": "2025-02-01", "hugging_face_id": None},
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek: DeepSeek V3", "context_length": 163840,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.0000003", "completion": "0.0000012"},
     "top_provider": {"max_completion_tokens": 16000}, "supported_parameters": ["tools", "response_format"],
     "hugging_face_id": "deepseek-ai/DeepSeek-V3"},
    {"id": "qwen/qwen3-coder:free", "name": "Qwen: Qwen3 Coder (free)", "context_length": 262144,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools"]},
    {"id": "~anthropic/claude-haiku-latest", "name": "Anthropic: Claude Haiku Latest", "context_length": 1000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.000001", "completion": "0.000005"}, "alias_target": {"slug": "anthropic/claude-haiku-4.5"}},
]
MD_DATA = {
    "anthropic": {"name": "Anthropic", "models": {
        "claude-haiku-4-5": {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "family": "claude-haiku",
                             "attachment": True, "reasoning": True, "tool_call": True, "structured_output": True,
                             "knowledge": "2025-02-28", "release_date": "2025-10-15", "last_updated": "2025-10-15",
                             "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]}, "open_weights": False,
                             "limit": {"context": 200000, "output": 64000}, "cost": {"input": 1, "output": 5},
                             "canonical_model_id": "anthropic/claude-haiku-4-5"}}},
    "reseller": {"name": "Reseller", "models": {
        "claude-haiku-4-5": {"id": "claude-haiku-4-5", "name": "Haiku (resold)", "modalities": {"input": ["text"], "output": ["text"]},
                             "limit": {"context": 100000, "output": 1000}, "cost": {"input": 0, "output": 0},
                             "canonical_model_id": "anthropic/claude-haiku-4-5"},
        "whisper-1": {"id": "whisper-1", "name": "Whisper", "modalities": {"input": ["audio"], "output": ["text"]},
                      "limit": {"context": 0, "output": 0}, "tool_call": False, "reasoning": False}}},
    "old": {"name": "Old", "models": {
        "gpt-4-0314": {"id": "gpt-4-0314", "name": "GPT-4 (2023)", "status": "deprecated",
                       "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 8192, "output": 8192},
                       "canonical_model_id": "openai/gpt-4-0314"}}},
}

class _Entry:
    def __init__(self, prefix: str, provider: str, model: str, enabled: bool = True) -> None:
        self.prefix, self.provider, self.model, self.enabled = prefix, provider, model, enabled
        self.id = f"{prefix}-{model}"

def fake_fetch(url: str):
    return {"data": OR_ROWS} if "openrouter" in url else MD_DATA

def reset_state() -> None:
    with mc._lock:
        mc._raw.clear()
        mc._probes.clear()
        mc._summary_cache.clear()
        mc._catalog.stamp = (-1.0, -1.0)
        mc._catalog.checked = 0.0
    for name in (mc.CATALOG_FILE, mc.RAW_FILE, mc.PROBES_FILE):
        try:
            os.remove(mc._path(name))
        except OSError:
            pass

class NormaliseTest(unittest.TestCase):
    def test_openrouter_row(self):
        row = mc.normalize_openrouter(OR_ROWS[0])
        self.assertEqual(row["name"], "Claude Haiku 4.5")
        self.assertEqual(row["vendor"], "Anthropic")
        self.assertEqual(row["in"], ["text", "image", "file"])
        self.assertEqual(row["context"], 200000)
        self.assertEqual(row["max_out"], 64000)
        self.assertTrue(row["tools"] and row["structured"] and row["json_mode"] and row["reasoning"])
        self.assertEqual(row["reasoning_efforts"], ["high", "low"])
        self.assertAlmostEqual(row["price"]["input"], 1.0)
        self.assertAlmostEqual(row["price"]["output"], 5.0)
        self.assertAlmostEqual(row["price"]["cache_read"], 0.1)
        self.assertEqual(row["price"]["web_search"], 0.01)

        self.assertIsNone(row["web"])
        self.assertEqual(row["price_tiers"][0]["min_prompt_tokens"], 100000)
        self.assertEqual(row["cutoff"], "2025-02-01")
        self.assertTrue(row["moderated"])
        self.assertEqual(row["release_date"], "2025-10-09")

    def test_modalities_from_modality_string_and_aliases(self):
        row = mc.normalize_openrouter({"id": "x/y", "architecture": {"modality": "text+image->text"}})
        self.assertEqual(row["in"], ["text", "image"])
        self.assertEqual(mc._modalities(["PDF", "Images", "speech"]), ["file", "image", "audio"])

    def test_models_dev_dedupes_resellers_and_prefers_lab(self):
        rows = {mc.model_info.canonical(r["id"]): r for r in mc.models_dev_rows(MD_DATA)}
        haiku = rows["claude-haiku-4.5"]
        self.assertEqual(haiku["vendor"], "Anthropic")
        self.assertEqual(haiku["in"], ["text", "image", "file"])
        self.assertEqual(haiku["cutoff"], "2025-02-28")
        self.assertIn("whisper-1", rows)
        self.assertEqual(rows[mc.model_info.canonical("openai/gpt-4-0314")]["status"], "deprecated")

    def test_bad_values_are_unknown_not_zero(self):
        self.assertIsNone(mc._per_million("-1"))
        self.assertIsNone(mc._per_million("abc"))
        self.assertIsNone(mc._tokens(True))
        self.assertIsNone(mc._tokens(12))
        self.assertEqual(mc._date(0), "")
        self.assertEqual(mc._date(1760000000000), "2025-10-09")

class CatalogRefreshTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_refresh_writes_and_indexes(self):
        result = mc.refresh_catalog(fetch=fake_fetch)
        self.assertTrue(result["ok"])
        self.assertTrue(result["sources"]["openrouter"]["ok"])
        status = mc.catalog_status()
        self.assertTrue(status["own"])
        found = mc._catalog.match("claude-haiku-4-5")
        self.assertEqual(found["openrouter"][0]["id"], "anthropic/claude-haiku-4.5")
        self.assertEqual(found["models.dev"][1], "exact")

    def test_failed_source_keeps_previous_rows(self):
        mc.refresh_catalog(fetch=fake_fetch)

        def half(url):
            if "models.dev" in url:
                raise RuntimeError("HTTP 503")
            return {"data": OR_ROWS}
        result = mc.refresh_catalog(fetch=half)
        self.assertTrue(result["ok"])
        self.assertFalse(result["sources"]["models.dev"]["ok"])
        self.assertIn("503", result["sources"]["models.dev"]["error"])
        self.assertGreater(result["sources"]["models.dev"]["rows"], 0)
        data = json.loads(Path(mc._path(mc.CATALOG_FILE)).read_text(encoding="utf-8"))
        self.assertTrue(any(r["source"] == "models.dev" for r in data["rows"]))

    def test_everything_failing_writes_nothing(self):
        def fail(url):
            raise OSError("offline")
        result = mc.refresh_catalog(fetch=fail)
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(mc._path(mc.CATALOG_FILE)))

    def test_module_cache_is_used_until_own_copy_exists(self):
        from app.pgdata import MEMU_DIR
        path = Path(MEMU_DIR) / "modules" / "persist" / "catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cache": {"at": time.time() * 1000, "rows": [
            {"id": "google/gemini-9-pro", "name": "Gemini 9 Pro", "context": 2000000, "in": ["text", "image", "audio"],
             "out": ["text"], "tools": True, "reasoning": True, "price": {"input": 2, "output": 12}}]}}), encoding="utf-8")
        try:
            mc._catalog.ensure(force=True)
            card = mc.describe("gemini-9-pro", entry=_Entry("custom", "custom", "gemini-9-pro"), include_raw=False)
            self.assertEqual(card["fields"]["context"]["value"], 2000000)
            self.assertTrue(card["matched"]["openrouter"]["via_module"])
            self.assertTrue(card["fields"]["audio_input"]["value"])
            self.assertTrue(mc.catalog_status()["module"])
        finally:
            path.unlink()
            mc._catalog.ensure(force=True)

class ProviderRowsTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_kiro_row(self):
        got = mc.extract_provider({"modelId": "claude-sonnet-4.5", "modelName": "Claude Sonnet 4.5",
                                   "supportedInputTypes": ["TEXT", "IMAGE"],
                                   "tokenLimits": {"maxInputTokens": 200000, "maxOutputTokens": 64000}})
        self.assertEqual(got["in"], ["text", "image"])
        self.assertEqual(got["context"], 200000)
        self.assertEqual(got["max_out"], 64000)
        self.assertEqual(got["name"], "Claude Sonnet 4.5")

    def test_lm_studio_and_ollama_rows(self):
        lm = mc.extract_provider({"id": "qwen2.5-vl-7b", "type": "vlm", "quantization": "Q4_K_M",
                                  "max_context_length": 32768, "capabilities": ["tool_use"], "publisher": "lmstudio-community"})
        self.assertIn("image", lm["in"])
        self.assertEqual(lm["quantization"], "Q4_K_M")
        self.assertTrue(lm["tools"])
        self.assertEqual(lm["vendor"], "lmstudio-community")
        emb = mc.extract_provider({"id": "nomic-embed", "type": "embeddings"})
        self.assertEqual(emb["kind"], "embedding")
        ollama = mc.extract_provider({"name": "llava:13b", "capabilities": ["completion", "vision"],
                                      "details": {"parameter_size": "13B", "quantization_level": "Q4_0", "family": "llama"}})
        self.assertIn("image", ollama["in"])
        self.assertEqual(ollama["quantization"], "Q4_0")
        self.assertEqual(ollama["size_label"], "13B")

    def test_gemini_and_generic_flags(self):
        gem = mc.extract_provider({"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"],
                                   "inputTokenLimit": 2048})
        self.assertEqual(gem["kind"], "embedding")
        flags = mc.extract_provider({"id": "x", "supports_vision": False, "supports_function_calling": True,
                                     "deprecated": True})
        self.assertFalse(flags["vision"])
        self.assertTrue(flags["tools"])
        self.assertTrue(flags["deprecated"])

    def test_openrouter_shaped_row_name_without_vendor(self):
        got = mc.extract_provider(OR_ROWS[0])
        self.assertEqual(got["name"], "Claude Haiku 4.5")
        self.assertEqual(got["price"]["output"], 5.0)

    def test_record_raw_keeps_rows_and_truncates_huge_ones(self):
        mc.record_raw("myapi", [{"id": "a", "context_length": 32000, "x": "y"},
                                {"id": "huge", "blob": ["z" * 3000] * 40}, {"no_id": 1}])
        self.assertEqual(mc.raw_row("myapi", "custom", "a")["row"]["x"], "y")
        self.assertTrue(mc.raw_row("myapi", "custom", "huge")["row"]["_truncated"])
        self.assertIsNone(mc.raw_row("myapi", "custom", "missing"))

        mc.record_raw("kiro", [{"modelId": "m1", "supportedInputTypes": ["TEXT"]}], id_field="modelId")
        self.assertIsNotNone(mc.raw_row("kiro-alt", "kiro", "m1"))

class ResolutionTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        mc.refresh_catalog(fetch=fake_fetch)

    def test_every_field_has_source_and_confidence(self):
        card = mc.describe("x", entry=_Entry("genspark", "genspark", "claude-haiku-4-5"))
        for key, item in card["fields"].items():
            self.assertIn("source", item, key)
            self.assertIn(item["confidence"], ("verified", "high", "medium", "low"), key)
        self.assertEqual(card["fields"]["streaming"]["source"], "proxy")
        self.assertEqual(card["fields"]["billing"]["value"], "subscription")
        self.assertTrue(card["fields"]["vision"]["value"])
        self.assertIn(card["fields"]["vision"]["source"], ("openrouter", "models.dev"))
        self.assertEqual(card["fields"]["knowledge_cutoff"]["source"], "models.dev")
        self.assertIn("openrouter", card["raw"])
        group_ids = [g["id"] for g in card["groups"]]
        self.assertEqual(group_ids[:3], ["identity", "modalities", "features"])

    def test_provider_row_beats_catalogue_and_name(self):
        entry = _Entry("myapi", "custom", "claude-haiku-4-5")
        mc.record_raw("myapi", [{"id": "claude-haiku-4-5", "input_modalities": ["text"], "context_length": 50000}])
        card = mc.describe(entry.id, entry=entry)
        self.assertFalse(card["fields"]["vision"]["value"])
        self.assertEqual(card["fields"]["vision"]["source"], "provider")
        self.assertEqual(card["fields"]["context"]["value"], 50000)
        self.assertTrue(card["fields"]["context"]["alts"])

    def test_adapter_without_images_is_no_for_the_proxy(self):
        card = mc.describe("grok-grok-4", entry=_Entry("grok", "grok", "grok-4"))
        self.assertFalse(card["fields"]["vision"]["value"])
        self.assertEqual(card["fields"]["vision"]["source"], "proxy")
        self.assertFalse(mc.adapter_vision("grok-grok-4", "grok", "grok", "grok-4", entry=_Entry("grok", "grok", "grok-4")))

    def test_free_only_from_this_connection(self):

        card = mc.describe("x", entry=_Entry("myapi", "custom", "claude-haiku-4-5"))
        self.assertNotEqual(card["summary"].get("free"), True)
        mc.record_raw("or", [OR_ROWS[2]])
        entry = _Entry("or", "custom", "qwen/qwen3-coder:free")
        self.assertTrue(mc.describe(entry.id, entry=entry)["summary"]["free"])

        mc.record_raw("genspark", [{"id": "deepseek-chat", "pricing": {"input": 0, "output": 0}}])
        card = mc.describe("g", entry=_Entry("genspark", "genspark", "deepseek-chat"))
        self.assertFalse(card["summary"]["free"])

    def test_name_heuristics(self):
        self.assertEqual(mc.heuristics("whisper-large-v3")["kind"], "stt")
        self.assertEqual(mc.heuristics("text-embedding-3-large")["kind"], "embedding")
        self.assertEqual(mc.heuristics("gpt-image-1")["out"], ["image"])
        self.assertTrue(mc.heuristics("deepseek-r1")["reasoning"])
        self.assertTrue(mc.heuristics("claude-sonnet-4.5-thinking")["reasoning"])
        self.assertFalse(mc.heuristics("deepseek-chat")["vision"])
        self.assertTrue(mc.heuristics("qwen2.5-vl-72b")["vision"])
        self.assertTrue(mc.heuristics("llama-3.3-70b")["open_weights"])
        self.assertFalse(mc.heuristics("gpt-5")["open_weights"])
        self.assertTrue(mc.heuristics("openrouter/sonar-pro")["web"])
        self.assertTrue(mc.heuristics("model:free")["free"])
        self.assertEqual(mc.heuristics(""), {})

    def test_kind_from_output_list_but_never_over_speech_names(self):
        card = mc.describe("x", entry=_Entry("myapi", "custom", "whisper-1"))
        self.assertEqual(card["fields"]["kind"]["value"], "stt")
        card = mc.describe("x", entry=_Entry("myapi", "custom", "deepseek/deepseek-chat"))
        self.assertEqual(card["fields"]["kind"]["value"], "chat")
        self.assertNotEqual(card["fields"]["kind"]["source"], "heuristic")

    def test_local_model_vision_follows_projector(self):
        info = {"id": "q", "file": "q.gguf", "size": 4_000_000_000, "repo": "Qwen/Qwen2.5-VL-7B-GGUF",
                "meta": {"quant": "Q4_K_M", "size_label": "7B", "architecture": "qwen2vl", "layers": 28,
                         "context_length": 128000, "vision": False, "license": "apache-2.0"},
                "settings": {"context": 16384, "mmproj": "C:/m/mmproj.gguf", "name": "Qwen VL"}}
        with patch.object(mc, "_local_info", return_value=info):
            entry = _Entry("local", "local", "q")
            card = mc.describe(entry.id, entry=entry)
            self.assertTrue(card["fields"]["vision"]["value"])
            self.assertEqual(card["fields"]["vision"]["source"], "local")
            self.assertEqual(card["fields"]["quantization"]["value"], "Q4_K_M")
            self.assertEqual(card["fields"]["context"]["value"], 16384)
            self.assertEqual(card["fields"]["trained_context"]["value"], 128000)
            self.assertTrue(card["summary"]["free"])
            self.assertTrue(mc.adapter_vision(entry.id, "local", "local", "q", entry=entry))
        info["settings"]["mmproj"] = ""
        with patch.object(mc, "_local_info", return_value=info):
            mc._summary_cache.clear()
            self.assertFalse(mc.adapter_vision(entry.id, "local", "local", "q", entry=entry))
            self.assertFalse(mc.describe(entry.id, entry=entry)["fields"]["vision"]["value"])

    def test_adapter_vision_keeps_old_default_without_evidence(self):
        entry = _Entry("myapi", "custom", "totally-unknown-finetune")
        self.assertTrue(mc.adapter_vision(entry.id, entry.prefix, entry.provider, entry.model, entry=entry))
        entry = _Entry("myapi", "custom", "deepseek/deepseek-chat")
        self.assertFalse(mc.adapter_vision(entry.id, entry.prefix, entry.provider, entry.model, entry=entry))

    def test_unknown_model_still_gets_a_card(self):
        card = mc.describe("nothing-like-this-exists-42", include_raw=False)
        self.assertFalse(card["known"])
        self.assertIn("streaming", card["fields"])

class ProbeTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_png_is_valid(self):
        data = mc.solid_png((10, 20, 30), 8)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (8, 8))
        idat = data.index(b"IDAT")
        length = struct.unpack(">I", data[idat - 4:idat])[0]
        raw = zlib.decompress(data[idat + 4:idat + 4 + length])
        self.assertEqual(raw[:4], b"\x00\x0a\x14\x1e")

    def test_judge(self):
        self.assertEqual(mc.judge_probe("Red", "red"), (True, "correct"))
        self.assertEqual(mc.judge_probe("Это красный цвет.", "red"), (True, "correct"))
        self.assertEqual(mc.judge_probe("Blue", "red"), (False, "wrong"))
        self.assertEqual(mc.judge_probe("I can't see images, sorry", "red"), (False, "blind"))
        self.assertEqual(mc.judge_probe("red or blue?", "red")[1], "ambiguous")
        self.assertEqual(mc.judge_probe("", "red"), (None, "empty"))
        self.assertEqual(mc.judge_probe("hmm", "red"), (None, "unclear"))

    def _run(self, complete, color="green"):
        return asyncio.run(mc.probe_vision("myapi-m", complete=complete, color=color, timeout=2))

    def test_probe_correct_answer_is_stored_and_wins(self):
        sent = {}

        async def complete(model, messages, params):
            sent["model"], sent["messages"], sent["params"] = model, messages, params
            return {"text": "Green", "usage": {"prompt_tokens": 120}}
        result = self._run(complete)
        self.assertTrue(result["vision"])
        self.assertEqual(sent["model"], "myapi-m")
        self.assertTrue(sent["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertLessEqual(sent["params"]["max_tokens"], 64)
        entry = _Entry("myapi", "custom", "m")
        card = mc.describe(entry.id, entry=entry)
        self.assertEqual(card["fields"]["vision"]["source"], "probe")
        self.assertEqual(card["fields"]["vision"]["confidence"], "verified")

        self.assertTrue(mc.adapter_vision(entry.id, "myapi", "custom", "deepseek-chat", entry=entry))
        self.assertTrue(mc.forget_probe(entry.id))
        self.assertIsNone(mc.probe_result(entry.id))

    def test_probe_error_kinds(self):
        class Unsupported(Exception):
            status = 400

        async def unsupported(*_a):
            raise Unsupported("This model does not support image input")
        self.assertEqual(self._run(unsupported)["verdict"], "unsupported")
        self.assertFalse(mc.probe_result("myapi-m")["vision"])

        class Down(Exception):
            status = 503

        async def down(*_a):
            raise Down("upstream image service overloaded")
        mc.forget_probe("myapi-m")
        result = self._run(down)
        self.assertEqual(result["verdict"], "error")
        self.assertIsNone(mc.probe_result("myapi-m"))

        async def slow(*_a):
            await asyncio.sleep(5)
        self.assertEqual(self._run(slow)["verdict"], "timeout")

class AdminRoutesTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        from fastapi.testclient import TestClient
        from app.config import config_store
        from app.main import create_app
        from app.state import Account, ModelEntry, state
        self.state = state

        connection = Account("custom", {"name": "capsapi", "base_url": "https://capsapi.test/v1"}, id="capsapi-acc")
        self.patches = [patch.object(config_store, "_runtime_host", "127.0.0.1"),
                        patch.object(config_store, "_runtime_port", 8317),
                        patch.dict(state.accounts, {connection.id: connection})]
        for item in self.patches:
            item.start()
        self.entry = ModelEntry("capsapi", "custom", "deepseek/deepseek-chat", source="manual")
        state.models[self.entry.id] = self.entry
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8317")

    def tearDown(self):
        self.client.close()
        self.state.models.pop(self.entry.id, None)
        for item in self.patches:
            item.stop()

    def test_capabilities_and_card(self):
        mc.refresh_catalog(fetch=fake_fetch)
        data = self.client.get("/api/admin/models/capabilities").json()
        self.assertIn(self.entry.id, data["capabilities"])
        self.assertFalse(data["capabilities"][self.entry.id]["vision"])
        card = self.client.get(f"/api/admin/models/{self.entry.id}/info")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["matched"]["openrouter"]["id"], "deepseek/deepseek-chat")
        self.assertEqual(self.client.get("/api/admin/models/no-such-model/info").status_code, 404)
        self.assertEqual(self.client.get("/api/admin/models/no-such-model/info?any=1").status_code, 200)

    def test_probe_needs_confirmation_and_forget_does_not_delete_model(self):
        response = self.client.post(f"/api/admin/models/{self.entry.id}/probe", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error_en", response.json())

        async def fake(model_id, **_kw):
            return {"vision": True, "verdict": "correct", "expected": "red", "answer": "red"}
        with patch.object(mc, "probe_vision", side_effect=fake):
            response = self.client.post(f"/api/admin/models/{self.entry.id}/probe", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["vision"])
        response = self.client.post(f"/api/admin/models/{self.entry.id}/probe", json={"action": "forget"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.entry.id, self.state.models)

    def test_catalog_refresh_route(self):
        with patch.object(mc, "refresh_catalog", return_value={"ok": False, "sources": {"openrouter": {"ok": False, "error": "offline"}}}):
            response = self.client.post("/api/admin/models/catalog/refresh")
        self.assertEqual(response.status_code, 502)
        self.assertIn("catalog", response.json())
        self.assertEqual(self.client.get("/api/admin/models/catalog/status").status_code, 200)

    def test_pg_state_uses_shared_vision_and_badges(self):
        mc.refresh_catalog(fetch=fake_fetch)
        from app.state import Account
        account = Account("custom", {"name": "capsapi", "base_url": "http://127.0.0.1:9/v1", "api_key": "x"})
        account.status = "working"
        self.state.accounts[account.id] = account
        try:
            rows = {row["id"]: row for row in self.client.get("/api/admin/pg/state").json()["models"]}
            caps = rows[self.entry.id]["caps"]
            self.assertFalse(caps["vision"])
            self.assertTrue(caps["tools"])
            asyncio.run(mc.probe_vision(self.entry.id, complete=self._green, color="green"))
            rows = {row["id"]: row for row in self.client.get("/api/admin/pg/state").json()["models"]}
            self.assertTrue(rows[self.entry.id]["caps"]["vision"])
        finally:
            self.state.accounts.pop(account.id, None)

    @staticmethod
    async def _green(*_a):
        return {"text": "green"}

class SpeedTest(unittest.TestCase):
    def test_600_models_stay_fast_after_first_pass(self):
        reset_state()
        mc.refresh_catalog(fetch=fake_fetch)
        entries = [_Entry("bulk", "custom", f"vendor/model-{i}-{'vl' if i % 3 else 'chat'}") for i in range(650)]
        for entry in entries:
            mc.capabilities_for(entry.id, entry=entry)
        started = time.perf_counter()
        for entry in entries:
            mc.adapter_vision(entry.id, entry.prefix, entry.provider, entry.model, entry=entry)
            mc.capabilities_for(entry.id, entry=entry)
        self.assertLess(time.perf_counter() - started, 0.25)

class UiWiringTest(unittest.TestCase):
    def test_static_files_and_i18n_parity(self):
        js = (ROOT / "app/static/model_caps.js").read_text(encoding="utf-8")
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertIn('src="/model_caps.js"', html)
        self.assertIn('href="/model_caps.css"', html)
        for call in ("MemuModelCaps.badges(m.id)", "MemuModelCaps.passes(m.id)", "MemuModelCaps.open(", "MemuModelCaps.relabel()"):
            self.assertIn(call, app_js)
        ru_block = js[js.index("Object.assign(I18N.ru"):js.index("Object.assign(I18N.en")]
        en_block = js[js.index("Object.assign(I18N.en"):js.index("const ic =")]
        key = re.compile(r'"(mcap\.[^"]+)":')
        self.assertEqual(set(key.findall(ru_block)), set(key.findall(en_block)))
        used = set(re.findall(r't\("(mcap\.[a-z_.]+)"\)', js))
        self.assertTrue(used <= set(key.findall(ru_block)), used - set(key.findall(ru_block)))

        self.assertIn("copyText(", js)
        self.assertNotIn("navigator.clipboard", js)

    def test_icons_used_in_js_exist_in_css(self):
        js = (ROOT / "app/static/model_caps.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/model_caps.css").read_text(encoding="utf-8")
        app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        names = set(re.findall(r'\bi: "([a-z-]+)"', js)) | set(re.findall(r'ic\("([a-z-]+)"\)', js)) | {"info"}
        names |= set(re.findall(r'data-i="([a-z-]+)"', app_js))
        for name in names:
            self.assertIn(f'data-i="{name}"', css, name)
        self.assertIn("prefers-reduced-motion", css)

if __name__ == "__main__":
    unittest.main()
