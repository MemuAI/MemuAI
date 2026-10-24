"""Round 35: the query language of the PC-wide file search (:mod:`app.fsindex`) — pure Python, no I/O.

The syntax follows Everything (voidtools) so a query typed by the user or the model works the same with the
Everything backend and with MemuAI's own index:

* ``report 2026``            — every word must occur in the NAME (AND), case-insensitive, ``ё`` = ``е``;
* ``"annual report"``       — a phrase with spaces;
* ``*.txt``, ``IMG_??.jpg`` — wildcards match the WHOLE name;
* ``a|b`` (glued)           — OR of the two neighbours (as in Everything: ``report *.pdf|*.docx``);
  ``отчёт аренды | rental agreement`` (spaced ``|``) — alternatives of whole word groups; ``!tmp`` — NOT;
* ``ext:pdf;docx``          — by extension; ``type:image|video|audio|doc|archive|exe|code|text`` (also Russian);
* ``size:>5mb``, ``size:<100kb``, ``size:1mb..10mb``, ``size:empty|tiny|small|medium|large|huge|gigantic``;
* ``dm:today|yesterday|thisweek|lastweek|thismonth|lastmonth|thisyear|lastyear``, ``dm:7d`` / ``dm:3h``
  (within the last…), ``dm:2026-10-01``, ``dm:>2026-01``, ``dm:2026-01-01..2026-02-01``, ``dm:сегодня``;
* ``folder:`` / ``file:``   — only folders / only files; ``folder:name`` — folders whose name has «name»;
* ``in:C:\\Projects`` (``infolder:``, ``folder:<path>``) — inside a folder and its subfolders;
  ``parent:<path>`` — directly in it;
* ``path:src/app`` or any word with ``\\`` or ``/`` — matched against the FULL path;
* ``regex:^IMG_\\d+``       — a regular expression on the name;
* ``sort:name|date|size|path`` — order of the results (default — relevance).

:func:`parse` returns a :class:`Query`: ``groups`` — alternatives (OR), each an AND-list of :class:`Term`; a glued
``a|b`` (or single terms on both sides of a spaced ``|``) is one ``or`` term. A term knows how to test
one record (``lname, ldir, size, mtime, is_dir`` — lowered name, lowered normalized folder path) and which literal
strings could find it fast in the index blobs (:attr:`Term.lits`).
"""
from __future__ import annotations

import calendar
import fnmatch
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- normalization


def low(text: str) -> str:
    """Lowered text of the same length (the index keeps original and lowered names side by side)."""
    out = str(text).lower()
    if len(out) != len(text):
        out = "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in str(text))
    return out.replace("ё", "е")


def norm_path(path: str) -> str:
    """Lowered folder path with ``/`` separators and no trailing separator: ``C:\\Users\\`` → ``c:/users``."""
    text = low(str(path or "")).replace("\\", "/")
    while "//" in text[1:]:
        text = text[0] + text[1:].replace("//", "/")
    if len(text) > 1 and text.endswith("/"):
        text = text.rstrip("/")
    if text == "/":
        return ""
    return text


def full_lpath(ldir: str, lname: str) -> str:
    return ldir + "/" + lname


# ---------------------------------------------------------------- kinds

KINDS: Dict[str, Tuple[str, ...]] = {
    "image": ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif", "svg", "ico", "raw", "cr2",
              "nef", "arw", "dng", "psd", "avif", "jfif"),
    "video": ("mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m4v", "mpg", "mpeg", "3gp", "ts", "m2ts", "vob"),
    "audio": ("mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus", "aiff", "ape", "mid", "midi", "amr"),
    "doc": ("pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp",
            "epub", "djvu", "fb2", "pages", "numbers", "key", "tex"),
    "archive": ("zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "cab", "lz", "lzma", "img"),
    "exe": ("exe", "msi", "bat", "cmd", "ps1", "lnk", "appx", "msix", "com", "scr", "vbs", "jar", "apk"),
    "code": ("py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "cc", "cs", "java", "kt", "go", "rs", "rb",
             "php", "swift", "lua", "sh", "ps1", "sql", "html", "htm", "css", "scss", "json", "yaml", "yml", "toml",
             "xml", "ini", "vue", "svelte", "dart", "r", "m", "ipynb"),
    "text": ("txt", "md", "log", "csv", "json", "xml", "yaml", "yml", "ini", "cfg", "conf", "rtf"),
}
KIND_ALIASES = {
    "image": "image", "images": "image", "img": "image", "pic": "image", "picture": "image", "pictures": "image",
    "photo": "image", "photos": "image", "картинка": "image", "картинки": "image", "изображение": "image",
    "изображения": "image", "фото": "image", "фотографии": "image",
    "video": "video", "videos": "video", "movie": "video", "видео": "video", "фильм": "video", "фильмы": "video",
    "audio": "audio", "music": "audio", "sound": "audio", "аудио": "audio", "музыка": "audio", "звук": "audio",
    "doc": "doc", "docs": "doc", "document": "doc", "documents": "doc", "документ": "doc", "документы": "doc",
    "archive": "archive", "archives": "archive", "zip": "archive", "архив": "archive", "архивы": "archive",
    "exe": "exe", "executable": "exe", "program": "exe", "programs": "exe", "app": "exe", "программа": "exe",
    "программы": "exe",
    "code": "code", "source": "code", "код": "code",
    "text": "text", "текст": "text",
    "folder": "folder", "folders": "folder", "dir": "folder", "directory": "folder", "папка": "folder",
    "папки": "folder",
    "file": "file", "files": "file", "файл": "file", "файлы": "file",
}


def kind_of(name: str, is_dir: bool = False) -> str:
    """The icon group of a name: folder / image / video / audio / doc / archive / exe / code / text / file."""
    if is_dir:
        return "folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    for kind in ("image", "video", "audio", "archive", "exe", "code"):
        if ext in KINDS[kind]:
            return kind
    if ext in ("txt", "md", "log"):
        return "text"
    if ext in KINDS["doc"]:
        return "doc"
    return "file"


# ---------------------------------------------------------------- sizes / dates

_UNITS = {"": 1, "b": 1, "б": 1, "byte": 1, "bytes": 1, "k": 1024, "kb": 1024, "кб": 1024, "к": 1024,
          "m": 1024 ** 2, "mb": 1024 ** 2, "мб": 1024 ** 2, "м": 1024 ** 2,
          "g": 1024 ** 3, "gb": 1024 ** 3, "гб": 1024 ** 3, "г": 1024 ** 3,
          "t": 1024 ** 4, "tb": 1024 ** 4, "тб": 1024 ** 4}
SIZE_WORDS = {  # Everything's definitions
    "empty": (0, 0), "пустой": (0, 0), "пустые": (0, 0),
    "tiny": (0, 10 * 1024), "small": (10 * 1024 + 1, 100 * 1024), "medium": (100 * 1024 + 1, 1024 ** 2),
    "large": (1024 ** 2 + 1, 16 * 1024 ** 2), "huge": (16 * 1024 ** 2 + 1, 128 * 1024 ** 2),
    "gigantic": (128 * 1024 ** 2 + 1, None),
}
INF = float("inf")


class QueryError(ValueError):
    pass


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([a-zа-я]*)\s*", low(text))
    if not m or m.group(2) not in _UNITS:
        raise QueryError(f"bad size: {text}")
    return int(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2)])


def size_range(value: str) -> Tuple[float, float]:
    """``>5mb`` → (5mb+1, inf); ``1mb..10mb`` → (1mb, 10mb); ``tiny`` → …; inclusive bounds."""
    v = low(value).strip()
    if v in SIZE_WORDS:
        lo, hi = SIZE_WORDS[v]
        return float(lo), float(INF if hi is None else hi)
    for sep in ("..", "-"):
        if sep in v and not v.startswith(sep):
            a, b = v.split(sep, 1)
            return float(parse_size(a)), float(parse_size(b))
    m = re.match(r"(>=|<=|>|<|=)?(.*)$", v)
    op, rest = m.group(1) or "=", m.group(2)
    n = parse_size(rest)
    if op == ">":
        return n + 1.0, INF
    if op == ">=":
        return float(n), INF
    if op == "<":
        return 0.0, n - 1.0
    if op == "<=":
        return 0.0, float(n)
    return float(n), float(n)


_DATE_WORDS = {"сегодня": "today", "вчера": "yesterday", "этанеделя": "thisweek", "наэтойнеделе": "thisweek",
               "прошлаянеделя": "lastweek", "этотмесяц": "thismonth", "прошлыймесяц": "lastmonth",
               "этотгод": "thisyear", "прошлыйгод": "lastyear"}


def _day_start(ts: float) -> float:
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _month_start(year: int, month: int) -> float:
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return time.mktime((year, month, 1, 0, 0, 0, 0, 0, -1))


def _check_date(text: str, year: int, month: int, day: int) -> None:
    import datetime
    try:
        datetime.date(year, month, day)
    except ValueError:
        raise QueryError(f"bad date: {text}") from None


def _date_span(text: str, now: float) -> Tuple[float, float]:
    """One date → [start, end) in epoch seconds (local time)."""
    v = low(text).strip().replace(" ", "")
    v = _DATE_WORDS.get(v, v)
    today = _day_start(now)
    lt = time.localtime(now)
    if v == "today":
        return today, today + 86400
    if v == "yesterday":
        return today - 86400, today
    if v in ("thisweek", "currentweek"):
        start = today - lt.tm_wday * 86400
        return start, start + 7 * 86400
    if v in ("lastweek", "prevweek", "previousweek"):
        start = today - lt.tm_wday * 86400 - 7 * 86400
        return start, start + 7 * 86400
    if v in ("pastweek",):
        return now - 7 * 86400, now + 1
    if v in ("thismonth", "currentmonth"):
        return _month_start(lt.tm_year, lt.tm_mon), _month_start(lt.tm_year, lt.tm_mon + 1)
    if v in ("lastmonth", "prevmonth"):
        return _month_start(lt.tm_year, lt.tm_mon - 1), _month_start(lt.tm_year, lt.tm_mon)
    if v in ("thisyear", "currentyear"):
        return _month_start(lt.tm_year, 1), _month_start(lt.tm_year + 1, 1)
    if v in ("lastyear", "prevyear"):
        return _month_start(lt.tm_year - 1, 1), _month_start(lt.tm_year, 1)
    m = re.fullmatch(r"(?:last|past)?(\d+)(min|mins|minutes?|m|h|hours?|ч|d|days?|д|дн|w|weeks?|н|mo|months?|y|years?)", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        sec = (60 if unit in ("min", "mins", "minute", "minutes", "m") else
               3600 if unit in ("h", "hour", "hours", "ч") else
               86400 if unit in ("d", "day", "days", "д", "дн") else
               7 * 86400 if unit in ("w", "week", "weeks", "н") else
               30 * 86400 if unit in ("mo", "month", "months") else 365 * 86400)
        return now - n * sec, now + 1
    m = re.fullmatch(r"(\d{4})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?", v)
    if m:
        y, mo, d = int(m.group(1)), m.group(2), m.group(3)
        _check_date(text, y, int(mo or 1), int(d or 1))      # Round 36: «2026-13» was January 2027, «02-30» March 2
        if d:
            start = time.mktime((y, int(mo), int(d), 0, 0, 0, 0, 0, -1))
            return start, start + 86400
        if mo:
            return _month_start(y, int(mo)), _month_start(y, int(mo) + 1)
        return _month_start(y, 1), _month_start(y + 1, 1)
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", v)
    if m:
        _check_date(text, int(m.group(3)), int(m.group(2)), int(m.group(1)))
        start = time.mktime((int(m.group(3)), int(m.group(2)), int(m.group(1)), 0, 0, 0, 0, 0, -1))
        return start, start + 86400
    raise QueryError(f"bad date: {text}")


def date_range(value: str, now: Optional[float] = None) -> Tuple[float, float]:
    """``today`` / ``>2026-01`` / ``2026-01-01..2026-02-01`` → [lo, hi) epoch seconds."""
    now = time.time() if now is None else now
    v = low(value).strip()
    if ".." in v:
        a, b = v.split("..", 1)
        return (_date_span(a, now)[0] if a else 0.0), (_date_span(b, now)[1] if b else INF)
    m = re.match(r"(>=|<=|>|<|=)?(.*)$", v)
    op, rest = m.group(1) or "=", m.group(2)
    lo, hi = _date_span(rest, now)
    if op == ">":
        return hi, INF
    if op == ">=":
        return lo, INF
    if op == "<":
        return 0.0, lo
    if op == "<=":
        return 0.0, hi
    return lo, hi


# ---------------------------------------------------------------- terms

Rec = Tuple[str, str, int, float, bool]   # lname, ldir, size, mtime, is_dir


@dataclass
class Term:
    kind: str                       # text | glob | ext | size | dm | dir | file | in | parent | path | regex | none
    neg: bool = False
    value: Any = None
    test: Callable[[str, str, int, float, bool], bool] = field(default=lambda *a: True, repr=False)
    #: literal strings in the lowered NAME blob ("\n" + name + "\n" lines) that every match contains — any of them
    #: (OR); empty → this term cannot drive the search
    lits: Tuple[str, ...] = ()
    #: plain text term → the exact / prefix passes of the ranking may use it
    plain: str = ""
    needs_size: bool = False
    needs_time: bool = False
    #: path terms: literal in the lowered full path ("c:/users/x/name")
    path_lit: str = ""
    #: in:/parent: folder (normalized)
    folder: str = ""

    def lit_score(self) -> int:
        if self.neg or not self.lits:
            return -1
        best = min(len(x) for x in self.lits)
        return best * 2 - (len(self.lits) - 1)


@dataclass
class Query:
    groups: List[List[Term]]
    sort: str = "relevance"
    raw: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(group for group in self.groups)


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    return re.compile(fnmatch.translate(pattern), re.S)


def _glob_lits(pattern: str) -> Tuple[str, ...]:
    pieces = re.split(r"[*?]+", pattern)
    cands: List[Tuple[int, str]] = []
    for i, piece in enumerate(pieces):
        if not piece:
            continue
        lit = piece
        bonus = 0
        if i == 0:
            lit = "\n" + lit
            bonus += 2
        if i == len(pieces) - 1:
            lit = lit + "\n"
            bonus += 2
        cands.append((len(piece) * 2 + bonus, lit))
    if not cands:
        return ()
    cands.sort(reverse=True)
    return (cands[0][1],)


def _tokens(text: str) -> List[str]:
    """Split on spaces, keeping "quoted parts" (also inside key:"a b") and turning ``|`` into its own token."""
    out: List[str] = []
    cur = ""
    quoted = False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch.isspace() and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        elif ch == "|" and not quoted and not cur.lower().startswith(("regex:", "!regex:")):
            # «a | b» (spaced) separates whole alternatives; «a|b» (glued) binds just the two neighbours
            nxt = text[i + 1] if i + 1 < len(text) else " "
            tight = bool(cur) and not nxt.isspace()
            if cur:
                out.append(cur)
            out.append("|t" if tight else "|")
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _unquote(text: str) -> str:
    return text.replace('"', "")


def _looks_like_path(text: str) -> bool:
    return "/" in text or "\\" in text or bool(re.match(r"^[a-zA-Z]:", text)) or text.startswith("~")


def expand_folder(text: str, places: Optional[Callable[[str], str]] = None) -> str:
    """``~``, ``%USERPROFILE%``, ``$HOME`` and place words (``Desktop``, ``Загрузки`` via ``places``) → a path."""
    raw = _unquote(text).strip()
    if places is not None and not _looks_like_path(raw):
        try:
            found = places(raw)
        except Exception:
            found = ""
        if found:
            return found
    return os.path.expandvars(os.path.expanduser(raw))


KEYS = {"ext", "size", "dm", "datemodified", "date", "folder", "folders", "file", "files", "in", "infolder",
        "parent", "path", "regex", "type", "kind", "sort", "тип", "папка", "в", "размер", "дата", "расширение"}
_KEY_ALIAS = {"datemodified": "dm", "date": "dm", "дата": "dm", "размер": "size", "расширение": "ext",
              "infolder": "in", "в": "in", "folders": "folder", "папка": "folder", "files": "file", "kind": "type",
              "тип": "type"}


_NEEDS_VALUE = {"type", "size", "dm", "in", "parent", "regex", "path", "ext"}


def _make_term(token: str, now: float, places: Optional[Callable[[str], str]]) -> Optional[Term]:
    neg = False
    while token.startswith("!") and len(token) > 1:
        neg = not neg
        token = token[1:]
    key, value = "", token
    m = re.match(r"^([a-zA-Zа-яА-ЯёЁ]+):(.*)$", token, re.S)
    if m and low(m.group(1)) in KEYS and not re.match(r"^[a-zA-Z]:[\\/]", token):
        key, value = low(m.group(1)), m.group(2)
        key = _KEY_ALIAS.get(key, key)
    value = _unquote(value)
    if key in _NEEDS_VALUE and not value.strip(" <>="):
        return None     # Round 36: a filter still being typed («type:», «size:>», «in:») — no error flash on every key
    if key == "sort":
        return Term(kind="sort", value=low(value) or "relevance")
    if key == "ext":
        exts = tuple(sorted({low(x).strip().lstrip("*").lstrip(".") for x in re.split(r"[;,|]", value) if x.strip()}))
        if not exts:
            return None
        suffixes = tuple("." + e for e in exts)
        return Term(kind="ext", neg=neg, value=exts, lits=tuple(s + "\n" for s in suffixes),
                    test=lambda n, d, s, t, isdir, _x=suffixes: (not isdir) and n.endswith(_x))
    if key == "type":
        kind = KIND_ALIASES.get(low(value).strip())
        if kind is None:
            raise QueryError(f"unknown type: {value}")
        if kind == "folder":
            return Term(kind="dir", neg=neg, test=lambda n, d, s, t, isdir: isdir)
        if kind == "file":
            return Term(kind="file", neg=neg, test=lambda n, d, s, t, isdir: not isdir)
        suffixes = tuple("." + e for e in KINDS[kind])
        return Term(kind="ext", neg=neg, value=KINDS[kind], lits=tuple(s + "\n" for s in suffixes),
                    test=lambda n, d, s, t, isdir, _x=suffixes: (not isdir) and n.endswith(_x))
    if key == "size":
        lo, hi = size_range(value)
        return Term(kind="size", neg=neg, value=(lo, hi), needs_size=True,
                    test=lambda n, d, s, t, isdir, _lo=lo, _hi=hi: (not isdir) and s >= 0 and _lo <= s <= _hi)
    if key == "dm":
        lo, hi = date_range(value, now)
        return Term(kind="dm", neg=neg, value=(lo, hi), needs_time=True,
                    test=lambda n, d, s, t, isdir, _lo=lo, _hi=hi: t is not None and t >= 0 and _lo <= t < _hi)
    if key == "file" and not value:
        return Term(kind="file", neg=neg, test=lambda n, d, s, t, isdir: not isdir)
    if key == "folder" and not value:
        return Term(kind="dir", neg=neg, test=lambda n, d, s, t, isdir: isdir)
    if key in ("in", "parent") or (key == "folder" and _looks_like_path(value)):
        folder = norm_path(expand_folder(value, places))
        if key == "parent":
            return Term(kind="parent", neg=neg, folder=folder, value=folder,
                        test=lambda n, d, s, t, isdir, _f=folder: d == _f)
        pre = folder + "/"
        return Term(kind="in", neg=neg, folder=folder, value=folder,
                    test=lambda n, d, s, t, isdir, _f=folder, _p=pre: d == _f or d.startswith(_p))
    if key in ("folder", "file"):     # folder:name / file:name
        sub = low(value)
        want_dir = key == "folder"
        if any(c in sub for c in "*?"):
            rx = _glob_regex(sub)
            return Term(kind="glob", neg=neg, value=sub, lits=_glob_lits(sub),
                        test=lambda n, d, s, t, isdir, _r=rx, _w=want_dir: isdir == _w and bool(_r.match(n)))
        return Term(kind="text", neg=neg, value=sub, lits=(sub,),
                    test=lambda n, d, s, t, isdir, _s=sub, _w=want_dir: isdir == _w and _s in n)
    if key == "regex":
        try:
            rx = re.compile(value, re.I)
        except re.error as exc:
            raise QueryError(f"bad regex: {exc}")
        return Term(kind="regex", neg=neg, value=rx, test=lambda n, d, s, t, isdir, _r=rx: bool(_r.search(n)))
    text = low(value)
    if not text:
        return None
    if key == "path" or "/" in text or "\\" in text:
        lit = text.replace("\\", "/")
        if any(c in lit for c in "*?"):
            rx = _glob_regex(lit)
            name_part = lit.rsplit("/", 1)[-1]
            return Term(kind="pathglob", neg=neg, value=lit,
                        lits=_glob_lits(name_part) if name_part and name_part not in ("*",) else (),
                        test=lambda n, d, s, t, isdir, _r=rx: bool(_r.match(full_lpath(d, n))))
        # no name literal: the text may be in the folder part (the index drives it through the folder paths)
        return Term(kind="path", neg=neg, value=lit, path_lit=lit,
                    test=lambda n, d, s, t, isdir, _l=lit: _l in full_lpath(d, n))
    if any(c in text for c in "*?"):
        rx = _glob_regex(text)
        return Term(kind="glob", neg=neg, value=text, lits=_glob_lits(text),
                    test=lambda n, d, s, t, isdir, _r=rx: bool(_r.match(n)))
    return Term(kind="text", neg=neg, value=text, lits=(text,), plain=text,
                test=lambda n, d, s, t, isdir, _s=text: _s in n)


def _or_term(a: Term, b: Term) -> Term:
    """``a | b`` — Everything's precedence: OR binds tighter than the space (AND)."""
    subs = (a.value if a.kind == "or" else [a]) + [b]
    lits: Tuple[str, ...] = ()
    if all(t.lits and not t.neg for t in subs):
        lits = tuple(dict.fromkeys(l for t in subs for l in t.lits))

    def test(n, d, s, t, isdir, _subs=tuple(subs)):
        return any(x.test(n, d, s, t, isdir) != x.neg for x in _subs)
    return Term(kind="or", value=subs, lits=lits, test=test,
                needs_size=any(t.needs_size for t in subs), needs_time=any(t.needs_time for t in subs))


def _merge_single_alternatives(groups: List[List[Term]]) -> List[List[Term]]:
    """``*.jpg | *.png`` (one term on each side) is the same as the glued form: one ``or`` term, one group —
    the index then drives it with both literals at once."""
    if len(groups) > 1 and all(len(g) == 1 for g in groups):
        merged = groups[0][0]
        for g in groups[1:]:
            merged = _or_term(merged, g[0])
        return [[merged]]
    return groups


def parse(text: str, now: Optional[float] = None, places: Optional[Callable[[str], str]] = None,
          kinds: Optional[Sequence[str]] = None, roots: Optional[Sequence[str]] = None) -> Query:
    """Parse a query; ``kinds`` (``["image","doc"]``, ``"folder"``, ``"file"``) and ``roots`` (folders) are ANDed
    to every group (as the contract of :func:`app.fsindex.search` says). Bad tokens raise :class:`QueryError`."""
    now = time.time() if now is None else now
    query = Query(groups=[[]], raw=str(text or ""))
    terms: List[Term] = []
    groups: List[List[Term]] = []
    pending_or = False
    for token in _tokens(str(text or "").strip()):
        if token == "|t":
            pending_or = bool(terms)
            continue
        if token in ("|", "OR"):
            # a spaced «|» (or OR) between multi-word sides: alternatives of whole groups
            if terms:
                groups.append(terms)
                terms = []
            pending_or = False
            continue
        term = _make_term(token, now, places)
        if term is None:
            continue
        if term.kind == "sort":
            query.sort = {"date": "date", "dm": "date", "дата": "date", "size": "size", "размер": "size",
                          "name": "name", "имя": "name", "path": "path", "путь": "path"}.get(term.value, "relevance")
            continue
        if pending_or and terms:
            terms[-1] = _or_term(terms[-1], term)
        else:
            terms.append(term)
        pending_or = False
    if terms:
        groups.append(terms)
    groups = _merge_single_alternatives(groups)
    query.groups = groups or [[]]
    extra: List[Term] = []
    kind_list = [kinds] if isinstance(kinds, str) else list(kinds or [])
    kind_list = [str(k).strip() for k in kind_list if str(k or "").strip()]
    if kind_list:
        exts: List[str] = []
        only_dirs = only_files = False
        for k in kind_list:
            alias = KIND_ALIASES.get(low(k))
            if alias == "folder":
                only_dirs = True
            elif alias == "file":
                only_files = True
            elif alias:
                exts.extend(KINDS[alias])
            else:
                exts.append(low(k).lstrip("."))
        if exts:
            suffixes = tuple(sorted({"." + e for e in exts}))
            extra.append(Term(kind="ext", value=tuple(e[1:] for e in suffixes), lits=tuple(s + "\n" for s in suffixes),
                              test=lambda n, d, s, t, isdir, _x=suffixes: (not isdir) and n.endswith(_x)))
        elif only_dirs and not only_files:
            extra.append(Term(kind="dir", test=lambda n, d, s, t, isdir: isdir))
        elif only_files and not only_dirs:
            extra.append(Term(kind="file", test=lambda n, d, s, t, isdir: not isdir))
    root_list = [roots] if isinstance(roots, str) else list(roots or [])
    folders = [norm_path(expand_folder(str(r), places)) for r in root_list if str(r or "").strip()]
    if folders:
        if len(folders) == 1:
            f = folders[0]
            extra.append(Term(kind="in", folder=f, value=f,
                              test=lambda n, d, s, t, isdir, _f=f, _p=f + "/": d == _f or d.startswith(_p)))
        else:
            pres = tuple(f + "/" for f in folders)
            fs = tuple(folders)
            extra.append(Term(kind="inany", value=fs,
                              test=lambda n, d, s, t, isdir, _f=fs, _p=pres: d in _f or d.startswith(_p)))
    if extra:
        query.groups = [g + extra for g in query.groups]
    return query


def has_terms(query: Query) -> bool:
    return any(t.kind not in ("sort",) for g in query.groups for t in g)


def match(group: Sequence[Term], lname: str, ldir: str, size: int, mtime: float, is_dir: bool) -> bool:
    for term in group:
        if term.test(lname, ldir, size, mtime, is_dir) == term.neg:
            return False
    return True


def quality(group: Sequence[Term], lname: str) -> int:
    """0 exact name (or name without extension), 1 name starts with, 2 starts a word, 3 elsewhere / no text."""
    best = 3
    for term in group:
        if term.neg or not term.plain:
            continue
        p = term.plain
        stem = lname.rsplit(".", 1)[0] if "." in lname else lname
        if lname == p or stem == p:
            q = 0
        elif lname.startswith(p):
            q = 1
        else:
            i = lname.find(p)
            q = 2 if i > 0 and not lname[i - 1].isalnum() else 3
        best = min(best, q)
        break
    return best


# ---------------------------------------------------------------- Everything translation

def to_everything(text: str, kinds: Optional[Sequence[str]] = None, roots: Optional[Sequence[str]] = None,
                  places: Optional[Callable[[str], str]] = None, now: Optional[float] = None) -> str:
    """Our syntax → Everything's: mostly the same; ``in:``/``type:``/Russian words/``Nd`` dates are rewritten."""
    now = time.time() if now is None else now
    out: List[str] = []
    groups: List[List[str]] = []
    for token in _tokens(str(text or "").strip()):
        if token == "|t":
            out.append("|")
            continue
        if token == "|":
            if out:
                groups.append(out)
            out = []
            continue
        neg = ""
        t = token
        while t.startswith("!") and len(t) > 1:
            neg = "!" if not neg else ""
            t = t[1:]
        m = re.match(r"^([a-zA-Zа-яА-ЯёЁ]+):(.*)$", t, re.S)
        if not (m and low(m.group(1)) in KEYS and not re.match(r"^[a-zA-Z]:[\\/]", t)):
            out.append(neg + t)
            continue
        key = _KEY_ALIAS.get(low(m.group(1)), low(m.group(1)))
        value = _unquote(m.group(2))
        if key == "sort" or (key in _NEEDS_VALUE and not value.strip(" <>=")):
            continue
        if key == "type":
            kind = KIND_ALIASES.get(low(value).strip())
            if kind == "folder":
                out.append(neg + "folder:")
            elif kind == "file":
                out.append(neg + "file:")
            elif kind:
                out.append(neg + "ext:" + ";".join(KINDS[kind]))
            continue
        if key in ("in",) or (key == "folder" and _looks_like_path(value)):
            path = expand_folder(value, places).rstrip("\\/")
            out.append(neg + '"' + path.replace("/", "\\") + '\\"')
            continue
        if key == "parent":
            path = expand_folder(value, places).rstrip("\\/")
            out.append(neg + 'parent:"' + path.replace("/", "\\") + '"')
            continue
        if key == "size":
            v = low(value)
            for ru, en in (("кб", "kb"), ("мб", "mb"), ("гб", "gb"), ("тб", "tb"), ("б", "")):
                v = v.replace(ru, en)
            out.append(neg + "size:" + v)
            continue
        if key == "dm":
            v = low(value).replace(" ", "")
            v = _DATE_WORDS.get(v, v)
            mm = re.fullmatch(r"(\d+)(min|mins|minutes?|m|h|hours?|ч|d|days?|д|дн|w|weeks?|н|mo|months?|y|years?)", v)
            if mm:
                unit = mm.group(2)
                word = ("minutes" if unit in ("min", "mins", "minute", "minutes", "m") else
                        "hours" if unit in ("h", "hour", "hours", "ч") else
                        "days" if unit in ("d", "day", "days", "д", "дн") else
                        "weeks" if unit in ("w", "week", "weeks", "н") else
                        "months" if unit in ("mo", "month", "months") else "years")
                v = f"last{mm.group(1)}{word}"
            out.append(neg + "dm:" + v)
            continue
        if key == "path":
            out.append(neg + '"' + value.replace("/", "\\") + '"' if " " in value else neg + value.replace("/", "\\"))
            continue
        if key == "ext":
            out.append(neg + "ext:" + ";".join(x.strip().lstrip("*.") for x in re.split(r"[;,|]", value) if x.strip()))
            continue
        out.append(neg + key + ":" + (('"' + value + '"') if (" " in value or "|" in value) else value))
    if out:
        groups.append(out)
    if len(groups) > 1:
        query = " | ".join(("<" + " ".join(g) + ">") if len(g) > 1 else " ".join(g) for g in groups)
        query = "<" + query + ">"
    else:
        query = " ".join(groups[0]) if groups else ""
    extra: List[str] = []
    kind_list = [kinds] if isinstance(kinds, str) else list(kinds or [])
    exts: List[str] = []
    for k in kind_list:
        alias = KIND_ALIASES.get(low(str(k)))
        if alias == "folder":
            extra.append("folder:")
        elif alias == "file":
            extra.append("file:")
        elif alias:
            exts.extend(KINDS[alias])
        elif str(k).strip():
            exts.append(low(str(k)).lstrip("."))
    if exts:
        extra.append("ext:" + ";".join(dict.fromkeys(exts)))
    root_list = [roots] if isinstance(roots, str) else list(roots or [])
    paths = ['"' + expand_folder(str(r), places).rstrip("\\/").replace("/", "\\") + '\\"' for r in root_list if str(r or "").strip()]
    if paths:
        extra.append(paths[0] if len(paths) == 1 else "<" + "|".join(paths) + ">")
    if extra:
        query = " ".join([query] + extra).strip()
    return query
