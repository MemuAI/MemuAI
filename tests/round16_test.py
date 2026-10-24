"""Round 16: durable module monitors, configurable music, and voice ownership.

All remote responses are fixtures and all files belong to temporary folders.
"""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app import module_media as media, module_monitor as monitor, modules_host as host, pgdata
from app.playground_api import create_app


def record(identity, score=1):
    return {"id": identity, "name": identity, "values": {"score": score},
            "family": identity.split("/")[0] if "/" in identity else ""}


def audio(raw=b"RIFF\x00\x00\x00\x00WAVEfixture", mime="audio/wav"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class MonitorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "modules"
        self.patch = patch.object(host, "default_root", return_value=self.root)
        self.patch.start()
        host.configure_module(self.root, "api-models", enabled=True)
        self.spec = {"id": "source", "title": "Fixture", "url": "https://api.example.com/models",
                     "format": "json", "items_path": "data", "value_keys": ["score"]}
        self.notified = AsyncMock()
        self.notification_patch = patch.object(monitor, "notify", self.notified)
        self.notification_patch.start()

    def tearDown(self):
        self.notification_patch.stop()
        self.patch.stop()
        self.temp.cleanup()

    def configure(self, **kwargs):
        return monitor.configure("api-models", {"enabled": True, "sources": [self.spec], **kwargs})

    async def scan(self, records):
        with patch.object(monitor, "read_source", AsyncMock(return_value=records)):
            return await monitor.scan("api-models", force=True)

    async def test_baseline_is_quiet_then_only_new_selected_family_is_reported(self):
        self.spec["families"] = ["google"]
        self.configure()
        await self.scan({key: record(key) for key in ["google/old", "openai/old"]})
        self.notified.assert_not_awaited()
        result = await self.scan({key: record(key) for key in ["google/old", "google/new", "openai/new"]})
        self.assertEqual(result["sources"][0]["count"], 2)
        self.assertEqual(result["sources"][0]["families_available"], ["google", "openai"])
        text = self.notified.await_args.args[2]
        self.assertIn("google/new", text)
        self.assertNotIn("openai/new", text)
        self.notified.reset_mock()
        await self.scan({key: record(key) for key in ["google/old", "google/new", "openai/new"]})
        self.notified.assert_not_awaited()

    async def test_errors_keep_baseline_and_credential_is_never_returned(self):
        self.spec["api_key"] = "SECRET"
        self.configure()
        await self.scan({"a": record("a")})
        with patch.object(monitor, "read_source", AsyncMock(side_effect=ValueError("SECRET failure"))):
            failed = await monitor.scan("api-models", force=True)
        self.assertNotIn("SECRET", json.dumps(failed))
        self.assertEqual(failed["sources"][0]["count"], 1)
        self.assertIn("baseline", monitor._read()["jobs"]["api-models"]["sources"][0])
        await self.scan({"a": record("a"), "b": record("b")})
        self.assertIn("b", self.notified.await_args.args[2])

    async def test_paused_jobs_can_be_checked_manually(self):
        self.configure(enabled=False)
        reader = AsyncMock(return_value={"a": record("a")})
        with patch.object(monitor, "read_source", reader):
            await monitor.scan("api-models")
            reader.assert_not_awaited()
            await monitor.scan("api-models", force=True)
            reader.assert_awaited_once()

    async def test_disabled_modules_cannot_scan(self):
        self.configure()
        host.configure_module(self.root, "api-models", enabled=False)
        with self.assertRaises(PermissionError):
            await monitor.scan("api-models", force=True)

    async def test_changes_include_scores_and_removals(self):
        self.configure(mode="changes", telegram=False)
        await self.scan({"a": record("a", 10), "b": record("b", 20)})
        await self.scan({"a": record("a", 11)})
        self.assertIn("10 → 11", self.notified.await_args.args[2])
        self.assertIn("− b", self.notified.await_args.args[2])
        self.assertFalse(self.notified.await_args.args[3])

    async def test_configuration_retains_key_baseline_but_url_change_does_not(self):
        self.spec["api_key"] = "secret"
        self.configure()
        await self.scan({"a": record("a")})
        del self.spec["api_key"]
        self.configure(interval=900)
        stored = monitor._read()["jobs"]["api-models"]["sources"][0]
        self.assertEqual(stored["api_key"], "secret")
        self.assertIn("baseline", stored)
        self.assertNotIn("baseline", monitor.status("api-models")["sources"][0])
        self.spec["url"] = "https://other.example.com/models"
        self.configure()
        stored = monitor._read()["jobs"]["api-models"]["sources"][0]
        self.assertFalse(stored.get("api_key"))
        self.assertNotIn("baseline", stored)

    async def test_revision_changed_while_scanning_discards_stale_result(self):
        self.configure()
        async def read(_):
            self.configure(enabled=False)
            return {"a": record("a")}
        with patch.object(monitor, "read_source", read):
            result = await monitor.scan("api-models", force=True)
        self.assertFalse(result["enabled"])
        self.assertFalse(result["sources"][0].get("last_success"))
        self.notified.assert_not_awaited()

    async def test_parallel_scan_does_not_duplicate_read_or_notification(self):
        self.configure()
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(_):
            entered.set()
            await release.wait()
            return {"a": record("a")}
        reader = AsyncMock(side_effect=read)
        with patch.object(monitor, "read_source", reader):
            first = asyncio.create_task(monitor.scan("api-models", force=True))
            await entered.wait()
            await monitor.scan("api-models", force=True)
            reader.assert_awaited_once()
            release.set()
            await first

    async def test_pagination_joins_records_and_keeps_credentials_out_of_url(self):
        source = {**self.spec, "api_key": "secret", "auth": "google",
                  "pagination": {"token_path": "nextPageToken", "parameter": "pageToken"}}
        request = AsyncMock(side_effect=[(b'{"data":[{"id":"a","score":1}],"nextPageToken":"abc"}', "application/json", source["url"]),
                                        (b'{"data":[{"id":"b","score":2}]}', "application/json", source["url"])])
        with patch.object(monitor, "public_request", request):
            self.assertEqual(set(await monitor.read_source(source)), {"a", "b"})
        self.assertIn("pageToken=abc", request.await_args_list[1].args[0])
        self.assertNotIn("secret", request.await_args_list[1].args[0])
        self.assertEqual(request.await_args.kwargs["headers"]["x-goog-api-key"], "secret")

    async def test_notification_history_survives_reload_and_respects_channels(self):
        self.notification_patch.stop()
        try:
            with patch("app.pgdata.load_preferences", return_value={"notify_windows": False}), \
                 patch("app.telegram_bot.send_notification", AsyncMock()) as telegram:
                await monitor.notify("api-models", "Title", "Body", False)
                telegram.assert_not_awaited()
                await monitor.notify("api-models", "Other", "New", True)
                telegram.assert_awaited_once()
            self.assertEqual(monitor.events(1)["events"][0]["text"], "New")
            self.assertEqual(monitor.events()["cursor"], 2)
        finally:
            self.notification_patch.start()

    async def test_discovery_uses_release_list_and_previous_published_table(self):
        source = {"url": "https://scores.example.com/", "format": "csv", "id_keys": ["model"], "value_keys": ["score"],
                  "discover": [{"kind": "script", "contains": "main."}, {"kind": "date", "template": "/table_{date}.csv"}]}
        async def request(url, **kwargs):
            if url.endswith("/"):
                return b'<script src="/static/main.js"></script>', "text/html", url
            if url.endswith(".js"):
                return b'const releases=["2026-10-03","2026-10-01"];const unrelated="2099-01-01";', "text/javascript", url
            if "2026_10_03" in url:
                raise ValueError("API HTTP 404")
            return b"model,score\na,9\n", "text/csv", url
        reader = AsyncMock(side_effect=request)
        with patch.object(monitor, "public_request", reader):
            self.assertEqual((await monitor.read_source(source))["a"]["values"], {"score": "9"})
        self.assertEqual(reader.await_args.args[0], "https://scores.example.com/table_2026_10_01.csv")
        self.assertFalse(any("2099" in call.args[0] for call in reader.await_args_list))

    async def test_discovery_does_not_hide_server_failure_with_older_data(self):
        source = {"url": "https://scores.example.com/main.js", "format": "csv", "id_keys": ["model"],
                  "discover": [{"kind": "date", "template": "/table_{date}.csv"}]}
        reader = AsyncMock(side_effect=[(b'["2026-10-03","2026-10-01"]', "text/javascript", source["url"]),
                                       ValueError("API HTTP 503")])
        with patch.object(monitor, "public_request", reader), self.assertRaisesRegex(ValueError, "503"):
            await monitor.read_source(source)
        self.assertEqual(reader.await_count, 2)


class ExtractionTest(unittest.TestCase):
    def test_proxy_builds_ship_manifests_required_by_background_permissions(self):
        import build
        for key in ("proxy", "proxy_only"):
            with self.subTest(target=key):
                self.assertIn("app/module_builtins;app/module_builtins", build.TARGETS[key]["data"])
                for module in ("api-models", "benchmarks"):
                    package = host.load_package(host.default_root(), module)
                    self.assertIn("background", package["permissions"])

    def test_embedded_react_payload_and_selected_agent_signal(self):
        data = {"signals": [{"signal": "success", "rows": [{"name": "model", "score": 9}]}]}
        text = '<script>self.__next_f.push([1,' + json.dumps("prefix" + json.dumps(data)) + '])</script>'
        spec = {"format": "embedded", "marker": '"signals":', "select": {"key": "signal", "value": "success", "items_path": "rows"},
                "id_keys": ["name"], "value_keys": ["score"]}
        self.assertEqual(monitor.extract(text, spec)["model"]["values"], {"score": 9})

    def test_csv_wildcard_ignores_description_and_missing_scores(self):
        values = monitor.extract("model,score,release,other\na,12.5,October,\n", {"format": "csv", "id_keys": ["model"], "value_keys": ["*"]})
        self.assertEqual(values["a"]["values"], {"score": "12.5"})

    def test_js_data_is_parsed_without_execution(self):
        result = monitor.extract('const data=[{name:"a",score:12,rank:1}]; throw new Error("x");',
                                 {"format": "js-records", "id_keys": ["name"], "value_keys": ["score", "rank"]})
        self.assertEqual(result["a"]["values"], {"score": 12, "rank": 1})

    def test_invalid_or_empty_records_fail_instead_of_clearing_baseline(self):
        for text in ('{"data":[]}', '{"data":[{"label":"a"}]}', '<html>maintenance</html>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                monitor.extract(text, {})


class PublicRequestTest(unittest.IsolatedAsyncioTestCase):
    async def request(self, handler, **kwargs):
        real_client = httpx.AsyncClient
        def client(**options):
            return real_client(transport=httpx.MockTransport(handler), **options)
        with patch.object(monitor.httpx, "AsyncClient", client), patch("app.websearch._resolve_public", AsyncMock()):
            return await monitor.public_request("https://api.example.com/start", **kwargs)

    def response(self, raw=b"ok", peer="8.8.8.8", **kwargs):
        class Stream:
            def get_extra_info(self, name):
                return (peer, 443)
        return httpx.Response(200, content=raw, extensions={"network_stream": Stream()}, **kwargs)

    async def test_rebound_connected_private_peer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Private"):
            await self.request(lambda request: self.response(peer="127.0.0.1"))

    async def test_missing_peer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "verified"):
            await self.request(lambda request: httpx.Response(200, content=b"ok"))

    async def test_public_reply_is_bounded_and_credential_redirect_refused(self):
        self.assertEqual((await self.request(lambda request: self.response()))[0], b"ok")
        with self.assertRaisesRegex(ValueError, "too large"):
            await self.request(lambda request: self.response(b"123456"), limit=5)
        def redirect(request):
            response = self.response()
            response.status_code = 302
            response.headers["location"] = "https://other.example.com/steal"
            return response
        with self.assertRaisesRegex(ValueError, "Credential redirect"):
            await self.request(redirect, headers={"Authorization": "Bearer secret"})


class MediaTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(host, "default_root", return_value=self.root / "modules")
        self.root_patch.start()
        self.data_patch = patch.object(pgdata, "MEMU_DIR", self.root / "data")
        self.data_patch.start()
        host.configure_module(self.root / "modules", "studio", enabled=True)
        self.config = {"endpoint": "https://music.example.com/generate", "api_key": "secret",
                       "template": {"prompt": "{{prompt}}", "instrumental": "{{instrumental}}", "seconds": "{{duration}}"}}
        media.music_config("studio", self.config)

    def tearDown(self):
        self.data_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_clip_is_deduplicated_and_profile_owned(self):
        first = media.save_clip(audio(), profile_id="default", duration=1)
        second = media.save_clip(audio(), profile_id="default")
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(len(list(media.clip_folder("default").glob("*.wav"))), 1)
        with patch.object(pgdata, "_profiles_state", return_value={"active_id": "default", "items": [{"id": "default"}, {"id": "other"}]}):
            self.assertNotEqual(media.clip_folder("other"), media.clip_folder("default"))

    def test_audio_mismatch_and_html_are_rejected(self):
        for value in [audio(b"<html>bad</html>"), "data:text/html;base64,YQ==", "not audio"]:
            with self.assertRaises(ValueError):
                media.save_clip(value)

    def test_key_omission_preserves_and_explicit_blank_clears(self):
        self.assertNotIn("secret", json.dumps(media.music_config("studio")))
        config = {key: value for key, value in self.config.items() if key != "api_key"}
        self.assertTrue(media.music_config("studio", config)["key_saved"])
        self.assertFalse(media.music_config("studio", {**config, "api_key": ""})["key_saved"])

    async def test_raw_music_and_native_template_types(self):
        raw, _ = media.decode_audio(audio())
        request = AsyncMock(return_value=(raw, "audio/wav", self.config["endpoint"]))
        with patch.object(media, "public_request", request):
            output = await media.generate_music("studio", {"prompt": "Test", "duration": 70, "instrumental": False})
        self.assertEqual(output["audio"], audio())
        body = request.await_args.kwargs["payload"]
        self.assertIs(body["instrumental"], False)
        self.assertEqual(body["seconds"], 70)
        self.assertEqual(request.await_args.kwargs["headers"]["Authorization"], "Bearer secret")

    async def test_base64_wav_and_blank_url_path(self):
        raw, _ = media.decode_audio(audio())
        media.music_config("studio", {**self.config, "audio_path": "", "base64_path": "predictions.0.bytesBase64Encoded"})
        body = json.dumps({"predictions": [{"bytesBase64Encoded": base64.b64encode(raw).decode()}]}).encode()
        with patch.object(media, "public_request", AsyncMock(return_value=(body, "application/json", self.config["endpoint"]))):
            output = await media.generate_music("studio", {})
        self.assertEqual(output["mime"], "audio/wav")
        self.assertEqual(output["audio"], audio())

    async def test_job_polling_and_relative_ogg_download_without_auth(self):
        config = {**self.config, "poll_url": "/jobs/{id}", "audio_path": "output.url"}
        media.music_config("studio", config)
        request = AsyncMock(side_effect=[(b'{"id":"job 1","status":"queued"}', "application/json", config["endpoint"]),
            (b'{"status":"done","output":{"url":"/song.ogg"}}', "application/json", "https://music.example.com/jobs/job%201"),
            (b"OggSfixture", "application/octet-stream", "https://music.example.com/song.ogg")])
        with patch.object(media, "public_request", request), patch.object(media.asyncio, "sleep", AsyncMock()):
            output = await media.generate_music("studio", {"prompt": "Song"})
        self.assertEqual(output["mime"], "audio/ogg")
        self.assertIn("job%201", request.await_args_list[1].args[0])
        self.assertEqual(request.await_args_list[2].args[0], "https://music.example.com/song.ogg")
        self.assertNotIn("headers", request.await_args_list[2].kwargs)

    async def test_cross_origin_poll_and_failed_generation_are_rejected(self):
        media.music_config("studio", {**self.config, "poll_url": "https://other.example.com/{id}"})
        with patch.object(media, "public_request", AsyncMock(return_value=(b'{"id":"1","status":"queued"}', "application/json", self.config["endpoint"]))):
            with self.assertRaisesRegex(ValueError, "same API origin"):
                await media.generate_music("studio", {})

    async def test_music_cancel_stops_active_request_and_cleans_job(self):
        from fastapi import FastAPI
        from app import module_services_api
        application = FastAPI()
        application.include_router(module_services_api.router)
        entered = asyncio.Event()
        async def generate(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(media, "generate_music", generate):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1") as client:
                request = asyncio.create_task(client.post("/api/pg/modules/music", json={"module_id": "studio", "action": "generate", "request_id": "fixture-job"}))
                await entered.wait()
                response = await client.post("/api/pg/modules/music", json={"module_id": "studio", "action": "cancel", "request_id": "fixture-job"})
                self.assertTrue(response.json()["cancelled"])
                self.assertEqual((await request).status_code, 409)
                self.assertNotIn(("studio", "fixture-job"), module_services_api._MUSIC_JOBS)
        with patch.object(media, "public_request", AsyncMock(return_value=(b'{"status":"failed"}', "application/json", self.config["endpoint"]))):
            with self.assertRaisesRegex(ValueError, "generation failed"):
                await media.generate_music("studio", {})

    def test_clip_http_replay_rejects_other_profile_and_traversal(self):
        with TestClient(create_app(), base_url="http://127.0.0.1") as client:
            result = client.post("/api/voice/clips", json={"audio": audio(), "profile_id": "default"})
            self.assertEqual(result.status_code, 200, result.text)
            url = result.json()["url"]
            self.assertEqual(client.get(url).content, media.decode_audio(audio())[0])
            self.assertEqual(client.get(url.replace("profile_id=default", "profile_id=other")).status_code, 403)
            self.assertEqual(client.post("/api/voice/clips", json={"audio": audio(), "profile_id": "other"}).status_code, 409)
            self.assertEqual(client.get("/api/voice/clips/not-a-hash.wav").status_code, 404)


class ReasoningAndCookieTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_effort_fields_reach_proxy_provider(self):
        from app.openai_api import chat_completions
        from app.state import state
        from tests.protocol_contract_test import make_request
        captured = []
        async def chunks(model, messages, params, **kwargs):
            captured.append(params)
            yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        for settings in ({"reasoning_effort": "high"}, {"reasoning": {"effort": "medium"}},
                         {"effort": "low", "reasoning_effort": "high"}):
            with patch("app.openai_api._check_auth", return_value=None), \
                 patch("app.openai_api.chat_service.stream_completion", chunks), patch.object(state, "commit_trace"):
                result = await chat_completions(make_request({"model": "antigravity-gemini-3.8-flash",
                    "messages": [{"role": "user", "content": "fixture"}], **settings}))
                self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual([item["effort"] for item in captured], ["high", "medium", "low"])

    def test_gemini_imports_header_curl_and_cookie_export(self):
        from app.providers.geminiweb import _parse_cookie_blob
        cookie = "SAPISID=fixture; other=value"
        for value in [cookie, "Cookie: " + cookie, "curl https://gemini.google.com -H 'Cookie: " + cookie + "'",
                      "curl https://gemini.google.com --cookie '" + cookie + "'",
                      json.dumps([{"name": "SAPISID", "value": "fixture"}, {"name": "other", "value": "value"}])]:
            with self.subTest(value=value):
                self.assertEqual(_parse_cookie_blob(value), (cookie, "fixture"))


if __name__ == "__main__":
    unittest.main()
