"""Shared benchmark code remains editable and self-contained after export."""
from pathlib import Path
import tempfile
import unittest

from app import modules_host


class SharedBenchmarkPackageTest(unittest.TestCase):
    def test_builtin_export_import_and_edit_preserve_shared_code_once(self):
        with tempfile.TemporaryDirectory(prefix="memuai-shared-data-") as temporary:
            root = Path(temporary)
            for identity in ("catalog", "benchmarks"):
                with self.subTest(module=identity):
                    original = modules_host.load_package(root, identity)
                    self.assertEqual(original["version"], "1.0")
                    self.assertEqual(original["js"].count("/* @memu-inlined:benchmarks */"), 1)
                    self.assertIn("MemuBenchmarkData", original["js"])
                    exported = modules_host.export_package(root, identity)
                    self.assertEqual(exported["files"]["main.js"], original["js"])
                    copied_id = "copy-" + identity
                    modules_host.import_package(root, exported, new_id=copied_id)
                    copied = modules_host.load_package(root, copied_id)
                    self.assertEqual(copied["js"], original["js"])
                    modules_host.update_code(root, copied_id, {"js": copied["js"] + "\n// user edit"})
                    edited = modules_host.load_package(root, copied_id)
                    self.assertEqual(edited["js"].count("/* @memu-inlined:benchmarks */"), 1)
                    self.assertTrue(edited["js"].endswith("// user edit"))
                    document = modules_host.module_document(edited)
                    self.assertEqual(document.count("/* @memu-inlined:benchmarks */"), 1)
                    self.assertIn("connect-src 'none'", document)

    def test_unrelated_module_keeps_its_authored_javascript(self):
        with tempfile.TemporaryDirectory(prefix="memuai-unshared-data-") as temporary:
            root = Path(temporary)
            modules_host.create_module(root, {"id": "plain", "title": "Plain", "js": "const example = 1;", "permissions": []})
            self.assertEqual(modules_host.load_package(root, "plain")["js"], "const example = 1;")


if __name__ == "__main__":
    unittest.main()
