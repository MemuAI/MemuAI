"""Round 24 (r24-quick-settings): меню-слои, голос быстрого доступа, новые команды, анализ YouTube.

Без сети: YouTube — фикстуры (oEmbed, страница watch с ytInitialPlayerResponse, timedtext json3/vtt).
Браузерная часть (headless Chromium, если есть) воспроизводит баг «Озвучка не закрывается»: выпадающий
список pretty-select, чей обработчик change перерисовывает панель, раньше оставлял меню в <body> навсегда.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

os.environ.setdefault("MEMUAI_ASSISTANT_NO_WARM", "1")

from app import assistant_commands  # noqa: F401  (устанавливает действия Round 24)
from app import assistant_catalog as catalog
from app import assistant_intents as intents
from app import assistant_r24 as r24
from app import youtube_analysis as yt

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
VID = "dQw4w9WgXcQ"

PLAYER = {
    "playabilityStatus": {"status": "OK"},
    "videoDetails": {"videoId": VID, "lengthSeconds": "212"},
    "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"baseUrl": "https://www.youtube.com/api/timedtext?v=" + VID + "&lang=en&kind=asr", "languageCode": "en", "kind": "asr",
         "name": {"simpleText": "English (auto-generated)"}, "isTranslatable": True},
        {"baseUrl": "https://www.youtube.com/api/timedtext?v=" + VID + "&lang=ru", "languageCode": "ru",
         "name": {"runs": [{"text": "Русский"}]}, "isTranslatable": True},
    ]}},
}
WATCH = ("<html><script>var ytInitialPlayerResponse = " + json.dumps(PLAYER) +
         ";var meta = {\"a\": \"}{\"};</script></html>")
JSON3 = json.dumps({"events": [
    {"tStartMs": 0, "segs": [{"utf8": "Привет"}, {"utf8": " всем"}]},
    {"tStartMs": 1500},
    {"tStartMs": 45000, "segs": [{"utf8": "Это &amp; тест"}]},
    {"tStartMs": 125000, "segs": [{"utf8": "Вывод"}]},
]})
VTT = """WEBVTT
Kind: captions
Language: ru

00:00:01.000 --> 00:00:03.000
<c>Первая</c> строка

00:01:05.500 --> 00:01:07.000
Вторая строка
"""
OEMBED = json.dumps({"title": "Тестовое видео", "author_name": "Автор", "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg"})


def fake_fetch(fail_json3: bool = False, no_captions: bool = False, status: str = "OK"):
    calls = []

    async def fetch(url: str) -> str:
        calls.append(url)
        if "oembed" in url:
            return OEMBED
        if "/watch?" in url:
            player = json.loads(json.dumps(PLAYER))
            player["playabilityStatus"]["status"] = status
            if no_captions:
                player.pop("captions")
            return "<script>var ytInitialPlayerResponse = " + json.dumps(player) + ";</script>"
        if "timedtext" in url:
            if "fmt=json3" in url:
                if fail_json3:
                    raise RuntimeError("HTTP 429")
                return JSON3
            return VTT
        raise AssertionError(url)
    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def run(coro):
    return asyncio.run(coro)


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        yt._CACHE.clear()

    def test_video_id_forms(self):
        for url in (f"https://www.youtube.com/watch?v={VID}&t=10", f"https://youtu.be/{VID}?si=x", f"youtube.com/shorts/{VID}",
                    f"https://m.youtube.com/watch?feature=share&v={VID}", f"https://www.youtube.com/embed/{VID}",
                    f"https://music.youtube.com/watch?v={VID}", f"https://www.youtube.com/live/{VID}", VID,
                    f"посмотри https://youtu.be/{VID} пожалуйста"):
            self.assertEqual(yt.video_id(url), VID, url)
        for bad in ("https://example.com/watch?v=" + VID, "https://www.youtube.com/@channel", "", "abc"):
            self.assertEqual(yt.video_id(bad), "", bad)

    def test_player_response_with_braces_in_strings(self):
        self.assertEqual(yt.player_response(WATCH)["videoDetails"]["videoId"], VID)
        self.assertEqual(yt.player_response("<html>nothing</html>"), {})

    def test_track_choice_prefers_user_language_and_manual(self):
        tracks = yt.caption_tracks(PLAYER)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[1]["name"], "Русский")
        self.assertEqual(yt.pick_track(tracks, "ru")[0]["lang"], "ru")
        self.assertEqual(yt.pick_track(tracks, "en")[0]["lang"], "en")
        self.assertEqual(yt.pick_track(tracks, "de")[0]["lang"], "ru")   # ручная лучше авто
        self.assertEqual(yt.pick_track([], "ru"), (None, ""))

    def test_transcript_json3_and_vtt_fallback(self):
        data = run(yt.transcript(VID, "ru", fake_fetch(), use_ytdlp=False))
        self.assertEqual(data["source"], "timedtext")
        self.assertEqual([s["text"] for s in data["segments"]], ["Привет всем", "Это & тест", "Вывод"])
        self.assertEqual(data["duration"], 212)
        data = run(yt.transcript(VID, "ru", fake_fetch(fail_json3=True), use_ytdlp=False))
        self.assertEqual([s["text"] for s in data["segments"]], ["Первая строка", "Вторая строка"])
        self.assertAlmostEqual(data["segments"][1]["start"], 65.5)

    def test_honest_errors(self):
        with self.assertRaises(yt.TranscriptUnavailable) as cm:
            run(yt.transcript(VID, "ru", fake_fetch(no_captions=True), use_ytdlp=False))
        self.assertEqual(cm.exception.code, "no_captions")
        with self.assertRaises(yt.TranscriptUnavailable) as cm:
            run(yt.transcript(VID, "ru", fake_fetch(no_captions=True, status="LOGIN_REQUIRED"), use_ytdlp=False))
        self.assertEqual(cm.exception.code, "login")
        with self.assertRaises(yt.TranscriptUnavailable) as cm:
            run(yt.load("https://example.com", "ru", fake_fetch()))
        self.assertEqual(cm.exception.code, "bad_url")

    def test_chunks_carry_timestamps(self):
        segs = [{"start": i * 10.0, "text": "слово " * 20} for i in range(200)]
        chunks = yt.chunk_segments(segs, 2000)
        self.assertGreater(len(chunks), 5)
        for ch in chunks:
            self.assertLessEqual(len(ch), 2200)
            self.assertTrue(ch.startswith("["), ch[:20])

    def test_link_timestamps(self):
        out = yt.link_timestamps("Начало [0:45], потом (1:02:03) и [99:99] и [ссылка](http://x)", VID)
        self.assertIn(f"[0:45](https://www.youtube.com/watch?v={VID}&t=45s)", out)
        self.assertIn(f"[1:02:03](https://www.youtube.com/watch?v={VID}&t=3723s)", out)
        self.assertIn("[99:99]", out)
        self.assertIn("[ссылка](http://x)", out)

    def test_analyze_single_and_map_reduce_with_cache(self):
        prompts = []

        async def complete(prompt: str) -> str:
            prompts.append(prompt)
            return "Главное [0:45]"
        fetch = fake_fetch()
        res = run(yt.analyze(f"https://youtu.be/{VID}", complete, mode="timestamps", fetch=fetch, use_ytdlp=False))
        self.assertEqual(res["title"], "Тестовое видео")
        self.assertIn("&t=45s", res["text"])
        self.assertEqual(len(prompts), 1)
        self.assertIn("оглавление", prompts[0].lower())
        n_calls = len(fetch.calls)
        run(yt.analyze(f"https://youtu.be/{VID}", complete, mode="timestamps", fetch=fetch, use_ytdlp=False))
        self.assertEqual(len(fetch.calls), n_calls)      # кэш транскрипта
        self.assertEqual(len(prompts), 1)                # кэш ответа
        run(yt.analyze(f"https://youtu.be/{VID}", complete, mode="question", question="что в конце?", fetch=fetch, use_ytdlp=False))
        self.assertIn("что в конце?", prompts[-1])
        # длинное видео → map/reduce
        prompts.clear()
        segs = [{"start": i * 5.0, "text": "текст " * 40} for i in range(400)]
        run(yt.summarize({"id": VID, "title": "t"}, segs, complete, mode="brief", chunk_chars=5000, max_chunks=4))
        self.assertEqual(len(prompts), 5)                # 4 части + сведение
        self.assertIn("заметки по частям", prompts[-1])

    def test_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()

        async def complete(prompt: str) -> str:
            return "Итог [2:05]"
        yt.register(app, lambda: complete, fetch=fake_fetch(), use_ytdlp=False)
        client = TestClient(app)
        r = client.post("/api/youtube/analyze", json={"url": f"https://youtu.be/{VID}", "mode": "brief"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("&t=125s", r.json()["text"])
        r = client.post("/api/youtube/analyze", json={"url": "https://example.com"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "bad_url")
        r = client.post("/api/youtube/transcript", json={"url": f"https://youtu.be/{VID}"})
        self.assertIn("[2:05] Вывод", r.json()["text"])


class CommandTests(unittest.TestCase):
    def test_catalog_installed_once(self):
        r24.install(); r24.install()
        ids = [a["id"] for a in catalog.ACTIONS]
        for aid in r24.NEW_IDS:
            self.assertEqual(ids.count(aid), 1, aid)
            self.assertIn(aid, catalog.BY_ID)
        self.assertEqual(catalog.BY_ID["dfy.start"]["tier"], "confirm")
        self.assertEqual(len([n for n, _p, _f in intents._RULES if n.startswith("r24.")]), 10)
        self.assertEqual(catalog.BY_ID["computer.use"]["tier"], "confirm")

    def test_phrases(self):
        cases = {
            "пароль на 32 символа": ("password.generate", {"length": 32}),
            "сгенерируй пароль без спецсимволов": ("password.generate", {"symbols": False}),
            "uuid": ("uuid.generate", {}),
            "сколько будет 10 км в милях": ("unit.convert", {"amount": 10.0, "from": "km", "to": "mi"}),
            "100 °F в цельсия": ("unit.convert", {"amount": 100.0, "from": "f", "to": "c"}),
            "переведи на английский: привет мир": ("text.translate", {"to": "английский", "text": "привет мир"}),
            "кратко перескажи буфер": ("text.summarize", {"text": ""}),
            "сделай за меня: забронируй отель в Казани": ("dfy.start", {"goal": "забронируй отель в Казани"}),
            f"https://youtu.be/{VID}": ("youtube.analyze", {"url": f"https://youtu.be/{VID}", "mode": "brief"}),
            f"таймкоды https://youtu.be/{VID}": ("youtube.analyze", {"url": f"https://youtu.be/{VID}", "mode": "timestamps"}),
            "проанализируй видео": ("youtube.analyze", {"url": "", "mode": "brief"}),
            "возьми управление: открой блокнот и напиши привет": ("computer.use", {"task": "открой блокнот и напиши привет"}),
        }
        for text, (action, params) in cases.items():
            m = intents.match(text)
            self.assertIsNotNone(m, text)
            self.assertEqual((m["action"], m["params"]), (action, params), text)
        q = intents.match(f"https://youtu.be/{VID} что автор говорит про деньги")
        self.assertEqual(q["params"]["mode"], "question")
        # старые команды не перехвачены
        self.assertEqual(intents.match(f"открой https://youtu.be/{VID}")["action"], "open.url")
        self.assertEqual(intents.match("громкость 30")["action"], "volume.set")
        self.assertEqual(intents.match("таймер 10 минут")["action"], "timer.start")
        self.assertIsNone(intents.match("10 км в килограммы"))    # разные величины → модели

    def test_convert_and_password(self):
        self.assertAlmostEqual(r24.convert(10, "km", "mi"), 6.2137, places=3)
        self.assertAlmostEqual(r24.convert(100, "f", "c"), 37.7778, places=3)
        self.assertAlmostEqual(r24.convert(0, "c", "k"), 273.15)
        self.assertAlmostEqual(r24.convert(1, "gb", "mb"), 1024)
        self.assertEqual(r24.unit_key("километров"), "km")
        self.assertEqual(r24.unit_key("фунты"), "lb")
        with self.assertRaises(ValueError):
            r24.convert(1, "km", "kg")
        pw = r24.make_password(24, True)
        self.assertEqual(len(pw), 24)
        self.assertTrue(any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(not c.isalnum() for c in pw))
        self.assertTrue(r24.make_password(12, False).isalnum())
        self.assertEqual(len(r24.make_password(3)), 8)

    def test_engine_runs_new_actions(self):
        from app.assistant_commands import Engine

        class Clip:
            text = "https://youtu.be/" + VID

        eng = Engine.__new__(Engine)
        try:
            eng = Engine()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Engine() unavailable: {exc}")
        for aid in r24.NEW_IDS:
            self.assertIn(aid, eng.handlers)
        card = run(eng.run("unit.convert", {"amount": 5, "from": "kg", "to": "lb"}))
        self.assertEqual(card["status"], "done", card)
        self.assertIn("11.0231", card["message"])
        card = run(eng.run("password.generate", {"length": 16}))
        self.assertEqual(card["status"], "done", card)
        self.assertEqual(len(card["copy"]), 16)
        card = run(eng.run("dfy.start", {"goal": "забронируй отель"}))
        self.assertEqual(card["status"], "confirm")          # агенту — только после подтверждения
        card = run(eng.run("dfy.start", {"goal": "забронируй отель"}, confirmed=True))
        self.assertEqual(card["client"], {"type": "dfy", "goal": "забронируй отель", "source": "quick"})
        card = run(eng.run("computer.use", {"task": "открой блокнот"}))
        self.assertEqual(card["status"], "confirm")          # передача мыши — только после подтверждения
        card = run(eng.run("computer.use", {"task": "открой блокнот"}, confirmed=True))
        if os.sys.platform != "win32":
            self.assertEqual(card["status"], "unsupported")


class VoiceStatusTests(unittest.TestCase):
    STT = [{"id": "small", "name": "Whisper small", "downloaded": False, "download_status": "idle", "progress": 0},
           {"id": "base", "name": "Whisper base", "downloaded": True}]
    TTS = [{"id": "ru_RU-irina-medium", "name": "Piper · irina", "downloaded": False}]

    def test_inherits_voice_settings_and_reports_missing_download(self):
        st = r24.voice_status({"voice_stt_provider": "local", "voice_stt_local_model": "small"}, {}, self.STT, self.TTS, [], "linux")
        self.assertEqual(st["stt"]["source"], "voice")
        self.assertFalse(st["stt"]["ready"])
        self.assertEqual((st["stt"]["fix"], st["stt"]["fix_id"], st["stt"]["fix_kind"]), ("download", "small", "stt"))
        self.assertFalse(st["tts"]["ready"])                 # голоса Windows вне Windows
        self.assertIn("Proxy", st["proxy_note"])

    def test_quick_overrides_and_api_needs_voice_url(self):
        st = r24.voice_status({"voice_stt_local_model": "small"}, {"stt_provider": "local", "stt_local_model": "base",
                              "tts_provider": "api", "tts_api_voice": "nova"}, self.STT, self.TTS, [], "win32")
        self.assertEqual((st["stt"]["source"], st["stt"]["ready"]), ("quick", True))
        self.assertFalse(st["tts"]["ready"])
        self.assertEqual(st["tts"]["fix"], "open_voice")
        st = r24.voice_status({"voice_tts_api_url": "https://api.openai.com/v1", "voice_tts_api_model": "gpt-4o-mini-tts",
                               "voice_tts_api_key": "sk-test"},     # Round 25: a cloud service needs its key to be ready
                              {"tts_provider": "api", "tts_api_voice": "nova"}, self.STT, self.TTS, [], "win32")
        self.assertTrue(st["tts"]["ready"])
        self.assertIn("api.openai.com", st["tts"]["label"])
        self.assertEqual(st["tts"]["voice"], "nova")

    def test_downloaded_voice(self):
        st = r24.voice_status({}, {"tts_provider": "downloaded"}, self.STT, self.TTS, [], "win32")
        self.assertFalse(st["tts"]["ready"])
        self.assertEqual(st["tts"]["fix_id"], "ru_RU-irina-medium")
        tts = [dict(self.TTS[0], downloaded=True)]
        st = r24.voice_status({}, {"tts_provider": "downloaded"}, self.STT, tts, [], "win32")
        self.assertTrue(st["tts"]["ready"])


class StaticContracts(unittest.TestCase):
    def test_js_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        for rel in ("memu-layers.js", "app.js", "quick-settings.js", "settings-kit.js", "quick/plugins/commands.js",
                    "quick/plugins/commands-settings.js"):
            r = subprocess.run([node, "--check", str(PG / rel)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, rel + r.stderr)

    def test_layers_loaded_before_settings_kit_and_app(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        i = html.index("/memu-layers.js"); self.assertLess(i, html.index("/settings-kit.js")); self.assertLess(i, html.index("/app.js"))

    def test_rerender_closes_layers(self):
        src = (PG / "quick" / "plugins" / "commands-settings.js").read_text(encoding="utf-8")
        self.assertRegex(src, r"MemuLayers\.closeAll\(\"rerender\"\)[\s\S]{0,200}el\.innerHTML")
        app = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn('$$("body > .pretty-select-menu")', app)
        self.assertIn("menu._layerOwner = wrap", app)


def _chromium() -> str:
    for c in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", shutil.which("chromium") or "",
              "/workspace/work/chromium/chrome"):
        if c and os.path.exists(c):
            return c
    return ""


try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


def _pretty_select_source() -> str:
    app = (PG / "app.js").read_text(encoding="utf-8")
    start = app.index("function closePrettySelects(")
    end = app.index("/* ------------------------------ верхняя панель окна")
    return app[start:end]


@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class LayersBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls._pw.stop()

    def page(self):
        page = self.browser.new_page(viewport={"width": 900, "height": 600})
        layers = (PG / "memu-layers.js").read_text(encoding="utf-8")
        pretty = _pretty_select_source()
        html = """<!doctype html><html><head><style>
          .pretty-native{display:none}.pretty-select-menu{display:none;position:fixed;background:#fff;border:1px solid #888}
          .pretty-select-menu.visible{display:block}.pretty-select-menu button{display:block;width:100%}
          #scroller{height:200px;overflow:auto}</style></head><body>
          <div id="scroller"><div style="height:900px"><div id="host"></div></div></div><input id="other">
          <script>
          const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
          const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
          const PG_LANG = "ru"; function closeFloatingMenus() {}
          // Round 25: enhanceSelects hands long lists to the searchable picker (not part of this page).
          function attachSelectSearch() { return false; }
          </script><script>""" + layers + "</script><script>" + pretty + """</script><script>
          window.changes = 0;
          function render(value) {
            const host = document.getElementById("host");
            host.innerHTML = `<label>Озвучка <select id="tts">${["", "windows", "downloaded", "api", "local"].map((v) =>
              `<option value="${v}" ${v === value ? "selected" : ""}>${v || "как в Голос"}</option>`).join("")}</select></label>`;
            enhanceSelects(host);
          }
          document.addEventListener("change", (e) => { if (e.target.id === "tts") { window.changes++; MemuLayers.closeAll("rerender"); render(e.target.value); } });
          render("");
          </script></body></html>"""
        page.set_content(html)
        return page

    def visible_menus(self, page):
        return page.evaluate("Array.from(document.querySelectorAll('.pretty-select-menu')).filter(m => m.getClientRects().length).length")

    def test_option_click_with_rerender_leaves_no_hanging_menu(self):
        page = self.page()
        page.click(".pretty-select-button")
        self.assertEqual(self.visible_menus(page), 1)
        page.click("[data-pretty-option='3']")
        self.assertEqual(page.evaluate("window.changes"), 1)
        self.assertEqual(self.visible_menus(page), 0)
        self.assertEqual(page.evaluate("document.querySelectorAll('body > .pretty-select-menu').length"), 0)
        self.assertEqual(page.evaluate("document.querySelector('#tts').value"), "api")
        # повтор много раз — по-прежнему одно меню максимум
        for i in range(5):
            page.click(".pretty-select-button"); page.click(f"[data-pretty-option='{i % 5}']")
        self.assertEqual(self.visible_menus(page), 0)

    def test_escape_outside_scroll_blur_and_keyboard(self):
        page = self.page()
        page.click(".pretty-select-button"); page.keyboard.press("Escape")
        self.assertEqual(self.visible_menus(page), 0)
        self.assertEqual(page.evaluate("document.activeElement.className"), "pretty-select-button")
        page.click(".pretty-select-button"); page.mouse.click(850, 550)
        self.assertEqual(self.visible_menus(page), 0)
        page.click(".pretty-select-button"); page.evaluate("document.getElementById('scroller').scrollTop = 50; document.getElementById('scroller').dispatchEvent(new Event('scroll'))")
        self.assertEqual(self.visible_menus(page), 0)
        page.click(".pretty-select-button"); page.evaluate("window.dispatchEvent(new Event('blur'))")
        self.assertEqual(self.visible_menus(page), 0)
        # клавиатура: Enter открывает, стрелки двигают, Enter выбирает
        page.focus(".pretty-select-button"); page.keyboard.press("Enter")
        self.assertEqual(self.visible_menus(page), 1)
        page.keyboard.press("ArrowDown"); page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
        self.assertEqual(page.evaluate("document.querySelector('#tts').value"), "downloaded")
        self.assertEqual(self.visible_menus(page), 0)

    def test_menu_stays_inside_viewport(self):
        page = self.page()
        page.set_viewport_size({"width": 900, "height": 140})
        page.click(".pretty-select-button")
        box = page.evaluate("(() => { const r = document.querySelector('.pretty-select-menu.visible').getBoundingClientRect(); return [r.top, r.bottom]; })()")
        self.assertGreaterEqual(box[0], 0)
        self.assertLessEqual(box[1], 140)

    def test_detached_owner_menu_is_swept(self):
        page = self.page()
        page.click(".pretty-select-button")
        page.evaluate("document.getElementById('host').innerHTML = ''")   # чужая перерисовка без closeAll
        page.wait_for_timeout(120)
        self.assertEqual(page.evaluate("document.querySelectorAll('body > .pretty-select-menu').length"), 0)
        self.assertFalse(page.evaluate("MemuLayers.isOpen()"))


if __name__ == "__main__":
    unittest.main()
