"""Offline music proof: real Proxy accounts, routes, adapters and cancellation."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-music-tests-")

import asyncio
import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app import admin_api, module_media, module_services_api, modules_host, music_runtime, openai_api
from app import companion
from app.config import config_store
from app.providers import get
from app.providers import _providers
from app.providers.custom import CustomProvider
from app.state import Account, ModelEntry, state

WAV = b"RIFF\x00\x00\x00\x00WAVEfixture"

class MusicProxyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(modules_host, "default_root", return_value=self.root / "modules"),
                        patch.dict(_providers, {"custom": CustomProvider()}, clear=True),
                        patch.object(state, "accounts", {}), patch.object(state, "models", {}),
                        patch.object(state, "save_accounts"), patch.object(state, "save_models"),
                        patch.object(admin_api, "_auth", return_value=None),
                        patch.object(openai_api, "_check_auth", return_value=None)]
        for item in self.patches:
            item.start()
        modules_host.configure_module(self.root / "modules", "studio", enabled=True)
        self.app = FastAPI()
        self.app.include_router(admin_api.router)
        self.app.include_router(openai_api.router)
        self.app.include_router(module_services_api.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        for task in music_runtime._JOBS.values():
            task.cancel()
        music_runtime._JOBS.clear()
        for task in module_services_api._MUSIC_JOBS.values():
            task.cancel()
        module_services_api._MUSIC_JOBS.clear()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def account(self, config=None, **fields):
        values = {"name": "musicfixture", "base_url": "https://music.example.com/v1", "api_key": "fixture-secret",
                  "music_models": "song-v1", "music_config": json.dumps(config or {"endpoint": "music", "auth": "bearer"})}
        values.update(fields)
        account = Account("custom", values, label="Fixture music")
        state.accounts[account.id] = account
        return account

    async def test_account_to_catalog_to_studio_generation_uses_proxy_key(self):
        config = {"endpoint": "music", "auth": "bearer", "template": {"model": "{{model}}", "text": "{{prompt}}",
                  "seconds": "{{duration}}", "instrumental": "{{instrumental}}", "style": "{{style}}", "lyrics": "{{lyrics}}"}}
        with patch.object(module_media, "public_request", AsyncMock(return_value=(WAV, "audio/wav", "https://music.example.com/v1/music"))) as upstream:
            added = await self.client.post("/api/admin/accounts", json={"provider": "custom", "fields": {
                "name": "musicfixture", "base_url": "https://music.example.com/v1", "api_key": "fixture-secret",
                "music_models": "song-v1", "music_config": json.dumps(config)}})
            self.assertEqual(added.status_code, 200, added.text)
            self.assertNotIn("fixture-secret", added.text)
            pg = (await self.client.get("/api/admin/pg/state")).json()
            row = next(item for item in pg["models"] if item["id"] == "musicfixture-song-v1")
            self.assertTrue(row["caps"]["music"])
            self.assertFalse(row["caps"]["text"])
            self.assertIn(row["id"], pg["music_models"])
            catalog = (await self.client.post("/api/pg/modules/music", json={"module_id": "studio", "action": "models"})).json()
            self.assertEqual(catalog["models"][0]["id"], row["id"])
            self.assertNotIn("fixture-secret", json.dumps(catalog))
            response = await self.client.post("/api/pg/modules/music", json={"module_id": "studio", "model": row["id"],
                "request_id": "fixture-music", "prompt": "Calm piano", "duration": 70, "tempo": 92,
                "instrumental": False, "lyrics": "A fixture lyric", "style": "jazz"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(base64.b64decode(response.json()["audio"].split(",")[1]), WAV)
            request = upstream.await_args
            self.assertEqual(request.args[0], "https://music.example.com/v1/music")
            self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer fixture-secret")
            self.assertEqual(request.kwargs["payload"], {"model": "song-v1", "text": "Calm piano", "seconds": 70,
                                                      "instrumental": False, "style": "jazz", "lyrics": "A fixture lyric"})
            self.assertNotIn("fixture-secret", response.text)
            public = (await self.client.get("/v1/models")).json()["data"]
            self.assertTrue(next(item for item in public if item["id"] == row["id"])["capabilities"]["music"])

    async def test_named_elevenlabs_uses_header_and_typed_milliseconds(self):
        self.account({"preset": "elevenlabs", "endpoint": "music", "auth": "header", "auth_header": "xi-api-key"},
                     music_models="music_v2_5", headers='{"X-Custom":"fixture"}')
        with patch.object(module_media, "public_request", AsyncMock(return_value=(WAV, "audio/wav", "https://music.example.com/v1/music"))) as upstream:
            result = await music_runtime.generate({"model": "musicfixture-music_v2_5", "prompt": "Piano", "style": "warm", "duration": 42,
                                                   "tempo": 80, "instrumental": False, "lyrics": "Hello"})
        sent = upstream.await_args.kwargs
        self.assertEqual(sent["headers"]["xi-api-key"], "fixture-secret")
        self.assertNotIn("Authorization", sent["headers"])
        self.assertEqual(sent["headers"]["X-Custom"], "fixture")
        self.assertEqual(sent["payload"]["music_length_ms"], 42000)
        self.assertIs(sent["payload"]["force_instrumental"], False)
        self.assertIn("Lyrics:\nHello", sent["payload"]["prompt"])
        self.assertEqual(result["model"], "musicfixture-music_v2_5")

    async def test_minimax_url_download_never_sends_key_to_cdn(self):
        self.account({"preset": "minimax", "endpoint": "music_generation", "auth": "bearer"}, music_models="music-3.0")
        replies = [(b'{"data":{"audio":"https://cdn.example.com/song.wav"},"base_resp":{"status_code":0}}',
                    "application/json", "https://music.example.com/v1/music_generation"), (WAV, "audio/wav", "https://cdn.example.com/song.wav")]
        with patch.object(module_media, "public_request", AsyncMock(side_effect=replies)) as upstream:
            await music_runtime.generate({"model": "musicfixture-music-3.0", "prompt": "Music", "instrumental": True})
        self.assertIs(upstream.await_args_list[0].kwargs["payload"]["is_instrumental"], True)
        self.assertEqual(upstream.await_args_list[0].kwargs["payload"]["output_format"], "url")
        self.assertIsNone(upstream.await_args_list[1].kwargs["headers"])

    async def test_base64_hex_and_protected_same_origin_audio(self):
        for key, encoded in [("base64_path", base64.b64encode(WAV).decode()), ("hex_path", WAV.hex())]:
            state.accounts.clear()
            self.account({"endpoint": "music", key: "output", "audio_path": "", "base64_path": "output" if key == "base64_path" else ""})
            with patch.object(module_media, "public_request", AsyncMock(return_value=(json.dumps({"output": encoded}).encode(), "application/json", "https://music.example.com/v1/music"))):
                result = await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
                self.assertEqual(base64.b64decode(result["audio"].split(",")[1]), WAV)
        state.accounts.clear()
        self.account()
        with patch.object(module_media, "public_request", AsyncMock(side_effect=[
            (b'{"data":[{"url":"/private/song.wav"}]}', "application/json", "https://music.example.com/v1/music"),
            (WAV, "audio/wav", "https://music.example.com/private/song.wav")])) as upstream:
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(upstream.await_args_list[1].kwargs["headers"]["Authorization"], "Bearer fixture-secret")

    async def test_async_job_relative_polling_and_typed_json(self):
        self.account({"endpoint": "music", "poll_url": "/jobs/{id}", "audio_path": "output.url"})
        replies = [(b'{"id":"job 1","status":"queued"}', "application/json", "https://music.example.com/v1/music"),
                   (b'{"status":"done","output":{"url":"/song.wav"}}', "application/json", "https://music.example.com/jobs/job%201"),
                   (WAV, "audio/wav", "https://music.example.com/song.wav")]
        with patch.object(module_media, "public_request", AsyncMock(side_effect=replies)) as upstream, patch.object(module_media.asyncio, "sleep", AsyncMock()):
            result = await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(upstream.await_args_list[1].args[0], "https://music.example.com/jobs/job%201")
        self.assertEqual(result["mime"], "audio/wav")

    async def test_cancel_cancels_upstream_task_and_cleans_jobs(self):
        self.account()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set(); raise
        with patch.object(module_media, "public_request", slow):
            request = asyncio.create_task(self.client.post("/v1/audio/music", json={"request_id": "cancel-fixture", "model": "musicfixture-song-v1", "prompt": "Fixture"}))
            await asyncio.wait_for(entered.wait(), timeout=3)
            cancel = await self.client.post("/v1/audio/music", json={"action": "cancel", "request_id": "cancel-fixture"})
            self.assertTrue(cancel.json()["cancelled"])
            response = await request
            self.assertEqual(response.status_code, 409)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(music_runtime._JOBS)

    async def test_disabled_models_and_wrong_accounts_are_rejected_before_network(self):
        account = self.account()
        for model in ["other-song-v1", "musicfixture-chat-model"]:
            with self.assertRaises(ValueError):
                await music_runtime.generate({"model": model, "prompt": "Fixture"})
        account.enabled = False
        self.assertFalse(music_runtime.models())
        with self.assertRaises(ValueError):
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        account.enabled = True
        entry = ModelEntry("musicfixture", "custom", "song-v1")
        entry.enabled = False
        state.models[entry.id] = entry
        self.assertFalse(music_runtime.models())
        with self.assertRaises(ValueError):
            await music_runtime.generate({"model": entry.id, "prompt": "Fixture"})

    async def test_text_catalog_companion_fallback_and_chat_route_exclude_declared_music(self):
        self.account()
        music = ModelEntry("musicfixture", "custom", "song-v1")
        text = ModelEntry("musicfixture", "custom", "chat-v1")
        state.models.update({music.id: music, text.id: text})
        rows = (await self.client.get("/api/admin/models")).json()["models"]
        self.assertEqual(next(row for row in rows if row["id"] == music.id)["caps"], {"music": True, "text": False})
        self.assertTrue(music.enabled)
        with patch.object(config_store.settings, "companion_model", music.id):
            self.assertEqual(companion.default_model(), text.id)
            state.models.pop(text.id)
            self.assertEqual(companion.default_model(), "")
        response = await self.client.post("/v1/chat/completions", json={"model": music.id, "messages": [{"role": "user", "content": "Fixture"}]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["type"], "unsupported_modality")
        self.assertIn("/v1/audio/music", response.text)
        self.assertEqual(next(iter(state.accounts.values())).stats["requests"], 0)

    async def test_music_public_model_capabilities_follow_prefix_preference(self):
        self.account()
        for prefixed in (True, False):
            with self.subTest(prefixed=prefixed), patch.object(config_store.settings, "use_prefixes", prefixed):
                models = (await self.client.get("/v1/models")).json()["data"]
                expected = "musicfixture-song-v1" if prefixed else "song-v1"
                self.assertEqual([row["id"] for row in models], [expected])
                row = await self.client.get("/v1/models/" + expected)
                self.assertEqual(row.status_code, 200, row.text)
                self.assertEqual(row.json()["capabilities"], {"music": True, "text": False})

    async def test_errors_and_cross_origin_config_do_not_expose_credentials(self):
        self.account()
        with patch.object(module_media, "public_request", AsyncMock(side_effect=ValueError("API HTTP 401"))):
            response = await self.client.post("/v1/audio/music", json={"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("401", response.text)
        self.assertNotIn("fixture-secret", response.text)
        for config in [{"endpoint": "https://other.example.com/music"}, {"poll_url": "https://other.example.com/{id}"},
                       {"api_key": "not-in-config"}, {"auth_header": "bad\nheader", "auth": "header"}]:
            with self.assertRaises(ValueError):
                music_runtime.parse_config({"base_url": "https://music.example.com/v1", "music_config": json.dumps(config)})

    async def test_key_failover_and_reserve_only_after_explicit_http_rejection(self):
        primary = self.account(api_keys="second-fixture-key", key_pool_mode="failover-on-match", key_error_keywords="HTTP 401")
        reserve = self.account(api_key="reserve-fixture-key")
        reserve.priority = "reserve"
        provider = get("custom")
        with patch.object(provider, "_key_candidates", side_effect=lambda account: ["fixture-secret", "second-fixture-key"] if account is primary else ["reserve-fixture-key"]), patch.object(module_media, "public_request", AsyncMock(side_effect=[
            ValueError("API HTTP 401"), ValueError("API HTTP 429"), (WAV, "audio/wav", "https://music.example.com/v1/music")])) as upstream:
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual([call.kwargs["headers"]["Authorization"] for call in upstream.await_args_list],
                         ["Bearer fixture-secret", "Bearer second-fixture-key", "Bearer reserve-fixture-key"])
        self.assertIn(provider._key_hash("fixture-secret"), primary.extra["disabled_api_key_hashes"])
        self.assertEqual(reserve.stats["success"], 1)

    async def test_accepted_async_job_is_never_resubmitted_on_polling_failure(self):
        self.account({"endpoint": "music", "poll_url": "/jobs/{id}"})
        reserve = self.account({"endpoint": "music", "poll_url": "/jobs/{id}"}, api_key="reserve-fixture-key")
        reserve.priority = "reserve"
        with patch.object(module_media, "public_request", AsyncMock(side_effect=[
            (b'{"id":"accepted","status":"queued"}', "application/json", "https://music.example.com/v1/music"),
            ValueError("API HTTP 503")])) as upstream, patch.object(module_media.asyncio, "sleep", AsyncMock()):
            with self.assertRaisesRegex(ValueError, "503"):
                await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(upstream.await_count, 2)
        self.assertEqual(sum(call.kwargs.get("method") == "POST" for call in upstream.await_args_list), 1)

    async def test_nonmatching_pool_error_moves_to_reserve_without_using_another_key(self):
        primary = self.account(api_keys="second-fixture-key", key_pool_mode="failover-on-match", key_error_keywords="HTTP 401")
        reserve = self.account(api_key="reserve-fixture-key")
        reserve.priority = "reserve"
        provider = get("custom")
        with patch.object(provider, "_key_candidates", side_effect=lambda account: ["fixture-secret", "second-fixture-key"] if account is primary else ["reserve-fixture-key"]), patch.object(module_media, "public_request", AsyncMock(side_effect=[
            ValueError("API HTTP 429"), (WAV, "audio/wav", "https://music.example.com/v1/music")])) as upstream:
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual([call.kwargs["headers"]["Authorization"] for call in upstream.await_args_list],
                         ["Bearer fixture-secret", "Bearer reserve-fixture-key"])
        self.assertNotIn("disabled_api_key_hashes", primary.extra)

    async def test_disabled_key_pool_is_skipped_before_network_and_primary_order_is_kept(self):
        primary = self.account()
        reserve = self.account(api_key="reserve-fixture-key")
        reserve.priority, reserve.order = "reserve", -100
        provider = get("custom")
        primary.extra["disabled_api_key_hashes"] = [provider._key_hash("fixture-secret")]
        with patch.object(module_media, "public_request", AsyncMock(return_value=(WAV, "audio/wav", "https://music.example.com/v1/music"))) as upstream:
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(upstream.await_count, 1)
        self.assertEqual(upstream.await_args.kwargs["headers"]["Authorization"], "Bearer reserve-fixture-key")
        primary.extra.clear()
        with patch.object(module_media, "public_request", AsyncMock(return_value=(WAV, "audio/wav", "https://music.example.com/v1/music"))) as upstream:
            await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(upstream.await_args.kwargs["headers"]["Authorization"], "Bearer fixture-secret")

    async def test_module_disable_and_permission_revocation_drop_in_flight_results(self):
        self.account({"endpoint": "music", "poll_url": "/jobs/{id}", "audio_path": "output.url"})
        root = self.root / "modules"
        permissions = modules_host.load_package(root, "studio")["permissions"]
        replies = [(b'{"id":"accepted","status":"queued"}', "application/json", "https://music.example.com/v1/music"),
                   (b'{"status":"done","output":{"url":"/song.wav"}}', "application/json", "https://music.example.com/jobs/accepted"),
                   (WAV, "audio/wav", "https://music.example.com/song.wav")]
        for revoke in (False, True):
            for boundary in (1, 2, 3):
                with self.subTest(revoke=revoke, boundary=boundary):
                    modules_host.update_code(root, "studio", {"permissions": permissions})
                    modules_host.configure_module(root, "studio", enabled=True)
                    called = 0
                    async def upstream(*args, **kwargs):
                        nonlocal called
                        called += 1
                        if called == boundary:
                            if revoke:
                                modules_host.update_code(root, "studio", {"permissions": []})
                            else:
                                modules_host.configure_module(root, "studio", enabled=False)
                        return replies[called - 1]
                    with patch.object(module_media, "public_request", upstream), patch.object(module_media.asyncio, "sleep", AsyncMock()):
                        response = await self.client.post("/api/pg/modules/music", json={"module_id": "studio", "model": "musicfixture-song-v1", "request_id": "module-rights", "prompt": "Fixture"})
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertNotIn("audio", response.json())
                    self.assertEqual(called, boundary)
                    self.assertFalse(module_services_api._MUSIC_JOBS)

    async def test_revocation_during_http_rejection_prevents_failover(self):
        self.account()
        reserve = self.account(api_key="reserve-fixture-key")
        reserve.priority = "reserve"
        calls = 0
        async def reject(*args, **kwargs):
            nonlocal calls
            calls += 1
            modules_host.configure_module(self.root / "modules", "studio", enabled=False)
            raise ValueError("API HTTP 401")
        with patch.object(module_media, "public_request", reject):
            response = await self.client.post("/api/pg/modules/music", json={"module_id": "studio", "model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(calls, 1)

    async def test_module_disabled_before_generation_never_calls_upstream(self):
        self.account()
        modules_host.configure_module(self.root / "modules", "studio", enabled=False)
        with patch.object(module_media, "public_request", AsyncMock()) as upstream:
            response = await self.client.post("/api/pg/modules/music", json={"module_id": "studio", "model": "musicfixture-song-v1", "prompt": "Fixture"})
        self.assertEqual(response.status_code, 403, response.text)
        upstream.assert_not_awaited()

    async def test_disable_account_while_result_is_in_flight_drops_the_audio(self):
        account = self.account()
        async def result(*args, **kwargs):
            account.enabled = False
            return WAV, "audio/wav", "https://music.example.com/v1/music"
        with patch.object(module_media, "public_request", result):
            with self.assertRaisesRegex(ValueError, "disabled"):
                await music_runtime.generate({"model": "musicfixture-song-v1", "prompt": "Fixture"})

    async def test_legacy_import_is_explicit_server_side_idempotent_and_preserves_file(self):
        legacy = {"endpoint": "https://music.example.com/v1/music", "api_key": "legacy-fixture-key", "model": "old-music"}
        module_media.music_config("studio", legacy)
        target = self.root / "modules" / "media-connections.json"
        before = target.read_bytes()
        presets = await self.client.get("/api/admin/music/presets")
        self.assertTrue(presets.json()["legacy_available"])
        self.assertNotIn("legacy-fixture-key", presets.text)
        self.assertFalse(state.accounts)
        imported = await self.client.post("/api/admin/music/import-studio", json={})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertNotIn("legacy-fixture-key", imported.text)
        self.assertEqual(len(state.accounts), 1)
        self.assertEqual(next(iter(state.accounts.values())).fields["api_key"], "legacy-fixture-key")
        self.assertTrue((await self.client.post("/api/admin/music/import-studio", json={})).json()["already_imported"])
        self.assertEqual(target.read_bytes(), before)

    def test_account_schema_exposes_music_controls_and_rejects_missing_adapter(self):
        provider = get("custom")
        self.assertTrue({"music_models", "music_config"}.issubset({field.key for field in provider.account_fields()}))
        self.assertIsNotNone(provider.validate_fields({"name": "fixture", "base_url": "https://music.example.com", "music_models": "song"}))

class MusicAccountBrowserTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Edge account-editor acceptance")
    def test_actual_proxy_editor_and_schemas(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("round17_proxy_music_browser.py"))],
                                capture_output=True, text=True, encoding="utf-8", timeout=60,
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)

if __name__ == "__main__":
    unittest.main()
