"""Persistent hash cache for the MemuFiles duplicate finder.

One SQLite file next to the other MemuFiles state (``files_dir()``):
``hash-cache.sqlite3``. A row is only trusted while the file still has the
same size, mtime and inode it had when it was hashed, so an edited file can
never come back with a stale hash. Small files are not stored at all —
hashing them is cheaper than a database round trip.

The cache is best-effort by design: any SQLite or I/O failure disables it
for the rest of the run instead of breaking the duplicate search.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Only files this big are worth keeping: below it a fresh hash is faster
# than opening the database, and the cache would drown in tiny entries.
MIN_CACHED_SIZE = 1024 * 1024
MAX_ENTRIES = 200_000
_SCHEMA = """CREATE TABLE IF NOT EXISTS hashes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    dev INTEGER NOT NULL,
    ino INTEGER NOT NULL,
    full TEXT NOT NULL,
    prefix TEXT NOT NULL,
    seen INTEGER NOT NULL
)"""

# (path, size, mtime_ns, dev, ino)
CacheKey = Tuple[str, int, int, int, int]


def stat_key(path: str) -> Optional[CacheKey]:
    """The cache key of ``path`` from a fresh stat, or None when it is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)


class HashCache:
    def __init__(self, path: str | Path, max_entries: int = MAX_ENTRIES,
                 min_size: int = MIN_CACHED_SIZE) -> None:
        self.path = str(path)
        self.max_entries = max_entries
        self.min_size = min_size
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._broken = False

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._broken:
            return None
        if self._conn is not None:
            return self._conn
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_SCHEMA)
            conn.commit()
        except (OSError, sqlite3.Error):
            self._broken = True
            return None
        self._conn = conn
        return conn

    def lookup(self, keys: Sequence[CacheKey]) -> Dict[str, Tuple[str, str]]:
        """``{path: (full, prefix)}`` for the keys that are still valid."""
        conn = self._connect()
        if conn is None or not keys:
            return {}
        found: Dict[str, Tuple[str, str]] = {}
        try:
            with self._lock:
                for key in keys:
                    row = conn.execute(
                        "SELECT path, full, prefix FROM hashes"
                        " WHERE path = ? AND size = ? AND mtime_ns = ? AND dev = ? AND ino = ?",
                        key).fetchone()
                    if row is not None:
                        found[row[0]] = (row[1], row[2])
        except sqlite3.Error:
            self._broken = True
            return {}
        return found

    def store(self, entries: Iterable[Tuple[CacheKey, str, str]]) -> None:
        """Persist freshly computed hashes, then prune the oldest rows."""
        conn = self._connect()
        if conn is None:
            return
        now = time.time_ns()
        rows = [(key[0], key[1], key[2], key[3], key[4], full, prefix, now)
                for key, full, prefix in entries if key[1] >= self.min_size]
        if not rows:
            return
        try:
            with self._lock:
                conn.executemany(
                    "INSERT OR REPLACE INTO hashes"
                    " (path, size, mtime_ns, dev, ino, full, prefix, seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                conn.commit()
                count = conn.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
                if count > self.max_entries:
                    conn.execute(
                        "DELETE FROM hashes WHERE path NOT IN"
                        " (SELECT path FROM hashes ORDER BY seen DESC, rowid DESC LIMIT ?)",
                        (self.max_entries,))
                    conn.commit()
        except sqlite3.Error:
            self._broken = True

    def count(self) -> int:
        conn = self._connect()
        if conn is None:
            return 0
        try:
            with self._lock:
                return int(conn.execute("SELECT COUNT(*) FROM hashes").fetchone()[0])
        except sqlite3.Error:
            self._broken = True
            return 0

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def cache_path() -> Path:
    """Where the cache lives, unless ``MEMUFILES_HASH_CACHE`` says otherwise."""
    override = os.environ.get("MEMUFILES_HASH_CACHE", "").strip()
    if override:
        return Path(override)
    from .paths import files_dir
    return files_dir() / "hash-cache.sqlite3"
