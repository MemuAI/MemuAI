"""MemuNote 4.0 keyboard/indentation regressions in an isolated real editor.

Uses only local static assets; no user profile, Proxy or model calls.
"""
from __future__ import annotations

import functools
import http.server
import os
from pathlib import Path
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"

FIXTURE = b"""<!doctype html><html data-theme="dark" class="fallback-tokens"><head>
<meta charset="utf-8"><link rel="stylesheet" href="/memunote.css">
<style>body{padding:24px}#editor{height:80vh;min-width:0}</style></head><body><div id="editor"></div>
<script type="module">
import * as editor from '/js/editor.js';
import {codeComplete} from '/js/codecomplete.js';
import {I18N} from '/js/core.js';
const {CM,createState,loadLanguage,languageFor,setGhost}=editor;
window.noteEditor=editor;
window.makeEditor=async ({doc='',file='fixture.js',key='javascript',cursor,settings={},readOnly=false,lang='ru'}={})=>{
  window.view?.destroy();
  I18N.lang=lang;
  const opts={settings:{tab_size:4,indent_with_tabs:false,auto_close_brackets:true,bracket_matching:true,...settings},
    kind:file.endsWith('.json')?'json':file.endsWith('.md')?'markdown':'text',readOnly,
    extraConfig:()=>codeComplete(()=>key,true),selection:{anchor:cursor??doc.length}};
  const state=createState(doc,opts);
  const view=window.view=new CM.EditorView({state,parent:document.querySelector('#editor')});
  view.dispatch({effects:editor.langComp.reconfigure(await loadLanguage(languageFor(file,opts.kind)))});
  view.focus();
};
window.startCompletion=()=>CM.completionKeymap.find(k=>k.key==='Ctrl-Space').run(view);
window.setTestGhost=text=>view.dispatch({effects:setGhost.of({text,pos:view.state.selection.main.head})});
window.snapshot=()=>({text:view.state.doc.toString(),pos:view.state.selection.main.head,unit:view.state.facet(CM.indentUnit)});
window.ready=true;
</script></body></html>"""

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/__editor_fixture__":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FIXTURE)))
            self.end_headers()
            self.wfile.write(FIXTURE)
        else:
            super().do_GET()

    def log_message(self, *args):
        pass

class EditorBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Playwright unavailable")
        cls.pw = sync_playwright().start()
        chrome = os.environ.get("MEMU_TEST_CHROMIUM", cls.pw.chromium.executable_path)
        if not Path(chrome).is_file():
            chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not Path(chrome).is_file():
            cls.pw.stop()
            raise unittest.SkipTest("Chromium unavailable")
        cls.browser = cls.pw.chromium.launch(executable_path=chrome, headless=True)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(STATIC)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1080, "height": 740})
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}/__editor_fixture__")
        self.page.wait_for_function("window.ready")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def editor(self, **opts):
        self.page.evaluate("opts=>makeEditor(opts)", opts)

    def complete(self):
        self.page.evaluate("startCompletion()")
        self.page.wait_for_selector(".cm-tooltip-autocomplete li[aria-selected=true]")
        self.page.wait_for_timeout(100)

    def snap(self):
        return self.page.evaluate("snapshot()")

    def test_tab_accepts_selected_completion_without_indenting(self):
        self.editor(doc="cons")
        self.complete()
        label = self.page.locator("li[aria-selected=true] .cm-completionLabel").inner_text()
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], label)
        self.assertEqual(self.page.locator(".cm-tooltip-autocomplete").count(), 0)
        self.page.keyboard.press("Control+z")
        self.assertEqual(self.snap()["text"], "cons")

    def test_enter_starts_new_line_instead_of_accepting_completion(self):
        self.editor(doc="cons")
        self.complete()
        self.page.keyboard.press("Enter")
        self.assertEqual(self.snap()["text"], "cons\n")

    def test_existing_two_space_json_keeps_alignment_on_enter(self):
        doc = '{\n  "name": "Memu",\n  "items": [\n    1,\n    2\n  ]\n}'
        self.editor(doc=doc, file="data.json", key="", cursor=doc.index(",") + 1)
        self.page.keyboard.press("Enter")
        self.assertEqual(self.snap()["text"], doc.replace('"Memu",', '"Memu",\n  '))
        self.assertEqual(self.snap()["unit"], "  ")

    def test_nested_json_empty_pairs_use_existing_unit(self):
        doc = '{\n  "existing": true,\n  "data": {}\n}'
        self.editor(doc=doc, file="data.json", key="", cursor=doc.index("{}") + 1)
        self.page.keyboard.press("Enter")
        self.assertEqual(self.snap()["text"], doc.replace("{}", "{\n    \n  }"))

    def test_plain_tab_shift_tab_and_ghost_fallback(self):
        self.editor(doc="x", file="note.txt", key="")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "    x")
        self.page.keyboard.press("Shift+Tab")
        self.assertEqual(self.snap()["text"], "x")
        self.page.evaluate("setTestGhost('yz')")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "xyz")

    def test_plain_text_pairs_quotes_skip_close_and_paired_backspace(self):
        for opening,closing in [('(',')'),('[',']'),('{','}'),('"','"')]:
            self.editor(doc='',file='note.txt',key='')
            self.page.keyboard.type(opening)
            self.assertEqual(self.snap()['text'],opening+closing)
            self.page.keyboard.type(closing)
            self.assertEqual(self.snap()['text'],opening+closing)
            self.assertEqual(self.snap()['pos'],2)
            self.editor(doc='',file='note.txt',key='')
            self.page.keyboard.type(opening);self.page.keyboard.press('Backspace')
            self.assertEqual(self.snap()['text'],'')

    def test_visible_completion_wins_over_ghost_and_escape_dismisses_both(self):
        self.editor(doc="cons")
        self.complete()
        label = self.page.locator("li[aria-selected=true] .cm-completionLabel").inner_text()
        self.page.evaluate("setTestGhost('wrong')")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], label)
        self.editor(doc="cons")
        self.complete()
        self.page.evaluate("setTestGhost('wrong')")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.snap()["text"], "cons")
        self.assertEqual(self.page.locator(".cm-tooltip-autocomplete, .mn-ghost").count(), 0)

    def test_read_only_ghost_cannot_change_document(self):
        self.editor(doc="x", file="note.txt", key="", readOnly=True)
        self.page.evaluate("setTestGhost('yz')")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "x")

    def test_tab_indented_python_keeps_tabs_and_new_file_uses_defaults(self):
        doc = "def hello():\n\treturn True"
        self.editor(doc=doc, file="main.py", key="python")
        self.page.keyboard.press("Enter")
        self.assertEqual(self.snap()["text"], doc + "\n\t")
        self.assertEqual(self.snap()["unit"], "\t")
        self.editor(doc="", settings={"tab_size": 2})
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "  ")

    def test_indentation_ignores_an_isolated_alignment_outlier(self):
        result = self.page.evaluate("""() => noteEditor.detectIndentation(
          'function f() {\\n' + '    body();\\n'.repeat(10) + '       aligned();\\n}', {tab_size:2})""")
        self.assertEqual(result["tab_size"], 4)
        self.assertFalse(result["indent_with_tabs"])

    def test_buffer_completion_supports_unicode_identifiers(self):
        self.editor(doc="const название = 1;\nназ")
        self.complete()
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "const название = 1;\nназвание")

    def test_large_file_completion_finds_local_and_header_names(self):
        self.editor(doc="const headerVariable = 1;\n" + "// filler\n" * 340000 + "const nearbyVariable = 2;\nnear")
        self.page.keyboard.press("Control+End")
        self.complete()
        self.assertIn("nearbyVariable", self.page.locator(".cm-completionLabel").all_inner_texts())
        self.page.locator(".cm-completionLabel", has_text="nearbyVariable").click()
        self.assertTrue(self.snap()["text"].endswith("\nnearbyVariable"))

    def test_disabled_completion_keeps_enter_and_plain_tab(self):
        self.editor(doc="cons")
        self.complete()
        self.page.keyboard.press("Escape")
        self.page.keyboard.press("Enter")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.snap()["text"], "cons\n    ")

    def test_popup_theme_bounds_and_keyboard_hint(self):
        for theme, width in (("dark", 1080), ("light", 420)):
            self.page.set_viewport_size({"width": width, "height": 740})
            self.page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            self.editor(doc="cons", lang="en")
            self.complete()
            popup = self.page.locator(".cm-tooltip-autocomplete")
            box = popup.bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertLessEqual(box["x"] + box["width"], width)
            self.assertLessEqual(box["height"], 360)
            self.assertIn("Tab", self.page.evaluate("getComputedStyle(document.querySelector('.cm-tooltip-autocomplete'),'::after').content"))
            self.assertGreaterEqual(self.page.locator("li[aria-selected=true]").bounding_box()["height"], 28)

if __name__ == "__main__":
    unittest.main()
