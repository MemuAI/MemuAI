"""Round 33 (#26): the Windows side of «a module as its own app».

* ``create_shortcut`` — a Start menu / desktop ``.lnk`` through the shell's own
  ``IShellLinkW`` COM object (vtable calls with ctypes, the same technique as
  ``installer/winplat.py``: no PowerShell, no pywin32 — Defender-friendly) with
  ``System.AppUserModel.ID`` set, so the shortcut, a pinned taskbar button and
  the running module window are ONE application for the shell;
* ``set_window_app_id`` — the per-window ``System.AppUserModel.*`` properties
  (``SHGetPropertyStoreForWindow``): the module window gets its own taskbar
  button group, and pinning that button relaunches ``MemuAI.exe --module <id>``;
* ``set_window_icon`` — ``WM_SETICON`` with the module's ``.ico`` (DPI-sized frames);
* ``retarget_shortcut_icons`` / ``refresh_shell_icons`` — pinned / copied shortcuts follow a new ``.ico``,
  Explorer drops its cached picture (Round 40);
* ``known_folder`` — Start menu Programs / Desktop.

Everything is guarded: off Windows every helper returns ``False``/``None``.
The COM/ctypes plumbing is injected through ``api`` objects so the logic is
mock-tested on Linux (tests/round33_module_apps_test.py); the real calls need
real Windows (see changelog/round33 notes).
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("memuai.module_apps")

FOLDERS = {
    "programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
}

AUMID_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
PID_RELAUNCH_COMMAND = 2
PID_RELAUNCH_ICON = 3
PID_RELAUNCH_NAME = 4
PID_ID = 5
IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"

def available() -> bool:
    return sys.platform == "win32"

def _ct():
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class _PVUnion(ctypes.Union):
        _fields_ = [("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_ulonglong * 2)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("u", _PVUnion)]

    def guid(text: str):
        value = uuid.UUID(text)
        out = GUID()
        out.Data1, out.Data2, out.Data3 = value.time_low, value.time_mid, value.time_hi_version
        for i, byte in enumerate(value.bytes[8:]):
            out.Data4[i] = byte
        return out

    def method(obj, index: int, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        function = prototype(vtable[index])
        return lambda *args: function(obj, *args)

    def release(obj) -> None:
        if obj and obj.value:
            try:
                vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)
            except Exception:  # noqa: BLE001
                pass

    return ctypes, wintypes, GUID, PROPERTYKEY, PROPVARIANT, guid, method, release

def _set_string_props(ctypes, PROPERTYKEY, PROPVARIANT, guid, method, store, props: dict) -> None:
    """IPropertyStore::SetValue (vtable 6) for every pid → string, then Commit (7)."""
    keep = []
    for pid, text in props.items():
        if text is None:
            continue
        key = PROPERTYKEY(guid(AUMID_FMTID), pid)
        value = PROPVARIANT()
        if text == "":
            value.vt = 0
        else:
            value.vt = 31
            buffer = ctypes.create_unicode_buffer(str(text))
            keep.append(buffer)
            value.u.pwszVal = ctypes.cast(buffer, ctypes.c_wchar_p)
        method(store, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            ctypes.byref(key), ctypes.byref(value))
    method(store, 7)()

def known_folder(name: str) -> Optional[Path]:
    if not available() or name not in FOLDERS:
        return None
    try:
        ctypes, wintypes, GUID, _PK, _PV, guid, _m, _r = _ct()
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        out = ctypes.c_wchar_p()
        folder = guid(FOLDERS[name])
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out))
        try:
            if hr == 0 and out.value:
                return Path(out.value)
        finally:
            if out:
                ole32.CoTaskMemFree(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("known folder %s unavailable: %s", name, exc)
    return None

def create_shortcut(link: Path, target: str, args: str = "", workdir: str = "", icon: str = "",
                    description: str = "", app_id: str = "") -> bool:
    """Write ``link`` (a .lnk) pointing at ``target args`` with the AppUserModelID ``app_id``."""
    if not available():
        return False
    ctypes, wintypes, GUID, PROPERTYKEY, PROPVARIANT, guid, method, release = _ct()
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    initialized = ole32.CoInitializeEx(None, 0x2)
    link_obj, persist, store = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = guid("{00021401-0000-0000-C000-000000000046}")
        iid_link = guid("{000214F9-0000-0000-C000-000000000046}")
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid_link), ctypes.byref(link_obj))
        if hr < 0 or not link_obj.value:
            raise OSError(f"CoCreateInstance(ShellLink) failed: 0x{hr & 0xFFFFFFFF:08X}")
        method(link_obj, 20, wintypes.LPCWSTR)(str(target))
        if args:
            method(link_obj, 11, wintypes.LPCWSTR)(args)
        method(link_obj, 9, wintypes.LPCWSTR)(workdir or str(Path(target).parent))
        if description:
            method(link_obj, 7, wintypes.LPCWSTR)(description[:250])
        if icon:
            method(link_obj, 17, wintypes.LPCWSTR, ctypes.c_int)(icon, 0)
        query = method(link_obj, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        if app_id:
            try:
                query(ctypes.byref(guid(IID_PROPERTY_STORE)), ctypes.byref(store))
                _set_string_props(ctypes, PROPERTYKEY, PROPVARIANT, guid, method, store, {PID_ID: app_id})
            except OSError as exc:
                log.warning("AppUserModelID not set on %s: %s", link, exc)
        query(ctypes.byref(guid("{0000010b-0000-0000-C000-000000000046}")), ctypes.byref(persist))
        Path(link).parent.mkdir(parents=True, exist_ok=True)
        method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(str(link), True)
        return Path(link).exists()
    finally:
        for obj in (store, persist, link_obj):
            release(obj)
        if initialized in (0, 1):
            ole32.CoUninitialize()

def set_window_app_id(hwnd: int, app_id: str, relaunch_command: str = "", display_name: str = "",
                      icon: str = "") -> bool:
    """Give one window its own taskbar identity (and the command a pinned button runs)."""
    if not available() or not hwnd:
        return False
    try:
        ctypes, wintypes, GUID, PROPERTYKEY, PROPVARIANT, guid, method, release = _ct()
        shell32 = ctypes.windll.shell32
        shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID),
                                                        ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
        store = ctypes.c_void_p()
        hr = shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(guid(IID_PROPERTY_STORE)),
                                                 ctypes.byref(store))
        if hr < 0 or not store.value:
            return False
        try:
            props = {PID_ID: app_id}
            if relaunch_command and display_name:

                props.update({PID_RELAUNCH_COMMAND: relaunch_command, PID_RELAUNCH_NAME: display_name,
                              PID_RELAUNCH_ICON: icon or None})
            _set_string_props(ctypes, PROPERTYKEY, PROPVARIANT, guid, method, store, props)
            return True
        finally:
            release(store)
    except Exception as exc:  # noqa: BLE001
        log.warning("window AppUserModelID not set: %s", exc)
        return False

def set_window_icon(hwnd: int, ico_path: str) -> bool:
    """``WM_SETICON`` at the sizes of the window's monitor DPI (Round 40: 24/48 px frames at 150 %, not a
    16/32 px icon stretched by the taskbar), through the HICON keeper of ``win_app_identity``."""
    if not available() or not hwnd or not ico_path or not Path(ico_path).is_file():
        return False
    try:
        from .win_app_identity import WINDOW_ICONS
        return WINDOW_ICONS.apply(int(hwnd), str(ico_path), force=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("window icon not set: %s", exc)
        return False

def retarget_shortcut_icons(module_id: str, icon: str, icon_dir: Path, folders=None) -> list:
    """Round 40: every other shortcut of this module app — the ones Windows made when the app was pinned to the
    taskbar or Start, a copy the person made — still points at an older ``.ico`` of it (``<id>.ico`` under
    ``icon_dir``): re-point it at ``icon``. Shortcuts with any other icon are never touched."""
    if not available():
        return []
    from . import win_app_identity
    win = win_app_identity.api()
    if win is None:
        return []
    base = os.path.normcase(os.path.abspath(str(icon_dir))) + os.sep
    name = f"{module_id}.ico".lower()
    want = os.path.normcase(os.path.abspath(str(icon)))
    changed = []
    for folder in (win_app_identity.shortcut_folders() if folders is None else folders):
        try:
            links = sorted(p for p in Path(folder).glob("*.lnk") if p.is_file())
        except OSError:
            continue
        for link in links:
            try:
                info = win.read_shortcut(str(link))
                if not info or not info[1]:
                    continue
                current = os.path.normcase(os.path.abspath(info[1]))
                if current == want or not current.startswith(base) or os.path.basename(current).lower() != name:
                    continue
                if win.write_shortcut_icon(str(link), str(icon), 0):
                    changed.append(str(link))
            except Exception as exc:  # noqa: BLE001
                log.debug("shortcut %s: %s", link, exc)
    return changed

def refresh_shell_icons(paths) -> int:
    """``SHChangeNotify``: Explorer, Start and the taskbar re-read these icons (0 off Windows)."""
    if not available():
        return 0
    from .win_app_identity import refresh_shell_icons as notify
    return notify([p for p in paths if p])

def set_dark_caption(hwnd: int, dark: bool) -> bool:
    if not available() or not hwnd:
        return False
    try:
        from .win_window_theme import apply_frame
        return apply_frame(hwnd, dark)
    except Exception:  # noqa: BLE001
        pass
    return False

def foreground(hwnd: int) -> bool:
    if not available() or not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False
