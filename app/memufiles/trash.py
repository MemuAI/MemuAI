"""Recycle Bin / Trash for MemuFiles — no third-party dependency.

* Windows: ``SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO)`` puts items into the
  real Recycle Bin (exactly what Explorer does). Listing / restoring / emptying
  goes through the ``Shell.Application`` namespace 10 (PowerShell, no window).
  NOT verified on real Windows in Round 35 — see the changelog.
* Linux / macOS (dev and tests): the freedesktop.org Trash spec
  (``$XDG_DATA_HOME/Trash/{files,info}``); ``MEMUFILES_TRASH_DIR`` overrides the
  location (tests).

:func:`send` returns a token per item that :func:`restore` accepts, so an undo
of "delete" puts the files back where they were.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("memufiles.trash")
IS_WIN = os.name == "nt"
_CREATE_NO_WINDOW = 0x08000000

class TrashError(Exception):
    pass

def _windows() -> bool:

    return IS_WIN and not os.environ.get("MEMUFILES_TRASH_DIR")

def trash_root() -> Path:
    env = os.environ.get("MEMUFILES_TRASH_DIR")
    if env:
        root = Path(env)
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        root = Path(base) / "Trash"
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "info").mkdir(parents=True, exist_ok=True)
    return root

def _posix_send(path: str) -> str:
    root = trash_root()
    name = os.path.basename(path.rstrip(os.sep)) or "item"
    candidate, index = name, 1
    while os.path.lexists(root / "files" / candidate) or (root / "info" / (candidate + ".trashinfo")).exists():
        index += 1
        base, ext = os.path.splitext(name)
        candidate = f"{base}.{index}{ext}"
    info = ("[Trash Info]\nPath=" + urllib.parse.quote(os.path.abspath(path)) + "\nDeletionDate="
            + datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    info_path = root / "info" / (candidate + ".trashinfo")
    with open(info_path, "x", encoding="utf-8") as handle:
        handle.write(info)
    try:
        shutil.move(path, str(root / "files" / candidate))
    except BaseException:
        info_path.unlink(missing_ok=True)
        raise
    return candidate

def _posix_items() -> List[Dict[str, Any]]:
    root = trash_root()
    rows = []
    for info in (root / "info").glob("*.trashinfo"):
        token = info.name[:-len(".trashinfo")]
        target = root / "files" / token
        original, deleted = "", ""
        try:
            for line in info.read_text(encoding="utf-8").splitlines():
                if line.startswith("Path="):
                    original = urllib.parse.unquote(line[5:])
                elif line.startswith("DeletionDate="):
                    deleted = line[13:]
        except OSError:
            continue
        try:
            st = os.lstat(target)
        except OSError:
            continue
        is_dir = os.path.isdir(target)
        size = st.st_size if not is_dir else 0
        try:
            ts = int(datetime.datetime.fromisoformat(deleted).timestamp() * 1000) if deleted else 0
        except ValueError:
            ts = 0
        rows.append({"token": token, "name": os.path.basename(original) or token, "original": original,
                     "deleted": ts, "size": size, "is_dir": is_dir, "path": str(target)})
    rows.sort(key=lambda r: -r["deleted"])
    return rows

def _posix_restore(token: str, target: Optional[str] = None) -> str:
    root = trash_root()
    if "/" in token or "\\" in token or ":" in token or token in ("", ".", ".."):
        raise TrashError("bad token")
    stored = root / "files" / token
    info = root / "info" / (token + ".trashinfo")
    if not os.path.lexists(stored):
        raise TrashError("not in trash")
    original = target
    if not original:
        for line in info.read_text(encoding="utf-8").splitlines():
            if line.startswith("Path="):
                original = urllib.parse.unquote(line[5:])
    if not original:
        raise TrashError("original path unknown")
    if os.path.lexists(original):
        from .fsops import unique_name
        original = os.path.join(os.path.dirname(original), unique_name(os.path.dirname(original),
                                                                         os.path.basename(original)))
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(str(stored), original)
    info.unlink(missing_ok=True)
    return original

def _posix_purge(token: str) -> None:
    root = trash_root()
    if "/" in token or "\\" in token or ":" in token or token in ("", ".", ".."):
        raise TrashError("bad token")
    stored = root / "files" / token
    if stored.is_dir() and not stored.is_symlink():
        shutil.rmtree(stored)
    elif os.path.lexists(stored):
        stored.unlink()
    (root / "info" / (token + ".trashinfo")).unlink(missing_ok=True)

def _win_send(paths: List[str]) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    FOF_WANTNUKEWARNING = 0x4000
    joined = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    op = SHFILEOPSTRUCTW(None, FO_DELETE, joined, None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING,
                         False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise TrashError(f"SHFileOperation error 0x{result:x}")
    if op.fAnyOperationsAborted:
        raise TrashError("aborted")

_PS_LIST = r"""
$ErrorActionPreference='SilentlyContinue'
$sh = New-Object -ComObject Shell.Application
$bin = $sh.NameSpace(10)
$out = @()
foreach ($i in $bin.Items()) {
  $from = $bin.GetDetailsOf($i, 1)
  $out += [pscustomobject]@{ name=$i.Name; path=$i.Path; from=$from; size=[int64]$i.Size;
    is_dir=[bool]$i.IsFolder; deleted=$bin.GetDetailsOf($i, 2) }
}
$out | ConvertTo-Json -Compress -Depth 3
"""

_PS_RESTORE = r"""
$ErrorActionPreference='Stop'
$sh = New-Object -ComObject Shell.Application
$bin = $sh.NameSpace(10)
$done = $false
foreach ($i in $bin.Items()) {
  if ($i.Path -eq $env:MF_TOKEN) {
    foreach ($v in $i.Verbs()) { if ($v.Name -match 'undelete|R&estore|Восстановить|Восст') { $v.DoIt(); $done = $true; break } }
    if (-not $done) { $i.InvokeVerb('undelete'); $done = $true }
    break
  }
}
if ($done) { 'ok' } else { 'missing' }
"""

def _powershell(script: str, env_extra: Optional[Dict[str, str]] = None, timeout: float = 60) -> str:
    env = dict(os.environ)
    env.update(env_extra or {})
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",   # noqa: S603,S607
                             "-Command", script], capture_output=True, timeout=timeout, env=env,
                            creationflags=_CREATE_NO_WINDOW)
    return result.stdout.decode("utf-8", "replace").strip()

def _win_items() -> List[Dict[str, Any]]:
    raw = _powershell("[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + _PS_LIST)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        original = os.path.join(str(item.get("from") or ""), str(item.get("name") or ""))
        rows.append({"token": str(item.get("path") or ""), "name": str(item.get("name") or ""), "original": original,
                     "deleted": 0, "deleted_text": str(item.get("deleted") or ""), "size": int(item.get("size") or 0),
                     "is_dir": bool(item.get("is_dir")), "path": str(item.get("path") or "")})
    return rows

def _win_find_token(original: str, after: float) -> str:
    """The Recycle Bin item ($R…) that came from *original* — newest one."""
    wanted = os.path.normcase(os.path.abspath(original))
    for row in _win_items():
        if os.path.normcase(row["original"]) == wanted:
            return row["token"]
    return ""

def send(paths: List[str]) -> List[Dict[str, str]]:
    """Move *paths* to the Recycle Bin / Trash. ``[{"path", "token"}]`` (token may be "" on Windows)."""
    out = []
    if _windows():
        _win_send(paths)
        for path in paths:
            out.append({"path": path, "token": ""})
        return out
    for path in paths:
        out.append({"path": path, "token": _posix_send(path)})
    return out

def items() -> List[Dict[str, Any]]:
    try:
        return _win_items() if _windows() else _posix_items()
    except Exception as exc:  # noqa: BLE001
        log.warning("trash listing failed: %s", exc)
        return []

def restore(token: str) -> str:
    if _windows():
        reply = _powershell(_PS_RESTORE, {"MF_TOKEN": token})
        if reply != "ok":
            raise TrashError("restore failed")
        return token
    return _posix_restore(token)

def restore_original(original: str, token: str = "") -> str:
    """Undo of a delete: put *original* back (by token on posix, by its old path on Windows)."""
    if _windows():
        found = token or _win_find_token(original, 0)
        if not found:
            raise TrashError("not found in the Recycle Bin")
        restore(found)
        return original
    return _posix_restore(token)

def purge(tokens: List[str]) -> int:
    count = 0
    for token in tokens:
        try:
            if _windows():
                path = token
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.lexists(path):
                    os.unlink(path)
            else:
                _posix_purge(token)
            count += 1
        except OSError as exc:
            log.warning("purge %s failed: %s", token, exc)
    return count

def empty() -> int:
    if _windows():
        import ctypes

        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
        return 0 if result in (0, -2147418113) else -1
    rows = _posix_items()
    return purge([r["token"] for r in rows])

def stamp() -> float:
    return time.time()
