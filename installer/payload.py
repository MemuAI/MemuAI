"""MemuAI payload container ("MPK1"): the program files appended to the Setup exe.

Layout of a finished ``MemuAI-Setup-<ver>.exe`` (the setup stub is a normal
PyInstaller one-file exe = bootloader PE + its CArchive ending in a "cookie")::

    [stub: bootloader PE image                 ]
    [payload: compressed blocks ...............]   <- inserted here
    [index: zlib-compressed JSON               ]
    [trailer: 40 bytes                         ]
    [stub: PyInstaller CArchive ... cookie     ]
    [optional Authenticode table (signtool)    ]

The PyInstaller bootloader scans the file *backwards* for its cookie and
computes the archive start relative to the cookie (bootloader/src/pyi_archive.c,
``_pyi_archive_find_pkg_cookie_offset``), so data inserted before the archive is
invisible to it — and, unlike data appended after the cookie, it does not make
the bootloader read hundreds of megabytes on every start. Stubs without a
PyInstaller cookie (unit tests) get the payload appended at the end instead.

Trailer = ``MAGIC (8) | payload_len (u64) | index_len (u64) | version (u32) |
reserved (u32) | MAGIC_END (8)``. ``payload_len`` counts blocks + index, so the
payload starts at ``trailer_pos - payload_len``. The uninstaller is the stub
without the payload (``Payload.read_stub``).

Why not a zip: files are grouped into solid xz blocks (BCJ x86 filter for
binaries) that are compressed in parallel at build time — close to the ratio
of Inno's solid LZMA2 while the build stays fast — and every file carries a
SHA-256, so a damaged download is reported instead of installing broken
binaries. Nothing is ever stored uncompressed: a raw PyInstaller exe inside the
payload would expose its own archive cookie to the stub's bootloader, which
searches the file backwards for the *last* cookie (``assemble`` checks every block and the index).
"""
from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import re
import tempfile
from itertools import groupby
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

MAGIC = b"MEMUPAK1"
MAGIC_END = b"1KAPUMEM"
TRAILER = struct.Struct("<8sQQII8s")
FORMAT_VERSION = 2
SUPPORTED_FORMATS = (1, 2)
MAX_INDEX_BYTES = 32 * 1024 * 1024

PYI_COOKIE_MAGIC = b"MEI\014\013\012\013\016"

TRAILER_SEARCH = 4 * 1024 * 1024
BLOCK_TARGET = 8 * 1024 * 1024
BINARY_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".node", ".sys", ".ocx", ".cpl"}
CHUNK = 1024 * 1024

class PayloadError(Exception):
    """The payload is missing, truncated or damaged."""

@dataclass
class FileEntry:
    path: str
    component: str
    size: int
    sha256: str
    block: int = -1
    offset: int = 0
    mode: int = 0o644

    def to_json(self) -> dict:
        return {"p": self.path, "c": self.component, "s": self.size, "h": self.sha256,
                "b": self.block, "o": self.offset}

    @classmethod
    def from_json(cls, raw: dict) -> "FileEntry":
        return cls(path=str(raw["p"]), component=str(raw["c"]), size=int(raw["s"]),
                   sha256=str(raw["h"]), block=int(raw["b"]), offset=int(raw["o"]))

@dataclass
class Block:
    offset: int
    csize: int
    size: int
    filters: str

    def to_json(self) -> dict:
        return {"o": self.offset, "c": self.csize, "s": self.size, "f": self.filters}

    @classmethod
    def from_json(cls, raw: dict) -> "Block":
        return cls(offset=int(raw["o"]), csize=int(raw["c"]), size=int(raw["s"]), filters=str(raw["f"]))

@dataclass
class Index:
    meta: Dict[str, object]
    files: List[FileEntry]
    blocks: List[Block]
    dirs: List[Tuple[str, str]] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        raw = {"format": FORMAT_VERSION, "meta": self.meta,
               "files": [f.to_json() for f in self.files],
               "blocks": [b.to_json() for b in self.blocks],
               "dirs": [list(d) for d in self.dirs]}
        return zlib.compress(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Index":
        try:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(data, MAX_INDEX_BYTES + 1)
            if len(decoded) > MAX_INDEX_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                raise PayloadError("payload index exceeds its size limit or is truncated")
            raw = json.loads(decoded.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("index must be an object")
            version = raw.get("format")
            if type(version) is not int or version not in SUPPORTED_FORMATS:
                raise PayloadError(f"unsupported payload format {version}")
            if not isinstance(raw.get("meta", {}), dict) or not all(isinstance(raw.get(key, []), list) for key in ("files", "blocks", "dirs")):
                raise ValueError("invalid index collections")
            def integer(row, key):
                value = row[key]
                if type(value) is not int or not 0 <= value < 2**63:
                    raise ValueError(f"invalid {key}")
                return value
            files = []
            for row in raw.get("files", []):
                if not isinstance(row, dict) or not isinstance(row.get("p"), str) or not isinstance(row.get("c"), str):
                    raise ValueError("invalid file entry")
                digest = row.get("h")
                if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                    raise ValueError("invalid file checksum")
                files.append(FileEntry(safe_relpath(row['p']), row['c'], integer(row,'s'), digest.lower(), integer(row,'b'), integer(row,'o')))
            blocks = []
            for row in raw.get("blocks", []):
                if not isinstance(row, dict) or row.get('f') not in ('plain','x86'):
                    raise ValueError("invalid block entry")
                block = Block(integer(row,'o'), integer(row,'c'), integer(row,'s'), row['f'])
                if not block.csize:
                    raise ValueError("empty compressed block")
                blocks.append(block)
            dirs = []
            for row in raw.get("dirs", []):
                if not isinstance(row, list) or len(row) != 2 or not all(isinstance(v, str) for v in row):
                    raise ValueError("invalid directory entry")
                dirs.append((row[0],safe_relpath(row[1])))
            index = cls(meta=raw.get('meta', {}), files=files, blocks=blocks, dirs=dirs)
            index.validate()
            return index
        except PayloadError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError, zlib.error, RecursionError) as exc:
            raise PayloadError(f"payload index is damaged: {exc}") from exc

    def validate(self) -> None:
        paths = set()
        for entry in self.files:
            key = entry.path.casefold()
            if key in paths or not entry.component or entry.block >= len(self.blocks):
                raise PayloadError("duplicate path or invalid component/block reference")
            paths.add(key)
            if entry.offset + entry.size > self.blocks[entry.block].size:
                raise PayloadError("file extends beyond its payload block")
        for component, directory in self.dirs:
            if not component or directory.casefold() in paths:
                raise PayloadError("directory conflicts with a payload file")
        for key in paths:
            if any('/'.join(key.split('/')[:i]) in paths for i in range(1,len(key.split('/')))):
                raise PayloadError("file conflicts with a payload directory")
        physical_end = 0
        for block in sorted(self.blocks, key=lambda item:item.offset):
            if block.offset < physical_end:
                raise PayloadError("compressed blocks overlap")
            physical_end = block.offset + block.csize
        by_block = {}
        for entry in self.files:
            if entry.size:
                by_block.setdefault(entry.block, []).append(entry)
        for entries in by_block.values():
            previous = None
            for entry in sorted(entries, key=lambda item:(item.offset,item.size)):
                if previous and entry.offset < previous.offset + previous.size:
                    if (entry.offset,entry.size,entry.sha256) != (previous.offset,previous.size,previous.sha256):
                        raise PayloadError("payload file ranges overlap")
                else:
                    previous = entry

    def components(self) -> List[str]:
        seen: List[str] = []
        for entry in self.files:
            if entry.component not in seen:
                seen.append(entry.component)
        return seen

    def size_of(self, components: Iterable[str]) -> int:
        wanted = set(components)
        return sum(f.size for f in self.files if f.component in wanted)

    def top_entries(self, component: str) -> List[str]:
        """First path segments a component owns inside the install directory."""
        names: List[str] = []
        for entry in self.files:
            if entry.component == component:
                head = entry.path.split("/", 1)[0]
                if head not in names:
                    names.append(head)
        for comp, path in self.dirs:
            if comp == component:
                head = path.split("/", 1)[0]
                if head not in names:
                    names.append(head)
        return names

def safe_relpath(path: str) -> str:
    text = str(path).replace("\\", "/")
    segments = text.split("/")
    if not text or text.startswith("/") or ":" in text or any(
        part in ("", ".", "..") or part.rstrip(" .") != part or
        any(ord(ch) < 32 or ch in '<>"|?*' for ch in part) or
        re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?",part,re.I)
        for part in segments
    ):
        raise PayloadError(f"unsafe payload path: {path!r}")
    return "/".join(segments)

def _filters(kind: str, preset: int) -> list:
    lzma2 = {"id": lzma.FILTER_LZMA2, "preset": preset}
    return [{"id": lzma.FILTER_X86}, lzma2] if kind == "x86" else [lzma2]

def _compress(data: bytes, kind: str, preset: int) -> bytes:
    return lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, filters=_filters(kind, preset))

def _hash_file(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size

def collect_tree(source: Path, dest_prefix: str, component: str) -> Tuple[List[Tuple[FileEntry, Path]], List[Tuple[str, str]]]:
    """List every file under *source* as ``dest_prefix/relative`` entries."""
    files: List[Tuple[FileEntry, Path]] = []
    empty_dirs: List[Tuple[str, str]] = []
    source = Path(source)
    if not source.is_dir():
        raise PayloadError(f"payload source folder not found: {source}")
    for root, dirnames, filenames in os.walk(source):
        dirnames.sort()
        root_path = Path(root)
        rel_root = root_path.relative_to(source).as_posix()
        if not dirnames and not filenames and rel_root != ".":
            empty_dirs.append((component, safe_relpath(f"{dest_prefix}/{rel_root}".strip("/"))))
        for name in sorted(filenames):
            full = root_path / name
            rel = name if rel_root == "." else f"{rel_root}/{name}"
            dest = safe_relpath(f"{dest_prefix}/{rel}".strip("/") if dest_prefix else rel)
            digest, size = _hash_file(full)
            files.append((FileEntry(path=dest, component=component, size=size, sha256=digest), full))
    return files, empty_dirs

def build_payload(sources: Sequence[Tuple[str, Path, str]], meta: Dict[str, object], out: BinaryIO, *,
                  preset: int = 6 | lzma.PRESET_EXTREME, workers: Optional[int] = None,
                  block_target: int = BLOCK_TARGET, cache_dir: Optional[Path] = None,
                  log: Callable[[str], None] = lambda _m: None) -> Index:
    """Write blocks + index + trailer of the given ``(component, folder, dest_prefix)`` trees to *out*.

    *out* must be positioned where the payload starts (the end of the stub).
    *cache_dir* (round 36): compressed blocks are kept there under the hash of their exact contents (member
    paths, sizes, SHA-256, filters, preset), so a rebuild recompresses only the blocks whose files changed —
    the LZMA of ~400 MB of unchanged programs was most of the installer build time. Blocks not used by this
    build are removed from the cache afterwards.
    """
    collected: List[Tuple[FileEntry, Path]] = []
    dirs: List[Tuple[str, str]] = []
    for component, folder, prefix in sources:
        files, empty = collect_tree(Path(folder), prefix, component)
        if not files:
            raise PayloadError(f"payload source folder is empty: {folder}")
        collected += files
        dirs += empty
    seen = set()
    for entry, _ in collected:
        key = entry.path.casefold()
        if key in seen:
            raise PayloadError(f"duplicate payload path: {entry.path}")
        seen.add(key)

    def kind(entry: FileEntry) -> str:
        return "x86" if PurePosixPath(entry.path).suffix.lower() in BINARY_SUFFIXES else "plain"

    aliases = {}
    canonical = {}
    unique = []
    for i, (entry, _) in enumerate(collected):
        key = (entry.size, entry.sha256, kind(entry))
        first = canonical.get(key)
        if first is None:
            canonical[key] = i
            unique.append(i)
        else:
            aliases[i] = first
    order = sorted(unique, key=lambda i: (
        collected[i][0].component, kind(collected[i][0]),
        PurePosixPath(collected[i][0].path).suffix.lower(), collected[i][0].path))
    plans: List[Tuple[str, List[int]]] = []
    current: List[int] = []
    current_kind = ""
    current_comp = ""
    current_size = 0
    for i in order:
        entry = collected[i][0]
        k = kind(entry)
        if current and (k != current_kind or entry.component != current_comp
                        or current_size + entry.size > block_target):
            plans.append((current_kind, current))
            current, current_size = [], 0
        current.append(i)
        current_kind, current_comp = k, entry.component
        current_size += entry.size
    if current:
        plans.append((current_kind, current))

    def block_key(plan: Tuple[str, List[int]]) -> str:
        digest = hashlib.sha256(f"{FORMAT_VERSION}|{plan[0]}|{preset}|".encode("utf-8"))
        for i in plan[1]:
            entry = collected[i][0]
            digest.update(f"{entry.path}\0{entry.size}\0{entry.sha256}\n".encode("utf-8"))
        return digest.hexdigest()

    used: set = set()
    hits = [0]

    def make_block(plan: Tuple[str, List[int]]) -> Tuple[bytes, int]:
        cached = None
        if cache_dir is not None:
            key = block_key(plan)
            used.add(key + ".xz")
            cached = Path(cache_dir) / (key + ".xz")
            try:
                data = cached.read_bytes()
                if not valid_cached(data, plan):
                    raise OSError("damaged block cache")
                hits[0] += 1
                return data, sum(collected[i][0].size for i in plan[1])
            except OSError:
                pass
        compressed, raw_size = _make_block(plan)
        if cached is not None:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                part = cached.with_name(cached.name + f".{os.getpid()}.part")
                part.write_bytes(compressed)
                os.replace(part, cached)
            except OSError:
                pass
        return compressed, raw_size

    def valid_cached(data, plan):
        try:
            with lzma.LZMAFile(io.BytesIO(data)) as stream:
                for i in plan[1]:
                    entry = collected[i][0]
                    remaining, digest = entry.size, hashlib.sha256()
                    while remaining:
                        piece = stream.read(min(CHUNK, remaining))
                        if not piece:
                            return False
                        digest.update(piece)
                        remaining -= len(piece)
                    if digest.hexdigest() != entry.sha256:
                        return False
                return not stream.read(1)
        except (lzma.LZMAError, EOFError, OSError):
            return False

    def _make_block(plan: Tuple[str, List[int]]) -> Tuple[bytes, int]:
        kind, members = plan
        compressed = io.BytesIO()
        compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, filters=_filters(kind,preset))
        raw_size = 0
        for i in members:
            entry, path = collected[i]
            digest, size = hashlib.sha256(), 0
            with path.open('rb') as stream:
                for piece in iter(lambda:stream.read(CHUNK), b''):
                    size += len(piece)
                    digest.update(piece)
                    compressed.write(compressor.compress(piece))
            if size != entry.size or digest.hexdigest() != entry.sha256:
                raise PayloadError(f"file changed while packing: {path}")
            raw_size += size
        compressed.write(compressor.flush())
        return compressed.getvalue(), raw_size

    workers = workers or max(1, min(os.cpu_count() or 2, 8))
    blocks: List[Block] = []
    entries: List[FileEntry] = []
    start = out.tell()
    written = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:

        for number, (plan, (compressed, raw_size)) in enumerate(zip(plans, pool.map(make_block, plans))):
            if PYI_COOKIE_MAGIC in compressed:
                raise PayloadError("compressed block contains the PyInstaller cookie magic; rebuild with another preset")
            blocks.append(Block(offset=written, csize=len(compressed), size=raw_size, filters=plan[0]))
            out.write(compressed)
            offset = 0
            for i in plan[1]:
                entry = collected[i][0]
                entry.block, entry.offset = number, offset
                offset += entry.size
                entries.append(entry)
            written += len(compressed)
            log(f"block {number + 1}/{len(plans)}: {raw_size} -> {len(compressed)} bytes")
    if cache_dir is not None:
        log(f"block cache: {hits[0]}/{len(plans)} blocks reused")
        try:
            for stale in Path(cache_dir).glob("*.xz"):
                if stale.name not in used:
                    stale.unlink()
        except OSError:
            pass
    for duplicate, original in aliases.items():
        entry, first = collected[duplicate][0], collected[original][0]
        entry.block, entry.offset = first.block, first.offset
        entries.append(entry)
    entries.sort(key=lambda e: (e.block, e.offset, e.size))
    meta = dict(meta)
    meta.setdefault("total_size", sum(e.size for e in entries))
    meta["stored_size"] = sum(block.size for block in blocks)
    meta["deduplicated_bytes"] = sum(e.size for e in entries) - meta["stored_size"]
    meta.setdefault("file_count", len(entries))
    index = Index(meta=meta, files=entries, blocks=blocks, dirs=dirs)
    data = index.to_bytes()
    if PYI_COOKIE_MAGIC in data:
        raise PayloadError("payload index contains the PyInstaller cookie magic")
    out.write(data)
    payload_len = written + len(data)
    out.write(TRAILER.pack(MAGIC, payload_len, len(data), FORMAT_VERSION, 0, MAGIC_END))
    assert out.tell() - start == payload_len + TRAILER.size
    return index

def assemble(stub: Path, sources: Sequence[Tuple[str, Path, str]], meta: Dict[str, object], output: Path,
             **kwargs) -> Index:
    """Copy the stub to *output* and append the payload (atomic replace)."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    stub_bytes = Path(stub).read_bytes()
    if MAGIC in stub_bytes:
        raise PayloadError("the stub already carries a payload")
    split = pyinstaller_archive_offset(stub_bytes)
    head, tail = (stub_bytes[:split], stub_bytes[split:]) if split is not None else (stub_bytes, b"")
    try:
        with temporary.open("wb") as out:
            out.write(head)
            index = build_payload(sources, meta, out, **kwargs)
            out.write(tail)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return index

class _Region(io.RawIOBase):
    """Read-only view of ``[start, start+length)`` of a file."""

    def __init__(self, handle: BinaryIO, start: int, length: int) -> None:
        super().__init__()
        self._h, self._start, self._len, self._pos = handle, start, length, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        remaining = self._len - self._pos
        if remaining <= 0:
            return 0
        want = min(len(buffer), remaining)
        self._h.seek(self._start + self._pos)
        data = self._h.read(want)
        buffer[:len(data)] = data
        self._pos += len(data)
        return len(data)

PYI_COOKIE = struct.Struct(">8sIIII64s")

def pyinstaller_archive_offset(data: bytes) -> Optional[int]:
    """Start of the PyInstaller CArchive of an *unsigned* one-file exe, or None."""
    pos = data.rfind(PYI_COOKIE_MAGIC)
    if pos < 0 or pos + PYI_COOKIE.size != len(data):
        return None
    pkg_length = PYI_COOKIE.unpack_from(data, pos)[1]
    start = pos + PYI_COOKIE.size - pkg_length
    return start if 0 < start < pos else None

@dataclass
class Located:
    payload_start: int
    payload_len: int
    index_len: int
    trailer_pos: int
    archive_end: int = 0

    @property
    def trailer_end(self) -> int:
        return self.trailer_pos + TRAILER.size

def _check_trailer(raw: bytes, trailer_pos: int) -> Optional[Located]:
    magic, payload_len, index_len, version, _r, end = TRAILER.unpack(raw)
    if magic != MAGIC or end != MAGIC_END:
        return None
    if version not in SUPPORTED_FORMATS:
        raise PayloadError(f"unsupported payload format {version}")
    start = trailer_pos - payload_len
    if start < 0 or index_len > payload_len:
        raise PayloadError("payload trailer is inconsistent (truncated download?)")
    return Located(start, payload_len, index_len, trailer_pos)

def locate(handle: BinaryIO) -> Optional[Located]:
    """Find the trailer (tolerating a signature appended at the end). None = no payload."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    window = min(size, TRAILER_SEARCH)
    handle.seek(size - window)
    tail = handle.read(window)

    cookie = tail.rfind(PYI_COOKIE_MAGIC)
    if cookie >= 0 and cookie + PYI_COOKIE.size <= len(tail):
        cookie_pos = size - window + cookie
        pkg_length = PYI_COOKIE.unpack_from(tail, cookie)[1]
        archive_start = cookie_pos + PYI_COOKIE.size - pkg_length
        trailer_pos = archive_start - TRAILER.size
        if trailer_pos >= 0:
            handle.seek(trailer_pos)
            raw = handle.read(TRAILER.size)
            if len(raw) == TRAILER.size:
                found = _check_trailer(raw, trailer_pos)
                if found is not None:
                    found.archive_end = cookie_pos + PYI_COOKIE.size
                    return found

    pos = tail.rfind(MAGIC)
    while pos >= 0:
        if pos + TRAILER.size <= len(tail):
            magic, payload_len, index_len, version, _r, end = TRAILER.unpack_from(tail, pos)
            if magic == MAGIC and end == MAGIC_END:
                trailer_pos = size - window + pos
                if version not in SUPPORTED_FORMATS:
                    raise PayloadError(f"unsupported payload format {version}")
                start = trailer_pos - payload_len
                if start < 0 or index_len > payload_len:
                    raise PayloadError("payload trailer is inconsistent (truncated download?)")
                return Located(start, payload_len, index_len, trailer_pos)
        pos = tail.rfind(MAGIC, 0, pos)
    return None

class Payload:
    """Opened payload of a Setup exe."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._h = self.path.open("rb")
        try:
            found = locate(self._h)
            if found is None:
                raise PayloadError("no payload in this file")
            self.loc = found
            self._h.seek(found.trailer_pos - found.index_len)
            if found.index_len > MAX_INDEX_BYTES:
                raise PayloadError("compressed payload index exceeds its size limit")
            self.index = Index.from_bytes(self._h.read(found.index_len))
            block_limit = found.payload_len - found.index_len
            if any(block.offset + block.csize > block_limit for block in self.index.blocks):
                raise PayloadError("compressed block extends beyond the payload region")
        except BaseException:
            self._h.close()
            raise

    @classmethod
    def open_optional(cls, path: Path) -> Optional["Payload"]:
        try:
            with Path(path).open("rb") as handle:
                if locate(handle) is None:
                    return None
        except OSError:
            return None
        return cls(path)

    def close(self) -> None:
        self._h.close()

    def __enter__(self) -> "Payload":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def meta(self) -> Dict[str, object]:
        return self.index.meta

    def read_stub(self) -> bytes:
        """The Setup program without its payload (= the uninstaller), without a signature table."""
        self._h.seek(0)
        head = self._h.read(self.loc.payload_start)
        if not self.loc.archive_end:
            return head
        self._h.seek(self.loc.trailer_end)
        return head + self._h.read(self.loc.archive_end - self.loc.trailer_end)

    def _block_stream(self, block: Block) -> Iterator[bytes]:
        region = _Region(self._h, self.loc.payload_start + block.offset, block.csize)
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        produced = 0
        while True:
            chunk = region.read(CHUNK)
            if not chunk:
                break
            try:
                data = decompressor.decompress(chunk, CHUNK)
            except (lzma.LZMAError, EOFError) as exc:
                raise PayloadError(f"payload block is damaged: {exc}") from None
            while data:
                produced += len(data)
                yield data
                if decompressor.needs_input or decompressor.eof:
                    break
                try:
                    data = decompressor.decompress(b"", CHUNK)
                except (lzma.LZMAError, EOFError) as exc:
                    raise PayloadError(f"payload block is damaged: {exc}") from None
        if not decompressor.eof or produced != block.size or decompressor.unused_data:
            raise PayloadError("payload block is truncated or damaged")

    def iter_files(self, components: Iterable[str],
                   cancelled: Callable[[], bool] = lambda: False) -> Iterator[Tuple[FileEntry, Iterator[bytes]]]:
        """Yield ``(entry, chunks)`` for the wanted components in payload order.

        Each chunk iterator must be consumed before advancing. The SHA-256 of
        every file is verified; a mismatch raises :class:`PayloadError`.
        """
        wanted = set(components)
        by_block: Dict[int, List[FileEntry]] = {}
        for entry in self.index.files:
            if entry.component in wanted:
                by_block.setdefault(entry.block, []).append(entry)
        for number in sorted(by_block):
            if cancelled():
                return
            block = self.index.blocks[number]
            members = sorted(by_block[number], key=lambda e:(e.offset,e.size,e.sha256))
            stream = self._block_stream(block)
            buffer = bytearray()
            position = 0

            def take(length: int) -> Iterator[bytes]:
                nonlocal buffer, position
                remaining = length
                while remaining:
                    if cancelled():
                        return
                    if not buffer:
                        try:
                            buffer = bytearray(next(stream))
                        except StopIteration:
                            raise PayloadError("payload block ended early") from None
                    piece = bytes(buffer[:remaining])
                    del buffer[:len(piece)]
                    position += len(piece)
                    remaining -= len(piece)
                    yield piece

            for _, grouped in groupby(members,key=lambda e:(e.offset,e.size,e.sha256)):
                group = list(grouped)
                first = group[0]
                if first.offset < position:
                    raise PayloadError("payload index is inconsistent")
                for _ in take(first.offset - position):
                    pass
                spool = tempfile.SpooledTemporaryFile(max_size=2*CHUNK) if len(group)>1 else None
                try:
                    def chunks():
                        digest = hashlib.sha256()
                        for piece in take(first.size):
                            digest.update(piece)
                            if spool is not None:
                                spool.write(piece)
                            yield piece
                        if not cancelled() and digest.hexdigest() != first.sha256:
                            raise PayloadError(f"checksum mismatch: {first.path}")
                    initial = chunks()
                    yield first, initial
                    for _ in initial:
                        pass
                    if cancelled():
                        return
                    for entry in group[1:]:
                        spool.seek(0)
                        def replay(entry=entry):
                            digest = hashlib.sha256()
                            while not cancelled():
                                piece = spool.read(CHUNK)
                                if not piece:
                                    break
                                digest.update(piece)
                                yield piece
                            if not cancelled() and digest.hexdigest() != entry.sha256:
                                raise PayloadError(f"checksum mismatch: {entry.path}")
                        repeated = replay()
                        yield entry, repeated
                        for _ in repeated:
                            pass
                        if cancelled():
                            return
                finally:
                    if spool is not None:
                        spool.close()

            for _ in stream:
                pass

def strip_signature_directory(stub: bytes) -> bytes:
    """Clear the PE security directory if it points past the end of *stub*.

    The uninstaller is the Setup exe without its payload. When the release
    Setup was Authenticode-signed, its header points at a certificate table
    that lies beyond the cut; a dangling entry would make Windows treat the
    copy as *badly signed* instead of simply unsigned.
    """
    data = bytearray(stub)
    try:
        if data[:2] != b"MZ":
            return bytes(data)
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return bytes(data)
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        directories = optional + (112 if magic == 0x20B else 96)
        security = directories + 4 * 8
        address, size = struct.unpack_from("<II", data, security)
        if address and address + size > len(data):
            struct.pack_into("<II", data, security, 0, 0)
    except struct.error:
        pass
    return bytes(data)
