"""Round 31 — MemuNote AI panel: redesign + agent mode (the AI edits the document).

Covers: the edit protocol parser (app/memunote/edits.py, streaming and whole), the agent prompt
(app/memunote/prompts.py), model labels and the status cache (app/memunote/ai.py), the SSE edit events
of /api/ai/chat against a fake Proxy (app/memunote/server.py), the pure JS helpers in Node
(static/js/ai-diff.js: diff → hunks, resolving edits, context excerpts for huge files), i18n of the
panel, wiring (CSS, build.py, MemuNote.spec). The live test talks to a real model when
XKIRO_BASE/XKIRO_KEY are set (source /workspace/work/secrets/test_keys.env), otherwise it is skipped.
"""
from __future__ import annotations

import http.server
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"
JS = STATIC / "js"

import sys  # noqa: E402
sys.path.insert(0, str(ROOT))

from app.memunote import edits, prompts  # noqa: E402

NODE = shutil.which("node")

def run_stream(text, chunks=None):
    s = edits.EditStream()
    out = []
    if chunks is None:
        out += s.feed(text)
    else:
        for c in chunks:
            out += s.feed(c)
    out += s.finish()
    prose = "".join(v for k, v in out if k == "delta")
    found = [v for k, v in out if k == "edit"]
    return prose, found, out

ANSWER = '''Исправлю опечатку и добавлю вывод.

```xml
<edit op="replace">
<find>Превет мир</find>
<text>Привет, мир</text>
</edit>
<edit op="insert" at="end">
<text>
## Вывод

Всё хорошо.</text>
</edit>
```
Готово. Текст <editor> и a < b. ```python
x = 1
```'''

class EditProtocolTest(unittest.TestCase):
    def test_tag_blocks_are_parsed_and_fences_around_them_dropped(self) -> None:
        prose, found, _ = run_stream(ANSWER)
        self.assertEqual(prose, "Исправлю опечатку и добавлю вывод.\n\nГотово. Текст <editor> и a < b. ```python\nx = 1\n```")
        self.assertEqual(found[0], {"op": "replace", "find": "Превет мир", "text": "Привет, мир", "index": 0})
        self.assertEqual(found[1], {"op": "insert", "at": "end", "text": "## Вывод\n\nВсё хорошо.", "index": 1})

    def test_any_chunking_gives_the_same_result(self) -> None:
        base = run_stream(ANSWER)[:2]
        for seed in range(400):
            rnd = random.Random(seed)
            chunks, i = [], 0
            while i < len(ANSWER):
                n = rnd.randint(1, 9)
                chunks.append(ANSWER[i:i + n])
                i += n
            self.assertEqual(run_stream(ANSWER, chunks)[:2], base, f"seed {seed}")

    def test_live_progress_events(self) -> None:
        long = '<edit op="insert" at="cursor"><text>' + "слово " * 40 + "</text></edit>"
        _, found, out = run_stream(long, [long[i:i + 7] for i in range(0, len(long), 7)])
        kinds = [k for k, _ in out]
        self.assertEqual(kinds[0], "edit_start")
        self.assertIn("edit_progress", kinds)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["text"].startswith("слово"))

    def test_json_fallback_and_ordinary_json_code(self) -> None:
        prose, found = edits.parse_all('Ок\n```json\n{"edits":[{"op":"replace","find":"a","text":"b"}]}\n```\nи код\n'
                                       '```json\n{"x":1}\n```')
        self.assertEqual(found, [{"op": "replace", "find": "a", "text": "b", "index": 0}])
        self.assertIn('{"x":1}', prose)

    def test_incomplete_block_after_stop_is_marked(self) -> None:
        prose, found = edits.parse_all('Пишу <edit op="insert" at="cursor"><text>hello wor')
        self.assertEqual(prose, "Пишу ")
        self.assertEqual(found[0]["text"], "hello wor")
        self.assertTrue(found[0]["incomplete"])

    def test_normalize_validates_and_aliases(self) -> None:
        n = edits.normalize
        self.assertIsNone(n({"op": "bogus", "text": "x"}))
        self.assertIsNone(n({"op": "replace", "text": "x"}))
        self.assertIsNone(n({"op": "insert", "at": "after", "text": "x"}))
        self.assertEqual(n({"op": "append", "text": "x"}), {"op": "insert", "text": "x", "at": "end"})
        self.assertEqual(n({"type": "remove", "find": "z"}), {"op": "delete", "text": "", "find": "z"})
        self.assertEqual(n({"op": "replace", "find": "a⟦cursor⟧b", "text": "c"})["find"], "ab")
        self.assertEqual(n({"op": "replace_all", "find": "a", "text": "b", "regex": "true"})["regex"], True)
        self.assertEqual(n({"op": "new_document", "title": "T", "text": "x", "kind": "weird"})["kind"], "markdown")
        self.assertEqual(n({"op": "rewrite_section", "heading": "## Итоги", "text": "x"})["heading"], "## Итоги")

    def test_text_may_mention_its_own_tag(self) -> None:
        _, found = edits.parse_all('<edit op="insert" at="end"><text>use <text> tags</text></edit>')
        self.assertEqual(found[0]["text"], "use <text> tags")

class AgentPromptTest(unittest.TestCase):
    def build(self, context, input_text="", history=None):
        body = {"action": "agent", "context": context, "input": input_text,
                "messages": history or [{"role": "user", "content": "Добавь вывод"}]}
        return prompts.build_messages(body, {"ai_context_chars": 60000}, "ru", use_memory=False)["messages"]

    def test_agent_prompt_has_protocol_document_outline_and_selection(self) -> None:
        msgs = self.build({"name": "a.md", "kind": "markdown", "text": "# T\n\nтекст⟦cursor⟧", "total_chars": 12,
                           "lines": 3, "outline": [{"level": 1, "title": "T", "line": 1}], "scope": "selection"},
                          input_text="текст")
        system = msgs[0]["content"]
        self.assertIn('<edit op="replace">', system)
        self.assertIn("Markdown", system)
        doc = msgs[1]["content"]
        self.assertIn('<document name="a.md" kind="markdown"', doc)
        self.assertIn("<outline>", doc)
        self.assertIn("<selection", doc)
        self.assertIn("change only the selected text", doc)
        self.assertEqual(msgs[-1], {"role": "user", "content": "Добавь вывод"})

    def test_excerpt_flag_and_size_limit(self) -> None:
        big = "строка\n" * 30000
        msgs = self.build({"name": "big.txt", "kind": "text", "text": big, "total_chars": 5_000_000, "truncated": True})
        doc = msgs[1]["content"]
        self.assertIn('excerpt="true"', doc)
        self.assertIn('chars="5000000"', doc)
        self.assertLess(len(doc), 70000)

    def test_empty_document(self) -> None:
        msgs = self.build({"name": "new", "kind": "text", "text": ""})
        self.assertIn('empty="true"', msgs[1]["content"])

    def test_history_of_edits_is_kept(self) -> None:
        msgs = self.build({"name": "a", "kind": "text", "text": "x"}, history=[
            {"role": "user", "content": "раз"}, {"role": "assistant", "content": "ок (Proposed edits: [insert])"},
            {"role": "user", "content": "два"}])
        self.assertEqual([m["content"] for m in msgs[-3:]], ["раз", "ок (Proposed edits: [insert])", "два"])

class ModelsAndStatusTest(unittest.TestCase):
    def test_context_family_label_is_not_the_model_name(self) -> None:
        from app.memunote import ai
        state = {"models": [{"id": "xkiro-qwen/qwen3.8-max:free", "context": {"label": "Qwen3 (256K)", "context": 262144}},
                            {"id": "xkiro-qwen/qwen3.7-flash:free", "context": {"label": "Qwen3 (256K)"}},
                            {"id": "named", "label": "Nice name"}]}
        with mock.patch.object(ai, "request_json", return_value=state):
            rows = ai.models("http://127.0.0.1:1")

        labels = [r["label"] for r in rows]
        self.assertEqual(labels, ["Qwen3.8 Max", "Qwen3.7 Flash", "Nice name"])
        self.assertNotIn("Qwen3 (256K)", labels)
        self.assertEqual(len(set(labels)), 3)
        self.assertEqual([r["id"] for r in rows], ["xkiro-qwen/qwen3.8-max:free", "xkiro-qwen/qwen3.7-flash:free", "named"])
        self.assertEqual(rows[0]["family"], "Qwen3 (256K)")

    def test_status_cache_is_keyed_by_the_configured_url(self) -> None:
        from app.memunote import ai
        calls = []

        def fake_health(base):
            calls.append(base)
            return False
        ai._STATUS_CACHE.update(at=0.0, value=None)
        with mock.patch.object(ai, "health", fake_health):
            ai.status("http://127.0.0.1:8718")
            ai.status("http://127.0.0.1:8718")
            ai.status("")
        self.assertEqual(len(calls), 2, calls)
        ai._STATUS_CACHE.update(at=0.0, value=None)

class _FakeProxy(http.server.BaseHTTPRequestHandler):
    answer = ""

    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):  # noqa: N802
        body = json.dumps({"service": "memuai-proxy"} if self.path == "/health" else {"models": [{"id": "fake"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(self.answer), 5):
            chunk = {"choices": [{"delta": {"content": self.answer[i:i + 5]}}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

class ServerAgentStreamTest(unittest.TestCase):
    def test_agent_chat_emits_edit_events(self) -> None:
        _FakeProxy.answer = ANSWER
        fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeProxy)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": tmp.name,
                                           "MEMUAI_PROXY_URL": f"http://127.0.0.1:{fake.server_address[1]}"})
        env.start()
        try:
            from app.memunote import server
            app = server.NoteApp([])
            httpd, _ = server.start(app)
            try:
                port = httpd.server_address[1]
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/ai/chat", method="POST",
                    data=json.dumps({"action": "agent", "model": "fake", "memory": False,
                                     "messages": [{"role": "user", "content": "go"}],
                                     "context": {"name": "a.md", "kind": "markdown", "text": "Превет мир"}}).encode(),
                    headers={"Content-Type": "application/json", "X-MN-Token": app.token, "Host": f"127.0.0.1:{port}"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                raw = opener.open(req, timeout=20).read().decode("utf-8")
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            env.stop()
            fake.shutdown()
            tmp.cleanup()
        events = [json.loads(line[5:]) for line in raw.split("\n") if line.startswith("data:")]
        kinds = [e["t"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertTrue(events[0]["agent"])
        self.assertEqual(kinds[-1], "done")
        found = [e for e in events if e["t"] == "edit"]
        self.assertEqual([(e["op"], e.get("find")) for e in found], [("replace", "Превет мир"), ("insert", None)])
        prose = "".join(e["text"] for e in events if e["t"] == "delta")
        self.assertNotIn("<edit ", prose)
        self.assertNotIn("```xml", prose)
        self.assertIn("<editor>", prose)

@unittest.skipUnless(NODE, "node is not installed")
class DiffHelpersNodeTest(unittest.TestCase):
    def node(self, script: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.mjs"
            path.write_text(f'import * as D from "{(JS / "ai-diff.js").as_uri()}";\n' + script, encoding="utf-8")
            return json.loads(subprocess.check_output([NODE, str(path)], text=True))

    def test_hunks_always_rebuild_the_new_text(self) -> None:
        r = self.node(r"""
const apply = (t, hs) => { let o = t; for (const h of [...hs].sort((a, b) => b.from - a.from)) o = o.slice(0, h.from) + h.insert + o.slice(h.to); return o; };
let rnd = 7; const R = (n) => { rnd = (rnd * 1103515245 + 12345) % 2147483648; return rnd % n; };
const words = "the a cat dog привет мир, . ! \n \n\n run fast slow один два".split(" ");
let bad = 0;
for (let i = 0; i < 1500; i++) {
  const mk = () => Array.from({ length: R(40) }, () => words[R(words.length)] + (R(3) ? " " : "")).join("");
  const a = mk(), b = R(4) ? a.replace(/cat/g, "кот").replace(/dog/, "") + mk().slice(0, R(10)) : mk();
  const base = R(5), hs = D.buildHunks(a, b, base);
  if (apply("x".repeat(base) + a, hs).slice(base) !== b) bad++;
  for (const h of hs) if (("x".repeat(base) + a).slice(h.from, h.to) !== h.del) bad++;
}
console.log(JSON.stringify({ bad }));
""")
        self.assertEqual(r["bad"], 0)

    def test_grammar_fix_gives_word_hunks_translation_gives_paragraphs(self) -> None:
        r = self.node(r"""
const g = D.buildHunks("Превет мир. Как дила? Всё хорошо.", "Привет, мир. Как дела? Всё хорошо.");
const a = "Первый абзац.\n\nПопробовали эчпочмак, чак-чак и губадию. Цены приятно удивили: обед стоил 1500 рублей.\n\nКонец.";
const b = "Первый абзац.\n\nWe tried echpochmak, chak-chak and gubadiya. Prices were a pleasant surprise: lunch cost 1500 rubles.\n\nКонец.";
console.log(JSON.stringify({ g: g.map((h) => [h.del, h.insert]), t: D.buildHunks(a, b).map((h) => h.del) }));
""")
        self.assertEqual(r["g"], [["Превет", "Привет,"], ["дила", "дела"]])
        self.assertEqual(len(r["t"]), 1)
        self.assertTrue(r["t"][0].startswith("Попробовали") and r["t"][0].endswith("рублей."))

    def test_resolve_edit_operations(self) -> None:
        r = self.node(r"""
const doc = "# Title\n\nIntro text here.\n\n## Итоги\n\nOld summary.\nMore.\n\n## Next\n\nBody\n";
const R = (e, info = {}) => D.resolveEdit(e, doc, { cursor: 0, kind: "markdown", ...info });
const ap = (r) => { let o = doc; for (const c of [...r.changes].sort((a, b) => b.from - a.from)) o = o.slice(0, c.from) + c.insert + o.slice(c.to); return o; };
console.log(JSON.stringify({
  section: ap(R({ op: "rewrite_section", heading: "## Итоги", text: "New summary." })),
  fuzzy: ap(R({ op: "replace", find: "Intro  text\nhere.", text: "X" })),
  end: ap(R({ op: "insert", at: "end", text: "## End\nok" })),
  after: ap(R({ op: "insert", at: "after", anchor: "Intro text here.", text: "\nNew para" })),
  sel: ap(R({ op: "replace_selection", text: "S" }, { selFrom: 2, selTo: 7 })),
  all: ap(R({ op: "replace_all", find: "e", text: "E" })).split("E").length - 1,
  missing: R({ op: "replace", find: "nope", text: "x" }).error,
  nosel: R({ op: "replace_selection", text: "x" }).error,
}));
""")
        self.assertIn("## Итоги\n\nNew summary.\n\n## Next", r["section"])
        self.assertIn("\n\nX\n\n## Итоги", r["fuzzy"])
        self.assertTrue(r["end"].endswith("Body\n\n## End\nok\n"))
        self.assertIn("Intro text here.\n\nNew para\n\n## Итоги", r["after"])
        self.assertTrue(r["sel"].startswith("# S\n"))
        self.assertGreater(r["all"], 5)
        self.assertEqual(r["missing"], "not_found")
        self.assertEqual(r["nosel"], "no_selection")

    def test_context_of_a_huge_file_is_an_excerpt(self) -> None:
        r = self.node(r"""
const big = "строка журнала, ничего интересного\n".repeat(80000) + "## Раздел\n\nСекрет\n";
const pos = big.indexOf("Секрет");
const c = D.buildContext({ text: big, kind: "markdown", selFrom: pos, selTo: pos, limit: 48000 });
const small = D.buildContext({ text: "abc def", kind: "text", selFrom: 4, selTo: 7, limit: 48000 });
console.log(JSON.stringify({ total: big.length, len: c.text.length, truncated: c.truncated, hasSecret: c.text.includes("⟦cursor⟧Секрет"),
  outline: c.outline, scope: small.scope, smallText: small.text, sel: small.selection, range: small.range }));
""")
        self.assertGreater(r["total"], 2_500_000)
        self.assertLess(r["len"], 50000)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["hasSecret"])
        self.assertEqual(r["outline"], [{"level": 2, "title": "Раздел", "line": 80001}])
        self.assertEqual(r["scope"], "selection")
        self.assertEqual(r["smallText"], "abc ⟦selection⟧def⟦/selection⟧")
        self.assertEqual(r["sel"], "def")
        self.assertEqual(r["range"], [4, 7])

class StaticWiringTest(unittest.TestCase):
    def test_scripts_parse(self) -> None:
        if not NODE:
            self.skipTest("node is not installed")
        for name in ("ai.js", "ai-edit.js", "ai-diff.js", "app.js", "i18n.js"):
            subprocess.run([NODE, "--check", str(JS / name)], check=True)

    def test_panel_css_is_shipped_and_loaded(self) -> None:
        css = (STATIC / "ai-panel.css").read_text(encoding="utf-8")
        for cls in (".aip-head", ".aip-card", ".aip-hunk", ".aip-compose", ".aip-empty", ".aip.collapsed", ".mn-ai-del",
                    ".mn-ai-ins", ".mn-ai-stream", "@container aip", ':root[data-theme="light"]'):
            self.assertIn(cls, css)

        self.assertNotRegex(css, r"background:\s*#[0-9a-f]{3,6}\b(?!.*var\()")
        self.assertIn('ai-panel.css', (JS / "ai.js").read_text(encoding="utf-8"))

    def test_i18n_of_the_panel(self) -> None:
        if not NODE:
            self.skipTest("node is not installed")
        script = r"""
import { DICT } from "%s";
import fs from "fs";
const src = fs.readFileSync(%s, "utf8");
const keys = new Set([...src.matchAll(/\bt\(\s*"([^"]+)"/g)].map((m) => m[1]));
for (const m of src.matchAll(/(?:label|hint): "(ai_[a-z_0-9]+)"/g)) keys.add(m[1]);
for (const m of src.matchAll(/"(ai_sug_[a-z0-9]+)"/g)) keys.add(m[1]);
for (const m of src.matchAll(/t\(`(ai_[a-z]+)_\$\{/g)) keys.add("prefix:" + m[1]);
["lang_", "tone_"].forEach((k) => keys.delete(k));
const plain = [...keys].filter((k) => !k.startsWith("prefix:"));
const ops = ["replace", "replace_all", "delete", "insert", "replace_selection", "rewrite_section", "new_document", "transform", "invalid"].map((x) => "ai_op_" + x);
const hunks = ["not_found", "overlap", "stale", "invalid", "no_selection", "bad_regex", "unsupported"].map((x) => "ai_hunk_" + x);
const scopes = ["document", "selection", "section"].map((x) => `ai_scope_${x}_hint`);
const dyn = ["ai_auto_on", "ai_auto_off", "ai_ph_edit", "ai_ph_chat", "ai_wrote", "ai_wrote_partial", "ai_agent_transform"];
const all = [...plain, ...ops, ...hunks, ...scopes, ...dyn];
const out = { missing_ru: all.filter((k) => !(k in DICT.ru)), missing_en: all.filter((k) => !(k in DICT.en)),
  cyr: Object.entries(DICT.en).filter(([, v]) => /[\u0400-\u04FF]/.test(v)).map(([k]) => k), n: all.length };
console.log(JSON.stringify(out));
""" % ((JS / "i18n.js").as_uri(), json.dumps(str(JS / "ai.js")))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            out = json.loads(subprocess.check_output([NODE, str(path)], text=True))
        self.assertGreater(out["n"], 80)
        self.assertEqual(out["missing_ru"], [])
        self.assertEqual(out["missing_en"], [])
        self.assertEqual(out["cyr"], [])

    def test_build_and_spec_include_the_edits_module(self) -> None:
        self.assertIn('"app.memunote.edits"', (ROOT / "build.py").read_text(encoding="utf-8"))
        self.assertIn("'app.memunote.edits'", (ROOT / "MemuNote.spec").read_text(encoding="utf-8"))

    def test_inline_context_is_an_excerpt_not_the_whole_file(self) -> None:
        app_js = (JS / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("text.slice(0, 2_000_000)", app_js)
        self.assertIn("aiExcerpt(", app_js)

@unittest.skipUnless(os.environ.get("XKIRO_BASE") and os.environ.get("XKIRO_KEY"), "live keys are not set")
class LiveAgentTest(unittest.TestCase):
    """A real model answers the agent prompt with edit blocks that parse and resolve."""

    def test_qwen_produces_valid_edits(self) -> None:
        doc = "# Поездка\n\nМы приехали в Казань в пятницу. Первым делом пошли в Кремль.\n\n## Итоги\n\nПоездка удалась.\n"
        msgs = prompts.build_messages({"action": "agent", "context": {"name": "trip.md", "kind": "markdown", "text": doc},
                                       "messages": [{"role": "user", "content": "Замени «Кремль» на «Казанский Кремль» и добавь в конец раздел «Советы» из двух пунктов."}]},
                                      {"ai_context_chars": 60000}, "ru", use_memory=False)["messages"]
        base = os.environ["XKIRO_BASE"].rstrip("/")
        last_error = None
        for key in [os.environ.get(k) for k in ("XKIRO_KEY", "XKIRO_KEY2", "XKIRO_KEY3") if os.environ.get(k)]:
            req = urllib.request.Request(base + "/chat/completions", method="POST",
                                         data=json.dumps({"model": "qwen/qwen3.8-max:free",
                                                          "messages": msgs, "stream": False}).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                                  "User-Agent": "MemuNote-tests/3.5"})
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=180).read())
                break
            except Exception as exc:  # noqa: BLE001 - 429 → next key
                last_error = exc
                time.sleep(2)
        else:
            self.skipTest(f"model unavailable: {last_error}")
        answer = data["choices"][0]["message"]["content"]
        prose, found = edits.parse_all(answer)
        self.assertTrue(found, answer[:500])
        ops = {e["op"] for e in found}
        self.assertTrue(ops & {"replace", "replace_all"}, found)
        self.assertTrue(ops & {"insert", "rewrite_section"}, found)
        for e in found:
            if e["op"] == "replace":
                self.assertIn(e["find"], doc)
        self.assertNotIn("<edit ", prose)

if __name__ == "__main__":
    unittest.main()
