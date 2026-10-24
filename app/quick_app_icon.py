"""Round 27 (r27-hunt-canvas): the icon of a program as a PNG ``data:`` URL — «Иконка программы» of a Quick Actions button.

``app_icon(app, lang)`` takes what the user typed in the button's «Открыть программу» step (a name as the assistant
resolves it — «телеграм», «Chrome» — or a path to an ``.exe`` / ``.lnk`` / ``.ico``) and returns
``{"ok": True, "data": "data:image/png;base64,…", "app": <display name>}`` or ``{"ok": False, "error": …}``.

Windows only, flat Win32 calls through ctypes (no COM vtables — a wrong slot would take the whole server down):

* a path (or the start-menu shortcut / App Paths target of a resolved name) → ``SHGetFileInfoW(SHGFI_ICONLOCATION)``
  gives the file and index of its icon (a shortcut's own icon included) → ``SHDefExtractIconW`` at 96 px (the
  big, alpha-blended frame of the ``.ico``/``.exe`` resource);
* a Store app (``shell:AppsFolder\\<AppID>``) or anything else → ``SHParseDisplayName`` +
  ``SHGetFileInfoW(SHGFI_PIDL | SHGFI_ICON | SHGFI_LARGEICON)`` (the shell's 32 px icon);
* ``HICON`` → ``GetIconInfo`` + ``GetDIBits`` (32-bit BGRA; old icons without alpha get it from their mask) → PNG
  written with :mod:`zlib` (no Pillow needed).

UNC paths (``\\\\server\\share``) are refused: reading an icon there would send the user's Windows credentials to that
server. The page scales nothing — the PNG is 96 px or less, far under the 96 KB picture limit.
"""
from __future__ import annotations

import base64
import os
import shutil
import struct
import sys
import zlib
from typing import Any, Dict, Optional, Tuple

ICON_SIZE = 96


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def png_data_url(width: int, height: int, rgba: bytes) -> str:
    """RGBA rows → ``data:image/png;base64,…`` (8-bit RGBA, no interlace)."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def bgra_to_rgba(bgra: bytes, mask: Optional[bytes] = None) -> bytes:
    """GDI's BGRA → RGBA. An icon without any alpha (pre-XP) takes it from its AND mask: white = transparent."""
    data = bytearray(bgra)
    data[0::4], data[2::4] = bgra[2::4], bgra[0::4]
    if not any(data[3::4]):
        if mask is not None and len(mask) == len(bgra):
            data[3::4] = bytes(0 if m else 255 for m in mask[0::4])
        else:
            data[3::4] = b"\xff" * (len(data) // 4)
    return bytes(data)


def resolve_target(app: str) -> Tuple[str, str]:
    """(target, display name): a path as given, else the assistant's app index (Start menu, App Paths, Store)."""
    value = os.path.expandvars(str(app or "").strip().strip('"'))
    if os.path.isabs(value) or value.lower().endswith((".exe", ".lnk", ".ico", ".dll")):
        if not os.path.isabs(value):
            value = shutil.which(value) or value
        return value, os.path.splitext(os.path.basename(value))[0]
    from . import assistant_apps
    found = assistant_apps.resolve(value, assistant_apps.index(wait=True))
    entry = found.get("match")
    if not entry:
        return "", ""
    target = str(entry.get("target") or "")
    if target and not os.path.isabs(target) and target.lower().endswith(".exe"):
        target = shutil.which(target) or target
    return target, str(entry.get("name") or value)


def app_icon(app: Any, lang: str = "ru") -> Dict[str, Any]:
    name = str(app or "").strip()[:1000]
    if not name:
        return {"ok": False, "error": tr(lang, "Укажите программу или путь к .exe", "Give an app or a path to an .exe")}
    if sys.platform != "win32":
        return {"ok": False, "error": tr(lang, "Иконка программы берётся в приложении MemuAI для Windows",
                                         "The app icon is read in the MemuAI Windows app")}
    try:
        target, display = resolve_target(name)
    except Exception:                                   # noqa: BLE001 - the app index is best effort
        target, display = "", ""
    if not target:
        return {"ok": False, "error": tr(lang, f"Не нашёл программу «{name}»", f"Couldn't find the app “{name}”")}
    if target.replace("/", "\\").startswith("\\\\"):
        return {"ok": False, "error": tr(lang, "Сетевые пути не поддерживаются", "Network paths are not supported")}
    try:
        data = _win_icon(target)
    except Exception:                                   # noqa: BLE001 - any GDI failure = «no icon», never a crash
        data = None
    if not data:
        return {"ok": False, "error": tr(lang, "У этой программы не удалось взять иконку", "Couldn't get this app's icon")}
    return {"ok": True, "data": data, "app": display[:120]}


# ------------------------------------------------------------------ Win32 (ctypes)

def _win_icon(target: str) -> Optional[str]:
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                    ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    SHGFI_ICON, SHGFI_LARGEICON, SHGFI_ICONLOCATION, SHGFI_PIDL = 0x100, 0x0, 0x1000, 0x8
    shell32.SHGetFileInfoW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    shell32.SHDefExtractIconW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.UINT, ctypes.POINTER(wintypes.HICON),
                                          ctypes.POINTER(wintypes.HICON), wintypes.UINT]
    shell32.SHDefExtractIconW.restype = ctypes.c_long
    shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wintypes.ULONG,
                                           ctypes.POINTER(wintypes.ULONG)]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFO), wintypes.UINT]

    # the shell needs COM on this (worker) thread; S_FALSE / RPC_E_CHANGED_MODE are fine too
    initialized = ole32.CoInitializeEx(None, 0x2) in (0, 1)
    icon = wintypes.HICON(0)
    try:
        if os.path.exists(target):
            info = SHFILEINFOW()
            if shell32.SHGetFileInfoW(target, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICONLOCATION):
                source, index = info.szDisplayName or target, info.iIcon
            else:
                source, index = target, 0
            large = wintypes.HICON(0)
            if shell32.SHDefExtractIconW(source, index, 0, ctypes.byref(large), None, ICON_SIZE | (16 << 16)) == 0 and large.value:
                icon = large
            if not icon.value:
                info = SHFILEINFOW()
                if shell32.SHGetFileInfoW(target, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON):
                    icon = wintypes.HICON(info.hIcon)
        else:                                            # shell:AppsFolder\<AppID>, a URI handler
            pidl = ctypes.c_void_p()
            if shell32.SHParseDisplayName(target, None, ctypes.byref(pidl), 0, None) == 0 and pidl.value:
                try:
                    info = SHFILEINFOW()
                    if shell32.SHGetFileInfoW(pidl, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_PIDL | SHGFI_ICON | SHGFI_LARGEICON):
                        icon = wintypes.HICON(info.hIcon)
                finally:
                    ole32.CoTaskMemFree(pidl)
        if not icon.value:
            return None
        return _icon_png(icon, user32, gdi32, ICONINFO, BITMAP, BITMAPINFO, BITMAPINFOHEADER)
    finally:
        if icon.value:
            user32.DestroyIcon(icon)
        if initialized:
            ole32.CoUninitialize()


def _icon_png(icon, user32, gdi32, ICONINFO, BITMAP, BITMAPINFO, BITMAPINFOHEADER) -> Optional[str]:
    import ctypes
    ii = ICONINFO()
    if not user32.GetIconInfo(icon, ctypes.byref(ii)):
        return None
    dc = gdi32.CreateCompatibleDC(None)
    try:
        if not ii.hbmColor:
            return None                                  # a monochrome cursor-style icon: nothing worth a button
        bm = BITMAP()
        gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = int(bm.bmWidth), abs(int(bm.bmHeight))
        if not (0 < w <= 512 and 0 < h <= 512):
            return None

        def bits(hbm) -> Optional[bytes]:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h          # top-down rows
            bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
            buf = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(dc, hbm, 0, h, buf, ctypes.byref(bmi), 0) != h:
                return None
            return buf.raw
        color = bits(ii.hbmColor)
        if color is None:
            return None
        mask = bits(ii.hbmMask) if ii.hbmMask else None
        return png_data_url(w, h, bgra_to_rgba(color, mask))
    finally:
        gdi32.DeleteDC(dc)
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)
