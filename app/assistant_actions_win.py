"""Windows implementations of the Quick Ask assistant actions (Round 22).

Everything goes through :class:`WinBackend`. Rules followed in this file:

* every ctypes function gets ``argtypes``/``restype`` (64-bit handles are
  ``c_void_p``/``HWND``, never ``int`` truncated to 32 bits);
* every public method catches its own failures and raises :class:`ActionError`
  with a human message — a broken API never escapes as a crash, and importing
  the module never touches Windows (all bindings are created lazily);
* COM is initialised per call on the worker thread that runs it (the engine
  runs blocking actions with ``asyncio.to_thread``);
* window geometry is handled with the thread temporarily switched to
  per-monitor-v2 DPI awareness, so rectangles are physical pixels on every
  monitor (negative coordinates included).

On non-Windows systems :class:`NullBackend` answers ``unsupported`` for
OS actions; tests use their own fake backend.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import assistant_layout as geo

log = logging.getLogger("memuai.assistant.win")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


class ActionError(RuntimeError):
    """A failure with a message fit for the user (``code`` for the UI)."""

    def __init__(self, message: str, code: str = "failed", en: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.en = en

    def text(self, lang: str = "ru") -> str:
        """The message in the interface language (``en`` when given, else the Russian one)."""
        return self.en if lang == "en" and self.en else str(self)


class Unsupported(ActionError):
    def __init__(self, message: str = "", en: str = "") -> None:
        super().__init__(message or "Это действие доступно только в Windows-приложении MemuAI.", "unsupported",
                         en or ("" if message else "This action works only in the MemuAI Windows app."))


# ------------------------------------------------------------------ key names (pure, tested)

VK = {
    "ctrl": 0x11, "control": 0x11, "ctl": 0x11, "alt": 0x12, "menu": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
    "lwin": 0x5B, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "bksp": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "printscreen": 0x2C, "prtsc": 0x2C, "capslock": 0x14, "numlock": 0x90,
    "scrolllock": 0x91, "pause": 0x13, "apps": 0x5D, "contextmenu": 0x5D,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF, "medianext": 0xB0, "mediaprev": 0xB1,
    "mediastop": 0xB2, "mediaplaypause": 0xB3, "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "+": 0xBB, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, "`": 0xC0, "=": 0xBB,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    VK[_c] = ord(_c.upper())
# Keys on the extended part of the keyboard (KEYEVENTF_EXTENDEDKEY), as in automation_runtime._EXTENDED_VK:
# NumLock (Microsoft's own keybd_event sample sets the flag for it), right Win and numpad «/» included.
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5C, 0x5D, 0x2C, 0x6F, 0x90,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def text_key_plan(text: str) -> List[Tuple[str, int]]:
    """How :meth:`WinBackend.type_text` types ``text``: ``("vk", code)`` for Enter/Tab (many apps ignore a Unicode
    "\\n" or "\\t"), ``("unit", utf16)`` for everything else. "\\r\\n" is one Enter. Characters outside the BMP are
    a surrogate pair (KEYEVENTF_UNICODE takes UTF-16 code units); a lone surrogate (from broken JSON) is passed
    through instead of raising UnicodeEncodeError and dropping the whole text."""
    plan: List[Tuple[str, int]] = []
    previous = ""
    for char in text:
        if char == "\n":
            if previous != "\r":
                plan.append(("vk", 0x0D))
        elif char == "\r":
            plan.append(("vk", 0x0D))
        elif char == "\t":
            plan.append(("vk", 0x09))
        else:
            data = char.encode("utf-16-le", "surrogatepass")
            plan.extend(("unit", int.from_bytes(data[i:i + 2], "little")) for i in range(0, len(data), 2))
        previous = char
    return plan
_KEY_ALIASES_RU = {"контрол": "ctrl", "ктрл": "ctrl", "альт": "alt", "шифт": "shift", "вин": "win", "ввод": "enter",
                   "энтер": "enter", "пробел": "space", "таб": "tab", "эскейп": "esc", "удалить": "delete"}
BLOCKED_COMBOS = {("ctrl", "alt", "delete")}
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2}


def parse_keys(text: str) -> List[int]:
    """``"Ctrl+Shift+T"`` → ``[0x11, 0x10, 0x54]``; raises ActionError for unknown keys."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", str(text or "").strip()) if p.strip()]
    if not parts or len(parts) > 5:
        raise ActionError("Укажите сочетание клавиш, например Ctrl+Shift+T", "bad_params",
                          "Give a key combination, for example Ctrl+Shift+T")
    names = [_KEY_ALIASES_RU.get(p, p) for p in parts]
    if tuple(sorted(set(names))) in {tuple(sorted(c)) for c in BLOCKED_COMBOS}:
        raise ActionError("Ctrl+Alt+Delete нельзя нажать программно — это защищённое сочетание Windows.", "blocked",
                          "Ctrl+Alt+Delete cannot be pressed by a program — Windows protects this combination.")
    out = []
    for name in names:
        if name not in VK:
            raise ActionError(f"Неизвестная клавиша: {name}", "bad_params", f"Unknown key: {name}")
        out.append(VK[name])
    return out


# ------------------------------------------------------------------ helpers

def run_powershell(script: str, timeout: float = 20.0) -> Tuple[int, str, str]:
    if not IS_WINDOWS:
        raise Unsupported()
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; " + script
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", full], capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ActionError("PowerShell не ответил вовремя", "timeout", "PowerShell did not answer in time") from None
    except OSError as exc:
        raise ActionError(f"PowerShell недоступен: {exc}", en=f"PowerShell is unavailable: {exc}") from None
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip(), proc.stderr.decode("utf-8", "replace").strip()


def _run(args: List[str], timeout: float = 15.0) -> Tuple[int, str]:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
    except subprocess.TimeoutExpired:
        raise ActionError(f"{args[0]} не ответил вовремя", "timeout", f"{args[0]} did not answer in time") from None
    except OSError as exc:
        raise ActionError(str(exc)) from None
    raw = proc.stdout + proc.stderr
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return proc.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return proc.returncode, raw.decode("utf-8", "replace")


RADIO_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($WinRtTask, $ResultType) { $asTask = $asTaskGeneric.MakeGenericMethod($ResultType); $netTask = $asTask.Invoke($null, @($WinRtTask)); $netTask.Wait(-1) | Out-Null; $netTask.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$radio = $radios | Where-Object { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $radio) { Write-Output '{"ok":false,"code":"no_radio"}'; exit 0 }
$before = [string]$radio.State
if ('__STATE__' -ne '') { $res = Await ($radio.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus]); $res = [string]$res } else { $res = 'Allowed' }
Write-Output (@{ok=($res -eq 'Allowed'); before=$before; result=$res; access=[string]$access} | ConvertTo-Json -Compress)
"""

PROTECTED_PROCESSES = {"system", "system idle process", "idle", "csrss.exe", "wininit.exe", "winlogon.exe", "smss.exe",
                       "lsass.exe", "services.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "lsaiso.exe",
                       "registry", "memory compression", "secure system", "audiodg.exe", "msmpeng.exe",
                       "memuai.exe", "memuaiproxy.exe", "sihost.exe", "ctfmon.exe", "conhost.exe"}


def known_folder(name: str) -> str:
    home = os.path.expanduser("~")
    fallback = {"downloads": os.path.join(home, "Downloads"), "documents": os.path.join(home, "Documents"),
                "desktop": os.path.join(home, "Desktop"), "pictures": os.path.join(home, "Pictures"),
                "music": os.path.join(home, "Music"), "videos": os.path.join(home, "Videos"), "home": home,
                "appdata": os.environ.get("APPDATA", home), "temp": os.environ.get("TEMP", "/tmp")}
    reg = {"downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "documents": "Personal", "desktop": "Desktop",
           "pictures": "My Pictures", "music": "My Music", "videos": "My Video"}
    if name == "screenshots":
        return os.path.join(known_folder("pictures"), "Screenshots")
    if name == "memuai":
        from .config import DATA_DIR
        return str(DATA_DIR)
    if IS_WINDOWS and name in reg:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, reg[name])
                path = os.path.expandvars(str(value))
                if path:
                    return path
        except OSError:
            pass
    return fallback.get(name, home)


# ------------------------------------------------------------------ backend API

class Backend:
    """Interface (and the non-Windows behaviour): OS actions raise :class:`Unsupported`."""
    name = "null"

    def _no(self, *_a, **_k):
        raise Unsupported()

    open_target = launch_app = windows = foreground = focus_window = close_window = window_cmd = _no
    place_window = monitors = volume_get = volume_set = volume_mute = media_key = send_keys = type_text = _no
    brightness_get = brightness_set = theme_get = theme_set = radio = screenshot = clipboard_get = _no
    clipboard_set = recycle_info = recycle_empty = sysinfo = processes = kill = lock = sleep = hibernate = _no
    shutdown = restart = signout = cancel_shutdown = powershell = app_index = _no
    # Round 33 (r33-quick): processes without a visible window (tray apps) — assistant_procs_win.py
    procs = pid_alive = close_pid = _no

    def own_pid(self) -> int:
        return os.getpid()


class NullBackend(Backend):
    pass


# ------------------------------------------------------------------ Win32 bindings

class _W:
    """Lazily created ctypes bindings (only on Windows, only when first needed)."""
    _inst: Optional["_W"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "_W":
        if not IS_WINDOWS:
            raise Unsupported()
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def __init__(self) -> None:
        from ctypes import wintypes as wt
        self.wt = wt
        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        k = self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        try:
            self.dwmapi = ctypes.WinDLL("dwmapi")
        except OSError:
            self.dwmapi = None
        try:
            self.shcore = ctypes.WinDLL("shcore")
        except OSError:
            self.shcore = None
        try:
            self.dxva2 = ctypes.WinDLL("dxva2")
        except OSError:
            self.dxva2 = None
        try:
            self.powrprof = ctypes.WinDLL("powrprof")
        except OSError:
            self.powrprof = None

        HWND, LPARAM, WPARAM, BOOL, UINT, DWORD = wt.HWND, wt.LPARAM, wt.WPARAM, wt.BOOL, wt.UINT, wt.DWORD
        self.RECT, self.POINT = wt.RECT, wt.POINT

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", DWORD),
                        ("szDevice", wt.WCHAR * 32)]

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", UINT), ("flags", UINT), ("showCmd", UINT), ("ptMinPosition", wt.POINT),
                        ("ptMaxPosition", wt.POINT), ("rcNormalPosition", wt.RECT)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", DWORD), ("time", DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", DWORD), ("dwFlags", DWORD),
                        ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", DWORD), ("u", _U)]

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", DWORD), ("dwMemoryLoad", DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", wt.BYTE), ("BatteryFlag", wt.BYTE), ("BatteryLifePercent", wt.BYTE),
                        ("SystemStatusFlag", wt.BYTE), ("BatteryLifeTime", DWORD), ("BatteryFullLifeTime", DWORD)]

        class SHQUERYRBINFO(ctypes.Structure):
            # shellapi.h packs to 1 byte on 32-bit only
            if ctypes.sizeof(ctypes.c_void_p) == 4:
                _pack_ = 1
            _fields_ = [("cbSize", DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

        class PHYSICAL_MONITOR(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("hPhysicalMonitor", wt.HANDLE), ("szPhysicalMonitorDescription", wt.WCHAR * 128)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        self.MONITORINFOEXW, self.WINDOWPLACEMENT, self.INPUT, self.KEYBDINPUT = MONITORINFOEXW, WINDOWPLACEMENT, INPUT, KEYBDINPUT
        self.MEMORYSTATUSEX, self.SYSTEM_POWER_STATUS, self.SHQUERYRBINFO = MEMORYSTATUSEX, SYSTEM_POWER_STATUS, SHQUERYRBINFO
        self.PHYSICAL_MONITOR, self.GUID = PHYSICAL_MONITOR, GUID

        self.WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
        self.MONITORENUMPROC = ctypes.WINFUNCTYPE(BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), LPARAM)

        def sig(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.EnumWindows = sig(u, "EnumWindows", BOOL, self.WNDENUMPROC, LPARAM)
        self.IsWindowVisible = sig(u, "IsWindowVisible", BOOL, HWND)
        self.IsIconic = sig(u, "IsIconic", BOOL, HWND)
        self.IsZoomed = sig(u, "IsZoomed", BOOL, HWND)
        self.IsWindow = sig(u, "IsWindow", BOOL, HWND)
        self.GetWindowTextLengthW = sig(u, "GetWindowTextLengthW", ctypes.c_int, HWND)
        self.GetWindowTextW = sig(u, "GetWindowTextW", ctypes.c_int, HWND, wt.LPWSTR, ctypes.c_int)
        self.GetClassNameW = sig(u, "GetClassNameW", ctypes.c_int, HWND, wt.LPWSTR, ctypes.c_int)
        self.GetWindowThreadProcessId = sig(u, "GetWindowThreadProcessId", DWORD, HWND, ctypes.POINTER(DWORD))
        self.GetWindowRect = sig(u, "GetWindowRect", BOOL, HWND, ctypes.POINTER(wt.RECT))
        self.GetForegroundWindow = sig(u, "GetForegroundWindow", HWND)
        self.SetForegroundWindow = sig(u, "SetForegroundWindow", BOOL, HWND)
        self.BringWindowToTop = sig(u, "BringWindowToTop", BOOL, HWND)
        self.ShowWindow = sig(u, "ShowWindow", BOOL, HWND, ctypes.c_int)
        self.SetWindowPos = sig(u, "SetWindowPos", BOOL, HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, UINT)
        self.GetWindowPlacement = sig(u, "GetWindowPlacement", BOOL, HWND, ctypes.POINTER(WINDOWPLACEMENT))
        self.SetWindowPlacement = sig(u, "SetWindowPlacement", BOOL, HWND, ctypes.POINTER(WINDOWPLACEMENT))
        self.PostMessageW = sig(u, "PostMessageW", BOOL, HWND, UINT, WPARAM, LPARAM)
        self.GetWindow = sig(u, "GetWindow", HWND, HWND, UINT)
        self.GetAncestor = sig(u, "GetAncestor", HWND, HWND, UINT)
        self.GetWindowLongPtrW = sig(u, "GetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "GetWindowLongW",
                                     ctypes.c_ssize_t, HWND, ctypes.c_int)
        self.EnumDisplayMonitors = sig(u, "EnumDisplayMonitors", BOOL, wt.HDC, ctypes.POINTER(wt.RECT),
                                       self.MONITORENUMPROC, LPARAM)
        self.GetMonitorInfoW = sig(u, "GetMonitorInfoW", BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
        self.MonitorFromWindow = sig(u, "MonitorFromWindow", wt.HMONITOR, HWND, DWORD)
        self.SendInput = sig(u, "SendInput", UINT, UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.LockWorkStation = sig(u, "LockWorkStation", BOOL)
        self.ExitWindowsEx = sig(u, "ExitWindowsEx", BOOL, UINT, DWORD)
        self.OpenClipboard = sig(u, "OpenClipboard", BOOL, HWND)
        self.CloseClipboard = sig(u, "CloseClipboard", BOOL)
        self.EmptyClipboard = sig(u, "EmptyClipboard", BOOL)
        self.GetClipboardData = sig(u, "GetClipboardData", wt.HANDLE, UINT)
        self.SetClipboardData = sig(u, "SetClipboardData", wt.HANDLE, UINT, wt.HANDLE)
        self.IsClipboardFormatAvailable = sig(u, "IsClipboardFormatAvailable", BOOL, UINT)
        self.SendMessageTimeoutW = sig(u, "SendMessageTimeoutW", ctypes.c_ssize_t, HWND, UINT, WPARAM, ctypes.c_wchar_p,
                                       UINT, UINT, ctypes.POINTER(ctypes.c_size_t))
        self.GetSystemMetrics = sig(u, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        self.AllowSetForegroundWindow = sig(u, "AllowSetForegroundWindow", BOOL, DWORD)
        try:
            self.SetThreadDpiAwarenessContext = sig(u, "SetThreadDpiAwarenessContext", ctypes.c_void_p, ctypes.c_void_p)
        except AttributeError:
            self.SetThreadDpiAwarenessContext = None

        self.OpenProcess = sig(k, "OpenProcess", wt.HANDLE, DWORD, BOOL, DWORD)
        self.CloseHandle = sig(k, "CloseHandle", BOOL, wt.HANDLE)
        self.QueryFullProcessImageNameW = sig(k, "QueryFullProcessImageNameW", BOOL, wt.HANDLE, DWORD, wt.LPWSTR,
                                              ctypes.POINTER(DWORD))
        self.GlobalAlloc = sig(k, "GlobalAlloc", wt.HGLOBAL, UINT, ctypes.c_size_t)
        self.GlobalFree = sig(k, "GlobalFree", wt.HGLOBAL, wt.HGLOBAL)
        self.GlobalLock = sig(k, "GlobalLock", ctypes.c_void_p, wt.HGLOBAL)
        self.GlobalUnlock = sig(k, "GlobalUnlock", BOOL, wt.HGLOBAL)
        self.GlobalSize = sig(k, "GlobalSize", ctypes.c_size_t, wt.HGLOBAL)
        self.GlobalMemoryStatusEx = sig(k, "GlobalMemoryStatusEx", BOOL, ctypes.POINTER(MEMORYSTATUSEX))
        self.GetSystemPowerStatus = sig(k, "GetSystemPowerStatus", BOOL, ctypes.POINTER(SYSTEM_POWER_STATUS))
        self.GetTickCount64 = sig(k, "GetTickCount64", ctypes.c_ulonglong)
        self.GetSystemTimes = sig(k, "GetSystemTimes", BOOL, ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME),
                                  ctypes.POINTER(wt.FILETIME))
        self.GetLogicalDrives = sig(k, "GetLogicalDrives", DWORD)
        self.GetDriveTypeW = sig(k, "GetDriveTypeW", UINT, wt.LPCWSTR)
        self.GetCurrentProcessId = sig(k, "GetCurrentProcessId", DWORD)

        self.SHQueryRecycleBinW = sig(self.shell32, "SHQueryRecycleBinW", ctypes.c_long, wt.LPCWSTR,
                                      ctypes.POINTER(SHQUERYRBINFO))
        self.SHEmptyRecycleBinW = sig(self.shell32, "SHEmptyRecycleBinW", ctypes.c_long, HWND, wt.LPCWSTR, DWORD)
        self.CoInitializeEx = sig(self.ole32, "CoInitializeEx", ctypes.c_long, ctypes.c_void_p, DWORD)
        self.CoUninitialize = sig(self.ole32, "CoUninitialize", None)
        self.CoCreateInstance = sig(self.ole32, "CoCreateInstance", ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_void_p,
                                    DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        if self.dwmapi is not None:
            self.DwmGetWindowAttribute = sig(self.dwmapi, "DwmGetWindowAttribute", ctypes.c_long, HWND, DWORD,
                                             ctypes.c_void_p, DWORD)
        if self.shcore is not None:
            self.GetDpiForMonitor = sig(self.shcore, "GetDpiForMonitor", ctypes.c_long, wt.HMONITOR, ctypes.c_int,
                                        ctypes.POINTER(UINT), ctypes.POINTER(UINT))
        if self.dxva2 is not None:
            self.GetNumberOfPhysicalMonitorsFromHMONITOR = sig(self.dxva2, "GetNumberOfPhysicalMonitorsFromHMONITOR",
                                                                BOOL, wt.HMONITOR, ctypes.POINTER(DWORD))
            self.GetPhysicalMonitorsFromHMONITOR = sig(self.dxva2, "GetPhysicalMonitorsFromHMONITOR", BOOL, wt.HMONITOR,
                                                       DWORD, ctypes.POINTER(PHYSICAL_MONITOR))
            self.GetMonitorBrightness = sig(self.dxva2, "GetMonitorBrightness", BOOL, wt.HANDLE, ctypes.POINTER(DWORD),
                                            ctypes.POINTER(DWORD), ctypes.POINTER(DWORD))
            self.SetMonitorBrightness = sig(self.dxva2, "SetMonitorBrightness", BOOL, wt.HANDLE, DWORD)
            self.DestroyPhysicalMonitors = sig(self.dxva2, "DestroyPhysicalMonitors", BOOL, DWORD,
                                               ctypes.POINTER(PHYSICAL_MONITOR))
        if self.powrprof is not None:
            self.SetSuspendState = sig(self.powrprof, "SetSuspendState", ctypes.c_ubyte, ctypes.c_ubyte,
                                       ctypes.c_ubyte, ctypes.c_ubyte)

    def guid(self, text: str):
        return self.GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    @contextlib.contextmanager
    def per_monitor_dpi(self):
        previous = None
        if self.SetThreadDpiAwarenessContext is not None:
            try:
                previous = self.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
            except Exception:
                previous = None
        try:
            yield
        finally:
            if previous:
                try:
                    self.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
                except Exception:
                    pass


# ------------------------------------------------------------------ COM: Core Audio

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
RPC_E_CHANGED_MODE = -2147417850


def _vcall(ptr: ctypes.c_void_p, index: int, restype, *args_types):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *args_types)
    fn = proto(vtbl[index])
    return lambda *a: fn(ptr, *a)


def _release(ptr: ctypes.c_void_p) -> None:
    if ptr and ptr.value:
        try:
            _vcall(ptr, 2, ctypes.c_ulong)()
        except Exception:
            pass


@contextlib.contextmanager
def _com(w: _W):
    hr = w.CoInitializeEx(None, 0)          # COINIT_MULTITHREADED
    try:
        yield
    finally:
        if hr in (0, 1):                     # S_OK / S_FALSE: balance it
            w.CoUninitialize()


@contextlib.contextmanager
def _endpoint_volume(w: _W):
    with _com(w):
        enum = ctypes.c_void_p()
        device = ctypes.c_void_p()
        volume = ctypes.c_void_p()
        try:
            hr = w.CoCreateInstance(ctypes.byref(w.guid(CLSID_MMDeviceEnumerator)), None, 0x17,
                                    ctypes.byref(w.guid(IID_IMMDeviceEnumerator)), ctypes.byref(enum))
            if hr != 0 or not enum.value:
                raise ActionError(f"Аудиоустройство недоступно (0x{hr & 0xFFFFFFFF:08X})",
                                  en=f"The audio device is unavailable (0x{hr & 0xFFFFFFFF:08X})")
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eMultimedia, &device)
            hr = _vcall(enum, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                0, 1, ctypes.byref(device))
            if hr != 0 or not device.value:
                raise ActionError("Нет устройства воспроизведения по умолчанию", "no_device", "No default playback device")
            # IMMDevice::Activate(IID_IAudioEndpointVolume, CLSCTX_ALL, NULL, &volume)
            hr = _vcall(device, 3, ctypes.c_long, ctypes.POINTER(w.GUID), ctypes.c_uint32, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(w.guid(IID_IAudioEndpointVolume)), 0x17, None,
                                                         ctypes.byref(volume))
            if hr != 0 or not volume.value:
                raise ActionError("Регулятор громкости недоступен", en="The volume control is unavailable")
            yield volume
        finally:
            _release(volume)
            _release(device)
            _release(enum)


# ------------------------------------------------------------------ the backend

class WinBackend(Backend):
    name = "windows"

    def __init__(self) -> None:
        self._w: Optional[_W] = None

    @property
    def w(self) -> _W:
        if self._w is None:
            self._w = _W.get()
        return self._w

    # ---------------------------------------------------------- opening things
    def open_target(self, target: str) -> None:
        try:
            if target.lower().startswith("shell:appsfolder\\"):
                subprocess.Popen(["explorer.exe", target], creationflags=CREATE_NO_WINDOW)
            else:
                os.startfile(target)            # type: ignore[attr-defined]
        except OSError as exc:
            raise ActionError(f"Не удалось открыть: {exc}", en=f"Could not open: {exc}") from None

    def launch_app(self, entry: Dict[str, Any]) -> None:
        target = str(entry.get("target") or "")
        if not target:
            raise ActionError("Не знаю, как запустить это приложение", en="I don't know how to start this app")
        if target == "memuai:":
            raise ActionError("MemuAI уже запущен", "noop", "MemuAI is already running")
        self.open_target(target)

    def app_index(self) -> List[Dict[str, Any]]:
        from . import assistant_apps
        return assistant_apps.index(wait=False)

    # ---------------------------------------------------------- windows
    def _title(self, hwnd) -> str:
        w = self.w
        length = w.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        w.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _class(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.w.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _pid(self, hwnd) -> int:
        pid = self.w.wt.DWORD()
        self.w.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _exe_path(self, pid: int) -> str:
        w = self.w
        handle = w.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = w.wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if w.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            w.CloseHandle(handle)

    def _cloaked(self, hwnd) -> bool:
        w = self.w
        if w.dwmapi is None:
            return False
        value = ctypes.c_int(0)
        try:
            w.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))   # DWMWA_CLOAKED
        except Exception:
            return False
        return bool(value.value)

    def _visible_rect(self, hwnd) -> Tuple[int, int, int, int]:
        w = self.w
        rect = w.RECT()
        if w.dwmapi is not None:
            try:
                if w.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:   # EXTENDED_FRAME_BOUNDS
                    return rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                pass
        w.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def _window_rect(self, hwnd) -> Tuple[int, int, int, int]:
        rect = self.w.RECT()
        self.w.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def _placement(self, hwnd):
        wp = self.w.WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(wp)
        self.w.GetWindowPlacement(hwnd, ctypes.byref(wp))
        return wp

    def _is_app_window(self, hwnd) -> bool:
        w = self.w
        if not w.IsWindowVisible(hwnd) or self._cloaked(hwnd):
            return False
        if w.GetWindow(hwnd, 4):          # GW_OWNER: owned popups are not app windows
            return False
        ex = w.GetWindowLongPtrW(hwnd, -20)   # GWL_EXSTYLE
        if ex & 0x00000080 and not ex & 0x00040000:     # TOOLWINDOW without APPWINDOW
            return False
        if not self._title(hwnd):
            return False
        cls = self._class(hwnd)
        if cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"):
            return False
        return True

    def windows(self, include_minimized: bool = True) -> List[Dict[str, Any]]:
        w = self.w
        out: List[Dict[str, Any]] = []
        handles: List[int] = []

        @w.WNDENUMPROC
        def collect(hwnd, _lparam):
            handles.append(hwnd)
            return True

        with w.per_monitor_dpi():
            w.EnumWindows(collect, 0)
            own = os.getpid()
            mons = self.monitors()
            for hwnd in handles:
                try:
                    if not self._is_app_window(hwnd):
                        continue
                    pid = self._pid(hwnd)
                    title = self._title(hwnd)
                    if pid == own and ("Quick" in title or "MemuAI Quick" in title):
                        continue
                    iconic = bool(w.IsIconic(hwnd))
                    if iconic and not include_minimized:
                        continue
                    path = self._exe_path(pid)
                    wp = self._placement(hwnd)
                    rc = wp.rcNormalPosition
                    state = "minimized" if iconic else ("maximized" if w.IsZoomed(hwnd) else "normal")
                    visible = self._visible_rect(hwnd)
                    normal = (rc.left, rc.top, rc.right, rc.bottom)
                    if state != "normal":
                        # rcNormalPosition is in workspace coordinates of its monitor
                        mon = mons[geo.monitor_for_rect(normal, mons)] if mons else None
                        if mon:
                            dx, dy = mon["work"][0] - mon["rect"][0], mon["work"][1] - mon["rect"][1]
                            normal = (normal[0] + dx, normal[1] + dy, normal[2] + dx, normal[3] + dy)
                    out.append({"hwnd": int(hwnd), "title": title, "pid": pid, "path": path,
                                "exe": os.path.basename(path).lower(), "app": os.path.splitext(os.path.basename(path))[0],
                                "class": self._class(hwnd), "state": state,
                                "rect": list(visible if state == "normal" else normal),
                                "normal_rect": list(normal), "own": pid == own})
                except Exception as exc:
                    log.debug("window %s skipped: %s", hwnd, exc)
        return out

    def _monitor_of(self, rect) -> Optional[Dict[str, Any]]:
        mons = self.monitors()
        if not mons:
            return None
        return mons[geo.monitor_for_rect(tuple(rect), mons)]

    def foreground(self) -> Optional[Dict[str, Any]]:
        """The window the user works in: the foreground one, skipping MemuAI's own popup."""
        w = self.w
        fg = w.GetForegroundWindow()
        own = os.getpid()
        rows = self.windows(include_minimized=False)
        for row in rows:
            if fg and row["hwnd"] == int(fg) and row["pid"] != own:
                return row
        # z-order: EnumWindows walks top → bottom
        for row in rows:
            if row["pid"] != own:
                return row
        return None

    def focus_window(self, hwnd: int) -> None:
        w = self.w
        handle = w.wt.HWND(hwnd)
        if w.IsIconic(handle):
            w.ShowWindow(handle, 9)               # SW_RESTORE
        if not w.SetForegroundWindow(handle):
            # The classic workaround: a synthetic Alt press unlocks SetForegroundWindow.
            self._keys([0x12], press_only=True)
            w.SetForegroundWindow(handle)
        w.BringWindowToTop(handle)

    def close_window(self, hwnd: int) -> None:
        self.w.PostMessageW(self.w.wt.HWND(hwnd), 0x0010, 0, 0)     # WM_CLOSE

    def window_cmd(self, hwnd: int, action: str) -> None:
        w = self.w
        handle = w.wt.HWND(hwnd)
        if action == "maximize":
            w.ShowWindow(handle, 3)
        elif action == "minimize":
            w.ShowWindow(handle, 6)
        elif action == "restore":
            w.ShowWindow(handle, 9)
        elif action == "close":
            self.close_window(hwnd)
        elif action in ("topmost", "untopmost"):
            w.SetWindowPos(handle, w.wt.HWND(-1 if action == "topmost" else -2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        else:
            raise ActionError(f"Неизвестное действие с окном: {action}", "bad_params", f"Unknown window action: {action}")

    def place_window(self, hwnd: int, rect, state: str = "normal") -> None:
        """Put the *visible* frame at ``rect`` (physical px), then apply ``state``."""
        w = self.w
        handle = w.wt.HWND(hwnd)
        with w.per_monitor_dpi():
            if w.IsIconic(handle) or w.IsZoomed(handle):
                w.ShowWindow(handle, 1)            # SW_SHOWNORMAL — only a normal window can be moved
                time.sleep(0.05)
            for _attempt in range(2):
                # twice: crossing to a monitor with another DPI makes the app rescale itself after the first move
                offsets = geo.frame_offsets(self._window_rect(handle), self._visible_rect(handle))
                left, top, right, bottom = geo.expand(tuple(int(v) for v in rect), offsets)
                w.SetWindowPos(handle, None, left, top, right - left, bottom - top, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE
                time.sleep(0.08)
            if state == "maximized":
                w.ShowWindow(handle, 3)
            elif state == "minimized":
                w.ShowWindow(handle, 7)            # SW_SHOWMINNOACTIVE

    def monitors(self) -> List[Dict[str, Any]]:
        w = self.w
        out: List[Dict[str, Any]] = []

        @w.MONITORENUMPROC
        def collect(hmon, _hdc, _rect, _lparam):
            info = w.MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if w.GetMonitorInfoW(hmon, ctypes.byref(info)):
                dpi = 96
                if w.shcore is not None:
                    x, y = w.wt.UINT(), w.wt.UINT()
                    try:
                        if w.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                            dpi = int(x.value) or 96
                    except Exception:
                        pass
                r, k = info.rcMonitor, info.rcWork
                out.append({"device": info.szDevice, "rect": [r.left, r.top, r.right, r.bottom],
                            "work": [k.left, k.top, k.right, k.bottom], "dpi": dpi, "primary": bool(info.dwFlags & 1),
                            "handle": hmon})
            return True

        with w.per_monitor_dpi():
            w.EnumDisplayMonitors(None, None, collect, 0)
        out.sort(key=lambda m: (not m["primary"], m["rect"][0], m["rect"][1]))
        for i, mon in enumerate(out):
            mon["index"] = i
        return out

    # ---------------------------------------------------------- keyboard
    def _send(self, inputs) -> None:
        w = self.w
        count = len(inputs)
        if not count:
            return
        array = (w.INPUT * count)(*inputs)
        sent = w.SendInput(count, array, ctypes.sizeof(w.INPUT))
        if sent != count:
            raise ActionError("Windows не принял нажатия клавиш (окно с правами администратора не принимает ввод "
                              "от обычных программ)", "input_blocked",
                              "Windows rejected the key presses (a window running as administrator does not accept "
                              "input from regular programs)")

    def _key(self, vk: int, up: bool = False):
        w = self.w
        flags = (0x0002 if up else 0) | (0x0001 if vk in EXTENDED else 0)
        item = w.INPUT()
        item.type = 1
        item.ki = w.KEYBDINPUT(vk, 0, flags, 0, 0)
        return item

    def _keys(self, vks: List[int], press_only: bool = False) -> None:
        downs = [self._key(vk) for vk in vks]
        ups = [self._key(vk, True) for vk in reversed(vks)]
        self._send(downs + ups)

    def send_keys(self, combo: str) -> None:
        self._keys(parse_keys(combo))

    def media_key(self, key: str) -> None:
        vk = MEDIA_KEYS.get(key)
        if vk is None:
            raise ActionError("Неизвестная медиаклавиша", "bad_params", "Unknown media key")
        self._keys([vk])

    def type_text(self, text: str) -> None:
        w = self.w
        items = []
        for kind, code in text_key_plan(str(text or "")[:5000]):
            if kind == "vk":
                items += [self._key(code), self._key(code, True)]
                continue
            for up in (False, True):
                item = w.INPUT()
                item.type = 1
                item.ki = w.KEYBDINPUT(0, code, 0x0004 | (0x0002 if up else 0), 0, 0)    # KEYEVENTF_UNICODE
                items.append(item)
        for i in range(0, len(items), 200):
            self._send(items[i:i + 200])
            time.sleep(0.01)

    # ---------------------------------------------------------- audio
    def volume_get(self) -> Dict[str, Any]:
        w = self.w
        try:
            with _endpoint_volume(w) as vol:
                level = ctypes.c_float()
                muted = w.wt.BOOL()
                _vcall(vol, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float))(ctypes.byref(level))
                _vcall(vol, 15, ctypes.c_long, ctypes.POINTER(w.wt.BOOL))(ctypes.byref(muted))
                return {"level": int(round(level.value * 100)), "mute": bool(muted.value)}
        except ActionError:
            raise
        except Exception as exc:
            raise ActionError(f"Не удалось прочитать громкость: {exc}", en=f"Could not read the volume: {exc}") from None

    def volume_set(self, level: int) -> Dict[str, Any]:
        w = self.w
        level = max(0, min(100, int(level)))
        try:
            with _endpoint_volume(w) as vol:
                hr = _vcall(vol, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p)(ctypes.c_float(level / 100.0), None)
                if hr != 0:
                    raise ActionError(f"Громкость не изменилась (0x{hr & 0xFFFFFFFF:08X})",
                                      en=f"The volume did not change (0x{hr & 0xFFFFFFFF:08X})")
                if level > 0:
                    _vcall(vol, 14, ctypes.c_long, w.wt.BOOL, ctypes.c_void_p)(False, None)
        except ActionError:
            raise
        except Exception:
            # Fallback: volume keys (2 % per press) — imprecise but works without COM.
            self._send([x for _ in range(50) for x in (self._key(0xAE), self._key(0xAE, True))])
            self._send([x for _ in range(level // 2) for x in (self._key(0xAF), self._key(0xAF, True))])
        return self.volume_get()

    def volume_mute(self, mute: Optional[bool]) -> Dict[str, Any]:
        w = self.w
        try:
            with _endpoint_volume(w) as vol:
                if mute is None:
                    muted = w.wt.BOOL()
                    _vcall(vol, 15, ctypes.c_long, ctypes.POINTER(w.wt.BOOL))(ctypes.byref(muted))
                    mute = not bool(muted.value)
                _vcall(vol, 14, ctypes.c_long, w.wt.BOOL, ctypes.c_void_p)(bool(mute), None)
        except ActionError:
            raise
        except Exception:
            self._keys([0xAD])
        return self.volume_get()

    # ---------------------------------------------------------- brightness
    def _ddc_handles(self):
        w = self.w
        if w.dxva2 is None:
            return []
        result = []
        for mon in self.monitors():
            count = w.wt.DWORD()
            try:
                if not w.GetNumberOfPhysicalMonitorsFromHMONITOR(mon["handle"], ctypes.byref(count)) or not count.value:
                    continue
                array = (w.PHYSICAL_MONITOR * count.value)()
                if w.GetPhysicalMonitorsFromHMONITOR(mon["handle"], count.value, array):
                    result.append((count.value, array))
            except Exception:
                continue
        return result

    def brightness_get(self) -> Dict[str, Any]:
        displays = []
        try:
            code, out, _err = run_powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
                                            "-ErrorAction Stop | Select-Object -ExpandProperty CurrentBrightness) -join ','",
                                            timeout=8)
            for part in out.split(","):
                if part.strip().isdigit():
                    displays.append({"kind": "internal", "level": int(part)})
        except ActionError:
            pass
        w = self.w
        for count, array in self._ddc_handles():
            try:
                for i in range(count):
                    lo, cur, hi = w.wt.DWORD(), w.wt.DWORD(), w.wt.DWORD()
                    if w.GetMonitorBrightness(array[i].hPhysicalMonitor, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)):
                        span = max(1, hi.value - lo.value)
                        displays.append({"kind": "external", "name": array[i].szPhysicalMonitorDescription,
                                         "level": int(round((cur.value - lo.value) * 100 / span))})
            finally:
                w.DestroyPhysicalMonitors(count, array)
        if not displays:
            raise ActionError("Яркость этого экрана нельзя менять программно (нет WMI-подсветки и DDC/CI). "
                              "Включите DDC/CI в меню монитора.", "no_brightness",
                              "This screen's brightness cannot be changed by a program (no WMI backlight or DDC/CI). "
                              "Turn on DDC/CI in the monitor's menu.")
        return {"level": displays[0]["level"], "displays": displays}

    def brightness_set(self, level: int) -> Dict[str, Any]:
        level = max(0, min(100, int(level)))
        changed = 0
        try:
            code, _out, _err = run_powershell(
                "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop; "
                f"foreach ($x in $m) {{ Invoke-CimMethod -InputObject $x -MethodName WmiSetBrightness "
                f"-Arguments @{{Timeout=[uint32]0; Brightness=[byte]{level}}} | Out-Null }}; 'ok'", timeout=10)
            if code == 0 and "ok" in _out:
                changed += 1
        except ActionError:
            pass
        w = self.w
        for count, array in self._ddc_handles():
            try:
                for i in range(count):
                    lo, cur, hi = w.wt.DWORD(), w.wt.DWORD(), w.wt.DWORD()
                    handle = array[i].hPhysicalMonitor
                    if w.GetMonitorBrightness(handle, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)):
                        value = lo.value + int(round((hi.value - lo.value) * level / 100))
                        if w.SetMonitorBrightness(handle, value):
                            changed += 1
            finally:
                w.DestroyPhysicalMonitors(count, array)
        if not changed:
            raise ActionError("Яркость этого экрана нельзя менять программно (нет WMI-подсветки и DDC/CI).",
                              "no_brightness",
                              "This screen's brightness cannot be changed by a program (no WMI backlight or DDC/CI).")
        return {"level": level, "changed": changed}

    # ---------------------------------------------------------- theme, radios
    _THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

    def theme_get(self) -> Dict[str, Any]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._THEME_KEY) as key:
                apps, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        except OSError:
            apps = 1
        return {"dark": not bool(apps)}

    def theme_set(self, dark: bool) -> Dict[str, Any]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._THEME_KEY, 0, winreg.KEY_SET_VALUE) as key:
                for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
        except OSError as exc:
            raise ActionError(f"Не удалось изменить тему: {exc}", en=f"Could not change the theme: {exc}") from None
        result = ctypes.c_size_t()
        try:
            self.w.SendMessageTimeoutW(self.w.wt.HWND(0xFFFF), 0x001A, 0, "ImmersiveColorSet", 0x0002, 300,
                                       ctypes.byref(result))
        except Exception:
            pass
        return {"dark": bool(dark)}

    def radio(self, kind: str, on: Optional[bool]) -> Dict[str, Any]:
        state = "" if on is None else ("On" if on else "Off")
        script = RADIO_SCRIPT.replace("__KIND__", "WiFi" if kind == "wifi" else "Bluetooth").replace("__STATE__", state)
        code, out, err = run_powershell(script, timeout=20)
        try:
            data = json.loads(out.splitlines()[-1]) if out else {}
        except (ValueError, IndexError):
            data = {}
        if data.get("code") == "no_radio":
            raise ActionError("Адаптер Bluetooth не найден" if kind == "bluetooth" else "Адаптер Wi‑Fi не найден", "no_radio",
                              "No Bluetooth adapter found" if kind == "bluetooth" else "No Wi‑Fi adapter found")
        if not data.get("ok"):
            detail = f": {data.get('result')}" if data.get("result") else ""
            raise ActionError(("Windows не разрешил изменить радиомодуль" + detail)
                              if data else (err[:300] or "Не удалось изменить радиомодуль"), "denied",
                              ("Windows did not allow changing the radio" + detail)
                              if data else (err[:300] or "Could not change the radio"))
        return {"before": str(data.get("before") or ""), "on": on}

    # ---------------------------------------------------------- screenshot, clipboard
    def screenshot(self, mode: str = "screen", rect=None, copy: bool = False) -> Dict[str, Any]:
        if mode == "region":
            self.open_target("ms-screenclip:")
            return {"path": "", "region": True}
        try:
            from PIL import ImageGrab
        except Exception:
            raise ActionError("Модуль Pillow недоступен", en="The Pillow module is unavailable") from None
        with self.w.per_monitor_dpi():
            image = ImageGrab.grab(all_screens=True, include_layered_windows=True)
            if rect:
                vx, vy = self.w.GetSystemMetrics(76), self.w.GetSystemMetrics(77)     # SM_X/YVIRTUALSCREEN
                l, t, r, b = rect
                image = image.crop((l - vx, t - vy, r - vx, b - vy))
        folder = known_folder("screenshots")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "MemuAI " + datetime.now().strftime("%Y-%m-%d %H-%M-%S") + ".png")
        image.save(path, "PNG")
        if copy:
            try:
                import io
                buf = io.BytesIO()
                image.convert("RGB").save(buf, "BMP")
                self._clipboard_put(8, buf.getvalue()[14:])       # CF_DIB = BMP without the file header
            except Exception as exc:
                log.debug("screenshot to clipboard failed: %s", exc)
        return {"path": path, "width": image.width, "height": image.height}

    @contextlib.contextmanager
    def _clipboard(self):
        w = self.w
        for _ in range(20):
            if w.OpenClipboard(None):
                break
            time.sleep(0.03)
        else:
            raise ActionError("Буфер обмена занят другой программой", "busy", "Another program is using the clipboard")
        try:
            yield
        finally:
            w.CloseClipboard()

    def clipboard_get(self) -> str:
        w = self.w
        with self._clipboard():
            if not w.IsClipboardFormatAvailable(13):        # CF_UNICODETEXT
                return ""
            handle = w.GetClipboardData(13)
            if not handle:
                return ""
            pointer = w.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)[:100000]
            finally:
                w.GlobalUnlock(handle)

    def _clipboard_put(self, fmt: int, data: bytes) -> None:
        w = self.w
        handle = w.GlobalAlloc(0x0002, len(data))           # GMEM_MOVEABLE
        if not handle:
            raise ActionError("Недостаточно памяти для буфера обмена", en="Not enough memory for the clipboard")
        pointer = w.GlobalLock(handle)
        if not pointer:
            w.GlobalFree(handle)
            raise ActionError("Недостаточно памяти для буфера обмена", en="Not enough memory for the clipboard")
        ctypes.memmove(pointer, data, len(data))
        w.GlobalUnlock(handle)
        with self._clipboard():
            w.EmptyClipboard()
            if not w.SetClipboardData(fmt, handle):
                w.GlobalFree(handle)
                raise ActionError("Буфер обмена не принял данные", en="The clipboard did not accept the data")

    def clipboard_set(self, text: str) -> None:
        if not text.strip():
            with self._clipboard():
                self.w.EmptyClipboard()
            return
        self._clipboard_put(13, (str(text) + "\0").encode("utf-16-le"))

    # ---------------------------------------------------------- recycle bin
    def recycle_info(self) -> Dict[str, Any]:
        info = self.w.SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        hr = self.w.SHQueryRecycleBinW(None, ctypes.byref(info))
        if hr != 0:
            raise ActionError("Не удалось прочитать корзину", en="Could not read the Recycle Bin")
        return {"items": int(info.i64NumItems), "bytes": int(info.i64Size)}

    def recycle_empty(self) -> Dict[str, Any]:
        before = self.recycle_info()
        if before["items"] == 0:
            return {**before, "emptied": False}
        hr = self.w.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)      # NOCONFIRMATION|NOPROGRESSUI|NOSOUND
        if hr != 0:
            after = self.recycle_info()
            if after["items"]:
                raise ActionError(f"Корзина не очищена (0x{hr & 0xFFFFFFFF:08X})",
                                  en=f"The Recycle Bin was not emptied (0x{hr & 0xFFFFFFFF:08X})")
        return {**before, "emptied": True}

    # ---------------------------------------------------------- system info
    def _cpu_percent(self, interval: float = 0.3) -> float:
        w = self.w

        def sample():
            idle, kern, user = w.wt.FILETIME(), w.wt.FILETIME(), w.wt.FILETIME()
            w.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime   # noqa: E731
            return val(idle), val(kern), val(user)
        i1, k1, u1 = sample()
        time.sleep(interval)
        i2, k2, u2 = sample()
        total = (k2 - k1) + (u2 - u1)
        return round(100.0 * (total - (i2 - i1)) / total, 1) if total > 0 else 0.0

    def sysinfo(self, what: str = "all") -> Dict[str, Any]:
        w = self.w
        out: Dict[str, Any] = {}
        if what in ("all", "cpu"):
            name = ""
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                    name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            except OSError:
                pass
            out["cpu"] = {"percent": self._cpu_percent(), "name": name, "threads": os.cpu_count() or 0}
        if what in ("all", "ram"):
            mem = w.MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(mem)
            if w.GlobalMemoryStatusEx(ctypes.byref(mem)):
                out["ram"] = {"total": int(mem.ullTotalPhys), "free": int(mem.ullAvailPhys),
                              "percent": int(mem.dwMemoryLoad)}
        if what in ("all", "battery"):
            status = w.SYSTEM_POWER_STATUS()
            if w.GetSystemPowerStatus(ctypes.byref(status)):
                has = status.BatteryFlag != 128 and status.BatteryLifePercent != 255
                out["battery"] = {"present": bool(has), "percent": int(status.BatteryLifePercent) if has else None,
                                  "plugged": status.ACLineStatus == 1,
                                  "seconds_left": int(status.BatteryLifeTime) if status.BatteryLifeTime != 0xFFFFFFFF else None,
                                  "saver": bool(status.SystemStatusFlag)}
        if what in ("all", "disk"):
            disks = []
            mask = w.GetLogicalDrives()
            for i in range(26):
                if not mask & (1 << i):
                    continue
                root = f"{chr(65 + i)}:\\"
                if w.GetDriveTypeW(root) not in (2, 3):        # removable, fixed
                    continue
                try:
                    usage = shutil.disk_usage(root)
                    disks.append({"drive": root[:2], "total": usage.total, "free": usage.free})
                except OSError:
                    continue
            out["disk"] = disks
        if what in ("all", "network"):
            ip = ""
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect(("192.0.2.1", 9))           # TEST-NET: no packet is sent for UDP connect
                    ip = sock.getsockname()[0]
            except OSError:
                pass
            ssid = ""
            try:
                code, text = _run(["netsh", "wlan", "show", "interfaces"], timeout=5)
                found = re.search(r"^\s*SSID\s*:\s*(.+)$", text, re.M)
                ssid = found.group(1).strip() if found else ""
            except ActionError:
                pass
            out["network"] = {"host": socket.gethostname(), "ip": ip, "wifi": ssid}
        if what in ("all", "uptime"):
            out["uptime"] = int(w.GetTickCount64() // 1000)
        if what == "all":
            out["os"] = platform.platform()
        return out

    def processes(self) -> List[Dict[str, Any]]:
        code, text = _run(["tasklist", "/fo", "csv", "/nh"], timeout=15)
        rows = []
        import csv
        import io
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 5:
                continue
            mem = re.sub(r"[^\d]", "", row[4])
            try:
                rows.append({"name": row[0], "pid": int(row[1]), "memory": int(mem or 0) * 1024})
            except ValueError:
                continue
        return rows

    # ---------------------------------------------------------- Round 33: tray apps (assistant_procs_win.py)
    def procs(self) -> List[Dict[str, Any]]:
        from . import assistant_procs_win as pw
        try:
            return pw.list_processes()
        except (Unsupported, ActionError):
            raise
        except Exception as exc:                 # a broken snapshot: the slow but sure tasklist
            log.debug("toolhelp failed: %s", exc)
            return [{"pid": r["pid"], "ppid": 0, "exe": r["name"].lower(), "name": r["name"], "path": ""}
                    for r in self.processes()]

    def pid_alive(self, pid: int) -> bool:
        from . import assistant_procs_win as pw
        return pw.pid_alive(pid)

    def close_pid(self, pid: int) -> int:
        """Polite WM_CLOSE to every top-level window of the process, hidden ones (tray apps) included."""
        if int(pid) == os.getpid():
            raise ActionError("Нельзя закрыть сам MemuAI", "blocked", "MemuAI cannot close itself")
        from . import assistant_procs_win as pw
        return pw.post_close(pid)

    def kill(self, pid: int) -> None:
        if pid == os.getpid():
            raise ActionError("Нельзя завершить сам MemuAI", "blocked", "MemuAI cannot end itself")
        # No /T: «заверши процесс explorer» with /T used to kill the whole tree — every program
        # started from Explorer (that is, nearly everything, MemuAI included) without saving.
        code, text = _run(["taskkill", "/PID", str(int(pid)), "/F"], timeout=15)
        if code != 0:
            raise ActionError(text.strip()[:300] or "Процесс не завершён (возможно, нужны права администратора)",
                              en=text.strip()[:300] or "The process was not ended (administrator rights may be needed)")

    # ---------------------------------------------------------- power
    def lock(self) -> None:
        if not self.w.LockWorkStation():
            raise ActionError("Windows не заблокировал компьютер", en="Windows did not lock the computer")

    def sleep(self) -> None:
        if self.w.powrprof is None or not self.w.SetSuspendState(0, 0, 0):
            raise ActionError("Спящий режим недоступен", en="Sleep is unavailable")

    def hibernate(self) -> None:
        if self.w.powrprof is None or not self.w.SetSuspendState(1, 0, 0):
            raise ActionError("Гибернация выключена в Windows (powercfg /hibernate on)",
                              en="Hibernation is off in Windows (powercfg /hibernate on)")

    def shutdown(self, delay: int = 0) -> None:
        code, text = _run(["shutdown", "/s", "/t", str(max(0, int(delay))), "/c", "MemuAI: выключение"])
        if code != 0:
            raise ActionError(text.strip()[:300] or "Выключение не запущено", en=text.strip()[:300] or "Shutdown did not start")

    def restart(self, delay: int = 0) -> None:
        code, text = _run(["shutdown", "/r", "/t", str(max(0, int(delay))), "/c", "MemuAI: перезагрузка"])
        if code != 0:
            raise ActionError(text.strip()[:300] or "Перезагрузка не запущена", en=text.strip()[:300] or "Restart did not start")

    def cancel_shutdown(self) -> bool:
        code, _text = _run(["shutdown", "/a"])
        return code == 0

    def signout(self) -> None:
        if not self.w.ExitWindowsEx(0, 0x80000000):          # EWX_LOGOFF, SHTDN_REASON_FLAG_PLANNED
            raise ActionError("Windows не выполнил выход из системы", en="Windows did not sign you out")

    def powershell(self, command: str, timeout: float = 60.0) -> Dict[str, Any]:
        code, out, err = run_powershell(command, timeout=timeout)
        return {"code": code, "stdout": out[-8000:], "stderr": err[-4000:]}


def default_backend() -> Backend:
    return WinBackend() if IS_WINDOWS else NullBackend()
