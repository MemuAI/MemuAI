"""Global hotkey for MemuAI on Windows: ``RegisterHotKey`` on its own thread.

Why a dedicated thread with its own message loop
------------------------------------------------
``RegisterHotKey(NULL, …)`` posts ``WM_HOTKEY`` to the *message queue of the
thread that registered it*. The WinForms UI thread of pywebview is busy with
its own loop and the uvicorn thread has no queue at all, so the listener owns
a small thread that does nothing but ``GetMessageW``. Registering, changing
and removing the hotkey are requests posted to that thread
(``PostThreadMessageW``) and answered through an ``Event`` — RegisterHotKey
and UnregisterHotKey must be called from the registering thread.

A low-level keyboard hook (``WH_KEYBOARD_LL``) is deliberately *not* used:
it sees every keystroke of the user, slows down typing in every program if
our process stalls, and antivirus software treats it as a keylogger pattern.
``RegisterHotKey`` is the documented way to get one global shortcut.

Alt+Space
---------
Registering Alt+Space works and replaces the window system menu shortcut in
every program while MemuAI runs (the menu is still reachable by a right click
on a title bar). When another program already registered the same hotkey,
``RegisterHotKey`` fails with ``ERROR_HOTKEY_ALREADY_REGISTERED`` (1409) and
the status says so. Launchers that use a low-level hook instead (PowerToys
Run, Flow Launcher) make our registration *succeed* but the key never
arrives — :func:`running_processes` lets the UI warn about them.

Nothing here raises into the caller: every failure is a status dict.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .quick_ask_core import KEYS, MOD_NOREPEAT, VK_MODIFIERS, HotkeyError, hotkey_from_vks, parse_hotkey

log = logging.getLogger("memuai.hotkey")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_APP_HOTKEY = WM_APP + 0x51A0
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0x4D51

def describe_error(code: int, hotkey: str, lang: str = "ru") -> str:
    en = lang == "en"
    if code == ERROR_HOTKEY_ALREADY_REGISTERED:
        return (f"{hotkey} is already taken by another program (often PowerToys Run, Flow Launcher, a "
                "translator or a game overlay). Close it or choose another combination."
                if en else
                f"{hotkey} уже занято другой программой (часто PowerToys Run, Flow Launcher, переводчик "
                "или оверлей игры). Закройте её или выберите другое сочетание.")
    return (f"Windows did not register {hotkey} (error {code})." if en
            else f"Windows не зарегистрировал {hotkey} (ошибка {code}).")

class _Win32:
    """ctypes signatures, 64-bit safe, on private WinDLL instances."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                        ("time", wintypes.DWORD), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long),
                        ("lPrivate", wintypes.DWORD)]

        self.MSG = MSG
        self.RegisterHotKey = user32.RegisterHotKey
        self.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        self.RegisterHotKey.restype = wintypes.BOOL
        self.UnregisterHotKey = user32.UnregisterHotKey
        self.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.UnregisterHotKey.restype = wintypes.BOOL
        self.GetMessageW = user32.GetMessageW
        self.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        self.GetMessageW.restype = ctypes.c_int
        self.PeekMessageW = user32.PeekMessageW
        self.PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                      ctypes.c_uint]
        self.PeekMessageW.restype = wintypes.BOOL
        self.PostThreadMessageW = user32.PostThreadMessageW
        self.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.PostThreadMessageW.restype = wintypes.BOOL
        self.GetCurrentThreadId = kernel32.GetCurrentThreadId
        self.GetCurrentThreadId.argtypes = []
        self.GetCurrentThreadId.restype = wintypes.DWORD

    def last_error(self) -> int:
        return self.ctypes.get_last_error()

class HotkeyListener:
    """One global hotkey; ``callback()`` runs on a short-lived worker thread.

    ``set_hotkey(text)`` → ``{"ok": bool, "hotkey": str, "code": str, "error": str}``.
    The previous registration stays active when the new one fails, so a
    typo in Settings never leaves the user without Quick Ask.
    """

    def __init__(self, callback: Callable[[], Any], *, lang: str = "ru",
                 api_factory: Optional[Callable[[], Any]] = None) -> None:
        self.callback = callback
        self.lang = lang
        self._api_factory = api_factory or _Win32
        self._api = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._requests: List[Dict[str, Any]] = []
        self.active = ""
        self.status: Dict[str, Any] = {"ok": False, "hotkey": "", "code": "not_started", "error": ""}
        self._last_fire = 0.0

        self.suspended = ""
        self._resume_timer: Optional[threading.Timer] = None
        self.last_win32 = 0

    def start(self) -> bool:
        if sys.platform != "win32" and self._api_factory is _Win32:
            self.status = {"ok": False, "hotkey": "", "code": "unsupported",
                           "error": "Global hotkeys are available only on Windows"}
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="memuai-hotkey", daemon=True)
        self._thread.start()
        return self._ready.wait(3.0) and bool(self._thread_id)

    def set_hotkey(self, text: str, timeout: float = 3.0) -> Dict[str, Any]:
        try:
            modifiers, vk, canonical = parse_hotkey(text)
        except HotkeyError as exc:
            return {"ok": False, "hotkey": str(text or ""), "code": exc.code, "error": str(exc)}
        if not self.start():
            return dict(self.status)
        return self._request({"op": "set", "mods": modifiers, "vk": vk, "hotkey": canonical}, timeout)

    def probe(self, text: str, timeout: float = 3.0) -> Dict[str, Any]:
        """Is ``text`` free right now? Registers and immediately releases it (current one untouched)."""
        try:
            modifiers, vk, canonical = parse_hotkey(text)
        except HotkeyError as exc:
            return {"ok": False, "hotkey": str(text or ""), "code": exc.code, "error": str(exc)}
        if canonical in (self.active, self.suspended) and canonical:
            return {"ok": True, "hotkey": canonical, "code": "ours", "error": ""}
        if not self.start():
            return dict(self.status)
        return self._request({"op": "probe", "mods": modifiers, "vk": vk, "hotkey": canonical}, timeout)

    def suspend(self, seconds: float = 20.0, timeout: float = 3.0) -> Dict[str, Any]:
        """Release our hotkey for a while (the capture field in Settings); ``resume()`` restores it."""
        if not (self._thread and self._thread.is_alive()):
            return {"ok": True, "hotkey": "", "code": "off", "error": ""}
        result = self._request({"op": "suspend"}, timeout)
        timer, self._resume_timer = self._resume_timer, None
        if timer:
            timer.cancel()
        if self.suspended:
            self._resume_timer = threading.Timer(max(1.0, float(seconds)), self.resume)
            self._resume_timer.daemon = True
            self._resume_timer.start()
        return result

    def resume(self, timeout: float = 3.0) -> Dict[str, Any]:
        timer, self._resume_timer = self._resume_timer, None
        if timer:
            timer.cancel()
        if not self.suspended or not (self._thread and self._thread.is_alive()):
            return dict(self.status)
        return self._request({"op": "resume"}, timeout)

    def clear(self, timeout: float = 3.0) -> Dict[str, Any]:
        if not (self._thread and self._thread.is_alive()):
            self.active = ""
            return {"ok": True, "hotkey": "", "code": "off", "error": ""}
        return self._request({"op": "clear"}, timeout)

    def stop(self) -> None:
        thread, thread_id = self._thread, self._thread_id
        if thread and thread.is_alive() and thread_id and self._api is not None:
            try:
                self._api.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
            thread.join(2.0)
        self._thread = None
        self._thread_id = 0

    def _request(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        done = threading.Event()
        request["done"] = done
        with self._lock:
            self._requests.append(request)
        try:
            posted = bool(self._api.PostThreadMessageW(self._thread_id, WM_APP_HOTKEY, 0, 0))
        except Exception:
            posted = False
        if not posted or not done.wait(timeout):
            with self._lock:
                if request in self._requests:
                    self._requests.remove(request)
            return {"ok": False, "hotkey": request.get("hotkey", ""), "code": "timeout",
                    "error": "Поток горячих клавиш не ответил"}
        return request.get("result") or {"ok": False, "code": "failed", "error": ""}

    def _run(self) -> None:
        try:
            api = self._api = self._api_factory()
            msg = api.MSG()

            api.PeekMessageW(api.ctypes.byref(msg), None, 0, 0, 0)
            self._thread_id = int(api.GetCurrentThreadId())
        except Exception as exc:
            self.status = {"ok": False, "hotkey": "", "code": "unsupported", "error": str(exc)}
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                result = api.GetMessageW(api.ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self._fire()
                elif msg.message == WM_APP_HOTKEY:
                    self._drain()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("hotkey loop stopped: %s", exc)
        finally:
            try:
                if self.active:
                    api.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
            self.active = ""

    def _drain(self) -> None:
        with self._lock:
            pending, self._requests = self._requests, []
        for request in pending:
            try:
                request["result"] = self._handle(request)
            except Exception as exc:
                request["result"] = {"ok": False, "hotkey": request.get("hotkey", ""), "code": "failed",
                                     "error": str(exc)}
            request["done"].set()

    def _handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        api = self._api
        op = request["op"]
        if op == "clear":
            if self.active:
                api.UnregisterHotKey(None, HOTKEY_ID)
            self.active = ""
            self.suspended = ""
            self.status = {"ok": True, "hotkey": "", "code": "off", "error": ""}
            return dict(self.status)
        if op == "suspend":
            if self.active:
                api.UnregisterHotKey(None, HOTKEY_ID)
                self.suspended, self.active = self.active, ""
            return {"ok": True, "hotkey": self.suspended, "code": "suspended", "error": ""}
        if op == "resume":
            hotkey, self.suspended = self.suspended, ""
            if not hotkey or self.active:
                return dict(self.status)
            mods, vk, _ = parse_hotkey(hotkey)
            if self._register(mods, vk):
                self.active = hotkey
                self.status = {"ok": True, "hotkey": hotkey, "code": "registered", "error": ""}
            else:
                code = self.last_win32
                self.status = {"ok": False, "hotkey": hotkey, "code": self._code(code),
                               "error": describe_error(code, hotkey, self.lang), "win32": code}
            return dict(self.status)
        hotkey, mods, vk = request["hotkey"], int(request["mods"]), int(request["vk"])
        if op == "probe":
            probe_id = HOTKEY_ID + 1
            if api.RegisterHotKey(None, probe_id, mods | MOD_NOREPEAT, vk):
                api.UnregisterHotKey(None, probe_id)
                return {"ok": True, "hotkey": hotkey, "code": "free", "error": ""}
            code = api.last_error()
            return {"ok": False, "hotkey": hotkey, "code": self._code(code),
                    "error": describe_error(code, hotkey, self.lang), "win32": code}

        previous = self.active or self.suspended
        if previous == hotkey and self.active == hotkey:
            return dict(self.status)
        if self.active:
            api.UnregisterHotKey(None, HOTKEY_ID)
        self.active = ""
        self.suspended = ""
        if self._register(mods, vk):
            self.active = hotkey
            self.status = {"ok": True, "hotkey": hotkey, "code": "registered", "error": ""}
            return dict(self.status)
        code = self.last_win32
        failure = {"ok": False, "hotkey": hotkey, "code": self._code(code),
                   "error": describe_error(code, hotkey, self.lang), "win32": code}
        self.active = ""
        if previous:
            try:
                restored_mods, restored_vk, _ = parse_hotkey(previous)
                if self._register(restored_mods, restored_vk):
                    self.active = previous
                    failure["kept"] = previous
            except Exception:
                pass
        self.status = dict(failure) if not self.active else {
            "ok": True, "hotkey": self.active, "code": "registered", "error": "",
            "rejected": hotkey, "rejected_error": failure["error"]}
        return failure

    @staticmethod
    def _code(win32: int) -> str:
        return "busy" if win32 == ERROR_HOTKEY_ALREADY_REGISTERED else "failed"

    def _register(self, mods: int, vk: int) -> bool:
        """RegisterHotKey for our id — never fails because of *our own* stale registration.

        "Already registered" (1409) is also what Windows answers when this very
        process still holds the combination: under our id (a registration the
        bookkeeping lost track of) or under the probe id. Both are released and
        the registration retried once before the failure is reported, so the
        settings never claim that "another program" took our own hotkey.
        """
        api = self._api
        if api.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            self.last_win32 = 0
            return True
        self.last_win32 = int(api.last_error() or 0)
        if self.last_win32 != ERROR_HOTKEY_ALREADY_REGISTERED:
            return False
        released = False
        for own in (HOTKEY_ID, HOTKEY_ID + 1):
            try:
                released = bool(api.UnregisterHotKey(None, own)) or released
            except Exception:
                pass
        if released:
            if api.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                self.last_win32 = 0
                return True
            self.last_win32 = int(api.last_error() or ERROR_HOTKEY_ALREADY_REGISTERED)
        return False

    def _fire(self) -> None:
        now = time.monotonic()

        if now - self._last_fire < 0.12:
            return
        self._last_fire = now
        threading.Thread(target=self._safe_callback, name="memuai-hotkey-fire", daemon=True).start()

    def _safe_callback(self) -> None:
        try:
            self.callback()
        except Exception as exc:
            log.warning("quick ask hotkey callback failed: %s", exc)

class ComboCapture:
    """Native capture of a key combination for the Settings field (Round 22).

    The browser cannot capture every combination: Windows turns Alt+Space
    into the window's system menu before WebView2 sees the Space, and many
    Win+… chords never reach a page. While the capture field is active, the
    settings page asks the server to poll the keyboard state
    (``GetAsyncKeyState``, ~120 Hz) — *only* while a MemuAI window is in the
    foreground, only until the first combination or the timeout (15 s) — and
    returns the canonical text of the first chord whose main key goes down.
    No hook is installed; nothing is logged or kept.
    """

    POLL = 0.008

    def __init__(self, key_down: Callable[[int], bool], *, foreground_ok: Callable[[], bool] = lambda: True) -> None:
        self.key_down = key_down
        self.foreground_ok = foreground_ok
        self.vks = sorted(set(KEYS.values()) | set(VK_MODIFIERS))

    def _down(self) -> set:
        out = set()
        for vk in self.vks:
            try:
                if self.key_down(vk):
                    out.add(vk)
            except Exception:
                pass
        return out

    def run(self, timeout: float = 15.0, cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.2, float(timeout))
        held = self._down()
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                return {"ok": False, "code": "cancelled", "hotkey": ""}
            if not self.foreground_ok():
                time.sleep(0.05)
                held = self._down()
                continue
            down = self._down()
            fresh = {vk for vk in down - held if vk not in VK_MODIFIERS}
            held = {vk for vk in held if vk in down}
            if fresh:
                if fresh == {0x1B} and not (down & set(VK_MODIFIERS)):
                    return {"ok": False, "code": "cancelled", "hotkey": ""}
                combo = hotkey_from_vks(sorted(down & set(VK_MODIFIERS)) + sorted(fresh)[:1])
                if combo:
                    return {"ok": True, "code": "captured", "hotkey": combo}
            time.sleep(self.POLL)
        return {"ok": False, "code": "timeout", "hotkey": ""}

def running_processes() -> List[str]:
    """Lower-case image names of running processes (Toolhelp snapshot; [] off Windows)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        snapshot = kernel32.CreateToolhelp32Snapshot
        snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        snapshot.restype = ctypes.c_void_p
        first = kernel32.Process32FirstW
        first.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        first.restype = wintypes.BOOL
        nxt = kernel32.Process32NextW
        nxt.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        nxt.restype = wintypes.BOOL
        close = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        handle = snapshot(0x00000002, 0)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return []
        names: List[str] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = first(handle, ctypes.byref(entry))
            while ok:
                names.append(entry.szExeFile.lower())
                ok = nxt(handle, ctypes.byref(entry))
        finally:
            close(handle)
        return names
    except Exception:
        return []

VK_ESCAPE = 0x1B
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10

def escape_should_close(vk: int, message: int, flags: int, *, visible: bool, foreground: int,
                        own_windows: "tuple | list | set", pinned: bool = False) -> bool:
    """Pure decision of the global Escape hook (unit-tested off Windows).

    * only a real (not injected — our own Ctrl+V/insert never trips it) Escape key-down;
    * only while Quick Ask is visible;
    * NOT when one of our windows is in front: then the page handles Escape itself, level by level
      (open menu/list/DFY question → … → the window), so one press never skips a level;
    * NOT when the window is pinned — a pinned popup closes only by Escape inside it / the button / the hotkey.
    """
    if int(vk) != VK_ESCAPE or int(message) not in (WM_KEYDOWN, WM_SYSKEYDOWN):
        return False
    if int(flags) & LLKHF_INJECTED:
        return False
    if not visible or pinned:
        return False
    if foreground and int(foreground) in {int(h) for h in own_windows if h}:
        return False
    return True

class EscapeWatcher:
    """A ``WH_KEYBOARD_LL`` hook that exists ONLY while Quick Ask is visible.

    Why not ``RegisterHotKey(VK_ESCAPE)``: a registered hotkey swallows the key — Escape would stop working in
    every other program while the popup is open (and with hide-on-blur gone the popup can stay open for long).
    The low-level hook never swallows anything: it always calls ``CallNextHookEx`` and only *schedules* the
    hide on a worker thread (hook procedures must return within the LowLevelHooksTimeout, ~300 ms).
    ``arm()`` installs the hook on its own thread with a message loop; ``disarm()`` posts ``WM_QUIT`` and the
    thread unhooks — no hook stays in the system while the popup is hidden.
    """

    def __init__(self, on_escape: Callable[[], Any], decide: Callable[[int, int, int], bool]) -> None:
        self.on_escape = on_escape
        self.decide = decide
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._lock = threading.Lock()
        self.armed = False
        self.error = ""

    def arm(self) -> bool:
        if sys.platform != "win32":
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            ready = threading.Event()
            thread = threading.Thread(target=self._run, args=(ready,), name="memuai-quick-escape", daemon=True)
            thread._memu_ready = ready          # type: ignore[attr-defined]
            thread._memu_tid = 0                # type: ignore[attr-defined]  (set by the hook thread itself)
            self._thread = thread
            thread.start()
        ready.wait(2.0)
        return self.armed

    def disarm(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return

        ready = getattr(thread, "_memu_ready", None)
        if ready is not None:
            ready.wait(2.0)
        thread_id = int(getattr(thread, "_memu_tid", 0) or 0)
        if thread_id and sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.WinDLL("user32")
                user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
                user32.PostThreadMessageW.restype = wintypes.BOOL
                user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        self.armed = False

    def _run(self, ready: threading.Event) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        hook = None

        def proc(code, wparam, lparam):
            try:
                if code == 0:
                    info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if int(info.vkCode) == VK_ESCAPE and self.decide(int(info.vkCode), int(wparam), int(info.flags)):
                        threading.Thread(target=self._fire, daemon=True).start()
            except Exception:
                pass
            return user32.CallNextHookEx(hook, code, wparam, lparam)

        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        me = threading.current_thread()
        callback = HOOKPROC(proc)
        self._callback = callback
        msg = wintypes.MSG()

        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        me._memu_tid = self._thread_id         # type: ignore[attr-defined]
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self.error = "SetWindowsHookExW failed: %s" % ctypes.get_last_error()
            ready.set()
            return
        if self._thread is me:
            self.armed = True
        ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if self._thread is not me:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            if self._thread is me or self._thread is None:
                self.armed = False
            del callback

    def _fire(self) -> None:
        try:
            self.on_escape()
        except Exception:
            pass
