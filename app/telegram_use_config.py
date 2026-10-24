"""Telegram personal account configuration; credentials never enter preferences/API output."""
from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .telegram_use_arguments import SUMMARY_ARGUMENTS, schema

DEFAULT_COMMANDS = [
    {"name": "question", "trigger": "!//question", "kind": "question", "enabled": True,
     "prompt": "Answer the user's question using the conversation if relevant. Use the language of the question.",
     "context_messages": 30, "output": "replace", "allowed_users": [], "allowed_chats": [],
     "max_per_hour": 10, "max_per_day": 50, "cooldown_seconds": 5},
    {"name": "voice", "trigger": "!//voice", "kind": "voice", "enabled": True,
     "prompt": "", "context_messages": 0, "output": "replace", "allowed_users": [], "allowed_chats": [],
     "max_per_hour": 10, "max_per_day": 50, "cooldown_seconds": 5},
    {"name": "summary", "trigger": "!//summary", "kind": "summary", "enabled": True,
     "arguments": SUMMARY_ARGUMENTS,
     "prompt": "Summarize the last {count} messages in {chat}: main topics, decisions, outstanding questions and next actions. Do not invent missing details.",
     "context_messages": 50, "output": "replace", "allowed_users": [], "allowed_chats": [],
     "max_per_hour": 10, "max_per_day": 50, "cooldown_seconds": 5},
]
DEFAULTS = {"enabled": False, "commands_enabled": False, "model": "", "api_id": 0,
            "allow_writes": True, "schedule_writes": False, "max_media_mb": 20,
            "context_chars": 32000, "max_parallel": 2, "commands": DEFAULT_COMMANDS,
            "web_enabled": True, "system_prompt": "",
            "stt_source": "voice", "stt_local_model": "", "stt_language": "auto",
            "voice_cleanup_enabled": False, "voice_cleanup_model": "", "voice_cleanup_prompt": ""}
_lock = threading.RLock()
_stores: dict[str, Any] = {}


def path():
    from .pgdata import MEMU_DIR
    return MEMU_DIR / "telegram_use.json"


def secrets():
    from .gdrive_sync_crypto import SecretStore
    from .storage import _json_transaction
    folder = path().parent / "telegram_use_secure"
    key = str(folder.resolve())
    with _lock:
        if key not in _stores:
            class ProtectedStore(SecretStore):
                def set(self, name: str, value: str) -> None:
                    # Session, API hash and bridge token may be touched by different processes.
                    with _json_transaction(self.path):
                        super().set(name, value)
            _stores[key] = ProtectedStore(folder)
        return _stores[key]


def integer(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def ids(value: Any) -> list[int]:
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    return list(dict.fromkeys(int(str(x)) for x in (value if isinstance(value, list) else [])
                              if re.fullmatch(r"-?\d{1,20}", str(x)) and int(str(x)) != 0))[:200]


def command(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Команда должна быть объектом / Command must be an object")
    trigger = str(value.get("trigger") or "").strip()
    if not re.fullmatch(r"!//[A-Za-zА-Яа-яЁё0-9_-]{1,32}", trigger):
        raise ValueError("Триггер: !//name, 1–32 буквы, цифры, _ или -")
    kind = value.get("kind") if value.get("kind") in ("voice", "summary") else "question"
    arguments = value.get("arguments")
    # Upgrade the shipped summary command without touching custom command instructions.
    if arguments is None and trigger.casefold() == "!//summary" and value.get("name") == "summary":
        kind, arguments = "summary", SUMMARY_ARGUMENTS
    if kind == "summary" and arguments is None:
        arguments = SUMMARY_ARGUMENTS
    arguments = schema(arguments)
    if kind == "summary":
        count = next((a for a in arguments if a["name"] == "count"), None)
        if not count or count["type"] != "integer" or not count["required"]:
            raise ValueError("Пересказу нужен обязательный целочисленный аргумент count / Summary requires a required integer count argument")
        count["min"], count["max"] = max(1, count.get("min", 1)), min(1000, count.get("max", 1000))
        if count["min"] > count["max"]:
            raise ValueError("Количество сообщений: 1–1000 / Message count: 1–1000")
        if any(a["name"] == "chat" and a["type"] != "chat" for a in arguments):
            raise ValueError("Аргумент chat должен иметь тип «Название чата» / The chat argument must have type chat")
    return {"name": str(value.get("name") or trigger[3:])[:80], "trigger": trigger,
            "enabled": value.get("enabled", True) is not False,
            "kind": kind, "arguments": arguments,
            "prompt": str(value.get("prompt") or "")[:16000], "model": str(value.get("model") or "")[:200],
            "context_messages": integer(value.get("context_messages"), 30, 0, 200),
            "output": value.get("output") if value.get("output") in ("reply", "memuai") else "replace",
            "allowed_users": ids(value.get("allowed_users")), "allowed_chats": ids(value.get("allowed_chats")),
            "max_per_hour": integer(value.get("max_per_hour"), 10, 1, 1000),
            "max_per_day": integer(value.get("max_per_day"), 50, 1, 10000),
            "cooldown_seconds": integer(value.get("cooldown_seconds"), 5, 0, 86400),
            "max_input_chars": integer(value.get("max_input_chars"), 8000, 100, 32000)}


def normalize(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
    for key in ("enabled", "commands_enabled", "allow_writes", "schedule_writes", "web_enabled", "voice_cleanup_enabled"):
        out[key] = out[key] is True
    out["api_id"] = integer(out["api_id"], 0, 0, 2147483647)
    out["max_media_mb"] = integer(out["max_media_mb"], 20, 1, 50)
    out["context_chars"] = integer(out["context_chars"], 32000, 1000, 100000)
    out["max_parallel"] = integer(out["max_parallel"], 2, 1, 4)
    out["model"] = str(out["model"] or "")[:200]
    out["system_prompt"] = str(out["system_prompt"] or "")[:16000]
    out["stt_source"] = out["stt_source"] if out["stt_source"] in ("voice", "quick", "local", "api") else "voice"
    out["stt_local_model"] = str(out["stt_local_model"] or "")[:120]
    language = str(out["stt_language"] or "auto").strip().lower()
    out["stt_language"] = language if re.fullmatch(r"auto|[a-z]{2,3}(?:-[a-z]{2,4})?", language) else "auto"
    out["voice_cleanup_model"] = str(out["voice_cleanup_model"] or "")[:200]
    out["voice_cleanup_prompt"] = str(out["voice_cleanup_prompt"] or "")[:16000]
    items = out["commands"] if isinstance(out["commands"], list) else DEFAULT_COMMANDS
    out["commands"] = [command(item) for item in items[:100]]
    triggers = [item["trigger"].casefold() for item in out["commands"]]
    if len(set(triggers)) != len(triggers):
        raise ValueError("Триггеры команд должны различаться / Duplicate command triggers")
    return out


def load() -> dict:
    from .storage import load_json_with_recovery
    return normalize(load_json_with_recovery(path(), {}))


def save(values: dict) -> dict:
    from .storage import _json_transaction, save_json
    with _lock, _json_transaction(path()):
        out = normalize({**load(), **{k: v for k, v in values.items() if k in DEFAULTS}})
        save_json(path(), out)
    return out


def parse_proxy(value: Any) -> dict:
    """Validated MTProxy / SOCKS4/5 / HTTP URL. Credentials stay in SecretStore."""
    raw = str(value or "").strip()
    if not raw:
        return {}
    if len(raw) > 4096:
        raise ValueError("Proxy URL too long")
    parsed = urlparse(raw)
    query = parse_qs(parsed.query)
    tg = parsed.scheme.lower() == "tg" or (parsed.scheme == "https" and parsed.hostname in ("t.me", "telegram.me"))
    if tg:
        kind = parsed.netloc if parsed.scheme == "tg" else parsed.path.strip("/")
        if kind not in ("proxy", "socks"):
            raise ValueError("Нужна ссылка tg://proxy или tg://socks")
        host = (query.get("server") or [""])[0]
        port = (query.get("port") or [""])[0]
        if kind == "proxy":
            secret = (query.get("secret") or [""])[0]
            # Telethon supports ordinary and dd-secret MTProxy. TLS camouflage (ee) needs another transport.
            if not re.fullmatch(r"(?:dd)?[0-9a-fA-F]{32}", secret):
                raise ValueError("MTProxy: нужен hex secret (32 символа, возможно с dd); ee/Fake-TLS не поддерживается")
            result = {"kind": "mtproto", "secret": secret}
        else:
            result = {"kind": "socks5", "username": (query.get("user") or [""])[0],
                      "password": (query.get("pass") or [""])[0]}
    else:
        if parsed.scheme.lower() not in ("socks5", "socks5h", "socks4", "http"):
            raise ValueError("Proxy: tg://proxy, tg://socks, socks5://, socks4:// или http://")
        host = parsed.hostname or ""
        try:
            port = parsed.port or ""
        except ValueError:
            raise ValueError("Некорректный порт proxy") from None
        result = {"kind": "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme.lower(),
                  "username": unquote(parsed.username or ""), "password": unquote(parsed.password or "")}
    if not host or any(c.isspace() or ord(c) < 32 for c in host) or len(host) > 253:
        raise ValueError("Некорректный адрес proxy")
    if not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Порт proxy: 1–65535")
    return {**result, "host": host, "port": int(port)}
