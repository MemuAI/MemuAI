"""Pure model identity and task contracts shared by desktop clients and Proxy."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List

_MODE_WORDS = ("thinking", "reasoning", "non-reasoning", "high", "low", "medium", "max", "xhigh",
               "minimal", "latest", "preview", "exp", "experimental", "tiered",
               "online", "free", "extended", "beta", "it", "hf", "gguf", "public")
_DATE_RE = re.compile(r"[-_@](?:20\d{2}[-_]?\d{2}[-_]?\d{2}|\d{6,8})$")

_QUANT_RE = re.compile(r"[-_.](?:q\d(?:[-_]k)?(?:[-_][msl0-9])*|iq\d[-_][a-z]+|bf16|f16|fp8|fp16|int4|int8|awq|gptq|mlx|4bit|8bit)$")

def canonical(model: str) -> str:
    """``anthropic/Claude-Opus-5-5-20260901:thinking`` -> ``claude-opus-5.5``."""
    return _canonical(str(model or ""))

@lru_cache(maxsize=8192)
def _canonical(text: str) -> str:
    text = text.strip().lower()
    parts = [part for part in text.split("/") if part]

    while len(parts) > 1 and re.fullmatch(r"latest|rc|deprecated|\d{8}", parts[-1]):
        parts.pop()
    text = parts[-1] if parts else ""
    text = re.split(r"[:@](?![0-9]{2}$)", text)[0]
    text = re.sub(r"\(([^)]*)\)", r" \1 ", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-.")
    for _ in range(3):
        text = _DATE_RE.sub("", text)
        text = _QUANT_RE.sub("", text)
        parts = text.split("-")
        while len(parts) > 1 and parts[-1] in _MODE_WORDS:
            parts.pop()
        text = "-".join(parts)

    text = re.sub(r"(?<=\d)-(?=\d(?:[^0-9bm]|$))", ".", text)
    return text.strip("-.")

DECISION_MODELS: Dict[str, Dict[str, Any]] = {
    "~typesafe/jev-latest": {
        "name": "Jev Latest", "maker": "TypeSafe", "price_input": 0.042, "context": 32000, "recommended": True,
        "note": "Always the newest Jev (today 1.13): upgrades without changing settings. The best all-round pick."},
    "typesafe/jev-1.13": {
        "name": "Jev 1.13", "maker": "TypeSafe", "price_input": 0.042, "context": 32000,
        "note": "The reference System One model: typed choice, score and yes/no with calibrated odds. Pinned version."},
    "jaredpalmer/kev-4b": {
        "name": "Kev 4B", "maker": "Jared Palmer", "price_input": 0.042, "context": 8192,
        "note": "Small open-weight model (Qwen3.5-4B based) on the same contract. Short states only — 8k context."},
    "respan/span-01": {
        "name": "Span-01", "maker": "Respan", "price_input": 0.02, "context": 0, "kinds": ["noul"],
        "note": "Scores how likely a described behaviour is present in a conversation — suits yes/no risk and intent checks."},
    "respan/span-01-lite": {
        "name": "Span-01 Lite", "maker": "Respan", "price_input": 0.0, "context": 0, "kinds": ["noul"], "free": True,
        "note": "The free, lighter Span-01: the same behaviour scoring for light use."},
    "respan/span-01-lite:free": {
        "name": "Span-01 Lite (free)", "maker": "Respan", "price_input": 0.0, "context": 0, "kinds": ["noul"], "free": True,
        "note": "The free, lighter Span-01: the same behaviour scoring for light use."},
    "upstage/solar-decide": {
        "name": "Solar Decide", "maker": "Upstage", "price_input": 0.05, "context": 524288,
        "note": "Built on Solar Mini 4 with a 512k context — for long states (big diffs, long reports)."},
    "inception/mercury-decide:free": {
        "name": "Mercury Decide (free)", "maker": "Inception", "price_input": 0.0, "context": 32768, "free": True,
        "note": "Free. Returns a choice, a score or a yes/no; 32k context."},
    "togethercomputer/tev1-4b-experimental": {
        "name": "Tev1 4B Experimental", "maker": "Together", "price_input": 0.042, "context": 32768, "kinds": ["choice"],
        "note": "Experimental: picks ONE option out of 2–24, no scores — good for routing and skill choice only."},
}

def _strs(values: Any) -> List[str]:
    if isinstance(values, str):
        values = re.split(r"[,+\s]+", values)
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(v).strip().lower() for v in values if isinstance(v, (str, int)) and str(v).strip()]

def _modality(name: str) -> str:
    name = str(name or "").lower()
    if name in ("pdf", "document", "documents", "file", "files"):
        return "file"
    if name in ("images", "img", "vision", "image_url"):
        return "image"
    if name in ("speech", "voice"):
        return "audio"
    if name in ("embedding", "embeddings", "vector"):
        return "embeddings"
    return name

def _modalities(values: Any) -> List[str]:
    out: List[str] = []
    for value in _strs(values):
        name = _modality(value)
        if name and name not in out:
            out.append(name)
    return out

_IMAGE_GEN = re.compile(r"(?<![a-z0-9])(?:dall-?e|gpt-image|imagen|flux|sdxl|stable-diffusion|seedream|kolors|recraft|"
                        r"ideogram|nano-banana|midjourney|playground-v|hidream|qwen-image|wan-?2|kling|veo|sora|"
                        r"runway|hailuo|seedance|imagine|wanx|cogview|kandinsky|t2i)(?![a-z])|-image(?:-|$)")
_VIDEO_GEN = re.compile(r"(?<![a-z0-9])(?:veo|sora|kling|runway|hailuo|seedance|wan-?2\.\d-?(?:t2v|i2v)|video)(?![a-z])")
_EMBED = re.compile(r"(?<![a-z0-9])(?:embed\w*|bge|e5-|gte-|nomic-embed|rerank\w*)")
_STT = re.compile(r"(?<![a-z0-9])(?:whisper|transcribe|stt|asr|parakeet)(?![a-z])")
_TTS = re.compile(r"(?<![a-z0-9])(?:tts|speech|voice|eleven|kokoro|piper)(?![a-z])")
_MUSIC = re.compile(r"(?<![a-z0-9])(?:suno|udio|lyria|music)(?![a-z])")

def known_decision_model(model: str) -> bool:
    """Stable decision identities, including connection prefixes and aliases."""
    raw = str(model or "").lower().strip().lstrip("~")
    if any(raw == key.lstrip("~") or raw.endswith("-" + key.lstrip("~")) or raw.endswith("/" + key.lstrip("~")) for key in DECISION_MODELS):
        return True
    return bool(re.search(r"(?:^|[-_/])jev(?:[-_.]?(?:v?\d|latest)|$)", raw))

def _value(fields, key):
    item = fields.get(key)
    return item.get("value") if isinstance(item, dict) else None

def kind_for_row(row: Dict[str, Any], *, entry: Any = None) -> str:
    """Canonical task contract; a generic text input is not proof of chat output."""
    caps = row.get("caps") if isinstance(row.get("caps"), dict) else {}
    identifiers = [getattr(entry, "model", ""), row.get("raw_model"), row.get("id")]
    if any(known_decision_model(value) for value in identifiers if value):
        return "decisions"
    architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
    fields = caps.get("fields") if isinstance(caps.get("fields"), dict) else {}
    outputs = _modalities(architecture.get("output_modalities") or row.get("output_modalities") or caps.get("out") or caps.get("output_modalities") or _value(fields, "output"))
    if "decisions" in outputs:return "decisions"
    if "embeddings" in outputs:return "embedding"
    if "scores" in outputs:return "rerank"
    aliases = {"text":"chat", "embeddings":"embedding", "reranker":"rerank", "reranking":"rerank", "decision":"decisions", "transcription":"stt", "speech-to-text":"stt", "text-to-speech":"tts"}
    declared = str(row.get("kind") or caps.get("kind") or _value(fields, "kind") or "").lower()
    declared = aliases.get(declared, declared)
    if declared in {"image","video","music","audio","stt","tts","embedding","rerank","decisions"}:return declared
    if outputs and "text" not in outputs:
        for value, kind in (("image","image"),("video","video"),("audio","audio"),("music","music")):
            if value in outputs:return kind
    if "text" in outputs and "video" not in outputs and "image" not in outputs:
        for identifier in identifiers:
            if identifier and _STT.search(canonical(str(identifier))):
                return "stt"
        return "chat"
    is_summary_caps = ("effective" in caps or "reference" in caps or "video_input" in caps or "audio_input" in caps or "fields" in caps)
    if not is_summary_caps and declared != "chat":
        chat_like = any(caps.get(other) for other in ("vision", "tools", "reasoning", "thinking", "structured", "json", "web", "code", "caching"))
        for key in ("video", "image", "music"):
            if caps.get(key) and not chat_like and (key != "music" or caps.get("text") is not True):return key

    for identifier in identifiers:
        if identifier:
            kind = infer_kind(str(identifier))
            if kind != "chat":return kind
    return "chat"

def infer_kind(model: str) -> str:
    name = canonical(model)
    raw = str(model or "").lower()
    if not name:
        return "chat"
    kind = "chat"
    if known_decision_model(raw) or re.search(r"(?:^|[-_/])decisions?(?:[-_/]|$)", raw):
        kind = "decisions"
    elif re.search(r"(?:^|[-_/])rerank(?:er|ing)?(?:[-_/]|$)", raw):
        kind = "rerank"
    elif _EMBED.search(name):
        kind = "embedding"
    elif _MUSIC.search(name) and not re.search(r"chat|instruct", name):
        kind = "music"
    elif _VIDEO_GEN.search(name) and not re.search(r"vl|vision|understanding|llava|chat|instruct|omni|analysis|caption|qa|embed", name):
        kind = "video"
    elif _IMAGE_GEN.search(name):
        kind = "image"
    elif _STT.search(name):
        kind = "stt"
    elif _TTS.search(name) and not re.search(r"chat|instruct|omni", name):
        kind = "tts"
    return kind
