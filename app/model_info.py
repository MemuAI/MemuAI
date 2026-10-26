"""What the Proxy knows about a model: its context window and output limit.

MemuAI needs the context window of every model to compress long chats at the
right moment. Providers name the same model in many ways
(``prefix-claude-opus-5.5``, ``anthropic/claude-opus-5-5``,
``claude-opus-5-5-20260901``, ``Claude Opus 5.5 (thinking)``), so the lookup
first reduces a name to a canonical form and then asks, in order:

1. **the provider itself** — numbers recorded while listing models
   (Antigravity ``maxTokens``, OpenRouter ``context_length``, Gemini
   ``inputTokenLimit``, vLLM ``max_model_len`` and similar fields);
2. **the local runtime** — the context a llama-server model is started with;
3. **the catalogue below** — published limits of well-known models;
4. **the family** — a conservative default for the model family;
5. nothing — MemuAI then falls back to the user's default.

Every answer carries its ``source`` so the interface can say where a number
came from. Nothing here makes network requests: provider numbers are recorded
by the adapters while they already talk to the provider.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import DATA_DIR

from .model_contract import canonical, _canonical

KNOWN: List[Tuple[str, int, Optional[int], str]] = [

    (r"(?:mythos|fable)-?5\.1", 1_000_000, 128_000, "Claude Fable 5.1"),
    (r"(?:mythos|fable)-?5(?![.\d])", 1_000_000, 128_000, "Claude Fable 5"),
    (r"opus-?5\.5", 1_000_000, 128_000, "Claude Opus 5.5"),
    (r"opus-?5(?![.\d])", 1_000_000, 128_000, "Claude Opus 5"),
    (r"sonnet-?5(?![.\d])", 1_000_000, 128_000, "Claude Sonnet 5"),
    (r"opus-?4\.[678]", 1_000_000, 128_000, "Claude Opus 4.6–4.8"),
    (r"sonnet-?4\.6", 1_000_000, 128_000, "Claude Sonnet 4.6"),
    (r"haiku-?4\.5|4\.5-?haiku", 200_000, 64_000, "Claude Haiku 4.5"),
    (r"sonnet-?4\.5", 200_000, 64_000, "Claude Sonnet 4.5"),
    (r"opus-?4\.5", 200_000, 64_000, "Claude Opus 4.5"),
    (r"opus-?4(?:\.1)?(?![.\d])", 200_000, 32_000, "Claude Opus 4 / 4.1"),
    (r"sonnet-?4(?![.\d])", 200_000, 64_000, "Claude Sonnet 4"),
    (r"claude-?3\.7|3\.7-?sonnet", 200_000, 64_000, "Claude Sonnet 3.7"),
    (r"claude-?3\.5|3\.5-?(?:sonnet|haiku)", 200_000, 8_192, "Claude 3.5"),
    (r"claude-?3", 200_000, 4_096, "Claude 3"),

    (r"gpt-?5(?:\.\d)?-?chat|chatgpt", 128_000, 16_384, "ChatGPT (chat-latest)"),
    (r"gpt-?(?:6|5\.[4-9])(?!-?(?:mini|nano))", 1_050_000, 128_000, "GPT-5.4+ / GPT-6"),
    (r"gpt-?5", 400_000, 128_000, "GPT-5"),
    (r"gpt-?4\.5", 128_000, 16_384, "GPT-4.5"),
    (r"gpt-?4\.1", 1_047_576, 32_768, "GPT-4.1"),
    (r"gpt-?4o", 128_000, 16_384, "GPT-4o"),
    (r"gpt-?4-?(?:turbo|1106|0125)", 128_000, 4_096, "GPT-4 Turbo"),
    (r"gpt-?4(?![.\do])", 8_192, 8_192, "GPT-4"),
    (r"gpt-?3\.5", 16_385, 4_096, "GPT-3.5 Turbo"),
    (r"gpt-?oss", 131_072, 131_072, "gpt-oss"),
    (r"(?<![a-z0-9])o[134](?:-?(?:mini|pro))?(?![a-z0-9])", 200_000, 100_000, "OpenAI o-series"),

    (r"gemini-?1\.5-?pro", 2_097_152, 8_192, "Gemini 1.5 Pro"),
    (r"gemini-?1\.5", 1_048_576, 8_192, "Gemini 1.5"),
    (r"gemini-?(?:2|3)(?:\.\d)?.*image", 32_768, 32_768, "Gemini Image"),
    (r"gemini-?(?:2|3)|gemini-?pro-?agent", 1_048_576, 65_536, "Gemini 2–3"),
    (r"gemini-?pro", 32_760, 8_192, "Gemini 1.0 Pro"),
    (r"gemma-?3", 131_072, 8_192, "Gemma 3"),
    (r"gemma-?2", 8_192, 8_192, "Gemma 2"),

    (r"grok-?4\.(?:[5-9]|1\d)", 500_000, None, "Grok 4.5+"),
    (r"grok-?4\.(?:2|3)", 1_000_000, None, "Grok 4.20 / 4.3"),
    (r"grok-?4\.1.*fast", 131_072, None, "Grok 4.1 Fast"),
    (r"grok-?(?:4|5)", 256_000, None, "Grok 4"),
    (r"grok-?(?:2|3)", 131_072, None, "Grok 2–3"),
    (r"grok-?code", 256_000, None, "Grok Code"),

    (r"deepseek-?v4", 1_048_576, None, "DeepSeek V4"),
    (r"deepseek-?(?:v3\.[1-9]|r1-?0528)", 163_840, 65_536, "DeepSeek V3.1+"),
    (r"deepseek-?r1", 64_000, 32_768, "DeepSeek R1"),
    (r"deepseek", 128_000, 8_192, "DeepSeek"),
    (r"qwen-?3\.[6-9]-?(?:plus|max)", 1_000_000, 65_536, "Qwen 3.6+ Plus/Max"),
    (r"qwen-?3.*coder-?480b", 1_000_000, 65_536, "Qwen3 Coder 480B"),
    (r"qwen-?3\.[5-9]|qwen-?3.*(?:max|next|coder|2507|vl)", 262_144, 65_536, "Qwen3 (256K)"),
    (r"qwen-?3", 131_072, 32_768, "Qwen3"),
    (r"qwen-?2\.5.*1m", 1_010_000, 8_192, "Qwen2.5 1M"),
    (r"qwen-?2\.5-?coder", 32_768, 8_192, "Qwen2.5 Coder"),
    (r"qwen-?2\.5|qwen-?plus", 131_072, 8_192, "Qwen2.5"),
    (r"qwq", 131_072, 32_768, "QwQ"),
    (r"kimi-?k2\.\d|kimi-?k2-?0905|kimi-?k3", 262_144, 32_768, "Kimi K2.5+"),
    (r"kimi-?k2", 131_072, 32_768, "Kimi K2"),
    (r"glm-?5\.[2-9]", 1_000_000, 128_000, "GLM-5.2+"),
    (r"glm-?(?:4\.[6-9]|5)", 200_000, 128_000, "GLM-4.6 / 5"),
    (r"glm-?4\.5", 131_072, 96_000, "GLM-4.5"),
    (r"glm", 128_000, None, "GLM"),
    (r"minimax-?m1", 1_000_000, 40_000, "MiniMax M1"),
    (r"minimax-?m3", 524_288, None, "MiniMax M3"),
    (r"minimax", 204_800, None, "MiniMax M2"),
    (r"muse-?spark", 1_000_000, None, "Meta Muse Spark"),
    (r"llama-?4", 131_072, None, "Llama 4"),
    (r"llama-?3\.[123]", 131_072, None, "Llama 3.1–3.3"),
    (r"llama-?3", 8_192, None, "Llama 3"),
    (r"llama-?2", 4_096, None, "Llama 2"),
    (r"codestral", 256_000, None, "Codestral"),
    (r"mistral-?medium-?3\.5", 262_144, None, "Mistral Medium 3.5"),
    (r"devstral|magistral|mistral-?(?:large|medium)|pixtral", 131_072, None, "Mistral"),
    (r"mistral-?small|ministral|mixtral|mistral-?7b", 32_768, None, "Mistral Small"),
    (r"command-?a", 256_000, 8_000, "Command A"),
    (r"command-?r", 128_000, 4_000, "Command R"),
    (r"phi-?4", 16_384, None, "Phi-4"),
]
_KNOWN = [(re.compile(pattern), context, output, label) for pattern, context, output, label in KNOWN]

FAMILY_DEFAULTS: List[Tuple[str, int]] = [
    (r"claude|opus|sonnet|haiku|fable|mythos", 200_000),
    (r"gemini", 1_048_576),
    (r"gpt|chatgpt|(?<![a-z])o\d", 128_000),
    (r"grok", 131_072),
    (r"qwen|deepseek|kimi|glm|llama|mistral|minimax", 128_000),
]
_FAMILY = [(re.compile(pattern), context) for pattern, context in FAMILY_DEFAULTS]

_NOT_CHAT = re.compile(r"(?<![a-z0-9])(?:embed\w*|whisper|tts|rerank\w*|moderation|dall-?e|gpt-image|imagen|flux|"
                       r"sdxl|stable-diffusion|seedream|kolors|recraft|ideogram|nano-banana)(?![a-z])|-image(?:-|$)")
_SEES = re.compile(
    r"vision|visual|(?<![a-z0-9])vl(?![a-z])|[-.]vl[-.\d]|omni|multimodal|llava|moondream|minicpm-v|internvl|pixtral|"
    r"cogvlm|qvq|paligemma|molmo|smolvlm|idefics|florence|ovis|fuyu|janus|step-1v|glm-[45]\.?\d?v|kimi-vl|"
    r"deepseek-ocr|doubao-seed|ui-tars|llama-4|gemma-?[34](?!-1b)|(?<![a-z0-9])command-a-vision|aya-vision|"

    r"(?<![a-z0-9])qwen3\.[5-9]-\d+b")
_BLIND = re.compile(
    r"(?<![a-z0-9])(?:gpt-3|text-davinci|babbage|davinci|"
    r"gpt-4(?:-\d{4}|-32k)?$|o1-(?:mini|preview)|o3-mini|gpt-oss|claude-(?:instant|2)|gemini-1\.0|gemini-pro$|"
    r"deepseek-(?:chat|coder|reasoner|r1|v[23])|deepseek$|qwen\d*(?:\.\d+)?-?(?:coder|max|plus|turbo|long|chat|\d+b)?(?![a-z])|"
    r"qwq|kimi-k2(?!\.[5-9])|glm-4\.[5-9](?!v)|glm-[5-9](?!v)|minimax-m|"
    r"llama-?3(?:\.[013])?-|llama-?3\.2-(?:1|3)b|llama-2|mistral-(?:7b|nemo|small-(?:2|3\.0))|mixtral|codestral|devstral|"
    r"magistral|phi-[34]|grok-[123](?![\d.]*-?vision)|grok-code|command-(?!a-vision)|yi-|nova-micro|sonar|hermes|dolphin|"
    r"wizardlm|nemotron|granite|starcoder|codellama|wan-|zephyr|openchat)")
_FAMILY_SEES = re.compile(
    r"(?<![a-z0-9])(?:claude|fable|mythos|gemini|gpt-4o|gpt-4\.1|gpt-4\.5|gpt-4-turbo|gpt-5|gpt-6|chatgpt-4o|o1(?:-pro)?$|"
    r"o3(?!-mini)|o4|grok-4|mistral-(?:medium|large)-3|mistral-small-3\.[1-9]|kimi-k2\.[5-9]|kimi-k3|nova-(?:lite|pro|premier)|"
    r"sonnet|opus|haiku)")

_RAW_SEES = re.compile(r"(?<![a-z0-9])qwen-?3\.[5-9]-(?:plus|flash|omni|vl)(?![a-z])|(?<![a-z0-9])qwen-?3\.(?:[89]|\d{2})-max(?![a-z])"
                       r"|(?<![a-z0-9])deepseek-v4\.[1-9]-flash(?![a-z])|(?<![a-z0-9])glm-?5\.[3-9]-flash")

def vision_hint(model: str) -> Optional[bool]:
    """Does a model of this name accept pictures? ``True`` / ``False`` from the family it belongs to, ``None`` when
    the name says nothing (an unknown fine-tune, a private deployment)."""
    raw = str(model or "").lower()

    raw = re.sub(r"(?<=\d)[-_](?=\d(?:[^0-9bm]|$))", ".", raw)
    if _RAW_SEES.search(raw) and not _NOT_CHAT.search(raw):
        return True
    return _vision_hint(canonical(model))

@lru_cache(maxsize=8192)
def _vision_hint(name: str) -> Optional[bool]:
    if not name:
        return None
    if _NOT_CHAT.search(name):
        return False
    if _SEES.search(name):
        return True
    if _BLIND.search(name):
        return False
    if _FAMILY_SEES.search(name):
        return True
    return None

def lookup_known(model: str) -> Optional[Dict[str, Any]]:
    found = _lookup_known_cached(canonical(model))
    return dict(found) if found is not None else None

@lru_cache(maxsize=8192)
def _lookup_known_cached(name: str) -> Optional[Tuple[Tuple[str, Any], ...]]:
    for pattern, context, output, label in _KNOWN:
        if pattern.search(name):
            return tuple({"context": context, "output": output, "label": label, "source": "catalog"}.items())
    for pattern, context in _FAMILY:
        if pattern.search(name):
            return tuple({"context": context, "output": None, "label": "", "source": "family"}.items())
    return None

_lock = threading.Lock()
_learned: Dict[str, Dict[str, Any]] = {}
_loaded = False
_save_timer: Optional[threading.Timer] = None

CONTEXT_FIELDS = ("context_length", "context_window", "max_context_length", "max_model_len",
                  "max_input_tokens", "input_token_limit", "inputTokenLimit", "maxTokens",
                  "max_seq_len", "contextLength", "context_size", "n_ctx")
OUTPUT_FIELDS = ("max_output_tokens", "output_token_limit", "outputTokenLimit", "maxOutputTokens",
                 "max_completion_tokens", "max_tokens")

def _path() -> str:
    return os.path.join(DATA_DIR, "model-info.json")

def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_path(), encoding="utf-8") as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            _learned.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
    except (OSError, ValueError):
        pass

def _key(prefix: str, model: str) -> str:
    return f"{str(prefix or '').lower()}::{str(model or '')}"

def _as_tokens(value: Any) -> Optional[int]:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 1_000 <= number <= 100_000_000 else None

def extract_limits(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Context/output numbers from one model-list row of any common shape."""
    if not isinstance(row, dict):
        return None, None
    sources = [row]
    for nested in ("top_provider", "limits", "metadata", "meta", "capabilities"):
        if isinstance(row.get(nested), dict):
            sources.append(row[nested])
    context = output = None
    for source in sources:
        for field in CONTEXT_FIELDS:
            if context is None and field in source:
                context = _as_tokens(source.get(field))
        for field in OUTPUT_FIELDS:
            if output is None and field in source and field != "max_tokens":
                output = _as_tokens(source.get(field))
    if output is None and isinstance(row.get("top_provider"), dict):
        output = _as_tokens(row["top_provider"].get("max_completion_tokens"))
    return context, output

def record(prefix: str, model: str, *, context: Optional[int] = None, output: Optional[int] = None,
           source: str = "api") -> None:
    """Remember what a provider reported. Saved in the background, never blocks."""
    context = _as_tokens(context)
    output = _as_tokens(output)
    if not context and not output:
        return
    global _save_timer
    with _lock:
        _ensure_loaded()
        key = _key(prefix, model)
        old = _learned.get(key) or {}
        row = {"context": context or old.get("context"), "output": output or old.get("output"), "source": source}
        if all(old.get(field) == row[field] for field in row):
            return
        _learned[key] = {**row, "ts": int(time.time())}
        if _save_timer is None:

            _save_timer = threading.Timer(1.5, flush)
            _save_timer.daemon = True
            _save_timer.start()

def record_rows(prefix: str, rows: Iterable[Dict[str, Any]], *, id_field: str = "id",
                source: str = "api") -> int:
    """Record every row of a provider's model list that carries a limit."""
    count = 0
    for row in rows or []:
        if not isinstance(row, dict) or not row.get(id_field):
            continue
        context, output = extract_limits(row)
        if context or output:
            record(prefix, str(row[id_field]), context=context, output=output, source=source)
            count += 1
    return count

def flush() -> None:
    global _save_timer
    with _lock:
        _save_timer = None
        snapshot = dict(_learned)
    try:
        from .storage import save_json
        save_json(_path(), snapshot, backup=False)
    except Exception:
        pass

def learned(prefix: str, model: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _ensure_loaded()
        return dict(_learned.get(_key(prefix, model)) or {}) or None

def resolve(prefix: str, provider: str, model: str) -> Dict[str, Any]:
    """Best known limits for one catalogue entry, with where they came from."""
    result: Dict[str, Any] = {"context": None, "output": None, "source": "", "label": "",
                              "canonical": canonical(model)}
    if provider == "local":
        try:
            from . import local_models
            info = local_models.get_model(model)
            if info:
                result.update({"context": int(info["settings"].get("context") or 0) or None,
                               "source": "local", "label": str(info["settings"].get("name") or "")})
                trained = int((info.get("meta") or {}).get("context_length") or 0)
                if trained:
                    result["trained_context"] = trained
                return result
        except Exception:
            pass
    known = lookup_known(model)
    got = learned(prefix, model) or learned(provider, model)
    if got and got.get("context"):
        result.update({"context": got["context"], "output": got.get("output"),
                       "source": str(got.get("source") or "api")})
        if known:
            result["label"] = known.get("label") or ""
            result["output"] = result["output"] or known.get("output")
        return result
    if known:
        result.update(known)
    return result
