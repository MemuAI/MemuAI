"""Plain-text I/O for MemuNote: encoding and line-ending detection, safe saving.

Only the first ``SAMPLE`` bytes are inspected, so a 1 GB log is classified in
microseconds; the page then decodes the raw bytes itself with ``TextDecoder``
(no copy through Python for big files). Saving goes the other way: UTF-8 text
from the page is re-encoded here into the file's encoding and line endings.

Detection order: BOM → UTF-16/32 without BOM (zero-byte pattern) → strict
UTF-8 → single-byte Cyrillic (cp1251 / koi8-r / cp866 by letter statistics) →
cp1252. No third-party detector: ``chardet`` would add 1 MB and 100 ms.
"""
from __future__ import annotations

import codecs
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

SAMPLE = 256 * 1024

ENCODINGS = [
    ("utf-8", "UTF-8", "utf-8"),
    ("utf-8-sig", "UTF-8 BOM", "utf-8"),
    ("utf-16-le", "UTF-16 LE", "utf-16le"),
    ("utf-16-be", "UTF-16 BE", "utf-16be"),
    ("cp1251", "Windows-1251", "windows-1251"),
    ("koi8-r", "KOI8-R", "koi8-r"),
    ("cp866", "CP866 (DOS)", "ibm866"),
    ("cp1252", "Windows-1252", "windows-1252"),
    ("iso-8859-1", "ISO-8859-1", "iso-8859-1"),
    ("iso-8859-5", "ISO-8859-5", "iso-8859-5"),
    ("mac-cyrillic", "Mac Cyrillic", "x-mac-cyrillic"),
    ("cp1250", "Windows-1250", "windows-1250"),
    ("cp1253", "Windows-1253", "windows-1253"),
    ("cp1254", "Windows-1254", "windows-1254"),
    ("cp1255", "Windows-1255", "windows-1255"),
    ("cp1256", "Windows-1256", "windows-1256"),
    ("cp1257", "Windows-1257", "windows-1257"),
    ("gb18030", "GB18030", "gb18030"),
    ("big5", "Big5", "big5"),
    ("shift_jis", "Shift-JIS", "shift_jis"),
    ("euc-kr", "EUC-KR", "euc-kr"),
]
ENCODING_NAMES = {row[0] for row in ENCODINGS}
WEB_NAMES = {row[0]: row[2] for row in ENCODINGS}

BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]

_COMMON_CYR = set("оеаинтсрвлкмдпуяыьгзбчйхжшюцщэфъёіїєґ")
_CYR_PAIRS = set("ст но то на ен ов ни ра во ко ро по ос пр ре го ал ор от ет ли та ер ть ес ва ол ри ит ин ел до тр ми ве ви за ак ма ле се ла ан те не ил ог ти ки ка об ат ый ая ие ий ых ар др ру ир ав ус сл лу уг".split())

@dataclass
class TextInfo:
    encoding: str
    bom: bool
    eol: str
    binary: bool
    confidence: float
    damaged: int = 0

    @property
    def web_encoding(self) -> str:
        if self.encoding in ("utf-32-le", "utf-32-be"):
            return ""
        return WEB_NAMES.get(self.encoding, self.encoding)

def normalize_encoding(name: str) -> str:
    raw = str(name or "").strip().lower().replace("_", "-")
    aliases = {"utf8": "utf-8", "utf-8bom": "utf-8-sig", "utf-8-bom": "utf-8-sig", "windows-1251": "cp1251",
               "windows-1252": "cp1252", "utf-16le": "utf-16-le", "utf-16be": "utf-16-be", "utf-16": "utf-16-le",
               "ibm866": "cp866", "dos": "cp866", "latin-1": "iso-8859-1", "latin1": "iso-8859-1",
               "koi8r": "koi8-r", "shift-jis": "shift_jis", "sjis": "shift_jis", "x-mac-cyrillic": "mac-cyrillic"}
    raw = aliases.get(raw, raw)
    if raw.startswith("windows-125"):
        raw = "cp" + raw[8:]
    return raw if raw in ENCODING_NAMES or raw in ("utf-32-le", "utf-32-be") else "utf-8"

def _looks_utf16(sample: bytes) -> Optional[str]:
    if len(sample) < 4:
        return None
    even = sample[0::2][:4096]
    odd = sample[1::2][:4096]
    zeros_even = even.count(0) / max(len(even), 1)
    zeros_odd = odd.count(0) / max(len(odd), 1)
    if zeros_odd > 0.3 and zeros_even < 0.05:
        return "utf-16-le"
    if zeros_even > 0.3 and zeros_odd < 0.05:
        return "utf-16-be"

    for high, low, name in ((odd, even, "utf-16-le"), (even, odd, "utf-16-be")):
        if len(high) < 16:
            continue
        top_value = max(range(0x00, 0x06), key=high.count)
        share = (high.count(top_value) + (high.count(0) if top_value else 0)) / len(high)
        if share < 0.6 or high.count(top_value) / len(high) < 0.3 or len(set(low)) < 8:
            continue
        try:
            text = sample[: len(sample) // 2 * 2].decode(name)
        except UnicodeDecodeError:
            continue
        good = sum(1 for c in text if c.isalpha() or c.isspace() or c in ".,;:!?-()\"'0123456789")
        if good / max(len(text), 1) > 0.85:
            return name
    return None

def _is_binary(sample: bytes) -> bool:
    if not sample:
        return False
    head = sample[:8192]
    if b"\x00" in head:
        return True
    control = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13, 27, 8))
    return control / len(head) > 0.10

def _utf8_ok(sample: bytes, complete: bool) -> bool:
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:

        return (not complete) and exc.start >= len(sample) - 3 and exc.reason == "unexpected end of data"

_TYPOGRAPHY = set("«»—–…„“”‘’№°±§©®™•·\u00a0")

def _cyr_score(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = [c for c in letters if "\u0400" <= c <= "\u04ff"]
    if not cyr:
        return 0.0
    common = sum(1 for c in cyr if c.lower() in _COMMON_CYR)
    lower = sum(1 for c in cyr if c.islower())
    folded = text.lower()
    pairs = [folded[i:i + 2] for i in range(len(folded) - 1)
             if folded[i].isalpha() and folded[i + 1].isalpha()]
    pair_score = sum(p in _CYR_PAIRS for p in pairs) / max(1, len(pairs))

    weird = sum(1 for c in text if ord(c) > 0x7F and not c.isalpha() and not c.isspace() and c not in _TYPOGRAPHY)
    penalty = 1.0 - min(0.6, 3.0 * weird / len(letters))

    return ((len(cyr) / len(letters)) * (common / len(cyr)) *
            (0.95 + 0.05 * lower / len(cyr)) * (0.75 + 0.25 * pair_score) * penalty)

def _bad_utf8(data: bytes, limit: int) -> list:
    """(start, end) of each invalid UTF-8 sequence, as "replace" / TextDecoder split them; at most *limit*."""
    runs, pos = [], 0
    while len(runs) < limit:
        try:
            data[pos:].decode("utf-8")
            break
        except UnicodeDecodeError as exc:
            runs.append((pos + exc.start, pos + exc.end))
            pos += exc.end
    return runs

def _utf8_damage(sample: bytes, complete: bool) -> int:
    """Round 34: invalid sequences in text that is otherwise clearly UTF-8 (0 = not that case). One broken byte in
    a UTF-8 file made the whole file open as cp1252 mojibake; a real cp1251/koi8 file has (almost) no valid UTF-8
    multi-byte characters, a damaged UTF-8 file has many and only a few bad ones."""
    if not complete:
        sample = sample[:max(0, len(sample) - 3)]
    bad = _bad_utf8(sample, 200)
    if not bad or len(bad) >= 200:
        return 0
    multibyte = sum(1 for c in sample.decode("utf-8", errors="ignore") if ord(c) > 0x7F)
    return len(bad) if multibyte >= 3 and multibyte >= 4 * len(bad) else 0

def _single_byte(sample: bytes) -> Tuple[str, float]:
    high = sum(1 for b in sample if b >= 0x80)
    if not high:
        return "utf-8", 1.0
    best, best_score = "cp1252", 0.0
    for name in ("cp1251", "koi8-r", "cp866", "mac-cyrillic"):
        try:
            text = sample.decode(name, errors="strict")
        except UnicodeDecodeError:
            continue
        score = _cyr_score(text)
        if score > best_score + 0.02:
            best, best_score = name, score
    if best_score >= 0.45:
        return best, min(0.95, best_score)
    return "cp1252", 0.4

def detect_eol(sample: str | bytes) -> str:
    if isinstance(sample, bytes):
        crlf, lf, cr = sample.count(b"\r\n"), sample.count(b"\n"), sample.count(b"\r")
    else:
        crlf, lf, cr = sample.count("\r\n"), sample.count("\n"), sample.count("\r")
    lone_lf, lone_cr = lf - crlf, cr - crlf
    kinds = [k for k, n in (("crlf", crlf), ("lf", lone_lf), ("cr", lone_cr)) if n]
    if not kinds:
        return "crlf" if os.name == "nt" else "lf"
    if len(kinds) > 1:

        counts = {"crlf": crlf, "lf": lone_lf, "cr": lone_cr}
        top = max(counts, key=counts.get)
        return top if counts[top] >= 0.98 * (crlf + lone_lf + lone_cr) else "mixed"
    return kinds[0]

def majority_eol(sample: str | bytes) -> str:
    """Round 34: the most common line ending — what a "mixed" file is saved with (was: the default EOL setting,
    so a mostly-CRLF file became LF on Linux / a mostly-LF file became CRLF on Windows)."""
    nl, cr = ("\n", "\r") if isinstance(sample, str) else (b"\n", b"\r")
    crlf = sample.count(cr + nl)
    counts = {"crlf": crlf, "lf": sample.count(nl) - crlf, "cr": sample.count(cr) - crlf}
    top = max(counts, key=counts.get)
    return top if counts[top] else ""

def detect_bytes(sample: bytes, complete: bool = True) -> TextInfo:
    for bom, name in BOMS:
        if sample.startswith(bom):
            eol_sample = sample[len(bom):]
            try:
                eol = detect_eol(eol_sample.decode(name.replace("-sig", ""), errors="ignore"))
            except LookupError:
                eol = "crlf"
            return TextInfo(name, True, eol, False, 1.0)
    utf16 = _looks_utf16(sample)
    if utf16:
        text = sample.decode(utf16, errors="ignore")
        return TextInfo(utf16, False, detect_eol(text), False, 0.8)
    binary = _is_binary(sample)
    if _utf8_ok(sample, complete):
        return TextInfo("utf-8", False, detect_eol(sample), binary, 1.0 if not binary else 0.3)
    damaged = _utf8_damage(sample, complete)
    if damaged:
        return TextInfo("utf-8", False, detect_eol(sample), binary, 0.6, damaged)
    name, confidence = _single_byte(sample)
    return TextInfo(name, False, detect_eol(sample), binary, confidence)

def detect_file(path: os.PathLike | str) -> TextInfo:
    with open(path, "rb") as handle:
        sample = handle.read(SAMPLE)
        complete = len(sample) < SAMPLE or not handle.read(1)
    return detect_bytes(sample, complete)

def decode(data: bytes, encoding: str) -> str:
    name = normalize_encoding(encoding)
    if name in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
        for bom, bom_name in BOMS:
            if bom_name == name and data.startswith(bom):
                data = data[len(bom):]
    return data.decode(name, errors="replace")

def read_text(path: os.PathLike | str, encoding: str = "") -> Tuple[str, TextInfo]:
    data = Path(path).read_bytes()
    info = detect_bytes(data[:SAMPLE], len(data) <= SAMPLE)
    if encoding:
        info.encoding = normalize_encoding(encoding)
        info.bom = info.encoding in ("utf-8-sig",) or (info.bom and info.encoding.startswith("utf-16"))
    return decode(data, info.encoding), info

_EOL_RE = re.compile(r"\r\n|\r|\n")

def convert_eol(text: str, eol: str) -> str:
    target = {"crlf": "\r\n", "lf": "\n", "cr": "\r"}.get(eol)
    if target is None:
        return text
    if "\r" not in text:
        return text if target == "\n" else text.replace("\n", target)
    return _EOL_RE.sub(target, text)

def encode_utf8_bytes(raw: bytes, encoding: str, eol: str = "lf", bom: Optional[bool] = None) -> Optional[bytes]:
    """Fast path of :func:`encode` for UTF-8 bytes from the page saved as UTF-8 (no decode/encode round trip
    of a 50 MB string). None when it does not apply (another encoding, or ``\r`` already in the text)."""
    name = normalize_encoding(encoding)
    if name not in ("utf-8", "utf-8-sig") or b"\r" in raw:
        return None
    target = {"crlf": b"\r\n", "lf": b"\n", "cr": b"\r"}.get(eol)
    body = raw if target in (None, b"\n") else raw.replace(b"\n", target)
    want_bom = (name == "utf-8-sig") if bom is None else bool(bom)
    return (codecs.BOM_UTF8 + body) if want_bom else body

def encode(text: str, encoding: str, eol: str = "lf", bom: Optional[bool] = None) -> Tuple[bytes, int]:
    """Bytes for saving and the number of characters the encoding could not represent."""
    name = normalize_encoding(encoding)
    text = convert_eol(text, eol)
    lost = 0
    codec = "utf-8" if name == "utf-8-sig" else name
    try:
        body = text.encode(codec)
    except UnicodeEncodeError:

        body = text.encode(codec, errors="replace")
        question = "?".encode(codec)
        lost = max(0, body.count(question) - text.count("?"))
    want_bom = (name == "utf-8-sig") if bom is None else bool(bom)
    if want_bom:
        body = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE,
                "utf-32-le": codecs.BOM_UTF32_LE, "utf-32-be": codecs.BOM_UTF32_BE}.get(codec, b"") + body
    return body, lost

_REPLACED = "\ufffd".encode("utf-8")

def restore_damaged_utf8(data: bytes, original: bytes) -> bytes:
    """Round 34: put the original broken bytes of a damaged UTF-8 file back where the editor shows \ufffd, so
    saving it changes only what the user edited. Each \ufffd of the editor (TextDecoder = Python "replace", one per
    broken sequence) maps in order to its source: the bad bytes, or a real U+FFFD of the file. If the user removed
    or typed \ufffd characters the mapping is unknown and *data* is returned as is."""
    runs = _bad_utf8(original, 100000)
    if not runs or len(runs) >= 100000:
        return data
    sources, pos = [], 0
    for start, end in runs:
        sources += [_REPLACED] * original.count(_REPLACED, pos, start) + [original[start:end]]
        pos = end
    sources += [_REPLACED] * original.count(_REPLACED, pos)
    parts = data.split(_REPLACED)
    if len(parts) != len(sources) + 1:
        return data
    out = bytearray(parts[0])
    for src, part in zip(sources, parts[1:]):
        out += src
        out += part
    return bytes(out)

def atomic_write(path: os.PathLike | str, data: bytes, keep_backup: bool = False) -> None:
    """Write next to the target and ``os.replace`` it: a crash or a full disk never truncates the file.

    Keeps the file's permission bits; with *keep_backup* the previous version stays as ``name~``.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    try:
        mode = target.stat().st_mode & 0o777
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".mn-tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        if keep_backup and target.exists():
            backup = target.with_name(target.name + "~")
            try:
                if backup.exists():
                    backup.unlink()
                os.link(target, backup)
            except OSError:
                try:
                    import shutil
                    shutil.copy2(target, backup)
                except OSError:
                    pass
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
