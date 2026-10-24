"""Round 29: the Windows half of :mod:`app.quick_apps` / :mod:`app.quick_explorer` / :mod:`app.quick_extras`.

Everything here is guarded: off Windows each function returns an empty result (the tests replace the module's
functions with fakes). Shell COM, Office COM and Windows OCR are reached through a short ``powershell.exe``
script — the bundle has no pywin32/comtypes and ``Marshal.GetActiveObject`` is not in the .NET runtime pythonnet
may load. Values reach the script ONLY through environment variables (never formatted into the source), so a
folder or a window title cannot inject PowerShell code.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

log = logging.getLogger("memuai.quick.apps.win")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


def run_ps(script: str, env: Optional[Dict[str, str]] = None, timeout: float = 8.0) -> Dict[str, Any]:
    """Run a PowerShell script that prints ONE JSON value; ``{"error"}`` on failure."""
    if not IS_WINDOWS:
        return {"error": "unsupported"}
    full = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
            "$ErrorActionPreference='Stop'; " + script)
    environ = dict(os.environ)
    for key, value in (env or {}).items():
        environ[key] = str(value)
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", "-"], input=full.encode("utf-8"), capture_output=True, timeout=timeout,
                              env=environ, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except OSError as exc:
        return {"error": str(exc)}
    out = proc.stdout.decode("utf-8", "replace").strip()
    if not out:
        err = proc.stderr.decode("utf-8", "replace").strip()
        return {"error": err[-400:] or "empty"}
    for candidate in (out, out.splitlines()[-1]):
        try:
            return {"value": json.loads(candidate)}
        except ValueError:
            continue
    return {"error": "bad output"}


# ---------------------------------------------------------------- windows

def describe_window(hwnd: int) -> Dict[str, Any]:
    """``{"class", "title", "exe", "pid", "color"?}`` of a top-level window (``{}`` off Windows)."""
    if not IS_WINDOWS or not hwnd:
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        from . import quick_ask_browser as qb
        info = dict(qb.foreground_info(hwnd))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 255)
        info["class"] = buffer.value
        color = pixel_under_cursor()
        if color:
            info["color"] = color
        return info
    except Exception as exc:
        log.debug("describe_window failed: %s", exc)
        return {}


def pixel_under_cursor() -> Optional[Dict[str, Any]]:
    """The screen colour under the mouse pointer right now (``None`` off Windows)."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.GetPixel.restype = wintypes.DWORD
        dc = user32.GetDC(None)
        try:
            value = int(gdi32.GetPixel(dc, point.x, point.y))
        finally:
            user32.ReleaseDC(None, dc)
        if value == 0xFFFFFFFF:      # CLR_INVALID (a protected surface)
            return None
        r, g, b = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
        return {"hex": "#%02X%02X%02X" % (r, g, b), "r": r, "g": g, "b": b, "x": point.x, "y": point.y}
    except Exception:
        return None


_KNOWN = {"desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
          "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
          "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}", "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"}


def known_folder(name: str) -> str:
    if not IS_WINDOWS or name not in _KNOWN:
        return ""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]
        raw = uuid.UUID(_KNOWN[name]).bytes_le
        guid = GUID.from_buffer_copy(raw)
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        path = ctypes.c_wchar_p()
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) != 0:
            return ""
        try:
            return str(path.value or "")
        finally:
            ole32.CoTaskMemFree(path)
    except Exception:
        return ""


# ---------------------------------------------------------------- Explorer

EXPLORER_SCRIPT = r"""
$h = [int64]$env:MEMU_HWND
$title = [string]$env:MEMU_TITLE
$sh = New-Object -ComObject Shell.Application
$rows = @()
foreach ($w in @($sh.Windows())) {
  try {
    if ([int64]$w.HWND -ne $h) { continue }
    $doc = $w.Document
    $p = [string]$doc.Folder.Self.Path
    $sel = @()
    foreach ($i in @($doc.SelectedItems())) { $sel += [string]$i.Path; if ($sel.Count -ge 500) { break } }
    $rows += [pscustomobject]@{ path = $p; name = [string]$w.LocationName; selected = $sel }
  } catch { }
}
ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
"""


def pick_explorer_tab(rows: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    """Windows 11 tabs share one HWND: the active tab is the one whose name is the window title."""
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    clean = str(title or "").strip().lower()
    for suffix in (" - file explorer", " — проводник", " - проводник", " - explorer"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    for row in rows:
        if str(row.get("name") or "").strip().lower() == clean:
            return row
    for row in rows:
        if os.path.basename(str(row.get("path") or "").rstrip("\\/")).lower() == clean:
            return row
    return rows[0]


def explorer_folder(hwnd: int, title: str = "") -> Dict[str, Any]:
    """``{"path", "selected": [full paths], "reason"}`` of the Explorer window (its active tab)."""
    result = run_ps(EXPLORER_SCRIPT, {"MEMU_HWND": str(int(hwnd)), "MEMU_TITLE": str(title or "")[:300]}, timeout=6)
    if result.get("error"):
        return {"path": "", "selected": [], "reason": "com_failed"}
    value = result.get("value")
    rows = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
    tab = pick_explorer_tab(rows, title)
    if not tab:
        return {"path": "", "selected": [], "reason": "no_window"}
    path = str(tab.get("path") or "")
    selected = tab.get("selected") or []
    selected = [str(p) for p in (selected if isinstance(selected, list) else [selected]) if p]
    if not path or path.startswith("::") or not os.path.isdir(path):
        # This PC, Recycle Bin, Control Panel, a library, a search, a phone/FTP: not a folder on disk
        return {"path": "", "selected": [], "reason": "virtual", "location": str(tab.get("name") or "")}
    return {"path": path, "selected": [p for p in selected if os.path.exists(p)]}


def desktop_selection() -> List[str]:
    """Items selected on the desktop are not reachable through ``Shell.Application.Windows()`` — none."""
    return []


# ---------------------------------------------------------------- processes

def cmdline(pid: int) -> List[str]:
    if not pid:
        return []
    try:
        import psutil
        return [str(arg) for arg in psutil.Process(int(pid)).cmdline()][:40]
    except Exception:
        return []


def terminal_cwd(pid: int, exe: str, title: str = "") -> Dict[str, Any]:
    """The working folder of the shell in a terminal window. PowerShell does not keep the process folder in sync
    with ``Set-Location`` — then the title (``PS C:\\x>`` / ``C:\\x``) is preferred and the result is ``approx``."""
    import re
    out: Dict[str, Any] = {"path": "", "approx": False, "shell": ""}
    match = re.search(r"([A-Za-z]:\\[^<>:\"|?*\r\n]*)", str(title or ""))
    from_title = match.group(1).rstrip("> ").rstrip() if match else ""
    if from_title and os.path.isdir(from_title):
        out["path"] = from_title
    try:
        import psutil
        proc = psutil.Process(int(pid))
        candidates = [proc] + list(proc.children(recursive=True))
        shells = [p for p in candidates if (p.name() or "").lower() in
                  {"cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "wsl.exe", "zsh.exe", "fish.exe", "nu.exe"}]
        shells.sort(key=lambda p: p.create_time(), reverse=True)
        if shells:
            shell = shells[0]
            out["shell"] = shell.name()
            if not out["path"]:
                try:
                    cwd = shell.cwd()
                    if cwd and os.path.isdir(cwd):
                        out["path"] = cwd
                        out["approx"] = shell.name().lower() in ("powershell.exe", "pwsh.exe")
                except Exception:
                    pass
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- Office (read only)

OFFICE_SCRIPT = r"""
$kind = [string]$env:MEMU_KIND
$limit = [int]$env:MEMU_LIMIT
$o = $null
function Clip([string]$s) { if ($s.Length -gt $limit) { return $s.Substring(0, $limit) } return $s }
if ($kind -eq 'word') {
  $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
  $d = $app.ActiveDocument
  $sel = ''
  try { $sel = [string]$app.Selection.Text } catch { }
  $o = [pscustomobject]@{ name = [string]$d.Name; path = [string]$d.FullName; text = (Clip ([string]$d.Content.Text)); selection = (Clip $sel); pages = [int]$d.ComputeStatistics(2) }
} elseif ($kind -eq 'excel') {
  $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $app.ActiveWorkbook; $ws = $app.ActiveSheet; $ur = $ws.UsedRange
  $rows = [Math]::Min([int]$ur.Rows.Count, 400); $cols = [Math]::Min([int]$ur.Columns.Count, 40)
  $sb = New-Object System.Text.StringBuilder
  for ($r = 1; $r -le $rows; $r++) {
    $line = @()
    for ($c = 1; $c -le $cols; $c++) { $line += ([string]$ur.Cells.Item($r, $c).Text) -replace "`t", ' ' }
    [void]$sb.AppendLine(($line -join "`t"))
    if ($sb.Length -gt $limit) { break }
  }
  $sheets = @(); foreach ($s in $wb.Worksheets) { $sheets += [string]$s.Name }
  $o = [pscustomobject]@{ name = [string]$wb.Name; path = [string]$wb.FullName; sheet = [string]$ws.Name; sheets = $sheets; address = [string]$ur.Address(0,0); selection = [string]$app.Selection.Address(0,0); text = (Clip $sb.ToString()); rows = [int]$ur.Rows.Count; cols = [int]$ur.Columns.Count }
} elseif ($kind -eq 'powerpoint') {
  $app = [Runtime.InteropServices.Marshal]::GetActiveObject('PowerPoint.Application')
  $p = $app.ActivePresentation
  $sb = New-Object System.Text.StringBuilder
  foreach ($s in $p.Slides) {
    [void]$sb.AppendLine('--- ' + [string]$s.SlideIndex)
    foreach ($sh in $s.Shapes) { try { if ($sh.HasTextFrame) { [void]$sb.AppendLine([string]$sh.TextFrame.TextRange.Text) } } catch { } }
    if ($sb.Length -gt $limit) { break }
  }
  $o = [pscustomobject]@{ name = [string]$p.Name; path = [string]$p.FullName; text = (Clip $sb.ToString()); slides = [int]$p.Slides.Count }
}
ConvertTo-Json -InputObject $o -Depth 3 -Compress
"""


def office_document(kind: str, limit: int = 60000) -> Dict[str, Any]:
    """The open Word / Excel / PowerPoint document as text (read only)."""
    if kind not in ("word", "excel", "powerpoint"):
        return {"error": "unknown"}
    result = run_ps(OFFICE_SCRIPT, {"MEMU_KIND": kind, "MEMU_LIMIT": str(int(limit))}, timeout=20)
    if result.get("error"):
        return {"error": "com_failed", "detail": str(result["error"])[:200]}
    value = result.get("value")
    return value if isinstance(value, dict) else {"error": "empty"}


# ---------------------------------------------------------------- shell actions

def reveal(path: str) -> bool:
    """Show ``path`` selected in File Explorer."""
    if not IS_WINDOWS or not path:
        return False
    try:
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)], creationflags=CREATE_NO_WINDOW)
        return True
    except OSError:
        return False


def open_path(path: str) -> bool:
    if not IS_WINDOWS or not path:
        return False
    try:
        os.startfile(os.path.normpath(path))   # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def refresh_explorer(folder: str) -> None:
    """Ask Explorer to re-read a folder after our changes (SHChangeNotify SHCNE_UPDATEDIR)."""
    if not IS_WINDOWS or not folder:
        return
    try:
        import ctypes
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
        shell32.SHChangeNotify(0x00001000, 0x0005 | 0x1000, folder, None)   # SHCNE_UPDATEDIR, SHCNF_PATHW|FLUSHNOWAIT
    except Exception:
        pass


# ---------------------------------------------------------------- Windows OCR (no model)

OCR_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:MEMU_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = $null
if ($env:MEMU_LANG) { try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $env:MEMU_LANG)) } catch { } }
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($engine -eq $null) { ConvertTo-Json -InputObject @{ error = 'no_engine' } -Compress; exit }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @(); foreach ($l in $result.Lines) { $lines += [string]$l.Text }
$stream.Dispose()
ConvertTo-Json -InputObject @{ text = ($lines -join "`n"); lang = [string]$engine.RecognizerLanguage.LanguageTag; lines = $lines.Count } -Compress
"""


def windows_ocr(image_path: str, lang: str = "") -> Dict[str, Any]:
    if not IS_WINDOWS:
        return {"error": "unsupported"}
    result = run_ps(OCR_SCRIPT, {"MEMU_IMAGE": image_path, "MEMU_LANG": lang}, timeout=25)
    if result.get("error"):
        return {"error": "ocr_failed", "detail": str(result["error"])[:200]}
    value = result.get("value")
    return value if isinstance(value, dict) else {"error": "empty"}
