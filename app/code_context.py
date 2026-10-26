"""MemuCode context engine.

A bounded, incremental cache between the coding agent and the repository.
The agent receives the file tree, then requests exact line ranges; repeated
reads of unchanged ranges collapse to short references, so long sessions do
not resend the same code forever.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .path_safety import is_link

MAX_FILE_BYTES = 2_000_000
MAX_INDEX_ENTRIES = 6_000
MAX_READ_CHARS = 120_000
MAX_RANGES_PER_FILE = 24
MAX_CACHED_TEXTS = 48

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".cache", ".next",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__",
    "node_modules", "vendor", "venv", ".venv", "dist", "build", "coverage",
    ".memuai", ".todo", ".code_search_cache",
}
TEXT_SUFFIXES = {
    "", ".c", ".cc", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".go", ".graphql", ".h", ".hpp", ".htm", ".html", ".ini", ".java",
    ".js", ".json", ".jsx", ".kt", ".less", ".md", ".mjs", ".php",
    ".properties", ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
}

class CodeContextEngine:
    """Per-session repository cache. In-memory only; a session is a chat id."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.indexed_at = 0.0
        self.tree: List[str] = []
        self.tree_truncated = False
        self.files: Dict[str, Dict[str, Any]] = {}
        self.read_ranges: Dict[str, List[Tuple[int, int]]] = {}
        self.saved_chars = 0
        self.requested_chars = 0
        self._text_order: List[str] = []
        self._lock = threading.Lock()

    def refresh(self) -> Dict[str, Any]:
        """Walk the project and update only entries whose mtime/size changed."""
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> Dict[str, Any]:
        self.indexed_at = time.time()
        entries: List[str] = []
        seen: set[str] = set()
        stop = False
        for current, dirs, files in os.walk(self.root, followlinks=False):
            if stop:
                dirs[:] = []
                continue
            base = Path(current)

            dirs[:] = sorted(d for d in dirs
                             if d.lower() not in SKIP_DIRS and d not in SKIP_DIRS and not is_link(base / d))
            for name in sorted(files):
                if len(entries) >= MAX_INDEX_ENTRIES:
                    self.tree_truncated = True
                    stop = True
                    break
                path = base / name
                try:
                    resolved = path.resolve()
                    resolved.relative_to(self.root)
                    stat = resolved.stat()
                except (OSError, ValueError):
                    continue
                rel = resolved.relative_to(self.root).as_posix()
                entries.append(rel)
                seen.add(rel)
                previous = self.files.get(rel)
                if previous and previous.get("size") == stat.st_size and previous.get("mtime_ns") == stat.st_mtime_ns:
                    continue
                self.read_ranges.pop(rel, None)
                self.files[rel] = {
                    "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                    "text": "", "hash": "", "readable": self._is_text(resolved, stat.st_size),
                }
        self.tree = entries
        if len(entries) < MAX_INDEX_ENTRIES:
            self.tree_truncated = False
        for stale in set(self.files) - seen:
            self.files.pop(stale, None)
            self.read_ranges.pop(stale, None)
            if stale in self._text_order:
                self._text_order.remove(stale)
        return self.stats()

    def _is_text(self, path: Path, size: int) -> bool:
        if size > MAX_FILE_BYTES or path.suffix.lower() not in TEXT_SUFFIXES:
            return False
        try:
            return b"\x00" not in path.read_bytes()[:8192]
        except OSError:
            return False

    def _canon(self, rel: str) -> str:
        """Ключ файла, как его хранит индекс: ./a.py, src//a.py и a.py — один файл."""
        raw = str(rel or "").replace("\\", "/").strip().lstrip("/")
        try:
            resolved = (self.root / raw).resolve()
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return "../" + raw
        except OSError:
            return raw

    def _sync_row(self, rel: str) -> None:
        """Сверяет запись индекса с диском при каждом чтении.

        Файл мог поменять кто угодно: редактор, форматтер из code_run, откат,
        git checkout, work_write. Раньше агент получал «уже прочитано» или
        «файла нет» до следующего обновления дерева.
        """
        path = self.root / rel
        try:
            stat = path.stat()
        except OSError:
            if rel in self.files:
                self.files.pop(rel, None)
                self.read_ranges.pop(rel, None)
                if rel in self._text_order:
                    self._text_order.remove(rel)
            return
        if not path.is_file():
            return
        row = self.files.get(rel)
        if row and row.get("size") == stat.st_size and row.get("mtime_ns") == stat.st_mtime_ns:
            return
        self.read_ranges.pop(rel, None)
        self.files[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                           "text": "", "hash": "", "readable": self._is_text(path, stat.st_size)}
        if rel not in self.tree:
            self.tree.append(rel)

    def _load_text(self, rel: str) -> str:
        rel = self._canon(rel)
        if rel.startswith("../") or rel == "..":
            return ""
        self._sync_row(rel)
        row = self.files.get(rel)
        if row is None:
            return ""
        if row.get("text"):
            return str(row["text"])
        if not row.get("readable"):
            return ""
        try:
            raw = (self.root / rel).read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            row["readable"] = False
            return ""
        clipped = text[:MAX_READ_CHARS]
        digest = hashlib.sha256(clipped.encode("utf-8")).hexdigest()[:16]
        if row.get("hash") and row.get("hash") != digest:
            self.read_ranges.pop(rel, None)
        row["text"] = clipped
        row["hash"] = digest
        row["lines"] = clipped.count("\n") + (0 if clipped.endswith("\n") or not clipped else 1)
        self._remember_text(rel)
        return clipped

    def _remember_text(self, rel: str) -> None:
        if rel in self._text_order:
            self._text_order.remove(rel)
        self._text_order.append(rel)
        while len(self._text_order) > MAX_CACHED_TEXTS:
            old = self._text_order.pop(0)
            row = self.files.get(old)
            if row is not None:
                row["text"] = ""

    def invalidate_file(self, rel: str) -> None:
        rel = self._canon(rel)
        with self._lock:
            self.read_ranges.pop(rel, None)
            self.files.pop(rel, None)
            if rel in self._text_order:
                self._text_order.remove(rel)

    def stats(self) -> Dict[str, Any]:
        return {
            "root": str(self.root), "entries": len(self.tree),
            "truncated": self.tree_truncated, "indexed_at": self.indexed_at,
            "saved_chars": self.saved_chars, "requested_chars": self.requested_chars,
        }

    def read_cached(self, rel: str, start_line: int = 1, end_line: int = 0,
                    max_chars: int = 16_000) -> Dict[str, Any]:
        with self._lock:
            return self._read_cached_locked(rel, start_line, end_line, max_chars)

    def _read_cached_locked(self, rel: str, start_line: int, end_line: int,
                            max_chars: int) -> Dict[str, Any]:
        rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
        if rel.startswith("../") or "/../" in f"/{rel}/" or rel == "..":
            return {"path": rel, "error": "path escapes the project"}
        rel = self._canon(rel)
        if rel.startswith("../") or rel == "..":
            return {"path": rel, "error": "path escapes the project"}
        start = max(1, int(start_line or 1))
        end = int(end_line or 0)
        text = self._load_text(rel)
        row = self.files.get(rel)
        if row is None or not text:
            return {"path": rel, "error": "file is missing, binary, or too large"}
        lines = text.splitlines()
        if not lines or start > len(lines):
            return {"path": rel, "error": "line range is outside the file",
                    "total_lines": len(lines)}
        end = min(end if end > 0 else len(lines), len(lines))
        if end < start:
            return {"path": rel, "error": "line range is empty", "total_lines": len(lines)}
        full = "\n".join(lines[start - 1:end])
        limit = max(200, int(max_chars or 16_000))
        requested = full[:limit]
        truncated = len(full) > len(requested)
        self.requested_chars += len(requested)
        ranges = self.read_ranges.setdefault(rel, [])
        if self._fully_covered(start, end, ranges) and not truncated:
            self.saved_chars += len(requested)
            return {"path": rel, "already_read": True, "range": [start, end],
                    "reference": f"already read {rel}:{start}-{end}",
                    "covered_by": [[a, b] for a, b in ranges],
                    "saved_chars": len(requested), "total_lines": len(lines)}
        if not truncated:
            self._remember(ranges, start, end)
        return {"path": rel, "already_read": False, "range": [start, end],
                "text": requested, "total_lines": len(lines),
                "truncated": truncated, "hash": row.get("hash", "")}

    def _fully_covered(self, start: int, end: int, ranges: List[Tuple[int, int]]) -> bool:
        cursor = start
        for left, right in sorted(ranges):
            if right < cursor:
                continue
            if left > cursor:
                return False
            cursor = max(cursor, right + 1)
            if cursor > end:
                return True
        return cursor > end

    def _remember(self, ranges: List[Tuple[int, int]], start: int, end: int) -> None:
        merged: List[List[int]] = []
        for left, right in sorted([*ranges, (start, end)]):
            if right < left:
                continue
            if not merged or left > merged[-1][1] + 1:
                merged.append([left, right])
            else:
                merged[-1][1] = max(merged[-1][1], right)
        ranges[:] = [(left, right) for left, right in merged[:MAX_RANGES_PER_FILE]]

    def outline_deep(self, rel: str) -> Dict[str, Any]:
        with self._lock:
            return self._outline_locked(rel)

    def _outline_locked(self, rel: str) -> Dict[str, Any]:
        rel = self._canon(rel)
        text = self._load_text(rel)
        if not text:
            return {"path": rel, "error": "file is not readable"}
        symbols: List[Dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            kind = None
            if stripped.startswith(("def ", "async def ", "class ")):
                kind = "def" if "def " in stripped[:10] else "class"
            elif line.startswith("function ") or line.startswith("export function "):
                kind = "function"
            elif line.startswith(("export ", "const ", "let ")) and " => " in line and "(" in line:
                kind = "function"
            elif stripped.startswith(("pub fn ", "fn ", "impl ", "struct ", "trait ")):
                kind = "rust"
            if kind:
                name = stripped.split("(", 1)[0].split(" ", 1)[-1].split(":", 1)[0].strip()
                symbols.append({"line": number, "kind": kind, "name": name[:160]})
        return {"path": rel, "symbols": symbols[:300], "total_symbols": len(symbols)}

    def search_symbols(self, query: str, limit: int = 40) -> Dict[str, Any]:
        """Filename and declaration search. Does not mark ranges as read."""
        needle = str(query or "").strip().lower()
        if len(needle) < 2:
            return {"query": needle, "symbols": [], "error": "query must be at least 2 characters"}
        with self._lock:
            if not self.tree:
                self._refresh_locked()
            found: List[Dict[str, Any]] = []
            scanned = 0
            for rel in self.tree:
                if len(found) >= limit:
                    break
                row = self.files.get(rel) or {}
                if needle in Path(rel).name.lower():
                    found.append({"path": rel, "line": 1, "kind": "file", "name": Path(rel).name})
                if not row.get("readable") or scanned >= 250:
                    continue
                scanned += 1
                outline = self._outline_locked(rel)
                for item in outline.get("symbols") or []:
                    if needle in str(item.get("name") or "").lower():
                        found.append({"path": rel, **item})
                        if len(found) >= limit:
                            break
            return {"query": needle, "symbols": found[:limit], "scanned": scanned}

    def selective_context(self, query: str, max_files: int = 8, max_chars: int = 48_000) -> Dict[str, Any]:
        with self._lock:
            return self._selective_locked(query, max_files, max_chars)

    def _selective_locked(self, query: str, max_files: int, max_chars: int) -> Dict[str, Any]:
        """Bounded prompt context: tree, summaries, and only fresh excerpts."""
        if not self.tree:
            self._refresh_locked()
        query_tokens = {token.lower() for token in __import__("re").findall(r"[\w.-]{2,}", str(query or ""))}
        scored: List[Tuple[float, str]] = []
        for rel, row in self.files.items():
            score = 0.0
            low = rel.lower()
            for token in query_tokens:
                if token in low:
                    score += 20
            if Path(rel).name.lower() in {"readme.md", "package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod", "main.py", "app.js", "index.html"}:
                score += 15
            score -= rel.count("/") * 0.5
            if row.get("size", 0) > MAX_FILE_BYTES:
                continue
            if score > 0:
                scored.append((score, rel))
        scored.sort(key=lambda item: (-item[0], item[1].lower()))
        selected = [rel for _, rel in scored[:max_files]]
        if not selected:
            selected = [rel for rel, row in self.files.items() if row.get("readable")][:4]
        files: List[Dict[str, Any]] = []
        remaining = max_chars
        for rel in selected:
            if remaining < 500:
                break
            text = self._load_text(rel)
            if not text:
                continue
            ranges = self.read_ranges.get(rel) or []
            summary = {"path": rel, "size": self.files[rel].get("size", 0),
                       "read_ranges": [[a, b] for a, b in ranges]}
            excerpt = self._read_cached_locked(
                rel, 1, min(180, text.count("\n") + 1), min(12_000, remaining))
            files.append({**summary, "excerpt": "" if excerpt.get("already_read") else excerpt.get("text", "")})
            remaining -= len(excerpt.get("text") or "")
        return {"root": str(self.root), "index": self.tree[:800],
                "index_truncated": self.tree_truncated, "files": files,
                **self.stats()}

_SESSIONS: Dict[str, CodeContextEngine] = {}

def engine_for(session_id: str, root: str | Path) -> CodeContextEngine:
    session_id = str(session_id or "default")
    engine = _SESSIONS.get(session_id)
    if engine is None or str(engine.root) != str(Path(root).resolve()):
        engine = CodeContextEngine(root)
        _SESSIONS[session_id] = engine
    return engine

def drop_session(session_id: str) -> None:
    _SESSIONS.pop(str(session_id or ""), None)
