"""OpenDocument text (.odt) reader and writer — zipfile + ElementTree, no odfpy.

Reads headings, paragraphs, alignment, bullet/numbered lists (nested), text
formatting from automatic and common styles (bold, italic, underline, strike,
colour, background, size, font, super/subscript), links, spaces/tabs/line
breaks, tables (with column spans) and pictures. Writes a valid ODF 1.2
package with matching automatic styles.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from .sheets import safe_fromstring
from xml.sax.saxutils import escape

from .docx import MIME, _image_size
from .richhtml import Block, Cell, Para, Rule, Run, Table, css_size_to_pt, norm_color, norm_font, safe_link

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}
MAX_PART = 200 * 1024 * 1024

def n(prefix: str, tag: str) -> str:
    return "{%s}%s" % (NS[prefix], tag)

class OdtError(ValueError):
    pass

def _length_px(value: str) -> int:
    match = re.fullmatch(r"\s*([\d.]+)\s*(cm|mm|in|pt|px)?\s*", str(value or ""))
    if not match:
        return 0
    number = float(match.group(1))
    factor = {"cm": 96 / 2.54, "mm": 96 / 25.4, "in": 96, "pt": 96 / 72, "px": 1}.get(match.group(2) or "px", 1)
    return int(number * factor)

class _Reader:
    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise OdtError("not an .odt (zip) file") from exc
        self.styles: Dict[str, dict] = {}
        self.list_styles: Dict[str, Dict[int, str]] = {}
        for part in ("styles.xml", "content.xml"):
            raw = self._part(part)
            if raw:
                root = safe_fromstring(raw)
                self._collect_styles(root)
                if part == "content.xml":
                    self.content = root
        if not hasattr(self, "content"):
            raise OdtError("content.xml is missing")

    def _part(self, name: str) -> Optional[bytes]:
        try:
            info = self.zip.getinfo(name)
        except KeyError:
            return None
        if info.file_size > MAX_PART:
            raise OdtError(f"{name} is too large")
        return self.zip.read(info)

    def _collect_styles(self, root) -> None:
        for style in root.iter(n("style", "style")):
            name = style.get(n("style", "name"), "")
            info = {"parent": style.get(n("style", "parent-style-name"), ""),
                    "display": (style.get(n("style", "display-name")) or name).lower().replace("_20_", " "),
                    "list": style.get(n("style", "list-style-name"), ""),
                    "outline": style.get(n("style", "default-outline-level"), "")}
            text = style.find(n("style", "text-properties"))
            if text is not None:
                info["text"] = dict(text.attrib)
            para = style.find(n("style", "paragraph-properties"))
            if para is not None:
                info["para"] = dict(para.attrib)
            self.styles[name] = info
        for lst in root.iter(n("text", "list-style")):
            levels: Dict[int, str] = {}
            for child in lst:
                level = int(child.get(n("text", "level"), "1")) - 1
                levels[level] = "ol" if child.tag == n("text", "list-level-style-number") else "ul"
            self.list_styles[lst.get(n("style", "name"), "")] = levels

    def _chain(self, name: str) -> List[dict]:
        out, seen = [], set()
        while name and name not in seen and name in self.styles:
            seen.add(name)
            out.append(self.styles[name])
            name = self.styles[name]["parent"]
        return list(reversed(out))

    def _apply_text(self, run: Run, name: str) -> Run:
        run = Run(**{**run.__dict__, "text": ""})
        for info in self._chain(name):
            props = info.get("text") or {}
            for key, value in props.items():
                local = key.split("}")[-1]
                if local == "font-weight":
                    run.bold = value in ("bold", "600", "700", "800", "900")
                elif local == "font-style":
                    run.italic = value == "italic"
                elif local == "text-underline-style":
                    run.underline = value != "none"
                elif local == "text-line-through-style":
                    run.strike = value != "none"
                elif local == "color" and key.startswith("{" + NS["fo"]):
                    run.color = norm_color(value)
                elif local == "background-color":
                    run.highlight = norm_color(value)
                elif local == "font-size" and key.startswith("{" + NS["fo"]):
                    run.size = css_size_to_pt(value) if not value.endswith("%") else run.size
                elif local == "font-name" and key.startswith("{" + NS["style"]):
                    run.font = norm_font(value)
                elif local == "text-position":
                    run.sup = value.startswith("super") or (value[:1].isdigit() and not value.startswith("0"))
                    run.sub = value.startswith("sub") or value.startswith("-")
        return run

    def _para_kind(self, name: str) -> Tuple[str, str]:
        kind, align = "p", ""
        for info in self._chain(name):
            display = info["display"]
            match = re.match(r"heading\s*(\d)", display)
            if match:
                kind = "h" + str(min(6, int(match.group(1))))
            elif display in ("title",):
                kind = "h1"
            elif display in ("subtitle",):
                kind = "h2"
            elif "quotation" in display or display == "quote":
                kind = "blockquote"
            elif "preformatted" in display or display in ("source text", "code"):
                kind = "pre"
            props = info.get("para") or {}
            value = props.get(n("fo", "text-align"), "")
            if value:
                align = {"center": "center", "end": "right", "right": "right", "justify": "justify"}.get(value, "")
        return kind, align

    def blocks(self) -> List[Block]:
        body = self.content.find(f"{n('office', 'body')}/{n('office', 'text')}")
        out: List[Block] = []
        if body is not None:
            self._container(body, out, [], "")
        return out

    def _container(self, element, out: List[Block], lists: List[str], list_style: str) -> None:
        for child in element:
            tag = child.tag
            if tag in (n("text", "p"), n("text", "h")):
                self._paragraph(child, out, lists)
            elif tag == n("text", "list"):
                style = child.get(n("text", "style-name"), "") or list_style
                level = len(lists)
                kind = self.list_styles.get(style, {}).get(level, "ul")
                for item in child:
                    if item.tag in (n("text", "list-item"), n("text", "list-header")):
                        self._container(item, out, lists + [kind], style)
            elif tag == n("table", "table"):
                out.append(self._table(child))
            elif tag in (n("text", "section"), n("text", "soft-page-break")):
                self._container(child, out, lists, list_style)

    def _table(self, element) -> Table:
        table = Table()
        header_rows = set()
        for group in element.findall(n("table", "table-header-rows")):
            header_rows.update(group.findall(n("table", "table-row")))
        for row_el in element.iter(n("table", "table-row")):
            header = row_el in header_rows
            row: List[Cell] = []
            for cell_el in row_el:
                if cell_el.tag == n("table", "covered-table-cell"):
                    continue
                if cell_el.tag != n("table", "table-cell"):
                    continue
                try:
                    span = int(cell_el.get(n("table", "number-columns-spanned"), "1"))
                    repeat = min(64, int(cell_el.get(n("table", "number-columns-repeated"), "1")))
                except ValueError:
                    span, repeat = 1, 1
                blocks: List[Block] = []
                self._container(cell_el, blocks, [], "")
                for _ in range(repeat if not blocks else 1):
                    row.append(Cell(blocks=[b for b in blocks if isinstance(b, Para)], header=header,
                                    colspan=max(1, span)))

            while len(row) > 1 and not row[-1].blocks:
                row.pop()
            table.rows.append(row)
        return table

    def _paragraph(self, element, out: List[Block], lists: List[str]) -> None:
        style = element.get(n("text", "style-name"), "")
        kind, align = self._para_kind(style)
        if element.tag == n("text", "h"):
            try:
                level = int(element.get(n("text", "outline-level"), "1"))
            except ValueError:
                level = 1
            kind = "h" + str(min(6, max(1, level)))
        para = Para(kind=kind, align=align)
        if lists:
            para.kind, para.list, para.level = "li", lists[-1], len(lists) - 1

        base = self._apply_text(Run(), style) if kind == "p" else Run()
        self._inline(element, para, base)
        out.append(para)

    def _add(self, para: Para, run: Run) -> None:
        if not run.text:
            return
        if para.runs and not para.runs[-1].br and not para.runs[-1].image and para.runs[-1].same_format(run):
            para.runs[-1].text += run.text
        else:
            para.runs.append(run)

    def _inline(self, element, para: Para, base: Run) -> None:
        if element.text:
            self._add(para, Run(**{**base.__dict__, "text": element.text}))
        for child in element:
            tag = child.tag
            if tag == n("text", "span"):
                self._inline(child, para, self._apply_text(base, child.get(n("text", "style-name"), "")))
            elif tag == n("text", "a"):
                link = safe_link(child.get(n("xlink", "href"), ""))
                self._inline(child, para, Run(**{**base.__dict__, "link": link or base.link}))
            elif tag == n("text", "s"):
                try:
                    count = int(child.get(n("text", "c"), "1"))
                except ValueError:
                    count = 1
                self._add(para, Run(**{**base.__dict__, "text": " " * max(1, min(count, 1000))}))
            elif tag == n("text", "tab"):
                self._add(para, Run(**{**base.__dict__, "text": "\t"}))
            elif tag == n("text", "line-break"):
                para.runs.append(Run(br=True))
            elif tag == n("draw", "frame"):
                image = child.find(n("draw", "image"))
                if image is not None:
                    run = self._image(image.get(n("xlink", "href"), ""), child)
                    if run:
                        para.runs.append(run)
            elif tag in (n("text", "note"), n("office", "annotation"), n("text", "bookmark-ref")):
                pass
            else:
                self._inline(child, para, base)
            if child.tail:
                self._add(para, Run(**{**base.__dict__, "text": child.tail}))

    def _image(self, href: str, frame) -> Optional[Run]:
        if not href or href.startswith(("http:", "https:", "/", "..")):
            return None
        ext = href.rsplit(".", 1)[-1].lower()
        mime = MIME.get(ext, "")
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/svg+xml"):
            return None
        data = self._part(href)
        if not data or len(data) > 25 * 1024 * 1024:
            return None
        return Run(image=f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
                   width=_length_px(frame.get(n("svg", "width"), "")),
                   height=_length_px(frame.get(n("svg", "height"), "")))

def read_odt(data: bytes) -> Tuple[List[Block], dict]:
    from .richhtml import tidy
    reader = _Reader(data)
    return tidy(reader.blocks()), {}

def _t(value: str) -> str:
    return escape(re.sub("[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value))

def _a(value: str) -> str:
    return escape(str(value), {'"': "&quot;"})

class _Writer:
    def __init__(self) -> None:
        self.text_styles: Dict[tuple, str] = {}
        self.para_styles: Dict[tuple, str] = {}
        self.images: List[Tuple[str, bytes, str]] = []
        self.list_styles: Dict[tuple, str] = {}
        self.frame = 0

    def text_style(self, run: Run) -> str:
        key = (run.bold, run.italic, run.underline, run.strike, run.code, run.sup, run.sub, run.color,
               run.highlight, run.size, run.font)
        if not any(key):
            return ""
        if key not in self.text_styles:
            self.text_styles[key] = f"T{len(self.text_styles) + 1}"
        return self.text_styles[key]

    def para_style(self, parent: str, align: str) -> str:
        if not align:
            return parent
        key = (parent, align)
        if key not in self.para_styles:
            self.para_styles[key] = f"P{len(self.para_styles) + 1}"
        return self.para_styles[key]

    def run_xml(self, run: Run) -> str:
        if run.br:
            return "<text:line-break/>"
        if run.image:
            return self.image_xml(run)
        text = ""
        for index, part in enumerate(run.text.split("\t")):
            if index:
                text += "<text:tab/>"

            text += re.sub(r"  +", lambda m: " " + f'<text:s text:c="{len(m.group(0)) - 1}"/>', _t(part))
        style = self.text_style(run)
        if style:
            text = f'<text:span text:style-name="{style}">{text}</text:span>'
        if run.link:
            text = f'<text:a xlink:type="simple" xlink:href="{_a(run.link)}">{text}</text:a>'
        return text

    def image_xml(self, run: Run) -> str:
        match = re.match(r"data:image/(png|jpe?g|gif|bmp|webp|svg\+xml);base64,(.*)$", run.image, re.S)
        if not match:
            return ""
        try:
            data = base64.b64decode(match.group(2))
        except (ValueError, TypeError):
            return ""
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(match.group(1), match.group(1))
        name = f"Pictures/image{len(self.images) + 1}.{ext}"
        self.images.append((name, data, MIME.get(ext, "image/png")))
        width, height = run.width, run.height
        if not (width and height):
            size = _image_size(data) or (320, 240)
            width, height = width or size[0], height or size[1]
        if width > 624:
            height, width = int(height * 624 / width), 624
        self.frame += 1
        return (f'<draw:frame draw:name="Image{self.frame}" text:anchor-type="as-char" '
                f'svg:width="{width / 96:.3f}in" svg:height="{height / 96:.3f}in" draw:z-index="0">'
                f'<draw:image xlink:href="{name}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>'
                "</draw:frame>")

    def inline(self, para: Para) -> str:
        runs = list(para.runs)
        if para.checked is not None:
            runs.insert(0, Run(text=("\u2611 " if para.checked else "\u2610 ")))
        return "".join(self.run_xml(r) for r in runs)

    def para_xml(self, para: Para) -> str:
        if para.kind.startswith("h") and len(para.kind) == 2:
            level = int(para.kind[1])
            style = self.para_style(f"Heading_20_{level}", para.align)
            return f'<text:h text:style-name="{style}" text:outline-level="{level}">{self.inline(para)}</text:h>'
        parent = {"blockquote": "Quotations", "pre": "Preformatted_20_Text"}.get(para.kind, "Standard")
        style = self.para_style(parent, para.align)
        return f'<text:p text:style-name="{style}">{self.inline(para)}</text:p>'

    def body(self, blocks: List[Block]) -> str:
        out: List[str] = []
        index = 0
        while index < len(blocks):
            block = blocks[index]
            if isinstance(block, Para) and block.kind == "li":
                group = []
                while index < len(blocks) and isinstance(blocks[index], Para) and blocks[index].kind == "li":
                    item = blocks[index]

                    if group and item.level == 0 and (item.list == "ol") != (group[0].list == "ol"):
                        break
                    group.append(item)
                    index += 1
                out.append(self.list_xml(group))
                continue
            if isinstance(block, Rule):
                out.append('<text:p text:style-name="MN_PageBreak"/>' if block.page_break
                           else '<text:p text:style-name="Horizontal_20_Line"/>')
            elif isinstance(block, Table):
                out.append(self.table_xml(block))
            elif block.kind == "pre":
                for line in block.text.split("\n"):
                    out.append(self.para_xml(Para(kind="pre", runs=[Run(text=line)] if line else [])))
            else:
                out.append(self.para_xml(block))
            index += 1
        return "".join(out)

    def list_xml(self, items: List[Para]) -> str:
        """Flat list items (with levels) -> nested ``text:list`` elements."""
        kinds: Dict[int, str] = {}
        for item in items:
            kinds.setdefault(min(8, item.level), "ol" if item.list == "ol" else "ul")
        signature = tuple(kinds.get(level, "ul") for level in range(10))
        if signature not in self.list_styles:
            self.list_styles[signature] = f"MN_L{len(self.list_styles) + 1}"
        top = self.list_styles[signature]

        def render(start: int, level: int) -> Tuple[str, int]:
            style = f' text:style-name="{top}"' if level == 0 else ""
            out = [f"<text:list{style}>"]
            index = start
            while index < len(items) and min(8, items[index].level) >= level:
                if min(8, items[index].level) == level:
                    item = items[index]
                    inner = self.para_xml(Para(kind="p", runs=item.runs, align=item.align, checked=item.checked))
                    index += 1
                    if index < len(items) and min(8, items[index].level) > level:
                        child, index = render(index, level + 1)
                        inner += child
                else:
                    inner, index = render(index, level + 1)
                out.append(f"<text:list-item>{inner}</text:list-item>")
            out.append("</text:list>")
            return "".join(out), index

        return render(0, 0)[0]

    def table_xml(self, table: Table) -> str:
        width = max((sum(c.colspan for c in row) for row in table.rows), default=1)
        rows = []
        for row_index, row in enumerate(table.rows):
            cells = []
            used = 0
            for cell in row:
                span = f' table:number-columns-spanned="{cell.colspan}"' if cell.colspan > 1 else ""
                paras = []
                for p in cell.blocks:
                    if isinstance(p, Para):
                        if cell.header:
                            p = Para(kind=p.kind, align=p.align,
                                     runs=[Run(**{**r.__dict__, "bold": True}) if not r.br and not r.image else r
                                           for r in p.runs])
                        paras.append(self.para_xml(p))
                inner = "".join(paras) or '<text:p text:style-name="Standard"/>'
                cells.append(f'<table:table-cell table:style-name="MN_Cell" office:value-type="string"{span}>{inner}</table:table-cell>')
                cells.extend("<table:covered-table-cell/>" for _ in range(cell.colspan - 1))
                used += cell.colspan
            cells.extend('<table:table-cell table:style-name="MN_Cell" office:value-type="string"><text:p/></table:table-cell>'
                         for _ in range(width - used))
            xml = "<table:table-row>" + "".join(cells) + "</table:table-row>"
            if row_index == 0 and row and all(c.header for c in row):
                xml = f"<table:table-header-rows>{xml}</table:table-header-rows>"
            rows.append(xml)
        self.frame += 1
        return (f'<table:table table:name="Table{self.frame}" table:style-name="MN_Table">'
                f'<table:table-column table:number-columns-repeated="{width}"/>' + "".join(rows) + "</table:table>")

    def automatic_styles(self) -> str:
        out = []
        for (parent, align), name in self.para_styles.items():
            fo = {"right": "end", "left": "start"}.get(align, align)
            out.append(f'<style:style style:name="{name}" style:family="paragraph" style:parent-style-name="{parent}">'
                       f'<style:paragraph-properties fo:text-align="{fo}"/></style:style>')
        for key, name in self.text_styles.items():
            bold, italic, underline, strike, code, sup, sub, color, highlight, size, font = key
            props = []
            if bold:
                props.append('fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"')
            if italic:
                props.append('fo:font-style="italic" style:font-style-asian="italic" style:font-style-complex="italic"')
            if underline:
                props.append('style:text-underline-style="solid" style:text-underline-width="auto" style:text-underline-color="font-color"')
            if strike:
                props.append('style:text-line-through-style="solid"')
            if code:
                props.append('style:font-name="Consolas" fo:font-family="Consolas"')
            elif font:
                props.append(f'style:font-name="{_a(font)}" fo:font-family="{_a(font)}"')
            if sup:
                props.append('style:text-position="super 58%"')
            elif sub:
                props.append('style:text-position="sub 58%"')
            if color:
                props.append(f'fo:color="{color}"')
            if highlight:
                props.append(f'fo:background-color="{highlight}"')
            if size:
                props.append(f'fo:font-size="{size:g}pt"')
            out.append(f'<style:style style:name="{name}" style:family="text"><style:text-properties {" ".join(props)}/></style:style>')
        out.append('<style:style style:name="MN_Table" style:family="table"><style:table-properties style:width="17cm" table:align="margins"/></style:style>')
        out.append('<style:style style:name="MN_Cell" style:family="table-cell"><style:table-cell-properties fo:padding="0.1cm" fo:border="0.5pt solid #bfbfbf"/></style:style>')
        out.append('<style:style style:name="MN_PageBreak" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:break-after="page"/></style:style>')
        for signature, name in self.list_styles.items():
            levels = []
            for level in range(1, 11):
                kind = signature[level - 1]
                indent = f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="{0.635 * level:.3f}cm" fo:text-indent="-0.635cm" fo:margin-left="{0.635 * level:.3f}cm"/></style:list-level-properties>'
                if kind == "ul":
                    bullet = "\u2022\u25e6\u25aa"[(level - 1) % 3]
                    levels.append(f'<text:list-level-style-bullet text:level="{level}" text:bullet-char="{bullet}">{indent}</text:list-level-style-bullet>')
                else:
                    fmt = "1ai"[(level - 1) % 3]
                    levels.append(f'<text:list-level-style-number text:level="{level}" style:num-suffix="." style:num-format="{fmt}">{indent}</text:list-level-style-number>')
            out.append(f'<text:list-style style:name="{name}">{"".join(levels)}</text:list-style>')
        return "".join(out)

_XMLNS = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k not in ("manifest", "loext"))

_HEADINGS = "".join(
    f'<style:style style:name="Heading_20_{i}" style:display-name="Heading {i}" style:family="paragraph" '
    f'style:parent-style-name="Heading" style:default-outline-level="{i}" style:class="text">'
    f'<style:text-properties fo:font-size="{size}pt" fo:font-weight="bold"/></style:style>'
    for i, size in ((1, 16), (2, 14), (3, 13), (4, 12), (5, 11), (6, 11)))

_STYLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {_XMLNS} office:version="1.2"><office:font-face-decls/><office:styles>
<style:default-style style:family="paragraph"><style:paragraph-properties fo:margin-bottom="0.2cm" fo:line-height="115%"/><style:text-properties style:font-name="Calibri" fo:font-family="Calibri" fo:font-size="11pt" fo:language="ru" fo:country="RU"/></style:default-style>
<style:style style:name="Standard" style:family="paragraph" style:class="text"/>
<style:style style:name="Heading" style:family="paragraph" style:parent-style-name="Standard" style:class="text"><style:paragraph-properties fo:margin-top="0.42cm" fo:margin-bottom="0.21cm" fo:keep-with-next="always"/><style:text-properties fo:font-weight="bold"/></style:style>
{_HEADINGS}
<style:style style:name="Quotations" style:family="paragraph" style:parent-style-name="Standard" style:class="html"><style:paragraph-properties fo:margin-left="1cm" fo:padding-left="0.3cm" fo:border-left="1.5pt solid #a0a0a0"/><style:text-properties fo:font-style="italic" fo:color="#555555"/></style:style>
<style:style style:name="Preformatted_20_Text" style:display-name="Preformatted Text" style:family="paragraph" style:parent-style-name="Standard" style:class="html"><style:paragraph-properties fo:margin-bottom="0cm" fo:background-color="#f3f3f3"/><style:text-properties style:font-name="Consolas" fo:font-family="Consolas" fo:font-size="10pt"/></style:style>
<style:style style:name="Horizontal_20_Line" style:display-name="Horizontal Line" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:border-bottom="0.5pt solid #bfbfbf" fo:padding="0cm"/></style:style>
</office:styles><office:automatic-styles><style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm" fo:margin-top="2cm" fo:margin-bottom="2cm" fo:margin-left="3cm" fo:margin-right="1.5cm"/></style:page-layout></office:automatic-styles>
<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="pm1"/></office:master-styles></office:document-styles>"""

def write_odt(blocks: List[Block], title: str = "") -> bytes:
    writer = _Writer()
    body = writer.body(blocks)
    content = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {_XMLNS} office:version="1.2">'
               f"<office:automatic-styles>{writer.automatic_styles()}</office:automatic-styles>"
               f"<office:body><office:text>{body}</office:text></office:body></office:document-content>")
    meta = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-meta {_XMLNS} office:version="1.2"><office:meta>'
            f"<meta:generator>MemuNote</meta:generator><dc:title>{_t(title)}</dc:title></office:meta></office:document-meta>")
    manifest = ['<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest xmlns:manifest="'
                + NS["manifest"] + '" manifest:version="1.2">',
                '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="application/vnd.oasis.opendocument.text"/>']
    for part in ("content.xml", "styles.xml", "meta.xml"):
        manifest.append(f'<manifest:file-entry manifest:full-path="{part}" manifest:media-type="text/xml"/>')
    for name, _data, mime in writer.images:
        manifest.append(f'<manifest:file-entry manifest:full-path="{name}" manifest:media-type="{mime}"/>')
    manifest.append("</manifest:manifest>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text",
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr("content.xml", content)
        archive.writestr("styles.xml", _STYLES)
        archive.writestr("meta.xml", meta)
        archive.writestr("META-INF/manifest.xml", "".join(manifest))
        for name, data, _mime in writer.images:
            archive.writestr(name, data, compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()
