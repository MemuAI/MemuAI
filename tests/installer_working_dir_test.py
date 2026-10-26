"""Updater/Setup handoff must not lock a directory that Setup replaces."""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer import cli, relaunch

class WorkingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "installed"
        self.app.mkdir()
        self.safe = self.root / "temp"
        self.safe.mkdir()
        self.exe = self.safe / "Setup.exe"
        self.exe.write_bytes(b"MZ-fixture")

    def test_parent_and_child_restart_outside_installed_directory(self):
        args = cli.parse(["--silent", "--dir=next", "--log=setup.log", "--components=proxy,memuai", "--tasks="])
        with patch.object(Path, "cwd", return_value=self.app), \
             patch.object(relaunch.tempfile, "gettempdir", return_value=str(self.safe)), \
             patch.object(relaunch.subprocess, "Popen") as launch, \
             patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "old-runtime"}):
            self.assertTrue(relaunch.restart_outside_installation(self.exe, args, [self.app]))
        command = launch.call_args.args[0]
        self.assertEqual(command[0], str(self.exe))
        reparsed = cli.parse(command[1:])
        self.assertTrue(Path(reparsed.directory).is_absolute())
        self.assertTrue(Path(reparsed.log).is_absolute())
        self.assertEqual(reparsed.components, ["proxy", "memuai"])
        self.assertEqual(reparsed.tasks, [])
        self.assertEqual(launch.call_args.kwargs["cwd"], str(self.safe))
        env = launch.call_args.kwargs["env"]
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)

    def test_independent_replacement_does_not_relaunch_forever(self):
        with patch.object(Path, "cwd", return_value=self.safe), patch.object(relaunch.subprocess, "Popen") as launch:
            self.assertFalse(relaunch.restart_outside_installation(self.exe, cli.Args(), [self.app]))
            launch.assert_not_called()

    def test_executable_inside_installation_is_copied_before_relaunch(self):
        exe = self.app / "Setup.exe"
        exe.write_bytes(b"MZ-payload")
        with patch.object(Path, "cwd", return_value=self.safe), \
             patch.object(relaunch.tempfile, "gettempdir", return_value=str(self.safe)), \
             patch.object(relaunch.subprocess, "Popen") as launch:
            self.assertTrue(relaunch.restart_outside_installation(exe, cli.Args(), [self.app]))
        copied = Path(launch.call_args.args[0][0])
        self.assertNotEqual(copied, exe)
        self.assertEqual(copied.read_bytes(), exe.read_bytes())
        self.assertNotIn(self.app, copied.parents)

    def test_temp_inside_installation_is_rejected(self):
        with patch.object(Path, "cwd", return_value=self.app), \
             patch.object(relaunch.tempfile, "gettempdir", return_value=str(self.app)), \
             patch.object(relaunch.subprocess, "Popen") as launch:
            with self.assertRaises(OSError):
                relaunch.restart_outside_installation(self.exe, cli.Args(), [self.app])
            launch.assert_not_called()

    def test_cli_options_survive_a_restart(self):
        original = cli.parse(["/VERYSILENT", "/DIR=relative", "/COMPONENTS=proxy,memuai", "/TASKS=",
                              "/MERGETASKS=desktopicon,!autostart", "/TYPE=full", "/LANG=ru", "/LOG",
                              "--uninstall", "--app-dir=installed", "--remove-data", "--force-close",
                              "--product=memuai", "--relaunched", "--launch"])
        expected = asdict(original)
        for field in ("directory", "app_dir"):
            expected[field] = str(Path(expected[field]).resolve())
        actual = asdict(cli.parse(relaunch.absolute_arguments(original)))
        self.assertEqual(actual, expected)

    def test_nsis_dir_with_spaces_is_preserved(self):
        original = cli.parse(["/S", "/D=relative", "folder", "with spaces"])
        reparsed = cli.parse(relaunch.absolute_arguments(original))
        self.assertEqual(reparsed.directory, str(Path("relative folder with spaces").resolve()))

    def test_setup_restarts_before_mutex_ui_or_installation(self):
        from installer import main as setup_main
        from tests.installer_engine_test import FakePlatform, make_setup
        setup = make_setup(self.root, "3.8.2")
        platform = FakePlatform(self.root / "pc")
        target = self.root / "target"
        with patch.object(setup_main.sys, "frozen", True, create=True), \
             patch.object(setup_main.sys, "executable", str(setup)), \
             patch.object(setup_main, "make_platform", return_value=platform), \
             patch.object(setup_main, "setup_logging", return_value=""), \
             patch.object(relaunch, "restart_outside_installation", return_value=True) as restart, \
             patch.object(setup_main, "interactive_install") as install:
            self.assertEqual(setup_main.main(["--dir=" + str(target)]), 0)
        self.assertEqual(restart.call_args.args[2], [target])
        install.assert_not_called()
        self.assertEqual(platform.registry, {})
        self.assertFalse(target.exists())

    def test_external_directory_users_are_not_force_terminated(self):
        from installer.engine import Engine, SetupError
        from installer import products
        from tests.installer_engine_test import FakePlatform
        platform = FakePlatform(self.root / "pc")
        platform.directory_users = lambda folder: [(12345, "Notepad.exe")]
        engine = Engine(products.SUITE, platform)
        with self.assertRaises(SetupError) as error:
            engine.close_apps(self.app, force=True, wait=0)
        self.assertEqual(error.exception.code, "directory_in_use")
        self.assertIn("Notepad.exe", str(error.exception))
        self.assertFalse(any(call[0] == "terminate" for call in platform.calls))

    @unittest.skipUnless(os.name == "nt", "Windows log viewer")
    def test_log_viewer_does_not_inherit_an_application_directory(self):
        from installer.bridge import Bridge
        path = self.root / "setup.log"
        path.write_text("fixture", encoding="utf-8")
        bridge = Bridge(None, "install", "en", str(path))
        with patch("installer.bridge.subprocess.Popen") as launch:
            self.assertTrue(bridge.open_log())
        self.assertEqual(launch.call_args.kwargs["cwd"], tempfile.gettempdir())
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

if __name__ == "__main__":
    unittest.main()
