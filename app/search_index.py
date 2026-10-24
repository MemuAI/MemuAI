"""MemuAI global search — the local inverted index (Round 29, r29-search).

One in-memory index over everything MemuAI keeps: chats (Chat / Work / MemuCode,
Telegram chats included), Work projects and the files of saved projects (bounded),
memory facts, prompts, schedules, documentation, panels, modules and the Quick
Access history.  Modules add their own sources through :func:`register_provider`
(the Characters module, MemuNote, …).

Design
------
* A *provider* owns one source.  ``revision()`` is a cheap fingerprint (a file
  stat); only when it changes the provider lists its *units* — ``{unit_key:
  (signature, build)}`` — and only units whose signature changed are re-indexed
  (a chat that got one new message is re-tokenised alone, the other 199 are not).
* Postings are ``term -> {doc_id: weight}`` with small integer weights (the
  interpreter caches small ints, so a posting costs one dict slot).  Every token
  is stored twice: as itself and as ``~stem`` (a light RU/EN stemmer), so
  «чаты» finds «чат», «running» finds «run».
* Matching per query token: exact > stem > prefix (as-you-type) > fuzzy (one or
  two edits).  The query is also tried in the other keyboard layout
  («ghbdtn» → «привет») when that finds more.  ``ё`` is ``е``.
* AND is evaluated per *group* (a chat = its title + all its messages), so
  «python ошибка» finds a chat where the words are in different messages; the
  best messages of the group come back as sub-hits with highlighted snippets.
* Operators: ``"exact phrase"``, ``-word``, ``in:work`` / ``type:docs``,
  ``from:me`` / ``from:ai``, ``model:gpt``, ``folder:name``, ``project:name``,
  ``is:pinned``, ``after:2026-01-31``, ``before:…``, ``date:today|week|month|year``.

Nothing here touches the network; Work files are read only inside the saved
project root (links/junctions are not followed), with hard caps on the number of
files, bytes per file and total bytes per pass.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("memuai.search")

# ---------------------------------------------------------------------------
# Text: folding, tokens, stems, keyboard layout
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL_RE = re.compile(r"(?<=[a-zа-я])(?=[A-ZА-Я])")
_SPACE_RE = re.compile(r"\s+")

EN_RU = dict(zip("qwertyuiop[]asdfghjkl;'zxcvbnm,.`", "йцукенгшщзхъфывапролджэячсмитьбюё"))
EN_RU.update(dict(zip("QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>~", "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮЁ")))
RU_EN = {v: k for k, v in EN_RU.items()}


def fold_char(ch: str) -> str:
    low = ch.lower()
    if len(low) != 1:
        low = ch
    return "е" if low == "ё" else low


def fold(text: str) -> str:
    """Lower case, ``ё`` → ``е``; the length is preserved (snippet offsets stay valid)."""
    return "".join(fold_char(ch) for ch in str(text or ""))


def tokens(text: str) -> List[str]:
    value = str(text or "")
    low = value.lower()
    if len(low) != len(value):
        low = fold(value)
    return _TOKEN_RE.findall(low.replace("ё", "е"))


def swap_layout(text: str) -> str:
    """Text typed in the wrong keyboard layout, converted; ``""`` when not applicable."""
    value = str(text or "")
    letters = [ch for ch in value if ch.isalpha()]
    if not letters:
        return ""
    latin = sum(1 for ch in letters if "a" <= ch.lower() <= "z")
    cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    if latin and not cyr:
        return "".join(EN_RU.get(ch, ch) for ch in value)
    if cyr and not latin:
        return "".join(RU_EN.get(ch, ch) for ch in value)
    return ""


_RU_REFLEXIVE = ("ся", "сь")
_RU_ENDINGS = sorted({
    # adjectives / participles
    "ими", "ыми", "ого", "его", "ому", "ему", "ая", "яя", "ую", "юю", "ой", "ей", "ий", "ый", "ое", "ее",
    "ые", "ие", "ых", "их", "ым", "им", "ом", "ем", "ющий", "ющая", "ющее", "ющие", "вший", "вшая",
    "енный", "енная", "анный", "анная", "ость", "ости", "остью", "остей",
    # verbs
    "ешь", "ете", "ет", "ут", "ют", "ит", "ят", "ишь", "ите", "ила", "ило", "или", "ал", "ала", "ало", "али",
    "ял", "яла", "яло", "яли", "ел", "ела", "ело", "ели", "ать", "ять", "ить", "еть", "уть", "ыть",
    "ует", "уют", "ывать", "ивать", "овать", "ировать", "ируют", "ирует",
    # nouns
    "ами", "ями", "ах", "ях", "ам", "ям", "ов", "ев", "ию", "ия", "ие", "ий", "ья", "ье", "ью", "ей",
    "иями", "ием", "ией", "ение", "ения", "ений", "ениям", "ениями", "ании", "ание", "ания", "аний",
    "и", "ы", "а", "я", "о", "е", "у", "ю", "ь", "й",
}, key=len, reverse=True)
_EN_RULES = (("ational", "ate"), ("ization", "ize"), ("fulness", "ful"), ("ousness", "ous"),
             ("iveness", "ive"), ("ations", "ate"), ("ation", "ate"), ("ments", ""), ("ment", ""),
             ("ness", ""), ("ings", ""), ("ing", ""), ("edly", ""), ("ies", "y"), ("ied", "y"),
             ("sses", "ss"), ("ed", ""), ("es", ""), ("ly", ""), ("er", ""), ("s", ""))


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    """A deliberately light stemmer: good enough to join word forms, never clever."""
    w = word
    # Short nouns whose stem ends in «й» (чай, май, бой, рай, край): their forms leave a 2-letter stem that the
    # endings below refuse to cut, so «чаем», «чаю», «чая» never met «чай». Fold them back onto the nominative.
    if 3 <= len(w) <= 5 and w[:2] and w[1] in "аеиоуыэюя" and w[0] not in "аеиоуыэюяьъй" \
            and w[2:] in ("ем", "ём", "е", "ю", "я", "и", "ев", "ям", "ях", "ями", "ей"):
        return w[:2] + "й"
    if len(w) <= 3 or w.isdigit():
        return w
    if any("а" <= ch <= "я" for ch in w):
        for suffix in _RU_REFLEXIVE:
            if w.endswith(suffix) and len(w) - 2 >= 4:
                w = w[:-2]
                break
        for ending in _RU_ENDINGS:
            if w.endswith(ending) and len(w) - len(ending) >= 3:
                w = w[: -len(ending)]
                break
        if len(w) > 4 and w.endswith(("ь", "й")):
            w = w[:-1]
        return w
    if w.isascii():
        if w.endswith("ss"):
            return w
        for suffix, repl in _EN_RULES:
            if w.endswith(suffix) and len(w) - len(suffix) + len(repl) >= 3:
                w = w[: -len(suffix)] + repl
                break
        if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "lsz":   # running → runn → run
            w = w[:-1]
        if len(w) > 4 and w.endswith("e"):
            w = w[:-1]
        return w
    return w


def edit_distance_le(a: str, b: str, limit: int) -> bool:
    """``True`` when the Levenshtein distance (with transpositions) is ``<= limit`` (1 or 2)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return False
    if limit == 1:
        i = 0
        while i < la and i < lb and a[i] == b[i]:
            i += 1
        if la == lb:
            return a[i + 1:] == b[i + 1:] or (i + 1 < la and a[i] == b[i + 1] and a[i + 1] == b[i]
                                                and a[i + 2:] == b[i + 2:])
        return a[i + 1:] == b[i:] if la > lb else a[i:] == b[i + 1:]
    prev2: List[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        lo = limit + 1
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            lo = min(lo, v)
        if lo > limit:
            return False
        prev2, prev = prev, cur
    return prev[lb] <= limit


# ---------------------------------------------------------------------------
# Documents and providers
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Doc:
    """One searchable thing.  ``group`` joins documents into one result (a chat)."""
    type: str                      # chat | work | code | project | file | memory | prompt | schedule | doc | panel | module | quick | <custom>
    key: str                       # globally unique, stable id
    title: str = ""
    text: str = ""
    ts: float = 0.0                # milliseconds
    group: str = ""
    group_title: str = ""
    open: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    subtitle: str = ""
    title_weight: int = 4
    store_text: bool = True        # False: the text is re-read for snippets (Work files)
    loader: Optional[Callable[[], str]] = None


UnitMap = Dict[str, Tuple[Any, Callable[[], List[Doc]]]]


class Provider:
    """A source of documents.  Subclass or use :class:`FunctionProvider`."""
    name = "base"
    types: Tuple[str, ...] = ()
    min_interval = 0.0             # seconds between revision checks (0 = every time)
    desktop_only = False           # hidden from the Telegram Mini App

    def revision(self) -> Any:
        return None

    def units(self) -> UnitMap:
        return {}


class FunctionProvider(Provider):
    """A provider from two callables — the simplest way for a module to plug in::

        from app.search_index import FunctionProvider, Doc, register_provider
        register_provider(FunctionProvider(
            "characters", ("character",), revision=lambda: path.stat().st_mtime_ns,
            units=lambda: {c["id"]: (c["updated"], lambda c=c: [Doc("character", "character:" + c["id"],
                                                                     c["name"], c["description"],
                                                                     open={"action": "character", "id": c["id"]})])
                           for c in load_characters()}))
    """

    def __init__(self, name: str, types: Iterable[str], *, revision: Callable[[], Any],
                 units: Callable[[], UnitMap], min_interval: float = 0.0, desktop_only: bool = False,
                 labels: Optional[Dict[str, Tuple[str, str]]] = None) -> None:
        self.name = name
        self.types = tuple(types)
        self._revision = revision
        self._units = units
        self.min_interval = min_interval
        self.desktop_only = desktop_only
        self.labels = labels or {}

    def revision(self) -> Any:
        return self._revision()

    def units(self) -> UnitMap:
        return self._units()


# Labels of the known types: (ru, en).  Custom providers may add theirs.
TYPE_LABELS: Dict[str, Tuple[str, str]] = {
    "chat": ("Чаты", "Chats"), "work": ("Work", "Work"), "code": ("MemuCode", "MemuCode"),
    "project": ("Проекты", "Projects"), "file": ("Файлы", "Files"), "memory": ("Память", "Memory"),
    "prompt": ("Промпты", "Prompts"), "schedule": ("Планирование", "Schedules"),
    "doc": ("Документация", "Documentation"), "panel": ("Панели", "Panels"), "module": ("Модули", "Modules"),
    "quick": ("Быстрый доступ", "Quick Access"), "character": ("Персонажи", "Characters"),
}
TYPE_ORDER = ["chat", "work", "code", "character", "project", "file", "memory", "prompt", "schedule",
              "panel", "module", "quick", "doc"]
# «in:» aliases → (types, meta flags)
SCOPE_ALIASES: Dict[str, Tuple[Tuple[str, ...], Dict[str, Any]]] = {
    "chat": (("chat",), {}), "chats": (("chat",), {}), "чат": (("chat",), {}), "чаты": (("chat",), {}),
    "work": (("work", "project", "file"), {}), "ворк": (("work", "project", "file"), {}),
    "code": (("code",), {}), "memucode": (("code",), {}), "код": (("code",), {}),
    "telegram": (("chat", "work", "code"), {"telegram": True}), "tg": (("chat", "work", "code"), {"telegram": True}),
    "телеграм": (("chat", "work", "code"), {"telegram": True}),
    "files": (("file",), {}), "file": (("file",), {}), "файлы": (("file",), {}), "файл": (("file",), {}),
    "projects": (("project",), {}), "project": (("project",), {}), "проекты": (("project",), {}),
    "memory": (("memory",), {}), "память": (("memory",), {}),
    "prompts": (("prompt",), {}), "prompt": (("prompt",), {}), "промпты": (("prompt",), {}),
    "schedules": (("schedule",), {}), "schedule": (("schedule",), {}), "tasks": (("schedule",), {}),
    "задачи": (("schedule",), {}), "планирование": (("schedule",), {}),
    "docs": (("doc",), {}), "doc": (("doc",), {}), "help": (("doc",), {}), "документация": (("doc",), {}),
    "справка": (("doc",), {}),
    "panels": (("panel",), {}), "panel": (("panel",), {}), "панели": (("panel",), {}),
    "modules": (("module",), {}), "module": (("module",), {}), "модули": (("module",), {}),
    "quick": (("quick",), {}), "быстрый": (("quick",), {}),
    "characters": (("character",), {}), "character": (("character",), {}), "персонажи": (("character",), {}),
}


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

MAX_INDEXED_CHARS = 40_000        # per document
MAX_STEMS_PER_GROUP = 30_000
MAX_STORED_CHARS = 24_000
MAX_WEIGHT = 250                  # small ints are shared objects: a posting costs one dict slot


@dataclass(slots=True)
class Group:
    """What the postings point at: one result (a chat with its messages, a doc page with its sections…)."""
    key: str
    unit: Tuple[str, str]
    doc_ids: List[int]
    head: int                      # the document that represents the group (a chat's title row)
    length: int = 1


class SearchIndex:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.providers: Dict[str, Provider] = {}
        self.docs: Dict[int, Doc] = {}
        self.key_to_id: Dict[str, int] = {}
        self.groups: Dict[int, Group] = {}
        self.group_terms: Dict[int, Tuple[str, ...]] = {}
        self.postings: Dict[str, Dict[int, int]] = {}      # stem -> {group id: weight}
        self.vocab: Dict[str, str] = {}                     # surface word -> stem (append-only)
        self.unit_docs: Dict[Tuple[str, str], List[int]] = {}
        self.unit_groups: Dict[Tuple[str, str], List[int]] = {}
        self.unit_sig: Dict[Tuple[str, str], Any] = {}
        self.prov_rev: Dict[str, Any] = {}
        self.prov_checked: Dict[str, float] = {}
        self.prov_errors: Dict[str, str] = {}
        self.next_id = 1
        self._vocab_sorted: List[str] = []
        self._vocab_size = -1
        self._by_first: Dict[str, List[str]] = {}
        self.stats = {"refresh_ms": 0.0, "refreshes": 0, "last_refresh": 0.0}

    # ----------------------------------------------------------- providers
    def register(self, provider: Provider) -> None:
        with self.lock:
            old = self.providers.get(provider.name)
            if old is not None and old is not provider:
                self._drop_provider(provider.name)
            self.providers[provider.name] = provider
            for kind, label in getattr(provider, "labels", {}).items():
                TYPE_LABELS.setdefault(kind, tuple(label))
            for kind in provider.types:
                if kind not in TYPE_ORDER:
                    TYPE_ORDER.append(kind)

    def unregister(self, name: str) -> None:
        with self.lock:
            self.providers.pop(name, None)
            self._drop_provider(name)

    def _drop_provider(self, name: str) -> None:
        for unit in [u for u in self.unit_docs if u[0] == name]:
            self._remove_unit(unit)
        self.prov_rev.pop(name, None)
        self.prov_checked.pop(name, None)

    # ----------------------------------------------------------- mutation
    def _remove_unit(self, unit: Tuple[str, str]) -> None:
        for gid in self.unit_groups.pop(unit, []):
            self.groups.pop(gid, None)
            for term in self.group_terms.pop(gid, ()):
                row = self.postings.get(term)
                if row is not None:
                    row.pop(gid, None)
                    if not row:
                        del self.postings[term]
        for doc_id in self.unit_docs.pop(unit, []):
            doc = self.docs.pop(doc_id, None)
            if doc is not None and self.key_to_id.get(doc.key) == doc_id:
                self.key_to_id.pop(doc.key, None)
        self.unit_sig.pop(unit, None)

    def _add_unit(self, unit: Tuple[str, str], docs: List[Doc]) -> None:
        by_group: Dict[str, List[Doc]] = {}
        for doc in docs:
            by_group.setdefault(doc.group or doc.key, []).append(doc)
        doc_ids: List[int] = []
        gids: List[int] = []
        vocab = self.vocab
        for _gkey, members in by_group.items():
            gid = self.next_id
            self.next_id += 1
            weights: Counter = Counter()
            length = 0
            ids: List[int] = []
            head = 0
            for doc in members:
                old = self.key_to_id.get(doc.key)
                if old is not None and old in self.docs:
                    # the same key from another unit: the newer one wins
                    self.docs.pop(old, None)
                doc_id = self.next_id
                self.next_id += 1
                counts: Counter = Counter()
                if doc.title:
                    title_tokens = tokens(_CAMEL_RE.sub(" ", doc.title))
                    for tok in set(title_tokens):
                        counts[tok] += doc.title_weight
                    length += len(title_tokens)
                if doc.text:
                    body_tokens = tokens(doc.text[:MAX_INDEXED_CHARS])
                    counts.update(body_tokens)
                    length += len(body_tokens)
                for tok, count in counts.items():
                    if len(tok) > 48:
                        continue
                    st = vocab.get(tok)
                    if st is None:
                        st = vocab[tok] = stem(tok)
                    weights[st] += count
                if doc.store_text and len(doc.text) > MAX_STORED_CHARS:
                    doc.text = doc.text[:MAX_STORED_CHARS]
                elif not doc.store_text:
                    doc.text = ""
                doc.meta["_gid"] = gid
                self.docs[doc_id] = doc
                self.key_to_id[doc.key] = doc_id
                ids.append(doc_id)
                if doc.meta.get("is_title") and not (head and self.docs[head].meta.get("is_title")):
                    head = doc_id
                elif not head:
                    head = doc_id
            if len(weights) > MAX_STEMS_PER_GROUP:
                weights = Counter(dict(weights.most_common(MAX_STEMS_PER_GROUP)))
            for term, weight in weights.items():
                row = self.postings.get(term)
                if row is None:
                    row = self.postings[term] = {}
                row[gid] = weight if weight < MAX_WEIGHT else MAX_WEIGHT
            self.groups[gid] = Group(members[0].group or members[0].key, unit, ids, head, max(1, length))
            self.group_terms[gid] = tuple(weights)
            gids.append(gid)
            doc_ids.extend(ids)
        self.unit_docs[unit] = doc_ids
        self.unit_groups[unit] = gids

    def refresh(self, names: Optional[Iterable[str]] = None, *, force: bool = False) -> int:
        """Bring the providers up to date; returns the number of re-indexed units."""
        started = time.perf_counter()
        changed = 0
        with self.lock:
            providers = [self.providers[n] for n in (names or list(self.providers)) if n in self.providers]
        for provider in providers:
            now = time.monotonic()
            if not force and provider.min_interval and \
                    now - self.prov_checked.get(provider.name, -1e9) < provider.min_interval:
                continue
            self.prov_checked[provider.name] = now
            try:
                rev = provider.revision()
            except Exception as exc:  # a broken source must not break search
                self.prov_errors[provider.name] = str(exc)[:300]
                continue
            if not force and rev is not None and self.prov_rev.get(provider.name) == rev:
                continue
            try:
                units = provider.units()
            except Exception as exc:
                log.warning("search: provider %s failed: %s", provider.name, exc)
                self.prov_errors[provider.name] = str(exc)[:300]
                continue
            self.prov_errors.pop(provider.name, None)
            with self.lock:
                stale = [u for u in self.unit_docs if u[0] == provider.name and u[1] not in units]
                todo = [(key, sig, build) for key, (sig, build) in units.items()
                        if force or self.unit_sig.get((provider.name, key), _MISSING) != sig]
            # Build the documents outside the lock (reading files is the slow part) and swap them in
            # in small batches, so a query never waits for a whole rebuild.
            for start in range(0, max(1, len(todo)), 64):
                batch: List[Tuple[str, Any, List[Doc]]] = []
                for key, sig, build in todo[start:start + 64]:
                    try:
                        batch.append((key, sig, [d for d in (build() or []) if isinstance(d, Doc)]))
                    except Exception as exc:
                        log.debug("search: unit %s/%s failed: %s", provider.name, key, exc)
                with self.lock:
                    for key, sig, docs in batch:
                        unit = (provider.name, key)
                        self._remove_unit(unit)
                        self._add_unit(unit, docs)
                        self.unit_sig[unit] = sig
                        changed += 1
            with self.lock:
                for unit in stale:
                    self._remove_unit(unit)
                changed += len(stale)
                self.prov_rev[provider.name] = rev
        took = (time.perf_counter() - started) * 1000
        if changed:
            self.stats["refresh_ms"] = round(took, 1)
            self.stats["last_refresh"] = time.time()
        self.stats["refreshes"] += 1
        return changed

    # ----------------------------------------------------------- vocabulary
    def _vocabulary(self) -> List[str]:
        if self._vocab_size != len(self.vocab):
            vocab = sorted(self.vocab)
            by_first: Dict[str, List[str]] = {}
            for term in vocab:
                by_first.setdefault(term[0], []).append(term)
            self._vocab_sorted, self._by_first, self._vocab_size = vocab, by_first, len(self.vocab)
        return self._vocab_sorted

    def df(self, surface: str) -> int:
        return len(self.postings.get(self.vocab.get(surface) or stem(surface), ()))

    def prefix_terms(self, prefix: str, limit: int = 40) -> List[str]:
        vocab = self._vocabulary()
        start = bisect_left(vocab, prefix)
        out: List[str] = []
        for i in range(start, len(vocab)):
            term = vocab[i]
            if not term.startswith(prefix):
                break
            if term != prefix:
                out.append(term)
            if len(out) >= 3000:
                break
        out = [t for t in out if self.vocab.get(t) in self.postings]
        if len(out) > limit:
            out.sort(key=lambda t: -self.df(t))
            out = out[:limit]
        return out

    def fuzzy_terms(self, word: str, limit: int = 12) -> List[str]:
        if len(word) < 4:
            return []
        self._vocabulary()
        max_dist = 2 if len(word) >= 8 else 1
        out: List[Tuple[int, str]] = []
        candidates = list(self._by_first.get(word[0], ()))
        if len(word) >= 5:   # the first letter can be the typo too
            candidates += [t for f, rows in self._by_first.items() if f != word[0] for t in rows
                           if len(t) >= 5 and t[1:3] == word[1:3]]
        for term in candidates:
            if abs(len(term) - len(word)) > max_dist or term == word:
                continue
            if edit_distance_le(word, term, 1):
                out.append((1, term))
            elif max_dist == 2 and edit_distance_le(word, term, 2):
                out.append((2, term))
        out = [row for row in out if self.vocab.get(row[1]) in self.postings]
        out.sort(key=lambda row: (row[0], -self.df(row[1])))
        return [term for _dist, term in out[:limit]]

    # ----------------------------------------------------------- stats
    def summary(self) -> Dict[str, Any]:
        with self.lock:
            per_type = Counter(doc.type for doc in self.docs.values())
            return {"docs": len(self.docs), "groups": len(self.groups), "terms": len(self.postings),
                    "vocabulary": len(self.vocab), "units": len(self.unit_docs), "types": dict(per_type),
                    "providers": sorted(self.providers), "errors": dict(self.prov_errors), **self.stats}


_MISSING = object()
INDEX = SearchIndex()


def register_provider(provider: Provider) -> None:
    """Public hook for modules: add (or replace) a search source."""
    INDEX.register(provider)


def unregister_provider(name: str) -> None:
    INDEX.unregister(name)



# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------

_OP_RE = re.compile(r'(-?)(?:(\w+):("[^"]*"|\S+)|"([^"]*)"?|(\S+))', re.UNICODE)
DAY_MS = 86_400_000
CHAT_TYPES = ("chat", "work", "code")
# User content first: the shipped documentation is large and would otherwise crowd out a chat.
TYPE_WEIGHT = {"doc": 0.72, "file": 0.9, "quick": 0.9}


def _parse_date(value: str, end: bool = False) -> Optional[float]:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m", "%Y"):
        try:
            moment = time.strptime(value, fmt)
        except ValueError:
            continue
        stamp = time.mktime(moment) * 1000
        if end:
            # the last millisecond of that day / month / year (a fixed «+31 days» spilled 2026-06 into July 1st)
            year, month, day = moment.tm_year, moment.tm_mon, moment.tm_mday
            if fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                nxt = (year, month, day + 1)
            elif fmt == "%Y-%m":
                nxt = (year + month // 12, month % 12 + 1, 1)
            else:
                nxt = (year + 1, 1, 1)
            stamp = time.mktime((*nxt, 0, 0, 0, 0, 0, -1)) * 1000 - 1
        return stamp
    return None


def _relative_date(value: str, now_ms: float) -> Optional[Tuple[float, float]]:
    value = value.lower()
    local = time.localtime(now_ms / 1000)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000
    spans = {"today": (midnight, now_ms + 1), "сегодня": (midnight, now_ms + 1),
             "yesterday": (midnight - DAY_MS, midnight), "вчера": (midnight - DAY_MS, midnight),
             "week": (now_ms - 7 * DAY_MS, now_ms + 1), "неделя": (now_ms - 7 * DAY_MS, now_ms + 1),
             "month": (now_ms - 31 * DAY_MS, now_ms + 1), "месяц": (now_ms - 31 * DAY_MS, now_ms + 1),
             "year": (now_ms - 366 * DAY_MS, now_ms + 1), "год": (now_ms - 366 * DAY_MS, now_ms + 1)}
    return spans.get(value)


@dataclass
class Query:
    words: List[str] = field(default_factory=list)          # folded positive words
    phrases: List[str] = field(default_factory=list)        # folded exact phrases
    excluded: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    flags: Dict[str, Any] = field(default_factory=dict)     # telegram / pinned / role / model / folder / project
    date_from: Optional[float] = None
    date_to: Optional[float] = None
    operators: List[Dict[str, str]] = field(default_factory=list)
    raw: str = ""

    @property
    def empty(self) -> bool:
        return not self.words and not self.phrases


def parse_query(raw: str, *, now_ms: Optional[float] = None) -> Query:
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    q = Query(raw=str(raw or "")[:400])
    for neg, op, op_val, phrase, word in _OP_RE.findall(q.raw):
        if op:
            key = fold(op)
            value = op_val.strip('"')
            fv = fold(value).strip()
            handled = True
            if key in ("in", "type", "в", "тип"):
                scope = SCOPE_ALIASES.get(fv)
                if scope:
                    q.types.extend(t for t in scope[0] if t not in q.types)
                    q.flags.update(scope[1])
                elif fv:
                    q.types.append(fv)
            elif key in ("from", "от"):
                # «Вы»/«You» is the badge of the user's own messages in the results; an unknown author
                # (from:deepseek) means the answers of that model
                if fv in ("me", "я", "user", "мне", "меня", "вы", "you", "i", "мои", "мой", "mine", "my"):
                    q.flags["role"] = "user"
                else:
                    q.flags["role"] = "assistant"
                    if fv and fv not in ("ai", "ии", "assistant", "ассистент", "bot", "бот", "memuai", "model", "модель"):
                        q.flags["model"] = fv
            elif key in ("model", "модель"):
                q.flags["model"] = fv
            elif key in ("folder", "папка"):
                q.flags["folder"] = fv
            elif key in ("project", "проект"):
                q.flags["project"] = fv
            elif key in ("is", "это") and fv in ("pinned", "закреплён", "закреплен", "pin"):
                q.flags["pinned"] = True
            elif key in ("is", "это") and fv in ("telegram", "tg"):
                q.flags["telegram"] = True
            elif key in ("after", "since", "после", "с"):
                q.date_from = _parse_date(value) or q.date_from
            elif key in ("before", "until", "до"):
                q.date_to = _parse_date(value, end=True) or q.date_to
            elif key in ("date", "дата", "when", "когда"):
                span = _relative_date(fv, now_ms)
                if span:
                    q.date_from, q.date_to = span
                elif ".." in value:      # date:2026-05..2026-06 — from the first day of A to the last day of B
                    first, _, last = value.partition("..")
                    q.date_from = _parse_date(first) if first.strip() else q.date_from
                    q.date_to = _parse_date(last, end=True) if last.strip() else q.date_to
                else:
                    start = _parse_date(value)
                    if start is not None:
                        q.date_from, q.date_to = start, _parse_date(value, end=True)
            else:
                handled = False
            if handled:
                q.operators.append({"key": key, "value": value, "neg": "1" if neg else ""})
                continue
            word = f"{op}:{op_val}"
        if phrase:
            folded = _SPACE_RE.sub(" ", fold(phrase)).strip()
            if folded:
                (q.excluded if neg else q.phrases).append(folded)
            continue
        for tok in tokens(word):
            if neg:
                q.excluded.append(tok)
            else:
                q.words.append(tok)
    # a phrase also contributes its words to the matching
    return q


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_LOG = [0.0] + [1.0 + math.log(w) for w in range(1, MAX_WEIGHT + 1)]
GROUP_FILTERS = ("telegram", "pinned", "folder", "project")
DOC_FILTERS = ("role", "model", "date_from", "date_to")
VERIFY_ALL_LIMIT = 1500


@dataclass
class WordMatch:
    word: str
    found: Dict[int, float]        # group id -> weight
    surfaces: set                  # matched vocabulary words (exact / prefix / fuzzy)
    stems: set
    quality: int                   # 3 exact or stem, 2 prefix, 1 fuzzy, 0 nothing
    idf: float = 0.0
    regex: Any = None


def _alternation(surfaces: Iterable[str], stems: Iterable[str]) -> str:
    # The pattern matches at a word start, so an alternative that starts with another one adds nothing
    # (every «ошибк…» is already covered by «ошиб»): keep only the shortest prefixes.
    alts: List[str] = []
    for alt in sorted({a for a in list(surfaces) + list(stems) if a}):
        if alts and alt.startswith(alts[-1]):
            continue
        alts.append(alt)
    alts.sort(key=len, reverse=True)
    return "|".join(re.escape(a) for a in alts[:160])


_J_FORMS = "(?:й|ем|ём|е|ю|я|и|ев|ям|ях|ями|ей)(?![^\\W_])"


def _word_regex(surfaces: set, stems: set):
    alt = _alternation(surfaces, stems)
    if not alt:
        return None
    # «чай» stands for «чаем/чаю/чае…» (see stem()): highlight those forms too, but not «час», «чаша».
    j_forms = [re.escape(st[:2]) + _J_FORMS for st in stems if len(st) == 3 and st.endswith("й")]
    if j_forms:
        alt = "|".join(j_forms) + "|" + alt
    return re.compile(r"(?<![^\W_])(?:%s)[^\W_]*" % alt, re.UNICODE)


def _match_word(index: SearchIndex, word: str, last: bool) -> WordMatch:
    found: Dict[int, float] = {}
    stems: Dict[str, float] = {}
    surfaces = {word}

    def add_stem(st: str, factor: float) -> None:
        if factor > stems.get(st, 0.0):
            stems[st] = factor

    add_stem(index.vocab.get(word) or stem(word), 1.0)
    if word in index.vocab:
        add_stem(index.vocab[word], 1.0)
    quality = 3 if any(st in index.postings for st in stems) else 0
    if last or len(word) >= 3 or not quality:
        for term in index.prefix_terms(word, 40 if last else 16):
            surfaces.add(term)
            add_stem(index.vocab[term], 0.66 if last else 0.5)
        if not quality and len(stems) > 1:
            quality = 2
    hits = sum(len(index.postings.get(st, ())) for st in stems)
    # Typos only when the word itself (or its forms) found nothing: a valid stem hit must not drag in
    # look-alike words — «чаем» found «чай» and also every «чем».
    if hits < 3 and len(word) >= 4 and quality < 3:
        for term in index.fuzzy_terms(word):
            surfaces.add(term)
            add_stem(index.vocab[term], 0.42)
        if not quality and len(stems) > 1:
            quality = 1
    for st, factor in stems.items():
        row = index.postings.get(st)
        if not row:
            continue
        for gid, weight in row.items():
            value = _LOG[weight] * factor
            if value > found.get(gid, 0.0):
                found[gid] = value
    if not found:
        quality = 0
    return WordMatch(word, found, surfaces, set(stems), quality, regex=_word_regex(surfaces, set(stems)))


def fast_fold(text: str) -> str:
    low = text.lower()
    if len(low) != len(text):
        return fold(text)
    return low.replace("ё", "е")


def _group_passes(index: SearchIndex, group: Group, q: Query, types: Optional[set], filters: Dict[str, Any]) -> bool:
    head = index.docs.get(group.head)
    if head is None:
        return False
    if types and head.type not in types:
        return False
    meta = head.meta
    lang = filters.get("lang")
    if lang and meta.get("lang") and meta.get("lang") != lang:
        return False
    if filters.get("remote") and meta.get("desktop_only"):
        return False
    flags = _flags(q, filters)
    if (flags.get("folder") or flags.get("pinned") or flags.get("telegram") or flags.get("role")) \
            and head.type not in CHAT_TYPES:
        return False
    if flags.get("telegram") and not meta.get("telegram"):
        return False
    if flags.get("pinned") and not meta.get("pinned"):
        return False
    folder = fold(str(flags.get("folder") or ""))
    if folder and not (fold(str(meta.get("folder_id") or "")) == folder or folder in fold(str(meta.get("folder") or ""))):
        return False
    project = fold(str(flags.get("project") or ""))
    if project and not (fold(str(meta.get("project_id") or "")) == project or project in fold(str(meta.get("project") or ""))):
        return False
    model = fold(str(flags.get("model") or ""))
    if model:
        models = [m for d in group.doc_ids for m in (index.docs[d].meta.get("models") or [index.docs[d].meta.get("model") or ""])] \
            if len(group.doc_ids) < 4 else (meta.get("models") or [meta.get("model") or ""])
        if not any(model in fold(str(m)) for m in models if m):
            return False
    return True


def _flags(q: Query, filters: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(q.flags)
    for key in ("telegram", "pinned", "role", "model", "folder", "project"):
        if filters.get(key) not in (None, "", False):
            out[key] = filters[key]
    return out


def _doc_filters(q: Query, filters: Dict[str, Any]) -> Dict[str, Any]:
    flags = _flags(q, filters)
    out: Dict[str, Any] = {}
    if flags.get("role"):
        out["role"] = flags["role"]
    if flags.get("model"):
        out["model"] = fold(str(flags["model"]))
    date_from = filters.get("date_from") if filters.get("date_from") is not None else q.date_from
    date_to = filters.get("date_to") if filters.get("date_to") is not None else q.date_to
    if date_from is not None:
        out["date_from"] = float(date_from)
    if date_to is not None:
        out["date_to"] = float(date_to)
    return out


def _doc_ok(doc: Doc, dflt: Dict[str, Any]) -> bool:
    if not dflt:
        return True
    meta = doc.meta
    if dflt.get("role") and meta.get("role") != dflt["role"]:
        return False
    if dflt.get("model"):
        models = meta.get("models") or ([meta["model"]] if meta.get("model") else [])
        if not any(dflt["model"] in fold(str(m)) for m in models):
            return False
    if "date_from" in dflt or "date_to" in dflt:
        ts = doc.ts or 0
        if not ts or ("date_from" in dflt and ts < dflt["date_from"]) or ("date_to" in dflt and ts > dflt["date_to"]):
            return False
    return True


def _phrase_regex(phrase: str):
    return re.compile(r"\s+".join(re.escape(part) for part in phrase.split(" ") if part), re.UNICODE)


def _doc_folded(doc: Doc, spaces: bool = False) -> str:
    text = doc.text if doc.store_text else _loaded_text(doc)
    value = fast_fold((doc.title + "\n" + text) if doc.title else text)
    return _SPACE_RE.sub(" ", value) if spaces else value


def _loaded_text(doc: Doc) -> str:
    if doc.loader is None:
        return ""
    try:
        return str(doc.loader() or "")
    except Exception:
        return ""


def snippet(text: str, regex: Any, phrases: List[str], *, width: int = 190) -> Tuple[str, List[List[int]]]:
    """A window of ``text`` around the densest matches and the highlight ranges ``[start, length]``."""
    display = _SPACE_RE.sub(" ", str(text or "")).strip()
    if not display:
        return "", []
    folded = fast_fold(display)
    hits: List[Tuple[int, int, str]] = []
    if regex is not None:
        for m in regex.finditer(folded):
            hits.append((m.start(), m.end() - m.start(), m.group(0)[:6]))
            if len(hits) > 400:
                break
    for phrase in phrases:
        start = folded.find(phrase)
        while start >= 0 and len(hits) < 500:
            hits.append((start, len(phrase), "\x00" + phrase))
            start = folded.find(phrase, start + len(phrase))
    hits.sort()
    if len(display) <= width:
        lo, hi = 0, len(display)
    elif not hits:
        lo, hi = 0, width
    else:
        best, best_score = hits[0][0], -1.0
        for i, (pos, _ln, _key) in enumerate(hits):
            keys = set()
            for p, _l, k in hits[i:]:
                if p >= pos + width - 40:
                    break
                keys.add(k)
            score = len(keys) + sum(2 for k in keys if k.startswith("\x00")) - pos / 1e6
            if score > best_score:
                best, best_score = pos, score
            if i > 120:
                break
        lo = max(0, best - width // 4)
        if lo:
            space = display.rfind(" ", max(0, lo - 18), lo)
            lo = space + 1 if space >= 0 else lo
        hi = min(len(display), lo + width)
        if hi < len(display):
            space = display.rfind(" ", lo + width - 24, hi)
            hi = space if space > lo else hi
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(display) else ""
    out = prefix + display[lo:hi] + suffix
    ranges: List[List[int]] = []
    for pos, ln, _key in hits:
        if pos >= lo and pos + ln <= hi:
            ranges.append([pos - lo + len(prefix), ln])
    ranges.sort()
    merged: List[List[int]] = []
    for start, ln in ranges:
        if merged and start <= merged[-1][0] + merged[-1][1]:
            end = max(merged[-1][0] + merged[-1][1], start + ln)
            merged[-1][1] = end - merged[-1][0]
        else:
            merged.append([start, ln])
    return out, merged


def _ranges(text: str, regex: Any) -> List[List[int]]:
    if regex is None or not text:
        return []
    return [[m.start(), m.end() - m.start()] for m in regex.finditer(fast_fold(text))][:40]


_PUBLIC_META = ("role", "model", "models", "mode", "telegram", "pinned", "folder", "folder_id", "project",
                "project_id", "path", "line", "lang", "kind", "enabled", "messages", "unread", "set", "icon",
                "heading", "title_en", "name_ru", "branch", "versions")


def _public_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {k: meta[k] for k in _PUBLIC_META if k in meta and meta[k] not in (None, "", [], False)}


def _item(index: SearchIndex, group: Group, score: float) -> Dict[str, Any]:
    head = index.docs[group.head]
    return {"id": head.key, "type": head.type, "title": head.group_title or head.title, "subtitle": head.subtitle,
            "ts": max((index.docs[d].ts for d in group.doc_ids), default=0), "score": round(score, 4),
            "group": group.key, "meta": _public_meta(head.meta), "open": dict(head.open),
            "title_highlights": [], "snippet": "", "highlights": [], "hits": []}


def _hit(doc: Doc, snip: str, ranges: List[List[int]]) -> Dict[str, Any]:
    return {"id": doc.key, "snippet": snip, "highlights": ranges, "ts": doc.ts, "meta": _public_meta(doc.meta),
            "open": dict(doc.open), "title": doc.title if doc.title != doc.group_title else ""}


def _browse(index: SearchIndex, q: Query, types: Optional[set], filters: Dict[str, Any],
            limit: int) -> Dict[str, Any]:
    """Only operators / chips («model:claude», «is:pinned», «date:today»): the newest matching things."""
    dflt = _doc_filters(q, filters)
    has_filter = bool(types or q.flags or dflt or q.excluded or
                      any(filters.get(k) not in (None, "", False) for k in GROUP_FILTERS))
    if not has_filter:
        return {"items": [], "total": 0, "partial": False, "quality": 0}
    excluded = {gid for ex in q.excluded for gid in index.postings.get(index.vocab.get(ex) or stem(ex), {})}
    rows: List[Tuple[float, Group, List[Doc]]] = []
    for gid, group in index.groups.items():
        if gid in excluded or not _group_passes(index, group, q, types, filters):
            continue
        docs = [index.docs[d] for d in group.doc_ids]
        if dflt:
            docs = [d for d in docs if not d.meta.get("is_title") and _doc_ok(d, dflt)]
            if not docs:
                continue
        rows.append((max((d.ts for d in docs), default=0), group, docs))
    rows.sort(key=lambda r: -r[0])
    items: List[Dict[str, Any]] = []
    for ts, group, docs in rows[:limit]:
        item = _item(index, group, 0.0)
        item["ts"] = ts
        body = sorted((d for d in docs if not d.meta.get("is_title")), key=lambda d: -d.ts)
        lead = body[0] if body else index.docs[group.head]
        text = lead.text if lead.store_text else ""
        item["snippet"] = snippet(text or lead.subtitle or lead.title, None, [])[0]
        if body and index.docs[group.head].meta.get("is_title"):
            item["hit_count"] = len(body)
            item["hits"] = [_hit(d, snippet(d.text, None, [])[0], []) for d in body[:2]]
        elif body:
            item["open"] = dict(lead.open)
        items.append(item)
    return {"items": items, "total": len(rows), "partial": False, "quality": 3}


def _run(index: SearchIndex, q: Query, types: Optional[set], filters: Dict[str, Any],
         limit: int, per_group: int) -> Dict[str, Any]:
    words = list(dict.fromkeys(q.words + [w for p in q.phrases for w in tokens(p)]))[:12]
    if not words:
        return _browse(index, q, types, filters, limit)
    total_groups = max(1, len(index.groups))
    ends_with_space = q.raw.endswith(" ")
    matches = [_match_word(index, w, last=(i == len(words) - 1 and not ends_with_space)) for i, w in enumerate(words)]
    for m in matches:
        m.idf = math.log(1.0 + total_groups / (1.0 + len(m.found))) if m.found else 0.0
    quality = min(m.quality for m in matches)
    excluded = {gid for ex in q.excluded for gid in index.postings.get(index.vocab.get(ex) or stem(ex), {})}
    # candidate groups: every word (AND), else the best partial matches
    order = sorted(range(len(matches)), key=lambda i: len(matches[i].found))
    candidates = set(matches[order[0]].found)
    for i in order[1:]:
        candidates &= matches[i].found.keys()
        if not candidates:
            break
    partial = False
    need = set(range(len(words)))
    if not candidates and len(words) > 1:
        partial = True
        counts: Counter = Counter()
        for m in matches:
            counts.update(m.found.keys())
        threshold = len(words) - 1 if len(words) > 2 else 1
        candidates = {gid for gid, n in counts.items() if n >= threshold}
    candidates -= excluded
    passing: List[Tuple[float, Group]] = []
    now = time.time() * 1000
    folded_query = " ".join(words)
    for gid in candidates:
        group = index.groups.get(gid)
        if group is None or not _group_passes(index, group, q, types, filters):
            continue
        score = 0.0
        for m in matches:
            score += m.found.get(gid, 0.0) * m.idf
        score /= 1.0 + 0.12 * math.log1p(group.length / 60.0)
        head = index.docs[group.head]
        title_folded = fast_fold(head.group_title or head.title)
        if folded_query and folded_query in title_folded:
            score *= 1.7 if title_folded.startswith(folded_query) else 1.35
        if head.meta.get("pinned"):
            score *= 1.12
        ts = max((index.docs[d].ts for d in group.doc_ids[-3:]), default=0) if group.doc_ids else 0
        if ts:
            score *= 1.0 + 0.3 / (1.0 + max(0.0, (now - ts) / DAY_MS) / 30.0)
        score *= TYPE_WEIGHT.get(head.type, 1.0)
        passing.append((score, group))
    passing.sort(key=lambda r: -r[0])
    dflt = _doc_filters(q, filters)
    must_verify = bool(dflt or q.phrases)
    combined = _word_regex(set().union(*(m.surfaces for m in matches)), set().union(*(m.stems for m in matches)))
    items: List[Dict[str, Any]] = []
    total = 0
    capped = False
    budget_end = time.perf_counter() + 0.25
    for score, group in passing:
        if len(items) >= limit and (not must_verify or total >= VERIFY_ALL_LIMIT
                                    or time.perf_counter() > budget_end):
            if must_verify:
                capped = True
            break
        detail = _group_detail(index, group, matches, q, dflt, partial, need, per_group,
                               verify=must_verify, build=len(items) < limit, combined=combined)
        if detail is None:
            continue
        total += 1
        if len(items) < limit:
            item = _item(index, group, score)
            item.update(detail)
            items.append(item)
    if not must_verify:
        total = len(passing)
    return {"items": items, "total": total, "partial": partial, "quality": quality, "capped": capped}


def _group_detail(index: SearchIndex, group: Group, matches: List[WordMatch], q: Query, dflt: Dict[str, Any],
                  partial: bool, need: set, per_group: int, *, verify: bool, build: bool,
                  combined: Any) -> Optional[Dict[str, Any]]:
    """Which documents of the group match (and pass the per-message filters); ``None`` drops the group."""
    head = index.docs[group.head]
    if not verify and not build:
        return {}
    single = len(group.doc_ids) == 1
    scored: List[Tuple[int, float, Doc, str]] = []
    covered: set = set()
    phrase_ok = {p: False for p in q.phrases}
    phrase_res = {p: _phrase_regex(p) for p in q.phrases}
    for doc_id in group.doc_ids:
        doc = index.docs[doc_id]
        if dflt and not doc.meta.get("is_title") and not _doc_ok(doc, dflt):
            continue
        if dflt and doc.meta.get("is_title") and not single:
            continue
        if single and not doc.store_text and not verify:
            # a Work file: its text is read only for the snippet
            scored.append((len(matches), 0.0, doc, ""))
            covered = set(need)
            break
        folded = _doc_folded(doc)
        hit = {i for i, m in enumerate(matches) if m.regex is not None and m.regex.search(folded)}
        for p, rx in phrase_res.items():
            if rx.search(folded):
                phrase_ok[p] = True
                hit.add(-1)
        if hit:
            covered |= {i for i in hit if i >= 0}
            scored.append((len(hit), doc.ts, doc, folded))
            if not build and covered == need and all(phrase_ok.values()):
                return {}
    if verify:
        if not all(phrase_ok.values()):
            return None
        if not partial and covered != need:
            return None
        if not scored:
            return None
    if not build:
        return {}
    detail: Dict[str, Any] = {}
    title = head.group_title or head.title
    detail["title_highlights"] = _ranges(title, combined)
    body = [row for row in scored if not row[2].meta.get("is_title")]
    body.sort(key=lambda r: (-r[0], -r[1]))
    if len(group.doc_ids) > 1 and head.meta.get("is_title"):
        detail["hit_count"] = len(body)
        detail["hits"] = []
        for _n, _ts, doc, _f in body[:per_group]:
            text = doc.text if doc.store_text else _loaded_text(doc)
            snip, ranges = snippet(text.strip() or doc.subtitle or doc.title, combined, q.phrases)
            detail["hits"].append(_hit(doc, snip, ranges))
        if detail.get("hits"):
            detail["snippet"] = detail["hits"][0]["snippet"]
            detail["highlights"] = detail["hits"][0]["highlights"]
        else:
            first = next((index.docs[d] for d in group.doc_ids if not index.docs[d].meta.get("is_title")), None)
            detail["snippet"] = snippet(first.text, combined, [])[0] if first else ""
            detail["highlights"] = []
    else:
        lead = body[0][2] if body else (scored[0][2] if scored else head)
        text = lead.text if lead.store_text else _loaded_text(lead)
        detail["snippet"], detail["highlights"] = snippet(text.strip() or lead.subtitle or lead.title, combined, q.phrases)
        detail["open"] = dict(lead.open)
        if lead is not head and lead.title and lead.title != title:
            detail["subtitle"] = lead.subtitle or lead.title
        if len(group.doc_ids) > 1:
            detail["hit_count"] = len(body)
            detail["hits"] = []
            for _n, _ts, doc, _f in body[1:per_group]:
                snip, ranges = snippet(doc.text.strip() or doc.title, combined, q.phrases)
                detail["hits"].append(_hit(doc, snip, ranges))
    return detail


def search(raw: str, *, types: Optional[Iterable[str]] = None, filters: Optional[Dict[str, Any]] = None,
           limit: int = 60, per_group: int = 3, lang: str = "", refresh: bool = True,
           remote: bool = False) -> Dict[str, Any]:
    """Run a query.  ``types``/``filters`` come from the chips; operators in ``raw`` add to them."""
    started = time.perf_counter()
    if refresh:
        INDEX.refresh()
    q = parse_query(raw)
    flt = dict(filters or {})
    if lang:
        flt["lang"] = lang
    if remote:
        flt["remote"] = True
    wanted = set(types or []) | set(q.types)
    if q.types and types:
        wanted = set(types) & set(q.types) or set(q.types)
    limit = max(1, min(200, int(limit)))
    per_group = max(1, min(8, int(per_group)))
    with INDEX.lock:
        result = _run(INDEX, q, wanted or None, flt, limit, per_group)
        switched = ""
        alt = swap_layout(raw) if q.words else ""
        if alt and (result["total"] < 3 or result["quality"] <= 1):
            q2 = parse_query(alt)
            if q2.words:
                other = _run(INDEX, q2, wanted or None, flt, limit, per_group)
                if other["total"] > result["total"] and other["quality"] >= min(2, result["quality"] + 1):
                    result, switched, q = other, alt, q2
        suggestion = ""
        if not result["total"] and q.words:
            fixed = []
            for word in q.words:
                if INDEX.vocab.get(word) in INDEX.postings:
                    fixed.append(word)
                    continue
                near = INDEX.fuzzy_terms(word, 1)
                fixed.append(near[0] if near else word)
            if fixed != q.words:
                suggestion = " ".join(fixed)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in result["items"]:
        groups.setdefault(item["type"], []).append(item)
    order = {t: i for i, t in enumerate(TYPE_ORDER)}
    best_group = sorted(groups, key=lambda t: (-max(i["score"] for i in groups[t]), order.get(t, 99)))
    if not q.words:
        best_group = sorted(groups, key=lambda t: order.get(t, 99))
    payload_groups = [{"type": t, "label": list(TYPE_LABELS.get(t, (t, t))), "count": len(groups[t]),
                       "items": groups[t]} for t in best_group]
    return {
        "query": raw, "took_ms": round((time.perf_counter() - started) * 1000, 2),
        "total": result["total"], "capped": bool(result.get("capped")), "partial": result["partial"],
        "layout_switched": switched, "suggestion": suggestion,
        "parsed": {"words": q.words, "phrases": q.phrases, "excluded": q.excluded, "types": q.types,
                   "flags": q.flags, "date_from": q.date_from, "date_to": q.date_to, "operators": q.operators},
        "items": result["items"], "groups": payload_groups,
    }



# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------

def _stat_rev(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_mtime_ns:x}-{st.st_size:x}"
    except OSError:
        return "0"


def _num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


_MD_HIDDEN_BLOCK_RE = re.compile(r"```[ \t]*(memuai-(?:memory|chart|visual|products?)|vega(?:-lite)?|json-chart)\b.*?(?:```|\Z)",
                                 re.S | re.I)
_MD_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*$", re.M)
_MD_MARKER_RE = re.compile(r"\[(?:MEM|TITLE):[^\]]*\]")
_MD_RULE_RE = re.compile(r"^[ \t]*(?:\|?[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$|^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_MD_LINE_PREFIX_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|(?:[-*+]|\d{1,3}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)+", re.M)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPH_RE = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1|(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_MD_CODE_RE = re.compile(r"`+([^`\n]+)`+")
_MD_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<![\w$])\$(?=\S)([^$\n]+?)(?<=\S)\$(?![\w$])", re.S)


def plain_markdown(text: str) -> str:
    """What a reader sees of a Markdown answer, for snippets and the preview: no ``##``/``**``/``$``/table rules,
    no hidden chart/memory JSON blocks or [TITLE:]/[MEM:] markers. Code and formula text itself stays searchable."""
    value = str(text or "")
    if not any(ch in value for ch in "#*_`$[|>-~\\") and not re.search(r"^\s*\d+[.)]\s", value, re.M):
        return value
    value = _MD_HIDDEN_BLOCK_RE.sub(" ", value)
    value = _MD_FENCE_RE.sub("", value)
    value = _MD_MARKER_RE.sub("", value)
    value = _MD_RULE_RE.sub("", value)
    value = _MD_LINE_PREFIX_RE.sub("", value)
    value = _MD_IMAGE_RE.sub(r"\1", value)
    value = _MD_LINK_RE.sub(r"\1", value)
    value = _MD_MATH_RE.sub(lambda m: next(g for g in m.groups() if g is not None), value)
    value = _MD_CODE_RE.sub(r"\1", value)
    for _ in range(2):   # ***bold italic***
        value = _MD_EMPH_RE.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(3), value)
    # table rows only («| a | b |» → «a · b»): a pipe inside code (mermaid «-->|yes|», shell) stays
    value = re.sub(r"^[ \t]*\|(.*)\|[ \t]*$", lambda m: " · ".join(c.strip() for c in m.group(1).split("|")), value, flags=re.M)
    return value


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):   # OpenAI-style parts
        content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    text = plain_markdown(str(content or ""))
    names = [str(a.get("name") or "") for a in (message.get("attachments") or []) if isinstance(a, dict)]
    if names:
        text += "\n" + " ".join(n for n in names if n)
    return text


class ChatsProvider(Provider):
    """Chats of the active profile: Chat, Work and MemuCode, Telegram chats flagged."""
    name = "chats"
    types = ("chat", "work", "code")

    def _file(self) -> Path:
        from .pgdata import MEMU_DIR, _profile_data_name
        return Path(MEMU_DIR) / _profile_data_name("chats.json")

    def _prefs_rev(self) -> str:
        from .pgdata import MEMU_DIR
        return _stat_rev(Path(MEMU_DIR) / "preferences.json")

    def revision(self) -> Any:
        return (str(self._file()), _stat_rev(self._file()), self._prefs_rev())

    def units(self) -> UnitMap:
        from .pgdata import load_chats, load_preferences, load_workspaces
        chats = load_chats()
        try:
            prefs = load_preferences()
        except Exception:
            prefs = {}
        folders: Dict[str, str] = {}
        for key in ("chat_folders", "work_folders", "code_folders"):
            for row in prefs.get(key) or []:
                if isinstance(row, dict) and row.get("id"):
                    folders[str(row["id"])] = str(row.get("name") or "")
        try:
            projects = {p["id"]: p["name"] for p in load_workspaces().get("items", [])}
        except Exception:
            projects = {}
        memes = {str(m.get("id")): str(m.get("name") or "") for m in (prefs.get("memes") or [])
                 if isinstance(m, dict) and m.get("id")}
        out: UnitMap = {}
        for chat in chats:
            chat_id = str(chat.get("id") or "")
            if not chat_id or chat.get("temp"):
                continue
            messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
            folder_name = folders.get(str(chat.get("folder_id") or ""), "")
            project_name = projects.get(str(chat.get("project_id") or ""), "")
            sig = (chat.get("updated"), len(messages), chat.get("title"), bool(chat.get("pinned")),
                   chat.get("folder_id"), folder_name, project_name, _branch_sig(chat))
            out[chat_id] = (sig, (lambda c=chat, f=folder_name, p=project_name: self._docs(c, f, p, memes)))
        return out

    @staticmethod
    def _docs(chat: Dict[str, Any], folder: str, project: str, memes: Dict[str, str]) -> List[Doc]:
        chat_id = str(chat["id"])
        mode = str(chat.get("mode") or "chat")
        kind = mode if mode in ("work", "code") else "chat"
        title = str(chat.get("title") or "").strip()
        messages = [m for m in (chat.get("messages") or []) if isinstance(m, dict)]
        models = sorted({str(m.get("model")) for m in messages if m.get("role") == "assistant" and m.get("model")})
        updated = _num(chat.get("updated")) or max((_num(m.get("createdAt")) for m in messages), default=0.0)
        base_meta = {"mode": mode, "telegram": bool(chat.get("telegram")) or any(m.get("via") == "telegram" for m in messages),
                     "pinned": bool(chat.get("pinned")), "folder_id": str(chat.get("folder_id") or ""),
                     "folder": folder, "project_id": str(chat.get("project_id") or ""), "project": project,
                     "models": models, "messages": len(messages), "unread": bool(chat.get("unread"))}
        visible = [(i, m) for i, m in enumerate(messages) if not m.get("internal") and m.get("role") in ("user", "assistant")]
        subtitle_bits = [project] if project else []
        if folder:
            subtitle_bits.append(folder)
        subtitle = " · ".join(subtitle_bits)
        docs = [Doc(kind, f"chat:{chat_id}", title or "…", "", updated, group=f"chat:{chat_id}", group_title=title,
                    open={"action": "chat", "chat_id": chat_id, "mode": mode},
                    meta={**base_meta, "is_title": True}, subtitle=subtitle, title_weight=6)]
        for index, message in visible:
            text = _message_text(message)
            if not text.strip():
                continue
            role = str(message.get("role"))
            created = _num(message.get("createdAt")) or updated
            docs.append(Doc(kind, f"msg:{chat_id}:{index}", "", text, created, group=f"chat:{chat_id}",
                            group_title=title,
                            open={"action": "chat", "chat_id": chat_id, "mode": mode, "index": index,
                                  "created_at": created, "role": role},
                            meta={"role": role, "model": str(message.get("model") or "")},
                            subtitle=subtitle))
        # Round 35 (branches): messages of the OTHER versions are searchable too, labelled «другая версия»;
        # opening one makes its branch active (search.js → MemuBranches.revealMessage(message_id)).
        try:
            from . import chat_tree
            hidden = list(chat_tree.inactive_messages(chat))[:4000]
        except Exception:
            hidden = []
        for row in hidden:
            message = row["m"]
            if message.get("internal") or message.get("role") not in ("user", "assistant") or not message.get("id"):
                continue
            text = _message_text(message)
            if not text.strip():
                continue
            role = str(message.get("role"))
            created = _num(message.get("createdAt")) or updated
            docs.append(Doc(kind, f"msgv:{chat_id}:{message['id']}", "", text, created, group=f"chat:{chat_id}",
                            group_title=title,
                            open={"action": "chat", "chat_id": chat_id, "mode": mode, "message_id": str(message["id"]),
                                  "branch": "other", "created_at": created, "role": role},
                            meta={"role": role, "model": str(message.get("model") or ""), "branch": "other"},
                            subtitle=subtitle))
        return docs


def _branch_sig(chat: Dict[str, Any]) -> str:
    """Changes whenever the set of chat versions does (search re-indexes the chat then)."""
    branches = chat.get("branches")
    forks = branches.get("forks") if isinstance(branches, dict) else None
    if not isinstance(forks, dict) or not forks:
        return ""
    return ";".join(f"{k}:{(f or {}).get('sel')}/{len((f or {}).get('alts') or [])}" for k, f in sorted(forks.items())
                    if isinstance(f, dict))


class MemoryProvider(Provider):
    name = "memory"
    types = ("memory",)

    # pgdata.load_memory() reads memuai/memory.json (not profile-scoped); the raw file is read here so the
    # richer rows of a newer memory format (dicts with text/category/time) keep their fields.
    def revision(self) -> Any:
        from .pgdata import MEMU_DIR
        return _stat_rev(Path(MEMU_DIR) / "memory.json")

    def units(self) -> UnitMap:
        from .pgdata import MEMU_DIR
        try:
            raw = json.loads((Path(MEMU_DIR) / "memory.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        items = raw.get("items") if isinstance(raw.get("items"), list) else None
        docs: List[Doc] = []
        if items is not None:
            # memory v2 (app/memory_v2.py): structured items; deleted ones (the trash) are not searched
            for item in items[:3000]:
                if not isinstance(item, dict) or item.get("status") == "deleted":
                    continue
                text = str(item.get("text") or "")
                if not text.strip():
                    continue
                ident = str(item.get("id") or len(docs))
                source = item.get("source") if isinstance(item.get("source"), dict) else {}
                category = str(item.get("category") or "")
                tags = " ".join(str(t) for t in (item.get("tags") or []) if t)
                subtitle = " · ".join(x for x in (category, str(source.get("chat_title") or "")) if x)
                docs.append(Doc("memory", f"memory:{ident}", text.strip().splitlines()[0][:120],
                                f"{text}\n{tags}", _num(item.get("updated_at") or item.get("created_at")),
                                open={"action": "memory", "id": ident, "status": str(item.get("status") or "active")},
                                meta={"kind": category, "pinned": bool(item.get("pinned")),
                                      "pending": item.get("status") == "pending"},
                                subtitle=subtitle, title_weight=1))
        else:
            facts = raw.get("facts") if isinstance(raw.get("facts"), list) else []
            for index, fact in enumerate(facts[:2000]):
                text = str(fact.get("text") or "") if isinstance(fact, dict) else str(fact or "")
                if not text.strip():
                    continue
                docs.append(Doc("memory", f"memory:{index}", text.strip().splitlines()[0][:120], text,
                                open={"action": "memory", "index": index}, title_weight=1))
        rev = self.revision()
        return {"facts": (rev, lambda: docs)}


class PromptsProvider(Provider):
    name = "prompts"
    types = ("prompt",)

    def revision(self) -> Any:
        from .pgdata import MEMU_DIR
        return _stat_rev(Path(MEMU_DIR) / "prompts.json")

    def units(self) -> UnitMap:
        from .pgdata import load_prompts
        data = load_prompts()
        rev = self.revision()

        def build() -> List[Doc]:
            docs: List[Doc] = []
            for pset in data.get("prompt_sets") or []:
                if not isinstance(pset, dict):
                    continue
                set_id, set_name = str(pset.get("id") or ""), str(pset.get("name") or "")
                for row in pset.get("prompts") or []:
                    if not isinstance(row, dict):
                        continue
                    names = " ".join(str(row.get(k) or "") for k in ("name", "name_ru"))
                    desc = " ".join(str(row.get(k) or "") for k in ("description", "description_ru"))
                    docs.append(Doc("prompt", f"prompt:{set_id}:{row.get('id')}",
                                    str(row.get("name") or row.get("id")),
                                    f"{names}\n{desc}\n{str(row.get('text') or '')[:12000]}",
                                    open={"action": "prompt", "set_id": set_id, "prompt_id": str(row.get("id") or "")},
                                    meta={"set": set_name, "name_ru": str(row.get("name_ru") or ""),
                                          "set_ru": str(pset.get("name_ru") or "")},
                                    subtitle=set_name))
            return docs
        return {"all": (rev, build)}


class SchedulesProvider(Provider):
    name = "schedules"
    types = ("schedule",)

    def revision(self) -> Any:
        from .pgdata import MEMU_DIR
        return _stat_rev(Path(MEMU_DIR) / "schedules.json")

    def units(self) -> UnitMap:
        from .pgdata import load_schedules
        tasks = load_schedules()
        rev = self.revision()

        def build() -> List[Doc]:
            docs = []
            for task in tasks:
                task_id = str(task.get("id") or "")
                if not task_id:
                    continue
                when = task.get("daily_at") or (f"{task.get('interval_minutes')} min" if task.get("interval_minutes") else "")
                docs.append(Doc("schedule", f"schedule:{task_id}", str(task.get("name") or task.get("prompt") or "")[:160],
                                f"{task.get('prompt') or ''}\n{task.get('reminder_text') or ''}",
                                _num(task.get("run_at")) or _num(task.get("created")),
                                open={"action": "schedule", "task_id": task_id},
                                meta={"model": str(task.get("model") or ""), "enabled": task.get("enabled", True) is not False},
                                subtitle=str(when or "")))
            return docs
        return {"all": (rev, build)}


class DocsProvider(Provider):
    name = "docs"
    types = ("doc",)
    min_interval = 60.0

    def revision(self) -> Any:
        from .docs import docs_dir
        root = docs_dir()
        return (_stat_rev(root / "index.json"), _stat_rev(root / "ru"), _stat_rev(root / "en"))

    def units(self) -> UnitMap:
        from . import docs as docs_mod
        out: UnitMap = {}
        for lang in ("ru", "en"):
            try:
                catalog = docs_mod.catalog(lang)
            except Exception:
                continue
            ids: List[str] = []
            for group in catalog.get("groups") or []:
                for page in group.get("pages") or []:
                    if isinstance(page, dict) and page.get("id"):
                        ids.append(str(page["id"]))
            for page_id in ids:
                path = docs_mod.docs_dir() / lang / f"{page_id}.md"
                out[f"{lang}:{page_id}"] = (_stat_rev(path), (lambda l=lang, p=page_id: self._docs(l, p)))
        return out

    @staticmethod
    def _docs(lang: str, page_id: str) -> List[Doc]:
        from . import docs as docs_mod
        found = docs_mod._page_in(lang, page_id)
        if not found:
            return []
        docs: List[Doc] = []
        for heading, anchor, text in docs_mod._sections(found["body"]):
            clean = plain_markdown(docs_mod.plain(re.sub(r"```.*?```", " ", text, flags=re.S))).replace("#", " ")
            title = f"{found['title']} — {heading}" if heading else found["title"]
            docs.append(Doc("doc", f"doc:{lang}:{page_id}:{anchor}", title, f"{found.get('summary') or ''}\n{clean}" if not heading else clean,
                            group=f"doc:{lang}:{page_id}", group_title=found["title"],
                            open={"action": "doc", "page": page_id, "anchor": anchor},
                            meta={"lang": lang, "heading": heading}, subtitle=heading, title_weight=5))
        return docs


class WorkProjectsProvider(Provider):
    """Saved Work projects (name + path); their files come from :class:`WorkFilesProvider`."""
    name = "projects"
    types = ("project",)

    def revision(self) -> Any:
        from .pgdata import MEMU_DIR, _profile_data_name
        return _stat_rev(Path(MEMU_DIR) / _profile_data_name("workspaces.json"))

    def units(self) -> UnitMap:
        from .pgdata import load_workspaces
        data = load_workspaces()
        out: UnitMap = {}
        for item in data.get("items", []):
            pid = str(item["id"])
            out[pid] = ((item["name"], item["path"]), (lambda it=item: [Doc(
                "project", f"project:{it['id']}", it["name"], it["path"], _num(it.get("added")) * (1000 if 0 < _num(it.get("added")) < 1e11 else 1),
                open={"action": "project", "project_id": str(it["id"])},
                meta={"path": it["path"], "project_id": str(it["id"]), "project": it["name"], "desktop_only": True},
                subtitle=it["path"], title_weight=6)]))
        return out


# Work files ------------------------------------------------------------------

WORK_MAX_FILES_PER_PROJECT = 6000     # file names
WORK_MAX_CONTENT_FILES = 2500         # files whose text is indexed
WORK_MAX_FILE_BYTES = 400_000         # bigger files: name only
WORK_READ_BYTES = 48_000              # text indexed per file
WORK_MAX_BYTES_PER_PASS = 24_000_000
WORK_PASS_SECONDS = 4.0
WORK_RESCAN_SECONDS = 90.0


def _work_skip_dirs() -> set:
    try:
        from .work_context import SKIP_DIRS
        return set(SKIP_DIRS) | {".tox", "target", "bin", "obj", "out", ".gradle", ".terraform", "Pods"}
    except Exception:
        return {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}


def _work_text_ok(path: Path) -> bool:
    try:
        from .work_context import _is_probably_text
        return _is_probably_text(path)
    except Exception:
        return path.suffix.lower() in {".md", ".txt", ".py", ".js", ".ts", ".json"}


def read_text_file(path: Path, limit: int = WORK_READ_BYTES) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return ""
    if b"\x00" in raw[:8192]:
        return ""
    raw = raw[:limit]
    try:
        from .work_tools import _decode
        text, _enc = _decode(raw, len(raw) >= limit)
    except Exception:
        text = raw.decode("utf-8", "replace")
    return text.replace("\x00", "")


def scan_project(root: Path, *, deadline: float, budget: List[int]) -> List[Tuple[str, int, int]]:
    """``(relative path, mtime_ns, size)`` of the project's files; never leaves ``root``."""
    from .path_safety import is_link
    skip = _work_skip_dirs()
    out: List[Tuple[str, int, int]] = []
    try:
        root = root.resolve()
    except OSError:
        return out
    stack = [root]
    while stack and len(out) < WORK_MAX_FILES_PER_PROJECT and time.monotonic() < deadline:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        entries.sort(key=lambda e: e.name.lower())
        dirs: List[Path] = []
        for entry in entries:
            name = entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name in skip or name.startswith(".") and name not in (".github",):
                        continue
                    path = Path(entry.path)
                    if is_link(path):
                        continue
                    dirs.append(path)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    rel = Path(entry.path).relative_to(root).as_posix()
                    out.append((rel, st.st_mtime_ns, st.st_size))
                    if len(out) >= WORK_MAX_FILES_PER_PROJECT:
                        break
            except (OSError, ValueError):
                continue
        stack.extend(reversed(dirs))
    return out


class WorkFilesProvider(Provider):
    """Files of the saved Work projects: names always, text of small text files (bounded)."""
    name = "files"
    types = ("file",)
    min_interval = WORK_RESCAN_SECONDS
    desktop_only = True

    def __init__(self) -> None:
        self.enabled = True

    def revision(self) -> Any:
        return None   # always rescan (throttled by min_interval)

    def units(self) -> UnitMap:
        if not self.enabled:
            return {}
        from .pgdata import load_workspaces
        data = load_workspaces()
        out: UnitMap = {}
        deadline = time.monotonic() + WORK_PASS_SECONDS
        budget = [WORK_MAX_BYTES_PER_PASS]
        for item in data.get("items", [])[:40]:
            root = Path(item["path"])
            if not root.is_dir():
                continue
            files = scan_project(root, deadline=deadline, budget=budget)
            content_count = 0
            for rel, mtime, size in files:
                with_text = size <= WORK_MAX_FILE_BYTES and _work_text_ok(Path(rel)) and content_count < WORK_MAX_CONTENT_FILES
                if with_text:
                    content_count += 1
                key = f"{item['id']}:{rel}"
                out[key] = ((mtime, size, with_text, item["name"]),
                            (lambda it=item, r=rel, m=mtime, t=with_text: self._doc(it, r, m, t)))
        return out

    @staticmethod
    def _doc(item: Dict[str, Any], rel: str, mtime: int, with_text: bool) -> List[Doc]:
        root = Path(item["path"]).resolve()
        path = (root / rel)
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            return []
        text = read_text_file(path) if with_text else ""
        name = rel.rsplit("/", 1)[-1]
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        # the path words are indexed with the name (src/app/search.js → src app search js)
        body = rel.replace("/", " ").replace(".", " ") + "\n" + text

        def loader(p=path) -> str:
            try:
                p.resolve().relative_to(root)
            except (OSError, ValueError):
                return ""
            return read_text_file(p)

        return [Doc("file", f"file:{item['id']}:{rel}", name, body, mtime / 1e6,
                    open={"action": "file", "project_id": str(item["id"]), "path": rel},
                    meta={"project_id": str(item["id"]), "project": item["name"], "path": rel, "desktop_only": True},
                    subtitle=f"{item['name']} · {folder}" if folder else item["name"], title_weight=8,
                    store_text=False, loader=(loader if with_text else (lambda: "")))]


class PanelsProvider(Provider):
    name = "panels"
    types = ("panel",)
    min_interval = 5.0

    def revision(self) -> Any:
        from . import panels
        return _stat_rev(Path(panels._path()))

    def units(self) -> UnitMap:
        from . import panels
        doc = panels.load()
        rev = self.revision()

        def build() -> List[Doc]:
            out = []
            for panel in panels.active(doc):
                values = panel.get("values") if isinstance(panel.get("values"), dict) else {}
                text = " ".join(str(v)[:200] for v in list(values.values())[:40] if isinstance(v, (str, int, float)))
                out.append(Doc("panel", f"panel:{panel['id']}", str(panel.get("title") or panel.get("kind") or ""),
                               f"{panel.get('kind') or ''} {panel.get('subject') or ''}\n{text}",
                               _num(panel.get("updated_at") or panel.get("created_at")) * 1000 if 0 < _num(panel.get("updated_at") or panel.get("created_at")) < 1e11 else 0,
                               open={"action": "panel", "panel_id": str(panel["id"])},
                               meta={"kind": str(panel.get("kind") or "")}, subtitle=str(panel.get("kind") or "")))
            return out
        return {"all": (rev, build)}


class ModulesProvider(Provider):
    name = "modules"
    types = ("module",)
    min_interval = 10.0
    desktop_only = True

    def revision(self) -> Any:
        from . import modules_host
        root = modules_host.default_root()
        return (_stat_rev(root), _stat_rev(root / "state.json"))

    def units(self) -> UnitMap:
        from . import modules_host
        rows = modules_host.list_modules(modules_host.default_root())
        rev = self.revision()

        def build() -> List[Doc]:
            out = []
            for row in rows:
                if not row.get("id"):
                    continue
                # One document per UI language (like docs): the English UI must not show Russian titles/summaries.
                for lang in ("ru", "en"):
                    title = (row.get("title_en") if lang == "en" else "") or row.get("title") or row["id"]
                    summary = (row.get("summary_en") if lang == "en" else "") or row.get("summary") or ""
                    other = row.get("title") if lang == "en" else row.get("title_en")
                    out.append(Doc("module", f"module:{lang}:{row['id']}", title,
                                   " ".join(str(v or "") for v in (summary, other, row.get("author"), row["id"])),
                                   open={"action": "module", "module_id": row["id"]},
                                   meta={"enabled": bool(row.get("enabled")), "title_en": row.get("title_en") or "",
                                         "icon": row.get("icon") or "", "desktop_only": True, "lang": lang},
                                   subtitle=summary, title_weight=6))
            return out
        return {"all": (rev, build)}


class QuickHistoryProvider(Provider):
    name = "quick"
    types = ("quick",)

    def revision(self) -> Any:
        from .pgdata import MEMU_DIR
        return _stat_rev(Path(MEMU_DIR) / "quick_history.json")

    def units(self) -> UnitMap:
        from .pgdata import MEMU_DIR
        try:
            rows = json.loads((Path(MEMU_DIR) / "quick_history.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        rev = self.revision()

        def build() -> List[Doc]:
            out = []
            for i, row in enumerate(rows if isinstance(rows, list) else []):
                if not isinstance(row, dict) or not row.get("q"):
                    continue
                out.append(Doc("quick", f"quick:{i}:{int(_num(row.get('t')))}", str(row.get("q"))[:160],
                               f"{row.get('q')}\n{row.get('a') or ''}", _num(row.get("t")),
                               open={"action": "quick", "q": str(row.get("q"))[:2000]},
                               meta={"model": str(row.get("model") or "")}, subtitle=str(row.get("model") or ""),
                               title_weight=3))
            return out
        return {"all": (rev, build)}


def install_default_providers() -> None:
    for provider in (ChatsProvider(), MemoryProvider(), PromptsProvider(), SchedulesProvider(), DocsProvider(),
                     WorkProjectsProvider(), WorkFilesProvider(), PanelsProvider(), ModulesProvider(),
                     QuickHistoryProvider()):
        if provider.name not in INDEX.providers:
            INDEX.register(provider)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview(key: str, raw_query: str = "", *, context: int = 2) -> Optional[Dict[str, Any]]:
    """The context around a result for the preview pane."""
    q = parse_query(raw_query)
    words = list(dict.fromkeys(q.words + [w for p in q.phrases for w in tokens(p)]))[:12]
    with INDEX.lock:
        doc_id = INDEX.key_to_id.get(key)
        doc = INDEX.docs.get(doc_id) if doc_id else None
        if doc is None:
            return None
        matches = [_match_word(INDEX, w, last=(i == len(words) - 1)) for i, w in enumerate(words)]
        regex = _word_regex(set().union(set(), *(m.surfaces for m in matches)),
                            set().union(set(), *(m.stems for m in matches))) if matches else None
        group = INDEX.groups.get(doc.meta.get("_gid"))
        siblings = [INDEX.docs[d] for d in group.doc_ids] if group else [doc]
    out: Dict[str, Any] = {"id": key, "type": doc.type, "title": doc.group_title or doc.title,
                           "subtitle": doc.subtitle, "ts": doc.ts, "meta": _public_meta(doc.meta),
                           "open": dict(doc.open), "blocks": []}

    def block(text: str, role: str = "", label: str = "", hit: bool = False, ts: float = 0.0,
              width: int = 900, open_: Optional[Dict[str, Any]] = None) -> None:
        snip, ranges = snippet(text, regex, q.phrases, width=width)
        out["blocks"].append({"text": snip, "highlights": ranges, "role": role, "label": label, "hit": hit,
                              "ts": ts, "open": open_ or {}})

    if doc.type in CHAT_TYPES:
        chat_id = doc.open.get("chat_id")
        index = doc.open.get("index")
        from .pgdata import load_chats
        chat = next((c for c in load_chats() if str(c.get("id")) == chat_id), None)
        branch_rows = None
        if chat and doc.open.get("message_id") and doc.open.get("branch") == "other":
            # Round 35: an inactive version — show it among its own neighbours (from that version)
            from . import chat_tree
            loc = chat_tree.locate(chat, str(doc.open.get("message_id")))
            if loc:
                alt = chat["branches"]["forks"][loc["key"]]["alts"][loc["alt"]]
                branch_rows = [m for m in alt.get("m") or [] if isinstance(m, dict)]
                index = next((i for i, m in enumerate(branch_rows) if m.get("id") == doc.open.get("message_id")), None)
        if chat:
            messages = branch_rows if branch_rows is not None else [m for m in chat.get("messages") or [] if isinstance(m, dict)]
            visible = [i for i, m in enumerate(messages)
                       if not m.get("internal") and m.get("role") in ("user", "assistant")]
            if index is None:
                # the chat itself: its first exchange, or the first message that matches
                first_hit = next((i for i in visible if regex is not None and
                                  regex.search(fast_fold(_message_text(messages[i])))), None)
                index_view = first_hit if first_hit is not None else (visible[0] if visible else None)
            else:
                index_view = index
            pos = visible.index(index_view) if index_view in visible else 0
            for i in visible[max(0, pos - context):pos + context + 1]:
                m = messages[i]
                created = _num(m.get("createdAt"))
                block(_message_text(m), str(m.get("role")), str(m.get("model") or ""), i == index_view, created,
                      1600 if i == index_view else 420,
                      ({"action": "chat", "chat_id": chat_id, "mode": doc.open.get("mode"), "message_id": str(m.get("id") or ""),
                        "branch": "other", "created_at": created, "role": str(m.get("role"))} if branch_rows is not None and m.get("id") else
                       {"action": "chat", "chat_id": chat_id, "mode": doc.open.get("mode"), "index": i,
                        "created_at": created, "role": str(m.get("role"))}))
            out["messages"] = len(visible)
            out["position"] = pos + 1 if visible else 0
            if branch_rows is not None:
                out["branch"] = "other"
    elif doc.type == "file":
        text = _loaded_text(doc)
        lines = text.splitlines()
        hit_line = 0
        if regex is not None:
            for i, line in enumerate(lines):
                if regex.search(fast_fold(line)):
                    hit_line = i
                    break
        lo = max(0, hit_line - 6)
        excerpt = lines[lo:lo + 30]
        out["code"] = {"start": lo + 1, "hit": hit_line + 1, "lines": [l[:400] for l in excerpt],
                       "highlights": [_ranges(l[:400], regex) for l in excerpt]}
        out["open"] = {**doc.open, "line": hit_line + 1}
        out["meta"]["line"] = hit_line + 1
    elif doc.type == "doc" and len(siblings) > 1:
        for sib in siblings:
            if sib is doc or (regex is not None and regex.search(_doc_folded(sib))):
                block(sib.text, label=sib.meta.get("heading") or "", hit=sib is doc, width=1200 if sib is doc else 360,
                      open_=dict(sib.open))
            if len(out["blocks"]) >= 4:
                break
    else:
        text = doc.text if doc.store_text else _loaded_text(doc)
        block(text or doc.title, label=doc.subtitle, hit=True, width=2400)
    return out
