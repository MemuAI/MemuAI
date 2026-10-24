"""Round 35 (files): MemuFiles — the optional file manager app (app/memufiles/**, memufiles.py).

Backend unit tests (ops queue, conflicts, undo, Recycle Bin, batch rename, search,
jobs, archives, AI plan builder/executor with a fake model), the HTTP server
(token, NDJSON listing, Range), packaging glue (build.py target, spec, installer
component, MemuAI launcher) and the i18n dictionaries (EN without Cyrillic).

Run: /workspace/work/venv/bin/python -m pytest -q tests/round35_files_test.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


def make_tree(base: Path) -> Path:
    (base / "src" / "sub").mkdir(parents=True)
    (base / "src" / "a.txt").write_text("hello", encoding="utf-8")
    (base / "src" / "big.bin").write_bytes(os.urandom(300_000))
    (base / "src" / "sub" / "b.md").write_text("# title\n" + "x" * 1000, encoding="utf-8")
    (base / "dst").mkdir()
    return base


def wait_op(queue, op, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if op.status in ("done", "error", "cancelled"):
            return op
        time.sleep(0.01)
    raise AssertionError(f"operation stuck in {op.status}")


def new_queue():
    from app.memufiles.opqueue import OpQueue
    events = []
    return OpQueue(notify=events.append), events


# ------------------------------------------------------------------ operations queue

def test_copy_tree_and_undo(tmp_path):
    base = make_tree(tmp_path)
    queue, events = new_queue()
    op = wait_op(queue, queue.submit("copy", [str(base / "src")], str(base / "dst")))
    assert op.status == "done", op.errors
    assert (base / "dst" / "src" / "sub" / "b.md").read_text(encoding="utf-8").startswith("# title")
    assert (base / "dst" / "src" / "big.bin").stat().st_size == 300_000
    assert op.done_bytes == op.total_bytes > 0
    assert any(e["status"] == "done" for e in events)
    undo = wait_op(queue, queue.undo())
    assert undo.status == "done"
    assert not (base / "dst" / "src").exists()            # the copy went to the trash
    assert (base / "src" / "a.txt").exists()                # the source is untouched


def test_copy_into_same_folder_makes_copy_name(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    op = wait_op(queue, queue.submit("copy", [str(base / "src" / "a.txt")], str(base / "src"), copy_suffix=" - копия"))
    assert op.status == "done"
    assert (base / "src" / "a - копия.txt").read_text(encoding="utf-8") == "hello"
    op2 = wait_op(queue, queue.submit("copy", [str(base / "src" / "a.txt")], str(base / "src"), copy_suffix=" - копия"))
    assert op2.status == "done"
    assert (base / "src" / "a - копия (2).txt").exists()


def test_copy_into_itself_is_refused(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    op = wait_op(queue, queue.submit("copy", [str(base / "src")], str(base / "src" / "sub")))
    assert op.status == "error" and op.message == "into_self"


def _conflict_answer(queue, op, choice, apply_all=False, name=""):
    deadline = time.time() + 5
    while op.status != "conflict":
        assert time.time() < deadline, op.status
        time.sleep(0.01)
    assert op.conflict["src"]["name"] and op.conflict["dst"]["name"]
    queue.resolve(op.id, choice, apply_all, name)


def test_conflict_replace_skip_keep_both_rename(tmp_path):
    base = make_tree(tmp_path)
    (base / "dst" / "a.txt").write_text("old", encoding="utf-8")
    queue, _ = new_queue()
    for choice, check in (("skip", lambda: (base / "dst" / "a.txt").read_text(encoding="utf-8") == "old"),
                          ("keep_both", lambda: (base / "dst" / "a (2).txt").read_text(encoding="utf-8") == "hello"),
                          ("rename", lambda: (base / "dst" / "renamed.txt").exists()),
                          ("replace", lambda: (base / "dst" / "a.txt").read_text(encoding="utf-8") == "hello")):
        op = queue.submit("copy", [str(base / "src" / "a.txt")], str(base / "dst"))
        _conflict_answer(queue, op, choice, name="renamed.txt")
        wait_op(queue, op)
        assert op.status == "done", (choice, op.errors)
        assert check(), choice


def test_conflict_apply_to_all_and_folder_merge(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    wait_op(queue, queue.submit("copy", [str(base / "src")], str(base / "dst")))
    (base / "src" / "new.txt").write_text("n", encoding="utf-8")
    op = queue.submit("copy", [str(base / "src")], str(base / "dst"))
    _conflict_answer(queue, op, "skip", apply_all=True)
    wait_op(queue, op)
    assert op.status == "done"
    assert (base / "dst" / "src" / "new.txt").exists()      # merged into the existing folder
    assert op.policy == "skip"


def test_conflict_cancel_stops_operation(tmp_path):
    base = make_tree(tmp_path)
    (base / "dst" / "a.txt").write_text("old", encoding="utf-8")
    queue, _ = new_queue()
    op = queue.submit("copy", [str(base / "src" / "a.txt"), str(base / "src" / "big.bin")], str(base / "dst"))
    _conflict_answer(queue, op, "cancel")
    wait_op(queue, op)
    assert op.status == "cancelled"
    assert not (base / "dst" / "big.bin").exists()


def test_pause_resume_cancel_removes_partial(tmp_path, monkeypatch):
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue, "CHUNK", 1024)
    base = make_tree(tmp_path)
    (base / "src" / "huge.bin").write_bytes(b"\0" * 3_000_000)
    queue, _ = new_queue()
    op = queue.submit("copy", [str(base / "src" / "huge.bin")], str(base / "dst"))
    queue.pause(op.id)
    time.sleep(0.15)
    done_at_pause = op.done_bytes
    time.sleep(0.15)
    assert op.done_bytes == done_at_pause and op.status == "paused"
    queue.cancel(op.id)
    wait_op(queue, op)
    assert op.status == "cancelled"
    assert not (base / "dst" / "huge.bin").exists()         # no half-written file is left behind


def test_move_and_undo(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    op = wait_op(queue, queue.submit("move", [str(base / "src" / "a.txt"), str(base / "src" / "sub")], str(base / "dst")))
    assert op.status == "done", op.errors
    assert (base / "dst" / "a.txt").exists() and (base / "dst" / "sub" / "b.md").exists()
    assert not (base / "src" / "a.txt").exists()
    wait_op(queue, queue.undo(op.id))
    assert (base / "src" / "a.txt").exists() and (base / "src" / "sub" / "b.md").exists()
    assert not (base / "dst" / "a.txt").exists()


def test_trash_and_undo_restores(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    op = wait_op(queue, queue.submit("trash", [str(base / "src" / "a.txt"), str(base / "src" / "sub")]))
    assert op.status == "done", op.errors
    assert not (base / "src" / "a.txt").exists()
    from app.memufiles import trash
    names = {row["name"] for row in trash.items()}
    assert {"a.txt", "sub"} <= names
    wait_op(queue, queue.undo())
    assert (base / "src" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert (base / "src" / "sub" / "b.md").exists()
    assert trash.items() == []


def test_trash_restore_purge_empty(tmp_path):
    from app.memufiles import trash
    f = tmp_path / "x.txt"
    f.write_text("1", encoding="utf-8")
    token = trash.send([str(f)])[0]["token"]
    assert not f.exists()
    assert trash.restore(token) == str(f) and f.exists()
    trash.send([str(f)])
    (tmp_path / "x.txt").write_text("again", encoding="utf-8")
    second = trash.send([str(f)])[0]["token"]
    assert len(trash.items()) == 2
    assert trash.purge([second]) == 1
    assert trash.empty() == 1 and trash.items() == []


def test_permanent_delete_and_protected_paths(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    op = wait_op(queue, queue.submit("delete", [str(base / "src")]))
    assert op.status == "done" and not (base / "src").exists()
    assert op.undo == []                                     # permanent: nothing to undo
    from app.memufiles import fsops
    assert fsops.protected_reason("/") == "root"
    assert fsops.protected_reason(os.path.expanduser("~")) == "home"
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32") if os.name == "nt" else "/usr/bin/python3"
    assert fsops.protected_reason(system) == "system"
    assert fsops.protected_reason(str(base / "dst")) is None
    op = wait_op(queue, queue.submit("delete", [os.path.expanduser("~")]))
    assert op.status == "error" and op.message == "protected"


def test_compress_and_extract_with_zip_slip_guard(tmp_path):
    base = make_tree(tmp_path)
    queue, _ = new_queue()
    zpath = base / "dst" / "pack.zip"
    op = wait_op(queue, queue.submit("compress", [str(base / "src" / "a.txt"), str(base / "src" / "sub")], str(zpath)))
    assert op.status == "done", op.errors
    with zipfile.ZipFile(zpath) as zf:
        assert set(zf.namelist()) >= {"a.txt", "sub/b.md"}
    out = base / "dst" / "pack"
    op = wait_op(queue, queue.submit("extract", [str(zpath)], str(out)))
    assert op.status == "done", op.errors
    assert (out / "sub" / "b.md").exists()
    evil = base / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "x")
        zf.writestr("ok.txt", "fine")
    op = wait_op(queue, queue.submit("extract", [str(evil)], str(base / "evil")))
    assert not (base / "escaped.txt").exists()
    assert (base / "evil" / "ok.txt").exists()
    assert any(e["error"] == "unsafe_path" for e in op.errors)


# ------------------------------------------------------------------ batch rename

def test_batch_rename_preview_and_swap(tmp_path):
    from app.memufiles import batchrename
    for name in ("one.JPG", "two.JPG", "three.jpg"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    paths = [str(tmp_path / n) for n in ("one.JPG", "two.JPG", "three.jpg")]
    rows = batchrename.preview(paths, {"pattern": "photo_{n:3}", "start": 1, "ext_case": "lower"})
    assert [r["new"] for r in rows] == ["photo_001.jpg", "photo_002.jpg", "photo_003.jpg"]
    assert not any(r["error"] for r in rows)
    rows = batchrename.preview(paths, {"pattern": "same"})
    assert rows[0]["new"] == "same.JPG" and rows[1]["error"] == "duplicate"
    rows = batchrename.preview(paths, {"pattern": "{name}", "find": "o", "replace": "0", "case": "upper"})
    assert rows[0]["new"] == "0NE.JPG"
    rows = batchrename.preview(paths, {"pattern": "{name}", "find": "(t)(\\w+)", "replace": "\\2-\\1", "regex": True})
    assert rows[1]["new"] == "wo-t.JPG"
    rows = batchrename.preview(paths, {"pattern": "a/b"})
    assert rows[0]["error"] == "invalid_chars"
    # swapping names in one batch works (two-phase rename) and is undoable
    queue, _ = new_queue()
    a, b = tmp_path / "one.JPG", tmp_path / "two.JPG"
    op = wait_op(queue, queue.submit("rename", [str(a), str(b)], pairs=[[str(a), "two.JPG"], [str(b), "one.JPG"]]))
    assert op.status == "done", op.errors
    assert a.read_text(encoding="utf-8") == "two.JPG" and b.read_text(encoding="utf-8") == "one.JPG"
    wait_op(queue, queue.undo())
    assert a.read_text(encoding="utf-8") == "one.JPG"


def test_unique_and_valid_names(tmp_path):
    from app.memufiles import fsops
    (tmp_path / "doc.txt").write_text("", encoding="utf-8")
    (tmp_path / "doc (2).txt").write_text("", encoding="utf-8")
    assert fsops.unique_name(str(tmp_path), "doc.txt") == "doc (3).txt"
    assert fsops.unique_name(str(tmp_path), "new.txt") == "new.txt"
    assert fsops.valid_name("") == "empty_name"
    assert fsops.valid_name("a/b") == "invalid_chars"
    assert fsops.valid_name("ok name.txt") is None
    assert fsops.split_name(".bashrc") == (".bashrc", "")
    assert fsops.split_name("a.tar.gz") == ("a.tar", ".gz")


# ------------------------------------------------------------------ search / jobs

def test_search_query_and_walk(tmp_path):
    from app.memufiles import search
    base = make_tree(tmp_path)
    q = search.Query("*.md")
    rows = list(search.walk_search(str(base), q, lambda: False))
    assert [r["n"] for r in rows] == ["b.md"]
    q = search.Query("size:>100kb")
    assert [r["n"] for r in search.walk_search(str(base), q, lambda: False)] == ["big.bin"]
    q = search.Query('content:"hello"')
    assert [r["n"] for r in search.walk_search(str(base), q, lambda: False)] == ["a.txt"]
    q = search.Query("kind:folder sub")
    assert [r["n"] for r in search.walk_search(str(base), q, lambda: False)] == ["sub"]
    assert search.parse_size("5кб") == 5120 and search.parse_size("1.5mb") == 1572864


def test_jobs_usage_dupes_size(tmp_path):
    from app.memufiles.jobs import Jobs
    base = make_tree(tmp_path)
    (base / "dst" / "copy.bin").write_bytes((base / "src" / "big.bin").read_bytes())
    (base / "dst" / "copy2.txt").write_text("hello", encoding="utf-8")
    jobs = Jobs()
    job = jobs.start("dupes", {"path": str(base), "wait": 10})
    assert job.status == "done", job.error
    groups = job.result["groups"]
    assert groups[0]["size"] == 300_000 and len(groups[0]["files"]) == 2
    assert any(g["size"] == 5 for g in groups)
    job = jobs.start("usage", {"path": str(base), "wait": 10})
    assert job.result["children"][0]["name"] == "src"
    job = jobs.start("size", {"paths": [str(base / "src")], "wait": 10})
    assert job.result["files"] == 3 and job.result["size"] > 300_000
    job = jobs.start("hash", {"path": str(base / "src" / "a.txt"), "wait": 10})
    assert job.result["md5"] == "5d41402abc4b2a76b9719d911017c592"


def test_archive_listing_and_text_preview(tmp_path):
    from app.memufiles import preview
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("dir/file.txt", "abc")
    info = preview.archive_list(str(z))
    assert info["kind"] == "zip" and info["items"][0]["name"] == "dir/file.txt"
    t = tmp_path / "cp.txt"
    t.write_bytes("Привет мир".encode("cp1251"))
    data = preview.read_text(str(t))
    assert data["text"] == "Привет мир" and data["encoding"] == "cp1251"
    (tmp_path / "bin.dat").write_bytes(b"\0\1\2" * 100)
    assert preview.read_text(str(tmp_path / "bin.dat"))["binary"]
    assert preview.text_length(str(t)) == 10


def test_thumbnail_cached(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    from app.memufiles import preview
    img = tmp_path / "p.png"
    PIL.new("RGB", (800, 400), (200, 30, 30)).save(img)
    data, ctype = preview.thumbnail(str(img), 128)
    assert ctype.startswith("image/") and len(data) > 50
    assert preview.cache_size()["count"] == 1
    data2, _ = preview.thumbnail(str(img), 128)
    assert data2 == data


# ------------------------------------------------------------------ AI assistant (fake model)

def test_ai_plan_select_small_files_and_execute(tmp_path):
    from app.memufiles import assistant
    base = make_tree(tmp_path)
    (base / "src" / "tiny.log").write_text("1", encoding="utf-8")
    scope = str(base / "src")
    plan = assistant.build_plan(scope, {"title": "small", "actions": [
        {"op": "trash", "select": {"max_size": "5kb"}, "reason": "small"},
        {"op": "delete", "paths": ["/etc/passwd"]},
        {"op": "trash", "paths": [str(tmp_path / "dst")]},
    ]})
    names = sorted(os.path.basename(i["path"]) for i in plan["items"])
    assert names == ["a.txt", "b.md", "tiny.log"]
    assert plan["totals"]["trash"]["count"] == 3
    whys = {r["why"] for r in plan["rejected"]}
    assert "outside_scope" in whys
    queue, _ = new_queue()
    # the user unticks b.md: only the confirmed items are touched
    selected = [i["i"] for i in plan["items"] if not i["path"].endswith("b.md")]
    result = assistant.execute_plan(plan, selected, queue)
    for op_id in result["ops"]:
        wait_op(queue, queue.get(op_id))
    assert not (base / "src" / "a.txt").exists() and not (base / "src" / "tiny.log").exists()
    assert (base / "src" / "sub" / "b.md").exists() and (base / "src" / "big.bin").exists()
    with pytest.raises(Exception):
        assistant.execute_plan(plan, selected, queue)        # a plan runs once


def test_ai_plan_chars_filter_and_delete_maps_to_trash(tmp_path):
    from app.memufiles import assistant
    base = make_tree(tmp_path)
    plan = assistant.build_plan(str(base / "src"), {"actions": [{"op": "delete", "select": {"max_chars": 500}}]})
    assert [os.path.basename(i["path"]) for i in plan["items"]] == ["a.txt"]
    assert plan["has_permanent"]
    queue, _ = new_queue()
    result = assistant.execute_plan(plan, [0], queue, permanent=False)
    wait_op(queue, queue.get(result["ops"][0]))
    assert queue.get(result["ops"][0]).kind == "trash"      # without the explicit switch: Recycle Bin


def test_ai_executor_revalidates(tmp_path):
    from app.memufiles import assistant
    base = make_tree(tmp_path)
    plan = assistant.build_plan(str(base / "src"), {"actions": [{"op": "trash", "paths": ["a.txt"]},
                                                                 {"op": "move", "paths": ["sub"], "dest": "../dst"}]})
    assert len(plan["items"]) == 1 and plan["rejected"][0]["why"] == "dest_outside_scope"
    (base / "src" / "a.txt").unlink()
    queue, _ = new_queue()
    result = assistant.execute_plan(plan, [0, 99], queue)
    assert result["ops"] == [] and result["skipped"][0]["why"] == "not_found"


def test_ai_chat_loop_with_fake_model(tmp_path):
    from app.memufiles import assistant
    base = make_tree(tmp_path)
    replies = iter([
        'Смотрю. <tool>{"name": "find_files", "args": {"max_size": "5kb"}}</tool>',
        'Нашёл. <tool>{"name": "propose_plan", "args": {"title": "Удалить мелкие", "actions": [{"op": "trash", "select": {"max_size": "5kb"}}]}}</tool>',
        "План готов: 2 файла в корзину.",
    ])
    seen_messages = []

    def llm(messages):
        seen_messages.append(list(messages))
        text = next(replies)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]
    events = []
    plans = assistant.PlanStore()
    assistant.run_chat(str(base / "src"), [{"role": "user", "content": "удали всё меньше 5 кб"}], "ru", llm,
                       lambda e: events.append(e) or True, plans, threading.Event())
    kinds = [e["t"] for e in events]
    assert "step" in kinds and "plan" in kinds and kinds[-1] == "final"
    plan_event = next(e for e in events if e["t"] == "plan")
    assert len(plan_event["plan"]["items"]) == 2
    visible = "".join(e["text"] for e in events if e["t"] == "delta")
    assert "<tool" not in visible and "<to" not in visible.replace("Смотрю", "")
    assert "tool_result" in seen_messages[1][-1]["content"]
    assert (base / "src" / "a.txt").exists()                # nothing changed without confirmation


def test_holdback_never_leaks_tool_marker():
    from app.memufiles.assistant import HoldBack
    hold = HoldBack()
    out = "".join(hold.feed(c) for c in ["Hi <", "to", "ol>{", "}"]) + hold.finish()
    assert out == "Hi "
    hold = HoldBack()
    out = "".join(hold.feed(c) for c in ["a < b", " ok"]) + hold.finish()
    assert out == "a < b ok"


# ------------------------------------------------------------------ HTTP server

@pytest.fixture()
def server(tmp_path):
    from app.memufiles import server as srv
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    yield app
    httpd.shutdown()


def call(app, path, body=None, token=True, raw=False, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=data, method="POST" if body is not None else "GET")
    if token:
        req.add_header("X-MF-Token", app.token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            content = resp.read()
            return resp.status, (content if raw else json.loads(content) if not resp.headers.get("Content-Type", "").startswith("application/x-ndjson") else [json.loads(l) for l in content.splitlines() if l])
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_server_token_listing_and_ops(server, tmp_path):
    base = make_tree(tmp_path)
    status, _ = call(server, "/api/boot", token=False)
    assert status == 401
    status, boot = call(server, "/api/boot")
    assert status == 200 and boot["app"] == "MemuFiles"
    status, lines = call(server, "/api/ls?path=" + urllib.request.quote(str(base / "src")))
    assert lines[0]["ok"] and lines[-1]["done"] and lines[-1]["count"] == 3
    rows = [r for line in lines if "rows" in line for r in line["rows"]]
    assert {r["n"] for r in rows} == {"a.txt", "big.bin", "sub"}
    assert next(r for r in rows if r["n"] == "sub")["d"] == 1
    status, err = call(server, "/api/ls?path=" + urllib.request.quote(str(base / "nope")))
    assert status == 404
    status, made = call(server, "/api/mkdir", {"parent": str(base / "dst"), "name": "Новая папка"})
    assert status == 200 and os.path.isdir(made["path"])
    status, err = call(server, "/api/mkdir", {"parent": str(base / "dst"), "name": "Новая папка"})
    assert status == 409 and err["error"] == "exists"
    status, ren = call(server, "/api/rename", {"path": made["path"], "name": "Готово"})
    assert status == 200 and ren["path"].endswith("Готово")
    status, undo = call(server, "/api/ops/undo", {})
    assert status == 200
    time.sleep(0.3)
    assert (base / "dst" / "Новая папка").is_dir()
    status, sub = call(server, "/api/ops/submit", {"kind": "trash", "sources": [os.path.expanduser("~")]})
    assert status == 409 and sub["error"] == "protected"
    status, sub = call(server, "/api/ops/submit", {"kind": "copy", "sources": [str(base / "src" / "a.txt")], "dest": str(base / "dst")})
    assert status == 200
    deadline = time.time() + 5
    while time.time() < deadline:
        _, ops = call(server, "/api/ops")
        if any(o["id"] == sub["op"]["id"] and o["status"] == "done" for o in ops["ops"]):
            break
        time.sleep(0.05)
    assert (base / "dst" / "a.txt").exists()
    status, seq = call(server, "/api/events?since=0")
    assert any(e["type"] == "op" for e in seq["events"])


def test_server_raw_range_and_search(server, tmp_path):
    base = make_tree(tmp_path)
    url = "/api/raw?path=" + urllib.request.quote(str(base / "src" / "a.txt")) + "&t=" + server.token
    status, content = call(server, url, token=False, raw=True, headers={"Range": "bytes=1-3"})
    assert status == 206 and content == b"ell"
    status, lines = call(server, "/api/search?q=b.md&root=" + urllib.request.quote(str(base)))
    rows = [r for line in lines if "rows" in line for r in line["rows"]]
    assert [r["n"] for r in rows] == ["b.md"] and lines[-1]["done"]
    status, prev = call(server, "/api/rename/preview", {"paths": [str(base / "src" / "a.txt")], "rule": {"pattern": "z_{name}"}})
    assert prev["items"][0]["new"] == "z_a.txt"
    status, props = call(server, "/api/props", {"path": str(base / "src" / "a.txt")})
    assert props["size"] == 5 and props["ext"] == ".txt"
    status, host = 0, None
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/boot", headers={"Host": "evil.com", "X-MF-Token": server.token})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 403


def test_server_ai_chat_with_override(server, tmp_path):
    base = make_tree(tmp_path)

    def llm(messages):
        last = messages[-1]["content"]
        if "tool_result" in last and "propose_plan" in last:
            yield "Готово."
        elif "tool_result" in last:
            yield '<tool>{"name": "propose_plan", "args": {"actions": [{"op": "trash", "paths": ["a.txt"]}]}}</tool>'
        else:
            yield '<tool>{"name": "list_folder", "args": {}}</tool>'
    server.llm_override = llm
    status, content = call(server, "/api/ai/chat", {"scope": str(base / "src"), "messages": [{"role": "user", "content": "удали a.txt"}]}, raw=True)
    events = [json.loads(line[6:]) for line in content.decode().split("\n\n") if line.startswith("data: ")]
    plan = next(e["plan"] for e in events if e["t"] == "plan")
    assert (base / "src" / "a.txt").exists()                 # proposal only
    status, result = call(server, "/api/ai/execute", {"plan_id": plan["id"], "selected": [0]})
    assert status == 200 and len(result["ops"]) == 1
    time.sleep(0.5)
    assert not (base / "src" / "a.txt").exists()


# ------------------------------------------------------------------ packaging, launcher, i18n

def test_build_target_spec_installer_and_launcher():
    import build
    target = build.TARGETS["memufiles"]
    assert target["entry"] == "memufiles.py" and target["icon"] == "memufiles-icon.ico"
    for module in ("app.memufiles.server", "app.memufiles.desktop", "app.memufiles.opqueue", "app.memufiles.assistant",
                   "app.memufiles.trash", "app.memufiles.clip"):
        assert module in target["hidden"]
    spec = (ROOT / "MemuFiles.spec").read_text(encoding="utf-8")
    assert "memufiles.py" in spec and "app.memufiles.server" in spec
    for name in ("memufiles-icon.ico", "memufiles-icon.png", "memufiles-version.txt"):
        assert (ROOT / name).is_file(), name
    from installer import products
    assert "memufiles" in products.SUITE.component_ids()
    assert ("memufiles", r"Packages\MemuFiles.zip") in products.SUITE.packages
    chosen, _ = products.SUITE.normalize(["proxy", "memufiles"], [])
    assert "memufiles" not in chosen                          # full edition only
    html = (ROOT / "installer" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="opt-memufiles"' in html
    from app import memufiles_manager
    st = memufiles_manager.status()
    assert st["dev"] is True and st["installed"] is True
    pg_html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
    assert "memufiles-settings.js" in pg_html
    assert "MemuFilesSettings" in (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")


def test_i18n_en_has_no_cyrillic_and_keys_match():
    text = (ROOT / "app" / "memufiles" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    en = text[text.index("const EN"):text.index("const RU")]
    ru = text[text.index("const RU"):]
    assert not re.search(r"[А-Яа-яЁё]", en)
    key = re.compile(r'^\s*"?([\w.]+)"?\s*:', re.M)
    assert set(key.findall(en)) == set(key.findall(ru))


def test_static_assets_present():
    static = ROOT / "app" / "memufiles" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="([^"?#]+)', html):
        assert (static / ref).is_file(), ref


# ------------------------------------------------------------------ second finisher (Round 35)
class _FakeIndex:
    """Stands in for app.fsindex: an index that is not loaded yet answers [] instantly."""
    def __init__(self, state="idle", file_bytes=0, rows=None):
        self.state, self.file_bytes, self.rows, self.started, self.calls = state, file_bytes, rows or [], 0, 0

    def status(self):
        return {"backend": "own", "own": {"state": self.state, "entries": len(self.rows), "file_bytes": self.file_bytes}}

    def start(self):
        self.started += 1

    def search_ex(self, text, limit=200, roots=None):
        self.calls += 1
        return {"results": list(self.rows) if self.state == "ready" else [], "error": ""}


def test_everywhere_search_walks_when_index_not_ready(server, tmp_path, monkeypatch):
    """«Везде» used to ask an idle index, get [] in 1 ms and show «ничего не найдено»."""
    from app.memufiles import search, fsops
    base = make_tree(tmp_path)
    fake = _FakeIndex(state="idle")
    monkeypatch.setattr(search, "_fsindex", lambda: fake)
    monkeypatch.setattr(fsops, "drives", lambda: [{"path": str(base)}])
    status, lines = call(server, "/api/search?scope=everywhere&q=b.md&root=" + urllib.request.quote(str(base)))
    rows = [r for line in lines if "rows" in line for r in line["rows"]]
    assert [r["n"] for r in rows] == ["b.md"] and lines[-1]["source"] == "walk"
    assert fake.calls == 0


def test_index_ready_warm_loads_saved_index_once(monkeypatch):
    from app.memufiles import search
    fake = _FakeIndex(state="idle", file_bytes=1234)
    monkeypatch.setattr(search, "_fsindex", lambda: fake)
    monkeypatch.setattr(search, "_WARMED", False)
    assert search.index_ready() is False and fake.started == 1
    assert search.index_ready() is False and fake.started == 1      # only once
    fake.state = "ready"
    assert search.index_ready() is True
    empty = _FakeIndex(state="idle", file_bytes=0)                    # nothing saved: never starts a build here
    monkeypatch.setattr(search, "_fsindex", lambda: empty)
    monkeypatch.setattr(search, "_WARMED", False)
    assert search.index_ready() is False and empty.started == 0


def test_second_finisher_ui_contracts():
    static = ROOT / "app" / "memufiles" / "static"
    ai = (static / "js" / "ai.js").read_text(encoding="utf-8")
    assert "renderMarkdown" in ai and "setMd(current" in ai                  # answers are rendered Markdown
    assert 'class="sc-path"' in ai                                           # icon span no longer styled as the path
    css = (static / "memufiles.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr)" in css                    # 480px window: no horizontal overflow
    assert ".row .cell.path" in css and "@media (max-width: 1040px)" in css
    i18n = (static / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "«{q}» во всём компьютере" in i18n
    inst = (ROOT / "installer" / "ui" / "style.css").read_text(encoding="utf-8")
    assert ".toggle-pair:not(:has(> .toggle-row[hidden])) > .toggle-row" in inst


def test_proxy_only_composite_has_no_chat_app_wording():
    text = (ROOT / "app" / "static" / "composite.js").read_text(encoding="utf-8")
    assert not re.search(r"\bMemuAI\b(?!\s+Proxy)", text)
