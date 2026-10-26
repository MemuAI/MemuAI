"""Win+E fallback (Round 38): a low-level keyboard hook while MemuFiles runs.

The registry route (:mod:`.default_shell`, the ``{52205fd8-…}\\shell\\opennewwindow`` key Files uses) is the main
way; some Windows 11 builds / policies ignore it, so the user can also turn this on: while the MemuFiles window is
open, Win+E is swallowed and opens a new MemuFiles tab instead. ``RegisterHotKey`` cannot take Win+E (Explorer owns
it), hence ``WH_KEYBOARD_LL``. When MemuFiles is closed Windows behaves normally — nothing persists.

The hook thread only runs a message loop; the callback is posted to a worker so the hook returns in microseconds
(Windows unhooks slow LL hooks). A dummy key (VK 0xFF) is injected before the Win key is released so the swallowed
chord does not open the Start menu.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

log = logging.getLogger("memufiles.winkey")

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_E, VK_LWIN, VK_RWIN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x45, 0x5B, 0x5C, 0x10, 0x11, 0x12
VK_DUMMY = 0xFF
LLKHF_INJECTED = 0x10

_thread: Optional[threading.Thread] = None
_thread_id = 0

def is_chord(vk: int, message: int, down: Callable[[int], bool], injected: bool = False) -> bool:
    """Pure decision (tested): plain Win+E key-down, no Ctrl/Alt/Shift, not our own injected input."""
    if injected or vk != VK_E or message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
        return False
    if not (down(VK_LWIN) or down(VK_RWIN)):
        return False
    return not (down(VK_CONTROL) or down(VK_MENU) or down(VK_SHIFT))

def start(on_win_e: Callable[[], None]) -> bool:
    """Install the hook on its own thread (Windows only; idempotent). False when it could not start."""
    global _thread
    if os.name != "nt" or (_thread is not None and _thread.is_alive()):
        return os.name == "nt"
    ready = threading.Event()
    result = {"ok": False}
    _thread = threading.Thread(target=_loop, args=(on_win_e, ready, result), name="memufiles-win-e", daemon=True)
    _thread.start()
    ready.wait(3.0)
    return result["ok"]

def stop() -> None:
    global _thread
    if os.name == "nt" and _thread is not None and _thread.is_alive() and _thread_id:
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(_thread_id, 0x0012, 0, 0)
    _thread = None

def _loop(on_win_e: Callable[[], None], ready: threading.Event, result: dict) -> None:
    global _thread_id
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def fire() -> None:
        try:
            on_win_e()
        except Exception:  # noqa: BLE001
            log.exception("Win+E handler failed")

    def proc(code: int, wparam: int, lparam: int) -> int:
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if is_chord(info.vkCode, wparam, down, bool(info.flags & LLKHF_INJECTED)):
                user32.keybd_event(VK_DUMMY, 0, 0, 0)
                user32.keybd_event(VK_DUMMY, 0, 2, 0)
                threading.Thread(target=fire, name="memufiles-win-e-open", daemon=True).start()
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    callback = HOOKPROC(proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
    _thread_id = kernel32.GetCurrentThreadId()
    result["ok"] = bool(hook)
    ready.set()
    if not hook:
        log.warning("Win+E hook was not installed (error %s)", kernel32.GetLastError())
        return
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnhookWindowsHookEx(hook)
