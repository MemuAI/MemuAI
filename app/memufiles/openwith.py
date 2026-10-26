"""«Open with» for MemuFiles: the apps Windows recommends for a file, launching one, and the system chooser.

Round 35 called ``rundll32 shell32.dll,OpenAs_RunDLL`` through ``Popen([...])``: the list form quotes a path
with spaces, which OpenAs_RunDLL (deprecated, takes the raw tail of the command line) does not parse, and the
dialog had no owner, so it often opened behind the window or not at all. Now:

* ``handlers(path)`` — ``SHAssocEnumHandlers(ext, ASSOC_FILTER_RECOMMENDED)``: the same list Explorer shows
  (desktop and Store apps), the default app first; falls back to the registry (``OpenWithProgids`` /
  ``OpenWithList`` in HKCR and the per-user ``FileExts``) when the shell enumerator fails.
* ``launch(path, handler_id)`` — ``IAssocHandler::Invoke`` with the file's ``IDataObject`` (no command line,
  so quotes, spaces and Cyrillic cannot break it); registry entries go through ``ShellExecuteEx`` with
  ``SEE_MASK_CLASSNAME`` (``lpClass`` = ProgID / ``Applications\\x.exe``).
* ``chooser(path, hwnd)`` — ``SHOpenWithDialog`` (the modern «How do you want to open this file?») owned by the
  MemuFiles window, falling back to the ``openas`` verb and then to OpenAs_RunDLL with an unquoted path.

Every shell call runs on one long-lived STA thread (``CoInitializeEx`` + a message pump), so COM is always
initialised, calls never race each other, and shell extensions are not cut off by a finishing HTTP thread.
The modal chooser gets its own STA thread so it never blocks the handler list.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("memufiles.openwith")
IS_WIN = os.name == "nt"

ASSOC_FILTER_RECOMMENDED = 0x1
ASSOCSTR_EXECUTABLE = 2
ASSOCSTR_FRIENDLYAPPNAME = 4
ASSOCF_INIT_IGNOREUNKNOWN = 0x400
ASSOCF_OPEN_BYEXENAME = 0x2
OAIF_ALLOW_REGISTRATION, OAIF_REGISTER_EXT, OAIF_EXEC = 0x1, 0x2, 0x4
SEE_MASK_CLASSNAME, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI, SEE_MASK_INVOKEIDLIST = 0x1, 0x100, 0x400, 0xC
COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE = 0x2, 0x4
ERROR_CANCELLED_HR = 0x800704C7
MAX_HANDLERS = 24
_ICONS: Dict[str, Tuple[str, int]] = {}
_ICON_PNG: Dict[str, Optional[bytes]] = {}

class OpenWithError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code

class _StaWorker:
    """One background STA thread; ``call(fn)`` runs ``fn`` there and returns its result."""

    def __init__(self) -> None:
        self._jobs: "queue.Queue[Tuple[Callable[[], Any], Dict[str, Any], threading.Event]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def call(self, fn: Callable[[], Any], timeout: float = 15.0) -> Any:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="memufiles-shell", daemon=True)
                self._thread.start()
        box: Dict[str, Any] = {}
        done = threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(timeout):
            box["abandoned"] = True
            raise OpenWithError("timeout", "the Windows shell did not answer")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _run(self) -> None:
        _co_init()
        while True:
            try:
                fn, box, done = self._jobs.get(timeout=0.05)
            except queue.Empty:
                _pump()
                continue
            if box.get("abandoned"):
                done.set()
                continue
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
                box["error"] = exc
            finally:
                done.set()
            _pump()

_WORKER = _StaWorker()

def _api() -> Any:
    """``ctypes.windll`` (tests substitute a fake with shell32/ole32/user32/shlwapi attributes)."""
    return ctypes.windll  # type: ignore[attr-defined]

def _co_init() -> None:
    if not IS_WIN:
        return
    try:
        _api().ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    except Exception as exc:  # noqa: BLE001
        log.debug("CoInitializeEx failed: %s", exc)

def _pump() -> None:
    """Dispatch pending window messages of the STA thread (DDE launches and shell extensions need it)."""
    if not IS_WIN:
        return
    try:
        from ctypes import wintypes
        msg = wintypes.MSG()
        user32 = _api().user32
        for _ in range(64):
            if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except Exception as exc:  # noqa: BLE001 - the worker must never die in its idle loop
        log.debug("message pump failed: %s", exc)

def _allow_foreground() -> None:
    """Let the launched app / OpenWith.exe come to the front instead of flashing behind MemuFiles."""
    try:
        _api().user32.AllowSetForegroundWindow(-1)
    except Exception:  # noqa: BLE001
        pass

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str = "") -> None:
        super().__init__()
        if text:
            import uuid
            raw = uuid.UUID(text).bytes_le
            ctypes.memmove(ctypes.byref(self), raw, 16)

IID_IShellItem = "43826d1e-e718-42ee-bc55-a1e261c37bfe"
BHID_DataObject = "b8c0bd9f-ed24-455c-83e6-d5390c4fe8c4"
IID_IDataObject = "0000010e-0000-0000-c000-000000000046"

def _vcall(ptr: int, index: int, argtypes: List[Any], *args: Any) -> int:
    """Call method ``index`` of the COM vtable of ``ptr``; returns the raw HRESULT."""
    vtbl = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)   # type: ignore[attr-defined]
    return int(proto(vtbl[index])(ptr, *args))

def _release(ptr: Optional[int]) -> None:
    if ptr:
        try:
            _vcall(ptr, 2, [])
        except Exception:  # noqa: BLE001
            pass

def _failed(hr: int) -> bool:
    return (hr & 0xFFFFFFFF) >= 0x80000000

def _co_string(ptr: int, index: int) -> str:
    out = ctypes.c_wchar_p()
    hr = _vcall(ptr, index, [ctypes.POINTER(ctypes.c_wchar_p)], ctypes.byref(out))
    if _failed(hr) or not out.value:
        return ""
    value = out.value
    try:
        _api().ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    except Exception:  # noqa: BLE001
        pass
    return value

def _ext(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return ext if len(ext) > 1 else ""

def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(path.strip().strip('"')))) if path else ""

def assoc_query(assoc: str, what: int, flags: int = ASSOCF_INIT_IGNOREUNKNOWN) -> str:
    """``AssocQueryStringW`` (shlwapi): the default executable / friendly name for an extension, ProgID or exe."""
    size = ctypes.c_uint32(1024)
    buf = ctypes.create_unicode_buffer(1024)
    try:
        hr = _api().shlwapi.AssocQueryStringW(flags, what, assoc, None, buf, ctypes.byref(size))
    except Exception:  # noqa: BLE001
        return ""
    return buf.value if not _failed(int(hr)) else ""

def _handler_id(kind: str, name: str) -> str:
    return f"{kind}:{name}"

def _enum_shell(ext: str) -> List[Dict[str, Any]]:
    """Recommended handlers from ``SHAssocEnumHandlers`` (must run on the STA worker)."""
    enum = ctypes.c_void_p()
    hr = int(_api().shell32.SHAssocEnumHandlers(ext, ASSOC_FILTER_RECOMMENDED, ctypes.byref(enum)))
    if _failed(hr) or not enum.value:
        raise OpenWithError("enum_failed", hex(hr & 0xFFFFFFFF))
    found: List[Dict[str, Any]] = []
    try:
        for _ in range(MAX_HANDLERS * 2):
            item = ctypes.c_void_p()
            fetched = ctypes.c_ulong(0)
            hr = _vcall(enum.value, 3, [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)],
                        1, ctypes.byref(item), ctypes.byref(fetched))
            if hr != 0 or not fetched.value or not item.value:
                break
            try:
                name = _co_string(item.value, 3)
                ui = _co_string(item.value, 4) or os.path.basename(name)
                icon_path = ctypes.c_wchar_p()
                icon_index = ctypes.c_int(0)
                icon: Optional[Tuple[str, int]] = None
                if not _failed(_vcall(item.value, 5, [ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(ctypes.c_int)],
                                      ctypes.byref(icon_path), ctypes.byref(icon_index))) and icon_path.value:
                    icon = (icon_path.value, int(icon_index.value))
                    try:
                        _api().ole32.CoTaskMemFree(ctypes.cast(icon_path, ctypes.c_void_p))
                    except Exception:  # noqa: BLE001
                        pass
                if name:
                    found.append({"id": _handler_id("shell", name), "name": ui, "exe": name, "icon_loc": icon})
            finally:
                _release(item.value)
            if len(found) >= MAX_HANDLERS:
                break
    finally:
        _release(enum.value)
    return found

def _enum_registry(ext: str) -> List[Dict[str, Any]]:
    """Fallback list from the registry: ProgIDs and ``Applications\\*.exe`` registered for the extension."""
    import winreg   # type: ignore
    classes: List[str] = []

    def values(root: Any, sub: str) -> List[str]:
        out: List[str] = []
        try:
            with winreg.OpenKey(root, sub) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    out.append((name, value))   # type: ignore[arg-type]
        except OSError:
            pass
        return out   # type: ignore[return-value]

    def subkeys(root: Any, sub: str) -> List[str]:
        out: List[str] = []
        try:
            with winreg.OpenKey(root, sub) as key:
                index = 0
                while True:
                    try:
                        out.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            pass
        return out

    user = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}"
    mru = dict(values(winreg.HKEY_CURRENT_USER, user + r"\OpenWithList"))
    order = str(mru.get("MRUList") or "")
    for letter in order:
        exe = mru.get(letter)
        if isinstance(exe, str) and exe.lower().endswith(".exe"):
            classes.append("Applications\\" + exe)
    for name, _value in values(winreg.HKEY_CURRENT_USER, user + r"\OpenWithProgids") + \
            values(winreg.HKEY_CLASSES_ROOT, ext + r"\OpenWithProgids"):
        if name:
            classes.append(name)
    for exe in subkeys(winreg.HKEY_CLASSES_ROOT, ext + r"\OpenWithList"):
        if exe.lower().endswith(".exe"):
            classes.append("Applications\\" + exe)
    found: List[Dict[str, Any]] = []
    seen: set = set()
    for cls in classes:
        key = cls.lower()
        if key in seen:
            continue
        seen.add(key)
        if cls.startswith("Applications\\"):
            exe_name = cls.split("\\", 1)[1]
            name = assoc_query(exe_name, ASSOCSTR_FRIENDLYAPPNAME, ASSOCF_INIT_IGNOREUNKNOWN | ASSOCF_OPEN_BYEXENAME)
            exe = assoc_query(exe_name, ASSOCSTR_EXECUTABLE, ASSOCF_INIT_IGNOREUNKNOWN | ASSOCF_OPEN_BYEXENAME)
        else:
            name = assoc_query(cls, ASSOCSTR_FRIENDLYAPPNAME)
            exe = assoc_query(cls, ASSOCSTR_EXECUTABLE)
        if not name and not exe:
            continue
        found.append({"id": _handler_id("class", cls), "name": name or os.path.basename(exe) or cls, "exe": exe,
                      "icon_loc": (exe, 0) if exe and exe.lower().endswith(".exe") else None})
        if len(found) >= MAX_HANDLERS:
            break
    return found

def _dedupe_and_sort(items: List[Dict[str, Any]], default_exe: str, default_name: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    target = _norm(default_exe)

    by_name = default_name.strip().lower() if not any(target and _norm(i.get("exe") or "") == target for i in items) else ""
    for item in items:
        key = (_norm(item.get("exe") or "") or item["id"].lower(), item["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        item["default"] = (bool(target) and _norm(item.get("exe") or "") == target) or \
            (bool(by_name) and item["name"].strip().lower() == by_name and not any(o["default"] for o in out))
        out.append(item)
    out.sort(key=lambda item: (not item["default"], item["name"].lower()))
    return out

def _list_now(path: str) -> List[Dict[str, Any]]:
    ext = _ext(path)
    if not ext:
        return []
    default_exe = assoc_query(ext, ASSOCSTR_EXECUTABLE)
    try:
        items = _enum_shell(ext)
    except Exception as exc:  # noqa: BLE001 - old shell / broken registration: use the registry
        log.info("SHAssocEnumHandlers(%s) failed: %s; using the registry", ext, exc)
        items = []
    if not items:
        try:
            items = _enum_registry(ext)
        except Exception as exc:  # noqa: BLE001
            log.info("registry handlers for %s failed: %s", ext, exc)
            items = []
    items = _dedupe_and_sort(items, default_exe, assoc_query(ext, ASSOCSTR_FRIENDLYAPPNAME))
    for item in items:
        loc = item.pop("icon_loc", None)
        if loc:
            _ICONS[item["id"]] = loc
        item["icon"] = bool(loc)
    return items

def handlers(path: str) -> List[Dict[str, Any]]:
    """``[{id, name, exe, default, icon}]`` — apps that can open ``path`` (empty off Windows / no extension)."""
    if not IS_WIN or os.path.isdir(path):
        return []
    return list(_WORKER.call(lambda: _list_now(path)))

def _shell_execute(path: str, verb: Optional[str] = None, cls: Optional[str] = None,
                   exe: Optional[str] = None, hwnd: int = 0) -> None:
    from ctypes import wintypes

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOASYNC
    info.hwnd = hwnd or None
    info.nShow = 1
    info.lpDirectory = os.path.dirname(path) or None
    if exe:
        info.lpFile = exe
        info.lpParameters = '"' + path + '"'
    else:
        info.lpFile = path
        info.lpVerb = verb
        if cls:
            info.fMask |= SEE_MASK_CLASSNAME
            info.lpClass = cls
        elif verb in ("properties", "openas", "runas", "print", "edit"):
            info.fMask |= SEE_MASK_INVOKEIDLIST
    _allow_foreground()
    if not _api().shell32.ShellExecuteExW(ctypes.byref(info)):
        raise OpenWithError("shell_failed", f"{verb or cls or exe}: error {ctypes.GetLastError()}")   # type: ignore[attr-defined]

def _data_object(path: str) -> int:
    item = ctypes.c_void_p()
    iid = GUID(IID_IShellItem)
    hr = int(_api().shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(iid), ctypes.byref(item)))
    if _failed(hr) or not item.value:
        raise OpenWithError("shell_item", hex(hr & 0xFFFFFFFF))
    try:
        data = ctypes.c_void_p()
        bhid, iid_data = GUID(BHID_DataObject), GUID(IID_IDataObject)
        hr = _vcall(item.value, 3, [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
                                    ctypes.POINTER(ctypes.c_void_p)],
                    None, ctypes.byref(bhid), ctypes.byref(iid_data), ctypes.byref(data))
        if _failed(hr) or not data.value:
            raise OpenWithError("data_object", hex(hr & 0xFFFFFFFF))
        return int(data.value)
    finally:
        _release(item.value)

def _invoke_shell(path: str, exe: str) -> bool:
    """Find the recommended handler named ``exe`` again (fresh pointers, same thread) and ``Invoke`` it."""
    ext = _ext(path)
    enum = ctypes.c_void_p()
    if _failed(int(_api().shell32.SHAssocEnumHandlers(ext, ASSOC_FILTER_RECOMMENDED, ctypes.byref(enum)))) \
            or not enum.value:
        return False
    try:
        for _ in range(MAX_HANDLERS * 2):
            item = ctypes.c_void_p()
            fetched = ctypes.c_ulong(0)
            hr = _vcall(enum.value, 3, [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)],
                        1, ctypes.byref(item), ctypes.byref(fetched))
            if hr != 0 or not fetched.value or not item.value:
                return False
            try:
                if _co_string(item.value, 3) != exe:
                    continue
                data = _data_object(path)
                try:
                    _allow_foreground()
                    hr = _vcall(item.value, 8, [ctypes.c_void_p], data)
                finally:
                    _release(data)
                if _failed(hr):
                    raise OpenWithError("invoke_failed", hex(hr & 0xFFFFFFFF))
                return True
            finally:
                _release(item.value)
    finally:
        _release(enum.value)
    return False

def _launch_now(path: str, handler_id: str) -> None:
    kind, _sep, name = handler_id.partition(":")
    if kind == "shell":
        failure: Optional[OpenWithError] = None
        try:
            if _invoke_shell(path, name):
                return
        except OpenWithError as exc:
            log.info("Invoke(%s) failed: %s", name, exc)
            failure = exc
        if name.lower().endswith(".exe") and os.path.isfile(name):
            _shell_execute(path, exe=name)
            return
        raise failure or OpenWithError("handler_gone", name)
    if kind == "class" and name:
        _shell_execute(path, verb="open", cls=name)
        return
    raise OpenWithError("bad_handler", handler_id)

def launch(path: str, handler_id: str) -> None:
    if not IS_WIN:
        raise OpenWithError("unsupported", "Open with needs Windows")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    _WORKER.call(lambda: _launch_now(path, handler_id), timeout=30.0)

def _openas_rundll(path: str) -> None:
    """Last resort: OpenAs_RunDLL reads the raw command-line tail, so the path must NOT be quoted."""
    import subprocess
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    command = f'"{os.path.join(system, "System32", "rundll32.exe")}" shell32.dll,OpenAs_RunDLL {path}'
    subprocess.Popen(command, close_fds=True)   # noqa: S603 - a string: no list2cmdline quoting

def _chooser_now(path: str, hwnd: int) -> str:
    class OPENASINFO(ctypes.Structure):
        _fields_ = [("pcszFile", ctypes.c_wchar_p), ("pcszClass", ctypes.c_wchar_p), ("oaifInFlags", ctypes.c_int)]

    info = OPENASINFO(path, None, OAIF_ALLOW_REGISTRATION | OAIF_REGISTER_EXT | OAIF_EXEC)
    _allow_foreground()
    try:
        hr = int(_api().shell32.SHOpenWithDialog(ctypes.c_void_p(hwnd) if hwnd else None, ctypes.byref(info)))
    except Exception as exc:  # noqa: BLE001 - no export (very old shell)
        log.info("SHOpenWithDialog unavailable: %s", exc)
        hr = -1
    if not _failed(hr):
        return "dialog"
    if (hr & 0xFFFFFFFF) == ERROR_CANCELLED_HR:
        return "cancelled"
    try:
        _shell_execute(path, verb="openas", hwnd=hwnd)
        return "openas"
    except Exception as exc:  # noqa: BLE001
        log.info("openas verb failed: %s", exc)
    _openas_rundll(path)
    return "rundll"

def chooser(path: str, hwnd: int = 0) -> str:
    """Show the Windows «Open with» chooser without blocking the caller; returns how it was started."""
    if not IS_WIN:
        raise OpenWithError("unsupported", "Open with needs Windows")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    started = threading.Event()
    box: Dict[str, Any] = {}

    def run() -> None:
        _co_init()
        try:
            started.set()
            box["how"] = _chooser_now(path, hwnd)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
            log.warning("open-with chooser failed: %s", exc)
        finally:
            started.set()

    thread = threading.Thread(target=run, name="memufiles-openwith", daemon=True)
    thread.start()
    thread.join(0.6)
    if "error" in box:
        raise box["error"]
    return str(box.get("how") or "dialog")

def _icon_from_exe(location: str, index: int, size: int = 32) -> Optional[bytes]:
    from ctypes import wintypes
    api = _api()
    large = wintypes.HICON()
    if not api.shell32.ExtractIconExW(location, index, ctypes.byref(large), None, 1) or not large.value:
        return None
    try:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight, header.biPlanes, header.biBitCount = size, -size, 1, 32
        bits = ctypes.c_void_p()
        gdi = api.gdi32
        for name in ("CreateCompatibleDC", "CreateDIBSection", "SelectObject"):
            getattr(gdi, name).restype = ctypes.c_void_p
        dc = ctypes.c_void_p(gdi.CreateCompatibleDC(None))
        if not dc.value:
            return None
        bitmap = ctypes.c_void_p(gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0))
        try:
            if not bitmap.value or not bits.value:
                return None
            old = ctypes.c_void_p(gdi.SelectObject(dc, bitmap))
            api.user32.DrawIconEx(dc, 0, 0, large, size, size, 0, None, 3)
            gdi.SelectObject(dc, old)
            raw = ctypes.string_at(bits, size * size * 4)
        finally:
            if bitmap.value:
                gdi.DeleteObject(bitmap)
            gdi.DeleteDC(dc)
    finally:
        api.user32.DestroyIcon(large)
    return _bgra_png(raw, size)

def _bgra_png(raw: bytes, size: int) -> Optional[bytes]:
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow is optional
        return None
    import io
    if not any(raw[3::4]):
        raw = b"".join(raw[i:i + 3] + b"\xff" for i in range(0, len(raw), 4))
    image = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()

def _icon_from_resource(location: str, size: int = 32) -> Optional[bytes]:
    """Store apps report ``@{Package?ms-resource://…}``: ``SHLoadIndirectString`` resolves it to a PNG asset."""
    buf = ctypes.create_unicode_buffer(1024)
    if _failed(int(_api().shlwapi.SHLoadIndirectString(location, buf, 1024, None))) or not os.path.isfile(buf.value):
        return None
    try:
        from PIL import Image
        import io
        with Image.open(buf.value) as image:
            image = image.convert("RGBA")
            image.thumbnail((size, size))
            out = io.BytesIO()
            image.save(out, "PNG")
            return out.getvalue()
    except Exception:  # noqa: BLE001
        with open(buf.value, "rb") as handle:
            return handle.read(512 * 1024)

def _icon_now(handler_id: str) -> Optional[bytes]:
    location = _ICONS.get(handler_id)
    if not location:
        return None
    path, index = location
    if path.startswith("@"):
        return _icon_from_resource(path)
    return _icon_from_exe(os.path.expandvars(path.strip('"')), index)

def icon_png(handler_id: str) -> Optional[bytes]:
    """32 px PNG of an app from the last ``handlers()`` list (cached; None when unknown / no Pillow)."""
    if not IS_WIN:
        return None
    if handler_id not in _ICONS:
        return None
    if handler_id not in _ICON_PNG:
        try:
            _ICON_PNG[handler_id] = _WORKER.call(lambda: _icon_now(handler_id))
        except Exception as exc:  # noqa: BLE001
            log.debug("icon %s failed: %s", handler_id, exc)
            _ICON_PNG[handler_id] = None
    return _ICON_PNG[handler_id]

def shell_verb(path: str, verb: str, hwnd: int = 0) -> None:
    """``ShellExecuteEx`` with a verb (Properties, Run as administrator…) on the STA worker."""
    if not IS_WIN:
        raise OpenWithError("unsupported", "needs Windows")
    _WORKER.call(lambda: _shell_execute(path, verb=verb, hwnd=hwnd), timeout=30.0)
