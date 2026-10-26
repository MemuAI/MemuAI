"""Round 30 (r30-misc): honest token estimates, readable schemes, /image /video in the message box.

    python -m unittest tests.round30_misc_test
The browser part (tests/round30_misc_browser.py) runs when headless Chromium is available.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))

from app import token_estimate as te  # noqa: E402

RU = "Быстрая коричневая лиса прыгает через ленивую собаку у реки. " * 10
EN = "The quick brown fox jumps over the lazy dog near the river. " * 10
CODE = "def fibonacci(n: int) -> int:\n    return n if n < 2 else fibonacci(n - 1) + fibonacci(n - 2)\n" * 10
CJK = "人工智能正在改变世界。敏捷的棕色狐狸跳过了懒狗。" * 10
NODE = shutil.which("node")

def node(script: str, *args: str, stdin: str = "") -> str:
    return subprocess.run([NODE, "-e", script, *args], input=stdin, capture_output=True, text=True, timeout=60, check=True).stdout

class TokenEstimateTest(unittest.TestCase):
    def test_languages_are_counted_differently(self) -> None:
        ru, en = te.estimate(RU), te.estimate(EN)

        self.assertTrue(190 <= ru <= 260, ru)
        self.assertTrue(115 <= en <= 160, en)
        self.assertGreater(ru / len(RU), 1.3 * en / len(EN))
        self.assertGreater(te.estimate(CJK), len(CJK) * 0.5)
        self.assertEqual(te.estimate(""), 0)
        self.assertEqual(te.estimate(None), 0)

    def test_model_family_changes_the_count(self) -> None:
        self.assertGreater(te.estimate(RU, "claude-sonnet-4-6"), te.estimate(RU, "gpt-5"))
        self.assertGreater(te.estimate(EN, "claude-opus-4-7"), te.estimate(EN, "claude-sonnet-4-6"))
        self.assertEqual(te.estimate(RU, "some-unknown-model"), te.estimate(RU))
        cases = {"antigravity-claude-opus-4-6-thinking": "claude", "claude-opus-4.7": "claude-new", "gpt-4o-mini": "gpt",
                 "openai/o3": "gpt", "gemini-3-flash": "gemini", "qwen/qwen3.8-max:free": "qwen", "deepseek-v4.1-flash": "deepseek",
                 "llama-4-scout": "llama", "mistral-small": "mistral", "glm-4.6": "glm", "MiniMax-M2": "minimax", "local-model": ""}
        self.assertEqual({name: te.family_of(name) for name in cases}, cases)

    def test_breakdown_is_marked_as_an_estimate_with_a_range(self) -> None:
        info = te.breakdown(RU + CODE, exact=False)
        self.assertTrue(info["estimated"])
        self.assertLessEqual(info["low"], info["tokens"])
        self.assertLessEqual(info["tokens"], info["high"])
        self.assertGreater(info["high"], info["low"])
        self.assertEqual([row["id"] for row in info["families"]], [row["id"] for row in te.FAMILIES])
        self.assertGreater(info["mix"]["cyrillic"], 0.3)
        self.assertGreater(info["mix"]["code"], 0.05)

    def test_the_memo_is_bounded_and_gives_the_same_numbers(self) -> None:
        text = RU * 3
        first = te.estimate(text)
        te._MEMO.clear()
        self.assertEqual(te.estimate(text), first)
        for number in range(te._MEMO_LIMIT * 2):
            te.estimate(f"{number} " + "z" * 500)
        self.assertLessEqual(len(te._MEMO), te._MEMO_LIMIT)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_js_twin_has_the_same_table_and_the_same_numbers(self) -> None:
        source = (STATIC / "tokens.js").read_text(encoding="utf-8")
        table = source[source.index("/* TABLE-BEGIN */"):source.index("/* TABLE-END */")]
        rows = re.findall(r'id: "([\w-]+)", label: "([^"]+)", coef: \[([^\]]+)\]', table)
        self.assertEqual([(row["id"], row["label"], row["coef"]) for row in te.FAMILIES],
                         [(rid, label, [float(x) for x in coef.split(",")]) for rid, label, coef in rows])
        self.assertIn(", ".join(f'"{name}"' for name in te.FEATURES), source)
        texts = [RU, EN, CODE, CJK, "😀🚀 naïve façade Ελληνικά العربية\n\t\tindent", "a" * 380, "", "x"]
        out = json.loads(node("const T=require(process.argv[1]); const xs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                              "console.log(JSON.stringify(xs.map(t=>[T.estimate(t),T.estimate(t,'claude-x'),T.features(t)])))",
                              str(STATIC / "tokens.js"), stdin=json.dumps(texts)))
        self.assertEqual(out, [[te.estimate(t), te.estimate(t, "claude-x"), te.features(t)] for t in texts])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_js_rounds_an_estimate_without_false_precision(self) -> None:
        out = json.loads(node("const T=require(process.argv[1]); console.log(JSON.stringify([T.approx(84330), T.approx(999),"
                              "T.shortRange(78933, 125881, 'ru'), T.shortRange(950, 1200, 'ru'), T.shortRange(78933, 125881, 'en'), T.labelOf('gpt-5')]))",
                              str(STATIC / "tokens.js")))
        self.assertEqual(out, [84300, 999, "79–126 тыс.", "950–1,2 тыс.", "79k–126k", "GPT-4o / GPT-5"])

class TokenEstimateUsersTest(unittest.TestCase):
    """Every place that used «length / 4» goes through the one estimate now."""

    def test_proxy_and_memucode_and_memory_use_it(self) -> None:
        from app.cli import tokens
        from app.memory_v2 import estimate_tokens
        from app.providers.base import approx_tokens
        self.assertEqual(approx_tokens(RU), te.estimate(RU))
        self.assertEqual(approx_tokens(RU, "claude-sonnet-4-6"), te.estimate(RU, "claude-sonnet-4-6"))
        self.assertEqual(approx_tokens(""), 1)
        self.assertEqual(tokens.estimate_text(RU), te.estimate(RU))
        self.assertEqual(estimate_tokens(RU), te.estimate(RU) + 4)
        self.assertAlmostEqual(tokens.chunk_tokens(RU), te.estimate(RU), delta=1)

    def test_proxy_estimate_of_a_request_counts_tool_arguments_as_text(self) -> None:
        from app.chat import _completion_tokens, _prompt_tokens
        self.assertEqual(_completion_tokens("", ['{"path": "a.txt"}'], ""), te.estimate('{"path": "a.txt"}'))
        self.assertEqual(_prompt_tokens([{"role": "user", "content": RU}], "gpt-5"), te.estimate(RU, "gpt-5") + 4)

    def test_modules_ai_pack_reports_an_estimate_with_a_range(self) -> None:
        from app import modules_ai_pack
        summary = modules_ai_pack.pack_summary(RU * 20 + CODE * 20)
        for key in ("tokens", "tokens_low", "tokens_high", "tokens_families", "tokens_mix"):
            self.assertIn(key, summary)
        self.assertTrue(summary["tokens_estimated"])
        self.assertLess(summary["tokens_low"], summary["tokens_high"])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_window_counts_file_attachments_by_their_text(self) -> None:
        """A text file in the window's counter costs what its text costs (Russian more than English); a binary
        container stays a rough size guess; context.js survives a page where tokens.js did not load."""
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuContext.fileTokens(data, model)", app)
        script = ("const fs=require('fs'), vm=require('vm'); const [tok, ctx]=process.argv.slice(1);"
                  "const box={console:{warn(){}}, atob, TextDecoder, Uint8Array, Math, String, Map};"
                  "vm.runInNewContext(fs.readFileSync(ctx,'utf8')+';this.C=MemuContext', box);"
                  "const lonely=box.C.estimateText('Привет, мир');"
                  "const b={atob, TextDecoder, Uint8Array, Math, String, Map, console};"
                  "vm.runInNewContext(fs.readFileSync(tok,'utf8').replace('\"use strict\";','')+';'+fs.readFileSync(ctx,'utf8').replace('\"use strict\";','')+';this.C=MemuContext;this.T=MemuTokens', b);"
                  "const xs=JSON.parse(fs.readFileSync(0,'utf8')); const url=(t,m)=>'data:'+m+';base64,'+Buffer.from(t).toString('base64');"
                  "console.log(JSON.stringify({lonely, ru:b.C.fileTokens(url(xs[0],'text/plain')), ruText:b.T.estimate(xs[0]),"
                  "en:b.C.fileTokens(url(xs[1],'text/markdown')), enText:b.T.estimate(xs[1]),"
                  "pdf:b.C.fileTokens(url('%PDF-1.7\\n\\u0000'+'x'.repeat(4000),'application/pdf')),"
                  "part:b.C.contentTokens([{type:'image_url', filename:'a.txt', image_url:{url:url(xs[0],'text/plain')}}]).text}));")
        out = json.loads(node(script, str(STATIC / "tokens.js"), str(STATIC / "context.js"), stdin=json.dumps([RU * 5, EN * 5])))
        self.assertGreater(out["lonely"], 0)
        self.assertEqual(out["ru"], out["ruText"])
        self.assertEqual(out["en"], out["enText"])
        self.assertEqual(out["part"], out["ruText"])
        self.assertGreater(out["pdf"], 900)
        self.assertLess(out["pdf"], 1200)

    def test_telegram_mini_app_history_budget_is_in_tokens(self) -> None:
        from app import tgapp_chat
        rows = [{"role": "user", "content": "вопрос " + RU * 40}, {"role": "assistant", "content": "ответ " + RU * 40}] * 30
        picked = tgapp_chat.history_messages(rows)
        self.assertLessEqual(sum(te.estimate(m["content"]) for m in picked[1:]), tgapp_chat.MAX_HISTORY_TOKENS)
        self.assertGreater(len(picked), 2)

    def test_no_length_divided_by_four_is_left(self) -> None:
        pattern = re.compile(r"(len\([^)]*\)|\.length|chars)\s*/{1,2}\s*(4|3\.6)\b")
        hits = []
        for path in [*(ROOT / "app").glob("*.py"), *(ROOT / "app" / "cli").rglob("*.py"), *STATIC.glob("*.js")]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and re.search(r"token", line, re.I):
                    hits.append(f"{path.name}:{number}: {line.strip()[:120]}")
        self.assertEqual(hits, [])

    def test_the_window_loads_tokens_js_before_its_users(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/tokens.js"), html.index("/context.js"))
        self.assertLess(html.index("/context.js"), html.index("/app.js"))
        self.assertIn("MemuContext.useModel(model)", (STATIC / "app.js").read_text(encoding="utf-8"))
        aipack = (STATIC / "modules-aipack.js").read_text(encoding="utf-8")
        self.assertIn("tokens_families", aipack)
        self.assertIn('L("оценка", "estimate")', aipack)

class WiringTest(unittest.TestCase):
    def test_scripts_are_loaded_in_both_windows(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        quick = (STATIC / "quick" / "index.html").read_text(encoding="utf-8")
        for page in (index, quick):
            self.assertLess(page.index("/diagram-zoom.js"), page.index("/diagrams.js"))
            self.assertIn('href="/cmd-highlight.css?v=1.0-r30"', page)
            self.assertIn('src="/cmd-highlight.js?v=1.0-r30"', page)
        self.assertLess(quick.index("/quick/quick.js"), quick.index("/cmd-highlight.js"))
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("data.send && /^\\/(image|img|video)\\s+\\S/i.test", app_js)
        quick_js = (STATIC / "quick" / "quick.js").read_text(encoding="utf-8")
        self.assertIn("!/^\\/(image|img|video)\\s/i.test(v)", quick_js)
        diagrams = (STATIC / "diagrams.js").read_text(encoding="utf-8")
        self.assertIn("MemuDiagramZoom.attach(", diagrams)

    def test_every_new_line_has_both_languages(self) -> None:
        for name in ("cmd-highlight.js", "diagram-zoom.js"):
            source = (STATIC / name).read_text(encoding="utf-8")
            for ru, en in re.findall(r't\("([^"]*)", "([^"]*)"\)', source):
                self.assertTrue(re.search(r"[а-яё]", ru, re.I), (name, ru))
                self.assertTrue(en and not re.search(r"[а-яё]", en, re.I), (name, en))

def _chromium() -> bool:
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(exe)

@unittest.skipUnless(_chromium(), "headless Chromium is not available")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run = subprocess.run([sys.executable, str(ROOT / "tests" / "round30_misc_browser.py")], capture_output=True, text=True, timeout=180)
        lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(run.stderr[-2000:])
        cls.out = json.loads(lines[-1])

    def test_the_chip_follows_the_text_and_leaves_typing_alone(self) -> None:
        cmd = self.out["cmd"]
        self.assertEqual((cmd["bare"]["chip"], cmd["bare"]["ghost"], cmd["bare"]["indent"]), ("image", True, "22px"))
        self.assertTrue(cmd["bare"]["note"].startswith("Картинка"))
        self.assertEqual((cmd["typed"]["value"], cmd["typed"]["caret"], cmd["typed"]["ghost"]), ("/image кот на луне", 18, False))
        self.assertEqual(cmd["caret_home"]["caret"], 1)
        self.assertEqual((cmd["cleared"]["chip"], cmd["cleared"]["indent"], cmd["cleared"]["note"]), ("", "", False))
        self.assertEqual((cmd["undo"]["value"], cmd["undo"]["chip"]), ("/image кот на луне", "image"))
        self.assertEqual(cmd["programmatic_clear"]["chip"], "")
        self.assertEqual(cmd["paste"]["chip"], "video")
        self.assertTrue(cmd["ime_done"]["value"].endswith("камера"))
        self.assertEqual(cmd["ime_done"]["chip"], "video")
        self.assertTrue(cmd["heights_grew"])
        self.assertGreater(int(cmd["long"]["height"].rstrip("px")), int(cmd["paste"]["height"].rstrip("px")))
        self.assertEqual(cmd["not_command"]["chip"], "")

    def test_a_scheme_is_readable_and_zooms(self) -> None:
        zoom = self.out["zoom"]
        initial = zoom["initial"]
        self.assertGreaterEqual(initial["text"], 10.9)
        self.assertTrue(initial["wide"])
        self.assertLessEqual(initial["cardWidth"], 760)
        self.assertGreater(zoom["ctrl_wheel_in"]["scale"], initial["scale"])
        self.assertNotEqual((zoom["dragged"]["left"], zoom["dragged"]["top"]), (zoom["ctrl_wheel_in"]["left"], zoom["ctrl_wheel_in"]["top"]))
        self.assertFalse(zoom["fit"]["wide"])
        self.assertEqual(zoom["one"]["pct"], "100%")
        self.assertEqual(zoom["reset"]["pct"], initial["pct"])
        self.assertGreater(zoom["dblclick"]["scale"], zoom["reset"]["scale"])
        self.assertEqual(zoom["after_theme"]["pct"], zoom["dblclick"]["pct"])
        self.assertEqual(zoom["after_theme"]["height"], initial["height"])

if __name__ == "__main__":
    unittest.main()
