"""Round 38 (r38-files): «MemuFiles по умолчанию» — backup → enable → rollback restores the registry exactly.

The registry is a fake HKCU (case-insensitive, like Windows); nothing touches a real registry or the network.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.memufiles import default_shell as ds  # noqa: E402
from app.memufiles import winkey  # noqa: E402

class FakeReg:
    """HKCU in a dict: {lower key: {lower name: (name, type, data)}}; keys exist only when created."""

    def __init__(self):
        self.keys = {}
        self.fail_after = None
        self.notified = 0

    def _parents(self, path):
        parts = path.split("\\")
        return ["\\".join(parts[:n]) for n in range(1, len(parts) + 1)]

    def get(self, path, name):
        row = self.keys.get(path.lower(), {}).get(name.lower())
        return (row[1], row[2]) if row else None

    def key_exists(self, path):
        return path.lower() in self.keys

    def set(self, path, name, kind, data):
        if self.fail_after is not None:
            if self.fail_after <= 0:
                raise PermissionError("access denied")
            self.fail_after -= 1
        for parent in self._parents(path):
            self.keys.setdefault(parent.lower(), {})
        self.keys[path.lower()][name.lower()] = (name, kind, data)

    def delete_value(self, path, name):
        self.keys.get(path.lower(), {}).pop(name.lower(), None)

    def is_empty(self, path):
        low = path.lower()
        return not self.keys.get(low) and not any(k.startswith(low + "\\") for k in self.keys)

    def delete_tree(self, path):
        low = path.lower()
        for key in [k for k in self.keys if k == low or k.startswith(low + "\\")]:
            del self.keys[key]

    def notify(self):
        self.notified += 1

    def snapshot(self):
        return json.dumps(self.keys, sort_keys=True)

BASE = ["software", "software\\classes", "software\\classes\\clsid"]

def clean_reg():
    reg = FakeReg()
    for key in BASE:
        reg.keys[key] = {}
    return reg

class DefaultShellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r38-files-")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": os.path.join(self.tmp, "data")})
        self.env.start()
        self.exe = os.path.join(self.tmp, "Программы", "MemuFiles", "MemuFiles.exe")
        os.makedirs(os.path.dirname(self.exe))
        Path(self.exe).write_bytes(b"MZ")
        self.reg = clean_reg()
        ds.set_registry(self.reg)
        self.exe_patch = mock.patch.object(ds, "current_exe", lambda: self.exe if os.path.isfile(self.exe) else None)
        self.exe_patch.start()

    def tearDown(self):
        self.exe_patch.stop()
        ds.set_registry(None)
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_registry_enable_both_then_restore_is_exact(self):
        before = self.reg.snapshot()
        ds.enable("folders")
        ds.enable("win_e")
        st = ds.status()
        self.assertEqual(st["state"], "on")
        cmd = self.reg.get(r"Software\Classes\Folder\shell\open\command", "")
        self.assertEqual(cmd[0], ds.REG_EXPAND_SZ)
        self.assertIn("wscript.exe", cmd[1])
        self.assertTrue(cmd[1].endswith('"%1"'))
        self.assertEqual(self.reg.get(r"Software\Classes\Folder\shell\open\command", "DelegateExecute"), (ds.REG_SZ, ""))
        self.assertEqual(self.reg.get(r"Software\Classes\Directory\shell", "")[1], "MemuFiles")
        self.assertEqual(self.reg.get(r"Software\Classes\Drive\shell", "")[1], "MemuFiles")
        win_e = r"Software\Classes\CLSID\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}\shell\opennewwindow\command"
        self.assertNotIn("%1", self.reg.get(win_e, "")[1])
        self.assertEqual(self.reg.get(win_e, "DelegateExecute"), (ds.REG_SZ, ""))
        self.assertIsNone(self.reg.get(r"Software\Classes\Folder\shell\explore\command", ""),
                          "the explore verb must stay Explorer's — it is the loop-free way back")
        self.assertTrue(ds.launcher_path().is_file())
        self.assertTrue(ds.emergency_reg_path().is_file())
        result = ds.restore_all()
        self.assertEqual(result["problems"], [])
        self.assertEqual(self.reg.snapshot(), before)
        self.assertEqual(ds.status()["state"], "off")
        self.assertFalse(ds.launcher_path().exists())
        self.assertFalse((ds.shell_dir() / "state.json").exists())
        self.assertGreater(self.reg.notified, 0)

    def test_existing_values_are_restored_with_types_and_foreign_keys_survive(self):
        self.reg.set(r"Software\Classes\Directory\shell", "", ds.REG_SZ, "OtherVerb")
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "", ds.REG_EXPAND_SZ, r"%SystemRoot%\other.exe %1")
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "DelegateExecute", ds.REG_SZ, "{11dbb47c-a525-400b-9e80-a54615a090c0}")
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "Extra", ds.REG_DWORD, 7)
        before = self.reg.snapshot()
        ds.enable("folders")

        self.reg.set(r"Software\Classes\Directory\shell\VSCode\command", "", ds.REG_SZ, "code.exe %1")
        ds.disable("folders")
        self.reg.delete_tree(r"Software\Classes\Directory\shell\VSCode")
        self.assertEqual(self.reg.snapshot(), before)
        self.assertEqual(self.reg.get(r"Software\Classes\Folder\shell\open\command", ""), (ds.REG_EXPAND_SZ, r"%SystemRoot%\other.exe %1"))

    def test_foreign_verb_added_meanwhile_is_kept(self):
        ds.enable("folders")
        self.reg.set(r"Software\Classes\Directory\shell\VSCode\command", "", ds.REG_SZ, "code.exe %1")
        ds.restore_all()
        self.assertEqual(self.reg.get(r"Software\Classes\Directory\shell\VSCode\command", ""), (ds.REG_SZ, "code.exe %1"))
        self.assertIsNone(self.reg.get(r"Software\Classes\Directory\shell", ""))
        self.assertFalse(self.reg.key_exists(r"Software\Classes\Directory\shell\MemuFiles"))

    def test_second_enable_keeps_the_first_backup(self):
        self.reg.set(r"Software\Classes\Directory\shell", "", ds.REG_SZ, "OtherVerb")
        before = self.reg.snapshot()
        ds.enable("folders")
        ds.enable("folders")
        state = ds.load_state()
        row = [v for v in state["features"]["folders"]["backup"]["values"]
               if v["key"].endswith("Directory\\shell") and v["name"] == ""][0]
        self.assertEqual(row["data"], "OtherVerb")
        ds.restore_all()
        self.assertEqual(self.reg.snapshot(), before)

    def test_features_are_independent(self):
        before = self.reg.snapshot()
        ds.enable("folders")
        ds.enable("win_e")
        ds.disable("win_e")
        st = ds.status()
        self.assertEqual(st["features"]["folders"]["state"], "on")
        self.assertEqual(st["features"]["win_e"]["state"], "off")
        self.assertEqual(st["state"], "partial")
        self.assertFalse(self.reg.key_exists(r"Software\Classes\CLSID\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}"))
        ds.disable("folders")
        self.assertEqual(self.reg.snapshot(), before)

    def test_partial_when_another_program_took_over(self):
        ds.enable("folders")
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "", ds.REG_SZ, "files.exe %1")
        self.assertEqual(ds.status()["features"]["folders"]["state"], "partial")

    def test_registry_failure_rolls_back(self):
        before = self.reg.snapshot()
        self.reg.fail_after = 3
        with self.assertRaises(ds.ShellError):
            ds.enable("folders")
        self.reg.fail_after = None
        self.assertEqual(self.reg.snapshot(), before)
        self.assertEqual(ds.status()["state"], "off")

    def test_lost_backup_restore_removes_only_ours(self):
        ds.enable("folders")
        ds.enable("win_e")
        (ds.shell_dir() / "state.json").unlink()
        self.reg.set(r"Software\Classes\Drive\shell\Foreign", "", ds.REG_SZ, "x")
        ds.restore_all()
        self.assertEqual(ds.status()["state"], "off")
        self.assertIsNone(self.reg.get(r"Software\Classes\Folder\shell\open\command", ""))
        self.assertIsNone(self.reg.get(r"Software\Classes\Folder\shell\open\command", "DelegateExecute"))
        self.assertFalse(self.reg.key_exists(r"Software\Classes\Folder\shell\open\command"))
        self.assertEqual(self.reg.get(r"Software\Classes\Drive\shell\Foreign", ""), (ds.REG_SZ, "x"))

    def test_lost_backup_then_enable_records_absent_original(self):
        ds.enable("folders")
        (ds.shell_dir() / "state.json").unlink()
        ds.enable("folders")
        ds.restore_all()
        self.assertIsNone(self.reg.get(r"Software\Classes\Folder\shell\open\command", ""))

    def test_foreign_values_untouched_without_backup(self):
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "", ds.REG_SZ, "files.exe %1")
        self.reg.set(r"Software\Classes\Folder\shell\open\command", "DelegateExecute", ds.REG_SZ, "")
        before = self.reg.snapshot()
        ds.restore_all()
        self.assertEqual(self.reg.snapshot(), before)

    def test_removed_exe_still_restorable_and_launcher_falls_back(self):
        before = self.reg.snapshot()
        ds.enable("folders")
        os.remove(self.exe)
        st = ds.status()
        self.assertFalse(st["exe_exists"])
        self.assertTrue(st["can_restore"])
        source = ds.launcher_path().read_bytes()
        self.assertTrue(source.startswith(b"\xff\xfe"))
        text = source[2:].decode("utf-16-le")
        self.assertIn("FileExists(EXE)", text)
        self.assertIn('"explore"', text)
        self.assertIn("Программы", text)
        self.assertEqual(ds.restore_all()["problems"], [])
        self.assertEqual(self.reg.snapshot(), before)

    def test_enable_needs_installed_exe(self):
        os.remove(self.exe)
        with self.assertRaises(ds.ShellError) as ctx:
            ds.enable("folders")
        self.assertEqual(ctx.exception.code, "no_exe")
        self.assertEqual(self.reg.snapshot(), clean_reg().snapshot())

    def test_heal_repoints_moved_exe(self):
        ds.enable("folders")
        new_exe = os.path.join(self.tmp, "Moved", "MemuFiles.exe")
        os.makedirs(os.path.dirname(new_exe))
        Path(new_exe).write_bytes(b"MZ")
        self.assertTrue(ds.heal(new_exe))
        self.assertIn("Moved", ds.launcher_path().read_bytes()[2:].decode("utf-16-le"))
        self.assertIn("Moved", self.reg.get(r"Software\Classes\Directory\shell\MemuFiles", "Icon")[1])
        self.assertFalse(ds.heal(new_exe))

    def test_direct_mode_when_script_host_disabled(self):
        self.reg.set(ds.WSH_KEY, "Enabled", ds.REG_SZ, "0")
        ds.enable("folders")
        cmd = self.reg.get(r"Software\Classes\Folder\shell\open\command", "")[1]
        self.assertIn("--shell-open", cmd)
        self.assertNotIn("wscript", cmd)
        self.assertFalse(ds.launcher_path().exists())

    def test_emergency_reg_file(self):
        self.reg.set(r"Software\Classes\Directory\shell", "", ds.REG_SZ, 'Other "verb"')
        ds.enable("folders")
        ds.enable("win_e")
        raw = ds.emergency_reg_path().read_bytes()
        self.assertTrue(raw.startswith(b"\xff\xfe"))
        text = raw[2:].decode("utf-16-le")
        self.assertTrue(text.startswith("Windows Registry Editor Version 5.00"))
        self.assertIn(r"[-HKEY_CURRENT_USER\Software\Classes\Directory\shell\MemuFiles]", text)
        self.assertIn(r"[-HKEY_CURRENT_USER\Software\Classes\CLSID\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}]", text)
        self.assertIn('@="Other \\"verb\\""', text)
        self.assertIn('"DelegateExecute"=-', text)
        generic = ds.emergency_reg_text(None)
        self.assertIn(r"[-HKEY_CURRENT_USER\Software\Classes\Drive\shell\MemuFiles]", generic)

    def test_handle_requires_confirmation_and_validates(self):
        self.assertEqual(ds.handle({"action": "enable", "feature": "folders"})["error"], "not_confirmed")
        self.assertEqual(ds.status()["state"], "off")
        self.assertEqual(ds.handle({"action": "enable", "feature": "nope", "confirmed": True})["error"], "bad_feature")
        self.assertEqual(ds.handle({"action": "zzz"})["error"], "bad_action")
        self.assertEqual(ds.handle({"action": "enable", "feature": "win_e", "confirmed": True})["state"], "partial")
        self.assertTrue(ds.handle({"action": "hook", "on": True})["hook"])
        self.assertEqual(ds.handle({"action": "restore"})["state"], "off")
        self.assertFalse(ds.status()["hook"], "restore also turns the Win+E fallback off")

class RouteTest(unittest.TestCase):
    def test_special_paths_go_to_explorer(self):
        for raw in ("::{645FF040-5081-101B-9F08-00AA002F954E}", "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\::{21EC2020-3AEA-1069-A2DD-08002B30309D}",
                    "shell:RecycleBinFolder", "Shell:Downloads", r"\\server", "/no/such/folder"):
            self.assertEqual(ds.route(raw)[0], "explorer", raw)

    def test_real_folders_open_in_memufiles(self):
        tmp = tempfile.mkdtemp(prefix="r38-route-")
        try:
            cyr = os.path.join(tmp, "Документы и фото")
            deep = cyr
            while len(deep) < 300:
                deep = os.path.join(deep, "длинная папка")
            os.makedirs(deep)
            self.assertEqual(ds.route(cyr), ("open", cyr))
            self.assertEqual(ds.route('"' + cyr + '"'), ("open", cyr))
            self.assertEqual(ds.route(deep), ("open", deep))
            self.assertEqual(ds.route(""), ("open", ""))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_drive_root_quote_is_repaired(self):
        with mock.patch.object(ds.os.path, "isdir", lambda p: p == "C:\\"):
            self.assertEqual(ds.route('C:"'), ("open", "C:\\"))
            self.assertEqual(ds.route("C:"), ("open", "C:\\"))

    def test_desktop_shell_open(self):
        from app.memufiles import desktop
        args = desktop._parse_args(["--shell-open", "::{645FF040-5081-101B-9F08-00AA002F954E}"])
        self.assertEqual(args["paths"], [])
        with mock.patch.object(ds, "explore") as explore:
            self.assertIsNone(desktop._shell_open(args["raw"]))
            explore.assert_called_once()
        self.assertEqual(desktop._shell_open([]), [os.path.expanduser("~")])

class LauncherSyntaxTest(unittest.TestCase):
    def test_launcher_is_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.js")
            Path(path).write_text(ds.launcher_source("C:\\Users\\Анна\\MemuFiles.exe"), encoding="utf-8")
            self.assertEqual(subprocess.run([node, "--check", path], capture_output=True).returncode, 0)

class UnavailableTest(unittest.TestCase):
    def test_linux_is_unavailable_and_never_raises(self):
        if os.name == "nt":
            self.skipTest("real Windows")
        ds.set_registry(None)
        st = ds.status()
        self.assertFalse(st["available"])
        self.assertEqual(st["state"], "unavailable")
        self.assertEqual(ds.handle({"action": "enable", "feature": "folders", "confirmed": True})["error"], "unavailable")
        self.assertFalse(ds.handle({"action": "restore"})["available"])
        self.assertFalse(ds.heal("/x/MemuFiles.exe"))
        self.assertEqual(ds.cli_restore(), 0)

    def test_memuai_route(self):
        if os.name == "nt":
            self.skipTest("real Windows")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import memufiles_manager
        app = FastAPI()
        memufiles_manager.register(app)
        client = TestClient(app)
        self.assertEqual(client.get("/api/pg/memufiles/default-shell").json()["state"], "unavailable")
        self.assertEqual(client.post("/api/pg/memufiles/default-shell", json={"action": "enable", "feature": "folders",
                                                                               "confirmed": True}).json()["error"], "unavailable")

class WinKeyTest(unittest.TestCase):
    def test_chord(self):
        held = set()
        down = lambda vk: vk in held  # noqa: E731
        held.add(winkey.VK_LWIN)
        self.assertTrue(winkey.is_chord(winkey.VK_E, winkey.WM_KEYDOWN, down))
        self.assertFalse(winkey.is_chord(winkey.VK_E, winkey.WM_KEYDOWN, down, injected=True))
        self.assertFalse(winkey.is_chord(0x46, winkey.WM_KEYDOWN, down))
        held.add(winkey.VK_SHIFT)
        self.assertFalse(winkey.is_chord(winkey.VK_E, winkey.WM_KEYDOWN, down))
        held.clear()
        self.assertFalse(winkey.is_chord(winkey.VK_E, winkey.WM_KEYDOWN, down))

class WiringTest(unittest.TestCase):
    def test_uninstall_paths_restore_explorer(self):
        from installer.products import PRODUCTS
        found = [p for p in (PRODUCTS.values() if isinstance(PRODUCTS, dict) else PRODUCTS)
                 if ("memufiles", r"MemuFiles\MemuFiles.exe", "--restore-explorer") in p.pre_uninstall]
        self.assertTrue(found)
        src = (ROOT / "app" / "memufiles_manager.py").read_text(encoding="utf-8")
        body = src[src.index("def uninstall("):src.index("def _restore_explorer")]
        self.assertLess(body.index("_restore_explorer()"), body.index("_remove_tree(folder)"))
        desk = (ROOT / "app" / "memufiles" / "desktop.py").read_text(encoding="utf-8")
        self.assertLess(desk.index('"--restore-explorer" in flags'), desk.index("hand_off(args[\"paths\"]"))

if __name__ == "__main__":
    unittest.main()
