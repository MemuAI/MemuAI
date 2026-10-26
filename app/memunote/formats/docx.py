"""Word documents (.docx) without python-docx: zipfile + ElementTree.

Reading keeps what an editor can show and edit: paragraphs with heading /
quote / title styles, alignment, bullet and numbered lists (``numbering.xml``),
run formatting (bold, italic, underline, strike, colour, highlight/shading,
size, font, super/subscript), hyperlinks, inline pictures, tables (with
``gridSpan``), line and page breaks. Headers, footers, comments, footnotes,
text boxes, fields and tracked deletions are not shown.

Writing produces a clean, valid WordprocessingML package (styles, numbering,
pictures, hyperlinks, core properties). When the original file is given, its
page size and margins, headers/footers (with their pictures) and authorship are kept. The UI warns that features MemuNote cannot
edit are simplified on save and offers "Save as copy".
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import re
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from .sheets import safe_fromstring
from xml.sax.saxutils import escape

from .richhtml import Block, Cell, Para, Rule, Run, Table, norm_color, safe_link

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
V = "urn:schemas-microsoft-com:vml"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

MAX_PART = 200 * 1024 * 1024

def q(ns: str, tag: str) -> str:
    return "{%s}%s" % (ns, tag)

HIGHLIGHT = {"yellow": "#ffff00", "green": "#00ff00", "cyan": "#00ffff", "magenta": "#ff00ff", "blue": "#0000ff",
             "red": "#ff0000", "darkBlue": "#000080", "darkCyan": "#008080", "darkGreen": "#008000",
             "darkMagenta": "#800080", "darkRed": "#800000", "darkYellow": "#808000", "darkGray": "#808080",
             "lightGray": "#c0c0c0", "black": "#000000", "white": "#ffffff"}

MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
        "webp": "image/webp", "svg": "image/svg+xml", "tif": "image/tiff", "tiff": "image/tiff",
        "emf": "image/emf", "wmf": "image/wmf"}

class DocxError(ValueError):
    pass

def _read_part(archive: zipfile.ZipFile, name: str) -> Optional[bytes]:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART:
        raise DocxError(f"{name} is too large")
    return archive.read(info)

def _rels(archive: zipfile.ZipFile, part: str) -> Dict[str, Tuple[str, str]]:
    folder, _, base = part.rpartition("/")
    data = _read_part(archive, f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels")
    out: Dict[str, Tuple[str, str]] = {}
    if not data:
        return out
    for rel in safe_fromstring(data):
        out[rel.get("Id", "")] = (rel.get("Target", ""), rel.get("TargetMode", ""))
    return out

def _resolve(folder: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (folder + "/" + target).split("/") if folder else target.split("/")
    out: List[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        elif part and part != ".":
            out.append(part)
    return "/".join(out)

def _main_part(archive: zipfile.ZipFile) -> str:
    data = _read_part(archive, "_rels/.rels")
    if data:
        for rel in safe_fromstring(data):
            if rel.get("Type", "").endswith("/officeDocument"):
                return _resolve("", rel.get("Target", ""))
    return "word/document.xml"

class _Reader:
    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise DocxError("not a .docx (zip) file") from exc
        self.main = _main_part(self.zip)
        raw = _read_part(self.zip, self.main)
        if raw is None:
            raise DocxError("word/document.xml is missing")
        self.doc = safe_fromstring(raw)
        self.folder = self.main.rpartition("/")[0]
        self.rels = _rels(self.zip, self.main)
        self.styles = self._styles()
        self.numbering = self._numbering()
        self.images = 0

    def _styles(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        target = next((t for t, _m in self.rels.values() if t.endswith("styles.xml")), "styles.xml")
        data = _read_part(self.zip, _resolve(self.folder, target))
        if not data:
            return out
        root = safe_fromstring(data)
        for style in root.findall(q(W, "style")):
            sid = style.get(q(W, "styleId"), "")
            name_el = style.find(q(W, "name"))
            name = (name_el.get(q(W, "val"), "") if name_el is not None else sid).lower()
            based = style.find(q(W, "basedOn"))
            outline = style.find(f"{q(W, 'pPr')}/{q(W, 'outlineLvl')}")
            num_el = style.find(f"{q(W, 'pPr')}/{q(W, 'numPr')}")
            num = None
            if num_el is not None:
                nid_el, lvl_el = num_el.find(q(W, "numId")), num_el.find(q(W, "ilvl"))
                try:
                    num = (nid_el.get(q(W, "val"), "") if nid_el is not None else "",
                           int(lvl_el.get(q(W, "val"), "0")) if lvl_el is not None else 0)
                except ValueError:
                    num = None
            out[sid] = {"name": name, "based": based.get(q(W, "val"), "") if based is not None else "",
                        "outline": int(outline.get(q(W, "val"), "9")) if outline is not None else None,
                        "num": num}
        return out

    def _numbering(self) -> Dict[Tuple[str, int], str]:
        out: Dict[Tuple[str, int], str] = {}
        target = next((t for t, _m in self.rels.values() if t.endswith("numbering.xml")), "numbering.xml")
        data = _read_part(self.zip, _resolve(self.folder, target))
        if not data:
            return out
        root = safe_fromstring(data)
        abstract: Dict[str, Dict[int, str]] = {}
        for item in root.findall(q(W, "abstractNum")):
            levels = {}
            for lvl in item.findall(q(W, "lvl")):
                fmt = lvl.find(q(W, "numFmt"))
                value = fmt.get(q(W, "val"), "bullet") if fmt is not None else "bullet"
                levels[int(lvl.get(q(W, "ilvl"), "0"))] = "ul" if value in ("bullet", "none") else "ol"
            abstract[item.get(q(W, "abstractNumId"), "")] = levels
        for num in root.findall(q(W, "num")):
            ref = num.find(q(W, "abstractNumId"))
            levels = abstract.get(ref.get(q(W, "val"), "") if ref is not None else "", {})
            for level, kind in levels.items():
                out[(num.get(q(W, "numId"), ""), level)] = kind
        return out

    def _style_num(self, sid: str) -> Optional[Tuple[str, int]]:
        """Numbering inherited from a paragraph style ("List Bullet", "List Number" keep numPr there)."""
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            style = self.styles.get(sid)
            if not style:
                return None
            if style.get("num"):
                return style["num"]
            sid = style["based"]
        return None

    def _style_kind(self, sid: str) -> str:
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            style = self.styles.get(sid)
            if not style:
                break
            name = style["name"]
            match = re.match(r"heading\s*(\d)", name)
            if match:
                return "h" + str(min(6, max(1, int(match.group(1)))))
            if name == "title":
                return "h1"
            if name == "subtitle":
                return "h2"
            if "quote" in name:
                return "blockquote"
            if name in ("html preformatted", "code", "source code", "plain text"):
                return "pre"
            if style["outline"] is not None and style["outline"] < 6:
                return "h" + str(style["outline"] + 1)
            sid = style["based"]
        return "p"

    def blocks(self) -> List[Block]:
        body = self.doc.find(q(W, "body"))
        out: List[Block] = []
        if body is not None:
            self._container(body, out)
        return out

    def _container(self, element, out: List[Block]) -> None:
        for child in element:
            tag = child.tag
            if tag == q(W, "p"):
                self._paragraph(child, out)
            elif tag == q(W, "tbl"):
                out.append(self._table(child))
            elif tag in (q(W, "sdt"),):
                content = child.find(q(W, "sdtContent"))
                if content is not None:
                    self._container(content, out)
            elif tag in (q(W, "customXml"), q(W, "ins"), q(W, "smartTag")):
                self._container(child, out)

    def _table(self, element) -> Table:
        table = Table()
        for tr in element.findall(q(W, "tr")):
            header = tr.find(f"{q(W, 'trPr')}/{q(W, 'tblHeader')}") is not None
            row: List[Cell] = []
            for tc in tr.findall(q(W, "tc")):
                span_el = tc.find(f"{q(W, 'tcPr')}/{q(W, 'gridSpan')}")
                try:
                    span = int(span_el.get(q(W, "val"), "1")) if span_el is not None else 1
                except ValueError:
                    span = 1
                blocks: List[Block] = []
                merge = tc.find(f"{q(W, 'tcPr')}/{q(W, 'vMerge')}")
                if merge is None or merge.get(q(W, "val")) == "restart":
                    self._container(tc, blocks)
                cell = Cell(blocks=[b for b in blocks if isinstance(b, Para)], header=header, colspan=max(1, span))
                row.append(cell)
            table.rows.append(row)
        return table

    def _paragraph(self, element, out: List[Block]) -> None:
        para = Para()
        ppr = element.find(q(W, "pPr"))
        if ppr is not None:
            style = ppr.find(q(W, "pStyle"))
            style_num = None
            if style is not None:
                para.kind = self._style_kind(style.get(q(W, "val"), ""))
                style_num = self._style_num(style.get(q(W, "val"), ""))
            jc = ppr.find(q(W, "jc"))
            if jc is not None:
                para.align = {"center": "center", "right": "right", "end": "right", "both": "justify",
                              "distribute": "justify", "left": "", "start": ""}.get(jc.get(q(W, "val"), ""), "")
            num = ppr.find(q(W, "numPr"))
            if num is not None or style_num:
                num_id = num.find(q(W, "numId")) if num is not None else None
                ilvl = num.find(q(W, "ilvl")) if num is not None else None
                nid = num_id.get(q(W, "val"), "") if num_id is not None else (style_num[0] if style_num else "")
                try:
                    level = int(ilvl.get(q(W, "val"), "0")) if ilvl is not None else (style_num[1] if style_num else 0)
                except ValueError:
                    level = 0
                if nid and nid != "0":
                    para.kind = "li"
                    para.level = max(0, min(8, level))
                    para.list = self.numbering.get((nid, level), "ul")
        breaks: List[Rule] = []
        self._inline(element, para, Run(), breaks)
        if not (breaks and not para.runs):
            out.append(para)
        out.extend(breaks)

    def _run_format(self, rpr, base: Run) -> Run:
        run = Run(bold=base.bold, italic=base.italic, underline=base.underline, strike=base.strike,
                  code=base.code, sup=base.sup, sub=base.sub, color=base.color, highlight=base.highlight,
                  size=base.size, font=base.font, link=base.link)
        if rpr is None:
            return run

        def flag(name: str) -> Optional[bool]:
            el = rpr.find(q(W, name))
            if el is None:
                return None
            return el.get(q(W, "val"), "true").lower() not in ("0", "false", "off", "none")
        for name, attr in (("b", "bold"), ("i", "italic"), ("strike", "strike")):
            value = flag(name)
            if value is not None:
                setattr(run, attr, value)
        if flag("dstrike"):
            run.strike = True
        underline = rpr.find(q(W, "u"))
        if underline is not None:
            run.underline = underline.get(q(W, "val"), "single") != "none"
        color = rpr.find(q(W, "color"))
        if color is not None:
            run.color = norm_color(color.get(q(W, "val"), ""))
        highlight = rpr.find(q(W, "highlight"))
        if highlight is not None:
            run.highlight = HIGHLIGHT.get(highlight.get(q(W, "val"), ""), "")
        shading = rpr.find(q(W, "shd"))
        if shading is not None and not run.highlight:
            run.highlight = norm_color(shading.get(q(W, "fill"), ""))
        size = rpr.find(q(W, "sz"))
        if size is not None:
            try:
                run.size = int(size.get(q(W, "val"), "0")) / 2
            except ValueError:
                pass
        fonts = rpr.find(q(W, "rFonts"))
        if fonts is not None:
            run.font = fonts.get(q(W, "ascii")) or fonts.get(q(W, "hAnsi")) or fonts.get(q(W, "cs")) or run.font
        vert = rpr.find(q(W, "vertAlign"))
        if vert is not None:
            value = vert.get(q(W, "val"), "")
            run.sup, run.sub = value == "superscript", value == "subscript"
        style = rpr.find(q(W, "rStyle"))
        if style is not None and style.get(q(W, "val"), "").lower() in ("htmlcode", "code", "inlinecode", "verbatimchar"):
            run.code = True
        return run

    def _image(self, element) -> Optional[Run]:
        blip = element.find(f".//{q(A, 'blip')}")
        rid = blip.get(q(R, "embed"), "") if blip is not None else ""
        if not rid:
            imagedata = element.find(f".//{q(V, 'imagedata')}")
            rid = imagedata.get(q(R, "id"), "") if imagedata is not None else ""
        target = self.rels.get(rid, ("", ""))[0]
        if not target:
            return None
        name = _resolve(self.folder, target)
        ext = name.rsplit(".", 1)[-1].lower()
        mime = MIME.get(ext, "")
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/svg+xml"):
            return Run(text="[image]")
        data = _read_part(self.zip, name)
        if not data or len(data) > 25 * 1024 * 1024:
            return None
        width = height = 0
        extent = element.find(f".//{q(WP, 'extent')}")
        if extent is not None:
            try:
                width = int(int(extent.get("cx", "0")) / 9525)
                height = int(int(extent.get("cy", "0")) / 9525)
            except ValueError:
                pass
        self.images += 1
        return Run(image=f"data:{mime};base64," + base64.b64encode(data).decode("ascii"), width=width, height=height)

    def _inline(self, element, para: Para, base: Run, breaks: List[Rule]) -> None:
        for child in element:
            tag = child.tag
            if tag == q(W, "r"):
                fmt = self._run_format(child.find(q(W, "rPr")), base)
                for item in child:
                    t = item.tag
                    if t == q(W, "t"):
                        if item.text:
                            run = Run(**{**fmt.__dict__, "text": item.text})
                            self._append(para, run)
                    elif t == q(W, "tab"):
                        self._append(para, Run(**{**fmt.__dict__, "text": "\t"}))
                    elif t in (q(W, "br"), q(W, "cr")):
                        if item.get(q(W, "type")) == "page":
                            breaks.append(Rule(page_break=True))
                        else:
                            para.runs.append(Run(br=True))
                    elif t == q(W, "noBreakHyphen"):
                        self._append(para, Run(**{**fmt.__dict__, "text": "\u2011"}))
                    elif t == q(W, "sym"):
                        self._append(para, Run(**{**fmt.__dict__, "text": "\u25a1"}))
                    elif t in (q(W, "drawing"), q(W, "pict"), q(W, "object")):
                        image = self._image(item)
                        if image:
                            para.runs.append(image)
                    elif t == q(MC, "AlternateContent"):
                        choice = item.find(q(MC, "Choice"))
                        if choice is not None:
                            image = self._image(choice)
                            if image:
                                para.runs.append(image)
            elif tag == q(W, "hyperlink"):
                rid = child.get(q(R, "id"), "")
                target = self.rels.get(rid, ("", ""))[0] if rid else ""
                anchor = child.get(q(W, "anchor"), "")
                link = safe_link(target) or (("#" + anchor) if anchor else "")
                sub = Run(**{**base.__dict__, "link": link or base.link})
                self._inline(child, para, sub, breaks)
            elif tag in (q(W, "ins"), q(W, "smartTag"), q(W, "fldSimple"), q(W, "customXml"), q(W, "sdt"),
                         q(W, "sdtContent"), q(W, "bdo"), q(W, "dir")):
                self._inline(child, para, base, breaks)

    @staticmethod
    def _append(para: Para, run: Run) -> None:
        if para.runs and not para.runs[-1].br and not para.runs[-1].image and para.runs[-1].same_format(run):
            para.runs[-1].text += run.text
        else:
            para.runs.append(run)

    def page_setup(self) -> Dict[str, Dict[str, str]]:
        body = self.doc.find(q(W, "body"))
        sect = body.find(q(W, "sectPr")) if body is not None else None
        out: Dict[str, Dict[str, str]] = {}
        if sect is not None:
            for name in ("pgSz", "pgMar"):
                el = sect.find(q(W, name))
                if el is not None:
                    out[name] = {k.split("}")[-1]: v for k, v in el.attrib.items()}
        return out

    def properties(self) -> Dict[str, str]:
        data = _read_part(self.zip, "docProps/core.xml")
        out: Dict[str, str] = {}
        if not data:
            return out
        try:
            root = safe_fromstring(data)
        except ET.ParseError:
            return out
        for child in root:
            name = child.tag.split("}")[-1]
            if name in ("title", "subject", "creator", "description", "keywords") and child.text:
                out[name] = child.text.strip()[:500]
        return out

def read_docx(data: bytes) -> Tuple[List[Block], dict]:
    from .richhtml import tidy
    reader = _Reader(data)
    blocks = tidy(reader.blocks())
    meta = {"page": reader.page_setup(), "properties": reader.properties(), "images": reader.images}
    return blocks, meta

def docx_text(data: bytes) -> str:
    from .richhtml import to_text
    return to_text(read_docx(data)[0])

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%(W)s">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ru-RU" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
%(headings)s
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="567"/><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="A0A0A0"/></w:pBdr></w:pPr><w:rPr><w:i/><w:color w:val="555555"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:shd w:val="clear" w:color="auto" w:fill="F3F3F3"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="40"/><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="InlineCode"><w:name w:val="Inline Code"/><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:shd w:val="clear" w:color="auto" w:fill="F0F0F0"/></w:rPr></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/><w:pPr><w:spacing w:after="0"/></w:pPr><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:left w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:right w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/></w:tblBorders></w:tblPr></w:style>
</w:styles>"""

_HEADING_SIZES = {1: 32, 2: 28, 3: 26, 4: 24, 5: 22, 6: 22}

def _styles_xml() -> str:
    headings = []
    for level in range(1, 7):
        color = '<w:color w:val="1F3864"/>' if level <= 2 else ""
        headings.append(
            f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/>'
            f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>'
            f'<w:spacing w:before="{280 if level < 3 else 200}" w:after="80"/><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:sz w:val="{_HEADING_SIZES[level]}"/><w:szCs w:val="{_HEADING_SIZES[level]}"/>'
            f'{color}</w:rPr></w:style>')
    return _STYLES % {"W": W, "headings": "\n".join(headings)}

def _numbering_xml(nums: List[Tuple[int, str]]) -> str:
    bullets = ["\u2022", "\u25e6", "\u25aa"]
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{W}">']
    for abstract_id, kind in ((1, "ul"), (2, "ol")):
        out.append(f'<w:abstractNum w:abstractNumId="{abstract_id}"><w:multiLevelType w:val="hybridMultilevel"/>')
        for level in range(9):
            if kind == "ul":
                fmt, text = "bullet", bullets[level % 3]
            else:
                fmt = ("decimal", "lowerLetter", "lowerRoman")[level % 3]
                text = f"%{level + 1}."
            out.append(f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
                       f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/><w:pPr>'
                       f'<w:ind w:left="{720 + 360 * level}" w:hanging="360"/></w:pPr></w:lvl>')
        out.append("</w:abstractNum>")
    for num_id, kind in nums:
        abstract = 1 if kind == "ul" else 2
        restart = "" if kind == "ul" else (
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>')
        out.append(f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abstract}"/>{restart}</w:num>')
    out.append("</w:numbering>")
    return "".join(out)

def _attr(value: str) -> str:
    return escape(str(value), {'"': "&quot;"})

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

def _text(value: str) -> str:
    return escape(_XML_BAD.sub("", value))

class _Writer:
    def __init__(self, page: Optional[dict] = None) -> None:
        self.rels: List[Tuple[str, str, str, str]] = []
        self.media: List[Tuple[str, bytes]] = []
        self.nums: List[Tuple[int, str]] = []
        self.page = page or {}
        self.drawing_id = 0
        self._rid = 3

    def rid(self) -> str:
        self._rid += 1
        return f"rId{self._rid}"

    def link(self, url: str) -> str:
        for rid, kind, target, _mode in self.rels:
            if kind == "hyperlink" and target == url:
                return rid
        rid = self.rid()
        self.rels.append((rid, "hyperlink", url, "External"))
        return rid

    def image(self, data_uri: str) -> Optional[Tuple[str, bytes, str]]:
        match = re.match(r"data:image/(png|jpe?g|gif|bmp|webp|svg\+xml);base64,(.*)$", data_uri, re.S)
        if not match:
            return None
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(match.group(1), match.group(1))
        try:
            data = base64.b64decode(match.group(2))
        except (ValueError, TypeError):
            return None
        name = f"image{len(self.media) + 1}.{ext}"
        self.media.append((name, data))
        rid = self.rid()
        self.rels.append((rid, "image", f"media/{name}", ""))
        return rid, data, ext

    def run_xml(self, run: Run) -> str:
        if run.br:
            return "<w:r><w:br/></w:r>"
        if run.image:
            return self.picture(run)
        props = []
        if run.code:
            props.append('<w:rStyle w:val="InlineCode"/>')
        elif run.link:
            props.append('<w:rStyle w:val="Hyperlink"/>')
        if run.font:
            f = _attr(run.font)
            props.append(f'<w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:cs="{f}" w:eastAsia="{f}"/>')
        if run.bold:
            props.append("<w:b/><w:bCs/>")
        if run.italic:
            props.append("<w:i/><w:iCs/>")
        if run.strike:
            props.append("<w:strike/>")
        if run.color:
            props.append(f'<w:color w:val="{run.color.lstrip("#").upper()}"/>')
        if run.size:
            half = int(round(run.size * 2))
            props.append(f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>')
        if run.highlight:
            named = {v: k for k, v in HIGHLIGHT.items()}.get(run.highlight.lower())
            if named:
                props.append(f'<w:highlight w:val="{named}"/>')
            else:
                props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{run.highlight.lstrip("#").upper()}"/>')
        if run.underline:
            props.append('<w:u w:val="single"/>')
        if run.sup:
            props.append('<w:vertAlign w:val="superscript"/>')
        elif run.sub:
            props.append('<w:vertAlign w:val="subscript"/>')
        rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
        pieces = []
        for index, part in enumerate(run.text.split("\t")):
            if index:
                pieces.append("<w:tab/>")
            if part:
                pieces.append(f'<w:t xml:space="preserve">{_text(part)}</w:t>')
        return f"<w:r>{rpr}{''.join(pieces)}</w:r>"

    def picture(self, run: Run) -> str:
        made = self.image(run.image)
        if not made:
            return ""
        rid, data, _ext = made
        width, height = run.width, run.height
        if not (width and height):
            size = _image_size(data)
            if size:
                if width and not height:
                    height = int(size[1] * width / max(size[0], 1))
                elif height and not width:
                    width = int(size[0] * height / max(size[1], 1))
                else:
                    width, height = size
            else:
                width, height = width or 320, height or 240
        max_width = 624
        if width > max_width:
            height = int(height * max_width / width)
            width = max_width
        cx, cy = width * 9525, height * 9525
        self.drawing_id += 1
        n = self.drawing_id
        return (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
                f'<wp:docPr id="{n}" name="Picture {n}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/>'
                f'</wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="{PIC}"><pic:pic><pic:nvPicPr>'
                f'<pic:cNvPr id="{n}" name="image{n}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
                f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr>'
                f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect">'
                f'<a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')

    def runs_xml(self, runs: List[Run]) -> str:
        out = []
        index = 0
        while index < len(runs):
            run = runs[index]
            if run.link and not run.image and not run.br:
                group = []
                while index < len(runs) and runs[index].link == run.link and not runs[index].br:
                    group.append(self.run_xml(runs[index]))
                    index += 1
                if run.link.startswith("#"):
                    out.append(f'<w:hyperlink w:anchor="{_attr(run.link[1:])}">{"".join(group)}</w:hyperlink>')
                else:
                    out.append(f'<w:hyperlink r:id="{self.link(run.link)}" w:history="1">{"".join(group)}</w:hyperlink>')
                continue
            out.append(self.run_xml(run))
            index += 1
        return "".join(out)

    def para_xml(self, para: Para, num_id: int = 0) -> str:
        props = []
        style = {"h1": "Heading1", "h2": "Heading2", "h3": "Heading3", "h4": "Heading4", "h5": "Heading5",
                 "h6": "Heading6", "blockquote": "Quote", "pre": "Code", "li": "ListParagraph"}.get(para.kind)
        if style:
            props.append(f'<w:pStyle w:val="{style}"/>')
        if para.kind == "li" and num_id:
            props.append(f'<w:numPr><w:ilvl w:val="{max(0, min(8, para.level))}"/><w:numId w:val="{num_id}"/></w:numPr>')
        if para.align:
            jc = "both" if para.align == "justify" else para.align
            props.append(f'<w:jc w:val="{jc}"/>')
        ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
        runs = list(para.runs)
        if para.checked is not None:
            runs.insert(0, Run(text=("\u2611 " if para.checked else "\u2610 ")))
        return f"<w:p>{ppr}{self.runs_xml(runs)}</w:p>"

    def table_xml(self, table: Table) -> str:
        width = max((sum(c.colspan for c in row) for row in table.rows), default=1)
        col = int(9360 / max(width, 1))
        grid = "".join(f'<w:gridCol w:w="{col}"/>' for _ in range(width))
        rows = []
        for row in table.rows:
            cells = []
            header = bool(row) and all(c.header for c in row)
            for cell in row:
                span = f'<w:gridSpan w:val="{cell.colspan}"/>' if cell.colspan > 1 else ""
                inner = "".join(self.para_xml(p) for p in cell.blocks if isinstance(p, Para)) or "<w:p/>"
                if cell.header:
                    inner = re.sub(r"<w:r>(?!<w:rPr>)", "<w:r><w:rPr><w:b/></w:rPr>", inner)
                    inner = re.sub(r"<w:r><w:rPr>(?!<w:b/>)", "<w:r><w:rPr><w:b/>", inner)
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{col * cell.colspan}" w:type="dxa"/>{span}</w:tcPr>{inner}</w:tc>')
            trpr = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
            rows.append(f"<w:tr>{trpr}{''.join(cells)}</w:tr>")
        return (f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
                f'<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
                f'w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')

    def body(self, blocks: List[Block]) -> str:
        out = []
        num_id = 0
        prev_list = ""
        for block in blocks:
            if isinstance(block, Para) and block.kind == "li":
                key = block.list or "ul"
                if prev_list != key or (block.level == 0 and key == "ol" and prev_list != key):
                    num_id = len(self.nums) + 1
                    self.nums.append((num_id, key))
                prev_list = key
                out.append(self.para_xml(block, num_id))
                continue
            prev_list = ""
            if isinstance(block, Table) and out and out[-1].startswith("<w:tbl>"):
                out.append("<w:p/>")
            if isinstance(block, Rule):
                out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>' if block.page_break else
                           '<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="BFBFBF"/>'
                           '</w:pBdr></w:pPr></w:p>')
            elif isinstance(block, Table):
                out.append(self.table_xml(block))
            else:
                if block.kind == "pre":
                    for line in block.text.split("\n"):
                        out.append(self.para_xml(Para(kind="pre", runs=[Run(text=line)] if line else [])))
                else:
                    out.append(self.para_xml(block))
        size = self.page.get("pgSz") or {"w": "11906", "h": "16838"}
        margin = self.page.get("pgMar") or {"top": "1134", "right": "850", "bottom": "1134", "left": "1701",
                                            "header": "708", "footer": "708", "gutter": "0"}
        size_xml = " ".join(f'w:{k}="{_attr(v)}"' for k, v in size.items() if re.fullmatch(r"\w+", k))
        margin_xml = " ".join(f'w:{k}="{_attr(v)}"' for k, v in margin.items() if re.fullmatch(r"\w+", k))
        out.append(f"<w:sectPr><w:pgSz {size_xml}/><w:pgMar {margin_xml}/></w:sectPr>")
        return "".join(out)

def _image_size(data: bytes) -> Optional[Tuple[int, int]]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            length = int.from_bytes(data[index + 2:index + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
            index += 2 + length
    if data[:2] == b"BM" and len(data) >= 26:
        return int.from_bytes(data[18:22], "little"), abs(int.from_bytes(data[22:26], "little", signed=True))
    return None

_HF_TYPES = {"headerReference": ("header", "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"),
             "footerReference": ("footer", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml")}

def _carry_headers(original: bytes) -> dict:
    """Headers/footers of the original document, copied verbatim into the rewritten package.

    MemuNote edits only the body; without this a save silently dropped the page headers and footers
    (page numbers, letterheads). Their own pictures/links come along (parts renamed ``mn_*`` so they
    never clash with the body's media). Returns sectPr references, parts, rels and content types.
    """
    out = {"refs": [], "parts": [], "rels": [], "overrides": [], "defaults": {}, "title_pg": False}
    try:
        reader = _Reader(original)
    except Exception:  # noqa: BLE001 - damaged original: nothing to carry
        return out
    body = reader.doc.find(q(W, "body"))
    sect = body.find(q(W, "sectPr")) if body is not None else None
    if sect is None:
        return out
    out["title_pg"] = sect.find(q(W, "titlePg")) is not None
    count = 0
    for child in sect:
        local = child.tag.split("}")[-1]
        if local not in _HF_TYPES:
            continue
        kind, ctype = _HF_TYPES[local]
        target, mode = reader.rels.get(child.get(q(R, "id"), ""), ("", ""))
        if not target or mode:
            continue
        part = _resolve(reader.folder, target)
        try:
            data = _read_part(reader.zip, part)
        except DocxError:
            data = None
        if not data:
            continue
        count += 1
        name = f"mn_{kind}{count}.xml"
        rels_xml = ""
        part_folder = part.rpartition("/")[0]
        rels_data = _read_part(reader.zip, f"{part_folder}/_rels/{part.rpartition('/')[2]}.rels")
        if rels_data:
            try:
                rels_root = safe_fromstring(rels_data)
            except ET.ParseError:
                rels_root = None
            items = []
            for rel in (rels_root if rels_root is not None else []):
                rid, rtype, rtarget = rel.get("Id", ""), rel.get("Type", ""), rel.get("Target", "")
                if rel.get("TargetMode") == "External":
                    items.append(f'<Relationship Id="{_attr(rid)}" Type="{_attr(rtype)}" Target="{_attr(rtarget)}" TargetMode="External"/>')
                    continue
                media_part = _resolve(part_folder, rtarget)
                try:
                    media = _read_part(reader.zip, media_part)
                except DocxError:
                    media = None
                ext = media_part.rsplit(".", 1)[-1].lower() if "." in media_part else ""
                if not media or ext not in MIME:
                    continue
                media_name = f"media/mn_{kind}{count}_{len(items) + 1}.{ext}"
                out["parts"].append((f"word/{media_name}", media))
                out["defaults"][ext] = MIME[ext]
                items.append(f'<Relationship Id="{_attr(rid)}" Type="{_attr(rtype)}" Target="{media_name}"/>')
            rels_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_REL}">'
                        + "".join(items) + "</Relationships>")
        out["parts"].append((f"word/{name}", data))
        if rels_xml:
            out["parts"].append((f"word/_rels/{name}.rels", rels_xml.encode("utf-8")))
        rid = f"rIdMnHf{count}"
        out["rels"].append((rid, kind, name))
        htype = child.get(q(W, "type"), "default")
        out["refs"].append(f'<w:{local} w:type="{_attr(htype)}" r:id="{rid}"/>')
        out["overrides"].append(f'<Override PartName="/word/{name}" ContentType="{ctype}"/>')
    return out

def _original_core(original: bytes) -> Dict[str, str]:
    """creator / created of the original (a save must not claim MemuNote wrote the document)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(original))
        root = safe_fromstring(_read_part(archive, "docProps/core.xml") or b"<x/>")
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for child in root:
        local = child.tag.split("}")[-1]
        if local in ("creator", "created", "subject", "keywords", "description", "lastModifiedBy") and child.text:
            out[local] = child.text.strip()[:500]
    return out

def write_docx(blocks: List[Block], title: str = "", original: Optional[bytes] = None) -> bytes:
    page = {}
    if original:
        try:
            page = _Reader(original).page_setup()
        except Exception:     # noqa: BLE001 - a damaged original only loses its page setup
            page = {}
    writer = _Writer(page)
    body = writer.body(blocks)
    carried = _carry_headers(original) if original else {"refs": [], "parts": [], "rels": [], "overrides": [],
                                                          "defaults": {}, "title_pg": False}
    core_orig = _original_core(original) if original else {}
    if carried["refs"] or carried["title_pg"]:
        body = body.replace("<w:sectPr>", "<w:sectPr>" + "".join(carried["refs"]), 1)
        if carried["title_pg"]:
            body = body.replace("</w:sectPr>", "<w:titlePg/></w:sectPr>", 1)
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}">'
                f"<w:body>{body}</w:body></w:document>")
    rels = [f'<Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>',
            f'<Relationship Id="rId2" Type="{R}/numbering" Target="numbering.xml"/>',
            f'<Relationship Id="rId3" Type="{R}/settings" Target="settings.xml"/>']
    for rid, kind, target, mode in writer.rels:
        extra = ' TargetMode="External"' if mode else ""
        rels.append(f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{_attr(target)}"{extra}/>')
    for rid, kind, target in carried["rels"]:
        rels.append(f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{_attr(target)}"/>')
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    defaults = {ext: MIME[ext] for name, _data in writer.media for ext in [name.rsplit(".", 1)[-1]]}
    defaults.update({ext: mime for ext, mime in carried["defaults"].items() if ext not in defaults})
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     + "".join(f'<Default Extension="{ext}" ContentType="{mime}"/>' for ext, mime in defaults.items())
                     + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                     '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                     + "".join(carried["overrides"]) + "</Types>")
    root_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_REL}">'
                 f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 f'<Relationship Id="rId3" Type="{R}/extended-properties" Target="docProps/app.xml"/></Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_text(title)}</dc:title><dc:creator>{_text(core_orig.get('creator', 'MemuNote'))}</dc:creator>"
            + "".join(f"<dc:{k}>{_text(core_orig[k])}</dc:{k}>" for k in ("subject", "description") if core_orig.get(k))
            + (f"<cp:keywords>{_text(core_orig['keywords'])}</cp:keywords>" if core_orig.get("keywords") else "")
            + f'<dcterms:created xsi:type="dcterms:W3CDTF">{_text(core_orig.get("created", now))}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           "<Application>MemuNote</Application></Properties>")
    settings = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings xmlns:w="{W}">'
                '<w:defaultTabStop w:val="708"/><w:characterSpacingControl w:val="doNotCompress"/>'
                '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
                "</w:settings>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _styles_xml())
        archive.writestr("word/numbering.xml", _numbering_xml(writer.nums))
        archive.writestr("word/settings.xml", settings)
        archive.writestr("word/_rels/document.xml.rels",
                         f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_REL}">'
                         + "".join(rels) + "</Relationships>")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        for name, data in writer.media:
            archive.writestr(f"word/media/{name}", data, compress_type=zipfile.ZIP_STORED)
        for name, data in carried["parts"]:
            archive.writestr(name, data)
    return buffer.getvalue()
