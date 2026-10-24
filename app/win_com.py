"""Minimal shell COM calls through vtables with ctypes (Round 33; Windows only).

* ``set_window_properties`` — ``SHGetPropertyStoreForWindow`` → ``IPropertyStore``:
  the window's AppUserModelID and relaunch command / name / icon (what a
  taskbar entry pinned from the running window uses).
* ``read_shortcut`` / ``write_shortcut_icon`` — ``IShellLinkW`` + ``IPersistFile``:
  load an existing .lnk, read target and icon, change only IconLocation and save
  in place (every other field, the AppUserModelID property included, stays).

Same technique as ``installer/winplat.py`` (no pywin32, no PowerShell). The
module imports nothing Windows-specific at import time.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, Sequence, Tuple

log = logging.getLogger("memuai.identity")

CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010b-0000-0000-C000-000000000046}"
IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
VT_LPWSTR = 31
STGM_READWRITE = 0x00000002

_types = None


def _ct():
    """ctypes structures, built on first use (keeps the import cheap and portable)."""
    global _types
    if _types is not None:
        return _types
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def parse(cls, text: str) -> "GUID":
            value = uuid.UUID(text)
            guid = cls()
            guid.Data1, guid.Data2, guid.Data3 = value.time_low, value.time_mid, value.time_hi_version
            for i, byte in enumerate(value.bytes[8:]):
                guid.Data4[i] = byte
            return guid

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class _PVUnion(ctypes.Union):
        _fields_ = [("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_ulonglong * 2)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("u", _PVUnion)]

    _types = (ctypes, wintypes, GUID, PROPERTYKEY, PROPVARIANT)
    return _types


def _method(obj, index: int, *argtypes):
    ctypes = _ct()[0]
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[index])
    return lambda *args: function(obj, *args)


def _release(obj) -> None:
    ctypes = _ct()[0]
    if obj and obj.value:
        try:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)
        except Exception:  # noqa: BLE001
            pass


class _Com:
    """``CoInitializeEx`` for the calling thread, balanced on exit."""

    def __enter__(self):
        ctypes = _ct()[0]
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoInitializeEx.restype = ctypes.c_long
        self.ole32 = ole32
        self.initialized = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        return self

    def __exit__(self, *exc) -> None:
        if self.initialized in (0, 1):
            self.ole32.CoUninitialize()


def set_window_properties(hwnd: int, fmtid: str, props: Sequence[Tuple[int, str]]) -> bool:
    ctypes, wintypes, GUID, PROPERTYKEY, PROPVARIANT = _ct()
    shell32 = ctypes.windll.shell32
    shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID),
                                                    ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    with _Com():
        store = ctypes.c_void_p()
        hr = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(GUID.parse(IID_PROPERTY_STORE)),
                                                 ctypes.byref(store))
        if hr < 0 or not store.value:
            return False
        try:
            set_value = _method(store, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
            keep = []
            for pid, text in props:
                key = PROPERTYKEY(GUID.parse(fmtid), pid)
                value = PROPVARIANT()
                value.vt = VT_LPWSTR
                buffer = ctypes.create_unicode_buffer(text)
                keep.append(buffer)
                value.u.pwszVal = ctypes.cast(buffer, ctypes.c_wchar_p)
                set_value(ctypes.byref(key), ctypes.byref(value))
            _method(store, 7)()                                   # Commit
            return True
        except OSError as exc:
            log.debug("IPropertyStore on the window failed: %s", exc)
            return False
        finally:
            _release(store)


def _open_link(path: str, mode: int):
    """(link, persist) COM objects with ``path`` loaded, or raises OSError."""
    ctypes, wintypes, GUID, _key, _pv = _ct()
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_SHELL_LINK)), None, 1,
                                ctypes.byref(GUID.parse(IID_SHELL_LINK_W)), ctypes.byref(link))
    if hr < 0 or not link.value:
        raise OSError(f"CoCreateInstance(ShellLink) failed: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        _method(link, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(GUID.parse(IID_PERSIST_FILE)), ctypes.byref(persist))
        _method(persist, 5, wintypes.LPCWSTR, wintypes.DWORD)(path, mode)       # IPersistFile::Load
    except Exception:
        _release(persist)
        _release(link)
        raise
    return link, persist


def read_shortcut(path: str) -> Optional[Tuple[str, str, int]]:
    """(target path, icon location, icon index) of a .lnk, or None."""
    ctypes, wintypes = _ct()[:2]
    with _Com():
        try:
            link, persist = _open_link(path, 0)
        except OSError as exc:
            log.debug("cannot read %s: %s", path, exc)
            return None
        try:
            target = ctypes.create_unicode_buffer(1024)
            _method(link, 3, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(
                target, 1024, None, 0x4)                                         # GetPath, SLGP_RAWPATH
            icon = ctypes.create_unicode_buffer(1024)
            index = ctypes.c_int(0)
            _method(link, 16, wintypes.LPWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_int))(
                icon, 1024, ctypes.byref(index))                                 # GetIconLocation
            return target.value, icon.value, int(index.value)
        except OSError as exc:
            log.debug("cannot read %s: %s", path, exc)
            return None
        finally:
            _release(persist)
            _release(link)


def write_shortcut_icon(path: str, icon: str, index: int = 0) -> bool:
    """Change only the IconLocation of an existing .lnk and save it in place."""
    ctypes, wintypes = _ct()[:2]
    with _Com():
        try:
            link, persist = _open_link(path, STGM_READWRITE)
        except OSError as exc:
            log.debug("cannot open %s: %s", path, exc)
            return False
        try:
            _method(link, 17, wintypes.LPCWSTR, ctypes.c_int)(icon, int(index))  # SetIconLocation
            _method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(None, True)    # Save (same file)
            return True
        except OSError as exc:
            log.debug("cannot save %s: %s", path, exc)
            return False
        finally:
            _release(persist)
            _release(link)
