"""Round 29 — MemuNote in the MemuAI Setup: optional component, Packages\\MemuNote.zip when it is
not selected, kept by updates once MemuAI installed it, "Open with" undone on uninstall.

The Windows side is the fake platform of ``installer_engine_test``; the file system is real."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from installer import payload, products
from installer.engine import Engine, Options

from tests.installer_engine_test import STUB, FakePlatform, write_tree


def dist_with_memunote(base: Path, version: str) -> list:
    trees = {
        "proxy": {"MemuAIProxy.exe": b"proxy-" + version.encode() + os.urandom(500)},
        "memuai": {"MemuAI.exe": b"memuai-" + version.encode()},
        "memucode": {"MemuCode.exe": b"memucode-" + version.encode()},
        "memunote": {"MemuNote.exe": b"memunote-" + version.encode(),
                     "_internal/app/memunote/static/index.html": "<html>" + version + "</html>",
                     "_internal/app/memunote/static/js/app.js": "boot();\n" * 400},
    }
    sources = []
    for comp, files in trees.items():
        folder = base / version / comp
        write_tree(folder, files)
        (folder / "_internal" / "empty-dir").mkdir(parents=True, exist_ok=True)
        sources.append((comp, folder, products.SUITE.component(comp).dest))
    return sources


class MemuNoteSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.os = FakePlatform(self.base / "pc")
        self.app = self.base / "pc" / "Local" / "Programs" / "MemuAI"
        self.paks = []

    def tearDown(self) -> None:
        for pak in self.paks:
            pak.close()
        self.tmp.cleanup()

    def engine(self, version: str) -> Engine:
        stub = self.base / f"stub-{version}.exe"
        stub.write_bytes(STUB)
        setup = self.base / f"{version}-Setup.exe"
        payload.assemble(stub, dist_with_memunote(self.base, version), {"product": "memuai", "version": version},
                         setup, preset=1, workers=2, block_target=16 * 1024)
        pak = payload.Payload(setup)
        self.paks.append(pak)
        return Engine(products.SUITE, self.os, pak, close_wait=0.01)

    def install(self, version: str, components) -> dict:
        eng = self.engine(version)
        return eng.install(Options(str(self.app), list(components), ["autostart"], "ru"))

    def zip_path(self) -> Path:
        return self.app / "Packages" / "MemuNote.zip"

    def test_not_selected_ships_as_a_zip(self) -> None:
        result = self.install("3.1", ["proxy", "memuai", "memucode"])
        self.assertTrue(result["ok"], result)
        self.assertNotIn("memunote", result["components"])
        self.assertFalse((self.app / "MemuNote").exists())
        with zipfile.ZipFile(self.zip_path()) as archive:
            names = archive.namelist()
            meta = json.loads(archive.read("memunote-package.json"))
            self.assertEqual(archive.read("MemuNote.exe"), b"memunote-3.1")
        self.assertIn("_internal/app/memunote/static/js/app.js", names)
        self.assertFalse(any(n.startswith("MemuNote/") for n in names), "paths are relative to the MemuNote folder")
        self.assertEqual(meta["version"], "3.1")
        self.assertEqual(meta["exe"], "MemuNote.exe")
        start = self.base / "pc" / "StartMenu" / "MemuAI"
        self.assertFalse((start / "MemuNote.lnk").exists())

    def test_selected_installs_the_folder_and_shortcut(self) -> None:
        self.install("3.1", ["proxy", "memuai"])           # zip first …
        self.assertTrue(self.zip_path().is_file())
        result = self.install("3.1", ["proxy", "memuai", "memunote"])
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.app / "MemuNote" / "MemuNote.exe").is_file())
        self.assertFalse(self.zip_path().exists(), "a selected component leaves no stale package")
        link = json.loads((self.base / "pc" / "StartMenu" / "MemuAI" / "MemuNote.lnk").read_text(encoding="utf-8"))
        self.assertTrue(link["target"].endswith("MemuNote.exe"))
        self.assertEqual(link["app_id"], "MemuAI.MemuNote")

    def test_proxy_only_edition_gets_no_package(self) -> None:
        result = self.install("3.1", ["proxy", "memunote"])
        self.assertEqual(result["components"], ["proxy"])
        self.assertFalse(self.zip_path().exists())

    def test_update_keeps_memunote_installed_from_memuai_settings(self) -> None:
        self.install("3.1", ["proxy", "memuai"])
        # MemuAI → Settings → MemuNote unpacked the zip (app/memunote_manager.py).
        with zipfile.ZipFile(self.zip_path()) as archive:
            archive.extractall(self.app / "MemuNote")
        eng = self.engine("3.2")
        found = eng.detect()
        self.assertIn("memunote", found.components)
        defaults = eng.defaults(found)
        self.assertIn("memunote", defaults.components)
        result = eng.install(defaults)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.app / "MemuNote" / "MemuNote.exe").read_bytes(), b"memunote-3.2")

    def test_uninstall_unregisters_open_with_and_removes_packages(self) -> None:
        self.install("3.1", ["proxy", "memuai", "memunote"])
        self.install("3.1", ["proxy", "memuai", "memunote"])
        (self.app / "Packages").mkdir(exist_ok=True)
        (self.app / "Packages" / "MemuNote.zip").write_bytes(b"PK")
        eng = Engine(products.SUITE, self.os, None, close_wait=0.01)
        result = eng.uninstall(self.app)
        self.assertTrue(result["ok"], result)
        self.assertIn(("run", "MemuNote.exe", ("--unregister-open-with",)), self.os.calls)
        self.assertIn(("run", "MemuNote.exe", ("--shutdown",)), self.os.calls)
        self.assertFalse((self.app / "MemuNote").exists())
        self.assertFalse((self.app / "Packages").exists())


if __name__ == "__main__":
    unittest.main()
