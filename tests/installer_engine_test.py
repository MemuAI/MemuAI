"""MemuAI Setup engine: payload container, install / upgrade / uninstall, silent mode.

Everything runs on any OS: Windows itself (registry, shortcuts, processes) is a
fake platform, the file system work is real (temporary folders).
"""
from __future__ import annotations

import io
import json
import os
import re
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer import cli, engine, i18n, main as setup_main, payload, products
from installer.engine import AppsRunning, Cancelled, Engine, Options, SetupError

ROOT = Path(__file__).resolve().parents[1]
STUB = b"MZ" + b"\0" * 58 + struct.pack("<I", 64) + b"PE\0\0" + b"\0" * 20 + struct.pack("<H", 0x20B) + b"\0" * 400 + b"STUB-END"


class FakePlatform(engine.Platform):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.registry: dict = {}
        self.processes: list = []
        self.calls: list = []
        self.launched: list = []
        self.webview = "130.0.0.0"
        self.envs = {"LOCALAPPDATA": str(root / "Local"), "APPDATA": str(root / "Roaming"),
                     "USERPROFILE": str(root / "home"), "TEMP": str(root / "Temp")}
        for value in self.envs.values():
            os.makedirs(value, exist_ok=True)

    def env(self, name):
        return self.envs.get(name, "")

    def known_folder(self, name):
        path = self.root / ("StartMenu" if name == "programs" else "Desktop")
        path.mkdir(exist_ok=True)
        return path

    def reg_get(self, root, key, name):
        return self.registry.get((root, key), {}).get(name)

    def reg_values(self, root, key):
        return dict(self.registry.get((root, key), {}))

    def reg_set(self, root, key, name, value, kind="sz"):
        self.registry.setdefault((root, key), {})[name] = value

    def reg_delete_value(self, root, key, name):
        self.registry.get((root, key), {}).pop(name, None)

    def reg_delete_key(self, root, key):
        self.registry.pop((root, key), None)

    def create_shortcut(self, link, target, args="", workdir="", icon="", description="", app_id=""):
        Path(link).write_text(json.dumps({"target": str(target), "args": args, "workdir": workdir,
                                          "app_id": app_id}), encoding="utf-8")

    def processes_in(self, folder):
        return list(self.processes)

    def terminate(self, pid):
        self.calls.append(("terminate", pid))
        self.processes = [p for p in self.processes if p[0] != pid]

    def run_wait(self, exe, args, timeout):
        self.calls.append(("run", Path(exe).name, tuple(args)))
        return 0

    def launch(self, exe, args, cwd=None):
        self.launched.append((Path(exe).name, tuple(args)))

    def broadcast_environment(self):
        self.calls.append(("broadcast",))

    def disk_free(self, path):
        return 500 * 1024 ** 3

    def webview2_version(self):
        return self.webview

    def sleep(self, seconds):
        pass


def write_tree(base: Path, files: dict) -> None:
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def suite_dist(base: Path, version: str, extra: dict | None = None) -> list:
    """Fake PyInstaller output folders for the three suite programs."""
    trees = {
        "proxy": {"MemuAIProxy.exe": b"proxy-exe-" + version.encode() + os.urandom(3000),
                  "_internal/python313.dll": os.urandom(20000), "_internal/app/static/app.js": "console.log(1)\n" * 300},
        "memuai": {"MemuAI.exe": b"memuai-exe-" + version.encode(), "MemuAI.pkg": os.urandom(5000),
                   "_internal/app/pgstatic/index.html": "<html>" + version + "</html>",
                   "_internal/deep/" + "/".join(["level"] * 12) + "/data.bin": os.urandom(700)},
        "memucode": {"MemuCode.exe": b"memucode-" + version.encode(), "_internal/rich/__init__.pyc": os.urandom(800)},
    }
    for comp, files in (extra or {}).items():
        trees[comp].update(files)
    sources = []
    for comp, files in trees.items():
        folder = base / version / comp
        write_tree(folder, files)
        (folder / "_internal" / "empty-dir").mkdir(parents=True, exist_ok=True)
        dest = products.SUITE.component(comp).dest
        sources.append((comp, folder, dest))
    return sources


def make_setup(base: Path, version: str, product=products.SUITE, sources=None, name="Setup.exe") -> Path:
    stub = base / f"stub-{version}.exe"
    stub.write_bytes(STUB)
    if sources is None:
        sources = suite_dist(base, version)
    out = base / f"{version}-{name}"
    payload.assemble(stub, sources, {"product": product.id, "version": version}, out, preset=1, workers=2,
                     block_target=16 * 1024)
    return out


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_verifies_every_file_and_keeps_the_stub(self):
        setup = make_setup(self.base, "2.1")
        with payload.Payload(setup) as pak:
            self.assertEqual(pak.read_stub(), STUB)
            self.assertEqual(pak.meta["version"], "2.1")
            self.assertEqual(set(pak.index.components()), {"proxy", "memuai", "memucode"})
            self.assertEqual(pak.index.top_entries("memuai"), ["MemuAI"])
            seen = {}
            for entry, chunks in pak.iter_files(["proxy", "memuai", "memucode"]):
                seen[entry.path] = b"".join(chunks)
            source = self.base / "2.1" / "memuai" / "MemuAI.pkg"
            self.assertEqual(seen["MemuAI/MemuAI.pkg"], source.read_bytes())
            self.assertIn(("memuai", "MemuAI/_internal/empty-dir"), pak.index.dirs)
            self.assertGreater(len(pak.index.blocks), 3, "small block target -> several solid blocks")
            self.assertTrue(any(b.filters == "x86" for b in pak.index.blocks))

    def test_damaged_block_is_reported_not_installed(self):
        setup = make_setup(self.base, "2.1")
        data = bytearray(setup.read_bytes())
        with payload.Payload(setup) as pak:
            block = pak.index.blocks[1]
            offset = pak.loc.payload_start + block.offset + block.csize // 2
        data[offset] ^= 0xFF
        setup.write_bytes(bytes(data))
        with payload.Payload(setup) as pak, self.assertRaises(payload.PayloadError):
            for _entry, chunks in pak.iter_files(pak.index.components()):
                for _ in chunks:
                    pass

    def test_trailer_is_found_behind_an_appended_signature(self):
        setup = make_setup(self.base, "2.1")
        with setup.open("ab") as handle:
            handle.write(b"\x30\x82" + os.urandom(9000))      # an Authenticode table lands here
        with payload.Payload(setup) as pak:
            self.assertEqual(pak.read_stub(), STUB)
            self.assertEqual(len(list(pak.iter_files(["memucode"]))), 2)

    def test_file_without_payload_and_truncated_payload(self):
        plain = self.base / "plain.exe"
        plain.write_bytes(STUB)
        self.assertIsNone(payload.Payload.open_optional(plain))
        setup = make_setup(self.base, "2.1")
        data = setup.read_bytes()
        cut = self.base / "cut.exe"
        cut.write_bytes(data[:len(STUB)] + data[-40:])    # trailer survives, blocks are gone
        with self.assertRaises(payload.PayloadError):
            payload.Payload(cut)

    def test_unsafe_paths_and_cookie_guard(self):
        for bad in ("../x", "/abs", "C:/x", "a/../../b", "a//b"):
            with self.assertRaises(payload.PayloadError):
                payload.safe_relpath(bad)
        self.assertEqual(payload.safe_relpath("a\\b\\c.dll"), "a/b/c.dll")
        setup = make_setup(self.base, "2.1")
        with payload.Payload(setup) as pak:
            region = setup.read_bytes()[pak.loc.payload_start:]
        self.assertNotIn(payload.PYI_COOKIE_MAGIC, region)

    def test_signature_directory_pointing_past_the_stub_is_cleared(self):
        stub = bytearray(STUB)
        security = 64 + 24 + 112 + 4 * 8
        struct.pack_into("<II", stub, security, len(stub) + 100, 4096)
        cleaned = payload.strip_signature_directory(bytes(stub))
        self.assertEqual(struct.unpack_from("<II", cleaned, security), (0, 0))
        struct.pack_into("<II", stub, security, 10, 20)       # inside the stub: untouched
        self.assertEqual(payload.strip_signature_directory(bytes(stub)), bytes(stub))


    def test_payload_goes_before_a_pyinstaller_archive(self):
        archive = b"CARCHIVE" + os.urandom(3000)
        cookie = payload.PYI_COOKIE.pack(payload.PYI_COOKIE_MAGIC, len(archive) + payload.PYI_COOKIE.size,
                                         0, 0, 313, b"python313.dll")
        stub = STUB + archive + cookie
        self.assertEqual(payload.pyinstaller_archive_offset(stub), len(STUB))
        (self.base / "pyi.exe").write_bytes(stub)
        out = self.base / "pyi-setup.exe"
        payload.assemble(self.base / "pyi.exe", suite_dist(self.base, "3.0"), {"product": "memuai", "version": "3.0"},
                         out, preset=1, workers=2)
        data = out.read_bytes()
        self.assertTrue(data.startswith(STUB))
        self.assertTrue(data.endswith(archive + cookie), "the bootloader still finds its cookie at the very end")
        with out.open("ab") as handle:
            handle.write(os.urandom(5000))                   # signature table
        with payload.Payload(out) as pak:
            self.assertGreater(pak.loc.archive_end, 0)
            self.assertEqual(pak.read_stub(), stub, "uninstaller = stub without payload and signature")
            files = {e.path: b"".join(c) for e, c in pak.iter_files(["memucode"])}
            self.assertEqual(files["MemuCode/MemuCode.exe"], b"memucode-3.0")


class VersionTest(unittest.TestCase):
    def test_compare(self):
        cmp = engine.compare_versions
        self.assertEqual(cmp("2.0", "2.0.0.0"), 0)
        self.assertEqual(cmp("2.1", "2.0"), 1)
        self.assertEqual(cmp("1.200", "2.0"), -1)
        self.assertEqual(cmp("1.200", "1.100"), 1)
        self.assertEqual(cmp("2.1-beta", "2.1"), -1)
        self.assertEqual(cmp("v2.10", "2.9"), 1)
        self.assertEqual(cmp("", "0"), 0)

    def test_close_wait_is_configurable(self):
        self.assertEqual(Engine(products.SUITE, engine.Platform()).close_wait, engine.DEFAULT_CLOSE_WAIT)
        self.assertEqual(Engine(products.SUITE, engine.Platform(), close_wait=0.2).close_wait, 0.2)
        self.assertEqual(Engine(products.SUITE, engine.Platform(), close_wait=-3).close_wait, 0.0)

    def test_path_entries(self):
        self.assertEqual(engine.add_path_entry("C:\\a;C:\\b", "C:\\B\\"), "C:\\a;C:\\b")
        self.assertEqual(engine.add_path_entry("", "C:\\x"), "C:\\x")
        self.assertEqual(engine.remove_path_entry("C:\\a;c:\\X\\;;C:\\b", "C:\\x"), "C:\\a;C:\\b")


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.os = FakePlatform(self.base / "pc")
        self.app = Path(self.os.envs["LOCALAPPDATA"]) / "Programs" / "MemuAI"

    def tearDown(self):
        self.doCleanups()  # close payload handles before Windows removes their setup exe
        self.tmp.cleanup()

    def engine_for(self, setup: Path, product=products.SUITE) -> Engine:
        pak = payload.Payload(setup)
        self.addCleanup(pak.close)
        return Engine(product, self.os, pak, close_wait=0.05)

    def install(self, version="2.0", components=("proxy", "memuai", "memucode"), tasks=("autostart",),
                language="en", setup=None, **kwargs):
        setup = setup or make_setup(self.base, version)
        eng = self.engine_for(setup)
        events = []
        result = eng.install(Options(str(self.app), list(components), list(tasks), language),
                             events.append, **kwargs)
        return eng, result, events

    def key(self):
        return ("HKCU", products.UNINSTALL_ROOT + "\\" + products.SUITE.reg_key)

    def test_fresh_install_registers_everything(self):
        eng, result, events = self.install(tasks=("autostart", "desktopicon", "proxyicon"), language="ru")
        self.assertTrue(result["ok"])
        self.assertEqual(eng.default_dir(), str(self.app))
        self.assertTrue((self.app / "MemuAI" / "MemuAI.exe").is_file())
        self.assertTrue((self.app / "MemuAIProxy" / "_internal" / "python313.dll").is_file())
        self.assertTrue((self.app / "MemuAI" / "_internal" / "empty-dir").is_dir())
        self.assertEqual((self.app / "MemuAI-Uninstall.exe").read_bytes(), STUB)
        state = json.loads((self.app / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(state["components"], ["proxy", "memuai", "memucode"])
        self.assertEqual(state["entries"]["memucode"], ["MemuCode"])
        values = self.os.registry[self.key()]
        for name in ("DisplayName", "DisplayIcon", "DisplayVersion", "Publisher", "UninstallString",
                     "QuietUninstallString", "EstimatedSize", "InstallLocation"):
            self.assertIn(name, values)
        self.assertEqual(values["DisplayVersion"], "2.0")
        self.assertIn("--uninstall", values["UninstallString"])
        self.assertTrue(values["DisplayIcon"].endswith("MemuAI.exe,0"))
        run = self.os.registry[("HKCU", products.RUN_KEY)]
        self.assertTrue(run["MemuAIProxy"].endswith('MemuAIProxy.exe" --background'))
        self.assertNotIn("MemuAI", run)
        path = self.os.registry[("HKCU", "Environment")]["Path"]
        self.assertIn(str(self.app / "MemuCode"), path)
        start = self.base / "pc" / "StartMenu" / "MemuAI"
        self.assertEqual(sorted(p.name for p in start.iterdir()),
                         ["MemuAI (безопасный режим).lnk", "MemuAI Proxy.lnk", "MemuAI.lnk",
                          "MemuCode CLI.lnk", "Удалить MemuAI.lnk"])  # Round 29: safe-mode shortcut
        safe = json.loads((start / "MemuAI (безопасный режим).lnk").read_text(encoding="utf-8"))
        self.assertIn("--safe-mode", safe.get("args", ""))
        link = json.loads((start / "MemuAI.lnk").read_text(encoding="utf-8"))
        self.assertEqual(link["app_id"], "MemuAI.Desktop")
        cli_link = json.loads((start / "MemuCode CLI.lnk").read_text(encoding="utf-8"))
        self.assertEqual(cli_link["workdir"], self.os.envs["USERPROFILE"])
        desktop = self.base / "pc" / "Desktop"
        self.assertEqual(sorted(p.name for p in desktop.iterdir()), ["MemuAI Proxy.lnk", "MemuAI.lnk"])
        self.assertEqual(events[-1]["phase"], "done")
        percents = [e["percent"] for e in events if "percent" in e]
        self.assertEqual(percents, sorted(percents))
        self.assertFalse(any(name.startswith(".~") for name in os.listdir(self.app)))
        self.assertIn(("broadcast",), self.os.calls)

    def test_upgrade_swaps_folders_and_keeps_user_files(self):
        self.install("2.0")
        stale = self.app / "MemuAI" / "_internal" / "stale.py"
        stale.write_text("old")
        (self.app / "notes.txt").write_text("mine")
        data = Path(self.os.envs["APPDATA"]) / "MemuAI" / "chats.json"
        data.parent.mkdir(parents=True)
        data.write_text("{}")
        self.os.processes = [(42, str(self.app / "MemuAI" / "MemuAI.exe"))]
        setup = make_setup(self.base, "2.1")
        eng = self.engine_for(setup)
        existing = eng.detect()
        self.assertEqual((existing.version, existing.source), ("2.0", "memuai"))
        self.assertEqual(eng.install_kind(existing), "upgrade")
        with self.assertRaises(AppsRunning) as ctx:
            eng.install(Options(str(self.app), ["proxy", "memuai"], ["autostart"]))
        self.assertEqual(ctx.exception.apps, ["MemuAI.exe"])
        self.assertIn(("run", "MemuAI.exe", ("--shutdown",)), self.os.calls)
        self.assertIn(("run", "MemuAIProxy.exe", ("--shutdown",)), self.os.calls)
        self.assertTrue(stale.exists(), "nothing changes while apps are running")
        eng.install(Options(str(self.app), ["proxy", "memuai"], ["autostart"]), force_close=True)
        self.assertIn(("terminate", 42), self.os.calls)
        self.assertFalse(stale.exists(), "the program folder is replaced, not overlaid")
        self.assertIn("2.1", (self.app / "MemuAI" / "_internal" / "app" / "pgstatic" / "index.html").read_text())
        self.assertEqual((self.app / "notes.txt").read_text(), "mine")
        self.assertEqual(data.read_text(), "{}")
        # MemuCode deselected: folder, shortcut and PATH entry leave together.
        self.assertFalse((self.app / "MemuCode").exists())
        self.assertNotIn(str(self.app / "MemuCode"), self.os.registry[("HKCU", "Environment")].get("Path", ""))
        self.assertFalse((self.base / "pc" / "StartMenu" / "MemuAI" / "MemuCode CLI.lnk").exists())
        self.assertEqual(self.os.registry[self.key()]["DisplayVersion"], "2.1")

    def test_proxy_only_edition_removes_the_chat_app(self):
        self.install("2.0", tasks=("autostart", "desktopicon", "memuaiautostart"))
        self.assertIn("MemuAI", self.os.registry[("HKCU", products.RUN_KEY)])
        eng, result, _ = self.install("2.1", components=("proxy",), tasks=("autostart", "desktopicon", "memuaiautostart"))
        self.assertEqual(result["components"], ["proxy"])
        self.assertEqual(result["tasks"], ["autostart"], "tasks of absent components are dropped")
        self.assertFalse((self.app / "MemuAI").exists())
        self.assertFalse((self.app / "MemuCode").exists())
        self.assertNotIn("MemuAI", self.os.registry[("HKCU", products.RUN_KEY)])
        self.assertFalse((self.base / "pc" / "Desktop" / "MemuAI.lnk").exists())
        self.assertTrue((self.app / "MemuAIProxy" / "MemuAIProxy.exe").exists())
        self.assertEqual(eng.launch(self.app, result["components"]), True)
        self.assertEqual(self.os.launched[-1], ("MemuAIProxy.exe", ("--background",)))

    def test_inno_installation_is_taken_over(self):
        self.app.mkdir(parents=True)
        write_tree(self.app, {"unins000.exe": b"inno", "unins000.dat": b"inno", "MemuAI.exe": b"flat",
                              "MemuAIProxy.exe": b"flat", "_internal/old.dll": b"old",
                              "MemuAIProxy/MemuAIProxy.exe": b"old proxy", "MemuAI/MemuAI.exe": b"old"})
        self.os.registry[self.key()] = {
            "Inno Setup: App Path": str(self.app), "InstallLocation": str(self.app) + "\\",
            "DisplayVersion": "1.200", "Inno Setup: Selected Components": "proxy,memuai",
            "Inno Setup: Selected Tasks": "autostart,desktopicon", "Inno Setup: Language": "ru",
        }
        self.os.registry[("HKCU", products.RUN_KEY)] = {"MemuAI": '"x\\MemuAI.exe"'}
        start = self.base / "pc" / "StartMenu" / "MemuAI"
        start.mkdir(parents=True)
        (start / "MemuAI Proxy без окна.lnk").write_text("legacy")
        setup = make_setup(self.base, "2.0")
        eng = self.engine_for(setup)
        existing = eng.detect()
        self.assertEqual(existing.source, "inno")
        self.assertEqual(existing.components, ["proxy", "memuai"])
        defaults = eng.defaults(existing)
        self.assertEqual(defaults.language, "ru")
        self.assertEqual(defaults.components, ["proxy", "memuai"])
        self.assertIn("desktopicon", defaults.tasks)
        self.assertIn("memuaiautostart", defaults.tasks, "autostart enabled inside the app survives the update")
        eng.install(defaults)
        names = set(os.listdir(self.app))
        self.assertFalse({"unins000.exe", "unins000.dat", "MemuAI.exe", "MemuAIProxy.exe", "_internal"} & names)
        values = self.os.registry[self.key()]
        self.assertNotIn("Inno Setup: App Path", values)
        self.assertEqual(values["DisplayVersion"], "2.0")
        self.assertFalse((start / "MemuAI Proxy без окна.lnk").exists())

    def test_failed_extraction_rolls_back_and_leaves_no_staging(self):
        self.install("2.0")
        before = (self.app / "MemuAI" / "MemuAI.exe").read_bytes()
        setup = make_setup(self.base, "2.1")
        data = bytearray(setup.read_bytes())
        with payload.Payload(setup) as pak:
            last = pak.index.blocks[-1]
            data[pak.loc.payload_start + last.offset + last.csize // 2] ^= 0x55
        setup.write_bytes(bytes(data))
        eng = self.engine_for(setup)
        with self.assertRaises(SetupError) as ctx:
            eng.install(Options(str(self.app), ["proxy", "memuai", "memucode"], ["autostart"]))
        self.assertEqual(ctx.exception.code, "payload_damaged")
        self.assertEqual((self.app / "MemuAI" / "MemuAI.exe").read_bytes(), before)
        self.assertFalse(any(name.startswith(".~") for name in os.listdir(self.app)))
        self.assertEqual(self.os.registry[self.key()]["DisplayVersion"], "2.0")

    def test_swap_failure_restores_previous_folders(self):
        self.install("2.0")
        eng = self.engine_for(make_setup(self.base, "2.1"))
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if ".~stage-" in str(src) and "MemuCode" in str(src):
                raise PermissionError(13, "locked", str(dst))
            return real_replace(src, dst)

        with patch("installer.engine.os.replace", side_effect=flaky):
            with self.assertRaises(SetupError) as ctx:
                eng.install(Options(str(self.app), ["proxy", "memuai", "memucode"], ["autostart"]))
        self.assertEqual(ctx.exception.code, "write_failed")
        self.assertIn("2.0", (self.app / "MemuAI" / "_internal" / "app" / "pgstatic" / "index.html").read_text())
        self.assertTrue((self.app / "MemuCode" / "MemuCode.exe").read_bytes().startswith(b"memucode-2.0"))
        self.assertFalse(any(name.startswith(".~") for name in os.listdir(self.app)))

    def test_cancel_keeps_the_previous_version(self):
        self.install("2.0")
        eng = self.engine_for(make_setup(self.base, "2.1"))

        def report(event):
            if event.get("phase") == "extracting":
                eng.cancel_event.set()

        with self.assertRaises(Cancelled):
            eng.install(Options(str(self.app), ["proxy", "memuai", "memucode"], ["autostart"]), report)
        self.assertIn("2.0", (self.app / "MemuAI" / "_internal" / "app" / "pgstatic" / "index.html").read_text())
        self.assertFalse(any(name.startswith(".~") for name in os.listdir(self.app)))

    def test_not_enough_space(self):
        eng = self.engine_for(make_setup(self.base, "2.0"))
        with patch.object(self.os, "disk_free", return_value=1024):
            self.assertEqual(eng.check_dir(str(self.app), ["proxy"])["error"], "dir_no_space")
            with self.assertRaises(SetupError) as ctx:
                eng.install(Options(str(self.app), ["proxy"], []))
        self.assertEqual(ctx.exception.code, "dir_no_space")

    def test_check_dir_guards_foreign_and_root_folders(self):
        eng = self.engine_for(make_setup(self.base, "2.0"))
        foreign = self.base / "pc" / "home" / "Documents"
        foreign.mkdir(parents=True)
        (foreign / "thesis.docx").write_text("x")
        res = eng.check_dir(str(foreign), ["proxy"])
        self.assertTrue(res["ok"] and res["adjusted"])
        self.assertEqual(Path(res["path"]), foreign / "MemuAI")
        res = eng.check_dir(self.os.envs["USERPROFILE"], ["proxy"])
        self.assertTrue(res["adjusted"])
        self.assertFalse(eng.check_dir("relative\\path", ["proxy"])["ok"])
        self.assertEqual(eng.check_dir("", ["proxy"])["error"], "dir_empty")
        self.install("2.0", setup=eng.payload.path)
        res = eng.check_dir(str(self.app), ["proxy"])
        self.assertTrue(res["ok"])
        self.assertFalse(res["adjusted"], "our own folder is an upgrade target")

    def test_uninstall_keeps_personal_data_by_default(self):
        self.install("2.0", tasks=("autostart", "desktopicon", "memuaiautostart"))
        data = Path(self.os.envs["APPDATA"]) / "MemuAI"
        (data / "memuai").mkdir(parents=True)
        (data / "memuai" / "chats.json").write_text("{}")
        uninstaller = Engine(products.SUITE, self.os, None)
        result = uninstaller.uninstall(self.app)
        self.assertTrue(result["ok"])
        self.assertFalse(self.app.exists())
        self.assertTrue((data / "memuai" / "chats.json").exists())
        self.assertNotIn(self.key(), self.os.registry)
        self.assertEqual(self.os.registry.get(("HKCU", products.RUN_KEY)), {})
        self.assertNotIn(str(self.app / "MemuCode"), self.os.registry[("HKCU", "Environment")].get("Path", ""))
        self.assertFalse((self.base / "pc" / "StartMenu" / "MemuAI").exists())
        self.assertEqual(list((self.base / "pc" / "Desktop").iterdir()), [])
        self.assertIn(str(data), result["kept_data"])

    def test_uninstall_can_remove_data_but_only_known_folders(self):
        self.install("2.0")
        roaming = Path(self.os.envs["APPDATA"])
        (roaming / "MemuAI").mkdir()
        (roaming / "MemuAIProxy").mkdir()
        (Path(self.os.envs["LOCALAPPDATA"]) / "MemuAI" / "bin").mkdir(parents=True)
        (self.app / "user-notes.txt").write_text("keep")
        result = Engine(products.SUITE, self.os, None).uninstall(self.app, remove_data=True)
        self.assertFalse((roaming / "MemuAI").exists())
        self.assertFalse((Path(self.os.envs["LOCALAPPDATA"]) / "MemuAI").exists())
        self.assertTrue((roaming / "MemuAIProxy").exists(), "the standalone Proxy's data is another product's")
        self.assertTrue((self.app / "user-notes.txt").exists(), "unknown files in the program folder stay")
        self.assertEqual(len(result["removed_data"]), 2)
        eng = Engine(products.SUITE, self.os, None)
        self.assertFalse(eng._safe_data_dir(roaming / "Other", self.app))
        self.assertFalse(eng._safe_data_dir(roaming / "MemuAI", roaming / "MemuAI"))

    def test_standalone_proxy_product(self):
        dist = self.base / "proxy-dist"
        write_tree(dist, {"MemuAIProxy.exe": b"standalone", "_internal/x.dll": os.urandom(999)})
        setup = make_setup(self.base, "2.0", products.PROXY, [("proxy", dist, "")])
        eng = self.engine_for(setup, products.PROXY)
        target = Path(self.os.envs["LOCALAPPDATA"]) / "Programs" / "MemuAI Proxy"
        self.assertEqual(eng.default_dir(), str(target))
        target.mkdir(parents=True)
        (target / "readme-by-user.txt").write_text("x")
        eng.install(Options(str(target), ["proxy"], ["autostart"]))
        self.assertTrue((target / "MemuAIProxy.exe").exists())
        self.assertTrue((target / "MemuAIProxy-Uninstall.exe").exists())
        run = self.os.registry[("HKCU", products.RUN_KEY)]
        self.assertTrue(run["MemuAIProxyStandalone"].endswith('MemuAIProxy.exe" --background'))
        self.assertNotIn("MemuAIProxy", run)
        start = self.base / "pc" / "StartMenu" / "MemuAI Proxy"
        self.assertTrue((start / "MemuAI Proxy.lnk").exists())
        Engine(products.PROXY, self.os, None).uninstall(target)
        self.assertEqual(os.listdir(target), ["readme-by-user.txt"])
        self.assertNotIn(("HKCU", products.UNINSTALL_ROOT + "\\" + products.PROXY.reg_key), self.os.registry)


class CliTest(unittest.TestCase):
    def test_inno_and_nsis_switches(self):
        args = cli.parse(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/DIR=D:\\Apps\\MemuAI", "/COMPONENTS=memuai,memucode",
                          "/TASKS=autostart,!desktopicon", "/LANG=ru", "/LOG=C:\\l.txt", "/SP-"])
        self.assertTrue(args.silent and args.very_silent)
        self.assertEqual(args.directory, "D:\\Apps\\MemuAI")
        self.assertEqual(args.components, ["memuai", "memucode"])
        self.assertEqual(args.language, "ru")
        self.assertEqual(args.log, "C:\\l.txt")
        self.assertEqual(args.unknown, [])
        nsis = cli.parse(["/S", "/D=C:\\Program Files Custom\\MemuAI"])
        self.assertTrue(nsis.silent)
        self.assertEqual(nsis.directory, "C:\\Program Files Custom\\MemuAI")
        un = cli.parse(["--uninstall", "--silent", "--remove-data", "--app-dir", "C:\\x"])
        self.assertTrue(un.uninstall and un.silent and un.remove_data)
        self.assertEqual(un.app_dir, "C:\\x")

    def test_log_switch_forms(self):
        self.assertEqual(cli.parse(["/LOG"]).log, "")
        self.assertIsNone(cli.parse([]).log)
        self.assertEqual(cli.parse(["--log", "C:\\x.log", "/S"]).log, "C:\\x.log")
        self.assertTrue(cli.parse(["--log", "C:\\x.log", "/S"]).silent)

    def test_selection(self):
        suite = products.SUITE
        args = cli.parse(["--edition", "proxy", "--tasks", "autostart,proxyicon,desktopicon"])
        self.assertEqual(cli.resolve_selection(args, suite, suite.default_components(), suite.default_tasks()),
                         (["proxy"], ["autostart", "proxyicon"]))
        args = cli.parse(["/MERGETASKS=desktopicon,!autostart"])
        self.assertEqual(cli.resolve_selection(args, suite, ["proxy", "memuai"], ["autostart"]),
                         (["proxy", "memuai"], ["desktopicon"]))
        args = cli.parse(["/COMPONENTS=memucode"])
        self.assertEqual(cli.resolve_selection(args, suite, ["proxy", "memuai"], [])[0], ["proxy"],
                         "the CLI ships with the full edition only")


class SilentMainTest(unittest.TestCase):
    def test_silent_install_and_uninstall_through_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake = FakePlatform(base / "pc")
            setup = make_setup(base, "2.0")
            target = base / "pc" / "Apps" / "MemuAI"
            with patch.object(setup_main, "make_platform", return_value=fake), \
                    patch.dict(os.environ, {"MEMUAI_SETUP_EXE": str(setup)}), \
                    patch.object(setup_main, "setup_logging", return_value=str(base / "setup.log")):
                (base / "setup.log").write_text("log")
                code = setup_main.main(["/VERYSILENT", f"/DIR={target}", "/TASKS=autostart", "--launch"])
                self.assertEqual(code, 0)
                self.assertTrue((target / "MemuAI" / "MemuAI.exe").exists())
                self.assertTrue((target / "install.log").exists())
                self.assertEqual(fake.launched, [("MemuAI.exe", ())])
                with patch.dict(os.environ, {"MEMUAI_SETUP_EXE": str(target / "MemuAI-Uninstall.exe")}):
                    code = setup_main.main(["--uninstall", "--silent"])
                self.assertEqual(code, 0)
                self.assertFalse(target.exists())

    def test_damaged_setup_exits_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.exe"
            broken.write_bytes(STUB + payload.TRAILER.pack(payload.MAGIC, 10 ** 9, 5, 1, 0, payload.MAGIC_END))
            with patch.dict(os.environ, {"MEMUAI_SETUP_EXE": str(broken)}), \
                    patch.object(setup_main, "setup_logging", return_value=""):
                self.assertEqual(setup_main.main(["/S"]), 1)


class BridgeTest(unittest.TestCase):
    def test_install_runs_in_a_worker_and_reports_status(self):
        import time
        from installer.bridge import Bridge
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake = FakePlatform(base / "pc")
            setup = make_setup(base, "2.0")
            pak = payload.Payload(setup)
            self.addCleanup(pak.close)
            bridge = Bridge(Engine(products.SUITE, fake, pak, close_wait=0.05), "install", "ru")
            info = bridge.init()
            self.assertEqual(info["kind"], "fresh")
            self.assertEqual(info["lang"], "ru")
            self.assertEqual([c["id"] for c in info["components"]], ["proxy", "memuai", "memucode"])
            self.assertTrue(all(c["size"] > 0 for c in info["components"]))
            self.assertTrue(bridge.check_dir(info["defaults"]["dir"], info["defaults"]["components"])["ok"])
            fake.processes = [(7, "x\\MemuAI.exe")]
            (Path(info["defaults"]["dir"]) / "MemuAI").mkdir(parents=True)
            bridge.install({"dir": info["defaults"]["dir"], "components": ["proxy", "memuai"], "tasks": ["autostart"]})
            for _ in range(400):
                if bridge.poll()["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(bridge.poll()["status"], "apps")
            self.assertEqual(bridge.poll()["apps"], ["MemuAI.exe"])
            bridge.install({"dir": info["defaults"]["dir"], "components": ["proxy", "memuai"], "tasks": ["autostart"]},
                           True)
            for _ in range(800):
                if bridge.poll()["status"] != "running":
                    break
                time.sleep(0.01)
            state = bridge.poll()
            self.assertEqual(state["status"], "done", state)
            self.assertEqual(state["percent"], 100)
            self.assertTrue(bridge.launch())
            self.assertEqual(fake.launched, [("MemuAI.exe", ())])
            self.assertFalse([name for name in vars(bridge) if not name.startswith("_")],
                             "pywebview exposes public attributes to the page")
            pak.close()


class I18nParityTest(unittest.TestCase):
    def test_native_strings_have_both_languages(self):
        for key, entry in i18n.STRINGS.items():
            self.assertEqual(set(entry), {"en", "ru"}, key)
        self.assertEqual(i18n.pick_language("ru_RU.UTF-8"), "ru")
        self.assertEqual(i18n.pick_language("de-DE"), "en")

    def test_interface_dictionaries_match(self):
        source = (ROOT / "installer" / "ui" / "i18n.js").read_text(encoding="utf-8")
        en = source.split("  en: {", 1)[1].split("\n  },\n  ru: {", 1)[0]
        ru = source.split("\n  ru: {", 1)[1]
        keys = lambda text: set(re.findall(r'^\s+"([\w.]+)":', text, flags=re.M))  # noqa: E731
        self.assertTrue(keys(en))
        self.assertEqual(keys(en) ^ keys(ru), set())
        used = set(re.findall(r'data-i18n(?:-title)?="([\w.]+)"', (ROOT / "installer" / "ui" / "index.html").read_text(encoding="utf-8")))
        self.assertEqual(used - keys(en), set())


if __name__ == "__main__":
    unittest.main()
