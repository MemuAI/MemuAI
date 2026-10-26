"""Real streaming local downloads and native STT HTTP contracts, all offline."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

from app import pgdata, stt_providers as sp, voice_downloads as vd
from app.voice_runtime import VoiceRuntime

class NativeSpeech(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, kind, handler, **extra):
        row = sp.PROVIDERS[kind]
        return await sp.transcribe(b"audio-data", "audio/ogg; codecs=opus", base_url=row["base_url"],
            api_key="secret-key", model=(row["models"] or [""])[0], language="ru",
            transport=httpx.MockTransport(handler), **extra)

    async def test_openai_new_language_parameter_and_legacy_compatibility(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"text": "Привет"})
        self.assertEqual(await self.invoke("openai", handler), "Привет")
        self.assertEqual(seen[0].url.path, "/v1/audio/transcriptions")
        self.assertIn(b'name="languages[]"', seen[0].content)
        self.assertNotIn(b'name="language"', seen[0].content)
        self.assertIn(b'filename="recording.ogg"', seen[0].content)
        await sp.transcribe(b"audio", "audio/webm", base_url=sp.PROVIDERS["openai"]["base_url"],
            api_key="secret-key", model="gpt-4o-mini-transcribe", language="en", transport=httpx.MockTransport(handler))
        self.assertIn(b'name="language"', seen[-1].content)
        self.assertNotIn(b'name="languages[]"', seen[-1].content)

    async def test_groq_and_mistral_actual_multipart_endpoints(self):
        for kind in ("groq", "mistral"):
            with self.subTest(kind=kind):
                def handler(request):
                    self.assertTrue(request.url.path.endswith("/audio/transcriptions"))
                    self.assertEqual(request.headers["authorization"], "Bearer secret-key")
                    self.assertIn(sp.PROVIDERS[kind]["models"][0].encode(), request.content)
                    return httpx.Response(200, json={"text": "Привет"})
                self.assertEqual(await self.invoke(kind, handler), "Привет")

    async def test_gemini_native_audio_prompt_and_no_thinking_leak(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v1beta/models/gemini-3.8-flash:generateContent")
            self.assertEqual(request.headers["x-goog-api-key"], "secret-key")
            data = json.loads(request.content)
            parts = data["contents"][0]["parts"]
            self.assertIn("Do not translate", parts[0]["text"])
            self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), b"audio-data")
            self.assertEqual(parts[1]["inlineData"]["mimeType"], "audio/ogg")
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                {"thought": True, "text": "private"}, {"text": "Привет"}]}}]})
        self.assertEqual(await self.invoke("gemini", handler), "Привет")

    async def test_gemini_large_audio_upload_deleted_on_success_and_error(self):
        for fail in (False, True):
            calls = []
            def handler(request):
                calls.append((request.method, request.url.path))
                if request.url.path == "/upload/v1beta/files":
                    self.assertEqual(request.headers["x-goog-upload-header-content-length"], str(13 * 1024 * 1024))
                    return httpx.Response(200, headers={"x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/session"})
                if request.url.path == "/upload/session":
                    return httpx.Response(200, json={"file": {"name": "files/audio1", "uri": "https://generativelanguage.googleapis.com/v1beta/files/audio1"}})
                if request.method == "DELETE": return httpx.Response(200, json={})
                if fail: return httpx.Response(429, json={"error": {"message": "quota exceeded secret-key"}})
                self.assertIn("fileData", json.loads(request.content)["contents"][0]["parts"][1])
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Transcript"}]}}]})
            task = sp.transcribe(b"x" * (13 * 1024 * 1024), "audio/wav", base_url=sp.PROVIDERS["gemini"]["base_url"],
                api_key="secret-key", model="gemini-3.8-flash", transport=httpx.MockTransport(handler))
            if fail:
                with self.assertRaises(RuntimeError) as error: await task
                self.assertNotIn("secret-key", str(error.exception))
            else: self.assertEqual(await task, "Transcript")
            self.assertEqual(calls[-1], ("DELETE", "/v1beta/files/audio1"))

    async def test_gemini_upload_cannot_send_key_to_other_origin(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, headers={"x-goog-upload-url": "https://other.example/upload"})
        with self.assertRaisesRegex(RuntimeError, "upload URL"):
            await sp.transcribe(b"x" * (13 * 1024 * 1024), "audio/wav", base_url=sp.PROVIDERS["gemini"]["base_url"],
                api_key="secret-key", model="gemini-3.8-flash", transport=httpx.MockTransport(handler))
        self.assertEqual(len(calls), 1)

    async def test_elevenlabs_native_scribe_uses_own_key_and_language(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v1/speech-to-text")
            self.assertEqual(request.headers["xi-api-key"], "secret-key")
            self.assertNotIn("authorization", request.headers)
            self.assertIn(b'name="model_id"', request.content)
            self.assertIn(b"scribe_v2", request.content)
            self.assertIn(b'name="language_code"', request.content)
            return httpx.Response(200, json={"text": "Привет"})
        self.assertEqual(await self.invoke("elevenlabs", handler), "Привет")

    async def test_deepgram_binary_and_automatic_language(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Token secret-key")
            self.assertEqual(request.content, b"audio")
            self.assertEqual(request.url.params["detect_language"], "true")
            self.assertNotIn("language", request.url.params)
            return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": "Hello"}]}]}})
        text = await sp.transcribe(b"audio", "audio/webm", base_url=sp.PROVIDERS["deepgram"]["base_url"],
            api_key="secret-key", model="nova-3", language="auto", transport=httpx.MockTransport(handler))
        self.assertEqual(text, "Hello")

    async def test_azure_fast_transcription_multipart_and_locale(self):
        def handler(request):
            self.assertEqual(request.url.path, "/speechtotext/transcriptions:transcribe")
            self.assertEqual(request.url.params["api-version"], "2025-10-15")
            self.assertEqual(request.headers["Ocp-Apim-Subscription-Key"], "secret-key")
            self.assertIn(b'name="definition"', request.content)
            self.assertIn(b'"ru-RU"', request.content)
            return httpx.Response(200, json={"combinedPhrases": [{"text": "Привет"}, {"text": "Мир"}]})
        self.assertEqual(await self.invoke("azure", handler), "Привет\nМир")

    async def test_custom_local_api_needs_no_key_and_runtime_delegates(self):
        def handler(request):
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(request.url.path, "/v1/audio/transcriptions")
            return httpx.Response(200, json={"text": "Local"})
        runtime = VoiceRuntime.__new__(VoiceRuntime)
        self.assertEqual(await runtime.transcribe_api(b"audio", "audio/webm", base_url="http://127.0.0.1:7777/v1",
            api_key="", model="local", transport=httpx.MockTransport(handler)), "Local")

    async def test_empty_malformed_and_html_error_are_readable_without_secret(self):
        for payload in ({}, [], {"text": ""}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                await self.invoke("elevenlabs", lambda request: httpx.Response(200, json=payload))
        with self.assertRaises(RuntimeError) as error:
            await self.invoke("elevenlabs", lambda request: httpx.Response(401, content=b"<html>secret-key wrong key</html>"))
        self.assertNotIn("<", str(error.exception)); self.assertNotIn("secret-key", str(error.exception))
        with self.assertRaisesRegex(ValueError, "API"):
            await sp.transcribe(b"audio", "audio/wav", base_url=sp.PROVIDERS["elevenlabs"]["base_url"],api_key="",model="scribe_v2")

class DownloadContracts(unittest.IsolatedAsyncioTestCase):
    async def test_real_transfer_reports_bytes_percent_and_cancel_closes_connection(self):
        total = 2 * 1024 * 1024
        disconnected = threading.Event()
        class Server(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                self.send_response(200);self.send_header("Content-Length",str(total));self.end_headers()
                try:
                    for _ in range(total // 16384):
                        self.wfile.write(b"x" * 16384);self.wfile.flush();time.sleep(.01)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): disconnected.set()
        server = ThreadingHTTPServer(("127.0.0.1",0),Server)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                runtime = VoiceRuntime(folder)
                files=[{"name":"model.bin","size":total,"url":f"http://127.0.0.1:{server.server_port}/model"},
                       {"name":"config.json","size":2,"url":f"http://127.0.0.1:{server.server_port}/config"}]
                with patch.object(vd,"manifest",return_value=files):
                    await runtime.start_download("tiny")
                    state=runtime.downloads["stt:tiny"]
                    for _ in range(100):
                        if state.get("bytes_downloaded",0)>65536: break
                        await asyncio.sleep(.01)
                    self.assertGreater(state["bytes_downloaded"],65536)
                    self.assertGreater(state["progress"],0);self.assertLess(state["progress"],99)
                    first_progress=state["progress"]
                    for _ in range(100):
                        if state.get("bytes_downloaded",0)>total//4: break
                        await asyncio.sleep(.01)
                    self.assertGreater(state["progress"],first_progress,"Progress advances during the file, never sticks at 5%")
                    self.assertGreaterEqual(state["progress"],25)
                    self.assertEqual(state["total_bytes"],total+2)

                    json.dumps(await runtime.start_download("tiny"))
                    self.assertTrue(runtime.cancel_download("tiny"))
                    await asyncio.wait_for(state["task"],timeout=3)
                self.assertEqual(state["status"],"cancelled")
                self.assertFalse(runtime._is_downloaded("tiny"))
                self.assertEqual(list(runtime.root.glob(".*-download-*")),[])
                self.assertTrue(await asyncio.to_thread(disconnected.wait,2),"Cancellation closes the HTTP connection")
        finally:
            server.shutdown();server.server_close()

    async def test_atomic_complete_checksum_and_incomplete_cleanup(self):
        for corrupt in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                runtime=VoiceRuntime(folder);state={}
                def manifest(_id,_state,_cancel):
                    return [{"name":name,"url":"https://example.test/"+name,"size":len(raw),
                             "sha256":"0"*64 if corrupt else hashlib.sha256(raw).hexdigest()}
                            for name,raw in (("model.bin",b"model"),("config.json",b"{}"))]
                class Response(io.BytesIO):
                    headers={}
                    def __enter__(self): return self
                    def __exit__(self,*_): self.close()
                def opener(url,_cancel,_state,**_): return Response(b"{}" if url.endswith("config.json") else b"model")
                with patch.object(vd,"manifest",side_effect=manifest),patch.object(vd,"_open",side_effect=opener):
                    if corrupt:
                        with self.assertRaisesRegex(RuntimeError,"повреждён"): runtime._download_local_model("tiny",state)
                    else:
                        runtime._download_local_model("tiny",state)
                        self.assertTrue(runtime._is_downloaded("tiny"));self.assertIsNone(runtime._model)
                        self.assertEqual(state["bytes_downloaded"],7)
                self.assertEqual(list(runtime.root.glob(".*-download-*")),[])

    async def test_manifest_pins_revision_and_accepts_only_model_assets(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*_): self.close()
        payload={"sha":"a"*40,"siblings":[{"rfilename":"model.bin","size":4},{"rfilename":"config.json","size":2},
            {"rfilename":"../../escape.py","size":3},{"rfilename":"README.md","size":9}]}
        with patch.object(vd,"_open",return_value=Response(json.dumps(payload).encode())):
            result=vd.manifest("distil-small.en",{},threading.Event())
        self.assertEqual({x["name"] for x in result},{"model.bin","config.json"})
        self.assertTrue(all("/resolve/"+"a"*40+"/" in x["url"] for x in result))
        self.assertIn("faster-distil-whisper-small.en",result[0]["url"])

    async def test_unknown_total_reports_bytes_without_false_percentage(self):
        class Response(io.BytesIO):
            headers={}
            def __enter__(self): return self
            def __exit__(self,*_): self.close()
        with tempfile.TemporaryDirectory() as folder,patch.object(vd,"_open",side_effect=lambda *_,**__:Response(b"hello")):
            state={};vd.transfer([{"name":"audio.onnx","url":"https://example.test/audio"}],Path(folder),state,threading.Event())
            self.assertIsNone(state["progress"]);self.assertEqual(state["bytes_downloaded"],5);self.assertEqual(state["total_bytes"],0)

    async def test_shutdown_cancellation_interrupts_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=VoiceRuntime(folder);stopped=threading.Event()
            def transfer(_id,state,cancel):
                cancel.wait(3);stopped.set()
            with patch.object(runtime,"_download_local_model",side_effect=transfer):
                await runtime.start_download("tiny");await asyncio.sleep(.02)
                runtime.downloads["stt:tiny"]["task"].cancel()
                await runtime.downloads["stt:tiny"]["task"]
                self.assertTrue(await asyncio.to_thread(stopped.wait,1))
            self.assertEqual(runtime.downloads["stt:tiny"]["status"],"cancelled")

class SettingsCompatibility(unittest.TestCase):
    def test_english_distil_models_are_not_advertised_as_multilingual(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=VoiceRuntime(folder)
            rows={row["id"]:row for row in runtime.catalog()}
            for model in ("distil-large-v2","distil-large-v3"):
                self.assertEqual(rows[model]["language_codes"],["en"])
                with self.assertRaisesRegex(ValueError,"не поддерживает язык ru"):
                    runtime.transcribe_local(b"audio","audio/wav",model,"ru-RU")

    def test_profiles_have_bounded_fields_and_existing_selected_model_is_preserved(self):
        prefs=pgdata._normalize_preferences({"voice_stt_provider":"api","voice_stt_api_url":"https://api.elevenlabs.io",
            "voice_stt_api_model":"scribe_v1","voice_stt_api_key":"old-key",
            "voice_stt_api_profiles":{"elevenlabs":{"url":"https://api.elevenlabs.io","key":"old-key","model":"scribe_v1"},"unknown":{"key":"x"}}})
        self.assertEqual(prefs["voice_stt_api_model"],"scribe_v1")
        self.assertEqual(prefs["voice_stt_api_key"],"old-key")
        self.assertEqual(set(prefs["voice_stt_api_profiles"]),{"elevenlabs"})

if __name__ == "__main__": unittest.main()
