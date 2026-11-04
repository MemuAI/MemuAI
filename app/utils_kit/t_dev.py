# -*- coding: utf-8 -*-
"""Formatters and developer micro-tools: JSON/XML/CSS/SQL formatting, cron, chmod, regex, curl → code, safe calculator…"""
from __future__ import annotations

import ast
import calendar
import json
import math
import operator
import re
import shlex
import threading
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .core import O, UtilError, guess_mime, lines_of, table, utility

# ── JSON ────────────────────────────────────────────────────────────────────────────────────────────────────────────
class _Loose:
    """A forgiving JSON reader (JSON5-like): // and /* */ comments, trailing commas, single quotes, unquoted keys,
    Python literals (True/False/None), NaN/Infinity, hex numbers, a leading +."""

    def __init__(self, text: str):
        self.s, self.i, self.n = text, 0, len(text)

    def fail(self, what: str):
        line = self.s.count("\n", 0, self.i) + 1
        col = self.i - (self.s.rfind("\n", 0, self.i) + 1) + 1
        raise ValueError(f"{what} (line {line}, column {col})")

    def ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n\ufeff\u00a0":
                self.i += 1
            elif s.startswith("//", self.i) or (c == "#" and not s[s.rfind("\n", 0, self.i) + 1:self.i].strip()):
                end = s.find("\n", self.i)
                self.i = n if end < 0 else end + 1
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                self.i = n if end < 0 else end + 2
            else:
                break

    def value(self):
        self.ws()
        if self.i >= self.n:
            self.fail("Unexpected end")
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "\"'`":
            return self.string()
        return self.bare()

    def obj(self):
        self.i += 1
        out = {}
        while True:
            self.ws()
            if self.i < self.n and self.s[self.i] == "}":
                self.i += 1
                return out
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i >= self.n:
                self.fail("Unclosed {")
            if self.s[self.i] in "\"'`":
                key = self.string()
            else:
                m = re.compile(r"[^\s:,{}\[\]]+").match(self.s, self.i)
                if not m:
                    self.fail("Expected a key")
                key, self.i = m.group(0), m.end()
            self.ws()
            if self.i >= self.n or self.s[self.i] not in ":=":
                self.fail("Expected ':'")
            self.i += 1
            out[str(key)] = self.value()
            self.ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and self.s[self.i] != "}":
                if self.s[self.i] in "\"'`" or re.match(r"[A-Za-z_$]", self.s[self.i]):
                    continue  # a missing comma between members
                self.fail("Expected ',' or '}'")

    def arr(self):
        self.i += 1
        out = []
        while True:
            self.ws()
            if self.i < self.n and self.s[self.i] == "]":
                self.i += 1
                return out
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i >= self.n:
                self.fail("Unclosed [")
            out.append(self.value())
            self.ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and self.s[self.i] != "]":
                if self.s[self.i] in "\"'`{[-0123456789" or self.s[self.i].isalpha():
                    continue  # a missing comma between items
                self.fail("Expected ',' or ']'")

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        esc = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "/": "/", "\\": "\\", "'": "'", '"': '"', "`": "`", "\n": ""}
        while self.i < self.n:
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                if nxt == "u" and re.match(r"[0-9a-fA-F]{4}", self.s[self.i + 2:self.i + 6]):
                    code = int(self.s[self.i + 2:self.i + 6], 16)
                    self.i += 6
                    if 0xD800 <= code < 0xDC00 and self.s.startswith("\\u", self.i):
                        low = int(self.s[self.i + 2:self.i + 6], 16)
                        if 0xDC00 <= low < 0xE000:
                            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                            self.i += 6
                    out.append(chr(code))
                    continue
                if nxt == "x" and re.match(r"[0-9a-fA-F]{2}", self.s[self.i + 2:self.i + 4]):
                    out.append(chr(int(self.s[self.i + 2:self.i + 4], 16)))
                    self.i += 4
                    continue
                out.append(esc.get(nxt, nxt))
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        self.fail("Unclosed string")

    def bare(self):
        m = re.compile(r"[^\s,:{}\[\]]+").match(self.s, self.i)
        if not m:
            self.fail(f"Unexpected '{self.s[self.i]}'")
        word = m.group(0)
        self.i = m.end()
        low = word.lower()
        consts = {"true": True, "false": False, "null": None, "none": None, "nil": None, "undefined": None,
                  "nan": float("nan"), "infinity": float("inf"), "+infinity": float("inf"), "-infinity": float("-inf")}
        if low in consts:
            return consts[low]
        clean = word.replace("_", "")
        try:
            if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", clean):
                return int(clean, 16)
            if re.fullmatch(r"[+-]?\d+", clean):
                return int(clean)
            return float(clean)
        except ValueError:
            return word  # an unquoted string value

    def parse(self):
        value = self.value()
        self.ws()
        if self.i < self.n and self.s[self.i] == ";":
            self.i += 1
            self.ws()
        if self.i < self.n:
            self.fail("Extra data after the value")
        return value

def load_json_lenient(text: str):
    """Strict JSON first; then the forgiving reader (comments, trailing commas, single quotes, unquoted keys)."""
    try:
        return json.loads(text), []
    except json.JSONDecodeError as first:
        body = text.strip()
        m = re.match(r"^(?:(?:export\s+)?(?:const|let|var)\s+[\w$]+\s*=\s*|module\.exports\s*=\s*|[\w$.]+\s*=\s*)(?=[{\[])", body)
        if m:
            body = body[m.end():]
        try:
            return _Loose(body).parse(), [first]
        except (ValueError, RecursionError):
            raise UtilError(f"Ошибка JSON: {first.msg} (строка {first.lineno}, столбец {first.colno})",
                            f"JSON error: {first.msg} (line {first.lineno}, column {first.colno})")

@utility("fmt.json", "format", "JSON: форматировать, сжать, починить", "JSON: format, minify, repair",
         "Красивые отступы, сжатие в одну строку, сортировка ключей; чинит комментарии, лишние запятые и одинарные кавычки.",
         "Pretty indent, minify, sort keys; repairs comments, trailing commas and single quotes.",
         opts=[O("mode", "choice", "Режим", "Mode", "pretty", choices=[("pretty", "Красиво", "Pretty"), ("minify", "В одну строку", "Minify"), ("escape", "Как строку JSON", "As a JSON string"), ("unescape", "Из строки JSON", "From a JSON string")]),
               O("indent", "choice", "Отступ", "Indent", "2", show="mode=pretty", choices=[("2", "2 пробела", "2 spaces"), ("4", "4 пробела", "4 spaces"), ("tab", "Табуляция", "Tab")]),
               O("sort", "bool", "Сортировать ключи", "Sort keys", False), O("ascii", "bool", "Экранировать не-ASCII", "Escape non-ASCII", False)],
         live=True, ai_default=True, keys="json форматировать pretty minify beautify валидатор")
def fmt_json(ctx):
    text = ctx.need_text().strip()
    mode = ctx.o["mode"]
    if mode == "unescape":
        try:
            value = json.loads(text if text.startswith('"') else '"' + text + '"')
        except json.JSONDecodeError as exc:
            raise UtilError(f"Это не строка JSON: {exc.msg}", f"Not a JSON string: {exc.msg}")
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    value, repaired = load_json_lenient(text)
    if mode == "escape":
        return json.dumps(json.dumps(value, ensure_ascii=ctx.o["ascii"], sort_keys=ctx.o["sort"]), ensure_ascii=ctx.o["ascii"])
    if mode == "minify":
        out = json.dumps(value, ensure_ascii=ctx.o["ascii"], sort_keys=ctx.o["sort"], separators=(",", ":"))
    else:
        indent = "\t" if ctx.o["indent"] == "tab" else int(ctx.o["indent"])
        out = json.dumps(value, ensure_ascii=ctx.o["ascii"], sort_keys=ctx.o["sort"], indent=indent)
    note = ctx.tr("JSON был с ошибками — исправлен", "The JSON had errors — repaired") if repaired else ""
    return {"text": out, "lang": "json", "note": note}

# ── XML / HTML / CSS / SQL ──────────────────────────────────────────────────────────────────────────────────────────
@utility("fmt.xml", "format", "XML: форматировать / сжать", "XML: format / minify",
         "Отступы для XML (SVG, RSS, конфиги) или сжатие в одну строку; показывает ошибку разметки.",
         "Indent XML (SVG, RSS, configs) or minify it; shows markup errors.",
         opts=[O("mode", "choice", "Режим", "Mode", "pretty", choices=[("pretty", "Красиво", "Pretty"), ("minify", "Сжать", "Minify")]),
               O("indent", "int", "Отступ", "Indent", 2, min=1, max=8, show="mode=pretty")],
         live=True, ai_default=True, keys="xml svg rss форматировать pretty")
def fmt_xml(ctx):
    from xml.dom import minidom
    from xml.parsers.expat import ExpatError
    text = ctx.need_text().strip()
    if "<!ENTITY" in text.upper():
        raise UtilError("XML с объявлениями ENTITY не обрабатывается (защита от XXE)", "XML with ENTITY declarations is refused (XXE guard)")
    try:
        dom = minidom.parseString(text.encode("utf-8"))
    except ExpatError as exc:
        raise UtilError(f"Ошибка XML: {exc}", f"XML error: {exc}")
    if ctx.o["mode"] == "minify":
        out = re.sub(r">\s+<", "><", dom.toxml())
    else:
        out = dom.toprettyxml(indent=" " * int(ctx.o["indent"]))
        out = "\n".join(line for line in out.split("\n") if line.strip())
    if not text.startswith("<?xml"):
        out = re.sub(r"^<\?xml[^>]*\?>\s*", "", out)
    return {"text": out, "lang": "xml"}

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "!doctype"}

_INLINE = {"a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark", "q", "s", "samp", "small",
           "span", "strong", "sub", "sup", "time", "u", "var", "img", "wbr", "del", "ins", "label", "button", "input", "select", "font"}
_RAW = {"pre", "script", "style", "textarea"}

def _html_tree(text: str):
    """A forgiving tree: [tag, start_text, children] / ("text", t) / ("raw", t). Stray end tags are dropped, unclosed
    ones close at their parent's end."""
    from html.parser import HTMLParser

    root = ["#root", "", []]
    stack = [root]

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            node = [tag, self.get_starttag_text() or f"<{tag}>", []]
            stack[-1][2].append(node)
            if tag not in _VOID:
                stack.append(node)

        def handle_startendtag(self, tag, attrs):
            stack[-1][2].append([tag, self.get_starttag_text() or f"<{tag}/>", None])

        def handle_endtag(self, tag):
            for k in range(len(stack) - 1, 0, -1):
                if stack[k][0] == tag:
                    del stack[k:]
                    return

        def handle_data(self, data):
            stack[-1][2].append(("raw" if stack[-1][0] in _RAW else "text", data))

        def handle_comment(self, data):
            stack[-1][2].append(("raw", f"<!--{data}-->"))

        def handle_decl(self, decl):
            stack[-1][2].append(("raw", f"<!{decl}>"))

        def handle_entityref(self, name):
            self.handle_data(f"&{name};")

        def handle_charref(self, name):
            self.handle_data(f"&#{name};")

    parser = P(convert_charrefs=False)
    parser.feed(text)
    parser.close()
    return root

def _html_inline(node) -> str:
    if isinstance(node, tuple):
        return re.sub(r"\s+", " ", node[1]) if node[0] == "text" else node[1]
    tag, start, kids = node
    if kids is None or tag in _VOID:
        return start
    return start + "".join(_html_inline(k) for k in kids) + f"</{tag}>"

def _html_is_inline(node) -> bool:
    if isinstance(node, tuple):
        return node[0] == "text" or not node[1].startswith("<!")
    return node[0] in _INLINE and all(_html_is_inline(k) for k in (node[2] or []))

def _html_pretty(node, depth: int, out: list) -> None:
    pad = "  " * depth
    if isinstance(node, tuple):
        text = node[1].strip() if node[0] == "text" else node[1].strip("\n")
        if text:
            out.append(pad + (re.sub(r"\s+", " ", text) if node[0] == "text" else text))
        return
    tag, start, kids = node
    if kids is None or tag in _VOID:
        out.append(pad + start)
        return
    if tag in _RAW:
        out.append(pad + start + "".join(k[1] for k in kids) + f"</{tag}>")
        return
    if all(_html_is_inline(k) for k in kids):
        line = (start + "".join(_html_inline(k) for k in kids).strip() + f"</{tag}>")
        if len(line) + len(pad) <= 110 or not kids:
            out.append(pad + line)
            return
    out.append(pad + start)
    run: list = []

    def flush():
        text = "".join(_html_inline(k) for k in run).strip()
        if text:
            out.append("  " * (depth + 1) + text)
        run.clear()
    for kid in kids:
        if _html_is_inline(kid):
            run.append(kid)
        else:
            flush()
            _html_pretty(kid, depth + 1, out)
    flush()
    out.append(pad + f"</{tag}>")

@utility("fmt.html", "format", "HTML: форматировать / сжать", "HTML: format / minify",
         "Расставляет отступы в HTML или сжимает его (убирает комментарии и пробелы между тегами).",
         "Indents HTML or minifies it (drops comments and whitespace between tags).",
         opts=[O("mode", "choice", "Режим", "Mode", "pretty", choices=[("pretty", "Красиво", "Pretty"), ("minify", "Сжать", "Minify")])],
         live=True, keys="html форматировать beautify minify")
def fmt_html(ctx):
    text = ctx.need_text()
    if ctx.o["mode"] == "minify":
        parts = re.split(r"(?is)(<(?:pre|script|style|textarea)\b.*?</(?:pre|script|style|textarea)>)", text)
        res = []
        for k, part in enumerate(parts):
            if k % 2:
                res.append(part)
                continue
            part = re.sub(r"<!--(?!\[if).*?-->", "", part, flags=re.S)
            part = re.sub(r">\s+<", "><", part)
            res.append(re.sub(r"\s{2,}", " ", part))
        return {"text": "".join(res).strip(), "lang": "html"}
    out: list = []
    for kid in _html_tree(text)[2]:
        _html_pretty(kid, 0, out)
    return {"text": "\n".join(out), "lang": "html"}

def _css_tokens(text: str):
    """CSS → [("text", chunk) | ("{",) | ("}",) | (";",) | ("comment", c)] — strings, url(...) and parentheses are kept whole."""
    out, buf, i, n, depth = [], [], 0, len(text), 0

    def flush():
        if buf:
            out.append(("text", "".join(buf)))
            buf.clear()
    while i < n:
        c = text[i]
        if c in "\"'":
            end = i + 1
            while end < n and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            flush()
            out.append(("comment", text[i:end]))
            i = end
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        if depth == 0 and c in "{};":
            flush()
            out.append((c,))
        else:
            buf.append(c)
        i += 1
    flush()
    return out

def _css_squash(chunk: str, selector: bool) -> str:
    """Whitespace runs → one space outside strings; around , (and > ~ + in selectors) none. Parentheses keep their
    inner spaces (``calc(1px + 2px)`` stays valid)."""
    parts = re.split(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\([^()]*\))", chunk)
    res = []
    for k, part in enumerate(parts):
        if k % 2:
            res.append(re.sub(r"\s+", " ", part) if part.startswith("(") else part)
            continue
        part = re.sub(r"\s+", " ", part)
        part = re.sub(r"\s*([,>~+])\s*" if selector else r"\s*(,)\s*", r"\1", part)
        res.append(part)
    return "".join(res).strip()

def _css_decl(chunk: str, minify: bool) -> str:
    chunk = _css_squash(chunk, False)
    m = re.match(r"^([^:]+?)\s*:\s*(.*)$", chunk, re.S)
    if not m or m.group(1).strip().startswith("@"):
        return chunk
    value = re.sub(r"\s*!\s*important", "!important" if minify else " !important", m.group(2).strip())
    if not minify:
        bits = re.split(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\([^()]*\))", value)
        value = "".join(b if k % 2 else b.replace(",", ", ") for k, b in enumerate(bits))
    return f"{m.group(1).strip()}:{value}" if minify else f"{m.group(1).strip()}: {value}"

@utility("fmt.css", "format", "CSS: форматировать / сжать", "CSS: format / minify",
         "Раскладывает CSS по строкам с отступами или сжимает до минимума.",
         "Lays CSS out with indentation or minifies it.",
         opts=[O("mode", "choice", "Режим", "Mode", "pretty", choices=[("pretty", "Красиво", "Pretty"), ("minify", "Сжать", "Minify")])],
         live=True, keys="css форматировать minify beautify")
def fmt_css(ctx):
    text = ctx.need_text()
    minify = ctx.o["mode"] == "minify"
    toks = _css_tokens(text)
    out, depth, pending = [], 0, []
    if minify:
        res = []
        for idx, tok in enumerate(toks):
            kind = tok[0]
            if kind == "comment":
                if tok[1].startswith("/*!"):
                    res.append(tok[1])
                continue
            if kind == "text":
                nxt = next((t[0] for t in toks[idx + 1:] if t[0] != "comment"), "")
                if not tok[1].strip():
                    continue
                res.append(_css_squash(tok[1], True) if nxt == "{" else _css_decl(tok[1], True))
            elif kind == "}" and res and res[-1] == ";":
                res[-1] = "}"
            else:
                res.append(kind)
        return {"text": "".join(res), "lang": "css"}
    for idx, tok in enumerate(toks):
        kind = tok[0]
        pad = "  " * depth
        if kind == "comment":
            out.append(pad + tok[1].strip())
        elif kind == "text":
            if not tok[1].strip():
                continue
            nxt = next((t[0] for t in toks[idx + 1:] if t[0] != "comment"), "")
            if nxt == "{":
                sel = _css_squash(tok[1], True)
                sel = re.sub(r",(?=\S)", ", ", sel) if sel.startswith("@") else sel.replace(",", ",\n" + pad)
                sel = re.sub(r"([>~+])", r" \1 ", sel) if not sel.startswith("@") else sel
                pending.append(pad + re.sub(r"  +", " ", sel))
            else:
                out.append(pad + _css_decl(tok[1], False) + ";")
        elif kind == "{":
            head = pending.pop() if pending else pad
            out.append(head + " {")
            depth += 1
        elif kind == "}":
            depth = max(0, depth - 1)
            out.append("  " * depth + "}")
            if depth == 0:
                out.append("")
    pretty = "\n".join(out).strip() + "\n"
    return {"text": re.sub(r"\n{3,}", "\n\n", pretty), "lang": "css"}

_SQL_KW = ("select distinct", "select", "from", "where", "group by", "order by", "having", "limit", "offset", "union all", "union",
           "insert into", "values", "update", "set", "delete from", "left join", "right join", "inner join", "full join",
           "cross join", "join", "on", "and", "or", "returning", "with", "create table", "alter table", "drop table")

@utility("fmt.sql", "format", "SQL: форматировать", "SQL: format",
         "Ключевые слова заглавными, каждое предложение (SELECT, FROM, WHERE, JOIN…) с новой строки.",
         "Upper-case keywords, each clause (SELECT, FROM, WHERE, JOIN…) on its own line.",
         opts=[O("upper", "bool", "Ключевые слова заглавными", "Upper-case keywords", True)],
         live=True, keys="sql форматировать beautify запрос")
def fmt_sql(ctx):
    text = re.sub(r"\s+", " ", ctx.need_text()).strip()
    strings: List[str] = []
    text = re.sub(r"'(?:''|[^'])*'", lambda m: strings.append(m.group(0)) or f"\x00{len(strings) - 1}\x00", text)
    for kw in sorted(_SQL_KW, key=len, reverse=True):
        rx = re.compile(r"(?<![\w.])" + kw.replace(" ", r"\s+") + r"(?![\w])", re.I)
        word = kw.upper() if ctx.o["upper"] else kw
        if kw in ("and", "or", "on"):
            text = rx.sub("\n  " + word, text)
        else:
            text = rx.sub("\n" + word, text)
    others = r"\b(as|in|not|null|is|like|between|case|when|then|else|end|desc|asc|count|sum|avg|min|max|distinct|exists|by|into|primary|key|default|references|int|integer|text|varchar|boolean|date|timestamp)\b"
    if ctx.o["upper"]:
        text = re.sub(others, lambda m: m.group(0).upper(), text, flags=re.I)
    text = re.sub(r",\s*(?![^()]*\))", ",\n  ", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], text)
    return {"text": "\n".join(line.rstrip() for line in text.strip().split("\n") if line.strip()), "lang": "sql"}

# ── tables ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_delimited(text: str) -> List[List[str]]:
    import csv
    import io
    text = text.strip("\n")
    if text.lstrip().startswith("|"):
        rows = []
        for line in lines_of(text):
            line = line.strip()
            if not line or re.fullmatch(r"\|?[\s:\-|]+\|?", line):
                continue
            rows.append([c.strip() for c in line.strip("|").split("|")])
        return rows
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in text else csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect) if row]

@utility("fmt.table", "format", "Таблица: CSV ↔ Markdown ↔ HTML ↔ ASCII", "Table: CSV ↔ Markdown ↔ HTML ↔ ASCII",
         "Превращает CSV/TSV или скопированную из Excel таблицу в Markdown, HTML, ASCII-рамку, CSV или JSON.",
         "Turns CSV/TSV or a table copied from Excel into Markdown, HTML, an ASCII box, CSV or JSON.",
         opts=[O("to", "choice", "Во что", "To", "md", choices=[("md", "Markdown", "Markdown"), ("html", "HTML", "HTML"), ("ascii", "ASCII-рамка", "ASCII box"),
                                                         ("csv", "CSV", "CSV"), ("tsv", "TSV (для Excel)", "TSV (for Excel)"), ("json", "JSON", "JSON")]),
               O("header", "bool", "Первая строка — заголовок", "First row is a header", True)],
         live=True, ai_default=True, keys="таблица markdown csv html ascii excel table")
def fmt_table(ctx):
    import csv
    import io
    rows = parse_delimited(ctx.need_text())
    if not rows:
        raise UtilError("Не нашёл таблицу", "No table found")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    to, header = ctx.o["to"], ctx.o["header"]
    if to == "md":
        head = rows[0] if header else [f"#{i + 1}" for i in range(width)]
        body = rows[1:] if header else rows
        esc = lambda v: v.replace("|", "\\|")  # noqa: E731
        out = ["| " + " | ".join(esc(h) for h in head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
        out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in body]
        return {"text": "\n".join(out), "lang": "markdown"}
    if to == "html":
        from html import escape
        out = ["<table>"]
        if header:
            out.append("  <thead><tr>" + "".join(f"<th>{escape(c)}</th>" for c in rows[0]) + "</tr></thead>")
        out.append("  <tbody>")
        out += ["    <tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in (rows[1:] if header else rows)]
        out += ["  </tbody>", "</table>"]
        return {"text": "\n".join(out), "lang": "html"}
    if to == "ascii":
        widths = [max(len(str(r[i])) for r in rows) for i in range(width)]
        line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        out = [line]
        for n, r in enumerate(rows):
            out.append("| " + " | ".join(str(c).ljust(w) for c, w in zip(r, widths)) + " |")
            if n == 0 and header:
                out.append(line.replace("-", "="))
        out.append(line)
        return {"text": "\n".join(out)}
    if to == "json":
        if header:
            return {"text": json.dumps([dict(zip(rows[0], r)) for r in rows[1:]], ensure_ascii=False, indent=2), "lang": "json"}
        return {"text": json.dumps(rows, ensure_ascii=False, indent=2), "lang": "json"}
    buf = io.StringIO()
    csv.writer(buf, delimiter="\t" if to == "tsv" else ",", lineterminator="\n").writerows(rows)
    return buf.getvalue()

@utility("fmt.number", "format", "Форматирование чисел", "Number formatting",
         "Разряды (1 234 567,89), округление, экспонента, проценты, байты (1.5 ГБ), научная и инженерная запись.",
         "Thousands separators, rounding, scientific/engineering notation, percent, bytes (1.5 GB).",
         opts=[O("style", "choice", "Вид", "Style", "ru", choices=[("ru", "1 234 567,89", "1 234 567,89"), ("en", "1,234,567.89", "1,234,567.89"),
                                                            ("plain", "1234567.89", "1234567.89"), ("sci", "1.23e+06", "1.23e+06"),
                                                            ("eng", "1.23M (СИ-приставки)", "1.23M (SI prefixes)"), ("bytes", "Байты → КБ/МБ/ГБ", "Bytes → KB/MB/GB"),
                                                            ("percent", "Доля → %", "Fraction → %")]),
               O("digits", "int", "Знаков после запятой", "Decimals", 2, min=0, max=12)],
         live=True, keys="число формат разряды округление number format thousands")
def fmt_number(ctx):
    out = []
    for line in lines_of(ctx.need_text("Введите число", "Enter a number")):
        raw = line.strip().replace("\u00a0", "").replace(" ", "")
        if not raw:
            continue
        if raw.count(",") == 1 and raw.count(".") == 0:
            raw = raw.replace(",", ".")
        raw = raw.replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            raise UtilError(f"«{line}» — не число", f"“{line}” is not a number")
        d, style = int(ctx.o["digits"]), ctx.o["style"]
        if style in ("ru", "en", "plain"):
            s = f"{value:,.{d}f}"
            s = s.replace(",", "\u00a0").replace(".", ",") if style == "ru" else s.replace(",", "") if style == "plain" else s
        elif style == "sci":
            s = f"{value:.{d}e}"
        elif style == "eng":
            s = _si(value, d)
        elif style == "bytes":
            from .core import human_size
            s = human_size(value, ctx.en)
        else:
            s = f"{value * 100:.{d}f}%"
        out.append(s)
    return "\n".join(out)

def _si(value: float, d: int) -> str:
    if value == 0:
        return "0"
    exp = int(math.floor(math.log10(abs(value)) / 3))
    exp = max(-4, min(4, exp))
    prefix = {-4: "p", -3: "n", -2: "µ", -1: "m", 0: "", 1: "k", 2: "M", 3: "G", 4: "T"}[exp]
    return f"{value / 1000 ** exp:.{d}f}{prefix}"

# ── cron ────────────────────────────────────────────────────────────────────────────────────────────────────────────
_CRON_ALIASES = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
                 "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DAYS = "sun mon tue wed thu fri sat".split()

def _cron_field(spec: str, lo: int, hi: int, names=None) -> List[int]:
    values = set()
    for part in spec.lower().split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError("step")
        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _cron_val(x, names, lo), _cron_val(y, names, lo)
        else:
            a = _cron_val(part, names, lo)
            b = hi if step > 1 else a
        if a < lo or b > hi + (1 if names is _DAYS else 0) or a > b:
            raise ValueError(part)
        values.update(range(a, b + 1, step))
    if names is _DAYS and 7 in values:
        values.discard(7)
        values.add(0)
    return sorted(values)

def _cron_val(token: str, names, lo: int) -> int:
    if names and token[:3] in names:
        return names.index(token[:3]) + (1 if names is _MONTHS else 0)
    return int(token)

def cron_parse(expr: str):
    expr = _CRON_ALIASES.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) == 6:
        parts = parts[1:]
    if len(parts) != 5:
        raise UtilError("В cron-выражении 5 полей: минута час день месяц день_недели", "A cron expression has 5 fields: minute hour day month weekday")
    try:
        return (_cron_field(parts[0], 0, 59), _cron_field(parts[1], 0, 23), _cron_field(parts[2], 1, 31),
                _cron_field(parts[3], 1, 12, _MONTHS), _cron_field(parts[4], 0, 6, _DAYS), parts)
    except (ValueError, IndexError) as exc:
        raise UtilError(f"Ошибка в поле cron: {exc}", f"Bad cron field: {exc}")

def cron_next(expr: str, start: datetime, count: int) -> List[datetime]:
    mins, hours, days, months, wdays, parts = cron_parse(expr)
    dom_any, dow_any = parts[2] in ("*", "?"), parts[4] in ("*", "?")
    out, t = [], start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = start + timedelta(days=366 * 5)
    while len(out) < count and t < limit:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        dom_ok, dow_ok = t.day in days, (t.isoweekday() % 7) in wdays
        day_ok = (dom_ok and dow_ok) if (dom_any or dow_any) else (dom_ok or dow_ok)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute not in mins:
            t += timedelta(minutes=1)
            continue
        out.append(t)
        t += timedelta(minutes=1)
    return out

def _cron_human(parts: List[str], en: bool) -> str:
    m, h, dom, mon, dow = parts
    bits = []
    if m.startswith("*/"):
        bits.append(f"every {m[2:]} min" if en else f"каждые {m[2:]} мин")
    elif m == "*":
        bits.append("every minute" if en else "каждую минуту")
    if h == "*" and m.isdigit():
        bits.append(f"at minute {m} of every hour" if en else f"в {m} мин каждого часа")
    elif h.startswith("*/"):
        bits.append(f"every {h[2:]} h" if en else f"каждые {h[2:]} ч")
    elif h != "*" and m.isdigit() and all(x.isdigit() for x in h.split(",")):
        times = ", ".join(f"{int(x):02d}:{int(m):02d}" for x in h.split(","))
        bits.append(f"at {times}" if en else f"в {times}")
    elif h != "*":
        bits.append(f"hours {h}" if en else f"часы {h}")
    if dom != "*" and dom != "?":
        bits.append(f"on day {dom} of the month" if en else f"{dom} числа")
    if mon != "*":
        names = calendar.month_abbr[1:] if en else ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
        pretty = re.sub(r"\d+", lambda x: names[(int(x.group(0)) - 1) % 12], mon)
        bits.append(("in " if en else "в месяцы: ") + pretty)
    if dow != "*" and dow != "?":
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] if en else ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]
        pretty = re.sub(r"\d", lambda x: names[int(x.group(0)) % 7], dow)
        bits.append(("on " if en else "по дням: ") + pretty)
    return ", ".join(bits) or expr_any(en)

def expr_any(en: bool) -> str:
    return "every minute" if en else "каждую минуту"

@utility("dev.cron", "dev", "Cron: расшифровать и следующие запуски", "Cron: explain and next runs",
         "Объясняет cron-выражение по-человечески и показывает ближайшие запуски.",
         "Explains a cron expression in plain words and lists the next runs.",
         opts=[O("count", "int", "Сколько запусков показать", "Runs to show", 8, min=1, max=50)],
         live=True, ai_default=True, placeholder="*/15 9-18 * * 1-5", keys="cron крон расписание crontab schedule")
def dev_cron(ctx):
    expr = ctx.need_text("Введите cron-выражение, например */15 9-18 * * 1-5", "Enter a cron expression, e.g. */15 9-18 * * 1-5").strip()
    parts = cron_parse(expr)[5]
    runs = cron_next(expr, datetime.now(), int(ctx.o["count"]))
    wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"] if not ctx.en else ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {"info": [[ctx.tr("Смысл", "Meaning"), _cron_human(parts, ctx.en)], [ctx.tr("Поля", "Fields"), " | ".join(parts)]],
            "text": "\n".join(f"{t:%Y-%m-%d %H:%M} ({wd[t.weekday()]})" for t in runs) or ctx.tr("Нет запусков в ближайшие 5 лет", "No runs in the next 5 years")}

# ── chmod ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("dev.chmod", "dev", "chmod: 755 ↔ rwxr-xr-x", "chmod: 755 ↔ rwxr-xr-x",
         "Права доступа Unix: восьмеричные ↔ символьные, с объяснением кто что может.",
         "Unix permissions: octal ↔ symbolic, with who-can-do-what.",
         live=True, placeholder="755", keys="chmod права unix permissions rwx")
def dev_chmod(ctx):
    value = ctx.need_text("Введите 755 или rwxr-xr-x", "Enter 755 or rwxr-xr-x").strip()
    if re.fullmatch(r"[0-7]{3,4}", value):
        digits = value[-3:]
        special = int(value[0]) if len(value) == 4 else 0
    elif re.fullmatch(r"[-dlrwxsStT]{9,10}", value):
        sym = value[-9:]
        digits = "".join(str((4 if sym[i] == "r" else 0) + (2 if sym[i + 1] == "w" else 0) + (1 if sym[i + 2] in "xst" else 0)) for i in (0, 3, 6))
        special = (4 if sym[2] in "sS" else 0) + (2 if sym[5] in "sS" else 0) + (1 if sym[8] in "tT" else 0)
    else:
        raise UtilError("Нужно 3–4 цифры 0–7 или 9 символов rwx", "Use 3–4 digits 0–7 or 9 rwx characters")
    sym = ""
    for d in digits:
        n = int(d)
        sym += ("r" if n & 4 else "-") + ("w" if n & 2 else "-") + ("x" if n & 1 else "-")
    sym_l = list(sym)
    if special & 4:
        sym_l[2] = "s" if sym_l[2] == "x" else "S"
    if special & 2:
        sym_l[5] = "s" if sym_l[5] == "x" else "S"
    if special & 1:
        sym_l[8] = "t" if sym_l[8] == "x" else "T"
    sym = "".join(sym_l)
    who = [ctx.tr("Владелец", "Owner"), ctx.tr("Группа", "Group"), ctx.tr("Остальные", "Others")]
    rows = []
    for i, d in enumerate(digits):
        n = int(d)
        can = [w for bit, w in ((4, ctx.tr("чтение", "read")), (2, ctx.tr("запись", "write")), (1, ctx.tr("запуск", "execute"))) if n & bit]
        rows.append([who[i], ", ".join(can) or ctx.tr("ничего", "nothing")])
    octal = (str(special) if special else "") + digits
    return {"info": [[ctx.tr("Восьмеричная", "Octal"), octal], [ctx.tr("Символьная", "Symbolic"), sym], ["chmod", f"chmod {octal} file"]] + rows}

# ── HTTP status / MIME / user agent ─────────────────────────────────────────────────────────────────────────────────
HTTP = {100: ("Continue", "Продолжай"), 101: ("Switching Protocols", "Смена протокола"), 200: ("OK", "Успешно"), 201: ("Created", "Создано"),
        202: ("Accepted", "Принято в обработку"), 204: ("No Content", "Нет содержимого"), 206: ("Partial Content", "Частичное содержимое"),
        301: ("Moved Permanently", "Перемещено навсегда"), 302: ("Found", "Временно перемещено"), 303: ("See Other", "Смотри другой адрес"),
        304: ("Not Modified", "Не изменилось (кэш)"), 307: ("Temporary Redirect", "Временный редирект с тем же методом"),
        308: ("Permanent Redirect", "Постоянный редирект с тем же методом"), 400: ("Bad Request", "Неверный запрос"),
        401: ("Unauthorized", "Нужна авторизация"), 402: ("Payment Required", "Нужна оплата"), 403: ("Forbidden", "Доступ запрещён"),
        404: ("Not Found", "Не найдено"), 405: ("Method Not Allowed", "Метод не разрешён"), 406: ("Not Acceptable", "Неприемлемый формат"),
        408: ("Request Timeout", "Истекло время запроса"), 409: ("Conflict", "Конфликт"), 410: ("Gone", "Удалено навсегда"),
        411: ("Length Required", "Нужна длина"), 412: ("Precondition Failed", "Условие не выполнено"), 413: ("Content Too Large", "Слишком большой запрос"),
        414: ("URI Too Long", "Слишком длинный адрес"), 415: ("Unsupported Media Type", "Неподдерживаемый тип данных"),
        416: ("Range Not Satisfiable", "Диапазон недостижим"), 418: ("I'm a teapot", "Я чайник"), 422: ("Unprocessable Content", "Данные не прошли проверку"),
        425: ("Too Early", "Слишком рано"), 426: ("Upgrade Required", "Нужно обновить протокол"), 428: ("Precondition Required", "Нужно условие"),
        429: ("Too Many Requests", "Слишком много запросов (лимит)"), 431: ("Request Header Fields Too Large", "Слишком большие заголовки"),
        451: ("Unavailable For Legal Reasons", "Недоступно по юридическим причинам"), 500: ("Internal Server Error", "Внутренняя ошибка сервера"),
        501: ("Not Implemented", "Не реализовано"), 502: ("Bad Gateway", "Плохой шлюз (ошибка вышестоящего сервера)"),
        503: ("Service Unavailable", "Сервис недоступен (перегрузка/обслуживание)"), 504: ("Gateway Timeout", "Шлюз не дождался ответа"),
        505: ("HTTP Version Not Supported", "Версия HTTP не поддерживается"), 507: ("Insufficient Storage", "Недостаточно места"),
        508: ("Loop Detected", "Обнаружен цикл"), 511: ("Network Authentication Required", "Нужна авторизация в сети"),
        520: ("Unknown Error (Cloudflare)", "Неизвестная ошибка (Cloudflare)"), 521: ("Web Server Is Down (Cloudflare)", "Сервер выключен (Cloudflare)"),
        522: ("Connection Timed Out (Cloudflare)", "Сервер не ответил (Cloudflare)"), 524: ("A Timeout Occurred (Cloudflare)", "Таймаут (Cloudflare)")}

@utility("dev.http", "dev", "Коды ответов HTTP", "HTTP status codes",
         "Что значит 403, 429 или 502 — и что с этим делать. Можно искать по слову.",
         "What 403, 429 or 502 means — and what to do. Search by word too.",
         live=True, input="text", placeholder="429", keys="http статус код ответа 404 500 status code")
def dev_http(ctx):
    q = (ctx.text or "").strip().lower()
    rows = []
    for code, (en, ru) in HTTP.items():
        if not q or q in str(code) or q in en.lower() or q in ru.lower() or (len(q) == 3 and q.endswith("xx") and str(code)[0] == q[0]):
            rows.append([code, en, ru if not ctx.en else ""])
    if not rows:
        raise UtilError("Такого кода нет в справочнике", "No such code in the reference")
    cols = ["Code", "Name"] if ctx.en else ["Код", "Название", "Значение"]
    return {"table": table(cols, [r[:2] if ctx.en else r for r in rows])}

_MAGIC = [(b"\x89PNG\r\n\x1a\n", "PNG", "image/png"), (b"\xff\xd8\xff", "JPEG", "image/jpeg"), (b"GIF87a", "GIF", "image/gif"),
          (b"GIF89a", "GIF", "image/gif"), (b"%PDF-", "PDF", "application/pdf"), (b"PK\x03\x04", "ZIP / DOCX / XLSX / JAR", "application/zip"),
          (b"Rar!\x1a\x07", "RAR", "application/vnd.rar"), (b"7z\xbc\xaf\x27\x1c", "7-Zip", "application/x-7z-compressed"),
          (b"\x1f\x8b", "GZIP", "application/gzip"), (b"BZh", "BZIP2", "application/x-bzip2"), (b"\xfd7zXZ\x00", "XZ", "application/x-xz"),
          (b"MZ", "Windows EXE/DLL", "application/vnd.microsoft.portable-executable"), (b"\x7fELF", "ELF (Linux)", "application/x-elf"),
          (b"ID3", "MP3 (ID3)", "audio/mpeg"), (b"\xff\xfb", "MP3", "audio/mpeg"), (b"OggS", "OGG", "audio/ogg"), (b"fLaC", "FLAC", "audio/flac"),
          (b"\x1aE\xdf\xa3", "Matroska / WebM", "video/x-matroska"), (b"BM", "BMP", "image/bmp"), (b"\x00\x00\x01\x00", "ICO", "image/x-icon"),
          (b"II*\x00", "TIFF", "image/tiff"), (b"MM\x00*", "TIFF", "image/tiff"), (b"SQLite format 3\x00", "SQLite", "application/vnd.sqlite3"),
          (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "MS Office 97–2003 (DOC/XLS/MSG)", "application/x-ole-storage"),
          (b"{\\rtf", "RTF", "application/rtf"), (b"wOFF", "WOFF", "font/woff"), (b"wOF2", "WOFF2", "font/woff2"), (b"\x00\x01\x00\x00", "TrueType", "font/ttf"),
          (b"OTTO", "OpenType", "font/otf"), (b"PAR1", "Parquet", "application/vnd.apache.parquet"), (b"8BPS", "Photoshop PSD", "image/vnd.adobe.photoshop")]

@utility("dev.mime", "dev", "Тип файла и MIME", "File type and MIME",
         "По файлу — настоящий тип по «магическим байтам» (даже если расширение врёт); по тексту — MIME для расширения и наоборот.",
         "For a file — its real type from magic bytes (even with a wrong extension); for text — the MIME of an extension and back.",
         input="any", live=True, placeholder=".webp", keys="mime тип файла расширение magic bytes сигнатура file type")
def dev_mime(ctx):
    if ctx.files:
        f = ctx.files[0]
        head = f.data[:64]
        found = next(((name, mime) for sig, name, mime in _MAGIC if head.startswith(sig)), None)
        if head[:4] == b"RIFF":
            found = {b"WEBP": ("WebP", "image/webp"), b"WAVE": ("WAV", "audio/wav"), b"AVI ": ("AVI", "video/x-msvideo")}.get(head[8:12], ("RIFF", "application/octet-stream"))
        if head[4:8] == b"ftyp":
            brand = head[8:12].decode("latin-1")
            found = ("HEIC/AVIF" if brand in ("heic", "heix", "avif", "mif1") else "MP4/MOV/M4A", f"video/mp4 ({brand})")
        if not found and f.data[:5].lower() in (b"<?xml", b"<svg ") or b"<svg" in head:
            found = ("SVG / XML", "image/svg+xml")
        if not found:
            try:
                f.data[:4096].decode("utf-8")
                found = (ctx.tr("Текст (UTF-8)", "Text (UTF-8)"), "text/plain")
            except UnicodeDecodeError:
                found = (ctx.tr("Неизвестный двоичный", "Unknown binary"), "application/octet-stream")
        by_ext = guess_mime(f.name)
        mismatch = by_ext.split("/")[0] != found[1].split("/")[0] and by_ext != "application/octet-stream"
        info = [[ctx.tr("Файл", "File"), f.name], [ctx.tr("Определено по содержимому", "Detected from content"), found[0]],
                ["MIME", found[1]], [ctx.tr("MIME по расширению", "MIME by extension"), by_ext],
                [ctx.tr("Первые байты", "First bytes"), head[:16].hex(" ").upper()]]
        return {"info": info, "note": ctx.tr("⚠ Расширение не соответствует содержимому", "⚠ The extension does not match the content") if mismatch else ""}
    q = ctx.need_text("Введите расширение (.webp) или MIME (image/webp)", "Enter an extension (.webp) or a MIME type").strip().lower()
    import mimetypes
    if "/" in q:
        exts = mimetypes.guess_all_extensions(q) or [e for e in (".webp", ".avif", ".opus", ".m4a", ".mkv", ".md", ".yaml") if guess_mime("x" + e) == q]
        return {"info": [["MIME", q], [ctx.tr("Расширения", "Extensions"), ", ".join(exts) or "—"]]}
    ext = q if q.startswith(".") else "." + q
    return {"info": [[ctx.tr("Расширение", "Extension"), ext], ["MIME", guess_mime("file" + ext)]]}

@utility("dev.useragent", "dev", "Разбор User-Agent", "User-Agent parser",
         "Браузер, версия, движок, ОС и тип устройства по строке User-Agent.",
         "Browser, version, engine, OS and device type from a User-Agent string.",
         live=True, keys="user agent юзер агент браузер ua")
def dev_useragent(ctx):
    ua = ctx.need_text("Вставьте строку User-Agent", "Paste a User-Agent string")
    def find(rx):
        m = re.search(rx, ua)
        return m.group(1) if m else ""
    browser = "—"
    for name, rx in (("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"), ("Opera", r"OPR/([\d.]+)"), ("Yandex Browser", r"YaBrowser/([\d.]+)"),
                     ("Samsung Internet", r"SamsungBrowser/([\d.]+)"), ("Vivaldi", r"Vivaldi/([\d.]+)"), ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
                     ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"), ("Safari", r"Version/([\d.]+).*Safari"), ("Electron", r"Electron/([\d.]+)"),
                     ("curl", r"curl/([\d.]+)"), ("Python", r"python-requests/([\d.]+)"), ("Googlebot", r"Googlebot/([\d.]+)"), ("YandexBot", r"YandexBot/([\d.]+)")):
        v = find(rx)
        if v:
            browser = f"{name} {v}"
            break
    os_name = "—"
    for name, rx in (("Windows 10/11", r"Windows NT (10\.0)"), ("Windows 8.1", r"Windows NT (6\.3)"), ("Windows 7", r"Windows NT (6\.1)"),
                     ("Android", r"Android ([\d.]+)"), ("iOS", r"(?:iPhone|iPad) OS ([\d_]+)"), ("macOS", r"Mac OS X ([\d_.]+)"),
                     ("ChromeOS", r"CrOS \w+ ([\d.]+)"), ("Linux", r"(Linux)")):
        v = find(rx)
        if v:
            os_name = f"{name} {v.replace('_', '.')}" if name not in ("Linux", "Windows 10/11", "Windows 8.1", "Windows 7") else name
            break
    engine = "Blink" if "Chrome/" in ua and "Edge/" not in ua else "Gecko" if "Gecko/" in ua and "like Gecko" not in ua else "WebKit" if "AppleWebKit" in ua else "—"
    device = ctx.tr("Бот", "Bot") if re.search(r"bot|crawl|spider|slurp", ua, re.I) else ctx.tr("Планшет", "Tablet") if re.search(r"iPad|Tablet", ua) else \
        ctx.tr("Телефон", "Phone") if re.search(r"Mobi|iPhone|Android", ua) else ctx.tr("Компьютер", "Desktop")
    return {"info": [[ctx.tr("Браузер", "Browser"), browser], [ctx.tr("Движок", "Engine"), engine], [ctx.tr("ОС", "OS"), os_name], [ctx.tr("Устройство", "Device"), device]]}

# ── safe calculator ─────────────────────────────────────────────────────────────────────────────────────────────────
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.BitXor: operator.xor,
        ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.LShift: operator.lshift, ast.RShift: operator.rshift}
_FUNCS = {name: getattr(math, name) for name in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "log", "log10", "log2", "exp",
                                                   "floor", "ceil", "factorial", "gcd", "hypot", "degrees", "radians", "sinh", "cosh", "tanh")}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max, "ln": math.log, "lcm": math.lcm, "comb": math.comb, "perm": math.perm})
_CONST = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "π": math.pi}

def safe_eval(expr: str, names: Dict[str, float] = None) -> float:
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-").replace(",", ".")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)
    tree = ast.parse(expr, mode="eval")
    env = dict(_CONST, **(names or {}))

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10000:
                raise UtilError("Слишком большая степень", "Exponent too large")
            if isinstance(node.op, (ast.LShift,)) and right > 4096:
                raise UtilError("Слишком большой сдвиг", "Shift too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            args = [ev(a) for a in node.args]
            if node.func.id == "factorial" and args and args[0] > 5000:
                raise UtilError("Слишком большой факториал", "Factorial too large")
            return _FUNCS[node.func.id](*args)
        raise UtilError("Допустимы числа, + − × ÷ ^ %, скобки и функции sqrt, sin, log…", "Only numbers, + - * / ^ %, brackets and functions like sqrt, sin, log")
    return ev(tree)

@utility("dev.calc", "dev", "Калькулятор выражений", "Expression calculator",
         "Считает выражения построчно: 2^10, sqrt(2)*pi, 15% от суммы, sin(radians(30)); можно переменные x = 5.",
         "Evaluates expressions line by line: 2^10, sqrt(2)*pi, 15%, sin(radians(30)); variables like x = 5 work.",
         live=True, ai_default=True, placeholder="x = 1500\nx * 1.2\nsqrt(2) * pi", keys="калькулятор посчитать вычислить calculator math")
def dev_calc(ctx):
    names: Dict[str, float] = {}
    out = []
    for line in lines_of(ctx.need_text("Введите выражение", "Enter an expression")):
        src = line.split("#", 1)[0].strip()
        if not src:
            out.append("")
            continue
        m = re.fullmatch(r"([A-Za-zА-Яа-я_]\w*)\s*=\s*(.+)", src)
        try:
            value = safe_eval(m.group(2) if m else src, names)
        except UtilError:
            raise
        except ZeroDivisionError:
            out.append(f"{src} → " + ctx.tr("деление на ноль", "division by zero"))
            continue
        except (SyntaxError, TypeError, ValueError, OverflowError) as exc:
            out.append(f"{src} → " + ctx.tr(f"ошибка: {exc}", f"error: {exc}"))
            continue
        if m:
            names[m.group(1)] = value
        names["ans"] = value
        pretty = f"{value:.12g}" if isinstance(value, float) else str(value)
        out.append(f"{src} = {pretty}")
    return "\n".join(out)

# ── JSON → code ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _pascal(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name) or ["Item"]
    out = "".join(p[:1].upper() + p[1:] for p in parts)
    return ("N" + out) if out[0].isdigit() else out

def _merge_types(values: List[Any]) -> Any:
    dicts = [v for v in values if isinstance(v, dict)]
    if dicts:
        merged: Dict[str, Any] = {}
        for d in dicts:
            for k, v in d.items():
                merged.setdefault(k, []).append(v)
        return {"__obj__": {k: _merge_types(v) for k, v in merged.items()}, "__opt__": {k for k in merged if any(k not in d for d in dicts)},
                "__null__": any(v is None for v in values)}
    kinds = {type(v).__name__ for v in values if v is not None}
    if "list" in kinds:
        return {"__list__": _merge_types([x for v in values if isinstance(v, list) for x in v])}
    if kinds == {"int"}:
        return "int"
    if kinds <= {"int", "float"} and kinds:
        return "float"
    if kinds == {"bool"}:
        return "bool"
    if kinds == {"str"}:
        return "str"
    return "any"

def json_to_code(value: Any, lang: str, root: str = "Root") -> str:
    classes: List[str] = []
    seen: Dict[str, int] = {}

    def tname(shape, name):
        if isinstance(shape, dict) and "__obj__" in shape:
            return emit(shape, name)
        if isinstance(shape, dict) and "__list__" in shape:
            inner = tname(shape["__list__"], name.rstrip("s") or name + "Item")
            return {"ts": f"{inner}[]", "py": f"List[{inner}]", "pyd": f"List[{inner}]", "go": f"[]{inner}", "cs": f"List<{inner}>",
                    "kt": f"List<{inner}>", "java": f"List<{inner}>", "rust": f"Vec<{inner}>"}[lang]
        return {"ts": {"int": "number", "float": "number", "bool": "boolean", "str": "string", "any": "unknown"},
                "py": {"int": "int", "float": "float", "bool": "bool", "str": "str", "any": "Any"},
                "pyd": {"int": "int", "float": "float", "bool": "bool", "str": "str", "any": "Any"},
                "go": {"int": "int64", "float": "float64", "bool": "bool", "str": "string", "any": "interface{}"},
                "cs": {"int": "long", "float": "double", "bool": "bool", "str": "string", "any": "object"},
                "kt": {"int": "Long", "float": "Double", "bool": "Boolean", "str": "String", "any": "Any?"},
                "java": {"int": "long", "float": "double", "bool": "boolean", "str": "String", "any": "Object"},
                "rust": {"int": "i64", "float": "f64", "bool": "bool", "str": "String", "any": "serde_json::Value"}}[lang][shape]

    def emit(shape, name):
        cname = _pascal(name)
        if cname in seen:
            seen[cname] += 1
            cname = f"{cname}{seen[cname]}"
        else:
            seen[cname] = 1
        fields = shape["__obj__"]
        rows = []
        for key, sub in fields.items():
            t = tname(sub, key)
            opt = key in shape["__opt__"]
            ident = re.sub(r"\W", "_", key)
            if ident and ident[0].isdigit():
                ident = "_" + ident
            if lang == "ts":
                safe = key if re.fullmatch(r"[A-Za-z_$][\w$]*", key) else json.dumps(key)
                rows.append(f"  {safe}{'?' if opt else ''}: {t};")
            elif lang in ("py", "pyd"):
                rows.append(f"    {ident}: {'Optional[' + t + '] = None' if opt else t}")
            elif lang == "go":
                rows.append(f"\t{_pascal(key)} {t} `json:\"{key}{',omitempty' if opt else ''}\"`")
            elif lang == "cs":
                rows.append(f"    [JsonPropertyName(\"{key}\")] public {t}{'?' if opt else ''} {_pascal(key)} {{ get; set; }}")
            elif lang == "kt":
                rows.append(f"    @SerialName(\"{key}\") val {ident}: {t}{'? = null' if opt else ''},")
            elif lang == "java":
                rows.append(f"    @JsonProperty(\"{key}\") public {t} {ident};")
            elif lang == "rust":
                rows.append(f"    #[serde(rename = \"{key}\")]\n    pub {ident.lower()}: {'Option<' + t + '>' if opt else t},")
        if lang == "ts":
            body = f"export interface {cname} {{\n" + "\n".join(rows) + "\n}"
        elif lang == "py":
            body = f"@dataclass\nclass {cname}:\n" + ("\n".join(sorted(rows, key=lambda r: "= None" in r)) or "    pass")
        elif lang == "pyd":
            body = f"class {cname}(BaseModel):\n" + ("\n".join(rows) or "    pass")
        elif lang == "go":
            body = f"type {cname} struct {{\n" + "\n".join(rows) + "\n}"
        elif lang == "cs":
            body = f"public class {cname}\n{{\n" + "\n".join(rows) + "\n}"
        elif lang == "kt":
            body = f"@Serializable\ndata class {cname}(\n" + "\n".join(rows) + "\n)"
        elif lang == "java":
            body = f"public class {cname} {{\n" + "\n".join(rows) + "\n}"
        else:
            body = f"#[derive(Debug, Serialize, Deserialize)]\npub struct {cname} {{\n" + "\n".join(rows) + "\n}"
        classes.append(body)
        return cname

    shape = _merge_types([value])
    if isinstance(shape, dict) and "__list__" in shape:
        shape = shape["__list__"]
    if not (isinstance(shape, dict) and "__obj__" in shape):
        raise UtilError("Нужен JSON-объект или массив объектов", "Need a JSON object or an array of objects")
    emit(shape, root)
    head = {"py": "from dataclasses import dataclass\nfrom typing import Any, List, Optional\n\n\n",
            "pyd": "from typing import Any, List, Optional\n\nfrom pydantic import BaseModel\n\n\n",
            "go": "package main\n\n", "cs": "using System.Collections.Generic;\nusing System.Text.Json.Serialization;\n\n",
            "kt": "import kotlinx.serialization.SerialName\nimport kotlinx.serialization.Serializable\n\n",
            "java": "import com.fasterxml.jackson.annotation.JsonProperty;\nimport java.util.List;\n\n",
            "rust": "use serde::{Deserialize, Serialize};\n\n"}.get(lang, "")
    sep = "\n\n\n" if lang in ("py", "pyd") else "\n\n"
    return head + sep.join(classes)

def json_schema(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "object", "properties": {k: json_schema(v) for k, v in value.items()}, "required": list(value)}
    if isinstance(value, list):
        return {"type": "array", "items": json_schema(value[0]) if value else {}}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string"}

@utility("dev.json_code", "dev", "JSON → типы для кода", "JSON → code types",
         "По примеру JSON создаёт TypeScript-интерфейсы, Python dataclass/Pydantic, Go, C#, Kotlin, Java, Rust или JSON Schema.",
         "Generates TypeScript interfaces, Python dataclasses/Pydantic, Go, C#, Kotlin, Java, Rust or JSON Schema from sample JSON.",
         opts=[O("lang", "choice", "Язык", "Language", "ts", choices=[("ts", "TypeScript", "TypeScript"), ("py", "Python dataclass", "Python dataclass"),
                                                                ("pyd", "Pydantic", "Pydantic"), ("go", "Go", "Go"), ("cs", "C#", "C#"), ("kt", "Kotlin", "Kotlin"),
                                                                ("java", "Java", "Java"), ("rust", "Rust (serde)", "Rust (serde)"), ("schema", "JSON Schema", "JSON Schema")]),
               O("root", "text", "Имя корневого типа", "Root type name", "Root")],
         live=True, ai_default=True, keys="json typescript interface типы pydantic go struct schema")
def dev_json_code(ctx):
    value, _ = load_json_lenient(ctx.need_text("Вставьте пример JSON", "Paste sample JSON"))
    if ctx.o["lang"] == "schema":
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": ctx.o["root"] or "Root", **json_schema(value)}
        return {"text": json.dumps(schema, ensure_ascii=False, indent=2), "lang": "json"}
    lang_name = {"ts": "typescript", "py": "python", "pyd": "python", "go": "go", "cs": "csharp", "kt": "kotlin", "java": "java", "rust": "rust"}[ctx.o["lang"]]
    return {"text": json_to_code(value, ctx.o["lang"], ctx.o["root"] or "Root"), "lang": lang_name}

def _dir_opt_escape():
    return O("dir", "choice", "Направление", "Direction", "encode", choices=[("encode", "Экранировать", "Escape"), ("decode", "Убрать экранирование", "Unescape")])

# ── escape ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("dev.escape", "dev", "Экранирование строк", "String escaping",
         "Подготовить строку для JSON, JavaScript, Python, SQL, регулярки, shell, CSV, XML или C — и обратно.",
         "Prepare a string for JSON, JavaScript, Python, SQL, regex, shell, CSV, XML or C — and back.",
         opts=[O("for", "choice", "Для чего", "Target", "json", choices=[("json", "JSON", "JSON"), ("js", "JavaScript", "JavaScript"), ("python", "Python", "Python"),
                                                                    ("sql", "SQL", "SQL"), ("regex", "Регулярное выражение", "Regex"), ("shell", "Shell (bash)", "Shell (bash)"),
                                                                    ("powershell", "PowerShell", "PowerShell"), ("csv", "CSV", "CSV"), ("xml", "XML", "XML"), ("c", "C/C++/Java", "C/C++/Java")]),
               _dir_opt_escape()],
         live=True, ai_default=True, keys="экранирование escape кавычки строка string")
def dev_escape(ctx):
    import html as _html
    text, target, decode = ctx.need_text(), ctx.o["for"], ctx.o["dir"] == "decode"
    if decode:
        if target in ("json", "js", "c", "python"):
            try:
                return json.loads('"' + text.strip().strip('"').replace("\\'", "'") + '"')
            except json.JSONDecodeError:
                return text.encode("latin-1", "backslashreplace").decode("unicode_escape")
        if target == "sql":
            return text.strip().strip("'").replace("''", "'")
        if target == "regex":
            return re.sub(r"\\(.)", r"\1", text)
        if target == "csv":
            t = text.strip()
            return t[1:-1].replace('""', '"') if t.startswith('"') and t.endswith('"') else t
        if target == "xml":
            return _html.unescape(text)
        if target == "shell":
            try:
                return " ".join(shlex.split(text))
            except ValueError as exc:
                raise UtilError(str(exc), str(exc))
        return text.replace("`'", "'").replace("''", "'").strip("'")
    if target == "json":
        return json.dumps(text, ensure_ascii=False)
    if target == "js":
        return "'" + json.dumps(text, ensure_ascii=False)[1:-1].replace("\\\"", "\"").replace("'", "\\'") + "'"
    if target == "python":
        return repr(text)
    if target == "c":
        return json.dumps(text, ensure_ascii=True)
    if target == "sql":
        return "'" + text.replace("'", "''") + "'"
    if target == "regex":
        return re.escape(text)
    if target == "shell":
        return shlex.quote(text)
    if target == "powershell":
        return "'" + text.replace("'", "''") + "'"
    if target == "csv":
        return '"' + text.replace('"', '""') + '"' if re.search(r'[",\n;]', text) else text
    return _html.escape(text, quote=True).replace("&#x27;", "&apos;")

# ── regex tester ────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("dev.regex", "dev", "Тестер регулярных выражений", "Regex tester",
         "Проверяет регулярку на тексте: все совпадения, группы и позиции. Защищён от «зависающих» выражений.",
         "Tests a regex on a text: every match, groups and positions. Guarded against catastrophic patterns.",
         opts=[O("pattern", "text", "Регулярное выражение", "Pattern", ""), O("icase", "bool", "Без учёта регистра (i)", "Ignore case (i)", False),
               O("multiline", "bool", "Многострочный (m)", "Multiline (m)", False), O("dotall", "bool", "Точка = любой символ (s)", "Dot matches all (s)", False)],
         live=True, ai_default=True, keys="regex регулярка регулярное выражение тестер regexp")
def dev_regex(ctx):
    pattern, text = ctx.o["pattern"], ctx.text or ""
    if not pattern:
        raise UtilError("Введите регулярное выражение", "Enter a pattern")
    flags = (re.I if ctx.o["icase"] else 0) | (re.M if ctx.o["multiline"] else 0) | (re.S if ctx.o["dotall"] else 0)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        raise UtilError(f"Ошибка в выражении: {exc}", f"Pattern error: {exc}")
    found: List[Any] = []
    error: List[str] = []

    def work():
        try:
            for i, m in enumerate(rx.finditer(text)):
                if i >= 1000:
                    break
                found.append(m)
        except Exception as exc:  # noqa: BLE001
            error.append(str(exc))
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(3.0)
    if th.is_alive():
        raise UtilError("Выражение считается слишком долго (катастрофический возврат). Упростите вложенные квантификаторы.",
                        "The pattern takes too long (catastrophic backtracking). Simplify nested quantifiers.")
    rows = []
    for i, m in enumerate(found, 1):
        groups = " · ".join(f"{k}={v!r}" for k, v in m.groupdict().items()) if m.groupdict() else " · ".join(repr(g) for g in m.groups())
        rows.append([i, m.group(0), f"{m.start()}–{m.end()}", groups])
    out = {"note": ctx.tr(f"Совпадений: {len(found)}", f"Matches: {len(found)}")}
    if rows:
        out["table"] = table(["#", ctx.tr("Совпадение", "Match"), ctx.tr("Позиция", "Position"), ctx.tr("Группы", "Groups")], rows)
    return out

# ── URL parse / build ───────────────────────────────────────────────────────────────────────────────────────────────
@utility("dev.url_parse", "dev", "Разобрать ссылку", "Parse a URL",
         "Схема, домен, порт, путь, параметры запроса таблицей и якорь; убирает UTM-метки.",
         "Scheme, host, port, path, query parameters as a table and the fragment; strips UTM tags.",
         opts=[O("clean", "bool", "Показать ссылку без UTM и трекинга", "Show the link without UTM/tracking", True)],
         live=True, ai_default=True, keys="ссылка url параметры query utm разобрать parse")
def dev_url_parse(ctx):
    raw = ctx.need_text("Вставьте ссылку", "Paste a link").strip()
    u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
    params = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    info = [[ctx.tr("Схема", "Scheme"), u.scheme], [ctx.tr("Домен", "Host"), u.hostname or ""], [ctx.tr("Порт", "Port"), u.port or ctx.tr("по умолчанию", "default")],
            [ctx.tr("Путь", "Path"), urllib.parse.unquote(u.path) or "/"], [ctx.tr("Якорь", "Fragment"), urllib.parse.unquote(u.fragment)]]
    if u.username:
        info.append([ctx.tr("Пользователь", "User"), u.username])
    out: Dict[str, Any] = {"info": info}
    if params:
        out["table"] = table([ctx.tr("Параметр", "Parameter"), ctx.tr("Значение", "Value")], [[k, v] for k, v in params])
    if ctx.o["clean"]:
        tracking = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|ysclid|mc_cid|mc_eid|_openstat|from|ref|igshid|si|spm)$", re.I)
        kept = [(k, v) for k, v in params if not tracking.match(k)]
        out["text"] = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(kept), u.fragment))
    return out

# ── curl → code ─────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_curl(command: str) -> Dict[str, Any]:
    command = command.replace("\\\r\n", " ").replace("\\\n", " ").replace("^\r\n", " ").replace("^\n", " ").strip()
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise UtilError(f"Не удалось разобрать команду: {exc}", f"Cannot parse the command: {exc}")
    if argv and argv[0].lower() in ("curl", "curl.exe"):
        argv = argv[1:]
    req: Dict[str, Any] = {"method": "", "url": "", "headers": {}, "data": None, "form": [], "auth": None, "insecure": False, "follow": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        if a in ("-X", "--request"):
            req["method"] = nxt.upper(); i += 1
        elif a in ("-H", "--header"):
            k, _, v = nxt.partition(":"); req["headers"][k.strip()] = v.strip(); i += 1
        elif a in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--json"):
            req["data"] = (req["data"] + "&" + nxt) if req["data"] else nxt
            if a == "--json":
                req["headers"].setdefault("Content-Type", "application/json")
            i += 1
        elif a == "--data-urlencode":
            k, _, v = nxt.partition("=")
            piece = f"{k}={urllib.parse.quote(v)}" if _ else urllib.parse.quote(nxt)
            req["data"] = (req["data"] + "&" + piece) if req["data"] else piece; i += 1
        elif a in ("-F", "--form"):
            req["form"].append(nxt); i += 1
        elif a in ("-u", "--user"):
            req["auth"] = nxt; i += 1
        elif a in ("-A", "--user-agent"):
            req["headers"]["User-Agent"] = nxt; i += 1
        elif a in ("-b", "--cookie"):
            req["headers"]["Cookie"] = nxt; i += 1
        elif a in ("-e", "--referer"):
            req["headers"]["Referer"] = nxt; i += 1
        elif a in ("-k", "--insecure"):
            req["insecure"] = True
        elif a in ("-L", "--location"):
            req["follow"] = True
        elif a == "--url":
            req["url"] = nxt; i += 1
        elif a in ("-o", "--output", "-m", "--max-time", "--connect-timeout", "-w", "--write-out", "-x", "--proxy", "--retry"):
            i += 1
        elif not a.startswith("-") and not req["url"]:
            req["url"] = a
        i += 1
    if not req["url"]:
        raise UtilError("В команде нет адреса", "The command has no URL")
    req["method"] = req["method"] or ("POST" if req["data"] is not None or req["form"] else "GET")
    return req

def _curl_python(req, lib):
    lines = [f"import {lib}", ""]
    if req["headers"]:
        lines.append("headers = " + json.dumps(req["headers"], ensure_ascii=False, indent=4))
    body_kw = ""
    data = req["data"]
    if data is not None:
        try:
            parsed = json.loads(data)
            lines.append("payload = " + json.dumps(parsed, ensure_ascii=False, indent=4).replace("true", "True").replace("false", "False").replace("null", "None"))
            body_kw = ", json=payload"
        except (json.JSONDecodeError, TypeError):
            lines.append(f"data = {data!r}")
            body_kw = ", data=data"
    if req["form"]:
        files = {}
        for item in req["form"]:
            k, _, v = item.partition("=")
            files[k] = (f"open({v[1:]!r}, 'rb')" if v.startswith("@") else repr(v))
        lines.append("files = {" + ", ".join(f"{k!r}: {v}" for k, v in files.items()) + "}")
        body_kw += ", files=files"
    args = f"{req['url']!r}" + (", headers=headers" if req["headers"] else "") + body_kw
    if req["auth"]:
        user, _, pw = req["auth"].partition(":")
        args += f", auth=({user!r}, {pw!r})"
    if req["insecure"]:
        args += ", verify=False"
    if lib == "requests" and not req["follow"] and req["method"] in ("GET",):
        pass
    if lib == "httpx" and req["follow"]:
        args += ", follow_redirects=True"
    lines += ["", f"response = {lib}.request({req['method']!r}, {args}, timeout=30)", "print(response.status_code)", "print(response.text)"]
    return "\n".join(lines)

def _curl_fetch(req):
    opts = [f"  method: {json.dumps(req['method'])}"]
    headers = dict(req["headers"])
    if req["auth"]:
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(req["auth"].encode()).decode()
    if headers:
        opts.append("  headers: " + json.dumps(headers, ensure_ascii=False, indent=4).replace("\n", "\n  "))
    if req["data"] is not None:
        try:
            json.loads(req["data"])
            opts.append("  body: JSON.stringify(" + req["data"].strip() + ")")
        except json.JSONDecodeError:
            opts.append("  body: " + json.dumps(req["data"], ensure_ascii=False))
    return (f"const response = await fetch({json.dumps(req['url'])}, {{\n" + ",\n".join(opts) + "\n});\n"
            "console.log(response.status);\nconsole.log(await response.text());")

def _curl_go(req):
    body = "nil"
    pre = ""
    if req["data"] is not None:
        pre = f"\tbody := strings.NewReader({json.dumps(req['data'], ensure_ascii=False)})\n"
        body = "body"
    hdr = "".join(f"\treq.Header.Set({json.dumps(k)}, {json.dumps(v, ensure_ascii=False)})\n" for k, v in req["headers"].items())
    if req["auth"]:
        user, _, pw = req["auth"].partition(":")
        hdr += f"\treq.SetBasicAuth({json.dumps(user)}, {json.dumps(pw)})\n"
    return ("package main\n\nimport (\n\t\"fmt\"\n\t\"io\"\n\t\"net/http\"\n" + ("\t\"strings\"\n" if pre else "") + ")\n\nfunc main() {\n" + pre +
            f"\treq, err := http.NewRequest({json.dumps(req['method'])}, {json.dumps(req['url'])}, {body})\n\tif err != nil {{\n\t\tpanic(err)\n\t}}\n" + hdr +
            "\tresp, err := http.DefaultClient.Do(req)\n\tif err != nil {\n\t\tpanic(err)\n\t}\n\tdefer resp.Body.Close()\n"
            "\tdata, _ := io.ReadAll(resp.Body)\n\tfmt.Println(resp.StatusCode, string(data))\n}")

def _curl_php(req):
    lines = ["<?php", f"$ch = curl_init({json.dumps(req['url'])});", "curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);",
             f"curl_setopt($ch, CURLOPT_CUSTOMREQUEST, {json.dumps(req['method'])});"]
    if req["headers"]:
        lines.append("curl_setopt($ch, CURLOPT_HTTPHEADER, [" + ", ".join(json.dumps(f"{k}: {v}", ensure_ascii=False) for k, v in req["headers"].items()) + "]);")
    if req["data"] is not None:
        lines.append(f"curl_setopt($ch, CURLOPT_POSTFIELDS, {json.dumps(req['data'], ensure_ascii=False)});")
    if req["auth"]:
        lines.append(f"curl_setopt($ch, CURLOPT_USERPWD, {json.dumps(req['auth'])});")
    if req["follow"]:
        lines.append("curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);")
    lines += ["$response = curl_exec($ch);", "curl_close($ch);", "echo $response;"]
    return "\n".join(lines)

@utility("dev.curl", "dev", "curl → код (Python, JS, Go, PHP)", "curl → code (Python, JS, Go, PHP)",
         "Превращает команду curl (например, «Copy as cURL» из браузера) в код на Python requests/httpx, JavaScript fetch, Go или PHP.",
         "Turns a curl command (e.g. the browser's “Copy as cURL”) into Python requests/httpx, JavaScript fetch, Go or PHP code.",
         opts=[O("lang", "choice", "Язык", "Language", "requests", choices=[("requests", "Python requests", "Python requests"), ("httpx", "Python httpx", "Python httpx"),
                                                                     ("fetch", "JavaScript fetch", "JavaScript fetch"), ("go", "Go net/http", "Go net/http"), ("php", "PHP cURL", "PHP cURL")])],
         live=True, ai_default=True, keys="curl requests fetch код конвертер copy as curl")
def dev_curl(ctx):
    req = parse_curl(ctx.need_text("Вставьте команду curl", "Paste a curl command"))
    lang = ctx.o["lang"]
    code = {"requests": lambda: _curl_python(req, "requests"), "httpx": lambda: _curl_python(req, "httpx"), "fetch": lambda: _curl_fetch(req),
            "go": lambda: _curl_go(req), "php": lambda: _curl_php(req)}[lang]()
    return {"text": code, "lang": {"requests": "python", "httpx": "python", "fetch": "javascript", "go": "go", "php": "php"}[lang]}

# ── .gitignore ──────────────────────────────────────────────────────────────────────────────────────────────────────
_GITIGNORE = {
    "python": "__pycache__/\n*.py[cod]\n*.egg-info/\n.eggs/\nbuild/\ndist/\n.venv/\nvenv/\nenv/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.coverage\nhtmlcov/\n.ipynb_checkpoints/\n.env",
    "node": "node_modules/\nnpm-debug.log*\nyarn-debug.log*\nyarn-error.log*\npnpm-debug.log*\n.pnpm-store/\ndist/\nbuild/\n.next/\n.nuxt/\n.cache/\ncoverage/\n.env\n.env.local",
    "java": "*.class\n*.jar\n*.war\ntarget/\nbuild/\n.gradle/\nout/\n*.log",
    "dotnet": "bin/\nobj/\n*.user\n*.suo\n.vs/\n*.nupkg\nTestResults/\npackages/",
    "go": "*.exe\n*.test\n*.out\nvendor/\ngo.work",
    "rust": "target/\nCargo.lock\n**/*.rs.bk",
    "unity": "[Ll]ibrary/\n[Tt]emp/\n[Oo]bj/\n[Bb]uild/\n[Bb]uilds/\n[Ll]ogs/\n[Uu]ser[Ss]ettings/\n*.csproj\n*.sln\n*.pidb\n*.apk",
    "android": "*.apk\n*.aab\n.gradle/\nbuild/\nlocal.properties\n.idea/\n*.iml\ncaptures/",
    "windows": "Thumbs.db\nehthumbs.db\nDesktop.ini\n$RECYCLE.BIN/\n*.lnk",
    "macos": ".DS_Store\n.AppleDouble\n.LSOverride\n._*\n.Spotlight-V100\n.Trashes",
    "linux": "*~\n.fuse_hidden*\n.directory\n.Trash-*\n.nfs*",
    "jetbrains": ".idea/\n*.iml\n*.ipr\n*.iws\nout/",
    "vscode": ".vscode/*\n!.vscode/settings.json\n!.vscode/tasks.json\n!.vscode/launch.json\n!.vscode/extensions.json\n*.code-workspace",
    "secrets": ".env\n.env.*\n!.env.example\n*.pem\n*.key\n*.p12\n*.pfx\nsecrets.*\ncredentials.json",
}

@utility("dev.gitignore", "dev", ".gitignore по шаблонам", ".gitignore from templates",
         "Собирает .gitignore для Python, Node, Java, .NET, Go, Rust, Unity, Android, ОС и редакторов.",
         "Builds a .gitignore for Python, Node, Java, .NET, Go, Rust, Unity, Android, OSes and editors.",
         input="none", opts=[O(name, "bool", name.capitalize() if name not in ("dotnet", "vscode", "macos", "jetbrains") else
                               {"dotnet": ".NET", "vscode": "VS Code", "macos": "macOS", "jetbrains": "JetBrains"}[name],
                               name.capitalize() if name not in ("dotnet", "vscode", "macos", "jetbrains") else
                               {"dotnet": ".NET", "vscode": "VS Code", "macos": "macOS", "jetbrains": "JetBrains"}[name],
                               name in ("python", "windows", "vscode", "secrets")) for name in _GITIGNORE],
         live=True, keys="gitignore git игнор шаблон")
def dev_gitignore(ctx):
    parts = [f"# {name}\n{_GITIGNORE[name]}" for name in _GITIGNORE if ctx.o.get(name)]
    if not parts:
        raise UtilError("Отметьте хотя бы один шаблон", "Tick at least one template")
    text = "\n\n".join(parts) + "\n"
    ctx.out_text(".gitignore", text)
    return {"text": text}
