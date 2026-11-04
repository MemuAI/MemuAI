"""MemuMeet: checklist extraction, summary styles, export, settings and the redesigned UI wiring."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MEET_DIR = ROOT / "app" / "module_builtins" / "meet"

spec = importlib.util.spec_from_file_location("memu_meet_backend_v2_test", MEET_DIR / "backend.py")
backend = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(backend)

from app import module_actions as ma  # noqa: E402
from app.characters_api import store_set  # noqa: E402

SUMMARY = """## Конспект
- Обсудили релиз.

## Задачи
- [ ] Подготовить сборку установщика — Иван, до 15.11
- [x] **Мария**: написать changelog
- Проверить нагрузку прокси (ответственный: Пётр)
- Подготовить сборку установщика

## Решения
- Релиз в пятницу.
"""


class Tasks(unittest.TestCase):
    def test_extract_tasks_reads_boxes_owners_due_and_dedupes(self) -> None:
        tasks = backend.extract_tasks(SUMMARY)
        self.assertEqual([t["text"] for t in tasks],
                         ["Подготовить сборку установщика", "написать changelog", "Проверить нагрузку прокси"])
        self.assertEqual(tasks[0]["owner"], "Иван")
        self.assertTrue(tasks[0]["due"].startswith("15.11"))
        self.assertFalse(tasks[0]["done"])
        self.assertEqual(tasks[1]["owner"], "Мария")
        self.assertTrue(tasks[1]["done"])
        self.assertEqual(tasks[2]["owner"], "Пётр")

    def test_extract_tasks_ignores_other_sections_and_english_heading(self) -> None:
        self.assertEqual(backend.extract_tasks("## Решения\n- Релиз в пятницу"), [])
        rows = backend.extract_tasks("**Action items:**\n- Ship the build — Ann\n- Ship the build")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "Ann")

    def test_clean_tasks_and_lines(self) -> None:
        rows = backend.clean_tasks([{"text": " A ", "owner": "Bob", "done": 1, "junk": "x"}, {"text": ""}, "bad"])
        self.assertEqual(rows, [{"text": "A", "owner": "Bob", "due": "", "done": True}])
        self.assertEqual(backend.task_lines({"tasks": rows}), ["- [x] A — Bob"])
        self.assertEqual(backend.task_key("**Сделать** отчёт!"), backend.task_key("сделать   отчёт"))


class SummaryAndExport(unittest.TestCase):
    def test_every_style_has_hints_and_prompt_mentions_it(self) -> None:
        self.assertIn("standard", backend.SUMMARY_STYLES)
        for style in backend.SUMMARY_STYLES:
            self.assertIn(style, backend.STYLE_HINTS)
        brief = backend.summary_prompt("текст встречи", lang="ru", style="brief")
        standard = backend.summary_prompt("текст встречи", lang="ru", style="standard")
        self.assertNotEqual(brief, standard)
        # an unknown style falls back to the standard prompt instead of failing
        self.assertEqual(backend.summary_prompt("текст встречи", lang="ru", style="??"), standard)

    def test_export_carries_checklist_notes_and_markers(self) -> None:
        meeting = backend.new_meeting("Планёрка", source="microphone")
        meeting.update(summary=SUMMARY, transcript="Всем привет", notes="Позвонить в банк",
                       tasks=backend.extract_tasks(SUMMARY), markers=[{"t": 43, "label": "Риск"}])
        md = backend.build_export(meeting, kind="markdown")
        self.assertIn("- [ ] Подготовить сборку установщика", md)
        self.assertIn("- [x] написать changelog", md)
        self.assertIn("Позвонить в банк", md)
        self.assertIn("Риск", md)
        txt = backend.build_export(meeting, kind="text")
        self.assertIn("Позвонить в банк", txt)
        row = backend.meeting_summary(meeting)
        self.assertEqual((row["tasks"], row["tasks_open"], row["markers"]), (3, 2, 1))
        self.assertNotIn("audio", json.dumps(row).replace("has_audio", ""))

    def test_settings_keep_known_values_only(self) -> None:
        defaults = backend.default_settings()
        self.assertEqual(defaults["summary_style"], "standard")
        self.assertFalse(defaults["auto_transcribe"])
        self.assertTrue(defaults["auto_tasks"])
        clean = backend.clean_settings({"summary_style": "protocol", "auto_transcribe": True, "auto_tasks": False,
                                        "evil": 1, "keep_audio": "yes"})
        self.assertEqual(clean["summary_style"], "protocol")
        self.assertTrue(clean["auto_transcribe"])
        self.assertFalse(clean["auto_tasks"])
        self.assertNotIn("evil", clean)
        self.assertEqual(clean["keep_audio"], defaults["keep_audio"])
        self.assertEqual(backend.clean_settings({"summary_style": "nope"})["summary_style"], "standard")


class TasksDigestAction(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "modules"
        self._env = os.environ.get(ma.OUTPUT_ROOT_ENV)
        os.environ[ma.OUTPUT_ROOT_ENV] = str(Path(self.tmp.name) / "out")

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop(ma.OUTPUT_ROOT_ENV, None)
        else:
            os.environ[ma.OUTPUT_ROOT_ENV] = self._env
        self.tmp.cleanup()

    def test_structured_checklist_wins_over_summary_text(self) -> None:
        now = time.time() * 1000
        store_set(self.root, "meet", "meetings/m1", {
            "id": "m1", "title": "Планёрка", "created_at": now, "summary": SUMMARY,
            "tasks": [{"text": "Открытая", "owner": "Иван", "done": False}, {"text": "Готовая", "done": True}]})
        store_set(self.root, "meet", "meetings/m2", {
            "id": "m2", "title": "Старая", "created_at": now, "summary": "## Задачи\n- Из текста"})
        result = asyncio.run(ma.run_module_action("meet", "meet.tasks_digest", root=self.root, dry=True))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["meetings"], 2)
        self.assertEqual(result["data"]["tasks"], 2)  # "Открытая" + "Из текста"; the ticked one is skipped
        self.assertEqual(result["data"]["tasks_open"], 2)
        self.assertIn("Открытая — Иван", result["text"])
        self.assertNotIn("Готовая", result["text"])
        both = asyncio.run(ma.run_module_action("meet", "meet.tasks_digest", root=self.root, dry=True,
                                                settings={"include_done": True}))
        self.assertEqual(both["data"]["tasks"], 3)


class UiWiring(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (MEET_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (MEET_DIR / "main.js").read_text(encoding="utf-8")
        self.i18n = json.loads((MEET_DIR / "i18n.json").read_text(encoding="utf-8"))

    def test_every_i18n_key_used_exists_in_both_languages(self) -> None:
        self.assertEqual(set(self.i18n["ru"]), set(self.i18n["en"]))
        used = set(re.findall(r'data-i18n(?:-placeholder|-aria-label|-title)?="([^"]+)"', self.html))
        used |= set(re.findall(r'\bt\("([a-z_]+\.[a-z0-9_.]+)"', self.js))
        missing = sorted(k for k in used if not k.endswith(".") and k not in self.i18n["ru"])
        self.assertEqual(missing, [])

    def test_detail_tabs_have_panes_and_main_js_knows_them(self) -> None:
        tabs = re.findall(r'data-dtab="([a-z]+)"', self.html)
        panes = re.findall(r'data-dpane="([a-z]+)"', self.html)
        self.assertEqual(sorted(set(tabs)), sorted(set(panes)))
        for name in ("summary", "transcript", "tasks", "notes", "ask", "export"):
            self.assertIn(name, tabs)
        for element_id in ("stats", "sort", "filters", "detail-star", "tasks", "notes", "ask-form", "rec-mark",
                           "set-style", "set-auto-transcribe", "set-auto-tasks", "storage-bar"):
            self.assertIn(f'id="{element_id}"', self.html, element_id)
            self.assertIn(f'"#{element_id}"', self.js, element_id)

    def test_frontend_mirrors_backend_and_keeps_safety_rules(self) -> None:
        self.assertIn("function extractTasks", self.js)
        self.assertIn("function flushNotes", self.js)
        # styles in the UI are exactly the backend's
        for style in backend.SUMMARY_STYLES:
            self.assertIn(f'value="{style}"', self.html, style)
        # nothing leaves the module without a click: no automatic summarise on open
        self.assertIn("confirm_before_summary", self.js)
        self.assertNotIn("innerHTML = current.summary", self.js)


if __name__ == "__main__":
    unittest.main()
