"""Round 40 (r40-files-max): file metadata for the «Details» tab of Properties (``GET|POST /api/x40/meta``).

Stdlib + Pillow only (pypdf / mutagen used when present): pictures (size, DPI, EXIF: date taken, camera, lens,
exposure, ISO, focal length, GPS), PDF (pages, title, author), Office/ODF (title, author, pages/words/slides/sheets),
audio (tags + duration through mutagen), MP4/MOV/M4A duration from the ``mvhd`` box, archives (files, unpacked size),
text (lines, encoding). Every reader is bounded in time and size; a failure just leaves fields out.
"""
from __future__ import annotations

import os
import re
import struct
import zipfile
from typing import Any, Dict

from .fsops import FsError, clean_path

MAX_TEXT = 8 * 1024 * 1024

def _ratio(value: Any) -> float:
    try:
        return float(value[0]) / float(value[1]) if isinstance(value, tuple) else float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0

def _image(path: str, out: Dict[str, Any]) -> None:
    from PIL import Image
    with Image.open(path) as img:
        out["dimensions"] = f"{img.width} × {img.height} px"
        out["mode"] = img.mode
        dpi = img.info.get("dpi")
        if dpi and _ratio(dpi[0]):
            out["dpi"] = f"{round(_ratio(dpi[0]))} dpi"
        frames = getattr(img, "n_frames", 1)
        if frames and frames > 1:
            out["frames"] = int(frames)
        exif = img.getexif()
        sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        taken = sub.get(36867) or exif.get(306)
        if taken:
            out["taken"] = str(taken).replace(":", "-", 2)[:19]
        camera = " ".join(str(exif.get(k) or "").strip("\x00 ") for k in (271, 272)).strip()
        if camera:
            out["camera"] = camera[:80]
        if sub.get(42036):
            out["lens"] = str(sub.get(42036)).strip("\x00 ")[:80]
        exposure = _ratio(sub.get(33434))
        if exposure:
            out["exposure"] = f"1/{round(1 / exposure)} s" if exposure < 1 else f"{exposure:g} s"
        if sub.get(34855):
            out["iso"] = int(sub.get(34855)) if not isinstance(sub.get(34855), tuple) else int(sub.get(34855)[0])
        focal = _ratio(sub.get(37386))
        if focal:
            out["focal"] = f"{focal:g} mm"
        if exif.get(305):
            out["software"] = str(exif.get(305)).strip("\x00 ")[:60]
        gps = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
        if gps.get(2) and gps.get(4):
            def deg(v: Any, ref: Any) -> float:
                d = _ratio(v[0]) + _ratio(v[1]) / 60 + _ratio(v[2]) / 3600
                return -d if str(ref).upper() in ("S", "W") else d
            out["gps"] = f"{deg(gps[2], gps.get(1)):.5f}, {deg(gps[4], gps.get(3)):.5f}"

def _pdf(path: str, out: Dict[str, Any]) -> None:
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - optional
        with open(path, "rb") as handle:
            head = handle.read(4 * 1024 * 1024)
        pages = len(re.findall(rb"/Type\s*/Page(?!s)", head))
        if pages:
            out["pages"] = pages
        return
    reader = PdfReader(path)
    out["pages"] = len(reader.pages)
    info = reader.metadata or {}
    for key, name in (("/Title", "title"), ("/Author", "author"), ("/Subject", "subject"), ("/Producer", "software")):
        value = str(info.get(key) or "").strip()
        if value:
            out[name] = value[:160]

def _office(path: str, ext: str, out: Dict[str, Any]) -> None:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        core = zf.read("docProps/core.xml")[:65536].decode("utf-8", "replace") if "docProps/core.xml" in names else ""
        app = zf.read("docProps/app.xml")[:65536].decode("utf-8", "replace") if "docProps/app.xml" in names else ""
        meta = zf.read("meta.xml")[:65536].decode("utf-8", "replace") if "meta.xml" in names else ""
        if ext == ".xlsx" and "xl/workbook.xml" in names:
            out["sheets"] = len(re.findall(r"<sheet\b", zf.read("xl/workbook.xml")[:262144].decode("utf-8", "replace")))
        if ext == ".pptx":
            slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
            if slides:
                out["slides"] = len(slides)
    def tag(text: str, name: str) -> str:
        match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", text, re.S)
        return re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else ""
    for name, keys in (("title", ("dc:title",)), ("author", ("dc:creator", "meta:initial-creator")), ("subject", ("dc:subject",)),
                       ("created", ("dcterms:created", "meta:creation-date")), ("modified", ("dcterms:modified", "dc:date"))):
        for key in keys:
            value = tag(core, key) or tag(meta, key)
            if value:
                out[name] = value[:160].replace("T", " ").rstrip("Z")[:19] if name in ("created", "modified") else value[:160]
                break
    for name, key in (("pages", "Pages"), ("words", "Words"), ("slides", "Slides")):
        value = tag(app, key)
        if value.isdigit() and int(value) > 0:
            out.setdefault(name, int(value))
    if tag(app, "Application"):
        out["software"] = tag(app, "Application")[:60]

def _mp4_duration(path: str) -> float:
    """``mvhd`` of an ISO-BMFF file (mp4/mov/m4a/3gp) — top level ``moov`` only, never reads the media data."""
    with open(path, "rb") as handle:
        size_total = os.fstat(handle.fileno()).st_size
        pos = 0
        for _ in range(64):
            if pos + 8 > size_total:
                return 0.0
            handle.seek(pos)
            size, kind = struct.unpack(">I4s", handle.read(8))
            header = 8
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
                header = 16
            elif size == 0:
                size = size_total - pos
            if kind == b"moov":
                data = handle.read(min(size - header, 4 * 1024 * 1024))
                at = data.find(b"mvhd")
                if at < 4:
                    return 0.0
                version = data[at + 4]
                if version == 1:
                    scale, duration = struct.unpack(">IQ", data[at + 24:at + 36])
                else:
                    scale, duration = struct.unpack(">II", data[at + 16:at + 24])
                return duration / scale if scale else 0.0
            if size < header:
                return 0.0
            pos += size
    return 0.0

def _audio(path: str, out: Dict[str, Any]) -> None:
    try:
        import mutagen
    except Exception:  # noqa: BLE001
        return
    tags = mutagen.File(path, easy=True)
    if tags is None:
        return
    info = getattr(tags, "info", None)
    if info is not None and getattr(info, "length", 0):
        out["duration"] = round(float(info.length), 1)
    if info is not None and getattr(info, "bitrate", 0):
        out["bitrate"] = int(info.bitrate)
    for key, name in (("title", "title"), ("artist", "artist"), ("album", "album"), ("date", "year"), ("genre", "genre"),
                      ("tracknumber", "track")):
        value = tags.get(key) if hasattr(tags, "get") else None
        if value:
            out[name] = str(value[0])[:120]

def meta(path: str) -> Dict[str, Any]:
    path = clean_path(path)
    if not os.path.exists(path):
        raise FsError("not_found", path)
    if os.path.isdir(path):
        return {}
    ext = os.path.splitext(path)[1].lower()
    out: Dict[str, Any] = {}
    try:
        if ext in (".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".heic", ".avif"):
            _image(path, out)
        elif ext == ".pdf":
            _pdf(path, out)
        elif ext in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
            _office(path, ext, out)
        elif ext in (".zip", ".jar", ".apk", ".epub"):
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                out["files"] = sum(1 for i in infos if not i.is_dir())
                out["unpacked"] = sum(i.file_size for i in infos)
        if ext in (".mp4", ".m4v", ".mov", ".m4a", ".3gp"):
            duration = _mp4_duration(path)
            if duration:
                out["duration"] = round(duration, 1)
        if ext in (".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".wma", ".aac", ".mp4", ".mkv", ".webm"):
            _audio(path, out)
        from .search import KIND_EXT
        if ext in KIND_EXT.get("code", set()) or ext in (".txt", ".md", ".csv", ".log", ".json", ".xml", ".ini", ".srt"):
            if os.path.getsize(path) <= MAX_TEXT:
                from .preview import read_text
                info = read_text(path, MAX_TEXT)
                if not info.get("binary"):
                    text = info.get("text", "")
                    out["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
                    out["words"] = len(text.split())
                    if info.get("encoding"):
                        out["encoding"] = str(info["encoding"]).upper()
    except Exception:  # noqa: BLE001 - damaged / unreadable file: whatever was read so far
        pass
    return out

def handle(op: str, method: str, body: Dict[str, Any], query: Dict[str, str]) -> Dict[str, Any]:
    if op == "meta":
        return {"ok": True, "meta": meta(str(body.get("path") or query.get("path") or ""))}
    raise FsError("not_found", op)
