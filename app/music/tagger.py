# -*- coding: utf-8 -*-
"""Write tags and the cover INTO the audio file, so a track keeps them outside MemuMusic (Explorer, phone, other players).

Uses ``mutagen`` when it is installed (pure Python, no ffmpeg needed): MP4/M4A, MP3 (ID3), FLAC, Ogg Opus/Vorbis.
WebM/other containers are skipped unless ffmpeg is available, then they are remuxed with ``-c copy`` metadata
(no re-encode, cover not attached). Nothing here ever raises: a file that cannot be tagged plays as it is.
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from . import tools

TAG_EXTS = (".m4a", ".mp4", ".aac", ".mp3", ".flac", ".opus", ".ogg", ".oga")


def available() -> Dict[str, bool]:
    try:
        import mutagen  # noqa: F401
        has_mutagen = True
    except Exception:
        has_mutagen = False
    return {"mutagen": has_mutagen, "ffmpeg": bool(tools.ffmpeg_path())}


def _jpeg(cover: Optional[bytes], max_side: int = 1000) -> Optional[bytes]:
    """Players expect JPEG/PNG; thumbnails often come as WebP. Re-encode with Pillow when possible."""
    if not cover:
        return None
    if cover[:3] == b"\xff\xd8\xff" and len(cover) < 900_000:
        return cover
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(cover)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return cover if cover[:8] == b"\x89PNG\r\n\x1a\n" else None


def _clean(fields: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for key in ("title", "artist", "album", "album_artist", "year", "genre"):
        value = str(fields.get(key) or "").strip()
        if value:
            out[key] = value[:300]
    try:
        if int(fields.get("track_no") or 0) > 0:
            out["track_no"] = str(int(fields["track_no"]))
    except (TypeError, ValueError):
        pass
    if fields.get("source_url"):
        out["comment"] = str(fields["source_url"])[:500]
    return out


def embed(path: Path, fields: Dict[str, Any], cover: Optional[bytes] = None) -> Dict[str, Any]:
    """Returns {ok, method, error}. Safe to call for any file."""
    path = Path(path)
    tags = _clean(fields)
    if not path.is_file() or not tags and not cover:
        return {"ok": False, "method": "", "error": "nothing to write"}
    ext = path.suffix.lower()
    if ext in TAG_EXTS and available()["mutagen"]:
        try:
            _with_mutagen(path, ext, tags, _jpeg(cover))
            return {"ok": True, "method": "mutagen", "error": ""}
        except Exception as exc:   # a broken/unknown file must never break the download
            error = f"{type(exc).__name__}: {exc}"[:200]
    else:
        error = "mutagen missing" if ext in TAG_EXTS else "container not supported by mutagen"
    ffmpeg = tools.ffmpeg_path()
    if ffmpeg and ext in (".webm", ".mka", ".m4a", ".mp3", ".ogg", ".opus", ".flac"):
        try:
            _with_ffmpeg(ffmpeg, path, tags)
            return {"ok": True, "method": "ffmpeg", "error": ""}
        except Exception as exc:
            error = f"ffmpeg: {exc}"[:200]
    return {"ok": False, "method": "", "error": error}


def _with_mutagen(path: Path, ext: str, tags: Dict[str, str], cover: Optional[bytes]) -> None:
    if ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4, MP4Cover
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        mapping = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "album_artist": "aART",
                   "year": "\xa9day", "genre": "\xa9gen", "comment": "\xa9cmt"}
        for key, atom in mapping.items():
            if key in tags:
                audio.tags[atom] = [tags[key]]
        if "track_no" in tags:
            audio.tags["trkn"] = [(int(tags["track_no"]), 0)]
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
            audio.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
        audio.save()
        return
    if ext == ".mp3":
        from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
        try:
            audio = ID3(str(path))
        except ID3NoHeaderError:
            audio = ID3()
        frames = {"title": TIT2, "artist": TPE1, "album": TALB, "album_artist": TPE2, "year": TDRC, "genre": TCON,
                  "track_no": TRCK}
        for key, frame in frames.items():
            if key in tags:
                audio.setall(frame.__name__, [frame(encoding=3, text=tags[key])])
        if "comment" in tags:
            audio.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=tags["comment"])])
        if cover:
            mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
            audio.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover)])
        audio.save(str(path), v2_version=3)
        return
    # FLAC / Ogg Opus / Ogg Vorbis — Vorbis comments, cover as METADATA_BLOCK_PICTURE.
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    audio = MutagenFile(str(path))
    if audio is None:
        raise ValueError("unknown audio file")
    if audio.tags is None:
        audio.add_tags()
    mapping = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist", "year": "date",
               "genre": "genre", "track_no": "tracknumber", "comment": "comment"}
    for key, name in mapping.items():
        if key in tags:
            audio.tags[name] = [tags[key]]
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            audio.add_picture(pic)
        else:
            audio.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()


def _with_ffmpeg(ffmpeg: str, path: Path, tags: Dict[str, str]) -> None:
    names = {"title": "title", "artist": "artist", "album": "album", "album_artist": "album_artist", "year": "date",
             "genre": "genre", "track_no": "track", "comment": "comment"}
    tmp = path.with_name(path.stem + ".tagging" + path.suffix)
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy"]
    for key, value in tags.items():
        cmd += ["-metadata", f"{names[key]}={value}"]
    cmd.append(str(tmp))
    out = subprocess.run(cmd, capture_output=True, timeout=120, **tools.popen_kwargs())
    if out.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RuntimeError((out.stderr or b"").decode("utf-8", "replace")[-200:] or f"exit {out.returncode}")
    shutil.move(str(tmp), str(path))
