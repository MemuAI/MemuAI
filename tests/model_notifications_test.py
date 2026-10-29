"""Offline regressions for model notification details and monitor recovery."""
from __future__ import annotations

import asyncio
import json
import httpx
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import module_monitor as monitor, modules_host as host, module_integration_data as data
from app.model_listing import find_spec, listing_spec, notification_detail


class ListingMetadata(unittest.TestCase):
    def test_zero_unknown_invalid_and_nonfinite_prices_are_distinct(self):
        for value in (None, "", True, False, "NaN", "Infinity", "-1", {}, [], "1e309"):
            with self.subTest(value=value):
                self.assertIsNone(listing_spec({"pricing": {"prompt": value}})["input"])
        self.assertEqual(listing_spec({"pricing": {"prompt": "0", "completion": "0.000003"}})["output"], 3)
        self.assertEqual(listing_spec({"pricing": {"prompt": "0"}})["input"], 0)
        self.assertIn("бесплатно → не указана", notification_detail({"input": 0}))
        self.assertIn("output: text", notification_detail({"outputs": ["text"]}, en=True))

    def test_malformed_optional_metadata_never_breaks_the_listing(self):
        for value in (None, [], "text", True, 10):
            row = listing_spec({"pricing": value, "architecture": value, "context_length": "NaN", "top_provider": value})
            self.assertEqual(row, {"context": 0, "input": None, "output": None, "inputs": [], "outputs": []})
        self.assertEqual(listing_spec({"architecture": {"modality": "text+image->text+image"}})["outputs"], ["text", "image"])

    def test_catalogue_aliases_do_not_mix_price_variants_or_vendors(self):
        specs = {"a/model": {"input": 5}, "a/model:free": {"input": 0}, "b/model": {"input": 9}}
        self.assertEqual(find_spec("model:free", "", specs), {"input": 0})
        self.assertEqual(find_spec("model", "", specs), {})
        self.assertEqual(find_spec("a/model", "", specs), {"input": 5})
        self.assertEqual(find_spec("new/model", "", {"a/model:free": {"input": 0}}), {})

    def test_catalogue_bad_row_does_not_discard_valid_siblings(self):
        rows = {"data": [None, {"id": "bad", "pricing": [], "architecture": "x", "context_length": "x"},
                         {"id": "good", "pricing": {"prompt": "0"}, "architecture": {"output_modalities": ["text"]}}]}
        response = type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: rows})()
        with patch("httpx.get", return_value=response):
            result = data._fetch_openrouter()
        self.assertEqual(result["good"]["input"], 0)
        self.assertEqual(result["good"]["outputs"], ["text"])


class NotificationRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memu-model-notifications-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "modules"
        p = patch.object(host, "default_root", return_value=self.root)
        p.start(); self.addCleanup(p.stop)
        host.configure_module(self.root, "api-models", enabled=True)
        self.spec = {"id": "fixture", "title": "Fixture", "url": "https://example.com/models"}
        for p in (patch("app.pgdata.load_preferences", return_value={"notify_windows": False, "language": "en"}),
                  patch("app.inbox_hooks.module_event"), patch("app.telegram_bot.send_notification", AsyncMock()),
                  patch.object(data, "catalog", return_value={})):
            p.start(); self.addCleanup(p.stop)

    def configure(self, sources=None):
        monitor.configure("api-models", {"enabled": True, "telegram": False, "sources": sources or [self.spec]})

    async def scan(self, rows):
        records = monitor.extract(json.dumps({"data": rows}), self.spec)
        with patch.object(monitor, "read_source", AsyncMock(return_value=records)):
            return await monitor.scan("api-models", force=True)

    async def test_price_type_snapshot_and_channels_keep_full_model_id(self):
        self.configure()
        await self.scan([{"id": "a/old"}])
        await self.scan([{"id": "a/old"}, {"id": "a/new:free", "name": "New Model", "pricing": {"prompt": "0", "completion": "0.000002"},
                                          "architecture": {"output_modalities": ["text", "image"]}}])
        event, = monitor.events(0, "api-models")["events"]
        self.assertIn("+ New Model · a/new:free\n  Price / 1M tokens", event["text"])
        self.assertIn("free → $2; output: text, image", event["text"])
        self.assertEqual(event["models"][0]["key"], "a/new:free")
        with patch.object(data, "_monitor", return_value={"events": [event]}), patch.object(data, "_monitor_status", return_value={}):
            model, = data.api_models_digest(prices=False)["models"]
        self.assertEqual((model["key"], model["input"], model["outputs"]), ("a/new:free", 0, ["text", "image"]))

    async def test_unknown_price_and_output_are_explicit(self):
        self.configure()
        await self.scan([{"id": "old"}])
        await self.scan([{"id": "old"}, {"id": "new"}])
        text = monitor.events()["events"][0]["text"]
        self.assertIn("not listed → not listed; output: not listed", text)

    async def test_every_notification_channel_receives_price_and_output(self):
        record = {"id": "a/m", "name": "M", "spec": {"input": 1.5, "output": 0, "outputs": ["audio"]}}
        with patch("app.pgdata.load_preferences", return_value={"notify_windows": True, "language": "en"}), \
                patch("app.desktop_notifications.show_completion") as windows, \
                patch("app.telegram_bot.send_notification", AsyncMock()) as telegram, \
                patch("app.inbox_hooks.module_event") as inbox:
            await monitor.notify("api-models", "Models", "+ M · a/m", models=[record])
        text = monitor.events()["events"][0]["text"]
        self.assertIn("$1.5 → free; output: audio", text)
        self.assertEqual(windows.call_args.args[1], text)
        self.assertEqual(telegram.await_args.args[1], text)
        self.assertEqual(inbox.call_args.args[2], text)

    async def test_digest_uses_complete_snapshots_when_toast_text_is_truncated(self):
        outputs = ["custom_mode_" + "x" * 26 + str(i) for i in range(12)]
        rows = [{"id": "a/" + str(i), "name": "M" * 290 + str(i), "spec": {"input": 0, "output": 0, "outputs": outputs}} for i in range(50)]
        text = "\n".join("+ " + row["name"] + " · " + row["id"] for row in rows)
        await monitor.notify("api-models", "Models", text, False, models=rows)
        event, = monitor.events()["events"]
        self.assertEqual(len(event["text"]), 10000)
        with patch.object(data, "_monitor", return_value={"events": [event]}), patch.object(data, "_monitor_status", return_value={}):
            digest = data.api_models_digest(limit=20, prices=False)
        self.assertEqual(len(digest["models"]), 20)
        self.assertEqual(digest["models"][-1]["key"], "a/19")

    async def test_api_error_response_is_bounded_while_streaming(self):
        class ErrorBody(httpx.AsyncByteStream):
            consumed = 0
            async def __aiter__(self):
                for _ in range(100):
                    self.consumed += 1
                    yield b"x" * 4096
        stream = ErrorBody()
        peer = type("Peer", (), {"get_extra_info": lambda self, name: ("8.8.8.8", 443)})()
        def response(request):
            return httpx.Response(503, stream=stream, extensions={"network_stream": peer})
        client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch.object(monitor.httpx, "AsyncClient", return_value=client), patch("app.websearch._resolve_public", AsyncMock()):
            with self.assertRaises(ValueError) as error:
                await monitor.public_request("https://example.com/models")
        self.assertEqual(len(error.exception.body), 8192)
        self.assertEqual(stream.consumed, 2)

    async def test_changed_credentials_reset_error_and_attempt_for_own_source_only(self):
        sources = [{**self.spec, "id": "one", "api_key": "old", "auth": "bearer"},
                   {**self.spec, "id": "two", "api_key": "old", "auth": "bearer"}]
        self.configure(sources)
        def fail(store):
            for source in store["jobs"]["api-models"]["sources"]:
                source.update(error="old failure", last_attempt=123, baseline={})
        monitor._mutate(fail)
        self.configure([{**sources[0], "api_key": "new"}, sources[1]])
        one, two = monitor.status("api-models")["sources"]
        self.assertNotIn("error", one)
        self.assertNotIn("last_attempt", one)
        self.assertEqual(two["error"], "old failure")
        self.assertEqual(two["last_attempt"], 123)

    async def test_cancelled_scan_releases_lease_and_can_retry_immediately(self):
        self.configure()
        with patch.object(monitor, "read_source", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await monitor.scan("api-models", force=True)
        stored = monitor._read()["jobs"]["api-models"]["sources"][0]
        self.assertNotIn("lease", stored)
        self.assertNotIn("last_attempt", stored)
        self.assertEqual((await self.scan([{"id": "a"}]))["sources"][0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
