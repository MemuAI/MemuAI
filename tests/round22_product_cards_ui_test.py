"""Round 22: product cards UI — research progress instead of flickering drafts,
copy actions, budget/availability flags and per-store status in the side sheet.

products.js runs in headless Chromium on a tiny harness page (no app.js): the
pieces app.js calls (holdStream, progress, intent) and the card/sheet markup are
exercised directly. Skipped without Playwright/Chromium.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import threading
import unittest
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


HARNESS = """<!doctype html><html lang="ru" data-theme="light"><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/design.css"><link rel="stylesheet" href="/motion.css"><link rel="stylesheet" href="/products.css">
</head><body><div id="out" style="width:900px;padding:20px"></div>
<script>var PG_LANG = "ru"; window.copied = []; window.toasts = [];
async function copyText(text, notify) { window.copied.push(text); return true; }
function toast(text, kind) { window.toasts.push([text, kind]); }</script>
<script src="/products.js"></script></body></html>"""

ITEM = {
    "id": "p1", "name": "WD Blue SN5000 1 ТБ", "brand": "WD", "model": "WDS100T4B0E", "best": True, "badge": "Лучший выбор",
    "budget_status": "within", "availability": "in_stock",
    "key_specs": ["PCIe 4.0 x4", "до 5150 МБ/с"], "summary": "Современный TLC-накопитель.",
    "pros": ["Холодный", "TLC"], "cons": ["Без DRAM"], "rating": 4.8, "review_count": 1250, "rating_store": "DNS",
    "price": {"amount": 8999, "currency": "RUB", "approx": False, "store": "DNS", "source": "https://www.dns-shop.ru/product/a1/",
              "from": True},
    "offers": [
        {"store_id": "dns", "store": "DNS", "url": "https://www.dns-shop.ru/product/a1/", "verified": True,
         "availability": "in_stock", "price": {"amount": 8999, "currency": "RUB", "approx": False}, "color": "#fc8507"},
        {"store_id": "ozon", "store": "Ozon", "url": "https://www.ozon.ru/product/x-55/", "verified": True,
         "availability": "in_stock", "price": {"amount": 9290, "currency": "RUB", "approx": False}, "color": "#005bff"},
    ],
    "store_checks": [
        {"store_id": "wildberries", "store": "Wildberries", "status": "unchecked",
         "search_url": "https://www.wildberries.ru/catalog/0/search.aspx?search=WD+Blue"},
        {"store_id": "citilink", "store": "Ситилинк", "status": "unconfirmed", "url": "https://www.citilink.ru/product/wd-77/"},
        {"store_id": "mvideo", "store": "М.Видео", "status": "not_found"},
    ],
}
OVER = {"id": "p2", "name": "ADATA Legend 850 1TB", "budget_status": "over",
        "price": {"amount": 16357, "currency": "RUB", "approx": False},
        "offers": [{"store_id": "ozon", "store": "Ozon", "url": "https://www.ozon.ru/product/adata-1/", "verified": True,
                    "availability": "in_stock", "price": {"amount": 16357, "currency": "RUB", "approx": False}}]}
GONE = {"id": "p3", "name": "Crucial P3 1TB", "availability": "unavailable", "discontinued": True}
UNVERIFIED = {"id": "p4", "name": "Kingston NV3 1 ТБ", "budget_status": "unverified",
              "price": {"amount": 6500, "currency": "RUB", "approx": True}}
DATA = {"v": 1, "locale": "ru", "currency": "RUB", "budget": {"amount": 15000, "currency": "RUB"},
        "items": [ITEM, UNVERIFIED, OVER, GONE]}


@unittest.skipUnless(_chromium(), "Chromium is not installed")
class ProductCardsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright unavailable: {exc}")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.startswith("/harness"):
                    body = HARNESS.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return super().do_GET()

            def do_POST(self):  # noqa: N802 - the normalize endpoint is not served here
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(PG)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def page(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.server.server_port}/harness")
        page.wait_for_timeout(150)
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def render(self, page, data=DATA):
        raw = json.dumps(data, ensure_ascii=False)
        page.evaluate("""([raw, data]) => {
          const key = MemuProducts._keyOf(raw);
          MemuProducts._cache.set(key, { ok: true, data });
          document.getElementById('out').innerHTML = MemuProducts.placeholder(raw, false);
          return key;
        }""", [raw, data])

    # ------------------------------------------------------------ intent + progress
    def test_intent_detection(self):
        page = self.page()
        cases = page.evaluate("""() => [
          'Подбери SSD до 15000 ₽', 'какой ноутбук купить для учёбы', 'посоветуй наушники до 10 тыс',
          'best noise-cancelling headphones under $200', 'which monitor should I buy for coding',
          'как работает SSD', 'почему тормозит мой ноутбук', 'what is NVMe', 'привет', 'подбери SSD без карточек'
        ].map(MemuProducts.intent)""")
        self.assertEqual(cases, [True, True, True, True, True, False, False, False, False, False])

    def test_draft_text_between_tool_rounds_is_held_behind_progress(self):
        page = self.page()
        result = page.evaluate("""() => {
          const m = { role: 'assistant', streaming: true, productIntent: true,
                      content: 'WD Blue SN5000 — лучший выбор. Подробности в карточках:' };
          const first = MemuProducts.holdStream(m, m.content);
          m.toolHistory = [{ tool: 'web_search' }];
          m.toolCalls = { status: 'working', calls: [{ tool: 'product_lookup', args: { query: 'WD Blue SN5000 1TB' } },
                                                    { tool: 'product_lookup', args: { query: 'Kingston NV3 1TB' } }] };
          m.content = '';
          const second = MemuProducts.holdStream(m, '');
          m.toolHistory.push({ tool: 'product_lookup' }); m.toolCalls.status = 'done';
          m.content = 'WD Blue SN5000 — лучший выбор.';
          const third = MemuProducts.holdStream(m, m.content);
          m.content += '\\n\\n```memuai-products\\n{"items":[';
          const final = MemuProducts.holdStream(m, m.content);
          m.streaming = false;
          const done = MemuProducts.holdStream({ ...m, content: 'текст' }, 'текст');
          const noIntent = MemuProducts.holdStream({ role: 'assistant', streaming: true, content: 'Ответ' }, 'Ответ');
          const longAnswer = MemuProducts.holdStream({ role: 'assistant', streaming: true, productIntent: true,
                                                       content: 'x'.repeat(1600) }, 'x'.repeat(1600));
          const stage = (s) => s && JSON.parse(s.split('\\n')[1]);
          return { first: stage(first), second: stage(second), third: stage(third), final, done, noIntent, longAnswer };
        }""")
        self.assertEqual(result["first"]["step"], "search")
        self.assertEqual(result["second"]["step"], "prices")
        self.assertEqual(result["second"]["count"], 2)
        self.assertEqual(result["second"]["names"], ["WD Blue SN5000 1TB", "Kingston NV3 1TB"])
        self.assertEqual(result["third"]["step"], "cards")
        self.assertIsNone(result["final"])           # the final answer with its block streams normally
        self.assertIsNone(result["done"])
        self.assertIsNone(result["noIntent"])
        self.assertIsNone(result["longAnswer"])

    def test_progress_markup_and_screenshot(self):
        page = self.page()
        page.evaluate("""() => { document.getElementById('out').innerHTML = MemuProducts.progress(JSON.stringify(
          { step: 'prices', stores: 7, count: 6, names: ['WD Blue SN5000 1TB', 'Kingston NV3 1TB', '<b>x</b>'] })); }""")
        state = page.evaluate("""() => ({
          steps: [...document.querySelectorAll('.prd-steps li')].map(li => li.className),
          label: document.querySelector('.prd-progress .prd-title').textContent,
          names: [...document.querySelectorAll('.prd-progress-names span')].map(s => s.textContent),
          skeletons: document.querySelectorAll('.prd-progress .prd-skel').length,
          live: document.querySelector('.prd-progress [aria-live]') !== null })""")
        self.assertEqual(state["steps"], ["done", "now", "todo"])
        self.assertEqual(page.evaluate("() => document.querySelector('.prd-steps li.now').textContent"),
                         "Проверяю цены и наличие: 6 моделей, 7 магазинов")
        for n, word in ((1, "модель"), (2, "модели"), (5, "моделей"), (11, "моделей"), (22, "модели")):
            text = page.evaluate("(n) => { const box = document.createElement('div'); box.innerHTML = MemuProducts.progress("
                                 "JSON.stringify({ step: 'prices', stores: 7, count: n === 1 ? 2 : n })); return box.textContent; }", n)
            if n != 1:
                self.assertIn(f"{n} {word},", text)
        self.assertIn("Проверяю цены", state["label"])
        self.assertIn("<b>x</b>", state["names"])     # escaped, shown as text
        self.assertEqual(state["skeletons"], 3)
        self.assertTrue(state["live"])
        shots = os.environ.get("R22_SHOT_DIR")
        if shots:
            page.screenshot(path=str(Path(shots) / "ui-progress-harness.png"), clip={"x": 0, "y": 0, "width": 960, "height": 420})

    # ------------------------------------------------------------ flags + copy
    def test_flags_on_cards(self):
        page = self.page()
        self.render(page)
        flags = page.evaluate("() => [...document.querySelectorAll('.prd-cell')].map(c => "
                              "[c.querySelector('.prd-name').textContent, (c.querySelector('.prd-flag') || {}).textContent || ''])")
        self.assertEqual(flags, [["WD Blue SN5000 1 ТБ", "В бюджете"], ["Kingston NV3 1 ТБ", "Цена не подтверждена"],
                                 ["ADATA Legend 850 1TB", "Выше бюджета"], ["Crucial P3 1TB", "Снят с продажи"]])
        # No nested interactive elements: the copy button is a sibling of the card button.
        nested = page.evaluate("() => document.querySelectorAll('.prd-card button, .prd-card a').length")
        self.assertEqual(nested, 0)
        self.assertEqual(page.evaluate("() => document.querySelectorAll('.prd-row > [role=listitem]').length"), 4)

    def test_copy_menu_on_a_card(self):
        page = self.page()
        self.render(page)
        page.hover(".prd-cell >> nth=0")
        page.click(".prd-cell >> nth=0 >> .prd-copy")
        items = page.evaluate("() => [...document.querySelectorAll('.prd-menu [data-prd-menu]')].map(b => [b.dataset.prdMenu, b.disabled])")
        self.assertEqual(items, [["name", False], ["link", False], ["card", False]])
        self.assertTrue(page.evaluate("() => document.activeElement.dataset.prdMenu === 'name'"))
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("() => window.copied.pop()"), "https://www.dns-shop.ru/product/a1/")  # cheapest verified
        self.assertFalse(page.evaluate("() => !!document.querySelector('.prd-menu')"))
        page.click(".prd-cell >> nth=0 >> .prd-copy")
        page.click(".prd-menu [data-prd-menu=name]")
        self.assertEqual(page.evaluate("() => window.copied.pop()"), "WD Blue SN5000 1 ТБ (WDS100T4B0E)")
        self.assertEqual(page.evaluate("() => window.toasts.pop()"), ["Название скопировано", "ok"])
        # Opening the menu never opens the side sheet.
        self.assertFalse(page.evaluate("() => !!document.querySelector('.prd-sheet.open')"))
        # Esc closes the menu and returns focus to the button.
        page.click(".prd-cell >> nth=1 >> .prd-copy")
        page.keyboard.press("Escape")
        self.assertTrue(page.evaluate("() => !document.querySelector('.prd-menu') && document.activeElement.classList.contains('prd-copy')"))
        # An item with no offer link: the link entry is disabled.
        page.click(".prd-cell >> nth=3 >> .prd-copy")
        self.assertTrue(page.evaluate("() => document.querySelector('.prd-menu [data-prd-menu=link]').disabled"))

    def test_card_text_format(self):
        page = self.page()
        text = page.evaluate("(item) => MemuProducts.cardText(item)", ITEM)
        self.assertEqual(text.split("\n"), [
            "WD Blue SN5000 1 ТБ (WDS100T4B0E)",
            "Лучший выбор · В бюджете",
            "Цена: от 8\u00a0999\u00a0₽ (DNS)",
            "Рейтинг: 4,8 из 5 (1\u00a0250 оценок, DNS)",
            "Главное: PCIe 4.0 x4; до 5150 МБ/с",
            "Кратко: Современный TLC-накопитель.",
            "Плюсы: Холодный; TLC",
            "Минусы: Без DRAM",
            "Где купить:",
            "- DNS: 8\u00a0999\u00a0₽ · в наличии — https://www.dns-shop.ru/product/a1/",
            "- Ozon: 9\u00a0290\u00a0₽ · в наличии — https://www.ozon.ru/product/x-55/",
            "- Wildberries: не удалось проверить",
            "- Ситилинк: найден, цена не подтверждена — https://www.citilink.ru/product/wd-77/",
            "- М.Видео: не найден",
            "Цены и наличие меняются — проверьте у продавца.",
        ])
        approx = page.evaluate("(item) => MemuProducts.cardText(item)", UNVERIFIED)
        self.assertIn("Цена: ≈\u00a06\u00a0500\u00a0₽ (ориентировочно, не подтверждена магазином)", approx)
        self.assertIn("Цена не подтверждена", approx)
        self.assertEqual(page.evaluate("(item) => MemuProducts.nameText(item)", {"name": "WD Blue WDS100T4B0E", "model": "wds100t4b0e"}),
                         "WD Blue WDS100T4B0E")

    def test_side_sheet_copy_and_store_checks(self):
        page = self.page()
        self.render(page)
        page.click(".prd-card >> nth=0")
        page.wait_for_timeout(400)
        rows = page.evaluate("() => [...document.querySelectorAll('.prd-offer')].map(li => [li.querySelector('b').textContent.trim(),"
                             " li.querySelector('small').textContent, li.classList.contains('unchecked'),"
                             " (li.querySelector('a.prd-open') || {}).href || ''])")
        self.assertEqual([r[0] for r in rows], ["DNS", "Ozon", "Wildberries", "Ситилинк", "М.Видео"])
        self.assertEqual([r[2] for r in rows], [False, False, True, True, True])
        self.assertEqual(rows[2][1], "Не удалось проверить")
        self.assertEqual(rows[2][3], "https://www.wildberries.ru/catalog/0/search.aspx?search=WD+Blue")
        self.assertEqual(rows[3][1], "Найден, цена не подтверждена")
        self.assertEqual(rows[3][3], "https://www.citilink.ru/product/wd-77/")
        self.assertEqual(rows[4][3], "")                                  # nothing to open
        self.assertIn("В бюджете", page.evaluate("() => document.querySelector('.prd-price-block .prd-flag').textContent"))
        page.click(".prd-offer >> nth=1 >> [data-prd-copy=link]")
        self.assertEqual(page.evaluate("() => window.copied.pop()"), "https://www.ozon.ru/product/x-55/")
        page.click(".prd-copy-name")
        self.assertEqual(page.evaluate("() => window.copied.pop()"), "WD Blue SN5000 1 ТБ (WDS100T4B0E)")
        page.click(".prd-sheet-head [data-prd-copy-menu]")
        page.click(".prd-menu [data-prd-menu=card]")
        self.assertTrue(page.evaluate("() => window.copied.pop().startsWith('WD Blue SN5000 1 ТБ (WDS100T4B0E)\\nЛучший выбор')"))
        self.assertTrue(page.evaluate("() => document.querySelector('.prd-sheet').classList.contains('open')"))
        # The sheet's Esc still closes the sheet when no menu is open.
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertFalse(page.evaluate("() => document.querySelector('.prd-sheet').classList.contains('open')"))
        shots = os.environ.get("R22_SHOT_DIR")
        if shots:
            page.click(".prd-card >> nth=2")
            page.wait_for_timeout(400)
            page.screenshot(path=str(Path(shots) / "ui-sheet-over-budget-harness.png"))

    def test_keyboard_navigation_between_cards_survives_the_cell_wrapper(self):
        page = self.page()
        self.render(page)
        page.focus(".prd-card >> nth=0")
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.pi"), "1")
        page.keyboard.press("End")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.pi"), "3")


if __name__ == "__main__":
    unittest.main()
