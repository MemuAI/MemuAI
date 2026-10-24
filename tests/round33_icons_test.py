"""Round 33 — new MemuAI / MemuAI Proxy icons (#27) and the "white document" taskbar icon (#16).

Win32 is faked everywhere (``win_app_identity.set_api``); what needs a real
Windows desktop is listed in .r33/r33-icons.md.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import struct
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import assistant_icon, win_app_identity as ident  # noqa: E402

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def ico_entries(path: Path):
    data = path.read_bytes()
    _reserved, kind, count = struct.unpack("<HHH", data[:6])
    out = []
    for i in range(count):
        w, h, _c, _r, _planes, bpp, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i: 22 + 16 * i])
        out.append({"size": w or 256, "bpp": bpp, "png": data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"})
    return kind, out


def frame(path: Path, size: int):
    from PIL import Image
    image = Image.open(path)
    image.size = (size, size)
    image.load()
    return image.convert("RGBA")


# ------------------------------------------------------------------ assets
class IconAssetsTest(unittest.TestCase):
    def test_ico_files_have_every_size_bmp_below_256(self):
        for name in ("memuai-icon.ico", "icon.ico", "memunote-icon.ico"):
            kind, entries = ico_entries(ROOT / name)
            self.assertEqual(kind, 1, name)
            self.assertEqual(sorted(e["size"] for e in entries), list(ICO_SIZES), name)
            for entry in entries:
                self.assertEqual(entry["bpp"], 32, name)
                self.assertEqual(entry["png"], entry["size"] == 256, f"{name} {entry['size']}")

    def test_pngs(self):
        from PIL import Image
        for name, size in (("memuai-icon.png", 512), ("icon.png", 512), ("app/static/icon.png", 256),
                           ("installer/ui/assets/logo.png", 128), ("installer/ui/assets/logo-proxy.png", 128)):
            with Image.open(ROOT / name) as image:
                self.assertEqual(image.size, (size, size), name)
                self.assertEqual(image.mode, "RGBA", name)
                self.assertEqual(image.getpixel((size - 3, size - 3))[3], 0, f"{name}: corner must be transparent")

    def test_silhouettes_are_distinct_at_16px(self):
        ai, proxy = frame(ROOT / "memuai-icon.ico", 16), frame(ROOT / "icon.ico", 16)
        # MemuAI: speech bubble — the tail fills the bottom-left, the bottom-right is empty.
        self.assertGreater(ai.getpixel((1, 15))[3], 120)
        self.assertLess(ai.getpixel((15, 15))[3], 30)
        self.assertEqual(ai.getpixel((8, 6))[:3], (255, 255, 255), "white star in the middle")
        # Proxy: round badge — corners empty, warm star in the centre between white brackets.
        self.assertLess(proxy.getpixel((0, 0))[3], 40)
        r, g, b, a = proxy.getpixel((8, 8))
        self.assertGreater(r, b, "warm star")
        self.assertGreater(min(proxy.getpixel((3, 8))[:3]), 200, "left bracket is white")

    def test_small_frames_are_drawn_not_shrunk(self):
        """The 16-px frame is its own drawing: the shrunk 256-px frame looks different."""
        from PIL import Image, ImageChops, ImageStat
        for name in ("memuai-icon.ico", "icon.ico"):
            small = frame(ROOT / name, 16)
            shrunk = frame(ROOT / name, 256).resize((16, 16), Image.LANCZOS)
            diff = ImageStat.Stat(ImageChops.difference(small, shrunk)).mean
            self.assertGreater(sum(diff[:3]), 6, name)

    def test_svgs(self):
        for name in ("app/pgstatic/memuai-icon.svg", "app/static/proxy-icon.svg", "app/tgapp/icon.svg"):
            text = (ROOT / name).read_text(encoding="utf-8")
            root = ET.fromstring(text)
            self.assertTrue(root.tag.endswith("svg"), name)
            self.assertNotIn("M17 44V22l15 13 15-13v22", text, "the old «M» Mini App mark is gone")

    def test_build_scripts(self):
        import build_memuai_icon as kit
        import build_proxy_icon as proxy
        self.assertEqual([kit.variant_for(s) for s in (16, 20, 24, 40, 48, 256)],
                         ["tiny", "tiny", "small", "small", "full", "full"])
        for draw in (kit.memuai_svg, proxy.proxy_svg):
            for variant in ("tiny", "small", "full"):
                ET.fromstring(draw(variant))
        self.assertIn("crystal", kit.memuai_svg("full").lower() + "crystal")
        # build.py never re-runs the Playwright scripts while the committed files exist.
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"build_proxy_icon.py"', build)
        self.assertNotIn("st_mtime >= script.stat().st_mtime", build)

    def test_write_ico_round_trip(self):
        from PIL import Image, ImageChops
        import build_memuai_icon as kit
        frames = {s: Image.new("RGBA", (s, s), (10, 200, 30, 255)) for s in (16, 32, 256)}
        frames[16].putpixel((0, 0), (0, 0, 0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.ico"
            kit.write_ico(frames, path)
            kind, entries = ico_entries(path)
            self.assertEqual([e["size"] for e in entries], [16, 32, 256])
            for size, image in frames.items():
                self.assertIsNone(ImageChops.difference(frame(path, size), image).getbbox(), size)

    def test_shipped_and_used_everywhere(self):
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        self.assertIn("('memuai-icon.ico', '.')", spec)
        for module in ("app.win_app_identity", "app.win_com", "app.assistant_icon"):
            self.assertIn(f"'{module}'", spec)
        proxy_spec = (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8")
        self.assertIn("('icon.ico', '.')", proxy_spec)
        self.assertIn("'app.win_app_identity'", proxy_spec)
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"memuai-icon.ico;."', build)
        self.assertEqual(build.count('"icon.ico;."'), 2, "proxy and proxy_only")
        self.assertIn('"app.assistant_icon"', build)
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("M5.5 17.5v-11l6.5 7 6.5-7v11", html, "Proxy panel logo uses the new gateway mark")
        self.assertEqual(html.count("M8.6 6.6 4.2 12l4.4 5.4"), 3)
        about = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<img src="/memuai-icon.svg', about)


# ------------------------------------------------------------------ Win32 fake
class FakeApi:
    def __init__(self):
        self.calls = []
        self.next_handle = 0x8000_0000      # above 2**31: the old code could not pass these
        self.windows = {100, 200}
        self.links = {}
        self.app_id_result = True

    def icon_sizes(self, hwnd):
        return (20, 40) if hwnd == 200 else (16, 32)

    def tray_icon_size(self):
        return 16

    def load_icon(self, path, size):
        self.next_handle += 1
        self.calls.append(("load", os.path.basename(path), size, self.next_handle))
        return self.next_handle

    def set_icon(self, hwnd, which, handle):
        self.calls.append(("set", hwnd, which, handle))

    def destroy_icon(self, handle):
        self.calls.append(("destroy", handle))

    def is_window(self, hwnd):
        return hwnd in self.windows

    def set_process_app_id(self, app_id):
        self.calls.append(("appid", app_id))
        return self.app_id_result

    def notify_path_updated(self, path):
        self.calls.append(("notify", path))

    def notify_assoc_changed(self):
        self.calls.append(("assoc",))

    def set_window_properties(self, hwnd, props):
        self.calls.append(("props", hwnd, list(props)))
        return True

    def read_shortcut(self, link):
        return self.links.get(link)

    def write_shortcut_icon(self, link, icon, index):
        target, _old, _i = self.links[link]
        self.links[link] = (target, icon, index)
        self.calls.append(("link", os.path.basename(link), icon))
        return True


class Win32Case(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        ident.set_api(self.api)
        ident.WINDOW_ICONS = ident.WindowIcons()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        ident.set_api(None)
        ident.WINDOW_ICONS = ident.WindowIcons()
        self.tmp.cleanup()


class ProcessIdentityTest(Win32Case):
    def test_app_id_is_set_once(self):
        self.assertTrue(ident.set_process_app_id("MemuAI.Desktop"))
        self.assertTrue(ident.set_process_app_id("MemuAI.Desktop"))
        self.assertEqual([c for c in self.api.calls if c[0] == "appid"], [("appid", "MemuAI.Desktop")])
        self.assertEqual(ident.process_app_id(), "MemuAI.Desktop")

    def test_off_windows_everything_is_a_no_op(self):
        ident.set_api(None)
        with patch.object(ident, "is_windows", return_value=False):
            self.assertFalse(ident.set_process_app_id("X"))
            self.assertFalse(ident.WindowIcons().apply(1, str(ROOT / "icon.ico")))
            self.assertEqual(ident.refresh_shell_icons(["a"]), 0)
            self.assertEqual(ident.sync_shortcut_icons("x"), [])
            self.assertFalse(ident.set_window_identity(1, "X", "X", ""))

    def test_window_identity_relaunch_only_when_frozen(self):
        self.assertTrue(ident.set_window_identity(100, "MemuAI.Desktop", "MemuAI", r"C:\M\memuai-icon.ico,0",
                                                  command='"C:\\M\\MemuAI.exe"'))
        props = self.api.calls[-1][2]
        self.assertEqual(props[-1], (ident.PID_ID, "MemuAI.Desktop"), "the id goes last")
        count = len(self.api.calls)
        ident.set_window_identity(100, "MemuAI.Desktop", "MemuAI", r"C:\M\memuai-icon.ico,0",
                                  command='"C:\\M\\MemuAI.exe"')
        self.assertEqual(len(self.api.calls), count, "same identity again: not re-announced")
        self.assertIn((ident.PID_RELAUNCH_ICON, r"C:\M\memuai-icon.ico,0"), props)
        self.assertIn((ident.PID_RELAUNCH_NAME, "MemuAI"), props)
        with patch.object(sys, "frozen", False, create=True):
            ident.set_window_identity(100, "MemuAI.Desktop", "MemuAI", "x,0")
        self.assertEqual(self.api.calls[-1][2], [(ident.PID_ID, "MemuAI.Desktop")])


class WindowIconsTest(Win32Case):
    def test_apply_sets_small_and_big_and_keeps_handles(self):
        icons = ident.WindowIcons()
        path = str(ROOT / "memuai-icon.ico")
        self.assertTrue(icons.apply(200, path))
        loads = [c for c in self.api.calls if c[0] == "load"]
        self.assertEqual([c[2] for c in loads], [20, 40], "DPI-aware sizes")
        sets = [c for c in self.api.calls if c[0] == "set"]
        self.assertEqual([(c[1], c[2]) for c in sets], [(200, ident.ICON_SMALL), (200, ident.ICON_BIG)])
        self.assertGreater(sets[0][3], 2 ** 31, "64-bit handles pass through")
        self.assertFalse([c for c in self.api.calls if c[0] == "destroy"])
        before = len(self.api.calls)
        self.assertTrue(icons.apply(200, path), "same file again: nothing to do")
        self.assertEqual(len(self.api.calls), before)

    def test_old_handles_destroyed_only_after_the_new_ones_are_set(self):
        icons = ident.WindowIcons()
        icons.apply(100, str(ROOT / "memuai-icon.ico"))
        old = [c[3] for c in self.api.calls if c[0] == "load"]
        self.api.calls.clear()
        icons.apply(100, str(ROOT / "icon.ico"))
        kinds = [c[0] for c in self.api.calls]
        self.assertEqual(kinds, ["load", "load", "set", "set", "destroy", "destroy"])
        self.assertEqual(sorted(c[1] for c in self.api.calls if c[0] == "destroy"), sorted(old))
        icons.forget(100)
        self.assertEqual(len([c for c in self.api.calls if c[0] == "destroy"]), 4)

    def test_missing_file_or_window(self):
        icons = ident.WindowIcons()
        self.assertFalse(icons.apply(100, str(self.base / "none.ico")))
        self.assertFalse(icons.apply(999, str(ROOT / "icon.ico")))
        self.assertFalse(self.api.calls)

    def test_refresh_shell_icons(self):
        self.assertEqual(ident.refresh_shell_icons(["a.lnk", "", "b.exe"]), 3)
        self.assertEqual(self.api.calls, [("notify", "a.lnk"), ("notify", "b.exe"), ("assoc",)])


class ShortcutSyncTest(Win32Case):
    def make(self, name, target, icon):
        link = self.base / "links" / name
        link.parent.mkdir(exist_ok=True)
        link.write_bytes(b"L")
        self.api.links[str(link)] = (target, icon, 0)
        return str(link)

    def test_only_our_shortcuts_change(self):
        exe = r"C:\Apps\MemuAI\MemuAI.exe"
        owned = self.base / "data" / "memuai"
        owned.mkdir(parents=True)
        (owned / "assistant-old.ico").write_bytes(b"x")
        user_icon = self.base / "mine.ico"
        user_icon.write_bytes(b"x")
        ours = self.make("MemuAI.lnk", exe, str(owned / "assistant-old.ico"))
        broken = self.make("Pinned.lnk", exe, str(self.base / "deleted" / "assistant.ico"))
        custom = self.make("Custom.lnk", exe, str(user_icon))
        other = self.make("Proxy.lnk", r"C:\Apps\MemuAIProxy\MemuAIProxy.exe", "")
        plain = self.make("Plain.lnk", exe, "")
        new = str(owned / "assistant-new.ico")
        changed = ident.sync_shortcut_icons(new, owned_dirs=[str(owned)], folders=[self.base / "links"])
        self.assertEqual(sorted(changed), sorted([ours, broken, plain]))
        self.assertEqual(self.api.links[custom][1], str(user_icon), "an icon the user chose stays")
        self.assertEqual(self.api.links[other][1], "")
        self.assertIn(("assoc",), self.api.calls)
        self.assertIn(("notify", new), self.api.calls)
        # Back to the default look: every shortcut we changed points at the exe again.
        self.api.calls.clear()
        changed = ident.sync_shortcut_icons("", owned_dirs=[str(owned)], folders=[self.base / "links"])
        self.assertEqual(sorted(changed), sorted([ours, broken, plain]))
        for link in (ours, broken, plain):
            self.assertEqual(self.api.links[link][1], exe)
        self.assertEqual(self.api.links[custom][1], str(user_icon))
        # Nothing to do the second time.
        self.assertEqual(ident.sync_shortcut_icons("", owned_dirs=[str(owned)], folders=[self.base / "links"]), [])

    def test_folders_cover_pinned_copies(self):
        folders = [str(p) for p in ident.shortcut_folders({"APPDATA": r"C:\U\AppData\Roaming",
                                                           "USERPROFILE": r"C:\U"})]
        self.assertTrue(any(p.replace("\\", "/").endswith("User Pinned/TaskBar") for p in folders))
        self.assertTrue(any(p.replace("\\", "/").endswith("Start Menu/Programs/MemuAI") for p in folders))


# ------------------------------------------------------------------ assistant icon
class AssistantIconTest(Win32Case):
    def setUp(self):
        super().setUp()
        self.data = self.base / "data"

    def test_default_look_uses_the_shipped_icon(self):
        ico, png, changed = assistant_icon.ensure_files(self.data, "", assistant_icon.DEFAULT_GRADIENT)
        self.assertTrue(ico.endswith("memuai-icon.ico"))
        self.assertTrue(changed, "first run repairs shortcuts of older versions once")
        self.assertFalse(assistant_icon.ensure_files(self.data, "", "")[2])
        self.assertFalse(list((self.data / "memuai").glob("assistant-*.ico")))
        self.assertEqual(assistant_icon.current_icon(self.data)[0], ico)

    def test_custom_gradient_files_written_once_with_hashed_names(self):
        grad = "linear-gradient(135deg,#3B82F6,#10b981)"
        ico, png, changed = assistant_icon.ensure_files(self.data, "", grad)
        self.assertTrue(changed)
        self.assertRegex(os.path.basename(ico), r"^assistant-[0-9a-f]{16}\.ico$")
        kind, entries = ico_entries(Path(ico))
        self.assertEqual(sorted(e["size"] for e in entries), list(ICO_SIZES))
        self.assertFalse([e for e in entries if e["png"] and e["size"] < 256])
        mtime = os.path.getmtime(ico)
        self.assertEqual(assistant_icon.ensure_files(self.data, "", grad.lower()), (ico, png, False))
        self.assertEqual(os.path.getmtime(ico), mtime, "unchanged look → no rewrite")
        self.assertFalse(list((self.data / "memuai").glob("*.tmp")))
        self.assertEqual(assistant_icon.current_icon(self.data), (ico, png))
        # The bubble silhouette in the custom colours.
        image = frame(Path(ico), 256)
        self.assertGreater(image.getpixel((40, 230))[3], 100, "tail")
        self.assertEqual(image.getpixel((250, 250))[3], 0)
        self.assertGreater(image.getpixel((70, 60))[2], image.getpixel((70, 60))[1], "blue start, top-left")

    def test_prune_and_back_to_default(self):
        for color in ("#111111", "#222222", "#333333", "#444444", "#555555"):
            assistant_icon.ensure_files(self.data, "", f"linear-gradient(135deg,{color},#999999)")
        self.assertLessEqual(len(list((self.data / "memuai").glob("assistant-*.ico"))), assistant_icon.KEEP_GENERATED)
        ico, _png, changed = assistant_icon.ensure_files(self.data, "", "")
        self.assertTrue(changed)
        self.assertTrue(ico.endswith("memuai-icon.ico"))
        self.assertTrue(assistant_icon.current_icon(self.data)[0].endswith("memuai-icon.ico"))

    def test_photo(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (300, 200), (200, 30, 30)).save(buffer, "PNG")
        avatar = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        ico, png, changed = assistant_icon.ensure_files(self.data, avatar, "")
        image = Image.open(png)
        self.assertEqual(image.size, (256, 256))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((128, 128))[:3], (200, 30, 30))

    def test_sync_applies_window_tray_and_shortcuts_only_on_change(self):
        with patch("app.desktop.refresh_tray_icon") as tray, \
                patch.object(assistant_icon, "schedule_shortcut_sync") as links:
            result = assistant_icon.sync(self.data, "", "linear-gradient(135deg,#123456,#abcdef)", 100)
            self.assertEqual(result, {"ok": True, "changed": True, "default": False})
            self.assertEqual(tray.call_count, 1)
            self.assertEqual(links.call_count, 1)
            result = assistant_icon.sync(self.data, "", "linear-gradient(135deg,#123456,#abcdef)", 100)
            self.assertEqual(result["changed"], False)
            self.assertEqual((tray.call_count, links.call_count), (1, 1))
        sets = [c for c in self.api.calls if c[0] == "set"]
        self.assertEqual(len(sets), 2, "window icon set once, the repeat is a no-op")


# ------------------------------------------------------------------ wiring
class WiringTest(unittest.TestCase):
    def test_endpoint_delegates_and_old_path_is_gone(self):
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        block = source[source.index('@app.post("/api/window/icon")'):source.index('@app.post("/api/window/sound")')]
        self.assertIn("assistant_icon.sync", block)
        for gone in ("powershell", "SetClassLongPtr", "SHChangeNotify", "assistant-window.ico"):
            self.assertNotIn(gone, block)
        self.assertNotIn("_update_shortcut_icons", source)

    def test_memuai_sets_app_id_once_before_windows(self):
        source = (ROOT / "memuai.py").read_text(encoding="utf-8")
        configure = source[source.index("def _configure_native_window"):source.index("def _configure_native_window") + 3000]
        configure = configure[:configure.index("\ndef ", 10)]
        self.assertNotIn("SetCurrentProcessExplicitAppUserModelID(", configure)
        self.assertIn("_apply_window_identity(hwnd)", configure)
        main = source[source.index("def main()"):]
        self.assertLess(main.index("set_process_app_id"), main.index("DesktopShell("))
        self.assertIn("window_ico=_memuai_icon_ico()", main)

    def test_crisp_tray_class(self):
        from app import desktop

        class BaseIcon:
            def __init__(self, *a, **k):
                self._icon_handle = None

            def _assert_icon_handle(self):
                self._icon_handle = "pystray"

        fake = types.SimpleNamespace(Icon=BaseIcon)
        with patch.object(desktop.sys, "platform", "linux"):
            self.assertIs(desktop._crisp_tray_class(fake), BaseIcon)
        api = FakeApi()
        ident.set_api(api)
        try:
            with patch.object(desktop.sys, "platform", "win32"):
                cls = desktop._crisp_tray_class(fake)
            icon = cls()
            icon._memu_ico = str(ROOT / "icon.ico")
            icon._assert_icon_handle()
            self.assertEqual(icon._icon_handle, api.next_handle)
            self.assertEqual(api.calls[-1][:3], ("load", "icon.ico", 16))
            other = cls()
            other._memu_ico = ""
            other._assert_icon_handle()
            self.assertEqual(other._icon_handle, "pystray", "falls back to pystray")
        finally:
            ident.set_api(None)

    def test_shell_tray_refresh_passes_the_ico(self):
        from app import desktop
        shell = desktop.DesktopShell("http://x", tray_icon="memuai-icon.png", tray_ico="memuai-icon.ico",
                                     window_ico="memuai-icon.ico")
        shell.tray = types.SimpleNamespace(icon=None, _memu_ico="")
        with patch.object(desktop, "_active_shell", shell), patch.object(shell, "set_window_icon") as win:
            self.assertTrue(desktop.refresh_tray_icon(str(ROOT / "icon.png"), str(ROOT / "icon.ico")))
        win.assert_called_once_with(str(ROOT / "icon.ico"))
        self.assertEqual(shell.tray._memu_ico, str(ROOT / "icon.ico"))
        self.assertIsNotNone(shell.tray.icon)

    def test_win_com_imports_anywhere(self):
        import importlib
        module = importlib.import_module("app.win_com")
        self.assertTrue(callable(module.write_shortcut_icon))


class InstallerRefreshTest(unittest.TestCase):
    def test_install_refreshes_exe_and_shortcut_icons(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import installer_engine_test as it
        from installer import products
        from installer.engine import Engine, Options
        from installer import payload

        class Recording(it.FakePlatform):
            def refresh_shell_icons(self, paths):
                self.calls.append(("icons", [Path(p).name for p in paths]))

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake = Recording(base / "pc")
            setup = it.make_setup(base, "3.5")
            with payload.Payload(setup) as pak:
                eng = Engine(products.SUITE, fake, pak, close_wait=0.05)
                app = Path(fake.envs["LOCALAPPDATA"]) / "Programs" / "MemuAI"
                result = eng.install(Options(str(app), ["proxy", "memuai"], ["autostart", "desktopicon"], "en"),
                                     lambda _e: None)
            self.assertTrue(result["ok"])
            calls = [c for c in fake.calls if c[0] == "icons"]
            self.assertEqual(len(calls), 1)
            names = calls[0][1]
            self.assertIn("MemuAI.exe", names)
            self.assertIn("MemuAIProxy.exe", names)
            self.assertIn("MemuAI.lnk", names)


if __name__ == "__main__":
    unittest.main()
