"""Round 40 (r40-files-core): progress of file operations on the MemuFiles taskbar button, like Explorer.

``ITaskbarList3::SetProgressValue / SetProgressState`` (green bar; yellow when
paused or waiting for a conflict answer; marquee while counting files) and
``FlashWindowEx`` when a long operation finishes while the window is in the
background. All COM calls run on one STA thread of their own (the taskbar
object is apartment-threaded); callers only hand over the latest state, which
is throttled to a few updates per second. Off Windows (and when anything in
the shell refuses) it is a silent no-op.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("memufiles.taskbar")

TBPF_NOPROGRESS, TBPF_INDETERMINATE, TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED = 0, 1, 2, 4, 8
FINISHED = ("done", "error", "cancelled")
MIN_INTERVAL = 0.3
LONG_OP = 3.0           # an operation that ran this long flashes the taskbar button when it ends in the background


def summarize(ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The one bar the taskbar shows for all running operations: ``{state, done, total}``."""
    active = [o for o in ops if o.get("status") not in FINISHED]
    if not active:
        return {"state": TBPF_NOPROGRESS, "done": 0, "total": 0}
    if any(o.get("status") in ("paused", "conflict") for o in active):
        state = TBPF_PAUSED
    elif all(o.get("status") in ("queued", "scanning") or (o.get("unit") == "items" and (o.get("total_items") or 0) <= 1)
             for o in active):
        state = TBPF_INDETERMINATE
    else:
        state = TBPF_NORMAL
    fractions = []
    for o in active:
        if o.get("unit") == "bytes" and o.get("total_bytes"):
            fractions.append(min(1.0, o.get("done_bytes", 0) / o["total_bytes"]))
        elif o.get("total_items"):
            fractions.append(min(1.0, o.get("done_items", 0) / o["total_items"]))
        else:
            fractions.append(0.0)
    done = int(1000 * sum(fractions) / len(fractions))
    return {"state": state, "done": done, "total": 1000}


class TaskbarProgress:
    """Feed it operation snapshots (:meth:`op_changed`); it keeps the taskbar button in step."""

    def __init__(self, hwnd: Callable[[], int], ops: Callable[[], List[Dict[str, Any]]]) -> None:
        self._hwnd = hwnd
        self._ops = ops
        self._wake = threading.Event()
        self._flash = False
        self._last: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None
        self._enabled = os.name == "nt"

    def op_changed(self, snap: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        if snap.get("status") in FINISHED and float(snap.get("elapsed") or 0) >= LONG_OP:
            self._flash = True
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="memufiles-taskbar", daemon=True)
            self._thread.start()
        self._wake.set()

    # ---------------------------------------------------------------- STA thread
    def _loop(self) -> None:
        try:
            com = _TaskbarCom()
        except Exception as exc:  # noqa: BLE001
            log.debug("taskbar progress unavailable: %s", exc)
            self._enabled = False
            return
        while True:
            self._wake.wait()
            self._wake.clear()
            try:
                hwnd = int(self._hwnd() or 0)
                if not hwnd:
                    continue
                state = summarize(self._ops())
                if state != self._last:
                    com.set_state(hwnd, state["state"])
                    if state["state"] in (TBPF_NORMAL, TBPF_PAUSED):
                        com.set_value(hwnd, state["done"], state["total"])
                    self._last = state
                if self._flash:
                    self._flash = False
                    _flash_if_background(hwnd)
            except Exception as exc:  # noqa: BLE001
                log.debug("taskbar update failed: %s", exc)
            time.sleep(MIN_INTERVAL)


class _TaskbarCom:
    """``ITaskbarList3`` through its vtable (no comtypes): HrInit 3, SetProgressValue 9, SetProgressState 10."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0x2)                      # COINIT_APARTMENTTHREADED
        clsid = _guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
        iid = _guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
        self.ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1 | 0x4, ctypes.byref(iid), ctypes.byref(self.ptr))
        if hr != 0 or not self.ptr:
            raise OSError(f"CoCreateInstance(TaskbarList) 0x{hr & 0xFFFFFFFF:08x}")
        self._call(3, ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p))
        self.wintypes = wintypes

    def _call(self, index: int, proto: Any, *args: Any) -> int:
        ctypes = self.ctypes
        vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return proto(vtable[index])(self.ptr, *args)

    def set_value(self, hwnd: int, done: int, total: int) -> None:
        ctypes = self.ctypes
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong)
        self._call(9, proto, ctypes.c_void_p(hwnd), done, total)

    def set_state(self, hwnd: int, flags: int) -> None:
        ctypes = self.ctypes
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
        self._call(10, proto, ctypes.c_void_p(hwnd), flags)


def _guid(text: str) -> Any:
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]
    guid = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


def _flash_if_background(hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    if (user32.GetForegroundWindow() or 0) == hwnd:
        return

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", ctypes.c_void_p), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), ctypes.c_void_p(hwnd), 0x2 | 0xC, 3, 0)   # TRAY | TIMERNOFG
    user32.FlashWindowEx(ctypes.byref(info))
