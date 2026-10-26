"""MemuFiles · archives opened like folders (round 41, r41-files).

A path that goes THROUGH an archive file — ``D:\\Downloads\\pack.zip\\docs\\a.txt`` — is browsed like a folder:

* :func:`split` finds the archive part of such a path (the longest existing file with an archive extension);
* :func:`listing` gives the rows of one folder inside (``fsops.entry_row`` shape: n d s m + ``a`` = in an archive),
  built from a cached index (archive mtime+size) — zip/jar via :mod:`zipfile`, tar/tgz/tbz/txz via :mod:`tarfile`,
  a single ``.gz/.bz2/.xz`` file as one member, everything else (7z, rar, cab, iso…) through 7-Zip when installed;
* :func:`materialize` writes one member to a cache file (preview, thumbnails, «open», Quick Look, drag out);
* operations (``opqueue``): :func:`extract` — chosen members / everything, with the browsed folder stripped
  (``args.members``, ``args.strip``, ``args.password``); :func:`compress` — zip / 7z / tar / tar.gz / tar.xz /
  tar.bz2 with a level and an optional password (7z / zip through 7-Zip); :func:`add` — files into an existing archive.

Read-only inside an archive except «add» (zip natively, others via 7-Zip). Stdlib only (7-Zip is optional).
"""
from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from . import fsops
from .fsops import FsError

COMPOUND = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")
ZIP_EXT = (".zip", ".jar", ".apk", ".xpi", ".nupkg", ".whl", ".cbz", ".epub")
TAR_EXT = (".tar", ".tgz", ".tbz", ".tbz2", ".txz") + COMPOUND[:3]
SINGLE_EXT = (".gz", ".bz2", ".xz")
SEVEN_EXT = (".7z", ".rar", ".cab", ".iso", ".arj", ".lzh", ".wim", ".cbr", ".cb7", ".zst", ".tar.zst", ".z", ".lz", ".lzma")
ALL_EXT = tuple(sorted(set(ZIP_EXT + TAR_EXT + SINGLE_EXT + SEVEN_EXT), key=len, reverse=True))

ASSOC_EXT = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".txz", ".tbz2", ".zst", ".cab", ".lzh", ".arj")

_CACHE: Dict[str, Tuple[Tuple[float, int], Dict[str, Any]]] = {}
_LOCK = threading.Lock()
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

def is_archive_name(name: str) -> bool:
    return str(name or "").lower().endswith(ALL_EXT)

def kind_of(path: str) -> str:
    low = path.lower()
    if low.endswith(ZIP_EXT):
        return "zip"
    if low.endswith(TAR_EXT):
        return "tar"
    if low.endswith(COMPOUND[3]):
        return "7z"
    if low.endswith(SINGLE_EXT):
        return "single"
    return "7z"

def split(path: str) -> Optional[Tuple[str, str]]:
    """``(archive file, inner "a/b")`` when *path* is an archive or goes through one; ``None`` otherwise.
    Real folders win: a folder named «x.zip» is a folder."""
    path = os.path.normpath(str(path or ""))
    if os.path.isdir(path):
        return None
    probe, rest = path, []
    for _ in range(64):
        if is_archive_name(probe) and os.path.isfile(probe):
            return probe, "/".join(reversed(rest))
        head, tail = os.path.split(probe)
        if not tail or head == probe:
            return None
        rest.append(tail)
        probe = head
        if os.path.isdir(probe):
            return None
    return None

def seven_zip() -> str:
    from .opqueue import OpQueue
    return OpQueue.seven_zip()

def _zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            return name.encode("cp437").decode("cp866")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return name

def _clean(name: str) -> str:
    parts = [p for p in str(name).replace("\\", "/").split("/") if p not in ("", ".")]
    return "/".join(p for p in parts if p != "..")

def _single_name(path: str) -> str:
    base = os.path.basename(path)
    stem = base[: base.rfind(".")] if "." in base else base
    return stem or "data"

def _scan(path: str) -> Dict[str, Any]:
    """Index of the whole archive: ``{"kind", "encrypted", "entries": {inner: row}, "dirs": {inner: [child names]}}``."""
    kind = kind_of(path)
    entries: Dict[str, Dict[str, Any]] = {}
    encrypted = False

    def add(name: str, is_dir: bool, size: int, mtime: float, packed: Optional[int] = None) -> None:
        name = _clean(name)
        if not name:
            return
        parts = name.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            entries.setdefault(parent, {"d": 1, "s": 0, "m": int(mtime * 1000)})
        row = {"d": 1 if is_dir else 0, "s": 0 if is_dir else int(size or 0), "m": int((mtime or 0) * 1000)}
        if packed is not None and not is_dir:
            row["p"] = int(packed)
        if is_dir and name in entries:
            entries[name].update(m=row["m"])
        else:
            entries[name] = row

    if kind == "zip" or (kind != "tar" and zipfile.is_zipfile(path)):
        kind = "zip"
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                encrypted = encrypted or bool(info.flag_bits & 0x1)
                try:
                    mtime = time.mktime(info.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    mtime = 0
                add(_zip_name(info), info.is_dir(), info.file_size, mtime, info.compress_size)
    elif kind == "tar" or (kind != "single" and tarfile.is_tarfile(path)):
        kind = "tar"
        with tarfile.open(path) as tf:
            for member in tf:
                if member.isdir() or member.isfile():
                    add(member.name, member.isdir(), member.size, member.mtime)
    elif kind == "single":
        add(_single_name(path), False, -1, os.path.getmtime(path))
        entries[_single_name(path)]["s"] = _single_size(path)
    else:
        tool = seven_zip()
        if not tool:
            raise FsError("no_7zip", path)
        result = subprocess.run([tool, "l", "-slt", "-ba", "-p", path], capture_output=True, timeout=120,  # noqa: S603
                                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        current: Dict[str, Any] = {}
        for line in result.stdout.decode("utf-8", "replace").splitlines() + [""]:
            if not line.strip():
                if current.get("Path"):
                    stamp = current.get("Modified", "")
                    try:
                        mtime = time.mktime(time.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        mtime = 0
                    is_dir = current.get("Folder") == "+" or current.get("Attributes", "")[:1] == "D"
                    encrypted = encrypted or current.get("Encrypted") == "+"
                    size = current.get("Size", "0")
                    packed = current.get("Packed Size", "")
                    add(current["Path"], is_dir, int(size) if size.isdigit() else 0, mtime,
                        int(packed) if packed.isdigit() else None)
                current = {}
                continue
            key, sep, value = line.partition(" = ")
            if sep:
                current[key] = value
        if not entries and result.returncode not in (0, 1):
            raise FsError("archive_unreadable", result.stderr.decode("utf-8", "replace")[:200] or path)
    dirs: Dict[str, List[str]] = {"": []}
    for name in entries:
        parent, _, leaf = name.rpartition("/")
        dirs.setdefault(parent, []).append(leaf)
        if entries[name]["d"]:
            dirs.setdefault(name, [])
    return {"kind": kind, "encrypted": encrypted, "entries": entries, "dirs": dirs}

def _single_size(path: str) -> int:
    if path.lower().endswith(".gz"):
        try:
            with open(path, "rb") as handle:
                handle.seek(-4, 2)
                return int.from_bytes(handle.read(4), "little")
        except OSError:
            return 0
    return 0

def index(path: str) -> Dict[str, Any]:
    st = os.stat(path)
    sig = (st.st_mtime, st.st_size)
    key = fsops.norm(path)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == sig:
            return cached[1]
    data = _scan(path)
    with _LOCK:
        _CACHE[key] = (sig, data)
        while len(_CACHE) > 12:
            _CACHE.pop(next(iter(_CACHE)))
    return data

def info(path: str) -> Dict[str, Any]:
    """Summary for the page: kind, encrypted, files/folders count, unpacked size, the single root folder (if any)."""
    data = index(path)
    files = [r for r in data["entries"].values() if not r["d"]]
    roots = data["dirs"].get("", [])
    single = roots[0] if len(roots) == 1 and data["entries"].get(roots[0], {}).get("d") else ""
    return {"kind": data["kind"], "encrypted": data["encrypted"], "files": len(files),
            "folders": sum(1 for r in data["entries"].values() if r["d"]), "unpacked": sum(r["s"] for r in files),
            "single_root": single, "writable": can_add(path), "seven_zip": bool(seven_zip())}

def listing(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """``(head, rows)`` of the folder *path* inside an archive (raises FileNotFoundError for a missing inner folder)."""
    found = split(path)
    if not found:
        raise FileNotFoundError(path)
    archive, inner = found
    data = index(archive)
    if inner and not data["entries"].get(inner, {}).get("d"):
        raise NotADirectoryError(path) if inner in data["entries"] else FileNotFoundError(path)
    rows = []
    for leaf in sorted(data["dirs"].get(inner, [])):
        row = dict(data["entries"][(inner + "/" if inner else "") + leaf])
        row["n"] = leaf
        row["a"] = 1
        if leaf.startswith("."):
            row["h"] = 1
        rows.append(row)
    head = {"archive": {"path": archive, "inner": inner, "kind": data["kind"], "encrypted": data["encrypted"],
                        "writable": can_add(archive)}}
    return head, rows

def can_add(path: str) -> bool:
    kind = kind_of(path)
    if kind == "zip":
        return os.access(path, os.W_OK)
    if kind == "single":
        return False
    return bool(seven_zip()) and os.access(path, os.W_OK) and not path.lower().endswith((".rar", ".iso", ".cab"))

def _cache_root() -> str:
    root = os.path.join(tempfile.gettempdir(), "memufiles-archive")
    os.makedirs(root, exist_ok=True)
    return root

def materialize(path: str, password: str = "") -> str:
    """A real file with the content of the archive member *path* (cached per archive state); folders are refused."""
    found = split(path)
    if not found or not found[1]:
        raise FileNotFoundError(path)
    archive, inner = found
    data = index(archive)
    row = data["entries"].get(inner)
    if row is None:
        raise FileNotFoundError(path)
    if row["d"]:
        raise FsError("is_archive_folder", path)
    st = os.stat(archive)
    tag = hashlib.sha1(f"{fsops.norm(archive)}|{st.st_mtime}|{st.st_size}".encode("utf-8", "replace")).hexdigest()[:16]
    target = os.path.join(_cache_root(), tag, *inner.split("/"))
    if os.path.isfile(target) and os.path.getsize(target) == row["s"]:
        return target
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = target + ".part"
    kind = data["kind"]
    try:
        with open(partial, "wb") as out:
            if kind == "zip":
                with zipfile.ZipFile(archive) as zf:
                    member = next((i for i in zf.infolist() if _clean(_zip_name(i)) == inner), None)
                    if member is None:
                        raise FileNotFoundError(path)
                    try:
                        with zf.open(member, pwd=password.encode("utf-8") if password else None) as reader:
                            shutil.copyfileobj(reader, out, 1024 * 1024)
                    except RuntimeError as exc:
                        raise FsError("password_required" if not password else "wrong_password", path) from exc
            elif kind == "tar":
                with tarfile.open(archive) as tf:
                    member = next((m for m in tf if _clean(m.name) == inner and m.isfile()), None)
                    reader = tf.extractfile(member) if member is not None else None
                    if reader is None:
                        raise FileNotFoundError(path)
                    with reader:
                        shutil.copyfileobj(reader, out, 1024 * 1024)
            elif kind == "single":
                opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[os.path.splitext(archive.lower())[1]]
                with opener(archive, "rb") as reader:
                    shutil.copyfileobj(reader, out, 1024 * 1024)
            else:
                tool = seven_zip()
                if not tool:
                    raise FsError("no_7zip", archive)
                proc = subprocess.run([tool, "e", "-so", "-p" + password, archive, inner.replace("/", os.sep)],  # noqa: S603
                                      stdout=out, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=600,
                                      creationflags=CREATE_NO_WINDOW)
                if proc.returncode not in (0, 1):
                    err = proc.stderr.decode("utf-8", "replace")
                    raise FsError("wrong_password" if "password" in err.lower() else "extract_failed", err[:200] or path)
        os.replace(partial, target)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    try:
        os.utime(target, (row["m"] / 1000, row["m"] / 1000))
    except (OSError, OverflowError, ValueError):
        pass
    return target

def resolve_read(path: str) -> str:
    """For read-only handlers (raw, thumb, text, open): a path inside an archive → its cached real file."""
    if os.path.exists(path):
        return path
    found = split(path)
    return materialize(path) if found and found[1] else path

def _pick(name: str, members: List[str], strip: str) -> Optional[str]:
    """The member's new relative name, or None when it is not chosen."""
    name = _clean(name)
    if members and not any(name == m or name.startswith(m + "/") for m in members):
        return None
    if strip:
        if not (name == strip or name.startswith(strip + "/")):
            return None
        name = name[len(strip) + 1:]
    return name or None

def extract(queue: Any, op: Any) -> None:
    """``extract`` with ``args.members`` (inner paths), ``args.strip`` (inner folder removed from the names) and
    ``args.password``. Same conflict handling, progress and undo as the plain extract."""
    archive = op.sources[0]
    dest = op.dest
    members = [_clean(m) for m in op.args.get("members") or [] if _clean(m)]
    strip = _clean(op.args.get("strip") or "")
    password = str(op.args.get("password") or "")
    if not os.path.isdir(os.path.dirname(dest) or dest):
        raise FsError("no_destination", dest)
    fresh = not os.path.exists(dest)
    os.makedirs(dest, exist_ok=True)
    if fresh:
        op.undo.append(("trash_created", dest))
    op.created_paths.append(dest)
    base = os.path.realpath(dest)

    def target_of(rel: str) -> Optional[str]:
        if not rel or ":" in rel.split("/")[0]:
            return None
        target = os.path.realpath(os.path.join(dest, *rel.split("/")))
        return target if fsops.is_inside(target, base) else None

    def write(reader: Any, rel: str, size: int, mtime: float) -> None:
        target = target_of(rel)
        if target is None:
            op.errors.append({"path": rel, "error": "unsafe_path"})
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        resolved = queue._target_for(op, archive + "/" + rel, target) if not fresh else target
        if resolved is None:
            op.done_items += 1
            return
        existed = os.path.lexists(resolved)
        op.current = rel
        try:
            queue._write_stream(op, reader, resolved, existed, size)
        except Exception as exc:
            from .opqueue import SkipFile
            if isinstance(exc, SkipFile):
                return
            raise
        try:
            os.utime(resolved, (mtime, mtime))
        except (OSError, OverflowError, ValueError):
            pass
        if not fresh and not existed:
            op.undo.append(("trash_created", resolved))
        op.done_items += 1

    kind = index(archive)["kind"]
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            chosen = [(i, _pick(_zip_name(i), members, strip)) for i in zf.infolist()]
            chosen = [(i, rel) for i, rel in chosen if rel]
            op.total_items = len(chosen)
            op.total_bytes = sum(i.file_size for i, _ in chosen if not i.is_dir())
            op.status = "running"
            pwd = password.encode("utf-8") if password else None
            for member, rel in chosen:
                op.check()
                if member.is_dir():
                    target = target_of(rel)
                    if target:
                        os.makedirs(target, exist_ok=True)
                    op.done_items += 1
                    continue
                try:
                    mtime = time.mktime(member.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    mtime = time.time()
                try:
                    with zf.open(member, pwd=pwd) as reader:
                        write(reader, rel, member.file_size, mtime)
                except RuntimeError as exc:
                    raise FsError("password_required" if not password else "wrong_password", archive) from exc
        return
    if kind == "tar":
        with tarfile.open(archive) as tf:
            chosen = [(m, _pick(m.name, members, strip)) for m in tf.getmembers() if m.isfile() or m.isdir()]
            chosen = [(m, rel) for m, rel in chosen if rel]
            op.total_items = len(chosen)
            op.total_bytes = sum(m.size for m, _ in chosen if m.isfile())
            op.status = "running"
            for member, rel in chosen:
                op.check()
                if member.isdir():
                    target = target_of(rel)
                    if target:
                        os.makedirs(target, exist_ok=True)
                    op.done_items += 1
                    continue
                reader = tf.extractfile(member)
                if reader is not None:
                    with reader:
                        write(reader, rel, member.size, member.mtime)
        return
    if kind == "single":
        name = _single_name(archive)
        if _pick(name, members, strip) is None:
            return
        op.total_items = 1
        op.total_bytes = os.path.getsize(archive)
        op.status = "running"
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[os.path.splitext(archive.lower())[1]]
        with opener(archive, "rb") as reader:
            write(reader, name, -1, os.path.getmtime(archive))
        return
    _extract_7z(queue, op, archive, dest, members, strip, password, fresh)

def _extract_7z(queue: Any, op: Any, archive: str, dest: str, members: List[str], strip: str, password: str,
                fresh: bool) -> None:
    """7-Zip formats: into a temp folder next to the destination, then moved in (conflicts asked as usual)."""
    tool = seven_zip()
    if not tool:
        raise FsError("no_7zip", archive)
    data = index(archive)
    wanted = [n for n, r in data["entries"].items() if not r["d"] and _pick(n, members, strip)]
    op.total_items = len(wanted)
    op.total_bytes = sum(data["entries"][n]["s"] for n in wanted)
    op.status = "running"
    temp = tempfile.mkdtemp(prefix=".memufiles-x-", dir=dest)
    try:
        cmd = [tool, "x", "-y", "-bso0", "-bsp1", "-p" + password, f"-o{temp}", archive]
        cmd += [m.replace("/", os.sep) for m in members] if members else []
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,  # noqa: S603
                                creationflags=CREATE_NO_WINDOW)
        pct = re.compile(rb"(\d{1,3})%")
        buf = b""
        while True:
            if op._cancel.is_set():
                proc.kill()
                from .opqueue import Cancelled
                raise Cancelled()
            chunk = proc.stdout.read1(256) if proc.stdout else b""   # type: ignore[union-attr]
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            buf = (buf + chunk)[-64:]
            hits = pct.findall(buf)
            if hits and op.total_bytes:
                done = int(op.total_bytes * min(100, int(hits[-1])) / 100)
                if done > op.done_bytes:
                    op.add_bytes(done - op.done_bytes)
                    queue._notify(op)
        if proc.wait() not in (0, 1):
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
            raise FsError("wrong_password" if "password" in err.lower() else "extract_failed", err[:300] or archive)
        src_root = os.path.join(temp, *strip.split("/")) if strip else temp
        for name in sorted(os.listdir(src_root)) if os.path.isdir(src_root) else []:
            src = os.path.join(src_root, name)
            target = os.path.join(dest, name)
            resolved = queue._target_for(op, src, target) if (not fresh and os.path.lexists(target)) else target
            if resolved is None:
                continue
            if os.path.lexists(resolved):
                if os.path.isdir(resolved) and not os.path.islink(resolved):
                    shutil.rmtree(resolved)
                else:
                    os.unlink(resolved)
            os.replace(src, resolved)
            if not fresh:
                op.undo.append(("trash_created", resolved))
        op.done_items = op.total_items
        if op.total_bytes > op.done_bytes:
            op.add_bytes(op.total_bytes - op.done_bytes)
    finally:
        shutil.rmtree(temp, ignore_errors=True)

LEVELS = {"store": (zipfile.ZIP_STORED, 0, 0), "fast": (zipfile.ZIP_DEFLATED, 1, 1), "normal": (zipfile.ZIP_DEFLATED, 6, 5),
          "max": (zipfile.ZIP_DEFLATED, 9, 9)}
FORMATS = ("zip", "7z", "tar", "tar.gz", "tar.xz", "tar.bz2")

def _files_of(sources: List[str]) -> Tuple[str, List[Tuple[str, str]], List[str]]:
    parent = os.path.commonpath([os.path.dirname(s.rstrip("\\/")) for s in sources]) if sources else ""
    files: List[Tuple[str, str]] = []
    empty_dirs: List[str] = []
    for src in sources:
        if os.path.isdir(src):
            for root, dirs, names in os.walk(src):
                rel_root = os.path.relpath(root, parent).replace(os.sep, "/")
                if not names and not dirs:
                    empty_dirs.append(rel_root)
                for name in names:
                    files.append((os.path.join(root, name), rel_root + "/" + name))
        else:
            files.append((src, os.path.relpath(src, parent).replace(os.sep, "/")))
    return parent, files, empty_dirs

class _Counting:
    """A reader that reports progress (tar streaming) and honours cancel."""

    def __init__(self, handle: Any, op: Any, queue: Any) -> None:
        self.handle, self.op, self.queue = handle, op, queue

    def read(self, size: int = -1) -> bytes:
        self.op.check()
        block = self.handle.read(size)
        if block:
            self.op.add_bytes(len(block))
            self.queue._notify(self.op)
        return block

def compress(queue: Any, op: Any) -> None:
    """``compress`` with ``args.format`` (zip | 7z | tar | tar.gz | tar.xz | tar.bz2), ``args.level``
    (store | fast | normal | max) and ``args.password`` (zip/7z — through 7-Zip)."""
    fmt = str(op.args.get("format") or "zip")
    if fmt not in FORMATS:
        raise FsError("bad_format", fmt)
    level = str(op.args.get("level") or "normal")
    level = level if level in LEVELS else "normal"
    password = str(op.args.get("password") or "")
    dest = op.dest
    folder = os.path.dirname(dest)
    if os.path.lexists(dest):
        dest = os.path.join(folder, fsops.unique_name(folder, os.path.basename(dest)))
        op.dest = dest
    queue._scan(op, op.sources)
    partial = dest + ".partial"
    parent, files, empty_dirs = _files_of(op.sources)
    try:
        if fmt == "7z" or (password and fmt == "zip"):
            _compress_7z(queue, op, partial, fmt, level, password)
        elif fmt == "zip":
            method, zlevel, _ = LEVELS[level]
            with zipfile.ZipFile(partial, "w", compression=method, compresslevel=zlevel or None, allowZip64=True) as zf:
                for rel in empty_dirs:
                    zf.writestr(rel + "/", b"")
                for full, rel in files:
                    op.check()
                    op.current = full
                    try:
                        op.start_file(full, os.path.getsize(full))
                        zinfo = zipfile.ZipInfo.from_file(full, rel, strict_timestamps=False)
                        zinfo.compress_type = method
                        with open(full, "rb") as reader, zf.open(zinfo, "w") as writer:
                            shutil.copyfileobj(_Counting(reader, op, queue), writer, 1024 * 1024)
                    except OSError as exc:
                        op.errors.append({"path": full, "error": exc.strerror or str(exc)})
                    op.done_items += 1
        else:
            mode = {"tar": "w", "tar.gz": "w:gz", "tar.xz": "w:xz", "tar.bz2": "w:bz2"}[fmt]
            kwargs: Dict[str, Any] = {}
            if fmt in ("tar.gz", "tar.bz2"):
                kwargs["compresslevel"] = max(1, LEVELS[level][1] or 1)
            elif fmt == "tar.xz":
                kwargs["preset"] = {"store": 0, "fast": 1, "normal": 6, "max": 9}[level]
            with tarfile.open(partial, mode, **kwargs) as tf:   # type: ignore[call-overload]
                for rel in empty_dirs:
                    tf.add(os.path.join(parent, *rel.split("/")), arcname=rel, recursive=False)
                for full, rel in files:
                    op.check()
                    op.current = full
                    try:
                        op.start_file(full, os.path.getsize(full))
                        tinfo = tf.gettarinfo(full, arcname=rel)
                        with open(full, "rb") as reader:
                            tf.addfile(tinfo, _Counting(reader, op, queue))
                    except OSError as exc:
                        op.errors.append({"path": full, "error": exc.strerror or str(exc)})
                    op.done_items += 1
        os.replace(partial, dest)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    op.undo.append(("trash_created", dest))
    op.created_paths.append(dest)

def _run_7z(queue: Any, op: Any, cmd: List[str], cwd: str) -> None:
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,  # noqa: S603
                            creationflags=CREATE_NO_WINDOW)
    pct = re.compile(rb"(\d{1,3})%")
    buf = b""
    while True:
        if op._cancel.is_set():
            proc.kill()
            from .opqueue import Cancelled
            raise Cancelled()
        chunk = proc.stdout.read1(256) if proc.stdout else b""   # type: ignore[union-attr]
        if not chunk:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
            continue
        buf = (buf + chunk)[-64:]
        hits = pct.findall(buf)
        if hits and op.total_bytes:
            done = int(op.total_bytes * min(100, int(hits[-1])) / 100)
            if done > op.done_bytes:
                op.add_bytes(done - op.done_bytes)
                queue._notify(op)
    if proc.wait() not in (0, 1):
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
        raise FsError("compress_failed", err[:300])

def _compress_7z(queue: Any, op: Any, partial: str, fmt: str, level: str, password: str) -> None:
    tool = seven_zip()
    if not tool:
        raise FsError("no_7zip", partial)
    parent = os.path.commonpath([os.path.dirname(s.rstrip("\\/")) for s in op.sources])
    names = [os.path.relpath(s, parent) for s in op.sources]
    cmd = [tool, "a", "-t" + ("7z" if fmt == "7z" else "zip"), "-bso0", "-bsp1", "-mx=" + str(LEVELS[level][2]), "-y"]
    if password:
        cmd += ["-p" + password] + (["-mhe=on"] if fmt == "7z" else ["-mem=AES256"])
    op.status = "running"
    _run_7z(queue, op, cmd + [partial, "--"] + names, parent)
    op.done_items = op.total_items

def add(queue: Any, op: Any) -> None:
    """``arcadd``: put ``sources`` into the archive ``dest`` under ``args.inner`` (zip natively, others via 7-Zip).
    Names already in a zip are reported (``exists``) — a zip can't replace an entry in place."""
    archive = op.dest
    inner = _clean(op.args.get("inner") or "")
    if not can_add(archive):
        raise FsError("archive_readonly", archive)
    queue._scan(op, op.sources)
    parent, files, empty_dirs = _files_of(op.sources)
    prefix = inner + "/" if inner else ""
    op.status = "running"
    if kind_of(archive) == "zip":
        with zipfile.ZipFile(archive) as zf:
            present = {_clean(_zip_name(i)) for i in zf.infolist()}
        with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for rel in empty_dirs:
                if prefix + rel not in present:
                    zf.writestr(prefix + rel + "/", b"")
            for full, rel in files:
                op.check()
                name = prefix + rel
                op.current = full
                if name in present:
                    op.errors.append({"path": full, "error": "exists"})
                    op.done_items += 1
                    continue
                op.start_file(full, os.path.getsize(full))
                zinfo = zipfile.ZipInfo.from_file(full, name, strict_timestamps=False)
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as reader, zf.open(zinfo, "w") as writer:
                    shutil.copyfileobj(_Counting(reader, op, queue), writer, 1024 * 1024)
                op.done_items += 1
    else:
        tool = seven_zip()
        if inner:
            stage = tempfile.mkdtemp(prefix=".memufiles-add-")
            try:
                target = os.path.join(stage, *inner.split("/"))
                os.makedirs(target, exist_ok=True)
                for src in op.sources:
                    dst = os.path.join(target, os.path.basename(src.rstrip("\\/")))
                    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
                _run_7z(queue, op, [tool, "a", "-bso0", "-bsp1", "-y", archive, "--", inner.split("/")[0]], stage)
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        else:
            names = [os.path.relpath(s, parent) for s in op.sources]
            _run_7z(queue, op, [tool, "a", "-bso0", "-bsp1", "-y", archive, "--"] + names, parent)
        op.done_items = op.total_items
    with _LOCK:
        _CACHE.pop(fsops.norm(archive), None)
    op.created_paths.append(archive)
