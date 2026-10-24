"""Round22 panels: extended cadences (1 min … daily) with per-kind floors and the scheduler, rate-limit
back-off, «PC performance» (pure build from two samples + the panel), creation that fills immediately,
and source binding for AI panels (only cited / evidenced links, never «250 best movies»)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import panels, panels_system, pgdata


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        panels._TASKS.clear()
        self.saved = panels.context()

    def tearDown(self):
        self.patch.stop()
        panels.set_context(self.saved)
        panels._TASKS.clear()
        self.tmp.cleanup()

    def find(self, pid):
        return next(p for p in panels.load()["panels"] if p["id"] == pid)


class CadenceTest(Base):
    def test_presets_and_floors(self):
        self.assertEqual(panels.CADENCE_PRESETS, (1, 5, 15, 30, 60, 180, 360, 720, 1440))
        for kind, floor in (("system", 1), ("usage", 1), ("crypto", 1), ("rss", 5), ("weather", 15), ("custom", 15),
                            ("news", 15), ("countdown", 60)):
            self.assertEqual(panels.floor_minutes(kind), floor, kind)
            self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 1}, kind=kind)["minutes"], floor)
        self.assertEqual(panels._clean_cadence({"kind": "interval", "minutes": 99999}, kind="rss")["minutes"], panels.MAX_INTERVAL_MINUTES)

    def test_one_minute_panels_are_due_every_minute(self):
        p = panels.create_panel(template="crypto", params={"coin": "bitcoin"}, cadence={"kind": "interval", "minutes": 1}, start=False)
        now = 1_800_000_000.0

        def stamp(d):
            for row in d["panels"]:
                if row["id"] == p["id"]:
                    row["status"], row["data"], row["last_refresh"] = "live", {"price": 1}, now
                    row["next_refresh"] = panels.next_refresh_at(row["cadence"], now=now)

        panels.mutate(stamp)
        doc = panels.load()
        self.assertNotIn(p["id"], panels.due_ids(doc, now + 30))
        self.assertIn(p["id"], panels.due_ids(doc, now + 61))
        self.assertLessEqual(panels.LOOP_SECONDS, 30, "the loop must be able to honour a 1-minute cadence")

    def test_edit_cadence_reschedules_from_the_last_refresh(self):
        p = panels.create_panel(template="countdown", params={"title": "X", "target": "2031-01-01T00:00"})
        out = panels.update_panel(p["id"], {"cadence": {"kind": "interval", "minutes": 5}})
        self.assertEqual(out["cadence"], {"kind": "interval", "minutes": 60}, "countdown floor")
        q = panels.create_panel(template="rss", params={"url": "https://e.example/rss"}, start=False)
        out = panels.update_panel(q["id"], {"cadence": "15 мин"})
        self.assertEqual(out["cadence"], {"kind": "interval", "minutes": 15})

    def test_rate_limit_backs_off_by_retry_after(self):
        p = panels.create_panel(template="crypto", params={"coin": "bitcoin"}, cadence={"kind": "interval", "minutes": 1}, start=False)
        now = 1_800_000_000.0
        out = panels._finish(p["id"], wall=panels.Wall("blocked:rate-limit", "429", retry_after=900), now=now)
        self.assertGreaterEqual(out["next_refresh"] - now, 900)
        out = panels._finish(p["id"], wall=panels.Wall("blocked:rate-limit", "429", retry_after=900), now=now)
        self.assertGreaterEqual(out["next_refresh"] - now, 1800, "exponential on repeated refusals")
        out = panels._finish(p["id"], wall=panels.Wall("blocked:source-down", "x"), now=now)
        self.assertLessEqual(out["next_refresh"] - now, 24 * 3600)


class SystemTest(Base):
    def test_build_from_two_samples(self):
        prev = {"t": 0.0, "cpu": (100.0, 400.0), "net": (1000.0, 500.0), "disk_io": (0.0, 0.0)}
        cur = {"t": 2.0, "cpu": (130.0, 500.0), "mem": {"total": 16 * 2 ** 30, "used": 4 * 2 ** 30},
               "net": (3048.0, 1524.0), "disk_io": (2 * 2 ** 20, 0.0),
               "disks": [{"mount": "C:", "total": 500e9, "used": 125e9}], "battery": None, "temp": 54.0, "uptime": 99.0}
        out = panels_system.build(prev, cur, 2.0, lang="ru")
        self.assertEqual(out["cpu"], 70.0)          # 1 - 30/100
        self.assertEqual(out["ram"], 25.0)
        self.assertEqual(out["ram_caption"], "4 ГБ / 16 ГБ")
        self.assertEqual(out["net_down"], "1 КБ/с")
        self.assertEqual(out["disk_read"], "1 МБ/с")
        self.assertEqual(out["disk"], 25.0)
        self.assertEqual(out["disk_label"], "Диск C:")
        self.assertEqual(out["temp_text"], "54°")
        self.assertEqual(out["battery_text"], "", "no battery on a desktop — hidden, not «unknown»")
        unknown = panels_system.build({"t": 0}, {"t": 1}, 1.0, lang="en")
        self.assertIsNone(unknown["cpu"])
        self.assertEqual(unknown["cpu_text"], "—")
        self.assertEqual(unknown["net_down"], "—")

    def test_live_sample_on_this_machine(self):
        out = panels_system.sample(lang="en")
        self.assertIn("cpu", out)
        if out["cpu"] is not None:
            self.assertTrue(0 <= out["cpu"] <= 100)

    def test_system_panel_fills_at_creation(self):
        panel = asyncio.run(panels.create_and_fill(template="system", placements=["home", "appbar"], wait=10))
        self.assertEqual(panel["status"], "live")
        self.assertEqual(panel["title"], "Производительность ПК")
        self.assertIn("cpu_text", panel["data"])
        self.assertIn("disk_rw", panel["data"])
        self.assertEqual(panel["spec"]["mini"]["value"], "{{cpu_text}}")
        self.assertEqual(panel["sources"], [])
        types = [b["type"] for b in panel["spec"]["blocks"]]
        self.assertIn("row", types)
        self.assertEqual([c["type"] for c in panel["spec"]["blocks"][1]["blocks"]], ["gauge"] * 4)


async def fake_search(query, limit=5):
    return {"results": [
        {"title": "250 лучших фильмов", "url": "https://kino.example/top250", "snippet": "Рейтинг фильмов 9.1"},
        {"title": "Интернет-магазин электроники", "url": "https://shop.example/", "snippet": "Скидки до 50%"},
        {"title": "Курс золота сегодня", "url": "https://gold.example/price", "snippet": "Золото: 8 712,40 ₽ за грамм"},
    ], "answer": None, "pages": []}


class FakeModel:
    def __init__(self, answer):
        self.answer = answer

    async def complete(self, model, messages):
        return json.dumps(self.answer, ensure_ascii=False)


async def failing_fetch(url, limit=12000):
    raise ValueError("HTTP 403 Forbidden")


class SourcesTest(Base):
    def test_only_evidenced_sources_reach_the_card(self):
        model = FakeModel({"values": {"value": "8 712,40", "unit": "₽/г"}})
        panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, default_model=lambda: "m"))
        p = panels.create_panel(template="web", params={"url": "https://gold.example/price", "what": "Курс золота"}, start=False)
        panels.update_panel(p["id"], {"runbook": {"prompt": "x", "queries": ["курс золота"], "urls": []}})
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live")
        self.assertEqual([s["url"] for s in out["sources"]], ["https://gold.example/price"])

    def test_cited_but_unseen_urls_are_dropped(self):
        model = FakeModel({"values": {"value": "1"}, "evidence": {"value": "https://made-up.example/x"}})
        panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, default_model=lambda: "m"))
        p = panels.create_panel(template="web", params={"url": "https://gold.example/price", "what": "x"}, start=False)
        panels.update_panel(p["id"], {"runbook": {"prompt": "x", "queries": ["q"], "urls": []}})
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertFalse(any("made-up" in s["url"] or "kino" in s["url"] or "shop" in s["url"] for s in out["sources"]))

    def test_one_blocked_page_is_not_a_wall_when_search_works(self):
        model = FakeModel({"values": {"value": "8 712,40"}, "evidence": {"value": "https://gold.example/price"}})
        panels.set_context(panels.PanelContext(complete=model.complete, search=fake_search, fetch=failing_fetch,
                                               default_model=lambda: "m"))
        p = panels.create_panel(template="web", params={"url": "https://guarded.example/a", "what": "Курс золота"}, start=False)
        panels.update_panel(p["id"], {"runbook": {"prompt": "x", "queries": ["курс золота"], "urls": ["https://guarded.example/a"]}})
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live", out.get("wall"))
        self.assertIsNone(out["wall"])

    def test_creation_waits_for_the_first_fill_and_reports_progress(self):
        seen = []

        async def slow_search(query, limit=5):
            pid = next(iter(panels._TASKS))
            seen.append(panels.public_panel(self.find(pid)).get("progress"))
            return await fake_search(query, limit)

        model = FakeModel({"values": {"score": "2:1", "headline": "Зенит — Спартак"}})
        panels.set_context(panels.PanelContext(complete=model.complete, search=slow_search, default_model=lambda: "m"))
        panel = asyncio.run(panels.create_and_fill(template="sports", params={"team": "Зенит"}, wait=10))
        self.assertEqual(panel["status"], "live")
        self.assertTrue(seen and seen[0] and seen[0]["stage"] in ("search", "fetch"))
        self.assertNotIn(panel["id"], panels._PROGRESS, "progress is cleared when the refresh ends")


class RoutesTest(Base):
    def test_new_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.panels_api import register_panel_routes
        app = FastAPI()
        register_panel_routes(app)
        with TestClient(app) as client:
            live = client.get("/api/pg/panels-system?lang=en").json()
            self.assertIn("cpu_text", live["values"])
            made = client.post("/api/pg/panels", json={"template": "system", "placements": ["home"]}).json()["panel"]
            self.assertEqual(made["status"], "live")
            self.assertEqual(made["floor_minutes"], 1)
            snap = client.get("/api/pg/panels").json()
            self.assertEqual(snap["limits"]["presets"], list(panels.CADENCE_PRESETS))
            self.assertEqual(snap["limits"]["floors"]["news"], 15)
            det = client.get("/api/pg/panels-usage/detect").json()
            self.assertIn("claude", det["detected"])
            bad = client.post("/api/pg/panels-usage/secret", json={"id": "a_123456", "token": "x"})
            self.assertEqual(bad.status_code, 400)
            # This offline test must not discover a real running Antigravity IDE.
            with patch("app.panels_usage.antigravity_endpoints", return_value=[]):
                test = client.post("/api/pg/panels-usage/test", json={"provider": "antigravity", "mode": "dir",
                                                                      "dir": self.tmp.name}).json()
            self.assertEqual(test["account"]["state"], "wall")
            news = client.post("/api/pg/panels", json={"template": "news", "params": {"topic": "ai"}, "wait": 0.1}).json()["panel"]
            self.assertEqual(news["kind"], "news")
            self.assertNotIn("feed", news)
            reset = client.post(f"/api/pg/panels/{news['id']}/feed-reset").json()
            self.assertEqual(reset["panel"]["id"], news["id"])
            no = client.post(f"/api/pg/panels/{made['id']}/feed-reset")
            self.assertEqual(no.status_code, 400)


class PackagingTest(unittest.TestCase):
    """Модули панелей импортируются лениво (внутри функций) — сборка должна назвать их явно,
    а psutil (необязательный, но улучшает «Производительность ПК») — стоять в requirements и hiddenimports."""

    def test_build_and_spec_name_the_lazy_panel_modules(self):
        root = Path(__file__).resolve().parents[1]
        build = (root / "build.py").read_text(encoding="utf-8")
        spec = (root / "MemuAI.spec").read_text(encoding="utf-8")
        for mod in ("app.panels_curate", "app.panels_usage", "app.panels_system", "psutil"):
            self.assertIn(f'"{mod}"', build, mod)
            self.assertIn(f"'{mod}'", spec, mod)
        reqs = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("psutil") for line in reqs))


if __name__ == "__main__":
    unittest.main()
