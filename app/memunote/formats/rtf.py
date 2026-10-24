"""Rich Text Format (.rtf) reader and writer in pure Python.

The reader is a real tokenizer (groups, control words, ``\\'hh`` bytes in the
document code page, ``\\uN`` with ``\\ucN`` fallback skipping), not a regex
stripper: it keeps paragraphs, alignment, headings from the style sheet /
``\\outlinelevel``, bold/italic/underline/strike, colours, highlight, sizes,
fonts, super/subscript, hyperlinks (``HYPERLINK`` fields), simple tables
(``\\trowd … \\cell … \\row``), lists (``\\ls``/``\\ilvl`` with the list text
deciding bullets vs numbers) and PNG/JPEG pictures (``\\pict``).
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Dict, List, Optional, Tuple

from .docx import _image_size
from .richhtml import Block, Cell, Para, Rule, Run, Table, safe_link

_TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([\r\n]+)|([^\\{}\r\n]+)", re.S)

# Destinations whose text is never shown.
_SKIP_DEST = {"fonttbl", "colortbl", "stylesheet", "info", "header", "footer", "headerl", "headerr", "headerf",
              "footerl", "footerr", "footerf", "footnote", "annotation", "xmlnstbl", "listtable",
              "listoverridetable", "rsidtbl", "generator", "themedata", "colorschememapping", "latentstyles",
              "datastore", "mmathPr", "pgdsctbl", "filetbl", "revtbl", "object", "fldinst", "bkmkstart",
              "bkmkend", "nonshppict", "shp", "shpinst", "atnid", "atnauthor", "comment", "private", "pntext",
              "pntxta", "pntxtb", "template", "userprops", "docvar", "wgrffmtfilter", "xmlopen", "xmlclose"}

# Destinations whose pictures are not body content either (page headers/footers, notes, comments).
_HIDDEN_DEST = {"header", "footer", "headerl", "headerr", "headerf", "footerl", "footerr", "footerf", "footnote",
                "annotation", "comment", "nonshppict"}

_CHARSETS = {0: "cp1252", 1: "cp1252", 2: "cp1252", 77: "mac-roman", 128: "cp932", 129: "cp949", 134: "gb2312",
             136: "big5", 161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255", 178: "cp1256",
             186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250", 255: "cp437"}


class _State:
    __slots__ = ("run", "skip", "uc", "dest", "font_cp", "pict", "field_inst", "pict_sub")

    def __init__(self) -> None:
        self.run = Run()
        self.skip = False
        self.uc = 1
        self.dest = ""
        self.font_cp = ""
        self.pict: Optional[dict] = None
        self.field_inst = False
        self.pict_sub = False      # a group nested inside \pict ({\*\picprop …}): its text is not picture data

    def copy(self) -> "_State":
        new = _State()
        new.run = Run(**{**self.run.__dict__, "text": ""})
        new.skip, new.uc, new.dest, new.font_cp = self.skip, self.uc, self.dest, self.font_cp
        new.pict = self.pict
        new.pict_sub = self.pict is not None
        new.field_inst = self.field_inst
        return new


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.codepage = "cp1252"
        self.fonts: Dict[int, Tuple[str, str]] = {}
        self.colors: List[str] = []
        self.styles: Dict[int, str] = {}
        self.blocks: List[Block] = []
        self.para = Para()
        self.pending = bytearray()
        self.pending_cp = ""
        self.skip_chars = 0
        self.table: Optional[Table] = None
        self.row: List[Cell] = []
        self.cell: List[Para] = []
        self.in_table = False
        self.list_text = ""
        self.in_list_text = False
        self.field_url = ""
        self.list_id = 0
        self.ilvl = 0

    # ------------------------------------------------------------ text plumbing
    def _flush_bytes(self, state: _State) -> None:
        if self.pending:
            try:
                text = bytes(self.pending).decode(self.pending_cp or self.codepage, errors="replace")
            except LookupError:
                text = bytes(self.pending).decode("cp1252", errors="replace")
            self.pending.clear()
            self._text(text, state)

    def _text(self, text: str, state: _State) -> None:
        if not text:
            return
        if self.in_list_text:
            self.list_text += text
            return
        run = Run(**{**state.run.__dict__, "text": text})
        if self.field_url and not run.link:
            run.link = self.field_url
        para = self.para
        if para.runs and not para.runs[-1].br and not para.runs[-1].image and para.runs[-1].same_format(run):
            para.runs[-1].text += text
        else:
            para.runs.append(run)

    def _end_para(self, force: bool = False) -> None:
        para = self.para
        if self.list_text.strip():
            marker = self.list_text.strip()
            para.kind = "li"
            para.list = "ol" if re.match(r"^[\(\[]?[0-9a-zA-ZivxIVX]{1,6}[.)\]]", marker) else "ul"
            para.level = self.ilvl
        elif self.list_id:
            para.kind, para.list, para.level = "li", "ul", self.ilvl
        self.list_text = ""
        if self.in_table:
            self.cell.append(para)
        else:
            self.blocks.append(para)
        # Paragraph properties persist until \pard.
        self.para = Para(kind=para.kind if para.kind != "li" else "p", align=para.align)

    # ------------------------------------------------------------ parsing
    def parse(self) -> List[Block]:
        stack: List[_State] = []
        state = _State()
        dest_started = False
        for match in _TOKEN.finditer(self.data):
            word, param, hexbyte, symbol, brace, newline, text = match.groups()
            if brace:
                self._flush_bytes(state)
                if brace == b"{":
                    if state.dest in ("stylesheet", "stylesheet_entry"):
                        self._style_index = None
                    elif state.dest in ("fonttbl", "fonttbl_entry"):
                        self._font_index = None
                    stack.append(state)
                    state = state.copy()
                    dest_started = True
                    continue
                closing = state
                if (closing.pict is not None and stack and stack[-1].pict is not closing.pict
                        and not any(item.dest in _HIDDEN_DEST for item in stack)):
                    self._picture(closing.pict)   # (a logo in a page header is not body content)
                if closing.dest == "listtext_marker":
                    self.in_list_text = False
                if closing.dest == "fldrslt":
                    self.field_url = ""
                state = stack.pop() if stack else _State()
                continue
            if newline:
                continue
            if self.skip_chars and (text or hexbyte):
                if text:
                    consumed = min(self.skip_chars, len(text))
                    self.skip_chars -= consumed
                    text = text[consumed:]
                    if not text:
                        continue
                else:
                    self.skip_chars -= 1
                    continue
            if hexbyte:
                if state.skip and state.pict is None:
                    continue
                if state.pict is not None:
                    if not state.pict_sub:
                        state.pict["hex"].append(hexbyte)
                    continue
                if self.pending_cp != (state.font_cp or self.codepage):
                    self._flush_bytes(state)
                    self.pending_cp = state.font_cp or self.codepage
                self.pending.append(int(hexbyte, 16))
                continue
            if text:
                if state.pict is not None:
                    if not state.pict_sub:
                        state.pict["hex"].append(text)
                    continue
                if state.dest == "fldinst_text":
                    found = re.search(rb'HYPERLINK\s+"([^"]+)"', text)
                    if found:
                        self.field_url = safe_link(found.group(1).decode("latin-1"))
                    continue
                if state.skip:
                    if state.dest == "fonttbl_entry":
                        self._font_name(state, text)
                    elif state.dest == "colortbl":
                        self._colors(text)
                    elif state.dest == "stylesheet_entry":
                        self._style_name(state, text)
                    continue
                self._flush_bytes(state)
                self._text(text.decode(state.font_cp or self.codepage, errors="replace"), state)
                continue
            if symbol:
                dest_started = False
                self._symbol(symbol, state)
                continue
            if word:
                self._flush_bytes(state)
                name = word.decode("ascii")
                value = int(param) if param is not None else None
                first = dest_started
                dest_started = False
                self._control(name, value, state, first, stack)
        self._flush_bytes(state)
        if self.para.runs:
            self._end_para()
        if self.table and self.table.rows:
            self.blocks.append(self.table)
        return self.blocks

    def _symbol(self, symbol: bytes, state: _State) -> None:
        if symbol == b"*":
            state.skip = True
            state.dest = "unknown"
            self._after_star = True
            return
        if state.skip:
            return
        char = {b"~": "\u00a0", b"-": "\u00ad", b"_": "\u2011", b"\\": "\\", b"{": "{", b"}": "}"}.get(symbol)
        if char:
            self._text(char, state)

    def _font_name(self, state: _State, text: bytes) -> None:
        index = getattr(self, "_font_index", None)
        if index is None:
            return
        name, cp = self.fonts.get(index, ("", ""))
        name = (name + text.decode("latin-1")).rstrip(";").strip()
        self.fonts[index] = (name, cp)

    def _colors(self, text: bytes) -> None:
        for _ in range(text.count(b";")):
            color = getattr(self, "_color", None)
            self.colors.append("#%02x%02x%02x" % color if color else "")
            self._color = None

    def _style_name(self, state: _State, text: bytes) -> None:
        index = getattr(self, "_style_index", None)
        if index is not None:
            self.styles[index] = (self.styles.get(index, "") + text.decode("latin-1")).rstrip(";").strip().lower()

    def _picture(self, pict: dict) -> None:
        raw = b"".join(pict["hex"])
        raw = re.sub(rb"[^0-9a-fA-F]", b"", raw)
        if len(raw) % 2:
            raw = raw[:-1]
        try:
            data = binascii.unhexlify(raw)
        except binascii.Error:
            return
        mime = {"png": "image/png", "jpeg": "image/jpeg"}.get(pict.get("kind", ""))
        if not mime or not data:
            return
        width = int(pict.get("goalw", 0) / 15) if pict.get("goalw") else 0
        height = int(pict.get("goalh", 0) / 15) if pict.get("goalh") else 0
        if pict.get("scalex"):
            width = int(width * pict["scalex"] / 100)
        if pict.get("scaley"):
            height = int(height * pict["scaley"] / 100)
        self.para.runs.append(Run(image=f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
                                  width=width, height=height))

    def _control(self, name: str, value: Optional[int], state: _State, first: bool, stack: List[_State]) -> None:
        run = state.run
        after_star = getattr(self, "_after_star", False)
        self._after_star = False
        # destinations
        if (first and name in _SKIP_DEST) or after_star:
            state.skip = True
            state.dest = {"fonttbl": "fonttbl", "colortbl": "colortbl", "stylesheet": "stylesheet",
                          "fldinst": "fldinst_text", "listtext": "listtext_marker"}.get(name, name)
            if name == "colortbl":
                self._color = None
            if name == "fldinst":
                state.skip = False
                state.dest = "fldinst_text"
            if name == "pntext":
                state.skip = False
                state.dest = "listtext_marker"
                self.in_list_text = True
            return
        if first and name == "listtext":
            state.dest = "listtext_marker"
            self.in_list_text = True
            return
        if first and name == "fldrslt":
            state.dest = "fldrslt"
            return
        if (first or after_star) and name == "pict" and state.dest != "nonshppict":
            state.pict = {"hex": [], "kind": ""}
            state.skip = True
            return
        if state.pict is not None:
            if name == "pngblip":
                state.pict["kind"] = "png"
            elif name == "jpegblip":
                state.pict["kind"] = "jpeg"
            elif name in ("picwgoal", "pichgoal"):
                state.pict["goalw" if name == "picwgoal" else "goalh"] = value or 0
            elif name in ("picscalex", "picscaley"):
                state.pict["scalex" if name == "picscalex" else "scaley"] = value or 100
            elif name == "bin" and value:
                self.skip_chars = value
            return
        # tables of the header
        if state.dest == "fonttbl" or state.dest == "fonttbl_entry":
            if name == "f":
                self._font_index = value or 0
                state.dest = "fonttbl_entry"
                self.fonts.setdefault(self._font_index, ("", ""))
            elif name == "fcharset" and getattr(self, "_font_index", None) is not None:
                font = self.fonts.get(self._font_index, ("", ""))
                self.fonts[self._font_index] = (font[0], _CHARSETS.get(value or 0, ""))
            return
        if state.dest == "colortbl":
            color = list(getattr(self, "_color", None) or (0, 0, 0))
            if name in ("red", "green", "blue"):
                color[("red", "green", "blue").index(name)] = max(0, min(255, value or 0))
                self._color = tuple(color)
            return
        if state.dest in ("stylesheet", "stylesheet_entry"):
            if name == "s":
                self._style_index = value or 0
                state.dest = "stylesheet_entry"
            return
        if state.skip:
            return
        if name == "ansicpg" and value:
            self.codepage = f"cp{value}"
            try:
                "".encode(self.codepage)
            except LookupError:
                self.codepage = "cp1252"
        elif name == "uc":
            state.uc = value if value is not None else 1
        elif name == "u":
            code = value if value is not None else 0
            if code < 0:
                code += 65536
            self._text(chr(code), state)
            self.skip_chars = state.uc
        elif name == "par":
            self._end_para()
        elif name == "sect" or name == "page":
            self._end_para()
            if name == "page" and not self.in_table:
                self.blocks.append(Rule(page_break=True))
        elif name == "line":
            self.para.runs.append(Run(br=True))
        elif name == "tab":
            self._text("\t", state)
        elif name in ("emdash", "endash", "bullet", "lquote", "rquote", "ldblquote", "rdblquote", "emspace", "enspace"):
            self._text({"emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022", "lquote": "\u2018",
                        "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d", "emspace": "\u2003",
                        "enspace": "\u2002"}[name], state)
        elif name == "pard":
            self.para.align = ""
            self.para.kind = "p"
            self.list_id = 0
            self.ilvl = 0
            if not self.in_table_row_open():
                self.in_table = False
        elif name == "plain":
            state.run = Run(link=run.link)
            state.font_cp = ""
        elif name in ("b", "i", "strike", "striked"):
            on = value != 0
            setattr(run, {"b": "bold", "i": "italic", "strike": "strike", "striked": "strike"}[name], on)
        elif name in ("ul", "uld", "uldb", "ulw", "ulwave", "uldash", "ulth"):
            run.underline = value != 0
        elif name == "ulnone":
            run.underline = False
        elif name == "super":
            run.sup, run.sub = True, False
        elif name == "sub":
            run.sub, run.sup = True, False
        elif name == "nosupersub":
            run.sup = run.sub = False
        elif name == "fs" and value:
            run.size = value / 2
        elif name == "f" and value is not None:
            font, cp = self.fonts.get(value, ("", ""))
            run.font = font or run.font
            state.font_cp = cp
        elif name == "cf":
            run.color = self.colors[value] if value is not None and 0 < value < len(self.colors) else ""
        elif name in ("highlight", "cb", "chcbpat"):
            run.highlight = self.colors[value] if value is not None and 0 < value < len(self.colors) else ""
        elif name in ("qc", "qr", "qj", "ql"):
            self.para.align = {"qc": "center", "qr": "right", "qj": "justify", "ql": ""}[name]
        elif name == "s" and value is not None:
            style = self.styles.get(value, "")
            match = re.match(r"heading\s*(\d)", style)
            if match:
                self.para.kind = "h" + str(min(6, int(match.group(1))))
            elif style == "title":
                self.para.kind = "h1"
            elif "quote" in style:
                self.para.kind = "blockquote"
            elif style in ("code", "html preformatted", "plain text"):
                self.para.kind = "pre"
        elif name == "outlinelevel" and value is not None and value < 6:
            self.para.kind = "h" + str(value + 1)
        elif name == "ls":
            self.list_id = value or 1
        elif name == "ilvl":
            self.ilvl = max(0, min(8, value or 0))
        elif name == "trowd":
            if self.table is None:
                self.table = Table()
            self._row_open = True
        elif name == "intbl":
            self.in_table = True
        elif name == "cell":
            self._flush_bytes(state)
            if self.para.runs or not self.cell:
                self._end_para()
            self.row.append(Cell(blocks=self.cell))
            self.cell = []
        elif name == "row":
            if self.table is None:
                self.table = Table()
            if self.row:
                self.table.rows.append(self.row)
            self.row = []
            self.in_table = False
            self._row_open = False

    def in_table_row_open(self) -> bool:
        return bool(getattr(self, "_row_open", False))


def read_rtf(data: bytes) -> Tuple[List[Block], dict]:
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("not an RTF document")
    from .richhtml import tidy
    reader = _TableAwareReader(data)
    return tidy(reader.parse()), {}


class _TableAwareReader(_Reader):
    """Places each table in the block flow at the paragraph where it ends."""

    def _end_para(self, force: bool = False) -> None:
        if not self.in_table and self.table is not None and self.table.rows:
            self.blocks.append(self.table)
            self.table = None
        super()._end_para(force)

    def parse(self) -> List[Block]:
        blocks = super().parse()
        # drop the empty trailing paragraph RTF writers add
        while blocks and isinstance(blocks[-1], Para) and not blocks[-1].runs:
            blocks.pop()
        return blocks


# ======================================================================================= writer

def _escape(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if char in "\\{}":
            out.append("\\" + char)
        elif char == "\t":
            out.append("\\tab ")
        elif char == "\n":
            out.append("\\line ")
        elif code < 128:
            out.append(char)
        elif code < 65536:
            signed = code if code < 32768 else code - 65536
            out.append(f"\\u{signed}?")
        else:
            value = code - 0x10000
            for unit in (0xD800 + (value >> 10), 0xDC00 + (value & 0x3FF)):
                out.append(f"\\u{unit - 65536}?")
    return "".join(out)


class _Writer:
    def __init__(self) -> None:
        self.fonts = ["Calibri", "Consolas"]
        self.colors: List[str] = []

    def font(self, name: str) -> int:
        if name not in self.fonts:
            self.fonts.append(name)
        return self.fonts.index(name)

    def color(self, value: str) -> int:
        if value not in self.colors:
            self.colors.append(value)
        return self.colors.index(value) + 1

    def run(self, run: Run) -> str:
        if run.br:
            return "\\line "
        if run.image:
            match = re.match(r"data:image/(png|jpe?g);base64,(.*)$", run.image, re.S)
            if not match:
                return ""
            try:
                data = base64.b64decode(match.group(2))
            except (ValueError, TypeError):
                return ""
            width, height = run.width, run.height
            if not (width and height):
                width, height = _image_size(data) or (320, 240)
            kind = "\\pngblip" if match.group(1) == "png" else "\\jpegblip"
            return (f"{{\\pict{kind}\\picwgoal{width * 15}\\pichgoal{height * 15} "
                    + binascii.hexlify(data).decode("ascii") + "}")
        codes = []
        if run.bold:
            codes.append("\\b")
        if run.italic:
            codes.append("\\i")
        if run.underline:
            codes.append("\\ul")
        if run.strike:
            codes.append("\\strike")
        if run.sup:
            codes.append("\\super")
        elif run.sub:
            codes.append("\\sub")
        if run.code:
            codes.append(f"\\f{self.font('Consolas')}")
        elif run.font:
            codes.append(f"\\f{self.font(run.font)}")
        if run.size:
            codes.append(f"\\fs{int(round(run.size * 2))}")
        if run.color:
            codes.append(f"\\cf{self.color(run.color)}")
        if run.highlight:
            codes.append(f"\\highlight{self.color(run.highlight)}")
        body = "{" + "".join(codes) + (" " if codes else "") + _escape(run.text) + "}"
        if run.link:
            return ('{\\field{\\*\\fldinst{HYPERLINK "' + run.link.replace('"', "%22").replace("\\", "/")
                    + '"}}{\\fldrslt{\\ul\\cf' + str(self.color("#0563c1")) + " " + body + "}}}")
        return body

    def para(self, para: Para, in_table: bool = False) -> str:
        codes = ["\\pard\\plain"]
        if in_table:
            codes.append("\\intbl")
        codes.append({"center": "\\qc", "right": "\\qr", "justify": "\\qj"}.get(para.align, "\\ql"))
        prefix = ""
        size = {"h1": 32, "h2": 28, "h3": 26, "h4": 24, "h5": 22, "h6": 22}.get(para.kind)
        if size:
            level = int(para.kind[1])
            codes.append(f"\\s{level}\\outlinelevel{level - 1}\\sb240\\sa80\\b\\fs{size}")
        elif para.kind == "blockquote":
            codes.append("\\s7\\li567\\i\\cf" + str(self.color("#555555")))
        elif para.kind == "pre":
            codes.append(f"\\s8\\f{self.font('Consolas')}\\fs20")
        elif para.kind == "li":
            level = max(0, min(8, para.level))
            indent = 720 + 360 * level
            override = getattr(para, "_override", 1)
            codes.append(f"\\ls{override}\\ilvl{level}\\fi-360\\li{indent}\\sa40")
            mark = "\u2022" if para.list != "ol" else f"{getattr(para, '_number', 1)}."
            if para.checked is not None:
                mark = "\u2611" if para.checked else "\u2610"
            prefix = "{\\listtext " + _escape(mark) + "\\tab}"
        else:
            codes.append("\\sa120")
        inner = "".join(self.run(r) for r in para.runs)
        if para.kind == "pre":
            inner = "{" + _escape(para.text) + "}"
        end = "" if in_table else "\\par"
        return "".join(codes) + " " + prefix + inner + end + "\n"

    def table(self, table: Table) -> str:
        width = max((sum(c.colspan for c in row) for row in table.rows), default=1)
        col = int(9360 / max(width, 1))
        out = []
        for row in table.rows:
            out.append("\\trowd\\trgaph108\\trleft0")
            x = 0
            for cell in row:
                x += col * cell.colspan
                out.append("\\clbrdrt\\brdrs\\brdrw10\\clbrdrl\\brdrs\\brdrw10\\clbrdrb\\brdrs\\brdrw10"
                           f"\\clbrdrr\\brdrs\\brdrw10\\cellx{x}")
            out.append("\n")
            for cell in row:
                paras = [p for p in cell.blocks if isinstance(p, Para)] or [Para()]
                for index, para in enumerate(paras):
                    if cell.header:
                        para = Para(kind=para.kind, align=para.align,
                                    runs=[Run(**{**r.__dict__, "bold": True}) if not r.br and not r.image else r
                                          for r in para.runs])
                    text = self.para(para, in_table=True)
                    out.append(text.rstrip("\n") + ("\\par " if index < len(paras) - 1 else ""))
                out.append("\\cell\n")
            out.append("\\row\n")
        out.append("\\pard\\plain\\par\n")
        return "".join(out)

    def document(self, blocks: List[Block]) -> str:
        body = []
        counters: Dict[int, int] = {}
        overrides: List[str] = []          # list id per override (\\ls1 = bullets, then one per numbered list)
        current = 0
        prev_li = False
        for block in blocks:
            is_li = isinstance(block, Para) and block.kind == "li"
            if is_li:
                kind = "ol" if block.list == "ol" else "ul"
                if not prev_li or (block.level == 0 and overrides and overrides[current - 1] != kind):
                    counters.clear()
                    overrides.append("ol" if block.list == "ol" else "ul")
                    current = len(overrides)
                if block.list == "ol":
                    counters[block.level] = counters.get(block.level, 0) + 1
                    for deeper in [k for k in counters if k > block.level]:
                        counters.pop(deeper)
                    block._number = counters[block.level]     # type: ignore[attr-defined]
                block._override = current                       # type: ignore[attr-defined]
            prev_li = is_li
            if isinstance(block, Rule):
                body.append("\\page\n" if block.page_break else
                            "\\pard\\brdrb\\brdrs\\brdrw10\\brsp20 \\par\n")
            elif isinstance(block, Table):
                body.append(self.table(block))
            else:
                body.append(self.para(block))
        fonts = "".join(f"{{\\f{i}\\fnil\\fcharset0 {name};}}" for i, name in enumerate(self.fonts))
        colors = ";" + "".join("\\red%d\\green%d\\blue%d;" % (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
                               for c in self.colors)
        styles = ("{\\stylesheet{\\s0 Normal;}" + "".join(f"{{\\s{i} heading {i};}}" for i in range(1, 7))
                  + "{\\s7 Quote;}{\\s8 Code;}}")
        lists = ""
        if overrides:
            def list_def(list_id: int, kind: str) -> str:
                levels = []
                for level in range(9):
                    indent = 720 + 360 * level
                    if kind == "ul":
                        text = "{\\leveltext\\'01\\u8226 ?;}{\\levelnumbers;}"
                        nfc = 23
                    else:
                        text = "{\\leveltext\\'02\\'%02x.;}{\\levelnumbers\\'01;}" % level
                        nfc = 0
                    levels.append(f"{{\\listlevel\\levelnfc{nfc}\\levelnfcn{nfc}\\leveljc0\\leveljcn0"
                                  f"\\levelfollow0\\levelstartat1\\levelspace0\\levelindent0{text}"
                                  f"\\fi-360\\li{indent}\\lin{indent}}}")
                return f"{{\\list\\listtemplateid{list_id}\\listhybrid{''.join(levels)}{{\\listname ;}}\\listid{list_id}}}"
            table = "{\\*\\listtable" + list_def(1, "ul") + list_def(2, "ol") + "}"
            rows = []
            for number, kind in enumerate(overrides, start=1):
                list_id = 1 if kind == "ul" else 2
                restart = ("\\listoverridecount1{\\lfolevel\\listoverridestartat\\levelstartat1}"
                           if kind == "ol" else "\\listoverridecount0")
                rows.append(f"{{\\listoverride\\listid{list_id}{restart}\\ls{number}}}")
            lists = table + "{\\*\\listoverridetable" + "".join(rows) + "}"
        return ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1{\\fonttbl" + fonts + "}{\\colortbl" + colors + "}"
                + styles + lists + "{\\*\\generator MemuNote;}\\viewkind4\\fs22\n" + "".join(body) + "}")


def write_rtf(blocks: List[Block]) -> bytes:
    return _Writer().document(blocks).encode("ascii")
