"""Round 31 (r31-panels): fixes of the panels zone.

* lenient model JSON: <think> blocks, prose around the object, a stray closing brace mid-answer, trailing commas,
  the object with expected keys wins over a nested one;
* blocks written as {"metric": {...}} / with "kind" instead of "type";
* model_wall(): rate limit / missing model / rejected key / provider down / generic, proxy-offline, timeout;
* drop_placeholders() / tidy_model_text(): «N/A», «—», markdown/HTML in values;
* static AI panels (no bindings) are accepted; a second pass by the model's runbook fills missing values;
* edits: subject change → recompose + immediate rebuild, runbook change → rebuild, model change clears a model wall,
  title_custom only when the title really changes;
* reset_stale_claims(); _today() with a numeric offset; _lenient_target(); word-boundary chip labels;
* Hacker News named source; telegram unit spacing, countdown block of AI panels, no double date;
* panels.js wall texts for the new statuses (RU + EN, no Cyrillic in EN).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import panels, panels_curate, pgdata, telegram_panels  # noqa: E402

PANELS_JS = (ROOT / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8")
PANELS_CSS = (ROOT / "app" / "pgstatic" / "panels.css").read_text(encoding="utf-8")
TG_PANELS_JS = (ROOT / "app" / "tgapp" / "panels.js").read_text(encoding="utf-8")

class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        panels._TASKS.clear()
        self.saved_ctx = panels.context()
        self.saved_loop = panels._LOOP

    def tearDown(self) -> None:
        panels.set_context(self.saved_ctx)
        panels._LOOP = self.saved_loop
        panels._TASKS.clear()
        self.patch.stop()
        self.tmp.cleanup()

    def find(self, pid):
        return next(p for p in panels.load()["panels"] if p["id"] == pid)

class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    async def complete(self, model, messages):
        self.prompts.append((model, messages))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)

async def fake_search(query, limit=5):
    return {"results": [{"title": "Page", "url": "https://src.example/a", "snippet": "Value 42"}],
            "answer": None, "pages": [{"url": "https://src.example/a", "excerpt": "Value 42"}]}

async def fake_fetch(url, limit=12000):
    return {"url": url, "title": "Page", "text": "Value 42 at " + url}

def ctx(model):
    panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, fetch=fake_fetch,
                                           default_model=lambda: "prov/test-model"))

class ExtractJson(unittest.TestCase):
    def test_think_block_and_prose(self):
        text = "<think>Maybe {{price}} or {\"x\": 1}</think>Sure! Here: {\"values\": {\"price\": \"5\"}} hope it helps"
        self.assertEqual(panels._extract_json(text), {"values": {"price": "5"}})

    def test_unclosed_think_prefix(self):
        text = "reasoning {\"a\":1} more</think>\n{\"spec\": {\"blocks\": []}}"
        self.assertEqual(panels._extract_json(text), {"spec": {"blocks": []}})

    def test_stray_brace_mid_answer(self):
        text = ('{"title":"HN","spec":{"blocks":[]},"runbook":{"prompt":"p","urls":["https://news.ycombinator.com"]}},'
                '"rhythm":"hourly","cadence":{"kind":"manual"}}')
        out = panels._extract_json(text)
        self.assertEqual(out["rhythm"], "hourly")
        self.assertEqual(out["cadence"], {"kind": "manual"})
        self.assertEqual(out["runbook"]["urls"], ["https://news.ycombinator.com"])

    def test_trailing_commas_and_fence(self):
        text = "```json\n{\"values\": {\"a\": 1,},}\n```"
        self.assertEqual(panels._extract_json(text), {"values": {"a": 1}})

    def test_top_level_object_wins_over_nested(self):
        text = '{"items": [{"title": "A", "url": "https://a"}, {"title": "B"}]}'
        self.assertEqual(panels._extract_json(text), {"items": [{"title": "A", "url": "https://a"}, {"title": "B"}]})

    def test_expected_keys_preferred(self):
        text = 'Example: {"foo": 1}. Answer: {"values": {"x": "2"}}'
        self.assertEqual(panels._extract_json(text), {"values": {"x": "2"}})

    def test_no_json_is_a_format_wall(self):
        with self.assertRaises(panels.Wall) as cm:
            panels._extract_json("I cannot do that")
        self.assertEqual(cm.exception.status, "blocked:model-format")

class BlockAliases(unittest.TestCase):
    def test_wrapped_and_kind(self):
        spec, errors = panels.validate_spec({"blocks": [
            {"metric": {"value": "{{price}}", "label": "Price"}},
            {"kind": "badge", "text": "ok", "tone": "ok"},
            {"title": "Hello"},
            {"block": "text", "text": "x"},
        ]})
        self.assertEqual([b["type"] for b in spec["blocks"]], ["metric", "badge", "title", "text"], errors)
        self.assertEqual(spec["blocks"][2]["text"], "Hello")

    def test_unknown_still_rejected(self):
        spec, errors = panels.validate_spec({"blocks": [{"weird": {"a": 1}}, {"type": "text", "text": "ok"}]})
        self.assertEqual([b["type"] for b in spec["blocks"]], ["text"])
        self.assertTrue(any("unknown block type" in e for e in errors))

    def test_countdown_target_is_normalised(self):
        spec, _ = panels.validate_spec({"blocks": [
            {"type": "countdown", "target": "Fri 2027-01-01 12:00 AM MSK (UTC+03:00)", "label": "NY"}]})
        self.assertEqual(spec["blocks"][0]["target"], "2027-01-01T00:00+03:00")
        spec, _ = panels.validate_spec({"blocks": [{"type": "countdown", "target": "{{target}}"}]})
        self.assertEqual(spec["blocks"][0]["target"], "{{target}}")

class LenientTarget(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(panels._lenient_target("2027-01-01T00:00+03:00"), "2027-01-01T00:00+03:00")
        self.assertEqual(panels._lenient_target("01.01.2027 18:30 UTC+3"), "2027-01-01T18:30+03:00")
        self.assertEqual(panels._lenient_target("2027-03-05 01:15 PM GMT-05:00"), "2027-03-05T13:15-05:00")
        self.assertTrue(panels._lenient_target("2027-01-01").startswith("2027-01-01T00:00"))
        self.assertEqual(panels._lenient_target("soon"), "")
        self.assertEqual(panels._lenient_target("2027-02-31"), "")

class Today(unittest.TestCase):
    def test_numeric_offset_and_weekday(self):
        text = panels._today({})
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(UTC[+-]\d{2}:\d{2}, [A-Z][a-z]+day; user's local time\)$")

class ClipWords(unittest.TestCase):
    def test_word_boundary(self):
        self.assertEqual(panels._clip_words("Все публикации под тегом", 18), "Все публикации…")
        self.assertEqual(panels._clip_words("short", 18), "short")
        self.assertLessEqual(len(panels._clip_words("Supercalifragilisticexpialidocious", 10)), 10)

    def test_web_title_capitalised(self):
        p = panels.make_template_panel("web", {"url": "https://python.org", "what": "последняя версия Python и дата выхода"},
                                       panels._clean_settings({}))
        self.assertTrue(p["title"].startswith("Последняя версия"))
        self.assertFalse(p["spec"]["mini"]["label"].endswith(" Pyt"))
        self.assertLessEqual(len(p["spec"]["mini"]["label"]), 20)

class ModelWall(unittest.TestCase):
    def test_statuses(self):
        cases = {
            "HTTP 429: Too Many Requests": "blocked:model-rate-limit",
            "quota exceeded for this key": "blocked:model-rate-limit",
            "HTTP 404 model not found": "blocked:model-missing",
            "Proxy не нашёл модель foo": "blocked:model-missing",
            "HTTP 401 Unauthorized": "blocked:model-auth",
            "HTTP 503 No available accounts": "blocked:model-down",
            "Proxy offline": "blocked:proxy-offline",
            "something weird": "blocked:model-error",
        }
        for message, status in cases.items():
            self.assertEqual(panels.model_wall(message, "prov/qwen-x").status, status, message)

    def test_rate_limit_retry_after_and_language(self):
        wall = panels.model_wall("HTTP 429 retry-after: 120", "prov/qwen-x", {"lang": "en"})
        self.assertEqual(wall.retry_after, 120.0)
        self.assertIn("qwen-x", wall.message)
        self.assertNotRegex(wall.message, "[А-Яа-яЁё]")
        self.assertEqual(panels.model_wall("HTTP 429", "m").retry_after, 900.0)
        ru = panels.model_wall("HTTP 404", "prov/abc", {"lang": "ru"})
        self.assertIn("не найдена", ru.message)

    def test_long_provider_text_not_on_card(self):
        wall = panels.model_wall("HTTP 503 " + "x" * 500, "m")
        self.assertLess(len(wall.message), 140)

    def test_transient_model_down(self):
        self.assertIn("blocked:model-down", panels.TRANSIENT_WALLS)

class AskModel(Base):
    def test_exceptions_map_to_walls(self):
        import httpx

        async def run(exc):
            async def complete(model, messages):
                raise exc
            panels.set_context(panels.PanelContext(complete=complete))
            with self.assertRaises(panels.Wall) as cm:
                await panels._ask_model("p/m", "s", "u", {"lang": "en"})
            return cm.exception

        self.assertEqual(asyncio.run(run(httpx.ConnectError("All connection attempts failed"))).status, "blocked:proxy-offline")
        self.assertEqual(asyncio.run(run(httpx.ReadTimeout("t"))).status, "blocked:model-timeout")
        self.assertEqual(asyncio.run(run(RuntimeError("HTTP 429 slow down"))).status, "blocked:model-rate-limit")
        fmt = asyncio.run(self._format())
        self.assertEqual(fmt.status, "blocked:model-format")
        self.assertIn("wrong format", fmt.message)

    async def _format(self):
        async def complete(model, messages):
            return "no json here"
        panels.set_context(panels.PanelContext(complete=complete))
        try:
            await panels._ask_model("p/m", "s", "u", {"lang": "en"})
        except panels.Wall as wall:
            return wall

    def test_rate_limit_backoff_uses_retry_after(self):
        ctx(FakeModel([RuntimeError("HTTP 429 retry-after: 1200")]))
        p = panels.create_panel(template="web", params={"url": "https://shop.example/item", "what": "price"}, start=False)
        now = time.time()
        out = asyncio.run(panels.refresh_panel(p["id"], force=True, now=now))
        self.assertEqual(out["wall"]["status"], "blocked:model-rate-limit")
        self.assertGreaterEqual(out["next_refresh"] - now, 1200 - 1)

class Placeholders(unittest.TestCase):
    def test_drop(self):
        out = panels.drop_placeholders({"a": "N/A", "b": "—", "c": "нет данных", "d": "  ", "e": "42",
                                        "f": ["n/a", "ok"], "g": [{"title": "N/A"}], "h": 0})
        self.assertEqual(out, {"e": "42", "f": ["ok"], "h": 0})

    def test_tidy(self):
        self.assertEqual(panels.tidy_model_text("**83 000 $**"), "83 000 $")
        self.assertEqual(panels.tidy_model_text("`3.15.0`"), "3.15.0")
        self.assertEqual(panels.tidy_model_text("<b>2:3</b>"), "2:3")
        self.assertEqual(panels.tidy_model_text("[Reuters](https://reuters.com/x)"), "Reuters")
        self.assertEqual(panels.tidy_model_text("Tom &amp; Jerry"), "Tom & Jerry")
        self.assertEqual(panels.tidy_model_text("2 * 3 = 6"), "2 * 3 = 6")
        rows = panels.drop_placeholders({"news": [{"title": "**A**", "url": "https://a.example/**x**"}]})
        self.assertEqual(rows["news"][0], {"title": "A", "url": "https://a.example/**x**"})

class Compose(Base):
    def test_static_panel_without_bindings(self):
        ctx(FakeModel([{"title": "New Year", "spec": {"blocks": [
            {"type": "title", "text": "New Year"},
            {"type": "countdown", "target": "2099-01-01T00:00+03:00", "label": "NY"}]}}]))
        p = panels.create_panel(subject="countdown to new year 2099", start=False)
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live", out.get("wall"))
        self.assertEqual([b["type"] for b in out["spec"]["blocks"]], ["title", "countdown"])

    def test_second_pass_by_runbook(self):
        model = FakeModel([
            {"title": "Py", "spec": {"blocks": [{"type": "metric", "value": "{{version}}", "label": "Python"}]},
             "values": {}, "runbook": {"prompt": "latest python", "urls": ["https://python.org/downloads"]}},
            {"values": {"version": "3.15.0"}, "evidence": [{"url": "https://python.org/downloads"}]},
        ])
        ctx(model)
        p = panels.create_panel(subject="latest python version", start=False)
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live", out.get("wall"))
        self.assertEqual(out["data"].get("version"), "3.15.0")
        self.assertEqual(len(model.prompts), 2)

    def test_values_without_any_and_no_runbook_is_not_found(self):
        ctx(FakeModel([{"title": "X", "spec": {"blocks": [{"type": "metric", "value": "{{v}}"}]}, "values": {"v": "N/A"}}]))
        p = panels.create_panel(subject="unknown thing", start=False)
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "blocked:not-found")

    def test_compose_prompt_has_offset_and_no_remarks_rule(self):
        model = FakeModel([{"title": "T", "spec": {"blocks": [{"type": "metric", "value": "{{v}}"}]}, "values": {"v": "1"}}])
        ctx(model)
        p = panels.create_panel(subject="thing", start=False)
        asyncio.run(panels.refresh_panel(p["id"], force=True))
        system, user = model.prompts[0][1][0]["content"], model.prompts[0][1][1]["content"]
        self.assertIn("never substitute a different subject", system)
        self.assertRegex(user, r"Now: .*\(UTC[+-]\d\d:\d\d")

class Edits(Base):
    def _live_ai(self):
        ctx(FakeModel([{"title": "Bitcoin", "spec": {"blocks": [{"type": "metric", "value": "{{price}}"}]},
                        "values": {"price": "83 000"}}]))
        p = panels.create_panel(subject="bitcoin price", start=False)
        return asyncio.run(panels.refresh_panel(p["id"], force=True))

    def test_unchanged_title_is_not_custom(self):
        p = self._live_ai()
        with patch.object(panels, "start_refresh"):
            out = panels.update_panel(p["id"], {"title": p["title"]})
        self.assertFalse(out.get("title_custom"))
        with patch.object(panels, "start_refresh"):
            out = panels.update_panel(p["id"], {"title": "Мой BTC"})
        self.assertTrue(out["title_custom"])
        self.assertEqual(out["title"], "Мой BTC")

    def test_subject_change_recomposes_now(self):
        p = self._live_ai()
        calls = []
        with patch.object(panels, "start_refresh", side_effect=lambda pid, **kw: calls.append(pid)):
            out = panels.update_panel(p["id"], {"subject": "ethereum price"})
        self.assertTrue(out["recompose"])
        self.assertEqual(calls, [p["id"]])

        ctx(FakeModel([{"title": "Ethereum", "spec": {"blocks": [{"type": "metric", "value": "{{eth}}"}]},
                        "values": {"eth": "3 100"}}]))
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["data"], {"eth": "3 100"})
        self.assertFalse(out["recompose"])
        self.assertEqual(out["title"], "Ethereum")

    def test_failed_recompose_keeps_old_spec(self):
        p = self._live_ai()
        with patch.object(panels, "start_refresh"):
            panels.update_panel(p["id"], {"subject": "ethereum price"})
        ctx(FakeModel([RuntimeError("HTTP 503")]))
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["data"], {"price": "83 000"})
        self.assertTrue(out["recompose"])
        self.assertEqual(out["wall"]["status"], "blocked:model-down")

    def test_runbook_change_rebuilds(self):
        p = self._live_ai()
        calls = []
        with patch.object(panels, "start_refresh", side_effect=lambda pid, **kw: calls.append(pid)):
            panels.update_panel(p["id"], {"runbook": p["runbook"]})
            self.assertEqual(calls, [])
            panels.update_panel(p["id"], {"runbook": {"prompt": "use coingecko", "urls": ["https://coingecko.com"]}})
        self.assertEqual(calls, [p["id"]])

    def test_model_change_clears_model_wall(self):
        ctx(FakeModel([RuntimeError("HTTP 404 model not found")]))
        p = panels.create_panel(subject="bitcoin price", start=False)
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "blocked:model-missing")
        calls = []
        with patch.object(panels, "start_refresh", side_effect=lambda pid, **kw: calls.append(pid)):
            out = panels.update_panel(p["id"], {"ai_model": "prov/other"})
        self.assertIsNone(out["wall"])
        self.assertEqual(out["failures"], 0)
        self.assertEqual(calls, [p["id"]])

class StaleClaims(Base):
    def test_reset(self):
        p = panels.create_panel(template="countdown", params={"target": "2099-01-01T00:00", "title": "X"}, start=False)

        def mark(doc):
            panels._find(doc, p["id"])["refreshing_until"] = time.time() + 600
        panels.mutate(mark)
        self.assertEqual(panels.reset_stale_claims(), 1)
        self.assertFalse(self.find(p["id"]).get("refreshing_until"))
        self.assertEqual(panels.reset_stale_claims(), 0)

class HackerNews(unittest.TestCase):
    def test_wants_hn(self):
        for text in ("Top 5 Hacker News stories", "топ HN за сегодня", "news.ycombinator.com front page", "хакер ньюс"):
            self.assertTrue(panels_curate.wants_hn(text), text)
        for text in ("hacker attacks this week", "security news", "HNL airport"):
            self.assertFalse(panels_curate.wants_hn(text), text)

    def test_not_security_topic(self):
        self.assertNotEqual(panels_curate.detect_topic("Top 5 Hacker News stories right now"), "security")

    def test_plan_is_hn_front_page(self):
        plan = panels_curate.plan_for("Top 5 Hacker News stories right now")
        self.assertEqual(plan.get("named"), "hn")
        self.assertEqual(plan["keywords"], [])
        self.assertEqual(plan["queries"], [])
        self.assertTrue(plan["hn"])

class Telegram(unittest.TestCase):
    def _panel(self, blocks, data=None, title="T"):
        return {"id": "p1", "title": title, "kind": "custom", "status": "live", "spec": {"blocks": blocks},
                "data": data or {}, "sources": [], "wall": None, "updated": time.time(), "source": {"type": "ai"}}

    def test_unit_spacing(self):
        html = telegram_panels.render_html(self._panel([{"type": "metric", "value": "{{v}}", "unit": "$"}], {"v": "83 002"}))
        self.assertIn("<b>83 002</b> $", html)
        html = telegram_panels.render_html(self._panel([{"type": "metric", "value": "{{v}}", "unit": "°"}], {"v": "21"}))
        self.assertIn("<b>21</b>°", html)

    def test_countdown_block_of_ai_panel(self):
        target = (datetime.now().astimezone() + timedelta(days=10, hours=2)).isoformat(timespec="minutes")
        html = telegram_panels.render_html(self._panel([{"type": "title", "text": "NY"},
                                                        {"type": "countdown", "target": target}]))
        self.assertIn("<b>10</b>", html)
        self.assertRegex(html, r"Осталось дней")

    def test_no_double_date(self):
        target = (datetime.now().astimezone() + timedelta(days=5)).replace(microsecond=0)
        label = target.strftime("%d.%m.%Y")
        html = telegram_panels.render_html(self._panel([
            {"type": "title", "text": "Trip", "subtitle": label},
            {"type": "countdown", "target": target.isoformat(timespec="minutes"), "label": label}]))
        self.assertEqual(html.count(label), 1, html)

class WindowTexts(unittest.TestCase):
    def test_new_wall_texts(self):
        for reason in ("model-rate-limit", "model-missing", "model-auth", "model-down", "model-format", "bad-spec", "no-web"):
            match = re.search(r'"%s": t\("([^"]+)", "([^"]+)"\)' % re.escape(reason), PANELS_JS)
            self.assertTrue(match, reason)
            self.assertNotRegex(match.group(2), "[А-Яа-яЁё]", reason)

    def test_detail_shown_for_model_walls(self):
        self.assertIn("model-(error|rate-limit|down|missing|auth)", PANELS_JS)

class ImageAlias(unittest.TestCase):
    def test_url_as_picture(self):
        spec, errors = panels.validate_spec({"blocks": [{"type": "image", "url": "{{img}}", "caption": "c"}]})
        self.assertEqual(spec["blocks"], [{"type": "image", "src": "{{img}}", "caption": "c"}], errors)
        spec, _ = panels.validate_spec({"blocks": [{"type": "image", "image": "https://x.org/a.png"}]})
        self.assertEqual(spec["blocks"][0]["src"], "https://x.org/a.png")
        spec, _ = panels.validate_spec({"blocks": [{"type": "image", "src": "https://x.org/a.png", "url": "https://x.org"}]})
        self.assertEqual(spec["blocks"][0], {"type": "image", "src": "https://x.org/a.png", "url": "https://x.org"})

class CountdownTargets(unittest.TestCase):
    def test_bound_values_become_iso(self):
        spec = {"blocks": [{"type": "countdown", "target": "{{ny}}"},
                           {"type": "row", "blocks": [{"type": "countdown", "target": "{{b}}"}]},
                           {"type": "metric", "value": "{{c}}"}]}
        out = panels.countdown_targets(spec, {"ny": "01.01.2027 00:00 UTC+3", "b": "soon", "c": "01.01.2027"})
        self.assertEqual(out["ny"], "2027-01-01T00:00+03:00")
        self.assertNotIn("b", out)
        self.assertEqual(out["c"], "01.01.2027")

class HackerNewsStoredTopic(Base):
    def test_old_security_topic_is_redecided(self):
        seen = {}
        real_plan_for = panels_curate.plan_for

        def spy(brief, **kw):
            seen["topic"] = kw.get("topic")
            return real_plan_for(brief, **kw)

        async def build_feed(plan, state, **kw):
            seen["named"] = plan.get("named")
            return {"items": [], "sources": [], "note": "", "errors": [], "state": state,
                    "stats": {"candidates": 0, "relevant": 0, "stories": 1, "ranked": 0, "kept": 0, "shown": 0,
                              "sources_ok": 1, "sources_failed": 0, "model": False}}

        panel = {"id": "p_hn", "title": "HN", "subject": "", "options": {}, "cadence": {"kind": "manual"},
                 "source": {"type": "feed", "params": {"topic": "security", "brief": "Top 5 Hacker News stories right now", "count": 5}},
                 "feed": None, "ai_model": ""}
        settings = panels._clean_settings({"lang": "en"})
        with patch.object(panels_curate, "plan_for", spy), patch.object(panels_curate, "build_feed", build_feed):
            values, _sources, _state = asyncio.run(panels.fetch_feed(panel, settings))
        self.assertEqual(seen["topic"], "")
        self.assertEqual(seen["named"], "hn")
        self.assertTrue(values["subtitle"].startswith("1 source · 1 story"), values["subtitle"])

class TelegramCards(unittest.TestCase):
    def _panel(self, blocks, data=None, wall=None, kind="custom"):
        return {"id": "p1", "title": "T", "kind": kind, "status": "live", "spec": {"blocks": blocks},
                "data": data or {}, "sources": [], "wall": wall, "updated": time.time(), "source": {"type": "ai"}}

    def test_card_counts_days_of_ai_countdown(self):
        target = (datetime.now().astimezone() + timedelta(days=12, hours=3)).isoformat(timespec="minutes")
        c = telegram_panels.card(self._panel([{"type": "countdown", "target": "{{t}}", "label": "NY"}], {"t": target}), "ru")
        self.assertEqual(c["countdown"]["days_left"], 12)
        self.assertEqual(c["countdown"]["label"], "NY")

    def test_card_image(self):
        c = telegram_panels.card(self._panel([{"type": "image", "src": "{{i}}", "caption": "Comet"}],
                                             {"i": "https://img.example/a.jpg"}), "ru")
        self.assertEqual(c["image"]["src"], "https://img.example/a.jpg")
        self.assertIn("Comet", c["text"])
        self.assertIn("pn-img", TG_PANELS_JS)
        self.assertNotIn("onerror=", TG_PANELS_JS)

    def test_wall_in_the_other_language(self):
        wall = {"status": "needs_connector:github", "message": "Connect GitHub in Settings → Integrations."}
        c = telegram_panels.card(self._panel([], wall=wall, kind="github"), "ru")
        self.assertRegex(c["error"], "[А-Яа-я]")
        c = telegram_panels.card(self._panel([], wall=wall, kind="github"), "en")
        self.assertEqual(c["error"], wall["message"])
        ru = {"status": "blocked:model-down", "message": "Модель «x» сейчас недоступна — повторю позже."}
        c = telegram_panels.card(self._panel([], wall=ru), "en")
        self.assertNotRegex(c["error"], "[А-Яа-яЁё]")

class Pickers(unittest.TestCase):
    def test_own_pickers_wired(self):
        self.assertIn("function pickers(root)", PANELS_JS)
        self.assertIn("UI.enhanceTimes(root)", PANELS_JS)
        self.assertIn("UI.enhanceDates(root)", PANELS_JS)
        self.assertRegex(PANELS_JS, r"pickers\(back\);\s*\n\s*bindSeg\(back\)")
        self.assertIn("pickers(el);", PANELS_JS)
        self.assertIn('pickedValue(v("day"))', PANELS_JS)

        self.assertNotIn('$$q("[data-time]"', PANELS_JS)
        self.assertIn(".pln-pop { z-index: 4100; }", PANELS_CSS)

    def test_wall_detail_only_in_ui_language(self):
        self.assertIn("(en() || /[А-Яа-яЁё]/.test(detail))", PANELS_JS)

if __name__ == "__main__":
    unittest.main()
