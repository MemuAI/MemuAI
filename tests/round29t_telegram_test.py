"""Round 29 phase 2 (r29t-telegram): regressions for the Telegram bot, the Mini App and its search.

Each test names the bug it guards (see .phase2/r29t-telegram.md, fixes 1–13 and later)."""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import tg_webapp, telegram_voice, tgapp_extras  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TGAPP = ROOT / "app" / "tgapp"
TOKEN = "123456:TEST-token_for-mini-app"
USER = 4343
NODE = shutil.which("node")

def _js_function(source: str, name: str) -> str:
    """The text of ``function name(...) { ... }`` from a JS file (brace matched, strings ignored loosely)."""
    start = source.index(f"function {name}(")
    depth, i, opened = 0, source.index("{", start), False
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
            opened = True
        elif ch == "}":
            depth -= 1
            if opened and depth == 0:
                return source[start:i + 1]
        i += 1
    raise ValueError(name)

def _node(script: str) -> str:
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return out.stdout

def _tone(fmt: str, codec: str, seconds: float = 1.0, rate: int = 48000) -> bytes:
    import av
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=fmt) as out:
        stream = out.add_stream(codec, rate=rate)
        stream.layout = "mono"
        samples = np.array([[math.sin(2 * math.pi * 440 * n / rate) * 0.3 for n in range(int(rate * seconds))]],
                           dtype=np.float32)
        fmt_name = "fltp" if codec in ("libopus", "aac", "opus") else "s16"
        if fmt_name == "s16":
            samples = (samples * 32767).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples, format=fmt_name, layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()

class VoiceDecoding(unittest.TestCase):
    def setUp(self):
        try:
            import av  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("PyAV / numpy not installed")

    def test_decode_does_not_use_the_removed_metadata_errors_argument(self):
        import av
        from app import voice_runtime
        wav = _tone("wav", "pcm_s16le", 1.0, 48000)
        real_open = av.open

        def strict_open(*args, **kwargs):
            if "metadata_errors" in kwargs:
                raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
            return real_open(*args, **kwargs)
        with mock.patch.object(av, "open", strict_open):
            pcm = voice_runtime.decode_audio_pcm(wav)
        self.assertEqual(str(pcm.dtype), "float32")
        self.assertEqual(pcm.ndim, 1)
        self.assertAlmostEqual(len(pcm) / 16000, 1.0, delta=0.05)
        self.assertLessEqual(float(abs(pcm).max()), 1.0)
        self.assertGreater(float(abs(pcm).max()), 0.2)

    def test_decode_opus_from_a_path_and_empty_audio(self):
        from app import voice_runtime
        try:
            ogg = _tone("ogg", "libopus", 0.5, 48000)
        except Exception as exc:  # pragma: no cover - codec missing in this PyAV build
            self.skipTest(f"no opus encoder: {exc}")
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as handle:
            handle.write(ogg)
        try:
            pcm = voice_runtime.decode_audio_pcm(handle.name)
        finally:
            os.unlink(handle.name)
        self.assertAlmostEqual(len(pcm) / 16000, 0.5, delta=0.08)

    def test_transcribe_local_feeds_pcm_and_maps_errors(self):
        from app import voice_runtime
        source = (ROOT / "app" / "voice_runtime.py").read_text(encoding="utf-8")
        self.assertIn("decode_audio_pcm(path)", source)
        self.assertIn("Не удалось прочитать аудио", source)
        self.assertEqual(telegram_voice._friendly(RuntimeError("Не удалось прочитать аудио"), "en"), "Could not read the audio")
        self.assertTrue(callable(voice_runtime.decode_audio_pcm))

    def test_requirements_pin_av_below_19(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^av>=\d+,<19\s*$")

class FakeApi:
    def __init__(self):
        self.calls = []

    async def call(self, method, **payload):
        self.calls.append((method, payload, time.monotonic()))
        if method == "sendMessage":
            return {"message_id": 77}
        if method == "getFile":
            return {"file_path": "voice/1.oga", "file_size": 3000}
        return True

    async def download(self, path, max_bytes):
        return b"o" * 3000

class BotTyping(unittest.TestCase):
    def test_typing_is_renewed_until_the_transcript_is_ready_and_then_stops(self):
        api = FakeApi()
        ok = {"available": True, "engine": "app", "model": "small", "reason": "", "message": ""}

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.25)
            return "hello"

        async def scenario():
            routed = await telegram_voice.route_message(api, "5", {"message_id": 3, "voice": {"file_id": "f", "duration": 4}}, {}, "en")
            count = sum(1 for c in api.calls if c[0] == "sendChatAction")
            await asyncio.sleep(0.15)
            return routed, count, sum(1 for c in api.calls if c[0] == "sendChatAction")
        with mock.patch.object(telegram_voice, "TYPING_EVERY", 0.04), \
                mock.patch.object(telegram_voice, "status", return_value=ok), \
                mock.patch.object(telegram_voice, "transcribe", slow):
            routed, during, later = asyncio.run(scenario())
        self.assertEqual(routed["text"], "hello")
        self.assertGreaterEqual(during, 4, "«typing…» must be renewed while transcribing")
        self.assertEqual(during, later, "and must stop once the transcript is ready")
        self.assertTrue(all(c[1]["action"] == "typing" for c in api.calls if c[0] == "sendChatAction"))

    def test_default_interval_is_below_telegrams_five_seconds(self):
        self.assertLess(telegram_voice.TYPING_EVERY, 5)

class BotModelNames(unittest.TestCase):
    def test_uri_models_show_the_model_not_the_release_tag(self):
        from app import telegram_bot as bot

        self.assertEqual(bot._short_model("gpt://b1gfolder/deepseek-v4.1-flash/latest", {}, limit=40), "DeepSeek V4.1 Flash")
        self.assertEqual(bot._short_model("ydx-gpt://b1g/yandexgpt/rc", {}, limit=40), "YandexGPT")
        self.assertEqual(bot._short_model("genspark-claude-sonnet-4", {}, limit=40), "Claude Sonnet 4")
        self.assertEqual(bot._short_model("m", {"model_aliases": {"m": "Мой"}}), "Мой")
        short = bot._short_model("provider/a-very-long-model-name-that-goes-on", {})
        self.assertTrue(short.endswith("…"))
        self.assertLessEqual(len(short), 18)

    def test_model_button_long_names_get_an_ellipsis(self):
        from app import telegram_bot as bot
        name = "x" * 80
        button = bot._model_button(name, {}, current=name)
        self.assertTrue(button["text"].startswith("✓ "))
        self.assertTrue(button["text"].endswith("…"))
        self.assertLessEqual(len(button["text"]), 56)
        self.assertEqual(bot._model_button("short", {}, current="")["text"], "Short")
        self.assertEqual(bot._model_button("aws9-glm-5.3", {}, current="")["text"], "GLM-5.3 · aws9-glm-5.3")

@unittest.skipUnless(NODE, "node is required")
class MiniAppFrontEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (TGAPP / "app.js").read_text(encoding="utf-8")
        cls.css = (TGAPP / "app.css").read_text(encoding="utf-8")
        cls.layout = (TGAPP / "layout.css").read_text(encoding="utf-8")
        cls.extras = (TGAPP / "extras.js").read_text(encoding="utf-8")

    def test_model_name_of_uri_and_prefixed_ids(self):
        fn = _js_function(self.app, "modelName")
        script = ("const S={models:[{id:'named',name:'Nice name'}]};" + fn +
                  ";console.log(JSON.stringify(['ydx-gpt://b1g4abc/deepseek-v4.1-flash/latest','gpt://f/yandexgpt',"
                  "'genspark-claude-sonnet-4','grok-4','named','plain-model'].map(modelName)))")
        self.assertEqual(json.loads(_node(script)),
                         ["deepseek-v4.1-flash", "yandexgpt", "claude-sonnet-4", "grok-4", "Nice name", "plain-model"])

    def test_thinking_pill_icon_is_sized_and_stays_on_one_line(self):
        self.assertRegex(self.css, r"\.thinking summary svg\s*\{[^}]*width:\s*16px")
        self.assertRegex(self.css, r"\.thinking summary\s*\{[^}]*white-space:\s*nowrap")

    def test_error_banner_offers_retry(self):
        self.assertRegex(self.app, r'class="msg-error">[^\n]*data-regen="1"')

    def test_enter_while_answering_does_not_stop(self):
        block = self.app[self.app.index('if (e.key === "Enter" && !e.shiftKey'):][:900]
        self.assertIn("Stop", block)
        self.assertRegex(block, r"C\.job|busy|running")

    def test_rail_is_wide_enough_for_labels(self):
        width = int(re.search(r"--rail-w:\s*(\d+)px", self.layout).group(1))
        self.assertGreaterEqual(width, 84)

    def test_search_empty_state_only_without_results_and_preview_subtitle(self):
        self.assertIn("data.subtitle && data.subtitle !== data.title", self.app)

    def test_voice_switch_titles_wrap(self):
        self.assertRegex(self.css, r"label\.cell \.cell-title\s*\{[^}]*white-space:\s*normal")

    def test_prompt_previews_strip_identity_tags(self):
        match = re.search(r"p\.text\.replace\((/[^\n]*?/gi), \" \"\)\.replace\(/\\s\+/g, \" \"\)", self.extras)
        self.assertTrue(match, "the prompt preview cleans tags")
        script = (f"const re={match.group(1)};console.log('<identity>You are a <b>poet</b></identity>\\n<rules x=\"1\">"
                  "Rhyme</rules> a < b'.replace(re,' ').replace(/\\s+/g,' ').trim())")
        self.assertEqual(_node(script).strip(), "You are a poet Rhyme a < b")

class SearchUnits(unittest.TestCase):
    def test_snippet_starts_near_the_match(self):
        text = ("Вступление " * 30) + "здесь важное слово борщ и ещё немного текста"
        snippet = tgapp_extras._snippet(text, "борщ")
        self.assertTrue(snippet.startswith("…"))
        self.assertIn("борщ", snippet[:80])
        self.assertFalse(snippet[1:].startswith(" "))
        self.assertEqual(tgapp_extras._snippet("коротко про борщ", "борщ"), "коротко про борщ")

    def test_word_form_only_chats_are_kept(self):
        rows = [{"id": "a", "title": "Суп", "updated": 5, "messages": []},
                {"id": "b", "title": "Чай", "updated": 6, "messages": [], "temp": True}]
        found = {"groups": [{"type": "chat", "items": [
            {"chat_id": "a", "title": "Суп", "title_highlights": [], "hits": [{"role": "user", "snippet": "борщи", "highlights": [[0, 5]]}]},
            {"chat_id": "b", "title": "Чай", "title_highlights": [], "hits": []},
            {"chat_id": "zzz", "title": "?", "title_highlights": [], "hits": []}]}]}
        merged = tgapp_extras.merge_chat_hits([], found, rows)
        self.assertEqual([row["id"] for row in merged], ["a"])
        self.assertEqual(merged[0]["matches"][0]["highlights"], [[0, 5]])

    def test_stem_joins_short_j_nouns(self):
        from app.search_index import stem
        for form in ("чаем", "чаю", "чая", "чаи", "чай"):
            self.assertEqual(stem(form), stem("чай"), form)
        self.assertNotEqual(stem("чем"), stem("чай"))
        self.assertEqual(stem("борщи"), stem("борщ"))

class MiniAppApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app import tgapp_chat
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER)})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare", "code_control": True, "web_login": True})
        cls.models = mock.patch.object(tgapp_chat, "chat_models", return_value=[
            {"id": "test/model", "name": "Test", "provider": "test", "vision": False, "pinned": False, "default": True}])
        cls.models.start()
        from app.tgapp_api import create_tgapp, auth_failures, auth_limiter
        auth_failures._hits.clear()
        auth_limiter._hits.clear()
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()
        response = cls.client.post("/tgapp/api/auth", json={"init_data": tg_webapp.sign_init_data(
            {"auth_date": int(time.time()), "query_id": "AAH", "user": {"id": USER, "first_name": "T"}}, TOKEN)})
        cls.auth = {"Authorization": "Bearer " + response.json()["token"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.models.stop()
        tg_webapp.save_config({"enabled": False})

    def post(self, name, body=None):
        return self.client.post("/tgapp/api/" + name, json=body or {}, headers=self.auth)

    def test_run_now_on_a_disabled_task_and_a_rule_task(self):
        from app.pgdata import load_schedules, save_schedules
        save_schedules([{"id": "off", "name": "Off", "prompt": "x", "daily_at": "09:00", "enabled": False},
                        {"id": "rule", "name": "Rule", "prompt": "y", "enabled": False, "rule": {"kind": "interval"}}])
        before = time.time()
        self.assertEqual(self.post("schedules.run", {"id": "off"}).status_code, 200)
        self.assertEqual(self.post("schedules.run", {"id": "rule"}).status_code, 200)
        rows = {row["id"]: row for row in load_schedules()}
        self.assertTrue(rows["off"]["enabled"])
        self.assertGreaterEqual(rows["off"]["next_run"], before - 1)
        self.assertGreaterEqual(rows["rule"]["run_now"], before - 1)
        self.assertFalse(rows["rule"]["enabled"], "a paused rule runs once and stays paused")

    def test_re_enabling_replans_the_next_run(self):
        from app.pgdata import load_schedules, save_schedules
        save_schedules([{"id": "t", "name": "T", "prompt": "x", "daily_at": "09:00", "enabled": False,
                         "next_run": time.time() - 86400 * 3}])
        self.post("schedules.toggle", {"id": "t", "enabled": True})
        row = load_schedules()[0]
        self.assertTrue(row["enabled"])
        self.assertFalse(row.get("next_run") and row["next_run"] < time.time() - 3600,
                         "a stale slot must not fire at once")

    def test_prompt_library_marks_the_default_system_prompt(self):
        rows = self.post("prompts.list").json()["prompts"]
        defaults = [row["id"] for row in rows if row["default"]]
        self.assertEqual(defaults, ["memuai-default"])
        extras = (TGAPP / "extras.js").read_text(encoding="utf-8")
        self.assertIn("...(p.default ? [] :", extras)

    def test_voice_switch_explanations_are_not_clamped(self):
        css = (TGAPP / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"label\.cell \.cell-sub\s*\{[^}]*-webkit-line-clamp:\s*unset")

    def test_word_form_search_through_the_api(self):
        from app.pgdata import upsert_chat
        upsert_chat({"id": "c-tea", "title": "Утро", "mode": "chat", "updated": time.time() * 1000,
                     "messages": [{"role": "user", "content": "Заварил зелёный чай с мятой", "createdAt": 1}]})
        data = self.post("chats.search", {"query": "чаем"}).json()
        self.assertIn("c-tea", [hit["id"] for hit in data["hits"]])

@unittest.skipUnless(NODE, "node is required")
class PhoneSignInAndCode(unittest.TestCase):
    def test_used_link_error_survives_the_first_draw(self):
        web = (TGAPP / "web.js").read_text(encoding="utf-8")
        self.assertIn("let notice = initialError", web)
        self.assertIn('Waiting for the confirmation in Telegram…"), notice)', web)
        self.assertNotIn('if (initialError) draw("", initialError);', web)

    def test_dialog_text_is_unwrapped_but_code_stays(self):
        code = (TGAPP / "code.js").read_text(encoding="utf-8")
        fn = _js_function(code, "unwrapBody")
        text = ("MemuCode may read, change and run files in\nthis folder. Project hooks and MCP servers execute\n"
                "commands from the repository.\n\n- one item that is long enough here\n  continued\n+ added line\nrm -rf build")
        out = _node(fn + ";console.log(JSON.stringify(unwrapBody(" + json.dumps(text) + ")))")
        self.assertEqual(json.loads(out), "MemuCode may read, change and run files in this folder. Project hooks and MCP "
                         "servers execute commands from the repository.\n\n- one item that is long enough here\n"
                         "  continued\n+ added line\nrm -rf build")

    def test_live_reasoning_tail_starts_at_a_boundary(self):
        code = (TGAPP / "code.js").read_text(encoding="utf-8")
        fn = _js_function(code, "tailText")
        out = json.loads(_node(fn + ";console.log(JSON.stringify([tailText('short', 600), tailText('abc def ghi jkl', 9),"
                                    " tailText('xxxx\\nline two here', 15)]))"))
        self.assertEqual(out, ["short", "…ghi jkl", "line two here"])
        self.assertIn("tailText(live.reasoning, 600)", code)

    def test_computer_only_commands_are_hidden_and_refused(self):
        code = (TGAPP / "code.js").read_text(encoding="utf-8")
        match = re.search(r"const LOCAL_ONLY = new Set\((\[[^\]]*\])\)", code)
        names = set(json.loads(match.group(1)))
        self.assertTrue({"voice", "paste", "copy", "exit", "vim", "terminal-setup"} <= names)
        self.assertNotIn("model", names)
        self.assertIn("LOCAL_ONLY.has(String(row.name", code)
        self.assertIn("LOCAL_ONLY.has(local[1].toLowerCase())", code)

class HostNotes(unittest.TestCase):
    def test_interrupted_note_is_skipped_and_tree_corner_dropped(self):
        from app.cli import remote_host

        class Block:
            kind = "notice"
            version = 0

            def __init__(self, text):
                self.source = text

        class UI:
            pass
        ui = UI()
        ui.transcript = UI()
        ui.transcript.blocks = [Block("  ⎿  Interrupted · What should MemuCode do instead?"), Block("  ⎿  Saved to notes"),
                                Block("✻ Worked for 3s")]
        texts = [row["text"] for row in remote_host.notes_feed(ui)]
        self.assertEqual(texts, ["Saved to notes", "✻ Worked for 3s"])

class FuzzyDoesNotOverrideStems(unittest.TestCase):
    def setUp(self):
        from app import search_index as si
        from app.pgdata import MEMU_DIR
        self.si, self.old = si, si.INDEX
        si.INDEX = si.SearchIndex()
        Path(MEMU_DIR).mkdir(parents=True, exist_ok=True)
        now = time.time() * 1000
        chats = [{"id": "tea", "title": "Утро", "updated": now, "messages": [
                     {"role": "user", "content": "Пью зелёный чай с мятой", "createdAt": now - 1}]},
                 {"id": "what", "title": "Суп", "updated": now, "messages": [
                     {"role": "user", "content": "Чем подавать борщ?", "createdAt": now - 1}]}]
        (Path(MEMU_DIR) / "chats.json").write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
        si.register_provider(si.ChatsProvider())

    def tearDown(self):
        self.si.INDEX = self.old

    def test_word_form_hit_keeps_look_alikes_out(self):
        items = self.si.search("чаем")["items"]
        self.assertEqual([item["title"] for item in items], ["Утро"])
        self.assertEqual(self.si.stem("чае"), self.si.stem("чай"))

    def test_forms_of_j_nouns_are_highlighted(self):
        regex = self.si._word_regex({"чаем"}, {"чай"})
        found = [m.group(0) for m in regex.finditer("чем заменить сахар в чае, пью чай, час и чаша, чаем и чаю")]
        self.assertEqual(found, ["чае", "чай", "чаем", "чаю"])

    def test_a_real_typo_still_finds_the_word(self):
        self.assertIn("Суп", [item["title"] for item in self.si.search("борш")["items"]] +
                      [item["title"] for item in self.si.search("боршь")["items"]])

class MiniAppLookWaveC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (TGAPP / "app.js").read_text(encoding="utf-8")
        cls.css = (TGAPP / "app.css").read_text(encoding="utf-8")
        cls.layout = (TGAPP / "layout.css").read_text(encoding="utf-8")
        cls.md = (TGAPP / "md.js").read_text(encoding="utf-8")

    def test_25_search_mark_has_contrast(self):
        rule = re.search(r"(?m)^mark\s*\{([^}]*)\}", self.layout)
        self.assertTrue(rule)
        self.assertIn("var(--accent)", rule.group(1))
        self.assertIn("color: var(--text)", rule.group(1))

    def test_26_tab_labels_fit_on_narrow_phones(self):
        self.assertRegex(self.css, r"@media \(max-width: 380px\) \{ \.tab \{ font-size: 9\.5px")

    def test_27_fab_leaves_room_for_the_last_row(self):
        self.assertTrue(".view:has(> .fab) { padding-bottom: calc(var(--tabbar-h) + var(--safe-bottom) + 96px); }" in self.css)

    def test_29_settings_value_gives_way_to_the_title(self):
        self.assertRegex(self.css, r"\.cell-body:has\(> \.cell-title:only-child\) \+ \.cell-value \{ flex: 0 100 auto")

    def test_30_new_chat_button_hides_while_searching(self):

        self.assertIn('fab.classList.toggle("fab-off", !!S.chatQuery.trim()', self.app)
        self.assertRegex(self.css, r"\.fab\.fab-off \{[^}]*pointer-events: none")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_28_math_without_katex(self):
        script = ("global.window={};" + self.md + ";const r=window.MemuMD.render;console.log(JSON.stringify(["
                  "r('Формула: $E = mc^2$'), r('$x_1 + x_{12}$'), r('\\\\(\\\\frac{a+b}{2} \\\\le \\\\alpha\\\\)'),"
                  "r('costs $5 and $10 today'), r('$$\\n\\\\sqrt{x^2+y^2}\\n$$'), r('**b** $a*b*c$')]))")
        out = json.loads(_node(script))
        self.assertIn('mc<sup>2</sup>', out[0])
        self.assertIn('x<sub>1</sub> + x<sub>12</sub>', out[1])
        self.assertNotIn("<em>", out[1])
        self.assertIn("(a+b)⁄2 ≤ α", out[2])
        self.assertEqual(out[3], "<p>costs $5 and $10 today</p>")
        self.assertIn('md-math-block', out[4])
        self.assertIn('√<span class="md-sqrt">x<sup>2</sup>+y<sup>2</sup></span>', out[4])
        self.assertIn('<span class="md-math">a*b*c</span>', out[5])
        self.assertNotIn("$$", out[4])

class ModulesWaveC(unittest.TestCase):
    def test_31_narrow_module_window_keeps_its_title(self):
        css = (ROOT / "app" / "pgstatic" / "modules.css").read_text(encoding="utf-8")
        self.assertIn(".module-window-bar { container-type: inline-size; }", css)
        block = css[css.index("@container (max-width: 460px)"):][:400]
        self.assertIn(".module-window-tools button { width: 27px; }", block)

    def test_32_module_model_names_are_short(self):
        src = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

        block = src[src.index('def _module_pretty_name'):][:9000]
        self.assertIn('aliases.get(model_id)', block)
        pattern = re.search(r're\.sub\(r"(\^\[a-z\][^"]+)", r"\\1", model_id', block)
        self.assertTrue(pattern)
        self.assertEqual(re.sub(pattern.group(1), r"\1", "ydx-gpt://b1g4/deepseek-v4.1-flash/latest", flags=re.I),
                         "deepseek-v4.1-flash")

    def _report(self, js):
        from app import modules_validate as mv
        files = {"manifest.json": json.dumps({"id": "t-mod", "title": "T", "permissions": ["chat", "storage"]}),
                 "index.html": '<div class="mu-page"><canvas id="c" width="200" height="200"></canvas></div>',
                 "style.css": "", "main.js": js, "i18n.json": json.dumps({"ru": {}, "en": {}})}
        return mv.validate_package(mv._from_files(files, "t"), check_syntax=False)

    def test_33_memu_complete_counts_as_a_model_call(self):
        report = self._report("Memu.complete({messages: []}).then(() => {});")
        texts = [p.get("en", "") for p in report["problems"]]
        self.assertFalse(any("never talks to a model" in t for t in texts), texts)

    def test_34_canvas_css_variable_is_an_error_in_validator_harness_and_pack(self):
        report = self._report("const ctx = document.getElementById('c').getContext('2d');\nctx.fillStyle = 'var(--panel)';\n")
        hits = [p for p in report["problems"] if "Canvas" in p.get("en", "")]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["level"], "error")
        self.assertEqual(hits[0]["line"], 2)
        ok = self._report("const c = getComputedStyle(document.documentElement).getPropertyValue('--panel').trim();\n"
                          "document.getElementById('c').getContext('2d').fillStyle = c;\n")
        self.assertFalse([p for p in ok["problems"] if "Canvas" in p.get("en", "")])
        from app import modules_ai_pack as pack
        en = pack.build_pack("en", include_harness=True)
        self.assertIn("Canvas does not understand CSS variables", en)
        self.assertIn("Common first-try mistakes", en)
        self.assertIn("js_raw_rules", en)
        self.assertIn('["js", pkg.js, RULES.js_raw_rules || []]', (ROOT / "app" / "pgstatic" / "memu-module-test.js").read_text(encoding="utf-8"))
        self.assertIn("Частые ошибки первой попытки", pack.build_pack("ru", include_harness=False))

    STORE_BAD = ["Memu.store.get(`pomodoro:${d}`, 0)", 'Memu.store.get("_x")', 'Memu.store.get("my key")',
                 'Memu.store.get("заметки")', 'Memu.store.set("a//b", 1)', 'Memu.store.set("/a", 1)',
                 'Memu.store.set("a/", 1)', 'Memu.store.set("a./b", 1)', 'Memu.store.set("notes.", 1)',
                 "Memu.store.remove('a/.b')", 'Memu.store.list("x:")', "Memu.store.set(`note ${i}`, v)"]
    STORE_GOOD = ['Memu.store.set("days/2026-10-10", 1)', "Memu.store.get(`days/${d}`)", 'Memu.store.list("chats/")',
                  "Memu.store.get(key)", 'Memu.store.list("")', "Memu.store.list()", 'Memu.store.set("notes.v2", x)',
                  "Memu.store.get(`n/${id}/v`)", "Memu.store.get(`${p}/x`)", "Memu.store.get('a.b-c_d/e')"]

    def test_35_store_key_rule_in_validator_pack_and_harness(self):
        for js in self.STORE_BAD:
            hits = [p for p in self._report(js)["problems"] if "Memu.store key" in p.get("en", "")]
            self.assertEqual([p["level"] for p in hits], ["error"], js)
        for js in self.STORE_GOOD:
            self.assertFalse([p for p in self._report(js)["problems"] if "Memu.store key" in p.get("en", "")], js)
        from app import modules_ai_pack as pack
        self.assertIn('not "pomodoro:2026-10-10"', pack.build_pack("en", include_harness=False))
        self.assertIn('а не "pomodoro:2026-10-10"', pack.build_pack("ru", include_harness=False))

    @unittest.skipUnless(NODE, "node is required")
    def test_35_harness_key_check_matches_the_server_and_names_the_key(self):
        from app.characters_api import check_key
        src = (ROOT / "app" / "pgstatic" / "memu-module-test.js").read_text(encoding="utf-8")
        start = src.index("const keyOk = ")
        fn = src[start:src.index("const need = ", start)]
        self.assertIn("JSON.stringify(key)", src[src.index("const need = ", start):][:600])
        keys = ["days/2026-10-10", "a", "a/b/c", "a/b/c/d", "_x", "a//b", "/a", "a/", "notes.", "a./b", "con", "nul.txt/x",
                "com1", "pomodoro:1", "my key", " days/1 ", "заметки", "x" * 96, "x" * 97, "a.b-c_d", "", "a/.b", "..", "a/../b"]
        out = json.loads(_node(fn + "console.log(JSON.stringify(" + json.dumps(keys) + ".map(keyOk)))"))

        def server(key):
            try:
                check_key(key)
                return True
            except ValueError:
                return False
        self.assertEqual(dict(zip(keys, out)), {key: server(key) for key in keys})

if __name__ == "__main__":
    unittest.main()
