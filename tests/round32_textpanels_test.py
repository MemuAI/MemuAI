"""Round 32 (r32-textpanels): the «text» look of a panel as real text — a user template with placeholders bound to the
panel's values, placed anywhere inside the panel.

Backend: app/quick_panel_look.py (clean_text / clean_look partial / apply_patch) and POST /api/quick/canvas/look on a
temporary profile. Frontend: node runs pgstatic/quick/panel-text.js (parser, filters, escaping, missing values, loops,
default templates per panel type, faces) and static wiring checks. Browser: a headless Chromium render of the real
Quick Access window with text panels and the editor (skipped without Chromium / playwright).

    PYTHONUTF8=1 NO_PROXY='*' python -m pytest -q tests/round32_textpanels_test.py
"""
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r32tp-")

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"
CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")


class TextModel(unittest.TestCase):
    def test_clean_text(self):
        from app import quick_panel_look as L
        text = L.clean_text({"mode": "free", "h": 9999, "halo": "neon", "blocks": [
            {"t": "BTC {price}\r\nx\x07", "x": 80, "y": -5, "w": 50, "weight": 720, "size": 1000, "color": "#ABCDEF", "align": "middle", "font": "comic"},
            "", {"t": 5}, "   ", "{plain}", {"t": "y" * 5000, "color": "red"}]})
        self.assertEqual((text["mode"], text["h"], text["halo"]), ("free", 480, "auto"))
        self.assertEqual(len(text["blocks"]), 3)
        b = text["blocks"][0]
        self.assertEqual(b["t"], "BTC {price}\nx")            # CRLF → LF, control characters dropped
        self.assertEqual((b["x"], b["y"], b["w"]), (80.0, 0, 20.0))  # x + w never leaves the box
        self.assertEqual((b["weight"], b["size"], b["color"], b["align"], b["font"]), (700, 400, "#abcdef", "left", ""))
        self.assertEqual(text["blocks"][1]["t"], "{plain}")
        self.assertEqual(len(text["blocks"][2]["t"]), L.MAX_TEXT_LEN)
        self.assertEqual(text["blocks"][2]["color"], "")
        self.assertIsNone(L.clean_text({"blocks": ["", " "]}))
        self.assertIsNone(L.clean_text("BTC"))
        many = L.clean_text({"blocks": ["{a}"] * 40})
        self.assertEqual(len(many["blocks"]), L.MAX_TEXT_BLOCKS)

    def test_text_only_per_panel_and_patch(self):
        from app import quick_panel_look as L
        self.assertNotIn("text", L.clean_look({"style": "text", "text": {"blocks": ["{a}"]}}))          # not global
        own = L.clean_look({"style": "text", "text": {"blocks": ["{a}"]}}, partial=True)
        self.assertEqual(own["text"]["blocks"][0]["t"], "{a}")
        doc = {"look": dict(L.DEFAULT_LOOK), "looks": {}, "embed": dict(L.DEFAULT_EMBED)}
        L.apply_patch(doc, {"panel": "p_btc", "look": {"style": "text", "text": {"mode": "free", "blocks": [{"t": "BTC {price}", "x": 10}]}}})
        self.assertEqual(doc["looks"]["p_btc"]["text"]["blocks"][0]["x"], 10.0)
        L.apply_patch(doc, {"panel": "p_btc", "look": {"opacity": 50}})                 # another key keeps the template
        self.assertIn("text", doc["looks"]["p_btc"])
        L.apply_patch(doc, {"panel": "p_btc", "look": {"text": None}})                  # back to the automatic one
        self.assertEqual(doc["looks"]["p_btc"], {"style": "text", "opacity": 50})
        L.apply_patch(doc, {"look": {"text": {"blocks": ["{x}"]}}})
        self.assertNotIn("text", doc["look"])
        looks = L.clean_looks({"p_a": {"text": {"blocks": ["{a}"]}}, "p_b": {"text": {"blocks": []}}})
        self.assertEqual(list(looks), ["p_a"])


class TextRoute(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import quick_canvas
        self.path = quick_canvas._path()
        self.backup = self.path.read_bytes() if self.path.exists() else None
        app = FastAPI()
        app.include_router(quick_canvas.router)
        self.client = TestClient(app)

    def tearDown(self):
        if self.backup is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(self.backup)

    def test_persisted(self):
        c = self.client
        text = {"mode": "free", "h": 120, "halo": "strong", "blocks": [{"t": "Зенит — {next_match.opponent}, {next_match.date|date}", "x": 5, "y": 40, "w": 90, "size": 140}]}
        out = c.post("/api/quick/canvas/look", json={"panel": "p_zenit", "look": {"style": "text", "text": text}}).json()
        self.assertEqual(out["looks"]["p_zenit"]["text"]["blocks"][0]["size"], 140)
        c.post("/api/quick/canvas/settings", json={"settings": {"density": "large"}})        # an unrelated save keeps it
        state = c.get("/api/quick/canvas").json()
        saved = state["looks"]["p_zenit"]["text"]
        self.assertEqual((saved["mode"], saved["h"], saved["halo"]), ("free", 120, "strong"))
        self.assertEqual(saved["blocks"][0]["t"], text["blocks"][0]["t"])
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("text", stored["looks"]["p_zenit"])
        c.post("/api/quick/canvas/look", json={"panel": "p_zenit", "look": {"text": None}})
        self.assertNotIn("text", c.get("/api/quick/canvas").json()["looks"].get("p_zenit", {}))


NODE_PRELUDE = r"""
const fs = require('fs');
global.window = global; global.document = { documentElement: { lang: process.env.LANG_UI || 'ru' } };
global.MemuPanels = {
  dataOf: (p) => ({ ...(p.data || {}) }),
  miniOf: (p) => ({ value: p.data && p.data.price ? String(p.data.price) : '', unit: (p.spec && p.spec.mini && p.spec.mini.unit) || '$', label: p.title, trend: '', delta: '' }),
  linesOf: (p) => [{ label: p.title, value: '1', unit: '' }],
};
const T = require(process.argv[1]);
"""


@unittest.skipUnless(shutil.which("node"), "node is needed")
class TemplateEngine(unittest.TestCase):
    def run_node(self, body, lang="ru"):
        res = subprocess.run(["node", "-e", NODE_PRELUDE + body, str(QUICK / "panel-text.js")], capture_output=True,
                             text=True, timeout=30, env={**os.environ, "LANG_UI": lang, "TZ": "Europe/Moscow"})
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.replace("\u00a0", " ").replace("\u202f", " "))

    def test_placeholders_filters_escaping_missing(self):
        out = self.run_node(r"""
const ctx = { price: "67 432,15", change: -2.345, name: "Bitcoin <b>", n: 1234567, items: [{ title: "A <i>", value: "1" }, { title: "B" }, { title: "C", value: 3 }],
  next_match: { opponent: "Спартак", date: "2026-10-14T19:30:00+03:00" }, nums: [1, 2, 3], empty: "", dash: "—", zero: 0, flag: false };
const P = (t) => T.renderPlain(t, ctx, { lang: 'ru' });
console.log(JSON.stringify({
  basic: P("BTC {price} ({change|signed})"), dbl: P("{{price}}"),
  round: P("{price|round:0}|{price|fixed:1}|{change|pct}|{change|abs}|{n|short}|{zero}"),
  nested: P("Зенит — {next_match.opponent}, {next_match.date|date} {next_match.date|time}"),
  index: P("{items[0].title}/{items[-1].title}/{items[5].title}"),
  loop: P("{#items|limit:2}{@n}. {title} — {value ?? 'нет'}\n{/items}"),
  inverted: P("{^missing}пусто{/missing}{#empty}X{/empty}{^items}нет{/items}"),
  missing: P("[{missing}] [{missing|default:\"нет данных\"}] [{missing ?? name}] [{dash}] [{empty|default:'—'}]"),
  literal: P("\\{x} {not valid} {a b} {"), join: P('{nums|join:" / "} {nums|last} {nums|count} {items|pluck:title|join}'),
  unknownFilter: P("{price|zzz}"), bool: P("{flag|yesno}"),
  html: T.render("<script>{name}</script>{items[0].title}", ctx, { lang: 'ru' }),
  missHtml: T.render("{missing}", ctx, { lang: 'ru' }),
  tone: T.render("{change|signed|tone}", ctx, { lang: 'ru' }),
  br: T.render("a\n{name}", ctx, { lang: 'ru' }),
  errors: T.parse("{#items}{x}{/zz}").errors.map((e) => e.code),
  lint: T.lint("{x|nope} {#a}", ctx).map((e) => e.code + ':' + e.name),
  nums: [T.toNum("$67,432.10", 'en'), T.toNum("+2,4 %", 'ru'), T.toNum("1 234,5", 'ru'), T.toNum("−3.1%", 'ru'), T.toNum("abc", 'ru')],
  dates: [T.toDate("Fri 2027-01-01 3:00 AM MSK (UTC+03:00)").getTime(), T.toDate("01.01.2027") && T.toDate("01.01.2027").getFullYear(), T.toDate("hello 2026"), T.toDate(1791972000).getTime()],
  budget: T.renderPlain("{#items}{#.}{title}{/.}{/items}".repeat(200), ctx).length <= 6200,
}));""")
        self.assertEqual(out["basic"], "BTC 67 432,15 (−2,35)")
        self.assertEqual(out["dbl"], "67 432,15")
        self.assertEqual(out["round"], "67 432|67 432,2|-2,3%|2,35|1,23 млн|0")
        self.assertEqual(out["nested"], "Зенит — Спартак, 14 окт. 19:30")
        self.assertEqual(out["index"], "A <i>/C/—")
        self.assertEqual(out["loop"], "1. A <i> — 1\n2. B — нет\n")
        self.assertEqual(out["inverted"], "пусто")
        self.assertEqual(out["missing"], "[—] [нет данных] [Bitcoin <b>] [—] [—]")
        self.assertEqual(out["literal"], "{x} {not valid} {a b} {")
        self.assertEqual(out["join"], "1 / 2 / 3 3 3 A <i>, B, C")
        self.assertEqual(out["unknownFilter"], "67 432,15")
        self.assertEqual(out["bool"], "нет")
        self.assertNotIn("<script>", out["html"])
        self.assertNotIn("<b>", out["html"])
        self.assertIn("&lt;script&gt;", out["html"])
        self.assertIn("&lt;i&gt;", out["html"])
        self.assertIn('class="pt-miss"', out["missHtml"])
        self.assertIn('class="pt-v pt-down"', out["tone"])
        self.assertIn("<br>", out["br"])
        self.assertEqual(out["errors"], ["close", "open"])
        self.assertIn("filter:nope", out["lint"])
        self.assertIn("open:a", out["lint"])
        self.assertIn("missing:x", out["lint"])
        self.assertEqual(out["nums"], [67432.1, 2.4, 1234.5, -3.1, None])
        self.assertEqual(out["dates"][0], 1798761600000)
        self.assertEqual(out["dates"][1], 2027)
        self.assertIsNone(out["dates"][2])
        self.assertEqual(out["dates"][3], 1791972000000)
        self.assertTrue(out["budget"])

    def test_defaults_per_type(self):
        out = self.run_node(r"""
const panels = {
  crypto: { id: 'p_c', kind: 'crypto', title: 'Bitcoin', data: { name: 'Bitcoin', symbol: 'btc', price: '82 976', vs_sign: '$', delta: '+0,30%', trend: 'up' }, spec: {} },
  currency: { id: 'p_fx', kind: 'currency', title: 'USD → RUB', data: { rate: '84,90', delta: '−0,51', date_label: 'на 10.10.2026' }, spec: { mini: { unit: '₽' } } },
  weather: { id: 'p_w', kind: 'weather', title: 'Погода', data: { place: 'Москва', temp: 7, t_unit: '°C', cond: 'Облачно', feels: 4 }, spec: {} },
  countdown: { id: 'p_cd', kind: 'countdown', title: 'Новый год', data: { days_left: 81, target_label: '01.01.2027' }, spec: {} },
  rss: { id: 'p_r', kind: 'rss', title: 'HN', data: { feed_title: 'Hacker News', items: [{ title: 'One' }, { title: 'Two' }, { title: 'Three' }, { title: 'Four' }] }, spec: {} },
  sports: { id: 'p_s', kind: 'sports', title: 'Зенит', data: { headline: 'Краснодар — Зенит', competition: 'РПЛ', score: '2:3', last_label: '10 октября' },
    spec: { blocks: [{ type: 'title', text: '{{headline}}', subtitle: '{{competition}}' }, { type: 'metric', value: '{{score}}', label: '{{last_label}}' }, { type: 'text', text: '{{next_label}}', muted: true }], mini: { value: '{{score}}', label: 'Зенит' } } },
  custom: { id: 'p_g', kind: 'custom', title: 'Золото', data: { price: 4193.76, change: '+49,17' },
    spec: { blocks: [{ type: 'title', text: 'Золото (XAU/USD)' }, { type: 'metric', value: '{{price}}', unit: '$', label: 'за унцию', delta: '{{change}}' }], mini: { value: '{{price}}', unit: '$' } } },
  news: { id: 'p_n', kind: 'custom', title: 'Новости', data: { headlines: [{ title: 'H1', value: '5' }, { title: 'H2' }] },
    spec: { blocks: [{ type: 'title', text: 'Новости' }, { type: 'list', items: '{{headlines}}' }] } },
  empty: { id: 'p_e', kind: 'custom', title: 'Пусто', data: { x: 1 }, spec: { blocks: [] } },
};
const res = {};
for (const [k, p] of Object.entries(panels)) {
  const d = T.defaultText(p, { lang: 'ru' });
  const ctx = T.contextOf(p);
  res[k] = { mode: d.mode, auto: d.auto, lines: d.blocks.map((b) => T.renderPlain(b.t, ctx, { lang: 'ru' }).trim()), size: d.blocks.map((b) => b.size),
    face: T.faceHtml(p, { style: 'text' }, { lang: 'ru' }), noTitle: T.faceHtml(p, { style: 'text' }, { lang: 'ru', titles: false }),
    plain: T.plainText(p, { style: 'text' }, { lang: 'ru' }) };
}
const own = T.faceHtml(panels.crypto, { style: 'text', text: { mode: 'free', h: 120, halo: 'strong', blocks: [{ t: 'BTC {price}', x: 10, y: 20, w: 60, size: 200, weight: 700, color: 'accent', align: 'right' }] } }, { lang: 'ru' });
res.own = own;
res.ph = T.placeholders(panels.rss).map((x) => x.path + ':' + x.kind);
console.log(JSON.stringify(res));""")
        self.assertEqual(out["crypto"]["lines"], ["Bitcoin · BTC", "82 976 $", "+0,30% за 24 ч"])
        self.assertEqual(out["currency"]["lines"][1:], ["84,90 ₽", "−0,51 · на 10.10.2026"])
        self.assertEqual(out["weather"]["lines"], ["Москва", "7°C", "Облачно · ощущается 4°"])
        self.assertEqual(out["countdown"]["lines"][1:], ["81 дн.", "01.01.2027"])
        self.assertEqual(out["rss"]["lines"], ["Hacker News", "One\nTwo\nThree"])
        self.assertEqual(out["sports"]["lines"], ["Зенит", "Краснодар — Зенит", "2:3", "10 октября\nРПЛ"])   # no lone «—» line
        self.assertEqual(out["custom"]["lines"], ["Золото (XAU/USD)", "4 193,76 $", "+49,17 · за унцию"])
        self.assertEqual(out["news"]["lines"][1], "H1 — 5\nH2")
        self.assertTrue(out["empty"]["lines"])
        for k in ("crypto", "sports", "custom", "weather"):
            self.assertTrue(out[k]["auto"])
            self.assertEqual(out[k]["mode"], "flow")
            self.assertGreater(max(out[k]["size"]), 150, k)                   # the main value is the big one
            self.assertIn('class="pl-face pl-text pt-face"', out[k]["face"])
            self.assertIn('data-auto="1"', out[k]["face"])
            self.assertEqual(out[k]["noTitle"].count('class="pt-b"'), out[k]["face"].count('class="pt-b"') - 1)
        self.assertEqual(out["crypto"]["plain"], "Bitcoin · BTC\n82 976 $\n+0,30% за 24 ч")
        own = out["own"]
        for frag in ('data-mode="free"', 'data-halo="strong"', "--pt-h:120px", "left:10%", "top:20%", "min-width:60%", "max-width:90%", "text-align:right",
                     "font-weight:700", "color:var(--accent)", "--pt-size:2"):
            self.assertIn(frag, own)
        self.assertIn("items:list", out["ph"])
        self.assertIn("items[0].title:value", out["ph"])
        self.assertIn("feed_title:value", out["ph"])

    def test_english(self):
        out = self.run_node(r"""
const p = { id: 'p_c', kind: 'crypto', title: 'Bitcoin', data: { name: 'Bitcoin', symbol: 'BTC', price: '82,976', vs_sign: '$', delta: '+0.30%' }, spec: {} };
console.log(JSON.stringify({ plain: T.plainText(p, { style: 'text' }, { lang: 'en' }), num: T.renderPlain('{n|short} {n|num}', { n: 1234567 }, { lang: 'en' }), rel: T.renderPlain('{d|rel}', { d: Date.now() + 3 * 3600e3 }, { lang: 'en' }) }));""", lang="en")
        self.assertEqual(out["plain"], "Bitcoin · BTC\n82,976 $\n+0.30% in 24h")
        self.assertEqual(out["num"], "1.23M 1,234,567")
        self.assertEqual(out["rel"], "in 3 h")


class Wiring(unittest.TestCase):
    def read(self, rel):
        return (ROOT / "app" / "pgstatic" / rel).read_text(encoding="utf-8")

    def test_static(self):
        look = self.read("quick/panel-look.js")
        self.assertIn("MemuPanelText.faceHtml(panel, look", look)
        self.assertIn('panel-text.js" + v', look)
        self.assertIn("dataOf: (panel)", self.read("panels.js"))
        layer = self.read("quick/layer.html")
        self.assertLess(layer.index("panel-text.js"), layer.index("panel-look.js"))
        embed = self.read("quick/plugins/canvas-embed.js")
        for frag in ('"text-edit"', "MemuPanelTextEditor.mount", 'registerPanel("panel-text-edit"', '"memu-panel-text"', "new ResizeObserver"):
            self.assertIn(frag, embed)
        settings = self.read("quick/plugins/canvas-look-settings.js")
        self.assertIn("data-qls-text", settings)
        self.assertIn("MemuPanelTextEditor.mount", settings)
        index = json.loads(self.read("quick/plugins/index.json"))["plugins"]
        self.assertEqual(next(r for r in index if r["id"] == "canvas-embed")["v"], "1.2-r32")
        for rel in ("quick/panel-text-editor.js", "quick/plugins/canvas-embed.js", "quick/plugins/canvas-look-settings.js", "quick/panel-text.js"):
            src = self.read(rel)
            self.assertNotRegex(src, r'tr\("[^"]*[А-Яа-я][^"]*"\)', rel)       # every Russian string has its English pair
            self.assertNotRegex(src, r't\("[^"]*[А-Яа-я][^"]*"\)', rel)
        editor = self.read("quick/panel-text-editor.js")
        # English strings of the editor carry no Cyrillic
        import re
        for m in re.finditer(r'tr\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', editor):
            self.assertNotRegex(m.group(2), r"[А-Яа-яЁё]", m.group(2))
        for m in re.finditer(r'\["[^"]+", "((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\]', editor):
            self.assertNotRegex(m.group(2), r"[А-Яа-яЁё]", m.group(2))


@unittest.skipUnless(os.path.exists(CHROME), "chromium is needed")
class BrowserRender(unittest.TestCase):
    """The real Quick Access window (app.quick_canvas + MemuAI panel routes on a temporary profile) with a text panel
    built into it: the template renders as text, values are escaped, the editor opens, a drag places a block freely and
    saving persists it."""

    def test_window_and_editor(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is needed")
        res = subprocess.run([sys.executable, str(Path(__file__).with_name("round32_textpanels_browser.py"))],
                             capture_output=True, text=True, timeout=240, env={**os.environ, "NO_PROXY": "*", "PYTHONUTF8": "1"})
        self.assertEqual(res.returncode, 0, res.stdout[-3000:] + res.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
