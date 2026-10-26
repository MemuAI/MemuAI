"""Round 29: the program Quick Access was opened over — Explorer, the desktop, editors, Office, terminals.

Alt+Space over a File Explorer window gives the window a *folder context*: the folder that tab shows and the
items selected in it (read through ``Shell.Application`` — :mod:`app.quick_apps_win`). Over the desktop the
context is the Desktop folder; over VS Code / Notepad++ / Sublime / Notepad / JetBrains IDEs — the active
file (from the window title, the process command line and VS Code's own list of opened folders); over
Word / Excel / PowerPoint — the open document (read only, on demand, through COM); over a terminal — its
working folder when it can be known.

This module is the pure part (no Win32 at import time): classification of a window, title parsing, the
context registry and the settings. :mod:`app.quick_explorer` turns a context with a folder into a Work-like
agent STRICTLY scoped to that folder.

Security model: a folder root is never taken from the page. It is captured natively at the hotkey (or
registered by tests) and kept here under an opaque context id; requests only name the id. The root is
re-resolved and must still be a directory; system folders (Windows, Program Files, drive roots…) are
read-only.

Storage: ``memuai/quick_apps.json`` — ``settings`` + ``presets`` (outside ``preferences.json``: the main
window saves whole snapshots of that file).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote, urlparse

log = logging.getLogger("memuai.quick.apps")

IS_WINDOWS = sys.platform == "win32"

ACCESS_MODES = ("ask", "full")
PRESETS_LIMIT = 40
DEFAULT_SETTINGS: Dict[str, Any] = {
    "explorer": True,
    "apps": True,
    "office": True,
    "access": "ask",
    "hidden": False,
    "trash": True,
    "files": True,

    "currency": True,
    "color": True,
    "ocr": True,
    "error": True,
    "notes": True,
    "downloads": True,
    "presets": True,
    "notes_dir": "",
    "currency_to": "",
}

def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def flag(value: Any) -> bool:
    """Round 34: a tool flag from a model — ``"false"`` (a string) was read as ``True`` by ``bool()``."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return value is True or (isinstance(value, (int, float)) and value == 1)

def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "on", "yes"):
            return True
        if low in ("false", "0", "off", "no"):
            return False
    return default

def normalize_settings(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = src.get(key, default)
        if isinstance(default, bool):
            out[key] = _bool(value, default)
        elif key == "access":
            out[key] = value if value in ACCESS_MODES else "ask"
        elif key == "currency_to":
            code = re.sub(r"[^A-Za-z]", "", str(value or "")).upper()[:3]
            out[key] = code if len(code) == 3 else ""
        elif key == "notes_dir":
            text = str(value or "").strip()[:400]
            out[key] = text if not text or os.path.isabs(text) else ""
        else:
            out[key] = value
    return out

def _app_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"[^\w .+-]", "", text)[:60].strip()
    if text and "." not in text:
        text += ".exe"
    return text

def normalize_presets(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        name = re.sub(r"\s+", " ", str(row.get("name") or "")).strip()[:40]
        prompt = str(row.get("prompt") or "").strip()[:2000]
        apps = row.get("apps")
        if isinstance(apps, str):
            apps = re.split(r"[,;\s]+", apps)
        apps = [key for key in (_app_key(item) for item in (apps if isinstance(apps, list) else [])) if key][:12]
        if not name or not prompt:
            continue
        pid = str(row.get("id") or "")
        if not re.fullmatch(r"p_[a-z0-9]{4,16}", pid) or pid in seen:
            pid = "p_" + uuid.uuid4().hex[:10]
        seen.add(pid)
        out.append({"id": pid, "name": name, "prompt": prompt, "apps": apps,
                    "context": _bool(row.get("context"), True)})
        if len(out) >= PRESETS_LIMIT:
            break
    return out

def default_presets(lang: str = "ru") -> List[Dict[str, Any]]:
    rows = [
        (["outlook.exe", "olk.exe", "thunderbird.exe"], tr(lang, "Ответ на письмо", "Reply to the email"),
         tr(lang, "Напиши вежливый ответ на это письмо: коротко, по делу, в тоне отправителя.",
            "Write a polite reply to this email: short, to the point, in the sender's tone.")),
        (["excel.exe"], tr(lang, "Помощь с формулой", "Formula help"),
         tr(lang, "Помоги с формулой Excel для этой таблицы. Сначала формула в блоке кода, потом одно предложение пояснения.",
            "Help with an Excel formula for this sheet. The formula first in a code block, then one sentence of explanation.")),
        (["code.exe", "cursor.exe", "notepad++.exe", "sublime_text.exe"], tr(lang, "Найти ошибки", "Find bugs"),
         tr(lang, "Найди ошибки и слабые места в этом файле. Список: строка — проблема — исправление.",
            "Find bugs and weak spots in this file. A list: line — problem — fix.")),
        (["winword.exe"], tr(lang, "Улучшить текст", "Improve the text"),
         tr(lang, "Предложи, как улучшить этот документ: структура, ясность, ошибки. Коротким списком.",
            "Suggest how to improve this document: structure, clarity, mistakes. A short list.")),
    ]
    return normalize_presets([{"apps": apps, "name": name, "prompt": prompt} for apps, name, prompt in rows])

_LOCK = threading.RLock()

def _memu_dir() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)

def _config_file() -> Path:
    return _memu_dir() / "quick_apps.json"

def load_config(lang: str = "ru") -> Dict[str, Any]:
    with _LOCK:
        data = None
        try:
            from .storage import load_json_with_recovery
            data = load_json_with_recovery(_config_file(), None, validator=lambda v: isinstance(v, dict))
        except Exception:
            data = None
        if not isinstance(data, dict):
            data = {"settings": dict(DEFAULT_SETTINGS), "presets": default_presets(lang)}
            _save(data)
        return {"settings": normalize_settings(data.get("settings")),
                "presets": _localize_default_presets(normalize_presets(data.get("presets")), lang)}

def _localize_default_presets(rows: List[Dict[str, Any]], lang: str) -> List[Dict[str, Any]]:
    """Built-in presets the user never edited follow the interface language (they were stored in the
    language of the first start, so an English UI showed Russian buttons). Edited ones stay as written."""
    target = default_presets(lang)
    by_text = {}
    for other in ("ru", "en"):
        for index, row in enumerate(default_presets(other)):
            by_text[(row["name"], row["prompt"])] = index
    out = []
    for row in rows:
        index = by_text.get((row["name"], row["prompt"]))
        if index is not None and index < len(target):
            row = {**row, "name": target[index]["name"], "prompt": target[index]["prompt"]}
        out.append(row)
    return out

def _save(data: Dict[str, Any]) -> None:
    from .storage import save_json
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, data, backup=False)

def save_settings(patch: Any, lang: str = "ru") -> Dict[str, Any]:
    with _LOCK:
        config = load_config(lang)
        merged = dict(config["settings"])
        if isinstance(patch, dict):
            merged.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
        config["settings"] = normalize_settings(merged)
        _save(config)
        return config

def save_presets(rows: Any, lang: str = "ru") -> Dict[str, Any]:
    with _LOCK:
        config = load_config(lang)
        config["presets"] = normalize_presets(rows)
        _save(config)
        return config

def settings() -> Dict[str, Any]:
    try:
        return load_config()["settings"]
    except Exception:
        return dict(DEFAULT_SETTINGS)

EXPLORER_CLASSES = {"cabinetwclass", "explorewclass"}
DESKTOP_CLASSES = {"progman", "workerw"}
VSCODE_EXES = {"code.exe": "Code", "code - insiders.exe": "Code - Insiders", "cursor.exe": "Cursor",
               "windsurf.exe": "Windsurf", "vscodium.exe": "VSCodium", "codium.exe": "VSCodium",
               "trae.exe": "Trae", "kiro.exe": "Kiro"}
EDITOR_EXES = {"notepad++.exe": "Notepad++", "sublime_text.exe": "Sublime Text", "notepad.exe": "Notepad",
               "notepad3.exe": "Notepad3", "notepad4.exe": "Notepad4", "akelpad.exe": "AkelPad",
               "emeditor.exe": "EmEditor", "zed.exe": "Zed", "fleet.exe": "Fleet", "kate.exe": "Kate",
               "gvim.exe": "Vim", "typora.exe": "Typora", "obsidian.exe": "Obsidian", "memunote.exe": "MemuNote"}
JETBRAINS_RE = re.compile(r"^(idea|pycharm|webstorm|phpstorm|goland|rider|clion|rubymine|datagrip|rustrover|"
                          r"studio|aqua|dataspell)(64)?\.exe$")
OFFICE_EXES = {"winword.exe": "word", "excel.exe": "excel", "powerpnt.exe": "powerpoint"}
TERMINAL_EXES = {"windowsterminal.exe": "Windows Terminal", "cmd.exe": "cmd", "powershell.exe": "PowerShell",
                 "pwsh.exe": "PowerShell", "conhost.exe": "Console", "openconsole.exe": "Console",
                 "wezterm-gui.exe": "WezTerm", "alacritty.exe": "Alacritty", "mintty.exe": "Git Bash",
                 "tabby.exe": "Tabby", "hyper.exe": "Hyper", "conemu64.exe": "ConEmu", "conemu.exe": "ConEmu"}
TERMINAL_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class", "pseudoconsolewindow"}
SHELL_EXES = {"cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "zsh.exe", "fish.exe", "nu.exe", "wsl.exe",
              "python.exe", "node.exe"}
OWN_EXES = {"memuai.exe", "memuaiproxy.exe", "memucode.exe"}
MEMUFILES_EXE = "memufiles.exe"

def _exe_name(exe: Any) -> str:
    return str(exe or "").replace("\\", "/").rsplit("/", 1)[-1].lower()

def classify(window_class: str, exe: str, title: str = "") -> Dict[str, str]:
    """``{"kind": explorer|desktop|vscode|editor|jetbrains|office|terminal|app|own|none, "app": display name}``."""
    cls = str(window_class or "").lower()
    name = _exe_name(exe)
    if name in OWN_EXES:
        return {"kind": "own", "app": "MemuAI"}
    if name == MEMUFILES_EXE:
        return {"kind": "explorer", "app": "MemuFiles"}
    if cls in EXPLORER_CLASSES:
        return {"kind": "explorer", "app": "Explorer"}
    if cls in DESKTOP_CLASSES and name in ("explorer.exe", ""):
        return {"kind": "desktop", "app": "Desktop"}
    if name in VSCODE_EXES:
        return {"kind": "vscode", "app": VSCODE_EXES[name]}
    if name in EDITOR_EXES:
        return {"kind": "editor", "app": EDITOR_EXES[name]}
    if JETBRAINS_RE.match(name):
        return {"kind": "jetbrains", "app": "JetBrains"}
    if name in OFFICE_EXES:
        return {"kind": "office", "app": {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}[OFFICE_EXES[name]]}
    if name in TERMINAL_EXES or cls in TERMINAL_CLASSES:
        return {"kind": "terminal", "app": TERMINAL_EXES.get(name, "Terminal")}
    if not name and not cls:
        return {"kind": "none", "app": ""}
    pretty = re.sub(r"\.exe$", "", name) if name else (title or "")[:40]
    return {"kind": "app", "app": pretty[:1].upper() + pretty[1:] if pretty else ""}

_DIRTY = re.compile(r"^\s*(?:[●•*]\s*)+")

def parse_title(kind: str, exe: str, title: str) -> Dict[str, str]:
    """What the title tells about the open file: ``{"file", "path", "folder"}`` (empty strings when unknown)."""
    text = _DIRTY.sub("", str(title or "")).strip()
    text = re.sub(r"\s*\[(?:Administrator|Администратор|Restricted Mode|Ограниченный режим|Unsupported|"
                  r"Extension Development Host)\]\s*", " ", text, flags=re.I).strip()
    name = _exe_name(exe)
    out = {"file": "", "path": "", "folder": ""}
    if kind == "vscode":
        parts = [part.strip() for part in re.split(r"\s+[-—]\s+", text) if part.strip()]
        if parts and parts[-1].lower().startswith(("visual studio code", "cursor", "windsurf", "vscodium", "trae",
                                                    "kiro", "code - insiders")):
            parts = parts[:-1]
        if len(parts) >= 2:
            out["file"], out["folder"] = parts[0], re.sub(r"\s*\((?:Workspace|Рабочая область)\)$", "", parts[-1])
        elif len(parts) == 1:
            out["folder"] = re.sub(r"\s*\((?:Workspace|Рабочая область)\)$", "", parts[0])
        if out["file"].lower() in ("welcome", "добро пожаловать", "settings", "параметры", "extensions",
                                   "расширения", "keyboard shortcuts", "release notes"):
            out["file"] = ""
        return out
    if kind == "jetbrains":
        parts = [part.strip() for part in re.split(r"\s+[–—-]\s+", text) if part.strip()]
        if len(parts) >= 2:
            out["folder"], out["file"] = parts[0], parts[-1].replace("\\", "/").rsplit("/", 1)[-1]
        return out
    if kind == "editor":
        head = re.split(r"\s+[-—]\s+(?=[^-—]*$)", text, maxsplit=1)[0].strip()
        head = re.sub(r"\s+\([^)]*\)$", "", head) if name == "sublime_text.exe" else head
        head = _DIRTY.sub("", head).strip()
        if re.match(r"^[A-Za-z]:[\\/]|^\\\\", head):
            out["path"] = head
            out["file"] = re.split(r"[\\/]", head)[-1]
        elif head and not re.match(r"^(new \d+|untitled|безымянный|без имени|новый \d+)", head, re.I):
            out["file"] = head
        return out
    if kind == "office":
        head = re.split(r"\s+[-—]\s+(?=[^-—]*$)", text, maxsplit=1)[0].strip()
        head = re.sub(r"\s*\[[^\]]*\]$", "", head).strip()
        out["file"] = head
        return out
    return out

def _known_roots() -> List[Path]:
    rows: List[Path] = []
    for env in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(env)
        if value:
            rows.append(Path(value))
    if not IS_WINDOWS:
        rows += [Path(p) for p in ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr", "/var")]
    return rows

def is_protected(path: Path) -> bool:
    """System folders and drive roots are read-only for the folder agent (never written, renamed or deleted)."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return True
    if resolved.parent == resolved:
        return True
    home = Path(os.path.expanduser("~")).resolve()
    if resolved == home.parent:
        return True
    for root in _known_roots():
        try:
            root = root.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == root or root in resolved.parents:
            return True
    return False

def desktop_folder() -> str:
    """The Desktop folder (OneDrive-redirected one on Windows when that is where it lives)."""
    if IS_WINDOWS:
        try:
            from . import quick_apps_win
            path = quick_apps_win.known_folder("desktop")
            if path:
                return path
        except Exception:
            pass
    for candidate in (Path(os.environ.get("OneDrive") or "_") / "Desktop", Path.home() / "Desktop",
                      Path.home() / "Рабочий стол"):
        if candidate.is_dir():
            return str(candidate)
    return ""

def xdg_user_dir(key: str, home: Optional[Path] = None) -> str:
    """``XDG_<KEY>_DIR`` from the environment or ``~/.config/user-dirs.dirs`` (Linux desktops put a localised
    Downloads folder there, e.g. ``"$HOME/Загрузки"``); "" when it is not set or not a folder."""
    home = Path(home) if home else Path.home()
    name = f"XDG_{key.upper()}_DIR"
    value = os.environ.get(name, "")
    if not value:
        conf = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config")) / "user-dirs.dirs"
        try:
            for line in conf.read_text(encoding="utf-8", errors="replace").splitlines()[:200]:
                match = re.match(r'\s*' + name + r'\s*=\s*"?([^"]*)"?\s*$', line)
                if match:
                    value = match.group(1)
        except OSError:
            value = ""
    if not value:
        return ""
    value = value.replace("$HOME", str(home)).replace("${HOME}", str(home))
    path = Path(value)

    if not path.is_absolute() or path == home or not path.is_dir():
        return ""
    return str(path)

def downloads_folder() -> str:
    """The user's Downloads folder, "" when there is none (callers must not turn "" into the working folder)."""
    if IS_WINDOWS:
        try:
            from . import quick_apps_win
            path = quick_apps_win.known_folder("downloads")
            if path and os.path.isdir(path):
                return path
        except Exception:
            pass
    else:
        path = xdg_user_dir("download")
        if path:
            return path
    homes = [Path.home()]
    profile = os.environ.get("USERPROFILE") if IS_WINDOWS else ""
    if profile and Path(profile) != homes[0]:
        homes.append(Path(profile))
    for home in homes:
        for candidate in (home / "Downloads", home / "Загрузки"):
            if candidate.is_dir():
                return str(candidate)
    return ""

def documents_folder() -> str:
    if IS_WINDOWS:
        try:
            from . import quick_apps_win
            path = quick_apps_win.known_folder("documents")
            if path:
                return path
        except Exception:
            pass
    for candidate in (Path.home() / "Documents", Path.home() / "Документы"):
        if candidate.is_dir():
            return str(candidate)
    return str(Path.home())

def vscode_folders(app: str = "Code", appdata: Optional[str] = None) -> List[str]:
    """Folders VS Code (or a fork) remembers as opened (``User/globalStorage/storage.json``), newest first."""
    base = Path(appdata or os.environ.get("APPDATA") or Path.home() / ".config")
    path = base / app / "User" / "globalStorage" / "storage.json"
    try:
        if path.stat().st_size > 8_000_000:
            return []
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    found: List[str] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 8 or len(found) > 400:
            return
        if isinstance(value, str):
            if value.startswith("file:///"):
                found.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and key.startswith("file:///"):
                    found.append(key)
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
    windows = data.get("windowsState") if isinstance(data, dict) else None
    walk(windows)
    walk(data)
    out: List[str] = []
    for uri in found:
        parsed = urlparse(uri)
        local = unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:", local):
            local = local[1:]
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            local = "//" + parsed.netloc + local
        local = local.replace("/", os.sep) if IS_WINDOWS else local
        if local and local not in out:
            out.append(local)
    return out

def find_named_file(root: Path, name: str, *, limit: int = 20000) -> str:
    """The shortest relative path of a file called ``name`` under ``root`` ("" when none / ambiguous at depth)."""
    from .work_tools import SKIP_DIRS
    target = str(name or "").strip().lower()
    if not target:
        return ""
    best = ""
    seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for file in files:
            seen += 1
            if file.lower() == target:
                rel = os.path.relpath(os.path.join(current, file), root)
                if not best or rel.count(os.sep) < best.count(os.sep):
                    best = rel
        if seen > limit:
            break
    return best

def resolve_editor_file(kind: str, app: str, parsed: Dict[str, str], cmdline: List[str],
                        appdata: Optional[str] = None) -> Dict[str, str]:
    """Turn a title (+ the command line) into ``{"path", "folder"}`` — empty when it cannot be known for sure."""
    out = {"path": "", "folder": ""}
    path = parsed.get("path") or ""
    name = (parsed.get("file") or "").strip()
    if path and os.path.isfile(path):
        out["path"] = path
        out["folder"] = os.path.dirname(path)
    if not out["path"] and name:
        for arg in cmdline or []:
            arg = str(arg or "").strip('"')
            if arg and os.path.basename(arg).lower() == name.lower() and os.path.isfile(arg):
                out["path"] = arg
                out["folder"] = os.path.dirname(arg)
                break
    if kind == "vscode":
        folder_name = (parsed.get("folder") or "").strip().lower()
        if folder_name:
            app_dir = {"Code": "Code", "Code - Insiders": "Code - Insiders", "Cursor": "Cursor", "Windsurf": "Windsurf",
                       "VSCodium": "VSCodium", "Trae": "Trae", "Kiro": "Kiro"}.get(app, "Code")
            for folder in vscode_folders(app_dir, appdata):
                if os.path.basename(folder.rstrip("\\/")).lower() == folder_name and os.path.isdir(folder):
                    out["folder"] = folder
                    break
        if out["folder"] and name and not out["path"]:
            rel = find_named_file(Path(out["folder"]), name)
            if rel:
                out["path"] = os.path.join(out["folder"], rel)
    return out

CONTEXTS_KEPT = 24
_STATE: Dict[str, Any] = {"seq": 0, "current": None, "contexts": {}, "event": threading.Event(), "order": []}
_STATE["event"].set()

def _new_id(seq: int) -> str:
    return "a%d%s" % (seq, uuid.uuid4().hex[:6])

def register_context(ctx: Dict[str, Any], *, current: bool = True) -> Dict[str, Any]:
    """Store a captured context (also the entry point of the tests) and make it the current one
    (``current=False`` — a folder the file tools work in, app/quick_files.py: the hotkey's context stays)."""
    with _LOCK:
        _STATE["seq"] += 1
        ctx = dict(ctx)
        ctx.setdefault("id", _new_id(_STATE["seq"]))
        ctx.setdefault("seq", _STATE["seq"])
        ctx.setdefault("t", time.time())
        folder = str(ctx.get("folder") or "")
        if folder:
            try:
                resolved = Path(folder).resolve()
                ctx["folder"] = str(resolved) if resolved.is_dir() else ""
            except (OSError, RuntimeError):
                ctx["folder"] = ""
            if not ctx["folder"]:
                ctx.setdefault("reason", "not_a_folder")
        if ctx.get("folder"):
            ctx["writable"] = not is_protected(Path(ctx["folder"])) and os.access(ctx["folder"], os.W_OK)
        _STATE["contexts"][ctx["id"]] = ctx
        _STATE["order"].append(ctx["id"])
        while len(_STATE["order"]) > CONTEXTS_KEPT:
            _STATE["contexts"].pop(_STATE["order"].pop(0), None)
        if current:
            _STATE["current"] = ctx["id"]
        return ctx

def get_context(ctx_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _STATE["contexts"].get(str(ctx_id or ""))

def update_context(ctx_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _LOCK:
        ctx = _STATE["contexts"].get(str(ctx_id or ""))
        if ctx is not None:
            ctx.update(patch)
        return ctx

def current_context(wait: float = 0.0) -> Optional[Dict[str, Any]]:
    if wait > 0:
        _STATE["event"].wait(min(wait, 6.0))
    with _LOCK:
        current = _STATE["current"]
        return _STATE["contexts"].get(current) if current else None

def clear_current() -> None:
    with _LOCK:
        _STATE["current"] = None

def public_context(ctx: Optional[Dict[str, Any]], lang: str = "ru") -> Optional[Dict[str, Any]]:
    """What the page may see (no hwnd/pid; paths are the user's own — they are shown in the chip tooltip)."""
    if not ctx:
        return None
    selected = [str(p) for p in (ctx.get("selected") or [])]
    folder = str(ctx.get("folder") or "")
    rel = []
    for item in selected[:200]:
        try:
            rel.append(os.path.relpath(item, folder) if folder else os.path.basename(item))
        except ValueError:
            rel.append(os.path.basename(item))
    out = {
        "id": ctx.get("id"), "seq": ctx.get("seq"), "kind": ctx.get("kind"), "app": ctx.get("app") or "",
        "exe": ctx.get("exe") or "", "title": str(ctx.get("title") or "")[:300],
        "folder": folder, "folder_name": (os.path.basename(folder.rstrip("\\/")) or folder) if folder else "",
        "selected": rel, "selected_count": len(selected), "selected_dirs": int(ctx.get("selected_dirs") or 0),
        "file": str(ctx.get("file") or ""), "file_name": str(ctx.get("file_name") or ""),
        "doc": ctx.get("doc") if isinstance(ctx.get("doc"), dict) else None,
        "writable": bool(ctx.get("writable")), "approx": bool(ctx.get("approx")),
        "reason": str(ctx.get("reason") or ""), "pending": bool(ctx.get("pending")),
        "color": ctx.get("color") if isinstance(ctx.get("color"), dict) else None,
    }
    return out

def capture(hwnd: int, *, source: str = "hotkey", prefs: Optional[Dict[str, Any]] = None,
            describe: Optional[Callable[[int], Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Read the context of window ``hwnd`` synchronously (the thread of :func:`note_foreground`)."""
    config = prefs if isinstance(prefs, dict) else settings()
    try:
        from . import quick_apps_win as win
    except Exception:
        win = None
    describe = describe or (win.describe_window if win is not None else None)
    if describe is None:
        return None
    info = describe(int(hwnd or 0)) or {}
    meta = classify(info.get("class", ""), info.get("exe", ""), info.get("title", ""))
    kind = meta["kind"]
    ctx: Dict[str, Any] = {"kind": kind, "app": meta["app"], "exe": _exe_name(info.get("exe")),
                           "title": str(info.get("title") or "")[:400], "hwnd": int(hwnd or 0),
                           "pid": int(info.get("pid") or 0), "source": source}
    if isinstance(info.get("color"), dict):
        ctx["color"] = info["color"]
    if kind in ("own", "none"):
        return ctx
    if kind in ("explorer", "desktop") and not config.get("explorer", True):
        ctx["kind"], ctx["reason"] = "app", "off"
        return ctx
    if kind not in ("explorer", "desktop", "app") and not config.get("apps", True):
        ctx["kind"], ctx["reason"] = "app", "off"
        return ctx
    try:
        if kind == "explorer" and meta["app"] == "MemuFiles":
            folder = memufiles_folder(ctx["pid"])
            ctx.update({"folder": folder.get("path") or "", "selected": folder.get("selected") or [],
                        "reason": folder.get("reason") or ""})
            ctx["selected_dirs"] = sum(1 for p in ctx["selected"] if os.path.isdir(p))
        elif kind == "explorer" and win is not None:
            folder = win.explorer_folder(int(hwnd), info.get("title", ""))
            ctx.update({"folder": folder.get("path") or "", "selected": folder.get("selected") or [],
                        "reason": folder.get("reason") or ""})
            ctx["selected_dirs"] = sum(1 for p in ctx["selected"] if os.path.isdir(p))
            if folder.get("location"):
                ctx["location"] = folder["location"]
        elif kind == "desktop":
            ctx["folder"] = desktop_folder()
            if win is not None:
                try:
                    ctx["selected"] = win.desktop_selection() or []
                except Exception:
                    ctx["selected"] = []
        elif kind in ("vscode", "editor", "jetbrains"):
            parsed = parse_title(kind, ctx["exe"], ctx["title"])
            cmdline = win.cmdline(ctx["pid"]) if win is not None else []
            found = resolve_editor_file(kind, meta["app"], parsed, cmdline)
            ctx.update({"file": found["path"], "file_name": parsed.get("file") or os.path.basename(found["path"]),
                        "folder": found["folder"]})
            if not found["path"] and parsed.get("file"):
                ctx["reason"] = "file_unknown"
        elif kind == "office":
            parsed = parse_title(kind, ctx["exe"], ctx["title"])
            ctx["doc"] = {"kind": OFFICE_EXES.get(ctx["exe"], "word"), "name": parsed.get("file") or ""}
            if not config.get("office", True):
                ctx["doc"]["off"] = True
        elif kind == "terminal" and win is not None:
            cwd = win.terminal_cwd(ctx["pid"], ctx["exe"], ctx["title"])
            ctx.update({"folder": cwd.get("path") or "", "approx": bool(cwd.get("approx")),
                        "shell": cwd.get("shell") or ""})
    except Exception as exc:
        log.debug("quick apps: capture failed: %s", exc)
        ctx["reason"] = ctx.get("reason") or "error"
    return ctx

def memufiles_folder(pid: int = 0) -> Dict[str, Any]:
    """r41-files: the folder and selection of the MemuFiles window in front — asked from the running MemuFiles over its
    local API (``/api/remote {action: "state"}``: the page reports them on every navigation / selection change).
    Inside an archive opened like a folder: the archive's own folder with the archive selected (the folder tools need a
    real folder). ``{"path", "selected", "reason"}``."""
    try:
        from .memufiles import desktop as memufiles_desktop
        reply = memufiles_desktop._remote("state", {}, timeout=1.0)
    except Exception:                             # noqa: BLE001 - MemuFiles not installed / broken instance file
        reply = None
    if not reply or not isinstance(reply.get("state"), dict):
        return {"path": "", "selected": [], "reason": "memufiles_offline"}
    state = reply["state"]
    if pid and state.get("pid") and int(state["pid"]) != int(pid):
        return {"path": "", "selected": [], "reason": "memufiles_other"}
    folder = str(state.get("folder") or "")
    selected = [str(p) for p in state.get("selected") or [] if isinstance(p, str)]
    if state.get("archive") and folder:
        selected, folder = [folder], os.path.dirname(folder)
    if not folder or not os.path.isdir(folder):
        return {"path": "", "selected": [], "reason": "no_folder"}
    return {"path": folder, "selected": [p for p in selected if os.path.exists(p)][:200], "reason": ""}

def note_foreground(hwnd: int, source: str = "hotkey", *, own_pids: Any = ()) -> None:
    """The hotkey: remember the program in front and read its context in the background (never blocks)."""
    if not hwnd:
        return
    with _LOCK:
        _STATE["seq"] += 1
        token = _STATE["seq"]
        _STATE["event"].clear()
        _STATE["current"] = None

    def run() -> None:
        try:
            ctx = capture(hwnd, source=source)
            if ctx is not None and int(ctx.get("pid") or 0) in set(own_pids or ()):
                ctx = None
            with _LOCK:
                stale = _STATE["seq"] != token
            if ctx is not None and not stale and ctx.get("kind") not in ("own", "none"):
                register_context(ctx)
        except Exception as exc:
            log.debug("quick apps: background capture failed: %s", exc)
        finally:
            with _LOCK:
                if _STATE["seq"] in (token, token + 1):
                    _STATE["event"].set()
    threading.Thread(target=run, name="memuai-quick-apps", daemon=True).start()

def presets_for(ctx: Optional[Dict[str, Any]], presets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    exe = _exe_name((ctx or {}).get("exe"))
    out = []
    for row in presets:
        apps = row.get("apps") or []
        if not apps or exe in apps:
            out.append(row)
    return out[:12]
