"""Холст 2.0 / Canvas 2.0 — multi-file artifacts (Round 40, zone r40-canvas).

A multi-file artifact (a small site: ``index.html`` + ``style.css`` + ``app.js`` + ``data.json``, or a code project)
keeps ONE text per version, a *bundle*: every file starts with its own marker line ``⟦file: path⟧``. Everything the
canvas already does with a text works on the bundle unchanged — versions, the patch engine, hunk diff and per-hunk
review, restore, branches — and old single-text artifacts stay exactly as they were (no marker → one file).

* :func:`pack` / :func:`unpack` — files ⇄ bundle (exact round trip).
* :func:`clean_path` — a safe relative path (no ``..``, no drive, no reserved characters).
* :func:`assemble` — the bundle of a web project → ONE sandboxed preview page: ``<link href="style.css">`` and
  ``<script src="app.js">`` are inlined, unreferenced CSS/JS are added before ``</body>``, other files (JSON, CSV,
  SVG, text) are served to the page's ``fetch()`` from memory (the sandbox has no network). Returns a line map so an
  error at line N of the page is reported as ``app.js:12``.
* :func:`to_zip` — the files as a .zip for «Скачать».
"""
import io
import json
import re
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MARK_OPEN = "\u27e6file: "
MARK_CLOSE = "\u27e7"
MARK_RE = re.compile(r"^\u27e6file: (.+?)\u27e7$", re.M)
MAX_FILES = 40
_BAD_PART = re.compile(r'[<>:"|?*\x00-\x1f\u27e6\u27e7]')
WEB_EXT = (".html", ".htm")
STYLE_EXT = (".css",)
SCRIPT_EXT = (".js", ".mjs")
DEFAULT_NAME = {"html": "index.html", "document": "README.md", "table": "data.csv", "email": "email.txt",
                "diagram": "diagram.mmd", "slides": "slides.md"}


def mark(path: str) -> str:
    return f"{MARK_OPEN}{path}{MARK_CLOSE}"


def is_bundle(text: Any) -> bool:
    return isinstance(text, str) and text.startswith(MARK_OPEN) and bool(MARK_RE.match(text))


def clean_path(value: Any) -> str:
    """``./src\\app.js`` → ``src/app.js``; raises ValueError for ``..``, absolute/drive paths and bad characters."""
    raw = str(value or "").strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"file path must be relative: {raw!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise ValueError("file path is empty")
    for part in parts:
        if part == ".." or _BAD_PART.search(part) or len(part) > 80 or part.strip(" .") == "":
            raise ValueError(f"bad file path: {raw!r}")
    path = "/".join(parts)
    if len(path) > 160:
        raise ValueError(f"file path is too long: {raw[:60]!r}…")
    return path


def pack(files: Sequence[Tuple[str, str]]) -> str:
    if not files:
        raise ValueError("a multi-file artifact needs at least one file")
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files")
    seen = set()
    parts = []
    for path, content in files:
        path = clean_path(path)
        if path.lower() in seen:
            raise ValueError(f"duplicate file {path}")
        seen.add(path.lower())
        content = "" if content is None else str(content)
        if MARK_RE.search(content):
            raise ValueError(f"file {path} contains a canvas file marker line")
        parts.append(mark(path) + "\n" + content)
    return "\n".join(parts)


def unpack(text: Any) -> Optional[List[Tuple[str, str]]]:
    """Bundle → [(path, content)], or None for an ordinary single-text artifact."""
    if not is_bundle(text):
        return None
    marks = list(MARK_RE.finditer(text))
    out: List[Tuple[str, str]] = []
    for n, m in enumerate(marks):
        start = m.end() + (1 if text[m.end():m.end() + 1] == "\n" else 0)
        end = marks[n + 1].start() - 1 if n + 1 < len(marks) else len(text)
        out.append((m.group(1), text[start:max(start, end)]))
    return out


def normalize_files(value: Any) -> List[Tuple[str, str]]:
    """What models send: ``[{path, content}]``, ``[{name, code}]``, ``{"index.html": "…"}`` → [(path, content)]."""
    rows: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        rows = [(k, v) for k, v in value.items()]
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                path = item.get("path") or item.get("name") or item.get("file") or item.get("filename")
                content = next((item[k] for k in ("content", "text", "code", "body", "source") if item.get(k) is not None), "")
                rows.append((path, content))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                rows.append((item[0], item[1]))
    out = []
    for path, content in rows:
        content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
        out.append((clean_path(path), _strip_fence(content)))
    return out


def _strip_fence(content: str) -> str:
    m = re.match(r"^\s*```[\w+#.-]*[ \t]*\n(.*?)\n```\s*$", content, flags=re.S)
    return m.group(1) if m else content


def find_file(files: Sequence[Tuple[str, str]], ref: Any) -> int:
    """Index of a file by exact path, then case-insensitive, then by its base name when unique. -1 if none."""
    key = str(ref or "").strip().replace("\\", "/").lstrip("./")
    for n, (path, _c) in enumerate(files):
        if path == key:
            return n
    low = key.lower()
    for n, (path, _c) in enumerate(files):
        if path.lower() == low:
            return n
    base = [n for n, (path, _c) in enumerate(files) if path.rsplit("/", 1)[-1].lower() == low.rsplit("/", 1)[-1]]
    return base[0] if len(base) == 1 else -1


def file_of_line(bundle: str, line_index: int) -> Tuple[str, int]:
    """0-based line of a bundle → (path, 1-based line inside that file); ("", 0) on a marker line / outside."""
    path, start = "", 0
    for n, line in enumerate(bundle.split("\n")):
        m = MARK_RE.match(line)
        if m:
            if n == line_index:
                return m.group(1), 0
            path, start = m.group(1), n + 1
        elif n == line_index:
            return path, n - start + 1
    return path, max(0, line_index - start + 1) if path else 0


def ext_of(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""


def entry_of(files: Sequence[Tuple[str, str]]) -> str:
    paths = [p for p, _c in files]
    if "index.html" in paths:
        return "index.html"
    html = [p for p in paths if p.lower().endswith(WEB_EXT)]
    return sorted(html, key=lambda p: (p.count("/"), p))[0] if html else ""


def is_web(files: Sequence[Tuple[str, str]]) -> bool:
    return bool(entry_of(files)) or any(p.lower().endswith(SCRIPT_EXT) for p, _c in files)


_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>\s*</script\s*>", re.I)
_ATTR_RE = re.compile(r"""\b(href|src)\s*=\s*(?:(["'])([^"']*)\2|([^\s>"']+))""", re.I)
_IMG_RE = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])([^"']+)\2""", re.I)


def _ref(value: str, base: str) -> str:
    """A page's href/src → a project path (relative to the page's folder), '' for URLs."""
    value = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not value or re.match(r"^[a-z][a-z0-9+.-]*:|^//", value, re.I):
        return ""
    folder = base.rsplit("/", 1)[0] + "/" if "/" in base else ""
    parts: List[str] = []
    for seg in ((value[1:] if value.startswith("/") else folder + value)).split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def assemble(files: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    """A web project → ``{html, map:[{from, to, path, line}], files:{path: text}, entry}`` for the sandboxed preview.

    ``map`` rows are 1-based line ranges of ``html`` that came from a project file starting at its line ``line``.
    """
    fmap = {p: c for p, c in files}
    lower = {p.lower(): p for p in fmap}
    entry = entry_of(files)
    used = {entry} if entry else set()
    segs: List[Tuple[str, str, int]] = []   # (text, path or "", first line of that file)

    def style_block(path: str) -> List[Tuple[str, str, int]]:
        body = fmap[path].replace("</style", "<\\/style")
        return [(f'<style data-memu-file="{path}">\n', "", 0), (body, path, 1), ("\n</style>", "", 0)]

    def script_block(path: str, attrs: str = "") -> List[Tuple[str, str, int]]:
        body = fmap[path].replace("</script", "<\\/script")
        keep = re.sub(_ATTR_RE, "", attrs).strip()
        keep = re.sub(r"\b(defer|async)\b", "", keep).strip()
        return [(f'<script data-memu-file="{path}"{(" " + keep) if keep else ""}>\n', "", 0), (body, path, 1), ("\n</script>", "", 0)]

    def extras() -> List[Tuple[str, str, int]]:
        out: List[Tuple[str, str, int]] = []
        for path, _c in files:
            if path in used:
                continue
            if path.lower().endswith(STYLE_EXT):
                used.add(path)
                out += style_block(path) + [("\n", "", 0)]
        for path, _c in files:
            if path in used:
                continue
            if path.lower().endswith(SCRIPT_EXT):
                used.add(path)
                out += script_block(path) + [("\n", "", 0)]
        return out

    if entry:
        page = fmap[entry]
        cuts: List[Tuple[int, int, List[Tuple[str, str, int]]]] = []
        for m in _LINK_RE.finditer(page):
            tag = m.group(0)
            if not re.search(r"\brel\s*=\s*[\"']?stylesheet", tag, re.I):
                continue
            href = next((a.group(3) or a.group(4) or "" for a in _ATTR_RE.finditer(tag) if a.group(1).lower() == "href"), "")
            path = lower.get(_ref(href, entry).lower(), "")
            if path and path.lower().endswith(STYLE_EXT):
                used.add(path)
                cuts.append((m.start(), m.end(), style_block(path)))
        for m in _SCRIPT_RE.finditer(page):
            src = next((a.group(3) or a.group(4) or "" for a in _ATTR_RE.finditer(m.group(1)) if a.group(1).lower() == "src"), "")
            path = lower.get(_ref(src, entry).lower(), "")
            if path and path.lower().endswith(SCRIPT_EXT):
                used.add(path)
                cuts.append((m.start(), m.end(), script_block(path, m.group(1))))
        cuts.sort(key=lambda c: c[0])
        body_end = page.lower().rfind("</body")
        insert_at = body_end if body_end >= 0 else len(page)
        pos, line = 0, 1
        pending_extras = True
        for start, end, block in cuts + [(insert_at, insert_at, None)]:
            if start < pos:
                continue
            if block is None and pending_extras and start >= pos:
                chunk = page[pos:start]
                segs.append((chunk, entry, line))
                line += chunk.count("\n")
                pos = start
                segs += extras()
                pending_extras = False
                continue
            chunk = page[pos:start]
            segs.append((chunk, entry, line))
            line += chunk.count("\n") + page[start:end].count("\n")
            segs += block
            pos = end
        segs.append((page[pos:], entry, line))
    elif any(p.lower().endswith(SCRIPT_EXT) for p in fmap):
        segs.append(('<!doctype html><html><head><meta charset="utf-8"><style>body{font:14px system-ui,sans-serif;'
                     'margin:16px;color:#222;background:#fff}@media (prefers-color-scheme: dark){body{color:#ddd;'
                     'background:#16181c}}</style>\n', "", 0))
        for path, _c in files:
            if path.lower().endswith(STYLE_EXT):
                used.add(path)
                segs += style_block(path) + [("\n", "", 0)]
        segs.append(('</head><body><div id="app"></div>\n', "", 0))
        segs += extras()
        segs.append(("</body></html>", "", 0))
    else:
        return {"html": "", "map": [], "files": {}, "entry": ""}

    html_parts: List[str] = []
    rows: List[Dict[str, Any]] = []
    cur = 1
    for text, path, first in segs:
        if not text:
            continue
        lines = text.count("\n")
        if path:
            rows.append({"from": cur, "to": cur + lines, "path": path, "line": first})
        html_parts.append(text)
        cur += lines
    html = "".join(html_parts)
    # <img src="logo.svg"> of a project file → an inline data: URL (the sandbox blocks everything else)
    def img(m):
        path = lower.get(_ref(m.group(3), entry or "").lower(), "")
        if path and path.lower().endswith(".svg"):
            from urllib.parse import quote
            return f"{m.group(1)}{m.group(2)}data:image/svg+xml;charset=utf-8,{quote(fmap[path])}{m.group(2)}"
        return m.group(0)
    html = _IMG_RE.sub(img, html)
    vfs = {p: c for p, c in files if p != entry and len(c) <= 400_000}
    return {"html": html, "map": rows, "files": vfs, "entry": entry or ""}


def to_zip(files: Iterable[Tuple[str, str]], folder: str = "") -> bytes:
    buf = io.BytesIO()
    prefix = (folder.strip("/") + "/") if folder else ""
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files:
            zf.writestr(prefix + path, content.encode("utf-8"))
    return buf.getvalue()


def listing(files: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
    return [{"path": p, "lines": c.count("\n") + 1, "chars": len(c)} for p, c in files]
