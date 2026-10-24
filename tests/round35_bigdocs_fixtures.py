"""Synthetic big documents with known facts on known pages (Round 35, bigdocs tests and live checks).

* :func:`make_pdf` — a ~600-page PDF (pure Python, Helvetica, English) with an outline of 20 chapters;
* :func:`make_docx` — a ~200-page Russian contract DOCX with real page breaks and «Заголовок 1» headings;
* :func:`make_docx_flow` — the same text WITHOUT page breaks (approximate pages);
* :func:`make_epub` — a small EPUB with 5 chapters.
"""
from __future__ import annotations

import io
import random
import zipfile
from typing import Dict, List, Tuple

PDF_FACTS: Dict[int, str] = {
    137: "The vault access code is 4471-KESTREL and only the night warden knows it.",
    412: "Captain Morrow named her ship the Silver Albatross after the storm of 1893.",
    589: "The penalty for late delivery is 3.5 percent of the price per week of delay.",
    23: "The lighthouse keeper Elias Brandt kept exactly forty-two cats.",
}
DOCX_FACTS: Dict[int, str] = {
    57: "Срок поставки оборудования составляет 45 календарных дней с даты подписания спецификации.",
    133: "За просрочку оплаты Покупатель уплачивает неустойку в размере 0,7% от суммы долга за каждый день.",
    188: "Все споры передаются на рассмотрение в Арбитражный суд города Казани.",
    9: "Гарантийный срок на оборудование составляет 36 месяцев.",
}
_EN_WORDS = ("river garden market stone window letter morning village silver road harbor merchant evening winter "
             "candle orchard bridge lantern meadow quiet north tower bell ledger coast hill mill scholar copper "
             "basket thread wagon chapel field forest cloud promise journey").split()
_RU_WORDS = ("сторона договор поставка товар оплата счёт акт приёмка качество упаковка маркировка документ "
             "уведомление представитель обязательство срок порядок условие стоимость партия склад перевозчик "
             "претензия гарантия ответственность расчёт реквизиты спецификация приложение").split()


def _filler(rng: random.Random, words: List[str], n_sent: int) -> List[str]:
    out = []
    for _ in range(n_sent):
        k = rng.randint(8, 16)
        s = " ".join(rng.choice(words) for _ in range(k))
        out.append(s[0].upper() + s[1:] + ".")
    return out


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: int = 600, chapters: int = 20, seed: int = 7) -> bytes:
    """A real multi-page PDF: each page a few text lines; chapter titles start pages 1, 31, 61… with an outline."""
    rng = random.Random(seed)
    per = pages // chapters
    objects: List[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: List[int] = []
    pages_obj_index = add(b"")   # placeholder: /Pages
    for p in range(1, pages + 1):
        lines: List[str] = []
        if (p - 1) % per == 0:
            lines.append(f"Chapter {(p - 1) // per + 1}: The {rng.choice(_EN_WORDS).title()} {rng.choice(_EN_WORDS).title()}")
        sents = _filler(rng, _EN_WORDS, 9)
        if p in PDF_FACTS:
            sents.insert(4, PDF_FACTS[p])
        lines.extend(sents)
        stream = ["BT", "/F1 10 Tf", "13 TL", "50 790 Td"]
        for line in lines:
            # wrap at ~95 chars
            while line:
                piece, line = line[:95], line[95:]
                if line and " " in piece:
                    cut = piece.rfind(" ")
                    line = piece[cut + 1:] + line
                    piece = piece[:cut]
                stream.append(f"({_pdf_escape(piece)}) Tj T*")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R >> >> "
                            b"/Contents %d 0 R >>" % (pages_obj_index, font, content)))
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects[pages_obj_index - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    # outline
    outlines_index = add(b"")
    item_ids = []
    first_item = len(objects) + 1
    for c in range(chapters):
        item_ids.append(first_item + c)
    for c in range(chapters):
        prev = b" /Prev %d 0 R" % item_ids[c - 1] if c > 0 else b""
        nxt = b" /Next %d 0 R" % item_ids[c + 1] if c + 1 < chapters else b""
        title = f"Chapter {c + 1}".encode("latin-1")
        add(b"<< /Title (%s) /Parent %d 0 R /Dest [%d 0 R /Fit]%s%s >>" % (title, outlines_index, page_ids[c * per], prev, nxt))
    objects[outlines_index - 1] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (
        item_ids[0], item_ids[-1], chapters)
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R /Outlines %d 0 R /PageMode /UseOutlines >>" % (pages_obj_index, outlines_index))
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref))
    return out.getvalue()


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="a"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="2"><w:name w:val="heading 2"/></w:style>
</w:styles>"""
_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _docx(paragraphs: List[Tuple[str, str]]) -> bytes:
    """paragraphs: (kind, text) — kind ∈ {"p", "h1", "h2", "break"}."""
    body = []
    for kind, text in paragraphs:
        if kind == "break":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif kind in ("h1", "h2"):
            body.append(f'<w:p><w:pPr><w:pStyle w:val="{kind[1]}"/></w:pPr><w:r><w:t>{_xml(text)}</w:t></w:r></w:p>')
        else:
            body.append(f'<w:p><w:r><w:t xml:space="preserve">{_xml(text)}</w:t></w:r></w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(body) + "<w:sectPr/></w:body></w:document>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
    return out.getvalue()


def _contract_pages(pages: int, seed: int) -> List[List[Tuple[str, str]]]:
    rng = random.Random(seed)
    sections = 10
    per = pages // sections
    out = []
    for p in range(1, pages + 1):
        paras: List[Tuple[str, str]] = []
        if (p - 1) % per == 0:
            paras.append(("h1", f"Раздел {(p - 1) // per + 1}. {rng.choice(_RU_WORDS).capitalize()} и {rng.choice(_RU_WORDS)}"))
        sents = _filler(rng, _RU_WORDS, 12)
        if p in DOCX_FACTS:
            sents.insert(5, DOCX_FACTS[p])
        for i in range(0, len(sents), 3):
            paras.append(("p", f"{p}.{i // 3 + 1}. " + " ".join(sents[i:i + 3])))
        out.append(paras)
    return out


def make_docx(pages: int = 200, seed: int = 11) -> bytes:
    rows: List[Tuple[str, str]] = []
    for i, paras in enumerate(_contract_pages(pages, seed)):
        if i:
            rows.append(("break", ""))
        rows.extend(paras)
    return _docx(rows)


def make_docx_flow(pages: int = 60, seed: int = 11) -> bytes:
    rows: List[Tuple[str, str]] = []
    for paras in _contract_pages(pages, seed):
        rows.extend(paras)
    return _docx(rows)


def make_epub(chapters: int = 5, seed: int = 3) -> bytes:
    rng = random.Random(seed)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        items, refs = [], []
        for c in range(1, chapters + 1):
            paras = "".join(f"<p>{s}</p>" for s in _filler(rng, _EN_WORDS, 40))
            if c == 4:
                paras += "<p>The hidden treasure lies beneath the old copper mill.</p>"
            z.writestr(f"OEBPS/ch{c}.xhtml", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>c{c}</title></head>'
                       f"<body><h1>Chapter {c}</h1>{paras}</body></html>")
            items.append(f'<item id="c{c}" href="ch{c}.xhtml" media-type="application/xhtml+xml"/>')
            refs.append(f'<itemref idref="c{c}"/>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Book</dc:title></metadata>'
                   f'<manifest>{"".join(items)}</manifest><spine>{"".join(refs)}</spine></package>')
    return out.getvalue()
