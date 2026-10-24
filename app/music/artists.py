"""MemuMusic · round 39: artists and albums you can edit, manual order of playlists and their tracks.

* ``artist_meta`` table (created lazily): photo (file in ``covers/``, travels as a small data: URL) and a short bio,
  keyed by the lower-cased artist name. Renaming an artist rewrites every track's ``artist``/``album_artist``
  (and the file tags via ``extras.update_many``); renaming onto an existing artist merges the two.
* Artist photos online: Deezer ``/search/artist`` (``picture_xl``). iTunes has no artist pictures.
* ``playlist.reorder`` moves tracks of a plain playlist; ``playlists.reorder`` stores the sidebar order (setting ``playlist_order``).
"""
from __future__ import annotations

import base64
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

from .library import Library, SMART, norm_text, save_cover

_TABLE = ("CREATE TABLE IF NOT EXISTS artist_meta (key TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
          "image TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', auto INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)")


def _key(name: str) -> str:
    return str(name or "").strip().lower()


def _ensure(lib: Library) -> None:
    if not getattr(lib, "_artist_meta_ready", False):
        lib._x(_TABLE)
        lib._artist_meta_ready = True


def _row(lib: Library, name: str) -> Optional[Dict[str, Any]]:
    _ensure(lib)
    rows = lib._q("SELECT * FROM artist_meta WHERE key=?", (_key(name),))
    return dict(rows[0]) if rows else None


def _data_url(lib: Library, file_name: str) -> Optional[str]:
    if not file_name:
        return None
    try:
        return "data:image/jpeg;base64," + base64.b64encode((lib.covers_dir / file_name).read_bytes()).decode("ascii")
    except OSError:
        return None


def meta(lib: Library, name: str) -> Dict[str, Any]:
    """``{image, bio, image_auto}`` of one artist (image: data: URL or None)."""
    row = _row(lib, name) or {}
    return {"image": _data_url(lib, row.get("image", "")), "bio": row.get("bio", ""), "image_auto": bool(row.get("auto"))}


def all_meta(lib: Library) -> Dict[str, Dict[str, Any]]:
    _ensure(lib)
    return {r["key"]: {"image": _data_url(lib, r["image"]), "bio": r["bio"]} for r in lib._q("SELECT * FROM artist_meta")}


def _tracks_of(lib: Library, name: str) -> List[str]:
    return [r["id"] for r in lib._q("SELECT id FROM tracks WHERE lower(artist)=lower(?) OR lower(album_artist)=lower(?)",
                                    (name, name))]


def _put(lib: Library, name: str, **fields: Any) -> None:
    _ensure(lib)
    row = _row(lib, name) or {"key": _key(name), "name": name, "image": "", "bio": "", "auto": 0}
    row.update(fields, name=name, updated_at=time.time())
    lib._x("INSERT OR REPLACE INTO artist_meta (key, name, image, bio, auto, updated_at) VALUES (?,?,?,?,?,?)",
           (_key(name), row["name"], row["image"], row["bio"], int(row["auto"] or 0), row["updated_at"]))


def rename(lib: Library, old: str, new: str, base: str = "") -> Dict[str, Any]:
    """All tracks of ``old`` become ``new`` (tags too). ``merged`` — ``new`` already had tracks."""
    from . import extras
    old, new = str(old or "").strip(), str(new or "").strip()[:200]
    if not new:
        raise ValueError("Имя исполнителя пустое / The artist name is empty")
    ids = _tracks_of(lib, old)
    if not ids:
        raise KeyError("Исполнитель не найден / Artist not found")
    merged = _key(new) != _key(old) and bool(_tracks_of(lib, new))
    by_artist = [r["id"] for r in lib._q("SELECT id FROM tracks WHERE lower(artist)=lower(?)", (old,))]
    by_album = [r["id"] for r in lib._q("SELECT id FROM tracks WHERE lower(album_artist)=lower(?)", (old,))]
    if by_artist:
        extras.update_many(lib, by_artist, {"artist": new}, base)
    if by_album:
        extras.update_many(lib, by_album, {"album_artist": new}, base)
    src, dst = _row(lib, old), _row(lib, new) if _key(new) != _key(old) else None
    if src:
        keep = dst or {}
        lib._x("DELETE FROM artist_meta WHERE key=?", (_key(old),))
        _put(lib, new, image=keep.get("image") or src["image"], bio=keep.get("bio") or src["bio"],
             auto=keep.get("auto", 0) if keep.get("image") else src["auto"])
        if keep.get("image") and src["image"] and src["image"] != keep["image"]:
            _unlink(lib, src["image"])
    elif dst is None and _row(lib, new):
        _put(lib, new)   # same artist, new spelling
    return {"ok": True, "name": new, "merged": merged, "tracks": len(ids)}


def _unlink(lib: Library, file_name: str) -> None:
    try:
        (lib.covers_dir / file_name).unlink()
    except OSError:
        pass


def _store_image(lib: Library, raw: bytes) -> str:
    name = f"artist-{uuid.uuid4().hex[:10]}.jpg"
    saved = save_cover(raw, lib.covers_dir / name)
    if not saved:
        raise ValueError("Это не картинка / This is not an image")
    try:
        from PIL import Image
        with Image.open(saved) as img:
            img.convert("RGB").resize((480, 480)).save(saved, "JPEG", quality=86)   # small: travels as data: URL
    except Exception:
        pass
    return name


def set_image(lib: Library, name: str, args: Dict[str, Any], client_factory=None, auto: bool = False) -> Dict[str, Any]:
    from .extras import _fetch, _image_bytes
    name = str(name or "").strip()
    if not _tracks_of(lib, name):
        raise KeyError("Исполнитель не найден / Artist not found")
    old = (_row(lib, name) or {}).get("image", "")
    file_name = ""
    if not args.get("clear"):
        raw = _image_bytes(args["data"]) if args.get("data") else _fetch(args.get("url", ""), client_factory)
        file_name = _store_image(lib, raw)
    _put(lib, name, image=file_name, auto=1 if auto or args.get("clear") else 0)   # cleared by hand → no auto photo again
    if old and old != file_name:
        _unlink(lib, old)
    return {"ok": True, "name": name, **meta(lib, name)}


def set_bio(lib: Library, name: str, bio: str) -> Dict[str, Any]:
    if not _tracks_of(lib, name):
        raise KeyError("Исполнитель не найден / Artist not found")
    _put(lib, str(name).strip(), bio=str(bio or "").strip()[:2000])
    return {"ok": True, "name": name, **meta(lib, name)}


def image_search(q: str, limit: int = 12, client_factory=None) -> List[Dict[str, Any]]:
    """Artist photos from Deezer (``picture_xl`` 1000×1000). Network; errors → []."""
    q = str(q or "").strip()
    if not q:
        return []
    from . import sources
    client = (client_factory or sources._client)()
    out: List[Dict[str, Any]] = []
    try:
        data = client.get("https://api.deezer.com/search/artist?" + urllib.parse.urlencode(
            {"q": q, "limit": max(1, min(30, int(limit or 12)))})).json()
        for r in data.get("data") or []:
            big = str(r.get("picture_xl") or r.get("picture_big") or "")
            if big and "/artist//" not in big:   # Deezer's placeholder has an empty picture hash
                out.append({"source": "deezer", "name": r.get("name") or "", "fans": int(r.get("nb_fan") or 0),
                            "thumb": str(r.get("picture_medium") or big), "url": big})
    except Exception:
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass
    return out


def image_auto(lib: Library, name: str, client_factory=None) -> Dict[str, Any]:
    """No photo yet and the setting allows it → the Deezer artist with exactly this name (once per artist)."""
    row = _row(lib, name)
    if row and (row["image"] or row["auto"]):
        return {"ok": True, "found": bool(row["image"]), **meta(lib, name)}
    if not lib.settings().get("artist_photos", True):
        return {"ok": True, "found": False, "image": None}
    hit = next((x for x in image_search(name, 5, client_factory) if norm_text(x["name"]) == norm_text(name)), None)
    if not hit:
        _put(lib, str(name).strip(), auto=1)   # do not ask Deezer again for this artist
        return {"ok": True, "found": False, "image": None}
    try:
        return {"found": True, **set_image(lib, name, {"url": hit["url"]}, client_factory, auto=True)}
    except (ValueError, RuntimeError):
        _put(lib, str(name).strip(), auto=1)
        return {"ok": True, "found": False, "image": None}


def album_update(lib: Library, ids: List[str], args: Dict[str, Any], client_factory=None, base: str = "") -> Dict[str, Any]:
    """One album (its track ids): new title / album artist / cover for every track."""
    from . import extras
    ids = [str(i) for i in (ids or [])][:2000]
    if not ids:
        raise ValueError("Не выбраны треки / No tracks selected")
    changes: Dict[str, Any] = {}
    if args.get("title") is not None:
        title = str(args["title"]).strip()[:200]
        if not title:
            raise ValueError("Название альбома пустое / The album title is empty")
        changes["album"] = title
    if args.get("album_artist") is not None:
        changes["album_artist"] = str(args["album_artist"]).strip()[:200]
    if args.get("year") is not None:
        changes["year"] = str(args["year"]).strip()[:10]
    if changes:
        extras.update_many(lib, ids, changes, base)
    cover = args.get("cover") if isinstance(args.get("cover"), dict) else None
    if cover:
        extras.set_cover(lib, ids[0], cover, client_factory, base)
        if len(ids) > 1 and not cover.get("clear"):
            extras.update_many(lib, ids, {"cover_from": ids[0]}, base)
        elif cover.get("clear"):
            for tid in ids[1:]:
                extras.set_cover(lib, tid, {"clear": True}, client_factory, base)
    return {"ok": True, "tracks": [lib.get(t, base) for t in ids]}


def reorder_playlist(lib: Library, pid: str, args: Dict[str, Any], base: str = "") -> Dict[str, Any]:
    """``{order: [ids]}`` — the whole new order; or ``{ids: [...], to: index}`` — move these tracks to ``to``
    (``0`` = top, ``-1`` = bottom; index in the list *without* the moved tracks)."""
    if pid in SMART or not lib._q("SELECT id FROM playlists WHERE id=? AND rules=''", (pid,)):
        raise ValueError("Порядок можно менять только в обычном плейлисте / Only a plain playlist has a manual order")
    current = lib._playlist_ids(pid)
    if isinstance(args.get("order"), list):
        order = [str(t) for t in args["order"]]
        order += [t for t in current if t not in set(order)]   # never lose a track on a stale client
    else:
        moving = [str(t) for t in (args.get("ids") or []) if str(t) in set(current)]
        rest = [t for t in current if t not in set(moving)]
        to = int(args.get("to", -1))
        to = len(rest) if to < 0 or to > len(rest) else to
        order = rest[:to] + moving + rest[to:]
    lib._write_order(pid, order)
    return lib.playlist(pid, base)


def reorder_playlists(lib: Library, ids: List[str]) -> Dict[str, Any]:
    """Sidebar / grid order of plain playlists (setting ``playlist_order``, synced with the Mini App)."""
    clean = [str(i) for i in (ids or []) if str(i).startswith("pl-")][:200]
    lib.set_settings({"playlist_order": clean})
    return {"ok": True, "order": clean}
