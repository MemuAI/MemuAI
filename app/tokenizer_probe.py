"""Tokenizer fingerprint for the model detector.

Every model family counts tokens with its own tokenizer, and a reseller
cannot fake that number without re-tokenizing the request itself: the
provider's ``prompt_tokens`` for a fixed text is a property of the model that
really served it. The probe sends three fixed texts (prose and code, digits
and symbols, non-Latin scripts) plus an empty baseline, subtracts the
baseline (so chat templates and injected system prompts cancel out) and
compares the three differences with reference counts.

The texts below must never change: every reference in ``REFERENCES`` was
measured for exactly these bytes. ``source`` says how a reference was
obtained: ``offline`` — computed with the vendor's published tokenizer,
``first-party`` — measured on the vendor's own API, ``observed`` — measured
through third-party gateways that agreed with each other (weaker: shown as a
hint, never as proof).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PREFIX = "Reply with the single word OK and nothing else.\n\n<<<\n"
SUFFIX = "\n>>>"

SEGMENTS: Dict[str, str] = {
    "prose": (
        "The quick brown fox jumps over the lazy dog near the riverbank, while seventeen unremarkable pigeons "
        "reorganize themselves counterclockwise around an abandoned lighthouse. Nevertheless, the cartographer's "
        "apprentice misremembered every coordinate.\n"
        "def fibonacci(n: int) -> int:\n"
        "    return n if n < 2 else fibonacci(n - 1) + fibonacci(n - 2)\n"
        "SELECT user_id, COUNT(*) AS total FROM purchases WHERE created_at >= '2024-01-01' GROUP BY user_id "
        "HAVING total > 3;\n"
        "https://example.com/api/v2/items?sort=desc&limit=25#section-4 antidisestablishmentarianism "
        "pneumonoultramicroscopicsilicovolcanoconiosis supercalifragilisticexpialidocious"
    ),
    "symbols": (
        "3141592653589793238462643383279502884197 1,234,567.89 0x7fffffff 00000000 9999999999 +1-202-555-0173 "
        "2024-11-05T13:45:30Z 6.02214076e23 -273.15 1/3 = 0.3333333333 "
        "<<<>>> ==== !!!!!! ???? $$$$ %%% ^^^^ ~~~~ |||| //// @@@@ #### &&&& **** ---- ____ ++++ "
        "a1b2c3d4e5f6g7h8i9j0 AbCdEfGhIjKlMnOpQrStUvWxYz {[()]} ::: ;;; ,,, ... "
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ),
    "scripts": (
        "Съешь же ещё этих мягких французских булок, да выпей чаю. Электрификация железнодорожного транспорта "
        "продолжается. 敏捷的棕色狐狸跳过了懒狗。人工智能正在改变世界。 すばやい茶色の狐がのろまな犬を飛び越える。 "
        "빠른 갈색 여우가 게으른 개를 뛰어넘습니다. الثعلب البني السريع يقفز فوق الكلب الكسول. "
        "Η γρήγορη καφέ αλεπού πηδάει πάνω από το τεμπέλικο σκυλί. 😀🚀🌍🎉 naïve façade Übergrößenträger"
    ),
}
ORDER = ("prose", "symbols", "scripts")

REFERENCES: List[Dict[str, Any]] = [
    {"id": "o200k", "label": "OpenAI o200k (GPT-4o … GPT-5.x, o-series, gpt-oss)", "families": ["gpt"],
     "source": "offline", "counts": [159, 192, 147]},
    {"id": "cl100k", "label": "OpenAI cl100k (GPT-4, GPT-3.5) / Phi-4", "families": ["gpt", "phi"],
     "source": "offline", "counts": [164, 200, 252]},
    {"id": "claude", "label": "Claude (3 … 4.6, Haiku 4.5)", "families": ["claude"],
     "source": "first-party", "counts": [188, 224, 228]},
    {"id": "claude-2026", "label": "Claude (Opus 4.7 and newer)", "families": ["claude"],
     "source": "observed", "counts": [279, 262, 229]},
    {"id": "gemini", "label": "Gemini / Gemma", "families": ["gemini"],
     "source": "offline", "counts": [175, 285, 132]},
    {"id": "qwen", "label": "Qwen (2.5, 3)", "families": ["qwen"], "source": "offline", "counts": [169, 293, 183]},
    {"id": "deepseek", "label": "DeepSeek (V3, R1)", "families": ["deepseek"], "source": "offline", "counts": [160, 202, 159]},
    {"id": "llama3", "label": "Llama 3.x", "families": ["llama"], "source": "offline", "counts": [163, 200, 161]},
    {"id": "llama4", "label": "Llama 4", "families": ["llama"], "source": "offline", "counts": [158, 192, 139]},
    {"id": "mistral", "label": "Mistral (Nemo, Small 3 — Tekken)", "families": ["mistral"],
     "source": "offline", "counts": [173, 298, 161]},
    {"id": "mistral-v3", "label": "Mistral (7B v0.3 and older)", "families": ["mistral"],
     "source": "offline", "counts": [208, 305, 249]},
    {"id": "glm", "label": "GLM (4.5, 4.6)", "families": ["glm"], "source": "offline", "counts": [164, 224, 156]},
    {"id": "glm-5", "label": "GLM (5.x)", "families": ["glm"], "source": "observed", "counts": [164, 224, 152]},
    {"id": "minimax", "label": "MiniMax (M2)", "families": ["minimax"], "source": "offline", "counts": [160, 194, 156]},
    {"id": "hunyuan", "label": "Hunyuan", "families": ["hunyuan"], "source": "offline", "counts": [169, 292, 222]},
    {"id": "yi", "label": "Yi (1.5)", "families": ["yi"], "source": "offline", "counts": [200, 315, 311]},
]

BARE_REQUEST_TOKENS = 30

IMPLAUSIBLE_RATIO = 0.6

MATCH_TOLERANCE = 0.025

SHIFT_TOLERANCE = 0.012

MAX_SHIFT = 120

MISMATCH_TOLERANCE = 0.08

def messages(segment: str = "") -> List[Dict[str, str]]:
    """The probe request for one segment; an empty name gives the baseline."""
    return [{"role": "user", "content": PREFIX + SEGMENTS.get(segment, "") + SUFFIX}]

def probe_plan() -> List[Dict[str, Any]]:
    """Baseline, the three texts, the baseline again.

    The detector runs these one after another (``chain``), not in parallel: a gateway that
    caches or rotates its hidden system prompt reports a different size for the first
    request than for the following ones, and the second baseline shows whether it moved.
    """
    def step(name: str, segment: str) -> Dict[str, Any]:
        return {"id": f"tokenizer_{name}", "kind": "tokenizer", "chain": "tokenizer", "max_tokens": 16,
                "messages": messages(segment)}
    return [step("base", "")] + [step(name, name) for name in ORDER] + [step("base2", "")]

def measure(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Token counts of the three segments from the probe results, or why there are none."""
    rows = {name: results.get(f"tokenizer_{name}") or {} for name in ("base",) + ORDER}
    if not any(rows.values()):
        return {"status": "not_run"}
    failed = [name for name, row in rows.items() if not row or row.get("error")]
    if failed:
        return {"status": "failed", "failed": failed}
    usages = {name: row.get("usage") or {} for name, row in rows.items()}
    if any(not usage or usage.get("estimated") is not False for usage in usages.values()):

        return {"status": "no_usage"}
    first = int(usages["base"].get("prompt_tokens") or 0)
    again = results.get("tokenizer_base2") or {}
    second = int((again.get("usage") or {}).get("prompt_tokens") or 0) \
        if not again.get("error") and (again.get("usage") or {}).get("estimated") is False else 0

    base = second or first
    counts = [int(usages[name].get("prompt_tokens") or 0) - base for name in ORDER]
    if base <= 0 or any(value <= 0 for value in counts):
        return {"status": "no_usage"}
    jitter = abs(first - second) if second else 0
    return {"status": "measured", "counts": counts, "base": base,
            "stable": jitter <= 2, "jitter": jitter if jitter > 2 else 0}

def distance(counts: List[int], reference: List[int]) -> float:
    """Largest relative difference over the three segments."""
    return max(abs(got - want) / max(1, want) for got, want in zip(counts, reference))

def shifted(counts: List[int], reference: List[int]) -> tuple:
    """The constant offset that brings the counts closest to a reference, and what is left.

    A hidden prompt that changed between the baseline and the texts adds the same number of
    tokens to all three differences; the *shape* of the fingerprint survives it.
    """
    offset = round(sum(got - want for got, want in zip(counts, reference)) / len(reference))
    return offset, max(abs(got - want - offset) / max(1, want) for got, want in zip(counts, reference))

def compare(counts: List[int], references: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """References ordered by closeness to the measured counts."""
    rows = [{"id": row["id"], "label": row["label"], "families": list(row["families"]),
             "source": row.get("source", "observed"), "counts": list(row["counts"]),
             "distance": round(distance(counts, row["counts"]), 4)}
            for row in (REFERENCES if references is None else references)]
    return sorted(rows, key=lambda row: row["distance"])

def assess(results: Dict[str, Dict[str, Any]], claimed: str,
           references: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The tokenizer verdict for the detector report.

    ``verdict``: ``match`` — the counts are those of the claimed family's
    tokenizer; ``mismatch`` — they are another family's; ``unknown`` — they fit
    no reference we have (a new tokenizer: says nothing either way);
    ``no_usage`` / ``failed`` / ``not_run`` — nothing was measured.
    """
    measured = measure(results)
    if measured["status"] != "measured":
        return {"verdict": measured["status"], **{k: v for k, v in measured.items() if k != "status"}}
    ranked = compare(measured["counts"], references)
    close = [row for row in ranked if row["distance"] <= MATCH_TOLERANCE]
    out: Dict[str, Any] = {"counts": measured["counts"], "base": measured["base"], "nearest": ranked[:3],
                           "matches": close, "families": sorted({family for row in close for family in row["families"]}),
                           "hidden_tokens": max(0, measured["base"] - BARE_REQUEST_TOKENS),

                           "strong": any(row["source"] != "observed" for row in close),
                           "stable": measured.get("stable", True), "shift": 0}
    pool = REFERENCES if references is None else references
    if pool and all(got < IMPLAUSIBLE_RATIO * min(row["counts"][index] for row in pool)
                    for index, got in enumerate(measured["counts"])):
        out["verdict"] = "implausible"
        out["claimed_reference"] = None
        return out
    own = [row for row in ranked if claimed and claimed in row["families"]]
    out["claimed_reference"] = own[0] if own else None
    jitter = int(measured.get("jitter") or 0)
    if not close:

        moved = []
        for row in ranked:
            offset, rest = shifted(measured["counts"], row["counts"])
            if rest <= SHIFT_TOLERANCE and 0 < abs(offset) <= MAX_SHIFT:
                moved.append({**row, "distance": round(rest, 4), "shift": offset})
            elif jitter and jitter <= MAX_SHIFT:
                rest = max(min(abs(got - want + step) for step in (-jitter, 0, jitter)) / max(1, want)
                           for got, want in zip(measured["counts"], row["counts"]))
                if rest <= SHIFT_TOLERANCE:
                    moved.append({**row, "distance": round(rest, 4), "shift": jitter})
        families = sorted({family for row in moved for family in row["families"]})

        if moved and len(families) == 1:
            close = sorted(moved, key=lambda row: row["distance"])
            out.update({"matches": close, "families": families, "strong": False, "shift": close[0]["shift"],
                        "hidden_tokens": max(0, measured["base"] + close[0]["shift"] - BARE_REQUEST_TOKENS)})
    if close:
        if not claimed:
            out["verdict"] = "identified"
        elif claimed in out["families"]:
            out["verdict"] = "match"
        else:
            out["verdict"] = "mismatch"
    elif (own and out["stable"] and own[0]["distance"] > MISMATCH_TOLERANCE
          and all(row["distance"] > MISMATCH_TOLERANCE for row in own)):

        out["verdict"] = "unlike_claimed"
    else:
        out["verdict"] = "unknown"
    return out
