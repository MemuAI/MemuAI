"""Round 30: auto-complete of MemuCode commands *and their arguments* for the Mini App (``code.complete``).

The terminal completes ``/model <text>``, ``/resume``, ``/permissions``, ``/cd <path>``, ``@file`` and the fixed
words of a command (``app/cli/tui/input.py: MemuCompleter``); that class needs prompt_toolkit and the live
terminal's state, so the phone gets the same suggestions from the Proxy here, from real data:

* commands — the catalogue the terminal publishes (``<rc>.commands.json``, ``tgapp_code.command_catalog``);
* models — the terminal's model list + the chat models of the running Proxy;
* sessions — MemuCode conversations of the project (``/resume``);
* permission modes, effort and other fixed words — ``CHOICES`` + the choices written in a command's hint
  (``[auto|minimal|low|…]``);
* folders and files — ``/cd``, ``/add-dir`` (any folder, like the «new session» picker: names of folders only),
  ``/attach``, ``/image``, ``/add``, ``/diff``, ``@mentions`` (files only inside the project folder).

``complete(text)`` gets the text *before the cursor* and returns ``{"kind", "span", "items"}``: the client replaces
the last ``span`` characters with an item's ``value``. Pure Python, no prompt_toolkit (the Proxy bundle).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_ITEMS = 40
INDEX_LIMIT = 20000
INDEX_TTL = 30.0
SCAN_LIMIT = 5000
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".memucode-cache", ".idea",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".gradle", ".tox"}

MODES = (
    ("default", "Спрашивать перед правками и командами", "Ask before edits and commands"),
    ("acceptEdits", "Правки без вопросов, команды — с разрешением", "Edits without asking, commands with approval"),
    ("plan", "Сначала план, без правок", "Plan first, no edits"),
    ("auto", "Безопасное — само, рискованное — с вопросом", "Safe steps on its own, risky ones ask"),
    ("bypassPermissions", "Всё без вопросов — опасно", "Everything without asking — dangerous"),
)
EFFORT = (
    ("auto", "Сама решает", "Decides itself"), ("minimal", "Почти без рассуждений", "Barely thinks"),
    ("low", "Коротко", "Short"), ("medium", "Обычно", "Normal"), ("high", "Глубоко", "Deep"),
    ("max", "Максимально глубоко", "As deep as possible"),
)
#: Fixed words after a command (mirrors ``MemuCompleter.CHOICES`` of the terminal).
CHOICES: Dict[str, Tuple[str, ...]] = {
    "goal": ("status", "pause", "continue", "clear"),
    "theme": ("dark", "light", "dark-daltonized", "light-daltonized"),
    "config": ("list", "get", "set", "unset", "edit"),
    "cost": ("set", "refresh", "prices"),
    "stats": ("7", "30", "all"),
    "schedule": ("list", "new", "run", "delete", "pause", "resume"),
    "skills": ("list", "new", "edit", "delete", "disable", "enable", "view", "restore"),
    "import": ("claude", "codex", "cursor", "agy", "grok", "gemini", "opencode", "all"),
    "dashboard": ("claude", "codex", "cursor", "agy", "grok", "gemini", "opencode"),
    "think": ("on", "off"), "web": ("on", "off"),
    "proxy": ("status", "add", "remove"),
    # Round 33: MemuCode /auto-continue (mirrors app/cli/tui/input.py)
    "auto-continue": ("auto", "status", "cancel", "now", "+15m", "+30m", "+1h", "+2h", "+5h", "tomorrow 09:00"),
}
MODEL_COMMANDS = {"model": ("auto", "notes"), "small-model": (), "advisor": ("off",), "vision": ("auto", "on", "off", "test")}
FOLDER_COMMANDS = {"cd", "add-dir"}
FILE_COMMANDS = {"attach", "image", "add", "diff", "export", "explain", "refactor", "review", "test"}
#: Words of a hint that name what to type, not a value (``[model|auto]`` → only ``auto`` is a choice).
PLACEHOLDERS = {"name", "model", "color", "#hex", "id", "words", "task", "tool", "number", "n", "file", "path", "target",
                "query", "question", "instructions", "command", "style", "mode", "search", "text", "url", "interval",
                "fact", "goal", "description", "files", "next", "request", "when", "prompt", "builtin"}
MENTION = re.compile(r"(?:^|[\s(])@(\"[^\"]*|[^\s@\"]*)$")

_index_lock = threading.Lock()
_index: Dict[str, Tuple[float, List[str]]] = {}


def _t(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


# --------------------------------------------------------------------------- matching

def _score(query: str, value: str, extra: str = "") -> Optional[float]:
    """Every word of ``query`` must match ``value`` (prefix > word start > inside) or, weaker, ``extra``."""
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    if not words:
        return 0.0
    low, more = value.lower(), extra.lower()
    parts = [p for p in re.split(r"[^a-z0-9а-яё]+", low) if p]
    total = 0.0
    for word in words:
        if low == word:
            total += 1000
        elif low.startswith(word):
            total += 600
        elif any(part.startswith(word) for part in parts):
            total += 400
        elif word in low:
            total += 200
        elif word in more:
            total += 50
        else:
            return None
    return total - len(low) * 0.01


def _ranked(query: str, rows: Iterable[Dict[str, Any]], *, words_only: bool = False) -> List[Dict[str, Any]]:
    """Best matches first; with nothing typed — in the given order. ``words_only``: fixed words are matched by
    themselves, not by their explanation (``/permissions a`` → acceptEdits, auto — not «default»)."""
    scored = []
    for index, row in enumerate(rows):
        score = _score(query, row["value"], "" if words_only else row.get("meta", "") + " " + row.get("label", ""))
        if score is not None and (not words_only or score >= 400 or not query.strip()):
            scored.append((score, index, row))
    if not query.strip():
        scored.sort(key=lambda item: item[1])
    else:
        scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _s, _i, row in scored[:MAX_ITEMS]]


# --------------------------------------------------------------------------- data

def hint_choices(hint: str) -> List[str]:
    """``[auto|minimal|low]`` → the words; placeholders (``model``, ``path``) and free text are left out."""
    hint = str(hint or "").strip()
    if "|" not in hint:
        return []
    first = re.split(r"\]\s|\>\s", hint, maxsplit=1)[0]
    words = [w.strip(" []<>.…") for w in first.split("|")]
    out = [w for w in words if w and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", w) and w.lower() not in PLACEHOLDERS]
    return out[:20]


def _resolve(name: str, commands: Sequence[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    low = name.lower()
    for row in commands:
        if str(row.get("name") or "").lower() == low:
            return low, row
    for row in commands:
        if low in [str(a).lower() for a in row.get("aliases") or []]:
            return str(row.get("name") or low).lower(), row
    return low, {}


def project_files(root: str) -> List[str]:
    """Relative paths (``/`` separators, folders end with ``/``) of the project, cached for a little while."""
    now = time.monotonic()
    with _index_lock:
        cached = _index.get(root)
        if cached and now - cached[0] < INDEX_TTL:
            return cached[1]
    out: List[str] = []
    base = Path(root)
    stack = [base]
    while stack and len(out) < INDEX_LIMIT:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                items = sorted(entries, key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in items:
            if entry.name.startswith(".") and entry.name not in (".github", ".memucode"):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            rel = Path(entry.path).relative_to(base).as_posix()
            if is_dir:
                if entry.name in SKIP_DIRS:
                    continue
                out.append(rel + "/")
                stack.append(Path(entry.path))
            else:
                out.append(rel)
            if len(out) >= INDEX_LIMIT:
                break
    with _index_lock:
        if len(_index) > 16:
            _index.clear()
        _index[root] = (now, out)
    return out


def _quote(value: str, folder: bool) -> str:
    if " " not in value:
        return value
    return '"' + value if folder else '"' + value + '"'


def _mentions(raw: str, root: str, lang: str) -> List[Dict[str, Any]]:
    query = raw.lstrip('"').replace("\\", "/")
    if not root or not Path(root).is_dir():
        return []
    rows = project_files(root)
    low = query.lower()
    if not low:
        picked = sorted(rows, key=lambda r: (r.rstrip("/").count("/"), not r.endswith("/"), r.lower()))[:MAX_ITEMS]
    else:
        scored = []
        for rel in rows:
            name = rel.lower()
            if low not in name:
                continue
            leaf = name.rstrip("/").rsplit("/", 1)[-1]
            score = (3 if leaf.startswith(low) else 2 if name.startswith(low) else 1, -len(name))
            scored.append((score, rel))
        scored.sort(key=lambda item: (-item[0][0], -item[0][1]))
        picked = [rel for _s, rel in scored[:MAX_ITEMS]]
    return [{"value": "@" + _quote(rel, rel.endswith("/")), "label": rel, "kind": "folder" if rel.endswith("/") else "file",
             "meta": _t(lang, "папка", "folder") if rel.endswith("/") else _t(lang, "файл", "file"),
             "final": not rel.endswith("/")} for rel in picked]


def _paths(raw: str, root: str, *, folders_only: bool, lang: str) -> List[Dict[str, Any]]:
    """Entries of the folder the typed path points into. Folders anywhere (like the «new session» picker);
    files only inside the project folder."""
    raw = re.sub(r"\s+(?:--new|-n)\s*$", "", raw)
    query = raw.strip('"')
    typed = Path(query).expanduser() if query else Path("")
    ends = query.endswith(("/", "\\"))
    base = typed if (ends or not query) else typed.parent
    if not base.is_absolute():
        if not root:
            return []
        base = Path(root) / base
    prefix = "" if ends or not query else typed.name.lower()
    try:
        base = base.resolve()
    except OSError:
        return []
    inside = False
    if root:
        try:
            base.relative_to(Path(root).resolve())
            inside = True
        except ValueError:
            inside = False
    found: List[Tuple[bool, str, str]] = []
    try:
        with os.scandir(base) as entries:
            for count, entry in enumerate(entries):
                if count >= SCAN_LIMIT:
                    break
                name = entry.name
                if name.startswith(".") and not prefix.startswith("."):
                    continue
                if prefix and not name.lower().startswith(prefix):
                    continue
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if not is_dir and (folders_only or not inside):
                    continue
                if is_dir and name in SKIP_DIRS and not prefix:
                    continue
                found.append((not is_dir, name.lower(), name))
    except OSError:
        return []
    found.sort()
    shown_base = query[: len(query) - len(typed.name)] if query and not ends else query
    out = []
    for not_dir, _low, name in found[:MAX_ITEMS]:
        value = shown_base + name + ("/" if not not_dir else "")
        out.append({"value": _quote(value, not not_dir), "label": value, "kind": "file" if not_dir else "folder",
                    "meta": _t(lang, "файл", "file") if not_dir else _t(lang, "папка", "folder"),
                    "final": not_dir or folders_only})
    return out


def _sessions(root: str, lang: str) -> List[Dict[str, Any]]:
    try:
        from .cli.session import list_sessions
        rows = list_sessions(Path(root), limit=60) if root else list_sessions(limit=60)
    except Exception:
        return []
    out = []
    for row in rows:
        session_id = str(row.get("id") or "")
        if not session_id:
            continue
        title = str(row.get("title") or row.get("first") or "").strip()
        out.append({"value": session_id, "label": title[:80] or session_id, "meta": session_id if title else "",
                    "kind": "session", "final": True})
    return out


def _models(query: str, names: Sequence[str], extra_words: Sequence[str], current: str, lang: str,
            labels: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    labels = labels or {}
    rows = [{"value": word, "label": word, "meta": _t(lang, "режим", "option"), "kind": "choice", "final": True}
            for word in extra_words if word.startswith(query.strip().lower())]
    seen = set()
    for model_id in names:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        meta = labels.get(model_id, "")
        if model_id == current:
            meta = (_t(lang, "сейчас", "current") + (" · " + meta if meta and meta != model_id else "")).strip()
        rows.append({"value": model_id, "label": model_id, "meta": "" if meta == model_id else meta, "kind": "model",
                     "final": True})
    return rows


def _commands(query: str, commands: Sequence[Dict[str, Any]], lang: str) -> List[Dict[str, Any]]:
    rows = []
    for row in commands:
        name = str(row.get("name") or "")
        if not name:
            continue
        text = (row.get("en") if lang == "en" else row.get("ru")) or row.get("description") or ""
        rows.append({"value": "/" + name, "label": "/" + name, "hint": str(row.get("hint") or ""), "meta": str(text)[:200],
                     "kind": "command", "final": not str(row.get("hint") or "").startswith("<"),
                     "aliases": [str(a) for a in row.get("aliases") or []]})
    q = query.lower()
    if not q:
        return rows[:MAX_ITEMS]
    scored = []
    for index, row in enumerate(rows):
        name = row["value"][1:].lower()
        aliases = [a.lower() for a in row["aliases"]]
        if name == q or q in aliases:
            score = 100.0
        elif name.startswith(q):
            score = 80 - len(name) / 100
        elif any(a.startswith(q) for a in aliases):
            score = 60.0
        elif q in name:
            score = 40.0
        elif len(q) >= 3 and q in row["meta"].lower():
            score = 10.0
        else:
            continue
        scored.append((score, index, row))
    scored.sort(key=lambda item: (-item[0], item[2]["value"]))
    return [row for _s, _i, row in scored[:MAX_ITEMS]]


# --------------------------------------------------------------------------- entry point

def complete(text: str, *, root: str = "", catalog: Optional[Dict[str, Any]] = None, models: Sequence[str] = (),
             model_labels: Optional[Dict[str, str]] = None, lang: str = "ru") -> Dict[str, Any]:
    """Suggestions for the text before the cursor (one line of the MemuCode input on the phone)."""
    text = str(text or "")[-2000:]
    catalog = catalog or {}
    commands = [row for row in catalog.get("commands") or [] if isinstance(row, dict)]
    lang = "en" if lang == "en" else "ru"
    empty = {"kind": "", "span": 0, "items": []}
    if "\n" in text:
        return empty
    if text.startswith("/") and " " not in text:
        return {"kind": "command", "span": len(text), "items": _commands(text[1:], commands, lang)}
    match = MENTION.search(text)
    if match:
        raw = match.group(1)
        return {"kind": "mention", "span": len(raw) + 1, "items": _mentions(raw, root, lang)}
    if not text.startswith("/"):
        return empty
    head, _, raw = text.partition(" ")
    name, row = _resolve(head[1:], commands)
    args = raw.split(" ")
    span = len(raw)
    items: List[Dict[str, Any]] = []
    kind = "argument"
    if name in FOLDER_COMMANDS:
        kind, items = "path", _paths(raw, root, folders_only=True, lang=lang)
    elif name in FILE_COMMANDS and len(args) == 1 and (raw or name in ("attach", "image", "add", "diff")):
        kind, items = "path", _paths(raw, root, folders_only=False, lang=lang)
    elif name in MODEL_COMMANDS and len(args) == 1:
        names = [str(x) for x in catalog.get("models") or []] + [str(x) for x in models]
        items = _ranked(raw, _models(raw, names, MODEL_COMMANDS[name], str(catalog.get("model") or ""), lang, model_labels))
    elif name in ("mode", "permissions", "allowed-tools", "approvals") and len(args) == 1:
        items = _ranked(raw, [{"value": mode, "label": mode, "meta": _t(lang, ru, en), "kind": "choice", "final": True}
                              for mode, ru, en in MODES], words_only=True)
    elif name == "effort" and len(args) == 1:
        items = _ranked(raw, [{"value": word, "label": word, "meta": _t(lang, ru, en), "kind": "choice", "final": True}
                              for word, ru, en in EFFORT], words_only=True)
    elif name in ("resume", "sessions") and len(args) == 1:
        items = _ranked(raw, _sessions(root, lang))
    elif name in ("help", "?", "h") and len(args) == 1:
        items = [dict(item, value=item["value"][1:], final=True) for item in _commands(raw, commands, lang)]
    elif name == "skills" and len(args) == 2 and args[0] in ("edit", "delete", "disable", "enable", "view", "restore"):
        span = len(args[1])
        skills = [r for r in commands if "skill" in str(r.get("source") or "").lower()]
        items = _ranked(args[1], [{"value": str(r["name"]), "label": str(r["name"]),
                                   "meta": str(r.get("description") or "")[:120], "kind": "choice", "final": True} for r in skills])
    elif len(args) == 1:
        words = CHOICES.get(name) or tuple(hint_choices(str(row.get("hint") or "")))
        items = _ranked(raw, [{"value": w, "label": w, "meta": "", "kind": "choice", "final": True} for w in words],
                        words_only=True)
    return {"kind": kind, "span": span, "items": items, "hint": str(row.get("hint") or "")}
