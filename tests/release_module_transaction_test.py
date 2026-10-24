"""Whole-package ownership and failed-commit regressions, temporary data only."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import modules_host as host

if os.environ.get("MEMUAI_AUDIT_BASELINE_ROOT"):
    import importlib.util
    source = Path(os.environ["MEMUAI_AUDIT_BASELINE_ROOT"]) / "app" / "modules_host.py"
    spec = importlib.util.spec_from_file_location("app._audit_baseline_modules_host", source)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)


class ModulePackageTransactions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="memuai-package-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        host.create_module(self.root, {"id":"proof", "title":"Old", "html":"old html",
            "css":"old css", "js":"old js", "i18n":{"en":{"hello":"Old"}}})
        self.folder = self.root / "custom" / "proof"
        (self.folder / "assets").mkdir()
        (self.folder / "assets" / "sentinel.bin").write_bytes(b"unknown asset")
        self.original = self.snapshot()

    def snapshot(self):
        return {path.relative_to(self.folder).as_posix():path.read_bytes()
                for path in self.folder.rglob("*") if path.is_file()}

    def test_manifest_staging_failure_leaves_whole_previous_package(self):
        with patch.object(host, "_write_json", side_effect=OSError("manifest write failed")):
            with self.assertRaises(OSError):
                host.update_code(self.root, "proof", {"html":"new html", "css":"new css",
                    "js":"new js", "i18n":{}, "title":"New"})
        self.assertEqual(self.snapshot(), self.original)

    def test_directory_commit_failure_rolls_back_all_files_and_unknown_assets(self):
        replace = os.replace
        failed = []
        def fail_commit(source, target):
            if Path(source).name.startswith(".proof.staging-") and Path(target) == self.folder:
                failed.append(1)
                raise PermissionError("publish failed")
            return replace(source, target)
        with patch.object(host.os, "replace", side_effect=fail_commit):
            with self.assertRaises(PermissionError):
                host.update_code(self.root, "proof", {"html":"new html", "i18n":{}, "title":"New"})
        self.assertEqual(failed, [1])
        self.assertEqual(self.snapshot(), self.original)
        self.assertFalse(list(self.folder.parent.glob(".proof.staging-*")))
        self.assertFalse(list(self.folder.parent.glob(".proof.previous-*")))

    def hard_exit_during_commit(self):
        code = '''import os, sys
from pathlib import Path
from app import modules_host as host
folder = Path(sys.argv[1]) / "custom" / "proof"
replace = host.os.replace
def exit_after_previous(source, target):
    replace(source, target)
    if Path(source) == folder and Path(target).name.startswith(".proof.previous-"):
        os._exit(17)
host.os.replace = exit_after_previous
host.update_code(Path(sys.argv[1]), "proof", {"html":"interrupted new html"})
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root)], capture_output=True,
                                text=True, timeout=8, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse(self.folder.exists())
        self.assertEqual(len(list(self.folder.parent.glob(".proof.previous-*"))), 1)

    def test_list_modules_recovers_whole_package_after_hard_exit_between_renames(self):
        self.hard_exit_during_commit()
        rows = host.list_modules(self.root)
        self.assertTrue(any(row["id"] == "proof" for row in rows))
        self.assertEqual(self.snapshot(), self.original)
        self.assertFalse(list(self.folder.parent.glob(".proof.previous-*")))

    def test_load_package_recovers_whole_package_after_hard_exit_between_renames(self):
        self.hard_exit_during_commit()
        self.assertEqual(host.load_package(self.root, "proof")["html"], "old html")
        self.assertEqual(self.snapshot(), self.original)

    def test_recovery_preserves_ambiguous_retained_packages(self):
        retained = []
        for suffix in ("0" * 32, "1" * 32):
            target = self.folder.parent / (".proof.previous-" + suffix)
            shutil.copytree(self.folder, target)
            retained.append(target)
        shutil.rmtree(self.folder)
        with self.assertRaises(KeyError):
            host.load_package(self.root, "proof")
        self.assertFalse(self.folder.exists())
        self.assertTrue(all(path.is_dir() for path in retained))

    @unittest.skipUnless(os.name == "nt", "native Windows sharing violation")
    def test_native_locked_style_keeps_entire_old_package(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        handle = create(str(self.folder / "style.css"), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            with self.assertRaises(PermissionError):
                host.update_code(self.root, "proof", {"html":"new html", "css":"new css", "js":"new js"})
        finally:
            close(handle)
        self.assertEqual(self.snapshot(), self.original)

    def test_success_preserves_assets_backup_history_and_updates_then_deletes_i18n(self):
        backups = set((self.folder / "backups").glob("manifest-*.json"))
        host.update_code(self.root, "proof", {"title":"New", "html":"new html",
            "i18n":{"en":{"hello":"New"}}})
        self.assertEqual(host.load_package(self.root, "proof")["i18n"], {"en":{"hello":"New"}})
        self.assertEqual((self.folder / "assets" / "sentinel.bin").read_bytes(), b"unknown asset")
        self.assertTrue(backups.issubset(set((self.folder / "backups").glob("manifest-*.json"))))
        latest = sorted((self.folder / "backups").glob("manifest-*.json"))[-1]
        self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["title"], "New")
        host.update_code(self.root, "proof", {"i18n":{}})
        self.assertFalse((self.folder / "i18n.json").exists())
        self.assertEqual(host.load_package(self.root, "proof")["i18n"], {})
        self.assertEqual((self.folder / "style.css").read_text(encoding="utf-8"), "old css")

    def test_parallel_partial_updates_preserve_both_changes(self):
        first_entered, release, second_started, second_read = (threading.Event() for _ in range(4))
        writer = host._write_package
        reader = host._text_file
        def block_first(folder, manifest, code):
            if code.get("html") == "new html" and code.get("css") == "old css":
                first_entered.set()
                self.assertTrue(release.wait(3))
            return writer(folder, manifest, code)
        def observe_second(folder, name):
            if threading.current_thread().name == "second-save":
                second_read.set()
            return reader(folder, name)
        def second():
            threading.current_thread().name = "second-save"
            second_started.set()
            return host.update_code(self.root, "proof", {"css":"new css"})
        with patch.object(host, "_write_package", side_effect=block_first), \
             patch.object(host, "_text_file", side_effect=observe_second), \
             ThreadPoolExecutor(2) as workers:
            first = workers.submit(host.update_code, self.root, "proof", {"html":"new html"})
            try:
                self.assertTrue(first_entered.wait(3))
                other = workers.submit(second)
                self.assertTrue(second_started.wait(3))
                if second_read.wait(.1):
                    other.result(timeout=3)
            finally:
                release.set()
            first.result(timeout=3)
            other.result(timeout=3)
        package = host.load_package(self.root, "proof")
        self.assertEqual((package["html"], package["css"]), ("new html", "new css"))

    def test_parallel_create_cannot_replace_winning_package(self):
        entered, release, attempted = (threading.Event() for _ in range(3))
        writer = host._write_package
        def block_first(folder, manifest, code):
            if code.get("html") == "first winner":
                entered.set()
                self.assertTrue(release.wait(3))
            return writer(folder, manifest, code)
        def other_create():
            attempted.set()
            return host.create_module(self.root, {"id":"new-module", "html":"second loser"})
        with patch.object(host, "_write_package", side_effect=block_first), ThreadPoolExecutor(2) as workers:
            first = workers.submit(host.create_module, self.root, {"id":"new-module", "html":"first winner"})
            try:
                self.assertTrue(entered.wait(3))
                other = workers.submit(other_create)
                self.assertTrue(attempted.wait(3))
                # The losing create may be blocked on the package transaction.
            finally:
                release.set()
            first.result(timeout=3)
            with self.assertRaises(ValueError):
                other.result(timeout=3)
        self.assertEqual(host.load_package(self.root, "new-module")["html"], "first winner")

    def test_persistent_save_during_directory_swap_keeps_declared_fields(self):
        host.update_code(self.root, "proof", {"persist":["a", "b"]})
        host.save_persistent(self.root, "proof", {"a":4, "b":7})
        moved, release, saving = (threading.Event() for _ in range(3))
        replace = os.replace
        def pause_publication(source, target):
            replace(source, target)
            if Path(source) == self.folder and Path(target).name.startswith(".proof.previous-"):
                moved.set()
                self.assertTrue(release.wait(3))
        def save():
            saving.set()
            return host.save_persistent(self.root, "proof", {"a":5})
        with patch.object(host.os, "replace", side_effect=pause_publication), ThreadPoolExecutor(2) as workers:
            update = workers.submit(host.update_code, self.root, "proof", {"html":"new html"})
            try:
                self.assertTrue(moved.wait(3))
                changed = workers.submit(save)
                self.assertTrue(saving.wait(3))
                try:
                    changed.result(timeout=.1)
                except TimeoutError:
                    pass
            finally:
                release.set()
            update.result(timeout=3)
            changed.result(timeout=3)
        self.assertEqual(host.load_persistent(self.root, "proof"), {"a":5, "b":7})

    def test_known_source_symlink_cannot_overwrite_external_sentinel(self):
        external = self.root / "external.html"
        external.write_text("external sentinel", encoding="utf-8")
        target = self.folder / "index.html"
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"file symlink unavailable: {exc}")
        with self.assertRaises(ValueError):
            host.update_code(self.root, "proof", {"html":"must stay inside"})
        self.assertEqual(external.read_text(encoding="utf-8"), "external sentinel")
        self.assertTrue(target.is_symlink())

    @unittest.skipUnless(os.name == "nt", "native junction")
    def test_unknown_asset_junction_cannot_escape_package_transaction(self):
        external = self.root / "external-assets"
        external.mkdir()
        (external / "sentinel.txt").write_text("external sentinel", encoding="utf-8")
        junction = self.folder / "linked-assets"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(external)],
                              capture_output=True, text=True, errors="replace",
                              creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(made.returncode, 0, made.stderr)
        try:
            with self.assertRaises(ValueError):
                host.update_code(self.root, "proof", {"html":"new html"})
            self.assertEqual((external / "sentinel.txt").read_text(encoding="utf-8"), "external sentinel")
            self.assertEqual((self.folder / "index.html").read_bytes(), self.original["index.html"])
        finally:
            junction.rmdir()


if __name__ == "__main__":
    unittest.main()
