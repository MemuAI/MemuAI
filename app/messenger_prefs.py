"""Settings of the Discord and WhatsApp bots (``discord_*`` / ``whatsapp_*`` preferences)."""
from __future__ import annotations

import re
from typing import Any, Dict, List

PREF_DEFAULTS: Dict[str, Any] = {
    "discord_enabled": False,
    "discord_bot_token": "",
    "discord_language": "ru",
    "discord_model": "",
    "discord_allowed_users": [],
    "whatsapp_enabled": False,
    "whatsapp_mode": "cloud",
    "whatsapp_access_token": "",
    "whatsapp_phone_id": "",
    "whatsapp_app_secret": "",
    "whatsapp_verify_token": "",
    "whatsapp_waha_url": "http://127.0.0.1:3000",
    "whatsapp_waha_api_key": "",
    "whatsapp_waha_session": "default",
    "whatsapp_waha_chat": "",
    "whatsapp_language": "ru",
    "whatsapp_model": "",
    "whatsapp_allowed_users": [],
}

_ID_RULES = {"discord": r"\d{5,25}", "whatsapp": r"\d{7,15}"}


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _text(value: Any, size: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()[:size]


def _ids(value: Any, platform: str) -> List[str]:
    rule = _ID_RULES[platform]
    split = r"[,;\n]+" if platform == "whatsapp" else r"[\s,;]+"
    items = value if isinstance(value, list) else re.split(split, str(value or ""))
    clean = []
    for item in items:
        text = re.sub(r"[\s+()\-]", "", str(item or "")) if platform == "whatsapp" else str(item or "").strip()
        if re.fullmatch(rule, text) and text not in clean:
            clean.append(text)
    return clean[:10]


def _url(value: Any, fallback: str) -> str:
    text = _text(value, 500).rstrip("/")
    return text if re.fullmatch(r"https?://[^\s/?#]+(:\d+)?(/[^\s?#]*)?", text) else fallback


def normalize_prefs(out: Dict[str, Any], raw: Any = None) -> Dict[str, Any]:
    """Validate the messenger keys in place (called by pgdata)."""
    for key in ("discord_enabled", "whatsapp_enabled"):
        out[key] = _bool(out.get(key, PREF_DEFAULTS[key]))
    for platform in ("discord", "whatsapp"):
        out[f"{platform}_language"] = "en" if out.get(f"{platform}_language") == "en" else "ru"
        out[f"{platform}_model"] = _text(out.get(f"{platform}_model"), 300)
        out[f"{platform}_allowed_users"] = _ids(out.get(f"{platform}_allowed_users"), platform)
    out["discord_bot_token"] = _text(out.get("discord_bot_token"), 256)
    out["whatsapp_mode"] = "waha" if out.get("whatsapp_mode") == "waha" else "cloud"
    out["whatsapp_access_token"] = _text(out.get("whatsapp_access_token"), 1024)
    out["whatsapp_phone_id"] = re.sub(r"\D", "", str(out.get("whatsapp_phone_id") or ""))[:30]
    out["whatsapp_app_secret"] = _text(out.get("whatsapp_app_secret"), 200)
    out["whatsapp_verify_token"] = re.sub(r"[^\w.\-~]", "", str(out.get("whatsapp_verify_token") or ""))[:200]
    out["whatsapp_waha_url"] = _url(out.get("whatsapp_waha_url"), PREF_DEFAULTS["whatsapp_waha_url"])
    out["whatsapp_waha_api_key"] = _text(out.get("whatsapp_waha_api_key"), 300)
    session = _text(out.get("whatsapp_waha_session"), 64)
    out["whatsapp_waha_session"] = session if re.fullmatch(r"[\w.\-]{1,64}", session) else "default"
    out["whatsapp_waha_chat"] = _text(out.get("whatsapp_waha_chat"), 80)
    return out
