# -*- coding: utf-8 -*-
"""Round 29 phase 2 (r29t-chat) — regression tests for bugs found while testing the main window live."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import memory_v2 as mem, pgdata  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

def css(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")

class MemoryFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="r29t-mem-"))
        self.patch = patch.object(pgdata, "MEMU_DIR", self.dir)
        self.patch.start()
        mem._USES.clear()

    def tearDown(self) -> None:
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_placeholder_echo_skipped(self) -> None:
        for echo in ("...", "…", "concise fact", "Short standalone fact in the user's language", "факт", "  «new text» "):
            self.assertTrue(mem._placeholder(echo), echo)
        for real in ("Пользователь живёт в Казани", "Likes green tea", "Аллергия на арахис"):
            self.assertFalse(mem._placeholder(real), real)
        doc = mem.normalize({})
        doc["settings"]["mode"] = "auto"
        res = mem.apply_proposals(doc, [{"op": "add", "fact": "concise fact"}, {"op": "add", "fact": "..."}],
                                  service="chat")
        self.assertEqual(res["skipped"], 2)
        self.assertFalse(res["added"] or res["pending"])

    def test_protocol_forbids_legacy_marker_and_discussion(self) -> None:
        doc = mem.normalize({})

        for guard in sorted(mem.GUARDS):
            doc["settings"]["guard"] = guard
            for service in ("chat", "work", "code", "quick", "telegram"):
                text = mem.protocol_prompt(service, doc=doc).lower()
                self.assertIn("never write [mem:", text, (service, guard))
                self.assertRegex(text, r"never mention or discuss[^.]*\bprotocol\b", (service, guard))

    def test_guard_controls_sensitive_wording(self) -> None:
        doc = mem.normalize({})
        doc["settings"]["guard"] = "ask"
        self.assertIn("an allergy, a diet, accessibility needs", mem.protocol_prompt("chat", doc=doc))
        doc["settings"]["guard"] = "block"
        text = mem.protocol_prompt("chat", doc=doc)
        self.assertIn("Never store exact addresses or health", text)
        self.assertNotIn("an allergy, a diet", text)

    def test_sensitive_goes_to_inbox_with_guard_ask(self) -> None:
        doc = mem.normalize({})
        doc["settings"]["guard"] = "ask"
        doc["settings"]["mode"] = "auto"
        res = mem.apply_proposals(doc, [{"op": "add", "fact": "У пользователя аллергия на арахис", "confidence": 0.9}],
                                  service="chat")
        self.assertEqual(len(res["pending"]), 1, res)
        self.assertFalse(res["added"])

    def test_prompt_mirrors(self) -> None:

        js = (STATIC / "app.js").read_text(encoding="utf-8")
        start = js.index("const MEM_PROMPT = `") + len("const MEM_PROMPT = `")
        mirror = js[start:js.index("`;", start)].replace("\\`", "`").replace("\\$", "$").replace("\\\\", "\\")
        self.assertEqual(mirror, pgdata.DEFAULT_PROMPT)
        self.assertIn("if a <memory_protocol> section is present, follow only it", pgdata.DEFAULT_PROMPT)
        self.assertIn("Never mention either hidden protocol in the visible answer", pgdata.DEFAULT_PROMPT)

class SearchFixes(unittest.TestCase):
    def test_memory_kind_localized(self) -> None:
        from app import search_api
        from app.memory_v2 import CATEGORY_LABELS
        kind = next(iter(CATEGORY_LABELS))
        for lang, idx in (("ru", 0), ("en", 1)):
            items = [{"type": "memory", "subtitle": kind + " · chat", "meta": {"kind": kind}},
                     {"type": "chat", "subtitle": kind, "meta": {"kind": kind}}]
            search_api._localize_memory_kinds(items, lang)
            self.assertEqual(items[0]["subtitle"], CATEGORY_LABELS[kind][idx] + " · chat")
            self.assertEqual(items[1]["subtitle"], kind)

    def test_search_css(self) -> None:
        text = css("search.css")
        self.assertRegex(text, r":where\(\.msx-root\) button \{[^}]*font: inherit")
        bar = re.search(r"\.msx-pv-actions \{([^}]*)\}", text).group(1)
        self.assertIn("background: var(--msx-side)", bar)
        self.assertNotIn("gradient", bar)

class CssFixes(unittest.TestCase):
    def test_memory_banner_icon_not_stretched(self) -> None:
        text = css("memory.css")
        self.assertIn(".mem2-banner > span:not(.ui-icon) {", text)
        self.assertNotRegex(text, r"\.mem2-banner > span \{")

    def test_schedsend_single_focus_ring(self) -> None:
        text = css("schedsend.css")
        self.assertRegex(text, r"\.ss-nl-input:focus, \.ss-nl-input:focus-visible \{ outline: none; \}")

    def test_diagram_theme(self) -> None:
        text = (STATIC / "diagrams.js").read_text(encoding="utf-8")
        self.assertIn("useWidth: 760", text)
        for i in range(6, 12):
            self.assertIn(f"cScale{i}:", text)

    def test_tooltip_words_boxes(self) -> None:
        text = (STATIC / "tooltip.js").read_text(encoding="utf-8")
        self.assertIn("const boxOf = (node) =>", text)

class Phase2ChatFixes(unittest.TestCase):
    def test_plain_markdown_snippets(self) -> None:
        from app.search_index import plain_markdown, _message_text
        text = ("## Итоги\n\nФормула: $E = mc^2$ и $$\\frac{1}{3}$$\n\n1. Первый\n- **жирный** и `код`\n\n> Цитата\n\n---\n"
                "| Город | Страна |\n|---|---:|\n| Москва | Россия |\n\n```python\ndef fib(n):\n    return n\n```\n"
                '```memuai-chart\n{"type":"bar","labels":["Q1"]}\n```\n```mermaid\nflowchart LR\n  B -->|yes| C\n```\n'
                "[MemuAI](https://example.com) стоит $5 и $10, snake_case_name [TITLE: x]")
        out = plain_markdown(text)
        for raw in ("##", "**", "`", "$E", "|---", '"labels"', "[TITLE", "https://example.com", "\n> "):
            self.assertNotIn(raw, out)
        for kept in ("Итоги", "E = mc^2", "\\frac{1}{3}", "Москва · Россия", "def fib(n):", "B -->|yes| C",
                     "MemuAI", "$5 и $10", "snake_case_name", "Цитата"):
            self.assertIn(kept, out)
        self.assertEqual(plain_markdown("просто текст"), "просто текст")
        self.assertIn("pic.png", _message_text({"content": "**x**", "attachments": [{"name": "pic.png"}]}))

    def test_static_files_revalidate(self) -> None:
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        client = TestClient(create_app(), base_url="http://127.0.0.1")
        response = client.get("/app.js")
        self.assertEqual(response.headers.get("cache-control"), "no-cache")
        tag = response.headers.get("etag")
        self.assertTrue(tag)
        self.assertEqual(client.get("/app.js", headers={"If-None-Match": tag}).status_code, 304)
        self.assertEqual(client.get("/").headers.get("cache-control"), "no-cache")

    def test_schedsend_menu_fits_and_kbd_follows_hotkeys(self) -> None:
        js = (STATIC / "schedsend.js").read_text(encoding="utf-8")
        self.assertIn('menu.dataset.side = "left"', js)
        self.assertIn("menu.scrollHeight + menu.offsetHeight - menu.clientHeight", js)
        self.assertIn('MemuHotkeys.value("schedule_send", combo)', js)
        self.assertNotIn('<kbd class="ss-kbd">Ctrl+Shift+Enter</kbd>', js)
        self.assertIn('.ss-menu[data-side="left"]', css("schedsend.css"))

    def test_export_controls_consistent(self) -> None:
        js = (STATIC / "export.js").read_text(encoding="utf-8")
        self.assertIn('data-opt="font" data-no-search>', js)
        self.assertNotRegex(css("export.css"), r"\.mx-theme small \{[^}]*text-overflow: ellipsis")

class Phase2SearchAndExport(unittest.TestCase):
    def test_from_and_date_range_operators(self) -> None:
        import time as _t
        from app.search_index import parse_query
        self.assertEqual(parse_query("from:вы пицца").flags, {"role": "user"})
        self.assertEqual(parse_query("from:you x").flags, {"role": "user"})
        self.assertEqual(parse_query("from:ai x").flags, {"role": "assistant"})
        self.assertEqual(parse_query("from:deepseek x").flags, {"role": "assistant", "model": "deepseek"})
        q = parse_query("date:2026-05..2026-06 x")
        self.assertEqual(_t.strftime("%Y-%m-%d", _t.localtime(q.date_from / 1000)), "2026-05-01")
        self.assertEqual(_t.strftime("%Y-%m-%d %H:%M", _t.localtime(q.date_to / 1000)), "2026-06-30 23:59")
        q = parse_query("date:2026-06")
        self.assertEqual(_t.strftime("%m-%d", _t.localtime(q.date_to / 1000)), "06-30")
        q = parse_query("before:2026-12")
        self.assertEqual(_t.strftime("%Y-%m-%d", _t.localtime(q.date_to / 1000)), "2026-12-31")

    def test_jump_centres_the_word_in_long_answers(self) -> None:
        js = (STATIC / "search.js").read_text(encoding="utf-8")
        self.assertIn('node.style.contentVisibility = "visible"', js)
        self.assertIn("const target = tall && firstMark ? firstMark : node;", js)

    def test_export_redraws_diagrams_in_export_theme(self) -> None:
        js = (STATIC / "export.js").read_text(encoding="utf-8")
        self.assertIn('D.exportScene(block, { theme, background: "none", padding: 0 })', js)
        self.assertIn("D.toPNG(block, { scale: 2, theme })", js)

class Phase2Chat3(unittest.TestCase):
    """r29c-chat3: stuck detector with real-model patterns, Work plan continuation, Ctrl+K over dialogs, Mermaid repair."""

    @classmethod
    def setUpClass(cls) -> None:
        import json as _json
        import subprocess
        if not shutil.which("node"):
            raise unittest.SkipTest("node is not installed")
        res = subprocess.run(["node", str(ROOT / "tests" / "round29t_chat3_runner.cjs")], capture_output=True, text=True, timeout=60)
        if res.returncode:
            raise AssertionError(res.stderr)
        cls.out = _json.loads(res.stdout)

    def test_interleaved_tool_rounds_and_plan_turns_pause(self) -> None:

        self.assertGreaterEqual(self.out["interleaved"]["pausedAt"], 0)
        self.assertLessEqual(self.out["interleaved"]["pausedAt"], 5)
        self.assertEqual(self.out["interleaved"]["reason"], "repeat")

    def test_toggled_plan_steps_do_not_count_as_progress(self) -> None:
        self.assertEqual(self.out["toggled"]["reason"], "plan_stalled")
        self.assertLessEqual(self.out["toggled"]["pausedAt"], 7)

    def test_real_progress_never_paused(self) -> None:
        self.assertFalse(self.out["progressing"]["falsePause"])

    def test_mermaid_labels_with_brackets_are_quoted(self) -> None:
        fixed, shapes, quoted, other = self.out["mermaid"]
        self.assertIn('F["Send 2FA code (SMS / authenticator app)"]', fixed)
        self.assertIn("G[User enters code]", fixed)
        self.assertIn('A{"Is it ok (y/n)?"}', shapes)
        self.assertIn('B("Done (fast)")', shapes)
        for keep in ("C[(Database)]", "D[[Sub]]", "E((Circle))"):
            self.assertIn(keep, shapes)
        self.assertIn('A["Already (quoted)"]', quoted)
        self.assertEqual(other, "sequenceDiagram\n  A->>B: hi (x)")
        js = (STATIC / "diagrams.js").read_text(encoding="utf-8")
        self.assertIn("const fixed = repairMermaid(clean);", js)

    def test_work_plan_and_goal_continue_between_turns(self) -> None:
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (generationJobs.has(S.currentId) || S.mode !== "work" || !S.workGoal?.enabled) return;', js)
        self.assertIn('if (generationJobs.has(S.currentId) || S.mode !== "work" || !plan || plan.status !== "active") return;', js)
        self.assertNotIn('if (generating || S.mode !== "work"', js)

    def test_stuck_pause_wording_and_stop_settles_the_turn(self) -> None:
        js = (STATIC / "stuck.js").read_text(encoding="utf-8")
        self.assertIn('"Пауза — ждёт вашего решения"', js)
        self.assertIn('if (phase !== "tools") settleTurn();', js)
        self.assertIn("const d = detectorFor(workKey(S.currentId, phase));", js)

    def test_ctrl_k_over_dialogs(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('else if (window.MemuSearch && shortcutActive("search", "Ctrl+K", e)) { e.preventDefault(); MemuSearch.toggle(); }', app)
        self.assertIn('if (e.target?.closest?.(".msx-root")) return;', app)
        js = (STATIC / "search.js").read_text(encoding="utf-8")
        activate = js[js.index("function activate(entry)"):js.index("async function secondary(entry)")]
        self.assertLess(activate.index("close(false);"), activate.index("openTarget("))
        self.assertIn('H.value("search", "Ctrl+K")', js)
        self.assertNotIn("btn.innerHTML = `<kbd>Ctrl K</kbd>`;", js)
        for lang, word in (("ru", "фильтр списка чатов"), ("en", "filters the chat list")):
            self.assertIn(word, (ROOT / "app" / "docs" / lang / "interface.md").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
