"""Reviewable text changes with encoding preservation and stale-file detection."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict

from .fsops import FsError

MAX_BYTES = 2 * 1024 * 1024

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def snapshot(path: str) -> tuple:
    from ..memunote.formats import textio

    target = Path(path)
    if not target.exists():
        return b"", "", None
    if not target.is_file() or target.stat().st_size > MAX_BYTES:
        raise FsError("not_text", path)
    with target.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise FsError("too_large", path)
    info = textio.detect_bytes(raw, True)
    if info.binary or info.damaged:
        raise FsError("not_text", path)
    try:
        raw.decode(info.encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise FsError("not_text", path) from exc
    return raw, textio.decode(raw, info.encoding), info

def prepare(path: str, action: Dict[str, Any]) -> Dict[str, Any]:
    from ..memunote.formats import textio

    raw, before, info = snapshot(path)
    exists = Path(path).exists()
    if action.get("op") in ("edit", "replace"):
        if not exists:
            raise FsError("not_found", path)
        find = action.get("old_text", action.get("find"))
        replacement = action.get("new_text", action.get("replace"))
        if not isinstance(find, str) or not find or not isinstance(replacement, str):
            raise FsError("bad_edit", path)

        find = find.replace("\r\n", "\n").replace("\r", "\n")
        replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
        pattern = r"(?:\r\n|\r|\n)".join(re.escape(line) for line in find.split("\n"))
        matches = list(re.finditer(pattern, before))
        count = len(matches)
        if not count:
            raise FsError("text_not_found", path)
        if count > 1 and action.get("all") is not True:
            raise FsError("ambiguous_edit", path)
        replacement = textio.convert_eol(replacement, info.eol if info.eol != "mixed" else
                                         textio.majority_eol(before) or "lf")
        after = before
        for match in reversed(matches if action.get("all") is True else matches[:1]):
            after = after[:match.start()] + replacement + after[match.end():]
    else:
        after = action.get("content", action.get("text"))
        if not isinstance(after, str):
            raise FsError("bad_edit", path)
    encoding, eol, bom = (info.encoding, info.eol, info.bom) if info else ("utf-8", "lf", False)
    if len(after.encode("utf-8")) > MAX_BYTES:
        raise FsError("too_large", path)
    data, lost = textio.encode(after, encoding, eol, bom)
    if lost:
        raise FsError("encoding_loss", path)
    if exists and data == raw:
        raise FsError("no_changes", path)
    import difflib
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       fromfile=Path(path).name, tofile=Path(path).name, n=3))
    return {"op": "write", "path": path, "content": after, "expected": digest(raw) if exists else None,
            "encoding": encoding, "eol": eol, "bom": bom, "diff": diff[:24000], "diff_truncated": len(diff) > 24000,
            "size": len(data), "is_dir": False, "creating": not exists}

def encoded(item: Dict[str, Any]) -> bytes:
    from ..memunote.formats import textio

    data, lost = textio.encode(item["content"], item["encoding"], item["eol"], item["bom"])
    if lost:
        raise FsError("encoding_loss", item["path"])
    return data
