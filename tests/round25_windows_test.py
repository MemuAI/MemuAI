"""Round 25 (b7): Windows-native code checked off Windows.

* Struct layouts of every ctypes binding layer, built under an LLP64 emulation (``long``/``DWORD``/``BOOL``
  32-bit, ``WCHAR`` 16-bit, pointers 64-bit) and compared with the sizes ``sizeof`` gives in a 64-bit Windows
  build: a wrong ``cbSize`` makes SendInput / GetMonitorInfoW / SHQueryRecycleBinW fail silently.
* Startup window colour = the page's first-paint ``--bg`` (no flash of another shade).
* Lazily imported round-25 modules and new data are part of the frozen build.
* Typing plan of the Quick Ask ``type_text`` action (surrogate pairs, lone surrogates, Enter/Tab).
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

X64 = ctypes.sizeof(ctypes.c_void_p) == 8


class _FakeFunction:
    """Stands in for a DLL export: accepts ``argtypes``/``restype`` like the real one."""

    def __call__(self, *args, **kwargs):  # pragma: no cover - never called by these tests
        raise AssertionError("a Win32 function was called off Windows")


class _FakeDLL:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __getattr__(self, name: str) -> _FakeFunction:
        if name.startswith("__"):
            raise AttributeError(name)
        function = _FakeFunction()
        setattr(self, name, function)
        return function


def _llp64_wintypes() -> types.ModuleType:
    """``ctypes.wintypes`` with the widths of Windows (Linux' own uses 64-bit ``long``)."""
    wt = types.ModuleType("ctypes.wintypes")
    LONG, DWORD = ctypes.c_int32, ctypes.c_uint32
    handle = ctypes.c_void_p
    values = dict(BYTE=ctypes.c_ubyte, WORD=ctypes.c_ushort, DWORD=DWORD, LONG=LONG, ULONG=DWORD,
                  BOOL=ctypes.c_int32, BOOLEAN=ctypes.c_ubyte, INT=ctypes.c_int, UINT=ctypes.c_uint,
                  SHORT=ctypes.c_short, USHORT=ctypes.c_ushort, WCHAR=ctypes.c_uint16,
                  WPARAM=ctypes.c_size_t, LPARAM=ctypes.c_ssize_t, LPCWSTR=ctypes.c_wchar_p,
                  LPWSTR=ctypes.c_wchar_p, LPVOID=ctypes.c_void_p, ATOM=ctypes.c_ushort)
    for name in ("HANDLE", "HWND", "HDC", "HMONITOR", "HGLOBAL", "HINSTANCE", "HMODULE", "HICON", "HCURSOR",
                 "HBRUSH", "HMENU", "HBITMAP", "HGDIOBJ", "HRGN", "HKEY", "HLOCAL"):
        values[name] = handle
    for name, value in values.items():
        setattr(wt, name, value)

    class RECT(ctypes.Structure):
        _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", LONG), ("y", LONG)]

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]

    class MSG(ctypes.Structure):
        _fields_ = [("hWnd", handle), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t), ("time", DWORD), ("pt", POINT)]

    wt.RECT, wt.POINT, wt.FILETIME, wt.MSG = RECT, POINT, FILETIME, MSG
    return wt


@contextlib.contextmanager
def windows_x64_ctypes():
    """Run a binding layer's constructor here as if on 64-bit Windows (layouts only, nothing is called)."""
    wt = _llp64_wintypes()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(ctypes, "WinDLL", _FakeDLL, create=True))
        stack.enter_context(mock.patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True))
        stack.enter_context(mock.patch.object(ctypes, "c_long", ctypes.c_int32))
        stack.enter_context(mock.patch.object(ctypes, "c_ulong", ctypes.c_uint32))
        stack.enter_context(mock.patch.object(ctypes, "wintypes", wt, create=True))
        stack.enter_context(mock.patch.dict(sys.modules, {"ctypes.wintypes": wt}))
        yield wt


@unittest.skipUnless(X64, "the expected sizes are the 64-bit Windows ones")
class StructLayoutsX64(unittest.TestCase):
    """Sizes from a 64-bit MSVC build of <windows.h> (sizeof)."""

    def test_assistant_actions_bindings(self):
        from app import assistant_actions_win as aw
        with windows_x64_ctypes():
            w = aw._W()
        sizes = {"INPUT": 40, "KEYBDINPUT": 24, "MONITORINFOEXW": 104, "WINDOWPLACEMENT": 44,
                 "MEMORYSTATUSEX": 64, "SYSTEM_POWER_STATUS": 12, "SHQUERYRBINFO": 24,
                 "PHYSICAL_MONITOR": 264, "GUID": 16}
        for name, expected in sizes.items():
            with self.subTest(struct=name):
                self.assertEqual(ctypes.sizeof(getattr(w, name)), expected)
        self.assertEqual(w.guid("BCDE0395-E52F-467C-8E3D-C4579291692E").Data1, 0xBCDE0395)

    def test_automation_runtime_input(self):
        from app import automation_runtime as ar
        types_ = ar._win_input_types()
        self.assertEqual(ctypes.sizeof(types_["INPUT"]), 40)
        self.assertEqual(ctypes.sizeof(types_["MOUSEINPUT"]), 32)
        self.assertEqual(ctypes.sizeof(types_["KEYBDINPUT"]), 24)
        self.assertEqual(ar._utf16_units("\ud83d"), [0xD83D])   # lone surrogate: no UnicodeEncodeError

    def test_titlebar_bindings(self):
        from app import win_titlebar
        with windows_x64_ctypes():
            api = win_titlebar.Win32Api()
        for name, expected in {"RECT": 16, "POINT": 8, "MARGINS": 16, "APPBARDATA": 48, "MONITORINFO": 40}.items():
            with self.subTest(struct=name):
                self.assertEqual(ctypes.sizeof(getattr(api, name)), expected)

    def test_hotkey_message_struct(self):
        from app import win_hotkey
        with windows_x64_ctypes():
            api = win_hotkey._Win32()
        msg_type = api.GetMessageW.argtypes[0]._type_
        self.assertEqual(ctypes.sizeof(msg_type), 48)   # MSG incl. lPrivate

    def test_companion_window_bindings(self):
        from app import companion_window
        with windows_x64_ctypes():
            api = companion_window._Win32()
        self.assertEqual(ctypes.sizeof(api.POINT), 8)
        self.assertEqual(ctypes.sizeof(api.MONITORINFO), 40)
        # The gaze feed reads physical pixels whatever the thread's DPI context.
        self.assertIsNotNone(api.GetPhysicalCursorPos)


class TypingPlan(unittest.TestCase):
    def test_unicode_enter_tab_and_surrogates(self):
        from app.assistant_actions_win import text_key_plan
        self.assertEqual(text_key_plan("Я\r\nb\rc\td"),
                         [("unit", ord("Я")), ("vk", 0x0D), ("unit", ord("b")), ("vk", 0x0D),
                          ("unit", ord("c")), ("vk", 0x09), ("unit", ord("d"))])
        self.assertEqual(text_key_plan("😀"), [("unit", 0xD83D), ("unit", 0xDE00)])
        # A lone surrogate (broken JSON from a model) used to raise and drop the whole text.
        self.assertEqual(text_key_plan("a\ud83d"), [("unit", ord("a")), ("unit", 0xD83D)])

    def test_numlock_and_numpad_divide_are_extended(self):
        from app import assistant_actions_win as aw
        from app import automation_runtime as ar
        for vk in (0x90, 0x6F, 0x5C):
            self.assertIn(vk, aw.EXTENDED)
            self.assertIn(vk, ar._EXTENDED_VK)


class StartupBackground(unittest.TestCase):
    def _background(self, prefs):
        import memuai
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "memuai"
            folder.mkdir()
            if prefs is not None:
                (folder / "preferences.json").write_text(json.dumps(prefs), encoding="utf-8")
            with mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": tmp}):
                theme = memuai._startup_theme()
                return theme, memuai._startup_background(theme)

    def test_matches_the_first_paint_of_the_page(self):
        from app import themes
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        # index.html falls back to these when it has no cached --bg; the native window must agree.
        self.assertEqual(self._background(None), ("dark", themes.variant_tokens("dark")["--bg"]))
        self.assertIn('"' + themes.variant_tokens("dark")["--bg"] + '"', html)
        self.assertEqual(self._background({"theme": "light"}), ("light", themes.variant_tokens("light")["--bg"]))
        self.assertIn('"' + themes.variant_tokens("light")["--bg"] + '"', html)

    def test_follows_the_chosen_variant(self):
        from app import themes
        for mode in ("dark", "light"):
            for row in themes.VARIANTS[mode]:
                with self.subTest(mode=mode, variant=row["id"]):
                    _theme, colour = self._background({"theme": mode, f"surface_{mode}": row["id"]})
                    self.assertEqual(colour, themes.variant_tokens(mode, row["id"])["--bg"])

    def test_broken_preferences_fall_back(self):
        _theme, colour = self._background({"theme": "dark", "surface_dark": "custom:missing-theme"})
        self.assertRegex(colour, r"^#[0-9a-fA-F]{6}$")

    def test_proxy_window_matches_its_panel(self):
        import inspect
        from app import desktop
        css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
        default = inspect.signature(desktop.DesktopShell.__init__).parameters["background_color"].default
        self.assertIn(f"--bg: {default};", css)


class FrozenBuildContents(unittest.TestCase):
    def setUp(self):
        import build
        self.targets = build.TARGETS

    def test_round25_lazy_modules_are_hidden_imports(self):
        hidden = set(self.targets["memuai"]["hidden"])
        for module in ("app.assistant_r25", "app.tts_providers"):
            self.assertIn(module, hidden)
        # Every assistant/quick/DFY/panels module is named: they are imported inside functions.
        for path in sorted((ROOT / "app").glob("*.py")):
            if path.stem.startswith(("assistant_", "quick_ask", "dfy", "panels")):
                with self.subTest(module=path.stem):
                    self.assertIn(f"app.{path.stem}", hidden)
        self.assertIn("app.tts_providers", self.targets["proxy"]["hidden"])   # Telegram voice replies

    def test_new_static_files_and_docs_are_in_bundled_folders(self):
        data = {item.split(";")[0] for item in self.targets["memuai"]["data"]}
        self.assertIn("app/pgstatic", data)
        self.assertIn("app/docs", data)
        self.assertTrue((ROOT / "app" / "pgstatic" / "voice-settings.js").is_file())
        index = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
        text = json.dumps(index, ensure_ascii=False)
        for page in sorted((ROOT / "app" / "docs" / "ru").glob("*.md")):
            with self.subTest(page=page.name):
                self.assertTrue((ROOT / "app" / "docs" / "en" / page.name).is_file())
                self.assertIn(page.stem, text)

    def test_checked_in_specs_agree(self):
        for spec, target in (("MemuAI.spec", "memuai"), ("MemuAIProxy.spec", "proxy")):
            source = (ROOT / spec).read_text(encoding="utf-8")
            with self.subTest(spec=spec):
                self.assertIn("'app.tts_providers'", source)
        self.assertIn("'app.assistant_r25'", (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
