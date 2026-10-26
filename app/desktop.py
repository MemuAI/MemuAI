"""Нативная оболочка MemuAI Proxy: окно WebView2 + трей.

Закрытие окна НЕ завершает приложение — оно остаётся жить в трее,
«Открыть» в трее пересоздаёт окно, «Выключить» завершает процесс.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Optional

_active_shell: "DesktopShell | None" = None

def wait_for_service(probe: Callable[[], bool], server_thread: threading.Thread,
                     *, attempts: int, interval: float) -> bool:
    """Wait for a local HTTP service without masking an early server crash."""
    for _ in range(max(1, attempts)):
        try:
            if probe():

                return server_thread.is_alive()
        except Exception:
            pass
        if not server_thread.is_alive():
            return False
        time.sleep(max(0.0, interval))
    return False

def stop_server(server, server_thread: threading.Thread, *, timeout: float = 8.0) -> None:
    """Give uvicorn and its lifespan handlers time to shut down cleanly."""
    server.should_exit = True
    if server_thread is threading.current_thread():
        return
    server_thread.join(max(0.0, timeout))
    if server_thread.is_alive():

        server.force_exit = True
        server_thread.join(1.0)

def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)

def message_box(title: str, text: str, error: bool = False) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10 if error else 0x40)
    except Exception:
        print(f"[{title}] {text}")

class InstanceLock:
    """Именованный lock текущей Windows-сессии.

    HTTP-порт для этого не подходит: второй процесс выбирал следующий свободный
    порт и поэтому тоже считал себя единственным экземпляром.
    """

    def __init__(self, handle=None, fallback=None) -> None:
        self.handle = handle
        self.fallback = fallback

    def close(self) -> None:
        if self.handle and sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
        if self.fallback:
            try:
                self.fallback.close()
            except Exception:
                pass
            self.fallback = None

def acquire_instance_lock(name: str = "MemuAIProxy") -> Optional[InstanceLock]:
    """Возвращает lock либо ``None``, если приложение уже запущено."""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.CreateMutexW(None, False, f"Local\\{name}.SingleInstance")
            if not handle:
                return None
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                return None
            return InstanceLock(handle=handle)
        except Exception:
            pass

    import socket
    port = 48731 if name == "MemuAIProxy" else 48732
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return InstanceLock(fallback=sock)
    except OSError:
        sock.close()
        return None

def show_active_window() -> bool:
    shell = _active_shell
    if not shell:
        return False
    shell.show_window()
    return True

def quit_active_shell() -> bool:
    shell = _active_shell
    if not shell:
        return False
    shell.request_quit()
    return True

def check_webview2() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import winreg
        keys = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in keys:
                try:
                    winreg.OpenKey(root, sub)
                    return True
                except OSError:
                    continue
    except Exception:
        return True
    return False

def try_install_webview2() -> bool:
    import subprocess
    import tempfile
    import urllib.request
    dst = ""
    try:
        handle, dst = tempfile.mkstemp(prefix="MemuAIProxy-WebView2-", suffix=".exe")
        os.close(handle)
        urllib.request.urlretrieve("https://go.microsoft.com/fwlink/p/?LinkId=2124703", dst)
        if not _is_trusted_windows_binary(dst):
            return False
        return subprocess.run([dst, "/silent", "/install"], timeout=300,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).returncode == 0
    except Exception:
        return False
    finally:
        if dst:
            try:
                os.remove(dst)
            except OSError:
                pass

def _is_trusted_windows_binary(path: str) -> bool:
    """Validate Authenticode with WinVerifyTrust without launching a shell."""
    if sys.platform != "win32" or not os.path.isfile(path):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class WINTRUST_FILE_INFO(ctypes.Structure):
            _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                        ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

        class WINTRUST_DATA(ctypes.Structure):
            _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                        ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                        ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                        ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                        ("dwStateAction", wintypes.DWORD), ("hWVTStateData", wintypes.HANDLE),
                        ("pwszURLReference", wintypes.LPCWSTR), ("dwProvFlags", wintypes.DWORD),
                        ("dwUIContext", wintypes.DWORD), ("pSignatureSettings", ctypes.c_void_p)]

        action = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                      (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        file_info = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), str(path), None, None)
        trust_data = WINTRUST_DATA(
            ctypes.sizeof(WINTRUST_DATA), None, None,
            2,
            0,
            1,
            ctypes.pointer(file_info),
            1, None, None, 0, 0, None,
        )
        verify = ctypes.windll.wintrust.WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
        verify.restype = ctypes.c_long
        trusted = verify(None, ctypes.byref(action), ctypes.byref(trust_data)) == 0
        trust_data.dwStateAction = 2
        verify(None, ctypes.byref(action), ctypes.byref(trust_data))
        return trusted
    except Exception:
        return False

class DesktopShell:
    """Окно + трей. Окно можно закрывать — приложение продолжает жить в трее."""

    def __init__(self, url: str, close_to_tray: bool = True, show_tray: bool = True,
                 show_on_start: bool = True, title: str = "MemuAI Proxy",
                 app_ref=None, on_ready=None, width: int = 1240, height: int = 860,
                 min_size: tuple[int, int] = (760, 520),

                 background_color: str = "#09090b",
                 tray_icon: str = "icon.png",
                 app_user_model_id: str = "", js_api=None,
                 tray_ico: str = "icon.ico", window_ico: str = "") -> None:
        self.url = url
        self.title = title
        self.app_ref = app_ref
        self.on_ready = on_ready
        self.width, self.height = width, height
        self.min_size = min_size
        self.background_color = background_color
        self._native_theme_initialized = False
        self.close_to_tray = close_to_tray
        self.show_tray = show_tray

        self.tray_icon = tray_icon

        self.tray_ico = tray_ico
        self.window_ico = window_ico
        self.app_user_model_id = app_user_model_id
        self.show_on_start = show_on_start

        self.js_api = js_api
        self.quit_event = threading.Event()

        self.quit_hooks: list = []
        self.tray = None
        self.window = None
        self._quitting = False

        self.close_guard = None

        self.ready_on_load = False

    def run(self) -> None:
        import webview
        global _active_shell
        _active_shell = self
        if sys.platform == "win32" and self.app_user_model_id:

            try:
                from . import win_app_identity
                win_app_identity.set_process_app_id(self.app_user_model_id)
            except Exception:
                pass
        tray_started = False
        if self.show_tray:
            tray_started = self._start_tray()

        extra = {"js_api": self.js_api} if self.js_api is not None else {}
        self.window = webview.create_window(
            self.title, self.url, width=self.width, height=self.height,
            min_size=self.min_size, background_color=self.background_color,

            frameless=False, easy_drag=False, resizable=True,

            hidden=not self.show_on_start and tray_started,
            **extra,
        )
        if self.app_ref is not None:
            try:
                self.app_ref.state.window = self.window
            except Exception:
                pass
        if sys.platform == "win32":
            for name in ("shown", "loaded"):
                event = getattr(self.window.events, name, None)
                if event is not None:
                    event += self._apply_native_theme
        if sys.platform == "win32" and self.window_ico:
            self.window.events.shown += self._apply_window_icon

            self.window.events.loaded += self._apply_window_icon
        if self.on_ready:
            self.window.events.shown += lambda: self.on_ready(self.window)
            if self.ready_on_load:
                self.window.events.loaded += lambda *_: self.on_ready(self.window)
        self.window.events.closing += self._on_closing
        try:
            try:
                webview.start(gui="edgechromium")
            except TypeError:
                webview.start()
        finally:
            self.quit_event.set()
            self._stop_tray()
            self.window = None
            _active_shell = None

    def _apply_native_theme(self, *_args) -> None:
        """CSS does not paint the native frame or backing pixels at DPI resize edges."""
        if getattr(self, "_native_theme_initialized", False):
            return
        try:
            from . import win_app_identity, win_window_theme
            from .win_titlebar import winforms_ui_invoker
            native = getattr(self.window, "native", None)
            if native is None:
                return
            color = win_window_theme.background(self.background_color)
            rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
            dark = rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114 < 128
            hwnd = win_app_identity.native_hwnd(self.window)
            self._native_theme_initialized = bool(winforms_ui_invoker(native)(lambda: win_window_theme.apply(native, hwnd, dark, color)))
        except Exception:
            pass

    def _apply_window_icon(self, *_args) -> None:
        """Crisp per-DPI icons from the .ico (pywebview only extracts the exe's 32-px one)."""
        try:
            from . import win_app_identity
            hwnd = win_app_identity.native_hwnd(self.window)
            if hwnd:
                win_app_identity.WINDOW_ICONS.apply(hwnd, self._resolve(self.window_ico))
        except Exception:
            pass

    def set_window_icon(self, ico_path: str) -> bool:
        """Swap the window icon (MemuAI: the assistant's look changed)."""
        self.window_ico = ico_path or self.window_ico
        try:
            from . import win_app_identity
            hwnd = win_app_identity.native_hwnd(self.window)
            return bool(hwnd) and win_app_identity.WINDOW_ICONS.apply(hwnd, self._resolve(self.window_ico))
        except Exception:
            return False

    @staticmethod
    def _resolve(name: str) -> str:
        if not name:
            return ""
        return name if os.path.isabs(name) else resource_path(name)

    def _on_closing(self):
        if self.close_guard is not None and not self._quitting:
            try:
                if self.close_guard():
                    return False
            except Exception:
                pass
        if self.close_to_tray and self.tray is not None and not self._quitting:
            try:
                self.window.hide()
            except Exception:
                pass

            return False
        self.quit_event.set()
        self._run_quit_hooks()
        return True

    def _run_quit_hooks(self) -> None:
        hooks, self.quit_hooks = list(self.quit_hooks), []
        for hook in hooks:
            try:
                hook()
            except Exception:
                pass

    def show_window(self, *args) -> None:
        if not self.window:
            return
        try:
            self.window.show()
            if sys.platform == "win32":
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, self.title)
                if hwnd and ctypes.windll.user32.IsIconic(hwnd):
                    self.window.restore()
        except Exception:
            pass

    def request_quit(self, *args) -> None:
        self._quitting = True
        self.quit_event.set()
        self._run_quit_hooks()
        self._stop_tray()
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass

    def _stop_tray(self) -> None:
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None

    def _tray_icon_paths(self) -> list:
        names = [self.tray_icon, "icon.png"]
        paths = []
        for name in names:
            if not name:
                continue
            paths.append(name if os.path.isabs(name) else resource_path(name))
        return paths

    def _tray_image(self):
        from PIL import Image
        for candidate in self._tray_icon_paths():
            try:
                if candidate and os.path.exists(candidate):
                    return Image.open(candidate)
            except Exception:
                continue
        return Image.new("RGB", (64, 64), (124, 58, 237))

    def set_tray_icon(self, path: str, ico: str = "") -> bool:
        """Swap the live tray image after the user changed the app icon."""
        if not self.tray:
            return False
        try:
            from PIL import Image

            self.tray._memu_ico = self._resolve(ico) if ico else ""
            self.tray.icon = Image.open(path)
            return True
        except Exception:
            return False

    def _start_tray(self) -> bool:
        try:
            import pystray
        except ImportError:
            return False
        try:
            image = self._tray_image()

            icon_id = "".join(ch.lower() if ch.isalnum() else "_" for ch in self.title).strip("_") or "memuai"
            self.tray = _crisp_tray_class(pystray)(
                icon_id, image, self.title,
                menu=pystray.Menu(
                pystray.MenuItem(f"Открыть {self.title}", self.show_window, default=True),
                    pystray.MenuItem("Выключить", self.request_quit),
                ))
            self.tray._memu_ico = self._resolve(self.tray_ico) if self.tray_ico else ""
            threading.Thread(target=self.tray.run, daemon=True).start()
            return True
        except Exception:
            self.tray = None
            return False

def refresh_tray_icon(path: str, ico: str = "") -> bool:
    """Update the running shell's tray (and window) icon, if a shell is active."""
    shell = _active_shell
    if shell is None:
        return False
    if ico:
        shell.set_window_icon(ico)
    return bool(shell.set_tray_icon(path, ico))

def _crisp_tray_class(pystray):
    """pystray's Icon that, on Windows, loads the tray icon from our .ico at the tray size.

    pystray saves the PIL image to a temporary .ico and loads it with
    ``LR_DEFAULTSIZE`` — the 32-px frame, which the shell then shrinks to 16 px
    (blurry). With ``_memu_ico`` set the frame drawn for SM_CXSMICON is used.
    Any problem falls back to pystray's own path.
    """
    base = pystray.Icon
    if sys.platform != "win32" or not hasattr(base, "_assert_icon_handle"):
        return base

    class CrispIcon(base):
        _memu_ico = ""

        def _assert_icon_handle(self):
            if getattr(self, "_icon_handle", None):
                return
            path = getattr(self, "_memu_ico", "")
            if path and os.path.isfile(path):
                try:
                    from . import win_app_identity
                    win = win_app_identity.api()
                    if win is not None:
                        handle = win.load_icon(path, win.tray_icon_size())
                        if handle:
                            self._icon_handle = handle
                            return
                except Exception:
                    pass
            super()._assert_icon_handle()

    return CrispIcon

_LOCAL_PERMISSIONS = {"Microphone", "Camera", "ClipboardRead"}

def grant_local_permissions(native, origin: str) -> bool:
    """Stop WebView2 asking "Allow microphone / clipboard?" for our own page.

    The prompt is a browser safeguard for websites; MemuAI's UI is a local
    app page, so the question only confuses people. The handler checks the
    requesting URI's origin exactly — a page loaded from anywhere else
    (a module frame from another origin, an external site) still gets the
    normal behaviour. Must run on the WinForms UI thread.
    """
    if sys.platform != "win32" or native is None:
        return False
    try:
        from System import Func, Type  # type: ignore
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState  # type: ignore
    except Exception:
        return False
    allowed_origin = str(origin or "").rstrip("/").lower()

    def on_permission(_sender, args) -> None:
        try:
            uri = str(args.Uri or "").lower()
            same_origin = uri == allowed_origin or uri.startswith(allowed_origin + "/")
            kind = args.PermissionKind
            try:
                number = int(kind)
            except Exception:
                number = -1

            if same_origin and (str(kind).split(".")[-1] in _LOCAL_PERMISSIONS or number in (1, 2, 6)):
                args.State = CoreWebView2PermissionState.Allow
        except Exception:
            pass

    def attach() -> None:
        webview = getattr(native, "webview", None)
        if webview is None:
            return
        if webview.CoreWebView2 is not None:
            webview.CoreWebView2.PermissionRequested += on_permission
            return

        def ready(_sender, args) -> None:
            try:
                if args.IsSuccess and webview.CoreWebView2 is not None:
                    webview.CoreWebView2.PermissionRequested += on_permission
            except Exception:
                pass
        webview.CoreWebView2InitializationCompleted += ready

    try:
        if getattr(native, "InvokeRequired", False):
            native.Invoke(Func[Type](lambda: attach()))
        else:
            attach()
        native._memuai_permission_handler = on_permission
        return True
    except Exception:
        return False

def ensure_per_monitor_dpi_awareness() -> None:
    """Ask Windows for per-monitor-v2 DPI awareness before any window exists.

    pywebview's WinForms backend later calls the legacy ``SetProcessDPIAware``
    (system-DPI-aware only), which is fine on a single monitor but misaligns
    every screen coordinate — screenshots, clicks, window rects — on a
    multi-monitor setup where screens use different scaling. Windows honours
    only the *first* successful DPI-awareness call per process, so setting
    the modern per-monitor-v2 mode here, before ``webview.start()`` runs,
    makes pywebview's later call a harmless no-op and gives every ``ctypes``
    call in ``automation_runtime`` (Computer Use) real physical pixels on
    every monitor.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

def run_desktop(url: str, port: int, close_to_tray: bool = True,
                show_tray: bool = True, show_on_start: bool = True) -> int:
    ensure_per_monitor_dpi_awareness()
    if not check_webview2():
        message_box(
            "MemuAI Proxy",
            "Не установлен компонент WebView2 (браузерный движок Windows). "
            "Сейчас попробую установить его автоматически (официальный компонент Microsoft).")
        if not try_install_webview2():
            message_box(
                "MemuAI Proxy",
                "Не удалось установить WebView2 автоматически. "
                "Скачайте вручную: https://developer.microsoft.com/microsoft-edge/webview2/ "
                "и запустите MemuAI Proxy снова.",
                error=True)
            return 1

    shell = DesktopShell(url, close_to_tray=close_to_tray,
                         show_tray=show_tray, show_on_start=show_on_start,
                         tray_ico="icon.ico", window_ico="icon.ico")
    try:
        shell.run()
    except Exception as e:
        message_box("MemuAI Proxy",
                    f"Окно не открылось: {e}. "
                    "Попробуйте снова; если повторяется — используйте MemuAIProxy.exe --headless.",
                    error=True)
        return 1
    return 0
