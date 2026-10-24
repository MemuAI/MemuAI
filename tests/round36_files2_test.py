"""Round 36 (r36-files-2): MemuFiles second-wave fixes — ZIP edge cases, safe extraction, open-with worker,
free-model tool calls, breadcrumb/menus contracts. No network, no Windows needed."""
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    from app.memufiles import opqueue
    return opqueue.OpQueue()


def _wait(op, timeout=20.0):
    end = time.time() + timeout
    while op.status not in ("done", "error", "cancelled", "conflict") and time.time() < end:
        time.sleep(0.02)
    return op


def test_compress_file_older_than_1980_and_no_forced_zip64(queue, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    old = src / "старый.txt"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (100000, 100000))                       # 1970: ZipInfo.from_file used to raise ValueError
    op = _wait(queue.submit("compress", [str(old)], str(tmp_path / "a.zip")))
    assert op.status == "done", op.errors
    with zipfile.ZipFile(tmp_path / "a.zip") as zf:
        info = zf.infolist()[0]
        assert info.filename == "старый.txt" and info.flag_bits & 0x800
        assert info.date_time[0] == 1980
        assert b"\x01\x00" not in info.extra[:2]         # no Zip64 extra field on a tiny file
        assert zf.read(info) == b"old"


def test_extract_cancel_removes_partial_and_keeps_replaced_original(queue, tmp_path, monkeypatch):
    from app.memufiles import opqueue
    monkeypatch.setattr(opqueue, "CHUNK", 64 * 1024)
    original_check = opqueue.Operation.check

    def slow(self):
        time.sleep(0.003)
        original_check(self)
    monkeypatch.setattr(opqueue.Operation, "check", slow)
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("big.bin", os.urandom(1024) * 12000)
    op = queue.submit("extract", [str(archive)], str(tmp_path / "out"))
    time.sleep(0.25)
    queue.cancel(op.id)
    assert _wait(op).status == "cancelled"
    assert os.listdir(tmp_path / "out") == []               # no half-written big.bin
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "big.bin").write_bytes(b"keep")
    op = queue.submit("extract", [str(archive)], str(dest))
    assert _wait(op).status == "conflict"
    queue.resolve(op.id, "replace")
    time.sleep(0.25)
    queue.cancel(op.id)
    assert _wait(op).status == "cancelled"
    assert (dest / "big.bin").read_bytes() == b"keep"        # "Replace" + cancel no longer destroys the old file
    assert sorted(os.listdir(dest)) == ["big.bin"]


def test_extract_replace_completes(queue, tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("f.txt", "new")
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "f.txt").write_text("old")
    op = queue.submit("extract", [str(archive)], str(dest), on_conflict="replace")
    assert _wait(op).status == "done"
    assert (dest / "f.txt").read_text() == "new" and sorted(os.listdir(dest)) == ["f.txt"]


def test_sta_worker_skips_abandoned_jobs():
    from app.memufiles import openwith
    worker = openwith._StaWorker()
    gate = threading.Event()
    ran = []
    threading.Thread(target=lambda: worker.call(lambda: gate.wait(5), timeout=6), daemon=True).start()
    time.sleep(0.1)
    with pytest.raises(openwith.OpenWithError):
        worker.call(lambda: ran.append("late launch"), timeout=0.2)
    gate.set()
    assert worker.call(lambda: "next") == "next"
    assert ran == []                                         # a timed-out launch never runs later


def test_default_handler_matched_by_friendly_name_for_store_apps():
    from app.memufiles import openwith
    items = [{"id": "shell:Microsoft.Windows.Photos_8wekyb3d8bbwe!App", "name": "Фотографии", "exe": "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"},
             {"id": r"shell:C:\Paint\paint.exe", "name": "Paint", "exe": r"C:\Paint\paint.exe"}]
    out = openwith._dedupe_and_sort(items, "", "Фотографии")
    assert out[0]["name"] == "Фотографии" and out[0]["default"] and not out[1]["default"]
    out = openwith._dedupe_and_sort([dict(i) for i in items], r"C:\Paint\paint.exe", "Фотографии")
    assert out[0]["name"] == "Paint" and [i["default"] for i in out] == [True, False]


def test_icon_for_unknown_handler_is_not_cached(monkeypatch):
    from app.memufiles import openwith
    monkeypatch.setattr(openwith, "IS_WIN", True)
    openwith._ICON_PNG.pop("shell:nope", None)
    assert openwith.icon_png("shell:nope") is None
    assert "shell:nope" not in openwith._ICON_PNG


def test_free_model_pythonic_tool_call_is_parsed_and_hidden():
    from app.memufiles.assistant import HoldBack, parse_tool
    text = "ok <\u200b|tool_call_start|>[find_files(root='/tmp/x', recursive=True, ext=['txt'], min_size='5kb')]<\u200b|tool_call_end|>"
    call = parse_tool(text)
    assert call["name"] == "find_files" and call["args"] == {"root": "/tmp/x", "recursive": True, "ext": ["txt"], "min_size": "5kb"}
    hold = HoldBack()
    assert hold.feed(text[:5]) + hold.feed(text[5:]) == "ok "
    assert parse_tool("<|tool_call_start|>[find_files(root=open('x'))]<|tool_call_end|>")["name"] == "_bad_json"
    assert parse_tool("plain answer") is None


def test_ui_contracts():
    app = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    css = (STATIC / "memufiles.css").read_text(encoding="utf-8")
    assert 'parts[0].path === "/") parts.shift()' in app               # no «⌂ › / › tmp» on Linux
    assert "this.crumbMenu(last, null, tail)" in app                   # arrow after the last folder
    assert "this._openWith.delete(ext)" in app                          # a failed list is not cached
    assert "opts.parentButton.focus()" in core                          # ArrowLeft returns to the parent item
    assert 'e.type !== "mouseenter"' in core                            # hover does not steal keyboard focus
    assert ".searchbox:not(:focus-within) .search-scope:not(.on)" in css
