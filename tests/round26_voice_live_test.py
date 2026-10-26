"""Round 26: MemuAI Live (Yandex Realtime, worklet, turns), voice runtime, local model downloads.

No network: provider events, Hugging Face and Piper downloads are fixtures.
"""
from __future__ import annotations

import collections
import errno
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import local_models as lm
from app import realtime_live
from app.voice_runtime import VoiceRuntime, prepare_tts_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_models_test import gguf_bytes  # noqa: E402

YANDEX_URL = "wss://ai.api.cloud.yandex.net/v1/realtime/openai"
YANDEX_MODEL = "gpt://b1gfolder/speech-realtime-250923/latest"

def adapter(provider="custom", url=YANDEX_URL, key="AQVNfixture", model=YANDEX_MODEL):
    prefs = {"voice_realtime_provider": provider, "voice_realtime_base_url": url,
             "voice_realtime_api_keys": {provider: key}, "voice_realtime_model": model}
    return realtime_live.build_adapter(realtime_live.LiveConfig.from_preferences(prefs, "Persona"))

class YandexRealtimeTests(unittest.TestCase):
    def test_api_key_goes_as_api_key_iam_token_as_bearer(self):

        self.assertEqual(adapter().headers(), {"Authorization": "Api-Key AQVNfixture"})
        self.assertEqual(adapter(key="t1.iamtoken").headers(), {"Authorization": "Bearer t1.iamtoken"})
        self.assertEqual(adapter(key="Bearer explicit").headers(), {"Authorization": "Bearer explicit"})
        other = adapter(url="wss://realtime.example.com/v1/realtime", model="rt")
        self.assertEqual(other.headers(), {"Authorization": "Bearer AQVNfixture"})
        self.assertIn("model=gpt%3A//b1gfolder", adapter().urls()[0])

    def test_no_frames_or_tools_for_yandex(self):

        live = adapter()
        self.assertEqual((live.video_mode, live.search_mode), ("", ""))
        session = live.setup_messages()[0]["session"]
        self.assertNotIn("tools", session)
        live.keep_frame("AAAA")
        self.assertEqual(live.text_messages("hi")[0]["item"]["content"][0]["type"], "input_text")
        ready = live.ready_event()
        self.assertEqual((ready["video"], ready["search"]), ("", ""))

        other = adapter(url="wss://realtime.example.com/v1/realtime", model="rt")
        self.assertEqual((other.video_mode, other.search_mode), ("turn", "tool"))

    def test_text_deltas_stream_captions_without_duplicates(self):
        live = adapter()
        events = [{"type": "response.output_text.delta", "item_id": "a1", "delta": "Че"},
                  {"type": "response.output_text.delta", "item_id": "a1", "delta": "тыре."},
                  {"type": "response.output_audio_transcript.done", "item_id": "a1", "transcript": "Четыре."}]
        out = [item for event in events for item in live.translate(event)]
        self.assertEqual([item.get("delta") or item.get("text") for item in out], ["Че", "тыре.", "Четыре."])
        self.assertTrue(out[-1]["final"])

        both = adapter(url="wss://realtime.example.com/v1/realtime", model="rt")
        events = [{"type": "response.output_audio_transcript.delta", "item_id": "b1", "delta": "Hi"},
                  {"type": "response.output_text.delta", "item_id": "b1", "delta": "Hi"},
                  {"type": "response.output_audio_transcript.delta", "item_id": "b1", "delta": " there"}]
        out = [item for event in events for item in both.translate(event)]
        self.assertEqual([item["delta"] for item in out], ["Hi", " there"])
        self.assertEqual(both.transcript_text(), "Assistant: Hi there")

class LiveBrowserCodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node runtime unavailable")
    def test_worklet_and_turns(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "round26_voice_live_runner.cjs")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_yandex_model_badge_is_short(self):
        import re
        script = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const model = shortModelId(info.model);", script)
        pattern = re.search(r'function shortModelId\(id\) \{ return String\(id \|\| ""\)\.replace\(/(.+?)/i, "\$1"\); \}', script).group(1)
        pattern = "(?i)" + pattern.replace("\\/", "/")
        self.assertEqual(re.sub(pattern, r"\1", YANDEX_MODEL), "speech-realtime-250923")
        self.assertEqual(re.sub(pattern, r"\1", "gpt-realtime"), "gpt-realtime")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".live-call-head b { white-space:nowrap; flex:none; }", css)

    def test_classic_live_clip_keeps_its_length(self):

        script = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("finishVoiceRecording(blob, voiceRecordingKind, owner, (Date.now() - recordingStarted) / 1000)", script)
        self.assertIn('body: { audio, duration: seconds, profile_id: ownerProfile }', script)

    def test_worklet_cache_is_busted(self):
        source = (ROOT / "app" / "pgstatic" / "realtime.js").read_text(encoding="utf-8")
        self.assertIn("/realtime-worklet.js?v=1.1", source)

class VoiceRuntimeTests(unittest.TestCase):
    def test_spoken_inserts_follow_the_answer_language(self):
        english = prepare_tts_text("Run this:\n```py\nprint(1)\n```\n![chart](a.png) $$x^2$$")
        self.assertNotRegex(english, "[а-яё]")
        self.assertEqual(VoiceRuntime.detect_language(english), "en")
        self.assertIn("Code snippet omitted", english)
        russian = prepare_tts_text("Запустите:\n```py\nprint(1)\n```")
        self.assertIn("Фрагмент кода опущен", russian)

    def _download(self, runtime, opener):
        state = {"progress": 0}
        with patch("app.voice_runtime.urllib.request.urlopen", opener):
            runtime._download_tts_model("ru_RU-irina-medium", state, threading.Event())

    def test_cut_piper_download_is_not_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = VoiceRuntime(temp)

            class Cut(io.BytesIO):
                headers = {"Content-Length": "1000"}

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            with self.assertRaisesRegex(RuntimeError, "не полностью"):
                self._download(runtime, lambda request, timeout=60: Cut(b"x" * 400))
            self.assertFalse(runtime._is_downloaded("ru_RU-irina-medium", "tts"))
            self.assertEqual([p for p in Path(runtime.tts_root).iterdir()], [])

    def test_disk_full_is_reported_plainly(self):
        import asyncio
        with tempfile.TemporaryDirectory() as temp:
            runtime = VoiceRuntime(temp)

            def full(*_args, **_kwargs):
                raise OSError(errno.ENOSPC, "No space left on device")

            async def scenario():
                with patch.object(runtime, "_download_tts_model", full):
                    await runtime.start_download("ru_RU-irina-medium", "tts")
                    await runtime.downloads["tts:ru_RU-irina-medium"]["task"]
            asyncio.run(scenario())
            state = runtime.downloads["tts:ru_RU-irina-medium"]
            self.assertEqual(state["status"], "error")
            self.assertIn("Недостаточно места", state["error"])

BODY = gguf_bytes({"general.architecture": "llama", "general.name": "Tiny"}) + bytes(range(256)) * 12000
SHA = hashlib.sha256(BODY).hexdigest()

class FakeHub:
    """Hugging Face resolve/main: HEAD with x-linked-etag on the redirect, Range GETs."""

    def __init__(self, cut=None, corrupt=False):
        self.cut, self.corrupt, self.calls = cut, corrupt, []

    def __call__(self, request):
        self.calls.append((request.method, request.headers.get("range")))
        if request.method == "HEAD" and "cdn" not in request.url.host:
            return httpx.Response(302, headers={"location": "https://cdn.example/file", "x-linked-etag": f'"{SHA}"',
                                                "x-linked-size": str(len(BODY))})
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(BODY))})
        start = int(request.headers["range"].split("=")[1].split("-")[0]) if request.headers.get("range") else 0
        if start >= len(BODY):
            return httpx.Response(416, headers={"content-range": f"bytes */{len(BODY)}"})
        data = BODY[start:]
        if self.corrupt:
            data = data[:-16] + b"\xff" * 16
        cut, self.cut = self.cut, None

        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for offset in range(0, len(data), 65536):
                    if cut is not None and offset >= cut:
                        raise httpx.ReadError("connection reset")
                    yield data[offset:offset + 65536]
        headers = {"content-length": str(len(data))}
        if start:
            headers["content-range"] = f"bytes {start}-{len(BODY) - 1}/{len(BODY)}"
        return httpx.Response(206 if start else 200, headers=headers, stream=Stream())

class LocalModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(lm, "DATA_DIR", self.temp.name), patch.object(lm, "sync_catalog", lambda: None)]
        for item in self.patches:
            item.start()
        self.folder = lm._repo_folder("org/repo")

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def run_download(self, hub, **extra):
        client = lambda timeout=30.0: httpx.Client(transport=httpx.MockTransport(hub), follow_redirects=True)
        with patch.object(lm, "_client", client):
            job = lm.start_download("org/repo", ["tiny-Q4_K_M.gguf"], **extra)
            deadline = time.time() + 20
            while time.time() < deadline:
                row = next(item for item in lm.downloads() if item["id"] == job["id"])
                if row["state"] not in ("queued", "downloading"):
                    return row
                time.sleep(0.02)
        self.fail("download did not finish")

    def files(self):
        return sorted(path.name for path in self.folder.iterdir()) if self.folder.exists() else []

    def test_interrupted_download_resumes_with_range(self):
        failed = self.run_download(FakeHub(cut=1_200_000))
        self.assertEqual(failed["state"], "error")
        self.assertEqual(self.files(), ["tiny-Q4_K_M.gguf.part"])
        hub = FakeHub()
        done = self.run_download(hub)
        self.assertEqual(done["state"], "done", done["error"])
        self.assertTrue(any(method == "GET" and value for method, value in hub.calls), hub.calls)
        self.assertEqual(hashlib.sha256((self.folder / "tiny-Q4_K_M.gguf").read_bytes()).hexdigest(), SHA)

    def test_checksum_mismatch_discards_the_file(self):
        row = self.run_download(FakeHub(corrupt=True))
        self.assertEqual(row["state"], "error")
        self.assertIn("SHA-256", row["error"])
        self.assertEqual(self.files(), [])
        self.assertEqual(lm.list_models(), [])
        self.assertEqual(self.run_download(FakeHub())["state"], "done")

    def test_oversized_part_does_not_block_retries_forever(self):
        self.folder.mkdir(parents=True)
        (self.folder / "tiny-Q4_K_M.gguf.part").write_bytes(b"x" * (len(BODY) + 10))
        row = self.run_download(FakeHub())
        self.assertEqual(row["state"], "error")
        self.assertEqual(self.files(), [])
        self.assertEqual(self.run_download(FakeHub())["state"], "done")

    def test_not_enough_space_is_refused_before_downloading(self):
        usage = collections.namedtuple("usage", "total used free")
        hub = FakeHub()
        with patch.object(lm.shutil, "disk_usage", lambda _path: usage(10, 10, 1000)):
            row = self.run_download(hub)
        self.assertEqual(row["state"], "error")
        self.assertIn("Недостаточно места", row["error"])
        self.assertIn("МБ", row["error"])
        self.assertFalse(any(method == "GET" for method, _ in hub.calls))

    def test_disk_full_during_download(self):
        def full(*_args, **_kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")
        with patch.object(lm, "_fetch", full):
            row = self.run_download(FakeHub())
        self.assertIn("Недостаточно места", row["error"])

    def test_retry_has_the_whole_file_set(self):
        row = self.run_download(FakeHub(corrupt=True), projector="mmproj-F16.gguf")
        self.assertEqual(row["projector"], "mmproj-F16.gguf")
        script = (ROOT / "app" / "static" / "local.js").read_text(encoding="utf-8")
        self.assertIn('data-lm="dl-retry"', script)
        self.assertIn('case "dl-retry"', script)

    def test_quoted_extra_arguments_reach_llama_server_unquoted(self):
        pool = lm.Pool()
        pool.command_override = ["llama-server"]
        row = {"path": "C:\\m.gguf", "extra_args": '--chat-template-file "C:\\My Models\\t.jinja" -fa'}
        args = pool.command(row, 9000, "local-m")
        index = args.index("--chat-template-file")
        self.assertEqual(args[index + 1], "C:\\My Models\\t.jinja")
        self.assertEqual(args[-1], "-fa")

if __name__ == "__main__":
    unittest.main()
