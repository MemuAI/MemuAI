"""Local models: GGUF metadata, catalogue, runtime choice and the server pool."""
from __future__ import annotations

import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import local_models as lm

ROOT = Path(__file__).resolve().parents[1]

def gguf_bytes(meta: dict) -> bytes:
    """A minimal valid GGUF v3 header with the given key/values."""
    def string(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<Q", len(raw)) + raw

    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(meta))
    for key, value in meta.items():
        out += string(key)
        if isinstance(value, bool):
            out += struct.pack("<I", 7) + struct.pack("<?", value)
        elif isinstance(value, int):
            out += struct.pack("<I", 4) + struct.pack("<I", value)
        elif isinstance(value, list):
            out += struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", len(value))
            out += b"".join(string(item) for item in value)
        else:
            out += struct.pack("<I", 8) + string(str(value))
    return out + b"\x00" * 64

def write_model(folder: Path, name: str = "Tiny-Chat-Q4_K_M.gguf", **extra) -> Path:
    meta = {"general.architecture": "llama", "general.name": "Tiny Chat", "llama.context_length": 32768,
            "llama.block_count": 22, "general.file_type": 15, "tokenizer.ggml.tokens": ["a", "b", "c"] * 50,
            "tokenizer.chat_template": "{{ messages }}"}
    meta.update(extra)
    path = folder / name
    path.write_bytes(gguf_bytes(meta))
    return path

class MetadataTests(unittest.TestCase):
    def test_header_is_read_and_vocab_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            info = lm.read_gguf(write_model(Path(temp)))
        self.assertEqual(info["architecture"], "llama")
        self.assertEqual(info["name"], "Tiny Chat")
        self.assertEqual(info["context_length"], 32768)
        self.assertEqual(info["quant"], "Q4_K_M")
        self.assertTrue(info["chat_template"])

    def test_not_gguf_and_truncated_files_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "x.gguf"
            bad.write_bytes(b"NOPE" + b"\x00" * 30)
            with self.assertRaises(lm.LocalModelError):
                lm.read_gguf(bad)
            cut = Path(temp) / "cut.gguf"
            cut.write_bytes(gguf_bytes({"general.name": "x"})[:20])
            with self.assertRaises(lm.LocalModelError):
                lm.read_gguf(cut)

    def test_names(self) -> None:
        self.assertEqual(lm.slugify("Qwen2.5-7B-Instruct-Q4_K_M.gguf"), "qwen2.5-7b-instruct-q4-k-m")
        self.assertEqual(lm.slugify("Big-Q5_K_M-00001-of-00003.gguf"), "big-q5-k-m")
        self.assertEqual(lm.quant_from_name("model-IQ4_XS.gguf"), "IQ4_XS")
        self.assertEqual(lm.quant_from_name("model.Q8_0.gguf"), "Q8_0")

class RuntimeChoiceTests(unittest.TestCase):
    ASSETS = [(name, "https://example/" + name) for name in [
        "llama-b1-bin-win-cpu-x64.zip", "llama-b1-bin-win-vulkan-x64.zip", "llama-b1-bin-win-cuda-12.4-x64.zip",
        "llama-b1-bin-win-cuda-13.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "cudart-llama-bin-win-cuda-13.4-x64.zip", "llama-b1-bin-win-cpu-arm64.zip", "llama-b1-bin-ubuntu-x64.tar.gz"]]

    def test_assets_are_chosen_by_pattern(self) -> None:
        self.assertEqual([n for n, _ in lm._choose_assets(self.ASSETS, "cpu")], ["llama-b1-bin-win-cpu-x64.zip"])
        self.assertEqual([n for n, _ in lm._choose_assets(self.ASSETS, "vulkan")], ["llama-b1-bin-win-vulkan-x64.zip"])

        self.assertEqual([n for n, _ in lm._choose_assets(self.ASSETS, "cuda")],
                         ["llama-b1-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"])
        with self.assertRaises(lm.LocalModelError):
            lm._choose_assets([("llama-b1-bin-macos-arm64.tar.gz", "x")], "cpu")

class CatalogueAndPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [patch.object(lm, "DATA_DIR", str(self.data)), patch.object(lm, "sync_catalog", lambda: None)]
        for item in self.patches:
            item.start()
        lm.pool.command_override = [sys.executable, str(ROOT / "tests" / "fake_llama_server.py")]

    def tearDown(self) -> None:
        lm.pool.stop_all()
        lm.pool.command_override = None
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_register_update_and_remove(self) -> None:
        source = write_model(self.data)

        with patch.object(lm, "fit", return_value={"level": "cpu"}):
            row = lm.register_file(source)
        self.assertEqual(row["id"], "tiny-chat-q4-k-m")
        self.assertEqual(row["public_id"], "local-tiny-chat-q4-k-m")
        self.assertEqual(row["settings"]["context"], 8192)
        self.assertEqual(row["settings"]["name"], "Tiny Chat")
        again = lm.register_file(source)
        self.assertEqual(again["id"], row["id"], "the same file is not added twice")
        updated = lm.update_model(row["id"], {"context": 99999999, "temperature": "0.4", "unknown": 1})
        self.assertEqual(updated["settings"]["context"], 1_048_576)
        self.assertAlmostEqual(updated["settings"]["temperature"], 0.4)
        with self.assertRaises(lm.LocalModelError):
            lm.update_model(row["id"], {"context": "lots"})

        lm.remove_model(row["id"], delete_file=True)
        self.assertTrue(source.exists())
        self.assertIsNone(lm.get_model(row["id"]))

    def test_runtime_removal_only_deletes_owned_build(self) -> None:
        outside = self.data / "external" / "b123-cpu"
        outside.mkdir(parents=True)
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        lm._mutate(lambda data: data.update(runtime={"folder": str(outside)}))
        lm.remove_runtime()
        self.assertTrue(marker.exists())

        owned = lm.runtime_dir() / "b123-cpu"
        owned.mkdir(parents=True)
        (owned / "server.exe").write_bytes(b"test")
        lm._mutate(lambda data: data.update(runtime={"folder": str(owned)}))
        lm.remove_runtime()
        self.assertFalse(owned.exists())

    def test_projector_file_is_not_a_model(self) -> None:
        with self.assertRaises(lm.LocalModelError):
            lm.register_file(write_model(self.data, "mmproj-f16.gguf", **{"general.architecture": "clip"}))

    def test_server_starts_serves_and_stops(self) -> None:
        slug = lm.register_file(write_model(self.data))["id"]
        port = lm.pool.ensure(slug, timeout=20)
        self.assertEqual(lm.pool.states()[slug]["state"], "ready")
        self.assertIn("fake llama-server", "\n".join(lm.pool.log(slug)))
        self.assertEqual(lm.pool.ensure(slug, timeout=20), port, "a ready server is reused")
        lm.pool.stop(slug)
        self.assertEqual(lm.pool.states()[slug]["state"], "stopped")

    def test_only_launch_settings_restart_the_server(self) -> None:
        slug = lm.register_file(write_model(self.data))["id"]
        lm.pool.ensure(slug, timeout=20)
        lm.update_model(slug, {"name": "Renamed", "temperature": 0.3, "system_prompt": "x"})
        self.assertEqual(lm.pool.states()[slug]["state"], "ready", "per-request settings keep the server")
        lm.update_model(slug, {"context": 4096})
        self.assertEqual(lm.pool.states()[slug]["state"], "stopped", "a new context needs a restart")

    def test_only_max_loaded_models_stay_in_memory(self) -> None:
        first = lm.register_file(write_model(self.data, "a-Q4_K_M.gguf"))["id"]
        second = lm.register_file(write_model(self.data, "b-Q4_K_M.gguf"))["id"]
        lm.pool.ensure(first, timeout=20)
        lm.pool.ensure(second, timeout=20)
        states = lm.pool.states()
        self.assertEqual(states[first]["state"], "stopped")
        self.assertEqual(states[second]["state"], "ready")

    def test_a_crashing_server_reports_its_log(self) -> None:
        slug = lm.register_file(write_model(self.data))["id"]
        lm.update_model(slug, {"extra_args": "--crash"})
        with self.assertRaises(lm.LocalModelError) as caught:
            lm.pool.ensure(slug, timeout=20)
        self.assertIn("failed to allocate", str(caught.exception))

    def test_provider_streams_through_the_server(self) -> None:
        from app.providers.local import LocalProvider
        slug = lm.register_file(write_model(self.data))["id"]
        lm.update_model(slug, {"temperature": 0.25, "system_prompt": "Be brief"})

        async def collect():
            provider = LocalProvider()
            parts = []
            async for event in provider.chat_stream(None, slug, [{"role": "user", "content": "hello there"}], {}):
                parts.append(event.get("text", ""))
            return "".join(parts)

        text = asyncio.run(collect())
        self.assertIn(f"[local-{slug}]", text)
        self.assertIn("t=0.25", text)
        self.assertIn("sys=Be brief", text)
        self.assertIn("hello there", text)

    def test_images_without_projector_are_refused_honestly(self) -> None:
        from app.providers.base import ProviderServerError
        from app.providers.local import LocalProvider
        slug = lm.register_file(write_model(self.data))["id"]
        message = [{"role": "user", "content": [{"type": "text", "text": "what"},
                                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]

        async def run():
            async for _ in LocalProvider().chat_stream(None, slug, message, {}):
                pass

        with self.assertRaises(ProviderServerError):
            asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
