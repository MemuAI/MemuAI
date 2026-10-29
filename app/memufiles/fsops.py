"""File-system primitives of MemuFiles: listing, drives, places, properties, safety rules, shell glue.

Cross-platform on purpose (developed and tested on Linux, shipped for Windows):
every Windows API is behind ``os.name == "nt"`` and ``ctypes`` imports inside
functions. Nothing here imports Pillow, the AI client or the index.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import shutil
import stat as statmod
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("memufiles.fs")

IS_WIN = os.name == "nt"
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_READONLY = 0x1
_CREATE_NO_WINDOW = 0x08000000

class FsError(Exception):
    """An expected failure with a stable code the page translates (``exists``, ``protected``, …)."""

    def __init__(self, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.extra = extra

def clean_path(value: Any) -> str:
    text = str(value or "").strip().strip('"')
    if not text or "\x00" in text or len(text) > 4096:
        raise FsError("bad_path", "bad path")
    text = os.path.expandvars(os.path.expanduser(text))
    if IS_WIN and len(text) == 2 and text[1] == ":":
        text += "\\"
    if not os.path.isabs(text):
        raise FsError("bad_path", "path must be absolute")
    return os.path.normpath(text)

def lnk_target(path: str) -> str:
    """Round 39: the target of a Windows shortcut (``.lnk``) from its LinkInfo (MS-SHLLINK §2.3) — local paths
    (Unicode when present, else the ANSI code page) and UNC shares; "" when it has none or the file is not a
    shortcut. Used to open a folder shortcut *in place*, as Explorer does, instead of launching it."""
    import struct
    try:
        with open(path, "rb") as handle:
            data = handle.read(1 << 16)
    except OSError:
        return ""
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return ""
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 0x4C
    if flags & 0x1:
        if pos + 2 > len(data):
            return ""
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if not flags & 0x2 or pos + 28 > len(data):
        return ""
    size, header, info_flags, _vol, local_off, net_off, suffix_off = struct.unpack_from("<7I", data, pos)
    if size < 28 or header < 28 or header > size or pos + size > len(data):
        return ""
    info = data[pos:pos + size]

    def ansi(off: int) -> str:
        raw = info[off:info.index(b"\0", off)] if off and b"\0" in info[off:] else b""
        return raw.decode("mbcs" if IS_WIN else "cp1252", "replace")

    def wide(off: int) -> str:
        end = off
        while end + 1 < len(info) and info[end:end + 2] != b"\0\0":
            end += 2
        return info[off:end].decode("utf-16-le", "replace")
    try:
        suffix = ansi(suffix_off)
        if header >= 0x24 and len(info) >= 36:
            local_u, suffix_u = struct.unpack_from("<2I", info, 28)
            if suffix_u:
                suffix = wide(suffix_u)
            if info_flags & 0x1 and local_u:
                return wide(local_u) + suffix
        if info_flags & 0x1 and local_off:
            return ansi(local_off) + suffix
        if info_flags & 0x2 and net_off:
            net_name_off = struct.unpack_from("<I", info, net_off + 8)[0]
            share = ansi(net_off + net_name_off)
            return share + ("\\" + suffix if suffix and not share.endswith("\\") else suffix)
    except (ValueError, struct.error):
        return ""
    return ""

STAMP_SCAN_LIMIT = 3000

def dir_stamp(path: str) -> str:
    """Round 39: «did this folder change?» for the auto-refresh poll (``/api/ls`` head and ``/api/dirstamp``).

    The folder's mtime alone missed two cases: a file whose content/size changed (the folder's mtime stays) and
    FAT/exFAT USB sticks, where Windows never updates a folder's mtime. Folders up to ``STAMP_SCAN_LIMIT``
    entries also get a cheap signature of names/sizes/mtimes (``scandir`` returns them without extra calls on
    Windows); bigger folders keep the mtime only, so the 2.5 s poll stays cheap."""
    st = os.stat(path)
    sig = 0
    try:
        with os.scandir(path) as entries:
            for n, entry in enumerate(entries):
                if n >= STAMP_SCAN_LIMIT:
                    sig = -1
                    break
                try:
                    info = entry.stat(follow_symlinks=False)
                    sig ^= hash((entry.name, info.st_size, info.st_mtime_ns))
                except OSError:
                    sig ^= hash(entry.name)
    except OSError:
        sig = -2
    return f"{st.st_mtime_ns}:{sig & 0xFFFFFFFFFFFF if sig >= 0 else sig}"

def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))

def is_inside(path: str, root: str) -> bool:
    """``path`` is ``root`` or below it (case-insensitive on Windows)."""
    p, r = norm(path), norm(root)
    if p == r:
        return True
    r = r if r.endswith(os.sep) else r + os.sep
    return p.startswith(r)

def split_name(name: str):
    """``("archive.tar", ".gz")`` style split that keeps dot-files whole (``.bashrc`` has no extension)."""
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    base, ext = os.path.splitext(name)
    return base, ext

INVALID_CHARS = set('<>:"/\\|?*') if IS_WIN else {"/"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

def valid_name(name: str) -> Optional[str]:
    """None when *name* is a valid file name on this OS, else an error code."""
    if not name or name in (".", ".."):
        return "empty_name"
    if len(name) > 255:
        return "name_too_long"
    if any(ch in INVALID_CHARS or ord(ch) < 32 for ch in name):
        return "invalid_chars"
    if IS_WIN:
        if name.rstrip(" .") != name:
            return "trailing_dot"
        if name.split(".")[0].upper() in RESERVED:
            return "reserved_name"
    return None

def unique_name(folder: str, name: str) -> str:
    """``name`` or ``name (2).ext`` … the first one that does not exist in *folder* (Explorer style)."""
    if not os.path.lexists(os.path.join(folder, name)):
        return name
    base, ext = split_name(name)
    import re
    match = re.match(r"^(.*) \((\d+)\)$", base)
    if match:
        base, start = match.group(1), int(match.group(2)) + 1
    else:
        start = 2
    for index in range(start, start + 100000):
        candidate = f"{base} ({index}){ext}"
        if not os.path.lexists(os.path.join(folder, candidate)):
            return candidate
    raise FsError("exists", name)

def _win_dirs() -> List[str]:
    env = os.environ
    dirs = [env.get("SystemRoot") or r"C:\Windows", env.get("ProgramFiles") or r"C:\Program Files",
            env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)", env.get("ProgramData") or r"C:\ProgramData",
            env.get("ProgramW6432") or "", r"C:\System Volume Information", r"C:\$Recycle.Bin", r"C:\Recovery",
            r"C:\Boot", r"C:\Users\Default", r"C:\Users\Public"]
    return [d for d in dirs if d]

POSIX_SYSTEM = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/proc",
                "/sbin", "/snap", "/srv", "/sys", "/usr", "/var", "/opt", "/System", "/Library", "/Applications"]

def protected_reason(path: str) -> Optional[str]:
    """Why *path* must never be deleted / moved by MemuFiles (or the AI): ``root``, ``system``, ``home``…

    Covers drive roots, the OS folders (and everything inside them), the user's
    profile folder itself and its top-level special folders (they can be emptied,
    not removed), and the app's own data folder.
    """
    try:
        p = os.path.normpath(os.path.abspath(path))
    except (TypeError, ValueError):
        return "bad_path"
    drive, rest = os.path.splitdrive(p)
    if rest in ("", os.sep, "/") or p == os.path.dirname(p):
        return "root"
    for system in (_win_dirs() if IS_WIN else POSIX_SYSTEM):
        if is_inside(p, system):
            return "system"
    home = os.path.expanduser("~")
    if norm(p) == norm(home):
        return "home"
    if IS_WIN:
        users = os.path.dirname(home)
        if norm(p) == norm(users):
            return "system"
    parent = os.path.dirname(p)
    if norm(parent) == norm(home) and os.path.basename(p) in SPECIAL_NAMES:
        return "special"
    if IS_WIN and is_inside(p, os.path.join(home, "AppData")) and norm(os.path.dirname(p)) in (
            norm(os.path.join(home, "AppData")), norm(home)):
        return "system"
    try:
        from .paths import data_root
        if norm(p) == norm(str(data_root())):
            return "app_data"
    except Exception:  # noqa: BLE001
        pass
    return None

SPECIAL_NAMES = {"Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "AppData", "OneDrive",
                 "Рабочий стол", "Документы", "Загрузки", "Изображения", "Музыка", "Видео"}

def ensure_mutable(path: str) -> None:
    reason = protected_reason(path)
    if reason:
        raise FsError("protected", path, reason=reason, path=path)

def _attrs(entry_stat) -> int:
    return int(getattr(entry_stat, "st_file_attributes", 0) or 0)

def entry_row(name: str, full: str, st: Optional[os.stat_result], is_dir: bool, is_link: bool) -> Dict[str, Any]:
    """The compact row the page renders: n(ame) d(ir) s(ize) m(time ms) c(reated) h(idden) l(ink) r(eadonly)."""
    attrs = _attrs(st) if st else 0
    hidden = bool(attrs & FILE_ATTRIBUTE_HIDDEN) if IS_WIN else name.startswith(".")
    row: Dict[str, Any] = {"n": name, "d": 1 if is_dir else 0}
    if st is not None:
        row["s"] = 0 if is_dir else int(st.st_size)
        row["m"] = int(st.st_mtime * 1000)
        created = getattr(st, "st_birthtime", None) or (st.st_ctime if IS_WIN else None)
        if created:
            row["c"] = int(created * 1000)
        if IS_WIN:
            readonly = bool(attrs & FILE_ATTRIBUTE_READONLY)
        else:
            readonly = not (st.st_mode & statmod.S_IWUSR)
        if readonly:
            row["r"] = 1
        if IS_WIN and attrs & FILE_ATTRIBUTE_SYSTEM:
            row["sys"] = 1
    else:
        row["s"], row["m"] = 0, 0
        row["e"] = 1
    if hidden:
        row["h"] = 1
    if is_link:
        row["l"] = 1
    return row

def iter_dir(path: str, chunk: int = 2000) -> Iterator[List[Dict[str, Any]]]:
    """Stream a folder's rows in chunks (``os.scandir`` — one syscall per entry on Windows)."""
    batch: List[Dict[str, Any]] = []
    with os.scandir(path) as handle:
        for entry in handle:
            try:
                is_link = entry.is_symlink()
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False
                try:
                    st = entry.stat(follow_symlinks=True)
                except OSError:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        st = None
                batch.append(entry_row(entry.name, entry.path, st, is_dir, is_link))
            except Exception as exc:  # noqa: BLE001 - one odd entry must not end the listing
                log.debug("skip %s: %s", entry.name, exc)
                continue
            if len(batch) >= chunk:
                yield batch
                batch = []
    if batch:
        yield batch

def list_dir(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for batch in iter_dir(path):
        rows.extend(batch)
    return rows

def complete(prefix: str, limit: int = 30, show_hidden: bool = False) -> List[str]:
    """Address-bar autocomplete: sub-folders of the typed parent that start with the typed tail."""
    text = os.path.expandvars(os.path.expanduser(str(prefix or "")))
    if not text:
        return []
    if text.endswith(("/", "\\")):
        parent, tail = text, ""
    else:
        parent, tail = os.path.dirname(text), os.path.basename(text)
    if IS_WIN and len(parent) == 2 and parent[1] == ":":
        parent += "\\"
    if not parent or not os.path.isdir(parent):
        return []
    out: List[str] = []
    low = tail.lower()
    try:
        with os.scandir(parent) as handle:
            for entry in handle:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if not show_hidden and entry.name.startswith(".") and not low.startswith("."):
                    continue
                if entry.name.lower().startswith(low):
                    out.append(os.path.join(parent, entry.name))
    except OSError:
        return []
    out.sort(key=lambda s: s.lower())
    return out[:limit]

DRIVE_TYPES = {0: "unknown", 1: "none", 2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ram"}

def drives() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if IS_WIN:
        import ctypes
        kernel = ctypes.windll.kernel32
        mask = kernel.GetLogicalDrives()
        old_mode = kernel.SetErrorMode(1)
        try:
            for index in range(26):
                if not mask & (1 << index):
                    continue
                root = f"{chr(65 + index)}:\\"
                kind = DRIVE_TYPES.get(kernel.GetDriveTypeW(root), "unknown")
                label_buf = ctypes.create_unicode_buffer(261)
                fs_buf = ctypes.create_unicode_buffer(261)
                ok = kernel.GetVolumeInformationW(root, label_buf, 261, None, None, None, fs_buf, 261)
                total = free = 0
                if ok:
                    free_c, total_c = ctypes.c_ulonglong(), ctypes.c_ulonglong()
                    if kernel.GetDiskFreeSpaceExW(root, None, ctypes.byref(total_c), ctypes.byref(free_c)):
                        total, free = total_c.value, free_c.value
                rows.append({"path": root, "label": label_buf.value if ok else "", "fs": fs_buf.value if ok else "",
                             "kind": kind, "total": total, "free": free, "ready": bool(ok)})
        finally:
            kernel.SetErrorMode(old_mode)
        return rows
    seen = set()
    candidates = ["/"]
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
                if fstype in ("proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "overlay",
                              "securityfs", "pstore", "bpf", "tracefs", "debugfs", "mqueue", "hugetlbfs", "fusectl",
                              "configfs", "autofs", "squashfs", "nsfs", "ramfs", "rpc_pipefs", "binfmt_misc"):
                    continue
                if not device.startswith("/") and fstype not in ("9p", "virtiofs", "nfs", "nfs4", "cifs", "fuse.sshfs"):
                    continue
                if mount.startswith(("/proc", "/sys", "/dev", "/run", "/snap", "/boot/efi")):
                    continue
                candidates.append(mount)
    except OSError:
        pass
    for mount in candidates:
        if mount in seen:
            continue
        seen.add(mount)
        try:
            usage = shutil.disk_usage(mount)
            total, free = usage.total, usage.free
        except OSError:
            continue
        label = "/" if mount == "/" else os.path.basename(mount.rstrip("/")) or mount
        rows.append({"path": mount, "label": label, "fs": "", "kind": "fixed", "total": total, "free": free,
                     "ready": True})
        if len(rows) >= 12:
            break
    return rows

_KNOWN_FOLDERS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

def _known_folder(guid: str) -> str:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]
    import uuid
    u = uuid.UUID(guid)
    g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        return ""
    value = out.value or ""
    ctypes.windll.ole32.CoTaskMemFree(out)
    return value

def _xdg_dirs() -> Dict[str, str]:
    home = os.path.expanduser("~")
    result = {}
    config = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")) / "user-dirs.dirs"
    mapping = {"XDG_DESKTOP_DIR": "desktop", "XDG_DOCUMENTS_DIR": "documents", "XDG_DOWNLOAD_DIR": "downloads",
               "XDG_PICTURES_DIR": "pictures", "XDG_MUSIC_DIR": "music", "XDG_VIDEOS_DIR": "videos"}
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in mapping:
                    result[mapping[key.strip()]] = value.strip().strip('"').replace("$HOME", home)
    except OSError:
        pass
    return result

def places() -> List[Dict[str, Any]]:
    """Libraries: Desktop, Documents, Downloads, Pictures, Music, Videos (only the ones that exist) + home."""
    home = os.path.expanduser("~")
    rows = [{"id": "home", "path": home}]
    xdg = {} if IS_WIN else _xdg_dirs()
    defaults = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
                "music": "Music", "videos": "Videos"}
    for key, name in defaults.items():
        path = ""
        if IS_WIN:
            try:
                path = _known_folder(_KNOWN_FOLDERS[key])
            except Exception:  # noqa: BLE001
                path = ""
        path = path or xdg.get(key) or os.path.join(home, name)
        rows.append({"id": key, "path": path, "exists": os.path.isdir(path)})
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
    rows.append({"id": "onedrive", "path": onedrive, "exists": bool(onedrive and os.path.isdir(onedrive))})
    return rows

def _fmt_time(ts: Optional[float]) -> Optional[int]:
    return int(ts * 1000) if ts else None

def properties(path: str) -> Dict[str, Any]:
    st = os.stat(path, follow_symlinks=False)
    is_link = statmod.S_ISLNK(st.st_mode)
    try:
        full = os.stat(path)
    except OSError:
        full = st
    is_dir = statmod.S_ISDIR(full.st_mode)
    attrs = _attrs(full)
    name = os.path.basename(path) or path
    info: Dict[str, Any] = {
        "path": path, "name": name, "parent": os.path.dirname(path), "is_dir": is_dir, "is_link": is_link,
        "size": 0 if is_dir else full.st_size, "modified": _fmt_time(full.st_mtime), "accessed": _fmt_time(full.st_atime),
        "created": _fmt_time(getattr(full, "st_birthtime", None) or (full.st_ctime if IS_WIN else None)),
        "changed": None if IS_WIN else _fmt_time(full.st_ctime),
        "hidden": bool(attrs & FILE_ATTRIBUTE_HIDDEN) if IS_WIN else name.startswith("."),
        "readonly": bool(attrs & FILE_ATTRIBUTE_READONLY) if IS_WIN else not os.access(path, os.W_OK),
        "system": bool(attrs & FILE_ATTRIBUTE_SYSTEM),
        "mode": statmod.filemode(full.st_mode), "protected": protected_reason(path) or "",
        "ext": split_name(name)[1].lower(),
    }
    if is_link:
        try:
            info["target"] = os.readlink(path)
        except OSError:
            pass
    if not IS_WIN:
        try:
            import pwd
            import grp
            info["owner"] = pwd.getpwuid(full.st_uid).pw_name
            info["group"] = grp.getgrgid(full.st_gid).gr_name
        except (ImportError, KeyError):
            pass
    try:
        usage = shutil.disk_usage(path if is_dir else os.path.dirname(path))
        info["disk"] = {"total": usage.total, "free": usage.free}
    except OSError:
        pass
    if not is_dir and info["ext"] in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico"):
        try:
            from PIL import Image  # type: ignore
            with Image.open(path) as image:
                info["image"] = {"width": image.width, "height": image.height, "mode": image.mode,
                                 "format": image.format}
        except Exception:  # noqa: BLE001
            pass
    return info

def set_attributes(path: str, hidden: Optional[bool] = None, readonly: Optional[bool] = None) -> Dict[str, Any]:
    if readonly is not None:
        st = os.stat(path)
        if IS_WIN:
            mode = st.st_mode & ~statmod.S_IWRITE if readonly else st.st_mode | statmod.S_IWRITE
        else:
            mode = st.st_mode & ~(statmod.S_IWUSR | statmod.S_IWGRP | statmod.S_IWOTH) if readonly \
                else st.st_mode | statmod.S_IWUSR
        os.chmod(path, mode)
    if hidden is not None:
        if IS_WIN:
            import ctypes
            attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
            if attrs == -1 or attrs == 0xFFFFFFFF:
                raise FsError("attributes_failed", path)
            attrs = attrs | FILE_ATTRIBUTE_HIDDEN if hidden else attrs & ~FILE_ATTRIBUTE_HIDDEN
            if not ctypes.windll.kernel32.SetFileAttributesW(path, attrs):
                raise FsError("attributes_failed", path)
        else:
            name = os.path.basename(path)
            if hidden and not name.startswith("."):
                raise FsError("hidden_posix", "on Linux/macOS a hidden file starts with a dot — rename it")
            if not hidden and name.startswith("."):
                raise FsError("hidden_posix", "rename the file without the leading dot")
    return properties(path)

def file_hash(path: str, algo: str = "sha256", cancel=None) -> str:
    if algo not in ("md5", "sha1", "sha256"):
        raise FsError("bad_algo", algo)
    digest = hashlib.new(algo)
    with open(path, "rb") as handle:
        while True:
            if cancel is not None and cancel.is_set():
                raise FsError("cancelled")
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

def make_folder(parent: str, name: str) -> str:
    error = valid_name(name)
    if error:
        raise FsError(error, name)
    target = os.path.join(parent, name)
    if os.path.lexists(target):
        raise FsError("exists", target, path=target)
    os.mkdir(target)
    return target

def make_file(parent: str, name: str, content: bytes = b"") -> str:
    error = valid_name(name)
    if error:
        raise FsError(error, name)
    target = os.path.join(parent, name)
    with open(target, "xb") as handle:
        handle.write(content)
    return target

def rename(path: str, new_name: str) -> str:
    error = valid_name(new_name)
    if error:
        raise FsError(error, new_name)
    ensure_mutable(path)
    parent = os.path.dirname(path)
    target = os.path.join(parent, new_name)
    if norm(target) != norm(path) and os.path.lexists(target):
        raise FsError("exists", target, path=target)
    if norm(target) == norm(path) and target != path:
        temp = os.path.join(parent, f".~memufiles-{os.getpid()}-{time.time_ns()}")
        os.rename(path, temp)
        os.rename(temp, target)
    else:
        os.rename(path, target)
    try:
        from . import settings
        settings.tags_moved(path, target)
    except Exception:  # noqa: BLE001
        pass
    return target

def _popen(command: List[str], cwd: Optional[str] = None) -> None:
    flags = 0x00000008 | 0x00000200 if IS_WIN else 0
    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,   # noqa: S603
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags, start_new_session=not IS_WIN)

def open_default(path: str) -> None:
    if IS_WIN:
        os.startfile(path)            # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        _popen(["open", path])
    else:
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise FsError("no_opener", "xdg-open is not installed")
        _popen([opener, "open", path] if opener.endswith("gio") else [opener, path])

def open_with_dialog(path: str, hwnd: int = 0) -> str:
    """The Windows «Open with» chooser (``openwith.chooser``: SHOpenWithDialog on its own STA thread)."""
    if not IS_WIN:
        raise FsError("unsupported", "Open with… needs Windows")
    from . import openwith
    try:
        return openwith.chooser(path, hwnd)
    except openwith.OpenWithError as exc:
        raise FsError(exc.code, str(exc)) from None

def shell_verb(path: str, verb: str, hwnd: int = 0) -> None:
    """ShellExecuteEx with a verb (``properties``, ``runas``, ``print``…) — the real Windows dialogs, run on the
    shell STA thread (COM initialised, SEE_MASK_NOASYNC) so the verb is not cut off by a finishing request."""
    if not IS_WIN:
        raise FsError("unsupported", "needs Windows")
    from . import openwith
    try:
        openwith.shell_verb(path, verb, hwnd)
    except openwith.OpenWithError as exc:
        raise FsError(exc.code, str(exc)) from None

def open_terminal(folder: str, preference: str = "auto") -> str:
    if IS_WIN:
        choices = []
        if preference in ("auto", "wt") and shutil.which("wt.exe"):
            choices.append(["wt.exe", "-d", folder])
        if preference in ("auto", "powershell"):
            pwsh = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
            if pwsh:
                choices.append(["cmd.exe", "/c", "start", "", pwsh, "-NoExit", "-Command",
                                f"Set-Location -LiteralPath '{folder.replace(chr(39), chr(39) * 2)}'"])
        choices.append(["cmd.exe", "/c", "start", "", "cmd.exe", "/K", f'cd /d "{folder}"'])
        for command in choices:
            try:
                subprocess.Popen(command, cwd=folder, creationflags=_CREATE_NO_WINDOW)   # noqa: S603
                return command[0]
            except OSError:
                continue
        raise FsError("no_terminal")
    if sys.platform == "darwin":
        _popen(["open", "-a", "Terminal", folder])
        return "Terminal"
    for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "xterm"):
        exe = shutil.which(term)
        if exe:
            _popen([exe], cwd=folder)
            return term
    raise FsError("no_terminal")

def reveal_native(path: str) -> None:
    """Show the item in the system file manager (Explorer) — "open in Explorer" for anything we lack."""
    if IS_WIN:
        subprocess.Popen(["explorer.exe", "/select,", path])   # noqa: S603,S607
    else:
        open_default(os.path.dirname(path) if os.path.isfile(path) else path)

def now_ms() -> int:
    return int(time.time() * 1000)

def iso(ms: Optional[int]) -> str:
    if not ms:
        return ""
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
