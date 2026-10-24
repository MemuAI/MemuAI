"""E-books for MemuNote (Round 31): ``.epub`` and ``.fb2`` → the safe HTML of the Document view (read-only).

EPUB: ``META-INF/container.xml`` → OPF → spine order; every chapter's XHTML body goes through
:func:`richhtml.parse_html` / :func:`richhtml.to_html` (the same whitelist as DOCX: no scripts, no
styles, links only http(s)/mailto), pictures are inlined as ``data:`` URIs (capped). FB2: the XML
``<body>``/``<section>``/``<title>``/``<p>``/``<emphasis>``/``<strong>``/``<image>`` (binaries inline).
Returns ``{"html", "title", "author", "chapters": [{"title", "id"}]}``.
"""
from __future__ import annotations

import base64
import html as _html
import io
import posixpath
import re
import zipfile
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from . import richhtml
from .sheets import safe_fromstring

MAX_IMAGE = 3 * 1024 * 1024        # bytes per picture
MAX_IMAGES_TOTAL = 40 * 1024 * 1024
MAX_HTML = 60 * 1024 * 1024
_IMG_TYPES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp", ".bmp": "bmp", ".svg": "svg+xml"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_epub(data: bytes) -> Dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        container = safe_fromstring(z.read("META-INF/container.xml"))
        opf_path = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), "")
        if not opf_path or opf_path not in names:
            raise ValueError("EPUB without a package document")
        opf = safe_fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest, spine, title, author = {}, [], "", ""
        for el in opf.iter():
            tag = _local(el.tag)
            if tag == "item":
                manifest[el.get("id")] = (posixpath.normpath(posixpath.join(base, el.get("href", ""))), el.get("media-type", ""))
            elif tag == "itemref":
                spine.append(el.get("idref"))
            elif tag == "title" and not title:
                title = (el.text or "").strip()
            elif tag == "creator" and not author:
                author = (el.text or "").strip()
        used = [0]

        def image_uri(chapter: str, src: str) -> str:
            src = src.split("#")[0]
            path = posixpath.normpath(posixpath.join(posixpath.dirname(chapter), src))
            kind = _IMG_TYPES.get(posixpath.splitext(path)[1].lower())
            if not kind or path not in names:
                return ""
            raw = z.read(path)
            if len(raw) > MAX_IMAGE or used[0] + len(raw) > MAX_IMAGES_TOTAL:
                return ""
            used[0] += len(raw)
            return f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")

        parts: List[str] = []
        chapters = []
        size = 0
        for n, idref in enumerate(spine):
            path, mtype = manifest.get(idref, ("", ""))
            if not path or path not in names or "html" not in (mtype or "xhtml"):
                continue
            text = z.read(path).decode("utf-8", errors="replace")
            body = re.search(r"<body[^>]*>(.*)</body>", text, re.S | re.I)
            body_html = body.group(1) if body else text
            body_html = re.sub(r"<(script|style)\b.*?</\1\s*>", "", body_html, flags=re.S | re.I)
            body_html = re.sub(r"<image\b[^>]*?(?:xlink:)?href=\"([^\"]+)\"[^>]*/?>", r'<img src="\1">', body_html, flags=re.I)
            body_html = re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")',
                               lambda m: m.group(1) + (image_uri(path, _html.unescape(m.group(2))) if not m.group(2).startswith("data:") else m.group(2)) + m.group(3),
                               body_html, flags=re.I)
            safe = richhtml.to_html(richhtml.parse_html(body_html))
            if not safe.strip():
                continue
            head = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", safe, re.S)
            chapter_title = re.sub(r"<[^>]+>", "", head.group(1)).strip() if head else ""
            anchor = f"mn-ch-{n}"
            chapters.append({"title": _html.unescape(chapter_title) or f"#{len(chapters) + 1}", "id": anchor})
            parts.append(f'<hr class="mn-chapter" id="{anchor}">' if parts else f'<span id="{anchor}"></span>')
            parts.append(safe)
            size += len(safe)
            if size > MAX_HTML:
                break
    return {"html": "\n".join(parts), "title": title, "author": author, "chapters": chapters}


def read_fb2(data: bytes) -> Dict[str, Any]:
    raw = data
    if raw[:2] == b"PK":               # .fb2.zip
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            inner = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
            if not inner:
                raise ValueError("no .fb2 inside the archive")
            raw = z.read(inner)
    root = safe_fromstring(raw)
    binaries = {}
    for el in root.iter():
        if _local(el.tag) == "binary" and el.get("id") and el.text:
            ctype = el.get("content-type", "image/jpeg")
            if ctype in ("image/jpeg", "image/png", "image/gif", "image/webp") and len(el.text) < MAX_IMAGE * 4 // 3:
                binaries[el.get("id")] = f"data:{ctype};base64," + re.sub(r"\s+", "", el.text)
    title, author = "", ""
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "book-title" and not title:
            title = (el.text or "").strip()
        if tag == "author" and not author:
            author = " ".join(filter(None, ((c.text or "").strip() for c in el if _local(c.tag) in ("first-name", "last-name"))))
    chapters: List[Dict[str, str]] = []

    def inline(el) -> str:
        out = [_html.escape(el.text or "")]
        for child in el:
            tag = _local(child.tag)
            inner = inline(child)
            if tag == "strong":
                out.append(f"<b>{inner}</b>")
            elif tag == "emphasis":
                out.append(f"<i>{inner}</i>")
            elif tag == "strikethrough":
                out.append(f"<s>{inner}</s>")
            elif tag in ("sup", "sub", "code"):
                out.append(f"<{tag}>{inner}</{tag}>")
            elif tag == "a":
                href = next((v for k, v in child.attrib.items() if k.endswith("href")), "")
                out.append(f'<a href="{_html.escape(href)}">{inner}</a>' if href.startswith(("http://", "https://")) else inner)
            else:
                out.append(inner)
            out.append(_html.escape(child.tail or ""))
        return "".join(out)

    def block(el, depth: int) -> str:
        tag = _local(el.tag)
        if tag == "section":
            return "".join(block(c, depth + 1) for c in el)
        if tag == "title":
            text = " ".join(inline(p) for p in el if _local(p.tag) == "p") or inline(el)
            level = min(4, max(1, depth))
            anchor = f"mn-ch-{len(chapters)}"
            if level <= 2:
                chapters.append({"title": re.sub(r"<[^>]+>", "", _html.unescape(text)).strip() or f"#{len(chapters) + 1}", "id": anchor})
            return f'<h{level} id="{anchor}">{text}</h{level}>'
        if tag in ("p", "v", "text-author"):
            return f"<p>{inline(el)}</p>"
        if tag == "subtitle":
            return f"<h4>{inline(el)}</h4>"
        if tag == "empty-line":
            return "<p><br></p>"
        if tag in ("epigraph", "cite", "poem", "stanza"):
            inner = "".join(block(c, depth) for c in el)
            return f"<blockquote>{inner}</blockquote>" if tag in ("epigraph", "cite") else inner
        if tag == "image":
            ref = next((v for k, v in el.attrib.items() if k.endswith("href")), "").lstrip("#")
            return f'<p><img src="{binaries[ref]}"></p>' if ref in binaries else ""
        return ""

    bodies = [el for el in root if _local(el.tag) == "body"]
    html_parts = []
    for body in bodies[:3]:
        html_parts.append("".join(block(c, 0) for c in body))
    safe = richhtml.to_html(richhtml.parse_html("".join(html_parts)))
    # richhtml drops ids: put the chapter anchors back on the headings in order
    k = iter(chapters)

    def add_id(m):
        nxt = next(k, None)
        return f'<{m.group(1)} id="{nxt["id"]}"' if nxt else m.group(0)
    safe = re.sub(r"<(h[12])(?=[\s>])", add_id, safe)
    return {"html": safe, "title": title, "author": author, "chapters": chapters}


def read_book(path: str, data: bytes = None) -> Dict[str, Any]:
    data = data if data is not None else open(path, "rb").read()
    low = path.lower()
    if low.endswith(".epub"):
        return read_epub(data)
    return read_fb2(data)
