"""Round 33 (r33-quick): running programs without a visible window — tray apps («закрой incy»).

``app.close`` used to look only at visible top-level windows. A program that lives in the notification
area (proxy/VPN clients such as INCY, messengers, launchers) has no such window, so «закрой incy» found
nothing, fell through to the chat model and nothing happened. This module gives the engine:

* :func:`list_processes` — every process of the session as ``{pid, exe, path, ppid}`` (Toolhelp32 snapshot +
  ``QueryFullProcessImageNameW``; tens of milliseconds, no ``tasklist``);
* :func:`pid_alive` — ``GetExitCodeProcess == STILL_ACTIVE``;
* :func:`windows_of_pid` — ALL top-level windows of a process, hidden ones included (the hidden main window of
  a tray app is what receives ``WM_CLOSE``);
* :func:`post_close` — a polite ``WM_CLOSE`` to each of them (like the × button; the app may ask to save).

Importing never touches Windows; the bindings are created on first use and every call raises
:class:`assistant_actions_win.Unsupported` on other systems. Pure helpers (:func:`is_system_path`,
:func:`user_apps`) are tested on Linux.
"""
from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import re
import sys
import threading
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("memuai.assistant.procs")

IS_WINDOWS = sys.platform == "win32"
STILL_ACTIVE = 259

# processes that are never «the app the user means» (shells, hosts, runtimes, MemuAI itself)
HOST_PROCESSES = {
    "system", "registry", "idle", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "explorer.exe", "sihost.exe", "ctfmon.exe", "conhost.exe", "fontdrvhost.exe",
    "taskhostw.exe", "runtimebroker.exe", "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "applicationframehost.exe", "dllhost.exe", "backgroundtaskhost.exe",
    "smartscreen.exe", "securityhealthsystray.exe", "securityhealthservice.exe", "msmpeng.exe", "audiodg.exe",
    "spoolsv.exe", "wmiprvse.exe", "memuai.exe", "memuaiproxy.exe", "memucode.exe", "memunote.exe",
    "python.exe", "pythonw.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "openconsole.exe",
    "msedgewebview2.exe", "crashpad_handler.exe", "lsaiso.exe", "memory compression", "secure system",
}


def is_system_path(path: str) -> bool:
    """``C:\\Windows\\…`` (System32, SysWOW64, WinSxS, SystemApps…) — Windows' own programs."""
    low = str(path or "").replace("/", "\\").lower()
    windir = (os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows").replace("/", "\\").lower().rstrip("\\")
    return bool(low) and (low.startswith(windir + "\\") or low.startswith(r"c:\windows" + "\\"))


def user_apps(rows: Iterable[Dict[str, Any]], own_pid: Optional[int] = None) -> List[Dict[str, Any]]:
    """Processes a person could mean by name: not Windows' own, not a host/runtime, not MemuAI; one row per pid."""
    own = os.getpid() if own_pid is None else own_pid
    out = []
    for row in rows or []:
        exe = str(row.get("exe") or "").lower()
        pid = int(row.get("pid") or 0)
        if not exe or pid in (0, 4, own) or exe in HOST_PROCESSES or is_system_path(row.get("path") or ""):
            continue
        out.append(row)
    return out


def exe_folder(path: str) -> str:
    """``C:\\…\\INCY\\app.exe`` → ``INCY`` (Windows paths on any OS)."""
    return ntpath.basename(ntpath.dirname(str(path or "")))


def app_label(exe: str, path: str = "") -> str:
    """``INCY.exe`` → ``INCY``; ``C:\\…\\Telegram Desktop\\Telegram.exe`` → ``Telegram``."""
    stem = re.sub(r"\.exe$", "", ntpath.basename(str(path or exe or "")), flags=re.I)
    return stem or str(exe or "")


def pretty_label(stem: str, title: str = "") -> str:
    """A name for the card: «YouTube - Google Chrome» + ``chrome`` → «Google Chrome»; ``telegram`` → «Telegram»."""
    stem = str(stem or "").strip()
    text = str(title or "").strip()
    low = re.sub(r"[^a-z0-9]", "", stem.lower())
    for sep in (" - ", " — ", " – ", " | "):
        if sep in text:
            tail = text.rsplit(sep, 1)[1].strip()
            if low and low in re.sub(r"[^a-z0-9]", "", tail.lower()) and len(tail) <= 40:
                return tail
    if text and low and re.sub(r"[^a-z0-9]", "", text.lower()) == low:
        return text
    return stem[:1].upper() + stem[1:] if stem.islower() else stem


# ------------------------------------------------------------------ Win32 (lazy)

class _K:
    _inst: Optional["_K"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "_K":
        if not IS_WINDOWS:
            from .assistant_actions_win import Unsupported
            raise Unsupported()
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
        return cls._inst

    def __init__(self) -> None:
        from ctypes import wintypes as wt
        self.wt = wt
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u = ctypes.WinDLL("user32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", wt.WCHAR * 260)]

        self.PROCESSENTRY32W = PROCESSENTRY32W

        def sig(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.CreateToolhelp32Snapshot = sig(k, "CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
        self.Process32FirstW = sig(k, "Process32FirstW", wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        self.Process32NextW = sig(k, "Process32NextW", wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        self.OpenProcess = sig(k, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        self.CloseHandle = sig(k, "CloseHandle", wt.BOOL, wt.HANDLE)
        self.GetExitCodeProcess = sig(k, "GetExitCodeProcess", wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))
        self.QueryFullProcessImageNameW = sig(k, "QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                              ctypes.POINTER(wt.DWORD))
        self.ProcessIdToSessionId = sig(k, "ProcessIdToSessionId", wt.BOOL, wt.DWORD, ctypes.POINTER(wt.DWORD))
        self.GetCurrentProcessId = sig(k, "GetCurrentProcessId", wt.DWORD)
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.EnumWindows = sig(u, "EnumWindows", wt.BOOL, self.WNDENUMPROC, wt.LPARAM)
        self.GetWindowThreadProcessId = sig(u, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
        self.IsWindowVisible = sig(u, "IsWindowVisible", wt.BOOL, wt.HWND)
        self.GetWindowTextW = sig(u, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
        self.GetClassNameW = sig(u, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
        self.GetWindow = sig(u, "GetWindow", wt.HWND, wt.HWND, wt.UINT)
        self.PostMessageW = sig(u, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    def session_of(self, pid: int) -> int:
        out = self.wt.DWORD(0)
        return int(out.value) if self.ProcessIdToSessionId(pid, ctypes.byref(out)) else -1

    def image_path(self, pid: int) -> str:
        handle = self.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = self.wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            return buf.value if self.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
        finally:
            self.CloseHandle(handle)


def list_processes(same_session: bool = True) -> List[Dict[str, Any]]:
    """Every process of the user's session: ``{pid, ppid, exe (lower), name, path}``."""
    k = _K.get()
    snap = k.CreateToolhelp32Snapshot(0x00000002, 0)          # TH32CS_SNAPPROCESS
    if not snap or int(snap) == -1 or snap == ctypes.c_void_p(-1).value:
        from .assistant_actions_win import ActionError
        raise ActionError("Не удалось получить список процессов", en="Could not list the processes")
    rows: List[Dict[str, Any]] = []
    try:
        entry = k.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        mine = k.session_of(k.GetCurrentProcessId()) if same_session else -1
        while ok:
            pid = int(entry.th32ProcessID)
            name = str(entry.szExeFile or "")
            if pid not in (0, 4) and (mine < 0 or k.session_of(pid) == mine):
                rows.append({"pid": pid, "ppid": int(entry.th32ParentProcessID), "exe": name.lower(), "name": name,
                             "path": ""})
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    for row in rows:
        try:
            row["path"] = k.image_path(row["pid"])
        except Exception:
            pass
    return rows


def pid_alive(pid: int) -> bool:
    k = _K.get()
    handle = k.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = k.wt.DWORD(0)
        return bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and int(code.value) == STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


def windows_of_pid(pid: int) -> List[Dict[str, Any]]:
    """All unowned top-level windows of a process, hidden included: ``{hwnd, title, class, visible}``."""
    k = _K.get()
    out: List[Dict[str, Any]] = []

    @k.WNDENUMPROC
    def collect(hwnd, _lparam):
        try:
            owner = k.wt.DWORD()
            k.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if int(owner.value) == int(pid) and not k.GetWindow(hwnd, 4):      # GW_OWNER
                title = ctypes.create_unicode_buffer(256)
                k.GetWindowTextW(hwnd, title, 256)
                cls = ctypes.create_unicode_buffer(256)
                k.GetClassNameW(hwnd, cls, 256)
                out.append({"hwnd": int(hwnd), "title": title.value, "class": cls.value,
                            "visible": bool(k.IsWindowVisible(hwnd))})
        except Exception:
            pass
        return True

    k.EnumWindows(collect, 0)
    return out


# helper/IME windows every GUI thread owns; WM_CLOSE to them means nothing
_NOISE_CLASSES = {"ime", "msctfime ui", "default ime", "gdi+ hook window class", "tooltips_class32"}


def post_close(pid: int) -> int:
    """``WM_CLOSE`` to every meaningful top-level window of ``pid``; returns how many got it."""
    k = _K.get()
    sent = 0
    for win in windows_of_pid(pid):
        if win["class"].lower() in _NOISE_CLASSES:
            continue
        try:
            if k.PostMessageW(k.wt.HWND(win["hwnd"]), 0x0010, 0, 0):
                sent += 1
        except Exception:
            pass
    return sent
