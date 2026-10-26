"""Round 29 wave D (r29d-quick-modules): regressions for Quick Ask / Quick window / module AI pack fixes."""
from __future__ import annotations

import unittest

from app.assistant_intents import match

def _action(text):
    hit = match(text)
    return hit and hit["action"]

class ImageIntentExcludesTextArtifacts(unittest.TestCase):
    """«Нарисуй mermaid-диаграмму…» in Quick Ask generated a PICTURE: the local image.generate rule fired on any
    «нарисуй/draw …». Tables, diagrams, charts and schemes are text answers (rendered by the chat), as in media_routing."""

    TEXT = [
        "Нарисуй mermaid-диаграмму процесса заказа",
        "нарисуй mermaid диаграмму",
        "Нарисуй диаграмму последовательности входа пользователя",
        "нарисуй схему сети офиса",
        "нарисуй блок-схему алгоритма сортировки",
        "нарисуй блоксхему",
        "нарисуй таблицу умножения",
        "нарисуй график продаж по месяцам",
        "нарисуй гистограмму оценок",
        "нарисуй майнд-карту по теме экологии",
        "нарисуй интеллект-карту",
        "нарисуй mermaid gantt проекта",
        "сделай диаграмму Ганта для ремонта",
        "сгенерируй схему базы данных",
        "Нарисуй ASCII-арт кота",
        "нарисуй svg иконку",
        "Draw a mermaid diagram of the login flow",
        "draw a flowchart for checkout",
        "draw a sequence diagram",
        "draw an ER diagram for a shop",
        "draw a table of planets",
        "draw a chart of sales by month",
        "draw a graph of y = x^2",
        "draw a mind map about python",
        "draw a gantt chart",
        "make a diagram of the network",
        "generate a mermaid flowchart",
        "draw a timeline of WW2",
    ]
    IMAGE = [
        ("нарисуй кота в космосе", "кота в космосе"),
        ("draw a cat in space", "a cat in space"),
        ("сгенерируй картинку закат над морем", None),
        ("generate an image of a red fox", "a red fox"),
        ("нарисуй картинку с диаграммой на доске", None),
        ("draw a picture of a whiteboard with a chart", None),
        ("нарисуй дом у озера", None),
        ("paint a sunset", None),
    ]

    def test_text_artifacts_go_to_model(self):
        for phrase in self.TEXT:
            with self.subTest(phrase=phrase):
                self.assertNotEqual(_action(phrase), "image.generate")

    def test_pictures_still_generate(self):
        for phrase, prompt in self.IMAGE:
            with self.subTest(phrase=phrase):
                hit = match(phrase)
                self.assertTrue(hit, phrase)
                self.assertEqual(hit["action"], "image.generate")
                if prompt:
                    self.assertEqual(hit["params"]["prompt"], prompt)

class OcrErrorMessages(unittest.TestCase):
    """r29c fix: any Windows OCR failure used to say «add an OCR language in Settings → Time & language», even for a
    PowerShell timeout or an unreadable image. Only ``no_engine`` means a missing language now."""

    PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def _run(self, result, lang):
        from unittest import mock
        from app import quick_apps, quick_apps_win, quick_extras
        with mock.patch.object(quick_apps, "IS_WINDOWS", True), \
                mock.patch.object(quick_apps_win, "windows_ocr", return_value=result) as fake:
            out = quick_extras.ocr_image(self.PNG, lang)
        fake.assert_called_once()
        return out

    def test_failure_is_not_missing_language(self):
        for err in ("ocr_failed", "timeout", "empty", "unsupported"):
            for lang in ("ru", "en"):
                with self.subTest(err=err, lang=lang):
                    out = self._run({"error": err, "detail": "PowerShell exited 1"}, lang)
                    self.assertEqual(out["code"], "ocr_failed")
                    self.assertNotIn("Параметры", out["error"])
                    self.assertNotIn("Settings", out["error"])
                    if lang == "en":
                        self.assertNotRegex(out["error"], "[А-Яа-яЁё]")

    def test_no_engine_points_to_language_settings(self):
        out = self._run({"error": "no_engine"}, "ru")
        self.assertEqual(out["code"], "ocr")
        self.assertIn("Время и язык", out["error"])
        self.assertIn("Time & language", self._run({"error": "no_engine"}, "en")["error"])

    def test_success_and_bad_input(self):
        out = self._run({"text": "  Итого 12 450 ₽ \n", "lang": "ru", "lines": 1}, "ru")
        self.assertEqual(out, {"text": "Итого 12 450 ₽", "lang": "ru", "lines": 1})
        from app import quick_extras
        self.assertEqual(quick_extras.ocr_image("data:text/plain;base64,AAAA", "en")["code"], "bad_image")

class EmptyFollowUpAfterToolCard(unittest.TestCase):
    """Full access, real deepseek-flash: «Допиши в todo.txt …» was applied (card «Готово: 1 изменение»), then the
    model's follow-up stream ended without text → «Не получилось … Повторить» above a change that WAS made."""

    CALL = '```memuai-tool\n[{"tool":"r29d_change","args":{"ops":[]}}]\n```'
    EMPTY = "custom: провайдер завершил поток без текста. Чаще всего это отказ модели по фильтрам безопасности."

    def _run(self, *, ui, second, lang="ru"):
        import asyncio, json
        from app import quick_ask_api, quick_ask_core as core

        async def handler(args, ctx):
            return {"applied": 1, **({"ui": {"type": "changeset", "set": {"id": "s1"}}} if ui else {})}
        saved = quick_ask_api.EXTRA_TOOLS.get("r29d_change")
        quick_ask_api.EXTRA_TOOLS["r29d_change"] = {"description": "test", "handler": handler, "label": None, "enabled": None}
        replies = [("text", self.CALL), second]

        async def stream_lines(path, payload):
            kind, value = replies.pop(0)
            item = {"choices": [{"delta": {"content": value}}]} if kind == "text" else {"error": {"message": value}}
            yield "data: " + json.dumps(item, ensure_ascii=False)
            yield "data: [DONE]"

        async def collect():
            return [json.loads(chunk[6:]) async for chunk in quick_ask_api.ask_events(
                text="допиши строку", images=[], selection="", model="flash", history=[], vision=False,
                native_search=False, settings=dict(core.PREF_DEFAULTS, quick_ask_web_search=False), lang=lang,
                stream_lines=stream_lines, run_tool=None, rich_search=None, rich_payload=None, capture=None)]
        try:
            return asyncio.run(collect())
        finally:
            if saved is None:
                quick_ask_api.EXTRA_TOOLS.pop("r29d_change", None)
            else:
                quick_ask_api.EXTRA_TOOLS["r29d_change"] = saved

    def test_card_shown_then_empty_stream_is_not_an_error(self):
        for lang, text in (("ru", "Результат — в карточке ниже."), ("en", "The result is in the card below.")):
            with self.subTest(lang=lang):
                events = self._run(ui=True, second=("error", self.EMPTY), lang=lang)
                self.assertFalse([e for e in events if e["event"] == "error"])
                self.assertTrue([e for e in events if e["event"] == "tool_ui"])
                self.assertEqual((events[-1]["event"], events[-1]["text"]), ("done", text))

    def test_other_failures_and_no_card_still_report(self):
        events = self._run(ui=False, second=("error", self.EMPTY))
        self.assertEqual(events[-1]["event"], "error")
        events = self._run(ui=True, second=("error", "HTTP 429: rate limit"))
        self.assertEqual(events[-1]["event"], "error")

class ExplorerNoteQuotes(unittest.TestCase):
    """EN answers said «waits for your «Apply»» — the tool note quoted the button in Russian guillemets."""

    def test_button_names_follow_the_language(self):
        from unittest import mock
        from app import quick_apps, quick_explorer
        change = {"id": "s1", "ops": [{"n": 1, "status": "pending", "path": "a.zip"}]}
        for lang, apply in (("en", "“Apply”"), ("ru", "«Применить»")):
            with mock.patch.object(quick_explorer, "plan_changes", return_value=change), \
                    mock.patch.object(quick_explorer, "public_set", return_value={}), \
                    mock.patch.object(quick_apps, "settings", return_value={"access": "ask"}):
                note = quick_explorer._change_tool({}, {"ops": []}, lang)["note"]
            self.assertIn(apply, note)
            if lang == "en":
                self.assertNotIn("«", note)

class QuickWindowFrontendFixes(unittest.TestCase):
    """Static guards for the Quick window fixes found with screenshots (r29d)."""

    def setUp(self):
        import json, pathlib
        self.root = pathlib.Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "quick" / "plugins"
        self.read = lambda name: (self.root / name).read_text(encoding="utf-8")
        self.index = {row["id"]: row for row in json.loads(self.read("index.json"))["plugins"]}

    def test_change_cards_never_push_the_window_out(self):
        css, js = self.read("explorer.css"), self.read("explorer.js")
        self.assertRegex(css, r"\.qa-plugin\.qe-cards \{ max-height: min\(calc\(var\(--qa-max-h\)[^}]*overflow-y: auto")
        self.assertIn('const compact = !pending && set.id !== last && !set.expanded;', js)
        self.assertIn("qe-undo-sm", js)
        self.assertNotEqual(self.index["explorer"]["v"], "1.0-r29")

    def test_extras_alignment_ocr_empty_and_kbd(self):
        css, js = self.read("extras.css"), self.read("extras.js")
        self.assertIn('.qa-plugin.qxx[data-mode="inline"] { padding: 0 14px 4px var(--qa-pad', css)
        self.assertIn(".qxx-foot > .qxx-btns", css)
        self.assertIn(".qxx-foot .qa-btn.accent kbd", css)
        self.assertIn('data-x="ocr-model"', js)
        self.assertIn('data-x="ocr-retry"', js)

        empty = js[js.index("!o.text ? `"):js.index('<textarea class="qxx-text" id="qxx-ocr"')]
        self.assertNotIn("ocr-copy", empty)
        self.assertNotEqual(self.index["extras"]["v"], "1.0-r29")

class PanelWallText(unittest.TestCase):
    """Screen layer at DPR 1.5, S tile: «Источник больше не отвечает или не нашёл данные. У MemuAI Proxy ещё нет
    статистики…» (generic + specific) shrank the card to zoom 0.68; GitHub/Calendar said only «Нужно подключение к
    сервису.» although the source said where to connect; the EN UI showed Russian messages saved at refresh time."""

    def _wall(self, lang, wall):
        import json, pathlib, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node is required")
        js = (pathlib.Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8")
        body = js[js.index("function wallText("):js.index("/* ------------------------------------------------------------ blocks */")]
        script = (f"const en = () => {json.dumps(lang == 'en')}; const t = (ru, e) => (en() ? e : ru);\n{body}\n"
                  f"console.log(JSON.stringify(wallText({json.dumps(wall, ensure_ascii=False)})));")
        return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)

    def test_specific_message_replaces_the_generic_one(self):
        proxy = {"status": "source_gone:proxy-metrics", "message": "У MemuAI Proxy ещё нет статистики (или она выключена в его настройках)."}
        self.assertEqual(self._wall("ru", proxy), proxy["message"])
        gh = {"status": "needs_connector:github", "message": "Подключите GitHub в «Настройки → Интеграции»."}
        self.assertEqual(self._wall("ru", gh), gh["message"])
        self.assertEqual(self._wall("en", {"status": "needs_connector:github", "message": "Connect GitHub in Settings → Integrations."}),
                         "Connect GitHub in Settings → Integrations.")

    def test_english_ui_never_shows_a_russian_message(self):
        for wall in ({"status": "source_gone:moex", "message": "Мосбиржа не знает тикер XXXX"},
                     {"status": "needs_connector:github", "message": "Подключите GitHub"},
                     {"status": "blocked:weird", "message": "Что-то пошло не так"}):
            with self.subTest(wall=wall["status"]):
                self.assertNotRegex(self._wall("en", wall), "[А-Яа-яЁё]")
        self.assertIn("rate-limiting", self._wall("en", {"status": "blocked:rate-limit", "message": "HTTP 429"}))

class LayerTimersGoLive(unittest.TestCase):
    """Screen layer: the Pomodoro/timers card stayed «Загружаю таймеры…» for good (seen in layer_matrix with 5 s waits):
    panels.js checked for visible timer cards when its data arrived — before the layer had put the cards in the DOM —
    and its check used offsetParent, which is null inside the layer's position:fixed root."""

    def test_wiring(self):
        import pathlib, re
        static = pathlib.Path(__file__).resolve().parents[1] / "app" / "pgstatic"
        panels = (static / "panels.js").read_text(encoding="utf-8")
        layer = (static / "quick" / "layer.js").read_text(encoding="utf-8")
        visible = panels[panels.index("const visibleCards = "):panels.index("const visibleChips = ")]
        self.assertIn("getClientRects().length > 0", visible)
        self.assertNotIn("offsetParent", visible)
        api = panels[panels.index("cardHtml, chipHtml, "):]
        self.assertRegex(api, r"\bsyncLive,")
        self.assertIn("MemuPanels.syncLive()", layer)
        for fn in ("function render() {", "function renderPanels() {"):
            body = layer[layer.index(fn):]
            body = body[:body.index("\n  }\n")]
            self.assertIn("panelsLive();", body, fn)
        self.assertRegex((static / "quick" / "layer.html").read_text(encoding="utf-8"), r'/quick/layer\.js\?v=1\.[3-9]-r(29|3\d)')

if __name__ == "__main__":
    unittest.main()
