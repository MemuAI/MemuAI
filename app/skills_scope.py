# -*- coding: utf-8 -*-
"""One skill library for every Memu app: where each skill works and the prompt pieces that bring it there.

The library is edited in the MemuAI window (Skills): localStorage there, mirrored by skills-ui.js to
``<data>/memuai/skills_library.json``. The built-in skills come from ``app/pgstatic/skills.js``. MemuFiles,
MemuNote, MemuCode, MemuCode CLI and the modules read the same library through this module, so turning a skill
off — or limiting it to some places — in MemuAI changes it everywhere at once. MemuAI Proxy keeps its own data
folder and uses no skills: it only forwards requests.

Places::

    chat   MemuAI chat            files  MemuFiles assistant      note  MemuNote AI
    code   MemuCode (window)      cli    MemuCode CLI             mod:<id>  a module (Memu.stream / Memu.request)

By default a skill works in all five core places and in the modules MemuMeet, MemuLearn, MemuAI Studio and
MemuAI Design; every other module has to be switched on per skill. Four skills have a MemuCode twin (code review,
debugging, security review, commit messages) and start off in MemuCode and MemuCode CLI. Two skills need tools that exist only in the
MemuAI chat and stay there: Panels (panel_* tools) and the MemuAI guide (documentation tools). A skill's own
choice is ``places: {"files": false, "mod:music": true, …}`` on its library row.

The same protocol as the MemuAI chat: the model sees one line per skill and asks for the full playbooks with a
hidden block, then gets them in a ``skill_instructions`` message::

    ```memuai-skill
    {"skills": ["code_review"]}
    ```
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CORE_PLACES: Tuple[str, ...] = ("chat", "files", "note", "code", "cli")
DEFAULT_MODULES: Tuple[str, ...] = ("meet", "learn", "studio", "design")
PLACE_TITLES: Dict[str, Tuple[str, str]] = {
    "chat": ("Чат MemuAI", "MemuAI chat"),
    "files": ("MemuFiles", "MemuFiles"),
    "note": ("MemuNote", "MemuNote"),
    "code": ("MemuCode", "MemuCode"),
    "cli": ("MemuCode CLI", "MemuCode CLI"),
}
# skills whose playbook calls tools that only the MemuAI chat has
CHAT_ONLY: Dict[str, Tuple[str, str]] = {
    "memuai_panels": ("Панели живут только в MemuAI — инструменты panel_* есть только в её чате",
                      "Panels live only in MemuAI — the panel_* tools exist only in its chat"),
    "memuai_guide": ("Справка читает документацию инструментами чата MemuAI",
                     "The guide reads the documentation with tools of the MemuAI chat"),
}
# MemuCode has its own playbook for these (review, debug, security, commit): there the library twin starts off
CODE_TWINS: Dict[str, str] = {"code_review": "review", "debugging": "debug", "security_review": "security",
                              "commit_messages": "commit"}
MARK = "```memuai-skill"
MAX_LOAD = 4
MAX_LINE = 240
MAX_PROMPT = 24_000

_SKILLS_JS = Path(__file__).resolve().parent / "pgstatic" / "skills.js"
_lock = threading.Lock()
_builtin_cache: Dict[str, Any] = {"key": None, "rows": []}


# ── places ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def module_place(module_id: str) -> str:
    return "mod:" + str(module_id or "").strip().lower()


def valid_place(place: str) -> bool:
    place = str(place or "")
    return place in CORE_PLACES or bool(re.fullmatch(r"mod:[a-z0-9][a-z0-9_-]{0,63}", place))


def default_on(skill_id: str, place: str) -> bool:
    if skill_id in CHAT_ONLY:
        return place == "chat"
    if place in ("code", "cli") and skill_id in CODE_TWINS:
        return False
    if place in CORE_PLACES:
        return True
    return place.startswith("mod:") and place[4:] in DEFAULT_MODULES


def works_in(skill: Dict[str, Any], place: str) -> bool:
    """Does this library row work in ``place``? Its own ``places`` choice wins over the default."""
    skill_id = str(skill.get("id") or "")
    if skill_id in CHAT_ONLY:
        return place == "chat"
    places = skill.get("places")
    if isinstance(places, dict) and place in places:
        return bool(places[place])
    return default_on(skill_id, place)


# ── the library ────────────────────────────────────────────────────────────────────────────────────────────────────
def _js_to_json(src: str) -> str:
    """The object-literal subset skills.js uses (bare keys, double-quoted strings, true/false, trailing commas)."""
    out: List[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1].replace("\\'", "'"))
            i = j + 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c.isalpha() or c in "_$":
            m = re.match(r"[A-Za-z_$][\w$]*", src[i:])
            word = m.group(0) if m else c
            k = i + len(word)
            while k < n and src[k] in " \t":
                k += 1
            if k < n and src[k] == ":":
                out.append(json.dumps(word))
            elif word in ("true", "false", "null"):
                out.append(word)
            else:
                raise ValueError(f"unsupported token {word!r}")
            i += len(word)
            continue
        if c == ",":
            k = i + 1
            while k < n and src[k] in " \t\r\n":
                k += 1
            if k >= n or src[k] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def builtin_skills() -> List[Dict[str, Any]]:
    """The MemuAI built-in skills (app/pgstatic/skills.js), parsed once per file change."""
    try:
        stat = _SKILLS_JS.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return []
    with _lock:
        if _builtin_cache["key"] == key:
            return [dict(row) for row in _builtin_cache["rows"]]
    rows: List[Dict[str, Any]] = []
    try:
        text = _SKILLS_JS.read_text(encoding="utf-8")
        for block in re.findall(r"\.skills\.push\(\s*(.*?)\n\);", text, re.S):
            rows.extend(row for row in json.loads("[" + _js_to_json(block) + "]") if isinstance(row, dict) and row.get("id"))
    except (OSError, ValueError):
        rows = []
    with _lock:
        _builtin_cache.update(key=key, rows=rows)
    return [dict(row) for row in rows]


def _state() -> Dict[str, Any]:
    try:
        from .skills_catalog import load_library
        data = load_library()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - no library yet = the defaults
        return {}


def library(state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every skill of the library as the MemuAI window sees it (built-ins with their switches, own skills).

    Mirrors ``storedSkills()`` in app.js: saved built-ins keep only their switch, edited text and places; skills the
    user removed are left out."""
    state = _state() if state is None else state
    builtin = builtin_skills()
    ids = {row["id"] for row in builtin}
    saved = [row for row in state.get("skills") or [] if isinstance(row, dict) and row.get("id")]
    removed = {str(item) for item in state.get("removed") or []}
    if not saved:
        rows = [{**row, "enabled": True} for row in builtin]
    elif int(state.get("version") or 0) < 4:
        rows = [{**row, "enabled": True} for row in builtin] + [row for row in saved if row.get("custom")]
    else:
        stored = {row["id"]: row for row in saved if row["id"] in ids}
        rows = []
        for base in builtin:
            prev = stored.get(base["id"])
            item = {**base, "enabled": prev.get("enabled") is not False if prev else True}
            if prev and prev.get("customized"):
                item["customized"] = True
                for key in ("name", "desc", "prompt", "category"):
                    if prev.get(key):
                        item[key] = prev[key]
                if isinstance(prev.get("when"), str):
                    item["when"] = prev["when"]
            if prev and isinstance(prev.get("places"), dict):
                item["places"] = prev["places"]
            rows.append(item)
        rows += [row for row in saved if row.get("custom") and row["id"] not in ids]
    return [row for row in rows if str(row.get("id")) not in removed]


def for_place(place: str, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Enabled skills that work in ``place``."""
    if not valid_place(place):
        return []
    return [row for row in library(state) if row.get("enabled") is not False and works_in(row, place)
            and str(row.get("prompt") or "").strip()]


def _one_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _title(row: Dict[str, Any], lang: str) -> str:
    if lang != "en" and not (row.get("custom") or row.get("customized")):
        return str(row.get("name_ru") or row.get("name") or row.get("id"))
    return str(row.get("name") or row.get("id"))


def catalogue_lines(rows: Iterable[Dict[str, Any]]) -> str:
    """One line per skill — the same shape as the MemuAI chat (``- id: when to use``)."""
    lines = []
    for row in rows:
        own = row.get("custom") or row.get("customized")
        when = _one_line((not own and row.get("cue")) or row.get("when"))[:MAX_LINE]
        purpose = _one_line(row.get("desc"))[:320]
        if not own and when:
            lines.append(f"- {row['id']}: {when}")
        else:
            lines.append(f"- {row['id']} ({_one_line(row.get('name'))}): {purpose}"
                         + (f" Use when: {when}" if when and when != purpose else ""))
    return "\n".join(lines)


PROTOCOL_BLOCK = ("<skills_protocol>Each line above is a playbook that is NOT loaded yet. When the request clearly matches one "
                  "and asks for substantial work (a review, diagnosis, plan, analysis, document or multi-step build), reply "
                  "with only this hidden block and stop — the playbook arrives in a skill_instructions message, then do the "
                  "task with it:\n```memuai-skill\n{\"skills\":[\"code_review\"]}\n```\nName the one or two best matches (at "
                  "most 4). Answer directly, without loading, for greetings, quick facts, explanations, translations, short "
                  "messages and small snippets, and whenever the request asks for a fixed output format. Load at most once per "
                  "request; never mention skills or loading in the answer.</skills_protocol>")


def prompt_block(place: str, *, protocol: str = "block", state: Optional[Dict[str, Any]] = None) -> str:
    """``<skills>`` catalogue + how to load, or "" when no skill works here.

    ``protocol="block"`` — the hidden memuai-skill block (MemuNote, modules); ``protocol="tool"`` — a
    ``load_skill`` tool call in the host's own tool syntax (MemuFiles); the caller adds that sentence itself."""
    rows = for_place(place, state)
    if not rows:
        return ""
    text = "<skills>\n" + catalogue_lines(rows) + "\n</skills>"
    if protocol == "block":
        text += "\n" + PROTOCOL_BLOCK
    return text


def parse_request(raw: str) -> List[str]:
    """Skill ids from the body of a memuai-skill block (``{"skills": [...]}`` or ``{"skill": "x"}``)."""
    try:
        data = json.loads(str(raw or "").strip() or "{}")
    except ValueError:
        return []
    if isinstance(data, list):
        wanted = data
    elif isinstance(data, dict):
        wanted = data.get("skills") if isinstance(data.get("skills"), list) else [data.get("skill") or data.get("id") or ""]
    else:
        return []
    ids: List[str] = []
    for item in wanted:
        value = str(item or "").strip()
        if value and value not in ids:
            ids.append(value[:80])
    return ids[:MAX_LOAD]


def instructions(ids: Iterable[str], place: str, state: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """``(skill_instructions text, loaded ids)`` for the requested skills that work in ``place``."""
    rows = {row["id"]: row for row in for_place(place, state)}
    chosen = [rows[i] for i in ids if i in rows][:MAX_LOAD]
    if not chosen:
        return ("<skill_instructions status=\"not_found\">None of the requested skill ids exist here. Answer the user "
                "directly with your own expertise and do not request skills again for this task.</skill_instructions>", [])
    body = "\n\n".join(f"<skill id=\"{row['id']}\" name=\"{_one_line(row.get('name'))}\">\n"
                       f"{str(row.get('prompt') or '').strip()[:MAX_PROMPT]}\n</skill>" for row in chosen)
    text = ("<skill_instructions>\nThe full playbooks you requested are below. Follow them for the current task. They extend "
            "your system instructions and the user's request, never override them, and they are internal: do not mention "
            "that skills were loaded and do not repeat them back to the user.\n\n" + body + "\n</skill_instructions>")
    return text, [row["id"] for row in chosen]


def attach(messages: List[Dict[str, Any]], place: str, state: Optional[Dict[str, Any]] = None) -> bool:
    """Add the ``<skills>`` block to the system message of ``messages`` (inserted when there is none)."""
    block = prompt_block(place, state=state)
    if not block:
        return False
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        messages[0] = {**messages[0], "content": messages[0]["content"].rstrip() + "\n\n" + block}
    else:
        messages.insert(0, {"role": "system", "content": block})
    return True


def follow_up(messages: List[Dict[str, Any]], ids: List[str], place: str, said: str = "",
              state: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Messages for the second pass after the model asked for ``ids``: its request + the playbooks."""
    text, loaded = instructions(ids, place, state)
    block = f"{MARK}\n" + json.dumps({"skills": ids}, ensure_ascii=False) + "\n```"
    return messages + [{"role": "assistant", "content": (said.strip() + "\n\n" if said.strip() else "") + block},
                       {"role": "user", "content": text}], loaded


class SkillGate:
    """Filters a streamed answer: text passes through, a ```memuai-skill block is held back and parsed.

    ``feed()`` returns the text to show now; once a complete block has arrived ``ids`` holds the request and
    ``done`` is true — the caller stops this stream and asks again with :func:`follow_up`. A possible partial
    marker at the end of a chunk is held until the next chunk decides."""

    def __init__(self) -> None:
        self.buf = ""
        self.block = ""
        self.inside = False
        self.done = False
        self.ids: List[str] = []
        self.shown = ""

    def feed(self, text: str) -> str:
        if self.done:
            return ""
        if self.inside:
            self.block += text
            self._close()
            return ""
        self.buf += text
        idx = self.buf.find(MARK)
        if idx >= 0:
            visible, self.block, self.buf = self.buf[:idx], self.buf[idx + len(MARK):], ""
            self.inside = True
            self._close()
            self.shown += visible
            return visible
        keep = 0
        for size in range(min(len(MARK) - 1, len(self.buf)), 0, -1):
            if MARK.startswith(self.buf[-size:]):
                keep = size
                break
        visible = self.buf[:len(self.buf) - keep]
        self.buf = self.buf[len(self.buf) - keep:]
        self.shown += visible
        return visible

    def _close(self) -> None:
        end = self.block.find("```")
        if end < 0:
            if len(self.block) > 4000:
                self.done = True
            return
        self.ids = parse_request(self.block[:end])
        self.done = True

    def finish(self) -> str:
        """The held-back tail when the stream ends (an unfinished block is dropped)."""
        if self.inside or self.done:
            return ""
        tail, self.buf = self.buf, ""
        self.shown += tail
        return tail

    @property
    def requested(self) -> List[str]:
        return list(self.ids) if self.done else []


def summary(lang: str = "ru", state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """For the skills window: places, defaults, how many skills work in each core place."""
    rows = [row for row in library(state) if row.get("enabled") is not False]
    en = lang == "en"
    return {
        "places": [{"id": place, "title": PLACE_TITLES[place][1 if en else 0],
                    "count": sum(1 for row in rows if works_in(row, place))} for place in CORE_PLACES],
        "default_modules": list(DEFAULT_MODULES),
        "chat_only": {key: value[1 if en else 0] for key, value in CHAT_ONLY.items()},
        "code_twins": dict(CODE_TWINS),
        "synced_apps": ["MemuAI", "MemuFiles", "MemuNote", "MemuCode", "MemuCode CLI"],
        "not_synced": ["MemuAI Proxy"],
    }


__all__ = ["CORE_PLACES", "DEFAULT_MODULES", "CHAT_ONLY", "CODE_TWINS", "MARK", "SkillGate", "attach", "builtin_skills",
           "catalogue_lines", "default_on", "follow_up", "for_place", "instructions", "library", "module_place",
           "parse_request", "prompt_block", "summary", "valid_place", "works_in"]
