"""Round 26 (panels, settings, appearance, voice, docs).

* RSS mini-chip label is cut at a word with «…», not mid-word («Все публикации под»).
* Cloud TTS: a Base URL pasted as the full endpoint (…/audio/speech) still works; an ElevenLabs
  spent quota (HTTP 401 «quota_exceeded») is not reported as a wrong key; size-limit and empty
  transcript errors follow the UI language.
* Documentation and in-app hints only point at settings pages that exist («Настройки → X»).
* Settings search finds the lazily drawn Panels/Modules pages and names the design/accent pickers
  by what they are (headless Chromium on the real UI fixture).
* A panel metric whose delta is bound but whose trend is a fixed word follows the delta's sign;
  stat tiles are never narrower than their value; the panels list wraps in a narrow window.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import panels, pgdata
from app import tts_providers as tp
from app.voice_runtime import VoiceRuntime

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
DOCS = ROOT / "app" / "docs"

def run(coro):
    return asyncio.run(coro)

class RssShortLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def test_clip_words(self):
        self.assertEqual(panels._clip_words("Все публикации подряд на Хабре", 18), "Все публикации…")
        self.assertEqual(panels._clip_words("Хабр", 18), "Хабр")
        self.assertEqual(panels._clip_words("Суперкалифрагилистикэкспиалидоций", 18), "Суперкалифрагилис…")
        self.assertLessEqual(len(panels._clip_words("a " * 40, 18)), 18)

    def test_rss_feed_short_is_cut_at_a_word(self):
        rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Все публикации подряд на Хабре</title>
        <item><title>Первая</title><link>https://example.org/1</link></item></channel></rss>"""

        async def fake(url, *, limit=0):
            return rss

        panel = {"id": "p_x", "kind": "rss", "title": "RSS", "source": {"type": "rss", "params": {"url": "https://habr.com/rss"}},
                 "options": {}, "feed": {}}
        fetch = getattr(panels, "fetch_rss", None) or getattr(panels, "_fetch_rss")
        with patch.object(panels, "_http_text", fake):
            values, _sources = run(fetch(panel, panels.default_settings()))
        self.assertEqual(values["feed_short"], "Все публикации…")
        self.assertEqual(values["feed_title"], "Все публикации подряд на Хабре")

def synth(handler, text="Привет, мир.", **kw):
    tp.CACHE.items.clear()
    params = {"base_url": "https://api.openai.com/v1", "api_key": "sk-test", "model": "", "voice": ""}
    params.update(kw)
    return run(tp.synthesize(text, transport=httpx.MockTransport(handler), **params))

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" + b"\x00" * 64

class CloudTtsTest(unittest.TestCase):
    def test_full_endpoint_pasted_as_base_url(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append(str(request.url))
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

        audio, mime = synth(handler, base_url="https://api.openai.com/v1/audio/speech/")
        self.assertEqual(mime, "audio/mpeg")
        self.assertEqual(seen, ["https://api.openai.com/v1/audio/speech"])
        synth(handler, base_url="http://127.0.0.1:8880/v1/Audio/Speech")
        self.assertEqual(seen[-1], "http://127.0.0.1:8880/v1/audio/speech")

    def test_transcription_full_endpoint_pasted(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"text": "привет"})

        runtime = VoiceRuntime.__new__(VoiceRuntime)
        text = run(runtime.transcribe_api(b"RIFF", "audio/wav", base_url="https://api.groq.com/openai/v1/audio/transcriptions",
                                          api_key="k", model="whisper-large-v3-turbo", transport=httpx.MockTransport(handler)))
        self.assertEqual(text, "привет")
        self.assertEqual(seen, ["https://api.groq.com/openai/v1/audio/transcriptions"])

    def test_empty_transcript_message_follows_language(self):
        runtime = VoiceRuntime.__new__(VoiceRuntime)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"text": "  "}))
        with self.assertRaisesRegex(RuntimeError, "empty transcript"):
            run(runtime.transcribe_api(b"RIFF", "audio/wav", base_url="https://api.openai.com/v1", api_key="k",
                                       model="", lang="en", transport=transport))

    def test_elevenlabs_quota_401_is_not_a_wrong_key(self):
        body = {"detail": {"status": "quota_exceeded",
                           "message": "This request exceeds your quota of 10000. You have 12 credits remaining, while 40 credits are required for this request."}}

        def handler(request):
            return httpx.Response(401, json=body)

        with self.assertRaises(tp.TTSApiError) as ctx:
            synth(handler, base_url="https://api.elevenlabs.io", api_key="sk_abc123456", model="eleven_multilingual_v2",
                  voice="JBFqnCBsd6RMkjVDRZzb")
        text = str(ctx.exception)
        self.assertIn("квота", text)
        self.assertNotIn("неверный", text)

        msg = tp.error_message("elevenlabs", 401, json.dumps({"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}).encode())
        self.assertIn("неверный", msg)

    def test_too_large_answer_follows_language(self):
        with patch.object(tp, "MAX_AUDIO_BYTES", 10):
            with self.assertRaisesRegex(tp.TTSApiError, "too large"):
                synth(lambda request: httpx.Response(200, content=MP3), lang="en")

SETTINGS_PAGES_RU = {"Общие", "Внешний вид", "Уведомления", "Модели", "Чат и ответы", "Веб-поиск", "Изображения и видео",
                     "Голос и Live", "Work и агенты", "Do For You", "Быстрый доступ", "Панели", "Telegram", "Интеграции",
                     "Синхронизация", "Горячие клавиши",
                     "Профиль и память", "Рефлексия", "Время и фокус",
                     "Системные промпты", "Данные", "Модули", "Документация", "О программе",
                     "MemuNote",
                     "MemuFiles", "Ссылки и командная строка"}
SETTINGS_PAGES_EN = {"General", "Appearance", "Notifications", "Models", "Chat & replies", "Web search", "Images & video",
                     "Voice & Live", "Work & agents", "Do For You", "Quick Ask", "Panels", "Telegram", "Integrations",
                     "Profile & memory", "Reflect", "Time & focus", "System prompts",
                     "Sync", "Keyboard shortcuts", "Data", "Modules", "Documentation", "About",
                     "MemuNote", "MemuFiles", "Links & command line"}

NOT_MEMUAI = ("Proxy →", "Mini App", "Windows Settings", "Windows ", "Параметры Windows", "(Settings → Data controls", "(Settings → Privacy",
              "Telegram →", "MemuMusic Settings →",
              "Settings → Privacy → Microphone",
              "Notion **")

def _proxy_sections():
    """Section names of the Proxy settings (static/settings-i18n.js «sg.sec.*», ru and en) plus «Дополнительно»."""
    text = (ROOT / "app" / "static" / "settings-i18n.js").read_text(encoding="utf-8")
    return set(re.findall(r'"sg\.sec\.[a-z]+": "([^"]+)"', text)) | {"Дополнительно", "Advanced"}

def _memunote_sections():
    """Section names of MemuNote's own settings window (memunote/static/js/i18n.js «set_general/editor/files/ai»)."""
    text = (ROOT / "app" / "memunote" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    return set(re.findall(r'set_(?:general|editor|files|ai): "([^"]+)"', text))

class SettingsPathsTest(unittest.TestCase):
    def test_settings_pages_list_matches_app(self):
        source = (PG / "app.js").read_text(encoding="utf-8")
        start = source.index("function settingsPages()")
        block = source[start:source.index("\nfunction ", start + 1)]
        pages = re.findall(r'P\("[a-z]+", "[a-z]+", [^,]+, "([^"]+)", "([^"]+)"', block)
        self.assertEqual({ru for ru, _ in pages}, SETTINGS_PAGES_RU)
        self.assertEqual({en for _, en in pages}, SETTINGS_PAGES_EN)

    def _bad_paths(self, text, proxy_doc=False, memunote_doc=False):
        bad = []
        for match in re.finditer(r"(Настройки|Settings) → ([^→.,;:)(»\"<*|\n]{2,40})", text):
            line = text[max(0, text.rfind("\n", 0, match.start())):match.end()]
            if any(marker in line[-len(match.group(0)) - 30:] for marker in NOT_MEMUAI):
                continue
            head = match.group(2).strip()
            pages = SETTINGS_PAGES_RU if match.group(1) == "Настройки" else SETTINGS_PAGES_EN
            if proxy_doc or "Proxy" in line:
                pages = pages | _proxy_sections()
            if memunote_doc:
                pages = pages | _memunote_sections()

            if head in pages or head in ("Искусственный интеллект", "AI") or any(head.startswith(p + " ") for p in pages):
                continue
            bad.append(match.group(0))
        return bad

    def test_docs_point_at_existing_pages(self):
        bad = {}
        for path in sorted(DOCS.rglob("*.md")):
            found = self._bad_paths(path.read_text(encoding="utf-8"), proxy_doc=path.name.startswith("proxy"),
                                    memunote_doc=path.name == "memunote.md")
            if found:
                bad[str(path.relative_to(DOCS))] = found
        self.assertEqual(bad, {})

    def test_panels_hints_point_at_existing_pages(self):
        for name in ("panels.js", "panels-telegram.js", "modules.js", "voice-settings.js", "appearance.js", "docs.js"):
            text = (PG / name).read_text(encoding="utf-8")

            body = re.sub(r"^/\*.*?\*/", "", text, count=1, flags=re.S)
            self.assertEqual(self._bad_paths(body), [], name)

def _browser_base():
    return __import__("tests.panels_browser_test", fromlist=["_chromium"])

@unittest.skipUnless(_browser_base()._chromium(), "Chromium is not installed")
class PanelsRenderBrowserTest(unittest.TestCase):
    """Rendering on the real UI fixture (index.html + panels.js/css)."""

    def _page(self, pw, snap, width=1300):
        from tests import panels_browser_test as base
        browser = pw.chromium.launch(executable_path=base._chromium(), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def route(r):
            path = r.request.url[len(base.ORIGIN):].split("?")[0]
            if path in ("/", "/index.html"):
                return r.fulfill(status=200, content_type="text/html", body=base.FIXTURE)
            if path == "/api/pg/panels":
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(snap))
            if path.startswith("/api/pg/panels"):
                return r.fulfill(status=200, content_type="application/json", body="{}")
            file = base.STATIC / path.lstrip("/")
            if file.is_file():
                kind = "text/css" if file.suffix == ".css" else "application/javascript"
                return r.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return r.fulfill(status=404, body="")

        page.route(f"{base.ORIGIN}/**", route)
        page.goto(base.ORIGIN + "/")
        return browser, page, errors

    def test_bound_delta_decides_trend_and_stats_fit(self):
        from playwright.sync_api import sync_playwright
        from tests import panels_browser_test as base
        blocks = [{"type": "metric", "value": "{{price}}", "unit": "$", "label": "Bitcoin", "delta": "{{d}}", "trend": "down"},
                  {"type": "stats", "items": [{"label": "1h", "value": "{{a}}"}, {"label": "7d", "value": "{{b}}"},
                                              {"label": "7d Low", "value": "{{lo}}", "unit": "$"}, {"label": "7d High", "value": "{{hi}}", "unit": "$"}]}]
        data = {"price": "82,467", "d": "+2.1%", "a": "-0.2%", "b": "-3.9%", "lo": "81,630", "hi": "86,620"}
        snap = {"panels": [base.panel("p_btc0000001", "custom", "BTC", blocks, data, ["home"], size="m")],
                "dismissed": [], "templates": [], "settings": base.SNAPSHOT["settings"]}
        with sync_playwright() as pw:
            browser, page, errors = self._page(pw, snap)
            page.wait_for_selector('#panels-home [data-panel-id="p_btc0000001"] .mp-stat', timeout=5000)
            state = page.evaluate("""() => {
              const card = document.querySelector('#panels-home [data-panel-id="p_btc0000001"]');
              const stats = [...card.querySelectorAll('.mp-stat b')].map(b => b.scrollWidth - b.clientWidth);
              return {trend: card.dataset.trend, delta: card.querySelector('.mp-delta').dataset.trend, clipped: stats.filter(x => x > 1).length};
            }""")
            self.assertEqual(state["trend"], "up")
            self.assertEqual(state["delta"], "up")
            self.assertEqual(state["clipped"], 0, "a stat value is cut with an ellipsis")

            chip = page.evaluate("""() => { const p = MemuPanels._state.panels[0];
              const html = MemuPanels._chipHtml({...p, data: {...p.data, d: '−1.5%'}});
              return /data-trend="down"/.test(html); }""")
            self.assertTrue(chip)
            browser.close()
        self.assertEqual(errors, [])

@unittest.skipUnless(_browser_base()._chromium(), "Chromium is not installed")
class SettingsSearchBrowserTest(unittest.TestCase):
    """The settings search on the REAL UI (appearance_fixture) in a narrow window."""

    def test_search_finds_lazy_pages_and_named_pickers(self):
        import time
        from playwright.sync_api import sync_playwright
        from tests import panels_browser_test as base
        from tests.appearance_fixture import build_fixture
        snap = {"panels": [base.panel("p_w000000001", "weather", "Погода", [], {}, ["home", "appbar"], status="setup",
                                      wall={"status": "needs_location:weather", "message": "Где вы? Укажите город или разрешите определить местоположение."})],
                "dismissed": [], "templates": [], "settings": base.SNAPSHOT["settings"]}
        modules = {"modules": [{"id": "translator", "title": "Переводчик", "title_en": "Translator", "summary": "Две колонки",
                                "builtin": True, "enabled": False, "permissions": [], "slot": "sidebar"}], "removed": []}
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_fixture(Path(tmp))
            html = fixture.read_text(encoding="utf-8").replace(
                "else if (path === '/api/pg/themes')",
                f"else if (path === '/api/pg/panels') value = {json.dumps(snap)};\n  "
                f"else if (path === '/api/pg/modules') value = {json.dumps(modules)};\n  else if (path === '/api/pg/themes')", 1)
            fixture.write_text(html, encoding="utf-8")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=base._chromium(), args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 640, "height": 900})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc) + " " + str(exc.stack or "")[:400]))
                page.goto(fixture.as_uri(), wait_until="domcontentloaded")
                page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
                page.evaluate("() => MemuModules.refresh()")
                page.evaluate("() => showSettings(true)")
                page.wait_for_selector("#panel .sx-shell", timeout=10000)
                time.sleep(0.5)
                hits = page.evaluate("""() => Object.fromEntries(['единицы', 'местоположение', 'модель для панелей', 'создать модуль',
                    'переводчик', 'брутализм', 'терракота', 'без анимации']
                  .map(q => { const h = MemuSettingsKit.search(q)[0]; return [q, h ? h.page + ': ' + h.label : '']; }))""")
                self.assertEqual(hits["единицы"], "panels: Единицы")
                self.assertTrue(hits["местоположение"].startswith("panels: Местоположение"), hits)
                self.assertTrue(hits["модель для панелей"].startswith("panels: Модель для панелей"), hits)
                self.assertEqual(hits["создать модуль"], "modules: Создать модуль")
                self.assertEqual(hits["переводчик"], "modules: Переводчик")
                self.assertEqual(hits["брутализм"], "appearance: Дизайн интерфейса")
                self.assertEqual(hits["терракота"], "appearance: Акцентный цвет")
                self.assertEqual(hits["без анимации"], "appearance: Анимации")

                page.evaluate("() => MemuSettingsKit.jump('единицы')")
                page.wait_for_selector("#pane-panels .sx-flash [data-units]", timeout=5000)

                page.wait_for_selector('#pane-panels .mp-set-item[data-panel-id="p_w000000001"]', timeout=5000)
                geo = page.evaluate("""() => { const item = document.querySelector('#pane-panels .mp-set-item');
                  return {name: item.querySelector('.t').getBoundingClientRect().width, item: item.getBoundingClientRect().width}; }""")
                self.assertGreater(geo["name"], geo["item"] * 0.7, geo)
                browser.close()
        self.assertEqual(errors, [])

class AppearanceSearchLabelsTest(unittest.TestCase):
    def test_pickers_carry_their_own_search_label(self):
        source = (PG / "appearance.js").read_text(encoding="utf-8")
        for cls in ("theme-options", "accent-options", "design-options"):
            self.assertRegex(source, rf'class="{cls}" data-sx-label="\$\{{L\(')
        kit = (PG / "settings-kit.js").read_text(encoding="utf-8")
        self.assertIn("node.dataset.sxLabel", kit)

if __name__ == "__main__":
    unittest.main()
