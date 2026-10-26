"""MemuCode artifacts: versioned, session-scoped deliverables.

Artifacts keep generated HTML/React/SVG/markdown/code/data out of the chat
stream. They are stored under the user's MemuAI data directory with atomic
writes and can be published into the project through the normal write flow.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()

from .config import DATA_DIR

ARTIFACT_ROOT = Path(DATA_DIR) / "memuai" / "code_artifacts"
KINDS = {"html", "react", "svg", "markdown", "code", "data"}
MAX_CONTENT = 2_000_000
MAX_SESSIONS = 80
MAX_ARTIFACTS = 60

def _safe_session(session_id: str) -> str:
    return "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")[:64] or "default"

def _path(session_id: str) -> Path:
    return ARTIFACT_ROOT / f"{_safe_session(session_id)}.json"

def _empty(session_id: str) -> Dict[str, Any]:
    return {"session": _safe_session(session_id), "created": time.time(),
            "updated": time.time(), "artifacts": []}

def _load(session_id: str) -> Dict[str, Any]:
    path = _path(session_id)
    if not path.is_file():
        return _empty(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty(session_id)
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        return _empty(session_id)
    return data

def _save(session_id: str, data: Dict[str, Any]) -> None:
    path = _path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def _sanitize(kind: str, title: str, content: str) -> Dict[str, str]:
    kind = str(kind or "code").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ", ".join(sorted(KINDS)))
    title = re.sub(r"[\r\n\t]+", " ", str(title or "Untitled")).strip()[:160] or "Untitled"
    content = str(content or "")[:MAX_CONTENT]
    return {"kind": kind, "title": title, "content": content}

def create(session_id: str, *, kind: str, title: str, content: str) -> Dict[str, Any]:
    with _LOCK:
        return _create_locked(session_id, kind=kind, title=title, content=content)

def _create_locked(session_id: str, *, kind: str, title: str, content: str) -> Dict[str, Any]:
    data = _load(session_id)
    clean = _sanitize(kind, title, content)
    artifact = {
        "id": uuid.uuid4().hex[:12], "version": 1, **clean,
        "created": time.time(), "updated": time.time(),
    }
    data["artifacts"].append(artifact)
    data["artifacts"] = data["artifacts"][-MAX_ARTIFACTS:]
    data["updated"] = time.time()
    _save(session_id, data)
    _prune_sessions()
    return artifact

def update(session_id: str, artifact_id: str, *, kind: Optional[str] = None,
           title: Optional[str] = None, content: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        return _update_locked(session_id, artifact_id, kind=kind, title=title, content=content)

def _update_locked(session_id: str, artifact_id: str, *, kind: Optional[str],
                   title: Optional[str], content: Optional[str]) -> Dict[str, Any]:
    data = _load(session_id)
    artifact = next((row for row in data["artifacts"] if row.get("id") == str(artifact_id)), None)
    if artifact is None:
        raise KeyError("artifact not found")
    next_kind = kind or artifact.get("kind", "code")
    next_title = title if title is not None else artifact.get("title", "")
    next_content = content if content is not None else artifact.get("content", "")
    clean = _sanitize(next_kind, next_title, next_content)
    artifact.update(clean)
    artifact["version"] = int(artifact.get("version", 1)) + 1
    artifact["updated"] = time.time()
    data["updated"] = time.time()
    _save(session_id, data)
    return artifact

def get(session_id: str, artifact_id: str) -> Dict[str, Any]:
    artifact = next((row for row in _load(session_id)["artifacts"]
                     if row.get("id") == str(artifact_id)), None)
    if artifact is None:
        raise KeyError("artifact not found")
    return artifact

def list_artifacts(session_id: str) -> List[Dict[str, Any]]:
    rows = _load(session_id)["artifacts"]
    return [{key: value for key, value in row.items() if key != "content"} for row in rows]

def delete(session_id: str, artifact_id: str) -> None:
    with _LOCK:
        data = _load(session_id)
        data["artifacts"] = [row for row in data["artifacts"] if row.get("id") != str(artifact_id)]
        data["updated"] = time.time()
        _save(session_id, data)

def publish(session_id: str, artifact_id: str, root: Path, rel_path: str) -> Dict[str, Any]:
    """Copy an artifact into the project. The path must stay inside the root."""
    from . import code_checkpoints, work_tools

    root = Path(root).resolve()
    relative = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not relative or relative.startswith("../") or "/../" in f"/{relative}/":
        raise ValueError("path escapes the project")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the project") from exc
    artifact = get(session_id, artifact_id)
    if target.is_dir():
        raise ValueError("path is a directory")
    if target.is_file() and target.stat().st_size > code_checkpoints.MAX_FILE_BYTES:
        raise ValueError("existing file is too large to save an undo checkpoint")
    checkpoint = code_checkpoints.snapshot(session_id, root, relative, label="artifact_publish")
    if not checkpoint["stored"] and checkpoint.get("reason") != "already captured":
        raise ValueError("could not save an undo checkpoint")
    work_tools.tool_write(root, {"path": relative, "text": str(artifact.get("content") or ""),
                                 "allow_shrink": True})
    return {"id": artifact["id"], "path": target.relative_to(root).as_posix(),
            "version": artifact.get("version", 1), "size": target.stat().st_size}

def _prune_sessions() -> None:
    if not ARTIFACT_ROOT.is_dir():
        return
    files = [item for item in ARTIFACT_ROOT.glob("*.json") if item.is_file()]
    if len(files) <= MAX_SESSIONS:
        return
    files.sort(key=lambda item: item.stat().st_mtime)
    for stale in files[:len(files) - MAX_SESSIONS]:
        try:
            stale.unlink()
        except OSError:
            pass
