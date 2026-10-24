"""Keeps the whole test run away from the real MemuAI data folder.

``python -m unittest discover -s tests`` does not import ``tests/__init__.py``:
the start folder becomes the top level, so its modules load as plain
``appearance_preferences_test`` and so on. The first of them that imported
``app`` used to bind ``app.config.DATA_DIR`` to the real ``%APPDATA%\\MemuAI``,
and tests then rewrote the person's preferences (a test agent replaced their
agents), rotated their account backups, and appended to metrics and logs.

Discovery imports test files in sorted order and ``_`` sorts before letters,
so this module runs first and fixes the folder before anything imports ``app``.
A single module run as ``python -m unittest tests.companion_test`` goes through
``tests/__init__.py``, which does the same.
"""
from __future__ import annotations

import os
import tempfile
import unittest

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import config  # noqa: E402


class DataIsolationTest(unittest.TestCase):
    def test_the_run_uses_a_temporary_data_folder(self) -> None:
        folder = os.path.normcase(os.path.abspath(config.DATA_DIR))
        self.assertEqual(folder, os.path.normcase(os.path.abspath(os.environ["MEMUAI_DATA_DIR"])))
        for name in ("APPDATA", "LOCALAPPDATA"):
            if os.environ.get(name):
                real = os.path.normcase(os.path.abspath(os.path.join(os.environ[name], "MemuAI")))
                self.assertNotEqual(folder, real, "tests must never run against the real MemuAI data")


if __name__ == "__main__":
    unittest.main()
