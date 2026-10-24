# -*- coding: utf-8 -*-
"""Round 29: chat export — DOCX writer (WordprocessingML by hand), MathML → OMML, inline KaTeX CSS.

The window renders the chat to HTML with its own renderer (Markdown, KaTeX, syntax highlighting,
tables, diagrams as PNG) and sends that HTML here. :func:`html_to_docx` turns it into a real Word
document without any third-party dependency: ``zipfile`` + XML strings.

What becomes what in Word
-------------------------
* headings, paragraphs, bold/italic/strike/underline/sup/sub, links (real hyperlinks), inline code;
* bullet / numbered / task lists (nested; every numbered list restarts at its own start);
* tables — real Word tables with a repeated header row, shading, column spans and alignment;
* code blocks — monospace shaded paragraphs with the token colours of the chosen theme;
* formulas — KaTeX ships MathML next to its HTML; it is converted to native Office Math (OMML), so
  equations stay editable. Anything the converter does not know falls back to the LaTeX source;
* images — PNG/JPEG/GIF/BMP (WebP is converted with Pillow), scaled to the text width;
* messages — a coloured role line («You · 12:30», «MemuAI · model»), reasoning and tool steps as
  shaded blocks, a title block with metadata, a page header and «page X of Y» footer;
* page size A4/Letter, orientation, margins, fonts, base size and a theme palette (light, dark —
  with a page background —, sepia, print or the app's current design).
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import html
import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PG_DIR = Path(__file__).parent / "pgstatic"
KATEX_DIR = PG_DIR / "vendor" / "katex"

MAX_HTML_CHARS = 200 * 1024 * 1024
MAX_IMAGE_BYTES = 30 * 1024 * 1024
FORMATS = ("pdf", "docx", "html", "md", "json", "txt")

PREF_DEFAULTS: Dict[str, Any] = {
    # The last options of the export dialog (format, theme, fonts, page…), restored next time.
    "chat_export_options": {},
}
_OPTION_KEYS = {
    "format": ("pdf", "docx", "html", "md", "json", "txt"),
    "theme": ("light", "dark", "sepia", "print", "app"),
    "font": None, "mono": None, "size": None, "page": ("a4", "letter"), "orientation": ("portrait", "landscape"),
    "margins": ("narrow", "normal", "wide"), "images": None, "math": None, "code": None, "reasoning": None,
    "tools": None, "meta": None, "header": None, "footer": None, "numbers": None, "toc": None,
    "range": ("all", "from", "selected"), "save": ("downloads", "ask"), "stats": None, "timestamps": None,
}


def normalize_prefs(out: Dict[str, Any], raw: Any) -> None:
    source = raw if isinstance(raw, dict) else {}
    value = source.get("chat_export_options", out.get("chat_export_options"))
    clean: Dict[str, Any] = {}
    if isinstance(value, dict):
        for key, allowed in _OPTION_KEYS.items():
            if key not in value:
                continue
            item = value[key]
            if allowed is not None:
                if item in allowed:
                    clean[key] = item
            elif isinstance(item, bool):
                clean[key] = item
            elif isinstance(item, (int, float)) and key == "size":
                clean[key] = max(8, min(20, int(item)))
            elif isinstance(item, str) and key in ("font", "mono", "header", "footer"):
                clean[key] = item[:120]
    out["chat_export_options"] = clean


# --------------------------------------------------------------------------- small helpers

def safe_filename(name: str, ext: str) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(name or "")).strip(" .")[:100] or "MemuAI chat"
    if stem.split(".", 1)[0].strip().casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                                                     *(f"lpt{i}" for i in range(1, 10))}:
        stem = "_" + stem
    ext = re.sub(r"[^a-z0-9]", "", str(ext or "").lower())[:8] or "txt"
    return f"{stem}.{ext}"


def decode_data_url(value: str, limit: int = MAX_IMAGE_BYTES) -> Tuple[str, bytes]:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        raise ValueError("not a data URL")
    header, payload = value.split(",", 1)
    mime = header[5:].split(";", 1)[0].strip().lower() or "application/octet-stream"
    if ";base64" in header:
        if len(payload) * 3 // 4 > limit:
            raise ValueError("too large")
        raw = base64.b64decode(payload, validate=False)
    else:
        from urllib.parse import unquote_to_bytes
        raw = unquote_to_bytes(payload)
    if len(raw) > limit:
        raise ValueError("too large")
    return mime, raw


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_SHORT_HEX = re.compile(r"^#?([0-9a-fA-F]{3})$")
_RGB = re.compile(r"^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)")


def hex_color(value: Any, fallback: str = "000000") -> str:
    text = str(value or "").strip()
    match = _HEX.match(text)
    if match:
        return match.group(1).upper()
    match = _SHORT_HEX.match(text)
    if match:
        return "".join(ch * 2 for ch in match.group(1)).upper()
    match = _RGB.match(text)
    if match:
        return "".join(f"{max(0, min(255, int(part))):02X}" for part in match.groups())
    return fallback


DEFAULT_PALETTE = {
    "bg": "FFFFFF", "text": "1F2328", "muted": "656D76", "accent": "B4532F", "border": "D0D7DE",
    "code_bg": "F6F8FA", "code_text": "1F2328", "quote_bg": "F6F8FA", "user_bg": "F3F0EA", "user_border": "B4532F",
    "assistant_border": "8C959F", "link": "0969DA", "table_head_bg": "EEF1F4", "step_bg": "F6F8FA",
    "tokens": {"kw": "CF222E", "type": "953800", "str": "0A3069", "com": "6E7781", "num": "0550AE", "fn": "8250DF",
               "op": "CF222E", "lit": "0550AE", "tag": "116329", "attr": "0550AE", "prop": "0550AE", "var": "953800",
               "meta": "6E7781", "punct": "57606A"},
}


def palette_from(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for key, value in DEFAULT_PALETTE.items():
        if key == "tokens":
            tokens = source.get("tokens") if isinstance(source.get("tokens"), dict) else {}
            out["tokens"] = {name: hex_color(tokens.get(name), color) for name, color in value.items()}
        else:
            out[key] = hex_color(source.get(key), value)
    return out


def xml_text(value: str) -> str:
    # Control characters are not allowed in XML 1.0 (a model may emit \x0b or \x1b).
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(value))
    return html.escape(clean, quote=False)


def xml_attr(value: str) -> str:
    return html.escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)), quote=True)


# --------------------------------------------------------------------------- KaTeX CSS with embedded fonts

_KATEX_CACHE: Dict[str, Any] = {}


def katex_inline_css() -> str:
    """KaTeX's stylesheet with its WOFF2 fonts embedded as data URLs (offline HTML/PDF)."""
    css_path = KATEX_DIR / "katex.min.css"
    try:
        stamp = css_path.stat().st_mtime_ns
    except OSError:
        return ""
    if _KATEX_CACHE.get("stamp") == stamp:
        return _KATEX_CACHE["css"]
    css = css_path.read_text(encoding="utf-8")

    def font_face(match: "re.Match[str]") -> str:
        block = match.group(0)
        woff2 = re.search(r"url\((?:fonts/)?([^)]+?\.woff2)\)", block)
        if not woff2:
            return block
        try:
            data = (KATEX_DIR / "fonts" / Path(woff2.group(1)).name).read_bytes()
        except OSError:
            return block
        src = f"src:url(data:font/woff2;base64,{base64.b64encode(data).decode('ascii')}) format(\"woff2\")"
        return re.sub(r"src:[^;}]+", src, block, count=1)

    css = re.sub(r"@font-face\{[^}]*\}", font_face, css)
    _KATEX_CACHE.update({"stamp": stamp, "css": css})
    return css


# --------------------------------------------------------------------------- tiny DOM

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Optional[Dict[str, str]] = None, parent: Optional["Node"] = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: List[Any] = []
        self.parent = parent

    @property
    def classes(self) -> set:
        return set(str(self.attrs.get("class") or "").split())

    def has_class(self, *names: str) -> bool:
        cls = self.classes
        return any(name in cls for name in names)

    def text(self) -> str:
        parts: List[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts)

    def find(self, tag: str) -> Optional["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    return child
                found = child.find(tag)
                if found is not None:
                    return found
        return None

    def elements(self) -> List["Node"]:
        return [child for child in self.children if isinstance(child, Node)]


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        node = Node(tag.lower(), {key.lower(): (value or "") for key, value in attrs}, self.cur)
        self.cur.children.append(node)
        if node.tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        node = Node(tag.lower(), {key.lower(): (value or "") for key, value in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        if data:
            self.cur.children.append(data)


def parse_html(source: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(str(source or ""))
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- MathML → OMML

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NARY = set("∑∏∐∫∬∭∮∯∰⋃⋂⋁⋀⨁⨂⨀")
ACCENTS = {"^": "̂", "ˆ": "̂", "~": "̃", "˜": "̃", "¯": "̅", "‾": "̅", "→": "⃗", "⃗": "⃗", "˙": "̇", "¨": "̈",
           "ˇ": "̌", "´": "́", "`": "̀", "˘": "̆", "⏞": "⏞", "⏟": "⏟"}


class MathConverter:
    def __init__(self, font: str = "Cambria Math", color: str = "") -> None:
        self.font = font
        self.color = color

    def run(self, text: str, *, style: str = "", scr: str = "", normal: bool = False) -> str:
        if not text:
            return ""
        props = []
        if scr:
            props.append(f'<m:scr m:val="{scr}"/>')
        if style:
            props.append(f'<m:sty m:val="{style}"/>')
        if normal:
            props.append("<m:nor/>")
        rpr = f"<m:rPr>{''.join(props)}</m:rPr>" if props else ""
        color = f'<w:color w:val="{self.color}"/>' if self.color else ""
        wpr = f'<w:rPr><w:rFonts w:ascii="{self.font}" w:hAnsi="{self.font}"/>{color}</w:rPr>'
        return f'<m:r>{rpr}{wpr}<m:t xml:space="preserve">{xml_text(text)}</m:t></m:r>'

    def convert(self, math: Node) -> str:
        return self.seq(math.elements() if math.tag != "semantics" else math.elements()[:1])

    def seq(self, nodes: List[Node]) -> str:
        out: List[str] = []
        index = 0
        while index < len(nodes):
            node = nodes[index]
            op = self._nary_operator(node)
            if op is not None:
                # «∑_{i}^{n} x_i»: the operand is the next sibling of the n-ary operator.
                operand = nodes[index + 1] if index + 1 < len(nodes) else None
                out.append(self._nary(node, op, operand))
                index += 2 if operand is not None else 1
                continue
            out.append(self.node(node))
            index += 1
        return "".join(out)

    def _nary_operator(self, node: Node) -> Optional[str]:
        if node.tag in ("munderover", "msubsup", "munder", "msub", "mover", "msup"):
            kids = node.elements()
            if kids and kids[0].tag == "mo" and kids[0].text().strip() in NARY:
                return kids[0].text().strip()
        if node.tag == "mo" and node.text().strip() in NARY:
            return node.text().strip()
        return None

    def _nary(self, node: Node, op: str, operand: Optional[Node]) -> str:
        kids = node.elements()
        sub = sup = ""
        under = node.tag in ("munderover", "munder", "mover")
        if node.tag in ("munderover", "msubsup") and len(kids) >= 3:
            sub, sup = self.node(kids[1]), self.node(kids[2])
        elif node.tag in ("munder", "msub") and len(kids) >= 2:
            sub = self.node(kids[1])
        elif node.tag in ("mover", "msup") and len(kids) >= 2:
            sup = self.node(kids[1])
        loc = "undOvr" if under or op in "∑∏∐⋃⋂⋁⋀⨁⨂⨀" else "subSup"
        props = (f'<m:naryPr><m:chr m:val="{xml_attr(op)}"/><m:limLoc m:val="{loc}"/>'
                 + ("" if sub else '<m:subHide m:val="1"/>') + ("" if sup else '<m:supHide m:val="1"/>') + "</m:naryPr>")
        body = self.node(operand) if operand is not None else ""
        return f"<m:nary>{props}<m:sub>{sub}</m:sub><m:sup>{sup}</m:sup><m:e>{body}</m:e></m:nary>"

    def node(self, node: Optional[Node]) -> str:
        if node is None:
            return ""
        tag = node.tag
        kids = node.elements()
        if tag in ("math", "mrow", "mstyle", "mpadded", "merror", "maction"):
            if tag == "mrow" and len(kids) >= 2 and kids[0].tag == "mo" and kids[-1].tag == "mo" and (
                    kids[0].attrs.get("fence") == "true" or kids[-1].attrs.get("fence") == "true"):
                beg, end = kids[0].text().strip(), kids[-1].text().strip()
                inner = self.seq(kids[1:-1])
                return (f'<m:d><m:dPr><m:begChr m:val="{xml_attr(beg)}"/><m:endChr m:val="{xml_attr(end)}"/></m:dPr>'
                        f"<m:e>{inner}</m:e></m:d>")
            return self.seq(kids)
        if tag == "semantics":
            return self.node(kids[0]) if kids else ""
        if tag in ("annotation", "annotation-xml", "mphantom", "none", "mprescripts"):
            return ""
        if tag == "mi":
            text = node.text()
            variant = node.attrs.get("mathvariant", "")
            scr = {"double-struck": "double-struck", "fraktur": "fraktur", "script": "script",
                   "sans-serif": "sans-serif", "monospace": "monospace"}.get(variant, "")
            style = {"normal": "p", "bold": "b", "italic": "i", "bold-italic": "bi"}.get(variant, "")
            if not style and len(text) > 1:
                style = "p"
            return self.run(text, style=style, scr=scr)
        if tag == "mn":
            return self.run(node.text(), style="p")
        if tag == "mo":
            return self.run(node.text(), style="p")
        if tag in ("mtext", "ms"):
            return self.run(node.text(), style="p", normal=True)
        if tag == "mspace":
            width = node.attrs.get("width", "")
            return self.run("\u2009" if width and not width.startswith("0") else "")
        if tag == "mfrac":
            num = self.node(kids[0]) if kids else ""
            den = self.node(kids[1]) if len(kids) > 1 else ""
            bar = node.attrs.get("linethickness", "")
            fpr = '<m:fPr><m:type m:val="noBar"/></m:fPr>' if bar in ("0", "0px", "0em", "0.0em") else ""
            return f"<m:f>{fpr}<m:num>{num}</m:num><m:den>{den}</m:den></m:f>"
        if tag == "msup":
            return f"<m:sSup><m:e>{self.node(kids[0]) if kids else ''}</m:e><m:sup>{self.node(kids[1]) if len(kids) > 1 else ''}</m:sup></m:sSup>"
        if tag == "msub":
            return f"<m:sSub><m:e>{self.node(kids[0]) if kids else ''}</m:e><m:sub>{self.node(kids[1]) if len(kids) > 1 else ''}</m:sub></m:sSub>"
        if tag == "msubsup":
            return (f"<m:sSubSup><m:e>{self.node(kids[0]) if kids else ''}</m:e>"
                    f"<m:sub>{self.node(kids[1]) if len(kids) > 1 else ''}</m:sub>"
                    f"<m:sup>{self.node(kids[2]) if len(kids) > 2 else ''}</m:sup></m:sSubSup>")
        if tag == "msqrt":
            return f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>{self.seq(kids)}</m:e></m:rad>'
        if tag == "mroot":
            return (f"<m:rad><m:deg>{self.node(kids[1]) if len(kids) > 1 else ''}</m:deg>"
                    f"<m:e>{self.node(kids[0]) if kids else ''}</m:e></m:rad>")
        if tag in ("mover", "munder", "munderover"):
            base = self.node(kids[0]) if kids else ""
            if tag == "mover" and len(kids) > 1:
                mark = kids[1].text().strip()
                if mark in ("¯", "‾", "―", "_") and kids[1].tag == "mo":
                    return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{base}</m:e></m:bar>'
                if node.attrs.get("accent") == "true" or mark in ACCENTS:
                    chr_ = ACCENTS.get(mark, mark)
                    if chr_ in ("⏞",):
                        return f'<m:groupChr><m:groupChrPr><m:chr m:val="⏞"/><m:pos m:val="top"/></m:groupChrPr><m:e>{base}</m:e></m:groupChr>'
                    return f'<m:acc><m:accPr><m:chr m:val="{xml_attr(chr_)}"/></m:accPr><m:e>{base}</m:e></m:acc>'
                return f"<m:limUpp><m:e>{base}</m:e><m:lim>{self.node(kids[1])}</m:lim></m:limUpp>"
            if tag == "munder" and len(kids) > 1:
                mark = kids[1].text().strip()
                if mark in ("_", "‾", "¯", "̲") and kids[1].tag == "mo":
                    return f'<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e>{base}</m:e></m:bar>'
                if mark == "⏟":
                    return f'<m:groupChr><m:groupChrPr><m:chr m:val="⏟"/><m:pos m:val="bot"/></m:groupChrPr><m:e>{base}</m:e></m:groupChr>'
                return f"<m:limLow><m:e>{base}</m:e><m:lim>{self.node(kids[1])}</m:lim></m:limLow>"
            if tag == "munderover" and len(kids) > 2:
                low = f"<m:limLow><m:e>{base}</m:e><m:lim>{self.node(kids[1])}</m:lim></m:limLow>"
                return f"<m:limUpp><m:e>{low}</m:e><m:lim>{self.node(kids[2])}</m:lim></m:limUpp>"
            return base
        if tag == "mtable":
            rows = []
            width = max((len(row.elements()) for row in kids if row.tag in ("mtr", "mlabeledtr")), default=1)
            for row in kids:
                if row.tag not in ("mtr", "mlabeledtr"):
                    continue
                cells = [cell for cell in row.elements() if cell.tag == "mtd"]
                if row.tag == "mlabeledtr" and cells:
                    cells = cells[1:]
                cells += [Node("mtd")] * (width - len(cells))
                rows.append("<m:mr>" + "".join(f"<m:e>{self.seq(cell.elements())}</m:e>" for cell in cells) + "</m:mr>")
            mpr = f'<m:mPr><m:mcs><m:mc><m:mcPr><m:count m:val="{width}"/><m:mcJc m:val="center"/></m:mcPr></m:mc></m:mcs></m:mPr>'
            return f"<m:m>{mpr}{''.join(rows)}</m:m>"
        if tag == "menclose":
            return f"<m:borderBox><m:e>{self.seq(kids)}</m:e></m:borderBox>"
        if tag == "mfenced":
            beg, end = node.attrs.get("open", "("), node.attrs.get("close", ")")
            return (f'<m:d><m:dPr><m:begChr m:val="{xml_attr(beg)}"/><m:endChr m:val="{xml_attr(end)}"/></m:dPr>'
                    + "".join(f"<m:e>{self.node(kid)}</m:e>" for kid in kids) + "</m:d>")
        if tag in ("mmultiscripts",):
            return self.seq(kids)
        # Unknown element: its text, so nothing silently disappears.
        return self.seq(kids) if kids else self.run(node.text())


def latex_of(katex: Node) -> str:
    annotation = None
    stack = [katex]
    while stack:
        node = stack.pop()
        for child in node.children:
            if isinstance(child, Node):
                if child.tag == "annotation":
                    annotation = child
                    break
                stack.append(child)
        if annotation is not None:
            break
    return annotation.text().strip() if annotation is not None else katex.text().strip()


def mathml_to_omml(math: Node, *, color: str = "") -> str:
    return MathConverter(color=color).convert(math)


# --------------------------------------------------------------------------- DOCX writer

EMU_PER_PX = 9525
TWIPS_PER_MM = 1440 / 25.4
PAGE_SIZES = {"a4": (210.0, 297.0), "letter": (215.9, 279.4)}
MARGINS = {"narrow": 12.7, "normal": 20.0, "wide": 30.0}

NS = ('xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      f'xmlns:m="{M_NS}" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
      'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="w14"')

BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "h1", "h2", "h3", "h4",
              "h5", "h6", "ul", "ol", "li", "pre", "blockquote", "table", "hr", "figure", "figcaption", "details",
              "summary", "dl", "dt", "dd", "iframe", "svg", "canvas", "video", "audio", "form"}
SKIP_TAGS = {"script", "style", "button", "noscript", "template", "input", "select", "textarea", "head", "title",
             "meta", "link", "svg", "canvas", "video", "audio", "object", "embed"}
SKIP_CLASSES = {"code-header", "katex-html", "code-copy-btn", "mx-noprint", "msg-actions", "sr-only", "mx-skip"}


class DocxBuilder:
    def __init__(self, options: Dict[str, Any]) -> None:
        self.opt = options
        self.pal = palette_from(options.get("palette"))
        self.font = str(options.get("font") or "Calibri")[:80]
        self.mono = str(options.get("mono") or "Consolas")[:80]
        self.size = max(8, min(20, int(options.get("size") or 11)))
        self.rels: List[Tuple[str, str, str, str]] = []   # (id, type, target, mode)
        self.media: List[Tuple[str, bytes]] = []
        self.content_types: set = set()
        self.nums: List[str] = []
        self.next_num = 3                                  # 1 = bullets, 2 = default decimal
        self.doc_pr = 1
        self.warnings: List[str] = []
        page = PAGE_SIZES.get(str(options.get("page") or "a4"), PAGE_SIZES["a4"])
        if options.get("orientation") == "landscape":
            page = (page[1], page[0])
        self.page_mm = page
        margin = options.get("margin_mm")
        try:
            margin = float(margin)
        except (TypeError, ValueError):
            margin = MARGINS.get(str(options.get("margins") or "normal"), 20.0)
        self.margin_mm = max(5.0, min(50.0, margin))
        self.content_width_emu = int((page[0] - 2 * self.margin_mm) / 25.4 * 914400)

    # ---- relationships & media
    def rel(self, rtype: str, target: str, external: bool = False) -> str:
        rid = f"rId{len(self.rels) + 10}"
        self.rels.append((rid, rtype, target, "External" if external else ""))
        return rid

    def add_image(self, data: bytes, mime: str) -> Tuple[str, int, int]:
        ext = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/gif": "gif", "image/bmp": "bmp"}.get(mime)
        width = height = 0
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                if ext is None:
                    buf = io.BytesIO()
                    (img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img).save(buf, "PNG")
                    data, ext = buf.getvalue(), "png"
        except Exception:
            if ext is None:
                raise ValueError("unsupported image")
        if not width or not height:
            width, height = 640, 360
        name = f"image{len(self.media) + 1}.{ext}"
        self.media.append((name, data))
        self.content_types.add(ext)
        rid = self.rel("http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", f"media/{name}")
        return rid, width, height

    def drawing(self, rid: str, width_px: int, height_px: int, *, max_width_px: Optional[float] = None, alt: str = "") -> str:
        cx, cy = width_px * EMU_PER_PX, height_px * EMU_PER_PX
        if max_width_px:
            limit = int(max_width_px * EMU_PER_PX)
            if cx > limit:
                cy, cx = int(cy * limit / cx), limit
        if cx > self.content_width_emu:
            cy, cx = int(cy * self.content_width_emu / cx), self.content_width_emu
        max_h = int((self.page_mm[1] - 2 * self.margin_mm - 30) / 25.4 * 914400)
        if cy > max_h:
            cx, cy = int(cx * max_h / cy), max_h
        self.doc_pr += 1
        pid = self.doc_pr
        alt_attr = xml_attr(alt[:200])
        return (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
                f'<wp:docPr id="{pid}" name="Picture {pid}" descr="{alt_attr}"/><wp:cNvGraphicFramePr>'
                '<a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic>'
                '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                f'<pic:nvPicPr><pic:cNvPr id="{pid}" name="Picture {pid}" descr="{alt_attr}"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                '</wp:inline></w:drawing></w:r>')

    # ---- runs
    def run(self, text: str, fmt: Dict[str, Any]) -> str:
        if text == "":
            return ""
        props: List[str] = []
        if fmt.get("style"):
            props.append(f'<w:rStyle w:val="{fmt["style"]}"/>')
        font = self.mono if fmt.get("code") else fmt.get("font")
        if font:
            props.append(f'<w:rFonts w:ascii="{xml_attr(font)}" w:hAnsi="{xml_attr(font)}" w:cs="{xml_attr(font)}" w:eastAsia="{xml_attr(font)}"/>')
        if fmt.get("bold"):
            props.append("<w:b/><w:bCs/>")
        if fmt.get("italic"):
            props.append("<w:i/><w:iCs/>")
        if fmt.get("strike"):
            props.append("<w:strike/>")
        color = fmt.get("color")
        if color:
            props.append(f'<w:color w:val="{color}"/>')
        if fmt.get("size"):
            half = int(round(float(fmt["size"]) * 2))
            props.append(f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>')
        if fmt.get("underline"):
            props.append('<w:u w:val="single"/>')
        if fmt.get("shade"):
            props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{fmt["shade"]}"/>')
        if fmt.get("vert"):
            props.append(f'<w:vertAlign w:val="{fmt["vert"]}"/>')
        rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
        pieces: List[str] = []
        lines = str(text).split("\n")
        for index, line in enumerate(lines):
            if index:
                pieces.append("<w:br/>")
            parts = line.split("\t")
            for j, part in enumerate(parts):
                if j:
                    pieces.append("<w:tab/>")
                if part:
                    pieces.append(f'<w:t xml:space="preserve">{xml_text(part)}</w:t>')
        return f"<w:r>{rpr}{''.join(pieces)}</w:r>" if pieces else ""

    def para(self, content: str, ppr: Dict[str, Any]) -> str:
        props: List[str] = []
        if ppr.get("style"):
            props.append(f'<w:pStyle w:val="{ppr["style"]}"/>')
        if ppr.get("keep_next"):
            props.append("<w:keepNext/>")
        if ppr.get("num") is not None:
            num_id, level = ppr["num"]
            props.append(f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>')
        borders = []
        if ppr.get("border_left"):
            borders.append(f'<w:left w:val="single" w:sz="18" w:space="8" w:color="{ppr["border_left"]}"/>')
        if ppr.get("border_box"):
            color = ppr["border_box"]
            borders = [f'<w:{side} w:val="single" w:sz="4" w:space="4" w:color="{color}"/>' for side in ("top", "left", "bottom", "right")]
        if ppr.get("border_bottom"):
            borders.append(f'<w:bottom w:val="single" w:sz="6" w:space="1" w:color="{ppr["border_bottom"]}"/>')
        if borders:
            props.append(f"<w:pBdr>{''.join(borders)}</w:pBdr>")
        if ppr.get("shade"):
            props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{ppr["shade"]}"/>')
        spacing = ppr.get("spacing")
        if spacing:
            props.append(f'<w:spacing w:before="{spacing[0]}" w:after="{spacing[1]}"/>')
        indent = int(ppr.get("indent") or 0)
        if indent:
            props.append(f'<w:ind w:left="{indent}" w:right="{int(ppr.get("indent_right") or 0)}"/>')
        if ppr.get("align"):
            props.append(f'<w:jc w:val="{ppr["align"]}"/>')
        pprxml = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
        return f"<w:p>{pprxml}{content}</w:p>"

    # ---- numbering
    def new_list(self, ordered: bool, start: int = 1, level: int = 0) -> int:
        """Bullets share one numbering; every numbered list gets its own (it restarts at ``start``)."""
        if not ordered:
            return 1
        num_id = self.next_num
        self.next_num += 1
        self.nums.append(f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="2"/>'
                         f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="{max(1, int(start))}"/></w:lvlOverride></w:num>')
        return num_id

    # ---- conversion
    def convert(self, root: Node) -> str:
        ctx = {"ppr": {}, "fmt": {}, "list": None, "indent": 0}
        return "".join(self.blocks(root, ctx))

    def blocks(self, node: Node, ctx: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        runs: List[str] = []

        def flush() -> None:
            if runs and any(r.strip() for r in runs):
                content = "".join(runs)
                if re.sub(r"<[^>]+>", "", content).strip() or "<w:drawing>" in content or "<m:oMath" in content:
                    out.append(self.para(content, dict(ctx["ppr"])))
            runs.clear()

        for child in node.children:
            if isinstance(child, str):
                text = re.sub(r"\s+", " ", child)
                if text.strip() or (runs and text):
                    runs.append(self.run(text if runs else text.lstrip(), ctx["fmt"]))
                continue
            if self._skip(child):
                continue
            if self._is_block(child):
                flush()
                out.extend(self.block(child, ctx))
            else:
                runs.extend(self.inline(child, ctx, ctx["fmt"]))
        flush()
        return out

    def _skip(self, node: Node) -> bool:
        if node.tag in SKIP_TAGS and not (node.tag == "svg" and node.attrs.get("data-mx-keep")):
            return True
        if node.classes & SKIP_CLASSES:
            return True
        style = str(node.attrs.get("style") or "").replace(" ", "").lower()
        return "display:none" in style or node.attrs.get("hidden") is not None and node.tag != "details"

    def _is_block(self, node: Node) -> bool:
        if node.tag in BLOCK_TAGS:
            return True
        if node.tag == "span" and node.has_class("katex-display"):
            return True
        if node.tag == "img" and node.has_class("mx-block-img"):
            return True
        return node.tag == "div"

    def block(self, node: Node, ctx: Dict[str, Any]) -> List[str]:
        tag = node.tag
        pal = self.pal
        cls = node.classes
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            style = "Title" if "mx-title" in cls else f"Heading{min(level, 6)}"
            return [self.para("".join(self.inline_children(node, ctx, {**ctx["fmt"]})),
                              {**ctx["ppr"], "style": style, "keep_next": True, "num": None})]
        if tag == "hr":
            return [self.para("", {"border_bottom": pal["border"], "spacing": (120, 120)})]
        if tag == "span" and "katex-display" in cls:
            math = node.find("math")
            return [self.para(self.math_xml(node, math, display=True), {**ctx["ppr"], "align": "center", "num": None})]
        if tag == "img":
            return [self.para(self.image(node), {**ctx["ppr"], "align": "center", "num": None})]
        if tag in ("ul", "ol"):
            return self.list_block(node, ctx, tag == "ol")
        if tag == "li":
            return self.blocks(node, ctx)
        if tag == "pre":
            return self.code_block(node, ctx)
        if tag == "blockquote":
            sub = self._child_ctx(ctx, ppr={"style": "MXQuote", "border_left": pal["accent"], "shade": pal["quote_bg"],
                                            "indent": int(ctx["ppr"].get("indent") or 0) + 240},
                                  fmt={**ctx["fmt"], "color": pal["muted"]})
            return self.blocks(node, sub)
        if tag == "table":
            return self.table(node, ctx)
        if tag in ("figure",):
            return self.blocks(node, ctx)
        if tag == "figcaption":
            return [self.para("".join(self.inline_children(node, ctx, {**ctx["fmt"], "italic": True, "color": pal["muted"], "size": self.size - 1})),
                              {"align": "center", "style": "MXCaption"})]
        if tag == "details":
            return self.blocks(node, ctx)
        if tag == "summary":
            return [self.para("".join(self.inline_children(node, ctx, {**ctx["fmt"], "bold": True})), {**ctx["ppr"], "keep_next": True})]
        if tag == "iframe":
            title = node.attrs.get("title") or ("Interactive visualization" if self.opt.get("lang") == "en" else "Интерактивная визуализация")
            note = ("(open the HTML export to interact with it)" if self.opt.get("lang") == "en" else "(интерактивна в HTML-экспорте)")
            return [self.para(self.run(f"[{title}] {note}", {"italic": True, "color": pal["muted"]}), {"align": "center"})]
        if tag in ("dl",):
            return self.blocks(node, ctx)
        if tag == "dt":
            return [self.para("".join(self.inline_children(node, ctx, {**ctx["fmt"], "bold": True})), dict(ctx["ppr"]))]
        if tag == "dd":
            return self.blocks(node, self._child_ctx(ctx, ppr={"indent": int(ctx["ppr"].get("indent") or 0) + 360}))
        # div / section / article / header / p and friends
        if "code-block-wrap" in cls:
            pre = node.find("pre")
            label = str(node.attrs.get("data-lang") or "").strip()
            out: List[str] = []
            if label and label not in ("code", "text", "plain") and self.opt.get("code_labels", True):
                out.append(self.para(self.run(label, {"code": True, "size": max(7, self.size - 2.5), "color": pal["muted"]}),
                                     {"style": "MXCodeLabel", "keep_next": True}))
            if pre is not None:
                out.extend(self.code_block(pre, ctx))
            return out
        if "mx-who" in cls:
            role = node.attrs.get("data-role", "assistant")
            color = pal["user_border"] if role == "user" else pal["assistant_border"]
            return [self.para("".join(self.inline_children(node, ctx, {"bold": True, "color": color, "size": self.size - 0.5})),
                              {"style": "MXWho", "keep_next": True, "border_bottom": pal["border"], "spacing": (280, 100)})]
        if "mx-meta" in cls or "mx-stats" in cls:
            sub = self._child_ctx(ctx, ppr={"style": "MXMeta"}, fmt={**ctx["fmt"], "color": pal["muted"], "size": self.size - 1.5})
            return self.blocks(node, sub)
        if "mx-step" in cls:
            sub = self._child_ctx(ctx, ppr={"style": "MXStep", "shade": pal["step_bg"], "border_left": pal["border"],
                                            "indent": int(ctx["ppr"].get("indent") or 0) + 120},
                                  fmt={**ctx["fmt"], "color": pal["muted"], "size": self.size - 1})
            return self.blocks(node, sub)
        if "mx-user" in cls and self.opt.get("user_shade", True):
            sub = self._child_ctx(ctx, ppr={"shade": pal["user_bg"]})
            return self.blocks(node, sub)
        if "md-task" in cls:
            return self.blocks(node, ctx)
        if "table-wrap" in cls:
            return self.blocks(node, ctx)
        if node.attrs.get("data-docx-break") == "page":
            return ['<w:p><w:r><w:br w:type="page"/></w:r></w:p>'] + self.blocks(node, ctx)
        if tag == "p":
            content = "".join(self.inline_children(node, ctx, ctx["fmt"]))
            return [self.para(content, dict(ctx["ppr"]))] if content else []
        return self.blocks(node, ctx)

    def _child_ctx(self, ctx: Dict[str, Any], *, ppr: Optional[Dict[str, Any]] = None,
                   fmt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged = dict(ctx["ppr"])
        merged.update(ppr or {})
        return {**ctx, "ppr": merged, "fmt": fmt if fmt is not None else dict(ctx["fmt"])}

    def list_block(self, node: Node, ctx: Dict[str, Any], ordered: bool) -> List[str]:
        level = min(8, ctx.get("list_level", -1) + 1)
        try:
            start = int(node.attrs.get("start") or 1)
        except ValueError:
            start = 1
        num_id = self.new_list(ordered, start, level)
        marker = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
        continuation = f'<w:ind w:left="{720 + 360 * level}"/>'
        out: List[str] = []
        for item in node.elements():
            if item.tag != "li":
                continue
            sub = {**ctx, "ppr": {**ctx["ppr"], "num": (num_id, level), "style": "ListParagraph"},
                   "fmt": dict(ctx["fmt"]), "list_level": level}
            produced = self.blocks(item, sub) or [self.para("", sub["ppr"])]
            out.append(produced[0])
            # Only the first paragraph of an item carries the bullet; the rest are indented continuations
            # (nested lists keep their own numbering).
            for extra in produced[1:]:
                if marker in extra:
                    extra = extra.replace(marker, continuation, 1)
                out.append(extra)
        return out

    def code_block(self, pre: Node, ctx: Dict[str, Any]) -> List[str]:
        pal = self.pal
        runs: List[str] = []
        base = {"code": True, "color": pal["code_text"], "size": max(7, self.size - 1.5)}
        self.code_runs(pre, base, runs)
        content = "".join(runs) or self.run(" ", base)
        return [self.para(content, {"style": "MXCode", "shade": pal["code_bg"], "border_box": pal["border"],
                                    "indent": int(ctx["ppr"].get("indent") or 0) + 60, "indent_right": 60,
                                    "spacing": (60, 160)})]

    def code_runs(self, node: Node, fmt: Dict[str, Any], out: List[str]) -> None:
        for child in node.children:
            if isinstance(child, str):
                out.append(self.run(child, fmt))
                continue
            if child.tag in ("button",) or child.classes & SKIP_CLASSES:
                continue
            sub = dict(fmt)
            for name in child.classes:
                if name.startswith("tok-"):
                    token = name[4:]
                    if token in self.pal["tokens"]:
                        sub["color"] = self.pal["tokens"][token]
                    if token == "com":
                        sub["italic"] = True
                    if token == "strong":
                        sub["bold"] = True
            if child.tag == "br":
                out.append("<w:r><w:br/></w:r>")
                continue
            self.code_runs(child, sub, out)

    def table(self, node: Node, ctx: Dict[str, Any]) -> List[str]:
        pal = self.pal
        rows: List[Tuple[Node, bool]] = []
        for part in node.elements():
            if part.tag in ("thead", "tbody", "tfoot"):
                for row in part.elements():
                    if row.tag == "tr":
                        rows.append((row, part.tag == "thead"))
            elif part.tag == "tr":
                rows.append((part, all(cell.tag == "th" for cell in part.elements())))
        if not rows:
            return []
        width = max(sum(max(1, self._int(cell.attrs.get("colspan"), 1)) for cell in row.elements() if cell.tag in ("td", "th"))
                    for row, _ in rows)
        total = int((self.page_mm[0] - 2 * self.margin_mm) * TWIPS_PER_MM)
        col = max(400, total // max(1, width))
        grid = "".join(f'<w:gridCol w:w="{col}"/>' for _ in range(width))
        border = pal["border"]
        borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="{border}"/>'
                          for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
        xml = [f'<w:tbl><w:tblPr><w:tblStyle w:val="MXTable"/><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders>'
               '<w:tblLayout w:type="autofit"/><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
               '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
               '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
               f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>"]
        for index, (row, head) in enumerate(rows):
            cells = [cell for cell in row.elements() if cell.tag in ("td", "th")]
            trpr = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if head else "<w:trPr><w:cantSplit/></w:trPr>"
            parts = [f"<w:tr>{trpr}"]
            for cell in cells:
                span = max(1, self._int(cell.attrs.get("colspan"), 1))
                is_head = head or cell.tag == "th"
                fill = pal["table_head_bg"] if is_head else ("" if index % 2 or not self.opt.get("zebra") else pal["code_bg"])
                tcpr = f'<w:tcW w:w="{col * span}" w:type="dxa"/>' + (f'<w:gridSpan w:val="{span}"/>' if span > 1 else "")
                if fill:
                    tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
                align = self._align(cell)
                sub = {**ctx, "ppr": {"spacing": (20, 20), **({"align": align} if align else {})},
                       "fmt": {**ctx["fmt"], **({"bold": True} if is_head else {})}}
                inner = self.blocks(cell, sub) or [self.para("", sub["ppr"])]
                parts.append(f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{''.join(inner)}</w:tc>")
            missing = width - sum(max(1, self._int(c.attrs.get("colspan"), 1)) for c in cells)
            for _ in range(max(0, missing)):
                parts.append(f'<w:tc><w:tcPr><w:tcW w:w="{col}" w:type="dxa"/></w:tcPr><w:p/></w:tc>')
            parts.append("</w:tr>")
            xml.append("".join(parts))
        xml.append("</w:tbl>")
        # An empty paragraph keeps two adjacent tables apart and gives the table air.
        return ["".join(xml), self.para("", {"spacing": (0, 80)})]

    @staticmethod
    def _int(value: Any, default: int) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _align(node: Node) -> str:
        style = str(node.attrs.get("style") or "").lower()
        align = node.attrs.get("align", "")
        match = re.search(r"text-align\s*:\s*(left|right|center|justify)", style)
        value = (match.group(1) if match else align).lower()
        return {"left": "left", "right": "right", "center": "center", "justify": "both"}.get(value, "")

    # ---- inline
    def inline_children(self, node: Node, ctx: Dict[str, Any], fmt: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        first = True
        for child in node.children:
            if isinstance(child, str):
                text = re.sub(r"\s+", " ", child)
                if first:
                    text = text.lstrip()
                if text:
                    out.append(self.run(text, fmt))
                    first = False
                continue
            if self._skip(child):
                continue
            produced = self.inline(child, ctx, fmt)
            if produced:
                first = False
            out.extend(produced)
        return out

    def inline(self, node: Node, ctx: Dict[str, Any], fmt: Dict[str, Any]) -> List[str]:
        tag = node.tag
        pal = self.pal
        if self._skip(node):
            return []
        if tag == "br":
            return ["<w:r><w:br/></w:r>"]
        if tag == "span" and node.has_class("katex"):
            return [self.math_xml(node, node.find("math"), display=False)]
        if tag == "img":
            return [self.image(node)]
        if tag in ("strong", "b"):
            return self.inline_children(node, ctx, {**fmt, "bold": True})
        if tag in ("em", "i", "cite", "dfn"):
            return self.inline_children(node, ctx, {**fmt, "italic": True})
        if tag in ("del", "s", "strike"):
            return self.inline_children(node, ctx, {**fmt, "strike": True})
        if tag in ("u", "ins"):
            return self.inline_children(node, ctx, {**fmt, "underline": True})
        if tag == "sup":
            return self.inline_children(node, ctx, {**fmt, "vert": "superscript"})
        if tag == "sub":
            return self.inline_children(node, ctx, {**fmt, "vert": "subscript"})
        if tag == "mark":
            return self.inline_children(node, ctx, {**fmt, "shade": "FFF3B0"})
        if tag in ("code", "kbd", "samp", "tt"):
            return self.inline_children(node, ctx, {**fmt, "code": True, "shade": pal["code_bg"], "color": pal["code_text"]})
        if tag == "a":
            href = str(node.attrs.get("href") or "")
            inner = self.inline_children(node, ctx, {**fmt, "color": pal["link"], "underline": True})
            if re.match(r"^(https?|mailto):", href, re.I):
                rid = self.rel("http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", href, external=True)
                return [f'<w:hyperlink r:id="{rid}" w:history="1">{"".join(inner)}</w:hyperlink>']
            return inner
        if tag == "span" and node.has_class("md-check"):
            return [self.run(node.text().strip() + " ", fmt)]
        sub = dict(fmt)
        for name in node.classes:
            if name.startswith("tok-") and name[4:] in pal["tokens"]:
                sub["color"] = pal["tokens"][name[4:]]
        if self._is_block(node):
            # A block inside an inline context (rare markup): keep its text.
            return self.inline_children(node, ctx, sub)
        return self.inline_children(node, ctx, sub)

    def math_xml(self, wrapper: Node, math: Optional[Node], *, display: bool) -> str:
        color = self.pal["text"]
        if math is not None and self.opt.get("math", True):
            try:
                body = mathml_to_omml(math, color=color)
                if body:
                    xml = f"<m:oMath>{body}</m:oMath>"
                    return f"<m:oMathPara>{xml}</m:oMathPara>" if display else xml
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"math: {type(exc).__name__}")
        tex = latex_of(wrapper) or wrapper.text()
        return self.run(tex, {"code": True, "color": self.pal["muted"]})

    def image(self, node: Node) -> str:
        src = str(node.attrs.get("src") or "")
        alt = str(node.attrs.get("alt") or "")
        if not self.opt.get("images", True):
            return self.run(f"[{alt or 'image'}]", {"italic": True, "color": self.pal["muted"]})
        try:
            mime, data = decode_data_url(src)
            if mime == "image/svg+xml":
                raise ValueError("svg")
            rid, width, height = self.add_image(data, mime)
        except (ValueError, binascii.Error):
            label = alt or ("image" if self.opt.get("lang") == "en" else "изображение")
            return self.run(f"[{label}]", {"italic": True, "color": self.pal["muted"]})
        try:
            css_width = float(node.attrs.get("data-width") or node.attrs.get("width") or 0)
        except ValueError:
            css_width = 0.0
        try:
            scale = max(1.0, min(4.0, float(node.attrs.get("data-scale") or 1)))
        except ValueError:
            scale = 1.0
        if not css_width and scale > 1 and width:
            css_width = width / scale
        if css_width and width:
            height = int(height * css_width / width)
            width = int(css_width)
        return self.drawing(rid, width, height, alt=alt)

    # ---- parts
    def styles_xml(self) -> str:
        pal, font, mono, size = self.pal, xml_attr(self.font), xml_attr(self.mono), self.size
        half = size * 2

        def style(sid: str, name: str, ppr: str = "", rpr: str = "", based: str = "Normal", kind: str = "paragraph",
                  next_: str = "") -> str:
            return (f'<w:style w:type="{kind}" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
                    + (f'<w:basedOn w:val="{based}"/>' if based else "") + (f'<w:next w:val="{next_}"/>' if next_ else "")
                    + '<w:qFormat/>' + (f"<w:pPr>{ppr}</w:pPr>" if ppr else "") + (f"<w:rPr>{rpr}</w:rPr>" if rpr else "") + "</w:style>")

        heading_sizes = [size + 9, size + 6, size + 3.5, size + 2, size + 1, size]
        headings = "".join(
            f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:basedOn w:val="Normal"/>'
            f'<w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>'
            f'<w:spacing w:before="{280 if i < 3 else 200}" w:after="{100}"/><w:outlineLvl w:val="{i - 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:bCs/><w:color w:val="{pal["text"] if i > 1 else pal["accent"]}"/>'
            f'<w:sz w:val="{int(heading_sizes[i - 1] * 2)}"/><w:szCs w:val="{int(heading_sizes[i - 1] * 2)}"/></w:rPr></w:style>'
            for i in range(1, 7))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr>'
                f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}" w:eastAsia="{font}"/>'
                f'<w:color w:val="{pal["text"]}"/><w:sz w:val="{half}"/><w:szCs w:val="{half}"/><w:lang w:val="{"en-US" if self.opt.get("lang") == "en" else "ru-RU"}"/>'
                '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
                '</w:pPrDefault></w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>'
                + style("Title", "Title", '<w:spacing w:before="0" w:after="80"/>',
                        f'<w:b/><w:color w:val="{pal["text"]}"/><w:sz w:val="{int((size + 13) * 2)}"/><w:szCs w:val="{int((size + 13) * 2)}"/>')
                + headings
                + style("ListParagraph", "List Paragraph", '<w:spacing w:after="40"/><w:ind w:left="720"/><w:contextualSpacing/>')
                + style("MXWho", "MemuAI Role", '<w:keepNext/>', f'<w:b/><w:color w:val="{pal["accent"]}"/>')
                + style("MXCode", "MemuAI Code", '<w:spacing w:before="60" w:after="160" w:line="240" w:lineRule="auto"/>',
                        f'<w:rFonts w:ascii="{mono}" w:hAnsi="{mono}" w:cs="{mono}" w:eastAsia="{mono}"/><w:noProof/>')
                + style("MXCodeLabel", "MemuAI Code Label", '<w:keepNext/><w:spacing w:before="120" w:after="0"/>')
                + style("MXQuote", "MemuAI Quote", '<w:spacing w:after="80"/>', '<w:i/>')
                + style("MXStep", "MemuAI Step", '<w:spacing w:after="60"/>')
                + style("MXMeta", "MemuAI Meta", '<w:spacing w:after="40"/>')
                + style("MXCaption", "MemuAI Caption", '<w:spacing w:after="160"/>', "<w:i/>")
                + '<w:style w:type="table" w:customStyle="1" w:styleId="MXTable"><w:name w:val="MemuAI Table"/>'
                  '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/></w:tblPr></w:style>'
                + '</w:styles>')

    def numbering_xml(self) -> str:
        bullets = ["•", "◦", "▪", "•", "◦", "▪", "•", "◦", "▪"]
        fmts = ["decimal", "lowerLetter", "lowerRoman", "decimal", "lowerLetter", "lowerRoman", "decimal", "lowerLetter", "lowerRoman"]

        def lvl(i: int, ordered: bool) -> str:
            indent = 720 + 360 * i
            if ordered:
                return (f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmts[i]}"/>'
                        f'<w:lvlText w:val="%{i + 1}."/><w:lvlJc w:val="left"/>'
                        f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr></w:lvl>')
            return (f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                    f'<w:lvlText w:val="{bullets[i]}"/><w:lvlJc w:val="left"/>'
                    f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr>'
                    f'<w:rPr><w:color w:val="{self.pal["accent"]}"/></w:rPr></w:lvl>')

        abstract = (f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{"".join(lvl(i, False) for i in range(9))}</w:abstractNum>'
                    f'<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="hybridMultilevel"/>{"".join(lvl(i, True) for i in range(9))}</w:abstractNum>')
        nums = '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>' + "".join(self.nums)
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {NS}>{abstract}{nums}</w:numbering>'

    def header_xml(self, text: str) -> str:
        pal = self.pal
        content = self.run(text, {"color": pal["muted"], "size": max(7, self.size - 2.5)}) if text else ""
        para = self.para(content, {"align": "right", "border_bottom": pal["border"] if text else "", "spacing": (0, 120)})
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {NS}>{para}</w:hdr>'

    def footer_xml(self, text: str, numbers: bool) -> str:
        pal = self.pal
        small = {"color": pal["muted"], "size": max(7, self.size - 2.5)}
        runs = self.run(text, small) if text else ""
        if numbers:
            en = self.opt.get("lang") == "en"
            sep = "   ·   " if text else ""
            half = int(max(7, self.size - 2.5) * 2)
            rpr = f'<w:rPr><w:color w:val="{pal["muted"]}"/><w:sz w:val="{half}"/></w:rPr>'
            runs += self.run(sep + ("Page " if en else "Стр. "), small)
            runs += f'<w:fldSimple w:instr=" PAGE "><w:r>{rpr}<w:t>1</w:t></w:r></w:fldSimple>'
            runs += self.run(" of " if en else " из ", small)
            runs += f'<w:fldSimple w:instr=" NUMPAGES "><w:r>{rpr}<w:t>1</w:t></w:r></w:fldSimple>'
        para = self.para(runs, {"align": "center"})
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {NS}>{para}</w:ftr>'

    def build(self, body_html: str) -> bytes:
        root = parse_html(body_html)
        body = self.convert(root)
        opt = self.opt
        header_text = str(opt.get("header_text") or "")[:200]
        footer_text = str(opt.get("footer_text") or "")[:200]
        numbers = bool(opt.get("page_numbers", True))
        hdr_id = self.rel("http://schemas.openxmlformats.org/officeDocument/2006/relationships/header", "header1.xml") \
            if header_text else ""
        ftr_id = self.rel("http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer", "footer1.xml") \
            if (footer_text or numbers) else ""
        width_tw = int(self.page_mm[0] * TWIPS_PER_MM)
        height_tw = int(self.page_mm[1] * TWIPS_PER_MM)
        margin_tw = int(self.margin_mm * TWIPS_PER_MM)
        orient = ' w:orient="landscape"' if opt.get("orientation") == "landscape" else ""
        sect = ("<w:sectPr>" + (f'<w:headerReference w:type="default" r:id="{hdr_id}"/>' if hdr_id else "")
                + (f'<w:footerReference w:type="default" r:id="{ftr_id}"/>' if ftr_id else "")
                + f'<w:pgSz w:w="{width_tw}" w:h="{height_tw}"{orient}/>'
                f'<w:pgMar w:top="{margin_tw}" w:right="{margin_tw}" w:bottom="{margin_tw}" w:left="{margin_tw}" '
                f'w:header="{max(360, margin_tw // 2)}" w:footer="{max(360, margin_tw // 2)}" w:gutter="0"/>'
                '<w:cols w:space="708"/></w:sectPr>')
        background = self.pal["bg"]
        dark_page = background not in ("FFFFFF", "FFFFFE") and bool(opt.get("page_background", True))
        bg_xml = f'<w:background w:color="{background}"/>' if dark_page else ""
        document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}>{bg_xml}<w:body>'
                    f"{body}{sect}</w:body></w:document>")
        settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:settings {NS}>' + ("<w:displayBackgroundShape/>" if dark_page else "")
                    + '<w:defaultTabStop w:val="708"/><w:characterSpacingControl w:val="doNotCompress"/>'
                    '<m:mathPr><m:mathFont m:val="Cambria Math"/><m:dispDef/></m:mathPr><w:compat>'
                    '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
                    '</w:compat></w:settings>')
        title = str(opt.get("title") or "MemuAI")[:250]
        created = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f"<dc:title>{xml_text(title)}</dc:title><dc:creator>MemuAI</dc:creator><cp:lastModifiedBy>MemuAI</cp:lastModifiedBy>"
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified></cp:coreProperties>')
        app_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                   'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                   "<Application>MemuAI</Application></Properties>")
        defaults = "".join(f'<Default Extension="{ext}" ContentType="image/{ext}"/>' for ext in sorted(self.content_types))
        overrides = ('<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                     '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                     + ('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>' if hdr_id else "")
                     + ('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>' if ftr_id else ""))
        content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         f"{defaults}{overrides}</Types>")
        root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                     '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                     "</Relationships>")
        doc_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>']
        for rid, rtype, target, mode in self.rels:
            doc_rels.append(f'<Relationship Id="{rid}" Type="{rtype}" Target="{xml_attr(target)}"'
                            + (f' TargetMode="{mode}"' if mode else "") + "/>")
        doc_rels.append("</Relationships>")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("word/document.xml", document)
            archive.writestr("word/styles.xml", self.styles_xml())
            archive.writestr("word/numbering.xml", self.numbering_xml())
            archive.writestr("word/settings.xml", settings)
            archive.writestr("word/_rels/document.xml.rels", "".join(doc_rels))
            if hdr_id:
                archive.writestr("word/header1.xml", self.header_xml(header_text))
            if ftr_id:
                archive.writestr("word/footer1.xml", self.footer_xml(footer_text, numbers))
            archive.writestr("docProps/core.xml", core)
            archive.writestr("docProps/app.xml", app_xml)
            for name, data in self.media:
                archive.writestr(f"word/media/{name}", data, compress_type=zipfile.ZIP_STORED)
        return buffer.getvalue()


def html_to_docx(body_html: str, options: Optional[Dict[str, Any]] = None) -> bytes:
    if len(body_html or "") > MAX_HTML_CHARS:
        raise ValueError("document too large")
    return DocxBuilder(dict(options or {})).build(body_html)


# --------------------------------------------------------------------------- HTML document helpers

def finalize_html(document: str, *, embed_katex: bool) -> str:
    """Insert the KaTeX stylesheet (fonts embedded) where the window left its placeholder."""
    marker = "/*MX-KATEX-CSS*/"
    if marker not in document:
        return document
    return document.replace(marker, katex_inline_css() if embed_katex else "", 1)
