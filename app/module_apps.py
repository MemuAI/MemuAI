"""Round 33: modules outside the MemuAI window (#6, #26).

Two features share one piece of machinery — a **native module window**: a
separate pywebview window that shows exactly one module (the page
``/?module_app=<id>`` = the normal MemuAI page in «module only» mode, so the
module keeps every bridge feature, the theme, the language and the backend).

* **#26 «Install as an app»** (``install``/``uninstall``): a Start menu (and
  optionally desktop) shortcut ``<Title> (MemuAI).lnk`` → ``MemuAI.exe --module
  <id>`` with the module's own icon and AppUserModelID ``MemuAI.Module.<id>``.
  The window of that module carries the same id, so Windows groups it as its own
  application on the taskbar, separately from MemuAI. Launching the shortcut
  while MemuAI runs reuses the running instance (``POST /api/pg/module-apps/launch``
  from the second process, see ``memuai.py``); without a running MemuAI the
  process starts in module-only mode (no main window; the tray icon stays) and
  exits when the last module window closes, if the main window was never opened.
  The module can also be hidden from the MemuAI module list (the existing
  ``launcher.hidden`` flag).
* **#6 Closing MemuAI with modules open** (``main_close_guard``): the main
  window's close (×, Alt+F4, taskbar «Close window» — all reach pywebview's
  ``closing``) no longer silently takes the modules away. The page is asked
  (``MemuModuleApps.askAppClose()``): keep the modules — each moves to its own
  window and MemuAI hides to the tray — or close them too, or cancel; «don't ask
  again» stores the choice (``apps.json → on_app_close``). A module with unsaved
  work or a running generation always asks.

State: ``modules/apps.json`` (``{"apps": {id: {...}}, "on_app_close": "ask"}``) —
not state.json (``modules_host._mutate_state`` rewrites that file whole).
Win32 calls live in ``app/module_apps_win.py`` and are injected (mock-tested).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import module_appbar, modules_host
from .storage import load_json_with_recovery, mutate_json

log = logging.getLogger("memuai.module_apps")

CLOSE_CHOICES = ("ask", "keep", "close")
AUMID_PREFIX = "MemuAI.Module."
ICON_PNG_LIMIT = 600_000
WINDOW_MIN = (420, 320)
WINDOW_DEFAULT = (1000, 720)
_FILE_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def _lang_en() -> bool:
    try:
        from .pgdata import load_preferences
        return load_preferences().get("language") == "en"
    except Exception:
        return False

def text(ru: str, en: str) -> str:
    return en if _lang_en() else ru

def _root(root: Optional[Path] = None) -> Path:
    return Path(root) if root is not None else modules_host.default_root()

def _store_path(root: Optional[Path] = None) -> Path:
    return _root(root) / "apps.json"

def app_dir(root: Optional[Path] = None) -> Path:
    """Icons of the module apps. Deliberately OUTSIDE ``<data>/memuai``: the MemuAI icon sync
    (``assistant_icon`` → ``win_app_identity.sync_shortcut_icons``) re-points every MemuAI.exe
    shortcut whose icon lives there — it would turn module shortcuts into MemuAI ones."""
    if root is not None:
        return Path(root) / "apps"
    from .pgdata import DATA_DIR
    return Path(DATA_DIR) / "module-apps"

def _icon_file_of(row: Dict[str, Any], module_id: str) -> str:
    value = str(row.get("icon_file") or "")
    return value if len(value) < 600 and Path(value).name.lower() == f"{module_id}.ico" else ""

def _normalize_store(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    clean: Dict[str, Any] = {}
    for key, row in apps.items():
        try:
            module_id = modules_host._safe_id(key)
        except ValueError:
            continue
        row = row if isinstance(row, dict) else {}
        clean[module_id] = {
            "installed": float(row.get("installed") or 0) or time.time(),
            "desktop": bool(row.get("desktop", True)),
            "start_menu": bool(row.get("start_menu", True)),
            "hidden_in_memuai": bool(row.get("hidden_in_memuai", False)),
            "links": [str(item) for item in (row.get("links") or []) if isinstance(item, str)][:4],
            "title": str(row.get("title") or "")[:120],

            "icon_sig": str(row.get("icon_sig") or "")[:80],
            "icon_file": _icon_file_of(row, module_id),
        }
    sizes = data.get("sizes") if isinstance(data.get("sizes"), dict) else {}
    clean_sizes = {}
    for key, size in sizes.items():
        try:
            module_id = modules_host._safe_id(key)
            w, h = int(size.get("width")), int(size.get("height"))
        except (ValueError, TypeError, AttributeError):
            continue
        clean_sizes[module_id] = {"width": max(WINDOW_MIN[0], min(4000, w)), "height": max(WINDOW_MIN[1], min(3000, h))}
    choice = data.get("on_app_close")

    appbar = data.get("appbar") if isinstance(data.get("appbar"), dict) else {}
    clean_appbar = {}
    for key, flag in appbar.items():
        try:
            clean_appbar[modules_host._safe_id(key)] = bool(flag)
        except ValueError:
            continue
    return {"apps": clean, "sizes": clean_sizes, "on_app_close": choice if choice in CLOSE_CHOICES else "ask",
            "custom_appbar": bool(data.get("custom_appbar", True)), "appbar": clean_appbar}

def load_store(root: Optional[Path] = None) -> Dict[str, Any]:
    return _normalize_store(load_json_with_recovery(str(_store_path(root)), {}))

def _mutate(fn: Callable[[Dict[str, Any]], None], root: Optional[Path] = None) -> Dict[str, Any]:
    path = _store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    def apply(raw: Any):
        data = _normalize_store(raw)
        fn(data)
        return True, _normalize_store(data)

    mutate_json(path, {}, apply, backup=False, validator=lambda value: isinstance(value, dict), repair_invalid=True)
    return load_store(root)

def set_close_choice(choice: str, root: Optional[Path] = None) -> str:
    if choice not in CLOSE_CHOICES:
        raise ValueError(text("Неизвестный вариант", "Unknown choice"))
    _mutate(lambda data: data.__setitem__("on_app_close", choice), root)
    return choice

def set_appbar(enabled: bool, module_id: str = "", root: Optional[Path] = None) -> Dict[str, Any]:
    """Round 38: switch the custom title bar for one module (``module_id``) or for every module app."""
    module_id = modules_host._safe_id(module_id) if module_id else ""

    def apply(data: Dict[str, Any]) -> None:
        if module_id:
            data["appbar"][module_id] = bool(enabled)
        else:
            data["custom_appbar"] = bool(enabled)
    return _mutate(apply, root)

def remember_size(module_id: str, width: int, height: int, root: Optional[Path] = None) -> None:
    module_id = modules_host._safe_id(module_id)
    _mutate(lambda data: data["sizes"].__setitem__(module_id, {"width": int(width), "height": int(height)}), root)

def window_size(module_id: str, root: Optional[Path] = None) -> tuple:
    store = load_store(root)
    size = store["sizes"].get(module_id)
    if size:
        return size["width"], size["height"]
    try:
        policy = modules_host.resolve_window(_root(root), module_id)
        w = int(policy.get("width") or WINDOW_DEFAULT[0])
        h = int(policy.get("height") or WINDOW_DEFAULT[1])

        return max(WINDOW_MIN[0], min(1600, w)), max(WINDOW_MIN[1], min(1100, h + 8))
    except Exception:
        return WINDOW_DEFAULT

def app_id(module_id: str) -> str:
    """AppUserModelID of the module app: ``MemuAI.Module.<id>`` (≤ 128 chars, no spaces)."""
    module_id = modules_host._safe_id(module_id)
    return (AUMID_PREFIX + module_id)[:128]

def shortcut_name(title: str, module_id: str) -> str:
    clean = _FILE_BAD.sub(" ", str(title or "")).strip().strip(".") or module_id
    clean = re.sub(r"\s+", " ", clean)[:80]
    return f"{clean} (MemuAI).lnk"

def launch_command(module_id: str, *, frozen: Optional[bool] = None, executable: str = "",
                   program_dir: str = "") -> Dict[str, str]:
    """What the shortcut runs: ``MemuAI.exe --module <id>`` (or pythonw memuai.py in a dev checkout)."""
    module_id = modules_host._safe_id(module_id)
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    executable = executable or sys.executable
    if frozen:
        target = executable
        args = f"--module {module_id}"
        workdir = str(Path(target).parent)
    else:
        root = Path(program_dir or os.environ.get("MEMUAI_PROGRAM_DIR") or Path(__file__).resolve().parents[1])
        pyw = Path(executable).with_name("pythonw.exe")
        target = str(pyw if pyw.exists() else Path(executable))
        args = f'"{root / "memuai.py"}" --module {module_id}'
        workdir = str(root)
    return {"target": target, "args": args, "workdir": workdir,
            "command": f'"{target}" {args}'}

def _accent_rgb(accent: str) -> tuple:
    value = str(accent or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    return (174, 86, 48)

def _letter_tile(title: str, accent: str):
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    r, g, b = _accent_rgb(accent)
    draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=58, fill=(r, g, b, 255))
    letter = (str(title or "?").strip()[:1] or "?").upper()
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 140)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), letter, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), letter, font=font, fill=(255, 255, 255, 255))
    return image

def decode_png(data_url: str):
    """A PNG data URL from the page (the module glyph drawn on a canvas) → a PIL image, or ``None``."""
    if not data_url or not isinstance(data_url, str):
        return None
    match = re.fullmatch(r"data:image/png;base64,([A-Za-z0-9+/=\s]+)", data_url.strip())
    if not match:
        return None
    try:
        raw = base64.b64decode(match.group(1), validate=False)
    except Exception:
        return None
    if len(raw) > ICON_PNG_LIMIT or not raw.startswith(b"\x89PNG"):
        return None
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(raw))
        if image.width > 1024 or image.height > 1024 or image.width < 16:
            return None
        return image.convert("RGBA")
    except Exception:
        return None

def _icon_picture(module_id: str, row: Dict[str, Any], png: str, root: Optional[Path]):
    """``(ico bytes, 256 px preview, signature)`` of a module app icon.

    Round 40: the module's own icon file wins (the user's icon, a built-in's ``icon.ico`` drawn per size from its
    ``icon.svg``, a raster package icon) — the same picture whether the page asked or the start-up repair did;
    then the glyph tile the page drew, then a letter tile. The signature says what the ``.ico`` was made from, so
    :func:`repair` can tell an outdated app icon (an update of MemuAI changed the built-in's icon)."""
    from . import module_icons
    source = module_icons.app_icon_source(_root(root), module_id)
    image = module_icons.raster_for(_root(root), module_id) if source is not None else None
    if image is not None:
        data = source.read_bytes() if source.suffix.lower() == ".ico" else module_icons.ico_bytes(module_icons.ico_frames(image))
        return data, image, module_icons.source_signature(source)
    image = decode_png(png)
    if image is not None:
        sig = page_signature(png)
    else:
        title, accent = str(row.get("title") or module_id), str(row.get("accent") or "")
        image = _letter_tile(title, accent)
        sig = "letter:" + hashlib.sha256(f"{title}\0{accent}".encode("utf-8")).hexdigest()[:16] + ":" + module_icons.ICO_RECIPE
    if image.size != (256, 256):
        from PIL import Image
        image = image.resize((256, 256), Image.LANCZOS)
    return module_icons.ico_bytes(module_icons.ico_frames(image)), image, sig

def page_signature(png: str) -> str:
    """The signature of an app icon the page drew (a glyph tile, see :func:`_icon_picture`)."""
    from .module_icons import ICO_RECIPE
    return "page:" + hashlib.sha256(str(png or "").strip().encode("utf-8")).hexdigest()[:16] + ":" + ICO_RECIPE

def _shell_dir(root: Optional[Path] = None) -> Path:
    return app_dir(root) / "shell"

def write_icon(module_id: str, row: Dict[str, Any], png: str = "", root: Optional[Path] = None) -> Dict[str, str]:
    """``<id>.ico`` + ``<id>.png`` (the settings preview) + ``shell/<hash>/<id>.ico`` — the copy shortcuts, pinned
    taskbar buttons and the window's relaunch icon point at.

    Round 40: Explorer and the taskbar cache an icon by its PATH: rewriting ``<id>.ico`` in place left the old
    picture on every shortcut and taskbar button. A new picture gets a new path (its content hash), so the shell
    has to read it; older copies of this module's icon are removed."""
    module_id = modules_host._safe_id(module_id)
    folder = app_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    data, image, sig = _icon_picture(module_id, row, png, root)
    ico = folder / f"{module_id}.ico"
    png_path = folder / f"{module_id}.png"
    image.save(png_path, format="PNG")
    shell_ico = _shell_dir(root) / hashlib.sha256(data).hexdigest()[:12] / f"{module_id}.ico"
    for path in (ico, shell_ico):
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".ico.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    _prune_shell_icons(module_id, keep=shell_ico, root=root)
    return {"ico": str(ico), "png": str(png_path), "shell": str(shell_ico), "sig": sig}

def _prune_shell_icons(module_id: str, keep: Optional[Path] = None, root: Optional[Path] = None) -> None:
    base = _shell_dir(root)
    for path in base.glob(f"*/{module_id}.ico") if base.is_dir() else ():
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
            path.parent.rmdir()
        except OSError:
            pass

def shell_icon(module_id: str, root: Optional[Path] = None) -> Path:
    """The ``.ico`` the shell shows for an app (the hashed copy), else ``<id>.ico``."""
    entry = load_store(root)["apps"].get(module_id) or {}
    path = Path(str(entry.get("icon_file") or ""))
    if entry.get("icon_file") and path.is_file():
        return path
    return app_dir(root) / f"{module_id}.ico"

def refresh_app_icon(module_id: str, png: str = "", root: Optional[Path] = None,
                     shell: Optional["Shell"] = None) -> Dict[str, str]:
    """Round 40: an installed app takes its module's current icon everywhere the shell keeps one — the ``.ico``,
    the Start menu / desktop shortcuts (rewritten), shortcuts Windows made when the app was pinned to the taskbar
    or Start (re-pointed), and Explorer's icon cache (``SHChangeNotify``). The open window is
    :meth:`ModuleWindows.refresh_icon`."""
    shell = shell or SHELL
    module_id = modules_host._safe_id(module_id)
    row = _module_row(module_id, root)
    out = write_icon(module_id, row, png, root)
    entry = load_store(root)["apps"].get(module_id)
    if not entry:
        return out
    entry["icon_sig"], entry["icon_file"] = out["sig"], out["shell"]
    if shell.available():
        _remove_links(entry.get("links") or [], root)
        entry["links"] = _write_links(module_id, row, entry, out["shell"], shell)
        moved = shell.retarget_icons(module_id, out["shell"], app_dir(root))
        shell.notify([*entry["links"], *moved, out["shell"]])
    _mutate(lambda data: data["apps"].__setitem__(module_id, entry), root)
    return out

def _module_row(module_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
    module_id = modules_host._safe_id(module_id)
    row = next((item for item in modules_host.list_modules(_root(root)) if item["id"] == module_id), None)
    if row is None:
        raise KeyError(text("Модуль не найден", "Module not found"))
    return row

def _title(row: Dict[str, Any]) -> str:
    return (row.get("title_en") or row.get("title")) if _lang_en() else (row.get("title") or row.get("title_en"))

class Shell:
    """The Windows shell operations (``module_apps_win``), replaceable in tests."""

    def available(self) -> bool:
        from . import module_apps_win
        return module_apps_win.available()

    def folder(self, name: str) -> Optional[Path]:
        from . import module_apps_win
        return module_apps_win.known_folder(name)

    def shortcut(self, link: Path, **kwargs) -> bool:
        from . import module_apps_win
        return module_apps_win.create_shortcut(link, **kwargs)

    def retarget_icons(self, module_id: str, icon: str, icon_dir: Path) -> List[str]:
        """Shortcuts made by Windows (pinned to the taskbar / Start) or copied by the person: re-point to ``icon``."""
        from . import module_apps_win
        return module_apps_win.retarget_shortcut_icons(module_id, icon, icon_dir)

    def notify(self, paths: List[str]) -> None:
        from . import module_apps_win
        module_apps_win.refresh_shell_icons(paths)

SHELL = Shell()

def _write_links(module_id: str, row: Dict[str, Any], entry: Dict[str, Any], icon: str, shell: Shell) -> List[str]:
    if not shell.available():
        return []
    command = launch_command(module_id)
    name = shortcut_name(_title(row), module_id)
    links: List[str] = []
    wanted = [("programs", entry.get("start_menu", True)), ("desktop", entry.get("desktop", True))]
    for folder_name, on in wanted:
        if not on:
            continue
        folder = shell.folder(folder_name)
        if folder is None:
            continue
        link = Path(folder) / name
        try:
            if shell.shortcut(link, target=command["target"], args=command["args"], workdir=command["workdir"],
                              icon=icon, description=text(f"Модуль MemuAI «{_title(row)}»",
                                                          f"MemuAI module “{_title(row)}”"),
                              app_id=app_id(module_id)):
                links.append(str(link))
        except Exception as exc:  # noqa: BLE001
            log.warning("module app shortcut %s failed: %s", link, exc)
    return links

def _remove_links(links: List[str], root: Optional[Path] = None) -> None:
    for item in links:
        path = Path(item)

        if path.suffix.lower() == ".lnk" and path.name.endswith("(MemuAI).lnk"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("module app shortcut %s not removed: %s", path, exc)

def install(module_id: str, *, desktop: bool = True, start_menu: bool = True, hide: bool = False,
            icon_png: str = "", root: Optional[Path] = None, shell: Optional[Shell] = None) -> Dict[str, Any]:
    shell = shell or SHELL
    module_id = modules_host._safe_id(module_id)
    row = _module_row(module_id, root)
    if not row.get("enabled"):
        row = modules_host.configure_module(_root(root), module_id, enabled=True)
    previous = load_store(root)["apps"].get(module_id)
    if previous:
        _remove_links(previous.get("links") or [], root)
    icons = write_icon(module_id, row, icon_png, root)
    entry = {"installed": time.time(), "desktop": bool(desktop), "start_menu": bool(start_menu),
             "hidden_in_memuai": bool(hide), "title": _title(row), "icon_sig": icons["sig"], "icon_file": icons["shell"]}
    entry["links"] = _write_links(module_id, row, entry, icons["shell"], shell)
    if entry["links"]:
        shell.notify([*entry["links"], icons["shell"]])
    if shell.available() and (desktop or start_menu) and not entry["links"]:
        raise OSError(text("Windows не дал создать ярлык. Подробности — в журнале MemuAI.",
                           "Windows refused to create the shortcut. See the MemuAI log for details."))
    _mutate(lambda data: data["apps"].__setitem__(module_id, entry), root)
    modules_host.configure_module(_root(root), module_id, hidden=bool(hide))
    return describe(module_id, root, shell=shell)

def update(module_id: str, *, hide: Optional[bool] = None, desktop: Optional[bool] = None,
           start_menu: Optional[bool] = None, root: Optional[Path] = None,
           shell: Optional[Shell] = None) -> Dict[str, Any]:
    shell = shell or SHELL
    module_id = modules_host._safe_id(module_id)
    entry = load_store(root)["apps"].get(module_id)
    if not entry:
        raise KeyError(text("Модуль не установлен как приложение", "The module is not installed as an app"))
    row = _module_row(module_id, root)
    if hide is not None:
        entry["hidden_in_memuai"] = bool(hide)
        modules_host.configure_module(_root(root), module_id, hidden=bool(hide))
    if desktop is not None or start_menu is not None:
        if desktop is not None:
            entry["desktop"] = bool(desktop)
        if start_menu is not None:
            entry["start_menu"] = bool(start_menu)
        _remove_links(entry.get("links") or [], root)
        ico = shell_icon(module_id, root)
        if not ico.is_file():
            icons = write_icon(module_id, row, "", root)
            entry["icon_sig"], entry["icon_file"] = icons["sig"], icons["shell"]
            ico = Path(icons["shell"])
        entry["links"] = _write_links(module_id, row, entry, str(ico), shell)
    _mutate(lambda data: data["apps"].__setitem__(module_id, entry), root)
    return describe(module_id, root, shell=shell)

def uninstall(module_id: str, *, unhide: bool = True, root: Optional[Path] = None) -> Dict[str, Any]:
    module_id = modules_host._safe_id(module_id)
    entry = load_store(root)["apps"].get(module_id)
    if entry:
        _remove_links(entry.get("links") or [], root)
    for suffix in (".ico", ".png"):
        try:
            (app_dir(root) / f"{module_id}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    _prune_shell_icons(module_id, root=root)
    _mutate(lambda data: data["apps"].pop(module_id, None), root)
    if unhide and entry and entry.get("hidden_in_memuai"):
        try:
            modules_host.configure_module(_root(root), module_id, hidden=False)
        except (KeyError, ValueError):
            pass
    return {"ok": True, "id": module_id}

def repair(root: Optional[Path] = None, shell: Optional[Shell] = None) -> int:
    """After MemuAI moved or updated: rewrite shortcuts, drop apps of deleted modules. Returns the count fixed."""
    shell = shell or SHELL
    store = load_store(root)
    known = {row["id"]: row for row in modules_host.list_modules(_root(root))}
    fixed = 0
    for module_id, entry in store["apps"].items():
        if module_id not in known:
            uninstall(module_id, unhide=False, root=root)
            fixed += 1
            continue
        if icon_outdated(module_id, entry, root):

            refresh_app_icon(module_id, root=root, shell=shell)
            fixed += 1
            continue
        if not shell.available():
            continue
        missing = (entry.get("start_menu") or entry.get("desktop")) and not all(Path(p).exists() for p in entry["links"])
        if missing or not entry["links"]:
            ico = shell_icon(module_id, root)
            if not ico.is_file():
                icons = write_icon(module_id, known[module_id], "", root)
                entry["icon_sig"], entry["icon_file"] = icons["sig"], icons["shell"]
                ico = Path(icons["shell"])
            entry["links"] = _write_links(module_id, known[module_id], entry, str(ico), shell)
            _mutate(lambda data, m=module_id, e=entry: data["apps"].__setitem__(m, e), root)
            fixed += 1
    return fixed

def icon_outdated(module_id: str, entry: Dict[str, Any], root: Optional[Path] = None) -> bool:
    """Is an installed app's ``.ico`` not the module's current icon? Apps installed before Round 40 have no
    signature: a module with an icon file gets a new ``.ico`` (and the shortcuts a new icon path) once."""
    from . import module_icons
    if not shell_icon(module_id, root).is_file():
        return True
    want = module_icons.source_signature(module_icons.app_icon_source(_root(root), module_id))
    if want:
        return entry.get("icon_sig") != want

    return str(entry.get("icon_sig") or "").startswith("file:")

def describe(module_id: str, root: Optional[Path] = None, shell: Optional[Shell] = None) -> Dict[str, Any]:
    shell = shell or SHELL
    entry = load_store(root)["apps"].get(module_id) or {}
    return {"id": module_id, "installed": bool(entry), **entry,
            "app_id": app_id(module_id), "command": launch_command(module_id)["command"],
            "icon": f"/api/pg/module-apps/icon/{module_id}.png" if (app_dir(root) / f"{module_id}.png").is_file() else "",
            "shortcuts_supported": shell.available()}

class ModuleWindows:
    """Native module windows of the running MemuAI (pywebview), plus the close guard of the main window.

    ``webview``/``win`` are injectable for tests. Every public method is safe to
    call from any thread; pywebview marshals window calls to the UI thread.
    """

    def __init__(self, base_url: str, *, webview_module=None, win=None, root: Optional[Path] = None,
                 module_only: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self._webview = webview_module
        self._win = win
        self.root = root
        self.windows: Dict[str, Any] = {}
        self.hwnds: Dict[str, int] = {}
        self.window_state: Dict[str, Dict[str, Any]] = {}
        self.main_modules: List[Dict[str, Any]] = []
        self.module_only = module_only
        self.main_shown = False
        self.quitting = False
        self._force_close: set = set()
        self.appbars: Dict[str, Any] = {}
        self._appbar_factory: Optional[Callable[..., Any]] = None
        self._allow_main_close = False
        self._lock = threading.RLock()

        self.main_window_ref: Callable[[], Any] = lambda: None
        self.close_main_cb: Callable[[], None] = lambda: None
        self.quit_cb: Callable[[], None] = lambda: None
        self.show_main_cb: Callable[[], None] = lambda: None
        self.dark_cb: Callable[[], bool] = lambda: True

    @property
    def webview(self):
        if self._webview is None:
            import webview  # noqa: PLC0415
            self._webview = webview
        return self._webview

    @property
    def win(self):
        if self._win is None:
            from . import module_apps_win
            self._win = module_apps_win
        return self._win

    def open_ids(self) -> List[str]:
        with self._lock:
            return list(self.windows)

    def _eval_main(self, script: str) -> None:
        window = self.main_window_ref()
        if window is None:
            return

        def run():
            try:
                window.evaluate_js(script)
            except Exception as exc:  # noqa: BLE001
                log.debug("module apps: main page call failed: %s", exc)
        threading.Thread(target=run, name="MemuAI module apps", daemon=True).start()

    def _eval_window(self, module_id: str, script: str) -> None:
        window = self.windows.get(module_id)
        if window is None:
            return

        def run():
            try:
                window.evaluate_js(script)
            except Exception as exc:  # noqa: BLE001
                log.debug("module apps: module page call failed: %s", exc)
        threading.Thread(target=run, name="MemuAI module window", daemon=True).start()

    def notify_main(self) -> None:
        self._eval_main("window.MemuModuleApps && MemuModuleApps.sync(%s)" % json.dumps(self.open_ids()))

    def open(self, module_id: str, width: int = 0, height: int = 0) -> Dict[str, Any]:
        module_id = modules_host._safe_id(module_id)
        row = _module_row(module_id, self.root)
        if not row.get("enabled"):
            raise PermissionError(text("Модуль выключен — включите его в настройках модулей",
                                       "The module is off — turn it on in the module settings"))
        with self._lock:
            existing = self.windows.get(module_id)
            if existing is not None:
                self.focus(module_id)
                return {"ok": True, "native": True, "focused": True}
            if not width or not height:
                width, height = window_size(module_id, self.root)
            title = _title(row)
            dark = bool(self.dark_cb())
            options = dict(width=int(width), height=int(height), min_size=WINDOW_MIN, resizable=True,
                           frameless=False, easy_drag=False, hidden=True,
                           background_color="#1a1a1c" if dark else "#fcfcfb")
            if module_id == "music":
                options["text_select"] = True

            controller = None
            if module_appbar.enabled(load_store(self.root), module_id):
                controller = module_appbar.create_controller(factory=self._appbar_factory)
            if controller is not None:
                spec = module_appbar.for_module(module_id, row.get("window"))
                options["js_api"] = module_appbar.ModuleAppbarBridge(controller, module_id, spec)
            url = f"{self.base_url}/?module_app={module_id}"
            window = None
            for _ in range(2):
                try:
                    window = self.webview.create_window(title, url, **options)
                    break
                except TypeError:
                    options.pop("easy_drag", None)
            if window is None:
                raise RuntimeError(text("Не удалось открыть окно модуля", "Could not open the module window"))
            self.windows[module_id] = window
            if controller is not None:
                self.appbars[module_id] = controller
        window.events.closing += lambda *_a, m=module_id: self._on_closing(m)
        window.events.closed += lambda *_a, m=module_id: self._on_closed(m)
        threading.Thread(target=self._style_and_show, args=(module_id, window, title, dark),
                         name="MemuAI module window", daemon=True).start()
        self.notify_main()
        return {"ok": True, "native": True}

    def _style_and_show(self, module_id: str, window, title: str, dark: bool) -> None:
        """Taskbar identity + icon BEFORE the window is first shown (no regrouping flash)."""
        hwnd = 0

        deadline = time.monotonic() + (8 if sys.platform == "win32" else 0)
        form = getattr(window, "native", None)
        while time.monotonic() < deadline and form is None:
            form = getattr(window, "native", None)
            if form is None:
                time.sleep(0.05)
        if form is not None:
            try:
                from .win_titlebar import winforms_ui_invoker
                hwnd = int(winforms_ui_invoker(form)(lambda: int(form.Handle.ToInt64())) or 0)
            except Exception:
                hwnd = 0
            try:
                from .desktop import grant_local_permissions
                grant_local_permissions(form, self.base_url)
            except Exception:
                pass

            module_appbar.attach(self.appbars.get(module_id), form)
        if hwnd:
            self.hwnds[module_id] = hwnd
            ico = shell_icon(module_id, self.root)

            if not ico.is_file() or module_id not in load_store(self.root)["apps"]:
                try:
                    ico = Path(write_icon(module_id, _module_row(module_id, self.root), "", self.root)["shell"])
                except Exception:
                    pass
            command = launch_command(module_id)["command"]
            self.win.set_window_app_id(hwnd, app_id(module_id), command, title, str(ico) if ico.is_file() else "")
            self._apply_icon(module_id, hwnd, str(ico))

            if getattr(self.appbars.get(module_id), "native_theme", None) is None:
                self.win.set_dark_caption(hwnd, dark)
        try:
            window.show()
        except Exception as exc:  # noqa: BLE001
            log.warning("module window %s not shown: %s", module_id, exc)
        if hwnd:
            self.win.foreground(hwnd)

    def focus(self, module_id: str) -> bool:
        window = self.windows.get(module_id)
        if window is None:
            return False
        try:
            window.show()
            window.restore()
        except Exception:
            pass
        return bool(self.win.foreground(self.hwnds.get(module_id, 0))) or True

    def minimize(self, module_id: str) -> bool:
        window = self.windows.get(module_id)
        if window is None:
            return False
        try:
            window.minimize()
            return True
        except Exception:
            return False

    def close(self, module_id: str, force: bool = False) -> bool:
        window = self.windows.get(module_id)
        if window is None:
            return False
        if force:
            self._force_close.add(module_id)
        try:
            window.destroy()
        except Exception as exc:  # noqa: BLE001
            log.warning("module window %s not closed: %s", module_id, exc)
            return False
        return True

    def _apply_icon(self, module_id: str, hwnd: int, ico: str) -> None:
        """``WM_SETICON`` at the monitor's icon sizes, and the WinForms ``Form.Icon`` too: the form re-sends its own
        icon whenever its handle is re-created (pywebview starts every form with the MemuAI.exe icon)."""
        self.win.set_window_icon(hwnd, ico)
        form = getattr(self.windows.get(module_id), "native", None)
        if form is None or not Path(ico).is_file():
            return
        try:
            from System.Drawing import Icon  # type: ignore[import-not-found]
            from .win_titlebar import winforms_ui_invoker
            winforms_ui_invoker(form)(lambda: setattr(form, "Icon", Icon(ico)))
        except Exception:  # noqa: BLE001 — not WinForms (tests, other GUIs): WM_SETICON is enough
            pass

    def refresh_icon(self, module_id: str) -> None:
        """The module's icon changed (Round 39/40): the open window, its taskbar button and the icon a pinned
        button relaunches with take the new ``.ico``."""
        hwnd = self.hwnds.get(module_id, 0)
        ico = shell_icon(module_id, self.root)
        if not hwnd or not ico.is_file():
            return
        try:
            title = _title(_module_row(module_id, self.root))
        except KeyError:
            title = ""
        if title:
            self.win.set_window_app_id(hwnd, app_id(module_id), launch_command(module_id)["command"], title, str(ico))
        self._apply_icon(module_id, hwnd, str(ico))

    def apply_appbar(self) -> None:
        """Round 38: the switch changed — re-read it for every open module window (caption hook + page)."""
        from .win_titlebar import MODE_CUSTOM, MODE_NATIVE
        store = load_store(self.root)
        for module_id, controller in list(self.appbars.items()):
            try:
                controller.set_mode(MODE_CUSTOM if module_appbar.enabled(store, module_id) else MODE_NATIVE)
            except Exception as exc:  # noqa: BLE001
                log.warning("module appbar %s: %s", module_id, exc)
            self._eval_window(module_id, "window.MemuModuleAppbar && MemuModuleAppbar.refresh()")

    def report(self, module_id: str, state: Dict[str, Any]) -> None:
        self.window_state[module_id] = {"busy": bool(state.get("busy")), "unsaved": bool(state.get("unsaved")),
                                        "width": int(state.get("width") or 0), "height": int(state.get("height") or 0)}

    def _on_closing(self, module_id: str) -> bool:
        if self.quitting or module_id in self._force_close:
            return True
        state = self.window_state.get(module_id) or {}
        if state.get("busy") or state.get("unsaved"):

            self._eval_window(module_id, "window.MemuModuleApps && MemuModuleApps.askWindowClose()")
            return False
        return True

    def _on_closed(self, module_id: str) -> None:
        state = self.window_state.pop(module_id, {}) or {}
        with self._lock:
            self.windows.pop(module_id, None)
            self.hwnds.pop(module_id, None)
            self.appbars.pop(module_id, None)
            self._force_close.discard(module_id)
            remaining = len(self.windows)
        if state.get("width") and state.get("height"):
            try:
                remember_size(module_id, state["width"], state["height"], self.root)
            except Exception:
                pass
        if self.quitting:
            return
        self.notify_main()
        if not remaining and self.module_only and not self.main_shown:

            threading.Thread(target=self.quit_cb, name="MemuAI module app exit", daemon=True).start()

    def destroy_all(self) -> None:
        """Quit hook: the app is exiting — module windows go without questions."""
        self.quitting = True
        for module_id in list(self.windows):
            try:
                self.windows[module_id].destroy()
            except Exception:
                pass

    def report_main(self, modules: List[Dict[str, Any]]) -> None:
        clean = []
        for row in modules or []:
            try:
                module_id = modules_host._safe_id(row.get("id"))
            except (ValueError, AttributeError):
                continue
            clean.append({"id": module_id, "busy": bool(row.get("busy")), "unsaved": bool(row.get("unsaved")),
                          "minimized": bool(row.get("minimized")),
                          "width": int(row.get("width") or 0), "height": int(row.get("height") or 0)})
        self.main_modules = clean[:12]

    def main_close_guard(self) -> bool:
        """pywebview ``closing`` of the main window. ``True`` = cancel (the page decides)."""
        if self.quitting:
            return False
        if self._allow_main_close:
            self._allow_main_close = False
            return False
        modules = list(self.main_modules)
        if not modules:
            return False
        choice = load_store(self.root)["on_app_close"]
        risky = any(row["busy"] or row["unsaved"] for row in modules)
        if choice == "keep" and not risky and self.supported():
            self.keep(modules)
            return False
        if choice == "close" and not risky:
            self._eval_main("window.MemuModuleApps && MemuModuleApps.closeInApp()")
            return False
        self._eval_main("window.MemuModuleApps && MemuModuleApps.askAppClose()")
        return True

    def keep(self, modules: List[Dict[str, Any]]) -> List[str]:
        """Move the main window's modules to their own windows (sizes kept)."""
        opened = []
        for row in modules:
            try:
                self.open(row["id"], row.get("width") or 0, row.get("height") or 0)
                opened.append(row["id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("module %s not moved to its own window: %s", row.get("id"), exc)
        self.main_modules = [row for row in self.main_modules if row["id"] not in opened]
        self._eval_main("window.MemuModuleApps && MemuModuleApps.movedOut(%s)" % json.dumps(opened))
        return opened

    def proceed_main_close(self) -> None:
        """The page answered «close»/«keep»: close the main window the usual way (tray or quit)."""
        self._allow_main_close = True
        try:
            self.close_main_cb()
        finally:
            self._allow_main_close = False

    def supported(self) -> bool:
        """Native windows can be opened (the manager only exists inside the desktop shell)."""
        return not self.quitting

MANAGER: Optional[ModuleWindows] = None

def manager(app=None) -> Optional[ModuleWindows]:
    if app is not None:
        found = getattr(getattr(app, "state", None), "module_windows", None)
        if found is not None:
            return found
    return MANAGER

try:
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
except Exception:  # pragma: no cover - the desktop helpers work without FastAPI
    Request = FileResponse = JSONResponse = None  # type: ignore

def register(app) -> None:

    def err(message: str, status: int = 400):
        return JSONResponse({"error": message}, status)

    async def body(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def state_payload() -> Dict[str, Any]:
        store = load_store()
        known = {row["id"]: row for row in modules_host.list_modules(modules_host.default_root())}
        mgr = manager(app)
        apps = [describe(module_id) for module_id in store["apps"] if module_id in known]
        return {"apps": apps, "on_app_close": store["on_app_close"],
                "custom_appbar": store["custom_appbar"], "appbar": store["appbar"],
                "native": bool(mgr is not None), "shortcuts_supported": SHELL.available(),
                "open_windows": mgr.open_ids() if mgr else [],
                "module_only": bool(mgr and mgr.module_only and not mgr.main_shown)}

    @app.get("/api/pg/module-apps")
    async def module_apps_state():
        return state_payload()

    @app.post("/api/pg/module-apps/install")
    async def module_apps_install(request: Request):
        data = await body(request)
        try:
            result = install(str(data.get("id") or ""), desktop=bool(data.get("desktop", True)),
                             start_menu=bool(data.get("start_menu", True)), hide=bool(data.get("hide", False)),
                             icon_png=str(data.get("icon_png") or ""))
        except KeyError as exc:
            return err(str(exc.args[0]) if exc.args else text("Модуль не найден", "Module not found"), 404)
        except ValueError as exc:
            return err(str(exc))
        except OSError as exc:
            return err(str(exc), 500)
        return {"ok": True, "app": result, **state_payload()}

    @app.post("/api/pg/module-apps/update")
    async def module_apps_update(request: Request):
        data = await body(request)
        try:
            result = update(str(data.get("id") or ""), hide=data.get("hide") if "hide" in data else None,
                            desktop=data.get("desktop") if "desktop" in data else None,
                            start_menu=data.get("start_menu") if "start_menu" in data else None)
        except KeyError as exc:
            return err(str(exc.args[0]) if exc.args else "not found", 404)
        except ValueError as exc:
            return err(str(exc))
        return {"ok": True, "app": result, **state_payload()}

    @app.post("/api/pg/module-apps/uninstall")
    async def module_apps_uninstall(request: Request):
        data = await body(request)
        try:
            uninstall(str(data.get("id") or ""), unhide=bool(data.get("unhide", True)))
        except ValueError as exc:
            return err(str(exc))
        return {"ok": True, **state_payload()}

    @app.post("/api/pg/module-apps/settings")
    async def module_apps_settings(request: Request):
        data = await body(request)
        try:
            set_close_choice(str(data.get("on_app_close") or ""))
        except ValueError as exc:
            return err(str(exc))
        return {"ok": True, **state_payload()}

    @app.post("/api/pg/module-apps/appbar")
    async def module_apps_appbar(request: Request):
        """Round 38: {enabled, id?} — the custom title bar for one module app (id) or for all of them."""
        data = await body(request)
        try:
            set_appbar(bool(data.get("enabled", True)), str(data.get("id") or ""))
        except ValueError as exc:
            return err(str(exc))
        mgr = manager(app)
        if mgr is not None:
            mgr.apply_appbar()
        return {"ok": True, **state_payload()}

    @app.get("/api/pg/module-apps/icon/{name}")
    async def module_apps_icon(name: str):
        match = re.fullmatch(r"([a-z0-9][a-z0-9_-]{0,63})\.png", name or "")
        if not match:
            return err("bad name", 404)
        path = app_dir() / f"{match.group(1)}.png"
        if not path.is_file():
            return err("no icon", 404)
        return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.post("/api/pg/module-apps/icon-sync")
    async def module_apps_icon_sync(request: Request):
        """Round 40: {id, icon_png} — the page's picture of an installed app's icon (once per start): an app whose
        ``.ico`` is not its module's current icon gets a new one — shortcuts, pinned buttons, the open window."""
        data = await body(request)
        png = str(data.get("icon_png") or "")
        try:
            module_id = modules_host._safe_id(str(data.get("id") or ""))
        except ValueError as exc:
            return err(str(exc))
        entry = load_store()["apps"].get(module_id)
        if not entry:
            return err(text("Модуль не установлен как приложение", "The module is not installed as an app"), 404)
        from .module_icons import app_icon_source
        if app_icon_source(_root(), module_id) is not None or decode_png(png) is None:
            changed = icon_outdated(module_id, entry)
        else:
            changed = entry.get("icon_sig") != page_signature(png) or not shell_icon(module_id).is_file()
        if changed:
            try:
                await asyncio.to_thread(refresh_app_icon, module_id, png)
            except (KeyError, OSError) as exc:
                return err(str(exc.args[0]) if exc.args else "icon", 500)
            mgr = manager(app)
            if mgr is not None:
                mgr.refresh_icon(module_id)
        return {"ok": True, "changed": bool(changed)}

    def open_module(module_id: str, width: int = 0, height: int = 0, from_launch: bool = False):
        mgr = manager(app)
        try:
            module_id = modules_host._safe_id(module_id)
            row = _module_row(module_id)
        except (ValueError, KeyError) as exc:
            return err(str(exc.args[0]) if exc.args else "not found", 404)
        if mgr is None:

            return {"ok": True, "native": False, "url": f"/?module_app={module_id}"}
        if from_launch and any(item["id"] == module_id for item in mgr.main_modules):

            mgr._eval_main("window.MemuModuleApps && MemuModuleApps.popOut(%s)" % json.dumps(module_id))
            return {"ok": True, "native": True, "moved": True}
        try:
            if not row.get("enabled"):
                modules_host.configure_module(modules_host.default_root(), module_id, enabled=True)
            return mgr.open(module_id, width, height)
        except PermissionError as exc:
            return err(str(exc), 403)
        except Exception as exc:  # noqa: BLE001
            log.warning("module window %s: %s", module_id, exc)
            return err(text("Не удалось открыть окно модуля", "Could not open the module window"), 500)

    @app.post("/api/pg/module-apps/open")
    async def module_apps_open(request: Request):
        data = await body(request)
        return open_module(str(data.get("id") or ""), int(data.get("width") or 0), int(data.get("height") or 0))

    @app.post("/api/pg/module-apps/launch")
    async def module_apps_launch(request: Request):
        """IPC from a second ``MemuAI.exe --module <id>`` (shortcut / pinned button)."""
        data = await body(request)
        return open_module(str(data.get("id") or ""), from_launch=True)

    @app.post("/api/pg/module-apps/state")
    async def module_apps_report(request: Request):
        data = await body(request)
        mgr = manager(app)
        if mgr is None:
            return {"ok": True, "native": False}
        if data.get("where") == "main":
            mgr.report_main(data.get("modules") if isinstance(data.get("modules"), list) else [])
        else:
            try:
                mgr.report(modules_host._safe_id(data.get("id")), data)
            except ValueError as exc:
                return err(str(exc))
        return {"ok": True, "native": True}

    @app.post("/api/pg/module-apps/app-close")
    async def module_apps_app_close(request: Request):
        """The page answered the «MemuAI is closing, modules are open» question."""
        data = await body(request)
        action = str(data.get("action") or "")
        if action not in ("keep", "close", "cancel"):
            return err(text("Неизвестное действие", "Unknown action"))
        if data.get("remember") and action in ("keep", "close"):
            set_close_choice(action)
        mgr = manager(app)
        if mgr is None or action == "cancel":
            return {"ok": True, "moved": []}
        moved: List[str] = []
        if action == "keep":
            modules = [row for row in mgr.main_modules
                       if not data.get("ids") or row["id"] in data.get("ids")]
            moved = mgr.keep(modules)
        threading.Thread(target=mgr.proceed_main_close, name="MemuAI close", daemon=True).start()
        return {"ok": True, "moved": moved}

    @app.post("/api/pg/module-apps/window")
    async def module_apps_window(request: Request):
        """From a module window page: close it (after its own question), show MemuAI, or dock back."""
        data = await body(request)
        action = str(data.get("action") or "")
        mgr = manager(app)
        if mgr is None:
            return {"ok": True, "native": False}
        try:
            module_id = modules_host._safe_id(data.get("id"))
        except ValueError as exc:
            return err(str(exc))
        if action == "close":
            mgr.close(module_id, force=bool(data.get("force")))
        elif action == "main":
            mgr.main_shown = True
            mgr.show_main_cb()
        elif action == "dock":

            mgr.main_shown = True
            mgr.show_main_cb()
            mgr.close(module_id, force=True)
            mgr._eval_main("window.MemuModules && MemuModules.open(%s, {docked: true})" % json.dumps(module_id))
        elif action == "focus":
            mgr.focus(module_id)
        elif action == "minimize":
            mgr.minimize(module_id)
        else:
            return err(text("Неизвестное действие", "Unknown action"))
        return {"ok": True, "native": True}

    if sys.platform == "win32":
        threading.Thread(target=lambda: _safe_repair(), name="MemuAI module apps repair", daemon=True).start()

def _safe_repair() -> None:
    try:
        time.sleep(4)
        repair()
    except Exception as exc:  # noqa: BLE001
        log.warning("module apps repair failed: %s", exc)
