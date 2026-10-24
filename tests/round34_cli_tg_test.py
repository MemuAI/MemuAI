"""Round 34 — MemuCode CLI, Telegram, Mini App: the fixes of this round (no network)."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r34-cli-tg-test-"))

TG = ROOT / "app" / "tgapp"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _plain(ansi: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", ansi)


def _renderer():
    from app.cli.tui.render import Renderer
    from app.cli.tui.theme import palette_for
    return Renderer(palette_for("dark"), "truecolor")


class TuiLayout(unittest.TestCase):
    """1–6: banner, spinner line, dialog width, /help, notices, a stopped step."""

    def test_banner_recent_rows_skip_empty_and_align(self):
        from app.cli.tui import banner as mod
        rows = [{"id": "cur", "title": "x", "updated": time.time()},
                {"id": "a", "title": "", "first": "", "updated": time.time()},          # opened and closed
                {"id": "b", "title": "Очень длинный заголовок разговора " * 4, "updated": time.time() - 90000},
                {"id": "c", "title": "Short", "updated": time.time() - 30}]
        runtime = SimpleNamespace(root=Path("."), session=SimpleNamespace(id="cur"), memuai_prefs=lambda: {},
                                  settings={"onboarded": True})
        src = _read(ROOT / "app" / "cli" / "tui" / "banner.py")
        self.assertIn('no_wrap=True, overflow="ellipsis"', src)
        with patch.object(mod, "list_sessions", return_value=rows), patch.object(mod, "user_name", return_value=""):
            banner = mod.Banner.__new__(mod.Banner)
            try:
                mod.Banner.__init__(banner, _renderer(), runtime)
            except TypeError:
                self.skipTest("Banner signature changed")
        titles = [title for _when, title in banner.recent]
        self.assertEqual(len(titles), 2)                                  # the empty one is gone
        self.assertTrue(all(title.strip() for title in titles))
        self.assertEqual(len({len(when) for when, _t in banner.recent}), 1)   # one column for the times

    def test_spinner_meta_never_wraps(self):
        from prompt_toolkit.utils import get_cwidth
        from app.cli.tui.app import _ellipsize, fit_meta
        meta = ["12s", "↓ 1.2k tokens", "thinking", "esc to interrupt"]
        for cells in (80, 40, 30, 20, 12, 6):
            line = fit_meta(meta, cells)
            self.assertLessEqual(get_cwidth(line), cells, (cells, line))
        self.assertIn("esc to interrupt", fit_meta(meta, 34))
        self.assertIn("12s", fit_meta(meta, 20))
        self.assertTrue(_ellipsize("Tip: " + "word " * 40, 30).endswith("…"))

    def test_renderer_follows_the_live_width(self):
        r = _renderer()
        r.width_of = lambda: 61
        self.assertEqual(r._live_width(), 61)

    def test_help_overview_fits_narrow_windows(self):
        from app.cli.commands.info_cmds import help_overview
        r = _renderer()
        for width in (70, 80, 120):
            text = _plain(r.render(help_overview(width), width=width))
            self.assertTrue(all(len(line.rstrip()) <= width for line in text.splitlines()), width)

    def test_notice_hangs_its_continuation(self):
        r = _renderer()
        text = _plain(r.notice("Это очень длинное уведомление " * 6, "info", 50))
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line.rstrip()) <= 50 for line in lines))
        self.assertTrue(lines[1].startswith(" "))                         # not back in column 0

    def test_single_stopped_step_is_not_repeated(self):
        from app.cli.tools.base import ToolCall, ToolResult
        from app.cli.tui.activity import ActivityView, Step
        call = ToolCall(name="Bash", args={"command": "for i in 1 2 3; do sleep 1; done"})
        call.status = "cancelled"
        steps = [Step(call, ToolResult(ok=False, summary="stopped", error="stopped"))]
        rows = ActivityView(_renderer()).problem_lines(steps, 100)
        self.assertEqual(rows, [])
        call2 = ToolCall(name="Bash", args={"command": "pytest"})
        call2.status = "done"
        failed = [Step(call2, ToolResult(ok=False, summary="exit 1", error="AssertionError: 2 != 3"))]
        self.assertEqual(len(ActivityView(_renderer()).problem_lines(failed, 100)), 1)


class SchedulePhrases(unittest.TestCase):
    """7 + this round: /schedule phrases; DST-safe next runs."""
    NOW = time.mktime((2026, 10, 11, 22, 0, 0, 0, 0, -1))

    def test_phrases(self):
        from app.cli.schedule import parse_when
        expect = {"завтра в 9": "once", "tomorrow 9am": "once", "6pm": "once", "18:30": "once",
                  "по будням в 9:30": "daily", "mon-fri 9:00": "daily", "пн-пт 10:00": "daily",
                  "каждый понедельник в 10": "daily", "12.10.2026 9:00": "once", "12.10 18:30": "once",
                  "через полчаса": "once", "через час": "once", "в полночь": "once", "в 9 вечера": "once",
                  "в понедельник в 10": "once", "cron 0 9 * * 1-5": "cron"}
        for phrase, kind in expect.items():
            self.assertEqual(parse_when(phrase, self.NOW)["kind"], kind, phrase)
        self.assertEqual(parse_when("по будням в 9:30", self.NOW)["days"], [0, 1, 2, 3, 4])
        at = datetime.fromtimestamp(parse_when("в 9 вечера", self.NOW)["at"])
        self.assertEqual((at.hour, at.minute), (21, 0))
        self.assertEqual(parse_when("через полчаса", self.NOW)["at"], self.NOW + 1800)

    def test_old_refusals_stay(self):
        from app.cli.schedule import ScheduleError, parse_when
        for phrase in ("daily 9", "at 25:00", "9"):
            with self.assertRaises(ScheduleError, msg=phrase):
                parse_when(phrase, self.NOW)

    def test_fall_back_hour_never_answers_the_past(self):
        if not hasattr(time, "tzset"):
            self.skipTest("no tzset")
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Berlin"
        time.tzset()
        try:
            from app.cli.schedule import next_run
            second = datetime(2026, 10, 25, 2, 30, fold=1).timestamp()      # the repeated 02:30
            first = datetime(2026, 10, 25, 2, 30, fold=0).timestamp()
            for after in (first, second, datetime(2026, 10, 25, 2, 10, fold=1).timestamp()):
                for when in ({"kind": "cron", "expr": "*/15 * * * *"}, {"kind": "cron", "expr": "30 2 * * *"},
                             {"kind": "daily", "time": "02:30"}):
                    self.assertGreater(next_run(when, after), after, (when, after))
            # A daily 02:30 runs once on that night, not again at the repeated 02:30.
            self.assertGreater(next_run({"kind": "daily", "time": "02:30"}, first + 5) - first, 20 * 3600)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


class FilesErrorsAgent(unittest.TestCase):
    """12–14 and this round's corpus for the «announced step» nudge."""

    def test_mentions_skip_caches(self):
        from app.cli.tools.files import _junk
        self.assertTrue(_junk("__pycache__/calc.cpython-312.pyc"))
        self.assertTrue(_junk("node_modules/x/index.js"))
        self.assertTrue(_junk("calc.pyc"))
        self.assertFalse(_junk("calc.py"))
        self.assertFalse(_junk("docs/venvs.md"))

    def test_balance_ran_out_is_quota(self):
        from app.cli.errors import category_of
        self.assertEqual(category_of(503, "У провайдера закончился баланс"), "quota")
        self.assertEqual(category_of(402, "Insufficient balance, please top up"), "quota")
        self.assertNotEqual(category_of(503, "Service temporarily unavailable"), "quota")

    FINAL = ["Готово. Начну с тестов, если захотите — скажите.", "Готово: добавил median и тесты.",
             "Добавил функцию median в calc.py и тесты в test_calc.py. Все 5 тестов проходят.",
             "I've updated the README. Let me know if you want anything else.",
             "Done. I'll be here if you need more changes.", "Файл создан. Если нужно, я могу добавить ещё тесты.",
             "Исправил ошибку деления на ноль. Теперь функция возвращает 0 для пустого списка.",
             "Всё работает. В следующий раз я проверю это раньше.", "Тесты прошли: 12 passed. Что дальше?",
             "Сделал рефакторинг. Начну с малого: в будущем лучше разделить модуль.",
             "Here is the summary of changes:\n- added median\n- updated tests",
             "The function now handles empty lists. I will note that sorting is O(n log n).",
             "Ошибок не найдено. Проверю ещё раз при следующем запуске, если попросите.", "Ответ: 42.",
             "Изменения внесены в 3 файла. Запускаю тесты не нужно — они уже прошли.",
             "Итог: README обновлён, лицензия добавлена. Дальше можно настроить CI.",
             "Let me summarize: I added median and tests, all pass.", "Я прочитаю файл, если вы пришлёте путь.",
             "Если хотите, я добавлю типы. Скажите.", "Пишу кратко: всё сделано.",
             "All tests pass. I'll be happy to help further.", "Задача выполнена. Могу также добавить документацию.",
             "I can also add type hints if you'd like.", "Всё сделано, тесты зелёные.", ""]
    ANNOUNCED = ["Начну с чтения файлов проекта.", "Шаг 1 — добавляю median в calc.py. Для пустого списка — 0.",
                 "Let me read the file first.", "Now I'll run the tests.", "Сейчас посмотрю структуру проекта…",
                 "Понял задачу. Перехожу к правке test_calc.py.", "I'll start by listing the files.",
                 "Сначала прочитаю файл, потом исправлю.", "Let me check the tests.", "Next, I'll update the README."]

    def test_announces_next_step_corpus(self):
        from app.cli.agent import announces_next_step
        self.assertEqual([t for t in self.FINAL if announces_next_step(t)], [])
        self.assertEqual([t for t in self.ANNOUNCED if not announces_next_step(t)], [])


class MiniApp(unittest.TestCase):
    """8–11, the session caption, the TodoWrite card and the desktop send button."""

    def test_one_new_action_at_a_time(self):
        app_js, code_js, layout = _read(TG / "app.js"), _read(TG / "code.js"), _read(TG / "layout.css")
        self.assertIn('fab.classList.toggle("fab-off", !!S.chatQuery.trim() || !rows.length)', app_js)
        self.assertIn('$("#code-fab", view)', code_js)
        self.assertIn('html[data-layout="split"] #code-fab { display: none; }', layout)

    def test_meme_new_card_has_no_glow(self):
        self.assertIn(".meme-new::after { display: none; }", _read(TG / "app.css"))

    def test_profile_card_does_not_repeat_the_profile(self):
        js = _read(TG / "app.js")
        self.assertIn("const profileLine = profile.name && profile.name !== name", js)

    def test_settings_values_line_up(self):
        css = _read(TG / "app.css")
        self.assertIn("min-width: 2.6em; margin-left: auto;", css)
        self.assertIn(".cell-value + .chev { margin-left: -6px; }", css)
        self.assertIn("-webkit-line-clamp: 2", css)

    def test_session_caption_is_short_and_todowrite_not_doubled(self):
        js = _read(TG / "code.js")
        self.assertIn('tr("Запустится при отправке", "Starts when you send")', js)
        self.assertIn('it.name === "TodoWrite" && res && res.ok !== false && lastTodos >= 0', js)

    def test_disabled_send_button_stays_grey_under_the_mouse(self):
        layout = _read(TG / "layout.css")
        self.assertIn('html[data-hover="1"] .send-btn[disabled]:hover { filter: none;', layout)
        self.assertIn('html[data-hover="1"] .send-btn.stop:hover { background: var(--text);', layout)
        # The disabled rule comes after the generic hover rules it must beat.
        self.assertGreater(layout.index(".send-btn[disabled]:hover"), layout.index('.send-btn:hover {'))
        self.assertGreater(layout.index(".send-btn[disabled]:hover"), layout.index(".round-btn:hover"))

    def test_cache_versions_bumped(self):
        html = _read(TG / "index.html")
        for name in ("app.css", "layout.css", "code.js", "app.js"):
            self.assertRegex(html, name.replace(".", r"\.") + r"\?v=[\d.]+-r3[4-9]")


class Prices(unittest.TestCase):
    """`memucode models` and /model: one price for one model; GLM-5.3 at the vendor's rate."""

    def test_glm_vendor_price_beats_a_reseller_listing(self):
        from app import pricing
        fake = {"glm-5.3": {"id": "z-ai/glm-5.3", "price": {"input": 0.07, "output": 4.8, "cacheRead": 0.05}}}
        with patch.object(pricing, "_index_now", return_value=fake):
            pricing._memo.clear()
            for model, provider in (("reformboss-glm-5.3", "custom"), ("glm-5.3", ""), ("z-ai/glm-5.3", "")):
                price = pricing._lookup(model, provider, None)
                self.assertEqual((price.input, price.output, price.cache_read), (1.4, 4.4, 0.26), model)
                self.assertEqual(price.source, "vendor")
            flash = pricing._lookup("glm-5.3-flash", "", None)
            self.assertEqual((flash.input, flash.output), (0.15, 0.5))
        pricing._memo.clear()

    def test_overrides_still_win(self):
        from app import pricing
        price = pricing._lookup("glm-5.3", "", {"glm-5.3": {"input": 9, "output": 9}})
        self.assertEqual(price.input, 9)

    def test_models_command_uses_the_online_index(self):
        src = _read(ROOT / "app" / "cli" / "main.py")
        self.assertIn("await asyncio.to_thread(pricing.refresh_online, timeout=8.0)", src)
        self.assertIn('pricing.lookup(chosen, getattr(info, "provider", "") or "")',
                      _read(ROOT / "app" / "cli" / "commands" / "model_cmds.py"))


class ProxyTokenPath(unittest.TestCase):
    """MEMUAI_TOKEN: ignored only on a loopback Proxy (by design); checked when the Proxy is open to the network."""

    def test_admin_auth(self):
        from app import admin_api
        bad = SimpleNamespace(headers={"authorization": "Bearer wrong-token"})
        with patch.object(admin_api, "_browser_origin_guard", return_value=None):
            with patch.object(admin_api, "_is_loopback_bind", return_value=True):
                self.assertIsNone(admin_api._auth(bad))
            with patch.object(admin_api, "_is_loopback_bind", return_value=False):
                denied = admin_api._auth(bad)
                self.assertEqual(denied.status_code, 401)
                good = "r34-test-session"
                admin_api._sessions[good] = time.time() + 60
                try:
                    self.assertIsNone(admin_api._auth(SimpleNamespace(headers={"authorization": "Bearer " + good})))
                finally:
                    admin_api._sessions.pop(good, None)

    def test_hub_keys(self):
        from app.config import config_store
        saved = list(config_store.settings.api_keys)
        try:
            config_store.settings.api_keys = ["right"]
            self.assertFalse(config_store.check_api_key("wrong"))
            self.assertTrue(config_store.check_api_key("right"))
        finally:
            config_store.settings.api_keys = saved


class Voice(unittest.TestCase):
    def test_audio_suffix(self):
        from app.voice_runtime import audio_suffix
        self.assertEqual(audio_suffix("audio/ogg; codecs=opus", ".webm"), ".ogg")
        self.assertEqual(audio_suffix("audio/x-m4a", ".webm"), ".m4a")
        self.assertEqual(audio_suffix("AUDIO/FLAC", ".webm"), ".flac")
        self.assertEqual(audio_suffix("audio/unknown", ".webm"), ".webm")


if __name__ == "__main__":
    unittest.main()
