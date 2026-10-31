"""«Новые модели API» (4.5): the OpenRouter catalogue keeps the release
date, follows link pagination, dedupes, and surfaces the newest models
first — instead of freezing at whatever page the first fetch returned.

Reproduction: ``extract()`` dropped ``created`` entirely, so the feed could
neither sort by release date nor show it; ``read_source`` had no pagination
spec for OpenRouter, so a ``links.next`` page was never fetched; and the
feed listed models in API order grouped by scan time.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import module_integration_data as data
from app import module_monitor as monitor
from app import modules_host as host
from app.model_listing import listing_spec


def row(model_id, created, name=None):
    return {"id": model_id, "name": name or model_id, "created": created,
            "context_length": 100000,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}}


SPEC = {"format": "json", "items_path": "data", "id_keys": ["id"], "label_keys": ["name", "id"]}


class ListingCreatedTest(unittest.TestCase):
    def test_created_parsed_from_seconds_string_and_iso(self):
        self.assertEqual(listing_spec({"created": 1793207893})["created"], 1793207893)
        self.assertEqual(listing_spec({"created": "1793207893"})["created"], 1793207893)
        self.assertEqual(listing_spec({"created": "2026-10-28T17:18:13Z"})["created"], 1793207893)

    def test_created_parsed_from_milliseconds(self):
        self.assertEqual(listing_spec({"created": 1793436000000})["created"], 1793436000)
        self.assertEqual(listing_spec({"created": "1793436000000"})["created"], 1793436000)

    def test_missing_or_bad_created_leaves_spec_shape_unchanged(self):
        for value in (None, "", "garbage", -5, 0, True, 99999999999):
            spec = listing_spec({"created": value})
            self.assertNotIn("created", spec)
        self.assertEqual(listing_spec({"pricing": {}}),
                         {"context": 0, "input": None, "output": None, "inputs": [], "outputs": []})


class ExtractTest(unittest.TestCase):
    def test_created_survives_and_duplicates_collapse(self):
        payload = {"data": [row("particle-ai/particle-1", 1793207893),
                            row("newco/alpha-1", 1793300000),
                            row("newco/alpha-1", 1793300000)],
                   "total_count": 3, "links": {"next": None}}
        records = monitor.extract(json.dumps(payload), SPEC)
        self.assertEqual(sorted(records), ["newco/alpha-1", "particle-ai/particle-1"])
        self.assertEqual(records["newco/alpha-1"]["spec"]["created"], 1793300000)


class ReadSourcePaginationTest(unittest.IsolatedAsyncioTestCase):
    async def test_links_next_pages_are_followed_and_deduped(self):
        pages = {
            "https://openrouter.ai/api/v1/models": {"data": [row("a/one", 100), row("a/two", 200)],
                                                    "total_count": 3,
                                                    "links": {"next": "/api/v1/models?page=2"}},
            "https://openrouter.ai/api/v1/models?page=2": {"data": [row("a/two", 200), row("a/three", 300)],
                                                           "total_count": 3, "links": {"next": None}},
        }
        seen_urls = []

        async def fake_request(url, headers=None, **kwargs):
            seen_urls.append(url)
            return json.dumps(pages[url]).encode(), "application/json", url

        source = {"url": "https://openrouter.ai/api/v1/models", **SPEC}
        with patch.object(monitor, "public_request", fake_request):
            records = await monitor.read_source(source)
        self.assertEqual(sorted(records), ["a/one", "a/three", "a/two"])
        self.assertEqual(records["a/three"]["spec"]["created"], 300)
        self.assertEqual(seen_urls, ["https://openrouter.ai/api/v1/models",
                                     "https://openrouter.ai/api/v1/models?page=2"])

    async def test_offsite_next_link_is_not_followed(self):
        pages = {"https://openrouter.ai/api/v1/models": {"data": [row("a/one", 100)],
                                                         "links": {"next": "https://evil.example/models"}}}

        async def fake_request(url, headers=None, **kwargs):
            return json.dumps(pages[url]).encode(), "application/json", url

        source = {"url": "https://openrouter.ai/api/v1/models", **SPEC}
        with patch.object(monitor, "public_request", fake_request):
            records = await monitor.read_source(source)
        self.assertEqual(sorted(records), ["a/one"])

    async def test_cursor_pagination_keeps_the_current_url(self):
        seen_urls = []

        async def fake_request(url, headers=None, **kwargs):
            seen_urls.append(url)
            if "after_id" in url:
                return json.dumps({"data": [row("a/two", 200)], "has_more": False, "last_id": "a/two"}).encode(), "application/json", url
            return json.dumps({"data": [row("a/one", 100)], "has_more": True, "last_id": "a/one"}).encode(), "application/json", url

        source = {"url": "https://api.example.com/v1/models?limit=1000", **SPEC,
                  "pagination": {"token_path": "last_id", "parameter": "after_id", "more_path": "has_more"}}
        with patch.object(monitor, "public_request", fake_request):
            records = await monitor.read_source(source)
        self.assertEqual(sorted(records), ["a/one", "a/two"])
        self.assertIn("limit=1000", seen_urls[1])
        self.assertIn("after_id=a%2Fone", seen_urls[1])


class ScanSortTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memu-api-models-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "modules"
        patcher = patch.object(host, "default_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        host.configure_module(self.root, "api-models", enabled=True)
        for p in (patch("app.pgdata.load_preferences", return_value={"notify_windows": False, "language": "en"}),
                  patch("app.inbox_hooks.module_event"),
                  patch("app.telegram_bot.send_notification", AsyncMock()),
                  patch.object(data, "catalog", return_value={})):
            p.start()
            self.addCleanup(p.stop)
        self.spec = {"id": "openrouter", "title": "OpenRouter",
                     "url": "https://openrouter.ai/api/v1/models", **SPEC, "families": [], "api_key": ""}

    def configure(self):
        monitor.configure("api-models", {"enabled": True, "interval": 300, "mode": "new",
                                         "telegram": False, "sources": [self.spec]})

    def scan(self, rows):
        # Rows are raw catalogue rows; the scan consumes extract() records.
        records = monitor.extract(json.dumps({"data": list(rows)}), self.spec)
        with patch.object(monitor, "read_source", AsyncMock(return_value=records)):
            asyncio.run(monitor.scan("api-models", force=True))

    def test_models_newer_than_particle_appear_newest_first(self):
        self.configure()
        baseline = [row("particle-ai/particle-1", 1793207893), row("oldco/legacy", 1700000000)]
        self.scan(baseline)
        self.assertEqual(monitor.events(0, "api-models")["events"], [])
        newer = list(baseline)
        for model_id, created in (("newco/alpha-1", 1793300000), ("newco/beta-2", 1793400000),
                                  ("newco/gamma-3", 1793500000), ("newco/delta-4", 1793600000),
                                  ("newco/epsilon-5", 1793700000)):
            newer.append(row(model_id, created))
        self.scan(newer)
        events = monitor.events(0, "api-models")["events"]
        self.assertEqual(len(events), 1)
        keys = [model["key"] for model in events[0]["models"]]
        self.assertEqual(keys, ["newco/epsilon-5", "newco/delta-4", "newco/gamma-3",
                                "newco/beta-2", "newco/alpha-1"])
        self.assertEqual(events[0]["models"][0]["spec"]["created"], 1793700000)
        # A repeat scan with the same catalogue reports nothing new.
        self.scan(newer)
        self.assertEqual(len(monitor.events(0, "api-models")["events"]), 1)

    def test_failed_fetch_keeps_the_baseline_and_reports_the_error(self):
        self.configure()
        self.scan([row("a/one", 100)])
        with patch.object(monitor, "read_source", AsyncMock(side_effect=ValueError("API HTTP 503"))):
            asyncio.run(monitor.scan("api-models", force=True))
        status = monitor.status("api-models")
        self.assertEqual(status["sources"][0]["error"], "API HTTP 503")
        self.assertEqual(monitor.events(0, "api-models")["events"], [])

    def test_filtered_scan_does_not_burn_unseen_families(self):
        # Round 73: a scan with an explicit family filter must not mark
        # models from other families as seen; widening the filter later
        # must still report them as new.
        self.spec["families"] = ["google", "openai"]
        self.configure()
        rows = [row("google/gemini-2", 1793200000), row("openai/gpt-5", 1793201000),
                row("particle-ai/particle-1", 1793207893),
                row("tbc/mischief-1", 1793436000), row("baseten/joey-1", 1793373600),
                row("sourceful/riverflow-v2.6-pro", 1793344800),
                row("nvidia/nemotron-3.5-transcribe", 1793330400),
                row("assemblyai/assemblyai-tts-1", 1793274000)]
        self.scan(rows)
        self.assertEqual(monitor.events(0, "api-models")["events"], [])
        self.spec["families"] = []
        self.configure()
        self.scan(rows)
        events = monitor.events(0, "api-models")["events"]
        self.assertEqual(len(events), 1)
        keys = [model["key"] for model in events[0]["models"]]
        # particle-1 was not visible under the filter either, so it is
        # correctly reported as new as well, last by release date.
        self.assertEqual(keys, ["tbc/mischief-1", "baseten/joey-1",
                                "sourceful/riverflow-v2.6-pro",
                                "nvidia/nemotron-3.5-transcribe",
                                "assemblyai/assemblyai-tts-1",
                                "particle-ai/particle-1"])

    def test_scan_over_fifty_new_models_reports_all(self):
        # Round 73: more than 50 new models in one scan must all be
        # reported (chunked notifications), none silently dropped.
        self.configure()
        self.scan([row("oldco/legacy", 1700000000)])
        rows = [row("oldco/legacy", 1700000000)]
        rows += [row("newco/m%02d" % i, 1793300000 + i) for i in range(55)]
        self.scan(rows)
        events = monitor.events(0, "api-models")["events"]
        keys = [model["key"] for event in events for model in event["models"]]
        self.assertEqual(len(events), 2)
        self.assertEqual(len(keys), 55)
        self.assertEqual(len(set(keys)), 55)
        self.assertEqual(keys, ["newco/m%02d" % i for i in range(54, -1, -1)])


class DigestSortTest(unittest.TestCase):
    def test_digest_sorts_by_release_date_and_exposes_it(self):
        event = {"id": 7, "module_id": "api-models", "title": "Новые модели API · OpenRouter",
                 "at": 1793440000, "text": "",
                 "models": [{"name": "Particle AI: Particle-1", "key": "particle-ai/particle-1",
                             "spec": {"created": 1793207893, "input": 1.0, "output": 2.0}},
                            {"name": "NewCo: Epsilon 5", "key": "newco/epsilon-5",
                             "spec": {"created": 1793700000, "input": 1.0, "output": 2.0}},
                            {"name": "NewCo: Alpha 1", "key": "newco/alpha-1",
                             "spec": {"created": 1793300000, "input": 1.0, "output": 2.0}}]}
        with patch.object(data, "_monitor", return_value={"events": [event], "seen": 0}), \
                patch.object(data, "_monitor_status", return_value={}), \
                patch.object(data, "catalog", return_value={}):
            models = data.api_models_digest(limit=8, prices=False)["models"]
        self.assertEqual([m["key"] for m in models],
                         ["newco/epsilon-5", "newco/alpha-1", "particle-ai/particle-1"])
        self.assertEqual([m["released"] for m in models], [1793700000, 1793300000, 1793207893])


class SixModelsContractTest(unittest.TestCase):
    """Round 73: the six newest models from the OpenRouter website (Newest
    sort, 28-30 Oct 2026) must surface in release order, particle-1 last.

    ``created`` values are fixed here; particle-1 keeps its real live
    ``/v1/models`` value (2026-10-28T17:18:13Z). The other five are the
    website's Newest listing: they are NOT in the live ``/v1/models``
    response (checked 2026-10-31: 456 models, particle-1 newest), so this
    contract is exercised against a mock catalogue containing exactly
    these six rows, as the module must surface every row it receives.
    """

    SIX = [("tbc/mischief-1", 1793436000, "TBC: mischief 1"),
           ("baseten/joey-1", 1793373600, "Baseten: Joey 1"),
           ("sourceful/riverflow-v2.6-pro", 1793344800, "Sourceful: Riverflow V2.6 Pro"),
           ("nvidia/nemotron-3.5-transcribe", 1793330400, "NVIDIA: Nemotron 3.5 Transcribe"),
           ("assemblyai/assemblyai-tts-1", 1793274000, "AssemblyAI: AssemblyAI TTS 1"),
           ("particle-ai/particle-1", 1793207893, "Particle AI: Particle-1")]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memu-api-models-six-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "modules"
        patcher = patch.object(host, "default_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        host.configure_module(self.root, "api-models", enabled=True)
        for p in (patch("app.pgdata.load_preferences", return_value={"notify_windows": False, "language": "en"}),
                  patch("app.inbox_hooks.module_event"),
                  patch("app.telegram_bot.send_notification", AsyncMock()),
                  patch.object(data, "catalog", return_value={})):
            p.start()
            self.addCleanup(p.stop)
        self.spec = {"id": "openrouter", "title": "OpenRouter",
                     "url": "https://openrouter.ai/api/v1/models", **SPEC, "families": [], "api_key": ""}
        monitor.configure("api-models", {"enabled": True, "interval": 300, "mode": "new",
                                         "telegram": False, "sources": [self.spec]})

    def scan(self, rows):
        records = monitor.extract(json.dumps({"data": list(rows)}), self.spec)
        with patch.object(monitor, "read_source", AsyncMock(return_value=records)):
            asyncio.run(monitor.scan("api-models", force=True))

    def test_six_models_surface_newest_first_with_particle_last(self):
        self.scan([row(*self.SIX[-1])])
        self.scan([row(*item) for item in self.SIX])
        events = monitor.events(0, "api-models")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual([model["key"] for model in events[0]["models"]],
                         [key for key, _, _ in self.SIX[:5]])
        self.assertEqual([model["spec"]["created"] for model in events[0]["models"]],
                         [created for _, created, _ in self.SIX[:5]])
        self.scan([row(*item) for item in self.SIX])
        self.assertEqual(len(monitor.events(0, "api-models")["events"]), 1)

    def test_digest_orders_all_six_with_particle_last(self):
        event = {"id": 3, "module_id": "api-models", "title": "Новые модели API · OpenRouter",
                 "at": 1793440000, "text": "",
                 "models": [{"name": name, "key": key, "spec": {"created": created, "input": 1.0, "output": 2.0}}
                            for key, created, name in reversed(self.SIX)]}
        with patch.object(data, "_monitor", return_value={"events": [event], "seen": 0}), \
                patch.object(data, "_monitor_status", return_value={}), \
                patch.object(data, "catalog", return_value={}):
            models = data.api_models_digest(limit=8, prices=False)["models"]
        self.assertEqual([model["key"] for model in models], [key for key, _, _ in self.SIX])
        self.assertEqual([model["released"] for model in models], [created for _, created, _ in self.SIX])

    def test_status_exposes_catalog_update_time_and_count(self):
        self.scan([row(*item) for item in self.SIX])
        source = monitor.status("api-models")["sources"][0]
        self.assertGreater(source["last_success"], 0)
        self.assertEqual(source["count"], 6)
        self.assertEqual(source["error"], "")


if __name__ == "__main__":
    unittest.main()
