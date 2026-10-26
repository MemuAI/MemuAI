"""The rich-document model shared by the DOCX / ODT / RTF readers and writers.

Readers turn a file into :class:`Block` objects and :func:`to_html` renders
them as the small, safe HTML dialect the "Document" editor of MemuNote edits
(``contenteditable``). Writers go the other way: :func:`parse_html` reads that
HTML back (also whatever Chromium's editing commands produced) into blocks.

Everything that leaves :func:`to_html` is escaped; attribute values are
whitelisted (colours, sizes, fonts, ``http(s)``/``mailto`` links, ``data:image``
pictures), so a hostile document cannot inject script into the editor.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import List, Optional, Union

@dataclass
class Run:
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    sup: bool = False
    sub: bool = False
    color: str = ""
    highlight: str = ""
    size: float = 0.0
    font: str = ""
    link: str = ""
    br: bool = False
    image: str = ""
    width: int = 0
    height: int = 0

    def same_format(self, other: "Run") -> bool:
        return (self.bold, self.italic, self.underline, self.strike, self.code, self.sup, self.sub, self.color,
                self.highlight, self.size, self.font, self.link) == (
            other.bold, other.italic, other.underline, other.strike, other.code, other.sup, other.sub,
            other.color, other.highlight, other.size, other.font, other.link)

@dataclass
class Para:
    kind: str = "p"
    runs: List[Run] = field(default_factory=list)
    list: str = ""
    level: int = 0
    align: str = ""
    checked: Optional[bool] = None

    @property
    def text(self) -> str:
        return "".join("\n" if r.br else r.text for r in self.runs)

@dataclass
class Cell:
    blocks: List["Para"] = field(default_factory=list)
    header: bool = False
    colspan: int = 1

@dataclass
class Table:
    rows: List[List[Cell]] = field(default_factory=list)

@dataclass
class Rule:
    page_break: bool = False

Block = Union[Para, Table, Rule]

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff", "gray": "#808080", "grey": "#808080",
          "darkred": "#8b0000", "darkblue": "#00008b", "darkgreen": "#006400", "orange": "#ffa500",
          "purple": "#800080", "lightgray": "#d3d3d3", "darkgray": "#a9a9a9", "transparent": ""}

def norm_color(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or value in ("auto", "inherit", "initial", "none"):
        return ""
    if value in _NAMED:
        return _NAMED[value]
    if re.fullmatch(r"#?[0-9a-f]{6}", value):
        return "#" + value.lstrip("#")
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    match = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)", value)
    if match:
        if match.group(4) is not None and float(match.group(4)) == 0:
            return ""
        return "#" + "".join(f"{min(255, int(match.group(i))):02x}" for i in (1, 2, 3))
    return ""

def norm_font(value: str) -> str:
    first = str(value or "").split(",")[0].strip().strip("'\"")
    return first if re.fullmatch(r"[\w .\-]{1,64}", first or "-") and first else ""

def css_size_to_pt(value: str) -> float:
    match = re.fullmatch(r"\s*([\d.]+)\s*(pt|px|em|rem|%)?\s*", str(value or ""))
    if not match:
        return {"xx-small": 7, "x-small": 7.5, "small": 10, "medium": 12, "large": 13.5, "x-large": 18,
                "xx-large": 24, "xxx-large": 36}.get(str(value or "").strip(), 0.0)
    number = float(match.group(1))
    unit = match.group(2) or "px"
    if unit == "pt":
        pt = number
    elif unit == "px":
        pt = number * 0.75
    elif unit in ("em", "rem"):
        pt = number * 12
    else:
        pt = number * 12 / 100
    return round(min(max(pt, 1.0), 400.0) * 2) / 2

def safe_link(url: str) -> str:
    url = str(url or "").strip()
    if re.match(r"^(https?:|mailto:)", url, re.I) or url.startswith("#"):
        return url
    if re.match(r"^www\.", url, re.I):
        return "https://" + url
    return ""

def safe_image(src: str) -> str:
    src = str(src or "").strip()
    if re.match(r"^data:image/(png|jpe?g|gif|webp|bmp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$", src):
        return src.replace("\n", "").replace(" ", "")
    return ""

def _run_html(run: Run) -> str:
    if run.br:
        return "<br>"
    if run.image:
        size = ""
        if run.width:
            size += f' width="{int(run.width)}"'
        if run.height:
            size += f' height="{int(run.height)}"'
        return f'<img src="{html.escape(run.image, quote=True)}"{size} alt="">'
    text = html.escape(run.text, quote=False)
    if not text:
        return ""
    styles = []
    if run.color:
        styles.append(f"color:{run.color}")
    if run.highlight:
        styles.append(f"background-color:{run.highlight}")
    if run.size:
        styles.append(f"font-size:{run.size:g}pt")
    if run.font:
        styles.append(f"font-family:{html.escape(run.font, quote=True)}")
    if styles:
        text = f'<span style="{";".join(styles)}">{text}</span>'
    for flag, tag in ((run.code, "code"), (run.sup, "sup"), (run.sub, "sub"), (run.strike, "s"),
                      (run.underline, "u"), (run.italic, "em"), (run.bold, "strong")):
        if flag:
            text = f"<{tag}>{text}</{tag}>"
    if run.link:
        text = f'<a href="{html.escape(run.link, quote=True)}">{text}</a>'
    return text

def _para_inner(para: Para) -> str:
    inner = "".join(_run_html(r) for r in para.runs)
    return inner or "<br>"

def to_html(blocks: List[Block]) -> str:
    out: List[str] = []
    stack: List[str] = []

    def close_lists(level: int) -> None:
        while len(stack) > level:
            out.append(f"</li></{stack.pop()}>")

    for block in blocks:
        if isinstance(block, Para) and block.kind == "li":
            want = max(0, min(8, block.level)) + 1
            tag = "ol" if block.list == "ol" else "ul"
            if len(stack) > want:
                close_lists(want)
            if len(stack) == want and stack[-1] != tag:
                close_lists(want - 1)
            if len(stack) == want:
                out.append("</li>")
            while len(stack) < want:
                cls = ' class="mn-tasks"' if block.checked is not None and tag == "ul" else ""
                out.append(f"<{tag}{cls}>")
                stack.append(tag)
            align = f' style="text-align:{block.align}"' if block.align else ""
            check = ""
            if block.checked is not None:
                check = f' data-checked="{"true" if block.checked else "false"}"'
            out.append(f"<li{align}{check}>{_para_inner(block)}")
            continue
        close_lists(0)
        if isinstance(block, Rule):
            out.append('<hr class="mn-pagebreak">' if block.page_break else "<hr>")
        elif isinstance(block, Table):
            rows = []
            for row in block.rows:
                cells = []
                for cell in row:
                    tag = "th" if cell.header else "td"
                    span = f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
                    inner = "".join(f"<p>{_para_inner(p)}</p>" if p.kind == "p" else to_html([p])
                                    for p in cell.blocks) or "<p><br></p>"
                    cells.append(f"<{tag}{span}>{inner}</{tag}>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            out.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
        else:
            tag = block.kind if block.kind in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre") else "p"
            align = f' style="text-align:{block.align}"' if block.align in ("left", "center", "right", "justify") else ""
            if tag == "pre":
                out.append(f"<pre>{html.escape(block.text, quote=False)}</pre>")
            else:
                out.append(f"<{tag}{align}>{_para_inner(block)}</{tag}>")
    close_lists(0)
    return "\n".join(out)

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "li", "section", "article",
               "header", "footer", "main", "figure", "figcaption", "address", "dt", "dd"}
_SKIP = {"script", "style", "head", "title", "template", "noscript", "iframe", "object", "svg", "math"}

def _parse_style(value: str) -> dict:
    out = {}
    for part in str(value or "").split(";"):
        if ":" in part:
            key, val = part.split(":", 1)
            out[key.strip().lower()] = val.strip()
    return out

class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Block] = []
        self.fmt: List[Run] = [Run()]
        self.fmt_tags: List[str] = []
        self.para: Optional[Para] = None
        self.block_stack: List[Para] = []
        self.lists: List[str] = []
        self.list_tasks: List[bool] = []
        self.skip = 0
        self.pre = 0

        self.tables: List[dict] = []

    def _container(self) -> List[Block]:
        if self.tables and self.tables[-1]["cell"] is not None:
            return self.tables[-1]["cell"].blocks
        return self.blocks

    def _template(self) -> Para:
        return self.block_stack[-1] if self.block_stack else Para()

    def _flush(self) -> None:
        if self.para is not None:
            para = self.para
            self.para = None

            while para.runs and not para.runs[-1].br and not para.runs[-1].image and not para.runs[-1].text.strip():
                para.runs.pop()
            if para.runs and para.runs[-1].br and len(para.runs) > 1:
                para.runs.pop()
            if para.runs and para.kind != "pre":
                first = para.runs[0]
                if not first.br and not first.image:
                    first.text = first.text.lstrip()
                last = para.runs[-1]
                if not last.br and not last.image:

                    last.text = last.text.rstrip(" \t\r\n")
            if para.runs or para.kind in ("li",) or getattr(para, "_explicit", False):
                self._container().append(para)

    def _ensure_para(self) -> Para:
        if self.para is None:
            tpl = self._template()
            self.para = Para(kind=tpl.kind, list=tpl.list, level=tpl.level, align=tpl.align, checked=tpl.checked)
        return self.para

    def _add_run(self, run: Run) -> None:
        para = self._ensure_para()
        if run.text and para.runs and not para.runs[-1].br and not para.runs[-1].image \
                and para.runs[-1].same_format(run):
            para.runs[-1].text += run.text
        else:
            para.runs.append(run)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        style = _parse_style(attrs.get("style", ""))
        if tag in ("br",):
            self._add_run(Run(br=True))
            return
        if tag == "img":
            src = safe_image(attrs.get("src", ""))
            if src:
                def num(name):
                    raw = attrs.get(name) or style.get(name, "")
                    match = re.match(r"\s*(\d+)", str(raw))
                    return int(match.group(1)) if match else 0
                self._add_run(Run(image=src, width=num("width"), height=num("height")))
            return
        if tag == "hr":
            self._flush()
            self._container().append(Rule(page_break="mn-pagebreak" in attrs.get("class", "")
                                          or "page-break" in style.get("page-break-after", "")
                                          + style.get("break-after", "")))
            return
        if tag == "table":
            self._flush()
            self.tables.append({"table": Table(), "row": None, "cell": None, "saved": (self.block_stack, self.para)})
            self.block_stack = []
            return
        if self.tables:
            state = self.tables[-1]
            if tag == "tr":
                self._flush()
                state["row"] = []
                state["table"].rows.append(state["row"])
                return
            if tag in ("td", "th"):
                self._flush()
                if state["row"] is None:
                    state["row"] = []
                    state["table"].rows.append(state["row"])
                try:
                    span = max(1, min(64, int(attrs.get("colspan") or 1)))
                except ValueError:
                    span = 1
                state["cell"] = Cell(header=tag == "th", colspan=span)
                state["row"].append(state["cell"])
                return
        if tag in ("ul", "ol"):
            self._flush()
            self.lists.append(tag)
            self.list_tasks.append("mn-tasks" in attrs.get("class", "") or "contains-task-list" in attrs.get("class", ""))
            return
        if tag in _BLOCK_TAGS:
            if self.para is not None and not self.para.runs and self.block_stack:
                self.para = None
            self._flush()
            kind = tag if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre") else "p"
            tpl = self._template()
            if tag == "li":
                kind = "li"
            elif tpl.kind in ("blockquote", "li") and tag in ("p", "div"):
                kind = tpl.kind
            align = style.get("text-align", "") or attrs.get("align", "")
            align = align.lower() if align.lower() in ("left", "center", "right", "justify") else tpl.align
            para = Para(kind=kind, align=align)
            if kind == "li" or tpl.kind == "li":
                para.kind = "li"
                para.list = self.lists[-1] if self.lists else "ul"
                para.level = max(0, len(self.lists) - 1)
                checked = attrs.get("data-checked")
                if checked in ("true", "false"):
                    para.checked = checked == "true"
                elif tpl.kind == "li":
                    para.checked = tpl.checked
                elif self.list_tasks and self.list_tasks[-1]:
                    para.checked = False
            if kind == "pre":
                self.pre += 1
            self.block_stack.append(para)
            if tag in ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._ensure_para()._explicit = True
            return
        if tag == "input" and attrs.get("type") == "checkbox":
            para = self._ensure_para()
            if para.kind == "li":
                para.checked = "checked" in attrs
            return

        cur = replace(self.fmt[-1], text="", br=False, image="", width=0, height=0)
        if tag in ("b", "strong"):
            cur.bold = True
        elif tag in ("i", "em", "cite", "dfn"):
            cur.italic = True
        elif tag in ("u", "ins"):
            cur.underline = True
        elif tag in ("s", "strike", "del"):
            cur.strike = True
        elif tag in ("code", "kbd", "samp", "tt"):
            cur.code = True
        elif tag == "sup":
            cur.sup = True
        elif tag == "sub":
            cur.sub = True
        elif tag == "mark":
            cur.highlight = cur.highlight or "#ffff00"
        elif tag == "a":
            cur.link = safe_link(attrs.get("href", "")) or cur.link
        elif tag == "font":
            cur.color = norm_color(attrs.get("color", "")) or cur.color
            cur.font = norm_font(attrs.get("face", "")) or cur.font
            size = {"1": 7.5, "2": 10, "3": 12, "4": 13.5, "5": 18, "6": 24, "7": 36}.get(attrs.get("size", ""))
            cur.size = size or cur.size
        weight = style.get("font-weight", "")
        if weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600):
            cur.bold = True
        elif weight in ("normal", "400", "lighter"):
            cur.bold = False
        if style.get("font-style") == "italic":
            cur.italic = True
        deco = style.get("text-decoration", "") + " " + style.get("text-decoration-line", "")
        if "underline" in deco:
            cur.underline = True
        if "line-through" in deco:
            cur.strike = True
        if style.get("color"):
            cur.color = norm_color(style["color"]) or cur.color
        background = style.get("background-color") or style.get("background")
        if background:
            cur.highlight = norm_color(background) or cur.highlight
        if style.get("font-size"):
            cur.size = css_size_to_pt(style["font-size"]) or cur.size
        if style.get("font-family"):
            cur.font = norm_font(style["font-family"]) or cur.font
        if style.get("vertical-align") == "super":
            cur.sup = True
        elif style.get("vertical-align") == "sub":
            cur.sub = True
        self.fmt.append(cur)
        self.fmt_tags.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "table" and self.tables:
            self._flush()
            state = self.tables.pop()
            self.block_stack, self.para = state["saved"]
            self._flush()
            self._container().append(state["table"])
            return
        if self.tables and tag in ("td", "th"):
            self._flush()
            self.tables[-1]["cell"] = None
            return
        if self.tables and tag == "tr":
            self._flush()
            self.tables[-1]["row"] = None
            return
        if tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
                self.list_tasks.pop()
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if self.block_stack:
                closed = self.block_stack.pop()
                if closed.kind == "pre":
                    self.pre = max(0, self.pre - 1)
            return
        if tag in self.fmt_tags:

            while self.fmt_tags:
                last = self.fmt_tags.pop()
                self.fmt.pop()
                if last == tag:
                    break

    def handle_data(self, data):
        if self.skip or not data:
            return
        if not self.pre:
            data = re.sub(r"[ \t\r\n\f]+", " ", data)
            if data == " " and self.para is None:
                return
            if self.para is None and not data.strip():
                return
            run = replace(self.fmt[-1], text=data)
            self._add_run(run)
            return
        lines = data.replace("\r\n", "\n").split("\n")
        for index, line in enumerate(lines):
            if index:
                self._add_run(Run(br=True))
            if line:
                self._add_run(replace(self.fmt[-1], text=line))

    def close(self):
        super().close()
        self._flush()
        while self.tables:
            state = self.tables.pop()
            self.blocks.append(state["table"])

def parse_html(source: str) -> List[Block]:
    parser = _Parser()
    parser.feed(str(source or ""))
    parser.close()

    return parser.blocks

def to_text(blocks: List[Block]) -> str:
    lines: List[str] = []
    for block in blocks:
        if isinstance(block, Para):
            prefix = ""
            if block.kind == "li":
                prefix = "  " * block.level + ("• " if block.list != "ol" else "- ")
            lines.append(prefix + block.text)
        elif isinstance(block, Table):
            for row in block.rows:
                lines.append("\t".join(" ".join(p.text for p in cell.blocks) for cell in row))
        elif isinstance(block, Rule):
            lines.append("")
    return "\n".join(lines)

def _md_inline(para: Para) -> str:
    out = []
    for run in para.runs:
        if run.br:
            out.append("  \n")
            continue
        if run.image:
            out.append(f"![]({run.image})")
            continue
        text = run.text
        if not run.code:
            text = re.sub(r"([\\`*_\[\]<>|])", r"\\\1", text)
        if not text.strip():
            out.append(text)
            continue
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if run.code:
            core = f"`{core}`"
        if run.strike:
            core = f"~~{core}~~"
        if run.italic:
            core = f"*{core}*"
        if run.bold:
            core = f"**{core}**"
        if run.link:
            core = f"[{core}]({run.link})"
        out.append(lead + core + trail)
    return "".join(out)

def to_markdown(blocks: List[Block]) -> str:
    out: List[str] = []
    counters: dict = {}
    prev_li = False
    for block in blocks:
        if isinstance(block, Para) and block.kind == "li":
            indent = "   " * block.level
            if block.list == "ol":
                counters[block.level] = counters.get(block.level, 0) + 1
                mark = f"{counters[block.level]}."
            else:
                mark = "-"
            box = "" if block.checked is None else ("[x] " if block.checked else "[ ] ")
            out.append(f"{indent}{mark} {box}{_md_inline(block)}")
            for deeper in [k for k in counters if k > block.level]:
                counters.pop(deeper)
            prev_li = True
            continue
        if prev_li:
            out.append("")
            counters.clear()
            prev_li = False
        if isinstance(block, Rule):
            out.append("---\n")
        elif isinstance(block, Table):
            if not block.rows:
                continue
            width = max(sum(c.colspan for c in row) for row in block.rows)
            def cells(row):
                values = []
                for cell in row:
                    values.append(" ".join(_md_inline(p) for p in cell.blocks).replace("|", "\\|").replace("\n", " "))
                    values += [""] * (cell.colspan - 1)
                return values + [""] * (width - len(values))
            rows = [cells(r) for r in block.rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "|".join([" --- "] * width) + "|")
            for row in rows[1:]:
                out.append("| " + " | ".join(row) + " |")
            out.append("")
        elif block.kind.startswith("h") and len(block.kind) == 2:
            out.append("#" * int(block.kind[1]) + " " + _md_inline(block) + "\n")
        elif block.kind == "blockquote":
            out.append("> " + _md_inline(block).replace("\n", "\n> ") + "\n")
        elif block.kind == "pre":
            out.append("```\n" + block.text + "\n```\n")
        else:
            text = _md_inline(block)
            if block.align == "center":
                text = f'<div align="center">{text}</div>' if text else text
            out.append(text + "\n")
    return "\n".join(out).rstrip() + "\n"

def tidy(blocks: List[Block]) -> List[Block]:
    """Post-processing shared by the readers: consecutive code lines become one ``pre`` block."""
    out: List[Block] = []
    for block in blocks:
        if (isinstance(block, Para) and block.kind == "pre" and out and isinstance(out[-1], Para)
                and out[-1].kind == "pre"):
            out[-1].runs.append(Run(br=True))
            out[-1].runs.extend(block.runs)
            continue
        out.append(block)
    return out

def text_to_blocks(text: str) -> List[Block]:
    return [Para(runs=[Run(text=line)] if line else []) for line in str(text or "").replace("\r\n", "\n").split("\n")]
