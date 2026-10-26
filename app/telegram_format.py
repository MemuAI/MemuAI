"""Markdown -> Telegram HTML rendering for the MemuAI Telegram bot.

Telegram understands only a small HTML subset (b, i, u, s, a, code, pre,
blockquote, tg-spoiler), so model answers written in Markdown are translated
instead of being sent raw:

* fenced code keeps its language (Telegram shows a copy button on ``<pre>``);
* tables become aligned monospace grids, or "cards" when too wide for a phone;
* nested bullet/numbered/task lists keep their depth;
* LaTeX ($…$, $$…$$, \\(…\\), \\[…\\]) is turned into readable Unicode;
* long quotes collapse into ``<blockquote expandable>``.

``split_telegram_html`` cuts rendered HTML into messages that fit the 4096
character limit without ever leaving a tag open: tags that are open at a cut
are closed at the end of one message and reopened at the start of the next.
``render_partial`` renders a half-written streaming answer (an unclosed code
fence is closed temporarily) so drafts never show raw Markdown.
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import List, Optional, Tuple

CODE_MARK = "\x01"
INLINE_MARK = "\x02"

MAX_LENGTH = 3800
TABLE_GRID_MAX_WIDTH = 46

_FENCE_OPEN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})[ \t]*([\w+.#-]*)[^\n`]*$")
_TAGS = ("b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code",
         "pre", "blockquote", "span", "tg-spoiler", "tg-emoji")

def _escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)

def utf16_len(text: str) -> int:
    """Length as Telegram counts it (astral characters such as emoji take 2)."""
    return len(str(text or "").encode("utf-16-le")) // 2

_TEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "cdot": "·", "cdots": "⋯", "ldots": "…", "dots": "…", "div": "÷",
    "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝", "infty": "∞",
    "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "oint": "∮", "partial": "∂",
    "nabla": "∇", "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔", "mapsto": "↦",
    "implies": "⇒", "iff": "⇔", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂",
    "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩",
    "emptyset": "∅", "varnothing": "∅", "forall": "∀", "exists": "∃", "neg": "¬",
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "oplus": "⊕", "otimes": "⊗",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "degree": "°", "circ": "∘",
    "prime": "′", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ",
    "mathbb{R}": "ℝ", "mathbb{N}": "ℕ", "mathbb{Z}": "ℤ", "mathbb{Q}": "ℚ", "mathbb{C}": "ℂ",
    "lim": "lim", "log": "log", "ln": "ln", "sin": "sin", "cos": "cos", "tan": "tan",
    "exp": "exp", "max": "max", "min": "min", "det": "det",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
}
_SUPERSCRIPT = str.maketrans("0123456789+-=()niax", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵃˣ")
_SUBSCRIPT = str.maketrans("0123456789+-=()aeoxijn", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₙ")

def _tex_group(source: str, start: int) -> Tuple[str, int]:
    """Return the {…} group (or single character) starting at ``start``."""
    while start < len(source) and source[start] == " ":
        start += 1
    if start >= len(source):
        return "", start
    if source[start] != "{":
        if source[start] == "\\":
            match = re.match(r"\\[A-Za-z]+", source[start:])
            if match:
                return match.group(0), start + len(match.group(0))
        return source[start], start + 1
    depth, index = 0, start
    while index < len(source):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index], index + 1
        index += 1
    return source[start + 1:], len(source)

def _script(value: str, table: dict, marker: str) -> str:
    converted = value.translate(table)
    allowed = {chr(code) for code in table.values()}
    if converted and all(ch in allowed for ch in converted):
        return converted
    return marker + ("(" + value + ")" if len(value) > 1 else value)

def tex_to_unicode(source: str) -> str:
    """Best-effort readable rendering of a LaTeX formula (Telegram has no math)."""
    value = str(source or "").strip()
    value = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\b\s*", "", value)
    value = re.sub(r"\\(?:displaystyle|textstyle|limits|nolimits)\b", "", value)
    value = re.sub(r"\\(?:,|;|:|!|quad|qquad)", " ", value)
    value = value.replace("\\\\", "\n").replace("&", " ")
    value = re.sub(r"\\mathbb\{([RNZQC])\}", lambda m: _TEX_SYMBOLS["mathbb{" + m.group(1) + "}"], value)
    out: List[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            match = re.match(r"\\([A-Za-z]+|.)", value[index:])
            name = match.group(1) if match else ""
            index += len(name) + 1
            if name in ("frac", "dfrac", "tfrac"):
                top, index = _tex_group(value, index)
                bottom, index = _tex_group(value, index)
                top, bottom = tex_to_unicode(top), tex_to_unicode(bottom)
                wrap = lambda part: part if re.fullmatch(r"[\w.]+", part) else "(" + part + ")"
                out.append(wrap(top) + "/" + wrap(bottom))
            elif name == "sqrt":
                degree = ""
                if index < len(value) and value[index] == "[":
                    close = value.find("]", index)
                    degree, index = value[index + 1:close], close + 1
                body, index = _tex_group(value, index)
                body = tex_to_unicode(body)
                root = {"3": "∛", "4": "∜"}.get(degree.strip(), "√")
                out.append(root + (body if re.fullmatch(r"\w+", body) else "(" + body + ")"))
            elif name in ("text", "mathrm", "mathbf", "mathit", "operatorname", "textbf",
                          "mathsf", "mathtt", "boldsymbol", "mbox", "textit", "mathcal"):
                body, index = _tex_group(value, index)
                out.append(tex_to_unicode(body) if name != "text" else body)
            elif name in ("vec", "hat", "bar", "overline", "tilde", "dot"):
                body, index = _tex_group(value, index)
                mark = {"vec": "\u20d7", "hat": "\u0302", "bar": "\u0305", "overline": "\u0305",
                        "tilde": "\u0303", "dot": "\u0307"}[name]
                out.append(tex_to_unicode(body) + mark)
            elif name in ("begin", "end"):
                _, index = _tex_group(value, index)
            elif name in _TEX_SYMBOLS:
                out.append(_TEX_SYMBOLS[name])
            elif name in ("{", "}", "%", "$", "#", "_"):
                out.append(name)
            else:
                out.append(name)
            continue
        if char in "^_":
            body, index = _tex_group(value, index + 1)
            body = tex_to_unicode(body)
            out.append(_script(body, _SUPERSCRIPT if char == "^" else _SUBSCRIPT, char))
            continue
        if char in "{}":
            index += 1
            continue
        out.append(char)
        index += 1
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()

def _looks_like_math(body: str) -> bool:
    body = body.strip()
    if not body or body != body.strip("$"):
        return False
    if re.fullmatch(r"[\d.,\s]+", body):
        return False
    return bool(re.search(r"[\\^_=+<>]|\b[a-zA-Z]\b", body))

_EMPHASIS_TAG = re.compile(r"<(/?)(b|i|s|tg-spoiler)>")

def _repair_nesting(value: str) -> str:
    """Emphasis regexes can produce crossed tags (``<i>a<b>b</i>c</b>`` from
    stray asterisks); Telegram rejects the whole message for that. Close and
    reopen the inner tags so the markup nests. Text is already escaped here,
    so every tag matched is one we generated."""
    out: List[str] = []
    stack: List[str] = []
    position = 0
    for match in _EMPHASIS_TAG.finditer(value):
        out.append(value[position:match.start()])
        position = match.end()
        closing, name = bool(match.group(1)), match.group(2)
        if not closing:
            stack.append(name)
            out.append(match.group(0))
            continue
        if name not in stack:
            continue
        index = len(stack) - 1 - stack[::-1].index(name)
        inner = stack[index + 1:]
        out.extend(f"</{tag}>" for tag in reversed(inner))
        out.append(match.group(0))
        out.extend(f"<{tag}>" for tag in inner)
        del stack[index]
    out.append(value[position:])
    out.extend(f"</{tag}>" for tag in reversed(stack))
    return re.sub(r"<(b|i|s|tg-spoiler)></\1>", "", "".join(out))

def _inline(text: str) -> str:
    """Convert inline Markdown of a single line/paragraph to Telegram HTML."""
    spans: List[str] = []

    def keep(rendered: str) -> str:
        spans.append(rendered)
        return f"{INLINE_MARK}{len(spans) - 1}{INLINE_MARK}"

    source = str(text or "")

    source = re.sub(r"(`+)([^`]+?)\1", lambda m: keep("<code>" + _escape(m.group(2)) + "</code>"), source)

    source = re.sub(r"\\\((.+?)\\\)", lambda m: keep("<code>" + _escape(tex_to_unicode(m.group(1))) + "</code>"), source)
    source = re.sub(r"(?<![\\$\w])\$(?!\s)([^$\n]{1,200}?)(?<!\s)\$(?![\w$])",
                    lambda m: keep("<code>" + _escape(tex_to_unicode(m.group(1))) + "</code>")
                    if _looks_like_math(m.group(1)) else m.group(0), source)

    def link(match: re.Match, image: bool = False) -> str:
        label, href = match.group(1), match.group(2).strip()
        if href.startswith("<") and href.endswith(">"):
            href = href[1:-1]
        if not re.match(r"(?i)^(https?|tg|mailto):", href):
            return keep(_escape(("🖼 " if image else "") + (label or href)))
        href = html.escape(href, quote=True).replace("&#x27;", "'")
        text_part = _inline(label) if label else _escape(href)
        return keep(f'<a href="{href}">{("🖼 " if image else "") + text_part}</a>')

    source = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", lambda m: link(m, True), source)
    source = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link, source)
    source = re.sub(r"<(https?://[^\s>]+)>", lambda m: keep(
        f'<a href="{html.escape(m.group(1), quote=True)}">{_escape(m.group(1))}</a>'), source)

    value = _escape(source)
    value = re.sub(r"\|\|(?=\S)(.+?)(?<=\S)\|\|", r"<tg-spoiler>\1</tg-spoiler>", value)
    value = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", value)
    value = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<b><i>\1</i></b>", value)
    value = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<b>\1</b>", value)
    value = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", value)
    value = re.sub(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", r"<i>\1</i>", value)
    value = _repair_nesting(value)
    for _ in range(3):
        if INLINE_MARK not in value:
            break
        value = re.sub(f"{INLINE_MARK}(\\d+){INLINE_MARK}", lambda m: spans[int(m.group(1))], value)
    return value

def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width

def _pad(text: str, width: int, align: str) -> str:
    gap = max(0, width - _display_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        return " " * (gap // 2) + text + " " * (gap - gap // 2)
    return text + " " * gap

def _is_divider_row(cells: List[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell.strip() or "x") for cell in cells)

def _cells(line: str) -> List[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", value)]

def _plain(text: str) -> str:
    """Strip Markdown emphasis for monospace table cells (not escaped)."""
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text or ""))
    value = re.sub(r"(\*\*|__|~~|`|(?<!\w)\*|(?<!\w)_(?=\S))", "", value)
    value = re.sub(r"\$([^$]+)\$", lambda m: tex_to_unicode(m.group(1)), value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    return value.strip()

def _table(lines: List[str]) -> str:
    raw_rows = [_cells(line) for line in lines]
    aligns: List[str] = []
    for row in raw_rows:
        if _is_divider_row(row):
            aligns = ["center" if c.strip().startswith(":") and c.strip().endswith(":") else
                      "right" if c.strip().endswith(":") else "left" for c in row]
            break
    rows = [row for row in raw_rows if not _is_divider_row(row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [[_plain(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
    aligns = (aligns + ["left"] * width)[:width]
    widths = [max(_display_width(row[index]) for row in rows) for index in range(width)]
    total = sum(widths) + 3 * (width - 1)
    if total <= TABLE_GRID_MAX_WIDTH or width == 1:
        out: List[str] = []
        for index, row in enumerate(rows):
            out.append(" │ ".join(_pad(row[column], widths[column], aligns[column])
                                  for column in range(width)).rstrip())
            if index == 0 and len(rows) > 1:
                out.append("─┼─".join("─" * widths[column] for column in range(width)))
        return "<pre>" + _escape("\n".join(out)) + "</pre>"

    header, body = rows[0], rows[1:] or [rows[0]]
    cards: List[str] = []
    for row in body:
        first = row[0] or "—"
        lines_out = ["▫️ <b>" + _escape(first) + "</b>"]
        for column in range(1, width):
            if row[column]:
                label = header[column] if len(rows) > 1 else ""
                lines_out.append(("   " + _escape(label) + ": " if label else "   ") + _escape(row[column]))
        cards.append("\n".join(lines_out))
    return "\n".join(cards)

def _list_depth(indent: str, unit: int) -> int:
    width = len(indent.replace("\t", "    "))
    return min(5, width // max(1, unit))

def _code_block(language: str, body: str) -> str:
    code = _escape(body.strip("\n"))
    language = re.sub(r"[^\w+.#-]", "", language or "")[:32]
    if language:
        return f'<pre><code class="language-{_escape(language)}">{code}</code></pre>'
    return f"<pre>{code}</pre>"

def markdown_to_telegram_html(text: str, *, partial: bool = False) -> str:
    """Render model Markdown as Telegram HTML.

    With ``partial`` an unterminated code fence/math block (a streaming
    answer cut mid-way) is rendered as if it were closed.
    """
    blocks: List[str] = []
    lines_in = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: List[str] = []
    index = 0

    while index < len(lines_in):
        line = lines_in[index]
        fence = _FENCE_OPEN.match(line)
        if fence:
            marker, language = fence.group(2), fence.group(3)
            body: List[str] = []
            index += 1
            closed = False
            while index < len(lines_in):
                if re.match(r"^\s{0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$", lines_in[index]):
                    closed = True
                    index += 1
                    break
                body.append(lines_in[index])
                index += 1
            if not closed and not partial and not body:
                lines.append(line)
                continue
            blocks.append(_code_block(language, "\n".join(body)))
            lines.append(f"{CODE_MARK}{len(blocks) - 1}{CODE_MARK}")
            continue
        math = re.match(r"^\s*(\$\$|\\\[)(.*)$", line)
        if math:
            closer = "$$" if math.group(1) == "$$" else "\\]"
            rest = math.group(2)
            if closer in rest:
                formula, _, tail = rest.partition(closer)
                blocks.append("<pre>" + _escape(tex_to_unicode(formula)) + "</pre>")
                lines.append(f"{CODE_MARK}{len(blocks) - 1}{CODE_MARK}" + (" " + tail if tail.strip() else ""))
                index += 1
                continue
            body = [rest]
            probe = index + 1
            closed = False
            while probe < len(lines_in) and probe - index < 60:
                if closer in lines_in[probe]:
                    before, _, _ = lines_in[probe].partition(closer)
                    body.append(before)
                    closed = True
                    break
                body.append(lines_in[probe])
                probe += 1
            if closed or partial:
                blocks.append("<pre>" + _escape(tex_to_unicode("\n".join(body))) + "</pre>")
                lines.append(f"{CODE_MARK}{len(blocks) - 1}{CODE_MARK}")
                index = probe + 1
                continue
        lines.append(line)
        index += 1

    indents = [len(m.group(1)) for m in (re.match(r"^( +)(?:[-*+]|\d{1,3}[.)])\s", l) for l in lines) if m]
    unit = 2 if any(value % 4 for value in indents) or not indents else 4
    if indents and min(indents) >= 4 and all(value % 4 == 0 for value in indents):
        unit = 4

    out: List[str] = []
    index = 0
    bullets = ("•", "◦", "▪", "▫", "‣", "⁃")
    pad = "\u00a0\u00a0\u00a0"
    while index < len(lines):
        line = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if "|" in line and "-" in following and _is_divider_row(_cells(following)):
            table: List[str] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table.append(lines[index])
                index += 1
            rendered = _table(table)
            if rendered:
                out.append(rendered)
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
        if heading:
            title = _inline(heading.group(2).strip().rstrip("#").strip())
            level = len(heading.group(1))
            if out and out[-1].strip():
                out.append("")
            out.append(("<b><u>" + title + "</u></b>") if level == 1 else "<b>" + title + "</b>")
            index += 1
            continue
        if re.match(r"^\s{0,3}>\s?", line):
            quote: List[str] = []
            while index < len(lines) and re.match(r"^\s{0,3}>\s?", lines[index]):
                quote.append(re.sub(r"^\s{0,3}>\s?", "", lines[index]))
                index += 1
            alert = re.match(r"^\[!(\w+)\]\s*$", quote[0]) if quote else None
            if alert:
                icon = {"NOTE": "ℹ️", "TIP": "💡", "IMPORTANT": "❗", "WARNING": "⚠️",
                        "CAUTION": "⛔"}.get(alert.group(1).upper(), "ℹ️")
                quote[0] = icon + " **" + alert.group(1).capitalize() + "**"
            body = "\n".join(_inline(row) for row in quote)
            tag = "blockquote expandable" if len(quote) > 8 or len(body) > 900 else "blockquote"
            out.append(f"<{tag}>{body}</blockquote>")
            continue
        if re.fullmatch(r"\s{0,3}([-*_])(\s*\1){2,}\s*", line):
            out.append("──────────")
            index += 1
            continue
        task = re.match(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$", line)
        if task:
            mark = "✅" if task.group(2).lower() == "x" else "⬜"
            out.append(pad * _list_depth(task.group(1), unit) + mark + " " + _inline(task.group(3)))
            index += 1
            continue
        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            depth = _list_depth(bullet.group(1), unit)
            out.append(pad * depth + bullets[depth % len(bullets)] + " " + _inline(bullet.group(2)))
            index += 1
            continue
        numbered = re.match(r"^(\s*)(\d{1,3})([.)])\s+(.*)$", line)
        if numbered:
            depth = _list_depth(numbered.group(1), unit)
            out.append(pad * depth + numbered.group(2) + ". " + _inline(numbered.group(4)))
            index += 1
            continue
        out.append(_inline(line))
        index += 1
    result = "\n".join(out)
    result = re.sub(f"{CODE_MARK}(\\d+){CODE_MARK}", lambda m: blocks[int(m.group(1))], result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()

def render_partial(text: str) -> str:
    """Render a streaming (unfinished) answer: open fences are closed."""
    value = str(text or "")

    value = re.sub(r"(\*\*|__|~~|\[[^\]]*|`)$", "", value)
    return markdown_to_telegram_html(_close_open_emphasis(value), partial=True)

def _close_open_emphasis(value: str) -> str:
    """Round 26: ``**Сравнение Pyth`` mid-stream was shown with raw asterisks
    until the closing marker arrived; close what the last paragraph opened."""
    if value.count("```") % 2:
        return value
    paragraph = value[value.rfind("\n\n") + 1:]
    if paragraph.count("`") % 2:
        return value + "`"
    plain = re.sub(r"`[^`]*`", "", paragraph)
    marks = [mark for mark in ("**", "~~") if plain.count(mark) % 2]
    marks.sort(key=plain.rfind, reverse=True)
    return value + "".join(marks)

_TOKEN = re.compile(r"<[^>]*>|&#?\w+;|\n|[ \t]+|[^<&\n \t]+|[<&]")

def _tag_name(tag: str) -> Tuple[str, bool]:
    match = re.match(r"^<\s*(/?)\s*([\w-]+)", tag)
    if not match:
        return "", False
    return match.group(2).lower(), bool(match.group(1))

def html_to_plain(text: str) -> str:
    """Telegram rejected the markup: keep the words, drop the tags."""
    value = re.sub(r"<br\s*/?>", "\n", str(text or ""), flags=re.I)
    value = re.sub(r'<a href="([^"]*)">([\s\S]*?)</a>',
                   lambda m: m.group(2) + ("" if html.unescape(m.group(1)) in m.group(2) else
                                           " (" + html.unescape(m.group(1)) + ")"), value)
    return html.unescape(re.sub(r"<[^>]+>", "", value))

def _drop_empty_elements(chunk: str) -> str:
    previous = None
    while previous != chunk:
        previous = chunk
        chunk = re.sub(r"<(\w[\w-]*)(?:\s[^>]*)?>(\s*)</\1>", r"\2", chunk)
    return chunk.strip("\n")

def split_telegram_html(text: str, limit: int = MAX_LENGTH) -> List[str]:
    """Split rendered HTML into valid messages of at most ``limit`` units.

    Tags open at a cut are closed in the first part and reopened (with the
    same attributes, e.g. the code language) in the next one. Cuts prefer a
    blank line, then a line break outside formatting, then any line break,
    then a space; an entity (``&amp;``) is never split.
    """
    value = str(text or "").strip()
    if not value:
        return ["—"]
    limit = max(64, int(limit))
    tokens: List[str] = []
    for token in _TOKEN.findall(value):
        if token.startswith("<") and len(token) > 1:
            name, _ = _tag_name(token)
            if name not in _TAGS:
                token = _escape(token)
                tokens.extend(_TOKEN.findall(token))
                continue
        if token in ("<", "&"):
            token = _escape(token)
        piece = limit // 3
        if utf16_len(token) > piece and not token.startswith("<"):
            tokens.extend(token[i:i + piece] for i in range(0, len(token), piece))
        else:
            tokens.append(token)

    def closing(state: List[Tuple[str, str]]) -> str:
        return "".join(f"</{name}>" for name, _ in reversed(state))

    def apply(state: List[Tuple[str, str]], token: str) -> List[Tuple[str, str]]:
        if not (token.startswith("<") and len(token) > 1):
            return state
        name, is_close = _tag_name(token)
        result = list(state)
        if is_close:
            for position in range(len(result) - 1, -1, -1):
                if result[position][0] == name:
                    del result[position:]
                    break
        elif name and not token.endswith("/>"):
            result.append((name, token))
        return result

    chunks: List[str] = []
    start_stack: List[Tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        stack = list(start_stack)
        prefix = "".join(opening for _, opening in stack)
        length = utf16_len(prefix)
        body: List[str] = []

        breaks: List[Tuple[int, List[Tuple[str, str]], int, int]] = []
        probe = index
        while probe < len(tokens):
            token = tokens[probe]
            after = apply(stack, token)
            size = utf16_len(token)
            if body and length + size + utf16_len(closing(after)) > limit:
                break
            body.append(token)
            length += size
            stack = after
            if token == "\n":
                previous = tokens[probe - 1] if probe > index else ""
                priority = 3 if previous == "\n" and not stack else 2 if not stack else 1
                breaks.append((probe + 1, list(stack), priority, length))
            elif not token.strip():
                in_pre = any(name in ("pre", "code") for name, _ in stack)
                breaks.append((probe + 1, list(stack), -1 if in_pre else 0, length))
            probe += 1
        if probe >= len(tokens):
            rendered = _drop_empty_elements(prefix + "".join(body).rstrip(" \t\n") + closing(stack))
            if rendered.strip():
                chunks.append(rendered)
            break
        chosen: Optional[Tuple[int, List[Tuple[str, str]], int, int]] = None
        for wanted in (3, 2, 1, 0, -1):
            candidates = [item for item in breaks if item[2] == wanted and item[3] > limit * 0.45]
            if candidates:
                chosen = candidates[-1]
                break
        if chosen is None and breaks and breaks[-1][3] > limit * 0.2:
            chosen = breaks[-1]
        cut, cut_stack = (chosen[0], chosen[1]) if chosen else (probe, stack)
        head = "".join(tokens[index:cut]).rstrip(" \t\n")
        rendered = _drop_empty_elements(prefix + head + closing(cut_stack))
        if rendered.strip():
            chunks.append(rendered)
        index = cut
        if any(name in ("pre", "code") for name, _ in cut_stack):

            if index < len(tokens) and tokens[index] == "\n":
                index += 1
        else:
            while index < len(tokens) and not tokens[index].strip():
                index += 1
        start_stack = cut_stack
    return [chunk for chunk in chunks if html_to_plain(chunk).strip()] or [
        chunk for chunk in chunks if chunk.strip()] or ["—"]

_HEADING_LINE = re.compile(r"^\s{0,3}(#{1,6}\s+\S|\*\*[^*\n]+\*\*:?\s*$|__[^_\n]+__:?\s*$)")

def split_markdown_source(text: str, limit: int) -> Tuple[str, str, int]:
    """Cut a streaming Markdown answer: ``(head, carry, consumed)``.

    While streaming, an answer that outgrows one message gets its finished
    ``head`` sent as a permanent message. The cut is at a paragraph, then a
    line, then a word boundary. A code fence open at the cut is closed in the
    head, and ``carry`` (the reopening fence with its language) must be put in
    front of ``text[consumed:]`` to continue. ``consumed`` is 0 when no cut
    is needed.
    """
    value = str(text or "")
    if len(value) <= limit:
        return "", "", 0
    window = value[:limit]
    cut = window.rfind("\n\n")
    if cut < limit * 0.5:
        cut = window.rfind("\n")
    if cut < limit * 0.3:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = limit

    last_line = value[:cut].rstrip("\n").rsplit("\n", 1)[-1]
    if _HEADING_LINE.match(last_line):
        before = value[:cut].rstrip("\n").rfind("\n")
        if before >= limit * 0.3:
            cut = before
    head = value[:cut]
    consumed = cut
    while consumed < len(value) and value[consumed] == "\n":
        consumed += 1
    fence_lang: Optional[str] = None
    marker = ""
    for line in head.split("\n"):
        match = _FENCE_OPEN.match(line)
        if fence_lang is None and match:
            fence_lang, marker = match.group(3) or "", match.group(2)
        elif fence_lang is not None and re.match(r"^\s{0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$", line):
            fence_lang, marker = None, ""
    carry = ""
    if fence_lang is not None:
        head = head.rstrip("\n") + "\n" + marker
        carry = marker + fence_lang + "\n"
    return head, carry, consumed
