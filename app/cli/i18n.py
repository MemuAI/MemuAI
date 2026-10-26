"""English interface strings for MemuCode CLI.

The two-argument ``tr`` API remains for existing callers while all visible
CLI text uses the English argument.
"""
from __future__ import annotations

_LANG = "en"

def detect_language() -> str:
    """The CLI interface is English on every host."""
    return "en"

def set_language(value: str) -> str:
    global _LANG

    _LANG = "en"
    return _LANG

def language() -> str:
    return _LANG

def tr(ru: str, en: str) -> str:
    return en

def plural(count: int, one: str, few: str, many: str, en_one: str = "", en_many: str = "") -> str:
    """Russian plural forms; English falls back to one/many."""
    n = abs(int(count))
    if _LANG == "en":
        word = en_one if n == 1 else (en_many or en_one + "s")
        return f"{count} {word}"
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{count} {word}"
