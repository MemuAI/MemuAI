# -*- coding: utf-8 -*-
"""MemuMusic, round 38 — the «many more features» part of the library.

* Rule playlists (``rules`` = {match: all|any, conditions: [{field, op, value}], sort, limit}) evaluated to SQL.
* Genres / folders / duplicates / radio (similar tracks) / history-friendly helpers.
* Covers: from a file (base64 / data: URL), a web URL, an online search (iTunes + Deezer), a video frame, or none.
* Bulk edit, M3U/M3U8 export and import, upload of dropped files, «show in folder», re-download swap, resume position.
* Loudness: EBU R128 integrated loudness through ffmpeg in one background thread → ``gain`` (dB to the target).
Everything is synchronous (callers run it in a thread) and raises ValueError/KeyError for the API layer.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import tools
from .library import AUDIO_EXTS, Library, folder_cover, norm_text, read_tags

# --------------------------------------------------------------------------- rule playlists
RULE_FIELDS = {
    # field: (sql expression, kind)
    "title": ("title", "text"), "artist": ("artist", "text"), "album": ("album", "text"),
    "album_artist": ("album_artist", "text"), "genre": ("genre", "text"), "source": ("source", "text"),
    "year": ("CAST(NULLIF(year,'') AS INTEGER)", "number"), "duration": ("duration", "number"),
    "plays": ("plays", "number"), "liked": ("liked", "bool"), "bitrate": ("bitrate", "number"),
    "added_days": ("added_at", "days"), "played_days": ("last_played", "days"), "file_ext": ("file_ext", "text"),
}
RULE_OPS = {"text": ("contains", "not_contains", "is", "is_not", "starts"), "number": ("gt", "lt", "eq", "between"),
            "bool": ("is",), "days": ("within", "older")}
RULE_SORTS = {"added": "added_at DESC", "title": "title COLLATE NOCASE", "artist": "artist COLLATE NOCASE, album, track_no",
              "plays": "plays DESC, last_played DESC", "recent": "last_played DESC", "duration": "duration DESC",
              "year": "year DESC", "random": "RANDOM()", "album": "album COLLATE NOCASE, track_no"}


def clean_rules(rules: Any) -> Dict[str, Any]:
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except ValueError:
            raise ValueError("rules: некорректный JSON / invalid JSON")
    if not isinstance(rules, dict):
        raise ValueError("rules: ожидается объект / an object is expected")
    conditions = []
    for cond in (rules.get("conditions") or [])[:20]:
        if not isinstance(cond, dict) or cond.get("field") not in RULE_FIELDS:
            raise ValueError(f"rules: неизвестное поле / unknown field {cond.get('field') if isinstance(cond, dict) else cond}")
        kind = RULE_FIELDS[cond["field"]][1]
        op = cond.get("op") or RULE_OPS[kind][0]
        if op not in RULE_OPS[kind]:
            raise ValueError(f"rules: оператор {op} не подходит к {cond['field']} / operator does not fit the field")
        value = cond.get("value")
        if kind == "text":
            value = str(value or "")[:120]
        elif kind == "bool":
            value = value not in (False, 0, "0", "false", "", None)
        elif op == "between":
            pair = value if isinstance(value, (list, tuple)) else str(value or "").replace("–", "-").split("-")
            try:
                value = [float(pair[0]), float(pair[1])]
            except (IndexError, TypeError, ValueError):
                raise ValueError("rules: between — два числа / two numbers")
        else:
            try:
                value = float(value or 0)
            except (TypeError, ValueError):
                raise ValueError(f"rules: {cond['field']} — нужно число / a number is required")
        conditions.append({"field": cond["field"], "op": op, "value": value})
    sort = rules.get("sort") if rules.get("sort") in RULE_SORTS else "added"
    try:
        limit = max(0, min(5000, int(rules.get("limit") or 0)))
    except (TypeError, ValueError):
        limit = 0
    return {"match": "any" if rules.get("match") == "any" else "all", "conditions": conditions, "sort": sort, "limit": limit}


def rules_sql(rules: Dict[str, Any]) -> tuple:
    parts, params = [], []
    now = time.time()
    for cond in rules["conditions"]:
        expr, kind = RULE_FIELDS[cond["field"]]
        op, value = cond["op"], cond["value"]
        if kind == "text":
            low = f"lower({expr})"
            if op == "contains":
                parts.append(f"{low} LIKE ?"); params.append(f"%{value.lower()}%")
            elif op == "not_contains":
                parts.append(f"{low} NOT LIKE ?"); params.append(f"%{value.lower()}%")
            elif op == "is":
                parts.append(f"{low} = ?"); params.append(value.lower())
            elif op == "is_not":
                parts.append(f"{low} <> ?"); params.append(value.lower())
            else:
                parts.append(f"{low} LIKE ?"); params.append(f"{value.lower()}%")
        elif kind == "bool":
            parts.append(f"{expr} = ?"); params.append(1 if value else 0)
        elif kind == "days":
            stamp = now - float(value) * 86400
            parts.append(f"{expr} >= ?" if op == "within" else f"({expr} > 0 AND {expr} < ?)"); params.append(stamp)
        elif op == "between":
            parts.append(f"{expr} BETWEEN ? AND ?"); params += sorted(value)
        else:
            parts.append(f"{expr} {'>' if op == 'gt' else '<' if op == 'lt' else '='} ?"); params.append(value)
    glue = " OR " if rules["match"] == "any" else " AND "
    where = (" WHERE " + glue.join(f"({p})" for p in parts)) if parts else ""
    sql = f"SELECT id FROM tracks{where} ORDER BY {RULE_SORTS[rules['sort']]}"
    if rules["limit"]:
        sql += f" LIMIT {int(rules['limit'])}"
    return sql, params


def rule_ids(lib: Library, stored: Any) -> List[str]:
    try:
        rules = clean_rules(stored)
    except ValueError:
        return []
    sql, params = rules_sql(rules)
    return [r["id"] for r in lib._q(sql, params)]


def rules_preview(lib: Library, rules: Any, base: str = "") -> Dict[str, Any]:
    ids = rule_ids(lib, clean_rules(rules))
    return {"count": len(ids), "tracks": lib._by_ids(ids[:30], base)}


# --------------------------------------------------------------------------- genres / folders / duplicates
def split_genres(text: str) -> List[str]:
    return [g.strip() for g in re.split(r"[;/]|, ", text or "") if g.strip()]


def genres(lib: Library, base: str = "") -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for r in lib._q("SELECT id, genre, duration, cover, accent, plays FROM tracks WHERE genre<>''"):
        for name in split_genres(r["genre"]):
            item = groups.setdefault(name.lower(), {"name": name, "tracks": 0, "duration": 0.0, "plays": 0, "cover": None, "accent": ""})
            item["tracks"] += 1
            item["duration"] += r["duration"] or 0
            item["plays"] += r["plays"] or 0
            if not item["cover"] and r["cover"]:
                item["cover"], item["accent"] = lib.cover_url(r["id"], base), r["accent"]
    return sorted(groups.values(), key=lambda g: (-g["tracks"], g["name"].lower()))


def _folder_of(lib: Library, row: Dict[str, Any]) -> str:
    return str(lib.file_path(row).parent)


def folders(lib: Library) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for r in lib._q("SELECT id, file, duration FROM tracks"):
        path = _folder_of(lib, dict(r))
        item = groups.setdefault(path, {"path": path, "name": Path(path).name or path, "tracks": 0, "duration": 0.0})
        item["tracks"] += 1
        item["duration"] += r["duration"] or 0
    return sorted(groups.values(), key=lambda f: f["path"].lower())


def folder_ids(lib: Library, folder: str) -> List[str]:
    want = os.path.normcase(os.path.normpath(str(folder or "")))
    if not want or want == ".":
        return []
    out = []
    for r in lib._q("SELECT id, file FROM tracks"):
        path = os.path.normcase(os.path.normpath(_folder_of(lib, dict(r))))
        if path == want or path.startswith(want.rstrip(os.sep) + os.sep):
            out.append(r["id"])
    return out


def duplicates(lib: Library, base: str = "") -> List[Dict[str, Any]]:
    """Groups of the same song (normalised title + artist, durations within 4 s). Best file first."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for r in lib._q("SELECT * FROM tracks"):
        key = norm_text(r["title"]) + "|" + norm_text(r["artist"])
        if key.strip("|"):
            buckets.setdefault(key, []).append(dict(r))
    groups = []
    for key, rows in buckets.items():
        rows.sort(key=lambda x: x["duration"] or 0)
        cluster: List[Dict[str, Any]] = []
        for row in rows + [None]:
            if row is not None and (not cluster or abs((row["duration"] or 0) - (cluster[-1]["duration"] or 0)) <= 4
                                    or not row["duration"] or not cluster[-1]["duration"]):
                cluster.append(row)
                continue
            if len(cluster) > 1:
                cluster.sort(key=lambda x: (-(x["bitrate"] or 0), -(x["size"] or 0), -(x["plays"] or 0)))
                groups.append({"key": key, "tracks": [lib.get(x["id"], base) for x in cluster]})
            cluster = [row] if row is not None else []
    return groups


# --------------------------------------------------------------------------- radio / mix
def radio(lib: Library, track_id: str, limit: int = 30, base: str = "") -> Dict[str, Any]:
    """Similar tracks from the library: same artist, same album artist, shared genres, then close year;
    liked/played ones rank higher; a little shuffle so two radios of one seed differ."""
    seed = lib.row(track_id)
    if not seed:
        raise KeyError("Трек не найден / Track not found")
    import random
    seed_genres = {g.lower() for g in split_genres(seed["genre"])}
    try:
        seed_year = int(seed["year"][:4])
    except (TypeError, ValueError):
        seed_year = 0
    scored = []
    for r in lib._q("SELECT * FROM tracks WHERE id<>?", (track_id,)):
        score = 0.0
        if seed["artist"] and norm_text(r["artist"]) == norm_text(seed["artist"]):
            score += 5
        if seed["album_artist"] and norm_text(r["album_artist"]) == norm_text(seed["album_artist"]):
            score += 2
        shared = seed_genres & {g.lower() for g in split_genres(r["genre"])}
        score += 3 * len(shared)
        try:
            if seed_year and abs(int(r["year"][:4]) - seed_year) <= 3:
                score += 1
        except (TypeError, ValueError):
            pass
        if score <= 0:
            continue
        score += (1.5 if r["liked"] else 0) + min(2.0, (r["plays"] or 0) / 5) + random.random() * 1.5
        scored.append((score, r["id"]))
    scored.sort(reverse=True)
    ids = [track_id] + [tid for _, tid in scored[:max(1, min(200, int(limit or 30))) - 1]]
    if len(ids) < 8:   # thin library: pad with liked/most played
        for r in lib._q("SELECT id FROM tracks WHERE id NOT IN (%s) ORDER BY liked DESC, plays DESC, RANDOM() LIMIT ?"
                        % ",".join("?" * len(ids)), (*ids, 12 - len(ids))):
            ids.append(r["id"])
    query = " ".join(x for x in (seed["artist"], seed["genre"].split(";")[0] if seed["genre"] else "") if x).strip()
    return {"seed": lib.get(track_id, base), "tracks": lib._by_ids(ids, base), "online_query": query or seed["title"]}


# --------------------------------------------------------------------------- covers
def _image_bytes(data: str) -> bytes:
    text = str(data or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        raw = base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        raise ValueError("Картинка повреждена / The image is broken")
    if len(raw) < 64:
        raise ValueError("Пустая картинка / Empty image")
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("Картинка больше 20 МБ / The image is larger than 20 MB")
    return raw


def _fetch(url: str, client_factory: Optional[Callable[[], Any]] = None, limit: int = 20 * 1024 * 1024) -> bytes:
    url = str(url or "").strip()
    if not re.match(r"^https?://", url):
        raise ValueError("Нужна ссылка http(s) / An http(s) link is required")
    from . import sources
    client = (client_factory or sources._client)()
    try:
        resp = client.get(url)
        if resp.status_code >= 400:
            raise ValueError(f"HTTP {resp.status_code}")
        data = resp.content
    finally:
        try:
            client.close()
        except Exception:
            pass
    if len(data) > limit:
        raise ValueError("Файл слишком большой / The file is too large")
    return data


def _video_frame(lib: Library, row: Dict[str, Any], at: float, client_factory=None) -> bytes:
    """A frame of the track's own video (mp4/webm with a video stream) via ffmpeg, else the source video's
    thumbnail (YouTube max-res still)."""
    ff = tools.ffmpeg_path()
    path = lib.file_path(row)
    if ff and path.is_file():
        out = subprocess.run([ff, "-v", "error", "-ss", str(max(0.0, float(at or 0))), "-i", str(path), "-map", "0:v:0",
                              "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                             capture_output=True, timeout=60, **tools.popen_kwargs())
        if out.returncode == 0 and len(out.stdout) > 1000:
            return out.stdout
    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/embed/)([A-Za-z0-9_-]{11})", row.get("source_url") or "")
    if m:
        for name in ("maxresdefault", "sddefault", "hqdefault"):
            try:
                return _fetch(f"https://i.ytimg.com/vi/{m.group(1)}/{name}.jpg", client_factory)
            except ValueError:
                continue
    raise ValueError("У трека нет видео-кадра / The track has no video frame")


def set_cover(lib: Library, track_id: str, args: Dict[str, Any], client_factory=None, base: str = "") -> Dict[str, Any]:
    row = lib.row(track_id)
    if not row:
        raise KeyError("Трек не найден / Track not found")
    if args.get("clear"):
        if row.get("cover"):
            try:
                (lib.covers_dir / row["cover"]).unlink()
            except OSError:
                pass
        lib._x("UPDATE tracks SET cover='', accent='' WHERE id=?", (track_id,))
        return lib.get(track_id, base)
    if args.get("data"):
        raw = _image_bytes(args["data"])
    elif args.get("url"):
        raw = _fetch(args["url"], client_factory)
    elif args.get("video") is not None:
        raw = _video_frame(lib, row, float(args.get("video") or 0), client_factory)
    else:
        raise ValueError("Нужно data, url, video или clear / data, url, video or clear is required")
    if not lib.set_cover(track_id, raw):
        raise ValueError("Это не картинка / This is not an image")
    if args.get("write_tags", lib.settings().get("write_tags", True)):
        lib.write_tags(track_id, with_cover=True)
    return lib.get(track_id, base)


def cover_search(q: str, limit: int = 12, client_factory=None) -> List[Dict[str, Any]]:
    """Album art candidates: iTunes Search (600×600 → 1200) and Deezer search (cover_xl). Network; errors → []."""
    q = str(q or "").strip()
    if not q:
        return []
    from . import sources
    import urllib.parse
    limit = max(1, min(30, int(limit or 12)))
    out: List[Dict[str, Any]] = []
    client = (client_factory or sources._client)()
    try:
        try:
            data = client.get("https://itunes.apple.com/search?" + urllib.parse.urlencode(
                {"term": q, "media": "music", "entity": "song", "limit": limit})).json()
            for r in data.get("results") or []:
                art = str(r.get("artworkUrl100") or "")
                if art:
                    out.append({"source": "itunes", "title": r.get("trackName") or r.get("collectionName") or "",
                                "artist": r.get("artistName") or "", "album": r.get("collectionName") or "",
                                "thumb": art.replace("100x100", "300x300"), "url": art.replace("100x100bb", "1200x1200bb")})
        except Exception:
            pass
        try:
            data = client.get("https://api.deezer.com/search?" + urllib.parse.urlencode({"q": q, "limit": limit})).json()
            for r in data.get("data") or []:
                album = r.get("album") or {}
                if album.get("cover_xl"):
                    out.append({"source": "deezer", "title": r.get("title") or "", "artist": (r.get("artist") or {}).get("name", ""),
                                "album": album.get("title") or "", "thumb": album.get("cover_medium") or album["cover_xl"],
                                "url": album["cover_xl"]})
        except Exception:
            pass
    finally:
        try:
            client.close()
        except Exception:
            pass
    seen, unique = set(), []
    for item in out:   # one candidate per album art
        key = item["url"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[: limit * 2]


def set_playlist_cover(lib: Library, pid: str, args: Dict[str, Any], client_factory=None, base: str = "") -> Dict[str, Any]:
    from .library import SMART, save_cover
    if pid in SMART:
        raise ValueError("У умного плейлиста нельзя сменить обложку / A smart playlist cover cannot be changed")
    rows = lib._q("SELECT cover FROM playlists WHERE id=?", (pid,))
    if not rows:
        raise KeyError("Плейлист не найден / Playlist not found")
    old = rows[0]["cover"]
    name = ""
    if not args.get("clear"):
        raw = _image_bytes(args["data"]) if args.get("data") else _fetch(args.get("url", ""), client_factory)
        name = f"pl-{pid}-{uuid.uuid4().hex[:6]}.jpg"
        saved = save_cover(raw, lib.covers_dir / name)
        if not saved:
            raise ValueError("Это не картинка / This is not an image")
        try:
            from PIL import Image
            with Image.open(saved) as img:
                img.convert("RGB").resize((480, 480)).save(saved, "JPEG", quality=86)   # small: travels as data: URL
        except Exception:
            pass
    lib._x("UPDATE playlists SET cover=?, updated_at=? WHERE id=?", (name, time.time(), pid))
    if old and old != name:
        try:
            (lib.covers_dir / old).unlink()
        except OSError:
            pass
    return lib.playlist(pid, base)


# --------------------------------------------------------------------------- bulk edit
BULK_FIELDS = ("artist", "album", "album_artist", "year", "genre", "title", "track_no")


def update_many(lib: Library, ids: List[str], changes: Dict[str, Any], base: str = "") -> Dict[str, Any]:
    """Only the fields present (not None) change; ``""`` clears a field. Returns updated tracks."""
    ids = [str(i) for i in (ids or [])][:5000]
    if not ids:
        raise ValueError("Не выбраны треки / No tracks selected")
    fields = {k: changes[k] for k in BULK_FIELDS if k in changes and changes[k] is not None}
    write = changes.get("write_tags", lib.settings().get("write_tags", True))
    out = []
    for tid in ids:
        out.append(lib.update(tid, {**fields, "write_tags": write}, base))
    if changes.get("cover_from"):   # one cover for all (e.g. an album)
        src = lib.cover_path(str(changes["cover_from"]))
        if src:
            raw = src.read_bytes()
            for tid in ids:
                if tid != changes["cover_from"]:
                    lib.set_cover(tid, raw)
                    if write:
                        lib.write_tags(tid, with_cover=True)
            out = [lib.get(tid, base) for tid in ids]
    return {"ok": True, "tracks": out}


# --------------------------------------------------------------------------- M3U
def export_m3u(lib: Library, pid: str, path: str = "", fmt: str = "m3u8") -> Dict[str, Any]:
    pl = lib.playlist(pid)
    lines = ["#EXTM3U", f"#PLAYLIST:{pl['name']}"]
    for t in pl["tracks"]:
        row = lib.row(t["id"]) or {}
        name = " - ".join(x for x in (t["artist"], t["title"]) if x)
        lines.append(f"#EXTINF:{int(t['duration'] or 0)},{name}")
        lines.append(str(lib.file_path(row)))
    text = "\n".join(lines) + "\n"
    safe = re.sub(r'[\\/:*?"<>|]+', "_", pl["name"]).strip() or "playlist"
    filename = f"{safe}.{'m3u' if fmt == 'm3u' else 'm3u8'}"
    written = ""
    if path:
        target = Path(str(path)).expanduser()
        if not target.is_absolute():
            raise ValueError("Нужен полный путь / An absolute path is required")
        if target.is_dir() or not target.suffix:
            target = target / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8" if fmt != "m3u" else "utf-8-sig")
        written = str(target)
    return {"text": text, "filename": filename, "path": written, "count": len(pl["tracks"])}


def import_m3u(lib: Library, text: str = "", path: str = "", name: str = "") -> Dict[str, Any]:
    """Entries are matched to the library by file path, then by «Artist - Title» of #EXTINF / the file name.
    Files that exist on disk but are not in the library are imported first."""
    base_dir = None
    if path:
        source = Path(str(path)).expanduser()
        if not source.is_file():
            raise ValueError("Файл плейлиста не найден / Playlist file not found")
        raw = source.read_bytes()
        text = raw.decode("utf-8-sig", "replace") if b"\x00" not in raw[:200] else raw.decode("utf-16", "replace")
        base_dir = source.parent
        name = name or source.stem
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    if not any(lines):
        raise ValueError("Плейлист пустой / The playlist is empty")
    ids, missing, title_hint = [], [], ""
    for ln in lines:
        if not ln:
            continue
        if ln.startswith("#PLAYLIST:") and not name:
            name = ln[10:].strip()
            continue
        if ln.startswith("#EXTINF"):
            title_hint = ln.split(",", 1)[-1].strip()
            continue
        if ln.startswith("#"):
            continue
        entry = Path(ln.replace("file:///", "").replace("\\", os.sep) if os.sep == "/" else ln.replace("file:///", ""))
        if base_dir is not None and not entry.is_absolute():
            entry = base_dir / entry
        row = lib.find_by_file(entry) or _match_name(lib, title_hint or entry.stem)
        if not row and entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
            row = import_file(lib, entry)
        if row:
            ids.append(row["id"])
        else:
            missing.append(title_hint or ln)
        title_hint = ""
    pl = lib.create_playlist(name or "M3U", ids)
    return {"playlist": pl, "matched": len(ids), "missing": missing[:200]}


def _match_name(lib: Library, text: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"^\d{1,3}[\s._-]+", "", str(text or ""))
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return lib.find_similar(title.strip(), artist.strip()) or lib.find_similar(artist.strip(), title.strip())
    return lib.find_similar(text, "")


# --------------------------------------------------------------------------- files
def import_file(lib: Library, path: Path, source: str = "local") -> Dict[str, Any]:
    existing = lib.find_by_file(path)
    if existing:
        return existing
    tags = read_tags(path)
    fields = {**tags["fields"], "file": str(path), "source": source, "source_key": "file:" + str(path)}
    if not fields.get("duration"):
        fields["duration"] = tools.probe_duration(path)
    row = lib.add(fields, tags.get("cover") if tags.get("cover") is not None else folder_cover(path.parent))
    analyzer().add(row["id"])
    return row


def upload(lib: Library, name: str, data: str, base: str = "") -> Dict[str, Any]:
    """A file dropped into the window (the sandbox has no paths): saved into the download folder/«Imported»."""
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", Path(str(name or "track")).name).strip(" .") or "track"
    if Path(clean).suffix.lower() not in AUDIO_EXTS:
        raise ValueError("Это не аудиофайл / This is not an audio file")
    raw = _image_bytes(data) if data else b""
    if len(raw) < 256:
        raise ValueError("Пустой файл / Empty file")
    folder = lib.download_dir() / "Imported"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / clean
    if target.exists():
        target = folder / f"{target.stem}-{uuid.uuid4().hex[:4]}{target.suffix}"
    target.write_bytes(raw)
    row = import_file(lib, target)
    return lib.get(row["id"], base)


def _open_memufiles(path: Path) -> bool:
    try:
        from .. import memufiles_manager
        from ..memufiles import desktop as mf_desktop
        return memufiles_manager.available() and bool(mf_desktop.open_path(str(path)))
    except Exception:
        return False


def reveal(lib: Library, track_id: str, opener: Optional[Callable[[Path], bool]] = None, via: str = "auto") -> Dict[str, Any]:
    """Show the file: ``via`` auto (MemuFiles when installed, else the system manager) | memufiles | system."""
    row = lib.row(track_id)
    if not row:
        raise KeyError("Трек не найден / Track not found")
    path = lib.file_path(row)
    if not path.exists():
        raise ValueError("Файл не найден на диске / The file is missing on disk")
    opened, used = False, "system"
    if opener is not None:
        opened = bool(opener(path))
    elif os.environ.get("MEMU_TEST_NO_OPEN"):
        pass
    elif via != "system" and _open_memufiles(path):
        opened, used = True, "memufiles"
    elif via != "memufiles":
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)], **tools.popen_kwargs())
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except OSError:
            opened = False
    return {"path": str(path), "folder": str(path.parent), "opened": opened, "via": used}


def replace_track(lib: Library, old_id: str, new_id: str) -> None:
    """After a re-download: the new file takes over likes, plays, playlists, lyrics and the id of the old one."""
    old, new = lib.row(old_id), lib.row(new_id)
    if not old or not new:
        return
    with lib._lock:
        db = lib._db
        db.execute("UPDATE tracks SET liked=?, liked_at=?, plays=?, last_played=?, added_at=?, title=?, artist=?, album=?, "
                   "album_artist=?, year=?, genre=?, track_no=? WHERE id=?",
                   (old["liked"], old["liked_at"], old["plays"], old["last_played"], old["added_at"],
                    old["title"] or new["title"], old["artist"] or new["artist"], old["album"] or new["album"],
                    old["album_artist"] or new["album_artist"], old["year"] or new["year"], old["genre"] or new["genre"],
                    old["track_no"] or new["track_no"], new_id))
        db.execute("UPDATE OR IGNORE playlist_tracks SET track_id=? WHERE track_id=?", (new_id, old_id))
        db.execute("UPDATE plays SET track_id=? WHERE track_id=?", (new_id, old_id))
        db.execute("UPDATE OR IGNORE lyrics SET track_id=? WHERE track_id=?", (new_id, old_id))
        db.commit()
    lib.delete(old_id, delete_file=True)


def set_resume(lib: Library, track_id: str, position: float) -> Dict[str, Any]:
    pos = max(0.0, float(position or 0))
    if not lib._x("UPDATE tracks SET resume_at=? WHERE id=?", (pos, track_id)):
        raise KeyError("Трек не найден / Track not found")
    return {"ok": True, "id": track_id, "resume_at": pos}


# --------------------------------------------------------------------------- loudness
def measure_lufs(path: Path) -> Optional[float]:
    """Integrated loudness (EBU R128) with ffmpeg; None when ffmpeg is missing or the file is silent/broken."""
    ff = tools.ffmpeg_path()
    if not ff or not Path(path).is_file():
        return None
    try:
        out = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-filter:a", "ebur128=framelog=quiet",
                              "-f", "null", "-"], capture_output=True, timeout=300, **tools.popen_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stderr or b"").decode("utf-8", "replace")
    found = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", text)
    if not found:
        return None
    value = float(found[-1])
    return value if value > -70 else None


def store_loudness(lib: Library, track_id: str, lufs: Optional[float]) -> Optional[float]:
    if lufs is None:
        lib._x("UPDATE tracks SET lufs=NULL, gain=0 WHERE id=?", (track_id,))
        return None
    target = float(lib.settings().get("normalize_target", -14))
    gain = round(max(-18.0, min(12.0, target - lufs)), 2)
    lib._x("UPDATE tracks SET lufs=?, gain=? WHERE id=?", (round(lufs, 2), gain, track_id))
    return gain


class Analyzer:
    """One background thread, one file at a time (ffmpeg is the heavy part; the UI never waits)."""

    def __init__(self, library_factory: Optional[Callable[[], Library]] = None):
        self.library_factory = library_factory
        self.queue: List[str] = []
        self.cond = threading.Condition()
        self.thread: Optional[threading.Thread] = None
        self.done = 0
        self.failed = 0
        self.current = ""

    def _lib(self) -> Library:
        if self.library_factory:
            return self.library_factory()
        from .library import get_library
        return get_library()

    def add(self, track_id: str, force: bool = False) -> bool:
        try:
            lib = self._lib()
            if not force and not lib.settings().get("auto_analyze", True):
                return False
        except Exception:
            return False
        if not tools.ffmpeg_path():
            return False
        with self.cond:
            if track_id in self.queue or track_id == self.current:
                return False
            self.queue.append(track_id)
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="memumusic-loudness", daemon=True)
                self.thread.start()
            self.cond.notify()
        return True

    def status(self) -> Dict[str, Any]:
        return {"queued": len(self.queue), "done": self.done, "failed": self.failed, "running": bool(self.current),
                "ffmpeg": bool(tools.ffmpeg_path())}

    def _loop(self) -> None:
        while True:
            with self.cond:
                if not self.queue:
                    self.cond.wait(30)
                    if not self.queue:
                        self.current = ""
                        return
                self.current = self.queue.pop(0)
            lib = self._lib()
            row = lib.row(self.current)
            if row:
                gain = store_loudness(lib, self.current, measure_lufs(lib.file_path(row)))
                if gain is None:
                    self.failed += 1
                else:
                    self.done += 1
            with self.cond:
                self.current = ""


_ANALYZER: Optional[Analyzer] = None
_ANALYZER_LOCK = threading.Lock()


def analyzer() -> Analyzer:
    global _ANALYZER
    with _ANALYZER_LOCK:
        if _ANALYZER is None:
            _ANALYZER = Analyzer()
        return _ANALYZER


def analyze(lib: Library, ids: Optional[List[str]] = None, missing_only: bool = True) -> Dict[str, Any]:
    if ids:
        targets = [str(i) for i in ids]
    else:
        sql = "SELECT id FROM tracks" + (" WHERE lufs IS NULL" if missing_only else "")
        targets = [r["id"] for r in lib._q(sql)]
    queued = sum(1 for tid in targets if analyzer().add(tid, force=True))
    return {"queued": queued, **analyzer().status()}
