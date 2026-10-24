"""Round 29 (r29-schedsend-export): scheduled messages («Отложенная отправка»).

* create validates (empty text, too soon, a year ahead, Work chat without a project), stores per profile;
* claim is exclusive: two senders never both own one item; a late item follows «schedsend_missed»
  (send / ask / skip); «send now» bypasses the policy;
* a repeating message continues as a fresh row after it was sent; a failed «when online» message retries;
* a stale «sending» claim becomes failed (never silently re-sent);
* preferences normalization of the schedsend_* keys;
* routes /api/schedsend/* through the real MemuAI app;
* the runner uses only tgapp_chat helpers that exist with the signatures it calls;
* the frontend is wired (index.html tags after app.js, settings hooks) and English strings have no Cyrillic;
* MemuAI.spec / MemuAIProxy.spec / build.py ship the modules.
"""
from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-schedsend-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
CYR = re.compile(r"[А-Яа-яЁё]")
HOUR = 3600 * 1000


class QueueTest(unittest.TestCase):
    def setUp(self) -> None:
        from app import scheduled_messages as sm
        self.sm = sm
        self.profile = None
        path = sm.path_for(self.profile)
        if path.exists():
            path.unlink()
        self.now = sm.now_ms()

    def make(self, **extra):
        data = {"chat_id": "chat-1", "text": "hello", "trigger": "time", "due_at": self.now + HOUR}
        data.update(extra)
        return self.sm.create(data, self.profile, now=self.now)

    def test_create_validation(self) -> None:
        sm = self.sm
        item = self.make()
        self.assertEqual(item["status"], "scheduled")
        self.assertEqual([row["id"] for row in sm.load()], [item["id"]])
        with self.assertRaises(sm.ScheduleError):
            self.make(text="   ")
        with self.assertRaises(sm.ScheduleError):
            self.make(due_at=self.now + 1000)
        with self.assertRaises(sm.ScheduleError):
            self.make(due_at=self.now + 400 * 24 * HOUR)
        with self.assertRaises(sm.ScheduleError):
            self.make(mode="work")
        online = self.make(trigger="online", repeat="daily", due_at=0)
        self.assertEqual(online["repeat"], "none")

    def test_claim_is_exclusive(self) -> None:
        sm = self.sm
        item = self.make()
        at = self.now + HOUR + 1000
        first = sm.claim(item["id"], "desktop", self.profile, now=at)
        second = sm.claim(item["id"], "proxy", self.profile, now=at)
        self.assertEqual(first["action"], "send")
        self.assertEqual(second["action"], "none")
        done = sm.complete(item["id"], True, self.profile, now=at + 5000)
        self.assertEqual(done["status"], "sent")
        self.assertEqual(sm.claim(item["id"], "proxy", self.profile, now=at + 6000)["action"], "none")

    def test_missed_policy(self) -> None:
        sm = self.sm
        late_at = self.now + HOUR + sm.LATE_GRACE_MS + 60_000
        for policy, action, status in (("ask", "ask", "missed"), ("skip", "skip", "skipped"), ("send", "send", "sending")):
            item = self.make()
            result = sm.claim(item["id"], "proxy", self.profile, policy=policy, now=late_at)
            self.assertEqual(result["action"], action, policy)
            row = next(r for r in sm.load() if r["id"] == item["id"])
            self.assertEqual(row["status"], status, policy)
        # «send now» bypasses the policy
        item = self.make()
        sm.send_now(item["id"], self.profile, now=self.now + 1000)
        result = sm.claim(item["id"], "proxy", self.profile, policy="skip", now=late_at)
        self.assertEqual(result["action"], "send")

    def test_repeat_and_retry(self) -> None:
        sm = self.sm
        item = self.make(repeat="daily")
        at = self.now + HOUR + 1000
        sm.claim(item["id"], "desktop", self.profile, now=at)
        sm.complete(item["id"], True, self.profile, now=at)
        rows = [r for r in sm.load() if r["status"] == "scheduled"]
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["due_at"], at)
        self.assertEqual(rows[0]["repeat"], "daily")
        online = self.make(trigger="online", due_at=0)
        sm.claim(online["id"], "proxy", self.profile, now=self.now + 1000)
        back = sm.complete(online["id"], False, self.profile, error="offline", retry=True, now=self.now + 2000)
        self.assertEqual(back["status"], "scheduled")
        self.assertGreater(back["next_attempt"], self.now + 2000)

    def test_stale_claim_fails(self) -> None:
        sm = self.sm
        item = self.make()
        at = self.now + HOUR + 1000
        sm.claim(item["id"], "proxy", self.profile, now=at)
        self.assertEqual(sm.release_stale(self.profile, now=at + sm.SENDING_STALE_MS + 1000), 1)
        row = next(r for r in sm.load() if r["id"] == item["id"])
        self.assertEqual(row["status"], "failed")

    def test_prefs_normalization(self) -> None:
        sm = self.sm
        out = dict(sm.PREF_DEFAULTS)
        sm.normalize_prefs(out, {"schedsend_missed": "WHATEVER", "schedsend_background": "false", "schedsend_notify": 0})
        self.assertEqual(out["schedsend_missed"], "send")
        self.assertFalse(out["schedsend_background"])
        self.assertFalse(out["schedsend_notify"])
        sm.normalize_prefs(out, {"schedsend_missed": "ask"})
        self.assertEqual(out["schedsend_missed"], "ask")


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_create_list_update_delete(self) -> None:
        from app import scheduled_messages as sm
        due = sm.now_ms() + 2 * HOUR
        response = self.client.post("/api/schedsend/create", json={"item": {"chat_id": "route-chat", "text": "hi", "due_at": due}})
        self.assertEqual(response.status_code, 200, response.text)
        item_id = response.json()["item"]["id"]
        listed = self.client.get("/api/schedsend/list?chat_id=route-chat").json()
        self.assertIn(item_id, [row["id"] for row in listed["items"]])
        self.assertGreaterEqual(listed["summary"]["active"], 1)
        response = self.client.post("/api/schedsend/update", json={"id": item_id, "patch": {"text": "changed"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["text"], "changed")
        response = self.client.post("/api/schedsend/delete", json={"id": item_id})
        self.assertTrue(response.json()["ok"])
        response = self.client.post("/api/schedsend/create", json={"item": {"chat_id": "route-chat", "text": "", "due_at": due}, "lang": "en"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CYR.search(response.json().get("error", "")))


class RunnerTest(unittest.TestCase):
    def test_tgapp_chat_contract(self) -> None:
        from app import tgapp_chat as tg
        for name in ("jobs", "profile_by_id", "find_meme", "chat_models", "pick_model", "MEME_CAPS", "meme_caps",
                     "prepare_attachments", "now_ms", "profile_memory", "profile_reference_context",
                     "build_system_prompt", "title_prompt_for", "history_messages", "Job", "_generate",
                     "model_provider", "is_image_model"):
            self.assertTrue(hasattr(tg, name), name)
        self.assertIn("memory", tg.MEME_CAPS)
        params = list(inspect.signature(tg._generate).parameters)
        self.assertEqual(params[:12], ["job", "chat", "history", "user_row", "messages", "model", "web_search",
                                       "thinking", "profile", "lang", "temp", "meme_id"])
        system = inspect.signature(tg.build_system_prompt).parameters
        for key in ("model", "prefs", "lang", "temp", "meme", "has_profile_context", "title_prompt"):
            self.assertIn(key, system)
        self.assertTrue(hasattr(tg.jobs, "active_for_chat") and hasattr(tg.jobs, "add"))

    def test_runner_imports(self) -> None:
        from app import scheduled_messages_runner as runner
        self.assertTrue(inspect.iscoroutinefunction(runner.runner_loop))
        self.assertTrue(inspect.iscoroutinefunction(runner.tick))
        block = runner.delivery_block({"due_at": 0, "trigger": "time", "late": False}, "en")
        self.assertFalse(CYR.search(block))


class FrontendTest(unittest.TestCase):
    def test_wiring(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("schedsend.js", "schedsend.css"):
            self.assertTrue((STATIC / name).is_file())
            self.assertIn(f"/{name}?v=", index)
        self.assertLess(index.index("/app.js?v="), index.index("/schedsend.js?v="))
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuSchedSend.settingsHtml", app)
        self.assertIn("MemuSchedSend.bindSettings", app)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("scheduled_messages_api.register(app)", api)
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("scheduled_messages_runner", main)

    def test_english_strings(self) -> None:
        source = (STATIC / "schedsend.js").read_text(encoding="utf-8")
        pairs = re.findall(r"\bt\(\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*\"((?:[^\"\\]|\\.)*)\"", source)
        self.assertGreater(len(pairs), 30)
        for ru, en in pairs:
            self.assertFalse(CYR.search(en), en)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_check(self) -> None:
        result = subprocess.run(["node", "--check", str(STATIC / "schedsend.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docs(self) -> None:
        for lang in ("ru", "en"):
            self.assertTrue((ROOT / "app" / "docs" / lang / "scheduled-messages.md").is_file())
        self.assertFalse(CYR.search((ROOT / "app" / "docs" / "en" / "scheduled-messages.md").read_text(encoding="utf-8")))


class BuildTest(unittest.TestCase):
    def test_spec_and_build(self) -> None:
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        proxy = (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for module in ("app.scheduled_messages", "app.scheduled_messages_api", "app.scheduled_messages_runner"):
            self.assertIn(f"'{module}'", spec)
            self.assertIn(f'"{module}"', build)
        for module in ("app.scheduled_messages", "app.scheduled_messages_runner"):
            self.assertIn(f"'{module}'", proxy)


if __name__ == "__main__":
    unittest.main()
