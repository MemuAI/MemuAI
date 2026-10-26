"""Чекпоинты MemuCode: любую правку агента можно отменить.

Агент, который пишет в файлы, обязан быть обратимым. Перед первым изменением
файла в сессии его содержимое сохраняется, и вся сессия откатывается одной
командой — даже если git в проекте нет или изменения не закоммичены.

Снимки лежат в `%APPDATA%/MemuAI/memuai/checkpoints/<session>/` рядом с
остальными данными приложения и не попадают в сам проект.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR
from .path_safety import is_link

CHECKPOINT_ROOT = Path(DATA_DIR) / "memuai" / "checkpoints"

MAX_SESSIONS = 200
MAX_FILE_BYTES = 8 * 1024 * 1024
INDEX_NAME = "index.json"

_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

def _lock_for(session_id: str) -> threading.Lock:
    """Сохранение в редакторе и запись агента идут в разных потоках: без
    блокировки одна из записей индекса терялась."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(session_id or "default"), threading.Lock())

def _key(root: Any, rel: str) -> tuple:
    return (os.path.normcase(str(root)), os.path.normcase(str(rel).replace("\\", "/")))

def _session_dir(session_id: str) -> Path:
    safe = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")[:64]
    return CHECKPOINT_ROOT / (safe or "default")

def _index_path(session_id: str) -> Path:
    return _session_dir(session_id) / INDEX_NAME

def _load_index(session_id: str) -> Dict[str, Any]:
    path = _index_path(session_id)
    if not path.is_file():
        return {"session": session_id, "created": time.time(), "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"session": session_id, "created": time.time(), "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"session": session_id, "created": time.time(), "entries": []}
    return data

def _save_index(session_id: str, index: Dict[str, Any]) -> None:
    folder = _session_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{INDEX_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, folder / INDEX_NAME)
    finally:
        temporary.unlink(missing_ok=True)

def _prune_sessions() -> None:
    """Старые сессии удаляются: снимки не должны расти бесконечно."""
    if not CHECKPOINT_ROOT.is_dir():
        return
    folders = [p for p in CHECKPOINT_ROOT.iterdir() if p.is_dir()]
    if len(folders) <= MAX_SESSIONS:
        return
    folders.sort(key=lambda p: p.stat().st_mtime)
    for stale in folders[:len(folders) - MAX_SESSIONS]:
        shutil.rmtree(stale, ignore_errors=True)

def _inside(root: Path, rel_path: str) -> Optional[Path]:
    """The file ``rel_path`` inside ``root``, or None when it escapes the project.

    A snapshot is taken before the write tool validates its path. Without this
    check a rejected ``../outside.txt`` was still recorded as "created by the
    session", and a later "undo all" deleted a file outside the project.
    """
    try:
        base = Path(os.path.abspath(root))
        target = Path(os.path.abspath(base / rel_path))
        target.relative_to(base)
        if target == base:
            return None

        for part in (target, *target.parents):
            if is_link(part):
                return None
        base = base.resolve()
        target = target.resolve()
        target.relative_to(base)
    except (OSError, ValueError):
        return None
    return target

def snapshot(session_id: str, root: Path, rel_path: str, *, label: str = "",
             turn: str = "", directory: bool = False) -> Dict[str, Any]:
    """Сохраняет состояние файла ДО правки. Повторный вызов ничего не делает.

    Хранится только первая версия: откат должен возвращать к тому, что было до
    вмешательства агента, а не к предыдущему его шагу.
    """
    with _lock_for(session_id):
        return _snapshot_locked(session_id, root, rel_path, label=label, turn=turn, directory=directory)

def _snapshot_locked(session_id: str, root: Path, rel_path: str, *, label: str = "",
                     turn: str = "", directory: bool = False) -> Dict[str, Any]:
    index = _load_index(session_id)
    base_path = Path(root).resolve()
    base = str(base_path)
    source = _inside(root, str(rel_path).replace("\\", "/"))
    if source is None:
        return {"path": str(rel_path), "stored": False, "reason": "outside the project"}

    rel_path = source.relative_to(base_path).as_posix()

    turn = str(turn or "")[:80]
    known = {_key(entry.get("root") or index.get("root") or base, entry["path"]) + (str(entry.get("turn") or ""),)
             for entry in index["entries"]}
    if _key(base, rel_path) + (turn,) in known:
        return {"path": rel_path, "stored": False, "reason": "already captured"}
    if source.is_dir() and not directory:
        return {"path": rel_path, "stored": False, "reason": "path is a directory"}
    if directory and source.exists() and not source.is_dir():
        return {"path": rel_path, "stored": False, "reason": "expected a directory"}
    folder = _session_dir(session_id) / "files"
    folder.mkdir(parents=True, exist_ok=True)
    entry: Dict[str, Any] = {
        "path": rel_path, "at": time.time(), "label": str(label)[:120],
        "existed": source.is_dir() if directory else source.is_file(), "turn": turn,

        "root": str(Path(root).resolve()),
    }
    if directory:
        entry["directory"] = True
    elif source.is_file():
        try:
            size = source.stat().st_size
        except OSError as exc:
            return {"path": rel_path, "stored": False, "reason": str(exc)[:200]}
        if size > MAX_FILE_BYTES:
            return {"path": rel_path, "stored": False, "reason": "file is too large for an undo checkpoint"}
        blob = folder / (uuid.uuid4().hex + ".bin")
        try:
            shutil.copy2(source, blob)
            entry["blob"] = blob.name
            entry["size"] = size
        except OSError as exc:
            blob.unlink(missing_ok=True)
            return {"path": rel_path, "stored": False, "reason": str(exc)[:200]}
    index["entries"].append(entry)
    index["root"] = str(Path(root).resolve())
    index["updated"] = time.time()
    _save_index(session_id, index)
    _prune_sessions()
    return {"path": rel_path, "stored": True, "existed": entry["existed"]}

def _tree_entries(root: Path, target: Path) -> List[tuple[Path, bool]]:
    """Collect one bounded tree without traversing links or junctions."""
    base = Path(root).resolve()
    pending = [target]
    entries = []
    files = 0
    while pending:
        path = pending.pop()
        if _inside(root, path.relative_to(base).as_posix()) is None:
            raise ValueError("Нельзя сохранить каталог со ссылками для безопасного отката")
        directory = path.is_dir()
        entries.append((path, directory))
        files += int(not directory)
        if len(entries) > 4000 or files > 2000:
            raise ValueError("В папке слишком много файлов для отката (>2000 файлов или >4000 путей)")
        if directory:
            for child in path.iterdir():
                pending.append(child)
                if len(pending) + len(entries) > 4000:
                    raise ValueError("В папке слишком много путей для отката (>4000)")
    return entries

def _capture_entries(session_id: str, root: Path, entries: List[tuple[Path, bool]], *,
                     label: str, turn: str) -> None:
    base = Path(root).resolve()
    for path, directory in entries:
        result = snapshot(session_id, root, path.relative_to(base).as_posix(),
                          label=label, turn=turn, directory=directory)
        if not result.get("stored") and result.get("reason") != "already captured":
            raise ValueError(f"Не удалось сохранить путь для отката: {path} ({result.get('reason')})")

def require_snapshot(session_id: str, root: Path, rel_path: str, *, label: str = "",
                     turn: str = "") -> None:
    """Refuse a session write when its previous contents cannot be restored."""
    target = _inside(root, str(rel_path).replace("\\", "/"))
    if target is None:
        raise ValueError("Путь находится вне проекта или содержит ссылку")
    entries = _tree_entries(root, target) if target.is_dir() else [(target, False)]
    _capture_entries(session_id, root, entries, label=label, turn=turn)

def require_move_snapshots(session_id: str, root: Path, source_rel: str, destination_rel: str,
                           *, label: str = "work_move", turn: str = "") -> None:
    """Capture both trees, including new empty directories, before a move."""
    source = _inside(root, source_rel)
    destination = _inside(root, destination_rel)
    if source is None or destination is None:
        raise ValueError("Путь перемещения находится вне проекта")
    directory = source.is_dir()
    entries = _tree_entries(root, source) if directory else [(source, False)]
    _capture_entries(session_id, root, entries, label=label, turn=turn)
    if not directory:
        require_snapshot(session_id, root, destination_rel, label=label, turn=turn)
        return
    base = Path(root).resolve()

    missing = []
    parent = destination.parent
    while parent != base and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    pairs = [(path, True) for path in reversed(missing)]

    pairs += [(destination / path.relative_to(source), is_directory)
              for path, is_directory in entries]
    _capture_entries(session_id, root, pairs, label=label, turn=turn)

def list_entries(session_id: str) -> List[Dict[str, Any]]:
    index = _load_index(session_id)
    rows, seen = [], set()
    for entry in index["entries"]:

        key = _key(entry.get("root") or index.get("root") or "", entry["path"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({"path": entry["path"], "existed": entry.get("existed", False),
                     "at": entry.get("at", 0), "label": entry.get("label", ""),
                     "restorable": bool(entry.get("blob")) or bool(entry.get("directory")) or not entry.get("existed", False)})
    return rows

def restore(session_id: str, root: Path, *, paths: Optional[List[str]] = None,
            turn: str = "") -> Dict[str, Any]:
    """Возвращает файлы к состоянию до правок сессии.

    Файл, которого раньше не было, удаляется; изменённый — перезаписывается
    сохранённой копией. Отдельные пути можно откатить выборочно.
    """
    with _lock_for(session_id):
        return _restore_locked(session_id, root, paths=paths, turn=turn)

def _restore_locked(session_id: str, root: Path, *, paths: Optional[List[str]] = None,
                    turn: str = "") -> Dict[str, Any]:
    index = _load_index(session_id)
    wanted = {os.path.normcase(Path(str(p).replace("\\", "/")).as_posix()) for p in (paths or [])}
    restored: List[str] = []
    removed: List[str] = []
    failed: List[Dict[str, str]] = []
    kept: List[Dict[str, Any]] = []
    folder = _session_dir(session_id) / "files"

    for entry in reversed(index["entries"]):
        rel = entry["path"]
        if (wanted and os.path.normcase(rel) not in wanted) or (turn and str(entry.get("turn") or "") != turn):
            kept.append(entry)
            continue

        recorded = str(entry.get("root") or index.get("root") or "")
        if recorded and not Path(recorded).is_dir():
            failed.append({"path": rel, "error": "исходный проект недоступен"})
            kept.append(entry)
            continue
        base = Path(recorded) if recorded else root
        target = _inside(base, rel)
        if target is None:
            failed.append({"path": rel, "error": "путь вне проекта или содержит ссылку"})
            kept.append(entry)
            continue
        if entry.get("directory"):
            try:
                if entry.get("existed"):
                    target.mkdir(parents=True, exist_ok=True)
                    restored.append(rel)
                else:
                    if target.exists():
                        target.rmdir()
                    removed.append(rel)
            except OSError as exc:
                failed.append({"path": rel, "error": str(exc)[:200]})
                kept.append(entry)
            continue
        if target.is_dir():
            failed.append({"path": rel, "error": "путь стал каталогом; каталог не удалён"})
            kept.append(entry)
            continue
        try:
            if not entry.get("existed"):
                if target.exists():
                    target.unlink()
                removed.append(rel)
            elif entry.get("blob"):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / entry["blob"], target)
                restored.append(rel)
            else:
                failed.append({"path": rel, "error": entry.get("error") or
                               "файл был слишком большим для снимка"})
                kept.append(entry)
        except OSError as exc:
            failed.append({"path": rel, "error": str(exc)[:200]})
            kept.append(entry)
    index["entries"] = list(reversed(kept))
    index["updated"] = time.time()
    _save_index(session_id, index)
    return {"restored": restored, "removed": removed, "failed": failed,
            "remaining": len(kept)}

def clear(session_id: str) -> None:
    shutil.rmtree(_session_dir(session_id), ignore_errors=True)
