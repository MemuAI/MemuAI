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

# H8: files that must NEVER be sent to a model as excerpts. A Work project
# routinely contains live secrets (.env with API keys, private keys, cloud
# credentials); the excerpt picker scored them like any other text file, so
# asking "how is the env configured" could paste the key into the prompt.
# Names are matched case-insensitively; hidden files are excluded unless
# they are on the small allow-list of known-harmless dotfiles.
SECRET_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development", ".env.test",
    ".env.example", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".npmrc",
    ".pypirc", "credentials", ".netrc", ".dockercfg", ".dockerconfigjson",
    "service-account.json", "google-services.json",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".jks"}
SECRET_DIRS = {".aws", ".ssh", ".gnupg", ".kube", ".docker"}
HARMLESS_DOTFILES = {".gitignore", ".gitattributes", ".editorconfig", ".gitmodules"}


def _is_secret_file(rel: str, name: str) -> bool:
    low = name.lower()
    if low in SECRET_NAMES:
        return True
    if Path(name).suffix.lower() in SECRET_SUFFIXES:
        return True
    parts = {part.lower() for part in rel.split("/")[:-1]}
    if parts & SECRET_DIRS:
        return True
    # Hidden files (other than the harmless allow-list) are never excerpts:
    # .env*, .secrets, .passwd, etc. live here.
    if low.startswith(".") and low not in HARMLESS_DOTFILES:
        return True
    return False


RULE_FILES = ("AGENTS.md", "CLAUDE.md")
RULES_LIMIT = 12_000

def project_rules(root: str | Path, *, limit: int = RULES_LIMIT) -> dict[str, Any]:
    """The repository's own instructions for coding agents, if it has any.

    AGENTS.md is the common convention; CLAUDE.md is read when it is the only
    one. Only the project root is checked, links are ignored and the text is
    clipped, so a huge file cannot flood the prompt.
    """
    project = Path(root).resolve()
    try:
        names = {entry.name.lower(): entry for entry in project.iterdir()
                 if entry.is_file() and not is_link(entry)}
    except OSError:
        return {}
    from .work_tools import WorkError, read_text
    for name in RULE_FILES:
        path = names.get(name.lower())
        if path is None:
            continue
        try:
            text, clipped = read_text(path, limit)
        except WorkError:
            continue
        text = text.strip()
        if text:
            return {"file": path.name, "text": text, "truncated": bool(clipped)}
    return {}

CODE_RULE_FILES = ("MEMUCODE.md", "AGENTS.md", "CLAUDE.md")

def code_rules(root: str | Path, *, limit: int = RULES_LIMIT) -> dict[str, Any]:
    """MemuCode: the same instruction files as MemuCode CLI — the personal MEMUCODE.md, the folders above the
    project, MEMUCODE.md / AGENTS.md / CLAUDE.md of the project (also in .memucode/ and .claude/), the
    *.local.md files, with their @imports. Falls back to :func:`project_rules`."""
    try:
        from .cli.project import memory_files
        rows = memory_files(Path(root), list(CODE_RULE_FILES))
    except Exception:  # noqa: BLE001
        return project_rules(root, limit=limit)
    if not rows:
        return {}
    project = Path(root).resolve()
    parts: list[str] = []
    names: list[str] = []
    for row in rows:
        try:
            shown = row.path.resolve().relative_to(project).as_posix()
        except ValueError:
            shown = row.path.name if row.scope != "user" else "~/" + row.path.name
        names.append(shown)
        parts.append(f"<memory path=\"{shown}\" scope=\"{row.scope}\">\n{row.text.strip()}\n</memory>")
    text = "\n".join(parts)
    clipped = len(text) > limit
    return {"file": ", ".join(names[:4]) + (f" +{len(names) - 4}" if len(names) > 4 else ""),
            "text": text[:limit], "truncated": clipped, "files": names}

def project_index(root: str | Path, *, max_files: int = 800, max_entries: int = 4_000) -> dict[str, Any]:
    """Only the file list and a short overview — no file is opened.

    Work and MemuCode read files with tools, so the prompt needs the structure,
    not excerpts; this keeps every turn from re-reading the repository. The walk
    is the same as in `build_work_context`, so the list matches what it returned.
    """
    project = Path(root).resolve()
    index: list[str] = []
    languages: dict[str, int] = {}
    entry_points: list[str] = []
    from .work_tools import ENTRY_NAMES
    for current, dirs, files in os.walk(project, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(name for name in dirs
                         if name.lower() not in SKIP_DIRS and not _escapes(base / name, project))
        for name in sorted(files):
            if len(index) >= max_entries:
                break
            path = base / name
            try:
                resolved = path.resolve()
                rel = resolved.relative_to(project).as_posix()
            except (OSError, ValueError, RuntimeError):
                continue
            index.append(rel)
            suffix = Path(name).suffix.lower()
            if suffix:
                languages[suffix] = languages.get(suffix, 0) + 1
            if name.lower() in ENTRY_NAMES and len(entry_points) < 12:
                entry_points.append(rel)
        if len(index) >= max_entries:
            break
    top = sorted(languages.items(), key=lambda item: -item[1])[:8]
    return {"index": index[:max_files],
            "index_truncated": len(index) > max_files or len(index) >= max_entries,
            "overview": {"languages": [{"suffix": suffix, "files": count} for suffix, count in top],
                         "entry_points": entry_points, "count": len(index)}}

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
    if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
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
            except (OSError, ValueError, RuntimeError):
                continue
            rel = resolved.relative_to(project).as_posix()
            index.append(rel)
            if stat.st_size > 2_000_000 or not _is_probably_text(resolved):
                continue
            # H8: secret files are listed in the index (structure) but their
            # contents are never excerpted into a model prompt.
            if _is_secret_file(rel, name):
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
    remaining = max(200, int(max_chars))
    for score, rel, path, size in selected:
        if remaining < 200:
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
        # What the budget did, in the open: how many relevant candidates the
        # cap left out, and how much of the char budget was used. Callers
        # (and tests) can tell «nothing was cut» from «the project was cut».
        "budget": {"max_chars": int(max_chars), "used_chars": sum(len(item["text"]) for item in files_out),
                   "candidates": len(candidates), "selected": len(files_out),
                   "dropped": max(0, len(candidates) - len(files_out)),
                   "scanned": len(index)},
    }
