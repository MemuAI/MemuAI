"""Windows clipboard interop for MemuFiles: files copied/cut in Explorer paste here, and vice versa.

Round 40 (r40-files-core): VIRTUAL files too — what Explorer puts on the clipboard for files copied out of a zip
folder (and Outlook for attachments): ``FileGroupDescriptorW`` + ``FileContents`` (an IStream or HGLOBAL per
item, no paths). :func:`iter_virtual` reads them through OLE on the calling thread; the ``vpaste`` operation
(opqueue) writes them with the usual progress, conflicts and cancel.

``CF_HDROP`` (a ``DROPFILES`` block of NUL-separated UTF-16 paths) plus the
``Preferred DropEffect`` format (2 = move/cut, 5 = copy) — what Explorer reads
and writes. Off Windows both functions are no-ops (the page keeps its own
internal clipboard either way). NOT verified on real Windows in Round 35.
"""
from __future__ import annotations

import os
import struct
from typing import Any, Dict, List

IS_WIN = os.name == "nt"
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return ctypes, user32, kernel32


def read_files() -> Dict[str, Any]:
    """``{"paths": [...], "cut": bool, "virtual": n}`` from the system clipboard (empty off Windows)."""
    if not IS_WIN:
        return {"paths": [], "cut": False, "virtual": 0}
    ctypes, user32, kernel32 = _user32()
    paths: List[str] = []
    cut = False
    if not user32.OpenClipboard(None):
        return {"paths": [], "cut": False, "busy": True}
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            handle = user32.GetClipboardData(CF_HDROP)
            if handle:
                count = ctypes.windll.shell32.DragQueryFileW(ctypes.c_void_p(handle), 0xFFFFFFFF, None, 0)
                for index in range(count):
                    length = ctypes.windll.shell32.DragQueryFileW(ctypes.c_void_p(handle), index, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.shell32.DragQueryFileW(ctypes.c_void_p(handle), index, buffer, length + 1)
                    paths.append(buffer.value)
        effect_format = user32.RegisterClipboardFormatW("Preferred DropEffect")
        if effect_format and user32.IsClipboardFormatAvailable(effect_format):
            handle = user32.GetClipboardData(effect_format)
            if handle:
                pointer = kernel32.GlobalLock(handle)
                if pointer:
                    try:
                        cut = bool(ctypes.c_uint32.from_address(pointer).value & 2)
                    finally:
                        kernel32.GlobalUnlock(handle)
        virtual = 0
        group_format = user32.RegisterClipboardFormatW("FileGroupDescriptorW")
        if not paths and group_format and user32.IsClipboardFormatAvailable(group_format):
            handle = user32.GetClipboardData(group_format)
            pointer = kernel32.GlobalLock(handle) if handle else None
            if pointer:
                try:
                    virtual = ctypes.c_uint32.from_address(pointer).value
                finally:
                    kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    return {"paths": paths, "cut": cut, "virtual": virtual}


def write_files(paths: List[str], cut: bool = False, owner: int = 0) -> bool:
    """Put *paths* on the clipboard for Explorer; an empty list clears files left there by a cut that was just
    pasted (Explorer does the same — a second paste must not try to move files that are gone)."""
    if not IS_WIN:
        return False
    ctypes, user32, kernel32 = _user32()
    if not paths:
        if not user32.OpenClipboard(None):
            return False
        try:
            if user32.IsClipboardFormatAvailable(CF_HDROP):
                user32.EmptyClipboard()
                return True
            return False
        finally:
            user32.CloseClipboard()
    files = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)        # DROPFILES: pFiles=20, pt, fNC, fWide=1
    blob = header + files

    def alloc(data: bytes):
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return None
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return handle

    def put(fmt: int, data: bytes) -> bool:
        handle = alloc(data)
        if not handle:
            return False
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)
            return False
        return True  # Windows owns the handle now

    # EmptyClipboard after OpenClipboard(NULL) leaves a NULL owner and makes
    # SetClipboardData fail. Use the actual MemuFiles HWND whenever available.
    hwnd = owner or user32.GetForegroundWindow()
    if not hwnd or not user32.OpenClipboard(hwnd):
        return False
    try:
        if not user32.EmptyClipboard() or not put(CF_HDROP, blob):
            return False
        effect_format = user32.RegisterClipboardFormatW("Preferred DropEffect")
        if effect_format:
            return put(effect_format, struct.pack("<I", 2 if cut else 5))
        return True
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- virtual files (FileGroupDescriptorW / FileContents)
DESCRIPTOR_SIZE = 592          # FILEDESCRIPTORW
FD_ATTRIBUTES, FD_WRITESTIME, FD_FILESIZE = 0x4, 0x20, 0x40
FILE_ATTRIBUTE_DIRECTORY = 0x10


def parse_group(data: bytes) -> List[Dict[str, Any]]:
    """FILEGROUPDESCRIPTORW → ``[{rel, size (-1 unknown), mtime (s, 0 unknown), dir}]``."""
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    out: List[Dict[str, Any]] = []
    for index in range(count):
        base = 4 + index * DESCRIPTOR_SIZE
        if base + DESCRIPTOR_SIZE > len(data):
            break
        flags, = struct.unpack_from("<I", data, base)
        attrs, = struct.unpack_from("<I", data, base + 36)
        write_time, = struct.unpack_from("<Q", data, base + 56)
        size_high, size_low = struct.unpack_from("<II", data, base + 64)
        raw = data[base + 72:base + 72 + 520].decode("utf-16-le", "replace")
        name = raw.split("\0", 1)[0].replace("\\", "/").strip("/")
        if not name:
            continue
        mtime = (write_time - 116444736000000000) / 1e7 if flags & FD_WRITESTIME and write_time else 0
        out.append({"rel": name, "size": (size_high << 32 | size_low) if flags & FD_FILESIZE else -1,
                    "mtime": max(0.0, mtime), "dir": bool(flags & FD_ATTRIBUTES and attrs & FILE_ATTRIBUTE_DIRECTORY),
                    "index": index})
    return out


def iter_virtual():
    """Yield ``(item, reader)`` for every virtual file on the clipboard (``reader`` is None for folders).

    Must run on one thread from start to end (OLE apartment); the reader of an item is valid until the next one."""
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes
    ole32 = ctypes.windll.ole32
    ole32.OleInitialize(None)
    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    cf_group = user32.RegisterClipboardFormatW("FileGroupDescriptorW")
    cf_contents = user32.RegisterClipboardFormatW("FileContents")

    class FORMATETC(ctypes.Structure):
        _fields_ = [("cfFormat", ctypes.c_ushort), ("ptd", ctypes.c_void_p), ("dwAspect", wintypes.DWORD),
                    ("lindex", ctypes.c_long), ("tymed", wintypes.DWORD)]

    class STGMEDIUM(ctypes.Structure):
        _fields_ = [("tymed", wintypes.DWORD), ("data", ctypes.c_void_p), ("pUnkForRelease", ctypes.c_void_p)]

    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]

    def vcall(obj, index, restype, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

    data_object = ctypes.c_void_p()
    if ole32.OleGetClipboard(ctypes.byref(data_object)) != 0 or not data_object:
        return
    get_data = vcall(data_object, 3, ctypes.c_long, ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM))   # IDataObject::GetData
    try:
        fmt = FORMATETC(cf_group, None, 1, -1, 1)                  # DVASPECT_CONTENT, TYMED_HGLOBAL
        medium = STGMEDIUM()
        if get_data(data_object, ctypes.byref(fmt), ctypes.byref(medium)) != 0:
            return
        try:
            pointer = kernel32.GlobalLock(medium.data)
            blob = ctypes.string_at(pointer, kernel32.GlobalSize(medium.data)) if pointer else b""
            kernel32.GlobalUnlock(medium.data)
        finally:
            ole32.ReleaseStgMedium(ctypes.byref(medium))
        items = parse_group(blob)
        # first a summary: the operation knows its total up front (an honest bar from the first byte)
        yield {"summary": True, "count": len(items), "bytes": sum(max(0, i["size"]) for i in items if not i["dir"])}, None
        for item in items:
            if item["dir"]:
                yield item, None
                continue
            fmt = FORMATETC(cf_contents, None, 1, item["index"], 4 | 1)    # TYMED_ISTREAM | TYMED_HGLOBAL
            medium = STGMEDIUM()
            if get_data(data_object, ctypes.byref(fmt), ctypes.byref(medium)) != 0:
                yield item, _Failed()
                continue
            try:
                if medium.tymed == 4:                                       # IStream
                    yield item, _StreamReader(medium.data, vcall)
                else:                                                       # HGLOBAL
                    pointer = kernel32.GlobalLock(medium.data)
                    size = kernel32.GlobalSize(medium.data)
                    if item["size"] >= 0:
                        size = min(size, item["size"])
                    blob = ctypes.string_at(pointer, size) if pointer else b""
                    kernel32.GlobalUnlock(medium.data)
                    import io
                    yield item, io.BytesIO(blob)
            finally:
                ole32.ReleaseStgMedium(ctypes.byref(medium))
    finally:
        vcall(data_object, 2, ctypes.c_ulong)(data_object)                 # IUnknown::Release


class _Failed:
    def read(self, size: int) -> bytes:
        raise OSError("the source app did not hand over the file")


class _StreamReader:
    """``read(n)`` over an IStream (vtable 3: Read(pv, cb, pcbRead))."""

    def __init__(self, stream: Any, vcall: Any) -> None:
        import ctypes
        self.ctypes = ctypes
        self.stream = stream
        self._read = vcall(stream, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))

    def read(self, size: int) -> bytes:
        ctypes = self.ctypes
        buffer = ctypes.create_string_buffer(size)
        got = ctypes.c_ulong(0)
        hr = self._read(self.stream, buffer, size, ctypes.byref(got))
        if hr < 0:
            raise OSError(f"IStream::Read 0x{hr & 0xFFFFFFFF:08x}")
        return buffer.raw[:got.value]
