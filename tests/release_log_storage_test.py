"""Bounded request-log recovery and serialized rotate/append/clear operations."""
import builtins
from collections import deque
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import state as storage

class LogStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "requests.jsonl"
        self.owner = object.__new__(storage.State)
        self.owner._lock = threading.RLock()
        self.owner.logs = deque(maxlen=4)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("LOGS_PATH", str(self.path)), ("LOGS_DIR", self.temporary.name),
                            ("LOG_MEMORY_LIMIT", 4), ("LOG_FILE_MAX_BYTES", 1024)):
            stack.enter_context(patch.object(storage, name, value))
        stack.enter_context(patch.object(storage, "_log_persist_enabled", return_value=True))

    @staticmethod
    def row(request_id, padding=""):
        return json.dumps({"request_id": request_id, "padding": padding}, ensure_ascii=False).encode("utf-8") + b"\n"

    def read_ids(self):
        return [json.loads(row)["request_id"] for row in self.path.read_bytes().splitlines()]

    def run_during_paused_rotation(self, concurrent):
        self.path.write_bytes(self.row("old", "x" * 280) * 5)
        reached = threading.Event()
        release = threading.Event()
        second_done = threading.Event()
        errors = []
        original_open = builtins.open

        def controlled_open(path, mode="r", *args, **kwargs):
            if (str(path).startswith(str(self.path) + ".tmp") and mode == "wb"
                    and threading.current_thread().name == "log-review-rotator"):
                reached.set()
                if not release.wait(5):
                    raise AssertionError("rotation proof did not release writer")
            return original_open(path, mode, *args, **kwargs)

        def run(operation, finished=None):
            try:
                operation()
            except BaseException as exc:
                errors.append(exc)
            finally:
                if finished is not None:
                    finished.set()

        first = threading.Thread(name="log-review-rotator", target=run,
                                 args=(lambda: self.owner.persist_log(storage.LogEntry(request_id="writer-A")),))
        second = threading.Thread(target=run, args=(concurrent, second_done))
        with patch("builtins.open", controlled_open):
            first.start()
            try:
                self.assertTrue(reached.wait(5), "writer never reached rotation publication")
                second.start()

                second_done.wait(0.5)
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])

    def test_concurrent_append_survives_rotation_publication(self):
        self.run_during_paused_rotation(
            lambda: self.owner.persist_log(storage.LogEntry(request_id="writer-B")))
        self.assertIn("writer-A", self.read_ids())
        self.assertIn("writer-B", self.read_ids())

    def test_clear_waits_for_rotation_and_does_not_resurrect_file(self):
        self.owner.logs.append(storage.LogEntry(request_id="old"))
        self.run_during_paused_rotation(self.owner.clear_logs)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.owner.logs), [])

    def test_cancel_releases_memory_lock_before_waiting_for_file_transaction(self):
        from app.storage import _json_transaction

        self.path.write_bytes(json.dumps({"request_id": "loaded", "prompt": {
            "messages": [{"role": "user", "content": "hello"}]}}).encode() + b"\n")
        self.owner.accounts = {}
        reached = threading.Event()
        original_persist = self.owner.persist_log

        def persist(entry):
            reached.set()
            original_persist(entry)

        with patch.object(self.owner, "persist_log", persist), \
             patch.object(storage, "state", self.owner), \
             patch.object(storage, "_log_payloads_enabled", return_value=True):
            entry = storage.LogEntry(request_id="canceled")
            thread = threading.Thread(target=self.owner.cancel_trace, args=(entry,))
            try:
                with _json_transaction(str(self.path)):
                    thread.start()
                    self.assertTrue(reached.wait(5))
                    acquired = self.owner._lock.acquire(timeout=1)
                    self.assertTrue(acquired, "cancel held the memory lock while waiting for the log file")
                    if acquired:
                        self.owner._lock.release()

                    self.owner._load_logs()
                    self.assertIsNotNone(next(item for item in self.owner.logs
                                              if item.request_id == "loaded").prompt)
            finally:
                if thread.ident is not None:
                    thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertIn("canceled", self.read_ids())

    def test_load_streams_bounded_lines_and_keeps_recent_row_limit(self):
        self.path.write_bytes(b"".join(self.row(str(index), "x" * 80) for index in range(100)))
        original_open = builtins.open
        read_limits = []

        class BoundedReader:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def readline(self, size=-1):
                if not 0 < size <= 1025:
                    raise AssertionError("log loading used an unbounded line read")
                read_limits.append(size)
                return self.handle.readline(size)

            def readlines(self, *args):
                raise AssertionError("log loading buffered the complete file")

        def bounded_open(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            return BoundedReader(handle) if str(path) == str(self.path) and "r" in mode else handle

        with patch("builtins.open", bounded_open):
            self.owner._load_logs()
        self.assertTrue(read_limits)
        self.assertEqual([entry.request_id for entry in self.owner.logs], ["99", "98", "97", "96"])

    def test_load_byte_budget_and_oversized_line_preserve_neighbors(self):
        recent = [self.row("before", "x" * 220), self.row("after", "x" * 220)]
        self.path.write_bytes(self.row("older", "x" * 700) + recent[0]
                              + self.row("oversized", "x" * 3000) + recent[1])
        self.owner._load_logs()
        self.assertEqual([entry.request_id for entry in self.owner.logs], ["after", "before"])

    def test_rotation_retains_only_complete_valid_utf8_records(self):
        self.path.write_bytes(self.row("old") + self.row("before", "Кот")
                              + b'{"request_id":"bad-utf8","padding":"\xff"}\n'
                              + b"invalid json\n" + b"x" * 3000 + b"\n"
                              + self.row("after", "🐈")
                              + self.row("incomplete")[:-1])
        self.owner._rotate_logs()
        self.assertLessEqual(self.path.stat().st_size, 512)
        self.assertEqual(self.read_ids(), ["before", "after"])
        self.owner._load_logs()
        self.assertEqual([entry.request_id for entry in self.owner.logs], ["after", "before"])

    def test_failed_rotation_publication_preserves_original_and_cleans_temp(self):
        original = self.row("before") + self.row("after")
        self.path.write_bytes(original)
        with patch.object(storage.os, "replace", side_effect=PermissionError("locked log")):
            self.owner._rotate_logs()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("requests.jsonl.tmp*")), [])

    def test_append_after_torn_last_record_starts_a_complete_new_line(self):
        self.path.write_bytes(self.row("old") + b'{"request_id":"torn')
        self.owner.persist_log(storage.LogEntry(request_id="new"))
        self.owner._load_logs()
        self.assertEqual([entry.request_id for entry in self.owner.logs], ["new", "old"])

    def test_disabled_persistence_does_not_create_log_file(self):
        with patch.object(storage, "_log_persist_enabled", return_value=False):
            self.owner.persist_log(storage.LogEntry(request_id="disabled"))
        self.assertFalse(self.path.exists())

    def test_short_known_credentials_are_collected_without_metadata_values(self):
        account = storage.Account("custom", {"api_key": "short", "session_id": "s",
                                             "token_count": "public-metadata"})
        with patch.object(storage.state, "accounts", {account.id: account}), \
             patch.object(storage.config_store.settings, "api_keys", ["k"]), \
             patch.object(storage.config_store.settings, "search_api_key", "q"):
            values = storage._prompt_secret_values()
        self.assertTrue({"short", "s", "k", "q"}.issubset(values))
        self.assertNotIn("public-metadata", values)

if __name__ == "__main__":
    unittest.main()
