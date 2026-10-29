"""Round 33: local model quality (runtime, provider) and the local model wizard.

Hardware is mocked everywhere (``local_hw.finish`` / ``MEMUAI_HW_MOCK`` / the ``runner`` hook):
the real probes (DXGI, CIM, nvidia-smi) need Windows and a GPU.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import local_hw, local_models as lm, local_wizard as wiz  # noqa: E402
from app.providers import local as local_provider  # noqa: E402

GB = 1024 ** 3
CYRILLIC = re.compile("[А-Яа-яЁё]")

PC_4070 = {"cpu": {"name": "Ryzen 7 7700X", "threads": 16, "cores": 8, "avx2": True, "avx512": True},
           "ram": {"total": 32 * GB, "type": "DDR5", "speed": 6000},
           "gpus": [{"name": "NVIDIA GeForce RTX 4070", "vram": 12 * GB},
                    {"name": "AMD Radeon(TM) Graphics", "vram": 512 * 1024 ** 2}],
           "disk": {"free": 400 * GB}}
LAPTOP = {"cpu": {"name": "Core i5-1235U", "threads": 12, "cores": 10, "avx2": True},
          "ram": {"total": 16 * GB, "type": "DDR4", "speed": 3200},
          "gpus": [{"name": "Intel(R) Iris(R) Xe Graphics", "vram": 128 * 1024 ** 2}], "disk": {"free": 60 * GB}}
WORKSTATION = {"cpu": {"cores": 24, "threads": 32, "avx2": True}, "ram": {"total": 128 * GB, "type": "DDR5", "speed": 6400},
               "gpus": [{"name": "NVIDIA GeForce RTX 5090", "vram": 32 * GB}], "disk": {"free": 2000 * GB}}

class HardwareTests(unittest.TestCase):
    def test_finish_marks_integrated_graphics_and_picks_backend(self):
        hw = local_hw.finish(PC_4070)
        self.assertEqual(hw["main_gpu"]["name"], "NVIDIA GeForce RTX 4070")
        self.assertEqual(hw["backend"], "cuda")
        self.assertTrue(next(g for g in hw["gpus"] if "Radeon" in g["name"])["integrated"])
        self.assertEqual(hw["main_gpu"]["bandwidth"], 504)
        self.assertEqual(hw["ram"]["bandwidth"], int(6000 * 8 * 2 / 1000 * 0.75))
        laptop = local_hw.finish(LAPTOP)
        self.assertIsNone(laptop["main_gpu"])
        self.assertEqual(laptop["backend"], "cpu")
        amd = local_hw.finish({"gpus": [{"name": "AMD Radeon RX 7800 XT", "vram": 16 * GB}], "ram": {"total": 32 * GB}})
        self.assertEqual(amd["backend"], "vulkan")
        arc = local_hw.finish({"gpus": [{"name": "Intel(R) Arc(TM) A770 Graphics", "vram": 16 * GB}], "ram": {"total": 32 * GB}})
        self.assertFalse(arc["gpus"][0]["integrated"])
        self.assertEqual(arc["backend"], "vulkan")

    def test_bandwidth_tables(self):
        self.assertEqual(local_hw.gpu_bandwidth("NVIDIA GeForce RTX 4090"), 1008)
        self.assertLess(local_hw.gpu_bandwidth("NVIDIA GeForce RTX 4090 Laptop GPU"), 1008)
        self.assertEqual(local_hw.gpu_bandwidth("Mystery GPU", 8 * GB), 272)
        self.assertEqual(local_hw.ram_bandwidth("DDR4", 3200), int(51.2 * 0.75))
        self.assertEqual(local_hw.ram_bandwidth(), 50)

    def test_mock_env_and_nvidia_smi_parsing(self):
        with patch.dict(os.environ, {"MEMUAI_HW_MOCK": json.dumps(LAPTOP)}):
            hw = local_hw.detect()
        self.assertTrue(hw["mocked"])
        self.assertEqual(hw["ram"]["total"], 16 * GB)
        calls = []

        def runner(args, timeout=0):
            calls.append(args)
            return SimpleNamespace(stdout="NVIDIA GeForce RTX 3060, 12288, 11000, 566.36, 8.6\n")

        with patch.object(local_hw, "runner", runner):
            rows = local_hw._nvidia_smi()
        self.assertEqual(rows[0]["vram"], 12288 * 1024 * 1024)
        self.assertEqual(rows[0]["vram_free"], 11000 * 1024 * 1024)
        self.assertEqual(rows[0]["compute"], "8.6")
        self.assertIn("--format=csv,noheader,nounits", calls[0])

    def test_legacy_shape_for_local_models(self):
        legacy = local_hw.legacy(local_hw.finish(PC_4070))
        self.assertEqual(legacy["ram"], 32 * GB)
        self.assertEqual(legacy["cpu_threads"], 16)
        self.assertEqual(legacy["cpu_cores"], 8)
        self.assertEqual({g["name"] for g in legacy["gpus"]}, {"NVIDIA GeForce RTX 4070", "AMD Radeon(TM) Graphics"})

class RuntimeQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hw = local_hw.legacy(local_hw.finish(PC_4070))
        self.patches = [patch.object(lm, "hardware", return_value=self.hw)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()
        lm._fit_support.clear()

    def test_kv_cache_from_gguf_metadata(self):
        dense = {"layers": 32, "head_count_kv": 8, "key_length": 128, "value_length": 128}
        self.assertEqual(lm.kv_bytes_per_token(dense), 32 * 8 * 256 * 2)
        hybrid = dict(dense, layers=64, head_count_kv=4, key_length=256, value_length=256, full_attention_interval=4)
        self.assertEqual(lm.kv_bytes_per_token(hybrid), 16 * 4 * 512 * 2)
        mla = {"layers": 47, "kv_lora_rank": 512, "rope_dims": 64}
        self.assertEqual(lm.kv_bytes_per_token(mla), 47 * 576 * 2)
        self.assertEqual(lm.kv_bytes_per_token({}), 0)

    def test_fit_levels_ignore_integrated_graphics(self):
        self.assertEqual(lm.fit(6 * GB)["level"], "gpu")
        partial = lm.fit(20 * GB)
        self.assertEqual(partial["level"], "partial")
        self.assertTrue(0 < partial["gpu_share"] < 1)
        self.assertEqual(lm.fit(60 * GB)["level"], "too_big")
        with patch.object(lm, "hardware", return_value=local_hw.legacy(local_hw.finish(LAPTOP))):
            self.assertEqual(lm.fit(4 * GB)["level"], "ram")

    def _command(self, row, binary="/x/llama-server", fit=None, backend="cuda", server_path=""):
        catalog = {"models": {}, "settings": dict(lm.DEFAULT_SETTINGS, server_path=server_path),
                   "runtime": {"backend": backend, "tag": "b7800"}}
        pool = lm.Pool()
        with patch.object(lm, "_load_catalog", return_value=catalog), \
                patch.object(lm, "server_binary", return_value=Path(binary)), \
                patch.object(lm, "supports_fit", return_value=bool(fit)):
            return pool.command(row, 5000, "local-x")

    def test_auto_layers_let_llama_cpp_fit_the_model(self):
        row = dict(lm.MODEL_DEFAULTS, path="/m/model.gguf", size=20 * GB, meta={"layers": 40})
        args = self._command(row, fit=True)
        self.assertNotIn("-ngl", args)
        args = self._command(row, fit=False)
        layers = int(args[args.index("-ngl") + 1])
        self.assertTrue(0 < layers < 40, layers)
        small = dict(row, size=4 * GB)
        args = self._command(small, fit=False)
        self.assertEqual(args[args.index("-ngl") + 1], "999")
        args = self._command(row, fit=True, backend="cpu")
        self.assertEqual(args[args.index("-ngl") + 1], "0")
        explicit = dict(row, gpu_layers=12, draft="")
        args = self._command(explicit, fit=True)
        self.assertEqual(args[args.index("-ngl") + 1], "12")

    def test_supports_fit_reads_the_help_text(self):
        folder = Path(self.tmp.name)
        for name, text, expected in (("new", "-ngl, --n-gpu-layers N\n-fit, --fit [on|off]  fit to memory", True),
                                     ("old", "-ngl, --n-gpu-layers N\n--flash-attn", False)):
            script = folder / f"llama-{name}"
            script.write_text("mock executable", encoding="utf-8")
            with patch.object(lm, "_load_catalog", return_value={"models": {}, "settings": {}, "runtime": {}}), \
                    patch.object(lm.subprocess, "run", return_value=SimpleNamespace(stdout=text, stderr="")) as probe:
                self.assertEqual(lm.supports_fit(str(script)), expected, name)
                self.assertEqual(probe.call_args.args[0], [str(script), "--help"])

    def test_default_context_follows_the_hardware(self):
        self.assertEqual(lm.default_context(5 * GB, {"context_length": 32768}), 16384)
        self.assertEqual(lm.default_context(20 * GB, {"context_length": 32768}), 8192)
        self.assertEqual(lm.default_context(1 * GB, {"context_length": 4096}), 4096)
        self.assertEqual(lm.default_context(1 * GB, {}), 8192)

    def test_preset_never_carries_paths_or_arguments(self):
        job = lm.Download("a/b", ["x.gguf"], preset={"context": 32768, "extra_args": "--evil", "mmproj": "/etc/x",
                                                      "temperature": 0.6})
        self.assertEqual(job.preset, {"context": 32768, "temperature": 0.6})
        self.assertEqual(job.public()["preset"], {"context": 32768, "temperature": 0.6})

    def test_imatrix_files_are_not_models(self):
        tree = [{"type": "file", "path": "Model-Q4_K_M.gguf", "size": 5 * GB},
                {"type": "file", "path": "imatrix_unsloth.gguf", "size": 10 * 1024 ** 2},
                {"type": "file", "path": "mmproj-F16.gguf", "size": 800 * 1024 ** 2}]

        class Response:
            status_code = 200

            def json(self):
                return tree

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, *args, **kwargs):
                return Response()

        with patch.object(lm, "_client", return_value=Client()), patch.object(lm, "_hf_headers", return_value={}):
            files = lm.hf_files("org/repo")
        self.assertEqual([group["name"] for group in files["files"]], ["Model-Q4_K_M.gguf"])
        self.assertEqual(len(files["projectors"]), 1)

class ProviderTests(unittest.TestCase):
    def collect(self, chunks, native=False):
        splitter = local_provider.ThinkSplitter()
        splitter.native = native
        events = []
        for chunk in chunks:
            events += splitter.feed(chunk)
        events += splitter.flush()
        text = "".join(e["text"] for e in events if e["type"] == "text")
        reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
        return text, reasoning

    def test_think_tags_move_to_reasoning_across_chunks(self):
        self.assertEqual(self.collect(["<thi", "nk>plan", " it</th", "ink>\n\nAnswer", "!"]), ("Answer!", "plan it"))
        self.assertEqual(self.collect(["◁think▷x◁/think▷y"]), ("y", "x"))
        self.assertEqual(self.collect(["Hello <think>not at start</think>"]), ("Hello <think>not at start</think>", ""))
        self.assertEqual(self.collect(["<think>unfinished"]), ("", "unfinished"))
        self.assertEqual(self.collect(["<think>a</think>b"], native=True), ("<think>a</think>b", ""))
        self.assertEqual(self.collect(["", "  ", "Hi"]), ("  Hi", ""))

    def test_tool_call_events_keep_indexes(self):
        slots = {"next": 0, "last": 0}
        events = local_provider.tool_call_events([{"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{\"a\""}}], slots)
        events += local_provider.tool_call_events([{"function": {"arguments": ":1}"}}], slots)
        self.assertEqual([e["index"] for e in events], [0, 0])
        self.assertEqual(events[0]["name"], "f")
        self.assertEqual(events[1]["arguments"], ":1}")

    def test_context_overflow_error_is_explained(self):
        body = json.dumps({"error": {"code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 9000,
                                     "n_ctx": 8192, "message": "the request exceeds the available context size"}})
        error = local_provider.http_error(400, body)
        self.assertEqual(error.status, 400)
        self.assertIn("9000", error.message)
        self.assertIn("Context length", error.message)
        self.assertIn("Длину контекста", error.message_ru)
        self.assertIsNone(CYRILLIC.search(error.message))
        plain = local_provider.http_error(500, "boom")
        self.assertIn("HTTP 500", plain.message)

    def test_reasoning_effort_becomes_template_kwargs(self):
        self.assertEqual(local_provider.template_kwargs("minimal"), {"enable_thinking": False, "reasoning_effort": "low"})
        self.assertEqual(local_provider.template_kwargs("high")["reasoning_effort"], "high")
        self.assertEqual(local_provider.template_kwargs(None), {})

    def test_tools_reach_llama_server_and_tool_calls_come_back(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                chunks = [{"choices": [{"delta": {"reasoning_content": "need weather"}}]},
                          {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                                  "function": {"name": "get_weather", "arguments": ""}}]}}]},
                          {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"city\":\"Oslo\"}"}}]}}]},
                          {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
                for chunk in chunks:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        info = {"settings": dict(lm.MODEL_DEFAULTS, mmproj="")}
        tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]

        async def run():
            provider = local_provider.LocalProvider()
            return [event async for event in provider.chat_stream(
                None, "m", [{"role": "user", "content": "weather?"}],
                {"tools": tools, "tool_choice": "auto", "effort": "low"})]

        try:
            with patch.object(lm, "get_model", return_value=info), \
                    patch.object(lm.pool, "ensure", return_value=server.server_address[1]), \
                    patch.object(lm.pool, "touch"):
                events = asyncio.run(run())
        finally:
            server.shutdown()
        self.assertEqual(seen["body"]["tools"], tools)
        self.assertEqual(seen["body"]["tool_choice"], "auto")
        self.assertEqual(seen["body"]["chat_template_kwargs"]["reasoning_effort"], "low")
        calls = [e for e in events if e["type"] == "tool_call"]
        self.assertEqual(calls[0]["name"], "get_weather")
        self.assertEqual("".join(e["arguments"] or "" for e in calls), "{\"city\":\"Oslo\"}")
        self.assertIn({"type": "finish", "reason": "tool_calls"}, events)
        self.assertEqual(events[0], {"type": "reasoning", "text": "need weather"})

    def test_local_models_accept_tools_in_the_api(self):
        from app import tool_protocol
        with patch.object(tool_protocol, "resolve", return_value=SimpleNamespace(provider="local")):
            self.assertTrue(tool_protocol.tool_provider_supported("local-x"))
        with patch.object(tool_protocol, "resolve", return_value=SimpleNamespace(provider="genspark")):
            self.assertFalse(tool_protocol.tool_provider_supported("x"))
        self.assertTrue(tool_protocol.TOOL_PROVIDER_ERROR.startswith("Tool calls require"))

class CatalogTests(unittest.TestCase):
    def test_bundled_catalogue_is_complete(self):
        catalog = json.loads(wiz.BUNDLED.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(catalog["models"]), 30)
        ids = [row["id"] for row in catalog["models"]]
        self.assertEqual(len(ids), len(set(ids)))
        for row in catalog["models"]:
            with self.subTest(row["id"]):
                self.assertRegex(row["repo"], r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
                self.assertTrue(row["files"], "no files")
                for quant, info in row["files"].items():
                    self.assertGreater(info["size"], 100 * 1024 ** 2)
                    self.assertTrue(all(path.endswith(".gguf") for path in info["files"]))
                self.assertGreater(row.get("ctx_max") or 0, 4000)
                self.assertTrue((row.get("kv") or {}).get("per_token") or (row.get("kv") or {}).get("fixed"))
                for use in ("chat", "coding", "ru", "vision", "reasoning", "agent"):
                    self.assertTrue(0 <= row["scores"][use] <= 100)
                self.assertTrue(row["why"]["ru"] and row["why"]["en"])
                self.assertIsNone(CYRILLIC.search(row["why"]["en"]))
                self.assertIn("vision", row["caps"])
                if row["caps"]["vision"]:
                    self.assertTrue(row.get("mmproj"), "vision model without a projector")

    def test_kv_from_config_layouts(self):
        qwen = {"text_config": {"num_hidden_layers": 64, "num_attention_heads": 24, "num_key_value_heads": 4, "head_dim": 256,
                                "layer_types": ["linear_attention"] * 3 * 16 + ["full_attention"] * 16}}
        self.assertEqual(wiz.kv_from_config(qwen), {"per_token": 16 * 4 * 256 * 4, "fixed": 0})
        gemma = {"num_hidden_layers": 6, "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 256,
                 "global_head_dim": 512, "num_global_key_value_heads": 1, "sliding_window": 1024,
                 "layer_types": ["sliding_attention"] * 5 + ["full_attention"]}
        self.assertEqual(wiz.kv_from_config(gemma), {"per_token": 512 * 4, "fixed": 5 * 8 * 256 * 4 * 1024})
        glm = {"num_hidden_layers": 47, "kv_lora_rank": 512, "qk_rope_head_dim": 64, "num_attention_heads": 20}
        self.assertEqual(wiz.kv_from_config(glm)["per_token"], 47 * 576 * 2)
        nemotron = {"num_hidden_layers": 4, "num_attention_heads": 32, "num_key_value_heads": 2, "head_dim": 128,
                    "layers_block_type": ["mamba", "moe", "mamba", "attention"]}
        self.assertEqual(wiz.kv_from_config(nemotron)["per_token"], 2 * 128 * 4)

    def test_pick_files_prefers_plain_quants_and_f16_projector(self):
        tree = [{"type": "file", "path": p, "size": s} for p, s in (
            ("M-UD-Q4_K_M.gguf", 10), ("M-Q4_K_M.gguf", 9), ("M-UD-Q3_K_XL.gguf", 7), ("BF16/M-BF16-00001-of-00002.gguf", 30),
            ("BF16/M-BF16-00002-of-00002.gguf", 5), ("mmproj-BF16.gguf", 2), ("mmproj-F16.gguf", 2), ("MTP/mtp-M-Q8_0.gguf", 1),
            ("imatrix_unsloth.gguf", 1), ("eagle3-M-Q8_0.gguf", 1))]
        picked = wiz.pick_files(tree, ["Q3_K_M", "Q4_K_M", "Q8_0"])
        self.assertEqual(picked["files"]["Q4_K_M"]["files"], ["M-Q4_K_M.gguf"])
        self.assertEqual(picked["files"]["Q3_K_M"]["quant"], "Q3_K_XL")
        self.assertNotIn("Q8_0", picked["files"])
        self.assertEqual(picked["mmproj"]["path"], "mmproj-F16.gguf")
        self.assertEqual(picked["draft"]["path"], "MTP/mtp-M-Q8_0.gguf")

    def test_refresh_entry_uses_the_api(self):
        entry = {"id": "x", "repo": "org/x-GGUF", "base": "org/x", "quants": ["Q4_K_M"], "caps": {"vision": False}}

        def get_json(url):
            if url.endswith("tree/main?recursive=true"):
                return [{"type": "file", "path": "x-Q4_K_M.gguf", "size": 5 * GB}]
            if "/api/models/org/x-GGUF?" in url:
                return {"gguf": {"total": 8_000_000_000, "context_length": 131072, "architecture": "llama"}}
            if url.endswith("config.json"):
                return {"num_hidden_layers": 32, "num_attention_heads": 32, "num_key_value_heads": 8, "hidden_size": 4096}
            raise AssertionError(url)

        fresh = wiz.refresh_entry(entry, get_json)
        self.assertEqual(fresh["files"]["Q4_K_M"]["size"], 5 * GB)
        self.assertEqual(fresh["ctx_max"], 131072)
        self.assertEqual(fresh["kv"]["per_token"], 32 * 8 * 128 * 4)
        self.assertFalse(fresh["moe"])

class RecommendTests(unittest.TestCase):
    def rec(self, goals, profile=PC_4070):
        return wiz.recommend(goals, profile, installed={})

    def test_results_fit_and_are_explained_in_both_languages(self):
        result = self.rec({"uses": ["chat"], "language": "ru"})
        self.assertTrue(result["results"])
        for row in result["results"]:
            self.assertIn(row["placement"], ("gpu", "partial", "cpu"))
            self.assertGreater(row["tps"], 1)
            self.assertTrue(row["why"] and row["why_en"])
            for text in row["why_en"] + row["warnings_en"]:
                self.assertIsNone(CYRILLIC.search(text), text)
            self.assertIn(row["quant"], [a["quant"] for a in row["alternatives"]])
            self.assertLessEqual(row["context"], row["ctx_max"])
        self.assertIsNone(CYRILLIC.search(result["summary_en"]))
        scores = [row["score"] for row in result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_hard_filters(self):
        vision = self.rec({"uses": ["vision"]})
        catalog = {row["id"]: row for row in wiz.load_catalog()["models"]}
        self.assertTrue(all(catalog[row["id"]]["caps"]["vision"] for row in vision["results"]))
        self.assertTrue(any(e["reason_en"] == "cannot see images" for e in vision["excluded"]))
        agent = self.rec({"uses": ["agent"]})
        self.assertNotIn("yandexgpt-5-lite-8b", [row["id"] for row in agent["results"] + agent["more"]])
        open_only = self.rec({"uses": ["chat"], "license": "open"})
        self.assertTrue(all(catalog[row["id"]]["license"] in ("apache-2.0", "mit") for row in open_only["results"] + open_only["more"]))

    def test_hardware_drives_the_choice(self):
        laptop = self.rec({"uses": ["chat"]}, LAPTOP)
        self.assertTrue(all(row["placement"] == "cpu" for row in laptop["results"]))
        self.assertTrue(any(e["id"] == "qwen3.8-27b" for e in laptop["excluded"]))
        big = self.rec({"uses": ["chat", "coding"], "priority": 20}, WORKSTATION)
        self.assertEqual(big["results"][0]["placement"], "gpu")
        self.assertGreaterEqual(big["results"][0]["params_b"], 20)

    def test_speed_priority_prefers_faster_models(self):
        quality = self.rec({"uses": ["chat"], "priority": 0}, PC_4070)["results"][0]
        speed = self.rec({"uses": ["chat"], "priority": 100}, PC_4070)["results"][0]
        self.assertGreaterEqual(speed["tps"], quality["tps"])

    def test_context_is_reduced_to_fit_and_warned(self):
        small_gpu = {"gpus": [{"name": "NVIDIA GeForce RTX 3050", "vram": 4 * GB}], "ram": {"total": 8 * GB}, "cpu": {"cores": 4}}
        result = self.rec({"uses": ["docs"], "context": 131072}, small_gpu)
        reduced = [row for row in result["results"] + result["more"] if row["context"] < min(131072, row["ctx_max"])]
        self.assertTrue(reduced)
        self.assertTrue(any("context is reduced" in w for w in reduced[0]["warnings_en"]))

    def test_keep_free_memory_shrinks_the_budget(self):
        free = wiz.budgets(local_hw.finish(PC_4070), 0)["ram"]
        kept = wiz.budgets(local_hw.finish(PC_4070), 8)["ram"]
        self.assertEqual(free - kept, 8 * GB)

    def test_preset_uses_catalogue_sampling(self):
        row = next(r for r in self.rec({"uses": ["chat"]}, WORKSTATION)["results"] + self.rec({"uses": ["chat"]}, WORKSTATION)["more"]
                   if r["id"].startswith("qwen3"))
        self.assertEqual(row["preset"]["gpu_layers"], -1)
        self.assertEqual(row["preset"]["top_k"], 20)
        self.assertEqual(row["preset"]["context"], row["context"])

    def test_install_resolves_files_live_and_passes_the_preset(self):
        listing = {"files": [{"name": "Qwen3.5-4B-Q4_K_M.gguf", "files": ["Qwen3.5-4B-Q4_K_M.gguf"], "size": 1}],
                   "projectors": [{"path": "mmproj-F16.gguf", "size": 1}], "drafts": []}
        calls = {}

        def start_download(repo, files, projector="", draft="", preset=None):
            calls.update(repo=repo, files=files, projector=projector, draft=draft, preset=preset)
            return {"id": "d1", "state": "queued"}

        runtime = {"installed": True, "install": {"state": "idle"}}
        with patch.object(lm, "hf_files", return_value=listing), patch.object(lm, "start_download", start_download), \
                patch.object(lm, "runtime_status", return_value=runtime):
            result = wiz.install("qwen3.5-4b", "Q4_K_M", 32768, vision=True,
                                 preset={"name": "Qwen3.5 4B", "context": 16384, "temperature": 0.6})
        self.assertEqual(result["download"]["id"], "d1")
        self.assertEqual(calls["repo"], "unsloth/Qwen3.5-4B-GGUF")
        self.assertEqual(calls["files"], ["Qwen3.5-4B-Q4_K_M.gguf"])
        self.assertEqual(calls["projector"], "mmproj-F16.gguf")
        self.assertEqual(calls["preset"]["context"], 32768)
        with self.assertRaises(lm.LocalModelError):
            wiz.install("no-such-model", "Q4_K_M")

class RouteTests(unittest.TestCase):
    def test_wizard_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import local_wizard_api
        app = FastAPI()
        local_wizard_api.register(app)
        client = TestClient(app)
        with patch.object(local_wizard_api, "_auth", return_value=None), \
                patch.dict(os.environ, {"MEMUAI_HW_MOCK": json.dumps(PC_4070)}), \
                patch.object(lm, "runtime_status", return_value={"installed": False, "tag": "", "backend": "",
                                                                  "platform": "win32", "install": {"state": "idle"}}), \
                patch.object(wiz, "installed_index", return_value={}):
            hw = client.get("/api/admin/local/wizard/hardware").json()
            self.assertEqual(hw["hardware"]["main_gpu"]["name"], "NVIDIA GeForce RTX 4070")
            self.assertFalse(hw["runtime"]["installed"])
            rec = client.post("/api/admin/local/wizard/recommend", json={"goals": {"uses": ["coding"]}}).json()
            self.assertTrue(rec["results"])
            edited = client.post("/api/admin/local/wizard/recommend",
                                 json={"goals": {"uses": ["chat"]}, "hardware": LAPTOP}).json()
            self.assertIsNone(edited["hardware"]["main_gpu"])
            bad = client.post("/api/admin/local/wizard/install", json={"id": "nope", "quant": "Q4_K_M"})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(bad.json()["error_en"], "This model is not in the wizard catalogue")
            status = client.get("/api/admin/local/wizard/catalog/status").json()
            self.assertGreaterEqual(status["count"], 30)
        with patch.object(local_wizard_api, "_auth", return_value=__import__("fastapi").responses.JSONResponse({"error": "x"}, 401)):
            self.assertEqual(client.get("/api/admin/local/wizard/hardware").status_code, 401)

class FrontendWiringTests(unittest.TestCase):
    def test_index_loads_the_wizard_and_files_exist(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/local-wizard.js?v=3.5-r33"></script>', html)
        self.assertIn('href="/local-wizard.css?v=3.5-r33"', html)
        self.assertTrue((ROOT / "app/static/local-wizard.js").is_file())
        self.assertTrue((ROOT / "app/static/local-wizard.css").is_file())
        local = (ROOT / "app/static/local.js").read_text(encoding="utf-8")
        self.assertIn("MemuLocalWizard.open()", local)

    def test_every_wizard_api_path_exists(self):
        js = (ROOT / "app/static/local-wizard.js").read_text(encoding="utf-8")
        from app import local_wizard_api
        routes = {route.path for route in local_wizard_api.router.routes}
        for path in set(re.findall(r"/api/admin/local/wizard/[a-z/]+", js)):
            self.assertIn(path.rstrip("/"), routes, path)

    def test_english_branches_have_no_cyrillic(self):
        js = (ROOT / "app/static/local-wizard.js").read_text(encoding="utf-8")
        for match in re.finditer(r'L\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', js):
            self.assertIsNone(CYRILLIC.search(match.group(2)), match.group(2))

    def test_packaging_ships_the_catalogue(self):
        spec = (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("('app/local_catalog', 'app/local_catalog')", spec)
        self.assertIn("'app.local_wizard_api'", spec)
        self.assertEqual(build.count('"app/local_catalog;app/local_catalog"'), 2)
        self.assertEqual(build.count('"app.local_wizard_api"'), 2)

if __name__ == "__main__":
    unittest.main()
