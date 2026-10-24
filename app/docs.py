"""MemuAI documentation: the pages behind Settings → Documentation.

Pages are Markdown files in ``app/docs/<lang>/<id>.md`` with a small header::

    ---
    title: Свои темы
    summary: Как создать тему и поменять в ней что угодно.
    ---

``app/docs/index.json`` lists the groups and the order of pages. A page that
exists in only one language is served in that language with ``fallback``
set, so a missing translation never shows an empty screen. Everything here is
read-only and cached by file modification time.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LANGUAGES = ("ru", "en")
_PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_lock = threading.Lock()
_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


def docs_dir() -> Path:
    """Where the pages live, inside a frozen build too (see MemuAI.spec)."""
    base = getattr(sys, "_MEIPASS", "")
    if base:
        frozen = Path(base) / "app" / "docs"
        if frozen.is_dir():
            return frozen
    return Path(__file__).resolve().parent / "docs"


def _lang(value: Any) -> str:
    return "en" if str(value or "") == "en" else "ru"


def _index() -> Dict[str, Any]:
    try:
        data = json.loads((docs_dir() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    groups = data.get("groups") if isinstance(data, dict) else None
    return {"groups": [row for row in groups or [] if isinstance(row, dict) and row.get("id")]}


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

def plain(text: str) -> str:
    """Markdown inline syntax removed; used for anchors, search and snippets."""
    value = str(text or "")
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)  # Round 29: screenshots
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\+\+([^\s+](?:[^\n]*?[^\s+])??)\+\+", r"\1", value)  # ++Ctrl+Shift+T++
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"(\*\*|__|\*|~~)", "", value)
    return value.strip()


def anchor(text: str) -> str:
    """Heading anchor; docs.js computes the same one on the client."""
    slug = re.sub(r"[\W_]+", "-", plain(text).lower()).strip("-")
    return slug[:80] or "section"


def _parse(raw: str) -> Tuple[Dict[str, str], str]:
    meta: Dict[str, str] = {}
    body = raw.replace("\r\n", "\n")
    match = re.match(r"---\n(.*?)\n---\n", body, re.S)
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip():
                meta[key.strip()] = value.strip()
        body = body[match.end():]
    return meta, body.strip() + "\n"


def _headings(body: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        match = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", line)
        if not match:
            continue
        text = plain(match.group(2))
        slug = anchor(text)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        rows.append({"level": len(match.group(1)), "text": text, "anchor": slug})
    return rows


def _load(lang: str, page_id: str) -> Optional[Dict[str, Any]]:
    if not _PAGE_ID.match(page_id):
        return None
    path = docs_dir() / lang / f"{page_id}.md"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    key = (lang, page_id)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    try:
        meta, body = _parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    headings = _headings(body)
    first = next((row["text"] for row in headings if row["level"] == 1), "")
    page = {
        "id": page_id,
        "lang": lang,
        "title": meta.get("title") or first or page_id,
        "summary": meta.get("summary", ""),
        "updated": meta.get("updated", ""),
        "body": body,
        "headings": headings,
    }
    with _lock:
        _cache[key] = (stamp, page)
    return page


def _page_in(lang: str, page_id: str) -> Optional[Dict[str, Any]]:
    """The page in the wanted language, else in the other one (marked)."""
    page = _load(lang, page_id)
    if page:
        return dict(page, fallback=False)
    for other in LANGUAGES:
        if other != lang:
            page = _load(other, page_id)
            if page:
                return dict(page, fallback=True)
    return None


def _ordered_ids() -> List[Tuple[str, str]]:
    return [(str(group["id"]), str(page_id)) for group in _index()["groups"]
            for page_id in group.get("pages") or [] if _PAGE_ID.match(str(page_id))]


def catalog(language: str = "ru") -> Dict[str, Any]:
    lang = _lang(language)
    groups: List[Dict[str, Any]] = []
    for group in _index()["groups"]:
        pages: List[Dict[str, Any]] = []
        for page_id in group.get("pages") or []:
            page = _page_in(lang, str(page_id))
            if page:
                pages.append({"id": page["id"], "title": page["title"], "summary": page["summary"],
                              "fallback": page["fallback"]})
        if pages:
            groups.append({"id": group["id"], "title": group.get(lang) or group.get("ru") or group["id"],
                           "icon": group.get("icon") or "", "pages": pages})
    return {"lang": lang, "groups": groups, "home": groups[0]["pages"][0]["id"] if groups else ""}


def page(language: str, page_id: str) -> Optional[Dict[str, Any]]:
    lang = _lang(language)
    found = _page_in(lang, str(page_id or ""))
    if not found:
        return None
    order = [(group_id, pid) for group_id, pid in _ordered_ids() if _page_in(lang, pid)]
    ids = [pid for _, pid in order]
    position = ids.index(found["id"]) if found["id"] in ids else -1
    group_id = order[position][0] if position >= 0 else ""
    group = next((row for row in _index()["groups"] if row["id"] == group_id), {})

    def neighbour(offset: int) -> Optional[Dict[str, str]]:
        index = position + offset
        if position < 0 or not 0 <= index < len(ids):
            return None
        other = _page_in(lang, ids[index])
        return {"id": other["id"], "title": other["title"]} if other else None

    try:  # Round 29: «Кратко от ИИ» shipped with the docs (app/docs_ai.py)
        from .docs_ai import summary_for
        ai_summary = summary_for(found["lang"], found["id"], found["title"], found["body"])
    except Exception:
        ai_summary = None
    return dict(found, group=group_id, group_title=group.get(lang) or group.get("ru") or "",
                prev=neighbour(-1), next=neighbour(1), ai_summary=ai_summary)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _sections(body: str) -> List[Tuple[str, str, str]]:
    """(heading, anchor, text) blocks, the page start counting as one section."""
    sections: List[Tuple[str, str, str]] = []
    headings = iter(_headings(body))
    current = ("", "", [])  # type: ignore[var-annotated]
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and re.match(r"^#{1,4}\s+", line):
            sections.append((current[0], current[1], "\n".join(current[2])))
            row = next(headings, None)
            current = (row["text"], row["anchor"], []) if row else ("", "", [])
            continue
        current[2].append(line)
    sections.append((current[0], current[1], "\n".join(current[2])))
    return [row for row in sections if row[0] or row[2].strip()]


def _snippet(text: str, needle: str, width: int = 160) -> str:
    clean = re.sub(r"\s+", " ", plain(re.sub(r"```.*?```", " ", text, flags=re.S)
                                   .replace("|", " ").replace("#", " ").replace(">", " "))).strip()
    at = clean.lower().find(needle)
    if at < 0:
        # The match may live only in a code block: show that line of code.
        line = next((row.strip() for row in text.splitlines()
                     if needle in row.lower() and not row.lstrip().startswith("```")), "")
        if line:
            return line[:width] + ("…" if len(line) > width else "")
        return clean[:width] + ("…" if len(clean) > width else "")
    start = max(0, at - width // 3)
    end = min(len(clean), start + width)
    return ("…" if start else "") + clean[start:end] + ("…" if end < len(clean) else "")


def search(language: str, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    lang = _lang(language)
    needle = re.sub(r"\s+", " ", str(query or "").strip().lower())[:120]
    if len(needle) < 2:
        return []
    words = [word for word in needle.split(" ") if len(word) > 1]
    results: List[Dict[str, Any]] = []
    for group_id, page_id in _ordered_ids():
        found = _page_in(lang, page_id)
        if not found:
            continue
        title = found["title"].lower()
        base = (40 if needle in title else 0) + sum(8 for word in words if word in title)
        base += 6 if needle in found["summary"].lower() else 0
        best: Optional[Dict[str, Any]] = None
        for heading, slug, text in _sections(found["body"]):
            head = heading.lower()
            body = plain(text).lower()
            score = base + (25 if needle in head else 0) + sum(5 for word in words if word in head)
            hits = body.count(needle)
            score += min(hits, 6) * 4 + sum(1 for word in words if word in body)
            if not hits and not all(word in f"{title} {head} {body}" for word in words):
                continue
            if score and (best is None or score > best["score"]):
                best = {"id": page_id, "group": group_id, "title": found["title"],
                        "heading": heading, "anchor": slug, "score": score,
                        "snippet": _snippet(text or heading, needle if hits else (words[0] if words else needle))}
        if best:
            results.append(best)
    results.sort(key=lambda row: (-row["score"], row["title"]))
    return results[:max(1, min(50, int(limit or 20)))]
