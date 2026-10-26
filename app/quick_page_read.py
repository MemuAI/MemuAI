"""Round 32: fast and complete reading of the browser tab for Quick Ask («перескажи страницу»).

Before Round 32 the page went through the generic ``web_fetch`` pipeline: one regex pass over the whole HTML
(menus, footers, cookie banners, «related» lists and all), cut at a fixed number of characters, re-fetched on
every question; a hanging site cost up to 25 s before the visible text of the tab was even tried, and a page
question without a detected tab fell through to a web search of the question itself.

This module is the page half of the fix (``quick_ask_api.read_page_content`` uses it):

* :func:`extract` — readability-style main content from raw HTML with the standard library only
  (``html.parser``): drops scripts, styles, SVG, canvases, iframes, forms, images (``alt`` optional),
  base64 blobs, navigation / header / footer / aside, hidden elements and ad / cookie / share / comment /
  «related» blocks; picks ``<article>`` / ``<main>`` / ``[role=main]`` or the densest text block; keeps
  headings, lists, tables, code and links compactly (Markdown).
* :func:`fit` — a token budget with smart truncation: the head, an outline of the omitted headings, the tail.
* :func:`fetch_html` — ONE direct HTTP fetch of the tab's URL through ``websearch.fetch_page(raw=True)``
  (public-address and redirect checks, 5 MB cap, short timeout). The user's cookies are not available, so a
  login/paywall page is detected by ``quick_ask_browser.page_mismatch`` and the visible text of the tab
  (UI Automation) is used instead.
* a small per-URL cache (:func:`cache_get` / :func:`cache_put`, 10 min) — follow-up questions about the same
  page, a ``read_page`` / ``web_fetch`` call of the model for that URL do not fetch again.

Pure functions, no Win32: unit-tested on Linux (``tests/round32_quick_test.py``).
"""
from __future__ import annotations

import html as _html
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urldefrag

PAGE_TOKENS = 8000

MAX_EXTRACT = 400_000
FETCH_TIMEOUT = 9.0
CACHE_TTL = 600.0
CACHE_MAX = 24

_DROP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "template", "object", "embed", "video",
              "audio", "picture", "source", "track", "map", "math", "form", "button", "select", "option",
              "input", "textarea", "label", "dialog", "nav", "aside", "footer", "menu", "head", "link", "meta"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "div", "section", "article", "main", "header", "figure", "figcaption", "blockquote", "ul", "ol",
          "li", "dl", "dt", "dd", "table", "tr", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "address",
          "details", "summary", "body", "html", "center"}
_JUNK = re.compile(
    r"(?:^|[\s_-])(?:ads?|adv|advert\w*|adsbygoogle|banner|cookies?|consent|gdpr|sidebar|side-bar|widget|comments?|"
    r"disqus|share|sharing|social|related|recommend\w*|promo\w*|newsletter|subscri\w*|popup|modal|overlay|breadcrumbs?|"
    r"footer|navbar|nav|menu|toolbar|pagination|pager|sponsor\w*|outbrain|taboola|teaser|rating|tags?|author-box|"
    r"skip-link|visually-hidden|sr-only|screen-reader\w*|print-only|"
    r"mw-editsection|navbox\w*|reflist|mw-references-wrap|catlinks|printfooter|mw-jump-link|toc|vector-toc|"
    r"noprint|metadata|hatnote|ambox|sistersitebox|authority-control)(?:$|[\s_-])", re.I)
_CITE = re.compile(r"\[\s*(?:\d{1,4}|[a-z]{1,2}|citation needed|nb \d+|note \d+)\s*\]", re.I)
_JUNK_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alert", "menu",
               "menubar", "toolbar", "tablist"}
_PRE_STRIP = re.compile(r"<(script|style|noscript|svg|template|iframe|canvas|math)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_DATA_URI = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=\s]{40,}")
_WS = re.compile(r"[ \t\r\f\v\u00a0\u200b]+")
_LOOKS_HTML = re.compile(r"<(?:!doctype|html|head|body|div|p|article|main|span|a|table|ul|h[1-6])\b", re.I)

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "junk", "text_len", "p_len")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_Node"]) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: List[Any] = []
        self.junk = 0
        self.text_len = 0
        self.p_len = 0

def _is_junk(tag: str, attrs: Dict[str, str]) -> int:
    """0 = content; 1 = always dropped (tag, hidden, role); 2 = dropped by its class/id (a guess: rescued when it
    turns out to hold most of the page's text, e.g. ``<div class="post comments-on">``)."""
    if tag in _DROP_TAGS:
        return 1
    if "hidden" in attrs or attrs.get("aria-hidden") == "true":
        return 1
    style = attrs.get("style", "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return 1
    if attrs.get("role", "").lower() in _JUNK_ROLES:
        return 1
    if tag in ("html", "body", "main", "article"):
        return 0
    marks = (attrs.get("class", "") + " " + attrs.get("id", "")).strip()
    return 2 if marks and _JUNK.search(marks) else 0

class _TreeBuilder(HTMLParser):
    """A forgiving, small DOM: enough structure for picking the main block and rendering Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root
        self.title = ""
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs_list: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            return
        attrs = {k.lower(): (v or "") for k, v in attrs_list if k}
        node = _Node(tag, attrs, self.cur)
        node.junk = _is_junk(tag, attrs)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs_list: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs_list if k}
        node = _Node(tag, attrs, self.cur)
        node.junk = _is_junk(tag, attrs)
        self.cur.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if data:
            self.cur.children.append(data)

def _measure(node: _Node, ignore_soft: bool = False) -> None:
    """Text length and «paragraph text» length of every subtree (iterative: deep pages must not recurse)."""
    order: List[_Node] = []
    stack = [node]
    while stack:
        item = stack.pop()
        order.append(item)
        stack.extend(child for child in item.children if isinstance(child, _Node))
    for item in reversed(order):
        if item.junk == 1 or (item.junk and not ignore_soft):
            item.text_len = item.p_len = 0
            continue
        text = sum(len(c.strip()) for c in item.children if isinstance(c, str))
        sub = [c for c in item.children if isinstance(c, _Node)]
        item.text_len = text + sum(c.text_len for c in sub)
        own_p = item.text_len if item.tag in ("p", "pre", "li", "blockquote", "td", "dd") else 0
        item.p_len = max(own_p, sum(c.p_len for c in sub) + (text if text > 80 else 0))

def _walk(node: _Node):
    stack = [node]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(reversed([c for c in item.children if isinstance(c, _Node)]))

def _pick_main(root: _Node) -> _Node:
    """``<article>`` / ``<main>`` / ``[role=main]`` / ``[itemprop=articleBody]`` with most of the text, else the
    block where the paragraph text concentrates (readability idea), else ``<body>``."""
    body = next((n for n in _walk(root) if n.tag == "body"), root)
    total = max(body.p_len, 1)
    candidates = []
    for node in _walk(body):
        if node.junk:
            continue
        hint = node.tag in ("article", "main") or node.attrs.get("role") == "main" \
            or "articlebody" in node.attrs.get("itemprop", "").lower()
        if hint and node.p_len >= 300:
            candidates.append(node)
    if candidates:
        best = max(candidates, key=lambda n: n.p_len)

        while True:
            inner = [n for n in candidates if n is not best and n.p_len >= 0.6 * best.p_len and _inside(n, best)]
            if not inner:
                break
            best = max(inner, key=lambda n: n.p_len)

        if best.p_len >= 0.4 * total:
            return _with_title(best, body)

    node = body
    while True:
        kids = [c for c in node.children if isinstance(c, _Node) and not c.junk]
        top = max(kids, key=lambda c: c.p_len, default=None)
        if top is None or top.p_len < 0.6 * max(node.p_len, 1) or top.p_len < 400:
            break
        node = top
    return _with_title(node, body) if node is not body else body

def _inside(node: _Node, outer: _Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent is outer:
            return True
        parent = parent.parent
    return False

def _with_title(node: _Node, body: _Node) -> _Node:
    """Keep the page's ``<h1>`` when the main block starts below it (common: title in a header, text in a div)."""
    if any(n.tag == "h1" for n in _walk(node)):
        return node
    h1 = next((n for n in _walk(body) if n.tag == "h1" and not n.junk), None)
    if h1 is None:
        return node
    wrapper = _Node("#main", {}, None)
    wrapper.children = [h1, node]
    return wrapper

class _Renderer:
    def __init__(self, base: str, *, links: bool = False, alt: bool = False, max_links: int = 120) -> None:
        self.base, self.links, self.alt, self.max_links = base, links, alt, max_links
        self.out: List[str] = []
        self.link_count = 0
        self.seen_links: Dict[str, int] = {}
        self.found: List[Tuple[str, str]] = []

    def inline(self, node: _Node) -> str:
        return "".join(self.one(child, node.tag) for child in node.children)

    def one(self, child: Any, parent_tag: str = "") -> str:
        """The inline text of one child (links/code/images/citations handled)."""
        if isinstance(child, str):
            return child
        if child.junk:
            return ""
        tag = child.tag
        if tag == "br":
            return "\n"
        if tag == "img":
            alt = _WS.sub(" ", child.attrs.get("alt", "")).strip()
            return f" [image: {alt[:120]}] " if self.alt and len(alt) > 3 else ""
        if tag == "a":
            raw = self.inline(child)
            link = self._link(_WS.sub(" ", raw).strip(), child.attrs.get("href", "").strip())

            return (" " if raw[:1].isspace() else "") + link + (" " if raw[-1:].isspace() else "")
        if tag == "code" and parent_tag != "pre":
            code = _WS.sub(" ", self.inline(child)).strip()
            return f"`{code}`" if code and "`" not in code else code
        if tag in ("sup", "sub"):
            inner = self.inline(child)
            if tag == "sup" and _CITE.fullmatch(_WS.sub(" ", inner).strip()):
                return ""
            return inner
        return self.inline(child) if tag not in _BLOCK else " " + self.inline(child) + " "

    def _link(self, text: str, href: str) -> str:
        if not text:
            return ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return text
        url = urljoin(self.base, href) if self.base else href
        if not url.startswith(("http://", "https://")) or len(url) > 300:
            return text
        if text == url or len(text) < 2 or url in self.seen_links:
            return text
        self.seen_links[url] = 1
        if len(self.found) < 400:
            self.found.append((text[:120], url))
        if not self.links or self.link_count >= self.max_links:
            return text
        self.link_count += 1
        return f"[{text}]({url})"

    def emit(self, text: str) -> None:
        text = text.strip("\n")
        if text.strip():
            self.out.append(text)

    def block(self, node: _Node, depth: int = 0, list_prefix: str = "", level: int = 0) -> None:

        if depth > 60:
            self.emit(_clean_line(self.inline(node)))
            return
        buffer: List[str] = []

        def flush() -> None:
            if buffer:
                text = _clean_line("".join(buffer))
                if text:
                    self.emit(list_prefix + text if list_prefix else text)
                buffer.clear()

        for child in node.children:
            if isinstance(child, str):
                buffer.append(child)
                continue
            if child.junk:
                continue
            tag = child.tag
            if tag not in _BLOCK and tag not in ("table",):
                if tag == "a" or not _contains_block(child):
                    buffer.append(self.one(child, node.tag))
                    continue

                flush()
                self.block(child, depth + 1, "", level)
                continue
            flush()
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = _clean_line(self.inline(child))
                if text:
                    self.emit("#" * int(tag[1]) + " " + text)
            elif tag == "pre":
                code = _html.unescape(_text_raw(child)).strip("\n")
                if code.strip():
                    self.emit("```\n" + code[:20000] + "\n```")
            elif tag in ("ul", "ol"):
                index = 0
                for item in child.children:
                    if isinstance(item, _Node) and item.tag == "li" and not item.junk:
                        index += 1
                        mark = f"{index}. " if tag == "ol" else "- "
                        self.block(item, depth + 1, "  " * min(level, 4) + mark, level + 1)
            elif tag == "li":
                self.block(child, depth + 1, "  " * min(level, 4) + "- ", level + 1)
            elif tag == "table":
                self.table(child)
            elif tag == "blockquote":
                inner = _Renderer(self.base, links=self.links, alt=self.alt, max_links=0)
                inner.block(child, depth + 1)
                for line in "\n".join(inner.out).splitlines():
                    if line.strip():
                        self.emit("> " + line)
            elif tag == "hr":
                continue
            else:
                self.block(child, depth + 1, list_prefix if tag in ("p", "div", "span") and list_prefix else "", level)
                list_prefix = "  " * level if list_prefix else ""
        flush()

    def table(self, node: _Node) -> None:
        rows: List[List[str]] = []
        for tr in _walk(node):
            if tr.tag != "tr" or tr.junk:
                continue
            cells = [_clean_line(self.inline(c)).replace("|", "/")[:200] for c in tr.children
                     if isinstance(c, _Node) and c.tag in ("td", "th") and not c.junk]
            if any(cells):
                rows.append(cells)
            if len(rows) >= 200:
                break
        if not rows:
            return
        if len(rows) == 1 or max(len(r) for r in rows) == 1:
            for row in rows:
                self.emit(" ".join(row))
            return
        width = max(len(r) for r in rows)
        lines = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
        lines.insert(1, "|" + " --- |" * width)
        self.emit("\n".join(lines))

def _contains_block(node: _Node) -> bool:
    """A block element (or a table) anywhere below ``node`` (junk subtrees skipped)."""
    stack = [c for c in node.children if isinstance(c, _Node)]
    while stack:
        item = stack.pop()
        if item.junk:
            continue
        if item.tag in _BLOCK or item.tag == "table":
            return True
        stack.extend(c for c in item.children if isinstance(c, _Node))
    return False

def _text_raw(node: _Node) -> str:
    parts: List[str] = []
    stack: List[Any] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif item.tag == "br":
            parts.append("\n")
        elif not item.junk or item is node:
            stack.extend(reversed(item.children))
    return "".join(parts)

def _clean_line(text: str) -> str:
    lines = [_WS.sub(" ", line).strip() for line in str(text).split("\n")]
    return "\n".join(line for line in lines if line)

def looks_like_html(text: str) -> bool:
    return bool(_LOOKS_HTML.search(str(text or "")[:20000]))

def extract(source: str, url: str = "", *, links: bool = False, alt: bool = False) -> Dict[str, Any]:
    """Raw HTML (or plain text) → ``{"title", "text" (Markdown), "links" [[text, url]], "chars", "html_bytes", "ms"}``.

    ``links=True`` writes ``[text](url)`` inline; by default the text stays clean and the content's links come
    separately (``links``, document order) for :func:`links_block`."""
    started = time.monotonic()
    raw = str(source or "")
    if not looks_like_html(raw):
        text = re.sub(r"\n{3,}", "\n\n", _clean_line(raw))[:MAX_EXTRACT]
        return {"title": "", "text": text, "links": [], "chars": len(text), "html_bytes": len(raw),
                "ms": int((time.monotonic() - started) * 1000)}
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw[:200000], re.I | re.S)
    title = _WS.sub(" ", _html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)))).strip()[:300] if title_match else ""

    cleaned = _PRE_STRIP.sub(" ", raw)
    cleaned = _COMMENTS.sub(" ", cleaned)
    cleaned = _DATA_URI.sub("data:", cleaned)
    builder = _TreeBuilder()
    try:
        builder.feed(cleaned)
        builder.close()
    except Exception:
        pass
    root = builder.root
    _measure(root, ignore_soft=True)
    total = max(root.p_len, 1)
    for node in _walk(root):
        if node.junk == 2 and node.p_len >= max(1500, 0.4 * total):
            node.junk = 0
    _measure(root)
    main = _pick_main(root)
    renderer = _Renderer(base_url(url, raw), links=links, alt=alt)
    text = _render(renderer, main)
    if len(text) < 200 and main is not root:

        renderer = _Renderer(base_url(url, raw), links=links, alt=alt)
        text = _render(renderer, root)
    text = text[:MAX_EXTRACT]
    return {"title": title or _WS.sub(" ", builder.title).strip()[:300], "text": text,
            "links": [list(row) for row in renderer.found], "chars": len(text),
            "html_bytes": len(raw), "ms": int((time.monotonic() - started) * 1000)}

def links_block(links: Any, *, max_tokens: int = 500, limit: int = 25) -> str:
    """A compact «links on the page» list (most descriptive texts first, then document order), within budget."""
    rows = [(str(t), str(u)) for t, u in (links or []) if str(t).strip() and str(u).startswith("http")]
    if not rows:
        return ""
    ranked = sorted(range(len(rows)), key=lambda i: (-min(len(rows[i][0]), 60), i))[:limit]
    lines, used = [], 0
    for i in sorted(ranked):
        line = f"- {rows[i][0]} — {rows[i][1]}"
        cost = estimate_tokens(line)
        if used + cost > max_tokens:
            break
        lines.append(line)
        used += cost
    return ("Links on the page:\n" + "\n".join(lines)) if lines else ""

def _render(renderer: "_Renderer", node: _Node) -> str:
    try:
        renderer.block(node)
        return _dedupe("\n\n".join(renderer.out))
    except RecursionError:

        text = _text_raw(node)
        return re.sub(r"\n{3,}", "\n\n", _clean_line(text))

def base_url(url: str, raw: str = "") -> str:
    match = re.search(r"<base\s[^>]*href=[\"']([^\"']+)[\"']", raw[:20000], re.I)
    if match:
        return urljoin(url or "", match.group(1))
    return url or ""

_LIST_LINE = re.compile(r"^\s*(?:- |\d+\. )")

def _dedupe(text: str) -> str:
    """Repeated blocks (the same teaser twice, a sticky title) are kept once; list items stay on adjacent lines."""
    seen = set()
    out: List[str] = []
    for block in text.split("\n\n"):
        key = block.strip()
        if not key:
            continue
        if len(key) > 30 and key in seen:
            continue
        seen.add(key)
        if out and _LIST_LINE.match(block) and (_LIST_LINE.match(out[-1].rsplit("\n", 1)[-1]) or out[-1].startswith("  ")):
            out[-1] += "\n" + block
        else:
            out.append(block)
    return "\n\n".join(out)

def clean_visible(text: str) -> str:
    """The visible text of a tab (UI Automation): whitespace collapsed, consecutive duplicate lines dropped."""
    lines: List[str] = []
    for line in str(text or "").replace("\r", "\n").split("\n"):
        line = _WS.sub(" ", line).strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:MAX_EXTRACT]

def estimate_tokens(text: str) -> int:
    """A cheap, conservative token estimate: ~4 Latin chars, ~2.4 Cyrillic/Greek chars, 1 CJK char per token."""
    value = str(text or "")
    if not value:
        return 0
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", value))
    other = len(re.findall(r"[\u0080-\u2fff]", value))
    latin = len(value) - cjk - other
    return int(latin / 4.0 + other / 2.4 + cjk) + 1

def chars_for_tokens(text: str, tokens: int) -> int:
    """How many leading characters of ``text`` fit ``tokens`` (by the same estimate)."""
    total = estimate_tokens(text)
    if total <= tokens:
        return len(text)
    return max(200, int(len(text) * tokens / float(total)))

def fit(text: str, *, max_tokens: int = PAGE_TOKENS, max_chars: int = 0) -> Dict[str, Any]:
    """``{"text", "chars", "tokens", "truncated", "omitted"}`` — the page within budget.

    Over budget: ~80 % head (cut at a paragraph), then a line with the omitted share and the headings that were
    left out (an outline the model can mention), then ~12 % tail (conclusions are usually at the end)."""
    value = str(text or "").strip()
    tokens = estimate_tokens(value)
    limit_chars = max_chars if max_chars and max_chars > 0 else 0
    if tokens <= max_tokens and (not limit_chars or len(value) <= limit_chars):
        return {"text": value, "chars": len(value), "tokens": tokens, "truncated": False, "omitted": 0}
    budget_chars = chars_for_tokens(value, max_tokens)
    if limit_chars:
        budget_chars = min(budget_chars, limit_chars)
    head_len, tail_len = int(budget_chars * 0.8), int(budget_chars * 0.12)
    head = value[:head_len]
    cut = head.rfind("\n\n")
    if cut > head_len * 0.6:
        head = head[:cut]
    tail = value[-tail_len:] if tail_len > 200 else ""
    if tail:

        for sep, share in (("\n\n", 0.5), ("\n", 0.5), (". ", 0.4), (" ", 0.2)):
            start = tail.find(sep)
            if 0 <= start < len(tail) * share:
                tail = tail[start + len(sep):]
                break
    middle = value[len(head):len(value) - len(tail)]
    all_headings = [line.strip() for line in middle.splitlines() if line.startswith("#")]
    headings = all_headings if len(all_headings) <= 30 else all_headings[:15] + ["…"] + all_headings[-15:]
    omitted = len(middle)
    note = (f"\n\n[… {omitted} characters of the page are omitted here"
            + (". Omitted sections: " + "; ".join(h.lstrip("# ")[:80] for h in headings) if headings else "")
            + " …]\n\n")
    if tail and all_headings and not tail.lstrip().startswith("#"):
        tail = all_headings[-1] + " (end)\n\n" + tail
    out = head + note + tail
    return {"text": out, "chars": len(value), "tokens": estimate_tokens(out), "truncated": True, "omitted": omitted}

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

def cache_key(url: str) -> str:
    return urldefrag(str(url or "").strip())[0]

def page_key(url: str, title: str = "") -> str:
    """The cache key of a TAB: its URL and its title — the same URL with another title (a single-page app moved on,
    a page that changed) is read again."""
    base = cache_key(url)
    title = " ".join(str(title or "").lower().split())[:200]
    return base + ("\x1f" + title if base and title else "")

def cache_get(url: str) -> Optional[Dict[str, Any]]:
    key = url if "\x1f" in str(url) else cache_key(url)
    if not key:
        return None
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if not row:
            return None
        if time.monotonic() - row[0] > CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return dict(row[1])

def cache_put(url: str, doc: Dict[str, Any], *aliases: str) -> None:
    keys = {(u if "\x1f" in u else cache_key(u)) for u in (url, *aliases) if u}
    keys.discard("")
    if not keys:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX:
            for stale in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:CACHE_MAX // 2]:
                _CACHE.pop(stale, None)
        now = time.monotonic()
        for key in keys:
            _CACHE[key] = (now, dict(doc))

def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()

async def fetch_html(url: str, *, timeout: float = FETCH_TIMEOUT) -> Dict[str, Any]:
    """ONE direct fetch of the tab's URL: ``{"url", "raw", "truncated"}`` or ``{"error"}`` (never raises)."""
    import asyncio
    from . import websearch
    try:
        page = await asyncio.wait_for(websearch.fetch_page(url, limit=6_000_000, raw=True, timeout=timeout),
                                      timeout + 2.0)
    except asyncio.TimeoutError:
        return {"error": f"timeout after {timeout:.0f} s"}
    except Exception as exc:
        return {"error": str(exc) or exc.__class__.__name__}
    return {"url": str(page.get("url") or url), "raw": str(page.get("raw") or page.get("text") or ""),
            "truncated": bool(page.get("truncated"))}
