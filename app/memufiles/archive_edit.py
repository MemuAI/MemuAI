"""MemuFiles · editing an archive in place.

Operations (``opqueue``):

* ``arcedit`` — ``args.actions``: ``{"do": "delete", "path"}``, ``{"do": "rename", "from", "to"}`` (a full inner
  path, so it also moves things between folders of the archive) and ``{"do": "mkdir", "path"}``, applied in order;
* ``arctest`` — reads every member back (CRC / stream check), damaged ones land in ``op.errors``;
* ``arcconvert`` — the same content in another format (zip / 7z / tar / tar.gz / tar.xz / tar.bz2);
* :func:`rewrite_add` — «add» with replacing names that are already there (used by ``archive.add``).

How an archive is changed:

* zip-family files are rewritten natively entry by entry; untouched members are copied RAW — no recompression,
  encryption and compression methods stay as they were, legacy (non-UTF-8) names keep their bytes;
* tar / tar.gz / tar.bz2 / tar.xz are re-streamed with :mod:`tarfile` (hard links follow their targets);
* 7z goes through 7-Zip (``d`` / ``rn`` with wildcards off, ``a`` from a staging folder) on a copy;
* the new archive is built next to the old one (``.partial``) and swapped in; the old one goes to the Recycle Bin
  under a dated name, so «Undo» puts it back.

rar / iso / cab and single-file .gz / .bz2 / .xz stay read-only. Stdlib only (7-Zip is optional).
"""
from __future__ import annotations

import bz2
import gzip
import logging
import lzma
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
import zipfile
import zlib
from typing import Any, Dict, List, Optional, Tuple

from . import archive as arc, fsops, trash
from .fsops import FsError

log = logging.getLogger("memufiles.archive_edit")

ACTIONS = ("delete", "rename", "mkdir")
EDIT_7Z = (".7z", ".cb7")
MAX_ACTIONS = 10000
MAX_COMMENT = 65535
CHUNK = 1024 * 1024
_TAR_EXT = {".tar.gz": "gz", ".tgz": "gz", ".tar.bz2": "bz2", ".tbz": "bz2", ".tbz2": "bz2", ".tar.xz": "xz",
            ".txz": "xz", ".tar": ""}
_BAD_CHARS = set('<>:"|?*\\')

# ── what can be edited ─────────────────────────────────────────────────────────────────────────────────────────

def editable(path: str) -> str:
    """``"zip"`` / ``"tar"`` / ``"7z"`` — how *path* can be changed, ``""`` when it is read-only."""
    try:
        if not os.path.isfile(path) or not os.access(path, os.W_OK) or not os.access(os.path.dirname(path) or ".", os.W_OK):
            return ""
    except (OSError, ValueError):
        return ""
    kind = arc.kind_of(path)
    if kind in ("zip", "tar"):
        return kind
    if kind == "7z" and path.lower().endswith(EDIT_7Z) and arc.seven_zip():
        return "7z"
    return ""

def _tar_compression(path: str) -> str:
    low = path.lower()
    for ext, mode in sorted(_TAR_EXT.items(), key=lambda kv: -len(kv[0])):
        if low.endswith(ext):
            return mode
    with open(path, "rb") as handle:
        magic = handle.read(6)
    if magic[:2] == b"\x1f\x8b":
        return "gz"
    if magic[:3] == b"BZh":
        return "bz2"
    if magic == b"\xfd7zXZ\x00":
        return "xz"
    return ""

# ── actions → a plan ───────────────────────────────────────────────────────────────────────────────────────────

def _check_new(name: str) -> None:
    for part in name.split("/"):
        if len(part) > 255 or any(ch in _BAD_CHARS or ord(ch) < 32 for ch in part) or part.rstrip(" .") != part:
            raise FsError("invalid_chars", name)

def clean_actions(raw: Any) -> List[Dict[str, str]]:
    """Validated ``args.actions`` (inner paths normalised, nothing can point outside the archive)."""
    if not isinstance(raw, list) or not raw:
        raise FsError("nothing_selected")
    if len(raw) > MAX_ACTIONS:
        raise FsError("too_many", str(len(raw)))
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise FsError("bad_action")
        do = str(item.get("do") or "")
        if do in ("delete", "mkdir"):
            path = arc._clean(item.get("path") or "")
            if not path:
                raise FsError("bad_action", do)
            if do == "mkdir":
                _check_new(path)
            out.append({"do": do, "path": path})
        elif do == "rename":
            src, dst = arc._clean(item.get("from") or ""), arc._clean(item.get("to") or "")
            if not src or not dst:
                raise FsError("bad_action", do)
            if dst == src:
                continue
            if dst.startswith(src + "/"):
                raise FsError("into_self", dst)
            _check_new(dst)
            out.append({"do": "rename", "from": src, "to": dst})
        else:
            raise FsError("bad_action", do)
    if not out:
        raise FsError("nothing_selected")
    return out

def _under(name: str, root: str) -> bool:
    return name == root or name.startswith(root + "/")

def _parents(name: str) -> List[str]:
    parts = name.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]

class Plan:
    """Where every inner name ends up: :meth:`map` gives the new name or ``None`` (deleted)."""

    def __init__(self, entries: Dict[str, Dict[str, Any]], actions: List[Dict[str, str]]) -> None:
        self.cur: Dict[str, Optional[str]] = {name: name for name in entries}
        alive = set(entries)
        dirs = {name for name, row in entries.items() if row.get("d")}
        self.created: List[str] = []

        def no_file_parents(path: str) -> None:
            for parent in _parents(path):
                if parent in alive and parent not in dirs:
                    raise FsError("exists", parent)

        for act in actions:
            if act["do"] == "delete":
                root = act["path"]
                if root not in alive:
                    raise FsError("not_found", root)
                for orig, now in self.cur.items():
                    if now is not None and _under(now, root):
                        self.cur[orig] = None
                self.created = [c for c in self.created if not _under(c, root)]
                alive = {n for n in alive if not _under(n, root)}
                dirs = {n for n in dirs if not _under(n, root)}
            elif act["do"] == "rename":
                src, dst = act["from"], act["to"]
                if src not in alive:
                    raise FsError("not_found", src)
                if dst in alive:
                    raise FsError("exists", dst)
                no_file_parents(dst)
                move = lambda n, s=src, d=dst: d + n[len(s):] if _under(n, s) else n  # noqa: E731
                for orig, now in self.cur.items():
                    if now is not None and _under(now, src):
                        self.cur[orig] = move(now)
                self.created = [move(c) for c in self.created]
                alive = {move(n) for n in alive} | set(_parents(dst))
                dirs = {move(n) for n in dirs} | set(_parents(dst))
            else:
                path = act["path"]
                if path in alive:
                    raise FsError("exists", path)
                no_file_parents(path)
                self.created.append(path)
                alive |= {path, *_parents(path)}
                dirs |= {path, *_parents(path)}
        self.changed = any(orig != now for orig, now in self.cur.items()) or bool(self.created)

    def map(self, name: str) -> Optional[str]:
        """The new name of an inner *name* (members the index does not list — links, devices — follow their folder)."""
        if name in self.cur:
            return self.cur[name]
        for parent in reversed(_parents(name)):
            if parent in self.cur:
                now = self.cur[parent]
                return None if now is None else now + name[len(parent):]
        return name

# ── zip: raw copy of members ───────────────────────────────────────────────────────────────────────────────────

class _RawInfo(zipfile.ZipInfo):
    """A member copied as it is: the name keeps its original bytes (cp437 / OEM names stay unchanged)."""
    __slots__ = ("raw_name",)

    def _encodeFilenameFlags(self) -> Tuple[bytes, int]:   # noqa: N802 — zipfile's name
        return self.raw_name, self.flag_bits

def _strip_zip64(extra: bytes) -> bytes:
    out, i = b"", 0
    while i + 4 <= len(extra):
        xid, size = struct.unpack("<HH", extra[i:i + 4])
        if xid != 1:
            out += extra[i:i + 4 + size]
        i += 4 + size
    return out

def _copy_member(src: Any, zout: zipfile.ZipFile, info: zipfile.ZipInfo, new_name: Optional[str], op: Any,
                 queue: Any) -> None:
    """Copy one member's compressed bytes into *zout* (renamed when *new_name* is given)."""
    src.seek(info.header_offset)
    head = src.read(30)
    if len(head) < 30 or head[:4] != b"PK\x03\x04":
        raise FsError("archive_damaged", info.filename)
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    src.seek(name_len + extra_len, 1)
    if new_name is None:
        out: zipfile.ZipInfo = _RawInfo(info.filename, info.date_time)
        out.raw_name = info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437")   # type: ignore[attr-defined]
        out.flag_bits = info.flag_bits
    else:
        out = zipfile.ZipInfo(new_name, info.date_time)
        out.flag_bits = info.flag_bits & ~0x800
    for attr in ("compress_type", "comment", "create_system", "create_version", "extract_version", "external_attr",
                 "internal_attr", "CRC", "compress_size", "file_size"):
        setattr(out, attr, getattr(info, attr))
    out.extra = _strip_zip64(info.extra)
    zip64 = info.file_size > zipfile.ZIP64_LIMIT or info.compress_size > zipfile.ZIP64_LIMIT
    zout.fp.seek(zout.start_dir)   # type: ignore[union-attr]
    out.header_offset = zout.fp.tell()   # type: ignore[union-attr]
    zout.fp.write(out.FileHeader(zip64))   # type: ignore[union-attr]
    left = info.compress_size
    while left > 0:
        op.check()
        block = src.read(min(CHUNK, left))
        if not block:
            raise FsError("archive_damaged", info.filename)
        zout.fp.write(block)   # type: ignore[union-attr]
        left -= len(block)
        op.add_bytes(len(block))
        queue._notify(op)
    if info.flag_bits & 0x08:
        fmt = "<LLQQ" if zip64 else "<LLLL"
        zout.fp.write(struct.pack(fmt, 0x08074b50, info.CRC, info.compress_size, info.file_size))   # type: ignore[union-attr]
    zout.start_dir = zout.fp.tell()   # type: ignore[union-attr]
    zout.filelist.append(out)
    zout.NameToInfo[out.filename] = out
    zout._didModify = True   # type: ignore[attr-defined]

def _dir_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name.rstrip("/") + "/", time.localtime()[:6])
    info.external_attr = (0o40775 << 16) | 0x10
    return info

def _zip_add_file(zout: zipfile.ZipFile, full: str, name: str, op: Any, queue: Any) -> None:
    op.current = full
    op.start_file(full, os.path.getsize(full))
    zinfo = zipfile.ZipInfo.from_file(full, name, strict_timestamps=False)
    zinfo.compress_type = zipfile.ZIP_DEFLATED
    with open(full, "rb") as reader, zout.open(zinfo, "w") as writer:
        shutil.copyfileobj(arc._Counting(reader, op, queue), writer, CHUNK)

def _rewrite_zip(queue: Any, op: Any, archive: str, partial: str, plan: Plan,
                 adds: List[Tuple[str, str]], add_dirs: List[str]) -> None:
    with zipfile.ZipFile(archive) as zin:
        infos = zin.infolist()
        comment = zin.comment
    op.total_bytes = sum(i.compress_size for i in infos) + sum(os.path.getsize(f) for f, _ in adds)
    op.status = "running"
    queue._notify(op, force=True)
    with open(archive, "rb") as src, zipfile.ZipFile(partial, "w", allowZip64=True) as zout:
        for info in infos:
            op.check()
            name = arc._clean(arc._zip_name(info))
            new = plan.map(name) if name else name
            if new is None:
                continue
            op.current = name
            _copy_member(src, zout, info, None if new == name else new + ("/" if info.is_dir() else ""), op, queue)
        for name in plan.created + add_dirs:
            zout.writestr(_dir_info(name), b"")
        for full, name in adds:
            op.check()
            try:
                _zip_add_file(zout, full, name, op, queue)
            except OSError as exc:
                op.errors.append({"path": full, "error": exc.strerror or str(exc)})
            op.done_items += 1
        zout.comment = comment

# ── tar: re-stream ─────────────────────────────────────────────────────────────────────────────────────────────

def _tar_writer(partial: str, compression: str) -> tarfile.TarFile:
    if compression == "gz":
        return tarfile.open(partial, "w:gz", compresslevel=6)
    if compression == "bz2":
        return tarfile.open(partial, "w:bz2", compresslevel=9)
    if compression == "xz":
        return tarfile.open(partial, "w:xz", preset=6)
    return tarfile.open(partial, "w")

def _rewrite_tar(queue: Any, op: Any, archive: str, partial: str, plan: Plan,
                 adds: List[Tuple[str, str]], add_dirs: List[str]) -> None:
    data = arc.index(archive)
    op.total_bytes = sum(r["s"] for r in data["entries"].values() if not r["d"]) + \
        sum(os.path.getsize(f) for f, _ in adds)
    op.status = "running"
    queue._notify(op, force=True)
    with tarfile.open(archive) as tin, _tar_writer(partial, _tar_compression(archive)) as tout:
        for member in tin:
            op.check()
            name = arc._clean(member.name)
            new = plan.map(name) if name else name
            if new is None:
                continue
            out = member if new == name and not member.islnk() else _renamed(member, new or member.name)
            op.current = name
            if member.islnk():
                target = plan.map(arc._clean(member.linkname))
                if target is None:   # the file it pointed to is gone: the link becomes that file
                    source = tin._find_link_target(member)   # type: ignore[attr-defined]
                    reader = tin.extractfile(source)
                    out.type, out.linkname, out.size = tarfile.REGTYPE, "", source.size
                    tout.addfile(out, arc._Counting(reader, op, queue) if reader else None)
                    continue
                out.linkname = target
            if member.isfile():
                reader = tin.extractfile(member)
                tout.addfile(out, arc._Counting(reader, op, queue) if reader else None)
            else:
                tout.addfile(out)
        for name in plan.created + add_dirs:
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
            tout.addfile(info)
        for full, name in adds:
            op.check()
            op.current = full
            try:
                op.start_file(full, os.path.getsize(full))
                info = tout.gettarinfo(full, arcname=name)
                with open(full, "rb") as reader:
                    tout.addfile(info, arc._Counting(reader, op, queue))
            except OSError as exc:
                op.errors.append({"path": full, "error": exc.strerror or str(exc)})
            op.done_items += 1

def _renamed(member: tarfile.TarInfo, name: str) -> tarfile.TarInfo:
    import copy
    out = copy.copy(member)
    out.pax_headers = {k: v for k, v in member.pax_headers.items() if k != "path"}
    out.name = name
    return out

# ── 7z through 7-Zip ───────────────────────────────────────────────────────────────────────────────────────────

def _list_file(folder: str, lines: List[str]) -> str:
    path = os.path.join(folder, "list.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path

_PASSWORD_HINTS = ("wrong password", "can not open encrypted", "cannot open encrypted", "break signaled",
                   "enter password")

def _seven(queue: Any, op: Any, cmd: List[str], cwd: str) -> None:
    try:
        arc._run_7z(queue, op, cmd, cwd)
    except FsError as exc:
        text = str(exc).lower()
        if any(hint in text for hint in _PASSWORD_HINTS):
            raise FsError("password_required", op.dest) from None
        raise FsError("archive_edit_failed", str(exc)[:300]) from None

def _edit_7z(queue: Any, op: Any, archive: str, partial: str, actions: List[Dict[str, str]], password: str) -> None:
    tool = arc.seven_zip()
    op.total_bytes = os.path.getsize(archive)
    op.status = "running"
    queue._notify(op, force=True)
    with open(archive, "rb") as reader, open(partial, "wb") as writer:
        shutil.copyfileobj(arc._Counting(reader, op, queue), writer, CHUNK)
    op.done_bytes = 0
    base = ["-y", "-bso0", "-bsp1", "-t7z", "-spd", "-scsUTF-8"] + (["-p" + password] if password else [])
    work = tempfile.mkdtemp(prefix=".memufiles-7z-")
    try:
        groups: List[Tuple[str, List[Dict[str, str]]]] = []
        for act in actions:
            if groups and groups[-1][0] == act["do"] and act["do"] != "mkdir":
                groups[-1][1].append(act)
            else:
                groups.append((act["do"], [act]))
        for do, acts in groups:
            op.check()
            if do == "delete":
                listing = _list_file(work, [a["path"] for a in acts])
                _seven(queue, op, [tool, "d"] + base + [partial, "@" + listing], work)
            elif do == "rename":
                listing = _list_file(work, [x for a in acts for x in (a["from"], a["to"])])
                _seven(queue, op, [tool, "rn"] + base + [partial, "@" + listing], work)
            else:
                stage = tempfile.mkdtemp(dir=work)
                os.makedirs(os.path.join(stage, *acts[0]["path"].split("/")), exist_ok=True)
                _seven(queue, op, [tool, "a"] + base + [partial, "--", acts[0]["path"].split("/")[0]], stage)
            op.done_items += len(acts)
    finally:
        shutil.rmtree(work, ignore_errors=True)

# ── swap in + undo ─────────────────────────────────────────────────────────────────────────────────────────────

def _split_ext(name: str) -> Tuple[str, str]:
    low = name.lower()
    for ext in arc.COMPOUND:
        if low.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)], name[-len(ext):]
    stem, ext = os.path.splitext(name)
    return (stem, ext) if stem else (name, "")

def _swap(op: Any, archive: str, partial: str) -> None:
    """Put *partial* in place of *archive*; the old archive goes to the Recycle Bin under a dated name (undo)."""
    folder, name = os.path.split(archive)
    stem, ext = _split_ext(name)
    backup = os.path.join(folder, fsops.unique_name(folder, f"{stem} ({time.strftime('%Y-%m-%d %H-%M-%S')}){ext}"))
    try:
        shutil.copymode(archive, partial)
    except OSError:
        pass
    os.replace(archive, backup)
    try:
        os.replace(partial, archive)
    except BaseException:
        os.replace(backup, archive)
        raise
    with arc._LOCK:
        arc._CACHE.pop(fsops.norm(archive), None)
    try:
        sent = trash.send([backup])
        token = sent[0].get("token", "") if sent else ""
    except Exception as exc:  # noqa: BLE001 — no Recycle Bin there (network share…): the edit stays, no undo
        log.warning("old copy of %s not kept: %s", archive, exc)
        try:
            os.unlink(backup)
        except OSError:
            pass
        return
    op.undo += [("move_back", backup, archive), ("restore", backup, token), ("trash_created", archive)]

def _drop(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass

# ── operations ─────────────────────────────────────────────────────────────────────────────────────────────────

def edit(queue: Any, op: Any) -> None:
    """``arcedit``: delete / rename / move / new folder inside the archive ``op.dest``."""
    archive = op.dest
    how = editable(archive)
    if not how:
        raise FsError("archive_readonly", archive)
    actions = clean_actions(op.args.get("actions"))
    plan = Plan(arc.index(archive)["entries"], actions)
    op.total_items = len(actions)
    if not plan.changed:
        op.done_items = op.total_items
        return
    partial = archive + ".partial"
    try:
        if how == "zip":
            _rewrite_zip(queue, op, archive, partial, plan, [], [])
        elif how == "tar":
            _rewrite_tar(queue, op, archive, partial, plan, [], [])
        else:
            _edit_7z(queue, op, archive, partial, actions, str(op.args.get("password") or ""))
        op.check()
        _swap(op, archive, partial)
    except BaseException:
        _drop(partial)
        raise
    op.done_items = op.total_items
    op.created_paths.append(archive)

def rewrite_add(queue: Any, op: Any, archive: str, files: List[Tuple[str, str]], empty_dirs: List[str],
                policy: str) -> None:
    """«Add» to a zip / tar by rewriting it. Names already there: ``policy`` «replace» swaps them, «skip» keeps the
    old ones quietly, anything else keeps them and reports ``exists``. A file never replaces a folder."""
    how = editable(archive)
    if how not in ("zip", "tar"):
        raise FsError("archive_readonly", archive)
    entries = arc.index(archive)["entries"]
    keep: List[Tuple[str, str]] = []
    clash: List[str] = []
    for full, name in files:
        row = entries.get(name)
        if row is None:
            keep.append((full, name))
        elif row.get("d") or policy not in ("replace", "skip"):
            op.errors.append({"path": full, "error": "exists"})
            op.done_items += 1
        elif policy == "replace":
            keep.append((full, name))
            clash.append(name)
        else:
            op.done_items += 1
    dirs = [d for d in empty_dirs if d not in entries]
    if not keep and not dirs:
        return
    plan = Plan(entries, [{"do": "delete", "path": n} for n in clash])
    partial = archive + ".partial"
    try:
        if how == "zip":
            _rewrite_zip(queue, op, archive, partial, plan, keep, dirs)
        else:
            _rewrite_tar(queue, op, archive, partial, plan, keep, dirs)
        op.check()
        _swap(op, archive, partial)
    except BaseException:
        _drop(partial)
        raise

def _test_7z(queue: Any, op: Any, archive: str, password: str) -> None:
    tool = arc.seven_zip()
    if not tool:
        raise FsError("no_7zip", archive)
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen([tool, "t", "-y", "-bb1", "-bsp0", "-scsUTF-8", "-p" + password, archive],  # noqa: S603
                                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=arc.CREATE_NO_WINDOW)
        # F34: a hung 7z used to run forever (no timeout, only cancel).
        deadline = time.monotonic() + 600
        while proc.poll() is None:
            if time.monotonic() > deadline:
                proc.kill()
                raise FsError("extract_timeout", archive)
            if op._cancel.is_set():
                proc.kill()
                from .opqueue import Cancelled
                raise Cancelled()
            time.sleep(0.1)
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    if re.search(r"wrong password|can ?not open encrypted|break signaled", text, re.I):
        raise FsError("password_required" if not password else "bad_password", archive)
    bad = []
    for line in text.splitlines():
        hit = re.match(r"^\s*(?:ERROR:\s*)?(?:Data Error|CRC Failed|Unexpected end of data|Headers Error)[^:]*:\s*(.+)$",
                       line, re.I)
        if hit:
            bad.append(hit.group(1).strip())
    for name in bad[:500]:
        op.errors.append({"path": name, "error": "damaged"})
    if proc.returncode not in (0, 1) and not bad:
        op.errors.append({"path": archive, "error": "damaged"})
    op.done_items = max(1, op.total_items - len(bad))

def test(queue: Any, op: Any) -> None:
    """``arctest``: read every member back; damaged ones are listed in ``op.errors``."""
    archive = op.sources[0] if op.sources else op.dest
    password = str(op.args.get("password") or "")
    broken = (zipfile.BadZipFile, zlib.error, EOFError, lzma.LZMAError, OSError, tarfile.TarError, ValueError,
              struct.error)
    try:
        data = arc.index(archive)
    except broken:
        op.errors.append({"path": os.path.basename(archive), "error": "damaged"})
        op.total_items = op.done_items = 1
        op.message = "damaged"
        return
    files = [r for r in data["entries"].values() if not r["d"]]
    op.total_items = len(files)
    op.total_bytes = sum(max(0, r["s"]) for r in files)
    op.status = "running"
    queue._notify(op, force=True)
    kind = data["kind"]

    def drain(reader: Any, name: str) -> None:
        op.current = name
        while True:
            op.check()
            block = reader.read(CHUNK)
            if not block:
                break
            op.add_bytes(len(block))
            queue._notify(op)

    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = arc._clean(arc._zip_name(info))
                try:
                    with zf.open(info, pwd=password.encode("utf-8") if password else None) as reader:
                        drain(reader, name)
                except NotImplementedError:
                    if arc.seven_zip():
                        op.errors.clear()
                        return _test_7z(queue, op, archive, password)
                    op.errors.append({"path": name, "error": "unsupported_method"})
                except RuntimeError as exc:
                    if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                        raise FsError("password_required" if not password else "bad_password", archive) from None
                    op.errors.append({"path": name, "error": "damaged"})
                except broken:
                    op.errors.append({"path": name, "error": "damaged"})
                op.done_items += 1
    elif kind == "tar":
        try:
            with tarfile.open(archive) as tf:
                for member in tf:
                    if not member.isfile():
                        continue
                    name = arc._clean(member.name)
                    try:
                        reader = tf.extractfile(member)
                        if reader:
                            drain(reader, name)
                    except broken:
                        op.errors.append({"path": name, "error": "damaged"})
                        break
                    op.done_items += 1
        except broken:
            op.errors.append({"path": archive, "error": "damaged"})
    elif kind == "single":
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}.get(os.path.splitext(archive)[1].lower())
        try:
            with opener(archive, "rb") as reader:   # type: ignore[misc]
                drain(reader, os.path.basename(archive))
            op.done_items = 1
        except broken:
            op.errors.append({"path": os.path.basename(archive), "error": "damaged"})
    else:
        _test_7z(queue, op, archive, password)
    op.message = "ok" if not op.errors else "damaged"

def _unpack_all(queue: Any, op: Any, archive: str, folder: str, password: str) -> None:
    """Unpack an archive into *folder* (used by edit/convert).

    F34: the old code trusted the cleaned name only — a tar member that is
    a symlink / device / hardlink was skipped by the isfile() check but a
    zip member with a symlink bit, or a tar member whose *name* resolves
    through a previously created symlink dir, could still escape *folder*.
    Every target is re-checked with is_inside() after realpath, symlinks
    and special files are refused, and a bad timestamp no longer aborts
    the whole unpack.
    """
    data = arc.index(archive)
    kind = data["kind"]
    base = os.path.realpath(folder)

    def _safe_target(name: str) -> Optional[str]:
        target = os.path.realpath(os.path.join(folder, *name.split("/")))
        return target if fsops.is_inside(target, base) else None

    def _stamp(target: str, stamp: float) -> None:
        try:
            os.utime(target, (stamp, stamp))
        except (OSError, OverflowError, ValueError):
            pass

    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                op.check()
                name = arc._clean(arc._zip_name(info))
                if not name:
                    continue
                target = _safe_target(name)
                if target is None:
                    op.errors.append({"path": name, "error": "unsafe_path"})
                    continue
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if (info.external_attr >> 28) == 0xA:  # symlink bit in a zip
                    op.errors.append({"path": name, "error": "symlink_skipped"})
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                try:
                    with zf.open(info, pwd=password.encode("utf-8") if password else None) as reader, \
                            open(target, "wb") as writer:
                        shutil.copyfileobj(reader, writer, CHUNK)
                except RuntimeError as exc:
                    raise FsError("password_required" if not password else "bad_password", archive) from exc
                except NotImplementedError:
                    shutil.rmtree(folder, ignore_errors=True)
                    os.makedirs(folder, exist_ok=True)
                    return _unpack_7z(queue, op, archive, folder, password)
                try:
                    stamp = time.mktime(info.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    stamp = time.time()
                _stamp(target, stamp)
    elif kind == "tar":
        with tarfile.open(archive) as tf:
            for member in tf:
                op.check()
                name = arc._clean(member.name)
                # F34: refuse symlinks, hardlinks and device nodes outright.
                if not name or (not member.isfile() and not member.isdir()) or member.issym() or member.islnk() or member.isdev():
                    continue
                target = _safe_target(name)
                if target is None:
                    op.errors.append({"path": name, "error": "unsafe_path"})
                    continue
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                reader = tf.extractfile(member)
                with open(target, "wb") as writer:
                    if reader:
                        shutil.copyfileobj(reader, writer, CHUNK)
                _stamp(target, member.mtime)
    elif kind == "single":
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[os.path.splitext(archive)[1].lower()]
        with opener(archive, "rb") as reader, open(os.path.join(folder, arc._single_name(archive)), "wb") as writer:   # type: ignore[operator]
            shutil.copyfileobj(reader, writer, CHUNK)
    else:
        _unpack_7z(queue, op, archive, folder, password)

def _unpack_7z(queue: Any, op: Any, archive: str, folder: str, password: str) -> None:
    tool = arc.seven_zip()
    if not tool:
        raise FsError("no_7zip", archive)
    try:
        arc._run_7z(queue, op, [tool, "x", "-y", "-bso0", "-bsp1", "-p" + password, "-o" + folder, "--", archive], folder)
    except FsError as exc:
        if any(hint in str(exc).lower() for hint in _PASSWORD_HINTS):
            raise FsError("password_required" if not password else "bad_password", archive) from None
        raise

def convert(queue: Any, op: Any) -> None:
    """``arcconvert``: ``sources[0]`` → a new archive ``dest`` in ``args.format`` (the old one stays)."""
    source = op.sources[0]
    fmt = str(op.args.get("format") or "zip")
    if fmt not in arc.FORMATS:
        raise FsError("bad_format", fmt)
    if fmt == "7z" and not arc.seven_zip():
        raise FsError("no_7zip", source)
    temp = tempfile.mkdtemp(prefix=".memufiles-convert-")
    try:
        op.status = "scanning"
        queue._notify(op, force=True)
        _unpack_all(queue, op, source, temp, str(op.args.get("source_password") or ""))
        tops = [os.path.join(temp, n) for n in sorted(os.listdir(temp))]
        if not tops:
            raise FsError("archive_empty", source)
        op.done_bytes = op.total_bytes = 0
        op.sources = tops
        try:
            arc.compress(queue, op)
        finally:
            op.sources = [source]
    finally:
        shutil.rmtree(temp, ignore_errors=True)

# ── zip comment ────────────────────────────────────────────────────────────────────────────────────────────────

def get_comment(path: str) -> Dict[str, Any]:
    if arc.kind_of(path) != "zip" or not zipfile.is_zipfile(path):
        return {"supported": False, "comment": "", "writable": False}
    with zipfile.ZipFile(path) as zf:
        raw = zf.comment or b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp866", "replace")
    return {"supported": True, "comment": text, "writable": editable(path) == "zip"}

def set_comment(path: str, text: str) -> Dict[str, Any]:
    if editable(path) != "zip":
        raise FsError("archive_readonly", path)
    raw = str(text or "").replace("\r\n", "\n").encode("utf-8")
    if len(raw) > MAX_COMMENT:
        raise FsError("too_long", str(len(raw)))
    with zipfile.ZipFile(path, "a") as zf:
        zf.comment = raw
    with arc._LOCK:
        arc._CACHE.pop(fsops.norm(path), None)
    return get_comment(path)
