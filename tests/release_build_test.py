"""A release cache must reflect file contents and its actual Python runtime."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import build_freeze


class BuildCacheRegressionTest(unittest.TestCase):
    def test_launcher_archive_separation_changes_only_the_exe_option(self):
        original = "a = Analysis([])\nexe = EXE(\n    pyz,\n    exclude_binaries=True,\n    upx=False,\n)\ncoll = COLLECT(exe)\n"
        result = build_freeze.external_archive_spec(original)
        self.assertEqual(result.replace("    append_pkg=False,\n", ""), original)
        self.assertTrue(build.TARGETS["memuai"]["external_pkg"])
        for unsupported in ("exe = EXE(pyz)", original + "\n    exclude_binaries=True,\n"):
            with self.assertRaises(ValueError):
                build_freeze.external_archive_spec(unsupported)
    def test_same_length_edit_with_preserved_timestamp_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(build, "ROOT", Path(temp)):
            path = Path(temp) / "entry.py"
            path.write_bytes(b"print('old')")
            stamp = path.stat()
            first = build.file_hash([path])
            path.write_bytes(b"print('new')")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertNotEqual(build.file_hash([path]), first)

    def test_changed_runtime_or_dependency_set_invalidates_executable_stamp(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(build, "ROOT", Path(temp)), \
             patch.object(build, "INPUT_GLOBS", ["*.py"]):
            (Path(temp) / "entry.py").write_text("pass", encoding="utf-8")
            with patch.object(build, "runtime_fingerprint", return_value="python-old + packages-one"):
                first = build.inputs_hash("proxy-2.0")
            for fingerprint in ("python-new + packages-one", "python-old + packages-two"):
                with self.subTest(fingerprint=fingerprint), \
                     patch.object(build, "runtime_fingerprint", return_value=fingerprint):
                    self.assertNotEqual(build.inputs_hash("proxy-2.0"), first)


if __name__ == "__main__":
    unittest.main()
