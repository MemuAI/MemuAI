"""Server-side storage: files, projects, chats and the bridge queue.

Everything lives under one server data directory, in plain subfolders:

    <data>/files/      arbitrary files synced from the client
    <data>/projects/   project documents (one JSON file per project)
    <data>/chats/      chat documents (one JSON file per chat)
    <data>/bridge/     bridge requests from the server-side AI + results

Relative paths are validated and resolved against the owning root on every
access: a ``..`` or absolute path can never escape the server data dir.
Writes are atomic (temp file + ``os.replace`` in the same directory).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BRIDGE_STATUSES = ("pending", "done", "failed", "denied")


def safe_rel(rel: str, *, allow_subdirs: bool = True) -> str:
    """Validate a client-supplied relative path; raise ValueError if unsafe."""
    raw = str(rel or "")
    if not raw or "\x00" in raw:
        raise ValueError("empty path")
    if raw.startswith(("/", "\\")) or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("absolute path")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("unsafe path")
    if not allow_subdirs and len(parts) != 1:
        raise ValueError("subdirectories are not allowed here")
    return "/".join(parts)


def resolve_under(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` and prove it stays inside."""
    root_resolved = root.resolve()
    target = (root_resolved / safe_rel(rel)).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError("path escapes the data directory")
    return target


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ServerStore:
    """File / document storage rooted at a server data directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.files_root = self.root / "files"
        self.projects_root = self.root / "projects"
        self.chats_root = self.root / "chats"
        self.bridge_root = self.root / "bridge" / "requests"
        for folder in (self.files_root, self.projects_root, self.chats_root, self.bridge_root):
            folder.mkdir(parents=True, exist_ok=True)

    # ---- files ---------------------------------------------------------
    def put_file(self, rel: str, data: bytes) -> Dict[str, Any]:
        target = resolve_under(self.files_root, rel)
        _atomic_write(target, bytes(data))
        return self.file_meta(rel)

    def get_file(self, rel: str) -> Optional[bytes]:
        target = resolve_under(self.files_root, rel)
        try:
            return target.read_bytes()
        except OSError:
            return None

    def delete_file(self, rel: str) -> bool:
        target = resolve_under(self.files_root, rel)
        try:
            target.unlink()
            return True
        except OSError:
            return False

    def file_meta(self, rel: str) -> Dict[str, Any]:
        target = resolve_under(self.files_root, rel)
        try:
            stat = target.stat()
            data = target.read_bytes()
        except OSError:
            raise KeyError(rel)
        return {"rel": safe_rel(rel), "size": stat.st_size,
                "mtime": round(stat.st_mtime, 3), "sha256": _sha256(data)}

    def list_files(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for path in sorted(self.files_root.rglob("*")):
            if path.is_file() and ".tmp-" not in path.name:
                rel = path.relative_to(self.files_root).as_posix()
                out.append(self.file_meta(rel))
        return out

    # ---- documents: chats and projects ---------------------------------
    def _doc_root(self, kind: str) -> Path:
        if kind == "chats":
            return self.chats_root
        if kind == "projects":
            return self.projects_root
        raise ValueError("unknown document kind")

    def put_doc(self, kind: str, doc_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not _DOC_ID_RE.match(doc_id or ""):
            raise ValueError("invalid document id")
        if not isinstance(payload, dict):
            raise ValueError("document payload must be an object")
        root = self._doc_root(kind)
        body = dict(payload)
        body.setdefault("id", doc_id)
        body["updated_ts"] = time.time()
        _atomic_write(root / (doc_id + ".json"),
                      json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"))
        return {"id": doc_id, "updated_ts": body["updated_ts"],
                "title": body.get("title", "")}

    def get_doc(self, kind: str, doc_id: str) -> Optional[Dict[str, Any]]:
        if not _DOC_ID_RE.match(doc_id or ""):
            raise ValueError("invalid document id")
        path = self._doc_root(kind) / (doc_id + ".json")
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def list_docs(self, kind: str) -> List[Dict[str, Any]]:
        root = self._doc_root(kind)
        out = []
        for path in sorted(root.glob("*.json")):
            try:
                doc = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"id": doc.get("id", path.stem),
                        "updated_ts": doc.get("updated_ts", 0),
                        "title": doc.get("title", "")})
        return out

    def delete_doc(self, kind: str, doc_id: str) -> bool:
        if not _DOC_ID_RE.match(doc_id or ""):
            raise ValueError("invalid document id")
        try:
            (self._doc_root(kind) / (doc_id + ".json")).unlink()
            return True
        except OSError:
            return False

    # ---- manifest ------------------------------------------------------
    def manifest(self) -> Dict[str, Any]:
        return {
            "files": {row["rel"]: row for row in self.list_files()},
            "chats": {row["id"]: row for row in self.list_docs("chats")},
            "projects": {row["id"]: row for row in self.list_docs("projects")},
        }

    # ---- bridge queue --------------------------------------------------
    def enqueue_bridge_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        req_id = "br_" + secrets.token_hex(8)
        record = dict(request)
        record.update({"id": req_id, "status": "pending", "created_ts": time.time(),
                       "result": None})
        _atomic_write(self.bridge_root / (req_id + ".json"),
                      json.dumps(record, ensure_ascii=False).encode("utf-8"))
        return record

    def list_bridge_requests(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for path in sorted(self.bridge_root.glob("br_*.json")):
            try:
                record = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if status and record.get("status") != status:
                continue
            out.append(record)
        return out

    def get_bridge_request(self, req_id: str) -> Optional[Dict[str, Any]]:
        if not re.match(r"^br_[0-9a-f]+$", req_id or ""):
            return None
        path = self.bridge_root / (req_id + ".json")
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def set_bridge_result(self, req_id: str, status: str,
                          result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if status not in _BRIDGE_STATUSES:
            raise ValueError("invalid bridge status")
        record = self.get_bridge_request(req_id)
        if record is None:
            return None
        record["status"] = status
        record["result"] = result or {}
        record["finished_ts"] = time.time()
        _atomic_write(self.bridge_root / (req_id + ".json"),
                      json.dumps(record, ensure_ascii=False).encode("utf-8"))
        return record
