"""Regression tests for the backend audit fixes (storage, scheduler, Telegram,
Work context, web fetch SSRF, integrations, decisions)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import decisions, storage, telegram_bot, telegram_format, websearch, work_context

class StorageAuditTest(unittest.TestCase):
    def test_corrupt_main_is_kept_before_mutation_overwrites_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chats.json"
            path.write_text('[{"id": "a", "title": "half-writ', encoding="utf-8")
            result = storage.mutate_json(path, [], lambda rows: (rows.append({"id": "b"}) or True),
                                         backup=False, validator=lambda v: isinstance(v, list),
                                         repair_invalid=True)
            self.assertEqual(result, [{"id": "b"}])
            kept = list((Path(temp) / "backups").glob("chats.corrupt-*.json"))
            self.assertEqual(len(kept), 1)
            self.assertIn("half-writ", kept[0].read_text(encoding="utf-8"))

            self.assertEqual(storage._backup_candidates(path), [])

    def test_unopenable_main_aborts_mutation_instead_of_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chats.json"
            path.write_text('[{"id": "keep"}]', encoding="utf-8")
            calls = []
            with patch.object(storage, "_load_primary", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    storage.mutate_json(path, [], lambda rows: calls.append(1) or True,
                                        backup=False, validator=lambda v: isinstance(v, list))
            self.assertEqual(calls, [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), [{"id": "keep"}])

            with patch.object(storage, "_load_primary", side_effect=PermissionError("locked")):
                self.assertEqual(storage.load_json_with_recovery(path, "default"), "default")

    def test_backups_are_ordered_by_write_time_not_local_clock_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            bdir = Path(temp) / "backups"
            bdir.mkdir()

            names = ["state-20261025-025900-000000001.json", "state-20261025-021000-000000002.json"]
            base = time.time() - 100
            for offset, name in enumerate(names):
                (bdir / name).write_text(json.dumps(offset), encoding="utf-8")
                os.utime(bdir / name, (base + offset, base + offset))
            newest = storage._backup_candidates(path)[0]
            self.assertTrue(newest.endswith(names[1]))
            self.assertEqual(storage.load_json_with_recovery(path, None), 1)

class TelegramAuditTest(unittest.TestCase):
    def test_callback_data_never_exceeds_telegram_limit(self) -> None:
        self.assertEqual(telegram_bot._cb("open:chat-123"), "open:chat-123")
        self.assertEqual(telegram_bot._cb("hist:" + "x" * 80 + ":1"), "noop")
        self.assertEqual(telegram_bot._cb("open:" + "я" * 30), "noop")

    def test_polling_backoff_grows_and_honours_retry_after(self) -> None:
        error = RuntimeError("Unauthorized")
        delays = [telegram_bot._poll_backoff(n, error) for n in range(1, 8)]
        self.assertEqual(delays[0], 5)
        self.assertEqual(max(delays), 60)
        self.assertEqual(delays, sorted(delays))
        self.assertEqual(telegram_bot._poll_backoff(1, RuntimeError(
            "Too Many Requests: retry after 17")), 17)

    def test_link_with_quote_keeps_valid_href(self) -> None:
        rendered = telegram_format.markdown_to_telegram_html('[x](https://a.example/?q="b")')
        self.assertEqual(rendered, '<a href="https://a.example/?q=&quot;b&quot;">x</a>')

class WorkContextAuditTest(unittest.TestCase):
    def test_clipped_utf8_is_not_decoded_as_utf16_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "big.md"

            path.write_text("я" * 4000 + "x", encoding="utf-8")
            text, truncated = work_context._safe_read(path, set(), 1001)
            self.assertTrue(truncated)
            self.assertIn("яяя", text)
            small = Path(temp) / "small.md"
            small.write_text("я" * 600, encoding="utf-8")
            text, truncated = work_context._safe_read(small, set(), 1000)
            self.assertEqual(text, "я" * 600)
            self.assertFalse(truncated)

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_directory_link_outside_root_is_not_walked(self) -> None:
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as project:
            for index in range(5):
                Path(outside, f"secret{index}.py").write_text("x = 1\n", encoding="utf-8")
            Path(project, "main.py").write_text("print(1)\n", encoding="utf-8")
            os.symlink(outside, Path(project, "linked"), target_is_directory=True)
            walked = []
            real_resolve = Path.resolve

            def spy(self, *args, **kwargs):
                walked.append(str(self))
                return real_resolve(self, *args, **kwargs)

            with patch.object(Path, "resolve", spy):
                result = work_context.build_work_context(project, "main")
            self.assertEqual(result["index"], ["main.py"])
            self.assertFalse(any("secret" in item for item in walked))

class WebFetchPeerTest(unittest.TestCase):
    def test_private_connected_peer_is_rejected_for_direct_connections(self) -> None:
        class Resp:
            primary_ip = "127.0.0.1"

        with patch.dict(os.environ, {name: "" for name in websearch._PROXY_ENV}):
            self.assertTrue(websearch._private_peer(Resp(), ""))
            Resp.primary_ip = "93.184.216.34"
            self.assertFalse(websearch._private_peer(Resp(), ""))
            Resp.primary_ip = "::ffff:10.0.0.1"
            self.assertTrue(websearch._private_peer(Resp(), ""))
        Resp.primary_ip = "127.0.0.1"

        self.assertFalse(websearch._private_peer(Resp(), "http://127.0.0.1:7890"))
        self.assertFalse(websearch._private_peer(object(), ""))

class DecisionsEmulationTest(unittest.TestCase):
    def test_off_schema_chat_answer_does_not_raise(self) -> None:
        async def llm(_system, _user):
            return json.dumps({"answers": {"a": 0.9, "b": {"probabilities": {"x": "high", "y": 2}},
                                           "c": {"probabilities": {"1": "?", "2": -1}}}})

        client = decisions.DecisionClient({"provider": "model"}, llm=llm)
        questions = {"a": decisions.Question.noul("is it"),
                     "b": decisions.Question.choice("pick", ["x", "y"]),
                     "c": decisions.Question.score("rate", ["low", "high"])}
        result = asyncio.run(client._emulate("state", questions))
        self.assertAlmostEqual(result["a"].noul, 0.9)
        self.assertEqual(result["b"].choice, "y")
        self.assertAlmostEqual(sum(result["c"].probabilities.values()), 1.0)

if __name__ == "__main__":
    unittest.main()
