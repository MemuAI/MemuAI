"""MemuNote (Round 33, item #9): built-in spell checking, Russian + English, offline.

Engine: Hunspell dictionaries shipped with MemuNote (``app/memunote/dicts``: ru_RU by A. Lebedev, en_US from
SCOWL — both BSD-style licences, files next to them) read by ``spylls`` (a pure-Python Hunspell port, MPL-2.0).
A word is checked against the dictionary of its script (Cyrillic → ru, Latin → en), so mixed RU/EN text is
never flagged just for being in the other language. If ``spylls`` or the dictionaries are missing, the Windows
spell checker (``app/spellcheck.py``, ISpellChecker COM — the same one MemuAI's chat uses) is the fallback.

The page tokenises the visible text itself (code, URLs, e-mails, ``CamelCase``, ``snake_case``, words with digits
are skipped there) and asks about unique words only:

* ``POST /api/spell/check``    ``{words: [...]}`` → ``{ready, bad: [...]}`` (``ready: false`` while loading);
* ``POST /api/spell/suggest``  ``{word}`` → ``{suggestions: [...]}`` (≤ 8);
* ``POST /api/spell/add``      ``{word}`` → the user dictionary ``memunote/dictionary.txt`` (also ``remove``);
* ``GET  /api/spell/status``   engine, languages, number of user words.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .paths import note_dir

log = logging.getLogger("memunote.spell")

DICT_DIR = Path(__file__).resolve().parent / "dicts"
LANGS = {"ru": "ru_RU", "en": "en_US"}
MAX_WORDS = 4000
MAX_WORD_LEN = 48
CYR = re.compile(r"[\u0400-\u04FF]")
LAT = re.compile(r"[A-Za-z\u00C0-\u024F]")
WORD_OK = re.compile(r"^[\w'’\-]+$", re.UNICODE)

class _Engine:
    def __init__(self) -> None:
        self.dicts: Dict[str, Any] = {}
        self.state = "idle"
        self.error = ""
        self.lock = threading.Lock()
        self.cache: Dict[str, bool] = {}
        self.user: Optional[Set[str]] = None
        self.ignored: Set[str] = set()

    def ensure(self, wait: float = 0.0) -> str:
        with self.lock:
            if self.state == "idle":
                self.state = "loading"
                threading.Thread(target=self._load, name="memunote-spell", daemon=True).start()
        if wait and self.state == "loading":
            deadline = threading.Event()
            for _ in range(int(wait * 20)):
                if self.state != "loading":
                    break
                deadline.wait(0.05)
        return self.state

    def _load(self) -> None:
        try:
            from spylls.hunspell import Dictionary
            loaded = {}
            for lang, name in LANGS.items():
                base = DICT_DIR / name
                if (base.with_suffix(".aff")).is_file() and (base.with_suffix(".dic")).is_file():
                    loaded[lang] = Dictionary.from_files(str(base))
            if not loaded:
                raise FileNotFoundError("no dictionaries in " + str(DICT_DIR))
            self.dicts = loaded
            self.state = "ready"
        except Exception as exc:  # noqa: BLE001
            log.warning("built-in spell checker unavailable: %s", exc)
            self.error = str(exc)[:300]
            self.state = "windows" if _windows_available() else "unavailable"

    def user_words(self) -> Set[str]:
        if self.user is None:
            try:
                text = (note_dir() / "dictionary.txt").read_text(encoding="utf-8")
            except OSError:
                text = ""
            self.user = {w.strip() for w in text.splitlines() if w.strip()}
        return self.user

    def _save_user(self) -> None:
        path = note_dir() / "dictionary.txt"
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(sorted(self.user_words(), key=str.lower)) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def add(self, word: str) -> None:
        self.user_words().add(word)
        self.cache.pop(word, None)
        self._save_user()

    def remove(self, word: str) -> None:
        self.user_words().discard(word)
        self.cache.pop(word, None)
        self._save_user()

    def known(self, word: str) -> bool:
        users = self.user_words()
        if word in users or any(u.lower() == word.lower() for u in users):
            return True
        if word in self.cache:
            return self.cache[word]
        ok = self._known(word)
        if len(self.cache) > 200_000:
            self.cache.clear()
        self.cache[word] = ok
        return ok

    def _dict_for(self, word: str):
        if CYR.search(word):
            return self.dicts.get("ru")
        if LAT.search(word):
            return self.dicts.get("en")
        return None

    def _known(self, word: str) -> bool:
        d = self._dict_for(word)
        if d is None:
            return True
        variants = [word, word.replace("’", "'")]
        if "ё" in word or "Ё" in word:
            variants.append(word.replace("ё", "е").replace("Ё", "Е"))
        for v in variants:
            try:
                if d.lookup(v):
                    return True
            except Exception:  # noqa: BLE001
                return True
        if "-" in word:
            parts = [p for p in word.split("-") if p]
            return bool(parts) and all(self._known(p) for p in parts)
        return False

    def check(self, words: List[str]) -> List[str]:
        state = self.ensure()
        if state == "ready":
            return [w for w in words if not self.known(w)]
        if state == "windows":
            return _windows_bad(words, self.user_words())
        return []

    def suggest(self, word: str) -> List[str]:
        if self.ensure(wait=8) == "ready":
            d = self._dict_for(word)
            if d is None:
                return []
            out: List[str] = []
            try:
                for s in d.suggest(word):
                    if s not in out:
                        out.append(s)
                    if len(out) >= 14:
                        break
            except Exception:  # noqa: BLE001
                pass
            return rerank(word, out)[:8]
        if self.state == "windows":
            return _windows_suggest(word)
        return []

_CHEAP = {frozenset(p) for p in ("еи", "оа", "её", "иы", "ая", "ую", "эе", "шщ", "зс", "дт", "бп", "вф", "гк", "жш", "ьъ",
                                  "ei", "ao", "ou", "ae", "iy", "sz", "ck")}

def _distance(a: str, b: str) -> float:
    a, b = a.lower(), b.lower()
    prev2: List[float] = []
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                sub = 0.0
            elif frozenset((a[i - 1], b[j - 1])) in _CHEAP:
                sub = 0.5
            else:
                sub = 1.0
            ins = 1.2 if b[j - 1] == " " else 1.0
            cur[j] = min(prev[j] + 1, cur[j - 1] + ins, prev[j - 1] + sub)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 0.7)
        prev2, prev = prev, cur
    return prev[-1]

def rerank(word: str, items: List[str]) -> List[str]:
    lower = word[:1].islower()

    def score(pair):
        idx, s = pair
        return _distance(word, s) + (1.0 if lower and s[:1].isupper() else 0.0) + idx * 0.04
    return [s for _, s in sorted(enumerate(items), key=score)]

ENGINE = _Engine()

def _windows_available() -> bool:
    try:
        from .. import spellcheck
        return bool(spellcheck.spell_status().get("available"))
    except Exception:  # noqa: BLE001
        return False

def _windows_bad(words: List[str], user: Set[str]) -> List[str]:
    try:
        from .. import spellcheck
    except Exception:  # noqa: BLE001
        return []
    text = "\n".join(words)
    lang = "ru-RU" if any(CYR.search(w) for w in words) else "en-US"
    result = spellcheck.check_text(text, lang)
    bad = []
    for err in result.get("errors") or []:
        w = text[err.get("start", 0): err.get("start", 0) + err.get("length", 0)]
        if w and w not in user:
            bad.append(w)
    return bad

def _windows_suggest(word: str) -> List[str]:
    try:
        from .. import spellcheck
        lang = "ru-RU" if CYR.search(word) else "en-US"
        for err in spellcheck.check_text(word, lang).get("errors") or []:
            return list(err.get("suggestions") or [])[:8]
    except Exception:  # noqa: BLE001
        pass
    return []

def _clean_word(value: Any) -> str:
    word = str(value or "").strip()[:MAX_WORD_LEN]
    return word if word and WORD_OK.match(word) else ""

def api_spell_status(self, method: str) -> None:
    state = ENGINE.ensure()
    self._json({"ok": True, "state": state, "engine": "hunspell" if state == "ready" else state,
                "languages": sorted(ENGINE.dicts) if state == "ready" else [], "user_words": len(ENGINE.user_words()),
                "error": ENGINE.error})

def api_spell_check(self, method: str) -> None:
    body = self._json_body()
    words: List[str] = []
    seen: Set[str] = set()
    for item in (body.get("words") or [])[:MAX_WORDS]:
        word = _clean_word(item)
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    bad = ENGINE.check(words)
    self._json({"ok": True, "ready": ENGINE.state in ("ready", "windows"), "state": ENGINE.state, "bad": bad})

def api_spell_suggest(self, method: str) -> None:
    word = _clean_word(self._json_body().get("word"))
    self._json({"ok": True, "suggestions": ENGINE.suggest(word) if word else []})

def api_spell_add(self, method: str) -> None:
    word = _clean_word(self._json_body().get("word"))
    if not word:
        self._error("bad word")
        return
    ENGINE.add(word)
    self._json({"ok": True, "word": word})

def api_spell_remove(self, method: str) -> None:
    word = _clean_word(self._json_body().get("word"))
    if word:
        ENGINE.remove(word)
    self._json({"ok": True})

def register(handler_cls) -> None:
    for name in ("status", "check", "suggest", "add", "remove"):
        setattr(handler_cls, "api_spell_" + name, globals()["api_spell_" + name])
