"""Round 27: the Windows side of the new Quick Ask features (:mod:`app.quick_features`).

* :class:`ClipboardWatcher` — a polling thread on ``GetClipboardSequenceNumber`` (one cheap call every
  0.4 s, no window and no message loop of our own). Before reading anything it checks the formats a
  program sets to keep its data out of clipboard monitors — ``ExcludeClipboardContentFromMonitorProcessing``,
  ``CanIncludeInClipboardHistory`` = 0 (what password managers and Windows itself use) and
  ``Clipboard Viewer Ignore`` — and the owner process (password managers, the user's own list, MemuAI itself).
* Region overlay — ONE frameless always-on-top pywebview window (``/quick/region.html``), created on first use
  and reused: it covers the monitor under the cursor (physical pixels) and shows the frozen screenshot; the
  page answers with fractions of its viewport, so the crop is independent of the monitor's DPI.
* Stickers — one small frameless always-on-top tool window per open sticker (``/quick/sticker.html``);
  moving/resizing is done natively (``GetCursorPos`` loop while the button is down), geometry is kept in
  physical pixels in ``quick_stickers.json`` and clamped to a monitor's work area on restore.
* Optional global hotkeys (region, clipboard history) — :class:`app.win_hotkey.HotkeyListener`.

Everything here runs on worker threads (never on the WinForms UI thread); window calls go through
``winforms_ui_invoker`` like :mod:`app.quick_ask`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import quick_features as qf

log = logging.getLogger("memuai.quick.features")

POLL_SECONDS = 0.4
REGION_TIMEOUT = 180.0
STICKER_SIZE = (340, 250)
VK_LEFT = 0x25
GMEM_SIZE_DWORD = 4
BUSY = object()

_RUNTIMES: Dict[int, "Runtime"] = {}
_RUNTIMES_LOCK = threading.Lock()

def runtime(manager: Any) -> "Runtime":
    with _RUNTIMES_LOCK:
        rt = _RUNTIMES.get(id(manager))
        if rt is None:
            rt = _RUNTIMES[id(manager)] = Runtime(manager)
        return rt

def destroy_all() -> None:
    """MemuAI quits (``DesktopShell.quit_hooks``): stop the watcher and the hotkeys, close our windows."""
    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
    for rt in runtimes:
        try:
            rt.destroy()
        except Exception as exc:
            log.debug("quick features: destroy failed: %s", exc)

def selection_busy() -> bool:
    """The selection capture of Quick Ask (``app.quick_selection``) is copying / restoring the clipboard right now:
    its short Ctrl+C is not something the user copied."""
    try:
        from . import quick_selection
        return bool(quick_selection.clipboard_busy())
    except Exception:
        return False

def process_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        import psutil
        return psutil.Process(int(pid)).name().lower()
    except Exception:
        return ""

class ClipboardFormats:
    """The registered «do not monitor» formats + owner lookup (ctypes, 64-bit-safe signatures)."""

    def __init__(self, api: Any) -> None:
        ctypes, wintypes = api.ctypes, api.wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.api = api
        register = user32.RegisterClipboardFormatW
        register.restype, register.argtypes = ctypes.c_uint, [wintypes.LPCWSTR]
        self.available = user32.IsClipboardFormatAvailable
        self.available.restype, self.available.argtypes = wintypes.BOOL, [ctypes.c_uint]
        self.owner = user32.GetClipboardOwner
        self.owner.restype, self.owner.argtypes = ctypes.c_void_p, []
        self.size = ctypes.WinDLL("kernel32").GlobalSize
        self.size.restype, self.size.argtypes = ctypes.c_size_t, [ctypes.c_void_p]
        self.exclude = int(register("ExcludeClipboardContentFromMonitorProcessing") or 0)
        self.history = int(register("CanIncludeInClipboardHistory") or 0)
        self.viewer_ignore = int(register("Clipboard Viewer Ignore") or 0)

    def owner_pid(self) -> int:
        hwnd = self.owner()
        if not hwnd:
            return 0
        pid = self.api.wintypes.DWORD(0)
        self.api.GetWindowThreadProcessId(hwnd, self.api.ctypes.byref(pid))
        return int(pid.value)

    def history_allowed(self) -> Any:
        """``CanIncludeInClipboardHistory`` present with DWORD 0 → the program forbids history (:data:`BUSY` — the
        clipboard is held open by someone: cannot verify now, never read it unverified)."""
        api = self.api
        if not self.history or not self.available(self.history):
            return True
        for _attempt in range(5):
            if api.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return BUSY
        try:
            handle = api.GetClipboardData(self.history)
            if not handle or int(self.size(handle) or 0) < GMEM_SIZE_DWORD:
                return True
            pointer = api.GlobalLock(handle)
            if not pointer:
                return False
            try:
                return api.ctypes.c_uint32.from_address(pointer).value != 0
            finally:
                api.GlobalUnlock(handle)
        finally:
            api.CloseClipboard()

    def private(self) -> Any:
        if (self.exclude and self.available(self.exclude)) or (self.viewer_ignore and self.available(self.viewer_ignore)):
            return True
        allowed = self.history_allowed()
        return BUSY if allowed is BUSY else not allowed

class ClipboardWatcher:
    def __init__(self, api: Any, foreground_pid=lambda: 0) -> None:
        self.api = api
        self.foreground_pid = foreground_pid
        self.settings: Dict[str, Any] = dict(qf.DEFAULT_SETTINGS)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.error = ""
        self.skipped = 0
        self._formats: Optional[ClipboardFormats] = None
        self.quiet_until = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, settings: Dict[str, Any]) -> None:
        self.settings = dict(settings)
        if self.running and not self._stop.is_set():
            return

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="memuai-clipboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def quiet(self, seconds: float) -> None:
        """MemuAI itself puts text on the clipboard for a moment (a snippet or a history item pasted, then the user's
        clipboard put back): those changes are not something the user copied."""
        self.quiet_until = max(self.quiet_until, time.monotonic() + seconds)

    def _run(self, stop: Optional[threading.Event] = None) -> None:
        stop = stop or self._stop
        try:
            self._formats = ClipboardFormats(self.api)
        except Exception as exc:
            self.error = str(exc)
            log.warning("quick clipboard: formats unavailable: %s", exc)
            return
        last = int(self.api.GetClipboardSequenceNumber() or 0)
        own = os.getpid()
        retries = 0
        while not stop.wait(POLL_SECONDS):
            seq = last
            try:
                seq = int(self.api.GetClipboardSequenceNumber() or 0)
                if seq == last:
                    continue
                time.sleep(0.05)
                if self.settings.get("clipboard_paused") or selection_busy() or time.monotonic() < self.quiet_until:
                    last, retries = seq, 0
                    continue
                if self.capture(own) is BUSY and retries < 4:
                    retries += 1
                    continue
                last, retries = seq, 0
            except Exception as exc:
                last, retries = seq, 0
                log.debug("quick clipboard: %s", exc)

    def capture(self, own_pid: int) -> Any:
        """The new row, ``None`` (not recorded) or :data:`BUSY` (the clipboard could not be opened — try again)."""
        formats = self._formats
        if formats is None:
            return None
        private = formats.private()
        if private is BUSY:
            return BUSY
        if private:
            self.skipped += 1
            return None
        pid = formats.owner_pid() or int(self.foreground_pid() or 0)
        if pid == own_pid:
            return None
        app = process_name(pid)
        if qf.ignored_process(app, self.settings):
            self.skipped += 1
            return None
        available = self.api.clipboard_formats()
        if -1 in available:
            return BUSY
        if 13 not in available:
            return None
        text = self.api.get_text()
        if text is None:
            return BUSY
        text = text or ""
        if not text.strip():
            return None
        if self.settings.get("clipboard_skip_secrets", True) and qf.looks_secret(text):
            self.skipped += 1
            return None
        return qf.CLIPBOARD.add(text, app=app, limit=int(self.settings.get("clipboard_limit") or 200))

class Runtime:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.api = manager.api
        self.watcher = ClipboardWatcher(self.api, self._foreground_pid)
        self._lock = threading.RLock()
        self._started = False
        self._sessions: Dict[str, qf.RegionSession] = {}
        self._region_lock = threading.Lock()
        self.overlay = None
        self.overlay_form = None
        self.overlay_hwnd = 0
        self._overlay_loaded = threading.Event()
        self.stickers: Dict[str, Dict[str, Any]] = {}
        self._hotkeys: Dict[str, Any] = {}
        self.hotkey_status: Dict[str, Dict[str, Any]] = {}
        self.pending_region: Optional[Dict[str, Any]] = None
        self._quitting = False
        self._stickers_on: Optional[bool] = None

    def _own_windows(self) -> None:
        """Tell the Quick Ask Escape hook which windows are ours (Escape in the overlay or a sticker is theirs)."""
        hwnds = [self.overlay_hwnd] + [entry["hwnd"] for entry in self.stickers.values()]
        self.manager.extra_windows = tuple(h for h in hwnds if h)

    def ensure_started(self, settings: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
        """First call (the warm Quick Ask page loads the plugin at startup) also brings back open stickers."""
        with self._lock:
            first = not self._started
            self._started = True
        status = self.apply(settings, lang)
        if first and settings.get("stickers", True):
            threading.Thread(target=self._restore_stickers, name="memuai-stickers", daemon=True).start()
        return status

    def apply(self, settings: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
        if settings.get("clipboard"):
            self.watcher.start(settings)
        else:
            self.watcher.stop()
        self.watcher.settings = dict(settings)
        for name, key, callback in (("region", "region_hotkey", self._hotkey_region),
                                    ("clipboard", "clipboard_hotkey", self._hotkey_clipboard)):
            enabled = settings.get(name) if name == "region" else settings.get("clipboard")
            self.hotkey_status[name] = self._set_hotkey(name, settings.get(key) if enabled else "", callback, lang)
        stickers_on = bool(settings.get("stickers", True))
        if not stickers_on:
            for sticker_id in list(self.stickers):
                self.close_sticker(sticker_id)
        elif self._stickers_on is False and self._started:

            threading.Thread(target=self._restore_stickers, name="memuai-stickers", daemon=True).start()
        self._stickers_on = stickers_on
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"clipboard": {"running": self.watcher.running, "paused": bool(self.watcher.settings.get("clipboard_paused")),
                              "error": self.watcher.error, "skipped": self.watcher.skipped},
                "hotkeys": dict(self.hotkey_status), "stickers_open": len(self.stickers)}

    def destroy(self) -> None:
        self._quitting = True
        self.watcher.stop()
        for listener in list(self._hotkeys.values()):
            try:
                listener.stop()
            except Exception:
                pass
        self._hotkeys.clear()
        for sticker_id in list(self.stickers):
            self.close_sticker(sticker_id)
        window, self.overlay = self.overlay, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    def _set_hotkey(self, name: str, hotkey: str, callback, lang: str) -> Dict[str, Any]:
        listener = self._hotkeys.get(name)
        if not hotkey:
            if listener is not None:
                listener.clear()
            return {"ok": True, "hotkey": "", "code": "off", "error": ""}
        quick = str((self.manager.load_prefs() or {}).get("quick_ask_hotkey") or "")
        from .quick_ask_core import normalize_hotkey
        if normalize_hotkey(quick, "") == hotkey:
            return {"ok": False, "hotkey": hotkey, "code": "quick_ask",
                    "error": qf.tr(lang, "Это сочетание открывает сам быстрый доступ", "This is the Quick Ask hotkey itself")}
        if listener is not None and listener.active == hotkey and listener.status.get("ok"):
            return dict(listener.status)
        if listener is None:
            from .win_hotkey import HotkeyListener
            listener = self._hotkeys[name] = HotkeyListener(callback, lang=lang)
        listener.lang = lang
        return listener.set_hotkey(hotkey)

    def _hotkey_region(self) -> None:
        result = self.pick_region(self._lang())
        if result.get("cancelled") or not result.get("data"):
            return
        self.pending_region = result
        self.manager.show("api")
        self.manager._push("window.QuickFeatures && QuickFeatures.regionReady()")

    def _hotkey_clipboard(self) -> None:
        self.manager.show("hotkey")
        self.manager._push("window.QuickFeatures && QuickFeatures.open('clipboard')")

    def _lang(self) -> str:
        try:
            return "en" if (self.manager.load_prefs() or {}).get("language") == "en" else "ru"
        except Exception:
            return "ru"

    def _foreground_pid(self) -> int:
        try:
            hwnd = self.api.GetForegroundWindow()
            pid = self.api.wintypes.DWORD(0)
            self.api.GetWindowThreadProcessId(hwnd, self.api.ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    def clipboard_text(self) -> str:
        try:
            return self.api.get_text() or ""
        except Exception:
            return ""

    def copy(self, text: str) -> bool:
        return bool(self.manager.copy_text(text))

    def insert(self, text: str, cursor: int = 0) -> bool:
        """Paste into the program the window was opened over, put the caret at ``{cursor}`` and give the user
        their clipboard back when it held plain text (images/files there are left alone — we only replaced it)."""
        from .quick_ask import TEXT_FORMATS
        api = self.api
        formats = api.clipboard_formats()
        restore = -1 not in formats and not (formats - TEXT_FORMATS)
        original = api.get_text() if restore and formats else None
        if formats and original is None:
            restore = False
        self.watcher.quiet(3.0)
        if not self.manager.insert_text(text):
            return False
        if 0 < cursor <= 400:
            time.sleep(0.05)
            for _ in range(int(cursor)):
                api.chord(VK_LEFT)
        if restore:
            time.sleep(0.25)
            api.set_text(original)
            self.watcher.quiet(1.0)
        return True

    def session(self, token: str) -> Optional[qf.RegionSession]:
        return self._sessions.get(token) if token else None

    def take_pending_region(self) -> Optional[Dict[str, Any]]:
        result, self.pending_region = self.pending_region, None
        return result

    def pick_region(self, lang: str = "ru") -> Dict[str, Any]:
        """Freeze the monitor under the cursor, let the user drag a rectangle, return the crop (``encode_image``).

        An open Quick Ask window stays where it is and keeps its question, chips and attachments: it is only
        excluded from the capture (``WDA_EXCLUDEFROMCAPTURE``) and covered by the overlay. Before Windows 10 2004,
        where that flag does not exist, it is hidden for the capture and shown again."""
        if not self._region_lock.acquire(blocking=False):
            return {"error": qf.tr(lang, "Область уже выбирается", "A region is already being selected"), "code": "busy"}
        from .quick_ask import EXCLUDE_FROM_CAPTURE_BUILD, WDA_EXCLUDEFROMCAPTURE, WDA_NONE
        manager, api = self.manager, self.api
        was_visible = bool(manager.visible and manager.hwnd)
        excluded = hidden = layer_excluded = False
        layer = getattr(manager, "layer", None)
        layer_hwnd = int(getattr(layer, "hwnd", 0) or 0) if layer is not None and getattr(layer, "visible", False) else 0
        try:
            try:
                from PIL import ImageGrab
            except Exception as exc:
                return {"error": qf.tr(lang, f"Pillow недоступен: {exc}", f"Pillow is unavailable: {exc}"), "code": "pillow"}
            if was_visible:
                if manager.build >= EXCLUDE_FROM_CAPTURE_BUILD and api.SetWindowDisplayAffinity:
                    excluded = manager._affinity(manager.hwnd, WDA_EXCLUDEFROMCAPTURE)

                    if excluded and layer_hwnd:
                        layer_excluded = manager._affinity(layer_hwnd, WDA_EXCLUDEFROMCAPTURE)
                if not excluded:
                    hidden = manager.hide(restore_focus=True)
                    time.sleep(0.2)
            try:
                if api.DwmFlush:
                    api.DwmFlush()
            except Exception:
                pass
            from .quick_ask_core import pick_monitor
            monitors = api.monitors()
            if not monitors:
                return {"error": qf.tr(lang, "Не удалось получить список мониторов", "Could not list the monitors"), "code": "monitors"}
            monitor = monitors[pick_monitor(monitors, *api.cursor())]
            try:
                image = ImageGrab.grab(bbox=tuple(monitor["rect"]), all_screens=True, include_layered_windows=True)
            except Exception as exc:
                return {"error": qf.tr(lang, f"Снимок не удался: {exc}", f"Screenshot failed: {exc}"), "code": "grab"}
            finally:
                if excluded:
                    manager._affinity(manager.hwnd, WDA_NONE)
                if layer_excluded:
                    manager._affinity(layer_hwnd, WDA_NONE)
            session = qf.RegionSession(image, monitor, lang)
            self._sessions = {session.token: session}
            if not self._show_overlay(session):
                return {"error": qf.tr(lang, "Не удалось открыть окно выделения", "Could not open the selection overlay"), "code": "overlay"}
            if not session.done.wait(REGION_TIMEOUT):
                session.finish(None)
            self._hide_overlay()
            return dict(session.result)
        finally:
            self._sessions = {}
            self._region_lock.release()
            if was_visible and not self._quitting:
                if hidden:
                    manager.show("api")
                else:
                    manager.raise_to_top()
                    api.take_foreground(manager.hwnd)
                    manager._push("window.QuickAsk && QuickAsk.focusInput()")

    def _ensure_overlay(self, url: str) -> bool:
        if self.overlay_hwnd and self.api.IsWindow(self.overlay_hwnd):
            return True
        self._overlay_loaded.clear()
        options = dict(width=800, height=600, frameless=True, easy_drag=False, on_top=True, hidden=True, focus=True,
                       resizable=False, shadow=False, background_color="#000000")
        window = self._create_window("MemuAI · region", url, options)
        if window is None:
            return False
        window.events.loaded += lambda *_: self._overlay_loaded.set()
        window.events.closing += lambda *_: self._overlay_closing()
        form, hwnd = self._native_handle(window)
        if not hwnd:
            return False
        self.overlay, self.overlay_form, self.overlay_hwnd = window, form, hwnd
        self._own_windows()
        self._tool_window(hwnd)
        self._overlay_loaded.wait(8)
        return True

    def _overlay_closing(self) -> bool:
        if self._quitting:
            return True
        for session in list(self._sessions.values()):
            session.finish(None)
        return False

    def _show_overlay(self, session: qf.RegionSession) -> bool:
        url = f"{self.manager.base_url}/quick/region.html?native=1&token={session.token}&lang={session.lang}"
        created = not (self.overlay_hwnd and self.api.IsWindow(self.overlay_hwnd))
        if not self._ensure_overlay(url):
            return False
        if not created:
            payload = json.dumps({"token": session.token, "lang": session.lang})
            self._eval(self.overlay, f"window.RegionPick && RegionPick.start({payload})")
        left, top, right, bottom = (int(v) for v in session.monitor["rect"])
        from .quick_ask import HWND_TOPMOST, SWP_SHOWWINDOW
        self.api.SetWindowPos(self.overlay_hwnd, HWND_TOPMOST, left, top, right - left, bottom - top, SWP_SHOWWINDOW)
        self._ui(self.overlay_form, lambda: (self.overlay_form.Show(), None)[1])
        self.api.SetWindowPos(self.overlay_hwnd, HWND_TOPMOST, left, top, right - left, bottom - top, SWP_SHOWWINDOW)
        self.api.take_foreground(self.overlay_hwnd)
        self._ui(self.overlay_form, self._focus_overlay_browser)
        self._eval(self.overlay, "window.RegionPick && RegionPick.focus()")
        return True

    def _focus_overlay_browser(self) -> None:
        try:
            self.overlay_form.Activate()
            browser = getattr(self.overlay_form, "webview", None)
            if browser is not None:
                browser.Focus()
        except Exception:
            pass

    def _hide_overlay(self) -> None:
        if self.overlay_form is not None:
            self._eval(self.overlay, "window.RegionPick && RegionPick.reset()")
            self._ui(self.overlay_form, lambda: (self.overlay_form.Hide(), None)[1])

    def _restore_stickers(self) -> None:
        deadline = time.monotonic() + 60
        while not getattr(self.manager, "hwnd", 0) and time.monotonic() < deadline and not self._quitting:
            time.sleep(1.0)
        for row in qf.load_stickers():
            if row["open"] and not self._quitting:
                self.show_sticker(row["id"])

    def _sticker_geometry(self, row: Dict[str, Any]) -> tuple:
        from .quick_ask_core import pick_monitor
        monitors = self.api.monitors()
        geo = row.get("geometry") or {}
        if geo.get("w") and "x" in geo and "y" in geo:
            index = pick_monitor(monitors, int(geo["x"]) + int(geo["w"]) // 2, int(geo["y"]) + 12)
        else:
            index = pick_monitor(monitors, *self.api.cursor())
        monitor = monitors[index]
        scale = max(1.0, float(monitor.get("dpi") or 96) / 96.0)
        work = tuple(int(v) for v in (monitor.get("work") or monitor["rect"]))
        w = int(geo.get("w") or STICKER_SIZE[0] * scale)
        h = int(geo.get("h") or STICKER_SIZE[1] * scale)
        if "x" in geo and "y" in geo:
            x, y = int(geo["x"]), int(geo["y"])
        else:
            step = int(28 * scale) * (len(self.stickers) % 6)
            x, y = work[2] - w - int(24 * scale) - step, work[1] + int(24 * scale) + step
        return contain(x, y, w, h, work)

    def show_sticker(self, sticker_id: str) -> bool:
        if self._quitting:
            return False
        row = next((s for s in qf.load_stickers() if s["id"] == sticker_id), None)
        if row is None:
            return False
        entry = self.stickers.get(sticker_id)
        if entry and self.api.IsWindow(entry["hwnd"]):
            from .quick_ask import HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW
            self.api.SetWindowPos(entry["hwnd"], HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            return True
        if len(self.stickers) >= qf.STICKERS_OPEN:
            return False
        dark = (self.manager.load_prefs() or {}).get("theme") != "light"

        options = dict(width=STICKER_SIZE[0], height=STICKER_SIZE[1], frameless=True, easy_drag=False, on_top=True,
                       hidden=True, focus=True, resizable=False, shadow=True, min_size=(160, 90),
                       background_color="#1b1c20" if dark else "#f7f6f3")
        window = self._create_window("MemuAI · " + (row["title"] or "sticker")[:60],
                                     f"{self.manager.base_url}/quick/sticker.html?native=1&id={sticker_id}", options)
        if window is None:
            return False
        form, hwnd = self._native_handle(window)
        if not hwnd:
            return False
        window.events.closing += lambda *_: self._sticker_closing(sticker_id)
        self.stickers[sticker_id] = {"window": window, "form": form, "hwnd": hwnd}
        self._own_windows()
        self._tool_window(hwnd)
        x, y, w, h = self._sticker_geometry(row)
        from .quick_ask import HWND_TOPMOST, SWP_NOACTIVATE, SWP_SHOWWINDOW
        self.api.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        self._ui(form, lambda: (self.api.ShowWindow(hwnd, 4), None)[1])
        self.api.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return True

    def _sticker_closing(self, sticker_id: str) -> bool:

        if self._quitting or sticker_id not in self.stickers:
            return True

        threading.Thread(target=self._close_and_forget, args=(sticker_id,), daemon=True).start()
        return False

    def _close_and_forget(self, sticker_id: str) -> None:
        def close(rows: List[Dict[str, Any]]):
            for row in rows:
                if row["id"] == sticker_id:
                    row["open"] = False
        qf.mutate_stickers(close)
        self.close_sticker(sticker_id)

    def close_sticker(self, sticker_id: str) -> bool:
        """Close the window only; whether it comes back after a restart is the ``open`` flag in the file."""
        entry = self.stickers.pop(sticker_id, None)
        if entry is None:
            return False
        self._own_windows()
        try:
            entry["window"].destroy()
        except Exception:
            pass
        return True

    def refresh_sticker(self, sticker_id: str) -> None:
        entry = self.stickers.get(sticker_id)
        if entry is not None:
            self._eval(entry["window"], "window.Sticker && Sticker.reload()")

    def sticker_window_action(self, sticker_id: str, action: str) -> Dict[str, Any]:
        entry = self.stickers.get(sticker_id)
        if entry is None or not self.api.IsWindow(entry["hwnd"]):
            return {"ok": False}
        hwnd = entry["hwnd"]
        if action == "raise":
            self.api.take_foreground(hwnd)
            return {"ok": True}
        threading.Thread(target=self._follow, args=(sticker_id, hwnd, action), name="memuai-sticker-drag", daemon=True).start()
        return {"ok": True}

    def _follow(self, sticker_id: str, hwnd: int, action: str) -> None:
        """Move (``drag``) or resize (``resize``, bottom-right grip) while the primary button is held."""
        api = self.api
        rect = api.window_rect(hwnd)
        if not rect:
            return
        from .quick_ask import SWP_ASYNCWINDOWPOS, SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOZORDER
        start_x, start_y = api.cursor()
        left, top, right, bottom = rect
        x, y, w, h = left, top, right - left, bottom - top
        deadline = time.monotonic() + 120
        moved = False
        while api.button_down() and time.monotonic() < deadline:
            cx, cy = api.cursor()
            dx, dy = cx - start_x, cy - start_y
            if dx or dy:
                moved = True
                if action == "drag":
                    x, y = left + dx, top + dy
                    api.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
                else:
                    w, h = max(160, right - left + dx), max(90, bottom - top + dy)
                    api.SetWindowPos(hwnd, None, left, top, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            time.sleep(0.012)
        if not moved:
            return
        from .quick_ask_core import pick_monitor
        monitors = api.monitors()
        if monitors:
            monitor = monitors[pick_monitor(monitors, x + w // 2, y + 12)]
            work = tuple(int(v) for v in (monitor.get("work") or monitor["rect"]))
            fx, fy, fw, fh = contain(x, y, w, h, work)
            if (fx, fy, fw, fh) != (x, y, w, h):
                api.SetWindowPos(hwnd, None, fx, fy, fw, fh, SWP_NOZORDER | SWP_NOACTIVATE)
            x, y, w, h = fx, fy, fw, fh

        def save(rows: List[Dict[str, Any]]):
            for row in rows:
                if row["id"] == sticker_id:
                    row["geometry"] = {"x": x, "y": y, "w": w, "h": h}
        qf.mutate_stickers(save)

    def _create_window(self, title: str, url: str, options: Dict[str, Any]):
        try:
            import webview
        except Exception:
            return None
        for _attempt in range(2):
            try:
                return webview.create_window(title, url, **options)
            except TypeError:
                for key in ("focus", "shadow", "easy_drag"):
                    options.pop(key, None)
            except Exception as exc:
                log.warning("quick features: create_window failed: %s", exc)
                return None
        return None

    def _native_handle(self, window) -> tuple:
        deadline = time.monotonic() + 8
        form = None
        while time.monotonic() < deadline and form is None:
            form = getattr(window, "native", None)
            if form is None:
                time.sleep(0.05)
        if form is None:
            return None, 0
        hwnd = int(self._ui(form, lambda: int(form.Handle.ToInt64())) or 0)
        return form, hwnd

    def _tool_window(self, hwnd: int) -> None:
        from .quick_ask import GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW
        try:
            style = int(self.api.GetWindowLong(hwnd, GWL_EXSTYLE))
            self.api.SetWindowLong(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        except Exception:
            pass

    @staticmethod
    def _ui(form, fn):
        if form is None:
            return None
        try:
            from .win_titlebar import winforms_ui_invoker
            return winforms_ui_invoker(form)(fn)
        except Exception as exc:
            log.debug("quick features: UI call failed: %s", exc)
            return None

    @staticmethod
    def _eval(window, script: str) -> Any:
        if window is None:
            return None
        try:
            return window.evaluate_js(script)
        except Exception as exc:
            log.debug("quick features: evaluate_js failed: %s", exc)
            return None

def contain(x: int, y: int, w: int, h: int, work: tuple) -> tuple:
    """The whole window inside the work area (shrunk when the area is smaller)."""
    left, top, right, bottom = work
    w = max(1, min(int(w), right - left))
    h = max(1, min(int(h), bottom - top))
    return max(left, min(right - w, int(x))), max(top, min(bottom - h, int(y))), w, h
