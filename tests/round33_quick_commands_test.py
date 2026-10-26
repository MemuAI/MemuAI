"""Round 33 (r33-quick): Quick Ask commands — «закрой incy», the AI command router and a test matrix of every command.

* tray apps: «закрой incy» when INCY lives in the notification area (no visible window) — polite WM_CLOSE to its hidden
  windows, then the process is ended; a window that hides itself to the tray on close gets «Завершить полностью»;
* app-name matching: Cyrillic spelling of a Latin name («инси» = INCY), the wrong keyboard layout («штсн»), the words
  around the name («закрой окно/программу инси»), an exe named only by its folder, Windows' own processes never;
* the AI router (``app/assistant_router.py``): when it is called, the prompt (actions + running apps), parsing, the safe
  layer (confirmation, «trusted» not honoured, disabled actions), timeouts/garbage → the normal chat answer;
* ``/api/assistant/run`` end to end with a fake model call;
* the phrase matrix: ~170 RU/EN phrasings of every command family → the expected action, then every matched phrase
  executed through the engine on a full fake Windows backend (no handler may crash).
Nothing here touches Windows, the network or a real model.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import assistant_apps as apps  # noqa: E402
from app import assistant_commands as ac  # noqa: E402
from app import assistant_intents as intents  # noqa: E402
from app import assistant_procs_win as procs_win  # noqa: E402
from app import assistant_router as router  # noqa: E402
from app import assistant_timers as timers_mod  # noqa: E402
from app.assistant_actions_win import ActionError, Backend  # noqa: E402

NOW = datetime(2026, 10, 8, 10, 30)
_SAVED: dict = {}

def setUpModule() -> None:
    from app import quick_ask_api
    _SAVED["tools"] = dict(quick_ask_api.EXTRA_TOOLS)
    _SAVED["routes"] = list(quick_ask_api.EXTRA_ROUTES)

def tearDownModule() -> None:
    from app import quick_ask_api
    quick_ask_api.EXTRA_TOOLS.clear()
    quick_ask_api.EXTRA_TOOLS.update(_SAVED.get("tools", {}))
    quick_ask_api.EXTRA_ROUTES[:] = _SAVED.get("routes", [])

def run(coro):
    return asyncio.run(coro)

def win(hwnd, title, pid, path, state="normal"):
    exe = os.path.basename(path.replace("\\", "/")).lower()
    return {"hwnd": hwnd, "title": title, "pid": pid, "path": path, "exe": exe, "app": os.path.splitext(os.path.basename(path.replace("\\", "/")))[0],
            "state": state, "rect": [100, 100, 900, 700], "normal_rect": [100, 100, 900, 700], "own": False}

class WinFake(Backend):
    """Every backend method of the Windows app, recorded; processes with a «tray» behaviour."""
    name = "fake-win"

    def __init__(self):
        self.calls = []
        self.volume = {"level": 50, "mute": False}
        self.brightness = 70
        self.dark = False
        self.clip = "old clip"
        self.wins = [win(101, "Telegram", 11, r"C:\Users\u\AppData\Roaming\Telegram Desktop\Telegram.exe"),
                     win(102, "main.py — Visual Studio Code", 12, r"C:\VS\Code.exe", "maximized"),
                     win(103, "YouTube - Google Chrome", 13, r"C:\Program Files\Google\Chrome\Application\chrome.exe")]
        self.process_rows = [
            {"pid": 11, "ppid": 1, "exe": "telegram.exe", "name": "Telegram.exe", "path": self.wins[0]["path"]},
            {"pid": 12, "ppid": 1, "exe": "code.exe", "name": "Code.exe", "path": r"C:\VS\Code.exe"},
            {"pid": 13, "ppid": 1, "exe": "chrome.exe", "name": "chrome.exe", "path": self.wins[2]["path"]},
            {"pid": 21, "ppid": 1, "exe": "incy.exe", "name": "INCY.exe", "path": r"C:\Users\u\AppData\Local\Programs\INCY\INCY.exe"},
            {"pid": 22, "ppid": 21, "exe": "incy.exe", "name": "INCY.exe", "path": r"C:\Users\u\AppData\Local\Programs\INCY\INCY.exe"},
            {"pid": 23, "ppid": 21, "exe": "xray.exe", "name": "xray.exe", "path": r"C:\Users\u\AppData\Local\Programs\INCY\core\xray.exe"},
            {"pid": 31, "ppid": 1, "exe": "svchost.exe", "name": "svchost.exe", "path": r"C:\Windows\System32\svchost.exe"},
            {"pid": 32, "ppid": 1, "exe": "explorer.exe", "name": "explorer.exe", "path": r"C:\Windows\explorer.exe"},
            {"pid": 33, "ppid": 1, "exe": "app.exe", "name": "app.exe", "path": r"C:\Programs\Hiddify\app.exe"},
        ]
        self.alive = {r["pid"] for r in self.process_rows}
        self.hide_to_tray = {11}
        self.ignore_close = {21}
        self.mons = [{"index": 0, "device": r"\\.\DISPLAY1", "rect": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040], "dpi": 96, "primary": True},
                     {"index": 1, "device": r"\\.\DISPLAY2", "rect": [1920, 0, 4480, 1440], "work": [1920, 0, 4480, 1400], "dpi": 144, "primary": False}]

    def _log(self, *args):
        self.calls.append(args)

    def windows(self, include_minimized=True):
        return [dict(w) for w in self.wins]

    def foreground(self):
        return dict(self.wins[0]) if self.wins else None

    def monitors(self):
        return [dict(m) for m in self.mons]

    def close_window(self, hwnd):
        self._log("close", hwnd)
        row = next((w for w in self.wins if w["hwnd"] == hwnd), None)
        if row:
            self.wins.remove(row)
            if row["pid"] not in self.hide_to_tray:
                self.alive.discard(row["pid"])

    def focus_window(self, hwnd):
        self._log("focus", hwnd)

    def window_cmd(self, hwnd, action):
        self._log("wcmd", hwnd, action)

    def place_window(self, hwnd, rect, state="normal"):
        self._log("place", hwnd, tuple(rect), state)

    def procs(self):
        return [dict(r) for r in self.process_rows if r["pid"] in self.alive]

    def processes(self):
        return [{"name": r["name"], "pid": r["pid"], "memory": 10 << 20} for r in self.process_rows if r["pid"] in self.alive]

    def pid_alive(self, pid):
        return pid in self.alive

    def close_pid(self, pid):
        self._log("close_pid", pid)
        if pid not in self.ignore_close:
            self.alive.discard(pid)
        return 1

    def kill(self, pid):
        self._log("kill", pid)
        self.alive.discard(pid)

    def volume_get(self):
        return dict(self.volume)

    def volume_set(self, level):
        self._log("volume_set", level)
        self.volume["level"] = level
        return dict(self.volume)

    def volume_mute(self, mute):
        self.volume["mute"] = (not self.volume["mute"]) if mute is None else bool(mute)
        return dict(self.volume)

    def media_key(self, key):
        self._log("media", key)

    def send_keys(self, combo):
        self._log("keys", combo)

    def type_text(self, text):
        self._log("type", text)

    def brightness_get(self):
        return {"level": self.brightness, "displays": []}

    def brightness_set(self, level):
        self.brightness = level
        return {"level": level}

    def theme_get(self):
        return {"dark": self.dark}

    def theme_set(self, dark):
        self.dark = dark
        return {"dark": dark}

    def radio(self, kind, on):
        self._log("radio", kind, on)
        return {"before": "On", "on": on}

    def screenshot(self, mode="screen", rect=None, copy=False):
        self._log("screenshot", mode)
        return {"region": True} if mode == "region" else {"path": r"C:\Users\u\Pictures\Screenshots\shot.png"}

    def clipboard_get(self):
        return self.clip

    def clipboard_set(self, text):
        self.clip = text

    def recycle_info(self):
        return {"items": 3, "bytes": 2048}

    def recycle_empty(self):
        return {"items": 3, "bytes": 2048, "emptied": True}

    def sysinfo(self, what="all"):
        return {"battery": {"present": True, "percent": 81, "plugged": False, "seconds_left": 7200},
                "network": {"ip": "192.168.1.5", "wifi": "home", "host": "pc"}}

    def lock(self):
        self._log("lock")

    def sleep(self):
        self._log("sleep")

    def hibernate(self):
        self._log("hibernate")

    def shutdown(self, delay=0):
        self._log("shutdown", delay)

    def restart(self, delay=0):
        self._log("restart", delay)

    def signout(self):
        self._log("signout")

    def cancel_shutdown(self):
        return True

    def app_index(self):
        return apps.merge_index(apps.builtin_entries(), [
            apps._entry("Telegram Desktop", "lnk", r"C:\Start\Telegram Desktop.lnk"),
            apps._entry("INCY", "lnk", r"C:\Start\INCY.lnk", exe="INCY.exe"),
            apps._entry("Google Chrome", "lnk", r"C:\Start\Google Chrome.lnk"),
            apps._entry("Spotify", "uwp", r"shell:AppsFolder\Spotify"),
            apps._entry("Visual Studio Code", "lnk", r"C:\Start\Visual Studio Code.lnk")])

    def launch_app(self, entry):
        self._log("launch", entry["name"])

    def open_target(self, target):
        self._log("open", target)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backend = WinFake()
        self.engine = ac.Engine(backend=self.backend, store=ac.Store(self.tmp / "assistant.json"),
                                audit=ac.Audit(self.tmp / "audit.jsonl"),
                                timers=timers_mod.TimerManager(self.tmp / "timers.json"), now_fn=lambda: NOW)
        self.platform = mock.patch.object(ac.sys, "platform", "win32")
        self.platform.start()

        self.sleep = mock.patch.object(ac.time, "sleep", lambda _s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.platform.stop()

    def settings(self, **patch):
        self.engine.store.update(lambda d: d.__setitem__("settings", ac.normalize_settings({**d["settings"], **patch})))

    def confirm(self, card):
        self.assertEqual(card["status"], "confirm", card)
        return run(self.engine.confirm(card["confirm"]["token"], True))

class NameMatchingTest(unittest.TestCase):
    def test_sound_alike_and_layout(self):
        for spoken in ("incy", "INCY", "инси", "Инси", "штсн", "окно инси", "программу INCY", "приложение incy", "«incy»"):
            with self.subTest(spoken=spoken):
                self.assertGreaterEqual(apps.match_process(spoken, "incy.exe", "INCY"), 0.9)
        for spoken in ("вопрос", "музыку", "свет", "все", "телеграм"):
            with self.subTest(spoken=spoken):
                self.assertLess(apps.match_process(spoken, "incy.exe", "INCY"), 0.8)
        self.assertEqual(apps.phonetic("инси"), apps.phonetic("incy"))
        self.assertEqual(apps.layout_swap("штсн"), "incy")
        self.assertEqual(apps.layout_swap("ntktuhfv"), "телеграм")
        self.assertEqual(apps.layout_swap("incy два"), "")
        self.assertEqual(apps.clean_name("окно программы INCY"), "INCY")
        self.assertEqual(apps.clean_name("the app Slack"), "Slack")

    def test_resolve_installed_app_by_cyrillic_spelling(self):
        index = WinFake().app_index()
        self.assertEqual(apps.resolve("инси", index)["match"]["name"], "INCY")
        self.assertEqual(apps.resolve("спотифай", index)["match"]["name"], "Spotify")
        self.assertEqual(apps.resolve("телега", index)["match"]["name"], "Telegram Desktop")

    def test_user_apps_filter(self):
        rows = WinFake().process_rows + [{"pid": os.getpid(), "exe": "memuai.exe", "path": r"C:\MemuAI\MemuAI.exe"}]
        names = {r["exe"] for r in procs_win.user_apps(rows)}
        self.assertIn("incy.exe", names)
        self.assertNotIn("svchost.exe", names)
        self.assertNotIn("explorer.exe", names)
        self.assertNotIn("memuai.exe", names)
        self.assertTrue(procs_win.is_system_path(r"C:\Windows\System32\notepad.exe"))
        self.assertFalse(procs_win.is_system_path(r"C:\Program Files\INCY\INCY.exe"))
        self.assertEqual(procs_win.app_label("incy.exe", r"C:\x\INCY.exe"), "INCY")

    def test_win32_helpers_are_guarded_off_windows(self):
        if os.name == "nt":
            self.skipTest("Windows")
        from app.assistant_actions_win import Unsupported
        for fn, args in ((procs_win.list_processes, ()), (procs_win.pid_alive, (1,)), (procs_win.post_close, (1,))):
            with self.assertRaises(Unsupported):
                fn(*args)
        with self.assertRaises(Unsupported):
            Backend().procs()

class TrayCloseTest(Base):
    def test_close_incy_in_the_tray_is_found_confirmed_and_quit(self):
        found = self.engine.match("закрой incy")
        self.assertEqual(found["action"], "app.close")
        self.assertTrue(run(self.engine.precheck(found)), "a tray app counts as running (it went to the model before)")
        card = run(self.engine.run("app.close", found["params"]))
        self.assertIn("INCY", card["message"])
        self.assertIn("трее", card["message"].replace("области уведомлений", "трее"))
        done = self.confirm(card)
        self.assertEqual(done["status"], "done", done)

        self.assertIn(("close_pid", 21), self.backend.calls)
        self.assertIn(("kill", 21), self.backend.calls)
        self.assertNotIn(("kill", 23), self.backend.calls)
        self.assertTrue(done["data"]["forced"])
        self.assertNotIn(21, self.backend.alive)

    def test_polite_close_is_enough_when_the_app_listens(self):
        self.backend.ignore_close = set()
        done = self.confirm(run(self.engine.run("app.close", {"name": "инси"})))
        self.assertEqual(done["status"], "done")
        self.assertFalse(done["data"]["forced"])
        self.assertFalse(any(c[0] == "kill" for c in self.backend.calls))

    def test_every_spelling_reaches_incy(self):
        for phrase in ("закрой инси", "закрой INCY", "закрой программу incy", "закрой окно инси", "выйди из incy", "заверши incy",
                       "закрыть incy", "выруби инси", "close incy", "quit INCY", "закрой штсн", "закрой-ка incy",
                       "закрой, пожалуйста, incy", "закрой incy пожалуйста", "Закрой Incy!"):
            with self.subTest(phrase=phrase):
                found = self.engine.match(phrase)
                self.assertIsNotNone(found)
                self.assertEqual(found["action"], "app.close")
                targets = self.engine._app_targets(found["params"]["name"])
                self.assertEqual(targets["label"], "INCY")

    def test_window_that_hides_to_tray_offers_quit_completely(self):
        done = self.confirm(run(self.engine.run("app.close", {"name": "телеграм"})))
        self.assertEqual(done["status"], "done")
        self.assertIn("трее", done["message"])
        self.assertEqual(done["next"]["action"], "app.close")
        self.assertIn(11, self.backend.alive)

        again = self.confirm(run(self.engine.run(done["next"]["action"], done["next"]["params"])))
        self.assertEqual(again["status"], "done", again)
        self.assertNotIn(11, self.backend.alive)

    def test_normal_close_reports_closed(self):
        done = self.confirm(run(self.engine.run("app.close", {"name": "хром"})))
        self.assertEqual(done["message"], "Закрыл Google Chrome")
        self.assertNotIn("next", done)

    def test_not_running_and_system_processes(self):
        self.assertFalse(run(self.engine.precheck({"action": "app.close", "params": {"name": "вопрос"}})))
        self.assertFalse(run(self.engine.precheck({"action": "app.close", "params": {"name": "svchost"}})))
        self.assertFalse(run(self.engine.precheck({"action": "app.close", "params": {"name": "проводник"}})),
                         "Explorer without a window is the shell, never «an app in the tray»")
        card = run(self.engine.run("app.close", {"name": "вопрос"}, confirmed=True))
        self.assertEqual(card["code"], "not_running")

    def test_generic_exe_is_named_by_its_folder(self):
        self.assertEqual(self.engine._app_targets("hiddify")["label"], "app")
        self.assertTrue(self.engine._app_targets("хиддифай")["procs"])

    def test_switch_to_a_tray_app_starts_its_exe_again(self):
        card = run(self.engine.run("app.switch", {"name": "инси"}))
        self.assertEqual(card["status"], "done", card)
        self.assertIn(("open", r"C:\Users\u\AppData\Local\Programs\INCY\INCY.exe"), self.backend.calls)

    def test_window_of_a_named_app(self):
        found = self.engine.match("сверни хром")
        self.assertEqual((found["action"], found["params"]), ("window.action", {"action": "minimize", "target": "хром"}))
        self.assertTrue(run(self.engine.precheck(found)))
        card = run(self.engine.run(found["action"], found["params"]))
        self.assertEqual(card["status"], "done")
        self.assertIn(("wcmd", 103, "minimize"), self.backend.calls)
        self.assertFalse(run(self.engine.precheck(self.engine.match("сверни вопросы"))))

class RouterUnitTest(unittest.TestCase):
    def test_looks_like_command(self):
        for text in ("закрой инси", "вырубай incy", "сделай звук потише", "звук на минимум", "quit spotify",
                     "turn the volume down a bit", "громкость на полную", "поставь будильник на семь"):
            with self.subTest(text=text):
                self.assertTrue(router.looks_like_command(text))
        for text in ("что такое квазар", "как закрыть программу?", "привет", "почему небо голубое",
                     "напиши мне длинное письмо начальнику о том что я заболел и не приду завтра на работу в офис",
                     "расскажи анекдот"):
            with self.subTest(text=text):
                self.assertFalse(router.looks_like_command(text))

    def test_should_route_modes(self):
        s = ac.normalize_settings({})
        self.assertEqual(s["ai_router"], "auto")
        self.assertTrue(router.should_route(s, "закрой инси"))
        self.assertFalse(router.should_route(s, "привет как дела"))
        self.assertTrue(router.should_route(s, "привет как дела", soft_miss=True))
        self.assertTrue(router.should_route({**s, "ai_router": "always"}, "привет как дела"))
        self.assertFalse(router.should_route({**s, "ai_router": "always"}, "что такое квазар"))
        self.assertFalse(router.should_route({**s, "ai_router": "off"}, "закрой инси", soft_miss=True))
        self.assertFalse(router.should_route({**s, "enabled": False}, "закрой инси"))

    def test_settings_normalisation(self):
        s = ac.normalize_settings({"ai_router": "loud", "router_model": "  qwen3.8-flash ", "router_timeout": 99})
        self.assertEqual((s["ai_router"], s["router_model"], s["router_timeout"]), ("auto", "qwen3.8-flash", 15))
        self.assertEqual(ac.normalize_settings({"router_timeout": "0"})["router_timeout"], 1)
        self.assertEqual(ac.normalize_settings({"ai_router": "off"})["ai_router"], "off")

    def test_parse_answer(self):
        self.assertEqual(router.parse_answer('{"a":"app.close","p":{"name":"INCY"}}'), ("app.close", {"name": "INCY"}))
        self.assertEqual(router.parse_answer('<think>hm</think>```json\n{"a": "volume.set", "p": {"delta": -10}}\n```'),
                         ("volume.set", {"delta": -10}))
        self.assertEqual(router.parse_answer('Sure! {"action":"chat"} done'), ("chat", {}))
        self.assertEqual(router.parse_answer('{"a":"type.text","p":{"text":"a } b"}}'), ("type.text", {"text": "a } b"}))
        self.assertIsNone(router.parse_answer('{"a":"app.close","p":{"name":"IN'))
        self.assertIsNone(router.parse_answer("no json"))
        self.assertTrue(router.json_closed('{"a":"chat"}'))
        self.assertFalse(router.json_closed('{"a":"ch'))

    def test_prompt_is_small_and_live(self):
        s = ac.normalize_settings({"disabled_actions": ["lock.screen"]})
        lines = router.action_lines(s)
        joined = "\n".join(lines)
        self.assertIn("app.close(name)", joined)
        self.assertNotIn("lock.screen", joined)
        self.assertNotIn("shell.run", joined)
        self.assertNotIn("_timer", joined)
        msgs = router.build_messages("закрой инси", s, {"running": ["INCY (tray/background)"], "commands": ["Доброе утро"]})
        self.assertLess(len(msgs[0]["content"]), 4500, "the router prompt must stay tiny")
        self.assertIn("Running: INCY (tray/background)", msgs[1]["content"])
        self.assertIn("Доброе утро", msgs[1]["content"])
        running = router.running_apps(WinFake().windows(), WinFake().procs())
        self.assertIn("INCY (tray/background)", running)
        self.assertTrue(any(r.startswith("Telegram") for r in running))
        self.assertFalse(any("svchost" in r for r in running))

class RouterEngineTest(Base):
    def setUp(self):
        super().setUp()
        self.prefs = mock.patch("app.pgdata.load_preferences", return_value={"quick_ask_model": "fast-model"})
        self.prefs.start()
        self.seen = []

    def tearDown(self):
        self.prefs.stop()
        super().tearDown()

    def answer(self, raw, delay=0.0):
        async def call(model, messages):
            self.seen.append((model, messages))
            if delay:
                await asyncio.sleep(delay)
            return raw
        self.engine.router_call = call

    def test_route_picks_the_action_with_the_quick_model(self):
        self.answer('{"a":"app.close","p":{"name":"INCY"}}')
        decision = run(router.route(self.engine, "вырубай эту впн штуку"))
        self.assertEqual((decision["action"], decision["params"]), ("app.close", {"name": "INCY"}))
        self.assertEqual(self.seen[0][0], "fast-model")
        self.assertIn("INCY (tray/background)", self.seen[0][1][1]["content"])

    def test_router_model_setting_wins(self):
        self.settings(router_model="qwen3.8-flash")
        self.answer('{"a":"chat"}')
        self.assertEqual(run(router.route(self.engine, "закрой инси"))["action"], "chat")
        self.assertEqual(self.seen[0][0], "qwen3.8-flash")

    def test_failures_become_chat(self):
        for raw, reason in (("garbage", "unparsable"), ('{"a":"format.disk","p":{}}', "unknown"),
                            ('{"a":"shell.run","p":{"command":"rm"}}', "unknown")):
            with self.subTest(raw=raw):
                self.answer(raw)
                decision = run(router.route(self.engine, "закрой инси"))
                self.assertEqual((decision["action"], decision["reason"]), ("chat", reason))
        self.settings(router_timeout=1)
        self.answer('{"a":"chat"}', delay=3)
        decision = run(router.route(self.engine, "закрой инси"))
        self.assertEqual(decision["reason"], "timeout")
        self.assertLess(decision["ms"], 2500)

    def test_disabled_action_and_off_mode(self):
        self.settings(disabled_actions=["volume.set"])
        self.answer('{"a":"volume.set","p":{"level":0}}')
        self.assertEqual(run(router.route(self.engine, "звук на минимум"))["action"], "chat")
        self.settings(ai_router="off")
        self.assertIsNone(run(router.route(self.engine, "звук на минимум")))

    def test_no_model_no_router(self):
        self.prefs.stop()
        with mock.patch("app.pgdata.load_preferences", return_value={}):
            self.answer('{"a":"chat"}')
            self.assertIsNone(run(router.route(self.engine, "закрой инси")))
        self.prefs.start()

    def test_router_source_keeps_confirmations_and_ignores_trust(self):
        self.settings(trusted_actions=["app.close"])
        self.assertEqual(run(self.engine.run("app.close", {"name": "хром"}))["status"], "done")
        self.backend.wins.append(win(104, "YouTube - Google Chrome", 14, r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        self.backend.alive.add(14)
        card = run(self.engine.run("app.close", {"name": "хром"}, source="router", emit=False))
        self.assertEqual(card["status"], "confirm", "an AI guess never inherits the user's trust")
        self.assertEqual(card["source"], "router")
        self.assertEqual(self.engine.events_after(0), [], "router cards are returned, not emitted twice")
        self.assertEqual(run(self.engine.run("clipboard.get", {}, source="router"))["status"], "confirm")

class RouterRouteTest(Base):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        ac.set_engine(self.engine)
        app = FastAPI()
        app.state.quick_ask = None
        with mock.patch.dict(os.environ, {"MEMUAI_ASSISTANT_NO_WARM": "1"}):
            ac.register(app)
        self.engine = app.state.assistant
        self.engine.backend = self.backend
        self.client = TestClient(app)
        self.prefs = mock.patch("app.pgdata.load_preferences", return_value={"quick_ask_model": "fast-model"})
        self.prefs.start()
        self.calls = []

    def tearDown(self):
        self.prefs.stop()
        ac.set_engine(None)
        super().tearDown()

    def answer(self, raw):
        async def call(model, messages):
            self.calls.append(messages[1]["content"])
            return raw
        self.engine.router_call = call

    def post(self, text, **extra):
        return self.client.post("/api/assistant/run", json={"text": text, **extra}).json()

    def test_local_phrase_never_calls_the_model(self):
        self.answer('{"a":"chat"}')
        data = self.post("громкость 30")
        self.assertEqual(data["card"]["action"], "volume.set")
        self.assertEqual(self.calls, [])
        data = self.post("закрой incy")
        self.assertEqual(data["card"]["status"], "confirm")
        self.assertIn("INCY", data["card"]["message"])
        self.assertEqual(self.calls, [])

    def test_unknown_phrase_routes_to_an_action(self):
        self.answer('{"a":"app.close","p":{"name":"INCY"}}')
        data = self.post("вырубай эту впн штуку")
        self.assertEqual(data["card"]["status"], "confirm")
        self.assertEqual(data["card"]["source"], "router")
        self.assertEqual(data["router"]["model"], "fast-model")
        self.assertEqual(data["match"]["rule"], "ai-router")

    def test_soft_miss_goes_to_the_router_with_context(self):
        self.answer('{"a":"volume.mute","p":{"mute":true}}')
        data = self.post("выруби звук")
        self.assertEqual(data["card"]["action"], "volume.mute")
        self.assertEqual(data["card"]["status"], "done")
        self.assertTrue(self.backend.volume["mute"])

    def test_chat_answers_and_questions_skip_the_router(self):
        self.answer('{"a":"chat"}')
        self.assertIsNone(self.post("сделай мне что-нибудь интересное")["card"])
        self.calls.clear()
        self.assertIsNone(self.post("что такое квазар")["card"])
        self.assertEqual(self.calls, [], "a question never waits for the router")

    def test_wrong_guess_falls_back_to_chat(self):
        self.answer('{"a":"app.close","p":{"name":"Nothing"}}')
        data = self.post("закрой эту штуку")
        self.assertTrue(data["card"]["fallback"])
        self.assertNotEqual(data["card"]["status"], "confirm")
        data = self.post("глуши эту штуку")
        self.assertIsNone(data["card"])
        self.assertEqual(data["router"]["reason"], "not_running")

MATRIX = [

    ("закрой incy", "app.close"), ("закрой INCY", "app.close"), ("закрой инси", "app.close"), ("закрой программу incy", "app.close"),
    ("закрой приложение инси", "app.close"), ("закрой-ка incy", "app.close"), ("закрой incy пожалуйста", "app.close"),
    ("закрой, пожалуйста, incy", "app.close"), ("пожалуйста закрой incy", "app.close"), ("Закрой Incy!", "app.close"),
    ("выйди из incy", "app.close"), ("заверши incy", "app.close"), ("закрыть incy", "app.close"), ("выруби incy", "app.close"),
    ("убей incy", "app.close"), ("close incy", "app.close"), ("quit incy", "app.close"), ("exit incy", "app.close"), ("kill incy", "app.close"),
    ("закрой телеграм", "app.close"), ("закрой хром", "app.close"), ("закрой окно", "window.action"), ("закрой это окно", "window.action"),
    ("открой incy", "app.open"), ("запусти инси", "app.open"), ("открой телеграм", "app.open"), ("open telegram", "app.open"),
    ("launch spotify", "app.open"), ("переключись на incy", "app.switch"), ("перейди в телеграм", "app.switch"),
    ("switch to chrome", "app.switch"), ("покажи incy", "app.switch"), ("какие окна открыты", "app.list"), ("список окон", "app.list"),

    ("громкость 30", "volume.set"), ("громкость на 30", "volume.set"), ("звук 50", "volume.set"), ("volume 30", "volume.set"),
    ("set volume to 40", "volume.set"), ("громче", "volume.set"), ("тише", "volume.set"), ("сделай потише", "volume.set"),
    ("убавь звук", "volume.set"), ("louder", "volume.set"), ("звук на максимум", "volume.set"), ("громкость на минимум", "volume.set"),
    ("выключи звук", "volume.mute"), ("без звука", "volume.mute"), ("mute", "volume.mute"), ("какая громкость", "volume.get"),
    ("пауза", "media.key"), ("следующий трек", "media.key"), ("предыдущий трек", "media.key"), ("next track", "media.key"),
    ("включи despacito на ютубе", "media.search"),

    ("яркость 60", "brightness.set"), ("ярче", "brightness.set"), ("тёмная тема", "theme.set"), ("dark mode", "theme.set"),
    ("ночной свет", "nightlight.open"), ("разверни окно", "window.action"), ("сверни окно", "window.action"),
    ("сверни все окна", "window.minimize_all"), ("окно влево", "window.action"), ("snap left", "window.action"),
    ("перенеси окно на другой монитор", "window.move"), ("расставь окна сеткой", "window.arrange"), ("поверх всех окон", "window.action"),
    ("сверни хром", "window.action"), ("разверни телеграм", "window.action"),

    ("таймер 10 минут", "timer.start"), ("поставь таймер на 5 минут", "timer.start"), ("таймер на полторы минуты", "timer.start"),
    ("засеки 3 минуты", "timer.start"), ("timer 10 minutes for tea", "timer.start"), ("set a timer for 5 min", "timer.start"),
    ("отмени таймер", "timer.cancel"), ("какие таймеры", "timer.list"), ("будильник на 7:30", "alarm.set"),
    ("запусти помодоро", "pomodoro.control"), ("секундомер", "stopwatch.control"),
    ("напомни через 10 минут выключить чайник", "reminder.create"), ("remind me in 5 minutes to stretch", "reminder.create"),
    ("сколько времени", "time.now"),

    ("скриншот", "screenshot.take"), ("сделай скрин", "screenshot.take"), ("screenshot", "screenshot.take"),
    ("скриншот области", "screenshot.take"), ("сколько заряда", "sysinfo.get"), ("мой ip", "sysinfo.get"),
    ("очисти корзину", "recycle.empty"), ("заблокируй компьютер", "lock.screen"), ("выключи компьютер", "power.shutdown"),
    ("перезагрузи компьютер", "power.restart"), ("спящий режим", "power.sleep"), ("отмени выключение", "power.cancel"),
    ("включи wifi", "wifi.set"), ("включи блютуз", "bluetooth.set"), ("что в буфере", "clipboard.get"),
    ("скопируй привет в буфер", "clipboard.set"), ("напечатай привет", "type.text"), ("нажми ctrl+s", "keys.press"),
    ("заверши процесс chrome", "process.kill"), ("список процессов", "process.list"),

    ("открой загрузки", "open.folder"), ("открой документы", "open.folder"), ("открой настройки звука", "open.settings"),
    ("open sound settings", "open.settings"), ("открой ютуб", "open.url"), ("открой github", "open.url"),
    ("загугли рецепт борща", "web.search"), ("закажи роллы", "food.order"), ("погода завтра", "weather.get"),
    ("100 usd в рублях", "currency.convert"), ("вызови такси", "taxi.order"),

    ("нарисуй кота в космосе", "image.generate"), ("заметка купить молоко", "note.add"), ("открой панели в memuai", "memuai.open"),
    ("помощь", "assistant.help"), ("#ff8800 в rgb", "color.convert"), ("декодируй base64 aGVsbG8=", "text.encode"),
    ("sha256 привет", "hash.text"), ("сколько дней до нового года", "date.calc"), ("время в Токио", "time.zone"),
    ("брось 2 кубика", "random.pick"), ("сгенерируй пароль", "password.generate"), ("5 км в милях", "unit.convert"),

    ("что такое квазар", None), ("как закрыть программу в windows", None), ("напиши стих про осень", None), ("привет", None),
    ("почему небо голубое", None), ("найди ошибку в коде", None),
]

NOT_EXECUTED = {"weather.get", "currency.convert", "image.generate", "note.add", "text.translate", "dfy.start", "computer.use",
                "media.search", "food.order", "taxi.order", "web.search", "open.url", "memuai.open", "power.shutdown",
                "power.restart", "power.sleep", "reminder.create", "schedule.plan"}

class PhraseMatrixTest(Base):
    def test_every_phrase_maps_to_its_action(self):
        for text, expected in MATRIX:
            with self.subTest(text=text):
                found = intents.match(text, now=NOW)
                self.assertEqual(found["action"] if found else None, expected, found)

    def test_every_matched_phrase_executes_without_a_crash(self):
        executed = 0
        for text, expected in MATRIX:
            if not expected or expected in NOT_EXECUTED:
                continue
            self.backend = WinFake()
            self.engine.backend = self.backend
            self.engine.rate.allow = lambda *_a, **_k: True
            with self.subTest(text=text):
                found = self.engine.match(text)
                self.assertIsNotNone(found)
                card = run(self.engine.run(found["action"], found["params"]))
                if card["status"] == "confirm":
                    card = run(self.engine.confirm(card["confirm"]["token"], True))
                self.assertNotEqual(card.get("code"), "failed", card)
                self.assertNotIn(card["status"], ("unsupported",), card)
                if found["action"] not in ("app.open",):
                    self.assertIn(card["status"], ("done", "error"), card)
                if card["status"] == "error":
                    self.assertIn(card.get("code"), ("not_running", "no_window", "empty", "not_found"), (text, card))
                executed += 1
        self.assertGreater(executed, 90)

    def test_open_incy_resolves_the_start_menu_entry(self):
        for text in ("открой incy", "запусти инси", "открой штсн"):
            with self.subTest(text=text):
                found = self.engine.match(text)
                card = run(self.engine.run(found["action"], found["params"]))
                self.assertEqual(card["status"], "done", card)
                self.assertEqual(self.backend.calls[-1], ("launch", "INCY"))

if __name__ == "__main__":
    unittest.main()
