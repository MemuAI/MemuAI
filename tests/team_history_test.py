"""Team page: identity fields, starters, presets, run history and statistics endpoints."""
from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import pgdata, teams, teams_api
from app.team_runtime import TeamRun

ROOT = Path(__file__).resolve().parents[1]


def team(**extra):
    return {"id": "t1", "name": "Совет", "manager_model": "m", "members": [
        {"id": "a", "name": "A", "model": "m", "role": "Lead", "is_lead": True},
        {"id": "b", "name": "B", "model": "m", "role": "Critic"}], **extra}


def snapshot(run_id, team_id="t1", status="completed", started=0, finished=0, calls=5, tokens=1000, source="chat"):
    return {"id": run_id, "team_id": team_id, "team_name": "Совет", "status": status, "mode": "chat", "source": source,
            "started": started, "finished": finished, "calls": calls, "max_calls": 100, "tool_calls": 1,
            "members": [{"id": "manager"}, {"id": "a"}, {"id": "b"}],
            "tasks": [{"id": "x", "title": "Первое задание", "status": "completed"}, {"id": "y", "title": "Второе", "status": "error"}],
            "usage": {"prompt_tokens": tokens // 2, "completion_tokens": tokens // 2}, "events": [{"type": "phase"}]}


class Identity(unittest.TestCase):
    def test_icon_color_pin_and_starters_are_normalized(self):
        row = teams.normalize(team(icon="vote", color="violet", pinned=True,
                                   starters=["  Первая   задача ", "", "Первая задача", "x" * 500] + [f"s{i}" for i in range(10)]))
        self.assertEqual((row["icon"], row["color"], row["pinned"]), ("vote", "violet", True))
        self.assertEqual(row["starters"][0], "Первая задача")
        self.assertEqual(len(row["starters"]), teams.MAX_STARTERS)
        self.assertTrue(all(len(s) <= teams.STARTER_CHARS for s in row["starters"]))

    def test_unknown_identity_values_fall_back(self):
        row = teams.normalize(team(icon="<svg>", color="red", pinned="yes", starters="nope"))
        self.assertEqual((row["icon"], row["color"], row["pinned"], row["starters"]), ("users", "", False, []))
        legacy = teams.normalize(team())
        self.assertEqual((legacy["icon"], legacy["color"], legacy["pinned"], legacy["starters"]), ("users", "", False, []))

    def test_presets_are_complete_and_bilingual(self):
        ids = [p["id"] for p in teams.PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 10)
        for required in ("feature", "research", "creative"):
            self.assertIn(required, ids)
        cats = {c["id"] for c in teams.preset_categories()}
        for preset in teams.PRESETS:
            with self.subTest(preset=preset["id"]):
                self.assertIn(preset["category"], cats)
                self.assertIn(preset["icon"], teams.TEAM_ICONS)
                self.assertTrue(preset["name_en"] and preset["description_en"])
                self.assertTrue(1 <= len(preset["members"]) <= teams.MAX_MEMBERS)
                self.assertEqual(sum(1 for m in preset["members"] if m.get("is_lead")), 1)
                self.assertEqual(len(preset["starters"]), len(preset["starters_en"]))
                for member in preset["members"]:
                    self.assertTrue(member["role"] and member["role_en"] and member["name_en"])
                # A preset turned into a team (with a model) must pass strict validation.
                teams.normalize({"name": preset["name"], "manager_model": "m", "icon": preset["icon"],
                                 "starters": preset["starters"],
                                 "members": [{"name": m["name"], "role": m["role"], "model": "m", "is_lead": m.get("is_lead") is True,
                                              "instructions": m.get("instructions", "")} for m in preset["members"]]})

    def test_preset_icons_exist_in_the_icon_set(self):
        app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        block = app_js[app_js.index("const ICONS"):]
        block = block[:block.index("};")]
        names = set(re.findall(r"\n\s*([A-Za-z0-9_]+)\s*:", block))
        missing = [icon for icon in teams.TEAM_ICONS if icon not in names]
        self.assertEqual(missing, [])


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / "team-runs.json"
        self.patch = patch.object(teams, "_runs_path", return_value=self.runs)
        self.patch.start()
        now = int(time.time() * 1000)
        rows = [snapshot("r1", started=now - 5000, finished=now - 1000, calls=3, tokens=400),
                snapshot("r2", status="error", started=now - 900000, finished=now - 800000, calls=7, tokens=0, source="schedule"),
                snapshot("r3", team_id="t2", started=now - 100, finished=0)]
        rows[1]["usage"] = {"estimated_prompt_tokens": 90, "estimated_completion_tokens": 10, "estimated_calls": 2}
        self.runs.write_text(json.dumps(rows), encoding="utf-8")

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_summary_has_no_events_or_reports(self):
        rows = teams.run_history("p", "t1")
        self.assertEqual([r["id"] for r in rows], ["r1", "r2"])
        first = rows[0]
        self.assertNotIn("events", first)
        self.assertEqual((first["duration_ms"], first["tokens"], first["tasks"], first["tasks_done"], first["tasks_failed"]), (4000, 400, 2, 1, 1))
        self.assertEqual(first["titles"][0], "Первое задание")
        self.assertEqual(rows[1]["tokens"], 100)
        self.assertTrue(rows[1]["estimated"])
        self.assertEqual(rows[1]["source"], "schedule")

    def test_stats_per_team(self):
        stats = teams.run_stats("p")
        self.assertEqual(stats["t1"]["runs"], 2)
        self.assertEqual((stats["t1"]["completed"], stats["t1"]["failed"]), (1, 1))
        self.assertEqual(stats["t1"]["calls"], 10)
        self.assertEqual(stats["t1"]["last_status"], "completed")
        self.assertEqual(stats["t1"]["avg_ms"], (4000 + 100000) // 2)
        self.assertEqual(stats["t2"]["avg_ms"], 0)

    def test_delete_one_run_and_a_whole_team(self):
        self.assertEqual(teams.delete_run("r1", "p"), 1)
        self.assertEqual([r["id"] for r in teams.run_history("p")], ["r3", "r2"])
        self.assertEqual(teams.delete_run("", "p", team_id="t1"), 1)
        self.assertEqual([r["id"] for r in teams.run_history("p")], ["r3"])
        self.assertEqual(teams.delete_run("missing", "p"), 0)

    def test_snapshot_records_the_source(self):
        run = TeamRun(teams.normalize(team()), complete=None, tool=None, emit=lambda e: None, guard=lambda: None,
                      messages=[], source="schedule")
        self.assertEqual(run.snapshot()["source"], "schedule")
        self.assertEqual(TeamRun(teams.normalize(team()), complete=None, tool=None, emit=lambda e: None,
                                 guard=lambda: None, messages=[], source="bogus").snapshot()["source"], "chat")


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [patch.object(pgdata, "MEMU_DIR", base), patch.object(pgdata, "load_profiles", return_value={"active_id": "default"})]
        for p in self.patches:
            p.start()
        app = FastAPI()
        teams_api.register(app)
        self.client = TestClient(app)
        teams.save(team(icon="chart", starters=["Быстрая задача"]), "default")
        now = int(time.time() * 1000)
        teams_api._save_run("default", snapshot("r1", started=now - 2000, finished=now))
        teams_api._save_run("default", snapshot("r2", team_id="other", started=now - 1000, finished=now))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_list_includes_stats_categories_and_limits(self):
        data = self.client.get("/api/pg/teams").json()
        self.assertEqual(data["teams"][0]["icon"], "chart")
        self.assertEqual(data["teams"][0]["starters"], ["Быстрая задача"])
        self.assertEqual(data["stats"]["t1"]["runs"], 1)
        self.assertTrue(data["categories"])
        self.assertIn("violet", data["limits"]["colors"])

    def test_runs_endpoint_filters_and_clear(self):
        rows = self.client.get("/api/pg/teams/runs", params={"team_id": "t1"}).json()["runs"]
        self.assertEqual([r["id"] for r in rows], ["r1"])
        self.assertEqual(len(self.client.get("/api/pg/teams/runs").json()["runs"]), 2)
        self.assertEqual(self.client.post("/api/pg/teams/runs-clear", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/pg/teams/runs-clear", json={"run_id": "r1"}).json()["removed"], 1)
        self.assertEqual(self.client.get("/api/pg/teams/runs/r1").status_code, 404)
        self.assertEqual(self.client.post("/api/pg/teams/runs-clear", json={"team_id": "other"}).json()["removed"], 1)
        self.assertEqual(self.client.get("/api/pg/teams/runs").json()["runs"], [])

    def test_profile_mismatch_is_rejected(self):
        self.assertEqual(self.client.get("/api/pg/teams/runs", params={"profile_id": "else"}).status_code, 409)
        self.assertEqual(self.client.post("/api/pg/teams/runs-clear", json={"profile_id": "else", "run_id": "r1"}).status_code, 409)


class Frontend(unittest.TestCase):
    def test_team_page_uses_the_new_endpoints_and_keeps_contract_ids(self):
        js = (ROOT / "app/pgstatic/teams.js").read_text(encoding="utf-8")
        for marker in ("/api/pg/teams/runs?", "/api/pg/teams/runs-clear", "data-tm-starter", "data-tm-cat", "#tm-import",
                       "memuai-team", "data-tm-icon", "data-tm-color", "#tm-same-model", "data-tm-move", "tm-history",
                       "#tm-start", "#tm-edit", "#tm-sidebar", "data-tm-preset", "#tm-save", "#tm-add-member"):
            self.assertIn(marker.lstrip("#"), js, marker)
        self.assertNotIn("tm-avatar", js)
        css = (ROOT / "app/pgstatic/teams.css").read_text(encoding="utf-8")
        for marker in (".tm-stats", ".tm-history", ".tm-starter", ".tm-cat", ".tm-icon-pick", ".tm-color-pick", ".tm-c-violet"):
            self.assertIn(marker, css, marker)


if __name__ == "__main__":
    unittest.main()
