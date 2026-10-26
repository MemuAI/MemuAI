"""Round 59 regression tests: UI redesigns, Teams active-section exit, panel-restored, Modules, MemuDB, and audit fixes."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class Round59BackendAndAuditTests(unittest.TestCase):
    def test_teams_save_preserves_order_and_runtime_snapshot_keeps_is_lead(self) -> None:
        from app import teams
        from app.team_runtime import TeamRun

        with tempfile.TemporaryDirectory() as tmp:
            teams_file = Path(tmp) / "teams.json"
            with patch.object(teams, "_path", return_value=teams_file):
                t1 = teams.save(
                    {"name": "Alpha", "manager_model": "m1", "members": [{"name": "A", "role": "Analyst", "model": "m1"}]},
                    profile_id="p1",
                )
                t2 = teams.save(
                    {"name": "Beta", "manager_model": "m2", "members": [{"name": "B", "role": "Reviewer", "model": "m2"}]},
                    profile_id="p1",
                )
                teams.save({**t1, "name": "Alpha Edited"}, profile_id="p1")
                saved_ids = [row["id"] for row in teams.load("p1")]
                self.assertEqual(saved_ids, [t1["id"], t2["id"]])

        team_spec = {
            "id": "team-1",
            "name": "Engineering",
            "manager_model": "mgr-model",
            "lead_member_id": "m-lead",
            "members": [
                {"id": "m-lead", "name": "Lead", "role": "Tech Lead", "model": "lead-model", "is_lead": True},
                {"id": "m-spec", "name": "Dev", "role": "Developer", "model": "dev-model", "is_lead": False},
            ],
        }
        async def _noop(*args, **kwargs):
            return {"text": "", "usage": {}}

        run = TeamRun(
            team_spec,
            complete=_noop,
            tool=_noop,
            emit=lambda ev: None,
            guard=lambda: None,
            messages=[{"role": "user", "content": "Build feature"}],
        )
        snap = run.snapshot()
        lead_rows = [m for m in snap["members"] if m["id"] == "m-lead"]
        self.assertEqual(len(lead_rows), 1)
        self.assertTrue(lead_rows[0].get("is_lead"))

    def test_modules_validate_recognizes_v4_bridge_methods_and_icons(self) -> None:
        from app import module_icons, modules_validate

        methods = modules_validate.bridge_methods()
        for expected in ("music.call", "learn.call", "database.call", "integration.list", "appbar.set"):
            self.assertIn(expected, methods)

        self.assertEqual(module_icons.BUILTIN_GLYPH.get("memudb"), "database")
        icons_js = (ROOT / "app" / "pgstatic" / "module-icons.js").read_text(encoding="utf-8")
        for icon_name in ('"database"', '"shield"', '"code"'):
            self.assertIn(icon_name, icons_js)

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "db-helper"
            pkg.mkdir()
            (pkg / "manifest.json").write_text(
                json.dumps({
                    "id": "db-helper",
                    "title": "DB Helper",
                    "title_en": "DB Helper",
                    "version": "1.0.0",
                    "permissions": ["database"],
                }),
                encoding="utf-8",
            )
            (pkg / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
            (pkg / "main.js").write_text("Memu.database.call('connection.list', {});", encoding="utf-8")
            report = modules_validate.validate_path(pkg, check_syntax=False)
            problem_texts = " ".join(p.get("en", "") for p in report.get("problems", []))
            self.assertNotIn("database.call", problem_texts)
            self.assertTrue(report.get("ok"), report.get("problems"))

    def test_memudb_nan_cell_session_eviction_and_explain_dedup(self) -> None:
        from app.memudb import Workspace, _cell

        self.assertEqual(_cell(float("nan")), "nan")
        self.assertEqual(_cell(float("inf")), "inf")

        owner = ("u1", "memudb")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)")
            conn.execute("INSERT INTO items VALUES (1, 'one')")
            conn.close()

            ws = Workspace()
            try:
                info = ws.open(str(db_path), owner)
                cid = info["id"]
                plan = ws.query(cid, owner, "EXPLAIN QUERY PLAN SELECT * FROM items", explain=True)
                self.assertTrue(plan["rows"])
            finally:
                ws.close_all()

    def test_memudb_duckdb_comment_ddl_guard_and_schema_changes_preview(self) -> None:
        import duckdb
        from app.memudb import DBError
        from app.memudb_duckdb import DuckWorkspace

        owner = ("u1", "memudb")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "analytics.duckdb"
            conn = duckdb.connect(str(db_path))
            conn.execute("CREATE TABLE seed(id INTEGER)")
            conn.close()
            ws = DuckWorkspace()
            try:
                info = ws.open(str(db_path), owner)
                cid = info["id"]
                with self.assertRaises(DBError):
                    ws.query(cid, owner, "-- comment line\nCREATE TABLE forbidden(id INT)")
                ws.configure(cid, owner, False, confirmed=True)
                prev = ws.preview(cid, owner, [{"sql": "CREATE TABLE metrics(id INTEGER, val DOUBLE)"}])
                added_tables = [c["name"] for c in prev["schema_changes"] if c.get("before") is None and c.get("after")]
                self.assertIn("metrics", added_tables)
                ws.discard(cid, owner, prev["preview_id"])
            finally:
                ws.close_all()

    def test_voice_prepare_tts_strips_crlf_and_complex_code_fences(self) -> None:
        from app.voice_runtime import prepare_tts_text

        out = prepare_tts_text("Hello world!\r\n```c# title=\"demo\"\r\nConsole.WriteLine(1);\r\n```\r\nDone.")
        self.assertNotIn("Console.WriteLine", out)
        self.assertIn("Code snippet omitted", out)

    def test_active_section_css_present_across_designs(self) -> None:
        for rel in (
            "app/pgstatic/design.css",
            "app/pgstatic/designs/aurora.css",
            "app/pgstatic/designs/brutal.css",
            "app/pgstatic/designs/glass.css",
            "app/pgstatic/designs/soft.css",
            "app/pgstatic/designs/terminal.css",
        ):
            css = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("active-section", css, f"Missing active-section rule in {rel}")

    def test_video_input_models_classified_as_chat_not_video_generator(self) -> None:
        from app import model_capabilities as mc, model_catalog

        for model_id in (
            "google/gemini-2.5-pro",
            "qwen/qwen2.5-vl-72b-instruct",
            "llava-next-video-34b",
            "custom/video-understanding-chat",
        ):
            with self.subTest(model=model_id):
                raw_entry = {
                    "source": "provider",
                    "row": {
                        "id": model_id,
                        "architecture": {
                            "input_modalities": ["text", "image", "video"],
                            "output_modalities": ["text"],
                        },
                    },
                }
                with patch.object(mc, "raw_row", return_value=raw_entry):
                    desc = mc.describe(model_id)
                self.assertEqual(desc["summary"]["kind"], "chat")
                self.assertTrue(desc["summary"]["video"])
                self.assertEqual(
                    mc.kind_for_row({"id": model_id, "caps": desc["summary"]}),
                    "chat",
                )

        for gen_id in ("wan-ai/wan2.1-t2v-14b", "google/veo-2", "openai/sora"):
            with self.subTest(generator=gen_id):
                self.assertEqual(mc.kind_for_row({"id": gen_id, "caps": {}}), "video")

        payload = {
            "models": [
                {
                    "id": "qwen/qwen2.5-vl-72b-instruct",
                    "architecture": {
                        "input_modalities": ["text", "image", "video"],
                        "output_modalities": ["text"],
                    },
                },
                {
                    "id": "wan-ai/wan2.1-t2v-14b",
                    "architecture": {
                        "input_modalities": ["text"],
                        "output_modalities": ["video"],
                    },
                },
            ]
        }
        with patch.object(model_catalog, "vision_state", return_value={"state": "yes", "src": "provider"}):
            model_catalog.enrich(payload)
        by_id = {r["id"]: r for r in payload["models"]}
        self.assertEqual(by_id["qwen/qwen2.5-vl-72b-instruct"]["kind"], "chat")
        self.assertEqual(by_id["wan-ai/wan2.1-t2v-14b"]["kind"], "video")

    def test_non_chat_models_stay_in_model_list_and_warn_on_select(self) -> None:
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        picker_js = (ROOT / "app" / "pgstatic" / "model-picker.js").read_text(encoding="utf-8")
        self.assertIn("warnIfSpecialized", picker_js)
        self.assertIn("warnIfSpecialized", app_js)

        self.assertIn("return S.models.filter((m) => !hidden.has(m.id));", app_js)

    def test_teams_integration_in_planning_and_flows(self) -> None:
        import asyncio
        from app import flow_engine as fe, schedule_rules as sr, schedule_rules_api as sra, schedule_runtime as srt, teams

        self.assertIn("team", sr.ACTION_KINDS)
        self.assertIn("team", srt.RUNTIME_KINDS)
        self.assertIn("team", fe.STEP_TYPES)

        with tempfile.TemporaryDirectory() as tmp:
            teams_file = Path(tmp) / "teams.json"
            runs_file = Path(tmp) / "team-runs.json"
            with patch.object(teams, "_path", return_value=teams_file), patch.object(teams, "_runs_path", return_value=runs_file):
                saved_team = teams.save(
                    {
                        "name": "Morning Analysts",
                        "manager_model": "mgr-1",
                        "members": [{"name": "Alice", "role": "Researcher", "model": "worker-1"}],
                    },
                    profile_id="default",
                )
                team_id = saved_team["id"]

                cleaned = sra._clean_task(
                    {
                        "name": "Team briefing",
                        "kind": "team",
                        "team_id": team_id,
                        "prompt": "Prepare morning digest",
                        "rule": {"when": [{"type": "daily", "times": ["09:00"]}]},
                    }
                )
                self.assertEqual(cleaned["kind"], "team")
                self.assertEqual(cleaned["team_id"], team_id)
                self.assertIsNone(sra._validate_action(cleaned, "ru"))

                self.assertEqual(sr.warnings(cleaned, [], "ru"), [])
                missing_warn = sr.warnings({**cleaned, "team_id": "missing"}, [], "ru")
                self.assertTrue(any("удалена" in w for w in missing_warn))

                steps = fe.task_to_flow(cleaned)
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0]["type"], "team")
                self.assertEqual(steps[0]["team_id"], team_id)
                self.assertEqual(steps[0]["prompt"], "Prepare morning digest")
                self.assertEqual(fe.validate_flow(steps, "ru"), [])

                class FakeTeamRun:
                    def __init__(self, team_obj, **kwargs):
                        self.team = team_obj
                        self.kwargs = kwargs
                        self.id = "tr-test"

                    async def run(self):
                        return {"text": "Team briefing ready: 3 key highlights.", "run": self.snapshot()}

                    def snapshot(self):
                        return {"id": "tr-test", "status": "completed"}

                with patch("app.team_runtime.TeamRun", FakeTeamRun):
                    ok, answer, extra = asyncio.run(srt._team(cleaned))
                    self.assertTrue(ok)
                    self.assertIn("Team briefing ready", answer)
                    self.assertEqual(extra.get("team_id"), team_id)
                    self.assertEqual(extra.get("team_name"), "Morning Analysts")

if __name__ == "__main__":
    unittest.main()
