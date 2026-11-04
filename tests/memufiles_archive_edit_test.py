"""MemuFiles archive editor: delete / rename / move / new folder inside zip (raw copy keeps encryption, methods,
legacy names, data descriptors), tar.gz (links follow), 7z (through 7-Zip); undo through the Recycle Bin; «add»
with replace / skip; integrity test; zip comment; conversion; API validation. No network."""
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

SEVEN = shutil.which("7za") or shutil.which("7z") or shutil.which("7zz")

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield

@pytest.fixture()
def app():
    from app.memufiles import server as srv
    files_app = srv.FilesApp()
    httpd, _ = srv.start(files_app)
    try:
        yield files_app
    finally:
        httpd.shutdown()

def call(app, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-MF-Token": app.token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read())

def call_err(app, path, body):
    with pytest.raises(urllib.error.HTTPError) as info:
        call(app, path, body)
    return info.value.code, json.loads(info.value.read())

def run(app, kind, sources, dest, **args):
    res = call(app, "/api/ops/submit", {"kind": kind, "sources": sources, "dest": dest, "args": args})
    op_id = res["op"]["id"]
    end = time.time() + 30
    while time.time() < end:
        op = app.queue.ops[op_id]
        if op.status in ("done", "error", "cancelled"):
            return op
        time.sleep(0.05)
    raise AssertionError("operation did not finish")

def undo(app, op):
    res = call(app, "/api/ops/undo", {"id": op.id})
    end = time.time() + 30
    while time.time() < end:
        u = app.queue.ops[res["op"]["id"]]
        if u.status in ("done", "error", "cancelled"):
            return u
        time.sleep(0.05)
    raise AssertionError("undo did not finish")

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class _Pipe:
    """Unseekable output: zipfile writes data descriptors (flag bit 3)."""
    def __init__(self):
        self.buf = io.BytesIO()
    def write(self, data):
        return self.buf.write(data)
    def flush(self):
        pass

def make_zip(path: Path) -> None:
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pack/README.md", "# hello\n" * 50)
        zf.writestr("pack/src/main.py", "print(1)\n")
        zf.writestr("pack/src/util.py", "x = 2\n")
        zf.writestr(zipfile.ZipInfo("keep/"), b"")
        info = zipfile.ZipInfo("stored.bin")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"\x00" * 1000)
        zf.writestr(_OemName("Отчёт.txt".encode("cp866").decode("cp437")), "старый")
        zf.comment = "Комментарий архива".encode("utf-8")
    path.write_bytes(pipe.buf.getvalue())

class _OemName(zipfile.ZipInfo):
    """A legacy entry: an OEM (cp866) name without the UTF-8 flag, as old Windows archivers wrote them."""
    __slots__ = ()

    def _encodeFilenameFlags(self):
        return "Отчёт.txt".encode("cp866"), self.flag_bits & ~0x800

def names(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(i.filename for i in zf.infolist())

def test_plan_moves_implicit_folders_and_conflicts():
    from app.memufiles.archive_edit import Plan
    from app.memufiles.fsops import FsError
    entries = {"a": {"d": 1}, "a/x.txt": {"d": 0}, "a/b": {"d": 1}, "a/b/y.txt": {"d": 0}, "z.txt": {"d": 0}}
    plan = Plan(entries, [{"do": "rename", "from": "a", "to": "docs/a2"}, {"do": "delete", "path": "docs/a2/b"},
                          {"do": "mkdir", "path": "docs/a2/new"}])
    assert plan.map("a/x.txt") == "docs/a2/x.txt" and plan.map("a/b/y.txt") is None and plan.map("z.txt") == "z.txt"
    assert plan.map("a/link-not-indexed") == "docs/a2/link-not-indexed" and plan.created == ["docs/a2/new"]
    for actions, code in (([{"do": "rename", "from": "z.txt", "to": "a"}], "exists"),
                          ([{"do": "delete", "path": "nope"}], "not_found"),
                          ([{"do": "mkdir", "path": "z.txt/inner"}], "exists"),
                          ([{"do": "mkdir", "path": "a/b"}], "exists")):
        with pytest.raises(FsError) as err:
            Plan(entries, actions)
        assert err.value.code == code
    assert not Plan(entries, [{"do": "rename", "from": "a", "to": "a"}] and []).changed

def test_clean_actions_rejects_bad_input():
    from app.memufiles.archive_edit import clean_actions
    from app.memufiles.fsops import FsError
    assert clean_actions([{"do": "delete", "path": "../../etc/passwd"}]) == [{"do": "delete", "path": "etc/passwd"}]
    assert clean_actions([{"do": "rename", "from": "a\\b", "to": "c/./d"}]) == [{"do": "rename", "from": "a/b", "to": "c/d"}]
    for bad, code in (([], "nothing_selected"), ("x", "nothing_selected"), ([{"do": "chmod", "path": "a"}], "bad_action"),
                      ([{"do": "delete", "path": "/"}], "bad_action"), ([{"do": "rename", "from": "a", "to": "a/b"}], "into_self"),
                      ([{"do": "mkdir", "path": "a:b"}], "invalid_chars"), ([{"do": "rename", "from": "a", "to": "b?"}], "invalid_chars")):
        with pytest.raises(FsError) as err:
            clean_actions(bad)
        assert err.value.code == code

def test_zip_edit_keeps_raw_members_and_undo(tmp_path, app):
    z = tmp_path / "pack.zip"
    make_zip(z)
    before = sha(z)
    with zipfile.ZipFile(z) as zf:
        assert zf.getinfo("pack/README.md").flag_bits & 0x08   # data descriptor entries
    op = run(app, "arcedit", [str(z)], str(z), actions=[
        {"do": "delete", "path": "stored.bin"}, {"do": "rename", "from": "pack/src", "to": "pack/source"},
        {"do": "mkdir", "path": "pack/new folder"}, {"do": "rename", "from": "pack/README.md", "to": "README.md"}])
    assert op.status == "done" and not op.errors, op.errors
    assert names(z) == sorted(["README.md", "pack/source/main.py", "pack/source/util.py", "keep/", "pack/new folder/",
                               "Отчёт.txt".encode("cp866").decode("cp437")])
    with zipfile.ZipFile(z) as zf:
        assert zf.testzip() is None
        assert zf.read("README.md") == b"# hello\n" * 50 and zf.read("pack/source/main.py") == b"print(1)\n"
        legacy = [i for i in zf.infolist() if not i.flag_bits & 0x800 and i.filename.endswith(".txt")][0]
        assert legacy.orig_filename.encode("cp437") == "Отчёт.txt".encode("cp866")   # bytes kept
        assert zf.comment.decode("utf-8") == "Комментарий архива"
    head = call(app, "/api/arc/info?path=" + urllib.request.quote(str(z)))
    assert head["writable"] and head["files"] == 4
    assert op.snapshot()["undoable"]
    assert not list(tmp_path.glob("*.partial")) and not [p for p in os.listdir(tmp_path) if p.startswith("pack (")]
    back = undo(app, op)
    assert back.status == "done" and not back.errors, back.errors
    assert sha(z) == before
    assert not [p for p in os.listdir(tmp_path) if p.startswith("pack (")]

@pytest.mark.skipif(not SEVEN, reason="no 7-Zip")
def test_zip_edit_keeps_encrypted_members(tmp_path, app):
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "secret.txt").write_text("top secret " * 20)
    (src / "docs" / "other.txt").write_text("other")
    z = tmp_path / "locked.zip"
    subprocess.run([SEVEN, "a", "-tzip", "-pSECRET", "-mem=ZipCrypto", str(z), "docs"], cwd=src, check=True,
                   capture_output=True)
    op = run(app, "arcedit", [str(z)], str(z), actions=[{"do": "delete", "path": "docs/other.txt"},
                                                         {"do": "rename", "from": "docs", "to": "papers"}])
    assert op.status == "done", op.errors
    with zipfile.ZipFile(z) as zf:
        assert zf.read("papers/secret.txt", pwd=b"SECRET") == b"top secret " * 20
        assert zf.getinfo("papers/secret.txt").flag_bits & 0x1
    out = subprocess.run([SEVEN, "t", "-pSECRET", str(z)], capture_output=True)
    assert out.returncode == 0, out.stdout

def make_tar(path: Path, base: Path) -> None:
    root = base / "tsrc"
    (root / "dir" / "sub").mkdir(parents=True)
    (root / "dir" / "a.txt").write_text("A" * 100)
    (root / "dir" / "sub" / "b.txt").write_text("B")
    (root / "top.txt").write_text("top")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(root / "dir", arcname="dir")
        tf.add(root / "top.txt", arcname="top.txt")
        link = tarfile.TarInfo("hard.txt")
        link.type, link.linkname = tarfile.LNKTYPE, "top.txt"
        tf.addfile(link)
        sym = tarfile.TarInfo("dir/sub/sym")
        sym.type, sym.linkname = tarfile.SYMTYPE, "b.txt"
        tf.addfile(sym)

def test_tar_gz_edit_links_and_undo(tmp_path, app):
    t = tmp_path / "data.tar.gz"
    make_tar(t, tmp_path)
    before = sha(t)
    op = run(app, "arcedit", [str(t)], str(t), actions=[{"do": "rename", "from": "dir/sub", "to": "dir/renamed"},
                                                         {"do": "delete", "path": "top.txt"},
                                                         {"do": "mkdir", "path": "empty"}])
    assert op.status == "done" and not op.errors, op.errors
    assert t.read_bytes()[:2] == b"\x1f\x8b"
    with tarfile.open(t) as tf:
        members = {m.name: m for m in tf.getmembers()}
        assert "dir/renamed/b.txt" in members and "dir/sub/b.txt" not in members and "top.txt" not in members
        assert members["dir/renamed/sym"].issym() and members["empty"].isdir()
        assert members["hard.txt"].isfile() and tf.extractfile("hard.txt").read() == b"top"
    assert undo(app, op).status == "done" and sha(t) == before

def test_tar_rename_hardlink_target(tmp_path, app):
    t = tmp_path / "data.tar"
    make_tar(tmp_path / "x.tar.gz", tmp_path)
    with tarfile.open(tmp_path / "x.tar.gz") as src, tarfile.open(t, "w") as dst:
        for m in src:
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    op = run(app, "arcedit", [str(t)], str(t), actions=[{"do": "rename", "from": "top.txt", "to": "dir/top2.txt"}])
    assert op.status == "done", op.errors
    with tarfile.open(t) as tf:
        assert tf.getmember("hard.txt").linkname == "dir/top2.txt" and tf.extractfile("hard.txt").read() == b"top"

@pytest.mark.skipif(not SEVEN, reason="no 7-Zip")
def test_7z_edit(tmp_path, app):
    src = tmp_path / "s"
    (src / "dir" / "sub").mkdir(parents=True)
    (src / "dir" / "a.txt").write_text("a")
    (src / "dir" / "sub" / "b.txt").write_text("b")
    (src / "c*.txt").write_text("c")
    s = tmp_path / "x.7z"
    subprocess.run([SEVEN, "a", str(s), "dir", "c*.txt"], cwd=src, check=True, capture_output=True)
    op = run(app, "arcedit", [str(s)], str(s), actions=[{"do": "delete", "path": "c*.txt"},
                                                         {"do": "rename", "from": "dir", "to": "newdir"},
                                                         {"do": "mkdir", "path": "newdir/fresh"}])
    assert op.status == "done" and not op.errors, (op.errors, op.message)
    listing = subprocess.run([SEVEN, "l", "-ba", "-slt", str(s)], capture_output=True).stdout.decode()
    paths = sorted(l[7:] for l in listing.splitlines() if l.startswith("Path = "))
    assert paths == ["newdir", "newdir/a.txt", "newdir/fresh", "newdir/sub", "newdir/sub/b.txt"]
    assert undo(app, op).status == "done"
    listing = subprocess.run([SEVEN, "l", "-ba", "-slt", str(s)], capture_output=True).stdout.decode()
    assert "Path = c*.txt" in listing

def test_add_replace_skip_and_tar_without_7zip(tmp_path, app, monkeypatch):
    z = tmp_path / "pack.zip"
    make_zip(z)
    new = tmp_path / "in" / "main.py"
    new.parent.mkdir()
    new.write_bytes(b"print(2)\n")
    op = run(app, "arcadd", [str(new)], str(z), inner="pack/src")
    assert op.errors and op.errors[0]["error"] == "exists"
    op = run(app, "arcadd", [str(new)], str(z), inner="pack/src", on_conflict="skip")
    assert op.status == "done" and not op.errors
    op = run(app, "arcadd", [str(new)], str(z), inner="pack/src", on_conflict="replace")
    assert op.status == "done" and not op.errors, op.errors
    with zipfile.ZipFile(z) as zf:
        assert zf.read("pack/src/main.py") == b"print(2)\n" and zf.testzip() is None
        assert [i.filename for i in zf.infolist()].count("pack/src/main.py") == 1
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue.OpQueue, "seven_zip", staticmethod(lambda: ""))
    t = tmp_path / "data.tar.gz"
    make_tar(t, tmp_path)
    assert call(app, "/api/arc/info?path=" + urllib.request.quote(str(t)))["writable"]
    op = run(app, "arcadd", [str(new)], str(t), inner="dir")
    assert op.status == "done" and not op.errors, op.errors
    with tarfile.open(t) as tf:
        assert tf.extractfile("dir/main.py").read() == b"print(2)\n"

def test_arctest_reports_damaged_members(tmp_path, app):
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("good.txt", "fine" * 100)
        zf.writestr("bad.txt", "X" * 400)
    op = run(app, "arctest", [str(z)], "")
    assert op.status == "done" and op.message == "ok" and not op.errors
    raw = bytearray(z.read_bytes())
    at = raw.find(b"X" * 400)
    raw[at + 10] = ord("Y")
    z.write_bytes(bytes(raw))
    op = run(app, "arctest", [str(z)], "")
    assert op.message == "damaged" and [e["path"] for e in op.errors] == ["bad.txt"]
    t = tmp_path / "cut.tar.gz"
    make_tar(t, tmp_path)
    t.write_bytes(t.read_bytes()[:-40])
    op = run(app, "arctest", [str(t)], "")
    assert op.errors and op.message == "damaged"

def test_zip_comment_api(tmp_path, app):
    z = tmp_path / "pack.zip"
    make_zip(z)
    got = call(app, "/api/arc/comment?path=" + urllib.request.quote(str(z)))
    assert got["supported"] and got["writable"] and got["comment"] == "Комментарий архива"
    got = call(app, "/api/arc/comment", {"path": str(z), "comment": "Новый\r\nкомментарий"})
    assert got["comment"] == "Новый\nкомментарий"
    with zipfile.ZipFile(z) as zf:
        assert zf.testzip() is None and zf.comment.decode() == "Новый\nкомментарий"
    t = tmp_path / "d.tar"
    with tarfile.open(t, "w") as tf:
        tf.addfile(tarfile.TarInfo("e"))
    assert call(app, "/api/arc/comment?path=" + urllib.request.quote(str(t)))["supported"] is False
    code, body = call_err(app, "/api/arc/comment", {"path": str(t), "comment": "x"})
    assert code == 400 and body["error"] == "archive_readonly"

def test_convert_zip_to_tar_xz_and_7z(tmp_path, app):
    z = tmp_path / "pack.zip"
    make_zip(z)
    op = run(app, "arcconvert", [str(z)], str(tmp_path / "pack.tar.xz"), format="tar.xz")
    assert op.status == "done", op.errors
    with tarfile.open(tmp_path / "pack.tar.xz") as tf:
        got = {m.name for m in tf.getmembers() if m.isfile()}
        assert "pack/src/main.py" in got and "stored.bin" in got
        assert tf.extractfile("pack/README.md").read() == b"# hello\n" * 50
    assert z.exists()
    if SEVEN:
        op = run(app, "arcconvert", [str(tmp_path / "pack.tar.xz")], str(tmp_path / "pack.7z"), format="7z")
        assert op.status == "done", op.errors
        listing = subprocess.run([SEVEN, "l", "-ba", "-slt", str(tmp_path / "pack.7z")], capture_output=True).stdout.decode()
        assert "Path = pack/src/main.py" in listing

def test_api_validation(tmp_path, app):
    z = tmp_path / "pack.zip"
    make_zip(z)
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    rar = tmp_path / "x.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00")
    for body, code, err in (
            ({"kind": "arcedit", "sources": [str(z)], "dest": str(z), "args": {"actions": [{"do": "zap", "path": "a"}]}}, 400, "bad_action"),
            ({"kind": "arcedit", "sources": [str(z)], "dest": str(z), "args": {}}, 400, "nothing_selected"),
            ({"kind": "arcedit", "sources": [str(plain)], "dest": str(plain), "args": {"actions": [{"do": "delete", "path": "a"}]}}, 400, "not_an_archive"),
            ({"kind": "arcedit", "sources": [str(rar)], "dest": str(rar), "args": {"actions": [{"do": "delete", "path": "a"}]}}, 400, "archive_readonly"),
            ({"kind": "arcconvert", "sources": [str(z)], "dest": str(tmp_path / "o.zip"), "args": {"format": "rar"}}, 400, "bad_format"),
            ({"kind": "arcconvert", "sources": [str(z)], "dest": os.path.join(str(z), "in.zip"), "args": {"format": "zip"}}, 400, "bad_destination"),
            ({"kind": "arctest", "sources": [str(plain)], "dest": ""}, 400, "not_an_archive")):
        got_code, got = call_err(app, "/api/ops/submit", body)
        assert (got_code, got["error"]) == (code, err), (body, got)
    op = run(app, "arcedit", [str(z)], str(z), actions=[{"do": "rename", "from": "stored.bin", "to": "keep"}])
    assert op.status == "error" and op.message == "exists"
    op = run(app, "arcedit", [str(z)], str(z), actions=[{"do": "delete", "path": "missing.txt"}])
    assert op.status == "error" and op.message == "not_found"
    assert not list(tmp_path.glob("*.partial"))
