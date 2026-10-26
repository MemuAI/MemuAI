"""Round 29 phase 2 (r29e-matrix): design matrix fixes, relative time wording, MemuCode stuck pause."""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def _js_function(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    depth = 0
    for j in range(src.index("{", i), len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(name)

def _node(script: str) -> str:
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout.strip()

CASES = (43 * 3600, 25 * 3600, int(47.9 * 3600), 24 * 3600 - 20, 60 * 3600, 5 * 86400 + 3600, 2 * 3600 + 15 * 60)
RU = ["через 1 день 19 ч", "через 1 день 1 ч", "через 2 дня", "через 1 день", "через 2 дня 12 ч", "через 5 дней", "через 2 ч 15 мин"]
EN = ["in 1 day 19 h", "in 1 day 1 h", "in 2 days", "in 1 day", "in 2 days 12 h", "in 5 days", "in 2 h 15 min"]

class RelativeTimeUnified(unittest.TestCase):
    """Desktop said «через 43 ч», the Mini App «через 2 дня» for the same run: both now use days beyond 24 h."""

    def test_desktop_and_mini_app_agree(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        for path, tname in (("app/pgstatic/plan.js", "T"), ("app/tgapp/plan.js", "tr")):
            src = (ROOT / path).read_text(encoding="utf-8")
            plural = _js_function(src, "ruPlural" if "function ruPlural(" in src else "plural")
            for lang, want in (("ru", RU), ("en", EN)):
                js = (f"const {tname}=(r,e)=>'{lang}'==='en'?e:r; {plural}\n{_js_function(src, 'relIn')}\n"
                      f"const now=Date.now()/1000; Date.now=()=>now*1000; console.log(JSON.stringify({list(CASES)}.map(x=>relIn(now+x))))")
                import json
                self.assertEqual(json.loads(_node(js)), want, (path, lang))

    def test_telegram_bot_when_text_agrees(self) -> None:
        from app import schedule_quick as sq
        now = 1_800_000_000.0
        got = [sq.when_text(now + x, "ru", now).split(" · ")[1] for x in CASES]
        self.assertEqual(got, RU)
        got = [sq.when_text(now + x, "en", now).split(" · ")[1] for x in CASES]
        self.assertEqual(got, EN)

class MemuCodeCliStuckPause(unittest.TestCase):
    """Live (pty) run with a looping model: the old duplicate-call hard stop ended the turn one round before the stuck
    detector could pause, so an interactive user never saw the pause."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests import memucode_cli_test as m
        m.setUpModule()
        cls.m = m

    @classmethod
    def tearDownClass(cls) -> None:
        cls.m.tearDownModule()

    def _ui(self, answers):
        m = self.m

        class Asking(m.Recorder):
            def __init__(self):
                super().__init__()
                self.questions = []

            async def ask_question(self, question, options, multi=False):
                self.questions.append((question, list(options)))
                return answers.pop(0) if answers else None
        return Asking()

    def test_interactive_loop_is_paused_not_hard_stopped(self) -> None:
        from app.cli.agent import Agent
        from app.cli import agent as agent_mod
        ls = '```memuai-tool\n[{"tool":"LS","args":{}}]\n```'
        ui = self._ui(["Stop"])
        runtime, backend = self.m.make_runtime(turns=[ls] * (agent_mod.MAX_DUPLICATE_ROUNDS + 6), ui=ui)
        result = self.m.run(Agent(runtime).run_turn("list forever"))
        self.assertEqual(len(ui.questions), 1, "the pause question must be asked")
        question, options = ui.questions[0]
        self.assertEqual(options, ["Continue", "Continue with a hint", "Change approach", "Stop"])
        self.assertIn("\n\n", question)
        self.assertIn("- `LS", question)
        self.assertNotIn("Continue / hint", question)
        self.assertEqual(result.stopped, "stuck")
        self.assertTrue(result.error)
        self.assertFalse(any("Loop guard" in str(r.messages[-1].get("content")) for r in backend.requests))

    def test_headless_keeps_the_final_answer_guard(self) -> None:
        from app.cli.agent import Agent
        from app.cli import agent as agent_mod
        ls = '```memuai-tool\n[{"tool":"LS","args":{}}]\n```'
        runtime, backend = self.m.make_runtime(turns=[ls] * (agent_mod.MAX_DUPLICATE_ROUNDS + 1) + ["Done."])
        runtime.ui.interactive = False
        result = self.m.run(Agent(runtime).run_turn("list forever"))
        self.assertEqual(result.text, "Done.")
        self.assertIn("Loop guard", str(backend.requests[-1].messages[-1].get("content")))

    def test_tool_agents_get_a_tool_hint(self) -> None:
        from app import stuck_detector as sd
        d = sd.StuckDetector("normal", area="cli")
        step = sd.cli_round([{"name": "Bash", "args": {"command": "cat status.txt"}}])
        hints = [d.observe(step["action"], label=step["label"]) for _ in range(2)]
        self.assertEqual(hints[-1].level, "hint")
        self.assertNotIn("element", hints[-1].model_hint)
        self.assertIn("tool", hints[-1].model_hint)
        browser = sd.StuckDetector("normal", area="browser")
        for _ in range(2):
            v = browser.observe({"action": "click", "x": 1, "y": 1}, state="s")
        self.assertIn("element", v.model_hint)

    def test_tui_reports_the_stop_and_opens_text_questions_in_text_mode(self) -> None:
        src = (ROOT / "app/cli/tui/app.py").read_text(encoding="utf-8")
        self.assertIn('result.stopped == "stuck" and result.error', src)
        body = src[src.index("    async def ask_question("):src.index("    async def approve_plan(")]
        self.assertIn("if not options:\n            dialog.text_mode = True", body)

class MemuCodeWindowStuckPause(unittest.TestCase):
    def test_label_badge_and_typed_verdict(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        import json
        out = json.loads(_node_file(ROOT / "tests" / "round29e_matrix_runner.cjs", str(ROOT)))
        self.assertIn("pausedAt", out)
        self.assertEqual(out["steps"], ["code_run cat status.txt"])
        self.assertTrue(out["badge"])
        self.assertTrue(out["cleared"])
        self.assertEqual(out["status"], "cancelled")

    def test_composer_settles_on_pause(self) -> None:
        js = (ROOT / "app/pgstatic/stuck.js").read_text(encoding="utf-8")
        self.assertIn("settleWhenIdle(ctx.chatId", js)

def _node_file(path: Path, *args: str) -> str:
    return subprocess.run(["node", str(path), *args], capture_output=True, text=True, check=True, timeout=30).stdout.strip()

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        h = h.lstrip("#")
        ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)

class DesignMatrixWaveF(unittest.TestCase):
    """r29f: fixes found by the design × theme × language × size matrix."""

    def test_plan_step_number_is_readable_on_amber_and_green(self) -> None:
        import re
        css = _read("app/pgstatic/plan.css")
        root = re.search(r"\.pln-root \{[^}]*--pi: (#[0-9a-f]{6}); --pd: (#[0-9a-f]{6});", css)
        ink = re.search(r"\.b-if, \.b-do \{ --ci: (#[0-9a-f]{6}); \}", css)
        self.assertTrue(root and ink, "amber/green blocks must set dark ink")
        for bg in root.groups():
            self.assertLess(_contrast("#ffffff", bg), 3.0)
            self.assertGreaterEqual(_contrast(ink.group(1), bg), 4.5)
        step = css[css.index(".pln-step {"):]
        self.assertIn("color: var(--ci, #fff)", step[:step.index("}")])
        self.assertIn("color: var(--ci, #fff); background: var(--c); }", css)

    def test_search_placeholders_fit(self) -> None:
        hk = _read("app/pgstatic/hotkeys.js")
        line = next(x for x in hk.splitlines() if 'id="hk-query"' in x)
        self.assertIn("Найти действие или клавишу…", line)
        self.assertIn("Find an action or key…", line)
        self.assertIn('title="${escHtml(L("Найти действие или клавишу, например', line)
        docs = _read("app/pgstatic/docs.js")
        line = docs[docs.index('id="docs-query"'):docs.index("</label>", docs.index('id="docs-query"'))]
        self.assertIn('placeholder="${L("Поиск статей", "Search docs")}"', line)
        self.assertIn('aria-label="${L("Поиск по документации", "Search the documentation")}"', line)

    def test_chip_rows_wrap_instead_of_hiding(self) -> None:
        css = _read("app/pgstatic/search.css")
        rule = css[css.index(".msx-chips {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("flex-wrap: wrap", rule)
        self.assertNotIn("mask-image", rule)
        self.assertNotIn("overflow-x: auto", rule)
        ch = _read("app/module_builtins/characters/style.css")
        rule = ch[ch.index(".ch-tagbar{"):]
        rule = rule[:rule.index("}")]
        self.assertIn("flex-wrap:wrap", rule)
        self.assertNotIn("mask-image", rule)

        self.assertNotIn("overflow", rule)
        self.assertNotIn(".ch-tagbar::-webkit-scrollbar{display:none}", ch)
        js = _read("app/module_builtins/characters/main.js")
        self.assertIn('more.textContent = "+" + hidden;', js)
        self.assertIn('t("tags_less")', js)

    def test_long_model_ids_get_a_tooltip(self) -> None:
        js = _read("app/pgstatic/app.js")
        pretty = js[js.index("function enhanceSelects("):js.index("const openMenu = () =>", js.index("function enhanceSelects("))]
        self.assertIn('button.title = (selected?.text || "").length > 20 ? selected.text', pretty)
        self.assertIn("selected.value.length > (selected.text || \"\").length + 3 ? selected.value", pretty)
        self.assertIn("option.text.length > 28 ? `title=", pretty)
        i = js.index('wrap.className = "mpick";')
        self.assertIn('button.title = label.length > 20 ? label : "";', js[i:i + 4000])
        css = _read("app/pgstatic/style.css")
        self.assertRegex(css, r"\.pretty-select-button span \{[^}]*text-overflow:ellipsis")
        self.assertRegex(css, r"\.mpick-value \{[^}]*text-overflow: ellipsis")

    def test_keyboard_highlight_does_not_beat_design_selected_styles(self) -> None:
        css = _read("app/pgstatic/settings.css")
        self.assertIn(":root :where(.pretty-select-menu, #context-menu, .context-menu) button.active {", css)
        self.assertNotIn(":is(.pretty-select-menu, #context-menu, .context-menu) button.active", css)
        brutal = _read("app/pgstatic/designs/brutal.css")
        self.assertIn(".pretty-select-menu button.selected, .plus-row.is-active) { background: var(--accent); color: var(--on-accent); }", brutal)

    def test_palette_input_has_no_design_focus_ring_and_safe_banner_text_fits(self) -> None:
        self.assertIn(":root .msx-root .msx-input:focus-visible { outline: none; box-shadow: none; }", _read("app/pgstatic/search.css"))
        self.assertIn("-webkit-line-clamp: 3; line-clamp: 3;", _read("app/pgstatic/safe-mode.css"))
        plan = _read("app/pgstatic/plan.css")
        self.assertIn(".pln-hint.warn { color: color-mix(in srgb, #e3a33b 60%, var(--text)); }", plan)
        self.assertIn('placeholder="${esc(t("Конфиденциально", "Confidential"))}"', _read("app/pgstatic/export.js"))

if __name__ == "__main__":
    unittest.main()
