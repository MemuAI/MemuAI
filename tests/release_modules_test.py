"""Transactional module partial-save regressions, temporary data only."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import modules_host as host


class ModulePersistentTransactions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="memuai-module-persist-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        host.create_module(self.root, {"id": "proof", "title": "Proof",
                                     "persist": ["a", "b"], "html": "<p>proof</p>"})
        host.save_persistent(self.root, "proof", {"a": 0, "b": 0})

    def test_parallel_partial_saves_preserve_both_updates(self):
        # Force the old out-of-transaction read path to load the same version.
        # A transaction does its own read under the lock, so never hits this gap.
        original = host.load_persistent
        barrier = threading.Barrier(2)

        def overlapping_read(*args, **kwargs):
            value = original(*args, **kwargs)
            barrier.wait(timeout=5)
            return value

        with patch.object(host, "load_persistent", side_effect=overlapping_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(host.save_persistent, self.root, "proof", {"a": 1})
                b = pool.submit(host.save_persistent, self.root, "proof", {"b": 1})
                a.result(timeout=10)
                b.result(timeout=10)
        self.assertEqual(host.load_persistent(self.root, "proof"), {"a": 1, "b": 1})

    def test_persists_only_declared_fields(self):
        path = self.root / "persist" / "proof.json"
        path.write_text(json.dumps({"a": 3, "b": 4, "unlisted": "secret"}), encoding="utf-8")
        result = host.save_persistent(self.root, "proof", {"a": 5, "unlisted": "new"})
        self.assertEqual(result, {"a": 5, "b": 4})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)

    def test_oversized_value_preserves_old_value_and_other_updates(self):
        with self.assertRaises(ValueError):
            host.save_persistent(self.root, "proof", {"a": "x" * host._TEXT_LIMIT, "b": 7})
        self.assertEqual(host.load_persistent(self.root, "proof"), {"a": 0, "b": 7})

    def test_invalid_data_and_argument_contract(self):
        path = self.root / "persist" / "proof.json"
        path.write_text("[]", encoding="utf-8")
        self.assertEqual(host.save_persistent(self.root, "proof", {"a": 9}), {"a": 9})
        with self.assertRaises(ValueError):
            host.save_persistent(self.root, "proof", [1, 2])


if __name__ == "__main__":
    unittest.main()
