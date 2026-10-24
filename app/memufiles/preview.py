"""Previews of MemuFiles: thumbnails (cached, generated off-thread), text heads, archive listings.

Thumbnails: Pillow (lazy import) in a small thread pool, cached as WebP/PNG in
``memufiles/thumbs/<sha1(path, size, mtime, px)>`` — a file that changes gets a
new key, old keys are pruned when the cache grows past ``MAX_CACHE``.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import tarfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from .paths import thumbs_dir

log = logging.getLogger("memufiles.preview")

THUMB_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".tga", ".ppm", ".avif", ".heic"}
MAX_CACHE = 400 * 1024 * 1024
_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.RLock()
_INFLIGHT: Dict[str, Any] = {}


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=max(2, min(4, (os.cpu_count() or 2) // 2)),
                                       thread_name_prefix="memufiles-thumb")
        return _POOL


def thumb_key(path: str, px: int) -> Tuple[str, os.stat_result]:
    st = os.stat(path)
    raw = f"{os.path.normcase(path)}|{st.st_size}|{st.st_mtime_ns}|{px}".encode("utf-8", "surrogatepass")
    return hashlib.sha1(raw).hexdigest(), st


def _render(path: str, px: int, out_path: str) -> bytes:
    from PIL import Image, ImageOps  # type: ignore
    try:
        Image.MAX_IMAGE_PIXELS = 300_000_000
    except Exception:  # noqa: BLE001
        pass
    with Image.open(path) as image:
        try:
            image.draft("RGB", (px * 2, px * 2))       # JPEG: decode at a reduced scale (much faster)
        except Exception:  # noqa: BLE001
            pass
        try:
            image.seek(0)
        except Exception:  # noqa: BLE001
            pass
        image = ImageOps.exif_transpose(image)
        image.thumbnail((px, px), Image.LANCZOS if max(image.size) < 4000 else Image.BILINEAR)
        has_alpha = image.mode in ("RGBA", "LA", "P")
        image = image.convert("RGBA" if has_alpha else "RGB")
        buffer = io.BytesIO()
        try:
            image.save(buffer, "WEBP", quality=82, method=3)
            data = buffer.getvalue()
        except Exception:  # noqa: BLE001 - Pillow without WebP
            buffer = io.BytesIO()
            image.save(buffer, "PNG" if has_alpha else "JPEG", quality=85)
            data = buffer.getvalue()
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, out_path)
    return data


def thumbnail(path: str, px: int = 256) -> Tuple[bytes, str]:
    """``(bytes, content type)`` of a cached thumbnail; raises on unsupported/broken images."""
    px = max(32, min(int(px or 256), 1024))
    key, _st = thumb_key(path, px)
    out_path = os.path.join(str(thumbs_dir()), key)
    if os.path.isfile(out_path):
        with open(out_path, "rb") as handle:
            data = handle.read()
        return data, _ctype(data)
    with _POOL_LOCK:
        future = _INFLIGHT.get(key)
        if future is None:
            future = _pool().submit(_render, path, px, out_path)
            _INFLIGHT[key] = future
    try:
        data = future.result(timeout=60)
    finally:
        with _POOL_LOCK:
            _INFLIGHT.pop(key, None)
    _maybe_prune()
    return data, _ctype(data)


def _ctype(data: bytes) -> str:
    if data[:4] == b"RIFF":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


_PRUNE_COUNTER = [0]


def _maybe_prune() -> None:
    _PRUNE_COUNTER[0] += 1
    if _PRUNE_COUNTER[0] % 200:
        return
    try:
        folder = str(thumbs_dir())
        entries = [(e.stat().st_atime, e.stat().st_size, e.path) for e in os.scandir(folder) if e.is_file()]
        total = sum(e[1] for e in entries)
        if total <= MAX_CACHE:
            return
        for _atime, size, path in sorted(entries):
            os.unlink(path)
            total -= size
            if total <= MAX_CACHE * 0.7:
                break
    except OSError:
        pass


def cache_size() -> Dict[str, int]:
    total = count = 0
    try:
        for entry in os.scandir(str(thumbs_dir())):
            if entry.is_file():
                total += entry.stat().st_size
                count += 1
    except OSError:
        pass
    return {"size": total, "count": count}


def clear_cache() -> int:
    count = 0
    for entry in os.scandir(str(thumbs_dir())):
        try:
            os.unlink(entry.path)
            count += 1
        except OSError:
            pass
    return count


# ------------------------------------------------------------------ text

def read_text(path: str, max_bytes: int = 512 * 1024) -> Dict[str, Any]:
    """The head of a text file, decoded (UTF-8 / UTF-16 BOM / cp1251), with a binary guess."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        data = handle.read(max_bytes)
    truncated = size > len(data)
    encoding = "utf-8"
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        import codecs
        encoding = "utf-32"
        text = codecs.getincrementaldecoder(encoding)(errors="replace").decode(data, final=not truncated)
    elif data.startswith(b"\xef\xbb\xbf"):
        text, encoding = data[3:].decode("utf-8", "replace"), "utf-8-sig"
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        import codecs
        encoding = "utf-16"
        text = codecs.getincrementaldecoder(encoding)(errors="replace").decode(data, final=not truncated)
    else:
        sample = data[:8192]
        if b"\x00" in sample:
            return {"binary": True, "size": size, "text": "", "encoding": "", "truncated": truncated}
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if truncated and exc.start > len(data) - 4:          # a multi-byte char cut at the end
                text = data[:exc.start].decode("utf-8", "replace")
            else:
                try:
                    text, encoding = data.decode("cp1251"), "cp1251"
                except UnicodeDecodeError:
                    text, encoding = data.decode("latin-1"), "latin-1"
    return {"binary": False, "size": size, "text": text, "encoding": encoding, "truncated": truncated,
            "lines": text.count("\n") + 1}


def text_length(path: str, limit: int = 4 * 1024 * 1024) -> Optional[int]:
    """Number of characters of a text file (None for binaries / too big / unreadable)."""
    try:
        if os.path.getsize(path) > limit:
            return None
        info = read_text(path, limit)
    except OSError:
        return None
    if info.get("binary"):
        return None
    return len(info["text"])


# ------------------------------------------------------------------ archives

def archive_list(path: str, limit: int = 3000) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    total = 0
    kind = ""
    if zipfile.is_zipfile(path):
        kind = "zip"
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            for info in infos[:limit]:
                name = info.filename
                if not (info.flag_bits & 0x800):
                    try:
                        name = name.encode("cp437").decode("utf-8")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                rows.append({"name": name, "size": info.file_size, "packed": info.compress_size,
                             "is_dir": info.is_dir()})
            total = len(infos)
    elif tarfile.is_tarfile(path):
        kind = "tar"
        with tarfile.open(path) as tf:
            for index, member in enumerate(tf):
                if index < limit:
                    rows.append({"name": member.name, "size": member.size, "packed": None, "is_dir": member.isdir()})
                total = index + 1
    else:
        from .opqueue import OpQueue
        tool = OpQueue.seven_zip()
        if not tool:
            return {"kind": "unknown", "items": [], "total": 0, "error": "no_7zip"}
        import subprocess
        kind = "7z"
        result = subprocess.run([tool, "l", "-slt", "-ba", path], capture_output=True, timeout=60,   # noqa: S603
                                creationflags=0x08000000 if os.name == "nt" else 0)
        current: Dict[str, Any] = {}
        for line in result.stdout.decode("utf-8", "replace").splitlines() + [""]:
            if not line.strip():
                if current.get("name"):
                    total += 1
                    if len(rows) < limit:
                        rows.append(current)
                current = {}
                continue
            key, _, value = line.partition(" = ")
            if key == "Path":
                current["name"] = value
            elif key == "Size":
                current["size"] = int(value or 0) if value.isdigit() else 0
            elif key == "Packed Size":
                current["packed"] = int(value) if value.isdigit() else None
            elif key == "Folder":
                current["is_dir"] = value == "+"
            elif key == "Attributes" and "D" in value[:1]:
                current["is_dir"] = True
    unpacked = sum(r.get("size") or 0 for r in rows)
    return {"kind": kind, "items": rows, "total": total, "unpacked": unpacked}
