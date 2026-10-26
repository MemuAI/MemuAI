"""Round 23 panels in headless Chromium: dense layout without holes, Tab autocomplete of ideas,
To-Do inside the card (add / toggle / delete by keyboard), custom cadence, per-panel model, content options.

Uses the fixture page of panels_browser_test; the panel API is mocked in Python (an in-memory To-Do store).
Skipped when Playwright or a Chromium build is not installed.
"""
from __future__ import annotations

import json
import re
import time
import unittest

from tests.panels_browser_test import FIXTURE, ORIGIN, STATIC, _chromium, panel

NOW = time.time()
SCHEMA = None

def schema():
    global SCHEMA
    if SCHEMA is None:
        from app import panels_options
        SCHEMA = json.loads(json.dumps(panels_options.public_schema()))
    return SCHEMA

def todo_item(iid, title, **extra):
    out = {"id": iid, "title": title, "done": False, "done_at": None, "created": NOW, "updated": NOW, "due": "", "priority": 0,
           "tags": [], "notes": "", "subtasks": [], "repeat": None, "remind": "", "reminder": {}, "completed_count": 0}
    out.update(extra)
    return out

def snapshot(store):
    tall_items = [{"title": f"Story {i} with a fairly long headline about a release", "url": f"https://n{i}.example/", "source": "Src",
                   "ts": NOW - i * 3600, "summary": "Summary.", "tone": "ok"} for i in range(10)]
    lists = store["lists"]
    return {
        "panels": [
            panel("p_weather001", "weather", "Погода", [{"type": "title", "text": "Рыбинск", "subtitle": "Ясно"},
                                                       {"type": "metric", "value": "{{temp}}", "unit": "°C", "icon": "clear"}],
                  {"temp": 7}, ["home"], size="m", source={"type": "open-meteo", "params": {"city": "Рыбинск"}},
                  options={"fields": ["feels", "hilo"], "forecast": "auto"}),
            panel("p_news000001", "news", "Новости", [{"type": "title", "text": "Новости"},
                                                     {"type": "list", "items": "{{items}}", "max": 10, "style": "feed", "summary": "always"}],
                  {"items": tall_items}, ["home"], size="m", source={"type": "feed", "params": {"topic": "ai"}}),
            panel("p_stock00001", "stock", "SBER", [{"type": "title", "text": "Сбербанк"}, {"type": "metric", "value": "279,5", "unit": "₽"},
                                                    {"type": "sparkline", "points": "{{hist}}"}],
                  {"hist": [1, 2, 3, 2, 4]}, ["home"], size="m", source={"type": "stock", "params": {"symbol": "SBER"}}),
            panel("p_todo000001", "todo", "Задачи", [{"type": "title", "text": "{{panel_title}}", "subtitle": "{{todo_subtitle}}", "icon": "todo"},
                                                    {"type": "todo", "quick_add": True}],
                  {"local": True}, ["home"], size="m", source={"type": "local", "params": {}}, cadence={"kind": "manual"},
                  options={"lists": [lists[0]["id"]], "filter": "all", "sort": "manual", "show_done": False, "quick_add": True},
                  todo={"lists": lists, "all": [{"id": l["id"], "name": l["name"], "color": "accent"} for l in lists]}),
            panel("p_custom0001", "custom", "Своя", [{"type": "metric", "value": "1", "unit": "g"}], {"x": 1}, ["home"], size="s",
                  source={"type": "ai", "params": {}}),
            panel("p_sys0000001", "system", "ПК", [{"type": "title", "text": "ПК"}, {"type": "gauge", "value": "{{cpu}}", "label": "CPU"}],
                  {"cpu": 40}, ["home"], size="s", source={"type": "system", "params": {}},
                  options={"metrics": ["cpu", "ram", "gpu"], "style": "rings", "live": "off"}),
        ],
        "dismissed": [], "templates": [],
        "settings": {"enabled": True, "location": {"mode": "unset"}, "units": "auto", "lang": "ru", "locale": "", "appbar_max": 4, "model": ""},
        "option_schema": schema(),
        "limits": {"floors": {"weather": 15, "news": 15, "system": 1, "custom": 15, "stock": 1},
                   "presets": [1, 5, 15, 30, 60, 180, 360, 720, 1440],
                   "floor_reasons": {"weather": ["Open-Meteo пересчитывает текущую погоду раз в 15 минут.", "x"]},
                   "local_kinds": ["todo", "notes", "clock", "timers"], "live_kinds": ["system", "timers", "clock"]},
    }

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class PanelsR23BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright unavailable: {exc}")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.store = {"lists": [{"id": "l_work000001", "name": "Работа", "color": "blue", "created": NOW, "items": [
            todo_item("t_first00001", "Позвонить в банк", priority=1),
            todo_item("t_second0001", "Ревью PR", tags=["код"])]}], "seq": 0}
        self.page = self.browser.new_page(viewport={"width": 1300, "height": 1100})
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        store = self.store

        def ok(r, body):
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        def route(r):
            path = r.request.url[len(ORIGIN):].split("?")[0]
            method = r.request.method
            body = json.loads(r.request.post_data or "{}") if r.request.post_data else {}
            if path in ("/", "/index.html"):
                return r.fulfill(status=200, content_type="text/html", body=FIXTURE)
            if path == "/api/pg/panels" and method == "GET":
                return ok(r, snapshot(store))
            if path == "/api/pg/panels-todo" and method == "GET":
                return ok(r, {"lists": store["lists"]})
            match = re.fullmatch(r"/api/pg/panels-todo/(l_\w+)/items(?:/(t_\w+))?", path)
            if match:
                lst = next(l for l in store["lists"] if l["id"] == match.group(1))
                if method == "POST":
                    store["seq"] += 1
                    title = re.sub(r"\s*(завтра|!1)\s*", " ", body["title"]).strip()
                    item = todo_item(f"t_new{store['seq']:07d}", title, priority=1 if "!1" in body["title"] else 0,
                                     due="2031-01-02" if "завтра" in body["title"] else "")
                    lst["items"].append(item)
                    return ok(r, {"item": item})
                item = next(i for i in lst["items"] if i["id"] == match.group(2))
                if method == "PATCH":
                    for key in ("done", "title", "priority", "due"):
                        if key in body:
                            item[key] = body[key]
                    return ok(r, {"item": dict(item, advanced="")})
                if method == "DELETE":
                    lst["items"].remove(item)
                    return ok(r, {"item": item})
            if path == "/api/pg/panels-preview":
                return ok(r, {"spec": {"blocks": [{"type": "title", "text": "preview"}]}, "refetch": False})
            if path == "/api/pg/panels-system":
                return ok(r, {"values": {"cpu": 12, "cpu_text": "12"}})
            if path.startswith("/api/pg/panels"):
                return ok(r, {"ok": True, "panel": {"id": "p_x", "status": "live"}, "settings": snapshot(store)["settings"]})
            file = STATIC / path.lstrip("/")
            if file.is_file():
                kind = "text/css" if file.suffix == ".css" else "application/javascript"
                return r.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return r.fulfill(status=404, body="")
        self.page.route(f"{ORIGIN}/**", route)
        self.page.goto(ORIGIN + "/")
        self.page.wait_for_selector("#panels-home .mp-card", timeout=5000)
        self.page.wait_for_function("document.querySelector('#panels-home .mp-grid')?.dataset.masonry === 'on'", timeout=5000)

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def requests(self, method, prefix=""):
        return [r for r in self.page.evaluate("window.__requests") if r[0] == method and r[1].startswith(prefix)]

    def test_dense_layout_leaves_no_holes(self):
        page = self.page
        holes = page.evaluate("""(() => {
          const grid = document.querySelector('#panels-home .mp-grid');
          const gap = parseFloat(getComputedStyle(grid).columnGap);
          const g = grid.getBoundingClientRect();
          const cards = [...grid.children].map(el => ({ id: el.dataset.panelId || el.className, r: el.getBoundingClientRect() }));
          const out = [];
          for (const c of cards) {
            if (c.r.top - g.top < 2) continue;                       // first row
            // something must end right above this card in every column it spans
            const above = cards.filter(o => o !== c && o.r.bottom <= c.r.top + 1 && o.r.right > c.r.left + 2 && o.r.left < c.r.right - 2);
            const nearest = Math.max(...above.map(o => o.r.bottom), g.top);
            if (c.r.top - nearest > gap + 6) out.push([c.id, Math.round(c.r.top - nearest)]);
          }
          return out; })()""")
        self.assertEqual(holes, [])

        weather = page.locator('[data-panel-id="p_weather001"]').bounding_box()
        stock = page.locator('[data-panel-id="p_stock00001"]').bounding_box()
        news = page.locator('[data-panel-id="p_news000001"]').bounding_box()
        self.assertLess(stock["y"], news["y"] + news["height"])
        self.assertLess(stock["y"] - (weather["y"] + weather["height"]), 24)

        page.evaluate("MemuPanels._state.panels.find(p => p.id === 'p_stock00001').size = 'w'; MemuPanels.render()")
        page.wait_for_timeout(100)
        grid = page.locator("#panels-home .mp-grid").bounding_box()
        wide = page.locator('[data-panel-id="p_stock00001"]').bounding_box()
        self.assertGreater(wide["width"], grid["width"] - 4)
        page.set_viewport_size({"width": 560, "height": 900})
        page.wait_for_timeout(300)
        inside = page.evaluate("""(() => { const g = document.querySelector('#panels-home .mp-grid').getBoundingClientRect();
          return [...document.querySelectorAll('#panels-home .mp-grid > *')].every(c => { const r = c.getBoundingClientRect(); return r.left >= g.left - 1 && r.right <= g.right + 1; }); })()""")
        self.assertTrue(inside)

    def test_drag_reorder_still_posts_order(self):
        page = self.page
        page.drag_and_drop('#panels-home [data-panel-id="p_custom0001"]', '#panels-home [data-panel-id="p_weather001"]',
                           target_position={"x": 20, "y": 10})
        page.wait_for_timeout(300)
        posts = self.requests("POST", "/api/pg/panels-order")
        self.assertTrue(posts, "drop did not post a new order")
        self.assertEqual(page.locator(".mp-placeholder").count(), 0)

    def test_tab_accepts_the_idea_ghost_text(self):
        page = self.page
        page.click("#panels-home .panels-home-head [data-mp-act=gallery]")
        page.click(".mp-tpl[data-kind=custom]")
        area = page.locator(".mp-modal [data-ghost]")
        area.click()
        self.assertTrue(page.locator(".mp-modal .mp-ghost .rest").inner_text())
        page.keyboard.type("Курс зо")
        self.assertEqual(page.locator(".mp-modal .mp-ghost .rest").inner_text(), "лота и его изменение за день")
        page.keyboard.press("Tab")
        self.assertEqual(area.input_value(), "Курс золота и его изменение за день")
        self.assertTrue(page.evaluate("document.activeElement.matches('[data-ghost]')"))

        area.fill("zzzz qq")
        page.keyboard.press("Tab")
        self.assertFalse(page.evaluate("document.activeElement.matches('[data-ghost]')"))

        page.locator(".mp-modal .mp-idea[data-idea]").first.click()
        self.assertGreater(len(area.input_value()), 10)
        self.assertGreaterEqual(page.locator(".mp-modal .mp-idea[data-idea]").count(), 4)

    def test_todo_add_toggle_delete_from_the_card(self):
        page = self.page
        card = page.locator('#panels-home [data-panel-id="p_todo000001"]')
        self.assertEqual(card.locator("[data-todo-item]").count(), 2)
        self.assertIn("2 открыто", card.locator(".mp-title small").inner_text())
        add = card.locator("[data-todo-add]")
        add.click()
        page.keyboard.type("Купить хлеб завтра !1")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelectorAll('#panels-home [data-panel-id=p_todo000001] [data-todo-item]').length === 3")
        post = self.requests("POST", "/api/pg/panels-todo/l_work000001/items")[-1]
        self.assertEqual(post[2]["title"], "Купить хлеб завтра !1")
        self.assertTrue(post[2]["quick"])
        self.assertEqual(add.input_value(), "")
        self.assertTrue(page.evaluate("document.activeElement.matches('[data-todo-add]')"))

        card.locator('[data-todo-item="t_first00001"] [data-todo-toggle]').click()
        page.wait_for_function("!document.querySelector('#panels-home [data-todo-item=t_first00001]')")
        patch = self.requests("PATCH", "/api/pg/panels-todo/l_work000001/items/t_first00001")[-1]
        self.assertEqual(patch[2]["done"], True)

        add.click()
        page.keyboard.press("ArrowDown")
        focused = page.evaluate("document.activeElement.dataset.todoItem")
        self.assertEqual(focused, "t_second0001")
        page.keyboard.press("Delete")
        page.wait_for_timeout(200)
        self.assertTrue(self.requests("DELETE", "/api/pg/panels-todo/l_work000001/items/t_second0001"))

        self.assertEqual(page.locator('#panels-home [data-panel-id="p_todo000001"]').count(), 1)
        self.assertFalse([r for r in self.requests("PATCH", "/api/pg/panels/p_todo000001") if r[2] and "placements" in r[2]])
        self.assertFalse(self.requests("DELETE", "/api/pg/panels/p_todo000001"))

    def test_todo_editor_saves_fields(self):
        page = self.page
        page.locator('#panels-home [data-todo-item="t_second0001"] [data-todo-open]').click()
        pop = page.locator(".mp-todo-pop")
        self.assertEqual(pop.count(), 1)
        pop.locator('[data-te="priority"] button[data-v="2"]').click()
        page.wait_for_timeout(200)
        patch = self.requests("PATCH", "/api/pg/panels-todo/l_work000001/items/t_second0001")[-1]
        self.assertEqual(patch[2]["priority"], 2)
        self.assertEqual(patch[2]["title"], "Ревью PR")
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".mp-todo-pop").count(), 0)

    def test_custom_interval_respects_the_floor_and_explains_it(self):
        page = self.page
        page.evaluate("MemuPanels.openEditor('p_weather001')")
        page.select_option(".mp-modal [data-mp-cadence]", "custom")
        page.fill(".mp-modal [data-cad-n]", "30")
        page.select_option(".mp-modal [data-cad-unit]", "s")
        hint = page.locator(".mp-modal [data-cad-hint]")
        self.assertEqual(hint.get_attribute("data-warn"), "1")
        self.assertIn("15 мин", hint.inner_text())
        self.assertIn("Open-Meteo", hint.inner_text())
        page.click(".mp-modal [data-cad-more] > summary")
        page.click('.mp-modal [data-cad-days2] [data-days="0,1,2,3,4"]')
        page.check(".mp-modal [data-cad-win]")
        page.click(".mp-modal [data-mp-save]")
        page.wait_for_timeout(200)
        body = self.requests("PATCH", "/api/pg/panels/p_weather001")[-1][2]
        self.assertEqual(body["cadence"], {"kind": "interval", "minutes": 15, "days": [0, 1, 2, 3, 4], "from": "09:00", "to": "18:00"})

    def test_times_and_days_cadence(self):
        page = self.page
        page.evaluate("MemuPanels.openEditor('p_weather001')")
        page.select_option(".mp-modal [data-mp-cadence]", "times")
        page.fill(".mp-modal [data-time-new]", "19:30")
        page.click(".mp-modal [data-time-add]")
        page.click('.mp-modal [data-cad-days] [data-day="6"]')
        page.click(".mp-modal [data-mp-save]")
        page.wait_for_timeout(200)
        body = self.requests("PATCH", "/api/pg/panels/p_weather001")[-1][2]
        self.assertEqual(body["cadence"], {"kind": "times", "at": ["08:00", "19:30"], "days": [0, 1, 2, 3, 4, 5]})

    def test_model_per_panel_and_honest_state(self):
        page = self.page
        page.evaluate("MemuPanels.openEditor('p_custom0001')")
        page.click(".mp-modal [data-mp-more] > summary")
        select = page.locator(".mp-modal [data-mp-aimodel]")
        self.assertTrue(select.is_disabled())
        self.assertIn("Proxy", page.locator(".mp-modal [data-mp-aimodel] + small").inner_text())
        page.keyboard.press("Escape")
        page.evaluate("S.models = [{id: 'gpt-x'}, {id: 'claude-y'}]")
        page.evaluate("MemuPanels.openEditor('p_custom0001')")
        page.click(".mp-modal [data-mp-more] > summary")
        self.assertFalse(page.locator(".mp-modal [data-mp-aimodel]").is_disabled())
        page.select_option(".mp-modal [data-mp-aimodel]", "claude-y")
        page.click(".mp-modal [data-mp-save]")
        page.wait_for_timeout(200)
        body = self.requests("PATCH", "/api/pg/panels/p_custom0001")[-1][2]
        self.assertEqual(body["ai_model"], "claude-y")

    def test_content_options_reach_the_patch(self):
        page = self.page
        page.evaluate("MemuPanels.openEditor('p_sys0000001')")
        metrics = page.locator('.mp-modal [data-opt="metrics"]')
        metrics.locator('[data-v="ram"] input').uncheck()
        metrics.locator('[data-v="procs"] input').check()
        metrics.locator('[data-v="cores"] input').check()
        page.click('.mp-modal [data-opt="live"] button[data-v="1"]')
        page.wait_for_timeout(300)
        self.assertTrue(self.requests("POST", "/api/pg/panels-preview"))
        page.click(".mp-modal [data-mp-save]")
        page.wait_for_timeout(200)
        body = self.requests("PATCH", "/api/pg/panels/p_sys0000001")[-1][2]
        self.assertEqual(body["options"]["metrics"], ["cpu", "gpu", "cores", "procs"])
        self.assertEqual(body["options"]["live"], "1")

        page.evaluate("MemuPanels.openEditor('p_sys0000001')")
        self.assertFalse(page.locator('.mp-modal [data-opt-wrap="procs_count"]').is_visible())

if __name__ == "__main__":
    unittest.main()
