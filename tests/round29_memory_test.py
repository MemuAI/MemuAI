# -*- coding: utf-8 -*-
"""Round 29 — memory v2 (app/memory_v2.py, app/memory_api.py, pgstatic/memory.js)."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import memory_v2 as mem, pgdata  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"


class MemoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="mem2-"))
        self.patch = patch.object(pgdata, "MEMU_DIR", self.dir)
        self.patch.start()
        mem._USES.clear()

    def tearDown(self) -> None:
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name: str, data) -> None:
        (self.dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def raw(self, name: str = "memory.json"):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    @staticmethod
    def block(*facts) -> str:
        return "Ответ.\n\n```memuai-memory\n" + json.dumps({"facts": list(facts)}, ensure_ascii=False) + "\n```"


class StorageTest(MemoryCase):
    def test_v1_migrates_and_legacy_readers_keep_working(self) -> None:
        self.write("memory.json", {"enabled": True, "facts": ["Зовут Иван", "Любит Python", "Зовут Иван"]})
        doc = mem.load()
        self.assertEqual([i["text"] for i in doc["items"]], ["Зовут Иван", "Любит Python"])
        self.assertTrue(all(i["source"]["service"] == "legacy" for i in doc["items"]))
        mem.mutate(lambda d: None)
        raw = self.raw()
        self.assertEqual(raw["version"], 2)
        self.assertEqual(raw["facts"], ["Зовут Иван", "Любит Python"])
        self.assertEqual(pgdata.load_memory(), {"enabled": True, "facts": ["Зовут Иван", "Любит Python"]})

    def test_broken_shapes_never_raise(self) -> None:
        self.write("memory.json", {"enabled": "false", "facts": {"bad": "shape"}, "items": "x", "rules": 5})
        doc = mem.load()
        self.assertFalse(doc["enabled"])
        self.assertEqual(doc["items"], [])
        (self.dir / "memory.json").write_text("[1, 2", encoding="utf-8")
        self.assertEqual(mem.load()["items"], [])
        mem.add_manual("Любит чай")
        self.assertEqual(mem.load()["facts"], ["Любит чай"])

    def test_a_v1_writer_is_reconciled_without_losing_v2_data(self) -> None:
        mem.add_manual("Любит чай")
        mem.add_manual("Пишет на Rust", scope=["code"])
        raw = self.raw()
        raw["facts"] = ["Любит кофе"]          # an older client replaced the list
        self.write("memory.json", raw)
        doc = mem.load()
        active = {i["text"]: i for i in doc["items"] if i["status"] == "active"}
        self.assertIn("Любит кофе", active)
        self.assertNotIn("Любит чай", active)
        self.assertIn("Пишет на Rust", active, "a service-scoped fact is not part of the legacy list")
        mem.save_legacy({"enabled": True, "facts": ["Любит кофе", "Любит чай"]})
        self.assertEqual(sorted(mem.load()["facts"]), ["Любит кофе", "Любит чай"])
        self.assertEqual(len([i for i in mem.load()["items"] if i["text"] == "Любит чай"]), 1, "restored, not duplicated")


class ExtractionTest(MemoryCase):
    def test_block_and_legacy_marker_are_stored_with_their_source(self) -> None:
        raw = self.block({"fact": "Живёт в Берлине", "category": "identity", "confidence": 0.9},
                         {"fact": "Пользуется Neovim", "category": "tech", "confidence": 0.8, "scope": ["memucode"]}) \
            + " [MEM: любит чай]"
        clean, res = mem.process_answer(raw, service="chat", source={"chat_id": "c1", "chat_title": "Т", "message_at": 7,
                                                                     "excerpt": "я из Берлина"})
        self.assertEqual(clean, "Ответ.")
        self.assertEqual(len(res["added"]), 3)
        doc = mem.load()
        nvim = next(i for i in doc["items"] if i["text"] == "Пользуется Neovim")
        self.assertEqual(nvim["scope"], ["code"])
        self.assertEqual(nvim["source"]["chat_id"], "c1")
        self.assertEqual(nvim["source"]["message_at"], 7)
        self.assertEqual(nvim["confidence"], 0.8)
        tea = next(i for i in doc["items"] if i["text"] == "любит чай")
        self.assertEqual(tea["confidence"], mem.LEGACY_CONFIDENCE)

    def test_duplicates_merge_and_contradictions_update(self) -> None:
        mem.process_answer(self.block({"fact": "Живёт в Берлине", "category": "identity", "confidence": 0.9}),
                           service="chat", source={"chat_id": "a"})
        _, res = mem.process_answer(self.block({"fact": "живёт в берлине", "confidence": 0.95}), service="telegram",
                                    source={"chat_id": "b"})
        self.assertEqual(res["duplicates"], 1)
        _, res = mem.process_answer(self.block({"fact": "Живёт в Москве", "category": "identity", "confidence": 0.9}),
                                    service="telegram", source={"chat_id": "c"})
        self.assertEqual([r["text"] for r in res["updated"]], ["Живёт в Москве"])
        doc = mem.load()
        active = [i for i in doc["items"] if i["status"] == "active"]
        self.assertEqual(len(active), 1)
        item = active[0]
        self.assertEqual(item["source"]["chat_id"], "c", "the new wording's chat becomes the primary source")
        self.assertTrue(any(h["action"] == "updated" and h["text"] == "Живёт в Берлине" for h in item["history"]))

    def test_update_and_forget_by_id(self) -> None:
        mem.add_manual("Любит зелёный чай")
        fid = mem.load()["items"][0]["id"]
        mem.process_answer(self.block({"op": "update", "id": fid, "fact": "Любит улун", "confidence": 0.9}), service="chat")
        self.assertEqual(mem.load()["facts"], ["Любит улун"])
        mem.process_answer(self.block({"op": "forget", "id": fid}), service="chat")
        doc = mem.load()
        self.assertEqual(doc["facts"], [])
        self.assertEqual(doc["items"][0]["status"], "deleted")

    def test_secrets_rules_sensitive_and_ask_mode(self) -> None:
        _, res = mem.process_answer(self.block({"fact": "Пароль от почты: hunter2", "confidence": 1},
                                               {"fact": "Ключ sk-abcdefghijklmnopqrstuv1234", "confidence": 1}),
                                    service="chat")
        self.assertEqual(res["blocked"], 2)
        self.assertNotIn("hunter2", json.dumps(self.raw(), ensure_ascii=False))
        mem.add_rule("Не запоминай ничего о здоровье и «кот»")
        _, res = mem.process_answer(self.block({"fact": "Болеет диабетом", "confidence": 0.9},
                                               {"fact": "Есть кот Барсик", "confidence": 0.9}), service="chat")
        self.assertEqual(res["blocked"], 2)
        _, res = mem.process_answer(self.block({"fact": "Зарплата 300 тысяч", "confidence": 0.9}), service="chat")
        self.assertEqual(len(res["pending"]), 1)
        self.assertEqual(res["pending"][0]["reason"], "sensitive")
        _, res = mem.process_answer(self.block({"fact": "Наверное любит джаз", "confidence": 0.3}), service="chat")
        self.assertEqual(res["pending"][0]["reason"], "low_confidence")
        mem.set_settings({"mode": "ask"})
        _, res = mem.process_answer(self.block({"fact": "Любит горы", "confidence": 0.9}), service="chat")
        pid = res["pending"][0]["id"]
        self.assertEqual(res["pending"][0]["reason"], "ask")
        mem.approve(pid, text="Очень любит горы")
        self.assertIn("Очень любит горы", mem.load()["facts"])
        _, res = mem.process_answer(self.block({"fact": "Любит море", "confidence": 0.9}), service="chat")
        mem.reject(res["pending"][0]["id"])
        _, res = mem.process_answer(self.block({"fact": "Любит море", "confidence": 0.9}), service="chat")
        self.assertEqual(res["blocked"], 1, "a rejected fact is not proposed again")

    def test_service_switches_and_temp(self) -> None:
        mem.set_settings({"services": {"telegram": {"write": False, "read": False}}})
        _, res = mem.process_answer(self.block({"fact": "Любит чай", "confidence": 0.9}), service="telegram")
        self.assertEqual(res["added"], [])
        _, res = mem.process_answer(self.block({"fact": "Любит чай", "confidence": 0.9}), service="chat", temp=True)
        self.assertEqual(res["added"], [])
        mem.add_manual("Любит чай")
        self.assertEqual(mem.context_facts("telegram", "чай"), [])
        self.assertEqual(mem.context_facts("chat", "чай", temp=True), [])
        self.assertEqual(mem.protocol_prompt("telegram"), "")
        self.assertIn("memuai-memory", mem.protocol_prompt("chat"))

    def test_forget_chat_keeps_facts_confirmed_elsewhere(self) -> None:
        mem.process_answer(self.block({"fact": "Любит чай", "confidence": 0.9}), service="chat", source={"chat_id": "x"})
        mem.process_answer(self.block({"fact": "Любит горы", "confidence": 0.9}), service="chat", source={"chat_id": "y"})
        mem.process_answer(self.block({"fact": "Любит горы", "confidence": 0.9}), service="chat", source={"chat_id": "x"})
        out = mem.forget_chat("x")
        self.assertEqual(out["result"], 1)
        self.assertEqual(mem.load()["facts"], ["Любит горы"])


class RankingTest(MemoryCase):
    def test_scope_pins_relevance_and_budget(self) -> None:
        for i in range(60):
            mem.add_manual(f"Факт номер {i} про разные вещи и случайные темы")
        mem.add_manual("Любимый редактор — Neovim", scope=["code"])
        mem.add_manual("Пишет на Python и TypeScript")
        mem.add_manual("Зовут Анна", pinned=True)
        facts = mem.context_facts("chat", "какой язык программирования выбрать: python?", budget_tokens=60)
        texts = [f["text"] for f in facts]
        self.assertEqual(texts[0], "Зовут Анна", "pinned first")
        self.assertIn("Пишет на Python и TypeScript", texts[:3])
        self.assertNotIn("Любимый редактор — Neovim", texts, "scoped to MemuCode")
        self.assertLess(len(texts), 10)
        code = [f["text"] for f in mem.context_facts("code", "редактор", budget_tokens=60)]
        self.assertIn("Любимый редактор — Neovim", code[:3])
        block = mem.profile_context("chat", "python", name="Анна")
        self.assertIn("untrusted personalization data", block)
        self.assertRegex(block, r"\[m[0-9a-f]{6}\] Зовут Анна")


class StreamingTest(unittest.TestCase):
    TEXT = ("Вот ответ с кодом:\n```python\nprint('```memu')\n```\nи всё. [MEM: любит чай] Дальше."
            "\n\n```memuai-memory\n{\"facts\":[{\"fact\":\"секретный факт\"}]}\n```\n")

    def test_strip_markers(self) -> None:
        clean = mem.strip_markers(self.TEXT, partial=False).strip()
        self.assertNotIn("секретный", clean)
        self.assertNotIn("[MEM", clean)
        self.assertIn("print('```memu')", clean)
        self.assertEqual(mem.strip_markers("ok ```memuai-memory\n{\"facts\":[").strip(), "ok")
        self.assertEqual(mem.strip_markers("ok ``", partial=True), "ok ")

    def test_stream_filter_never_shows_a_block_at_any_split(self) -> None:
        rng = random.Random(29)
        expected = mem.strip_markers(self.TEXT, partial=False).rstrip()
        for _ in range(300):
            cuts = sorted(rng.sample(range(1, len(self.TEXT)), rng.randint(1, 25)))
            pieces = [self.TEXT[a:b] for a, b in zip([0] + cuts, cuts + [len(self.TEXT)])]
            flt, shown = mem.StreamFilter(), ""
            for piece in pieces:
                shown += flt.feed(piece)
                self.assertNotIn("секретный", shown)
                self.assertNotIn("memuai-memory", shown)
                self.assertNotIn("[MEM:", shown)
            shown += flt.finish()
            self.assertEqual(shown.rstrip(), expected)


class ImportExportTest(MemoryCase):
    def test_round_trip_json_markdown_and_text(self) -> None:
        mem.add_manual("Любит чай", pinned=True, tags=["еда"])
        mem.add_manual("Пишет на Rust", scope=["code"])
        mem.add_rule("Пиши кратко")
        data = mem.export_doc()
        md = mem.export_markdown(lang="en")
        self.assertIn("- Любит чай", md)
        self.assertIn("only for MemuCode", md)
        other = Path(tempfile.mkdtemp())
        with patch.object(pgdata, "MEMU_DIR", other):
            out = mem.import_data(json.dumps(data, ensure_ascii=False))
            self.assertEqual(len(out["result"]["added"]), 2)
            doc = mem.load()
            tea = next(i for i in doc["items"] if i["text"] == "Любит чай")
            self.assertTrue(tea["pinned"])
            self.assertEqual(next(i for i in doc["items"] if i["text"] == "Пишет на Rust")["scope"], ["code"])
            self.assertEqual([r["text"] for r in doc["rules"]], ["Пиши кратко"])
            out = mem.import_data("- Любит чай\n- Любит горы\n# heading")
            self.assertEqual(out["result"]["duplicates"], 1)
            self.assertEqual(len(out["result"]["added"]), 1)
        shutil.rmtree(other, ignore_errors=True)


class ProfilesTest(MemoryCase):
    def profiles(self, active: str) -> None:
        self.write("profiles.json", {"active_id": active, "items": [
            {"id": "default", "name": "A", "memory": {"enabled": True, "facts": ["старый факт A"]}},
            {"id": "p2", "name": "B", "memory": {"enabled": True, "facts": ["снимок B"]}}]})

    def test_switch_parks_documents_and_heals(self) -> None:
        self.profiles("default")
        mem.add_manual("Факт A")
        self.profiles("p2")
        doc = mem.switch_profile("default", "p2")
        self.assertEqual(doc["facts"], ["снимок B"], "a profile without a v2 file starts from its snapshot")
        self.assertTrue((self.dir / "memory_profiles" / "default.json").exists())
        mem.add_manual("Факт B")
        self.assertEqual(sorted(mem.load("default")["facts"]), ["Факт A", "старый факт A"])
        # the active profile changed elsewhere (no switch call): the next read swaps the files itself
        self.profiles("default")
        self.assertEqual(sorted(mem.load()["facts"]), ["Факт A", "старый факт A"])
        self.assertEqual(sorted(mem.load("p2")["facts"]), ["Факт B", "снимок B"])

    def test_deleting_the_active_profile_does_not_leak_its_memory(self) -> None:
        self.profiles("p2")
        mem.switch_profile("default", "p2")
        mem.add_manual("Тайна профиля B")
        self.write("profiles.json", {"active_id": "default", "items": [
            {"id": "default", "name": "A", "memory": {"enabled": True, "facts": ["старый факт A"]}}]})
        mem.delete_profile_memory("p2", was_active=True)
        self.assertNotIn("Тайна профиля B", json.dumps(self.raw(), ensure_ascii=False))
        self.assertEqual(mem.load()["facts"], ["старый факт A"])

    def test_facts_for_profile_dict(self) -> None:
        self.profiles("default")
        mem.add_manual("Живой факт")
        self.assertEqual(mem.facts_for_profile({"memory": {"facts": ["x"]}}, "telegram"), ["x"])
        lines = mem.facts_for_profile({"id": "default"}, "telegram")
        self.assertTrue(any(line.endswith("Живой факт") for line in lines))


class ApiTest(MemoryCase):
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import memory_api
        app = FastAPI()
        memory_api.register(app)
        return TestClient(app)

    def test_service_endpoints(self) -> None:
        c = self.client()
        r = c.post("/api/memory/propose", json={"service": "memunote", "facts": [{"fact": "Ведёт заметки в Markdown",
                                                                                     "confidence": 0.9, "scope": ["note"]}],
                                                 "source": {"chat_id": "n1", "chat_title": "Заметка"}}).json()
        self.assertEqual(len(r["added"]), 1)
        self.assertEqual(c.get("/api/memory/search", params={"service": "note", "q": "markdown"}).json()["facts"][0]["text"],
                         "Ведёт заметки в Markdown")
        self.assertEqual(c.get("/api/memory/search", params={"service": "chat", "q": "markdown"}).json()["facts"], [])
        ctx = c.post("/api/memory/context", json={"service": "note", "query": "markdown", "name": "Аня"}).json()
        self.assertIn("Ведёт заметки", ctx["block"])
        self.assertIn("memory_protocol", ctx["protocol"])
        self.assertEqual(c.post("/api/memory/context", json={"service": "chat", "temp": True}).json()["block"], "")
        out = c.post("/api/memory/extract", json={"service": "chat", "text": self.block({"fact": "Любит чай", "confidence": 0.9}),
                                                   "source": {"chat_id": "c9"}}).json()
        self.assertEqual(out["text"], "Ответ.")
        self.assertEqual(out["added"][0]["text"], "Любит чай")
        self.assertEqual(c.post("/api/memory/propose", json={"service": "nope", "fact": "x"}).status_code, 400)

    def test_panel_endpoints(self) -> None:
        c = self.client()
        doc = c.post("/api/memory/items", json={"text": "Любит чай", "tags": ["еда"]}).json()["memory"]
        fid = doc["items"][0]["id"]
        self.assertEqual(c.post("/api/memory/items", json={"text": "пароль: 12345"}).status_code, 400)
        doc = c.patch(f"/api/memory/items/{fid}", json={"text": "Любит улун", "pinned": True, "scope": ["code"]}).json()["memory"]
        item = doc["items"][0]
        self.assertEqual((item["text"], item["pinned"], item["scope"]), ("Любит улун", True, ["code"]))
        self.assertEqual(c.patch("/api/memory/items/missing", json={"text": "x"}).status_code, 404)
        doc = c.post("/api/memory/bulk", json={"ids": [fid], "action": "delete"}).json()["memory"]
        self.assertEqual(doc["stats"]["deleted"], 1)
        doc = c.post("/api/memory/bulk", json={"ids": [fid], "action": "restore"}).json()["memory"]
        self.assertEqual(doc["stats"]["active"], 1)
        r = c.post("/api/memory/rules", json={"text": "Не запоминай «улун»"}).json()
        self.assertEqual(r["rule_matches"], 1)
        r = c.post("/api/memory/rules/apply", json={}).json()
        self.assertEqual(r["count"], 1)
        self.assertEqual(c.post("/api/memory/settings", json={"mode": "ask", "budget_tokens": 99999}).json()["memory"]["settings"]["budget_tokens"], 8000)
        md = c.get("/api/memory/export", params={"format": "md"})
        self.assertEqual(md.status_code, 200)
        self.assertEqual(c.post("/api/memory/legacy", json={"facts": "x"}).status_code, 400)


class IntegrationTest(MemoryCase):
    def test_clients_strip_the_block(self) -> None:
        from app import tgapp_chat, telegram_bot
        text = "Привет! " + self.block({"fact": "x"})
        self.assertNotIn("memuai-memory", tgapp_chat.clean_answer(text))
        self.assertNotIn("memuai", telegram_bot._clean_internal_protocols("Привет ```memuai-mem", partial=True))
        self.assertNotIn("```memu", telegram_bot._clean_internal_protocols("Привет ```memu", partial=True))

    def test_desktop_hides_the_block_while_streaming(self) -> None:
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("|dfy|memory)", js, "hidePrivateProtocols removes finished memuai-memory blocks")
        self.assertIn("MemuMemory.contextFor", js)
        self.assertIn("MemuMemory.extract", js)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('/memory.js?v=1.1-r40', html)
        self.assertIn('/memory.css?v=1.2-r40', html)

    def test_memory_js_syntax_and_rule_parser_parity(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        subprocess.run([node, "--check", str(STATIC / "memory.js")], check=True)
        rules = ["Не запоминай ничего о здоровье", "Don't remember anything about money", "Пиши кратко",
                 "Никогда не запоминай «пароль» и \"кот\"", "Do not store my family or address"]
        script = ("globalThis.window=globalThis;globalThis.document={querySelectorAll:()=>[],querySelector:()=>null};"
                  "globalThis.setTimeout=()=>0;require(process.argv[1]);"
                  "const rules=JSON.parse(process.argv[2]);"
                  "console.log(JSON.stringify(rules.map(r=>{const p=MemuMemory._parseRule(r);return [p.negative,p.cats.sort(),p.terms];})))")
        out = subprocess.run([node, "-e", script, str(STATIC / "memory.js"), json.dumps(rules, ensure_ascii=False)],
                             check=True, capture_output=True, text=True).stdout
        js_rows = json.loads(out)
        for rule, row in zip(rules, js_rows):
            parsed = mem.parse_rule(rule)
            self.assertEqual(row, [parsed["negative"], sorted(parsed["deny_categories"]), parsed["deny_terms"]], rule)

    def test_english_ui_strings_have_no_cyrillic(self) -> None:
        import re
        js = (STATIC / "memory.js").read_text(encoding="utf-8")
        for ru, en in re.findall(r'L\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', js):
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", en), en)

    def test_build_lists_the_modules(self) -> None:
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"app.memory_v2", "app.memory_api"', build)
        for spec in ("MemuAI.spec", "MemuCode.spec", "MemuAIProxy.spec"):
            self.assertIn("app.memory_v2", (ROOT / spec).read_text(encoding="utf-8"), spec)


class DocsAndUiTest(unittest.TestCase):
    """The help article describes memory v2 as the panel implements it (finisher r29-finish-memory)."""

    def page(self, lang: str) -> str:
        return (ROOT / "app" / "docs" / lang / "memory.md").read_text(encoding="utf-8")

    def test_article_covers_memory_v2_in_both_languages(self) -> None:
        js = (STATIC / "memory.js").read_text(encoding="utf-8")
        words = {"ru": ["Входящие", "«Только для»", "Правила", "Корзин", "Экспорт JSON", "Экспорт Markdown", "Импорт…",
                        "Уверенность", "Источник", "Спрашивать меня", "Порог уверенности", "Объём памяти в запросе",
                        "Забыть всё из этого чата", "Память из этого чата", "Mini App"],
                 "en": ["Inbox", "«Only for»", "Rules", "Trash", "Export JSON", "Export Markdown", "Import…",
                        "Confidence", "Source", "Ask me", "Confidence threshold", "Memory per request",
                        "Forget everything from this chat", "Memory from this chat", "Mini App"]}
        for lang, need in words.items():
            text = self.page(lang)
            for word in need:
                self.assertIn(word, text, f"{lang}: {word}")
                if lang == "en" and word != "Mini App":
                    self.assertIn(word.strip("«»…"), js, f"the panel has no «{word}»")
            self.assertIn(str(mem.TRASH_DAYS), text)
        self.assertIsNone(__import__("re").search(r"[А-Яа-яЁё]", self.page("en")))

    def test_panel_selects_never_become_the_searchable_picker(self) -> None:
        # a long list turned into «mpick» while short ones stayed pretty-selects: two looks in one toolbar
        js = (STATIC / "memory.js").read_text(encoding="utf-8")
        import re
        selects = re.findall(r"<select\b[^>]*>", js)
        self.assertTrue(selects)
        for tag in selects:
            self.assertIn("data-no-search", tag, tag)


if __name__ == "__main__":
    unittest.main()
