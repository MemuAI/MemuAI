"""Round21 panels renderer smoke test in headless Chromium.

A small fixture page carries the containers panels.js mounts into (start screen, sidebar, top
bar) and mocks /api/pg/panels*; every block type, the three render modes, the «+N» fold of the
top-bar minis, the context menu, the gallery, the chat preview and the skill are checked.
Skipped when Playwright or a Chromium build is not installed.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
ORIGIN = "http://memu.test"

FIXTURE = """<!DOCTYPE html><html lang="ru" data-theme="dark" data-design="classic"><head><meta charset="utf-8">
<style>:root{--bg:#1a1a1c;--panel:#232326;--panel-2:#2a2a2e;--text:#eee;--muted:#999;--accent:#d97757;--border:#333;--ok:#3fb27f;--danger:#e5534b}
 body{margin:0;background:var(--bg);color:var(--text);font-family:sans-serif} #app{display:flex;height:100vh}
 .side{width:260px} .main{flex:1;display:flex;flex-direction:column;min-width:0}
 .topbar.appbar{display:flex;align-items:center;gap:6px;min-height:54px;padding:8px 14px;flex-wrap:wrap;box-sizing:border-box}
 .grow{flex:1} .chat-view-wrap{display:flex;flex-direction:column;flex:1;min-height:0}</style>
<link rel="stylesheet" href="/motion.css"><link rel="stylesheet" href="/panels.css"></head><body>
<div id="app"><aside class="side"><div class="side-scroll"><div class="side-nav"><button>Планирование</button></div>
<div class="side-group" id="group-chats"></div></div></aside>
<main class="main"><header class="topbar appbar" id="topbar"><button id="bar-menu">m</button><span id="model">Model</span>
<div class="grow"></div><button class="iconbtn" id="btn-focus">f</button><button class="iconbtn" id="btn-theme">t</button></header>
<div class="chat-view-wrap empty-chat" id="chat-view-wrap"><div class="chat" id="chat"><div class="greeting" id="greeting">
<div class="temp-guide" id="temp-guide" hidden></div></div><div class="messages" id="messages"></div></div>
<div class="composer-wrap"><textarea id="input"></textarea></div></div></main></div>
<script>
 window.MEMUAI_SKILL_LIBRARY = { version: 4, skills: [] };
 var PG_LANG = "ru"; var S = { models: [] };
 window.__requests = [];
 async function api(path, opts = {}) {
   window.__requests.push([opts.method || "GET", path, opts.body || null]);
   const resp = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" },
     body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
   const data = await resp.json().catch(() => ({}));
   if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
   return data;
 }
 function toast() {} function getSkills() { return MEMUAI_SKILL_LIBRARY.skills.map((s) => ({ ...s, enabled: true })); }
</script>
<script src="/panels.js"></script></body></html>"""

NOW = time.time()

def panel(pid, kind, title, blocks, data, places, size="m", **extra):
    out = {"id": pid, "kind": kind, "title": title, "status": "live", "spec": {"blocks": blocks}, "data": data,
           "sources": [{"name": "Source", "url": "https://source.example/"}], "placements": places,
           "order": {p: i for i, p in enumerate(("home", "sidebar", "appbar"))}, "size": size,
           "cadence": {"kind": "interval", "minutes": 60}, "last_refresh": NOW - 300, "wall": None,
           "source": {"type": "ai" if kind in ("custom", "sports", "web") else "fx", "params": {}}, "refreshing": False}
    out.update(extra)
    return out

ALL_BLOCKS = [
    {"type": "title", "text": "{{name}}", "subtitle": "sub", "icon": "chart"},
    {"type": "metric", "value": "{{v}}", "unit": "₽", "delta": "+1,2%", "trend": "up", "caption": "cap", "label": "lbl", "icon": "coin"},
    {"type": "stats", "items": [{"label": "A", "value": "1"}, {"label": "B", "value": "2", "unit": "%"}]},
    {"type": "strip", "items": "{{days}}"},
    {"type": "list", "items": "{{rows}}", "max": 3},
    {"type": "progress", "value": "{{p}}", "max": 100, "label": "Готово"},
    {"type": "sparkline", "points": "{{hist}}"},
    {"type": "badge", "text": "OK", "tone": "ok"},
    {"type": "text", "text": "Plain text", "muted": True},
    {"type": "link", "text": "Open", "url": "https://example.org"},
    {"type": "action", "text": "Ask", "prompt": "What changed?"},
    {"type": "countdown", "target": "2031-01-01T00:00:00"},
    {"type": "row", "blocks": [{"type": "badge", "text": "x"}, {"type": "text", "text": "y"}]},
]
ALL_DATA = {"name": "<img src=x onerror=window.__xss=1>", "v": 42, "p": 40, "hist": [1, 3, 2, 5],
            "days": [{"label": "Пн", "value": "5°", "icon": "rain"}], "rows": [
                {"title": "One", "url": "https://a.example"}, {"title": "Two", "url": "javascript:alert(1)"}, {"title": "Three"},
                {"title": "Four"}]}

SNAPSHOT = {
    "panels": [
        panel("p_alltypes01", "custom", "All", ALL_BLOCKS, ALL_DATA, ["home", "sidebar"], size="l"),
        panel("p_weather001", "weather", "Погода", [{"type": "metric", "value": "{{temp}}", "unit": "°C", "icon": "{{icon}}"}],
              {"temp": 12, "icon": "partly"}, ["home", "appbar"], spec_mini=True),
        panel("p_setup00001", "weather", "Погода 2", [], {}, ["sidebar"], status="setup",
              wall={"status": "needs_location:weather", "message": ""}),
        panel("p_build00001", "custom", "Building", [], {}, ["home", "appbar"], status="building", refreshing=True),
        panel("p_failed0001", "custom", "Failed", [], {}, ["home"], status="failed",
              wall={"status": "blocked:proxy-offline", "message": "offline"}),
    ] + [panel(f"p_chip{i:07d}", "currency", f"Chip {i}", [{"type": "metric", "value": "{{r}}", "unit": "₽"}],
               {"r": 80 + i}, ["appbar"]) for i in range(6)],
    "dismissed": [panel("p_gone000001", "rss", "Old feed", [], {}, ["home"], dismissed_at=NOW - 60)],
    "settings": {"enabled": True, "location": {"mode": "unset"}, "units": "auto", "lang": "ru", "locale": "", "appbar_max": 4},
    "templates": [],
}

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
                      "/workspace/work/chromium/chrome"):
        if candidate and Path(candidate).exists():
            return candidate
    return ""

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class PanelsBrowserTest(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={"width": 1300, "height": 860})
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)

        snap = SNAPSHOT

        def route(r):
            path = r.request.url[len(ORIGIN):].split("?")[0]
            if path in ("/", "/index.html"):
                return r.fulfill(status=200, content_type="text/html", body=FIXTURE)
            if path == "/api/pg/panels":
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(snap))
            if path.startswith("/api/pg/panels"):
                return r.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "settings": snap["settings"]}))
            file = STATIC / path.lstrip("/")
            if file.is_file():
                kind = "text/css" if file.suffix == ".css" else "application/javascript"
                return r.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return r.fulfill(status=404, body="")
        self.page.route(f"{ORIGIN}/**", route)
        self.page.goto(ORIGIN + "/")
        self.page.wait_for_selector("#panels-home .mp-card", timeout=5000)

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def test_mounts_and_renders_every_block(self):
        page = self.page
        self.assertEqual(page.locator("#panels-home .mp-card").count(), 4)
        self.assertEqual(page.locator("#group-panels .mp-card").count(), 2)
        card = page.locator('#panels-home .mp-card[data-panel-id="p_alltypes01"]')
        for cls in (".mp-title", ".mp-metric", ".mp-stats", ".mp-strip", ".mp-list", ".mp-progress", ".mp-spark",
                    ".mp-badge", ".mp-text", ".mp-link", ".mp-action", ".mp-countdown", ".mp-rowbox", ".mp-foot"):
            self.assertEqual(card.locator(cls).count() >= 1, True, cls)
        self.assertEqual(card.get_attribute("data-trend"), "up")

        self.assertIsNone(page.evaluate("window.__xss || null"))
        self.assertIn("<img", card.locator(".mp-title b").inner_text())
        self.assertEqual(card.locator('a.mp-row[href^="javascript"]').count(), 0)
        self.assertEqual(card.locator(".mp-row").count(), 3)
        self.assertEqual(card.locator(".mp-cd-cell").count(), 4)

        side = page.locator('#group-panels .mp-card[data-panel-id="p_alltypes01"]')
        self.assertEqual(side.locator(".mp-stats").count(), 0)
        self.assertEqual(side.locator(".mp-row").count(), 2)

        self.assertEqual(page.locator('[data-panel-id="p_build00001"] .mp-skel').count(), 1)
        self.assertIn("Proxy", page.locator('[data-panel-id="p_failed0001"] .mp-wall').inner_text())
        self.assertEqual(page.locator('#group-panels [data-panel-id="p_setup00001"] [data-mp-act=locate]').count(), 1)

    def test_appbar_minis_fold_into_more(self):
        page = self.page
        bar = page.locator("#panels-appbar")
        self.assertEqual(bar.locator(".mp-chip[data-panel-id]").count(), 8)
        visible = page.evaluate("[...document.querySelectorAll('#panels-appbar .mp-chip[data-panel-id]')].filter(c => !c.hidden).length")
        self.assertEqual(visible, 4)
        self.assertEqual(bar.locator(".mp-more").inner_text(), "+4")

        page.set_viewport_size({"width": 700, "height": 700})
        page.wait_for_timeout(250)
        top = page.evaluate("(() => { const b = document.querySelector('#topbar').getBoundingClientRect(); return b.height; })()")
        self.assertLess(top, 60)
        visible = page.evaluate("[...document.querySelectorAll('#panels-appbar .mp-chip[data-panel-id]')].filter(c => !c.hidden).length")
        self.assertLess(visible, 4)
        fits = page.evaluate("""(() => { const bar = document.querySelector('#panels-appbar'); const r = bar.getBoundingClientRect();
          return [...bar.children].filter(c => !c.hidden).every(c => { const b = c.getBoundingClientRect(); return b.left >= r.left - 1 && b.right <= r.right + 1; }); })()""")
        self.assertTrue(fits)
        page.click("#panels-appbar .mp-more")
        self.assertGreaterEqual(page.locator(".mp-pop .mp-pop-list .mp-chip").count(), 4)

    def test_chip_popover_menu_and_place_toggle(self):
        page = self.page
        page.click('#panels-appbar .mp-chip[data-panel-id="p_weather001"]')
        self.assertEqual(page.locator(".mp-pop .mp-card[data-mode=popover]").count(), 1)
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".mp-pop").count(), 0)
        card = page.locator('#panels-home .mp-card[data-panel-id="p_weather001"]')
        card.hover()
        card.locator("[data-mp-menu]").click()
        menu = page.locator(".mp-menu")
        self.assertEqual(menu.locator("[data-m=place]").count(), 3)
        self.assertEqual(menu.locator('[data-m=place][aria-checked=true]').count(), 2)
        menu.locator('[data-m=place][data-place=sidebar]').click()
        page.wait_for_timeout(200)
        patches = [r for r in page.evaluate("window.__requests") if r[0] == "PATCH"]
        self.assertEqual(patches[-1][1], "/api/pg/panels/p_weather001")
        self.assertEqual(sorted(patches[-1][2]["placements"]), ["appbar", "home", "sidebar"])

        card.click(button="right")
        self.assertEqual(page.locator(".mp-menu").count(), 1)

    def test_gallery_and_template_form(self):
        page = self.page
        page.click("#panels-home .panels-home-head [data-mp-act=gallery]")
        self.assertEqual(page.locator(".mp-modal .mp-tpl").count(), 19)
        self.assertEqual(page.locator(".mp-modal .mp-gal-group").count(), 5)
        self.assertEqual(page.locator(".mp-modal .mp-tpl").nth(1).get_attribute("data-kind"), "news")
        self.assertEqual(page.locator(".mp-modal [data-revive]").count(), 1)
        page.click(".mp-tpl[data-kind=rss]")
        page.click("[data-mp-create]")
        self.assertFalse([r for r in page.evaluate("window.__requests") if r[0] == "POST" and r[1] == "/api/pg/panels"])
        page.fill("[name=url]", "https://example.org/rss")
        page.click("[data-mp-create]")
        page.wait_for_timeout(200)
        post = [r for r in page.evaluate("window.__requests") if r[0] == "POST" and r[1] == "/api/pg/panels"][-1]
        self.assertEqual(post[2]["template"], "rss")
        self.assertEqual(post[2]["params"]["url"], "https://example.org/rss")
        self.assertEqual(post[2]["placements"], ["home"])

    def test_chat_preview_and_skill(self):
        page = self.page
        html = page.evaluate("""MemuPanels.inlineHtml({ role: 'assistant', toolHistory: [
            { tool: 'panel_create', ok: true, summary: '«Погода» · готова · #p_weather001' },
            { tool: 'web_search', ok: true, summary: '#p_alltypes01' } ] })""")
        self.assertIn('data-panel-inline="p_weather001"', html)
        self.assertNotIn("p_alltypes01", html)
        self.assertEqual(html.count('data-panel-pin="'), 3)
        skill = page.evaluate("MEMUAI_SKILL_LIBRARY.skills.find(s => s.id === 'memuai_panels')")
        self.assertTrue(skill["inline"])
        block = page.evaluate("MemuPanels.systemBlock('chat')")
        self.assertIn("panel_create", block)
        self.assertIn("p_weather001 — Погода", block)
        self.assertEqual(page.evaluate("MemuPanels.systemBlock('code')"), "")
        summary = page.evaluate("MemuPanels.summary('panel_create', { panel: { id: 'p_abc123456', title: 'X', status: 'building' } })")
        self.assertRegex(summary, r"#p_abc123456$")

if __name__ == "__main__":
    unittest.main()

FEED_ITEMS = [{"title": f"Story {i} about a model release", "url": f"https://news{i}.example/a", "source": f"Src{i}",
               "ts": NOW - i * 3600, "summary": f"Summary {i}.", "why": f"why {i}", "badge": "официально" if i == 0 else "",
               "tone": "ok", "links": [{"name": "HN", "url": f"https://news.ycombinator.com/item?id={i}"}]} for i in range(7)]
USAGE_ACCOUNTS = [
    {"id": "a_000001", "provider": "claude", "name": "Работа", "plan": "Max 20×", "state": "ok", "limits": [
        {"key": "five_hour", "title": "5 часов", "used": 70, "resets_at": NOW + 7200, "window": 18000, "start": NOW - 10800},
        {"key": "seven_day", "title": "Неделя", "used": 31, "resets_at": NOW + 3 * 86400, "window": 604800, "start": NOW - 4 * 86400}]},
    {"id": "a_000002", "provider": "codex", "name": "Личный", "plan": "Plus", "state": "stale", "wall": {"message": "429"}, "limits": [
        {"key": "primary_window", "title": "5 ч", "used": 100, "resets_at": NOW + 600, "window": 18000, "start": NOW - 17400}]},
    {"id": "a_000003", "provider": "antigravity", "name": "Antigravity", "state": "wall", "limits": [],
     "wall": {"status": "source_gone:antigravity", "message": "Antigravity не запущен"}},
]
R22_SNAPSHOT = {
    "panels": [
        panel("p_news000001", "news", "Новости ИИ", [{"type": "title", "text": "Новости ИИ", "icon": "news"},
                                                     {"type": "list", "items": "{{items}}", "max": 10, "style": "feed"}],
              {"items": FEED_ITEMS, "stats": {"sources_ok": 18, "sources_failed": 1, "candidates": 400, "stories": 100}},
              ["home"], size="m", source={"type": "feed", "params": {"topic": "ai"}}),
        panel("p_news000002", "news", "Пять ровно", [{"type": "list", "items": "{{items}}", "style": "feed"}],
              {"items": FEED_ITEMS[:5]}, ["home"], size="m", source={"type": "feed", "params": {}}),
        panel("p_sys0000001", "system", "ПК", [{"type": "row", "blocks": [
            {"type": "gauge", "value": "{{cpu}}", "label": "CPU"}, {"type": "gauge", "value": "{{gpu}}", "label": "GPU"},
            {"type": "gauge", "value": "{{ram}}", "label": "RAM", "min_size": "m"}]}],
              {"cpu": 93, "gpu": None, "ram": 40}, ["home"], size="m", source={"type": "system", "params": {}}),
        panel("p_usage00001", "usage", "Лимиты", [{"type": "usage", "accounts": "{{accounts}}"}],
              {"accounts": USAGE_ACCOUNTS}, ["home", "sidebar"], size="l", source={"type": "usage", "params": {}}),
        panel("p_progress01", "news", "Собирается", [], {}, ["home"], status="building", refreshing=True,
              progress={"stage": "dedupe", "detail": "116"}, source={"type": "feed", "params": {}}),
    ],
    "dismissed": [], "settings": SNAPSHOT["settings"], "templates": [],
    "limits": {"floors": {"news": 15, "system": 1}, "presets": [1, 5, 15, 30, 60, 180, 360, 720, 1440]},
}

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class PanelsR22BrowserTest(PanelsBrowserTest):
    def setUp(self):
        global SNAPSHOT
        self._saved, SNAPSHOT = SNAPSHOT, R22_SNAPSHOT
        try:
            super().setUp()
        finally:
            SNAPSHOT = self._saved

    test_mounts_and_renders_every_block = test_appbar_minis_fold_into_more = None
    test_chip_popover_menu_and_place_toggle = test_gallery_and_template_form = test_chat_preview_and_skill = None

    def test_feed_overflow_is_clickable_and_lists_everything(self):
        page = self.page
        card = page.locator('#panels-home [data-panel-id="p_news000001"]')
        self.assertEqual(card.locator(".mp-row").count(), 4)
        more = card.locator(".mp-list-more")
        self.assertEqual(more.inner_text().strip(), "Ещё 3")
        more.click()
        pop = page.locator(".mp-pop-detail")
        self.assertEqual(pop.locator(".mp-detail-row").count(), 7)
        self.assertEqual(pop.locator("a.mp-detail-title").first.get_attribute("href"), "https://news0.example/a")
        self.assertIn("Почему здесь", pop.locator(".mp-detail-why").first.inner_text())
        self.assertIn("Просмотрено источников: 18", pop.locator(".mp-detail-foot").inner_text())
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".mp-pop").count(), 0)

        five = page.locator('#panels-home [data-panel-id="p_news000002"]')
        self.assertEqual(five.locator(".mp-row").count(), 5)
        self.assertEqual(five.locator(".mp-list-more:not(.ghost)").count(), 0)

        page.evaluate("MemuPanels._state.panels.find(p => p.id === 'p_news000001').size = 'l'; MemuPanels.render()")
        self.assertEqual(page.locator('#panels-home [data-panel-id="p_news000001"] .mp-row').count(), 7)

    def test_gauges_usage_and_progress(self):
        page = self.page
        sys_card = page.locator('#panels-home [data-panel-id="p_sys0000001"]')
        self.assertEqual(sys_card.locator(".mp-gauge").count(), 3)
        self.assertEqual(sys_card.locator('.mp-gauge[data-tone="danger"]').count(), 1)
        self.assertEqual(sys_card.locator('.mp-gauge[data-tone="none"] b').first.inner_text(), "—")
        self.assertEqual(sys_card.locator('.mp-gauge[data-tone="none"] circle.val').count(), 0, "no stray round-cap dot on an empty ring")
        usage = page.locator('#panels-home [data-panel-id="p_usage00001"]')
        self.assertEqual(usage.locator(".mp-acc").count(), 3)
        self.assertEqual(usage.locator(".mp-lim").count(), 3)
        self.assertEqual(usage.locator('.mp-lim[data-pace="ahead"]').count(), 1)
        self.assertEqual(usage.locator('.mp-lim[data-pace="full"]').count(), 1)
        self.assertEqual(usage.locator(".mp-lim-bar em").count(), 3)
        self.assertRegex(usage.locator(".mp-lim-reset").first.inner_text(), r"сброс через [12] ч")
        self.assertIn("не запущен", usage.locator('.mp-acc[data-state="wall"]').inner_text())
        self.assertEqual(usage.locator(".mp-acc-state").inner_text().strip().lower(), "устарело")
        side = page.locator('#group-panels [data-panel-id="p_usage00001"]')
        self.assertEqual(side.locator(".mp-lim").count(), 2, "sidebar: one (the fullest) window per account")
        self.assertEqual(side.locator(".mp-lim-reset").count(), 0)
        steps = page.locator('[data-panel-id="p_progress01"] .mp-steps li')
        self.assertEqual(steps.count(), 5)
        self.assertEqual(page.locator('[data-panel-id="p_progress01"] .mp-steps li[data-state="done"]').count(), 2)
        self.assertIn("116", page.locator('[data-panel-id="p_progress01"] .mp-steps li[data-state="now"]').inner_text())

    def test_account_test_button_sends_its_own_row(self):
        """«Проверить» читает поля своей строки (раньше querySelectorAll на самой строке давал пустой список →
        на сервер уходило тело без provider и приходило «Неизвестный провайдер»)."""
        page = self.page
        sent = []
        page.on("request", lambda req: sent.append(req.post_data_json) if req.url.endswith("/api/pg/panels-usage/test") else None)
        page.click("#panels-home .panels-home-head [data-mp-act=gallery]")
        page.click(".mp-tpl[data-kind=usage]")
        page.click("[data-acc-add]")
        row = page.locator(".mp-acc-row").nth(1)
        row.locator("[data-acc-name]").fill("Личный")
        row.locator("[data-acc-test]").click()
        page.wait_for_timeout(400)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["provider"], "codex")
        self.assertEqual(sent[0]["name"], "Личный")
        self.assertRegex(sent[0]["id"], r"^a_[0-9a-f]{10}$")

        row.locator("[data-acc-provider]").select_option("antigravity")
        self.assertEqual(page.locator(".mp-acc-row").nth(1).locator("[data-acc-name]").input_value(), "Личный")

    def test_new_templates_in_the_gallery(self):
        page = self.page
        page.click("#panels-home .panels-home-head [data-mp-act=gallery]")
        page.click(".mp-tpl[data-kind=usage]")
        self.assertGreaterEqual(page.locator(".mp-acc-row").count(), 1)
        page.click("[data-acc-add]")
        self.assertEqual(page.locator(".mp-acc-row").count(), 2)
        page.locator(".mp-acc-row").nth(1).locator("[data-acc-mode]").select_option("token")
        self.assertTrue(page.locator(".mp-acc-row").nth(1).locator("[data-acc-token]").is_visible())
        options = page.eval_on_selector("[data-mp-cadence]", "s => [...s.options].map(o => o.value)")
        self.assertEqual(options[:3], ["1", "5", "15"])
        page.click("[data-mp-back]")
        page.click(".mp-tpl[data-kind=news]")
        options = page.eval_on_selector("[data-mp-cadence]", "s => [...s.options].map(o => o.value)")
        self.assertEqual(options[0], "15", "AI feeds: the floor is 15 minutes")
        self.assertIn("daily", options)
