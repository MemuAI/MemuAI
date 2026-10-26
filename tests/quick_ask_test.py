"""«Быстрый доступ» (Quick Ask, Alt+Space): everything that can be checked off Windows.

* hotkey strings: parsing, canonical form, validation, warnings, conflicts;
* the global-hotkey thread (app/win_hotkey.py) against a fake Win32 layer:
  registration, "already registered" (1409) with the previous hotkey kept,
  probing, WM_HOTKEY → callback, shutdown;
* window placement on multi-monitor desktops: negative coordinates, mixed
  DPIs, taskbars on every edge, the cursor in a gap, flipping above the
  cursor, height changes clamped to the work area;
* preference normalisation/migration and its wiring into pgdata;
* the system prompt, the hidden tool protocol (streaming filter + parser)
  and the whole answer loop with a fake Proxy, fake web search and a fake
  screen capture;
* the HTTP routes and the static page/settings wiring;
* the instant calculator (quick-calc.js) through Node, when Node exists.

Not verifiable here (Windows only): RegisterHotKey itself, focus stealing,
WinForms/WebView2 behaviour, DWM backdrop/corners, SetWindowDisplayAffinity.
"""
from __future__ import annotations

import asyncio
import json
import queue
import shutil
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app import quick_ask_core as core
from app import quick_ask_api
from app.win_hotkey import ERROR_HOTKEY_ALREADY_REGISTERED, HOTKEY_ID, WM_APP_HOTKEY, WM_HOTKEY, WM_QUIT, HotkeyListener

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

class HotkeyParsingTests(unittest.TestCase):
    def test_canonical_forms_and_aliases(self) -> None:
        self.assertEqual(core.parse_hotkey("Alt+Space"), (core.MOD_ALT, 0x20, "Alt+Space"))
        self.assertEqual(core.parse_hotkey(" alt + space ")[2], "Alt+Space")
        self.assertEqual(core.parse_hotkey("space+alt")[2], "Alt+Space")
        self.assertEqual(core.parse_hotkey("shift+control+space")[2], "Ctrl+Shift+Space")
        self.assertEqual(core.parse_hotkey("Win+Alt+K")[2], "Alt+Win+K")
        self.assertEqual(core.parse_hotkey("Ctrl+Alt+q"), (core.MOD_CONTROL | core.MOD_ALT, ord("Q"), "Ctrl+Alt+Q"))
        self.assertEqual(core.parse_hotkey("Alt+Backquote")[2], "Alt+`")
        self.assertEqual(core.parse_hotkey("Alt+`")[1], 0xC0)
        self.assertEqual(core.parse_hotkey("Ctrl+Alt+F5")[1], 0x74)
        self.assertEqual(core.parse_hotkey("Ctrl+Numpad7")[2], "Ctrl+Num7")
        self.assertEqual(core.parse_hotkey("Ctrl+Alt+KeyJ")[2], "Ctrl+Alt+J")
        self.assertEqual(core.parse_hotkey("F13")[2], "F13")
        self.assertEqual(core.parse_hotkey("Ctrl++")[2], "Ctrl+=")

    def test_invalid_hotkeys_have_stable_codes(self) -> None:
        cases = {"": "empty", "Ctrl+Alt": "no_key", "Q": "need_modifier", "Space": "need_modifier",
                 "Shift+A": "shift_only", "Alt+Q+W": "two_keys", "Alt+Banana": "unknown_key",
                 "Win+L": "reserved", "Alt+Tab": "reserved", "Alt+F4": "reserved", "Ctrl+C": "editing",
                 "Ctrl+V": "editing", "AltGr+Q": "unknown_key"}
        for text, code in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(core.HotkeyError) as caught:
                    core.parse_hotkey(text)
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(str(caught.exception))

    def test_normalize_falls_back_to_default(self) -> None:
        self.assertEqual(core.normalize_hotkey("ctrl+shift+space"), "Ctrl+Shift+Space")
        self.assertEqual(core.normalize_hotkey("Ctrl+C"), core.DEFAULT_HOTKEY)
        self.assertEqual(core.normalize_hotkey(None), core.DEFAULT_HOTKEY)
        for alternative in core.ALTERNATIVE_HOTKEYS:
            self.assertEqual(core.normalize_hotkey(alternative, ""), alternative)

    def test_warnings_document_the_trade_offs(self) -> None:
        codes = [w["code"] for w in core.hotkey_warnings("Alt+Space")]
        self.assertEqual(codes, ["system_menu"])
        self.assertIn("системного меню", core.hotkey_warnings("Alt+Space")[0]["text"])
        self.assertIn("window menu", core.hotkey_warnings("Alt+Space", "en")[0]["text"])
        self.assertIn("altgr", [w["code"] for w in core.hotkey_warnings("Ctrl+Alt+Q")])
        self.assertIn("ime", [w["code"] for w in core.hotkey_warnings("Ctrl+Shift+Space")])
        self.assertIn("win", [w["code"] for w in core.hotkey_warnings("Alt+Win+K")])
        self.assertEqual(core.hotkey_warnings("not a key"), [])

    def test_browser_capture_matches_python(self) -> None:
        self.assertEqual(core.hotkey_from_event("Space", alt=True), "Alt+Space")
        self.assertEqual(core.hotkey_from_event("KeyQ", ctrl=True, alt=True), "Ctrl+Alt+Q")
        self.assertEqual(core.hotkey_from_event("Backquote", alt=True), "Alt+`")
        self.assertEqual(core.hotkey_from_event("Digit5", ctrl=True, shift=True), "Ctrl+Shift+5")
        self.assertEqual(core.hotkey_from_event("Numpad3", ctrl=True), "Ctrl+Num3")
        self.assertEqual(core.parse_hotkey(core.hotkey_from_event("ArrowUp", ctrl=True, alt=True))[2], "Ctrl+Alt+Up")

        source = (STATIC / "quick-settings.js").read_text(encoding="utf-8")
        for code, name in (("Backquote", "`"), ("Space", "Space"), ("ArrowUp", "Up"), ("BracketLeft", "[")):
            self.assertIn(f'{code}: "{name}"', source)

    def test_conflicting_launchers_are_reported(self) -> None:
        running = ["explorer.exe", "PowerToys.PowerLauncher.exe", "flow.launcher.exe"]
        found = core.conflicts_for("Alt+Space", running)
        self.assertEqual({row["program"] for row in found}, {"PowerToys Run", "Flow Launcher"})
        self.assertEqual(core.conflicts_for("Ctrl+Alt+Space", running), [])
        self.assertEqual(core.conflicts_for("Alt+Space", ["explorer.exe"]), [])

class FakeWin32:
    """Enough of user32/kernel32 for HotkeyListener: a message queue per thread."""

    def __init__(self, taken=()) -> None:
        import ctypes

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                        ("lParam", ctypes.c_ssize_t)]

        self.ctypes = ctypes
        self.MSG = MSG
        self.queue: "queue.Queue" = queue.Queue()
        self.registered = {}
        self.taken = set(taken)
        self.error = 0
        self.calls = []

    def PeekMessageW(self, *_args):
        return 0

    def GetCurrentThreadId(self):
        return 4242

    def PostThreadMessageW(self, _thread, message, wparam, lparam):
        self.queue.put((message, wparam, lparam))
        return 1

    def GetMessageW(self, msg_ref, *_args):
        message, wparam, _lparam = self.queue.get()
        msg = msg_ref._obj
        msg.message, msg.wParam = message, wparam
        return 0 if message == WM_QUIT else 1

    def RegisterHotKey(self, _hwnd, ident, mods, vk):
        self.calls.append(("register", ident, mods, vk))
        if (mods & 0x0F, vk) in self.taken or ident in self.registered:
            self.error = ERROR_HOTKEY_ALREADY_REGISTERED
            return 0
        self.registered[ident] = (mods, vk)
        return 1

    def UnregisterHotKey(self, _hwnd, ident):
        self.calls.append(("unregister", ident))
        return 1 if self.registered.pop(ident, None) else 0

    def last_error(self):
        return self.error

class HotkeyListenerTests(unittest.TestCase):
    def make(self, taken=()):
        fake = FakeWin32(taken)
        fired = threading.Event()
        listener = HotkeyListener(fired.set, api_factory=lambda: fake)
        self.addCleanup(listener.stop)
        return listener, fake, fired

    def test_register_change_fire_and_stop(self) -> None:
        listener, fake, fired = self.make()
        status = listener.set_hotkey("alt+space")
        self.assertTrue(status["ok"], status)
        self.assertEqual(listener.active, "Alt+Space")
        self.assertEqual(fake.registered[HOTKEY_ID], (core.MOD_ALT | core.MOD_NOREPEAT, 0x20))
        fake.queue.put((WM_HOTKEY, HOTKEY_ID, 0))
        self.assertTrue(fired.wait(2), "WM_HOTKEY did not reach the callback")
        status = listener.set_hotkey("Ctrl+Alt+Space")
        self.assertTrue(status["ok"])
        self.assertEqual(fake.registered[HOTKEY_ID][1], 0x20)
        self.assertEqual(fake.registered[HOTKEY_ID][0] & 0x0F, core.MOD_CONTROL | core.MOD_ALT)
        listener.stop()
        self.assertEqual(fake.registered, {}, "the hotkey must be released when the thread stops")

    def test_busy_hotkey_keeps_the_previous_one(self) -> None:
        listener, fake, _ = self.make(taken={(core.MOD_CONTROL | core.MOD_SHIFT, 0x20)})
        self.assertTrue(listener.set_hotkey("Alt+Space")["ok"])
        failure = listener.set_hotkey("Ctrl+Shift+Space")
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["code"], "busy")
        self.assertIn("занято", failure["error"])
        self.assertEqual(failure.get("kept"), "Alt+Space")
        self.assertEqual(listener.active, "Alt+Space")
        self.assertEqual(listener.status["rejected"], "Ctrl+Shift+Space")
        self.assertEqual(fake.registered[HOTKEY_ID][1], 0x20)

    def test_probe_does_not_touch_the_active_hotkey(self) -> None:
        listener, fake, _ = self.make(taken={(core.MOD_ALT, ord("Q"))})
        listener.set_hotkey("Alt+Space")
        self.assertEqual(listener.probe("Ctrl+Alt+Space")["code"], "free")
        self.assertEqual(listener.probe("Alt+Q")["code"], "busy")
        self.assertEqual(listener.probe("Alt+Space")["code"], "ours")
        self.assertEqual(listener.probe("Ctrl+C")["code"], "editing")
        self.assertEqual(listener.active, "Alt+Space")
        self.assertEqual(set(fake.registered), {HOTKEY_ID})

    def test_invalid_text_never_reaches_win32_and_clear_releases(self) -> None:
        listener, fake, _ = self.make()
        self.assertEqual(listener.set_hotkey("Q")["code"], "need_modifier")
        self.assertEqual(fake.calls, [])
        listener.set_hotkey("Alt+Space")
        self.assertEqual(listener.clear()["code"], "off")
        self.assertEqual(fake.registered, {})

def monitor(rect, work=None, dpi=96, primary=False):
    return {"rect": rect, "work": work or rect, "dpi": dpi, "primary": primary}

class PlacementTests(unittest.TestCase):
    def assertInside(self, geometry, work):
        """The window and every height it may grow to stay inside the work area."""
        left, top, right, bottom = work
        self.assertGreaterEqual(geometry["x"], left)
        self.assertGreaterEqual(geometry["y"], top)
        self.assertLessEqual(geometry["x"] + geometry["w"], right)
        self.assertLessEqual(geometry["y"] + geometry["h"], bottom)
        if geometry["anchor"] == "bottom":
            self.assertGreaterEqual(geometry["bottom"] - geometry["max_h"], top)
            self.assertLessEqual(geometry["bottom"], bottom)
        else:
            self.assertLessEqual(geometry["y"] + geometry["max_h"], bottom)

    def test_single_full_hd_monitor_centered_upper_third(self) -> None:
        screens = [monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), primary=True)]
        g = core.place(screens, (300, 300))
        self.assertEqual((g["w"], g["h"]), (720, core.COLLAPSED_HEIGHT))
        self.assertEqual(g["x"], (1920 - 720) // 2)
        self.assertEqual(g["y"], round(1040 * core.TOP_RATIO))
        self.assertEqual(g["anchor"], "top")
        self.assertInside(g, (0, 0, 1920, 1040))
        self.assertLessEqual(g["max_h"], core.MAX_HEIGHT)

    def test_secondary_monitor_left_of_primary_with_its_own_dpi(self) -> None:
        screens = [monitor((0, 0, 3840, 2160), (0, 0, 3840, 2100), dpi=144, primary=True),
                   monitor((-1920, 0, 0, 1080), (-1920, 0, 0, 1032), dpi=96)]
        g = core.place(screens, (-400, 500))
        self.assertEqual(g["monitor"], 1)
        self.assertEqual(g["w"], 720)
        self.assertEqual(g["x"], -1920 + (1920 - 720) // 2)
        self.assertInside(g, (-1920, 0, 0, 1032))
        g = core.place(screens, (2000, 1000))
        self.assertEqual(g["monitor"], 0)
        self.assertEqual(g["scale"], 1.5)
        self.assertEqual(g["w"], 1080)
        self.assertEqual(g["h"], round(core.COLLAPSED_HEIGHT * 1.5))
        self.assertInside(g, (0, 0, 3840, 2100))

    def test_monitor_above_the_primary_negative_y(self) -> None:
        screens = [monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), primary=True),
                   monitor((-320, -1440, 2240, 0), (-320, -1440, 2240, -40), dpi=120)]
        g = core.place(screens, (500, -700))
        self.assertEqual(g["monitor"], 1)
        self.assertLess(g["y"], 0)
        self.assertInside(g, (-320, -1440, 2240, -40))
        self.assertEqual(g["w"], 900)

    def test_taskbar_on_every_edge(self) -> None:
        full = (0, 0, 1920, 1080)
        for work in ((0, 0, 1920, 1032), (0, 48, 1920, 1080), (62, 0, 1920, 1080), (0, 0, 1858, 1080)):
            with self.subTest(work=work):
                g = core.place([monitor(full, work, primary=True)], (900, 500))
                self.assertInside(g, work)
                self.assertEqual(g["x"], work[0] + (work[2] - work[0] - 720) // 2)
                g = core.place([monitor(full, work, primary=True)], (5, 1075), mode="cursor")
                self.assertInside(g, work)

    def test_cursor_mode_flips_above_near_the_bottom(self) -> None:
        screens = [monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), primary=True)]
        below = core.place(screens, (960, 200), mode="cursor")
        self.assertEqual(below["anchor"], "top")
        self.assertEqual(below["y"], 200 + core.CURSOR_GAP)
        above = core.place(screens, (1900, 1000), mode="cursor")
        self.assertEqual(above["anchor"], "bottom")
        self.assertEqual(above["bottom"], 1000 - core.CURSOR_GAP)
        self.assertLessEqual(above["x"] + above["w"], 1920 - core.SCREEN_MARGIN)
        self.assertInside(above, (0, 0, 1920, 1040))
        grown = core.resize(above, 500)
        self.assertEqual(grown["y"] + grown["h"], above["bottom"], "an upward popup keeps its bottom edge")
        self.assertGreaterEqual(grown["y"], 0)

    def test_cursor_in_a_gap_uses_the_nearest_monitor(self) -> None:
        screens = [monitor((0, 0, 1920, 1080), primary=True), monitor((2000, 0, 3920, 1080))]
        self.assertEqual(core.pick_monitor(screens, 1990, 500), 1)
        self.assertEqual(core.pick_monitor(screens, 1930, 500), 0)
        self.assertEqual(core.pick_monitor(screens, -5000, -5000), 0)

    def test_small_screen_clamps_width_and_height(self) -> None:
        g = core.place([monitor((0, 0, 640, 480), (0, 0, 640, 440), primary=True)], (10, 10), width=1100)
        self.assertLessEqual(g["w"], 640)
        self.assertInside(g, (0, 0, 640, 440))
        tall = core.resize(g, 5000)
        self.assertEqual(tall["h"], g["max_h"])

    def test_resize_keeps_top_and_respects_bounds(self) -> None:
        g = core.place([monitor((0, 0, 1920, 1080), (0, 0, 1920, 1040), primary=True)], (0, 0))
        grown = core.resize(g, 420)
        self.assertEqual(grown["y"], g["y"])
        self.assertEqual(grown["h"], 420)
        self.assertEqual(core.resize(g, 9999)["h"], g["max_h"])
        self.assertEqual(core.resize(g, 1)["h"], core.MIN_HEIGHT)
        wider = core.resize(g, 200, width_css=900)
        self.assertEqual(wider["w"], 900)
        self.assertEqual(wider["x"] + wider["w"] // 2, g["x"] + g["w"] // 2)
        self.assertEqual(core.resize(g, 200, width_css=5000)["w"], core.MAX_WIDTH)
        hidpi = core.place([monitor((0, 0, 2560, 1440), (0, 0, 2560, 1392), dpi=192, primary=True)], (0, 0))
        self.assertEqual(core.resize(hidpi, 300)["h"], 600)

    def test_ease_steps(self) -> None:
        steps = core.ease_steps(100, 400, 9)
        self.assertEqual(len(steps), 9)
        self.assertEqual(steps[-1], 400)
        self.assertEqual(steps, sorted(steps))
        self.assertGreater(steps[0] - 100, (400 - 100) / 9, "ease-out: the first step is the biggest")

    def test_capture_bbox(self) -> None:
        screens = [monitor((0, 0, 1920, 1080), primary=True), monitor((-1280, -200, 0, 824))]
        self.assertEqual(core.capture_bbox(screens, "cursor", 1), (-1280, -200, 0, 824))
        self.assertEqual(core.capture_bbox(screens, "all", 0), (-1280, -200, 1920, 1080))
        self.assertEqual(core.capture_bbox(screens, 1, 1), (0, 0, 1920, 1080))
        self.assertEqual(core.capture_bbox(screens, 9, 1), (-1280, -200, 0, 824))
        region = core.screenshot_args({"region": {"left": 0.5, "top": 0, "width": 0.5, "height": 0.5}})["region"]
        self.assertEqual(core.capture_bbox(screens, 1, 0, region), (960, 0, 1920, 540))
        self.assertEqual(core.screenshot_args({"monitor": "ALL"})["monitor"], "all")
        self.assertEqual(core.screenshot_args({"monitor": "2"})["monitor"], 2)
        self.assertEqual(core.screenshot_args({"monitor": "nonsense"})["monitor"], "cursor")
        self.assertEqual(core.screenshot_args({"region": {"left": 50, "top": 50, "width": 50, "height": 50}})["region"],
                         {"left": 0.5, "top": 0.5, "width": 0.5, "height": 0.5})

class PreferenceTests(unittest.TestCase):
    def test_defaults_are_registered_in_pgdata(self) -> None:
        from app.pgdata import DEFAULT_PREFERENCES, _normalize_preferences
        for key, value in core.PREF_DEFAULTS.items():
            self.assertIn(key, DEFAULT_PREFERENCES)
            self.assertEqual(DEFAULT_PREFERENCES[key], value)
        prefs = _normalize_preferences({})
        self.assertEqual(prefs["quick_ask_hotkey"], "Alt+Space")
        self.assertTrue(prefs["quick_ask_enabled"])
        self.assertEqual(prefs["quick_ask_model"], "")

        self.assertTrue(prefs["quick_ask_selection"])
        self.assertTrue(prefs["quick_ask_selection_clipboard"])

    def test_migration_of_odd_values(self) -> None:
        from app.pgdata import _normalize_preferences
        prefs = _normalize_preferences({"quick_ask_hotkey": "control + alt + space", "quick_ask_position": "near_cursor",
                                        "quick_ask_style": "short", "quick_ask_backdrop": "rainbow",
                                        "quick_ask_enabled": "false", "quick_ask_width": 5000, "quick_ask_model": " gpt "})
        self.assertEqual(prefs["quick_ask_hotkey"], "Ctrl+Alt+Space")
        self.assertEqual(prefs["quick_ask_position"], "cursor")
        self.assertEqual(prefs["quick_ask_style"], "brief")
        self.assertEqual(prefs["quick_ask_backdrop"], "auto")
        self.assertFalse(prefs["quick_ask_enabled"])
        self.assertEqual(prefs["quick_ask_model"], "gpt")
        self.assertNotIn("quick_ask_width", prefs, "the remembered width lives in quick_state.json")
        self.assertEqual(_normalize_preferences({"quick_ask_hotkey": "Ctrl+C"})["quick_ask_hotkey"], "Alt+Space")

    def test_save_preferences_round_trip(self) -> None:
        from app.pgdata import load_preferences, save_preferences
        saved = save_preferences({"quick_ask_hotkey": "alt+q", "quick_ask_style": "detailed", "theme": "dark"})
        self.assertEqual(saved["quick_ask_hotkey"], "Alt+Q")
        self.assertEqual(load_preferences()["quick_ask_style"], "detailed")
        save_preferences({"quick_ask_hotkey": "Alt+Space", "quick_ask_style": "brief"})

    def test_model_resolution_order(self) -> None:
        prefs = {"quick_ask_model": "quick", "default_model": "main"}
        self.assertEqual(quick_ask_api.resolve_model({"model": "picked"}, prefs), "picked")
        self.assertEqual(quick_ask_api.resolve_model({}, prefs), "quick")
        self.assertEqual(quick_ask_api.resolve_model({}, {"quick_ask_model": "", "default_model": "main"}), "main")
        self.assertEqual(quick_ask_api.resolve_model({}, {}), "")

    def test_backdrop_choice(self) -> None:
        from app.quick_ask import backdrop_kind
        self.assertEqual(backdrop_kind("auto", 22631), "acrylic")
        self.assertEqual(backdrop_kind("mica", 22631), "mica")
        self.assertEqual(backdrop_kind("auto", 22000), "")
        self.assertEqual(backdrop_kind("auto", 19045), "")
        self.assertEqual(backdrop_kind("off", 26100), "")

    def test_history(self) -> None:
        rows = core.add_history([], {"q": "a", "a": "1"})
        rows = core.add_history(rows, {"q": "b"})
        rows = core.add_history(rows, {"q": "  a  "})
        self.assertEqual([r["q"] for r in rows], ["a", "b"])
        self.assertEqual(core.add_history(rows, {"q": ""}), rows)
        many = []
        for i in range(100):
            many = core.add_history(many, {"q": f"q{i}"})
        self.assertEqual(len(many), core.HISTORY_LIMIT)
        self.assertEqual(many[0]["q"], "q99")

class PromptAndProtocolTests(unittest.TestCase):
    def test_prompt_is_short_and_answer_first(self) -> None:
        prompt = core.system_prompt(lang="ru", style="brief", web=True, screenshot=True, vision=True)
        self.assertIn("first line IS the answer", prompt)
        self.assertIn("web_search", prompt)
        self.assertIn("take_screenshot", prompt)
        self.assertIn("```memuai-tool", prompt)
        self.assertIn("WITHOUT the MemuAI windows", prompt)
        self.assertLess(len(prompt), 4200, "sent with every quick question: keep it small")
        self.assertIn("90 words", prompt)
        self.assertIn("180 words", core.system_prompt(style="balanced"))

    def test_prompt_tools_follow_settings(self) -> None:
        no_tools = core.system_prompt(web=False, screenshot=False)
        self.assertNotIn("memuai-tool", no_tools)
        blind = core.system_prompt(web=False, screenshot=True, vision=False)
        self.assertIn("cannot see images", blind)
        self.assertIn("no separate image-recognition model", blind)
        native = core.system_prompt(web=True, screenshot=False, native_search=True)
        self.assertNotIn("web_search {", native)
        self.assertIn("built-in web search", native)

    def test_visible_text_never_leaks_a_tool_block(self) -> None:
        text = 'Answer part. ```memuai-tool\n[{"tool":"web_search","args":{"query":"x"}}]\n```'
        for size in (1, 2, 3, 5, 7, 13):
            filt = core.VisibleText()
            shown = "".join(filt.feed(text[i:i + size]) for i in range(0, len(text), size)) + filt.finish()
            self.assertEqual(shown, "Answer part. ", size)
            self.assertNotIn("`", shown)
        filt = core.VisibleText()
        shown = filt.feed("Use ``") + filt.feed("`python\nprint(1)\n```") + filt.finish()
        self.assertEqual(shown, "Use ```python\nprint(1)\n```", "ordinary code fences pass through")

    def test_parse_tool_calls_tolerance(self) -> None:
        visible, calls, errors = core.parse_tool_calls(
            'x\n```memuai-tool\n[{"tool":"web_search","args":{"query":"погода"}},{"name":"take_screenshot","arguments":"{\\"monitor\\":\\"all\\"}"},]\n```\ny')
        self.assertEqual(visible, "x\n\ny")
        self.assertEqual(calls, [{"tool": "web_search", "args": {"query": "погода"}},
                                 {"tool": "take_screenshot", "args": {"monitor": "all"}}])
        self.assertEqual(errors, [])
        _, calls, _ = core.parse_tool_calls('```memuai-tool\n{"tool":"web_fetch","args":{"url":"https://a.b"}}')
        self.assertEqual(calls, [{"tool": "web_fetch", "args": {"url": "https://a.b"}}])
        _, calls, errors = core.parse_tool_calls("```memuai-tool\nnot json\n```")
        self.assertEqual(calls, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(core.parse_tool_calls("plain answer")[1], [])

def sse(*events):
    for event in events:
        yield "data: " + json.dumps(event, ensure_ascii=False)
    yield "data: [DONE]"

def delta(text):
    return {"choices": [{"delta": {"content": text}}]}

class AnswerLoopTests(unittest.TestCase):
    def run_loop(self, rounds, *, text="вопрос", images=(), vision=True, settings=None, search_payload=None,
                 capture=None, native_search=False, model="m1"):
        requests, tools, searches, captures = [], [], [], []
        replies = list(rounds)

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in sse(*[delta(piece) for piece in replies.pop(0)]):
                yield line

        async def run_tool(body):
            tools.append(body)
            return {"tool": body["tool"], "results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]}

        async def rich_search(query, **kwargs):
            searches.append((query, kwargs))
            return search_payload or {"query": query, "results": []}

        async def capture_fn(args):
            captures.append(args)
            return capture or {"ok": True, "data": "data:image/jpeg;base64,AAAA", "width": 10, "height": 10}

        prefs = dict(core.PREF_DEFAULTS, web_search_mode="auto", profile_name="")
        prefs.update(settings or {})

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text=text, images=list(images), selection="", model=model, history=[], vision=vision,
                    native_search=native_search, settings=prefs, lang="ru", stream_lines=stream_lines,
                    run_tool=run_tool, rich_search=rich_search, rich_payload=lambda d: d, capture=capture_fn):
                out.append(json.loads(chunk[len("data: "):]))
            return out

        events = asyncio.run(collect())
        return events, requests, tools, searches, captures

    def test_plain_answer_streams(self) -> None:
        events, requests, tools, _, _ = self.run_loop([["Пари", "ж."]])
        deltas = "".join(e["text"] for e in events if e["event"] == "delta")
        self.assertEqual(deltas, "Париж.")
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["text"], "Париж.")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["messages"][0]["role"], "system")
        self.assertIn("Quick Ask", requests[0]["messages"][0]["content"])
        self.assertEqual(requests[0]["effort"], "low")
        self.assertFalse(requests[0]["thinking"])
        self.assertTrue(requests[0]["tools_available"])
        self.assertEqual(tools, [])

    def test_web_search_round_trip(self) -> None:
        events, requests, tools, _, _ = self.run_loop([
            ['```memuai-tool\n[{"tool":"web_search","args":{"query":"курс евро"}}]\n```'],
            ["Около 95 ₽."]])
        self.assertEqual(tools[0]["tool"], "web_search")
        self.assertTrue(tools[0]["allow_web"])
        self.assertEqual(tools[0]["args"], {"query": "курс евро"})
        kinds = [e["event"] for e in events]
        self.assertIn("sources", kinds)
        self.assertNotIn("reset", kinds, "nothing visible was shown before the tool call")
        self.assertEqual("".join(e.get("text", "") for e in events if e["event"] == "delta"), "Около 95 ₽.")
        follow_up = requests[1]["messages"]
        self.assertEqual(follow_up[-2]["role"], "assistant")
        self.assertIn("<tool_results>", follow_up[-1]["content"])

    def test_text_before_a_tool_block_is_reset(self) -> None:
        events, *_ = self.run_loop([["Сейчас проверю. ", '```memuai-tool\n[{"tool":"web_search","args":{"query":"x"}}]\n```'],
                                    ["Ответ."]])
        kinds = [e["event"] for e in events]
        self.assertLess(kinds.index("reset"), kinds.index("done"))
        self.assertEqual(events[-1]["text"], "Ответ.")

    def test_screenshot_tool_attaches_the_image(self) -> None:
        events, requests, _, _, captures = self.run_loop([
            ['```memuai-tool\n[{"tool":"take_screenshot","args":{"monitor":"cursor"}}]\n```'], ["Это ошибка доступа."]])
        self.assertEqual(captures, [{"monitor": "cursor"}])
        self.assertIn("image", [e["event"] for e in events])
        last = requests[1]["messages"][-1]["content"]
        self.assertIsInstance(last, list)
        self.assertEqual(last[1]["type"], "image_url")

    def test_screenshot_tool_on_a_text_only_model(self) -> None:
        events, requests, _, _, captures = self.run_loop([
            ['```memuai-tool\n[{"tool":"take_screenshot","args":{}}]\n```'], ["Не вижу экран."]], vision=False)
        self.assertEqual(captures, [], "nothing is captured for a model that cannot see it")
        self.assertIn("cannot see images", requests[1]["messages"][-1]["content"])
        self.assertIn("cannot see images", requests[0]["messages"][0]["content"])

    def test_disabled_tools_refuse(self) -> None:

        with mock.patch.dict(quick_ask_api.EXTRA_TOOLS, clear=True):
            _, requests, tools, _, captures = self.run_loop([
                ['```memuai-tool\n[{"tool":"web_search","args":{"query":"x"}},{"tool":"take_screenshot","args":{}}]\n```'],
                ["ok"]], settings={"quick_ask_web_search": False, "quick_ask_screenshot_tool": False,

                                   "quick_ask_forms": False})
        self.assertEqual((tools, captures), ([], []))
        self.assertNotIn("memuai-tool", requests[0]["messages"][0]["content"])
        self.assertIn("turned off", requests[1]["messages"][-1]["content"])

    def test_images_need_a_vision_model(self) -> None:
        events, requests, *_ = self.run_loop([], images=["data:image/png;base64,AA"], vision=False)
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(events[0]["code"], "vision")
        self.assertIn("нет отдельной модели", events[0]["error"])
        self.assertEqual(requests, [])

    def test_images_are_sent_as_parts(self) -> None:
        _, requests, *_ = self.run_loop([["Кот."]], images=["data:image/png;base64,AA"], text="что это?")
        content = requests[0]["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "что это?"})
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,AA")

    def test_no_model(self) -> None:
        events, requests, *_ = self.run_loop([], model="")
        self.assertEqual(events[0]["code"], "no_model")
        self.assertEqual(requests, [])

    def test_fresh_data_is_prefetched(self) -> None:
        payload = {"query": "погода в Москве", "results": [{"title": "Погода", "url": "https://m.example/w"}],
                   "answer": {"kind": "weather", "text": "Москва: +7 °C", "source": "Open-Meteo"}}
        events, requests, _, searches, _ = self.run_loop([["+7 °C, облачно."]], text="какая сейчас погода в Москве",
                                                         search_payload=payload)
        self.assertEqual(len(searches), 1)
        kinds = [e["event"] for e in events]
        self.assertIn("instant", kinds)
        self.assertIn("sources", kinds)
        joined = json.dumps(requests[0]["messages"], ensure_ascii=False)
        self.assertIn("<web_prefetch>", joined)

    def test_native_search_skips_our_tools(self) -> None:
        _, requests, _, searches, _ = self.run_loop([["ok"]], text="последние новости", native_search=True)
        self.assertTrue(requests[0]["web_search"])
        self.assertEqual(searches, [])
        self.assertNotIn("web_search {", requests[0]["messages"][0]["content"])

    def test_tool_loop_is_bounded(self) -> None:
        call = ['```memuai-tool\n[{"tool":"web_search","args":{"query":"x"}}]\n```']
        events, requests, *_ = self.run_loop([call] * (core.MAX_TOOL_ROUNDS + 1))
        self.assertEqual(len(requests), core.MAX_TOOL_ROUNDS + 1)
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["code"], "tools")

    def test_provider_error_is_reported(self) -> None:
        async def stream_lines(path, payload):
            yield 'data: {"error": {"message": "quota exceeded"}}'

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="q", images=[], selection="", model="m", history=[], vision=True, native_search=False,
                    settings=dict(core.PREF_DEFAULTS, quick_ask_web_search=False), lang="ru", stream_lines=stream_lines,
                    run_tool=None, rich_search=None, rich_payload=None, capture=None):
                out.append(json.loads(chunk[6:]))
            return out
        events = asyncio.run(collect())
        self.assertEqual({k: events[-1][k] for k in ("event", "code", "error")},
                         {"event": "error", "code": "model", "error": "quota exceeded"})
        self.assertEqual(events[-1]["tier"], "fast")

    def test_messages_keep_recent_history_and_selection(self) -> None:
        history = [{"role": "user", "content": f"u{i}"} if i % 2 == 0 else {"role": "assistant", "content": f"a{i}"}
                   for i in range(30)] + [{"role": "system", "content": "evil"}]
        messages = quick_ask_api.build_messages(text="переведи", history=history, images=[], system="S",
                                                selection="hello")
        self.assertEqual(messages[0], {"role": "system", "content": "S"})
        self.assertEqual(sum(1 for m in messages if m["role"] == "system"), 1, "history cannot inject system messages")
        self.assertEqual(len(messages), 1 + quick_ask_api.MAX_HISTORY_TURNS * 2 + 1)
        self.assertIn("<selected_text>\nhello\n</selected_text>", messages[-1]["content"])

class RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        self.app = create_app()
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def test_config_hotkey_and_history_routes(self) -> None:
        config = self.client.get("/api/quick/config").json()
        self.assertEqual(config["prefs"]["quick_ask_hotkey"], "Alt+Space")
        self.assertFalse(config["status"]["native"])
        self.assertEqual(config["alternatives"], list(core.ALTERNATIVE_HOTKEYS))
        bad = self.client.post("/api/quick/hotkey", json={"action": "validate", "hotkey": "Ctrl+C"}).json()
        self.assertEqual((bad["ok"], bad["code"]), (False, "editing"))
        good = self.client.post("/api/quick/hotkey", json={"action": "apply", "hotkey": "alt+q"}).json()
        self.assertEqual((good["ok"], good["prefs_hotkey"]), (True, "Alt+Q"))
        self.client.post("/api/quick/hotkey", json={"action": "apply", "hotkey": "Alt+Space"})
        self.client.post("/api/quick/history", json={"clear": True})
        self.client.post("/api/quick/history", json={"q": "сколько будет 2+2", "a": "4", "model": "m"})
        items = self.client.get("/api/quick/history").json()["items"]
        self.assertEqual(items[0]["q"], "сколько будет 2+2")

    def test_window_routes_without_native_window(self) -> None:
        self.assertEqual(self.client.post("/api/quick/window", json={"action": "hide"}).json()["ok"], False)
        self.client.post("/api/quick/window", json={"action": "handoff", "handoff": {"messages": [{"role": "user", "content": "hi"}]}})
        taken = self.client.get("/api/quick/handoff").json()["handoff"]
        self.assertEqual(taken["messages"][0]["content"], "hi")
        self.assertIsNone(self.client.get("/api/quick/handoff").json()["handoff"], "a handoff is taken once")
        self.assertEqual(self.client.post("/api/quick/screenshot", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/quick/ask", json={}).status_code, 400)

    def test_static_page_and_settings_wiring(self) -> None:
        page = self.client.get("/quick/")
        self.assertEqual(page.status_code, 200)
        for asset in ("/quick/quick.css", "/quick/quick.js", "/quick/quick-md.js", "/quick/quick-calc.js", "/syntax.js"):
            self.assertIn(asset, page.text)
            self.assertEqual(self.client.get(asset).status_code, 200, asset)
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('/quick-settings.js', index)
        self.assertIn('/quick-settings.css', index)
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('P("quick"', app_js)
        self.assertIn('data-set-tab="${p.id}"', app_js)
        self.assertIn('id="pane-quick"', app_js)
        self.assertIn("MemuQuickSettings.bind", app_js)
        self.assertIn("window.MemuQuickHandoff", app_js)
        settings = (STATIC / "quick-settings.js").read_text(encoding="utf-8")

        self.assertIn("отдельной модели для изображений нет", settings)
        self.assertIn("there is no separate image model", settings)

    def test_native_hooks_and_shutdown(self) -> None:
        source = (ROOT / "memuai.py").read_text(encoding="utf-8")
        self.assertIn("_create_quick_ask(app, base_url)", source)
        self.assertIn("shell.quit_hooks.append(quick_ask.destroy)", source)
        from app.desktop import DesktopShell
        shell = DesktopShell("http://x")
        ran = []
        shell.quit_hooks.append(lambda: ran.append(1))
        shell.quit_hooks.append(lambda: 1 / 0)
        shell.request_quit()
        self.assertEqual(ran, [1])
        self.assertEqual(shell.quit_hooks, [])

    def test_manager_is_inert_off_windows(self) -> None:
        import sys
        if sys.platform == "win32":
            self.skipTest("Windows")
        from app.quick_ask import QuickAskManager
        manager = QuickAskManager("http://127.0.0.1:1", load_prefs=lambda: dict(core.PREF_DEFAULTS))
        self.assertFalse(manager.native_ok)
        manager.start()
        self.assertFalse(manager.show())
        self.assertFalse(manager.capture()["ok"])
        self.assertEqual(manager.apply_preferences({})["code"], "unsupported")

    def test_encode_image_downscales(self) -> None:
        from PIL import Image
        from app.quick_ask import encode_image
        out = encode_image(Image.new("RGB", (3840, 2160), (10, 20, 30)), max_side=1920, bbox=(0, 0, 3840, 2160))
        self.assertTrue(out["data"].startswith("data:image/jpeg;base64,"))
        self.assertEqual((out["width"], out["height"]), (1920, 1080))
        self.assertEqual(out["bbox"], [0, 0, 3840, 2160])

@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class InstantCalculatorTests(unittest.TestCase):
    def test_calculator_and_units(self) -> None:
        script = f"""
const c = require({json.dumps(str(STATIC / 'quick' / 'quick-calc.js'))});
const cases = ["2+2*3", "2^10 + 15%", "15% от 2400", "√16", "10!", "5 km в милях", "100 c to f", "3 GB in MB",
  "сколько будет 2+2", "hello", "2024", "1, 2, 3", "(1+2", "что такое 2+2 в двоичной"];
console.log(JSON.stringify(cases.map((q) => {{ const r = c.evaluate(q, "en"); return r && r.value; }})));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        values = json.loads(out.stdout)
        self.assertEqual(values[:4], [8, 1177.6, 360, 4])
        self.assertEqual(values[4], 3628800)
        self.assertAlmostEqual(values[5], 3.10686, places=4)
        self.assertEqual(values[6:9], [212, 3072, 4])
        self.assertEqual(values[9:], [None, None, None, None, None], "a normal question is never treated as maths")

if __name__ == "__main__":
    unittest.main()

from app import quick_ask_browser as qb
from app.win_hotkey import ComboCapture

class Round22HotkeyTests(unittest.TestCase):
    def test_alt_space_and_any_number_of_modifiers(self) -> None:
        self.assertEqual(core.parse_hotkey("Alt+Space")[:2], (core.MOD_ALT, 0x20))
        mods, vk, text = core.parse_hotkey("Win + Shift + Alt + Ctrl + K")
        self.assertEqual(text, "Ctrl+Alt+Shift+Win+K")
        self.assertEqual(mods, core.MOD_CONTROL | core.MOD_ALT | core.MOD_SHIFT | core.MOD_WIN)
        self.assertEqual(vk, ord("K"))
        self.assertEqual(core.parse_hotkey("ctrl+shift+alt+f5")[2], "Ctrl+Alt+Shift+F5")
        self.assertEqual(core.parse_hotkey("Ctrl+Alt+Shift+Space")[2], "Ctrl+Alt+Shift+Space")

    def test_f_keys_and_new_keys(self) -> None:
        self.assertEqual(core.parse_hotkey("F9")[2], "F9")
        self.assertIn("standalone", [w["code"] for w in core.hotkey_warnings("F9")])
        self.assertEqual(core.parse_hotkey("Shift+F2")[2], "Shift+F2")
        self.assertEqual(core.parse_hotkey("Ctrl+PrtSc")[1], 0x2C)
        self.assertEqual(core.parse_hotkey("Alt+Num+")[2], "Alt+Num+")
        self.assertEqual(core.parse_hotkey("Win+Esc")[2] if False else core.parse_hotkey("Ctrl+Alt+Backspace")[2],
                         "Ctrl+Alt+Backspace")
        for bad, code in (("Space", "need_modifier"), ("Esc", "need_modifier"), ("Shift+A", "shift_only"),
                          ("Ctrl+Shift+Esc", "reserved"), ("Alt+Esc", "reserved")):
            with self.assertRaises(core.HotkeyError) as ctx:
                core.parse_hotkey(bad)
            self.assertEqual(ctx.exception.code, code, bad)

    def test_browser_event_and_native_vk_capture(self) -> None:
        self.assertEqual(core.hotkey_from_event("Space", alt=True), "Alt+Space")
        self.assertEqual(core.hotkey_from_event("KeyK", ctrl=True, alt=True, shift=True, meta=True), "Ctrl+Alt+Shift+Win+K")
        self.assertEqual(core.hotkey_from_event("NumpadAdd", ctrl=True), "Ctrl+Num+")
        self.assertEqual(core.hotkey_from_vks([0xA4, 0x20]), "Alt+Space")
        self.assertEqual(core.hotkey_from_vks([0x5B, 0xA2, 0xA0, 0x12, 0x4B]), "Ctrl+Alt+Shift+Win+K")
        self.assertEqual(core.hotkey_from_vks([0x11, 0x10]), "")

        js = (STATIC / "quick-settings.js").read_text(encoding="utf-8")
        for code in ("NumpadAdd", "PrintScreen", "AltLeft", "MetaLeft", '"suspend"', '"capture"', '"resume"'):
            self.assertIn(code, js)

    def test_native_combo_capture(self) -> None:
        def run(script, foreground=lambda: True, held=()):
            frames = list(script)
            state = {"down": set(held)}

            def key_down(vk):
                return vk in state["down"]
            capture = ComboCapture(key_down, foreground_ok=foreground)
            capture.POLL = 0.001
            original = capture._down

            def stepping():
                if frames:
                    state["down"] = set(frames.pop(0))
                return original()
            capture._down = stepping
            return capture.run(timeout=1.0)
        self.assertEqual(run([[], [0x12], [0x12, 0x20]])["hotkey"], "Alt+Space")
        self.assertEqual(run([[0x11], [0x11, 0x10], [0x11, 0x10, 0x12], [0x11, 0x10, 0x12, 0x5B, 0x51]])["hotkey"],
                         "Ctrl+Alt+Shift+Win+Q")
        self.assertEqual(run([[], [0x1B]])["code"], "cancelled")

        self.assertEqual(run([[0x41], [0x41], [0x41, 0x12], [0x41, 0x12, 0x51]], held=(0x41,))["hotkey"], "Alt+Q")
        self.assertEqual(run([[0x12, 0x20]] * 3, foreground=lambda: False)["code"], "timeout")

class Round22OwnRegistrationTests(unittest.TestCase):
    """«Taken by another program» must never be our own registration."""

    def make(self, taken=()):
        fake = FakeWin32(taken)
        listener = HotkeyListener(lambda: None, api_factory=lambda: fake)
        self.addCleanup(listener.stop)
        return listener, fake

    def test_stale_own_registration_is_released_and_retried(self) -> None:
        listener, fake = self.make()

        fake.registered[HOTKEY_ID] = (core.MOD_ALT, 0x20)
        status = listener.set_hotkey("Alt+Space")
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["code"], "registered")
        self.assertEqual(fake.registered[HOTKEY_ID][1], 0x20)

    def test_probe_id_left_registered_is_released(self) -> None:
        listener, fake = self.make()
        fake.registered[HOTKEY_ID + 1] = (core.MOD_ALT, 0x20)
        fake.taken = {(core.MOD_ALT, 0x20)}

        def register(_hwnd, ident, mods, vk, original=fake.RegisterHotKey):

            if HOTKEY_ID + 1 not in fake.registered:
                fake.taken.discard((core.MOD_ALT, 0x20))
            return original(_hwnd, ident, mods, vk)
        fake.RegisterHotKey = register
        self.assertTrue(listener.set_hotkey("Alt+Space")["ok"])

    def test_real_conflict_still_reported_with_the_right_code(self) -> None:
        listener, fake = self.make(taken={(core.MOD_ALT, 0x20)})
        failure = listener.set_hotkey("Alt+Space")
        self.assertFalse(failure["ok"])
        self.assertEqual((failure["code"], failure["win32"]), ("busy", ERROR_HOTKEY_ALREADY_REGISTERED))

    def test_suspend_for_capture_and_resume(self) -> None:
        listener, fake = self.make()
        listener.set_hotkey("Alt+Space")
        self.assertEqual(listener.suspend(5)["code"], "suspended")
        self.assertEqual(fake.registered, {}, "while the capture listens, our hotkey is released")
        self.assertEqual(listener.probe("Alt+Space")["code"], "ours", "probing our own (suspended) hotkey is not a conflict")
        self.assertTrue(listener.resume()["ok"])
        self.assertEqual(fake.registered[HOTKEY_ID][1], 0x20)

        listener.suspend(5)
        self.assertTrue(listener.set_hotkey("Ctrl+Alt+Q")["ok"])
        self.assertEqual(listener.suspended, "")
        self.assertEqual(fake.registered[HOTKEY_ID][1], ord("Q"))
        listener.resume()
        self.assertEqual(listener.active, "Ctrl+Alt+Q")

    def test_conflicts_only_for_running_programs(self) -> None:
        self.assertEqual(core.conflicts_for("Alt+Space", []), [])
        self.assertEqual(core.conflicts_for("Alt+Space", ["explorer.exe", "memuai.exe"]), [])
        found = core.conflicts_for("Alt+Space", ["PowerToys.PowerLauncher.exe"])
        self.assertEqual(found[0]["program"], "PowerToys Run")
        self.assertEqual(core.conflicts_for("Ctrl+Alt+Space", ["powertoys.powerlauncher.exe"]), [])

class Round22PositionTests(unittest.TestCase):
    LEFT = {"rect": (-2880, 0, 0, 1620), "work": (-2880, 0, 0, 1560), "dpi": 144, "primary": False}
    MAIN = {"rect": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1040), "dpi": 96, "primary": True}

    def test_config_key_is_order_insensitive_and_dpi_aware(self) -> None:
        a = core.monitor_config_key([self.LEFT, self.MAIN])
        self.assertEqual(a, core.monitor_config_key([self.MAIN, self.LEFT]))
        self.assertNotEqual(a, core.monitor_config_key([self.MAIN]))
        self.assertNotEqual(a, core.monitor_config_key([dict(self.LEFT, dpi=96), self.MAIN]))

    def test_snap_to_corners_and_edges_on_a_scaled_negative_monitor(self) -> None:
        monitors = [self.LEFT, self.MAIN]
        w, h = 1080, 204
        margin = 24
        top_left = core.snap(monitors, (-2880 + 30, 20, -2880 + 30 + w, 20 + h))
        self.assertEqual((top_left["x"], top_left["y"], top_left["dock"]), (-2880 + margin, margin, "top-left"))
        bottom_right = core.snap(monitors, (-w - 40, 1560 - h - 30, -40, 1560 - 30))
        self.assertEqual(bottom_right["dock"], "bottom-right")
        self.assertEqual((bottom_right["x"], bottom_right["y"] + h), (-margin - w, 1560 - margin))
        self.assertEqual(bottom_right["anchor"], "bottom", "docked at the bottom, the window grows upwards")
        centre = core.snap(monitors, (-1440 - w // 2 + 10, 700, -1440 + w // 2 + 10, 700 + h))
        self.assertEqual((centre["record"]["h"], centre["dock"]), ("center", ""))
        free = core.snap(monitors, (-2400, 300, -2400 + w, 300 + h))
        self.assertEqual((free["record"]["h"], free["record"]["v"], free["anchor"]), ("free", "free", "top"))
        main_top = core.snap(monitors, (500, 30, 500 + 720, 30 + 136))
        self.assertEqual(main_top["dock"], "", "near the top but not near a side/centre: only the top edge sticks")
        self.assertEqual(main_top["y"], 16)

    def test_saved_spot_is_restored_with_the_current_size(self) -> None:
        monitors = [self.LEFT, self.MAIN]
        snapped = core.snap(monitors, (-1080 - 40, 1560 - 204 - 30, -40, 1560 - 30))
        geometry = core.place_saved(monitors, snapped["record"], (100, 100), width=720, height=300)
        self.assertEqual(geometry["monitor"], 0, "the remembered monitor, not the one under the cursor")
        self.assertEqual(geometry["anchor"], "bottom")
        self.assertEqual(geometry["y"] + geometry["h"], 1560 - 24)
        self.assertEqual(geometry["x"] + geometry["w"], -24)
        grown = core.resize(geometry, 500)
        self.assertEqual(grown["y"] + grown["h"], 1560 - 24, "a longer answer extends upwards")

        self.assertIsNotNone(core.place_saved([self.MAIN], dict(snapped["record"], index=0), (0, 0)))
        self.assertIsNone(core.place_saved([self.MAIN], dict(snapped["record"], index=3), (0, 0)))

    def test_docking_modes(self) -> None:
        bottom = core.place([self.MAIN], (10, 10), mode="bottom")
        self.assertEqual((bottom["anchor"], bottom["y"] + bottom["h"]), ("bottom", 1040 - 16))
        self.assertEqual(bottom["x"], (1920 - 720) // 2)
        right = core.place([self.MAIN], (10, 10), mode="top-right")
        self.assertEqual((right["x"] + right["w"], right["y"]), (1920 - 16, 16))
        left = core.place([self.LEFT], (-100, 100), mode="left")
        self.assertEqual(left["x"], -2880 + 24)

    def test_region_and_colour_helpers(self) -> None:
        self.assertEqual(core.region_diameter(12, 1.5), 36, "CreateRoundRectRgn wants the diameter")
        self.assertEqual(core.region_diameter(8, 1.0), 16)
        self.assertEqual(core.parse_css_color("rgb(27, 28, 32)"), (27, 28, 32))
        self.assertEqual(core.parse_css_color("#FA0"), (255, 170, 0))
        self.assertIsNone(core.parse_css_color("rgba(0, 0, 0, 0)"))
        self.assertIsNone(core.parse_css_color("color-mix(in srgb, red, blue)"))

    def test_page_radius_follows_the_window(self) -> None:
        css = (STATIC / "quick" / "quick.css").read_text(encoding="utf-8")
        self.assertIn("--qa-native-radius", css)
        self.assertIn('--qa-window-bg', css)
        js = (STATIC / "quick" / "quick.js").read_text(encoding="utf-8")
        self.assertIn('windowAction("surface"', js)
        native = (ROOT / "app" / "quick_ask.py").read_text(encoding="utf-8")
        self.assertIn("DWM_ROUND_RADIUS = 8", native)
        self.assertIn("core.region_diameter(self.radius_css, scale)", native)

    def test_prefs_v2_migration(self) -> None:
        from app.pgdata import _normalize_preferences
        old = _normalize_preferences({"quick_ask_v": 1, "quick_ask_position": "center"})
        self.assertEqual(old["quick_ask_position"], "last", "v1 'center' opens in the same place until dragged")
        self.assertEqual(old["quick_ask_v"], 3)
        kept = _normalize_preferences({"quick_ask_v": 2, "quick_ask_position": "center"})
        self.assertEqual(kept["quick_ask_position"], "center")
        self.assertEqual(_normalize_preferences({"quick_ask_position": "bottom-right"})["quick_ask_position"], "bottom-right")
        self.assertEqual(_normalize_preferences({"quick_ask_position": "moon"})["quick_ask_position"], "last")
        prefs = _normalize_preferences({"quick_ask_routing": "turbo", "quick_ask_instructions": "x" * 5000,
                                        "quick_ask_name": "  Джарвис  "})
        self.assertEqual(prefs["quick_ask_routing"], "auto")
        self.assertEqual(len(prefs["quick_ask_instructions"]), 4000)
        self.assertEqual(prefs["quick_ask_name"], "Джарвис")
        self.assertTrue(prefs["quick_ask_browser"])

class Round22PersonalityTests(unittest.TestCase):
    PRESETS = [{"id": "live-a", "name": "Мой Джарвис", "ai_name": "Джарвис", "gender": "male", "primary": "witty",
                "secondary": ["concise", "nope"], "custom": "Обращайся «сэр»."}]

    def test_ids_are_the_live_catalogue(self) -> None:
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        for key in core.PERSONALITIES:
            self.assertIn(f'["{key}",', app_js, key)

    def test_normalize_and_block(self) -> None:
        self.assertEqual(core.normalize_personality("concise"), "concise")
        self.assertEqual(core.normalize_personality("preset:live-a", self.PRESETS), "preset:live-a")
        self.assertEqual(core.normalize_personality("preset:gone", self.PRESETS), "", "a deleted preset falls back")
        self.assertEqual(core.normalize_personality("unknown"), "")
        self.assertEqual(core.personality_block(""), "", "neutral by default: nothing added to the prompt")
        block = core.personality_block("preset:live-a", presets=self.PRESETS, instructions="Я живу в Казани.")
        self.assertIn("Джарвис", block)
        self.assertIn("Обращайся «сэр».", block)
        self.assertIn("Я живу в Казани.", block)
        self.assertIn("masculine", block)
        self.assertIn(core.PERSONALITIES["concise"][2], block)
        self.assertNotIn("nope", block)
        prompt = core.system_prompt(personality=block)
        self.assertLess(prompt.index("</answer>"), prompt.index("<personality>"), "answer-first rules come first")

    def test_live_selection_is_independent(self) -> None:
        from app.pgdata import _normalize_preferences
        prefs = _normalize_preferences({"voice_live_primary_personality": "romantic", "quick_ask_personality": "coach",
                                        "voice_live_personality_presets": self.PRESETS})
        self.assertEqual((prefs["voice_live_primary_personality"], prefs["quick_ask_personality"]), ("romantic", "coach"))
        prefs = _normalize_preferences({"quick_ask_personality": "preset:live-a", "voice_live_personality_presets": self.PRESETS})
        self.assertEqual(prefs["quick_ask_personality"], "preset:live-a")

class Round22RoutingTests(unittest.TestCase):
    def test_heuristic(self) -> None:
        self.assertEqual(core.route("сколько будет 2+2"), ("fast", "simple"))
        self.assertEqual(core.route("Как по-английски «дедлайн»?")[0], "fast")
        self.assertEqual(core.route("Напиши функцию на Python, которая парсит CSV и считает статистику")[0], "advanced")
        self.assertEqual(core.route("Составь план переезда пошагово с бюджетом")[0], "advanced")
        self.assertEqual(core.route("x " * 2000)[0], "advanced")
        self.assertEqual(core.route("проанализируй аргументы автора", page=True)[0], "advanced")
        self.assertEqual(core.route("напиши код", mode="fast"), ("fast", "mode"))
        self.assertEqual(core.route("привет", mode="advanced"), ("advanced", "mode"))
        self.assertEqual(core.route("Напиши функцию сортировки", has_advanced=False), ("fast", "no_advanced"))

    def test_plan(self) -> None:
        settings = {"quick_ask_model": "flash", "quick_ask_advanced_model": "opus", "quick_ask_routing": "auto"}
        plan = quick_ask_api.plan_route({}, settings, text="2+2?", selection="", images=[], caps={}, caps_adv={})
        self.assertEqual((plan["model"], plan["tier"], plan["can_escalate"]), ("flash", "fast", True))
        plan = quick_ask_api.plan_route({}, settings, text="Напиши скрипт на bash для бэкапа", selection="", images=[],
                                        caps={}, caps_adv={})
        self.assertEqual((plan["model"], plan["tier"]), ("opus", "advanced"))
        plan = quick_ask_api.plan_route({"tier": "advanced"}, settings, text="Что на экране?", selection="",
                                        images=["data:image/png;base64,AA"], caps={}, caps_adv={"vision": False})
        self.assertEqual((plan["model"], plan["reason"]), ("flash", "advanced_no_vision"))
        plan = quick_ask_api.plan_route({"model": "mine", "model_explicit": True}, settings,
                                        text="Напиши скрипт на bash", selection="", images=[], caps={}, caps_adv={})
        self.assertEqual((plan["model"], plan["can_escalate"]), ("mine", False), "a model picked in the window is used as is")
        same = dict(settings, quick_ask_advanced_model="flash")
        plan = quick_ask_api.plan_route({}, same, text="Напиши скрипт", selection="", images=[], caps={}, caps_adv={})
        self.assertEqual((plan["tier"], plan["can_escalate"]), ("fast", False))

    def test_fast_model_escalates_itself(self) -> None:
        requests = []
        replies = [['```memuai-tool\n[{"tool":"escalate_to_advanced","args":{"reason":"hard proof"}}]\n```'], ["Доказательство: …"]]

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in sse(*[delta(piece) for piece in replies.pop(0)]):
                yield line

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="Докажи, что простых чисел бесконечно много", images=[], selection="", model="flash", history=[],
                    vision=True, native_search=False, settings=dict(core.PREF_DEFAULTS, quick_ask_web_search=False),
                    lang="ru", stream_lines=stream_lines, run_tool=None, rich_search=None, rich_payload=None,
                    capture=None, advanced_model="opus"):
                out.append(json.loads(chunk[6:]))
            return out
        events = asyncio.run(collect())
        self.assertEqual([r["model"] for r in requests], ["flash", "opus"])
        self.assertIn("escalate_to_advanced", requests[0]["messages"][0]["content"])
        self.assertNotIn("escalate_to_advanced", requests[1]["messages"][0]["content"], "the advanced model cannot escalate")
        routes = [e for e in events if e["event"] == "route"]
        self.assertEqual([(r["tier"], r["escalated"]) for r in routes], [("fast", False), ("advanced", True)])
        self.assertEqual(routes[1]["reason"], "hard proof")
        done = events[-1]
        self.assertEqual((done["event"], done["tier"], done["model"], done["text"]), ("done", "advanced", "opus", "Доказательство: …"))
        self.assertFalse(any("memuai-tool" in e.get("text", "") for e in events if e["event"] == "delta"))

    def test_without_advanced_model_the_tool_is_not_offered(self) -> None:
        prompt = core.system_prompt(can_escalate=False)
        self.assertNotIn("escalate_to_advanced", prompt)

class FakeUia:
    def __init__(self, edits=(), text="", empty_first=False) -> None:
        self._edits = list(edits)
        self._text = text
        self.calls = 0
        self.empty_first = empty_first

    def edits(self, hwnd):
        return self._edits

    def document_text(self, hwnd, limit):
        self.calls += 1
        if self.empty_first and self.calls == 1:
            return ""
        return self._text[:limit]

class Round22BrowserTests(unittest.TestCase):
    def test_browsers_and_titles(self) -> None:
        for exe, name in (("chrome.exe", "Google Chrome"), ("MSEDGE.EXE", "Microsoft Edge"), ("browser.exe", "Яндекс Браузер"),
                          ("firefox.exe", "Firefox"), ("vivaldi.exe", "Vivaldi"), ("opera.exe", "Opera"), ("brave.exe", "Brave")):
            self.assertEqual(qb.browser_for_process("C:\\Program Files\\X\\" + exe)["name"], name)
        self.assertIsNone(qb.browser_for_process("notepad.exe"))
        cases = {
            "Tauri vs Electron - Google Chrome": "Tauri vs Electron",
            "Новости — Яндекс Браузер": "Новости",
            "GitHub - user/repo: thing - Personal - Microsoft\u200b Edge": "GitHub - user/repo: thing",
            "Docs and 3 more pages - Work - Microsoft Edge": "Docs",
            "MDN Web Docs — Mozilla Firefox": "MDN Web Docs",
            "Habr — Mozilla Firefox Private Browsing": "Habr",
            "Plain title": "Plain title",
        }
        for raw, clean in cases.items():
            self.assertEqual(qb.clean_title(raw), clean, raw)

    def test_address_bar_values(self) -> None:
        self.assertEqual(qb.normalize_url("github.com/user/repo"), "https://github.com/user/repo")
        self.assertEqual(qb.normalize_url("https://ya.ru/?q=1"), "https://ya.ru/?q=1")
        self.assertEqual(qb.normalize_url("localhost:8601/quick/"), "http://localhost:8601/quick/")
        self.assertEqual(qb.normalize_url("192.168.1.10/admin"), "http://192.168.1.10/admin")
        self.assertEqual(qb.normalize_url("кто такой джарвис"), "", "search text is not a URL")
        self.assertEqual(qb.normalize_url("chrome://settings"), "")
        self.assertEqual(qb.normalize_url("about:blank"), "")
        self.assertEqual(qb.normalize_url("хабр.рф/news"), "https://хабр.рф/news")
        self.assertTrue(qb.is_internal_url("edge://flags"))

    def test_pick_address_with_mocked_uia(self) -> None:
        chrome = [{"name": "Address and search bar", "value": "news.ycombinator.com/item?id=1", "automation_id": ""}]
        self.assertEqual(qb.pick_address(chrome), "https://news.ycombinator.com/item?id=1")
        firefox = [{"name": "Search", "value": "query text"},
                   {"name": "Search with Google or enter address", "value": "https://developer.mozilla.org/en-US/"}]
        self.assertEqual(qb.pick_address(firefox), "https://developer.mozilla.org/en-US/")
        yandex = [{"name": "Умная строка", "value": "dzen.ru/a/xyz"}]
        self.assertEqual(qb.pick_address(yandex), "https://dzen.ru/a/xyz")
        page_input = [{"name": "Email", "value": "me@example.com"}]
        self.assertEqual(qb.pick_address(page_input), "", "a web form field is not the address bar")
        reader = qb.UiaReader(FakeUia(chrome))
        self.assertEqual(reader.address(1234), "https://news.ycombinator.com/item?id=1")
        self.assertEqual(reader.address(0), "")

    def test_detect_with_fake_window(self) -> None:
        reader = qb.UiaReader(FakeUia([{"name": "Address and search bar", "value": "example.org/a"}]))
        page = qb.detect(77, info={"exe": "chrome.exe", "title": "Example - Google Chrome"}, reader=reader)
        self.assertEqual((page["url"], page["title"], page["source"], page["browser"]),
                         ("https://example.org/a", "Example", "uia", "Google Chrome"))
        broken = qb.UiaReader(FakeUia([]))
        page = qb.detect(77, info={"exe": "firefox.exe", "title": "Article — Mozilla Firefox"}, reader=broken)
        self.assertEqual((page["url"], page["title"], page["source"]), ("", "Article", "title"), "title fallback")
        self.assertIsNone(qb.detect(77, info={"exe": "code.exe", "title": "x"}, reader=reader))

    def test_uia_timeout_never_blocks(self) -> None:
        class Slow(FakeUia):
            def edits(self, hwnd):
                time.sleep(2)
                return [{"name": "Address", "value": "a.com"}]
        started = time.monotonic()
        self.assertEqual(qb.UiaReader(Slow()).address(1, timeout=0.2), "")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_visible_text_retries_once(self) -> None:
        fake = FakeUia(text="Видимый текст статьи " * 10, empty_first=True)
        self.assertIn("Видимый текст", qb.UiaReader(fake).page_text(5))
        self.assertEqual(fake.calls, 2)

    def test_mismatch_reasons(self) -> None:
        article = {"title": "Tauri vs Electron", "text": "Tauri vs Electron. " + "Long readable text. " * 60}
        self.assertEqual(qb.page_mismatch("Tauri vs Electron", article), "")
        self.assertEqual(qb.page_mismatch("x", {"error": "HTTP 403"}), "fetch_failed")
        self.assertEqual(qb.page_mismatch("Inbox (3) - Gmail", {"title": "Gmail", "text": "Sign in to continue to Gmail"}),
                         "login_wall")
        self.assertEqual(qb.page_mismatch("Статья", {"title": "Статья", "text": "Оформите подписку, чтобы читать. " * 30}), "paywall")
        self.assertEqual(qb.page_mismatch("App", {"title": "App", "text": "Loading…"}), "empty")
        self.assertEqual(qb.page_mismatch("Мои заказы — Озон", {"title": "OZON интернет-магазин электроники одежды",
                                                                 "text": "Каталог товаров " * 50}), "title_mismatch")

    def test_read_page_falls_back_to_the_screen(self) -> None:
        class Manager:
            def page_text(self, page_id, limit):
                return "Текст, который видит пользователь: заказ №1 доставлен."

        async def login_wall(body):
            return {"tool": "web_fetch", "url": body["args"]["url"], "title": "Sign in", "text": "Please sign in to continue"}

        async def good(body):
            return {"tool": "web_fetch", "url": body["args"]["url"], "title": "Docs", "text": "Docs " + "content " * 200}
        page = {"id": "p1", "url": "https://shop.example/orders", "title": "Мои заказы"}
        out = asyncio.run(quick_ask_api.read_page_content(page, run_tool=login_wall, manager=Manager()))
        self.assertEqual((out["source"], out["reason"]), ("screen", "login_wall"))
        self.assertIn("заказ №1", out["text"])
        out = asyncio.run(quick_ask_api.read_page_content(dict(page, title="Docs"), run_tool=good, manager=Manager()))
        self.assertEqual(out["source"], "fetch")
        out = asyncio.run(quick_ask_api.read_page_content(dict(page, url=""), run_tool=good, manager=Manager()))
        self.assertEqual((out["source"], out["reason"]), ("screen", "no_url"))
        out = asyncio.run(quick_ask_api.read_page_content(dict(page, url=""), run_tool=good, manager=None))
        self.assertIn("error", out)
        out = asyncio.run(quick_ask_api.read_page_content(page, run_tool=good, manager=Manager(), allow_fetch=False))
        self.assertEqual((out["source"], out["reason"]), ("screen", "web_off"))

    def test_page_question_is_pre_read(self) -> None:
        requests = []

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in sse(delta("Суть: …")):
                yield line

        async def read_page(max_chars=24000):
            return {"tool": "read_page", "source": "fetch", "url": "https://a.dev/x", "title": "X", "text": "PAGE TEXT", "chars": 9}

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="Перескажи эту страницу", images=[], selection="", model="m", history=[], vision=True,
                    native_search=False, settings=dict(core.PREF_DEFAULTS), lang="ru", stream_lines=stream_lines,
                    run_tool=None, rich_search=None, rich_payload=None, capture=None,
                    page={"url": "https://a.dev/x", "title": "X", "browser": "Google Chrome"}, read_page=read_page,
                    preread_page=quick_ask_api.wants_page("Перескажи эту страницу")):
                out.append(json.loads(chunk[6:]))
            return out
        events = asyncio.run(collect())
        self.assertTrue(any(e["event"] == "page" and e["source"] == "fetch" for e in events))
        joined = json.dumps(requests[0]["messages"], ensure_ascii=False)
        self.assertIn("PAGE TEXT", joined)
        self.assertIn("<current_page>", requests[0]["messages"][0]["content"])
        self.assertIn("read_page", requests[0]["messages"][0]["content"])
        self.assertFalse(quick_ask_api.wants_page("сколько будет 2+2"))

class Round22ContractAndChatsTests(unittest.TestCase):
    def tearDown(self) -> None:
        quick_ask_api.EXTRA_TOOLS.pop("demo_tool", None)

    def test_backend_tool_and_client_tool(self) -> None:
        seen = []

        async def handler(args, ctx):
            seen.append((args, ctx["lang"]))
            return {"ok": True, "opened": args.get("app"), "ui": {"card": "opened"}}
        quick_ask_api.register_tool("demo_tool", "{app} — open a program", handler, label="Открываю")
        with self.assertRaises(ValueError):
            quick_ask_api.register_tool("web_search", "x", handler)
        replies = [['```memuai-tool\n[{"tool":"demo_tool","args":{"app":"notepad"}},{"tool":"page_js","args":{"q":1}}]\n```'], ["Готово."]]
        requests = []

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in sse(*[delta(piece) for piece in replies.pop(0)]):
                yield line

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="открой блокнот", images=[], selection="", model="m", history=[], vision=True,
                    native_search=False, settings=dict(core.PREF_DEFAULTS, quick_ask_web_search=False), lang="ru",
                    stream_lines=stream_lines, run_tool=None, rich_search=None, rich_payload=None, capture=None,
                    client_tools=[{"name": "page_js", "description": "{q} — run in the page"}]):
                event = json.loads(chunk[6:])
                out.append(event)
                if event["event"] == "client_tool":

                    asyncio.get_running_loop().call_later(0.01, quick_ask_api.resolve_client_tool, event["id"], {"value": 42})
            return out
        events = asyncio.run(collect())
        self.assertEqual(seen, [({"app": "notepad"}, "ru")])
        self.assertIn("demo_tool {app} — open a program", requests[0]["messages"][0]["content"])
        self.assertIn("page_js {q} — run in the page", requests[0]["messages"][0]["content"])
        self.assertTrue(any(e["event"] == "tool_ui" and e["ui"] == {"card": "opened"} for e in events))
        self.assertTrue(any(e["event"] == "status" and e.get("label") == "Открываю" for e in events))
        results = requests[1]["messages"][-1]["content"]
        self.assertIn('"opened": "notepad"', results)
        self.assertIn('"value": 42', results)
        self.assertEqual(events[-1]["text"], "Готово.")

    def test_router_registered_after_startup_is_reachable(self) -> None:
        from fastapi import APIRouter
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        app = create_app()
        router = APIRouter()

        @router.get("/api/quick/plugin-demo")
        async def demo():
            return {"ok": "plugin"}
        try:
            quick_ask_api.register_router(router)
            client = TestClient(app, base_url="http://127.0.0.1")
            self.assertEqual(client.get("/api/quick/plugin-demo").json(), {"ok": "plugin"})
        finally:
            quick_ask_api.EXTRA_ROUTES.remove(router)

    def test_manifest_and_static_contract(self) -> None:
        manifest = json.loads((STATIC / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
        self.assertIsInstance(manifest.get("plugins"), list)
        self.assertIsInstance(quick_ask_api.plugin_manifest()["plugins"], list)
        js = (STATIC / "quick" / "quick.js").read_text(encoding="utf-8")
        for name in ("registerTool", "registerPanel", "addComposerButton", "settingsSection", "window.MemuQuick = MemuQuick",
                     "/quick/plugins/index.json", '"answer-delta"', '"answer-done"', '"submit"'):
            self.assertIn(name, js)
        html = (STATIC / "quick" / "index.html").read_text(encoding="utf-8")
        for slot in ("qa-slot-top", "qa-slot-bottom", "qa-slot-overlay", "qa-plugin-buttons", "qa-chats", "qa-new", "qa-history"):
            self.assertIn(f'id="{slot}"', html)

    def test_quick_chats_are_separate_from_memuai_chats(self) -> None:
        from starlette.testclient import TestClient
        from app.pgdata import load_chats
        from app.playground_api import create_app
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        before = [c.get("id") for c in load_chats()]
        chat = {"id": "qcTEST01", "title": "", "created": 1, "updated": 2,
                "turns": [{"q": "Курс евро?", "a": "≈ 98 ₽", "model": "flash", "tier": "fast",
                           "page": {"url": "https://cbr.ru", "title": "ЦБ"}}]}
        self.assertTrue(client.post("/api/quick/chats", json={"chat": chat}).json()["ok"])
        try:
            listed = client.get("/api/quick/chats?q=евро").json()["items"]
            self.assertEqual(listed[0]["id"], "qcTEST01")
            self.assertEqual(listed[0]["page"]["url"], "https://cbr.ru")
            self.assertEqual(client.get("/api/quick/chats?q=несуществующее").json()["items"], [])
            full = client.get("/api/quick/chats/qcTEST01").json()["chat"]
            self.assertEqual(full["turns"][0]["a"], "≈ 98 ₽")
            self.assertEqual([c.get("id") for c in load_chats()], before, "Quick Ask chats never enter MemuAI's history")
            self.assertNotIn("qcTEST01", json.dumps(load_chats(), ensure_ascii=False))
            self.assertEqual(client.post("/api/quick/chats", json={"chat": {"id": "x"}}).status_code, 400)
        finally:
            client.delete("/api/quick/chats/qcTEST01")
        self.assertEqual(client.get("/api/quick/chats/qcTEST01").status_code, 404)

    def test_chat_storage_helpers(self) -> None:
        rows = []
        for i in range(core.CHATS_LIMIT + 5):
            rows = core.upsert_chat(rows, {"id": f"qc{i:05d}", "updated": i, "turns": [{"q": f"q{i}", "a": "a"}]})
        self.assertEqual(len(rows), core.CHATS_LIMIT)
        self.assertEqual(rows[0]["id"], f"qc{core.CHATS_LIMIT + 4:05d}")
        rows = core.upsert_chat(rows, {"id": "qc00010", "updated": 0, "pinned": True, "turns": [{"q": "pinned", "a": "a"}]})
        self.assertEqual(rows[0]["id"], "qc00010", "pinned chats stay on top")
        self.assertIsNone(core.clean_chat({"id": "../evil", "turns": [{"q": "x"}]}))
        self.assertIsNone(core.clean_chat({"id": "qcEMPTY", "turns": []}))
        found = core.search_chats(rows, "PINNED")
        self.assertEqual([r["id"] for r in found], ["qc00010"])

    def test_personalities_route(self) -> None:
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        data = TestClient(create_app(), base_url="http://127.0.0.1").get("/api/quick/personalities").json()
        self.assertEqual({row["id"] for row in data["builtin"]}, set(core.PERSONALITIES))
        self.assertIsInstance(data["presets"], list)
