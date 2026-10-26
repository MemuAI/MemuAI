"""Relevant-code selection for MemuAI Work.

The browser UI must not shovel an entire repository into every prompt.  This
module builds a cheap, deterministic project index and selects a small set of
text files whose paths and contents are most related to the current request.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .path_safety import is_link
from .work_tools import _decode

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".cache", ".next",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__",
    "node_modules", "vendor", "venv", ".venv", "dist", "build", "coverage",
}
TEXT_SUFFIXES = {
    "", ".c", ".cc", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".go", ".graphql", ".h", ".hpp", ".htm", ".html", ".ini", ".java",
    ".js", ".json", ".jsx", ".kt", ".less", ".md", ".mjs", ".php",
    ".properties", ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
}
IMPORTANT_NAMES = {
    "readme.md", "readme", "package.json", "pyproject.toml", "requirements.txt",
    "cargo.toml", "go.mod", "pom.xml", "dockerfile", "makefile", "index.html",
    "main.py", "app.py", "main.js", "app.js", "main.ts", "app.ts",
}

def _tokens(value: str) -> set[str]:
    return {part for part in re.findall(r"[\w.-]{2,}", value.lower(), re.UNICODE)
            if not part.isdigit()}

def _is_probably_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in IMPORTANT_NAMES

def _escapes(directory: Path, project: Path) -> bool:
    """A directory link (symlink/junction) or one resolving outside the root."""
    try:
        if is_link(directory):
            return True
        directory.resolve().relative_to(project)
    except (OSError, ValueError, RuntimeError):
        return True
    return False

def _safe_read(path: Path, query_tokens: set[str], limit: int) -> tuple[str, bool]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max(limit * 3, limit) + 1)
    except OSError:
        return "", False
    if b"\x00" in raw[:8192]:
        return "", False
    clipped = len(raw) > max(limit * 3, limit)
    truncated = clipped
    raw = raw[:max(limit * 3, limit)]

    text, _encoding = _decode(raw, clipped)
    text = text.replace("\x00", "")
    if len(text) <= limit:
        return text, truncated

    lines = text.splitlines()
    chosen = set(range(min(70, len(lines))))
    if query_tokens:
        for index, line in enumerate(lines):
            low = line.lower()
            if any(token in low for token in query_tokens):
                chosen.update(range(max(0, index - 8), min(len(lines), index + 9)))
                if len(chosen) > 450:
                    break
    rendered: list[str] = []
    previous = -2
    for index in sorted(chosen):
        if index != previous + 1:
            rendered.append("…")
        rendered.append(f"{index + 1}: {lines[index]}")
        previous = index
        if sum(map(len, rendered)) >= limit:
            break
    return "\n".join(rendered)[:limit], True

def build_work_context(root: str | Path, query: str, *, active_paths: list[str] | None = None,
                       max_files: int = 10, max_chars: int = 60_000,
                       max_entries: int = 4_000) -> dict[str, Any]:
    """Return a bounded project index and relevant file excerpts.

    Paths are always relative to ``root`` and no symlink target outside that
    root is read.  The function is intentionally dependency-free so it can run
    in the packaged desktop application.
    """
    project = Path(root).resolve()
    query = str(query or "")[:20_000]
    query_tokens = _tokens(query)
    active = {str(item).replace("\\", "/").lower() for item in (active_paths or [])[:30]}
    candidates: list[tuple[float, str, Path, int]] = []
    index: list[str] = []

    for current, dirs, files in os.walk(project, followlinks=False):
        base = Path(current)

        dirs[:] = [name for name in dirs
                   if name.lower() not in SKIP_DIRS and not _escapes(base / name, project)]
        for name in files:
            if len(index) >= max_entries:
                break
            path = base / name
            try:
                resolved = path.resolve()
                resolved.relative_to(project)
                stat = resolved.stat()
            except (OSError, ValueError):
                continue
            rel = resolved.relative_to(project).as_posix()
            index.append(rel)
            if stat.st_size > 2_000_000 or not _is_probably_text(resolved):
                continue
            low = rel.lower()
            path_tokens = _tokens(low)
            score = 0.0
            if low in active:
                score += 160
            if low in query.lower() or name.lower() in query.lower():
                score += 110
            score += len(query_tokens & path_tokens) * 20
            if name.lower() in IMPORTANT_NAMES:
                score += 18
            if re.search(r"(?:read|inspect|analy[sz]|fix|edit|project|прочит|изуч|анализ|исправ|измен|проект)", query, re.I):
                score += 4

            score -= rel.count("/") * 0.8
            score -= min(stat.st_size / 250_000, 5)
            candidates.append((score, rel, resolved, int(stat.st_size)))
        if len(index) >= max_entries:
            break

    candidates.sort(key=lambda item: (-item[0], item[1].lower()))
    selected = [item for item in candidates if item[0] > 0][:max_files]
    if not selected:
        selected = candidates[:min(4, max_files)]

    files_out: list[dict[str, Any]] = []
    remaining = max(2_000, int(max_chars))
    for score, rel, path, size in selected:
        if remaining < 500:
            break
        excerpt, truncated = _safe_read(path, query_tokens, min(24_000, remaining))
        if not excerpt.strip():
            continue
        files_out.append({
            "path": rel, "text": excerpt, "size": size,
            "score": round(score, 2), "truncated": truncated,
        })
        remaining -= len(excerpt)

    return {
        "index": index[:800],
        "index_truncated": len(index) > 800 or len(index) >= max_entries,
        "files": files_out,
        "chars": sum(len(item["text"]) for item in files_out),
    }
