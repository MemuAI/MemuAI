"""Round 33 (r33-thinking): the reasoning levels each model REALLY supports, and how to ask for them.

Before this module every MemuAI surface offered one template — Auto / Minimal / Low / Medium / High / Max —
for every model, and the Proxy forwarded the choice to almost nobody: an OpenAI-compatible connection
(``custom``) ignored it completely. Gemini 3.7 Flash, for example, accepts only low / medium / high
(``minimal`` is a 400), GPT-6.1 Sol rejects ``none`` and ``minimal``, GLM-5.3 cannot switch thinking off and
knows only low / high / max, DeepSeek and Qwen switch thinking with their own non-OpenAI fields.

This module is the single answer for all of it:

* :func:`profile` — what a model supports: ``kind`` (``levels`` / ``toggle`` / ``fixed`` / ``none``), the
  depth ``levels`` in order, whether reasoning can be switched ``off`` and the provider's ``default``.
  Sources in order: the user's override (Proxy → model card), what the API itself rejected earlier
  (learned), the provider's own catalogue (Antigravity), the family table below (official docs), a
  family guess for an unknown future model.
* :func:`clamp` — a stored or requested level → the nearest level the model accepts.
* :func:`resolve` — (``thinking`` flag, ``effort``) of a request → what to ask the API for.
* :func:`apply_openai_compatible` — writes the right request fields for the API dialect
  (``reasoning_effort`` / ``enable_thinking`` + ``thinking_budget`` / ``thinking.type`` /
  OpenRouter ``reasoning`` / Anthropic-compat ``thinking`` budget).
* :func:`learn_rejection` — the API answered 400 to a reasoning value: remember it, so the level
  disappears from the UI and is never sent again.

Vocabulary (stored in preferences, sent by every client): ``auto`` (the model decides — nothing is sent),
``off``, ``on`` (switch-only models), ``minimal``, ``low``, ``medium``, ``high``, ``xhigh``, ``max``.
The legacy ``none`` is read as ``off``.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEPTHS: Tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

CHOICES: Tuple[str, ...] = ("auto", "off", "on") + DEPTHS
ALIASES = {"none": "off", "disabled": "off", "false": "off", "no": "off", "min": "minimal",
           "enabled": "on", "true": "on", "yes": "on", "extra-high": "xhigh", "x-high": "xhigh",
           "ultra": "max", "maximum": "max", "mid": "medium", "default": "auto", "": "auto"}
KINDS = ("levels", "toggle", "fixed", "none")

QWEN_BUDGETS = {"low": 1024, "medium": 4096}

CLAUDE_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384, "xhigh": 24576, "max": 32000}

DATA_FILE_OVERRIDES = "reasoning-overrides.json"
DATA_FILE_LEARNED = "reasoning-learned.json"

_lock = threading.Lock()
_overrides: Optional[Dict[str, Dict[str, Any]]] = None
_learned: Optional[Dict[str, Dict[str, Any]]] = None

def normalize(value: Any) -> str:
    """Any spelling of a level → one of :data:`CHOICES` (unknown → ``auto``)."""
    text = str(value if value is not None else "").strip().lower()
    text = ALIASES.get(text, text)
    return text if text in CHOICES else "auto"

_VENDOR = re.compile(r"^(?:[a-z0-9_.-]+/)+")

def canonical(model: str) -> str:
    """``qwen/qwen3-8-max:free`` → ``qwen3.8-max``; ``ydx-gpt://f/deepseek-v4.1-flash/latest`` → ``deepseek-v4.1-flash``."""
    name = str(model or "").strip().lower().replace("_", "-")
    name = re.sub(r"^[a-z][a-z-]*://[^/]+/([^/]+)(?:/.*)?$", r"\1", name)
    name = re.sub(r"[:@].*$", "", name)
    name = _VENDOR.sub("", name)
    name = re.sub(r"(?<=\d)-(\d)(?=$|-)", r".\1", name)
    name = re.sub(r"-(?:latest|preview(?:-\d+)?|\d{4}-?\d{2}-?\d{2}|\d{4})$", "", name)
    return name

def _ver(name: str, pattern: str) -> Optional[float]:
    match = re.search(pattern, name)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None

def _p(kind: str, levels: Iterable[str] = (), *, off: bool = False, default: str = "", dialect: str = "effort",
       family: str = "", note: str = "", wire: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return {"kind": kind, "levels": [x for x in DEPTHS if x in set(levels)], "can_off": bool(off),
            "default": default, "dialect": dialect, "family": family, "note": note, "wire": dict(wire or {})}

NONE = _p("none", family="no-reasoning")

def _openai(name: str) -> Optional[Dict[str, Any]]:
    """OpenAI (developers.openai.com/api/docs/guides/reasoning, model pages)."""
    if re.search(r"(?:^|-)gpt-?oss", name):
        return _p("levels", ("low", "medium", "high"), default="medium", family="gpt-oss")
    if re.match(r"o1-(?:mini|preview)", name):
        return _p("fixed", family="o1-mini", note="no reasoning_effort parameter")
    if re.match(r"o[134](?:-|$)", name):
        return _p("levels", ("low", "medium", "high"), default="medium", family="o-series")
    if not re.match(r"(?:chat)?gpt-", name):
        return None
    if re.match(r"(?:chatgpt|gpt-[1-4](?![.\d]*\d{2,}))", name) and not re.match(r"gpt-[5-9]", name):
        return NONE
    major = _ver(name, r"^gpt-(\d+(?:\.\d+)?)") or 0
    if major < 5:
        return NONE
    if re.search(r"-chat(?:-|$)|chat-latest", name):
        return _p("fixed", family="gpt-chat", note="chat models reason with a fixed effort")
    if major >= 6:
        if "sol" in name:
            return _p("levels", ("low", "medium", "high", "xhigh", "max"), default="medium", family="gpt-6-sol")
        return _p("levels", ("low", "medium", "high", "xhigh", "max"), default="medium", family="gpt-6",
                  note="GPT-6 does not accept none")
    if re.search(r"-pro(?:-|$)", name):
        if major < 5.1:
            return _p("fixed", family="gpt-5-pro", note="gpt-5-pro supports only high")
        return _p("levels", ("medium", "high", "xhigh"), default="high", family="gpt-5-pro")
    if "codex" in name:
        if major >= 5.2 or "codex-max" in name:
            return _p("levels", ("low", "medium", "high", "xhigh"), default="medium", family="gpt-codex")
        return _p("levels", ("low", "medium", "high"), default="medium", family="gpt-codex")
    if major >= 5.2:
        return _p("levels", ("low", "medium", "high", "xhigh"), off=True, default="off" if major < 5.5 else "medium",
                  family="gpt-5.2+", wire={"off": "none"})
    if major >= 5.1:
        return _p("levels", ("low", "medium", "high"), off=True, default="off", family="gpt-5.1", wire={"off": "none"})
    return _p("levels", ("minimal", "low", "medium", "high"), default="medium", family="gpt-5")

def _gemini(name: str) -> Optional[Dict[str, Any]]:
    """Gemini (ai.google.dev/gemini-api/docs/thinking, …/docs/openai)."""
    if not name.startswith("gemini"):
        return None
    if re.search(r"image|tts|embedding|audio|live|veo|imagen", name):
        return NONE
    version = _ver(name, r"^gemini-(\d+(?:\.\d+)?)") or 0
    if version < 2.5:
        return NONE if version else None
    if version < 3:
        if "pro" in name:
            return _p("levels", ("low", "medium", "high"), default="", family="gemini-2.5-pro")
        return _p("levels", ("low", "medium", "high"), off=True, default="off" if "lite" in name else "",
                  family="gemini-2.5-flash", wire={"off": "none"})
    if "pro" in name:
        if version < 3.1:
            return _p("levels", ("low", "high"), default="high", family="gemini-3-pro")
        return _p("levels", ("low", "medium", "high"), default="high", family="gemini-3-pro")
    if "lite" in name:
        return _p("levels", ("minimal", "low", "medium", "high"), default="minimal", family="gemini-3-flash-lite")
    if version >= 3.7:
        return _p("levels", ("low", "medium", "high"), default="medium", family="gemini-3.7-flash")
    return _p("levels", ("minimal", "low", "medium", "high"), default="medium" if version >= 3.5 else "high",
              family="gemini-3-flash")

def _claude(name: str, raw: str) -> Optional[Dict[str, Any]]:
    """Claude (platform.claude.com/docs/en/build-with-claude/effort, adaptive-thinking)."""
    if not name.startswith("claude"):
        return None
    thinking_id = bool(re.search(r"thinking", raw))
    match = re.search(r"claude-(?:(opus|sonnet|haiku)-(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)-(opus|sonnet|haiku))", name)
    tier = (match.group(1) or match.group(4)) if match else ""
    version = float((match.group(2) or match.group(3)) if match else 0) if match else 0.0
    if version and version < 3.7:
        return NONE
    levels = ["low", "medium", "high"]
    if (tier == "opus" and version >= 4.6) or (tier == "sonnet" and version >= 4.6) or version >= 5:
        levels.append("max")
    if (tier == "opus" and version >= 4.7) or version >= 5:
        levels.append("xhigh")
    if thinking_id:

        return _p("levels", levels, default="high", family="claude-thinking", dialect="claude")
    return _p("levels", levels, off=True, default="off", family="claude", dialect="claude")

def _chinese(name: str) -> Optional[Dict[str, Any]]:
    """DeepSeek / Qwen / GLM / Kimi / MiniMax (official docs of each; live-checked through REFORM)."""
    if name.startswith("deepseek"):
        if re.search(r"reasoner|-r1|r1-|prover", name):
            return _p("fixed", family="deepseek-r1")
        if re.match(r"deepseek-(?:chat|coder|v2|v3(?!\.[1-9]))", name):
            return NONE

        return _p("levels", ("low", "high", "max"), off=True, default="high", family="deepseek",
                  dialect="thinking_type")
    if name.startswith(("qwen", "qwq", "qvq")):
        if re.match(r"(?:qwq|qvq)", name) or "thinking" in name:
            return _p("fixed", family="qwen-thinking")
        if re.search(r"instruct|coder|math|-mt|-tts|-asr|omni-turbo|^qwen-?\d?\.?\d?-?vl-max|^qwen2|^qwen-max",
                     name):
            return NONE
        version = _ver(name, r"^qwen(\d+(?:\.\d+)?)") or 0
        commercial = re.match(r"qwen-(?:plus|flash|turbo)", name)
        if not commercial and version < 3:
            return None

        if commercial or re.match(r"qwen3-max", name):
            default = "off"
        elif re.match(r"qwen3-vl", name):
            default = ""
        else:
            default = "high"
        return _p("levels", ("low", "medium", "high"), off=True, default=default, family="qwen", dialect="qwen",
                  note="levels map onto thinking_budget")
    if name.startswith("glm"):
        version = _ver(name, r"^glm-(\d+(?:\.\d+)?)") or 0
        if version >= 5.3:
            return _p("levels", ("low", "high", "max"), default="max", family="glm-5.3", dialect="thinking_type")
        if version >= 5.2:
            return _p("levels", ("low", "high", "max"), off=True, default="max", family="glm-5.2", dialect="thinking_type")
        if version >= 4.5:
            return _p("toggle", off=True, default="on", family="glm", dialect="thinking_type")
        return NONE
    if name.startswith("kimi") or name.startswith("moonshot"):
        version = _ver(name, r"kimi-k(\d+(?:\.\d+)?)") or 0
        if version >= 3:
            return _p("levels", ("low", "high", "max"), default="max", family="kimi-k3", dialect="thinking_type")
        if "code" in name or "thinking" in name:
            return _p("fixed", family="kimi-thinking")
        if version >= 2.5:
            return _p("toggle", off=True, default="on", family="kimi", dialect="thinking_type")
        return NONE
    if name.startswith("minimax"):
        version = _ver(name, r"minimax-m(\d+(?:\.\d+)?)") or 0
        return _p("fixed", family="minimax") if version >= 1 else None
    return None

def _grok(name: str) -> Optional[Dict[str, Any]]:
    """xAI (docs.x.ai/developers/model-capabilities/text/reasoning)."""
    if not name.startswith("grok"):
        return None
    if re.search(r"imagine|image|vision-|non-reasoning", name):
        return NONE
    version = _ver(name, r"^grok-(\d+(?:\.\d+)?)") or 0
    if re.match(r"grok-3-mini", name):
        return _p("levels", ("low", "high"), default="low", family="grok-3-mini")
    if version >= 4.6:
        return _p("levels", ("low", "medium", "high", "xhigh"), default="high", family="grok-4.6+")
    if version >= 4.5:
        return _p("levels", ("low", "medium", "high"), default="high", family="grok-4.5")
    if 4.3 <= version < 4.4:
        return _p("levels", ("low", "medium", "high"), off=True, default="", family="grok-4.3", wire={"off": "none"})
    if version >= 4 or re.search(r"code|composer|fast|reasoning", name):
        return _p("fixed", family="grok-4", note="reasoning_effort is rejected")
    if version and version < 3.5:
        return NONE
    return None

def family_profile(model: str) -> Optional[Dict[str, Any]]:
    """The documented profile of a model family, or ``None`` when the name is not recognised."""
    raw = str(model or "").lower()
    name = canonical(model)
    for rule in (_gemini, _openai, _chinese, _grok):
        found = rule(name)
        if found is not None:
            return dict(found, levels=list(found["levels"]))
    found = _claude(name, raw)
    if found is not None:
        return found
    if re.search(r"thinking|reason|(?<![a-z0-9])r1(?![0-9])", raw):
        return _p("fixed", family="thinking-id", note="a reasoning variant (by name)")
    return None

def guess_profile(model: str, reasoning: Optional[bool]) -> Dict[str, Any]:
    """An unknown model: no control without evidence that it reasons; otherwise the common OpenAI scale."""
    if reasoning is True:
        return _p("levels", ("low", "medium", "high"), default="", family="unknown",
                  note="assumed: the common low / medium / high scale")
    return dict(NONE, family="unknown")

def _antigravity(model: str) -> Optional[Dict[str, Any]]:
    try:
        from .providers import antigravity_models as ag
    except Exception:
        return None
    name = str(model or "")
    if name.startswith("antigravity-"):
        name = name[len("antigravity-"):]
    upstream = ag.upstream_id(name)
    low = upstream.lower()
    if upstream in ag.TIERED_UPSTREAM:
        if ag.request_level(name, ""):
            return _p("fixed", family="antigravity-tiered", note="the level is part of the model name")

        return _p("levels", ("low", "medium", "high"), default="medium", family="gemini-3.7-flash",
                  dialect="antigravity")
    if low.startswith("claude") and ag.is_thinking(upstream):

        return _p("levels", ("low", "medium", "high", "max"), off=True, default="high", family="claude-thinking",
                  dialect="antigravity")
    if low.startswith(("gemini", "gpt-oss")) or low.startswith("claude"):
        family = family_profile(upstream)
        if family and family["kind"] != "none":
            return _p("fixed", family=family["family"], note="Antigravity picks the depth itself")
        return family
    return None

def _web_provider(provider: str, model: str) -> Optional[Dict[str, Any]]:
    """Chat-website providers (Genspark, Gemini web, Grok web, Kiro) cannot be told how deep to think."""
    if provider not in ("genspark", "geminiweb", "grok", "kiro", "gs"):
        return None
    if provider == "geminiweb":

        return _p("levels", ("low", "medium", "high"), default="medium", family="gemini-web",
                  note="Gemini app Thinking level: high = extended thinking")
    family = family_profile(model)
    if family is None or family["kind"] == "none":
        return dict(NONE, family=(family or {}).get("family") or "no-reasoning")
    return _p("fixed", family=family["family"], note=f"{provider} decides the depth itself")

def _data_dir() -> str:
    try:
        from .model_capabilities import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.environ.get("MEMUAI_DATA_DIR") or "."

def _load(name: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(os.path.join(_data_dir(), name), encoding="utf-8") as stream:
            data = json.load(stream)
        return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def _store(name: str, data: Dict[str, Any]) -> None:
    path = os.path.join(_data_dir(), name)
    try:
        from .storage import save_json
        save_json(path, data, backup=False)
    except Exception:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=1)
        except OSError:
            pass

def _ensure() -> None:
    global _overrides, _learned
    if _overrides is None:
        _overrides = _load(DATA_FILE_OVERRIDES)
    if _learned is None:
        _learned = _load(DATA_FILE_LEARNED)

def reset_cache() -> None:
    """For tests and after the data directory changes."""
    global _overrides, _learned
    with _lock:
        _overrides = None
        _learned = None

def get_override(model_id: str) -> Dict[str, Any]:
    with _lock:
        _ensure()
        return dict((_overrides or {}).get(str(model_id or "")) or {})

def set_override(model_id: str, value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The user's own profile for a model (Proxy → model card). ``None`` / ``{}`` returns to automatic."""
    clean: Dict[str, Any] = {}
    if isinstance(value, dict) and value:
        kind = str(value.get("kind") or "levels")
        clean["kind"] = kind if kind in KINDS else "levels"
        levels = value.get("levels") if isinstance(value.get("levels"), list) else []
        clean["levels"] = [x for x in DEPTHS if x in {normalize(item) for item in levels}]
        clean["can_off"] = bool(value.get("can_off"))
        default = normalize(value.get("default"))
        clean["default"] = default if default in clean["levels"] or default in ("off", "on") else ""
        dialect = str(value.get("dialect") or "")
        if dialect in ("effort", "qwen", "thinking_type", "openrouter", "claude", "anthropic", "none"):
            clean["dialect"] = dialect
        if clean["kind"] == "levels" and not clean["levels"]:
            clean["kind"] = "toggle" if clean["can_off"] else "fixed"
    with _lock:
        _ensure()
        assert _overrides is not None and _learned is not None
        if clean:
            _overrides[str(model_id)] = clean
        else:
            _overrides.pop(str(model_id), None)

        _learned.pop(str(model_id), None)
        snapshot, learned = dict(_overrides), dict(_learned)
    _store(DATA_FILE_OVERRIDES, snapshot)
    _store(DATA_FILE_LEARNED, learned)
    return clean

def learned(model_id: str) -> Dict[str, Any]:
    with _lock:
        _ensure()
        return dict((_learned or {}).get(str(model_id or "")) or {})

def learn_rejection(model_id: str, level: str, *, all_params: bool = False) -> None:
    """The API refused this reasoning value (or every reasoning field) for this model: never send it again."""
    level = normalize(level)
    with _lock:
        _ensure()
        assert _learned is not None
        row = dict(_learned.get(str(model_id)) or {})
        if all_params:
            row["no_params"] = True
        elif level not in ("auto",):
            rejected = set(row.get("rejected") or [])
            rejected.add(level)
            row["rejected"] = [x for x in CHOICES if x in rejected]
        _learned[str(model_id)] = row
        snapshot = dict(_learned)
    _store(DATA_FILE_LEARNED, snapshot)

def learn_reasons(model_id: str) -> None:
    """An unknown model streamed reasoning: it reasons (its levels are then assumed, see :func:`guess_profile`)."""
    with _lock:
        _ensure()
        assert _learned is not None
        row = dict(_learned.get(str(model_id)) or {})
        if row.get("reasons"):
            return
        row["reasons"] = True
        _learned[str(model_id)] = row
        snapshot = dict(_learned)
    _store(DATA_FILE_LEARNED, snapshot)

def forget_learned(model_id: str) -> None:
    with _lock:
        _ensure()
        assert _learned is not None
        _learned.pop(str(model_id), None)
        snapshot = dict(_learned)
    _store(DATA_FILE_LEARNED, snapshot)

def _summary_reasoning(model_id: str) -> Optional[bool]:
    try:
        from . import model_capabilities
        value = model_capabilities.capabilities_for(model_id).get("reasoning")
        return value if isinstance(value, bool) else None
    except Exception:
        return None

def profile(model_id: str, provider: str = "", raw_model: str = "", *,
            reasoning: Optional[bool] = None, use_catalog: bool = True,
            use_override: bool = True) -> Dict[str, Any]:
    """What ``model_id`` supports. ``raw_model`` is the provider's own id (defaults to ``model_id``).

    Returned keys: ``kind``, ``levels``, ``can_off``, ``default``, ``dialect``, ``family``, ``source``
    (``override`` / ``provider`` / ``docs`` / ``guess``), ``note``, ``options`` (what a picker lists,
    ``auto`` first) and ``rejected`` (levels the API refused).
    """
    model_id = str(model_id or "")
    raw = str(raw_model or model_id)
    override = get_override(model_id) if use_override else {}
    result: Optional[Dict[str, Any]] = None
    source = "docs"
    if override:
        base = family_profile(raw) or _p("levels", family="custom")
        result = dict(base, **{k: v for k, v in override.items() if k in ("kind", "levels", "can_off", "default", "dialect")})
        result["levels"] = list(result["levels"])
        source = "override"
    if result is None and provider == "antigravity":
        result = _antigravity(raw)
        source = "provider"
    if result is None and provider in ("genspark", "geminiweb", "grok", "kiro", "gs"):
        result = _web_provider(provider, raw)
        source = "provider"
    if result is None:
        result = family_profile(raw)
        if result is None and raw != model_id:
            result = family_profile(model_id)
    if result is None:
        if reasoning is None and learned(model_id).get("reasons"):
            reasoning = True
        if reasoning is None and use_catalog:
            reasoning = _summary_reasoning(model_id)
        result = guess_profile(raw, reasoning)
        source = "guess"
    result = dict(result)
    result["source"] = source
    rejected: List[str] = []
    if source != "override":
        seen = learned(model_id)
        rejected = [x for x in seen.get("rejected") or [] if x in CHOICES]
        if seen.get("no_params") and result["kind"] in ("levels", "toggle"):
            result.update(kind="fixed", levels=[], can_off=False, note="the API rejects reasoning fields")
        if rejected:
            result["levels"] = [x for x in result["levels"] if x not in rejected]
            if "off" in rejected:
                result["can_off"] = False
            if result["kind"] == "levels" and not result["levels"]:
                result["kind"] = "toggle" if result["can_off"] else "fixed"
    result["rejected"] = rejected
    if result["kind"] in ("fixed", "none"):
        result["levels"], result["can_off"] = [], False
    if result["default"] and result["default"] not in result["levels"] and result["default"] not in ("off", "on"):
        result["default"] = ""
    result["options"] = options(result)
    return result

def options(prof: Dict[str, Any]) -> List[str]:
    """What a picker shows for this profile, in order (``auto`` always first; empty = no control)."""
    kind = prof.get("kind")
    if kind not in ("levels", "toggle"):
        return []
    out = ["auto"]
    if prof.get("can_off"):
        out.append("off")
    if kind == "toggle" and prof.get("default") != "on":
        out.append("on")
    out += [x for x in DEPTHS if x in (prof.get("levels") or [])]
    return out

def clamp(level: Any, prof: Dict[str, Any]) -> str:
    """A requested level → the nearest one the model accepts (``auto`` when there is nothing to choose)."""
    level = normalize(level)
    kind = prof.get("kind")
    if level == "auto" or kind not in ("levels", "toggle"):
        return "auto"
    levels = [x for x in DEPTHS if x in (prof.get("levels") or [])]
    can_off = bool(prof.get("can_off"))
    if level == "off":
        if can_off:
            return "off"
        return levels[0] if levels else "auto"
    if kind == "toggle":
        return "on"
    if level == "on":
        default = prof.get("default")
        if default in levels:
            return default
        return "medium" if "medium" in levels else (levels[len(levels) // 2] if levels else "auto")
    if level in levels:
        return level
    if not levels:
        return "auto"
    if level == "minimal" and can_off and "minimal" not in levels:

        return "off"
    if level == "max":
        return levels[-1]
    index = DEPTHS.index(level)
    best = min(levels, key=lambda x: (abs(DEPTHS.index(x) - index), -DEPTHS.index(x)))
    return best

def resolve(prof: Dict[str, Any], thinking: Any = None, effort: Any = None) -> str:
    """What to ask for: ``""`` (send nothing — the model decides), ``off``, ``on`` or a depth level.

    ``effort`` (the picker) wins. With ``auto`` the ``thinking`` switch only matters for a model whose
    reasoning is off by default (Claude without «-thinking», qwen-plus…): ``True`` turns it on.
    """
    level = clamp(effort, prof)
    if level != "auto":
        return level
    if thinking is True and prof.get("can_off") and prof.get("default") == "off":
        return clamp("on", prof)
    return ""

def wire_value(prof: Dict[str, Any], level: str) -> str:
    return str((prof.get("wire") or {}).get(level) or level)

OWN_FIELDS = ("reasoning_effort", "enable_thinking", "thinking_budget", "thinking", "reasoning")

def _host(base_url: str) -> str:
    match = re.match(r"^[a-z]+://([^/:]+)", str(base_url or "").lower())
    return match.group(1) if match else ""

def dialect_for(prof: Dict[str, Any], base_url: str = "") -> str:
    host = _host(base_url)
    explicit = prof.get("dialect") or "effort"
    if prof.get("source") == "override" and explicit not in ("", "effort"):
        return explicit
    if "openrouter" in host:
        return "openrouter"
    if host.endswith("anthropic.com"):
        return "anthropic"
    if "dashscope" in host or "aliyuncs" in host or "qwencloud" in host:
        return "qwen"
    if explicit == "claude":
        return "effort"
    return explicit

def apply_openai_compatible(body: Dict[str, Any], prof: Dict[str, Any], level: str,
                            base_url: str = "") -> Dict[str, Any]:
    """Write the request fields for ``level`` (from :func:`resolve`) into an OpenAI-style ``body``.

    Returns ``{"fields": [...], "level": level, "dialect": …}`` — what was written, for retries and logs.
    """
    info = {"fields": [], "level": level, "dialect": ""}
    if not level or prof.get("kind") not in ("levels", "toggle"):
        return info
    dialect = dialect_for(prof, base_url)
    info["dialect"] = dialect
    off = level == "off"
    depth = level if level in DEPTHS else ""

    def put(key: str, value: Any) -> None:
        body[key] = value
        info["fields"].append(key)

    if dialect == "openrouter":
        if off:
            put("reasoning", {"enabled": False})
        elif depth:
            put("reasoning", {"effort": {"max": "xhigh"}.get(depth, depth)})
        else:
            put("reasoning", {"enabled": True})
    elif dialect == "anthropic":
        if off:
            put("thinking", {"type": "disabled"})
        else:
            put("thinking", {"type": "enabled", "budget_tokens": CLAUDE_BUDGETS.get(depth or "high", 16384)})
    elif dialect == "qwen":
        if off:
            put("enable_thinking", False)
        else:
            put("enable_thinking", True)
            if depth in QWEN_BUDGETS:
                put("thinking_budget", QWEN_BUDGETS[depth])
    elif dialect == "thinking_type":
        if off:
            put("thinking", {"type": "disabled"})
        elif prof.get("can_off") and prof.get("kind") == "toggle":
            put("thinking", {"type": "enabled"})
        if depth and prof.get("kind") == "levels":
            put("reasoning_effort", wire_value(prof, depth))
    else:
        if off:
            value = wire_value(prof, "off")
            if value != "off":
                put("reasoning_effort", value)
        elif depth:
            put("reasoning_effort", wire_value(prof, depth))
        elif level == "on":
            put("reasoning_effort", "medium")
    return info

def strip_fields(body: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    clean = dict(body)
    for key in fields:
        clean.pop(key, None)
    return clean

_REJECT_HINT = re.compile(r"reason|effort|think|budget|enable_thinking|unsupported value|not supported|"
                          r"invalid (?:value|argument)|unknown (?:field|parameter)|extra (?:inputs|fields)|"
                          r"does not support parameter|no available accounts|запрос отклон", re.I)

def looks_like_rejection(status: int, text: str) -> bool:
    """Did the API refuse the request because of a reasoning field? (400/422; some gateways answer 503.)"""
    if status in (400, 422):
        return True if not text else bool(_REJECT_HINT.search(text)) or len(text) < 400
    if status == 503:
        return bool(re.search(r"no available accounts", text or "", re.I))
    return False

def for_request(model_id: str, provider: str, raw_model: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise ``params`` of a Proxy request in place: ``effort`` becomes a supported level (or is removed),
    ``_reasoning`` carries the profile and ``_reasoning_level`` what to ask the API for."""
    prof = profile(model_id, provider, raw_model)
    requested = params.get("effort")
    level = resolve(prof, params.get("thinking"), requested)
    params["_reasoning"] = prof
    params["_reasoning_level"] = level
    params["_model_id"] = model_id
    clamped = clamp(requested, prof)
    if clamped == "auto":
        params.pop("effort", None)
    else:
        params["effort"] = clamped
    return prof

def public(prof: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a profile a client needs (pickers, badges)."""
    return {key: prof.get(key) for key in ("kind", "levels", "can_off", "default", "options", "source",
                                            "family", "note", "rejected")}

def profile_for_catalog(entry: Any) -> Dict[str, Any]:
    """Profile of a Proxy catalogue entry (``.id``, ``.provider``, ``.model``)."""
    return public(profile(getattr(entry, "id", ""), getattr(entry, "provider", ""), getattr(entry, "model", "")))

def reasoning_levels(model_id: str, provider: str = "", raw_model: str = "") -> List[str]:
    """For other modules (unified model picker, characters…): the picker options of a model."""
    return profile(model_id, provider, raw_model)["options"]
