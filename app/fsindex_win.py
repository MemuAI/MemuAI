"""Round 35: Windows parts of the PC-wide file search (:mod:`app.fsindex`).

* **Everything** (voidtools): detection (its IPC window ``EVERYTHING_TASKBAR_NOTIFICATION``, the version through
  ``WM_USER``), search through — in this order — ``Everything64.dll`` (the official SDK, when the file is next to
  Everything or in ``memuai/fsindex``), our own IPC client (``WM_COPYDATA`` + ``EVERYTHING_IPC_QUERY2``: no extra
  files needed, a hidden message-only window receives the reply), or ``es.exe`` (the official command line).
* **Drives**: ``GetLogicalDrives`` / ``GetDriveTypeW`` / ``GetVolumeInformationW``.
* **MFT** (administrator only): ``FSCTL_ENUM_USN_DATA`` over ``\\\\.\\C:`` lists every file record (name, parent,
  attributes) in seconds; folders are resolved by file reference numbers. Sizes/dates are not in those records —
  :mod:`app.fsindex` stats them lazily for the results it shows (and for ``size:``/``dm:`` filters).
* **Live changes**: the USN journal (``FSCTL_READ_USN_JOURNAL``, administrator) after an MFT read, otherwise
  ``ReadDirectoryChangesW`` with ``bWatchSubtree`` on every drive root (works without administrator rights).

Everything Win32 is behind functions: importing this module on Linux is safe and the binary parsers
(:func:`parse_ipc_list2`, :func:`parse_usn_records`, :func:`parse_notify`, :func:`batches_from_records`,
:func:`parse_es_output`) are pure, so the tests run them on synthetic buffers.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

log = logging.getLogger("memuai.fsindex.win")
IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------- time

EPOCH_AS_FILETIME = 116444736000000000


def filetime_to_epoch(ft: int) -> Optional[float]:
    if ft is None or ft <= 0 or ft >= 0x7FFFFFFFFFFFFFFF or ft == 0xFFFFFFFFFFFFFFFF:
        return None
    return (ft - EPOCH_AS_FILETIME) / 10_000_000


# ---------------------------------------------------------------- Everything IPC (constants from everything_ipc.h)

EVERYTHING_IPC_WNDCLASSES = ("EVERYTHING_TASKBAR_NOTIFICATION", "EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)")
WM_USER = 0x0400
WM_COPYDATA = 0x004A
EVERYTHING_IPC_GET_MAJOR_VERSION = 0
EVERYTHING_IPC_GET_MINOR_VERSION = 1
EVERYTHING_IPC_GET_REVISION = 2
EVERYTHING_IPC_IS_DB_LOADED = 401
EVERYTHING_IPC_COPYDATA_QUERY2W = 18
REQ_NAME = 0x00000001
REQ_PATH = 0x00000002
REQ_FULL_PATH_AND_NAME = 0x00000004
REQ_EXTENSION = 0x00000008
REQ_SIZE = 0x00000010
REQ_DATE_CREATED = 0x00000020
REQ_DATE_MODIFIED = 0x00000040
REQ_DATE_ACCESSED = 0x00000080
REQ_ATTRIBUTES = 0x00000100
IPC_FOLDER = 0x00000001
IPC_DRIVE = 0x00000002
SORT = {"name": 1, "relevance": 14, "path": 3, "size": 6, "date": 14}   # DATE_MODIFIED_DESCENDING = 14
REPLY_ID = 0x4D454D55          # "MEMU": dwData of Everything's WM_COPYDATA reply
_STRING_FLAGS = (REQ_NAME, REQ_PATH, REQ_FULL_PATH_AND_NAME, REQ_EXTENSION)
_EIGHT_BYTE_FLAGS = (REQ_SIZE, REQ_DATE_CREATED, REQ_DATE_MODIFIED, REQ_DATE_ACCESSED)
_ORDER = (REQ_NAME, REQ_PATH, REQ_FULL_PATH_AND_NAME, REQ_EXTENSION, REQ_SIZE, REQ_DATE_CREATED, REQ_DATE_MODIFIED,
          REQ_DATE_ACCESSED, REQ_ATTRIBUTES)


def build_query2(reply_hwnd: int, query: str, limit: int, request_flags: int, sort: int, offset: int = 0,
                 search_flags: int = 0) -> bytes:
    """``EVERYTHING_IPC_QUERY2`` (7 DWORDs) + the UTF-16 search text with its terminating zero."""
    head = struct.pack("<IIIIIII", reply_hwnd & 0xFFFFFFFF, REPLY_ID, search_flags, offset, limit, request_flags, sort)
    return head + (query + "\0").encode("utf-16-le")


def parse_ipc_list2(data: bytes) -> Tuple[int, List[Dict[str, Any]]]:
    """``EVERYTHING_IPC_LIST2`` reply → (total items, rows). Each item's data follows the requested flags in bit
    order: strings are ``DWORD len`` + ``len`` WCHARs + a zero; size/dates 8 bytes; attributes 4 bytes."""
    tot, num, _offset, req, _sort = struct.unpack_from("<IIIII", data, 0)
    rows: List[Dict[str, Any]] = []
    for k in range(num):
        flags, data_offset = struct.unpack_from("<II", data, 20 + 8 * k)
        p = data_offset
        row: Dict[str, Any] = {"is_dir": bool(flags & (IPC_FOLDER | IPC_DRIVE))}
        for flag in _ORDER:
            if not req & flag:
                continue
            if flag in _STRING_FLAGS:
                (ln,) = struct.unpack_from("<I", data, p)
                text = data[p + 4:p + 4 + ln * 2].decode("utf-16-le", "replace")
                p += 4 + (ln + 1) * 2
                row[{REQ_NAME: "name", REQ_PATH: "dir", REQ_FULL_PATH_AND_NAME: "path", REQ_EXTENSION: "ext"}[flag]] = text
            elif flag in _EIGHT_BYTE_FLAGS:
                (value,) = struct.unpack_from("<Q", data, p)
                p += 8
                if flag == REQ_SIZE:
                    row["size"] = None if value >= 0x7FFFFFFFFFFFFFFF else int(value)
                elif flag == REQ_DATE_MODIFIED:
                    row["mtime"] = filetime_to_epoch(value)
            elif flag == REQ_ATTRIBUTES:
                (row["attrs"],) = struct.unpack_from("<I", data, p)
                p += 4
        if "path" not in row:
            folder = row.get("dir", "")
            name = row.get("name", "")
            row["path"] = (folder.rstrip("\\") + "\\" + name) if folder else name
        if "name" not in row:
            row["name"] = row["path"].rstrip("\\").rsplit("\\", 1)[-1]
        if "dir" not in row:
            row["dir"] = row["path"].rstrip("\\").rsplit("\\", 1)[0] if "\\" in row["path"] else ""
        rows.append(row)
    return tot, rows


def _row(row: Dict[str, Any]) -> Dict[str, Any]:
    from .fsindex_query import kind_of
    name = row.get("name") or ""
    is_dir = bool(row.get("is_dir"))
    size = row.get("size")
    return {"path": row.get("path") or "", "name": name, "dir": row.get("dir") or "",
            "size": None if is_dir or size is None or size < 0 else int(size),
            "mtime": row.get("mtime"), "is_dir": is_dir, "kind": kind_of(name, is_dir)}


# ---------------------------------------------------------------- Win32 helpers

def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def find_everything_window() -> int:
    if not IS_WINDOWS:
        return 0
    user32 = _user32()
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    for cls in EVERYTHING_IPC_WNDCLASSES:
        hwnd = user32.FindWindowW(cls, None)
        if hwnd:
            return int(hwnd)
    return 0


def _send_timeout(hwnd: int, msg: int, wparam: int, lparam: Any, timeout_ms: int = 600) -> Optional[int]:
    from ctypes import wintypes
    user32 = _user32()
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    result = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x0002, timeout_ms, ctypes.byref(result))  # SMTO_ABORTIFHUNG
    return int(result.value) if ok else None


def _program_dirs() -> List[str]:
    out = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            out += [os.path.join(base, "Everything"), os.path.join(base, "Everything 1.5a")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out += [os.path.join(local, "Programs", "Everything"), os.path.join(local, "Everything")]
    return list(dict.fromkeys(out))


def _registry_dirs() -> List[str]:
    if not IS_WINDOWS:
        return []
    out: List[str] = []
    try:
        import winreg
    except ImportError:
        return out
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                root = winreg.OpenKey(hive, base)
            except OSError:
                continue
            with root:
                for i in range(4000):
                    try:
                        sub = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    if "everything" not in sub.lower():
                        continue
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    value = str(winreg.QueryValueEx(key, value_name)[0]).strip().strip('"')
                                except OSError:
                                    continue
                                if value:
                                    folder = value if os.path.isdir(value) else os.path.dirname(value.split(",")[0])
                                    if folder:
                                        out.append(folder)
                    except OSError:
                        continue
    return out


def _data_dir() -> str:
    try:
        from .fsindex import data_dir
        return str(data_dir())
    except Exception:
        return ""


def everything_paths() -> Dict[str, str]:
    """Where Everything.exe, es.exe and Everything64.dll are (empty strings when not found)."""
    dirs = _registry_dirs() + _program_dirs()
    extra = [d for d in (_data_dir(), os.path.join(os.path.dirname(sys.executable), "bin")) if d]
    exe = es = dll = ""
    for d in dirs:
        for name in ("Everything.exe", "Everything64.exe"):
            p = os.path.join(d, name)
            if not exe and os.path.isfile(p):
                exe = p
    for d in dirs + extra:
        for name in ("Everything64.dll" if struct.calcsize("P") == 8 else "Everything32.dll",):
            p = os.path.join(d, name)
            if not dll and os.path.isfile(p):
                dll = p
        p = os.path.join(d, "es.exe")
        if not es and os.path.isfile(p):
            es = p
    if not es:
        es = shutil.which("es.exe") or shutil.which("es") or ""
        if es and not es.lower().endswith(".exe"):
            es = ""
    return {"exe": exe, "es": es, "dll": dll}


def everything_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"installed": False, "running": False, "version": "", "db_loaded": False, "via": ""}
    paths = everything_paths() if IS_WINDOWS else {"exe": "", "es": "", "dll": ""}
    info.update(paths)
    hwnd = find_everything_window()
    info["installed"] = bool(paths["exe"] or hwnd)
    if hwnd:
        info["running"] = True
        parts = [_send_timeout(hwnd, WM_USER, cmd, 0) for cmd in (EVERYTHING_IPC_GET_MAJOR_VERSION,
                                                                   EVERYTHING_IPC_GET_MINOR_VERSION,
                                                                   EVERYTHING_IPC_GET_REVISION)]
        if parts[0] is not None:
            info["version"] = ".".join(str(p or 0) for p in parts)
        loaded = _send_timeout(hwnd, WM_USER, EVERYTHING_IPC_IS_DB_LOADED, 0)
        info["db_loaded"] = bool(loaded) if loaded is not None else True
        info["via"] = "dll" if paths["dll"] else "ipc"
    return info


def start_everything(exe: str = "") -> bool:
    exe = exe or everything_paths()["exe"]
    if not exe or not os.path.isfile(exe):
        return False
    try:
        subprocess.Popen([exe, "-startup"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) |
                         getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
        return True
    except OSError:
        return False


# ---- own IPC client (no files needed)

_IPC_LOCK = threading.Lock()
_IPC_REPLIES: Dict[int, bytes] = {}
_IPC_CLASS: Dict[str, Any] = {}


def _ipc_class() -> Tuple[str, Any]:
    from ctypes import wintypes
    if _IPC_CLASS:
        return _IPC_CLASS["name"], _IPC_CLASS["hinst"]
    user32 = _user32()
    kernel32 = _kernel32()
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class COPYDATASTRUCT(ctypes.Structure):
        _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_COPYDATA and lparam:
            cds = ctypes.cast(lparam, ctypes.POINTER(COPYDATASTRUCT)).contents
            if cds.dwData == REPLY_ID and cds.lpData:
                _IPC_REPLIES[int(hwnd)] = ctypes.string_at(cds.lpData, cds.cbData)
                return 1
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = "MemuAIFsIndexEverythingReply"
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW(ctypes.byref(wc))      # 0 when already registered by an earlier call — fine
    _IPC_CLASS.update({"name": wc.lpszClassName, "hinst": hinst, "proc": proc, "wc": wc, "cds": COPYDATASTRUCT})
    return wc.lpszClassName, hinst


def ipc_query(query: str, limit: int, sort: int, timeout: float = 3.0,
              request_flags: int = REQ_NAME | REQ_PATH | REQ_SIZE | REQ_DATE_MODIFIED) -> Tuple[int, List[Dict[str, Any]]]:
    from ctypes import wintypes
    target = find_everything_window()
    if not target:
        raise RuntimeError("Everything is not running")
    with _IPC_LOCK:
        cls, hinst = _ipc_class()
        user32 = _user32()
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        hwnd = user32.CreateWindowExW(0, cls, "", 0, 0, 0, 0, 0, wintypes.HWND(-3), None, hinst, None)  # HWND_MESSAGE
        if not hwnd:
            raise RuntimeError("could not create the reply window")
        hwnd_i = int(hwnd)
        try:
            payload = build_query2(hwnd_i, query, limit, request_flags, sort)
            buf = ctypes.create_string_buffer(payload, len(payload))
            cds = _IPC_CLASS["cds"](EVERYTHING_IPC_COPYDATA_QUERY2W, len(payload), ctypes.cast(buf, ctypes.c_void_p))
            sent = _send_timeout(target, WM_COPYDATA, hwnd_i, ctypes.addressof(cds), int(timeout * 1000))
            if not sent:
                raise RuntimeError("Everything did not accept the query")
            msg = wintypes.MSG()
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                            wintypes.UINT, wintypes.UINT]
            user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL,
                                                         wintypes.DWORD, wintypes.DWORD]
            end = time.time() + timeout
            while hwnd_i not in _IPC_REPLIES and time.time() < end:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):   # PM_REMOVE (sent messages too)
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if hwnd_i in _IPC_REPLIES:
                    break
                user32.MsgWaitForMultipleObjects(0, None, False, 15, 0x04FF)       # QS_ALLINPUT
            data = _IPC_REPLIES.pop(hwnd_i, None)
            if data is None:
                raise TimeoutError("Everything did not answer in time")
            return parse_ipc_list2(data)
        finally:
            user32.DestroyWindow(hwnd)


# ---- SDK DLL

_DLL_LOCK = threading.Lock()
_DLL: Dict[str, Any] = {}


def dll_query(dll_path: str, query: str, limit: int, sort: int) -> Tuple[int, List[Dict[str, Any]]]:
    from ctypes import wintypes
    with _DLL_LOCK:
        lib = _DLL.get(dll_path)
        if lib is None:
            lib = ctypes.WinDLL(dll_path)
            lib.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
            lib.Everything_SetMax.argtypes = [wintypes.DWORD]
            lib.Everything_SetSort.argtypes = [wintypes.DWORD]
            lib.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
            lib.Everything_QueryW.argtypes = [wintypes.BOOL]
            lib.Everything_QueryW.restype = wintypes.BOOL
            lib.Everything_GetNumResults.restype = wintypes.DWORD
            lib.Everything_GetTotResults.restype = wintypes.DWORD
            lib.Everything_GetResultFullPathNameW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]
            lib.Everything_GetResultSize.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_longlong)]
            lib.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_ulonglong)]
            lib.Everything_IsFolderResult.argtypes = [wintypes.DWORD]
            lib.Everything_GetLastError.restype = wintypes.DWORD
            _DLL[dll_path] = lib
        lib.Everything_SetSearchW(query)
        lib.Everything_SetMax(limit)
        try:
            lib.Everything_SetSort(sort)
            lib.Everything_SetRequestFlags(REQ_FULL_PATH_AND_NAME | REQ_SIZE | REQ_DATE_MODIFIED)
        except AttributeError:
            pass
        if not lib.Everything_QueryW(True):
            raise RuntimeError(f"Everything SDK error {lib.Everything_GetLastError()}")
        rows = []
        buf = ctypes.create_unicode_buffer(32768)
        for i in range(lib.Everything_GetNumResults()):
            lib.Everything_GetResultFullPathNameW(i, buf, 32768)
            path = buf.value
            size = ctypes.c_longlong(-1)
            ft = ctypes.c_ulonglong(0)
            lib.Everything_GetResultSize(i, ctypes.byref(size))
            lib.Everything_GetResultDateModified(i, ctypes.byref(ft))
            folder, _, name = path.rpartition("\\")
            rows.append({"path": path, "name": name, "dir": folder, "size": size.value if size.value >= 0 else None,
                         "mtime": filetime_to_epoch(ft.value), "is_dir": bool(lib.Everything_IsFolderResult(i))})
        return int(lib.Everything_GetTotResults()), rows


# ---- es.exe

def parse_es_output(text: str) -> List[Dict[str, Any]]:
    """``es.exe -csv`` output (with or without a header; columns Filename/Size/Date Modified/Attributes in any
    order) or plain lines of paths → rows (missing columns are ``None`` and stat'ed by the caller)."""
    import csv
    import io
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if "," not in lines[0] and '"' not in lines[0]:
        return [{"path": line.strip()} for line in lines]
    reader = list(csv.reader(io.StringIO("\n".join(lines))))
    header = [h.strip().lower() for h in reader[0]]
    cols = {"path": -1, "size": -1, "mtime": -1, "attrs": -1}
    has_header = any(h in ("filename", "name", "full path", "path", "size", "date modified") for h in header)
    if has_header:
        for i, h in enumerate(header):
            if h in ("filename", "full path", "full path name", "path and name"):
                cols["path"] = i
            elif h == "size":
                cols["size"] = i
            elif h in ("date modified", "dm", "date_modified"):
                cols["mtime"] = i
            elif h in ("attributes", "attribs", "attrib"):
                cols["attrs"] = i
        body = reader[1:]
    else:
        cols["path"] = 0
        body = reader
    if cols["path"] < 0:
        cols["path"] = 0
    rows = []
    for rec in body:
        if not rec:
            continue
        row: Dict[str, Any] = {"path": rec[cols["path"]].strip()}
        if 0 <= cols["size"] < len(rec):
            try:
                row["size"] = int(rec[cols["size"]].replace(" ", "").replace("\xa0", "") or -1)
            except ValueError:
                row["size"] = None
        if 0 <= cols["mtime"] < len(rec):
            value = rec[cols["mtime"]].strip()
            if value.isdigit():
                row["mtime"] = filetime_to_epoch(int(value))
            else:
                try:
                    import datetime
                    row["mtime"] = datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    row["mtime"] = None
        if 0 <= cols["attrs"] < len(rec):
            row["is_dir"] = "D" in rec[cols["attrs"]].upper()
        rows.append(row)
    return rows


def es_query(es_path: str, query: str, limit: int, sort: str) -> Tuple[int, List[Dict[str, Any]]]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    sort_arg = {"name": "name", "path": "path", "size": "size-descending", "date": "date-modified-descending",
                "relevance": "date-modified-descending"}.get(sort, "name")
    tmp = os.path.join(tempfile.gettempdir(), f"memuai-es-{os.getpid()}-{threading.get_ident()}.csv")
    attempts = [
        [es_path, "-n", str(limit), "-sort", sort_arg, "-size", "-dm", "-attributes", "-date-format", "1",
         "-size-format", "1", "-export-csv", tmp, "-utf8-bom", query],
        [es_path, "-n", str(limit), query],
    ]
    last_error = ""
    for args in attempts:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            proc = subprocess.run(args, capture_output=True, timeout=6, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        if proc.returncode != 0:
            last_error = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[:200] or f"exit {proc.returncode}"
            continue
        if "-export-csv" in args and os.path.exists(tmp):
            with open(tmp, "rb") as fh:
                raw = fh.read()
            try:
                os.remove(tmp)
            except OSError:
                pass
            text = raw.decode("utf-8-sig", "replace")
        else:
            raw = proc.stdout or b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("mbcs" if IS_WINDOWS else "latin-1", "replace")
        rows = parse_es_output(text)
        for row in rows:
            path = row["path"]
            if row.get("size") is None or row.get("mtime") is None or "is_dir" not in row:
                try:
                    st = os.stat(path)
                    row.setdefault("is_dir", os.path.isdir(path))
                    if row.get("size") is None:
                        row["size"] = st.st_size
                    if row.get("mtime") is None:
                        row["mtime"] = st.st_mtime
                except OSError:
                    row.setdefault("is_dir", False)
            folder, _, name = path.rstrip("\\").rpartition("\\")
            row["name"], row["dir"] = name, folder
        return len(rows), rows
    raise RuntimeError("es.exe failed: " + last_error)


def everything_search(query: str, limit: int = 200, sort: str = "relevance", info: Optional[Dict[str, Any]] = None,
                      timeout: float = 3.0, rank_query: str = "") -> Dict[str, Any]:
    """Ask Everything (DLL → own IPC → es.exe). Relevance: Everything sorts by date, MemuAI re-ranks exact/prefix
    names and user folders first (the same rules as its own index)."""
    info = info or everything_info()
    ask = min(1000, max(limit, limit * 4 if sort == "relevance" else limit))
    sort_id = SORT.get(sort, 1)
    errors = []
    tot, rows, via = 0, None, ""
    if info.get("dll"):
        try:
            tot, rows = dll_query(info["dll"], query, ask, sort_id)
            via = "dll"
        except Exception as exc:
            errors.append(f"dll: {exc}")
    if rows is None and info.get("running"):
        try:
            tot, rows = ipc_query(query, ask, sort_id, timeout=timeout)
            via = "ipc"
        except Exception as exc:
            errors.append(f"ipc: {exc}")
    if rows is None and info.get("es"):
        try:
            tot, rows = es_query(info["es"], query, ask, sort)
            via = "es"
        except Exception as exc:
            errors.append(f"es: {exc}")
    if rows is None:
        raise RuntimeError("; ".join(errors) or "Everything is not available")
    results = [_row(r) for r in rows]
    if sort == "relevance":
        from . import fsindex_query as fq
        from .fsindex import _noise
        try:
            groups = [g for g in fq.parse(rank_query or query).groups if g] or [[]]
        except Exception:
            groups = [[]]
        order = {id(r): i for i, r in enumerate(results)}
        results.sort(key=lambda r: (min(fq.quality(g, fq.low(r["name"])) for g in groups),
                                    _noise(fq.norm_path(r["dir"])), order[id(r)]))
    return {"results": results[:limit], "total": max(tot, len(results)), "capped": tot > limit, "partial": False,
            "via": via}


# ---------------------------------------------------------------- drives / admin

def drives() -> List[Dict[str, Any]]:
    if not IS_WINDOWS:
        return []
    kernel32 = _kernel32()
    mask = kernel32.GetLogicalDrives()
    out = []
    kinds = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        kind = kinds.get(kernel32.GetDriveTypeW(root), "other")
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ok = False
        if kind in ("fixed", "removable", "ramdisk"):
            ok = bool(kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), label, 261, None, None, None, fs, 261))
        out.append({"path": root, "type": kind, "label": label.value if ok else "", "fs": fs.value if ok else ""})
    return out


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_ntfs_root(root: str) -> bool:
    if not IS_WINDOWS or len(root.rstrip("\\/")) != 2 or root[1] != ":":
        return False
    for d in drives():
        if d["path"].lower() == (root[:2] + "\\").lower():
            return d.get("fs", "").upper() == "NTFS" and d.get("type") == "fixed"
    return False


# ---------------------------------------------------------------- MFT / USN

FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_READ_USN_JOURNAL = 0x000900BB
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_HIDDEN = 0x2
USN_REASON_DATA_OVERWRITE = 0x1
USN_REASON_DATA_EXTEND = 0x2
USN_REASON_DATA_TRUNCATION = 0x4
USN_REASON_FILE_CREATE = 0x100
USN_REASON_FILE_DELETE = 0x200
USN_REASON_RENAME_OLD_NAME = 0x1000
USN_REASON_RENAME_NEW_NAME = 0x2000
USN_REASON_BASIC_INFO_CHANGE = 0x8000
USN_REASON_CLOSE = 0x80000000
ROOT_RECORD = 5
_V2 = struct.Struct("<IHHQQqqIIIIHH")


def parse_usn_records(buf: bytes, start: int = 8) -> Iterator[Tuple[int, int, str, int, int]]:
    """USN_RECORD_V2/V3 records after the 8-byte next-cursor → ``(frn, parent_frn, name, attributes, reason)``.
    V3 128-bit references are folded to their low 64 bits (NTFS keeps them there)."""
    p = start
    n = len(buf)
    while p + 8 <= n:
        length, major, _minor = struct.unpack_from("<IHH", buf, p)
        if length == 0 or p + length > n:
            break
        if major == 2:
            (_l, _ma, _mi, frn, parent, _usn, _ts, reason, _src, _sec, attrs, name_len, name_off) = _V2.unpack_from(buf, p)
        elif major == 3:
            frn = struct.unpack_from("<Q", buf, p + 8)[0]
            parent = struct.unpack_from("<Q", buf, p + 24)[0]
            reason, _src, _sec, attrs, name_len, name_off = struct.unpack_from("<IIIIHH", buf, p + 56)
        else:
            p += length
            continue
        name = buf[p + name_off:p + name_off + name_len].decode("utf-16-le", "replace")
        yield frn, parent, name, attrs, reason
        p += length


def batches_from_records(records: Iterable[Tuple[int, int, str, int]], root: str, rules: Any = None,
                         hidden: bool = True) -> Tuple[List[Tuple[str, List[Tuple[str, int, float, bool]]]], Dict[int, Tuple[int, str]]]:
    """MFT records ``(frn, parent_frn, name, attrs)`` of one volume → folder batches + ``{dir frn: (parent, name)}``
    (kept for the USN journal). Records not reachable from the root (metafiles, orphans) are dropped; excluded
    folders drop their whole subtree. Size/mtime are -1 (unknown)."""
    root = root[:2] + "\\"
    dirs: Dict[int, Tuple[int, str]] = {}
    files: List[Tuple[int, int, str, int]] = []
    root_frn: Optional[int] = None
    for frn, parent, name, attrs in records:
        if (frn & 0xFFFFFFFFFFFF) == ROOT_RECORD:
            root_frn = frn
            continue
        if attrs & FILE_ATTRIBUTE_DIRECTORY:
            dirs[frn] = (parent, name)
        files.append((frn, parent, name, attrs))
    paths: Dict[int, Optional[str]] = {}

    def is_root(ref: int) -> bool:
        return (ref & 0xFFFFFFFFFFFF) == ROOT_RECORD or (root_frn is not None and ref == root_frn)

    def path_of(ref: int) -> Optional[str]:
        if is_root(ref):
            return root
        chain = []
        cur = ref
        result: Optional[str] = None
        for _ in range(512):
            if cur in paths:
                result = paths[cur]
                break
            if is_root(cur):
                result = root
                break
            row = dirs.get(cur)
            if row is None:
                result = None
                break
            chain.append(cur)
            cur = row[0]
        for ref2 in reversed(chain):
            if result is not None:
                name = dirs[ref2][1]
                result = (result if result.endswith("\\") else result + "\\") + name
                if rules is not None and (rules.name_excluded(name) or (rules.paths and rules.path_excluded(result))):
                    result = None
            paths[ref2] = result
        return result

    grouped: Dict[str, List[Tuple[str, int, float, bool]]] = {}
    for frn, parent, name, attrs in files:
        if not hidden and attrs & FILE_ATTRIBUTE_HIDDEN:
            continue
        folder = path_of(parent)
        if folder is None:
            continue
        is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
        if is_dir and path_of(frn) is None:
            continue           # an excluded folder: not listed itself either
        grouped.setdefault(folder, []).append((name, 0 if is_dir else -1, -1.0, is_dir))
    return list(grouped.items()), dirs


_FRN_DIRS: Dict[str, Dict[int, Tuple[int, str]]] = {}


def _open_volume(root: str):
    from ctypes import wintypes
    kernel32 = _kernel32()
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW("\\\\.\\" + root[:2], 0x80000000, 0x1 | 0x2, None, 3, 0, None)  # GENERIC_READ, OPEN_EXISTING
    if not handle or handle == wintypes.HANDLE(-1).value:
        raise PermissionError(f"cannot open volume {root[:2]} (administrator rights are needed)")
    return handle


def _ioctl(handle, code: int, inbuf: bytes, out_size: int) -> Tuple[bool, bytes, int]:
    from ctypes import wintypes
    kernel32 = _kernel32()
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                         wintypes.LPVOID]
    inb = ctypes.create_string_buffer(inbuf, len(inbuf))
    out = ctypes.create_string_buffer(out_size)
    got = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(handle, code, inb, len(inbuf), out, out_size, ctypes.byref(got), None)
    return bool(ok), out.raw[:got.value], ctypes.get_last_error()


def query_journal(handle) -> Tuple[int, int]:
    ok, data, err = _ioctl(handle, FSCTL_QUERY_USN_JOURNAL, b"", 80)
    if not ok or len(data) < 24:
        raise OSError(f"USN journal unavailable (error {err})")
    journal_id, _first, next_usn = struct.unpack_from("<Qqq", data, 0)
    return journal_id, next_usn


def mft_batches(root: str, rules: Any = None, progress: Any = None, hidden: bool = True):
    """Every file record of an NTFS volume (administrator) → folder batches (see :func:`batches_from_records`)."""
    handle = _open_volume(root)
    kernel32 = _kernel32()
    try:
        _journal, next_usn = query_journal(handle)
        records: List[Tuple[int, int, str, int]] = []
        start = 0
        while True:
            inbuf = struct.pack("<Qqq", start, 0, next_usn)        # MFT_ENUM_DATA_V0
            ok, data, err = _ioctl(handle, FSCTL_ENUM_USN_DATA, inbuf, 1 << 20)
            if not ok:
                if err == 38:            # ERROR_HANDLE_EOF: done
                    break
                raise OSError(f"FSCTL_ENUM_USN_DATA failed (error {err})")
            if len(data) <= 8:
                break
            start = struct.unpack_from("<Q", data, 0)[0]
            for frn, parent, name, attrs, _reason in parse_usn_records(data):
                records.append((frn, parent, name, attrs))
            if progress is not None:
                progress.entries = len(records)
        batches, dirs = batches_from_records(records, root, rules, hidden)
        _FRN_DIRS[root[:2].upper()] = dirs
        return batches
    finally:
        kernel32.CloseHandle(handle)


def usn_events(records: Iterable[Tuple[int, int, str, int, int]], dirs: Dict[int, Tuple[int, str]], root: str,
               pending: Dict[int, str]) -> List[Tuple[str, str, str]]:
    """Journal records → ``(action, path, new_path)`` events; keeps ``dirs`` current for created/renamed folders.
    ``pending`` holds old paths of renames between their OLD_NAME and NEW_NAME records."""
    root = root[:2] + "\\"
    out: List[Tuple[str, str, str]] = []

    def folder(ref: int) -> Optional[str]:
        parts = []
        cur = ref
        for _ in range(512):
            if (cur & 0xFFFFFFFFFFFF) == ROOT_RECORD:
                return root + "\\".join(reversed(parts))
            row = dirs.get(cur)
            if row is None:
                return None
            parts.append(row[1])
            cur = row[0]
        return None

    for frn, parent, name, attrs, reason in records:
        base = folder(parent)
        if base is None:
            continue
        path = (base if base.endswith("\\") else base + "\\") + name
        is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
        if reason & USN_REASON_RENAME_OLD_NAME:
            pending[frn] = path
            continue
        if not reason & USN_REASON_CLOSE:
            continue
        if reason & USN_REASON_FILE_DELETE:
            dirs.pop(frn, None)
            if not reason & USN_REASON_FILE_CREATE:
                out.append(("deleted", path, ""))
            continue
        if reason & USN_REASON_RENAME_NEW_NAME:
            if is_dir:
                dirs[frn] = (parent, name)
            old = pending.pop(frn, "")
            out.append(("renamed", old, path) if old else ("created", path, ""))
            continue
        if reason & USN_REASON_FILE_CREATE:
            if is_dir:
                dirs[frn] = (parent, name)
            out.append(("created", path, ""))
            continue
        if reason & (USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND | USN_REASON_DATA_TRUNCATION |
                     USN_REASON_BASIC_INFO_CHANGE):
            out.append(("modified", path, ""))
    return out


class _Thread:
    def __init__(self, root: str, callback: Callable[[List[Tuple[str, str, str]]], None]) -> None:
        self.root = root
        self.callback = callback
        self.rules = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._safe_run, name=f"fsindex-{type(self).__name__}-{self.root[:2]}",
                                        daemon=True)
        self._thread.start()

    def _safe_run(self) -> None:
        try:
            self.run()
        except Exception as exc:
            log.info("fsindex: watcher %s stopped: %s", self.root, exc)

    def run(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()


class UsnWatcher(_Thread):
    """Polls the USN journal every second (administrator; after an MFT read of the same volume)."""

    def run(self) -> None:
        handle = _open_volume(self.root)
        kernel32 = _kernel32()
        try:
            journal_id, usn = query_journal(handle)
            dirs = _FRN_DIRS.setdefault(self.root[:2].upper(), {})
            pending: Dict[int, str] = {}
            mask = (USN_REASON_FILE_CREATE | USN_REASON_FILE_DELETE | USN_REASON_RENAME_OLD_NAME |
                    USN_REASON_RENAME_NEW_NAME | USN_REASON_CLOSE | USN_REASON_DATA_EXTEND |
                    USN_REASON_DATA_TRUNCATION | USN_REASON_DATA_OVERWRITE)
            while not self._stop.wait(1.0):
                inbuf = struct.pack("<qIIQQQ", usn, mask, 0, 0, 0, journal_id)   # READ_USN_JOURNAL_DATA_V0
                ok, data, err = _ioctl(handle, FSCTL_READ_USN_JOURNAL, inbuf, 1 << 16)
                if not ok or len(data) < 8:
                    if err in (1179, 1181):   # journal deleted / overwritten → the index needs a rescan
                        self.callback([("overflow", self.root, "")])
                        return
                    continue
                usn = struct.unpack_from("<q", data, 0)[0]
                events = usn_events(parse_usn_records(data), dirs, self.root, pending)
                if events:
                    self.callback(events)
        finally:
            kernel32.CloseHandle(handle)


def parse_notify(buf: bytes, root: str) -> List[Tuple[str, str, str]]:
    """``FILE_NOTIFY_INFORMATION`` chain → events (renames paired: action 4 = old name, 5 = new name)."""
    out: List[Tuple[str, str, str]] = []
    p = 0
    old = ""
    base = root if root.endswith(("\\", "/")) else root + "\\"
    names = {1: "created", 2: "deleted", 3: "modified"}
    while p + 12 <= len(buf):
        nxt, action, length = struct.unpack_from("<III", buf, p)
        name = buf[p + 12:p + 12 + length].decode("utf-16-le", "replace")
        path = base + name
        if action == 4:
            old = path
        elif action == 5:
            out.append(("renamed", old, path) if old else ("created", path, ""))
            old = ""
        elif action in names:
            out.append((names[action], path, ""))
        if not nxt:
            break
        p += nxt
    # several «modified» of one file in a burst → one
    seen = set()
    uniq = []
    for ev in out:
        if ev[0] == "modified":
            if ev[1] in seen:
                continue
            seen.add(ev[1])
        uniq.append(ev)
    return uniq


class DirWatcher(_Thread):
    """``ReadDirectoryChangesW`` on a drive root with the whole subtree (no administrator rights needed)."""

    def __init__(self, root: str, callback) -> None:
        super().__init__(root, callback)
        self._handle = None

    def run(self) -> None:
        from ctypes import wintypes
        kernel32 = _kernel32()
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(self.root, 0x0001, 0x1 | 0x2 | 0x4, None, 3, 0x02000000, None)
        if not handle or handle == wintypes.HANDLE(-1).value:
            raise OSError(f"cannot watch {self.root}")
        self._handle = handle
        kernel32.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                                   wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
                                                   wintypes.LPVOID]
        buf = ctypes.create_string_buffer(64 * 1024)
        got = wintypes.DWORD(0)
        try:
            while not self._stop.is_set():
                ok = kernel32.ReadDirectoryChangesW(handle, buf, len(buf), True, 0x1 | 0x2 | 0x8 | 0x10,
                                                    ctypes.byref(got), None, None)
                if self._stop.is_set():
                    break
                if not ok:
                    time.sleep(1.0)
                    continue
                if got.value == 0:          # the buffer overflowed: changes were lost
                    self.callback([("overflow", self.root, "")])
                    continue
                events = parse_notify(buf.raw[:got.value], self.root)
                if events:
                    self.callback(events)
        finally:
            kernel32.CloseHandle(handle)

    def stop(self) -> None:
        super().stop()
        if self._handle and IS_WINDOWS:
            try:
                kernel32 = _kernel32()
                kernel32.CancelIoEx(self._handle, None)
            except Exception:
                pass


# ---------------------------------------------------------------- shell

def reveal(path: str) -> bool:
    """Show a file selected in File Explorer."""
    if not IS_WINDOWS:
        return False
    try:
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)], close_fds=True)
        return True
    except OSError:
        return False
