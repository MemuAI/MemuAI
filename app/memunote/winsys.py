"""Windows integration of MemuNote (all guarded: no-ops or harmless fallbacks elsewhere).

* "Open with → MemuNote" (Round 31: opt-in, never pushy). Per user (HKCU only, no admin):
  ``Software\\Classes\\Applications\\MemuNote.exe`` with ``SupportedTypes`` (MemuNote is a
  candidate in "Choose another app" for these types) and, for types that already have
  a default program, the ``MemuNote.Document`` ProgID in ``.<ext>\\OpenWithProgids``
  (the "Open with" submenu). Rules:

  - only :data:`SAFE_EXT` (text, Markdown, data, documents) — never a script or an
    executable (:data:`NEVER_EXT`: .bat/.cmd/.ps1/.vbs/.js/.exe/.reg/.sh/.py…);
  - never a default value of ``.<ext>``, never ``UserChoice``, never ``OpenWithList``;
  - no ``.<ext>`` key is created for a type without a default program (there the only
    ProgID would act as the default), so MemuNote never takes over an unassociated type;
  - only written when the user turns it on (MemuNote or MemuAI settings) or runs
    ``--register-open-with``; ``--unregister-open-with`` (the uninstaller) removes it all.

  Rounds 29–30 registered ``MemuNote.Document`` for 60 types, scripts included, and
  created ``.<ext>`` keys for types without a program (MemuNote became the de-facto
  handler of ``.bat``/``.ps1`` on some systems). :func:`migrate` (first start of 3.5,
  marker ``Software\\MemuAI\\MemuNote\\AssocVersion``) removes all of that, including a
  ``UserChoice`` that points a script type at MemuNote, and re-registers the safe list
  only if the old registration was on.
* dark title bar (``DwmSetWindowAttribute`` 20/19) so the native caption matches the theme;
* bringing the window to the foreground for the single-instance hand-off;
* "Show in folder" (``explorer /select,``).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Dict, Iterable, List, Optional

log = logging.getLogger("memunote.winsys")

IS_WINDOWS = os.name == "nt"
APP_KEY = r"Software\Classes\Applications\MemuNote.exe"
PROGID = "MemuNote.Document"
PROGID_KEY = "Software\\Classes\\" + PROGID
STATE_KEY = r"Software\MemuAI\MemuNote"
ASSOC_VERSION = 2
FILE_EXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"

SAFE_EXT = [".txt", ".text", ".md", ".markdown", ".mdown", ".mkd", ".log", ".csv", ".tsv", ".json", ".jsonc",
            ".jsonl", ".ndjson", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".properties",
            ".rtf", ".docx", ".odt", ".pdf", ".epub", ".fb2", ".ipynb", ".srt", ".vtt", ".nfo", ".diff",
            ".patch", ".tex", ".rst", ".adoc", ".pptx", ".odp",

            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".psd", ".avif", ".heic", ".memuimg"]

NEVER_EXT = [".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml", ".vbs", ".vbe", ".js", ".jse", ".mjs", ".wsf",
             ".wsh", ".exe", ".com", ".scr", ".msi", ".msp", ".reg", ".sh", ".py", ".pyw", ".lnk", ".hta", ".cpl",
             ".msc", ".jar", ".url", ".inf", ".scf", ".pif", ".application", ".gadget", ".appref-ms", ".settingcontent-ms",
             ".html", ".htm", ".svg", ".xhtml", ".mht", ".mhtml"]

LEGACY_EXT = [".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".jsonc", ".xml", ".yaml", ".yml",
              ".ini", ".cfg", ".conf", ".toml", ".env", ".rtf", ".docx", ".odt", ".pdf", ".html", ".htm", ".css",
              ".js", ".mjs", ".ts", ".tsx", ".jsx", ".py", ".pyw", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
              ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat", ".cmd", ".ps1", ".lua", ".kt", ".swift",
              ".gitignore", ".srt", ".vtt", ".nfo", ".diff", ".patch", ".tex", ".rst", ".adoc", ".properties"]

OPEN_WITH_EXT = SAFE_EXT

assert not set(SAFE_EXT) & set(NEVER_EXT)

def safe_extensions(extensions: Optional[Iterable[str]] = None) -> List[str]:
    """*extensions* reduced to the safe list (lower-case, with a dot); the whole safe list for None."""
    if extensions is None:
        return list(SAFE_EXT)
    out: List[str] = []
    for ext in extensions:
        ext = str(ext or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if ext in SAFE_EXT and ext not in NEVER_EXT and ext not in out:
            out.append(ext)
    return out

def executable() -> str:
    """The MemuNote.exe of this installation (only frozen builds register themselves)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return ""

def reveal(path: str) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)], close_fds=True)   # noqa: S603,S607
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    for opener in ("xdg-open", "open"):
        try:
            subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
            return
        except OSError:
            continue

def _winreg():
    import winreg  # type: ignore
    return winreg

def _set(winreg, key: str, name: str, value, kind=None) -> None:
    handle = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(handle, name, 0, kind if kind is not None else winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(handle)

def _get(winreg, hive, key: str, name: str = ""):
    try:
        handle = winreg.OpenKey(hive, key)
    except OSError:
        return None
    try:
        return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return None
    finally:
        winreg.CloseKey(handle)

def _exists(winreg, hive, key: str) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(hive, key))
        return True
    except OSError:
        return False

def _delete_tree(winreg, key: str) -> None:
    try:
        handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return
    try:
        while True:
            try:
                child = winreg.EnumKey(handle, 0)
            except OSError:
                break
            _delete_tree(winreg, key + "\\" + child)
    finally:
        winreg.CloseKey(handle)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        pass

def _delete_value(winreg, key: str, name: str) -> bool:
    try:
        handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE)
    except OSError:
        return False
    try:
        winreg.DeleteValue(handle, name)
        return True
    except OSError:
        return False
    finally:
        winreg.CloseKey(handle)

def _is_empty(winreg, key: str) -> bool:
    try:
        handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        return False
    try:
        subkeys, values, _ = winreg.QueryInfoKey(handle)
        return subkeys == 0 and values == 0
    except OSError:
        return False
    finally:
        winreg.CloseKey(handle)

def _prune(winreg, key: str) -> None:
    """Delete *key* and then its parents up to ``Software\\Classes`` while they are empty."""
    while key.count("\\") >= 2 and key.lower() not in ("software\\classes", FILE_EXTS.lower()):
        if not _is_empty(winreg, key):
            return
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            return
        key = key.rsplit("\\", 1)[0]

def _values(winreg, key: str) -> List[tuple]:
    try:
        handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        return []
    out = []
    try:
        i = 0
        while True:
            try:
                out.append(winreg.EnumValue(handle, i))
            except OSError:
                break
            i += 1
    finally:
        winreg.CloseKey(handle)
    return out

def _has_default_program(winreg, ext: str) -> bool:
    """Does Windows already know a program for *ext* (ProgID default in HKCR, ignoring MemuNote's own)?"""
    value = _get(winreg, winreg.HKEY_CLASSES_ROOT, ext, "")
    return bool(value) and str(value) != PROGID

def _notify_shell() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:  # noqa: BLE001
        pass

def open_with_state(exe: str = "") -> Dict[str, object]:
    exe = exe or executable()
    if not IS_WINDOWS or not exe:
        return {"supported": False, "enabled": False, "extensions": list(SAFE_EXT)}
    try:
        winreg = _winreg()
        value = _get(winreg, winreg.HKEY_CURRENT_USER, APP_KEY + r"\shell\open\command", "")
        enabled = bool(value) and os.path.normcase(exe) in os.path.normcase(str(value))
        return {"supported": True, "enabled": enabled, "extensions": list(SAFE_EXT), "default": False}
    except OSError:
        return {"supported": True, "enabled": False, "extensions": list(SAFE_EXT), "default": False}

def register_open_with(enable: bool, exe: str = "", extensions: Optional[List[str]] = None,
                       winreg=None) -> Dict[str, object]:
    """Add (``enable``) or remove MemuNote as an *optional* "Open with" program. Never a default."""
    exe = exe or executable()
    if not IS_WINDOWS or not exe:
        return {"supported": False, "enabled": False}
    winreg = winreg or _winreg()
    if not enable:
        unregister(winreg)
        return {"supported": True, "enabled": False}
    exts = safe_extensions(extensions)
    unregister(winreg, notify=False)
    command = f'"{exe}" "%1"'
    _set(winreg, APP_KEY, "FriendlyAppName", "MemuNote")
    _set(winreg, APP_KEY + r"\DefaultIcon", "", f'"{exe}",0')
    _set(winreg, APP_KEY + r"\shell\open\command", "", command)
    for ext in exts:
        _set(winreg, APP_KEY + r"\SupportedTypes", ext, "")
    _set(winreg, PROGID_KEY, "", "MemuNote")
    _set(winreg, PROGID_KEY, "FriendlyTypeName", "MemuNote")
    _set(winreg, PROGID_KEY + r"\DefaultIcon", "", f'"{exe}",0')
    _set(winreg, PROGID_KEY + r"\shell\open\command", "", command)
    listed = []
    for ext in exts:

        if _has_default_program(winreg, ext):
            _set(winreg, rf"Software\Classes\{ext}\OpenWithProgids", PROGID, b"", winreg.REG_NONE)
            listed.append(ext)
    _set(winreg, STATE_KEY, "AssocVersion", str(ASSOC_VERSION))
    _notify_shell()
    return {"supported": True, "enabled": True, "extensions": exts, "open_with_progids": listed}

def unregister(winreg=None, extensions: Optional[List[str]] = None, notify: bool = True) -> None:
    """Remove every key MemuNote ever wrote for "Open with" (Rounds 29–31), leaving other programs alone."""
    if not IS_WINDOWS and winreg is None:
        return
    winreg = winreg or _winreg()
    _delete_tree(winreg, APP_KEY)
    _delete_tree(winreg, PROGID_KEY)
    names = set(LEGACY_EXT) | set(SAFE_EXT) | set(NEVER_EXT) | set(extensions or [])
    for ext in sorted(names):
        key = rf"Software\Classes\{ext}"
        if str(_get(winreg, winreg.HKEY_CURRENT_USER, key, "") or "") == PROGID:
            _delete_value(winreg, key, "")
        if _delete_value(winreg, key + r"\OpenWithProgids", PROGID):
            _prune(winreg, key + r"\OpenWithProgids")
        _delete_value(winreg, FILE_EXTS + "\\" + ext + r"\OpenWithProgids", PROGID)
    if notify:
        _notify_shell()

def forget_state(winreg=None) -> None:
    """Uninstall: also drop the migration marker (``Software\\MemuAI\\MemuNote``)."""
    if not IS_WINDOWS and winreg is None:
        return
    winreg = winreg or _winreg()
    _delete_tree(winreg, STATE_KEY)
    parent = STATE_KEY.rsplit("\\", 1)[0]
    if _is_empty(winreg, parent):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, parent)
        except OSError:
            pass

def _points_at_memunote(value) -> bool:
    text = str(value or "").lower()
    return text == PROGID.lower() or "memunote.exe" in text

def clean_script_choices(winreg=None) -> List[str]:
    """Drop a ``UserChoice`` / ``OpenWithList`` entry that points a script or executable type at MemuNote
    (Rounds 29–30 let the user click "Always" for .bat). Used by :func:`migrate` and by the uninstaller
    (``--unregister-open-with``): uninstalling a 3.5 that never started must not leave .bat pointing at a
    removed program. Document types keep the user's choice."""
    if not IS_WINDOWS and winreg is None:
        return []
    winreg = winreg or _winreg()
    fixed: List[str] = []
    for ext in sorted(set(LEGACY_EXT) | set(NEVER_EXT)):
        if ext in SAFE_EXT:
            continue
        base = FILE_EXTS + "\\" + ext
        choice = _get(winreg, winreg.HKEY_CURRENT_USER, base + r"\UserChoice", "ProgId")
        if _points_at_memunote(choice):
            _delete_tree(winreg, base + r"\UserChoice")
            fixed.append(ext)
        for name, value, _kind in _values(winreg, base + r"\OpenWithList"):
            if _points_at_memunote(value):
                _delete_value(winreg, base + r"\OpenWithList", name)
                fixed.append(ext)
    return sorted(set(fixed))

def migrate(winreg=None) -> Dict[str, object]:
    """One-time clean-up of the Round 29–30 registration (first start of 3.5). Cheap afterwards.

    Removes MemuNote from every script/executable type (``OpenWithProgids``, ``OpenWithList``,
    a ``UserChoice`` that points at MemuNote — Windows restores its own program), deletes the
    ``.<ext>`` keys it created, and re-registers the safe list only if "Open with" was on.
    """
    if not IS_WINDOWS and winreg is None:
        return {"migrated": False}
    try:
        winreg = winreg or _winreg()
    except ImportError:
        return {"migrated": False}
    try:
        done = int(str(_get(winreg, winreg.HKEY_CURRENT_USER, STATE_KEY, "AssocVersion") or 0))
    except ValueError:
        done = 0
    if done >= ASSOC_VERSION:
        return {"migrated": False}
    command = _get(winreg, winreg.HKEY_CURRENT_USER, APP_KEY + r"\shell\open\command", "")
    was_on = bool(command)
    exe = ""
    if was_on:
        text = str(command)
        exe = text.split('"')[1] if text.startswith('"') and text.count('"') >= 2 else text.split(" ")[0]
    unregister(winreg, notify=False)
    fixed = clean_script_choices(winreg)
    if was_on and exe:
        register_open_with(True, exe=exe, winreg=winreg)
    _set(winreg, STATE_KEY, "AssocVersion", str(ASSOC_VERSION))
    _notify_shell()
    return {"migrated": True, "reregistered": bool(was_on and exe), "fixed": sorted(set(fixed))}

WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

def webview2_version() -> str:
    """Installed WebView2 Runtime version ("" when absent) — the registry keys Microsoft documents.

    Without it pywebview silently falls back to the retired MSHTML (IE) engine,
    which cannot run MemuNote's page, so the caller opens the browser instead.
    """
    if not IS_WINDOWS:
        return ""
    try:
        winreg = _winreg()
    except ImportError:
        return ""
    keys = ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"))
    for hive, key in keys:
        try:
            with winreg.OpenKey(hive, key + "\\" + WEBVIEW2_CLIENT) as handle:
                value, _ = winreg.QueryValueEx(handle, "pv")
        except OSError:
            continue
        value = str(value or "").strip()
        if value and value != "0.0.0.0":
            return value
    return ""

def dark_titlebar(hwnd: int, dark: bool) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        from ..win_window_theme import apply_frame
        return apply_frame(hwnd, dark)
    except Exception as exc:  # noqa: BLE001
        log.debug("dark title bar: %s", exc)
    return False

def bring_to_front(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32")
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        ensure_on_screen(hwnd)
        user32.AllowSetForegroundWindow(-1)
        user32.SetForegroundWindow(hwnd)
    except Exception as exc:  # noqa: BLE001
        log.debug("foreground: %s", exc)

MIN_VISIBLE_W = 160
MIN_VISIBLE_H = 48

def monitor_work_areas() -> List[tuple]:
    """``[(left, top, right, bottom), …]`` of every monitor's work area ([] off Windows / on failure)."""
    if not IS_WINDOWS:
        return []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        areas: List[tuple] = []
        proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def collect(hmon, _hdc, _rect, _lp):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                areas.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
            return True

        user32.EnumDisplayMonitors(None, None, proc_type(collect), 0)
        return areas
    except Exception as exc:  # noqa: BLE001
        log.debug("monitors: %s", exc)
        return []

def rect_visible(x: int, y: int, width: int, areas: Iterable[tuple]) -> bool:
    """True when the window's caption strip (top ``MIN_VISIBLE_H`` px) is grabbable on some monitor."""
    for left, top, right, bottom in areas:
        vis_w = min(x + width, right) - max(x, left)
        vis_h = min(y + MIN_VISIBLE_H, bottom) - max(y, top)
        if vis_w >= min(MIN_VISIBLE_W, width) and vis_h >= MIN_VISIBLE_H // 2:
            return True
    return False

def normal_rect(hwnd: int) -> Optional[tuple]:
    """``(x, y, width, height)`` of the restored (non-minimised, non-maximised) window, via GetWindowPlacement."""
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                        ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                        ("rcNormalPosition", wintypes.RECT)]

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not ctypes.WinDLL("user32").GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
    except Exception as exc:  # noqa: BLE001
        log.debug("placement: %s", exc)
        return None

def ensure_on_screen(hwnd: int) -> bool:
    """Move a window that sits off every monitor to the centre of the primary work area. True if moved."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return False
        x, y, w, h = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        areas = monitor_work_areas()
        if not areas or rect_visible(x, y, w, areas):
            return False
        primary = next((a for a in areas if a[0] <= 0 < a[2] and a[1] <= 0 < a[3]), areas[0])
        left, top, right, bottom = primary
        w, h = min(w, right - left), min(h, bottom - top)
        nx, ny = left + (right - left - w) // 2, top + (bottom - top - h) // 2
        user32.SetWindowPos(wintypes.HWND(hwnd), None, nx, ny, w, h, 0x0004 | 0x0010)
        log.warning("window was off-screen at (%s,%s); moved to (%s,%s)", x, y, nx, ny)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("ensure on screen: %s", exc)
        return False
