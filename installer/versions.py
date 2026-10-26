"""Version numbers of installed copies: normalising, comparing, classifying.

The Setup decides between four welcome screens from two numbers — its own
version (``app/version.py``) and the version already installed:

* ``fresh``     — nothing is installed;
* ``upgrade``   — an older version is installed (or one whose number cannot be read);
* ``reinstall`` — the same version is installed (repair / change components);
* ``downgrade`` — a newer version is installed.

The installed number is read robustly (see :func:`installer.engine.Engine.detect`):
``install.json`` → ``DisplayVersion`` of the uninstall key (ours or Inno's) →
the file-version resource of the main exe → Inno's ``MajorVersion``/``MinorVersion``.
Garbage, empty values and ``0.0.0.0`` count as "unknown".

:func:`pe_file_version` reads ``VS_FIXEDFILEINFO`` straight from a PE file in pure
Python, so it works (and is tested) on any OS; on Windows ``winplat`` asks
``GetFileVersionInfoW`` first and falls back to it.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Optional, Tuple

_NUMERIC = re.compile(r"^\s*[vV]?\s*(\d{1,6}(?:\.\d{1,6}){0,3})(?:[\s\-_+.]*([A-Za-z][0-9A-Za-z.\-]{0,31}))?\s*$")

def normalize_version(value: object) -> str:
    """A display form of a version, or ``""`` when it is missing or unreadable.

    ``"2.0.0.0"`` → ``"2.0"``, ``"2.0.0"`` → ``"2.0"``, ``"3"`` → ``"3.0"``,
    ``"1.9.3"`` → ``"1.9.3"``, ``"v2.5"`` → ``"2.5"``, ``"3.0-beta"`` → ``"3.0-beta"``,
    ``None`` / ``""`` / ``"N/A"`` / ``"0.0.0.0"`` → ``""``.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    text = str(value).strip().strip('"').strip()
    match = _NUMERIC.match(text)
    if not match:
        return ""
    numbers = [int(part) for part in match.group(1).split(".")]
    if not any(numbers):
        return ""
    while len(numbers) > 2 and numbers[-1] == 0:
        numbers.pop()
    if len(numbers) == 1:
        numbers.append(0)
    suffix = (match.group(2) or "").strip(".-").lower()
    return ".".join(str(n) for n in numbers) + (f"-{suffix}" if suffix else "")

def version_key(value: object) -> Optional[Tuple[Tuple[int, int, int, int], int, str]]:
    """Sort key (pre-release before release) or ``None`` for an unknown version."""
    text = normalize_version(value)
    if not text:
        return None
    numeric, _, suffix = text.partition("-")
    numbers = [int(part) for part in numeric.split(".")][:4]
    numbers += [0] * (4 - len(numbers))
    return (numbers[0], numbers[1], numbers[2], numbers[3]), (0 if suffix else 1), suffix

def classify(setup_version: object, installed_version: object, found: bool = True) -> str:
    """``fresh`` | ``upgrade`` | ``reinstall`` | ``downgrade``.

    An installation whose number cannot be read is offered as an update: the
    Setup's files replace whatever is there, which is exactly what an update does.
    """
    if not found:
        return "fresh"
    mine, theirs = version_key(setup_version), version_key(installed_version)
    if mine is None or theirs is None:
        return "upgrade"
    if mine > theirs:
        return "upgrade"
    if mine == theirs:
        return "reinstall"
    return "downgrade"

def from_major_minor(major: object, minor: object) -> str:
    """Inno Setup's ``MajorVersion``/``MinorVersion`` DWORDs → ``"2.0"`` (or ``""``)."""
    try:
        a, b = int(major), int(minor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if a < 0 or b < 0 or (a == 0 and b == 0):
        return ""
    return normalize_version(f"{a}.{b}")

_FIXED_SIGNATURE = 0xFEEF04BD
_VS_KEY = "VS_VERSION_INFO".encode("utf-16-le")
RT_VERSION = 16

def _fixed_info(blob: bytes, start: int) -> str:
    """Parse ``VS_FIXEDFILEINFO`` at ``start`` (its signature) → ``"a.b.c.d"`` or ``""``."""
    if start < 0 or start + 52 > len(blob):
        return ""
    signature, struc, ms, ls = struct.unpack_from("<IIII", blob, start)
    if signature != _FIXED_SIGNATURE or (struc >> 16) not in (0, 1):
        return ""
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"

def _from_version_block(blob: bytes) -> str:
    """``VS_VERSIONINFO`` block → file version, by its key and alignment."""
    key = blob.find(_VS_KEY)
    if key < 0:
        return ""
    start = key + len(_VS_KEY) + 2
    start = (start + 3) & ~3
    found = _fixed_info(blob, start)
    if found:
        return found
    sig = blob.find(struct.pack("<I", _FIXED_SIGNATURE), key)
    return _fixed_info(blob, sig) if sig >= 0 else ""

def _rva_to_offset(rva: int, sections) -> int:
    for va, vsize, raw, rawsize in sections:
        if va <= rva < va + max(vsize, rawsize):
            return raw + (rva - va)
    return -1

def pe_file_version(path: object, max_bytes: int = 64 * 1024 * 1024) -> str:
    """File version of a Windows PE (exe/dll) as ``"a.b.c.d"``, or ``""``.

    Walks the resource directory to ``RT_VERSION``; when the tree is unusual it
    falls back to scanning the resource section for ``VS_VERSION_INFO``. Never
    raises: an unreadable or foreign file simply has no version.
    """
    try:
        with open(str(path), "rb") as handle:
            head = handle.read(4096)
            if len(head) < 0x40 or head[:2] != b"MZ":
                return ""
            pe = struct.unpack_from("<I", head, 0x3C)[0]
            if pe <= 0 or pe + 24 > len(head) or head[pe:pe + 4] != b"PE\0\0":
                return ""
            sections_count, = struct.unpack_from("<H", head, pe + 6)
            opt_size, = struct.unpack_from("<H", head, pe + 20)
            opt = pe + 24
            magic, = struct.unpack_from("<H", head, opt)
            if magic == 0x10B:
                dirs = opt + 96
            elif magic == 0x20B:
                dirs = opt + 112
            else:
                return ""
            count_off = dirs - 4
            if count_off + 4 > len(head):
                return ""
            dir_count, = struct.unpack_from("<I", head, count_off)
            if dir_count < 3 or dirs + 24 > len(head):
                return ""
            res_rva, res_size = struct.unpack_from("<II", head, dirs + 16)
            table = opt + opt_size
            sections = []
            for index in range(min(sections_count, 96)):
                off = table + index * 40
                if off + 40 > len(head):
                    break
                vsize, va, rawsize, raw = struct.unpack_from("<IIII", head, off + 8)
                sections.append((va, vsize, raw, rawsize))
            if not res_rva or not res_size:
                return ""
            base = _rva_to_offset(res_rva, sections)
            if base < 0:
                return ""
            size = min(res_size, max_bytes)
            handle.seek(base)
            rsrc = handle.read(size)
    except (OSError, struct.error, ValueError):
        return ""

    def entries(offset: int):
        if offset < 0 or offset + 16 > len(rsrc):
            return []
        named, ids = struct.unpack_from("<HH", rsrc, offset + 12)
        result = []
        for i in range(min(named + ids, 4096)):
            at = offset + 16 + i * 8
            if at + 8 > len(rsrc):
                break
            name, target = struct.unpack_from("<II", rsrc, at)
            result.append((name, target))
        return result

    try:
        for name, target in entries(0):
            if name & 0x80000000 or name != RT_VERSION or not target & 0x80000000:
                continue
            for _n2, t2 in entries(target & 0x7FFFFFFF):
                level3 = [t2] if not t2 & 0x80000000 else [t for _n, t in entries(t2 & 0x7FFFFFFF)]
                for leaf in level3:
                    if leaf & 0x80000000 or leaf + 16 > len(rsrc):
                        continue
                    data_rva, data_size = struct.unpack_from("<II", rsrc, leaf)
                    offset = data_rva - res_rva
                    if 0 <= offset < len(rsrc):
                        found = _from_version_block(rsrc[offset:offset + data_size])
                        if found:
                            return found
    except struct.error:
        pass
    return _from_version_block(rsrc)

def installed_exe_version(path: Path) -> str:
    """Normalised file version of an installed exe (``""`` when there is none)."""
    return normalize_version(pe_file_version(path))
