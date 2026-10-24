"""Round 33 (r33d-fixes): regressions of the final fixes.

* a bare ``/effort`` opens the picker (``reasoning_levels.ALIASES`` maps "" to "auto");
* Work-only tools (project_tree, file_exists) are not offered in plain chats and fail with a bilingual message;
* the Tools block shows «2 of 3 succeeded» instead of «failed» when the answer itself succeeded;
* Proxy-only static files never name the chat app in comments (build.py check).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")


class EffortCommandTest(unittest.TestCase):
    def test_bare_effort_is_not_aliased_to_auto(self) -> None:
        source = (ROOT / "app" / "cli" / "commands" / "model_cmds.py").read_text(encoding="utf-8")
        body = source.split("async def effort(", 1)[1].split("\n@command(", 1)[0]
        self.assertIn('if typed else ""', body)
        from app import reasoning_levels
        self.assertEqual(reasoning_levels.ALIASES.get(""), "auto")   # the reason the guard exists


class WorkOnlyToolsTest(unittest.TestCase):
    def test_default_prompts_do_not_list_work_only_tools(self) -> None:
        from app.pgdata import DEFAULT_PROMPT
        local = DEFAULT_PROMPT.split("<local_tools>", 1)[1].split("</local_tools>", 1)[0]
        self.assertNotIn("project_tree", local)
        self.assertNotIn("file_exists", local)
        js_local = APP_JS.split("<local_tools>", 1)[1].split("</local_tools>", 1)[0]
        self.assertNotIn("project_tree", js_local)
        self.assertIn("network_status, app_version", js_local)

    def test_chat_strips_them_from_old_prompt_copies_and_refuses_calls(self) -> None:
        self.assertIn('if (!isProjectMode(context.mode)) base = base.replace(/,\\s*project_tree,\\s*file_exists(?=,)/, "");',
                      APP_JS)
        old = "environment, project_tree, file_exists, network_status, app_version."
        self.assertEqual(re.sub(r",\s*project_tree,\s*file_exists(?=,)", "", old, count=1),
                         "environment, network_status, app_version.")
        self.assertIn('name === "project_tree" || name === "file_exists")) {', APP_JS)

    def test_backend_error_is_bilingual(self) -> None:
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("This tool works only in Work and MemuCode (open a Work project).", source)
        self.assertIn("Этот инструмент работает только в Work и MemuCode (откройте Work-проект).", source)
        self.assertIn("A Work project id is required", source)


class ToolsBlockStatusTest(unittest.TestCase):
    def test_partial_status_instead_of_failed(self) -> None:
        self.assertIn("statusLabel = \"\" }) {", APP_JS)
        self.assertIn('const failed = m.toolCalls?.status === "cancelled" || (!answered && lastRows.length', APP_JS)
        self.assertIn("`${okCalls} of ${calls.length} succeeded`", APP_JS)
        self.assertIn("`${okCalls} из ${calls.length} успешно`", APP_JS)
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".step.is-partial .step-status", css)


class ProxyOnlyStaticTest(unittest.TestCase):
    def test_guide_video_comments_do_not_name_the_chat_app(self) -> None:
        for name in ("guide-video.js", "guide-video.css"):
            text = (ROOT / "app" / "static" / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bMemuAI\b(?!\s+Proxy)", text), name)
            self.assertEqual(text, (ROOT / "app" / "pgstatic" / name).read_text(encoding="utf-8"), name)


class NotionDocstringTest(unittest.TestCase):
    def test_points_to_the_developer_portal(self) -> None:
        import app.integrations_notion as notion
        self.assertNotIn("notion.so/profile/integrations", notion.__doc__)
        self.assertIn("app.notion.com/developers", notion.__doc__)


if __name__ == "__main__":
    unittest.main()
