"""Рабочие области (стандартная / агентная): изоляция данных, память, журнал и контекст компаньона."""
import asyncio
import json
import os
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import memory_v2, pgdata, space_context, spaces


class _Data(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = patch.object(pgdata, "MEMU_DIR", self.root)
        self._patch.start()
        spaces._CACHE.clear()

    def tearDown(self):
        space_context.close_all()
        self._patch.stop()
        self._tmp.cleanup()


class SpacesStorageTest(_Data):
    def test_default_state_is_standard_and_legacy_paths(self):
        state = spaces.public_state()
        self.assertEqual(state["active_id"], "standard")
        self.assertEqual([i["id"] for i in state["items"]], ["standard"])
        self.assertEqual(pgdata._profile_data_name("chats.json"), "chats.json")
        self.assertEqual(pgdata._profile_data_name("quick_chats.json"), "quick_chats.json")

    def test_agent_space_scopes_chats_workspaces_and_teams(self):
        pgdata.save_chats([{"id": "std-1", "title": "Standard", "messages": [], "updated": 1}])
        item = spaces.create_space({"type": "agent", "name": "Мира", "companion": {"name": "Мира", "style": "warm"}})
        self.assertTrue(item["id"].startswith("s-"))
        self.assertEqual(item["companion"]["name"], "Мира")
        self.assertEqual(item["global_chat_id"], f"g-{item['id']}")
        spaces.activate_space(item["id"])
        for name in ("chats.json", "workspaces.json", "teams.json", "team-runs.json", "schedules.json"):
            self.assertEqual(Path(pgdata._profile_data_name(name)), Path("spaces") / item["id"] / name)
        self.assertEqual(pgdata._profile_data_name("quick_chats.json"), "quick_chats.json")
        self.assertEqual(pgdata.load_chats(), [])
        pgdata.save_chats([{"id": "agent-1", "title": "Agent", "messages": [], "updated": 2}])
        self.assertEqual([c["id"] for c in pgdata.load_chats()], ["agent-1"])
        spaces.activate_space("standard")
        self.assertEqual([c["id"] for c in pgdata.load_chats()], ["std-1"])

    def test_memory_is_separate_per_space(self):
        memory_v2.add_manual("Пользователь любит чай")
        item = spaces.create_space({"type": "agent", "activate": True})
        self.assertEqual(memory_v2.active_profile_id(), f"default--{item['id']}")
        texts = [i["text"] for i in memory_v2.load()["items"]]
        self.assertNotIn("Пользователь любит чай", texts)
        memory_v2.add_manual("Компаньона зовут Мира")
        live = json.loads((self.root / "memory.json").read_text(encoding="utf-8"))
        self.assertEqual(live["profile_id"], f"default--{item['id']}")
        spaces.activate_space("standard")
        texts = [i["text"] for i in memory_v2.load()["items"]]
        self.assertIn("Пользователь любит чай", texts)
        self.assertNotIn("Компаньона зовут Мира", texts)
        # an explicit plain profile id follows the profile's active space
        self.assertIn("Пользователь любит чай", [i["text"] for i in memory_v2.load("default")["items"]])
        spaces.activate_space(item["id"])
        self.assertIn("Компаньона зовут Мира", [i["text"] for i in memory_v2.load("default")["items"]])

    def test_delete_space_returns_to_standard_and_removes_memory(self):
        item = spaces.create_space({"type": "agent", "activate": True})
        memory_v2.add_manual("Временный факт")
        pgdata.save_chats([{"id": "agent-1", "messages": [], "updated": 2}])
        thread = space_context.thread_for()
        thread.ingest(item["global_chat_id"], [{"id": "m1", "role": "user", "seq": 1, "text": "Test"}])
        state = spaces.delete_space(item["id"])
        self.assertEqual(state["active_id"], "standard")
        self.assertEqual([i["id"] for i in state["items"]], ["standard"])
        self.assertFalse((self.root / "spaces" / item["id"]).exists())
        self.assertTrue(any((self.root / "spaces" / ".trash").iterdir()))
        with self.assertRaises(sqlite3.ProgrammingError):
            thread.conn.execute("SELECT 1")
        with self.assertRaises(KeyError):
            space_context.thread_for(item["id"])
        self.assertFalse((self.root / "memory_profiles" / f"default--{item['id']}.json").exists())
        self.assertNotIn("Временный факт", [i["text"] for i in memory_v2.load()["items"]])
        with self.assertRaises(ValueError):
            spaces.delete_space("standard")

    def test_delete_with_locked_files_preserves_workspace(self):
        item = spaces.create_space({"type": "agent", "activate": True})
        folder = spaces.space_dir(item["id"])
        with patch.object(Path, "rename", side_effect=PermissionError("files in use")):
            with self.assertRaises(PermissionError):
                spaces.delete_space(item["id"])
        self.assertEqual(spaces.active_space_id(), item["id"])
        self.assertTrue(folder.is_dir())

    def test_companion_normalisation(self):
        item = spaces.create_space({"type": "agent", "companion": {
            "name": "  Лео  ", "avatar": "javascript:alert(1)", "color": "red", "gender": "x", "style": "evil",
            "persona": "x" * 5000}})
        comp = item["companion"]
        self.assertEqual(comp["name"], "Лео")
        self.assertEqual(comp["avatar"], "preset:aurora")
        self.assertEqual(comp["color"], "#8b5cf6")
        self.assertEqual(comp["gender"], "f")
        self.assertEqual(comp["style"], "mirror")
        self.assertEqual(len(comp["persona"]), 1200)
        updated = spaces.update_space(item["id"], {"companion": {"name": "Лея", "avatar": "preset:sunset"},
                                                   "context": {"economy": "max", "embedding_model": "x-emb"}})
        self.assertEqual(updated["companion"]["name"], "Лея")
        self.assertEqual(updated["companion"]["avatar"], "preset:sunset")
        self.assertEqual(updated["companion"]["persona"], "x" * 1200)
        self.assertEqual(updated["context"]["economy"], "max")
        self.assertEqual(updated["context"]["embedding_model"], "x-emb")

    def test_space_limit(self):
        for _ in range(spaces.MAX_SPACES - 1):
            spaces.create_space({"type": "agent"})
        with self.assertRaises(ValueError):
            spaces.create_space({"type": "standard"})


def _rows(texts, start=0, ts0=1_700_000_000_000):
    out = []
    for i, (role, text) in enumerate(texts):
        out.append({"id": f"m{start + i}", "seq": start + i, "role": role, "text": text, "ts": ts0 + (start + i) * 60_000})
    return out


class ThreadEngineTest(_Data):
    def test_playground_startup_keeps_optional_numpy_unloaded(self):
        env = {**os.environ, "MEMUAI_DATA_DIR": str(self.root), "MEMUAI_ASSISTANT_NO_WARM": "1",
               "MEMUAI_FSINDEX_NOSTART": "1"}
        result = subprocess.run([sys.executable, "-X", "utf8", "-c",
                                 "import sys; from app.playground_api import create_app; "
                                 "create_app(); assert 'numpy' not in sys.modules"],
                                cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ingest_search_morphology_and_updates(self):
        thread = space_context.thread_for()
        thread.ingest("g-1", _rows([
            ("user", "Вчера я купила новые кроссовки для бега"),
            ("assistant", "Классно! Какие кроссовки выбрала?"),
            ("user", "Работаю над проектом MemuAI, пишу парсер на Python"),
            ("assistant", "Парсер — это интересно."),
        ]))
        hits = thread.search("g-1", "кроссовками", limit=3)
        self.assertTrue(hits)
        self.assertIn("кроссовки", hits[0]["snippet"])
        self.assertEqual(thread.search("g-1", "парсеры python", limit=1)[0]["mid"], "m2")
        thread.ingest("g-1", [{"id": "m3", "seq": 3, "role": "assistant", "text": "Парсер на Rust было бы быстрее"}])
        self.assertIn("Rust", thread.messages("g-1")[-1]["text"])
        thread.ingest("g-1", [], deleted=["m3"])
        self.assertEqual(len(thread.messages("g-1")), 3)
        self.assertEqual(thread.search("g-1", "кроссовки", before_seq=0), [])
        hidden = thread.ingest("g-1", [{"id": "m9", "seq": 9, "role": "assistant",
                                         "text": "Ок\n```memuai-mode\n{\"mode\":\"work\"}\n```"}])
        self.assertEqual(hidden["stored"], 1)
        self.assertNotIn("memuai-mode", thread.messages("g-1")[-1]["text"])

    def test_summaries_story_and_context(self):
        thread = space_context.thread_for()
        convo = []
        for i in range(60):
            convo.append(("user", f"Сообщение {i}: обсуждаем поездку в Казань и тему номер {i}"))
            convo.append(("assistant", f"Ответ {i}: понял про Казань и тему {i}"))
        thread.ingest("g-1", _rows(convo))
        calls = []

        async def completion(model, messages, **kw):
            calls.append((model, messages[0]["content"][:40]))
            if "STORY" in messages[0]["content"]:
                return "[2023] Пользователь планирует поездку в Казань.\nОткрыто: купить билеты"
            return "- Обсуждали поездку в Казань\n- Открыто: выбрать отель"

        maint = space_context.Maintainer(completion)
        names = {"user": "Аня", "assistant": "Мира"}
        settings = {"economy": "max", "summary_model": "sum-model"}
        made = asyncio.run(maint.summarize(thread, "g-1", 100, "chat-model", settings, names, max_episodes=10))
        self.assertGreaterEqual(made, 2)
        self.assertTrue(all(model == "sum-model" for model, _ in calls))
        self.assertLessEqual(thread.covered_through("g-1"), 99)
        ctx = space_context.build_context(thread, "g-1", query="Казань билеты", window_start_seq=100,
                                          economy="max", names=names)
        self.assertTrue(ctx["episodes"] or ctx["story"]["text"])
        self.assertIn("выбрать отель", " ".join(ctx["open_threads"]) + " " + ctx["story"]["text"] +
                      " ".join(e["text"] for e in ctx["episodes"]))
        for hit in ctx["recall"]:
            if hit["kind"] == "message":
                self.assertLess(hit["seq"], 100)

    def test_bridge_without_model_and_extractive_fallback(self):
        thread = space_context.thread_for()
        thread.ingest("p-1", _rows([("user", "Первое важное сообщение про отпуск."), ("assistant", "Записала про отпуск.")] * 3))
        ctx = space_context.build_context(thread, "p-1", query="отпуск", window_start_seq=6, economy="balanced",
                                          names={"user": "U", "assistant": "A"})
        self.assertFalse(ctx["complete"])
        self.assertIn("отпуск", ctx["bridge"])
        maint = space_context.Maintainer(None)
        thread.ingest("p-1", _rows([("user", f"текст {i}") for i in range(10)], start=6))
        made = asyncio.run(maint.summarize(thread, "p-1", 16, "", {"economy": "balanced"}, {"user": "U", "assistant": "A"}))
        self.assertGreaterEqual(made, 1)
        self.assertTrue(thread.episodes("p-1")[0]["text"])
        self.assertLessEqual(thread.covered_through("p-1"), 15)

    def test_vectors_hybrid_search(self):
        thread = space_context.thread_for()
        thread.ingest("g-1", _rows([("user", "кот спит на диване"), ("user", "собака гуляет во дворе"),
                                    ("user", "квартальный отчёт по продажам")]))

        def fake_vec(text):
            text = text.lower()
            return [1.0 if "кот" in text or "кош" in text else 0.0, 1.0 if "отчёт" in text or "выручк" in text else 0.0, 0.1]

        async def embed(model, texts):
            return [fake_vec(t) for t in texts]

        maint = space_context.Maintainer(None, embed)
        stored = asyncio.run(maint.embed_missing(thread, "g-1", "emb"))
        self.assertEqual(stored, 3)
        hits = thread.search("g-1", "выручка", limit=1, query_vec=fake_vec("выручка"), vec_model="emb")
        self.assertEqual(hits[0]["mid"], "m2")

    def test_vector_recall_applies_window_cutoff_before_top_matches(self):
        thread = space_context.thread_for()
        thread.ingest("g-1", _rows([("user", "We picked the wooden chair.")] * 30))
        ids = [row["id"] for row in thread.range("g-1", 0, 29)]
        thread.put_vectors("g-1", "emb", [{"kind": "m", "ref": ref} for ref in ids],
                           [[0.5, 0.5]] + [[1.0, 0.0]] * 29)
        with patch.object(space_context, "VECTOR_BATCH_ROWS", 2), patch.object(space_context, "_vector_numpy") as optional:
            hits = thread.search("g-1", "", limit=1, before_seq=1, query_vec=[1.0, 0.0], vec_model="emb")
            all_hits = thread.search("g-1", "", limit=1, query_vec=[1.0, 0.0], vec_model="emb")
            optional.assert_not_called()
        self.assertEqual(hits[0]["mid"], "m0")
        self.assertGreaterEqual(all_hits[0]["seq"], 1)


class SpacesApiTest(_Data):
    def test_http_api_roundtrip(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        async def completion(model, messages, **kw):
            return "- эпизод"

        async def proxy_json(method, path, body=None):
            data = json.loads(body)
            return 200, {"data": [{"index": i, "embedding": [1.0, float(len(t) % 3)]} for i, t in enumerate(data["input"])]}

        app = FastAPI()
        spaces.register(app)
        space_context.register(app, completion=completion, proxy_json=proxy_json)
        client = TestClient(app)
        self.assertEqual(client.get("/api/spaces").json()["active_id"], "standard")
        created = client.post("/api/spaces", json={"type": "agent", "name": "Компаньон",
                                                    "companion": {"name": "Нова"}, "activate": True}).json()
        sid = created["space"]["id"]
        self.assertEqual(client.get("/api/spaces").json()["active"]["companion"]["name"], "Нова")
        patched = client.patch(f"/api/spaces/{sid}", json={"context": {"economy": "rich"}}).json()
        self.assertEqual(patched["space"]["context"]["economy"], "rich")
        rows = [{"id": f"x{i}", "seq": i, "role": "user" if i % 2 == 0 else "assistant",
                 "text": f"реплика {i} про море", "ts": 1_700_000_000_000 + i} for i in range(20)]
        r = client.post("/api/space/thread/ingest", json={"chat_id": f"g-{sid}", "messages": rows}).json()
        self.assertEqual(r["count"], 20)
        hits = client.post("/api/space/thread/search", json={"chat_id": f"g-{sid}", "query": "море"}).json()["hits"]
        self.assertTrue(hits)
        ctx = client.post("/api/space/thread/context", json={"chat_id": f"g-{sid}", "query": "море",
                                                             "window_start_seq": 10}).json()
        self.assertIn("bridge", ctx)
        self.assertTrue(all(h["seq"] < 10 for h in ctx["recall"] if h["kind"] == "message"))
        page = client.get(f"/api/space/thread/messages?chat_id=g-{sid}&before_seq=10&limit=5").json()
        self.assertEqual([m["seq"] for m in page["messages"]], [5, 6, 7, 8, 9])
        stats = client.get(f"/api/space/thread/stats?chat_id=g-{sid}").json()
        self.assertEqual(stats["messages"], 20)
        self.assertEqual(client.post("/api/space/embeddings/test", json={"model": "e"}).json()["dims"], 2)
        self.assertEqual(client.post("/api/space/thread/ingest", json={"chat_id": "../x"}).status_code, 400)
        self.assertEqual(client.post(f"/api/spaces/{sid}/activate").json()["active_id"], sid)
        self.assertEqual(client.delete(f"/api/spaces/{sid}").json()["active_id"], "standard")
        self.assertEqual(client.delete("/api/spaces/standard").status_code, 400)


if __name__ == "__main__":
    unittest.main()
