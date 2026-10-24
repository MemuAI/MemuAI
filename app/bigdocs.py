"""Documents of any size (Round 35, zone bigdocs).

A long PDF / DOCX / EPUB / TXT used to be cut at the extraction limit (200 000 characters in the chat, 30 000 in
Telegram, head+tail in Quick Access, a 60 000-character window in MemuNote): the model saw the beginning of a
600-page book and answered about it as if it were the whole. This module keeps the WHOLE document and offers two
ways to work with it:

* **Вопрос по документу** (Q&A): the document is split into page-bound chunks, a local hybrid search (BM25 over
  stemmed words + character-trigram cosine, fused by reciprocal rank, then a lexical rerank) picks the fragments
  that answer the question; the model answers from them and cites pages as ``[стр. N]`` (``[p. N]`` in English).
  :func:`cite` turns those marks into ``{page, quote, verified}`` so the UI can show the quote on hover.
* **Весь документ** (map-reduce): the document is planned into parts along its own structure (chapters from the PDF
  outline / DOCX headings / EPUB spine / «Глава 7» lines), each part is summarized (map), the summaries are reduced
  hierarchically into one answer. The cost is estimated BEFORE starting from catalogue prices (:mod:`app.pricing`),
  progress reads «глава 7 из 20», a run can be cancelled and resumed (finished parts are cached on disk), and the
  final result is cached by ``(document hash, model, mode, prompt)``.

The core is framework-free: every surface passes its own ``complete(model, messages) -> str`` coroutine
(the playground → Proxy bridge, the Telegram bot → ``chat_service``, MemuNote → its own Proxy client).

Storage: ``<DATA_DIR>/memuai/bigdocs/<id>/doc.json.gz`` (pages, sections, chunks) and ``runs/<key>.json``
(map/reduce cache). ``id`` is the first 32 hex chars of the file's SHA-256. The store is pruned to
``STORE_LIMIT_BYTES`` by last use.
"""
# (no «from __future__ import annotations»: FastAPI resolves the route parameter annotations at runtime)

import asyncio
import gzip
import hashlib
import html as _html
import io
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

EXTRACTOR_VERSION = 3
MAX_FILE_BYTES = 200 * 1024 * 1024          # what the chat / Quick Access / MemuNote may hand over
MAX_UNPACKED_BYTES = 160 * 1024 * 1024      # DOCX/EPUB members (zip-bomb guard together with the ratio check)
MAX_PAGES = 5000
APPROX_PAGE_CHARS = 1800                    # a «page» of a flowing text (DOCX without page breaks, EPUB, TXT)
CHUNK_CHARS = 1100
STORE_LIMIT_BYTES = 600 * 1024 * 1024
MAP_CONCURRENCY = 3
DEFAULT_PART_TOKENS = 12000
DEFAULT_CONFIRM_USD = 0.25
UNKNOWN_PRICE_CONFIRM_TOKENS = 300_000
MAP_OUTPUT_TOKENS = 700
REDUCE_OUTPUT_TOKENS = 1500
DOC_EXTENSIONS = (".pdf", ".docx", ".epub", ".txt", ".md", ".markdown", ".html", ".htm", ".rst", ".log", ".csv",
                  ".json", ".xml", ".fb2")

Complete = Callable[[str, List[Dict[str, Any]]], Awaitable[str]]


class BigDocError(Exception):
    """A user-facing problem; ``code`` lets callers pick an HTTP status / wording."""

    def __init__(self, ru: str, en: str = "", code: str = "error") -> None:
        super().__init__(ru)
        self.ru, self.en, self.code = ru, en or ru, code

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru


def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


# ------------------------------------------------------------------ storage
def store_dir() -> Path:
    from .config import DATA_DIR
    path = Path(DATA_DIR) / "memuai" / "bigdocs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def doc_id_for(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:32]


_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def valid_id(value: Any) -> bool:
    return bool(_ID_RE.match(str(value or "")))


def _doc_path(doc_id: str) -> Path:
    if not valid_id(doc_id):
        raise BigDocError("Неверный идентификатор документа", "Invalid document id", "bad_id")
    return store_dir() / doc_id


_MEM: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_MEM_LOCK = threading.Lock()
_MEM_SIZE = 4


def _remember(doc: Dict[str, Any]) -> None:
    with _MEM_LOCK:
        _MEM[doc["id"]] = doc
        _MEM.move_to_end(doc["id"])
        while len(_MEM) > _MEM_SIZE:
            _MEM.popitem(last=False)


def save_doc(doc: Dict[str, Any]) -> None:
    folder = _doc_path(doc["id"])
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / ("doc.json.gz.%s.tmp" % uuid.uuid4().hex[:6])
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, folder / "doc.json.gz")
    _remember(doc)
    try:
        prune()
    except Exception:
        pass


def load_doc(doc_id: str) -> Optional[Dict[str, Any]]:
    with _MEM_LOCK:
        hit = _MEM.get(doc_id)
        if hit is not None:
            _MEM.move_to_end(doc_id)
    if hit is not None:
        return hit
    path = _doc_path(doc_id) / "doc.json.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    if int(doc.get("v") or 0) != EXTRACTOR_VERSION:
        return None
    try:
        os.utime(path, None)                 # last use, for pruning
    except OSError:
        pass
    _remember(doc)
    return doc


def get_doc(doc_id: str) -> Dict[str, Any]:
    doc = load_doc(doc_id)
    if doc is None:
        raise BigDocError("Документ больше не в кэше — прикрепите файл заново",
                          "The document is no longer cached — attach the file again", "gone")
    return doc


def prune(limit: int = STORE_LIMIT_BYTES) -> int:
    """Drop the least recently used documents beyond ``limit`` bytes; returns how many were removed."""
    root = store_dir()
    rows = []
    for folder in root.iterdir():
        if not folder.is_dir() or not valid_id(folder.name):
            continue
        size, last = 0, 0.0
        for item in folder.rglob("*"):
            if item.is_file():
                st = item.stat()
                size += st.st_size
                last = max(last, st.st_mtime)
        rows.append((last, size, folder))
    total = sum(r[1] for r in rows)
    removed = 0
    for last, size, folder in sorted(rows):
        if total <= limit:
            break
        shutil.rmtree(folder, ignore_errors=True)
        with _MEM_LOCK:
            _MEM.pop(folder.name, None)
        total -= size
        removed += 1
    return removed


# ------------------------------------------------------------------ text helpers
_WS = re.compile(r"[ \t\u00a0\u2007\u202f]+")


def _clean(text: str) -> str:
    text = str(text or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)              # hyphenated line ends of PDFs
    text = _WS.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    sample = raw[:20000]
    # cp1251 when the bytes look like Cyrillic letters, latin-1 otherwise
    cyr = sum(1 for b in sample if 0xC0 <= b <= 0xFF)
    return raw.decode("cp1251" if cyr > len(sample) * 0.15 else "latin-1", "replace")


class _HTMLText:
    """HTML → text with headings kept as ``#`` lines (no external parser)."""
    BLOCK = re.compile(r"</?(p|div|br|li|tr|section|article|blockquote|pre|table|ul|ol|dd|dt)\b[^>]*>", re.I)

    @classmethod
    def convert(cls, markup: str) -> Tuple[str, List[str]]:
        markup = re.sub(r"(?is)<(script|style|head|nav)\b.*?</\1>", " ", markup)
        titles: List[str] = []

        def heading(m: "re.Match[str]") -> str:
            level = int(m.group(1))
            inner = _html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            inner = _WS.sub(" ", inner).strip()
            if not inner:
                return "\n"
            titles.append(inner)
            return "\n\n" + "#" * level + " " + inner + "\n\n"
        markup = re.sub(r"(?is)<h([1-4])\b[^>]*>(.*?)</h\1>", heading, markup)
        markup = cls.BLOCK.sub("\n", markup)
        text = _html.unescape(re.sub(r"<[^>]+>", "", markup))
        return text, titles


# ------------------------------------------------------------------ structure detection
_CHAPTER_RE = re.compile(
    r"^(?:глава|часть|раздел|книга|том|статья|приложение|chapter|part|section|book|appendix|article)\s+"
    r"(?:[0-9]{1,4}|[ivxlcdm]{1,8}|[а-яё]+|one|two|three|four|five|six|seven|eight|nine|ten)\b[.:\s\-—–]*.{0,90}$",
    re.I)
_NUMBERED_RE = re.compile(r"^(?:\d{1,2})(?:\.\d{1,2}){0,2}\.?\s+[A-ZА-ЯЁ][^.!?]{2,80}$")


def detect_headings(text: str) -> List[Tuple[int, str]]:
    """Lines that look like headings: ``(level, title)``; markdown ``#`` lines, «Глава 7», numbered «3.2 Title»."""
    out: List[Tuple[int, str]] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or len(line) > 110:
            continue
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
        elif _CHAPTER_RE.match(line):
            out.append((1, line))
        elif _NUMBERED_RE.match(line) and len(line.split()) <= 12:
            out.append((2 if "." in line.split()[0].strip(".") else 1, line))
        elif line.isupper() and 4 <= len(line) <= 60 and len(line.split()) <= 8 and re.search(r"[A-ZА-ЯЁ]{3}", line):
            out.append((1, line.title() if len(line) > 3 else line))
    return out


def _pages_from_flow(blocks: List[Tuple[str, Optional[Tuple[int, str]]]], page_chars: int = APPROX_PAGE_CHARS
                     ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Paragraph blocks (text, heading?) → approximate pages of ~``page_chars`` characters + sections."""
    pages: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    buf: List[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            pages.append({"n": len(pages) + 1, "text": "\n".join(buf)})
        buf, size = [], 0
    for text, heading in blocks:
        text = text.strip()
        if not text:
            continue
        if heading and heading[0] == 1 and size > page_chars * 0.35:
            flush()                         # a chapter starts on a new page, like in a printed book
        while len(text) > page_chars * 1.6:  # a giant paragraph (a TXT without blank lines): split at a sentence
            room = max(200, page_chars - size)
            cut = text.rfind(". ", 0, room)
            cut = cut + 1 if cut > room * 0.4 else room
            buf.append(text[:cut].strip())
            text = text[cut:].strip()
            flush()
        if heading:
            sections.append({"title": heading[1][:160], "level": heading[0], "page": len(pages) + 1})
        buf.append(text)
        size += len(text) + 1
        if size >= page_chars:
            flush()
    flush()
    return pages, sections


# ------------------------------------------------------------------ extraction
Progress = Optional[Callable[[Dict[str, Any]], None]]


def _report(progress: Progress, **data: Any) -> None:
    if progress:
        try:
            progress(data)
        except Exception:
            pass


def _extract_pdf(raw: bytes, progress: Progress) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise BigDocError("Для PDF нужен компонент pypdf", "PDF needs the pypdf component", "pypdf") from None
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise BigDocError("PDF защищён паролем", "The PDF is password-protected", "encrypted") from None
        total = len(reader.pages)
    except BigDocError:
        raise
    except Exception as exc:
        raise BigDocError(f"PDF не разобран: {exc}", f"Could not parse the PDF: {exc}", "parse") from None
    if total > MAX_PAGES:
        raise BigDocError(f"В PDF {total} страниц — больше {MAX_PAGES}", f"The PDF has {total} pages — over {MAX_PAGES}",
                          "too_large")
    try:
        labels = list(reader.page_labels)
    except Exception:
        labels = []
    pages: List[Dict[str, Any]] = []
    last = 0.0
    for index in range(total):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception:
            text = ""
        page = {"n": index + 1, "text": _clean(text)}
        label = labels[index] if index < len(labels) else ""
        if label and label != str(index + 1):
            page["label"] = str(label)[:16]
        pages.append(page)
        now = time.monotonic()
        if now - last > 0.25 or index + 1 == total:
            last = now
            _report(progress, stage="read", page=index + 1, pages=total)
    sections: List[Dict[str, Any]] = []
    try:
        def walk(items: Any, level: int) -> None:
            for item in items:
                if isinstance(item, list):
                    walk(item, level + 1)
                    continue
                try:
                    number = reader.get_destination_page_number(item)
                    title = str(item.title or "").strip()
                except Exception:
                    continue
                if title and number is not None and number >= 0:
                    sections.append({"title": title[:160], "level": level, "page": number + 1})
        walk(reader.outline or [], 1)
    except Exception:
        sections = []
    if not sections:
        for page in pages:
            for level, title in detect_headings(page["text"])[:2]:
                sections.append({"title": title[:160], "level": level, "page": page["n"]})
    return {"kind": "pdf", "pages": pages, "sections": sections, "approx_pages": False}


def _zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_UNPACKED_BYTES or (info.compress_size > 0 and info.file_size / info.compress_size > 200
                                               and info.file_size > 20 * 1024 * 1024):
        raise BigDocError("Документ слишком большой в распакованном виде", "The document is too large when unpacked",
                          "too_large")
    return archive.read(name)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(raw: bytes, progress: Progress) -> Dict[str, Any]:
    import xml.etree.ElementTree as ET
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        xml = _zip_member(archive, "word/document.xml")
    except BigDocError:
        raise
    except Exception as exc:
        raise BigDocError(f"DOCX не разобран: {exc}", f"Could not parse the DOCX: {exc}", "parse") from None
    heading_styles: Dict[str, int] = {}
    try:
        styles = ET.fromstring(_zip_member(archive, "word/styles.xml"))
        for style in styles.iter(_W + "style"):
            sid = style.get(_W + "styleId") or ""
            name_el = style.find(_W + "name")
            name = (name_el.get(_W + "val") if name_el is not None else "") or ""
            m = re.match(r"(?i)^(?:heading|заголовок)\s*(\d)$", name.strip()) or re.match(r"(?i)^(?:heading|заголовок)(\d)$", sid)
            if m:
                heading_styles[sid] = int(m.group(1))
            elif name.strip().lower() == "title":
                heading_styles[sid] = 1
    except Exception:
        pass
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise BigDocError(f"DOCX повреждён: {exc}", f"The DOCX is damaged: {exc}", "parse") from None
    body = root.find(_W + "body")
    paragraphs: List[Tuple[str, Optional[int], int]] = []     # (text, heading level, page breaks BEFORE it)
    breaks_total = 0
    pending_breaks = 0
    for el in (body if body is not None else []):
        if el.tag == _W + "tbl":
            rows = []
            for tr in el.iter(_W + "tr"):
                cells = ["".join(t.text or "" for t in tc.iter(_W + "t")).strip() for tc in tr.iter(_W + "tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                paragraphs.append(("\n".join(rows), None, pending_breaks))
                pending_breaks = 0
            continue
        if el.tag != _W + "p":
            continue
        ppr = el.find(_W + "pPr")
        level = None
        if ppr is not None:
            ps = ppr.find(_W + "pStyle")
            if ps is not None:
                sid = ps.get(_W + "val") or ""
                level = heading_styles.get(sid)
                if level is None:
                    m = re.match(r"(?i)^(?:heading|заголовок)\s*(\d)$", sid)
                    level = int(m.group(1)) if m else None
            ol = ppr.find(_W + "outlineLvl")
            if level is None and ol is not None:
                try:
                    level = int(ol.get(_W + "val") or 9) + 1
                except ValueError:
                    level = None
                level = level if level and level <= 4 else None
            if ppr.find(_W + "pageBreakBefore") is not None:
                pending_breaks += 1
                breaks_total += 1
        parts: List[str] = []
        before = pending_breaks
        pending_breaks = 0
        for node in el.iter():
            tag = node.tag
            if tag == _W + "t":
                parts.append(node.text or "")
            elif tag == _W + "tab":
                parts.append("\t")
            elif tag == _W + "br":
                if node.get(_W + "type") == "page":
                    if "".join(parts).strip():
                        # the break is inside the paragraph: text before it stays on the previous page
                        paragraphs.append(("".join(parts), level, before))
                        parts, before = [], 0
                    before += 1
                    breaks_total += 1
                else:
                    parts.append("\n")
            elif tag == _W + "lastRenderedPageBreak":
                if "".join(parts).strip():
                    paragraphs.append(("".join(parts), level, before))
                    parts, before = [], 0
                before += 1
                breaks_total += 1
        text = "".join(parts)
        if text.strip() or before:
            paragraphs.append((text, level, before))
    total_chars = sum(len(p[0]) for p in paragraphs)
    _report(progress, stage="read", page=1, pages=1)
    # Real page breaks (typed or Word's last rendering) → exact pages; too few of them → approximate pages.
    if breaks_total >= max(2, total_chars // 9000):
        pages: List[Dict[str, Any]] = [{"n": 1, "text": ""}]
        sections: List[Dict[str, Any]] = []
        buf: List[str] = []
        for text, level, before in paragraphs:
            for _ in range(before):
                pages[-1]["text"] = _clean("\n".join(buf))
                buf = []
                pages.append({"n": len(pages) + 1, "text": ""})
            if text.strip():
                buf.append(text)
                if level:
                    sections.append({"title": _clean(text)[:160], "level": level, "page": pages[-1]["n"]})
        pages[-1]["text"] = _clean("\n".join(buf))
        while len(pages) > 1 and not pages[-1]["text"]:
            pages.pop()
        if not sections:
            for page in pages:
                for lv, title in detect_headings(page["text"])[:2]:
                    sections.append({"title": title, "level": lv, "page": page["n"]})
        return {"kind": "docx", "pages": pages, "sections": sections, "approx_pages": False}
    blocks = []
    for text, level, _before in paragraphs:
        clean = _clean(text)
        if not clean:
            continue
        heading = (level, clean) if level else None
        if heading is None:
            found = detect_headings(clean) if len(clean) < 110 else []
            heading = found[0] if found else None
        blocks.append((clean, heading))
    pages, sections = _pages_from_flow(blocks)
    return {"kind": "docx", "pages": pages, "sections": sections, "approx_pages": True}


def _extract_epub(raw: bytes, progress: Progress) -> Dict[str, Any]:
    import posixpath
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        container = _zip_member(archive, "META-INF/container.xml").decode("utf-8", "replace")
        opf_path = re.search(r'full-path="([^"]+)"', container).group(1)  # type: ignore[union-attr]
        opf = _zip_member(archive, opf_path).decode("utf-8", "replace")
    except BigDocError:
        raise
    except Exception as exc:
        raise BigDocError(f"EPUB не разобран: {exc}", f"Could not parse the EPUB: {exc}", "parse") from None
    base = posixpath.dirname(opf_path)
    manifest = {}
    for m in re.finditer(r"<(?:\w+:)?item\b([^>]*)/?>", opf):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(1)))
        if attrs.get("id") and attrs.get("href"):
            manifest[attrs["id"]] = (attrs["href"], attrs.get("media-type", ""))
    spine = re.findall(r'<(?:\w+:)?itemref\b[^>]*idref="([^"]+)"', opf)
    title_m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S)
    blocks: List[Tuple[str, Optional[Tuple[int, str]]]] = []
    for index, idref in enumerate(spine):
        href, mime = manifest.get(idref, ("", ""))
        if not href or ("html" not in mime and not href.lower().endswith((".html", ".xhtml", ".htm"))):
            continue
        path = posixpath.normpath(posixpath.join(base, _html.unescape(href).split("#")[0]))
        try:
            markup = _zip_member(archive, path).decode("utf-8", "replace")
        except (KeyError, BigDocError):
            continue
        text, titles = _HTMLText.convert(markup)
        first = True
        for para in re.split(r"\n\s*\n|\n", text):
            para = _clean(para)
            if not para:
                continue
            m = re.match(r"^(#{1,4})\s+(.+)$", para)
            if m:
                level = len(m.group(1))
                # the first heading of a spine item is a chapter, inner ones are its subsections
                blocks.append((m.group(2), (1 if first else min(4, level + 1), m.group(2))))
                first = False
            else:
                blocks.append((para, None))
        _report(progress, stage="read", page=index + 1, pages=len(spine))
    pages, sections = _pages_from_flow(blocks)
    out = {"kind": "epub", "pages": pages, "sections": sections, "approx_pages": True}
    if title_m:
        out["title"] = _clean(_html.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))))[:200]
    return out


def _extract_text(raw: bytes, name: str, progress: Progress) -> Dict[str, Any]:
    text = _decode(raw)
    lower = name.lower()
    if lower.endswith((".html", ".htm", ".xhtml", ".fb2")) or re.match(r"(?is)\s*(<!doctype html|<html)", text[:500]):
        if lower.endswith(".fb2"):
            text = re.sub(r"(?is)<binary\b.*?</binary>", " ", text)
            text = re.sub(r"(?is)<title>(.*?)</title>", lambda m: "<h2>" + m.group(1) + "</h2>", text)
        text, _titles = _HTMLText.convert(text)
    blocks: List[Tuple[str, Optional[Tuple[int, str]]]] = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        para = _clean(para)
        if not para:
            continue
        lines = para.split("\n")
        heads = detect_headings(lines[0]) if len(lines[0]) < 110 else []
        if heads and len(lines) > 1:            # «Глава 3\ntext…» in one paragraph: the heading is its own block
            blocks.append((lines[0], heads[0]))
            blocks.append(("\n".join(lines[1:]), None))
        else:
            blocks.append((para, heads[0] if heads and len(lines) == 1 else None))
    _report(progress, stage="read", page=1, pages=1)
    pages, sections = _pages_from_flow(blocks)
    kind = "md" if lower.endswith((".md", ".markdown")) else "html" if lower.endswith((".html", ".htm")) else "txt"
    return {"kind": kind, "pages": pages, "sections": sections, "approx_pages": True}


def is_document(name: str) -> bool:
    return str(name or "").lower().endswith(DOC_EXTENSIONS)


def extract(raw: bytes, name: str, progress: Progress = None) -> Dict[str, Any]:
    """Whole document → ``{kind, pages:[{n, text, label?}], sections:[{title, level, page}], approx_pages}``."""
    if len(raw) > MAX_FILE_BYTES:
        raise BigDocError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
    lower = str(name or "").lower()
    if lower.endswith(".pdf") or raw[:5] == b"%PDF-":
        result = _extract_pdf(raw, progress)
    elif lower.endswith(".docx"):
        result = _extract_docx(raw, progress)
    elif lower.endswith(".epub"):
        result = _extract_epub(raw, progress)
    elif lower.endswith((".doc", ".rtf", ".odt", ".pages", ".djvu", ".mobi", ".azw3")):
        raise BigDocError("Этот формат не читается — сохраните файл как PDF, DOCX, EPUB или TXT",
                          "This format cannot be read — save the file as PDF, DOCX, EPUB or TXT", "format")
    else:
        result = _extract_text(raw, name, progress)
    pages = [p for p in result["pages"] if isinstance(p, dict)]
    if not any(p.get("text") for p in pages):
        if result["kind"] == "pdf":
            raise BigDocError("В PDF нет текстового слоя (это скан) — распознайте его или пришлите текстовую версию",
                              "The PDF has no text layer (a scan) — OCR it or send a text version", "scan")
        raise BigDocError("Из файла не удалось извлечь текст", "No text could be extracted from the file", "empty")
    # sections: sorted, deduplicated, clamped to existing pages
    seen = set()
    sections = []
    for s in sorted(result.get("sections") or [], key=lambda s: (s["page"], s.get("level", 1))):
        key = (s["page"], s["title"].lower())
        if key in seen or not (1 <= s["page"] <= len(pages)):
            continue
        seen.add(key)
        sections.append(s)
    result["sections"] = sections[:2000]
    return result


# ------------------------------------------------------------------ tokenizer / stemmer
_STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж
тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда
можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем
хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это каком какие который которая
которые которого the a an and or of to in on for is are was were be been by with as at from that this it its into
what which who whom whose how why when where does do did not no yes than then there their they them he she we you i
""".split())
_RU_ENDINGS = sorted("""ость ости остью остей остям остями остях ение ения ению ением ении ений ениям ениями ениях ание ания
анию анием ании аний аниям аниями аниях иями ями ами ией иям иях ого его ому ему ыми ими ой ей ий ый ая яя ое ее ую юю ых их
ам ям ах ях ом ем ов ев ей ия ие ии ию ья ье ьи ью ть ти ла ло ли ет ют ут ит ат ят ешь ишь ем им ете ите ал ял ил ыл а я о е
ы и у ю ь""".split(), key=len, reverse=True)
_EN_ENDINGS = ("ingly", "edly", "ments", "ment", "ness", "ings", "ing", "ies", "ied", "ers", "est", "ed", "es", "ly", "er",
               "s")
_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.U)


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if word.isdigit() or len(word) <= 3:
        return word
    if re.match(r"^[а-я]+$", word):
        for end in _RU_ENDINGS:
            if word.endswith(end) and len(word) - len(end) >= 3:
                return word[: len(word) - len(end)]
        return word
    if re.match(r"^[a-z]+$", word):
        for end in _EN_ENDINGS:
            if word.endswith(end) and len(word) - len(end) >= 3:
                return word[: len(word) - len(end)]
    return word


def tokens(text: str, keep_stop: bool = False) -> List[str]:
    out = []
    for raw in _TOKEN_RE.findall(str(text or "")):
        low = raw.lower().replace("ё", "е")
        if not keep_stop and low in _STOP:
            continue
        out.append(stem(low))
    return out


def _trigrams(text: str) -> Counter:
    text = " " + re.sub(r"[^\w]+", " ", str(text or "").lower().replace("ё", "е")) + " "
    return Counter(text[i:i + 3] for i in range(len(text) - 2) if text[i:i + 3].strip())


# ------------------------------------------------------------------ chunking + index
def chunk_pages(pages: List[Dict[str, Any]], sections: List[Dict[str, Any]], size: int = CHUNK_CHARS
                ) -> List[Dict[str, Any]]:
    """Page-bound chunks (a chunk never crosses a page, so its page is exact) with the section they belong to."""
    sec_idx = 0
    chunks: List[Dict[str, Any]] = []
    for page in pages:
        while sec_idx + 1 < len(sections) and sections[sec_idx + 1]["page"] <= page["n"]:
            sec_idx += 1
        section = sections[sec_idx]["title"] if sections and sections[sec_idx]["page"] <= page["n"] else ""
        text = page.get("text") or ""
        if not text.strip():
            continue
        paras = [p for p in re.split(r"\n+", text) if p.strip()]
        buf, cur = [], 0
        pieces: List[str] = []
        for para in paras:
            while len(para) > size * 1.4:
                cut = para.rfind(". ", 0, size)
                cut = cut + 1 if cut > size * 0.4 else size
                if buf:
                    pieces.append("\n".join(buf))
                    buf, cur = [], 0
                pieces.append(para[:cut].strip())
                para = para[cut:].strip()
            if cur + len(para) > size and buf:
                pieces.append("\n".join(buf))
                buf, cur = [], 0
            buf.append(para)
            cur += len(para) + 1
        if buf:
            if pieces and cur < size * 0.25:
                pieces[-1] += "\n" + "\n".join(buf)
            else:
                pieces.append("\n".join(buf))
        for piece in pieces:
            chunks.append({"i": len(chunks), "page": page["n"], "section": section, "text": piece})
    return chunks


class Index:
    """BM25 over stemmed words + trigram cosine; built in memory from the chunks (≈ 100 ms for 600 pages)."""

    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self.chunks = chunks
        self.tf: List[Counter] = []
        self.df: Counter = Counter()
        self.lengths: List[int] = []
        for chunk in chunks:
            words = tokens(chunk["text"] + " " + chunk.get("section", ""))
            tf = Counter(words)
            self.tf.append(tf)
            self.lengths.append(len(words))
            self.df.update(tf.keys())
        self.n = len(chunks)
        self.avgdl = (sum(self.lengths) / self.n) if self.n else 1.0
        self._tri: Optional[List[Tuple[Counter, float]]] = None

    def bm25(self, query: List[str], k1: float = 1.4, b: float = 0.72) -> List[float]:
        scores = [0.0] * self.n
        for term in set(query):
            df = self.df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self.tf):
                f = tf.get(term)
                if f:
                    scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lengths[i] / self.avgdl))
        return scores

    def trigram(self, query: str) -> List[float]:
        if self._tri is None:
            rows = []
            for chunk in self.chunks:
                grams = _trigrams(chunk["text"])
                rows.append((grams, math.sqrt(sum(v * v for v in grams.values())) or 1.0))
            self._tri = rows
        q = _trigrams(query)
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        out = []
        for grams, norm in self._tri:
            dot = sum(v * grams.get(g, 0) for g, v in q.items())
            out.append(dot / (qn * norm))
        return out


_INDEX_CACHE: "OrderedDict[str, Index]" = OrderedDict()


def index_for(doc: Dict[str, Any]) -> Index:
    hit = _INDEX_CACHE.get(doc["id"])
    if hit is not None:
        _INDEX_CACHE.move_to_end(doc["id"])
        return hit
    idx = Index(doc["chunks"])
    _INDEX_CACHE[doc["id"]] = idx
    while len(_INDEX_CACHE) > 4:
        _INDEX_CACHE.popitem(last=False)
    return idx


# ------------------------------------------------------------------ ingest
def summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What the UI chip / API callers need about a document (no page texts)."""
    tops = [s for s in doc.get("sections") or [] if s.get("level", 1) == 1] or doc.get("sections") or []
    return {"id": doc["id"], "name": doc.get("name", ""), "kind": doc.get("kind", ""), "pages": len(doc["pages"]),
            "approx_pages": bool(doc.get("approx_pages")), "chars": doc.get("chars", 0), "tokens": doc.get("tokens", 0),
            "bytes": doc.get("bytes", 0), "chapters": len(tops), "sections": len(doc.get("sections") or []),
            "title": doc.get("title", ""), "toc": [{"title": s["title"], "page": s["page"]} for s in tops[:60]]}


def estimate_tokens(text_or_chars: Any, ratio: float = 1 / 3.3) -> int:
    chars = text_or_chars if isinstance(text_or_chars, int) else len(str(text_or_chars or ""))
    return int(chars * ratio) + 1


_RATIO: "OrderedDict[Tuple[str, str], float]" = OrderedDict()


def token_ratio(doc: Dict[str, Any], model: str = "") -> float:
    """Tokens per character of THIS document for this model's tokenizer family (app.token_estimate on a sample):
    Russian prose is ≈ 1/4.5 for GPT/DeepSeek and ≈ 1/3 for Qwen — a fixed 1/3.3 overestimated the cost by half."""
    key = (doc.get("id", ""), str(model or ""))
    hit = _RATIO.get(key)
    if hit:
        return hit
    sample = ""
    for page in doc.get("pages") or []:
        sample += (page.get("text") or "") + "\n"
        if len(sample) > 60000:
            break
    ratio = 1 / 3.3
    try:
        from . import token_estimate
        if sample.strip():
            ratio = max(1 / 8, min(1.0, token_estimate.estimate(sample, model) / len(sample)))
    except Exception:
        pass
    _RATIO[key] = ratio
    while len(_RATIO) > 64:
        _RATIO.popitem(last=False)
    return ratio


def ingest(raw: bytes, name: str, progress: Progress = None) -> Dict[str, Any]:
    """Extract (or reuse the cached extraction of) a document; returns the full doc dict."""
    doc_id = doc_id_for(raw)
    cached = load_doc(doc_id)
    if cached is not None:
        if name and cached.get("name") != name:
            cached = dict(cached, name=Path(name).name[:200])
        _report(progress, stage="cached")
        return cached
    result = extract(raw, name, progress)
    _report(progress, stage="index")
    pages = result["pages"]
    chunks = chunk_pages(pages, result["sections"])
    chars = sum(len(p.get("text") or "") for p in pages)
    doc = {"v": EXTRACTOR_VERSION, "id": doc_id, "name": Path(str(name or "file")).name[:200], "kind": result["kind"],
           "approx_pages": result["approx_pages"], "pages": pages, "sections": result["sections"], "chunks": chunks,
           "chars": chars, "tokens": 0, "bytes": len(raw), "created": time.time()}
    doc["tokens"] = estimate_tokens(chars, token_ratio(doc))
    if result.get("title"):
        doc["title"] = result["title"]
    save_doc(doc)
    return doc


def ingest_path(path: str, progress: Progress = None) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise BigDocError("Файл не найден", "File not found", "not_found")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise BigDocError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
    return ingest(p.read_bytes(), p.name, progress)


def full_text(doc: Dict[str, Any], with_pages: bool = True, lang: str = "ru") -> str:
    """The whole document with page markers (when it fits the model's context the chat sends it as is)."""
    if not with_pages:
        return "\n\n".join(p.get("text") or "" for p in doc["pages"])
    mark = _tr(lang, "стр.", "p.")
    return "\n\n".join(f"[{mark} {p['n']}]\n{p.get('text') or ''}" for p in doc["pages"] if p.get("text"))


# ------------------------------------------------------------------ retrieval (Q&A)
def _rrf(rankings: Iterable[List[int]], k: int = 60) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            out[i] = out.get(i, 0.0) + 1.0 / (k + rank + 1)
    return out


def subqueries(query: str) -> List[str]:
    """«Какой гарантийный срок и срок поставки?» → the whole question plus its parts, so one aspect of a compound
    question does not crowd out the other (a common word like «срок» matched everywhere)."""
    parts = [p.strip() for p in re.split(r"\s*(?:[,;]|\?|\bи\b|\band\b|\bа также\b|\bas well as\b)\s*", query, flags=re.I)]
    parts = [p for p in parts if len(tokens(p)) >= 1]
    if len(parts) < 2 or len(parts) > 5:
        return [query]
    # a part of a single word borrows the head of the question («Какой срок поставки и оплаты» → «… оплаты» + «срок»)
    head = tokens(parts[0])[:1]
    out = [query]
    for p in parts:
        out.append(p if len(tokens(p)) >= 2 or not head else f"{p} {' '.join(head)}")
    return out


def surface_phrases(query: str) -> List[str]:
    """Adjacent content-word pairs of the question as written (lowercase, ё→е): «Какой срок поставки?» → «срок поставки»."""
    words = [w.lower().replace("ё", "е") for w in re.findall(r"[^\W_]+", query, re.U)]
    out = []
    for a, b in zip(words, words[1:]):
        if a in _STOP or b in _STOP or len(a) < 3 or len(b) < 3 or a.isdigit() and b.isdigit():
            continue
        if f"{a} {b}" not in out:
            out.append(f"{a} {b}")
    return out[:8]


def search(doc: Dict[str, Any], query: str, k: int = 8, embed_scores: Optional[List[float]] = None,
           extra: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The ``k`` best fragments for ``query`` (a compound question: the best of each part first). ``extra`` — the same
    question in the document's language (:func:`translate_query`); its parts join the round-robin right after the
    original whole question, so a Russian question over an English book still finds the page."""
    queries = subqueries(query)
    for alt in extra or []:
        alt_parts = subqueries(alt)
        queries[1:1] = [q for q in alt_parts if q not in queries]
    if len(queries) == 1:
        return _search_one(doc, query, k, embed_scores)
    lists = [_search_one(doc, q, k, embed_scores if i == 0 else None) for i, q in enumerate(queries)]
    out: List[Dict[str, Any]] = []
    seen = set()
    for rank in range(k):                 # round-robin: whole question, part 1, part 2, …
        for hits in lists:
            if rank < len(hits) and hits[rank]["i"] not in seen and len(out) < k + len(queries) - 1:
                seen.add(hits[rank]["i"])
                out.append(hits[rank])
    return out


def _search_one(doc: Dict[str, Any], query: str, k: int = 8, embed_scores: Optional[List[float]] = None
                ) -> List[Dict[str, Any]]:
    """The ``k`` best fragments for ``query``: ``[{i, page, label, section, text, score}]`` (page order kept by rank)."""
    idx = index_for(doc)
    if not idx.n:
        return []
    q_tokens = tokens(query)
    bm = idx.bm25(q_tokens) if q_tokens else [0.0] * idx.n
    tri = idx.trigram(query)
    top = 150
    rankings = [sorted(range(idx.n), key=lambda i: -bm[i])[:top], sorted(range(idx.n), key=lambda i: -tri[i])[:top]]
    if embed_scores and len(embed_scores) == idx.n:
        rankings.append(sorted(range(idx.n), key=lambda i: -embed_scores[i])[:top])
    # exact phrases of the question («срок поставки», «гарантийный срок»): when both words are everywhere in a
    # contract, BM25 is flat and only the page that SAYS the phrase is the answer — a ranking of its own
    phrases = surface_phrases(query)
    phrase_hits: Dict[int, int] = {}
    if phrases:
        for i, chunk in enumerate(idx.chunks):
            low = chunk["text"].lower().replace("ё", "е")
            hits = sum(1 for ph in phrases if ph in low)
            if hits:
                phrase_hits[i] = hits
        if phrase_hits:
            rankings.append(sorted(phrase_hits, key=lambda i: (-phrase_hits[i], -bm[i]))[:top])
    fused = _rrf(rankings)
    # rerank: coverage of distinct query terms, exact numbers, adjacent word pairs (phrases)
    q_set = set(q_tokens)
    numbers = set(re.findall(r"\d+(?:[.,]\d+)?", query))
    pairs = set(zip(q_tokens, q_tokens[1:]))
    scored = []
    for i, base in fused.items():
        tf = idx.tf[i]
        cover = (sum(1 for t in q_set if t in tf) / len(q_set)) if q_set else 0.0
        text = idx.chunks[i]["text"]
        num_hit = sum(1 for n in numbers if n in text)
        chunk_tokens = tokens(text)
        pair_hit = sum(1 for p in zip(chunk_tokens, chunk_tokens[1:]) if p in pairs)
        # adjacent query words in the same order («срок поставки») are strong evidence: a page that merely mentions
        # both words somewhere loses to the one that says the phrase
        score = (base * (1 + 1.5 * cover) + 0.004 * num_hit + 0.011 * min(pair_hit, 3) + 0.0008 * bm[i]
                 + 0.02 * min(phrase_hits.get(i, 0), 2))
        scored.append((score, i))
    scored.sort(reverse=True)
    out = []
    seen = set()
    for score, i in scored:
        if len(out) >= k:
            break
        if i in seen:
            continue
        seen.add(i)
        chunk = idx.chunks[i]
        page = doc["pages"][chunk["page"] - 1] if 0 < chunk["page"] <= len(doc["pages"]) else {}
        out.append({"i": i, "page": chunk["page"], "label": page.get("label", ""), "section": chunk.get("section", ""),
                    "text": chunk["text"], "score": round(score, 5)})
    return out


def page_mark(page: int, lang: str = "ru", approx: bool = False) -> str:
    return f"[{'≈' if approx else ''}{_tr(lang, 'стр.', 'p.')} {page}]"


_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


def script_of(text: str) -> str:
    """«cyr» / «lat» by the letters of ``text`` ('' when there are too few letters to tell)."""
    cyr, lat = len(_CYR.findall(text or "")), len(_LAT.findall(text or ""))
    if cyr + lat < 6:
        return ""
    return "cyr" if cyr >= lat * 1.5 else "lat" if lat >= cyr * 1.5 else ""


def doc_script(doc: Dict[str, Any]) -> str:
    if "_script" not in doc:
        chunks = doc.get("chunks") or []
        step = max(1, len(chunks) // 60)
        doc["_script"] = script_of(" ".join(c.get("text", "")[:300] for c in chunks[::step][:60]))
    return doc["_script"]


def needs_translation(doc: Dict[str, Any], query: str) -> bool:
    """No embeddings in the Proxy → the local search is lexical: a question in another script than the document
    (a Russian question over an English book) would find nothing. Then the query is translated first."""
    a, b = script_of(query), doc_script(doc)
    return bool(a and b and a != b)


_TRANSLATIONS: "OrderedDict[Tuple[str, str], str]" = OrderedDict()


async def translate_query(doc: Dict[str, Any], query: str, model: str, complete: Optional[Complete],
                          timeout: float = 25.0) -> str:
    """The question rewritten into the document's language (search keywords), '' when not needed or it failed."""
    if not model or complete is None or not query.strip() or not needs_translation(doc, query):
        return ""
    target = "English" if doc_script(doc) == "lat" else "Russian"
    key = (query.strip()[:500], target)
    if key in _TRANSLATIONS:
        return _TRANSLATIONS[key]
    messages = [{"role": "system", "content": f"Translate the user's question into {target} for a keyword search in a "
                 f"{target} document. Keep names, numbers and terms. Output ONLY the translation, one line."},
                {"role": "user", "content": query.strip()[:1500]}]
    try:
        text = await asyncio.wait_for(complete(model, messages), timeout)
    except asyncio.CancelledError:
        raise
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip("«»\"'")[:600]
    if not text or script_of(text) == script_of(query):
        return ""
    _TRANSLATIONS[key] = text
    while len(_TRANSLATIONS) > 200:
        _TRANSLATIONS.popitem(last=False)
    return text


async def qa_context_async(doc: Dict[str, Any], question: str, k: int = 8, lang: str = "ru", model: str = "",
                           complete: Optional[Complete] = None) -> Dict[str, Any]:
    """:func:`qa_context` with the cross-language step (``translated`` says what was searched besides the question)."""
    alt = await translate_query(doc, question, model, complete)
    ctx = await asyncio.to_thread(qa_context, doc, question, k, lang, None, [alt] if alt else None)
    ctx["translated"] = alt
    return ctx


def qa_context(doc: Dict[str, Any], question: str, k: int = 8, lang: str = "ru",
               fragments: Optional[List[Dict[str, Any]]] = None, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fragments + the prompt block a chat model gets instead of the whole document."""
    frags = fragments if fragments is not None else search(doc, question, k=k, extra=extra)
    approx = bool(doc.get("approx_pages"))
    mark = _tr(lang, "стр.", "p.")
    lines = []
    for f in sorted(frags, key=lambda f: (f["page"], f["i"])):
        head = f"[{mark} {f['page']}]" + (f" ({f['section']})" if f.get("section") else "")
        lines.append(head + "\n" + f["text"])
    pages_word = "≈" if approx else ""
    block = (f'<document name="{_attr(doc.get("name", ""))}" pages="{pages_word}{len(doc["pages"])}" mode="retrieval">\n'
             + ("Fragments the local search found for the question (NOT the whole document). Each starts with its page."
                if lang == "en" else
                "Фрагменты, которые локальный поиск нашёл по вопросу (это НЕ весь документ). Перед каждым — его страница.")
             + "\n\n" + "\n\n".join(lines) + "\n</document>")
    if lang == "en":
        rule = ("Answer from these fragments only. After every claim cite its page exactly as [p. N] (several: [p. 3], [p. 7]) "
                "and quote the decisive words in quotation marks. Never cite a page that is not among the fragments. "
                "If the fragments do not contain the answer, say so plainly and suggest the «Whole document» mode.")
    else:
        rule = ("Отвечай только по этим фрагментам. После каждого утверждения ставь ссылку на страницу строго в виде "
                "[стр. N] (несколько — [стр. 3], [стр. 7]) и приводи решающие слова в кавычках «…». Не ссылайся на страницы, "
                "которых нет среди фрагментов. Если во фрагментах ответа нет — прямо скажи об этом и предложи режим "
                "«Весь документ».")
    if approx:
        rule += (" Page numbers are approximate (the file has no fixed pages)." if lang == "en"
                 else " Номера страниц приблизительные (в файле нет фиксированных страниц).")
    return {"fragments": frags, "block": block, "instruction": rule, "pages": sorted({f["page"] for f in frags})}


def _attr(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


_CITE_RE = re.compile(r"\[(≈)?\s*(?:стр\.?|страницы?|с\.|p\.|pp\.|page|pages)\s*([0-9][0-9,\s–\-—и and]*?)"
                      r"(?:[,;]\s*(?:§|п\.|пп\.|para\.?|section|раздел|разд\.)[^\]\[]{0,40})?\]", re.I)   # «[p. 57, § 57.2]» too


def cite_pages(mark_body: str) -> List[int]:
    pages: List[int] = []
    for part in re.split(r"[,;]|\sи\s|\sand\s", mark_body):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[–\-—]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < a <= b and b - a <= 20:
                pages.extend(range(a, b + 1))
            continue
        if part.isdigit():
            pages.append(int(part))
    return pages


def _sentences(text: str) -> List[str]:
    out = []
    for s in re.split(r"(?<=[.!?…])\s+|\n+", text):
        s = s.strip()
        if len(s) > 3 and len(re.findall(r"[^\W\d_]", s)) >= 4:     # not a bare «57.1.» clause number
            out.append(s)
    return out


def best_quote(doc: Dict[str, Any], page: int, claim: str, max_len: int = 260) -> Tuple[str, float]:
    """The sentence of ``page`` closest to ``claim`` (quoted words first, then stemmed overlap)."""
    if not (0 < page <= len(doc["pages"])):
        return "", 0.0
    text = doc["pages"][page - 1].get("text") or ""
    sents = _sentences(text)
    if not sents:
        return "", 0.0
    quoted = re.findall(r"[«\"“„]([^»\"”]{4,200})[»\"”]", claim)
    for q in quoted:
        qn = re.sub(r"\s+", " ", q.lower().replace("ё", "е")).strip()
        for s in sents:
            if qn and qn in re.sub(r"\s+", " ", s.lower().replace("ё", "е")):
                return s[:max_len], 1.0
    claim_t = set(tokens(claim))
    if not claim_t:
        return sents[0][:max_len], 0.0
    best, best_score = sents[0], 0.0
    for s in sents:
        st = set(tokens(s))
        if not st:
            continue
        score = len(claim_t & st) / (len(claim_t) ** 0.5 * len(st) ** 0.5)
        if score > best_score:
            best, best_score = s, score
    return best[:max_len], round(best_score, 3)


def cite(doc: Dict[str, Any], answer: str, fragments: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every ``[стр. N]`` mark of an answer → ``{mark, pages:[{page, quote, score, verified, retrieved}]}``.

    ``verified`` — the page exists and its best sentence shares words with the claim the mark follows;
    ``retrieved`` — the page was among the fragments the model was given (a page outside them is suspicious)."""
    retrieved = {f["page"] for f in fragments or []}
    out = []
    for m in _CITE_RE.finditer(answer or ""):
        start = max(answer.rfind("\n", 0, m.start()), max(answer.rfind(". ", 0, m.start()), answer.rfind("] ", 0, m.start())))
        claim = answer[max(0, start + 1, m.start() - 400):m.start()]
        rows = []
        for page in cite_pages(m.group(2))[:6]:
            quote, score = best_quote(doc, page, claim)
            rows.append({"page": page, "label": (doc["pages"][page - 1].get("label", "") if 0 < page <= len(doc["pages"]) else ""),
                         "quote": quote, "score": score, "exists": 0 < page <= len(doc["pages"]),
                         "verified": bool(quote) and score >= 0.18, "retrieved": (page in retrieved) if fragments else None})
        if rows:
            out.append({"mark": m.group(0), "start": m.start(), "end": m.end(), "pages": rows})
    return out


def citations_text(cites: List[Dict[str, Any]], lang: str = "ru", limit: int = 6) -> str:
    """Plain-text footnotes for Telegram: «стр. 12 — «quote»»."""
    seen = set()
    lines = []
    for c in cites:
        for row in c["pages"]:
            if row["page"] in seen or not row.get("quote"):
                continue
            seen.add(row["page"])
            lines.append(f"• {_tr(lang, 'стр.', 'p.')} {row['page']} — «{row['quote'][:180]}»")
            if len(lines) >= limit:
                return "\n".join(lines)
    return "\n".join(lines)


async def answer_question(doc: Dict[str, Any], question: str, model: str, complete: Complete, *, lang: str = "ru",
                          k: int = 8, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Q&A in one call (Telegram, Quick Access, tests): retrieval → model → citations."""
    ctx = await qa_context_async(doc, question, k=k, lang=lang, model=model, complete=complete)
    system = ("You answer questions about a long document using fragments found by a local search. " + ctx["instruction"])
    messages = [{"role": "system", "content": system}] + list(history or [])[-6:] + [
        {"role": "user", "content": ctx["block"] + "\n\n" + _tr(lang, "Вопрос: ", "Question: ") + question}]
    text = await complete(model, messages)
    return {"answer": text, "fragments": ctx["fragments"], "citations": cite(doc, text, ctx["fragments"])}


# ------------------------------------------------------------------ whole document: plan, estimate, map-reduce
def plan_parts(doc: Dict[str, Any], part_tokens: int = DEFAULT_PART_TOKENS) -> List[Dict[str, Any]]:
    """Parts along the document's structure: ``[{index, title, page_start, page_end, chars, chapter}]``.

    Top-level sections become parts; small neighbours are merged, a section bigger than ``part_tokens`` is split
    by pages. Without a usable structure the document is cut into equal page ranges."""
    pages = doc["pages"]
    n = len(pages)
    limit_chars = max(4000, int(part_tokens * 3.3))
    lens = [len(p.get("text") or "") for p in pages]
    level1 = [s for s in doc.get("sections") or [] if s.get("level", 1) == 1]
    tops = level1 if 2 <= len(level1) <= 400 else [s for s in doc.get("sections") or [] if s.get("level", 1) <= 2]
    if not (2 <= len(tops) <= 400):
        tops = []
    # dedupe by page, keep the first title on a page
    starts: List[Tuple[int, str]] = []
    for s in tops:
        if not starts or s["page"] != starts[-1][0]:
            starts.append((s["page"], s["title"]))
    if starts and starts[0][0] > 1:
        starts.insert(0, (1, ""))
    ranges: List[Tuple[int, int, str, bool]] = []    # (start, end, title, is_chapter)
    if starts:
        for j, (start, title) in enumerate(starts):
            end = (starts[j + 1][0] - 1) if j + 1 < len(starts) else n
            if end >= start:
                ranges.append((start, end, title, bool(title)))
    else:
        ranges.append((1, n, "", False))
    # split big ranges by pages
    split: List[Tuple[int, int, str, bool]] = []
    for start, end, title, chapter in ranges:
        size = sum(lens[start - 1:end])
        if size <= limit_chars:
            split.append((start, end, title, chapter))
            continue
        pieces = max(2, math.ceil(size / limit_chars))
        target = size / pieces
        cur_start, acc, piece = start, 0, 1
        for p in range(start, end + 1):
            acc += lens[p - 1]
            if acc >= target and p < end and piece < pieces:
                split.append((cur_start, p, title + (f" ({piece})" if title else ""), chapter))
                cur_start, acc, piece = p + 1, 0, piece + 1
        split.append((cur_start, end, title + (f" ({piece})" if title and piece > 1 else ""), chapter))
    # merge small neighbours (front matter, short chapters) while they fit
    merged: List[List[Any]] = []
    for start, end, title, chapter in split:
        size = sum(lens[start - 1:end])
        if merged:
            prev = merged[-1]
            if prev[4] + size <= limit_chars * 0.6 or (size < limit_chars * 0.08 and prev[4] + size <= limit_chars):
                prev[1] = end
                prev[4] += size
                if title and not prev[2]:
                    prev[2] = title
                elif title:
                    prev[2] = prev[2] + " · " + title if len(prev[2]) < 80 else prev[2]
                prev[3] = prev[3] or chapter
                continue
        merged.append([start, end, title, chapter, size])
    parts = []
    for i, (start, end, title, chapter, size) in enumerate(merged, 1):
        if size == 0:
            continue
        parts.append({"index": len(parts) + 1, "title": title[:160], "page_start": start, "page_end": end,
                      "chars": size, "chapter": bool(chapter)})
    return parts


def part_text(doc: Dict[str, Any], part: Dict[str, Any], lang: str = "ru") -> str:
    mark = _tr(lang, "стр.", "p.")
    return "\n\n".join(f"[{mark} {p['n']}]\n{p['text']}" for p in doc["pages"][part["page_start"] - 1:part["page_end"]]
                       if p.get("text"))


def unit_word(parts: List[Dict[str, Any]], lang: str = "ru") -> str:
    """«глава» when the parts follow the document's chapters, «часть» otherwise."""
    chapters = sum(1 for p in parts if p.get("chapter"))
    if chapters >= max(2, len(parts) * 0.6):
        return _tr(lang, "глава", "chapter")
    return _tr(lang, "часть", "part")


def progress_label(done: int, total: int, unit: str, lang: str = "ru") -> str:
    return f"{unit} {done} {_tr(lang, 'из', 'of')} {total}"


def _reduce_levels(n_items: int, per_item_tokens: int, budget_tokens: int) -> List[int]:
    """How many reduce calls each level needs (hierarchical reduce), bottom-up."""
    levels = []
    items = n_items
    per_group = max(2, budget_tokens // max(1, per_item_tokens))
    while items > per_group:
        groups = math.ceil(items / per_group)
        levels.append(groups)
        items = groups
        per_item_tokens = REDUCE_OUTPUT_TOKENS
        per_group = max(2, budget_tokens // per_item_tokens)
    levels.append(1)
    return levels


def estimate(doc: Dict[str, Any], model: str, *, prompt: str = "", part_tokens: int = DEFAULT_PART_TOKENS,
             confirm_usd: float = DEFAULT_CONFIRM_USD, lang: str = "ru", provider: str = "") -> Dict[str, Any]:
    """Tokens, requests and price of a whole-document run BEFORE it starts."""
    parts = plan_parts(doc, part_tokens)
    ratio = token_ratio(doc, model)
    overhead = 350 + estimate_tokens(prompt)
    map_in = sum(estimate_tokens(p["chars"], ratio) + overhead for p in parts)
    map_out = len(parts) * MAP_OUTPUT_TOKENS
    levels = _reduce_levels(len(parts), MAP_OUTPUT_TOKENS, max(6000, part_tokens))
    reduce_calls = sum(levels)
    reduce_in = len(parts) * MAP_OUTPUT_TOKENS + max(0, reduce_calls - 1) * REDUCE_OUTPUT_TOKENS + reduce_calls * overhead
    reduce_out = reduce_calls * REDUCE_OUTPUT_TOKENS
    tokens_in, tokens_out = map_in + reduce_in, map_out + reduce_out
    cached = run_cache_get(doc["id"], model, "whole", prompt) is not None
    map_state = _run_load(doc["id"], map_key(doc["id"], model, part_tokens)) or {}
    done_idx = {str(k) for k in (map_state.get("parts") or {}) if str(k).isdigit()}
    done_parts = sum(1 for p in parts if str(p["index"]) in done_idx)
    if done_parts:      # finished parts are not paid for again
        map_in -= sum(estimate_tokens(p["chars"], ratio) + overhead for p in parts if str(p["index"]) in done_idx)
        map_out -= done_parts * MAP_OUTPUT_TOKENS
        tokens_in, tokens_out = map_in + reduce_in, map_out + reduce_out
    if cached:
        tokens_in = tokens_out = 0
    cost, billing, price_info, known = 0.0, "unknown", None, False
    try:
        from . import pricing
        est = pricing.estimate(model, input_tokens=tokens_in, output_tokens=tokens_out, provider=provider)
        cost, billing = float(est.cost if hasattr(est, "cost") else est[0]), est.billing if hasattr(est, "billing") else est[2]
        price = est.price if hasattr(est, "price") else est[1]
        known = price is not None
        price_info = price.to_dict() if price is not None else None
        if billing == "local":
            known = True
    except Exception:
        pass
    needs_confirm = (known and billing == "api" and cost > float(confirm_usd)) or (
        not known and tokens_in > UNKNOWN_PRICE_CONFIRM_TOKENS)
    unit = unit_word(parts, lang)
    try:
        from .pricing import format_usd
        cost_text = format_usd(cost)
    except Exception:
        cost_text = f"${cost:.2f}"
    return {"parts": len(parts), "unit": unit, "requests": 0 if cached else len(parts) - done_parts + reduce_calls, "reduce_levels": len(levels),
            "input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": round(cost, 4), "cost_text": cost_text,
            "price_known": known, "billing": billing, "price": price_info, "needs_confirm": bool(needs_confirm) and not cached,
            "confirm_usd": confirm_usd, "cached": cached, "resumable_parts": done_parts if not cached else 0,
            "plan": [{"index": p["index"], "title": p["title"], "pages": [p["page_start"], p["page_end"]]} for p in parts]}


def map_key(doc_id: str, model: str, part_tokens: int) -> str:
    """Part summaries do not depend on the user's request: any later «Весь документ» question reuses them."""
    return run_key(doc_id, model, "map", f"pt{int(part_tokens)}")


def run_key(doc_id: str, model: str, mode: str, prompt: str) -> str:
    raw = json.dumps([doc_id, model, mode, re.sub(r"\s+", " ", str(prompt or "").strip().lower()), EXTRACTOR_VERSION, 1])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _run_path(doc_id: str, key: str) -> Path:
    return _doc_path(doc_id) / "runs" / (key + ".json")


def _run_load(doc_id: str, key: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_run_path(doc_id, key).read_text("utf-8"))
    except Exception:
        return None


_RUN_LOCK = threading.Lock()


def _run_save(doc_id: str, key: str, data: Dict[str, Any]) -> None:
    path = _run_path(doc_id, key)
    with _RUN_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".%s.tmp" % uuid.uuid4().hex[:6])
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)


def run_cache_get(doc_id: str, model: str, mode: str, prompt: str) -> Optional[Dict[str, Any]]:
    data = _run_load(doc_id, run_key(doc_id, model, mode, prompt))
    if data and data.get("final"):
        return data
    return None


def _map_prompt(doc: Dict[str, Any], part: Dict[str, Any], total: int, unit: str, prompt: str, lang: str) -> str:
    title = f" «{part['title']}»" if part.get("title") else ""
    pages = f"{part['page_start']}–{part['page_end']}"
    if lang == "en":
        return (f"Document “{doc.get('name', '')}”, {unit} {part['index']} of {total}{title}, pages {pages}.\n"
                "Summarize THIS part faithfully and densely: key events/arguments, definitions, names, numbers, dates, "
                "obligations and conditions. Keep page references as [p. N] for important facts. No introduction."
                + (f"\nThe user's final goal (note everything relevant to it): {prompt}" if prompt else ""))
    return (f"Документ «{doc.get('name', '')}», {unit} {part['index']} из {total}{title}, страницы {pages}.\n"
            "Перескажи ЭТУ часть точно и плотно: ключевые события/аргументы, определения, имена, числа, даты, обязательства "
            "и условия. Для важных фактов сохраняй ссылки на страницы в виде [стр. N]. Без вступлений."
            + (f"\nИтоговая задача пользователя (отметь всё, что к ней относится): {prompt}" if prompt else ""))


def _reduce_prompt(doc: Dict[str, Any], prompt: str, final: bool, lang: str, level: int) -> str:
    if lang == "en":
        if not final:
            return ("Below are consecutive part summaries of one document. Merge them into one shorter summary that keeps "
                    "all key facts, numbers and the [p. N] references, in order.")
        return (f"Below are summaries of all parts of the document “{doc.get('name', '')}” in order (pages {len(doc['pages'])}). "
                + (f"Task: {prompt}\n" if prompt else
                   "Write a structured overview of the WHOLE document: what it is, the main content part by part, key "
                   "facts/numbers/terms, conclusions.\n")
                + "Keep [p. N] references for important facts. Do not invent anything that is not in the summaries.")
    if not final:
        return ("Ниже — пересказы идущих подряд частей одного документа. Сведи их в один более короткий пересказ, сохранив "
                "все ключевые факты, числа и ссылки [стр. N], по порядку.")
    return (f"Ниже — пересказы всех частей документа «{doc.get('name', '')}» по порядку (страниц: {len(doc['pages'])}). "
            + (f"Задача: {prompt}\n" if prompt else
               "Напиши структурированный обзор ВСЕГО документа: что это, основное содержание по частям, ключевые "
               "факты/числа/термины, выводы.\n")
            + "Для важных фактов сохраняй ссылки [стр. N]. Не добавляй ничего, чего нет в пересказах.")


class Job:
    """One whole-document run: events for the UI, cancel flag, the asyncio task."""

    def __init__(self, doc: Dict[str, Any], model: str, prompt: str, lang: str, part_tokens: int) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.doc_id = doc["id"]
        self.name = doc.get("name", "")
        self.model = model
        self.prompt = prompt
        self.lang = lang
        self.part_tokens = part_tokens
        self.key = run_key(doc["id"], model, "whole", prompt)
        self.status = "running"          # running | done | error | cancelled
        self.events: List[Dict[str, Any]] = []
        self.result = ""
        self.error = ""
        self.done = 0
        self.total = 0
        self.unit = ""
        self.label = ""
        self.cancel = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self.changed = asyncio.Event()
        self.started = time.time()
        self.finished = 0.0
        self.retries = 0
        self.serial = False
        self.cached = False

    def emit(self, **event: Any) -> None:
        event.setdefault("t", round(time.time() - self.started, 2))
        self.events.append(event)
        if len(self.events) > 600:
            del self.events[1:200]
        self.changed.set()

    def state(self) -> Dict[str, Any]:
        return {"id": self.id, "doc_id": self.doc_id, "name": self.name, "model": self.model, "status": self.status,
                "done": self.done, "total": self.total, "unit": self.unit, "label": self.label, "error": self.error,
                "result": self.result if self.status == "done" else "", "elapsed": round((self.finished or time.time()) - self.started, 1),
                "stage": (self.events[-1].get("stage") if self.events else ""), "cached": self.cached,
                "retries": self.retries}


JOBS: "OrderedDict[str, Job]" = OrderedDict()


def _gc_jobs() -> None:
    while len(JOBS) > 40:
        oldest = next(iter(JOBS))
        if JOBS[oldest].status == "running":
            break
        JOBS.pop(oldest)


def find_running(doc_id: str, model: str, prompt: str) -> Optional[Job]:
    key = run_key(doc_id, model, "whole", prompt)
    for job in JOBS.values():
        if job.key == key and job.status == "running":
            return job
    return None


CALL_ATTEMPTS = 4
_RETRY_DELAYS = (2.0, 6.0, 15.0)
#: r36: one map/reduce request that streams nothing for this long is abandoned and retried — live runs (ReformBoss,
#: OpenRouter free) froze forever on a stalled stream: «Глава 3 из 6 · 5/6» for many minutes, no retry, no error
CALL_TIMEOUT = 300.0
_FATAL_RE = re.compile(r"HTTP 40[0134]\b|не наш[её]л модель|does not know model|ключ|API key|квот|quota|no completion bridge"
                       r"|модель не (?:найдена|поддерживается)|model_not_found|insufficient", re.I)


def is_fatal(exc: BaseException) -> bool:
    """A refusal another attempt cannot fix (wrong model, rejected key, exhausted quota)."""
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 425, 429):
        return True
    return bool(_FATAL_RE.search(str(exc)))


async def call_robust(job: "Job", complete: Complete, messages: List[Dict[str, Any]], *, index: Any = None,
                      serial: Optional[asyncio.Lock] = None, attempts: int = CALL_ATTEMPTS) -> str:
    """One map/reduce request with retries: a dropped stream («Connection closed abruptly»), a timeout or an empty
    answer is retried with a growing pause; after the first failure the run switches to one request at a time
    (``job.serial``) — gateways that drop parallel long streams then usually finish. Fatal refusals are not retried.
    Finished parts are already on disk, so even a run that gives up resumes from the last finished part."""
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        if job.cancel.is_set():
            raise asyncio.CancelledError()
        try:
            if job.serial and serial is not None:
                async with serial:
                    if job.cancel.is_set():
                        raise asyncio.CancelledError()
                    text = (await asyncio.wait_for(complete(job.model, messages), CALL_TIMEOUT) or "").strip()
            else:
                text = (await asyncio.wait_for(complete(job.model, messages), CALL_TIMEOUT) or "").strip()
            if text:
                return text
            last = RuntimeError(_tr(job.lang, "пустой ответ модели", "empty model answer"))
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            last = RuntimeError(_tr(job.lang, f"модель не ответила за {int(CALL_TIMEOUT)} с", f"no answer in {int(CALL_TIMEOUT)} s"))
        except Exception as exc:
            if is_fatal(exc):
                raise
            last = exc
        job.retries += 1
        job.serial = True
        if attempt == attempts - 1:
            break
        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
        job.emit(stage="retry", index=index, attempt=attempt + 2, attempts=attempts, wait=delay,
                 error=str(last)[:200])
        try:
            await asyncio.wait_for(job.cancel.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
    raise last if last is not None else RuntimeError("no answer")


async def run_whole(job: Job, doc: Dict[str, Any], complete: Complete) -> str:
    """Map every part (cached ones are skipped → resume), then reduce hierarchically."""
    lang = job.lang
    cached = run_cache_get(doc["id"], job.model, "whole", job.prompt)
    if cached:
        job.status, job.result, job.finished = "done", cached["final"], time.time()
        job.emit(stage="done", cached=True, result=job.result)
        return job.result
    parts = plan_parts(doc, job.part_tokens)
    unit = unit_word(parts, lang)
    job.unit, job.total = unit, len(parts)
    mkey = map_key(doc["id"], job.model, job.part_tokens)
    state = _run_load(doc["id"], mkey) or {}
    results: Dict[str, str] = dict(state.get("parts") or {})
    state = {"doc_id": doc["id"], "model": job.model, "parts": results, "total": len(parts),
             "created": state.get("created") or time.time()}
    job.done = sum(1 for p in parts if str(p["index"]) in results)
    job.emit(stage="plan", total=len(parts), unit=unit, resumed=job.done,
             plan=[{"index": p["index"], "title": p["title"], "pages": [p["page_start"], p["page_end"]]} for p in parts])
    gate = asyncio.Semaphore(MAP_CONCURRENCY)
    order_lock = asyncio.Lock()
    serial = asyncio.Lock()          # after a dropped stream the rest of the run goes one request at a time

    failed: List[BaseException] = []    # the first failure stops parts still waiting for the gate

    async def call(messages: List[Dict[str, Any]], index: Any) -> str:
        try:
            return await call_robust(job, complete, messages, index=index, serial=serial)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed.append(exc)
            raise

    async def one(part: Dict[str, Any]) -> None:
        key = str(part["index"])
        if key in results:
            return
        async with gate:
            if job.cancel.is_set() or failed:
                return
            job.emit(stage="map-start", index=part["index"], title=part["title"],
                     pages=[part["page_start"], part["page_end"]],
                     label=progress_label(part["index"], len(parts), unit, lang))
            messages = [{"role": "system", "content": _map_prompt(doc, part, len(parts), unit, "", lang)},
                        {"role": "user", "content": "<part>\n" + part_text(doc, part, lang) + "\n</part>"}]
            text = await call(messages, part["index"])
            if not text:
                raise RuntimeError(_tr(lang, "модель вернула пустой пересказ", "the model returned an empty summary"))
        async with order_lock:
            results[key] = text
            job.done += 1
            job.label = progress_label(job.done, len(parts), unit, lang)
            await asyncio.to_thread(_run_save, doc["id"], mkey, state)
            job.emit(stage="map", done=job.done, total=len(parts), index=part["index"], title=part["title"],
                     label=job.label, preview=text[:220])

    tasks = [asyncio.ensure_future(one(p)) for p in parts]
    cancel_wait = asyncio.ensure_future(job.cancel.wait())
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending | {cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done:
                for t in pending:
                    t.cancel()
                raise asyncio.CancelledError()
            pending.discard(cancel_wait)
            for t in done:
                if t is not cancel_wait and t.exception():
                    for other in pending:
                        other.cancel()
                    raise t.exception()  # type: ignore[misc]
    except BaseException:
        # cancelled / failed: whatever finished is on disk before the job reports its end (a resume must see it)
        try:
            _run_save(doc["id"], mkey, state)
        except Exception:
            pass
        raise
    finally:
        cancel_wait.cancel()
    summaries = [results[str(p["index"])] for p in parts]
    titles = [p["title"] for p in parts]
    level = 0
    budget_chars = int(max(6000, job.part_tokens) * 3.3)
    head_word = unit.capitalize()
    items = [f"## {head_word} {i + 1}" + (f": {titles[i]}" if titles[i] else "") + f"\n{s}" for i, s in enumerate(summaries)]
    while True:
        level += 1
        total_chars = sum(len(x) for x in items)
        final = total_chars <= budget_chars or len(items) <= 2
        if final:
            job.emit(stage="reduce", level=level, final=True,
                     label=_tr(lang, "Свожу всё вместе…", "Putting it all together…"))
            messages = [{"role": "system", "content": _reduce_prompt(doc, job.prompt, True, lang, level)},
                        {"role": "user", "content": "\n\n".join(items)}]
            if job.cancel.is_set():
                raise asyncio.CancelledError()
            result = await call(messages, "reduce")
            break
        groups: List[List[str]] = []
        cur: List[str] = []
        size = 0
        for x in items:
            if cur and size + len(x) > budget_chars:
                groups.append(cur)
                cur, size = [], 0
            cur.append(x)
            size += len(x)
        if cur:
            groups.append(cur)
        if len(groups) == len(items):          # nothing merges (huge summaries): pair them up
            groups = [items[i:i + 2] for i in range(0, len(items), 2)]
        new_items = []
        for gi, group in enumerate(groups, 1):
            if job.cancel.is_set():
                raise asyncio.CancelledError()
            cache_key = f"r{level}:{gi}:{hashlib.sha1((job.prompt + ''.join(group)).encode('utf-8')).hexdigest()[:10]}"
            text = results.get(cache_key)
            if not text:
                job.emit(stage="reduce", level=level, done=gi - 1, total=len(groups),
                         label=_tr(lang, f"Свод: уровень {level}, группа {gi} из {len(groups)}",
                                   f"Reduce: level {level}, group {gi} of {len(groups)}"))
                messages = [{"role": "system", "content": _reduce_prompt(doc, job.prompt, False, lang, level)},
                            {"role": "user", "content": "\n\n".join(group)}]
                text = await call(messages, f"reduce-{level}-{gi}")
                if not text:
                    raise RuntimeError(_tr(lang, "модель вернула пустой свод", "the model returned an empty summary"))
                results[cache_key] = text
                await asyncio.to_thread(_run_save, doc["id"], mkey, state)
            new_items.append(f"## {_tr(lang, 'Блок', 'Block')} {gi}\n{text}")
        items = new_items
    if not result:
        raise RuntimeError(_tr(lang, "модель вернула пустой свод", "the model returned an empty summary"))
    final = {"doc_id": doc["id"], "model": job.model, "prompt": job.prompt, "final": result, "parts": len(parts),
             "unit": unit, "finished": time.time()}
    await asyncio.to_thread(_run_save, doc["id"], job.key, final)
    job.result, job.status, job.finished = result, "done", time.time()
    job.emit(stage="done", result=result)
    return result


def start_job(doc: Dict[str, Any], model: str, complete: Complete, *, prompt: str = "", lang: str = "ru",
              part_tokens: int = DEFAULT_PART_TOKENS) -> Job:
    """Start (or join the running) whole-document job; must be called inside the event loop."""
    running = find_running(doc["id"], model, prompt)
    if running is not None:
        return running
    job = Job(doc, model, prompt, lang, part_tokens)
    JOBS[job.id] = job
    _gc_jobs()
    cached = run_cache_get(doc["id"], model, "whole", prompt)
    if cached:      # a cache hit answers at once: the route returns status «done» with the result, no task at all
        job.status, job.result, job.finished = "done", cached["final"], time.time()
        job.total = job.done = int(cached.get("parts") or 0)
        job.unit = str(cached.get("unit") or "")
        job.cached = True
        job.emit(stage="done", cached=True, result=job.result)
        return job

    async def runner() -> None:
        try:
            await run_whole(job, doc, complete)
        except asyncio.CancelledError:
            job.status, job.finished = "cancelled", time.time()
            job.emit(stage="cancelled", done=job.done, total=job.total)
        except Exception as exc:
            job.status, job.error, job.finished = "error", str(exc)[:500], time.time()
            job.emit(stage="error", error=job.error, done=job.done, total=job.total)
    job.task = asyncio.ensure_future(runner())
    return job


def cancel_job(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if job is None or job.status != "running":
        return False
    job.cancel.set()
    return True


async def job_events(job: Job, since: int = 0):
    """Async iterator of job events from index ``since`` until the job ends."""
    index = since
    while True:
        while index < len(job.events):
            yield index, job.events[index]
            index += 1
        if job.status != "running":
            return
        job.changed.clear()
        try:
            await asyncio.wait_for(job.changed.wait(), 15)
        except asyncio.TimeoutError:
            yield index, {"stage": "ping"}


# ------------------------------------------------------------------ a loop for threaded servers (MemuNote)
_BG: Dict[str, Any] = {}
_BG_LOCK = threading.Lock()


def background_loop() -> asyncio.AbstractEventLoop:
    """One asyncio loop in a daemon thread, for callers without their own loop (MemuNote's http.server)."""
    with _BG_LOCK:
        loop = _BG.get("loop")
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="bigdocs-loop", daemon=True).start()
            _BG["loop"] = loop
        return loop


def run_sync(coro: Any, timeout: Optional[float] = None) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, background_loop()).result(timeout)


# ------------------------------------------------------------------ Quick Access
def quick_context(body: Dict[str, Any], question: str, lang: str = "ru") -> str:
    """Hook of ``/api/quick/ask``: ``contexts: [{type:"bigdoc", id, mode:"ask"|"whole", prompt}]`` → a context block."""
    rows = body.get("contexts") if isinstance(body, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("type") != "bigdoc" or not valid_id(row.get("id")):
            continue
        doc = load_doc(str(row["id"]))
        if doc is None:
            return ("<document unavailable=\"true\">The attached document is no longer cached; ask the user to drop it again."
                    "</document>")
        if row.get("mode") == "whole":
            cached = run_cache_get(doc["id"], str(body.get("model") or ""), "whole", str(row.get("prompt") or question))
            if cached:
                return (f'<document name="{_attr(doc.get("name", ""))}" pages="{len(doc["pages"])}" mode="whole-document-summary">\n'
                        + cached["final"] + "\n</document>\nThe user chose «Whole document»: this is a map-reduce reading of all "
                        "pages made for this request. Answer from it and keep its page references "
                        + ("[p. N]." if lang == "en" else "[стр. N]."))
        alt = _translate_from_thread(doc, question, str(body.get("model") or ""))
        ctx = qa_context(doc, question, k=settings(_prefs())["k"], lang=lang, extra=[alt] if alt else None)
        return ctx["block"] + "\n" + ctx["instruction"]
    return ""


_BRIDGE: Dict[str, Any] = {"complete": None, "loop": None}     # the playground's completion + its event loop


def _translate_from_thread(doc: Dict[str, Any], question: str, model: str, timeout: float = 30.0) -> str:
    """Cross-language query translation for sync callers running in a worker thread (Quick Access): the coroutine
    runs on the playground's own loop (its httpx client belongs to it)."""
    loop, complete = _BRIDGE.get("loop"), _BRIDGE.get("complete")
    if not model or complete is None or loop is None or loop.is_closed() or not needs_translation(doc, question):
        return ""
    try:
        if asyncio.get_running_loop() is loop:      # called on the loop itself: never block it
            return ""
    except RuntimeError:
        pass
    try:
        return asyncio.run_coroutine_threadsafe(translate_query(doc, question, model, complete), loop).result(timeout)
    except Exception:
        return ""


# ------------------------------------------------------------------ settings
def settings(prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prefs = prefs or {}
    try:
        confirm = float(prefs.get("bigdocs_confirm_usd", DEFAULT_CONFIRM_USD))
    except (TypeError, ValueError):
        confirm = DEFAULT_CONFIRM_USD
    try:
        part_tokens = int(prefs.get("bigdocs_part_tokens", DEFAULT_PART_TOKENS))
    except (TypeError, ValueError):
        part_tokens = DEFAULT_PART_TOKENS
    try:
        k = int(prefs.get("bigdocs_fragments", 8))
    except (TypeError, ValueError):
        k = 8
    return {"confirm_usd": max(0.0, confirm), "part_tokens": min(60000, max(3000, part_tokens)), "k": min(20, max(3, k)),
            "auto": str(prefs.get("bigdocs_auto", "auto"))}


def _prefs() -> Dict[str, Any]:
    try:
        from .pgdata import load_preferences
        return load_preferences() or {}
    except Exception:
        return {}



# FastAPI routes (MemuAI playground) live in app/bigdocs_api.py: MemuNote.exe imports this core without FastAPI.
