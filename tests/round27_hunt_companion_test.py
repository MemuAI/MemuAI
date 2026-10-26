"""Round 27, phase 2 (r27-hunt-companion): regressions for bugs found in the Telegram focus/group
handling, the companion, MemuAI Live, voice, local models, music and the installer.

Offline: no network, temporary data dir, stubbed models/audio.
"""
import asyncio
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app import realtime_live, reflect, telegram_bot, telegram_focus

ROOT = Path(__file__).resolve().parents[1]

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def _node(script):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])

class QuietReminderCarriesItsHold(unittest.TestCase):
    """Two reminders in one chat (an unanswered one from the previous quiet window and a new one):
    «Продолжить» on the old card sent the message held by the new card."""

    def setUp(self):
        telegram_focus._held.clear()
        telegram_focus._acknowledged.clear()

    def _hold(self, ends_at, text):
        window = {"until": "07:00", "ends_at": ends_at}
        with patch.object(reflect, "quiet_now", return_value=window):
            return telegram_focus.quiet_hold("7", {"message_id": len(text)}, text, {})

    def test_old_card_does_not_release_the_new_hold(self):
        old = self._hold(1_000, "вчера")[1]["inline_keyboard"][0]
        new = self._hold(2_000, "сегодня")[1]["inline_keyboard"][0]
        self.assertNotEqual(old[0]["callback_data"], new[0]["callback_data"])
        for button in old:
            self.assertTrue(telegram_focus.is_quiet_callback(button["callback_data"]))
            held, note = telegram_focus.quiet_answer("7", button["callback_data"], {})
            self.assertEqual(held, [])
        self.assertEqual(telegram_focus._acknowledged, {})
        held, _ = telegram_focus.quiet_answer("7", new[0]["callback_data"], {})
        self.assertEqual([text for _, text in held], ["сегодня"])
        self.assertEqual(telegram_focus._acknowledged, {"7": 2_000})

    def test_stale_continue_says_expired(self):
        old = self._hold(1_000, "a")[1]["inline_keyboard"][0][0]["callback_data"]
        self._hold(2_000, "b")
        _, note = telegram_focus.quiet_answer("7", old, {"telegram_language": "en"})
        self.assertIn("expired", note)
        self.assertFalse(telegram_focus.is_quiet_callback("fq:other:1"))
        self.assertFalse(telegram_focus.is_quiet_callback("fb:break"))

class GroupCommandsForOtherBots(unittest.TestCase):
    """In a group the bot answered /cmd@otherbot (and sent «/start@otherbot» to the model as a prompt)."""

    def _dispatched(self, text, username="MemuHomeBot"):
        runner = telegram_bot._BotRunner(client=object())
        runner.bot = {"username": username}
        spawned = []

        def spawn(coro, chat_id, *, ordered):
            spawned.append(chat_id)
            coro.close()
        runner._spawn = spawn
        runner.dispatch({"message": {"message_id": 1, "text": text, "chat": {"id": -100, "type": "supergroup"},
                                     "from": {"id": 5}}}, {})
        return bool(spawned)

    def test_foreign_commands_are_ignored(self):
        self.assertFalse(self._dispatched("/start@OtherBot"))
        self.assertFalse(self._dispatched("/new@other_bot hello"))
        self.assertTrue(self._dispatched("/start@memuhomebot"))
        self.assertTrue(self._dispatched("/start"))
        self.assertTrue(self._dispatched("write to support@example.com"))
        self.assertTrue(self._dispatched("/start@OtherBot", username=""))

class LiveMessagesFollowTheWindowLanguage(unittest.TestCase):
    """MemuAI Live errors (relay, config, connect) were Russian in the English window
    («Live-модель закрыла соединение: …» toast)."""

    def test_localize(self):
        loc = realtime_live.localize
        self.assertEqual(loc("Live-модель закрыла соединение: mock drop", "en"), "The live model closed the connection: mock drop")
        self.assertEqual(loc("Live-модель закрыла соединение: mock drop", "ru"), "Live-модель закрыла соединение: mock drop")
        self.assertEqual(loc("Добавьте API-ключ OpenAI GPT-Live в настройках «Голос и Live»", "en"),
                         "Add the OpenAI GPT-Live API key in “Voice & Live” settings")
        self.assertEqual(loc("Провайдер закрыл соединение (1008) bad key", "en"), "The provider closed the connection (1008) bad key")
        self.assertEqual(loc("Incorrect API key provided", "en"), "Incorrect API key provided")

    def test_every_config_error_has_english(self):
        cases = [{"voice_realtime_provider": "custom"},
                 {"voice_realtime_provider": "custom", "voice_realtime_base_url": "http://x"},
                 {"voice_realtime_provider": "custom", "voice_realtime_base_url": "ws://example.com"},
                 {"voice_realtime_provider": "openai-live"},
                 {"voice_realtime_provider": "custom-gemini", "voice_realtime_base_url": "wss://h/x"}]
        for prefs in cases:
            with self.assertRaises(realtime_live.RealtimeConfigError) as caught:
                realtime_live.LiveConfig.from_preferences(prefs)
            english = realtime_live.localize(str(caught.exception), "en")
            self.assertNotRegex(english, "[А-Яа-яЁё]", prefs)

    def test_relay_messages_have_english(self):
        api = _read("app/playground_api.py")
        start = api.index('@app.websocket("/api/voice/realtime")')
        relay = api[api.index("async def _realtime_connect"):start + 12000]
        import re
        relay = re.sub(r'"""[\s\S]*?"""', "", relay)
        for literal in re.findall(r'"([^"\n]*[А-Яа-яЁё][^"\n]*)"', relay):
            if literal.startswith(("Ретранслятор", "не выбрана")):
                continue
            sample = re.sub(r"\{[^}]*\}", "x", literal).replace("%s", "401")
            self.assertNotRegex(realtime_live.localize(sample, "en"), "[А-Яа-яЁё]", literal)
        self.assertIn('payload = {**payload, "message": realtime_live.localize(payload["message"], lang)}', api)

class MicrophoneFallbackAndErrors(unittest.TestCase):
    """A microphone chosen in settings and then unplugged broke every Live session and dictation
    («Микрофон недоступен: Requested device not found» — English browser text in the Russian window)."""

    HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const calls = [];
const ctx = { PG_LANG: "ru", console, performance: { now: () => 0 },
  pref: (k, d) => (k === "voice_input_device_id" ? "gone" : d), document: {}, location: {},
  DOMException: class extends Error { constructor(m, n) { super(m); this.name = n; } },
  navigator: { mediaDevices: { getUserMedia: async (c) => { calls.push(c);
    if (c.audio.deviceId) { const e = new Error("Requested device not found"); e.name = "OverconstrainedError"; throw e; }
    return { id: "default" }; } } } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("app/pgstatic/realtime.js", "utf8") + ";globalThis.MR = MemuRealtime;", ctx);
(async () => {
  const stream = await ctx.MR.microphone({ channelCount: 1 });
  const denied = ctx.MR.micError({ name: "NotAllowedError" });
  ctx.PG_LANG = "en";
  const missing = ctx.MR.micError({ name: "NotFoundError", message: "Requested device not found" });
  console.log(JSON.stringify({ stream, calls, denied, missing }));
})();
"""

    def test_unplugged_microphone_falls_back_to_default(self):
        out = subprocess.run(["node", "-e", self.HARNESS], capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(data["stream"], {"id": "default"})
        self.assertEqual(data["calls"][0]["audio"]["deviceId"], {"exact": "gone"})
        self.assertEqual(data["calls"][1]["audio"], {"channelCount": 1})
        self.assertIn("нет доступа", data["denied"])
        self.assertIn("no microphone found", data["missing"])

    def test_dictation_uses_it_and_mutes_the_wake_word(self):
        js = _read("app/pgstatic/app.js")
        body = js[js.index("async function startVoiceRecording"):js.index("async function finishVoiceRecording")]
        self.assertIn("await MemuRealtime.microphone({", body)
        self.assertIn("MemuRealtime.micError(error)", body)
        self.assertIn('source: "voice", on: seconds > 0, seconds', body)

        self.assertLess(body.index("voiceRecorder.start(250);"), body.index("wakeBusy(120); wakeRenew"))
        self.assertIn("clearInterval(wakeRenew); wakeBusy(0);", body)
        rt = _read("app/pgstatic/realtime.js")
        self.assertIn('t("Микрофон недоступен: ", "Microphone unavailable: ") + micError(error)', rt)
        self.assertIn("cameraError(error)", rt)

class CompanionStateConcurrentSaves(unittest.TestCase):
    """state.json: the desktop window thread and the HTTP routes saved at once — read-modify-write lost a
    patch and the shared «state.json.tmp» made the other replace fail."""

    def test_parallel_patches_all_land(self):
        import tempfile
        import threading
        from app import companion_avatar
        root = Path(tempfile.mkdtemp(prefix="r27c-state-"))
        errors = []

        def worker(i):
            try:
                for _ in range(15):
                    if i % 2:
                        companion_avatar.save_state(root, {"desktop_by_monitor": {f"m{i}": {"x": i, "y": 0, "w": 300, "h": 440}}})
                    else:
                        companion_avatar.save_state(root, {"live_window": {"x": 0.5, "y": 0.25}})
            except Exception as exc:     # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        state = companion_avatar.load_state(root)
        self.assertEqual(sorted(state["desktop_by_monitor"]), ["m1", "m3", "m5", "m7"])
        self.assertEqual(list(root.glob("*.tmp")), [])

    def test_proxy_helper_error_in_english(self):
        from app import companion
        with patch.object(companion, "default_model", return_value=""):
            async def first():
                async for _ in companion.answer([], "hi", language="en"):
                    pass
            with self.assertRaisesRegex(ValueError, "No model is enabled"):
                asyncio.run(first())

class InstallerWhatsNewIsAbout31(unittest.TestCase):
    """«Что нового в MemuAI 3.1» used to list stale features; round 38 removed the «What's new» button and dialog
    from the Setup at the user's request — they must not come back."""

    def test_no_whats_new_button_or_dialog(self):
        html = _read("installer/ui/index.html")
        app = _read("installer/ui/app.js")
        i18n = _read("installer/ui/i18n.js")
        for needle in ("btn-notes", "notes-label"):
            self.assertNotIn(needle, html)
            self.assertNotIn(needle, app)
        self.assertNotIn("showNotes", app)
        self.assertNotIn("notes.title", app)
        for text in ('"notes.title"', "Что нового в {name}", "What’s new in {name}"):
            self.assertNotIn(text, i18n)
        for rel in ("installer/ui/app.js", "installer/ui/i18n.js"):
            self.assertEqual(subprocess.run(["node", "--check", str(ROOT / rel)]).returncode, 0)

if __name__ == "__main__":
    unittest.main()
