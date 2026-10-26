"""Round 25 (b5): panels refresh races and module host fixes.

* An edit made while a refresh is in flight is not rolled back by that refresh (own title, «Что
  показывать»), and new parameters trigger an immediate re-run instead of the stale result.
* A refresh cancelled by closing the app or removing the panel is not a source failure (no wall, no
  back-off); a panel purged mid-refresh does not crash the task.
* «Manual» cadence stays manual after a failure once the panel has data.
* Removing a panel from a worker thread (the HTTP route uses ``to_thread``) really cancels its refresh.
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import panels, pgdata

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Лента</title>
<item><title>Первая</title><link>https://example.org/1</link></item>
<item><title>Вторая</title><link>https://example.org/2</link></item></channel></rss>"""

class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        panels._TASKS.clear()
        panels._RERUN.clear()
        self.saved_loop = panels._LOOP

    def tearDown(self) -> None:
        self.patch.stop()
        panels._LOOP = self.saved_loop
        panels._TASKS.clear()
        panels._RERUN.clear()
        self.tmp.cleanup()

    def find(self, pid):
        return next(p for p in panels.load()["panels"] if p["id"] == pid)

class GatedNet:
    """``_http_text`` stand-in: the first call waits for ``gate``; every call is recorded."""

    def __init__(self):
        self.calls = []
        self.gate = None
        self.entered = None

    async def __call__(self, url, *, limit=0):
        self.calls.append(url)
        if len(self.calls) == 1:
            self.entered.set()
            await self.gate.wait()
        return RSS

class EditDuringRefreshTest(Base):
    def test_title_and_options_edited_mid_refresh_survive(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False)
        net = GatedNet()

        async def scenario():
            net.gate, net.entered = asyncio.Event(), asyncio.Event()
            panels.bind_loop()
            panels.start_refresh(p["id"])
            await net.entered.wait()

            await asyncio.to_thread(panels.update_panel, p["id"], {"title": "Моя лента",
                                                                    "options": {"show_time": False}})
            net.gate.set()
            await panels.wait_refresh(p["id"], 5)

        with patch.object(panels, "_http_text", net):
            asyncio.run(scenario())
        out = self.find(p["id"])
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["title"], "Моя лента")
        self.assertIs(out["options"]["show_time"], False)
        self.assertEqual(len(net.calls), 1, "a display-only change needs no second fetch")

    def test_new_params_mid_refresh_rerun_instead_of_stale_values(self):
        p = panels.create_panel(template="rss", params={"url": "https://old.example/feed"}, start=False)
        net = GatedNet()

        async def scenario():
            net.gate, net.entered = asyncio.Event(), asyncio.Event()
            panels.bind_loop()
            panels.start_refresh(p["id"])
            await net.entered.wait()
            await asyncio.to_thread(panels.update_panel, p["id"], {"params": {"url": "https://new.example/feed"}})
            net.gate.set()
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(net.calls) >= 2 and not panels.pid_busy(p["id"]):
                    break

        with patch.object(panels, "_http_text", net):
            asyncio.run(scenario())
        self.assertEqual(len(net.calls), 2, net.calls)
        self.assertIn("new.example", net.calls[1])
        out = self.find(p["id"])
        self.assertEqual(out["status"], "live")
        self.assertIsNone(out["refreshing_until"])
        self.assertNotIn(p["id"], panels._RERUN)

    def test_refresh_button_mid_refresh_does_not_discard_it(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False)
        net = GatedNet()

        async def scenario():
            net.gate, net.entered = asyncio.Event(), asyncio.Event()
            panels.bind_loop()
            panels.start_refresh(p["id"])
            await net.entered.wait()
            panels.start_refresh(p["id"], force=True)
            net.gate.set()
            await panels.wait_refresh(p["id"], 5)
            await asyncio.sleep(0.05)

        with patch.object(panels, "_http_text", net):
            asyncio.run(scenario())
        self.assertEqual(len(net.calls), 1)
        self.assertEqual(self.find(p["id"])["status"], "live")

    def test_countdown_keeps_its_own_title(self):
        p = panels.create_panel(template="countdown", params={"target": "2031-01-01T00:00", "name": "НГ"}, start=False)
        panels.update_panel(p["id"], {"title": "До отпуска"})
        out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["title"], "До отпуска")

class CancelAndRemoveTest(Base):
    def test_cancelled_build_is_not_a_failure(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False)
        net = GatedNet()

        async def scenario():
            net.gate, net.entered = asyncio.Event(), asyncio.Event()
            panels.bind_loop()
            panels.start_refresh(p["id"])
            await net.entered.wait()
            task = panels._TASKS[p["id"]]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with patch.object(panels, "_http_text", net):
            asyncio.run(scenario())
        out = self.find(p["id"])
        self.assertEqual(out["status"], "building")
        self.assertIsNone(out["wall"])
        self.assertEqual(out["failures"], 0)
        self.assertIsNone(out["refreshing_until"])
        self.assertIn(p["id"], panels.due_ids(panels.load(), time.time()), "the build resumes on the next start")

    def test_purge_from_worker_thread_cancels_refresh_cleanly(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False)
        net = GatedNet()
        errors = []

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx))
            net.gate, net.entered = asyncio.Event(), asyncio.Event()
            panels.bind_loop()
            panels.start_refresh(p["id"])
            task = panels._TASKS[p["id"]]
            await net.entered.wait()
            await asyncio.to_thread(panels.dismiss, p["id"], purge=True)
            done, _ = await asyncio.wait({task}, timeout=2)
            self.assertIn(task, done, "the refresh of a removed panel kept running")
            self.assertTrue(task.cancelled())

        with patch.object(panels, "_http_text", net), self.assertLogs("memuai.panels", "INFO") as logs:
            panels.log.info("start")
            asyncio.run(scenario())
        self.assertEqual(errors, [])
        self.assertFalse([line for line in logs.output if "crashed" in line], logs.output)
        self.assertFalse(any(x["id"] == p["id"] for x in panels.load()["panels"]))

    def test_finish_of_a_purged_panel_returns_none(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False)
        panels.dismiss(p["id"], purge=True)
        self.assertIsNone(panels._finish(p["id"], values={"x": 1}))

class ManualCadenceTest(Base):
    def test_failure_keeps_manual_panel_manual(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False,
                                cadence="manual")
        with patch.object(panels, "_http_text", GatedNetOk()):
            out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "live")
        self.assertIsNone(out["next_refresh"])

        async def down(url, *, limit=0):
            raise ValueError("HTTP 503")

        with patch.object(panels, "_http_text", down):
            out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["wall"]["status"], "blocked:source-down")
        self.assertIsNone(out["next_refresh"], "a manual panel must not start refreshing by itself after a failure")

    def test_failed_first_build_of_manual_panel_still_retries(self):
        p = panels.create_panel(template="rss", params={"url": "https://example.org/feed"}, start=False,
                                cadence="manual")

        async def down(url, *, limit=0):
            raise ValueError("HTTP 503")

        with patch.object(panels, "_http_text", down):
            out = asyncio.run(panels.refresh_panel(p["id"], force=True))
        self.assertEqual(out["status"], "failed")
        self.assertIsNotNone(out["next_refresh"])

class GatedNetOk:
    async def __call__(self, url, *, limit=0):
        return RSS

@unittest.skipUnless(__import__("tests.panels_browser_test", fromlist=["_chromium"])._chromium(), "Chromium is not installed")
class PanelsLayoutBrowserTest(unittest.TestCase):
    """A small card with a wall or a location request: text gets a line of its own, buttons grow with
    their label; a clock without seconds is redrawn by the minute number, not by waiting for «:00»."""

    def test_small_cards_and_clock(self):
        import json
        from playwright.sync_api import sync_playwright
        from tests import panels_browser_test as base
        clock_title, clock_spec = panels.typed_spec("clock", {}, panels.default_settings(), None)
        snap = {
            "panels": [
                base.panel("p_failed0001", "custom", "Курс", [], {}, ["home"], status="failed", size="s",
                           wall={"status": "blocked:source-down", "message": "HTTP 503"}),
                base.panel("p_setup00001", "weather", "Погода", [], {}, ["home"], status="setup", size="s",
                           wall={"status": "needs_location:weather", "message": ""}),
                base.panel("p_clock00001", "clock", clock_title, clock_spec["blocks"], {"local": True}, ["home"], size="s",
                           source={"type": "local", "params": {}}),
            ],
            "dismissed": [], "templates": [], "settings": base.SNAPSHOT["settings"],
        }
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=base._chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1300, "height": 900})
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
            page.wait_for_selector('#panels-home [data-panel-id="p_clock00001"] [data-mp-clock]', timeout=5000)
            wall = page.evaluate("""() => {
                const w = document.querySelector('[data-panel-id="p_failed0001"] .mp-wall');
                const text = w.querySelector(':scope > span:not(.mp-ico)'), btn = w.querySelector('button');
                return {wall: w.clientWidth, text: text.getBoundingClientRect().width,
                        textBottom: text.getBoundingClientRect().bottom, btnTop: btn.getBoundingClientRect().top,
                        icon: w.querySelector('.mp-ico').getBoundingClientRect().width};
            }""")
            self.assertGreater(wall["text"], wall["wall"] * 0.6, wall)
            self.assertGreaterEqual(wall["btnTop"], wall["textBottom"] - 1, "«Повторить» goes under the text")
            self.assertLess(wall["icon"], 24, "the warning icon keeps its size")
            overflow = page.evaluate("""() => [...document.querySelectorAll('[data-panel-id="p_setup00001"] .mp-btn')]
                .filter(b => b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1).length""")
            self.assertEqual(overflow, 0)

            page.evaluate("""() => { const el = document.querySelector('[data-panel-id="p_clock00001"] [data-mp-clock]');
                el.dataset.drawn = String(Math.floor(Date.now() / 60000) - 1); el.innerHTML = '<i id="stale"></i>'; }""")
            page.wait_for_function("""() => !document.getElementById('stale')""", timeout=2500)
            browser.close()
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
