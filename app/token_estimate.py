"""How many tokens a text costs — an honest estimate without a tokenizer.

Every model family counts tokens with its own vocabulary, and languages differ a
lot: a Russian word is one to three tokens, an English word about one, a Chinese
character almost one, code sits in between. "Characters divided by four" is
right for English prose only and undercounts Russian by a quarter or more.

The estimate is a linear model over a few character classes (Latin and Cyrillic
letters *and* words, digits, punctuation, line breaks, spaces, indentation,
CJK, Hangul, everything else). The coefficients below were fitted with
non-negative least squares on ~2 000 pieces of real text (MemuAI docs in
Russian and English, Python, JS, CSS, the modules AI pack, Wikipedia in
ru/zh/ja/ko, Russian mixed with code) against the published tokenizers:
o200k (GPT‑4o … GPT‑5), Gemma 3 (Gemini), Qwen 3, DeepSeek V3, Llama 3.2,
Mistral Nemo (Tekken), GLM 4.5, MiniMax M2 — mean error 5 % per piece, less
on long texts. Claude has no public tokenizer: its rows are the older Claude
tokenizer scaled by group (Latin/layout, digits/punctuation, other scripts) to
the counts ``tokenizer_probe.REFERENCES`` measured on the vendor's API, so they
are the roughest of the set.

``estimate()`` is the typical count (the median over the families) and is what
every counter in MemuAI shows; ``breakdown()`` adds the range over the families
and, when the optional ``tiktoken`` package is installed with its o200k table
cached, the exact GPT count. The JS twin is ``app/pgstatic/tokens.js`` — the
same features and the same table (``tests/round30_misc_test.py`` keeps them in
sync). To recalibrate: fit the twelve features in ``FEATURES`` order per
tokenizer and replace the rows of both files.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

FEATURES = ("latw", "lat", "cyrw", "cyr", "dig", "punct", "nl", "ws", "wsrun", "cjk", "hang", "other")

#: Tokens per unit of each feature, in ``FEATURES`` order.
FAMILIES: List[Dict[str, Any]] = [
    {"id": "gpt", "label": "GPT-4o / GPT-5", "source": "o200k",
     "coef": [0.484, 0.079, 1.053, 0.075, 1.033, 0.529, 0.438, 0.320, 0.046, 0.794, 0.673, 0.544]},
    {"id": "claude", "label": "Claude 3 – 4.6", "source": "derived",
     "coef": [0.724, 0.111, 0.907, 0.338, 1.180, 0.679, 0.721, 0.046, 0.010, 0.916, 1.195, 0.523]},
    {"id": "claude-new", "label": "Claude Opus 4.7+", "source": "derived",
     "coef": [1.075, 0.165, 0.911, 0.339, 1.380, 0.794, 1.069, 0.068, 0.015, 0.920, 1.200, 0.526]},
    {"id": "gemini", "label": "Gemini / Gemma", "source": "gemma-3",
     "coef": [0.672, 0.090, 1.220, 0.067, 1.512, 0.625, 0.956, 0.063, 0.097, 0.519, 0.665, 0.747]},
    {"id": "qwen", "label": "Qwen 3", "source": "qwen3",
     "coef": [0.485, 0.083, 0.977, 0.175, 1.562, 0.497, 0.432, 0.310, 0.049, 0.684, 0.801, 0.418]},
    {"id": "deepseek", "label": "DeepSeek V3 / R1", "source": "deepseek-v3",
     "coef": [0.498, 0.080, 1.370, 0.068, 0.953, 0.589, 0.338, 0.322, 0.078, 0.649, 0.771, 0.227]},
    {"id": "llama", "label": "Llama 3 / 4", "source": "llama-3.2",
     "coef": [0.564, 0.069, 0.957, 0.148, 1.066, 0.515, 0.411, 0.288, 0.043, 0.714, 0.715, 0.920]},
    {"id": "mistral", "label": "Mistral", "source": "tekken",
     "coef": [0.372, 0.123, 1.167, 0.106, 1.513, 0.565, 0.342, 0.214, 0.066, 0.776, 0.649, 1.204]},
    {"id": "glm", "label": "GLM", "source": "glm-4.5",
     "coef": [0.529, 0.068, 1.106, 0.075, 1.145, 0.504, 0.448, 0.345, 0.046, 0.717, 0.974, 0.334]},
    {"id": "minimax", "label": "MiniMax", "source": "minimax-m2",
     "coef": [0.450, 0.081, 1.209, 0.112, 0.907, 0.566, 0.378, 0.325, 0.063, 0.414, 0.567, 0.921]},
]
_BY_ID = {row["id"]: row for row in FAMILIES}

_LATIN_WORD = re.compile(r"[A-Za-z\u00C0-\u024F]+")
_CYRILLIC_WORD = re.compile(r"[\u0400-\u04FF]+")
_DIGIT = re.compile(r"[0-9]")
_PUNCT = re.compile(r"[!-/:-@\[-`{-~]")
_WS_RUN = re.compile(r"[ \t]{2,}")
_CJK = re.compile(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_HANGUL = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")

_MEMO: Dict[str, int] = {}
_MEMO_FROM = 400                  # shorter texts are cheaper to count than to look up
_MEMO_LIMIT = 1500


def features(text: str) -> List[int]:
    """Counts of the character classes, in ``FEATURES`` order."""
    text = str(text or "")
    if not text:
        return [0] * len(FEATURES)
    latin = _LATIN_WORD.findall(text)
    cyrillic = _CYRILLIC_WORD.findall(text)
    lat, cyr = sum(map(len, latin)), sum(map(len, cyrillic))
    nl = text.count("\n")
    spaces = text.count(" ") + text.count("\t")
    run = sum(len(item) - 1 for item in _WS_RUN.findall(text))
    if text.isascii():
        dig, punct, cjk, hang = len(_DIGIT.findall(text)), len(_PUNCT.findall(text)), 0, 0
    else:
        dig, punct = len(_DIGIT.findall(text)), len(_PUNCT.findall(text))
        cjk, hang = len(_CJK.findall(text)), len(_HANGUL.findall(text))
    known = lat + cyr + dig + punct + nl + spaces + cjk + hang
    return [len(latin), lat, len(cyrillic), cyr, dig, punct, nl, spaces - run, run, cjk, hang, max(0, len(text) - known)]


def _apply(coef: List[float], counts: List[int]) -> float:
    return sum(c * n for c, n in zip(coef, counts))


def _per_family(counts: List[int]) -> List[float]:
    return [_apply(row["coef"], counts) for row in FAMILIES]


def _ceil(value: float) -> int:
    # The tiny step keeps a float sum like 2.0000000001 from becoming 3 (and Python equal to JS).
    return int(math.ceil(value - 1e-9))


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def family_of(model: str) -> str:
    """The family id for a model name ("" when it is not one of ours)."""
    name = str(model or "").lower()
    if not name:
        return ""
    if re.search(r"claude|anthropic|opus|sonnet|haiku", name):
        return "claude-new" if re.search(r"(opus|sonnet|haiku)[-_ ]?(4[-_.][7-9]|[5-9])|claude[-_ ]?[5-9]", name) else "claude"
    for pattern, family in ((r"gemini|gemma", "gemini"), (r"qwen|qwq", "qwen"), (r"deepseek", "deepseek"),
                            (r"llama", "llama"), (r"mistral|mixtral|codestral|devstral|magistral|ministral", "mistral"),
                            (r"glm|zhipu|z-ai", "glm"), (r"minimax", "minimax"),
                            (r"gpt|openai|codex|(^|[-/_])o\d|chatgpt", "gpt")):
        if re.search(pattern, name):
            return family
    return ""


def estimate(text: Any, model: str = "") -> int:
    """Tokens of ``text``: the given model family's count, or the typical one (median of the families)."""
    if not text:
        return 0
    text = str(text)
    family = family_of(model)
    long = len(text) >= _MEMO_FROM
    key = family + "\0" + text if family else text
    if long:
        # Conversations are counted again at every request, and nearly all of it is the same strings as the time
        # before (a string remembers its own hash, so the lookup is cheap): count each one once.
        known = _MEMO.get(key)
        if known is not None:
            return known
    counts = features(text)
    value = _apply(_BY_ID[family]["coef"], counts) if family else _median(_per_family(counts))
    result = max(1, _ceil(value))
    if long:
        if len(_MEMO) >= _MEMO_LIMIT:
            for stale in list(_MEMO)[:_MEMO_LIMIT // 3]:       # the oldest third (dicts keep insertion order)
                _MEMO.pop(stale, None)
        _MEMO[key] = result
    return result


def estimate_float(text: Any) -> float:
    """Like :func:`estimate`, but fractional and without the memo: for short streamed pieces."""
    if not text:
        return 0.0
    return _median(_per_family(features(str(text))))


def script_mix(counts: List[int]) -> Dict[str, float]:
    """Share of the text's characters per script group (cyrillic, latin, code — digits and punctuation —, cjk, other)."""
    total = sum(counts[i] for i in (1, 3, 4, 5, 9, 10, 11)) or 1
    return {"cyrillic": round(counts[3] / total, 3), "latin": round(counts[1] / total, 3),
            "code": round((counts[4] + counts[5]) / total, 3), "cjk": round((counts[9] + counts[10]) / total, 3),
            "other": round(counts[11] / total, 3)}


_EXACT: Dict[str, Any] = {}


def _exact_o200k(text: str) -> Optional[int]:
    """The exact GPT count when the optional ``tiktoken`` package can load o200k (never downloads in a test run)."""
    if "enc" not in _EXACT:
        _EXACT["enc"] = None
        try:
            import hashlib
            import os
            import tempfile

            import tiktoken  # optional: pip install tiktoken
            # tiktoken downloads its table on first use; only take it when it is already cached.
            url = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
            folder = (os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR")
                      or os.path.join(tempfile.gettempdir(), "data-gym-cache"))
            if os.path.isfile(os.path.join(folder, hashlib.sha1(url.encode()).hexdigest())):
                _EXACT["enc"] = tiktoken.get_encoding("o200k_base")
        except Exception:  # not installed, or its table cannot be loaded
            _EXACT["enc"] = None
    encoder = _EXACT["enc"]
    if encoder is None:
        return None
    try:
        return len(encoder.encode(text, disallowed_special=()))
    except Exception:
        return None


def breakdown(text: Any, exact: bool = True) -> Dict[str, Any]:
    """The estimate with its honesty attached: range over the families, each family's count, the script mix."""
    text = str(text or "")
    counts = features(text)
    rows = [{"id": row["id"], "label": row["label"], "tokens": _ceil(value)}
            for row, value in zip(FAMILIES, _per_family(counts))]
    exact_gpt = _exact_o200k(text) if exact and text else None
    if exact_gpt is not None:
        rows[0].update(tokens=exact_gpt, exact=True)
    values = [row["tokens"] for row in rows]
    return {"tokens": estimate(text) if text else 0, "low": min(values) if text else 0, "high": max(values) if text else 0,
            "families": rows, "mix": script_mix(counts), "estimated": True,
            "chars_per_token": round(len(text) / max(1, estimate(text)), 2) if text else 0.0}
