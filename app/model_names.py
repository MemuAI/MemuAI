"""Round 33 (r33-models): one readable name for every model id — the same rules as ``pgstatic/memu-model-names.js``.

``reform-claude-opus-4-6-thinking``  → ``Claude Opus 4.6 Thinking``
``xkiro-qwen/qwen3.8-max:free``       → ``Qwen3.8 Max``  (+ badge ``free``)
``aws9-gpt-6.1-sol``                  → ``GPT-6.1 Sol``
``gpt://b1g…/yandexgpt-5.1/latest``   → ``YandexGPT 5.1``

The rules are generic, so names of models that do not exist yet come out right as well:

1. a name the user gave (``model_aliases``) always wins;
2. the account prefix is removed — the known one (``prefix=`` from Proxy) or an unknown first word in front of a known
   model family (``prefix-claude-…``); vendor paths (``openai/``, ``~anthropic/``, ``gpt://folder/…/latest``) too;
3. ``:free`` / ``:batch`` / ``:beta``… suffixes become badges, ``@2025…`` and date snapshots (``-20250514``,
   ``-2025-04-20``, ``-0902``) become a muted date tag;
4. versions ``4-6`` / ``4_6`` → ``4.6`` (sizes like ``3-70b`` stay);
5. vendor capitalisation (GPT, GLM, Qwen, DeepSeek, MiniMax…), sizes (70B, A3B), common words (Thinking, Flash, Mini…).

The JS file must give identical results: ``tests/round33_model_names_vectors.json`` is checked by both
(``tests/round33_models_test.py``). Change both files together.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

VERSION = "1.0-r33"

#: Lower-case token → display form. Brands keep their own spelling.
BRANDS: Dict[str, str] = {
    "gpt": "GPT", "chatgpt": "ChatGPT", "glm": "GLM", "qwen": "Qwen", "qwq": "QwQ", "qvq": "QVQ", "deepseek": "DeepSeek",
    "claude": "Claude", "gemini": "Gemini", "gemma": "Gemma", "grok": "Grok", "kimi": "Kimi", "moonshot": "Moonshot",
    "minimax": "MiniMax", "mistral": "Mistral", "mixtral": "Mixtral", "codestral": "Codestral", "devstral": "Devstral",
    "magistral": "Magistral", "ministral": "Ministral", "pixtral": "Pixtral", "llama": "Llama", "codellama": "CodeLlama",
    "phi": "Phi", "yi": "Yi", "command": "Command", "aya": "Aya", "nova": "Nova", "titan": "Titan", "sonar": "Sonar",
    "dall": "DALL", "imagen": "Imagen", "flux": "FLUX", "sdxl": "SDXL", "seedream": "Seedream", "seedance": "Seedance",
    "doubao": "Doubao", "ernie": "ERNIE", "hunyuan": "Hunyuan", "wanx": "Wanx", "wan": "Wan", "kling": "Kling",
    "veo": "Veo", "sora": "Sora", "hailuo": "Hailuo", "runway": "Runway", "ideogram": "Ideogram", "recraft": "Recraft",
    "midjourney": "Midjourney", "nemotron": "Nemotron", "granite": "Granite", "hermes": "Hermes", "olmo": "OLMo",
    "falcon": "Falcon", "internlm": "InternLM", "internvl": "InternVL", "baichuan": "Baichuan", "yandexgpt": "YandexGPT",
    "gigachat": "GigaChat", "jamba": "Jamba", "lfm": "LFM", "cogview": "CogView", "cogvideo": "CogVideo",
    "cogvideox": "CogVideoX", "whisper": "Whisper", "suno": "Suno", "udio": "Udio", "lyria": "Lyria", "kandinsky": "Kandinsky",
    "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable", "mythos": "Mythos", "codex": "Codex",
    "llava": "LLaVA", "moondream": "Moondream", "smollm": "SmolLM", "starcoder": "StarCoder", "dbrx": "DBRX",
    "palm": "PaLM", "bard": "Bard", "copilot": "Copilot", "perplexity": "Perplexity", "step": "Step", "spark": "Spark",
    "openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "xai": "xAI", "meta": "Meta", "ai": "AI",
    "api": "API", "oss": "OSS", "vl": "VL", "tts": "TTS", "stt": "STT", "asr": "ASR", "ocr": "OCR", "hd": "HD",
    "xl": "XL", "xxl": "XXL", "sd": "SD", "it": "IT", "moe": "MoE", "gguf": "GGUF", "awq": "AWQ", "gptq": "GPTQ",
    "fp8": "FP8", "fp16": "FP16", "bf16": "BF16", "int4": "INT4", "int8": "INT8", "mlx": "MLX", "rl": "RL", "ft": "FT",
    "t2i": "T2I", "t2v": "T2V", "i2v": "I2V", "i2i": "I2I", "r1": "R1", "rp": "RP", "nano-banana": "Nano Banana",
    "dall-e": "DALL·E", "gpt-oss": "GPT-OSS", "4o": "4o", "ui-tars": "UI-TARS", "apodex": "Apodex", "ollama": "Ollama",
    "auto": "Auto", "genspark": "Genspark", "kiro": "Kiro", "antigravity": "Antigravity",
}

#: Known model families: a word in front of one of them is an account prefix («reform-glm-5.3» → «glm-5.3»).
FAMILIES = (
    "claude", "gpt", "chatgpt", "o1", "o3", "o4", "o5", "gemini", "gemma", "grok", "llama", "codellama", "mistral",
    "mixtral", "codestral", "devstral", "magistral", "ministral", "pixtral", "qwen", "qwq", "qvq", "deepseek", "kimi",
    "moonshot", "glm", "minimax", "abab", "yi", "phi", "command", "aya", "nova", "titan", "sonar", "dall", "imagen",
    "flux", "sdxl", "stable", "seedream", "seedance", "doubao", "ernie", "hunyuan", "wanx", "wan", "kling", "veo", "sora",
    "hailuo", "runway", "ideogram", "recraft", "midjourney", "nemotron", "granite", "hermes", "olmo", "falcon", "internlm",
    "internvl", "baichuan", "yandexgpt", "gigachat", "jamba", "lfm", "cogview", "cogvideo", "cogvideox", "whisper", "tts",
    "suno", "udio", "lyria", "kandinsky", "nano", "fable", "mythos", "opus", "sonnet", "haiku", "codex", "llava",
    "moondream", "smollm", "starcoder", "dbrx", "palm", "text", "embedding", "ui", "step", "spark", "apodex", "auto",
    "chat", "playground", "hidream", "kolors", "qwen2", "qwen3", "llama3", "llama4", "gemma2", "gemma3",
)
_FAMILY_RE = re.compile(r"^(?:" + "|".join(sorted({re.escape(f) for f in FAMILIES}, key=len, reverse=True))
                        + r")(?:[-\d.:_/]|$)")

#: Brands written with a hyphen before their version («GPT-5.1», «GLM-5.3», «Phi-4»); Qwen glues it («Qwen3.8»).
HYPHEN_BRANDS = {"gpt", "glm", "phi", "chatgpt", "gpt-oss"}
GLUE_BRANDS = {"qwen", "qwq", "qvq"}

#: Variant suffixes after ``:`` that are badges, not part of the name.
SUFFIX_BADGES = {"free": "free", "batch": "batch", "beta": "beta", "extended": "extended", "nitro": "nitro",
                 "floor": "floor", "online": "online", "exacto": "exacto"}
#: Suffixes after ``:`` that are part of the name («:thinking» → «Thinking»).
SUFFIX_WORDS = {"thinking", "reasoning", "high", "low", "medium", "minimal", "max", "vision", "fast"}

_DATE_FULL = re.compile(r"(?:^|[-_.])((?:19|20)\d{2})-?(0[1-9]|1[0-2])-?([0-2]\d|3[01])$")
_DATE_SHORT = re.compile(r"(?:^|[-_.])(0[1-9]|1[0-2])([0-2]\d|3[01])$")
_DATE_MY = re.compile(r"(?:^|[-_.])(0[1-9]|1[0-2])-((?:19|20)\d{2})$")
_DATE_YYMMDD = re.compile(r"(?:^|[-_.])(2\d)(0[1-9]|1[0-2])([0-2]\d|3[01])$")
_DATE_YM = re.compile(r"(?:^|[-_.])((?:19|20)\d{2})-(0[1-9]|1[0-2])$")
_VERSION_DASH = re.compile(r"(?<=\d)[-_](?=\d(?:[^0-9bmk]|$))")
_SIZE = re.compile(r"^(?:(\d+)x)?(\d+(?:\.\d+)?)([bmkt])$")
_ACTIVE = re.compile(r"^([ae])(\d+(?:\.\d+)?)b$")
_QUANT = re.compile(r"(?<![a-z0-9])i?q\d(?:_k)?(?:_[a-z0-9]{1,2})?(?![a-z0-9])")
_LETTER_VER = re.compile(r"^([a-z])(\d+(?:\.\d+)*)([a-z]?)$")
_BRAND_VER = re.compile(r"^([a-z]+)(\.?)(\d+(?:\.\d+)*)([a-z]*)$")


def _strip_account(text: str, prefix: str) -> str:
    """Remove the Proxy account prefix (``reform-``) or a vendor path (``openai/``, ``gpt://folder/…/latest``)."""
    if prefix:
        p = prefix.lower()
        if text.lower().startswith(p + "-") and len(text) > len(p) + 1:
            text = text[len(p) + 1:]
        elif text.lower().startswith(p + "/") and len(text) > len(p) + 1:
            text = text[len(p) + 1:]
    text = text.lstrip("~")
    if "://" in text:                                  # gpt://folder/model/latest (Yandex)
        text = text.split("://", 1)[1]
        parts = [p for p in text.split("/") if p]
        while len(parts) > 1 and re.fullmatch(r"latest|rc|deprecated|\d{8}", parts[-1], re.I):
            parts.pop()
        text = parts[-1] if parts else text
    if "/" in text:
        parts = [p for p in text.split("/") if p]
        while len(parts) > 1 and re.fullmatch(r"latest|rc|deprecated", parts[-1], re.I):
            parts.pop()
        head = parts[0] if parts else ""
        text = parts[-1] if parts else text
        # «xkiro-qwen/qwen3.8-max» — the vendor path already removed the account; «genspark-image/auto» keeps the kind.
        if head.lower().endswith("-image") and text.lower() == "auto":
            text = "auto"
    if not prefix:
        low = text.lower()
        first, _, rest = low.partition("-")
        if rest and not _FAMILY_RE.match(first) and _FAMILY_RE.match(rest):
            text = text[len(first) + 1:]
    return text


def _token(word: str, index: int, prev: str) -> str:
    low = word.lower()
    if low in BRANDS:
        return BRANDS[low]
    size = _SIZE.match(low)
    if size:
        mult, num, unit = size.groups()
        return (f"{mult}x" if mult else "") + num + unit.upper()
    active = _ACTIVE.match(low)
    if active:
        return active.group(1).upper() + active.group(2) + "B"
    if re.fullmatch(r"\d+(?:\.\d+)*[a-z]?", low):            # 4.6, 4o, 3
        return low
    if re.fullmatch(r"o\d+", low) and index == 0:             # OpenAI o3 / o4
        return low
    letter = _LETTER_VER.match(low)
    if letter and letter.group(1) in "vkmrqs":                # v4.1, k2, m2, r1
        return letter.group(1).upper() + letter.group(2) + letter.group(3)
    brand = _BRAND_VER.match(low)
    if brand and brand.group(1) in BRANDS:                    # qwen3.8, gemma3, llama3.1, wanx2.1
        name, dot, ver, tail = BRANDS[brand.group(1)], brand.group(2), brand.group(3), brand.group(4)
        if dot:                                               # FLUX.2
            return name + "." + ver + tail
        if brand.group(1) in GLUE_BRANDS:
            return name + ver + tail
        if brand.group(1) in HYPHEN_BRANDS:
            return name + "-" + ver + tail
        return name + " " + ver + tail
    if re.fullmatch(r"[a-z]\d", low):
        return low.upper()
    return word[:1].upper() + word[1:] if word[:1].isalpha() else word


@lru_cache(maxsize=16384)
def _parse(model_id: str, prefix: str) -> Dict[str, Any]:
    raw = str(model_id or "").strip()
    text = _strip_account(raw, prefix)
    badges: List[str] = []
    words_after: List[str] = []
    # «:free», «:thinking», «@20250514»
    if "@" in text:
        text, _, _at = text.partition("@")
    while True:
        m = re.search(r":([a-z0-9-]+)$", text, re.I)
        if not m:
            break
        tag = m.group(1).lower()
        text = text[:m.start()]
        if tag in SUFFIX_BADGES:
            badges.insert(0, SUFFIX_BADGES[tag])
        elif tag in SUFFIX_WORDS:
            words_after.insert(0, tag)
        elif re.fullmatch(r"\d+(?:\.\d+)?[bmk]?", tag):   # ollama «llama3.1:8b»
            words_after.insert(0, tag)
        elif tag == "latest":
            continue
        else:
            words_after.insert(0, tag)
    for ext in (".gguf", ".bin", ".safetensors"):
        if text.lower().endswith(ext):
            text = text[: -len(ext)]
    date = ""
    for _ in range(2):
        m = _DATE_FULL.search(text)
        if m and m.start() > 0:
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            text = text[:m.start()]
            continue
        m = _DATE_YM.search(text)
        if m and m.start() > 0:
            date = f"{m.group(1)}-{m.group(2)}"
            text = text[:m.start()]
            continue
        m = _DATE_MY.search(text)
        if m and m.start() > 0:
            date = f"{m.group(2)}-{m.group(1)}"
            text = text[:m.start()]
            continue
        m = _DATE_YYMMDD.search(text)
        if m and m.start() > 0:
            date = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
            text = text[:m.start()]
            continue
        m = _DATE_SHORT.search(text)
        if m and m.start() > 0 and not re.search(r"(?:^|[-_.])k\d$", text[:m.start()].lower()):
            date = f"{m.group(1)}-{m.group(2)}"
            text = text[:m.start()]
            continue
        break
    text = _VERSION_DASH.sub(".", text)
    low = text.lower()
    # Multi-word brands first: «dall-e», «gpt-oss», «nano-banana», «ui-tars».
    for multi in ("dall-e", "gpt-oss", "nano-banana", "ui-tars"):
        low = re.sub(r"(?<![a-z0-9])" + re.escape(multi) + r"(?![a-z])", multi.replace("-", "\u2060"), low)
    # GGUF quantisation «q4_k_m» / «iq3_xs» stays one upper-case token.
    low = _QUANT.sub(lambda m: m.group(0).replace("_", "\u2061"), low)
    tokens = [t for t in re.split(r"[-_\s]+", low) if t]
    tokens = [t.replace("\u2061", "_").upper() if "\u2061" in t or _QUANT.fullmatch(t) else t for t in tokens]
    tokens = [t.replace("\u2060", "-") for t in tokens]
    tokens += words_after
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        shown = _token(tok, i, tokens[i - 1] if i else "")
        if tok in HYPHEN_BRANDS and nxt and re.fullmatch(r"\d+(?:\.\d+)*[a-z]{0,2}", nxt) and not _SIZE.match(nxt):
            shown = shown + "-" + (nxt[:-1] + "V" if nxt.endswith("v") else nxt)
            i += 1
        elif tok in GLUE_BRANDS and nxt and re.fullmatch(r"\d+(?:\.\d+)*", nxt):
            shown = shown + nxt
            i += 1
        out.append(shown)
        i += 1
    name = " ".join(out).strip() or raw
    return {"name": name, "base": text, "badges": badges, "date": date}


def parse(model_id: str, *, prefix: str = "") -> Dict[str, Any]:
    """``{name, base, badges, date}`` for one model id (see the module docstring)."""
    found = _parse(str(model_id or ""), str(prefix or "").strip().lower())
    return {"name": found["name"], "base": found["base"], "badges": list(found["badges"]), "date": found["date"]}


def pretty(model_id: str, *, prefix: str = "", aliases: Optional[Dict[str, str]] = None) -> str:
    """The readable name; a user alias wins."""
    alias = (aliases or {}).get(str(model_id or "")) if aliases else None
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    return parse(model_id, prefix=prefix)["name"]


def label(model_id: str, *, prefix: str = "", aliases: Optional[Dict[str, str]] = None, full: bool = True) -> str:
    """One-line label for plain-text places (Telegram buttons, the CLI): ``Claude Opus 4.6 · reform-claude-opus-4-6``."""
    name = pretty(model_id, prefix=prefix, aliases=aliases)
    if not full or name == model_id:
        return name
    return f"{name} · {model_id}"


def names_for(ids: Iterable[str], *, prefixes: Optional[Dict[str, str]] = None,
              aliases: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    prefixes = prefixes or {}
    return {i: pretty(i, prefix=prefixes.get(i, ""), aliases=aliases) for i in ids}
