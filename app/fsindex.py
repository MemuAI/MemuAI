"""Round 35: PC-wide instant file search («как Everything») — the engine behind Quick Access, the ``search_files``
model tool and MemuFiles.

Contract (other zones import this module lazily)::

    from app import fsindex
    fsindex.search(query, limit=200, kinds=None, roots=None) -> list[dict(path, name, size, mtime, is_dir, ...)]
    fsindex.search_ex(...) -> {"results", "total", "capped", "partial", "backend", "ms", "building", "error"}
    fsindex.status() -> dict           # backend, Everything detection, own index state, drives, settings
    fsindex.rebuild(wait=False)        # rescan the own index
    fsindex.get_settings() / fsindex.save_settings(patch)

Query syntax — :mod:`app.fsindex_query` (Everything-compatible: words AND, ``*.txt``, ``a | b``, ``!x``, ``ext:``,
``size:>5mb``, ``dm:today``, ``folder:``, ``in:<folder>``, ``path:``, ``regex:``, ``type:image``, ``sort:date``).

Backends, in order (setting ``backend`` = ``auto`` | ``everything`` | ``own``):

1. **Everything** (voidtools) when it runs: its IPC window (no extra files), ``Everything64.dll`` if present, or
   ``es.exe`` — :mod:`app.fsindex_win`. Nothing is indexed by MemuAI then.
2. **Own index**: on Windows, elevated, NTFS — the MFT read via ``FSCTL_ENUM_USN_DATA`` plus the USN journal for live
   changes; otherwise a parallel ``os.scandir`` walker (home folder first, so the first results come in seconds) with
   ``ReadDirectoryChangesW`` per drive for live changes (Windows) and a periodic rescan elsewhere. Saved compactly in
   ``memuai/fsindex/index.bin`` and loaded in about a second on the next start.

The own index (:class:`Snapshot`) keeps every name in ONE lowered string (``"\\n" + names joined by "\\n" + "\\n"``)
plus flat arrays (``array``) of offsets, parent folder, size, mtime. A query term turns into literal strings searched
with ``str.find`` over that blob (C speed): ``*.txt`` → ``".txt\\n"``, ``report`` → exact ``"\\nreport\\n"``, then
prefix ``"\\nreport"``, then anywhere — so the best matches are found first and the scan stops at a cap. Entries are
sorted by folder (folders in path order with ``/`` sorting first), so ``in:<folder>`` is one contiguous range.
Changes go to a small delta (added records + deleted ids) that is compacted in the background.

Privacy: the index never leaves the computer; only results of a search the user or the model asked for are shown/sent.
"""
from __future__ import annotations

import bisect
import itertools
import json
import logging
import os
import re
import struct
import sys
import threading
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from . import fsindex_query as fq
from .fsindex_query import low, norm_path

log = logging.getLogger("memuai.fsindex")

IS_WINDOWS = sys.platform == "win32"
VERSION = 1
MAGIC = b"MEMUFSX1"
SEARCH_DEADLINE = 0.35          # seconds: a search returns what it has (``partial``) after this
MAX_LIMIT = 2000
WALK_THREADS = 8
COMPACT_ADDED = 20000
COMPACT_DELETED = 60000

Batch = Tuple[str, Any]      # (folder display path, [(name, size, mtime, is_dir)] or a :class:`Packed`)


class Packed:
    """One folder's children in flat form (round 36): a 1M-file walk used to hold ~1M tuples + str + int + float
    objects (~230 MB, and the freed arenas stayed in the process RSS); this is ~40 bytes per file. Names are sorted
    by :func:`low` already, so :meth:`Snapshot.build` only concatenates. Iterates as ``(name, size, mtime, is_dir)``."""
    __slots__ = ("names", "lens", "size", "mtime", "isdir")

    def __init__(self, rows: Iterable[Tuple[str, int, float, bool]]) -> None:
        kids = sorted(((low(r[0]), r) for r in rows if r[0] and "\n" not in r[0] and "\r" not in r[0]),
                      key=lambda x: x[0])
        encoded = [r[0].encode("utf-8", "surrogatepass") for _l, r in kids]
        self.names = b"\n".join(encoded)
        self.lens = array("H", [len(e) for e in encoded])
        self.size = array("q", [int(r[1]) if r[1] is not None else -1 for _l, r in kids])
        self.mtime = array("d", [float(r[2]) if r[2] is not None else -1.0 for _l, r in kids])
        self.isdir = bytes(1 if r[3] else 0 for _l, r in kids)

    def __len__(self) -> int:
        return len(self.lens)

    def __iter__(self) -> Iterator[Tuple[str, int, float, bool]]:
        names = self.names.split(b"\n") if self.lens else []
        for i, raw in enumerate(names):
            yield raw.decode("utf-8", "surrogatepass"), self.size[i], self.mtime[i], bool(self.isdir[i])

DEFAULT_EXCLUDE_NAMES = ["$Recycle.Bin", "System Volume Information", "$WinREAgent", "Config.Msi", "WinSxS",
                         "$SysReset", "$Windows.~BT", "$Windows.~WS", "$GetCurrent", ".Trash-1000", "lost+found"]
DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,          # the feature as a whole (Quick Access search + model tool)
    "backend": "auto",        # auto | everything | own
    "roots": [],              # [] = every fixed drive (Windows) / the home folder (elsewhere)
    "exclude": list(DEFAULT_EXCLUDE_NAMES),   # names (any folder with this name) or full paths
    "live": True,             # follow changes (USN journal / ReadDirectoryChangesW / periodic rescan)
    "rescan_minutes": 60,     # periodic rescan where live changes are not available (0 = never)
    "mft": True,              # read the NTFS MFT when MemuAI runs as administrator
    "ai_tool": True,          # search_files for the Quick Access model and the main chat
    "quick_prefix": True,     # «?запрос» in the Quick Access input opens the file search
    "quick_glob": True,       # a bare «*.txt» in the input opens it too
    "hidden": True,           # include hidden / system files
    "preload": False,         # round 36: load / build the own index at MemuAI start (default: on the first search)
    "unload_minutes": 10,     # round 36: drop the own index from memory after this long without a search (0 = keep)
}


# ---------------------------------------------------------------- paths

def join(folder: str, name: str) -> str:
    if not folder:
        return "/" + name
    if folder.endswith(("\\", "/")):
        return folder + name
    sep = "\\" if ("\\" in folder or re.match(r"^[A-Za-z]:$", folder)) else "/"
    return folder + sep + name


def _split(path: str) -> Tuple[str, str]:
    path = str(path)
    if IS_WINDOWS or "\\" in path:
        head, _, tail = path.rstrip("\\/").replace("/", "\\").rpartition("\\")
        if re.match(r"^[A-Za-z]:$", head):
            head += "\\"
        return head, tail
    head, tail = os.path.split(path.rstrip("/"))
    return head or "/", tail


def data_dir() -> Path:
    """``%APPDATA%\\MemuAI\\memuai\\fsindex`` (``$MEMUAI_DATA_DIR/memuai/fsindex``) — the same rule as
    ``app.config.DATA_DIR`` without importing it (MemuFiles imports this module in its own light process)."""
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    base = Path(env) if env else Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "MemuAI"
    return base / "memuai" / "fsindex"


OWNER_STALE = 180.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    if IS_WINDOWS:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def claim_owner(force_check: bool = True) -> bool:
    """One process builds the index and follows changes (MemuAI, MemuFiles… may all import this module): the
    owner keeps ``owner.json`` fresh; the others load ``index.bin`` and reload it when it changes."""
    path = data_dir() / "owner.json"
    me = os.getpid()
    now = time.time()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        pid, at = int(info.get("pid") or 0), float(info.get("at") or 0)
    except Exception:
        pid, at = 0, 0.0
    if pid and pid != me and now - at < OWNER_STALE and _pid_alive(pid):
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{me}.tmp")
        tmp.write_text(json.dumps({"pid": me, "at": now}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return True
    return True


def release_owner() -> None:
    path = data_dir() / "owner.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if int(info.get("pid") or 0) == os.getpid():
            path.unlink()
    except Exception:
        pass


def _place(word: str) -> str:
    """``Desktop`` / ``Загрузки`` / ``documents`` → the real folder (quick_files' places)."""
    try:
        from . import quick_files
        key = quick_files._place_of(word)
        return quick_files.place_folder(key) if key else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- snapshot

class Snapshot:
    """An immutable index: names blob + flat arrays. Build with :meth:`build` from folder batches."""

    def __init__(self) -> None:
        self.blob = b"\n"           # original names (UTF-8), "\n" separated, leading and trailing "\n"
        self.lblob = b"\n"          # lowered, the same byte length (see :func:`low_bytes`)
        self.starts = array("q", [0])   # starts[i] = offset of the "\n" before name i; len n+1
        self.parent = array("i")    # folder index of entry i
        self.size = array("q")      # -1 = unknown (MFT) / folders 0
        self.mtime = array("d")     # -1 = unknown
        self.isdir = bytearray()
        self.byname = array("i")    # entry ids sorted by lowered name (exact / prefix lookups by bisect)
        self.dirs: List[str] = []   # folder display paths (sorted by dkeys with "/" first)
        self.dkeys: List[str] = []  # normalized lowered folder paths
        self.dblob = "\n"
        self.dstarts = array("q", [0])
        self.roots: List[str] = []
        self.created = 0.0
        self.source = "walk"
        self._dlookup: Optional[Dict[str, int]] = None
        self._noise: Dict[int, int] = {}

    # -- build
    @classmethod
    def build(cls, batches: Iterable[Batch], roots: Sequence[str] = (), source: str = "walk") -> "Snapshot":
        snap = cls()
        rows: Dict[str, Tuple[str, Any]] = {}
        for folder, children in batches:
            key = norm_path(folder)
            if key in rows:          # rare (the home folder walked first, ancestor rows): merge and re-pack
                rows[key] = (rows[key][0], Packed(itertools.chain(rows[key][1], children)))
            else:
                rows[key] = (folder, children if isinstance(children, Packed) else Packed(children))
        order = sorted(rows, key=lambda k: k.replace("/", "\x01"))
        pieces: List[bytes] = []
        parent = array("i")
        size = array("q")
        mtime = array("d")
        isdir = bytearray()
        lens = array("H")
        for d, key in enumerate(order):
            folder, kids = rows.pop(key)
            snap.dirs.append(folder)
            snap.dkeys.append(key)
            n = len(kids)
            if not n:
                continue
            pieces.append(kids.names)
            parent.extend(itertools.repeat(d, n))
            size.extend(kids.size)
            mtime.extend(kids.mtime)
            isdir.extend(kids.isdir)
            lens.extend(kids.lens)
        del rows
        snap.blob = b"\n" + b"\n".join(pieces) + b"\n" if pieces else b"\n"
        del pieces
        snap.lblob = low_bytes(snap.blob)
        snap.starts = array("q", itertools.accumulate(itertools.chain((0,), (n + 1 for n in lens))))
        del lens
        snap.parent, snap.size, snap.mtime, snap.isdir = parent, size, mtime, isdir
        snap._sort_names()
        snap._finish_dirs()
        snap.roots = list(roots)
        snap.created = time.time()
        snap.source = source
        return snap

    def _sort_names(self) -> None:
        lb, st = self.lblob, self.starts
        self.byname = array("i", sorted(range(len(self.parent)), key=lambda i: lb[st[i] + 1:st[i + 1]]))

    def name_range(self, prefix: str, exact: bool = False) -> Tuple[int, int]:
        """Positions in :attr:`byname` of names starting with ``prefix`` (``exact`` — equal to it)."""
        lb, st = self.lblob, self.starts
        key = prefix.encode("utf-8", "surrogatepass")
        k = lambda i: lb[st[i] + 1:st[i + 1]]
        lo = bisect.bisect_left(self.byname, key, key=k)
        hi = bisect.bisect_right(self.byname, key, key=k) if exact else \
            bisect.bisect_left(self.byname, key + b"\xff", key=k)
        return lo, hi

    def _finish_dirs(self) -> None:
        self.dblob = "\n" + "\n".join(self.dkeys) + "\n" if self.dkeys else "\n"
        self.dstarts = array("q", itertools.accumulate(itertools.chain((0,), (len(k) + 1 for k in self.dkeys))))

    # -- accessors
    def __len__(self) -> int:
        return len(self.parent)

    def name(self, i: int) -> str:
        return self.blob[self.starts[i] + 1:self.starts[i + 1]].decode("utf-8", "surrogatepass")

    def lname(self, i: int) -> str:
        return self.lblob[self.starts[i] + 1:self.starts[i + 1]].decode("utf-8", "surrogatepass")

    def path(self, i: int) -> str:
        return join(self.dirs[self.parent[i]], self.name(i))

    def dir_lookup(self) -> Dict[str, int]:
        if self._dlookup is None:
            self._dlookup = {k: i for i, k in enumerate(self.dkeys)}
        return self._dlookup

    def children(self, d: int) -> Tuple[int, int]:
        return bisect.bisect_left(self.parent, d), bisect.bisect_right(self.parent, d)

    def subtree_dirs(self, folder_key: str) -> Tuple[int, int]:
        # no third copy of every folder path (``dsort`` before round 36): the key is computed only for probed rows
        s = folder_key.replace("/", "\x01")
        k = lambda key: key.replace("/", "\x01")
        return bisect.bisect_left(self.dkeys, s, key=k), bisect.bisect_left(self.dkeys, s + "\x02", key=k)

    def subtree(self, folder_key: str) -> Tuple[int, int]:
        dlo, dhi = self.subtree_dirs(folder_key)
        if dlo >= dhi:
            return 0, 0
        return bisect.bisect_left(self.parent, dlo), bisect.bisect_left(self.parent, dhi)

    def find(self, path: str) -> int:
        """Index of an entry by its full path, -1 when absent."""
        folder, name = _split(path)
        d = self.dir_lookup().get(norm_path(folder))
        if d is None:
            return -1
        lo, hi = self.children(d)
        target = low(name)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.lname(mid) < target:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self.parent) and self.parent[lo] == d and self.lname(lo) == target:
            return lo
        return -1

    def noise(self, d: int) -> int:
        v = self._noise.get(d)
        if v is None:
            v = _noise(self.dkeys[d])
            if len(self._noise) < 500000:
                self._noise[d] = v
        return v

    # -- persistence
    def save(self, path: Any) -> None:
        path = Path(path)
        sections = [
            ("blob", self.blob),
            ("dirs", "\n".join(self.dirs).encode("utf-8", "surrogatepass")),
            ("starts", self.starts.tobytes()),
            ("parent", self.parent.tobytes()),
            ("size", self.size.tobytes()),
            ("mtime", self.mtime.tobytes()),
            ("isdir", bytes(self.isdir)),
            ("byname", self.byname.tobytes()),
        ]
        header = {"version": VERSION, "n": len(self), "ndirs": len(self.dirs), "roots": self.roots,
                  "created": self.created, "source": self.source, "byteorder": sys.byteorder,
                  "sections": [[name, len(data)] for name, data in sections]}
        raw = json.dumps(header).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            fh.write(MAGIC)
            fh.write(struct.pack("<I", len(raw)))
            fh.write(raw)
            for _name, data in sections:
                fh.write(data)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        with open(path, "rb") as fh:
            if fh.read(8) != MAGIC:
                raise ValueError("not a MemuAI file index")
            (hlen,) = struct.unpack("<I", fh.read(4))
            header = json.loads(fh.read(hlen).decode("utf-8"))
            if header.get("version") != VERSION or header.get("byteorder") != sys.byteorder:
                raise ValueError("index of another version")
            data = {name: fh.read(length) for name, length in header["sections"]}
        snap = cls()
        snap.blob = data["blob"]
        snap.lblob = low_bytes(snap.blob)
        dirs = data["dirs"].decode("utf-8", "surrogatepass")
        snap.dirs = dirs.split("\n") if dirs else []
        snap.dkeys = [norm_path(d) for d in snap.dirs]
        for name, typ in (("starts", "q"), ("parent", "i"), ("size", "q"), ("mtime", "d")):
            arr = array(typ)
            arr.frombytes(data[name])
            setattr(snap, name, arr)
        snap.isdir = bytearray(data["isdir"])
        snap.byname = array("i")
        snap.byname.frombytes(data.get("byname") or b"")
        n = int(header.get("n") or 0)
        if len(snap.byname) != n:
            snap._sort_names()
        if len(snap.parent) != n or len(snap.starts) != n + 1 or len(snap.lblob) != len(snap.blob) \
                or (n and snap.starts[-1] != len(snap.blob) - 1):
            raise ValueError("damaged index")
        snap._finish_dirs()
        snap.roots = list(header.get("roots") or [])
        snap.created = float(header.get("created") or 0)
        snap.source = str(header.get("source") or "walk")
        return snap


_NOISE = ("/windows/", "/appdata/", "/program files", "/programdata/", "/$", "/node_modules/", "/.git/",
          "/.cache/", "/cache/", "/caches/", "/temp/", "/tmp/", "/site-packages/", "/.npm/", "/.cargo/",
          "/.gradle/", "/.m2/", "/winsxs/", "/__pycache__/", "/.venv/", "/venv/", "/.local/share/", "/library/")


def _noise(ldir: str) -> int:
    d = ldir + "/"
    return 1 if any(m in d for m in _NOISE) else 0


# ---------------------------------------------------------------- index (snapshot + live delta)

class Index:
    """A :class:`Snapshot` plus the changes since it was built (added records, deleted ids, updated sizes)."""

    def __init__(self, snap: Optional[Snapshot] = None) -> None:
        self.snap = snap or Snapshot()
        self.deleted: Set[int] = set()
        self.added: Dict[str, Tuple[str, str, int, float, bool]] = {}   # lowered full path → (name, folder, size, mtime, is_dir)
        self.lock = threading.RLock()
        self.changes = 0

    def __len__(self) -> int:
        return len(self.snap) - len(self.deleted) + len(self.added)

    # -- live changes
    def _excluded(self, path: str, rules: "Exclusions") -> bool:
        return rules.path_excluded(path)

    def remove(self, path: str) -> None:
        with self.lock:
            key = norm_path(path)
            self.added.pop(key, None)
            prefix = key + "/"
            for k in [k for k in self.added if k.startswith(prefix)]:
                self.added.pop(k, None)
            i = self.snap.find(path)
            if i >= 0:
                self.deleted.add(i)
                if self.snap.isdir[i]:
                    lo, hi = self.snap.subtree(key)
                    self.deleted.update(range(lo, hi))
            self.changes += 1

    def add(self, path: str, size: int, mtime: float, is_dir: bool) -> None:
        folder, name = _split(path)
        if not name or "\n" in name:
            return
        with self.lock:
            i = self.snap.find(path)
            if i >= 0:
                self.deleted.discard(i)
                self.snap.size[i] = int(size)
                self.snap.mtime[i] = float(mtime)
                return
            self.added[norm_path(path)] = (name, folder, int(size), float(mtime), bool(is_dir))
            self.changes += 1

    def update(self, path: str, size: int, mtime: float) -> None:
        with self.lock:
            rec = self.added.get(norm_path(path))
            if rec:
                self.added[norm_path(path)] = (rec[0], rec[1], int(size), float(mtime), rec[4])
                return
            i = self.snap.find(path)
            if i >= 0 and i not in self.deleted:
                self.snap.size[i] = int(size)
                self.snap.mtime[i] = float(mtime)
            elif i < 0:
                self.add(path, size, mtime, os.path.isdir(path))

    def needs_compact(self) -> bool:
        return len(self.added) > COMPACT_ADDED or len(self.deleted) > COMPACT_DELETED

    def live_batches(self) -> Iterator[Batch]:
        """Every live record, folder by folder — for :meth:`Snapshot.build` (compaction / saving)."""
        snap = self.snap
        deleted = self.deleted
        with self.lock:
            added = list(self.added.values())
        # every folder row, the empty ones too (so later adds into them find their folder)
        for d in range(len(snap.dirs)):
            lo, hi = snap.children(d)
            kids = [(snap.name(j), snap.size[j], snap.mtime[j], bool(snap.isdir[j])) for j in range(lo, hi)
                    if j not in deleted]
            yield snap.dirs[d], kids
        for name, folder, size, mtime, is_dir in added:
            yield folder, [(name, size, mtime, is_dir)]

    def compacted(self) -> "Index":
        snap = Snapshot.build(self.live_batches(), self.snap.roots, self.snap.source)
        return Index(snap)

    # -- search
    def search(self, query: fq.Query, limit: int = 200, deadline: Optional[float] = None,
               stat_missing: bool = True) -> Dict[str, Any]:
        t0 = time.perf_counter()
        deadline = t0 + SEARCH_DEADLINE if deadline is None else deadline
        snap = self.snap
        deleted = self.deleted
        groups = [g for g in (query.groups or []) if g] or [[]]
        relevance = query.sort == "relevance"
        cap = max(limit * 2, 100) if relevance else 50000
        found: List[int] = []
        found_set: Set[int] = set()
        partial = False
        capped = False
        count = 0
        for group in groups:          # alternatives («a b | c d»): the union, each driven its own way
            c, cp, pt = self._collect(snap, group, cap, deadline, found, found_set, relevance, limit, stat_missing)
            count += c
            capped = capped or cp
            if pt:
                partial = True
                break
        lblob, starts, parent, dkeys = snap.lblob, snap.starts, snap.parent, snap.dkeys
        sizes, mtimes, isdir = snap.size, snap.mtime, snap.isdir
        delta: List[Tuple[str, str, int, float, bool]] = []
        with self.lock:
            added = list(self.added.values())
        for rec in added:
            name, folder, size, mtime, is_dir = rec
            ln, ld = low(name), norm_path(folder)
            if any(fq.match(g, ln, ld, size, mtime, is_dir) for g in groups):
                delta.append(rec)
        if len(groups) == 1:
            group = groups[0]
        else:
            group = None

        def qual(ln: str) -> int:
            if group is not None:
                return fq.quality(group, ln)
            return min(fq.quality(g, ln) for g in groups)
        # rank
        if relevance:
            def key_base(i: int) -> Tuple:
                ln = snap.lname(i)
                return (qual(ln), snap.noise(parent[i]), -mtimes[i], len(ln))
        elif query.sort == "name":
            def key_base(i: int) -> Tuple:
                return (snap.lname(i),)
        elif query.sort == "date":
            def key_base(i: int) -> Tuple:
                return (-mtimes[i],)
        elif query.sort == "size":
            def key_base(i: int) -> Tuple:
                return (-sizes[i],)
        else:
            def key_base(i: int) -> Tuple:
                return (dkeys[parent[i]], snap.lname(i))
        keyed = [(key_base(i), 0, i) for i in found]
        for j, rec in enumerate(delta):
            name, folder, size, mtime, is_dir = rec
            ln, ld = low(name), norm_path(folder)
            if relevance:
                k = (qual(ln), _noise(ld), -mtime, len(ln))
            elif query.sort == "name":
                k = (ln,)
            elif query.sort == "date":
                k = (-mtime,)
            elif query.sort == "size":
                k = (-size,)
            else:
                k = (ld, ln)
            keyed.append((k, 1, j))
        keyed.sort(key=lambda x: x[0])
        results = []
        for _k, kind, i in keyed[:limit]:
            if kind == 0:
                name = snap.name(i)
                folder = snap.dirs[parent[i]]
                results.append(_result(join(folder, name), name, folder, sizes[i], mtimes[i], bool(isdir[i])))
            else:
                name, folder, size, mtime, is_dir = delta[i]
                results.append(_result(join(folder, name), name, folder, size, mtime, is_dir))
        total = len(found) + len(delta)
        return {"results": results, "total": total, "capped": capped or partial, "partial": partial,
                "ms": round((time.perf_counter() - t0) * 1000, 2), "scanned": count}

    def _collect(self, snap: Snapshot, group: Sequence[fq.Term], cap: int, deadline: float, found: List[int],
                 found_set: Set[int], relevance: bool, limit: int, stat_missing: bool) -> Tuple[int, bool, bool]:
        """Matches of one AND-group into ``found`` (up to ``cap`` more) → (candidates seen, capped, partial)."""
        deleted = self.deleted
        needs_size = any(t.needs_size for t in group)
        needs_time = any(t.needs_time for t in group)
        tests = [(t.test, t.neg) for t in group]
        lblob, starts, parent, dkeys = snap.lblob, snap.starts, snap.parent, snap.dkeys
        sizes, mtimes, isdir = snap.size, snap.mtime, snap.isdir
        seen: Set[int] = set()
        count = 0
        capped = partial = False
        driver, boosts = self._driver(snap, group)
        sources = iter([driver])
        limit_found = len(found) + cap
        while True:
            gen = next(sources, None)
            if gen is None:
                break
            for i in gen:
                count += 1
                if (count & 511) == 0 and time.perf_counter() > deadline:
                    partial = True
                    break
                if i in seen:
                    continue
                seen.add(i)
                if i in deleted or i in found_set:
                    continue
                ln = lblob[starts[i] + 1:starts[i + 1]].decode("utf-8", "surrogatepass")
                s, m = sizes[i], mtimes[i]
                if (needs_size and s < 0 and not isdir[i]) or (needs_time and m < 0):
                    if not stat_missing:
                        continue
                    s, m = self._stat_into(snap, i)
                ok = True
                ld = dkeys[parent[i]]
                dflag = bool(isdir[i])
                for test, neg in tests:
                    if test(ln, ld, s, m, dflag) == neg:
                        ok = False
                        break
                if ok:
                    found.append(i)
                    found_set.add(i)
                    if len(found) >= limit_found:
                        capped = True
                        break
            if partial:
                break
            if capped and boosts and relevance:
                # more matches than the cap: make sure exact names and name prefixes are in (they rank first)
                sources = iter(boosts)
                boosts = []
                limit_found = len(found) + max(limit, 50)
                continue
        return count, capped, partial

    def _stat_into(self, snap: Snapshot, i: int) -> Tuple[int, float]:
        try:
            st = os.stat(snap.path(i), follow_symlinks=False)
            s, m = (0 if snap.isdir[i] else int(st.st_size)), float(st.st_mtime)
        except OSError:
            s, m = -1, -1.0
        snap.size[i], snap.mtime[i] = s, m
        return s, m

    def _driver(self, snap: Snapshot, group: Sequence[fq.Term]) -> Tuple[Iterator[int], List[Iterator[int]]]:
        """Candidate entry ids for a group — the most selective way the terms allow (see the module doc) — plus
        the «boost» passes (exact name, name prefix) used when a plain word has more matches than the cap."""
        pos_terms = [t for t in group if not t.neg]
        for t in pos_terms:
            if t.kind == "parent":
                d = snap.dir_lookup().get(t.folder)
                if d is None:
                    return iter(()), []
                lo, hi = snap.children(d)
                return iter(range(lo, hi)), []
        cands: List[Tuple[float, Callable[[], Iterator[int]], Optional[fq.Term]]] = []
        folder_terms = [t for t in pos_terms if t.kind == "in"]
        if folder_terms:
            lo, hi = snap.subtree(folder_terms[0].folder)
            if hi - lo < 20000:
                return iter(range(lo, hi)), []
            cands.append((hi - lo, lambda lo=lo, hi=hi: iter(range(lo, hi)), None))
        lit_terms = sorted((t for t in pos_terms if t.lits), key=lambda t: -t.lit_score())
        np = _np()
        numeric = [t for t in pos_terms if t.kind in ("size", "dm")] if np is not None else []
        if len(lit_terms) + len(numeric) + len(cands) > 1:
            for t in lit_terms[:4]:
                est = sum(snap.lblob.count(lit.encode("utf-8", "surrogatepass")) for lit in t.lits)
                cands.append((est, lambda t=t: self._lit_driver(snap, t), t))
            for t in numeric:
                ids = _np_range(np, snap, t)
                cands.append((len(ids), lambda ids=ids: iter(ids.tolist()), None))
        elif lit_terms:
            cands.append((0, lambda t=lit_terms[0]: self._lit_driver(snap, t), lit_terms[0]))
        elif numeric:
            ids = _np_range(np, snap, numeric[0])
            cands.append((len(ids), lambda ids=ids: iter(ids.tolist()), None))
        if cands:
            cands.sort(key=lambda c: c[0])
            _est, make, term = cands[0]
            boosts = []
            if term is not None and term.plain:
                boosts = [self._prefix_ids(snap, term.plain)]
            return make(), boosts
        path_terms = [t for t in pos_terms if t.kind == "path" and t.path_lit]
        if path_terms:
            return self._path_driver(snap, path_terms[0].path_lit), []
        regex_terms = [t for t in pos_terms if t.kind == "regex"]
        if regex_terms:
            return self._regex_driver(snap, regex_terms[0].value), []
        for t in pos_terms:
            if t.kind == "size":
                lo_s, hi_s = t.value
                return iter([i for i, v in enumerate(snap.size) if (lo_s <= v <= hi_s) or v < 0]), []
            if t.kind == "dm":
                lo_t, hi_t = t.value
                return iter([i for i, v in enumerate(snap.mtime) if (lo_t <= v < hi_t) or v < 0]), []
            if t.kind == "dir":
                return iter([i for i, v in enumerate(snap.isdir) if v]), []
        return iter(range(len(snap))), []

    @staticmethod
    def _prefix_ids(snap: Snapshot, p: str) -> Iterator[int]:
        """Exact name, then «name.ext», then names starting with ``p`` — by bisect in the name-sorted ids."""
        byname = snap.byname
        for lo, hi in (snap.name_range(p, exact=True), snap.name_range(p + "."), snap.name_range(p)):
            for k in range(lo, hi):
                yield byname[k]

    @staticmethod
    def _hits(snap: Snapshot, lit: str) -> Iterator[int]:
        blob, starts = snap.lblob, snap.starts
        lit = lit.encode("utf-8", "surrogatepass")
        find = blob.find
        right = bisect.bisect_right
        pos = 0
        while True:
            pos = find(lit, pos)
            if pos < 0:
                return
            i = right(starts, pos) - 1
            if i >= len(starts) - 1:
                return
            yield i
            pos = starts[i + 1]

    def _lit_driver(self, snap: Snapshot, term: fq.Term) -> Iterator[int]:
        for lit in term.lits:
            yield from self._hits(snap, lit)

    def _path_driver(self, snap: Snapshot, lit: str) -> Iterator[int]:
        head, _, tail = lit.rpartition("/")
        dblob, dstarts = snap.dblob, snap.dstarts
        dirs: List[int] = []
        seen: Set[int] = set()

        def dir_hits(dlit: str) -> None:
            pos = 0
            while True:
                pos = dblob.find(dlit, pos)
                if pos < 0:
                    return
                d = bisect.bisect_right(dstarts, pos) - 1
                if d >= len(dstarts) - 1:
                    return
                if d not in seen:
                    seen.add(d)
                    dirs.append(d)
                pos = dstarts[d + 1]
        dir_hits(lit)                   # the folder path itself contains the text → all its children
        if "/" not in lit:
            yield from self._hits(snap, lit)        # or in the name itself
        elif head:
            dir_hits(head + "\n")       # «…/head» + child name starting with tail
        elif tail:
            yield from self._hits(snap, "\n" + tail)
        for d in dirs:
            lo, hi = snap.children(d)
            yield from range(lo, hi)

    @classmethod
    def _regex_driver(cls, snap: Snapshot, rx: "re.Pattern[str]") -> Iterator[int]:
        lit = regex_literal(rx.pattern)
        if lit:
            yield from cls._hits(snap, lit)
            return
        try:
            multi = re.compile(rx.pattern, re.I | re.M)
        except re.error:
            yield from range(len(snap))
            return
        starts = snap.starts
        pos = 0
        n = len(starts) - 1
        # the pattern runs on text: char offsets of the decoded blob ≠ byte offsets, so map through the line number
        text = snap.lblob.decode("utf-8", "surrogatepass")
        starts = array("q", itertools.accumulate(itertools.chain(
            (0,), (len(piece) + 1 for piece in (text[1:-1].split("\n") if n else [])))))
        blob = text
        while pos < len(blob):
            m = multi.search(blob, pos)
            if not m:
                return
            i = bisect.bisect_right(starts, m.start()) - 1
            if i >= n:
                return
            if i >= 0:
                yield i
            pos = starts[i + 1] + (1 if m.end() == m.start() else 0) if i >= 0 else m.end() + 1


def low_bytes(blob: bytes) -> bytes:
    """Lowered UTF-8 with EXACTLY the same byte length (one offsets array serves both blobs): characters whose
    lowercase has another UTF-8 length (rare: «İ», «K» Kelvin…) stay as they are; ``ё`` → ``е``."""
    text = blob.decode("utf-8", "surrogatepass")
    out = text.lower().replace("ё", "е")
    enc = out.encode("utf-8", "surrogatepass")
    if len(enc) == len(blob):
        return enc
    chars = []
    for ch in text:
        lc = ch.lower()
        if lc == "ё":
            lc = "е"
        chars.append(lc if len(lc) == 1 and len(lc.encode("utf-8", "surrogatepass")) == len(ch.encode("utf-8", "surrogatepass")) else ch)
    return "".join(chars).encode("utf-8", "surrogatepass")


def regex_literal(pattern: str) -> str:
    """A literal every match of a simple regex contains (``^IMG_\\d+`` → ``"\\nimg_"``), or ``""``."""
    if "|" in pattern or "(?" in pattern:
        return ""
    anchored = pattern.startswith("^")
    body = pattern[1:] if anchored else pattern
    m = re.match(r"[A-Za-z0-9_\- а-яА-ЯёЁ]+", body)
    if not m:
        return ""
    lit = m.group(0)
    rest = body[len(lit):]
    if rest[:1] in ("*", "?", "{") and lit:
        lit = lit[:-1]          # «ab*» — the last char is optional
    if len(lit) < 2:
        return ""
    return ("\n" if anchored else "") + low(lit)


def release_memory() -> None:
    """Round 36: hand freed index memory back to the system (not only to Python's own allocator)."""
    import gc
    gc.collect()
    try:
        import ctypes
        if IS_WINDOWS:
            # trims the working set Task Manager shows; freed heap pages are returned by the CRT on their own
            # (NOT verified on Windows)
            kernel32 = ctypes.windll.kernel32
            ctypes.windll.psapi.EmptyWorkingSet(kernel32.GetCurrentProcess())
        elif sys.platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception as exc:
        log.debug("fsindex: memory release skipped: %s", exc)


_NP: List[Any] = []


def _np():
    if not _NP:
        try:
            import numpy
            _NP.append(numpy)
        except Exception:
            _NP.append(None)
    return _NP[0]


def _np_range(np: Any, snap: Snapshot, term: fq.Term) -> Any:
    lo, hi = term.value
    if term.kind == "size":
        a = np.frombuffer(snap.size, dtype=np.int64) if len(snap.size) else np.zeros(0, dtype=np.int64)
        mask = ((a >= lo) & (a <= min(hi, 2 ** 62))) | (a < 0)
    else:
        a = np.frombuffer(snap.mtime, dtype=np.float64) if len(snap.mtime) else np.zeros(0)
        mask = ((a >= lo) & (a < hi)) | (a < 0)
    return np.nonzero(mask)[0]


def _result(path: str, name: str, folder: str, size: int, mtime: float, is_dir: bool) -> Dict[str, Any]:
    return {"path": path, "name": name, "dir": folder, "size": int(size) if size is not None and size >= 0 else None,
            "mtime": float(mtime) if mtime is not None and mtime >= 0 else None, "is_dir": bool(is_dir),
            "kind": fq.kind_of(name, is_dir)}


# ---------------------------------------------------------------- walker

class Exclusions:
    def __init__(self, rules: Sequence[str]) -> None:
        self.names: Set[str] = set()
        self.paths: List[str] = []
        for rule in rules or []:
            r = str(rule or "").strip()
            if not r:
                continue
            if "/" in r or "\\" in r:
                self.paths.append(norm_path(os.path.expandvars(os.path.expanduser(r))))
            else:
                self.names.add(low(r))

    def name_excluded(self, name: str) -> bool:
        return low(name) in self.names

    def path_excluded(self, path: str) -> bool:
        key = norm_path(path)
        for p in self.paths:
            if key == p or key.startswith(p + "/"):
                return True
        parts = key.split("/")
        return any(part in self.names for part in parts)


class Progress:
    def __init__(self) -> None:
        self.dirs = 0
        self.entries = 0
        self.errors = 0
        self.current = ""
        self.phase = ""
        self.started = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"dirs": self.dirs, "entries": self.entries, "errors": self.errors, "current": self.current,
                "phase": self.phase, "seconds": round(time.time() - self.started, 1) if self.started else 0}


def _scan_dir(path: str, rules: Exclusions, hidden: bool) -> Tuple[Batch, List[str], int]:
    kids: List[Tuple[str, int, float, bool]] = []
    subdirs: List[str] = []
    errors = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                name = e.name
                if "\n" in name or "\r" in name:
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    errors += 1
                    continue
                attrs = getattr(st, "st_file_attributes", 0) or 0
                if not hidden and (attrs & 0x2 or (not IS_WINDOWS and name.startswith("."))):
                    continue
                if is_dir:
                    if rules.name_excluded(name):
                        continue
                    full = join(path, name)
                    if rules.paths and rules.path_excluded(full):
                        continue
                    kids.append((name, 0, float(st.st_mtime), True))
                    if not attrs & 0x400:       # FILE_ATTRIBUTE_REPARSE_POINT: junctions / links — not followed
                        subdirs.append(full)
                else:
                    kids.append((name, int(st.st_size), float(st.st_mtime), False))
    except OSError:
        errors += 1
    return (path, Packed(kids)), subdirs, errors


def walk(roots: Sequence[str], rules: Exclusions, *, hidden: bool = True, progress: Optional[Progress] = None,
         stop: Optional[threading.Event] = None, skip: Sequence[str] = (), threads: int = WALK_THREADS) -> List[Batch]:
    """Parallel breadth-first ``os.scandir`` of ``roots`` (levels in a thread pool: scandir releases the GIL)."""
    progress = progress or Progress()
    skip_keys = {norm_path(s) for s in skip}
    batches: List[Batch] = []
    frontier = [r for r in roots if os.path.isdir(r)]
    with ThreadPoolExecutor(max_workers=max(1, threads), thread_name_prefix="fsindex-walk") as pool:
        while frontier:
            if stop is not None and stop.is_set():
                break
            nxt: List[str] = []
            for batch, subdirs, errors in pool.map(lambda p: _scan_dir(p, rules, hidden), frontier):
                batches.append(batch)
                progress.dirs += 1
                progress.entries += len(batch[1])
                progress.errors += errors
                progress.current = batch[0]
                nxt.extend(s for s in subdirs if not skip_keys or norm_path(s) not in skip_keys)
            frontier = nxt
    return batches


def default_roots() -> List[str]:
    if IS_WINDOWS:
        try:
            from . import fsindex_win
            drives = [d["path"] for d in fsindex_win.drives() if d.get("type") == "fixed"]
            if drives:
                return drives
        except Exception as exc:
            log.debug("fsindex: drives failed: %s", exc)
        return ["C:\\"]
    return [str(Path.home())]


def available_drives() -> List[Dict[str, Any]]:
    if IS_WINDOWS:
        try:
            from . import fsindex_win
            return fsindex_win.drives()
        except Exception:
            return [{"path": "C:\\", "type": "fixed", "label": "", "fs": ""}]
    out = [{"path": str(Path.home()), "type": "home", "label": "~", "fs": ""}]
    out.append({"path": "/", "type": "fixed", "label": "/", "fs": ""})
    return out


# ---------------------------------------------------------------- settings

_SETTINGS_LOCK = threading.RLock()


def normalize_settings(raw: Any) -> Dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    src = raw if isinstance(raw, dict) else {}
    for key in ("enabled", "live", "mft", "ai_tool", "quick_prefix", "quick_glob", "hidden", "preload"):
        if key in src:
            out[key] = src[key] is True or str(src[key]).lower() in ("1", "true", "yes", "on")
    if src.get("backend") in ("auto", "everything", "own"):
        out["backend"] = src["backend"]
    if isinstance(src.get("roots"), list):
        out["roots"] = [str(r).strip()[:400] for r in src["roots"] if str(r or "").strip()][:32]
    if isinstance(src.get("exclude"), list):
        out["exclude"] = [str(r).strip()[:400] for r in src["exclude"] if str(r or "").strip()][:200]
    try:
        out["rescan_minutes"] = max(0, min(24 * 60, int(src.get("rescan_minutes", out["rescan_minutes"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["unload_minutes"] = max(0, min(24 * 60, int(src.get("unload_minutes", out["unload_minutes"]))))
    except (TypeError, ValueError):
        pass
    return out


def get_settings() -> Dict[str, Any]:
    with _SETTINGS_LOCK:
        try:
            raw = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        return normalize_settings(raw)


def save_settings(patch: Any) -> Dict[str, Any]:
    with _SETTINGS_LOCK:
        current = get_settings()
        before = dict(current)
        if isinstance(patch, dict):
            current.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
        current = normalize_settings(current)
        path = data_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    changed_scope = any(before.get(k) != current.get(k) for k in ("roots", "exclude", "hidden", "mft"))
    svc = service()
    if changed_scope and svc.index is not None:
        svc.rebuild()
    elif before.get("live") != current.get("live") or before.get("backend") != current.get("backend"):
        svc.restart_watchers()
    if current.get("enabled") and not before.get("enabled"):
        svc.start()
    return current


# ---------------------------------------------------------------- service

class Service:
    """The process-wide index: load / build in the background, live changes, backend choice."""

    def __init__(self) -> None:
        self.index: Optional[Index] = None
        self.state = "idle"          # idle | loading | building | ready | error
        self.error = ""
        self.progress = Progress()
        self.built_at = 0.0
        self.build_seconds = 0.0
        self.live_mode = "off"
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._watchers: List[Any] = []
        self._dirty = False
        self._last_save = 0.0
        self._timer: Optional[threading.Thread] = None
        self._everything_cache: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
        self.mft_used = False
        self.owner = False
        self._loaded_mtime = 0.0
        self.last_used = time.time()     # round 36: the last search / warm-up (idle unloading)
        self._unloaded_at = 0.0

    def touch(self) -> None:
        self.last_used = time.time()

    def unload_if_idle(self, settings: Dict[str, Any], now: Optional[float] = None) -> bool:
        """Round 36: free the in-memory index after ``unload_minutes`` without a search.

        ~90 MB per million files went back to the system only when MemuAI quit. The saved index.bin
        stays; the next search loads it again in about a second (lazy, as on the first search).
        """
        minutes = int(settings.get("unload_minutes") or 0)
        now = time.time() if now is None else now
        if not minutes or self.index is None or self.state != "ready" or now - self.last_used < minutes * 60:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._dirty and self.owner:
                self._save()
            for w in self._watchers:
                try:
                    w.stop()
                except Exception:
                    pass
            self._watchers = []
            self.live_mode = "off"
            self.index = None
            self.state = "idle"
            self._loaded_mtime = 0.0
            self._unloaded_at = now
        release_memory()
        log.info("fsindex: unloaded after %d min without a search", minutes)
        return True

    # -- Everything
    def everything(self, fresh: bool = False) -> Dict[str, Any]:
        at, cached = self._everything_cache
        if cached is not None and not fresh and time.time() - at < 5:
            return cached
        info: Dict[str, Any] = {"installed": False, "running": False, "version": "", "db_loaded": False,
                                "via": "", "es": "", "dll": "", "exe": ""}
        if IS_WINDOWS:
            try:
                from . import fsindex_win
                info.update(fsindex_win.everything_info())
            except Exception as exc:
                info["error"] = str(exc)[:200]
        self._everything_cache = (time.time(), info)
        return info

    def use_everything(self) -> bool:
        backend = get_settings().get("backend")
        if backend == "own" or not IS_WINDOWS:
            return False
        info = self.everything()
        return bool(info.get("running") and info.get("via"))

    # -- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self.index is not None and self.state == "ready":
                return
            settings = get_settings()
            if not settings.get("enabled"):
                return
            if self.use_everything() and settings.get("backend") != "own":
                # Everything answers; the own index is loaded only if a saved one exists (a fallback when it quits)
                if not (data_dir() / "index.bin").exists():
                    return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_start, name="fsindex-start", daemon=True)
            self._thread.start()

    def ensure_own(self) -> None:
        """A search needs the own index (Everything is off): load / build it now in the background."""
        if self.index is None and self.state not in ("loading", "building"):
            self._stop.clear()
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._run_start, name="fsindex-start", daemon=True)
                    self._thread.start()

    def _run_start(self) -> None:
        _np()                               # import numpy (optional) now, not on the first size:/dm: search
        path = data_dir() / "index.bin"
        settings = get_settings()
        self.owner = claim_owner()
        if not self.owner:
            # another MemuAI process builds and follows changes: use its saved index and reload it when it changes
            self.state = "loading" if path.exists() else "waiting"
            self._reload_if_changed()
            self._schedule()
            return
        if path.exists():
            self.state = "loading"
            try:
                t0 = time.time()
                snap = Snapshot.load(path)
                roots = settings.get("roots") or default_roots()
                if sorted(norm_path(r) for r in snap.roots) == sorted(norm_path(r) for r in roots):
                    self.index = Index(snap)
                    self.built_at = snap.created
                    self.state = "ready"
                    log.info("fsindex: loaded %d entries in %.2fs", len(snap), time.time() - t0)
                    self.restart_watchers()
                    # the saved index may be days old: refresh quietly when there is no live journal
                    # (round 36: reloaded after an idle unload — it was live until then; the rescan period decides)
                    fresh_for = 600 if not self._unloaded_at else \
                        max(600, int(settings.get("rescan_minutes") or 60) * 60)
                    if self.live_mode != "usn" and time.time() - max(snap.created, self._unloaded_at) > fresh_for:
                        self._build(settings, quiet=True)
                    self._schedule()
                    return
            except Exception as exc:
                log.warning("fsindex: saved index unusable (%s), rebuilding", exc)
        self._build(settings)
        self._schedule()

    def _reload_if_changed(self) -> bool:
        path = data_dir() / "index.bin"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        if mtime <= self._loaded_mtime:
            return False
        try:
            snap = Snapshot.load(path)
        except Exception as exc:
            log.debug("fsindex: reload failed: %s", exc)
            return False
        self._loaded_mtime = mtime
        self.index = Index(snap)
        self.built_at = snap.created
        self.state = "ready"
        return True

    def rebuild(self, wait: bool = False) -> None:
        if not self.owner:
            self.owner = claim_owner()
            if not self.owner:
                # the owner process rebuilds when it sees the request file
                try:
                    (data_dir() / "rebuild.request").write_text(str(time.time()), encoding="utf-8")
                except OSError:
                    pass
                return
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self.state == "building":
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._build, args=(get_settings(),), name="fsindex-build",
                                            daemon=True)
            self._thread.start()
            thread = self._thread
        if wait:
            thread.join()

    def _build(self, settings: Dict[str, Any], quiet: bool = False) -> None:
        roots = settings.get("roots") or default_roots()
        rules = Exclusions(settings.get("exclude") or [])
        hidden = bool(settings.get("hidden", True))
        prev_state = self.state
        if not quiet or self.index is None:
            self.state = "building"
        self.progress = Progress()
        self.progress.started = time.time()
        self.error = ""
        t0 = time.time()
        try:
            batches: List[Batch] = []
            source = "walk"
            self.mft_used = False
            mft_roots: List[str] = []
            if IS_WINDOWS and settings.get("mft", True):
                try:
                    from . import fsindex_win
                    if fsindex_win.is_admin():
                        for root in roots:
                            if fsindex_win.is_ntfs_root(root):
                                self.progress.phase = "mft"
                                self.progress.current = root
                                got = list(fsindex_win.mft_batches(root, rules, self.progress))
                                batches.extend(got)
                                mft_roots.append(root)
                except Exception as exc:
                    log.info("fsindex: MFT read unavailable (%s) — walking folders", exc)
            walk_roots = [r for r in roots if r not in mft_roots]
            if mft_roots:
                self.mft_used = True
                source = "mft" if not walk_roots else "mft+walk"
            home = str(Path.home())
            home_key = norm_path(home)
            first = [home] if any(home_key == norm_path(r) or home_key.startswith(norm_path(r) + "/") for r in walk_roots) \
                and not any(norm_path(r) == home_key for r in walk_roots) else []
            if first and not rules.path_excluded(home):
                self.progress.phase = "home"
                batches.extend(walk(first, rules, hidden=hidden, progress=self.progress, stop=self._stop))
                if self.index is None or not quiet:
                    # the first results within seconds: the home folder is searchable while the drives are walked
                    partial = Snapshot.build(list(batches) + _ancestor_rows(home, walk_roots), roots, source)
                    if self.index is None or self.state == "building":
                        self.index = Index(partial)
            if walk_roots:
                self.progress.phase = "drives"
                batches.extend(walk(walk_roots, rules, hidden=hidden, progress=self.progress, stop=self._stop,
                                    skip=first))
            if self._stop.is_set():
                self.state = prev_state if self.index is not None else "idle"
                return
            self.progress.phase = "sorting"
            snap = Snapshot.build(batches, roots, source)
            del batches
            self.index = Index(snap)
            self.built_at = snap.created
            self.build_seconds = round(time.time() - t0, 2)
            self.state = "ready"
            self.progress.phase = "done"
            log.info("fsindex: %d entries in %.1fs (%s)", len(snap), self.build_seconds, source)
            try:
                snap.save(data_dir() / "index.bin")
                self._last_save = time.time()
                self._loaded_mtime = (data_dir() / "index.bin").stat().st_mtime
            except Exception as exc:
                log.warning("fsindex: could not save the index: %s", exc)
            self.restart_watchers()
            release_memory()   # round 36: the walk batches (≈2× the index) go back to the system, not just to Python
        except Exception as exc:
            log.exception("fsindex: build failed")
            self.error = str(exc)[:300]
            self.state = "error" if self.index is None else "ready"

    def _schedule(self) -> None:
        if self._timer is not None and self._timer.is_alive():
            return
        self._timer = threading.Thread(target=self._tick_loop, name="fsindex-timer", daemon=True)
        self._timer.start()

    def _tick_loop(self) -> None:
        last_scan = time.time()
        while not self._stop.wait(30):
            try:
                settings = get_settings()
                if not self.owner:
                    if self.unload_if_idle(settings):
                        continue
                    if self.index is not None or not self._unloaded_at:   # unloaded: wait for the next search
                        self._reload_if_changed()
                    if claim_owner():          # the owner quit: take over (live changes, saving)
                        self.owner = True
                        self.restart_watchers()
                    continue
                claim_owner()                  # heartbeat
                request = data_dir() / "rebuild.request"
                if request.exists():
                    try:
                        request.unlink()
                    except OSError:
                        pass
                    if self.state == "ready":
                        self._build(settings)
                if self.unload_if_idle(settings):
                    continue
                idx = self.index
                if idx is None:
                    continue
                if idx.needs_compact() and self.state == "ready":
                    self.index = idx.compacted()
                    self._dirty = True
                if self._dirty and time.time() - self._last_save > 60 and self.state == "ready":
                    self._save()
                minutes = int(settings.get("rescan_minutes") or 0)
                if minutes and self.live_mode in ("off", "poll") and time.time() - last_scan > minutes * 60 \
                        and self.state == "ready":
                    last_scan = time.time()
                    self._build(settings, quiet=True)
            except Exception as exc:
                log.debug("fsindex: tick failed: %s", exc)

    def _save(self) -> None:
        idx = self.index
        if idx is None:
            return
        try:
            snap = Snapshot.build(idx.live_batches(), idx.snap.roots, idx.snap.source) if (idx.added or idx.deleted) else idx.snap
            snap.save(data_dir() / "index.bin")
            self._last_save = time.time()
            self._loaded_mtime = (data_dir() / "index.bin").stat().st_mtime
            self._dirty = False
        except Exception as exc:
            log.debug("fsindex: save failed: %s", exc)

    # -- live changes
    def restart_watchers(self) -> None:
        for w in self._watchers:
            try:
                w.stop()
            except Exception:
                pass
        self._watchers = []
        settings = get_settings()
        self.live_mode = "off"
        if not settings.get("live") or self.index is None or not self.owner:
            return
        roots = settings.get("roots") or default_roots()
        if IS_WINDOWS:
            try:
                from . import fsindex_win
                rules = Exclusions(settings.get("exclude") or [])
                for root in roots:
                    w = None
                    if self.mft_used and fsindex_win.is_ntfs_root(root):
                        w = fsindex_win.UsnWatcher(root, self.apply_events)
                        self.live_mode = "usn"
                    else:
                        w = fsindex_win.DirWatcher(root, self.apply_events)
                        if self.live_mode != "usn":
                            self.live_mode = "watch"
                    w.rules = rules
                    w.start()
                    self._watchers.append(w)
            except Exception as exc:
                log.info("fsindex: live changes unavailable: %s", exc)
                self.live_mode = "poll"
        else:
            self.live_mode = "poll"

    def apply_events(self, events: Iterable[Tuple[str, str, str]]) -> None:
        """``[(action, path, new_path)]`` from a watcher: created / deleted / modified / renamed."""
        idx = self.index
        if idx is None:
            return
        rules = Exclusions(get_settings().get("exclude") or [])
        own = norm_path(str(data_dir()))
        for action, path, new_path in events:
            if action == "overflow":
                # the watcher lost changes (a burst): rescan quietly, searches keep the current index meanwhile
                if self.state == "ready":
                    threading.Thread(target=self._build, args=(get_settings(), True), daemon=True).start()
                return
            key = norm_path(new_path or path)
            if key == own or key.startswith(own + "/"):
                continue
            try:
                if action == "renamed":
                    idx.remove(path)
                    action, path = "created", new_path
                if rules.path_excluded(path):
                    continue
                if action == "deleted":
                    idx.remove(path)
                elif action in ("created", "modified"):
                    try:
                        st = os.stat(path, follow_symlinks=False)
                    except OSError:
                        if action == "created":
                            continue
                        idx.remove(path)
                        continue
                    is_dir = os.path.isdir(path) and not os.path.islink(path)
                    if action == "created" or idx.snap.find(path) < 0:
                        idx.add(path, 0 if is_dir else st.st_size, st.st_mtime, is_dir)
                        if is_dir and action == "created":
                            for folder, kids in walk([path], rules, threads=2):
                                for name, size, mtime, kdir in kids:
                                    idx.add(join(folder, name), size, mtime, kdir)
                    else:
                        idx.update(path, 0 if is_dir else st.st_size, st.st_mtime)
                self._dirty = True
            except Exception as exc:
                log.debug("fsindex: event %s %s failed: %s", action, path, exc)

    def stop(self) -> None:
        self._stop.set()
        for w in self._watchers:
            try:
                w.stop()
            except Exception:
                pass
        self._watchers = []
        if self._dirty and self.owner:
            self._save()
        if self.owner:
            release_owner()


def _ancestor_rows(home: str, roots: Sequence[str]) -> List[Batch]:
    """Folder rows from a drive root down to the home folder (so the partial index knows «C:\\Users»)."""
    rows: List[Batch] = []
    path = home
    while True:
        parent, name = _split(path)
        if not name or norm_path(parent) == norm_path(path):
            break
        rows.append((parent, [(name, 0, -1.0, True)]))
        if any(norm_path(parent) == norm_path(r) for r in roots):
            break
        path = parent
    return rows


_SERVICE: Optional[Service] = None
_SERVICE_LOCK = threading.Lock()


def service() -> Service:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = Service()
        return _SERVICE


# ---------------------------------------------------------------- public API

def start() -> None:
    """Warm start (the Quick Access plugin calls it): load the saved index / build one when Everything is absent."""
    try:
        svc = service()
        svc.touch()
        svc.start()
    except Exception as exc:
        log.warning("fsindex: start failed: %s", exc)


def search_ex(query: str, limit: int = 200, kinds: Any = None, roots: Any = None, sort: Optional[str] = None,
              timeout: Optional[float] = None, backend: Optional[str] = None, validate: bool = True) -> Dict[str, Any]:
    """Search; the result says which backend answered and whether the list is complete."""
    t0 = time.perf_counter()
    limit = max(1, min(MAX_LIMIT, int(limit or 200)))
    text = str(query or "").strip()[:1000]
    out: Dict[str, Any] = {"results": [], "total": 0, "capped": False, "partial": False, "backend": "",
                           "ms": 0.0, "building": False, "error": "", "query": text}
    try:
        parsed = fq.parse(text, places=_place, kinds=kinds, roots=roots)
    except fq.QueryError as exc:
        out["error"] = str(exc)
        out["code"] = "query"
        return out
    if sort:
        parsed.sort = sort if sort in ("relevance", "name", "date", "size", "path") else parsed.sort
    if not fq.has_terms(parsed):
        out["error"] = "empty query"
        out["code"] = "empty"
        return out
    svc = service()
    settings = get_settings()
    choice = backend or settings.get("backend")
    if IS_WINDOWS and choice != "own" and svc.use_everything():
        try:
            from . import fsindex_win
            ev_query = fq.to_everything(text, kinds=kinds, roots=roots, places=_place)
            got = fsindex_win.everything_search(ev_query, limit=limit, sort=parsed.sort,
                                                info=svc.everything(), timeout=timeout or 3.0, rank_query=text)
            out.update(got)
            out["backend"] = "everything"
            out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return out
        except Exception as exc:
            log.info("fsindex: Everything failed (%s) — own index", exc)
            svc._everything_cache = (0.0, None)
            if choice == "everything":
                out["error"] = "everything: " + str(exc)[:200]
    svc.touch()
    if svc.index is None:
        svc.ensure_own()
        # loading a saved index takes about a second: wait for it a little
        waited = 0.0
        while svc.index is None and svc.state in ("loading", "idle", "building") and waited < 1.5:
            time.sleep(0.05)
            waited += 0.05
    idx = svc.index
    out["backend"] = "own"
    out["building"] = svc.state in ("building", "loading")
    if idx is None:
        out["error"] = out["error"] or ("indexing" if out["building"] else (svc.error or "no index"))
        out["code"] = "indexing" if out["building"] else "noindex"
        return out
    deadline = time.perf_counter() + (timeout if timeout else SEARCH_DEADLINE)
    got = idx.search(parsed, limit=limit, deadline=deadline)
    results = got["results"]
    if validate and results:
        alive = []
        for row in results:
            if os.path.lexists(row["path"]):
                alive.append(row)
            else:
                idx.remove(row["path"])
        if len(alive) != len(results):
            got["total"] = max(0, got["total"] - (len(results) - len(alive)))
        results = alive
    out.update(got)
    out["results"] = results
    out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def search(query: str, limit: int = 200, kinds: Any = None, roots: Any = None) -> List[Dict[str, Any]]:
    """The contract: a list of ``{path, name, size, mtime, is_dir, dir, kind}`` (best matches first)."""
    return search_ex(query, limit=limit, kinds=kinds, roots=roots)["results"]


def status() -> Dict[str, Any]:
    svc = service()
    settings = get_settings()
    idx = svc.index
    ev = svc.everything()
    active = "everything" if (IS_WINDOWS and settings.get("backend") != "own" and ev.get("running") and ev.get("via")) \
        else ("own" if settings.get("enabled") else "off")
    admin = False
    if IS_WINDOWS:
        try:
            from . import fsindex_win
            admin = fsindex_win.is_admin()
        except Exception:
            admin = False
    own = {"state": svc.state, "entries": len(idx) if idx is not None else 0,
           "dirs": len(idx.snap.dirs) if idx is not None else 0,
           "built_at": svc.built_at, "build_seconds": svc.build_seconds,
           "source": idx.snap.source if idx is not None else "",
           "roots": idx.snap.roots if idx is not None else (settings.get("roots") or default_roots()),
           "progress": svc.progress.as_dict() if svc.state in ("building", "loading") else None,
           "live": svc.live_mode, "error": svc.error,
           "pending": {"added": len(idx.added), "deleted": len(idx.deleted)} if idx is not None else None}
    try:
        file = data_dir() / "index.bin"
        own["file_bytes"] = file.stat().st_size if file.exists() else 0
    except OSError:
        own["file_bytes"] = 0
    ready = active == "everything" or (idx is not None and svc.state in ("ready", "building"))
    return {"backend": active, "ready": ready, "owner": svc.owner, "settings": settings, "everything": ev, "own": own,
            "platform": sys.platform, "admin": admin, "drives": available_drives(),
            "default_roots": default_roots()}


def rebuild(wait: bool = False) -> None:
    service().rebuild(wait=wait)


def shutdown() -> None:
    if _SERVICE is not None:
        _SERVICE.stop()
