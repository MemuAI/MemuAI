"""Round 26: chat saves must not stall the event loop or parse files twice."""
import json
import os
import tempfile
import unittest
from unittest import mock

from app import storage


class StorageSpeedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "chats.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_small_files_stay_pretty_large_ones_compact(self):
        storage._write_unlocked(self.path, {"a": [1, 2]})
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("\n  ", f.read())
        big = [{"id": str(i), "text": "привет " * 40} for i in range(2000)]
        storage._write_unlocked(self.path, big)
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("\n  ", raw)
        self.assertEqual(json.loads(raw), big)

    def test_repairing_mutation_parses_the_file_once(self):
        storage._write_unlocked(self.path, [{"id": "1"}])
        real = storage._load_primary
        calls = []

        def counting(path):
            calls.append(path)
            return real(path)

        with mock.patch.object(storage, "_load_primary", side_effect=counting):
            out = storage.mutate_json(self.path, [], lambda rows: (True, rows + [{"id": "2"}]),
                                      backup=False, validator=lambda v: isinstance(v, list),
                                      repair_invalid=True)
        self.assertEqual([r["id"] for r in out], ["1", "2"])
        self.assertEqual(len(calls), 1)

    def test_invalid_root_is_still_repaired(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        out = storage.mutate_json(self.path, [], lambda rows: False, backup=False,
                                  validator=lambda v: isinstance(v, list), repair_invalid=True)
        self.assertEqual(out, [])




class FreeVariantPriceTest(unittest.TestCase):
    def test_explicit_free_variant_costs_nothing(self):
        from app import pricing
        price = pricing.lookup("custom-qwen/qwen3.8-max:free", "custom")
        self.assertEqual((price.input, price.output), (0.0, 0.0))
        self.assertGreater(pricing.lookup("qwen/qwen3.8-max", "custom").input, 0)


if __name__ == "__main__":
    unittest.main()
