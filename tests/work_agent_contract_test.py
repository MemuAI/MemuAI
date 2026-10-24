"""Focused contracts for Work context, browser safety, and new UI modes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import pgdata, scheduler
from app.automation_runtime import ComputerAutomation, _json_object, _safe_navigation_url
from app.work_context import build_work_context


ROOT = Path(__file__).resolve().parents[1]


class WorkContextContracts(unittest.TestCase):
    def test_selector_prioritizes_named_source_and_skips_generated_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "node_modules").mkdir()
            (root / "src" / "billing.py").write_text(
                "def calculate_invoice_total(items):\n    return sum(items)\n", encoding="utf-8")
            (root / "src" / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "node_modules" / "billing.py").write_text("secret = 'skip'\n", encoding="utf-8")

            result = build_work_context(root, "Исправь calculate_invoice_total в src/billing.py")

            self.assertEqual(result["files"][0]["path"], "src/billing.py")
            self.assertIn("calculate_invoice_total", result["files"][0]["text"])
            self.assertFalse(any(path.startswith("node_modules/") for path in result["index"]))
            self.assertLessEqual(result["chars"], 60_000)


class AutomationSafetyContracts(unittest.TestCase):
    def test_action_json_parser_ignores_wrapping_text(self) -> None:
        self.assertEqual(_json_object('note\n{"action":"done","report":"ok"}\nextra')["report"], "ok")
        with self.assertRaises(ValueError):
            _json_object("not json")

    def test_browser_url_policy_blocks_credentials_and_private_networks(self) -> None:
        self.assertEqual(_safe_navigation_url("localhost:3000"), "https://localhost:3000")
        with self.assertRaises(ValueError):
            _safe_navigation_url("file:///C:/private.txt")
        with self.assertRaises(ValueError):
            _safe_navigation_url("https://user:password@example.com")
        with patch("app.automation_runtime.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("192.168.1.5", 443))]):
            with self.assertRaises(ValueError):
                _safe_navigation_url("https://private.example")
        with patch("app.automation_runtime.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.assertEqual(_safe_navigation_url("example.com/path"), "https://example.com/path")

    def test_computer_runtime_rejects_destructive_or_secret_text(self) -> None:
        blocked = ComputerAutomation._PROHIBITED_TYPED_TEXT
        self.assertRegex("Remove-Item C:\\data -Recurse", blocked)
        self.assertRegex("API key = super-secret", blocked)
        self.assertNotRegex("Please rename the heading to Overview", blocked)


class WorkSchedulingContracts(unittest.TestCase):
    def test_scheduled_work_parser_accepts_safe_files_only(self) -> None:
        response = (
            "Done\n```memuai-work\n{\"path\":\"reports/today.txt\"}\nhello\n```\n"
            "```memuai-work\n{\"path\":\"../escape.txt\"}\nno\n```"
        )
        clean, files = scheduler._scheduled_work_actions(response)
        self.assertEqual(files, [{"path": "reports/today.txt", "content": "hello\n"}])
        self.assertEqual(clean, "Done")

    def test_preferences_migrate_old_auto_apply_and_bound_context_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, "MEMU_DIR", Path(temp)):
            (Path(temp) / "preferences.json").write_text(json.dumps({
                "work_auto_apply": True,
                "context_compression_threshold": 999,
                "context_recent_messages": 1,
                "model_context_limits": {"model-a": 12, "": 99, "model-b": "bad"},
            }), encoding="utf-8")
            prefs = pgdata.load_preferences()
            self.assertEqual(prefs["work_access_mode"], "full")
            self.assertTrue(prefs["work_auto_apply"])
            self.assertEqual(prefs["context_compression_threshold"], 98)
            self.assertEqual(prefs["context_recent_messages"], 6)
            self.assertEqual(prefs["model_context_limits"], {"model-a": 2000, "model-b": 260000})


class WorkUiContracts(unittest.TestCase):
    def test_work_controls_and_right_click_search_mode_are_wired(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        for element_id in ("work-access", "work-plan", "work-goal", "automation-stage"):
            self.assertIn(f'id="{element_id}"', html)
        # Browser automation may be opened manually without granting the AI a
        # Computer Use capability.  Computer Use itself remains AI-only.
        self.assertIn('id="work-browser"', html)
        self.assertNotIn('id="work-computer"', html)
        self.assertNotIn('class="cap-caret"', html)
        render_caps = js[js.index("function renderCaps()") : js.index("/* ------------------------------ чаты")]
        self.assertIn("ts.oncontextmenu = openSearchModeMenu", render_caps)
        self.assertIn('case "/plan"', js)
        self.assertIn('case "/access"', js)
        self.assertIn('case "/goal"', js)
        self.assertIn('"@Computer"', js)
        self.assertIn('"@Browser"', js)
        self.assertIn("memuai-plan", js)

    def test_automation_and_context_contracts_are_exposed(self) -> None:
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/work/context", "/api/work/plan", "/api/work/browser/run",
                         "/api/work/computer/run", "/api/pg/context/compress"):
            self.assertIn(endpoint, api)
        self.assertIn("compactHistoryIfNeeded", js)
        self.assertIn("contextSummaryThrough", js)
        self.assertIn("automationRunning", js)

    def test_packaged_app_contains_visual_automation_dependencies(self) -> None:
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        # build_exe.bat только вызывает build.py: зависимости перечислены там.
        self.assertIn("build.py", (ROOT / "build_exe.bat").read_text(encoding="utf-8"))
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for dependency in ("websockets", "PIL.ImageGrab", "tkinter"):
            self.assertIn(dependency, spec)
            self.assertIn(dependency, build)
        self.assertIn("pillow", requirements.lower())
        self.assertIn("faster-whisper", requirements.lower())
        self.assertIn("faster_whisper", spec)


class FileRewriteGuard(unittest.TestCase):
    def test_full_rewrite_that_drops_most_of_a_file_is_refused(self) -> None:
        from app.work_tools import destructive_shrink, tool_write

        before = "\n".join(f"line {index}" for index in range(20)) + "\n"
        after = "line 0\nline 1\n"
        reason = destructive_shrink(before, after)
        self.assertIn("work_patch", reason)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "notes.py"
            target.write_text(before, encoding="utf-8")
            with self.assertRaises(Exception) as caught:
                tool_write(root, {"path": "notes.py", "text": after})
            self.assertIn("work_patch", str(caught.exception))
            self.assertEqual(target.read_text(encoding="utf-8"), before)
            written = tool_write(root, {"path": "notes.py", "text": after, "allow_shrink": True})
            self.assertIn("diff", written)
            self.assertEqual(target.read_text(encoding="utf-8"), after)

    def test_small_edits_and_new_files_stay_writable(self) -> None:
        from app.work_tools import destructive_shrink

        self.assertEqual(destructive_shrink("", "hello\n"), "")
        self.assertEqual(destructive_shrink("one\n", "two\n"), "")
        self.assertEqual(destructive_shrink("\n".join("x" for _ in range(10)), "\n".join("y" for _ in range(9))), "")


if __name__ == "__main__":
    unittest.main()
