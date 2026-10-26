"""Round 35 (mentions): @-references to everything in the chat composer.

* registry: every built-in provider is there, «@чат:» / «@chat:» category parsing, fuzzy search (ё, wrong keyboard layout);
* local providers on a private data dir: chats (+ message fragments, «#3-7» ranges, stable message ids across branch
  switches), memory facts, scheduled tasks and their results, panels, snippets, notes, Work files, PC files via a
  mocked ``app.fsindex``, URL detection, characters;
* integrations with a mocked ``integrations.run_tool``: Gmail letter / thread, Calendar event / day, Notion page,
  GitHub issue / PR / file, Drive file; not connected → «not_connected» (the window shows «Подключить»);
* resolve: bounded blocks, truncation note, total budget shared, errors per item, prompt block is untrusted data;
* routes (FastAPI TestClient): /api/mentions/providers|search|resolve;
* static wiring in app.js / index.html / build.py / spec, EN strings without Cyrillic;
* end to end in headless Chromium with the offline model mock: «@чат: договор» → chip with a token estimate,
  «@модель:» switches the model for one message, the model request carries the fragment as <mentioned_context>,
  the sent message shows the chips.
* live (keys from env, skipped without them): a real model answers from a mentioned chat fragment only.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-mentions-"))
os.environ.setdefault("MEMUAI_FSINDEX_NOSTART", "1")
for _key in ("NO_PROXY", "no_proxy"):
    os.environ[_key] = "127.0.0.1,localhost"

from app import mentions as M  # noqa: E402

PG = ROOT / "app" / "pgstatic"
NOW = time.time() * 1000

def msg(role, text, k, **extra):
    row = {"role": role, "content": text, "createdAt": NOW - k * 60000, "id": f"m-t{k}"}
    row.update(extra)
    return row

RENT = {"id": "c-rent", "title": "Договор аренды квартиры", "mode": "chat", "updated": NOW - 1000, "messages": [
    msg("user", "Помоги разобрать договор аренды квартиры на Лесной, 12.", 60),
    msg("assistant", "Конечно. Пришлите основные условия.", 59, model="m1"),
    msg("user", "Арендная плата 47 300 рублей в месяц, залог 60 000, арендодатель Сидоров.", 58),
    msg("assistant", "Понял: плата 47 300 ₽, залог 60 000 ₽.", 57, model="m1"),
    {"role": "user", "content": "<local-tool_report>internal</local-tool_report>", "internal": True},
    msg("user", "А коммуналку кто платит?", 56),
    msg("assistant", "Коммунальные по счётчикам платит арендатор.", 55, model="m1"),
]}
TRIP = {"id": "c-trip", "title": "Поездка в Казань", "mode": "chat", "updated": NOW - 5000, "messages": [
    msg("user", "План поездки в Казань", 40), msg("assistant", "Кремль и Баумана.", 39)]}

def seed():
    from app import pgdata, memory_v2
    pgdata.save_chats([RENT, TRIP])
    doc = memory_v2.load()
    if not any("Москве" in str(i.get("text")) for i in doc.get("items") or []):
        memory_v2.add_manual("Пользователь живёт в Москве")
    pgdata.save_schedules([{"id": "t-news", "name": "Утренняя сводка", "prompt": "Собери новости", "daily_at": "08:00",
                            "enabled": True, "created": time.time()}])
    pgdata.append_schedule_result("t-news", True, "ЦБ сохранил ставку 16%")

def run(coro):
    return asyncio.run(coro)

class RegistryTest(unittest.TestCase):
    def test_builtin_providers(self):
        want = {"chat", "memunote", "note", "task", "result", "gmail", "gmail_thread", "calendar", "calendar_day", "notion",
                "github", "drive", "memory", "panel", "snippet", "skill", "work", "pc", "downloads", "url", "clipboard",
                "model", "character", "docs", "inbox"}
        self.assertTrue(want <= set(M.PROVIDERS), want - set(M.PROVIDERS))
        for p in M.PROVIDERS.values():
            self.assertTrue(p.client or (p.search and p.resolve) or p.id == "clipboard", p.id)
            self.assertFalse(re.search(r"[А-Яа-яЁё]", p.label[1] + p.hint[1]), p.id)

    def test_parse_category(self):
        self.assertEqual(M.parse_category("чат: договор аренды"), ("chat", "договор аренды"))
        self.assertEqual(M.parse_category("chat:rent"), ("chat", "rent"))
        self.assertEqual(M.parse_category("письмо: счёт"), ("gmail", "счёт"))
        self.assertEqual(M.parse_category("Модель: qwen")[0], "model")
        self.assertEqual(M.parse_category("https://example.com"), (None, "https://example.com"))
        self.assertEqual(M.parse_category("просто текст"), (None, "просто текст"))

    def test_fuzzy(self):
        self.assertGreater(M.fuzzy("договор", "Договор аренды"), 80)
        self.assertGreater(M.fuzzy("аренды", "Договор аренды"), 60)
        self.assertGreater(M.fuzzy("ljujdjh", "Договор аренды"), 60)
        self.assertGreater(M.fuzzy("елка", "Ёлка"), 80)
        self.assertGreater(M.fuzzy("дгвр", "договор"), 0)
        self.assertEqual(M.fuzzy("zzz", "договор"), 0)

    def test_register_custom_provider(self):
        M.register_provider(M.Provider("x-test", ("Тест", "Test"), ("тест",), search=lambda q, c: [{"ref": 1, "title": "T " + q}],
                                       resolve=lambda r, c: {"title": "T", "text": "body " + str(r)}, order=99))
        try:
            res = run(M.search("тест: abc", {}))
            self.assertEqual(res["items"][0]["title"], "T abc")
            blocks = run(M.resolve_many([{"provider": "x-test", "ref": 5}]))
            self.assertEqual(blocks[0]["text"], "body 5")
        finally:
            M.unregister_provider("x-test")

    def test_parse_day(self):
        from datetime import date
        today = date(2026, 10, 12)
        self.assertEqual(M.parse_day("завтра", today), date(2026, 10, 13))
        self.assertEqual(M.parse_day("пятница", today), date(2026, 10, 16))
        self.assertEqual(M.parse_day("15.10", today), date(2026, 10, 15))
        self.assertEqual(M.parse_day("2026-12-31", today), date(2026, 12, 31))
        self.assertIsNone(M.parse_day("встреча", today))

class LocalProvidersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed()

    def test_chat_search_title_and_fragments(self):
        res = run(M.search("чат: договор", {"lang": "ru"}))
        self.assertEqual(res["category"], "chat")
        refs = [i["ref"] for i in res["items"]]
        self.assertIn({"chat": "c-rent"}, refs)
        frags = [r for r in refs if "from" in r]
        self.assertTrue(frags, res)
        self.assertTrue(all(r["chat"] == "c-rent" and r["from_id"] for r in frags))

    def test_chat_range_syntax(self):
        res = run(M.search("чат: договор #3-4", {"lang": "ru"}))
        whole = next(i for i in res["items"] if i.get("kind") == "chat")
        self.assertEqual((whole["ref"]["from"], whole["ref"]["to"]), (2, 3))
        self.assertIn("#3–4", whole["title"])

    def test_chat_resolve_fragment_skips_internal_and_uses_ids(self):
        block = run(M.resolve_many([{"provider": "chat", "ref": {"chat": "c-rent", "from": 2, "to": 3}}], {"lang": "ru"}))[0]
        self.assertIn("47 300", block["text"])
        self.assertIn("[#3] Пользователь", block["text"])
        self.assertNotIn("коммуналку", block["text"])
        self.assertNotIn("local-tool_report", block["text"])

        ref = {"chat": "c-rent", "from": 0, "to": 0, "from_id": "m-t56", "to_id": "m-t55"}
        block = run(M.resolve_many([{"provider": "chat", "ref": ref}], {"lang": "en"}))[0]
        self.assertIn("коммуналку", block["text"])
        self.assertIn("User", block["text"])
        self.assertNotIn("47 300", block["text"])

    def test_whole_chat_keeps_the_end_when_long(self):
        from app import pgdata
        long_chat = {"id": "c-long", "title": "Длинный", "mode": "chat", "updated": NOW, "messages": [
            msg("user" if i % 2 == 0 else "assistant", f"сообщение {i} " + "слово " * 200, 1000 - i) for i in range(80)]}
        pgdata.save_chats([RENT, TRIP, long_chat])
        try:
            block = run(M.resolve_many([{"provider": "chat", "ref": {"chat": "c-long"}}], item_tokens=2000))[0]
            self.assertTrue(block["truncated"])
            self.assertLessEqual(block["tokens"], 2100)
            self.assertIn("сообщение 79", block["text"])
            self.assertIn("omitted", block["text"])
            self.assertIn("Обрезано", block["note"])
        finally:
            pgdata.save_chats([RENT, TRIP])

    def test_total_budget_is_shared(self):
        items = [{"provider": "chat", "ref": {"chat": "c-rent"}}, {"provider": "chat", "ref": {"chat": "c-trip"}}]
        blocks = run(M.resolve_many(items, item_tokens=6000, total_tokens=800))
        self.assertTrue(all(b["tokens"] <= 450 for b in blocks), [b["tokens"] for b in blocks])

    def test_missing_chat_is_an_error_not_an_exception(self):
        block = run(M.resolve_many([{"provider": "chat", "ref": {"chat": "nope"}}], {"lang": "en"}))[0]
        self.assertIn("not found", block["error"])

    def test_memory_tasks_results(self):
        mem = run(M.search("память: москв", {}))["items"]
        self.assertTrue(mem and "Москве" in mem[0]["title"])
        task = run(M.search("задача: сводк", {}))["items"]
        self.assertEqual(task[0]["ref"], "t-news")
        res = run(M.search("результат:", {}))["items"]
        self.assertTrue(res and "Утренняя сводка" in res[0]["title"])
        blocks = run(M.resolve_many([{"provider": "memory", "ref": mem[0]["ref"]}, {"provider": "task", "ref": "t-news"},
                                     {"provider": "result", "ref": res[0]["ref"]}]))
        self.assertIn("Москве", blocks[0]["text"])
        self.assertIn("Собери новости", blocks[1]["text"])
        self.assertIn("16%", blocks[1]["text"])
        self.assertIn("16%", blocks[2]["text"])

    def test_panels(self):
        from app import panels
        fake = {"id": "p1", "title": "Курс доллара", "kind": "fx", "values": {"rate": "92.4", "change": "+0.3%"},
                "updated_at": time.time()}
        with mock.patch.object(panels, "load", return_value={}), mock.patch.object(panels, "active", return_value=[fake]):
            items = run(M.search("панель: курс", {}))["items"]
            self.assertEqual(items[0]["ref"], "p1")
            block = run(M.resolve_many([{"provider": "panel", "ref": "p1"}]))[0]
        self.assertIn("rate: 92.4", block["text"])

    def test_snippets_and_url(self):
        items = run(M.search("сниппет:", {"lang": "ru"}))["items"]
        self.assertTrue(items)
        block = run(M.resolve_many([{"provider": "snippet", "ref": items[0]["ref"]}]))[0]
        self.assertTrue(block["text"])
        self.assertNotIn("{date}", block["text"])
        url = run(M.search("example.com/page", {}))
        self.assertEqual(url["items"][0]["ref"], "https://example.com/page")
        self.assertTrue(url["remote"])
        bad = run(M.resolve_many([{"provider": "url", "ref": "file:///etc/passwd"}]))[0]
        self.assertIn("error", bad)

    def test_url_fetch_uses_safe_fetcher(self):
        from app import websearch

        async def fake(url, **kw):
            return {"url": url, "title": "Пример", "text": "Текст страницы про аренду"}
        with mock.patch.object(websearch, "fetch_page", side_effect=fake) as spy:
            block = run(M.resolve_many([{"provider": "url", "ref": "https://example.com"}]))[0]
        self.assertTrue(spy.called)
        self.assertIn("аренду", block["text"])

    def test_notes(self):
        notes = Path(os.environ["MEMUAI_DATA_DIR"]) / "notes.md"
        notes.write_text("# Быстрые заметки\n## 2026-10-10\n- 09:15 Купить молоко\n- 18:00 Позвонить Сидорову\n  про залог\n",
                         encoding="utf-8")
        with mock.patch.object(M, "_notes_path", return_value=notes):
            items = run(M.search("заметка: сидоров", {}))["items"]
            self.assertTrue(items)
            block = run(M.resolve_many([{"provider": "note", "ref": items[0]["ref"]}]))[0]
        self.assertIn("про залог", block["text"])

    def test_pc_files_via_fsindex_mock_and_seen_rule(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "отчёт.txt"
        f.write_text("Итог квартала: 1 234 567", encoding="utf-8")
        fake = mock.Mock()
        fake.search.return_value = [{"path": str(f), "name": f.name, "dir": str(tmp), "size": 40, "mtime": time.time(), "is_dir": False}]
        with mock.patch.object(M, "_fsindex", return_value=fake):
            items = run(M.search("пк: отчёт", {}))["items"]
        self.assertEqual(items[0]["ref"], str(f))
        block = run(M.resolve_many([{"provider": "pc", "ref": str(f)}]))[0]
        self.assertIn("1 234 567", block["text"])

        other = tmp / "secret.txt"
        other.write_text("x", encoding="utf-8")
        self.assertIn("error", run(M.resolve_many([{"provider": "pc", "ref": str(other)}]))[0])

    def test_docx_reader(self):
        import zipfile
        tmp = Path(tempfile.mkdtemp()) / "d.docx"
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Пункт 1</w:t></w:r></w:p><w:p><w:t>Пункт 2</w:t></w:p></w:document>")
        self.assertEqual(M.read_any_file(tmp, {}).split(), ["Пункт", "1", "Пункт", "2"])

    def test_work_files(self):
        from app import pgdata
        root = Path(tempfile.mkdtemp())
        (root / "src").mkdir()
        (root / "src" / "rent.py").write_text("RENT = 47300\n", encoding="utf-8")
        pgdata.save_workspaces({"active_id": "", "items": [{"id": "w1", "name": "Проект", "path": str(root)}]})
        block = run(M.resolve_many([{"provider": "work", "ref": {"project": "w1", "path": "src/rent.py"}}]))[0]
        self.assertIn("47300", block["text"])
        bad = run(M.resolve_many([{"provider": "work", "ref": {"project": "w1", "path": "../../etc/passwd"}}]))[0]
        self.assertIn("error", bad)

    def test_prompt_block_is_untrusted(self):
        blocks = run(M.resolve_many([{"provider": "chat", "ref": {"chat": "c-rent", "from": 2, "to": 2}}]))
        text = M.prompt_block(blocks + [{"provider": "x", "error": "boom"}])
        self.assertIn("<mentioned_context>", text)
        self.assertIn("untrusted", text)
        self.assertIn('type="chat"', text)
        self.assertNotIn("boom", text)
        self.assertEqual(M.prompt_block([]), "")

class IntegrationsMockTest(unittest.TestCase):
    """Gmail / Calendar / Notion / GitHub / Drive through a mocked integrations.run_tool."""

    def setUp(self):
        self.calls = []
        M._CONN_CACHE.update(at=time.time(), map={"gmail": True, "google_calendar": True, "notion": True, "github": True,
                                                  "google_drive": True})

    def tearDown(self):
        M._CONN_CACHE.update(at=0.0, map={})

    def fake_tool(self, name, args):
        self.calls.append((name, args))
        table = {
            "gmail_search": {"messages": [{"id": "g1", "thread_id": "th1", "subject": "Счёт за аренду", "from": "Сидоров <s@x.ru>", "date": "Mon, 12 Oct 2026"}]},
            "gmail_read": {"subject": "Счёт за аренду", "from": "Сидоров", "to": "me", "text": "Оплатите 47 300 до 5-го", "attachments": [{"filename": "счёт.pdf"}]},
            "gmail_read_thread": {"messages": [{"from": "A", "date": "1", "subject": "Тема", "text": "раз"}, {"from": "B", "date": "2", "text": "два"}]},
            "calendar_list_events": {"events": [{"id": "e1", "summary": "Встреча с арендодателем", "start": "2026-10-13T10:00:00"}]},
            "calendar_get_event": {"summary": "Встреча с арендодателем", "start": "2026-10-13T10:00", "location": "Лесная 12", "link": "https://cal/e1"},
            "notion_search": {"results": [{"id": "n1", "title": "Аренда — заметки", "url": "https://notion.so/n1"}]},
            "notion_read_page": {"title": "Аренда — заметки", "text": "Залог вернуть в июле", "properties": {"Статус": "Открыто"}},
            "github_search_issues": {"items": [{"repo": "me/memu", "number": 7, "title": "Bug in rent", "state": "open", "pull_request": False}]},
            "github_get_issue": {"number": 7, "title": "Bug in rent", "state": "open", "body": "Steps…", "labels": ["bug"], "comments_list": [{"user": "a", "body": "+1"}]},
            "github_get_pull": {"number": 8, "title": "Fix", "state": "open", "body": "PR body", "files": [{"filename": "a.py", "additions": 1, "deletions": 0, "patch": "+x"}]},
            "github_read_file": {"text": "print('hi')"},
            "drive_list_files": {"files": [{"id": "d1", "name": "Договор.docx", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-10-01T00:00:00Z"}]},
            "drive_read_file": {"name": "Договор.docx", "text": "Текст договора", "truncated": False},
        }
        return table[name]

    def test_all_integrations(self):
        with mock.patch.object(M, "_tool", side_effect=self.fake_tool):
            gm = run(M.search("gmail: счёт", {}))["items"]
            self.assertEqual(gm[0]["ref"]["id"], "g1")
            self.assertTrue(any(i.get("provider") == "gmail_thread" for i in gm))
            cal = run(M.search("календарь: встреча", {}))["items"]
            self.assertEqual(cal[-1]["ref"]["id"], "e1")
            day = run(M.search("календарь: завтра", {}))["items"]
            self.assertEqual(day[0]["provider"], "calendar_day")
            no = run(M.search("notion: аренда", {}))["items"]
            gh = run(M.search("github: rent", {}))["items"]
            self.assertIn("involves:@me", self.calls[-1][1]["query"])
            ghf = run(M.search("github: me/memu:src/a.py", {}))["items"]
            self.assertEqual(ghf[0]["ref"], {"repo": "me/memu", "path": "src/a.py"})
            dr = run(M.search("диск: договор", {}))["items"]
            blocks = run(M.resolve_many([
                {"provider": "gmail", "ref": gm[0]["ref"]},
                {"provider": "gmail_thread", "ref": "th1"},
                {"provider": "calendar", "ref": cal[-1]["ref"]},
                {"provider": "calendar_day", "ref": day[0]["ref"]},
                {"provider": "notion", "ref": no[0]["ref"]},
                {"provider": "github", "ref": gh[0]["ref"]},
                {"provider": "github", "ref": {"repo": "me/memu", "number": 8, "pr": True}},
                {"provider": "github", "ref": ghf[0]["ref"]},
                {"provider": "drive", "ref": dr[0]["ref"]},
            ], {"lang": "ru"}))
        texts = [b.get("text", "") for b in blocks]
        self.assertTrue(all(not b.get("error") for b in blocks), [b.get("error") for b in blocks])
        self.assertIn("47 300", texts[0]); self.assertIn("счёт.pdf", texts[0])
        self.assertIn("два", texts[1])
        self.assertIn("Лесная 12", texts[2]); self.assertEqual(blocks[2]["url"], "https://cal/e1")
        self.assertIn("Встреча", texts[3])
        self.assertIn("Залог", texts[4]); self.assertIn("Статус", texts[4])
        self.assertIn("+1", texts[5])
        self.assertIn("a.py", texts[6])
        self.assertIn("print", texts[7])
        self.assertIn("Текст договора", texts[8])

    def test_not_connected(self):
        M._CONN_CACHE.update(at=time.time(), map={"gmail": False})
        res = run(M.search("gmail: счёт", {}))
        self.assertEqual(res["items"], [])
        self.assertFalse(res["not_connected"]["connected"])
        self.assertEqual(res["not_connected"]["service"], "gmail")
        block = run(M.resolve_many([{"provider": "gmail", "ref": {"id": "g1"}}], {"lang": "en"}))[0]
        self.assertIn("not connected", block["error"])
        rows = run(M.search("счёт", {}))["remote"]
        gmail = next(r for r in rows if r["provider"] == "gmail")
        self.assertTrue(gmail["disabled"])
        self.assertEqual(gmail["connect"], "gmail")

    def test_integration_error_is_shown_per_item(self):
        def boom(name, args):
            raise RuntimeError("Gmail: токен отозван")
        with mock.patch.object(M, "_tool", side_effect=boom):
            res = run(M.search("gmail: x", {}))
            self.assertTrue(res["items"][0]["error"])
            block = run(M.resolve_many([{"provider": "gmail", "ref": {"id": "g1"}}]))[0]
        self.assertIn("токен", block["error"])

class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        M.register(app)
        cls.client = TestClient(app)

    def test_routes(self):
        prov = self.client.get("/api/mentions/providers").json()
        self.assertIn("chat", [p["id"] for p in prov["providers"]])
        self.assertEqual(prov["limits"]["max_items"], M.MAX_ITEMS)
        res = self.client.post("/api/mentions/search", json={"q": "чат: казань", "lang": "ru"}).json()
        self.assertEqual(res["items"][0]["ref"]["chat"], "c-trip")
        empty = self.client.post("/api/mentions/search", json={"q": ""}).json()
        self.assertIn("items", empty)
        out = self.client.post("/api/mentions/resolve", json={"items": [{"provider": "chat", "ref": {"chat": "c-trip"}}]}).json()
        self.assertIn("Кремль", out["blocks"][0]["text"])
        self.assertGreater(out["tokens"], 0)
        bad = self.client.post("/api/mentions/resolve", content=b"not json", headers={"content-type": "application/json"}).json()
        self.assertEqual(bad["blocks"], [])

class WiringTest(unittest.TestCase):
    def test_static_wiring(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        self.assertIn('/mentions.js', html)
        self.assertIn('/mentions.css', html)
        self.assertLess(html.index('/app.js'), html.index('/mentions.js'))
        app = (PG / "app.js").read_text(encoding="utf-8")
        for hook in ("MemuMentions?.owns()", "MemuMentions.prepare()", "MemuMentions?.clear()", "MemuMentions.messageChips(m, i)",
                     "MemuMentions.promptText(m)", "MemuMentions.tokensOf(message)", "function legacyMentionRows(", "userMsg.mentions = "):
            self.assertIn(hook, app, hook)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("mentions.register(app)", api)
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"app.mentions"', build)
        self.assertIn("'app.mentions'", (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))
        self.assertIn("'app.mentions'", (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8"))
        self.assertIn("_mention_block(row[\"mentions\"])", (ROOT / "app" / "telegram_bot.py").read_text(encoding="utf-8"))

    def test_english_strings_have_no_cyrillic(self):
        js = (PG / "mentions.js").read_text(encoding="utf-8")
        for m in re.finditer(r'L\((`[^`]*`|"[^"]*"),\s*(`[^`]*`|"[^"]*")\)', js):
            self.assertFalse(re.search(r"[А-Яа-яЁё]", m.group(2)), m.group(0))
        py = (ROOT / "app" / "mentions.py").read_text(encoding="utf-8")
        for m in re.finditer(r'_t\(ctx,\s*(f?"[^"]*"),\s*(f?"[^"]*")\)', py):
            self.assertFalse(re.search(r"[А-Яа-яЁё]", m.group(2)), m.group(0))

    def test_js_syntax(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        out = subprocess.run([node, "--check", str(PG / "mentions.js")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

def _chromium():
    for path in ("/usr/bin/chromium", "/workspace/work/chromium/chrome"):
        if os.path.exists(path):
            return path
    return None

@unittest.skipUnless(_chromium(), "chromium is not available")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        from tests.ui_lang_scan import Servers
        from app import pgdata
        cls.servers = Servers().start()
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        pgdata.save_chats([RENT, TRIP])
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)
        for _ in range(300):
            if cls.page.evaluate("typeof S !== 'undefined' && !!S.models && S.models.length > 1"):
                break
            cls.page.wait_for_timeout(200)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    def js(self, code):
        return self.page.evaluate(code)

    def wait(self, cond, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(cond):
                return
            self.page.wait_for_timeout(120)
        raise AssertionError("timeout: " + cond)

    def test_mention_flow(self):
        from tests import mock_custom

        class Recorder(dict):
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if key == "last_body":
                    self.setdefault("all", []).append(value)
        mock_custom.state = Recorder(mock_custom.state)

        def answer_body():
            rows = [b for b in mock_custom.state.get("all", []) if "<user_message>" not in json.dumps(b, ensure_ascii=False)]
            return rows[-1]
        page = self.page
        models = self.js("S.models.map((m) => m.id)")
        gpt = next(m for m in models if "gpt-test" in m)
        claude = next(m for m in models if "claude-test" in m)
        self.js(f"setCurrentModel({json.dumps(gpt)}); newChat(); closePanel && closePanel();")
        page.click("#input")
        page.keyboard.type("Сколько я плачу за квартиру? @")
        self.wait("!document.querySelector('#mention-menu').hidden && !!document.querySelector('#mention-menu .mn-cats')")
        page.keyboard.type("чат: договор")
        self.wait("[...document.querySelectorAll('#mention-menu .mn-row')].some((b) => /Договор аренды/.test(b.textContent))")

        page.keyboard.press("Enter")
        self.wait("MemuMentions._state.chips.length === 1 && MemuMentions._state.chips[0].status === 'ok'")
        self.assertNotIn("@чат", self.js("document.querySelector('#input').value"))
        self.assertRegex(self.js("document.querySelector('#mention-chips .mn-chip small').textContent"), r"^~\d")

        page.click("#mention-chips [data-mn-chip='0']")
        page.fill("#mn-from", "3"); page.fill("#mn-to", "4")
        page.click("#mn-range-apply")
        self.wait("MemuMentions._state.chips[0].status === 'ok' && MemuMentions._state.chips[0].ref.from === 2")
        self.js("closePanel()")

        page.click("#input")
        page.keyboard.press("End")
        page.keyboard.type(" @модель: claude")
        self.wait("[...document.querySelectorAll('#mention-menu .mn-row')].some((b) => /claude/i.test(b.textContent))")
        page.keyboard.press("Enter")
        self.wait("MemuMentions._state.chips.length === 2")
        self.js("send()")
        self.wait("S.messages.length === 2 && !S.messages[1].streaming", 40)
        body = answer_body()
        self.assertEqual(body["model"], "claude-test")
        sent = json.dumps(body["messages"], ensure_ascii=False)
        self.assertIn("<mentioned_context>", sent)
        self.assertIn("47 300", sent)
        self.assertNotIn("коммуналку", sent)
        self.assertEqual(self.js("S.messages[0].mentions.length"), 1)
        self.assertEqual(self.js("S.messages[0].mentionModel"), claude)
        self.assertEqual(self.js("currentModel()"), gpt)
        self.assertEqual(self.js("document.querySelectorAll('.msg-user .mn-msg-chips .mn-chip').length"), 2)
        self.assertEqual(self.js("MemuMentions._state.chips.length"), 0)

        page.fill("#input", "А залог?")
        self.js("send()")
        self.wait("S.messages.length === 4 && !S.messages[3].streaming", 40)
        self.assertIn("47 300", json.dumps(answer_body()["messages"], ensure_ascii=False))
        self.assertEqual(answer_body()["model"], "gpt-test")

        page.click("#input")
        page.keyboard.type(" @gmail: счёт")
        self.wait("!!document.querySelector('#mention-menu [data-mn-connect]')")
        page.keyboard.press("Escape")
        self.wait("document.querySelector('#mention-menu').hidden")
        self.assertEqual(self.errors, [])

def _live_env():
    keys = Path("/workspace/work/secrets/test_keys.env")
    env = dict(os.environ)
    if keys.exists() and not env.get("REFORM_KEY"):
        for line in keys.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env

class QuickAccessTest(unittest.TestCase):
    """r35-finish-b: @-mentions in the Quick Access composer (quick/plugins/mentions.js → mentions.quick_context)."""

    def setUp(self):
        self.calls = []

        def resolve(ref, ctx):
            self.calls.append(ref)
            return {"title": "Fake " + str(ref), "source": "Fake", "text": "FAKE-TEXT " + str(ref) * 3}
        M.register_provider(M.Provider("fakeq", ("Фейк", "Fake"), ("фейк",), resolve=resolve, order=99))

    def tearDown(self):
        M.unregister_provider("fakeq")

    def test_quick_context_uses_picked_text_resolves_the_rest_and_marks_earlier(self):
        body = {"contexts": [{"type": "bigdoc", "id": "x"}, {"type": "mentions",
                "items": [{"provider": "chat", "ref": {"chat": "c-rent"}, "title": "Договор", "source": "Чат",
                           "text": "Арендная плата 47 300 рублей"},
                          {"provider": "fakeq", "ref": "r1", "title": "f"},
                          {"provider": "model", "ref": "m", "title": "model is not a context"},
                          {"provider": "nope", "ref": "x", "text": "unknown provider"}],
                "earlier": [{"provider": "memory", "ref": "m1", "title": "Москва", "text": "Пользователь живёт в Москве"},
                            {"provider": "chat", "ref": {"chat": "c-rent"}, "title": "dup", "text": "dup of a current one"}]}]}
        block = run(M.quick_context(body, "ru"))
        self.assertIn("<mentioned_context>", block)
        self.assertIn("untrusted", block)
        self.assertIn("47 300", block)
        self.assertIn("FAKE-TEXT r1", block)
        self.assertEqual(self.calls, ["r1"])
        self.assertIn('earlier="true"', block)
        self.assertIn("Москве", block)
        self.assertNotIn("dup of a current one", block)
        self.assertNotIn("model is not a context", block)
        self.assertNotIn("unknown provider", block)
        self.assertEqual(block.count("<mention "), 3)

    def test_quick_context_bounds_and_empty(self):
        self.assertEqual(run(M.quick_context({}, "ru")), "")
        self.assertEqual(run(M.quick_context({"contexts": [{"type": "mentions", "items": []}]}, "ru")), "")
        huge = "слово " * 60000
        block = run(M.quick_context({"contexts": [{"type": "mentions", "items": [
            {"provider": "fakeq", "ref": "a", "title": "big", "text": huge}]}]}, "en"))
        self.assertIn('truncated="true"', block)
        self.assertLess(M.estimate_tokens(block), M.ITEM_TOKENS + 400)

    def test_quick_ask_route_sends_mentions_to_the_model(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import quick_ask_api
        sent = []

        async def stream_lines(path, payload):
            sent.append(json.loads(payload.decode("utf-8")))
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

        async def nothing(*_a, **_k):
            return {}
        app = FastAPI()
        quick_ask_api.register(app, stream_lines=stream_lines, run_tool=nothing, rich_search=nothing, rich_payload=lambda p: p)
        client = TestClient(app)
        r = client.post("/api/quick/ask", json={
            "text": "Сколько стоит аренда?", "model": "m1", "lang": "ru", "web": False, "tier": "fast",
            "contexts": [{"type": "mentions", "items": [{"provider": "chat", "ref": {"chat": "c-rent"}, "title": "Договор аренды",
                                                        "source": "Чат", "text": "Арендная плата 47 300 рублей"}],
                          "earlier": [{"provider": "memory", "ref": "m1", "title": "Москва", "text": "Пользователь живёт в Москве"}]}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(sent, r.text[:500])
        everything = json.dumps(sent[0]["messages"], ensure_ascii=False)
        self.assertIn("<mentioned_context>", everything)
        self.assertIn("47 300", everything)
        self.assertIn('earlier=\\"true\\"', everything)

    def test_quick_plugin_wired_and_parses(self):
        index = json.loads((PG / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
        row = next(p for p in index["plugins"] if p["id"] == "mentions")
        self.assertEqual((row["src"], row["css"]), ("mentions.js", "mentions.css"))
        js = (PG / "quick" / "plugins" / "mentions.js").read_text(encoding="utf-8")
        for needle in ('addRequestContext("mentions"', "/api/mentions/search", "/api/mentions/resolve", '"answer-start"', '"chat-new"'):
            self.assertIn(needle, js)
        for m in re.finditer(r'\bt\((`[^`]*`|"[^"]*"),\s*(`[^`]*`|"[^"]*")\)', js):
            self.assertFalse(re.search(r"[А-Яа-яЁё]", m.group(2)), m.group(0))
        api = (ROOT / "app" / "quick_ask_api.py").read_text(encoding="utf-8")
        self.assertIn("_mentions.quick_context(body, lang)", api)
        import shutil
        import subprocess
        node = shutil.which("node")
        if node:
            out = subprocess.run([node, "--check", str(PG / "quick" / "plugins" / "mentions.js")], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

@unittest.skipUnless(os.environ.get("MEMUAI_LIVE") == "1", "live test: set MEMUAI_LIVE=1 (keys from env)")
class LiveTest(unittest.TestCase):
    def test_model_uses_mentioned_fragment(self):
        import httpx
        env = _live_env()
        base, key, model = env.get("REFORM_BASE"), env.get("REFORM_KEY"), "qwen3.8-flash"
        if not (base and key):
            self.skipTest("no REFORM keys")
        seed()
        blocks = run(M.resolve_many([{"provider": "chat", "ref": {"chat": "c-rent", "from": 2, "to": 3}}]))
        messages = [{"role": "system", "content": "Answer briefly in Russian."},
                    {"role": "user", "content": "Сколько я плачу за квартиру и кто арендодатель?" + "\n\n" + M.prompt_block(blocks)}]
        r = httpx.post(base.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + key},
                       json={"model": model, "messages": messages, "temperature": 0}, timeout=90)
        text = r.json()["choices"][0]["message"]["content"]
        self.assertRegex(text.replace("\u00a0", " "), r"47[\s ]?300")
        self.assertIn("Сидоров", text)

if __name__ == "__main__":
    unittest.main()
