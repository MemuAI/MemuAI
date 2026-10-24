# -*- coding: utf-8 -*-
"""MemuMusic library: one SQLite file plus ``tracks/`` and ``covers/`` in ``MEMUAI_DATA_DIR/music``.

Imported folders stay where they are (the row keeps an absolute path); downloads land in
``tracks/`` (or the folder chosen in settings) and are stored relative to it when possible.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".webm", ".wma", ".alac", ".aiff", ".aif")
MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".webm": "audio/webm",
        ".wma": "audio/x-ms-wma", ".aiff": "audio/aiff", ".aif": "audio/aiff", ".alac": "audio/mp4"}
SMART = {
    "smart:recent": ("Недавно добавленные", "Recently added"),
    "smart:top": ("Самые слушаемые", "Most played"),
    "smart:liked": ("Любимые", "Liked"),
}
DEFAULT_SETTINGS: Dict[str, Any] = {
    "download_dir": "",          # empty -> MEMUAI_DATA_DIR/music/tracks
    "quality": "best",           # best | normal | small
    "prefer_m4a": True,          # m4a plays everywhere (iPhone Mini App too); False -> any bestaudio
    "embed_metadata": True,      # only when ffmpeg is available
    "cookies_from_browser": "",  # "" | edge | chrome | firefox | brave | opera | vivaldi — when YouTube asks to sign in
    "volume": 1.0,
    "parallel_downloads": 2,     # r37 wave 2: yt-dlp processes at once (1..4), across jobs and album/playlist items
    "embed_cover": True,         # write tags + cover into the file with mutagen (if installed; ffmpeg not needed)
    "auto_lyrics": True,         # search lyrics automatically (new tracks in the background + on open); off -> only on «Find»
    # r39-music-lyrics: enabled lyrics sources in the order of preference (lyrics.SOURCES), plain text when nothing synced
    "lyrics_sources": ["lrclib", "musixmatch", "netease", "qq", "genius", "lyricsovh"],
    "lyrics_plain_fallback": True,
    "lyrics_offset_ms": 0,       # r41-ui: general lyrics offset for every track, ms (+ = lyrics later; Bluetooth latency)
    "lyrics_sync": "smart",      # r41b: karaoke «smart» (word / estimated sung part), «line» (whole line lit), «off»
    "youtube_fallback": True,    # a failed YouTube download tries YouTube Music / YouTube / SoundCloud matches
    # r38 — every user preference lives here (synced with the Mini App), the module window only caches it.
    "smart_hidden": [],          # smart / rule playlist ids hidden from the lists (smart:recent, smart:top, smart:liked, pl-…)
    "smart_order": [],           # order of smart + rule playlists (ids); unknown ones keep their place after these
    "recent_days": 30,           # «Recently added»: tracks added within N days (0 = no limit), max 50/500 tracks
    "smart_limit": 50,           # how many tracks «Recently added» / «Most played» hold (10..500)
    "normalize": True,           # volume levelling with the measured loudness (gain dB per track)
    "normalize_target": -14,     # LUFS target (-23..-8)
    "auto_analyze": True,        # measure loudness of new tracks in the background (ffmpeg ebur128)
    "gapless": True,             # preload the next track and start it without a gap
    "crossfade": 0,              # seconds 0..12 (0 = gapless/cut)
    "resume_long": True,         # remember the position of long tracks/podcasts
    "resume_min": 20,            # minutes — what counts as long
    "seek_step": 10,             # seconds — arrow keys / mouse wheel over the progress bar
    "play_click": "double",      # double | single — which click starts a track in lists
    "auto_radio": False,         # when the queue ends, continue with similar tracks from the library
    "confirm_delete": True,      # ask before deleting from the library
    "write_tags": True,          # track edits go into the file tags too (tagger.py)
    "show_genres": True, "show_folders": True, "show_history": True,   # library tabs
    "hotkeys": {},               # action -> key combo overrides («Space», «Ctrl+ArrowRight», «Shift+N»…)
    # r39 (r39-music-lib): sorting per list and manual order, artist photos
    "sort_views": {},            # view -> "field:asc|desc" («library», «liked», «folder», «genre», «playlist», «albums»…)
    "playlist_order": [],        # manual order of plain playlists (sidebar / grid); new ones come first
    "artist_photos": True,       # artist without a photo → take it from Deezer (once per artist)
}
SETTING_CHOICES = {"play_click": ("double", "single"), "lyrics_sync": ("smart", "line", "off")}
SETTING_RANGES = {"recent_days": (0, 3650), "smart_limit": (10, 500), "normalize_target": (-23, -8), "crossfade": (0, 12),
                  "resume_min": (1, 240), "seek_step": (1, 60), "lyrics_offset_ms": (-2000, 2000)}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'local', source_url TEXT NOT NULL DEFAULT '', source_key TEXT NOT NULL DEFAULT '',
  file TEXT NOT NULL DEFAULT '', file_ext TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0,
  bitrate INTEGER NOT NULL DEFAULT 0, cover TEXT NOT NULL DEFAULT '', accent TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '', track_no INTEGER NOT NULL DEFAULT 0, genre TEXT NOT NULL DEFAULT '',
  liked INTEGER NOT NULL DEFAULT 0, liked_at REAL NOT NULL DEFAULT 0, added_at REAL NOT NULL DEFAULT 0,
  plays INTEGER NOT NULL DEFAULT 0, last_played REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tracks_source_key ON tracks(source_key);
CREATE INDEX IF NOT EXISTS tracks_file ON tracks(file);
CREATE TABLE IF NOT EXISTS plays (id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT NOT NULL, at REAL NOT NULL, seconds REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS plays_track ON plays(track_id);
CREATE TABLE IF NOT EXISTS playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS playlist_tracks (playlist_id TEXT NOT NULL, track_id TEXT NOT NULL, pos INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, track_id));
CREATE TABLE IF NOT EXISTS lyrics (track_id TEXT PRIMARY KEY, synced TEXT, plain TEXT, source TEXT NOT NULL DEFAULT '',
  fetched_at REAL NOT NULL DEFAULT 0);
"""
_EDITABLE = ("title", "artist", "album", "album_artist", "year", "track_no", "genre")


def data_root() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env) / "music"
    from ..config import DATA_DIR
    return Path(DATA_DIR) / "music"


def norm_text(value: Any) -> str:
    """Lower-case, no punctuation, no «(Official Video)» noise: the dedup/search key."""
    text = str(value or "").lower()
    text = re.sub(r"[\(\[][^\)\]]*(official|video|audio|lyrics?|clip|клип|remaster|hd|hq|4k|visualizer)[^\)\]]*[\)\]]", " ", text)
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def album_key(album: str, artist: str) -> str:
    return norm_text(artist) + "|" + norm_text(album)


def accent_of(path: Path) -> str:
    """Dominant, slightly saturated colour of a cover as ``#rrggbb`` (empty without Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        return ""
    try:
        with Image.open(path) as img:
            small = img.convert("RGB").resize((48, 48))
            quant = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
            palette = quant.getpalette() or []
            counts = sorted(quant.getcolors() or [], reverse=True)
    except Exception:
        return ""
    best, best_score = None, -1.0
    for count, index in counts:
        r, g, b = palette[index * 3: index * 3 + 3]
        hi, lo = max(r, g, b), min(r, g, b)
        sat = (hi - lo) / hi if hi else 0
        light = (hi + lo) / 510
        # Prefer vivid colours that are neither black nor white; area still matters.
        score = count * (0.25 + sat) * (1.0 - abs(light - 0.5))
        if score > best_score:
            best, best_score = (r, g, b), score
    if not best:
        return ""
    return "#%02x%02x%02x" % best


SORT_FIELDS = {"added": ("added_at", "desc"), "title": ("title COLLATE NOCASE", "asc"), "artist": ("artist COLLATE NOCASE", "asc"),
               "album": ("album COLLATE NOCASE", "asc"), "plays": ("plays", "desc"), "recent": ("last_played", "desc"),
               "duration": ("duration", "desc"), "liked": ("liked_at", "desc"), "year": ("year", "desc")}
_SORT_TAIL = {"artist": ", album COLLATE NOCASE, track_no", "album": ", track_no", "plays": ", last_played DESC"}


def sort_sql(sort: str = "added", direction: str = "") -> str:
    """ORDER BY of library.list (r39: any field with ``asc``/``desc``; default direction per field, newest first)."""
    field, _, tail_dir = str(sort or "added").partition(":")
    col, default = SORT_FIELDS.get(field, SORT_FIELDS["added"])
    if field not in SORT_FIELDS:
        field = "added"
    way = str(direction or tail_dir or default).lower()
    way = "ASC" if way == "asc" else "DESC"
    return f"{col} {way}{_SORT_TAIL.get(field, '')}, added_at DESC"


def save_cover(data: bytes, dest: Path) -> Optional[Path]:
    """Store cover bytes as a square JPEG (YouTube 16:9 thumbnails are centre-cropped)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            w, h = img.size
            if w and h and abs(w - h) / max(w, h) > 0.08:
                side = min(w, h)
                img = img.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
            if img.size[0] > 1200:
                img = img.resize((1200, 1200))
            img.save(dest, "JPEG", quality=90)
        return dest
    except ImportError:
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


class Library:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else data_root()
        self.tracks_dir = self.root / "tracks"
        self.covers_dir = self.root / "covers"
        for folder in (self.root, self.tracks_dir, self.covers_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.root / "library.sqlite"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # SQLite's built-in lower/LIKE only fold ASCII. Russian titles and
        # artists must remain searchable regardless of the letter case.
        self._db.create_function("lower", 1, lambda value: str(value).casefold() if value is not None else None,
                                 deterministic=True)
        self._db.create_function("memu_norm", 1, norm_text, deterministic=True)
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(plays)").fetchall()}
            if "device" not in cols:   # r37 wave 2: where the listen happened (pc / phone)
                self._db.execute("ALTER TABLE plays ADD COLUMN device TEXT NOT NULL DEFAULT ''")
            self._db.execute("CREATE INDEX IF NOT EXISTS plays_at ON plays(at)")
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(tracks)").fetchall()}
            for col, decl in (("gain", "REAL"), ("lufs", "REAL"), ("resume_at", "REAL NOT NULL DEFAULT 0")):
                if col not in cols:   # r38: loudness (dB to the target, measured LUFS), resume position
                    self._db.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(playlists)").fetchall()}
            if "rules" not in cols:   # r38: rule-based («smart») user playlists — JSON, empty for a plain one
                self._db.execute("ALTER TABLE playlists ADD COLUMN rules TEXT NOT NULL DEFAULT ''")
            self._db.commit()
        self._secret = self._load_secret()

    # ------------------------------------------------------------------ infra
    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _q(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(params)).fetchall()

    def _x(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._db.execute(sql, tuple(params))
            self._db.commit()
            return cur.rowcount

    def _load_secret(self) -> str:
        path = self.root / "secret.txt"
        try:
            text = path.read_text(encoding="utf-8").strip()
            if len(text) >= 32:
                return text
        except OSError:
            pass
        text = secrets.token_hex(24)
        path.write_text(text, encoding="utf-8")
        return text

    def token(self, kind: str, item_id: str) -> str:
        import hashlib
        import hmac
        return hmac.new(self._secret.encode(), f"{kind}:{item_id}".encode(), hashlib.sha256).hexdigest()[:32]

    def check_token(self, kind: str, item_id: str, token: str) -> bool:
        import hmac
        return bool(token) and hmac.compare_digest(self.token(kind, item_id), str(token))

    # ------------------------------------------------------------------ settings
    def settings(self) -> Dict[str, Any]:
        try:
            saved = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        import copy
        return {**copy.deepcopy(DEFAULT_SETTINGS), **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}

    def set_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        from ..storage import _json_transaction
        with _json_transaction(self.root / "settings.json"):
            return self._set_settings(changes)

    def _set_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        current = self.settings()
        for key, value in (changes or {}).items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key == "quality" and value not in ("best", "normal", "small"):
                raise ValueError("quality: best | normal | small")
            if key == "download_dir" and value:
                folder = Path(str(value)).expanduser()
                if not folder.is_absolute():
                    raise ValueError("Нужен полный путь к папке / An absolute folder path is required")
                folder.mkdir(parents=True, exist_ok=True)
                value = str(folder)
            if key == "cookies_from_browser" and value not in ("", "edge", "chrome", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"):
                raise ValueError("cookies_from_browser: edge | chrome | firefox | brave | opera | vivaldi")
            if key == "volume":
                value = max(0.0, min(1.0, float(value)))
            if key == "parallel_downloads":
                try:
                    value = max(1, min(4, int(value)))
                except (TypeError, ValueError):
                    raise ValueError("parallel_downloads: 1..4")
            if key in SETTING_CHOICES and value not in SETTING_CHOICES[key]:
                raise ValueError(f"{key}: " + " | ".join(SETTING_CHOICES[key]))
            if key in SETTING_RANGES:
                low, high = SETTING_RANGES[key]
                try:
                    value = max(low, min(high, int(float(value))))
                except (TypeError, ValueError):
                    raise ValueError(f"{key}: {low}..{high}")
            if isinstance(DEFAULT_SETTINGS[key], list):
                value = [str(x)[:80] for x in (value if isinstance(value, list) else [])][:200]
            if isinstance(DEFAULT_SETTINGS[key], dict):
                value = {str(k)[:40]: str(v)[:40] for k, v in (value if isinstance(value, dict) else {}).items()}
            if isinstance(DEFAULT_SETTINGS[key], bool):
                value = value not in (False, 0, "0", "false", "False", "", None)
            current[key] = value
        tmp = self.root / "settings.json.tmp"
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.root / "settings.json")
        return current

    def download_dir(self) -> Path:
        custom = self.settings().get("download_dir")
        folder = Path(custom) if custom else self.tracks_dir
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ------------------------------------------------------------------ tracks
    def file_path(self, row: Dict[str, Any]) -> Path:
        path = Path(row.get("file") or "")
        return path if path.is_absolute() else self.tracks_dir / path

    def _public(self, row: sqlite3.Row, base: str = "") -> Dict[str, Any]:
        data = dict(row)
        track_id = data["id"]
        data["liked"] = bool(data["liked"])
        data["cover"] = self.cover_url(track_id, base) if data.get("cover") else None
        data["stream"] = self.stream_url(track_id, base)
        data["album_key"] = album_key(data["album"], data["album_artist"] or data["artist"]) if data["album"] else ""
        data.pop("file", None)
        data.pop("source_key", None)
        return data

    def stream_url(self, track_id: str, base: str = "") -> str:
        return f"{base}/api/pg/music/stream/{track_id}?t={self.token('stream', track_id)}"

    def cover_url(self, track_id: str, base: str = "") -> str:
        row = self._q("SELECT cover FROM tracks WHERE id=?", (track_id,))
        stamp = ""
        if row and row[0]["cover"]:
            stamp = "&v=" + re.sub(r"\W", "", row[0]["cover"])[-8:]
        return f"{base}/api/pg/music/cover/{track_id}?t={self.token('cover', track_id)}{stamp}"

    def row(self, track_id: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM tracks WHERE id=?", (str(track_id),))
        return dict(rows[0]) if rows else None

    def get(self, track_id: str, base: str = "") -> Dict[str, Any]:
        rows = self._q("SELECT * FROM tracks WHERE id=?", (str(track_id),))
        if not rows:
            raise KeyError("Трек не найден / Track not found")
        return self._public(rows[0], base)

    def find_by_source(self, source_key: str) -> Optional[Dict[str, Any]]:
        if not source_key:
            return None
        rows = self._q("SELECT * FROM tracks WHERE source_key=?", (source_key,))
        for row in rows:
            if self.file_path(dict(row)).is_file():
                return dict(row)
        return None

    def find_by_file(self, path: Path) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM tracks WHERE file=?", (str(path),))
        return dict(rows[0]) if rows else None

    def find_similar(self, title: str, artist: str, duration: float = 0) -> Optional[Dict[str, Any]]:
        """Dedup across sources: same normalised title + artist (and close duration if both known)."""
        key_t, key_a = norm_text(title), norm_text(artist)
        if not key_t:
            return None
        for row in self._q("SELECT * FROM tracks WHERE memu_norm(title)=?", (key_t,)):
            data = dict(row)
            if norm_text(data["title"]) != key_t or (key_a and norm_text(data["artist"]) != key_a):
                continue
            if duration and data["duration"] and abs(duration - data["duration"]) > 8:
                continue
            if self.file_path(data).is_file():
                return data
        return None

    def add(self, fields: Dict[str, Any], cover_bytes: Optional[bytes] = None) -> Dict[str, Any]:
        track_id = uuid.uuid4().hex[:12]
        file_path = Path(fields["file"])
        stored = file_path
        try:
            stored = file_path.relative_to(self.tracks_dir)
        except ValueError:
            pass
        size = file_path.stat().st_size if file_path.is_file() else 0
        row = {
            "id": track_id, "title": str(fields.get("title") or file_path.stem)[:300],
            "artist": str(fields.get("artist") or "")[:300], "album": str(fields.get("album") or "")[:300],
            "album_artist": str(fields.get("album_artist") or "")[:300],
            "duration": float(fields.get("duration") or 0), "source": str(fields.get("source") or "local"),
            "source_url": str(fields.get("source_url") or ""), "source_key": str(fields.get("source_key") or ""),
            "file": str(stored), "file_ext": file_path.suffix.lower().lstrip("."), "size": size,
            "bitrate": int(fields.get("bitrate") or 0), "cover": "", "accent": "",
            "year": str(fields.get("year") or "")[:10], "track_no": int(fields.get("track_no") or 0),
            "genre": str(fields.get("genre") or "")[:80], "liked": 0, "liked_at": 0,
            "added_at": float(fields.get("added_at") or time.time()), "plays": 0, "last_played": 0,
        }
        cols = ",".join(row)
        self._x(f"INSERT INTO tracks ({cols}) VALUES ({','.join('?' * len(row))})", row.values())
        if cover_bytes:
            self.set_cover(track_id, cover_bytes)
        return self.row(track_id) or row

    def set_cover(self, track_id: str, data: bytes) -> bool:
        name = f"{track_id}-{uuid.uuid4().hex[:6]}.jpg"
        saved = save_cover(data, self.covers_dir / name)
        if not saved:
            return False
        old = self.row(track_id) or {}
        self._x("UPDATE tracks SET cover=?, accent=? WHERE id=?", (name, accent_of(saved), track_id))
        if old.get("cover") and old["cover"] != name:
            try:
                (self.covers_dir / old["cover"]).unlink()
            except OSError:
                pass
        return True

    def cover_path(self, track_id: str) -> Optional[Path]:
        row = self.row(track_id)
        if not row or not row.get("cover"):
            return None
        path = self.covers_dir / row["cover"]
        return path if path.is_file() else None

    def update(self, track_id: str, changes: Dict[str, Any], base: str = "") -> Dict[str, Any]:
        if not self.row(track_id):
            raise KeyError("Трек не найден / Track not found")
        sets, values = [], []
        for key in _EDITABLE:
            if key in changes and changes[key] is not None:
                sets.append(f"{key}=?")
                values.append(int(changes[key] or 0) if key == "track_no" else str(changes[key])[:300])
        accent = str(changes.get("accent") or "") if "accent" in changes else None
        if accent is not None and (accent == "" or re.fullmatch(r"#[0-9a-fA-F]{6}", accent)):
            # r38 (mod-integ): the colour of the track (cards, panels, the Quick Access ring); not a file tag.
            self._x("UPDATE tracks SET accent=? WHERE id=?", (accent.lower(), track_id))
        if sets:
            self._x(f"UPDATE tracks SET {', '.join(sets)} WHERE id=?", [*values, track_id])
            if changes.get("write_tags", self.settings().get("write_tags", True)):
                self.write_tags(track_id)
        return self.get(track_id, base)

    def write_tags(self, track_id: str, with_cover: bool = False) -> Dict[str, Any]:
        """r38: edits go into the file too (mutagen/ffmpeg via tagger.py); never raises."""
        row = self.row(track_id)
        if not row:
            return {"ok": False, "error": "missing"}
        from . import tagger
        cover = None
        if with_cover:
            path = self.cover_path(track_id)
            cover = path.read_bytes() if path else None
        return tagger.embed(self.file_path(row), row, cover)

    def delete(self, track_id: str, delete_file: bool = True) -> Dict[str, Any]:
        row = self.row(track_id)
        if not row:
            raise KeyError("Трек не найден / Track not found")
        removed_file = False
        path = self.file_path(row)
        # Only our own downloads are deleted from disk; an imported folder stays untouched.
        resolved = path.resolve()
        own = self.tracks_dir.resolve() in resolved.parents or self.download_dir().resolve() in resolved.parents
        # Two entries can reference one physical file (different sources,
        # metadata or a repeated import). Removing one must keep the other playable.
        shared = any(r["id"] != track_id and self.file_path(dict(r)).resolve() == resolved
                     for r in self._q("SELECT id, file FROM tracks"))
        if delete_file and own and not shared and path.is_file():
            try:
                path.unlink()
                removed_file = True
            except OSError:
                pass
        if row.get("cover"):
            try:
                (self.covers_dir / row["cover"]).unlink()
            except OSError:
                pass
        with self._lock, self._db:
            for sql in ("DELETE FROM tracks WHERE id=?", "DELETE FROM playlist_tracks WHERE track_id=?",
                        "DELETE FROM plays WHERE track_id=?", "DELETE FROM lyrics WHERE track_id=?"):
                self._db.execute(sql, (track_id,))
        return {"ok": True, "id": track_id, "file_removed": removed_file}

    def like(self, track_id: str, liked: bool) -> Dict[str, Any]:
        if not self._x("UPDATE tracks SET liked=?, liked_at=? WHERE id=?", (1 if liked else 0, time.time() if liked else 0, track_id)):
            raise KeyError("Трек не найден / Track not found")
        return {"ok": True, "id": track_id, "liked": bool(liked)}

    def played(self, track_id: str, seconds: float, device: str = "", at: Optional[float] = None) -> Dict[str, Any]:
        """A finished (or ≥30 s / half) listen counts as a play; shorter ones only go to history.
        ``at`` (epoch seconds) lets a phone report a listen it made offline; it must not be in the future."""
        row = self.row(track_id)
        if not row:
            raise KeyError("Трек не найден / Track not found")
        seconds = max(0.0, min(float(seconds or 0), 24 * 3600))
        counted = seconds >= 30 or (row["duration"] and seconds >= row["duration"] * 0.5)
        now = time.time()
        try:
            stamp = float(at) if at else now
        except (TypeError, ValueError):
            stamp = now
        if stamp > 1e11:
            stamp /= 1000.0
        if not (now - 365 * 86400 < stamp <= now + 60):
            stamp = now
        now = min(stamp, now)
        device = re.sub(r"[^a-z0-9_-]", "", str(device or "").lower())[:20]
        self._x("INSERT INTO plays (track_id, at, seconds, device) VALUES (?,?,?,?)", (track_id, now, seconds, device))
        if counted:
            self._x("UPDATE tracks SET plays=plays+1, last_played=MAX(last_played, ?) WHERE id=?", (now, track_id))
        return {"ok": True, "counted": bool(counted)}

    def list(self, q: str = "", sort: str = "added", filter: str = "", limit: int = 200, offset: int = 0,
             base: str = "", dir: str = "") -> Dict[str, Any]:
        where, params = [], []
        for word in str(q or "").split()[:6]:
            where.append("(lower(title) LIKE ? OR lower(artist) LIKE ? OR lower(album) LIKE ? OR lower(album_artist) LIKE ? OR lower(genre) LIKE ? OR year LIKE ?)")
            params += [f"%{word.casefold()}%"] * 6
        flt = str(filter or "")
        if flt == "liked":
            where.append("liked=1")
        elif flt == "downloaded":
            where.append("source NOT IN ('local')")
        elif flt == "local":
            where.append("source='local'")
        elif flt.startswith("source:"):
            where.append("source=?")
            params.append(flt[7:])
        elif flt.startswith("genre:"):   # r38: «Жанры» — one genre of the tag (case-insensitive, «Rock; Pop» counts for both)
            where.append("(';' || lower(replace(replace(replace(replace(genre, ' / ', ';'), '; ', ';'), ', ', ';'), '/', ';')) || ';') LIKE ?")
            params.append(f"%;{flt[6:].strip().lower()};%")
        elif flt.startswith("folder:"):  # r38: «Папки» — files of an imported/download folder (and its subfolders)
            from .extras import folder_ids
            ids = folder_ids(self, flt[7:])
            where.append(f"id IN ({','.join('?' * len(ids))})" if ids else "0")
            params += ids
        elif flt:
            where.append("source=?")
            params.append(flt)
        order = sort_sql(sort, dir)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._q(f"SELECT COUNT(*) AS n FROM tracks{clause}", params)[0]["n"]
        limit = max(1, min(5000, int(limit or 200)))
        rows = self._q(f"SELECT * FROM tracks{clause} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, max(0, int(offset or 0))])
        return {"tracks": [self._public(r, base) for r in rows], "total": total}

    def _by_ids(self, ids: List[str], base: str = "") -> List[Dict[str, Any]]:
        if not ids:
            return []
        rows = {r["id"]: r for r in self._q(f"SELECT * FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)}
        return [self._public(rows[i], base) for i in ids if i in rows]

    # ------------------------------------------------------------------ stats / artists / albums
    def stats(self, base: str = "", days: int = 30) -> Dict[str, Any]:
        agg = self._q("SELECT COUNT(*) AS n, COALESCE(SUM(duration),0) AS d, COUNT(DISTINCT lower(artist)) AS a FROM tracks")[0]
        listened = self._q("SELECT COALESCE(SUM(seconds),0) AS s, COUNT(*) AS n FROM plays")[0]
        top_artists = [{"name": r["artist"], "plays": r["p"], "tracks": r["n"]} for r in self._q(
            "SELECT artist, SUM(plays) AS p, COUNT(*) AS n FROM tracks WHERE artist<>'' GROUP BY lower(artist) "
            "ORDER BY p DESC, n DESC LIMIT 10")]
        top = [r["id"] for r in self._q("SELECT id FROM tracks WHERE plays>0 ORDER BY plays DESC, last_played DESC LIMIT 10")]
        recent = [r["track_id"] for r in self._q(
            "SELECT track_id, MAX(at) AS m FROM plays GROUP BY track_id ORDER BY m DESC LIMIT 12")]
        week = time.time() - 7 * 86400
        by_day = {}
        for r in self._q("SELECT at, seconds FROM plays WHERE at>=?", (week,)):
            day = time.strftime("%Y-%m-%d", time.localtime(r["at"]))
            by_day[day] = by_day.get(day, 0) + r["seconds"] / 60
        return {"tracks": agg["n"], "minutes": round(agg["d"] / 60, 1), "artists": agg["a"],
                "listened_minutes": round(listened["s"] / 60, 1), "listens": listened["n"],
                "liked": self._q("SELECT COUNT(*) AS n FROM tracks WHERE liked=1")[0]["n"],
                "top_artists": top_artists, "top_tracks": self._by_ids(top, base), "recent": self._by_ids(recent, base),
                "week": [{"day": k, "minutes": round(v, 1)} for k, v in sorted(by_day.items())],
                **self.aggregates(days)}

    def aggregates(self, days: int = 30) -> Dict[str, Any]:
        """Chart data (local time): ``by_day`` — every day of the last ``days`` (zeros included, oldest first),
        ``by_hour`` — 24 buckets and ``by_weekday`` — 7 buckets (0 = Monday) over the same window."""
        days = max(1, min(366, int(days or 30)))
        today = time.localtime()
        start_local = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - (days - 1), 0, 0, 0, 0, 0, -1))
        keys = [time.strftime("%Y-%m-%d", time.localtime(time.mktime(
            (today.tm_year, today.tm_mon, today.tm_mday - (days - 1) + i, 12, 0, 0, 0, 0, -1)))) for i in range(days)]
        day = {k: {"day": k, "minutes": 0.0, "listens": 0, "plays": 0} for k in keys}
        hour = [{"hour": h, "minutes": 0.0, "listens": 0} for h in range(24)]
        weekday = [{"weekday": d, "minutes": 0.0, "listens": 0} for d in range(7)]
        devices: Dict[str, float] = {}
        for r in self._q("SELECT p.at, p.seconds, p.device, t.duration FROM plays p LEFT JOIN tracks t ON t.id=p.track_id "
                         "WHERE p.at>=?", (start_local,)):
            local = time.localtime(r["at"])
            minutes = (r["seconds"] or 0) / 60
            counted = (r["seconds"] or 0) >= 30 or (r["duration"] and (r["seconds"] or 0) >= r["duration"] * 0.5)
            bucket = day.get(time.strftime("%Y-%m-%d", local))
            if bucket:
                bucket["minutes"] += minutes
                bucket["listens"] += 1
                bucket["plays"] += 1 if counted else 0
            hour[local.tm_hour]["minutes"] += minutes
            hour[local.tm_hour]["listens"] += 1
            weekday[local.tm_wday]["minutes"] += minutes
            weekday[local.tm_wday]["listens"] += 1
            key = r["device"] or "pc"
            devices[key] = devices.get(key, 0.0) + minutes
        for row in (*day.values(), *hour, *weekday):
            row["minutes"] = round(row["minutes"], 1)
        return {"days": days, "by_day": list(day.values()), "by_hour": hour, "by_weekday": weekday,
                "by_device": [{"device": k, "minutes": round(v, 1)} for k, v in sorted(devices.items(), key=lambda x: -x[1])]}

    def history(self, limit: int = 100, base: str = "", since: Optional[float] = None,
                until: Optional[float] = None) -> List[Dict[str, Any]]:
        """Listens, newest first. ``since``/``until`` — epoch seconds (or milliseconds) bounds."""
        where, params = [], []
        for op, value in ((">=", since), ("<", until)):
            try:
                stamp = float(value) if value not in (None, "") else 0.0
            except (TypeError, ValueError):
                raise ValueError("since/until: секунды эпохи / epoch seconds")
            if stamp:
                where.append(f"at{op}?")
                params.append(stamp / 1000.0 if stamp > 1e11 else stamp)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._q(f"SELECT track_id, at, seconds, device FROM plays{clause} ORDER BY at DESC LIMIT ?",
                       (*params, max(1, min(5000, int(limit or 100)))))
        tracks = {t["id"]: t for t in self._by_ids(list({r["track_id"] for r in rows}), base)}
        return [{"at": r["at"], "seconds": r["seconds"], "device": r["device"] or "pc", "track": tracks[r["track_id"]]}
                for r in rows if r["track_id"] in tracks]

    def artists(self, base: str = "") -> List[Dict[str, Any]]:
        rows = self._q("SELECT artist, COUNT(*) AS n, SUM(plays) AS p, SUM(duration) AS d, MAX(added_at) AS a, "
                       "(SELECT id FROM tracks t2 WHERE lower(t2.artist)=lower(tracks.artist) AND t2.cover<>'' LIMIT 1) AS cid "
                       "FROM tracks WHERE artist<>'' GROUP BY lower(artist) ORDER BY artist COLLATE NOCASE")
        from .artists import all_meta   # r39: own photo / bio
        meta = all_meta(self)
        return [{"name": r["artist"], "tracks": r["n"], "plays": r["p"] or 0, "duration": r["d"] or 0, "added_at": r["a"] or 0,
                 "image": (meta.get(r["artist"].lower()) or {}).get("image"),
                 "cover": self.cover_url(r["cid"], base) if r["cid"] else None,
                 "accent": (self.row(r["cid"]) or {}).get("accent", "") if r["cid"] else ""} for r in rows]

    def artist(self, name: str, base: str = "") -> Dict[str, Any]:
        rows = self._q("SELECT * FROM tracks WHERE lower(artist)=lower(?) ORDER BY album COLLATE NOCASE, track_no, title",
                       (str(name or ""),))
        if not rows:
            raise KeyError("Исполнитель не найден / Artist not found")
        tracks = [self._public(r, base) for r in rows]
        albums = [a for a in self.albums(base) if norm_text(a["artist"]) == norm_text(name)]
        from .artists import meta   # r39: own photo / bio
        return {"name": rows[0]["artist"], "tracks": tracks, "albums": albums, **meta(self, rows[0]["artist"]),
                "plays": sum(t["plays"] for t in tracks), "duration": sum(t["duration"] for t in tracks),
                "cover": next((t["cover"] for t in tracks if t["cover"]), None),
                "accent": next((t["accent"] for t in tracks if t["accent"]), "")}

    def albums(self, base: str = "") -> List[Dict[str, Any]]:
        groups: Dict[str, Dict[str, Any]] = {}
        for r in self._q("SELECT id, album, album_artist, artist, year, duration, cover, accent, added_at FROM tracks WHERE album<>''"):
            artist = r["album_artist"] or r["artist"]
            key = album_key(r["album"], artist)
            item = groups.setdefault(key, {"key": key, "title": r["album"], "artist": artist, "year": r["year"],
                                           "tracks": 0, "duration": 0.0, "cover": None, "accent": "", "added_at": 0})
            item["tracks"] += 1
            item["duration"] += r["duration"] or 0
            item["added_at"] = max(item["added_at"], r["added_at"])
            if not item["cover"] and r["cover"]:
                item["cover"], item["accent"] = self.cover_url(r["id"], base), r["accent"]
        return sorted(groups.values(), key=lambda a: -a["added_at"])

    def album(self, key: str, base: str = "") -> Dict[str, Any]:
        info = next((a for a in self.albums(base) if a["key"] == key), None)
        if not info:
            raise KeyError("Альбом не найден / Album not found")
        rows = [r for r in self._q("SELECT * FROM tracks WHERE album<>'' ORDER BY track_no, title")
                if album_key(r["album"], r["album_artist"] or r["artist"]) == key]
        return {**info, "items": [self._public(r, base) for r in rows]}

    # ------------------------------------------------------------------ playlists
    def _smart_ids(self, pid: str) -> List[str]:
        cfg = self.settings()
        limit = int(cfg.get("smart_limit") or 50)
        if pid == "smart:recent":
            days = int(cfg.get("recent_days") or 0)
            since = time.time() - days * 86400 if days else 0
            return [r["id"] for r in self._q("SELECT id FROM tracks WHERE added_at>=? ORDER BY added_at DESC LIMIT ?", (since, limit))]
        if pid == "smart:top":
            return [r["id"] for r in self._q("SELECT id FROM tracks WHERE plays>0 ORDER BY plays DESC, last_played DESC LIMIT ?", (limit,))]
        return [r["id"] for r in self._q("SELECT id FROM tracks WHERE liked=1 ORDER BY liked_at DESC")]

    def _playlist_ids(self, pid: str) -> List[str]:
        if pid in SMART:
            return self._smart_ids(pid)
        rules = self._q("SELECT rules FROM playlists WHERE id=?", (pid,))
        if rules and rules[0]["rules"]:
            from .extras import rule_ids
            return rule_ids(self, rules[0]["rules"])
        return [r["track_id"] for r in self._q("SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY pos", (pid,))]

    def _summary(self, pid: str, name: str, smart: bool, lang_en: bool, base: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        ids = self._playlist_ids(pid)
        tracks = self._by_ids(ids[:4], base)
        dur = 0.0
        if ids:
            dur = self._q(f"SELECT COALESCE(SUM(duration),0) AS d FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)[0]["d"]
        covers = [t["cover"] for t in tracks if t["cover"]]
        own = extra.pop("own_cover", "")
        return {"id": pid, "name": name, "smart": smart, "count": len(ids), "duration": dur,
                "cover": self.playlist_cover(own) or (covers[0] if covers else None), "covers": covers[:4],
                "own_cover": bool(own), "track_ids": ids[:4],
                "accent": next((t["accent"] for t in tracks if t["accent"]), ""), **extra}

    def playlist_cover(self, name: str) -> Optional[str]:
        """A playlist's own cover as a small data: URL (works in the sandboxed module and the Mini App alike)."""
        if not name:
            return None
        path = self.covers_dir / name
        try:
            import base64
            return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return None

    def playlists(self, base: str = "", lang: str = "ru", include_hidden: bool = False) -> List[Dict[str, Any]]:
        """Smart + rule playlists first (user order from ``smart_order``), then plain ones by last change.
        Ids in ``smart_hidden`` are left out unless ``include_hidden`` (then they carry ``hidden: true``)."""
        en = lang == "en"
        cfg = self.settings()
        hidden, order = set(cfg.get("smart_hidden") or []), list(cfg.get("smart_order") or [])
        auto = [self._summary(pid, names[1] if en else names[0], True, en, base,
                              {"name_ru": names[0], "name_en": names[1], "rules": None}) for pid, names in SMART.items()]
        plain = []
        manual = {pid: i for i, pid in enumerate(cfg.get("playlist_order") or [])}   # r39: user order, new ones first
        rows = sorted(self._q("SELECT * FROM playlists ORDER BY updated_at DESC"), key=lambda r: (r["id"] in manual, manual.get(r["id"], 0)))
        for r in rows:
            rules = json.loads(r["rules"]) if r["rules"] else None
            row = self._summary(r["id"], r["name"], False, en, base, {"description": r["description"], "updated_at": r["updated_at"],
                                                                      "rules": rules, "own_cover": r["cover"]})
            (auto if rules else plain).append(row)
        rank = {pid: i for i, pid in enumerate(order)}
        auto.sort(key=lambda p: rank.get(p["id"], len(rank)))
        out = []
        for row in auto + plain:
            row["hidden"] = row["id"] in hidden
            if include_hidden or not row["hidden"]:
                out.append(row)
        return out

    def playlist(self, pid: str, base: str = "", lang: str = "ru") -> Dict[str, Any]:
        pid = str(pid or "")
        if pid in SMART:
            names = SMART[pid]
            head = {"id": pid, "name": names[1] if lang == "en" else names[0], "smart": True, "description": ""}
        else:
            rows = self._q("SELECT * FROM playlists WHERE id=?", (pid,))
            if not rows:
                raise KeyError("Плейлист не найден / Playlist not found")
            head = {"id": pid, "name": rows[0]["name"], "smart": False, "description": rows[0]["description"],
                    "created_at": rows[0]["created_at"], "updated_at": rows[0]["updated_at"],
                    "rules": json.loads(rows[0]["rules"]) if rows[0]["rules"] else None, "own_cover": bool(rows[0]["cover"])}
        tracks = self._by_ids(self._playlist_ids(pid), base)
        covers = [t["cover"] for t in tracks if t["cover"]]
        own = self.playlist_cover(rows[0]["cover"]) if pid not in SMART else None
        return {**head, "tracks": tracks, "count": len(tracks), "duration": sum(t["duration"] for t in tracks),
                "cover": own or (covers[0] if covers else None), "covers": covers[:4],
                "accent": next((t["accent"] for t in tracks if t["accent"]), "")}

    def _write_order(self, pid: str, ids: List[str]) -> None:
        seen, clean = set(), []
        existing = {r["id"] for r in self._q(f"SELECT id FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
        for tid in ids:
            if tid in existing and tid not in seen:
                seen.add(tid)
                clean.append(tid)
        with self._lock:
            self._db.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
            self._db.executemany("INSERT INTO playlist_tracks (playlist_id, track_id, pos) VALUES (?,?,?)",
                                 [(pid, tid, i) for i, tid in enumerate(clean)])
            self._db.execute("UPDATE playlists SET updated_at=? WHERE id=?", (time.time(), pid))
            self._db.commit()

    def create_playlist(self, name: str, track_ids: Optional[List[str]] = None, description: str = "",
                        rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        name = str(name or "").strip()[:120]
        if not name:
            raise ValueError("Название плейлиста пустое / The playlist name is empty")
        pid = "pl-" + uuid.uuid4().hex[:10]
        now = time.time()
        from .extras import clean_rules
        stored = json.dumps(clean_rules(rules), ensure_ascii=False) if rules else ""
        self._x("INSERT INTO playlists (id, name, description, created_at, updated_at, rules) VALUES (?,?,?,?,?,?)",
                (pid, name, str(description or "")[:500], now, now, stored))
        self._write_order(pid, [str(t) for t in (track_ids or [])])
        return self.playlist(pid)

    def update_playlist(self, pid: str, args: Dict[str, Any], base: str = "") -> Dict[str, Any]:
        with self._lock:
            return self._update_playlist(pid, args, base)

    def _update_playlist(self, pid: str, args: Dict[str, Any], base: str = "") -> Dict[str, Any]:
        if pid in SMART:
            raise ValueError("Умный плейлист нельзя изменить / A smart playlist cannot be edited")
        if not self._q("SELECT id FROM playlists WHERE id=?", (pid,)):
            raise KeyError("Плейлист не найден / Playlist not found")
        if args.get("name") is not None:
            name = str(args["name"]).strip()[:120]
            if not name:
                raise ValueError("Название плейлиста пустое / The playlist name is empty")
            self._x("UPDATE playlists SET name=?, updated_at=? WHERE id=?", (name, time.time(), pid))
        if args.get("description") is not None:
            self._x("UPDATE playlists SET description=? WHERE id=?", (str(args["description"])[:500], pid))
        if "rules" in args:
            from .extras import clean_rules
            stored = json.dumps(clean_rules(args["rules"]), ensure_ascii=False) if args["rules"] else ""
            self._x("UPDATE playlists SET rules=?, updated_at=? WHERE id=?", (stored, time.time(), pid))
        if self._q("SELECT rules FROM playlists WHERE id=? AND rules<>''", (pid,)):
            return self.playlist(pid, base)   # a rule playlist has no stored track list
        ids = self._playlist_ids(pid)
        if isinstance(args.get("track_ids"), list):
            ids = [str(t) for t in args["track_ids"]]
        if isinstance(args.get("order"), list):
            ids = [str(t) for t in args["order"]]
        add = args.get("add")
        if add:
            ids += [str(t) for t in (add if isinstance(add, list) else [add])]
        remove = args.get("remove")
        if remove:
            gone = {str(t) for t in (remove if isinstance(remove, list) else [remove])}
            ids = [t for t in ids if t not in gone]
        self._write_order(pid, ids)
        return self.playlist(pid, base)

    def delete_playlist(self, pid: str) -> Dict[str, Any]:
        if pid in SMART:
            raise ValueError("Умный плейлист нельзя удалить / A smart playlist cannot be deleted")
        self._x("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        own = self._q("SELECT cover FROM playlists WHERE id=?", (pid,))
        if own and own[0]["cover"]:
            try:
                (self.covers_dir / own[0]["cover"]).unlink()
            except OSError:
                pass
        if not self._x("DELETE FROM playlists WHERE id=?", (pid,)):
            raise KeyError("Плейлист не найден / Playlist not found")
        return {"ok": True, "id": pid}

    # ------------------------------------------------------------------ lyrics cache
    def cached_lyrics(self, track_id: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM lyrics WHERE track_id=?", (track_id,))
        if not rows:
            return None
        r = rows[0]
        return {"synced": json.loads(r["synced"]) if r["synced"] else None, "plain": r["plain"],
                "source": r["source"], "fetched_at": r["fetched_at"]}

    def forget_lyrics(self, track_id: str) -> None:
        self._x("DELETE FROM lyrics WHERE track_id=?", (track_id,))

    def store_lyrics(self, track_id: str, synced: Optional[list], plain: Optional[str], source: str) -> None:
        self._x("INSERT OR REPLACE INTO lyrics (track_id, synced, plain, source, fetched_at) VALUES (?,?,?,?,?)",
                (track_id, json.dumps(synced, ensure_ascii=False) if synced else None, plain, source, time.time()))


_LIBS: Dict[str, Library] = {}
_LIBS_LOCK = threading.Lock()


def get_library() -> Library:
    root = str(data_root())
    with _LIBS_LOCK:
        lib = _LIBS.get(root)
        if lib is None:
            lib = _LIBS[root] = Library(Path(root))
        return lib


# ---------------------------------------------------------------------- local import helpers

def read_tags(path: Path) -> Dict[str, Any]:
    """Tags through mutagen when installed; otherwise «Artist - Title» from the file name."""
    info: Dict[str, Any] = {}
    cover: Optional[bytes] = None
    try:
        import mutagen  # optional
        audio = mutagen.File(str(path), easy=True)
        if audio is not None:
            def first(key: str) -> str:
                value = (audio.tags or {}).get(key) if audio.tags else None
                return str(value[0]) if value else ""
            info = {"title": first("title"), "artist": first("artist"), "album": first("album"),
                    "album_artist": first("albumartist"), "year": first("date")[:4], "genre": first("genre")}
            num = first("tracknumber").split("/")[0]
            info["track_no"] = int(num) if num.isdigit() else 0
            if audio.info is not None:
                info["duration"] = float(getattr(audio.info, "length", 0) or 0)
                info["bitrate"] = int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
        cover = _embedded_cover(path)
    except ImportError:
        pass
    except Exception:
        pass
    if not info.get("title"):
        stem = re.sub(r"^\d{1,3}[\s._-]+", "", path.stem).replace("_", " ").strip()
        if " - " in stem:
            artist, title = stem.split(" - ", 1)
            info["artist"] = info.get("artist") or artist.strip()
            info["title"] = title.strip()
        else:
            info["title"] = stem
    return {"fields": info, "cover": cover}


def _embedded_cover(path: Path) -> Optional[bytes]:
    import mutagen
    raw = mutagen.File(str(path))
    if raw is None:
        return None
    pictures = getattr(raw, "pictures", None)
    if pictures:
        return pictures[0].data
    tags = getattr(raw, "tags", None)
    if tags is None:
        return None
    for key in list(tags.keys()):
        if str(key).startswith("APIC"):
            return tags[key].data
    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        return bytes(covr[0])
    return None


def folder_cover(folder: Path) -> Optional[bytes]:
    for name in ("cover.jpg", "folder.jpg", "cover.png", "front.jpg", "album.jpg", "Cover.jpg", "Folder.jpg"):
        path = folder / name
        if path.is_file() and path.stat().st_size < 15 * 1024 * 1024:
            return path.read_bytes()
    return None
