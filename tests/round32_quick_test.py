"""Round 32 (quick): Quick Access is one unit until Escape + fast, complete reading of the browser tab.

Run: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=<tmp> python -m pytest -q tests/round32_quick_test.py
pywebview / Win32 are Windows-only: the window layer is faked here (SetWindowPos calls are recorded).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r32-quick-"))

from app import quick_ask_core as core  # noqa: E402
from app import quick_page_read as qpr  # noqa: E402

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

class Api:
    """Records SetWindowPos; GetForegroundWindow / GetWindowLong are configurable."""

    def __init__(self, foreground=200, foreground_topmost=False):
        self.calls, self.fg, self.fg_topmost = [], foreground, foreground_topmost

    def GetForegroundWindow(self):
        return self.fg

    def GetWindowLong(self, hwnd, index):
        return 0x8 if self.fg_topmost else 0

    def SetWindowPos(self, hwnd, insert, x, y, w, h, flags):
        self.calls.append((hwnd, insert, flags))
        return True

    def take_foreground(self, hwnd):
        return True

    def IsWindow(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False

class Layer:
    def __init__(self, hwnd=300, visible=True):
        self.hwnd, self.visible, self.hidden = hwnd, visible, 0

    def hide(self):
        self.hidden += 1
        self.visible = False
        return True

def manager(api, *, pinned=False, mode=None, layer=None):
    from app.quick_ask import QuickAskManager
    m = QuickAskManager.__new__(QuickAskManager)
    m.api, m.hwnd, m.visible, m.pinned, m.lowered, m._dragging = api, 100, True, pinned, False, False
    m._lock = threading.RLock()
    prefs = {} if mode is None else {"quick_ask_on_leave": mode}
    m.load_prefs = lambda: dict(prefs)
    m.layer = layer
    return m

class OneUnitTests(unittest.TestCase):
    def test_setting_defaults_to_stay_and_normalizes(self):
        self.assertEqual(core.PREF_DEFAULTS["quick_ask_on_leave"], "stay")
        self.assertEqual(core.ON_LEAVE_CHOICES, ("stay", "back"))
        for raw, want in ((None, "stay"), ("", "stay"), ("garbage", "stay"), ("back", "back"), ("BEHIND", "back"),
                          ("step_back", "back"), ("top", "stay"), ("stay", "stay")):
            out = core.normalize_prefs({"quick_ask_on_leave": raw})
            self.assertEqual(out["quick_ask_on_leave"], want, raw)

    def test_click_elsewhere_changes_nothing_by_default(self):
        api = Api()
        layer = Layer()
        m = manager(api, layer=layer)
        self.assertFalse(m.step_back())
        self.assertFalse(m.lowered)
        self.assertEqual(api.calls, [], "no z-order change: the window and its panels stay on top together")
        self.assertEqual(layer.hidden, 0)
        self.assertTrue(m.visible)

    def test_stale_lowered_state_is_raised_as_one_unit(self):
        from app import quick_ask as qa
        api = Api()
        layer = Layer()
        m = manager(api, layer=layer)
        m.lowered = True
        self.assertFalse(m.step_back())
        self.assertFalse(m.lowered)
        self.assertEqual([(h, i) for h, i, _ in api.calls], [(300, qa.HWND_TOPMOST), (100, qa.HWND_TOPMOST)])

    def test_opt_in_step_back_moves_window_and_layer_together(self):
        from app import quick_ask as qa
        api = Api()
        layer = Layer()
        m = manager(api, mode="back", layer=layer)
        self.assertTrue(m.step_back())
        self.assertTrue(m.lowered)

        self.assertEqual([(h, i) for h, i, _ in api.calls],
                         [(300, qa.HWND_NOTOPMOST), (300, 200), (100, qa.HWND_NOTOPMOST), (100, 200)])
        self.assertTrue(all(f & qa.SWP_NOACTIVATE and f & qa.SWP_NOMOVE for _, _, f in api.calls))
        api.calls.clear()
        self.assertTrue(m.raise_to_top())
        self.assertEqual([(h, i) for h, i, _ in api.calls], [(300, qa.HWND_TOPMOST), (100, qa.HWND_TOPMOST)])

    def test_opt_in_step_back_hidden_layer_is_not_touched(self):
        from app import quick_ask as qa
        api = Api()
        m = manager(api, mode="back", layer=Layer(visible=False))
        self.assertTrue(m.step_back())
        self.assertEqual([(h, i) for h, i, _ in api.calls], [(100, qa.HWND_NOTOPMOST), (100, 200)])

    def test_click_on_own_satellite_is_not_leaving(self):
        api = Api(foreground=300)
        m = manager(api, mode="back", layer=Layer())
        self.assertFalse(m.step_back())
        m.extra_windows = (444,)
        api.fg = 444
        self.assertFalse(m.step_back())
        self.assertEqual(api.calls, [])

    def test_pinned_never_steps_back(self):
        api = Api()
        self.assertFalse(manager(api, pinned=True, mode="back", layer=Layer()).step_back())
        self.assertEqual(api.calls, [])

    def test_layer_goes_under_a_stepped_back_window(self):
        from app import quick_ask as qa
        from app.quick_layer import ScreenLayer
        layer = ScreenLayer.__new__(ScreenLayer)
        m = manager(Api(), mode="back")
        layer.manager = m
        self.assertEqual(layer.insert_after(), qa.HWND_TOPMOST)
        m.lowered = True
        self.assertEqual(layer.insert_after(), 100)
        m.pinned = True
        self.assertEqual(layer.insert_after(), qa.HWND_TOPMOST)
        src = read("app/quick_layer.py")
        self.assertIn("insert = self.insert_after()", src)
        self.assertEqual(src.count("api.SetWindowPos(self.hwnd, insert,"), 2)

    def test_hide_always_hides_the_layer_too(self):
        from app import quick_ask as qa
        api = Api()
        layer = Layer()
        m = manager(api, layer=layer)
        m._anim_token = m._selection_token = m._layer_token = 0
        m.previous_hwnd = 0
        m.form = None
        m._push = lambda script: None
        m._on_ui = lambda fn: None
        m._disarm_escape = lambda: None
        self.assertTrue(qa.QuickAskManager.hide(m))
        self.assertFalse(m.visible)
        self.assertEqual(layer.hidden, 1)

    def test_layer_created_late_is_hidden_when_quick_access_closed_meanwhile(self):
        """Esc pressed while the layer window was still being created (first open): no orphaned panels."""
        from app import quick_ask as qa
        api = Api()
        events = []

        class SlowLayer(Layer):
            def available(self):
                return True

            def show(self, geometry, monitors, reason="open"):
                m.visible = False
                m._layer_token += 1
                self.visible = True
                events.append("show")
                return True

            def hide(self):
                events.append("hide")
                return super().hide()

            def public(self):
                return {"visible": self.visible}

        layer = SlowLayer(visible=False)
        m = manager(api, layer=layer)
        m.native_ok, m._quitting, m._layer_token = True, False, 0
        m._monitors = lambda: [{}]
        m.ensure_layer = lambda: layer
        m.layer_settings = lambda: {"on": True, "show": True}
        m._push = lambda script: None
        threads = []
        real = threading.Thread

        def spy(*a, **k):
            t = real(*a, **k)
            threads.append(t)
            return t
        with mock.patch.object(qa.threading, "Thread", side_effect=spy):
            qa.QuickAskManager._layer_follow(m, {"monitor": 0}, "open")
        for t in threads:
            t.join(3)
        self.assertEqual(events, ["show", "hide"])
        self.assertFalse(layer.visible)

    def test_current_page_skips_uia_for_the_same_tab(self):
        from app import quick_ask as qa
        from app import quick_ask_browser as qb
        m = manager(Api())
        m.native_ok, m._page_token, m.page = True, 0, None
        m._push = lambda script: None
        rows = [{"hwnd": 555, "exe": "chrome.exe", "visible": True, "iconic": False, "cloaked": False,
                 "title": "Статья - Google Chrome"}]
        detects = []

        def detect(hwnd, **kw):
            detects.append(hwnd)
            return {"hwnd": hwnd, "browser": "Google Chrome", "title": "Статья", "url": "https://e.org/a", "source": "uia",
                    "t": time.time()}
        with mock.patch.object(qb, "top_level_windows", lambda: rows), mock.patch.object(qb, "detect", detect):
            first = qa.QuickAskManager.current_page(m)
            self.assertEqual(first["url"], "https://e.org/a")
            self.assertIs(qa.QuickAskManager.current_page(m), first)
            self.assertEqual(detects, [555], "same window, same title, read moments ago: no second UIA read")
            rows[0]["title"] = "Другая вкладка - Google Chrome"
            qa.QuickAskManager.current_page(m)
            self.assertEqual(detects, [555, 555])
            rows[0]["title"] = "Статья - Google Chrome"
            m.page["t"] = time.time() - qa.PAGE_FRESH - 1
            m.page["title"] = "Статья"
            qa.QuickAskManager.current_page(m)
            self.assertEqual(len(detects), 3, "older than PAGE_FRESH: read again")

    def test_page_and_settings_contract(self):
        js = read("app/pgstatic/quick/quick.js")
        self.assertIn('windowAction("inactive")', js)
        self.assertIn('String(S.prefs.quick_ask_on_leave || "stay") !== "back"', js)
        settings = read("app/pgstatic/quick-settings.js")
        self.assertIn('K.select("st-qa-leave"', settings)
        self.assertIn('save({ quick_ask_on_leave: e.target.value })', settings)
        for word in ("Оставаться поверх", "Stay on top", "Уходить назад", "Step back"):
            self.assertIn(word, settings)
        en_part = re.findall(r'tr\("[^"]*", "([^"]*)"\)', settings[settings.index("st-qa-leave") - 900: settings.index("st-qa-leave") + 300])
        self.assertTrue(en_part)
        self.assertFalse(any(re.search("[А-Яа-яЁё]", s) for s in en_part), en_part)
        ru = read("app/docs/ru/quick-ask.md")
        en = read("app/docs/en/quick-ask.md")
        self.assertIn("остаётся **поверх**", ru)
        self.assertIn("stay **on top**", en)
        self.assertIn("Уходить назад", ru)
        self.assertIn("Step back", en)

ARTICLE = ("<p>" + "Основной текст статьи про асинхронность и очереди задач. " * 30 + "</p>")
PAGE = f"""<!doctype html><html><head><title>Статья о Python</title><style>body{{color:red}}</style>
<script>var tracking = {{ big: "{'x' * 5000}" }};</script></head>
<body><div class="cookie-consent">Мы используем cookies</div>
<header><nav><a href="/">Главная</a> <a href="/news">Новости</a></nav><h1>Как работает asyncio</h1></header>
<div class="layout"><aside class="sidebar">Популярное: 10 лучших котиков</aside>
<main><article>
{ARTICLE}
<p>Ссылка на <a href="/docs/asyncio">документацию</a> и сноска<sup class="reference">[12]</sup>.</p>
<h2>Задачи</h2><ul><li>create_task</li><li>gather <b>и</b> wait</li></ul>
<pre><code>async def main():
    await asyncio.sleep(1)</code></pre>
<table><tr><th>Функция</th><th>Что делает</th></tr><tr><td>run</td><td>запускает цикл</td></tr></table>
<img src="data:image/png;base64,{'A' * 4000}" alt="Схема цикла событий">
<svg width="10"><path d="M0 0L10 10"/></svg>
<div class="share-buttons">Поделиться в VK</div>
<div class="related-posts">Читайте также: другое</div>
</article></main></div>
<footer>© 2026 Сайт. Все права защищены.</footer></body></html>"""

class ExtractTests(unittest.TestCase):
    def test_main_content_kept_junk_dropped(self):
        doc = qpr.extract(PAGE, "https://example.org/blog/asyncio")
        text = doc["text"]
        self.assertEqual(doc["title"], "Статья о Python")
        self.assertTrue(text.startswith("# Как работает asyncio"), text[:80])
        self.assertIn("## Задачи", text)
        self.assertIn("- create_task", text)
        self.assertIn("- gather и wait", text)
        self.assertIn("```\nasync def main():\n    await asyncio.sleep(1)\n```", text)
        self.assertIn("| Функция | Что делает |", text)
        self.assertIn("| run | запускает цикл |", text)
        self.assertIn("Ссылка на документацию и сноска.", text)
        for junk in ("cookies", "Главная", "котиков", "Поделиться", "Читайте также", "Все права", "tracking",
                     "base64", "AAAA", "M0 0", "[12]", "color:red", "Схема цикла"):
            self.assertNotIn(junk, text, junk)
        self.assertIn(["документацию", "https://example.org/docs/asyncio"], doc["links"])

    def test_alt_texts_are_optional(self):
        self.assertIn("[image: Схема цикла событий]", qpr.extract(PAGE, "https://e.org", alt=True)["text"])

    def test_inline_links_mode_and_space_inside_link(self):
        html = '<html><body><article><p>' + 'слово ' * 80 + 'см. статью<a href="/x"> Боты в Директе</a> тут.</p></article></body></html>'
        self.assertIn("статью Боты в Директе тут", qpr.extract(html, "https://h.ru/a")["text"])
        self.assertIn("[Боты в Директе](https://h.ru/x)", qpr.extract(html, "https://h.ru/a", links=True)["text"])

    def test_junk_class_holding_the_whole_article_is_rescued(self):
        html = ('<html><body><div class="comments-enabled post"><p>' + "Real article text. " * 200 +
                '</p></div><div class="comments"><p>Nice post!</p></div></body></html>')
        text = qpr.extract(html, "https://e.org")["text"]
        self.assertIn("Real article text.", text)
        self.assertNotIn("Nice post!", text)

    def test_news_list_falls_back_from_a_small_article_card(self):
        cards = "".join(f"<article><h3>Новость {i}</h3><p>{'Короткий анонс. ' * 25}</p></article>" for i in range(8))
        text = qpr.extract(f"<html><body><main>{cards}</main></body></html>", "https://n.ru")["text"]
        self.assertIn("Новость 0", text)
        self.assertIn("Новость 7", text)

    def test_plain_text_and_json_pass_through(self):
        doc = qpr.extract('{"a": 1,\n\n\n\n"b": 2}', "https://api.e.org/x.json")
        self.assertEqual(doc["text"], '{"a": 1,\n"b": 2}')

    def test_mis_nested_markup_and_deep_trees_do_not_crash(self):
        deep = "<div>" * 3000 + "<p>" + "deep text " * 100 + "</p>" + "</div>" * 3000
        self.assertIn("deep text", qpr.extract("<html><body>" + deep + "</body></html>")["text"])
        unclosed = "<html><body>" + "".join("<div>text %d <span>" % i for i in range(1500)) + "</body></html>"
        self.assertIn("text 1499", qpr.extract(unclosed)["text"])
        broken = "<html><body><p>one <b>two <i>three</p><p>four</b> five</i></p><table><tr><td>x</table>"
        self.assertIn("four", qpr.extract(broken)["text"])

    def test_big_page_is_fast(self):
        body = "".join(f"<div class='row'><p>{'Абзац текста ' * 40}</p><img src='data:image/png;base64,{'B' * 2000}'>"
                       f"<a href='/p{i}'>ссылка {i}</a><svg><path d='{'M1 1' * 200}'/></svg></div>" for i in range(400))
        html = f"<html><body><nav>{'<a href=/>x</a>' * 500}</nav><main>{body}</main></body></html>"
        self.assertGreater(len(html), 1_300_000)
        started = time.monotonic()
        doc = qpr.extract(html, "https://big.example")
        self.assertLess(time.monotonic() - started, 6.0)
        self.assertNotIn("BBBB", doc["text"])
        self.assertLess(doc["chars"], len(html) / 5)

class BudgetTests(unittest.TestCase):
    def test_token_estimate(self):
        self.assertEqual(qpr.estimate_tokens(""), 0)
        latin = qpr.estimate_tokens("a" * 4000)
        cyr = qpr.estimate_tokens("я" * 4000)
        self.assertAlmostEqual(latin, 1001, delta=2)
        self.assertGreater(cyr, latin * 1.5)

    def test_fit_keeps_head_outline_and_tail(self):
        parts = [f"## Раздел {i}\n\n" + ("Текст раздела номер %d. " % i) * 60 for i in range(40)]
        text = "\n\n".join(parts)
        out = qpr.fit(text, max_tokens=3000)
        self.assertTrue(out["truncated"])
        self.assertLessEqual(out["tokens"], 3000 * 1.1)
        self.assertIn("## Раздел 0", out["text"])
        self.assertIn("## Раздел 39 (end)\n\nТекст", out["text"])
        self.assertIn("Раздел 38", out["text"])
        self.assertRegex(out["text"], r"characters of the page are omitted here\. Omitted sections: .*Раздел 2\d")
        small = qpr.fit("короткий текст", max_tokens=3000)
        self.assertEqual((small["text"], small["truncated"]), ("короткий текст", False))

    def test_links_block_is_bounded(self):
        links = [[f"Документ номер {i}", f"https://e.org/{i}"] for i in range(100)]
        block = qpr.links_block(links, max_tokens=200)
        self.assertTrue(block.startswith("Links on the page:"))
        self.assertLessEqual(qpr.estimate_tokens(block), 230)
        self.assertEqual(qpr.links_block([]), "")

class ReadPageTests(unittest.TestCase):
    def setUp(self):
        qpr.cache_clear()

    def read(self, page, **kw):
        from app import quick_ask_api
        return asyncio.run(quick_ask_api.read_page_content(page, run_tool=kw.pop("run_tool", None), **kw))

    def test_direct_fetch_extracts_and_caches(self):
        calls = []

        async def fetch_html(url):
            calls.append(url)
            return {"url": url, "raw": PAGE}
        page = {"id": "p1", "url": "https://example.org/blog/asyncio#top", "title": "Как работает asyncio"}
        out = self.read(page, fetch_html=fetch_html)
        self.assertEqual(out["source"], "fetch")
        self.assertIn("## Задачи", out["text"])
        self.assertNotIn("cookies", out["text"])
        self.assertFalse(out["cached"])
        for key in ("ms", "tokens", "chars", "truncated"):
            self.assertIn(key, out)
        again = self.read(dict(page, url="https://example.org/blog/asyncio"), fetch_html=fetch_html)
        self.assertTrue(again["cached"])
        self.assertEqual(len(calls), 1, "the same tab is not fetched twice")
        other = self.read(dict(page, title="Совсем другая страница"), fetch_html=fetch_html)
        self.assertFalse(other["cached"])
        self.assertEqual(len(calls), 2, "same URL, another title: read again")
        from app import quick_ask_api
        hit = quick_ask_api.page_for_url("https://example.org/blog/asyncio")
        self.assertTrue(hit and hit["cached"] and "## Задачи" in hit["text"])
        self.assertIsNone(quick_ask_api.page_for_url("https://example.org/unknown"))

    def test_web_off_never_uses_the_cache_or_the_network(self):
        async def fetch_html(url):
            raise AssertionError("no fetch when web access is off")

        class M:
            def page_text(self, pid, limit):
                return "Видимый текст вкладки. " * 40
        out = self.read({"id": "p1", "url": "https://e.org/a", "title": "A"}, fetch_html=fetch_html, manager=M(),
                        allow_fetch=False)
        self.assertEqual((out["source"], out["reason"]), ("screen", "web_off"))

    def test_login_wall_reads_the_screen_and_does_not_cache_it(self):
        async def fetch_html(url):
            return {"url": url, "raw": "<html><head><title>Вход</title></head><body><p>Войдите в аккаунт, чтобы продолжить</p></body></html>"}
        reads = []

        class M:
            def page_text(self, pid, limit):
                reads.append(pid)
                return "Мои заказы\nЗаказ №1 — доставлен\nЗаказ №1 — доставлен\n" + "строка " * 100
        page = {"id": "p9", "url": "https://shop.example/orders", "title": "Мои заказы"}
        out = self.read(page, fetch_html=fetch_html, manager=M())
        self.assertEqual((out["source"], out["reason"]), ("screen", "login_wall"))
        self.assertEqual(out["text"].count("Заказ №1"), 1, "consecutive duplicate lines are dropped")
        self.read(page, fetch_html=fetch_html, manager=M())
        self.assertEqual(len(reads), 2, "a personal page's visible text is never served from the cache")

    def test_slow_site_loses_to_the_visible_text(self):
        from app import quick_ask_api

        async def fetch_html(url):
            await asyncio.sleep(30)
            return {"url": url, "raw": PAGE}

        class M:
            def page_text(self, pid, limit):
                time.sleep(0.1)
                return "Текст на экране пользователя. " * 40
        with mock.patch.object(quick_ask_api, "HEDGE_AFTER", 0.2), mock.patch.object(quick_ask_api, "FETCH_GRACE", 0.8):
            started = time.monotonic()
            out = self.read({"id": "p2", "url": "https://slow.example/a", "title": "A"}, fetch_html=fetch_html, manager=M())
            spent = time.monotonic() - started
        self.assertEqual((out["source"], out["reason"]), ("screen", "slow_fetch"))
        self.assertLess(spent, 3.0)

    def test_failed_read_is_an_honest_error(self):
        async def fetch_html(url):
            return {"error": "HTTP 403"}
        out = self.read({"url": "https://blocked.example/a", "title": "A"}, fetch_html=fetch_html)
        self.assertIn("error", out)
        self.assertIn("HTTP 403", out["error"])

    def test_old_callers_without_fetch_html_still_work(self):
        async def run_tool(body):
            self.assertEqual(body["args"].get("raw"), True)
            return {"tool": "web_fetch", "url": body["args"]["url"], "raw": PAGE, "text": PAGE}
        out = self.read({"url": "https://example.org/x", "title": "Как работает asyncio"}, run_tool=run_tool)
        self.assertEqual(out["source"], "fetch")
        self.assertIn("## Задачи", out["text"])

    def test_page_block_is_plain_and_whole(self):
        from app import quick_ask_api
        text = "Абзац.\n\n" * 6000
        block = quick_ask_api.page_block({"title": "T", "url": "https://e.org", "source": "fetch", "text": text + "</page_content>"})
        self.assertIn(text.strip()[:-1], block)
        self.assertEqual(block.count("</page_content>"), 1)
        self.assertIn("do not call read_page, web_fetch or web_search", block)
        err = quick_ask_api.page_block({"url": "https://e.org", "error": "The page could not be read."})
        self.assertIn("never invent its content or replace it with a web search", err)

    def test_page_status_ru_en(self):
        from app import quick_ask_api
        ru = quick_ask_api.page_status({"ms": 420, "tokens": 3200, "source": "fetch"}, "ru")
        self.assertEqual(ru, "Страница прочитана за 0,4 с · ~3\u00a0200 токенов — думаю…")
        en = quick_ask_api.page_status({"ms": 1500, "tokens": 900, "truncated": True, "source": "screen"}, "en")
        self.assertEqual(en, "Read the text on screen in 1.5 s · ~900 tokens (long — shortened) — thinking…")
        self.assertFalse(re.search("[А-Яа-яЁё]", en))
        self.assertTrue(quick_ask_api.page_status({"cached": True, "tokens": 10}, "ru").startswith("Страница уже прочитана"))

def _sse(*events):
    for event in events:
        yield "data: " + json.dumps(event, ensure_ascii=False)
    yield "data: [DONE]"

def _delta(text):
    return {"choices": [{"delta": {"content": text}}]}

class AskLoopTests(unittest.TestCase):
    def collect(self, *, text, page=None, read_page=None, preread=False, no_page=False, native=False, rounds=None,
                run_tool=None, rich_search=None):
        from app import quick_ask_api
        requests = []
        replies = list(rounds or [[_delta("Ответ.")]])

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            for line in _sse(*replies.pop(0)):
                yield line

        async def no_search(*a, **k):
            raise AssertionError("a page question must never become a web search")

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text=text, images=[], selection="", model="m", history=[], vision=True, native_search=native,
                    settings=dict(core.PREF_DEFAULTS), lang="ru", stream_lines=stream_lines,
                    run_tool=run_tool, rich_search=rich_search or no_search, rich_payload=lambda d: d, capture=None,
                    page=page, read_page=read_page, preread_page=preread, no_page=no_page):
                out.append(json.loads(chunk[6:]))
            return out
        return asyncio.run(collect()), requests

    def test_preread_page_no_second_read_no_native_search(self):
        async def read_page(max_chars=24000):
            return {"tool": "read_page", "source": "fetch", "url": "https://a.dev/x", "title": "X", "text": "PAGE TEXT",
                    "chars": 9, "tokens": 4, "ms": 350, "truncated": False, "cached": False}
        events, requests = self.collect(text="Перескажи эту страницу", page={"url": "https://a.dev/x", "title": "X"},
                                        read_page=read_page, preread=True, native=True)
        page_ev = next(e for e in events if e["event"] == "page")
        self.assertEqual((page_ev["ms"], page_ev["tokens"], page_ev["cached"]), (350, 4, False))
        labels = [e.get("label") for e in events if e["event"] == "status"]
        self.assertIn("Страница прочитана за 0,3 с · ~4 токенов — думаю…".replace(" токенов", " токенов"), labels)
        self.assertNotIn("Думаю…", labels)
        system = requests[0]["messages"][0]["content"]
        self.assertIn("ALREADY in <page_content>", system)
        self.assertIn("PAGE TEXT", json.dumps(requests[0]["messages"], ensure_ascii=False))
        self.assertFalse(requests[0]["web_search"], "no native web search when the page is already read")

    def test_no_tab_page_question_is_not_web_searched(self):
        events, requests = self.collect(text="Сделай саммари этой страницы", no_page=True)
        self.assertFalse(any(e["event"] == "status" and e.get("status") == "searching" for e in events))
        self.assertIn("No browser tab is open", requests[0]["messages"][0]["content"])

    def test_failed_read_is_not_web_searched(self):
        async def read_page(max_chars=24000):
            return {"tool": "read_page", "url": "https://a.dev/x", "title": "X", "error": "The page could not be read."}
        events, requests = self.collect(text="Перескажи эту страницу", page={"url": "https://a.dev/x", "title": "X"},
                                        read_page=read_page, preread=True)
        self.assertFalse(any(e["event"] == "page" for e in events))
        self.assertIn("Reading the page failed", json.dumps(requests[0]["messages"], ensure_ascii=False))

    def test_model_web_fetch_of_the_read_tab_uses_the_cache(self):
        qpr.cache_clear()
        qpr.cache_put("https://a.dev/x", {"source": "fetch", "text": "CACHED PAGE", "title": "X", "url": "https://a.dev/x"})
        tool_calls = []

        async def run_tool(body):
            tool_calls.append(body)
            return {"tool": body["tool"], "text": "network"}
        call = '```memuai-tool\n{"tool": "web_fetch", "args": {"url": "https://a.dev/x"}}\n```'
        events, requests = self.collect(text="Что значит термин X на этой странице?", page={"url": "https://a.dev/x", "title": "X"},
                                        rounds=[[_delta(call)], [_delta("Готово.")]], run_tool=run_tool,
                                        rich_search=lambda *a, **k: asyncio.sleep(0, {}))
        self.assertEqual([c for c in tool_calls if c.get("tool") == "web_fetch"], [])
        self.assertIn("CACHED PAGE", json.dumps(requests[-1]["messages"], ensure_ascii=False))

class LivePageTests(unittest.TestCase):
    """Real pages through the real fetcher (skipped without network)."""

    def test_real_page_is_read_compactly(self):
        if os.environ.get("MEMU_R32_LIVE") != "1":
            self.skipTest("set MEMU_R32_LIVE=1 to fetch real pages")
        out = asyncio.run(qpr.fetch_html("https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch"))
        if out.get("error"):
            self.skipTest("no network: " + out["error"])
        doc = qpr.extract(out["raw"], out["url"])
        self.assertIn("# Using the Fetch API", doc["text"])
        self.assertLess(len(doc["text"]), len(out["raw"]) / 4)

class BuildTests(unittest.TestCase):
    def test_module_is_packaged(self):
        self.assertIn('"app.quick_page_read"', read("build.py"))
        self.assertIn("'app.quick_page_read'", read("MemuAI.spec"))

if __name__ == "__main__":
    unittest.main()
