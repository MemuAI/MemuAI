"""Copy text to the system clipboard without extra dependencies."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys

def copy_text(text: str) -> bool:
    if os.name == "nt":
        if _windows_copy(text):
            return True
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text.encode("utf-8"), timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    try:

        sys.stdout.write("\x1b]52;c;" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "\x07")
        sys.stdout.flush()
        return True
    except Exception:
        return False

CF_UNICODETEXT, CF_DIB, CF_HDROP, CF_DIBV5 = 13, 8, 15, 17

def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.RegisterClipboardFormatW.argtypes = (wintypes.LPCWSTR,)
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    return user32

def sequence_number() -> int:
    """Changes whenever anything is copied (0 where it is not available)."""
    if os.name != "nt":
        return 0
    try:
        return int(_user32().GetClipboardSequenceNumber())
    except Exception:
        return 0

def has_image() -> bool:
    """Is there a picture on the clipboard (a screenshot, a copied image)? Cheap: nothing is read."""
    if os.name != "nt":
        return False
    try:
        user32 = _user32()
        if user32.IsClipboardFormatAvailable(CF_DIB) or user32.IsClipboardFormatAvailable(CF_DIBV5):
            return True
        png = user32.RegisterClipboardFormatW("PNG")
        return bool(png and user32.IsClipboardFormatAvailable(png))
    except Exception:
        return False

def has_files() -> bool:
    """Files copied in Explorer."""
    if os.name != "nt":
        return False
    try:
        return bool(_user32().IsClipboardFormatAvailable(CF_HDROP))
    except Exception:
        return False

def read_text() -> str:
    """Text from the clipboard, without starting a shell (PowerShell takes ~0.5 s)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = _user32()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalLock.restype = wintypes.LPVOID
            kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            import time
            for _attempt in range(6):
                if user32.OpenClipboard(None):
                    break
                time.sleep(0.03)
            else:
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    return ""
                try:
                    return ctypes.wstring_at(pointer)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            return ""
    for command in (["pbpaste"], ["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            try:
                return subprocess.run(command, capture_output=True, timeout=5).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.SubprocessError):
                continue
    return ""

def _windows_copy(text: str) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = (wintypes.HWND,)
        user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
        user32.SetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
        data = text.encode("utf-16-le") + b"\x00\x00"
        for _attempt in range(5):
            if user32.OpenClipboard(None):
                break
            import time
            time.sleep(0.05)
        else:
            return False
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(0x0002, len(data))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(13, handle):
                return False
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False
