"""Round 40: «Сложность задачи» — a cheap difficulty classifier for the composite-model router.

:func:`classify` looks only at the request text (heuristics, RU + EN, no network, microseconds) and returns one of
:data:`LEVELS` plus a confidence. Composite models use it as the ``difficulty`` condition: simple / translation /
summary questions go to fast unlimited models, complex code, deep maths and architecture to flagships (whose quotas the
quota planner then spends carefully through the «least used» strategy).

When a composite model names a ``difficulty_model`` and the heuristics are unsure, :func:`refine` asks that (fast)
model once with a one-word prompt; the answer is cached by the text's hash, so a conversation is classified once.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.difficulty")

LEVELS = ("simple", "chat", "translation", "summary", "creative", "code", "complex_code", "deep_math", "architecture")

HARD = ("complex_code", "deep_math", "architecture")

_R = lambda pattern: re.compile(pattern, re.I | re.S)  # noqa: E731 — a table of patterns reads better this way

_TRANSLATE = _R(r"\b(переведи|перевод[а-я]*|translate|translation|на (английский|русский|немецкий|французский|"
                r"испанский|китайский|японский)|into (english|russian|german|french|spanish|chinese|japanese))\b")
_SUMMARY = _R(r"\b(кратко перескажи|перескажи|резюмир[а-я]*|сокращ[а-я]*|выжимк[а-я]*|конспект[а-я]*|тезис[а-я]*|"
              r"summari[sz]e|summary|tl;?dr|key points|in short)\b")
_CREATIVE = _R(r"\b(стих[а-я]*|рассказ[а-я]*|сказк[а-я]*|сочини|придумай|слоган[а-я]*|поздравлени[а-я]*|"
               r"poem|story|lyrics|slogan|write a song|brainstorm)\b")
_CODE = _R(r"```|\b(def |class |function\s|import |#include|console\.log|SELECT .+ FROM|public static|"
           r"код[а-я]*|скрипт[а-я]*|функци[юя]|python|javascript|typescript|rust|golang|c\+\+|sql|regex|bash|"
           r"bug|debug|stack ?trace|traceback|exception|compile|refactor|unit tests?|api endpoint)\b")
_COMPLEX_CODE = _R(r"\b(рефактор[а-я]*|оптимизир[а-я]*|многопоточн[а-я]*|конкурентн[а-я]*|race condition|deadlock|"
                   r"утечк[а-я]* памяти|memory leak|concurren[a-z]*|async(io)? .*(bug|hang)|"
                   r"производительност[а-я]*|performance|profil[a-z]*|алгоритм[а-я]*|algorithm|complexity|"
                   r"O\(n|segfault|undefined behavio|lock-free|compiler|parser|interpreter|"
                   r"весь (проект|репозитори)|whole (repo|codebase)|migrat[a-z]+ .*(codebase|project))\b")
_MATH = _R(r"\b(докажи|доказательств[а-я]*|теорем[а-я]*|лемм[а-я]*|интеграл[а-я]*|производн[а-я]*|"
           r"дифференциальн[а-я]*|матриц[а-я]*|собственн[а-я]* значени|вероятност[а-я]*|комбинаторик[а-я]*|"
           r"prove|proof|theorem|lemma|integral|derivative|eigen[a-z]*|differential equation|probability|"
           r"combinatori[a-z]*|olympiad|олимпиад[а-я]*|topolog[a-z]*|тополог[а-я]*)\b|∫|∑|∂|√|≤|≥|\\frac|\\int|"
           r"\^\d|\b\d+\s*[\^*/]\s*\d+")
_ARCH = _R(r"\b(архитектур[а-я]*|спроектируй|проектирован[а-я]*|масштабир[а-я]*|микросервис[а-я]*|"
           r"высоконагруж[а-я]*|отказоустойчив[а-я]*|system design|architecture|architect|scalab[a-z]*|"
           r"microservice[s]?|distributed|sharding|шардирован[а-я]*|trade-?offs?|design doc|rfc)\b")
_HARD_HINTS = _R(r"\b(пошагово|подробно|глубок[а-я]*|тщательно|step by step|in depth|thorough|rigorous[a-z]*|"
                 r"edge cases?|крайние случаи)\b")
_GREETING = _R(r"^\s*(привет|здравствуй[а-я]*|добр[а-я]+ (утро|день|вечер)|спасибо|ок|окей|hi|hello|hey|thanks|"
               r"thank you|ok|okay|yo)[\s!.,)?]*$")

def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""

def classify_text(text: str, *, tools: bool = False) -> Tuple[str, float]:
    """``(level, confidence 0..1)`` from the text alone."""
    text = (text or "")[-12000:]
    stripped = text.strip()
    if not stripped:
        return "simple", 0.5
    if _GREETING.match(stripped):
        return "chat", 0.95
    words = len(stripped.split())
    code_blocks = stripped.count("```") // 2
    score: Dict[str, float] = {}

    def add(level: str, value: float) -> None:
        score[level] = score.get(level, 0.0) + value

    if _TRANSLATE.search(stripped[:400]):
        add("translation", 2.2)
    if _SUMMARY.search(stripped[:600]):
        add("summary", 2.0)
    if _CREATIVE.search(stripped[:400]):
        add("creative", 1.6)
    code_hits = len(_CODE.findall(stripped[:6000]))
    if code_hits:
        add("code", 0.8 + 0.4 * min(code_hits, 5) + 0.6 * code_blocks)
    complex_hits = len(_COMPLEX_CODE.findall(stripped[:6000]))
    if complex_hits:
        add("complex_code", 1.0 + 0.9 * min(complex_hits, 4) + (0.8 if code_hits else 0.0))
    math_hits = len(_MATH.findall(stripped[:6000]))
    if math_hits:
        add("deep_math", 0.9 + 0.8 * min(math_hits, 5))
    arch_hits = len(_ARCH.findall(stripped[:6000]))
    if arch_hits:
        add("architecture", 1.2 + 1.0 * min(arch_hits, 4))
    hard_hint = bool(_HARD_HINTS.search(stripped[:2000]))

    if code_blocks >= 2 or (code_hits and words > 600):
        add("complex_code", 1.2)
    if hard_hint:
        for level in HARD:
            if level in score:
                score[level] += 0.8
    if tools and "code" in score:
        score["code"] += 0.4
    if not score:
        if words <= 25:
            return "simple", 0.75 if words <= 12 else 0.55
        return ("chat", 0.5) if words <= 200 else ("summary", 0.35) if words > 1500 else ("chat", 0.4)

    if "translation" in score and score["translation"] >= max(score.get(k, 0) for k in HARD + ("code",)):
        score["translation"] += 1.0
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    best, value = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = max(0.3, min(0.95, 0.45 + (value - second) * 0.18 + min(value, 4) * 0.06))
    if best == "code" and words <= 30 and not code_blocks:
        confidence = min(confidence, 0.6)
    return best, round(confidence, 2)

def classify_messages(messages: List[Dict[str, Any]], *, tools: bool = False) -> Tuple[str, float]:
    """The difficulty of the newest user turn (with a little of the previous one for «а теперь …» follow-ups)."""
    users = [_text_of(m.get("content")) for m in messages or [] if isinstance(m, dict) and m.get("role") == "user"]
    if not users:
        return "simple", 0.5
    last = users[-1]
    level, conf = classify_text(last, tools=tools)
    if conf < 0.6 and len(users) > 1 and len(last.split()) < 30:
        prev, prev_conf = classify_text(users[-2][-3000:] + "\n" + last, tools=tools)
        if prev in HARD and prev_conf >= conf:
            return prev, round(min(prev_conf, 0.7), 2)
    return level, conf

_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 500
PROMPT = ("Classify the difficulty of the user's task. Answer with ONE word from this list and nothing else: "
          + ", ".join(LEVELS) + ".\n- simple: a short factual question or small talk\n"
          "- complex_code: non-trivial programming (algorithms, concurrency, big refactors, performance)\n"
          "- deep_math: proofs, advanced maths\n- architecture: system design, scaling, trade-offs\n\nTask:\n")

def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\u0000{text[-4000:]}".encode("utf-8", "replace")).hexdigest()[:24]

def cached(model: str, text: str) -> Optional[str]:
    return _CACHE.get(_key(model, text))

async def refine(service: Any, model: str, messages: List[Dict[str, Any]], heuristic: Tuple[str, float],
                 *, timeout: float = 4.0, params: Optional[Dict[str, Any]] = None) -> Tuple[str, float, str]:
    """``(level, confidence, source)``; ``source`` is ``heuristic`` / ``cache`` / ``model``. Never raises."""
    level, conf = heuristic
    if not model or conf >= 0.7:
        return level, conf, "heuristic"
    users = [_text_of(m.get("content")) for m in messages or [] if isinstance(m, dict) and m.get("role") == "user"]
    text = (users[-1] if users else "")[-4000:]
    if not text.strip():
        return level, conf, "heuristic"
    key = _key(model, text)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key], 0.85, "cache"
    call_params = {"_stream": False, "max_tokens": 8, "temperature": 0, "effort": "off",
                   **({k: v for k, v in (params or {}).items() if k == "_composite_chain"})}
    answer = ""

    async def ask() -> None:
        nonlocal answer
        async for chunk in service.stream_completion(model, [{"role": "user", "content": PROMPT + text}],
                                                     call_params):
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if choices:
                answer += str((choices[0].get("delta") or {}).get("content") or "")
            if len(answer) > 60:
                break

    try:
        await asyncio.wait_for(ask(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — the classifier is optional; heuristics stay
        log.debug("difficulty model %s failed: %s", model, exc)
        return level, conf, "heuristic"
    word = re.sub(r"[^a-z_]", "", answer.strip().lower().split()[0] if answer.strip() else "")
    if word not in LEVELS:
        return level, conf, "heuristic"
    _CACHE[key] = word
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return word, 0.85, "model"
