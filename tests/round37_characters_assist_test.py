# -*- coding: utf-8 -*-
"""Round 37 — «Characters»: the AI co-writer (assist.js + assist-prompts.js).

No network: the module runs in Chromium inside the Round 29 harness (mock MemuAI host, fake streaming Memu.complete).

* the parts are inlined into main.js (modules_host «@memu-part»), i18n has every new key in ru + en;
* ✨ on every card field opens the action menu; «Improve» streams into a preview with a word diff, nothing changes
  until «Accept»; Accept goes into the field (the card data follows) and Ctrl+Z undoes it;
* a selection is rewritten alone («Replace the selection»), «Continue» appends, «3 variants» parses JSON;
* Esc closes the preview without closing the editor;
* the side panel: chat with a <field> proposal → «Apply to …» → preview on that field (other tab too);
  «Review» = local checks + model issues, «Fix» runs through the preview;
* lorebook: keyword chips, «find gaps» proposals added one by one;
* the prompt builder keeps macros, sends the card as context and the selection only;
* the co-writer model setting (empty = chat model) is what Memu.complete receives.
    python3 tests/round37_characters_assist_test.py /tmp/shots  — screenshots instead of the tests
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

from tests import round29_characters_harness as harness  # noqa: E402

MODULE = ROOT / "app" / "module_builtins" / "characters"

MODELS = json.dumps([
    {"id": "reform-qwen3.8-flash", "name": "Qwen3.8 Flash", "context": 128000, "default": True},
    {"id": "helper/model-b", "name": "Helper B", "context": 32000},
])
PARENT = harness.PARENT.replace(
    'case "models": return reply(d.id, [{ id: "test/model-a", name: "Model A", context: 128000 }, { id: "img/x", name: "Img", image: true }]);',
    'case "models": return reply(d.id, ' + MODELS + ');',
).replace(
    "const text = window.__reply;",
    "window.__n = (window.__n || 0) + 1; const text = window.__replyFor ? window.__replyFor(p, window.__n) : window.__reply;",
)

CARD = {
    "name": "Mira", "description": "{{char}} is a lighthouse keeper on a cold northern island. She talks to the gulls.",
    "personality": "quiet, stubborn, kind", "scenario": "", "first_mes": "*Mira opens the door.* You came in the storm, {{user}}?",
    "mes_example": "", "tags": ["drama"],
}


class AssistStaticTest(unittest.TestCase):
    def test_parts_are_inlined_in_order(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        a, b, c = (source.index("/* @memu-part:" + n + " */") for n in ("ai-create.js", "assist-prompts.js", "assist.js"))
        self.assertTrue(a < b < c < source.index("function boot()"))
        from app.modules_host import load_package
        with tempfile.TemporaryDirectory() as temp:
            js = load_package(Path(temp), "characters")["js"]
        self.assertNotIn("/* @memu-part:", js)
        self.assertIn("function assistPreview(", js)
        self.assertIn("var ASSIST_FIELD_GUIDE", js)

    def test_i18n_both_languages(self):
        data = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        import re
        used = set(re.findall(r'"(as_[a-z_]+)"', (MODULE / "assist.js").read_text(encoding="utf-8") + (MODULE / "main.js").read_text(encoding="utf-8")))
        used |= {"as_" + k for k in re.findall(r'"(tone_[a-z]+)"', (MODULE / "assist-prompts.js").read_text(encoding="utf-8"))}
        used |= {k + "_full" for k in used if k.startswith("as_q_")} | {"as_sev_high", "as_sev_medium", "as_sev_low"}
        used |= {k + "_sub" for k in ("as_gaps", "as_conflicts", "as_from_text")}
        missing = [k for k in sorted(used) if not k.endswith("_") and (k not in data["ru"] or k not in data["en"])]
        self.assertEqual(missing, [])
        cyr = re.compile("[А-Яа-яЁё]")
        self.assertEqual([k for k, v in data["en"].items() if k.startswith("as_") and cyr.search(v)], [])

    def test_default_settings(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        self.assertIn('assist: { model: "", lang: "auto", buttons: true }', source)


def open_page(pw, lang="en", size=(1440, 900)):
    browser = pw.chromium.launch(executable_path=harness.chromium(), args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    doc = harness.module_html(lang, "dark")
    parent = PARENT.replace("__ASSETS__", json.dumps(harness.assets(), ensure_ascii=False))
    errors: list = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=parent))
    page.route("http://harness.local/module", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
    page.goto("http://harness.local/")
    page.wait_for_timeout(1200)
    frame = harness.frame_of(page)
    frame.evaluate("() => { window.M = window.MemuCharacters; }")
    return browser, page, frame, errors


@unittest.skipUnless(sync_playwright and harness.chromium(), "playwright + chromium required")
class AssistBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert "window.__replyFor" in PARENT and "Helper B" in PARENT, "harness changed: update the replacements"
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.frame, cls.errors = open_page(cls.pw)
        cls.frame.evaluate("""(card) => { const c = M.newCharacter(card); c.id = 'c_mira'; return M.saveCharacter(c); }""", CARD)
        cls.frame.evaluate("() => M.openCharacterEditor('c_mira')")
        cls.frame.wait_for_selector(".ch-editor [data-assist='description']")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def ev(self, script, arg=None):
        return self.frame.evaluate(script, arg)

    def wait(self, script, timeout=8000):
        for _ in range(timeout // 100):
            if self.frame.evaluate(script):
                return True
            self.page.wait_for_timeout(100)
        self.fail("timeout waiting for: " + script)

    def reply(self, text):
        self.page.evaluate("(t) => { window.__replyFor = () => t; }", text)

    def sent(self):
        return self.page.evaluate("() => window.__sent")

    def open_menu(self, key):
        self.frame.hover(f"[data-assist='{key}']")
        self.frame.click(f".ch-field:has([data-assist='{key}']) .ch-as-btn")
        self.frame.wait_for_selector(".ch-menu")

    def test_1_improve_preview_accept_undo(self):
        before = self.ev("() => document.querySelector(\"[data-assist='description']\").value")
        self.reply("{{char}} is a weathered lighthouse keeper on a cold northern island. She talks to the gulls as if they were old friends.")
        self.open_menu("description")
        labels = self.ev("() => [...document.querySelectorAll('.ch-menu .ch-menu-item')].map(b => b.textContent.trim())")
        for name in ("Improve", "Continue", "Expand", "Shorten", "Fix mistakes", "Suggest 3 variants", "Own request…"):
            self.assertTrue(any(l.startswith(name) for l in labels), (name, labels))
        self.frame.click(".ch-menu .ch-menu-item:has-text('Improve')")
        self.frame.wait_for_selector(".ch-as-pv .ch-as-pv-foot:not([hidden]) .primary")
        self.assertTrue(self.ev("() => !!document.querySelector('.ch-as-diff ins')"))
        # nothing changed yet
        self.assertEqual(self.ev("() => document.querySelector(\"[data-assist='description']\").value"), before)
        req = self.sent()[-1]
        self.assertEqual(req["model"], "reform-qwen3.8-flash")
        self.assertIn("Field being edited: Description", req["messages"][1]["content"])
        self.assertIn("lighthouse keeper", req["messages"][1]["content"])
        self.assertIn("{{char}}", req["messages"][0]["content"])
        self.frame.click(".ch-as-pv .primary")
        self.wait("() => !document.querySelector('.ch-as-pv')")
        self.assertIn("weathered", self.ev("() => document.querySelector(\"[data-assist='description']\").value"))
        self.page.keyboard.press("Control+z")
        self.wait("() => document.querySelector(\"[data-assist='description']\").value.indexOf('weathered') < 0")

    def test_2_selection_only_and_esc(self):
        self.reply("chats with")
        self.ev("""() => { const el = document.querySelector("[data-assist='description']"); const i = el.value.indexOf('talks to'); el.focus(); el.setSelectionRange(i, i + 8); }""")
        self.open_menu("description")
        self.assertIn("Selection", self.ev("() => document.querySelector('.ch-menu .ch-menu-head').textContent"))
        self.frame.click(".ch-menu .ch-menu-item:has-text('Improve')")
        self.frame.wait_for_selector(".ch-as-pv .ch-as-pv-foot:not([hidden]) .primary")
        content = self.sent()[-1]["messages"][1]["content"]
        self.assertIn("rewrite ONLY it\ntalks to", content)
        self.assertIn("Replace the selection", self.ev("() => document.querySelector('.ch-as-pv .primary').textContent"))
        self.frame.click(".ch-as-pv .primary")
        self.wait("() => document.querySelector(\"[data-assist='description']\").value.indexOf('She chats with the gulls') >= 0")
        # Esc closes the preview, not the editor
        self.reply("x")
        self.open_menu("first_mes")
        self.frame.click(".ch-menu .ch-menu-item:has-text('Fix mistakes')")
        self.frame.wait_for_selector(".ch-as-pv")
        self.page.keyboard.press("Escape")
        self.wait("() => !document.querySelector('.ch-as-pv')")
        self.assertTrue(self.ev("() => !!document.querySelector('.ch-editor')"))

    def test_3_variants_and_continue(self):
        self.reply('{"variants": ["Version one.", "Version two.", "Version three."]}')
        self.open_menu("scenario")
        self.frame.click(".ch-menu .ch-menu-item:has-text('Suggest 3 variants')")
        self.frame.wait_for_selector(".ch-as-var")
        self.assertEqual(self.ev("() => document.querySelectorAll('.ch-as-var').length"), 3)
        self.frame.click(".ch-as-var:nth-child(2)")
        self.frame.click(".ch-as-pv .primary")
        self.wait("() => document.querySelector(\"[data-assist='scenario']\").value === 'Version two.'")
        self.reply("The storm rattles the glass.")
        self.open_menu("first_mes")
        self.frame.click(".ch-menu .ch-menu-item:has-text('Continue')")
        self.frame.wait_for_selector(".ch-as-pv .ch-as-pv-foot:not([hidden]) .primary")
        self.frame.click(".ch-as-pv .primary")
        self.wait("() => /\\{\\{user\\}\\}\\? The storm rattles the glass\\.$/.test(document.querySelector(\"[data-assist='first_mes']\").value)")

    def test_4_panel_chat_apply_to_other_tab(self):
        self.reply('Here is an idea.\n<field name="personality">quiet, stubborn, secretly afraid of the dark</field>')
        self.frame.click(".ch-as-open")
        self.frame.wait_for_selector(".ch-as-panel.open")
        self.frame.fill(".ch-as-input", "Improve her personality")
        self.frame.press(".ch-as-input", "Enter")
        self.frame.wait_for_selector(".ch-as-prop .mu-btn")
        system = self.sent()[-1]["messages"][0]["content"]
        self.assertIn("<field name=", system)
        self.assertIn("lighthouse keeper", system)
        self.frame.click(".ch-as-prop .mu-btn")
        self.frame.wait_for_selector(".ch-as-pv .primary")
        self.frame.click(".ch-as-pv .primary")
        self.wait("() => document.querySelector(\"[data-assist='personality']\").value.indexOf('afraid of the dark') >= 0")

    def test_5_review_local_and_model(self):
        self.ev("() => { const el = document.querySelector(\"[data-assist='mes_example']\"); el.value = '{{char}}: hello'; el.dispatchEvent(new Event('input')); }")
        self.reply('{"issues": [{"field": "personality", "severity": "high", "title": "Contradiction", "detail": "Kind vs cold.", "fix": "Make it consistent."}]}')
        self.frame.click(".ch-as-tab:has-text('Review')")
        self.frame.click(".ch-as-review-top .mu-btn")
        self.wait("() => [...document.querySelectorAll('.ch-as-issue')].some(r => r.textContent.indexOf('Contradiction') >= 0)")
        text = self.ev("() => document.querySelector('.ch-as-issues').textContent")
        self.assertIn("<START>", text)          # local check
        self.reply("quiet, stubborn, kind to everyone")
        self.frame.click(".ch-as-issue.high .mu-btn:has-text('Fix')")
        self.frame.wait_for_selector(".ch-as-pv .primary")
        self.assertIn("Make it consistent.", self.sent()[-1]["messages"][1]["content"])
        self.page.keyboard.press("Escape")

    def test_6_model_setting(self):
        self.ev("() => { M.S.settings.assist.model = 'helper/model-b'; }")
        self.reply("Mira")
        self.frame.click(".ch-editor .mu-tab >> nth=0")
        self.open_menu("name")
        self.frame.click(".ch-menu .ch-menu-item:has-text('Improve')")
        self.frame.wait_for_selector(".ch-as-pv .primary")
        self.assertEqual(self.sent()[-1]["model"], "helper/model-b")
        self.page.keyboard.press("Escape")
        self.ev("() => { M.S.settings.assist.model = ''; }")

    def test_7_pure_helpers(self):
        diff = self.ev("() => M.assistDiff('a quick fox', 'a slow fox')")
        self.assertEqual(diff, [["=", "a "], ["-", "quick"], ["+", "slow"], ["=", " fox"]])
        self.assertEqual(self.ev("() => M.assistClean('<think>hm</think>```\\nHello {{user}}\\n```')"), "Hello {{user}}")
        self.assertEqual(self.ev("() => M.assistClean('\u00abQuoted\u00bb')"), "Quoted")
        checks = self.ev("() => M.assistLocalChecks({name: 'Bob', description: '{user} meets Bob. Bob waves. Bob smiles.', first_mes: '', mes_example: 'hi'})")
        titles = " | ".join(c["title"] for c in checks)
        self.assertIn("empty", titles)
        self.assertIn("{user}", titles)
        self.assertIn("<START>", titles)
        self.assertIn("literally", titles)

    def test_8_lorebook_keys_and_gaps(self):
        self.frame.evaluate("() => { document.querySelectorAll('.ch-modal-wrap').forEach(w => w.remove()); }")
        wid = self.ev("""() => { const w = M.E.importWorld({entries: {0: {uid: 0, comment: 'The Lighthouse', key: ['lighthouse'], content: 'An old stone tower. Captain Rell built it.'}}}, 'Isle').world;
            w.id = 'w_isle'; w.name = 'Isle'; M.S.worlds[w.id] = w; M.S.worldIndex.push({id: w.id, name: w.name}); return w.id; }""")
        self.ev("(id) => M.openWorldEditor(id)", wid)
        self.frame.wait_for_selector(".ch-entry-head")
        self.frame.click(".ch-entry-head .ch-mini")
        self.frame.wait_for_selector("[data-assist-entry='0']")
        self.reply('{"keys": ["lighthouse", "tower", "beacon"]}')
        self.frame.hover("[data-assist-entry='0']")
        self.frame.click(".ch-entry-body .ch-field:has(.ch-label:text-is('Keywords')) .ch-as-btn")
        self.frame.wait_for_selector(".ch-as-chip")
        chips = self.ev("() => [...document.querySelectorAll('.ch-as-chip:not(.ghost)')].map(c => c.textContent)")
        self.assertEqual(chips, ["tower", "beacon"])     # already-present keys are dropped
        self.frame.click(".ch-as-chip:text-is('beacon')")
        self.frame.click(".ch-as-keys .primary")
        self.wait("() => JSON.stringify(M.S.worlds.w_isle.entries[0].key) === JSON.stringify(['lighthouse', 'beacon'])")
        self.reply('{"entries": [{"title": "Captain Rell", "keys": ["Rell"], "content": "The builder of the lighthouse.", "why": "named in The Lighthouse"}]}')
        self.frame.click(".ch-as-open")
        self.frame.click(".ch-as-tab:has-text('Tools')")
        self.frame.click(".ch-as-tool:has-text('Find gaps')")
        self.frame.wait_for_selector(".ch-as-ent .primary")
        self.frame.click(".ch-as-ent .primary")
        self.wait("() => Object.values(M.S.worlds.w_isle.entries).some(e => e.comment === 'Captain Rell' && e.key[0] === 'Rell')")

    def test_9_no_console_errors(self):
        self.assertEqual([e for e in self.errors if "favicon" not in e], [])


def shots(out: Path, lang: str):
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, page, frame, errors = open_page(pw, lang)
        frame.evaluate("""(card) => { const c = M.newCharacter(card); c.id = 'c_mira'; return M.saveCharacter(c); }""", CARD)
        frame.evaluate("() => M.openCharacterEditor('c_mira')")
        frame.wait_for_selector("[data-assist='description']")
        page.wait_for_timeout(400)
        frame.hover("[data-assist='description']")
        frame.click(".ch-field:has([data-assist='description']) .ch-as-btn")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"assist-menu-{lang}.png"))
        page.evaluate("(t) => { window.__replyFor = () => t; }", "{{char}} is a weathered lighthouse keeper on a cold northern island at the edge of the map. She talks to the gulls as if they were old friends and keeps a logbook of every ship that never arrived.")
        frame.click(".ch-menu .ch-menu-item >> nth=0")
        frame.wait_for_selector(".ch-as-pv .primary")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"assist-preview-{lang}.png"))
        page.evaluate("(t) => { window.__replyFor = () => t; }", "**What is missing:**\n- a reason she stays on the island\n- how she treats strangers\n\n<field name=\"scenario\">A winter storm strands {{user}} on Mira's island; the supply boat is a week away.</field>")
        frame.click(".ch-as-open")
        frame.fill(".ch-as-input", "What is missing?")
        frame.press(".ch-as-input", "Enter")
        frame.wait_for_selector(".ch-as-prop .mu-btn")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"assist-panel-{lang}.png"))
        page.evaluate("(t) => { window.__replyFor = () => t; }", json.dumps({"issues": [{"field": "personality", "severity": "high", "title": "Kind but cold", "detail": "The personality says kind, the first message is curt.", "fix": "x"}]}))
        frame.click(".ch-as-tab >> nth=1")
        frame.click(".ch-as-review-top .mu-btn")
        frame.wait_for_selector(".ch-as-issue.high")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"assist-review-{lang}.png"))
        print("errors:", errors)
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        for lang in ("ru", "en"):
            shots(Path(sys.argv[1]), lang)
    else:
        unittest.main()
