"""Round 35 — «Холст» / Canvas (app/canvas.py, pgstatic/canvas.js): patch engine, diff/hunks, store, tools, routes.

Run: NO_PROXY=127.0.0.1,localhost /workspace/work/venv/bin/python -m pytest -q tests/round35_canvas_test.py
Live part (real models through the Proxy) — see test_live_* (skipped without keys / a running dev stack).
"""
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="memu-canvas-test-")
os.environ.setdefault("MEMUAI_DATA_DIR", _TMP)

from app import canvas as C  # noqa: E402

DOC = """# Польза утренних прогулок

Утренняя прогулка — простой способ добавить в день движение. Не нужны абонемент, специальное оборудование
или спортивная подготовка: достаточно удобной обуви и короткого маршрута рядом с домом.

Вторая часть вступления рассказывает, что прогулку легко подстроить под своё самочувствие.

## Энергия

Свет и движение помогают проснуться.

## Сон

Регулярный режим помогает засыпать.
"""


# ============================================================== patch engine: matching

def test_exact_replace():
    new, rep = C.apply_edits(DOC, [{"find": "Свет и движение помогают проснуться.", "replace": "Свет бодрит."}])
    assert "Свет бодрит." in new and "Свет и движение" not in new
    assert rep[0]["how"] == "exact" and rep[0]["line"] == 10


def test_replace_preserves_rest_byte_for_byte():
    new, _ = C.apply_edits(DOC, [{"find": "## Сон", "replace": "## Сон и отдых"}])
    assert new == DOC.replace("## Сон", "## Сон и отдых")


def test_whitespace_normalized_match():
    # the model collapses the line break inside the paragraph into a space and adds double spaces
    find = "Не нужны абонемент,  специальное оборудование или спортивная подготовка"
    new, rep = C.apply_edits(DOC, [{"find": find, "replace": "Ничего не нужно"}])
    assert rep[0]["how"] == "normalized"
    assert "Ничего не нужно: достаточно удобной обуви" in new


def test_typographic_quotes_and_dashes_and_yo():
    text = "Он сказал: «Привет» — и ушёл.\n"
    new, rep = C.apply_edits(text, [{"find": 'Он сказал: "Привет" - и ушел.', "replace": "Тишина."}])
    assert new == "Тишина.\n" and rep[0]["how"] == "normalized"


def test_nbsp_and_zero_width_ignored():
    text = "Цена:\u00a0100\u200b руб.\n"
    new, _ = C.apply_edits(text, [{"find": "Цена: 100 руб.", "replace": "Цена: 90 руб."}])
    assert new == "Цена: 90 руб.\n"


def test_fuzzy_line_window_match():
    # one word differs and markdown marker missing → fuzzy, whole line replaced
    find = "Вторая часть вступления говорит, что прогулку легко подстроить под своё самочувствие."
    new, rep = C.apply_edits(DOC, [{"find": find, "replace": "Коротко: гуляйте в своём темпе."}])
    assert rep[0]["how"] == "fuzzy"
    assert "Коротко: гуляйте в своём темпе." in new and "Вторая часть" not in new
    assert "## Энергия" in new


def test_fuzzy_ignores_markdown_heading_marker():
    new, rep = C.apply_edits(DOC, [{"find": "Энергия!", "replace": "## Бодрость"}])
    assert rep[0]["how"] == "fuzzy" and "## Бодрость" in new and "## Энергия" not in new


def test_trimmed_find_with_surrounding_newlines():
    new, rep = C.apply_edits(DOC, [{"find": "\n\n## Сон\n\n", "replace": "## Отдых"}])
    assert "## Отдых" in new and rep[0]["how"] == "exact"


def test_not_found_error_is_readable_and_atomic():
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(DOC, [{"find": "## Сон", "replace": "## Отдых"},
                            {"find": "Совершенно другой текст, которого нет нигде", "replace": "x"}])
    exc = err.value
    assert exc.code == "not_found" and exc.index == 1
    assert exc.message.startswith("Edit #2 of 2")
    result = exc.to_result()
    assert "Nothing was changed" in result["hint"] and result["edit_index"] == 1


def test_not_found_gives_closest_when_similar_enough():
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(DOC, [{"find": "Регулярный режим сна помогает быстро засыпать и бодро просыпаться каждый день.", "replace": "x"}])
    res = err.value.to_result()
    if res.get("closest"):
        assert "Регулярный режим" in res["closest"] and res["closest_line"] == 14


def test_ambiguous_exact_match():
    text = "Пункт.\nПункт.\nКонец.\n"
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(text, [{"find": "Пункт.", "replace": "X"}])
    assert err.value.code == "ambiguous" and "2 times" in err.value.message and "lines 1, 2" in err.value.message


def test_all_and_occurrence():
    text = "a b a b a\n"
    assert C.apply_edits(text, [{"find": "a", "replace": "z", "all": True}])[0] == "z b z b z\n"
    assert C.apply_edits(text, [{"find": "a", "replace": "z", "occurrence": 2}])[0] == "a b z b a\n"
    assert C.apply_edits(text, [{"find": "a", "replace": "z", "occurrence": -1}])[0] == "a b a b z\n"
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(text, [{"find": "a", "replace": "z", "occurrence": 7}])
    assert err.value.code == "bad_occurrence"


def test_ambiguous_fuzzy_is_refused():
    text = "Первый длинный пункт про яблоки и груши\nвставка\nПервый длинный пункт про яблоки и грущи\n"
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(text, [{"find": "Первый длинный пункт про яблоки и грушы", "replace": "x"}])
    assert err.value.code == "ambiguous"


def test_sequential_edits_see_previous_result():
    new, _ = C.apply_edits("один\n", [{"find": "один", "replace": "два"}, {"find": "два", "replace": "три"}])
    assert new == "три\n"


def test_empty_find_and_bad_shapes():
    for bad in ([{"find": "", "replace": "x"}], [{"replace": "x"}], [], None, "text", [{"op": "jump"}], [42]):
        with pytest.raises(C.PatchError):
            C.apply_edits(DOC, bad)


def test_too_many_edits():
    with pytest.raises(C.PatchError) as err:
        C.apply_edits(DOC, [{"find": "a", "replace": "b"}] * (C.MAX_EDITS + 1))
    assert err.value.code == "too_many"


# ============================================================== patch engine: ops and aliases

def test_aliases_old_new_and_single_dict():
    assert C.apply_edits("x = 1\n", {"old_string": "x = 1", "new_string": "x = 2"})[0] == "x = 2\n"
    assert C.apply_edits("x = 1\n", [{"search": "1", "replacement": "3"}])[0] == "x = 3\n"
    assert C.apply_edits("x = 1\n", [{"old": "x = 1", "new": ""}])[0] == "\n"


def test_insert_after_whole_line_and_inline():
    text = "# Заголовок\nТекст.\n"
    assert C.apply_edits(text, [{"op": "insert_after", "anchor": "# Заголовок", "text": "Подзаголовок"}])[0] == \
        "# Заголовок\nПодзаголовок\nТекст.\n"
    assert C.apply_edits(text, [{"op": "insert_after", "anchor": "Заго", "text": "!"}])[0] == "# Заго!ловок\nТекст.\n"
    assert C.apply_edits("a\nb", [{"op": "after", "find": "b", "text": "c"}])[0] == "a\nb\nc"


def test_insert_before_whole_line():
    text = "</head>\n<body>\n"
    new, _ = C.apply_edits(text, [{"op": "insert_before", "anchor": "</head>", "text": "<style>body{}</style>"}])
    assert new == "<style>body{}</style>\n</head>\n<body>\n"


def test_delete_whole_line_removes_newline():
    assert C.apply_edits("a\nb\nc\n", [{"op": "delete", "find": "b"}])[0] == "a\nc\n"
    assert C.apply_edits("abc\n", [{"op": "delete", "find": "b"}])[0] == "ac\n"


def test_append_prepend_rewrite():
    assert C.apply_edits("a", [{"op": "append", "text": "b"}])[0] == "a\nb"
    assert C.apply_edits("a\n", [{"op": "append", "text": "b"}])[0] == "a\nb"
    assert C.apply_edits("b", [{"op": "prepend", "text": "a"}])[0] == "a\nb"
    assert C.apply_edits("old", [{"op": "rewrite", "text": "new"}])[0] == "new"
    assert C.apply_edits("old", [{"op": "replace_all", "content": "new"}])[0] == "new"


def test_html_dark_theme_patch():
    page = "<!doctype html>\n<html>\n<head>\n<style>\nbody { background: #fff; color: #222; }\n</style>\n</head>\n<body>\n<h1>Кофейня</h1>\n</body>\n</html>\n"
    new, rep = C.apply_edits(page, [
        {"op": "insert_before", "anchor": "</style>", "text": "@media (prefers-color-scheme: dark) { body { background: #111; color: #eee; } }"},
        {"find": "<h1>Кофейня</h1>", "replace": "<h1>Кофейня</h1>\n<button id=\"theme\">Тема</button>"},
    ])
    assert "@media (prefers-color-scheme: dark)" in new and new.index("@media") < new.index("</style>")
    assert '<button id="theme">' in new and [r["how"] for r in rep] == ["exact", "exact"]


def test_code_indentation_kept_by_exact_replace():
    code = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    new, _ = C.apply_edits(code, [{"find": "        return 1", "replace": "        return 2"}])
    assert new == code.replace("return 1", "return 2")


def test_large_text_performance():
    import time
    big = "\n".join(f"Строка номер {i} с каким-то текстом про проект." for i in range(3000)) + "\n"
    t0 = time.time()
    new, rep = C.apply_edits(big, [{"find": "Строка номер 2500 с каким-то текстом про проект.", "replace": "X"}])
    assert rep[0]["how"] == "exact" and time.time() - t0 < 1
    t0 = time.time()
    with pytest.raises(C.PatchError):
        C.apply_edits(big, [{"find": "Совсем другая строка без совпадений, длинная-предлинная", "replace": "X"}])
    assert time.time() - t0 < 10


# ============================================================== diff + hunks

def test_diff_hunks_and_word_diff():
    old = "a\nb\nc\nd\n"
    new = "a\nB\nc\nd\ne\n"
    hunks = C.diff_hunks(old, new)
    assert len(hunks) == 2
    assert hunks[0]["old"] == "b\n" and hunks[0]["new"] == "B\n" and hunks[0]["words"]
    assert hunks[1]["tag"] == "insert" and hunks[1]["new"] == "e\n"


def test_apply_hunks_all_none_some():
    old = "one\ntwo\nthree\nfour\n"
    new = "ONE\ntwo\nthree\nFOUR\nfive\n"
    n = len(C.diff_hunks(old, new))
    assert n == 2
    assert C.apply_hunks(old, new, range(n)) == new
    assert C.apply_hunks(old, new, []) == old
    assert C.apply_hunks(old, new, [0]) == "ONE\ntwo\nthree\nfour\n"
    assert C.apply_hunks(old, new, [1]) == "one\ntwo\nthree\nFOUR\nfive\n"


def test_diff_stats():
    assert C.diff_stats("a\nb\n", "a\nc\nd\n") == {"added": 2, "removed": 1}


# ============================================================== store

CHAT = "chat-test-canvas"


def test_store_create_edit_versions_and_review(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    art = C.create(CHAT, "markdown", "Статья", DOC, model="m1")
    assert art["type"] == "document" and art["head"] == 1 and art["id"].startswith("cv-")
    res = C.edit(CHAT, art["id"], [{"find": "## Сон", "replace": "## Отдых"}], model="m1")
    assert res["version"] == 2 and res["parent"] == 1 and res["stats"] == {"added": 1, "removed": 1}
    full = C.get(CHAT, art["id"])
    assert full["head"] == 2 and full["versions"][1]["review"] == "pending"
    # reject everything → a user version equal to v1
    r = C.resolve(CHAT, art["id"], 2, decision="reject")
    assert r["version"] == 3
    full = C.get(CHAT, art["id"])
    assert full["versions"][-1]["content"] == DOC and full["versions"][1]["review"] == "rejected"
    # user version, then set head back
    u = C.user_version(CHAT, art["id"], DOC + "\nP.S.\n")
    assert u["version"] == 4 and C.get(CHAT, art["id"])["versions"][-1]["author"] == "user"
    assert C.user_version(CHAT, art["id"], DOC + "\nP.S.\n")["unchanged"]
    C.set_head(CHAT, art["id"], 1)
    assert C.get(CHAT, art["id"])["head"] == 1


def test_partial_review(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    art = C.create(CHAT, "document", "Doc", "one\ntwo\nthree\nfour\n")
    C.edit(CHAT, art["id"], [{"find": "one", "replace": "ONE"}, {"find": "four", "replace": "FOUR"}])
    r = C.resolve(CHAT, art["id"], 2, reject=[1])
    assert r["accepted"] == 1 and r["rejected"] == 1
    full = C.get(CHAT, art["id"])
    assert full["versions"][-1]["content"] == "ONE\ntwo\nthree\nfour\n"
    assert full["versions"][1]["review"] == "partial"
    # accepting all just marks it
    C.edit(CHAT, art["id"], [{"find": "two", "replace": "TWO"}])
    r = C.resolve(CHAT, art["id"], full["head"] + 1, decision="accept")
    assert r["rejected"] == 0


def test_edit_on_base_version_and_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    art = C.create(CHAT, "code", "f.py", "x = 1\ny = 2\n", language="python")
    C.edit(CHAT, art["id"], [{"find": "x = 1", "replace": "x = 10"}])
    res = C.edit(CHAT, art["id"], [{"find": "y = 2", "replace": "y = 20"}], base=1)   # another branch saw v1
    assert res["parent"] == 1
    assert C.get(CHAT, art["id"])["versions"][-1]["content"] == "x = 1\ny = 20\n"
    with pytest.raises(C.PatchError) as err:
        C.edit(CHAT, art["id"], [{"find": "y = 20", "replace": "y = 20"}])
    assert err.value.code == "no_change"


def test_version_cap_keeps_head(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    monkeypatch.setattr(C, "MAX_VERSIONS", 5)
    art = C.create(CHAT, "document", "Doc", "v0\n")
    for i in range(1, 12):
        C.user_version(CHAT, art["id"], f"v{i}\n")
    full = C.get(CHAT, art["id"])
    assert len(full["versions"]) == 5 and full["head"] == 12 and full["versions"][-1]["v"] == 12


def test_find_artifact_by_title_and_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    a = C.create(CHAT, "document", "Первый", "a\n")
    b = C.create(CHAT, "email", "Письмо", "b\n", meta={"subject": "Тема", "to": "x@y.z"})
    data = C.load(CHAT)
    assert C.find_artifact(data, "первый")["id"] == a["id"]
    assert C.find_artifact(data, "")["id"] == b["id"]
    with pytest.raises(KeyError):
        C.find_artifact(data, "cv-nope00")
    assert C.get(CHAT, b["id"])["versions"][0]["meta"] == {"subject": "Тема", "to": "x@y.z"}


def test_chat_id_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    path = C._chat_file("../../evil")
    assert path.parent == tmp_path and "/" not in path.name and not path.name.startswith(".")
    with pytest.raises(ValueError):
        C._chat_file("")


def test_normalize_type():
    assert C.normalize_type("page") == "html" and C.normalize_type("csv") == "table"
    assert C.normalize_type("", "<!DOCTYPE html><html>") == "html"
    assert C.normalize_type("", "print(1)", "python") == "code"
    assert C.normalize_type("whatever") == "document"


# ============================================================== model tools

def test_run_tool_create_edit_read_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    r = C.run_tool(CHAT, "canvas_create", {"type": "document", "title": "Статья", "content": "```markdown\n" + DOC + "```"})
    assert r["ok"] and r["version"] == 1 and "do not paste" in r["note"]
    aid = r["id"]
    assert C.get(CHAT, aid)["versions"][0]["content"].startswith("# Польза")    # fence stripped
    e = C.run_tool(CHAT, "canvas_edit", {"id": aid, "edits": [{"find": "## Сон", "replace": "## Отдых"}]})
    assert e["ok"] and e["version"] == 2 and e["matched"] == "exact" and e["lines_added"] == 1
    bad = C.run_tool(CHAT, "canvas_edit", {"id": aid, "edits": [{"find": "нет такого текста вообще нигде", "replace": "x"}]})
    assert "error" in bad and bad["code"] == "not_found" and "hint" in bad
    assert C.get(CHAT, aid)["head"] == 2                                  # nothing changed on error
    unknown = C.run_tool(CHAT, "canvas_edit", {"id": "cv-zzzzzz", "edits": [{"find": "a", "replace": "b"}]})
    assert unknown["code"] == "unknown_artifact" and aid in unknown["error"]
    top = C.run_tool(CHAT, "canvas_edit", {"id": aid, "find": "## Отдых", "replace": "## Сон и отдых"})
    assert top["ok"]
    as_str = C.run_tool(CHAT, "canvas_edit", {"id": aid, "edits": json.dumps([{"find": "## Энергия", "replace": "## Бодрость"}])})
    assert as_str["ok"]
    rd = C.run_tool(CHAT, "canvas_read", {"id": aid, "start_line": 1, "end_line": 3})
    assert rd["ok"] and rd["total_lines"] > 3 and rd["content"].count("\n") == 2
    assert C.run_tool("", "canvas_create", {"content": "x"})["error"]
    assert C.run_tool(CHAT, "canvas_create", {"content": ""})["error"]
    assert C.run_tool(CHAT, "canvas_fly", {})["error"]


def test_run_tool_respects_branch_heads(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    aid = C.run_tool(CHAT, "canvas_create", {"type": "doc", "title": "T", "content": "a\nb\n"})["id"]
    C.run_tool(CHAT, "canvas_edit", {"id": aid, "edits": [{"find": "a", "replace": "A"}]})
    # the active branch still shows v1: the edit applies to v1
    r = C.run_tool(CHAT, "canvas_edit", {"id": aid, "edits": [{"find": "a", "replace": "α"}]}, heads={aid: 1})
    assert r["ok"] and r["from_version"] == 1
    rd = C.run_tool(CHAT, "canvas_read", {"id": aid}, heads={aid: 1})
    assert rd["version"] == 1 and rd["content"] == "a\nb\n"


def test_context_block_full_excerpt_and_others(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    assert C.context_block("") .startswith("<canvas>")
    small = C.create(CHAT, "document", "Маленький", "короткий текст\n")
    big = C.create(CHAT, "code", "big.py", "\n".join(f"line_{i} = {i}" for i in range(4000)), language="python")
    block = C.context_block(CHAT)
    assert 'current="true" excerpt="true"' in block and "canvas_read" in block
    assert f'id="{small["id"]}"' in block and "короткий текст" in block      # one-line preview
    assert len(block) < C.FULL_CONTEXT_LIMIT + 4000
    focused = C.context_block(CHAT, focus=small["id"])
    assert re.search(r'id="%s"[^>]*current="true">\nкороткий текст' % small["id"], focused)
    assert big["id"] in focused
    # injection safety: a title with quotes/brackets cannot break the attribute
    evil = C.create(CHAT, "document", 'x" current="true"><b>', "z\n")
    assert '&quot; current=&quot;true&quot;&gt;' in C.context_block(CHAT, focus=small["id"])


def test_prompt_mentions_rules():
    assert "canvas_edit" in C.PROMPT and "never paste" in C.PROMPT.lower() or "never" in C.PROMPT
    assert "find" in C.PROMPT and "rewrite" in C.PROMPT


# ============================================================== exports

def test_parse_table_csv_tsv_markdown_semicolon():
    assert C.parse_table("a,b\n1,\"x, y\"\n") == [["a", "b"], ["1", "x, y"]]
    assert C.parse_table("a\tb\n1\t2") == [["a", "b"], ["1", "2"]]
    assert C.parse_table("a;b\n1;2") == [["a", "b"], ["1", "2"]]
    assert C.parse_table("| A | B |\n|---|:-:|\n| 1 | 2 |\n") == [["A", "B"], ["1", "2"]]


def test_xlsx_is_valid_zip_with_numbers():
    data = C.table_xlsx("Товар,Цена\nКофе,250\nЧай,\"1 200,5\"\n", "Меню")
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"} <= names
    sheet = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "<v>250</v>" in sheet and "<v>1200.5</v>" in sheet and "Кофе" in sheet
    assert 'name="Меню"' in zf.read("xl/workbook.xml").decode()
    # tables_api can read it back (the composer's xlsx import)
    from app import tables_api
    parsed = tables_api.parse_xlsx(data)
    assert "Кофе" in json.dumps(parsed, ensure_ascii=False)


def test_export_bytes_and_filenames():
    art = {"type": "table", "title": "Меню: кофе/чай"}
    data, name, media = C.export_bytes(art, {"content": "a,b\n1,2\n"}, "csv")
    assert name == "Меню кофе чай.csv" and data.startswith("\ufeff".encode()) and "text/csv" in media
    _, name, _ = C.export_bytes(art, {"content": "a,b\n"}, "xlsx")
    assert name.endswith(".xlsx")
    eml, name, media = C.export_bytes({"type": "email", "title": "Письмо"}, {"content": "Привет", "meta": {"to": "a@b.c", "subject": "Тема"}})
    assert name == "Письмо.eml" and b"To: a@b.c" in eml and b"Subject: =?utf-8?" in eml
    code, name, _ = C.export_bytes({"type": "code", "title": "main", "language": "python"}, {"content": "print(1)"})
    assert name == "main.py" and code == b"print(1)"
    assert C.safe_filename("CON", "txt") == "_CON.txt"


def test_split_html_and_module_id():
    parts = C.split_html("<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Hi</h1>"
                         "<script>console.log(1)</script><script src=\"x.js\"></script></body></html>")
    assert parts["css"] == "h1{color:red}" and parts["js"] == "console.log(1)" and parts["html"].startswith("<h1>Hi</h1>")
    assert C.module_id_for("Кофейня «Утро»") == "kofeynya-utro"
    assert C.module_id_for("123", ["canvas-123"]) == "canvas-123-2"
    assert re.match(r"^[a-z][a-z0-9-]{0,40}$", C.module_id_for("Очень длинное название страницы для модуля MemuAI"))


# ============================================================== routes

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    C.register(app)
    return TestClient(app)


def test_routes_roundtrip(client):
    r = client.post("/api/pg/canvas/tool", json={"chat_id": "c1", "tool": "canvas_create",
                                                  "args": {"type": "html", "title": "Кофейня", "content": "<html><head></head><body><h1>Hi</h1></body></html>"}})
    aid = r.json()["id"]
    r = client.post("/api/pg/canvas/tool", json={"chat_id": "c1", "tool": "canvas_edit", "heads": {aid: "1"},
                                                  "args": {"id": aid, "edits": [{"find": "<h1>Hi</h1>", "replace": "<h1>Привет</h1>"}]}})
    assert r.json()["ok"] and r.json()["version"] == 2
    lst = client.get("/api/pg/canvas/c1").json()
    assert lst["artifacts"][0]["head"] == 2 and lst["artifacts"][0]["versions"][1]["review"] == "pending"
    full = client.get(f"/api/pg/canvas/c1/{aid}").json()["artifact"]
    assert len(full["versions"]) == 2 and full["versions"][0]["content"].startswith("<html>")
    diff = client.get(f"/api/pg/canvas/c1/{aid}/diff?v=2").json()
    assert diff["review"] == "pending" and len(diff["hunks"]) == 1 and diff["base"] == 1
    ctx = client.post("/api/pg/canvas/context", json={"chat_id": "c1", "heads": {aid: 1}}).json()["block"]
    assert 'version="1"' in ctx and "<h1>Hi</h1>" in ctx
    res = client.post(f"/api/pg/canvas/c1/{aid}/resolve", json={"v": 2, "decision": "reject"}).json()
    assert res["version"] == 3
    ver = client.post(f"/api/pg/canvas/c1/{aid}/version", json={"content": "<p>руками</p>", "base": 3}).json()
    assert ver["version"] == 4
    assert client.post(f"/api/pg/canvas/c1/{aid}/head", json={"v": 2}).json()["artifact"]["head"] == 2
    assert client.post(f"/api/pg/canvas/c1/{aid}/rename", json={"title": "Новая"}).json()["artifact"]["title"] == "Новая"
    exp = client.get(f"/api/pg/canvas/c1/{aid}/export?v=4")
    assert exp.status_code == 200 and exp.content == "<p>руками</p>".encode() and "filename*=UTF-8''" in exp.headers["content-disposition"]
    assert client.get("/api/pg/canvas/c1/cv-nope/diff").status_code == 404
    assert client.post(f"/api/pg/canvas/c1/{aid}/delete").json()["ok"]
    assert client.get("/api/pg/canvas/c1").json()["artifacts"] == []


def test_route_table_xlsx_export(client):
    r = client.post("/api/pg/canvas/c2/create", json={"type": "table", "title": "T", "content": "a,b\n1,2\n"}).json()
    aid = r["artifact"]["id"]
    x = client.get(f"/api/pg/canvas/c2/{aid}/export?fmt=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_route_memunote_not_installed_and_ok(client, monkeypatch, tmp_path):
    aid = client.post("/api/pg/canvas/c3/create", json={"type": "document", "title": "Заметка", "content": "# Hi\n"}).json()["artifact"]["id"]
    from app import memunote_manager

    def missing(files=None):
        raise memunote_manager.ManagerError("not_installed", "MemuNote is not installed")
    monkeypatch.setattr(memunote_manager, "open_app", missing)
    r = client.post(f"/api/pg/canvas/c3/{aid}/memunote", json={})
    assert r.status_code == 404 and r.json()["code"] == "not_installed"
    opened = {}
    monkeypatch.setattr(memunote_manager, "open_app", lambda files=None: opened.setdefault("files", files) and {"ok": True})
    r = client.post(f"/api/pg/canvas/c3/{aid}/memunote", json={})
    assert r.json()["ok"] and opened["files"][0].endswith("Заметка.md")
    assert Path(opened["files"][0]).read_text(encoding="utf-8") == "# Hi\n"


def test_route_module_creates_disabled_module(client, monkeypatch, tmp_path):
    from app import modules_host
    root = tmp_path / "modules"
    root.mkdir()
    monkeypatch.setattr(modules_host, "default_root", lambda: root)
    aid = client.post("/api/pg/canvas/c4/create", json={"type": "html", "title": "Таймер", "content":
                      "<html><head><style>b{}</style></head><body><b id=t>0</b><script>t.textContent=1</script></body></html>"}).json()["artifact"]["id"]
    r = client.post(f"/api/pg/canvas/c4/{aid}/module", json={}).json()
    assert r["ok"] and r["id"] == "taymer"
    folder = root / "custom" / "taymer"
    assert (folder / "manifest.json").exists()
    doc = client.post("/api/pg/canvas/c4/create", json={"type": "document", "title": "D", "content": "x"}).json()["artifact"]["id"]
    assert client.post(f"/api/pg/canvas/c4/{doc}/module", json={}).json()["code"] == "unsupported"


# ============================================================== window wiring (static checks)

def test_window_wiring():
    app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
    assert "MemuCanvas.runTool(assistant, call, source)" in app_js
    assert "await MemuCanvas.promptBlock(chatState)" in app_js
    assert "MemuCanvas.historyNote(m)" in app_js and "MemuCanvas.cardsHtml(m)" in app_js
    assert "/canvas.js?v=" in html and "/canvas.css?v=" in html
    js = (ROOT / "app/pgstatic/canvas.js").read_text(encoding="utf-8")
    assert 'sandbox="allow-scripts"' in js and "allow-same-origin" not in js.replace("lacks the 'allow-same-origin'", "")
    assert "connect-src 'none'" in js and "event.source !== frame.contentWindow" in js
    playground = (ROOT / "app/playground_api.py").read_text(encoding="utf-8")
    assert "canvas.register(app)" in playground
    assert "'app.canvas'" in (ROOT / "MemuAI.spec").read_text(encoding="utf-8")


def test_canvas_js_pure_helpers_in_node():
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        pytest.skip("node is not installed")
    script = r"""
global.window = global; global.localStorage = {getItem(){return null}, setItem(){}};
global.document = {addEventListener(){}, querySelector(){return null}, querySelectorAll(){return []}, body:{classList:{add(){},remove(){}}}};
window.addEventListener = () => {}; global.setInterval = () => 0;
require(process.argv[1]);
const C = window.MemuCanvas;
const list = [{id:"a", head:4, versions:[{v:1,parent:null,author:"model"},{v:2,parent:1,author:"model"},{v:3,parent:1,author:"model"},{v:4,parent:3,author:"user"}]}, {id:"b", head:1, versions:[{v:1}]}];
const out = {
  path2: C._pathHeads([{canvas:[{id:"a", v:2}]}], list).a,
  path3: C._pathHeads([{canvas:[{id:"a", v:3}]}], list).a,
  path1: C._pathHeads([{canvas:[{id:"a", v:1}]}], list).a,
  none: C._pathHeads([], list).a,
  b: C._pathHeads([{canvas:[{id:"a", v:1}]}], list).b,
  table: C._parseTable("| A | B |\n|---|---|\n| 1 | 2 |"),
  csv: C._parseTable('x,y\n"a, b",2'),
  doc: C._sandboxDoc("<html><head><title>t</title></head><body>hi</body></html>", "N1"),
  frag: C._sandboxDoc("<b>x</b>", "N2"),
  isTool: [C.isTool("canvas_edit"), C.isTool("canvas_x")],
};
console.log(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script, str(ROOT / "app/pgstatic/canvas.js")], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["path2"] == 2          # head v4 descends from v3, not from v2 → the branch sees v2
    assert out["path3"] == 4          # v4 is the user's edit on top of v3 → the branch sees v4
    assert out["path1"] == 1          # v3 (a model edit of another branch) sits between → the branch sees v1
    assert out["none"] == 4 and out["b"] == 1
    assert out["table"] == [["A", "B"], ["1", "2"]] and out["csv"] == [["x", "y"], ["a, b", "2"]]
    assert out["doc"].index("Content-Security-Policy") < out["doc"].index("<title>")
    assert "connect-src 'none'" in out["doc"] and '"N1"' in out["doc"]
    assert out["frag"].startswith("<!doctype html><html><head><meta charset")
    assert out["isTool"] == [True, False]


# ============================================================== live (real models through the Proxy)

def _live_env():
    keys = Path("/workspace/work/secrets/test_keys.env")
    if not keys.exists():
        return None
    env = {}
    for line in keys.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


@pytest.mark.parametrize("model_key", ["SOL", "REFORM"])
def test_live_model_patches_document(model_key, tmp_path, monkeypatch):
    """A real model gets the canvas prompt + artifact and must answer «сделай вступление короче» with canvas_edit
    patches that the engine applies (direct API call; the full window flow is checked by the screenshot script)."""
    env = _live_env()
    if not env or not env.get(f"{model_key}_KEY") or os.environ.get("CANVAS_LIVE") != "1":
        pytest.skip("live canvas test: set CANVAS_LIVE=1 and have test keys")
    import httpx
    monkeypatch.setattr(C, "_root", lambda: tmp_path)
    model = "gpt-6.1-sol" if model_key == "SOL" else "qwen3.8-max"
    aid = C.create("live", "document", "Прогулки", DOC)["id"]
    system = C.context_block("live") + "\n\nTools are called with a hidden block:\n```memuai-tool\n[{\"tool\": \"canvas_edit\", \"args\": {...}}]\n```"
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": "сделай вступление короче"}], "temperature": 0.2}
    r = httpx.post(env[f"{model_key}_BASE"].rstrip("/") + "/chat/completions", json=body,
                   headers={"Authorization": f"Bearer {env[f'{model_key}_KEY']}"}, timeout=180, trust_env=False)
    if r.status_code in (401, 402, 403, 429):
        pytest.skip(f"{model_key}: {r.status_code}")
    text = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"```memuai-tool\s*(\[.*?\]|\{.*?\})\s*```", text, flags=re.S)
    assert m, text[:800]
    calls = json.loads(m.group(1))
    calls = calls if isinstance(calls, list) else [calls]
    call = next(c for c in calls if (c.get("tool") or c.get("name")) == "canvas_edit")
    res = C.run_tool("live", "canvas_edit", call.get("args") or call.get("arguments") or {})
    assert res.get("ok"), res
    new = C.get("live", aid)["versions"][-1]["content"]
    assert len(new) < len(DOC) and "## Энергия" in new
