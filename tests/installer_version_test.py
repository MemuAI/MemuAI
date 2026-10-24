"""Round 21: version 3.0, upgrade detection and the size of the installer window.

* ``installer.versions``: normalising installed version numbers, classifying
  fresh / upgrade / reinstall / downgrade, Inno's MajorVersion/MinorVersion,
  and the pure-Python reader of the PE file-version resource;
* ``Engine.detect``: the installed version is read from install.json →
  DisplayVersion → the exe's version resource → MajorVersion/MinorVersion;
* ``installer.main.fit_window``: the client area is the design size × DPI scale,
  clamped to the monitor's work area and centred on it;
* the welcome screen has distinct UPDATE / REPAIR / DOWNGRADE wording in ru and en.
"""
from __future__ import annotations

import json
import re
import struct
import tempfile
import unittest
from pathlib import Path

from installer import main as setup_main, products, versions
from installer.engine import Engine, Existing, HKCU

from tests.installer_engine_test import FakePlatform

ROOT = Path(__file__).resolve().parents[1]


def make_pe(path: Path, version=(3, 0, 0, 0), pe32plus: bool = True, nested_lang: bool = True) -> Path:
    """A minimal but well-formed PE with a .rsrc section holding RT_VERSION."""
    ms = (version[0] << 16) | version[1]
    ls = (version[2] << 16) | version[3]
    key = "VS_VERSION_INFO\0".encode("utf-16-le")
    header = struct.pack("<HHH", 0, 52, 0) + key
    header += b"\0" * ((4 - len(header) % 4) % 4)
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 1, 0, 0, 0)
    block = header + fixed
    block = struct.pack("<H", len(block)) + block[2:]
    rva = 0x1000
    # resource tree: root -> type 16 -> name 1 -> lang 1033 -> data entry
    root = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 16, 0x80000000 | 24)
    level2 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1)
    if nested_lang:
        level2 += struct.pack("<II", 1, 0x80000000 | 48)
        level3 = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1033, 72)
    else:
        level2 += struct.pack("<II", 1, 48)
        level3 = b""
    tree = root + level2 + level3
    data_entry_at = 72 if nested_lang else 48
    tree = tree.ljust(data_entry_at, b"\0")
    data_at = data_entry_at + 16
    tree += struct.pack("<IIII", rva + data_at, len(block), 0, 0)
    rsrc = tree + block
    rsrc += b"\0" * ((0x200 - len(rsrc) % 0x200) % 0x200)
    opt_size = 240 if pe32plus else 224
    dos = b"MZ" + b"\0" * 58 + struct.pack("<I", 0x40)
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664 if pe32plus else 0x14C, 1, 0, 0, 0, opt_size, 0x22)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x20B if pe32plus else 0x10B)
    dirs = 112 if pe32plus else 96
    struct.pack_into("<I", opt, dirs - 4, 16)
    struct.pack_into("<II", opt, dirs + 16, rva, len(tree) + len(block))
    section = b".rsrc\0\0\0" + struct.pack("<IIIIIIHHI", len(rsrc), rva, len(rsrc), 0x200, 0, 0, 0, 0, 0x40000040)
    head = (dos + coff + bytes(opt) + section).ljust(0x200, b"\0")
    path.write_bytes(head + rsrc)
    return path


class NormalizeAndClassify(unittest.TestCase):
    def test_normalize_version(self):
        cases = {"2.0": "2.0", "2.0.0": "2.0", "2.0.0.0": "2.0", "3": "3.0", "1.9.3": "1.9.3", "1.9.3.0": "1.9.3",
                 "2.5.0.1": "2.5.0.1", "v2.5": "2.5", " 3.0 ": "3.0", "3.0-beta": "3.0-beta", "3.0 RC1": "3.0-rc1",
                 "1.200": "1.200", '"2.0"': "2.0", 2: "2.0", 2.5: "2.5",
                 "": "", None: "", "N/A": "", "abc": "", "0.0.0.0": "", "0": "", "2.0.0.0.5": "", "..": "",
                 True: "", "1.2.3.4.5.6": "", "version": ""}
        for raw, expected in cases.items():
            self.assertEqual(versions.normalize_version(raw), expected, repr(raw))

    def test_classification(self):
        c = versions.classify
        self.assertEqual(c("3.0", "", found=False), "fresh")
        self.assertEqual(c("3.0", "2.0", found=False), "fresh")
        for older in ("2.0", "2.0.0", "2.0.0.0", "1.0", "1.9.3", "2.5", "1.200", "v2.9.9", "3.0-beta", "3.0.0-rc1"):
            self.assertEqual(c("3.0", older), "upgrade", older)
        for same in ("3.0", "3.0.0", "3.0.0.0", "3", "v3.0"):
            self.assertEqual(c("3.0", same), "reinstall", same)
        for newer in ("3.0.1", "3.1", "4.0", "10.0", "3.0.0.1"):
            self.assertEqual(c("3.0", newer), "downgrade", newer)
        # Unknown installed number (missing, garbage, 0.0.0.0): offered as an update.
        for unknown in ("", None, "garbage", "0.0.0.0", "N/A"):
            self.assertEqual(c("3.0", unknown), "upgrade", repr(unknown))
        # A Setup without a version (development build) never claims to be older.
        self.assertEqual(c("", "2.0"), "upgrade")
        # A pre-release of the same number is older than the release.
        self.assertEqual(c("3.0-beta", "3.0"), "downgrade")
        self.assertEqual(c("3.0", "3.0-beta"), "upgrade")

    def test_inno_major_minor(self):
        self.assertEqual(versions.from_major_minor(2, 0), "2.0")
        self.assertEqual(versions.from_major_minor("1", "9"), "1.9")
        self.assertEqual(versions.from_major_minor(None, 0), "")
        self.assertEqual(versions.from_major_minor(0, 0), "")
        self.assertEqual(versions.from_major_minor("x", 1), "")
        self.assertEqual(versions.from_major_minor(-1, 2), "")


class PeVersionReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_pe32plus_and_pe32(self):
        self.assertEqual(versions.pe_file_version(make_pe(self.dir / "a.exe", (2, 0, 0, 0))), "2.0.0.0")
        self.assertEqual(versions.pe_file_version(make_pe(self.dir / "b.exe", (1, 9, 3, 0), pe32plus=False)), "1.9.3.0")
        self.assertEqual(versions.pe_file_version(make_pe(self.dir / "c.exe", (2, 5, 0, 7), nested_lang=False)), "2.5.0.7")
        self.assertEqual(versions.installed_exe_version(make_pe(self.dir / "d.exe", (3, 0, 0, 0))), "3.0")

    def test_foreign_and_broken_files_have_no_version(self):
        (self.dir / "text.exe").write_text("hello")
        (self.dir / "empty.exe").write_bytes(b"")
        (self.dir / "mz.exe").write_bytes(b"MZ" + b"\0" * 100)
        good = make_pe(self.dir / "good.exe").read_bytes()
        (self.dir / "cut.exe").write_bytes(good[:0x210])          # resource section truncated
        noise = bytearray(good)
        noise[0x200:0x240] = b"\xff" * 0x40                       # resource directory destroyed
        (self.dir / "noise.exe").write_bytes(bytes(noise))
        for name in ("text.exe", "empty.exe", "mz.exe", "cut.exe", "missing.exe"):
            self.assertEqual(versions.pe_file_version(self.dir / name), "", name)
        # the scan fallback still finds VS_VERSION_INFO when the tree is unusable
        self.assertEqual(versions.pe_file_version(self.dir / "noise.exe"), "3.0.0.0")


class DetectInstalledVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.os = FakePlatform(self.root)
        self.app = self.root / "Local" / "Programs" / "MemuAI"
        self.key = (HKCU, Engine(products.SUITE, self.os, version="3.0").uninstall_key)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self):
        return Engine(products.SUITE, self.os, version="3.0")

    def inno_key(self, **extra):
        values = {"Inno Setup: App Path": str(self.app), "InstallLocation": str(self.app) + "\\"}
        values.update(extra)
        self.os.registry[self.key] = values

    def test_display_version_of_an_inno_install(self):
        self.app.mkdir(parents=True)
        (self.app / "MemuAI.exe").write_bytes(b"flat")
        self.inno_key(DisplayVersion="2.0.0")
        eng = self.engine()
        existing = eng.detect()
        self.assertEqual((existing.source, existing.version), ("inno", "2.0"))
        self.assertEqual(eng.install_kind(existing), "upgrade")

    def test_exe_resource_when_display_version_is_missing_or_garbage(self):
        make_pe(self._mk("MemuAI.exe"), (1, 9, 3, 0))
        for display in (None, "", "unknown", "0.0.0.0"):
            self.inno_key(**({} if display is None else {"DisplayVersion": display}))
            existing = self.engine().detect()
            self.assertEqual(existing.version, "1.9.3", repr(display))
            self.assertEqual(self.engine().install_kind(existing), "upgrade")

    def test_new_layout_exe_and_inno_major_minor(self):
        make_pe(self._mk("MemuAIProxy/MemuAIProxy.exe"), (2, 5, 0, 0))
        self.inno_key()
        self.assertEqual(self.engine().detect().version, "2.5")
        (self.app / "MemuAIProxy" / "MemuAIProxy.exe").write_bytes(b"no version resource")
        self.inno_key(MajorVersion=2, MinorVersion=0)
        self.assertEqual(self.engine().detect().version, "2.0")
        self.inno_key()
        existing = self.engine().detect()
        self.assertEqual((existing.found, existing.version), (True, ""))
        self.assertEqual(self.engine().install_kind(existing), "upgrade")   # unknown → update

    def test_install_json_wins_and_same_or_newer_versions(self):
        self.app.mkdir(parents=True)
        self.os.registry[self.key] = {"InstallLocation": str(self.app), "DisplayVersion": "2.0"}
        for state_version, kind in (("3.0.0.0", "reinstall"), ("3.1", "downgrade"), ("2.0", "upgrade")):
            (self.app / "install.json").write_text(json.dumps({"product": "memuai", "version": state_version,
                                                               "components": ["proxy"], "tasks": []}), encoding="utf-8")
            existing = self.engine().detect()
            self.assertEqual(existing.version, versions.normalize_version(state_version))
            self.assertEqual(self.engine().install_kind(existing), kind)

    def test_a_broken_platform_reader_never_stops_detection(self):
        self._mk("MemuAI/MemuAI.exe").write_bytes(b"x")
        self.inno_key()
        self.os.file_version = lambda path: (_ for _ in ()).throw(OSError("access denied"))
        existing = self.engine().detect()
        self.assertTrue(existing.found)
        self.assertEqual(existing.version, "")

    def test_nothing_installed_is_fresh(self):
        eng = self.engine()
        self.assertEqual(eng.install_kind(eng.detect()), "fresh")
        self.assertEqual(eng.install_kind(Existing()), "fresh")

    def _mk(self, relative: str) -> Path:
        path = self.app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class WindowGeometry(unittest.TestCase):
    """pywebview sized the OUTER window and removed the frame afterwards: the client
    area came out 944×581. fit_window() makes the CLIENT area the design size."""

    def test_client_area_is_design_size_times_dpi_scale(self):
        fit = setup_main.fit_window
        g = fit((960, 620), 1.0, (0, 0, 1920, 1040))
        self.assertEqual((g["width"], g["height"], g["client"], g["logical"]), (960, 620, (960, 620), (960, 620)))
        self.assertEqual((g["x"], g["y"]), (480, 210))
        for scale in (1.25, 1.5, 1.75):
            g = fit((960, 620), scale, (0, 0, 3840, 2100))
            self.assertEqual(g["client"], (round(960 * scale), round(620 * scale)), scale)
            self.assertEqual(g["logical"], (960, 620))

    def test_a_frame_is_added_on_top_of_the_client_area(self):
        g = setup_main.fit_window((960, 620), 1.0, (0, 0, 1920, 1040), nonclient=(16, 39))
        self.assertEqual((g["width"], g["height"], g["client"]), (976, 659, (960, 620)))

    def test_clamped_to_the_work_area_and_centred_on_that_monitor(self):
        # 1366×768 at 150 %: work area 1366×728 physical → the page gets less than 960×620
        g = setup_main.fit_window((960, 620), 1.5, (0, 0, 1366, 728))
        self.assertLessEqual(g["width"], 1366 - 2 * 24)
        self.assertLessEqual(g["height"], 728 - 2 * 24)
        self.assertEqual((g["x"], g["y"]), ((1366 - g["width"]) // 2, (728 - g["height"]) // 2))
        self.assertLess(g["logical"][1], 620)                     # the fluid page takes over
        # second monitor to the right, taskbar on top
        g = setup_main.fit_window((960, 620), 1.0, (1920, 40, 3840, 1080))
        self.assertEqual((g["x"], g["y"]), (1920 + 480, 40 + 210))

    def test_nonsense_input_does_not_break(self):
        g = setup_main.fit_window((960, 620), "bad", (0, 0, 100, 100))
        self.assertGreater(g["width"], 0)
        self.assertEqual(setup_main.fit_window((960, 620), 0, (0, 0, 1920, 1080))["scale"], 1.0)

    def test_run_window_places_the_window_before_it_is_shown(self):
        source = (ROOT / "installer" / "main.py").read_text(encoding="utf-8")
        self.assertIn('("before_show", on_before_show)', source)
        self.assertIn("FormStartPosition.Manual", source)
        self.assertIn("ensure_client_size", source)
        win = (ROOT / "installer" / "winplat.py").read_text(encoding="utf-8")
        for needle in ("GetDpiForWindow", "MonitorFromPoint", "GetMonitorInfoW", "SetWindowPos.argtypes",
                       "GetClientRect.argtypes", "GetFileVersionInfoW.argtypes", "VerQueryValueW.argtypes"):
            self.assertIn(needle, win)


class WelcomeWording(unittest.TestCase):
    def setUp(self):
        text = (ROOT / "installer" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.dicts = {}
        for lang in ("en", "ru"):
            block = re.search(lang + r": \{(.*?)\n  \}", text, re.S).group(1)
            self.dicts[lang] = dict(re.findall(r'"([\w.]+)": "((?:[^"\\]|\\.)*)"', block))

    def test_update_repair_and_downgrade_say_different_things(self):
        ru, en = self.dicts["ru"], self.dicts["en"]
        self.assertEqual(ru["btn.update"], "Обновить")
        self.assertEqual(ru["btn.reinstall"], "Переустановить")
        self.assertIn("Обновление", ru["welcome.upgrade.title"])
        self.assertIn("{from} → {version}", ru["welcome.upgrade.kicker"])
        self.assertIn("Восстановить", ru["welcome.reinstall.title"])
        self.assertIn("переустановить", ru["welcome.reinstall.title"])
        self.assertIn("более новая", ru["welcome.downgrade.kicker"])
        self.assertIn("{version}", ru["btn.downgrade"])
        for lang in ("ru", "en"):
            d = self.dicts[lang]
            for key in ("welcome.upgrade.kicker.unknown", "welcome.downgrade.note", "vt.installed", "vt.new",
                        "vt.unknown", "vt.same", "vt.older", "install.title.repair", "done.title.repair",
                        "title.update", "keep.chats", "keep.settings"):
                self.assertTrue(d.get(key), f"{lang}: {key}")
            self.assertIn("{name}", d["install.title.update"], "no hard-coded product name")
        self.assertEqual(set(self.dicts["ru"]), set(self.dicts["en"]))

    def test_the_page_has_the_transition_visual_and_mock_versions(self):
        html = (ROOT / "installer" / "ui" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="vt"', 'id="vt-from"', 'id="vt-to"', 'id="keep-chips"', 'id="downgrade-note"',
                       'id="stage"', 'id="panel-ghost"'):
            self.assertIn(needle, html)
        app = (ROOT / "installer" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function fit()", app)
        self.assertIn("MIN_W = 860, MIN_H = 540", app)
        css = (ROOT / "installer" / "ui" / "style.css").read_text(encoding="utf-8")
        self.assertIn("container-type: size", css)
        self.assertNotIn("height: 542px", css, "the panel must not have the old fixed height")


if __name__ == "__main__":
    unittest.main()
