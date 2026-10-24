"""Windows implementation of :class:`installer.engine.Platform` (ctypes + winreg).

Every Win32 call declares ``argtypes``/``restype`` (64-bit handles and
pointers); every helper that is not essential degrades instead of failing.
Shortcuts are created through the shell's own ``IShellLinkW`` COM object
(called through its vtable with ctypes — no PowerShell, no pywin32); the
PowerShell ``WScript.Shell`` path is only a fallback when COM is unavailable.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .engine import Platform

log = logging.getLogger("memuai.setup")

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

FOLDERS = {
    "programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
}
WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_BOOTSTRAPPER = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def clean_environment() -> Dict[str, str]:
    """Environment for programs we start: no PyInstaller one-file bookkeeping.

    A frozen MemuAI started with the Setup's ``_PYI_*`` variables would think it
    is a child of the Setup's bootloader; PyInstaller documents
    ``PYINSTALLER_RESET_ENVIRONMENT`` for exactly this case.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("_PYI_") and k.upper() not in {"_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"}}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        value = uuid.UUID(text)
        guid = cls()
        guid.Data1, guid.Data2, guid.Data3 = value.time_low, value.time_mid, value.time_hi_version
        for i, byte in enumerate(value.bytes[8:]):
            guid.Data4[i] = byte
        return guid


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class _PVUnion(ctypes.Union):
    _fields_ = [("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_ulonglong * 2)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("u", _PVUnion)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def _com_method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[index])
    return lambda *args: function(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj and obj.value:
        try:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)
        except Exception:  # noqa: BLE001
            pass


def create_shell_link(link: Path, target: Path, args: str = "", workdir: str = "", icon: str = "",
                      description: str = "", app_id: str = "") -> None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    initialized = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    link_obj = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    store = ctypes.c_void_p()
    try:
        clsid = GUID.parse("{00021401-0000-0000-C000-000000000046}")
        iid_link = GUID.parse("{000214F9-0000-0000-C000-000000000046}")
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid_link), ctypes.byref(link_obj))
        if hr < 0 or not link_obj.value:
            raise OSError(f"CoCreateInstance(ShellLink) failed: 0x{hr & 0xFFFFFFFF:08X}")
        _com_method(link_obj, 20, wintypes.LPCWSTR)(str(target))                 # SetPath
        if args:
            _com_method(link_obj, 11, wintypes.LPCWSTR)(args)                     # SetArguments
        _com_method(link_obj, 9, wintypes.LPCWSTR)(workdir or str(target.parent))  # SetWorkingDirectory
        if description:
            _com_method(link_obj, 7, wintypes.LPCWSTR)(description)               # SetDescription
        if icon:
            _com_method(link_obj, 17, wintypes.LPCWSTR, ctypes.c_int)(icon, 0)    # SetIconLocation
        query = _com_method(link_obj, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        if app_id:
            try:
                query(ctypes.byref(GUID.parse("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")), ctypes.byref(store))
                key = PROPERTYKEY(GUID.parse("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)  # AppUserModel.ID
                value = PROPVARIANT()
                value.vt = 31  # VT_LPWSTR
                buffer = ctypes.create_unicode_buffer(app_id)
                value.u.pwszVal = ctypes.cast(buffer, ctypes.c_wchar_p)
                _com_method(store, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
                    ctypes.byref(key), ctypes.byref(value))
                _com_method(store, 7)()                                             # Commit
            except OSError as exc:
                log.warning("AppUserModelID not set on %s: %s", link, exc)
        query(ctypes.byref(GUID.parse("{0000010b-0000-0000-C000-000000000046}")), ctypes.byref(persist))
        _com_method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(str(link), True)  # IPersistFile::Save
    finally:
        for obj in (store, persist, link_obj):
            _release(obj)
        if initialized in (0, 1):
            ole32.CoUninitialize()


def _powershell_link(link: Path, target: Path, args: str, workdir: str, icon: str) -> None:
    def quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    script = "; ".join([
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + quote(link) + ")",
        "$s.TargetPath = " + quote(target), "$s.Arguments = " + quote(args),
        "$s.WorkingDirectory = " + quote(workdir or target.parent), "$s.IconLocation = " + quote(f"{icon},0"),
        "$s.Save()"])
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise OSError(f"shortcut fallback failed: {result.stderr.decode(errors='replace')[:200]}")


class WindowsPlatform(Platform):
    def __init__(self) -> None:
        import winreg
        self.winreg = winreg
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel32
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    # ------------------------------------------------------------------ folders
    def known_folder(self, name: str) -> Path:
        try:
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                     ctypes.POINTER(ctypes.c_wchar_p)]
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole32.CoTaskMemFree.restype = None
            out = ctypes.c_wchar_p()
            hr = shell32.SHGetKnownFolderPath(ctypes.byref(GUID.parse(FOLDERS[name])), 0, None, ctypes.byref(out))
            if hr == 0 and out.value:
                path = Path(out.value)
                ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
                return path
        except Exception as exc:  # noqa: BLE001
            log.warning("SHGetKnownFolderPath(%s) failed: %s", name, exc)
        if name == "programs":
            return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"

    # ------------------------------------------------------------------ registry
    def _root(self, root: str):
        w = self.winreg
        if root == "HKCU":
            return w.HKEY_CURRENT_USER, 0
        if root == "HKLM64":
            return w.HKEY_LOCAL_MACHINE, w.KEY_WOW64_64KEY
        if root == "HKLM32":
            return w.HKEY_LOCAL_MACHINE, w.KEY_WOW64_32KEY
        return w.HKEY_LOCAL_MACHINE, 0

    def reg_get(self, root: str, key: str, name: str):
        hive, view = self._root(root)
        try:
            with self.winreg.OpenKey(hive, key, 0, self.winreg.KEY_READ | view) as handle:
                return self.winreg.QueryValueEx(handle, name)[0]
        except FileNotFoundError:
            return None

    def reg_values(self, root: str, key: str) -> Dict[str, object]:
        hive, view = self._root(root)
        values: Dict[str, object] = {}
        try:
            with self.winreg.OpenKey(hive, key, 0, self.winreg.KEY_READ | view) as handle:
                index = 0
                while True:
                    try:
                        name, value, _kind = self.winreg.EnumValue(handle, index)
                    except OSError:
                        break
                    values[name] = value
                    index += 1
        except FileNotFoundError:
            return {}
        return values

    def reg_set(self, root: str, key: str, name: str, value, kind: str = "sz") -> None:
        hive, view = self._root(root)
        w = self.winreg
        kinds = {"sz": w.REG_SZ, "expand_sz": w.REG_EXPAND_SZ, "dword": w.REG_DWORD}
        with w.CreateKeyEx(hive, key, 0, w.KEY_WRITE | view) as handle:
            w.SetValueEx(handle, name, 0, kinds[kind], value)

    def reg_delete_value(self, root: str, key: str, name: str) -> None:
        hive, view = self._root(root)
        try:
            with self.winreg.OpenKey(hive, key, 0, self.winreg.KEY_SET_VALUE | view) as handle:
                self.winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass

    def reg_delete_key(self, root: str, key: str) -> None:
        hive, view = self._root(root)
        try:
            self.winreg.DeleteKeyEx(hive, key, view, 0)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------ shortcuts
    def create_shortcut(self, link: Path, target: Path, args: str = "", workdir: str = "",
                        icon: str = "", description: str = "", app_id: str = "") -> None:
        try:
            create_shell_link(link, target, args, workdir, icon, description, app_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("IShellLink failed for %s (%s); using the WScript.Shell fallback", link, exc)
            _powershell_link(link, target, args, workdir, icon)

    # ------------------------------------------------------------------ processes
    def processes_in(self, folder: Path) -> List[Tuple[int, str]]:
        k = self.kernel32
        prefix = os.path.normcase(os.path.abspath(str(folder))).rstrip("\\") + "\\"
        own = {os.getpid(), os.getppid()}
        found: List[Tuple[int, str]] = []
        snapshot = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return found
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                if pid and pid not in own:
                    handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if handle:
                        try:
                            size = wintypes.DWORD(32768)
                            buffer = ctypes.create_unicode_buffer(size.value)
                            if k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                                image = buffer.value
                                if os.path.normcase(image).startswith(prefix):
                                    found.append((pid, image))
                        finally:
                            k.CloseHandle(handle)
                ok = k.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k.CloseHandle(snapshot)
        return found

    def terminate(self, pid: int) -> None:
        k = self.kernel32
        handle = k.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not handle:
            raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
        try:
            if not k.TerminateProcess(handle, 1):
                raise OSError(ctypes.get_last_error(), f"TerminateProcess({pid}) failed")
        finally:
            k.CloseHandle(handle)

    def run_wait(self, exe: Path, args: Sequence[str], timeout: float) -> int:
        try:
            return subprocess.run([str(exe), *args], timeout=timeout, env=clean_environment(),
                                  cwd=str(Path(exe).parent), creationflags=CREATE_NO_WINDOW,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode
        except subprocess.TimeoutExpired:
            return -1

    def launch(self, exe: Path, args: Sequence[str], cwd: Optional[Path] = None) -> None:
        subprocess.Popen([str(exe), *args], cwd=str(cwd or Path(exe).parent), env=clean_environment(),
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ misc
    def refresh_shell_icons(self, paths: Sequence[Path]) -> None:
        """Round 33: SHCNE_UPDATEITEM for each shortcut/exe, then SHCNE_ASSOCCHANGED.

        After an update that changes an exe icon Explorer would keep showing the
        cached old icon — or a white page — for the shortcuts and pinned entries.
        """
        shell32 = ctypes.windll.shell32
        shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        shell32.SHChangeNotify.restype = None
        for path in paths:
            try:
                buffer = ctypes.create_unicode_buffer(str(path))
                shell32.SHChangeNotify(0x00002000, 0x0005 | 0x3000, ctypes.cast(buffer, ctypes.c_void_p), None)
            except OSError as exc:
                log.debug("SHChangeNotify(%s): %s", path, exc)
        try:
            shell32.SHChangeNotify(0x08000000, 0x0000 | 0x3000, None, None)   # SHCNE_ASSOCCHANGED
        except OSError as exc:
            log.debug("SHChangeNotify(ASSOCCHANGED): %s", exc)

    def broadcast_environment(self) -> None:
        user32 = ctypes.windll.user32
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                                               wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t
        result = ctypes.c_size_t()
        # HWND_BROADCAST, WM_SETTINGCHANGE, lParam "Environment", SMTO_ABORTIFHUNG, 5 s.
        user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result))

    def webview2_version(self) -> str:
        for root, key in (("HKLM32", "SOFTWARE\\" + WEBVIEW2_CLIENT), ("HKLM64", "SOFTWARE\\" + WEBVIEW2_CLIENT),
                          ("HKCU", "Software\\" + WEBVIEW2_CLIENT)):
            try:
                value = str(self.reg_get(root, key, "pv") or "")
            except OSError:
                value = ""
            if value and value != "0.0.0.0":
                return value
        return ""


    def file_version(self, path: Path) -> str:
        """``GetFileVersionInfoW`` → ``VS_FIXEDFILEINFO``; the pure-Python reader is the fallback."""
        try:
            version = ctypes.WinDLL("version")
            version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
            version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
            version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
            version.GetFileVersionInfoW.restype = wintypes.BOOL
            version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.POINTER(wintypes.UINT)]
            version.VerQueryValueW.restype = wintypes.BOOL
            handle = wintypes.DWORD(0)
            size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
            if size:
                buffer = ctypes.create_string_buffer(size)
                if version.GetFileVersionInfoW(str(path), 0, size, buffer):
                    pointer, length = ctypes.c_void_p(), wintypes.UINT(0)
                    if version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) \
                            and pointer.value and length.value >= 52:
                        fixed = (wintypes.DWORD * 13).from_address(pointer.value)
                        if fixed[0] == 0xFEEF04BD:
                            ms, ls = int(fixed[2]), int(fixed[3])
                            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception as exc:  # noqa: BLE001
            log.info("GetFileVersionInfoW failed for %s: %s", path, exc)
        return super().file_version(path)


# ---------------------------------------------------------------------- helpers used by main/bridge

def ui_language() -> str:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetUserDefaultUILanguage.argtypes = []
        kernel32.GetUserDefaultUILanguage.restype = wintypes.WORD
        primary = int(kernel32.GetUserDefaultUILanguage()) & 0x3FF
        return "ru" if primary in (0x19, 0x23, 0x3F) else "en"
    except Exception:  # noqa: BLE001
        return "en"


def message_box(text: str, title: str, flags: int = 0x40) -> int:
    """MB_* flags; returns IDOK=1, IDCANCEL=2, IDYES=6, IDNO=7."""
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        return int(user32.MessageBoxW(None, text, title, flags | 0x00010000))  # MB_SETFOREGROUND
    except Exception:  # noqa: BLE001
        print(f"{title}: {text}", file=sys.stderr)
        return 2


def single_instance(name: str):
    """Return a mutex handle, or None when another instance already holds it."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if handle and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(handle)
            return None
        return handle or True
    except Exception:  # noqa: BLE001
        return True


def release_instance(handle) -> None:
    """Close the named-mutex handle when an embedded/silent installer returns."""
    if handle is None or isinstance(handle, bool):
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def style_window(hwnd: int) -> None:
    """Windows 11 rounded corners (+ DWM shadow) for the frameless installer window."""
    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))

        class MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
        dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
        dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        margins = MARGINS(0, 0, 1, 0)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    except Exception as exc:  # noqa: BLE001
        log.info("window styling skipped: %s", exc)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def window_dpi(hwnd: int) -> int:
    """DPI the window is rendered at (WebView2 scales its content by the same factor)."""
    user32 = ctypes.windll.user32
    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        dpi = int(user32.GetDpiForWindow(hwnd))
        if dpi:
            return dpi
    except (AttributeError, OSError):
        pass                                   # Windows before 10 1607
    try:
        user32.GetDpiForSystem.argtypes = []
        user32.GetDpiForSystem.restype = wintypes.UINT
        dpi = int(user32.GetDpiForSystem())
        if dpi:
            return dpi
    except (AttributeError, OSError):
        pass
    try:
        gdi32 = ctypes.windll.gdi32
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        hdc = user32.GetDC(None)
        try:
            return int(gdi32.GetDeviceCaps(hdc, 88)) or 96   # LOGPIXELSX
        finally:
            user32.ReleaseDC(None, hdc)
    except (AttributeError, OSError):
        return 96


def cursor_work_area() -> Tuple[int, int, int, int]:
    """Work area (left, top, right, bottom; physical pixels) of the monitor under the cursor."""
    user32 = ctypes.windll.user32
    try:
        point = wintypes.POINT()
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        if not user32.GetCursorPos(ctypes.byref(point)):
            point = wintypes.POINT(0, 0)
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(point, 2)          # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            if work.right > work.left and work.bottom > work.top:
                return int(work.left), int(work.top), int(work.right), int(work.bottom)
    except (AttributeError, OSError):
        pass
    rect = _RECT()
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    return 0, 0, 1920, 1040


def window_frame(hwnd: int) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """((outer width, outer height), (client width, client height)) in physical pixels."""
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    outer, client = _RECT(), _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(outer)) or not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise OSError("GetWindowRect/GetClientRect failed")
    return ((outer.right - outer.left, outer.bottom - outer.top),
            (client.right - client.left, client.bottom - client.top))


def place_window(hwnd: int, logical: Tuple[int, int], fit) -> dict:
    """Give the window a CLIENT area of ``logical`` × DPI scale, clamped to the work area
    of the monitor under the cursor and centred on it. ``fit`` is ``main.fit_window``."""
    scale = window_dpi(hwnd) / 96.0
    work = cursor_work_area()
    (ow, oh), (cw, ch) = window_frame(hwnd)
    nonclient = (max(0, ow - cw), max(0, oh - ch))
    geometry = fit(logical, scale, work, nonclient)
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    # SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER
    if not user32.SetWindowPos(hwnd, None, geometry["x"], geometry["y"], geometry["width"], geometry["height"],
                               0x0004 | 0x0010 | 0x0200):
        raise OSError("SetWindowPos failed")
    geometry["work"] = work
    geometry["nonclient"] = nonclient
    return geometry


def ensure_client_size(hwnd: int, client: Tuple[int, int]) -> Optional[Tuple[int, int]]:
    """After the window is shown: grow/shrink the outer frame so the client area is
    exactly ``client`` (physical px), keeping the centre. Returns the corrected
    client size, or None when it already matched."""
    (ow, oh), (cw, ch) = window_frame(hwnd)
    dw, dh = client[0] - cw, client[1] - ch
    if abs(dw) <= 1 and abs(dh) <= 1:
        return None
    user32 = ctypes.windll.user32
    rect = _RECT()
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos(hwnd, None, rect.left - dw // 2, rect.top - dh // 2, ow + dw, oh + dh, 0x0004 | 0x0010 | 0x0200)
    return window_frame(hwnd)[1]


WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2


def _pack_point(x: int, y: int) -> int:
    """MAKELPARAM for screen coordinates (each a signed 16-bit value; negative on a left/upper monitor)."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def primary_button_down() -> bool:
    """Is the (logical) primary mouse button physically held right now?"""
    user32 = ctypes.windll.user32
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    swapped = bool(user32.GetSystemMetrics(23))        # SM_SWAPBUTTON: GetAsyncKeyState sees physical buttons
    return bool(user32.GetAsyncKeyState(0x02 if swapped else 0x01) & 0x8000)


def begin_window_drag(hwnd: int) -> bool:
    """Start the system move loop for a frameless window, as if its caption was pressed.

    MUST run on the window's GUI thread (main.py schedules it with BeginInvoke). The
    WebView2 child holds mouse capture, so we release it and hand the press to
    DefWindowProc as WM_NCLBUTTONDOWN/HTCAPTION: Windows then drags the window natively
    (smooth, multi-monitor, Aero Snap to edges) until the button is released. The form
    has no maximize box (resizable=False), so a double click never maximizes. When the
    button is already up (a quick click) nothing happens. Returns True when the loop ran.
    """
    try:
        if not hwnd or not primary_button_down():
            return False
        user32 = ctypes.windll.user32
        point = wintypes.POINT()
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False
        user32.ReleaseCapture.argtypes = []
        user32.ReleaseCapture.restype = wintypes.BOOL
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = ctypes.c_ssize_t     # LRESULT (LONG_PTR)
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, _pack_point(point.x, point.y))
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("window drag unavailable: %s", exc)
        return False


def download_webview2(target_dir: Path, timeout: float = 60) -> Path:
    """Download Microsoft's Evergreen WebView2 bootstrapper (signed by Microsoft)."""
    import urllib.request
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "MicrosoftEdgeWebview2Setup.exe"
    request = urllib.request.Request(WEBVIEW2_BOOTSTRAPPER, headers={"User-Agent": "MemuAI-Setup"})
    with urllib.request.urlopen(request, timeout=timeout) as response, path.open("wb") as handle:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            handle.write(chunk)
    return path


def install_webview2(target_dir: Path) -> None:
    bootstrapper = download_webview2(target_dir)
    # /silent /install is the documented unattended mode of the bootstrapper; without
    # administrator rights it installs per-user.
    result = subprocess.run([str(bootstrapper), "/silent", "/install"], timeout=600, env=clean_environment())
    if result.returncode != 0:
        raise OSError(f"WebView2 bootstrapper exit code {result.returncode}")
