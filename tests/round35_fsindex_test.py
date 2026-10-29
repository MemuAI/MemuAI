"""Round 35 · fsindex: PC-wide instant file search (app/fsindex*.py, Quick Access plugin «fsearch», search_files).

Run: MEMUAI_FSINDEX_NOSTART=1 /workspace/work/venv/bin/python -m pytest -q tests/round35_fsindex_test.py
Windows-only parts (Everything IPC/DLL/es.exe, MFT, USN, ReadDirectoryChangesW) are tested through their pure parsers
on synthetic buffers and with mocks; they are NOT exercised on a real Windows machine here.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-fsindex-"))
os.environ["MEMUAI_FSINDEX_NOSTART"] = "1"

from app import fsindex, fsindex_query as fq, fsindex_win as fw  # noqa: E402

NOW = time.mktime((2026, 10, 12, 12, 0, 0, 0, 0, -1))

def make_snap(rows=None):
    rows = rows or {
        "C:\\": [("Users", 0, NOW, True), ("Windows", 0, NOW, True), ("readme.md", 10, NOW - 86400 * 400, False)],
        "C:\\Users": [("Yono", 0, NOW, True)],
        "C:\\Users\\Yono": [("Desktop", 0, NOW, True), ("Projects", 0, NOW, True), ("AppData", 0, NOW, True)],
        "C:\\Users\\Yono\\Desktop": [("report.docx", 2048, NOW - 3600, False), ("Отчёт за март.pdf", 6 * 1024 ** 2, NOW - 86400 * 3, False),
                                    ("IMG_0001.jpg", 300000, NOW - 86400 * 40, False), ("old_report_2020.txt", 50, NOW - 86400 * 900, False)],
        "C:\\Users\\Yono\\Projects": [("memu", 0, NOW, True), ("report", 0, NOW, True)],
        "C:\\Users\\Yono\\Projects\\memu": [("main.py", 500, NOW - 60, False), ("notes.txt", 100, NOW - 120, False)],
        "C:\\Users\\Yono\\Projects\\memu-old": [("main.py", 400, NOW - 86400 * 100, False)],
        "C:\\Users\\Yono\\AppData": [("report.docx", 1, NOW, False)],
        "C:\\Windows": [("notepad.exe", 1000, NOW - 86400 * 1000, False)],
    }
    return fsindex.Snapshot.build(rows.items(), ["C:\\"])

def names(index, query, limit=50, **kw):
    parsed = fq.parse(query, now=NOW, **kw)
    return [r["name"] for r in index.search(parsed, limit=limit, deadline=time.perf_counter() + 5)["results"]]

@pytest.fixture()
def idx():
    return fsindex.Index(make_snap())

@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(fsindex, "_SERVICE", None)
    monkeypatch.setattr(fw, "everything_info", lambda: {"installed": False, "running": False, "via": ""})
    yield tmp_path
    if fsindex._SERVICE is not None:
        fsindex._SERVICE.stop()
    monkeypatch.setattr(fsindex, "_SERVICE", None)

def test_parse_words_glob_ext_and_or_precedence():
    q = fq.parse("report *.pdf|*.docx", now=NOW)
    group = q.groups[0]
    assert [t.kind for t in group] == ["text", "or"]
    assert fq.match(group, "report.docx", "c:/x", 1, NOW, False)
    assert not fq.match(group, "report.txt", "c:/x", 1, NOW, False)
    assert not fq.match(group, "notes.pdf", "c:/x", 1, NOW, False)

    q = fq.parse("договор аренды | rental agreement", now=NOW)
    assert len(q.groups) == 2 and [t.value for t in q.groups[1]] == ["rental", "agreement"]
    assert len(fq.parse("*.jpg | *.png").groups) == 1
    assert fq.parse("*.jpg | *.png").groups[0][0].kind == "or"
    q = fq.parse("!tmp ext:log;txt", now=NOW)
    assert fq.match(q.groups[0], "a.log", "c:", 1, NOW, False)
    assert not fq.match(q.groups[0], "tmp.log", "c:", 1, NOW, False)
    assert not fq.match(q.groups[0], "a.log", "c:", 0, NOW, True)
    assert fq.parse("*.txt").groups[0][0].lits == (".txt\n",)

def test_parse_size_and_date_ranges():
    assert fq.size_range(">5mb") == (5 * 1024 ** 2 + 1, fq.INF)
    assert fq.size_range("1kb..2kb") == (1024, 2048)
    assert fq.size_range("<=10 КБ") == (0, 10240)
    assert fq.size_range("empty") == (0, 0)
    assert fq.size_range("gigantic")[0] > 128 * 1024 ** 2
    lo, hi = fq.date_range("today", NOW)
    assert lo <= NOW < hi and hi - lo == 86400
    assert fq.date_range("сегодня", NOW) == (lo, hi)
    lo7, hi7 = fq.date_range("7d", NOW)
    assert abs(NOW - lo7 - 7 * 86400) < 1
    assert fq.date_range(">2026-10", NOW)[0] == time.mktime((2026, 11, 1, 0, 0, 0, 0, 0, -1))
    lo, hi = fq.date_range("2026-01-01..2026-02-01", NOW)
    assert lo == time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1)) and hi == time.mktime((2026, 2, 2, 0, 0, 0, 0, 0, -1))
    with pytest.raises(fq.QueryError):
        fq.parse("size:huge-ish")
    with pytest.raises(fq.QueryError):
        fq.parse("regex:(unclosed")

def test_parse_folder_terms_kinds_roots_sort():
    q = fq.parse("in:C:\\Users\\Yono main sort:date", now=NOW)
    assert q.sort == "date"
    inn = [t for t in q.groups[0] if t.kind == "in"][0]
    assert inn.folder == "c:/users/yono"
    assert inn.test("x", "c:/users/yono/projects", 0, 0, False) and not inn.test("x", "c:/users/yonox", 0, 0, False)
    assert fq.parse("folder:C:\\Temp").groups[0][0].kind == "in"
    assert fq.parse("folder:proj").groups[0][0].test("project", "c:", 0, 0, True)
    assert not fq.parse("folder:proj").groups[0][0].test("project", "c:", 0, 0, False)
    assert fq.parse("parent:D:\\A").groups[0][0].test("x", "d:/a", 0, 0, False)
    q = fq.parse("x", kinds=["image"], roots=["D:\\Photos", "E:\\"])
    kinds = [t.kind for t in q.groups[0]]
    assert "ext" in kinds and "inany" in kinds
    assert fq.parse("x", places=lambda w: "C:\\Users\\Yono\\Downloads" if w.lower() == "downloads" else "",
                    roots=["Downloads"]).groups[0][-1].folder == "c:/users/yono/downloads"

    assert fq.parse("regex:(foo|bar)\\d").groups[0][0].kind == "regex"

def test_to_everything_translation():
    ev = fq.to_everything("отчёт type:image dm:7d size:>5мб in:C:\\Docs !tmp sort:date", now=NOW)
    assert "ext:jpg;" in ev and "dm:last7days" in ev and "size:>5mb" in ev and '"C:\\Docs\\"' in ev and "!tmp" in ev
    assert "sort:" not in ev and "отчёт" in ev
    assert fq.to_everything("a", kinds=["folder"]) == "a folder:"
    assert fq.to_everything("a", roots=["C:\\A", "D:\\B"]) == 'a <"C:\\A\\"|"D:\\B\\">'
    assert fq.to_everything("regex:(a|b)x") == 'regex:"(a|b)x"'
    assert fq.to_everything("договор аренды | rental agreement") == "<<договор аренды> | <rental agreement>>"
    assert fq.to_everything("*.pdf|*.docx report") == "*.pdf | *.docx report"

def test_kind_of_and_low():
    assert fq.kind_of("a.JPG") == "image" and fq.kind_of("x", True) == "folder" and fq.kind_of("a.py") == "code"
    assert fq.kind_of("a.pdf") == "doc" and fq.kind_of("a.txt") == "text" and fq.kind_of("noext") == "file"
    assert fq.low("Ёлка") == "елка"
    blob = "\nİstanbul\nKelvin\nЁж\n".encode()
    assert len(fsindex.low_bytes(blob)) == len(blob)

def test_ranking_exact_first_user_folders_before_noise(idx):
    got = names(idx, "report")
    assert got[0] in ("report", "report.docx")
    assert got.index("report.docx") < got.index("old_report_2020.txt")
    paths = [r["path"] for r in idx.search(fq.parse("report.docx"), 10)["results"]]
    assert paths[0] == "C:\\Users\\Yono\\Desktop\\report.docx"
    assert names(idx, "отчет") == ["Отчёт за март.pdf"]

def test_filters(idx):
    assert names(idx, "*.txt") == ["notes.txt", "old_report_2020.txt"]
    assert set(names(idx, "ext:jpg;pdf")) == {"IMG_0001.jpg", "Отчёт за март.pdf"}
    assert names(idx, "size:>5mb") == ["Отчёт за март.pdf"]
    assert set(names(idx, "dm:today")) >= {"report.docx", "main.py"}
    assert "old_report_2020.txt" not in names(idx, "report dm:7d")
    assert set(names(idx, "folder: memu")) == {"memu"}
    assert set(names(idx, "in:C:\\Users\\Yono\\Projects")) == {"memu", "report", "main.py", "notes.txt"}
    assert names(idx, "parent:C:\\Users\\Yono\\Projects\\memu sort:name") == ["main.py", "notes.txt"]
    assert names(idx, "main !path:memu-old") == ["main.py"]
    assert names(idx, "main") == ["main.py", "main.py"]

    assert set(names(idx, "projects/memu")) == {"main.py", "notes.txt", "memu"}
    assert names(idx, "memu/not") == ["notes.txt"]
    assert set(names(idx, "path:windows")) == {"Windows", "notepad.exe"}
    assert names(idx, "regex:^img_\\d+") == ["IMG_0001.jpg"]
    assert names(idx, "regex:(foo|ПК|0001)") == ["IMG_0001.jpg"]
    assert names(idx, "img | notes sort:name") == ["IMG_0001.jpg", "notes.txt"]
    assert set(names(idx, "отчёт март | old report")) == {"Отчёт за март.pdf", "old_report_2020.txt"}
    assert names(idx, "sort:size *.*")[0] == "Отчёт за март.pdf"
    assert names(idx, "x", kinds=["folder"]) == []
    assert set(names(idx, "*", kinds=["code"])) == {"main.py"}

def test_find_delta_and_compaction(idx):
    snap = idx.snap
    i = snap.find("C:\\Users\\Yono\\Desktop\\REPORT.docx")
    assert i >= 0 and snap.path(i) == "C:\\Users\\Yono\\Desktop\\report.docx"
    assert snap.find("C:\\nope\\x") == -1
    idx.add("C:\\Users\\Yono\\Desktop\\new plan.xlsx", 10, NOW, False)
    assert names(idx, "plan") == ["new plan.xlsx"]
    idx.remove("C:\\Users\\Yono\\Projects")
    assert names(idx, "main") == [] and names(idx, "notes") == []
    assert sorted(names(idx, "report")) == ["old_report_2020.txt", "report.docx", "report.docx"]
    idx.update("C:\\Users\\Yono\\Desktop\\IMG_0001.jpg", 99 * 1024 ** 2, NOW)
    assert names(idx, "size:>50mb") == ["IMG_0001.jpg"]
    fresh = idx.compacted()
    assert len(fresh) == len(idx) and not fresh.added and not fresh.deleted
    assert names(fresh, "plan") == ["new plan.xlsx"] and names(fresh, "notes") == []

def test_save_load_roundtrip(tmp_path):
    snap = make_snap()
    snap.save(tmp_path / "i.bin")
    back = fsindex.Snapshot.load(tmp_path / "i.bin")
    assert back.blob == snap.blob and list(back.byname) == list(snap.byname) and back.dirs == snap.dirs
    assert names(fsindex.Index(back), "отчет") == ["Отчёт за март.pdf"]
    raw = (tmp_path / "i.bin").read_bytes()
    (tmp_path / "bad.bin").write_bytes(raw[:-7])
    with pytest.raises(Exception):
        fsindex.Snapshot.load(tmp_path / "bad.bin")

def test_walker_exclusions_and_hidden(tmp_path):
    (tmp_path / "a" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "a" / "keep").mkdir()
    (tmp_path / "a" / "keep" / "f.txt").write_text("1")
    (tmp_path / "a" / "node_modules" / "x" / "skip.txt").write_text("1")
    (tmp_path / "a" / ".hidden").write_text("1")
    try:
        os.symlink(tmp_path / "a" / "keep", tmp_path / "a" / "link", target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    rules = fsindex.Exclusions(["node_modules", str(tmp_path / "a" / "keep2")])
    batches = fsindex.walk([str(tmp_path / "a")], rules)
    snap = fsindex.Snapshot.build(batches, [str(tmp_path)])
    all_names = {snap.name(i) for i in range(len(snap))}
    assert {"keep", "f.txt", ".hidden", "link"} <= all_names and "skip.txt" not in all_names and "node_modules" not in all_names
    batches = fsindex.walk([str(tmp_path / "a")], rules, hidden=False)
    assert ".hidden" not in {n for _f, kids in batches for n, *_ in kids}

def test_service_build_events_and_search_ex(data_dir):
    root = data_dir / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "Report 2026.pdf").write_bytes(b"x" * 10)
    fsindex.save_settings({"roots": [str(root)], "live": False})
    fsindex.rebuild(wait=True)
    got = fsindex.search_ex("report", limit=5)
    assert got["backend"] == "own" and [r["name"] for r in got["results"]] == ["Report 2026.pdf"]
    assert set(fsindex.search("report")[0]) >= {"path", "name", "size", "mtime", "is_dir"}
    new = root / "docs" / "fresh.txt"
    new.write_text("hi")
    fsindex.service().apply_events([("created", str(new), "")])
    assert [r["name"] for r in fsindex.search("fresh")] == ["fresh.txt"]
    renamed = root / "docs" / "renamed.txt"
    new.rename(renamed)
    fsindex.service().apply_events([("renamed", str(new), str(renamed))])
    assert fsindex.search("fresh") == [] and [r["name"] for r in fsindex.search("renamed")] == ["renamed.txt"]
    renamed.unlink()
    assert fsindex.search("renamed") == []
    st = fsindex.status()
    assert st["ready"] and st["own"]["state"] == "ready" and st["own"]["file_bytes"] > 0 and st["owner"]
    assert fsindex.search_ex("")["code"] == "empty"
    assert fsindex.search_ex("size:lots")["code"] == "query"

    assert fsindex.claim_owner() is True
    owner = json.loads((fsindex.data_dir() / "owner.json").read_text())
    assert owner["pid"] == os.getpid()

def test_second_process_is_not_owner(data_dir, monkeypatch):
    path = fsindex.data_dir() / "owner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getppid(), "at": time.time()}))
    assert fsindex.claim_owner() is False
    path.write_text(json.dumps({"pid": os.getppid(), "at": time.time() - 1000}))
    assert fsindex.claim_owner() is True

def test_settings_normalize():
    s = fsindex.normalize_settings({"backend": "weird", "roots": ["  ", "D:\\"], "rescan_minutes": "5000", "ai_tool": "false"})
    assert s["backend"] == "auto" and s["roots"] == ["D:\\"] and s["rescan_minutes"] == 1440 and s["ai_tool"] is False

def _list2(items, req):
    head = struct.pack("<IIIII", 1234, len(items), 0, req, 1)
    table = b""
    data = b""
    base = 20 + 8 * len(items)
    for flags, fields in items:
        table += struct.pack("<II", flags, base + len(data))
        for value in fields:
            if isinstance(value, str):
                enc = value.encode("utf-16-le")
                data += struct.pack("<I", len(value)) + enc + b"\0\0"
            else:
                data += struct.pack("<Q", value)
    return head + table + data

def test_everything_ipc_reply_and_query_layout():
    ft = int((NOW + 11644473600) * 10_000_000)
    req = fw.REQ_NAME | fw.REQ_PATH | fw.REQ_SIZE | fw.REQ_DATE_MODIFIED
    buf = _list2([(0, ["Отчёт.pdf", "C:\\Docs", 4096, ft]), (fw.IPC_FOLDER, ["Docs", "C:\\", 0xFFFFFFFFFFFFFFFF, ft])], req)
    tot, rows = fw.parse_ipc_list2(buf)
    assert tot == 1234 and rows[0]["path"] == "C:\\Docs\\Отчёт.pdf" and rows[0]["size"] == 4096
    assert abs(rows[0]["mtime"] - NOW) < 1 and rows[1]["is_dir"] and rows[1]["size"] is None and rows[1]["path"] == "C:\\Docs"
    payload = fw.build_query2(0x1_0000_00AB, "*.txt", 50, req, 14)
    fields = struct.unpack_from("<IIIIIII", payload)
    assert fields == (0xAB, fw.REPLY_ID, 0, 0, 50, req, 14) and payload[28:].decode("utf-16-le") == "*.txt\0"

def test_everything_search_with_mocks(monkeypatch):
    rows = [{"path": "C:\\a\\old_report.txt", "name": "old_report.txt", "dir": "C:\\a", "size": 1, "mtime": NOW, "is_dir": False},
            {"path": "C:\\Users\\Y\\AppData\\report", "name": "report", "dir": "C:\\Users\\Y\\AppData", "size": None, "mtime": NOW, "is_dir": True},
            {"path": "C:\\Users\\Y\\report.docx", "name": "report.docx", "dir": "C:\\Users\\Y", "size": 5, "mtime": NOW, "is_dir": False}]
    monkeypatch.setattr(fw, "ipc_query", lambda q, limit, sort, timeout=3.0: (3, [dict(r) for r in rows]))
    got = fw.everything_search("report", limit=10, info={"running": True, "via": "ipc", "dll": "", "es": ""})
    assert got["via"] == "ipc" and [r["name"] for r in got["results"]] == ["report.docx", "report", "old_report.txt"]
    def boom(*a, **k):
        raise RuntimeError("no window")
    monkeypatch.setattr(fw, "ipc_query", boom)
    monkeypatch.setattr(fw, "es_query", lambda es, q, limit, sort: (1, [dict(rows[2])]))
    got = fw.everything_search("x", info={"running": True, "via": "ipc", "dll": "", "es": "C:\\es.exe"})
    assert got["via"] == "es" and got["results"][0]["kind"] == "doc"
    with pytest.raises(RuntimeError):
        fw.everything_search("x", info={"running": False, "dll": "", "es": ""})

def test_search_ex_prefers_everything_and_falls_back(data_dir, monkeypatch):
    calls = []
    svc = fsindex.service()
    svc.index = fsindex.Index(make_snap())
    svc.state = "ready"
    monkeypatch.setattr(fsindex, "IS_WINDOWS", True)
    monkeypatch.setattr(svc, "use_everything", lambda: True)
    monkeypatch.setattr(svc, "everything", lambda fresh=False: {"running": True, "via": "ipc"})
    def ev(query, limit=200, sort="relevance", info=None, timeout=3.0, rank_query=""):
        calls.append(query)
        return {"results": [{"path": "C:\\x\\a.txt", "name": "a.txt", "dir": "C:\\x", "size": 1, "mtime": NOW, "is_dir": False, "kind": "text"}],
                "total": 1, "capped": False, "partial": False, "via": "ipc"}
    monkeypatch.setattr(fw, "everything_search", ev)
    got = fsindex.search_ex("a type:text in:C:\\x", validate=False)
    assert got["backend"] == "everything" and calls and "ext:txt" in calls[0] and '"C:\\x\\"' in calls[0]
    def broken(*a, **k):
        raise RuntimeError("ipc timeout")
    monkeypatch.setattr(fw, "everything_search", broken)
    got = fsindex.search_ex("notes", validate=False)
    assert got["backend"] == "own" and [r["name"] for r in got["results"]] == ["notes.txt"]

def _usn(frn, parent, name, attrs=0, reason=0, major=2):
    enc = name.encode("utf-16-le")
    if major == 2:
        rec = fw._V2.pack(60 + len(enc), 2, 0, frn, parent, 0, 0, reason, 0, 0, attrs, len(enc), 60) + enc
    else:
        rec = (struct.pack("<IHH", 76 + len(enc), 3, 0) + struct.pack("<QQ", frn, 0) + struct.pack("<QQ", parent, 0)
               + struct.pack("<qq", 0, 0) + struct.pack("<IIIIHH", reason, 0, 0, attrs, len(enc), 76) + enc)
    pad = (8 - len(rec) % 8) % 8
    rec = rec + b"\0" * pad
    return struct.pack("<I", len(rec)) + rec[4:]

ROOT_FRN = (1 << 48) | 5

def test_usn_records_and_mft_batches():
    D = fw.FILE_ATTRIBUTE_DIRECTORY
    users, yono, skip = (2 << 48) | 100, (3 << 48) | 101, (1 << 48) | 102
    buf = struct.pack("<Q", 999) + b"".join([
        _usn(users, ROOT_FRN, "Users", D), _usn(yono, users, "Yono", D, major=3), _usn(200, yono, "a.txt"),
        _usn(skip, ROOT_FRN, "$Recycle.Bin", D), _usn(201, skip, "trash.txt"), _usn(202, 7777, "orphan.txt"),
        _usn(203, ROOT_FRN, "boot.ini", fw.FILE_ATTRIBUTE_HIDDEN)])
    recs = list(fw.parse_usn_records(buf))
    assert [r[2] for r in recs][:3] == ["Users", "Yono", "a.txt"] and recs[1][0] == yono
    rules = fsindex.Exclusions(["$Recycle.Bin"])
    batches, dirs = fw.batches_from_records([r[:4] for r in recs], "C:\\", rules)
    got = {folder: sorted(n for n, *_ in kids) for folder, kids in batches}
    assert got == {"C:\\": ["Users", "boot.ini"], "C:\\Users": ["Yono"], "C:\\Users\\Yono": ["a.txt"]}
    assert dict(batches)["C:\\Users\\Yono"][0][1] == -1
    batches, _ = fw.batches_from_records([r[:4] for r in recs], "C:\\", rules, hidden=False)
    assert "boot.ini" not in {n for _f, kids in batches for n, *_ in kids}
    snap = fsindex.Snapshot.build(batches, ["C:\\"], "mft")
    assert snap.path(snap.find("C:\\Users\\Yono\\a.txt")) == "C:\\Users\\Yono\\a.txt"

def test_usn_journal_events():
    D = fw.FILE_ATTRIBUTE_DIRECTORY
    dirs = {(2 << 48) | 100: (ROOT_FRN, "Docs")}
    docs = (2 << 48) | 100
    pending = {}
    C = fw.USN_REASON_CLOSE
    recs = [(300, docs, "new.txt", 0, fw.USN_REASON_FILE_CREATE | C),
            (301, docs, "old.txt", 0, fw.USN_REASON_RENAME_OLD_NAME),
            (301, docs, "new name.txt", 0, fw.USN_REASON_RENAME_NEW_NAME),
            (301, docs, "new name.txt", 0, fw.USN_REASON_RENAME_NEW_NAME | C),
            (302, docs, "gone.txt", 0, fw.USN_REASON_FILE_DELETE | C),
            (303, docs, "sub", D, fw.USN_REASON_FILE_CREATE | C),
            (304, 303, "in-sub.txt", 0, fw.USN_REASON_FILE_CREATE | C),
            (305, docs, "data.bin", 0, fw.USN_REASON_DATA_EXTEND | C),
            (306, docs, "temp.tmp", 0, fw.USN_REASON_FILE_CREATE | fw.USN_REASON_FILE_DELETE | C)]
    events = fw.usn_events(recs, dirs, "C:\\", pending)
    assert events == [("created", "C:\\Docs\\new.txt", ""), ("renamed", "C:\\Docs\\old.txt", "C:\\Docs\\new name.txt"),
                      ("deleted", "C:\\Docs\\gone.txt", ""), ("created", "C:\\Docs\\sub", ""),
                      ("created", "C:\\Docs\\sub\\in-sub.txt", ""), ("modified", "C:\\Docs\\data.bin", "")]

def test_notify_buffer_and_es_output():
    def fni(action, name, last=False):
        enc = name.encode("utf-16-le")
        size = 12 + len(enc)
        size += (4 - size % 4) % 4
        body = struct.pack("<III", 0 if last else size, action, len(enc)) + enc
        return body + b"\0" * (size - len(body))
    buf = fni(1, "a\\new.txt") + fni(3, "a\\new.txt") + fni(3, "a\\new.txt") + fni(4, "old") + fni(5, "neu") + fni(2, "x", True)
    assert fw.parse_notify(buf, "D:\\") == [("created", "D:\\a\\new.txt", ""), ("modified", "D:\\a\\new.txt", ""),
                                            ("renamed", "D:\\old", "D:\\neu"), ("deleted", "D:\\x", "")]
    rows = fw.parse_es_output('Filename,Size,Date Modified,Attributes\n"C:\\A\\b c.txt",12,2026-10-12T10:00:00,A\n"C:\\A\\d",,2026-10-12T10:00:00,D\n')
    assert rows[0]["path"] == "C:\\A\\b c.txt" and rows[0]["size"] == 12 and rows[1]["is_dir"] and rows[0]["mtime"]
    assert fw.parse_es_output("C:\\x.txt\r\nC:\\y\r\n") == [{"path": "C:\\x.txt"}, {"path": "C:\\y"}]
    assert fw.filetime_to_epoch(0) is None and abs(fw.filetime_to_epoch(116444736000000000)) < 1e-6

def test_windows_functions_are_safe_off_windows():
    if sys.platform == "win32":
        pytest.skip("Linux-only guard check")
    assert fw.find_everything_window() == 0 and fw.drives() == [] and fw.is_admin() is False
    assert fw.is_ntfs_root("C:\\") is False
    info = fw.everything_info()
    assert info["running"] is False and info["installed"] is False

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import fsindex_api
    app = FastAPI()
    app.include_router(fsindex_api.router)
    return TestClient(app), fsindex_api

def test_tool_and_routes(data_dir):
    root = data_dir / "root"
    (root / "x").mkdir(parents=True)
    (root / "x" / "Budget 2026.xlsx").write_text("1")
    fsindex.save_settings({"roots": [str(root)], "live": False})
    fsindex.rebuild(wait=True)
    client, api = _client()
    out = api.run_tool({"query": "budget", "limit": 5}, "ru", with_ui=True)
    assert out["count"] == 1 and out["results"][0]["path"].endswith("Budget 2026.xlsx") and out["ui"]["type"] == "fsresults"
    assert "size" in out["results"][0] and "modified" in out["results"][0]
    assert "hint" in api.run_tool({"query": "zzzqqq"}, "en")
    assert api.run_tool({}, "en")["error"]
    r = client.get("/api/fsindex/search", params={"q": "*.xlsx"}).json()
    assert [x["name"] for x in r["results"]] == ["Budget 2026.xlsx"] and r["drag_key"]

    assert client.post("/api/fsindex/open", json={"path": "/etc/passwd"}).status_code == 400
    path = r["results"][0]["path"]
    assert client.get("/api/fsindex/drag", params={"path": path, "k": "nope"}).status_code == 403
    got = client.get("/api/fsindex/drag", params={"path": path, "k": r["drag_key"]})
    assert got.status_code == 200 and got.content == b"1"
    st = client.get("/api/fsindex/status").json()
    assert st["own"]["entries"] >= 2 and "memufiles" in st
    assert client.post("/api/fsindex/settings", json={"settings": {"ai_tool": False}}).json()["settings"]["ai_tool"] is False
    assert "выключен" in api.run_tool({"query": "budget"}, "ru")["error"]
    fsindex.save_settings({"ai_tool": True})
    import asyncio
    res = asyncio.run(api.chat_tool({"query": "budget"}, {"lang": "en"}))
    assert res["tool"] == "search_files" and res["count"] == 1

def test_quick_tool_registered_and_prompts():
    from app import quick_ask_api, fsindex_api  # noqa: F401
    assert "search_files" in quick_ask_api.EXTRA_TOOLS
    text = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
    local = text.split("<local_tools>", 1)[1].split("</local_tools>", 1)[0]
    assert "search_files" in local and "network_status, app_version" in local
    from app.pgdata import DEFAULT_PROMPT
    assert "search_files" in DEFAULT_PROMPT.split("<local_tools>", 1)[1]
    assert "search_files" in (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
    from app import answer_guard
    assert answer_guard.TOOL_TAG.match("search_files")

def test_plugin_manifest_and_files():
    manifest = json.loads((ROOT / "app" / "pgstatic" / "quick" / "plugins" / "index.json").read_text(encoding="utf-8"))
    row = [p for p in manifest["plugins"] if isinstance(p, dict) and p.get("id") == "fsearch"][0]
    assert row["backend"] == "app.fsindex_api"
    for key in ("src", "css", "settings", "settings_css"):
        assert (ROOT / "app" / "pgstatic" / "quick" / "plugins" / row[key]).is_file()
    import re
    for name in ("fsearch.js", "fsearch-settings.js"):
        js = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / name).read_text(encoding="utf-8")

        for m in re.finditer(r'\b(?:t|tr)\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)', js):
            assert not re.search("[а-яА-ЯёЁ]", m.group(2)), (name, m.group(2))
        for m in re.finditer(r'\b(?:t|tr)\(`([^`]*)`,\s*`([^`]*)`\)', js):
            assert not re.search("[а-яА-ЯёЁ]", m.group(2)), (name, m.group(2))
    import build
    assert {"app.fsindex", "app.fsindex_query", "app.fsindex_win", "app.fsindex_api"} <= set(
        x for x in build.TARGETS["memuai"]["hidden"] if x.startswith("app.fsindex"))

def test_speed_200k():
    import random
    random.seed(3)
    words = "report invoice photo backup project memu draft final notes budget plan readme отчёт договор фото".split()
    exts = "txt pdf docx jpg png mp4 py json md zip".split()
    rows = {}
    dirs = [f"C:\\d{i}" for i in range(20000)]
    for d in dirs:
        rows[d] = []
    for i in range(200000):
        rows[dirs[i % len(dirs)]].append((f"{random.choice(words)}_{random.choice(words)}{i}.{random.choice(exts)}",
                                          i, NOW - i, False))
    rows["C:\\d5"].append(("report.docx", 1, NOW, False))
    index = fsindex.Index(fsindex.Snapshot.build(rows.items(), ["C:\\"]))
    for query in ("report", "*.pdf", "отчёт ext:pdf", "invoice budget", "zzzz", "ext:py dm:7d"):
        parsed = fq.parse(query, now=NOW)
        best = min((lambda: (lambda t0: (index.search(parsed, 50), time.perf_counter() - t0)[1])(time.perf_counter()))()
                   for _ in range(3))
        assert best < 0.15, (query, best)
    assert index.search(fq.parse("report"), 5)["results"][0]["name"] == "report.docx"
