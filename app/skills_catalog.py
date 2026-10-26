# -*- coding: utf-8 -*-
"""Skill catalog and the Agent Skills format for the MemuAI skill library (Round 32).

The open Agent Skills format (Anthropic; used by Claude Code, Codex, Cursor and many GitHub repos) is a folder::

    my-skill/
      SKILL.md          ---\\nname: my-skill\\ndescription: What it does. Use when …\\n---\\n<instructions>
      scripts/…         optional helpers (shown to the user, NEVER run by MemuAI)
      references/…      optional documents the instructions point to

This module parses that format (``parse_skill_md``, ``parse_zip``), writes it (``build_zip``), and fetches a
catalog of public skills from GitHub with a disk cache (TTL + ETag), rate-limit handling and an offline state.
Network access goes through ``_http_get`` only, so tests replace it with recorded responses.

Routes (window server, same localhost boundary as the rest of ``/api``)::

    GET  /api/pg/skills-catalog?refresh=0|1           curated sources + their skills + discovered repositories
         …&swr=1[&since=v]                         stale-while-revalidate: cached catalog at once, refresh in background
    GET  /api/pg/skills-catalog/repo?repo=o/r          skills of any GitHub repository («Explore»)
    GET  /api/pg/skills-catalog/skill?repo=o/r&path=p  one skill: SKILL.md (sanitised), files, warnings
    GET  /api/pg/skills-catalog/file?repo=o/r&path=p   text of one file of a skill (view a script; never executed)
    POST /api/pg/skills-catalog/resolve {url}          skills behind a GitHub link (repo / folder / SKILL.md)
    POST /api/pg/skills/parse {name, text|data_base64} skills from an uploaded SKILL.md / .zip
    POST /api/pg/skills/export {skills, format}        .zip (Agent Skills folders) or one SKILL.md
    GET|POST /api/pg/skills/library                    server-side backup of the window's skill library
    GET  /api/pg/skills/code-installed                 skills installed for MemuCode from the catalog
    POST /api/pg/skills-catalog/install-code {repo, path, include_scripts}
    POST /api/pg/skills-catalog/uninstall-code {slug}
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - the parser/catalog functions work without FastAPI
    Request = Any  # type: ignore

MAX_SKILL_MD = 512 * 1024
MAX_BODY = 200_000
MAX_FILE_VIEW = 64 * 1024
MAX_ZIP = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 600
MAX_SKILLS_PER_REPO = 80
MAX_CODE_INSTALL = 2 * 1024 * 1024
TTL_LIST = 6 * 3600
TTL_META = 24 * 3600
TTL_RAW = 24 * 3600
HTTP_TIMEOUT = 15.0
NET_SLOTS = 12
HEAD_BYTES = 8192

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

SOURCES: List[Dict[str, Any]] = [
    {"repo": "anthropics/skills", "official": True,
     "ru": "Официальные скиллы Anthropic", "en": "Official Anthropic skills"},
    {"repo": "obra/superpowers", "ru": "Superpowers: методики для агентов", "en": "Superpowers: agent workflows"},
    {"repo": "addyosmani/agent-skills", "ru": "Инженерные скиллы Addy Osmani", "en": "Engineering skills by Addy Osmani"},
    {"repo": "coreyhaines31/marketingskills", "ru": "Маркетинг: SEO, тексты, CRO", "en": "Marketing: SEO, copy, CRO"},
    {"repo": "blader/humanizer", "ru": "Humanizer: текст без следов ИИ", "en": "Humanizer: text without AI tells"},
]
TOPICS = ("agent-skills", "claude-skills", "ai-skills")

SCRIPT_EXT = {".py", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts",
              ".rb", ".pl", ".php", ".lua", ".vbs", ".jar", ".exe", ".dll", ".so", ".dylib", ".msi", ".scr", ".com",
              ".applescript", ".go", ".rs"}
BINARY_EXT = {".exe", ".dll", ".so", ".dylib", ".msi", ".scr", ".com", ".jar", ".bin", ".zip", ".7z", ".gz", ".tar",
              ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ttf", ".otf", ".woff", ".woff2", ".ico", ".mp3", ".mp4"}
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".xml", ".html", ".css", ".ini", ".cfg"}

_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_HIDDEN_CHARS = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DANGEROUS_HTML = re.compile(r"<\s*(script|style|iframe|object|embed|form|meta|link)\b[^>]*>[\s\S]*?(?:<\s*/\s*\1\s*>|$)"
                             r"|<\s*(script|iframe|object|embed|meta|link)\b[^>]*/?>", re.I)

class CatalogError(Exception):
    """A user-facing catalog error with a machine state (offline, rate_limited, not_found, invalid)."""

    def __init__(self, state: str, message: str = "", reset: int = 0):
        super().__init__(message or state)
        self.state = state
        self.reset = reset

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value

def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """``(meta, body)`` of a markdown file with an optional ``---`` YAML header.

    A deliberately small YAML subset that covers real SKILL.md files: ``key: value`` (plain or quoted),
    block scalars (``>``, ``|`` with ``-``/``+``), inline lists ``[a, b]``, ``- item`` lists and one level of
    nested mappings (``metadata:``). Anything it does not understand is skipped, never raised.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    match = re.match(r"^---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|$)", raw, re.S)
    if not match:
        return {}, raw
    lines = match.group(1).split("\n")
    body = raw[match.end():]
    meta: Dict[str, Any] = {}
    i = 0

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or indent_of(line) > 0:
            i += 1
            continue
        pair = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", line)
        if not pair:
            i += 1
            continue
        key, value = pair.group(1).strip().lower(), pair.group(2).rstrip()
        i += 1
        if value[:1] in (">", "|") and re.fullmatch(r"[>|][+-]?\d?", value):
            block: List[str] = []
            while i < len(lines) and (not lines[i].strip() or indent_of(lines[i]) > 0):
                block.append(lines[i])
                i += 1
            pad = min((indent_of(item) for item in block if item.strip()), default=0)
            rows = [item[pad:] if item.strip() else "" for item in block]
            if value.startswith(">"):
                text_value = re.sub(r"(?<!\n)\n(?!\n)", " ", "\n".join(rows)).strip()
            else:
                text_value = "\n".join(rows).rstrip()
            meta[key] = text_value
            continue
        if value == "":

            items: List[str] = []
            nested: Dict[str, str] = {}
            while i < len(lines) and (not lines[i].strip() or indent_of(lines[i]) > 0):
                inner = lines[i].strip()
                i += 1
                if not inner or inner.startswith("#"):
                    continue
                if inner.startswith("- "):
                    items.append(_unquote(inner[2:]))
                    continue
                sub = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", inner)
                if sub:
                    nested[sub.group(1).strip().lower()] = _unquote(sub.group(2))
            meta[key] = items if items else nested
            continue
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [_unquote(part) for part in value[1:-1].split(",") if part.strip()]
            continue
        meta[key] = _unquote(value)
    return meta, body

def slugify(name: str, limit: int = 64) -> str:
    """Agent Skills name: lowercase letters, digits and hyphens (other alphabets are kept for MemuAI ids)."""
    text = re.sub(r"[^\w-]+", "-", str(name or "").strip().lower(), flags=re.UNICODE).replace("_", "-")
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:limit].strip("-")

def sanitize_markdown(text: str) -> Tuple[str, List[str]]:
    """Strip what must never reach the preview or the model unannounced; returns ``(text, warnings)``.

    The window renders markdown with escaping anyway; this removes active HTML, control characters and the
    invisible / bidi characters that are used to hide instructions from a human reader.
    """
    warnings: List[str] = []
    value = str(text or "")
    if _HIDDEN_CHARS.search(value):
        warnings.append("hidden_chars")
        value = _HIDDEN_CHARS.sub("", value)
    value = _CONTROL.sub("", value)
    if _DANGEROUS_HTML.search(value):
        warnings.append("html_removed")
        value = _DANGEROUS_HTML.sub("", value)
    return value, warnings

_RISKY_PATTERNS = [
    ("pipe_to_shell", re.compile(r"(curl|wget|iwr|Invoke-WebRequest)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b|iex\s*\(", re.I)),
    ("destructive_command", re.compile(r"\brm\s+-rf\s+[/~]|\bformat\s+[a-z]:|Remove-Item\s+[^\n]*-Recurse[^\n]*-Force", re.I)),
    ("long_base64", re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")),
]

def content_warnings(body: str) -> List[str]:
    return [name for name, pattern in _RISKY_PATTERNS if pattern.search(body or "")]

def file_kind(path: str) -> str:
    ext = os.path.splitext(str(path).lower())[1]
    if ext in SCRIPT_EXT:
        return "script"
    if ext in BINARY_EXT:
        return "binary"
    if os.path.basename(str(path)).upper() == "SKILL.MD":
        return "skill"
    return "doc" if ext in TEXT_EXT or not ext else "other"

def parse_skill_md(text: str, fallback_name: str = "") -> Dict[str, Any]:
    """One skill from a SKILL.md text (or plain markdown). Raises ``ValueError`` when nothing usable is there."""
    if len(str(text or "").encode("utf-8", "ignore")) > MAX_SKILL_MD:
        raise ValueError("SKILL.md is too large")
    clean, warnings = sanitize_markdown(text)
    meta, body = parse_front_matter(clean)
    body = body.strip()
    name = str(meta.get("name") or meta.get("title") or "").strip()
    if not name:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        name = heading.group(1).strip() if heading else str(fallback_name or "").strip()
    if not body or not name:
        raise ValueError("no skill instructions found")
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    description = str(meta.get("description") or meta.get("desc") or "").strip()
    when = str(meta.get("when") or meta.get("when-to-use") or meta.get("when_to_use")
               or metadata.get("memuai-when") or metadata.get("when") or "").strip()
    category = str(meta.get("category") or metadata.get("memuai-category") or metadata.get("category") or "").strip()
    display = str(metadata.get("memuai-display-name") or "").strip()
    tools = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    if isinstance(tools, str):
        tools = [part.strip() for part in re.split(r"[,\s]+", tools) if part.strip()]
    if len(body) > MAX_BODY:
        warnings.append("truncated")
        body = body[:MAX_BODY]
    return {
        "name": (display or name)[:120],
        "slug": slugify(name) or slugify(fallback_name) or "skill",
        "description": description[:1024],
        "when": when[:400],
        "category": category[:60],
        "license": str(meta.get("license") or "").strip()[:200],
        "allowed_tools": [str(item)[:60] for item in tools][:30] if isinstance(tools, list) else [],
        "version": str(metadata.get("version") or meta.get("version") or "")[:40],
        "author": str(metadata.get("author") or meta.get("author") or "")[:80],
        "body": body,
        "warnings": warnings + content_warnings(body),
    }

def parse_zip(data: bytes) -> List[Dict[str, Any]]:
    """Every skill folder inside a .zip (any depth). Nothing is extracted to disk."""
    if len(data) > MAX_ZIP:
        raise ValueError("the archive is too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a zip archive") from exc
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError("the archive has too many files")
    if sum(info.file_size for info in infos) > MAX_ZIP:
        raise ValueError("the archive unpacks to too much data")
    names = [info.filename.replace("\\", "/") for info in infos if "__MACOSX/" not in info.filename]
    skill_files = sorted([name for name in names if os.path.basename(name).upper() == "SKILL.MD"], key=len)
    skills: List[Dict[str, Any]] = []
    for skill_path in skill_files:
        folder = skill_path.rsplit("/", 1)[0] + "/" if "/" in skill_path else ""
        try:
            raw = archive.read(skill_path if skill_path in archive.namelist() else skill_path.replace("/", "\\"))
        except KeyError:
            continue
        fallback = folder.rstrip("/").rsplit("/", 1)[-1] if folder else "skill"
        try:
            skill = parse_skill_md(raw.decode("utf-8", "replace"), fallback)
        except ValueError:
            continue
        nested = [other.rsplit("/", 1)[0] + "/" for other in skill_files if other != skill_path]
        files = []
        for name in names:
            if folder and not name.startswith(folder):
                continue
            rel = name[len(folder):]
            if not rel or rel.upper() == "SKILL.MD":
                continue
            if any(name.startswith(n) and len(n) > len(folder) for n in nested):
                continue
            info = archive.getinfo(name) if name in archive.namelist() else None
            files.append({"path": rel, "size": info.file_size if info else 0, "kind": file_kind(rel)})
        skill["files"] = files[:200]
        skill["has_scripts"] = any(f["kind"] in ("script", "binary") for f in files)
        skills.append(skill)
    if not skills:

        for name in names:
            if name.lower().endswith(".md") and archive.getinfo(name).file_size <= MAX_SKILL_MD:
                try:
                    skill = parse_skill_md(archive.read(name).decode("utf-8", "replace"),
                                           os.path.splitext(os.path.basename(name))[0])
                except ValueError:
                    continue
                skill.update(files=[], has_scripts=False)
                skills.append(skill)
    return skills[:100]

def _yaml_scalar(value: str) -> str:
    value = str(value or "").replace("\r", "").strip()
    if "\n" in value:
        value = re.sub(r"\s*\n\s*", " ", value)
    if value == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`,]|^[-?\s]|\s$", value) or value.lower() in ("yes", "no", "true", "false", "null"):
        return json.dumps(value, ensure_ascii=False)
    return value

def skill_to_md(skill: Dict[str, Any]) -> Tuple[str, str]:
    """``(slug, SKILL.md text)`` for one library skill (``name/desc/when/category/prompt``)."""
    name = str(skill.get("name") or "skill").strip()
    slug = slugify(skill.get("slug") or name) or "skill"
    description = str(skill.get("desc") or skill.get("description") or "").strip()
    when = str(skill.get("when") or "").strip()
    if when and when.lower() not in description.lower():
        description = (description.rstrip(".") + ". " if description else "") + "Use when: " + when
    lines = ["---", f"name: {_yaml_scalar(slug)}", f"description: {_yaml_scalar(description[:1024] or name)}"]
    if skill.get("license"):
        lines.append(f"license: {_yaml_scalar(skill['license'])}")
    meta = {"memuai-display-name": name if name != slug else "", "memuai-category": skill.get("category") or "",
            "memuai-when": when}
    meta = {key: value for key, value in meta.items() if value}
    if meta:
        lines.append("metadata:")
        lines += [f"  {key}: {_yaml_scalar(str(value))}" for key, value in meta.items()]
    lines.append("---")
    body = str(skill.get("prompt") or skill.get("body") or "").strip()
    return slug, "\n".join(lines) + "\n\n" + body + "\n"

def build_zip(skills: List[Dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    used: Dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for skill in skills[:500]:
            slug, text = skill_to_md(skill)
            used[slug] = used.get(slug, 0) + 1
            folder = slug if used[slug] == 1 else f"{slug}-{used[slug]}"
            archive.writestr(f"{folder}/SKILL.md", text)
    return buffer.getvalue()

def _data_dir() -> Path:
    from . import config
    return Path(config.DATA_DIR)

def cache_dir() -> Path:
    path = _data_dir() / "memuai" / "skills_catalog_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path

_client_lock = threading.Lock()
_client: Any = None

def _shared_client():
    """One pooled HTTP client (keep-alive): a new TLS handshake per SKILL.md made a cold catalog take minutes."""
    global _client
    with _client_lock:
        if _client is None:
            import httpx
            _client = httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True,
                                   limits=httpx.Limits(max_connections=NET_SLOTS, max_keepalive_connections=NET_SLOTS))
        return _client

def _default_http_get(url: str, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
    with _shared_client().stream("GET", url, headers=headers) as response:
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_SKILL_MD * 8:
                break
            chunks.append(chunk)
        return response.status_code, {k.lower(): v for k, v in response.headers.items()}, b"".join(chunks)

_http_get: Callable[[str, Dict[str, str]], Tuple[int, Dict[str, str], bytes]] = _default_http_get
_rate = {"remaining": None, "reset": 0, "limited_until": 0, "search_until": 0}
_lock = threading.Lock()
_net = threading.BoundedSemaphore(NET_SLOTS)

def _cache_file(url: str) -> Path:
    return cache_dir() / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

def _read_cache(url: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_cache_file(url).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

def _write_cache(url: str, entry: Dict[str, Any]) -> None:
    path = _cache_file(url)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass

def fetch(url: str, *, ttl: int, kind: str = "json", refresh: bool = False, cache_key: str = "",
          extra_headers: Optional[Dict[str, str]] = None) -> Tuple[Any, Dict[str, Any]]:
    """GET with a disk cache. Returns ``(data, info)``; ``info.stale`` is set when an old copy was served.

    Rate limits and network failures fall back to the cached copy; without one a ``CatalogError`` is raised.
    An expired copy is revalidated with ``If-None-Match`` (a 304 does not count against GitHub's limit).
    """
    key = cache_key or url
    cached = _read_cache(key)
    now = time.time()
    if cached and not refresh and now - float(cached.get("ts", 0)) < ttl:
        return cached.get("data"), {"cached": True, "stale": False, "ts": cached.get("ts")}
    is_api = url.startswith(API)

    bucket = "search_until" if is_api and "/search/" in url else "limited_until"
    if is_api and _rate[bucket] > now:
        if cached:
            return cached.get("data"), {"cached": True, "stale": True, "ts": cached.get("ts"), "state": "rate_limited"}
        raise CatalogError("rate_limited", "GitHub rate limit", int(_rate[bucket]))
    headers = {"User-Agent": "MemuAI-skill-catalog", "Accept": "application/vnd.github+json" if is_api else "*/*"}
    headers.update(extra_headers or {})
    token = os.environ.get("MEMUAI_GITHUB_TOKEN", "").strip()
    if token and is_api:
        headers["Authorization"] = f"Bearer {token}"
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    try:
        with _net:
            status, response_headers, body = _http_get(url, headers)
    except Exception as exc:
        if cached:
            return cached.get("data"), {"cached": True, "stale": True, "ts": cached.get("ts"), "state": "offline"}
        raise CatalogError("offline", f"{type(exc).__name__}: {exc}"[:300]) from exc
    if is_api:
        try:
            with _lock:
                if "x-ratelimit-remaining" in response_headers and bucket == "limited_until":
                    _rate["remaining"] = int(response_headers["x-ratelimit-remaining"])
                    _rate["reset"] = int(response_headers.get("x-ratelimit-reset") or 0)
        except ValueError:
            pass
    if status == 304 and cached:
        cached["ts"] = now
        _write_cache(key, cached)
        return cached.get("data"), {"cached": True, "stale": False, "ts": now}
    limited = status == 429 or (status == 403 and (response_headers.get("x-ratelimit-remaining") == "0"
                                                   or b"rate limit" in body[:400].lower()))
    if limited:
        reset = int(response_headers.get("x-ratelimit-reset") or 0) or int(now + 60 * 10)
        retry = response_headers.get("retry-after")
        if retry and retry.isdigit():
            reset = int(now + int(retry))
        _rate[bucket] = reset
        if cached:
            return cached.get("data"), {"cached": True, "stale": True, "ts": cached.get("ts"), "state": "rate_limited"}
        raise CatalogError("rate_limited", "GitHub rate limit", reset)
    if status == 404:
        raise CatalogError("not_found", url)
    if status == 407:
        if cached:
            return cached.get("data"), {"cached": True, "stale": True, "ts": cached.get("ts"), "state": "offline"}
        raise CatalogError("offline", "HTTP 407 proxy authentication required")
    if status >= 400:
        if cached:
            return cached.get("data"), {"cached": True, "stale": True, "ts": cached.get("ts"), "state": "error"}
        raise CatalogError("error", f"HTTP {status}")
    if kind == "json":
        try:
            data: Any = json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise CatalogError("error", "invalid JSON from GitHub") from exc
    else:
        if len(body) > MAX_SKILL_MD:
            raise CatalogError("too_large", url)
        data = body.decode("utf-8", "replace")
    _write_cache(key, {"ts": now, "etag": response_headers.get("etag", ""), "data": data})
    return data, {"cached": False, "stale": False, "ts": now, "status": status}

def check_repo(repo: str) -> str:
    repo = str(repo or "").strip().strip("/")
    if not _REPO_RE.match(repo) or ".." in repo:
        raise CatalogError("invalid", "repository must look like owner/name")
    return repo

def check_path(path: str) -> str:
    path = str(path or "").strip().strip("/")
    if ".." in path.split("/") or path.startswith(("/", "\\")) or "\\" in path or len(path) > 400:
        raise CatalogError("invalid", "bad path")
    return path

def _repo_meta(repo: str, refresh: bool = False) -> Dict[str, Any]:
    data, _ = fetch(f"{API}/repos/{repo}", ttl=TTL_META, refresh=refresh)
    return _repo_row(data or {})

def _date_of(value: Any) -> str:
    """``YYYY-MM-DD`` from a GitHub timestamp (tolerates proxies that rewrite ISO stamps into local text)."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return "-".join(match.groups()) if match else ""

def _repo_row(data: Dict[str, Any]) -> Dict[str, Any]:
    owner = data.get("owner") or {}
    return {
        "repo": data.get("full_name") or "",
        "description": str(data.get("description") or "")[:300],
        "stars": int(data.get("stargazers_count") or 0),
        "license": ((data.get("license") or {}).get("spdx_id") or "") if isinstance(data.get("license"), dict) else "",
        "updated": _date_of(data.get("pushed_at") or data.get("updated_at") or ""),
        "branch": data.get("default_branch") or "main",
        "author": owner.get("login") or str(data.get("full_name") or "").split("/")[0],
        "url": data.get("html_url") or (f"https://github.com/{data.get('full_name')}" if data.get("full_name") else ""),
        "topics": [str(t) for t in (data.get("topics") or [])][:12],
        "archived": bool(data.get("archived")),
    }

def _tree(repo: str, branch: str, refresh: bool = False) -> List[Dict[str, Any]]:
    data, _ = fetch(f"{API}/repos/{repo}/git/trees/{urllib.parse.quote(branch)}?recursive=1", ttl=TTL_LIST, refresh=refresh)
    return [item for item in (data or {}).get("tree", []) if isinstance(item, dict)]

def _skill_folders(tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``SKILL.md`` blobs of a tree with the files of their folders (nested skill folders excluded)."""
    blobs = [item for item in tree if item.get("type") == "blob"]
    skill_md = [item for item in blobs if os.path.basename(item.get("path", "")).upper() == "SKILL.MD"]
    skill_md = [item for item in skill_md
                if not re.match(r"(templates?|\.github)/", item["path"], re.I) and "/.github/" not in item["path"]]
    folders = sorted({item["path"].rsplit("/", 1)[0] if "/" in item["path"] else "" for item in skill_md})
    result = []
    for item in sorted(skill_md, key=lambda x: x["path"]):
        folder = item["path"].rsplit("/", 1)[0] if "/" in item["path"] else ""
        prefix = folder + "/" if folder else ""
        nested = [f + "/" for f in folders if f != folder and f.startswith(prefix) and (folder or f)]
        files = []
        for blob in blobs:
            path = blob.get("path", "")
            if not path.startswith(prefix) or path == item["path"]:
                continue
            if any(path.startswith(n) for n in nested):
                continue
            if not folder and "/" in path and not path.split("/", 1)[0] in ("scripts", "references", "assets", "resources", "reference", "docs"):
                continue
            files.append({"path": path[len(prefix):], "size": int(blob.get("size") or 0), "kind": file_kind(path)})
        result.append({"path": folder, "skill_md": item["path"], "sha": item.get("sha") or "",
                       "size": int(item.get("size") or 0), "files": files[:200]})
    return result

def _raw_by_sha(url: str, sha: str) -> str:
    """A raw file through the cache; a new blob sha (the file changed upstream) forces a fresh download."""
    cached = _read_cache(url) or {}
    if sha and cached.get("sha") == sha and isinstance(cached.get("data"), str):
        return cached["data"]
    stale = bool(sha) and bool(cached) and cached.get("sha") != sha
    text, _ = fetch(url, ttl=TTL_RAW, kind="text", refresh=stale)
    if sha:
        entry = _read_cache(url)
        if entry and entry.get("sha") != sha:
            entry["sha"] = sha
            _write_cache(url, entry)
    return text

def _skill_head(url: str, sha: str) -> str:
    """Enough of a SKILL.md for the list (its front matter): a full copy if cached, else a Range request."""
    full = _read_cache(url) or {}
    if sha and full.get("sha") == sha and isinstance(full.get("data"), str):
        return full["data"]
    key = url + "#head"
    head = _read_cache(key) or {}
    if sha and head.get("sha") == sha and isinstance(head.get("data"), str):
        return head["data"]
    text, info = fetch(url, ttl=TTL_RAW, kind="text", refresh=bool(head), cache_key=key,
                       extra_headers={"Range": f"bytes=0-{HEAD_BYTES - 1}"})
    partial = info.get("status") == 206 or len(text.encode("utf-8", "ignore")) >= HEAD_BYTES
    if partial and text.lstrip("\ufeff").startswith("---") and not re.search(r"\n(?:---|\.\.\.)[ \t]*(?:\n|$)", text[3:]):
        return _raw_by_sha(url, sha)
    entry = _read_cache(key)
    if entry is not None and sha and entry.get("sha") != sha:
        entry["sha"] = sha
        _write_cache(key, entry)
    return text

def _skill_meta(repo: str, branch: str, folder: Dict[str, Any], repo_row: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{RAW}/{repo}/{urllib.parse.quote(branch)}/{urllib.parse.quote(folder['skill_md'])}"
    files = folder["files"]
    base = {
        "id": f"gh:{repo}/{folder['path']}" if folder["path"] else f"gh:{repo}",
        "repo": repo, "path": folder["path"], "branch": branch, "sha": folder["sha"],
        "url": f"https://github.com/{repo}/tree/{branch}/{folder['path']}" if folder["path"] else f"https://github.com/{repo}",
        "stars": repo_row.get("stars", 0), "license": repo_row.get("license", ""), "author": repo_row.get("author", ""),
        "updated": repo_row.get("updated", ""), "files": files, "file_count": len(files),
        "has_scripts": any(f["kind"] in ("script", "binary") for f in files),
        "scripts": [f["path"] for f in files if f["kind"] in ("script", "binary")][:30],
    }
    fallback = folder["path"].rsplit("/", 1)[-1] if folder["path"] else repo.split("/")[1]
    if folder["size"] > MAX_SKILL_MD:
        return {**base, "name": fallback, "slug": slugify(fallback), "description": "", "too_large": True}
    try:
        text = _skill_head(url, folder["sha"])
        skill = parse_skill_md(text, fallback)
    except (CatalogError, ValueError):
        return {**base, "name": fallback, "slug": slugify(fallback), "description": "", "unavailable": True}
    license_ = skill.get("license") or base["license"]
    return {**base, "name": skill["name"], "slug": skill["slug"], "description": skill["description"][:600],
            "license": license_[:120], "category": skill.get("category", ""), "warnings": skill["warnings"],
            "size": folder["size"]}

def repo_skills(repo: str, *, refresh: bool = False, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    repo = check_repo(repo)
    meta = row or _repo_meta(repo, refresh=refresh)
    branch = meta.get("branch") or "main"
    folders = _skill_folders(_tree(repo, branch, refresh=refresh))
    truncated = len(folders) > MAX_SKILLS_PER_REPO
    folders = folders[:MAX_SKILLS_PER_REPO]
    with ThreadPoolExecutor(max_workers=8) as pool:
        skills = list(pool.map(lambda folder: _skill_meta(repo, branch, folder, meta), folders))
    return {"source": {**meta, "repo": repo, "count": len(skills), "truncated": truncated}, "skills": skills}

def discover(refresh: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Repositories tagged with skill topics, by stars (GitHub search; 10 req/min without a token)."""
    seen: Dict[str, Dict[str, Any]] = {}
    state = None
    for topic in TOPICS:
        url = f"{API}/search/repositories?q=topic:{topic}&sort=stars&order=desc&per_page=30"
        try:
            data, info = fetch(url, ttl=TTL_LIST, refresh=refresh)
            if info.get("state"):
                state = info["state"]
        except CatalogError as exc:
            state = exc.state
            continue
        for item in (data or {}).get("items", []) or []:
            row = _repo_row(item)
            if row["repo"] and row["repo"] not in seen:
                row["topic"] = topic
                seen[row["repo"]] = row
    rows = sorted(seen.values(), key=lambda r: -r["stars"])
    return rows[:60], state

def _source_result(spec: Dict[str, Any], refresh: bool) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        result = repo_skills(spec["repo"], refresh=refresh)
    except CatalogError as exc:
        return ({"repo": spec["repo"], "label_ru": spec["ru"], "label_en": spec["en"], "error": exc.state,
                 "official": bool(spec.get("official")), "count": 0}, [],
                {"repo": spec["repo"], "state": exc.state, "message": str(exc)[:200]})
    source = {**result["source"], "official": bool(spec.get("official")), "label_ru": spec["ru"], "label_en": spec["en"]}
    return source, [{**s, "official": bool(spec.get("official"))} for s in result["skills"]], None

def _assemble(done: Dict[int, Any], repos: List[Dict[str, Any]], discover_state: Optional[str], started: float,
              complete: bool) -> Dict[str, Any]:
    sources, skills, errors = [], [], []
    state = "ok"
    for index in sorted(done):
        source, rows, error = done[index]
        sources.append(source)
        skills += rows
        if error:
            errors.append(error)
            if error["state"] in ("offline", "rate_limited"):
                state = error["state"]
    curated = {spec["repo"] for spec in SOURCES}
    repos = [r for r in repos if r["repo"] not in curated]
    if not skills and errors:
        state = errors[0]["state"]
    elif errors or discover_state in ("offline", "rate_limited"):
        state = "partial" if skills else (discover_state or state)
    return {"state": state, "sources": sources, "skills": skills, "repos": repos, "errors": errors,
            "rate": {"remaining": _rate["remaining"], "reset": _rate["reset"], "limited_until": _rate["limited_until"]},
            "fetched_at": int(time.time()), "took_ms": int((time.time() - started) * 1000), "complete": complete}

def catalog(refresh: bool = False, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Every curated source and the discovered repositories, fetched in parallel (a bounded pool).

    ``on_progress`` receives the catalog assembled so far each time one source finishes (progressive output).
    """
    started = time.time()
    done: Dict[int, Any] = {}
    found: Dict[str, Any] = {"repos": [], "state": None}
    with ThreadPoolExecutor(max_workers=len(SOURCES) + 1) as pool:
        futures = {pool.submit(_source_result, spec, refresh): index for index, spec in enumerate(SOURCES)}
        searching = pool.submit(discover, refresh)
        for future in as_completed(list(futures)):
            done[futures[future]] = future.result()
            if on_progress and len(done) < len(SOURCES):
                on_progress(_assemble(dict(done), [], None, started, False))
        found["repos"], found["state"] = searching.result()
    return _assemble(done, found["repos"], found["state"], started, True)

_snap_cond = threading.Condition()
_snap: Dict[str, Any] = {"data": None, "partial": None, "version": 0, "running": False, "loaded": False}

def _snapshot_file() -> Path:
    return cache_dir() / "catalog_snapshot.json"

def _snapshot_view() -> Dict[str, Any]:
    """The catalog to show right now: the last complete one, else what a first load has fetched so far."""
    data = _snap["data"] or _snap["partial"] or {"state": "loading", "sources": [], "skills": [], "repos": [], "errors": [],
                                                  "rate": dict(_rate), "fetched_at": 0}
    age = time.time() - float(data.get("fetched_at") or 0)
    return {**data, "refreshing": _snap["running"], "version": _snap["version"],
            "stale": bool(_snap["data"]) and age > TTL_LIST, "cached": bool(_snap["data"]) and _snap["running"]}

def _refresh_worker(refresh: bool) -> None:
    def progress(partial: Dict[str, Any]) -> None:
        with _snap_cond:
            _snap["partial"] = partial
            _snap["version"] += 1
            _snap_cond.notify_all()
    try:
        result = catalog(refresh=refresh, on_progress=progress)
    except Exception as exc:  # pragma: no cover - catalog() turns network errors into states; this is a last resort
        result = None
        error = {"state": "error", "message": f"{type(exc).__name__}: {exc}"[:200]}
    with _snap_cond:
        if result is not None and (result["skills"] or not _snap["data"]):
            _snap["data"] = result
            try:
                tmp = _snapshot_file().with_suffix(".tmp")
                tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, _snapshot_file())
            except OSError:
                pass
        elif result is not None and _snap["data"]:

            _snap["data"] = {**_snap["data"], "state": result["state"] if result["state"] != "ok" else _snap["data"]["state"],
                             "rate": result["rate"]}
        elif result is None and not _snap["data"]:
            _snap["data"] = {**_snapshot_view(), **error, "errors": [error]}
        _snap["partial"] = None
        _snap["running"] = False
        _snap["version"] += 1
        _snap_cond.notify_all()

def catalog_swr(refresh: bool = False, since: Optional[int] = None, wait: float = 20.0) -> Dict[str, Any]:
    """Stale-while-revalidate: answer at once from the last catalog (memory, then disk) and refresh in the background.

    ``since`` long-polls: it returns when the snapshot version moves past it (a source finished, or the refresh
    ended), so the window shows sources as they arrive without a timer.
    """
    with _snap_cond:
        if not _snap["loaded"]:
            _snap["loaded"] = True
            try:
                saved = json.loads(_snapshot_file().read_text(encoding="utf-8"))
                if isinstance(saved, dict) and isinstance(saved.get("skills"), list):
                    _snap["data"] = saved
            except (OSError, ValueError):
                pass
        data = _snap["data"]
        old = not data or time.time() - float(data.get("fetched_at") or 0) > TTL_LIST
        if not _snap["running"] and since is None and (refresh or old):
            _snap["running"] = True
            _snap["version"] += 1
            threading.Thread(target=_refresh_worker, args=(refresh,), name="skills-catalog", daemon=True).start()
        if since is not None:
            deadline = time.time() + max(0.0, min(wait, 25.0))
            while _snap["running"] and _snap["version"] <= since and time.time() < deadline:
                _snap_cond.wait(timeout=max(0.05, deadline - time.time()))
        return _snapshot_view()

def skill_detail(repo: str, path: str) -> Dict[str, Any]:
    repo, path = check_repo(repo), check_path(path)
    meta = _repo_meta(repo)
    branch = meta.get("branch") or "main"
    folders = _skill_folders(_tree(repo, branch))
    folder = next((f for f in folders if f["path"] == path), None)
    if not folder:
        raise CatalogError("not_found", "no SKILL.md in that folder")
    url = f"{RAW}/{repo}/{urllib.parse.quote(branch)}/{urllib.parse.quote(folder['skill_md'])}"
    text = _raw_by_sha(url, folder["sha"])
    fallback = path.rsplit("/", 1)[-1] if path else repo.split("/")[1]
    skill = parse_skill_md(text, fallback)
    files = folder["files"]
    warnings = list(skill["warnings"])
    if any(f["kind"] == "script" for f in files):
        warnings.append("has_scripts")
    if any(f["kind"] == "binary" and os.path.splitext(f["path"].lower())[1] in SCRIPT_EXT for f in files):
        warnings.append("has_binaries")
    return {"repo": repo, "path": path, "branch": branch, "sha": folder["sha"], "source": meta,
            "url": f"https://github.com/{repo}/tree/{branch}/{path}" if path else f"https://github.com/{repo}",
            "raw_url": url, **skill, "files": files, "warnings": sorted(set(warnings)),
            "has_scripts": any(f["kind"] in ("script", "binary") for f in files)}

def file_text(repo: str, path: str) -> Dict[str, Any]:
    repo, path = check_repo(repo), check_path(path)
    if not path:
        raise CatalogError("invalid", "path required")
    if file_kind(path) == "binary":
        return {"repo": repo, "path": path, "binary": True, "text": ""}
    meta = _repo_meta(repo)
    url = f"{RAW}/{repo}/{urllib.parse.quote(meta.get('branch') or 'main')}/{urllib.parse.quote(path)}"
    text, _ = fetch(url, ttl=TTL_RAW, kind="text")
    clean, warnings = sanitize_markdown(text)
    truncated = len(clean) > MAX_FILE_VIEW
    return {"repo": repo, "path": path, "kind": file_kind(path), "text": clean[:MAX_FILE_VIEW], "truncated": truncated,
            "warnings": warnings}

_GH_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)(?:/(tree|blob)/([^/]+)(?:/(.*))?)?/?(?:[#?].*)?$", re.I)
_RAW_URL = re.compile(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", re.I)

def resolve_url(url: str) -> Dict[str, Any]:
    """Skills behind a GitHub link: a repository, a folder (``/tree/…``) or a SKILL.md (``/blob/…`` or raw)."""
    url = str(url or "").strip()
    match = _GH_URL.match(url)
    raw = _RAW_URL.match(url)
    if match:
        owner, name, _, _, sub = match.groups()
        repo, sub = f"{owner}/{name.removesuffix('.git')}", (sub or "").strip("/")
    elif raw:
        owner, name, _, sub = raw.groups()
        repo = f"{owner}/{name}"
    else:
        raise CatalogError("invalid", "only github.com and raw.githubusercontent.com links are supported")
    repo = check_repo(repo)
    if sub.upper().endswith("SKILL.MD"):
        sub = sub.rsplit("/", 1)[0] if "/" in sub else ""
    elif sub.lower().endswith(".md"):
        sub = sub.rsplit("/", 1)[0] if "/" in sub else ""
    result = repo_skills(repo)
    if sub:
        result["skills"] = [s for s in result["skills"] if s["path"] == sub or s["path"].startswith(sub + "/")]
    return result

def code_skills_root() -> Path:
    """The personal MemuCode skills folder shared by the MemuCode window and MemuCode CLI."""
    override = os.environ.get("MEMUCODE_CONFIG_DIR", "").strip()
    base = Path(override) if override else _data_dir() / "memucode"
    return base / "skills"

def install_code(repo: str, path: str, include_scripts: bool = False) -> Dict[str, Any]:
    detail = skill_detail(repo, path)
    slug = slugify(detail["slug"]) or "skill"
    if slug in ("con", "prn", "aux", "nul"):
        slug += "-skill"
    root = code_skills_root()
    target = root / slug
    if target.exists() and not (target / ".memuai-source.json").exists():
        raise CatalogError("exists", f"a skill named {slug} already exists")
    branch = detail["branch"]
    raw_text, _ = fetch(detail["raw_url"], ttl=TTL_RAW, kind="text")
    clean, _ = sanitize_markdown(raw_text)
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(clean, encoding="utf-8")
    written, skipped, total = ["SKILL.md"], [], len(clean)
    for item in detail["files"]:
        kind = item["kind"]
        if kind == "binary" or (kind == "script" and not include_scripts) or item["size"] > MAX_FILE_VIEW * 4:
            skipped.append(item["path"])
            continue
        if total + item["size"] > MAX_CODE_INSTALL:
            skipped.append(item["path"])
            continue
        rel = check_path(item["path"])
        full = (target / rel).resolve()
        if not str(full).startswith(str(target.resolve())):
            skipped.append(item["path"])
            continue
        src = f"{RAW}/{repo}/{urllib.parse.quote(branch)}/{urllib.parse.quote((path + '/' if path else '') + rel)}"
        try:
            text, _ = fetch(src, ttl=TTL_RAW, kind="text")
        except CatalogError:
            skipped.append(item["path"])
            continue
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
        written.append(rel)
        total += len(text)
    (target / ".memuai-source.json").write_text(json.dumps({
        "repo": repo, "path": path, "sha": detail["sha"], "branch": branch, "installed_at": int(time.time()),
        "scripts_included": bool(include_scripts)}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"slug": slug, "folder": str(target), "written": written, "skipped": skipped}

def installed_code() -> List[Dict[str, Any]]:
    root = code_skills_root()
    rows = []
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            marker = folder / ".memuai-source.json"
            if marker.exists():
                try:
                    info = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    info = {}
                rows.append({"slug": folder.name, **{k: info.get(k) for k in ("repo", "path", "sha", "installed_at")}})
    return rows

def uninstall_code(slug: str) -> Dict[str, Any]:
    slug = slugify(slug)
    target = code_skills_root() / slug
    if not slug or not (target / ".memuai-source.json").exists():
        raise CatalogError("not_found", "only skills installed from the catalog can be removed here")
    trash = _data_dir() / "memucode" / "trash" / "skills"
    trash.mkdir(parents=True, exist_ok=True)
    import shutil
    import uuid
    dest = trash / f"{slug}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    shutil.move(str(target), str(dest))
    return {"slug": slug, "trash": str(dest)}

def library_file() -> Path:
    path = _data_dir() / "memuai"
    path.mkdir(parents=True, exist_ok=True)
    return path / "skills_library.json"

def load_library() -> Dict[str, Any]:
    try:
        data = json.loads(library_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_library(data: Dict[str, Any]) -> Dict[str, Any]:
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise ValueError("skills must be a list")
    payload = {"skills": [s for s in skills if isinstance(s, dict)][:1000], "version": int(data.get("version") or 0),
               "removed": [str(x)[:80] for x in (data.get("removed") or []) if isinstance(x, (str, int))][:200],
               "saved_at": int(time.time())}
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 16 * 1024 * 1024:
        raise ValueError("library is too large")
    path = library_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, "count": len(payload["skills"]), "saved_at": payload["saved_at"]}

def register(app) -> None:
    import asyncio
    from fastapi.responses import JSONResponse, Response

    async def run(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except CatalogError as exc:
            status = {"invalid": 400, "not_found": 404, "rate_limited": 429, "offline": 503, "exists": 409,
                      "too_large": 413}.get(exc.state, 502)
            return JSONResponse({"error": str(exc)[:300], "state": exc.state, "reset": exc.reset}, status)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)[:300], "state": "invalid"}, 400)

    async def body_of(request: Request, limit: int = 8 * 1024 * 1024) -> Dict[str, Any]:
        raw = await request.body()
        if len(raw) > limit:
            raise ValueError("request is too large")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    @app.get("/api/pg/skills-catalog")
    async def skills_catalog(request: Request):
        params = request.query_params
        refresh = params.get("refresh") in ("1", "true")
        if params.get("swr") in ("1", "true"):
            since = params.get("since", "")
            return await run(catalog_swr, refresh=refresh, since=int(since) if since.isdigit() else None)
        return await run(catalog, refresh=refresh)

    @app.get("/api/pg/skills-catalog/repo")
    async def skills_catalog_repo(request: Request):
        return await run(repo_skills, request.query_params.get("repo", ""),
                         refresh=request.query_params.get("refresh") in ("1", "true"))

    @app.get("/api/pg/skills-catalog/skill")
    async def skills_catalog_skill(request: Request):
        return await run(skill_detail, request.query_params.get("repo", ""), request.query_params.get("path", ""))

    @app.get("/api/pg/skills-catalog/file")
    async def skills_catalog_file(request: Request):
        return await run(file_text, request.query_params.get("repo", ""), request.query_params.get("path", ""))

    @app.post("/api/pg/skills-catalog/resolve")
    async def skills_catalog_resolve(request: Request):
        try:
            body = await body_of(request, 64 * 1024)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)
        return await run(resolve_url, body.get("url", ""))

    @app.post("/api/pg/skills/parse")
    async def skills_parse(request: Request):
        try:
            body = await body_of(request, MAX_ZIP * 2)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)

        def work() -> Dict[str, Any]:
            name = str(body.get("name") or "")[:200]
            if body.get("data_base64"):
                try:
                    data = base64.b64decode(str(body["data_base64"]), validate=False)
                except (ValueError, TypeError) as exc:
                    raise ValueError("bad base64") from exc
                if data[:2] == b"PK":
                    return {"skills": parse_zip(data)}
                text = data.decode("utf-8", "replace")
            else:
                text = str(body.get("text") or "")
            fallback = re.sub(r"\.(md|markdown|txt)$", "", os.path.basename(name), flags=re.I)
            if fallback.upper() == "SKILL":
                fallback = ""
            return {"skills": [parse_skill_md(text, fallback or "skill")]}
        return await run(work)

    @app.post("/api/pg/skills/export")
    async def skills_export(request: Request):
        try:
            body = await body_of(request, 32 * 1024 * 1024)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)
        skills = [s for s in (body.get("skills") or []) if isinstance(s, dict)]
        if not skills:
            return JSONResponse({"error": "no skills", "state": "invalid"}, 400)
        if body.get("format") == "md" and len(skills) == 1:
            slug, text = skill_to_md(skills[0])
            return Response(text.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{slug or "skill"}-SKILL.md"'})
        data = await asyncio.to_thread(build_zip, skills)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="memuai-skills.zip"'})

    @app.get("/api/pg/skills/library")
    async def skills_library_get():
        return await run(load_library)

    @app.post("/api/pg/skills/library")
    async def skills_library_post(request: Request):
        try:
            body = await body_of(request, 17 * 1024 * 1024)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)
        return await run(save_library, body)

    @app.get("/api/pg/skills/code-installed")
    async def skills_code_installed():
        return await run(lambda: {"skills": installed_code(), "folder": str(code_skills_root())})

    @app.post("/api/pg/skills-catalog/install-code")
    async def skills_install_code(request: Request):
        try:
            body = await body_of(request, 64 * 1024)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)
        return await run(install_code, body.get("repo", ""), body.get("path", ""), bool(body.get("include_scripts")))

    @app.post("/api/pg/skills-catalog/uninstall-code")
    async def skills_uninstall_code(request: Request):
        try:
            body = await body_of(request, 64 * 1024)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "state": "invalid"}, 400)
        return await run(uninstall_code, body.get("slug", ""))
