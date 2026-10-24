"""The built-in companion of MemuAI Proxy.

A chat with a model the user picks from the catalogue, briefed with:

* the knowledge base in ``app/companion_kb/*.md`` — how Proxy, every provider,
  the API, local models and the detector work, and what each error means;
* a live snapshot of this Proxy: version, providers, account states, recent
  errors and non-secret settings. Secrets never enter the snapshot.

Only the knowledge-base files relevant to the question go into the prompt:
``overview`` and ``errors`` always, a provider file when that provider is
mentioned or has an account. When a new provider is added, its file is added
too (see README_FOR_AI.md).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

ALWAYS = ("overview", "errors")
#: Extra words that pull a knowledge-base file into the prompt.
TOPIC_WORDS: Dict[str, str] = {
    "api": r"/v1|api|ключ|key|curl|openai|sdk|клиент|client|cors|passthrough|/p/",
    "settings": r"настро|setting|таймаут|timeout|порт|port|прокси|proxy|ssl|балансир|balanc|retry|повтор",
    "local": r"локальн|local|gguf|llama|hugging ?face|ollama|lm studio|vram|cuda|vulkan",
    "detector": r"детектор|detector|подлин|genuine|подмен|substitut|настоящ",
    "troubleshooting": r"не работ|ошибк|error|fail|сломал|broken|не отвеча|timeout|висит|hang",
}


def kb_dir() -> Path:
    base = getattr(sys, "_MEIPASS", "")
    if base:
        frozen = Path(base) / "app" / "companion_kb"
        if frozen.is_dir():
            return frozen
    return Path(__file__).resolve().parent / "companion_kb"


def kb_files() -> Dict[str, str]:
    folder = kb_dir()
    files: Dict[str, str] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            try:
                files[path.stem] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    return files


def pick_kb(question: str, providers_in_use: List[str]) -> List[str]:
    files = kb_files()
    text = str(question or "").lower()
    chosen = [name for name in ALWAYS if name in files]
    for name in files:
        if name.startswith("provider-"):
            provider = name[len("provider-"):]
            if provider in providers_in_use or re.search(rf"\b{re.escape(provider)}\b", text):
                chosen.append(name)
    for name, pattern in TOPIC_WORDS.items():
        if name in files and re.search(pattern, text):
            chosen.append(name)
    return list(dict.fromkeys(chosen))


# --------------------------------------------------------------------------
# Live snapshot (no secrets)
# --------------------------------------------------------------------------

def snapshot(limit_errors: int = 8) -> Dict[str, Any]:
    from .config import config_store
    from .state import state
    from .version import APP_VERSION
    settings = config_store.settings
    accounts = []
    for account in state.all_accounts():
        accounts.append({
            "provider": account.provider, "label": account.label, "status": account.status,
            "message": str(account.status_message or "")[:200], "enabled": account.enabled,
            "priority": getattr(account, "priority", "normal"),
            "requests": account.stats.get("requests", 0), "errors": account.stats.get("failures", 0),
            "last_error": str(account.stats.get("last_error") or "")[:200],
        })
    errors = []
    for row in state.list_logs(limit=limit_errors, status="error").get("logs", []):
        errors.append({"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(row.get("ts") or 0)),
                       "model": row.get("model"), "provider": row.get("provider"),
                       "status": row.get("status"), "error": str(row.get("error") or "")[:300]})
        if len(errors) >= limit_errors:
            break
    return {
        "version": APP_VERSION,
        "proxy_enabled": settings.proxy_enabled,
        "listen": f"{settings.host}:{settings.port}",
        "balancing": settings.balancing,
        "use_prefixes": settings.use_prefixes,
        "strict_models": settings.strict_models,
        "default_provider": settings.default_provider,
        "timeouts": {"first_byte": settings.first_byte_timeout, "request": settings.request_timeout,
                     "stream_idle": settings.stream_idle_timeout},
        "proxy_configured": bool(settings.default_proxy),
        "verify_ssl": settings.verify_ssl,
        "api_keys": len(getattr(settings, "api_keys", []) or []),
        "accounts": accounts,
        "models_enabled": len(state.enabled_models()),
        "recent_errors": errors,
    }


def _snapshot_text(data: Dict[str, Any]) -> str:
    lines = [f"MemuAI Proxy {data['version']}, listening on {data['listen']}, proxy "
             f"{'ON' if data['proxy_enabled'] else 'OFF'}, balancing={data['balancing']}, "
             f"prefixes={'on' if data['use_prefixes'] else 'off'}, strict catalogue={data['strict_models']}, "
             f"default provider={data['default_provider'] or '—'}, verify SSL={data['verify_ssl']}, "
             f"global proxy={'set' if data['proxy_configured'] else 'none'}, API keys={data['api_keys']}, "
             f"enabled models={data['models_enabled']}.",
             f"Timeouts: {data['timeouts']}.", "Accounts:"]
    for row in data["accounts"] or []:
        lines.append(f"- {row['provider']} «{row['label']}»: status={row['status']}"
                     f"{' (' + row['message'] + ')' if row['message'] else ''}, enabled={row['enabled']}, "
                     f"priority={row['priority']}, requests={row['requests']}, errors={row['errors']}")
    if not data["accounts"]:
        lines.append("- none yet")
    lines.append("Recent errors:")
    for row in data["recent_errors"] or []:
        lines.append(f"- {row['time']} {row['provider'] or '?'} / {row['model'] or '?'} HTTP {row['status']}: {row['error']}")
    if not data["recent_errors"]:
        lines.append("- none")
    return "\n".join(lines)


PERSONA = """You are the built-in companion of MemuAI Proxy, a local Windows application that connects user
accounts of AI services (Genspark, Kiro, Antigravity, Gemini Web, Grok, custom OpenAI-compatible APIs and
local GGUF models) and serves them through one OpenAI-compatible API.

How you work:
- Diagnose from evidence: the error text, the account states and the knowledge base below. Name the most likely
  cause first, then give numbered steps the user can do in the Proxy window (tab and button names as they appear).
- Distinguish a local problem (settings, expired cookie, port, proxy) from an upstream change or outage of the
  service. Unofficial web providers can change without notice: say so when it is the likely reason.
- Never ask the user to paste cookies, tokens or API keys into the chat, and never print them.
- If the knowledge base does not cover something, say you are not sure instead of inventing menu items.
- Answer in the user's language ({language}). Be concise; use short lists and code blocks for commands.
"""


def build_messages(history: List[Dict[str, str]], question: str, *, language: str = "ru",
                   error: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    data = snapshot()
    providers = sorted({row["provider"] for row in data["accounts"]})
    topic = " ".join([question] + [str(error or "")] + [str(row.get("content", "")) for row in history[-4:]])
    names = pick_kb(topic, providers)
    files = kb_files()
    kb = "\n\n".join(f"<kb file=\"{name}\">\n{files[name].strip()}\n</kb>" for name in names if name in files)
    system = (PERSONA.format(language="Russian" if language == "ru" else "English")
              + f"\n<knowledge_base>\n{kb}\n</knowledge_base>\n\n<this_proxy>\n{_snapshot_text(data)}\n</this_proxy>")
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for row in history[-20:]:
        role = row.get("role")
        if role in ("user", "assistant") and str(row.get("content") or "").strip():
            messages.append({"role": role, "content": str(row["content"])[:12000]})
    text = str(question or "").strip()
    if error:
        details = "\n".join(f"{key}: {value}" for key, value in error.items() if value not in (None, "", []))
        text = (text + "\n\n" if text else "") + f"<error_details>\n{details[:6000]}\n</error_details>"
    messages.append({"role": "user", "content": text or "?"})
    return messages


def default_model() -> str:
    """The model the companion uses: the saved choice, else a sensible first pick."""
    from .config import config_store
    from .state import state
    from .music_runtime import is_music_model
    saved = str(getattr(config_store.settings, "companion_model", "") or "")
    enabled = [entry.id for entry in state.enabled_models() if not is_music_model(entry.id)]
    if saved and saved in enabled:
        return saved
    preferred = [r"claude.*(sonnet|opus)", r"gpt-?5|gpt-4\.1|gpt-4o", r"gemini-.*pro", r"deepseek", r"qwen"]
    for pattern in preferred:
        match = next((model for model in enabled if re.search(pattern, model.lower())), "")
        if match:
            return match
    return enabled[0] if enabled else ""


async def answer(history: List[Dict[str, str]], question: str, *, model: str = "", language: str = "ru",
                 error: Optional[Dict[str, Any]] = None) -> AsyncIterator[Dict[str, Any]]:
    from . import model_call
    chosen = model or default_model()
    if not chosen:
        raise ValueError("No model is enabled: add an account and the assistant will be able to answer" if language == "en"
                         else "Нет ни одной включённой модели: добавьте аккаунт, и помощник сможет отвечать")
    messages = build_messages(history, question, language=language, error=error)
    yield {"type": "meta", "model": chosen}
    async for event in model_call.stream(chosen, messages, {"temperature": 0.3, "max_tokens": 2500}):
        yield event
