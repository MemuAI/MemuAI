"""Round 34 (r34-main-chat): the main chat window — no network.

* patchMessageNodes (app.js): an unchanged message keeps its DOM node; changed / streaming / voice / visual ones are
  replaced; added and removed messages; «msg-new» only dropped; odd markup → full rebuild (headless Chromium);
* renderMessages binds buttons only on the re-rendered nodes, MemuMedia / MemuMediaCache hydrate only those;
* one waiting state instead of «Подготавливаю контекст…» + «Думает» + «MemuAI отвечает» at once;
* the chat title from the first message (shortChatTitle): no /command, cut on a word with «…» (Node);
* primaryModelScore / ensurePrimaryModelsPinned: newer flagships, -vl penalty, one flagship per Custom connection (Node);
* Settings: the filter field of a searchable select is not the row value (no false «changed»);
* theme variant cards align to the top; .mpick value ends with «…»; Reflection opens on «This month»;
* focus: one-field dialogs, «Edit message», model picker after a choice / Esc; cache versions in index.html.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
APP_JS = (PG / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
CHROME = Path(os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome")

def js_function(name: str) -> str:
    start = APP_JS.index(f"function {name}(")
    end = APP_JS.index("\n}\n", start) + 3
    return APP_JS[start:end]

def run_node(script: str):
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)

@unittest.skipUnless(NODE, "node is not installed")
class PureFunctionsTest(unittest.TestCase):
    def test_short_chat_title(self):
        got = run_node(js_function("shortChatTitle") + """
const cases = ["/image кот в космосе, акварель, мягкий свет, высокая детализация", "привет", "  много   пробелов  ",
  "Как сделать так, чтобы    код работал быстрее в Python и не тормозил", "x".repeat(70), "/video закат над морем"];
console.log(JSON.stringify(cases.map((c) => shortChatTitle(c))));""")
        self.assertEqual(got[0], "кот в космосе, акварель, мягкий свет, высокая…")
        self.assertEqual(got[1], "привет")
        self.assertEqual(got[2], "много пробелов")
        self.assertEqual(got[3], "Как сделать так, чтобы код работал быстрее в…")
        self.assertEqual(got[4], "x" * 48 + "…")
        self.assertEqual(got[5], "закат над морем")
        self.assertIn("shortChatTitle((S.messages.find(", js_function("fallbackChatTitle"))

    def test_primary_model_score(self):
        got = run_node("const pref = () => ''; " + js_function("primaryModelScore") + """
const s = (id) => primaryModelScore({ id });
console.log(JSON.stringify({ gpt6: s("aws9-gpt-6.1-sol"), vl: s("reform-qwen3-vl-plus"), grok5: s("grok-5"),
  ds4: s("deepseek-v4.1"), mini: s("gpt-6-mini"), sonnet: s("claude-sonnet-4.6") }));""")
        self.assertGreaterEqual(got["gpt6"], 100)
        self.assertGreaterEqual(got["grok5"], 100)
        self.assertGreaterEqual(got["ds4"], 100)
        self.assertLess(got["vl"], 0)
        self.assertGreater(got["gpt6"], got["mini"])
        self.assertGreaterEqual(got["sonnet"], 100)
        self.assertIn('model.provider === "custom" && (model.prefix || model.account)', js_function("ensurePrimaryModelsPinned"))

PATCH_SCRIPT = r"""
async () => {
  const box = document.createElement("div"); box.id = "messages"; document.body.appendChild(box);
  window.S = { messages: [{}, {}, {}] };
  const html = (i, text, extra = "") => `<div class="msg${extra}" data-msg="${i}"><div class="body">${text}</div><button class="b">x</button></div>`;
  const out = {};
  out.first = patchMessageNodes(box, [html(0, "a", " msg-new"), html(1, "b", " msg-new"), html(2, "c", " msg-new")]);
  const n0 = box.children[0], n1 = box.children[1], n2 = box.children[2];
  n1.querySelector(".b").onclick = () => { window.clicked = true; };
  // the entrance class is only dropped: same nodes, nothing fresh
  out.dropNew = patchMessageNodes(box, [html(0, "a"), html(1, "b"), html(2, "c")]).length;
  out.keptAll = box.children[0] === n0 && box.children[1] === n1 && box.children[2] === n2;
  out.newGone = !box.querySelector(".msg-new");
  // one message changed (an edit): only it is replaced, the kept node keeps its handler
  const fresh = patchMessageNodes(box, [html(0, "a"), html(1, "b"), html(2, "C!")]);
  out.changed = fresh.length; out.changedIsNew = fresh[0] === box.children[2] && box.children[2] !== n2;
  box.children[1].querySelector(".b").click(); out.handlerKept = !!window.clicked;
  // streaming answers are always rebuilt
  S.messages[2] = { streaming: true };
  out.streaming = patchMessageNodes(box, [html(0, "a"), html(1, "b"), html(2, "C!")]).length;
  S.messages[2] = {};
  // voice / visual hosts are always rebuilt (their stash/hydrate expects new nodes)
  box.children[0].insertAdjacentHTML("beforeend", '<div class="voice-message"></div>');
  out.voice = patchMessageNodes(box, [html(0, "a"), html(1, "b"), html(2, "C!")]).length;
  // a message appended, then one removed (indices shift → markup changes)
  S.messages.push({});
  out.appended = patchMessageNodes(box, [html(0, "a"), html(1, "b"), html(2, "C!"), html(3, "d")]).length;
  out.count4 = box.children.length;
  S.messages.splice(1, 1);
  out.removed = patchMessageNodes(box, [html(0, "a"), html(1, "C!"), html(2, "d")]).length;
  out.count3 = box.children.length; out.order = [...box.children].map((n) => n.dataset.msg + n.textContent.replace("x", "")).join(",");
  // markup that is not exactly one element → a full rebuild (null)
  out.odd = patchMessageNodes(box, [html(0, "a"), "<p>1</p><p>2</p>", html(2, "d")]);
  out.oddCount = box.children.length;
  // a foreign child (no stamp) → a full rebuild
  box.appendChild(document.createElement("div"));
  out.foreign = patchMessageNodes(box, [html(0, "a")]); out.foreignCount = box.children.length;
  return out;
}
"""

@unittest.skipUnless(CHROME.exists(), "headless Chromium is not available")
class PatchMessageNodesTest(unittest.TestCase):
    def test_patch(self):
        from playwright.async_api import async_playwright

        async def go():
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
                page = await browser.new_page()
                await page.set_content("<html><body></body></html>")
                await page.add_script_tag(content=js_function("patchMessageNodes"))
                result = await page.evaluate(PATCH_SCRIPT)
                await browser.close()
                return result

        r = asyncio.run(go())
        self.assertIsNone(r["first"])
        self.assertEqual(r["dropNew"], 0)
        self.assertTrue(r["keptAll"])
        self.assertTrue(r["newGone"])
        self.assertEqual(r["changed"], 1)
        self.assertTrue(r["changedIsNew"])
        self.assertTrue(r["handlerKept"])
        self.assertEqual(r["streaming"], 1)
        self.assertEqual(r["voice"], 1)
        self.assertEqual(r["appended"], 1)
        self.assertEqual(r["count4"], 4)
        self.assertEqual(r["removed"], 2)
        self.assertEqual(r["count3"], 3)
        self.assertEqual(r["order"], "0a,1C!,2d")
        self.assertIsNone(r["odd"])
        self.assertEqual(r["oddCount"], 4)
        self.assertIsNone(r["foreign"])
        self.assertEqual(r["foreignCount"], 1)

class WiringTest(unittest.TestCase):
    def test_render_messages_binds_only_fresh_nodes(self):
        render = js_function("renderMessages")
        self.assertIn("const fresh = patchMessageNodes(box, S.messages.map(", render)
        self.assertIn("MemuMedia.hydrate(box, fresh)", render)
        self.assertIn("(fresh || [box]).forEach((node) => MemuMediaCache.hydrate(node))", render)
        self.assertIn('const $$ = (sel) => fresh ? fresh.flatMap(', render)
        media = (PG / "media-routing.js").read_text(encoding="utf-8")
        self.assertIn("function hydrate(box, roots)", media)
        self.assertNotIn("box.querySelectorAll(\"[data-media-", media)

        self.assertIn('`#messages > [data-msg="${i}"] [data-thought-elapsed]`', APP_JS)

    def test_one_waiting_state(self):
        render = js_function("renderMessages")
        self.assertIn("!m.media && !m.thoughtStartedAt ? `<div class=\"generation-wave\">", render)
        self.assertIn("<em>${esc(waitingLabel)}</em>", render)
        self.assertIn("!generatingView && !m.thoughtStartedAt ? `<div class=\"msg-progress\">", render)
        self.assertIn('assistant.thoughtStartedAt = Date.now(); assistant.progress = "";', APP_JS)

    def test_settings_false_changed(self):
        kit = (PG / "settings-kit.js").read_text(encoding="utf-8")
        self.assertIn('input:not([type=hidden]):not(.mpick-search), select, textarea', kit)

    def test_css(self):
        appearance = (PG / "appearance.css").read_text(encoding="utf-8")
        self.assertRegex(appearance, r"\.variant-option \{[^}]*align-content: start")
        style = (PG / "style.css").read_text(encoding="utf-8")
        self.assertRegex(style, r"\.mpick-value \{[^}]*min-width: 0;[^}]*text-overflow: ellipsis")
        self.assertIn(".mpick-btn > svg { flex: none; }", style)

    def test_reflect_default_period(self):
        reflect = (PG / "reflect.js").read_text(encoding="utf-8")
        self.assertRegex(reflect, r'let period = "this_month";')
        self.assertLess(reflect.index('["this_month"'), reflect.index('["last_month"'))

    def test_focus(self):
        self.assertIn('const textField = dialog.querySelector(".sx-shell") ? null :', APP_JS)
        edit = js_function("editMessage")
        self.assertIn("area.setSelectionRange(area.value.length, area.value.length)", edit)
        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', edit)
        self.assertGreaterEqual(APP_JS.count('$("#input")?.focus({ preventScroll: true });'), 2)
        self.assertIn('if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeDD(); $("#model-dd-btn")?.focus', APP_JS)

    def test_agents_and_folders(self):

        self.assertIn('${loadMemes().length ? `<div class="row-end"><button class="pbtn ghost" id="meme-export-all">', APP_JS)
        self.assertIn(".panel .meme-choice-note { margin: 10px 0 0; }", (PG / "appearance.css").read_text(encoding="utf-8"))

        self.assertIn('<span>${esc(f.name)}</span>${total ? `<small>${total}</small>` : ""}</button>', APP_JS)

    def test_cache_versions(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "settings-kit.js", "media-routing.js", "appearance.css", "style.css", "reflect.js"):
            match = re.search(r'/' + re.escape(name) + r'\?v=([^"]+)"', html)
            self.assertIsNotNone(match, name)
            self.assertTrue(match.group(1).strip(), name)
            if name == "app.js":
                from app.version import APP_VERSION
                self.assertEqual(match.group(1), APP_VERSION)

if __name__ == "__main__":
    unittest.main()
