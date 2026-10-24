"""Instant search for MemuFiles.

1. ``app.fsindex`` (the PC-wide index of Round 35: Everything / NTFS MFT / its
   own walker index) when it exists and is ready — imported lazily, any failure
   falls back silently.
2. Otherwise (and always for "this folder" searches or content search) a fast
   recursive ``os.scandir`` walk that STREAMS matches, so the first results show
   in milliseconds even in a huge tree.

Query syntax (Explorer-like, all parts AND-ed):
``word`` substring of the name (case-insensitive), ``*.txt`` / ``a?c`` glob,
``ext:pdf``, ``kind:image|video|audio|doc|archive|code|folder``,
``size:>5mb`` / ``size:<10k``, ``modified:today|week|month|year`` or
``modified:>2026-01-01``, ``content:"text"`` (read text files ≤ 8 MB).
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

log = logging.getLogger("memufiles.search")

KIND_EXT = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff", ".heic", ".avif", ".psd", ".raw"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".3gp"},
    "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".mid", ".midi"},
    "doc": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx", ".odp", ".epub", ".fb2"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".iso"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
             ".html", ".css", ".scss", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".sh", ".ps1", ".bat", ".sql", ".kt", ".swift", ".lua"},
    "exe": {".exe", ".msi", ".bat", ".cmd", ".com", ".appimage", ".deb", ".rpm"},
}
TEXT_EXT = KIND_EXT["code"] | {".txt", ".md", ".csv", ".log", ".cfg", ".conf", ".env", ".tsv", ".rst", ".tex", ".srt", ".vtt", ".markdown"}
SKIP_DIRS = {"$Recycle.Bin", "System Volume Information", "node_modules", ".git", "__pycache__", ".venv", "venv",
             "Windows", "WinSxS", "proc", "sys", "dev"}
UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2, "g": 1024 ** 3, "gb": 1024 ** 3,
         "к": 1024, "кб": 1024, "м": 1024 ** 2, "мб": 1024 ** 2, "г": 1024 ** 3, "гб": 1024 ** 3}


def parse_size(text: str) -> Optional[int]:
    match = re.fullmatch(r"\s*([\d.,]+)\s*([a-zA-Zа-яА-Я]*)\s*", text or "")
    if not match:
        return None
    unit = match.group(2).lower()
    if unit not in UNITS:
        return None
    try:
        return int(float(match.group(1).replace(",", ".")) * UNITS[unit])
    except ValueError:
        return None


class Query:
    def __init__(self, text: str) -> None:
        self.raw = text or ""
        self.words: List[str] = []
        self.globs: List[str] = []
        self.exts: set = set()
        self.kinds: set = set()
        self.min_size: Optional[int] = None
        self.max_size: Optional[int] = None
        self.after: Optional[float] = None
        self.before: Optional[float] = None
        self.content: str = ""
        self.folders_only = False
        self.files_only = False
        for token in re.findall(r'(\w+:"[^"]*"|"[^"]*"|\S+)', self.raw):
            low = token.lower()
            key, _, value = low.partition(":")
            value_raw = token.split(":", 1)[1].strip('"') if ":" in token else ""
            if key == "ext" and value:
                self.exts |= {"." + v.lstrip(".") for v in value.split(",") if v}
            elif key in ("kind", "type", "тип") and value:
                if value in ("folder", "папка", "dir"):
                    self.folders_only = True
                elif value in ("file", "файл"):
                    self.files_only = True
                else:
                    self.kinds.add(value)
            elif key in ("size", "размер") and value:
                op = value[0] if value[0] in "<>" else ">"
                number = parse_size(value.lstrip("<>="))
                if number is not None:
                    if op == ">":
                        self.min_size = number
                    else:
                        self.max_size = number
            elif key in ("modified", "date", "изменён", "изменен", "дата") and value:
                now = time.time()
                spans = {"today": 86400, "сегодня": 86400, "week": 7 * 86400, "неделя": 7 * 86400,
                         "month": 31 * 86400, "месяц": 31 * 86400, "year": 366 * 86400, "год": 366 * 86400}
                if value in spans:
                    self.after = now - spans[value] if value not in ("today", "сегодня") else \
                        time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
                else:
                    op = value[0] if value[0] in "<>" else ">"
                    try:
                        stamp = time.mktime(time.strptime(value.lstrip("<>="), "%Y-%m-%d"))
                        if op == ">":
                            self.after = stamp
                        else:
                            self.before = stamp
                    except ValueError:
                        pass
            elif key in ("content", "содержимое", "text") and value_raw:
                self.content = value_raw.lower()
            else:
                word = token.strip('"')
                if not word:
                    continue
                if any(ch in word for ch in "*?["):
                    self.globs.append(word.lower())
                else:
                    self.words.append(word.lower())

    @property
    def empty(self) -> bool:
        return not (self.words or self.globs or self.exts or self.kinds or self.min_size is not None
                    or self.max_size is not None or self.after or self.before or self.content
                    or self.folders_only or self.files_only)

    def name_matches(self, name: str, is_dir: bool) -> bool:
        low = name.lower()
        if self.folders_only and not is_dir:
            return False
        if self.files_only and is_dir:
            return False
        for word in self.words:
            if word not in low:
                return False
        for pattern in self.globs:
            if not fnmatch.fnmatchcase(low, pattern):
                return False
        if self.exts or self.kinds:
            if is_dir:
                return False
            ext = os.path.splitext(low)[1]
            if self.exts and ext not in self.exts:
                return False
            if self.kinds and not any(ext in KIND_EXT.get(k, set()) for k in self.kinds):
                return False
        return True

    def needs_stat(self) -> bool:
        return self.min_size is not None or self.max_size is not None or bool(self.after or self.before)

    def stat_matches(self, size: int, mtime: float, is_dir: bool) -> bool:
        if (self.min_size is not None or self.max_size is not None) and is_dir:
            return False
        if self.min_size is not None and size < self.min_size:
            return False
        if self.max_size is not None and size > self.max_size:
            return False
        if self.after and mtime < self.after:
            return False
        if self.before and mtime > self.before:
            return False
        return True


def content_matches(path: str, needle: str, limit: int = 8 * 1024 * 1024) -> bool:
    try:
        if os.path.getsize(path) > limit:
            return False
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except OSError:
        return False
    if b"\x00" in data[:4096]:
        return False
    for encoding in ("utf-8", "cp1251"):
        try:
            return needle in data.decode(encoding).lower()
        except UnicodeDecodeError:
            continue
    return needle in data.decode("utf-8", "ignore").lower()


def _row(path: str, name: str, is_dir: bool, size: int, mtime: float) -> Dict[str, Any]:
    return {"path": path, "n": name, "d": 1 if is_dir else 0, "s": 0 if is_dir else int(size), "m": int(mtime * 1000),
            "h": 1 if name.startswith(".") else 0}


def walk_search(root: str, query: Query, cancel: Callable[[], bool], limit: int = 5000,
                show_hidden: bool = False, deadline: float = 120.0) -> Iterator[Dict[str, Any]]:
    """Breadth-first scandir walk; yields matching rows (stops at *limit*, *deadline* seconds or *cancel*)."""
    started = time.monotonic()
    found = 0
    queue = [root]
    while queue:
        if cancel() or time.monotonic() - started > deadline:
            return
        folder = queue.pop(0)
        try:
            handle = os.scandir(folder)
        except OSError:
            continue
        with handle:
            for entry in handle:
                name = entry.name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                hidden = name.startswith(".")
                if os.name == "nt" and not hidden:
                    try:
                        hidden = bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 2)
                    except OSError:
                        pass
                if hidden and not show_hidden:
                    continue
                if is_dir and name not in SKIP_DIRS:
                    queue.append(entry.path)
                if not query.name_matches(name, is_dir):
                    continue
                size, mtime = 0, 0.0
                if True:            # size/date are shown in the results anyway
                    try:
                        st = entry.stat(follow_symlinks=False)
                        size, mtime = st.st_size, st.st_mtime
                    except OSError:
                        pass
                if not query.stat_matches(size, mtime, is_dir):
                    continue
                if query.content:
                    if is_dir or os.path.splitext(name)[1].lower() not in TEXT_EXT or not content_matches(entry.path, query.content):
                        continue
                yield _row(entry.path, name, is_dir, size, mtime)
                found += 1
                if found >= limit:
                    return
        if cancel():
            return


def _fsindex():
    """``app.fsindex`` (Round 35, fsindex zone) or None. Its data folder follows MemuAI's
    (``MEMUAI_DATA_DIR`` / ``%APPDATA%\\MemuAI``) even in the frozen MemuFiles, where app.pgdata is excluded."""
    try:
        from .paths import data_root
        os.environ.setdefault("MEMUAI_DATA_DIR", str(data_root()))
        from .. import fsindex        # type: ignore
        return fsindex
    except Exception:  # noqa: BLE001
        return None


def index_available() -> bool:
    return _fsindex() is not None


def index_ready() -> bool:
    """The index can answer right now (Everything runs, or the own index is loaded) — never starts a build."""
    module = _fsindex()
    if module is None:
        return False
    try:
        status = module.status()
    except Exception:  # noqa: BLE001
        return False
    own = status.get("own") or {}
    if status.get("backend") == "everything" or own.get("state") == "ready":
        return True
    # MemuAI builds the index and saves it; this process starts "idle" and never loads it unless asked:
    # warm-load the saved index once in the background (a non-owner only reads it and follows its updates).
    global _WARMED
    if not _WARMED and own.get("state") in ("idle", "waiting") and own.get("file_bytes"):
        _WARMED = True
        try:
            module.start()
        except Exception as exc:  # noqa: BLE001
            log.info("fsindex warm start failed: %s", exc)
    return False


_WARMED = False


def index_status() -> Dict[str, Any]:
    module = _fsindex()
    if module is None:
        return {"available": False}
    try:
        status = module.status()
        return {"available": True, "backend": status.get("backend"), "state": (status.get("own") or {}).get("state"),
                "entries": (status.get("own") or {}).get("entries"),
                "everything": bool((status.get("everything") or {}).get("running"))}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": type(exc).__name__}


_KEY_MAP = {"modified": "dm", "date": "dm", "изменён": "dm", "изменен": "dm", "дата": "dm",
            "kind": "type", "тип": "type", "размер": "size"}


def to_index_query(text: str) -> str:
    """MemuFiles syntax → app.fsindex (Everything) syntax; ``content:`` is filtered afterwards, not sent."""
    out = []
    for token in re.findall(r'(\w+:"[^"]*"|"[^"]*"|\S+)', text or ""):
        key, sep, value = token.partition(":")
        low = key.lower()
        if sep and low in ("content", "содержимое", "text"):
            continue
        if sep and low in _KEY_MAP:
            mapped = _KEY_MAP[low]
            if mapped == "type" and value.lower() in ("folder", "папка", "dir"):
                out.append("folder:")
                continue
            if mapped == "type" and value.lower() in ("file", "файл"):
                out.append("file:")
                continue
            out.append(f"{mapped}:{value}")
        else:
            out.append(token)
    return " ".join(out)


def index_search(query: Query, roots: Optional[List[str]], limit: int) -> Optional[List[Dict[str, Any]]]:
    """Ask app.fsindex; None when it is missing, still building or failing (the caller walks instead)."""
    module = _fsindex()
    if module is None:
        return None
    text = to_index_query(query.raw)
    if not text.strip():
        return None
    try:
        if hasattr(module, "search_ex"):
            got = module.search_ex(text, limit=min(max(limit, 200), 2000), roots=roots)
            if got.get("error") and not got.get("results"):
                return None
            rows = got.get("results") or []
        else:
            rows = module.search(text, limit=min(max(limit, 200), 2000), roots=roots)
    except Exception as exc:  # noqa: BLE001
        log.info("fsindex search failed: %s", exc)
        return None
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        path = str(row["path"])
        name = str(row.get("name") or os.path.basename(path))
        is_dir = bool(row.get("is_dir"))
        if roots and not any(_inside(path, r) for r in roots):
            continue
        size = int(row.get("size") or 0)
        mtime = float(row.get("mtime") or 0)
        if mtime > 1e11:            # ms → s
            mtime /= 1000.0
        if query.content and (is_dir or not content_matches(path, query.content)):
            continue
        out.append(_row(path, name, is_dir, size, mtime))
        if len(out) >= limit:
            break
    return out


def _inside(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)
