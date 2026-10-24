"""Taskbar identity of the MemuAI windows: AppUserModelID, window icons, shortcut icons (Round 33).

Why MemuAI's taskbar button sometimes turned into a white document
------------------------------------------------------------------
The shell draws its generic "white page" when the icon it resolved for a
taskbar entry cannot be read. Before Round 33 MemuAI produced that state in
several ways (all fixed here and in ``app/assistant_icon.py``):

1. ``POST /api/window/icon`` ran on *every* preference change: it rewrote
   ``%APPDATA%\\MemuAI\\memuai\\assistant.ico`` in place (non-atomically), had
   PowerShell rewrite the IconLocation of the Start-menu/desktop shortcuts
   to that file and broadcast ``SHCNE_ASSOCCHANGED`` on the first call of every
   process. Explorer reading the file while it was being written cached a
   broken icon for the shortcut — the pinned button showed a white page until
   the icon cache was rebuilt.
2. The shortcuts kept pointing at that data file. When it disappeared (data
   reset, another data folder, uninstall + reinstall — the installer recreates
   its own shortcuts but never the copy Windows made when the user pinned
   MemuAI) every pinned entry lost its icon for good.
3. ``SetCurrentProcessExplicitAppUserModelID`` was called again from the
   "shown" hook of the main window (also after every restore from the tray),
   although Windows requires it before any UI exists; the window had no
   per-window identity or relaunch icon, so an entry pinned from the running
   window had no icon resource to fall back on.
4. ``WM_SETICON`` got 64-bit ``HICON`` values through ctypes without argtypes
   (truncated or rejected on some handle values), only one 32-px "big" icon
   for every DPI, and the window class icon of *all* WinForms windows of the
   process (Quick Access, stickers, the companion) was replaced as well.
5. Pillow's .ico files contain PNG frames only; small PNG frames are read
   blank by some shell code paths. Our .ico files are now BMP below 256 px
   (``build_memuai_icon.write_ico``).
6. After an update that changes the exe icon (Round 33 does) Explorer keeps
   the old icon cached for the shortcuts; the installer now refreshes them
   with ``SHChangeNotify(SHCNE_UPDATEITEM)`` + ``SHCNE_ASSOCCHANGED``.

Everything here is guarded: on other systems every function is a no-op that
returns ``False``/``None``. The Win32 surface is the small ``Win32Api`` class,
which the tests replace with a fake (see tests/round33_icons_test.py).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("memuai.identity")

APP_ID_MEMUAI = "MemuAI.Desktop"

WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50
SHCNE_UPDATEITEM = 0x00002000
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST, SHCNF_PATHW, SHCNF_FLUSHNOWAIT = 0x0000, 0x0005, 0x3000

#: PKEY_AppUserModel_* (propkey.h), fmtid {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}.
PKEY_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
PID_RELAUNCH_COMMAND, PID_RELAUNCH_ICON, PID_RELAUNCH_NAME, PID_ID = 2, 3, 4, 5


def is_windows() -> bool:
    return sys.platform == "win32"


# --------------------------------------------------------------------- Win32 API
class Win32Api:
    """The handful of Win32 calls used here, with explicit 64-bit-safe signatures."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.wintypes = wintypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        self.user32, self.shell32 = user32, shell32
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        self._metrics_for_dpi = getattr(user32, "GetSystemMetricsForDpi", None)
        if self._metrics_for_dpi is not None:
            self._metrics_for_dpi.argtypes = [ctypes.c_int, wintypes.UINT]
            self._metrics_for_dpi.restype = ctypes.c_int
        self._dpi_for_window = getattr(user32, "GetDpiForWindow", None)
        if self._dpi_for_window is not None:
            self._dpi_for_window.argtypes = [wintypes.HWND]
            self._dpi_for_window.restype = wintypes.UINT
        shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        shell32.SHChangeNotify.restype = None
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long

    # -- icons
    def icon_sizes(self, hwnd: int) -> Tuple[int, int]:
        """(small, big) icon sizes for the window's monitor DPI."""
        dpi = 0
        if self._dpi_for_window is not None and hwnd:
            try:
                dpi = int(self._dpi_for_window(hwnd) or 0)
            except Exception:  # noqa: BLE001
                dpi = 0
        if dpi and self._metrics_for_dpi is not None:
            return (int(self._metrics_for_dpi(SM_CXSMICON, dpi) or 16),
                    int(self._metrics_for_dpi(SM_CXICON, dpi) or 32))
        return int(self.user32.GetSystemMetrics(SM_CXSMICON) or 16), int(self.user32.GetSystemMetrics(SM_CXICON) or 32)

    def tray_icon_size(self) -> int:
        return int(self.user32.GetSystemMetrics(SM_CXSMICON) or 16)

    def load_icon(self, path: str, size: int) -> int:
        return int(self.user32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE) or 0)

    def set_icon(self, hwnd: int, which: int, handle: int) -> None:
        self.user32.SendMessageW(hwnd, WM_SETICON, which, handle)

    def destroy_icon(self, handle: int) -> None:
        if handle:
            self.user32.DestroyIcon(handle)

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self.user32.IsWindow(hwnd))

    # -- shell
    def set_process_app_id(self, app_id: str) -> bool:
        return self.shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0

    def notify_path_updated(self, path: str) -> None:
        buffer = self.ctypes.create_unicode_buffer(str(path))
        self.shell32.SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW | SHCNF_FLUSHNOWAIT,
                                    self.ctypes.cast(buffer, self.ctypes.c_void_p), None)

    def notify_assoc_changed(self) -> None:
        self.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSHNOWAIT, None, None)

    # -- COM: window property store and shell links
    def set_window_properties(self, hwnd: int, props: Sequence[Tuple[int, str]]) -> bool:
        """``SHGetPropertyStoreForWindow`` + ``IPropertyStore::SetValue`` (VT_LPWSTR) + ``Commit``."""
        from . import win_com
        return win_com.set_window_properties(hwnd, PKEY_FMTID, props)

    def read_shortcut(self, link: str) -> Optional[Tuple[str, str, int]]:
        from . import win_com
        return win_com.read_shortcut(link)

    def write_shortcut_icon(self, link: str, icon: str, index: int) -> bool:
        from . import win_com
        return win_com.write_shortcut_icon(link, icon, index)


_api: Optional[object] = None
_api_lock = threading.Lock()


def api():
    """The process-wide :class:`Win32Api` (``None`` off Windows or when ctypes fails)."""
    global _api
    if _api is not None:
        return _api
    if not is_windows():
        return None
    with _api_lock:
        if _api is None:
            try:
                _api = Win32Api()
            except Exception as exc:  # noqa: BLE001
                log.debug("Win32 API unavailable: %s", exc)
                return None
    return _api


def set_api(value) -> None:
    """Tests: install a fake API (``None`` restores the real one lazily)."""
    global _api, _process_app_id
    _api = value
    _process_app_id = ""
    _window_identity.clear()


# --------------------------------------------------------------- process identity
_process_app_id = ""


def set_process_app_id(app_id: str = APP_ID_MEMUAI) -> bool:
    """Set the explicit AppUserModelID once, before any window exists.

    Repeated calls with the same id are no-ops: Windows wants this call
    before the first window, and re-applying it later regroups the taskbar.
    """
    global _process_app_id
    if not app_id:
        return False
    if _process_app_id == app_id:
        return True
    win = api()
    if win is None:
        return False
    try:
        if win.set_process_app_id(app_id):
            _process_app_id = app_id
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("SetCurrentProcessExplicitAppUserModelID failed: %s", exc)
    return False


def process_app_id() -> str:
    return _process_app_id


def relaunch_command() -> str:
    """The command a pinned taskbar entry starts (only meaningful for the frozen exe)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return ""


def set_window_identity(hwnd: int, app_id: str, display_name: str, icon_resource: str,
                        command: Optional[str] = None) -> bool:
    """Give one window the app's identity and relaunch data (icon for pinning).

    The relaunch properties are only written for the frozen exe — a pinned
    ``python.exe`` without the script would start nothing useful.
    """
    win = api()
    if win is None or not hwnd or not app_id:
        return False
    command = relaunch_command() if command is None else command
    props: List[Tuple[int, str]] = []
    if command:
        props += [(PID_RELAUNCH_COMMAND, command), (PID_RELAUNCH_NAME, display_name)]
        if icon_resource:
            props.append((PID_RELAUNCH_ICON, icon_resource))
    # The id goes last: setting it makes the taskbar re-read the others.
    props.append((PID_ID, app_id))
    key = (int(hwnd), tuple(props))
    if _window_identity.get(int(hwnd)) == key:
        return True     # "shown" fires after every restore: never re-announce the same identity
    try:
        ok = bool(win.set_window_properties(hwnd, props))
    except Exception as exc:  # noqa: BLE001
        log.debug("window identity not set: %s", exc)
        return False
    if ok:
        _window_identity[int(hwnd)] = key
    return ok


_window_identity: Dict[int, tuple] = {}


# ------------------------------------------------------------------ window icons
class WindowIcons:
    """Keeps the HICONs given to windows alive until they are replaced.

    A handle is destroyed only after the window has received its successor;
    destroying the icon a window still shows is one way to get the white page.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[int, Tuple[str, float, Tuple[int, int], Tuple[int, int]]] = {}

    def apply(self, hwnd: int, ico_path: str, force: bool = False) -> bool:
        win = api()
        if win is None or not hwnd or not ico_path or not os.path.isfile(ico_path):
            return False
        try:
            if not win.is_window(hwnd):
                return False
            mtime = os.path.getmtime(ico_path)
            sizes = win.icon_sizes(hwnd)
            with self._lock:
                previous = self._state.get(hwnd)
                if (not force and previous and previous[0] == os.path.normcase(ico_path)
                        and previous[1] == mtime and previous[2] == sizes):
                    return True
                small = win.load_icon(ico_path, sizes[0])
                big = win.load_icon(ico_path, sizes[1])
                if not small or not big:
                    for handle in (small, big):
                        win.destroy_icon(handle)
                    return False
                win.set_icon(hwnd, ICON_SMALL, small)
                win.set_icon(hwnd, ICON_BIG, big)
                self._state[hwnd] = (os.path.normcase(ico_path), mtime, sizes, (small, big))
                if previous:
                    for handle in previous[3]:
                        if handle not in (small, big):
                            win.destroy_icon(handle)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("window icon not set: %s", exc)
            return False

    def current(self, hwnd: int) -> str:
        entry = self._state.get(hwnd)
        return entry[0] if entry else ""

    def forget(self, hwnd: int) -> None:
        """The window is gone: free its icons."""
        win = api()
        with self._lock:
            entry = self._state.pop(hwnd, None)
        if entry and win is not None:
            for handle in entry[3]:
                try:
                    win.destroy_icon(handle)
                except Exception:  # noqa: BLE001
                    pass


WINDOW_ICONS = WindowIcons()


def native_hwnd(window) -> int:
    """HWND of a pywebview window on WinForms (0 when unknown)."""
    if window is None or not is_windows():
        return 0
    try:
        from webview.platforms.winforms import BrowserView
        native = BrowserView.instances.get(window.uid)
        if native is not None:
            return int(native.Handle.ToInt64())
    except Exception:  # noqa: BLE001
        pass
    try:
        native = getattr(window, "native", None)
        if native is not None:
            return int(native.Handle.ToInt64())
    except Exception:  # noqa: BLE001
        pass
    return 0


def bundled_file(name: str) -> str:
    """A file shipped next to the code (PyInstaller ``_MEIPASS`` or the repo root)."""
    for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if base:
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                return candidate
    return ""


# -------------------------------------------------------------------- shell icons
def refresh_shell_icons(paths: Iterable[str] = (), assoc: bool = True) -> int:
    """Tell Explorer that these files' icons changed (and, optionally, re-read all icons).

    Returns how many notifications were sent (0 off Windows).
    """
    win = api()
    if win is None:
        return 0
    sent = 0
    for path in paths:
        if not path:
            continue
        try:
            win.notify_path_updated(str(path))
            sent += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("SHChangeNotify(%s) failed: %s", path, exc)
    if assoc:
        try:
            win.notify_assoc_changed()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("SHChangeNotify(ASSOCCHANGED) failed: %s", exc)
    return sent


def shortcut_folders(env: Optional[Dict[str, str]] = None) -> List[Path]:
    """Where MemuAI shortcuts live: Start menu, desktop, and the copies Windows makes when pinning."""
    env = dict(os.environ if env is None else env)
    appdata = env.get("APPDATA", "")
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    folders: List[Path] = []
    if appdata:
        programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        pinned = Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned"
        folders += [programs / "MemuAI", programs, pinned / "TaskBar", pinned / "StartMenu"]
    if home:
        folders += [Path(home) / "Desktop", Path(home) / "OneDrive" / "Desktop"]
    return folders


def _basename(path: str) -> str:
    """File name of a Windows or POSIX path, case-folded."""
    return os.path.normcase(str(path).replace("\\", "/").rsplit("/", 1)[-1]).lower()


def _is_ours(icon: str, owned_dirs: Sequence[str], exe_name: str) -> bool:
    """May we change a shortcut whose icon is ``icon``? Never touch an icon the user picked."""
    if not icon:
        return True
    norm = os.path.normcase(os.path.abspath(icon))
    if _basename(icon) == exe_name.lower():
        return True
    if any(norm.startswith(os.path.normcase(os.path.abspath(d)) + os.sep) for d in owned_dirs if d):
        return True
    return not os.path.isfile(icon)   # a broken icon is always repaired


def sync_shortcut_icons(desired_icon: str, exe_name: str = "MemuAI.exe",
                        owned_dirs: Sequence[str] = (), folders: Optional[Sequence[Path]] = None) -> List[str]:
    """Point every MemuAI shortcut at ``desired_icon`` (``""`` = the exe's own icon).

    Only shortcuts whose target is ``exe_name`` are considered, and only when
    their current icon is empty, the exe, one of our files (``owned_dirs``) or
    missing. Returns the changed shortcut paths (Explorer is notified).
    """
    win = api()
    if win is None:
        return []
    changed: List[str] = []
    for folder in (shortcut_folders() if folders is None else folders):
        try:
            links = sorted(p for p in Path(folder).glob("*.lnk") if p.is_file())
        except OSError:
            continue
        for link in links:
            try:
                info = win.read_shortcut(str(link))
                if not info:
                    continue
                target, icon, index = info
                if _basename(target) != exe_name.lower():
                    continue
                if not _is_ours(icon, owned_dirs, exe_name):
                    continue
                want = desired_icon or target
                if os.path.normcase(icon or target) == os.path.normcase(want) and index == 0:
                    continue
                if desired_icon == "" and not icon:
                    continue
                if win.write_shortcut_icon(str(link), want, 0):
                    changed.append(str(link))
            except Exception as exc:  # noqa: BLE001
                log.debug("shortcut %s: %s", link, exc)
    if changed:
        refresh_shell_icons(changed + ([desired_icon] if desired_icon else []))
    return changed
