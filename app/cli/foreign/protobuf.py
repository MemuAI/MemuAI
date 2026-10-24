"""A schema-less reader for Protocol Buffers, enough to pull text out of a message nobody gave us a schema for.

Antigravity keeps its conversations as protobuf messages inside SQLite. There is no ``.proto`` file to compile,
so a message is decoded by its wire format alone: a tree of ``(field number, kind, value)``. A length-delimited
field is a nested message when its bytes parse as one, otherwise text when they are printable UTF-8, otherwise
raw bytes. Which field means what is learnt by looking (see ``agy.py``), and everything that does not match is
simply ignored — a newer version that moves a field loses a detail, it does not break the reader.
"""
from __future__ import annotations

from typing import Any, Iterator, List, Optional, Sequence, Tuple

Node = Tuple[int, str, Any]            # (field number, "v" | "str" | "msg" | "bytes" | "f32" | "f64", value)
MAX_DEPTH = 8
MAX_BYTES = 8_000_000


def _varint(buffer: bytes, index: int) -> Tuple[int, int]:
    shift = 0
    result = 0
    while True:
        byte = buffer[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def _printable(text: str) -> bool:
    return all(char.isprintable() or char in "\n\r\t" for char in text)


def decode(buffer: bytes, depth: int = 0) -> Optional[List[Node]]:
    """The fields of ``buffer`` as a list, or None when it is not a valid message."""
    if not buffer or len(buffer) > MAX_BYTES:
        return None
    out: List[Node] = []
    index = 0
    try:
        while index < len(buffer):
            key, index = _varint(buffer, index)
            field, wire = key >> 3, key & 7
            if field == 0:
                return None
            if wire == 0:
                value, index = _varint(buffer, index)
                out.append((field, "v", value))
            elif wire == 1:
                out.append((field, "f64", buffer[index:index + 8]))
                index += 8
            elif wire == 5:
                out.append((field, "f32", buffer[index:index + 4]))
                index += 4
            elif wire == 2:
                length, index = _varint(buffer, index)
                chunk = buffer[index:index + length]
                index += length
                if len(chunk) != length:
                    return None
                out.append(_chunk(field, chunk, depth))
            else:
                return None
    except (IndexError, ValueError):
        return None
    return out


def _plausible(nested: Sequence[Node]) -> bool:
    """Does a parse look like a real message rather than text that happens to parse? Short ASCII words often do
    (``echo hi`` is ``f32 "cho "`` + ``varint 105``), so a message must hold a length-delimited field and no
    fixed-width ones — which the tag bytes of letters produce almost at random."""
    has_chunk = False
    stack = [nested]
    while stack:
        for _field, kind, value in stack.pop():
            if kind in ("f32", "f64"):
                return False
            if kind in ("str", "msg", "bytes"):
                has_chunk = True
            if kind == "msg":
                stack.append(value)
    return has_chunk


def _chunk(field: int, chunk: bytes, depth: int) -> Node:
    nested = decode(chunk, depth + 1) if depth < MAX_DEPTH and chunk else None
    try:
        text = chunk.decode("utf-8")
        printable = _printable(text)
    except UnicodeDecodeError:
        text, printable = "", False
    if printable and text:
        if nested and _plausible(nested):
            return field, "msg", nested
        return field, "str", text
    if nested:
        return field, "msg", nested
    return field, "bytes", chunk


def walk(tree: Sequence[Node], path: Tuple[int, ...] = ()) -> Iterator[Tuple[Tuple[int, ...], str, Any]]:
    """Every leaf of the tree with the path of field numbers that leads to it."""
    for field, kind, value in tree:
        here = path + (field,)
        if kind == "msg":
            yield from walk(value, here)
        else:
            yield here, kind, value


def strings(tree: Sequence[Node]) -> Iterator[Tuple[str, str]]:
    """``("20.7.3", "text")`` for every text leaf."""
    for path, kind, value in walk(tree):
        if kind == "str":
            yield ".".join(str(part) for part in path), value


def first(tree: Sequence[Node], *paths: str) -> str:
    """The first text found at any of the dotted ``paths`` (``"20.7.2"``), or ""."""
    wanted = set(paths)
    for path, text in strings(tree):
        if path in wanted:
            return text
    return ""


def longest(tree: Sequence[Node], prefix: str, *, skip: Sequence[str] = ()) -> str:
    """The longest text under a path prefix (``"28"``) that is not one of ``skip``."""
    best = ""
    for path, text in strings(tree):
        if (path == prefix or path.startswith(prefix + ".")) and text not in skip and len(text) > len(best):
            best = text
    return best
