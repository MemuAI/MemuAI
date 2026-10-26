"""Round21 panels: spec schema, storage/migration, cadence and walls, typed fetchers with mocked HTTP,
AI refresh/compose with a fake model, model tools and HTTP routes (app/panels.py, app/panels_api.py)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import panels, pgdata

WEATHER_JSON = {
    "current": {"temperature_2m": 12.4, "apparent_temperature": 10.1, "relative_humidity_2m": 71,
                "weather_code": 61, "wind_speed_10m": 4.2, "is_day": 1},
    "daily": {"time": ["2026-10-07", "2026-10-08", "2026-10-09"], "temperature_2m_max": [14, 15, 11],
              "temperature_2m_min": [8, 9, 6], "weather_code": [2, 61, 3]},
}
CBR_JSON = {"Date": "2026-10-07T11:30:00+03:00", "Valute": {
    "USD": {"Nominal": 1, "Value": 81.5, "Previous": 81.2},
    "EUR": {"Nominal": 1, "Value": 95.1, "Previous": 95.3},
    "JPY": {"Nominal": 100, "Value": 54.0, "Previous": 54.5}}}
RSS = """<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<rss version="2.0"><channel><title>Лента</title>
<item><title>Первая новость</title><link>https://example.org/1</link><pubDate>Wed, 07 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title><![CDATA[Вторая <b>новость</b>]]></title><link>javascript:alert(1)</link></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom feed</title>
<entry><title>Entry one</title><link href="https://example.org/a"/><updated>2026-10-07T09:00:00Z</updated></entry></feed>"""

class FakeNet:
    """Replaces panels._http_text: routes by URL substring, records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, url, *, limit=0):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, str) else json.dumps(value)
        raise ValueError(f"HTTP 404 {url}")

class PanelsBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(pgdata, "MEMU_DIR", self.root)]
        for item in self.patches:
            item.start()
        panels._TASKS.clear()
        self.saved_ctx = panels.context()
        self.saved_loop = panels._LOOP

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        panels.set_context(self.saved_ctx)
        panels._LOOP = self.saved_loop
        panels._TASKS.clear()
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)

    def find(self, pid):
        return next(p for p in panels.load()["panels"] if p["id"] == pid)

class SpecSchemaTest(unittest.TestCase):
    def test_valid_blocks_are_kept_and_bindings_found(self):
        spec, errors = panels.validate_spec({"blocks": [
            {"type": "title", "text": "{{place}}", "subtitle": "x", "icon": "{{icon}}"},
            {"type": "metric", "value": "{{temp}}", "unit": "°", "trend": "{{trend}}"},
            {"type": "list", "items": "{{items}}", "max": 50},
            {"type": "row", "blocks": [{"type": "badge", "text": "ok", "tone": "ok"}, {"type": "text", "text": "{{a.b}}"}]},
        ], "mini": {"value": "{{temp}}", "icon": "rain"}})
        self.assertEqual(errors, [])
        self.assertEqual([b["type"] for b in spec["blocks"]], ["title", "metric", "list", "row"])
        self.assertEqual(spec["blocks"][2]["max"], 20)
        self.assertEqual(panels.spec_bindings(spec), ["place", "icon", "temp", "trend", "items", "a"])
        self.assertEqual(spec["mini"]["icon"], "rain")

    def test_unknown_types_unsafe_urls_and_nesting_are_dropped(self):
        spec, errors = panels.validate_spec({"blocks": [
            {"type": "script", "text": "x"},
            {"type": "link", "text": "bad", "url": "javascript:alert(1)"},
            {"type": "image", "src": "file:///c:/secret.png"},
            {"type": "row", "blocks": [{"type": "row", "blocks": [{"type": "text", "text": "deep"}]}]},
            {"type": "link", "text": "ok", "url": "https://example.org"},
            "not a block",
        ]})
        self.assertEqual([b["type"] for b in spec["blocks"]], ["link"])
        self.assertTrue(any("unknown block type" in e for e in errors))
        self.assertTrue(any("row cannot be nested" in e for e in errors))

    def test_limits(self):
        spec, errors = panels.validate_spec({"blocks": [{"type": "text", "text": "x" * 5000}] * 40})
        self.assertEqual(len(spec["blocks"]), panels.MAX_BLOCKS)
        self.assertLessEqual(len(spec["blocks"][0]["text"]), 600)
        self.assertTrue(any("at most" in e for e in errors))
        empty, errors = panels.validate_spec({"blocks": []})
        self.assertEqual(empty["blocks"], [])
        self.assertIn("spec has no valid blocks", errors)
        image, _ = panels.validate_spec([{"type": "image", "src": "data:image/png;base64,AAAA"}])
        self.assertEqual(image["blocks"][0]["src"], "data:image/png;base64,AAAA")

    def test_clean_values_keeps_only_bound_keys(self):
        out = panels.clean_values({"temp": 12, "secret": "x", "items": [{"title": "a"}] * 100,
                                   "nan": float("nan")}, ["temp", "items", "nan"])
        self.assertEqual(set(out), {"temp", "items", "nan"})
        self.assertIsNone(out["nan"])
        self.assertEqual(len(out["items"]), panels.MAX_HISTORY)

    def test_cadence_respects_per_kind_floor(self):

        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 5}), {"kind": "interval", "minutes": 5})
        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 5}, kind="custom"), {"kind": "interval", "minutes": 15})
        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 1}, kind="system"), {"kind": "interval", "minutes": 1})
        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 1}, kind="weather"), {"kind": "interval", "minutes": 15})
        self.assertEqual(panels._clean_cadence(0.2, kind="usage"), {"kind": "interval", "minutes": 1})
        self.assertEqual(panels._clean_cadence("30 мин", kind="rss"), {"kind": "interval", "minutes": 30})
        self.assertEqual(panels._clean_cadence("3h", kind="rss"), {"kind": "interval", "minutes": 180})
        self.assertEqual(panels._clean_cadence(None, kind="system"), {"kind": "interval", "minutes": 5})
        self.assertEqual(panels._clean_cadence(15), {"kind": "interval", "minutes": 15})
        self.assertEqual(panels._clean_cadence("7:30"), {"kind": "daily", "at": "07:30"})
        self.assertEqual(panels._clean_cadence({"kind": "daily", "at": "99:00"}), {"kind": "daily", "at": "08:00"})
        self.assertEqual(panels._clean_cadence("manual"), {"kind": "manual"})
        self.assertEqual(panels._clean_cadence(None, kind="countdown"), {"kind": "manual"})
        self.assertIsNone(panels.next_refresh_at({"kind": "manual"}))
        now = 1_800_000_000.0
        self.assertEqual(panels.next_refresh_at({"kind": "interval", "minutes": 180}, now=now), now + 180 * 60)
        daily = panels.next_refresh_at({"kind": "daily", "at": "08:00"}, now=now)
        self.assertTrue(now < daily <= now + 86400 + 3600)

    def test_wall_slugs(self):
        self.assertEqual(panels.Wall("needs_login:Site.RU").status, "needs_login:site.ru")
        self.assertEqual(panels.Wall("weird").status, "blocked:weird")
        self.assertEqual(panels.Wall("hack:x").status, "blocked:x")

class StorageTest(PanelsBase):
    def test_first_load_seeds_builtin_weather_in_setup(self):
        doc = panels.load()
        self.assertTrue(doc["seeded"])
        self.assertEqual(len(doc["panels"]), 1)
        weather = doc["panels"][0]
        self.assertTrue(weather["builtin"])
        self.assertEqual(weather["kind"], "weather")
        self.assertEqual(weather["placements"], ["home", "appbar"])
        self.assertEqual(weather["status"], "setup")
        self.assertEqual(weather["wall"]["status"], "needs_location:weather")
        self.assertTrue((self.root / "panels.json").exists())

        panels.dismiss(weather["id"], purge=True)
        self.assertEqual(panels.load()["panels"], [])

    def test_migrates_legacy_list_and_garbage(self):
        (self.root / "panels.json").write_text(json.dumps([
            {"id": "bad id", "title": "Old", "kind": "nope", "spec": {"blocks": [{"type": "text", "text": "hi"}]},
             "placements": ["home", "moon", "home"], "size": "xl", "cadence": 1, "failures": "x"},
            "garbage", None,
        ]), encoding="utf-8")
        doc = panels.load()
        old = next(p for p in doc["panels"] if p["title"] == "Old")
        self.assertRegex(old["id"], r"^p_[a-z0-9]+$")
        self.assertEqual(old["kind"], "custom")
        self.assertEqual(old["placements"], ["home"])
        self.assertEqual(old["size"], "m")
        self.assertEqual(old["cadence"], {"kind": "interval", "minutes": 15})
        self.assertEqual(old["source"]["type"], "ai")
        self.assertEqual(doc["version"], panels.SCHEMA_VERSION)

        self.assertTrue(any(p["kind"] == "weather" for p in doc["panels"]))

    def test_corrupt_file_is_recovered(self):
        (self.root / "panels.json").write_text("{not json", encoding="utf-8")
        doc = panels.load()
        self.assertTrue(doc["seeded"])

    def test_profiles_get_their_own_file(self):
        pgdata._save("profiles.json", {"active_id": "work", "items": [{"id": "default", "name": "A"}, {"id": "work", "name": "B"}]})
        panels.load()
        self.assertTrue((self.root / "profiles" / "work" / "panels.json").exists())

    def test_create_place_reorder_move_dismiss_revive(self):
        cd = panels.create_panel(template="countdown", params={"title": "NY", "target": "2030-01-01T00:00"},
                                 placements=["home", "sidebar"])
        self.assertEqual(cd["status"], "live")
        self.assertEqual(cd["cadence"], {"kind": "manual"})
        self.assertIn("days_left", cd["data"])
        rss = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, placements=["home"])
        weather = next(p for p in panels.load()["panels"] if p["kind"] == "weather")
        snap = panels.reorder("home", [rss["id"], cd["id"], weather["id"]])
        home = sorted([p for p in snap["panels"] if "home" in p["placements"]], key=lambda p: p["order"]["home"])
        self.assertEqual([p["id"] for p in home], [rss["id"], cd["id"], weather["id"]])
        panels.move(rss["id"], "home", "appbar", 0)
        moved = self.find(rss["id"])
        self.assertEqual(moved["placements"], ["appbar"])
        panels.move(cd["id"], "", "appbar", 0)
        self.assertEqual(self.find(cd["id"])["placements"], ["home", "sidebar", "appbar"])
        self.assertEqual(self.find(cd["id"])["order"]["appbar"], 0)
        panels.dismiss(cd["id"])
        doc = panels.load()
        self.assertNotIn(cd["id"], [p["id"] for p in panels.active(doc)])
        self.assertIn(cd["id"], [p["id"] for p in panels.dismissed(doc)])
        panels.revive(cd["id"])
        self.assertIsNone(self.find(cd["id"])["dismissed_at"])
        with self.assertRaises(panels.PanelError):
            panels.create_panel(template="rss", params={"url": "ftp://x"})
        with self.assertRaises(panels.PanelError):
            panels.create_panel(template="nope")

    def test_dismissed_are_capped(self):
        ids = [panels.create_panel(template="countdown", params={"title": str(i), "target": "2030-01-01T00:00"})["id"]
               for i in range(panels.MAX_DISMISSED + 3)]
        for pid in ids:
            panels.dismiss(pid)
        self.assertEqual(len(panels.dismissed(panels.load())), panels.MAX_DISMISSED)

    def test_update_data_only_bound_keys(self):
        p = panels.create_panel(subject="Indicator", spec={"blocks": [{"type": "metric", "value": "{{v}}"}]})
        out = panels.update_data(p["id"], {"v": 5, "other": 1}, [{"name": "Src", "url": "https://src.example"}])
        self.assertEqual(out["data"], {"v": 5})
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["sources"], [{"name": "Src", "url": "https://src.example"}])
        with self.assertRaises(panels.PanelError):
            panels.update_data(p["id"], {"nope": 1})

    def test_update_panel_edits_structure(self):
        p = panels.create_panel(template="countdown", params={"title": "A", "target": "2030-01-01T00:00"})
        out = panels.update_panel(p["id"], {"title": "Renamed", "size": "l", "placements": ["sidebar", "bogus"],
                                            "cadence": {"kind": "interval", "minutes": 1}})
        self.assertEqual(out["title"], "Renamed")
        self.assertTrue(out["title_custom"])
        self.assertEqual(out["size"], "l")
        self.assertEqual(out["placements"], ["sidebar"])
        with self.assertRaises(panels.PanelError):
            panels.update_panel(p["id"], {"spec": {"blocks": [{"type": "bad"}]}})
        with self.assertRaises(panels.PanelError):
            panels.update_panel("p_doesnotexist", {"title": "x"})

    def test_settings_location_flow(self):
        weather = panels.load()["panels"][0]
        self.assertEqual(weather["status"], "setup")
        settings = panels.update_settings({"location": {"mode": "manual", "name": "Москва", "lat": 55.75, "lon": 37.62},
                                           "units": "imperial", "bogus": 1})
        self.assertEqual(settings["location"]["mode"], "manual")
        self.assertEqual(settings["units"], "imperial")
        self.assertNotIn("bogus", settings)
        weather = panels.load()["panels"][0]
        self.assertEqual(weather["status"], "building")
        self.assertIsNone(weather["wall"])
        panels.update_settings({"location": {"mode": "unset", "lat": None, "lon": None}})
        self.assertEqual(panels.load()["panels"][0]["status"], "setup")

        panels.update_settings({"lang": "en"})
        self.assertEqual(panels.load()["panels"][0]["title"], "Weather")

class RefreshTest(PanelsBase):
    def test_weather_refresh_and_schedule(self):
        net = FakeNet({"api.open-meteo.com/v1/forecast": WEATHER_JSON})
        panels.update_settings({"location": {"mode": "manual", "name": "Копенгаген", "lat": 55.67, "lon": 12.56},
                                "locale": "ru-RU"})
        pid = panels.load()["panels"][0]["id"]
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(pid, force=True, now=1_800_000_000.0))
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["data"]["temp"], 12)
        self.assertEqual(out["data"]["icon"], "rain")
        self.assertEqual(out["data"]["w_unit"], "м/с")
        self.assertEqual(out["data"]["days"][0]["label"], "Сегодня")
        self.assertEqual(out["sources"][0]["name"], "Open-Meteo")
        self.assertEqual(out["next_refresh"], 1_800_000_000.0 + 3600)
        self.assertIn("wind_speed_unit=ms", net.calls[0])
        self.assertIn("latitude=55.67", net.calls[0])
        doc = panels.load()
        self.assertEqual(panels.due_ids(doc, 1_800_000_000.0 + 60), [])
        self.assertEqual(panels.due_ids(doc, 1_800_000_000.0 + 3601), [pid])
        doc["settings"]["enabled"] = False
        self.assertEqual(panels.due_ids(doc, 1_800_000_000.0 + 3601), [])

    def test_weather_imperial_and_rate_limit_wall_backoff(self):
        panels.update_settings({"location": {"mode": "manual", "name": "NYC", "lat": 40.7, "lon": -74.0}, "locale": "en-US"})
        pid = panels.load()["panels"][0]["id"]
        net = FakeNet({"forecast": WEATHER_JSON})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(pid, force=True))
        self.assertIn("temperature_unit=fahrenheit", net.calls[0])
        self.assertEqual(out["data"]["t_unit"], "°F")
        limited = FakeNet({"forecast": {"error": True, "reason": "Daily API request limit exceeded."}})
        now = 1_800_000_000.0
        with patch.object(panels, "_http_text", limited):
            out = self.run_async(panels.refresh_panel(pid, force=True, now=now))
            self.assertEqual(out["wall"]["status"], "blocked:rate-limit")
            self.assertEqual(out["status"], "live")
            first = out["next_refresh"] - now
            out = self.run_async(panels.refresh_panel(pid, force=True, now=now))
            second = out["next_refresh"] - now
        self.assertEqual(out["failures"], 2)
        self.assertGreater(second, first)
        self.assertLessEqual(second, 24 * 3600)

    def test_user_walls_stop_automatic_refresh(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"})
        net = FakeNet({"example.org/feed": ValueError("HTTP 403")})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "needs_login:example.org")
        self.assertEqual(out["status"], "failed")
        self.assertIsNone(out["next_refresh"])
        self.assertNotIn(p["id"], panels.due_ids(panels.load(), time.time() + 10 ** 6))

        with patch.object(panels, "_http_text", FakeNet({"example.org/feed": RSS})):
            out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live")
        self.assertIsNone(out["wall"])

    def test_claim_prevents_double_refresh(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"})
        claimed = panels._claim(p["id"], force=True, now=time.time())
        self.assertIsNotNone(claimed)
        self.assertIsNone(panels._claim(p["id"], force=True, now=time.time()))
        self.assertIsNone(self.run_async(panels.refresh_panel(p["id"], force=True)))

        self.assertIsNotNone(panels._claim(p["id"], force=True, now=time.time() + panels.CLAIM_SECONDS + 1))

    def test_fx_cbr_and_frankfurter(self):
        p = panels.create_panel(template="currency", params={"base": "USD", "quote": "RUB"})
        with patch.object(panels, "_http_text", FakeNet({"cbr-xml-daily": CBR_JSON})):
            out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["data"]["rate"], "81,50")
        self.assertEqual(out["data"]["delta"], "+0,30")
        self.assertEqual(out["data"]["trend"], "up")
        self.assertEqual(out["data"]["hist"], [81.2, 81.5])
        nominal = panels.create_panel(template="currency", params={"base": "JPY", "quote": "RUB"})
        with patch.object(panels, "_http_text", FakeNet({"cbr-xml-daily": CBR_JSON})):
            out = self.run_async(panels.refresh_panel(nominal["id"], force=True))
        self.assertAlmostEqual(out["data"]["rate_num"], 0.54, places=6)
        self.assertEqual(out["data"]["rate"], "0,5400")
        eur = panels.create_panel(template="currency", params={"base": "EUR", "quote": "USD"})
        net = FakeNet({"frankfurter.dev": {"base": "EUR", "date": "2026-10-06", "rates": {"USD": 1.12}}})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(eur["id"], force=True))
        self.assertEqual(out["data"]["rate"], "1,12")
        self.assertIn("from=EUR&to=USD", net.calls[0])
        missing = panels.create_panel(template="currency", params={"base": "XYZ", "quote": "RUB"})
        with patch.object(panels, "_http_text", FakeNet({"cbr-xml-daily": CBR_JSON})):
            out = self.run_async(panels.refresh_panel(missing["id"], force=True))
        self.assertEqual(out["wall"]["status"], "source_gone:cbr")

    def test_crypto_and_stocks(self):
        coin = panels.create_panel(template="crypto", params={"coin": "bitcoin", "vs": "usd"})
        net = FakeNet({"simple/price": {"bitcoin": {"usd": 83539.4, "usd_24h_change": -3.17}},
                       "market_chart": {"prices": [[i, 80000 + i] for i in range(100)]}})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(coin["id"], force=True))
        self.assertEqual(out["data"]["price"], "83\u202f539")
        self.assertEqual(out["data"]["trend"], "down")
        self.assertEqual(out["data"]["symbol"], "BTC")
        self.assertLessEqual(len(out["data"]["hist"]), 26)
        moex = panels.create_panel(template="stock", params={"symbol": "sber", "exchange": "moex"})
        net = FakeNet({"iss.moex.com": {"marketdata": {"data": [["SBER", 283.86, 0.58, "15:07:02"]]},
                                        "securities": {"data": [["SBER", "Сбербанк", 282.22]]}}})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(moex["id"], force=True))
        self.assertEqual(out["data"]["name"], "Сбербанк")
        self.assertEqual(out["data"]["currency_sign"], "₽")
        self.assertEqual(out["data"]["hist"], [282.22, 283.86])
        us = panels.create_panel(template="stock", params={"symbol": "AAPL", "exchange": "us"})
        net = FakeNet({"finance.yahoo.com": {"chart": {"result": [{"meta": {"regularMarketPrice": 333.63, "chartPreviousClose": 330.0,
                       "currency": "USD", "longName": "Apple Inc."}, "indicators": {"quote": [{"close": [330, None, 333]}]}}]}}})
        with patch.object(panels, "_http_text", net):
            out = self.run_async(panels.refresh_panel(us["id"], force=True))
        self.assertEqual(out["data"]["currency_sign"], "$")
        self.assertEqual(out["data"]["trend"], "up")
        self.assertEqual(out["data"]["hist"], [330.0, 333.0])

    def test_rss_and_atom(self):
        title, items = panels.parse_feed(RSS)
        self.assertEqual(title, "Лента")
        self.assertEqual(items[0]["url"], "https://example.org/1")
        self.assertEqual(items[1]["title"], "Вторая новость")
        self.assertEqual(items[1]["url"], "")
        title, items = panels.parse_feed(ATOM)
        self.assertEqual((title, items[0]["url"]), ("Atom feed", "https://example.org/a"))
        with self.assertRaises(panels.Wall):
            panels.parse_feed("<html><body>not a feed")

    def test_countdown_refresh_needs_no_network(self):
        p = panels.create_panel(template="countdown", params={"title": "NY", "target": "2031-01-01T00:00"})
        with patch.object(panels, "_http_text", FakeNet({})) as net:
            out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(net.calls, [])
        self.assertGreater(out["data"]["days_left"], 0)

    def test_ip_location_only_on_explicit_request(self):
        net = FakeNet({"ipwho.is": {"success": True, "city": "Dublin", "latitude": 53.3, "longitude": -6.2,
                                    "country": "Ireland", "timezone": {"id": "Europe/Dublin"}},
                       "forecast": WEATHER_JSON})
        with patch.object(panels, "_http_text", net):
            panels.load()
            self.run_async(panels.tick())
            self.assertEqual(net.calls, [])
            settings = self.run_async(panels.locate_by_ip())
        self.assertEqual(settings["location"]["mode"], "auto")
        self.assertEqual(settings["location"]["name"], "Dublin")

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
    return {"results": [{"title": "Zenit news", "url": "https://sport.example/zenit", "snippet": "Зенит — Спартак 2:1"}],
            "answer": None, "pages": [{"url": "https://sport.example/zenit", "excerpt": "Зенит победил 2:1"}]}

async def fake_fetch(url, limit=12000):
    if "login" in url:
        raise ValueError("HTTP 401")
    if "guarded" in url:
        raise ValueError("HTTP 403")
    return {"url": url, "title": "Shop", "text": "Цена: 1 990 ₽"}

class AiTest(PanelsBase):
    def ctx(self, model):
        panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, fetch=fake_fetch,
                                               default_model=lambda: "default-model"))

    def test_ai_refresh_keeps_only_bound_keys_and_cites(self):
        model = FakeModel([{"values": {"score": "2:1", "invented": "x"},
                            "sources": [{"name": "Sport", "url": "https://sport.example/zenit"}, {"url": "javascript:x"}]}])
        self.ctx(model)
        p = panels.create_panel(template="sports", params={"team": "Зенит"}, model="chat-model")
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["data"], {"score": "2:1"})

        self.assertEqual([s["url"] for s in out["sources"]], ["https://sport.example/zenit"])
        used_model, messages = model.prompts[0]
        self.assertEqual(used_model, "chat-model")
        self.assertIn("Never invent", messages[0]["content"])
        self.assertIn("Зенит — Спартак 2:1", messages[1]["content"])
        self.assertIn('"headline"', messages[1]["content"])

    def test_ai_refresh_without_values_is_a_wall(self):
        self.ctx(FakeModel([{"values": {}}]))
        p = panels.create_panel(template="sports", params={"team": "Зенит"})
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["wall"]["status"], "blocked:not-found")

    def test_model_reported_wall_and_page_login(self):
        self.ctx(FakeModel([{"values": {}, "wall": {"status": "paywall:news.example", "message": "subscribers only"}}]))
        p = panels.create_panel(template="web", params={"url": "https://news.example/a", "what": "price"})
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "paywall:news.example")
        self.assertIsNone(out["next_refresh"])
        q = panels.create_panel(template="web", params={"url": "https://shop.example/login", "what": "price"})
        out = self.run_async(panels.refresh_panel(q["id"], force=True))
        self.assertEqual(out["wall"]["status"], "needs_login:shop.example")

    def test_no_proxy_or_model(self):
        panels.set_context(panels.PanelContext(search=fake_search, fetch=fake_fetch))
        p = panels.create_panel(template="web", params={"url": "https://shop.example/item", "what": "price"})
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "blocked:proxy-offline")

    def test_compose_from_brief(self):
        model = FakeModel(["Here you go:\n```json\n" + json.dumps({
            "title": "Tech", "spec": {"blocks": [{"type": "title", "text": "Tech"}, {"type": "list", "items": "{{news}}"},
                                               {"type": "evil", "x": 1}]},
            "values": {"news": [{"title": "A", "url": "https://a.example"}], "zzz": 1},
            "runbook": {"prompt": "search tech news", "queries": ["tech news today"]},
            "cadence": {"kind": "interval", "minutes": 10}, "rhythm": "daily-ish"}) + "\n```"])
        self.ctx(model)
        p = panels.create_panel(subject="Nasdaq composite level and its change")
        self.assertEqual(p["status"], "building")
        self.assertEqual(p["spec"]["blocks"], [])
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["title"], "Tech")
        self.assertEqual([b["type"] for b in out["spec"]["blocks"]], ["title", "list"])
        self.assertEqual(list(out["data"]), ["news"])
        self.assertEqual(out["cadence"], {"kind": "interval", "minutes": 15})
        self.assertEqual(out["runbook"]["queries"], ["tech news today"])
        self.assertEqual(out["sources"], [])
        self.assertNotIn("url", out["data"]["news"][0])
        self.assertIn("SLOWEST honest", model.prompts[0][1][0]["content"])

    def test_bad_model_json(self):
        self.ctx(FakeModel(["I cannot do that"]))
        p = panels.create_panel(subject="Something")
        out = self.run_async(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "blocked:model-format")
        self.assertEqual(out["status"], "failed")

class ToolsTest(PanelsBase):
    def test_tool_round_trip(self):
        model = FakeModel([{"values": {"rate": "90"}, "sources": [{"name": "X", "url": "https://x.example"}]}])
        panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, fetch=fake_fetch))

        async def scenario():
            created = await panels.run_tool("panel_create", {
                "subject": "Gold price", "placement": "sidebar,appbar", "size": "s", "title": "Gold",
                "blocks": [{"type": "metric", "value": "{{rate}}", "unit": "$"}],
                "runbook": {"queries": ["gold price"]}, "cadence": {"kind": "interval", "minutes": 120}}, model="m1")

            self.assertEqual(created["status"], "live")
            self.assertIn("ready and filled", created["note"])
            pid = created["panel"]["id"]
            self.assertEqual(created["panel"]["placements"], ["sidebar", "appbar"])
            for _ in range(50):
                await asyncio.sleep(0.02)
                if pid not in panels._TASKS:
                    break
            listed = await panels.run_tool("panel_list", {})
            row = next(r for r in listed["panels"] if r["id"] == pid)
            self.assertEqual(row["status"], "live")
            read = await panels.run_tool("panel_read", {"id": pid})
            self.assertEqual(read["panel"]["data"], {"rate": "90"})
            upd = await panels.run_tool("panel_update_data", {"id": pid, "values": {"rate": "91"}})
            self.assertNotIn("error", upd)
            edit = await panels.run_tool("panel_edit", {"id": pid, "title": "Золото", "placements": "home"})
            self.assertEqual(edit["panel"]["placements"], ["home"])
            removed = await panels.run_tool("panel_remove", {"id": pid})
            self.assertEqual(removed["removed"], pid)
            revived = await panels.run_tool("panel_revive", {"id": pid})
            self.assertEqual(revived["panel"]["id"], pid)
            missing = await panels.run_tool("panel_read", {"id": "p_nonexistent"})
            self.assertIn("error", missing)
            empty = await panels.run_tool("panel_create", {})
            self.assertIn("error", empty)
            tpl = await panels.run_tool("panel_create", {"template": "countdown", "params": {"title": "X", "target": "2031-05-01T10:00"}})
            self.assertEqual(tpl["status"], "live")
            templates = await panels.run_tool("panel_templates", {})
            self.assertIn("weather", [t["kind"] for t in templates["templates"]])

        asyncio.run(scenario())

    def test_playground_tool_route(self):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        with TestClient(create_app(), base_url="http://127.0.0.1") as client:
            resp = client.post("/api/pg/tools", json={"tool": "panel_list", "args": {}})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["panels"][0]["kind"], "weather")
            bad = client.post("/api/pg/tools", json={"tool": "panel_hack", "args": {}})
            self.assertIn("error", bad.json())

class RoutesTest(PanelsBase):
    def test_http_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.panels_api import register_panel_routes
        app = FastAPI()
        register_panel_routes(app)
        with TestClient(app) as client:
            snap = client.get("/api/pg/panels").json()
            self.assertEqual(len(snap["panels"]), 1)
            self.assertIn("templates", snap)
            created = client.post("/api/pg/panels", json={"template": "countdown", "placements": ["sidebar"],
                                                          "params": {"title": "Trip", "target": "2031-01-01T00:00"}})
            self.assertEqual(created.status_code, 200)
            pid = created.json()["panel"]["id"]
            self.assertEqual(client.get(f"/api/pg/panels/{pid}").json()["panel"]["title"], "Trip")
            bad = client.post("/api/pg/panels", json={"template": "rss", "params": {"url": ""}})
            self.assertEqual(bad.status_code, 400)
            patched = client.patch(f"/api/pg/panels/{pid}", json={"size": "l"}).json()["panel"]
            self.assertEqual(patched["size"], "l")
            order = client.post("/api/pg/panels-order", json={"id": pid, "from": "sidebar", "to": "home", "index": 0}).json()
            moved = next(p for p in order["panels"] if p["id"] == pid)
            self.assertEqual(moved["placements"], ["home"])
            self.assertEqual(moved["order"]["home"], 0)
            refreshed = client.post(f"/api/pg/panels/{pid}/refresh", json={"wait": 5}).json()["panel"]
            self.assertEqual(refreshed["status"], "live")
            self.assertEqual(client.delete(f"/api/pg/panels/{pid}").json(), {"ok": True})
            self.assertIn(pid, [p["id"] for p in client.get("/api/pg/panels").json()["dismissed"]])
            self.assertEqual(client.post(f"/api/pg/panels/{pid}/revive").status_code, 200)
            self.assertEqual(client.get("/api/pg/panels/p_missing00").status_code, 404)
            settings = client.post("/api/pg/panels-settings", json={"units": "metric", "appbar_max": 99}).json()["settings"]
            self.assertEqual((settings["units"], settings["appbar_max"]), ("metric", 8))
            with patch.object(panels, "_http_text", FakeNet({"geocoding-api": {"results": [
                    {"name": "Копенгаген", "latitude": 55.6, "longitude": 12.5, "country": "Дания"}]}})):
                geo = client.get("/api/pg/panels-geocode", params={"q": "Копен"}).json()
            self.assertEqual(geo["results"][0]["name"], "Копенгаген")

if __name__ == "__main__":
    unittest.main()
