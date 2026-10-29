"""Regression checks for text edits, project context and malformed file metadata."""
from __future__ import annotations

import struct
import codecs
from pathlib import Path

import pytest

from app import work_context, work_tools
from app.memufiles import fsops


def test_insert_after_unterminated_last_line_keeps_a_line_separator(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("last", encoding="utf-8")
    work_tools.tool_insert(tmp_path, {"path": "a.txt", "text": "inserted"})
    assert target.read_bytes() == b"last\ninserted\n"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be", "cp1251"])
def test_large_append_keeps_separator_and_encoding(tmp_path, monkeypatch, encoding):
    target = tmp_path / "a.txt"
    target.write_bytes((codecs.BOM_UTF16_BE if encoding == "utf-16-be" else b"") + ("a" * 120 + "last").encode(encoding))
    read = work_tools.read_text_meta
    monkeypatch.setattr(work_tools, "read_text_meta", lambda path: read(path, limit=100))
    work_tools.tool_append(tmp_path, {"path": "a.txt", "text": "added"})
    assert target.read_text(encoding="utf-16" if encoding == "utf-16-be" else encoding) == "a" * 120 + "last\nadded\n"


def test_search_zero_context_returns_only_matching_line(tmp_path):
    (tmp_path / "a.txt").write_text("before\nneedle\nafter\n", encoding="utf-8")
    result = work_tools.tool_search(tmp_path, {"query": "needle", "context": 0})
    assert result["matches"][0]["text"] == "2: needle"


def test_project_context_reads_utf16_files(tmp_path):
    (tmp_path / "README.md").write_text("Project description", encoding="utf-16")
    result = work_context.build_work_context(tmp_path, "Read README.md")
    assert result["files"][0]["text"] == "Project description"


def test_project_context_ignores_a_file_that_resolves_in_a_loop(tmp_path, monkeypatch):
    broken = tmp_path / "loop.txt"
    broken.write_text("loop", encoding="utf-8")
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == broken:
            raise RuntimeError("Symlink loop")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert work_context.build_work_context(tmp_path, "read")["files"] == []


def test_shortcut_with_truncated_idlist_returns_no_target(tmp_path):
    shortcut = tmp_path / "broken.lnk"
    data = bytearray(0x4C)
    struct.pack_into("<I", data, 0, 0x4C)
    struct.pack_into("<I", data, 0x14, 0x1)
    shortcut.write_bytes(data)
    assert fsops.lnk_target(str(shortcut)) == ""
