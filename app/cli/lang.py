"""Which language should the agent answer in? The one the user writes in.

The interface of MemuCode is English, and so are its instructions to the model — which made models
drift into English even for a Russian request. Every turn therefore carries a short reminder naming
the language of the user's latest message, detected here from its script (Cyrillic, Han, Hangul, …).
Latin-script text cannot be told apart cheaply, so it gets the general "same language as the user"
rule instead of a wrong guess.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_TAGGED = re.compile(r"<(attached_files|file|folder|system-reminder|supplemental_context|bash-stdout|"
                     r"tool_results?|memory|hook_context|session_start_context)\b.*?</\1>", re.S)
_URL_PATH = re.compile(r"https?://\S+|[A-Za-z]:[\\/][^\s]*|(?:\./|\.\./|/)?[\w.-]+(?:/[\w.-]+)+")

_RANGES = (
    ("cyrillic", 0x0400, 0x052F), ("han", 0x4E00, 0x9FFF), ("han", 0x3400, 0x4DBF),
    ("kana", 0x3040, 0x30FF), ("hangul", 0xAC00, 0xD7AF), ("hangul", 0x1100, 0x11FF),
    ("arabic", 0x0600, 0x06FF), ("hebrew", 0x0590, 0x05FF), ("greek", 0x0370, 0x03FF),
    ("thai", 0x0E00, 0x0E7F), ("devanagari", 0x0900, 0x097F),
)
_NAMES = {"han": "Chinese", "kana": "Japanese", "hangul": "Korean", "arabic": "Arabic", "hebrew": "Hebrew",
          "greek": "Greek", "thai": "Thai", "devanagari": "Hindi"}


def prose(text: str) -> str:
    """The natural-language part of a message: no code, attached files, paths or links."""
    text = _TAGGED.sub(" ", str(text or ""))
    text = _CODE.sub(" ", text)
    return _URL_PATH.sub(" ", text)


def _script(char: str) -> Optional[str]:
    code = ord(char)
    for name, low, high in _RANGES:
        if low <= code <= high:
            return name
    if char.isalpha() and code < 0x0250:
        return "latin"
    return None


def detect(text: str) -> str:
    """"Russian", "Chinese", … or "" when the text is Latin-script or has no letters."""
    counts: dict = {}
    sample = prose(text)
    for char in sample:
        if not char.isalpha():
            continue
        script = _script(char)
        if script:
            counts[script] = counts.get(script, 0) + 1
    if not counts:
        return ""
    if counts.get("kana"):
        return "Japanese"                                   # Japanese mixes kana with han
    letters = sum(counts.values())
    others = {name: number for name, number in counts.items() if name != "latin"}
    if not others:
        return ""
    script = max(others, key=lambda name: others[name])
    # Technical English words inside a Russian sentence are common; the reverse (one foreign word in an
    # English sentence) stays below the threshold.
    if counts[script] * 10 < letters * 3:
        return ""
    if script == "cyrillic":
        if re.search(r"[іїєґІЇЄҐ]", sample):
            return "Ukrainian"
        if re.search(r"[јљњћђџЈЉЊЋЂЏ]", sample):
            return "Serbian"
        if re.search(r"[ўЎ]", sample):
            return "Belarusian"
        return "Russian"
    return _NAMES.get(script, "")


#: Round 32: frequent English function words. A Latin-script message made mostly of them is English — the
#: general "(it may not be English)" fallback pushed models into Russian for a plain English request.
_ENGLISH_WORDS = frozenset(
    "the a an and or but if then is are was were be been it this that these those to of in on at for with from by "
    "as into what which who how why when where do does did not no can could should would will please fix add make "
    "find run show explain tell me my you your we our all any each every some only also there here so than"
    .split())
_WORD = re.compile(r"[A-Za-z']+")


def english(text: str) -> bool:
    """Is this Latin-script prose English? (cheap: share of common English function words)"""
    words = [word.lower() for word in _WORD.findall(prose(text))]
    if len(words) < 4:
        return False
    hits = sum(1 for word in words if word in _ENGLISH_WORDS)
    return hits >= 3 and hits * 100 >= len(words) * 22


def reminder(texts: Iterable[str]) -> str:
    """The per-turn language rule, from the user's latest messages (newest first wins)."""
    language = ""
    for text in texts:
        letters = sum(1 for char in prose(text) if char.isalpha())
        if not letters:
            continue                                 # "12345", "?!" — says nothing about the language
        language = detect(text) or ("English" if english(text) else "")
        if language or letters >= 12:
            break                                    # a real sentence decides; a short Latin word ("ok", "go on")
                                                     # is ambiguous, so the earlier message still counts
    if language:
        return (f"The user is writing in {language}. Reply in {language} — your messages, plans, todo items, "
                f"questions to the user and the final report — unless they ask for another language. Keep code, "
                f"identifiers, commands and file paths exactly as they are.")
    return "Reply in the same language as the user's latest message, unless they ask for another language."
