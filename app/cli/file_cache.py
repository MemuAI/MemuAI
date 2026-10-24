"""Bounded per-session decoded files; metadata is checked on every read."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import sys
import threading


def stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@dataclass(frozen=True)
class Snapshot:
    signature: tuple
    lines: tuple[str, ...]
    truncated: bool
    bytes: int


class TextFiles:
    def __init__(self, *, max_bytes=16 * 1024 * 1024, max_files=4):
        self.max_bytes, self.max_files = max_bytes, max_files
        self._rows = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key, signature):
        with self._lock:
            entry = self._rows.get(key)
            if entry is not None:
                if entry.signature == signature:
                    self._rows.move_to_end(key)
                    return entry
                self._bytes -= self._rows.pop(key).bytes
        return None

    def put(self, key, signature, lines, truncated):
        lines = tuple(lines)
        size = sys.getsizeof(lines) + sum(sys.getsizeof(line) for line in lines)
        if size > self.max_bytes or self.max_files < 1:
            return
        entry = Snapshot(signature, lines, truncated, size)
        with self._lock:
            previous = self._rows.pop(key, None)
            if previous is not None:
                self._bytes -= previous.bytes
            self._rows[key] = entry
            self._bytes += size
            while self._bytes > self.max_bytes or len(self._rows) > self.max_files:
                self._bytes -= self._rows.popitem(last=False)[1].bytes

    def clear(self):
        with self._lock:
            self._rows.clear()
            self._bytes = 0
