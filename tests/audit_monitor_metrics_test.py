"""Regression tests for the «Новые модели API» monitor and the Proxy statistics."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app import module_monitor, modules_host
from app.metrics import HOUR, Metrics


SPEC = {"id": "openrouter", "title": "OpenRouter", "url": "https://openrouter.ai/api/v1/models",
        "format": "json", "items_path": "data", "id_keys": ["id"], "label_keys": ["name", "id"],
        "families": [], "api_key": ""}


def record(model_id):
    return {"id": model_id, "name": model_id.upper(), "values": {}, "family": model_id.split("/")[0]}


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.root = modules_host.default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        modules_host._mutate_state(self.root, "api-models", lambda row: row.update(enabled=True))
        path = module_monitor._path()
        if path.exists():
            path.unlink()
        self.records = {"a/x": record("a/x")}
        self.sent = []

        async def fake_read(_source):
            return dict(self.records)

        async def fake_notify(_module, title, text, telegram=True):
            self.sent.append(text)

        for target, value in (("read_source", fake_read), ("notify", fake_notify)):
            patcher = patch.object(module_monitor, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def configure(self, **changes):
        module_monitor.configure("api-models", {"enabled": True, "interval": 300, "mode": "new",
                                                "telegram": True, "sources": [{**SPEC, **changes}]})

    def test_changing_the_family_filter_keeps_the_baseline(self):
        self.configure()
        asyncio.run(module_monitor.scan("api-models", force=True))          # baseline, silent
        self.assertEqual(self.sent, [])
        self.records["b/y"] = record("b/y")
        self.configure(families=["b", "a"])                                 # used to wipe the baseline
        asyncio.run(module_monitor.scan("api-models", force=True))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("b/y", self.sent[0])

    def test_changing_the_url_starts_a_new_baseline(self):
        self.configure()
        asyncio.run(module_monitor.scan("api-models", force=True))
        self.records["b/y"] = record("b/y")
        self.configure(url="https://example.com/v1/models")
        asyncio.run(module_monitor.scan("api-models", force=True))
        self.assertEqual(self.sent, [])

    def test_google_ids_get_real_families(self):
        rows = module_monitor.extract(json.dumps({"models": [{"name": "models/gemini-3-pro"}, {"name": "models/x/y"}]}),
                                      {"items_path": "models", "id_keys": ["name"]})
        self.assertEqual(rows["models/gemini-3-pro"]["family"], "")
        self.assertEqual(rows["models/x/y"]["family"], "x")


class TelegramModuleNotificationTest(unittest.TestCase):
    def test_module_alerts_are_not_silenced_by_completion_mode(self):
        from app import telegram_bot
        prefs = {"telegram_enabled": True, "telegram_bot_token": "1:" + "a" * 30, "telegram_chat_id": "42",
                 "telegram_notify_mode": "off"}
        sent = []

        async def fake_send(_client, _token, chat_id, body, keyboard=None, **kwargs):
            sent.append((chat_id, body, kwargs.get("silent")))

        with patch.object(telegram_bot, "load_preferences", return_value=prefs), \
                patch.object(telegram_bot, "_send", fake_send):
            asyncio.run(telegram_bot.send_notification("t", "chat done", kind="completion"))
            self.assertEqual(sent, [])
            asyncio.run(telegram_bot.send_notification("Новые модели", "+ X", kind="module"))
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0][2])


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "metrics.json")

    def test_version_one_files_load_and_new_fields_are_reported(self):
        stamp = int(time.time() // HOUR * HOUR) - 2 * HOUR
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "hours": {str(stamp): {
                "total": 2, "ok": 1, "failed": 1, "ms": 400,
                "providers": {"kiro": {"total": 2, "ok": 1, "failed": 1, "ms": 400}},
                "models": {"kiro-x": 2}, "errors": {"network_error": 1}, "lat": [0] * 11}}}, handle)
        metrics = Metrics(self.path)
        metrics.record(provider="custom", model="custom-y", ok=True, ms=800, prompt_tokens=100, completion_tokens=50)
        snap = metrics.snapshot("day")
        self.assertEqual(snap["total"], 3)
        self.assertEqual({row["model"] for row in snap["models"]}, {"kiro-x", "custom-y"})
        custom = next(row for row in snap["models"] if row["model"] == "custom-y")
        self.assertEqual((custom["prompt_tokens"], custom["completion_tokens"]), (100, 50))
        self.assertIn("comparison", snap)
        self.assertTrue(snap["has_data"])
        # Zero-filled hourly series: every hour of the window is present.
        stamps = [row["ts"] for row in snap["series"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(b - a == HOUR for a, b in zip(stamps, stamps[1:])))

    def test_no_requests_is_no_data_not_perfection(self):
        snap = Metrics(self.path).snapshot("today")
        self.assertFalse(snap["has_data"])
        self.assertEqual(snap["total"], 0)


if __name__ == "__main__":
    unittest.main()
