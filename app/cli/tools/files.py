"""File tools: Read, Write, Edit, MultiEdit, Glob, Grep, LS, Outline, Symbols,
ProjectInfo and Git.

Safety rules the model cannot talk its way around:

* an existing file must be read in this session before it is edited or
  overwritten, and must not have changed on disk since (someone else's newer
  change would otherwise be lost silently);
* every write is preceded by a checkpoint of the original file
  (:mod:`app.code_checkpoints`), so ``/rewind`` and ``/undo`` work without git;
* encodings (UTF-8 with or without BOM, UTF-16, cp1251) and line endings of
  existing files are preserved.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import fnmatch
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ... import code_checkpoints, code_tools
from ...work_tools import (SKIP_DIRS, WorkError, _atomic_write, _eol, _lf, _with_eol, read_text_meta,
                           tool_outline, unified_diff)
from ..i18n import plural, tr
from ..file_cache import TextFiles, stamp as file_stamp
from .base import (Prepared, Tool, ToolContext, ToolError, ToolResult, arg, display_path, flag, items, relative_key,
                   structured, whole, resolve_path, workspace_of)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
BINARY_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".class", ".o", ".zip", ".gz", ".7z",
                   ".rar", ".tar", ".jar", ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg", ".webm",
                   ".ttf", ".otf", ".woff", ".woff2", ".eot", ".psd", ".sqlite", ".db", ".ico", ".bin"}
MAX_READ_FILE = 12 * 1024 * 1024
DEFAULT_LINES = 300
MAX_LINE_CHARS = 2000
MAX_READ_CHARS = 100_000
MAX_GLOB = 250
MAX_GREP_LINES = 400


# ---------------------------------------------------------------- file index

#: Folders that hold application data or the system, never a project. A walk does not enter them when the
#: root is a whole drive or user profile (where one ``AppData`` alone has millions of files).
_BROAD_SKIP = {"appdata", "application data", "local settings", "windows", "program files", "program files (x86)",
               "programdata", "$recycle.bin", "system volume information", "recovery", "library", "snap",
               "winsxs", "$windows.~bt", "perflogs"}


def is_broad_root(root: Path) -> bool:
    """A whole drive or user profile. Scanning it takes minutes and its files are not a project."""
    try:
        resolved = Path(root).resolve()
    except OSError:
        return False
    if resolved.parent == resolved:                              # C:\ or /
        return True
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    if home is not None and (resolved == home or resolved in home.parents):
        return True
    if resolved.name.lower() in _BROAD_SKIP:
        return True
    try:
        with os.scandir(resolved) as entries:
            top = {entry.name.lower() for entry in entries}
    except OSError:
        return False
    return "appdata" in top or {"windows", "program files"} <= top


def _is_link(entry: "os.DirEntry") -> bool:
    """A symbolic link or a Windows junction: following it can loop or leave the project."""
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        return bool(is_junction()) if is_junction else False
    except OSError:
        return True


#: Caches and environments that are never worth an @mention or a Glob hit even when a repository forgot to ignore
#: them (``git ls-files -o`` lists untracked ``__pycache__/*.pyc`` of a project without a .gitignore).
_JUNK_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", "venv", ".tox"}


def _junk(path: str) -> bool:
    """Round 34: «@cal» offered ``__pycache__/calc.cpython-312.pyc`` next to ``calc.py``."""
    parts = path.split("/")
    return any(part in _JUNK_DIRS for part in parts[:-1]) or parts[-1].endswith((".pyc", ".pyo"))


class FileIndex:
    """Project file list for ``@`` mentions, the project overview and Glob/Grep.

    ``git ls-files`` (respects .gitignore) when the folder is a repository, otherwise a bounded
    breadth-first walk: shallow files first, never through links, and within a time and size budget.
    A whole drive or user profile gets a much smaller budget and is not walked into ``AppData``.

    ``files()`` never takes unbounded time. Interface code calls it with ``sync=False``: it hands back
    what is known at once and a background thread refreshes it (``on_ready`` fires when that is done).
    """

    TTL = 8.0
    LIMIT = 60_000
    BROAD_LIMIT = 6_000
    BUDGET = 3.0
    BROAD_BUDGET = 1.2
    BROAD_DEPTH = 3

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._files: List[str] = []
        self._at = 0.0
        self._scanned = False
        self._generation = 0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.truncated = False
        self.source = ""
        self.broad = False
        self.on_ready: Optional[Any] = None         # called (from the scanning thread) after each scan

    # ------------------------------------------------------------ public
    def invalidate(self) -> None:
        """Something changed on disk (a write, an undo): the next ``files()`` looks again."""
        self._generation += 1
        self._at = 0.0

    @property
    def scanning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def files(self, *, refresh: bool = False, sync: bool = True, wait: float = 0.0) -> List[str]:
        """The project's files as posix paths relative to the root.

        ``sync`` (tools, which already run off the interface thread): a stale list is rebuilt right here.
        ``sync=False`` (the interface): return what is known and rebuild in the background; ``wait`` is
        how long to wait for a first list when there is none yet.
        """
        stale = refresh or not self._scanned or time.time() - self._at >= self.TTL
        if not stale:
            return self._files
        if sync:
            self.scan()
            return self._files
        self._scan_in_background()
        if wait and not self._scanned:
            self._done.wait(wait)
        return self._files

    def scan(self) -> List[str]:
        """Rebuild the list now, in the calling thread."""
        requested = time.time()
        with self._lock:
            if self._scanned and self._at >= requested:         # another thread finished a scan meanwhile
                return self._files
            self._done.clear()
            generation = self._generation
            try:
                rows, source, complete = self._collect()
            except Exception:
                rows, source, complete = [], "", False
            self._files = rows
            self.source = source
            self.truncated = not complete
            self._scanned = True
            # An invalidate() that arrived while scanning leaves the list stale, so it is looked at again.
            self._at = time.time() if generation == self._generation else 0.0
            self._done.set()
        callback = self.on_ready
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        return self._files

    def describe(self) -> str:
        """One line for the interface when the list is not the whole project."""
        if not self.truncated:
            return ""
        if self.broad:
            return (f"This is a very large folder — {len(self._files):,} files indexed. "
                    "Type a path (src/) after @ to browse deeper.")
        return f"{len(self._files):,} files indexed (the project is larger than the limit)."

    # ------------------------------------------------------------ scanning
    def _scan_in_background(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=self.scan, name="memucode-file-index", daemon=True)
        self._thread = thread
        thread.start()

    def _collect(self) -> Tuple[List[str], str, bool]:
        self.broad = is_broad_root(self.root)
        if not self.broad:
            rows = self._git_files()
            if rows is not None:
                return rows[:self.LIMIT], "git", len(rows) <= self.LIMIT
        rows, complete = self._walk()
        return rows, "walk", complete

    def _git_files(self) -> Optional[List[str]]:
        if not (self.root / ".git").exists() or not shutil.which("git"):
            return None
        try:
            completed = subprocess.run(
                ["git", "-c", "core.quotepath=off", "ls-files", "-co", "--exclude-standard", "-z"],
                cwd=str(self.root), capture_output=True, timeout=12,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        rows = [name for name in completed.stdout.decode("utf-8", "replace").split("\0") if name and not _junk(name)]
        return sorted(dict.fromkeys(rows))

    def _link_is_inside_file(self, entry: "os.DirEntry") -> bool:
        try:
            target = Path(entry.path).resolve(strict=True)
            root = self.root.resolve()
            return target.is_file() and (target == root or root in target.parents)
        except (OSError, RuntimeError):
            return False

    def _walk(self) -> Tuple[List[str], bool]:
        """Breadth-first, so a cut-off keeps the shallow files. Returns (files, finished within the limits)."""
        broad = self.broad
        limit = self.BROAD_LIMIT if broad else self.LIMIT
        deadline = time.monotonic() + (self.BROAD_BUDGET if broad else self.BUDGET)
        max_depth = self.BROAD_DEPTH if broad else 200
        rows: List[str] = []
        complete = True
        queue: "deque[Tuple[str, str, int]]" = deque([(str(self.root), "", 0)])
        while queue:
            if time.monotonic() > deadline:
                complete = False
                break
            folder, prefix, depth = queue.popleft()
            try:
                with os.scandir(folder) as handle:
                    entries = sorted(handle, key=lambda entry: entry.name.lower())
            except OSError:
                continue
            subfolders: List[Tuple[str, str, int]] = []
            for entry in entries:
                name = entry.name
                try:
                    is_folder = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_folder:
                    if name in SKIP_DIRS or (name.startswith(".") and name != ".github"):
                        continue
                    if broad and name.lower() in _BROAD_SKIP:
                        continue
                    if _is_link(entry):
                        continue
                    if depth >= max_depth:
                        complete = False
                        continue
                    subfolders.append((entry.path, prefix + name + "/", depth + 1))
                else:
                    # A link to a folder is not a folder for is_dir(follow_symlinks=False), so it
                    # used to be listed as a "file"; a link leaving the project is never listed.
                    if _is_link(entry) and not self._link_is_inside_file(entry):
                        continue
                    if broad and (name.startswith(".") or name.lower() in ("ntuser.dat", "desktop.ini", "thumbs.db")
                                  or name.lower().startswith(("ntuser.", "$"))):
                        continue                 # profile bookkeeping, not something to mention
                    rows.append(prefix + name)
                    if len(rows) >= limit:
                        rows.sort()
                        return rows, False
            queue.extend(subfolders)
        rows.sort()
        return rows, complete


def glob_regex(pattern: str) -> re.Pattern:
    """Glob with ``**``, ``*``, ``?``, ``[...]`` and ``{a,b}`` → compiled regex (posix paths)."""
    pattern = pattern.replace("\\", "/")
    while pattern.startswith("./"):                 # "./src/*.py"; never lstrip("./"), which ate ".github"
        pattern = pattern[2:]
    out, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1:end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                index = end
        elif char == "{":
            end = pattern.find("}", index + 1)
            if end < 0:
                out.append(re.escape(char))
            else:
                options = pattern[index + 1:end].split(",")
                out.append("(?:" + "|".join(re.escape(option) for option in options) + ")")
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$", re.I if os.name == "nt" else 0)


def path_matches(pattern: str, rel: str) -> bool:
    """gitignore-like: a pattern without ``/`` matches the file name anywhere."""
    pattern = pattern.strip()
    if not pattern:
        return True
    if "/" not in pattern.strip("/"):
        return bool(glob_regex(pattern).match(rel.rsplit("/", 1)[-1])) or bool(glob_regex(pattern).match(rel))
    return bool(glob_regex(pattern.lstrip("/")).match(rel))


# ---------------------------------------------------------------- helpers

def _rel(ctx: ToolContext, path: Path) -> str:
    return display_path(ctx.runtime, path)


def _check_fresh(ctx: ToolContext, path: Path) -> None:
    """The model must have read the current version of an existing file."""
    if not path.is_file():
        return
    state = ctx.session.read_state.get(os.path.normcase(str(path)))
    if state is None:
        raise ToolError(f"{_rel(ctx, path)} has not been read in this session. Read it first "
                        "(at least the part you change), then edit it.")
    stat = path.stat()
    current = (stat.st_mtime_ns, stat.st_size) if len(state) == 2 else file_stamp(stat)
    if state != current:
        raise ToolError(f"{_rel(ctx, path)} was modified on disk after you read it (by the user, a "
                        "formatter or a command). Read it again before editing.")


def remember_read(ctx: ToolContext, path: Path) -> None:
    try:
        stat = path.stat()
    except OSError:
        return
    ctx.session.read_state[os.path.normcase(str(path))] = file_stamp(stat)


def _snapshot(ctx: ToolContext, path: Path) -> None:
    base = workspace_of(ctx.runtime, path) or path.parent
    rel = path.relative_to(base).as_posix()
    try:
        code_checkpoints.require_snapshot(ctx.session.id, base, rel, label=ctx.call.name, turn=ctx.turn)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _load_for_edit(path: Path) -> Tuple[str, str, str]:
    """(text with \\n, original EOL, encoding)."""
    try:
        text, truncated, encoding = read_text_meta(path, limit=MAX_READ_FILE)
    except WorkError as exc:
        raise ToolError(str(exc)) from exc
    if truncated:
        raise ToolError("The file is larger than 12 MB; edit it with a script via Bash instead.")
    return _lf(text), _eol(text), encoding


_PATH_LOCKS: Dict[str, "asyncio.Lock"] = {}


def path_lock(path: Path) -> "asyncio.Lock":
    """Writers of one file take turns: two agents (or an agent and a slow permission dialog) must not
    overwrite each other's edit with text computed from an older version."""
    key = os.path.normcase(str(path))
    lock = _PATH_LOCKS.get(key)
    if lock is None:
        lock = _PATH_LOCKS[key] = asyncio.Lock()
    return lock


def _diff_stats(diff: str) -> Tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def lines_word(count: int) -> str:
    return plural(count, "строка", "строки", "строк", "line", "lines")


def _change_summary(rel: str, diff: str, created: bool = False, lines: int = 0) -> str:
    if created:
        return tr(f"Создан {rel} ({lines_word(lines)})", f"Created {rel} ({lines_word(lines)})")
    added, removed = _diff_stats(diff)
    return tr(f"Изменён {rel}: +{added} −{removed}", f"Updated {rel}: +{added} −{removed}")


def _write(ctx: ToolContext, path: Path, text: str, *, eol: str = "\n", encoding: str = "utf-8",
           existed: bool = True) -> None:
    _snapshot(ctx, path)
    try:
        body = _with_eol(text, eol) if existed else text
        _atomic_write(path, body, encoding)
    except OSError as exc:
        raise ToolError(f"Could not write {_rel(ctx, path)}: {exc}") from exc
    remember_read(ctx, path)
    ctx.runtime.files.invalidate()
    ctx.session.note_edit(str(path), text)


def _numbered(lines: Sequence[str], start: int) -> str:
    width = max(4, len(str(start + len(lines))))
    out = []
    for offset, line in enumerate(lines):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + f"… [{len(line) - MAX_LINE_CHARS} more characters]"
        out.append(f"{start + offset:>{width}}\t{line}")
    return "\n".join(out)


# ---------------------------------------------------------------- Read

def own_output(path: Path) -> bool:
    """Round 32: a "see more" file MemuCode itself saved for a shortened command output — reading it back is
    not reading outside the project."""
    try:
        from ..paths import outputs_dir
        path.resolve().relative_to(outputs_dir().resolve())
        return path.name.startswith("bash-") and path.suffix == ".log"
    except (ValueError, OSError):
        pass
    try:
        # Round 33: MemuCode's own user documentation (the built-in memucode-guide skill points at it).
        from ...docs import docs_dir
        path.resolve().relative_to(docs_dir().resolve())
        return path.suffix.lower() == ".md"
    except (ValueError, OSError, ImportError):
        return False


class ReadTool(Tool):
    name = "Read"
    kind = "read"
    parallel = True
    short = "read a file with line numbers (offset/limit for big files); images, PDF and notebooks too"
    description = (
        "Reads a file. Returns lines prefixed with their numbers (cat -n style). By default up to 300 "
        "lines from the start; use offset (1-based line) and limit for large files. Images (png, jpg, gif, "
        "webp) are shown to you visually, PDFs as text, Jupyter notebooks as cells. Read files before "
        "editing them. Prefer Grep to locate the relevant symbol, then Read that range. "
        "Set limit explicitly for a larger section; force=true replays an unchanged range. "
        "Several independent reads may be requested in the same turn.")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "Absolute path or a path relative to the project root"},
        "offset": {"type": "integer", "description": "1-based line to start from"},
        "limit": {"type": "integer", "description": "Number of lines to read"},
        "force": {"type": "boolean", "description": "Return content even if this unchanged range was already read"},
        "question": {"type": "string", "description": "For an image: what to look for. A model that cannot see "
                                                      "images gets the image model's answer to exactly this"},
    }}
    required = ("file_path",)

    def permission_key(self, runtime, args):
        return relative_key(runtime, resolve_path(runtime, arg(args, "file_path", "path", "filename")))

    async def prepare(self, ctx, args):
        path = resolve_path(ctx.runtime, arg(args, "file_path", "path", "filename"))
        return Prepared(key=relative_key(ctx.runtime, path), preview=str(path),
                        title=tr("Чтение файла вне проекта", "Read a file outside the project"),
                        outside=workspace_of(ctx.runtime, path) is None and not own_output(path),
                        data={"path": path})

    async def run(self, ctx, args, prepared):
        path: Path = prepared.data["path"]
        if not path.exists():
            near = _similar_paths(ctx, path)
            hint = (" Did you mean: " + ", ".join(near) + "?") if near else ""
            raise ToolError(f"File does not exist: {_rel(ctx, path)}.{hint}")
        if path.is_dir():
            raise ToolError(f"{_rel(ctx, path)} is a directory. Use LS or Glob to list it.")
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            result = await asyncio.to_thread(_read_image, ctx, path)
            question = str(arg(args, "question", "ask", default="")).strip()
            if question:
                result.meta["question"] = question            # the image model answers this, if the model needs one
            return result
        if suffix == ".pdf":
            return await asyncio.to_thread(_read_pdf, ctx, path, args)
        if suffix == ".ipynb":
            return await asyncio.to_thread(_read_notebook, ctx, path)
        if suffix in BINARY_SUFFIXES:
            raise ToolError(f"{_rel(ctx, path)} is a binary file ({suffix}); it cannot be read as text.")
        return await asyncio.to_thread(self._read_text, ctx, path, args)

    def _read_text(self, ctx: ToolContext, path: Path, args: Dict[str, Any]) -> ToolResult:
        offset = whole(arg(args, "offset", "start_line"), 1, "offset", low=1)
        end_line = arg(args, "end_line")
        if end_line not in (None, ""):
            limit = max(1, whole(end_line, offset, "end_line") - offset + 1)
        else:
            limit = whole(arg(args, "limit"), DEFAULT_LINES, "limit", low=1)
        rel = _rel(ctx, path)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cache_key = (os.path.normcase(str(path)), offset, limit)
        session = ctx.session
        seen = session.read_cache.get(cache_key)
        if (seen and seen[0] == signature and seen[1] == session.context_epoch
                and (len(seen) < 4 or seen[3] == file_stamp(stat)) and not flag(args.get("force"))):
            shown_end = seen[2] if len(seen) > 2 else offset + limit - 1
            return ToolResult(ok=True, output=(
                f"[{rel} lines {offset}-{shown_end} are unchanged since you read them "
                "earlier in this conversation; use that content instead of reading it again.]"),
                summary=tr("Без изменений с прошлого чтения", "Unchanged since the last read"),
                meta={"unchanged": True})
        cache = getattr(session, "text_files", None)
        if cache is None:
            cache = session.text_files = TextFiles()
        key, stamp = os.path.normcase(str(path)), file_stamp(stat)
        snapshot = cache.get(key, stamp)
        if snapshot is not None:
            lines, truncated = snapshot.lines, snapshot.truncated
        else:
            try:
                text, truncated, _encoding = read_text_meta(path, limit=MAX_READ_FILE)
            except WorkError as exc:
                raise ToolError(str(exc)) from exc
            lines = _lf(text).split("\n")
            if lines and lines[-1] == "":
                lines.pop()
        # The edit freshness guard must describe the version actually read, even
        # if a formatter/user replaces the file while it is being decoded.
        session.read_state[os.path.normcase(str(path))] = stamp
        after = path.stat()
        stable = stamp == file_stamp(after)
        if stable and snapshot is None:
            cache.put(key, stamp, lines, truncated)
        total = len(lines)
        if total == 0:
            return ToolResult(ok=True, output=f"(empty file: {rel})", summary=tr("Пустой файл", "Empty file"))
        if offset > total:
            raise ToolError(f"{rel} has only {total} lines; offset {offset} is past the end.")
        window = lines[offset - 1:offset - 1 + limit]
        body = _numbered(window, offset)
        cut_note = ""
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS]
            last_line = offset + body.count("\n")
            body = body[:body.rfind("\n")] if "\n" in body else body
            cut_note = (f"\n… [output limited to {MAX_READ_CHARS} characters; continue with "
                        f"offset={last_line}]")
            shown_end = last_line - 1
        else:
            shown_end = offset + len(window) - 1
        if shown_end < total and not cut_note:
            cut_note = f"\n… [lines {offset}-{shown_end} of {total}; continue with offset={shown_end + 1}]"
        if truncated:
            cut_note += "\n… [file is larger than 12 MB; only the beginning was read]"
        if stable:
            session.read_cache[cache_key] = (signature, session.context_epoch, shown_end, stamp)
        else:
            session.read_cache.pop(cache_key, None)
            cut_note += "\n[File changed on disk during this read; read it again before editing.]"
        shown = shown_end - offset + 1
        summary = tr(f"Прочитано: {lines_word(shown)}", f"Read {lines_word(shown)}")
        if shown < total:
            summary += tr(f" из {total}", f" of {total}")
        return ToolResult(ok=True, output=body + cut_note, summary=summary,
                          meta={"lines": shown, "total": total})


def _similar_paths(ctx: ToolContext, path: Path) -> List[str]:
    name = path.name.lower()
    files = ctx.runtime.files.files()
    names = {rel.rsplit("/", 1)[-1].lower(): rel for rel in files}
    close = difflib.get_close_matches(name, list(names), n=3, cutoff=0.6)
    return [names[item] for item in close]


def _read_image(ctx: ToolContext, path: Path) -> ToolResult:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ToolError("Pillow is required to read images") from exc
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            frame = image.convert("RGB") if image.mode not in ("RGB", "L") else image.copy()
    except Exception as exc:
        raise ToolError(f"Could not open image {_rel(ctx, path)}: {exc}") from exc
    frame.thumbnail((1568, 1568))
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=85)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return ToolResult(ok=True, output=f"Image {_rel(ctx, path)} ({width}×{height}) is attached below.",
                      summary=tr(f"Изображение {width}×{height}", f"Image {width}×{height}"),
                      images=[{"media_type": "image/jpeg", "data": data, "width": frame.size[0],
                               "height": frame.size[1], "name": path.name}])


def _read_pdf(ctx: ToolContext, path: Path, args: Dict[str, Any]) -> ToolResult:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ToolError("pypdf is required to read PDF files") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ToolError(f"Could not open PDF: {exc}") from exc
    total = len(reader.pages)
    first = whole(arg(args, "offset"), 1, "offset", low=1)
    count = whole(arg(args, "limit"), 20, "limit", low=1, high=40)
    parts = []
    for number in range(first, min(total, first + count - 1) + 1):
        try:
            text = reader.pages[number - 1].extract_text() or ""
        except Exception:
            text = "[page could not be extracted]"
        parts.append(f"--- page {number} ---\n{text.strip()}")
    body = "\n\n".join(parts)[:MAX_READ_CHARS]
    last = min(total, first + count - 1)
    note = f"\n… [pages {first}-{last} of {total}; continue with offset={last + 1}]" if last < total else ""
    remember_read(ctx, path)
    return ToolResult(ok=True, output=body + note,
                      summary=tr(f"PDF: страницы {first}–{last} из {total}", f"PDF: pages {first}–{last} of {total}"))


def _read_notebook(ctx: ToolContext, path: Path) -> ToolResult:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"Could not parse notebook: {exc}") from exc
    cells = data.get("cells") or []
    parts = []
    for index, cell in enumerate(cells):
        kind = cell.get("cell_type", "code")
        source = "".join(cell.get("source") or [])
        parts.append(f"# cell {index} ({kind})\n{source}")
        outputs = []
        for output in cell.get("outputs") or []:
            if "text" in output:
                outputs.append("".join(output.get("text") or []))
            elif isinstance(output.get("data"), dict) and "text/plain" in output["data"]:
                outputs.append("".join(output["data"]["text/plain"]))
        if outputs:
            parts.append("# output\n" + "\n".join(outputs)[:4000])
    remember_read(ctx, path)
    return ToolResult(ok=True, output="\n\n".join(parts)[:MAX_READ_CHARS],
                      summary=tr(f"Ноутбук: {len(cells)} ячеек", f"Notebook: {len(cells)} cells"))


# ---------------------------------------------------------------- Write

class WriteTool(Tool):
    name = "Write"
    kind = "edit"
    plan_safe = False
    short = "create a new file or replace a file completely (read an existing file first)"
    description = (
        "Writes a whole file, creating parent folders. Overwrites an existing file completely, so for changes to "
        "an existing file use Edit/MultiEdit; an existing file must be read first in this session. Do not create "
        "files the task does not need (no unrequested READMEs or notes).")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "Path of the file to write"},
        "content": {"type": "string", "description": "The complete new content"},
    }}
    required = ("file_path", "content")
    allow_empty = ("content",)

    def permission_key(self, runtime, args):
        return relative_key(runtime, resolve_path(runtime, arg(args, "file_path", "path")))

    async def prepare(self, ctx, args):
        path = resolve_path(ctx.runtime, arg(args, "file_path", "path"))
        content = next((args[name] for name in ("content", "text", "file_text", "contents")
                        if isinstance(args.get(name), str)), None)        # "" is a real file: the empty one
        if not isinstance(content, str):
            raise ToolError("Write needs the whole file as a string in `content`.")
        if path.is_dir():
            raise ToolError(f"{_rel(ctx, path)} is a directory.")
        existed = path.is_file()
        before, eol, encoding = ("", "\n", "utf-8")
        if existed:
            _check_fresh(ctx, path)
            before, eol, encoding = _load_for_edit(path)
        after = _lf(content)
        rel = _rel(ctx, path)
        diff = unified_diff(before, after, rel, limit=600)
        return Prepared(key=relative_key(ctx.runtime, path), diff=diff if existed else "",
                        preview=diff if existed else after[:4000],
                        title=tr("Перезаписать файл", "Overwrite file") if existed else tr("Создать файл", "Create file"),
                        outside=workspace_of(ctx.runtime, path) is None,
                        data={"path": path, "after": after, "eol": eol, "encoding": encoding, "existed": existed,
                              "before": before})

    async def run(self, ctx, args, prepared):
        data = prepared.data
        path: Path = data["path"]
        rel = _rel(ctx, path)
        async with path_lock(path):
            if data["existed"]:
                current, _eol_now, _enc_now = await asyncio.to_thread(_load_for_edit, path)
                if current != data["before"]:
                    raise ToolError(f"{rel} changed while this write was waiting (another agent or you edited it). "
                                    "Read the file again and redo the change, so nobody's work is overwritten.")
            elif path.exists():
                raise ToolError(f"{rel} was created while this write waited. Read it first.")
            await asyncio.to_thread(_write, ctx, path, data["after"], eol=data["eol"], encoding=data["encoding"],
                                    existed=data["existed"])
        lines = 0 if not data["after"] else data["after"].count("\n") + (0 if data["after"].endswith("\n") else 1)
        if data["existed"]:
            diff = unified_diff(data["before"], data["after"], rel, limit=600)
            return ToolResult(ok=True, output=f"Wrote {rel} ({lines} lines).", diff=diff,
                              summary=_change_summary(rel, diff))
        return ToolResult(ok=True, output=f"Created {rel} ({lines} lines).",
                          summary=_change_summary(rel, "", created=True, lines=lines),
                          detail=data["after"][:3000], meta={"lines_added": lines})


# ---------------------------------------------------------------- Edit

_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
                         "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
                         "\u00a0": " ", "\u2009": " ", "\u202f": " "})
_LINE_NUMBER = re.compile(r"^ *\d+(?:\t|\u2192|: |\| )")


def _same_quotes(text: str) -> str:
    """Typographic quotes and odd spaces as plain ASCII — one character for one, so offsets stay valid."""
    return text.translate(_QUOTES)


def _strip_line_numbers(text: str) -> Optional[str]:
    """``old_string`` copied from Read output together with the ``  12\\t`` prefixes → the text without them;
    None when not every non-empty line carries such a prefix (then the digits are real content)."""
    lines = text.split("\n")
    filled = [line for line in lines if line.strip()]
    if not filled or not all(_LINE_NUMBER.match(line) for line in filled):
        return None
    return "\n".join(_LINE_NUMBER.sub("", line, count=1) for line in lines)


def _indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _line_spans(text: str, old: str, *, loose_indent: bool) -> List[Tuple[int, int, str]]:
    """Places where the lines of ``old`` match whole lines of ``text`` ignoring trailing whitespace (and, with
    ``loose_indent``, leading whitespace too). Each hit is (begin, end, indent of the file's first matched line);
    ``end`` stops before the newline of the last matched line."""
    lines = text.split("\n")
    want = old.split("\n")
    if not want or not any(line.strip() for line in want):
        return []

    def norm(line: str) -> str:
        return line.strip() if loose_indent else line.rstrip().replace("\t", "    ")

    target = [norm(line) for line in want]
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    first = next(index for index, line in enumerate(want) if line.strip())
    hits: List[Tuple[int, int, str]] = []
    for start in range(0, len(lines) - len(want) + 1):
        if norm(lines[start + first]) != target[first]:
            continue
        if all(norm(lines[start + offset]) == target[offset] for offset in range(len(want))):
            hits.append((starts[start], starts[start + len(want)] - 1, _indent_of(lines[start + first])))
            if len(hits) > 3:
                break
    return hits


def _retab(text: str, width: int, to_tabs: bool) -> str:
    """The leading indentation of every line of ``text`` turned into tabs (``width`` spaces each) or back."""
    out = []
    for line in text.split("\n"):
        lead = _indent_of(line)
        if to_tabs:
            spaces = len(lead.expandtabs(width))
            lead = "\t" * (spaces // width) + " " * (spaces % width)
        else:
            lead = lead.expandtabs(width)
        out.append(lead + line[len(_indent_of(line)):])
    return "\n".join(out)


def _indents(text: str) -> List[str]:
    return [_indent_of(line) for line in text.split("\n") if line.strip() and _indent_of(line)]


def _tab_width(old: str, file_indent: str, file_tabs: bool) -> int:
    """How many spaces one tab stands for when the model and the file disagree about tabs: the step of the
    space-indented side (old's own levels, or the file's indent against old's tabs), 4 when it says nothing."""
    if file_tabs:
        widths = [len(indent) for indent in _indents(old)]
        return math.gcd(*widths) if widths else 4
    first = _indent_of(next((line for line in old.split("\n") if line.strip()), ""))
    return len(file_indent) // len(first) if first and len(file_indent) % len(first) == 0 else 4


def _reindent(new: str, old: str, matched: str, *, width: int = 0) -> str:
    """``new`` shifted the way the file (its ``matched`` lines) is indented relative to ``old`` (a model often drops
    or adds one level). A file indented with tabs gets tabs even when the model wrote spaces (and the other way
    round); ``width`` is the spaces per tab when the match already fixed it."""
    first = next((line for line in old.split("\n") if line.strip()), "")
    old_indent = _indent_of(first)
    file_indent = _indent_of(next((line for line in matched.split("\n") if line.strip()), ""))
    file_side, old_side = _indents(matched), _indents(old) + _indents(new)
    file_tabs = any("\t" in indent for indent in file_side)
    old_tabs = any("\t" in indent for indent in old_side)
    if file_side and old_side and file_tabs != old_tabs:
        width = width or _tab_width(old, file_indent, file_tabs)
        new, old_indent = _retab(new, width, file_tabs), _retab(old_indent, width, file_tabs)
    if old_indent == file_indent:
        return new
    out = []
    for line in new.split("\n"):
        if not line.strip():
            out.append(line)
        elif file_indent.startswith(old_indent):
            out.append(file_indent[len(old_indent):] + line)
        elif old_indent.startswith(file_indent) and line.startswith(old_indent[:len(old_indent) - len(file_indent)]):
            out.append(line[len(old_indent) - len(file_indent):])
        else:
            out.append(line)
    return "\n".join(out)


def _line_numbers(text: str, needle: str, limit: int = 8) -> List[int]:
    found, position = [], text.find(needle)
    while position >= 0 and len(found) < limit:
        found.append(text.count("\n", 0, position) + 1)
        position = text.find(needle, position + max(1, len(needle)))
    return found


def _closest_hint(text: str, old: str) -> str:
    first = next((line.strip() for line in old.split("\n") if line.strip()), "")
    if not first:
        return ""
    lines = text.split("\n")
    if len(lines) > 20000:
        return ""
    matcher = difflib.SequenceMatcher(None, first[:300])
    best = (0.0, -1)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        matcher.set_seq2(stripped[:300])
        if matcher.real_quick_ratio() <= best[0] or matcher.quick_ratio() <= best[0]:
            continue
        ratio = matcher.ratio()
        if ratio > best[0]:
            best = (ratio, index)
    ratio, index = best
    if ratio < 0.5 or index < 0:
        return ""
    low, high = max(0, index - 2), min(len(lines), index + 3 + old.count("\n"))
    snippet = "\n".join(f"{n + 1:>5}\t{lines[n]}" for n in range(low, high))
    return f"\nThe closest text is near line {index + 1} (compare whitespace, quotes and spelling):\n{snippet}"


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> Tuple[str, int, bool]:
    """(new text, replacements, fuzzy?) or raises ToolError.

    Matching goes from strict to forgiving and only ever accepts a *unique* forgiving match: exact text; the text
    without Read's line-number prefixes; typographic quotes as ASCII; whole lines ignoring trailing whitespace;
    whole lines ignoring indentation (the replacement is re-indented to the file's level)."""
    old, new = _lf(old), _lf(new)
    if old == new:
        raise ToolError("old_string and new_string are identical — nothing to change.")
    if not old:
        raise ToolError("old_string is empty. Give the exact text to replace (copy it from a fresh Read).")
    count = text.count(old)
    if count == 0:
        stripped = _strip_line_numbers(old)
        if stripped is not None and stripped != old and stripped.strip():
            # The replacement was copied the same way; a line the model added has no prefix of its own.
            new_stripped = "\n".join(_LINE_NUMBER.sub("", line, count=1) for line in new.split("\n"))
            try:
                result, replaced, _fuzzy = apply_edit(text, stripped, new_stripped, replace_all)
                return result, replaced, True
            except ToolError:
                pass
    if count == 0:
        plain_text, plain_old = _same_quotes(text), _same_quotes(old)
        exact = plain_text.count(plain_old) if plain_old != old or plain_text != text else 0
        if exact == 1 or (exact > 1 and replace_all):
            out, position, last = [], plain_text.find(plain_old), 0
            while position >= 0:
                out += [text[last:position], new]
                last = position + len(plain_old)
                if not replace_all:
                    break
                position = plain_text.find(plain_old, last)
            out.append(text[last:])
            return "".join(out), (exact if replace_all else 1), True
        if exact > 1:
            raise ToolError(f"old_string occurs {exact} times (lines {', '.join(map(str, _line_numbers(plain_text, plain_old)))}). "
                            "Include more surrounding lines to make it unique, or pass replace_all: true.")
        core_old, core_new, eats_newline = plain_old, new, False
        if core_old.endswith("\n"):
            core_old = core_old[:-1]
            if core_new.endswith("\n"):
                core_new = core_new[:-1]
            else:
                eats_newline = True
        for loose in (False, True):
            hits = _line_spans(plain_text, core_old, loose_indent=loose)
            if len(hits) == 1:
                begin, end, _indent = hits[0]
                replacement = _reindent(core_new, core_old, plain_text[begin:end], width=0 if loose else 4)
                if eats_newline and end < len(text):
                    end += 1
                return text[:begin] + replacement + text[end:], 1, True
            if len(hits) > 1:
                where = ", ".join(str(text.count("\n", 0, begin) + 1) for begin, _end, _indent in hits[:4])
                raise ToolError(f"old_string matches several places when whitespace is ignored (lines {where}). "
                                "Copy it exactly from a fresh Read and include more surrounding lines.")
        raise ToolError("old_string was not found in the file. Copy it exactly from a fresh Read "
                        "(without the line-number prefix)." + _closest_hint(text, old))
    if count > 1 and not replace_all:
        lines = ", ".join(map(str, _line_numbers(text, old)))
        raise ToolError(f"old_string occurs {count} times (lines {lines}{'…' if count > 8 else ''}). Include more "
                        "surrounding lines to make it unique, or pass replace_all: true to change every occurrence.")
    return (text.replace(old, new) if replace_all else text.replace(old, new, 1)), (count if replace_all else 1), False


class EditTool(Tool):
    name = "Edit"
    kind = "edit"
    plan_safe = False
    short = "replace an exact fragment of a file: {file_path, old_string, new_string, replace_all?}"
    description = (
        "Replaces text in a file. Read the file first in this session. old_string is copied from the Read output "
        "WITHOUT the line-number prefix, with enough surrounding lines to occur exactly once (or set replace_all "
        "to change every occurrence, e.g. a rename). Small whitespace/indentation slips are tolerated only when the "
        "match stays unique; the result says so. Errors name the lines of ambiguous matches or show the closest "
        "text — fix old_string from that, do not guess. The file's encoding and line endings are kept. An empty "
        "old_string on a missing file creates it.")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string"},
        "old_string": {"type": "string", "description": "Exact text to replace"},
        "new_string": {"type": "string", "description": "Replacement text"},
        "replace_all": {"type": "boolean", "description": "Replace every occurrence"},
    }}
    required = ("file_path", "old_string", "new_string")
    allow_empty = ("old_string", "new_string")

    def check_required(self, args):
        # ``apply_patch`` (Codex-style "*** Begin Patch" or a unified diff) is mapped here; without this the model
        # only heard "missing file_path, old_string, new_string" and kept resending the same patch.
        patch = next((value for value in (args.get("input"), args.get("patch"), args.get("diff"))
                      if isinstance(value, str) and value.strip()), "")
        if patch and not args.get("old_string") and re.search(r"^(\*\*\* Begin Patch|--- |@@ )", patch, re.M):
            raise ToolError("Patches are not applied here (the call was NOT executed). Send each change as Edit "
                            "{file_path, old_string, new_string} — or MultiEdit for several changes in one file — "
                            "with old_string copied from a fresh Read; create new files with Write.")
        super().check_required(args)

    def permission_key(self, runtime, args):
        return relative_key(runtime, resolve_path(runtime, arg(args, "file_path", "path")))

    async def prepare(self, ctx, args):
        path = resolve_path(ctx.runtime, arg(args, "file_path", "path"))
        old = arg(args, "old_string", "old_text", "old_str", default="")
        new = arg(args, "new_string", "new_text", "new_str", default="")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old_string and new_string must be strings.")
        rel = _rel(ctx, path)
        if not path.exists():
            if old:
                raise ToolError(f"File does not exist: {rel}. Use Write to create it.")
            after = _lf(new)
            return Prepared(key=relative_key(ctx.runtime, path), preview=after[:4000],
                            title=tr("Создать файл", "Create file"),
                            outside=workspace_of(ctx.runtime, path) is None,
                            data={"path": path, "after": after, "existed": False, "eol": "\n",
                                  "encoding": "utf-8", "before": "", "fuzzy": False, "count": 1})
        if path.is_dir():
            raise ToolError(f"{rel} is a directory.")
        _check_fresh(ctx, path)
        before, eol, encoding = _load_for_edit(path)
        if not old:
            if before.strip():
                raise ToolError("old_string is empty. Give the exact text to replace (copy it from a fresh Read); "
                                f"to replace the whole of {rel} use Write.")
            after, count, fuzzy = _lf(new), 1, False          # a file with nothing in it: the replacement is its text
        else:
            after, count, fuzzy = apply_edit(before, old, new, flag(args.get("replace_all")))
        diff = unified_diff(before, after, rel, limit=600)
        return Prepared(key=relative_key(ctx.runtime, path), preview=diff, diff=diff,
                        title=tr("Изменить файл", "Edit file"), outside=workspace_of(ctx.runtime, path) is None,
                        data={"path": path, "after": after, "existed": True, "eol": eol, "encoding": encoding,
                              "before": before, "fuzzy": fuzzy, "count": count})

    async def run(self, ctx, args, prepared):
        data = prepared.data
        path: Path = data["path"]
        rel = _rel(ctx, path)
        diff, note = prepared.diff, ""
        async with path_lock(path):
            if not data["existed"] and path.exists():
                raise ToolError(f"{rel} was created while this edit waited. Read it, then edit it.")
            if data["existed"]:
                current, eol, encoding = await asyncio.to_thread(_load_for_edit, path)
                if current != data["before"]:
                    # Someone else (another agent, the user, a formatter) changed the file while this call
                    # waited for its turn or for approval: apply the same replacement to the newer text.
                    old = arg(args, "old_string", "old_text", "old_str", default="")
                    new = arg(args, "new_string", "new_text", "new_str", default="")
                    try:
                        after, count, fuzzy = apply_edit(current, old, new, flag(args.get("replace_all")))
                    except ToolError as exc:
                        raise ToolError(f"{rel} changed while this edit was waiting, and the text to replace is no "
                                        f"longer there. Read the file again. ({exc})") from exc
                    data.update(after=after, before=current, eol=eol, encoding=encoding, count=count, fuzzy=fuzzy)
                    diff = unified_diff(current, after, rel, limit=600)
                    note = " (the file had changed meanwhile; the edit was applied to the newer text)"
            await asyncio.to_thread(_write, ctx, path, data["after"], eol=data["eol"], encoding=data["encoding"],
                                    existed=data["existed"])
        if not data["existed"]:
            lines = 0 if not data["after"] else data["after"].count("\n") + (0 if data["after"].endswith("\n") else 1)
            return ToolResult(ok=True, output=f"Created {rel}.", summary=_change_summary(rel, "", True, lines))
        note += " (matched ignoring whitespace differences)" if data["fuzzy"] else ""
        return ToolResult(ok=True, output=f"Edited {rel}: {data['count']} replacement(s){note}.",
                          diff=diff, summary=_change_summary(rel, diff))


class MultiEditTool(Tool):
    name = "MultiEdit"
    kind = "edit"
    plan_safe = False
    short = "several exact replacements in one file, applied in order, all or nothing"
    description = ("Applies several Edit operations to one file in sequence. Each edit works on the "
                   "result of the previous one. If any edit fails, nothing is written.")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string"},
        "edits": {"type": "array", "items": {"type": "object", "properties": {
            "old_string": {"type": "string"}, "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"}}, "required": ["old_string", "new_string"]}},
    }}
    required = ("file_path", "edits")

    def permission_key(self, runtime, args):
        return relative_key(runtime, resolve_path(runtime, arg(args, "file_path", "path")))

    @staticmethod
    def edit_list(value: Any) -> List[Any]:
        """The edits of a call as a list of objects, from the shapes models send: the list itself, a JSON string,
        one edit object, ``{"edits": [...]}``, or ``[old, new]`` pairs."""
        value = structured(value)
        if isinstance(value, dict):
            inner = structured(value.get("edits"))
            if isinstance(inner, list):
                value = inner
            elif any(key in value for key in ("old_string", "old_text", "old_str", "oldString")):
                value = [value]
            else:
                value = [{"old_string": old, "new_string": new} for old, new in value.items()
                         if isinstance(old, str) and isinstance(new, str)]
        if not isinstance(value, list):
            return []
        rows: List[Any] = []
        for item in value:
            item = structured(item)
            if isinstance(item, (list, tuple)) and len(item) in (2, 3) and all(isinstance(x, str) for x in item[:2]):
                item = {"old_string": item[0], "new_string": item[1]}
            if isinstance(item, dict):
                item = {("old_string" if key in ("old_text", "old_str", "oldString", "old") else
                         "new_string" if key in ("new_text", "new_str", "newString", "new") else key): val
                        for key, val in item.items()}
            rows.append(item)
        return rows

    async def prepare(self, ctx, args):
        path = resolve_path(ctx.runtime, arg(args, "file_path", "path"))
        edits = self.edit_list(args.get("edits"))
        if not edits:
            raise ToolError("edits must be a non-empty list of {old_string, new_string}.")
        args["edits"] = edits                      # run() and the replay in the conversation read the same list
        rel = _rel(ctx, path)
        existed = path.is_file()
        if existed:
            _check_fresh(ctx, path)
            before, eol, encoding = _load_for_edit(path)
        else:
            first = edits[0] if isinstance(edits[0], dict) else {}
            if first.get("old_string"):
                raise ToolError(f"File does not exist: {rel}.")
            before, eol, encoding = "", "\n", "utf-8"
        text = before
        for index, edit in enumerate(edits, 1):
            if not isinstance(edit, dict):
                raise ToolError(f"edit #{index} is not an object")
            old = str(edit.get("old_string", edit.get("old_text", "")) or "")
            new = str(edit.get("new_string", edit.get("new_text", "")) or "")
            if not old and not text:
                text = _lf(new)
                continue
            try:
                text, _count, _fuzzy = apply_edit(text, old, new, flag(edit.get("replace_all")))
            except ToolError as exc:
                raise ToolError(f"edit #{index} failed, nothing was written: {exc}") from exc
        diff = unified_diff(before, text, rel, limit=600)
        return Prepared(key=relative_key(ctx.runtime, path), preview=diff, diff=diff,
                        title=tr("Изменить файл", "Edit file"), outside=workspace_of(ctx.runtime, path) is None,
                        data={"path": path, "after": text, "existed": existed, "eol": eol, "before": before,
                              "encoding": encoding, "count": len(edits)})

    @staticmethod
    def _apply(before: str, edits: List[Any]) -> str:
        text = before
        for index, edit in enumerate(edits, 1):
            old = str(edit.get("old_string", edit.get("old_text", "")) or "")
            new = str(edit.get("new_string", edit.get("new_text", "")) or "")
            if not old and not text:
                text = _lf(new)
                continue
            try:
                text, _count, _fuzzy = apply_edit(text, old, new, flag(edit.get("replace_all")))
            except ToolError as exc:
                raise ToolError(f"edit #{index} failed, nothing was written: {exc}") from exc
        return text

    async def run(self, ctx, args, prepared):
        data = prepared.data
        path: Path = data["path"]
        rel = _rel(ctx, path)
        diff, note = prepared.diff, ""
        async with path_lock(path):
            if data["existed"]:
                current, eol, encoding = await asyncio.to_thread(_load_for_edit, path)
                if current != data["before"]:
                    try:
                        after = self._apply(current, args.get("edits") or [])
                    except ToolError as exc:
                        raise ToolError(f"{rel} changed while these edits were waiting. Read the file again. "
                                        f"({exc})") from exc
                    data.update(after=after, before=current, eol=eol, encoding=encoding)
                    diff = unified_diff(current, after, rel, limit=600)
                    note = " (the file had changed meanwhile; the edits were applied to the newer text)"
            elif path.exists():
                raise ToolError(f"{rel} was created while these edits waited. Read it, then edit it.")
            await asyncio.to_thread(_write, ctx, path, data["after"], eol=data["eol"],
                                    encoding=data["encoding"], existed=data["existed"])
        return ToolResult(ok=True, output=f"Applied {data['count']} edits to {rel}.{note}", diff=diff,
                          summary=_change_summary(rel, diff))


# ---------------------------------------------------------------- Glob / Grep / LS

def _search_base(ctx: ToolContext, value: Any) -> Tuple[Path, str]:
    """Folder to search and its prefix relative to the project root."""
    if not value:
        return ctx.runtime.root, ""
    base = resolve_path(ctx.runtime, value)
    if not base.exists():
        raise ToolError(f"Path does not exist: {_rel(ctx, base)}")
    try:
        prefix = base.relative_to(ctx.runtime.root).as_posix()
        return base, "" if prefix == "." else prefix
    except ValueError:
        return base, "\0outside"


def _files_under(ctx: ToolContext, base: Path, prefix: str) -> List[Tuple[str, Path]]:
    """(display path, absolute path) of the files under ``base``."""
    if prefix == "\0outside":
        index = FileIndex(base)
        return [((base / rel).as_posix(), base / rel) for rel in index.files()]
    rows = ctx.runtime.files.files()
    if base.is_file():
        rel = base.relative_to(ctx.runtime.root).as_posix()
        return [(rel, base)]
    if prefix:
        rows = [rel for rel in rows if rel == prefix or rel.startswith(prefix + "/")]
    root = ctx.runtime.root
    return [(rel, root / rel) for rel in rows]


class GlobTool(Tool):
    name = "Glob"
    kind = "read"
    parallel = True
    short = "find files by name pattern (**/*.py, src/**/*.{ts,tsx}); newest first"
    description = ("Fast file pattern matching (respects .gitignore). Supports **, *, ?, [..] and {a,b}. "
                   "Returns matching paths sorted by modification time, newest first.")
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "Glob pattern"},
        "path": {"type": "string", "description": "Folder to search in (default: project root)"},
    }}
    required = ("pattern",)

    def permission_key(self, runtime, args):
        value = arg(args, "path")
        return relative_key(runtime, resolve_path(runtime, value)) if value else "."

    async def prepare(self, ctx, args):
        raw = arg(args, "pattern", "glob", default="*")
        pattern = (raw if isinstance(raw, str) else "*").strip().replace("\\", "/") or "*"
        folder = arg(args, "path")
        if re.match(r"^(?:[A-Za-z]:)?/", pattern) or pattern.startswith("~"):
            # An absolute pattern ("C:/proj/src/**/*.py", "/home/me/x/*.md"): the part before the first wildcard
            # is the folder, the rest the pattern — matching it against relative paths found nothing.
            parts = os.path.expanduser(pattern).replace("\\", "/").split("/")
            cut = next((index for index, part in enumerate(parts) if any(c in part for c in "*?[{")), len(parts) - 1)
            head, tail = "/".join(parts[:cut]) or "/", "/".join(parts[cut:]) or "*"
            if head.endswith(":"):
                head += "/"
            if folder:
                folder = str(Path(str(folder)) / head) if not os.path.isabs(head) else head
            else:
                folder = head
            pattern = tail
        base, prefix = _search_base(ctx, folder)
        return Prepared(key=relative_key(ctx.runtime, base), outside=prefix == "\0outside",
                        title=tr("Поиск файлов вне проекта", "Search files outside the project"),
                        data={"base": base, "prefix": prefix, "pattern": pattern})

    async def run(self, ctx, args, prepared):
        pattern = prepared.data.get("pattern") or "*"
        base, prefix = prepared.data["base"], prepared.data["prefix"]

        def work() -> ToolResult:
            regex = glob_regex(pattern)
            hits = []
            for display, absolute in _files_under(ctx, base, prefix):
                local = display[len(prefix) + 1:] if prefix and prefix != "\0outside" and display.startswith(prefix + "/") else display
                if prefix == "\0outside":
                    local = absolute.relative_to(base).as_posix()
                name_only = "/" not in pattern
                if regex.match(local) or (name_only and regex.match(local.rsplit("/", 1)[-1])):
                    try:
                        mtime = absolute.stat().st_mtime
                    except OSError:
                        continue
                    hits.append((mtime, display))
            hits.sort(key=lambda row: (-row[0], row[1]))
            shown = [row[1] for row in hits[:MAX_GLOB]]
            if not shown:
                return ToolResult(ok=True, output=f"No files match {pattern}.",
                                  summary=tr("Ничего не найдено", "No matches"))
            more = f"\n… and {len(hits) - MAX_GLOB} more (refine the pattern)" if len(hits) > MAX_GLOB else ""
            return ToolResult(ok=True, output="\n".join(shown) + more,
                              summary=tr(f"Найдено файлов: {len(hits)}", f"Found {len(hits)} files"),
                              detail="\n".join(shown[:30]))
        return await asyncio.to_thread(work)


TYPE_GLOBS = {
    "py": "*.{py,pyi}", "js": "*.{js,jsx,mjs,cjs}", "ts": "*.{ts,tsx,mts,cts}", "go": "*.go", "rust": "*.rs",
    "rs": "*.rs", "java": "*.java", "kotlin": "*.{kt,kts}", "c": "*.{c,h}", "cpp": "*.{cpp,cc,cxx,hpp,hh,h}",
    "cs": "*.cs", "php": "*.php", "rb": "*.rb", "ruby": "*.rb", "swift": "*.swift", "css": "*.{css,scss,sass,less}",
    "html": "*.{html,htm}", "md": "*.{md,mdx}", "json": "*.json", "yaml": "*.{yml,yaml}", "toml": "*.toml",
    "sql": "*.sql", "sh": "*.{sh,bash,zsh}", "ps": "*.{ps1,psm1}", "vue": "*.vue", "svelte": "*.svelte",
    "xml": "*.xml", "lua": "*.lua", "dart": "*.dart", "scala": "*.scala",
}


def _decode_for_search(raw: bytes) -> str:
    """File bytes as text for Grep: UTF-8 (with or without BOM), UTF-16 with a BOM, else Windows-1251 — a Russian
    Windows project keeps old files in cp1251, and decoding them as UTF-8 made every Cyrillic search miss."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", "replace")


class GrepTool(Tool):
    name = "Grep"
    kind = "read"
    parallel = True
    short = ("regex search in file contents {pattern, path?, glob?, type?, output_mode?: "
             "files_with_matches|content|count, -i?, -C?, head_limit?, multiline?}")
    description = (
        "Searches file contents with a regular expression (respects .gitignore). output_mode: "
        "'files_with_matches' (default, paths only), 'content' (matching lines with numbers; use -A/-B/-C "
        "for context) or 'count'. Filter files with glob ('*.py') or type ('py', 'ts'). Use this instead "
        "of running grep through Bash.")
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "Regular expression"},
        "path": {"type": "string", "description": "File or folder to search (default: project)"},
        "glob": {"type": "string", "description": "Only files matching this glob"},
        "type": {"type": "string", "description": "File type: py, js, ts, go, rust, java, md, …"},
        "output_mode": {"type": "string", "enum": ["files_with_matches", "content", "count"]},
        "-i": {"type": "boolean", "description": "Case insensitive"},
        "-n": {"type": "boolean", "description": "Line numbers (content mode, default true)"},
        "-A": {"type": "integer"}, "-B": {"type": "integer"}, "-C": {"type": "integer"},
        "multiline": {"type": "boolean", "description": "Let . and patterns span lines"},
        "head_limit": {"type": "integer", "description": "Limit the number of output lines/files"},
    }}
    required = ("pattern",)

    def permission_key(self, runtime, args):
        value = arg(args, "path")
        return relative_key(runtime, resolve_path(runtime, value)) if value else "."

    async def prepare(self, ctx, args):
        base, prefix = _search_base(ctx, arg(args, "path"))
        pattern = str(arg(args, "pattern", "query", "regex", default=""))
        if not pattern:
            raise ToolError("Grep needs a non-empty pattern.")
        return Prepared(key=relative_key(ctx.runtime, base), outside=prefix == "\0outside",
                        title=tr("Поиск вне проекта", "Search outside the project"),
                        data={"base": base, "prefix": prefix, "pattern": pattern})

    async def run(self, ctx, args, prepared):
        return await asyncio.to_thread(self._search, ctx, args, prepared)

    def _search(self, ctx: ToolContext, args: Dict[str, Any], prepared: Prepared) -> ToolResult:
        pattern = prepared.data["pattern"]
        mode = str(args.get("output_mode") or "files_with_matches")
        if mode not in ("files_with_matches", "content", "count"):
            mode = "files_with_matches"
        flags = re.IGNORECASE if flag(args.get("-i")) or flag(args.get("case_insensitive")) else 0
        multiline = flag(args.get("multiline"))
        if multiline:
            flags |= re.DOTALL | re.MULTILINE
        literal_note = ""
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            try:
                regex = re.compile(re.escape(pattern), flags)
            except re.error:
                raise ToolError(f"Invalid regular expression: {exc}") from exc
            literal_note = (f"[note: /{pattern}/ is not a valid regular expression ({exc}); it was searched as plain "
                            "text. Escape special characters such as ( [ { . * + ? with a backslash for a regex.]\n")
        context = whole(args.get("-C") or args.get("context"), 0, "context")
        before = whole(args.get("-B") or context, 0, "-B")
        after = whole(args.get("-A") or context, 0, "-A")
        before, after = max(0, min(before, 20)), max(0, min(after, 20))
        limit = whole(args.get("head_limit"), MAX_GREP_LINES if mode == "content" else 200, "head_limit")
        limit = max(1, min(limit, 2000))
        file_glob = str(args.get("glob") or "")
        kind = str(args.get("type") or "").lower()
        if kind in TYPE_GLOBS:
            file_glob = TYPE_GLOBS[kind] if not file_glob else file_glob
        base, prefix = prepared.data["base"], prepared.data["prefix"]
        lines_out: List[str] = []
        files_hit: List[Tuple[float, str, int]] = []
        scanned = 0
        for display, absolute in _files_under(ctx, base, prefix):
            if file_glob and not path_matches(file_glob, display):
                continue
            if absolute.suffix.lower() in BINARY_SUFFIXES or absolute.suffix.lower() in IMAGE_SUFFIXES:
                continue
            try:
                if absolute.stat().st_size > 3_000_000:
                    continue
                raw = absolute.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            scanned += 1
            text = _decode_for_search(raw)
            if mode == "files_with_matches":
                found = regex.search(text) if multiline else any(regex.search(line) for line in text.splitlines())
                if found:
                    files_hit.append((absolute.stat().st_mtime, display, 1))
                continue
            if multiline:
                matches = list(regex.finditer(text))
                if not matches:
                    continue
                files_hit.append((absolute.stat().st_mtime, display, len(matches)))
                if mode == "content" and len(lines_out) < limit:
                    for match in matches[:20]:
                        line_no = text.count("\n", 0, match.start()) + 1
                        snippet = match.group(0)[:600]
                        lines_out.append(f"{display}:{line_no}: {snippet}")
                continue
            file_lines = text.splitlines()
            hits = [index for index, line in enumerate(file_lines) if regex.search(line)]
            if not hits:
                continue
            files_hit.append((absolute.stat().st_mtime, display, len(hits)))
            if mode == "content" and len(lines_out) < limit:
                shown: set = set()
                hit_set = set(hits)
                for index in hits:
                    low, high = max(0, index - before), min(len(file_lines), index + after + 1)
                    if shown and low > max(shown) + 1:
                        lines_out.append("--")
                    for number in range(low, high):
                        if number in shown:
                            continue
                        shown.add(number)
                        mark = ":" if number in hit_set else "-"
                        line = file_lines[number]
                        if len(line) > 400:
                            line = line[:400] + "…"
                        lines_out.append(f"{display}{mark}{number + 1}{mark}{line}")
                        if len(lines_out) >= limit:
                            break
                    if len(lines_out) >= limit:
                        break
        if not files_hit:
            return ToolResult(ok=True, output=f"{literal_note}No matches for /{pattern}/ ({scanned} files searched).",
                              summary=tr("Совпадений нет", "No matches"))
        total_matches = sum(row[2] for row in files_hit)
        if mode == "count":
            rows = [f"{display}:{count}" for _m, display, count in sorted(files_hit, key=lambda r: r[1])][:limit]
            body = "\n".join(rows)
        elif mode == "content":
            body = "\n".join(lines_out[:limit])
            if len(lines_out) >= limit:
                body += f"\n… [output limited to {limit} lines; narrow the search or raise head_limit]"
        else:
            ordered = [display for _m, display, _c in sorted(files_hit, key=lambda r: (-r[0], r[1]))]
            body = "\n".join(ordered[:limit])
            if len(ordered) > limit:
                body += f"\n… and {len(ordered) - limit} more files"
        summary = (tr(f"Найдено файлов: {len(files_hit)}", f"Found {len(files_hit)} files") if mode == "files_with_matches"
                   else tr(f"{total_matches} совп. в {len(files_hit)} файл.", f"{total_matches} matches in {len(files_hit)} files"))
        body = literal_note + body
        return ToolResult(ok=True, output=body, summary=summary, detail=body[:3000])


class LSTool(Tool):
    name = "LS"
    kind = "read"
    parallel = True
    short = "list a folder as a tree (2 levels) with file sizes"
    description = "Lists files and folders of a directory as a tree, two levels deep, skipping build and cache folders."
    parameters = {"type": "object", "properties": {
        "path": {"type": "string", "description": "Folder (default: project root)"},
        "ignore": {"type": "array", "items": {"type": "string"}, "description": "Glob patterns to skip"},
    }}

    def permission_key(self, runtime, args):
        value = arg(args, "path")
        return relative_key(runtime, resolve_path(runtime, value)) if value else "."

    async def prepare(self, ctx, args):
        value = arg(args, "path")
        base = resolve_path(ctx.runtime, value) if value else ctx.runtime.root
        if not base.is_dir():
            raise ToolError(f"Not a directory: {_rel(ctx, base)}")
        return Prepared(key=relative_key(ctx.runtime, base), outside=workspace_of(ctx.runtime, base) is None,
                        title=tr("Просмотр папки вне проекта", "List a folder outside the project"),
                        data={"base": base})

    async def run(self, ctx, args, prepared):
        base: Path = prepared.data["base"]
        ignore = [str(item) for item in items(args.get("ignore")) if str(item)]

        def work() -> ToolResult:
            lines: List[str] = [f"{_rel(ctx, base)}/"]
            count = [0]

            def size_text(value: int) -> str:
                for unit in ("B", "KB", "MB", "GB"):
                    if value < 1024:
                        return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
                    value /= 1024
                return f"{value:.1f}TB"

            def walk(folder: Path, depth: int, indent: str) -> None:
                try:
                    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                except OSError:
                    return
                for entry in entries:
                    if count[0] >= 400:
                        return
                    if any(fnmatch.fnmatch(entry.name, pattern) for pattern in ignore):
                        continue
                    if entry.is_dir():
                        if entry.name in SKIP_DIRS or entry.name == ".git":
                            lines.append(f"{indent}{entry.name}/ (skipped)")
                            count[0] += 1
                            continue
                        try:
                            inside = sum(1 for _ in entry.iterdir())
                        except OSError:
                            inside = 0
                        lines.append(f"{indent}{entry.name}/ ({inside})")
                        count[0] += 1
                        if depth < 2:
                            walk(entry, depth + 1, indent + "  ")
                    else:
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            size = 0
                        lines.append(f"{indent}{entry.name}  {size_text(size)}")
                        count[0] += 1

            walk(base, 1, "  ")
            if count[0] >= 400:
                lines.append("  … (listing truncated; list a subfolder)")
            return ToolResult(ok=True, output="\n".join(lines),
                              summary=tr(f"{count[0]} элементов", f"{count[0]} entries"))
        return await asyncio.to_thread(work)


# ---------------------------------------------------------------- code navigation

class OutlineTool(Tool):
    name = "Outline"
    kind = "read"
    parallel = True
    short = "declarations of a file (classes, functions) without its body"
    description = "Lists the declarations of a source file with line numbers — a cheap way to see its structure."
    parameters = {"type": "object", "properties": {"file_path": {"type": "string"}}}
    required = ("file_path",)

    def permission_key(self, runtime, args):
        return relative_key(runtime, resolve_path(runtime, arg(args, "file_path", "path")))

    async def prepare(self, ctx, args):
        path = resolve_path(ctx.runtime, arg(args, "file_path", "path"))
        return Prepared(key=relative_key(ctx.runtime, path), preview=str(path),
                        title=tr("Чтение структуры файла вне проекта", "Read declarations outside the project"),
                        outside=workspace_of(ctx.runtime, path) is None, data={"path": path})

    async def run(self, ctx, args, prepared):
        path = prepared.data["path"]
        if not path.is_file():
            raise ToolError(f"File does not exist: {path}")
        base = workspace_of(ctx.runtime, path) or path.parent
        try:
            data = await asyncio.to_thread(tool_outline, base, {"path": path.relative_to(base).as_posix()})
        except WorkError as exc:
            raise ToolError(str(exc)) from exc
        rows = [f"{row['line']:>5}  {row['text']}" for row in data.get("symbols") or []]
        return ToolResult(ok=True, output="\n".join(rows) or "(no declarations found)",
                          summary=tr(f"{len(rows)} объявлений", f"{len(rows)} declarations"))


class SymbolsTool(Tool):
    name = "Symbols"
    kind = "read"
    parallel = True
    short = "find a file or a declared symbol by name across the project"
    description = "Finds files and declarations (functions, classes) whose name contains the query."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
    required = ("query",)

    async def run(self, ctx, args, prepared):
        from ... import code_context
        query = str(arg(args, "query", "name", default=""))
        engine = code_context.engine_for("cli-" + ctx.session.id, ctx.runtime.root)
        data = await asyncio.to_thread(engine.search_symbols, query, 60)
        if data.get("error"):
            raise ToolError(str(data["error"]))
        rows = [f"{row['path']}:{row.get('line', 1)}  {row.get('kind', '')} {row.get('name', '')}"
                for row in data.get("symbols") or []]
        return ToolResult(ok=True, output="\n".join(rows) or f"No symbol matches {query!r}.",
                          summary=tr(f"Найдено: {len(rows)}", f"Found {len(rows)}"))


class ProjectInfoTool(Tool):
    name = "ProjectInfo"
    kind = "read"
    parallel = True
    short = "stack, manifests, npm scripts and the suggested test command"
    description = "Summarizes the project: languages, manifests, package scripts, suggested test commands, git."
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx, args, prepared):
        data = await asyncio.to_thread(code_tools.tool_project_info, ctx.runtime.root, {})
        return ToolResult(ok=True, output=json.dumps(data, ensure_ascii=False, indent=1),
                          summary=", ".join(data.get("stacks") or []) or tr("Проект", "Project"))


class GitTool(Tool):
    name = "Git"
    kind = "read"
    parallel = True
    short = "read-only git: {action: status|diff|log|branches|show, path?, staged?, limit?, ref?}"
    description = ("Read-only view of the repository: status, diff (optionally staged or for one path), "
                   "recent log, branches, or show a commit. Use Bash for commands that change the repo.")
    parameters = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "diff", "log", "branches", "show"]},
        "path": {"type": "string"}, "staged": {"type": "boolean"}, "limit": {"type": "integer"},
        "ref": {"type": "string"}}}

    def display(self, args):
        return str(args.get("action") or "status")

    async def run(self, ctx, args, prepared):
        action = str(args.get("action") or "status").lower()
        if action not in ("status", "diff", "log", "branches", "show"):
            raise ToolError("Git supports status, diff, log, branches and show.")
        try:
            data = await asyncio.to_thread(code_tools.tool_git, ctx.runtime.root, {**args, "action": action})
        except WorkError as exc:
            raise ToolError(str(exc)) from exc
        if action == "diff":
            text = data.get("diff") or "(no changes)"
            added, removed = _diff_stats(text)
            return ToolResult(ok=True, output=text, diff=text, summary=f"+{added} −{removed}")
        if action == "status":
            changes = data.get("changes") or []
            summary = tr(f"ветка {data.get('branch') or '?'}, изменений: {len(changes)}",
                         f"branch {data.get('branch') or '?'}, {len(changes)} changes")
            return ToolResult(ok=True, output=json.dumps(data, ensure_ascii=False, indent=1), summary=summary)
        return ToolResult(ok=True, output=json.dumps(data, ensure_ascii=False, indent=1)[:60000],
                          summary=action)


def register(registry) -> None:
    for tool in (ReadTool(), WriteTool(), EditTool(), MultiEditTool(), GlobTool(), GrepTool(), LSTool(),
                 OutlineTool(), SymbolsTool(), ProjectInfoTool(), GitTool()):
        registry.add(tool)
