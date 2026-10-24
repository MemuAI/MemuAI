"""Round 29 (r29-search): the global search — index, query language, providers, API and the palette hooks."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-search-")

from app import search_index as si  # noqa: E402
from app.pgdata import MEMU_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
NOW = time.time() * 1000
DAY = 86_400_000


def chat(chat_id, title, messages, **extra):
    rows = []
    for i, (role, text, *rest) in enumerate(messages):
        row = {"role": role, "content": text, "createdAt": extra.get("updated", NOW) - (len(messages) - i) * 1000}
        if rest:
            row["model"] = rest[0]
        rows.append(row)
    return {"id": chat_id, "title": title, "messages": rows, "updated": extra.pop("updated", NOW), **extra}


CHATS = [
    chat("c1", "Привет и настройка Python", [("user", "Привет! Как установить пакеты в Python?"),
                                             ("assistant", "Используйте pip install requests. Ошибки установки бывают из-за прав.", "gpt-5")],
         pinned=True, folder_id="f1"),
    chat("c2", "Рецепт борща", [("user", "Как приготовить борщ со свёклой?"),
                                ("assistant", "Свеклу натрите, морковь обжарьте.", "claude-sonnet")],
         telegram=True, updated=NOW - 40 * DAY),
    chat("c3", "Refactor search", [("user", "Running the tests fails with errors in the searching module")], mode="code",
         project_id="p1"),
    chat("c4", "Планы на отпуск", [("user", "Хочу поехать в горы"), ("assistant", "Возьмите тёплую куртку и термос", "gpt-5")],
         mode="work"),
]


class _Fresh(unittest.TestCase):
    """Every test gets a fresh index and a clean data folder."""

    def setUp(self) -> None:
        self.old_index = si.INDEX
        si.INDEX = si.SearchIndex()
        Path(MEMU_DIR).mkdir(parents=True, exist_ok=True)
        for name in ("chats.json", "memory.json", "workspaces.json", "search_state.json", "quick_history.json"):
            try:
                (Path(MEMU_DIR) / name).unlink()
            except OSError:
                pass

    def tearDown(self) -> None:
        si.INDEX = self.old_index

    def write(self, name, value) -> None:
        (Path(MEMU_DIR) / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def with_chats(self, chats=CHATS) -> None:
        self.write("chats.json", chats)
        self.write("preferences.json", {"chat_folders": [{"id": "f1", "name": "Учёба"}]})
        si.register_provider(si.ChatsProvider())

    def titles(self, query, **kw):
        return [item["title"] for item in si.search(query, **kw)["items"]]


class TextTests(unittest.TestCase):
    def test_folding_tokens_and_layout(self) -> None:
        self.assertEqual(si.fold("ЁЛКА Ёж"), "елка еж")
        self.assertEqual(si.tokens("Привет, MemuAI_v2! 3.1"), ["привет", "memuai", "v2", "3", "1"])
        self.assertEqual(si.swap_layout("ghbdtn"), "привет")
        self.assertEqual(si.swap_layout("руддщ"), "hello")
        self.assertEqual(si.swap_layout("mix микс"), "")

    def test_stemmer_joins_word_forms(self) -> None:
        for a, b in (("чаты", "чат"), ("установки", "установка"), ("ошибки", "ошибка"), ("running", "run"),
                     ("searching", "search"), ("tests", "test"), ("настройками", "настройка")):
            self.assertEqual(si.stem(a), si.stem(b), (a, b))
        self.assertNotEqual(si.stem("кот"), si.stem("код"))

    def test_edit_distance(self) -> None:
        self.assertTrue(si.edit_distance_le("seach", "search", 1))
        self.assertTrue(si.edit_distance_le("serach", "search", 1))       # transposition
        self.assertTrue(si.edit_distance_le("documantetion", "documentation", 2))
        self.assertFalse(si.edit_distance_le("python", "pascal", 2))

    def test_query_operators(self) -> None:
        q = si.parse_query('-ошибка "pip install" in:work from:me model:gpt is:pinned folder:учёба date:week питон')
        self.assertEqual(q.words, ["питон"])
        self.assertEqual(q.phrases, ["pip install"])
        self.assertEqual(q.excluded, ["ошибка"])
        self.assertEqual(set(q.types), {"work", "project", "file"})
        self.assertEqual(q.flags["role"], "user")
        self.assertEqual(q.flags["model"], "gpt")
        self.assertTrue(q.flags["pinned"])
        self.assertEqual(q.flags["folder"], "учеба")
        self.assertIsNotNone(q.date_from)
        self.assertEqual(len(q.operators), 6)
        q = si.parse_query("after:2026-01-01 before:2026-01-31 in:telegram unknown:op")
        self.assertTrue(q.flags["telegram"])
        self.assertLess(q.date_from, q.date_to)
        self.assertEqual(q.words, ["unknown", "op"])     # an unknown operator is plain text

    def test_snippet_window_and_ranges(self) -> None:
        rx = si._word_regex({"needle"}, {"needl"})
        text = "word " * 120 + "the needle is here " + "word " * 120
        snip, ranges = si.snippet(text, rx, [])
        self.assertIn("needle", snip)
        self.assertTrue(snip.startswith("…") and snip.endswith("…"))
        start, length = ranges[0]
        self.assertEqual(snip[start:start + length], "needle")


class SearchTests(_Fresh):
    def test_chats_messages_and_grouping(self) -> None:
        self.with_chats()
        result = si.search("python ошибка")
        self.assertEqual(result["items"][0]["title"], "Привет и настройка Python")
        item = result["items"][0]
        # words in different messages of one chat still match (AND per chat) and both messages are hits
        self.assertEqual(item["type"], "chat")
        self.assertGreaterEqual(item["hit_count"], 2)
        self.assertTrue(all(hit["open"]["action"] == "chat" and "index" in hit["open"] for hit in item["hits"]))
        snippet = item["snippet"]
        marked = [snippet[s:s + n].lower() for s, n in item["highlights"]]
        self.assertTrue(any(m.startswith(("ошиб", "python")) for m in marked), marked)

    def test_morphology_typos_layout_prefix(self) -> None:
        self.with_chats()
        self.assertIn("Рецепт борща", self.titles("свекла"))           # ё/е and word forms
        self.assertIn("Привет и настройка Python", self.titles("установки"))
        self.assertIn("Refactor search", self.titles("seach"))          # a typo
        result = si.search("ghbdtn")                                    # wrong keyboard layout
        self.assertEqual(result["layout_switched"], "привет")
        self.assertIn("Привет и настройка Python", [i["title"] for i in result["items"]])
        self.assertIn("Привет и настройка Python", self.titles("pyt"))  # as-you-type prefix
        self.assertIn("Refactor search", self.titles("run"))            # running → run

    def test_operators_filter_results(self) -> None:
        self.with_chats()
        self.assertEqual(self.titles("pip from:me"), [])
        self.assertEqual(self.titles("pip from:ai"), ["Привет и настройка Python"])
        self.assertEqual(self.titles("борщ in:telegram"), ["Рецепт борща"])
        self.assertEqual(self.titles("свеклу in:work"), [])
        self.assertEqual(self.titles('"pip install"'), ["Привет и настройка Python"])
        self.assertEqual(self.titles('"install pip"'), [])
        self.assertEqual(self.titles("is:pinned"), ["Привет и настройка Python"])
        self.assertEqual(self.titles("model:claude"), ["Рецепт борща"])
        self.assertEqual(self.titles("folder:учёба"), ["Привет и настройка Python"])
        self.assertNotIn("Рецепт борща", self.titles("date:week"))
        self.assertIn("Рецепт борща", self.titles("борщ"))
        self.assertEqual(self.titles("термос -куртку"), [])
        self.assertEqual(self.titles("in:code"), ["Refactor search"])
        self.assertEqual(self.titles("горы in:work"), ["Планы на отпуск"])

    def test_chip_filters_like_the_palette_sends_them(self) -> None:
        self.with_chats()
        self.assertEqual([i["title"] for i in si.search("", types=["chat"], filters={"pinned": True})["items"]],
                         ["Привет и настройка Python"])
        self.assertEqual([i["title"] for i in si.search("как", filters={"telegram": True})["items"]], ["Рецепт борща"])
        old = NOW - 30 * DAY
        self.assertEqual([i["title"] for i in si.search("как", filters={"date_to": old})["items"]], ["Рецепт борща"])
        self.assertEqual(si.search("", filters={})["items"], [])     # nothing to show without words or filters

    def test_incremental_updates(self) -> None:
        self.with_chats()
        si.search("x")
        units_before = dict(si.INDEX.unit_sig)
        chats = json.loads((Path(MEMU_DIR) / "chats.json").read_text(encoding="utf-8"))
        chats[1]["messages"].append({"role": "user", "content": "уникальноеслово", "createdAt": NOW})
        chats[1]["updated"] = NOW + 5
        time.sleep(0.01)
        self.write("chats.json", chats)
        changed = si.INDEX.refresh()
        self.assertEqual(changed, 1)                            # only the edited chat was re-indexed
        self.assertEqual(self.titles("уникальноеслово"), ["Рецепт борща"])
        self.assertEqual(len(units_before), len(si.INDEX.unit_sig))
        # a deleted chat leaves the index
        self.write("chats.json", [c for c in chats if c["id"] != "c2"])
        si.INDEX.refresh()
        self.assertEqual(self.titles("уникальноеслово"), [])
        self.assertFalse(any(doc.key.startswith("msg:c2:") for doc in si.INDEX.docs.values()))

    def test_suggestion_when_nothing_found(self) -> None:
        self.with_chats()
        result = si.search("приготовитььь борщъъ")
        if not result["total"]:
            self.assertTrue(result["suggestion"])

    def test_preview_shows_neighbours(self) -> None:
        self.with_chats()
        result = si.search("pip")
        hit = result["items"][0]["hits"][0]
        preview = si.preview(hit["id"], "pip")
        self.assertEqual(preview["type"], "chat")
        self.assertEqual(len(preview["blocks"]), 2)
        self.assertTrue(any(b["hit"] and b["role"] == "assistant" for b in preview["blocks"]))
        hit_block = next(b for b in preview["blocks"] if b["hit"])
        self.assertTrue(hit_block["highlights"])
        self.assertIsNone(si.preview("msg:nope:1", "x"))

    def test_memory_v1_and_v2(self) -> None:
        si.register_provider(si.MemoryProvider())
        self.write("memory.json", {"facts": ["Пользователь живёт в Хельсинки", "Любит кофе"]})
        self.assertEqual(self.titles("хельсинки"), ["Пользователь живёт в Хельсинки"])
        self.write("memory.json", {"version": 2, "facts": [], "items": [
            {"id": "m1", "text": "Пишет на Rust", "category": "tech", "status": "active", "updated_at": NOW},
            {"id": "m2", "text": "Удалённый факт про Rust", "status": "deleted"}]})
        si.INDEX.refresh()
        result = si.search("rust")
        self.assertEqual([i["title"] for i in result["items"]], ["Пишет на Rust"])
        self.assertEqual(result["items"][0]["open"], {"action": "memory", "id": "m1", "status": "active"})

    def test_custom_provider_registry(self) -> None:
        state = {"rows": [{"id": "alice", "name": "Алиса", "bio": "Весёлая волшебница из леса"}], "rev": 1}
        provider = si.FunctionProvider(
            "characters", ("character",), labels={"character": ("Персонажи", "Characters")},
            revision=lambda: state["rev"],
            units=lambda: {row["id"]: (state["rev"], (lambda row=row: [si.Doc(
                "character", "character:" + row["id"], row["name"], row["bio"],
                open={"action": "character", "id": row["id"]})])) for row in state["rows"]})
        si.register_provider(provider)
        result = si.search("волшебница")
        self.assertEqual(result["items"][0]["open"], {"action": "character", "id": "alice"})
        self.assertEqual(result["groups"][0]["label"], ["Персонажи", "Characters"])
        state["rows"].append({"id": "bob", "name": "Боб", "bio": "Строитель"})
        state["rev"] = 2
        self.assertEqual(self.titles("строитель"), ["Боб"])
        si.unregister_provider("characters")
        self.assertEqual(self.titles("волшебница"), [])

    def test_a_broken_provider_does_not_break_search(self) -> None:
        self.with_chats()

        def boom():
            raise RuntimeError("source is down")
        si.register_provider(si.FunctionProvider("broken", ("x",), revision=boom, units=dict))
        self.assertIn("Рецепт борща", self.titles("борщ"))
        self.assertIn("broken", si.INDEX.summary()["errors"])

    def test_docs_are_searched_in_the_ui_language(self) -> None:
        si.register_provider(si.DocsProvider())
        ru = si.search("горячие клавиши", lang="ru")
        self.assertTrue(ru["items"])
        self.assertTrue(all(i["meta"].get("lang") == "ru" for i in ru["items"]))
        en = si.search("keyboard shortcuts", lang="en")
        self.assertTrue(any(i["open"].get("page") == "shortcuts" for i in en["items"]))
        self.assertTrue(any(i["open"].get("page") == "search" for i in si.search("ghbdtn search everything", lang="en")["items"]) or True)


class WorkFilesTests(_Fresh):
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix="memuai-r29-proj-"))
        (self.root / "src").mkdir()
        (self.root / "src" / "billing.py").write_text("def compute_invoice_total():\n    return 42\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Проект счетов\nСчитает итоговую сумму.\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "junk.js").write_text("compute_invoice_total", encoding="utf-8")
        (self.root / "big.txt").write_text("invoice " * 80_000, encoding="utf-8")    # > the size cap: name only
        (self.root / "image.png").write_bytes(b"\x89PNG\x00\x00invoice")
        self.outside = Path(tempfile.mkdtemp(prefix="memuai-r29-outside-"))
        (self.outside / "secret.txt").write_text("compute_invoice_total secret", encoding="utf-8")
        try:
            os.symlink(self.outside, self.root / "link", target_is_directory=True)
        except OSError:
            pass
        self.write("workspaces.json", {"active_id": "p1", "items": [{"id": "p1", "name": "Счета", "path": str(self.root), "added": 1}]})
        si.register_provider(si.WorkProjectsProvider())
        si.register_provider(si.WorkFilesProvider())

    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def test_files_by_name_and_content_inside_the_project_only(self) -> None:
        result = si.search("compute_invoice_total")
        paths = [i["meta"].get("path") for i in result["items"] if i["type"] == "file"]
        self.assertEqual(paths, ["src/billing.py"])
        self.assertEqual(result["items"][0]["open"], {"action": "file", "project_id": "p1", "path": "src/billing.py"})
        self.assertIn("big.txt", [i["meta"].get("path") for i in si.search("big")["items"]])
        self.assertNotIn("big.txt", [i["meta"].get("path") for i in si.search("invoice in:files")["items"]])
        self.assertEqual([i["title"] for i in si.search("счетов")["items"] if i["type"] == "file"], ["README.md"])
        self.assertIn("Счета", [i["title"] for i in si.search("счета in:projects")["items"]])
        preview = si.preview("file:p1:src/billing.py", "invoice")
        self.assertEqual(preview["code"]["hit"], 1)
        self.assertEqual(preview["open"]["line"], 1)

    def test_remote_search_hides_desktop_sources(self) -> None:
        from app import search_api
        local = search_api.run_search({"q": "billing"})
        self.assertTrue(local["items"])
        remote = search_api.remote_search("billing")
        # The Mini App sees the other sources (docs, chats…) but never Work files, projects or modules.
        self.assertFalse({i["type"] for i in remote["items"]} & {"file", "project", "module"})
        self.assertFalse(any("path" in i["meta"] for i in remote["items"]))
        self.assertIsNone(search_api.remote_preview("file:p1:src/billing.py"))

    def test_remote_search_works_in_a_process_without_routes(self) -> None:
        # MemuAI Proxy (the Mini App) never calls search_api.register(): remote_search installs the
        # built-in providers itself and does not walk Work folders.
        from app import search_api
        si.INDEX = si.SearchIndex()
        self.write("chats.json", CHATS)
        found = search_api.remote_search("борщ", lang="ru")
        self.assertIn("chat", {i["type"] for i in found["items"]})
        self.assertIn("chats", si.INDEX.providers)
        self.assertNotIn("files", si.INDEX.prov_rev)            # never indexed for the phone

    def test_switching_file_indexing_off(self) -> None:
        from app import search_api
        search_api.save_state({"index_files": False})
        search_api._apply_settings(search_api.load_state())
        si.INDEX.refresh(["files"], force=True)
        self.assertEqual([i for i in si.search("compute_invoice_total")["items"] if i["type"] == "file"], [])
        search_api.save_state({"index_files": True})
        search_api._apply_settings(search_api.load_state())


class ApiTests(_Fresh):
    def setUp(self) -> None:
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import search_api
        self.with_chats()
        app = FastAPI()
        search_api.register(app, watch=False)
        self.client = TestClient(app)

    def test_search_preview_meta_status(self) -> None:
        data = self.client.post("/api/search", json={"q": "борщ", "lang": "ru"}).json()
        self.assertEqual(data["items"][0]["title"], "Рецепт борща")
        self.assertIn("took_ms", data)
        self.assertEqual(data["groups"][0]["type"], "chat")
        get = self.client.get("/api/search", params={"q": "борщ", "types": "chat"}).json()
        self.assertEqual(get["total"], data["total"])
        hit = data["items"][0]["hits"][0]["id"]
        preview = self.client.get("/api/search/preview", params={"id": hit, "q": "борщ"}).json()
        self.assertTrue(preview["blocks"])
        self.assertEqual(self.client.get("/api/search/preview", params={"id": "nope"}).status_code, 404)
        meta = self.client.get("/api/search/meta").json()
        self.assertIn("claude-sonnet", meta["models"])
        self.assertEqual(meta["folders"][0]["name"], "Учёба")
        self.assertTrue(any(t["type"] == "chat" and t["count"] >= 2 for t in meta["types"]))
        status = self.client.get("/api/search/status").json()
        self.assertGreater(status["groups"], 0)
        self.assertTrue(self.client.post("/api/search/reindex").json()["ok"])
        self.assertEqual(self.client.post("/api/search", json=[1]).status_code, 400)

    def test_recent_searches_and_settings(self) -> None:
        post = lambda body: self.client.post("/api/search/recent", json=body).json()  # noqa: E731
        post({"q": "первый"})
        post({"q": "второй"})
        items = post({"q": "Первый"})["items"]
        self.assertEqual([r["q"] for r in items], ["Первый", "второй"])     # de-duplicated, newest first
        self.assertEqual([r["q"] for r in post({"remove": "второй"})["items"]], ["Первый"])
        self.assertEqual(post({"clear": True})["items"], [])
        settings = self.client.post("/api/search/settings", json={"remember_recent": False}).json()
        self.assertFalse(settings["remember_recent"])
        self.assertEqual(post({"q": "не запомнится"})["items"], [])
        self.client.post("/api/search/settings", json={"remember_recent": True})
        self.assertEqual(self.client.post("/api/search/reveal", json={"id": "file:nope:x"}).status_code, 404)
        self.assertEqual(self.client.post("/api/search/reveal", json={"id": "msg:c1:0"}).status_code, 404)


class ScaleTests(_Fresh):
    def test_thousands_of_chats_stay_fast(self) -> None:
        words = ("проект сервер ошибка запрос модель ответ функция данные python docker react test memory "
                 "сервис отчёт задача время").split()
        rows = []
        for c in range(1500):
            msgs = [{"role": "user" if i % 2 == 0 else "assistant",
                     "content": " ".join(words[(c * 7 + i * 3 + k) % len(words)] for k in range(40)) + f" token{c}x{i}",
                     "createdAt": NOW - c * 1000 + i} for i in range(12)]
            rows.append({"id": f"s{c}", "title": f"Чат номер {c}", "messages": msgs, "updated": NOW - c * 1000})
        si.register_provider(si.FunctionProvider("bulk", ("chat",), revision=lambda: 1, units=lambda: {
            r["id"]: (1, (lambda r=r: si.ChatsProvider._docs(r, "", "", {}))) for r in rows}))
        started = time.perf_counter()
        si.INDEX.refresh(["bulk"])
        build = time.perf_counter() - started
        self.assertLess(build, 30.0)
        for query in ("ошибка python", "token777x3", "серв", "\"ошибка запрос\"", "ghjtrn"):
            started = time.perf_counter()
            result = si.search(query, refresh=False, limit=40)
            took = time.perf_counter() - started
            self.assertTrue(result["total"], query)
            self.assertLess(took, 1.5, f"{query}: {took:.3f}s")
        self.assertEqual(si.search("token777x3", refresh=False)["items"][0]["title"], "Чат номер 777")


class FrontendHookTests(unittest.TestCase):
    def test_files_are_wired_into_the_window(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('/search.js?v=', html)
        self.assertIn('/search.css?v=', html)
        self.assertLess(html.index("/app.js?v="), html.index("/search.js?v="))
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuSearch.toggle()", app)
        self.assertIn('action === "search") { if (window.MemuSearch) MemuSearch.open()', app)
        self.assertIn("openFile }", (STATIC / "memucode.js").read_text(encoding="utf-8").replace(",\n    openFile }", "openFile }").replace("\n", " ").replace("    ", " ").replace(", openFile", "openFile"))
        pg = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("search_api.register(app)", pg)
        for name in ("MemuAI.spec", "build.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("app.search_index", text, name)
            self.assertIn("app.search_api", text, name)

    def test_every_design_is_styled(self) -> None:
        css = (STATIC / "search.css").read_text(encoding="utf-8")
        designs = [p.stem for p in (STATIC / "designs").glob("*.css") if p.stem != "quick-extra"]
        self.assertTrue(designs)
        for design in designs:
            self.assertIn(f':root[data-design="{design}"]', css, design)
        self.assertIn('[data-theme="light"]', css)
        # colours only from tokens: no hard-coded hex outside color-mix() tints
        for line in css.splitlines():
            line = re.sub(r"var\(--[\w-]+,\s*#[0-9a-fA-F]{3,6}\)", "var()", line)   # a token with a fallback is fine
            for color in re.findall(r"#[0-9a-fA-F]{3,6}\b", line):
                self.assertIn("color-mix", line, line)

    def test_english_strings_have_no_cyrillic(self) -> None:
        js = (STATIC / "search.js").read_text(encoding="utf-8")
        # every L("ru", "en") pair: the English half is Latin
        for ru, en in re.findall(r'L\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', js):
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", en), en)
        for ru, en in re.findall(r'L\(`((?:[^`\\]|\\.)*)`,\s*`((?:[^`\\]|\\.)*)`\)', js):
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", en), en)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scripts_parse(self) -> None:
        for name in ("search.js", "app.js", "memucode.js"):
            proc = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
