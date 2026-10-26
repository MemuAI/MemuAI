"""Regressions reproduced by the independent Round 46 Voice review."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import stt_providers, voice_downloads
from app.voice_runtime import VoiceRuntime

class VoiceReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_change_waits_for_download_and_cancelled_worker_to_finish(self):
        started, resume = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            runtime = VoiceRuntime(folder)
            root = runtime.root
            replacement = Path(folder) / "replacement"

            def transfer(_files, staging, state, _cancel):
                state["stage"] = "installing"
                started.set()
                resume.wait(3)
                (staging / "config.json").write_bytes(b"{}")
                (staging / "model.bin").write_bytes(b"model")

            try:
                with patch.object(voice_downloads, "manifest", return_value=[]), \
                        patch.object(voice_downloads, "transfer", side_effect=transfer):
                    await runtime.start_download("tiny")
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))

                    self.assertEqual(runtime.set_root(root), root)
                    with self.assertRaisesRegex(RuntimeError, "папку"):
                        runtime.set_root(replacement)
                    self.assertFalse(replacement.exists())
                    runtime.cancel_download("tiny")
                    self.assertEqual(runtime.downloads["stt:tiny"]["status"], "cancelled")
                    with self.assertRaisesRegex(RuntimeError, "папку"):
                        runtime.set_root(replacement)
                    resume.set()
                    await runtime.downloads["stt:tiny"]["task"]
                self.assertEqual(runtime.set_root(replacement), replacement.resolve())
                self.assertFalse(runtime._is_downloaded("tiny"))
                self.assertFalse((root / "stt" / "tiny").exists())
            finally:
                resume.set()

    async def test_invalid_gemini_upload_name_cannot_be_used_as_cleanup_path(self):
        for name in ("files/../../unrelated-resource", "../secret", "files/x?key=value", "files/x#fragment"):
            calls = []

            def handler(request):
                calls.append((request.method, request.url.path))
                if request.url.path == "/upload/v1beta/files":
                    return httpx.Response(200, headers={"x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/session"})
                if request.url.path == "/upload/session":
                    return httpx.Response(200, json={"file": {"name": name, "uri": "https://generativelanguage.googleapis.com/v1beta/files/x"}})
                raise AssertionError("Malformed upload must not authorize another request")

            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "audio file"):
                await stt_providers.transcribe(b"x" * (13 * 1024 * 1024), "audio/wav",
                    base_url=stt_providers.PROVIDERS["gemini"]["base_url"], api_key="dummy-review-key",
                    model="gemini-3.8-flash", transport=httpx.MockTransport(handler))
            self.assertEqual(calls, [("POST", "/upload/v1beta/files"), ("POST", "/upload/session")])

if __name__ == "__main__":
    unittest.main()
