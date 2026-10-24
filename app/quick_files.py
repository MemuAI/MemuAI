"""Round 30: Quick Access does file tasks itself — anywhere in the user's folders, not only over Explorer.

Before, the folder tools (:mod:`app.quick_explorer`) existed only while the window was opened over a File Explorer
window, strictly inside that folder. «Создай папку на рабочем столе» asked over a browser, or «перенеси это из
Загрузок» asked over another folder, had no tool at all — the model answered with a command to type. This module
adds the general file tools to every question that is about files:

* ``files_list {path, depth?, glob?}`` — entries of a folder;
* ``files_find {query, where?, glob?, content?}`` — find files / folders by name (and text) in the usual places;
* ``files_change {summary, ops}`` — the same change sets as the folder agent (create / rename / move / copy /
  delete → Recycle Bin), with absolute paths or places (``Desktop/…``); the base folder of the set is the deepest
  common folder of its paths, so «из Загрузок на рабочий стол» is one set;
* ``files_open {path, reveal?}`` — open a file / folder with its program, or show it selected in Explorer.

Paths: absolute, ``~/…``, a place (``Desktop``, ``Downloads``, ``Documents``, ``Pictures``, ``Music``, ``Videos``,
``Home`` — English or Russian names, the real known-folder paths on Windows / XDG on Linux), or ``./…`` = the
Explorer folder the window was opened over (its selection is in ``<folder_context>``). Changes are planned and
applied by :mod:`app.quick_explorer` (preview, «Применить», undo journal, Recycle Bin — :mod:`app.quick_trash`);
the access mode is the same setting (Ask / Full: in Full safe operations are done at once, deletes and
overwrites always wait for the user's click).

Never touched: system folders and drive roots (:func:`app.quick_apps.is_protected`), MemuAI's own program and data
folders (the agent cannot delete itself), ``AppData``, ``.ssh`` / ``.gnupg``, the Recycle Bin, links on the way.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import quick_apps
from .quick_apps import flag, tr

log = logging.getLogger("memuai.quick.files")

IS_WINDOWS = sys.platform == "win32"
FIND_LIMIT = 60
FIND_SECONDS = 4.0
LIST_LIMIT = 400
TOOL_ROUNDS = 8
EXECUTABLE_SUFFIXES = (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".com", ".lnk", ".reg", ".hta",
                       ".wsf", ".jar", ".sh", ".appimage", ".desktop",
                       # Round 34: other things Windows runs on a double click (shortcuts to a program, control panel
                       # items, script hosts, ClickOnce, Python)
                       ".url", ".pif", ".cpl", ".msc", ".msp", ".jse", ".vbe", ".wsh", ".psm1", ".scf", ".inf",
                       ".application", ".appref-ms", ".gadget", ".settingcontent-ms", ".py", ".pyw", ".pyz", ".psd1")

# ---------------------------------------------------------------- places

PLACES: Dict[str, Dict[str, Any]] = {
    "desktop": {"ru": "Рабочий стол", "en": "Desktop",
                "names": ("desktop", "рабочий стол", "рабочий_стол", "рабочийстол", "рабочем столе", "рабочего стола")},
    "downloads": {"ru": "Загрузки", "en": "Downloads", "names": ("downloads", "download", "загрузки", "загрузках", "загрузок")},
    "documents": {"ru": "Документы", "en": "Documents",
                  "names": ("documents", "docs", "документы", "документах", "мои документы")},
    "pictures": {"ru": "Изображения", "en": "Pictures",
                 "names": ("pictures", "images", "photos", "изображения", "картинки", "фото", "фотографии")},
    "music": {"ru": "Музыка", "en": "Music", "names": ("music", "музыка", "музыке")},
    "videos": {"ru": "Видео", "en": "Videos", "names": ("videos", "video", "видео")},
    "home": {"ru": "Папка пользователя", "en": "Home", "names": ("home", "домашняя папка", "папка пользователя", "профиль")},
}
_XDG_KEYS = {"desktop": "desktop", "downloads": "download", "documents": "documents", "pictures": "pictures",
             "music": "music", "videos": "videos"}
_FALLBACK_NAMES = {"desktop": ("Desktop", "Рабочий стол"), "downloads": ("Downloads", "Загрузки"),
                   "documents": ("Documents", "Документы"), "pictures": ("Pictures", "Изображения"),
                   "music": ("Music", "Музыка"), "videos": ("Videos", "Видео")}


class FilesError(Exception):
    def __init__(self, ru: str, en: str, code: str = "") -> None:
        super().__init__(ru)
        self.ru, self.en, self.code = ru, en, code

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def place_folder(key: str) -> str:
    """The real folder of a place ("" when this computer has none)."""
    if key == "home":
        return str(_home())
    if key == "desktop":
        return quick_apps.desktop_folder()
    if key == "downloads":
        return quick_apps.downloads_folder()
    if IS_WINDOWS:
        try:
            from . import quick_apps_win
            path = quick_apps_win.known_folder(key)
            if path and os.path.isdir(path):
                return path
        except Exception:
            pass
    elif key in _XDG_KEYS:
        path = quick_apps.xdg_user_dir(_XDG_KEYS[key])
        if path:
            return path
    if key == "documents":
        path = quick_apps.documents_folder()
        return path if path != str(_home()) else ""
    for name in _FALLBACK_NAMES.get(key, ()):
        candidate = _home() / name
        if candidate.is_dir():
            return str(candidate)
    return ""


def places(lang: str = "ru") -> List[Dict[str, str]]:
    out = []
    for key, meta in PLACES.items():
        folder = place_folder(key)
        if folder:
            out.append({"key": key, "name": meta["en"], "label": meta["en" if lang == "en" else "ru"], "path": folder})
    return out


def _place_of(word: str) -> str:
    low = re.sub(r"\s+", " ", word.strip().lower().replace("ё", "е"))
    for key, meta in PLACES.items():
        if low in meta["names"] or low == meta["en"].lower() or low == meta["ru"].lower():
            return key
    return ""


def label_for(path: Path, lang: str = "ru") -> str:
    """«Рабочий стол», «Загрузки/Отчёты», or the folder's own name."""
    best = ("", "")
    for key, meta in PLACES.items():
        if key == "home":
            continue
        folder = place_folder(key)
        if not folder:
            continue
        try:
            rel = path.relative_to(Path(folder))
        except ValueError:
            continue
        if not best[0] or len(folder) > len(best[1]):
            best = (meta["en" if lang == "en" else "ru"] + ("" if str(rel) in ("", ".") else "/" + rel.as_posix()), folder)
    if not best[0] and path == _home():
        return PLACES["home"]["en" if lang == "en" else "ru"]
    return best[0] or path.name or str(path)


# ---------------------------------------------------------------- what is off limits

def _forbidden_roots() -> List[Path]:
    rows: List[Path] = []
    try:
        from .config import DATA_DIR
        rows.append(Path(DATA_DIR))
    except Exception:
        pass
    try:
        from .pgdata import MEMU_DIR
        rows.append(Path(MEMU_DIR))
    except Exception:
        pass
    rows.append(Path(__file__).resolve().parent.parent)            # the program (source tree or the bundle)
    if getattr(sys, "frozen", False):
        rows.append(Path(sys.executable).resolve().parent)
    for env in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(env):
            rows.append(Path(os.environ[env]))
    home = _home()
    rows += [home / ".ssh", home / ".gnupg", home / "AppData"]
    return rows


def check_allowed(path: Path) -> None:
    """Refuse MemuAI's own folders, AppData, keys, the Recycle Bin (reading included)."""
    try:
        resolved = Path(os.path.realpath(path))
    except (OSError, ValueError):
        raise FilesError("Недопустимый путь", "Invalid path", "bad_path") from None
    if any(part.lower() in ("$recycle.bin", "system volume information") for part in resolved.parts):
        raise FilesError("Корзину и системные папки тома не трогаю", "The Recycle Bin and volume system folders are off limits",
                         "forbidden")
    for root in _forbidden_roots():
        try:
            real = Path(os.path.realpath(root))
        except (OSError, ValueError):
            continue
        if resolved == real or real in resolved.parents:
            raise FilesError(f"«{path}» — служебная папка (MemuAI, AppData, ключи): её не трогаю",
                             f"“{path}” is a service folder (MemuAI, AppData, keys): off limits", "forbidden")


def resolve(raw: Any, app_ctx: Optional[Dict[str, Any]] = None) -> Path:
    """A path the model named → an absolute, normalised path (not checked for existence)."""
    text = str(raw or "").strip().strip("\"'«»“”").strip()
    if not text:
        raise FilesError("Нужен путь", "A path is needed", "empty")
    if "\x00" in text or len(text) > 1000:
        raise FilesError("Недопустимый путь", "Invalid path", "bad_path")
    unix = text.replace("\\", "/")
    if any(part == ".." for part in unix.split("/")):
        raise FilesError(f"«{text}»: «..» в пути не поддерживается — назовите папку прямо",
                         f"“{text}”: “..” is not supported — name the folder directly", "bad_path")
    current = str((app_ctx or {}).get("folder") or "")
    if unix in (".", "./") or unix.startswith("./"):
        if not current:
            raise FilesError("Папка Проводника не открыта — укажите путь (например, Desktop/…)",
                             "No Explorer folder — give a path (e.g. Desktop/…)", "no_folder")
        base, rest = Path(current), unix[2:]
    elif unix == "~" or unix.startswith("~/"):
        base, rest = _home(), unix[2:]
    elif re.match(r"^[A-Za-z]:/", unix) or (not IS_WINDOWS and unix.startswith("/")):
        base, rest = Path(os.path.expandvars(text) if IS_WINDOWS else text), ""
    elif re.match(r"^[A-Za-z]:$", unix) or unix.startswith("//"):
        raise FilesError("Корень диска и сетевые пути не поддерживаются", "Drive roots and network paths are not supported",
                         "forbidden")
    else:
        if IS_WINDOWS and "%" in unix:
            expanded = os.path.expandvars(text).replace("\\", "/")
            if re.match(r"^[A-Za-z]:/", expanded):
                return resolve(expanded, app_ctx)
        head, _sep, tail = unix.partition("/")
        key = _place_of(head)
        if key:
            folder = place_folder(key)
            if not folder:
                raise FilesError(f"На этом компьютере нет папки «{PLACES[key]['ru']}»",
                                 f"This computer has no {PLACES[key]['en']} folder", "no_place")
            base, rest = Path(folder), tail
        elif current:
            base, rest = Path(current), unix
        else:
            raise FilesError(f"«{text}»: непонятно, где это — укажите полный путь или место (Desktop/…, Downloads/…)",
                             f"“{text}”: unclear where — give a full path or a place (Desktop/…, Downloads/…)", "unclear")
    path = Path(os.path.normpath(str(base / rest) if rest else str(base)))
    check_allowed(path)
    return path


# ---------------------------------------------------------------- tools

def _ctx(tool_ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    settings = (tool_ctx or {}).get("settings") or {}
    return quick_apps.get_context(settings.get("_quick_app_ctx") or "")


def _explorer():
    from . import quick_explorer
    return quick_explorer


def tool_list(app_ctx: Optional[Dict[str, Any]], args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    qe = _explorer()
    folder = resolve(args.get("path") or ".", app_ctx)
    if not folder.is_dir():
        raise FilesError(f"«{folder}» — не папка или её нет", f"“{folder}” is not a folder or does not exist", "not_dir")
    depth = max(1, min(3, int(args.get("depth") or 1)))
    pattern = str(args.get("glob") or "").strip().lower()
    hidden = bool(quick_apps.settings().get("hidden"))
    rows, truncated = [], False
    for path, is_dir, _level in qe.walk(folder, folder, depth=depth, hidden=hidden):
        rel = qe.rel_of(folder, path)
        if pattern and not is_dir and not fnmatch.fnmatch(path.name.lower(), pattern):
            continue
        row = {"path": rel + ("/" if is_dir else ""), "type": "dir" if is_dir else "file"}
        if not is_dir:
            row.update(qe._stat(path))
        rows.append(row)
        if len(rows) >= LIST_LIMIT:
            truncated = True
            break
    return {"folder": str(folder), "where": label_for(folder, lang), "entries": rows, "count": len(rows),
            "truncated": truncated}


def tool_find(app_ctx: Optional[Dict[str, Any]], args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    from .work_tools import read_text, WorkError
    qe = _explorer()
    query = str(args.get("query") or "").strip()[:200]
    pattern = str(args.get("glob") or "").strip().lower()
    if not query and not pattern:
        raise FilesError("Пустой запрос", "Empty query", "empty")
    where = args.get("where")
    roots: List[Path] = []
    for item in (where if isinstance(where, list) else ([where] if where else [])):
        roots.append(resolve(item, app_ctx))
    if not roots:
        if app_ctx and app_ctx.get("folder"):
            roots.append(Path(app_ctx["folder"]))
        for key in ("desktop", "downloads", "documents", "pictures", "music", "videos"):
            folder = place_folder(key)
            if folder:
                roots.append(Path(folder))
    seen_roots: List[Path] = []
    for root in sorted({r for r in roots if r.is_dir()}, key=lambda r: len(str(r))):
        if not any(root == r or r in root.parents for r in seen_roots):   # a place inside another one: walked once
            seen_roots.append(root)
    words = [w for w in re.split(r"\s+", query.lower()) if w]
    content = flag(args.get("content"))
    hidden = bool(quick_apps.settings().get("hidden"))
    deadline = time.monotonic() + FIND_SECONDS
    found, matches, timed_out = [], [], False
    for root in seen_roots:
        for path, is_dir, _level in qe.walk(root, root, depth=8, hidden=hidden, limit=60000):
            if time.monotonic() > deadline:
                timed_out = True
                break
            name = path.name.lower()
            if pattern and not fnmatch.fnmatch(name, pattern):
                continue
            if all(w in name for w in words):
                row = {"path": str(path), "type": "dir" if is_dir else "file"}
                if not is_dir:
                    row.update(qe._stat(path))
                found.append(row)
            elif content and words and not is_dir and qe._is_text_name(path.name):
                try:
                    if path.stat().st_size > 2_000_000:
                        continue
                    text, _ = read_text(path, limit=300_000)
                except (OSError, WorkError):
                    continue
                low = text.lower()
                if all(w in low for w in words):
                    matches.append({"path": str(path)})
            if len(found) >= FIND_LIMIT:
                break
        if timed_out or len(found) >= FIND_LIMIT:
            break
    return {"query": query, "searched": [str(r) for r in seen_roots], "found": found[:FIND_LIMIT],
            "content_matches": matches[:FIND_LIMIT], "truncated": timed_out or len(found) >= FIND_LIMIT}


_PATH_KEYS = ("path", "to")


def plan_paths(app_ctx: Optional[Dict[str, Any]], ops: Any) -> Tuple[Path, List[Dict[str, Any]]]:
    """Absolute / place paths of the operations → (the base folder, the operations relative to it)."""
    if not isinstance(ops, list) or not ops:
        raise FilesError("Нет операций", "No operations", "empty")
    resolved: List[Dict[str, Any]] = []
    parents: List[str] = []
    for raw in ops[:300]:
        if not isinstance(raw, dict):
            raise FilesError("Операция должна быть объектом", "An operation must be an object")
        row = dict(raw)
        for key in _PATH_KEYS:
            if row.get(key) not in (None, ""):
                path = resolve(row[key], app_ctx)
                row[key] = path
                parents.append(str(path.parent))
        if not isinstance(row.get("path"), Path):
            raise FilesError("У операции нет пути", "An operation has no path")
        resolved.append(row)
    try:
        base = Path(os.path.commonpath(parents))
    except ValueError:
        raise FilesError("Пути на разных дисках — переносите между дисками по одному в Проводнике",
                         "The paths are on different drives — move between drives in Explorer", "drives") from None
    while not base.is_dir() and base.parent != base:      # a folder the set creates itself
        base = base.parent
    check_allowed(base)
    if quick_apps.is_protected(base):
        raise FilesError(f"«{base}» — системная папка или корень диска: менять здесь нельзя",
                         f"“{base}” is a system folder or a drive root: no changes here", "read_only")
    out = []
    for row in resolved:
        for key in _PATH_KEYS:
            if isinstance(row.get(key), Path):
                rel = os.path.relpath(row[key], base)
                if rel == "." or rel.startswith(".."):
                    raise FilesError(f"«{row[key]}»: нельзя менять саму папку {base}",
                                     f"“{row[key]}”: cannot change the folder {base} itself", "bad_path")
                row[key] = Path(rel).as_posix()
        out.append(row)
    return base, out


NEW_FOLDER_RE = re.compile(r"^(?:новая папка|new folder)(?: \(\d+\))?$", re.I)


def _number_new_folders(base: Path, ops: List[Dict[str, Any]]) -> None:
    """«Создай папку» without a name: «Новая папка» that already exists becomes «Новая папка (2)», like Explorer."""
    taken = set()
    for row in ops:
        if str(row.get("op") or "").lower() not in ("mkdir", "folder"):
            continue
        rel = str(row.get("path") or "")
        head, _sep, name = rel.rpartition("/")
        if not NEW_FOLDER_RE.match(name):
            continue
        stem = re.sub(r" \(\d+\)$", "", name)
        candidate, index = name, 1
        while os.path.lexists(base / head / candidate) or (head, candidate.lower()) in taken:
            index += 1
            candidate = f"{stem} ({index})"
        taken.add((head, candidate.lower()))
        row["path"] = (head + "/" if head else "") + candidate


def tool_change(app_ctx: Optional[Dict[str, Any]], args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    qe = _explorer()
    base, ops = plan_paths(app_ctx, args.get("ops"))
    _number_new_folders(base, ops)
    ctx = quick_apps.register_context({"kind": "files", "app": "Files", "folder": str(base),
                                       "label": label_for(base, lang)}, current=False)
    try:
        return qe._change_tool(ctx, {"ops": ops, "summary": args.get("summary") or ""}, lang)
    except qe.FolderError as exc:
        raise FilesError(exc.ru, exc.en, exc.code) from None


def open_path(path: str, reveal: bool = False) -> bool:
    if IS_WINDOWS:
        from . import quick_apps_win
        return quick_apps_win.reveal(path) if reveal else quick_apps_win.open_path(path)
    if sys.platform.startswith("linux"):
        target = os.path.dirname(path) if reveal and not os.path.isdir(path) else path
        try:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False
    try:
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
        return True
    except OSError:
        return False


def tool_open(app_ctx: Optional[Dict[str, Any]], args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    path = resolve(args.get("path"), app_ctx)
    if not os.path.lexists(path):
        raise FilesError(f"«{path}» не найден", f"“{path}” not found", "missing")
    reveal = flag(args.get("reveal"))
    if not reveal and path.is_file() and path.suffix.lower() in EXECUTABLE_SUFFIXES:
        raise FilesError("Программы и скрипты отсюда не запускаю — откройте их сами или попросите показать в Проводнике",
                         "Programs and scripts are not launched from here — open them yourself or ask to show them in Explorer",
                         "executable")
    ok = open_path(str(path), reveal)
    if not ok:
        raise FilesError("Не получилось открыть", "Could not open it", "failed")
    return {"ok": True, "opened": str(path), "revealed": reveal,
            "note": "Done: it is open now. Say so in a few words."}


TOOL_DESCRIPTIONS = {
    "files_list": "{path, depth? 1-3, glob?} — list a folder anywhere in the user's folders (see <files>)",
    "files_find": "{query, where?, glob?, content?} — find files/folders by name words (content: also inside text "
                  "files) in the usual places, or in `where` (a path or a list)",
    "files_change": ("{summary, ops:[...]} — DO file changes: {op:\"mkdir\",path}, {op:\"write\",path,text} (new text "
                     "file), {op:\"move\",path,to} (rename/move), {op:\"copy\",path,to}, {op:\"delete\",path} (to the Recycle "
                     "Bin, the user confirms), {op:\"edit\",path,old_text,new_text}, {op:\"append\",path,text}. Paths: "
                     "absolute, ~/…, a place (Desktop/…, Downloads/…, Documents/…, Pictures/…, Music/…, Videos/…) or ./… "
                     "(the Explorer folder). Put all operations of one request into ONE call"),
    "files_open": "{path, reveal?} — open a file/folder with its program (reveal:true — show it selected in Explorer)",
}

_LABELS = {"files_list": ("Смотрю папку…", "Looking at the folder…"), "files_find": ("Ищу файлы…", "Finding files…"),
           "files_change": ("Выполняю…", "Doing it…"), "files_open": ("Открываю…", "Opening…")}

FILE_WORDS = re.compile(
    r"(папк|файл|рабоч\w*\s+стол|загрузк|документ|изображени|картинк|фотк|фотограф|музык|видео|корзин|ярлык|"
    r"переимен|рассортир|отсортир|упорядоч|разлож|\bfolder|\bfiles?\b|desktop|downloads?\b|documents\b|pictures\b|"
    r"\bmusic\b|\bvideos\b|recycle bin|\btrash\b|\brename|organi[sz]e|\bmkdir\b|[A-Za-z]:\\|~/|"
    r"\.(?:txt|docx?|xlsx?|pptx?|pdf|png|jpe?g|gif|zip|rar|7z|mp[34]|csv|md)\b)", re.I)


def wants_files(text: str) -> bool:
    return bool(FILE_WORDS.search(str(text or "")))


def files_block(app_ctx: Optional[Dict[str, Any]], lang: str = "ru") -> str:
    access = quick_apps.settings().get("access", "ask")
    rows = [f"{row['name']} = {row['path']}" for row in places(lang)]
    lines = ["The user's folders (places): " + "; ".join(rows) + ".",
             "File tasks (create, rename, move, copy, delete, open, find, organize) are YOURS to do with the files_* "
             "tools: call them, never answer with a command, a script or steps for the user to type, and never say "
             "a file was created / moved / renamed / deleted unless a tool result in THIS conversation says so. Unknown names: "
             "files_list / files_find first. A folder without a name → "
             + ("«Новая папка»" if lang != "en" else "“New folder”") + " (numbered automatically when taken).",
             ("Access: Full — safe changes are applied at once; deletes and overwrites wait for the user's click."
              if access == "full" else
              "Access: Ask — every change is previewed and runs after the user's «Применить»/“Apply” click.")
             + " Deleted items go to the Recycle Bin. After the tool: one short sentence about what was done or "
               "what waits for the click — the result card with «Undo» is under the answer.",
             "Off limits: system folders, drive roots, MemuAI's own and AppData folders.",
             # Round 35: the PC-wide index (app/fsindex_api.py) — files_find only walks the usual places
             "A file whose folder is unknown or outside the places (other drives, any folder): search_files finds it "
             "by name on the whole PC instantly — then files_open / files_change with the path it returned."]
    if app_ctx and app_ctx.get("folder"):
        lines.append('"this folder", "here", "these files" = the Explorer folder in <folder_context> (./… in files_* '
                     "paths, or the folder_* tools).")
    return "<files>\n" + "\n".join(lines) + "\n</files>"


def request_settings(text: str, app_ctx: Optional[Dict[str, Any]], lang: str = "ru") -> Dict[str, Any]:
    """The ``/api/quick/ask`` hook: turn the file tools on for a question about files (or over a folder)."""
    try:
        if not quick_apps.settings().get("files", True):
            return {}
    except Exception:
        return {}
    has_folder = bool(app_ctx and app_ctx.get("folder"))
    if not wants_files(text) and not has_folder:
        return {}
    return {"_quick_files": True, "_quick_files_block": files_block(app_ctx, lang), "_quick_tool_rounds": TOOL_ROUNDS}


def _enabled(settings: Dict[str, Any]) -> bool:
    return bool((settings or {}).get("_quick_files"))


def _tool(fn):
    async def handler(args: Dict[str, Any], tool_ctx: Dict[str, Any]) -> Dict[str, Any]:
        lang = (tool_ctx or {}).get("lang") or "ru"
        try:
            return await asyncio.to_thread(fn, _ctx(tool_ctx), args if isinstance(args, dict) else {}, lang)
        except FilesError as exc:
            return {"error": exc.text(lang), "code": exc.code}
        except Exception as exc:
            qe = _explorer()
            if isinstance(exc, qe.FolderError):
                return {"error": exc.text(lang), "code": exc.code}
            raise
    return handler


def _label(name: str):
    def label(args: Any, lang: str) -> str:
        ru, en = _LABELS[name]
        return en if lang == "en" else ru
    return label


def register_tools() -> None:
    from .quick_ask_api import register_tool
    for name, fn in (("files_list", tool_list), ("files_find", tool_find), ("files_change", tool_change),
                     ("files_open", tool_open)):
        try:
            register_tool(name, TOOL_DESCRIPTIONS[name], _tool(fn), label=_label(name), enabled=_enabled)
        except ValueError:
            pass


# ---------------------------------------------------------------- routes

router = APIRouter()


@router.get("/api/quick/files/places")
async def files_places(request: Request):
    lang = "en" if request.query_params.get("lang") == "en" else "ru"
    return {"places": await asyncio.to_thread(places, lang)}


@router.post("/api/quick/files/reveal")
async def files_reveal(request: Request):
    """«Показать» on a finished change card: the first created / moved / copied item of the set, in Explorer."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    lang = "en" if body.get("lang") == "en" else "ru"
    qe = _explorer()
    change = qe.get_set(str(body.get("set") or ""))
    if change is None:
        return JSONResponse({"error": tr(lang, "Набор изменений устарел", "The change set expired")}, 404)
    root = Path(change["root"])
    target = root
    for op in change["ops"]:
        if op.get("status") == "applied" and op.get("op") != "delete":
            candidate = root / (op.get("to") or op.get("path") or "")
            if os.path.lexists(candidate):
                target = candidate
                break
    ok = await asyncio.to_thread(open_path, str(target), target != root)
    return {"ok": ok, "path": str(target)}


def register() -> None:
    register_tools()
    from .quick_ask_api import register_router
    register_router(router)
