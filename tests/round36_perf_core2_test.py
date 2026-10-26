"""Round 36 (perf-core-2): light chat list + deltas, cheap polls, file index unloading, Quick Access warm policy.

No network, no Windows needed."""
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r36-perf-core2-"))
os.environ["MEMUAI_FSINDEX_NOSTART"] = "1"
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:9")

from app import chats_delta, fsindex, poll_etag, quick_warm  # noqa: E402

STATIC = ROOT / "app" / "pgstatic"

def chat(i, n=3, **extra):
    msgs = [{"role": "user" if k % 2 == 0 else "assistant", "content": f"message {k} of chat {i}"} for k in range(n)]
    return {"id": f"chat-{i}", "title": f"Chat {i}", "updated": 1000 + i, "mode": "chat", "messages": msgs, **extra}

class ChatsDeltaUnitTest(unittest.TestCase):
    def test_meta_has_no_history_and_carries_counters_preview_and_sig(self):
        row = chat(1, n=5, pinned=True, folder_id="f1")
        meta = chats_delta.chat_meta(row)
        self.assertNotIn("messages", meta)
        self.assertEqual((meta["id"], meta["count"], meta["pinned"], meta["folder_id"]), ("chat-1", 5, True, "f1"))
        self.assertEqual(meta["preview"], "message 4 of chat 1")
        self.assertEqual(meta["sig"], chats_delta.chat_sig(row))

    def test_sig_changes_with_any_byte_and_is_stable(self):
        a, b = chat(1), chat(1)
        self.assertEqual(chats_delta.chat_sig(a), chats_delta.chat_sig(b))
        b["messages"][1]["content"] += "!"
        self.assertNotEqual(chats_delta.chat_sig(a), chats_delta.chat_sig(b))

    def test_ids_parsing_and_cache_per_revision(self):
        self.assertEqual(chats_delta.parse_ids("a, b,,a", "c"), ["c", "a", "b"])
        rows = [chat(i) for i in range(3)]
        self.assertIsNone(chats_delta.cached_meta("unit", "r1"))
        meta = chats_delta.meta_for("unit", "r1", rows)
        self.assertIs(chats_delta.cached_meta("unit", "r1"), meta)
        chats_delta.meta_for("unit", "r2", rows)
        self.assertIsNone(chats_delta.cached_meta("unit", "r1"), "only the newest revision is kept")

class ChatsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_full_list_meta_ids_and_delta(self):
        c = self.client
        rows = [chat(i, n=4 + i) for i in range(6)]
        saved = c.post("/api/pg/chats", json={"chats": rows, "deleted": [], "partial": True}).json()
        self.assertTrue(saved["ok"])
        self.assertEqual(set(saved["sigs"]), {r["id"] for r in rows})
        full = c.get("/api/pg/chats").json()
        self.assertEqual(len(full["chats"]), 6)
        self.assertEqual(full["sigs"], saved["sigs"], "the save already told the window the stored fingerprints")
        meta = c.get("/api/pg/chats?view=meta").json()
        self.assertEqual(meta["rev"], full["rev"])
        self.assertTrue(all("messages" not in m for m in meta["meta"]))
        self.assertEqual({m["id"]: m["sig"] for m in meta["meta"]}, full["sigs"])
        self.assertEqual(c.get(f"/api/pg/chats?view=meta&rev={full['rev']}").json(), {"unchanged": True, "rev": full["rev"]})
        one = c.get("/api/pg/chats?id=chat-2").json()
        self.assertEqual([r["id"] for r in one["chats"]], ["chat-2"])
        self.assertEqual(one["chats"][0]["messages"], rows[2]["messages"])

        changed = dict(rows[3], messages=rows[3]["messages"] + [{"role": "user", "content": "from telegram"}], updated=99999)
        time.sleep(0.01)
        c.post("/api/pg/chats", json={"chats": [changed], "deleted": ["chat-5"], "partial": True})
        delta = c.get(f"/api/pg/chats?view=meta&rev={full['rev']}").json()
        ids = [m["id"] for m in delta["meta"]]
        self.assertNotIn("chat-5", ids)
        diff = [m["id"] for m in delta["meta"] if full["sigs"].get(m["id"]) != m["sig"]]
        self.assertEqual(diff, ["chat-3"], "only the changed chat is downloaded")
        got = c.get("/api/pg/chats?ids=" + ",".join(diff)).json()
        self.assertEqual(got["chats"][0]["messages"][-1]["content"], "from telegram")
        self.assertEqual(got["sigs"]["chat-3"], [m for m in delta["meta"] if m["id"] == "chat-3"][0]["sig"])

    def test_hub_answers_not_modified(self):
        first = self.client.get("/api/pg/hub")
        self.assertEqual(first.status_code, 200)
        tag = first.json()["etag"]
        self.assertEqual(first.headers["etag"], f'"{tag}"')
        self.assertEqual(self.client.get(f"/api/pg/hub?since={tag}").json(), {"unchanged": True, "etag": tag})
        self.assertEqual(self.client.get("/api/pg/hub", headers={"If-None-Match": f'"{tag}"'}).status_code, 304)

class PollEtagTest(unittest.TestCase):
    def test_answer_variants(self):
        data = {"models": [1, 2], "memory": {"a": 1}}
        tag = poll_etag.etag_of(data)
        self.assertEqual(tag, poll_etag.etag_of({"memory": {"a": 1}, "models": [1, 2]}))
        self.assertEqual(poll_etag.answer(data).status_code, 200)
        self.assertEqual(poll_etag.answer(data, if_none_match=f'W/"{tag}"').status_code, 304)
        self.assertIn(b'"unchanged":true', poll_etag.answer(data, since=tag).body)
        self.assertEqual(poll_etag.answer({"error": "x"}, status=502).status_code, 502)

class FsindexUnloadTest(unittest.TestCase):
    def test_idle_index_is_unloaded_and_search_touches(self):
        svc = fsindex.Service()
        svc.index = fsindex.Index(fsindex.Snapshot.build([("C:\\A", [("x.txt", 1, 1.0, False)])], ["C:\\"]))
        svc.state = "ready"
        settings = fsindex.normalize_settings({})
        self.assertEqual(settings["unload_minutes"], 10)
        svc.touch()
        self.assertFalse(svc.unload_if_idle(settings), "used just now")
        self.assertFalse(svc.unload_if_idle(fsindex.normalize_settings({"unload_minutes": 0}), now=time.time() + 10 ** 6))
        self.assertTrue(svc.unload_if_idle(settings, now=time.time() + 601))
        self.assertIsNone(svc.index)
        self.assertEqual(svc.state, "idle")
        self.assertGreater(svc._unloaded_at, 0)
        self.assertFalse(svc.unload_if_idle(settings, now=time.time() + 10 ** 6), "nothing left to unload")

    def test_setting_is_clamped(self):
        self.assertEqual(fsindex.normalize_settings({"unload_minutes": "-5"})["unload_minutes"], 0)
        self.assertEqual(fsindex.normalize_settings({"unload_minutes": 99999})["unload_minutes"], 24 * 60)
        self.assertEqual(fsindex.normalize_settings({"unload_minutes": "x"})["unload_minutes"], 10)

class QuickWarmTest(unittest.TestCase):
    def test_policy(self):
        self.assertEqual(quick_warm.policy({}), "auto")
        self.assertEqual(quick_warm.policy({"quick_ask_warm": "bogus"}), "auto")
        self.assertFalse(quick_warm.warm_at_start({"quick_ask_warm": "never"}))
        self.assertTrue(quick_warm.warm_at_start({}))
        kw = dict(has_window=True, visible=False, pinned=False)
        self.assertFalse(quick_warm.should_release({"quick_ask_warm": "always"}, hidden_for=10 ** 6, **kw))
        self.assertFalse(quick_warm.should_release({}, hidden_for=29 * 60, **kw))
        self.assertTrue(quick_warm.should_release({}, hidden_for=30 * 60, **kw))
        self.assertTrue(quick_warm.should_release({"quick_ask_warm": "never"}, hidden_for=120, **kw))
        self.assertFalse(quick_warm.should_release({}, hidden_for=10 ** 6, has_window=True, visible=False, pinned=True))
        self.assertFalse(quick_warm.should_release({}, hidden_for=10 ** 6, has_window=True, visible=True, pinned=False))
        self.assertFalse(quick_warm.should_release({}, hidden_for=10 ** 6, busy=True, **kw))

    def test_preference_is_normalised(self):
        from app import quick_ask_core
        self.assertEqual(quick_ask_core.PREF_DEFAULTS["quick_ask_warm"], "auto")

    def test_manager_wiring(self):
        src = (ROOT / "app" / "quick_ask.py").read_text(encoding="utf-8")
        self.assertIn("quick_warm.warm_at_start(prefs)", src)
        self.assertIn("def release_window", src)
        self.assertIn("self._quitting or self._releasing", src)

class FrontendWiringTest(unittest.TestCase):
    def test_app_js_syncs_by_delta(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        body = js[js.index("async function syncRemoteChats"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("/api/pg/chats?view=meta", body)
        self.assertIn("/api/pg/chats?ids=", body)
        self.assertIn("mergeRemoteChats(remote, profileId, true, allIds)", body)
        self.assertIn("rememberChatSigs(saved.sigs)", js)
        self.assertIn("rememberChatSigs(stored.sigs)", js)

    def test_settings_rows(self):
        fs = (STATIC / "quick" / "plugins" / "fsearch-settings.js").read_text(encoding="utf-8")
        self.assertIn('"fss-unload"', fs)
        self.assertIn("unload_minutes", fs)
        qs = (STATIC / "quick-settings.js").read_text(encoding="utf-8")
        self.assertRegex(qs, re.escape('save({ quick_ask_warm: e.target.value })'))

if __name__ == "__main__":
    unittest.main()
