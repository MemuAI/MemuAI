# -*- coding: utf-8 -*-
"""Round 40 — memory: associative relevance, Conflict Resolver, session diary, background worker (no network)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import memory_assoc as assoc, memory_v2 as mem, memory_worker as worker, pgdata  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

DISTRACTORS = ["Любит фильмы про космос", "Пишет на Python и TypeScript", "Работает дизайнером интерфейсов",
               "Играет в Genshin Impact", "Предпочитает короткие ответы", "Есть старший брат", "Учит японский язык",
               "Пользуется Windows 11", "Слушает джаз по вечерам", "Хочет пробежать марафон"]

class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="mem40-"))
        self.patch = patch.object(pgdata, "MEMU_DIR", self.dir)
        self.patch.start()
        mem._USES.clear()

    def tearDown(self) -> None:
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, *texts: str, **kw) -> None:
        for text in texts:
            mem.add_manual(text, **kw)

    def texts(self, rows) -> list:
        return [r["text"] for r in rows]

class StemAndIndexTest(unittest.TestCase):
    def test_stems_ru_en(self) -> None:
        self.assertEqual(assoc.stem("лактозу"), assoc.stem("лактоза"))
        self.assertEqual(assoc.stem("кофейне"), assoc.stem("кофейня"))
        self.assertEqual(assoc.stem("coffees"), assoc.stem("coffee"))
        self.assertEqual(assoc.stem("stopped"), "stop")
        self.assertNotIn("не", assoc.stems("Не переносит лактозу"))

    def test_seed_association_lactose_coffee(self) -> None:
        idx = assoc.Index([{"id": "a", "text": "Не переносит лактозу"}, {"id": "b", "text": "Пишет на Python"}])
        scores = idx.score("Какой кофе заказать в кофейне?")
        self.assertIn("a", scores)
        self.assertNotIn("b", scores)

    def test_model_concepts_are_keys(self) -> None:
        rows = [{"id": "c", "text": "Играет на виолончели"}]
        self.assertEqual(assoc.Index(rows).score("какую канифоль для смычка взять"), {})
        rows[0]["concepts"] = ["смычок", "канифоль", "оркестр", "cello"]
        self.assertIn("c", assoc.Index(rows).score("какую канифоль для смычка взять"))

    def test_trigram_fuzzy_typo(self) -> None:
        idx = assoc.Index([{"id": "d", "text": "Любит Достоевского"}])
        self.assertIn("d", idx.score("что почитать у достаевского"))

    def test_exact_beats_association(self) -> None:
        idx = assoc.Index([{"id": "x", "text": "Пьёт кофе без сахара"}, {"id": "y", "text": "Не переносит лактозу"}])
        scores = idx.score("кофе")
        self.assertGreater(scores["x"], scores["y"])

    def test_clean_concepts(self) -> None:
        self.assertEqual(assoc.clean_concepts("молоко, кофе; молоко|x"), ["молоко", "кофе"])
        self.assertEqual(len(assoc.clean_concepts([f"слово{i}" for i in range(30)])), assoc.MAX_CONCEPTS)

    def test_index_is_cached_and_fast(self) -> None:
        rows = [{"id": f"m{i}", "text": f"Факт номер {i} про тему {i % 37} и проект {i % 11}", "updated_at": 1}
                for i in range(1000)]
        first = assoc.index_for("perf", rows)
        self.assertIs(assoc.index_for("perf", rows), first)
        started = time.perf_counter()
        for _ in range(20):
            first.score("Какой кофе заказать в кофейне про проект и тему 5")
        self.assertLess((time.perf_counter() - started) / 20, 0.05)

class RelevanceTest(Case):
    def test_lactose_reaches_a_coffee_question_within_a_tight_budget(self) -> None:
        self.add(*DISTRACTORS)
        self.add("Не переносит лактозу")
        doc = mem.load()
        doc["settings"]["core"] = False
        rows = mem.rank(doc, "chat", "Какой кофе заказать в кофейне?", budget_tokens=30)
        self.assertEqual(rows[0]["text"], "Не переносит лактозу")

    def test_before_round40_it_did_not(self) -> None:
        """The old lexical tokens share nothing — this is the bug the user described."""
        self.assertFalse(set(mem.tokens("Не переносит лактозу")) & set(mem.tokens("Какой кофе заказать в кофейне")))

    def test_core_facts_always_join_within_their_share(self) -> None:
        self.add(*DISTRACTORS)
        self.add("Аллергия на арахис", "Зовут Аня")
        doc = mem.load()
        rows = self.texts(mem.rank(doc, "chat", "посоветуй фильм про космос", budget_tokens=60))
        self.assertIn("Аллергия на арахис", rows)
        self.assertIn("Любит фильмы про космос", rows)
        doc["settings"]["core"] = False
        self.assertNotIn("Аллергия на арахис", self.texts(mem.rank(doc, "chat", "посоветуй фильм про космос",
                                                                        budget_tokens=25)))

    def test_core_share_is_capped(self) -> None:
        self.add(*[f"Аллергия на продукт номер {i} и его производные" for i in range(20)])
        doc = mem.load()
        rows = mem.rank(doc, "chat", "привет", budget_tokens=400)
        spent = sum(mem.estimate_tokens(r["text"]) for r in rows if mem.is_core(r))
        self.assertLessEqual(spent, int(400 * mem.CORE_SHARE))

    def test_context_facts_and_block(self) -> None:
        self.add(*DISTRACTORS)
        self.add("Не переносит лактозу")
        block = mem.profile_context("chat", "Какой кофе заказать?")
        self.assertIn("Не переносит лактозу", block)

class ConceptsWriteTest(Case):
    def test_block_concepts_are_stored(self) -> None:
        raw = "Ок.\n\n```memuai-memory\n" + json.dumps({"facts": [{"fact": "Играет на виолончели", "category": "interest",
                                                                   "confidence": 0.9, "concepts": ["смычок", "оркестр"]}]},
                                                       ensure_ascii=False) + "\n```"
        clean, result = mem.process_answer(raw, service="chat")
        self.assertEqual(clean, "Ок.")
        item = mem.load()["items"][0]
        self.assertEqual(item["concepts"], ["смычок", "оркестр"])
        self.assertIn(item["id"], {f["id"] for f in mem.context_facts("chat", "нужен новый смычок", budget_tokens=20)})

    def test_edit_text_clears_concepts_for_a_refill(self) -> None:
        mem.propose([{"fact": "Играет на виолончели", "concepts": ["смычок"]}], service="chat", by="user")
        item = mem.load()["items"][0]
        mem.update_item(item["id"], {"text": "Играет на альте"})
        self.assertNotIn("concepts", mem.load()["items"][0])
        mem.update_item(item["id"], {"concepts": ["смычок", "альт"]})
        self.assertEqual(mem.load()["items"][0]["concepts"], ["смычок", "альт"])

    def test_protocol_asks_for_concepts_and_stays_compact(self) -> None:
        text = mem.protocol_prompt("chat", doc=mem.load())
        self.assertIn('"concepts"', text)
        self.assertLess(len(text), 2100)

class ConflictTest(Case):
    def test_moved_city_is_an_update_with_dated_history(self) -> None:
        self.add("Живёт в Москве")
        result = mem.propose([{"fact": "Переехала в Казань", "category": "identity", "confidence": 0.9}], service="chat")
        self.assertEqual(len(result["updated"]), 1)
        active = [i for i in mem.load()["items"] if i["status"] == "active"]
        self.assertEqual(self.texts(active), ["Переехала в Казань"])
        last = active[0]["history"][-1]
        self.assertEqual((last["action"], last["text"]), ("updated", "Живёт в Москве"))
        self.assertTrue(last["from"] <= last["until"])

    def test_opposite_claim_waits_in_the_inbox(self) -> None:
        self.add("Любит кофе", category="preference")
        result = mem.propose([{"fact": "Не пьёт кофе", "category": "preference", "confidence": 0.9}], service="chat")
        self.assertEqual(result["pending"][0]["reason"], "conflict")
        pending = [i for i in mem.load()["items"] if i["status"] == "pending"][0]
        self.assertEqual(pending["op"], "update")
        mem.approve(pending["id"])
        doc = mem.load()
        self.assertEqual(self.texts(i for i in doc["items"] if i["status"] == "active"), ["Не пьёт кофе"])
        self.assertTrue(doc["items"][0]["history"][-1].get("until"))

    def test_keep_both(self) -> None:
        self.add("Любит кофе", category="preference")
        mem.propose([{"fact": "Не любит кофе по вечерам", "category": "preference"}], service="chat")
        pending = [i for i in mem.load()["items"] if i["status"] == "pending"][0]
        mem.approve(pending["id"], keep_both=True)
        self.assertEqual(len([i for i in mem.load()["items"] if i["status"] == "active"]), 2)

    def test_explicit_change_word_updates_at_once(self) -> None:
        self.add("Любит кофе", category="preference")
        result = mem.propose([{"fact": "Больше не пьёт кофе", "category": "preference"}], service="chat")
        self.assertEqual(len(result["updated"]), 1)

    def test_negation_is_not_a_duplicate(self) -> None:
        self.add("Любит кофе", category="preference")
        result = mem.propose([{"fact": "Не любит кофе", "category": "preference"}], service="chat")
        self.assertEqual(result["duplicates"], 0)

    def test_unrelated_facts_stay_apart(self) -> None:
        self.add("Любит кофе", category="preference")
        result = mem.propose([{"fact": "Любит горы", "category": "preference"}], service="chat")
        self.assertEqual(len(result["added"]), 1)

    def test_inbox_card_shows_old_text_and_keep_both(self) -> None:
        posted = []

        class FakeInbox:
            def list(self, **kw):
                return []

            def resolve(self, *a, **kw):
                pass

            def post(self, kind, title, body, **kw):
                posted.append((title, body, kw))

        self.add("Любит кофе", category="preference")
        from app import inbox_hooks
        with patch.object(inbox_hooks, "_inbox", lambda: FakeInbox()):
            mem.propose([{"fact": "Не пьёт кофе", "category": "preference"}], service="chat")
        title, body, kw = posted[-1]
        self.assertIn("Любит кофе", body)
        self.assertIn("both", [a["id"] for a in kw["actions"]])

class MigrationTest(Case):
    def test_round39_document_loads_without_loss(self) -> None:
        old = {"version": 2, "enabled": True, "items": [
            {"id": "m1", "text": "Зовут Аня", "category": "identity", "status": "active", "created_at": 1,
             "history": [{"at": 1, "action": "created", "by": "user"}, {"at": 2, "action": "updated", "text": "Зовут Анна",
                                                                       "by": "ai"}]}],
            "rules": [{"id": "r1", "text": "Не запоминай адреса"}], "settings": {"budget_tokens": 900}}
        (self.dir / "memory.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        doc = mem.load()
        item = doc["items"][0]
        self.assertNotIn("concepts", item)
        self.assertEqual(len(item["history"]), 2)
        self.assertEqual(doc["diary"], [])
        self.assertEqual(doc["settings"]["budget_tokens"], 900)
        self.assertTrue(doc["settings"]["diary"]["enabled"])
        self.assertEqual(mem.public(doc)["stats"]["no_concepts"], 1)
        mem.mutate(lambda d: None)
        raw = json.loads((self.dir / "memory.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["rules"][0]["text"], "Не запоминай адреса")
        self.assertEqual(raw["items"][0]["history"][1]["text"], "Зовут Анна")

    def test_export_import_keep_concepts_and_diary(self) -> None:
        mem.propose([{"fact": "Играет на виолончели", "concepts": ["смычок"]}], service="chat", by="user")
        mem.save_diary({"text": "Обсуждали выбор смычка", "chat_id": "c1", "at": 1000})
        data = mem.export_doc()
        shutil.rmtree(self.dir)
        self.dir.mkdir()
        mem.import_data(json.dumps(data, ensure_ascii=False))
        doc = mem.load()
        self.assertEqual(doc["items"][0]["concepts"], ["смычок"])
        self.assertEqual(doc["diary"][0]["text"], "Обсуждали выбор смычка")

class DiaryTest(Case):
    def test_save_edit_delete_dismiss(self) -> None:
        row = mem.save_diary({"text": "Настраивали парсер вакансий", "chat_id": "c1", "at": 1000, "msgs": 8})["result"]
        mem.edit_diary(row["id"], "Настраивали парсер вакансий hh.ru")
        mem.save_diary({"text": "Другой итог", "chat_id": "c1", "at": 2000, "msgs": 14})
        entry = mem.load()["diary"][0]
        self.assertEqual(entry["text"], "Настраивали парсер вакансий hh.ru")
        self.assertEqual(entry["msgs"], 14)
        mem.delete_diary([entry["id"]])
        self.assertIsNone(mem.save_diary({"text": "Снова", "chat_id": "c1", "at": 3000})["result"])
        self.assertEqual(mem.load()["diary"], [])

    def test_secret_never_lands(self) -> None:
        with self.assertRaises(ValueError):
            mem.save_diary({"text": "Ключ sk-abcdefghijklmnopqrstuvwx", "chat_id": "c2"})

    def test_context_recent_and_relevant(self) -> None:
        day = 86400_000
        mem.save_diary({"text": "Настраивали парсер вакансий в Планировании", "chat_id": "a", "at": 10 * day})
        for n in range(5):
            mem.save_diary({"text": f"Болтали о погоде {n}", "chat_id": f"w{n}", "at": (20 + n) * day})
        rows = mem.context_diary("как там мой парсер вакансий?")
        texts = self.texts(rows)
        self.assertIn("Настраивали парсер вакансий в Планировании", texts)
        self.assertEqual(len([t for t in texts if t.startswith("Болтали")]), 3)
        block = mem.profile_context("chat", "как там мой парсер вакансий?")
        self.assertIn("recent_sessions", block)
        self.assertRegex(block, r"\d\d\.\d\d\.\d{4}: Настраивали парсер")
        mem.set_settings({"diary": {"enabled": False}})
        self.assertEqual(mem.context_diary("парсер"), [])

def run(coro):
    return asyncio.run(coro)

class WorkerTest(Case):
    def setUp(self) -> None:
        super().setUp()
        worker._STATE.update({"task": None, "last": 0.0, "backoff_until": 0.0, "last_error": ""})
        self.calls = []

    def fake(self, answers):
        async def completion(model, messages, **kw):
            self.calls.append((model, messages))
            value = answers.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return completion

    def test_fill_concepts_for_old_facts(self) -> None:
        (self.dir / "memory.json").write_text(json.dumps({"facts": ["Не переносит лактозу", "Есть собака"]},
                                                         ensure_ascii=False), encoding="utf-8")
        mem.mutate(lambda d: None)
        doc = mem.load()
        ids = [i["id"] for i in doc["items"]]
        answer = "```json\n" + json.dumps({ids[0]: ["молоко", "кофе", "milk"]}, ensure_ascii=False) + "\n```"
        self.assertEqual(run(worker.fill_concepts(self.fake([answer]), "m", doc)), 2)
        items = {i["id"]: i for i in mem.load()["items"]}
        self.assertEqual(items[ids[0]]["concepts"], ["молоко", "кофе", "milk"])
        self.assertEqual(items[ids[1]]["concepts"], [])
        self.assertEqual(run(worker.fill_concepts(self.fake([]), "m", mem.load())), 0)

    def chats(self, now):
        long = "Подробный разговор про настройку парсера вакансий и фильтры по зарплате. " * 6
        msgs = [{"role": "user" if n % 2 == 0 else "assistant", "content": long} for n in range(8)]
        return [
            {"id": "big", "title": "Парсер вакансий", "updated": now - 30 * 60_000, "messages": msgs},
            {"id": "fresh", "title": "Сейчас", "updated": now - 60_000, "messages": msgs},
            {"id": "small", "title": "Короткий", "updated": now - 30 * 60_000, "messages": msgs[:2]},
            {"id": "temp", "temp": True, "updated": now - 30 * 60_000, "messages": msgs},
        ]

    def test_diary_candidates_and_write(self) -> None:
        now = mem.now_ms()
        chats = self.chats(now)
        self.assertEqual([c["id"] for c in worker.diary_candidates(mem.load(), chats, now)], ["big"])
        with patch.object(pgdata, "load_chats", lambda *a, **k: chats):
            done = run(worker.write_diary(self.fake(["Настраивали парсер вакансий, выбрали фильтр по зарплате"]), "m",
                                          mem.load()))
        self.assertEqual(done, 1)
        entry = mem.load()["diary"][0]
        self.assertEqual((entry["chat_id"], entry["msgs"]), ("big", 8))
        self.assertEqual(worker.diary_candidates(mem.load(), chats, now), [])

    def test_failure_backs_off_and_kick_is_lazy(self) -> None:
        self.add("Не переносит лактозу")
        worker.configure(self.fake([RuntimeError("429 rate limit")]))
        mem.set_settings({"helper_model": "cheap-model"})

        async def go():
            self.assertTrue(worker.kick())
            self.assertFalse(worker.kick())
            await worker._STATE["task"]
        try:
            run(go())
            self.assertIn("429", worker.status()["error"])
            self.assertGreater(worker._STATE["backoff_until"], time.time())
            self.assertEqual(self.calls[0][0], "cheap-model")
        finally:
            worker.configure(None)

    def test_kick_without_loop_or_completion_is_a_no_op(self) -> None:
        worker.configure(None)
        self.assertFalse(worker.kick())

class ApiTest(Case):
    def client(self, completion=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import memory_api
        app = FastAPI()
        memory_api.register(app, completion=completion)
        return TestClient(app)

    def tearDown(self) -> None:
        worker.configure(None)
        super().tearDown()

    def test_context_carries_diary_and_associations(self) -> None:
        self.add(*DISTRACTORS)
        self.add("Не переносит лактозу")
        mem.save_diary({"text": "Выбирали кофемашину", "chat_id": "c9", "at": mem.now_ms()})
        c = self.client()
        out = c.post("/api/memory/context", json={"service": "chat", "query": "Какой кофе заказать в кофейне?",
                                                  "budget_tokens": 40}).json()
        self.assertIn("Не переносит лактозу", out["block"])
        self.assertIn("Выбирали кофемашину", out["block"])

    def test_diary_endpoints_and_settings(self) -> None:
        row = mem.save_diary({"text": "Итог", "chat_id": "c1"})["result"]
        c = self.client()
        r = c.patch(f"/api/memory/diary/{row['id']}", json={"text": "Новый итог"}).json()
        self.assertEqual(r["memory"]["diary"][0]["text"], "Новый итог")
        self.assertTrue(r["memory"]["diary"][0]["edited"])
        self.assertEqual(c.delete(f"/api/memory/diary/{row['id']}").json()["count"], 1)
        self.assertEqual(c.patch("/api/memory/diary/zzz", json={"text": "x"}).status_code, 404)
        s = c.post("/api/memory/settings", json={"diary": {"recent": 5}, "core": False, "helper_model": "x"}).json()
        self.assertEqual((s["memory"]["settings"]["diary"]["recent"], s["memory"]["settings"]["core"]), (5, False))
        self.assertIn("running", c.get("/api/memory/worker").json())
        self.assertFalse(c.post("/api/memory/worker/run", json={}).json()["started"])

    def test_patch_concepts_and_keep_both(self) -> None:
        self.add("Любит кофе", category="preference")
        c = self.client()
        item = mem.load()["items"][0]
        r = c.patch(f"/api/memory/items/{item['id']}", json={"concepts": ["капучино", "латте"]}).json()
        self.assertEqual(r["memory"]["items"][0]["concepts"], ["капучино", "латте"])
        mem.propose([{"fact": "Не пьёт кофе", "category": "preference"}], service="chat")
        pending = [i for i in mem.load()["items"] if i["status"] == "pending"][0]
        c.post(f"/api/memory/items/{pending['id']}/approve", json={"keep_both": True})
        self.assertEqual(len([i for i in mem.load()["items"] if i["status"] == "active"]), 2)

class UiTest(unittest.TestCase):
    def test_js_and_css(self) -> None:
        js = (STATIC / "memory.js").read_text(encoding="utf-8")
        css = (STATIC / "memory.css").read_text(encoding="utf-8")
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for needle in ("function diaryView", "data-both", "mem2-concepts", "/api/memory/worker/run", "mem2-period",
                       "Ассоциации", "Associations", "Дневник сессий", "Session diary", "Оставить оба", "Keep both"):
            self.assertIn(needle, js)
        for needle in (".mem2-concepts", ".mem2-diary-head", ".mem2-reason.r-conflict"):
            self.assertIn(needle, css)
        self.assertIn("/memory.js?v=1.1-r40", html)
        self.assertIn("/memory.css?v=1.2-r40", html)
        node = shutil.which("node")
        if node:
            self.assertEqual(subprocess.run([node, "--check", str(STATIC / "memory.js")]).returncode, 0)

if __name__ == "__main__":
    unittest.main()
