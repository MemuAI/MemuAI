"""Инструменты Work: модель сама решает, что прочитать и что изменить.

Раньше в промпт заранее складывали содержимое файлов, которые выбрала
эвристика: модель получала гору лишнего кода и всё равно не видела нужного.
Здесь она получает только структуру проекта, а файлы, поиск и правки
запрашивает вызовами — как в Claude Code и Codex.

Каждый путь проверяется относительно корня проекта: выйти за него нельзя.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from .path_safety import is_link
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".cache", ".next", ".nuxt",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", ".gradle",
    "node_modules", "vendor", "venv", ".venv", "env", "dist", "build",
    "coverage", "target", "out", ".terraform", ".tox", "bin", "obj",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".7z", ".rar", ".tar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".class", ".o",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg", ".webm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".psd", ".sqlite", ".db",
}

ENTRY_NAMES = (
    "readme.md", "readme", "package.json", "pyproject.toml", "requirements.txt",
    "cargo.toml", "go.mod", "composer.json", "gemfile", "pom.xml",
    "build.gradle", "dockerfile", "makefile", "index.html", "tsconfig.json",
)

MAX_TREE_ENTRIES = 4000
MAX_READ_BYTES = 400_000
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_MATCHES = 200

_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?"
    r"(?P<kind>class|def|function|interface|type|struct|enum|impl|fn|const|let|var|public|private|protected)"
    r"\s+(?P<name>[A-Za-z_$][\w$]*)",
)

class WorkError(Exception):
    """Ошибка инструмента, которую можно показать модели как результат."""

def resolve_inside(root: Path, rel: str) -> Path:
    """Путь внутри проекта. Любая попытка выйти наружу — ошибка."""
    text = str(rel or "").strip().replace("\\", "/").lstrip("/")
    if not text or text == ".":
        return root
    target = (root / text).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkError(f"Путь «{rel}» находится вне Work-проекта") from exc
    return target

def entry_path(root: Path, rel: str) -> Path:
    """The entry itself, not what a link at it points to: the parent is resolved, the last name is kept.

    ``work_delete`` / ``work_move`` of a symlink or junction inside the project used to act on its
    target — deleting the link «lib → src» removed the whole ``src`` folder."""
    text = PurePosixPath(str(rel or "").strip().replace("\\", "/").lstrip("/"))
    if text.name in ("", ".", ".."):
        return resolve_inside(root, str(rel))
    return resolve_inside(root, str(text.parent)) / text.name

def _remove_link(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        os.rmdir(path)

_REPEATS = {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}

def _nested_repeat(items: Any, maxrepeat: int) -> bool:
    """An unbounded repeat whose body is another unbounded repeat plus only optional parts —
    ``(a+)+``, ``(\\w*)*``, ``(\\w+\\s?)+`` — backtracks exponentially on a line that almost matches."""
    for op, av in items:
        name = str(op)
        if name in _REPEATS:
            _low, high, body = av
            if high == maxrepeat:
                inner = list(body)
                while len(inner) == 1 and str(inner[0][0]) == "SUBPATTERN":
                    inner = list(inner[0][1][-1])
                loops = [row for row in inner if str(row[0]) in _REPEATS and row[1][1] == maxrepeat]
                if loops and all(str(row[0]) in _REPEATS and row[1][0] == 0 for row in inner if row not in loops):
                    return True
            if _nested_repeat(body, maxrepeat):
                return True
        elif name == "SUBPATTERN":
            if _nested_repeat(av[-1], maxrepeat):
                return True
        elif name == "BRANCH":
            if any(_nested_repeat(branch, maxrepeat) for branch in av[1]):
                return True
        elif name == "ATOMIC_GROUP":
            if _nested_repeat(av, maxrepeat):
                return True
        elif name in ("ASSERT", "ASSERT_NOT"):
            if _nested_repeat(av[1], maxrepeat):
                return True
    return False

def _risky_regex(query: str, flags: int) -> bool:
    try:
        from re import _parser as sre
    except ImportError:
        import sre_parse as sre      # type: ignore[no-redef]
    try:
        return _nested_repeat(sre.parse(query, flags), sre.MAXREPEAT)
    except Exception:
        return False

def relative(root: Path, target: Path) -> str:
    try:
        return str(target.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(target)

def _is_text(path: Path) -> bool:
    return path.suffix.lower() not in BINARY_SUFFIXES

def _decode(raw: bytes, clipped: bool) -> Tuple[str, str]:
    """Текст и его кодировка. UTF-16 — только с BOM: без него почти любые
    байты «успешно» декодируются как UTF-16 и превращаются в иероглифы."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as exc:

        if clipped and exc.start >= len(raw) - 3:
            try:
                return raw[:exc.start].decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                pass
    try:
        return raw.decode("cp1251"), "cp1251"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"

def read_text_meta(path: Path, limit: int = MAX_READ_BYTES) -> Tuple[str, bool, str]:
    """(текст, обрезан ли, кодировка) — для правок, сохраняющих кодировку файла."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise WorkError(f"Не удалось прочитать файл: {exc}") from exc
    if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise WorkError("Файл двоичный, текст прочитать нельзя")
    truncated = len(raw) > limit
    text, encoding = _decode(raw[:limit], truncated)
    return text, truncated, encoding

def read_text(path: Path, limit: int = MAX_READ_BYTES) -> Tuple[str, bool]:
    """Текст файла с обрезкой. Второе значение — был ли файл длиннее лимита."""
    text, truncated, _encoding = read_text_meta(path, limit)
    return text, truncated

def _eol(text: str) -> str:
    return "\r\n" if text.count("\r\n") > text.count("\n") / 2 else "\n"

def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")

def _with_eol(text: str, eol: str) -> str:
    return text.replace("\n", "\r\n") if eol == "\r\n" else text

def iter_files(root: Path, *, limit: int = MAX_TREE_ENTRIES) -> Iterable[Path]:
    """Обход проекта без служебных каталогов, с жёстким пределом."""
    root = root.resolve()
    seen = 0
    stack: List[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            continue
        for entry in entries:
            try:
                entry.resolve().relative_to(root)
                if is_link(entry):
                    continue
            except (OSError, ValueError, RuntimeError):
                continue
            if entry.is_dir():
                if entry.name.lower() in SKIP_DIRS or entry.name.startswith(".") and entry.name != ".github":
                    continue
                stack.append(entry)
                continue
            seen += 1
            if seen > limit:
                return
            yield entry

def tool_tree(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Структура проекта: пути, размеры, счётчики. Содержимого здесь нет."""
    prefix = str(args.get("path") or "").strip().replace("\\", "/").strip("/")
    base = resolve_inside(root, prefix) if prefix else root
    if not base.is_dir():
        raise WorkError(f"«{prefix or '.'}» — не каталог")
    pattern = str(args.get("glob") or "").strip()
    limit = max(1, min(MAX_TREE_ENTRIES, int(args.get("limit") or 1200)))
    rows: List[Dict[str, Any]] = []
    truncated = False
    for path in iter_files(base, limit=MAX_TREE_ENTRIES):
        rel = relative(root, path)
        if pattern and not fnmatch.fnmatch(rel, pattern):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append({"path": rel, "size": size, "text": _is_text(path)})
        if len(rows) >= limit:
            truncated = True
            break
    rows.sort(key=lambda item: item["path"])
    return {"root": str(root), "files": rows, "count": len(rows),
            "truncated": truncated,
            "entry_points": [row["path"] for row in rows
                             if Path(row["path"]).name.lower() in ENTRY_NAMES][:12]}

def tool_read(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Файл целиком или диапазон строк — `start_line` и `end_line` (1-based)."""
    target = resolve_inside(root, str(args.get("path") or ""))
    if not target.is_file():
        raise WorkError(f"Файл «{args.get('path')}» не найден")
    text, truncated = read_text(target)
    lines = text.splitlines()
    start = args.get("start_line")
    end = args.get("end_line")
    if start is not None or end is not None:
        first = max(1, int(start or 1))
        last = min(len(lines), int(end or first + 200))
        window = lines[first - 1:last]
        numbered = "\n".join(f"{first + offset}: {line}" for offset, line in enumerate(window))
        return {"path": relative(root, target), "text": numbered,
                "start_line": first, "end_line": last, "total_lines": len(lines),
                "truncated": truncated}
    return {"path": relative(root, target), "text": text,
            "total_lines": len(lines), "truncated": truncated}

def tool_search(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Поиск по содержимому проекта. Основной способ «осмотреться»."""
    query = str(args.get("query") or "").strip()
    if not query:
        raise WorkError("Нужен непустой query")
    pattern = str(args.get("glob") or "").strip()
    limit = max(1, min(MAX_MATCHES, int(args.get("limit") or 60)))
    context = max(0, min(6, int(args.get("context") or 1)))
    flags = 0 if args.get("case_sensitive") else re.IGNORECASE
    try:
        needle = re.compile(query if args.get("regex") else re.escape(query), flags)
    except re.error as exc:
        raise WorkError(f"Некорректное регулярное выражение: {exc}") from exc
    if args.get("regex") and _risky_regex(query, flags):

        raise WorkError("Регулярное выражение с вложенными повторами вида (a+)+ может зависнуть; "
                        "упростите его (например, (a+)+ → a+). "
                        "Nested repeats like (a+)+ can hang; simplify the pattern.")
    matches: List[Dict[str, Any]] = []
    scanned = 0
    for path in iter_files(root):
        rel = relative(root, path)
        if pattern and not fnmatch.fnmatch(rel, pattern):
            continue
        if not _is_text(path):
            continue
        try:
            text, _ = read_text(path, limit=200_000)
        except WorkError:
            continue
        scanned += 1
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not needle.search(line):
                continue
            low = max(0, index - context)
            high = min(len(lines), index + context + 1)
            matches.append({
                "path": rel, "line": index + 1,
                "text": "\n".join(f"{low + offset + 1}: {value}"
                                  for offset, value in enumerate(lines[low:high])),
            })
            if len(matches) >= limit:
                return {"query": query, "matches": matches, "files_scanned": scanned,
                        "truncated": True}
    return {"query": query, "matches": matches, "files_scanned": scanned, "truncated": False}

def tool_outline(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Объявления файла без его содержимого — дешёвый способ понять структуру."""
    target = resolve_inside(root, str(args.get("path") or ""))
    if not target.is_file():
        raise WorkError(f"Файл «{args.get('path')}» не найден")
    text, _ = read_text(target)
    symbols = []
    for index, line in enumerate(text.splitlines(), 1):
        match = _SYMBOL_RE.match(line)
        if match:
            symbols.append({"line": index, "kind": match.group("kind"),
                            "name": match.group("name"), "text": line.strip()[:200]})
        if len(symbols) >= 400:
            break
    return {"path": relative(root, target), "symbols": symbols,
            "total_lines": len(text.splitlines())}

def attach_review(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Tell the model when a diff deletes more than it adds, before it stops."""
    diff = str(payload.get("diff") or "")
    removed = added = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    if removed >= 5 and removed > added + 2:
        payload = dict(payload)
        payload["review"] = (
            f"Проверьте diff: удалено {removed} строк, добавлено {added}. "
            "Если строки пропали случайно, верните их через work_patch до завершения. "
            f"Review the diff: {removed} lines removed, {added} added."
        )
    return payload

def destructive_shrink(before: str, after: str) -> str:
    """A full rewrite that throws away most of an existing file is rejected."""
    if not str(before or "").strip():
        return ""
    old = str(before).splitlines()
    new = str(after).splitlines()
    if len(old) < 8:
        return ""
    dropped = len(old) - len(new)
    if dropped < 12 or len(new) >= int(len(old) * 0.75):
        return ""
    return ("Полная перезапись удалила бы большую часть файла "
            f"({len(old)} → {len(new)} строк). Измени только нужные строки через "
            "work_patch или work_replace_lines. "
            "Refused: a full rewrite would drop most of the file. Use a patch.")

def tool_write(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Создаёт или заменяет файл целиком. Возвращает diff для показа человеку."""
    target = resolve_inside(root, str(args.get("path") or ""))
    if target == root:
        raise WorkError("Корень проекта нельзя заменить файлом")
    text = args.get("text")
    if not isinstance(text, str):
        raise WorkError("Поле text должно быть строкой")
    if len(text.encode("utf-8")) > MAX_WRITE_BYTES:
        raise WorkError("Файл превышает лимит 5 МБ")
    before, encoding, eol = "", "utf-8", "\n"
    existed = target.is_file()
    if existed:
        try:
            original, _, encoding = read_text_meta(target)
            before, eol = _lf(original), _eol(original)
        except WorkError:
            before = ""
    reason = "" if args.get("allow_shrink") else destructive_shrink(before, _lf(text))
    if reason:
        raise WorkError(reason)

    _atomic_write(target, _with_eol(_lf(text), eol) if existed and "\r" not in text else text, encoding)
    return attach_review({"path": relative(root, target), "created": not existed,
            "bytes": len(text.encode("utf-8")),
            "diff": unified_diff(before, text, relative(root, target))})

def tool_patch(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Точечная правка: заменяет найденный фрагмент, не переписывая весь файл.

    Так модель меняет три строки в большом файле, не пересылая его целиком —
    меньше токенов и нет риска потерять остальное содержимое.
    """
    target = resolve_inside(root, str(args.get("path") or ""))
    if not target.is_file():
        raise WorkError(f"Файл «{args.get('path')}» не найден")
    old = args.get("old_text")
    new = args.get("new_text")
    if not isinstance(old, str) or not isinstance(new, str):
        raise WorkError("Нужны строки old_text и new_text")
    if not old:
        raise WorkError("old_text не может быть пустым")
    original, truncated, encoding = read_text_meta(target)
    if truncated:
        raise WorkError("Файл слишком велик для точечной правки; используйте work_write")

    eol = _eol(original)
    before, old, new = _lf(original), _lf(old), _lf(new)

    raw_all = args.get("replace_all")
    replace_all = raw_all is True or str(raw_all).strip().lower() in ("true", "1", "yes", "да")
    try:
        from .cli.tools.base import ToolError
        from .cli.tools.files import apply_edit
    except Exception:
        occurrences = before.count(old)
        if occurrences == 0:
            raise WorkError("old_text не найден в файле — прочитайте файл заново")
        if occurrences > 1 and not replace_all:
            raise WorkError(f"old_text встречается {occurrences} раз; уточните фрагмент "
                            "или передайте replace_all: true")
        after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
        replacements, fuzzy = (occurrences if replace_all else 1), False
    else:
        try:
            after, replacements, fuzzy = apply_edit(before, old, new, replace_all)
        except ToolError as exc:
            raise WorkError(f"old_text: {exc} Прочитайте файл заново (work_read).") from exc
    _atomic_write(target, _with_eol(after, eol), encoding)
    result = {"path": relative(root, target), "replacements": replacements,
              "diff": unified_diff(before, after, relative(root, target))}
    if fuzzy:
        result["note"] = "matched ignoring whitespace/quote differences"
    return attach_review(result)

def tool_insert(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Вставляет текст перед указанной строкой (1-based).

    Для длинного документа это дешевле, чем `work_write`: пересылается только
    новый фрагмент, остальное содержимое не трогается вовсе.
    """
    target = resolve_inside(root, str(args.get("path") or ""))
    if not target.is_file():
        raise WorkError(f"Файл «{args.get('path')}» не найден")
    text = args.get("text")
    if not isinstance(text, str):
        raise WorkError("Поле text должно быть строкой")
    original, truncated, encoding = read_text_meta(target)
    if truncated:
        raise WorkError("Файл слишком велик для вставки; используйте work_write")
    eol, before, text = _eol(original), _lf(original), _lf(text)
    lines = before.splitlines(keepends=True)
    position = args.get("line")
    index = len(lines) if position is None else max(0, min(len(lines), int(position) - 1))
    block = text if text.endswith("\n") else text + "\n"
    after = "".join(lines[:index]) + block + "".join(lines[index:])
    _atomic_write(target, _with_eol(after, eol), encoding)
    return {"path": relative(root, target), "line": index + 1,
            "diff": unified_diff(before, after, relative(root, target))}

def tool_replace_lines(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Заменяет диапазон строк. Удобно, когда фрагмент повторяется в файле."""
    target = resolve_inside(root, str(args.get("path") or ""))
    if not target.is_file():
        raise WorkError(f"Файл «{args.get('path')}» не найден")
    text = args.get("text")
    if not isinstance(text, str):
        raise WorkError("Поле text должно быть строкой")
    original, truncated, encoding = read_text_meta(target)
    if truncated:
        raise WorkError("Файл слишком велик для правки по строкам")
    eol, before, text = _eol(original), _lf(original), _lf(text)
    lines = before.splitlines(keepends=True)
    try:
        start = max(1, int(args.get("start_line")))
        end = max(start, int(args.get("end_line", start)))
    except (TypeError, ValueError) as exc:
        raise WorkError("Нужны числовые start_line и end_line") from exc
    if start > len(lines):
        raise WorkError(f"В файле только {len(lines)} строк")
    block = "" if text == "" else (text if text.endswith("\n") else text + "\n")
    after = "".join(lines[:start - 1]) + block + "".join(lines[min(end, len(lines)):])
    _atomic_write(target, _with_eol(after, eol), encoding)
    return attach_review({"path": relative(root, target), "start_line": start, "end_line": end,
            "diff": unified_diff(before, after, relative(root, target))})

def tool_append(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Дописывает текст в конец файла, создавая его при необходимости."""
    target = resolve_inside(root, str(args.get("path") or ""))
    text = args.get("text")
    if not isinstance(text, str):
        raise WorkError("Поле text должно быть строкой")
    before, eol, encoding = "", "\n", "utf-8"
    text = _lf(text)
    if target.is_file():
        original, truncated, encoding = read_text_meta(target)
        eol, before = _eol(original), _lf(original)
        if truncated:

            with target.open("a", encoding="utf-8" if encoding == "utf-8-sig" else encoding,
                             newline="") as handle:
                handle.write(_with_eol(text if text.endswith("\n") else text + "\n", eol))
            return {"path": relative(root, target), "appended": len(text), "diff": ""}
    after = before + ("" if not before or before.endswith("\n") else "\n") + \
        (text if text.endswith("\n") else text + "\n")
    _atomic_write(target, _with_eol(after, eol), encoding)
    return {"path": relative(root, target), "appended": len(text),
            "diff": unified_diff(before, after, relative(root, target))}

def tool_diff(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    """Показывает, чем текст отличается от файла, ничего не записывая."""
    target = resolve_inside(root, str(args.get("path") or ""))
    text = args.get("text")
    if not isinstance(text, str):
        raise WorkError("Поле text должно быть строкой")
    before = ""
    if target.is_file():
        before, _ = read_text(target)
    return {"path": relative(root, target), "exists": target.is_file(),
            "diff": unified_diff(before, text, relative(root, target))}

def tool_delete(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    entry = entry_path(root, str(args.get("path") or ""))
    if entry != root and is_link(entry):
        _remove_link(entry)
        return {"path": relative(root, entry), "deleted": True, "link": True}
    target = resolve_inside(root, str(args.get("path") or ""))
    if target == root:
        raise WorkError("Корень проекта удалять нельзя")
    if not target.exists():
        raise WorkError(f"«{args.get('path')}» не существует")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"path": relative(root, target), "deleted": True}

def tool_move(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    source = entry_path(root, str(args.get("path") or ""))
    if not is_link(source):
        source = resolve_inside(root, str(args.get("path") or ""))
    destination = entry_path(root, str(args.get("to") or ""))
    if source == root or destination == root:
        raise WorkError("Корень проекта перемещать нельзя")
    if not source.exists() and not is_link(source):
        raise WorkError(f"«{args.get('path')}» не существует")
    if destination.exists() or is_link(destination):
        try:
            same = os.path.samefile(source, destination)
        except OSError:
            same = False
        if not same:
            raise WorkError(f"«{args.get('to')}» уже существует; удалите его сначала или выберите другое имя. "
                            "The destination already exists.")
    if source.is_dir() and not is_link(source):
        try:
            destination.relative_to(source)
        except ValueError:
            pass
        else:
            raise WorkError("Папку нельзя переместить внутрь неё самой / A folder cannot move into itself")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    return {"from": relative(root, source), "to": relative(root, destination)}

def tool_mkdir(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    target = resolve_inside(root, str(args.get("path") or ""))
    if target.exists() and not target.is_dir():
        raise WorkError(f"«{args.get('path')}» — уже файл, а не папка / already a file")
    target.mkdir(parents=True, exist_ok=True)
    return {"path": relative(root, target), "created": True}

def _atomic_write(target: Path, text: str, encoding: str = "utf-8") -> None:
    """Пишет файл, не меняя переводы строк.

    `write_text` на Windows превращает каждый \\n в \\r\\n. Для файла, который
    уже использует \\n, это означало испорченные концы строк после каждой
    правки, поэтому перевод отключён явно.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")

    codec = encoding if encoding in ("utf-8", "utf-8-sig", "utf-16", "cp1251", "latin-1") else "utf-8"
    try:
        with temporary.open("w", encoding=codec, newline="", errors="strict") as handle:
            handle.write(text)
    except UnicodeEncodeError:

        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    try:
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

def unified_diff(before: str, after: str, path: str, *, limit: int = 400) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3))
    if len(lines) > limit:
        lines = lines[:limit] + [f"… ещё {len(lines) - limit} строк"]
    return "\n".join(lines)

READ_TOOLS: Dict[str, Callable[[Path, Dict[str, Any]], Dict[str, Any]]] = {
    "work_tree": tool_tree,
    "work_read": tool_read,
    "work_search": tool_search,
    "work_outline": tool_outline,
    "work_diff": tool_diff,
}
WRITE_TOOLS: Dict[str, Callable[[Path, Dict[str, Any]], Dict[str, Any]]] = {
    "work_write": tool_write,
    "work_patch": tool_patch,
    "work_insert": tool_insert,
    "work_replace_lines": tool_replace_lines,
    "work_append": tool_append,
    "work_delete": tool_delete,
    "work_move": tool_move,
    "work_mkdir": tool_mkdir,
}
ALL_TOOLS = {**READ_TOOLS, **WRITE_TOOLS}

_WRITE_LOCK = threading.RLock()

def run(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = ALL_TOOLS.get(name)
    if handler is None:
        raise WorkError(f"Неизвестный инструмент «{name}»")
    started = time.time()
    try:
        if name in WRITE_TOOLS:
            with _WRITE_LOCK:
                result = handler(root, args or {})
        else:
            result = handler(root, args or {})
    except (OSError, ValueError, TypeError) as exc:

        raise WorkError(f"{type(exc).__name__}: {exc}") from exc
    result["tool"] = name
    result["ms"] = int((time.time() - started) * 1000)
    return result

def project_overview(root: Path, *, max_files: int = 1200) -> Dict[str, Any]:
    """Стартовый контекст Work: только структура и короткая справка о проекте.

    Содержимое файлов сюда не попадает — модель запрашивает его инструментами.
    """
    tree = tool_tree(root, {"limit": max_files})
    languages: Dict[str, int] = {}
    total_bytes = 0
    for row in tree["files"]:
        total_bytes += int(row.get("size") or 0)
        suffix = Path(row["path"]).suffix.lower()
        if suffix:
            languages[suffix] = languages.get(suffix, 0) + 1
    top = sorted(languages.items(), key=lambda kv: -kv[1])[:8]
    return {
        "root_name": root.name,
        "files": [row["path"] for row in tree["files"]],
        "count": tree["count"],
        "truncated": tree["truncated"],
        "entry_points": tree["entry_points"],
        "total_bytes": total_bytes,
        "languages": [{"suffix": suffix, "files": count} for suffix, count in top],
    }
