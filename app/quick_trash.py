"""Round 30: deleting to the Recycle Bin (Windows) / the Trash (freedesktop) — with «Undo» from MemuAI.

:func:`send` moves a file or a folder to the system bin and returns a small record that :func:`restore` uses to
put it back (the «Undo» of a Quick Access change set). ``None`` means the bin is not available for this path
(another volume on Linux, no shell on macOS, a failure): the caller falls back to its own undo stash.

* **Windows** — ``SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI |
  FOF_WANTNUKEWARNING)``: the official Recycle Bin, no MemuAI size cap. ``FOF_WANTNUKEWARNING`` overrides
  ``FOF_NOCONFIRMATION`` exactly when the item would be destroyed instead of recycled (too big for the bin, a drive
  without one) — Windows then asks the user, so nothing is ever deleted for good silently. The record names the
  ``$R…`` item: after the call ``<drive>\\$Recycle.Bin\\<SID>\\$I…`` files written since the call are parsed
  (:func:`parse_index` — version 1/2 of the ``$I`` format) and the one whose original path is ours is taken.
  Not found → still in the bin (restorable from Explorer), only MemuAI's «Undo» is unavailable for it.
* **Linux** — the freedesktop.org Trash spec (``$XDG_DATA_HOME/Trash/{files,info}``, home trash only, same device):
  ``<name>.trashinfo`` with ``Path`` / ``DeletionDate``, so file managers show and restore it as well.
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

log = logging.getLogger("memuai.quick.trash")

IS_WINDOWS = sys.platform == "win32"


def available() -> bool:
    return IS_WINDOWS or sys.platform.startswith("linux")


# ---------------------------------------------------------------- Windows

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


def parse_index(data: bytes) -> Optional[Dict[str, Any]]:
    """An ``$I…`` file of the Recycle Bin → ``{"path", "size", "deleted"}`` (``None`` when it is not one)."""
    try:
        version, size, filetime = struct.unpack_from("<qqq", data, 0)
        if version == 1:                               # Vista … 8.1: a fixed 260-character path
            raw = data[24:24 + 520]
        elif version == 2:                             # Windows 10+: length-prefixed path
            (chars,) = struct.unpack_from("<i", data, 24)
            if not 0 < chars <= 32768:
                return None
            raw = data[28:28 + chars * 2]
        else:
            return None
        path = raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
    except (struct.error, ValueError):
        return None
    if not path:
        return None
    deleted = (filetime - 116444736000000000) / 10_000_000 if filetime > 0 else 0.0
    return {"path": path, "size": int(size), "deleted": deleted}


def _bin_dirs(path: str) -> List[Path]:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    base = Path((drive or "C:") + "\\") / "$Recycle.Bin"
    out = []
    try:
        for entry in os.scandir(base):
            if entry.is_dir(follow_symlinks=False) and entry.name.upper().startswith("S-1-"):
                out.append(Path(entry.path))
    except OSError:
        pass
    return out


def find_recycled(path: str, since: float, dirs: Optional[List[Path]] = None) -> Optional[Dict[str, Any]]:
    """The ``$I`` / ``$R`` pair the bin wrote for ``path`` after ``since`` (newest first)."""
    want = os.path.normcase(os.path.abspath(path))
    best = None
    for folder in dirs if dirs is not None else _bin_dirs(path):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if not entry.name.startswith("$I"):
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < since - 2:
                    continue
                with open(entry.path, "rb") as handle:
                    info = parse_index(handle.read(24 + 4 + 65536 * 2))
            except OSError:
                continue
            if not info or os.path.normcase(os.path.abspath(info["path"])) != want:
                continue
            item = Path(folder) / ("$R" + entry.name[2:])
            if os.path.lexists(item) and (best is None or mtime > best["t"]):
                best = {"kind": "recycle", "item": str(item), "index": entry.path, "t": mtime}
    return best


def _shell_delete(path: str) -> bool:
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\x00"          # double-NUL terminated list (ctypes adds the second NUL)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    result = shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted and not os.path.lexists(path)


# ---------------------------------------------------------------- freedesktop

def _xdg_trash() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "Trash"


def _xdg_send(path: Path) -> Optional[Dict[str, Any]]:
    trash = _xdg_trash()
    files, info = trash / "files", trash / "info"
    try:
        files.mkdir(parents=True, exist_ok=True)
        info.mkdir(parents=True, exist_ok=True)
        os.chmod(trash, 0o700)
    except OSError:
        return None
    stem = path.name
    for attempt in range(1000):
        name = stem if attempt == 0 else f"{stem}.{attempt}"
        info_path = info / (name + ".trashinfo")
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (
                quote(str(path.absolute())), datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
        target = files / name
        try:
            os.rename(path, target)        # EXDEV on another volume: its own $topdir/.Trash is not handled here
        except OSError:
            info_path.unlink(missing_ok=True)
            return None
        return {"kind": "xdg", "item": str(target), "index": str(info_path)}
    return None


def original_path(record: Dict[str, Any]) -> str:
    """Where an item of the bin came from (read back from its index; "" when unknown)."""
    try:
        if record.get("kind") == "recycle":
            info = parse_index(Path(record["index"]).read_bytes())
            return info["path"] if info else ""
        if record.get("kind") == "xdg":
            for line in Path(record["index"]).read_text(encoding="utf-8").splitlines():
                if line.startswith("Path="):
                    return unquote(line[5:])
    except (OSError, KeyError, TypeError):
        pass
    return ""


# ---------------------------------------------------------------- the API

def send(path: Any) -> Optional[Dict[str, Any]]:
    """Move ``path`` to the bin → a record for :func:`restore` (``{"kind": "recycle"|"xdg"|"bin"}``) or ``None``.

    ``{"kind": "bin"}`` (Windows) — recycled, but the item was not found afterwards: restorable from the bin in
    Explorer, not by MemuAI's «Undo»."""
    target = Path(path)
    if not os.path.lexists(target):
        return None
    try:
        if IS_WINDOWS:
            since = time.time()
            if not _shell_delete(str(target)):
                return None
            return find_recycled(str(target), since) or {"kind": "bin"}
        if sys.platform.startswith("linux"):
            return _xdg_send(target)
    except Exception as exc:                            # never lose a file over the bin: the caller stashes it
        log.warning("recycle bin failed for %s: %s", target, exc)
    return None


def restore(record: Dict[str, Any], dest: Any) -> None:
    """Put an item back from the bin to ``dest`` (raises ``OSError`` when it is no longer there)."""
    if not isinstance(record, dict) or record.get("kind") not in ("recycle", "xdg"):
        raise OSError("not restorable from here")
    item = Path(str(record.get("item") or ""))
    if not os.path.lexists(item):
        raise FileNotFoundError("the item is no longer in the bin")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(item, dest)
    try:
        Path(str(record.get("index") or "")).unlink()
    except OSError:
        pass
